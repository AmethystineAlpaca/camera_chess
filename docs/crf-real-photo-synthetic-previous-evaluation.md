# Real photos with synthetic previous states

Date: 2026-10-11 · Experiment: [E12 in the Experiment Log](crf-experiment-log.md#e12--felix-dataset-photo-comparison-with-sampled-synthetic-previous-states--2026-10-11)

Latest update: [six neural postprocessors, trained on the existing synthetic split and evaluated on the identical 1/5/10-predecessor photo conditions](#neural-postprocessor-comparison--2026-10-11). The original baseline results below are retained.

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

The JSON records contain each sampled FEN and forward legal move, candidate count/index, each method's exact-match flag and square accuracy, split summaries, intervals, and model/input hashes. Full predicted board arrays are not stored. The current script and both results were fully reproduced during the log audit; artifact checksums are recorded in the [protocol/audit record](crf-photo-evaluation-audit.md). The five-state conditions exactly match the first five available conditions in the ten-state run.

Reports remain in the Git-ignored directory `data/crf/runs/real-photos-synthetic-previous-v1/` as `all-seed20261011-n5.json`, `all-seed20261011-n10.json`, and `chessq-lite-raw-test-audit.json`. Reproduce the raw test-split audit with:

```bash
PYTORCH_ENABLE_MPS_FALLBACK=1 research/edgetam-env/bin/python scripts/audit_chessq_lite_photo_test.py
```

To reproduce the predecessor evaluations without overwriting the saved records, supply separate output paths:

```bash
research/edgetam-env/bin/python scripts/evaluate_crf_synthetic_previous.py --split all --samples-per-photo 5 --seed 20261011 --output /tmp/crf-photo-n5-reproduced.json
research/edgetam-env/bin/python scripts/evaluate_crf_synthetic_previous.py --split all --samples-per-photo 10 --seed 20261011 --output /tmp/crf-photo-n10-reproduced.json
```

## Neural postprocessor comparison — 2026-10-11

This comparison trains only the postprocessor. ChessQ Lite V4 remains frozen; inputs are its existing 64×13 cached probabilities and the supplied previous board. All six new scorers add a learned residual to the frozen 25-weight v1 score and retain the identical candidate generator. No visual features, image-model training, temporal data, or live acceptance gates are used.

### Findings from this comparison

- The two-hidden-layer changed-square MLP has the highest one-predecessor score, **128/176 (72.73%)**, and all-five score, **97/174 (55.75%)**. Each is three more photos than v1; its all-ten result ties v1 at 80/172.
- Attention has the highest all-ten score, **82/172 (47.67%)**, two more photos than v1. Its one/five results improve by one photo each. Shared square MLP improves by one photo under each metric.
- **Every new-model-versus-v1 paired interval includes zero.** No architecture establishes a reliable overall winner in this small, already-inspected photo set. On the 55-photo source test subset, no new model beats v1 on all three metrics: square MLP leads the single-state count (34 versus 32), while v1 retains the best all-ten count (21 versus 20 for most new models, with Feature MLP tying 21).
- Synthetic validation improves for every new model, but this does not consistently transfer to photos. Attention has the best synthetic validation NLL and accuracy; Feature MLP has the second-best synthetic NLL yet regresses on all three photo metrics and produces 396 high-confidence wrong conditions. More nonlinear capacity alone does not resolve the synthetic-to-photo gap.
- These are first-run, fixed-budget architecture comparisons. There was no retraining, hyperparameter adjustment, or checkpoint reselection after observing these photo scores. The strongest point estimates can motivate future independently evaluated work; they do not justify automatic model replacement.

### Training and selection

All models reuse the existing 160,000 synthetic training observations (16,000 transitions × 10 noise profiles) and 20,000 synthetic validation observations (2,000 transitions × 10). No real photos are used for fitting or checkpoint selection. One run per architecture, seed `20261011`; CPU float32, AdamW, learning rate 0.001, weight decay 0.0001, gradient norm clipped at 5, batch size 16 transitions including all ten profiles, 12 epochs, learning rate multiplied by 0.3 after epochs 6 and 9. Checkpoints minimize synthetic validation NLL; epoch 0 (exactly v1) is also eligible. Zero-initialized output layers make every architecture start at v1. Architectures and training settings were fixed before this photo evaluation; there was no tuning on the photo results. This is a first architecture screen under one common optimizer schedule, not a claim that each architecture is fully optimized.

| Model | Residual architecture | Trainable parameters | Selected epoch | Train exact | Validation exact | Validation NLL | Training + epoch validation time |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Spatial Attention | 41→32 projection; 1 pre-norm encoder, 4 heads, FFN 64, dropout 0; 13-class head + operation head | 10,324 | 11 | 99.278% | 99.220% | 0.021181 | 244.6 s |
| Spatial CNN | 8×8 probability/prior/coordinate map: 41→16 (3×3)→16 (3×3)→13 (1×1); operation head | 8,468 | 9 | 99.147% | 98.765% | 0.039835 | 269.7 s |
| Feature MLP (1 hidden layer) | 25 existing feature differences → 32 SiLU → 1 | 865 | 11 | 99.028% | 98.990% | 0.026033 | 957.7 s |
| Changed-square MLP (1 hidden layer) | Shared 39→8 encoder; four changed-square slots + piece embeddings + coordinates + log evidence + flags: 87 → 32 SiLU → 1 | 3,221 | 12 | 98.752% | 98.625% | 0.037170 | 965.9 s |
| Changed-square MLP (2 hidden layers) | Same 87 inputs → 32 SiLU → 32 SiLU → 1 | 4,277 | 11 | 98.995% | 98.900% | 0.029872 | 948.9 s |
| Shared square MLP | Per-square probabilities, scaled log probabilities, prior one-hot: 39 → 32 SiLU → 13; operation head | 1,716 | 12 | 98.979% | 98.915% | 0.033174 | 104.6 s |

All models additionally retain 25 frozen v1 coefficients. Coordinates follow the existing rank-8-to-rank-1 square ordering; changed-square slots use the candidate generator's sorted square order with neutral padding. The visual feature differences in Feature MLP use log terms scaled by 1/8. The shared square encoder receives all 13 probabilities, their scaled logs, and the previous square label. Unchanged-square contributions cancel in the square/CNN/Attention scorers; changed-square MLPs jointly score the four slots and operation flags.

### Synthetic validation by operation

The photo evaluation contains legal target transitions only. These synthetic subgroups retain the unchanged and supported illegal-relocation checks; counts are observations, including ten profiles per transition.

| Model | Legal move (18,000) | Unchanged (1,500) | Illegal relocation (500) |
| --- | ---: | ---: | ---: |
| CRF v1 | 98.444% | 96.600% | 97.400% |
| Feature MLP (1 hidden layer) | 99.100% | 98.333% | 97.000% |
| Shared square MLP | 99.039% | 98.067% | 97.000% |
| Changed-square MLP (1 hidden layer) | 98.772% | 97.733% | 96.000% |
| Changed-square MLP (2 hidden layers) | 99.028% | 98.133% | 96.600% |
| Spatial CNN | 98.950% | 97.400% | 96.200% |
| Spatial Attention | 99.261% | 99.133% | 98.000% |

### Same 176-photo evaluation

The saved ten-state report supplies the exact predecessor FENs and ordering; the saved five-state report was checked against its prefixes. Raw, fixed, v1 and v2 predictions were recomputed and matched every saved condition. Denominators and all-correct definitions are unchanged: one predecessor uses 176 photos, all five uses 174, and all ten uses 172. Every model receives the same 1,738 conditions. No voting or best-of-N selection is performed.

| Model | One predecessor | All 5 correct | All 10 correct | Photo-weighted square accuracy |
| --- | ---: | ---: | ---: | ---: |
| Raw argmax | 1/176 — 0.57% | 1/174 — 0.57% | 1/172 — 0.58% | 80.45% |
| Fixed log-probability | 114/176 — 64.77% | 85/174 — 48.85% | 71/172 — 41.28% | 98.13% |
| CRF v1 | 125/176 — 71.02% | 94/174 — 54.02% | 80/172 — 46.51% | 98.55% |
| CRF v2 | 122/176 — 69.32% | 95/174 — 54.60% | 80/172 — 46.51% | 98.46% |
| Feature MLP (1 hidden layer) | 123/176 — 69.89% | 92/174 — 52.87% | 79/172 — 45.93% | 98.43% |
| Shared square MLP | 126/176 — 71.59% | 95/174 — 54.60% | 81/172 — 47.09% | 98.49% |
| Changed-square MLP (1 hidden layer) | 127/176 — 72.16% | 94/174 — 54.02% | 80/172 — 46.51% | 98.57% |
| Changed-square MLP (2 hidden layers) | 128/176 — 72.73% | 97/174 — 55.75% | 80/172 — 46.51% | 98.59% |
| Spatial CNN | 122/176 — 69.32% | 94/174 — 54.02% | 80/172 — 46.51% | 98.48% |
| Spatial Attention | 126/176 — 71.59% | 95/174 — 54.60% | 82/172 — 47.67% | 98.61% |

### Results by source-photo split

These are Felix's source splits; none was used to train the new postprocessors. The test-photo results were already inspected in earlier evaluations and are not a fresh untouched test.

| Source split | Model | One predecessor | All 5 correct | All 10 correct |
| --- | --- | ---: | ---: | ---: |
| train | Raw argmax | 1/54 — 1.85% | 1/53 — 1.89% | 1/53 — 1.89% |
| train | Fixed log-probability | 44/54 — 81.48% | 36/53 — 67.92% | 31/53 — 58.49% |
| train | CRF v1 | 45/54 — 83.33% | 39/53 — 73.58% | 34/53 — 64.15% |
| train | CRF v2 | 45/54 — 83.33% | 40/53 — 75.47% | 35/53 — 66.04% |
| train | Feature MLP (1 hidden layer) | 45/54 — 83.33% | 38/53 — 71.70% | 34/53 — 64.15% |
| train | Shared square MLP | 46/54 — 85.19% | 39/53 — 73.58% | 35/53 — 66.04% |
| train | Changed-square MLP (1 hidden layer) | 46/54 — 85.19% | 39/53 — 73.58% | 34/53 — 64.15% |
| train | Changed-square MLP (2 hidden layers) | 47/54 — 87.04% | 42/53 — 79.25% | 36/53 — 67.92% |
| train | Spatial CNN | 46/54 — 85.19% | 39/53 — 73.58% | 34/53 — 64.15% |
| train | Spatial Attention | 46/54 — 85.19% | 40/53 — 75.47% | 36/53 — 67.92% |
| validation | Raw argmax | 0/67 — 0.00% | 0/66 — 0.00% | 0/64 — 0.00% |
| validation | Fixed log-probability | 44/67 — 65.67% | 28/66 — 42.42% | 22/64 — 34.38% |
| validation | CRF v1 | 48/67 — 71.64% | 33/66 — 50.00% | 25/64 — 39.06% |
| validation | CRF v2 | 46/67 — 68.66% | 34/66 — 51.52% | 25/64 — 39.06% |
| validation | Feature MLP (1 hidden layer) | 46/67 — 68.66% | 32/66 — 48.48% | 24/64 — 37.50% |
| validation | Shared square MLP | 46/67 — 68.66% | 34/66 — 51.52% | 26/64 — 40.62% |
| validation | Changed-square MLP (1 hidden layer) | 48/67 — 71.64% | 33/66 — 50.00% | 26/64 — 40.62% |
| validation | Changed-square MLP (2 hidden layers) | 52/67 — 77.61% | 33/66 — 50.00% | 24/64 — 37.50% |
| validation | Spatial CNN | 47/67 — 70.15% | 33/66 — 50.00% | 26/64 — 40.62% |
| validation | Spatial Attention | 48/67 — 71.64% | 33/66 — 50.00% | 26/64 — 40.62% |
| test | Raw argmax | 0/55 — 0.00% | 0/55 — 0.00% | 0/55 — 0.00% |
| test | Fixed log-probability | 26/55 — 47.27% | 21/55 — 38.18% | 18/55 — 32.73% |
| test | CRF v1 | 32/55 — 58.18% | 22/55 — 40.00% | 21/55 — 38.18% |
| test | CRF v2 | 31/55 — 56.36% | 21/55 — 38.18% | 20/55 — 36.36% |
| test | Feature MLP (1 hidden layer) | 32/55 — 58.18% | 22/55 — 40.00% | 21/55 — 38.18% |
| test | Shared square MLP | 34/55 — 61.82% | 22/55 — 40.00% | 20/55 — 36.36% |
| test | Changed-square MLP (1 hidden layer) | 33/55 — 60.00% | 22/55 — 40.00% | 20/55 — 36.36% |
| test | Changed-square MLP (2 hidden layers) | 29/55 — 52.73% | 22/55 — 40.00% | 20/55 — 36.36% |
| test | Spatial CNN | 29/55 — 52.73% | 22/55 — 40.00% | 20/55 — 36.36% |
| test | Spatial Attention | 32/55 — 58.18% | 22/55 — 40.00% | 20/55 — 36.36% |

### Paired differences from v1

Percentage-point differences and 95% percentile intervals from 20,000 setup-cluster bootstrap resamples. They condition on the existing predecessor seed and one training seed; intervals are descriptive and unadjusted for multiple model comparisons.

| Model | One predecessor Δ [95% CI] | All 5 Δ [95% CI] | All 10 Δ [95% CI] |
| --- | ---: | ---: | ---: |
| Feature MLP (1 hidden layer) | -1.14 [-3.30, +1.09] | -1.15 [-2.70, +0.00] | -0.58 [-1.70, +0.00] |
| Shared square MLP | +0.57 [-3.65, +4.38] | +0.57 [-1.30, +2.44] | +0.58 [-2.13, +2.87] |
| Changed-square MLP (1 hidden layer) | +1.14 [-2.10, +4.14] | +0.00 [-1.67, +1.69] | +0.00 [-1.84, +1.52] |
| Changed-square MLP (2 hidden layers) | +1.70 [-2.19, +5.39] | +1.72 [-0.61, +4.49] | +0.00 [-2.37, +2.42] |
| Spatial CNN | -1.70 [-4.29, +0.60] | +0.00 [-1.69, +1.69] | +0.00 [-1.84, +1.52] |
| Spatial Attention | +0.57 [-1.97, +3.16] | +0.57 [-1.95, +3.25] | +1.16 [-1.85, +4.69] |

### Error confidence and scoring cost

Wrong high-confidence conditions count wrong top-ranked boards with candidate-relative posterior ≥0.90. Conditions reuse photos and are not independent events. Timing is CPU scoring of cached probabilities with already-generated candidates, including input tensor conversion; it excludes visual inference, candidate generation, live gates, and camera latency. Each model was warmed up once before measurement.

| Model | Wrong conditions | Wrong with posterior ≥0.90 | Conditional NLL | Scoring p50 | Scoring p95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Feature MLP (1 hidden layer) | 506 | 396 | 6.9925 | 0.413 ms | 0.479 ms |
| Shared square MLP | 480 | 296 | 4.0949 | 0.175 ms | 0.194 ms |
| Changed-square MLP (1 hidden layer) | 461 | 264 | 3.8476 | 0.334 ms | 0.375 ms |
| Changed-square MLP (2 hidden layers) | 452 | 274 | 4.3323 | 0.367 ms | 0.413 ms |
| Spatial CNN | 486 | 277 | 3.5323 | 0.200 ms | 0.224 ms |
| Spatial Attention | 448 | 255 | 3.3412 | 0.251 ms | 0.281 ms |

### Single-board latency comparison

Apple M4, CPU with four threads; one cached photo, 933 candidates, 30 repetitions after five scoring warmups per model. These are medians from the quick timing check requested after the accuracy evaluation.

| Postprocessor | Scoring only | Candidates + scoring + argmax |
| --- | ---: | ---: |
| v1 CRF | 0.200 ms | 7.815 ms |
| v2 CRF | 0.343 ms | 9.589 ms |
| Feature MLP | 1.615 ms | 10.283 ms |
| Shared square MLP | 0.417 ms | 7.983 ms |
| Changed-square MLP (1 hidden layer) | 0.875 ms | 8.364 ms |
| Changed-square MLP (2 hidden layers) | 0.973 ms | 8.531 ms |
| Spatial CNN | 0.456 ms | 7.777 ms |
| Spatial Attention | 0.599 ms | 8.048 ms |

Total postprocessing was approximately **8–10 ms** for this board. This excludes ChessQ image inference, camera capture, acceptance gates and temporal waiting. A separate benchmark was running concurrently, so small differences should not be treated as a reliable speed ranking. The earlier scoring table uses different inputs and run conditions. [Recorded timing summary and scope](evaluations/postprocessor-latency-20261011.json).

### Interpretation and artifacts

These results compare conditional scoring on real still-photo probabilities with synthetic predecessors constructed from the correct current label. Candidate coverage is guaranteed by that construction, not measured in deployment. They do not establish live commit accuracy, recovery from an incorrect previous board, or temporal behavior. All six models are retained regardless of photo performance. They are now selectable alongside v1/v2 in Settings → Recognition Model, behind the unchanged ChessQ Lite V4 image recognizer. The default two-layer changed-square MLP has the highest one-predecessor photo point estimate; this is not evidence of a live-video advantage. Synthetic test shards remain unread.

Published artifacts: [six trained checkpoints and their manifest](../research/models/neural-postprocessors-v1/manifest.json), and [machine-readable evaluation results, training curves, and audits](evaluations/neural-postprocessors-20261011.json). The checkpoint bytes match the selected models used for the tables above. Full image data and per-condition bulk records remain local.

Run directory (Git-ignored): `data/crf/runs/neural-postprocessors-run1`. Each model has `model.pt`, `history.json`, and `report.json`; the run has source snapshots, configuration, hashes, baseline validation reproduction, and training summary. `photo-evaluation.json` retains all predecessor conditions and each new model's predicted 64-square board, move, operation, posterior, target NLL, correctness, and score timing. It also records dataset, baseline, and checkpoint hashes. Full checkpoint loading was verified before evaluation.


**Completion audit:** All six checkpoints were reloaded and their hashes, selected epochs, parameter counts and frozen baseline verified. All 72 training epochs completed and 20 CRF/data/neural tests passed. See the [machine-readable audit records](evaluations/neural-postprocessors-20261011.json) for details.

Reproduction (use a new training directory; existing outputs are rejected):

```bash
research/edgetam-env/bin/python scripts/train_neural_postprocessors.py --output data/crf/runs/neural-postprocessors-reproduction
research/edgetam-env/bin/python scripts/evaluate_neural_postprocessors.py --run data/crf/runs/neural-postprocessors-reproduction
```
