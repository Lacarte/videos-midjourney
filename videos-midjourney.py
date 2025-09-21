from flask import Flask, request
import os
import json
import requests
import time
import random

app = Flask(__name__)
VIDEOS_FILE = "videos.json"
DOWNLOADS_DIR = "download-midjourney"

# User agents to rotate through
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15'
]

def get_download_headers():
    """Get headers that mimic a browser request."""
    return {
        'User-Agent': random.choice(USER_AGENTS),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate, br',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Cache-Control': 'max-age=0',
        # Add referer to make it look like coming from Discord/Midjourney
        'Referer': 'https://discord.com/',
    }

def load_videos():
    """Load existing videos.json into a list of dicts."""
    if os.path.exists(VIDEOS_FILE):
        with open(VIDEOS_FILE, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
                return data.get("videos", [])
            except json.JSONDecodeError:
                return []
    return []

def save_videos(videos):
    """Save video list to videos.json."""
    with open(VIDEOS_FILE, "w", encoding="utf-8") as f:
        json.dump({"videos": videos}, f, indent=2, ensure_ascii=False)

def save_new_videos(new_videos):
    """Append new unique videos (by videoName) to videos.json."""
    existing_videos = load_videos()
    existing_names = {v["videoName"] for v in existing_videos}

    # Deduplicate within new payload
    unique_new = {}
    for v in new_videos:
        if v["videoName"] not in unique_new:
            unique_new[v["videoName"]] = v

    # Filter out ones that already exist
    filtered_new = [
        v for name, v in unique_new.items() if name not in existing_names
    ]

    # Append and save
    updated_videos = existing_videos + filtered_new
    save_videos(updated_videos)

    return len(filtered_new)

def download_video_with_retry(url, filepath, max_retries=2):
    """Download a video with curl (primary) and requests fallback."""
    
    # Try curl first since it's working consistently
    print(f"🔧 Downloading {os.path.basename(filepath)} with curl")
    if download_with_curl(url, filepath):
        return True
    
    print(f"⚠️  Curl failed, trying requests as fallback...")
    
    # Fallback to requests if curl fails
    for attempt in range(max_retries):
        try:
            print(f"🔄 Requests attempt {attempt + 1}/{max_retries}")
            
            session = requests.Session()
            headers = get_download_headers()
            
            if attempt == 0:
                # Try alternative URL format
                alt_url = url.replace('/0.mp4', '.mp4')
                response = session.get(alt_url, headers=headers, stream=True, timeout=60)
                if response.status_code == 403:
                    response = session.get(url, headers=headers, stream=True, timeout=60)
            else:
                # Try with video-specific headers
                headers['Accept'] = 'video/mp4,video/*,*/*'
                headers['Range'] = 'bytes=0-'
                response = session.get(url, headers=headers, stream=True, timeout=60)
            
            response.raise_for_status()
            
            with open(filepath, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            
            print(f"✅ Downloaded {os.path.basename(filepath)} with requests")
            return True
            
        except Exception as e:
            print(f"❌ Requests attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(random.uniform(3, 8))
    
    return False

def download_with_curl(url, filepath):
    """Fallback method using curl subprocess."""
    try:
        import subprocess
        
        print(f"🔧 Trying curl fallback for {os.path.basename(filepath)}")
        
        # Construct curl command
        curl_cmd = [
            'curl',
            '-L',  # Follow redirects
            '-o', filepath,
            '--user-agent', random.choice(USER_AGENTS),
            '--referer', 'https://discord.com/',
            '--header', 'Accept: video/mp4,video/*,*/*',
            '--connect-timeout', '30',
            '--max-time', '300',
            '--retry', '2',
            '--retry-delay', '5',
            url
        ]
        
        # Run curl
        result = subprocess.run(curl_cmd, capture_output=True, text=True)
        
        if result.returncode == 0 and os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            print(f"✅ Downloaded {os.path.basename(filepath)} with curl")
            return True
        else:
            print(f"❌ Curl failed: {result.stderr}")
            if os.path.exists(filepath):
                os.remove(filepath)  # Remove empty file
            return False
            
    except Exception as e:
        print(f"❌ Curl fallback failed: {e}")
        return False

def download_pending_videos():
    """Download all videos where downloaded == False."""
    videos = load_videos()
    changed = False

    if not os.path.exists(DOWNLOADS_DIR):
        os.makedirs(DOWNLOADS_DIR)

    for i, video in enumerate(videos):
        if not video.get("downloaded", False):
            url = video["videoUrl"]
            filename = f"{video['videoName']}.mp4"
            filepath = os.path.join(DOWNLOADS_DIR, filename)

            print(f"⬇️  Downloading {filename} from {url}")
            
            if download_video_with_retry(url, filepath):
                video["downloaded"] = True
                changed = True
            else:
                print(f"💀 Failed to download {filename} after all retries")
            
            # Wait 15 seconds between downloads
            if i < len(videos) - 1:  # Don't wait after the last video
                print(f"⏳ Waiting 25 seconds before next download...")
                time.sleep(25)

    if changed:
        save_videos(videos)

@app.route("/dailyvids", methods=["POST"])
def dailyvids():
    data = request.json
    print("📩 Incoming request data:", data)

    videos = data.get("videos", [])
    added_count = save_new_videos(videos)

    print("⚡ Download videos will start right away....")
    download_pending_videos()

    return {"message": f"Saved {added_count} new videos"}, 200

if __name__ == "__main__":
    app.run(debug=True, port=5000)