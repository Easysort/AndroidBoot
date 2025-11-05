from applications.common import Env, Helper
import os, time, subprocess

procs: list[subprocess.Popen] = []
MAX_PROCS = Env.FPS * 4 # Takes about 4 seconds for a single capture

while True:
    procs = [p for p in procs if p.poll() is None]

    if len(procs) < MAX_PROCS:
        out_path = os.path.join(Env.IMAGES_DIR, f"photo_{Helper.current_time().strftime("%Y%m%dT%H%M%S%f")}.jpg")
        procs.append(subprocess.Popen(
            ["termux-camera-photo", "-c", "0", out_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True
        ))
    time.sleep(1 / Env.FPS)  # 0.25s at FPS=4