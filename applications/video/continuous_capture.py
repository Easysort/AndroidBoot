from applications.common import Env, Helper
import os, time, subprocess

procs: list[subprocess.Popen] = []
MAX_PROCS = Env.FPS * 4 # Takes about 4 seconds for a single capture

KEEPALIVE_PATH = os.path.join(Env.IMAGES_DIR, ".keepalive.jpg")
NIGHTTIME_INTERVAL = 5

while True:
    now = Helper.current_time()
    is_nighttime = now.hour < 6 or now.hour >= 22

    procs = [p for p in procs if p.poll() is None]

    if is_nighttime:
        if len(procs) < 1:
            procs.append(subprocess.Popen(
                ["termux-camera-photo", "-c", "0", KEEPALIVE_PATH],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True
            ))
            print(f"Keepalive capture at {now}")
        time.sleep(NIGHTTIME_INTERVAL)
    else:
        if len(procs) < MAX_PROCS:
            out_path = os.path.join(Env.IMAGES_DIR, f"photo_{now.strftime("%Y%m%dT%H%M%S%f")}.jpg")
            procs.append(subprocess.Popen(
                ["termux-camera-photo", "-c", "0", out_path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True
            ))
            print(f"Started capture at {now}")
        time.sleep(1 / Env.FPS)