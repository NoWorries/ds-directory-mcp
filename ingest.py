"""
Phase 1: Scrape a design system's docs, chunk the text, embed it, and load it into Qdrant.

Usage:
    python ingest.py <design_system_name> <start_url> [start_url2 ...]
    python ingest.py --all [--force]              # re-ingest every system in systems.yaml
    python ingest.py --new                        # only systems never indexed before
    python ingest.py --system "Shopify Polaris" [--force]   # re-ingest one entry

Example:
    python ingest.py "Atlassian Design System" https://atlassian.design/components

Re-running for a design_system_name that's already indexed replaces its old chunks
(deletes by that payload filter first), so scheduled re-ingestion doesn't accumulate
stale duplicates as source docs change.

Change detection (--all / --system, not --new): before crawling, does a cheap HEAD
request on the first start_url and compares its ETag/Last-Modified header against
what was stored last run. If unchanged, the whole crawl+embed is skipped. Many doc
sites (especially SPAs) don't send either header — in that case there's no signal to
compare, so it always re-crawls. This only checks the start_url, not every page, so
it's a coarse "did the site's front door change" heuristic, not a guarantee nothing
changed deeper in the site. Pass --force to bypass the check and always re-crawl.
"""

from __future__ import annotations

import json
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
import yaml
from bs4 import BeautifulSoup
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

from chunking import chunk_text
from config import (
    CRAWL_DELAY_SECONDS,
    EMBEDDING_DIM,
    QDRANT_API_KEY,
    QDRANT_COLLECTION,
    QDRANT_URL,
)
from embeddings import embed_texts
from resources import classify_links, enrich_resources, merge_resources, probe_well_known

SYSTEMS_REGISTRY = Path(__file__).parent / "systems.yaml"
PAGES_INDEX_FILE = Path(__file__).parent / "pages_index.json"

STRIP_TAGS = ["script", "style", "nav", "footer", "header", "noscript"]

# Repeated boilerplate lines that show up on nearly every page of many doc sites
# (feedback widgets, cookie notices, etc.) — pure noise for embeddings, stripped verbatim.
BOILERPLATE_LINES = [
    "Was this page helpful?",
    "We use this feedback to improve our documentation.",
    "Back to top",
    "Skip to main content",
    "Skip to content",
    "Accept cookies",
    "This site uses cookies",
    "All rights reserved",
]

# URL path fragments that are almost never useful design system documentation,
# excluded from every crawl regardless of system.
DEFAULT_EXCLUDE_PATTERNS = [
    r"/blog/", r"/careers/", r"/jobs/", r"/legal/", r"/privacy",
    r"/terms", r"/pricing", r"/changelog", r"/login", r"/signin",
    r"/sign-in", r"/search\?", r"/tag/", r"/tags/", r"/about-us",
    r"/press/", r"/events/", r"/newsletter",
]

# Pages with less than this much text (after boilerplate stripping) are almost
# always nav-only, redirect, or placeholder pages — not worth embedding.
MIN_CONTENT_LENGTH = 200


def ensure_collection(client: QdrantClient) -> None:
    if not client.collection_exists(QDRANT_COLLECTION):
        client.create_collection(
            collection_name=QDRANT_COLLECTION,
            vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
        )
    client.create_payload_index(
        collection_name=QDRANT_COLLECTION,
        field_name="design_system_name",
        field_schema=PayloadSchemaType.KEYWORD,
    )


def fetch_page(url: str) -> BeautifulSoup | None:
    try:
        response = requests.get(url, timeout=15, headers={"User-Agent": "ds-directory-mcp/1.0"})
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"  skip {url}: {exc}")
        return None
    return BeautifulSoup(response.text, "html.parser")


def extract_text(soup: BeautifulSoup) -> str:
    for tag_name in STRIP_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()
    text = soup.get_text(separator="\n", strip=True)

    lines = [line for line in text.split("\n") if line.strip() not in BOILERPLATE_LINES]
    return "\n".join(lines)


def extract_links(soup: BeautifulSoup, base_url: str, root_netloc: str) -> set[str]:
    links = set()
    for a in soup.find_all("a", href=True):
        absolute = urljoin(base_url, a["href"]).split("#")[0]
        if urlparse(absolute).netloc == root_netloc:
            links.add(absolute)
    return links


def extract_all_links(soup: BeautifulSoup, base_url: str) -> set[str]:
    """All absolute links regardless of domain — used for resource discovery
    (GitHub, Storybook, Figma, etc. usually live off the docs domain)."""
    return {urljoin(base_url, a["href"]).split("#")[0] for a in soup.find_all("a", href=True)}


