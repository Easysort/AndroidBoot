#!/usr/bin/env python3

"""
Absolute simplest motion capture on Android (Termux):
- Captures a photo every second via termux-camera-photo
- Detects motion via file-size change threshold
- Saves EVERY frame: motion -> ./motion, non-motion -> ./non-motion
- Serves a basic HTTP directory listing so you can browse images

Requirements on phone:
- termux-api installed and camera permission granted

Environment overrides (optional):
- CAMERA_ID (default "0")
- CAPTURE_INTERVAL_SEC (default "1")
- MOTION_SIZE_THRESHOLD (bytes, default "50000")
- PORT (default "8080")
- CAPTURE_DIR (default current directory)
"""

import os
import time
import subprocess
import threading
import shutil
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


CAMERA_ID = os.environ.get("CAMERA_ID", "0")
CAPTURE_INTERVAL_SEC = int(os.environ.get("CAPTURE_INTERVAL_SEC", "1"))
MOTION_SIZE_THRESHOLD = int(os.environ.get("MOTION_SIZE_THRESHOLD", "50000"))
PORT = int(os.environ.get("PORT", "8080"))

BASE_DIR = Path(os.environ.get("CAPTURE_DIR", ".")).resolve()
MOTION_DIR = BASE_DIR / "motion"
NON_MOTION_DIR = BASE_DIR / "non-motion"
TMP_DIR = BASE_DIR / "tmp"

_last_file_size_bytes: int | None = None


def ensure_directories() -> None:
    for d in (MOTION_DIR, NON_MOTION_DIR, TMP_DIR):
        d.mkdir(parents=True, exist_ok=True)


def capture_once(temp_path: Path) -> bool:
    """Capture a single photo to temp_path using termux-camera-photo."""
    try:
        result = subprocess.run(
            ["termux-camera-photo", "-c", str(CAMERA_ID), str(temp_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            # Keep logs minimal but helpful
            err = (result.stderr or "").strip()
            if err:
                print(f"capture failed (code {result.returncode}): {err}")
            return False
        return temp_path.exists() and temp_path.stat().st_size > 0
    except subprocess.TimeoutExpired:
        print("capture timeout")
        return False
    except Exception as e:
        print(f"capture error: {e}")
        return False


def motion_capture_loop() -> None:
    """Continuously capture, detect motion, and save to the appropriate folder."""
    global _last_file_size_bytes
    ensure_directories()

    print("Starting motion capture loop:")
    print(f"- Camera ID: {CAMERA_ID}")
    print(f"- Interval: {CAPTURE_INTERVAL_SEC}s")
    print(f"- Motion threshold: {MOTION_SIZE_THRESHOLD} bytes")
    print(f"- Saving to: {MOTION_DIR} and {NON_MOTION_DIR}")

    while True:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        temp_path = TMP_DIR / f"cap_{ts}.jpg"

        ok = capture_once(temp_path)
        if not ok:
            # Wait and try again next tick
            time.sleep(CAPTURE_INTERVAL_SEC)
            continue

        try:
            current_size = temp_path.stat().st_size
        except FileNotFoundError:
            current_size = 0

        # Detect motion via file-size difference
        motion = False
        if _last_file_size_bytes is not None:
            if abs(current_size - _last_file_size_bytes) >= MOTION_SIZE_THRESHOLD:
                motion = True
        _last_file_size_bytes = current_size

        # Decide destination and move
        dest_dir = MOTION_DIR if motion else NON_MOTION_DIR
        prefix = "motion" if motion else "nonmotion"
        dest_path = dest_dir / f"{prefix}_{ts}.jpg"
        try:
            temp_path.replace(dest_path)  # atomic if same filesystem
        except Exception:
            # Fallback to shutil.move when replace is not possible
            try:
                shutil.move(str(temp_path), str(dest_path))
            except Exception as e:
                print(f"save move failed: {e}")

        print(("MOTION" if motion else "no-motion") + f": saved {dest_path.name} (size={current_size})")

        # Sleep until next capture
        time.sleep(CAPTURE_INTERVAL_SEC)


def serve_files() -> None:
    """Serve the current directory so you can browse images in a browser."""
    os.chdir(str(BASE_DIR))
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), SimpleHTTPRequestHandler)
    print(f"Web server: http://0.0.0.0:{PORT}")
    print("Open the 'motion/' and 'non-motion/' folders in your browser.")
    httpd.serve_forever()


def main() -> None:
    capture_thread = threading.Thread(target=motion_capture_loop, daemon=True)
    capture_thread.start()
    serve_files()


if __name__ == "__main__":
    main()


