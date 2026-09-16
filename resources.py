"""
Discover secondary resources for a design system while crawling its docs site:
GitHub repo, Storybook, Figma files, markdown docs (design.md/CONTRIBUTING.md),
MCP server references, skill references, AI agent instruction files
(AGENTS.md, CLAUDE.md, llms.txt), npm package, and icon library links.

Once a GitHub repo and/or npm package is known, also pulls a few cheap signals
about it: license, star count, last-commit date, latest version, weekly downloads.

These aren't crawled/embedded for search — they're just linked from the site and
worth recording in the systems registry for humans (and future tooling) to use.
"""

import re
from urllib.parse import urljoin, urlparse

import requests

RESOURCE_PATTERNS = {
    "github": re.compile(r"github\.com/[\w.-]+/[\w.-]+/?$", re.IGNORECASE),
    "storybook": re.compile(r"storybook|chromatic\.com", re.IGNORECASE),
    "figma": re.compile(r"figma\.com/(file|design|proto)/", re.IGNORECASE),
    "markdown_docs": re.compile(r"/(design|contributing|readme)\.md$", re.IGNORECASE),
    "mcp": re.compile(r"mcp[-.]server|modelcontextprotocol|mcp\.json|mcp\.so", re.IGNORECASE),
    "skills": re.compile(r"/skills?/|skill\.md$", re.IGNORECASE),
    "agent_instructions": re.compile(r"/(agents|claude)\.md$|/llms(-full)?\.txt$", re.IGNORECASE),
    "npm": re.compile(r"npmjs\.com/package/", re.IGNORECASE),
    "icons": re.compile(r"/icons?(/|$)|icons?\.\w+\.(com|dev|io)", re.IGNORECASE),
    "copilot_instructions": re.compile(r"/copilot-instructions\.md$", re.IGNORECASE),
    "cursor_rules": re.compile(r"/\.cursorrules$|/\.cursor/rules", re.IGNORECASE),
    # Not every registry.json is one of these (any static site could have a
    # coincidentally-named file), so the path is anchored to the shadcn-style
    # convention (a bare/near-bare path, not buried arbitrarily deep) rather
    # than matching the filename anywhere.
    "registry": re.compile(r"/(r/)?registry\.json$", re.IGNORECASE),
}

# path -> resources.py key it counts as. Same taxonomy of "AI affordances" the
# State of AI in Design Systems survey tracks (agents-md, claude-md, llms-txt,
# mcp-server, copilot-instructions, cursor-rules, registry, ...) —
# https://state-of-ai-in-design-systems.netlify.app/ — extended here past the
# original llms.txt/AGENTS.md/CLAUDE.md/mcp.json set with the editor-specific
# instruction files and shadcn-style component registries most systems ship
# at a well-known root path rather than linking to from a docs page (so
# classify_links()'s crawled-link matching alone would miss them).
WELL_KNOWN_PATHS = {
    "llms.txt": "agent_instructions",
    "llms-full.txt": "agent_instructions",
    "AGENTS.md": "agent_instructions",
    "CLAUDE.md": "agent_instructions",
    ".well-known/mcp.json": "mcp",
    ".github/copilot-instructions.md": "copilot_instructions",
    ".cursorrules": "cursor_rules",
    ".cursor/rules": "cursor_rules",
    "registry.json": "registry",
}

# Matches npmjs.com's per-version package page (.../package/<name>/v/<semver>)
# — a docs site that links to a specific version in changelogs/release notes
# (PatternFly's is a real example: 150+ links, one per historical release of
# each of its ~30 component packages) would otherwise flood the npm resource
# list with dozens of duplicate entries for the exact same package. Stripped
# down to the canonical, versionless package URL before dedup so a
# multi-package system still shows every DISTINCT package once each, not
# every version of every package.
_NPM_VERSION_SUFFIX = re.compile(r"(/package/[^/]+(?:/[^/]+)?)/v/[\w.-]+/?$", re.IGNORECASE)


def canonicalize_npm_url(url: str) -> str:
    return _NPM_VERSION_SUFFIX.sub(r"\1", url).rstrip("/")


