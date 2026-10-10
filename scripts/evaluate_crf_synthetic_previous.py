#!/usr/bin/env python3
"""Compare raw recognition and CRFs on held-out real photos with synthetic priors.

The current board is publisher ground truth. For every photo, incoming legal
chess transitions are enumerated and a fixed-seed sample of synthetic previous
positions is selected. Every structured scorer receives the same prior for a
given photo/sample pair. No model is fitted on these photographs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import chess
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import crf_postprocessor as v1  # noqa: E402
import crf_postprocessor_v2 as v2  # noqa: E402


DATA = ROOT / "data/crf/real/robot-v1"
V1_MODEL = ROOT / "research/models/crf-postprocessor-v1.json"
V2_MODEL = ROOT / "research/models/crf-postprocessor-v2-250.json"
CLASSES = v1.CLASSES
SQUARES = v1.SQUARES
CAPTURE_TYPES = (chess.PAWN, chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN)


def board_from_labels(labels: np.ndarray) -> chess.Board:
    board = chess.Board(None)
    for square, label in zip(SQUARES, labels):
        if int(label):
            board.set_piece_at(square, chess.Piece.from_symbol(CLASSES[int(label)]))
    return board


def incoming_legal_states(target: chess.Board) -> list[tuple[str, str]]:
    """Enumerate distinct valid FENs with one legal move to target placement."""
    target_placement = target.board_fen()
    found: dict[str, str] = {}

    def consider(previous: chess.Board, move: chess.Move) -> None:
        previous.chess960 = False
        if not previous.is_valid() or move not in previous.legal_moves:
            return
        after = previous.copy(stack=False)
        after.push(move)
        if after.board_fen() != target_placement:
            return
        fen = previous.fen(en_passant="fen")
        found.setdefault(fen, move.uci())

    empty_squares = [s for s in chess.SQUARES if target.piece_at(s) is None]
    occupied_squares = [s for s in chess.SQUARES if target.piece_at(s) is not None]

    # Ordinary moves and captures, including a promoted piece's ordinary moves.
    for dst in occupied_squares:
        piece = target.piece_at(dst)
        assert piece is not None
        for src in empty_squares:
            previous = target.copy(stack=False)
            previous.remove_piece_at(dst)
            previous.set_piece_at(src, piece)
            previous.turn = piece.color
            previous.castling_rights = chess.BB_EMPTY
            previous.ep_square = None
            consider(previous, chess.Move(src, dst))

            for captured_type in CAPTURE_TYPES:
                previous = target.copy(stack=False)
                previous.remove_piece_at(dst)
                previous.set_piece_at(src, piece)
                previous.set_piece_at(dst, chess.Piece(captured_type, not piece.color))
                previous.turn = piece.color
                previous.castling_rights = chess.BB_EMPTY
                previous.ep_square = None
                consider(previous, chess.Move(src, dst))

    # Reverse a promotion: the resulting piece was a pawn on its penultimate rank.
    for dst in occupied_squares:
        promoted = target.piece_at(dst)
        assert promoted is not None
        if promoted.piece_type not in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN):
            continue
        final_rank = 7 if promoted.color == chess.WHITE else 0
        if chess.square_rank(dst) != final_rank:
            continue
        source_rank = final_rank - 1 if promoted.color == chess.WHITE else final_rank + 1
        for file_delta in (-1, 0, 1):
            src_file = chess.square_file(dst) + file_delta
            if not 0 <= src_file < 8:
                continue
            src = chess.square(src_file, source_rank)
            if target.piece_at(src) is not None:
                continue
            is_capture = file_delta != 0
            for captured_type in (CAPTURE_TYPES if is_capture else (None,)):
                previous = target.copy(stack=False)
                previous.remove_piece_at(dst)
                previous.set_piece_at(src, chess.Piece(chess.PAWN, promoted.color))
                if captured_type is not None:
                    previous.set_piece_at(dst, chess.Piece(captured_type, not promoted.color))
                previous.turn = promoted.color
                previous.castling_rights = chess.BB_EMPTY
                previous.ep_square = None
                consider(previous, chess.Move(src, dst, promotion=promoted.piece_type))

    # Reverse en passant, whose captured pawn is not on the destination square.
    for dst in occupied_squares:
        pawn = target.piece_at(dst)
        if pawn is None or pawn.piece_type != chess.PAWN:
            continue
        target_rank = chess.square_rank(dst)
        expected_rank = 5 if pawn.color == chess.WHITE else 2
        if target_rank != expected_rank:
            continue
        source_rank = target_rank - 1 if pawn.color == chess.WHITE else target_rank + 1
        captured_square = chess.square(chess.square_file(dst), source_rank)
        for file_delta in (-1, 1):
            src_file = chess.square_file(dst) + file_delta
            if not 0 <= src_file < 8:
                continue
            src = chess.square(src_file, source_rank)
            if target.piece_at(src) is not None or target.piece_at(captured_square) is not None:
                continue
            previous = target.copy(stack=False)
            previous.remove_piece_at(dst)
            previous.set_piece_at(src, pawn)
            previous.set_piece_at(captured_square, chess.Piece(chess.PAWN, not pawn.color))
            previous.turn = pawn.color
            previous.castling_rights = chess.BB_EMPTY
            previous.ep_square = dst
            consider(previous, chess.Move(src, dst))

    # Reverse standard castling and grant only the right consumed by that move.
    for color in (chess.WHITE, chess.BLACK):
        rank = 0 if color == chess.WHITE else 7
        king = chess.Piece(chess.KING, color)
        rook = chess.Piece(chess.ROOK, color)
        for king_dst, rook_dst, king_src, rook_src, right in (
            (chess.square(6, rank), chess.square(5, rank), chess.square(4, rank),
             chess.square(7, rank), chess.square(7, rank)),
            (chess.square(2, rank), chess.square(3, rank), chess.square(4, rank),
             chess.square(0, rank), chess.square(0, rank)),
        ):
            if target.piece_at(king_dst) != king or target.piece_at(rook_dst) != rook:
                continue
            if target.piece_at(king_src) is not None or target.piece_at(rook_src) is not None:
                continue
            previous = target.copy(stack=False)
            previous.remove_piece_at(king_dst)
            previous.remove_piece_at(rook_dst)
            previous.set_piece_at(king_src, king)
            previous.set_piece_at(rook_src, rook)
            previous.turn = color
            previous.castling_rights = chess.BB_SQUARES[right]
            previous.ep_square = None
            consider(previous, chess.Move(king_src, king_dst))

    return sorted(found.items())


def choose_states(candidates: list[tuple[str, str]], transition_id: str,
                  seed: int, samples: int) -> list[tuple[str, str]]:
    if not candidates:
        raise ValueError(f"No legal incoming transition for {transition_id}")
    # Hash-priority sampling is uniform without replacement and nested: the
    # first five predecessors are identical whether we request five or ten.
    return sorted(candidates, key=lambda item: (
        hashlib.sha256(f"{seed}|{transition_id}|{item[0]}".encode()).digest(), item[0]
    ))[:samples]


def winner(scores: np.ndarray, options: v1.Candidates) -> np.ndarray:
    return options.boards[int(np.argmax(scores))]


def cluster_interval(values: dict[str, float], cluster_by_photo: dict[str, str],
                     seed: int, draws: int = 20_000) -> list[float | None]:
    """Bootstrap publisher setups, retaining all photographed moves per setup."""
    if not values:
        return [None, None]
    members: dict[str, list[float]] = {}
    for photo_id, value in values.items():
        members.setdefault(cluster_by_photo[photo_id], []).append(float(value))
    clusters = sorted(members)
    sums = np.asarray([sum(members[cluster]) for cluster in clusters], dtype=np.float64)
    sizes = np.asarray([len(members[cluster]) for cluster in clusters], dtype=np.float64)
    rng = np.random.default_rng(seed)
    selected = rng.integers(0, len(clusters), size=(draws, len(clusters)))
    estimates = sums[selected].sum(axis=1) / sizes[selected].sum(axis=1)
    return [float(x) for x in np.quantile(estimates, [0.025, 0.975])]


def summarize(rows: list[dict], method: str, seed: int, requested_samples: int) -> dict:
    per_photo: dict[str, list[dict]] = {}
    cluster_by_photo: dict[str, str] = {}
    for row in rows:
        per_photo.setdefault(row["transition_id"], []).append(row["methods"][method])
        cluster_by_photo[row["transition_id"]] = row["setup_id"]
    photo_exact = {photo: float(np.mean([x["exact"] for x in states]))
                   for photo, states in per_photo.items()}
    photo_square = {photo: float(np.mean([x["square_accuracy"] for x in states]))
                    for photo, states in per_photo.items()}
    single_exact = {photo: float(states[0]["exact"]) for photo, states in per_photo.items()}
    strict_exact = {photo: float(all(x["exact"] for x in states))
                    for photo, states in per_photo.items()}
    strict_five = {photo: float(all(x["exact"] for x in states[:5]))
                   for photo, states in per_photo.items() if len(states) >= 5}
    strict_requested = {
        photo: float(all(x["exact"] for x in states))
        for photo, states in per_photo.items() if len(states) == requested_samples
    }
    return {
        "photos": len(per_photo),
        "publisher_setups": len(set(cluster_by_photo.values())),
        "conditions": len(rows),
        "single_random_previous_exact_board_accuracy": float(np.mean(list(single_exact.values()))),
        "single_random_previous_bootstrap_95_ci_by_setup": cluster_interval(single_exact, cluster_by_photo, seed + 2),
        "single_random_previous_correct_photos": int(sum(single_exact.values())),
        "strict_all_previous_states_exact_board_accuracy": float(np.mean(list(strict_exact.values()))),
        "strict_all_previous_states_bootstrap_95_ci_by_setup": cluster_interval(strict_exact, cluster_by_photo, seed + 3),
        "strict_all_previous_states_correct_photos": int(sum(strict_exact.values())),
        "photos_with_five_distinct_previous_states": len(strict_five),
        "strict_five_previous_states_exact_board_accuracy": float(np.mean(list(strict_five.values()))),
        "strict_five_previous_states_bootstrap_95_ci_by_setup": cluster_interval(
            strict_five, cluster_by_photo, seed + 4),
        "strict_five_previous_states_correct_photos": int(sum(strict_five.values())),
        "photos_with_requested_distinct_previous_states": len(strict_requested),
        "strict_requested_state_count": requested_samples,
        "strict_requested_previous_states_exact_board_accuracy": (
            float(np.mean(list(strict_requested.values()))) if strict_requested else None),
        "strict_requested_previous_states_bootstrap_95_ci_by_setup": cluster_interval(
            strict_requested, cluster_by_photo, seed + 5),
        "strict_requested_previous_states_correct_photos": int(sum(strict_requested.values())),
        "exact_board_accuracy": float(np.mean(list(photo_exact.values()))),
        "exact_board_bootstrap_95_ci_by_setup": cluster_interval(photo_exact, cluster_by_photo, seed),
        "square_accuracy": float(np.mean(list(photo_square.values()))),
        "square_bootstrap_95_ci_by_setup": cluster_interval(photo_square, cluster_by_photo, seed + 1),
        "exact_conditions_correct": int(sum(row["methods"][method]["exact"] for row in rows)),
        "conditions_total": len(rows),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("validation", "test", "all"), default="all")
    parser.add_argument("--samples-per-photo", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20261011)
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--v1-model", type=Path, default=V1_MODEL)
    parser.add_argument("--v2-model", type=Path, default=V2_MODEL)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    if args.samples_per_photo < 1:
        parser.error("--samples-per-photo must be at least 1")

    records = [json.loads(line) for line in (args.data / "moves.jsonl").read_text().splitlines()]
    arrays = np.load(args.data / "moves.npz", allow_pickle=False)
    q_all = v1.validate_probabilities(arrays["probabilities"])
    y_all = arrays["target"]
    if not (len(records) == len(q_all) == len(y_all)):
        raise ValueError("Photo records and probability arrays are misaligned")
    split = arrays["split"].astype(str)
    keep = np.ones(len(records), dtype=bool) if args.split == "all" else split == args.split
    indices = np.flatnonzero(keep)
    if not len(indices):
        raise ValueError(f"No photos found for split {args.split}")

    w1 = v1.load_weights(args.v1_model)
    w2 = v2.load_weights(args.v2_model)
    fixed = v1.fixed_weights()
    methods = ("raw_argmax", "fixed_log_probability", "crf_v1_25", "crf_v2_250")
    output_rows: list[dict] = []
    candidate_counts: list[int] = []
    incoming_counts: list[int] = []
    transition_inventory = []

    for index in indices:
        record = records[int(index)]
        target = y_all[int(index)]
        q = q_all[int(index)]
        target_board = board_from_labels(target)
        candidates = incoming_legal_states(target_board)
        incoming_counts.append(len(candidates))
        selected = choose_states(candidates, record["transition_id"], args.seed,
                                 args.samples_per_photo)
        transition_inventory.append({
            "transition_id": record["transition_id"],
            "image_sha256": record["image_sha256"],
            "setup_id": record["setup_id"],
            "split": str(split[int(index)]),
            "incoming_legal_state_count": len(candidates),
            "unique_sampled_previous_states": len(set(fen for fen, _ in selected)),
        })

        raw_board = q.argmax(axis=-1).astype(np.uint8)
        for sample_index, (previous_fen, synthetic_move) in enumerate(selected):
            options = v1.candidates(previous_fen)
            target_option = options.target_index(target)
            candidate_counts.append(len(options.boards))
            model_boards = {
                "raw_argmax": raw_board,
                "fixed_log_probability": winner(v1.score(q, options, fixed), options),
                "crf_v1_25": winner(v1.score(q, options, w1), options),
                "crf_v2_250": winner(v2.score(q, options, w2), options),
            }
            evaluations = {}
            for method, prediction in model_boards.items():
                prediction = np.asarray(prediction, dtype=np.uint8)
                evaluations[method] = {
                    "exact": bool(np.array_equal(prediction, target)),
                    "square_accuracy": float(np.mean(prediction == target)),
                }
            result = {
                "transition_id": record["transition_id"],
                "image_sha256": record["image_sha256"],
                "setup_id": record["setup_id"],
                "split": str(split[int(index)]),
                "sample_index": sample_index,
                "synthetic_previous_fen": previous_fen,
                "synthetic_forward_move_uci": synthetic_move,
                "target_candidate_index": target_option,
                "candidate_count": len(options.boards),
                "methods": evaluations,
            }
            output_rows.append(result)

    summary = {method: summarize(output_rows, method, args.seed + i * 101, args.samples_per_photo)
               for i, method in enumerate(methods)}
    split_summaries = {}
    for split_name in ("train", "validation", "test"):
        split_rows = [row for row in output_rows if row["split"] == split_name]
        if split_rows:
            split_summaries[split_name] = {
                method: summarize(split_rows, method, args.seed + 3000 + i * 101,
                                  args.samples_per_photo)
                for i, method in enumerate(methods)
            }
    deltas = {}
    for method in ("fixed_log_probability", "crf_v1_25", "crf_v2_250"):
        grouped: dict[str, list[float]] = {}
        for row in output_rows:
            raw = row["methods"]["raw_argmax"]["exact"]
            proposed = row["methods"][method]["exact"]
            grouped.setdefault(row["transition_id"], []).append(float(proposed) - float(raw))
        per_photo_delta = [float(np.mean(values)) for values in grouped.values()]
        cluster_by_photo = {row["transition_id"]: row["setup_id"] for row in output_rows}
        strict_by_photo: dict[str, list[dict]] = {}
        for row in output_rows:
            strict_by_photo.setdefault(row["transition_id"], []).append(row)
        strict_delta = [
            float(all(r["methods"][method]["exact"] for r in states))
            - float(all(r["methods"]["raw_argmax"]["exact"] for r in states))
            for states in strict_by_photo.values()
        ]
        eligible_strict_five = {
            photo: float(all(r["methods"][method]["exact"] for r in states[:5]))
                   - float(all(r["methods"]["raw_argmax"]["exact"] for r in states[:5]))
            for photo, states in strict_by_photo.items() if len(states) >= 5
        }
        eligible_strict_requested = {
            photo: float(all(r["methods"][method]["exact"] for r in states))
                   - float(all(r["methods"]["raw_argmax"]["exact"] for r in states))
            for photo, states in strict_by_photo.items() if len(states) == args.samples_per_photo
        }
        rescued = sum(not r["methods"]["raw_argmax"]["exact"] and r["methods"][method]["exact"]
                      for r in output_rows)
        damaged = sum(r["methods"]["raw_argmax"]["exact"] and not r["methods"][method]["exact"]
                      for r in output_rows)
        deltas[method] = {
            "paired_exact_delta_vs_raw": float(np.mean(per_photo_delta)),
            "paired_delta_bootstrap_95_ci_by_setup": cluster_interval(
                dict(zip(grouped, per_photo_delta)), cluster_by_photo, args.seed + 999),
            "strict_all_previous_states_delta_vs_raw": float(np.mean(strict_delta)),
            "strict_delta_bootstrap_95_ci_by_setup": cluster_interval(
                dict(zip(strict_by_photo, strict_delta)), cluster_by_photo, args.seed + 1000),
            "strict_five_delta_vs_raw": float(np.mean(list(eligible_strict_five.values()))),
            "strict_five_delta_bootstrap_95_ci_by_setup": cluster_interval(
                eligible_strict_five, cluster_by_photo, args.seed + 1001),
            "strict_requested_delta_vs_raw": float(np.mean(list(eligible_strict_requested.values()))),
            "strict_requested_delta_bootstrap_95_ci_by_setup": cluster_interval(
                eligible_strict_requested, cluster_by_photo, args.seed + 1002),
            "raw_wrong_corrected_conditions": int(rescued),
            "raw_correct_made_wrong_conditions": int(damaged),
        }

    grouped_conditions: dict[str, list[dict]] = {}
    for row in output_rows:
        grouped_conditions.setdefault(row["transition_id"], []).append(row)
    cluster_by_photo = {row["transition_id"]: row["setup_id"] for row in output_rows}
    paired_model_comparison = {}
    for method_a, method_b, name in (
        ("crf_v1_25", "crf_v2_250", "v1_minus_v2"),
        ("crf_v1_25", "fixed_log_probability", "v1_minus_fixed"),
        ("crf_v2_250", "fixed_log_probability", "v2_minus_fixed"),
    ):
        single_delta = {}
        strict_five_delta = {}
        strict_requested_delta = {}
        single_a_wins = single_b_wins = 0
        strict_a_wins = strict_b_wins = 0
        for photo, conditions in grouped_conditions.items():
            first = min(conditions, key=lambda row: row["sample_index"])
            a_single = first["methods"][method_a]["exact"]
            b_single = first["methods"][method_b]["exact"]
            single_delta[photo] = float(a_single) - float(b_single)
            single_a_wins += bool(a_single and not b_single)
            single_b_wins += bool(b_single and not a_single)
            if len(conditions) >= 5:
                first_five = sorted(conditions, key=lambda row: row["sample_index"])[:5]
                a_strict = all(row["methods"][method_a]["exact"] for row in first_five)
                b_strict = all(row["methods"][method_b]["exact"] for row in first_five)
                strict_five_delta[photo] = float(a_strict) - float(b_strict)
                strict_a_wins += bool(a_strict and not b_strict)
                strict_b_wins += bool(b_strict and not a_strict)
            if len(conditions) == args.samples_per_photo:
                a_strict_n = all(row["methods"][method_a]["exact"] for row in conditions)
                b_strict_n = all(row["methods"][method_b]["exact"] for row in conditions)
                strict_requested_delta[photo] = float(a_strict_n) - float(b_strict_n)
        paired_model_comparison[name] = {
            "photos_single": len(single_delta),
            "single_paired_accuracy_delta": float(np.mean(list(single_delta.values()))),
            "single_delta_bootstrap_95_ci_by_setup": cluster_interval(
                single_delta, cluster_by_photo, args.seed + 2001),
            "single_a_only_correct_photos": int(single_a_wins),
            "single_b_only_correct_photos": int(single_b_wins),
            "photos_strict_five": len(strict_five_delta),
            "strict_five_paired_accuracy_delta": float(np.mean(list(strict_five_delta.values()))),
            "strict_five_delta_bootstrap_95_ci_by_setup": cluster_interval(
                strict_five_delta, cluster_by_photo, args.seed + 2002),
            "strict_five_a_only_correct_photos": int(strict_a_wins),
            "strict_five_b_only_correct_photos": int(strict_b_wins),
            "photos_strict_requested": len(strict_requested_delta),
            "strict_requested_paired_accuracy_delta": float(np.mean(list(strict_requested_delta.values()))),
            "strict_requested_delta_bootstrap_95_ci_by_setup": cluster_interval(
                strict_requested_delta, cluster_by_photo, args.seed + 2003),
        }

    inference_meta = json.loads((args.data / "moves-inference.json").read_text())
    report = {
        "evaluation_id": "real-photos-synthetic-previous-v1",
        "created_utc": "2026-10-11",
        "dataset": "data/crf/real/robot-v1",
        "source_split": args.split,
        "photos": len(indices),
        "samples_per_photo": args.samples_per_photo,
        "conditions": len(output_rows),
        "random_seed": args.seed,
        "previous_state_method": "uniform fixed-seed sample from distinct valid standard-chess FENs with one legal incoming move whose resulting piece placement exactly matches the publisher-labeled photo board; includes ordinary moves, captures, promotions, en passant, and castling",
        "target_candidate_coverage": 1.0,
        "mean_incoming_legal_state_count": float(np.mean(incoming_counts)),
        "median_incoming_legal_state_count": float(np.median(incoming_counts)),
        "incoming_legal_state_count_range": [int(min(incoming_counts)), int(max(incoming_counts))],
        "source_split_photo_counts": dict(Counter(str(split[int(i)]) for i in indices)),
        "mean_crf_candidate_count": float(np.mean(candidate_counts)),
        "candidate_count_range": [int(min(candidate_counts)), int(max(candidate_counts))],
        "summary_by_method": summary,
        "summary_by_source_split": split_summaries,
        "paired_comparison_vs_raw": deltas,
        "paired_model_comparison": paired_model_comparison,
        "model_sha256": {
            "crf_v1_25": hashlib.sha256(args.v1_model.read_bytes()).hexdigest(),
            "crf_v2_250": hashlib.sha256(args.v2_model.read_bytes()).hexdigest(),
        },
        "baseline_model_sha256": inference_meta["model_sha256"],
        "cached_probabilities_sha256": inference_meta["probabilities_sha256"],
        "evaluation_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "limitations": [
            "These are rectified still photographs, not the application's continuous camera stream.",
            "The synthetic previous state is constructed conditional on the labeled target, so this measures a single-step oracle-state scenario and not end-to-end deployment accuracy.",
            "Multiple states for a photo are repeated conditions, not independent photographs; strict accuracy counts a photo only when every sampled state is correct. Confidence intervals bootstrap publisher setups to account for multiple transitions from one setup.",
            "The CRF weights were trained on synthetic observations only. Public-photo validation results were previously inspected for 67 photos, so the full 176-photo result is descriptive; the publisher test split is the cleanest photo subset.",
        ],
        "transition_inventory": transition_inventory,
        "condition_results": output_rows,
    }
    output = args.output or (ROOT / "data/crf/runs/real-photos-synthetic-previous-v1"
                             / f"{args.split}-seed{args.seed}-n{args.samples_per_photo}.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items()
                      if k not in ("transition_inventory", "condition_results")}, indent=2))
    print(f"report: {output.relative_to(ROOT) if output.is_relative_to(ROOT) else output}")


if __name__ == "__main__":
    main()
