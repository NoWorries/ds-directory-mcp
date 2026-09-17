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
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

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
    Range,
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
from embeddings import EmbeddingAuthError, embed_texts
from content_signals import detect_content_signals
from resources import (
    classify_links,
    enrich_resources,
    fetch_sitemap_urls,
    filter_unverified_resources,
    flag_unverified_resources,
    merge_resources,
    probe_sitemap,
    probe_well_known,
)
from text_utils import (
    full_name,
    humanize_url_path,
    in_any_scope,
    is_auth_or_error_title,
    path_scope,
)

SYSTEMS_REGISTRY = Path(__file__).parent / "systems.yaml"
PAGES_INDEX_FILE = Path(__file__).parent / "pages_index.json"
# Written by every shard of a sharded run (--all/--new/--refresh/--spa) —
# the list of design_system_names this shard actually touched (successfully
# or not). merge_shards.py reads this per shard instead of diffing each
# shard's full systems.yaml/pages_index.json copy against a freshly-checked-
# out base: that diff was unreliable (a run logged "689 entries updated"
# from 182 shards on a 241-entry registry, when at most ~60 genuine updates
# were possible) and, for pages_index.json specifically, was flat-out wrong
# — every shard's file is a full copy of the WHOLE index, so merging by
# `dict.update` let the last shard's stale copy of an untouched system
# silently overwrite another shard's fresh crawl of it.
SHARD_TOUCHED_FILE = Path(__file__).parent / "shard_touched.json"


def mark_shard_touched(design_system_name: str) -> None:
    touched = []
    if SHARD_TOUCHED_FILE.exists():
        touched = json.loads(SHARD_TOUCHED_FILE.read_text())
    if design_system_name not in touched:
        touched.append(design_system_name)
        SHARD_TOUCHED_FILE.write_text(json.dumps(touched, indent=2, ensure_ascii=False))

STRIP_TAGS = ["script", "style", "nav", "footer", "noscript", "svg", "aside", "button", "form", "iframe"]

# Inline elements whose own tag boundary shouldn't become a text boundary —
# get_text(separator="\n") inserts that separator between every tag's text,
# inline or not, so "Click <code>save</code> to continue" was coming out as
# three separate lines ("Click", "save", "to continue") instead of one
# sentence. Unwrapping these first (removing the tag but keeping its text
# in the surrounding flow) means only genuine block-level tag boundaries
# turn into newlines.
INLINE_TAGS = [
    "a", "code", "span", "strong", "em", "b", "i", "small", "sub", "sup",
    "mark", "abbr", "cite", "kbd", "var", "time", "u", "s", "wbr", "tt",
]

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
    r"/blog/", r"/posts/", r"/news/", r"/articles/", r"/insights/",
    r"/careers/", r"/jobs/", r"/legal/", r"/privacy",
    r"/terms", r"/pricing", r"/changelog", r"/login", r"/signin",
    r"/sign-in", r"/search\?", r"/tag/", r"/tags/", r"/about-us",
    r"/press/", r"/events/", r"/newsletter",
    # Component-preview/sandbox routes some design-system sites generate one
    # of per component per viewport (Storybook, Playroom, an iframe sandbox)
    # — real content, but as a RESOURCE link (see resources.py), not a page
    # worth crawling/embedding: confirmed live on AgDS, ~160 of a 300-page
    # crawl budget were burned on these before the real docs were reached.
    r"/responsive-preview", r"/playroom", r"/storybook/", r"/iframe\.html",
    r"/sandbox",
]

# A design system's own release-notes/updates page is occasionally under one
# of the paths above (rare) — that tradeoff is accepted; the much more common
# case is a marketing blog that isn't design-system documentation at all.

MAX_PATH_SEGMENTS = 8

def is_recursive_trap(path: str) -> bool:
    """Guards against a specific real, confirmed crawl bug: a page under
    e.g. /posts/some-post/ that links back to itself with a RELATIVE href
    ("posts/", or the post's own slug) resolves to a deeper copy of the same
    path each time (/posts/x/posts/x/posts/x/...) — same domain, same
    (growing) path prefix, so plain path-scoping doesn't catch it. Confirmed
    live on gold.designsystemau.org: two blog posts' relative links to
    "posts/" and to each other compounded into thousands of unique,
    ever-deeper URLs, burned the entire max_pages budget on zero real content,
    and repeated identically on every re-run since none of it was excluded.
    Reject a link once any single path segment appears three or more times,
    or once the path is simply unreasonably deep for real documentation. A
    single repeat is deliberately allowed — /components/buttons/buttons/ and
    /principles/accessibility/accessibility/ are real, indexed pages on
    Skyline and Kontur — so the first couple of levels of a trap still get
    fetched, but it can no longer grow without bound."""
    segments = [s for s in path.split("/") if s]
    if len(segments) > MAX_PATH_SEGMENTS:
        return True
    counts: dict[str, int] = {}
    for seg in segments:
        counts[seg] = counts.get(seg, 0) + 1
        if counts[seg] >= 3:
            return True
    return False

# Pages with less than this much text (after boilerplate stripping) are almost
# always nav-only, redirect, or placeholder pages — not worth embedding.
MIN_CONTENT_LENGTH = 200

# File extensions that are never real documentation pages — skipped before
# they ever enter the crawl queue (still recorded in all_links_seen for
# resource discovery, e.g. a linked PDF spec or icon set).
NON_HTML_EXTENSIONS = (
    ".pdf", ".zip", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
    ".mp4", ".webm", ".woff", ".woff2", ".ttf", ".css", ".js", ".json",
    ".xml", ".txt", ".ico",
)

