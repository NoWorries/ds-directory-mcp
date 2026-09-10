"""
Discover secondary resources for a design system while crawling its docs site:
GitHub repo, Storybook, Figma files, markdown docs (design.md/CONTRIBUTING.md),
MCP server references, skill references, and AI agent instruction files
(AGENTS.md, CLAUDE.md, llms.txt).

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


def merge_resources(*dicts: dict[str, list[str]]) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {}
    for d in dicts:
        for key, urls in d.items():
            merged.setdefault(key, [])
            for url in urls:
                if url not in merged[key]:
                    merged[key].append(url)
    return merged
