"""
Light dry-run check for a submitted design system, before a maintainer approves
it. Crawls a handful of pages (no Qdrant writes — this never touches the live
index) and runs the same resource discovery as a real ingest, so a human
reviewer sees exactly what they'd get without waiting for a full crawl.
"""

from ingest import crawl
from resources import classify_links, enrich_resources, merge_resources, probe_well_known

DRY_RUN_MAX_PAGES = 10


def run_dry_check(name: str, start_url: str, max_pages: int = DRY_RUN_MAX_PAGES) -> str:
    if not start_url.startswith(("http://", "https://")):
        return f"❌ **Invalid start URL** — `{start_url}` doesn't look like a URL. Please edit the submission."

    pages, all_links, _page_titles = crawl([start_url], max_pages=max_pages)
    resources = merge_resources(classify_links(all_links), probe_well_known(start_url))
    enrichment = enrich_resources(resources)

    lines = [f"## Dry-run result for **{name}**", ""]

    if not pages:
        lines.append(
            "❌ **No usable pages found.** A trial crawl fetched 0 pages with enough real content "
            "to index. This usually means the site is JS-rendered (the crawler only reads static "
            "HTML, not what a browser renders after JavaScript runs), it blocked the request, or "
            "the start URL is wrong. Worth checking the URL manually before approving."
        )
    elif len(pages) < 3:
        lines.append(
            f"⚠️ **Only {len(pages)} page(s) found** in a {max_pages}-page trial crawl. Might just "
            "need a different start URL or `include_patterns` tuning, or might be JS-rendered — "
            "worth a manual look before approving."
        )
    else:
        lines.append(f"✅ **{len(pages)} pages found** in a {max_pages}-page trial crawl — looks crawlable.")

    lines.append("")
    lines.append("### Sample pages fetched")
    for url in list(pages)[:5]:
        lines.append(f"- {url}")
    if not pages:
        lines.append("_None._")

    lines.append("")
    lines.append("### Resources discovered")
    if resources:
        for key, urls in resources.items():
            lines.append(f"- **{key}**: {urls[0]}")
    else:
        lines.append("_None found._")

    if enrichment.get("github_meta"):
        gh = enrichment["github_meta"]
        lines.append("")
        lines.append(
            f"**GitHub** — license: {gh.get('license', '?')}, stars: {gh.get('stars', '?')}, "
            f"last pushed: {gh.get('last_pushed', '?')}"
        )
    if enrichment.get("npm_meta"):
        npm = enrichment["npm_meta"]
        lines.append(
            f"**npm** — latest version: {npm.get('latest_version', '?')}, "
            f"weekly downloads: {npm.get('weekly_downloads', '?')}"
        )

    lines.append("")
    lines.append("---")
    lines.append(
        "**To approve:** add the `approved` label to this issue — it'll be added to "
        "`systems.yaml`, crawled in full immediately, and show up in the directory within minutes.\n"
        "**To reject:** just close this issue."
    )

    return "\n".join(lines)
