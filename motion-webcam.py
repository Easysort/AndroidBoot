#!/usr/bin/env python3

"""
Absolute simplest motion capture on Android (Termux):
- Captures a photo every second via termux-camera-photo
- Detects motion via deviation from rolling average of last non-motion sizes
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
- BASELINE_WINDOW_N (default "10") size of rolling window for baseline
- SERVER_ONLY (default "0"): if "1", only run the web server/grid
"""

import os
import time
import subprocess
import threading
import shutil
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from collections import deque


CAMERA_ID = os.environ.get("CAMERA_ID", "0")
CAPTURE_INTERVAL_SEC = int(os.environ.get("CAPTURE_INTERVAL_SEC", "1"))
MOTION_SIZE_THRESHOLD = int(os.environ.get("MOTION_SIZE_THRESHOLD", "120000"))
PORT = int(os.environ.get("PORT", "8080"))
SERVER_ONLY = os.environ.get("SERVER_ONLY", "0") == "1"
BASELINE_WINDOW_N = int(os.environ.get("BASELINE_WINDOW_N", "10"))

BASE_DIR = Path(os.environ.get("CAPTURE_DIR", ".")).resolve()
MOTION_DIR = BASE_DIR / "motion"
NON_MOTION_DIR = BASE_DIR / "non-motion"
TMP_DIR = BASE_DIR / "tmp"

_last_file_size_bytes: int | None = None
_baseline_non_motion_sizes = deque(maxlen=BASELINE_WINDOW_N)


def human_readable_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    kb = num_bytes / 1024.0
    if kb < 1024:
        return f"{int(kb)} KB"
    mb = kb / 1024.0
    if mb < 1024:
        return f"{mb:.1f} MB"
    gb = mb / 1024.0
    return f"{gb:.2f} GB"


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
    print(f"- Motion threshold (abs diff from baseline): {MOTION_SIZE_THRESHOLD} bytes")
    print(f"- Baseline window: last {BASELINE_WINDOW_N} non-motion frames")
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

        # Detect motion via deviation from rolling baseline of non-motion sizes
        # If baseline not established, treat as non-motion and seed the baseline.
        motion = False
        if len(_baseline_non_motion_sizes) >= 1:
            baseline_avg = sum(_baseline_non_motion_sizes) / len(_baseline_non_motion_sizes)
            if abs(current_size - baseline_avg) >= MOTION_SIZE_THRESHOLD:
                motion = True
        # Update baseline with non-motion frames only
        if not motion:
            _baseline_non_motion_sizes.append(current_size)
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


class GridHandler(SimpleHTTPRequestHandler):
    """Ultra-light handler that serves:
    - "/" : landing page with two links (motion, non-motion)
    - "/grid?type=motion|non" : 6-col grid of images from selected folder
    - Static files via parent handler (so images are served automatically)
    """

    def _send_html(self, html: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def do_GET(self):  # type: ignore[override]
        if self.path == "/" or self.path.startswith("/index.html"):
            html = (
                "<!doctype html><meta charset='utf-8'>"
                "<title>Capture Browser</title>"
                "<style>body{font:16px system-ui;margin:16px;background:#111;color:#eee}"
                "a{color:#7fd} .card{margin:12px 0;padding:12px;background:#181818;border-radius:8px}"
                "</style>"
                "<h2>Images</h2>"
                "<div class='card'><a href='/grid?type=motion'>View motion images</a></div>"
                "<div class='card'><a href='/grid?type=non'>View non-motion images</a></div>"
            )
            return self._send_html(html)

        if self.path.startswith("/grid"):
            # Parse query
            q = ""
            if "?" in self.path:
                q = self.path.split("?", 1)[1]
            params = {k: v for k, v in (p.split("=", 1) if "=" in p else (p, "") for p in q.split("&") if p)}
            which = params.get("type", "motion")
            folder = MOTION_DIR if which == "motion" else NON_MOTION_DIR

            # Collect images (newest first), limit for snappy load
            files = []
            try:
                for p in folder.iterdir():
                    if p.suffix.lower() in (".jpg", ".jpeg", ".png"):
                        files.append(p)
            except FileNotFoundError:
                files = []
            files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            files = files[:600]  # cap to avoid huge pages

            # Build grid
            rows = []
            row = []
            for i, p in enumerate(files, 1):
                rel = os.path.relpath(p, BASE_DIR).replace("\\", "/")
                try:
                    sz = p.stat().st_size
                except Exception:
                    sz = 0
                label = human_readable_size(int(sz))
                cell = (
                    "<div class='cell'>"
                    f"<img loading='lazy' src='/{rel}'>"
                    f"<div class='meta'>{label}</div>"
                    "</div>"
                )
                row.append(cell)
                if i % 6 == 0:
                    rows.append("<div class='row'>" + "".join(row) + "</div>")
                    row = []
            if row:
                rows.append("<div class='row'>" + "".join(row) + "</div>")

            html = (
                "<!doctype html><meta charset='utf-8'>"
                f"<title>{'Motion' if which=='motion' else 'Non-motion'} images</title>"
                "<style>body{font:16px system-ui;margin:8px;background:#111;color:#eee}"
                ".top{display:flex;gap:8px;align-items:center;margin:8px}"
                "a{color:#7fd} .btn{padding:6px 10px;background:#181818;border-radius:6px;display:inline-block}"
                ".grid{display:flex;flex-direction:column;gap:8px} .row{display:grid;grid-template-columns:repeat(6,1fr);gap:8px}"
                ".cell{background:#181818;border-radius:6px;padding:4px} .cell img{max-width:100%;height:auto;display:block;border-radius:4px}"
                ".meta{font-size:12px;color:#aaa;margin-top:4px;text-align:right}"
                "</style>"
                "<div class='top'>"
                "<a class='btn' href='/'>Home</a>"
                "<a class='btn' href='/grid?type=motion'>Motion</a>"
                "<a class='btn' href='/grid?type=non'>Non-motion</a>"
                "</div>"
                "<div class='grid'>"
                + "".join(rows) +
                "</div>"
            )
            return self._send_html(html)

        # Fallback to static serving for files
        return super().do_GET()


def serve_files() -> None:
    os.chdir(str(BASE_DIR))
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), GridHandler)
    print(f"Web server: http://0.0.0.0:{PORT}")
    print("Open / to choose 'motion' or 'non-motion', then browse the grid.")
    httpd.serve_forever()


def main() -> None:
    if not SERVER_ONLY:
        capture_thread = threading.Thread(target=motion_capture_loop, daemon=True)
        capture_thread.start()
    serve_files()


if __name__ == "__main__":
    main()


