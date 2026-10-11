# Local data for potential improvements

Implemented on 2026-10-11. This records evidence; it does not train, change model weights, upload anything, or certify predictions as ground truth.

## Settings

**Log Data for potential improvements (stored locally; never sent automatically)** is checked by default. The preference persists in `validation/recognition-settings.json`, independently of Recognition Model, including when postprocessing is disabled. **Open Log Folder** opens `validation/improvement-logs/` in Finder. The folder is Git-ignored.

Turning recording off prevents subsequent archive writes, including queued items from the old recording consent epoch. Existing recordings are preserved. Fix recognition still works: new correction drafts/edits are held in RAM while disabled instead of being archived, and are lost on exit. The application's current camera preview and active inference packet remain necessary runtime files; disabling collection does not disable the camera or erase older recordings.

The default total evidence budget is **20 GiB**, conservatively reserved across both writers. At the limit, or after a write error, collection stops and the UI shows a warning; game recognition continues. No automatic eviction occurs. After freeing space, toggle off/on to retry; moving complete old recording folders elsewhere preserves their evidence. The quota ledger intentionally does not reclaim reservations automatically after crashes or manual moves, so after moving complete recordings to another disk, turn Log Data off and run `research/edgetam-env/bin/python validation/evidence_review.py recount validation/improvement-logs`, then enable it again. Keep original recordings intact if they will still be used. Do not promise a precise hour count: inference images, sampled context images, JSON and fsync overhead all contribute.

## Evidence layout and identifiers

Folders and records use local ISO-like date/time **with UTC offset**, descriptive type, and a short random suffix, for example `2026-10-11_18-30-00.123456+0800_game-12345678_abcdef123456`. These names identify recording time, not chess truth. The embedded game ID, stream ID and frame capture timestamp are preserved independently.

Each recording contains:

- `session.json`: initial context hint, consent epoch, source hashes, budget and policy. The initial hint may precede player-color resolution or later model selection; per-decision context is authoritative for what the controller used.
- `observations/<dated ID>/`: exact original inference JPEG, original probabilities, canonical `64 × 13`, raw `64 × 1`, classifier checkpoint hash, preprocessing hash/settings, runtime versions and capture stream/sequence/time. Saved at the inference producer, even if the controller never consumes that result.
- `decisions/<dated ID>/`: explicit observation reference, pre- and post-evaluation state, logical and physical reference arrays/FENs, player/computer colors and whether resolved, turn, pending move, model/checkpoint, corrections, proxies/promotion, candidate proposal, gate results, temporal evidence and controller outcome. CRF/MLP outputs are retained when rejected. A missing/bypassed output stays null.
- `timeline/<dated ID>/`: sampled camera JPEGs with capture identity and detected skipped sequences, including periods with no inference. These are review context, **not uninterrupted video or proof that pieces stayed stationary**. No hand detector is claimed; visibility remains unreviewed. Clear but difficult frames are never excluded by low model confidence.
- `events/<dated ID>/`: successful user operations and before/after context. Correction events include the frozen reviewed snapshot, exact edits and the observation link. Only edited squares are human annotations; other squares are not promoted to truth.
- `labels/`: explicit offline review revisions. No labels are created automatically by acceptance, lack of edits, or later successful frames.

Every evidence item is written to an incomplete directory, flushed, checksummed and then renamed into place. Missing images, incomplete directories, mismatched hashes and absent source references cannot pass trusted export. A queue reference alone is not evidence that disk persistence succeeded. Queues are bounded; drops and write errors are reported. Capture timestamps, inference completion and controller evaluation times are distinct. Camera restarts change stream IDs. A game change preserves already queued evidence under its original recording; disabling collection invalidates pending old-consent writes.

Canonical order: squares `a8…h8, a7…h7, …, a1…h1`; classes `.PNBRQKpnbrqk`. Probabilities are unmodified. The live reference is never retrospectively replaced by a corrected state. Physical pawn substitutes and logical promoted identities remain separate. The selected model and the actual scorer (including fixed physical visual likelihood) are separately recorded.

## Offline inspection and verified export

Use the project's Python environment. Examples below use a recording folder as `$RECORDING` and one of its decision directories as `$DECISION`:

```bash
research/edgetam-env/bin/python validation/evidence_review.py audit validation/improvement-logs
research/edgetam-env/bin/python validation/evidence_review.py gallery "$RECORDING"
```

Open the generated `review.html` locally. Each decision links to the original full-resolution image and shows its reference, model and provisional prediction. Inspect the image and timeline; check **all 64 visible squares and the actual reference state**. Do not infer unchanged pieces across an occlusion, missed segment, move, lift or slide. Ambiguous images stay unverified. A later successful frame supplies a suggestion, never an automatic backward label.

Turn Log Data off before labeling/exporting so the recording and its correction frontier cannot change during review. For recordings still in the application folder, the tool enforces this. Only after the per-image review:

```bash
research/edgetam-env/bin/python validation/evidence_review.py label "$DECISION" \
  --target 'FULL_FEN_OR_PIECE_PLACEMENT' --reviewer 'reviewer-id' \
  --confirm-all-squares --confirm-reference
research/edgetam-env/bin/python validation/evidence_review.py export "$RECORDING" \
  --output /path/to/new-reviewed-export.jsonl
```

Labels are append-only and pin the image/decision hashes, reviewer, time, actual previous FEN and correction-event frontier. Export checks pairing, full labels, reference consistency and CRF candidate membership. Subsequent correction/undo/recovery events invalidate prior review until re-reviewed. A new export never overwrites an old one. The export report lists exclusions. Physical substitute/promotion cases remain recorded but are excluded from the ordinary CRF export protocol. An incorrect runtime reference must be studied separately; do not mark it verified to make an example exportable. Existing exports are snapshots, not live datasets: regenerate and check them again before training after corrections.

Partial corrections remain in the event/correction evidence and are not full-board labels. Whole-game/session split groups accompany exports; do not split neighboring frames between training and evaluation. The review flags are human assertions, not a mathematical correctness guarantee. No training or model deployment runs from this tool.

## Verification and remaining empirical checks

Automated tests exercise exact image/output/reference pairing, held/rejected decisions, commits, camera timeline gaps, model changes, opt-out during in-flight inference, re-enable epochs, queued final frames across game changes, quotas, interrupted writes, corrupt evidence, review/export gates, delayed corrections, HTTP preference persistence, folder opening and RAM-only corrections.

Physical-camera overhead, real storage/hour, annotation accuracy and recognition improvement require a 3–5-game pilot. This implementation does not claim those measurements. Sampled timelines cannot support frame-perfect full-video replay or certify a physical-position interval. No automatic occlusion-based rejection, label propagation, training, or uploading is performed.


Validation on 2026-10-11: the full application Python suite passed (208 tests at that run); the final focused evidence/settings suite passed all 24 tests after the last review/export checks were added. All seven JavaScript UI test scripts passed. A separate 3.75-second synthetic write check used 1280 × 720 JPEGs of 571,821 bytes at eight observations/second: 30 inference images, 30 sampled timeline images and 30 controller decisions were written, with zero reported drops/errors. Producer-hook plus controller time had a 4.24 ms median and 6.41 ms maximum. This is a short synthetic pipeline check, without camera capture or neural inference; it is not a live-game latency benchmark.