def classify_links(all_links: set[str]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {key: [] for key in RESOURCE_PATTERNS}
    for link in all_links:
        for key, pattern in RESOURCE_PATTERNS.items():
            if not pattern.search(link):
                continue
            value = canonicalize_npm_url(link) if key == "npm" else link
            if value not in found[key]:
                found[key].append(value)
    return {key: urls for key, urls in found.items() if urls}


def probe_well_known(start_url: str) -> dict[str, list[str]]:
    """Check common AI-agent discovery paths at the site root (e.g. /llms.txt,
    /.github/copilot-instructions.md) — see WELL_KNOWN_PATHS for the full set
    and what each one counts as.

    A 200 status alone isn't proof the path is real: a client-side-routed
    SPA commonly answers every unknown path with 200 + its normal HTML shell
    rather than a 404 (its router, not the server, decides what's "not
    found") — confirmed live against meshdesignsystem.com/.well-known/
    mcp.json, which returns 200 with content-type text/html, not real JSON.
    None of llms.txt/AGENTS.md/CLAUDE.md/mcp.json/a registry are ever
    legitimately served as text/html, so that content-type is treated as a
    false positive and skipped."""
    parsed = urlparse(start_url)
    root = f"{parsed.scheme}://{parsed.netloc}/"
    found: dict[str, list[str]] = {key: [] for key in set(WELL_KNOWN_PATHS.values())}

    for path, key in WELL_KNOWN_PATHS.items():
        url = urljoin(root, path)
        try:
            response = requests.head(url, timeout=8, allow_redirects=True)
            if response.status_code == 200 and "text/html" not in response.headers.get("content-type", "").lower():
                found[key].append(url)
        except requests.RequestException:
            continue

    return {key: urls for key, urls in found.items() if urls}


def probe_sitemap(start_url: str) -> dict:
    """Check for a sitemap (the robots.txt `Sitemap:` directive first, then
    the two conventional default paths) and, if found, the most recent
    <lastmod> across every URL it lists.

    This is a far more reliable "has this site actually changed" signal than
    fetch_change_signal()'s single ETag/Last-Modified header on the homepage
    alone — a sitemap's <lastmod> values reflect whatever page the site
    itself claims was last touched, not just the front door, so it catches a
    changed sub-page that never shows up in the homepage's own headers.
    Doesn't fetch/parse a sitemap index's child sitemaps — one network round
    trip, best-effort, not a guarantee of complete coverage either, just a
    much better signal than what a single page's headers give."""
    parsed = urlparse(start_url)
    root = f"{parsed.scheme}://{parsed.netloc}/"
    headers = {"User-Agent": "ds-directory-mcp/1.0"}

    candidates: list[str] = []
    try:
        robots = requests.get(urljoin(root, "robots.txt"), timeout=8, headers=headers)
        if robots.status_code == 200:
            candidates += re.findall(r"(?im)^sitemap:\s*(\S+)", robots.text)
    except requests.RequestException:
        pass
    candidates += [urljoin(root, "sitemap.xml"), urljoin(root, "sitemap_index.xml")]

    seen = set()
    for sitemap_url in candidates:
        if sitemap_url in seen:
            continue
        seen.add(sitemap_url)
        try:
            response = requests.get(sitemap_url, timeout=10, headers=headers)
            if response.status_code != 200 or "<" not in response.text:
                continue
        except requests.RequestException:
            continue

        lastmods = re.findall(r"<lastmod>\s*([^<\s]+)\s*</lastmod>", response.text, re.IGNORECASE)
        result = {"sitemap_url": sitemap_url}
        if lastmods:
            # ISO 8601 dates (with or without a time component) sort
            # correctly as plain strings, so max() finds the latest without
            # needing to parse each one.
            result["sitemap_last_modified"] = max(lastmods)
        return result

    return {}


def fetch_github_metadata(github_url: str) -> dict:
    """License, star count, and last-commit date for a github.com/owner/repo URL.
    Uses the unauthenticated GitHub API (60 req/hr) — fine for occasional reindexing."""
    match = re.search(r"github\.com/([\w.-]+)/([\w.-]+)", github_url, re.IGNORECASE)
    if not match:
        return {}
    owner, repo = match.groups()
    try:
        response = requests.get(
            f"https://api.github.com/repos/{owner}/{repo}",
            headers={"Accept": "application/vnd.github+json"},
            timeout=10,
        )
        if response.status_code != 200:
            return {}
        data = response.json()
    except requests.RequestException:
        return {}

    return {
        "license": (data.get("license") or {}).get("spdx_id"),
        "stars": data.get("stargazers_count"),
        "last_pushed": data.get("pushed_at"),
    }


def fetch_npm_metadata(npm_url: str) -> dict:
    """Latest version and weekly downloads for an npmjs.com/package/<name> URL."""
    match = re.search(r"npmjs\.com/package/([\w.@/-]+)", npm_url, re.IGNORECASE)
    if not match:
        return {}
    package = match.group(1)
    result = {}

    try:
        response = requests.get(f"https://registry.npmjs.org/{package}", timeout=10)
        if response.status_code == 200:
            result["latest_version"] = response.json().get("dist-tags", {}).get("latest")
    except requests.RequestException:
        pass

    try:
        response = requests.get(f"https://api.npmjs.org/downloads/point/last-week/{package}", timeout=10)
        if response.status_code == 200:
            result["weekly_downloads"] = response.json().get("downloads")
    except requests.RequestException:
        pass

    return result


def enrich_resources(resources: dict[str, list[str]]) -> dict:
    """Given discovered resource links, fetch cheap extra metadata for github/npm."""
    enrichment = {}
    if resources.get("github"):
        github_meta = fetch_github_metadata(resources["github"][0])
        if github_meta:
            enrichment["github_meta"] = github_meta
    if resources.get("npm"):
        npm_meta = fetch_npm_metadata(resources["npm"][0])
        if npm_meta:
            enrichment["npm_meta"] = npm_meta
    return enrichment


def merge_resources(*dicts: dict[str, list[str]]) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {}
    for d in dicts:
        for key, urls in d.items():
            merged.setdefault(key, [])
            for url in urls:
                if url not in merged[key]:
                    merged[key].append(url)
    return merged


# Words too generic to mean anything about whether a repo/package actually
# belongs to a given design system — "design system ui kit" tells you nothing
# on its own, so these don't count toward a name match either way.
_GENERIC_NAME_WORDS = {
    "design", "system", "systems", "ui", "kit", "library", "libraries", "components",
    "component", "framework", "docs", "documentation", "the", "and", "for", "guide",
    "guidelines", "style", "styleguide", "web", "app", "digital",
}


def _name_tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if len(w) >= 3 and w not in _GENERIC_NAME_WORDS}


