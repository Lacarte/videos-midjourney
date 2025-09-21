from flask import Flask, request
import os
import json

app = Flask(__name__)
VIDEOS_FILE = "videos.json"

def save_videos(new_videos):
    # Step 1: Load existing videos if file exists
    if os.path.exists(VIDEOS_FILE):
        with open(VIDEOS_FILE, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
                existing_videos = data.get("videos", [])
            except json.JSONDecodeError:
                existing_videos = []
    else:
        existing_videos = []

    # Step 2: Create a set of existing videoNames for duplicate check
    existing_names = {v["videoName"] for v in existing_videos}

    # Step 3: Filter out new videos that are already in the file
    filtered_new_videos = [
        v for v in new_videos if v["videoName"] not in existing_names
    ]

    # Step 4: Append new videos
    updated_videos = existing_videos + filtered_new_videos

    # Step 5: Save back to file
    with open(VIDEOS_FILE, "w", encoding="utf-8") as f:
        json.dump({"videos": updated_videos}, f, indent=2, ensure_ascii=False)

    return len(filtered_new_videos)


@app.route("/dailyvids", methods=["POST"])
def hello():
    data = request.json  # Expecting JSON body
    print("📩 Incoming request data:", data)

    videos = data.get("videos", [])
    added_count = save_videos(videos)

    return {"message": f"Saved {added_count} new videos"}, 200


if __name__ == "__main__":
    # Runs on http://localhost:5000
    app.run(debug=True, port=5000)
