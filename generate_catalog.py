import argparse
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent

URLS_FILE = BASE / "urls.txt"
CATALOG_FILE = BASE / "catalog.json"
ATTEMPTED_FILE = BASE / "attempted_ids.txt"
FAILED_FILE = BASE / "failed_ids.txt"

BATCH_SIZE = 20
WORKERS = 3
MAX_RETRIES = 3


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


def read_ids(path):
    if not path.exists():
        return set()

    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def atomic_write(path, content):
    fd, temp_path = tempfile.mkstemp(dir=BASE)

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(content)

        os.replace(temp_path, path)

    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def save_json(data):
    atomic_write(
        CATALOG_FILE,
        json.dumps(data, ensure_ascii=False, indent=2)
    )


def save_ids(path, ids):
    content = "\n".join(sorted(ids))

    if content:
        content += "\n"

    atomic_write(path, content)


def valid_title(title):
    if not isinstance(title, str):
        return False

    title = title.strip()

    if len(title) < 3:
        return False

    if re.match(r"^youtube\s+video\b", title, re.IGNORECASE):
        return False

    if title.lower() in ("video", "unknown", "untitled"):
        return False

    return True


def extract_video(video_id, url):
    attempts = [
        [],
        ["--extractor-args", "youtube:player_client=web"],
        ["--extractor-args", "youtube:player_client=android"],
    ]

    last_error = "Unknown extraction error"

    for attempt_number, extra_args in enumerate(attempts, 1):

        command = [
            sys.executable,
            "-m", "yt_dlp",
            "--ignore-config",
            "--ignore-no-formats-error",
            "--dump-single-json",
            "--no-warnings",
            "--no-playlist",
            "--skip-download",
        ] + extra_args + [url]

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=75
            )

            if result.returncode == 0 and result.stdout.strip():
                info = json.loads(result.stdout)

                title = info.get("title")

                if valid_title(title):
                    return {
                        "status": "success",
                        "video": {
                            "id": video_id,
                            "title": title.strip(),
                            "url": f"https://www.youtube.com/watch?v={video_id}",
                            "thumbnail": (
                                info.get("thumbnail")
                                or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
                            )
                        }
                    }

                last_error = "YouTube returned an invalid or placeholder title."

            else:
                last_error = result.stderr[-1000:] or "No metadata returned."

        except subprocess.TimeoutExpired:
            last_error = "Extraction timed out."

        except (json.JSONDecodeError, Exception) as error:
            last_error = str(error)

        if attempt_number < MAX_RETRIES:
            time.sleep(2)

    return {
        "status": "failed",
        "error": last_error,
        "id": video_id
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Retry previously failed videos."
    )
    args = parser.parse_args()

    if not URLS_FILE.exists():
        print("urls.txt not found.")
        return

    if CATALOG_FILE.exists():
        with open(CATALOG_FILE, encoding="utf-8") as file:
            catalog = json.load(file)
    else:
        catalog = []

    if not isinstance(catalog, list):
        print("catalog.json must contain a JSON list.")
        return

    attempted = read_ids(ATTEMPTED_FILE)
    failed = read_ids(FAILED_FILE)

    # Remove entries that contain placeholder titles.
    placeholders = [
        item for item in catalog
        if isinstance(item, dict)
        and not valid_title(item.get("title"))
    ]

    placeholder_ids = {
        item.get("id")
        for item in placeholders
        if item.get("id")
    }

    if placeholders:
        backup = BASE / (
            f"catalog.backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        )

        shutil.copy2(CATALOG_FILE, backup)

        catalog = [
            item for item in catalog
            if item not in placeholders
        ]

        attempted.difference_update(placeholder_ids)
        failed.difference_update(placeholder_ids)

        print(f"Removed {len(placeholders)} placeholder entries.")
        print(f"Backup created: {backup.name}")

    existing_ids = {
        item["id"]
        for item in catalog
        if isinstance(item, dict) and item.get("id")
    }

    attempted.update(existing_ids)

    # Read and deduplicate URLs.
    urls = []
    seen = set()

    with open(URLS_FILE, encoding="utf-8") as file:
        for line in file:
            url = line.strip()
            video_id = get_video_id(url)

            if video_id and video_id not in seen:
                urls.append((video_id, url))
                seen.add(video_id)

    url_map = dict(urls)

    # Previously invalid entries get priority.
    priority_ids = [
        video_id for video_id in placeholder_ids
        if video_id in url_map
    ]

    if args.retry_failed:
        priority_ids.extend(
            video_id for video_id in failed
            if video_id in url_map
        )

    priority_ids = list(dict.fromkeys(priority_ids))

    remaining = [
        video_id for video_id, url in urls
        if video_id not in attempted
        and video_id not in failed
        and video_id not in existing_ids
    ]

    candidates = priority_ids + remaining
    batch_ids = candidates[:BATCH_SIZE]

    print(f"\nExisting catalogue: {len(catalog)} videos")
    print(f"Total URLs: {len(urls)}")
    print(f"Remaining unprocessed: {len(remaining)}")
    print(f"Previously failed: {len(failed)}")
    print(f"Parallel workers: {WORKERS}")
    print(f"Processing this batch: {len(batch_ids)} videos")

    if not batch_ids:
        print("\nNo new videos to process.")

        if failed:
            print("To retry failed videos, run:")
            print("python3 generate_catalog.py --retry-failed")

        return

    results = []

    print("\nStarting parallel extraction...\n")

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=WORKERS
    ) as executor:

        futures = {
            executor.submit(
                extract_video,
                video_id,
                url_map[video_id]
            ): video_id
            for video_id in batch_ids
        }

        for number, future in enumerate(
            concurrent.futures.as_completed(futures), 1
        ):
            video_id = futures[future]

            try:
                result = future.result()
            except Exception as error:
                result = {
                    "status": "failed",
                    "id": video_id,
                    "error": str(error)
                }

            results.append(result)

            if result["status"] == "success":
                print(
                    f"[{number}/{len(batch_ids)}] "
                    f"ADDED: {result['video']['title']}"
                )
            else:
                print(
                    f"[{number}/{len(batch_ids)}] "
                    f"FAILED: {video_id} - {result['error'][:180]}"
                )

    # Merge results once, after all workers finish.
    added = 0
    failed_count = 0

    for result in results:
        video_id = result.get("id")

        attempted.add(video_id)

        if result["status"] == "success":
            video = result["video"]

            if video_id not in existing_ids:
                catalog.append(video)
                existing_ids.add(video_id)
                added += 1

            failed.discard(video_id)

        else:
            failed.add(video_id)
            failed_count += 1

    # Save the completed batch.
    save_json(catalog)
    save_ids(ATTEMPTED_FILE, attempted)
    save_ids(FAILED_FILE, failed)

    print("\n========== BATCH COMPLETED ==========")
    print(f"Successfully added: {added}")
    print(f"Failed: {failed_count}")
    print(f"Total catalogue: {len(catalog)}")
    print(f"Remaining URLs: {len(urls) - len(attempted)}")
    print("Files updated:")
    print("- catalog.json")
    print("- attempted_ids.txt")
    print("- failed_ids.txt")


if __name__ == "__main__":
    main()
