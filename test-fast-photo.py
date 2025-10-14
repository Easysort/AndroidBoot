import os
import time
import subprocess
from concurrent.futures import ThreadPoolExecutor
from collections import deque
import threading
import shutil
from uuid import uuid4
from datetime import datetime, timezone, timedelta
import pathlib
import requests

import cv2
import numpy as np

TMPDIR = os.path.expanduser("~/tmp")
os.makedirs(TMPDIR, exist_ok=True)

# Output directories
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUN_DIR = os.path.join(BASE_DIR, "run")
MOTION_DIR = os.path.join(RUN_DIR, "motion")
NO_MOTION_DIR = os.path.join(RUN_DIR, "no-motion")
os.makedirs(MOTION_DIR, exist_ok=True)
os.makedirs(NO_MOTION_DIR, exist_ok=True)

# Motion detection settings (tweak via env vars)
BUFFER_SIZE = int(os.getenv("MOTION_BUFFER_SIZE", "10"))
RESIZE_WIDTH = int(os.getenv("MOTION_RESIZE_WIDTH", "320"))
MOTION_PIXEL_THRESHOLD = int(os.getenv("MOTION_PIXEL_THRESHOLD", "25"))
MOTION_AREA_RATIO_THRESHOLD = float(os.getenv("MOTION_AREA_RATIO_THRESHOLD", "0.01"))
MEAN_SAVE_PATH = os.path.join(RUN_DIR, "background_mean.npy")

# Supabase config (follow uploader.py style)
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_KEY")
HEADERS = {"Authorization": f"Bearer {SUPABASE_KEY}", "apikey": SUPABASE_KEY} if (SUPABASE_URL and SUPABASE_KEY) else None

# Buckets
ARGO_BUCKET = "argo"
WARNINGS_BUCKET = "warnings"

# Device ID (same approach as uploader.py)
ID_FILE = "device_id.txt"
if os.path.exists(ID_FILE):
    DEVICE_ID = open(ID_FILE).read().strip()
else:
    DEVICE_ID = "unknown-device"

# Upload rate limiting
class UploadRateLimiter:
    def __init__(self):
        self.lock = threading.Lock()
        self.last_upload_ts = 0.0
        self.upload_times = deque()  # unix timestamps of uploads within last 60s

    def _prune(self, now_ts: float):
        # keep only events within last 60 seconds
        while self.upload_times and (now_ts - self.upload_times[0] > 60.0):
            self.upload_times.popleft()

    def decide_bucket(self, now_ts: float):
        with self.lock:
            self._prune(now_ts)
            # 3s minimum spacing between any uploads
            if now_ts - self.last_upload_ts < 3.0:
                return None
            count = len(self.upload_times)
            if count < 10:
                # reserve a slot for ARGO upload
                self.upload_times.append(now_ts)
                self.last_upload_ts = now_ts
                return ARGO_BUCKET
            elif count == 10:
                # 11th in the rolling minute goes to warnings only
                self.upload_times.append(now_ts)
                self.last_upload_ts = now_ts
                return WARNINGS_BUCKET
            else:
                # 12+ do nothing
                return None


rate_limiter = UploadRateLimiter()


def is_within_cet_window(now_utc: datetime) -> bool:
    """Return True only between 22:00 and 06:00 CET (UTC+2, no DST handling)."""
    cet = now_utc + timedelta(hours=2)
    hr = cet.hour
    return hr >= 22 or hr <= 6


def upload_image(img_path: str, bucket: str) -> str:
    """Upload image to Supabase Storage, return public URL. Follows uploader.py style."""
    if not HEADERS or not SUPABASE_URL:
        raise RuntimeError("Supabase env missing: SUPABASE_URL and SUPABASE_ANON_KEY")
    now = datetime.now(timezone.utc)
    key = f"{DEVICE_ID}/{now:%Y/%m/%d/%H}/{os.path.basename(img_path)}"
    url = f"{SUPABASE_URL}/storage/v1/object/{bucket}/{key}"
    with open(img_path, "rb") as f:
        r = requests.post(url, headers={**HEADERS, "x-upsert": "true", "Content-Type": "image/jpeg"}, data=f.read())
    r.raise_for_status()
    return f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{key}"


