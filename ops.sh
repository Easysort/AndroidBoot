#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

if [ -f "$REPO_DIR/.env" ]; then
  set -a
  source "$REPO_DIR/.env"
  set +a
fi

: "${STATE_DIR:=$HOME/.local/state/androidboot}"
: "${LOG_DIR:=$STATE_DIR/logs}"
: "${ERROR_DIR:=$STATE_DIR/errors}"

TAIL_LINES="${2:-50}"
SEP="$(printf '=%.0s' {1..60})"

section() {
  printf '\n%s\n  %s\n%s\n' "$SEP" "$1" "$SEP"
}

tail_if_exists() {
  local path="$1"
  if [ -f "$path" ]; then
    tail -n "$TAIL_LINES" "$path"
  else
    echo "(not found: $path)"
  fi
}

status_cmd() {
  section "Tmux sessions"
  tmux ls 2>/dev/null || echo "(no tmux sessions)"

  section "Processes"
  pgrep -a sshd || echo "sshd: not running"
  pgrep -a tailscaled || echo "tailscaled: not running"

  section "Disk"
  df -h "$HOME" || true

  section "Battery"
  termux-battery-status 2>/dev/null || echo "(termux-battery-status unavailable)"
}

logs_cmd() {
  section "Tmux sessions"
  tmux ls 2>/dev/null || echo "(no tmux sessions)"

  section "Uploader  ($LOG_DIR/uploader.log)"
  tail_if_exists "$LOG_DIR/uploader.log"

  section "Metrics API  ($LOG_DIR/metrics-api.log)"
  tail_if_exists "$LOG_DIR/metrics-api.log"

  section "Watchdog  ($LOG_DIR/watchdog.log)"
  tail_if_exists "$LOG_DIR/watchdog.log"

  section "Keep-warm  ($LOG_DIR/keepwarm.log)"
  tail_if_exists "$LOG_DIR/keepwarm.log"

  section "Blackbox  ($LOG_DIR/blackbox.log)"
  tail_if_exists "$LOG_DIR/blackbox.log"

  section "Errors  ($ERROR_DIR)"
  if [ -d "$ERROR_DIR" ]; then
    find "$ERROR_DIR" -maxdepth 1 -name '*.json' 2>/dev/null | sort | tail -10 | while read -r f; do
      printf '\n--- %s ---\n' "$(basename "$f")"
      cat "$f"
    done || true
  else
    echo "(not found: $ERROR_DIR)"
  fi

  section "AndroidBoot boot  (/data/local/tmp/androidboot-boot.log)"
  su -c "tail -n $TAIL_LINES /data/local/tmp/androidboot-boot.log 2>/dev/null" || echo "(not readable or not found)"

  section "SSHD Magisk  ($HOME/.ssh/sshd-magisk.log)"
  tail_if_exists "$HOME/.ssh/sshd-magisk.log"

  section "Logcat  (recent warnings/errors)"
  logcat -d -t "$TAIL_LINES" '*:W' 2>/dev/null || echo "(logcat unavailable)"

  section "dmesg  (kernel tail)"
  su -c "dmesg | tail -n $TAIL_LINES" 2>/dev/null || echo "(dmesg unavailable)"
}

stop_cmd() {
  termux-job-scheduler --cancel --job-id 700 2>/dev/null || true
  tmux kill-server 2>/dev/null || true
  echo "Stopped job 700 and tmux server."
}

STOP_FLAG=/data/local/tmp/sshd-stop

stop_sshd_cmd() {
  PREFIX=/data/data/com.termux/files/usr
  PIDFILE="$PREFIX/var/run/sshd-magisk.pid"

  su -c '
    touch "'"$STOP_FLAG"'" && echo "Created sshd-stop flag (keepalive will not restart sshd)"

    if [ -f "'"$PIDFILE"'" ]; then
      pid="$(cat "'"$PIDFILE"'" 2>/dev/null)"
      [ -n "$pid" ] && kill "$pid" 2>/dev/null && echo "Killed sshd (pid $pid) from pidfile"
      rm -f "'"$PIDFILE"'"
    fi

    pkill -x sshd 2>/dev/null && echo "Killed remaining sshd processes" || true
    sleep 1
    if pgrep -x sshd >/dev/null 2>&1; then
      echo "WARNING: sshd still running, sending SIGKILL"
      pkill -9 -x sshd 2>/dev/null || true
    fi
    pgrep -x sshd >/dev/null 2>&1 && echo "ERROR: sshd could not be stopped" || echo "All sshd stopped"
  '
}

start_sshd_cmd() {
  su -c '
    rm -f "'"$STOP_FLAG"'" && echo "Removed sshd-stop flag"
    echo "Magisk keepalive will restart sshd within 5 minutes (or on next boot loop tick)"
  '
}

case "${1:-}" in
  status) status_cmd ;;
  logs) logs_cmd ;;
  stop) stop_cmd ;;
  stop-sshd) stop_sshd_cmd ;;
  start-sshd) start_sshd_cmd ;;
  *)
    echo "Usage: $0 {status|logs [n]|stop|stop-sshd|start-sshd}"
    exit 1
    ;;
esac
