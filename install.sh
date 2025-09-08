#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

# Load env (non-exported like ROLE still visible here)
source "./phone.env"

pkg up -y
pkg i -y tmux openssh golang python jq curl tsu procps termux-api git coreutils inetutils vim

# Termux permissions & wake
termux-wake-lock || true
termux-notification --title "Phone metrics" --content "Installing dependencies..." || true

# # Build Go API
# export GO111MODULE=on
# go version
# go build -o metrics-api ./main.go

# Create dirs
mkdir -p "$STATE_DIR" "$LOG_DIR" "$ERROR_DIR" "$SENT_ERROR_DIR" "$TMPDIR"

# SSHD basic setup (Termux default port 8022)
if ! command -v sshd >/dev/null 2>&1; then
  echo "openssh missing?"
  exit 1
fi
# Ensure host keys exist
test -f "$PREFIX/etc/ssh/ssh_host_rsa_key" || sshd -Dd 2>/dev/null || true

# Termux:API apps needed (install Termux:API & Termux:Boot apps from F-Droid)
echo "IMPORTANT:"
echo " - Install Termux:API and Termux:Boot apps from F-Droid."
echo " - Grant camera, location, storage permissions to Termux."
echo " - Disable battery optimizations for Termux."

# # Tailscale install (static script; works on aarch64)
# if ! command -v tailscale >/dev/null 2>&1; then
#   curl -fsSL https://tailscale.com/install.sh | sh
# fi

# # Make scripts executable
chmod +x watchdog.sh uploader.py tmux-starter.sh

echo "Install done."
