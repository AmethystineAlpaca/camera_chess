#!/usr/bin/env python3
"""CPU training and validation gate for the experimental conditional model.

Uses existing game-level splits. Validation failure stops before test evaluation
or application integration. No visual-model weights or live game state change.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
from pathlib import Path
import shutil
import time

import chess
import numpy as np
import torch

from build_crf_dataset import ROOT, dump, file_hash, PROFILES, OPERATIONS
from crf_postprocessor import (CLASSES, FEATURES, SCHEMA, candidates, fixed_weights,
                               labels, predict, score, torch_scores)


GATES = {
    "candidate_coverage": 1.0,
    "synthetic_validation_exact_minimum": .95,
    "synthetic_validation_gain_over_fixed_minimum": .002,
    "operation_accuracy_regression_tolerance": .01,
    "public_validation_exact_minimum": .95,
    "public_validation_must_match_or_beat_fixed": True,
    "cpu_prediction_p95_seconds_maximum": .05,
}


def prepare(dataset, output, limit=None):
    """Build sparse candidate cache for train/validation only, never test."""
    started = time.perf_counter()
    manifest = json.loads((dataset / "manifest.json").read_text())
    if file_hash(dataset / "transitions.jsonl") != manifest["transition_sha256"]:
        raise ValueError("Transition metadata checksum mismatch")
    rows = [json.loads(s) for s in (dataset / "transitions.jsonl").read_text().splitlines()]
    selected = [i for i, row in enumerate(rows) if row["split"] in ("train", "validation")]
    if limit:
        selected = [i for split in ("train", "validation")
                    for i in [j for j in selected if rows[j]["split"] == split][:limit]]
    mapping = {old: i for i, old in enumerate(selected)}
    q = np.lib.format.open_memmap(output / "probabilities.npy", mode="w+", dtype=np.float32,
                                 shape=(len(selected), len(PROFILES), 64, 13))
    filled = np.zeros((len(selected), len(PROFILES)), dtype=bool)
    for item in manifest["shards"]:
        if item["file"].startswith("test-"):
            continue
        path = dataset / item["file"]
        if file_hash(path) != item["sha256"]:
            raise ValueError(f"Shard checksum mismatch: {path}")
        with np.load(path, allow_pickle=False) as z:
            # NPZ members decompress on each lookup: load each array once.
            transitions = z["transition_index"]
            profile_ids = z["noise_profile"]
            observations = z["probabilities"]
            shard_targets = z["target"]
            for old in np.unique(transitions):
                if int(old) not in mapping:
                    continue
                where = np.flatnonzero(transitions == old)
                index = mapping[int(old)]
                profiles = profile_ids[where].astype(int)
                if sorted(profiles.tolist()) != list(range(len(PROFILES))) or filled[index].any():
                    raise ValueError("Missing or duplicated profile variants")
                q[index, profiles] = observations[where]
                target = labels(chess.Board(rows[int(old)]["current_board_fen"] + " w - - 0 1"))
                if not np.all(shard_targets[where] == target):
                    raise ValueError("Shard target disagrees with metadata")
                filled[index, profiles] = True
    if not filled.all():
        raise ValueError("Missing observations")
    q.flush()
    arrays = {k: [] for k in ("squares", "before", "after", "flags", "operations")}
    offsets, targets, truth = [0], [], []
    counts = []
    for i, old in enumerate(selected):
        row = rows[old]
        options = candidates(row["previous_fen"])
        target = labels(chess.Board(row["current_board_fen"] + " w - - 0 1"))
        target_index = options.target_index(target)  # Fail rather than inject GT.
        if options.operations[target_index] != OPERATIONS.index(row["operation"]):
            raise ValueError("Candidate operation disagrees with label")
        targets.append(target_index); truth.append(target)
        offsets.append(offsets[-1] + len(options.boards)); counts.append(len(options.boards))
        for key in arrays:
            arrays[key].append(getattr(options, key))
        if (i + 1) % 1000 == 0:
            print(f"Candidate coverage: {i + 1}/{len(selected)} transitions, all covered", flush=True)
    packed = {k: np.concatenate(v) for k, v in arrays.items()}
    packed.update(offsets=np.array(offsets, dtype=np.int64), targets=np.array(targets, dtype=np.int64),
                  truth=np.stack(truth), original_index=np.array(selected, dtype=np.int64),
                  split=np.array([rows[i]["split"] for i in selected]),
                  operation=np.array([OPERATIONS.index(rows[i]["operation"]) for i in selected], dtype=np.uint8),
                  phase=np.array([rows[i]["phase"] for i in selected]))
    np.savez_compressed(output / "candidates.npz", **packed)
    report = dict(transitions=len(selected), samples=len(selected) * len(PROFILES),
                  target_coverage=1.0, candidate_count_mean=float(np.mean(counts)),
                  candidate_count_max=max(counts), seconds=time.perf_counter() - started,
                  test_shards_read=False, transition_sha256=manifest["transition_sha256"],
                  source_manifest_sha256=file_hash(dataset / "manifest.json"))
    dump(output / "candidate-coverage.json", report)
    return q, packed, [rows[i] for i in selected], report


def batches(q, data, indices, size, rng=None):
    order = indices.copy()
    if rng is not None:
        rng.shuffle(order)
    for begin in range(0, len(order), size):
        idx = order[begin:begin + size]
        lengths = data["offsets"][idx + 1] - data["offsets"][idx]
        count = int(lengths.max())
        shape = (len(idx), count, 4)
        squares = np.full(shape, 64, dtype=np.int64)
        before = np.zeros(shape, dtype=np.int64); after = np.zeros(shape, dtype=np.int64)
        flags = np.zeros((len(idx), count, 7), dtype=np.float32)
        for i, j in enumerate(idx):
            start, end = data["offsets"][j:j + 2]
            n = end - start
            squares[i, :n] = data["squares"][start:end]
            before[i, :n] = data["before"][start:end]
            after[i, :n] = data["after"][start:end]
            flags[i, :n] = data["flags"][start:end]
        yield (idx, torch.from_numpy(np.array(q[idx])), torch.from_numpy(squares),
               torch.from_numpy(before), torch.from_numpy(after), torch.from_numpy(flags),
               torch.from_numpy(np.arange(count)[None, :] < lengths[:, None]),
               torch.from_numpy(data["targets"][idx]))


def batch_scores(batch, weights, model=None):
    _, q, squares, before, after, flags, mask, _ = batch
    scorer = torch_scores if model is None else model.torch_scores
    return scorer(q, squares, before, after, flags, weights).masked_fill(~mask[:, None, :], -1e9)


def summarize(correct, raw, predictions, operations, profiles, phases):
    def group(values):
        return {str(k): dict(samples=int((values == k).sum()),
                             exact_accuracy=float(correct[values == k].mean())) for k in np.unique(values)}
    return dict(samples=len(correct), exact_accuracy=float(correct.mean()),
                raw_exact_accuracy=float(raw.mean()), wrong_proposals=int((~correct).sum()),
                operation_accuracy=float((predictions == operations).mean()),
                wrong_legal_proposals=int(((~correct) & (predictions == 0)).sum()),
                illegal_events_forced_legal=int(((operations == 2) & (predictions == 0)).sum()),
                by_operation=group(np.array(OPERATIONS)[operations]),
                by_profile=group(profiles), by_phase=group(phases))


@torch.no_grad()
def evaluate(q, data, indices, weights, size, model=None):
    correct, raw, pred_ops, op, profile, phase = [], [], [], [], [], []
    nll, n = 0., 0
    for batch in batches(q, data, indices, size):
        idx = batch[0]; scores = batch_scores(batch, weights, model)
        targets = batch[-1][:, None].expand(-1, len(PROFILES))
        nll += float(torch.nn.functional.cross_entropy(scores.flatten(0, 1), targets.flatten(), reduction="sum"))
        n += targets.numel()
        prediction = scores.argmax(-1).numpy()
        correct.extend((prediction == data["targets"][idx, None]).ravel())
        raw.extend(np.all(q[idx].argmax(-1) == data["truth"][idx, None, :], axis=-1).ravel())
        pred_ops.extend(data["operations"][data["offsets"][idx, None] + prediction].ravel())
        op.extend(np.repeat(data["operation"][idx], len(PROFILES)))
        profile.extend(PROFILES * len(idx))
        phase.extend(np.repeat(data["phase"][idx], len(PROFILES)))
    result = summarize(*[np.array(x) for x in (correct, raw, pred_ops, op, profile, phase)])
    result["conditional_nll"] = nll / n
    return result


def evaluate_real(path, weights, split="validation", model=None):
    rows = [json.loads(line) for line in (path / "moves.jsonl").read_text().splitlines()]
    correct, raw, pred_ops, errors, times = [], [], [], [], []
    with np.load(path / "moves.npz", allow_pickle=False) as z:
        for i, row in enumerate(rows):
            if row["split"] != split:
                continue
            if str(z["split"][i]) != split:
                raise ValueError("Real data split mismatch")
            q = z["probabilities"][i]; target = z["target"][i]
            options = candidates(row["previous_fen"])
            target_index = options.target_index(target)
            started = time.perf_counter()
            predictor = predict if model is None else model.predict
            result = predictor(row["previous_fen"], q, weights)
            times.append(time.perf_counter() - started)
            right = np.array_equal(result["labels"], target)
            correct.append(right); raw.append(np.array_equal(q.argmax(-1), target))
            pred_ops.append(result["operation"])
            if not right:
                errors.append(dict(id=row["id"], true_move=row["move_uci"], predicted_move=result["move_uci"],
                                   predicted_operation=OPERATIONS[result["operation"]],
                                   posterior=result["conditional_probability"],
                                   score_gap=result["score_gap"], target_candidate=target_index))
    if not correct:
        raise ValueError(f"No real examples in {split}")
    return dict(samples=len(correct), split=split, exact_accuracy=float(np.mean(correct)),
                correct_boards=int(sum(correct)), raw_exact_accuracy=float(np.mean(raw)),
                candidate_coverage=1.0, operation_counts=dict(collections.Counter(OPERATIONS[i] for i in pred_ops)),
                cpu_prediction_p50_seconds=float(np.median(times)),
                cpu_prediction_p95_seconds=float(np.percentile(times, 95)),
                errors=errors, limitation="Publisher still photos; legal moves only, no deployment acceptance/latency guarantee")


def validation_gate(fixed, learned, real_fixed, real_learned):
    tests = {
        "synthetic_exact_minimum": learned["exact_accuracy"] >= GATES["synthetic_validation_exact_minimum"],
        "synthetic_gain_over_fixed": learned["exact_accuracy"] - fixed["exact_accuracy"] >= GATES["synthetic_validation_gain_over_fixed_minimum"],
        "clean_observations_preserved": learned["by_profile"]["clean"]["exact_accuracy"] == 1.,
        "operation_subgroups_preserved": all(learned["by_operation"][k]["exact_accuracy"] >= v["exact_accuracy"]
                                            - GATES["operation_accuracy_regression_tolerance"] for k, v in fixed["by_operation"].items()),
        "public_exact_minimum": real_learned["exact_accuracy"] >= GATES["public_validation_exact_minimum"],
        "public_not_worse_than_fixed": real_learned["exact_accuracy"] >= real_fixed["exact_accuracy"],
        "cpu_latency": real_learned["cpu_prediction_p95_seconds"] <= GATES["cpu_prediction_p95_seconds_maximum"],
    }
    return dict(passed=all(tests.values()), checks=tests,
                decision="eligible_for_held_out_test" if all(tests.values()) else "stop_before_integration",
                automatic_commits_authorized=False,
                rationale="95% exact-board validation floor and measurable gain over fixed weights are provisional experiment gates, not a production reliability claim.")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", type=Path, default=ROOT / "data/crf/synthetic-v1")
    p.add_argument("--real", type=Path, default=ROOT / "data/crf/real/robot-v1")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--epochs", type=int, default=12)
    p.add_argument("--batch-size", type=int, default=32)
    p.add_argument("--limit", type=int, help="Smoke test: maximum transitions per split")
    args = p.parse_args()
    if args.epochs < 1 or args.batch_size < 1 or (args.limit is not None and args.limit < 1):
        raise ValueError("Epochs, batch size, and smoke limit must be positive")
    if args.output.exists():
        raise ValueError("Output already exists; use a fresh run directory")
    args.output.mkdir(parents=True)
    torch.set_num_threads(4)
    torch.manual_seed(20261011)
    rng = np.random.default_rng(20261011)
    config = dict(created_date="2026-10-11", seed=20261011, model="single_step_conditional_log_linear",
                  parameters=len(FEATURES), features=FEATURES, gates=GATES, epochs=args.epochs,
                  batch_size=args.batch_size, learning_rate=.03, l2=.001,
                  train_source="synthetic train only", checkpoint_selection="synthetic validation conditional NLL",
                  public_validation="one evaluation after checkpoint selection; no fitting on public photos",
                  test_policy="do not read synthetic test shards or score real test until validation gate passes",
                  smoke_limit=args.limit, torch=torch.__version__, numpy=np.__version__, device="cpu",
                  training_script_sha256=file_hash(__file__),
                  model_script_sha256=file_hash(Path(__file__).with_name("crf_postprocessor.py")))
    dump(args.output / "config.json", config)
    for name in ["train_crf.py", "crf_postprocessor.py"]:
        shutil.copy2(Path(__file__).with_name(name), args.output / name)
    q, data, _, coverage = prepare(args.dataset, args.output, args.limit)
    train = np.flatnonzero(data["split"] == "train")
    val = np.flatnonzero(data["split"] == "validation")
    fixed = evaluate(q, data, val, torch.tensor(fixed_weights()), args.batch_size)
    dump(args.output / "fixed-validation.json", fixed)
    print(f"Fixed validation: exact={fixed['exact_accuracy']:.4%}, NLL={fixed['conditional_nll']:.5f}", flush=True)
    weights = torch.nn.Parameter(torch.tensor(fixed_weights()))
    optimizer = torch.optim.Adam([weights], lr=.03)
    best, history, best_epoch = math.inf, [], 0
    best_weights = None
    started = time.perf_counter()
    for epoch in range(1, args.epochs + 1):
        epoch_start = time.perf_counter(); total_loss, total_n = 0., 0
        for batch in batches(q, data, train, args.batch_size, rng):
            optimizer.zero_grad()
            scores = batch_scores(batch, weights)
            targets = batch[-1][:, None].expand(-1, len(PROFILES))
            loss = torch.nn.functional.cross_entropy(scores.flatten(0, 1), targets.flatten())
            objective = loss + .001 * weights.square().sum()
            if not torch.isfinite(objective):
                raise ValueError("Non-finite training objective")
            objective.backward(); optimizer.step()
            total_loss += float(loss.detach()) * targets.numel(); total_n += targets.numel()
        validation = evaluate(q, data, val, weights.detach(), args.batch_size)
        entry = dict(epoch=epoch, train_nll=total_loss / total_n, validation_nll=validation["conditional_nll"],
                     validation_exact=validation["exact_accuracy"], seconds=time.perf_counter() - epoch_start)
        history.append(entry); dump(args.output / "history.json", history)
        print(json.dumps(entry), flush=True)
        if validation["conditional_nll"] < best:
            best = validation["conditional_nll"]; best_weights = weights.detach().numpy().copy(); best_epoch = epoch
        if epoch in (6, 9):
            for group in optimizer.param_groups:
                group["lr"] *= .3
    training_seconds = time.perf_counter() - started
    checkpoint = dict(schema=SCHEMA, classes=CLASSES, features=FEATURES, weights=best_weights.tolist(),
                      epoch=best_epoch, validation_nll=best, experimental=True,
                      decision="review_required", automatic_commit=False)
    dump(args.output / "model.json", checkpoint)
    learned = evaluate(q, data, val, torch.tensor(best_weights), args.batch_size)
    trained = evaluate(q, data, train, torch.tensor(best_weights), args.batch_size)
    dump(args.output / "learned-validation.json", learned)
    dump(args.output / "learned-training.json", trained)
    if args.limit:
        report = dict(candidate_coverage=coverage, training_seconds=training_seconds,
                      best_epoch=best_epoch, fixed_validation=fixed, learned_validation=learned,
                      gate=dict(passed=False, decision="smoke_only_not_integration_evidence"),
                      real_evaluated=False, test_evaluated=False, application_integrated=False)
        dump(args.output / "report.json", report)
        print(json.dumps({k: report[k] for k in ["training_seconds", "best_epoch", "gate"]}), flush=True)
        return
    real_fixed = evaluate_real(args.real, fixed_weights())
    real_learned = evaluate_real(args.real, best_weights)
    dump(args.output / "real-fixed-validation.json", real_fixed)
    dump(args.output / "real-learned-validation.json", real_learned)
    gate = validation_gate(fixed, learned, real_fixed, real_learned)
    report = dict(candidate_coverage=coverage, training_seconds=training_seconds,
                  best_epoch=best_epoch, fixed_validation=fixed, learned_validation=learned,
                  real_fixed={k: v for k, v in real_fixed.items() if k != "errors"},
                  real_learned={k: v for k, v in real_learned.items() if k != "errors"},
                  gate=gate, test_evaluated=False, application_integrated=False,
                  model_sha256=file_hash(args.output / "model.json"))
    dump(args.output / "report.json", report)
    print(json.dumps({k: report[k] for k in ["training_seconds", "best_epoch", "real_fixed", "real_learned", "gate"]}, indent=2), flush=True)


if __name__ == "__main__":
    main()
