# Camera Chess — Play Stockfish on a real chessboard

**V1 · Apple Silicon macOS** · English | [简体中文](README.zh-CN.md) | [Русский](README.ru.md)

**A physical chessboard. A camera. A computer opponent that speaks its moves.**

Camera Chess is a camera-based chess app that lets you play **Stockfish on a physical chessboard**. **ChessQ Lite V4** recognizes the pieces locally on your Apple Silicon Mac, and a **lightweight CRF trained for this project** combines its probabilities with the previous position. The digital board follows your moves, and the computer speaks its reply. Use an ordinary board and a camera—no electronic chessboard or piece sensors required.

![Camera Chess: camera-based physical chessboard recognition and Stockfish play on macOS](docs/media/demo-v1.gif)

[Screen recording · 55 sec](docs/media/demo-v1.mp4) · [Phone demo · portrait · 29 sec](docs/media/demo-v1-portrait.mp4) · [GIF preview](docs/media/demo-v1.gif)

The demo videos are silent; the app itself provides spoken moves.

## Why it feels good

- **Real-board play:** ChessQ Lite V4 reads the camera image; the CRF, temporal checks and chess rules help resolve uncertain piece labels using the previous position.
- **A capable opponent:** Stockfish supplies legal moves with four difficulty levels, clear board arrows, and English voice guidance. Sound starts enabled.
- **Fast feedback:** live camera, board updates, move confirmation and recovery cues keep the physical and digital boards connected.
- **Simple controls:** click the floating ♞ to open the game, use **Back to Home** to leave a session, and **Quit Application** to stop every background component.
- **Practical recovery:** save/load positions, resume a session, correct a misread square, or start from a custom endgame position.
- **Promotion without spare pieces:** choose queen, rook, bishop or knight, then keep the pawn as a substitute or replace it with the chosen piece. Confirm once the camera sees the matching board.
- **Local processing:** camera recognition and engine play run on your Mac; no cloud inference account is required.

## Recent updates

- Added the project-trained CRF postprocessor and automatic position acceptance, with correction and rollback through **Fix recognition**. Initial physical-board feedback is encouraging; broader accuracy testing is still needed.
- Added pawn promotion choices, including using a pawn as a substitute when no spare piece is available. **Implemented, but not yet fully tested by people on a physical board.**
- Added batch recognition corrections with a before/after comparison.
- Improved endgame setup, saved-game recovery and voice guidance.

Recognition accuracy still needs improvement, especially across different boards, lighting and camera angles. **Known promotion issue:** after a computer pawn promotes to a knight, the Confirm button can remain disabled. Reproducing and fixing this issue, and testing more real games, are the next priorities.

## How recognition works

```mermaid
flowchart TD
    C["Camera frame"] --> V["ChessQ Lite V4 · frozen weights<br/>64 × 13 piece probabilities"]
    V --> R["Project-trained CRF"]
    G["Accepted board + turn"] --> R
    R --> T["Temporal stability + chess rules"]
    T --> G
    G --> S["Stockfish → next move instruction"]
    G -. "Fix recognition, when needed" .-> F["Edit board; keep, undo or replace inferred move"]
    F --> G
```

The CRF is trained separately; the image recognizer's weights stay unchanged. It scores candidate positions using the previous board and current piece probabilities. Stable readings continue automatically through the turn rules. During the computer's turn, only its instructed move can be accepted; while it is thinking, another move cannot be committed. Corrections reconcile the edited board with current and recent positions and discard engine replies based on a mistaken move.

The CRF and UI are still being tuned. Synthetic validation results do not establish real-world camera accuracy. See the [experiment log](docs/crf-experiment-log.md) for the design, validation results and current limitations, and the [dataset build record](docs/crf-data.md) for sources and reproducible preparation. Downloaded datasets, camera frames, saved games, correction archives and run logs stay local and are excluded from Git; the repository includes code, documentation and the small trained CRF checkpoint.

## V1 performance

Measured on the development Apple Silicon Mac, across **426 recorded frames**, before the CRF integration:

