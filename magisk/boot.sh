#!/bin/bash
set -euo pipefail

if [ $# -ne 1 ]; then
  echo "Usage: $0 <filename>"
  echo "Example: $0 30700_xIkal"
  exit 1
fi

FILENAME="$1"
IMG="magisk_patched-$FILENAME.img"

if [ ! -f "$IMG" ]; then
  echo "Error: $IMG not found. Run pull.sh $FILENAME first."
  exit 1
fi

echo "Disabling off-mode charge..."
fastboot oem off-mode-charge 0

echo "Disabling charger screen..."
fastboot oem disable-charger-screen

echo "Booting patched image $IMG ..."
fastboot boot "$IMG"

echo "Done."
