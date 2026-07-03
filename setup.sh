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

# Keep package installs non-interactive. Some Termux postinst scripts
# (e.g. dpkg-perl) run cpan on first install, which otherwise blocks on
# an interactive configuration dialog.
export DEBIAN_FRONTEND=noninteractive
export PERL_MM_USE_DEFAULT=1

pkg up -y
pkg i -y tmux openssh python python-pip jq curl tsu procps termux-api git coreutils inetutils vim expect
pkg i -y x11-repo
pkg i -y dbus qt6-qtbase
pkg i -y opencv-python python-numpy python-pillow ffmpeg findutils
# Never `pip install --upgrade pip` on Termux: their pip is patched and
# self-upgrade is forbidden (it would break the python-pip package).
# Compiled deps (numpy, pillow, opencv) come from pkg above; pip is only
# used for pure-Python packages.
pip install requests python-dotenv

termux-setup-storage || true
termux-wake-lock || true
termux-notification --title "AndroidBoot setup" --content "Installing dependencies..." || true

echo ""
echo "Requesting root access. WATCH THE PHONE SCREEN and approve the Magisk prompt"
echo "(it denies automatically after ~10 seconds if ignored)."
if su -c true 2>/dev/null; then
  echo "Root access OK."
else
  echo "WARN: Root denied or unavailable. Root-dependent steps below will fail."
  echo "Open the Magisk app, grant Termux superuser access, then rerun ./setup.sh."
fi

# termux-camera-photo needs CAMERA on the Termux:API app. Without it the
# command still exits 0 but writes an empty file, so grant it here via root
# instead of relying on the manual settings step.
su -c 'pm grant com.termux.api android.permission.CAMERA; pm grant com.termux android.permission.CAMERA' || {
  echo "WARN: Could not auto-grant camera permission (no root?)."
  echo "Grant it manually: Android Settings -> Apps -> Termux:API -> Permissions -> Camera -> Allow"
}

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

chmod +x "$REPO_DIR/start.sh" "$REPO_DIR/stop.sh" "$REPO_DIR/setup.sh" "$REPO_DIR/ops.sh" "$REPO_DIR/watchdog.sh" \
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

# --- Verification: confirm every root-dependent step actually stuck, so a
# --- missed Magisk prompt earlier in the run cannot end in "Setup complete."
FAILURES=0

check() {
  local label="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    echo "PASS: $label"
  else
    echo "FAIL: $label"
    FAILURES=$((FAILURES + 1))
  fi
}

check_camera_perm() {
  local pkg="$1"
  su -c "dumpsys package $pkg" 2>/dev/null | grep -q 'android.permission.CAMERA: granted=true'
}

echo ""
echo "--- Verification ---"
check "root access (Magisk grant for Termux)" su -c true
check "camera permission on Termux:API" check_camera_perm com.termux.api
check "camera permission on Termux" check_camera_perm com.termux
check "Magisk boot script installed" su -c 'test -f /data/adb/service.d/androidboot.sh'
check "Termux:Boot script installed" test -f "$HOME/.termux/boot/androidboot.sh"
check ".env present" test -f "$REPO_DIR/.env"
check "operator keys in authorized_keys" test -s "$AUTH_KEYS"

if [ "$FAILURES" -gt 0 ]; then
  echo ""
  echo "SETUP INCOMPLETE: $FAILURES check(s) failed."
  echo "If root checks failed: open Magisk, approve/allow superuser for Termux, then rerun ./setup.sh."
  echo "Rerunning is safe; it redoes only what is missing."
  exit 1
fi

echo ""
echo "Setup complete. All checks passed."
echo "Next step: ./start.sh"
