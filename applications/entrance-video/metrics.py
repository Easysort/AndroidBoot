import json
import os
import pathlib
import subprocess
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional


HOME = os.path.expanduser("~")
TMPDIR = os.environ.get("TMPDIR", os.path.join(HOME, ".cache/phone-metrics"))
pathlib.Path(TMPDIR).mkdir(parents=True, exist_ok=True)

HOST = os.environ.get("HEALTH_HOST", "0.0.0.0")
PORT = int(os.environ.get("HEALTH_PORT", "5000"))

MAX_CPU_TEMP_C = float(os.environ.get("MAX_CPU_TEMP_C", "85"))
MAX_BATTERY_TEMP_C = float(os.environ.get("MAX_BATTERY_TEMP_C", "50"))
MAX_STORAGE_PERCENT = float(os.environ.get("MAX_STORAGE_PERCENT", "95"))


def sh(args: List[str], timeout: int = 5) -> str:
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
        for root, _, files in os.walk(base):
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


def storage_info(path: Optional[str] = None) -> Dict[str, Any]:
    if not path:
        path = HOME or "/"
    try:
        s = os.statvfs(path)
        bsize = s.f_frsize or s.f_bsize or 4096
        total = int(bsize * s.f_blocks)
        free = int(bsize * s.f_bavail)
        used = max(0, total - free)
        pu = (used / total) * 100.0 if total > 0 else 0.0
        return {
            "path": path,
            "total_bytes": total,
            "free_bytes": free,
            "used_bytes": used,
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
    out = sh(["dumpsys", "tethering"], timeout=4) or sh(
        [
            "dumpsys",
            "connectivity",
            "tethering",
        ],
        timeout=4,
    )
    if not out:
        return None
    l = out.lower()
    hints = ("tethered", "softap", "wifi tether", "ap: started", "started", "enabled")
    return any(h in l for h in hints)


def tmux_sessions() -> Dict[str, Any]:
    out = sh(["tmux", "ls"], timeout=3)
    if not out:
        return {"running": False, "sessions": []}
    sessions = []
    for line in out.splitlines():
        if ":" in line:
            sessions.append(line.split(":", 1)[0].strip())
    return {"running": len(sessions) > 0, "sessions": sessions}


def collect_health() -> Dict[str, Any]:
    errors: List[str] = []

    battery = termux_battery()
    pct = battery.get("percentage", battery.get("percent"))
    try:
        percentage = None if pct is None else max(0, min(100, int(round(float(pct)))))
    except Exception:
        percentage = None
        errors.append("battery_percent_unavailable")

    plugged = str(battery.get("plugged", "")).upper()
    charging = battery.get("charging")
    if charging is None:
        charging = plugged != "" and plugged != "UNPLUGGED"

    temperature_c = battery.get("temperature", battery.get("temp_c"))
    try:
        battery_temp_c = (
            None if temperature_c is None else round(float(temperature_c), 1)
        )
    except Exception:
        battery_temp_c = None
        errors.append("battery_temp_unavailable")

    cpu_p = cpu_percent()
    cpu_t = cpu_temperature_c()
    if cpu_t is None:
        errors.append("cpu_temp_unavailable")

    st = storage_info()
    ssid = wifi_ssid()
    hot = hotspot_likely_on()
    tmux = tmux_sessions()

    temps_ok = True
    if cpu_t is not None and cpu_t >= MAX_CPU_TEMP_C:
        temps_ok = False
    if battery_temp_c is not None and battery_temp_c >= MAX_BATTERY_TEMP_C:
        temps_ok = False

    storage_ok = True
    percent_used = st.get("percent_used")
    if isinstance(percent_used, (int, float)) and percent_used >= MAX_STORAGE_PERCENT:
        storage_ok = False

    tmux_running = tmux.get("running", False)
    healthy = tmux_running or (temps_ok and storage_ok and not errors)

    return {
        "healthy": healthy,
        "checks": {
            "temps_ok": temps_ok,
            "storage_ok": storage_ok,
            "tmux_running": tmux_running,
        },
        "temps": {
            "battery_c": battery_temp_c,
            "cpu_c": cpu_t,
        },
        "battery": {
            "percentage": percentage,
            "charging": charging,
            "plugged": plugged,
        },
        "cpu": {"percent": cpu_p},
        "storage": st,
        "net": {"ssid": ssid, "hotspot_on": hot},
        "tmux": tmux,
        "errors": errors,
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


class HealthHandler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode(
            "utf-8"
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.rstrip("/") == "" or self.path.rstrip("/") == "/health":
            payload = collect_health()
            status = 200 if payload.get("healthy") else 503
            self._send_json(status, payload)
            return
        self._send_json(404, {"error": "not_found", "path": self.path})

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    server = HTTPServer((HOST, PORT), HealthHandler)
    print(json.dumps({"event": "health_server_started", "host": HOST, "port": PORT}))
    server.serve_forever()


if __name__ == "__main__":
    main()
