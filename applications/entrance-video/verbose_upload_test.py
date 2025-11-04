import os
import sys
import json
import time
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv


def sanitize_key_component(s: str) -> str:
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
    return "".join(ch if ch in allowed else "_" for ch in s)


def main():
    load_dotenv()

    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    supabase_key = os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_KEY")
    bucket = os.environ.get("SUPABASE_BUCKET", "argo")

    if not supabase_url or not supabase_key:
        print(json.dumps({"event": "config_error", "reason": "Missing SUPABASE_URL or SUPABASE_ANON_KEY"}))
        sys.exit(2)

    id_file = os.path.join(os.path.dirname(__file__), "../../device_id.txt")
    if os.path.exists(id_file):
        device_id = open(id_file).read().strip()
    else:
        device_id = "unknown-device"
    safe_device = sanitize_key_component(device_id)

    now = datetime.now(timezone.utc)
    test_bytes = ("Upload test at " + now.isoformat() + " from device " + device_id + "\n").encode("utf-8")
    key = f"{safe_device}/{now:%Y/%m/%d/%H}/test_upload_{now:%Y%m%dT%H%M%SZ}.txt"
    url = f"{supabase_url}/storage/v1/object/{bucket}/{key}"

    headers = {
        "Authorization": f"Bearer {supabase_key}",
        "apikey": supabase_key,
        "x-upsert": "true",
        "Content-Type": "text/plain; charset=utf-8",
    }

    print(json.dumps({
        "event": "upload_attempt",
        "url": url,
        "bucket": bucket,
        "key": key,
        "headers_preview": {k: ("***" if k.lower().endswith("key") else v) for k, v in headers.items()},
        "bytes": len(test_bytes),
    }))

    try:
        resp = requests.post(url, headers=headers, data=test_bytes, timeout=60)
        print(json.dumps({
            "event": "upload_response",
            "status": resp.status_code,
            "ok": resp.ok,
            "text": resp.text,
        }))
        resp.raise_for_status()
        public_url = f"{supabase_url}/storage/v1/object/public/{bucket}/{key}"
        print(json.dumps({
            "event": "upload_success",
            "public_url": public_url,
        }))
        sys.exit(0)
    except Exception as e:
        print(json.dumps({
            "event": "upload_exception",
            "error": str(e),
            "hint": "Check bucket name, service policy, and that key components are ASCII-safe.",
        }))
        sys.exit(1)


if __name__ == "__main__":
    main()


