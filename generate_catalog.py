import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

URLS_FILE = Path("urls.txt")
CATALOG_FILE = Path("catalog.json")

MAX_NEW_VIDEOS = int(os.getenv("MAX_NEW_VIDEOS", "500"))
SAVE_EVERY = 25


def extract_id(url):
    match = re.search(
        r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})",
        url
    )
    return match.group(1) if match else None


def save_catalog(videos):
    temp_file = CATALOG_FILE.with_suffix(".tmp")
    temp_file.write_text(
        json.dumps(videos, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8"
    )
    temp_file.replace(CATALOG_FILE)


if not URLS_FILE.exists():
    sys.exit("ERROR: urls.txt not found.")

try:
    existing = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    if not isinstance(existing, list):
        existing = []
except (FileNotFoundError, json.JSONDecodeError):
    existing = []

seen = {str(v["id"]) for v in existing if v.get("id")}

pending = []

for line in URLS_FILE.read_text(encoding="utf-8").splitlines():
    url = line.strip().split("|")[0].strip()

    if not url.startswith("http"):
        continue

    video_id = extract_id(url)

    if video_id and video_id in seen:
        continue

    pending.append(url)

print(f"Existing videos: {len(existing)}", flush=True)
print(f"Remaining URLs: {len(pending)}", flush=True)
print(f"Maximum new videos this run: {MAX_NEW_VIDEOS}", flush=True)

if not pending:
    print("Catalogue is already up to date.", flush=True)
    sys.exit(0)

batch = pending[:MAX_NEW_VIDEOS]

with tempfile.NamedTemporaryFile(
    mode="w",
    encoding="utf-8",
    suffix=".txt",
    delete=False
) as file:
    file.write("\n".join(batch))
    batch_file = file.name

cmd = [
    sys.executable, "-m", "yt_dlp",
    "--batch-file", batch_file,
    "--dump-json",
    "--flat-playlist",
    "--ignore-errors",
    "--no-warnings",
    "--skip-download"
]

process = None
new_count = 0

try:
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=None,
        text=True,
        bufsize=1
    )

    for line in process.stdout:
        try:
            item = json.loads(line)

            video_id = str(item.get("id", "")).strip()
            title = str(item.get("title", "")).strip()

            if (
                not video_id
                or not title
                or not re.fullmatch(r"[A-Za-z0-9_-]+", video_id)
                or video_id in seen
            ):
                continue

            existing.append({
                "id": video_id,
                "title": title,
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "thumbnail": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
            })

            seen.add(video_id)
            new_count += 1

            if new_count % SAVE_EVERY == 0:
                save_catalog(existing)
                print(
                    f"Saved {len(existing)} total videos "
                    f"(+{new_count} this run)",
                    flush=True
                )

        except (json.JSONDecodeError, TypeError):
            continue

    process.wait()

finally:
    if process and process.poll() is None:
        process.terminate()
        process.wait()

    Path(batch_file).unlink(missing_ok=True)
    save_catalog(existing)

print(f"\nCompleted. Added {new_count} videos.", flush=True)
print(f"Total catalogue: {len(existing)} videos.", flush=True)
