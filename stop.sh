#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

# Cancel the periodic job FIRST. Otherwise termux-job-scheduler would relaunch
# start.sh (every ~15 min) right after we kill the tmux sessions below.
if command -v termux-job-scheduler >/dev/null 2>&1; then
  termux-job-scheduler --cancel --job-id 700 >/dev/null 2>&1 \
    && echo "Cancelled scheduler job 700." \
    || echo "Scheduler job 700 not active."
fi

# Stop the capture stack.
for session in metrics analyzer watchdog keepwarm; do
  if tmux has-session -t "$session" 2>/dev/null; then
    tmux kill-session -t "$session" 2>/dev/null || true
    echo "Stopped tmux session: $session"
  fi
done

# Kill the tmux server too if nothing else is left, so no stray loop survives.
if ! tmux ls >/dev/null 2>&1; then
  tmux kill-server 2>/dev/null || true
fi

# Release the ongoing notification and wake lock that start.sh set.
termux-notification-remove 42042 2>/dev/null || true
termux-wake-unlock 2>/dev/null || true

echo "Stopped. Update the repo, then run ./start.sh to bring it back up."
