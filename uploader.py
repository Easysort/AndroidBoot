#!/usr/bin/env python3
import os, json, time, subprocess, logging, requests, pathlib
from datetime import datetime, UTC
import subprocess

# --- Config (env must be set) ---
SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_ANON_KEY"]
BUCKET       = os.environ["SUPABASE_BUCKET"]
TABLE        = os.environ["SUPABASE_TABLE"]

HEADERS = {"Authorization": f"Bearer {SUPABASE_KEY}", "apikey": SUPABASE_KEY}
TMPDIR  = "/data/data/com.termux/files/home/.cache/phone-metrics"
pathlib.Path(TMPDIR).mkdir(parents=True, exist_ok=True)

# Stable device id (required)
ID_FILE = "device_id.txt"
if os.path.exists(ID_FILE):
    DEVICE_ID = open(ID_FILE).read().strip()
else:
    raise RuntimeError(f"Device ID file not found: {ID_FILE}\n"
                       f"Create one, e.g.:  echo my-phone-id > {ID_FILE}")

# --- Logging ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("uploader")

def termux_battery():
    try:
        out = subprocess.check_output(["termux-battery-status"], text=True)
        b   = json.loads(out)
        pct = b.get("percentage")
        temp = b.get("temperature")  # °C
        plugged = b.get("plugged")
        charging = bool(plugged and plugged != "UNPLUGGED")
        return pct, charging, temp
    except Exception as e:
        log.warning(f"battery read failed: {e}")
        return None, None, None

def take_photo():
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    img_path = os.path.join(TMPDIR, f"photo_{ts}.jpg")
    try:
        subprocess.check_call(["termux-camera-photo", "-c", "0", img_path])
        if os.path.exists(img_path) and os.path.getsize(img_path) > 0:
            return img_path
    except Exception as e:
        log.warning(f"camera failed: {e}")
    return None

def upload_image(img_path):
    now = datetime.now(UTC)
    key = f"{DEVICE_ID}/{now:%Y/%m/%d/%H}/{os.path.basename(img_path)}"
    url = f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{key}"
    with open(img_path, "rb") as f:
        r = requests.post(url, headers={**HEADERS, "x-upsert": "true", "Content-Type": "image/jpeg"}, data=f.read())
    r.raise_for_status()
    public_url = f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET}/{key}"
    return public_url

def insert_row(ts_iso, pct, charging, temp_c, image_url):
    url = f"{SUPABASE_URL}/rest/v1/{TABLE}"
    row = {
        "device_id": DEVICE_ID,
        "ts": ts_iso,
        "percentage": pct,
        "charging": charging,
        "temperature_c": temp_c,
        "image_url": image_url,
    }
    r = requests.post(url, headers={**HEADERS, "Content-Type": "application/json", "Prefer":"return=minimal"}, json=row)
    r.raise_for_status()

def keep_alive():
    subprocess.run(["termux-wake-lock"])
    subprocess.run(["sshd"])

def loop():
    log.info(f"start device_id={DEVICE_ID} bucket={BUCKET} table={TABLE}")
    while True:
        keep_alive()
        ts = datetime.now(UTC).isoformat()
        pct, charging, temp_c = termux_battery()
        log.info(f"battery pct={pct} charging={charging} temp_c={temp_c}")

        img_url = None
        img_path = take_photo()
        if img_path:
            try:
                size = os.path.getsize(img_path)
                log.info(f"captured {os.path.basename(img_path)} bytes={size}")
                img_url = upload_image(img_path)
                log.info(f"uploaded -> {img_url}")
            except Exception as e:
                log.error(f"upload failed: {e}")
            finally:
                try: os.remove(img_path)
                except Exception: pass
            # keep only last 50 local photos (should be none after delete, but in case)
            photos = sorted([p for p in pathlib.Path(TMPDIR).glob("photo_*.jpg")])
            for p in photos[:-50]:
                try: p.unlink(missing_ok=True)
                except Exception: pass
        else:
            log.warning("no image captured")

        try:
            insert_row(ts, pct, charging, temp_c, img_url)
            log.info("row inserted")
        except Exception as e:
            log.error(f"insert failed: {e}")

        left = 60
        while True:
            time.sleep(1)
            left -= 1
            print(f"left: {left}")
            if left <= 0:
                break

if __name__ == "__main__":
    loop()
