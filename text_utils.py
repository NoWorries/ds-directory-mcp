from __future__ import annotations

import re
from urllib.parse import urlparse

# Common em/en-dash and smart-quote sequences that show up as mojibake when a
# UTF-8 page got decoded as Latin-1 (see ingest.py's fetch_page — fixed there
# for future crawls, but titles already stored in pages_index.json from before
# that fix still carry the garbled bytes). Repaired here on read so already-
# crawled titles display correctly without needing a re-crawl.
_MOJIBAKE_REPAIRS = {
    'â\x80\x94': '—',  # em dash
    'â\x80\x93': '–',  # en dash
    'â\x80\x99': '’',  # right single quote / apostrophe
    'â\x80\x98': '‘',  # left single quote
    'â\x80\x9c': '“',  # left double quote
    'â\x80\x9d': '”',  # right double quote
}


def repair_mojibake(text: str) -> str:
    for bad, good in _MOJIBAKE_REPAIRS.items():
        text = text.replace(bad, good)
    return text


# Some already-crawled pages_index.json titles carry a literal "<!-- -->"
# left over from ingest.extract_title()'s pre-fix behavior (a title tag
# whose child nodes included an HTML comment — a Next.js hydration artifact
# — got flattened into the title text itself, e.g. "skeleton<!-- --> <!--
# -->—<!-- --> Lens"). extract_title() no longer produces this for a fresh
# crawl, but stripping it here means already-stored titles display cleanly
# too, without waiting for a re-crawl.
_HTML_COMMENT = re.compile(r"<!--.*?-->")


def strip_stray_html_comments(text: str) -> str:
    return re.sub(r"\s+", " ", _HTML_COMMENT.sub("", text)).strip()


# Trailing start-URL segments that name a system's front door rather than a
# section of it — see path_scope().
LANDING_PAGE_SEGMENTS = {
    "home", "index", "introduction", "intro", "overview", "welcome",
    "getting-started", "get-started", "start",
}


def path_scope(start_url: str) -> tuple[str, str]:
    """(netloc, path prefix) a link must fall under to count as a child of
    this start URL — not just the same domain. A design system that lives
    at a subpath of a huge corporate domain (e.g.
    microsoft.com/design/fluent/) would otherwise treat that domain's ENTIRE
    unrelated site as in-scope the moment any page links to the homepage or
    a nav item outside /design/fluent/ — confirmed live: Fluent's crawl
    wandered into Xbox, Surface hardware, and Microsoft 365 pricing pages,
    285 of them, because they all share microsoft.com. "The point of the
    provided URL is to search from there, and its children" — same-domain
    alone doesn't express that, same-domain-under-this-path does.

    A start URL that's already just a domain root ("/") is unaffected —
    its prefix is "/", which every same-domain path satisfies anyway.

    Two refinements, both from checking every non-root start URL in the
    registry against what it had actually indexed:
    - A start URL that points at a landing PAGE inside the system
      (design.gs.com/home/, tegel.scania.com/home/, vercel.com/design/
      introduction/, reshaped.so/content/docs/getting-started/overview)
      would otherwise scope the crawl to just that one page's subtree and
      lose the entire system. Trailing segments that are clearly "the front
      door, not a section" are stripped first — repeatedly, so
      getting-started/overview collapses to the docs root.
    - A path without a trailing slash is kept as-is as the prefix
      (/quickbooks matches /quickbooks and /quickbooks/...) unless it looks
      like a file (introduction.html), whose parent directory is used. The
      old "always take the parent" rule made /quickbooks scope to the whole
      domain while /quickbooks/ scoped correctly — the same system behaving
      differently on a trailing slash.
    An entry can always pin this explicitly with `scope_paths` in
    systems.yaml (see ingest.crawl()) when the heuristic gets it wrong.

    Shared between ingest.py (crawl scoping) and generate_components.py
    (excluding an off-scope crawled page from taxonomy matching — see
    build_component_index()) — pure urllib logic with no heavy dependencies,
    so it lives here rather than in ingest.py, which the generator scripts
    deliberately don't import (keeps their dependency set to PyYAML/requests,
    not qdrant-client/beautifulsoup4/crawl4ai)."""
    parsed = urlparse(start_url)
    path = parsed.path or "/"
    if "." in path.rsplit("/", 1)[-1]:
        path = path.rsplit("/", 1)[0] + "/"
    segments = [s for s in path.split("/") if s]
    while segments and segments[-1].lower() in LANDING_PAGE_SEGMENTS:
        segments.pop()
    prefix = "/" + "/".join(segments)
    if path.endswith("/") and not prefix.endswith("/"):
        prefix += "/"
    return parsed.netloc, prefix


def in_any_scope(url: str, root_scopes: set[tuple[str, str]]) -> bool:
    parsed = urlparse(url)
    path = parsed.path or "/"
    return any(parsed.netloc == netloc and path.startswith(prefix) for netloc, prefix in root_scopes)


def humanize_url_path(url: str) -> str:
    """Last URL path segment, turned into something readable —
    "progress-bar" -> "Progress bar". Used as a last-resort display title
    wherever no real title survived: ingest.extract_title()'s final fallback
    when a page has no <title>/og:title/<h1> at all, and generate_systems.py/
    generate_components.py's fallback for a pages_index.json title that's
    just a bare URL (written before that fallback chain existed) or that
    clean_title() couldn't distinguish from another page on the same
    system."""
    segment = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1] or urlparse(url).netloc
    words = re.split(r"[-_]+", segment)
    return " ".join(words).strip().capitalize() or url


