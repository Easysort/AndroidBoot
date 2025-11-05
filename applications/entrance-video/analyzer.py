import os
import io
import json
import time
import shutil
import requests
import pathlib
import subprocess
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor

import cv2
from PIL import Image

from dotenv import load_dotenv


# Config and directories
load_dotenv()

TMPDIR = os.path.expanduser("~/tmp")
os.makedirs(TMPDIR, exist_ok=True)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUN_DIR = os.path.join(BASE_DIR, "run")
IMAGES_DIR = os.path.join(RUN_DIR, "images")
VIDEOS_DIR = os.path.join(RUN_DIR, "videos")
os.makedirs(IMAGES_DIR, exist_ok=True)
os.makedirs(VIDEOS_DIR, exist_ok=True)

# Supabase config
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_KEY")
HEADERS = {"Authorization": f"Bearer {SUPABASE_KEY}", "apikey": SUPABASE_KEY} if (SUPABASE_URL and SUPABASE_KEY) else None
ARGO_BUCKET = "argo"

# Device ID
ID_FILE = "../../device_id.txt"
if os.path.exists(ID_FILE):
    DEVICE_ID = open(ID_FILE).read().strip()
else:
    DEVICE_ID = "unknown-device"


def sanitize_key_component(s: str) -> str:
    # allow alnum, dash, underscore, dot; replace others (incl curly quotes) with '_'
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
    return "".join(ch if ch in allowed else "_" for ch in s)

# Capture and encoding parameters (env-tunable)
CAPTURE_INTERVAL_S = float(os.getenv("CAPTURE_INTERVAL_S", "1"))
JPEG_TARGET_KB = int(os.getenv("JPEG_TARGET_KB", "100"))
JPEG_MAX_DIM = int(os.getenv("JPEG_MAX_DIM", "1280")) if os.getenv("JPEG_MAX_DIM") else None
JPEG_PROGRESSIVE = os.getenv("JPEG_PROGRESSIVE", "1") != "0"
VIDEO_FPS = int(os.getenv("VIDEO_FPS", "10"))
CAMERA_ID = os.getenv("CAMERA_ID", "0")


def log_event(event: str, **fields):
    try:
        payload = {"event": event, "ts": datetime.now(timezone.utc).isoformat(), **fields}
        print(json.dumps(payload, ensure_ascii=False))
    except Exception:
        print(f"{event}: {fields}")


# --- JPEG compression utilities (provided) ---
def resize_if_needed(im: Image.Image, max_dim: int | None) -> Image.Image:
    if not max_dim:
        return im
    w, h = im.size
    scale = min(1.0, float(max_dim) / max(w, h))
    if scale < 1.0:
        new_size = (int(w * scale), int(h * scale))
        im = im.resize(new_size, Image.LANCZOS)
    return im


def _jpeg_params(quality: int, progressive: bool) -> dict:
    return {
        "format": "JPEG",
        "quality": max(1, min(95, quality)),
        "progressive": progressive,
        "optimize": True,
        "subsampling": "4:2:0",
    }


def _encode_jpeg(im: Image.Image, quality: int, progressive: bool) -> bytes:
    out_im = im if im.mode in ("RGB", "L") else im.convert("RGB")
    buf = io.BytesIO()
    out_im.save(buf, **_jpeg_params(quality, progressive))
    return buf.getvalue()


def _find_quality_for_target(im: Image.Image, target_kb: int, tolerance: float,
                             qmin: int, qmax: int, progressive: bool) -> tuple[int, bytes]:
    target_bytes = target_kb * 1024
    best_q, best_bytes = qmin, None
    lo, hi = qmin, qmax
    for _ in range(12):
        mid = (lo + hi) // 2
        data = _encode_jpeg(im, mid, progressive)
        size = len(data)
        best_q, best_bytes = mid, data
        if abs(size - target_bytes) / target_bytes <= 0.07:
            break
        if size > target_bytes:
            hi = mid - 1
        else:
            lo = mid + 1
    return best_q, best_bytes  # type: ignore[return-value]


def compress_jpeg(
    image: Image.Image,
    *,
    max_dim: int | None = None,
    target_kb: int | None = None,
    quality: int = 100,
    tolerance: float = 0.07,
    progressive: bool = True,
) -> bytes:
    im = resize_if_needed(image, max_dim)
    qmin, qmax = 30, 92
    if target_kb:
        _, data = _find_quality_for_target(im, int(target_kb), float(tolerance), qmin, qmax, progressive)
        return data
    quality = max(qmin, min(qmax, quality))
    return _encode_jpeg(im, quality, progressive)


