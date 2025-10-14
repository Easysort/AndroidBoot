import os
import time
import threading
import subprocess
from pathlib import Path
from flask import Flask, send_from_directory, render_template_string, Response
from collections import deque

# Settings
IMG_DIR = Path("./test_fast_photo_latest")
IMG_DIR.mkdir(exist_ok=True)
CAMERA_ID = "2"  # '0.5x' ultrawide is typically camera "2" on many Androids (might differ)
PHOTO_INTERVAL = 1.0  # seconds (how often to start a new capture)
IMG_NAME = "latest.jpg"

latest_capture = {
    "path": IMG_DIR / IMG_NAME,
    "timestamp": 0.0,
    "error": None,
    "history": deque(maxlen=10)  # Keep last 10 timestamps
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
                        capture_time = time.time()
                        with lock:
                            latest_capture["timestamp"] = capture_time
                            latest_capture["error"] = None
                            latest_capture["history"].append(capture_time)
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
#error { color: #ff5555; font-size: 1.2em; margin: 10px; }
#history { margin: 20px auto; max-width: 600px; text-align: left; font-family: monospace; }
#history h3 { text-align: center; }
#history ul { list-style: none; padding: 0; }
#history li { padding: 5px; border-bottom: 1px solid #444; }
#debug { color: #888; font-size: 0.9em; margin: 10px; }
</style>
<h1>Fast Photo</h1>
<div id="timestamp">--</div>
<div id="error"></div>
<div id="debug"></div>
<div class="imgframe">
    <img id="liveimg" src="/photo?ts={{ ts }}" width="480" style="max-width: 95vw;" 
         onerror="this.style.display='none'; document.getElementById('error').textContent='Image failed to load'">
</div>
<div id="history">
    <h3>Last 10 Capture Times</h3>
    <ul id="historyList"></ul>
</div>
<script>
function refreshImg() {
    var img = document.getElementById('liveimg');
    img.style.display = 'inline';
    img.src = '/photo?ts=' + Date.now();
}
function refreshTime() {
    fetch('/meta').then(r=>r.json()).then(d=>{
        var micros = Math.round(d.since * 1000000);
        document.getElementById('timestamp').textContent = "Last updated: " + micros.toLocaleString() + " μs ago";
        
        if(d.error)
            document.getElementById('error').textContent = "Error: " + d.error;
        else
            document.getElementById('error').textContent = "";
        
        // Update history
        var historyList = document.getElementById('historyList');
        historyList.innerHTML = '';
        if(d.history && d.history.length > 0) {
            d.history.forEach((ts, idx) => {
                var li = document.createElement('li');
                var date = new Date(ts * 1000);
                var timeStr = date.toLocaleTimeString() + '.' + date.getMilliseconds();
                li.textContent = (d.history.length - idx) + '. ' + timeStr + ' (' + ts.toFixed(6) + 's)';
                historyList.appendChild(li);
            });
        } else {
            historyList.innerHTML = '<li>No captures yet...</li>';
        }
        
        // Debug info
        document.getElementById('debug').textContent = 'Image exists: ' + d.image_exists + ' | Last TS: ' + d.timestamp;
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
    if img.exists() and img.stat().st_size > 0:
        return send_from_directory(IMG_DIR, IMG_NAME)
    else:
        # Return a placeholder or 1x1 transparent pixel instead of 404
        return Response(
            b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x00\x00\x00\x21\xf9\x04\x01\x00\x00\x00\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x01\x44\x00\x3b',
            mimetype='image/gif'
        )

@app.route("/meta")
def photo_meta():
    with lock:
        ts = latest_capture["timestamp"]
        error = latest_capture["error"]
        history = list(latest_capture["history"])
    since = time.time() - ts if ts else -1
    img_exists = (IMG_DIR / IMG_NAME).exists()
    return {
        "timestamp": ts, 
        "since": since, 
        "error": error,
        "history": history,
        "image_exists": img_exists
    }

if __name__ == "__main__":
    # Start the photo thread
    threading.Thread(target=take_photo_forever, daemon=True).start()
    print(f"Serving live image at http://127.0.0.1:5100")
    app.run("0.0.0.0", 5100, debug=False)
