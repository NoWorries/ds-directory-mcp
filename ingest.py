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
    python ingest.py --spa --system "Adobe — Spectrum"          # re-render just one likely_spa entry
    python ingest.py --refresh --shallow [--shard 1/4]   # already-indexed systems only, shallow — see below
    python ingest.py --followup [--shard 1/4]     # only systems whose last crawl hit max_pages — see below
    python ingest.py --system "Shopify — Polaris" [--force]   # re-ingest one entry (org — design_system)
    python ingest.py --system "Shopify — Polaris" --force --shallow   # ...capped at SHALLOW_MAX_PAGES
    python ingest.py --system "Shopify — Polaris" --force --max-pages 40   # ...capped at an explicit count instead

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

--followup: targets systems whose most recent crawl hit_max_pages (real
content was still queued when the crawl stopped — see crawl()'s hit_max_pages
return value) and re-crawls just those with a much larger ceiling
(FOLLOWUP_MAX_PAGES), instead of waiting for the monthly --all to eventually
raise DEFAULT_MAX_PAGES for everyone. A crawl always discovers links in the
same order on a re-run (same site, same nav/sitemap), so simply raising the
cap for these specific systems is enough to reach genuinely new pages, not
just re-fetch the same ones already indexed. force=True for the same reason
--all uses it: a system's homepage looking unchanged says nothing about
whether the REST of the site — the part this exists to actually reach — has
changed. hit_max_pages is recomputed fresh every run either way (see
update_registry_stats()), so a followup that finally exhausts a site's real
link graph within the new budget clears the flag on its own.

--spa: targets systems flagged likely_spa=True (see ingest()'s SPA-shell
detection) — sites where a plain HTTP GET only ever returns an empty
client-side-rendered shell, so the ordinary crawler finds nothing however
many times you retry it. Does a full BFS crawl (crawl_spa(), same shape as
crawl()'s own link-following) rendering every page through crawl4ai
(Playwright under the hood) instead of requests+BeautifulSoup — see
ingest_spa(). Each render is expensive (multi-second delay per page, by
design), so this is meant for an occasional deep pass, not every reindex.
Requires `playwright install --with-deps chromium` to have been run once
in the environment (see reindex-spa.yml).

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
    QDRANT_COLLECTION,
    get_qdrant_client,
)
from embeddings import MAX_BATCH_SIZE, EmbeddingFatalError, embed_texts
from content_signals import detect_content_signals
from resources import (
    UNMAINTAINED_THRESHOLD_DAYS,
    classify_links,
    compute_likely_unmaintained,
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
# {design_system_name: [url, ...]} — every URL crawl() has ever seen return a
# confirmed HTTP 404 (see fetch_page()'s is_confirmed_404), so a future crawl
# never has to spend a request re-discovering that the same dead link is
# still dead. Confirmed live: get.foundation's own nav/sitemap still
# reference ~80 removed /emails/*, /sites/* pages from discontinued
# sub-products, and developers.arcgis.com's sitemap lists ~100 stale
# /releases/changelogs/* entries — both re-hit on every single crawl before
# this existed. No expiry: a 404 essentially never un-404s, and the rare
# exception (a site restores an old path) costs at most one missing page
# until someone notices and manually removes the entry.
DEAD_LINKS_FILE = Path(__file__).parent / "dead_links.json"
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

# fetch_rendered_page()'s two content-quality checks — see its docstring for
# the real systems (Dynatrace Barista, Decentraland UI) that motivated each.
CODE_HOST_DOMAINS = {"github.com", "gitlab.com", "bitbucket.org"}
STORYBOOK_CHROME_MARKERS = ("Skip to sidebar", "Open canvas in new tab")

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
    # \b (not a literal trailing "/") so a bare "/blog" with nothing after it
    # still matches, not just "/blog/anything" — confirmed live, Meta's
    # Astryx links its blog INDEX as bare "https://astryx.atmeta.com/blog"
    # (no trailing slash), which "/blog/" never matched. Same bug class the
    # "changelog" entry below already fixed for a different shape of the
    # same mistake (also missed a bare trailing segment); this is that fix
    # applied to the whole trailing-slash-anchored batch at once, not just
    # the one pattern that happened to get caught first. "/tag/" and
    # "/tags/" both switch to "\b" too, and stay two separate entries — a
    # bare "/tag" not being followed by a word character (\b) still means
    # "/tags" itself doesn't accidentally match the "/tag" entry.
    r"/blog\b", r"/posts\b", r"/news\b", r"/articles\b", r"/insights\b",
    r"/careers\b", r"/jobs\b", r"/legal\b", r"/privacy",
    r"/terms", r"/pricing", r"/login", r"/signin",
    r"/sign-in", r"/search\?", r"/tag\b", r"/tags\b", r"/about-us",
    r"/press\b", r"/events\b", r"/newsletter",
    # "changelog" bare (was "/changelog", requiring a path-segment boundary)
    # — confirmed live, Semrush's Intergalactic names each component's
    # version-history page as a hyphenated suffix on the component's OWN
    # segment ("accordion-changelog"), never its own "/changelog/" segment,
    # so the slash-anchored version never matched it. 84 of that system's
    # 494 pages were changelogs, at higher chunk density than everything
    # else combined (version-bump entries chunk small and repetitive):
    # 3,871 of 6,109 total chunks (63%) for one system. "changelog" itself
    # is specific enough a word that matching it unanchored anywhere in a
    # URL is safe — it's not a plausible substring of an unrelated real
    # word or path.
    r"changelog",
    # Same categories, Italian: designers.italia.it's community section
    # accounted for 104 of 299 indexed pages on a real crawl (35%) — a
    # government/community design system publishing its own docs in Italian
    # has no reason to also use the English path words above. "notizie"
    # (news) and "eventi" (events) are unambiguous non-English words, safe
    # to exclude everywhere the same way their English equivalents already
    # are; "media" (press/media coverage here) is deliberately NOT added
    # globally — it's also a common English word for an asset library or
    # the "Media Object" UI pattern, so it's a per-system exclude_patterns
    # entry on this one system's systems.yaml record instead.
    r"/notizie\b", r"/eventi\b",
    # Component-preview/sandbox routes some design-system sites generate one
    # of per component per viewport (Storybook, Playroom, an iframe sandbox)
    # — real content, but as a RESOURCE link (see resources.py), not a page
    # worth crawling/embedding: confirmed live on AgDS, ~160 of a 300-page
    # crawl budget were burned on these before the real docs were reached.
    r"/responsive-preview", r"/playroom", r"/storybook/", r"/iframe\.html",
    r"/sandbox",
    # Same category, Indiana University Rivet's own convention: a standalone
    # per-variant preview page per component/layout/utility ("/components/
    # preview/default-accordion", "/layouts/preview/blank-page/single-
    # column", "/utilities/flex/preview/large-right-item", ...) rather than
    # Storybook/Playroom naming — confirmed live, every single one of these
    # linked from Rivet's real docs pages came back "too little content"
    # (just the live demo markup, no surrounding doc text), and there are
    # hundreds of them under every section of the site, not just /components/
    # — confirmed live the first, narrower attempt at this exclusion
    # (literal "/components/preview/") correctly cut the ones under
    # /components/ but the crawl just fell into the identical rabbit hole
    # one level down, under /layouts/preview/ and /utilities/*/preview/
    # instead. Enough of them to burn an entire max_pages budget (and,
    # worse, enough elapsed crawl time to risk this project's own external-
    # killer problem — see CLAUDE.md) on pure waste after the real docs
    # pages were already reached via the sitemap. Bare "/preview/" (not
    # anchored to any one parent section) is what actually needs excluding
    # here — a real page having "preview" as a complete path segment
    # anywhere is specific enough not to plausibly collide with genuine
    # content on some other system.
    r"/preview/",
    # Same category, Ant Design's own convention: a standalone isolated-demo
    # page per code example (e.g. "~demos/button-demo-loading") rather than
    # per component — confirmed live, 45 of 234 pages fetched in one crawl
    # attempt were these, 44 of them too thin to embed (just one code demo
    # each, no surrounding doc text).
    r"/~demos/",
    # Docusaurus's own `create-docusaurus` scaffold ships this exact page —
    # confirmed live on Equinor EDS (a Docusaurus site, per its /docs/Next/
    # versioned-docs URL convention): a boilerplate "This is a page created
    # from a markdown file" demo nobody removes, not real documentation.
    # Docusaurus is common enough among design-system docs sites that this
    # is worth excluding everywhere, not just for the one system it was
    # first seen on.
    r"/markdown-page",
    # GitHub's own repo-management UI — confirmed live on NASA Web Design
    # System, whose start_url is a bare GitHub repo (no separate docs site):
    # 260 of 463 crawled "pages" were /actions/ workflow runs and /commit(s)/
    # history alone, and almost everything left after excluding those was
    # STILL GitHub chrome (/branches, /compare, /forks, /community, /labels,
    # /tags, /security — repo management, never documentation) or a
    # /tree|blob/<40-hex-char commit SHA>/ historical snapshot duplicating
    # whatever the default branch already has crawled under /tree/main or
    # /blob/main. Domain-anchored (github\.com/<owner>/<repo>/...) rather
    # than bare path fragments so a real docs site's own "/actions/" (a UI
    # component) or "/releases/" (a changelog) page isn't caught by
    # accident — these shapes only ever mean "GitHub's own UI" when they
    # follow this exact prefix.
    r"github\.com/[^/]+/[^/]+/(actions|commit|commits|pull|pulls|issues|discussions)(/|$)",
    r"github\.com/[^/]+/[^/]+/(releases|compare|branches|network|graphs|find|pulse|deployments)(/|$)",
    r"github\.com/[^/]+/[^/]+/(watchers|stargazers|settings|community|forks|labels|tags|security|custom-properties)(/|$)",
    r"github\.com/[^/]+/[^/]+/(tree|blob)/[0-9a-f]{40}",
]

# A design system's own release-notes/updates page is occasionally under one
# of the paths above (rare) — that tradeoff is accepted; the much more common
# case is a marketing blog that isn't design-system documentation at all.

