#!/data/data/com.termux/files/usr/bin/bash

termux-notification --id 42042 --title "Phone Watchdog" --content "running" --ongoing || true
termux-wake-lock || true

set -euo pipefail
cd "$(dirname "$0")"
source ../../.env

# export ENV for children
set -a
source ../../.env
set +a

mkdir -p "$LOG_DIR"
export PYTHONPATH="$REPO_DIR"

# Session 1: 
if ! tmux has-session -t metrics 2>/dev/null; then
  tmux new-session -d -s metrics "while :; do python metrics.py >>'$LOG_DIR/metrics-api.log' 2>&1; echo 'metrics-api crashed, restarting in 2s' >>'$LOG_DIR/metrics-api.log'; sleep 2; done"
fi

# Session 2: analyzer
if ! tmux has-session -t uploader 2>/dev/null; then
  tmux new-session -d -s analyzer "while :; do python analyzer.py >>'$LOG_DIR/uploader.log' 2>&1; echo 'uploader crashed, restarting in 5s' >>'$LOG_DIR/uploader.log'; sleep 5; done"
fi

# Session 3: watchdog
if ! tmux has-session -t watchdog 2>/dev/null; then
  tmux new-session -d -s watchdog "while :; do ../../watchdog.sh >>'$LOG_DIR/watchdog.log' 2>&1; echo 'watchdog exited, restarting in 5s' >>'$LOG_DIR/watchdog.log'; sleep 5; done"
fi

echo "tmux sessions running:"
tmux ls
