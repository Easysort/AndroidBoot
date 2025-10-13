#!/usr/bin/env python3

"""
Batch classifier: moves images from ./queue to ./motion or ./non-motion.

Two modes:
1) If YOLO weights available (Ultralytics), run a tiny person detector.
2) Fallback: size-based heuristic (very fast, worse accuracy).

Env vars:
- BASE_DIR (default ".")
- QUEUE_DIR (default "queue")
- MOTION_DIR (default "motion")
- NON_MOTION_DIR (default "non-motion")
- BATCH_N (default "20")
- SLEEP_SEC (default "5")  # pause between batches
- YOLO_WEIGHTS (optional) e.g., "yolov8n.pt" or "yolov8n-cls.pt" (person class requires detect weights)
- SIZE_ABS_THRESHOLD (default "150000") used only in fallback mode
"""

import os
import time
import shutil
from pathlib import Path

BASE_DIR = Path(os.environ.get("BASE_DIR", ".")).resolve()
QUEUE_DIR = BASE_DIR / os.environ.get("QUEUE_DIR", "queue")
MOTION_DIR = BASE_DIR / os.environ.get("MOTION_DIR", "motion")
NON_MOTION_DIR = BASE_DIR / os.environ.get("NON_MOTION_DIR", "non-motion")
BATCH_N = int(os.environ.get("BATCH_N", "20"))
SLEEP_SEC = float(os.environ.get("SLEEP_SEC", "5"))
YOLO_WEIGHTS = os.environ.get("YOLO_WEIGHTS", "").strip()
SIZE_ABS_THRESHOLD = int(os.environ.get("SIZE_ABS_THRESHOLD", "150000"))


def ensure_dirs() -> None:
    for d in (QUEUE_DIR, MOTION_DIR, NON_MOTION_DIR):
        d.mkdir(parents=True, exist_ok=True)


def try_import_yolo():
    if not YOLO_WEIGHTS:
        return None
    try:
        from ultralytics import YOLO  # type: ignore
        model = YOLO(YOLO_WEIGHTS)
        return model
    except Exception as e:
        print(f"YOLO not available ({e}); using fallback heuristic")
        return None


def classify_person_yolo(model, paths: list[Path]) -> list[tuple[Path, bool]]:
    results = []
    try:
        preds = model(paths, imgsz=320, conf=0.3, verbose=False)
        for p, pr in zip(paths, preds):
            has_person = False
            try:
                for b in pr.boxes:  # type: ignore[attr-defined]
                    cls_id = int(b.cls.item())
                    # COCO person class is 0
                    if cls_id == 0:
                        has_person = True
                        break
            except Exception:
                pass
            results.append((p, has_person))
    except Exception as e:
        print(f"YOLO inference error: {e}")
        # Fallback to heuristic for all
        return [(p, heuristic_is_motion(p)) for p in paths]
    return results


def heuristic_is_motion(p: Path) -> bool:
    try:
        size = p.stat().st_size
    except Exception:
        size = 0
    return size >= SIZE_ABS_THRESHOLD


def move_path(src: Path, dst_dir: Path) -> None:
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / src.name
    try:
        src.replace(dst)
    except Exception:
        try:
            shutil.move(str(src), str(dst))
        except Exception as e:
            print(f"move failed {src} -> {dst}: {e}")


def main() -> None:
    ensure_dirs()
    yolo = try_import_yolo()
    print("Batch classifier ready:")
    if yolo:
        print(f"- YOLO weights: {YOLO_WEIGHTS}")
    else:
        print(f"- Fallback size threshold: {SIZE_ABS_THRESHOLD} bytes")

    while True:
        try:
            items = [p for p in sorted(QUEUE_DIR.iterdir()) if p.suffix.lower() in (".jpg", ".jpeg", ".png")]
        except FileNotFoundError:
            items = []

        if not items:
            time.sleep(SLEEP_SEC)
            continue

        batch = items[:BATCH_N]
        if yolo:
            decisions = classify_person_yolo(yolo, batch)
        else:
            decisions = [(p, heuristic_is_motion(p)) for p in batch]

        for p, is_motion in decisions:
            move_path(p, MOTION_DIR if is_motion else NON_MOTION_DIR)
            print(("motion" if is_motion else "non" ) + f": {p.name}")

        # small pause to yield
        time.sleep(0.1)


if __name__ == "__main__":
    main()


