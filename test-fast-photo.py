import os
import time
import threading
import subprocess
from pathlib import Path
from flask import Flask, send_from_directory, render_template_string

# Settings
IMG_DIR = Path("./test_fast_photo_latest")
IMG_DIR.mkdir(exist_ok=True)
CAMERA_ID = "2"  # '0.5x' ultrawide is typically camera "2" on many Androids (might differ)
PHOTO_INTERVAL = 1.0  # seconds (how often to start a new capture)
IMG_NAME = "latest.jpg"

latest_capture = {
    "path": IMG_DIR / IMG_NAME,
    "timestamp": 0.0,
    "error": None
}
lock = threading.Lock()

def take_photo_forever():
    while True:
        dest_path = IMG_DIR / IMG_NAME
        tmp_path = IMG_DIR / (f"tmp_{int(time.time()*1000)}.jpg")
        started = time.time()
        try:
            # Start a new capture process every interval, don't wait for the last to finish
            def _capture():
                try:
                    subprocess.run([
                        "termux-camera-photo", "-c", CAMERA_ID, str(tmp_path)
                    ], timeout=10, check=True)
                    if tmp_path.exists() and tmp_path.stat().st_size > 0:
                        tmp_path.replace(dest_path)
                        with lock:
                            latest_capture["timestamp"] = time.time()
                            latest_capture["error"] = None
                    else:
                        with lock:
                            latest_capture["error"] = "No photo file produced"
                except Exception as e:
                    with lock:
                        latest_capture["error"] = str(e)
                finally:
                    if tmp_path.exists():
                        tmp_path.unlink(missing_ok=True)
            threading.Thread(target=_capture, daemon=True).start()
        except Exception as e:
            with lock:
                latest_capture["error"] = str(e)
        # Just sleep for the interval. Overlapping threads will be fine.
        time.sleep(PHOTO_INTERVAL)

# Web app to see live photo and time since last
app = Flask(__name__)

TEMPLATE = """
<!doctype html>
<title>Fast Photo Viewer</title>
<style>
body { font-family: sans-serif; background: #232323; color: #fafafa; text-align: center; }
.imgframe { margin: 20px auto; border: 2px solid #888; display: inline-block; background: #181818; }
#timestamp { font-size: 2em; margin-top: 10px; }
#error { color: #ff5555; font-size: 1.2em; }
</style>
<h1>Fast Photo</h1>
<div id="timestamp">--</div>
{% if error %}
<div id="error">{{ error }}</div>
{% endif %}
<div class="imgframe">
    <img id="liveimg" src="/photo?ts={{ ts }}" width="480" style="max-width: 95vw;">
</div>
<script>
function refreshImg() {
    var img = document.getElementById('liveimg');
    img.src = '/photo?ts=' + Date.now();
}
function refreshTime() {
    fetch('/meta').then(r=>r.json()).then(d=>{
        var secs = Math.round(d.since);
        document.getElementById('timestamp').textContent = "Last updated: " + secs + "s ago";
        if(d.error)
            document.getElementById('error').textContent = d.error;
        else
            document.getElementById('error').textContent = "";
    });
}
setInterval(refreshImg, 1500);
setInterval(refreshTime, 500);
window.onload = function(){ refreshImg(); refreshTime(); };
</script>
"""

@app.route("/")
def index():
    with lock:
        ts = latest_capture["timestamp"]
        error = latest_capture["error"]
    return render_template_string(TEMPLATE, ts=ts, error=error)

@app.route("/photo")
def photo():
    img = IMG_DIR / IMG_NAME
    if img.exists():
        return send_from_directory(IMG_DIR, IMG_NAME)
    else:
        from flask import abort
        return abort(404)

@app.route("/meta")
def photo_meta():
    with lock:
        ts = latest_capture["timestamp"]
        error = latest_capture["error"]
    since = time.time() - ts if ts else -1
    return {"timestamp": ts, "since": since, "error": error}

if __name__ == "__main__":
    # Start the photo thread
    threading.Thread(target=take_photo_forever, daemon=True).start()
    print(f"Serving live image at http://127.0.0.1:5100")
    app.run("0.0.0.0", 5100, debug=False)
