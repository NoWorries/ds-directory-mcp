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

SYSTEMS_REGISTRY = Path(__file__).parent / "systems.yaml"

STRIP_TAGS = ["script", "style", "nav", "footer", "header", "noscript"]


def ensure_collection(client: QdrantClient) -> None:
    if client.collection_exists(QDRANT_COLLECTION):
        return
    client.create_collection(
        collection_name=QDRANT_COLLECTION,
        vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
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
    return soup.get_text(separator="\n", strip=True)


def extract_links(soup: BeautifulSoup, base_url: str, root_netloc: str) -> set[str]:
    links = set()
    for a in soup.find_all("a", href=True):
        absolute = urljoin(base_url, a["href"]).split("#")[0]
        if urlparse(absolute).netloc == root_netloc:
            links.add(absolute)
    return links


def crawl(start_urls: list[str], max_pages: int = 200) -> dict[str, str]:
    """Breadth-first crawl restricted to the start URLs' domain(s). Returns {url: text}."""
    root_netlocs = {urlparse(u).netloc for u in start_urls}
    to_visit = list(start_urls)
    visited: set[str] = set()
    pages: dict[str, str] = {}

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
        if text:
            pages[url] = text

        for netloc in root_netlocs:
            for link in extract_links(soup, url, netloc):
                if link not in visited:
                    to_visit.append(link)

    return pages


def load_registry() -> list[dict]:
    with open(SYSTEMS_REGISTRY) as f:
        return yaml.safe_load(f) or []


def delete_existing(client: QdrantClient, design_system_name: str) -> None:
    client.delete(
        collection_name=QDRANT_COLLECTION,
        points_selector=Filter(
            must=[FieldCondition(key="design_system_name", match=MatchValue(value=design_system_name))]
        ),
    )


def ingest(design_system_name: str, start_urls: list[str]) -> None:
    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
    ensure_collection(client)
    delete_existing(client, design_system_name)

    pages = crawl(start_urls)
    print(f"\nFetched {len(pages)} pages. Chunking + embedding...")

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

    if args[0] == "--all":
        for entry in load_registry():
            print(f"\n=== {entry['name']} ===")
            ingest(design_system_name=entry["name"], start_urls=entry["start_urls"])

    elif args[0] == "--system":
        if len(args) < 2:
            print("Usage: python ingest.py --system \"<name from systems.yaml>\"")
            sys.exit(1)
        target_name = args[1]
        matches = [e for e in load_registry() if e["name"] == target_name]
        if not matches:
            print(f"No entry named {target_name!r} in systems.yaml")
            sys.exit(1)
        entry = matches[0]
        ingest(design_system_name=entry["name"], start_urls=entry["start_urls"])

    elif len(args) >= 2:
        ingest(design_system_name=args[0], start_urls=args[1:])

    else:
        print(__doc__)
        sys.exit(1)
