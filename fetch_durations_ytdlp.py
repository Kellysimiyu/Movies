#!/usr/bin/env python3
"""
Retry unknown durations using yt-dlp --batch-file.

The big win: each worker launches ONE yt-dlp process that handles
thousands of URLs, instead of launching a new process per video.
That's the difference between ~8 hours and ~45 minutes for 21k videos.

Usage:
    python3 fetch_durations_ytdlp.py
    python3 fetch_durations_ytdlp.py --workers 6
    python3 fetch_durations_ytdlp.py --workers 10       # go faster
    python3 fetch_durations_ytdlp.py --limit 500        # test on 500
    python3 fetch_durations_ytdlp.py --all              # re-check everything
    python3 fetch_durations_ytdlp.py --dry-run          # just report
"""

import subprocess
import json
import sys
import time
import argparse
import tempfile
import threading
from pathlib import Path

BASE         = Path(__file__).resolve().parent
CATALOG_FILE = BASE / "catalog.json"
CACHE_FILE   = BASE / "durations.json"

PRINT_FMT = "%(id)s|%(duration)s"


# ─────────────────────────────────────────────────────────────────
# File helpers
# ─────────────────────────────────────────────────────────────────

def load_json(path, default):
    if not path.exists():
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def save_json(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


# ─────────────────────────────────────────────────────────────────
# Worker
# ─────────────────────────────────────────────────────────────────

def run_worker(worker_id, video_ids, cache, cache_lock, stats):
    if not video_ids:
        return

    # Write a batch file with one URL per line
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as bf:
        for vid in video_ids:
            bf.write(f"https://www.youtube.com/watch?v={vid}\n")
        batch_path = bf.name

    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--ignore-config",
        "--batch-file", batch_path,
        "--skip-download",
        "--print", PRINT_FMT,
        "--quiet",
        "--no-warnings",
        "--ignore-errors",
        "--no-check-formats",
        "--no-playlist",
        "--socket-timeout", "15",
        "--retries", "2",
        "--extractor-retries", "1",
    ]

    print(f"[w{worker_id}] starting on {len(video_ids):,} URLs")
    start = time.time()
    recovered_here = 0

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )

        # Read stdout line by line as yt-dlp emits them
        for line in proc.stdout:
            line = line.rstrip("\n")
            if not line:
                continue
            parts = line.split("|", 1)
            if len(parts) != 2:
                continue
            vid, dur_str = parts
            # yt-dlp prints "NA" or "None" when duration is missing
            if dur_str in ("NA", "None", ""):
                continue
            try:
                dur = float(dur_str)
            except (ValueError, TypeError):
                continue

            with cache_lock:
                cache[vid] = dur
                stats["done"] += 1
                stats["recovered"] += 1
            recovered_here += 1

        proc.stdout.close()
        proc.wait()

    except KeyboardInterrupt:
        try:
            proc.terminate()
        except Exception:
            pass
        raise
    finally:
        Path(batch_path).unlink(missing_ok=True)
        with cache_lock:
            stats["workers_finished"] += 1
        elapsed = time.time() - start
        print(f"[w{worker_id}] done in {elapsed:.0f}s "
              f"— recovered {recovered_here:,}")