def is_excluded(url: str, exclude_patterns: list[str]) -> bool:
    return any(re.search(pattern, url, re.IGNORECASE) for pattern in exclude_patterns)


def matches_include(url: str, include_patterns: list[str]) -> bool:
    if not include_patterns:
        return True
    return any(pattern in url for pattern in include_patterns)


def extract_title(soup: BeautifulSoup, url: str) -> str:
    if soup.title and soup.title.string and soup.title.string.strip():
        return soup.title.string.strip()
    return url


def crawl(
    start_urls: list[str],
    max_pages: int = 30,
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
) -> tuple[dict[str, str], set[str], dict[str, str]]:
    """Breadth-first crawl restricted to the start URLs' domain(s).

    Returns ({url: text}, all_links_seen, {url: page_title}) — all_links_seen
    includes off-domain links (GitHub, Storybook, Figma, etc.) for resource
    discovery, even though only same-domain pages are actually crawled and
    embedded. page_title lets a browsable "jump straight to this page" index
    show something more useful than a bare URL.

    include_patterns: if given, only crawl URLs containing one of these substrings
    (start_urls themselves are always crawled regardless).
    exclude_patterns: additional regexes (on top of DEFAULT_EXCLUDE_PATTERNS) to skip.
    """
    include_patterns = include_patterns or []
    all_exclude_patterns = DEFAULT_EXCLUDE_PATTERNS + (exclude_patterns or [])

    root_netlocs = {urlparse(u).netloc for u in start_urls}
    to_visit = list(start_urls)
    visited: set[str] = set()
    pages: dict[str, str] = {}
    page_titles: dict[str, str] = {}
    all_links_seen: set[str] = set()

    while to_visit and len(visited) < max_pages:
        url = to_visit.pop(0)
        if url in visited:
            continue
        visited.add(url)

        print(f"Crawling: {url}")
        soup = fetch_page(url)
        time.sleep(CRAWL_DELAY_SECONDS)
        if soup is None:
            continue

        text = extract_text(soup)
        if len(text) >= MIN_CONTENT_LENGTH:
            pages[url] = text
            page_titles[url] = extract_title(soup, url)
        else:
            print(f"  skip (too little content): {url}")

        all_links_seen |= extract_all_links(soup, url)

        for netloc in root_netlocs:
            for link in extract_links(soup, url, netloc):
                if link in visited or link in to_visit:
                    continue
                if is_excluded(link, all_exclude_patterns):
                    continue
                if not matches_include(link, include_patterns):
                    continue
                to_visit.append(link)

    return pages, all_links_seen, page_titles


def load_registry() -> list[dict]:
    with open(SYSTEMS_REGISTRY) as f:
        return yaml.safe_load(f) or []


REGISTRY_HEADER = (
    "# Registry of external design systems to crawl and index.\n"
    "# Add an entry per system: a name and one or more start URLs to crawl from.\n"
    "#\n"
    "# 'resources', 'pages_indexed', 'etag', 'last_modified', 'last_checked', and any\n"
    "# '*_meta' fields are auto-populated by ingest.py — don't hand-edit them, your\n"
    "# changes will be overwritten on the next run. A missing 'pages_indexed' means\n"
    "# the entry has never been indexed (picked up by `ingest.py --new`).\n"
)


def save_registry(entries: list[dict]) -> None:
    with open(SYSTEMS_REGISTRY, "w") as f:
        f.write(REGISTRY_HEADER + "\n")
        yaml.dump(entries, f, sort_keys=False, allow_unicode=True, default_flow_style=False)


def update_pages_index(design_system_name: str, page_entries: list[dict]) -> None:
    """Stores {url, title} for every page actually indexed, so the directory
    page can offer a browsable list per system as an alternative to search."""
    index = {}
    if PAGES_INDEX_FILE.exists():
        index = json.loads(PAGES_INDEX_FILE.read_text())
    index[design_system_name] = sorted(page_entries, key=lambda p: p["title"].lower())
    PAGES_INDEX_FILE.write_text(json.dumps(index, indent=2, ensure_ascii=False))


def update_registry_fields(design_system_name: str, fields: dict) -> None:
    entries = load_registry()
    for entry in entries:
        if entry["name"] == design_system_name:
            entry.update({k: v for k, v in fields.items() if v is not None})
            break
    save_registry(entries)


def update_registry_stats(
    design_system_name: str,
    pages_indexed: int,
    resources: dict[str, list[str]],
    enrichment: dict,
) -> None:
    fields = {"pages_indexed": pages_indexed}
    if resources:
        fields["resources"] = resources
    if enrichment:
        fields.update(enrichment)
    update_registry_fields(design_system_name, fields)


