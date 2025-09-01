#!/usr/bin/env python3
import os, json, time, uuid, subprocess, datetime, requests, pathlib

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_ANON_KEY"]
BUCKET       = os.environ["SUPABASE_BUCKET"]
TABLE        = os.environ["SUPABASE_TABLE"]

HEADERS = {
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "apikey": SUPABASE_KEY,
}
TMPDIR  = "/data/data/com.termux/files/home/.cache/phone-metrics"
pathlib.Path(TMPDIR).mkdir(parents=True, exist_ok=True)

# Stable device id (persisted)
ID_FILE = "device_id.txt"
if os.path.exists(ID_FILE):
    DEVICE_ID = open(ID_FILE).read().strip()
else:
    raise Exception("Device ID file not found")

def termux_battery():
    try:
        out = subprocess.check_output(["termux-battery-status"], text=True)
        b   = json.loads(out)
        pct = b.get("percentage")
        temp = b.get("temperature")  # already °C in Termux:API
        plugged = b.get("plugged")
        charging = plugged and plugged != "UNPLUGGED"
        return pct, charging, temp
    except Exception as e:
        return None, None, None

def take_photo():
    ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    img_path = os.path.join(TMPDIR, f"photo_{ts}.jpg")
    # -c 0 tries back camera; use -c 1 for front if needed
    try:
        subprocess.check_call(["termux-camera-photo", "-c", "0", img_path])
        return img_path
    except Exception:
        return None

def upload_image(img_path):
    now = datetime.datetime.utcnow()
    key = f"{DEVICE_ID}/{now:%Y/%m/%d}/{os.path.basename(img_path)}"
    url = f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{key}"
    with open(img_path, "rb") as f:
        r = requests.post(
            url,
            headers={**HEADERS, "x-upsert": "true", "Content-Type": "image/jpeg"},
            data=f.read(),
        )
    r.raise_for_status()
    return f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET}/{key}"

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

def loop():
    while True:
        ts = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc).isoformat()
        pct, charging, temp_c = termux_battery()
        img_path = take_photo()
        img_url = None
        if img_path:
            try:
                img_url = upload_image(img_path)
                # keep last 50 photos locally, delete older
                photos = sorted([p for p in pathlib.Path(TMPDIR).glob("photo_*.jpg")])
                for p in photos[:-50]:
                    p.unlink(missing_ok=True)
            except Exception as e:
                img_url = None
        try:
            insert_row(ts, pct, charging, temp_c, img_url)
        except Exception as e:
            # Best-effort; on failure we just log and continue
            print("Insert failed:", e)
        time.sleep(60)

if __name__ == "__main__":
    loop()
