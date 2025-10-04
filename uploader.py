#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Phone metrics uploader (Python-only, no Go API)
- Collects: battery %, charging, battery temp
           CPU % and CPU temperature
           Storage totals/free/used and %
           Wi-Fi SSID and hotspot_on heuristic
- Captures a photo, uploads to Supabase Storage, inserts a row into public.phone_metrics
- Embeds full metrics JSON + any watchdog errors into the row

Requirements on phone (Termux):
  pkg i -y termux-api jq coreutils openssh python
  (and grant Termux:API permissions: battery, camera, Wi-Fi)
"""

import os, json, time, subprocess, logging, requests, pathlib, argparse, shutil, math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from dotenv import load_dotenv

load_dotenv()

# --- Config (env must be set) ---
SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_ANON_KEY"]
BUCKET       = os.environ["SUPABASE_BUCKET"]
TABLE        = os.environ.get("SUPABASE_TABLE", "phone_metrics")
NIGHT_TIME_SLEEP_SECONDS = os.environ.get("NIGHT_TIME_SLEEP_SECONDS", 60 * 15) # 15 minutes

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

UTC = timezone.utc

# ----------------- Shell helpers -----------------

def has_cmd(name: str) -> bool:
    return shutil.which(name) is not None

def sh(args: List[str], timeout: int = 5, root: bool = False) -> str:
    """
    Run a command and return stdout (stripped). If root=True and 'su' exists, run via su -c.
    """
    try:
        if root and has_cmd("su"):
            # Join and escape double-quotes minimally
            cmd = " ".join([subprocess.list2cmdline([a]) for a in args])
            out = subprocess.check_output(["su", "-c", cmd], stderr=subprocess.STDOUT, timeout=timeout)
        else:
            out = subprocess.check_output(args, stderr=subprocess.STDOUT, timeout=timeout)
        return out.decode("utf-8", "replace").strip()
    except Exception as e:
        return ""

def now_iso() -> str:
    return datetime.now(UTC).isoformat()

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

# ----------------- Metric collectors (Python-only) -----------------

def termux_battery() -> Dict[str, Any]:
    """
    Uses termux-battery-status (JSON).
    Returns a dict with keys: percentage, temperature, plugged, status, health, etc.
    """
    try:
        out = sh(["termux-battery-status"], timeout=4)
        if not out: return {}
        b = json.loads(out)
        # Normalize keys expected by downstream
        plugged = str(b.get("plugged", "")).upper()
        charging = plugged != "" and plugged != "UNPLUGGED"
        return {
            "raw": b,
            "percentage": b.get("percentage"),
            "percent": b.get("percentage"),
            "temperature": b.get("temperature"),
            "temp_c": b.get("temperature"),
            "plugged": b.get("plugged"),
            "status": b.get("status"),
            "health": b.get("health"),
            "charging": charging,
        }
    except Exception:
        return {}

def read_proc_stat() -> Tuple[int, int]:
    """
    Returns (total, idle) jiffies from /proc/stat.
    """
    try:
        with open("/proc/stat", "r") as f:
            for ln in f:
                if ln.startswith("cpu "):
                    parts = ln.split()[1:]
                    vals = [int(p) for p in parts[:8]]  # user,nice,system,idle,iowait,irq,softirq,steal
                    total = sum(vals)
                    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
                    return total, idle
    except Exception:
        pass
    return 0, 0

def cpu_percent(sample_ms: int = 250) -> float:
    a_tot, a_idle = read_proc_stat()
    time.sleep(sample_ms / 1000.0)
    b_tot, b_idle = read_proc_stat()
    d_tot = b_tot - a_tot
    d_idle = b_idle - a_idle
    if d_tot <= 0:
        return 0.0
    p = (1.0 - (d_idle / float(d_tot))) * 100.0
    p = max(0.0, min(100.0, p))
    return round(p, 1)

def cpu_temperature_c() -> Optional[float]:
    """
    Scan /sys/class/thermal/**/type for CPU-like zones and read their temp files.
    Return the hottest (°C). If none found, return None.
    """
    base = "/sys/class/thermal"
    best = None
    try:
        for root, dirs, files in os.walk(base):
            if os.path.basename(root).startswith("thermal_zone") and "type" in files:
                try:
                    typ = open(os.path.join(root, "type")).read().strip().lower()
                except Exception:
                    continue
                if not any(k in typ for k in ("cpu", "soc", "tsens")):
                    continue
                tpath = os.path.join(root, "temp")
                try:
                    s = open(tpath).read().strip()
                    val = float(s)
                    if val > 200:  # usually milli-deg C
                        val = val / 1000.0
                    if best is None or val > best:
                        best = val
                except Exception:
                    continue
    except Exception:
        pass
    return round(best, 1) if best is not None else None

def storage_info(path: str = None) -> Dict[str, Any]:
    """
    Use statvfs (works in Termux). Default path = $HOME.
    """
    if not path:
        path = HOME or "/"
    try:
        s = os.statvfs(path)
        bsize = s.f_frsize or s.f_bsize or 4096
        total = int(bsize * s.f_blocks)
        free  = int(bsize * s.f_bavail)
        used  = max(0, total - free)
        pu = (used / total) * 100.0 if total > 0 else 0.0
        return {
            "path": path,
            "total_bytes": total,
            "free_bytes":  free,
            "used_bytes":  used,
            "percent_used": round(pu, 1),
        }
    except Exception as e:
        return {"error": str(e)}

def wifi_ssid() -> Optional[str]:
    try:
        out = sh(["termux-wifi-connectioninfo"], timeout=4)
        if not out: return None
        m = json.loads(out)
        ssid = m.get("ssid")
        if isinstance(ssid, str) and ssid:
            return ssid
    except Exception:
        pass
    return None

def hotspot_likely_on() -> Optional[bool]:
    """
    Heuristic: parse dumpsys tethering/ connectivity for tethered/SoftAp indicators.
    Works best on rooted phones; falls back to user-space output if available.
    """
    # try with su first (more privileges on some builds)
    out = sh(["dumpsys", "connectivity", "tethering"], timeout=4, root=True)
    if not out:
        out = sh(["dumpsys", "tethering"], timeout=4, root=True)
    if not out:
        out = sh(["dumpsys", "connectivity", "tethering"], timeout=4, root=False) or sh(["dumpsys", "tethering"], timeout=4, root=False)
    if not out:
        return None
    l = out.lower()
    hints = ("tethered", "softap", "wifi tether", "ap: started", "started", "enabled")
    return any(h in l for h in hints)

def gather_metrics() -> Dict[str, Any]:
    bat = termux_battery()
    cpu_p = cpu_percent()
    cpu_t = cpu_temperature_c()
    st   = storage_info()
    ssid = wifi_ssid()
    hot  = hotspot_likely_on()

    return {
        "device_id": DEVICE_ID,
        "ts": now_iso(),
        "battery": bat,
        "cpu": {"percent": cpu_p},
        "temps": {"battery_c": bat.get("temp_c"), "cpu_c": cpu_t},
        "storage": st,
        "net": {"ssid": ssid, "hotspot_on": hot},
    }

# ----------------- Pipeline helpers -----------------

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
    cpu_percent_v = clamp_pct(cpu_obj.get("percent"))

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
            cpu_percent_v, cpu_temperature_c,
            to_int(storage_total_bytes),
            to_int(storage_free_bytes),
            to_int(storage_used_bytes),
            storage_percent_used, ssid, bool_or_none(hotspot_on))

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

def termux_camera_photo(camera_id: str) -> Optional[str]:
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    img_path = os.path.join(TMPDIR, f"photo_{ts}.jpg")
    try:
        subprocess.check_call(["termux-camera-photo", "-c", str(camera_id), img_path])
        if os.path.exists(img_path) and os.path.getsize(img_path) > 0:
            return img_path
    except Exception as e:
        log.warning(f"camera failed: {e}")
    return None

def upload_image(img_path: str) -> str:
    now = datetime.now(UTC)
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

def is_night_time():
    now = datetime.now(UTC)
    return now.hour >= 17 or now.hour <= 6

# ----------------- Main loop -----------------

def loop(sleep_seconds=60, night_time_sleep_seconds=900, camera_id="0"):
    log.info(f"start device_id={DEVICE_ID} table={TABLE} bucket={BUCKET} sleep={sleep_seconds}s night_time_sleep={night_time_sleep_seconds}")
    while True:
        keep_alive()

        metrics = gather_metrics()
        errs = collect_errors()

        ts, percentage, charging, temperature_c, \
        cpu_percent_v, cpu_temperature_c, \
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

            "cpu_percent": cpu_percent_v,
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

        if is_night_time():
            time.sleep(night_time_sleep_seconds)
        else:
            time.sleep(sleep_seconds)

# ----------------- CLI -----------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Phone metrics uploader (Python-only)")
    parser.add_argument("--sleep", "-s", type=int, default=int(os.environ.get("CHECK_INTERVAL","60")),
                       help="Sleep duration in seconds between uploads (default: 60)")
    parser.add_argument("--night_time_sleep", "-n", type=int, default=NIGHT_TIME_SLEEP_SECONDS,
                       help="Sleep duration in seconds between uploads during night time (17-7) (default: 15 minutes)")
    parser.add_argument("--camera", "-c", default=os.environ.get("CAMERA_ID","0"),
                       help="Camera id for termux-camera-photo (default: 0)")
    args = parser.parse_args()
    loop(args.sleep, args.night_time_sleep, args.camera)
