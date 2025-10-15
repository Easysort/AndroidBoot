
from json import load
import os
from dotenv import load_dotenv
from dataclasses import dataclass
from datetime import timezone, timedelta

load_dotenv()

REPO_DIR = os.environ["REPO_DIR"] or ""
DEVICE_ID_PATH = os.path.join(REPO_DIR, "device_id.txt")

check_path = lambda path: os.path.exists(path) or (_ for _ in ()).throw(FileNotFoundError(f"Path does not exist: {path}"))
[check_path(p) for p in [DEVICE_ID_PATH]]

@dataclass
class Env: 
    DEVICE_ID: str = open(DEVICE_ID_PATH).read().strip() or ""
    TIMEZONE: timezone = timezone(timedelta(hours=2)) # Default to CET