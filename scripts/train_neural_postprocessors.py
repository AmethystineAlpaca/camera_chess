#!/usr/bin/env python3
"""Train frozen-recognizer residual models, selecting only on synthetic validation."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import shutil
import time

import chess
import numpy as np
import torch
from torch.nn import functional as F

import crf_postprocessor as v1
from build_crf_dataset import ROOT, PROFILES, dump, file_hash
from neural_postprocessor import KINDS, ResidualScorer, load_model
from train_crf import batches, evaluate as evaluate_baseline, summarize

SEED = 20261011
BASELINE = ROOT / "research/models/crf-postprocessor-v1.json"
CACHE = ROOT / "data/crf/runs/crf-v2-250-run1"
DATASET = ROOT / "data/crf/synthetic-v1"


def read_cache(cache, dataset):
    coverage = json.loads((cache / "candidate-coverage.json").read_text())
    if file_hash(dataset / "manifest.json") != coverage["source_manifest_sha256"]:
        raise ValueError("Cache manifest mismatch")
    if file_hash(dataset / "transitions.jsonl") != coverage["transition_sha256"]:
        raise ValueError("Cache transition mismatch")
    q = np.load(cache / "probabilities.npy", mmap_mode="r")
    with np.load(cache / "candidates.npz", allow_pickle=False) as z:
        data = {k: z[k] for k in z.files}
    rows = [json.loads(s) for s in (dataset / "transitions.jsonl").read_text().splitlines()]
    selected = [rows[i] for i in data["original_index"]]
    if len(q) != len(selected) or set(data["split"]) != {"train", "validation"}:
        raise ValueError("Unexpected training cache splits")
    if any(row["split"] != split for row, split in zip(selected, data["split"])):
        raise ValueError("Cache row split mismatch")
    data["previous"] = np.stack([v1.labels(chess.Board(r["previous_fen"])) for r in selected]).astype(np.int64)
    return q, data, coverage


def forward_batch(model, data, batch):
    idx, q, squares, before, after, flags, mask, target = batch
    previous = torch.from_numpy(data["previous"][idx])
    return model(q, previous, squares, before, after, flags, mask)


@torch.no_grad()
def evaluate(model, q, data, indices, size):
    model.eval()
    correct, raw, pred_ops, operations, profiles, phases = [], [], [], [], [], []
    nll, n = 0., 0
    for batch in batches(q, data, indices, size):
        idx = batch[0]
        scores = forward_batch(model, data, batch)
        target = batch[-1][:, None].expand(-1, len(PROFILES))
        nll += float(F.cross_entropy(scores.flatten(0, 1), target.flatten(), reduction="sum"))
        n += target.numel()
        prediction = scores.argmax(-1).numpy()
        correct.extend((prediction == data["targets"][idx, None]).ravel())
        raw.extend(np.all(q[idx].argmax(-1) == data["truth"][idx, None], axis=-1).ravel())
        pred_ops.extend(data["operations"][data["offsets"][idx, None] + prediction].ravel())
        operations.extend(np.repeat(data["operation"][idx], len(PROFILES)))
        profiles.extend(PROFILES * len(idx))
        phases.extend(np.repeat(data["phase"][idx], len(PROFILES)))
    result = summarize(*[np.asarray(x) for x in (correct, raw, pred_ops, operations, profiles, phases)])
    result["conditional_nll"] = nll / n
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--models", nargs="+", choices=KINDS, default=list(KINDS))
    parser.add_argument("--epochs", type=int, default=12)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--limit", type=int, help="Synthetic-only smoke: transitions per split")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; use a new directory")
    if min(args.epochs, args.batch_size, args.threads, args.limit or 1) < 1:
        parser.error("Counts must be positive")
    torch.set_num_threads(args.threads)
    torch.use_deterministic_algorithms(True)
    args.output.mkdir(parents=True)
    protected = [BASELINE, ROOT / "research/models/crf-postprocessor-v2-250.json",
                 ROOT / "docs/crf-experiment-log.md",
                 ROOT / "docs/crf-experiment-log-2026-10-11-evaluation-issues.md"]
    hashes = {str(p.relative_to(ROOT)): file_hash(p) for p in protected}
    sources = ["neural_postprocessor.py", "train_neural_postprocessors.py", "train_crf.py",
               "crf_postprocessor.py", "evaluate_neural_postprocessors.py",
               "evaluate_crf_synthetic_previous.py"]
    config = dict(schema=1, date="2026-10-11", models=args.models, seed=SEED,
                  epochs=args.epochs, batch_size_transitions=args.batch_size,
                  profiles_per_transition=len(PROFILES), cpu_threads=args.threads,
                  optimizer="AdamW", learning_rate=.001, weight_decay=.0001,
                  lr_decay_epochs=[6, 9], lr_decay_factor=.3,
                  selection="minimum synthetic validation NLL including epoch 0 (frozen v1)",
                  initialization="frozen v1 plus zero-output residual; train residual only",
                  photo_policy="all architectures fixed before photo evaluation; no photo fitting or selection",
                  visual_model="frozen; cached 64x13 probabilities only; no image network instantiated",
                  temporal_models="not trained: existing examples do not contain measured video sequences",
                  dataset=str(DATASET.relative_to(ROOT)), cache=str(CACHE.relative_to(ROOT)),
                  smoke_limit=args.limit, device="cpu", torch=torch.__version__, numpy=np.__version__,
                  protected_sha256=hashes,
                  source_sha256={name: file_hash(Path(__file__).with_name(name)) for name in sources},
                  cache_sha256={name: file_hash(CACHE / name) for name in
                                ("probabilities.npy", "candidates.npz", "candidate-coverage.json")})
    dump(args.output / "config.json", config)
    for name in sources:
        shutil.copy2(Path(__file__).with_name(name), args.output / name)
    q, data, coverage = read_cache(CACHE, DATASET)
    dump(args.output / "candidate-coverage.json", coverage)
    train = np.flatnonzero(data["split"] == "train")
    val = np.flatnonzero(data["split"] == "validation")
    if args.limit:
        train, val = train[:args.limit], val[:args.limit]
    baseline = v1.load_weights(BASELINE)
    base_report = evaluate_baseline(q, data, val, torch.tensor(baseline), args.batch_size)
    dump(args.output / "baseline-v1-validation.json", base_report)
    if not args.limit:
        reference = json.loads((CACHE / "baseline-v1-validation.json").read_text())
        if (reference["exact_accuracy"] != base_report["exact_accuracy"] or
                abs(reference["conditional_nll"] - base_report["conditional_nll"]) > 1e-6):
            raise ValueError("Reused cache does not reproduce baseline")
    results = {}
    for kind in args.models:
        torch.manual_seed(SEED)
        rng = np.random.default_rng(SEED)
        model = ResidualScorer(kind, baseline)
        folder = args.output / kind
        folder.mkdir()
        optimizer = torch.optim.AdamW(model.parameters(), lr=.001, weight_decay=.0001)
        best = base_report["conditional_nll"]
        best_epoch = 0
        best_state = copy.deepcopy(model.state_dict())
        history = [dict(epoch=0, validation_nll=best, validation_exact=base_report["exact_accuracy"])]
        started = time.perf_counter()
        params = sum(p.numel() for p in model.parameters())
        print(json.dumps(dict(start=kind, parameters=params, train_observations=len(train)*10,
                              validation_observations=len(val)*10)), flush=True)
        for epoch in range(1, args.epochs + 1):
            epoch_start = time.perf_counter()
            model.train()
            loss_sum, count = 0., 0
            for step, batch in enumerate(batches(q, data, train, args.batch_size, rng)):
                optimizer.zero_grad(set_to_none=True)
                scores = forward_batch(model, data, batch)
                target = batch[-1][:, None].expand(-1, len(PROFILES))
                loss = F.cross_entropy(scores.flatten(0, 1), target.flatten())
                if not torch.isfinite(loss):
                    raise ValueError("Non-finite loss")
                loss.backward()
                norm = torch.nn.utils.clip_grad_norm_(model.parameters(), 5., error_if_nonfinite=True)
                optimizer.step()
                loss_sum += float(loss.detach()) * target.numel()
                count += target.numel()
                if (step + 1) % 250 == 0:
                    print(json.dumps(dict(model=kind, epoch=epoch, step=step+1,
                                          train_nll_so_far=loss_sum/count)), flush=True)
            validation = evaluate(model, q, data, val, args.batch_size)
            entry = dict(epoch=epoch, train_nll=loss_sum/count,
                         validation_nll=validation["conditional_nll"],
                         validation_exact=validation["exact_accuracy"],
                         learning_rate=optimizer.param_groups[0]["lr"],
                         seconds=time.perf_counter()-epoch_start)
            history.append(entry)
            dump(folder / "history.json", history)
            print(json.dumps(dict(model=kind, **entry)), flush=True)
            if validation["conditional_nll"] < best:
                best, best_epoch = validation["conditional_nll"], epoch
                best_state = copy.deepcopy(model.state_dict())
            if epoch in (6, 9):
                for group in optimizer.param_groups:
                    group["lr"] *= .3
        train_seconds = time.perf_counter()-started
        checkpoint = dict(schema=1, kind=kind, classes=v1.CLASSES, state_dict=best_state,
                          epoch=best_epoch, validation_nll=best, seed=SEED,
                          baseline_sha256=file_hash(BASELINE),
                          dataset_manifest_sha256=coverage["source_manifest_sha256"])
        torch.save(checkpoint, folder / "model.pt")
        selected, _ = load_model(folder / "model.pt")
        validation = evaluate(selected, q, data, val, args.batch_size)
        training = evaluate(selected, q, data, train, args.batch_size)
        if abs(validation["conditional_nll"]-best) > 1e-7:
            raise ValueError("Serialized model mismatch")
        report = dict(kind=kind, trainable_parameters=params, frozen_parameters=25,
                      best_epoch=best_epoch, training_seconds=train_seconds,
                      training=training, validation=validation,
                      model_sha256=file_hash(folder / "model.pt"))
        dump(folder / "report.json", report)
        results[kind] = report
        dump(args.output / "training-summary.json", results)
        print(json.dumps(dict(completed=kind, best_epoch=best_epoch,
                              validation_exact=validation["exact_accuracy"], seconds=train_seconds)), flush=True)
    changed = {name: dict(before_sha256=digest, after_sha256=file_hash(ROOT / name))
               for name, digest in hashes.items() if file_hash(ROOT / name) != digest}
    if any(not name.startswith("docs/") for name in changed):
        raise ValueError("Protected model changed during run")
    # A different chat may append unrelated documentation during a long run.
    # Record it without claiming preservation or discarding valid checkpoints.
    dump(args.output / "training-complete.json", dict(models=list(results),
         protected_files_unchanged=not changed, protected_models_unchanged=True,
         concurrent_document_changes=changed))


if __name__ == "__main__":
    main()