def split_org_name(entry: dict) -> tuple[str, str]:
    """A systems.yaml entry's (organization, design_system) fields, e.g.
    ("Adobe", "Spectrum"). organization is "" for entries with no real org
    (e.g. a standalone framework like "Foundation")."""
    return entry.get("organization", ""), entry.get("design_system", "")


def full_name(entry: dict) -> str:
    """"Adobe" + "Spectrum" -> "Adobe — Spectrum" — the single display/identity
    string used for Qdrant's design_system_name payload, pages_index.json keys,
    and slugs. Falls back to just the design_system when there's no org."""
    org, ds_name = split_org_name(entry)
    return f"{org} — {ds_name}" if org else ds_name


# A page's own title segment that just names a generic section rather than
# the actual thing the page is about — dropped by clean_title() the same way
# a repeated site-name segment is, since keeping either would surface as a
# useless page label ("Components", "Home") wherever titles get shown.
GENERIC_TITLE_SEGMENTS = {"home", "components", "docs", "documentation", "overview"}

_TITLE_SEPARATOR = re.compile(r"\s+(?:\||—|–|-|·|::)\s+")


def _title_segments(title: str) -> list[str]:
    return [s.strip() for s in _TITLE_SEPARATOR.split(title) if s.strip()]


def clean_title(title: str, *names: str, site_titles: list[str] | None = None) -> str:
    """"Components — Button | PIE" -> "Button". Site doc-page titles are
    almost always "<actual title> <separator> <site name...>", but WHICH
    segment is the real one varies by site — some lead with a generic
    section name instead of the page's own title, which the old "always keep
    the first segment" rule turned into just "Components" for every single
    one of that system's pages (confirmed live: 49/78 PIE pages, 238/296 BBC
    GEL pages, all 300/300 GOLD pages). Splitting on whitespace-padded
    separators only (not a bare hyphen) avoids wrongly splitting an
    identifier like "Sign-up" or "co-op" that happens to contain one.

    Instead: drop every segment that's generic noise or the site's own
    recurring name, then keep the LONGEST remaining one — relies on
    site_titles (below) to actually identify and drop a multi-word site name
    that would otherwise just be the longest segment by coincidence.

    - names (e.g. the org and/or system name) are dropped as segments,
      case-insensitively — kept as a *args like dedupe_system_name() for
      the same call-site convenience.
    - GENERIC_TITLE_SEGMENTS above are always dropped.
    - site_titles, when given (every page title from this same system,
      itself included), lets a segment that repeats in >30% of them get
      dropped too — the site-name suffix/prefix a system stamps on every
      title, whatever it happens to say, without needing to know it in
      advance. Passing this is what makes the PIE/BBC GEL/GOLD case above
      actually resolve to the page's own title rather than to the site name.

    Falls back to the original title (repaired, whitespace-normalized) if
    every segment gets filtered out, rather than returning an empty string
    the caller then has to handle specially."""
    repaired = strip_stray_html_comments(repair_mojibake(title))
    segments = _title_segments(repaired)
    if not segments:
        return repaired.strip()

    drop = {n.strip().lower() for n in names if n}
    drop |= GENERIC_TITLE_SEGMENTS

    if site_titles:
        counts: dict[str, int] = {}
        total = 0
        for other in site_titles:
            total += 1
            for seg in set(s.lower() for s in _title_segments(strip_stray_html_comments(repair_mojibake(other)))):
                counts[seg] = counts.get(seg, 0) + 1
        if total:
            drop |= {seg for seg, count in counts.items() if count / total > 0.3}

    kept = [s for s in segments if s.lower() not in drop]
    if not kept:
        return repaired.strip()
    return max(kept, key=len)


# Page titles that mean "this isn't the design system's content, it's an
# auth wall / consent screen / error page that happened to render enough
# text to clear MIN_CONTENT_LENGTH" — confirmed live: MYOB Feelix's crawl
# landed on a GitHub OAuth "Sign in to GitHub" redirect for all 18 of its
# "pages". Shared between ingest.py (skips these during crawl — see
# is_recursive_trap()'s neighbors there) and check_crawl_health.py/
# generate_directory.py (flags a system where this got through anyway,
# e.g. before this check existed, until it's re-crawled).
AUTH_TITLE_PATTERNS = [
    r"^sign in", r"^log ?in\b", r"^login\b", r"^sign up", r"^signup",
    r"just a moment", r"access denied", r"^404\b", r"page not found",
    r"^redirecting", r"attention required", r"^untitled$",
]


def is_auth_or_error_title(title: str) -> bool:
    return any(re.search(pattern, title, re.IGNORECASE) for pattern in AUTH_TITLE_PATTERNS)


def dedupe_system_name(cleaned_title: str, *names: str) -> str:
    """A page title that survives clean_title() can still just BE the system
    name — some sites put their own name first in the title ("Mozaic Design
    System - Breadcrumb"), which clean_title's "keep the first segment" rule
    can't distinguish from the normal case. Used wherever the system name is
    already shown right next to the title (component pages, system page
    lists), so it isn't shown a second time for nothing. Falls back to the
    original if stripping every candidate name would leave nothing."""
    for name in filter(None, names):
        if cleaned_title.strip().lower() == name.strip().lower():
            return ""
    return cleaned_title
