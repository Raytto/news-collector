#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SESSION_NAME="${BACKEND_SCREEN_SESSION:-news-collector-backend}"

if [ "${1-}" != "__run_backend" ]; then
  if ! command -v screen >/dev/null 2>&1; then
    echo "screen is required but not installed." >&2
    exit 1
  fi
  # Stop existing session with the same name, if any
  screen -S "$SESSION_NAME" -X quit >/dev/null 2>&1 || true
  screen -dmS "$SESSION_NAME" bash -lc "cd '$ROOT_DIR' && scripts/start-backend.sh __run_backend"
  echo "Backend starting in screen session '$SESSION_NAME'."
  exit 0
fi
shift

cd "$ROOT_DIR"

# Prefer conda env "news-collector" if available; fallback to local venv behavior
CONDA_ACTIVATED=0
if command -v conda >/dev/null 2>&1; then
  CONDA_BASE="$(conda info --base 2>/dev/null || true)"
  if [ -n "$CONDA_BASE" ] && [ -f "$CONDA_BASE/etc/profile.d/conda.sh" ]; then
    # shellcheck disable=SC1090
    source "$CONDA_BASE/etc/profile.d/conda.sh"
    TARGET_ENV="/root/miniconda3/envs/news-collector"
    [ -d "$TARGET_ENV" ] || TARGET_ENV="news-collector"
    if conda activate "$TARGET_ENV" >/dev/null 2>&1; then
      CONDA_ACTIVATED=1
      echo "[INFO] Using conda env: $TARGET_ENV"
    else
      echo "[WARN] Failed to activate conda env '$TARGET_ENV'; will fall back to venv" >&2
    fi
  fi
fi

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

if [ "$CONDA_ACTIVATED" = 1 ]; then
  USE_VENV=0
else
  USE_VENV=1
fi

# Ensure a usable venv only when conda is not active
if [ "$USE_VENV" = 1 ]; then
  if [ ! -f .venv/bin/activate ]; then
    rm -rf .venv 2>/dev/null || true
    if ! "$PY" -m venv .venv 2>/dev/null; then
      echo "[WARN] python venv unavailable; falling back to user site-packages" >&2
      USE_VENV=0
    fi
  fi
fi

if [ "$USE_VENV" = 1 ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install -q -r backend/requirements.txt
  export PYTHONPATH="$ROOT_DIR"
  exec "$PY" -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload --reload-dir backend --reload-exclude .cache --reload-exclude frontend/node_modules --reload-exclude frontend/dist
else
  pip install -q -r backend/requirements.txt
  export PYTHONPATH="$ROOT_DIR"
  exec "$PY" -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload --reload-dir backend --reload-exclude .cache --reload-exclude frontend/node_modules --reload-exclude frontend/dist
fi
