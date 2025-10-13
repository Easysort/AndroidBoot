#!/usr/bin/env python3

"""
Fast capture loop for Android Termux using termux-camera-photo.
Saves images as fast as possible into ./queue/ to be classified later.

Env vars:
- CAMERA_ID (default "0")
- CAPTURE_INTERVAL_SEC (float, default "0")  # extra delay target; pacing by elapsed
- QUEUE_DIR (default ".")  # images saved to QUEUE_DIR/queue/
- MAX_QUEUE_FILES (default "2000")  # soft limit to avoid filling storage
"""

import os
import time
import subprocess
from datetime import datetime
from pathlib import Path


CAMERA_ID = os.environ.get("CAMERA_ID", "0")
CAPTURE_INTERVAL_SEC = float(os.environ.get("CAPTURE_INTERVAL_SEC", "0"))
BASE_DIR = Path(os.environ.get("QUEUE_DIR", ".")).resolve()
QUEUE_DIR = BASE_DIR / "queue"
MAX_QUEUE_FILES = int(os.environ.get("MAX_QUEUE_FILES", "2000"))


def ensure_dirs() -> None:
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)


def capture_once(path: Path) -> bool:
    try:
        r = subprocess.run(
            ["termux-camera-photo", "-c", str(CAMERA_ID), str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
        if r.returncode != 0:
            err = (r.stderr or "").strip()
            if err:
                print(f"capture failed ({r.returncode}): {err}")
            return False
        return path.exists() and path.stat().st_size > 0
    except subprocess.TimeoutExpired:
        print("capture timeout")
        return False
    except Exception as e:
        print(f"capture error: {e}")
        return False


def main() -> None:
    ensure_dirs()
    print("Fast capture starting…")
    print(f"- Camera: {CAMERA_ID}")
    print(f"- Extra interval target: {CAPTURE_INTERVAL_SEC}s")
    print(f"- Queue dir: {QUEUE_DIR}")
    print(f"- Max queued files: {MAX_QUEUE_FILES}")

    while True:
        loop_start = time.time()

        # Soft backpressure when queue is large
        try:
            q_count = sum(1 for _ in QUEUE_DIR.iterdir())
        except FileNotFoundError:
            q_count = 0
        if q_count >= MAX_QUEUE_FILES:
            time.sleep(0.5)
            continue

        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        out_path = QUEUE_DIR / f"q_{ts}.jpg"
        ok = capture_once(out_path)
        if ok:
            try:
                size = out_path.stat().st_size
            except Exception:
                size = 0
            print(f"queued: {out_path.name} size={size}")
        else:
            # brief pause on failure
            time.sleep(0.2)

        # pace remainder only (avoid double-waiting)
        elapsed = time.time() - loop_start
        rem = CAPTURE_INTERVAL_SEC - elapsed
        if rem > 0:
            time.sleep(rem)


if __name__ == "__main__":
    main()


