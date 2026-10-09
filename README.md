# Camera Chess · V1

**A physical chessboard. A camera. A computer opponent that speaks its moves.**

Camera Chess turns an ordinary board into an interactive chess experience on Apple Silicon. Move real pieces, watch the digital position update, and play against Stockfish—all with local board recognition and a lightweight floating desktop launcher.

![Camera Chess V1 in action](docs/media/demo-v1.gif)

[Watch the full V1 demo](docs/media/demo-v1.mp4) · The public video is silent and has recording metadata removed.

## Why it feels good

- **Real-board play:** ChessQ Lite reads the camera image; confidence and temporal checks help keep uncertain readings from becoming accepted positions.
- **A capable opponent:** Stockfish supplies legal moves with four difficulty levels, clear board arrows, and English voice guidance. Sound starts enabled.
- **Fast feedback:** live camera, board updates, move confirmation and recovery cues keep the physical and digital boards connected.
- **Simple controls:** click the floating ♞ to open the game, use **Back to Home** to leave a session, and **Quit Application** to stop every background component.
- **Practical recovery:** save/load positions, resume a session, correct a misread square, or start from a custom endgame position.
- **Local processing:** camera recognition and engine play run on your Mac; no cloud inference account is required.

## V1 performance

Measured on the development Apple Silicon Mac, across **426 recorded frames**:

| Measurement | Median | 95th percentile |
| --- | ---: | ---: |
| Per-frame recognition processing | **387 ms** | **449 ms** |
| Capture-to-recognition-result latency | **487 ms** | **577 ms** |

These are warm-running frame timings, not camera startup or complete move-to-speech latency. Stable-position confirmation adds deliberate settling time. Both retained reference-board images matched their expected positions exactly (**2/2**); this small smoke check is not a general accuracy benchmark. The demo shows working physical-board play, while lighting, camera angle and occlusion still affect recognition.

**Validation:** 79 Python tests, browser audio/lifecycle checks, and actual-model reference/orientation checks passed in the V1 development environment.

## Try it

Requires **Apple Silicon macOS**, **Python 3.12**, **Xcode Command Line Tools**, and **Stockfish**. This is a source release; dependency downloads need internet access, while recognition and gameplay run locally afterward.

```bash
git clone https://github.com/AmethystineAlpaca/camera_chess.git
cd camera_chess
# Install prerequisites if needed: brew install python@3.12 stockfish
# Install Apple's tools if needed: xcode-select --install
./setup.sh
./start.sh
```

Click the floating chess piece, select **Start a New Game**, allow camera access, and keep all 64 squares visible. The computer plays the color nearest the camera. Move its pieces when instructed, then clear your hands for confirmation. Settings controls difficulty and speaking voice.

**Resume** keeps an in-memory game while the service runs; named **Save / Load** positions survive restarts. **Endgame** lets you confirm your color and the next side to move. Closing the last browser page stops the camera; quitting through the home screen or floating × closes the camera, recognition worker, engine and service.

## To do

- [ ] Polish onboarding, camera setup, sound feedback and permission/error messages.
- [ ] Add a clear pawn-promotion chooser, including underpromotion.
- [ ] Improve endgame setup, side-to-move confirmation and position recovery.
- [ ] Expand recognition benchmarks across boards, lighting and camera angles.
- [ ] Reduce settling time and measure full move-to-feedback latency.
- [ ] Package a signed, notarized Mac release with simpler installation.

## Development & credits

```bash
research/edgetam-env/bin/python -m unittest discover -s validation -p 'test_*.py'
node validation/test_ui_audio.cjs
```

Application code lives in `validation/`; the browser UI is `validation/results/lite-live/index.html`. `start.sh` builds the Swift launcher. The historical `edgetam-env` name is the active Python runtime path.

Recognition: [ChessQueries / ChessQ Lite](https://github.com/JSeytre/chessqueries), **PolyForm Noncommercial 1.0.0**. Opponent: [Stockfish](https://stockfishchess.org/), **GPLv3**. Setup downloads the upstream recognition code and verifies the model checksum; third-party licenses apply separately. Runtime logs, saves, private reference photos, local environments and compiled apps are excluded from this public repository.
