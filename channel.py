#!/usr/bin/env python3

import subprocess
import json
import sys
import re
import os
from pathlib import Path

BASE = Path(__file__).resolve().parent

OUTPUT_FILE = BASE / "urls.txt"
CATALOG_FILE = BASE / "catalog.json"

BATCH_SIZE = 25

# Only keep videos 1 hour (3600 seconds) or longer.
MIN_DURATION = 3600

# Fast filter: titles that mean the video is gone.
DEAD_TITLES = {
    "[deleted video]",
    "[private video]",
    "[unavailable video]",
    "deleted video",
    "private video",
    "unavailable video",
}

# Fast filter: availability values that mean it's not publicly watchable.
BAD_AVAILABILITY = {
    "private",
    "premium_only",
    "subscriber_only",
    "needs_auth",
    "unavailable",
}

# Slow verification: availability values we accept as "alive".
GOOD_AVAILABILITY = {"public", "unlisted", ""}


def valid_title(title):
    return (
        isinstance(title, str)
        and len(title.strip()) >= 3
    )


def load_catalog():

    if not CATALOG_FILE.exists():
        return []

    try:
        with open(CATALOG_FILE, encoding="utf-8") as f:
            data = json.load(f)

        return data if isinstance(data, list) else []

    except (json.JSONDecodeError, OSError):
        print("Warning: Could not read catalog.json")
        return []


def save_catalog(data):

    temp = CATALOG_FILE.with_suffix(".tmp")

    with open(temp, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )

    temp.replace(CATALOG_FILE)


def extract_video_id(url):

    match = re.search(
        r"(?:v=|youtu\.be/|/shorts/|/embed/)([a-zA-Z0-9_-]{11})",
        url
    )

    return match.group(1) if match else None


def get_existing_ids():

    ids = set()

    for item in load_catalog():

        if isinstance(item, dict) and item.get("id"):
            ids.add(item["id"])

    if OUTPUT_FILE.exists():

        with open(OUTPUT_FILE, encoding="utf-8") as f:

            for line in f:

                video_id = extract_video_id(line)

                if video_id:
                    ids.add(video_id)

    return ids


def is_obviously_dead(item):
    """Fast check — uses only flat-playlist metadata, no network."""

    title = (item.get("title") or "").strip().lower()

    if title in DEAD_TITLES:
        return True

    availability = (item.get("availability") or "").lower()

    if availability in BAD_AVAILABILITY:
        return True

    return False


def verify_video_alive(video_id, timeout=60):
    """Slow check — resolves the video with yt-dlp to confirm it exists.

    Returns True only if yt-dlp can fetch it AND reports a public/unlisted
    availability. Any error (deleted, private, geo-blocked, members-only,
    bot-check, network failure) returns False.
    """

    command = [
        sys.executable,
        "-m",
        "yt_dlp",

        "--ignore-config",
        "--simulate",
        "--skip-download",
        "--no-warnings",
        "--print", "%(id)s|%(availability)s",

        f"https://www.youtube.com/watch?v={video_id}",
    ]

    try:

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    except subprocess.TimeoutExpired:

        return False

    if result.returncode != 0:
        return False

    for line in result.stdout.strip().splitlines():

        if line.startswith(video_id + "|"):

            availability = line.split("|", 1)[1].strip().lower()

            return availability in GOOD_AVAILABILITY

    return False


def save_new_video(video, existing_ids):

    video_id = video["id"]

    if video_id in existing_ids:
        return False

    catalog = load_catalog()

    catalog.append(video)

    save_catalog(catalog)

    with open(OUTPUT_FILE, "a", encoding="utf-8") as f:
        f.write(video["url"] + "\n")

    existing_ids.add(video_id)

    return True


