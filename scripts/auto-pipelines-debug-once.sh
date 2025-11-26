#!/usr/bin/env bash
set -euo pipefail

# Run once: collect + AI evaluate, then execute pipelines with debug_enabled=1.

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-python}"
ENV_NAME="news-collector"

# Timestamped logging (Asia/Shanghai) to log/<ts>-auto-debug-once-log.txt
TS="$(TZ='Asia/Shanghai' date '+%Y%m%d-%H%M%S')"
LOG_DIR="$ROOT_DIR/log"
LOG_FILE="$LOG_DIR/${TS}-auto-debug-once-log.txt"
mkdir -p "$LOG_DIR"
exec > >(tee -a "$LOG_FILE") 2>&1
echo "[INFO] Log file: $LOG_FILE"

check_pipeline_config_debug() {
  local db_path="$ROOT_DIR/data/info.db"
  echo "[INFO] Checking pipeline DB configuration at $db_path (debug mode)" >&2
  "$PYTHON" - "$db_path" <<'PY'
import sqlite3
import sys
from pathlib import Path

def fail(msg: str, code: int = 1) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(code)

if len(sys.argv) < 2:
    fail("Missing DB path argument")

db_path = Path(sys.argv[1])
if not db_path.exists():
    fail(f"Pipeline database not found: {db_path}")

try:
    conn = sqlite3.connect(str(db_path))
except Exception as exc:
    fail(f"Failed to open pipeline database: {exc}")

with conn:
    cur = conn.cursor()
    has_pipelines = cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='pipelines'"
    ).fetchone()
    if not has_pipelines:
        fail("Pipeline DB missing pipelines table", code=2)

    # Ensure debug_enabled column exists; add if missing
    cur.execute("PRAGMA table_info(pipelines)")
    pcols = {row[1] for row in cur.fetchall()}
    if "debug_enabled" not in pcols:
        try:
            cur.execute("ALTER TABLE pipelines ADD COLUMN debug_enabled INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass

    try:
        pipelines = cur.execute(
            "SELECT id, name FROM pipelines WHERE COALESCE(debug_enabled,0)=1 ORDER BY id"
        ).fetchall()
    except sqlite3.OperationalError as exc:
        fail(f"DB missing debug_enabled column: {exc}", code=6)

    if not pipelines:
        fail("No debug pipelines found (set debug_enabled=1 to run).", code=3)

print("[INFO] Pipeline DB configuration for debug looks good.", file=sys.stderr)
PY
}

activate_runtime() {
  if command -v "$PYTHON" >/dev/null 2>&1; then
    return 0
  fi

  if ! command -v conda >/dev/null 2>&1; then
    for base in "$HOME/miniconda3" "$HOME/anaconda3" "/opt/conda" "/root/anaconda3"; do
      if [ -f "$base/etc/profile.d/conda.sh" ]; then
        # shellcheck source=/dev/null
        source "$base/etc/profile.d/conda.sh"
        break
      fi
    done
  fi
  if command -v conda >/dev/null 2>&1; then
    eval "$(conda shell.bash hook)"
    if conda activate "$ENV_NAME" >/dev/null 2>&1; then
      local found
      found="$(command -v python3 2>/dev/null || command -v python 2>/dev/null)"
      if [ -n "$found" ]; then
        PYTHON="$found"
        return 0
      fi
    fi
  fi

  if [ -f "$ROOT_DIR/.venv/bin/activate" ]; then
    # shellcheck source=/dev/null
    source "$ROOT_DIR/.venv/bin/activate"
    if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
      PYTHON="$ROOT_DIR/.venv/bin/python"
      return 0
    fi
    local venv_py
    venv_py="$(command -v python3 2>/dev/null || command -v python 2>/dev/null)"
    if [ -n "$venv_py" ]; then
      PYTHON="$venv_py"
      return 0
    fi
  fi

  if command -v python3 >/dev/null 2>&1; then
    PYTHON="python3"
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    PYTHON="python"
    return 0
  fi

  echo "[ERROR] Unable to locate a Python interpreter; set PYTHON env var or install Python." >&2
  return 1
}

run_once() {
  if ! activate_runtime; then
    return 1
  fi
  echo "[INFO] Using python interpreter: $PYTHON" >&2

  echo "[INFO] Applying AI metrics migration (idempotent)..." >&2
  $PYTHON "$ROOT_DIR/scripts/migrations/202510_ai_metrics_refactor.py" --db "$ROOT_DIR/data/info.db" || true

  check_pipeline_config_debug

  echo "[INFO] Collecting latest into SQLite..." >&2
  $PYTHON "$ROOT_DIR/news-collector/collector/collect_to_sqlite.py"

  echo "[INFO] Running AI evaluation for recent 72h..." >&2
  $PYTHON "$ROOT_DIR/news-collector/evaluator/ai_evaluate.py" --hours 72 --limit 400 || true

  echo "[INFO] Running debug pipelines sequentially..." >&2
  $PYTHON "$ROOT_DIR/news-collector/write-deliver-pipeline/pipeline_runner.py" --all --debug-only
}

run_once || true
