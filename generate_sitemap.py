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

from generate_directory import indexed_only, load_systems, visible_by_default
from slug import slugify
from text_utils import full_name

OUTPUT_FILE = Path(__file__).parent / "sitemap.xml"

STATIC_PATHS = ["/", "/directory", "/compare", "/search", "/components", "/patterns", "/foundations", "/suggest", "/report"]

# Taxonomy routes whose individual pages (components/button.html, etc.) get
# their own sitemap entries — generate_components.py writes these three
# directories. Read directly rather than importing generate_components (only
# ROUTES' dir names are needed, and this keeps generate_sitemap.py runnable
# even if that module's heavier imports ever change).
TAXONOMY_DIRS = ["components", "patterns", "foundations"]


def taxonomy_page_paths() -> list[str]:
    """/<dir>/<slug> for every generated taxonomy page (skips index.html,
    which is already covered by its STATIC_PATHS entry above). Reads
    whatever generate_components.py already wrote to disk this run — the
    workflow generates components/patterns/foundations before sitemap.xml
    for exactly this reason."""
    paths = []
    for dir_name in TAXONOMY_DIRS:
        dir_path = Path(__file__).parent / dir_name
        if not dir_path.is_dir():
            continue
        for html_file in sorted(dir_path.glob("*.html")):
            if html_file.stem == "index":
                continue
            paths.append(f"/{dir_name}/{html_file.stem}")
    return paths


def render_sitemap(site_url: str, entries: list[dict]) -> str:
    site_url = site_url.rstrip("/")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    urls = [(path, today) for path in STATIC_PATHS]
    urls += [(path, today) for path in taxonomy_page_paths()]
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
        systems = visible_by_default(indexed_only(load_systems()))
        taxonomy_paths = taxonomy_page_paths()
        OUTPUT_FILE.write_text(render_sitemap(site_url, systems))
        print(f"Wrote {OUTPUT_FILE} with {len(systems) + len(STATIC_PATHS) + len(taxonomy_paths)} URLs "
              f"({len(taxonomy_paths)} components/patterns/foundations pages).")
