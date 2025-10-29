#!/usr/bin/env bash
set -euo pipefail

# Legacy helper: collect + evaluate + run all pipelines via DB runner; repeat daily at 10:30.

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="python"
ENV_NAME="news-collector"
OUT_DIR="$ROOT_DIR/data/output"
mkdir -p "$OUT_DIR"

activate_conda() {
  # Ensure conda command available; try common installation paths.
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
    echo "[ERROR] conda command not found; please ensure Conda is installed and on PATH" >&2
    return 1
  fi

  eval "$(conda shell.bash hook)"
  conda activate "$ENV_NAME"
}

run_once() {
  echo "[INFO] Activating conda env: $ENV_NAME" >&2
  activate_conda

  echo "[INFO] Collecting latest into SQLite..." >&2
  $PYTHON "$ROOT_DIR/news-collector/collector/collect_to_sqlite.py"

  echo "[INFO] Running AI evaluation for recent 40h..." >&2
  $PYTHON "$ROOT_DIR/news-collector/evaluator/ai_evaluate.py" --hours 40 --limit 400 || true

  echo "[INFO] Ensuring pipeline schema and defaults (idempotent)" >&2
  $PYTHON "$ROOT_DIR/news-collector/write-deliver-pipeline/pipeline_admin.py" init
  $PYTHON "$ROOT_DIR/news-collector/write-deliver-pipeline/pipeline_admin.py" seed || true

  echo "[INFO] Running all DB pipelines via pipeline_runner" >&2
  $PYTHON "$ROOT_DIR/news-collector/write-deliver-pipeline/pipeline_runner.py" --all
}

sleep_until_1030_tomorrow() {
  # Compute next 10:30 local time tomorrow
  target_epoch=$(date -d "tomorrow 10:30" +%s)
  now_epoch=$(date +%s)
  sleep_secs=$(( target_epoch - now_epoch ))
  if [ "$sleep_secs" -le 0 ]; then
    # Fallback: sleep 24h
    sleep_secs=$(( 24*3600 ))
  fi
  echo "[INFO] Sleeping until tomorrow 10:30 ("$sleep_secs"s)" >&2
  sleep "$sleep_secs"
}

while true; do
  run_once || true
  sleep_until_1030_tomorrow
done
