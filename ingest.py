"""
Phase 1: Scrape a design system's docs, chunk the text, embed it, and load it into Qdrant.

Usage:
    python ingest.py <design_system_name> <start_url> [start_url2 ...]
    python ingest.py --all                        # re-ingest every system in systems.yaml
    python ingest.py --all --shard 1/4            # ...but only entries at index i where i%4==1
    python ingest.py --new                        # only systems never indexed before
    python ingest.py --new --shallow              # ...but cap every crawl at SHALLOW_MAX_PAGES pages
    python ingest.py --new --shallow --shard 1/4  # ...sharded the same way --all is
    python ingest.py --spa [--shard 1/4]          # re-render every likely_spa system via a headless browser
    python ingest.py --refresh --shallow [--shard 1/4]   # already-indexed systems only, shallow — see below
    python ingest.py --system "Shopify — Polaris" [--force]   # re-ingest one entry (org — design_system)

--shallow: overrides max_pages (both the per-entry systems.yaml value and
DEFAULT_MAX_PAGES) down to SHALLOW_MAX_PAGES for every system it touches.
For getting breadth across many never-before-indexed systems fast — a handful
of real pages per system beats a full deep crawl of a handful of systems when
most of the registry has nothing indexed at all yet. Once a system has been
shallow-crawled it has a pages_indexed value like anything else, so --new
won't pick it up again — the monthly --all full reindex is what deep-crawls
it properly later, exactly as it would for any other already-indexed system.

--refresh: like --all, but restricted to systems that are ALREADY indexed
(pages_indexed set) — the complement of --new. Exists specifically for
picking up a newly added resources.py/content_signals.py detector without
waiting for the monthly full reindex: content_signals/resources are
recomputed from freshly fetched page text/links on every crawl regardless
of the embed-skip optimization (see crawl()'s unchanged_urls), so a quick
--shallow pass here is enough to backfill a new signal across every
already-indexed system without the cost of a full deep re-embed. See
reindex-signals-refresh.yml, which runs this automatically whenever
resources.py or content_signals.py changes.

--spa: targets systems flagged likely_spa=True (see ingest()'s SPA-shell
detection) — sites where a plain HTTP GET only ever returns an empty
client-side-rendered shell, so the ordinary crawler finds nothing however
many times you retry it. Renders just the start URL through crawl4ai
(Playwright under the hood) instead of requests+BeautifulSoup — see
ingest_spa(). Requires `playwright install --with-deps chromium` to have
been run once in the environment (see reindex-spa.yml).

Example:
    python ingest.py "Atlassian Design System" https://atlassian.design/components

Re-running for a design_system_name that's already indexed replaces its old chunks
(deletes by that payload filter first), so scheduled re-ingestion doesn't accumulate
stale duplicates as source docs change.

Change detection (--system only — not --all, not --new): before crawling, does a
cheap HEAD request on the first start_url and compares its ETag/Last-Modified
header against what was stored last run; if unchanged, the whole crawl+embed is
skipped (pass --force to bypass and always re-crawl one system). This only checks
the start_url, not every page, so it's a coarse "did the site's front door
change" heuristic, not a guarantee nothing changed deeper in the site — which is
exactly why --all (the full reindex) no longer uses it at all: skipping an
entire system because its homepage header looked unchanged risked missing a
changed sub-page, or never catching up on pages an earlier max_pages-capped
crawl left un-indexed. --all always fully re-crawls every entry now.
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
    MatchAny,
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
from content_signals import detect_content_signals
from resources import (
    classify_links,
    enrich_resources,
    flag_unverified_resources,
    merge_resources,
    probe_sitemap,
    probe_well_known,
)
from text_utils import full_name

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

# Per-system crawl ceiling when a systems.yaml entry doesn't set its own
# max_pages. 246 of 247 registered systems currently rely on this default, so
# it was quietly capping nearly the entire site's coverage at 30 pages
# regardless of how big the real docs site is — bumped to give real multi-page
# doc sites a realistic shot at full coverage. Still overridable per-entry in
# systems.yaml for anything unusually large or unusually small. When a crawl
# actually hits this ceiling (see crawl()'s hit_max_pages return value), that's
# recorded on the entry and reported by check_crawl_health.py, since it means
# there's likely more real content on the site than got indexed.
DEFAULT_MAX_PAGES = 300

# Used by --shallow: a deliberately small crawl ceiling so a "get breadth"
# pass can touch many systems quickly instead of going deep on a few. Enough
# pages for real (non-boilerplate) content to show up and for the directory
# page to stop showing a system as unindexed, without spending anywhere near
# the time/embedding cost of a full DEFAULT_MAX_PAGES crawl.
SHALLOW_MAX_PAGES = 8


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
    # Needed by delete_urls()'s per-page delete filter (design_system_name +
    # url together) — Qdrant refuses to filter on a field with no payload
    # index at all ("Index required but not found for 'url'"), which this
    # collection never had until the per-page incremental-reindex feature
    # started filtering on it. create_payload_index is a no-op if the index
    # already exists, so this is safe to call on every run regardless.
    client.create_payload_index(
        collection_name=QDRANT_COLLECTION,
        field_name="url",
        field_schema=PayloadSchemaType.KEYWORD,
    )


def page_freshness_from_headers(headers) -> dict:
    """Same shape as fetch_change_signal()'s result, but from headers already
    in hand from a page fetched during a crawl — no extra request needed."""
    signal = {}
    if headers.get("ETag"):
        signal["etag"] = headers["ETag"]
    if headers.get("Last-Modified"):
        signal["last_modified"] = headers["Last-Modified"]
    return signal


def page_unchanged(previous: dict | None, current: dict) -> bool:
    """Per-page version of content_unchanged() (no sitemap signal at this
    granularity — that's a site-wide concept). True only when both sides
    actually have a signal and it matches; a page whose server sends neither
    header can't be judged unchanged, so it's always re-embedded."""
    if not previous or not current:
        return False
    if previous.get("etag") and previous["etag"] == current.get("etag"):
        return True
    if previous.get("last_modified") and previous["last_modified"] == current.get("last_modified"):
        return True
    return False


def fetch_page(url: str) -> tuple[BeautifulSoup | None, str | None, dict]:
    """Returns (soup, None, freshness) on success or (None, error_message, {})
    on failure. The error string is only actually used for the start URL (see
    crawl()) — that's the one fetch failure worth recording on the registry
    entry, since a DNS/connection failure there usually means the whole site
    is unreachable (moved, renamed, taken down), not just one bad link deeper
    in the crawl. freshness is this page's own ETag/Last-Modified (if the
    server sent either), used by crawl() to skip re-embedding a page whose
    content hasn't changed since it was last indexed."""
    try:
        response = requests.get(url, timeout=15, headers={"User-Agent": "ds-directory-mcp/1.0"})
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"  skip {url}: {exc}")
        return None, str(exc), {}
    # requests falls back to Latin-1 (per the old HTTP spec default) whenever a
    # server's Content-Type header omits a charset — even though virtually
    # every real docs site actually serves UTF-8. That mismatch is what turns
    # an em dash into "â€"" once BeautifulSoup decodes .text with the wrong
    # encoding, so force UTF-8 unless the server was explicit about something else.
    if "charset" not in response.headers.get("content-type", "").lower():
        response.encoding = "utf-8"
    return BeautifulSoup(response.text, "html.parser"), None, page_freshness_from_headers(response.headers)


