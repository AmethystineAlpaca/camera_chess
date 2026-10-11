# Optional board perspective correction

Settings → **Perspective correction** is enabled by default. The preference is
stored as `rectify_board` in the existing local recognition settings file.
Disabling it passes the original camera bytes to the existing recognizer.

When enabled, the side panel shows the original camera frame and the exact JPEG
passed to the model (before its normal resize/normalization). Both views are
published in one atomic `perspective-preview.json` packet. If localization fails,
the second view is explicitly labelled **original fallback**. Missing OpenCV,
blur, occlusion, an incomplete board, and a stale camera never authorize reuse
of an unchecked old transform. Changing the option preserves manual corrections.

## Implementation boundary

- `validation/board_rectification.py`: standalone JPEG → JPEG + metadata
  preprocessor; no imports from the model, game controller or server.
- Initialization uses bounded-resolution line/contour proposals and partial
  checkerboard corner lattices. Proposals must match an alternating 8×8 grid.
- Every processed frame uses forward/backward optical flow, RANSAC, spatial
  coverage checks and current-image checker validation. It reacquires after a
  lost track, a camera discontinuity, or every 12 tracked frames. There is no
  fixed camera-angle assumption and no extra neural model inference.
- Output is 768×768 with context padding; camera-facing orientation is preserved.
  The existing board-orientation check still runs on the model's actual input.
- `lite_live.py` has the input hook and atomic preview publication. Mode changes
  reset only the reader's temporal vote window, retaining its last confirmed
  position. Failure and disable return the original bytes.
- `lite_live_server.py` only validates/persists/exposes the boolean setting and
  invalidates cached postprocessor decisions when it changes.
- `board-perspective.js` / `.css` own the preview and checkbox behavior. The HTML
  provides their elements plus optional settings/frame/stop hooks.
- Correction evidence continues to retain the original camera image. Observation
  metadata includes whether correction was applied, corners, homography, output
  size, JPEG quality, and processing time. Improvement logs include this in the
  preprocessing description, so transformed predictions are not mistaken for
  raw-image predictions. Context timeline images remain untouched.

OpenCV is installed by `setup.sh` only if its required API is absent; see
`validation/rectification-requirements.txt`. Do not install multiple distributions
that provide `cv2` into the same environment. An existing installation without
OpenCV still runs recognition and displays an explicit fallback message.

## Validation and limits

Image-level tests exercise oblique 8×8 localization, partially occupied squares,
motion tracking, scene loss/reacquisition, rejection of non-boards, exact byte
bypass when disabled, missing OpenCV, and the paired-preview contract. HTTP tests
cover default/persisted settings, boolean validation and correction preservation.
UI tests cover settings failures, exact paired input, fallback, stale frames and
late responses after leaving the game. Desktop (1280×850) and narrow (390×844)
layouts were also checked in a real browser.

The current frozen recognizer was smoke-tested on original and transformed real
photos, including its orientation path. Successful inference is not evidence of
improved accuracy: this experiment changes the appearance of tall pieces. Plane
rectification cannot reveal occluded pieces or reconstruct a true overhead view.
Some real photos cannot be localized and intentionally fall back. This remains
an A/B feature requiring live handheld evaluation.

A local repeated-frame run on `camera-45.png` measured about 55 ms acquisition
and 8 ms median tracking (9 subsequent frames). This is a small warm machine
measurement, not an end-to-end latency guarantee or a moving-camera benchmark.
Generated QA screenshots, smoke outputs and logs stay under ignored
`validation/results/perspective-check/`.

## Removing the experiment

1. Remove the rectifier import/instance, processing/preview block and metadata
   field in `lite_live.py`; decode `j` instead of `model_jpeg` again. Remove the
   input-mode vote reset associated with that block.
2. Remove `rectify_board` handling from `lite_live_server.py` and the optional
   rectification field in improvement-log preprocessing metadata.
3. Remove the checkbox/paired-view wrappers, CSS/script includes and optional
   `window.BoardPerspective` hooks from `index.html`, retaining `#camera`.
4. Delete `board_rectification.py`, the two preview assets, their `.gitignore`
   exceptions, the dedicated image/UI tests, this document and the OpenCV setup
   block/requirements file. Remove the corresponding setting test.

No model weights, chess rules, saved-game schema or camera-capture code depend
on this experiment. An unused `rectify_board` key in local settings is harmless.
