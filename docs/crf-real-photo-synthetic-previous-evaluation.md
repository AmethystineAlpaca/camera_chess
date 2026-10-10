# Real photos with synthetic previous states

Date: 2026-10-11 · Experiment: [E12 in the Experiment Log](crf-experiment-log.md#e12--felix-dataset-photo-comparison-with-sampled-synthetic-previous-states--2026-10-11)

## Protocol

Evaluated cached recognizer probabilities on 176 retained photographs from Felix Fabricius's [Robot chess commentator: square dataset](https://huggingface.co/datasets/felixfabricius/robot-chess-commentator-squares), pinned at revision `d71ba52761e497b18bbe5743efcbafb0d1f21091`. These 176 photos come from 23 Felix-dataset setups: 54 train / 8 setups, 67 validation / 5 setups, and 55 test / 10 setups. The labels `train`, `validation`, and `test` are Felix's source-dataset splits; they are not splits used to train the CRFs. Neither CRF was fitted on real photos: both checkpoints were trained on synthetic probability observations and selected by synthetic validation NLL. Earlier comparisons inspected the 67-photo Felix validation split, so the 176-photo result is descriptive. The 55 retained test photos were not used for those earlier comparisons, but have now been evaluated and must not be described as untouched for future tuning. The recognizer's proprietary training-data identities and any overlap with these photos have not been independently audited.

### Recognizer identity and official results

The raw baseline uses the application's exact frozen **ChessQ Lite V4** checkpoint (`cq-vit-s-644-v4-seed2-safetensors-fp16-r2`, ViT-S FP16; SHA-256 `fd269bcfdefe6208b75490d7ac72524c0970f8212470a88012ce755b5f092d10`). It comes from Joël Seytre's [ChessQueries project](https://github.com/JSeytre/chessqueries); Felix Fabricius supplied the photo dataset, not the model. The [model card](https://huggingface.co/joelseytre/chessq-lite) describes Lite V4 as a smaller recognizer further trained on proprietary data.

The [ChessQueries paper](https://arxiv.org/abs/2608.30762) reports exact-board accuracy of 99.5% / 98.5% / 87.1% / 87.6% on ChessReD / ChessCog / SLCC / CVChess for its main ViT-L model; its ViT-S ablation reports 98.6% / 97.7% / 75.9% / 49.4%. Neither row is this exact Lite V4 checkpoint. The official [Lite V4 validation file](https://huggingface.co/joelseytre/chessq-lite/blob/main/cq-vit-s-644-v4-seed2-safetensors-fp16-r2-validation.json) reports 98.6% / 97.4% / 78.0% / 100% on those benchmarks, but its stated purpose is checking FP32-to-FP16 export consistency, not an independent accuracy evaluation. The official materials reviewed do not report results on Felix's dataset, and do not establish whether its photos overlap with proprietary training data.

The [evaluator](../scripts/evaluate_crf_synthetic_previous.py) constructs a finite pool of predecessor FENs by reversing supported moves from the **current piece placement labeled in the Felix dataset**. Each predecessor passes `python-chess` position-validity checks and has a verified legal forward move yielding exactly that placement. With seed `20261011`, hash-priority sampling without replacement selects up to ten distinct FENs per photo; the first five match the five-state run, and the first one supplies the single-state result.

This is sampling from the implemented generator's pool, not an exhaustive enumeration of every historical FEN or a sample of observed game histories. Ordinary generated predecessors have no castling rights or en passant square; the special-move constructions supply the necessary fields. Either moving color may be constructed. The matching target is the 64-square piece placement, not the full rule state or the dataset's recorded preceding move.

Every method uses the same cached probabilities for a photo. Raw per-square argmax receives no predecessor. Fixed scoring and both CRFs receive identical predecessors and share candidates: legal moves, unchanged boards, and supported illegal relocations. The fixed scorer maximizes the sum of visual log probabilities without learned coefficients, so it isolates the incremental benefit of learned scoring after supplying the reference and candidate constraints. It is not the original application's CRF-disabled pipeline. All methods return their top prediction without live acceptance gates or temporal votes. Target candidate coverage was 100% by construction and checked in every condition.

## Metric definitions

“All correct” refers to **all predicted current boards being correct**, not to the predecessors being correct. Each prediction must match all 64 labeled squares; a photo passes only if every selected predecessor yields that exact board. This is not majority voting, an average of five/ten predictions, or a sequence with predicted-state feedback.

- **One random predecessor:** the first sampled state; 176 eligible photos.
- **All correct with 5 different predecessors:** first five predictions all exact; 174 eligible photos and 876 total conditions in the five-state run. Two generator pools have only 2 and 4 predecessors.
- **All correct with 10 different predecessors:** all ten predictions exact; 172 eligible photos and 1,738 total conditions in the ten-state run. Two additional pools have only 6 predecessors each.

Photos lacking the requested number are excluded only from that all-correct denominator; their available conditions remain in the per-condition records. Multiple predecessors reuse the same photograph and probabilities, so they are repeated conditions, not additional independent images. The eligible photo sets differ between five and ten; compare the displayed fractions with their denominators. On the shared eligible photos, passing all ten necessarily includes passing the first five.

## Results across all 176 photos

| Method | One random predecessor | All correct with 5 different predecessors | All correct with 10 different predecessors | Mean per-square accuracy (10-state run) |
| --- | ---: | ---: | ---: | ---: |
| Raw per-square argmax | 1/176 — 0.57% | 1/174 — 0.57% | 1/172 — 0.58% | 80.45% |
| Fixed log-probability scorer | 114/176 — 64.77% | 85/174 — 48.85% | 71/172 — 41.28% | 98.13% |
| CRF v1 — 25 weights | 125/176 — 71.02% | 94/174 — 54.02% | 80/172 — 46.51% | 98.55% |
| CRF v2 — 250 weights | 122/176 — 69.32% | 95/174 — 54.60% | 80/172 — 46.51% | 98.46% |

The per-square column uses the **ten-state run**: average square accuracy over the available predecessors within each photo, then give each of the 176 photos equal weight. It is not a pooled mean over the 1,738 conditions or the one-state board-accuracy metric. Raw argmax repeats the same prediction for every predecessor; its changing all-correct percentages reflect only the eligible photo denominator.

## Results by Felix dataset split

Cells are correct photos / eligible photos. All 55 test photos have at least ten predecessors in the generator's pool.

| Felix dataset split | Method | One random predecessor | All correct with 5 different predecessors | All correct with 10 different predecessors |
| --- | --- | ---: | ---: | ---: |
| train | Raw per-square argmax | 1/54 | 1/53 | 1/53 |
| train | Fixed log-probability scorer | 44/54 | 36/53 | 31/53 |
| train | CRF v1 — 25 weights | 45/54 | 39/53 | 34/53 |
| train | CRF v2 — 250 weights | 45/54 | 40/53 | 35/53 |
| validation | Raw per-square argmax | 0/67 | 0/66 | 0/64 |
| validation | Fixed log-probability scorer | 44/67 | 28/66 | 22/64 |
| validation | CRF v1 — 25 weights | 48/67 | 33/66 | 25/64 |
| validation | CRF v2 — 250 weights | 46/67 | 34/66 | 25/64 |
| test | Raw per-square argmax | 0/55 | 0/55 | 0/55 |
| test | Fixed log-probability scorer | 26/55 | 21/55 | 18/55 |
| test | CRF v1 — 25 weights | 32/55 | 22/55 | 21/55 |
| test | CRF v2 — 250 weights | 31/55 | 21/55 | 20/55 |

For the test split, v1/v2 one-predecessor accuracy is 58.18% / 56.36%; all correct with five is 40.00% / 38.18%; all correct with ten is 38.18% / 36.36%. This is a one-photo difference under each metric, with only ten test setups. It does not establish a reliable winner.

## Raw recognizer audit on the full Felix test split

The original Felix test partition contains 88 source images; 55 remain in the 176-photo CRF dataset after requiring a usable recorded transition and excluding invalid/inconsistent labels, duplicates, cross-split placements, and overlap with synthetic training positions. To check whether this filtering explains the raw zero-exact result, [the frozen ChessQ Lite V4 checkpoint](../scripts/audit_chessq_lite_photo_test.py) was run directly on all 88 source test images with no CRF and no previous state. It produced **0/88 exact boards**, with 76.14% square accuracy (48.73 of 64 squares correct per image on average). On the retained 55-image subset it produced 0/55 exact boards and 76.28% square accuracy (48.82 squares correct on average). Direct raw predictions matched the cached predictions on all 55 retained images exactly. Thus filtering the Felix test partition down to 55 does not explain the zero exact-board count. This is still an evaluation on Felix's labeled photos, not an official ChessQ Lite benchmark or an audited holdout from the model's undisclosed proprietary training data.

The per-image audit report is stored at `data/crf/runs/real-photos-synthetic-previous-v1/chessq-lite-raw-test-audit.json` in the Git-ignored data directory.

## Uncertainty

The intervals use 20,000 bootstrap resamples of Felix dataset setups, retaining all photos within each selected setup. Photos and predecessor conditions are not treated as independent replicates. The intervals condition on the generated states at seed `20261011`; they do not include variation across new predecessor samples or camera domains.

| Method | One predecessor, 95% interval | All correct with 5 different predecessors, 95% interval | All correct with 10 different predecessors, 95% interval |
| --- | ---: | ---: | ---: |
| Raw per-square argmax | 0.00 to 1.88% | 0.00 to 1.91% | 0.00 to 1.91% |
| Fixed log-probability scorer | 48.59 to 79.05% | 31.86 to 64.71% | 25.93 to 56.86% |
| CRF v1 — 25 weights | 58.29 to 82.42% | 37.29 to 69.31% | 30.82 to 61.97% |
| CRF v2 — 250 weights | 53.79 to 82.97% | 37.50 to 70.26% | 30.57 to 62.00% |

Paired v1-minus-v2 differences are more informative than comparing separate intervals:

| Photo-level metric | v1 minus v2 | 95% setup-bootstrap interval |
| --- | ---: | ---: |
| One random predecessor | +1.70 pp | -2.72 to 6.73 pp |
| All correct with 5 different predecessors | -0.57 pp | -3.11 to 1.96 pp |
| All correct with 10 different predecessors | +0.00 pp | -2.37 to 2.38 pp |

For one predecessor, v1 alone is correct on 7 photos and v2 alone on 4. With five predecessors, v1 alone passes on 2 and v2 alone on 3. With ten, both pass on 80/172 photos. All paired intervals include zero. These observations do not establish that either 25 or 250 parameters is the optimal size; the models also differ in features and regularization coordinates.

## Limits and reproduction

The current images are real, but the predecessor is constructed conditional on the known correct current placement. This privileged input guarantees a legal one-move path to the target. It does not measure recognition of a real previous photo, recovery from an incorrect reference, predicted-state feedback, unchanged/illegal target events, live commit decisions, or latency. The images are rectified still photographs rather than the application's raw continuous camera stream. This is a conditional scoring and predecessor-sensitivity evaluation, not a deployment-accuracy measurement.

The JSON records contain each sampled FEN and forward legal move, candidate count/index, each method's exact-match flag and square accuracy, split summaries, intervals, and model/input hashes. Full predicted board arrays are not stored. The current script and both results were fully reproduced during the log audit; artifact checksums are recorded in E12. The five-state conditions exactly match the first five available conditions in the ten-state run.

Reports remain in the Git-ignored directory `data/crf/runs/real-photos-synthetic-previous-v1/` as `all-seed20261011-n5.json`, `all-seed20261011-n10.json`, and `chessq-lite-raw-test-audit.json`. Reproduce the raw test-split audit with:

```bash
PYTORCH_ENABLE_MPS_FALLBACK=1 research/edgetam-env/bin/python scripts/audit_chessq_lite_photo_test.py
```

To reproduce the predecessor evaluations without overwriting the saved records, supply separate output paths:

```bash
research/edgetam-env/bin/python scripts/evaluate_crf_synthetic_previous.py --split all --samples-per-photo 5 --seed 20261011 --output /tmp/crf-photo-n5-reproduced.json
research/edgetam-env/bin/python scripts/evaluate_crf_synthetic_previous.py --split all --samples-per-photo 10 --seed 20261011 --output /tmp/crf-photo-n10-reproduced.json
```
