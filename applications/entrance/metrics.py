import os, json, time, subprocess, requests, shutil, pathlib
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from applications.common.env import Env

UTC = timezone.utc

# Env and config (match uploader.py semantics)
SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_ANON_KEY"]
TABLE        = os.environ.get("SUPABASE_TABLE", "phone_metrics")
HEADERS = {"Authorization": f"Bearer {SUPABASE_KEY}", "apikey": SUPABASE_KEY}

HOME = os.path.expanduser("~")
TMPDIR = os.environ.get("TMPDIR", os.path.join(HOME, ".cache/phone-metrics"))
pathlib.Path(TMPDIR).mkdir(parents=True, exist_ok=True)


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def sh(args, timeout: int = 5) -> str:
    try:
        out = subprocess.check_output(args, stderr=subprocess.STDOUT, timeout=timeout)
        return out.decode("utf-8", "replace").strip()
    except Exception:
        return ""


def termux_battery() -> Dict[str, Any]:
    try:
        out = sh(["termux-battery-status"], timeout=4)
        return json.loads(out) if out else {}
    except Exception:
        return {}


def cpu_percent(sample_ms: int = 250) -> float:
    def read_proc_stat():
        try:
            with open("/proc/stat", "r") as f:
                for ln in f:
                    if ln.startswith("cpu "):
                        parts = ln.split()[1:]
                        vals = [int(p) for p in parts[:8]]
                        total = sum(vals)
                        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
                        return total, idle
        except Exception:
            pass
        return 0, 0

    a_tot, a_idle = read_proc_stat()
    time.sleep(sample_ms / 1000.0)
    b_tot, b_idle = read_proc_stat()
    d_tot = b_tot - a_tot
    d_idle = b_idle - a_idle
    if d_tot <= 0:
        return 0.0
    p = (1.0 - (d_idle / float(d_tot))) * 100.0
    return round(max(0.0, min(100.0, p)), 1)


def cpu_temperature_c() -> Optional[float]:
    base = "/sys/class/thermal"
    hottest = None
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
                    if val > 200:
                        val = val / 1000.0
                    if hottest is None or val > hottest:
                        hottest = val
                except Exception:
                    continue
    except Exception:
        pass
    return round(hottest, 1) if hottest is not None else None


def storage_info(path: str = None) -> Dict[str, Any]:
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
        if not out:
            return None
        m = json.loads(out)
        ssid = m.get("ssid")
        return ssid if isinstance(ssid, str) and ssid else None
    except Exception:
        return None


def hotspot_likely_on() -> Optional[bool]:
    out = sh(["dumpsys", "tethering"], timeout=4) or sh(["dumpsys", "connectivity", "tethering"], timeout=4)
    if not out:
        return None
    l = out.lower()
    hints = ("tethered", "softap", "wifi tether", "ap: started", "started", "enabled")
    return any(h in l for h in hints)


def insert_row(payload: Dict[str, Any]) -> None:
    url = f"{SUPABASE_URL}/rest/v1/{TABLE}"
    r = requests.post(url, headers={**HEADERS, "Content-Type": "application/json", "Prefer": "return=minimal"}, json=payload)
    r.raise_for_status()


def main() -> None:
    bat = termux_battery()
    pct = bat.get("percentage", bat.get("percent"))
    try:
        percentage = None if pct is None else max(0, min(100, int(round(float(pct)))))
    except Exception:
        percentage = None

    plugged = str(bat.get("plugged", "")).upper()
    charging = bat.get("charging")
    if charging is None:
        charging = plugged != "" and plugged != "UNPLUGGED"

    temperature_c = bat.get("temperature", bat.get("temp_c"))
    try:
        temperature_c = None if temperature_c is None else round(float(temperature_c), 1)
    except Exception:
        temperature_c = None

    cpu_p = cpu_percent()
    cpu_t = cpu_temperature_c()
    st    = storage_info()
    ssid  = wifi_ssid()
    hot   = hotspot_likely_on()

    metrics = {
        "device_id": Env.DEVICE_ID,
        "ts": now_iso(),
        "battery": bat,
        "cpu": {"percent": cpu_p},
        "temps": {"battery_c": temperature_c, "cpu_c": cpu_t},
        "storage": st,
        "net": {"ssid": ssid, "hotspot_on": hot},
    }

    row = {
        "device_id": Env.DEVICE_ID,
        "ts": metrics["ts"],
        "percentage": percentage,
        "charging": charging,
        "temperature_c": temperature_c,
        "image_url": None,
        "cpu_percent": cpu_p,
        "cpu_temperature_c": cpu_t,
        "storage_total_bytes": st.get("total_bytes"),
        "storage_free_bytes":  st.get("free_bytes"),
        "storage_used_bytes":  st.get("used_bytes"),
        "storage_percent_used": st.get("percent_used"),
        "ssid": ssid,
        "hotspot_on": hot,
        "metrics": metrics,
        "errors": [],
    }

    insert_row(row)
    print(json.dumps({"event": "metrics_uploaded", "ts": metrics["ts"]}))


if __name__ == "__main__":
    while True:
        try:
            main()
        except Exception as e:
            # Best-effort logging to stdout
            try:
                print(json.dumps({"event": "metrics_error", "error": str(e), "ts": now_iso()}))
            except Exception:
                pass
        time.sleep(900)  # 15 minutes

