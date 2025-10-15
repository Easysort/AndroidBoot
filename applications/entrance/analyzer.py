import os
import time
import subprocess
from concurrent.futures import ThreadPoolExecutor
from collections import deque
import threading
import shutil
from uuid import uuid4
import pathlib
import requests
from dotenv import load_dotenv
import json
from applications.common import Helper, Env

import cv2
import numpy as np

load_dotenv()

TMPDIR = os.path.expanduser("~/tmp")
os.makedirs(TMPDIR, exist_ok=True)

# Working directory for background mean and any state
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUN_DIR = os.path.join(BASE_DIR, "run")
os.makedirs(RUN_DIR, exist_ok=True)

# Motion detection settings (tweak via env vars)
BUFFER_SIZE = int(os.getenv("MOTION_BUFFER_SIZE", "100"))
RESIZE_WIDTH = int(os.getenv("MOTION_RESIZE_WIDTH", "320"))
MOTION_PIXEL_THRESHOLD = int(os.getenv("MOTION_PIXEL_THRESHOLD", "25"))
MOTION_AREA_RATIO_THRESHOLD = float(os.getenv("MOTION_AREA_RATIO_THRESHOLD", "0.01"))
MEAN_SAVE_PATH = os.path.join(RUN_DIR, "background_mean.npy")

# Additional thresholds for distinguishing global light shift (bad image) vs. local motion
LOW_DIFF_THRESHOLD = int(os.getenv("LOW_DIFF_THRESHOLD", "5"))
BAD_LOW_RATIO_THRESHOLD = float(os.getenv("BAD_LOW_RATIO_THRESHOLD", "0.6"))
BAD_HIGH_RATIO_MAX = float(os.getenv("BAD_HIGH_RATIO_MAX", "0.02"))

# Supabase config (follow uploader.py style)
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_KEY")
HEADERS = {"Authorization": f"Bearer {SUPABASE_KEY}", "apikey": SUPABASE_KEY} if (SUPABASE_URL and SUPABASE_KEY) else None

# Buckets
ARGO_BUCKET = "argo"
WARNINGS_BUCKET = "warnings"

# Upload rate limiting
class UploadRateLimiter:
    def __init__(self):
        self.lock = threading.Lock()
        # Rolling 1-hour window of successful uploads
        self.upload_times_hour = deque()
        # Burst control per motion: allow first 2, then 5s cooldown
        self.uploads_in_burst = 0
        self.cooldown_until_ts = 0.0

    def _prune_hour(self, now_ts: float):
        # keep only events within last 3600 seconds
        while self.upload_times_hour and (now_ts - self.upload_times_hour[0] > 3600.0):
            self.upload_times_hour.popleft()

    def decide_allow_upload(self, now_ts: float):
        """Return (allowed: bool, reason: Optional[str]).
        Enforces 15/hour cap and burst: first 2 allowed immediately, then 5s cooldown.
        """
        with self.lock:
            # Reset burst state after cooldown passes
            if now_ts >= self.cooldown_until_ts and self.uploads_in_burst >= 2:
                self.uploads_in_burst = 0

            self._prune_hour(now_ts)
            if len(self.upload_times_hour) >= 15:
                return False, "hour_cap"

            if now_ts < self.cooldown_until_ts:
                return False, "cooldown"

            if self.uploads_in_burst < 2:
                self.uploads_in_burst += 1
                if self.uploads_in_burst == 2:
                    self.cooldown_until_ts = now_ts + 5.0
                # Reserve slot in hour window now
                self.upload_times_hour.append(now_ts)
                return True, None

            # Should not reach here due to cooldown guard, but be safe
            return False, "cooldown"


rate_limiter = UploadRateLimiter()


def is_within_cet_window() -> bool:
    hr = Helper.current_time().hour
    return hr < 22 or hr >= 6


def upload_image(img_path: str, bucket: str) -> str:
    """Upload image to Supabase Storage, return public URL. Follows uploader.py style."""
    if not HEADERS or not SUPABASE_URL:
        raise RuntimeError("Supabase env missing: SUPABASE_URL and SUPABASE_ANON_KEY")
    now = Helper.current_time()
    key = f"{Env.DEVICE_ID}/{now:%Y/%m/%d/%H}/{os.path.basename(img_path)}"
    url = f"{SUPABASE_URL}/storage/v1/object/{bucket}/{key}"
    log_event("upload_request", url=url, bucket=bucket, key=key)
    with open(img_path, "rb") as f:
        r = requests.post(
            url,
            headers={**HEADERS, "x-upsert": "true", "Content-Type": "image/jpeg"},
            data=f.read(),
        )
    if not r.ok:
        try:
            body = r.text
        except Exception:
            body = ""
        log_event("upload_http_error", status=r.status_code, url=url, body=body)
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