def fetch_text_resource(url: str) -> str | None:
    """Plain-text fetch (no HTML parsing) for llms.txt/llms-full.txt-style
    resources — used as a fallback when a site's crawl comes back empty (see
    crawl()'s SPA fallback below). These are meant to be read directly by
    agents in the first place, so they're often the single best source of
    real content for a client-side-rendered site this crawler otherwise can't
    see through at all."""
    try:
        response = requests.get(url, timeout=15, headers={"User-Agent": "ds-directory-mcp/1.0"})
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"  skip {url}: {exc}")
        return None
    if "charset" not in response.headers.get("content-type", "").lower():
        response.encoding = "utf-8"
    return response.text


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
    max_pages: int = DEFAULT_MAX_PAGES,
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
    previous_pages: dict[str, dict] | None = None,
) -> tuple[dict[str, str], set[str], dict[str, str], bool, str | None, set[str], dict[str, dict]]:
    """Breadth-first crawl restricted to the start URLs' domain(s).

    Returns ({url: text}, all_links_seen, {url: page_title}, hit_max_pages,
    start_url_error, unchanged_urls, page_freshness) — all_links_seen includes
    off-domain links (GitHub, Storybook, Figma, etc.) for resource discovery,
    even though only same-domain pages are actually crawled and embedded.
    page_title lets a browsable "jump straight to this page" index show
    something more useful than a bare URL. hit_max_pages is True when the
    crawl stopped because it hit max_pages while pages were still queued to
    visit — i.e. there's real, undiscovered content still out there, as
    opposed to the crawl just running out of links on its own. start_url_error
    is the fetch error for the FIRST start URL specifically (None if it
    fetched fine) — a DNS/connection failure there usually means the whole
    site moved or is down, not just one bad link deeper in the crawl, so it's
    worth surfacing distinctly from an ordinary low-page-count crawl (see
    find_broken() in generate_directory.py / check_crawl_health.py).

    previous_pages ({url: {"etag":, "last_modified":}}, from pages_index.json)
    lets a re-crawl of an already-indexed system tell which pages haven't
    actually changed since last time — every page still gets fetched (its
    HTML is needed to discover outgoing links regardless), but a page whose
    fetch response matches its previous freshness signal is reported back in
    unchanged_urls so the caller (ingest()) can skip re-embedding/re-upserting
    it, instead of unconditionally re-processing every page on every crawl.
    page_freshness is every crawled page's current signal, to persist back
    into pages_index.json for next time. Both are empty when previous_pages
    is empty (e.g. a system's very first crawl) — behavior is then identical
    to before this existed.

    include_patterns: if given, only crawl URLs containing one of these substrings
    (start_urls themselves are always crawled regardless).
    exclude_patterns: additional regexes (on top of DEFAULT_EXCLUDE_PATTERNS) to skip.
    """
    include_patterns = include_patterns or []
    all_exclude_patterns = DEFAULT_EXCLUDE_PATTERNS + (exclude_patterns or [])
    previous_pages = previous_pages or {}

    root_netlocs = {urlparse(u).netloc for u in start_urls}
    to_visit = list(start_urls)
    visited: set[str] = set()
    pages: dict[str, str] = {}
    page_titles: dict[str, str] = {}
    all_links_seen: set[str] = set()
    start_url_error: str | None = None
    unchanged_urls: set[str] = set()
    page_freshness: dict[str, dict] = {}

    while to_visit and len(visited) < max_pages:
        url = to_visit.pop(0)
        if url in visited:
            continue
        visited.add(url)

        print(f"Crawling: {url}")
        soup, error, freshness = fetch_page(url)
        time.sleep(CRAWL_DELAY_SECONDS)
        if soup is None:
            if url == start_urls[0]:
                start_url_error = error
            continue

        text = extract_text(soup)
        if len(text) >= MIN_CONTENT_LENGTH:
            pages[url] = text
            page_titles[url] = extract_title(soup, url)
            if freshness:
                page_freshness[url] = freshness
            if page_unchanged(previous_pages.get(url), freshness):
                unchanged_urls.add(url)
                print(f"  unchanged since last crawl: {url}")
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

    hit_max_pages = len(visited) >= max_pages and bool(to_visit)
    if hit_max_pages:
        print(f"  hit max_pages ({max_pages}) with {len(to_visit)} more page(s) still queued — coverage is likely incomplete")
    return pages, all_links_seen, page_titles, hit_max_pages, start_url_error, unchanged_urls, page_freshness


