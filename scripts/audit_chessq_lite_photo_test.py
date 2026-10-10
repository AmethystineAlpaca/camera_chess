#!/usr/bin/env python3
"""Audit frozen ChessQ Lite raw argmax on every photo in the source test split."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = ROOT / "data/crf/real/robot-v1"
DEFAULT_MODEL = ROOT / "research/models/chessq-lite.safetensors"
DEFAULT_OUTPUT = ROOT / "data/crf/runs/real-photos-synthetic-previous-v1/chessq-lite-raw-test-audit.json"
ROTATE_TO_A8 = {"a8": 0, "a1": 1, "h1": 2, "h8": 3}


def load_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def metrics(predictions: dict[str, np.ndarray], rows: list[dict]) -> dict:
    targets = np.asarray([row["target"] for row in rows], dtype=np.uint8)
    predicted = np.stack([predictions[row["id"]] for row in rows])
    square_correct = predicted == targets
    correct_per_photo = square_correct.sum(axis=1)
    return {
        "photos": len(rows),
        "exact_boards_correct": int(np.all(square_correct, axis=1).sum()),
        "exact_board_accuracy": float(np.all(square_correct, axis=1).mean()),
        "square_accuracy": float(square_correct.mean()),
        "mean_correct_squares_per_photo": float(correct_per_photo.mean()),
        "mean_incorrect_squares_per_photo": float(64 - correct_per_photo.mean()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    source_rows = load_rows(args.data / "photos.jsonl")
    source_test = [row for row in source_rows if row["split"] == "test"]
    retained_test = [row for row in source_test if not row["move_exclusion_reasons"]]
    if not source_test or not retained_test:
        raise ValueError("Expected source test photos and a nonempty retained subset")

    # Set this before importing torch: MPS falls back to CPU for bicubic position
    # embedding interpolation, matching the original real-photo inference run.
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    sys.path.insert(0, str(ROOT / "research/chessqueries"))
    import torch
    from chessqueries.data.transforms import build_transform
    from chessqueries.models.checkpoint import load_safetensors_model

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    torch.set_num_threads(4)
    model = load_safetensors_model(args.model, device=device).float().eval()
    model.requires_grad_(False)
    transform = build_transform(644, train=False)
    predictions: dict[str, np.ndarray] = {}
    with torch.inference_mode():
        for index, row in enumerate(source_test, start=1):
            with Image.open(ROOT / row["image"]) as image:
                rgb = np.array(image.convert("RGB"))
            rgb = np.rot90(rgb, ROTATE_TO_A8[row["top_left_corner"]]).copy()
            tensor = torch.from_numpy(rgb).permute(2, 0, 1).float()
            logits = model(transform(tensor).unsqueeze(0).to(device))
            probabilities = logits.softmax(-1)[0].cpu().numpy()
            if probabilities.shape != (64, 13) or not np.isfinite(probabilities).all():
                raise ValueError(f"Invalid output for source photo {row['id']}")
            predictions[row["id"]] = probabilities.argmax(-1).astype(np.uint8)
            if index % 20 == 0:
                print(f"Inferred {index}/{len(source_test)} source-test photos", flush=True)

    # Ensure the expanded 88-photo audit reproduces cached raw predictions on
    # every one of the 55 retained test photos used by the CRF comparison.
    cached_rows = load_rows(args.data / "moves.jsonl")
    with np.load(args.data / "moves.npz", allow_pickle=False) as arrays:
        cached_predictions = arrays["probabilities"].argmax(axis=-1).astype(np.uint8)
        cached_splits = arrays["split"].astype(str)
    cached_test = {
        row["id"]: cached_predictions[index]
        for index, row in enumerate(cached_rows)
        if cached_splits[index] == "test"
    }
    parity = all(np.array_equal(predictions[row_id], prediction)
                 for row_id, prediction in cached_test.items())
    if len(cached_test) != len(retained_test) or set(cached_test) != {row["id"] for row in retained_test}:
        raise ValueError("Retained source test rows do not match cached inference rows")
    if not parity:
        raise ValueError("Direct raw predictions differ from the previously cached test predictions")

    source = json.loads((args.data / "source.json").read_text())
    model_manifest_path = args.model.with_name("chessq-lite-manifest.json")
    model_manifest = json.loads(model_manifest_path.read_text()) if model_manifest_path.exists() else {}
    report = {
        "evaluation": "raw frozen recognizer; per-square argmax, no CRF or predecessor",
        "photo_dataset": source["dataset"],
        "photo_dataset_revision": source["revision"],
        "source_split": "test",
        "source_test_scope": "all source test rows before transition-validity, duplicate, cross-split, or synthetic-overlap exclusions",
        "recognizer_release": model_manifest.get("model", {}).get("release", "unknown"),
        "recognizer_model": str(args.model.relative_to(ROOT)) if args.model.is_relative_to(ROOT) else str(args.model),
        "recognizer_sha256": hashlib.sha256(args.model.read_bytes()).hexdigest(),
        "preprocessing": "same frozen-model inference path as prepare_crf_real.py: RGB, Felix dataset top-left orientation rotated to a8, 644-square ChessQueries eval transform",
        "direct_raw_argmax_matches_cached_retained_test_predictions": parity,
        "all_source_test_rows": metrics(predictions, source_test),
        "retained_test_subset_used_in_crf_evaluation": metrics(predictions, retained_test),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    print(f"report: {args.output.relative_to(ROOT) if args.output.is_relative_to(ROOT) else args.output}")


if __name__ == "__main__":
    main()
