# Photo evaluation protocol and audit — 2026-10-11


**Question and training link:** Compare the saved E06 v1 checkpoint (25 weights, epoch 6 of 12) and E10 v2 checkpoint (250 weights, epoch 8 of 12) under supplied synthetic reference states. Both were fitted on the same synthetic training split and selected by synthetic validation NLL. This evaluation performs no fitting, checkpoint selection, or application replacement.

**Protocol and inputs:** All 176 retained photos from Felix Fabricius's [Robot chess commentator: square dataset](https://huggingface.co/datasets/felixfabricius/robot-chess-commentator-squares), revision `d71ba52761e497b18bbe5743efcbafb0d1f21091`, use the same cached frozen-recognizer `64 × 13` probabilities across methods. There are 23 dataset setups: 54 train photos / 8 setups, 67 validation / 5 setups, and 55 test / 10 setups. `train`, `validation`, and `test` are Felix's original photo partitions, not CRF training-data splits.

**Recognizer identity and official evaluation:** The raw baseline is the exact frozen **ChessQ Lite V4** checkpoint shipped with this application: release `cq-vit-s-644-v4-seed2-safetensors-fp16-r2`, ViT-S FP16, local SHA-256 `fd269bcfdefe6208b75490d7ac72524c0970f8212470a88012ce755b5f092d10`. This checkpoint is from Joël Seytre's [ChessQueries project](https://github.com/JSeytre/chessqueries); Felix Fabricius supplied the photo dataset, not the recognizer. The [Lite model card](https://huggingface.co/joelseytre/chessq-lite) describes V4 as a smaller recognizer further trained on proprietary data.

