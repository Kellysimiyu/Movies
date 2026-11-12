#!/usr/bin/env python3

import subprocess
import json
import sys
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent
OUTPUT_FILE = BASE / "urls.txt"
CATALOG_FILE = BASE / "catalog.json"


def valid_title(title):
    return isinstance(title, str) and len(title.strip()) >= 3


def load_catalog():
    if not CATALOG_FILE.exists():
        return []

    with open(CATALOG_FILE, encoding="utf-8") as f:
        data = json.load(f)

    return data if isinstance(data, list) else []


def save_catalog(data):
    temp = CATALOG_FILE.with_suffix(".tmp")

    with open(temp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    temp.replace(CATALOG_FILE)


def get_existing_ids():
    ids = set()

    catalog = load_catalog()
    for item in catalog:
        if isinstance(item, dict) and item.get("id"):
            ids.add(item["id"])

    if OUTPUT_FILE.exists():
        with open(OUTPUT_FILE, encoding="utf-8") as f:
            for line in f:
                match = re.search(r"(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})", line)
                if match:
                    ids.add(match.group(1))

    return ids


def run_ytdlp(url):
    command = [
        sys.executable, "-m", "yt_dlp",
        "--ignore-config",
        "--flat-playlist",
        "--dump-json",
        "--no-warnings",
        "--ignore-errors",
        "--skip-download",
        url
    ]

    print("\nCollecting channel videos. Please wait...\n")

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1
    )

    videos = []
    seen = set()

    for line in process.stdout:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue

        video_id = item.get("id")
        title = item.get("title")

        if not video_id or video_id in seen:
            continue

        if not valid_title(title):
            continue

        seen.add(video_id)

        videos.append({
            "id": video_id,
            "title": title.strip(),
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "thumbnail": (
                item.get("thumbnail")
                or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
            )
        })

        if len(videos) % 25 == 0:
            print(f"Found {len(videos)} videos...", end="\r")

    stderr = process.stderr.read()
    return_code = process.wait()

    if return_code != 0 and not videos:
        print("\nyt-dlp error:")
        print(stderr[-1500:])

    return videos


def resolve_channel(name):
    if name.startswith(("http://", "https://")):
        return name.rstrip("/")

    if name.startswith("@"):
        return "https://www.youtube.com/" + name

    print(f"\nSearching for channel: {name}")

    command = [
        sys.executable, "-m", "yt_dlp",
        "--ignore-config",
        f"ytsearch30:{name}",
        "--flat-playlist",
        "--dump-json",
        "--no-warnings"
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=300
    )

    channels = {}

    for line in result.stdout.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue

        channel = item.get("channel") or item.get("uploader")
        url = item.get("channel_url") or item.get("uploader_url")

        if channel and url:
            channels[(channel, url)] = channels.get((channel, url), 0) + 1

    if not channels:
        print("Channel not found. Try its @handle or URL.")
        return None

    matches = sorted(
        channels.items(),
        key=lambda x: x[1],
        reverse=True
    )

    print("\nMatching channels:")

    for i, ((channel, url), count) in enumerate(matches[:10], 1):
        print(f"{i}. {channel} — {url}")

    choice = input("\nSelect channel number: ").strip()

    try:
        return matches[int(choice) - 1][0][1]
    except (ValueError, IndexError):
        print("Invalid selection.")
        return None


def collect_channel():
    name = input(
        "\nEnter channel name, @handle, or URL: "
    ).strip()

    if not name:
        print("Channel name required.")
        return

    channel_url = resolve_channel(name)

    if not channel_url:
        return

    channel_url = channel_url.rstrip("/")

    if not channel_url.endswith(("/videos", "/streams")):
        channel_url += "/videos"

    print(f"\nChannel: {channel_url}")

    videos = run_ytdlp(channel_url)

    if not videos:
        print("No videos found.")
        return

    catalog = load_catalog()
    existing_ids = get_existing_ids()

    new_videos = [
        video for video in videos
        if video["id"] not in existing_ids
    ]

    if new_videos:
        catalog.extend(new_videos)
        save_catalog(catalog)

    new_urls = [
        video["url"] for video in videos
        if video["id"] not in existing_ids
    ]

    if new_urls:
        with open(OUTPUT_FILE, "a", encoding="utf-8") as f:
            for url in new_urls:
                f.write(url + "\n")

    print("\n========== COMPLETED ==========")
    print(f"Videos discovered: {len(videos)}")
    print(f"New videos added: {len(new_videos)}")
    print(f"New links saved: {len(new_urls)}")
    print(f"Total catalogue: {len(catalog)}")

    print("\nUpdated urls.txt and catalog.json")


if __name__ == "__main__":
    print("=" * 40)
    print("       YOUTUBE CHANNEL COLLECTOR")
    print("=" * 40)

    collect_channel()