# A translated copy of a page already being crawled — not excluded for being
# unrelated content (DEFAULT_EXCLUDE_PATTERNS above), but for being the SAME
# content a second time. Confirmed live: Ant Design suffixes every doc's
# Chinese translation with "-cn" ("introduce" / "introduce-cn"), which would
# otherwise double every page crawled for zero benefit to English-language
# search. Kept as its own list (checked separately in crawl(), not folded
# into DEFAULT_EXCLUDE_PATTERNS) so a skip here can also set
# has_localized_docs — worth noting on the system's own directory page even
# though only the English side gets indexed. Inherently a heuristic: a path
# segment or "-xx" suffix matching a known language code is assumed to be
# that language's translation of the same page, not a first-class English
# page that happens to end the same way.
LANGUAGE_EXCLUDE_PATTERNS = [
    # Safe to match as either a "-xx" suffix or a "/xx/" path segment — these
    # codes essentially never double as an ordinary English word/slug ending.
    # The trailing "." alternative (alongside /, end-of-string, ?, #) is for
    # a bare translated file rather than a path, confirmed live: Ant Design
    # serves "button-cn.md" and "llms-semantic-cn.md" alongside their
    # English originals, which the boundary originally missed entirely.
    r"[-/](?:zh(?:-cn|-tw|-hans|-hant)?|cn|ja|jp|ko|kr|de|fr|pt-br|ru|nl|pl|tr)(?:/|$|\?|#|\.)",
    # Only matched as a clean /xx/ path segment — these short codes double as
    # common English words/suffixes ("-it", "-es", "-hi", "-ar") and would
    # false-positive as a hyphen suffix (e.g. a slug ending "...-with-it").
    # "et" (Estonian) confirmed live: brand.estonia.ee serves every guideline
    # twice, e.g. "/guidelines/icons-and-pictograms" and its exact Estonian
    # translation "/et/juhendid/ikoonid-ja-piktogrammid" — the slash-bounded
    # segment match is safe even though "et" is a common English word
    # fragment ("get", "budget", "internet"), since none of those ever have
    # a "/" immediately before AND after just "et".
    r"/(?:es|it|hi|ar|vi|th|id|pt|et)/",
    r"[?&](?:lang|locale|hl)=(?!en\b)[a-z]{2}(?:-[a-z]{2})?\b",
]

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
# max_pages. Still overridable per-entry in systems.yaml for anything
# unusually large or unusually small. When a crawl actually hits this
# ceiling (see crawl()'s hit_max_pages return value), that's recorded on
# the entry and reported by check_crawl_health.py, since it means there's
# likely more real content on the site than got indexed.
#
# Lowered from 300 to 150 once extract_text()'s nav-stripping-before-link-
# discovery bug was fixed (see crawl()) — before that fix, most sites
# stalled out at a handful of pages regardless of this ceiling, so 300 was
# rarely actually reached. Once real link discovery started working, a
# 300-page crawl's CRAWL_DELAY_SECONDS=1.0 alone takes 5+ minutes before
# any fetch/parse/embed time, uncomfortably close to (or over) the 8-minute
# self-timeout budget a single-system ingest run gets locally. 150 keeps
# the mandatory delay under 2.5 minutes, leaving real headroom for the rest
# — still deep enough for the large majority of real docs sites.
DEFAULT_MAX_PAGES = 150

# Used by --shallow: a deliberately small crawl ceiling so a "get breadth"
# pass can touch many systems quickly instead of going deep on a few. Enough
# pages for real (non-boilerplate) content to show up and for the directory
# page to stop showing a system as unindexed, without spending anywhere near
# the time/embedding cost of a full DEFAULT_MAX_PAGES crawl.
SHALLOW_MAX_PAGES = 8

# Used by --followup: a deliberately generous ceiling, well above
# DEFAULT_MAX_PAGES, for the specific systems already known to have more
# real content than a normal crawl budget reaches (hit_max_pages=True) —
# a targeted deep pass on a short list of large sites, not something every
# system pays the time cost of on every run.
FOLLOWUP_MAX_PAGES = 500


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
    host = (parsed.hostname or "").lower()
    # Despite this function's own docstring listing "www vs non-www" among
    # what it normalizes, the code never actually did this — confirmed live
    # on IBM's Carbon Design System: start_urls uses www.carbondesignsystem.com,
    # every single page on the live site 301s to the bare domain, and
    # in_any_scope()'s exact netloc match then treats every discovered link
    # as "redirected out of scope" and skips it — a full re-crawl came back
    # with 0 pages for a previously-268-page system. Stripping here fixes it
    # everywhere at once: root_scopes (built from normalized start_urls),
    # discovered links (extract_all_links() normalizes each one), and the
    # final_url in-scope check after following a redirect all agree once
    # www is never part of the comparison.
    if host.startswith("www."):
        host = host[4:]
    port = parsed.port
    default_port = {"http": 80, "https": 443}.get(parsed.scheme)
    netloc = host + (f":{port}" if port and port != default_port else "")

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


def fetch_page(url: str) -> tuple[BeautifulSoup | None, str | None, dict, str | None, bool]:
    """Returns (soup, None, freshness, final_url, False) on success or
    (None, error_message, {}, None, is_confirmed_404) on failure. The error
    string is only actually used for the start URL (see crawl()) — that's
    the one fetch failure worth recording on the registry entry, since a
    DNS/connection failure there usually means the whole site is
    unreachable (moved, renamed, taken down), not just one bad link deeper
    in the crawl. freshness is this page's own ETag/Last-Modified (if the
    server sent either), used by crawl() to skip re-embedding a page whose
    content hasn't changed since it was last indexed. final_url is
    response.url, normalized — requests follows redirects itself, so a link
    crawled at one URL can come back having landed somewhere else entirely
    (crawl() checks this is still in scope before trusting the page).

    is_confirmed_404 is True only for an actual HTTP 404 — never for a
    timeout, a 5xx, a DNS failure, or any other transient-looking error —
    since crawl() uses it to permanently remember this URL as dead (see
    DEAD_LINKS_FILE) and skip fetching it on every future crawl. A
    transient error genuinely might succeed next time; a 404 essentially
    never un-404s.

    Streams the response and enforces MAX_PAGE_BYTES/PAGE_READ_TIMEOUT_SECONDS
    regardless of what Content-Type the server claims — see MAX_PAGE_BYTES's
    docstring for why this exists at all: a mislabeled or simply huge body
    (a video, a JS bundle, a PDF) read fully into memory and then handed to
    BeautifulSoup is what took down a GitHub Actions runner in production.

    On a confirmed 404, retries ONCE with a trailing slash appended before
    giving up — confirmed live, Adeo's Mozaic Design System returns a real
    200 for "/components/accordion/" and a 404 for the exact same path
    without the slash. normalize_url() deliberately strips trailing slashes
    (needed for sites where "/x" and "/x/" ARE the same page — 268 confirmed
    near-duplicate pairs otherwise), which is exactly right for most sites
    but turns a real page into a false 404 on any site (Next.js/Nuxt strict
    routing, and others) where the slash is actually part of the route.
    Trying both costs one extra request only on an actual 404, never on a
    normal successful fetch."""
    soup, error, freshness, final_url, is_404 = _fetch_page_once(url)
    if is_404 and not url.endswith("/") and "?" not in url:
        retry_soup, retry_error, retry_freshness, retry_final_url, retry_is_404 = _fetch_page_once(url + "/")
        if retry_soup is not None:
            return retry_soup, retry_error, retry_freshness, retry_final_url, retry_is_404
    return soup, error, freshness, final_url, is_404


def _fetch_page_once(url: str) -> tuple[BeautifulSoup | None, str | None, dict, str | None, bool]:
    """One fetch attempt, no retry — see fetch_page()'s docstring for the
    return shape and the trailing-slash retry wrapped around this."""
    try:
        response = requests.get(
            url, timeout=(10, PAGE_READ_TIMEOUT_SECONDS), stream=True,
            headers={"User-Agent": "ds-directory-mcp/1.0"},
        )
        response.raise_for_status()

        content_type = response.headers.get("content-type", "").lower()
        if content_type and "html" not in content_type and "xhtml" not in content_type:
            response.close()
            return None, f"non-html content-type: {content_type.split(';')[0]}", {}, None, False

        started = time.monotonic()
        chunks = []
        total = 0
        for chunk in response.iter_content(chunk_size=65536):
            total += len(chunk)
            if total > MAX_PAGE_BYTES:
                response.close()
                return None, f"page exceeded {MAX_PAGE_BYTES} bytes, skipped", {}, None, False
            if time.monotonic() - started > PAGE_READ_TIMEOUT_SECONDS:
                response.close()
                return None, "page read exceeded time budget, skipped", {}, None, False
            chunks.append(chunk)
        raw = b"".join(chunks)
    except requests.exceptions.HTTPError as exc:
        print(f"  skip {url}: {exc}")
        is_404 = exc.response is not None and exc.response.status_code == 404
        return None, str(exc), {}, None, is_404
    except requests.RequestException as exc:
        print(f"  skip {url}: {exc}")
        return None, str(exc), {}, None, False

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
    return _parse_html(body), None, page_freshness_from_headers(response.headers), normalize_url(response.url), False


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


def is_language_variant(url: str) -> bool:
    return any(re.search(pattern, url, re.IGNORECASE) for pattern in LANGUAGE_EXCLUDE_PATTERNS)


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
    """<meta name="robots" content="...noindex..."> — the page author telling
    SEARCH ENGINES not to rank this page. That's a signal about search
    ranking, not about whether an AI agent should be able to find this
    system's own real documentation — this tool's whole purpose, unlike a
    search engine's. Confirmed live: REI's Cedar docs (rei.github.io/
    rei-cedar-docs/) and Indiana University's Rivet (rivet.uits.iu.edu) are
    both real, substantial documentation sites marked noindex sitewide with
    no alternate canonical — almost certainly to avoid a GitHub Pages/staging
    mirror competing with a "real" domain in Google, not a statement that
    the content itself isn't real docs. No longer used to skip a page (see
    its one call site) — kept only to log that the signal was present, which
    is still useful context, just not disqualifying."""
    if not soup.head:
        return False
    for meta in soup.head.find_all("meta", attrs={"name": lambda v: v and v.lower() in ("robots", "googlebot")}):
        if "noindex" in (meta.get("content") or "").lower():
            return True
    return False


