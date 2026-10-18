#!/usr/bin/env bash
set -euo pipefail
umask 077
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3.11}"
PROFILE="${1:---cpu}"
case "$PROFILE" in --cpu|--gpu) ;; *) echo 'Usage: ./setup.sh [--cpu|--gpu]' >&2; exit 2 ;; esac
for command in "$PYTHON_BIN" node npm ffmpeg ffprobe; do
  command -v "$command" >/dev/null 2>&1 || { printf 'Missing dependency: %s\n' "$command" >&2; exit 1; }
done
"$PYTHON_BIN" -c 'import sys; assert sys.version_info[:2] == (3, 11), "Python 3.11 is required"'
node -e 'const [major, minor] = process.versions.node.split(".").map(Number); if (major < 22 || (major === 22 && minor < 12)) throw Error("Node.js 22.12 or newer is required")'
if [[ ! -x "$PROJECT_DIR/.venv/bin/python" ]]; then "$PYTHON_BIN" -m venv "$PROJECT_DIR/.venv"; fi
"$PROJECT_DIR/.venv/bin/python" -c 'import sys; assert sys.version_info[:2] == (3, 11), "The existing .venv must use Python 3.11"'
REQUIREMENTS="$PROJECT_DIR/requirements-cpu.txt"
if [[ "$PROFILE" == --gpu ]]; then REQUIREMENTS="$PROJECT_DIR/backend/requirements.txt"; fi
"$PROJECT_DIR/.venv/bin/python" -m pip install -r "$REQUIREMENTS"
"$PROJECT_DIR/.venv/bin/python" -m pip check
(
  cd "$PROJECT_DIR/frontend"
  npm ci --ignore-scripts
  npm run build
)
if [[ ! -e "$PROJECT_DIR/.env" ]]; then cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"; chmod 600 "$PROJECT_DIR/.env"; fi
printf '%s\n' 'Ready. Run ./run.sh demo, then ./run.sh ui (integrated) or ./run.sh annotate (standalone).'
