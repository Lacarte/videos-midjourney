from flask import Flask, request
from datetime import datetime
import sys
import os
import json
import requests
import time
import random
from utils import resource_path
import logging
import tempfile

"""
Key behavior change:
- We ONLY verify the file immediately after each download.
- We DO NOT scan/compare the JSON against files in "download-midjourney" anymore.
- When a download passes verification, we mark that single item as downloaded in videos.json.
- No global reconciliation/verification pass.
"""

# ---------------------------
# Logging
# ---------------------------

def setup_logging():
    logs_path = create_directory("logs")

    file_handler = logging.FileHandler(
        os.path.join(logs_path, f"log-{datetime.now().strftime('%Y-%m-%d')}.log"),
        mode="w",
        encoding="utf-8",
    )

    class SafeConsoleHandler(logging.StreamHandler):
        def emit(self, record):
            try:
                msg = self.format(record)
                safe_msg = msg.encode('ascii', 'replace').decode('ascii')
                stream = self.stream
                stream.write(safe_msg + self.terminator)
                self.flush()
            except Exception:
                self.handleError(record)

    console_handler = SafeConsoleHandler()

    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    logging.info("LOG: Logging system initialized successfully")


# ---------------------------
# Paths & Flask
# ---------------------------

def create_directory(dir_name):
    try:
        path = resource_path(dir_name)
        if not os.path.exists(path):
            os.makedirs(path)
            print(f"Created directory: {path}")
        return path
    except Exception as e:
        print(f"Error creating '{dir_name}' directory: {e}")
        sys.exit(1)


setup_logging()

app = Flask(__name__)
VIDEOS_FILE = "videos.json"
DOWNLOADS_DIR = "download-midjourney"


# ---------------------------
# HTTP headers/user-agents
# ---------------------------

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',
]


def get_download_headers():
    return {
        'User-Agent': random.choice(USER_AGENTS),
        'Accept': 'video/mp4,video/*,*/*',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate, br',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Cache-Control': 'max-age=0',
        'Referer': 'https://discord.com/',
        'Range': 'bytes=0-'
    }


# ---------------------------
# JSON DB helpers
# ---------------------------

def load_videos():
    if os.path.exists(VIDEOS_FILE):
        with open(VIDEOS_FILE, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
                return data.get("videos", [])
            except json.JSONDecodeError:
                return []
    return []


def save_videos(videos):
    with open(VIDEOS_FILE, "w", encoding="utf-8") as f:
        json.dump({"videos": videos}, f, indent=2, ensure_ascii=False)


def save_new_videos(new_videos):
    existing = load_videos()
    existing_names = {v.get("videoName") for v in existing if v.get("videoName")}

    unique_incoming = {}
    for v in new_videos:
        name = v.get("videoName")
        if not name:
            logging.info(f"WARNING: Skipping video with missing videoName: {v}")
            continue
        if name not in unique_incoming:
            v.setdefault("downloaded", False)
            unique_incoming[name] = v

    to_add = [v for name, v in unique_incoming.items() if name not in existing_names]
    if not to_add:
        logging.info("NO_NEW: No new videos to add")
        return 0

    save_videos(existing + to_add)
    logging.info(f"SAVED: Added {len(to_add)} new videos to database")
    return len(to_add)


# ---------------------------
# Download helpers (no global verification)
# ---------------------------

def verify_temp_file_is_ok(temp_path, min_size_bytes=8192):
    try:
        size = os.path.getsize(temp_path)
        if size <= min_size_bytes:
            logging.info(f"FILE_SMALL: temp file too small ({size} bytes)")
            return False
        return True
    except FileNotFoundError:
        return False


def atomic_move(src, dst):
    # Ensure target dir exists
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    # Atomic replace on same filesystem
    os.replace(src, dst)


def download_with_requests(url, final_path, max_retries=2):
    """Download to a temp file first, verify, then atomically move."""
    headers = get_download_headers()

    # temp file next to final path
    dir_ = os.path.dirname(final_path)
    base = os.path.basename(final_path)
    temp_path = os.path.join(dir_, f".{base}.part")

    for attempt in range(1, max_retries + 1):
        try:
            logging.info(f"REQUESTS: Attempt {attempt}/{max_retries} -> {url}")
            with requests.get(url, headers=headers, stream=True, timeout=90) as r:
                # If cdn sometimes wants /0.mp4 vs .mp4, try swap on first attempt failure only
                if r.status_code == 403 and attempt == 1 and url.endswith('/0.mp4'):
                    alt_url = url.replace('/0.mp4', '.mp4')
                    logging.info(f"REQUESTS: 403, trying alt url {alt_url}")
                    with requests.get(alt_url, headers=headers, stream=True, timeout=90) as r2:
                        r2.raise_for_status()
                        with open(temp_path, 'wb') as f:
                            for chunk in r2.iter_content(chunk_size=8192):
                                if chunk:
                                    f.write(chunk)
                else:
                    r.raise_for_status()
                    with open(temp_path, 'wb') as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)

            if verify_temp_file_is_ok(temp_path):
                atomic_move(temp_path, final_path)
                logging.info(f"SUCCESS: Downloaded with requests -> {os.path.basename(final_path)}")
                return True
            else:
                # cleanup and retry
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                time.sleep(random.uniform(3, 8))
        except Exception as e:
            logging.info(f"ERROR: requests attempt {attempt} failed: {e}")
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
            if attempt < max_retries:
                time.sleep(random.uniform(3, 8))

    return False


