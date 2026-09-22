"""
Fetch and store two screenshots per design system — screenshots/<slug>.jpg
(card size) and screenshots/<slug>-large.jpg (detail-page hero size) — via
WordPress's free mshots screenshot service (thum.io's free tier now requires
a paid account — confirmed live, it was serving an "Image not authorized"
placeholder instead of a real screenshot). See generate_directory.py's
THUMBNAIL_SIZES for the exact widths each is fetched at.

Only re-fetches a system whose homepage has actually changed since its
screenshot was last taken (same ETag/Last-Modified check ingest.py uses for
"has this site changed" — see screenshot_meta.json, this file's equivalent of
systems.yaml's etag/last_modified fields) — a design system's homepage
doesn't visually change often, so this is meant to run on a slow cadence
without burning a screenshot fetch on every single pass regardless. No
signal at all (a site that sends neither header) means we can't tell, so it
re-fetches every time in that case, same as ingest.py's content_unchanged().

mshots can return a "still generating" placeholder for a URL it hasn't
rendered before — one retry after a short wait is usually enough for the
real screenshot to be ready, which matters here since this result gets
committed and stays until the next real refetch (unlike the live per-request
fallback in thumbnail_src(), which just eats that placeholder as transient).

Usage:
    python fetch_screenshots.py           # only systems missing a screenshot or changed since last capture
    python fetch_screenshots.py --force   # re-fetch every system's screenshots regardless of change
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path
from urllib.parse import quote

import requests
import yaml

from generate_directory import THUMBNAIL_SIZES
from slug import slugify
from text_utils import full_name

SYSTEMS_REGISTRY = Path(__file__).parent / "systems.yaml"
SCREENSHOTS_DIR = Path(__file__).parent / "screenshots"
SCREENSHOT_META_FILE = Path(__file__).parent / "screenshot_meta.json"

# mshots' placeholder for "haven't rendered this URL yet" (WordPress logo +
# "Generating Preview...") is a small, fixed JPEG regardless of the requested
# width. Compared by MD5 rather than byte size — confirmed live this had
# silently broken: the byte-size check here was 3268 for a long time, but
# mshots' actual placeholder is now 8737 bytes (presumably changed on their
# end at some point), so the size check quietly stopped matching and 246 of
# ~483 committed screenshots turned out to be this exact placeholder, saved
# as if it were a real screenshot, for as long as this went unnoticed. A
# content hash still breaks the same way if mshots changes the placeholder
# again, but at least it can't silently match the WRONG thing the way a
# stale byte count can — a real screenshot would need to coincidentally hash
# identically, not just happen to be the same size.
_PLACEHOLDER_MD5 = "e89e34619e53928489a0c703c761cd58"
_RETRY_WAIT_SECONDS = 8


def load_systems() -> list[dict]:
    with open(SYSTEMS_REGISTRY) as f:
        return yaml.safe_load(f) or []


def load_screenshot_meta() -> dict:
    if not SCREENSHOT_META_FILE.exists():
        return {}
    return json.loads(SCREENSHOT_META_FILE.read_text())


def save_screenshot_meta(meta: dict) -> None:
    SCREENSHOT_META_FILE.write_text(json.dumps(meta, indent=2, sort_keys=True))


def fetch_homepage_signal(url: str) -> dict:
    """ETag/Last-Modified for a URL, if the server sends either — the same
    signal ingest.fetch_change_signal() uses, duplicated here (rather than
    imported) so this script's dependencies stay the lightweight
    requests+PyYAML pair fetch-screenshots.yml installs, not ingest.py's
    full bs4/qdrant-client chain."""
    headers = {"User-Agent": "ds-directory-mcp/1.0"}
    try:
        response = requests.head(url, timeout=10, allow_redirects=True, headers=headers)
        if response.status_code >= 400:
            response = requests.get(url, timeout=10, headers=headers)
    except requests.RequestException:
        return {}
    signal = {}
    if response.headers.get("ETag"):
        signal["etag"] = response.headers["ETag"]
    if response.headers.get("Last-Modified"):
        signal["last_modified"] = response.headers["Last-Modified"]
    return signal


def homepage_unchanged(previous: dict | None, current: dict) -> bool:
    if not previous or not current:
        return False
    if previous.get("etag") and previous["etag"] == current.get("etag"):
        return True
    if previous.get("last_modified") and previous["last_modified"] == current.get("last_modified"):
        return True
    return False


def fetch_screenshot(url: str, width: int) -> bytes | None:
    thumb_url = f"https://s0.wp.com/mshots/v1/{quote(url, safe='')}?w={width}"
    for attempt in range(2):
        try:
            response = requests.get(thumb_url, timeout=30)
        except requests.RequestException as exc:
            print(f"  failed: {exc}")
            return None
        if response.status_code != 200 or not response.content:
            return None
        if hashlib.md5(response.content).hexdigest() != _PLACEHOLDER_MD5:
            return response.content
        if attempt == 0:
            print(f"  still generating, waiting {_RETRY_WAIT_SECONDS}s and retrying once...")
            time.sleep(_RETRY_WAIT_SECONDS)
    print("  only a placeholder was available after retrying — skipping")
    return None


def main(force: bool = False) -> None:
    SCREENSHOTS_DIR.mkdir(exist_ok=True)
    systems = load_systems()
    meta = load_screenshot_meta()
    fetched = 0

    for entry in systems:
        start_url = (entry.get("start_urls") or [None])[0]
        if not start_url:
            continue

        name = full_name(entry)
        slug = slugify(name)
        has_all_sizes = all((SCREENSHOTS_DIR / f"{slug}{suffix}.jpg").exists() for suffix, _ in THUMBNAIL_SIZES.values())

        if has_all_sizes and not force:
            current_signal = fetch_homepage_signal(start_url)
            if homepage_unchanged(meta.get(name), current_signal):
                continue
        else:
            current_signal = fetch_homepage_signal(start_url)

        print(f"Fetching screenshot(s) for {name} ({start_url})...")
        got_any = False
        for size, (suffix, width) in THUMBNAIL_SIZES.items():
            content = fetch_screenshot(start_url, width)
            if content:
                (SCREENSHOTS_DIR / f"{slug}{suffix}.jpg").write_bytes(content)
                fetched += 1
                got_any = True
            else:
                print(f"  no {size} screenshot obtained for {name}")

        if got_any:
            meta[name] = current_signal

    save_screenshot_meta(meta)
    print(f"\nDone. {fetched} new screenshot(s) fetched.")


if __name__ == "__main__":
    main(force="--force" in sys.argv)
