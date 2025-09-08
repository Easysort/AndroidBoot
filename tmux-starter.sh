#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
source "./phone.env"

# export ENV for children
set -a
source ./phone.env
set +a

mkdir -p "$LOG_DIR"

# Session 1: metrics API (auto-restart loop)
if ! tmux has-session -t metrics 2>/dev/null; then
  tmux new-session -d -s metrics "while :; do ./api/metrics-api >>'$LOG_DIR/metrics-api.log' 2>&1; echo 'metrics-api crashed, restarting in 2s' >>'$LOG_DIR/metrics-api.log'; sleep 2; done"
fi

# Session 2: uploader
if ! tmux has-session -t uploader 2>/dev/null; then
  tmux new-session -d -s uploader "while :; do ./uploader.py -s ${CHECK_INTERVAL} -c ${CAMERA_ID} >>'$LOG_DIR/uploader.log' 2>&1; echo 'uploader crashed, restarting in 5s' >>'$LOG_DIR/uploader.log'; sleep 5; done"
fi

# Session 3: watchdog
if ! tmux has-session -t watchdog 2>/dev/null; then
  tmux new-session -d -s watchdog "while :; do ./watchdog.sh >>'$LOG_DIR/watchdog.log' 2>&1; echo 'watchdog exited, restarting in 5s' >>'$LOG_DIR/watchdog.log'; sleep 5; done"
fi

echo "tmux sessions running:"
tmux ls
