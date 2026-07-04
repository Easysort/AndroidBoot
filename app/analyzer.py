import os
import io
import json
import time
import shutil
import requests
import pathlib
import threading
import subprocess
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor

import cv2
from PIL import Image

from dotenv import load_dotenv


BASE_DIR = pathlib.Path(__file__).resolve().parent
REPO_DIR = pathlib.Path(os.environ.get("REPO_DIR", BASE_DIR.parent)).resolve()
load_dotenv(REPO_DIR / ".env")

TMPDIR = os.path.expanduser("~/tmp")
os.makedirs(TMPDIR, exist_ok=True)

RUN_DIR = os.path.join(str(REPO_DIR), "run")
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
ID_FILE = REPO_DIR / "device_id.txt"
if ID_FILE.exists():
    DEVICE_ID = ID_FILE.read_text().strip()
else:
    DEVICE_ID = "unknown-device"


def sanitize_key_component(s: str) -> str:
    # allow alnum, dash, underscore, dot; replace others (incl curly quotes) with '_'
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
    return "".join(ch if ch in allowed else "_" for ch in s)

# Capture and encoding parameters (env-tunable)
CAPTURE_INTERVAL_S = float(os.getenv("CAPTURE_INTERVAL_S", "1"))
# One termux-camera-photo call takes ~4-7s (open camera, focus, capture, close),
# so ~1 fps is only achievable by overlapping captures.
CAPTURE_WORKERS = int(os.getenv("CAPTURE_WORKERS", "4"))
# A segment is only finalized this long after it ends, so every in-flight
# capture (10s subprocess timeout + compression) has landed in its dir first.
FINALIZE_GRACE_S = float(os.getenv("FINALIZE_GRACE_S", "30"))
SEGMENT_S = 15 * 60
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


def capture_one(camera_id: str) -> str | None:
    # Route the photo into the segment matching its own capture time, so a
    # slow capture can never land in a directory the finalizer already handled.
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y%m%dT%H%M%S%fZ")
    raw_path = os.path.join(TMPDIR, f"photo_{ts}_raw.jpg")
    out_path = os.path.join(segment_dir(segment_id_from_dt(now)), f"photo_{ts}.jpg")
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
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
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


def sweep_finalizable(encoder: ThreadPoolExecutor, submitted: set) -> None:
    """Finalize every segment whose window ended more than FINALIZE_GRACE_S
    ago. Because captures route photos by their own timestamp, once the grace
    period has passed no new file can appear in such a dir. This also picks up
    segments stranded by a crash or reboot."""
    try:
        names = sorted(os.listdir(IMAGES_DIR))
    except Exception as e:
        log_event("segment_scan_error", error=str(e))
        return
    now = datetime.now(timezone.utc)
    for name in names:
        if name in submitted or not os.path.isdir(os.path.join(IMAGES_DIR, name)):
            continue
        try:
            seg_start = datetime.strptime(name, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if now >= seg_start + timedelta(seconds=SEGMENT_S + FINALIZE_GRACE_S):
            submitted.add(name)
            encoder.submit(finalize_and_upload_segment, name)


def main():
    encoder = ThreadPoolExecutor(max_workers=1)
    capture_pool = ThreadPoolExecutor(max_workers=CAPTURE_WORKERS)
    submitted_segments: set = set()

    # Overlapping captures are required for ~1 fps (each call takes ~4-7s), but
    # the camera can't keep up indefinitely, so cap the number in flight rather
    # than letting a backlog of minutes-late captures build up.
    inflight = 0
    inflight_lock = threading.Lock()
    max_inflight = CAPTURE_WORKERS

    def capture_task():
        nonlocal inflight
        try:
            capture_one(CAMERA_ID)
        finally:
            with inflight_lock:
                inflight -= 1

    next_capture = time.time()
    last_sweep = 0.0

    while True:
        t = time.time()

        if t >= next_capture:
            with inflight_lock:
                if inflight < max_inflight:
                    inflight += 1
                    capture_pool.submit(capture_task)
                # else: camera saturated; skip this tick instead of queueing.
            next_capture = max(next_capture + CAPTURE_INTERVAL_S, t)

        if t - last_sweep >= 5.0:
            sweep_finalizable(encoder, submitted_segments)
            last_sweep = t

        time.sleep(0.05)


if __name__ == "__main__":
    main()
