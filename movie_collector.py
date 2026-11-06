import json
import os
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

CHANNEL_URL = (
    "https://www.youtube.com/channel/"
    "UCX1nchEcBshItKBeJvH-YMw/videos"
)

CATALOG_FILE = BASE_DIR / "catalog.json"
URLS_FILE = BASE_DIR / "urls.txt"


def load_catalog():
    if not CATALOG_FILE.exists():
        return []

    try:
        with open(CATALOG_FILE, encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, list):
            raise ValueError("Catalogue must be a JSON list")

        return data

    except Exception as error:
        print("Cannot read catalogue:", error)
        sys.exit(1)


def save_catalog(catalog):
    temporary = CATALOG_FILE.with_suffix(".tmp")

    with open(temporary, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2, ensure_ascii=False)

    os.replace(temporary, CATALOG_FILE)


def save_urls(catalog):
    temporary = URLS_FILE.with_suffix(".tmp")

    with open(temporary, "w", encoding="utf-8") as f:
        for video in catalog:
            if video.get("url"):
                f.write(video["url"] + "\n")

    os.replace(temporary, URLS_FILE)


def collect_channel(catalog):
    existing_ids = {
        video.get("id")
        for video in catalog
        if video.get("id")
    }

    command = [
        sys.executable,
        "-m", "yt_dlp",
        "--flat-playlist",
        "--dump-json",
        "--ignore-errors",
        "--no-warnings",
        "--skip-download",
        "--ignore-config",
        "--no-abort-on-error",
        CHANNEL_URL
    ]

    print("\nConnecting to FREE MOVIES...")
    print("Channel ID: UCX1nchEcBshItKBeJvH-YMw")
    print("Fetching channel uploads. Please wait...\n")

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=900
        )

    except subprocess.TimeoutExpired:
        print("Channel extraction timed out.")
        return 0, 0

    if not result.stdout.strip():
        print("\nYouTube returned no video entries.")
        print("\nActual yt-dlp error/output:")
        print(result.stderr[-5000:] or "No error message returned.")
        print("\nCheck/update yt-dlp and try again.")
        return 0, 0

    added = 0
    skipped = 0

    for line in result.stdout.splitlines():
        try:
            item = json.loads(line)

            video_id = item.get("id")
            title = item.get("title")

            if not video_id or not title:
                continue

            if video_id in existing_ids:
                skipped += 1
                continue

            video = {
                "id": video_id,
                "title": title,
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "thumbnail": (
                    item.get("thumbnail")
                    or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
                ),
                "source_channel": "@FREEMOVIESYT"
            }

            if item.get("duration") is not None:
                video["duration"] = item["duration"]

            if item.get("upload_date"):
                video["upload_date"] = item["upload_date"]

            catalog.append(video)
            existing_ids.add(video_id)
            added += 1

            # Save after every new video.
            save_catalog(catalog)

            print(f"[{added}] ADDED: {title}")

        except json.JSONDecodeError:
            continue

    if result.returncode != 0:
        print("\nYouTube returned a warning/error:")
        print(result.stderr[-3000:])

    return added, skipped


def main():
    catalog = load_catalog()

    print("=" * 55)
    print("MOVIE ZONE - FREE MOVIES CHANNEL COLLECTOR")
    print("=" * 55)
    print("Existing catalogue:", len(catalog))

    added, skipped = collect_channel(catalog)

    save_catalog(catalog)
    save_urls(catalog)

    print("\n" + "=" * 55)
    print("COLLECTION FINISHED")
    print("=" * 55)
    print("New videos:", added)
    print("Duplicates skipped:", skipped)
    print("Total catalogue:", len(catalog))
    print("Saved:", CATALOG_FILE)
    print("Saved:", URLS_FILE)


if __name__ == "__main__":
    main()
