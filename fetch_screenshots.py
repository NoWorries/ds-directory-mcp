"""
Fetch and store one screenshot per design system into screenshots/<slug>.jpg,
via thum.io's free screenshot service.

Skips any system that already has a stored screenshot, so this is cheap to run
repeatedly — only newly added systems (or ones with a --force re-fetch) cost a
network request. Meant to run on a slow, separate cadence (see
.github/workflows/fetch-screenshots.yml) — screenshots don't need to be as fresh
as the doc index, and thum.io's free tier shouldn't be hit on every reindex.

Usage:
    python fetch_screenshots.py           # only systems missing a screenshot
    python fetch_screenshots.py --force   # re-fetch every system's screenshot
"""

import sys
from pathlib import Path

import requests
import yaml

from slug import slugify

SYSTEMS_REGISTRY = Path(__file__).parent / "systems.yaml"
SCREENSHOTS_DIR = Path(__file__).parent / "screenshots"


def load_systems() -> list[dict]:
    with open(SYSTEMS_REGISTRY) as f:
        return yaml.safe_load(f) or []


def fetch_screenshot(url: str) -> bytes | None:
    thumb_url = f"https://image.thum.io/get/width/320/{url}"
    try:
        response = requests.get(thumb_url, timeout=30)
        if response.status_code == 200 and response.content:
            return response.content
    except requests.RequestException as exc:
        print(f"  failed: {exc}")
    return None


def main(force: bool = False) -> None:
    SCREENSHOTS_DIR.mkdir(exist_ok=True)
    systems = load_systems()
    fetched = 0

    for entry in systems:
        start_url = (entry.get("start_urls") or [None])[0]
        if not start_url:
            continue

        out_path = SCREENSHOTS_DIR / f"{slugify(entry['name'])}.jpg"
        if out_path.exists() and not force:
            continue

        print(f"Fetching screenshot for {entry['name']} ({start_url})...")
        content = fetch_screenshot(start_url)
        if content:
            out_path.write_bytes(content)
            fetched += 1
        else:
            print(f"  no screenshot obtained for {entry['name']}")

    print(f"\nDone. {fetched} new screenshot(s) fetched.")


if __name__ == "__main__":
    main(force="--force" in sys.argv)
