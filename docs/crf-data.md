# CRF Dataset Build Record

Built on 2026-10-11. Proposal, equations, and experiment results: [CRF experiment log](crf-experiment-log.md).

## Deliverables

| Dataset | Location | Contents |
| --- | --- | --- |
| Synthetic training set | `data/crf/synthetic-v1/` | 20,000 unique transitions; 200,000 probability observations |
| Public real photographs | `data/crf/real/robot-v1/` | 371 source photos; 176 deduplicated, split-safe legal move pairs with cached frozen-model probabilities |
| Local human corrections | `data/crf/real/local-corrections/` | 26 paired frames; 27 explicitly labeled squares; all other squares masked out |
| Smoke build | `data/crf/smoke-v1/` | 1,000 transitions / 10,000 observations, validated before the full build; do not merge into training |

The first CRF training run is complete. Its real-photo validation gate failed and initially stopped integration; the user subsequently authorized a local application trial, recorded in E07. The integration does not change these datasets or their split. Results and the saved model are linked in the [experiment log](crf-experiment-log.md). Downloaded/generated data are local and Git-ignored; scripts and documentation are kept in the repository source tree. Full synthetic shards occupy approximately 600 MiB.

## Sources and provenance

1. **[Lichess official standard-game archives](https://database.lichess.org/), CC0.** Downloaded the complete January and February 2013 `.pgn.zst` archives and verified both against the official SHA-256 list. These small, complete archives avoid a multi-gigabyte download; chess rules do not require recent games, but this is not a representative modern move-frequency sample. Replayed the first 6,000 games from each archive. URLs and exact checksums are in `raw/sources.json` and the synthetic manifest.
2. **[Felix Fabricius: Robot chess commentator dataset](https://huggingface.co/datasets/felixfabricius/robot-chess-commentator-squares), CC-BY-4.0, copyright 2026 Felix Fabricius.** Pinned revision `d71ba52761e497b18bbe5743efcbafb0d1f21091`; downloaded only the three board-level Parquet files and verified their published LFS SHA-256 hashes. The publisher supplies labels, previous FENs, moves, setup splits, and flags for FEN/photo disagreements. Original images and attribution are retained. These are perspective-warped still photographs, not continuous video or this application's raw camera feed.
3. **Local correction archives.** Verify the image hash, capture time, and sequence against the exact model output. Only manually edited squares are ground truth; unchanged cells copied from model output are never promoted to human labels. Four explicit labels agree with the raw argmax and remain valid confirmations. One directory lacks a complete paired record and is excluded. This data stays private and is selection-biased toward recognition problems.

Other sources inspected: [ChessReD's official repository](https://github.com/tmasouris/end-to-end-chess-recognition) and [Kyouku's move-recognition project](https://github.com/Kyouku/chess-move-recognition). ChessReD was not mixed into this build pending model-training-overlap/split verification. Kyouku's linked video/ground-truth Drive folder returned HTTP 404; detection caches alone are not ground truth and were not imported. Forum/secondary links were used for discovery, not as anonymous training-data dumps.

## Synthetic construction

The builder reads 12,000 source games, rejects short/invalid games, deduplicates move sequences, and replays every retained game's moves with `python-chess`. It deterministically samples up to 12 ordinary plies per game and keeps special moves. Legal promotion alternatives are generated from reachable source positions and explicitly marked as generated.

Processing totals:

- 11,943 valid unique games; 799,101 legal plies replayed.
- 53 short/invalid games and 4 duplicate games excluded.
- 14,779 duplicate transition records removed; 149,995 unique candidate transitions remained.
- 4,658 transition records sharing physical placements across splits excluded, leaving 145,337 eligible candidates.
- Select 20,000 seeds: 18,000 legal moves, 1,500 generated unchanged states, 500 generated illegal relocations.
- Illegal examples move an existing non-king piece to an empty square without changing its identity. They do not advance the legal game state. This does not cover every possible mistake or interrupted capture.

Coverage of the final seeds:

| Category | Count |
| --- | ---: |
| Opening / middlegame / endgame | 4,339 / 9,149 / 6,512 |
| Captures | 3,655 |
| Castling | 1,263 |
| En passant | 93 |
| Promotions: queen / rook / bishop / knight | 676 / 677 / 673 / 631 |
| Recorded PGN moves | 16,022 |
| Generated legal promotion alternatives | 1,978 |

Phases are operational strata: endgame means at most 12 pieces; otherwise opening means ply <= 24; the rest is middlegame. Special-move counts overlap other categories. Special cases are oversampled for coverage, not weighted to deployment frequency.

Each seed produces ten observations, with seed `20261011` and a per-transition/profile SHA-256-derived RNG seed:

1. Clean soft probabilities with correct argmax.
2. Same-color type confusion affecting 1 occupied square.
3. Same-color type confusion affecting up to 5 occupied squares.
4. Same-color type confusion affecting up to 10 occupied squares.
5. Occupancy errors affecting up to 3 squares.
6. Color confusion affecting up to 2 occupied squares.
7. Adjacent-square assignment swap.
8. Five confidently wrong squares.
9. A 3 × 3 region of near-uniform uncertainty.
10. Ten mixed errors with varied severity.

All rows remain finite, positive, and normalized. Simulated probabilities are not measurements of ChessQ Lite's error distribution. The profile mixture is a training/stress distribution; acceptance thresholds must not be calibrated from its frequencies. These data provide board and operation supervision, not verified accept/reject labels for every ambiguous observation. Lifted-piece/capture-intermediate states and continuous-video timing remain outside v1.

## Deduplication and partitioning

- Game identity: SHA-256 of canonical initial state and UCI move sequence; ignore player names and PGN headers.
- Transition identity: canonical previous state, physical target placement, and operation. Ignore move counters; preserve side to move, castling, and effective en passant rights.
- Assign source games by deterministic hash to 80/10/10 groups before augmentation. Then exclude both endpoints of any transition whose physical placement crosses groups, even if turn/rights differ.
- All ten variants stay with their seed. Exact model inputs are hash-checked for duplicates. Final sizes are **160,000 train / 20,000 validation / 20,000 test** observations.
- Public real moves retain publisher setup splits. Of 216 initially valid photographed transitions, remove 28 cross-split overlaps, 10 duplicate transitions, and 2 overlaps with any synthetic endpoint: **176** remain. Source photos excluded from move evaluation are retained only as provenance; do not silently add them to the calibration pool.
- Local partial corrections are calibration-only and are not counted as independent whole-board test events.

## File format

`manifest.json` records sources, generator checksum, versions, RNG seed, coverage, shard checksums and counts. `generator-source.py` preserves the exact builder used. `transitions.jsonl` supplies FENs, operation, UCI move, source month/game ordinal, provenance, game group, split, and coverage flags.

Each `.npz` shard is loaded with `allow_pickle=False` and contains:

| Array | Shape / type | Meaning |
| --- | --- | --- |
| `probabilities` | N × 64 × 13, float32 | Synthetic observation |
| `previous` | N × 64, uint8 | Known-correct previous reference labels in synthetic v1 |
| `target` | N × 64, uint8 | True current physical labels |
| `transition_index` | N, uint32 | Row in `transitions.jsonl`, carrying the additional chess state |
| `noise_profile` | N, uint8 | Index into manifest profile list |
| `operation` | N, uint8 | Legal move / unchanged / illegal relocation |
| `perturbed_squares` | N, uint8 | Number of deliberately perturbed square distributions |

Class order is **`. P N B R Q K p n b r q k`**; square order is **a8…h8, a7…h7, …, a1…h1**, matching the existing recognizer. A vector of length 64 represents the requested 64 × 1 labels. The previous labels alone do not include castling/turn/en-passant metadata; join through `transition_index`.

Public real `moves.npz` contains measured `probabilities`, `previous`, `target`, and `split`; rows align with `moves.jsonl`. Images are rotated to a8 at the top left using publisher orientation metadata, with no ground-truth-based search for the best rotation. The frozen checkpoint SHA-256 and inference settings are in `moves-inference.json`. Model outputs may differ slightly across hardware/library versions.

Local `partial-labels.npz` contains measured probabilities, `target` (-1 for unknown cells), and `label_mask`; metadata align with `corrections.jsonl`. Never train against an unmasked -1 or assume the other cells are correct.

The live application uses one **Fix recognition** editor. Users edit incorrect squares; they do not tag legal move, unchanged, incomplete or physical mistake. The synthetic `operation` field is generated from the known transition and remains internal supervision. Accepting a live board automatically, or receiving no correction, does not create a human-labeled example. New correction archives preserve the displayed board, raw probabilities, source frame, explicit edits and game-state reconciliation; only the edited squares qualify as labels. These new archives have not been added to the frozen v1 datasets or their reported counts.

## Reproduce and verify

Run from the repository root. The existing environment supplies Python 3.12, python-chess 1.999 (module 1.11.2), and NumPy 2.5.3; `zstd` is required. Public Parquet preparation additionally uses PyArrow 26.0.0 installed into a dataset-only directory.

```bash
research/edgetam-env/bin/python -m unittest discover -s scripts -p 'test_crf_dataset.py' -v
research/edgetam-env/bin/python scripts/build_crf_dataset.py fetch
research/edgetam-env/bin/python scripts/build_crf_dataset.py build --transitions 1000 --games-per-source 300 --output data/crf/smoke-v1
research/edgetam-env/bin/python scripts/build_crf_dataset.py build --transitions 20000 --games-per-source 6000 --output data/crf/synthetic-v1
research/edgetam-env/bin/python scripts/build_crf_dataset.py validate --output data/crf/synthetic-v1
research/edgetam-env/bin/python -m pip install --target data/crf/python-deps --no-deps pyarrow==26.0.0
research/edgetam-env/bin/python scripts/prepare_crf_real.py public
research/edgetam-env/bin/python scripts/prepare_crf_real.py local
research/edgetam-env/bin/python scripts/prepare_crf_real.py infer --limit 5
research/edgetam-env/bin/python scripts/prepare_crf_real.py infer
research/edgetam-env/bin/python scripts/validate_crf_real.py
```

Build commands refuse to overwrite an existing synthetic output directory; use a new `--output` for another run. For a differently located synthetic dataset, pass `--synthetic` to public preparation. The real validator uses the documented default paths. Inference uses the existing frozen model on MPS with CPU fallback for an unsupported interpolation operation; generating synthetic observations requires no GPU.

## Validation and remaining gap

The 10,000-sample smoke build passed before full generation. Seven focused tests cover label order, state canonicalization, castling, en passant, all promotions, illegal physical relocation, noise validity/reproducibility, split leakage, and incorrect-target rejection. Full validation checked **200,000 samples / 12.8 million probability rows**, replayed every legal target, verified physical mistakes and metadata, and found zero duplicate inputs or cross-split game/placement overlaps. Exact results are in `synthetic-v1/validation.json` and `real/validation.json`.

The planned several hundred to 1,000 independent **in-domain video move/no-op/mistake events is not yet available**. The 176 public photo pairs and 27 local square labels are useful auxiliary data, not a substitute for that set. The public images have a different camera/warping distribution, and base-model pretraining overlap has not been independently audited. Do not use this build to claim deployment accuracy or latency.