def load_registry() -> list[dict]:
    with open(SYSTEMS_REGISTRY) as f:
        return yaml.safe_load(f) or []


REGISTRY_HEADER = (
    "# Registry of external design systems to crawl and index.\n"
    "# Add an entry per system: 'organization' (blank if there isn't a distinct one),\n"
    "# 'design_system' name, and one or more start URLs to crawl from.\n"
    "#\n"
    "# 'resources', 'pages_indexed', 'etag', 'last_modified', 'last_checked',\n"
    "# 'consecutive_crawl_failures', and any '*_meta' fields are auto-populated by\n"
    "# ingest.py — don't hand-edit them, your changes will be overwritten on the\n"
    "# next run. A missing 'pages_indexed' means the entry has never been indexed\n"
    "# (picked up by `ingest.py --new`). 'consecutive_crawl_failures' counts how\n"
    "# many checks IN A ROW the start URL couldn't be fetched at all — resets to 0\n"
    "# the moment a check succeeds; check_crawl_health.py uses a run of these to\n"
    "# flag a system as a real archival candidate rather than just currently having\n"
    "# a bad day.\n"
    "#\n"
    "# 'archived: true' is a graveyard marker for a system whose docs site is\n"
    "# confirmed gone with no live replacement — set by hand after investigating,\n"
    "# never by ingest.py. Comes with three companion fields:\n"
    "#   archived_status: one of shut_down / acquired / repo_archived /\n"
    "#     deliberately_removed / unmaintained — the category of \"why it's gone\"\n"
    "#   archived_reason: a short free-text note on what was actually checked\n"
    "#   archived_date: the date (YYYY-MM-DD) that check was done, since a site\n"
    "#     confirmed dead once can always come back — this is an \"as of\", not a\n"
    "#     permanent verdict\n"
    "# ingest.py skips these entirely (--all/--new/--spa), and they never appear\n"
    "# on the public site (generate_directory.py's indexed_only() excludes\n"
    "# anything without pages_indexed regardless). Kept in the registry rather\n"
    "# than deleted so a future contributor re-submitting the same dead system\n"
    "# gets caught by name instead of silently re-added.\n"
)