# ─────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=6,
                        help="Parallel yt-dlp processes (default: 6)")
    parser.add_argument("--all", action="store_true",
                        help="Re-check every video, not just unknowns")
    parser.add_argument("--dry-run", action="store_true",
                        help="Just report what would be done")
    parser.add_argument("--limit", type=int, default=0,
                        help="Limit to first N targets (for testing)")
    args = parser.parse_args()

    # Verify yt-dlp is installed
    try:
        subprocess.run(
            [sys.executable, "-m", "yt_dlp", "--version"],
            capture_output=True, check=True, timeout=15,
        )
    except (subprocess.CalledProcessError, FileNotFoundError,
            subprocess.TimeoutExpired):
        print("Error: yt-dlp not available.")
        print("Install / upgrade with:  pip install -U yt-dlp")
        return 1

    catalog = load_json(CATALOG_FILE, [])
    if not isinstance(catalog, list) or not catalog:
        print("Error: catalog.json missing or empty.")
        return 1

    cache = load_json(CACHE_FILE, {})
    if not isinstance(cache, dict):
        cache = {}

    # Migrate nulls out of cache (they need retrying)
    migrated = 0
    for vid in list(cache.keys()):
        if cache[vid] is None:
            del cache[vid]
            migrated += 1
    if migrated:
        print(f"Removed {migrated:,} null entries from cache")

    # Seed cache from any 'duration' fields already in catalog
    for item in catalog:
        if isinstance(item, dict):
            vid = item.get("id")
            dur = item.get("duration")
            if vid and dur is not None and vid not in cache:
                try:
                    cache[vid] = float(dur)
                except (TypeError, ValueError):
                    pass

    # Build targets (unknowns only, or all if --all)
    targets = []
    seen = set()
    for item in catalog:
        if not isinstance(item, dict):
            continue
        vid = item.get("id")
        if not vid or vid in seen:
            continue
        seen.add(vid)
        if vid in cache and not args.all:
            continue
        targets.append(vid)

    if args.limit > 0:
        targets = targets[:args.limit]

    print(f"Catalog entries:  {len(catalog):,}")
    print(f"Known durations:  {len(cache):,}")
    print(f"Targets:          {len(targets):,}")
    print(f"Workers:          {args.workers}")

    if not targets:
        print("\nNothing to fetch.")
        return 0

    if args.dry_run:
        print("\n[dry-run] Would fetch the above, nothing changed.")
        return 0

    # ── Distribute targets across workers ─────────────────────
    chunks = [[] for _ in range(args.workers)]
    for i, vid in enumerate(targets):
        chunks[i % args.workers].append(vid)

    sizes = [len(c) for c in chunks]
    print(f"Chunks:           {sizes}\n")

    cache_lock = threading.Lock()
    stats = {"done": 0, "recovered": 0, "workers_finished": 0}
    start = time.time()

    # ── Progress + periodic save thread ───────────────────────
    def progress_and_save():
        last_saved = 0
        while True:
            with cache_lock:
                done = stats["done"]
                finished = stats["workers_finished"]

            if finished >= args.workers:
                break

            elapsed = time.time() - start
            rate = done / elapsed if elapsed > 0 else 0
            eta = (len(targets) - done) / rate if rate > 0 else 0
            print(
                f"\r  {done:,}/{len(targets):,}  "
                f"({rate:4.1f}/s, ETA {int(eta//60)}m{int(eta%60):02d}s)   ",
                end="", flush=True,
            )

            # Save every 200 recovered
            if done - last_saved >= 200:
                with cache_lock:
                    snapshot = dict(cache)
                save_json(CACHE_FILE, snapshot)
                last_saved = done

            time.sleep(2)

    prog = threading.Thread(target=progress_and_save, daemon=True)
    prog.start()

    # ── Launch workers ────────────────────────────────────────
    threads = []
    try:
        for i in range(args.workers):
            t = threading.Thread(
                target=run_worker,
                args=(i, chunks[i], cache, cache_lock, stats),
            )
            t.start()
            threads.append(t)

        for t in threads:
            t.join()
    except KeyboardInterrupt:
        print("\n\nInterrupted — saving progress...")
        with cache_lock:
            snapshot = dict(cache)
        save_json(CACHE_FILE, snapshot)
        return 130

    # Final save
    with cache_lock:
        snapshot = dict(cache)
    save_json(CACHE_FILE, snapshot)

    elapsed = time.time() - start
    print(f"\n\nDone in {elapsed:.0f}s")
    print(f"  Recovered: {stats['recovered']:,}")
    print(f"  Still unknown: {len(targets) - stats['recovered']:,}")
    print(f"\nNow run:")
    print(f"  python3 remove_trailers.py --also-short 0.5 --dry-run")

    return 0


if __name__ == "__main__":
    sys.exit(main())