def run_ytdlp(url):

    command = [
        sys.executable,
        "-m",
        "yt_dlp",

        "--ignore-config",

        # Extract playlist entries without downloading videos.
        "--flat-playlist",

        # Process entries as they arrive.
        "--lazy-playlist",

        # Do not artificially limit playlist entries.
        "--yes-playlist",

        "--dump-json",

        "--no-warnings",
        "--ignore-errors",
        "--skip-download",

        # Avoid unnecessary download checks.
        "--no-check-formats",

        url
    ]

    print("\nStarting channel extraction...")
    print("Playlist limit: UNLIMITED")
    print("Downloading videos: DISABLED")
    print(f"Minimum duration: {MIN_DURATION} seconds (1 hour)")
    print("Dead-link filter: ENABLED (fast + per-candidate verify)")
    print("Saving results progressively.\n")

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1
    )

    existing_ids = get_existing_ids()

    discovered = 0
    added = 0
    skipped = 0
    too_short = 0
    dead = 0

    seen = set()

    try:

        for line in process.stdout:

            try:
                item = json.loads(line)

            except json.JSONDecodeError:
                continue

            video_id = item.get("id")
            title = item.get("title")
            duration = item.get("duration")

            if not video_id:
                continue

            if video_id in seen:
                continue

            seen.add(video_id)

            if not valid_title(title):
                continue

            # Only keep videos 1 hour (3600s) or longer.
            # Entries with unknown duration are skipped.
            if not isinstance(duration, (int, float)) or duration < MIN_DURATION:
                too_short += 1
                continue

            # Already in catalog / urls.txt → don't re-check.
            if video_id in existing_ids:
                skipped += 1
                continue

            # Fast, no-network dead-link filter.
            if is_obviously_dead(item):
                dead += 1
                continue

            # Slow verification — actually resolve the video.
            if not verify_video_alive(video_id):
                dead += 1
                print(
                    f"Skipped dead link: {video_id} — {title[:60]}",
                    flush=True,
                )
                continue

            discovered += 1

            video = {
                "id": video_id,

                "title": title.strip(),

                "url": (
                    f"https://www.youtube.com/watch?v={video_id}"
                ),

                "thumbnail": (
                    item.get("thumbnail")
                    or
                    f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
                ),

                "duration": int(duration),
            }

            if save_new_video(video, existing_ids):

                added += 1

            else:

                skipped += 1

            if discovered % BATCH_SIZE == 0:

                print(
                    f"Discovered: {discovered:,} | "
                    f"New: {added:,} | "
                    f"Duplicates: {skipped:,} | "
                    f"<1hr: {too_short:,} | "
                    f"Dead: {dead:,}",
                    end="\r",
                    flush=True
                )

    except KeyboardInterrupt:

        print("\n\nStopping extraction...")

        process.terminate()

    finally:

        process.stdout.close()

        if process.poll() is None:
            process.wait()

    print("\n\n========== EXTRACTION COMPLETE ==========")

    print(f"Videos ≥ 1hr discovered: {discovered:,}")
    print(f"New videos added:        {added:,}")
    print(f"Duplicates skipped:      {skipped:,}")
    print(f"Skipped (< 1hr/unknown): {too_short:,}")
    print(f"Skipped (dead link):     {dead:,}")

    print(
        f"Total catalogue: {len(load_catalog()):,}"
    )

    print("\nFiles updated:")
    print("catalog.json")
    print("urls.txt")


def resolve_channel(name):

    name = name.strip()

    if name.startswith(("http://", "https://")):

        return name.rstrip("/")

    if name.startswith("@"):

        return "https://www.youtube.com/" + name

    print(f"\nSearching for channel: {name}")

    command = [
        sys.executable,
        "-m",
        "yt_dlp",

        "--ignore-config",
        "--flat-playlist",
        "--dump-json",
        "--no-warnings",

        f"ytsearch30:{name}"
    ]

    try:

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=300
        )

    except subprocess.TimeoutExpired:

        print("Channel search timed out.")
        return None

    channels = {}

    for line in result.stdout.splitlines():

        try:
            item = json.loads(line)

        except json.JSONDecodeError:
            continue

        channel = (
            item.get("channel")
            or item.get("uploader")
        )

        url = (
            item.get("channel_url")
            or item.get("uploader_url")
        )

        if channel and url:

            channels[(channel, url)] = (
                channels.get((channel, url), 0) + 1
            )

    if not channels:

        print("Channel not found.")
        print("Try using its @handle or direct URL.")

        return None

    matches = sorted(
        channels.items(),
        key=lambda x: x[1],
        reverse=True
    )

    print("\nMatching channels:\n")

    for i, ((channel, url), count) in enumerate(
        matches[:10], 1
    ):

        print(f"{i}. {channel} — {url}")

    choice = input("\nSelect channel number: ").strip()

    try:

        index = int(choice) - 1

        return matches[index][0][1]

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

    # Collect the complete videos tab.
    if not channel_url.endswith(
        ("/videos", "/streams", "/shorts")
    ):

        channel_url += "/videos"

    print("\n========================================")
    print("CHANNEL:", channel_url)
    print("========================================")

    run_ytdlp(channel_url)


if __name__ == "__main__":

    print("=" * 50)
    print("       YOUTUBE UNLIMITED COLLECTOR")
    print("=" * 50)

    print("\nSupports:")
    print("- 1,000+ videos")
    print("- 2,000+ videos")
    print("- 4,000+ videos")
    print("- 10,000+ videos")
    print("- Unlimited available public videos")

    print("\nFilters:")
    print("- Only videos 1 hour or longer are saved")
    print("- Dead / private / unavailable links are ignored")

    collect_channel()