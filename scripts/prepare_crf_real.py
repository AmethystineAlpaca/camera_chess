#!/usr/bin/env python3
"""Acquire attributed real photographs and export partial local corrections.

Public photos are not continuous-video events. Local edits only label edited
squares. The optional inference step freezes the existing recognition model.
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import os
import shutil
import sys
import urllib.request
from pathlib import Path

import chess
import numpy as np
from PIL import Image

from build_crf_dataset import CLASSES, ROOT, SQUARES, digest, dump, file_hash, labels, state

REPO = "felixfabricius/robot-chess-commentator-squares"
REVISION = "d71ba52761e497b18bbe5743efcbafb0d1f21091"
FILES = {
    "train": "0936de5f53a9c579a3ec0ec0a186c4c5083271c4823c4bf62889181c47e83335",
    "val": "f32ed3181327b4dfbd8119250bc019315e0401e2899e63db1d865c83a4a065f4",
    "test": "09058170e3c00362911b8a5c1f5a4298bd4428adc91d9d5a5372a02dbf91d272",
}


def download(url, path, expected=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        temporary = path.with_suffix(".download")
        with urllib.request.urlopen(url, timeout=90) as r, temporary.open("wb") as f:
            shutil.copyfileobj(r, f)
        temporary.rename(path)
    if expected and file_hash(path) != expected:
        raise ValueError(f"Source checksum mismatch: {path}")


def physical_fen(target):
    board = chess.Board(None)
    for square, category in zip(SQUARES, target):
        if category:
            board.set_piece_at(square, chess.Piece.from_symbol(CLASSES[int(category)]))
    return board.board_fen()


def public_photos(raw, output, synthetic):
    sys.path.insert(0, str(ROOT / "data/crf/python-deps"))
    import pyarrow.parquet as pq

    output.mkdir(parents=True, exist_ok=True)
    (output / "images").mkdir(exist_ok=True)
    counts = collections.Counter()
    records, seen_images = [], set()
    source_files = []
    for source_split, expected in FILES.items():
        relative = f"data/boards/{source_split}-00000-of-00001.parquet"
        url = f"https://huggingface.co/datasets/{REPO}/resolve/{REVISION}/{relative}"
        path = raw / f"robot-{source_split}-00000-of-00001.parquet"
        download(url, path, expected)
        source_files.append({"url": url, "sha256": expected, "bytes": path.stat().st_size})
        table = pq.read_table(path)
        schema = json.loads(table.schema.metadata[b"huggingface"])["info"]["features"]
        corners = schema["top_left_corner"]["names"]
        # Source explicitly documents file-major a1,a2,...,a8,b1,... order and
        # labels empty,K,Q,R,B,N,P,k,q,r,b,n,p. Verify FEN agreement below.
        source_classes = ".KQRBNPkqrbnp"
        for entry in table.to_pylist():
            counts["source_photos"] += 1
            blob = entry["warped_image"]["bytes"]
            image_id = digest(blob)
            if image_id in seen_images:
                counts["duplicate_images_removed"] += 1
                continue
            seen_images.add(image_id)
            y = np.array([CLASSES.index(source_classes[entry["labels"][chess.square_file(s) * 8 + chess.square_rank(s)]])
                          for s in SQUARES], dtype=np.uint8)
            if len(entry["labels"]) != 64:
                raise ValueError("Incorrect source label length")
            placement = physical_fen(y)
            if entry["fen_matches_pixels"] and placement != chess.Board(entry["board_fen"]).board_fen():
                raise ValueError("Source FEN/labels disagree despite agreement flag")
            with Image.open(io.BytesIO(blob)) as im:
                im.verify()
            image_path = output / "images" / f"{image_id}.png"
            # Preserve original bytes; suffix follows verified image format.
            with Image.open(io.BytesIO(blob)) as im:
                suffix = ".jpg" if im.format == "JPEG" else ".png"
            image_path = image_path.with_suffix(suffix)
            image_path.write_bytes(blob)
            reasons = []
            if not entry["fen_matches_pixels"]:
                reasons.append("source_fen_does_not_match_photo")
            if not entry["valid_game_position"]:
                reasons.append("free_placement_not_a_verified_game_position")
            if not entry["previous_board_fen"] or not entry["move_uci"]:
                reasons.append("missing_previous_state_or_move")
            transition = None
            if not reasons:
                try:
                    previous = chess.Board(entry["previous_board_fen"])
                    move = chess.Move.from_uci(entry["move_uci"])
                    if not previous.is_valid() or not previous.is_legal(move):
                        reasons.append("recorded_move_is_not_legal")
                    else:
                        after = previous.copy(stack=False)
                        after.push(move)
                        if after.board_fen() != placement:
                            reasons.append("recorded_transition_target_mismatch")
                        else:
                            transition = digest(state(previous) + "|" + placement)
                except ValueError:
                    reasons.append("malformed_recorded_transition")
            records.append({"id": image_id, "source_image_id": entry["image_id"],
                            "setup_id": entry["setup_id"], "split": "validation" if source_split == "val" else source_split,
                            "image": str(image_path.relative_to(ROOT)), "image_sha256": image_id,
                            "target": y.tolist(), "current_board_fen": placement,
                            "source_board_fen": entry["board_fen"], "previous_fen": entry["previous_board_fen"],
                            "move_uci": entry["move_uci"], "top_left_corner": corners[entry["top_left_corner"]],
                            "transition_id": transition, "move_exclusion_reasons": reasons,
                            "observation_type": "real_rectified_still_photo", "label_source": "publisher_annotations"})
    eligible = [r for r in records if not r["move_exclusion_reasons"]]
    counts["legal_photo_transitions_before_dedup"] = len(eligible)
    if not (synthetic / "transitions.jsonl").exists():
        raise ValueError("Build the synthetic seed set first so real/synthetic overlap can be excluded")
    synthetic_rows = [json.loads(s) for s in (synthetic / "transitions.jsonl").read_text().splitlines()]
    synthetic_placements = {p for r in synthetic_rows for p in [r["previous_fen"].split()[0], r["current_board_fen"]]}
    # Group identity comes from setup. Exclude shared endpoints across setups'
    # publisher splits before transition dedup, so dedup cannot conceal overlap.
    owners = collections.defaultdict(set)
    for row in eligible:
        for placement in [row["previous_fen"].split()[0], row["current_board_fen"]]:
            owners[placement].add(row["split"])
    seen_transitions = set()
    for row in eligible:
        if any(len(owners[p]) > 1 for p in [row["previous_fen"].split()[0], row["current_board_fen"]]):
            row["move_exclusion_reasons"].append("cross_split_placement_overlap")
            counts["cross_split_move_exclusions"] += 1
        elif {row["previous_fen"].split()[0], row["current_board_fen"]} & synthetic_placements:
            row["move_exclusion_reasons"].append("synthetic_placement_overlap")
            counts["synthetic_overlap_exclusions"] += 1
        elif row["transition_id"] in seen_transitions:
            row["move_exclusion_reasons"].append("duplicate_transition")
            counts["duplicate_transitions_removed"] += 1
        else:
            seen_transitions.add(row["transition_id"])
    with (output / "photos.jsonl").open("w") as f:
        for row in records:
            f.write(json.dumps(row, sort_keys=True) + "\n")
    counts["unique_photos"] = len(records)
    counts["usable_legal_photo_transitions"] = sum(not r["move_exclusion_reasons"] for r in records)
    dump(output / "source.json", {"dataset": REPO, "revision": REVISION, "retrieved_date": "2026-10-11",
                                  "url": f"https://huggingface.co/datasets/{REPO}",
                                  "license": "CC-BY-4.0", "attribution": "Felix Fabricius, Robot chess commentator: square dataset, 2026",
                                  "files": source_files, "counts": dict(counts),
                                  "synthetic_transition_sha256": file_hash(synthetic / "transitions.jsonl"),
                                  "preparation_script_sha256": file_hash(__file__),
                                  "limitations": ["Still photographs after moves; no hand trajectories, timing, or real no-op events",
                                                  "Publisher images are perspective-warped; this differs from the application's raw camera feed",
                                                  "Publisher annotations, not independently relabeled by this project",
                                                  "Pretraining overlap with the frozen model has not been audited"]})
    print(json.dumps(dict(counts), indent=2), flush=True)


def local_corrections(output):
    output.mkdir(parents=True, exist_ok=True)
    counts = collections.Counter()
    records, observations, targets, masks = [], [], [], []
    seen = set()
    for folder in sorted((ROOT / "validation/recognition-logs").iterdir()):
        if not folder.is_dir():
            continue
        counts["directories"] += 1
        try:
            pair = json.loads((folder / "pair.json").read_text())
            recognition = json.loads((folder / "recognition.json").read_text())
            if not pair.get("complete") or pair.get("annotation_scope") != "edited_squares_only":
                counts["incomplete_or_unsupported_pair"] += 1
                continue
            source = pair["source"]
            image_path = folder / Path(source["image"]).name
            if file_hash(image_path) != source["sha256"] or any(source[k] != recognition[k] for k in ["sequence", "capture_time"]):
                counts["frame_identity_mismatch"] += 1
                continue
            q = np.array([[recognition["square_probabilities"][chess.square_name(s)][c] for c in CLASSES] for s in SQUARES], dtype=np.float32)
            if q.shape != (64, 13) or not np.isfinite(q).all() or (q < 0).any() or not np.allclose(q.sum(-1), 1, atol=1e-5):
                raise ValueError("Invalid local probabilities")
            y = np.full(64, -1, dtype=np.int8)
            for square, edit in pair["edits"].items():
                y[SQUARES.index(chess.parse_square(square))] = CLASSES.index(edit["after"])
            key = digest(source["sha256"] + json.dumps(pair["edits"], sort_keys=True))
            if key in seen:
                counts["duplicate_annotations_removed"] += 1
                continue
            seen.add(key)
            mask = y >= 0
            counts["explicitly_labeled_squares"] += int(mask.sum())
            counts["explicit_labels_equal_raw_argmax"] += int((q.argmax(-1)[mask] == y[mask]).sum())
            observations.append(q); targets.append(y); masks.append(mask)
            records.append({"id": key, "source_directory": str(folder.relative_to(ROOT)),
                            "image": str(image_path.relative_to(ROOT)), "image_sha256": source["sha256"],
                            "sequence": source["sequence"], "capture_time": source["capture_time"],
                            "annotation_scope": "edited_squares_only", "edits": pair["edits"],
                            "whole_board_ground_truth": False, "split": "calibration_only"})
        except (FileNotFoundError, KeyError):
            counts["missing_paired_evidence"] += 1
    if observations:
        np.savez_compressed(output / "partial-labels.npz", probabilities=np.stack(observations),
                            target=np.stack(targets), label_mask=np.stack(masks))
    with (output / "corrections.jsonl").open("w") as f:
        for row in records:
            f.write(json.dumps(row, sort_keys=True) + "\n")
    counts["usable_partial_frames"] = len(records)
    dump(output / "report.json", {"counts": dict(counts), "public": False,
                                  "limitations": "Only edited squares are human labels; no verified previous/current full-board pair. Selection is biased toward errors."})
    print(json.dumps(dict(counts), indent=2), flush=True)


def infer(output, limit=None):
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    import torch
    sys.path.insert(0, str(ROOT / "research/chessqueries"))
    from chessqueries.models.checkpoint import load_safetensors_model
    from chessqueries.data.transforms import build_transform

    rows = [json.loads(s) for s in (output / "photos.jsonl").read_text().splitlines()]
    rows = [r for r in rows if not r["move_exclusion_reasons"]]
    if limit:
        rows = rows[:limit]
    model_path = ROOT / "research/models/chessq-lite.safetensors"
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    torch.set_num_threads(4)
    model = load_safetensors_model(model_path, device=device).float().eval()
    model.requires_grad_(False)
    transform = build_transform(644, train=False)
    probabilities, targets, previous = [], [], []
    rotate = {"a8": 0, "a1": 1, "h1": 2, "h8": 3}
    with torch.inference_mode():
        for index, row in enumerate(rows):
            with Image.open(ROOT / row["image"]) as image:
                rgb = np.array(image.convert("RGB"))
            rgb = np.rot90(rgb, rotate[row["top_left_corner"]]).copy()
            tensor = torch.from_numpy(rgb).permute(2, 0, 1).float()
            logits = model(transform(tensor).unsqueeze(0).to(device))
            q = logits.softmax(-1)[0].cpu().numpy()
            if q.shape != (64, 13) or not np.isfinite(q).all() or not np.allclose(q.sum(-1), 1, atol=1e-5):
                raise ValueError("Invalid model output")
            probabilities.append(q); targets.append(row["target"]); previous.append(labels(chess.Board(row["previous_fen"])))
            if (index + 1) % 20 == 0:
                print(f"Frozen-model inference: {index + 1}/{len(rows)} photos", flush=True)
    q = np.stack(probabilities)
    truth = np.array(targets, dtype=np.uint8)
    suffix = "smoke" if limit else "moves"
    np.savez_compressed(output / f"{suffix}.npz", probabilities=q, target=truth,
                        previous=np.stack(previous), split=np.array([r["split"] for r in rows]))
    with (output / f"{suffix}.jsonl").open("w") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True) + "\n")
    report = {"samples": len(rows), "model_file": str(model_path.relative_to(ROOT)),
              "model_sha256": file_hash(model_path), "device": device, "frozen": True,
              "torch": torch.__version__, "mps_cpu_fallback": os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK"),
              "inference_script_sha256": file_hash(__file__),
              "orientation": "rotate from publisher top_left_corner to a8; no label-based rotation search",
              "raw_square_accuracy": float((q.argmax(-1) == truth).mean()),
              "raw_exact_board_accuracy": float((q.argmax(-1) == truth).all(-1).mean()),
              "split_counts": dict(collections.Counter(r["split"] for r in rows)),
              "classes": list(CLASSES), "square_order": [chess.square_name(s) for s in SQUARES],
              "probabilities_sha256": file_hash(output / f"{suffix}.npz"),
              "metadata_sha256": file_hash(output / f"{suffix}.jsonl")}
    dump(output / f"{suffix}-inference.json", report)
    print(json.dumps(report, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["public", "local", "infer"])
    parser.add_argument("--raw", type=Path, default=ROOT / "data/crf/raw")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--synthetic", type=Path, default=ROOT / "data/crf/synthetic-v1")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    output = args.output or ROOT / "data/crf/real" / ("local-corrections" if args.command == "local" else "robot-v1")
    if args.command == "public":
        public_photos(args.raw, output, args.synthetic)
    elif args.command == "local":
        local_corrections(output)
    else:
        infer(output, args.limit)


if __name__ == "__main__":
    main()