_LLMS_TXT_LINK_RE = re.compile(r"\[[^\]]*\]\((https?://[^\s)]+)\)")

# Chakra UI's llms.txt lists exactly six of these and nothing else (no
# direct page links at all) — a generous-looking cap that in practice still
# means at most a handful of extra fetches for any real site.
MAX_LLMS_TXT_POINTER_FOLLOWS = 5


def fetch_llms_txt(start_url: str) -> str | None:
    """Best-effort fetch of {start_url}/llms.txt — silent on any failure (no
    llms.txt, timeout, non-200), same "this only ever adds seeds, an absent
    one changes nothing" spirit as fetch_sitemap_urls(). Deliberately
    path-relative to start_url rather than the site root: a multi-product
    site (mui.com/material-ui/, confirmed live) hosts its llms.txt per
    section, not at the bare domain — the opposite of probe_well_known()'s
    fixed root-only check, which exists for a different purpose (recording
    whether the site has one at all, not finding the actual file to seed
    from)."""
    return _fetch_llms_txt_style_url(start_url.rstrip("/") + "/llms.txt")


def _fetch_llms_txt_style_url(url: str) -> str | None:
    """Same best-effort contract as fetch_llms_txt(), for a URL that's
    already absolute — the piece shared with following a pointer link (see
    discover_llms_txt_seeds()) instead of always building the URL from a
    start_url + "/llms.txt"."""
    try:
        response = requests.get(url, timeout=8, headers={"User-Agent": "ds-directory-mcp/1.0"})
        if response.status_code != 200:
            return None
        content_type = response.headers.get("content-type", "").lower()
        if "html" in content_type:
            return None  # SPA-style catch-all 200, not a real llms.txt — same check probe_well_known() makes
        return response.text
    except requests.RequestException:
        return None


def _is_llms_txt_pointer_link(url: str) -> bool:
    """True for a link that's itself another llms*.txt-style index/dump
    (llms-full.txt, llms-components.txt, ...) rather than a single real
    page — Chakra UI's llms.txt is ONLY six of these, no direct page links
    at all."""
    last_segment = urlparse(url).path.rsplit("/", 1)[-1].lower()
    return "llms" in last_segment or last_segment.endswith(".txt")


def extract_llms_txt_page_urls(text: str) -> list[str]:
    """Pulls candidate real-page URLs out of an llms.txt-style file's
    markdown links — pointer links (see _is_llms_txt_pointer_link) excluded;
    discover_llms_txt_seeds() is what follows those instead of just
    dropping them. Deliberately absolute-only — every llms.txt sampled
    while building this (Ant Design, Chakra UI, MUI) uses relative links
    (./llms-full.txt, ./design.md) only for its own self-navigation, and
    absolute links only for genuine content pages/pointers, so skipping
    relative links entirely already filters out exactly that noise without
    needing to resolve them. A link ending in .md has that suffix stripped
    instead of being dropped — MUI's convention (.../react-app-bar.md) —
    since the matching HTML page this crawler can actually parse lives at
    the same path without it."""
    urls = []
    for url in _LLMS_TXT_LINK_RE.findall(text):
        if _is_llms_txt_pointer_link(url):
            continue
        if url.endswith(".md"):
            url = url[:-3]
        urls.append(url)
    return urls


def discover_llms_txt_seeds(start_url: str) -> list[str]:
    """Every real per-page URL fetch_llms_txt() can find for start_url,
    including one level into any "documentation set" links it points to
    instead of just dropping them as noise — confirmed live, Chakra UI's
    llms.txt is ONLY six such pointers (llms-full.txt, llms-components.txt,
    etc) with zero direct page links, so without following them that site
    (and presumably others shaped the same way) got nothing at all from
    this. A followed file that turns out to be one big inlined content dump
    rather than a link list (also Chakra's case, for every one of its six —
    confirmed live, llms-components.txt is 1.6MB of concatenated component
    docs with no per-page URLs anywhere in it) simply contributes no extra
    seeds; this only ever adds them, never raises or removes any."""
    top_text = fetch_llms_txt(start_url)
    if not top_text:
        return []

    seeds = extract_llms_txt_page_urls(top_text)
    pointer_urls = [u for u in _LLMS_TXT_LINK_RE.findall(top_text) if _is_llms_txt_pointer_link(u)]
    for pointer_url in pointer_urls[:MAX_LLMS_TXT_POINTER_FOLLOWS]:
        sub_text = _fetch_llms_txt_style_url(pointer_url)
        if sub_text:
            seeds.extend(extract_llms_txt_page_urls(sub_text))
    return seeds


def crawl(
    start_urls: list[str],
    max_pages: int = DEFAULT_MAX_PAGES,
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
    previous_pages: dict[str, dict] | None = None,
    scope_paths: list[str] | None = None,
    known_dead: set[str] | None = None,
) -> tuple[dict[str, str], set[str], dict[str, str], bool, str | None, set[str], dict[str, dict], int, bool, set[str]]:
    """Breadth-first crawl restricted to the start URLs' domain(s).

    Returns ({url: text}, all_links_seen, {url: page_title}, hit_max_pages,
    start_url_error, unchanged_urls, page_freshness, fetched_count,
    has_localized_docs, newly_dead) —
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

    has_localized_docs is True when a discovered link looked like a translated
    copy of a page already in scope (see LANGUAGE_EXCLUDE_PATTERNS) — only the
    English side ever gets crawled/embedded, but this flag lets the caller
    note on the system's own page that other languages exist.

    known_dead (see DEAD_LINKS_FILE/load_dead_links()): URLs already
    confirmed 404 on a previous crawl — skipped entirely, both when seeding
    from the sitemap and when following links, so a system whose own nav/
    sitemap references long-removed pages doesn't pay that request again on
    every single crawl. newly_dead is every URL THIS crawl confirmed 404
    that wasn't already in known_dead — the caller merges it in via
    update_dead_links() so it's remembered from here on.
    """
    include_patterns = include_patterns or []
    all_exclude_patterns = DEFAULT_EXCLUDE_PATTERNS + (exclude_patterns or [])
    previous_pages = previous_pages or {}
    known_dead = known_dead or set()
    newly_dead: set[str] = set()

    # Normalized up front so root_scopes' netloc (lowercased by normalize_url)
    # actually matches what extract_links() compares against (every link it
    # returns is normalize_url()'d too) — a start_url with a mixed-case host
    # would otherwise never match its own scope.
    start_urls = [normalize_url(u) for u in start_urls]

    if scope_paths:
        root_scopes = {(urlparse(u).netloc, p) for u in start_urls for p in scope_paths}
    else:
        root_scopes = {path_scope(u) for u in start_urls}

    has_localized_docs = False

    # A capped crawl (hit_max_pages) always started BFS from the same URL in
    # the same nav-link order, so the pages just past max_pages never got a
    # turn on a later run even as already-indexed ones kept getting
    # re-visited — the site's own sitemap (when it has one) is a much better
    # source of "everything that exists" than nav-link discovery order.
    #
    # Every filter the link-follow loop below applies to a discovered link
    # applies here too — confirmed live, Ant Design's sitemap lists its own
    # developer blog (already covered by DEFAULT_EXCLUDE_PATTERNS' "/blog/")
    # right alongside real doc pages; without this check, a sitemap seed
    # skipped that filter entirely and 37 blog posts got crawled as if they
    # were design-system documentation. Language variants are filtered here
    # for the same reason — a sitemap lists every localized URL right
    # alongside its English original, so without this a site like Ant
    # Design (every page duplicated with a "-cn" suffix) would seed hundreds
    # of Chinese pages straight into to_visit before the link-follow loop
    # ever got a chance to skip them.
    sitemap_locs = [normalize_url(loc) for loc in fetch_sitemap_urls(start_urls[0])]
    sitemap_seeds = []
    for u in sitemap_locs:
        if u in start_urls or not in_any_scope(u, root_scopes):
            continue
        if u in known_dead:
            continue
        if is_language_variant(u):
            has_localized_docs = True
            continue
        if is_excluded(u, all_exclude_patterns):
            continue
        if not matches_include(u, include_patterns):
            continue
        sitemap_seeds.append(u)
    if sitemap_seeds:
        print(f"  seeded {len(sitemap_seeds)} URL(s) from the sitemap")

    # Same idea as the sitemap seeding above, from a second independent
    # source — a site's own llms.txt, when it has one, is a curated "here
    # are our real pages" list written for exactly this purpose (agent
    # consumption), and about a third of the systems checked while adding
    # this have one but no sitemap at all, so this is pure upside rather
    # than a fallback for the no-sitemap case specifically: it still adds
    # incremental seeds even on a site that already has a full sitemap
    # (llms.txt sometimes surfaces pages the sitemap omits, or in a more
    # deliberately curated order), and the dedup below means it can only
    # ever add to sitemap_seeds, never duplicate or override it.
    llms_seeds = []
    discovered_llms_urls = discover_llms_txt_seeds(start_urls[0])
    if discovered_llms_urls:
        seen_llms_seeds = set(sitemap_seeds)
        for u in discovered_llms_urls:
            u = normalize_url(u)
            if u in seen_llms_seeds or u in start_urls or not in_any_scope(u, root_scopes):
                continue
            if u in known_dead:
                continue
            if is_language_variant(u):
                has_localized_docs = True
                continue
            if is_excluded(u, all_exclude_patterns):
                continue
            if not matches_include(u, include_patterns):
                continue
            seen_llms_seeds.add(u)
            llms_seeds.append(u)
    if llms_seeds:
        print(f"  seeded {len(llms_seeds)} URL(s) from llms.txt")

    # start_urls always go first, unsorted — crawl() checks fetch failures
    # against start_urls[0] specifically (see start_url_error below), which
    # needs it attempted early regardless of freshness. Sitemap/llms.txt
    # seeds after that are stable-sorted so previously-unseen URLs are
    # visited before ones already indexed last time: within "new" or within
    # "seen before" the crawl is still seed order, just with new content
    # prioritized over re-treading old ground when max_pages can't fit
    # everything.
    to_visit = start_urls + sorted(sitemap_seeds + llms_seeds, key=lambda u: u in previous_pages)
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
        soup, error, freshness, final_url, is_404 = fetch_page(url)
        time.sleep(CRAWL_DELAY_SECONDS)
        if soup is None:
            if url == start_urls[0]:
                start_url_error = error
            if is_404:
                newly_dead.add(url)
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

        # Link discovery MUST happen before extract_text() below — confirmed
        # live, extract_text() strips <nav>/<footer>/<aside> (STRIP_TAGS) out
        # of `soup` IN PLACE as part of cleaning up the text it returns. Any
        # site whose internal navigation lives in one of those (standard,
        # semantic HTML — not an edge case) had every one of those links
        # silently invisible to extract_all_links()/extract_links() once
        # this ran first: GitLab's Pajamas Design System crawled down to
        # just its own homepage (185 real links on the page, 1 visible to
        # the old call order) with no error of any kind to signal it.
        all_links_seen |= extract_all_links(soup, url)

        for scope in root_scopes:
            for link in extract_links(soup, url, scope):
                if link in visited or link in to_visit_set:
                    continue
                if link in known_dead:
                    continue
                if is_language_variant(link):
                    has_localized_docs = True
                    continue
                if is_excluded(link, all_exclude_patterns):
                    continue
                if not matches_include(link, include_patterns):
                    continue
                to_visit.append(link)
                to_visit_set.add(link)
                to_visit_set.add(link)

        if has_noindex(soup):
            print(f"  (noindex — a search-ranking signal, not skipped here: {page_key})")
        if is_auth_or_error_title(title):
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

    hit_max_pages = len(visited) >= max_pages and bool(to_visit)
    if hit_max_pages:
        print(f"  hit max_pages ({max_pages}) with {len(to_visit)} more page(s) still queued — coverage is likely incomplete")
    return (
        pages, all_links_seen, page_titles, hit_max_pages, start_url_error,
        unchanged_urls, page_freshness, len(visited), has_localized_docs, newly_dead,
    )


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
    "# 'consecutive_crawl_failures', 'thin_page_ratio', 'has_localized_docs', and\n"
    "# any '*_meta' fields are auto-populated by ingest.py — don't hand-edit them,\n"
    "# your changes will be overwritten on the next run. A missing 'pages_indexed'\n"
    "# means the entry has never been indexed (picked up by `ingest.py --new`).\n"
    "# 'thin_page_ratio' is the fraction of fetched pages that had too little\n"
    "# content to embed (1 - pages_indexed/fetched) — high alongside a real\n"
    "# fetch count usually means a mostly client-side-rendered site, not a\n"
    "# small one; see 'likely_spa'.\n"
    "# 'has_localized_docs' is true when the crawl saw links that looked like a\n"
    "# translated copy of a page already in scope (e.g. a '-cn'/'/ja/' variant) —\n"
    "# only the English side is ever crawled/embedded; this just flags that the\n"
    "# system also has other-language docs, worth a note on its own page.\n"
    "# 'likely_unmaintained' is self-computed (see resources.compute_likely_\n"
    "# unmaintained) from the most recent GitHub push / npm publish date, if\n"
    "# either is known — true when it's over ~2 years old. Distinct from\n"
    "# 'archived': the docs site here is still up and crawls fine, but the\n"
    "# project itself looks dead, so eligible_for_all()/--new/--refresh/\n"
    "# --followup all skip it going forward (only a manual `--system` run\n"
    "# re-checks it). A system with no known GitHub/npm link is never\n"
    "# flagged — no evidence of staleness isn't evidence of freshness either.\n"
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


