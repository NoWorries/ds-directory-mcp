"""
Builds sitemap.xml — the one AI-affordance signal this project checks for in
every OTHER system (resources.probe_sitemap, used as a freshness signal) that
it didn't ship itself until now.

Unlike llms.txt/systems.json (which use root-relative paths and need no
domain), a sitemap's <loc> entries are required to be absolute URLs — and
this repo genuinely doesn't know its own deployed domain (SETUP.md's Netlify
section is explicit that "any placeholder deploy is fine"; nothing else here
hardcodes it either). Rather than guess a domain, this reads it from the
SITE_URL env var and skips generation with a clear message when unset —
wrong absolute URLs in a sitemap are worse than no sitemap at all.
"""

from __future__ import annotations

import html
import os
from datetime import datetime, timezone
from pathlib import Path

from generate_directory import indexed_only, load_systems
from slug import slugify
from text_utils import full_name

OUTPUT_FILE = Path(__file__).parent / "sitemap.xml"

STATIC_PATHS = ["/", "/directory", "/search", "/components", "/suggest", "/report"]


def render_sitemap(site_url: str, entries: list[dict]) -> str:
    site_url = site_url.rstrip("/")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    urls = [(path, today) for path in STATIC_PATHS]
    urls += [
        (f"/systems/{slugify(full_name(e))}", (e.get("last_checked") or today)[:10])
        for e in entries
    ]

    entries_xml = "\n".join(
        f'  <url>\n    <loc>{html.escape(site_url + path)}</loc>\n    <lastmod>{lastmod}</lastmod>\n  </url>'
        for path, lastmod in urls
    )
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{entries_xml}\n</urlset>\n'


if __name__ == "__main__":
    site_url = os.environ.get("SITE_URL", "").strip()
    if not site_url:
        print(
            "SITE_URL not set — skipping sitemap.xml. Set it once this site has a real "
            "domain (e.g. https://your-site.netlify.app) to enable this."
        )
    else:
        systems = indexed_only(load_systems())
        OUTPUT_FILE.write_text(render_sitemap(site_url, systems))
        print(f"Wrote {OUTPUT_FILE} with {len(systems) + len(STATIC_PATHS)} URLs.")