# --- Helpers for segmenting time into 15-minute blocks ---
def floor_to_15min(dt: datetime) -> datetime:
    minute = (dt.minute // 15) * 15
    return dt.replace(minute=minute, second=0, microsecond=0)


def segment_id_from_dt(dt: datetime) -> str:
    return floor_to_15min(dt).strftime("%Y%m%dT%H%M%SZ")


def segment_dir(seg_id: str) -> str:
    d = os.path.join(IMAGES_DIR, seg_id)
    os.makedirs(d, exist_ok=True)
    return d


def capture_one(camera_id: str, dest_dir: str) -> str | None:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    raw_path = os.path.join(TMPDIR, f"photo_{ts}_raw.jpg")
    out_path = os.path.join(dest_dir, f"photo_{ts}.jpg")
    try:
        subprocess.run(
            ["termux-camera-photo", "-c", str(camera_id), raw_path],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
    except Exception as e:
        log_event("capture_error", error=str(e))
        return None

    try:
        with Image.open(raw_path) as im:
            data = compress_jpeg(
                im,
                max_dim=JPEG_MAX_DIM,
                target_kb=JPEG_TARGET_KB,
                progressive=JPEG_PROGRESSIVE,
            )
        with open(out_path, "wb") as f:
            f.write(data)
        return out_path
    except Exception as e:
        log_event("compress_error", error=str(e))
        return None
    finally:
        try:
            os.remove(raw_path)
        except Exception:
            pass


    # (record mode removed; photo mode only)


def images_to_mp4(images: list[str], out_mp4: str, fps: int) -> bool:
    if not images:
        return False
    # Read first to get size
    first = cv2.imread(images[0])
    if first is None:
        return False
    height, width = first.shape[:2]
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(out_mp4, fourcc, float(fps), (width, height))
    if not writer.isOpened():
        return False

    try:
        for p in images:
            frame = cv2.imread(p)
            if frame is None:
                continue
            if frame.shape[1] != width or frame.shape[0] != height:
                frame = cv2.resize(frame, (width, height))
            writer.write(frame)
    finally:
        writer.release()
    return True


def upload_file(path: str, bucket: str, key: str, content_type: str) -> str:
    if not HEADERS or not SUPABASE_URL:
        raise RuntimeError("Supabase env missing: SUPABASE_URL and SUPABASE_ANON_KEY")
    url = f"{SUPABASE_URL}/storage/v1/object/{bucket}/{key}"
    with open(path, "rb") as f:
        r = requests.post(
            url,
            headers={**HEADERS, "x-upsert": "true", "Content-Type": content_type},
            data=f.read(),
            timeout=60,
        )
    if not r.ok:
        try:
            body = r.text
        except Exception:
            body = ""
        log_event("upload_http_error", status=r.status_code, url=url, body=body)
    r.raise_for_status()
    return f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{key}"


def finalize_and_upload_segment(seg_id: str) -> None:
    seg_path = os.path.join(IMAGES_DIR, seg_id)
    if not os.path.isdir(seg_path):
        return
    images = [os.path.join(seg_path, f) for f in sorted(os.listdir(seg_path)) if f.lower().endswith(".jpg")]
    if len(images) == 0:
        try:
            shutil.rmtree(seg_path)
        except Exception:
            pass
        return

    mp4_name = f"video_{seg_id}.mp4"
    mp4_path = os.path.join(VIDEOS_DIR, mp4_name)

    ok = images_to_mp4(images, mp4_path, VIDEO_FPS)
    if not ok:
        log_event("video_encode_failed", segment=seg_id)
        return

    # Upload under same place images would be: DEVICE_ID/YYYY/mm/dd/HH/
    seg_dt = datetime.strptime(seg_id, "%Y%m%dT%H%M%SZ")
    safe_device = sanitize_key_component(DEVICE_ID)
    key = f"{safe_device}/{seg_dt:%Y/%m/%d/%H}/{mp4_name}"
    try:
        url = upload_file(mp4_path, ARGO_BUCKET, key, "video/mp4")
        log_event("video_upload_success", url=url, key=key, segment=seg_id)
    except Exception as e:
        log_event("video_upload_error", error=str(e), key=key, segment=seg_id)
        return
    # Cleanup images and local mp4 (outside try so failures don’t mask upload issues)
    try:
        shutil.rmtree(seg_path)
    except Exception:
        pass
    try:
        os.remove(mp4_path)
    except Exception:
        pass


def main():
    current_seg = segment_id_from_dt(datetime.now(timezone.utc))
    seg_dir = segment_dir(current_seg)
    encoder = ThreadPoolExecutor(max_workers=1)
    capture_pool = ThreadPoolExecutor(max_workers=4)
    last_submit = 0.0

    while True:
        # finalize previous segment on boundary
        now = datetime.now(timezone.utc)
        seg_now = segment_id_from_dt(now)
        if seg_now != current_seg:
            prev = current_seg
            encoder.submit(finalize_and_upload_segment, prev)
            current_seg = seg_now
            seg_dir = segment_dir(current_seg)

        t = time.time()
        if t - last_submit >= CAPTURE_INTERVAL_S:
            capture_pool.submit(capture_one, CAMERA_ID, seg_dir)
            last_submit = t

        time.sleep(0.05)


if __name__ == "__main__":
    main()
