#!/usr/bin/env bash
# Runs on the operator laptop (NOT the phone).
# Installs the required Android apps over adb, skipping ones already installed:
#   Termux, Termux:API, Termux:Boot, Tailscale (all from F-Droid, same signing source).
#
# Usage: connect exactly one phone with USB debugging enabled, then:
#   ./provision-apps.sh
#
# APKs are cached in ~/.cache/androidboot-apks so subsequent phones install offline-fast.
set -euo pipefail

PACKAGES=(com.termux com.termux.api com.termux.boot com.tailscale.ipn)
CACHE_DIR="${HOME}/.cache/androidboot-apks"
mkdir -p "$CACHE_DIR"

if ! command -v adb >/dev/null 2>&1; then
  echo "ERROR: adb not found on this machine." >&2
  exit 1
fi

devices="$(adb devices | awk 'NR>1 && $2=="device" {print $1}')"
count="$(printf '%s\n' "$devices" | grep -c . || true)"
if [ "$count" -eq 0 ]; then
  echo "ERROR: no device connected (check cable + USB debugging authorization)." >&2
  exit 1
fi
if [ "$count" -gt 1 ] && [ -z "${ANDROID_SERIAL:-}" ]; then
  echo "ERROR: multiple devices connected. Set ANDROID_SERIAL=<serial> and re-run." >&2
  adb devices >&2
  exit 1
fi

echo "Device: ${ANDROID_SERIAL:-$devices}"

# Google Play Protect blocks F-Droid installs with a generic 'unknown error'
# (INSTALL_FAILED_VERIFICATION_FAILURE). Disable verification for installs.
adb shell settings put global verifier_verify_adb_installs 0 >/dev/null 2>&1 || true
adb shell settings put global package_verifier_enable 0 >/dev/null 2>&1 || true

is_installed() {
  adb shell pm list packages 2>/dev/null | tr -d '\r' | grep -qx "package:$1"
}

suggested_version_code() {
  curl -fsSL "https://f-droid.org/api/v1/packages/$1" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["suggestedVersionCode"])'
}

for pkg in "${PACKAGES[@]}"; do
  if is_installed "$pkg"; then
    echo "SKIP    $pkg (already installed)"
    continue
  fi

  echo "FETCH   $pkg: resolving latest version on F-Droid..."
  vc="$(suggested_version_code "$pkg")"
  apk="$CACHE_DIR/${pkg}_${vc}.apk"

  if [ ! -s "$apk" ]; then
    curl -fL --progress-bar -o "$apk" "https://f-droid.org/repo/${pkg}_${vc}.apk"
  else
    echo "CACHED  $apk"
  fi

  echo "INSTALL $pkg (versionCode $vc)"
  adb install "$apk"
done

echo
echo "Done. Manual follow-ups on the phone:"
echo "  1. Open Termux:Boot ONCE (required for boot broadcasts to be delivered)."
echo "  2. Open Tailscale, allow VPN, sign in, rename node per convention."
echo "  3. Grant Termux + Termux:API permissions per SETUP.md Phase 4."
