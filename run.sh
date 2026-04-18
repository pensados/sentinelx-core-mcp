#!/usr/bin/env bash
set -euo pipefail
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$BASE_DIR"

if [ -f "$BASE_DIR/.env" ]; then
  set -o allexport
  source "$BASE_DIR/.env"
  set +o allexport
fi

mkdir -p "${LOG_DIR:-$BASE_DIR/logs}"

PYTHON_BIN="$BASE_DIR/.venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
  echo "Virtualenv not found at $BASE_DIR/.venv" >&2
  echo "Create it with: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

exec "$BASE_DIR/.venv/bin/python" app.py >> "${LOG_FILE:-$BASE_DIR/logs/sentinelx-core-mcp.log}" 2>&1
