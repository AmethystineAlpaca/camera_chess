#!/usr/bin/env python3
"""Train a separately named v2-250 experiment on the existing v1 data splits.

Preserves v1 checkpoints/runs, scores validation only, never changes the live
adapter. Hyperparameters and epoch selection match E06; no public-photo tuning.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import shutil
import time

import numpy as np
import torch

from build_crf_dataset import ROOT, PROFILES, dump, file_hash
import crf_postprocessor as v1
import crf_postprocessor_v2 as v2
from train_crf import GATES, prepare, batches, batch_scores, evaluate, evaluate_real, validation_gate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "data/crf/synthetic-v1")
    parser.add_argument("--real", type=Path, default=ROOT / "data/crf/real/robot-v1")
    parser.add_argument("--baseline", type=Path, default=ROOT / "research/models/crf-postprocessor-v1.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=12)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--limit", type=int, help="Synthetic-only smoke test: transitions per split")
    args = parser.parse_args()
    if args.epochs < 1 or args.batch_size < 1 or (args.limit is not None and args.limit < 1):
        parser.error("Epochs, batch size, and smoke limit must be positive")
    if args.output.exists():
        parser.error("Output already exists; choose a new run directory")
    baseline_weights = v1.load_weights(args.baseline)
    protected = {args.baseline, Path(v1.__file__)}
    protected.update((ROOT / "data/crf/runs/crf-v1-run2").glob("*.json"))
    preserved = {str(p): file_hash(p) for p in sorted(protected)}
    args.output.mkdir(parents=True)
    torch.set_num_threads(4)
    torch.manual_seed(20261011)
    rng = np.random.default_rng(20261011)
    sources = ["train_crf_v2.py", "crf_postprocessor_v2.py", "train_crf.py",
               "crf_postprocessor.py", "build_crf_dataset.py"]
    config = dict(created_date="2026-10-11", model_id=v2.MODEL_ID, schema=v2.SCHEMA,
                  model="single_step_conditional_log_linear", parameters=len(v2.FEATURES),
                  features=v2.FEATURES, seed=20261011, epochs=args.epochs,
                  batch_size=args.batch_size, learning_rate=.03, learning_rate_decay_epochs=[6, 9],
                  learning_rate_decay_factor=.3, l2=.001, optimizer="Adam", cpu_threads=4,
                  initialization="zero residuals; exactly fixed sum-log-probability scoring",
                  regularization_note="Same L2 coefficient as v1, on v2 residual coordinates; induced priors differ",
                  dataset=str(args.dataset), real=str(args.real), baseline=str(args.baseline),
                  train_source="synthetic train only; unchanged v1 game splits and all ten profiles",
                  checkpoint_selection="synthetic validation conditional NLL",
                  public_validation="one evaluation after checkpoint selection; no fitting or tuning on public photos",
                  test_policy="validation-only comparison; no held-out test scoring even if screening passes",
                  gates=GATES, smoke_limit=args.limit, device="cpu", torch=torch.__version__, numpy=np.__version__,
                  source_sha256={name: file_hash(Path(__file__).with_name(name)) for name in sources},
                  preserved_v1_sha256=preserved)
    dump(args.output / "config.json", config)
    for name in sources:
        shutil.copy2(Path(__file__).with_name(name), args.output / name)
    q, data, _, coverage = prepare(args.dataset, args.output, args.limit)
    train = np.flatnonzero(data["split"] == "train")
    val = np.flatnonzero(data["split"] == "validation")
    fixed = evaluate(q, data, val, torch.tensor(v1.fixed_weights()), args.batch_size)
    initial = evaluate(q, data, val, torch.tensor(v2.fixed_weights()), args.batch_size, v2)
    if (initial["exact_accuracy"] != fixed["exact_accuracy"]
            or abs(initial["conditional_nll"] - fixed["conditional_nll"]) > 1e-6):
        raise ValueError("v2 initialization does not match the fixed baseline")
    dump(args.output / "fixed-validation.json", fixed)
    print(f"v2 parameters={len(v2.FEATURES)}; fixed validation={fixed['exact_accuracy']:.4%}", flush=True)
    weights = torch.nn.Parameter(torch.tensor(v2.fixed_weights()))
    optimizer = torch.optim.Adam([weights], lr=.03)
    best, best_epoch, best_weights, history = math.inf, None, None, []
    started = time.perf_counter()
    for epoch in range(1, args.epochs + 1):
        epoch_started = time.perf_counter()
        total_loss, n = 0., 0
        for batch in batches(q, data, train, args.batch_size, rng):
            optimizer.zero_grad()
            scores = batch_scores(batch, weights, v2)
            targets = batch[-1][:, None].expand(-1, len(PROFILES))
            loss = torch.nn.functional.cross_entropy(scores.flatten(0, 1), targets.flatten())
            objective = loss + .001 * weights.square().sum()
            if not torch.isfinite(objective):
                raise ValueError("Non-finite training objective")
            objective.backward()
            optimizer.step()
            total_loss += float(loss.detach()) * targets.numel()
            n += targets.numel()
        validation = evaluate(q, data, val, weights.detach(), args.batch_size, v2)
        entry = dict(epoch=epoch, train_nll=total_loss / n,
                     validation_nll=validation["conditional_nll"],
                     validation_exact=validation["exact_accuracy"],
                     learning_rate=optimizer.param_groups[0]["lr"], seconds=time.perf_counter() - epoch_started)
        history.append(entry)
        dump(args.output / "history.json", history)
        print(json.dumps(entry), flush=True)
        if validation["conditional_nll"] < best:
            best = validation["conditional_nll"]
            best_epoch, best_weights = epoch, weights.detach().numpy().copy()
        if epoch in (6, 9):
            for group in optimizer.param_groups:
                group["lr"] *= .3
    training_seconds = time.perf_counter() - started
    checkpoint = dict(schema=v2.SCHEMA, model_id=v2.MODEL_ID, classes=v2.CLASSES,
                      features=v2.FEATURES, weights=best_weights.tolist(), epoch=best_epoch,
                      validation_nll=best, experimental=True, decision="review_required", automatic_commit=False,
                      training_manifest_sha256=coverage["source_manifest_sha256"])
    dump(args.output / "model.json", checkpoint)
    # Evaluate the serialized artifact, not just in-memory optimizer state.
    selected = v2.load_weights(args.output / "model.json")
    learned = evaluate(q, data, val, torch.tensor(selected), args.batch_size, v2)
    trained = evaluate(q, data, train, torch.tensor(selected), args.batch_size, v2)
    baseline = evaluate(q, data, val, torch.tensor(baseline_weights), args.batch_size)
    baseline_train = evaluate(q, data, train, torch.tensor(baseline_weights), args.batch_size)
    for name, report in [("learned-validation", learned), ("learned-training", trained),
                         ("baseline-v1-validation", baseline), ("baseline-v1-training", baseline_train)]:
        dump(args.output / f"{name}.json", report)
    report = dict(model_id=v2.MODEL_ID, parameters=len(v2.FEATURES), candidate_coverage=coverage,
                  training_seconds=training_seconds, best_epoch=best_epoch, fixed_validation=fixed,
                  learned_training=trained, learned_validation=learned,
                  baseline_v1_training=baseline_train, baseline_v1_validation=baseline,
                  test_evaluated=False, application_integrated=False,
                  model_sha256=file_hash(args.output / "model.json"))
    if args.limit:
        report.update(real_evaluated=False, gate=dict(passed=False, decision="smoke_only_not_integration_evidence"))
    else:
        report["real_source_sha256"] = {name: file_hash(args.real / name) for name in ("moves.jsonl", "moves.npz")}
        real_fixed = evaluate_real(args.real, v1.fixed_weights())
        real_baseline = evaluate_real(args.real, baseline_weights)
        real_learned = evaluate_real(args.real, selected, model=v2)
        for name, value in [("real-fixed-validation", real_fixed), ("real-baseline-v1-validation", real_baseline),
                            ("real-learned-validation", real_learned)]:
            dump(args.output / f"{name}.json", value)
        for key, value in [("real_fixed", real_fixed), ("real_baseline_v1", real_baseline), ("real_learned", real_learned)]:
            report[key] = {k: v for k, v in value.items() if k != "errors"}
        old_errors = {e["id"] for e in real_baseline["errors"]}
        new_errors = {e["id"] for e in real_learned["errors"]}
        report["real_paired_comparison"] = dict(v1_wrong_v2_right=len(old_errors - new_errors),
                                              v1_right_v2_wrong=len(new_errors - old_errors),
                                              both_wrong=len(old_errors & new_errors),
                                              both_right=real_learned["samples"] - len(old_errors | new_errors))
        report.update(real_evaluated=True, gate=validation_gate(fixed, learned, real_fixed, real_learned))
    unchanged = all(file_hash(Path(p)) == digest for p, digest in preserved.items())
    if not unchanged:
        raise ValueError("A protected v1 artifact changed during the run")
    report["preserved_v1_artifacts_unchanged"] = unchanged
    dump(args.output / "report.json", report)
    print(json.dumps({k: report[k] for k in ("model_id", "training_seconds", "best_epoch", "gate")}, indent=2), flush=True)


if __name__ == "__main__":
    main()
