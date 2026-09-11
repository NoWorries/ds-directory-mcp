"""
Builds one detail page per system (systems/<slug>.html): full-size preview,
complete resource list, freshness info (when the site itself last changed vs.
when we last checked it), and the full list of crawled pages — replacing the
cramped in-card "Browse N pages" expander with its own page.

The directory page's cards become lightweight previews linking here, the way
a search-results snippet links to the real page rather than inlining it.
"""

from __future__ import annotations

import html
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

from generate_directory import COLUMNS, favicon_html, indexed_only, load_pages_index, load_systems, split_org_name, thumbnail_src
from page_shell import FONT_LINK, TOKENS_CSS, routes_nav
from slug import slugify
from text_utils import clean_title

SYSTEMS_DIR = Path(__file__).parent / "systems"


def format_http_date(value: str | None) -> str:
    if not value:
        return "unknown"
    try:
        return parsedate_to_datetime(value).strftime("%-d %b %Y")
    except (TypeError, ValueError):
        return value


def format_iso(value: str | None) -> str:
    if not value:
        return "never"
    try:
        dt = datetime.fromisoformat(value)
        return dt.strftime("%-d %b %Y, %H:%M UTC")
    except ValueError:
        return value


def render_resource_list(entry: dict) -> str:
    resources = entry.get("resources") or {}
    found = [(label, resources[key][0]) for key, label in COLUMNS if resources.get(key)]
    if not found:
        return '<p class="empty-note">No secondary resources discovered yet.</p>'
    items = "".join(
        f'<li><a href="{html.escape(url)}" target="_blank" rel="noopener">{label}</a></li>'
        for label, url in found
    )
    return f'<ul class="resource-list">{items}</ul>'


def render_page_list(name: str, pages_index: dict) -> str:
    pages = pages_index.get(name) or []
    if not pages:
        return '<p class="empty-note">No pages indexed yet.</p>'
    items = "".join(
        f'<li><a href="{html.escape(p["url"])}" target="_blank" rel="noopener">{html.escape(clean_title(p["title"]))}</a></li>'
        for p in sorted(pages, key=lambda p: clean_title(p["title"]).lower())
    )
    return f'<ul class="page-list">{items}</ul>'


def render_system_page(entry: dict, pages_index: dict) -> str:
    name_text = entry["name"]
    org, ds_name = split_org_name(name_text)
    start_url = (entry.get("start_urls") or [None])[0]
    favicon = favicon_html(start_url)

    pages_count = entry.get("pages_indexed")
    github_meta = entry.get("github_meta") or {}
    npm_meta = entry.get("npm_meta") or {}

    meta_parts = []
    if pages_count is not None:
        meta_parts.append(f"{pages_count} pages indexed")
    if github_meta.get("license"):
        meta_parts.append(github_meta["license"])
    if github_meta.get("stars") is not None:
        meta_parts.append(f'{github_meta["stars"]:,}★')
    if npm_meta.get("latest_version"):
        meta_parts.append(f'v{npm_meta["latest_version"]}')

    thumb_html = ""
    if start_url:
        src = thumbnail_src(name_text, start_url)
        thumb_html = (
            f'<a href="{html.escape(start_url)}" target="_blank" rel="noopener">'
            f'<img class="hero-thumb" src="{src}" alt="" loading="lazy"></a>'
        )

    domain = urlparse(start_url).netloc if start_url else ""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(ds_name)} — Design System Directory</title>
{FONT_LINK}
<style>
{TOKENS_CSS}
  .page {{ max-width: 760px; margin: 0 auto; }}
  .system-header {{ display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }}
  .org-label {{ font-size: 0.78rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; color: var(--text-faint); }}
  .meta-line {{ font-family: "JetBrains Mono", monospace; font-size: 0.85rem; color: var(--text-muted); margin: 0 0 20px; }}
  .meta-line .badge {{ background: var(--accent-soft); color: var(--accent-soft-text); border-radius: 4px; padding: 1px 6px; }}

  .hero-thumb {{
    display: block; width: 100%; aspect-ratio: 16 / 9; object-fit: cover; object-position: top;
    border-radius: 10px; border: 1px solid var(--border); background: var(--surface-sunken); margin-bottom: 28px;
  }}

  .freshness {{
    display: flex; gap: 24px; flex-wrap: wrap; background: var(--surface-sunken); border: 1px solid var(--border);
    border-radius: 8px; padding: 14px 18px; margin-bottom: 32px; font-size: 0.85rem;
  }}
  .freshness dt {{ font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.03em; color: var(--text-faint); margin: 0; }}
  .freshness dd {{ margin: 2px 0 0; font-family: "JetBrains Mono", monospace; color: var(--text); }}

  section.block {{ margin-bottom: 32px; }}
  section.block h2 {{
    font-family: "JetBrains Mono", monospace; font-size: 0.95rem; font-weight: 700; margin: 0 0 12px;
    padding-bottom: 8px; border-bottom: 1px solid var(--border);
  }}
  .resource-list, .page-list {{ list-style: none; margin: 0; padding: 0; }}
  .resource-list {{ display: flex; flex-wrap: wrap; gap: 8px; }}
  .resource-list li a {{
    display: inline-block; padding: 6px 12px; border: 1px solid var(--border); border-radius: 999px;
    text-decoration: none; font-size: 0.85rem; font-weight: 500;
  }}
  .resource-list li a:hover {{ border-color: var(--accent); color: var(--accent); }}
  .page-list {{ columns: 2; column-gap: 24px; }}
  .page-list li {{ padding: 6px 0; border-bottom: 1px solid var(--border); break-inside: avoid; }}
  .page-list a {{ text-decoration: none; }}
  .page-list a:hover {{ text-decoration: underline; }}
  .empty-note {{ color: var(--text-faint); font-size: 0.88rem; }}
  @media (max-width: 600px) {{ .page-list {{ columns: 1; }} }}
</style>
</head>
<body>
<div class="page">
  <p class="eyebrow"><a href="../directory.html">design-system-directory</a> / systems</p>
  <div class="system-header">{favicon}<span class="org-label">{html.escape(org) if org else html.escape(domain)}</span></div>
  <h1>{html.escape(ds_name)}</h1>
  <p class="meta-line">{" · ".join(html.escape(m) for m in meta_parts)}</p>
  {routes_nav("system", prefix="../")}
  {thumb_html}

  <dl class="freshness">
    <div><dt>Site last updated</dt><dd>{format_http_date(entry.get("last_modified"))}</dd></div>
    <div><dt>Last checked here</dt><dd>{format_iso(entry.get("last_checked"))}</dd></div>
  </dl>

  <section class="block">
    <h2>Resources</h2>
    {render_resource_list(entry)}
  </section>

  <section class="block">
    <h2>Indexed pages</h2>
    {render_page_list(name_text, pages_index)}
  </section>
</div>
</body>
</html>
"""


def main() -> None:
    entries = indexed_only(load_systems())
    pages_index = load_pages_index()

    SYSTEMS_DIR.mkdir(exist_ok=True)
    for entry in entries:
        out_path = SYSTEMS_DIR / f"{slugify(entry['name'])}.html"
        out_path.write_text(render_system_page(entry, pages_index))

    print(f"Wrote {len(entries)} system detail pages to {SYSTEMS_DIR}/")


if __name__ == "__main__":
    main()
