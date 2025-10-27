#!/usr/bin/env bash
set -euo pipefail

# Auto-run collector, write 24h HTML, and mail it; repeat daily at 10:30.

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="python"
ENV_NAME="news-collector"
OUT_DIR="$ROOT_DIR/data/output"
mkdir -p "$OUT_DIR"

activate_conda() {
  if [ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]; then
    # Common path
    # shellcheck source=/dev/null
    source "$HOME/miniconda3/etc/profile.d/conda.sh"
  elif command -v conda >/dev/null 2>&1; then
    # Fallback via hook
    eval "$(conda shell.bash hook)"
  fi
  conda activate "$ENV_NAME"
}

run_once() {
  echo "[INFO] Activating conda env: $ENV_NAME" >&2
  activate_conda

  echo "[INFO] Collecting latest into SQLite..." >&2
  $PYTHON "$ROOT_DIR/news-collector/manager/collect_to_sqlite.py"

  echo "[INFO] Running AI evaluation for recent 24h..." >&2
  $PYTHON "$ROOT_DIR/news-collector/manager/ai_evaluate.py" --hours 24 --limit 400 || true

  ts="$(date +%y%m%d-%H%M%S)"
  out_file="$OUT_DIR/${ts}-24h-info.html"

  echo "[INFO] Writing 24h digest: $out_file" >&2
  $PYTHON "$ROOT_DIR/news-collector/manager/info_writer.py" --hours 24 --output "$out_file"

  # Generate Feishu message (24h top articles) and broadcast as card
  msg_dir="$ROOT_DIR/data/feishu-msg"
  mkdir -p "$msg_dir"
  feishu_msg_file="$msg_dir/$(date +%Y%m%d)-feishu-msg.md"
  echo "[INFO] Building Feishu message: $feishu_msg_file" >&2
  $PYTHON "$ROOT_DIR/news-collector/manager/feishu_writer.py" --hours 24 --output "$feishu_msg_file" || true

  if [ -f "$feishu_msg_file" ]; then
    echo "[INFO] Broadcasting Feishu message to all groups" >&2
    $PYTHON "$ROOT_DIR/news-collector/manager/feishu_bot_today.py" \
      --to-all \
      --file "$feishu_msg_file" \
      --as-card \
      --title "24小时新文章" || true
  else
    echo "[WARN] Feishu message file not found, skip broadcast: $feishu_msg_file" >&2
  fi

  subject="$(date +%Y年%m月%d日)整合"
  echo "[INFO] Mailing digest to 306483372@qq.com (subject: $subject)" >&2
  $PYTHON "$ROOT_DIR/news-collector/manager/mail_today.py" \
    --html "$out_file" \
    --subject "$subject" \
    --sender "pangruitaosite@gmail.com" \
    --to "306483372@qq.com"
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
