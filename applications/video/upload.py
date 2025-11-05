import os, json, time, subprocess, requests
from typing import Any, Dict
from applications.common import Env, Helper
import shlex
import shutil


def termux_battery() -> Dict[str, Any]:
    out = subprocess.check_output(["termux-battery-status"], stderr=subprocess.STDOUT, timeout=4).decode("utf-8", "replace").strip()
    return json.loads(out) if out else {}


def cpu_temperature_c() -> float | None:
    base = "/sys/class/thermal"
    hottest = None
    try:
        for root, dirs, files in os.walk(base):
            if os.path.basename(root).startswith("thermal_zone") and "type" in files:
                try: typ = open(os.path.join(root, "type")).read().strip().lower()
                except Exception: continue
                if not any(k in typ for k in ("cpu", "soc", "tsens")): continue
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
    except Exception: pass
    return round(hottest, 1) if hottest is not None else None


def storage_info(path: str = "/") -> Dict[str, Any]:
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


def upload_metrics() -> None:
    bat = termux_battery()
    pct = bat.get("percentage") or bat.get("percent")
    percentage = None if pct is None else max(0, min(100, int(round(float(pct)))))

    plugged = str(bat.get("plugged", "")).upper()
    charging = bat.get("charging")
    if charging is None:
        charging = plugged != "" and plugged != "UNPLUGGED"

    temperature_c = bat.get("temperature", bat.get("temp_c"))
    temperature_c = None if temperature_c is None else round(float(temperature_c), 1)

    cpu_p = 0 # TODO: add CPU percent detection
    cpu_t = cpu_temperature_c()
    st    = storage_info()
    ssid  = None # TODO: add SSID detection
    hot   = None # TODO: add hotspot detection

    row = {
        "device_id": Env.DEVICE_ID,
        "ts": Helper.current_time(),
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
    }

    url = f"{Env.SUPABASE_URL}/rest/v1/{Env.TABLE}"
    print("Metrics send to Supabase: ", row)
    r = requests.post(url, headers={**Env.HEADERS, "Content-Type": "application/json", "Prefer": "return=minimal"}, json=row)
    r.raise_for_status()
    print(json.dumps({"event": "metrics_uploaded", "ts": row["ts"]}))

def compress_images_to_mp4() -> str:
    
    for file in os.listdir(Env.COMPRESSED_DIR): # Clean up old compressed images if any (could be here if error in previous run)
        os.remove(os.path.join(Env.COMPRESSED_DIR, file))

    files = sorted(os.listdir(Env.IMAGES_DIR))
    for i, file in enumerate(files):
        shutil.move(os.path.join(Env.IMAGES_DIR, file), os.path.join(Env.COMPRESSED_DIR, f"{i:06d}.jpg"))

    cmd = f"""
    ffmpeg -v error -stats -y \
    -framerate {16} -i {shlex.quote(Env.COMPRESSED_DIR)}/%06d.jpg \
    -vf "scale=1280:720:force_original_aspect_ratio=decrease,\
    pad=1280:720:(ow-iw)/2:(oh-ih)/2,\
    format=nv12,fps=16" \
    -c:v h264_mediacodec -b:v 2000k -maxrate 2500k -bufsize 5000k \
    -g 60 -r 16 -movflags +faststart \
    {shlex.quote(Env.VIDEOS_DIR)}/video_temp.mp4
    """

    subprocess.run(cmd, shell=True, check=True)

    return os.path.join(Env.VIDEOS_DIR, "video_temp.mp4")


def upload_mp4() -> None:
    print("Compressing images to MP4 started at", Helper.current_time())
    mp4_file = compress_images_to_mp4()
    r = requests.post(
       f"{Env.SUPABASE_URL}/storage/v1/object/{Env.UPLOAD_BUCKET}/{Env.DEVICE_ID}/{Helper.current_time().strftime("%Y/%m/%d/%H")}/{Helper.current_time().strftime("%H%M%S")}.mp4",
       headers={**Env.HEADERS, "x-upsert": "true", "Content-Type": "video/mp4"},
       data=open(mp4_file, "rb").read(),
       timeout=60,
    )
    if not r.ok:
        try: body = r.text
        except Exception: body = ""
        print(json.dumps({"event": "mp4_upload_error", "ts": Helper.current_time(), "body": body}))
        return
    print(json.dumps({"event": "mp4_uploaded", "ts": Helper.current_time()}))
    r.raise_for_status()

    os.remove(mp4_file)
    for file in os.listdir(Env.COMPRESSED_DIR):
        os.remove(os.path.join(Env.COMPRESSED_DIR, file))

    print("MP4 uploaded and compressed images removed at", Helper.current_time())


if __name__ == "__main__":
    while True:
        upload_mp4()
        upload_metrics()
        time.sleep(900)  # 15 minutes = 900