def save_registry(entries: list[dict]) -> None:
    with open(SYSTEMS_REGISTRY, "w") as f:
        f.write(REGISTRY_HEADER + "\n")
        yaml.dump(entries, f, sort_keys=False, allow_unicode=True, default_flow_style=False)


def update_pages_index(design_system_name: str, page_entries: list[dict]) -> None:
    """Stores {url, title} (plus etag/last_modified when the server sent
    either — see crawl()'s page_freshness) for every page actually indexed,
    so the directory page can offer a browsable list per system as an
    alternative to search, and so the *next* crawl can tell which pages
    haven't changed since this one (load_previous_pages())."""
    index = {}
    if PAGES_INDEX_FILE.exists():
        index = json.loads(PAGES_INDEX_FILE.read_text())
    index[design_system_name] = sorted(page_entries, key=lambda p: p["title"].lower())
    PAGES_INDEX_FILE.write_text(json.dumps(index, indent=2, ensure_ascii=False))


def update_registry_fields(design_system_name: str, fields: dict) -> None:
    entries = load_registry()
    for entry in entries:
        if full_name(entry) == design_system_name:
            entry.update({k: v for k, v in fields.items() if v is not None})
            break
    save_registry(entries)


def get_registry_entry(design_system_name: str) -> dict | None:
    for entry in load_registry():
        if full_name(entry) == design_system_name:
            return entry
    return None


def update_registry_stats(
    design_system_name: str,
    pages_indexed: int,
    resources: dict[str, list[str]],
    enrichment: dict,
    hit_max_pages: bool = False,
    crawl_error: str | None = None,
    likely_spa: bool = False,
    unverified_resources: dict[str, list[str]] | None = None,
    content_signals: dict | None = None,
    freshness_signal: dict | None = None,
) -> None:
    # hit_max_pages/crawl_error/likely_spa/unverified_resources written every
    # run (not just when truthy) so a system that used to hit the cap, fail
    # to fetch its start URL, look like an unrenderable SPA, or have a
    # dubious-looking resource link, and no longer does, gets that cleared
    # instead of staying stuck reporting a stale problem that's since been
    # fixed. "" rather than None for the no-error case specifically —
    # update_registry_fields drops None values so it could never clear a
    # previously-set crawl_error otherwise.
    #
    # last_checked/freshness_signal are written HERE, in the same call as
    # pages_indexed, rather than in a separate trailing call after ingest()
    # returns — a system was found with real pages_indexed/resources but
    # last_checked stuck at "never" (see generate_systems.py's "Last checked
    # here"), because the old code fetched the freshness signal and stamped
    # last_checked in a second call made only after ingest() had already
    # returned. Anything that interrupts the process in that gap (a runner
    # timeout/cancellation) leaves the crawl data written but last_checked
    # never set. One atomic call closes that window.
    #
    # consecutive_crawl_failures counts how many checks IN A ROW the start
    # URL itself couldn't be fetched at all (crawl_error set) — a single
    # failure is often a transient blip (a timeout, a momentary outage), but
    # one that keeps failing check after check is a real signal the site has
    # moved or gone away for good. check_crawl_health.py uses this to tell
    # "just had a bad day" apart from "probably dead, worth archiving" in its
    # report, instead of every currently-broken system looking identical
    # regardless of how long it's been that way. Resets to 0 the moment a
    # check succeeds again.
    previous = get_registry_entry(design_system_name) or {}
    consecutive_failures = previous.get("consecutive_crawl_failures", 0) + 1 if crawl_error else 0

    fields = {
        "pages_indexed": pages_indexed,
        "hit_max_pages": hit_max_pages,
        "crawl_error": crawl_error or "",
        "consecutive_crawl_failures": consecutive_failures,
        "likely_spa": likely_spa,
        "unverified_resources": unverified_resources or {},
        "content_signals": content_signals or {},
        "last_checked": now_iso(),
        **(freshness_signal or {}),
    }
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


