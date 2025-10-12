

import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
import requests

API_BASE = "http://127.0.0.1:8787"  # change to your phone's IP if remote
POLL_INTERVAL_SEC = 5

latest_jpeg = bytearray()
lock = threading.Lock()

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"""<!doctype html><html><head><meta charset='utf-8'>
<title>Latest Photo</title>
<style>body{margin:0;background:#111;color:#eee;font:16px system-ui;text-align:center}img{max-width:100vw;max-height:100vh}</style>
<script>
setInterval(()=>{const i=document.getElementById('img');i.src='/latest.jpg?t='+Date.now()}, 5000);
</script></head><body>
<h3>Latest capture (5s)</h3>
<img id='img' src='/latest.jpg' />
</body></html>""")
            return
        if self.path.startswith("/latest.jpg"):
            with lock:
                data = bytes(latest_jpeg)
            if not data:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_response(404)
        self.end_headers()

def poller():
    global latest_jpeg
    while True:
        try:
            r = requests.get(f"{API_BASE}/photo", timeout=20)
            if r.status_code == 200 and r.headers.get("content-type","").startswith("image/"):
                with lock:
                    latest_jpeg[:] = r.content
        except Exception:
            pass
        time.sleep(POLL_INTERVAL_SEC)

if __name__ == "__main__":
    t = threading.Thread(target=poller, daemon=True)
    t.start()
    server = HTTPServer(("0.0.0.0", 8080), Handler)
    print("Serving on http://0.0.0.0:8080")
    server.serve_forever()