#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PY="python"
command -v "$PY" >/dev/null 2>&1 || PY="python3"

USE_VENV=1
if [ ! -d .venv ]; then
  if ! "$PY" -m venv .venv 2>/dev/null; then
    echo "[WARN] python venv unavailable; falling back to user site-packages" >&2
    USE_VENV=0
  fi
fi

if [ "$USE_VENV" = 1 ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install -q -r backend/requirements.txt
  export PYTHONPATH="$ROOT_DIR"
  exec "$PY" -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload --reload-dir backend --reload-exclude .cache --reload-exclude frontend/node_modules --reload-exclude frontend/dist
else
  pip3 install --user --break-system-packages -q -r backend/requirements.txt
  export PYTHONPATH="$ROOT_DIR"
  exec python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload --reload-dir backend --reload-exclude .cache --reload-exclude frontend/node_modules --reload-exclude frontend/dist
fi
