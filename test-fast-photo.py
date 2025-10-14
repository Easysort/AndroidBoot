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
    "history": deque(maxlen=10),
    "last_stderr": None,
    "last_returncode": None
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
                    print(f"[{time.strftime('%H:%M:%S')}] Attempting capture to {tmp_path}")
                    
                    # Run the camera command and capture output
                    result = subprocess.run([
                        "termux-camera-photo", "-c", CAMERA_ID, str(tmp_path)
                    ], timeout=10, capture_output=True, text=True)
                    
                    print(f"[{time.strftime('%H:%M:%S')}] Return code: {result.returncode}")
                    if result.stdout:
                        print(f"[{time.strftime('%H:%M:%S')}] stdout: {result.stdout}")
                    if result.stderr:
                        print(f"[{time.strftime('%H:%M:%S')}] stderr: {result.stderr}")
                    
                    # Wait for the file to be written (termux-camera-photo returns before file is complete)
                    max_wait = 5  # seconds
                    wait_start = time.time()
                    last_size = 0
                    stable_count = 0
                    
                    while (time.time() - wait_start) < max_wait:
                        if tmp_path.exists():
                            current_size = tmp_path.stat().st_size
                            print(f"[{time.strftime('%H:%M:%S')}] File size: {current_size} bytes")
                            
                            if current_size > 0:
                                # Check if size is stable (hasn't changed in 2 checks)
                                if current_size == last_size:
                                    stable_count += 1
                                    if stable_count >= 2:  # Size stable for 2 checks
                                        print(f"[{time.strftime('%H:%M:%S')}] File size stable at {current_size} bytes")
                                        break
                                else:
                                    stable_count = 0
                                last_size = current_size
                            
                            time.sleep(0.1)  # Check every 100ms
                        else:
                            time.sleep(0.05)  # Wait for file to be created
                    
                    # Check final result
                    if tmp_path.exists():
                        file_size = tmp_path.stat().st_size
                        print(f"[{time.strftime('%H:%M:%S')}] Final file size: {file_size} bytes")
                        
                        if file_size > 0:
                            tmp_path.replace(dest_path)
                            capture_time = time.time()
                            with lock:
                                latest_capture["timestamp"] = capture_time
                                latest_capture["error"] = None
                                latest_capture["history"].append(capture_time)
                                latest_capture["last_returncode"] = result.returncode
                                latest_capture["last_stderr"] = result.stderr if result.stderr else None
                            print(f"[{time.strftime('%H:%M:%S')}] ✓ Capture successful! ({file_size} bytes)")
                        else:
                            error_msg = f"Photo file is empty (0 bytes) after {max_wait}s wait"
                            with lock:
                                latest_capture["error"] = error_msg
                                latest_capture["last_returncode"] = result.returncode
                                latest_capture["last_stderr"] = result.stderr if result.stderr else None
                            print(f"[{time.strftime('%H:%M:%S')}] ✗ {error_msg}")
                    else:
                        error_msg = f"No file created. Return code: {result.returncode}"
                        if result.stderr:
                            error_msg += f". Error: {result.stderr}"
                        with lock:
                            latest_capture["error"] = error_msg
                            latest_capture["last_returncode"] = result.returncode
                            latest_capture["last_stderr"] = result.stderr if result.stderr else None
                        print(f"[{time.strftime('%H:%M:%S')}] ✗ {error_msg}")
                        
                except subprocess.TimeoutExpired:
                    error_msg = "Camera capture timed out (>10s)"
                    with lock:
                        latest_capture["error"] = error_msg
                    print(f"[{time.strftime('%H:%M:%S')}] ✗ {error_msg}")
                except FileNotFoundError:
                    error_msg = "termux-camera-photo command not found"
                    with lock:
                        latest_capture["error"] = error_msg
                    print(f"[{time.strftime('%H:%M:%S')}] ✗ {error_msg}")
                except Exception as e:
                    error_msg = f"{type(e).__name__}: {str(e)}"
                    with lock:
                        latest_capture["error"] = error_msg
                    print(f"[{time.strftime('%H:%M:%S')}] ✗ Exception: {error_msg}")
                finally:
                    if tmp_path.exists():
                        try:
                            tmp_path.unlink()
                        except:
                            pass
            threading.Thread(target=_capture, daemon=True).start()
        except Exception as e:
            with lock:
                latest_capture["error"] = str(e)
            print(f"[{time.strftime('%H:%M:%S')}] ✗ Outer exception: {e}")
        # Just sleep for the interval. Overlapping threads will be fine.
        time.sleep(PHOTO_INTERVAL)

