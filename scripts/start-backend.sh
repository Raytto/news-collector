#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Load .env if present to configure SMTP and other settings
if [ -f .env ]; then
  # Export variables defined in .env (KEY=VALUE lines)
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

PY="python"
command -v "$PY" >/dev/null 2>&1 || PY="python3"

USE_VENV=1
# Ensure a usable venv (bin/activate must exist); otherwise try to create one.
if [ ! -f .venv/bin/activate ]; then
  rm -rf .venv 2>/dev/null || true
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