def download_with_curl(url, final_path):
    """Use curl to temp file then atomically move."""
    try:
        import subprocess
        dir_ = os.path.dirname(final_path)
        base = os.path.basename(final_path)
        temp_path = os.path.join(dir_, f".{base}.part")

        curl_cmd = [
            'curl', '-L',
            '--user-agent', random.choice(USER_AGENTS),
            '--referer', 'https://discord.com/',
            '--header', 'Accept: video/mp4,video/*,*/*',
            '--connect-timeout', '30',
            '--max-time', '300',
            '--retry', '2',
            '--retry-delay', '5',
            '-o', temp_path,
            url
        ]

        logging.info(f"CURL: {' '.join(curl_cmd[:-1])} <url>")
        result = subprocess.run(curl_cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logging.info(f"ERROR: curl failed rc={result.returncode}: {result.stderr}")
            if os.path.exists(temp_path):
                os.remove(temp_path)
            return False

        if verify_temp_file_is_ok(temp_path):
            atomic_move(temp_path, final_path)
            logging.info(f"SUCCESS: Downloaded with curl -> {os.path.basename(final_path)}")
            return True
        else:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            return False

    except Exception as e:
        logging.info(f"ERROR: curl exception: {e}")
        return False


def download_video_with_retry(url, final_path, prefer_curl=True):
    """Try curl first (optional), then requests. No global folder/JSON comparisons."""
    if prefer_curl:
        if download_with_curl(url, final_path):
            return True
        logging.info("FALLBACK: curl failed, trying requests...")
        return download_with_requests(url, final_path)
    else:
        if download_with_requests(url, final_path):
            return True
        logging.info("FALLBACK: requests failed, trying curl...")
        return download_with_curl(url, final_path)


# ---------------------------
# Core flow: per-item verify & mark
# ---------------------------

def mark_as_downloaded(videos, video_name):
    for v in videos:
        if v.get('videoName') == video_name:
            v['downloaded'] = True
            return True
    return False


def download_pending_videos():
    videos = load_videos()
    downloads_path = create_directory(DOWNLOADS_DIR)

    pending = [v for v in videos if not v.get('downloaded', False)]

    logging.info("\n" + "="*60)
    logging.info("SUMMARY: DOWNLOAD STATUS SUMMARY")
    logging.info("="*60)
    logging.info(f"TOTAL: {len(videos)} | COMPLETED: {len(videos) - len(pending)} | PENDING: {len(pending)}")
    logging.info("="*60)

    if not pending:
        logging.info("DONE: All videos already marked as downloaded. Nothing to do.")
        return

    for idx, video in enumerate(pending, start=1):
        url = video['videoUrl']
        name = video['videoName']
        filename = f"{name}.mp4"
        final_path = os.path.join(downloads_path, filename)

        logging.info(f"DOWNLOADING: [{idx}/{len(pending)}] {filename}")
        logging.info(f"URL: {url}")

        ok = download_video_with_retry(url, final_path, prefer_curl=True)
        if ok:
            # Immediate verification already performed in download helpers.
            # Mark only THIS item as downloaded and persist JSON immediately.
            if mark_as_downloaded(videos, name):
                save_videos(videos)
                logging.info(f"COMPLETED: Marked as downloaded in DB -> {name}")
            else:
                logging.info(f"WARNING: Could not find {name} in videos.json to mark as downloaded")
        else:
            logging.info(f"FAILED: Could not download {filename}")

        # pacing
        remaining = len(pending) - idx
        if remaining > 0:
            logging.info(f"WAITING: 15 seconds... ({remaining} remaining)")
            time.sleep(15)


# ---------------------------
# Flask endpoint
# ---------------------------

@app.route("/dailyvids", methods=["POST"])
def dailyvids():
    data = request.json or {}
    logging.info(f"REQUEST: Incoming request data: {data}")

    videos = data.get("videos", [])
    added_count = save_new_videos(videos)

    if added_count > 0:
        logging.info("STARTING: Downloading pending videos right away...")
        download_pending_videos()
    else:
        logging.info("NO_DOWNLOAD: No new videos to download. (No global verification performed.)")

    return {"message": f"Saved {added_count} new videos"}, 200


if __name__ == "__main__":
    logging.info("FLASK: Starting Flask application...")
    logging.info("SERVER: http://localhost:5000")
    app.run(debug=True, port=5000)
