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
}

WELL_KNOWN_PATHS = ["llms.txt", "llms-full.txt", "AGENTS.md", "CLAUDE.md", ".well-known/mcp.json"]


def classify_links(all_links: set[str]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {key: [] for key in RESOURCE_PATTERNS}
    for link in all_links:
        for key, pattern in RESOURCE_PATTERNS.items():
            if pattern.search(link) and link not in found[key]:
                found[key].append(link)
    return {key: urls for key, urls in found.items() if urls}


def probe_well_known(start_url: str) -> dict[str, list[str]]:
    """Check common AI-agent/MCP discovery paths at the site root (e.g. /llms.txt)."""
    parsed = urlparse(start_url)
    root = f"{parsed.scheme}://{parsed.netloc}/"
    found: dict[str, list[str]] = {"agent_instructions": [], "mcp": []}

    for path in WELL_KNOWN_PATHS:
        url = urljoin(root, path)
        try:
            response = requests.head(url, timeout=8, allow_redirects=True)
            if response.status_code == 200:
                key = "mcp" if "mcp" in path.lower() else "agent_instructions"
                found[key].append(url)
        except requests.RequestException:
            continue

    return {key: urls for key, urls in found.items() if urls}


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
