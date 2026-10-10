#!/usr/bin/env python3
"""Audit prepared real observations without trusting classifier predictions."""
import json
from pathlib import Path

import chess
import numpy as np

from build_crf_dataset import CLASSES, ROOT, SQUARES, dump, file_hash, labels


def read_rows(path):
    return [json.loads(s) for s in Path(path).read_text().splitlines()]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    root = ROOT / "data/crf"
    directory = root / "real/robot-v1"
    rows = read_rows(directory / "moves.jsonl")
    inference = json.loads((directory / "moves-inference.json").read_text())
    require(file_hash(directory / "moves.npz") == inference["probabilities_sha256"], "Model-output checksum mismatch")
    require(file_hash(directory / "moves.jsonl") == inference["metadata_sha256"], "Real metadata checksum mismatch")
    require(inference["classes"] == list(CLASSES), "Class mapping mismatch")
    require(inference["square_order"] == [chess.square_name(s) for s in SQUARES], "Square ordering mismatch")
    synthetic = read_rows(root / "synthetic-v1/transitions.jsonl")
    synthetic_placements = {p for r in synthetic for p in [r["previous_fen"].split()[0], r["current_board_fen"]]}
    seen_images, seen_transitions, setup_splits, placement_splits = set(), set(), {}, {}
    with np.load(directory / "moves.npz", allow_pickle=False) as arrays:
        q = arrays["probabilities"]
        require(q.shape == (len(rows), 64, 13), "Real probability shape mismatch")
        require(np.isfinite(q).all() and (q >= 0).all() and np.allclose(q.sum(-1), 1, atol=1e-5), "Invalid real probabilities")
        for i, row in enumerate(rows):
            require(not row["move_exclusion_reasons"], "Excluded photo in inference set")
            require(row["image_sha256"] not in seen_images and row["transition_id"] not in seen_transitions, "Duplicate real input/transition")
            seen_images.add(row["image_sha256"]); seen_transitions.add(row["transition_id"])
            require(file_hash(ROOT / row["image"]) == row["image_sha256"], "Image checksum mismatch")
            require(setup_splits.setdefault(row["setup_id"], row["split"]) == row["split"], "Setup crosses splits")
            for p in [row["previous_fen"].split()[0], row["current_board_fen"]]:
                require(p not in synthetic_placements, "Real/synthetic placement overlap")
                require(placement_splits.setdefault(p, row["split"]) == row["split"], "Real placement crosses splits")
            board = chess.Board(row["previous_fen"])
            require(np.array_equal(arrays["previous"][i], labels(board)), "Previous labels mismatch")
            move = chess.Move.from_uci(row["move_uci"])
            require(board.is_valid() and board.is_legal(move), "Invalid real move")
            board.push(move)
            require(board.board_fen() == row["current_board_fen"], "Real target is not the successor")
            require(np.array_equal(arrays["target"][i], labels(board)), "Real target encoding mismatch")
            require(str(arrays["split"][i]) == row["split"], "Array split mismatch")
    local = root / "real/local-corrections"
    corrections = read_rows(local / "corrections.jsonl")
    with np.load(local / "partial-labels.npz", allow_pickle=False) as arrays:
        q, target, mask = arrays["probabilities"], arrays["target"], arrays["label_mask"]
        require(q.shape == (len(corrections), 64, 13), "Local probability shape mismatch")
        require(np.isfinite(q).all() and (q >= 0).all() and np.allclose(q.sum(-1), 1, atol=1e-5), "Invalid local probabilities")
        require(np.all(target[~mask] == -1), "Unedited cells must not become labels")
        for i, row in enumerate(corrections):
            require(file_hash(ROOT / row["image"]) == row["image_sha256"], "Local image checksum mismatch")
            require(int(mask[i].sum()) == len(row["edits"]), "Local mask mismatch")
            for square, edit in row["edits"].items():
                index = SQUARES.index(chess.parse_square(square))
                require(mask[i, index] and target[i, index] == CLASSES.index(edit["after"]), "Local edited label mismatch")
        labeled_squares = int(mask.sum())
    report = {"passed": True, "real_photographed_move_pairs": len(rows),
              "duplicate_real_transitions": 0, "cross_split_setups": 0, "cross_split_placements": 0,
              "synthetic_real_placement_overlap": 0, "partial_local_frames": len(corrections),
              "explicit_local_square_labels": labeled_squares,
              "verified_continuous_video_events": 0,
              "note": "Structural validation of publisher/user labels; not independent human relabeling or a deployment accuracy claim."}
    dump(root / "real/validation.json", report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