def fetch_freshness_signal(url: str) -> dict:
    """fetch_change_signal()'s homepage ETag/Last-Modified plus
    probe_sitemap()'s sitemap_last_modified, if a sitemap exists — the
    combined signal stored on the registry entry and shown as "site last
    updated". A sitemap's <lastmod> reflects real sub-page changes a single
    homepage header can miss entirely, so content_unchanged() below prefers
    it over the header when both are present."""
    return {**fetch_change_signal(url), **probe_sitemap(url)}


def content_unchanged(entry: dict, new_signal: dict) -> bool:
    """True only if the server gave us a signal AND it matches what we stored last
    time. No signal at all means we can't tell, so we always re-crawl in that case.

    sitemap_last_modified is checked first when present — it reflects
    whatever page the site itself claims changed, not just the homepage's own
    header, so it's the more trustworthy of the two when both exist."""
    if not new_signal:
        return False
    if "sitemap_last_modified" in new_signal:
        return entry.get("sitemap_last_modified") == new_signal["sitemap_last_modified"]
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


def delete_urls(client: QdrantClient, design_system_name: str, urls: set[str]) -> None:
    """Deletes only the given pages' vectors (by url + design_system_name),
    leaving every other page's existing vectors untouched — used instead of
    delete_existing()'s full wipe when re-ingesting a system so pages that
    haven't changed since last crawl don't get re-embedded/re-upserted for
    nothing. A no-op for an empty set (Qdrant's own delete would otherwise
    match everything with an empty `any` list)."""
    if not urls:
        return
    client.delete(
        collection_name=QDRANT_COLLECTION,
        points_selector=Filter(
            must=[
                FieldCondition(key="design_system_name", match=MatchValue(value=design_system_name)),
                FieldCondition(key="url", match=MatchAny(any=list(urls))),
            ]
        ),
    )


def load_previous_pages(design_system_name: str) -> dict[str, dict]:
    """{url: {"etag":, "last_modified":}} from this system's last recorded
    crawl (pages_index.json), for crawl()'s per-page unchanged detection.
    Empty for a system that's never been indexed — every page is then
    necessarily "new", exactly like before this existed."""
    if not PAGES_INDEX_FILE.exists():
        return {}
    index = json.loads(PAGES_INDEX_FILE.read_text())
    return {
        p["url"]: {"etag": p.get("etag"), "last_modified": p.get("last_modified")}
        for p in index.get(design_system_name, [])
    }


