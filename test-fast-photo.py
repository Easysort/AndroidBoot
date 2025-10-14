import os
import time
import subprocess
from concurrent.futures import ThreadPoolExecutor

TMPDIR = os.path.expanduser("~/tmp")
os.makedirs(TMPDIR, exist_ok=True)

def take_photo(camera_id="0"):
    ts = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    out_path = os.path.join(TMPDIR, f"photo_{ts}.jpg")
    try:
        subprocess.run(
            ["termux-camera-photo", "-c", str(camera_id), out_path],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
        print(f"Photo taken: {out_path}")
    except Exception as e:
        print(f"Error taking photo at {ts}: {e}")

def main(interval=1, camera_id="0"):
    with ThreadPoolExecutor(max_workers=4) as executor:
        while True:
            executor.submit(take_photo, camera_id)
            time.sleep(interval)

if __name__ == "__main__":
    main()
