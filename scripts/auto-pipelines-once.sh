#!/usr/bin/env bash
set -euo pipefail

# Ingest (collect + evaluate) then run all DB-backed pipelines sequentially.
# Sleep until next 09:30 and repeat.

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="python"
ENV_NAME="news-collector"

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

  # Ensure pipeline tables and seed default pipelines
  echo "[INFO] Ensuring pipeline schema and seed" >&2
  $PYTHON "$ROOT_DIR/news-collector/write-deliver-pipeline/pipeline_admin.py" init
  $PYTHON "$ROOT_DIR/news-collector/write-deliver-pipeline/pipeline_admin.py" seed || true

  echo "[INFO] Collecting latest into SQLite..." >&2
  $PYTHON "$ROOT_DIR/news-collector/collector/collect_to_sqlite.py"

  echo "[INFO] Running AI evaluation for recent 40h..." >&2
  $PYTHON "$ROOT_DIR/news-collector/evaluator/ai_evaluate.py" --hours 40 --limit 400 || true

  echo "[INFO] Running all pipelines sequentially..." >&2
  $PYTHON "$ROOT_DIR/news-collector/write-deliver-pipeline/pipeline_runner.py" --all
}


run_once || true


