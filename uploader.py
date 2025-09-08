#!/usr/bin/env python3
import os, json, time, subprocess, logging, requests, pathlib, argparse, shutil
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# --- Config (env must be set) ---
SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_ANON_KEY"]
BUCKET       = os.environ["SUPABASE_BUCKET"]
TABLE        = os.environ.get("SUPABASE_TABLE", "phone_metrics")
GO_API_PORT  = os.environ.get("GO_API_PORT", "8787")

HEADERS = {"Authorization": f"Bearer {SUPABASE_KEY}", "apikey": SUPABASE_KEY}

HOME   = os.path.expanduser("~")
STATE_DIR = os.environ.get("STATE_DIR", os.path.join(HOME, ".local/state/phone-metrics"))
ERROR_DIR = os.environ.get("ERROR_DIR", os.path.join(STATE_DIR, "errors"))
SENT_ERROR_DIR = os.environ.get("SENT_ERROR_DIR", os.path.join(ERROR_DIR, "sent"))
TMPDIR  = os.environ.get("TMPDIR", os.path.join(HOME, ".cache/phone-metrics"))
pathlib.Path(TMPDIR).mkdir(parents=True, exist_ok=True)
pathlib.Path(SENT_ERROR_DIR).mkdir(parents=True, exist_ok=True)

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

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def http_get_metrics() -> Dict[str, Any]:
    url = f"http://127.0.0.1:{GO_API_PORT}/metrics"
    try:
        r = requests.get(url, timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error(f"/metrics failed: {e}")
        # Minimal payload so we still write a row
        return {"device_id": DEVICE_ID, "ts": now_iso(), "battery": {}, "cpu": {}, "storage": {}, "net": {}}

def safe_round(val: Any, ndigits: int) -> Optional[float]:
    try:
        if val is None: return None
        return round(float(val), ndigits)
    except Exception:
        return None

def clamp_pct(val: Any) -> Optional[float]:
    try:
        v = float(val)
        if v < 0: v = 0.0
        if v > 100: v = 100.0
        return round(v, 2)
    except Exception:
        return None

def extract_typed_fields(metrics: Dict[str, Any]) -> Tuple[str, Optional[int], Optional[bool], Optional[float],
                                                          Optional[float], Optional[float],
                                                          Optional[int], Optional[int], Optional[int],
                                                          Optional[float], Optional[str], Optional[bool]]:
    """
    Map metrics JSON -> table columns:
      ts, percentage, charging, temperature_c,
      cpu_percent, cpu_temperature_c,
      storage_total_bytes, storage_free_bytes, storage_used_bytes, storage_percent_used,
      ssid, hotspot_on
    """
    ts = metrics.get("ts") or now_iso()

    bat = metrics.get("battery") or {}
    pct = bat.get("percent", bat.get("percentage"))
    try:
        percentage = None if pct is None else max(0, min(100, int(round(float(pct)))))
    except Exception:
        percentage = None

    charging = bat.get("charging")
    if charging is None:
        plugged = str(bat.get("plugged", "")).upper()
        charging = plugged != "" and plugged != "UNPLUGGED"

    temperature_c = bat.get("temp_c", bat.get("temperature"))
    temperature_c = safe_round(temperature_c, 1)

    cpu_obj = metrics.get("cpu") or {}
    cpu_percent = clamp_pct(cpu_obj.get("percent"))

    temps = metrics.get("temps") or {}
    cpu_temperature_c = safe_round(temps.get("cpu_c"), 1)

    st = metrics.get("storage") or {}
    storage_total_bytes = st.get("total_bytes")
    storage_free_bytes  = st.get("free_bytes")
    storage_used_bytes  = st.get("used_bytes")
    try:
        storage_percent_used = clamp_pct(st.get("percent_used"))
    except Exception:
        storage_percent_used = None

    net = metrics.get("net") or {}
    ssid = net.get("ssid")
    hotspot_on = net.get("hotspot_on")

    return (ts, percentage, charging, temperature_c,
            cpu_percent, cpu_temperature_c,
            to_int(storage_total_bytes),
            to_int(storage_free_bytes),
            to_int(storage_used_bytes),
            storage_percent_used, ssid, bool_or_none(hotspot_on))

def to_int(x: Any) -> Optional[int]:
    try:
        if x is None: return None
        return int(x)
    except Exception:
        return None

def bool_or_none(x: Any) -> Optional[bool]:
    if isinstance(x, bool): return x
    if x in (None, "null", ""): return None
    s = str(x).strip().lower()
    if s in ("true", "1", "yes", "on"): return True
    if s in ("false", "0", "no", "off"): return False
    return None

def collect_errors() -> List[Dict[str, Any]]:
    errs: List[Dict[str, Any]] = []
    try:
        for p in sorted(pathlib.Path(ERROR_DIR).glob("*.json")):
            try:
                data = json.loads(p.read_text())
                errs.append(data)
                dest = pathlib.Path(SENT_ERROR_DIR) / p.name
                shutil.move(str(p), str(dest))
            except Exception as e:
                log.warning(f"error reading {p}: {e}")
    except FileNotFoundError:
        pass
    return errs

def termux_camera_photo(camera_id: str):
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    img_path = os.path.join(TMPDIR, f"photo_{ts}.jpg")
    try:
        subprocess.check_call(["termux-camera-photo", "-c", str(camera_id), img_path])
        if os.path.exists(img_path) and os.path.getsize(img_path) > 0:
            return img_path
    except Exception as e:
        log.warning(f"camera failed: {e}")
    return None

def upload_image(img_path: str) -> str:
    now = datetime.now(timezone.utc)
    key = f"{DEVICE_ID}/{now:%Y/%m/%d/%H}/{os.path.basename(img_path)}"
    url = f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{key}"
    with open(img_path, "rb") as f:
        r = requests.post(url, headers={**HEADERS, "x-upsert": "true", "Content-Type": "image/jpeg"}, data=f.read())
    r.raise_for_status()
    return f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET}/{key}"