| Measurement | Median | 95th percentile |
| --- | ---: | ---: |
| Per-frame recognition processing | **387 ms** | **449 ms** |
| Capture-to-recognition-result latency | **487 ms** | **577 ms** |

These are warm-running frame timings, not camera startup or complete move-to-speech latency. Stable-position confirmation adds deliberate settling time. Both retained reference-board images matched their expected positions exactly (**2/2**); this small smoke check is not a general accuracy benchmark. The demo shows working physical-board play, while lighting, camera angle and occlusion still affect recognition.

**Current application checks:** 166 Python tests and 7 browser-UI test scripts pass. These checks do not establish real-world recognition accuracy or replace human testing of promotion.

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

**Resume** keeps an in-memory game while the service runs; named **Save / Load** positions survive restarts and retain the saved player colors without checking board direction again. **Endgame** first asks which color you play, then scans the position and lets you confirm who moves next. Closing the last browser page stops the camera; quitting through the home screen or floating × closes the camera, recognition worker, engine and service.

**Promotion (experimental):** choose queen, rook, bishop or knight, then either keep the pawn as a substitute or replace it with the chosen piece. The digital board marks substitutes with **P**. This feature is implemented but still needs full human testing on a physical board.

## Fix recognition

If the displayed position is wrong, open **Fix recognition**, choose the actual pieces (or Empty), and apply the changes together. Play waits while you edit. The app can keep a correctly recorded move, undo a false detection, or replace it with the actual move; you do not need to classify the error. You can undo edits before applying them and view the locally saved camera image, original reading and corrected board afterward.

## To do

- [ ] Polish onboarding, camera setup, sound feedback and permission/error messages.
- [ ] Complete human testing of promotion, including underpromotion and pawn substitutes.
- [ ] Fix the disabled Confirm button reported after a computer pawn promotes to a knight.
- [ ] Improve endgame setup, side-to-move confirmation and position recovery.
- [ ] Improve recognition accuracy and evaluate it across boards, lighting and camera angles.
- [ ] Reduce settling time and measure full move-to-feedback latency.
- [ ] Package a signed, notarized Mac release with simpler installation.

## Common questions

**Is this an electronic chessboard alternative?** It uses computer vision with an ordinary physical board. You move both your pieces and Stockfish's pieces by hand; this is not a robotic board.

**Does it need cloud AI or a subscription?** No cloud inference account is required. Setup downloads the dependencies and model; board recognition and engine play then run locally.

**Which recognition model does it use?** [ChessQ Lite](https://huggingface.co/joelseytre/chessq-lite), the ViT-S/14 V4 FP16 checkpoint (`cq-vit-s-644-v4-seed2-safetensors-fp16-r2`), followed by this project's separately trained CRF. ChessQ supplies image probabilities, the CRF uses the previous position to interpret them, and Stockfish chooses the moves.

**Does it support Windows, Linux, or online chess?** V1 targets Apple Silicon macOS and local play against Stockfish. It does not currently provide online game integration or a signed installer.

## Feedback & contributions

Tried it with your own board? [Share feedback or report a problem](https://github.com/AmethystineAlpaca/camera_chess/issues/new/choose). Board style, camera angle, lighting, Mac model, and a reproducible example help improve recognition. See [CONTRIBUTING.md](CONTRIBUTING.md) for development and testing.

## Development & credits

```bash
research/edgetam-env/bin/python -m unittest discover -s validation -p 'test_*.py'
for test in validation/test_ui_*.cjs; do node "$test" || exit; done
```

Application code lives in `validation/`; the browser UI is `validation/results/lite-live/index.html`. `start.sh` builds the Swift launcher. The historical `edgetam-env` name is the active Python runtime path.

Recognition: [ChessQueries / ChessQ Lite](https://github.com/JSeytre/chessqueries), **PolyForm Noncommercial 1.0.0**. Opponent: [Stockfish](https://stockfishchess.org/), **GPLv3**. Setup downloads the upstream recognition code and verifies the model checksum; third-party licenses apply separately.