The [ChessQueries paper](https://arxiv.org/abs/2608.30762) reports exact-board accuracy of **99.5% / 98.5% / 87.1% / 87.6%** on ChessReD / ChessCog / SLCC / CVChess for its main ViT-L model; its ViT-S ablation reports **98.6% / 97.7% / 75.9% / 49.4%**. Neither is this exact Lite V4 checkpoint. The official [Lite V4 validation file](https://huggingface.co/joelseytre/chessq-lite/blob/main/cq-vit-s-644-v4-seed2-safetensors-fp16-r2-validation.json) reports **98.6% / 97.4% / 78.0% / 100%** on those benchmarks, but is an FP32-to-FP16 export-consistency check, not an independent accuracy evaluation. The official materials reviewed do not report Felix-dataset results or establish overlap with proprietary training data.

The [evaluator](../scripts/evaluate_crf_synthetic_previous.py) constructs a finite pool of predecessor FENs by reversing supported moves from the **current piece placement labeled in the Felix dataset**. Each predecessor passes `python-chess` position-validity checks and has a verified legal forward move yielding exactly that placement. With seed `20261011`, hash-priority sampling without replacement selects up to ten distinct FENs per photo; the first five match the five-state run, and the first one supplies the single-state result.

This is sampling from the implemented generator's pool, not an exhaustive enumeration of every historical FEN or a sample of observed game histories. Ordinary generated predecessors have no castling rights or en passant square; the special-move constructions supply the necessary fields. Either moving color may be constructed. The matching target is the 64-square piece placement, not the full rule state or the Felix dataset's recorded preceding move.

**What the baselines mean:** Raw per-square argmax uses only the current photo probabilities and receives no predecessor. The fixed scorer and both CRFs receive the identical sampled predecessor and share the candidate generator: legal successors, unchanged placement, and supported illegal relocations. The fixed scorer has no learned coefficients and maximizes the sum of visual log probabilities. It already benefits from the supplied state and candidate constraints. None of these rows runs the full CRF-disabled application, live acceptance gates, or temporal voting. The target was present in every candidate set by construction and verified in every condition; 100% target coverage here is not a deployment coverage estimate.

**Metrics and denominators:**

“All correct” refers to **all predicted current boards being correct**, not to the predecessors being correct. Each prediction must match all 64 labeled squares; a photo passes only if every selected predecessor yields that exact board. This is not majority voting, an average of five/ten predictions, or a sequence with predicted-state feedback.

- **One random predecessor:** the first sampled state; 176 eligible photos.
- **All correct with 5 different predecessors:** first five predictions all exact; 174 eligible photos and 876 total conditions in the five-state run. Two generator pools have only 2 and 4 predecessors.
- **All correct with 10 different predecessors:** all ten predictions exact; 172 eligible photos and 1,738 total conditions in the ten-state run. Two additional pools have only 6 predecessors each.

Photos lacking the requested number are excluded only from that all-correct denominator; their available conditions remain in the per-condition records. Multiple predecessors reuse the same photograph and probabilities, so they are repeated conditions, not additional independent images. The eligible photo sets differ between five and ten; compare the displayed fractions with their denominators. On the shared eligible photos, passing all ten necessarily includes passing the first five.

**All-photo results — descriptive:**

| Method | One random predecessor | All correct with 5 different predecessors | All correct with 10 different predecessors |
| --- | ---: | ---: | ---: |
| Raw per-square argmax | 1/176 — 0.57% | 1/174 — 0.57% | 1/172 — 0.58% |
| Fixed log-probability scorer | 114/176 — 64.77% | 85/174 — 48.85% | 71/172 — 41.28% |
| CRF v1 — 25 weights | 125/176 — 71.02% | 94/174 — 54.02% | 80/172 — 46.51% |
| CRF v2 — 250 weights | 122/176 — 69.32% | 95/174 — 54.60% | 80/172 — 46.51% |

**Results by Felix dataset split:** Cells below are correct photos / eligible photos, using the same definitions.

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

The 67-photo Felix validation split had already been inspected in earlier experiments, so the 176-photo total is descriptive. The 55-photo Felix test split was not used for those prior validation comparisons or CRF fitting, but **has now been evaluated and exposed**. It must not be described as untouched in future tuning. The frozen recognizer's pretraining overlap with this dataset has not been independently audited. The synthetic 20,000-observation test split remains unscored.

**Uncertainty and interpretation:** Paired differences use the same predecessors and photos. Confidence intervals use 20,000 bootstrap resamples of Felix dataset setups, retaining all photos within a sampled setup.

| Photo-level metric | v1 minus v2 | 95% setup-bootstrap interval |
| --- | ---: | ---: |
| One random predecessor | +1.70 pp | -2.72 to 6.73 pp |
| All correct with 5 different predecessors | -0.57 pp | -3.11 to 1.96 pp |
| All correct with 10 different predecessors | +0.00 pp | -2.37 to 2.38 pp |

All three intervals include zero. v1 leads by three photos with one predecessor; v2 leads by one photo with five; both pass on 80/172 with ten. On the test split, v1 leads by one photo under each metric. These results do not establish a reliable model-size winner or show that 25 weights are insufficient. Differences reflect the complete feature/regularization configurations, not parameter count alone. Intervals condition on this seed's generated states; they do not measure sensitivity across new predecessor samples or new camera domains.

**Limits:** Current images are real rectified still photos, but the previous state uses the correct current label to guarantee a one-legal-move relationship. The experiment measures conditional scoring with that privileged reference. It does not test recognition of an actual previous image, incorrect-reference recovery, unchanged/illegal target events, hand occlusion over time, wrong commits, or end-to-end latency. Its value is an offline comparison and a check of sensitivity to different supplied predecessors; real continuous-video evaluation remains pending.

**Raw recognizer audit of Felix's original test partition:** The source test partition contains 88 images; 55 are retained for the CRF comparison after transition/label consistency and overlap filters. Running the frozen ChessQ Lite V4 checkpoint directly on all 88 images, without CRF or a predecessor, gave **0/88 exact boards** and **76.14% square accuracy** (48.73/64 squares correct per image on average). The retained 55-image subset gave 0/55 exact and 76.28% square accuracy; direct predictions matched the cached values for all 55. Thus the 55-photo filter does not account for the zero exact-board result. This is Felix-dataset performance, not the model's published benchmark result or a verified holdout from its undisclosed proprietary training data. The [raw-test audit script](../scripts/audit_chessq_lite_photo_test.py) reproduces this check; its per-image JSON is in the Git-ignored run directory.

**Evidence and reconciliation:** The [evaluation report](crf-real-photo-synthetic-previous-evaluation.md) contains per-square means, individual-method intervals, and reproduction commands. The local JSON records contain each sampled FEN, legal forward move, candidate count/index, and each method's exact-match flag and square accuracy; they do not store the full predicted boards. During this review, the earlier text still contained results from the superseded sampling implementation. Replaced those figures with the current nested-sampling results, and fully reran both five- and ten-state evaluations into temporary audit files: all JSON fields matched, and all five-state conditions exactly matched the corresponding ten-state prefix. No source result, model, or dataset was overwritten by this audit.

| Audited artifact | SHA-256 |
| --- | --- |
| `scripts/evaluate_crf_synthetic_previous.py` | `25bce8ae35bd2e3ceff0f6f8da1da3619a27d821ab1c3932e4acb7f2042ed221` |
| `data/crf/runs/real-photos-synthetic-previous-v1/all-seed20261011-n5.json` | `9502d4fcdb65baabb62e225d512fe398eb7de3ec2a74210ac02baafbd28374e8` |
| `data/crf/runs/real-photos-synthetic-previous-v1/all-seed20261011-n10.json` | `d3d849f095916bf19ebff35d12434ef8520a983dc13392188be8faf5caa827c1` |

Model hashes remain those recorded in E06/E10. Cached photo probabilities: `974dcbe44e2d3cf734592adc097b60133bbd50bd27e61317ca20011310b03fc2`; frozen image checkpoint: `fd269bcfdefe6208b75490d7ac72524c0970f8212470a88012ce755b5f092d10`. Both were checked against the input files. Bulk artifacts are local and Git-ignored.
