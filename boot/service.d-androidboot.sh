#!/system/bin/sh
#
# ON-DEVICE VERIFICATION (operator must confirm after rollout)
# ------------------------------------------------------------
# V1 Root-cause check (do FIRST on a locked-out phone if one exists):
#    adb shell -> ps -A | grep sshd ; check uid ; cat $PREFIX/etc/ssh/sshd_config_root
#    Expected: one uid-0 sshd, PasswordAuthentication no, absolute AuthorizedKeysFile.
# V2 Login uid/shell: ssh in, run id -u, whoami, echo $HOME, echo $PREFIX.
#    Record whether session lands as termux uid or root and whether PATH/HOME are sane.
# V3 StrictModes: if V2 shows pubkey works, try StrictModes yes with chown on
#    ~/.ssh and authorized_keys to root:root (or matching sshd uid) and retest pubkey login.
#    Only keep StrictModes yes if pubkey auth still works; otherwise leave no.
# V4 ForceCommand: if V2 shows broken environment (wrong uid, missing PREFIX/PATH),
#    investigate Termux patched openssh ForceCommand / su wrapper BEFORE shipping one.
#    Acceptance test either way: ssh phone 'git -C ~/AndroidBoot pull' must succeed.
# V5 After am force-stop com.termux, wait 10 min, ssh again — must work key-only.
# V6 Reboot without opening Termux — ssh within 3 min must work key-only.
# V7 Password refused: ssh -o PreferredAuthentications=password phone must fail.
# V8 ADB break-glass: adb connect <tailscale-ip>:5555 then adb shell — note if OnePlus
#    resets service.adb.tcp.port after reboot (report if unavailable).
# V9 Tailscale: if the Magisk tailscale module is installed it runs its own tailscaled
#    with its own socket path; verify `tailscale status` from this script's context
#    talks to the right daemon (you may need TS_SOCKET / --socket to match the module).
# ------------------------------------------------------------
#
# Optional root-readable env: /data/local/tmp/androidboot.env
# One KEY=VALUE per line; # starts a comment. Supported keys:
#   TS_AUTHKEY=tskey-auth-...     reusable pre-authorized Tailscale auth key
#   NIGHTLY_REBOOT=1              1=enabled (default), 0=disabled
#   INTERVAL=300                  keepalive loop seconds (default 300)

LOG=/data/local/tmp/androidboot-boot.log
ENV_FILE=/data/local/tmp/androidboot.env
INTERVAL=300
STOP_FLAG=/data/local/tmp/sshd-stop
REBOOT_MARKER=/data/local/tmp/last-nightly-reboot

PREFIX=/data/data/com.termux/files/usr
HOME=/data/data/com.termux/files/home
PIDFILE="$PREFIX/var/run/sshd-magisk.pid"
SSHD_CONFIG="$PREFIX/etc/ssh/sshd_config_root"
OPS_KEYS="$HOME/AndroidBoot/keys/ops_authorized_keys"
AUTH_KEYS="$HOME/.ssh/authorized_keys"

export PREFIX HOME
export PATH="$PREFIX/bin:/system/bin:/system/xbin:/sbin"
export LD_LIBRARY_PATH="$PREFIX/lib"
export LANG=C.UTF-8

TS_AUTHKEY=""
NIGHTLY_REBOOT=1

timestamp() { date -Is; }

log() { echo "$(timestamp) $1" >> "$LOG"; }

have() { command -v "$1" >/dev/null 2>&1; }

load_env() {
  TS_AUTHKEY=""
  NIGHTLY_REBOOT=1
  if [ ! -f "$ENV_FILE" ]; then
    return 0
  fi
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      ''|\#*) continue ;;
    esac
    key="${line%%=*}"
    val="${line#*=}"
    case "$key" in
      TS_AUTHKEY) TS_AUTHKEY="$val" ;;
      NIGHTLY_REBOOT) NIGHTLY_REBOOT="$val" ;;
      INTERVAL)
        case "$val" in
          ''|*[!0-9]*) ;;
          *) INTERVAL="$val" ;;
        esac
        ;;
    esac
  done < "$ENV_FILE"
}