def load_dead_links(design_system_name: str) -> set[str]:
    """Every URL previously confirmed 404 for this system (see
    DEAD_LINKS_FILE) — crawl() skips fetching any of these again."""
    if not DEAD_LINKS_FILE.exists():
        return set()
    return set(json.loads(DEAD_LINKS_FILE.read_text()).get(design_system_name, []))


def update_dead_links(design_system_name: str, dead_urls: set[str]) -> None:
    """Merges newly-confirmed-404 URLs into DEAD_LINKS_FILE — additive only
    (union with whatever was already recorded), since this file has no
    expiry and nothing else in the pipeline ever un-marks a URL as dead."""
    if not dead_urls:
        return
    index = {}
    if DEAD_LINKS_FILE.exists():
        index = json.loads(DEAD_LINKS_FILE.read_text())
    existing = set(index.get(design_system_name, []))
    index[design_system_name] = sorted(existing | dead_urls)
    DEAD_LINKS_FILE.write_text(json.dumps(index, indent=2, ensure_ascii=False))


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
    has_localized_docs: bool = False,
    likely_unmaintained: bool = False,
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
        "has_localized_docs": has_localized_docs,
        "likely_unmaintained": likely_unmaintained,
        "last_checked": now_iso(),
        **(freshness_signal or {}),
    }
    if resources:
        fields["resources"] = resources
    if enrichment:
        fields.update(enrichment)
    if render_mode is not None:
        # Sticky by default: once a system's content only shows up through a
        # rendered (headless-browser) fetch, it stays flagged that way even
        # after a successful render clears likely_spa — see ingest_spa()'s
        # docstring for why: without this, --all's next plain crawl treats it
        # as an ordinary system again, finds nothing (it still can't execute
        # JS), and — before this existed — deleted the vectors the render
        # wrote.
        #
        # But "sticky" can't mean "permanent regardless of new evidence":
        # confirmed live on AWS Cloudscape, which got stuck at render_mode
        # "spa" (and therefore excluded from --all forever, per
        # eligible_for_all()) after ONE crawl's thin_page_ratio false-
        # positive — even though a plain crawl fetches hundreds of pages of
        # real content there. ingest()'s plain-crawl path (unlike
        # ingest_spa(), which only ever sets "spa") passes "" here
        # specifically when THIS run's own plain crawl just found real,
        # non-thin content — using the same "" (not None) distinction
        # crawl_error already relies on above, since a bare `if render_mode`
        # can set the field but can never clear it back to falsy.
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
    """{url: {"etag":, "last_modified":, "title":}} from this system's last
    recorded crawl (pages_index.json) — etag/last_modified for crawl()'s
    per-page unchanged detection, title so ingest()'s carried_over_pages can
    keep listing a page it didn't happen to re-visit this run without
    losing its title. Empty for a system that's never been indexed — every
    page is then necessarily "new", exactly like before this existed."""
    if not PAGES_INDEX_FILE.exists():
        return {}
    index = json.loads(PAGES_INDEX_FILE.read_text())
    return {
        p["url"]: {"etag": p.get("etag"), "last_modified": p.get("last_modified"), "title": p.get("title")}
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
    client = get_qdrant_client()
    ensure_collection(client)
    previous_pages = load_previous_pages(design_system_name)
    known_dead = load_dead_links(design_system_name)

    pages, all_links_seen, page_titles, hit_max_pages, start_url_error, unchanged_urls, page_freshness, fetched_count, has_localized_docs, newly_dead = crawl(
        start_urls,
        max_pages=max_pages,
        include_patterns=include_patterns,
        exclude_patterns=exclude_patterns,
        previous_pages=previous_pages,
        scope_paths=scope_paths,
        known_dead=known_dead,
    )
    update_dead_links(design_system_name, newly_dead)
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
            has_localized_docs=previous_entry.get("has_localized_docs", False),
            likely_unmaintained=previous_entry.get("likely_unmaintained", False),
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
    likely_unmaintained = compute_likely_unmaintained(enrichment)
    if resources:
        print(f"  discovered resources: {resources}")
    if unverified_resources:
        print(f"  excluded as unrelated (name doesn't obviously match): {unverified_resources}")
    if enrichment:
        print(f"  enrichment: {enrichment}")
    if likely_unmaintained:
        print(f"  looks unmaintained (last real activity over {UNMAINTAINED_THRESHOLD_DAYS} days ago)")

    # An llms.txt/llms-full.txt is worth indexing whenever a system has one —
    # it's specifically written for an agent to read directly, often denser
    # and better-organized than the crawled HTML pages themselves — not only
    # as a last-resort fallback when the plain crawl found nothing. Used to
    # only ever be fetched in the zero-pages branch below; now added
    # unconditionally whenever resources.py discovers one, on top of
    # whatever the ordinary crawl already found.
    all_exclude_patterns = DEFAULT_EXCLUDE_PATTERNS + (exclude_patterns or [])
    for llms_url in resources.get("agent_instructions", []):
        if llms_url in pages:
            continue
        # Confirmed live on NASA Web Design System (start_url is a bare
        # GitHub repo): probe_well_known() checks the START URL's OWN
        # domain root for llms.txt, which for a repo hosted directly under
        # github.com means github.com's own platform-wide llms.txt (generic
        # "what is GitHub" text), not anything about the design system
        # itself — exclude_patterns already exists for exactly "this URL
        # doesn't belong to this system's real docs", so it should apply
        # here too, not just to crawl()'s own page-following.
        if is_excluded(llms_url, all_exclude_patterns):
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
    # A third signature, alongside the two above: the crawl fetched a page
    # fine (so it isn't the zero-pages case) but found next to no <a href>
    # links anywhere in it — all_links_seen includes off-domain and
    # filtered-out links too, so a real docs homepage almost always has at
    # least a handful (nav, footer, social) even when none of them end up
    # followed. A handful-or-fewer total is the same "JS renders the real
    # page client-side, the fetched HTML is just an empty shell" signature
    # as the zero-pages case, just one page short of it — confirmed live on
    # e.g. Adobe Spectrum and Biings (0 same-domain links in the fetched
    # HTML, despite both having large real docs sites behind client-side
    # routing). NOT every system stuck at 1 page fits this: AWS Cloudscape's
    # homepage has 42 real same-domain links and still only yielded 1 page,
    # which is a different bug elsewhere in the crawl/follow logic, not this
    # one — this check deliberately stays narrow (<=3 total links) rather
    # than trying to catch that case too, since a low-but-nonzero link count
    # could also just be a real crawl bug worth its own investigation, not a
    # site to route to the SPA renderer.
    # Capped at fetched_count <= 1 so it can't misfire on a real multi-page
    # site that simply has few external links from pages deeper in the crawl.
    thin_link_shell = fetched_count <= 1 and len(all_links_seen) <= 3
    # The thin_page_ratio > 0.9 signature (below, after persisted_pages/
    # carried_over_pages are known) can override this — everything else
    # decided right here.
    likely_spa = (not pages and not start_url_error) or (bool(pages) and thin_link_shell)

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

    # A capped crawl (hit_max_pages) only proves these older pages weren't
    # VISITED this run, not that they're gone (see removed_urls above,
    # which is exactly why their Qdrant vectors were left untouched) — so
    # carry them into this run's listing/count too, instead of quietly
    # dropping them the moment a capped run happens to land on a different
    # subset of pages than last time. This is what lets successive
    # budget-limited runs of the same system (a shallow crawl, a --max-pages
    # retry) accumulate real coverage over time — crawl()'s seed sort
    # already deliberately favors reaching NEW pages over previously-seen
    # ones on a capped run, so each one adds to the total rather than just
    # swapping one small subset for another.
    carried_over_pages = {}
    if hit_max_pages:
        carried_over_pages = {
            url: meta for url, meta in previous_pages.items()
            if url not in persisted_pages and url not in removed_urls
        }

    # thin_page_ratio > 0.9 alone isn't enough — confirmed live on TWO real
    # design systems now (AWS Cloudscape, Meta Astryx), both with a normal
    # doc-site shape: a handful of substantial hub/index pages (/, /components,
    # /patterns) plus hundreds of individually-brief per-component reference
    # pages (each just a short description — real content, not a rendering
    # failure). That shape trips a high ratio on ordinary max_pages budgets
    # without the site being remotely SPA-only. A genuine JS-only shell keeps
    # close to ZERO pages regardless of how many it fetches, so also
    # requiring a low ABSOLUTE accumulated total (not just a high ratio on
    # THIS run) tells the two apart.
    #
    # Deliberately checked against len(persisted_pages) + len(carried_over_
    # pages) — the same accumulated total about to become pages_indexed
    # below — rather than this run's raw len(pages). A capped run on a big
    # site (crawl()'s seed sort favors reaching NEW pages over re-treading
    # ones already indexed) can easily fetch mostly-new, mostly-thin leaf
    # pages this run alone, while its real hub pages from a PRIOR run don't
    # get re-visited at all — confirmed live, this is exactly what tripped
    # Astryx even after switching the check to len(pages): 18 accumulated
    # real pages, but len(pages) this run alone was still under 5. Checking
    # the accumulated total instead means a site that's already proven it
    # has substantial real content stays proven, regardless of how thin any
    # one run's fresh fetches look next to it.
    if fetched_count >= 20 and thin_page_ratio > 0.9 and (len(persisted_pages) + len(carried_over_pages)) < 5:
        likely_spa = True

    update_registry_stats(
        design_system_name,
        pages_indexed=len(persisted_pages) + len(carried_over_pages),
        resources=resources,
        enrichment=enrichment,
        hit_max_pages=hit_max_pages,
        crawl_error=start_url_error,
        likely_spa=likely_spa,
        unverified_resources=unverified_resources,
        content_signals=content_signals,
        content_signal_sources=content_signal_sources,
        freshness_signal=fetch_freshness_signal(start_urls[0]),
        # Clears a stale sticky render_mode=="spa" from an earlier run once
        # THIS plain crawl proves it's no longer warranted — real pages
        # persisted and this run didn't itself re-flag likely_spa (see
        # update_registry_stats' render_mode param for the Cloudscape case
        # this closes: a false-positive thin_page_ratio call, once, used to
        # mean --all excluded it forever after). Left as None (untouched)
        # otherwise, so a thin/failed run can't accidentally clear a
        # genuinely-still-needed "spa" flag it didn't itself just disprove.
        render_mode="" if persisted_pages and not likely_spa else None,
        thin_page_ratio=thin_page_ratio,
        has_localized_docs=has_localized_docs,
        likely_unmaintained=likely_unmaintained,
    )
    update_pages_index(
        design_system_name,
        [
            {"url": url, "title": page_titles.get(url, url), **page_freshness.get(url, {})}
            for url in persisted_pages
        ] + [
            {"url": url, "title": meta.get("title") or url, **{k: v for k, v in meta.items() if k != "title" and v}}
            for url, meta in carried_over_pages.items()
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


def _pack_pages_into_batches(page_chunks: dict[str, list[str]]) -> list[list[str]]:
    """Groups whole pages together up to MAX_BATCH_SIZE total chunks per
    batch — greedy first-fit, not optimal bin-packing, but good enough here
    since the goal is just "many fewer, larger embed_texts() calls than one
    tiny call per page", not a perfectly minimal count. Originally built to
    stay under a cloud embedding API's per-request/per-day limits (now moot
    with the local model embeddings.py uses — no such limit exists), it's
    kept because batching still amortizes per-call overhead in
    embed_texts()/model.encode() even locally. A single page whose own
    chunk count already exceeds MAX_BATCH_SIZE (Atlassian's llms-full.txt:
    800+ chunks) still gets its own batch on its own — embed_texts() already
    re-splits anything over the limit internally, so correctness holds
    either way, it just can't share a batch with anyone else."""
    batches: list[list[str]] = []
    current: list[str] = []
    current_size = 0
    for url, chunks in page_chunks.items():
        if current and current_size + len(chunks) > MAX_BATCH_SIZE:
            batches.append(current)
            current, current_size = [], 0
        current.append(url)
        current_size += len(chunks)
    if current:
        batches.append(current)
    return batches


def _chunk_and_upsert(
    client: QdrantClient,
    design_system_name: str,
    pages: dict[str, str],
    page_titles: dict[str, str] | None = None,
) -> tuple[set[str], dict[str, int]]:
    """Shared tail of ingest()/ingest_spa(): chunk each page's text, embed,
    and upsert into Qdrant.

    Chunks from multiple pages are packed together into one embed_texts()
    call per up-to-MAX_BATCH_SIZE-chunk batch (see _pack_pages_into_batches)
    instead of one call per page (see that function's docstring for why —
    originally a cloud-provider request-quota workaround, kept now as a
    plain batching-efficiency win). Each BATCH is still embedded/upserted
    independently and committed to Qdrant as it goes, so a failure on one
    batch doesn't lose vectors already written for earlier batches in this
    same run — the trade-off against the old per-page isolation is that a
    batch failure now takes every page sharing that batch down with it
    rather than just one, but a local model has far fewer ways to fail
    mid-run than a flaky network call did, so a batch-level failure here
    should be rare, not routine.
    Returns (failed, chunk_counts): failed is the URLs that couldn't be
    embedded, so the caller can leave them out of registry/pages_index
    bookkeeping and let them be retried on the next run instead of being
    wrongly marked done; chunk_counts is {url: number of chunks written}
    for every URL that succeeded, so the caller can clean up any stale tail
    chunks left over from a previous, longer version of the same page (see
    delete_stale_chunks())."""
    page_titles = page_titles or {}
    failed = set()
    chunk_counts: dict[str, int] = {}
    indexed_at = now_iso()

    page_chunks = {url: chunk_text(text) for url, text in pages.items()}
    page_chunks = {url: chunks for url, chunks in page_chunks.items() if chunks}

    for batch_urls in _pack_pages_into_batches(page_chunks):
        batch_texts: list[str] = []
        spans: dict[str, tuple[int, int]] = {}
        for url in batch_urls:
            start = len(batch_texts)
            batch_texts.extend(page_chunks[url])
            spans[url] = (start, len(batch_texts))

        try:
            vectors = embed_texts(batch_texts)
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
                for url in batch_urls
                for i, (chunk, vector) in enumerate(
                    zip(page_chunks[url], vectors[spans[url][0] : spans[url][1]])
                )
            ]
            client.upsert(collection_name=QDRANT_COLLECTION, points=points)
            for url in batch_urls:
                count = spans[url][1] - spans[url][0]
                chunk_counts[url] = count
                print(f"  indexed {count} chunks from {url}")
        except EmbeddingFatalError:
            # Not a per-batch problem — every remaining embed call in this
            # run (this system's remaining pages, and every other system
            # still queued after it) would fail the exact same way, so
            # there's nothing to gain from treating this one batch as "just
            # skip it and keep going" the way a real batch failure below
            # does. Let it propagate all the way out of the --all/--new/
            # --refresh loop (see safe_run) instead of silently burning the
            # rest of this run's crawl time on calls guaranteed to fail too.
            raise
        except Exception as e:
            for url in batch_urls:
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

    Two config options set here, confirmed live against real failures rather
    than guessed: excluded_tags=STRIP_TAGS (the same list extract_text() uses
    for a plain-fetched page) — without it, crawl4ai's default markdown
    conversion includes everything, and Apple's Human Interface Guidelines
    page rendered to nothing but its global site nav (Swift, SwiftUI, WWDC,
    Developer Forums, ...), zero real HIG content. delay_before_return_html
    gives client-side JS more time to actually populate the page before
    capture — without it, combined with excluded_tags, Apple's page came
    back nearly empty (the real content hadn't rendered yet); with both
    together it correctly returns the real "Human Interface Guidelines"
    heading and intro text. Raised from 2.0 to 5.0 after Salesforce's
    Lightning Design System (hosted on zeroheight) came back as nothing but
    its own branded loading shell ("Refresh to view the mobile styleguide",
    a spinner image) at 2.0 — confirmed live that 5.0 is enough for it to
    finish rendering into real content ("Bring your brand to life", real
    version/changelog text), so this is a genuine delay-tuning fix, not a
    structural block. That distinction matters: it does NOT fix a site
    whose real content lives inside an <iframe> on a different origin
    (Storybook-based sites — no amount of extra delay reaches into a
    different origin's iframe); MIN_CONTENT_LENGTH and the two checks below
    handle those instead."""
    import asyncio

    from crawl4ai import AsyncWebCrawler

    async def _run():
        async with AsyncWebCrawler(config=_get_browser_config()) as crawler:
            return await _render_one(crawler, url, RENDER_CONFIG)

    try:
        return asyncio.run(_run())[:2]
    except Exception as exc:
        print(f"  rendered fetch failed for {url}: {exc}")
        return None, set()


# Shared between fetch_rendered_page() (one-off single-page render) and
# crawl_spa() (a full BFS reusing one browser session across many pages,
# rather than paying Chromium's launch cost per page like calling
# fetch_rendered_page() in a loop would) — see fetch_rendered_page()'s own
# docstring for why these two specific settings are tuned the way they are.
RENDER_CONFIG = None  # set below, after CrawlerRunConfig is importable


def _get_render_config():
    global RENDER_CONFIG
    if RENDER_CONFIG is None:
        from crawl4ai import CrawlerRunConfig
        RENDER_CONFIG = CrawlerRunConfig(excluded_tags=STRIP_TAGS, delay_before_return_html=5.0)
    return RENDER_CONFIG


BROWSER_CONFIG = None  # set below, after BrowserConfig is importable


def _get_browser_config():
    """channel="chrome" (real installed Google Chrome), not crawl4ai's
    default "chromium" (Playwright's own bundled download) — confirmed live
    on GitHub Actions (ubuntu-latest, Ubuntu 24.04 "noble"): every single
    render crashed with chrome-headless-shell exiting on SIGTRAP immediately
    on launch, regardless of which system it was rendering, and this
    persisted even after (a) pinning crawl4ai to an exact known-working
    version and (b) disabling Ubuntu 24.04's AppArmor unprivileged-userns
    restriction via sysctl (confirmed via the workflow's own log output —
    the sysctl command itself succeeded, "= 0" printed — so that specific
    restriction genuinely wasn't the blocker here). Per Chromium's own
    documentation on this exact class of failure
    (chromium.googlesource.com/chromium/src/+/main/docs/security/apparmor-
    userns-restrictions.md): Google Chrome's official .deb package ships
    its OWN registered AppArmor profile (/etc/apparmor.d/opt.google.chrome)
    that permits the sandbox operations properly; a bare Playwright-
    downloaded Chromium binary has no such profile at all and depends
    entirely on the system-wide sysctl instead — which evidently wasn't
    sufficient in this environment. Real installed Chrome sidesteps the
    whole problem by being covered its own profile. reindex-spa.yml
    installs it via `playwright install --with-deps chrome` (not
    `chromium`) specifically so this channel is actually present to use."""
    global BROWSER_CONFIG
    if BROWSER_CONFIG is None:
        from crawl4ai import BrowserConfig
        BROWSER_CONFIG = BrowserConfig(channel="chrome", chrome_channel="chrome")
    return BROWSER_CONFIG


async def _render_one(crawler, url: str, config) -> tuple[str | None, set[str], str | None]:
    """One page's worth of fetch_rendered_page()'s old inline logic, pulled
    out so crawl_spa() can call it many times against the SAME already-
    launched crawler/browser session instead of relaunching Chromium per
    page the way calling fetch_rendered_page() in a loop would. Returns
    (text, links_seen, title) — title is best-effort (crawl4ai's own parsed
    <title>, when it exposes one), None if unavailable."""
    from urllib.parse import urlparse as _urlparse

    try:
        result = await crawler.arun(url=url, config=config)
        # Some crawl4ai versions return a list-like container for a single
        # URL rather than the CrawlResult itself.
        if isinstance(result, list):
            result = result[0] if result else None
    except Exception as exc:
        print(f"  rendered fetch failed for {url}: {exc}")
        return None, set(), None

    if result is None or not getattr(result, "success", False):
        print(f"  rendered fetch did not succeed for {url}")
        return None, set(), None

    # A docs site that now just 301s to a code host isn't a docs site
    # anymore — confirmed live, barista.dynatrace.com redirects straight to
    # https://github.com/dynatrace-oss/barista, and rendering it "succeeded"
    # in the sense that crawl4ai returned 10 chunks worth of markdown — all
    # of it GitHub's own file-listing/commit-log chrome (migrations.json,
    # commit messages, contributor avatars), not one word of actual design
    # guidance. The project itself was pushed 8 days ago, so this isn't an
    # abandoned/likely_unmaintained case — the docs site is just gone.
    redirected_url = getattr(result, "redirected_url", None) or url
    original_domain = _urlparse(url).netloc
    final_domain = _urlparse(redirected_url).netloc
    if final_domain != original_domain and final_domain in CODE_HOST_DOMAINS:
        print(f"  rendered fetch redirected to a code host ({final_domain}), not a docs site — treating as no content")
        return None, set(), None

    # .markdown is a str-subclass in every crawl4ai version that's shipped
    # (kept backward-compatible on purpose per crawl4ai's own models.py) —
    # str() on it always gives the raw markdown text either way.
    text = str(getattr(result, "markdown", "") or "")

    # Storybook's own toolbar chrome ("Skip to sidebar", "Open canvas in new
    # tab") survives excluded_tags because it isn't inside a <nav>/<footer> —
    # confirmed live on Decentraland UI, which cleared MIN_CONTENT_LENGTH on
    # toolbar text plus one leaked code fragment, not real prose
    # documentation (the real story content lives inside a same-origin
    # <iframe>, same structural limit as Appear Here's Bloom/Artsy Palette/
    # AutoGuru Overdrive — those three happened to fall under
    # MIN_CONTENT_LENGTH on their own; this is the same call for the ones
    # that don't).
    if any(marker in text for marker in STORYBOOK_CHROME_MARKERS):
        print("  rendered fetch is Storybook toolbar chrome, not real page content — treating as no content")
        return None, set(), None

    links_seen: set[str] = set()
    links = getattr(result, "links", {}) or {}
    if isinstance(links, dict):
        for group in ("internal", "external"):
            for item in links.get(group, []) or []:
                href = item.get("href") if isinstance(item, dict) else item
                if href:
                    links_seen.add(href)

    title = None
    metadata = getattr(result, "metadata", None) or {}
    if isinstance(metadata, dict):
        title = metadata.get("title") or None

    return text, links_seen, title


def crawl_spa(
    start_urls: list[str],
    max_pages: int = DEFAULT_MAX_PAGES,
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
    scope_paths: list[str] | None = None,
    known_dead: set[str] | None = None,
    on_page: "Callable[[str, str, str], None] | None" = None,
    previously_indexed: set[str] | None = None,
) -> tuple[dict[str, str], set[str], dict[str, str], bool, str | None, set[str]]:
    """crawl()'s BFS shape, but rendering every page through a real headless
    browser (_render_one(), one shared session for the whole crawl) instead
    of a plain GET — for systems already flagged likely_spa/render_mode ==
    "spa", where real per-page content only exists after client-side JS
    runs. Confirmed live: Meta's Astryx has 235 real component pages
    (Button, Badge, Avatar, ...) whose plain-fetched HTML is 100% nav/footer
    chrome with ZERO real content — ingest_spa() rendering only the
    homepage never reached any of them.

    Same scope/exclude/include/known_dead filtering as crawl() (reused
    directly — a page worth rendering is still subject to the same "is this
    even part of the system's own docs" rules a plain-fetched page would
    be), but deliberately without crawl()'s freshness/etag machinery: a
    render is expensive enough that ingest_spa() re-renders everything on
    every run rather than trying to skip unchanged pages.

    on_page(url, text, title), when given, is called synchronously right
    after each page clears MIN_CONTENT_LENGTH — before the crawl moves on
    to the next URL. This is what lets ingest_spa() embed+upsert and
    checkpoint pages_indexed incrementally rather than only at the very
    end: confirmed live, a run rendering all 142 reachable pages
    successfully still lost every one of them to an external SIGKILL
    that hit during the batched embed step afterward (see this project's
    own CLAUDE.md on the external killer). A render this expensive (multi-
    second delay per page, real minutes for a whole run) makes losing
    everything to one kill far costlier than crawl()'s plain-GET path,
    where a batched-to-the-end embed is comparatively instant.

    previously_indexed (a set of URLs, not the full previous_pages dict —
    only membership matters here) biases the queue toward pages NOT in it,
    the same way crawl()'s own seed sort favors reaching pages outside
    previous_pages first. Confirmed live this matters a LOT for this path
    specifically: an external SIGKILL (see this project's own CLAUDE.md)
    striking early and often meant a naive retry-the-same-crawl loop kept
    re-rendering the same already-persisted homepage/hub pages every single
    attempt before dying, never advancing deeper into the hundreds of
    still-unindexed leaf pages — 3 retries in a row added a total of 2 new
    pages. crawl()'s plain-GET path has the same bias built in for exactly
    this reason; render_spa()'s retries just hadn't needed it until a
    kill-prone environment made "will this run even finish" the real
    constraint rather than "is this URL reachable at all."

    Returns (pages, all_links_seen, page_titles, hit_max_pages,
    start_url_error, newly_dead) — same meaning as crawl()'s equivalents,
    minus the two return values (unchanged_urls, page_freshness,
    fetched_count, has_localized_docs) that only make sense for a plain
    fetch. hit_max_pages/start_url_error/newly_dead let the caller reuse
    ingest()'s exact carry-over/health-report logic unchanged. Still
    returned in full (not just via the callback) so a caller that doesn't
    need incremental checkpointing can use crawl_spa() exactly as before."""
    import asyncio

    from crawl4ai import AsyncWebCrawler

    include_patterns = include_patterns or []
    all_exclude_patterns = DEFAULT_EXCLUDE_PATTERNS + (exclude_patterns or [])
    known_dead = known_dead or set()
    previously_indexed = previously_indexed or set()
    newly_dead: set[str] = set()
    start_urls = [normalize_url(u) for u in start_urls]

    if scope_paths:
        root_scopes = {(urlparse(u).netloc, p) for u in start_urls for p in scope_paths}
    else:
        root_scopes = {path_scope(u) for u in start_urls}

    config = _get_render_config()

    async def _crawl():
        pages: dict[str, str] = {}
        page_titles: dict[str, str] = {}
        all_links_seen: set[str] = set()
        start_url_error: str | None = None
        visited: set[str] = set()
        to_visit = list(start_urls)
        to_visit_set = set(to_visit)

        async with AsyncWebCrawler(config=_get_browser_config()) as crawler:
            while to_visit and len(visited) < max_pages:
                url = to_visit.pop(0)
                to_visit_set.discard(url)
                if url in visited:
                    continue
                visited.add(url)

                print(f"Rendering: {url}")
                text, raw_links, title = await _render_one(crawler, url, config)
                if text is None:
                    if url == start_urls[0]:
                        start_url_error = "rendered fetch returned no content"
                    continue

                abs_links = {normalize_url(urljoin(url, href)) for href in raw_links}
                all_links_seen |= abs_links

                if len(text) >= MIN_CONTENT_LENGTH:
                    pages[url] = text
                    page_titles[url] = title or url
                    if on_page:
                        on_page(url, text, title or url)

                for link in abs_links:
                    if link in visited or link in to_visit_set:
                        continue
                    if link in known_dead:
                        continue
                    if not in_any_scope(link, root_scopes):
                        continue
                    if is_language_variant(link):
                        continue
                    if is_excluded(link, all_exclude_patterns):
                        continue
                    if not matches_include(link, include_patterns):
                        continue
                    to_visit.append(link)
                    to_visit_set.add(link)

                # Re-sort after every page, not just once at seed time (as
                # crawl()'s equivalent does) — see this function's own
                # docstring for why this matters much more here: an
                # unpredictable external kill means "how far did we get
                # before dying" is the real constraint, not just "will we
                # eventually reach everything." Stable sort keeps FIFO
                # order within each priority tier.
                to_visit.sort(key=lambda u: u in previously_indexed)

        hit_max_pages = len(visited) >= max_pages and bool(to_visit)
        if hit_max_pages:
            print(f"  hit max_pages ({max_pages}) with {len(to_visit)} more page(s) still queued — coverage is likely incomplete")
        return pages, all_links_seen, page_titles, hit_max_pages, start_url_error, newly_dead

    return asyncio.run(_crawl())


def ingest_spa(design_system_name: str, start_urls: list[str], max_pages: int = DEFAULT_MAX_PAGES) -> None:
    """Rendered ingest for a system already flagged likely_spa/render_mode ==
    "spa" — a real BFS crawl (crawl_spa()) rendering every page through a
    headless browser, not just the homepage. Confirmed live this matters:
    Meta's Astryx has 235 real component pages whose plain-fetched HTML is
    pure nav/footer chrome; rendering only the homepage (this function's
    original scope) never reached a single one of them.

    Every render is expensive (a multi-second delay per page, deliberately
    — see fetch_rendered_page()'s docstring) compared to a plain GET, so
    unlike ingest(), this doesn't try to skip unchanged pages — it always
    re-renders everything crawl_spa() reaches, up to max_pages.

    Persists INCREMENTALLY, one page at a time, via crawl_spa()'s on_page
    callback — embedding/upserting to Qdrant and checkpointing pages_indexed
    right after each page, rather than batching everything until the whole
    crawl finishes. Confirmed live this matters a lot here specifically:
    a run against Astryx successfully rendered all 142 reachable pages, then
    lost every single one of them to an external SIGKILL (see this
    project's own CLAUDE.md) that hit during the batched embed step
    afterward — three such kills in a row, zero pages ever saved, despite
    the rendering itself working perfectly each time. A kill between
    incremental checkpoints now loses at most the one page in flight,
    not the whole run.

    The checkpoint deliberately only ever touches pages_indexed (via the
    lighter update_registry_fields(), not the full update_registry_stats())
    plus pages_index.json — update_registry_stats() unconditionally writes
    hit_max_pages/content_signals/likely_unmaintained/etc with placeholder
    defaults on every call, which would wipe real metadata to those
    defaults on every single page if called that often. The one full
    update_registry_stats() call, with everything actually known, still
    only happens once, after the whole crawl completes normally."""
    client = get_qdrant_client()
    ensure_collection(client)

    start_url = start_urls[0]
    previous_entry = get_registry_entry(design_system_name) or {}
    previous_pages = load_previous_pages(design_system_name)
    known_dead = load_dead_links(design_system_name)

    persisted_pages: dict[str, str] = {}
    persisted_titles: dict[str, str] = {}

    def _checkpoint() -> None:
        carried_over = {url: meta for url, meta in previous_pages.items() if url not in persisted_pages}
        update_registry_fields(design_system_name, {"pages_indexed": len(persisted_pages) + len(carried_over)})
        update_pages_index(
            design_system_name,
            [
                {"url": url, "title": persisted_titles.get(url, url)}
                for url in persisted_pages
            ] + [
                {"url": url, "title": meta.get("title") or url, **{k: v for k, v in meta.items() if k != "title" and v}}
                for url, meta in carried_over.items()
            ],
        )

    def _persist_page(url: str, text: str, title: str) -> None:
        failed_urls, new_chunk_counts = _chunk_and_upsert(client, design_system_name, {url: text}, {url: title})
        if url in failed_urls:
            print(f"  {url} failed to embed — will retry next run")
            return
        delete_stale_chunks(client, design_system_name, new_chunk_counts)
        persisted_pages[url] = text
        persisted_titles[url] = title
        _checkpoint()

    pages, all_links_seen, page_titles, hit_max_pages, start_url_error, newly_dead = crawl_spa(
        start_urls,
        max_pages=max_pages,
        include_patterns=previous_entry.get("include_patterns"),
        exclude_patterns=previous_entry.get("exclude_patterns"),
        scope_paths=previous_entry.get("scope_paths"),
        known_dead=known_dead,
        on_page=_persist_page,
        previously_indexed=set(previous_pages),
    )
    update_dead_links(design_system_name, newly_dead)
    print(f"\nRendered {len(pages)} page(s), {len(persisted_pages)} persisted.")

    if not persisted_pages and previous_entry.get("pages_indexed"):
        # Same principle as ingest()'s empty-crawl guard: a render that came
        # back empty is far more likely this run's failure (a flaky headless
        # browser, a momentary bot-block) than proof the rendered content is
        # gone — don't wipe an already-indexed system's vectors over it.
        # Checked against persisted_pages (what's actually IN Qdrant), not
        # pages (what crawl_spa() rendered before any embed was attempted).
        print(f"\n  render came back empty for a previously-indexed system — "
              f"leaving its {previous_entry.get('pages_indexed')} existing page(s) untouched.")
        update_registry_stats(
            design_system_name,
            pages_indexed=previous_entry.get("pages_indexed", 0),
            resources={},
            enrichment={},
            crawl_error=start_url_error or "rendered fetch returned 0 pages",
            unverified_resources=previous_entry.get("unverified_resources"),
            content_signals=previous_entry.get("content_signals"),
            content_signal_sources=previous_entry.get("content_signal_sources"),
            freshness_signal=fetch_freshness_signal(start_url),
            likely_unmaintained=previous_entry.get("likely_unmaintained", False),
        )
        return

    raw_resources = merge_resources(classify_links(all_links_seen, design_system_name), probe_well_known(start_url))
    unverified_resources = flag_unverified_resources(raw_resources, design_system_name)
    resources = filter_unverified_resources(raw_resources, unverified_resources)
    enrichment = enrich_resources(resources)
    likely_unmaintained = compute_likely_unmaintained(enrichment)

    # MERGE with what the registry already knew, rather than replacing it
    # outright — unlike ingest()'s plain-crawl path (which always does one
    # comprehensive BFS and so can safely treat its own findings as the
    # full current picture), a single ingest_spa() run's all_links_seen/
    # persisted_pages can be a much NARROWER slice of the site than what's
    # actually indexed overall. This got confirmed live as a real data-loss
    # bug, not a hypothetical: previously_indexed prioritization (added to
    # survive the external killer — see crawl_spa()'s docstring) means a
    # run that happens to complete can easily have steered entirely clear
    # of the homepage/hub pages where a resource or content signal was
    # ORIGINALLY found, and the old blind-overwrite treated "didn't
    # re-find it this narrow run" as "it's gone" — wiping Astryx's real
    # figma/icons resources and its React content signal in exactly this
    # way. A resource/signal is only ever additive here: nothing already
    # known is ever dropped just because one particular run's slice didn't
    # happen to pass through the page it lives on.
    resources = merge_resources(previous_entry.get("resources") or {}, resources)
    unverified_resources = {**(previous_entry.get("unverified_resources") or {}), **unverified_resources}
    content_signals, content_signal_sources = detect_content_signals(persisted_pages)
    content_signals = {**(previous_entry.get("content_signals") or {}), **content_signals}
    content_signal_sources = {**(previous_entry.get("content_signal_sources") or {}), **content_signal_sources}

    if resources:
        print(f"  discovered resources: {resources}")
    if unverified_resources:
        print(f"  excluded as unrelated (name doesn't obviously match): {unverified_resources}")
    if enrichment:
        print(f"  enrichment: {enrichment}")
    if likely_unmaintained:
        print(f"  looks unmaintained (last real activity over {UNMAINTAINED_THRESHOLD_DAYS} days ago)")
    if content_signals:
        print(f"  content signals: {content_signals}")

    # No embed step here — _persist_page() already embedded/upserted each
    # page incrementally as crawl_spa() rendered it (see this function's
    # own docstring for why). persisted_pages/persisted_titles are exactly
    # what's actually in Qdrant right now, not a fresh re-embed of `pages`.
    page_titles = persisted_titles

    # This path has no removal-detection at all yet (crawl_spa() never
    # populates newly_dead — unlike crawl()'s fetch_page(), it doesn't tell
    # a real 404 apart from a page just not being reachable this run) — so,
    # even more conservatively than ingest()'s hit_max_pages-gated carry-
    # over, ALWAYS carry over a previously-rendered page this run didn't
    # revisit, regardless of hit_max_pages. A page dropping out of the
    # rendered link-graph (a nav change, a capped run landing elsewhere)
    # is never treated as proof it's gone — same "removal is only ever
    # deliberate" principle as the plain-crawl path, just with no positive
    # signal available yet to ever prove otherwise on this one.
    carried_over_pages = {
        url: meta for url, meta in previous_pages.items() if url not in persisted_pages
    }

    update_registry_stats(
        design_system_name,
        pages_indexed=len(persisted_pages) + len(carried_over_pages),
        resources=resources,
        enrichment=enrichment,
        hit_max_pages=hit_max_pages,
        crawl_error=start_url_error,
        likely_spa=not persisted_pages and not carried_over_pages,
        # A successful render sticks: --all's plain-crawl filter excludes
        # render_mode == "spa" permanently, rather than relying on
        # likely_spa flipping False and being plain-recrawled next month,
        # finding 0 pages (since it can't execute JS either), and — before
        # this fix existed — deleting everything this render just wrote.
        render_mode="spa" if (persisted_pages or carried_over_pages) else previous_entry.get("render_mode"),
        unverified_resources=unverified_resources,
        content_signals=content_signals,
        content_signal_sources=content_signal_sources,
        freshness_signal=fetch_freshness_signal(start_url),
        likely_unmaintained=likely_unmaintained,
    )
    update_pages_index(
        design_system_name,
        [
            {"url": url, "title": page_titles.get(url, url)}
            for url in persisted_pages
        ] + [
            {"url": url, "title": meta.get("title") or url, **{k: v for k, v in meta.items() if k != "title" and v}}
            for url, meta in carried_over_pages.items()
        ],
    )
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
    handled inside crawl()/fetch_page() (a Qdrant/embedding error, a bug,
    etc) so it can't silently starve every other system still queued in this
    shard's loop — before this, one such crash meant every entry after it in
    --all/--new/--spa's for-loop never even ran, since nothing caught it.

    EmbeddingFatalError is the one exception deliberately NOT swallowed
    here: the local embedding model failing to load (missing dependency,
    corrupted/incomplete cache download, out of memory) applies to every
    remaining system in this loop identically, not just this one, so "skip
    it and try the next entry" would just mean re-hitting the same wall
    (and burning the same crawl time first) for every system left in this
    shard. Letting it propagate stops the whole --all/--new/--refresh run
    immediately instead of silently limping to the end having indexed
    nothing."""
    try:
        fn(*args, **kwargs)
    except EmbeddingFatalError:
        raise
    except Exception as exc:
        print(f"  !! {name} failed unexpectedly, skipping: {exc}")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def eligible_for_all(entry: dict) -> bool:
    """--all's (and reindex-full.yml's prepare job's) filter, in one place so
    the two can't silently drift apart — see 1.8/eligible_for_spa(). A system
    stays out of the plain-crawl rotation once it's flagged likely_spa — a
    plain HTTP GET has already been shown not to find real content there,
    and would just waste requests finding nothing again. reindex-spa.yml's
    headless render is the only thing that can make progress on it (see
    eligible_for_spa).

    Deliberately does NOT also exclude on render_mode == "spa" (a past
    successful render) the way it used to — confirmed live, that blanket
    exclusion is actively wrong for a HYBRID system: Meta's Astryx has real,
    substantial hub pages (/, /components, /patterns) a plain crawl finds
    fine (likely_spa correctly False, per the accumulated-total fix above),
    but 235 individual component pages that need rendering. Once ingest_spa()
    ever renders anything for it, render_mode="spa" sticks permanently,
    which under the old check meant --all would never plain-crawl its real
    hub pages again either — the exact trap this whole area keeps almost
    falling into, just from the other direction this time. The thing
    render_mode=="spa" originally protected against — a plain re-crawl
    finding nothing and deleting the vectors a render wrote — is already
    independently handled by ingest()'s own "previous_pages and not pages"
    empty-crawl guard, regardless of render_mode, so this exclusion was
    redundant for a genuine SPA shell (already caught by likely_spa alone)
    and only ever actively harmful for a hybrid one.

    Also excludes likely_unmaintained (see resources.compute_likely_
    unmaintained) — distinct from archived (site confirmed gone): here the
    docs site is still up and crawlable fine, but the underlying project's
    last real activity is old enough that re-crawling it on the normal
    schedule isn't worth the cost. Unlike archived, this is self-computed
    and could in principle self-correct if the project revived — it won't,
    though, since a flagged system stops being re-crawled by exactly this
    filter; only a manual `--system` run re-checks it."""
    return (
        not entry.get("archived")
        and not entry.get("likely_spa")
        and not entry.get("likely_unmaintained")
    )


def eligible_for_spa(entry: dict) -> bool:
    """--spa's filter — the complement of eligible_for_all() among
    non-archived entries: anything currently flagged likely_spa (never
    successfully rendered yet) OR already known to need rendering
    (render_mode == "spa", so a monthly --all wouldn't touch it — see
    ingest_spa()'s docstring) gets re-rendered here instead."""
    return (
        not entry.get("archived")
        and not entry.get("likely_unmaintained")
        and (entry.get("likely_spa") or entry.get("render_mode") == "spa")
    )


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

    # An explicit page count, for callers that want something between
    # SHALLOW_MAX_PAGES's "get breadth across hundreds of systems fast" (8
    # pages) and a full deep crawl — e.g. a retry stage giving a system that
    # failed at full depth a real shot at finishing before it's killed
    # again, where 8 pages leaves most of its content unindexed but the site
    # clearly has a lot more room in its time budget than that took.
    max_pages_flag = None
    if "--max-pages" in args:
        flag_pos = args.index("--max-pages")
        max_pages_flag = int(args[flag_pos + 1])
        args = args[:flag_pos] + args[flag_pos + 2 :]

    shallow_max_pages = max_pages_flag if max_pages_flag is not None else (SHALLOW_MAX_PAGES if shallow else None)

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
        new_entries = [e for e in load_registry() if "pages_indexed" not in e and not e.get("archived") and not e.get("likely_unmaintained")]
        if shard:
            new_entries = [e for i, e in enumerate(new_entries) if i % shard_total == shard_index]
            print(f"Shard {shard_index}/{shard_total}: {len(new_entries)} unindexed systems in this shard")
        if not new_entries:
            print("No unindexed systems found in this shard — everything in systems.yaml has been indexed at least once.")
        for entry in new_entries:
            safe_run(full_name(entry), ingest_entry, entry, force=True, max_pages_override=shallow_max_pages)

    elif args[0] == "--refresh":
        refresh_entries = [e for e in load_registry() if e.get("pages_indexed") and not e.get("archived") and not e.get("likely_unmaintained")]
        if shard:
            refresh_entries = [e for i, e in enumerate(refresh_entries) if i % shard_total == shard_index]
            print(f"Shard {shard_index}/{shard_total}: {len(refresh_entries)} already-indexed systems in this shard")
        if not refresh_entries:
            print("No already-indexed systems found in this shard.")
        for entry in refresh_entries:
            safe_run(full_name(entry), ingest_entry, entry, force=True, max_pages_override=shallow_max_pages)

    elif args[0] == "--followup":
        followup_entries = [e for e in load_registry() if e.get("hit_max_pages") and not e.get("archived") and not e.get("likely_unmaintained")]
        if shard:
            followup_entries = [e for i, e in enumerate(followup_entries) if i % shard_total == shard_index]
            print(f"Shard {shard_index}/{shard_total}: {len(followup_entries)} capped systems in this shard")
        if not followup_entries:
            print("No systems currently flagged hit_max_pages — nothing to follow up on.")
        for entry in followup_entries:
            safe_run(full_name(entry), ingest_entry, entry, force=True, max_pages_override=FOLLOWUP_MAX_PAGES)

    elif args[0] == "--spa":
        spa_entries = [e for e in load_registry() if eligible_for_spa(e)]
        if "--system" in args:
            # Single-system escape hatch — same idea as top-level --system,
            # but dispatching to ingest_spa() specifically, since that one
            # always calls ingest_entry() instead. Lets a supervisor retry
            # or checkpoint one likely_spa system at a time.
            target_name = args[args.index("--system") + 1]
            spa_entries = [e for e in spa_entries if full_name(e) == target_name]
            if not spa_entries:
                print(f"No likely_spa entry named {target_name!r} found (must pass eligible_for_spa)")
                sys.exit(1)
        elif shard:
            spa_entries = [e for i, e in enumerate(spa_entries) if i % shard_total == shard_index]
            print(f"Shard {shard_index}/{shard_total}: {len(spa_entries)} likely_spa systems in this shard")
        if not spa_entries:
            print("No likely_spa systems found in this shard.")
        for entry in spa_entries:
            name = full_name(entry)
            print(f"\n=== {name} === (rendered)")
            safe_run(
                name, ingest_spa, name, entry["start_urls"],
                max_pages=shallow_max_pages if shallow_max_pages is not None else DEFAULT_MAX_PAGES,
            )

    elif args[0] == "--system":
        if len(args) < 2:
            print("Usage: python ingest.py --system \"<name from systems.yaml>\" [--force] [--shallow]")
            sys.exit(1)
        target_name = args[1]
        matches = [e for e in load_registry() if full_name(e) == target_name]
        if not matches:
            print(f"No entry named {target_name!r} in systems.yaml")
            sys.exit(1)
        ingest_entry(matches[0], force=force, max_pages_override=shallow_max_pages)

    elif len(args) >= 2:
        ingest(design_system_name=args[0], start_urls=args[1:])

    else:
        print(__doc__)
        sys.exit(1)
