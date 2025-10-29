#!/usr/bin/env bash
set -euo pipefail

# Ingest (collect + evaluate) then run all DB-backed pipelines sequentially.
# Sleep until next 09:30 and repeat.

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="python"
ENV_NAME="news-collector"

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

activate_conda() {
  if ! command -v conda >/dev/null 2>&1; then
    for base in "$HOME/miniconda3" "$HOME/anaconda3" "/opt/conda" "/root/anaconda3"; do
      if [ -f "$base/etc/profile.d/conda.sh" ]; then
        # shellcheck source=/dev/null
        source "$base/etc/profile.d/conda.sh"
        break
      fi
    done
  fi
  if ! command -v conda >/dev/null 2>&1; then
    echo "[ERROR] conda command not found; ensure Conda is installed and on PATH" >&2
    return 1
  fi
  eval "$(conda shell.bash hook)"
  conda activate "$ENV_NAME"
}

sleep_until_next_0930() {
  now_epoch=$(date +%s)
  today_target=$(date -d "today 09:30" +%s)
  if [ "$now_epoch" -lt "$today_target" ]; then
    target_epoch="$today_target"
    label="today 09:30"
  else
    target_epoch=$(date -d "tomorrow 09:30" +%s)
    label="tomorrow 09:30"
  fi
  sleep_secs=$(( target_epoch - now_epoch ))
  if [ "$sleep_secs" -lt 0 ]; then
    sleep_secs=0
  fi
  echo "[INFO] Sleeping until $label (${sleep_secs}s)" >&2
  if [ "$sleep_secs" -gt 0 ]; then
    sleep "$sleep_secs"
  fi
}

run_once() {
  echo "[INFO] Activating conda env: $ENV_NAME" >&2
  activate_conda

  check_pipeline_config

  echo "[INFO] Collecting latest into SQLite..." >&2
  $PYTHON "$ROOT_DIR/news-collector/collector/collect_to_sqlite.py"

  echo "[INFO] Running AI evaluation for recent 40h..." >&2
  $PYTHON "$ROOT_DIR/news-collector/evaluator/ai_evaluate.py" --hours 40 --limit 400 || true

  echo "[INFO] Running all pipelines sequentially..." >&2
  $PYTHON "$ROOT_DIR/news-collector/write-deliver-pipeline/pipeline_runner.py" --all
}

while true; do
  sleep_until_next_0930
  run_once || true
done