# fetch_page() hard caps — a real confirmed incident: the crawler followed a
# link to a JS bundle / PDF / video and read the ENTIRE body into memory
# before parsing it as HTML, which starved a GitHub Actions runner enough
# that GitHub itself killed it ("lost communication with the server") —
# losing that whole shard's work, including everything it had already
# crawled and embedded before the runner died (the artifact upload can't run
# once the VM is torn down). A Content-Type check alone isn't enough — some
# servers mislabel a huge asset as text/html — so the byte cap applies
# regardless of what the server claims to be sending.
MAX_PAGE_BYTES = 3_000_000
PAGE_READ_TIMEOUT_SECONDS = 30

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
    if client.collection_exists(QDRANT_COLLECTION):
        # A leftover collection from a previous embedding provider/model with
        # a different native vector size is not a degraded state to work
        # around — confirmed live, switching Jina -> Gemini left the
        # collection at Jina's 768 dims while Gemini's real output is 3072,
        # and Qdrant rejects every single upsert outright ("Wrong input:
        # Vector dimension error") until the collection matches. There's
        # nothing worth preserving in that case either way: the OLD vectors
        # are a different model's semantic space entirely and were already
        # due for a full re-embed regardless of dimension. Recreate rather
        # than fail forever on every embed call.
        existing_size = client.get_collection(QDRANT_COLLECTION).config.params.vectors.size
        if existing_size != EMBEDDING_DIM:
            print(
                f"  !! {QDRANT_COLLECTION}'s existing vector size ({existing_size}) doesn't match "
                f"EMBEDDING_DIM ({EMBEDDING_DIM}) — deleting and recreating it. This is expected right "
                f"after an embedding-provider/model switch; every system will re-embed from scratch."
            )
            client.delete_collection(QDRANT_COLLECTION)

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
    # Lets delete_stale_chunks() below target "any chunk of this page past
    # index N" without touching the chunks a same-run upsert just wrote for
    # indices 0..N-1 — needed because point IDs are now deterministic
    # (hash of url+chunk_index), so re-embedding a page that shrank from 12
    # chunks to 8 overwrites indices 0-7 in place but would otherwise leave
    # the old 8-11 behind forever.
    client.create_payload_index(
        collection_name=QDRANT_COLLECTION,
        field_name="chunk_index",
        field_schema=PayloadSchemaType.INTEGER,
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


# Query parameters worth keeping when normalize_url() otherwise strips every
# query string — empty on purpose: nothing seen in the registry so far
# actually needs one preserved (see normalize_url()'s docstring for the
# Skyline /feedback/?page=N case that motivated dropping everything else).
URL_QUERY_ALLOWLIST: set[str] = set()


def normalize_url(url: str) -> str:
    """Canonical form of a URL so that trivially-different links (trailing
    slash, an explicit index.html, a tracking query string, http vs https,
    www vs non-www, mixed-case host) are recognized as the same page instead
    of being crawled and embedded twice. Confirmed live: 268 near-duplicate
    URL pairs and 189 query-string variants across the registry, the worst a
    single Skyline page (/feedback/) crawled 101 times as
    /feedback/?page=1, ?page=2, ... — each one a full fetch, a full
    embedding call, and a separate "page" in pages_index.json.

    - scheme/host lowercased, http promoted to https, default port stripped
    - fragment dropped (a link consumer never needs it; was already handled
      ad hoc at every call site before this existed)
    - a trailing /index.html or /index.htm collapsed to the directory it's in
    - trailing slash stripped, except the bare domain root
    - every query parameter dropped except URL_QUERY_ALLOWLIST (empty for
      now — add one there, deliberately, if a real system is ever found that
      needs a query param to reach distinct content, rather than keeping
      query strings by default and re-litigating this one page at a time)
    """
    parsed = urlparse(url)
    scheme = "https" if parsed.scheme in ("http", "https") else parsed.scheme
    host = parsed.hostname or ""
    port = parsed.port
    default_port = {"http": 80, "https": 443}.get(parsed.scheme)
    netloc = host.lower() + (f":{port}" if port and port != default_port else "")

    path = parsed.path or "/"
    if path.endswith("/index.html"):
        path = path[: -len("index.html")]
    elif path.endswith("/index.htm"):
        path = path[: -len("index.htm")]
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
        if not path:
            path = "/"

    if parsed.query and URL_QUERY_ALLOWLIST:
        kept = [
            (k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
            if k in URL_QUERY_ALLOWLIST
        ]
        query = urlencode(kept)
    else:
        query = ""

    return urlunparse((scheme, netloc, path, "", query, ""))


def _parse_html(body: str) -> BeautifulSoup:
    """lxml is ~10x faster and far lighter on memory than html.parser for a
    large document, but isn't a hard dependency (fall back if it's missing
    from the environment somehow)."""
    try:
        return BeautifulSoup(body, "lxml")
    except Exception:
        return BeautifulSoup(body, "html.parser")


def fetch_page(url: str) -> tuple[BeautifulSoup | None, str | None, dict, str | None]:
    """Returns (soup, None, freshness, final_url) on success or
    (None, error_message, {}, None) on failure. The error string is only
    actually used for the start URL (see crawl()) — that's the one fetch
    failure worth recording on the registry entry, since a DNS/connection
    failure there usually means the whole site is unreachable (moved,
    renamed, taken down), not just one bad link deeper in the crawl.
    freshness is this page's own ETag/Last-Modified (if the server sent
    either), used by crawl() to skip re-embedding a page whose content
    hasn't changed since it was last indexed. final_url is response.url,
    normalized — requests follows redirects itself, so a link crawled at
    one URL can come back having landed somewhere else entirely (crawl()
    checks this is still in scope before trusting the page).

    Streams the response and enforces MAX_PAGE_BYTES/PAGE_READ_TIMEOUT_SECONDS
    regardless of what Content-Type the server claims — see MAX_PAGE_BYTES's
    docstring for why this exists at all: a mislabeled or simply huge body
    (a video, a JS bundle, a PDF) read fully into memory and then handed to
    BeautifulSoup is what took down a GitHub Actions runner in production."""
    try:
        response = requests.get(
            url, timeout=(10, PAGE_READ_TIMEOUT_SECONDS), stream=True,
            headers={"User-Agent": "ds-directory-mcp/1.0"},
        )
        response.raise_for_status()

        content_type = response.headers.get("content-type", "").lower()
        if content_type and "html" not in content_type and "xhtml" not in content_type:
            response.close()
            return None, f"non-html content-type: {content_type.split(';')[0]}", {}, None

        started = time.monotonic()
        chunks = []
        total = 0
        for chunk in response.iter_content(chunk_size=65536):
            total += len(chunk)
            if total > MAX_PAGE_BYTES:
                response.close()
                return None, f"page exceeded {MAX_PAGE_BYTES} bytes, skipped", {}, None
            if time.monotonic() - started > PAGE_READ_TIMEOUT_SECONDS:
                response.close()
                return None, "page read exceeded time budget, skipped", {}, None
            chunks.append(chunk)
        raw = b"".join(chunks)
    except requests.RequestException as exc:
        print(f"  skip {url}: {exc}")
        return None, str(exc), {}, None

    # requests falls back to Latin-1 (per the old HTTP spec default) whenever a
    # server's Content-Type header omits a charset — even though virtually
    # every real docs site actually serves UTF-8. That mismatch is what turns
    # an em dash into "â€"" once BeautifulSoup decodes .text with the wrong
    # encoding, so force UTF-8 unless the server was explicit about something else.
    encoding = response.encoding if "charset" in content_type else "utf-8"
    try:
        body = raw.decode(encoding or "utf-8", errors="replace")
    except LookupError:
        body = raw.decode("utf-8", errors="replace")
    return _parse_html(body), None, page_freshness_from_headers(response.headers), normalize_url(response.url)


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
    # A framework that renders its page <h1>/intro INSIDE <article> or
    # <main> (common with Docusaurus/MDX-based doc sites) would otherwise
    # lose that content to an unconditional "strip every <header>" rule,
    # since <header><h1>...</h1></header> is a normal pattern for a page's
    # OWN title block, not just a site-wide nav banner. Only strip <header>
    # when it's a direct child of <body> — the site-chrome case — leaving a
    # page's own header alone wherever it's nested inside real content.
    for header in soup.find_all("header"):
        if header.parent and header.parent.name == "body":
            header.decompose()

    for tag_name in STRIP_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    for attr, value in (("aria-hidden", "true"), ("role", "navigation")):
        for tag in soup.find_all(attrs={attr: value}):
            tag.decompose()

    # Prefer the page's actual content container when the page provides
    # one — whatever sidebar/breadcrumb/related-links chrome surrounds
    # <main>/<article>/[role=main] is far more likely to be boilerplate than
    # signal, and this also means STRIP_TAGS/the attribute strips above
    # only need to catch chrome that ISN'T already excluded by scoping here.
    content = soup.find("main") or soup.find("article") or soup.find(attrs={"role": "main"}) or soup

    for tag_name in INLINE_TAGS:
        for tag in content.find_all(tag_name):
            tag.unwrap()
    # unwrap() alone leaves each former tag's text as its own separate
    # NavigableString sibling — get_text(separator=...) joins EVERY string
    # node with that separator regardless of whether a tag boundary used to
    # sit between them, so without this, "Click <code>save</code> to
    # <a>continue</a>." still comes out as four separate lines. smooth()
    # merges adjacent text nodes back into one run per line of real prose.
    content.smooth()

    text = content.get_text(separator="\n", strip=True)

    lines = [line for line in text.split("\n") if line.strip() not in BOILERPLATE_LINES]
    return "\n".join(lines)


def strip_cross_page_boilerplate(pages: dict[str, str]) -> dict[str, str]:
    """Drops any line appearing in more than half of a system's pages —
    the sidebar nav, footer, and cookie-banner text BOILERPLATE_LINES'
    fixed exact-match list barely scratches, since it's different text on
    every site (a nav item list, a company's own footer copy). Applied
    across the whole crawl at once so it needs no fixed list at all: real
    per-page content essentially never repeats verbatim on more than half a
    site's OTHER pages, while a page's own site-wide chrome always does.
    Only applied when there's enough of a sample to be confident about
    (fewer than 5 pages: too easy for a real repeated heading like
    "Overview" or "Examples" to trip a false positive)."""
    if len(pages) < 5:
        return pages
    line_counts: dict[str, int] = {}
    for text in pages.values():
        for line in set(text.split("\n")):
            stripped = line.strip()
            if stripped:
                line_counts[stripped] = line_counts.get(stripped, 0) + 1
    threshold = len(pages) / 2
    boilerplate = {line for line, count in line_counts.items() if count > threshold}
    if not boilerplate:
        return pages
    return {
        url: "\n".join(line for line in text.split("\n") if line.strip() not in boilerplate)
        for url, text in pages.items()
    }


def _looks_like_non_html(path: str) -> bool:
    return path.lower().endswith(NON_HTML_EXTENSIONS)


def extract_links(soup: BeautifulSoup, base_url: str, scope: tuple[str, str]) -> set[str]:
    netloc, prefix = scope
    links = set()
    for a in soup.find_all("a", href=True):
        absolute = normalize_url(urljoin(base_url, a["href"]))
        parsed = urlparse(absolute)
        path = parsed.path or "/"
        if (
            parsed.netloc == netloc
            and path.startswith(prefix)
            and not is_recursive_trap(path)
            and not _looks_like_non_html(path)
        ):
            links.add(absolute)
    return links


def extract_all_links(soup: BeautifulSoup, base_url: str) -> set[str]:
    """All absolute links regardless of domain — used for resource discovery
    (GitHub, Storybook, Figma, etc. usually live off the docs domain).
    Normalizing here (in particular, dropping query strings) is what
    collapses e.g. 60 Figma links that differ only by ?node-id=... into the
    one file they all actually point at — see resources.classify_links()."""
    return {normalize_url(urljoin(base_url, a["href"])) for a in soup.find_all("a", href=True)}


def is_excluded(url: str, exclude_patterns: list[str]) -> bool:
    return any(re.search(pattern, url, re.IGNORECASE) for pattern in exclude_patterns)


def matches_include(url: str, include_patterns: list[str]) -> bool:
    if not include_patterns:
        return True
    return any(pattern in url for pattern in include_patterns)


def extract_title(soup: BeautifulSoup, url: str) -> str:
    """The document's real title, trying several sources in order:
    <head><title>, then <meta property="og:title">, then the first <h1>,
    finally a humanized version of the URL's own last path segment — never
    the raw URL itself.

    <head><title> is deliberately scoped to soup.head, NOT soup.title (which
    matches the FIRST <title> tag anywhere in the whole document, by tag
    name alone). An inlined SVG icon's own accessibility <title> ("Open
    menu", "info icon") counts just as much as the real document title to
    that lookup — confirmed live on carbondesignsystem.com, where roughly
    half of 268 crawled pages ended up titled "Open menu" or "info icon"
    this way. Scoping to soup.head rules out every SVG title, wherever in
    <body> it sits.

    Uses get_text() rather than .string (which returns None, not the text,
    whenever the tag has more than one child node — e.g. a title containing
    an HTML comment, confirmed live as a Next.js-generated `<!-- -->` inside
    <title> leaving titles like "skeleton<!" once naively truncated)."""
    if soup.head:
        title_tag = soup.head.find("title")
        if title_tag:
            text = re.sub(r"\s+", " ", title_tag.get_text()).strip()
            text = re.sub(r"<!.*$", "", text).strip()
            if text:
                return text
        og_title = soup.head.find("meta", attrs={"property": "og:title"})
        if og_title and og_title.get("content"):
            text = re.sub(r"\s+", " ", og_title["content"]).strip()
            if text:
                return text
    h1 = soup.find("h1")
    if h1:
        text = re.sub(r"\s+", " ", h1.get_text()).strip()
        if text:
            return text
    return humanize_url_path(url)


def extract_canonical(soup: BeautifulSoup, base_url: str) -> str | None:
    """<link rel="canonical"> if present, absolute + normalized — the page
    author's own word for which URL this content should be attributed to
    (e.g. a paginated or tracked-parameter URL canonicalizing back to the
    plain page). Only worth trusting if it's still in the crawl's own scope;
    the caller checks that."""
    if not soup.head:
        return None
    link = soup.head.find("link", rel=lambda v: v and "canonical" in v.lower() if isinstance(v, str) else v and "canonical" in " ".join(v).lower())
    if link and link.get("href"):
        return normalize_url(urljoin(base_url, link["href"]))
    return None


def has_noindex(soup: BeautifulSoup) -> bool:
    """<meta name="robots" content="...noindex..."> — the page's own author
    saying this shouldn't be indexed. A crawler that fetches it anyway (it
    may still hold useful outgoing links) but embeds it regardless is
    ignoring an explicit, deliberate signal."""
    if not soup.head:
        return False
    for meta in soup.head.find_all("meta", attrs={"name": lambda v: v and v.lower() in ("robots", "googlebot")}):
        if "noindex" in (meta.get("content") or "").lower():
            return True
    return False


def crawl(
    start_urls: list[str],
    max_pages: int = DEFAULT_MAX_PAGES,
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
    previous_pages: dict[str, dict] | None = None,
    scope_paths: list[str] | None = None,
) -> tuple[dict[str, str], set[str], dict[str, str], bool, str | None, set[str], dict[str, dict], int]:
    """Breadth-first crawl restricted to the start URLs' domain(s).

    Returns ({url: text}, all_links_seen, {url: page_title}, hit_max_pages,
    start_url_error, unchanged_urls, page_freshness, fetched_count) —
    all_links_seen includes off-domain links (GitHub, Storybook, Figma, etc.)
    for resource discovery, even though only same-domain pages are actually
    crawled and embedded. page_title lets a browsable "jump straight to this
    page" index show something more useful than a bare URL. hit_max_pages is
    True when the crawl stopped because it hit max_pages while pages were
    still queued to visit — i.e. there's real, undiscovered content still out
    there, as opposed to the crawl just running out of links on its own.
    start_url_error is the fetch error for the FIRST start URL specifically
    (None if it fetched fine) — a DNS/connection failure there usually means
    the whole site moved or is down, not just one bad link deeper in the
    crawl, so it's worth surfacing distinctly from an ordinary low-page-count
    crawl (see find_broken() in generate_directory.py / check_crawl_health.py).
    fetched_count is every URL actually fetched (len(visited)), regardless of
    whether it turned into a usable page — the difference between this and
    len(pages) is how thin a site's real content is per page fetched (see
    ingest()'s likely_spa/thin_page_ratio: a low ratio despite a real fetch
    count is the signature of a mostly-JS-rendered site, not just a small one).

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
    scope_paths: explicit path prefixes (on the start URLs' domains) a link must
    fall under to be followed — overrides the path_scope() heuristic for the
    rare start URL it gets wrong. Links outside every scope are still recorded
    in all_links_seen for resource discovery; they just aren't crawled.
    """
    include_patterns = include_patterns or []
    all_exclude_patterns = DEFAULT_EXCLUDE_PATTERNS + (exclude_patterns or [])
    previous_pages = previous_pages or {}

    # Normalized up front so root_scopes' netloc (lowercased by normalize_url)
    # actually matches what extract_links() compares against (every link it
    # returns is normalize_url()'d too) — a start_url with a mixed-case host
    # would otherwise never match its own scope.
    start_urls = [normalize_url(u) for u in start_urls]

    if scope_paths:
        root_scopes = {(urlparse(u).netloc, p) for u in start_urls for p in scope_paths}
    else:
        root_scopes = {path_scope(u) for u in start_urls}

    # A capped crawl (hit_max_pages) always started BFS from the same URL in
    # the same nav-link order, so the pages just past max_pages never got a
    # turn on a later run even as already-indexed ones kept getting
    # re-visited — the site's own sitemap (when it has one) is a much better
    # source of "everything that exists" than nav-link discovery order.
    sitemap_seeds = [
        u for u in (normalize_url(loc) for loc in fetch_sitemap_urls(start_urls[0]))
        if u not in start_urls and in_any_scope(u, root_scopes)
    ]
    if sitemap_seeds:
        print(f"  seeded {len(sitemap_seeds)} URL(s) from the sitemap")

    # start_urls always go first, unsorted — crawl() checks fetch failures
    # against start_urls[0] specifically (see start_url_error below), which
    # needs it attempted early regardless of freshness. Sitemap seeds after
    # that are stable-sorted so previously-unseen URLs are visited before
    # ones already indexed last time: within "new" or within "seen before"
    # the crawl is still sitemap order, just with new content prioritized
    # over re-treading old ground when max_pages can't fit everything.
    to_visit = start_urls + sorted(sitemap_seeds, key=lambda u: u in previous_pages)
    to_visit_set = set(to_visit)  # O(1) "already queued" check below — to_visit itself stays a list to preserve BFS order
    visited: set[str] = set()
    pages: dict[str, str] = {}
    page_titles: dict[str, str] = {}
    all_links_seen: set[str] = set()
    start_url_error: str | None = None
    unchanged_urls: set[str] = set()
    page_freshness: dict[str, dict] = {}

    while to_visit and len(visited) < max_pages:
        url = to_visit.pop(0)
        to_visit_set.discard(url)
        if url in visited:
            continue
        visited.add(url)

        print(f"Crawling: {url}")
        soup, error, freshness, final_url = fetch_page(url)
        time.sleep(CRAWL_DELAY_SECONDS)
        if soup is None:
            if url == start_urls[0]:
                start_url_error = error
            continue

        # requests follows redirects itself — a URL crawled from inside scope
        # can come back having landed somewhere else entirely (a moved page,
        # a login wall, the domain's homepage). If that landing spot fell
        # outside every scope, this response doesn't belong to the system
        # being crawled: still worth mining for resource links (it was
        # fetched anyway), but not worth embedding or following further.
        if final_url and final_url != url and not in_any_scope(final_url, root_scopes):
            print(f"  redirected out of scope ({url} -> {final_url}), skip")
            all_links_seen |= extract_all_links(soup, url)
            continue

        # A page's own <link rel="canonical"> is its author's word for which
        # URL this content should be attributed to — e.g. several tracked-
        # parameter or paginated URLs all canonicalizing to the same plain
        # page. Only trusted when it's still in scope; otherwise fall back
        # to final_url (already normalize_url()'d by fetch_page) as the key.
        canonical = extract_canonical(soup, final_url or url)
        page_key = canonical if canonical and in_any_scope(canonical, root_scopes) else (final_url or url)
        title = extract_title(soup, page_key)

        if has_noindex(soup):
            print(f"  skip (noindex): {page_key}")
        elif is_auth_or_error_title(title):
            # A crawl that landed on a login wall, consent screen, or error
            # page — not this system's content at all. Confirmed live: an
            # OAuth redirect crawled 18 separate times (once per one-time
            # nonce in the URL) as if it were 18 real pages.
            print(f"  skip (auth/error page, title={title!r}): {page_key}")
        else:
            text = extract_text(soup)
            if len(text) >= MIN_CONTENT_LENGTH:
                pages[page_key] = text
                page_titles[page_key] = title
                if freshness:
                    page_freshness[page_key] = freshness
                if page_unchanged(previous_pages.get(page_key), freshness):
                    unchanged_urls.add(page_key)
                    print(f"  unchanged since last crawl: {page_key}")
            else:
                print(f"  skip (too little content): {page_key}")

        all_links_seen |= extract_all_links(soup, url)

        for scope in root_scopes:
            for link in extract_links(soup, url, scope):
                if link in visited or link in to_visit_set:
                    continue
                if is_excluded(link, all_exclude_patterns):
                    continue
                if not matches_include(link, include_patterns):
                    continue
                to_visit.append(link)
                to_visit_set.add(link)
                to_visit_set.add(link)

    hit_max_pages = len(visited) >= max_pages and bool(to_visit)
    if hit_max_pages:
        print(f"  hit max_pages ({max_pages}) with {len(to_visit)} more page(s) still queued — coverage is likely incomplete")
    return pages, all_links_seen, page_titles, hit_max_pages, start_url_error, unchanged_urls, page_freshness, len(visited)


def load_registry() -> list[dict]:
    with open(SYSTEMS_REGISTRY) as f:
        return yaml.safe_load(f) or []


REGISTRY_HEADER = (
    "# Registry of external design systems to crawl and index.\n"
    "# Add an entry per system: 'organization' (blank if there isn't a distinct one),\n"
    "# 'design_system' name, and one or more start URLs to crawl from. A crawl\n"
    "# only follows links under the start URL's own path (its \"children\"); set\n"
    "# 'scope_paths' (a list of path prefixes) to widen or pin that when the\n"
    "# start URL is a landing page deeper than the system's real root.\n"
    "#\n"
    "# 'resources', 'pages_indexed', 'etag', 'last_modified', 'last_checked',\n"
    "# 'consecutive_crawl_failures', 'thin_page_ratio', and any '*_meta' fields\n"
    "# are auto-populated by ingest.py — don't hand-edit them, your changes will\n"
    "# be overwritten on the next run. A missing 'pages_indexed' means the entry\n"
    "# has never been indexed (picked up by `ingest.py --new`).\n"
    "# 'thin_page_ratio' is the fraction of fetched pages that had too little\n"
    "# content to embed (1 - pages_indexed/fetched) — high alongside a real\n"
    "# fetch count usually means a mostly client-side-rendered site, not a\n"
    "# small one; see 'likely_spa'.\n"
    "# 'consecutive_crawl_failures' counts how many checks IN A ROW the start\n"
    "# URL couldn't be fetched at all — resets to 0\n"
    "# the moment a check succeeds; check_crawl_health.py uses a run of these to\n"
    "# flag a system as a real archival candidate rather than just currently having\n"
    "# a bad day.\n"
    "#\n"
    "# 'render_mode: spa' is set the first time `ingest.py --spa` successfully\n"
    "# renders a system's content through a headless browser — it's sticky (unlike\n"
    "# 'likely_spa', which a successful render clears), so `--all`'s plain HTTP\n"
    "# crawl permanently skips it instead of re-attempting a fetch that can't\n"
    "# execute JS, finding nothing, and deleting the vectors the render wrote.\n"
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
    content_signal_sources: dict | None = None,
    freshness_signal: dict | None = None,
    render_mode: str | None = None,
    thin_page_ratio: float | None = None,
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
        "content_signal_sources": content_signal_sources or {},
        "thin_page_ratio": round(thin_page_ratio, 2) if thin_page_ratio is not None else None,
        "last_checked": now_iso(),
        **(freshness_signal or {}),
    }
    if resources:
        fields["resources"] = resources
    if enrichment:
        fields.update(enrichment)
    if render_mode:
        # Sticky: once a system's content only shows up through a rendered
        # (headless-browser) fetch, it stays flagged that way even after a
        # successful render clears likely_spa — see ingest_spa()'s docstring
        # for why: without this, --all's next plain crawl treats it as an
        # ordinary system again, finds nothing (it still can't execute JS),
        # and — before this existed — deleted the vectors the render wrote.
        fields["render_mode"] = render_mode
    update_registry_fields(design_system_name, fields)
    mark_shard_touched(design_system_name)


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


def delete_urls(client: QdrantClient, design_system_name: str, urls: set[str]) -> None:
    """Deletes only the given pages' vectors (by url + design_system_name),
    leaving every other page's existing vectors untouched — so pages that
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


def delete_stale_chunks(client: QdrantClient, design_system_name: str, new_chunk_counts: dict[str, int]) -> None:
    """After _chunk_and_upsert() has (over)written chunks 0..N-1 for each
    re-embedded page (deterministic point IDs mean that upsert already
    replaced anything at those indices), this removes anything left over
    from a PREVIOUS run that had MORE chunks — e.g. a page that shrank from
    12 chunks to 8 would otherwise leave stale chunks 8-11 behind forever,
    since nothing else ever targets them. A no-op if every page's chunk
    count only ever grew or stayed the same (the common case)."""
    if not new_chunk_counts:
        return
    client.delete(
        collection_name=QDRANT_COLLECTION,
        points_selector=Filter(
            must=[FieldCondition(key="design_system_name", match=MatchValue(value=design_system_name))],
            should=[
                Filter(must=[
                    FieldCondition(key="url", match=MatchValue(value=url)),
                    FieldCondition(key="chunk_index", range=Range(gte=count)),
                ])
                for url, count in new_chunk_counts.items()
            ],
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
    scope_paths: list[str] | None = None,
    shallow: bool = False,
) -> None:
    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
    ensure_collection(client)
    previous_pages = load_previous_pages(design_system_name)

    pages, all_links_seen, page_titles, hit_max_pages, start_url_error, unchanged_urls, page_freshness, fetched_count = crawl(
        start_urls,
        max_pages=max_pages,
        include_patterns=include_patterns,
        exclude_patterns=exclude_patterns,
        previous_pages=previous_pages,
        scope_paths=scope_paths,
    )
    # A --shallow run's max_pages (SHALLOW_MAX_PAGES, currently 8) is a
    # deliberately tiny "get breadth fast" cap, not evidence the site has
    # more real content sitting undiscovered — but hit_max_pages doesn't
    # know that, so check_crawl_health.py's "raise max_pages" advice was
    # firing for the ~50 systems that had only ever been shallow-crawled.
    if shallow:
        hit_max_pages = False
    # A crawl that comes back with nothing (or couldn't even fetch its start
    # URL) is far more likely a transient failure — a rate limit, an outage,
    # a momentary SPA-shell response — than proof the system's real content
    # vanished. Treating it as "0 pages now" would otherwise delete every
    # previously-indexed page from Qdrant on a single bad run. Confirmed
    # live: a 2026-09-16 reindex zeroed out pages_indexed on 40 systems this
    # way. Bail out here, preserving the previous pages_indexed/pages_index
    # entry untouched, and let crawl_error/consecutive_crawl_failures record
    # that this run didn't succeed so check_crawl_health.py can flag it.
    if previous_pages and (start_url_error or not pages):
        print(f"\n  crawl came back empty ({start_url_error or 'no pages found'}) for a "
              f"previously-indexed system — leaving its {len(previous_pages)} existing "
              f"page(s) untouched instead of deleting them.")
        previous_entry = get_registry_entry(design_system_name) or {}
        update_registry_stats(
            design_system_name,
            pages_indexed=previous_entry.get("pages_indexed", 0),
            resources={},
            enrichment={},
            hit_max_pages=hit_max_pages,
            crawl_error=start_url_error or "crawl returned 0 pages",
            unverified_resources=previous_entry.get("unverified_resources"),
            content_signals=previous_entry.get("content_signals"),
            content_signal_sources=previous_entry.get("content_signal_sources"),
            freshness_signal=fetch_freshness_signal(start_urls[0]),
        )
        return

    # Cross-page boilerplate (sidebar nav, footer, cookie banner — different
    # text on every site, so no fixed list could catch it) removed before
    # anything downstream sees these pages' text: content_signals detection,
    # chunking, and embedding all benefit from the same cleanup.
    pages = strip_cross_page_boilerplate(pages)

    urls_to_upsert = set(pages) - unchanged_urls
    # A capped crawl (hit_max_pages) only proves those particular pages were
    # never VISITED this run — not that they no longer exist. Treating an
    # unvisited page the same as a genuinely removed one would delete real,
    # still-live content purely because BFS order or a slightly smaller
    # max_pages this run didn't reach it yet (see path_scope()/2.6).
    removed_urls = set() if hit_max_pages else set(previous_pages) - set(pages)
    if unchanged_urls:
        print(f"\nFetched {len(pages)} pages ({len(unchanged_urls)} unchanged since last crawl, "
              f"{len(urls_to_upsert)} to re-embed). Chunking + embedding...")
    else:
        print(f"\nFetched {len(pages)} pages. Chunking + embedding...")

    raw_resources = merge_resources(classify_links(all_links_seen, design_system_name), probe_well_known(start_urls[0]))
    unverified_resources = flag_unverified_resources(raw_resources, design_system_name)
    resources = filter_unverified_resources(raw_resources, unverified_resources)
    enrichment = enrich_resources(resources)
    if resources:
        print(f"  discovered resources: {resources}")
    if unverified_resources:
        print(f"  excluded as unrelated (name doesn't obviously match): {unverified_resources}")
    if enrichment:
        print(f"  enrichment: {enrichment}")

    # An llms.txt/llms-full.txt is worth indexing whenever a system has one —
    # it's specifically written for an agent to read directly, often denser
    # and better-organized than the crawled HTML pages themselves — not only
    # as a last-resort fallback when the plain crawl found nothing. Used to
    # only ever be fetched in the zero-pages branch below; now added
    # unconditionally whenever resources.py discovers one, on top of
    # whatever the ordinary crawl already found.
    for llms_url in resources.get("agent_instructions", []):
        if llms_url in pages:
            continue
        llms_text = fetch_text_resource(llms_url)
        if llms_text and len(llms_text) >= MIN_CONTENT_LENGTH:
            print(f"  indexing agent-instructions file as its own page: {llms_url} ({len(llms_text)} chars)")
            pages[llms_url] = llms_text
            page_titles[llms_url] = f"{design_system_name} — llms.txt"
            urls_to_upsert.add(llms_url)

    # A start URL that fetched fine (no start_url_error) but yielded zero
    # usable pages is the classic signature of a client-side-rendered SPA —
    # the crawler only ever sees the pre-JS HTML shell, which has nothing in
    # it. The llms.txt fetch above already covers the "fall back to it"
    # case; likely_spa is now purely a "did that leave us with anything at
    # all" check, not a second fetch attempt.
    #
    # A less obvious version of the same problem: SOME pages render (enough
    # to be fetched and pass MIN_CONTENT_LENGTH on a nav shell/cookie banner/
    # loading skeleton) but almost none carry real content — confirmed live
    # on Atlassian (max_pages: 150, pages_indexed: 6, hit_max_pages: true —
    # 144 of 150 fetches were too-thin-to-embed). That crawl never triggers
    # the zero-pages branch above, so it silently looked like "a small site"
    # rather than "mostly can't render this," and hit_max_pages actively
    # suggested raising max_pages, which would only fetch more of the same
    # thin shells. thin_page_ratio is recorded either way, for the health
    # report, regardless of which branch below actually flips likely_spa.
    thin_page_ratio = 1 - (len(pages) / fetched_count) if fetched_count else 0.0
    if fetched_count >= 20 and thin_page_ratio > 0.9:
        likely_spa = True
    else:
        likely_spa = not pages and not start_url_error

    content_signals, content_signal_sources = detect_content_signals(pages)
    if content_signals:
        print(f"  content signals: {content_signals}")

    # Embed/upsert BEFORE writing registry/pages_index stats, and only record
    # pages that actually made it into Qdrant. This used to be the other way
    # around: registry stats + pages_index were written first, based on the
    # raw crawl, then _chunk_and_upsert ran last and could fail partway
    # (e.g. the embedding API rate-limiting exhausting its retries) without anything
    # upstream ever finding out. That looked like progress — pages_indexed
    # said 300 — but most of those pages had no vectors in Qdrant at all, and
    # since pages_index.json also recorded them as seen, the *next* run's
    # freshness check saw them as unchanged and skipped re-embedding them
    # forever: permanently "indexed" in metadata, permanently absent from
    # search. A failed page is now simply left out of both, so the next
    # ingest naturally retries it (it won't be in previous_pages).
    #
    # Upsert happens BEFORE any delete, and point IDs are now deterministic
    # (chunk_point_id) rather than random uuid4s — so re-embedding a page just
    # overwrites its existing chunks in place instead of leaving it with zero
    # vectors between "old ones deleted" and "new ones written". A page whose
    # embed fails simply keeps whatever it had before, rather than losing it.
    failed_urls, new_chunk_counts = _chunk_and_upsert(
        client, design_system_name, {url: pages[url] for url in urls_to_upsert}, page_titles,
    )
    if failed_urls:
        print(f"  {len(failed_urls)} page(s) failed to embed and will be retried on the next run")

    # Only NOW delete: (a) any stale tail chunks left over from a page that
    # got shorter, and (b) pages that no longer exist at all — both skip
    # failed_urls, which keep whatever vectors they already had.
    delete_stale_chunks(client, design_system_name, new_chunk_counts)
    delete_urls(client, design_system_name, removed_urls)

    persisted_pages = {url: pages[url] for url in pages if url not in failed_urls}

    update_registry_stats(
        design_system_name,
        pages_indexed=len(persisted_pages),
        resources=resources,
        enrichment=enrichment,
        hit_max_pages=hit_max_pages,
        crawl_error=start_url_error,
        likely_spa=likely_spa,
        unverified_resources=unverified_resources,
        content_signals=content_signals,
        content_signal_sources=content_signal_sources,
        freshness_signal=fetch_freshness_signal(start_urls[0]),
        thin_page_ratio=thin_page_ratio,
    )
    update_pages_index(
        design_system_name,
        [
            {"url": url, "title": page_titles.get(url, url), **page_freshness.get(url, {})}
            for url in persisted_pages
        ],
    )
    print("\nDone.")


def chunk_point_id(design_system_name: str, url: str, chunk_index: int) -> str:
    """Deterministic point ID (a hash of design system + url + chunk index)
    instead of a random uuid4 per chunk. Re-embedding an unchanged-count page
    then overwrites its existing points in place via upsert, rather than
    creating duplicates that a separate delete_urls() call has to clear out
    first — which used to mean stale-but-still-searchable vectors sat there
    for however long it took the embed step to run, and a page whose embed
    failed partway ended up with NO vectors at all until the next run
    succeeded. Also lets ingest_spa() skip its own delete-then-reinsert
    entirely: rendering the same start URL again just overwrites its chunks."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{design_system_name}|{url}|{chunk_index}"))


