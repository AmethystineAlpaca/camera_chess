#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p validation/frames/lite-live validation/results/lite-live "Camera Chess.app/Contents/MacOS"
SOURCE="$PWD/validation/ChessLauncher.swift"
LAUNCHER="$PWD/Camera Chess.app/Contents/MacOS/CameraChess"
if [ ! -x "$LAUNCHER" ] || [ "$SOURCE" -nt "$LAUNCHER" ]; then
  /usr/bin/swiftc "$SOURCE" -o "$LAUNCHER.next"
  mv "$LAUNCHER.next" "$LAUNCHER"
fi
open "$PWD/Camera Chess.app"