def ingest(
    design_system_name: str,
    start_urls: list[str],
    max_pages: int = DEFAULT_MAX_PAGES,
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
) -> None:
    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
    ensure_collection(client)
    previous_pages = load_previous_pages(design_system_name)

    pages, all_links_seen, page_titles, hit_max_pages, start_url_error, unchanged_urls, page_freshness = crawl(
        start_urls,
        max_pages=max_pages,
        include_patterns=include_patterns,
        exclude_patterns=exclude_patterns,
        previous_pages=previous_pages,
    )
    urls_to_upsert = set(pages) - unchanged_urls
    removed_urls = set(previous_pages) - set(pages)
    if unchanged_urls:
        print(f"\nFetched {len(pages)} pages ({len(unchanged_urls)} unchanged since last crawl, "
              f"{len(urls_to_upsert)} to re-embed). Chunking + embedding...")
    else:
        print(f"\nFetched {len(pages)} pages. Chunking + embedding...")
    # Only touch Qdrant for pages that are new/changed or no longer exist —
    # an unchanged page's existing vectors are left exactly as they were,
    # instead of delete_existing()'s old "wipe the whole system, reinsert
    # everything" on every single ingest.
    delete_urls(client, design_system_name, urls_to_upsert | removed_urls)

    resources = merge_resources(classify_links(all_links_seen), probe_well_known(start_urls[0]))
    enrichment = enrich_resources(resources)
    unverified_resources = flag_unverified_resources(resources, design_system_name)
    if resources:
        print(f"  discovered resources: {resources}")
    if unverified_resources:
        print(f"  unverified (name doesn't obviously match): {unverified_resources}")
    if enrichment:
        print(f"  enrichment: {enrichment}")

    # A start URL that fetched fine (no start_url_error) but yielded zero
    # usable pages is the classic signature of a client-side-rendered SPA —
    # the crawler only ever sees the pre-JS HTML shell, which has nothing in
    # it. We can't execute JS, but many SPA-based doc sites publish an
    # llms.txt/llms-full.txt specifically so agents have something real to
    # read — try that as a fallback source before giving up entirely.
    likely_spa = False
    if not pages and not start_url_error:
        for llms_url in resources.get("agent_instructions", []):
            fallback_text = fetch_text_resource(llms_url)
            if fallback_text and len(fallback_text) >= MIN_CONTENT_LENGTH:
                print(f"  no crawlable pages found (likely a JS-rendered SPA) — falling back to {llms_url} ({len(fallback_text)} chars)")
                pages[llms_url] = fallback_text
                page_titles[llms_url] = f"{design_system_name} — llms.txt"
                urls_to_upsert.add(llms_url)
                break
        else:
            likely_spa = True

    content_signals = detect_content_signals(pages)
    if content_signals:
        print(f"  content signals: {content_signals}")

    update_registry_stats(
        design_system_name,
        pages_indexed=len(pages),
        resources=resources,
        enrichment=enrichment,
        hit_max_pages=hit_max_pages,
        crawl_error=start_url_error,
        likely_spa=likely_spa,
        unverified_resources=unverified_resources,
        content_signals=content_signals,
        freshness_signal=fetch_freshness_signal(start_urls[0]),
    )
    update_pages_index(
        design_system_name,
        [
            {"url": url, "title": page_titles.get(url, url), **page_freshness.get(url, {})}
            for url in pages
        ],
    )

    _chunk_and_upsert(client, design_system_name, {url: pages[url] for url in urls_to_upsert})
    print("\nDone.")


def _chunk_and_upsert(client: QdrantClient, design_system_name: str, pages: dict[str, str]) -> None:
    """Shared tail of ingest()/ingest_spa(): chunk each page's text, embed,
    and upsert into Qdrant."""
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


def fetch_rendered_page(url: str) -> tuple[str | None, set[str]]:
    """Fetches a page through an actual headless browser (crawl4ai, which
    wraps Playwright) instead of plain requests+BeautifulSoup — for systems
    already flagged likely_spa: their real content only exists after
    client-side JS runs, which a plain HTTP GET never sees.

    Returns (rendered_text, links_seen) — links_seen is best-effort (used for
    resource discovery only, not further crawling; see ingest_spa()), empty
    if the page's link data couldn't be read for any reason.
    """
    import asyncio

    from crawl4ai import AsyncWebCrawler

    async def _run():
        async with AsyncWebCrawler() as crawler:
            result = await crawler.arun(url=url)
            # Some crawl4ai versions return a list-like container for a
            # single URL rather than the CrawlResult itself.
            if isinstance(result, list):
                result = result[0] if result else None
            return result

    try:
        result = asyncio.run(_run())
    except Exception as exc:
        print(f"  rendered fetch failed for {url}: {exc}")
        return None, set()

    if result is None or not getattr(result, "success", False):
        print(f"  rendered fetch did not succeed for {url}")
        return None, set()

    # .markdown is a str-subclass in every crawl4ai version that's shipped
    # (kept backward-compatible on purpose per crawl4ai's own models.py) —
    # str() on it always gives the raw markdown text either way.
    text = str(getattr(result, "markdown", "") or "")

    links_seen: set[str] = set()
    links = getattr(result, "links", {}) or {}
    if isinstance(links, dict):
        for group in ("internal", "external"):
            for item in links.get(group, []) or []:
                href = item.get("href") if isinstance(item, dict) else item
                if href:
                    links_seen.add(href)

    return text, links_seen


