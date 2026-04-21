#!/system/bin/sh
# Stop all sshd processes on the device (requires root via su).
set -eu

PREFIX=/data/data/com.termux/files/usr
PIDFILE="$PREFIX/var/run/sshd-magisk.pid"

su -c '
  # Kill by pidfile first
  if [ -f "'"$PIDFILE"'" ]; then
    pid="$(cat "'"$PIDFILE"'" 2>/dev/null)"
    [ -n "$pid" ] && kill "$pid" 2>/dev/null && echo "Killed sshd (pid $pid) from pidfile"
    rm -f "'"$PIDFILE"'"
  fi

  # Kill any remaining sshd processes
  pkill -x sshd 2>/dev/null && echo "Killed remaining sshd processes" || true
  sleep 1

  # Verify
  if pgrep -x sshd >/dev/null 2>&1; then
    echo "WARNING: sshd still running, sending SIGKILL"
    pkill -9 -x sshd 2>/dev/null || true
  fi

  pgrep -x sshd >/dev/null 2>&1 && echo "ERROR: sshd could not be stopped" || echo "All sshd stopped"
'
