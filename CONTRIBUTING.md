# Contributing to Camera Chess

Feedback from real boards is especially useful. [Open an issue](https://github.com/AmethystineAlpaca/camera_chess/issues/new/choose) with your Mac/chip and macOS version, board and camera setup, lighting, expected behavior, actual behavior, and steps to reproduce. A short cropped board image or clip helps; remove personal information before uploading.

For recognition problems, include the intended position (FEN if available), camera orientation, and whether hands or pieces obscure any squares. For audio or lifecycle problems, mention open browser tabs and the exact sequence of controls used.

## Development

Follow the setup commands in [README.md](README.md). Application code is in `validation/`, the browser UI in `validation/results/lite-live/index.html`, and the Swift launcher in `validation/ChessLauncher.swift`.

```bash
research/edgetam-env/bin/python -m unittest discover -s validation -p 'test_*.py'
for test in validation/test_ui_*.cjs; do node "$test" || exit; done
```

Keep changes focused and describe how you checked the behavior. For camera-dependent changes, record the hardware and conditions used. Do not commit runtime logs, saves, private photos, downloaded model weights, local environments, or compiled applications. Respect the upstream ChessQueries/ChessQ Lite and Stockfish licenses described in the README.