def ingest_spa(design_system_name: str, start_urls: list[str]) -> None:
    """Single-page rendered ingest for a system already flagged likely_spa —
    renders just the start URL through a real headless browser (see
    fetch_rendered_page()) rather than doing a full BFS crawl. Deliberately
    scoped to one page for now: a multi-page rendered crawl would need its
    own link-following loop (same shape as crawl()'s, but rendering every
    page is far slower/heavier than a plain GET), which is a reasonable next
    step once this simpler version is confirmed working end to end."""
    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
    ensure_collection(client)
    delete_existing(client, design_system_name)

    start_url = start_urls[0]
    text, links_seen = fetch_rendered_page(start_url)

    pages: dict[str, str] = {}
    page_titles: dict[str, str] = {}
    if text and len(text) >= MIN_CONTENT_LENGTH:
        pages[start_url] = text
        page_titles[start_url] = design_system_name
    print(f"\nRendered {len(pages)} page(s). Chunking + embedding...")

    resources = merge_resources(classify_links(links_seen), probe_well_known(start_url))
    enrichment = enrich_resources(resources)
    unverified_resources = flag_unverified_resources(resources, design_system_name)
    if resources:
        print(f"  discovered resources: {resources}")
    if unverified_resources:
        print(f"  unverified (name doesn't obviously match): {unverified_resources}")
    if enrichment:
        print(f"  enrichment: {enrichment}")

    content_signals = detect_content_signals(pages)
    if content_signals:
        print(f"  content signals: {content_signals}")

    update_registry_stats(
        design_system_name,
        pages_indexed=len(pages),
        resources=resources,
        enrichment=enrichment,
        likely_spa=len(pages) == 0,
        unverified_resources=unverified_resources,
        content_signals=content_signals,
        freshness_signal=fetch_freshness_signal(start_url),
    )
    update_pages_index(design_system_name, [{"url": url, "title": page_titles.get(url, url)} for url in pages])

    _chunk_and_upsert(client, design_system_name, pages)
    print("\nDone.")


def ingest_entry(entry: dict, force: bool = False, max_pages_override: int | None = None) -> None:
    name = full_name(entry)
    start_urls = entry["start_urls"]
    never_indexed = "pages_indexed" not in entry

    if not force and not never_indexed:
        new_signal = fetch_freshness_signal(start_urls[0])
        if content_unchanged(entry, new_signal):
            print(f"\n=== {name} === (no changes detected at {start_urls[0]}, skipping)")
            update_registry_fields(name, {**new_signal, "last_checked": now_iso()})
            return

    max_pages = max_pages_override if max_pages_override is not None else entry.get("max_pages", DEFAULT_MAX_PAGES)
    print(f"\n=== {name} ==={' (shallow)' if max_pages_override is not None else ''}")
    # ingest() stamps last_checked/the freshness signal itself now, in the
    # same call that writes pages_indexed — no separate trailing call here
    # (see update_registry_stats()'s docstring for why that used to leave a
    # window where pages_indexed was written but last_checked never was).
    ingest(
        design_system_name=name,
        start_urls=start_urls,
        max_pages=max_pages,
        include_patterns=entry.get("include_patterns"),
        exclude_patterns=entry.get("exclude_patterns"),
    )


def safe_run(name: str, fn, *args, **kwargs) -> None:
    """Runs one entry's ingest and swallows any exception that isn't already
    handled inside crawl()/fetch_page() (a Qdrant/embedding-API error, a bug,
    etc) so it can't silently starve every other system still queued in this
    shard's loop — before this, one such crash meant every entry after it in
    --all/--new/--spa's for-loop never even ran, since nothing caught it."""
    try:
        fn(*args, **kwargs)
    except Exception as exc:
        print(f"  !! {name} failed unexpectedly, skipping: {exc}")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_shard(args: list[str]) -> tuple[int, int] | None:
    """--shard 1/4 means: process only entries at index i where i % 4 == 1.
    Splitting a long --all run across parallel CI jobs so one runner getting
    killed only loses its slice, not the whole 247-system run."""
    for i, arg in enumerate(args):
        if arg == "--shard" and i + 1 < len(args):
            index_str, _, total_str = args[i + 1].partition("/")
            return int(index_str), int(total_str)
    return None


