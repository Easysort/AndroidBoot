#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

LOG_DIR="${HOME}/.local/state/androidboot/logs"
BOOT_LOG="${LOG_DIR}/termux-boot.log"

mkdir -p "$LOG_DIR"
termux-wake-lock || true

echo "$(date -Is) termux-boot: wake-lock acquired, starting AndroidBoot" >>"$BOOT_LOG"
exec >>"$BOOT_LOG" 2>&1
cd "${HOME}/AndroidBoot"
exec ./start.sh
