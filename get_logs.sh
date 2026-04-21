#!/data/data/com.termux/files/usr/bin/bash
# Dump the latest logs from all services for quick debugging.
set -eu

cd "$(dirname "$0")"
source "./.env"

TAIL_LINES="${1:-50}"
SEP="$(printf '=%.0s' {1..60})"

section() {
  printf '\n%s\n  %s\n%s\n' "$SEP" "$1" "$SEP"
}

tail_if_exists() {
  if [ -f "$1" ]; then
    tail -n "$TAIL_LINES" "$1"
  else
    echo "(not found: $1)"
  fi
}

# --- Tmux sessions (are things even running?) ---
section "Tmux sessions"
tmux ls 2>/dev/null || echo "(no tmux sessions)"

# --- Uploader (Supabase uploads) ---
section "Uploader  ($LOG_DIR/uploader.log)"
tail_if_exists "$LOG_DIR/uploader.log"

# --- Capture (camera) ---
section "Capture  ($LOG_DIR/capture.log)"
tail_if_exists "$LOG_DIR/capture.log"

# --- Metrics API ---
section "Metrics API  ($LOG_DIR/metrics-api.log)"
tail_if_exists "$LOG_DIR/metrics-api.log"

# --- Watchdog ---
section "Watchdog  ($LOG_DIR/watchdog.log)"
tail_if_exists "$LOG_DIR/watchdog.log"

# --- Errors (unsent) ---
section "Errors  ($ERROR_DIR)"
if [ -d "$ERROR_DIR" ]; then
  count=$(find "$ERROR_DIR" -maxdepth 1 -name '*.json' 2>/dev/null | wc -l)
  echo "$count unsent error(s)"
  find "$ERROR_DIR" -maxdepth 1 -name '*.json' -print0 2>/dev/null \
    | xargs -0 ls -1t 2>/dev/null \
    | head -10 \
    | while read -r f; do
        printf '\n--- %s ---\n' "$(basename "$f")"
        cat "$f"
      done
else
  echo "(not found: $ERROR_DIR)"
fi

# --- SSHD keepalive (Magisk service) ---
section "SSHD keepalive  (/data/local/tmp/sshd-keepalive.log)"
su -c "tail -n $TAIL_LINES /data/local/tmp/sshd-keepalive.log 2>/dev/null" || echo "(not readable or not found)"

# --- SSHD Magisk log ---
SSHD_LOG="/data/data/com.termux/files/home/.ssh/sshd-magisk.log"
section "SSHD Magisk  ($SSHD_LOG)"
tail_if_exists "$SSHD_LOG"

# --- Logcat (recent errors/warnings) ---
section "Logcat  (recent errors & warnings)"
logcat -d -t "$TAIL_LINES" '*:W' 2>/dev/null || echo "(logcat not available)"

# --- dmesg (kernel — needs root) ---
section "dmesg  (kernel, last $TAIL_LINES lines)"
su -c "dmesg | tail -n $TAIL_LINES" 2>/dev/null || echo "(dmesg not available)"