def _chunk_and_upsert(
    client: QdrantClient,
    design_system_name: str,
    pages: dict[str, str],
    page_titles: dict[str, str] | None = None,
) -> tuple[set[str], dict[str, int]]:
    """Shared tail of ingest()/ingest_spa(): chunk each page's text, embed,
    and upsert into Qdrant. Each page is embedded/upserted independently and
    the result is committed to Qdrant as it goes, so a failure on one page
    (e.g. exhausted embedding-API rate-limit retries) doesn't lose vectors already
    written for earlier pages in this same run. Returns (failed, chunk_counts):
    failed is the URLs that couldn't be embedded, so the caller can leave
    them out of registry/pages_index bookkeeping and let them be retried on
    the next run instead of being wrongly marked done; chunk_counts is
    {url: number of chunks written} for every URL that succeeded, so the
    caller can clean up any stale tail chunks left over from a previous,
    longer version of the same page (see delete_stale_chunks())."""
    page_titles = page_titles or {}
    failed = set()
    chunk_counts: dict[str, int] = {}
    indexed_at = now_iso()
    for url, text in pages.items():
        chunks = chunk_text(text)
        if not chunks:
            continue

        try:
            vectors = embed_texts(chunks)
            points = [
                PointStruct(
                    id=chunk_point_id(design_system_name, url, i),
                    vector=vector,
                    payload={
                        "url": url,
                        "design_system_name": design_system_name,
                        "text_content": chunk,
                        "title": page_titles.get(url, url),
                        "chunk_index": i,
                        "indexed_at": indexed_at,
                    },
                )
                for i, (chunk, vector) in enumerate(zip(chunks, vectors))
            ]
            client.upsert(collection_name=QDRANT_COLLECTION, points=points)
            print(f"  indexed {len(points)} chunks from {url}")
            chunk_counts[url] = len(points)
        except EmbeddingAuthError:
            # Not a per-page problem — every remaining embed call in this run
            # (this system's remaining pages, and every other system still
            # queued after it) would fail the exact same way, so there's
            # nothing to gain from treating this one URL as "just skip it and
            # keep going" the way a real per-page failure below does. Let it
            # propagate all the way out of the --all/--new/--refresh loop
            # (see safe_run) instead of silently burning the rest of this
            # run's crawl time on calls that are guaranteed to fail too.
            raise
        except Exception as e:
            print(f"  !! failed to embed/upsert {url}, will retry next run: {e}")
            failed.add(url)
    return failed, chunk_counts


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

    start_url = start_urls[0]
    text, links_seen = fetch_rendered_page(start_url)

    pages: dict[str, str] = {}
    page_titles: dict[str, str] = {}
    if text and len(text) >= MIN_CONTENT_LENGTH:
        pages[start_url] = text
        page_titles[start_url] = design_system_name
    print(f"\nRendered {len(pages)} page(s). Chunking + embedding...")

    previous_entry = get_registry_entry(design_system_name) or {}
    if not pages and previous_entry.get("pages_indexed"):
        # Same principle as ingest()'s empty-crawl guard: a render that came
        # back empty is far more likely this run's failure (a flaky headless
        # browser, a momentary bot-block) than proof the rendered content is
        # gone — don't wipe an already-indexed system's vectors over it.
        print(f"\n  render came back empty for a previously-indexed system — "
              f"leaving its {previous_entry.get('pages_indexed')} existing page(s) untouched.")
        update_registry_stats(
            design_system_name,
            pages_indexed=previous_entry.get("pages_indexed", 0),
            resources={},
            enrichment={},
            crawl_error="rendered fetch returned 0 pages",
            unverified_resources=previous_entry.get("unverified_resources"),
            content_signals=previous_entry.get("content_signals"),
            content_signal_sources=previous_entry.get("content_signal_sources"),
            freshness_signal=fetch_freshness_signal(start_url),
        )
        return

    raw_resources = merge_resources(classify_links(links_seen, design_system_name), probe_well_known(start_url))
    unverified_resources = flag_unverified_resources(raw_resources, design_system_name)
    resources = filter_unverified_resources(raw_resources, unverified_resources)
    enrichment = enrich_resources(resources)
    if resources:
        print(f"  discovered resources: {resources}")
    if unverified_resources:
        print(f"  excluded as unrelated (name doesn't obviously match): {unverified_resources}")
    if enrichment:
        print(f"  enrichment: {enrichment}")

    content_signals, content_signal_sources = detect_content_signals(pages)
    if content_signals:
        print(f"  content signals: {content_signals}")

    # Embed BEFORE writing registry stats — same reasoning as ingest()'s
    # ordering: a page whose embedding fails shouldn't be recorded as
    # indexed. Deterministic point IDs (chunk_point_id) mean upserting the
    # same start_url again just overwrites its existing chunks in place, so
    # unlike the old code, there's no full delete_existing() wipe needed at
    # all — a failed render simply leaves whatever was there before intact.
    failed_urls, new_chunk_counts = _chunk_and_upsert(client, design_system_name, pages, page_titles)
    delete_stale_chunks(client, design_system_name, new_chunk_counts)
    persisted_pages = {url: pages[url] for url in pages if url not in failed_urls}

    update_registry_stats(
        design_system_name,
        pages_indexed=len(persisted_pages),
        resources=resources,
        enrichment=enrichment,
        likely_spa=len(persisted_pages) == 0,
        # A successful render sticks: --all's plain-crawl filter excludes
        # render_mode == "spa" permanently, rather than relying on
        # likely_spa flipping False and being plain-recrawled next month,
        # finding 0 pages (since it can't execute JS either), and — before
        # this fix existed — deleting everything this render just wrote.
        render_mode="spa" if persisted_pages else previous_entry.get("render_mode"),
        unverified_resources=unverified_resources,
        content_signals=content_signals,
        content_signal_sources=content_signal_sources,
        freshness_signal=fetch_freshness_signal(start_url),
    )
    update_pages_index(design_system_name, [{"url": url, "title": page_titles.get(url, url)} for url in persisted_pages])
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
            mark_shard_touched(name)
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
        scope_paths=entry.get("scope_paths"),
        shallow=max_pages_override is not None,
    )


