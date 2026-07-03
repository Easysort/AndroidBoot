#!/bin/bash
set -euo pipefail

if [ $# -ne 1 ]; then
  echo "Usage: $0 <filename>"
  echo "Example: $0 30700_xIkal"
  exit 1
fi

FILENAME="$1"
SRC="/storage/emulated/0/Download/magisk_patched-$FILENAME.img"

echo "Pulling $SRC ..."
adb pull "$SRC" .

echo "Done. Sending device to fastboot mode..."
adb reboot fastboot