# Web app to see live photo and time since last
app = Flask(__name__)

TEMPLATE = """
<!doctype html>
<title>Fast Photo Viewer</title>
<style>
body { font-family: sans-serif; background: #232323; color: #fafafa; text-align: center; padding: 20px; }
.imgframe { margin: 20px auto; border: 2px solid #888; display: inline-block; background: #181818; min-height: 300px; }
#timestamp { font-size: 2em; margin-top: 10px; }
#error { color: #ff5555; font-size: 1.2em; margin: 10px; white-space: pre-wrap; }
#history { margin: 20px auto; max-width: 600px; text-align: left; font-family: monospace; font-size: 0.9em; }
#history h3 { text-align: center; }
#history ul { list-style: none; padding: 0; }
#history li { padding: 5px; border-bottom: 1px solid #444; }
#debug { color: #888; font-size: 0.9em; margin: 10px; white-space: pre-wrap; }
</style>
<h1>Fast Photo</h1>
<div id="timestamp">--</div>
<div id="error"></div>
<div id="debug"></div>
<div class="imgframe">
    <img id="liveimg" src="/photo?ts={{ ts }}" width="480" style="max-width: 95vw; display: none;" 
         onload="this.style.display='inline';"
         onerror="console.error('Image load error');">
</div>
<div id="history">
    <h3>Last 10 Capture Times</h3>
    <ul id="historyList"></ul>
</div>
<script>
function refreshImg() {
    var img = document.getElementById('liveimg');
    img.src = '/photo?ts=' + Date.now();
}
function refreshTime() {
    fetch('/meta').then(r=>r.json()).then(d=>{
        var micros = Math.round(d.since * 1000000);
        document.getElementById('timestamp').textContent = "Last updated: " + micros.toLocaleString() + " μs ago";
        
        var debugInfo = 'Image exists: ' + d.image_exists + 
                       ' | Last TS: ' + d.timestamp +
                       ' | Return code: ' + d.last_returncode;
        if(d.last_stderr) {
            debugInfo += '\\nStderr: ' + d.last_stderr;
        }
        document.getElementById('debug').textContent = debugInfo;
        
        if(d.error) {
            document.getElementById('error').textContent = "Error: " + d.error;
        } else {
            document.getElementById('error').textContent = "";
        }
        
        // Update history
        var historyList = document.getElementById('historyList');
        historyList.innerHTML = '';
        if(d.history && d.history.length > 0) {
            d.history.forEach((ts, idx) => {
                var li = document.createElement('li');
                var date = new Date(ts * 1000);
                var timeStr = date.toLocaleTimeString() + '.' + String(date.getMilliseconds()).padStart(3, '0');
                li.textContent = (d.history.length - idx) + '. ' + timeStr + ' (' + ts.toFixed(6) + 's)';
                historyList.appendChild(li);
            });
        } else {
            historyList.innerHTML = '<li>No captures yet...</li>';
        }
    }).catch(e => {
        console.error('Fetch error:', e);
        document.getElementById('error').textContent = 'Failed to fetch metadata: ' + e;
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
    print(f"[{time.strftime('%H:%M:%S')}] Photo request - exists: {img.exists()}")
    if img.exists() and img.stat().st_size > 0:
        return send_from_directory(IMG_DIR, IMG_NAME, mimetype='image/jpeg')
    else:
        # Return a 1x1 transparent pixel instead of 404
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
        last_stderr = latest_capture["last_stderr"]
        last_returncode = latest_capture["last_returncode"]
    since = time.time() - ts if ts else -1
    img_exists = (IMG_DIR / IMG_NAME).exists()
    return {
        "timestamp": ts, 
        "since": since, 
        "error": error,
        "history": history,
        "image_exists": img_exists,
        "last_stderr": last_stderr,
        "last_returncode": last_returncode
    }

if __name__ == "__main__":
    # Start the photo thread
    print(f"[{time.strftime('%H:%M:%S')}] Starting camera capture thread")
    print(f"[{time.strftime('%H:%M:%S')}] Camera ID: {CAMERA_ID}")
    print(f"[{time.strftime('%H:%M:%S')}] Image directory: {IMG_DIR.absolute()}")
    threading.Thread(target=take_photo_forever, daemon=True).start()
    print(f"[{time.strftime('%H:%M:%S')}] Serving live image at http://0.0.0.0:5100")
    app.run("0.0.0.0", 5100, debug=False)
