#!/usr/bin/env bash
set -euo pipefail

# Ingest (collect + evaluate) then run all DB-backed pipelines sequentially.
# Sleep until the next 09:30 Beijing time (Asia/Shanghai, UTC+8) and repeat.

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-python}"
ENV_NAME="news-collector"

# Setup timestamped logging: mirror all output to console and log file
# Use Beijing time (Asia/Shanghai) for the timestamp
TS="$(TZ='Asia/Shanghai' date '+%Y%m%d-%H%M%S')"
LOG_DIR="$ROOT_DIR/log"
LOG_FILE="$LOG_DIR/${TS}-auto-930-log.txt"
mkdir -p "$LOG_DIR"
# Route both stdout and stderr through tee, appending to the log file
if command -v stdbuf >/dev/null 2>&1; then
  exec > >(stdbuf -oL -eL tee -a "$LOG_FILE") 2>&1
else
  exec > >(tee -a "$LOG_FILE") 2>&1
fi
echo "[INFO] Log file: $LOG_FILE"
export PYTHONUNBUFFERED=1

check_pipeline_config() {
  local db_path="$ROOT_DIR/data/info.db"
  echo "[INFO] Checking pipeline DB configuration at $db_path" >&2
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
    required_tables = (
        "pipelines",
        "pipeline_filters",
        "pipeline_writers",
        "pipeline_deliveries_email",
        "pipeline_deliveries_feishu",
        "pipeline_writer_metric_weights",
        "ai_metrics",
        "info_ai_scores",
        "info_ai_review",
    )
    missing_tables = []
    for tbl in required_tables:
        row = cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (tbl,),
        ).fetchone()
        if not row:
            missing_tables.append(tbl)
    if missing_tables:
        fail("Pipeline DB missing tables: " + ", ".join(missing_tables), code=2)

    cur.execute("PRAGMA table_info(pipeline_writers)")
    writer_cols = {row[1] for row in cur.fetchall()}
    required_writer_cols = ("limit_per_category", "per_source_cap")
    missing_writer_cols = [col for col in required_writer_cols if col not in writer_cols]
    if missing_writer_cols:
        fail("Pipeline writers table missing columns: " + ", ".join(missing_writer_cols), code=5)

    pipelines = cur.execute(
        "SELECT id, name FROM pipelines WHERE enabled=1 ORDER BY id"
    ).fetchall()
    if not pipelines:
        fail("No enabled pipelines found; import configurations before running.", code=3)

    missing_configs: list[str] = []
    for pid, name in pipelines:
        has_writer = cur.execute(
            "SELECT 1 FROM pipeline_writers WHERE pipeline_id=?",
            (pid,),
        ).fetchone()
        if not has_writer:
            missing_configs.append(f"{name} (writer)")
            continue
        has_email = cur.execute(
            "SELECT 1 FROM pipeline_deliveries_email WHERE pipeline_id=?",
            (pid,),
        ).fetchone()
        has_feishu = cur.execute(
            "SELECT 1 FROM pipeline_deliveries_feishu WHERE pipeline_id=?",
            (pid,),
        ).fetchone()
        if not (has_email or has_feishu):
            missing_configs.append(f"{name} (delivery)")

    if missing_configs:
        print("[ERROR] Pipelines missing writer/delivery configuration:", file=sys.stderr)
        for item in missing_configs:
            print(f"  - {item}", file=sys.stderr)
        print("Import or configure pipelines before rerunning.", file=sys.stderr)
        sys.exit(4)

print("[INFO] Pipeline DB configuration looks good.", file=sys.stderr)
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