def safe_run(name: str, fn, *args, **kwargs) -> None:
    """Runs one entry's ingest and swallows any exception that isn't already
    handled inside crawl()/fetch_page() (a Qdrant/embedding-API error, a bug,
    etc) so it can't silently starve every other system still queued in this
    shard's loop — before this, one such crash meant every entry after it in
    --all/--new/--spa's for-loop never even ran, since nothing caught it.

    EmbeddingAuthError is the one exception deliberately NOT swallowed here: an
    invalid/expired API key or exhausted quota applies to every remaining
    system in this loop identically, not just this one, so "skip it and try
    the next entry" would just mean re-hitting the same wall (and burning
    the same crawl time first) for every system left in this shard. Letting
    it propagate stops the whole --all/--new/--refresh run immediately
    instead of silently limping to the end having indexed nothing."""
    try:
        fn(*args, **kwargs)
    except EmbeddingAuthError:
        raise
    except Exception as exc:
        print(f"  !! {name} failed unexpectedly, skipping: {exc}")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def eligible_for_all(entry: dict) -> bool:
    """--all's (and reindex-full.yml's prepare job's) filter, in one place so
    the two can't silently drift apart — see 1.8/eligible_for_spa(). A system
    stays out of the plain-crawl rotation once it's flagged likely_spa OR has
    a sticky render_mode == "spa": either way, a plain HTTP GET has already
    been shown not to find real content there, and would just waste requests
    finding nothing again. reindex-spa.yml's headless render is the only
    thing that can make progress on it (see eligible_for_spa)."""
    return (
        not entry.get("archived")
        and not entry.get("likely_spa")
        and entry.get("render_mode") != "spa"
    )