class BackgroundModel:
    def __init__(self, maxlen=10, resize_width=320):
        self.frames = deque(maxlen=maxlen)
        self.lock = threading.Lock()
        self.mean_image = None
        self.resize_width = resize_width

    def preprocess(self, bgr_image):
        h, w = bgr_image.shape[:2]
        if self.resize_width and w != self.resize_width:
            scale = self.resize_width / float(w)
            bgr_image = cv2.resize(bgr_image, (self.resize_width, int(round(h * scale))))
        gray = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)
        return gray

    def get_mean(self):
        with self.lock:
            if self.mean_image is None:
                return None
            return self.mean_image.copy()

    def update(self, gray_frame):
        gray32 = gray_frame.astype(np.float32)
        with self.lock:
            self.frames.append(gray32)
            # Recompute uniform mean over the current buffer
            self.mean_image = np.mean(np.stack(list(self.frames), axis=0), axis=0)

    def save_mean(self, path):
        with self.lock:
            if self.mean_image is not None:
                np.save(path, self.mean_image)

    def try_load_mean(self, path):
        if os.path.exists(path):
            try:
                mean = np.load(path)
                if isinstance(mean, np.ndarray) and mean.ndim == 2:
                    with self.lock:
                        self.mean_image = mean.astype(np.float32)
                else:
                    print(f"Invalid mean format at {path}")
            except Exception as e:
                print(f"Failed loading mean from {path}: {e}")

    def reset(self):
        with self.lock:
            # clear frame buffer and mean image
            self.frames.clear()
            self.mean_image = None


background_model = BackgroundModel(maxlen=BUFFER_SIZE, resize_width=RESIZE_WIDTH)
background_model.try_load_mean(MEAN_SAVE_PATH)


def classify_and_route_photo(out_path):
    image = cv2.imread(out_path)
    if image is None:
        print(f"Failed to read image for analysis: {out_path}")
        return

    gray = background_model.preprocess(image)
    mean_image = background_model.get_mean()

    is_motion = False
    motion_ratio = 0.0
    if mean_image is not None:
        diff = cv2.absdiff(gray.astype(np.float32), mean_image)
        diff8 = np.clip(diff, 0, 255).astype(np.uint8)
        _, thresh = cv2.threshold(diff8, MOTION_PIXEL_THRESHOLD, 255, cv2.THRESH_BINARY)
        kernel = np.ones((3, 3), np.uint8)
        thresh = cv2.dilate(thresh, kernel, iterations=2)
        motion_ratio = float(cv2.countNonZero(thresh)) / float(thresh.size)
        is_motion = motion_ratio >= MOTION_AREA_RATIO_THRESHOLD

    # Update background only if no motion detected; persist mean for others
    if not is_motion:
        background_model.update(gray)
        background_model.save_mean(MEAN_SAVE_PATH)

    # Route photo to appropriate folder
    target_dir = MOTION_DIR if is_motion else NO_MOTION_DIR
    base_name = os.path.basename(out_path)
    dest_path = os.path.join(target_dir, base_name)
    if os.path.exists(dest_path):
        name, ext = os.path.splitext(base_name)
        dest_path = os.path.join(target_dir, f"{name}_{uuid4().hex[:6]}{ext}")
    try:
        shutil.move(out_path, dest_path)
    except Exception as e:
        print(f"Failed moving photo to destination: {e}")
        return

    label = "MOTION" if is_motion else "no-motion"
    print(f"Photo classified: {label} (ratio={motion_ratio:.4f}) -> {dest_path}")

    # Upload only motion frames, with CET 22-06 window gating and rate limits
    if is_motion and HEADERS and SUPABASE_URL:
        try:
            now_utc = datetime.now(timezone.utc)
            if is_within_cet_window(now_utc):
                bucket = rate_limiter.decide_bucket(time.time())
                if bucket:
                    url = upload_image(dest_path, bucket)
                    print(f"Uploaded to {bucket}: {url}")
                else:
                    # rate-limited or spacing not met
                    pass
            else:
                # Outside window; skip upload
                pass
        except Exception as e:
            print(f"Upload failed: {e}")

def take_photo(camera_id="0"):
    ts = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    # add short suffix to avoid collisions when taking multiple per second
    out_path = os.path.join(TMPDIR, f"photo_{ts}_{uuid4().hex[:6]}.jpg")
    try:
        subprocess.run(
            ["termux-camera-photo", "-c", str(camera_id), out_path],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
        # Analyze and route without blocking main loop (still inside worker thread)
        classify_and_route_photo(out_path)
    except Exception as e:
        print(f"Error taking photo at {ts}: {e}")

def main(interval=1, camera_id="0"):
    # Reset run directory on startup
    try:
        if os.path.exists(RUN_DIR):
            shutil.rmtree(RUN_DIR)
        os.makedirs(MOTION_DIR, exist_ok=True)
        os.makedirs(NO_MOTION_DIR, exist_ok=True)
    except Exception as e:
        print(f"Failed resetting run directory: {e}")
    # Start fresh background
    background_model.reset()

    with ThreadPoolExecutor(max_workers=4) as executor:
        while True:
            executor.submit(take_photo, camera_id)
            time.sleep(interval)

if __name__ == "__main__":
    main()