sleep_until_next_0930() {
  # Use Beijing time regardless of server local TZ
  local TZ_BEIJING="Asia/Shanghai"
  local now_epoch today_target target_epoch label sleep_secs

  now_epoch=$(date +%s)
  today_target=$(TZ="$TZ_BEIJING" date -d "today 09:30" +%s)

  if [ "$now_epoch" -lt "$today_target" ]; then
    target_epoch="$today_target"
    label="Beijing today 09:30"
  else
    target_epoch=$(TZ="$TZ_BEIJING" date -d "tomorrow 09:30" +%s)
    label="Beijing tomorrow 09:30"
  fi

  sleep_secs=$(( target_epoch - now_epoch ))
  if [ "$sleep_secs" -lt 0 ]; then
    sleep_secs=0
  fi
  # Show the absolute target time in server local TZ for convenience
  echo "[INFO] Sleeping until $label ($(date -d @"$target_epoch" '+%F %T %Z'); ${sleep_secs}s)" >&2
  if [ "$sleep_secs" -gt 0 ]; then
    sleep "$sleep_secs"
  fi
}

cleanup_old_outputs() {
  local output_dir="$ROOT_DIR/data/output"
  local ttl_days=7
  local removed=0
  local info_db="$ROOT_DIR/data/info.db"

  if [ ! -d "$output_dir" ]; then
    echo "[INFO] Output directory not found at $output_dir; skipping cleanup." >&2
    return 0
  fi

  # Safety guard: never touch the pipeline database.
  if [ -f "$info_db" ] && [ ! -O "$info_db" ]; then
    echo "[WARN] info.db ownership unexpected; skipping cleanup to be safe." >&2
    return 0
  fi

  echo "[INFO] Cleaning output files older than ${ttl_days} days in $output_dir" >&2
  while IFS= read -r file; do
    if [ "$file" = "$info_db" ]; then
      echo "[WARN] Skipping protected file: $file" >&2
      continue
    fi
    if rm -- "$file"; then
      echo "[INFO] Removed stale file: $file" >&2
      removed=$((removed + 1))
    else
      echo "[WARN] Failed to remove stale file: $file" >&2
    fi
  done < <(find "$output_dir" -type f -mtime +"$ttl_days" -print 2>/dev/null)

  if [ "$removed" -gt 0 ]; then
    echo "[INFO] Removed $removed stale file(s) from $output_dir" >&2
  else
    echo "[INFO] No stale output files found (older than ${ttl_days} days)." >&2
  fi
}

cleanup_old_temp() {
  local temp_dir="$ROOT_DIR/data/temp"
  local ttl_days=7
  local removed=0

  if [ ! -d "$temp_dir" ]; then
    echo "[INFO] Temp directory not found at $temp_dir; skipping cleanup." >&2
    return 0
  fi

  echo "[INFO] Cleaning temp files older than ${ttl_days} days in $temp_dir (ctime)" >&2
  while IFS= read -r file; do
    if rm -- "$file"; then
      echo "[INFO] Removed stale temp file: $file" >&2
      removed=$((removed + 1))
    else
      echo "[WARN] Failed to remove stale temp file: $file" >&2
    fi
  done < <(find "$temp_dir" -type f -ctime +"$ttl_days" -print 2>/dev/null)

  if [ "$removed" -gt 0 ]; then
    echo "[INFO] Removed $removed stale temp file(s) from $temp_dir" >&2
  else
    echo "[INFO] No stale temp files found (older than ${ttl_days} days)." >&2
  fi
}

run_once() {
  if ! activate_runtime; then
    return 1
  fi
  echo "[INFO] Using python interpreter: $PYTHON" >&2

  echo "[INFO] Applying AI metrics migration (idempotent)..." >&2
  $PYTHON "$ROOT_DIR/scripts/migrations/202510_ai_metrics_refactor.py" --db "$ROOT_DIR/data/info.db" || true
  echo "[INFO] Applying pipeline refactor migration (idempotent)..." >&2
  $PYTHON "$ROOT_DIR/scripts/migrations/pipeline_refactor.py" --db "$ROOT_DIR/data/info.db" || true

  check_pipeline_config

  echo "[INFO] Running orchestrator (collect→evaluate→write→deliver) for all pipelines..." >&2
  $PYTHON "$ROOT_DIR/news-collector/write-deliver-pipeline/pipeline_runner.py" --all
}

while true; do
  sleep_until_next_0930
  run_once || true
  cleanup_old_outputs || true
  cleanup_old_temp || true
done