ensure_dirs() {
  mkdir -p "$PREFIX/var/run" "$PREFIX/etc/ssh" "$HOME/.ssh" 2>/dev/null
}

sync_ops_keys() {
  ensure_dirs
  chmod 700 "$HOME/.ssh" 2>/dev/null
  touch "$AUTH_KEYS" 2>/dev/null
  chmod 600 "$AUTH_KEYS" 2>/dev/null

  if [ ! -f "$OPS_KEYS" ]; then
    log "[warn] ops keys file missing: $OPS_KEYS"
    return 0
  fi

  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      ''|\#*) continue ;;
    esac
    if ! grep -qF "$line" "$AUTH_KEYS" 2>/dev/null; then
      echo "$line" >> "$AUTH_KEYS"
      log "[keys] appended ops key to authorized_keys"
    fi
  done < "$OPS_KEYS"
}

write_sshd_config() {
  cat > "$SSHD_CONFIG" <<'CFG'
Port 8022
Protocol 2
# This sshd runs as uid 0; Termux's patched openssh maps logins to the daemon uid,
# so "PermitRootLogin no" could reject ALL logins here. prohibit-password keeps
# password auth blocked while allowing key logins.
PermitRootLogin prohibit-password
PasswordAuthentication no
PubkeyAuthentication yes
AuthorizedKeysFile /data/data/com.termux/files/home/.ssh/authorized_keys
# Home is owned by the Termux uid, not root; default StrictModes rejects authorized_keys when sshd runs as uid 0.
StrictModes no
HostKey /data/data/com.termux/files/usr/etc/ssh/ssh_host_rsa_key
HostKey /data/data/com.termux/files/usr/etc/ssh/ssh_host_ecdsa_key
HostKey /data/data/com.termux/files/usr/etc/ssh/ssh_host_ed25519_key
Subsystem sftp /data/data/com.termux/files/usr/libexec/sftp-server
PidFile /data/data/com.termux/files/usr/var/run/sshd-magisk.pid
UsePAM no
PrintMotd no
PrintLastLog no
ClientAliveInterval 120
ClientAliveCountMax 3
CFG
  log "[cfg] wrote $SSHD_CONFIG"
}

ensure_host_keys() {
  if [ ! -s "$PREFIX/etc/ssh/ssh_host_rsa_key" ] && [ -x "$PREFIX/bin/ssh-keygen" ]; then
    log "[keys] generating host keys (first boot only)"
    "$PREFIX/bin/ssh-keygen" -A >>"$LOG" 2>&1
  fi
}

kill_port_8022() {
  if have pkill; then
    pkill -x sshd 2>/dev/null || true
    sleep 1
    pkill -9 -x sshd 2>/dev/null || true
  elif have pidof; then
    for pid in $(pidof sshd 2>/dev/null); do
      kill "$pid" 2>/dev/null || true
    done
    sleep 1
    for pid in $(pidof sshd 2>/dev/null); do
      kill -9 "$pid" 2>/dev/null || true
    done
  fi
  rm -f "$PIDFILE" 2>/dev/null
  log "[kill] cleared sshd on port 8022"
}

running() {
  if [ -f "$PIDFILE" ]; then
    pid="$(cat "$PIDFILE" 2>/dev/null)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
  fi
  if have pgrep; then
    pgrep -f "$PREFIX/bin/sshd" >/dev/null 2>&1 && return 0
  fi
  return 1
}

start_sshd() {
  ensure_dirs
  sync_ops_keys
  write_sshd_config
  ensure_host_keys
  kill_port_8022

  "$PREFIX/bin/sshd" -f "$SSHD_CONFIG" -E "$HOME/.ssh/sshd-magisk.log" >>"$LOG" 2>&1 || true
}

_adb_tcp() {
  setprop service.adb.tcp.port 5555 2>/dev/null || true
  stop adbd 2>/dev/null || true
  start adbd 2>/dev/null || true
}

enable_adb_tcp() {
  _adb_tcp
  log "[adb] enabled TCP port 5555 (adb connect <tailscale-ip>:5555)"
}

