import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
URLS_FILE = BASE / "urls.txt"
CATALOG_FILE = BASE / "catalog.json"
ATTEMPTED_FILE = BASE / "attempted_ids.txt"

BATCH_SIZE = 20

def get_video_id(url):
    patterns = [
        r"[?&]v=([a-zA-Z0-9_-]{11})",
        r"youtu\.be/([a-zA-Z0-9_-]{11})",
        r"/shorts/([a-zA-Z0-9_-]{11})",
        r"/embed/([a-zA-Z0-9_-]{11})",
    ]

    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)

    return None


def save_json(data):
    fd, temp_path = tempfile.mkstemp(dir=BASE, suffix=".json")

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)

        os.replace(temp_path, CATALOG_FILE)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def main():
    if not URLS_FILE.exists():
        print("urls.txt not found.")
        sys.exit(1)

    if CATALOG_FILE.exists():
        with open(CATALOG_FILE, encoding="utf-8") as file:
            catalog = json.load(file)
    else:
        catalog = []

    if not isinstance(catalog, list):
        print("catalog.json must contain a JSON list.")
        sys.exit(1)

    existing_ids = {
        item["id"] for item in catalog if isinstance(item, dict) and "id" in item
    }

    attempted = set()

    if ATTEMPTED_FILE.exists():
        attempted = set(ATTEMPTED_FILE.read_text().splitlines())

    attempted.update(existing_ids)

    urls = []
    seen = set()

    with open(URLS_FILE, encoding="utf-8") as file:
        for line in file:
            url = line.strip()
            video_id = get_video_id(url)

            if video_id and video_id not in seen:
                urls.append((video_id, url))
                seen.add(video_id)

    remaining = [
        (video_id, url)
        for video_id, url in urls
        if video_id not in attempted
    ]

    batch = remaining[:BATCH_SIZE]

    print(f"Existing catalogue: {len(catalog)} videos")
    print(f"Remaining URLs: {len(remaining)}")
    print(f"Processing this batch: {len(batch)} videos")

    if not batch:
        print("All URLs have been attempted.")
        return

    added = 0

    for number, (video_id, url) in enumerate(batch, 1):
        print(f"\n[{number}/{len(batch)}] Processing {video_id}")

        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m", "yt_dlp",
                    "--ignore-config",
                    "--ignore-no-formats-error",
                    "--dump-single-json",
                    "--no-warnings",
                    "--no-playlist",
                    "--skip-download",
                    url,
                ],
                capture_output=True,
                text=True,
                timeout=90
            )

            if result.returncode != 0 or not result.stdout.strip():
                print("ERROR:", result.stderr[-1500:])
            else:
                info = json.loads(result.stdout)
                title = info.get("title")

                if title:
                    catalog.append({
                        "id": video_id,
                        "title": title,
                        "url": f"https://www.youtube.com/watch?v={video_id}",
                        "thumbnail": (
                            info.get("thumbnail")
                            or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
                        )
                    })

                    added += 1
                    print(f"Added: {title}")
                else:
                    print("Skipped: no title returned.")

        except subprocess.TimeoutExpired:
            print("Skipped: extraction timed out.")
        except Exception as error:
            print(f"Skipped: {error}")

        attempted.add(video_id)

        save_json(catalog)
        ATTEMPTED_FILE.write_text(
            "\n".join(sorted(attempted)) + "\n",
            encoding="utf-8"
        )

    print("\nBatch completed.")
    print(f"Successfully added: {added}")
    print(f"Total catalogue: {len(catalog)}")
    print("Files updated: catalog.json and attempted_ids.txt")


if __name__ == "__main__":
    main()
