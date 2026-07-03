#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

prompt_value() {
  local label="$1"
  local default="$2"
  local out_var="$3"
  local value=""

  read -r -p "$label [$default]: " value
  if [ -z "$value" ]; then
    value="$default"
  fi
  printf -v "$out_var" '%s' "$value"
}

pkg up -y
pkg i -y tmux openssh python jq curl tsu procps termux-api git coreutils inetutils vim expect
pkg i -y x11-repo
pkg i -y dbus qt6-qtbase
pkg i -y opencv-python python-numpy ffmpeg findutils
pip install --upgrade pip
pip install requests python-dotenv pillow

termux-setup-storage || true
termux-wake-lock || true
termux-notification --title "AndroidBoot setup" --content "Installing dependencies..." || true

current_device_id="$(cat "$REPO_DIR/device_id.txt" 2>/dev/null || true)"
prompt_value "DEVICE_ID (example: Argo-roskilde-01-03)" "${current_device_id:-device-unknown}" DEVICE_ID
printf '%s\n' "$DEVICE_ID" > "$REPO_DIR/device_id.txt"

# .env is copied from the operator's .env.full, not generated from prompts.
if [ -f "$REPO_DIR/.env" ]; then
  echo ".env already present, leaving it as is."
elif [ -f "$REPO_DIR/.env.full" ]; then
  cp "$REPO_DIR/.env.full" "$REPO_DIR/.env"
  echo "Copied .env.full -> .env"
else
  echo "ERROR: No .env or .env.full found in $REPO_DIR."
  echo "Copy it from the operator laptop first, e.g.:"
  echo "  scp .env.full <phone>:AndroidBoot/.env"
  echo "or via adb:"
  echo "  adb push .env.full /sdcard/Download/ (then in Termux: cp /sdcard/Download/.env.full ~/AndroidBoot/.env)"
  exit 1
fi

OPS_KEYS="$REPO_DIR/keys/ops_authorized_keys"
AUTH_KEYS="$HOME/.ssh/authorized_keys"

if [ ! -f "$OPS_KEYS" ]; then
  echo "ERROR: Missing $OPS_KEYS"
  exit 1
fi

mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
touch "$AUTH_KEYS"
chmod 600 "$AUTH_KEYS"

while IFS= read -r line || [ -n "$line" ]; do
  [[ "$line" =~ ^[[:space:]]*$ ]] && continue
  [[ "$line" =~ ^# ]] && continue
  if ! grep -qF "$line" "$AUTH_KEYS"; then
    printf '%s\n' "$line" >> "$AUTH_KEYS"
  fi
done < "$OPS_KEYS"

chmod +x "$REPO_DIR/start.sh" "$REPO_DIR/setup.sh" "$REPO_DIR/ops.sh" "$REPO_DIR/watchdog.sh" \
         "$REPO_DIR/app/keepwarm.sh" \
         "$REPO_DIR/boot/service.d-androidboot.sh" "$REPO_DIR/boot/termux-boot.sh"

mkdir -p "$HOME/.termux/boot"
cp "$REPO_DIR/boot/termux-boot.sh" "$HOME/.termux/boot/androidboot.sh"
chmod 700 "$HOME/.termux/boot/androidboot.sh"

echo "Installing Magisk boot script to /data/adb/service.d/androidboot.sh (requires root)..."
su -c "cp '$REPO_DIR/boot/service.d-androidboot.sh' /data/adb/service.d/androidboot.sh && chmod 755 /data/adb/service.d/androidboot.sh" || {
  echo "WARN: Could not install Magisk script automatically."
  echo "Run manually:"
  echo "  su -c 'cp $REPO_DIR/boot/service.d-androidboot.sh /data/adb/service.d/androidboot.sh && chmod 755 /data/adb/service.d/androidboot.sh'"
}

echo "Setup complete."
echo "Next step: ./start.sh"
