#!/system/bin/sh
# Move to: /data/adb/service.d/sshd-keepalive.sh
# with:
# su
# mv 98-sshd-keepalive.sh /data/adb/service.d/sshd-keepalive.sh
# chmod 755 /data/adb/service.d/sshd-keepalive.sh
# 
# Magisk service.d: keep Termux sshd alive even if the Termux app is killed.
# Checks every 10 minutes (adjust INTERVAL env if desired).

LOG=/data/local/tmp/sshd-keepalive.log
INTERVAL=${INTERVAL:-600}  # seconds, default 10 min

PREFIX=/data/data/com.termux/files/usr
HOME=/data/data/com.termux/files/home
PIDFILE="$PREFIX/var/run/sshd-magisk.pid"

# Minimal Termux runtime env so termux binaries run from root context
export PREFIX HOME
export PATH="$PREFIX/bin:/system/bin:/system/xbin:/sbin"
export LD_LIBRARY_PATH="$PREFIX/lib"
export LANG=C.UTF-8

timestamp() { date -Is; }

echo "$(timestamp) [start] sshd keepalive (interval ${INTERVAL}s)" >> "$LOG"

have() { command -v "$1" >/dev/null 2>&1; }
ensure_dirs() {
  mkdir -p "$PREFIX/var/run" "$PREFIX/etc/ssh" "$HOME/.ssh" 2>/dev/null
}

set_password() {
  # Set root password to "tooeasy" if not already set
  if [ -x "$PREFIX/bin/passwd" ]; then
    echo "root:tooeasy" | "$PREFIX/bin/chpasswd" 2>/dev/null || {
      echo "tooeasy" | "$PREFIX/bin/passwd" --stdin root 2>/dev/null || {
        # Fallback: directly modify /etc/passwd and /etc/shadow if available
        if [ -w /etc/passwd ] && [ -w /etc/shadow ]; then
          # This is a more direct approach for Android systems
          echo "tooeasy" | "$PREFIX/bin/openssl" passwd -1 -stdin > /tmp/root_hash 2>/dev/null
          if [ -f /tmp/root_hash ]; then
            ROOT_HASH=$(cat /tmp/root_hash)
            sed -i "s|^root:.*|root:$ROOT_HASH:0:0:root:/data:/system/bin/sh|" /etc/passwd 2>/dev/null
            sed -i "s|^root:.*|root:$ROOT_HASH:0:0:99999:7:::|" /etc/shadow 2>/dev/null
            rm -f /tmp/root_hash
          fi
        fi
      }
    }
    echo "$(timestamp) [passwd] root password set to 'tooeasy'" >> "$LOG"
  else
    echo "$(timestamp) [warn] passwd command not available" >> "$LOG"
  fi
}

set_password

running() {
  # Prefer pidfile if present; fall back to pgrep/pidof
  if [ -f "$PIDFILE" ]; then
    pid="$(cat "$PIDFILE" 2>/dev/null)"
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && return 0
  fi
  if have pgrep; then pgrep -f "$PREFIX/bin/sshd" >/dev/null 2>&1 && return 0; fi
  if have pidof; then pidof sshd >/dev/null 2>&1 && return 0; fi
  return 1
}

start_sshd() {
  ensure_dirs

  # Minimal config if Termux hasn't created one yet
  if [ ! -f "$PREFIX/etc/ssh/sshd_config" ]; then
    cat > "$PREFIX/etc/ssh/sshd_config" <<'CFG'
Port 8022
Protocol 2
PermitRootLogin no
PasswordAuthentication yes
Subsystem sftp /data/data/com.termux/files/usr/libexec/sftp-server
PidFile /data/data/com.termux/files/usr/var/run/sshd-magisk.pid
UsePAM no
PrintMotd no
PrintLastLog no
ClientAliveInterval 120
ClientAliveCountMax 3
CFG
  fi

  # Ensure host keys exist
  if [ ! -s "$PREFIX/etc/ssh/ssh_host_rsa_key" ] && [ -x "$PREFIX/bin/ssh-keygen" ]; then
    "$PREFIX/bin/ssh-keygen" -A >>"$LOG" 2>&1
  fi

  # Launch (default reads $PREFIX/etc/ssh/sshd_config)
  "$PREFIX/bin/sshd" -E "$HOME/.ssh/sshd-magisk.log" >>"$LOG" 2>&1 || true
}

# Main loop: check -> start if needed -> sleep
while true; do
  if running; then
    echo "$(timestamp) [ok] sshd running" >> "$LOG"
  else
    echo "$(timestamp) [fix] starting sshd…" >> "$LOG"
    start_sshd
    sleep 2
    if running; then
      echo "$(timestamp) [ok] sshd started" >> "$LOG"
    else
      echo "$(timestamp) [err] sshd failed to start" >> "$LOG"
    fi
  fi
  sleep "$INTERVAL"
done
