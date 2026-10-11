#!/usr/bin/env python3
"""Evaluate saved neural scorers on the exact existing 1/5/10 predecessor conditions."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import time

import chess
import numpy as np
import torch

import crf_postprocessor as v1
import crf_postprocessor_v2 as v2
from build_crf_dataset import ROOT, dump, file_hash
from evaluate_crf_synthetic_previous import summarize, cluster_interval
from neural_postprocessor import load_model, score_one

SOURCE = ROOT / "data/crf/runs/real-photos-synthetic-previous-v1"
PHOTO_DATA = ROOT / "data/crf/real/robot-v1"
NAMES = {"raw_argmax": "Raw argmax", "fixed_log_probability": "Fixed log-probability",
         "crf_v1_25": "CRF v1", "crf_v2_250": "CRF v2",
         "feature_mlp": "Feature MLP (1 hidden layer)", "square_mlp": "Shared square MLP",
         "move_mlp1": "Changed-square MLP (1 hidden layer)",
         "move_mlp2": "Changed-square MLP (2 hidden layers)",
         "cnn": "Spatial CNN", "attention": "Spatial Attention"}
MARKER = "## Neural postprocessor comparison — 2026-10-11"


def paired(rows, method, seed):
    grouped = {}
    clusters = {}
    for row in rows:
        grouped.setdefault(row["transition_id"], []).append(row)
        clusters[row["transition_id"]] = row["setup_id"]
    result = {}
    for n in (1, 5, 10):
        deltas = {}
        won = lost = 0
        for photo, items in grouped.items():
            if len(items) < n:
                continue
            items = sorted(items, key=lambda r: r["sample_index"])[:n]
            a = all(r["methods"][method]["exact"] for r in items)
            b = all(r["methods"]["crf_v1_25"]["exact"] for r in items)
            deltas[photo] = float(a)-float(b)
            won += a and not b
            lost += b and not a
        result[str(n)] = dict(delta=float(np.mean(list(deltas.values()))),
                              bootstrap_95_ci_by_setup=cluster_interval(deltas, clusters, seed+n),
                              new_only_correct=int(won), v1_only_correct=int(lost), photos=len(deltas))
    return result


def cells(summary):
    counts = [(summary["single_random_previous_correct_photos"], summary["photos"]),
              (summary["strict_five_previous_states_correct_photos"], summary["photos_with_five_distinct_previous_states"]),
              (summary["strict_requested_previous_states_correct_photos"], summary["photos_with_requested_distinct_previous_states"])]
    return [f"{a}/{b} — {100*a/b:.2f}%" for a, b in counts]


def markdown(report, training, run):
    lines = [MARKER, "",
        "This comparison trains only the postprocessor. ChessQ Lite V4 remains frozen; inputs are its existing 64×13 cached probabilities and the supplied previous board. All six new scorers add a learned residual to the frozen 25-weight v1 score and retain the identical candidate generator. No visual features, image-model training, temporal data, or live acceptance gates are used.", "",
        "### Training and selection", "",
        "All models reuse the existing 160,000 synthetic training observations (16,000 transitions × 10 noise profiles) and 20,000 synthetic validation observations (2,000 transitions × 10). No real photos are used for fitting or checkpoint selection. One run per architecture, seed `20261011`; CPU float32, AdamW, learning rate 0.001, weight decay 0.0001, gradient norm clipped at 5, batch size 16 transitions including all ten profiles, 12 epochs, learning rate multiplied by 0.3 after epochs 6 and 9. Checkpoints minimize synthetic validation NLL; epoch 0 (exactly v1) is also eligible. Zero-initialized output layers make every architecture start at v1. Architectures and training settings were fixed before this photo evaluation; there was no tuning on the photo results. This is a first architecture screen under one common optimizer schedule, not a claim that each architecture is fully optimized.", "",
        "| Model | Residual architecture | Trainable parameters | Selected epoch | Train exact | Validation exact | Validation NLL | Training + epoch validation time |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    descriptions = {
        "feature_mlp": "25 existing feature differences → 32 SiLU → 1",
        "square_mlp": "Per-square probabilities, scaled log probabilities, prior one-hot: 39 → 32 SiLU → 13; operation head",
        "move_mlp1": "Shared 39→8 encoder; four changed-square slots + piece embeddings + coordinates + log evidence + flags: 87 → 32 SiLU → 1",
        "move_mlp2": "Same 87 inputs → 32 SiLU → 32 SiLU → 1",
        "cnn": "8×8 probability/prior/coordinate map: 41→16 (3×3)→16 (3×3)→13 (1×1); operation head",
        "attention": "41→32 projection; 1 pre-norm encoder, 4 heads, FFN 64, dropout 0; 13-class head + operation head"}
    for kind, r in training.items():
        lines.append(f"| {NAMES[kind]} | {descriptions[kind]} | {r['trainable_parameters']:,} | {r['best_epoch']} | {r['training']['exact_accuracy']:.3%} | {r['validation']['exact_accuracy']:.3%} | {r['validation']['conditional_nll']:.6f} | {r['training_seconds']:.1f} s |")
    lines.extend(["", "All models additionally retain 25 frozen v1 coefficients. Coordinates follow the existing rank-8-to-rank-1 square ordering; changed-square slots use the candidate generator's sorted square order with neutral padding. The visual feature differences in Feature MLP use log terms scaled by 1/8. The shared square encoder receives all 13 probabilities, their scaled logs, and the previous square label. Unchanged-square contributions cancel in the square/CNN/Attention scorers; changed-square MLPs jointly score the four slots and operation flags.", "",
        "### Same 176-photo evaluation", "",
        "The saved ten-state report supplies the exact predecessor FENs and ordering; the saved five-state report was checked against its prefixes. Raw, fixed, v1 and v2 predictions were recomputed and matched every saved condition. Denominators and all-correct definitions are unchanged: one predecessor uses 176 photos, all five uses 174, and all ten uses 172. Every model receives the same 1,738 conditions. No voting or best-of-N selection is performed.", "",
        "| Model | One predecessor | All 5 correct | All 10 correct | Photo-weighted square accuracy |",
        "| --- | ---: | ---: | ---: | ---: |"])
    for kind, s in report["summary_by_method"].items():
        lines.append(f"| {NAMES[kind]} | {' | '.join(cells(s))} | {s['square_accuracy']:.2%} |")
    lines.extend(["", "### Results by source-photo split", "",
                  "These are Felix's source splits; none was used to train the new postprocessors. The test-photo results were already inspected in earlier evaluations and are not a fresh untouched test.", "",
                  "| Source split | Model | One predecessor | All 5 correct | All 10 correct |",
                  "| --- | --- | ---: | ---: | ---: |"])
    for split, summaries in report["summary_by_source_split"].items():
        for kind, s in summaries.items():
            lines.append(f"| {split} | {NAMES[kind]} | {' | '.join(cells(s))} |")
    lines.extend(["", "### Paired differences from v1", "",
                  "Percentage-point differences and 95% percentile intervals from 20,000 setup-cluster bootstrap resamples. They condition on the existing predecessor seed and one training seed; intervals are descriptive and unadjusted for multiple model comparisons.", "",
                  "| Model | One predecessor Δ [95% CI] | All 5 Δ [95% CI] | All 10 Δ [95% CI] |",
                  "| --- | ---: | ---: | ---: |"])
    for kind, values in report["paired_vs_v1"].items():
        parts = []
        for n in ("1", "5", "10"):
            r = values[n]
            lo, hi = r["bootstrap_95_ci_by_setup"]
            parts.append(f"{100*r['delta']:+.2f} [{100*lo:+.2f}, {100*hi:+.2f}]")
        lines.append(f"| {NAMES[kind]} | {' | '.join(parts)} |")
    lines.extend(["", "### Error confidence and scoring cost", "",
                  "Wrong high-confidence conditions count wrong top-ranked boards with candidate-relative posterior ≥0.90. Conditions reuse photos and are not independent events. Timing is CPU scoring of cached probabilities with already-generated candidates, including input tensor conversion; it excludes visual inference, candidate generation, live gates, and camera latency. Each model was warmed up once before measurement.", "",
                  "| Model | Wrong conditions | Wrong with posterior ≥0.90 | Conditional NLL | Scoring p50 | Scoring p95 |",
                  "| --- | ---: | ---: | ---: | ---: | ---: |"])
    for kind, r in report["diagnostics"].items():
        lines.append(f"| {NAMES[kind]} | {r['wrong_conditions']} | {r['wrong_high_confidence']} | {r['conditional_nll']:.4f} | {1000*r['scoring_p50_seconds']:.3f} ms | {1000*r['scoring_p95_seconds']:.3f} ms |")
    lines.extend(["", "### Interpretation and artifacts", "",
                  "These results compare conditional scoring on real still-photo probabilities with synthetic predecessors constructed from the correct current label. Candidate coverage is guaranteed by that construction, not measured in deployment. They do not establish live commit accuracy, recovery from an incorrect previous board, or temporal behavior. All six models are retained regardless of photo performance; none is integrated into the application. Synthetic test shards remain unread. This evaluation workflow does not write to the experiment logs.", "",
                  f"Run directory (Git-ignored): `{run.relative_to(ROOT)}`. Each model has `model.pt`, `history.json`, and `report.json`; the run has source snapshots, configuration, hashes, baseline validation reproduction, and training summary. `photo-evaluation.json` retains all predecessor conditions and each new model's predicted 64-square board, move, operation, posterior, target NLL, correctness, and score timing. It also records dataset, baseline, and checkpoint hashes. Full checkpoint loading was verified before evaluation.", "",
                  "Reproduction (use a new training directory; existing outputs are rejected):", "", "```bash",
                  "research/edgetam-env/bin/python scripts/train_neural_postprocessors.py --output data/crf/runs/neural-postprocessors-reproduction",
                  "research/edgetam-env/bin/python scripts/evaluate_neural_postprocessors.py --run data/crf/runs/neural-postprocessors-reproduction",
                  "```", ""])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--write-log", type=Path)
    args = parser.parse_args()
    output = args.run / "photo-evaluation.json"
    if output.exists():
        parser.error("Photo report exists; refusing to overwrite")
    config = json.loads((args.run / "config.json").read_text())
    if config["smoke_limit"]:
        parser.error("Smoke runs must not score photos")
    complete = json.loads((args.run / "training-complete.json").read_text())
    if complete["models"] != config["models"]:
        raise ValueError("Training suite incomplete")
    torch.set_num_threads(config["cpu_threads"])
    source = json.loads((SOURCE / "all-seed20261011-n10.json").read_text())
    five = json.loads((SOURCE / "all-seed20261011-n5.json").read_text())
    expected_five = [r for r in source["condition_results"] if r["sample_index"] < 5]
    if expected_five != five["condition_results"]:
        raise ValueError("Five-state report is not a prefix of ten-state report")
    metadata = [json.loads(s) for s in (PHOTO_DATA / "moves.jsonl").read_text().splitlines()]
    by_id = {r["transition_id"]: i for i, r in enumerate(metadata)}
    with np.load(PHOTO_DATA / "moves.npz", allow_pickle=False) as z:
        q_all, truth, splits = z["probabilities"], z["target"], z["split"]
    v1.validate_probabilities(q_all)
    baseline_paths = {"crf_v1_25": ROOT / "research/models/crf-postprocessor-v1.json",
                      "crf_v2_250": ROOT / "research/models/crf-postprocessor-v2-250.json"}
    for name, p in baseline_paths.items():
        if file_hash(p) != source["model_sha256"][name]:
            raise ValueError("Baseline checksum changed")
    w1 = v1.load_weights(baseline_paths["crf_v1_25"])
    w2 = v2.load_weights(baseline_paths["crf_v2_250"])
    training = json.loads((args.run / "training-summary.json").read_text())
    models = {}
    for kind in complete["models"]:
        path = args.run / kind / "model.pt"
        if file_hash(path) != training[kind]["model_sha256"]:
            raise ValueError("Checkpoint changed")
        models[kind], _ = load_model(path)
    rows = copy.deepcopy(source["condition_results"])
    times = {k: [] for k in models}
    for number, row in enumerate(rows):
        i = by_id[row["transition_id"]]
        if row["image_sha256"] != metadata[i]["image_sha256"] or row["split"] != str(splits[i]):
            raise ValueError("Photo alignment mismatch")
        q, target = q_all[i], truth[i]
        previous = chess.Board(row["synthetic_previous_fen"])
        forward = chess.Move.from_uci(row["synthetic_forward_move_uci"])
        if forward not in previous.legal_moves:
            raise ValueError("Saved predecessor move not legal")
        after = previous.copy(); after.push(forward)
        if not np.array_equal(v1.labels(after), target):
            raise ValueError("Saved predecessor does not yield photo target")
        options = v1.candidates(row["synthetic_previous_fen"])
        if len(options.boards) != row["candidate_count"] or options.target_index(target) != row["target_candidate_index"]:
            raise ValueError("Candidate set changed")
        baselines = {"raw_argmax": q.argmax(-1),
                     "fixed_log_probability": options.boards[v1.score(q, options, v1.fixed_weights()).argmax()],
                     "crf_v1_25": options.boards[v1.score(q, options, w1).argmax()],
                     "crf_v2_250": options.boards[v2.score(q, options, w2).argmax()]}
        for kind, prediction in baselines.items():
            calculated = dict(exact=bool(np.array_equal(prediction, target)),
                              square_accuracy=float(np.mean(prediction == target)))
            if row["methods"][kind] != calculated:
                raise ValueError(f"Existing result not reproduced: {kind}, {number}")
        for kind, model in models.items():
            if number == 0:
                score_one(model, q, v1.labels(previous), options)
            started = time.perf_counter()
            scores = score_one(model, q, v1.labels(previous), options)
            seconds = time.perf_counter()-started
            times[kind].append(seconds)
            best = int(scores.argmax())
            logz = float(np.logaddexp.reduce(scores.astype(np.float64)))
            prediction = options.boards[best]
            row["methods"][kind] = dict(exact=bool(np.array_equal(prediction, target)),
                square_accuracy=float(np.mean(prediction == target)),
                labels=prediction.tolist(), candidate_index=best,
                move_uci=options.moves[best], operation=int(options.operations[best]),
                posterior=float(np.exp(scores[best]-logz)),
                target_nll=float(logz-scores[row["target_candidate_index"]]), scoring_seconds=seconds)
        if (number+1) % 200 == 0:
            print(json.dumps(dict(photo_conditions_completed=number+1, total=len(rows))), flush=True)
    methods = list(rows[0]["methods"])
    summary = {k: summarize(rows, k, source["random_seed"]+i*101, 10) for i, k in enumerate(methods)}
    split_summary = {split: {k: summarize([r for r in rows if r["split"] == split], k,
                                         source["random_seed"]+3000+i*101, 10)
                             for i, k in enumerate(methods)} for split in ("train", "validation", "test")}
    diagnostics = {}
    for kind in models:
        values = [r["methods"][kind] for r in rows]
        diagnostics[kind] = dict(wrong_conditions=sum(not v["exact"] for v in values),
            wrong_high_confidence=sum(not v["exact"] and v["posterior"] >= .9 for v in values),
            conditional_nll=float(np.mean([v["target_nll"] for v in values])),
            scoring_p50_seconds=float(np.median(times[kind])),
            scoring_p95_seconds=float(np.percentile(times[kind], 95)))
    report = dict(evaluation_id="neural-postprocessors-20261011", photos=source["photos"],
                  conditions=len(rows), seed=source["random_seed"], target_candidate_coverage=1.,
                  summary_by_method=summary, summary_by_source_split=split_summary,
                  paired_vs_v1={k: paired(rows, k, source["random_seed"]+9000) for k in models},
                  diagnostics=diagnostics, condition_results=rows,
                  checkpoint_sha256={k: file_hash(args.run/k/"model.pt") for k in models},
                  source_sha256={str(p.relative_to(ROOT)): file_hash(p) for p in
                      [SOURCE/"all-seed20261011-n10.json", SOURCE/"all-seed20261011-n5.json",
                       PHOTO_DATA/"moves.jsonl", PHOTO_DATA/"moves.npz", Path(__file__)]},
                  baseline_model_sha256=source["baseline_model_sha256"],
                  all_existing_condition_results_reproduced=True)
    dump(output, report)
    section = markdown(report, training, args.run.resolve())
    (args.run / "evaluation-section.md").write_text(section)
    if args.write_log:
        text = args.write_log.read_text()
        if MARKER in text:
            raise ValueError("Evaluation section already exists")
        args.write_log.write_text(text.rstrip()+"\n\n"+section)
    print(json.dumps({k: cells(s) for k, s in summary.items()}, indent=2), flush=True)


if __name__ == "__main__":
    main()
