#!/usr/bin/env bash
set -euo pipefail

# Auto-run collector, write 24h HTML, and mail it; wait each day until 09:30 first.

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

  ts="$(date +%y%m%d-%H%M%S)"
  out_file="$OUT_DIR/${ts}-40h-info.html"

  echo "[INFO] Writing 40h digest: $out_file" >&2
  $PYTHON "$ROOT_DIR/news-collector/writer/info_writer.py" --hours 40 --output "$out_file"

  # Generate Feishu message (24h top articles) and broadcast as card
  msg_dir="$ROOT_DIR/data/feishu-msg"
  mkdir -p "$msg_dir"
  feishu_msg_file="$msg_dir/$(date +%Y%m%d)-feishu-msg.md"
  echo "[INFO] Building Feishu message: $feishu_msg_file" >&2
  $PYTHON "$ROOT_DIR/news-collector/writer/feishu_writer.py" --hours 40 --output "$feishu_msg_file" || true

  if [ -f "$feishu_msg_file" ]; then
    echo "[INFO] Broadcasting Feishu message to all groups" >&2
    $PYTHON "$ROOT_DIR/news-collector/deliver/feishu_bot_today.py" \
      --to-all \
      --file "$feishu_msg_file" \
      --as-card \
      --title "情报官今日推荐" || true
  else
    echo "[WARN] Feishu message file not found, skip broadcast: $feishu_msg_file" >&2
  fi

  subject="$(date +%Y年%m月%d日)整合"
  echo "[INFO] Mailing digest to 306483372@qq.com (subject: $subject)" >&2
  $PYTHON "$ROOT_DIR/news-collector/deliver/mail_today.py" \
    --html "$out_file" \
    --subject "$subject" \
    --sender "pangruitaosite@gmail.com" \
    --to "306483372@qq.com"
}

sleep_until_next_0930() {
  now_epoch=$(date +%s)
  today_target=$(date -d "today 09:30" +%s)

  if [ "$now_epoch" -lt "$today_target" ]; then
    target_epoch="$today_target"
    target_label="today 09:30"
  else
    target_epoch=$(date -d "tomorrow 09:30" +%s)
    target_label="tomorrow 09:30"
  fi

  sleep_secs=$(( target_epoch - now_epoch ))
  if [ "$sleep_secs" -lt 0 ]; then
    sleep_secs=0
  fi

  echo "[INFO] Sleeping until $target_label (${sleep_secs}s)" >&2
  if [ "$sleep_secs" -gt 0 ]; then
    sleep "$sleep_secs"
  fi
}

while true; do
  sleep_until_next_0930
  run_once || true
done