def insert_row(payload: Dict[str, Any]) -> None:
    url = f"{SUPABASE_URL}/rest/v1/{TABLE}"
    r = requests.post(url, headers={**HEADERS, "Content-Type": "application/json", "Prefer": "return=minimal"}, json=payload)
    r.raise_for_status()

def keep_alive():
    subprocess.run(["termux-wake-lock"])
    subprocess.run(["sshd"])

def loop(sleep_seconds=60, camera_id="0"):
    log.info(f"start device_id={DEVICE_ID} table={TABLE} bucket={BUCKET} sleep={sleep_seconds}s")
    while True:
        keep_alive()

        metrics = http_get_metrics()
        errs = collect_errors()

        ts, percentage, charging, temperature_c, \
        cpu_percent, cpu_temperature_c, \
        storage_total_bytes, storage_free_bytes, storage_used_bytes, \
        storage_percent_used, ssid, hotspot_on = extract_typed_fields(metrics)

        img_url = None
        img_path = termux_camera_photo(camera_id)
        if img_path:
            try:
                img_url = upload_image(img_path)
                log.info(f"uploaded -> {img_url}")
            except Exception as e:
                errs.append({"ts": now_iso(), "type": "upload", "reason": str(e)})
                log.error(f"upload failed: {e}")
            finally:
                try: os.remove(img_path)
                except Exception: pass
        else:
            log.warning("no image captured")

        row = {
            "device_id": DEVICE_ID,
            "ts": ts,
            "percentage": percentage,
            "charging": charging,
            "temperature_c": temperature_c,
            "image_url": img_url,

            "cpu_percent": cpu_percent,
            "cpu_temperature_c": cpu_temperature_c,

            "storage_total_bytes": storage_total_bytes,
            "storage_free_bytes":  storage_free_bytes,
            "storage_used_bytes":  storage_used_bytes,
            "storage_percent_used": storage_percent_used,

            "ssid": ssid,
            "hotspot_on": hotspot_on,

            "metrics": metrics,
            "errors": errs,
        }

        try:
            insert_row(row)
            log.info("row inserted")
        except Exception as e:
            log.error(f"insert failed: {e}")

        time.sleep(sleep_seconds)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Phone metrics uploader")
    parser.add_argument("--sleep", "-s", type=int, default=int(os.environ.get("CHECK_INTERVAL","60")),
                       help="Sleep duration in seconds between uploads (default: 60)")
    parser.add_argument("--camera", "-c", default=os.environ.get("CAMERA_ID","0"),
                       help="Camera id for termux-camera-photo (default: 0)")
    args = parser.parse_args()
    loop(args.sleep, args.camera)
