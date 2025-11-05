
import os
from dotenv import load_dotenv
from dataclasses import dataclass, field
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
    SUPABASE_URL: str = os.environ["SUPABASE_URL"].rstrip("/")
    SUPABASE_KEY: str = os.environ["SUPABASE_ANON_KEY"]
    TABLE: str = os.environ.get("SUPABASE_TABLE", "phone_metrics")

    @staticmethod
    def _default_headers() -> dict:
        return {"Authorization": f"Bearer {Env.SUPABASE_KEY}", "apikey": Env.SUPABASE_KEY}

    HEADERS: dict = field(default_factory=_default_headers)

    UPLOAD_BUCKET: str = os.environ["UPLOAD_BUCKET"]

    BASE_DIR = os.environ["REPO_DIR"]
    RUN_DIR = os.path.join(BASE_DIR, "run")
    IMAGES_DIR = os.path.join(RUN_DIR, "images")
    VIDEOS_DIR = os.path.join(RUN_DIR, "videos")
    COMPRESSED_DIR = os.path.join(RUN_DIR, "compressed")

    FPS = 4 # To change FPS change here and the delay in continuous_capture.sh to 1/FPS

    def __post_init__(self):
        os.makedirs(self.RUN_DIR, exist_ok=True)
        os.makedirs(self.IMAGES_DIR, exist_ok=True)
        os.makedirs(self.VIDEOS_DIR, exist_ok=True)
        os.makedirs(self.COMPRESSED_DIR, exist_ok=True)
