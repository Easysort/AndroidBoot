#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

if [ ! -f "$REPO_DIR/.env" ]; then
  echo "ERROR: Missing .env in $REPO_DIR. Run ./setup.sh first."
  exit 1
fi

set -a
source "$REPO_DIR/.env"
set +a

: "${STATE_DIR:=$HOME/.local/state/androidboot}"
: "${LOG_DIR:=$STATE_DIR/logs}"
: "${ERROR_DIR:=$STATE_DIR/errors}"
: "${SENT_ERROR_DIR:=$ERROR_DIR/sent}"
: "${TMPDIR:=$HOME/.cache/androidboot}"

preflight() {
  if [ ! -s "$REPO_DIR/device_id.txt" ]; then
    echo "ERROR: Missing or empty device_id.txt in $REPO_DIR."
    exit 1
  fi

  if [ -z "${SUPABASE_URL:-}" ]; then
    echo "ERROR: SUPABASE_URL is not set in .env."
    exit 1
  fi

  if [ -z "${SUPABASE_ANON_KEY:-${SUPABASE_KEY:-}}" ]; then
    echo "ERROR: SUPABASE_ANON_KEY (or SUPABASE_KEY) is not set in .env."
    exit 1
  fi

  if ! command -v termux-camera-photo >/dev/null 2>&1; then
    echo "ERROR: termux-camera-photo is unavailable. Install and grant Termux:API camera permission."
    exit 1
  fi

  if ! command -v tmux >/dev/null 2>&1; then
    echo "ERROR: tmux is unavailable."
    exit 1
  fi
}

mkdir -p "$STATE_DIR" "$LOG_DIR" "$ERROR_DIR" "$SENT_ERROR_DIR" "$TMPDIR"

termux-notification --id 42042 --title "AndroidBoot" --content "running" --ongoing || true
termux-wake-lock || true

preflight

start_session() {
  local name="$1"
  local cmd="$2"
  local log_file="$3"
  local crash_note="$4"
  local sleep_s="$5"

  if ! tmux has-session -t "$name" 2>/dev/null; then
    tmux new-session -d -s "$name" "cd \"$REPO_DIR\" && while :; do $cmd >>\"$log_file\" 2>&1; echo \"$crash_note\" >>\"$log_file\"; sleep $sleep_s; done"
  fi
}

for session in metrics analyzer watchdog keepwarm; do
  case "$session" in
    metrics)
      cmd="python app/metrics.py"
      log_file="$LOG_DIR/metrics-api.log"
      crash_note="metrics crashed, restarting in 2s"
      sleep_s="2"
      ;;
    analyzer)
      cmd="python app/analyzer.py"
      log_file="$LOG_DIR/uploader.log"
      crash_note="analyzer crashed, restarting in 5s"
      sleep_s="5"
      ;;
    watchdog)
      cmd="./watchdog.sh"
      log_file="$LOG_DIR/watchdog.log"
      crash_note="watchdog exited, restarting in 5s"
      sleep_s="5"
      ;;
    keepwarm)
      cmd="./app/keepwarm.sh"
      log_file="$LOG_DIR/keepwarm.log"
      crash_note="keepwarm exited, restarting in 5s"
      sleep_s="5"
      ;;
  esac
  start_session "$session" "$cmd" "$log_file" "$crash_note" "$sleep_s"
done

if command -v termux-job-scheduler >/dev/null 2>&1; then
  jobs="$(termux-job-scheduler -p 2>/dev/null || true)"
  # Re-registering the same job id is a harmless replace if this match ever misses.
  if ! printf '%s\n' "$jobs" | grep -qw '700'; then
    termux-job-scheduler --job-id 700 --script "$REPO_DIR/start.sh" --period-ms 900000 --persisted true --network any
  fi
fi

echo "tmux sessions running:"
tmux ls