def log_event(event: str, **fields):
    try:
        payload = {"event": event, "ts": Helper.current_time(), **fields}
        print(json.dumps(payload, ensure_ascii=False))
    except Exception:
        # Best-effort logging; fall back to simple print
        print(f"{event}: {fields}")


def classify_and_route_photo(out_path):
    image = cv2.imread(out_path)
    if image is None:
        log_event("analyze_error", reason="read_failed", path=out_path)
        return

    gray = background_model.preprocess(image)
    mean_image = background_model.get_mean()

    # If no background yet, warm up the model with this frame and skip motion
    if mean_image is None:
        background_model.update(gray)
        background_model.save_mean(MEAN_SAVE_PATH)
        try:
            os.remove(out_path)
        except Exception:
            pass
        log_event("no_motion", reason="warmup_no_mean")
        return

    # Compute absolute difference versus mean
    diff = cv2.absdiff(gray.astype(np.float32), mean_image)
    diff8 = np.clip(diff, 0, 255).astype(np.uint8)

    # High-diff mask (potential motion) and low-diff mask (global light changes)
    _, high_thresh = cv2.threshold(diff8, MOTION_PIXEL_THRESHOLD, 255, cv2.THRESH_BINARY)
    kernel = np.ones((3, 3), np.uint8)
    high_thresh_dilated = cv2.dilate(high_thresh, kernel, iterations=2)
    motion_ratio = float(cv2.countNonZero(high_thresh_dilated)) / float(high_thresh_dilated.size)

    low_mask = (diff8 >= LOW_DIFF_THRESHOLD) & (diff8 < MOTION_PIXEL_THRESHOLD)
    total_pixels = float(diff8.size)
    low_ratio = float(np.count_nonzero(low_mask)) / total_pixels
    high_ratio = float(np.count_nonzero(diff8 >= MOTION_PIXEL_THRESHOLD)) / total_pixels

    # Classification
    is_bad_image = (low_ratio >= BAD_LOW_RATIO_THRESHOLD) and (high_ratio <= BAD_HIGH_RATIO_MAX)
    is_motion = (not is_bad_image) and (motion_ratio >= MOTION_AREA_RATIO_THRESHOLD)

    # Always update the background with this frame (include motion frames) and persist
    background_model.update(gray)
    background_model.save_mean(MEAN_SAVE_PATH)

    # Handle outcomes
    if is_bad_image:
        try:
            os.remove(out_path)
        except Exception:
            pass
        log_event(
            "bad_image",
            low_ratio=round(low_ratio, 6),
            high_ratio=round(high_ratio, 6),
            motion_ratio=round(motion_ratio, 6),
        )
        return

    if not is_motion:
        try:
            os.remove(out_path)
        except Exception:
            pass
        log_event(
            "no_motion",
            low_ratio=round(low_ratio, 6),
            high_ratio=round(high_ratio, 6),
            motion_ratio=round(motion_ratio, 6),
        )
        return

    # Upload only motion frames, with CET 22-06 window gating and rate limits
    if HEADERS and SUPABASE_URL:
        try:
            if not is_within_cet_window():
                log_event("upload_skip", reason="outside_window", motion_ratio=round(motion_ratio, 6), now_local=Helper.current_time())
                return
            allowed, reason = rate_limiter.decide_allow_upload(time.time())
            if not allowed:
                log_event("upload_skip", reason=reason, motion_ratio=round(motion_ratio, 6))
                return
            bucket = ARGO_BUCKET
            url = upload_image(out_path, bucket)
            log_event("upload_success", bucket=bucket, url=url, motion_ratio=round(motion_ratio, 6))
        except Exception as e:
            log_event("upload_error", error=str(e))
        finally:
            try:
                os.remove(out_path)
            except Exception:
                pass

def take_photo(camera_id="0"):
    ts = Helper.current_time().strftime("%Y%m%dT%H%M%SZ")
    out_path = os.path.join(TMPDIR, f"photo_{ts}.jpg")
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
    # Reset run directory on startup (keep only as state dir for background mean)
    try:
        if os.path.exists(RUN_DIR):
            shutil.rmtree(RUN_DIR)
        os.makedirs(RUN_DIR, exist_ok=True)
    except Exception as e:
        log_event("run_reset_error", error=str(e))
    # Start fresh background
    background_model.reset()

    with ThreadPoolExecutor(max_workers=4) as executor:
        while True:
            executor.submit(take_photo, camera_id)
            time.sleep(interval)

if __name__ == "__main__":
    main()
