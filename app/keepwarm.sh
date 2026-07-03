#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"
source "./.env"

: "${KEEPWARM:=1}"
: "${KEEPWARM_START:=22}"
: "${KEEPWARM_END:=6}"
: "${KEEPWARM_MIN_C:=15}"
: "${KEEPWARM_MAX_C:=30}"
: "${LOG_DIR:=$HOME/.local/state/androidboot/logs}"

KEEPWARM_LOG="$LOG_DIR/keepwarm.log"
mkdir -p "$LOG_DIR"

LOAD_PIDS=()
STATE="init"

log_state() {
  printf '%s %s\n' "$(date -Is)" "$*" >>"$KEEPWARM_LOG"
}

kill_load() {
  local pid
  for pid in "${LOAD_PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  LOAD_PIDS=()
  # Safety net: reap any stray load processes from a previous crashed run.
  pkill -f 'sha256sum /dev/zero' 2>/dev/null || true
}

start_load() {
  kill_load
  # sha256sum on /dev/zero never terminates on its own; spawn it directly so
  # kill_load kills the actual load process (a subshell wrapper would leave
  # the child running forever after kill).
  sha256sum /dev/zero >/dev/null 2>&1 &
  LOAD_PIDS+=("$!")
  sha256sum /dev/zero >/dev/null 2>&1 &
  LOAD_PIDS+=("$!")
}

local_hour() {
  date +%H | sed 's/^0//'
}

in_window() {
  local hour="${1:-$(local_hour)}"
  [ -z "$hour" ] && hour=0
  if [ "$KEEPWARM_START" -gt "$KEEPWARM_END" ]; then
    [ "$hour" -ge "$KEEPWARM_START" ] || [ "$hour" -lt "$KEEPWARM_END" ]
  else
    [ "$hour" -ge "$KEEPWARM_START" ] && [ "$hour" -lt "$KEEPWARM_END" ]
  fi
}

battery_temp_c() {
  termux-battery-status 2>/dev/null | jq -r '.temperature // empty'
}

temp_lt() {
  awk -v a="$1" -v b="$2" 'BEGIN { exit !(a < b) }'
}

temp_ge() {
  awk -v a="$1" -v b="$2" 'BEGIN { exit !(a >= b) }'
}

set_state() {
  local next="$1"
  if [ "$STATE" != "$next" ]; then
    log_state "$next"
    STATE="$next"
  fi
}

termux-wake-lock || true

while :; do
  if [ "$KEEPWARM" != "1" ]; then
    set_state "disabled"
    kill_load
    sleep 60
    continue
  fi

  if ! in_window; then
    set_state "outside_window"
    kill_load
    sleep 60
    continue
  fi

  temp="$(battery_temp_c)"
  if [ -z "$temp" ]; then
    set_state "temp_unavailable"
    kill_load
    sleep 60
    continue
  fi

  if temp_ge "$temp" "$KEEPWARM_MAX_C"; then
    set_state "too_hot temp=${temp}C max=${KEEPWARM_MAX_C}C"
    kill_load
    sleep 60
    continue
  fi

  if temp_lt "$temp" "$KEEPWARM_MIN_C"; then
    set_state "warming temp=${temp}C min=${KEEPWARM_MIN_C}C"
    start_load
    sleep 30
    kill_load
    sleep 30
    continue
  fi

  set_state "warm_enough temp=${temp}C"
  kill_load
  sleep 60
done