def fetch_change_signal(url: str) -> dict:
    """ETag/Last-Modified for a URL, if the server sends them. Empty dict if neither
    is available (common for SPA-rendered doc sites) or the request fails."""
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


def content_unchanged(entry: dict, new_signal: dict) -> bool:
    """True only if the server gave us a signal AND it matches what we stored last
    time. No signal at all means we can't tell, so we always re-crawl in that case."""
    if not new_signal:
        return False
    if entry.get("etag") and entry["etag"] == new_signal.get("etag"):
        return True
    if entry.get("last_modified") and entry["last_modified"] == new_signal.get("last_modified"):
        return True
    return False


def delete_existing(client: QdrantClient, design_system_name: str) -> None:
    client.delete(
        collection_name=QDRANT_COLLECTION,
        points_selector=Filter(
            must=[FieldCondition(key="design_system_name", match=MatchValue(value=design_system_name))]
        ),
    )


def ingest(
    design_system_name: str,
    start_urls: list[str],
    max_pages: int = 30,
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
) -> None:
    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
    ensure_collection(client)
    delete_existing(client, design_system_name)

    pages, all_links_seen, page_titles = crawl(
        start_urls,
        max_pages=max_pages,
        include_patterns=include_patterns,
        exclude_patterns=exclude_patterns,
    )
    print(f"\nFetched {len(pages)} pages. Chunking + embedding...")

    resources = merge_resources(classify_links(all_links_seen), probe_well_known(start_urls[0]))
    enrichment = enrich_resources(resources)
    if resources:
        print(f"  discovered resources: {resources}")
    if enrichment:
        print(f"  enrichment: {enrichment}")
    update_registry_stats(design_system_name, pages_indexed=len(pages), resources=resources, enrichment=enrichment)
    update_pages_index(design_system_name, [{"url": url, "title": page_titles.get(url, url)} for url in pages])

    for url, text in pages.items():
        chunks = chunk_text(text)
        if not chunks:
            continue

        vectors = embed_texts(chunks)
        points = [
            PointStruct(
                id=str(uuid.uuid4()),
                vector=vector,
                payload={
                    "url": url,
                    "design_system_name": design_system_name,
                    "text_content": chunk,
                },
            )
            for chunk, vector in zip(chunks, vectors)
        ]
        client.upsert(collection_name=QDRANT_COLLECTION, points=points)
        print(f"  indexed {len(points)} chunks from {url}")

    print("\nDone.")


def ingest_entry(entry: dict, force: bool = False) -> None:
    start_urls = entry["start_urls"]
    never_indexed = "pages_indexed" not in entry

    if not force and not never_indexed:
        new_signal = fetch_change_signal(start_urls[0])
        if content_unchanged(entry, new_signal):
            print(f"\n=== {entry['name']} === (no changes detected at {start_urls[0]}, skipping)")
            update_registry_fields(entry["name"], {**new_signal, "last_checked": now_iso()})
            return

    print(f"\n=== {entry['name']} ===")
    ingest(
        design_system_name=entry["name"],
        start_urls=start_urls,
        max_pages=entry.get("max_pages", 30),
        include_patterns=entry.get("include_patterns"),
        exclude_patterns=entry.get("exclude_patterns"),
    )
    new_signal = fetch_change_signal(start_urls[0])
    update_registry_fields(entry["name"], {**new_signal, "last_checked": now_iso()})


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    args = sys.argv[1:]
    force = "--force" in args
    args = [a for a in args if a != "--force"]

    if not args:
        print(__doc__)
        sys.exit(1)

    if args[0] == "--all":
        for entry in load_registry():
            ingest_entry(entry, force=force)

    elif args[0] == "--new":
        new_entries = [e for e in load_registry() if "pages_indexed" not in e]
        if not new_entries:
            print("No unindexed systems found — everything in systems.yaml has been indexed at least once.")
        for entry in new_entries:
            ingest_entry(entry, force=True)

    elif args[0] == "--system":
        if len(args) < 2:
            print("Usage: python ingest.py --system \"<name from systems.yaml>\" [--force]")
            sys.exit(1)
        target_name = args[1]
        matches = [e for e in load_registry() if e["name"] == target_name]
        if not matches:
            print(f"No entry named {target_name!r} in systems.yaml")
            sys.exit(1)
        ingest_entry(matches[0], force=force)

    elif len(args) >= 2:
        ingest(design_system_name=args[0], start_urls=args[1:])

    else:
        print(__doc__)
        sys.exit(1)
