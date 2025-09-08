#!/data/data/com.termux/files/usr/bin/bash
# Keeps (every minute): hotspot (main) OR connected Wi-Fi (support), sshd, tailscale.
set -euo pipefail

cd "$(dirname "$0")"
source "./.env"

mkdir -p "$STATE_DIR" "$LOG_DIR" "$ERROR_DIR" "$SENT_ERROR_DIR" "$TMPDIR"

log()   { printf '%s %s\n' "$(date -Is)" "$*" | tee -a "$LOG_DIR/watchdog.log" ; }
jerr()  { # type, reason
  ts="$(date -Is)"
  f="$ERROR_DIR/${ts//:/-}_$1.json"
  printf '{"ts":"%s","type":"%s","reason":%s}\n' "$ts" "$1" "$(jq -Rn --arg s "$2" '$s')" > "$f"
  log "ERROR [$1] $2"
}

has_cmd(){ command -v "$1" >/dev/null 2>&1 ; }

ensure_sshd() {
  if ! pgrep -x sshd >/dev/null 2>&1; then
    log "Starting sshd..."
    nohup sshd >/dev/null 2>&1 || true
    sleep 1
    if ! pgrep -x sshd >/devnull 2>&1; then
      jerr "sshd" "failed to start sshd"
    else
      log "sshd OK"
    fi
  fi
}

ensure_tailscale() {
  # prefer userspace net if no magisk tun; requires root for tailscaled best results
  if ! pgrep -x tailscaled >/dev/null 2>&1; then
    log "Starting tailscaled..."
    # try root
    if has_cmd su; then
      su -c "nohup tailscaled --tun=autoselect </dev/null >/dev/null 2>&1 &"
      sleep 1
    else
      nohup tailscaled --tun=userspace-networking </dev/null >/dev/null 2>&1 &
      sleep 1
    fi
  fi
  # Bring TS up if not logged in
  if tailscale status >/dev/null 2>&1; then
    :
  else
    if [ -n "${TS_AUTHKEY:-}" ]; then
      log "tailscale up with authkey"
      tailscale up --authkey "$TS_AUTHKEY" $TS_ARGS || jerr "tailscale" "tailscale up failed"
    else
      jerr "tailscale" "not logged in and no TS_AUTHKEY provided"
    fi
  fi
}

# ---- Hotspot (main) or Wi-Fi (support) ----

is_hotspot_on() {
  # Try Android 11+ dumpsys tethering
  if su -c 'dumpsys connectivity tethering' >/dev/null 2>&1; then
    out="$(su -c 'dumpsys connectivity tethering' 2>/dev/null || true)"
    echo "$out" | grep -qiE 'Tethered.*wlan|SoftAp.*started|WiFi.*tether.*ENABLED' && return 0
  fi
  # Legacy
  if su -c 'dumpsys tethering' >/dev/null 2>&1; then
    out="$(su -c 'dumpsys tethering' 2>/dev/null || true)"
    echo "$out" | grep -qiE 'Tethered.*wlan|SoftAp.*started' && return 0
  fi
  return 1
}

enable_hotspot() {
  log "Enabling hotspot..."
  # Try connective cmd
  if su -c 'cmd connectivity tether start' >/dev/null 2>&1; then
    su -c 'cmd connectivity tether start' || true
    sleep 2
    is_hotspot_on && return 0
  fi
  # Try wifi softap
  if su -c 'cmd wifi set-softap-enabled enabled' >/dev/null 2>&1; then
    su -c 'cmd wifi set-softap-enabled enabled' || true
    sleep 2
    is_hotspot_on && return 0
  fi
  # Try legacy "ndc softap" (older devices)
  if su -c 'ndc softap startap' >/dev/null 2>&1; then
    su -c 'ndc softap startap' || true
    sleep 2
    is_hotspot_on && return 0
  fi
  jerr "hotspot" "could not enable hotspot via known commands"
  return 1
}

wifi_ssid() {
  if termux-wifi-connectioninfo >/dev/null 2>&1; then
    termux-wifi-connectioninfo | jq -r '.ssid // empty'
  else
    echo ""
  fi
}

connect_wifi() {
  # Requires root + Android 11+ "cmd wifi connect-network"
  if [ -z "${MAIN_SSID:-}" ] || [ -z "${MAIN_PSK:-}" ]; then
    jerr "wifi" "MAIN_SSID/MAIN_PSK not set"
    return 1
  fi
  log "Connecting Wi-Fi to $MAIN_SSID ..."
  if su -c 'cmd wifi help' >/dev/null 2>&1; then
    su -c "cmd wifi connect-network \"$MAIN_SSID\" \"$MAIN_PSK\"" || true
    sleep 5
  else
    jerr "wifi" "'cmd wifi' not available. Configure network once in Settings."
  fi
}

ensure_network_role() {
  if [ "${ROLE:-support}" = "main" ]; then
    if ! is_hotspot_on; then
      enable_hotspot || true
    fi
    is_hotspot_on || jerr "hotspot" "hotspot still OFF after attempt"
  else
    ssid="$(wifi_ssid)"
    if [ "$ssid" != "$MAIN_SSID" ]; then
      connect_wifi || true
      sleep 5
      ssid="$(wifi_ssid)"
      [ "$ssid" = "$MAIN_SSID" ] || jerr "wifi" "not connected to $MAIN_SSID (got: ${ssid:-none})"
    fi
  fi
}

# ---- main loop ----
log "Watchdog start role=${ROLE} every ${CHECK_INTERVAL}s"
termux-wake-lock || true

while :; do
  ensure_sshd
  ensure_tailscale
  ensure_network_role
  sleep "$CHECK_INTERVAL"
done