def eligible_for_spa(entry: dict) -> bool:
    """--spa's filter — the complement of eligible_for_all() among
    non-archived entries: anything currently flagged likely_spa (never
    successfully rendered yet) OR already known to need rendering
    (render_mode == "spa", so a monthly --all wouldn't touch it — see
    ingest_spa()'s docstring) gets re-rendered here instead."""
    return not entry.get("archived") and (entry.get("likely_spa") or entry.get("render_mode") == "spa")


def parse_shard(args: list[str]) -> tuple[int, int] | None:
    """--shard 1/4 means: process only entries at index i where i % 4 == 1.
    Splitting a long --all run across parallel CI jobs so one runner getting
    killed only loses its slice, not the whole 247-system run."""
    for i, arg in enumerate(args):
        if arg == "--shard" and i + 1 < len(args):
            index_str, _, total_str = args[i + 1].partition("/")
            return int(index_str), int(total_str)
    return None


def _limit_memory() -> None:
    """Belt-and-braces alongside fetch_page()'s streaming/size caps: if
    something still manages to balloon memory (a pathological page, a
    third-party library leak), raise MemoryError inside this process — where
    safe_run() catches it and moves on to the next system — rather than
    let the OS/CI runner starve and kill the whole job (see MAX_PAGE_BYTES).
    POSIX-only; a no-op anywhere resource.RLIMIT_AS isn't available (e.g. a
    local Windows dev run) since there's no equivalent knob to set there."""
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (4 * 1024**3, resource.RLIM_INFINITY))
    except Exception:
        pass


if __name__ == "__main__":
    _limit_memory()
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
        entries = [e for e in load_registry() if eligible_for_all(e)]
        if shard:
            entries = [e for i, e in enumerate(entries) if i % shard_total == shard_index]
            print(f"Shard {shard_index}/{shard_total}: {len(entries)} of {len([e for e in load_registry() if eligible_for_all(e)])} systems")
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
        spa_entries = [e for e in load_registry() if eligible_for_spa(e)]
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
