"""Experimental 250-weight single-frame CRF, selectable in the live adapter.

Same candidates as v1. Zero residual weights reproduce fixed log-probability
scoring. Features use only the current probabilities and candidate operation;
there are no video, position, target-derived, or learned chess-rule features.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

try:
    from .crf_postprocessor import CLASSES, EPSILON, GROUPS, candidates, validate_probabilities
except ImportError:
    from crf_postprocessor import CLASSES, EPSILON, GROUPS, candidates, validate_probabilities

SCHEMA = 2
MODEL_ID = "crf-v2-250"
OP_COLUMNS = [0, 2, 3, 4, 5, 6]  # Unchanged is the zero-score reference.
FEATURES = (
    [f"confusion_{c}_from_{k}" for c in CLASSES[1:] for k in CLASSES]
    + [f"group_{c}_{g}" for c in CLASSES[1:] for g in ("white", "black")]
    + [f"probability_{c}" for c in CLASSES]
    + [f"entropy_log_probability_{c}" for c in CLASSES]
    + [f"margin_log_probability_{c}" for c in CLASSES]
    + [f"argmax_match_{c}" for c in CLASSES]
    + [f"max_probability_bias_{c}" for c in CLASSES[1:]]
    + ["legal", "illegal", "capture", "castling", "en_passant", "promotion"]
)


def fixed_weights():
    return np.zeros(len(FEATURES), dtype=np.float32)


def from_v1(weights):
    """Embed any v1 scorer, modulo an irrelevant common candidate score.

For equivalence checks only; v2 training starts from fixed_weights().
"""
    v = np.asarray(weights, dtype=np.float32)
    if v.shape != (25,):
        raise ValueError("Expected 25 v1 weights")
    w = fixed_weights()
    matrix = w[:156].reshape(12, 13)
    matrix[:, 0] = -(v[0] - 1 + v[13])
    matrix[np.arange(12), np.arange(1, 13)] = v[1:13] - 1
    groups = w[156:180].reshape(12, 2)
    groups[np.arange(12), GROUPS[1:] - 1] = v[13 + GROUPS[1:]]
    w[180:193] = v[16]
    w[219:232] = v[17]
    w[244:] = v[[18, 20, 21, 22, 23, 24]]
    w[244:246] -= v[19]
    return w


def potentials(q, weights):
    q = validate_probabilities(q)
    w = np.asarray(weights)
    if w.shape != (len(FEATURES),) or not np.isfinite(w).all():
        raise ValueError("Expected 250 finite v2 weights")
    logq = np.log(np.maximum(q, EPSILON))
    groups = np.stack((q[..., 1:7].sum(-1), q[..., 7:].sum(-1)), axis=-1)
    # Empty-group log probability is already a matrix input: do not duplicate it.
    residual = logq @ w[:156].reshape(12, 13).T
    residual += np.log(np.maximum(groups, EPSILON)) @ w[156:180].reshape(12, 2).T
    top = np.sort(q, axis=-1)[..., -2:]
    maximum = top[..., 1:2]
    margin = maximum - top[..., :1]
    entropy = -(q * logq).sum(-1, keepdims=True) / np.float32(np.log(13))
    residual += maximum * w[232:244]
    p = logq + np.concatenate((np.zeros_like(residual[..., :1]), residual), axis=-1)
    return (p + q * w[180:193] + entropy * logq * w[193:206]
            + margin * logq * w[206:219]
            + (np.arange(13) == q.argmax(-1)[..., None]) * w[219:232])


def score(q, options, weights):
    p = potentials(q, weights)
    p = np.concatenate((p, np.zeros((*p.shape[:-2], 1, 13), dtype=p.dtype)), axis=-2)
    return ((p[..., options.squares, options.after] - p[..., options.squares, options.before]).sum(-1)
            + options.flags[:, OP_COLUMNS] @ weights[244:])


def load_weights(path):
    record = json.loads(Path(path).read_text())
    if (record.get("schema") != SCHEMA or record.get("model_id") != MODEL_ID
            or record.get("features") != FEATURES or record.get("classes") != CLASSES):
        raise ValueError("Incompatible v2 CRF model schema")
    w = np.asarray(record["weights"], dtype=np.float32)
    if w.shape != (len(FEATURES),) or not np.isfinite(w).all():
        raise ValueError("Invalid v2 CRF weights")
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
    board = options.boards[winner]
    fit = q[np.arange(64), board]
    return dict(labels=board.tolist(), operation=int(options.operations[winner]),
                move_uci=options.moves[winner], conditional_probability=float(mass[winner] / mass.sum()),
                score_gap=float(scores[winner] - scores[order[1]]),
                mean_log_visual_fit=float(np.log(np.maximum(fit, EPSILON)).mean()),
                candidate_count=len(scores), decision="review_required")


def torch_scores(q, squares, before, after, flags, weights):
    """Differentiable equivalent of sparse NumPy inference, including padding."""
    import torch
    logq = q.clamp_min(EPSILON).log()
    groups = torch.stack((q[..., 1:7].sum(-1), q[..., 7:].sum(-1)), dim=-1)
    residual = logq @ weights[:156].reshape(12, 13).T
    residual = residual + groups.clamp_min(EPSILON).log() @ weights[156:180].reshape(12, 2).T
    top = q.topk(2, dim=-1).values
    maximum, margin = top[..., :1], top[..., :1] - top[..., 1:2]
    entropy = -(q * logq).sum(-1, keepdim=True) / float(np.log(13))
    residual = residual + maximum * weights[232:244]
    p = logq + torch.cat((torch.zeros_like(residual[..., :1]), residual), dim=-1)
    p = (p + q * weights[180:193] + entropy * logq * weights[193:206]
         + margin * logq * weights[206:219]
         + (torch.arange(13, device=q.device) == q.argmax(-1)[..., None]) * weights[219:232])
    p = torch.cat((p, p.new_zeros((*p.shape[:-2], 1, 13))), dim=-2).flatten(-2)
    b, obs = q.shape[:2]
    to_index = (squares * 13 + after).reshape(b, 1, -1).expand(-1, obs, -1)
    from_index = (squares * 13 + before).reshape(b, 1, -1).expand(-1, obs, -1)
    delta = (p.gather(2, to_index) - p.gather(2, from_index)).reshape(b, obs, -1, 4).sum(-1)
    return delta + (flags[..., OP_COLUMNS] @ weights[244:])[:, None, :]
