#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
PYTHON=${PYTHON:-python3.12}
command -v "$PYTHON" >/dev/null || { echo 'Install Python 3.12 first (for example: brew install python@3.12).'; exit 1; }
command -v swiftc >/dev/null || { echo 'Install the Xcode Command Line Tools first: xcode-select --install'; exit 1; }
mkdir -p research/models validation/results/lite-live
if [ ! -d research/chessqueries ]; then
  git clone https://github.com/JSeytre/chessqueries.git research/chessqueries
  git -C research/chessqueries checkout 58c1d1e8572bdf5632a23a2fc2956d4a25d48b40
fi
if [ ! -x research/edgetam-env/bin/python ]; then
  "$PYTHON" -m venv research/edgetam-env
  research/edgetam-env/bin/python -m pip install -e research/chessqueries
fi
research/edgetam-env/bin/python - <<'PY'
import hashlib, json, urllib.request
from pathlib import Path
manifest=json.loads(Path('research/models/chessq-lite-manifest.json').read_text())
path=Path('research/models/chessq-lite.safetensors')
expected=manifest['model']['artifact_sha256']
if not path.exists():
    temporary=path.with_suffix('.download')
    print('Downloading ChessQ Lite weights...')
    urllib.request.urlretrieve(manifest['artifact']['url'],temporary)
    if hashlib.sha256(temporary.read_bytes()).hexdigest()!=expected:
        temporary.unlink()
        raise SystemExit('Model checksum mismatch.')
    temporary.replace(path)
if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
    raise SystemExit('Model checksum mismatch.')
print('Model verified.')
PY
if ! command -v stockfish >/dev/null && [ -z "${STOCKFISH_PATH:-}" ]; then
  echo 'Install Stockfish (brew install stockfish), or set STOCKFISH_PATH to its executable.'
fi
echo 'Setup complete. Run ./start.sh to open Camera Chess.'