if __name__ == "__main__":
    args = sys.argv[1:]
    force = "--force" in args
    args = [a for a in args if a != "--force"]

    shallow = "--shallow" in args
    args = [a for a in args if a != "--shallow"]
    shallow_max_pages = SHALLOW_MAX_PAGES if shallow else None

    shard = parse_shard(args)
    if shard:
        shard_index, shard_total = shard
        shard_flag_pos = args.index("--shard")
        args = args[:shard_flag_pos] + args[shard_flag_pos + 2:]

    if not args:
        print(__doc__)
        sys.exit(1)

    if args[0] == "--all":
        # likely_spa systems are deliberately excluded: a plain crawl already
        # found nothing real there once (that's what set the flag), and
        # would just fail identically again — reindex-spa.yml's headless
        # render is the only thing that can actually make progress on them,
        # so re-attempting the plain crawl here every month is pure wasted
        # requests. If a site stops being an SPA, likely_spa gets cleared by
        # a successful --spa run, not by --all guessing it's worth retrying.
        entries = [e for e in load_registry() if not e.get("archived") and not e.get("likely_spa")]
        if shard:
            entries = [e for i, e in enumerate(entries) if i % shard_total == shard_index]
            print(f"Shard {shard_index}/{shard_total}: {len(entries)} of {len([e for e in load_registry() if not e.get('archived') and not e.get('likely_spa')])} systems")
        # Always force=True here now, regardless of the --force flag: the
        # change-detection skip (fetch_change_signal/content_unchanged) only
        # ever checks the start URL's ETag/Last-Modified — a coarse "did the
        # front door change" signal that says nothing about whether a
        # sub-page changed, or whether an earlier max_pages-capped crawl left
        # real pages un-indexed. A full scan's whole point is to catch up on
        # exactly that, so it must never skip a system just because its
        # homepage header looks unchanged.
        for entry in entries:
            safe_run(full_name(entry), ingest_entry, entry, force=True, max_pages_override=shallow_max_pages)

    elif args[0] == "--new":
        new_entries = [e for e in load_registry() if "pages_indexed" not in e and not e.get("archived")]
        if shard:
            new_entries = [e for i, e in enumerate(new_entries) if i % shard_total == shard_index]
            print(f"Shard {shard_index}/{shard_total}: {len(new_entries)} unindexed systems in this shard")
        if not new_entries:
            print("No unindexed systems found in this shard — everything in systems.yaml has been indexed at least once.")
        for entry in new_entries:
            safe_run(full_name(entry), ingest_entry, entry, force=True, max_pages_override=shallow_max_pages)

    elif args[0] == "--refresh":
        refresh_entries = [e for e in load_registry() if e.get("pages_indexed") and not e.get("archived")]
        if shard:
            refresh_entries = [e for i, e in enumerate(refresh_entries) if i % shard_total == shard_index]
            print(f"Shard {shard_index}/{shard_total}: {len(refresh_entries)} already-indexed systems in this shard")
        if not refresh_entries:
            print("No already-indexed systems found in this shard.")
        for entry in refresh_entries:
            safe_run(full_name(entry), ingest_entry, entry, force=True, max_pages_override=shallow_max_pages)

    elif args[0] == "--spa":
        spa_entries = [e for e in load_registry() if e.get("likely_spa") and not e.get("archived")]
        if shard:
            spa_entries = [e for i, e in enumerate(spa_entries) if i % shard_total == shard_index]
            print(f"Shard {shard_index}/{shard_total}: {len(spa_entries)} likely_spa systems in this shard")
        if not spa_entries:
            print("No likely_spa systems found in this shard.")
        for entry in spa_entries:
            name = full_name(entry)
            print(f"\n=== {name} === (rendered)")
            safe_run(name, ingest_spa, name, entry["start_urls"])

    elif args[0] == "--system":
        if len(args) < 2:
            print("Usage: python ingest.py --system \"<name from systems.yaml>\" [--force]")
            sys.exit(1)
        target_name = args[1]
        matches = [e for e in load_registry() if full_name(e) == target_name]
        if not matches:
            print(f"No entry named {target_name!r} in systems.yaml")
            sys.exit(1)
        ingest_entry(matches[0], force=force)

    elif len(args) >= 2:
        ingest(design_system_name=args[0], start_urls=args[1:])

    else:
        print(__doc__)
        sys.exit(1)
