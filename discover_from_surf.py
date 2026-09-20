"""
Discovers design systems listed on https://designsystems.surf/design-systems/
that aren't already in our own systems.yaml — prints a candidate list for a
human to review, same as any "Suggest a system" submission; never writes to
systems.yaml itself. Complements ad-hoc submissions with a broader one-time
sweep of an existing curated aggregator (~90 systems at time of writing)
rather than waiting for individual suggestions to trickle in.

Deliberately only needs `requests` + PyYAML (see requirements-site.txt) —
no BeautifulSoup/lxml — so this runs anywhere the static-site generators
already do, independent of the crawl/embed stack (which needs a newer
Python locally right now — see embeddings.py/requirements-ingest.txt).

Usage: python3 discover_from_surf.py
"""

from __future__ import annotations

import re
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import requests
import yaml

SURF_LIST_URL = "https://designsystems.surf/design-systems/"
SYSTEMS_REGISTRY = Path(__file__).parent / "systems.yaml"
REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ds-directory-mcp-discovery/1.0)"}
# Same politeness delay ingest.py's own crawler uses (config.CRAWL_DELAY_SECONDS)
# — this fetches ~90 individual pages on the same third-party site in one run.
REQUEST_DELAY_SECONDS = 1.0

_SLUG_RE = re.compile(r'href="\./design-systems/([a-z0-9-]+)"')
_TITLE_RE = re.compile(r"<title>([^<|]+)")
_EXTERNAL_LINK_RE = re.compile(r'href="(https?://[^"]+)"')

# Site chrome/tooling hosts that show up on every designsystems.surf page —
# filtered out when picking the "real" external link for a candidate, so a
# Framer analytics script or a social-media footer link doesn't get mistaken
# for the system's own docs site.
_NOISE_HOST_SUBSTRINGS = (
    "designsystems.surf",
    "framer.com",
    "framerusercontent.com",
    "framerstatic.com",
    "fonts.gstatic.com",
    "fonts.googleapis.com",
    "linkedin.com",
    "threads.net",
    "instagram.com",
    "twitter.com",
    "x.com",
    "facebook.com",
)

# Real, legitimate links, but never the docs site itself — a page profiling
# one design system commonly also links its GitHub repo, Figma library,
# Storybook, npm package, and (confirmed live, Salesforce's page specifically)
# a one-off embedded YouTube walkthrough video. These are excluded from
# candidacy for the *primary* docs_url even though they're not noise.
_RESOURCE_HOST_SUBSTRINGS = (
    "github.com",
    "figma.com",
    "storybook.js.org",
    "npmjs.com",
    "youtube.com",
    "youtu.be",
)


def _fetch(url: str) -> str:
    response = requests.get(url, headers=REQUEST_HEADERS, timeout=20)
    response.raise_for_status()
    # requests falls back to guessing Latin-1 when a server doesn't send an
    # explicit charset — confirmed live, several designsystems.surf pages hit
    # that fallback despite actually being UTF-8, mangling em dashes in
    # titles ("Audi Design System â€" instead of "—"). apparent_encoding runs
    # a real content sniff (chardet/charset-normalizer) instead of assuming.
    if response.encoding is None or response.encoding.lower() == "iso-8859-1":
        response.encoding = response.apparent_encoding
    return response.text


def _is_noise(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(noise in host for noise in _NOISE_HOST_SUBSTRINGS)


def _is_resource_host(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(resource in host for resource in _RESOURCE_HOST_SUBSTRINGS)


def _domain(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def list_candidate_slugs() -> list[str]:
    html = _fetch(SURF_LIST_URL)
    return sorted(set(_SLUG_RE.findall(html)))


def fetch_candidate(slug: str) -> dict | None:
    """None if the page couldn't be fetched, or had no usable external link
    at all (just internal/noise links) — nothing meaningful to report either
    way."""
    url = f"https://designsystems.surf/design-systems/{slug}"
    try:
        html = _fetch(url)
    except requests.RequestException as exc:
        print(f"  !! failed to fetch {url}: {exc}")
        return None

    title_match = _TITLE_RE.search(html)
    name = title_match.group(1).strip() if title_match else slug

    external_links = [u for u in _EXTERNAL_LINK_RE.findall(html) if not _is_noise(u)]
    if not external_links:
        return None

    # The real docs site is the domain that recurs most often among this
    # page's external links — every /components/*, /guidelines/* deep link
    # on a system's profile page shares that one host. Picking "the first
    # external link found" instead — confirmed live, Salesforce's page
    # embeds a YouTube walkthrough video before its first real
    # lightningdesignsystem.com link — grabbed the video instead. Known
    # resource-type hosts (GitHub, Figma, Storybook, npm, YouTube) are
    # excluded from candidacy for the primary docs_url even if one recurs
    # often, since they're a resource ABOUT the system, not its own docs.
    domain_counts = Counter(_domain(u) for u in external_links if not _is_resource_host(u))
    if not domain_counts:
        return None
    primary_domain, _ = domain_counts.most_common(1)[0]
    # Shortest URL seen for that domain — most likely the bare root rather
    # than a deep page.
    docs_url = min((u for u in external_links if _domain(u) == primary_domain), key=len)
    github_url = next((u for u in external_links if "github.com" in u), None)

    return {"slug": slug, "name": name, "docs_url": docs_url, "github_url": github_url}


def existing_domains() -> set[str]:
    entries = yaml.safe_load(SYSTEMS_REGISTRY.read_text()) or []
    domains = set()
    for entry in entries:
        for url in entry.get("start_urls") or []:
            domains.add(_domain(url))
    return domains


def main() -> None:
    print(f"Fetching {SURF_LIST_URL} ...")
    slugs = list_candidate_slugs()
    print(f"Found {len(slugs)} listed design systems.\n")

    known_domains = existing_domains()
    candidates = []

    for slug in slugs:
        candidate = fetch_candidate(slug)
        time.sleep(REQUEST_DELAY_SECONDS)
        if not candidate:
            continue
        if _domain(candidate["docs_url"]) in known_domains:
            continue
        candidates.append(candidate)
        print(f"  NEW: {candidate['name']} — {candidate['docs_url']}")

    print(f"\n{len(candidates)} candidate(s) not already in {SYSTEMS_REGISTRY.name} "
          f"(matched by domain against existing start_urls):\n")
    for c in candidates:
        extra = f"  (GitHub: {c['github_url']})" if c["github_url"] else ""
        print(f"  - {c['name']}: {c['docs_url']}{extra}")

    print(
        "\nDiscovery report only — nothing was written to systems.yaml. Domain "
        "matching is a heuristic (a system already indexed under a different "
        "subdomain/rebrand could show up here as a false 'new') — review before "
        "adding any of these, same as reviewing a real submission."
    )


if __name__ == "__main__":
    main()