def looks_related(url: str, design_system_name: str) -> bool:
    """Best-effort check that a discovered github/npm link is actually about
    this design system, rather than an unrelated dependency or tool that
    just happened to be linked somewhere on the page (a build-tool repo in a
    footer credits list, a "powered by" badge, etc — CFPB's own docs site
    linking to a Grunt-based build script is a real example of exactly this).

    Compares meaningful words in the design system's name against the
    repo/package name in the URL. Deliberately permissive (substring
    containment, not exact match) since names get abbreviated/prefixed
    often (e.g. "cloudscape-design/components" for "Cloudscape Design
    System") — the goal is only to catch links with NO plausible connection
    at all, not to be a strict validator that flags legitimate variations."""
    name_words = _name_tokens(design_system_name)
    if not name_words:
        return True  # nothing meaningful to compare against — don't flag on no signal

    slug = re.sub(r"^https?://[^/]+/", "", url).rstrip("/")
    slug_words = _name_tokens(re.sub(r"[/_-]", " ", slug))
    if not slug_words:
        return True

    if name_words & slug_words:
        return True
    return any(nw in sw or sw in nw for nw in name_words for sw in slug_words)


# Only github/npm carry an obvious "repo or package name" to compare against
# the system's own name — storybook/figma/docs/mcp links don't have an
# equivalent identity signal to check, so they're not flagged either way.
_CHECKED_RESOURCE_KEYS = ("github", "npm")


def flag_unverified_resources(resources: dict[str, list[str]], design_system_name: str) -> dict[str, list[str]]:
    """{key: [urls]} for every discovered github/npm URL whose name doesn't
    obviously relate to this design system — checked across ALL urls under
    a key, not just the first, since a key can have more than one discovered
    link (e.g. two npm packages) and only some might be unrelated.

    Never shown on the public site (a "?" badge there just confuses
    visitors with no way to act on it) — check_crawl_health.py reports these
    to the maintainer as a GitHub issue instead, for a human to actually
    look at and resolve (keep it, tag it as unrelated, or remove it)."""
    unverified: dict[str, list[str]] = {}
    for key in _CHECKED_RESOURCE_KEYS:
        bad = [u for u in resources.get(key, []) if not looks_related(u, design_system_name)]
        if bad:
            unverified[key] = bad
    return unverified
