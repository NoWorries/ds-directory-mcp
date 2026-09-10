"""
Phase 1: Scrape a design system's docs, chunk the text, embed it, and load it into Qdrant.

Usage:
    python ingest.py <design_system_name> <start_url> [start_url2 ...]
    python ingest.py --all                 # re-ingest every system in systems.yaml
    python ingest.py --system "Shopify Polaris"   # re-ingest one entry from systems.yaml

Example:
    python ingest.py "Atlassian Design System" https://atlassian.design/components

Re-running for a design_system_name that's already indexed replaces its old chunks
(deletes by that payload filter first), so scheduled re-ingestion doesn't accumulate
stale duplicates as source docs change.
"""

import re
import sys
import time
import uuid
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
from resources import classify_links, merge_resources, probe_well_known

SYSTEMS_REGISTRY = Path(__file__).parent / "systems.yaml"

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


def crawl(
    start_urls: list[str],
    max_pages: int = 30,
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
) -> tuple[dict[str, str], set[str]]:
    """Breadth-first crawl restricted to the start URLs' domain(s).

    Returns ({url: text}, all_links_seen) — all_links_seen includes off-domain
    links (GitHub, Storybook, Figma, etc.) for resource discovery, even though
    only same-domain pages are actually crawled and embedded.

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

    return pages, all_links_seen


def load_registry() -> list[dict]:
    with open(SYSTEMS_REGISTRY) as f:
        return yaml.safe_load(f) or []


REGISTRY_HEADER = (
    "# Registry of external design systems to crawl and index.\n"
    "# Add an entry per system: a name and one or more start URLs to crawl from.\n"
    "# XUI is intentionally excluded — it has its own MCP server (xui-components-mcp).\n"
    "#\n"
    "# 'resources' is auto-populated by ingest.py from links found while crawling —\n"
    "# don't hand-edit it, your changes will be overwritten on the next run.\n"
)


def save_registry(entries: list[dict]) -> None:
    with open(SYSTEMS_REGISTRY, "w") as f:
        f.write(REGISTRY_HEADER + "\n")
        yaml.dump(entries, f, sort_keys=False, allow_unicode=True, default_flow_style=False)


def update_registry_resources(design_system_name: str, resources: dict[str, list[str]]) -> None:
    if not resources:
        return
    entries = load_registry()
    for entry in entries:
        if entry["name"] == design_system_name:
            entry["resources"] = resources
            break
    save_registry(entries)


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

    pages, all_links_seen = crawl(
        start_urls,
        max_pages=max_pages,
        include_patterns=include_patterns,
        exclude_patterns=exclude_patterns,
    )
    print(f"\nFetched {len(pages)} pages. Chunking + embedding...")

    resources = merge_resources(classify_links(all_links_seen), probe_well_known(start_urls[0]))
    if resources:
        print(f"  discovered resources: {resources}")
        update_registry_resources(design_system_name, resources)

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


if __name__ == "__main__":
    args = sys.argv[1:]

    if not args:
        print(__doc__)
        sys.exit(1)

    def ingest_entry(entry: dict) -> None:
        ingest(
            design_system_name=entry["name"],
            start_urls=entry["start_urls"],
            max_pages=entry.get("max_pages", 30),
            include_patterns=entry.get("include_patterns"),
            exclude_patterns=entry.get("exclude_patterns"),
        )

    if args[0] == "--all":
        for entry in load_registry():
            print(f"\n=== {entry['name']} ===")
            ingest_entry(entry)

    elif args[0] == "--system":
        if len(args) < 2:
            print("Usage: python ingest.py --system \"<name from systems.yaml>\"")
            sys.exit(1)
        target_name = args[1]
        matches = [e for e in load_registry() if e["name"] == target_name]
        if not matches:
            print(f"No entry named {target_name!r} in systems.yaml")
            sys.exit(1)
        ingest_entry(matches[0])

    elif len(args) >= 2:
        ingest(design_system_name=args[0], start_urls=args[1:])

    else:
        print(__doc__)
        sys.exit(1)