find_tailscaled_bin() {
  for candidate in \
    /data/adb/tailscale/tailscaled \
    /data/adb/tailscale/bin/tailscaled \
    /data/adb/modules/tailscale/system/bin/tailscaled; do
    if [ -x "$candidate" ]; then
      echo "$candidate"
      return 0
    fi
  done
  if have tailscaled; then
    command -v tailscaled
    return 0
  fi
  return 1
}

find_tailscale_bin() {
  for candidate in \
    /data/adb/tailscale/tailscale \
    /data/adb/tailscale/bin/tailscale \
    /data/adb/modules/tailscale/system/bin/tailscale; do
    if [ -x "$candidate" ]; then
      echo "$candidate"
      return 0
    fi
  done
  if have tailscale; then
    command -v tailscale
    return 0
  fi
  return 1
}

tailscaled_running() {
  if have pgrep; then
    pgrep -x tailscaled >/dev/null 2>&1 && return 0
  fi
  if have pidof; then
    pidof tailscaled >/dev/null 2>&1 && return 0
  fi
  return 1
}

tailscale_logged_in() {
  ts_bin="$1"
  "$ts_bin" status >/dev/null 2>&1
}

ensure_tailscale() {
  ts_bin=""
  tsd_bin=""
  if ! ts_bin="$(find_tailscale_bin)"; then
    log "[warn] tailscale CLI not found (checked /data/adb/tailscale/, PATH)"
    return 0
  fi
  if ! tsd_bin="$(find_tailscaled_bin)"; then
    log "[warn] tailscaled binary not found (checked /data/adb/tailscale/, PATH)"
    return 0
  fi

  if tailscaled_running; then
    :
  else
    log "[fix] starting tailscaled ($tsd_bin)..."
    # Bare fallback start; the Magisk tailscale module normally manages its own
    # daemon with its own state/socket paths. Default state dir does not exist
    # on Android, so pass one explicitly.
    "$tsd_bin" --state=/data/local/tmp/tailscaled.state >>"$LOG" 2>&1 &
    sleep 2
    if tailscaled_running; then
      log "[ok] tailscaled started"
    else
      log "[err] tailscaled failed to start"
      return 0
    fi
  fi

  if tailscale_logged_in "$ts_bin"; then
    if ! "$ts_bin" up >>"$LOG" 2>&1; then
      log "[warn] tailscale up failed (already logged in)"
    fi
  elif [ -n "$TS_AUTHKEY" ]; then
    if "$ts_bin" up --auth-key="$TS_AUTHKEY" >>"$LOG" 2>&1; then
      log "[ok] tailscale up with auth key"
    else
      log "[err] tailscale up with auth key failed"
    fi
  else
    log "[err] tailscale not logged in and TS_AUTHKEY unset in $ENV_FILE"
  fi
}

maybe_nightly_reboot() {
  if [ "$NIGHTLY_REBOOT" != "1" ]; then
    return 0
  fi

  hm="$(date +%H%M)"
  case "$hm" in
    040[0-9]|041[0-5]) ;;
    *) return 0 ;;
  esac

  today="$(date +%Y-%m-%d)"
  if [ -f "$REBOOT_MARKER" ]; then
    last="$(cat "$REBOOT_MARKER" 2>/dev/null)"
    if [ "$last" = "$today" ]; then
      return 0
    fi
  fi

  echo "$today" > "$REBOOT_MARKER"
  log "[reboot] nightly reboot scheduled (marker $today written before reboot)"
  svc power reboot 2>/dev/null || reboot
}

load_env
log "[start] androidboot service (interval ${INTERVAL}s, nightly_reboot=${NIGHTLY_REBOOT})"
enable_adb_tcp
ensure_tailscale

while true; do
  load_env

  if [ -f "$STOP_FLAG" ]; then
    log "[skip] sshd-stop flag set"
  elif running; then
    log "[ok] sshd running"
  else
    log "[fix] starting sshd..."
    start_sshd
    sleep 2
    if running; then
      log "[ok] sshd started"
    else
      log "[err] sshd failed to start"
    fi
  fi

  ensure_tailscale
  _adb_tcp
  maybe_nightly_reboot

  sleep "$INTERVAL"
done
