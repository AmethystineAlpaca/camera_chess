"""Experimental one-step conditional model. No camera/game state is mutated.

Scores every supported successor, including physical mistakes, using a fixed
confirmed FEN and an image's probabilities. The candidate set never uses targets.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

import chess
import numpy as np

CLASSES = ".PNBRQKpnbrqk"
SQUARES = [chess.square(f, r) for r in range(7, -1, -1) for f in range(8)]
GROUPS = np.array([0] + [1] * 6 + [2] * 6)
FEATURES = ([f"log_probability_{c}" for c in CLASSES]
            + ["log_empty", "log_white", "log_black", "probability", "argmax_match"]
            + ["legal", "unchanged", "illegal", "capture", "castling", "en_passant", "promotion"])
SCHEMA = 1
EPSILON = 1e-9


def labels(board):
    return np.array([CLASSES.index(p.symbol()) if (p := board.piece_at(s)) else 0
                     for s in SQUARES], dtype=np.uint8)


def fixed_weights():
    result = np.zeros(len(FEATURES), dtype=np.float32)
    result[:13] = 1
    return result


@dataclass
class Candidates:
    boards: np.ndarray
    squares: np.ndarray
    before: np.ndarray
    after: np.ndarray
    flags: np.ndarray
    operations: np.ndarray
    moves: list[str]

    def target_index(self, target):
        matches = np.flatnonzero(np.all(self.boards == target, axis=1))
        if len(matches) != 1:
            raise ValueError(f"Target has {len(matches)} matching candidates")
        return int(matches[0])


def candidates(previous_fen):
    board = chess.Board(previous_fen)
    if board.chess960 or not board.is_valid():
        raise ValueError("A valid, confirmed standard-chess reference is required")
    previous = labels(board)
    boards, flags, operations, moves = [previous], [[0, 1, 0, 0, 0, 0, 0]], [1], [""]
    seen = {previous.tobytes()}
    for move in board.legal_moves:
        end = board.copy(stack=False)
        end.push(move)
        value = labels(end)
        if value.tobytes() in seen:
            continue
        seen.add(value.tobytes())
        boards.append(value)
        flags.append([1, 0, 0, board.is_capture(move), board.is_castling(move),
                      board.is_en_passant(move), bool(move.promotion)])
        operations.append(0)
        moves.append(move.uci())
    # Both colors may be moved by mistake. This support matches synthetic v1.
    # Capturing illegally, lifted pieces, and king relocations are unsupported.
    empty = np.flatnonzero(previous == 0)
    for src in np.flatnonzero(previous):
        piece = int(previous[src])
        if piece in (6, 12):
            continue
        for dst in empty:
            if piece in (1, 7) and dst // 8 in (0, 7):
                continue
            value = previous.copy()
            value[src], value[dst] = 0, piece
            if value.tobytes() in seen:
                continue
            seen.add(value.tobytes())
            boards.append(value)
            flags.append([0, 0, 1, 0, 0, 0, 0])
            operations.append(2)
            moves.append(chess.square_name(SQUARES[src]) + chess.square_name(SQUARES[dst]))
    boards = np.stack(boards)
    squares = np.full((len(boards), 4), 64, dtype=np.uint8)
    before, after = np.zeros_like(squares), np.zeros_like(squares)
    for i, value in enumerate(boards):
        changed = np.flatnonzero(value != previous)
        if len(changed) > 4:
            raise ValueError("Unsupported transition size")
        squares[i, :len(changed)] = changed
        before[i, :len(changed)] = previous[changed]
        after[i, :len(changed)] = value[changed]
    return Candidates(boards, squares, before, after, np.array(flags, dtype=np.uint8),
                      np.array(operations, dtype=np.uint8), moves)


def validate_probabilities(q):
    q = np.asarray(q, dtype=np.float32)
    if q.shape[-2:] != (64, 13) or not np.isfinite(q).all() or (q < 0).any():
        raise ValueError("Expected finite nonnegative probabilities (..., 64, 13)")
    if not np.allclose(q.sum(-1), 1, atol=1e-5):
        raise ValueError("Probabilities must sum to one")
    return q


def potentials(q, weights):
    q = validate_probabilities(q)
    groups = np.stack([q[..., 0], q[..., 1:7].sum(-1), q[..., 7:].sum(-1)], axis=-1)
    return (np.log(np.maximum(q, EPSILON)) * weights[:13]
            + (np.log(np.maximum(groups, EPSILON)) * weights[13:16])[..., GROUPS]
            + q * weights[16]
            + (np.arange(13) == q.argmax(-1)[..., None]) * weights[17])


def score(q, options, weights):
    p = potentials(q, weights)
    p = np.concatenate([p, np.zeros((*p.shape[:-2], 1, 13), dtype=p.dtype)], axis=-2)
    return ((p[..., options.squares, options.after] - p[..., options.squares, options.before]).sum(-1)
            + options.flags @ weights[18:])


def load_weights(path):
    record = json.loads(Path(path).read_text())
    if record.get("schema") != SCHEMA or record.get("features") != FEATURES or record.get("classes") != CLASSES:
        raise ValueError("Incompatible CRF model schema")
    w = np.asarray(record["weights"], dtype=np.float32)
    if w.shape != (len(FEATURES),) or not np.isfinite(w).all():
        raise ValueError("Invalid CRF weights")
    return w


def predict(previous_fen, q, weights):
    q = validate_probabilities(q)
    if q.ndim != 2:
        raise ValueError("Inference requires one observation")
    options = candidates(previous_fen)
    scores = score(q, options, weights)
    order = np.argsort(-scores, kind="stable")
    winner = int(order[0])
    mass = np.exp(scores - scores.max())
    posterior = mass / mass.sum()
    board = options.boards[winner]
    fit = q[np.arange(64), board]
    return dict(labels=board.tolist(), operation=int(options.operations[winner]),
                move_uci=options.moves[winner], conditional_probability=float(posterior[winner]),
                score_gap=float(scores[winner] - scores[order[1]]),
                mean_log_visual_fit=float(np.log(np.maximum(fit, EPSILON)).mean()),
                candidate_count=len(scores), decision="review_required")


def torch_scores(q, squares, before, after, flags, weights):
    """Same linear scores, batched as events × observations × candidates.

    Only 25 weights require gradients. Padding is a neutral 65th square.
    """
    import torch
    group_ids = torch.tensor(GROUPS, dtype=torch.long, device=q.device)
    groups = torch.stack((q[..., 0], q[..., 1:7].sum(-1), q[..., 7:].sum(-1)), dim=-1)
    p = (q.clamp_min(EPSILON).log() * weights[:13]
         + (groups.clamp_min(EPSILON).log() * weights[13:16])[..., group_ids]
         + q * weights[16]
         + (torch.arange(13, device=q.device) == q.argmax(-1)[..., None]) * weights[17])
    p = torch.cat((p, p.new_zeros((*p.shape[:-2], 1, 13))), dim=-2).flatten(-2)
    b, obs = q.shape[:2]
    to_index = (squares * 13 + after).reshape(b, 1, -1).expand(-1, obs, -1)
    from_index = (squares * 13 + before).reshape(b, 1, -1).expand(-1, obs, -1)
    delta = (p.gather(2, to_index) - p.gather(2, from_index)).reshape(b, obs, -1, 4).sum(-1)
    return delta + (flags @ weights[18:])[:, None, :]
