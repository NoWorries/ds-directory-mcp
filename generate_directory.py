"""
Renders systems.yaml into a static directory.html — a matrix of every indexed
design system against the resources discovered for it (GitHub, Storybook, Figma,
MCP server, agent instructions, etc.), plus pages-indexed count and any GitHub/npm
enrichment (license, stars, latest version), and a card gallery with screenshots.

Run after ingest.py --all, as part of the weekly reindex workflow. No live backend
needed to view the result — it's a self-contained static page.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from urllib.parse import urlparse

import yaml

from page_shell import (
    CHECK_ICON_SVG,
    CHEVRON_RIGHT_ICON_SVG,
    CLOSE_ICON_SVG,
    EXPAND_ICON_SVG,
    EXTERNAL_LINK_ICON_SVG,
    FONT_LINK,
    GRID_ICON_SVG,
    LIST_ICON_SVG,
    TOKENS_CSS,
    routes_nav,
)
from slug import slugify
from text_utils import full_name, split_org_name

SYSTEMS_REGISTRY = Path(__file__).parent / "systems.yaml"
OUTPUT_FILE = Path(__file__).parent / "directory.html"
SCREENSHOTS_DIR = Path(__file__).parent / "screenshots"
PAGES_INDEX_FILE = Path(__file__).parent / "pages_index.json"

# A system with fewer indexed pages than this either genuinely has a tiny docs
# site, or (far more often) the crawl failed to get real content — blocked by
# robots.txt, a JS-rendered SPA our plain requests+BeautifulSoup crawler can't
# see through, a redirect loop, or a start_url that needs include_patterns/
# max_pages tuning. Flagged rather than shown at face value either way.
LOW_COVERAGE_THRESHOLD = 5

# Columns shown in the matrix, in order, mapped to the resources.py keys.
COLUMNS = [
    ("github", "GitHub"),
    ("npm", "npm"),
    ("storybook", "Storybook"),
    ("figma", "Figma"),
    ("icons", "Icons"),
    ("markdown_docs", "Markdown docs"),
    ("mcp", "MCP server"),
    ("skills", "Agent skill"),
    ("agent_instructions", "Agent instructions"),
]

# More AI-affordance resource.py keys (see WELL_KNOWN_PATHS/RESOURCE_PATTERNS
# there — same taxonomy the State of AI in Design Systems survey uses:
# https://state-of-ai-in-design-systems.netlify.app/), deliberately kept out
# of the summary matrix (COLUMNS above) rather than adding yet more columns
# to an already-wide table every visitor scrolls past — shown instead on each
# system's own detail page (generate_systems.py's render_resource_list, which
# iterates COLUMNS + DETAIL_ONLY_COLUMNS) alongside the resources that already
# live there.
DETAIL_ONLY_COLUMNS = [
    ("copilot_instructions", "Copilot instructions"),
    ("cursor_rules", "Cursor rules"),
    ("registry", "Component registry"),
]


def load_systems() -> list[dict]:
    with open(SYSTEMS_REGISTRY) as f:
        return yaml.safe_load(f) or []


def load_pages_index() -> dict:
    if not PAGES_INDEX_FILE.exists():
        return {}
    return json.loads(PAGES_INDEX_FILE.read_text())


def indexed_only(entries: list[dict]) -> list[dict]:
    """Systems that actually have something to show. Excludes both:
    - never crawled yet (pages_indexed is None) — "not yet indexed" is
      internal pipeline state, not something a visitor should see;
    - crawled but found nothing (pages_indexed == 0) — usually a JS-rendered
      site the crawler can't see through, a robots.txt block, or a start_url
      that needs tuning (see find_low_coverage()), not a system that
      genuinely has zero documentation. A public "0 pages indexed" card
      reads as broken, not as "nothing here yet."

    Either way they stay in systems.yaml (so `ingest.py --new`/a future full
    recrawl still picks them up) and still get reported to the maintainer —
    see check_crawl_health.py, which runs on the raw unfiltered registry —
    they just don't render anywhere on the public site until real content is
    found."""
    return [e for e in entries if e.get("pages_indexed")]


def compute_stats(entries: list[dict]) -> dict:
    """Homepage stats: how much is actually indexed, right now."""
    total_pages = sum(e.get("pages_indexed") or 0 for e in entries)
    resource_counts = {key: 0 for key, _ in COLUMNS}
    for e in entries:
        resources = e.get("resources") or {}
        for key, _ in COLUMNS:
            if resources.get(key):
                resource_counts[key] += 1
    checked_values = [e["last_checked"] for e in entries if e.get("last_checked")]
    return {
        "total_systems": len(entries),
        "total_pages": total_pages,
        "resource_counts": resource_counts,
        "last_checked": max(checked_values) if checked_values else None,
    }


def find_low_coverage(entries: list[dict]) -> list[dict]:
    """Systems that look like their crawl failed or was incomplete. Used by
    check_crawl_health.py to notify the maintainer — deliberately NOT surfaced
    on the public directory page, which stays clean of internal crawl-health
    noise for visitors. See check_crawl_health.py for where this goes instead.

    Excludes anything find_broken() already caught (crawl_error set) — a
    system whose start URL couldn't be fetched at all is a different, more
    specific problem than "crawled fine but found few pages," and would
    otherwise show up in both sections of the health report for the same
    underlying issue."""
    return [
        e for e in entries
        if e.get("pages_indexed") is not None
        and e["pages_indexed"] < LOW_COVERAGE_THRESHOLD
        and not e.get("crawl_error")
    ]


def find_capped(entries: list[dict]) -> list[dict]:
    """The opposite problem to find_low_coverage(): systems whose crawl hit
    ingest.py's max_pages ceiling with more pages still queued (see
    ingest.py's crawl()) — real content that exists but wasn't indexed,
    rather than a failed/blocked crawl. Also maintainer-only, for the same
    reason find_low_coverage() is."""
    return [e for e in entries if e.get("hit_max_pages")]


def find_unverified_resources(entries: list[dict]) -> list[dict]:
    """Systems with at least one discovered github/npm link whose name
    doesn't obviously relate to the system itself (see
    resources.flag_unverified_resources()) — e.g. a build-tool dependency
    swept up from a footer/credits link rather than the system's own repo.
    Maintainer-only: needs a human to actually look at the link and decide,
    not something a public "?" badge can resolve on its own."""
    return [e for e in entries if e.get("unverified_resources")]


def find_broken(entries: list[dict]) -> list[dict]:
    """Systems whose start URL couldn't even be fetched (DNS failure,
    connection refused, timeout, etc — see crawl()'s start_url_error in
    ingest.py) — usually means the site moved, was renamed, or is down,
    rather than the milder "crawled fine but found little" case
    find_low_coverage() covers. Also maintainer-only, for the same reason."""
    return [e for e in entries if e.get("crawl_error")]


COVERAGE_GLYPH = {"full": "●", "partial": "◐", "unknown": "○"}
COVERAGE_LABEL = {
    "full": "Crawl completed on its own — this is everything the crawler found",
    "partial": "Incomplete — hit the max_pages ceiling with more still queued, or coverage looks thin",
    "unknown": "Little to nothing indexed yet",
}


def coverage_status(entry: dict) -> str:
    """How complete our crawl of this system looks, based only on what the
    crawler itself could determine — never a guarantee the site's actual
    docs are fully covered, just the best signal available:

    - "full": the crawl ran to completion (didn't hit max_pages) with a
      healthy page count — nothing indicates more was left undiscovered.
    - "partial": something real was indexed, but it's known-incomplete —
      either hit_max_pages (more pages were queued when the crawl stopped)
      or the page count is suspiciously thin (find_low_coverage()'s
      threshold) without a harder failure explaining why.
    - "unknown": effectively nothing indexed (also excluded from every
      public page by indexed_only(), so this state is only ever seen
      internally/in the health report, never on the site itself).
    """
    pages = entry.get("pages_indexed")
    if not pages:
        return "unknown"
    if entry.get("hit_max_pages") or pages < LOW_COVERAGE_THRESHOLD:
        return "partial"
    return "full"


def favicon_html(start_url: str | None) -> str:
    domain = urlparse(start_url).netloc if start_url else ""
    if not domain:
        return ""
    return (
        f'<img class="favicon" src="https://www.google.com/s2/favicons?domain={html.escape(domain)}&sz=32" '
        f'alt="" width="16" height="16" loading="lazy">'
    )


def render_cell(entry: dict, key: str) -> str:
    urls = (entry.get("resources") or {}).get(key)
    if not urls:
        return '<td class="cell cell-none" title="None found">—</td>'
    # Anything flagged as possibly unrelated (resources.flag_unverified_resources)
    # isn't shown any differently here — it's still a real discovered link,
    # just one a maintainer should double-check. That review happens via the
    # crawl-health GitHub issue (see check_crawl_health.py), not a visible
    # marker on the public site, so a visitor never has to interpret an
    # unexplained "?" badge.
    if len(urls) == 1:
        primary = html.escape(urls[0])
        return f'<td class="cell cell-found"><a href="{primary}" target="_blank" rel="noopener" title="{primary}">{CHECK_ICON_SVG}</a></td>'

    # More than one link classified under this resource type (e.g. two npm
    # packages) — there's no single "the" link to open directly, so this
    # goes to the system's own detail page (which lists every one of them
    # under Resources) instead of silently picking urls[0] and hiding the
    # rest. The count badge signals there's more than one before you click.
    detail_href = f'systems/{slugify(full_name(entry))}#resources'
    return (
        f'<td class="cell cell-found cell-multi">'
        f'<a href="{detail_href}" title="{len(urls)} {key} links found — view all on the system page">'
        f'{CHECK_ICON_SVG}<span class="cell-count">{len(urls)}</span></a></td>'
    )


def found_count(entry: dict) -> int:
    resources = entry.get("resources") or {}
    return sum(1 for key, _ in COLUMNS if resources.get(key))


def render_name_block(entry: dict, link_to_detail: bool = False) -> str:
    org, ds_name = split_org_name(entry)
    favicon = favicon_html((entry.get("start_urls") or [None])[0])
    org_html = f'<span class="org">{html.escape(org)}</span>' if org else ""
    ds_name_html = html.escape(ds_name)
    name_block = f'<div class="name-block">{favicon}<div class="name-text">{org_html}<span class="ds-name">{ds_name_html}</span></div></div>'

    if not link_to_detail:
        return name_block

    # The whole cell is one tap target (not just the name text) — a real
    # <a href> so modifier-click/middle-click/right-click still work
    # normally; the JS at the bottom of render_page() intercepts a plain left
    # click to open the side panel instead of navigating away. The chevron
    # signals "this reveals nested content", matching that panel behavior.
    slug = slugify(full_name(entry))
    detail_href = f"systems/{slug}"
    return (
        f'<a class="name-cell-link" href="{detail_href}" data-slug="{slug}" data-name="{html.escape(full_name(entry))}">'
        f'{name_block}<span class="name-chevron">{CHEVRON_RIGHT_ICON_SVG}</span></a>'
    )


def render_docs_cell(entry: dict) -> str:
    start_url = (entry.get("start_urls") or [None])[0]
    if not start_url:
        return '<td class="cell cell-docs cell-none">—</td>'
    url = html.escape(start_url)
    return (
        f'<td class="cell cell-docs">'
        f'<a href="{url}" target="_blank" rel="noopener" title="Open {url}" aria-label="Open external docs site">'
        f"{EXTERNAL_LINK_ICON_SVG}</a></td>"
    )


def render_row(entry: dict) -> str:
    name_text = full_name(entry)
    _org, ds_name = split_org_name(entry)
    row_id = slugify(name_text)
    pages = entry.get("pages_indexed")
    pages_text = "—" if pages is None else str(pages)
    status = coverage_status(entry)
    n_found = found_count(entry)
    cells = "".join(render_cell(entry, key) for key, _ in COLUMNS)

    return f"""
    <tr id="{row_id}" data-name="{html.escape(name_text.lower())}" data-sort-name="{html.escape(ds_name.lower())}">
      <td class="name-cell">{render_name_block(entry, link_to_detail=True)}</td>
      {render_docs_cell(entry)}
      <td class="cell cell-pages" data-sort-value="{pages if pages is not None else -1}">
        <span class="pages-value"><span class="coverage-dot coverage-{status}" title="{html.escape(COVERAGE_LABEL[status])}">{COVERAGE_GLYPH[status]}</span>{pages_text}</span>
      </td>
      {cells}
      <td class="cell cell-found-count" data-sort-value="{n_found}">{n_found}/{len(COLUMNS)}</td>
    </tr>
    """


def thumbnail_src(name: str, start_url: str) -> str:
    stored = SCREENSHOTS_DIR / f"{slugify(name)}.jpg"
    if stored.exists():
        return f"screenshots/{stored.name}"
    # Not fetched into the repo yet (fetch_screenshots.py runs on a slow,
    # separate cadence) — fall back to a live fetch so new systems aren't blank.
    return f"https://image.thum.io/get/width/320/{start_url}"


def render_card(entry: dict) -> str:
    """A lightweight preview only — thumbnail, name, and a page count. The
    whole card is the link through to the system's own detail page for
    everything else (resources, freshness, full page list) — no separate
    "view details" line needed. Cards are a preview, not a second copy of the
    full detail, so the resource-found count stays in the table view only."""
    name_text = full_name(entry)
    start_url = (entry.get("start_urls") or [None])[0]
    detail_href = f"systems/{slugify(name_text)}"

    slug = slugify(name_text)
    thumb_html = ""
    if start_url:
        src = thumbnail_src(name_text, start_url)
        # Shared view-transition-name with the detail page's .hero-thumb —
        # see the comment on render_name_block's title transition above.
        thumb_html = f'<img class="card-thumb" src="{src}" alt="" loading="lazy" style="view-transition-name: thumb-{slug}; view-transition-class: thumb">'

    pages = entry.get("pages_indexed", 0)

    return f"""
    <a class="card" href="{detail_href}" data-name="{html.escape(name_text.lower())}">
      {thumb_html}
      <div class="card-body textured">
        {render_name_block(entry)}
        <div class="meta">{pages} pages indexed</div>
      </div>
    </a>
    """


def render_page(entries: list[dict]) -> str:
    entries_sorted = sorted(entries, key=lambda e: full_name(e).lower())

    # Column indices: 0 = name (text sort), 1 = docs link (not sortable),
    # 2 = pages (number), 3..3+len(COLUMNS)-1 = resource dots (found),
    # last = found-count (number, pinned).
    header_cells = "".join(
        f'<th data-col="{i + 3}" data-sort="found">{label}</th>' for i, (_, label) in enumerate(COLUMNS)
    )
    last_col = len(COLUMNS) + 3
    rows = "".join(render_row(e) for e in entries_sorted)
    cards = "".join(render_card(e) for e in entries_sorted)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Design Systems Directory</title>
{FONT_LINK}
<style>
{TOKENS_CSS}
  /* .page (eyebrow/heading/subtitle) deliberately stays at the shared
     PAGE_MAX_WIDTH, same as every other page — no reason for the intro here
     to look different from the rest of the site. Only the table/grid itself
     needs more room (with the full ~250-system registry and 9 resource
     columns, it wants noticeably more than this sample data's 4 rows show),
     so it lives in its own .table-breakout below: a separate block, centered
     independently at a wider FIXED cap. Fixed (not shrink-to-fit) on purpose
     — an earlier version sized this to the table's own content width, which
     meant the toolbar (and the List/Grid toggle inside it) physically moved
     between the two views, since Grid view has no table to size against.
     A constant width means the toggle sits in the same place either way. */
  .table-breakout {{ max-width: 1600px; margin: 0 auto; }}
  .table-toolbar {{ display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; margin-bottom: 8px; }}
  #tableFilter {{
    padding: 8px 12px; font: inherit; font-size: 0.85rem; border: 1px solid var(--border);
    border-radius: 6px; width: 280px; max-width: 100%; background: var(--surface); color: var(--text);
  }}
  #tableFilter:focus {{ outline: 2px solid var(--accent); outline-offset: 1px; }}

  .view-toggle {{ display: flex; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }}
  .view-toggle button {{
    display: flex; align-items: center; gap: 6px;
    font: inherit; font-size: 0.82rem; font-weight: 600; padding: 7px 14px; border: none; cursor: pointer;
    background: var(--surface); color: var(--text-muted);
  }}
  .view-toggle button svg {{ flex: none; }}
  .view-toggle button + button {{ border-left: 1px solid var(--border); }}
  .view-toggle button.current {{ background: var(--accent-soft); color: var(--accent); }}

  /* Classic CSS-only "scroll shadow": two background-attachment:local layers
     scroll WITH the content (so they only show at the true start/end, never
     appearing over content past the very edges) and two background-attachment:
     scroll shadow layers stay fixed to the viewport — the shadow only becomes
     visible once the local layer has scrolled out from under it, i.e. exactly
     when there's more table to the left/right than currently visible. */
  .table-wrap {{
    overflow-x: auto; border: 1px solid var(--border); border-radius: 10px;
    box-shadow: var(--shadow);
    background-color: var(--surface);
    background-image:
      linear-gradient(to right, var(--surface) 30%, rgba(0,0,0,0)),
      linear-gradient(to left, var(--surface) 30%, rgba(0,0,0,0)),
      linear-gradient(to right, rgba(15,23,42,0.15), rgba(15,23,42,0)),
      linear-gradient(to left, rgba(15,23,42,0.15), rgba(15,23,42,0));
    background-position: left center, right center, left center, right center;
    background-repeat: no-repeat;
    background-size: 24px 100%, 24px 100%, 10px 100%, 10px 100%;
    background-attachment: local, local, scroll, scroll;
  }}
  table {{ border-collapse: collapse; width: 100%; font-size: 0.86rem; }}
  th {{
    text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--border); font-weight: 600;
    white-space: nowrap; color: var(--text-muted); font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.03em;
    font-family: "JetBrains Mono", monospace;
  }}
  td {{ padding: 10px 12px; border-bottom: 1px solid var(--border); vertical-align: middle; }}
  /* Skips layout/paint for off-screen rows entirely — the load-bearing fix for
     hundreds of rows staying cheap without pagination or virtualization JS.
     contain-intrinsic-size is a rough guess at row height so the page doesn't
     jump around as rows are measured for the first time while scrolling. */
  tbody tr {{ content-visibility: auto; contain-intrinsic-size: auto 46px; }}
  tbody tr:last-child td {{ border-bottom: none; }}
  .name-cell {{ min-width: 200px; padding: 0; }}
  /* The whole column is one tap target (see render_name_block) — the <a>
     fills the cell itself (hence padding moved here from .name-cell) so
     there's no dead strip of cell around the name text that looks
     clickable but isn't. */
  .name-cell-link {{
    display: flex; align-items: center; justify-content: space-between; gap: 8px;
    padding: 10px 12px; color: inherit; text-decoration: none;
  }}
  .name-cell-link:hover {{ background: var(--surface-sunken); }}
  .name-cell-link:hover .name-chevron {{ color: var(--accent); transform: translateX(2px); }}
  .name-chevron {{ flex: none; display: flex; color: var(--text-faint); transition: transform 0.12s, color 0.12s; }}
  .name-block {{ display: flex; align-items: center; gap: 8px; min-width: 0; }}
  .name-text {{ display: flex; flex-direction: column; line-height: 1.25; min-width: 0; }}
  .org {{ font-size: 0.68rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; color: var(--text-faint); }}
  .ds-name {{ font-weight: 600; font-size: 0.92rem; }}
  .favicon {{ border-radius: 3px; flex: none; }}
  .meta {{ font-size: 0.78rem; color: var(--text-muted); margin-top: 3px; font-variant-numeric: tabular-nums; font-family: "JetBrains Mono", monospace; }}
  .cell {{ text-align: center; font-variant-numeric: tabular-nums; }}
  .cell-pages {{ font-family: "JetBrains Mono", monospace; color: var(--text-muted); text-align: left; }}
  /* coverage_status()'s three states — never shown as "unknown" here since
     indexed_only() already excludes those entries from every public page,
     but the rule stays complete for consistency with check_crawl_health.py. */
  .pages-value {{ display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }}
  .coverage-dot {{ font-size: 1.05rem; line-height: 1; }}
  .coverage-dot.coverage-full {{ color: #22c55e; }}
  .coverage-dot.coverage-partial {{ color: #f59e0b; }}
  .coverage-dot.coverage-unknown {{ color: var(--text-faint); }}
  /* A bare checkmark glyph doesn't read as clickable until you hover it —
     giving it a pill background (same visual language as .badge/.chip
     elsewhere) makes "this is a link" obvious at rest, not just on hover. */
  .cell-found a {{
    display: inline-flex; align-items: center; justify-content: center;
    color: var(--accent); background: var(--accent-soft); text-decoration: none;
    width: 26px; height: 22px; border-radius: 999px;
  }}
  .cell-found a:hover {{ background: var(--accent); color: #fff; }}
  /* Small count badge for when more than one link was classified under the
     same resource type (e.g. two npm packages) — sits at the pill's corner
     so it reads as "there's more here" without needing its own column. */
  .cell-multi a {{ position: relative; }}
  .cell-count {{
    position: absolute; top: -5px; right: -5px; min-width: 14px; height: 14px; padding: 0 3px;
    display: inline-flex; align-items: center; justify-content: center; border-radius: 999px;
    background: var(--accent); color: #fff; font-size: 0.6rem; font-weight: 700; line-height: 1;
    font-family: "JetBrains Mono", monospace;
  }}
  /* Distinct from the name link (which goes to this system's own detail
     page): same pill treatment, but muted rather than accent-colored so it
     doesn't read as another "resource found" indicator — this one always
     goes straight out to the system's real docs site. */
  .cell-docs a {{
    display: inline-flex; align-items: center; justify-content: center;
    color: var(--text-muted); background: var(--surface-sunken); text-decoration: none;
    width: 26px; height: 22px; border-radius: 999px; border: 1px solid var(--border);
  }}
  .cell-docs a:hover {{ color: var(--accent); border-color: var(--accent); }}
  .cell-none {{ color: var(--text-faint); }}
  /* Both edge columns are always pinned (sticky) — the shadow that signals
     "there's scrolled content hidden under here" is the part that's
     conditional, toggled by JS only when the table actually overflows its
     container (see the has-overflow class set near the bottom of the page's
     script). Without that, every row showed a shadow implying hidden
     content even on a table narrow enough to need no scrolling at all. */
  .name-cell, th[data-col="0"] {{ position: sticky; left: 0; background: var(--surface); z-index: 2; }}
  .cell-found-count, th:last-child {{ position: sticky; right: 0; background: var(--surface); z-index: 2; }}
  .table-wrap.has-overflow .name-cell,
  .table-wrap.has-overflow th[data-col="0"] {{ box-shadow: 6px 0 8px -8px rgba(15,23,42,0.25); }}
  .table-wrap.has-overflow .cell-found-count,
  .table-wrap.has-overflow th:last-child {{ box-shadow: -6px 0 8px -8px rgba(15,23,42,0.25); }}
  .badge {{
    background: var(--accent-soft); color: var(--accent-soft-text); border-radius: 4px; padding: 1px 6px;
    font-size: 0.72rem; font-family: "JetBrains Mono", monospace; font-weight: 500;
  }}
  th[data-col] {{ cursor: pointer; user-select: none; }}
  th[data-col]:hover {{ color: var(--accent); }}
  th.sorted-asc::after {{ content: " ▲"; font-size: 0.65em; }}
  th.sorted-desc::after {{ content: " ▼"; font-size: 0.65em; }}

  /* [hidden] must win over this class's own "display: grid" — an author-origin
     rule always overrides the UA stylesheet's hidden-implies-none regardless
     of source order, so without this override toggling hidden does nothing. */
  .cards-grid[hidden] {{ display: none; }}
  .cards-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 14px; }}
  /* Same fix as .cards-grid above, needed again here: .card sets its own
     "display: flex" below, which otherwise wins over [hidden] and breaks the
     per-card name-filter (the row-level version never needed this because tr
     doesn't set an explicit display of its own). */
  .card[hidden] {{ display: none; }}
  .card {{
    background: var(--surface); border: 1px solid var(--border); border-radius: 8px; overflow: hidden;
    box-shadow: var(--shadow); display: flex; flex-direction: column;
    content-visibility: auto; contain-intrinsic-size: auto 220px;
    text-decoration: none; color: inherit; transition: border-color 0.15s;
  }}
  .card:hover {{ border-color: var(--accent); }}
  .card .name-block {{ align-items: flex-start; }}
  .card-thumb {{
    width: 100%; aspect-ratio: 16 / 9; object-fit: cover; object-position: top;
    background: var(--surface-sunken); display: block;
  }}
  .card-body {{ padding: 14px; }}

  /* --- Detail side panel: lets a visitor preview a system without leaving
     the list (see the name-cell-link click handler below). Its content is
     the same systems/<slug>.html page, fetched and injected — not a second
     template to keep in sync — so it always matches the standalone page;
     "Open full page" just navigates there normally (and gets the .thumb
     view-transition morph on the way in, same as any other link to it). */
  .detail-panel-overlay {{ position: fixed; inset: 0; background: rgba(15,23,42,0.35); z-index: 199; }}
  .detail-panel-overlay[hidden] {{ display: none; }}
  .detail-panel {{
    position: fixed; top: 0; right: 0; height: 100%; width: min(480px, 92vw); z-index: 200;
    background: var(--surface); border-left: 1px solid var(--border);
    box-shadow: -12px 0 30px -12px rgba(15,23,42,0.3);
    transform: translateX(100%); transition: transform 0.22s ease;
    display: flex; flex-direction: column;
  }}
  .detail-panel.open {{ transform: translateX(0); }}
  .detail-panel-header {{
    display: flex; align-items: center; justify-content: space-between; gap: 12px; flex: none;
    padding: 14px 20px; border-bottom: 1px solid var(--border);
  }}
  .detail-panel-expand {{
    display: inline-flex; align-items: center; gap: 6px; font-size: 0.85rem; font-weight: 600;
    color: var(--accent); text-decoration: none;
  }}
  .detail-panel-expand:hover {{ text-decoration: underline; }}
  .detail-panel-close {{
    background: none; border: none; padding: 6px; margin: -6px; color: var(--text-muted); cursor: pointer;
    border-radius: 6px; display: flex;
  }}
  .detail-panel-close:hover {{ background: var(--surface-sunken); color: var(--text); }}
  .detail-panel-body {{ flex: 1; overflow-y: auto; padding: 8px 24px 32px; }}
  /* The injected content is the detail page's own `.page` div, which is
     normally centered at a max-width for a full page — inside the narrower
     panel it should just fill the available width instead. */
  .detail-panel-body .page {{ max-width: none; margin: 0; }}
  .detail-panel-body .page-list {{ columns: 1; }}
  @media (max-width: 640px) {{ .detail-panel {{ width: 100vw; }} }}
</style>
</head>
<body>
{routes_nav("directory")}
<div class="page">
  <p class="eyebrow">All Systems</p>
  <h1>Every indexed design system</h1>
  <p class="subtitle">{len(entries_sorted)} external design systems, cross-referenced by the resources each one has published — GitHub, Storybook, Figma, tokens, and more. Regenerated weekly.</p>
</div>

<div class="table-breakout">
  <div class="table-toolbar">
    <input id="tableFilter" type="text" placeholder="Filter by system or company name...">
    <div class="view-toggle" id="viewToggle" role="group" aria-label="View">
      <button type="button" data-view="list" class="current">{LIST_ICON_SVG} List</button>
      <button type="button" data-view="grid">{GRID_ICON_SVG} Grid</button>
    </div>
  </div>

  <div class="table-wrap" id="listView">
  <table id="directoryTable">
    <thead>
      <tr>
        <th data-col="0" data-sort="text">System</th>
        <th>Docs</th>
        <th data-col="2" data-sort="number">Pages</th>
        {header_cells}
        <th data-col="{last_col}" data-sort="number">Found</th>
      </tr>
    </thead>
    <tbody>
      {rows}
    </tbody>
  </table>
  </div>

  <div class="cards-grid" id="cardsGrid" hidden>
    {cards}
  </div>
</div>

<div class="detail-panel-overlay" id="detailPanelOverlay" hidden></div>
<aside class="detail-panel" id="detailPanel" aria-hidden="true">
  <div class="detail-panel-header">
    <a class="detail-panel-expand" id="detailPanelExpand" href="#">{EXPAND_ICON_SVG} Open full page</a>
    <button type="button" class="detail-panel-close" id="detailPanelClose" aria-label="Close">{CLOSE_ICON_SVG}</button>
  </div>
  <div class="detail-panel-body" id="detailPanelBody"></div>
</aside>

<script>
  // --- Table sort ---
  let currentSort = {{ col: null, dir: 1 }};

  function cellValue(row, col, type) {{
    // Sorting the System column by the visible design-system name (e.g.
    // "Mozaic Design System"), not by row.dataset.name — that's the "Org —
    // Name" identity string used for the name-filter box, and sorting by it
    // instead would order rows by the org prefix, which isn't what's visually
    // prominent in the name cell and made the sort look wrong/arbitrary.
    if (col === 0) return row.dataset.sortName || "";
    const cell = row.children[col];
    if (!cell) return "";
    if (type === "found") return cell.classList.contains("cell-found") ? 1 : 0;
    if (type === "number") return parseFloat(cell.dataset.sortValue ?? cell.textContent) || 0;
    return (cell.textContent || "").trim().toLowerCase();
  }}

  function sortTable(th) {{
    const col = parseInt(th.dataset.col, 10);
    const type = th.dataset.sort;
    const tbody = document.querySelector("#directoryTable tbody");
    const rows = Array.from(tbody.querySelectorAll("tr"));

    const dir = (currentSort.col === col && currentSort.dir === 1) ? -1 : 1;
    currentSort = {{ col, dir }};

    rows.sort((a, b) => {{
      const va = cellValue(a, col, type);
      const vb = cellValue(b, col, type);
      if (va < vb) return -1 * dir;
      if (va > vb) return 1 * dir;
      return 0;
    }});
    rows.forEach(row => tbody.appendChild(row));

    document.querySelectorAll("#directoryTable th[data-col]").forEach(h => h.classList.remove("sorted-asc", "sorted-desc"));
    th.classList.add(dir === 1 ? "sorted-asc" : "sorted-desc");
  }}

  document.querySelectorAll("#directoryTable th[data-col]").forEach(th => {{
    th.addEventListener("click", () => sortTable(th));
  }});

  // --- Name filter (plain substring match, separate from the semantic search above) —
  // applies to both the table and the card gallery below it, since they show the same data.
  const tableFilter = document.getElementById("tableFilter");
  tableFilter.addEventListener("input", () => {{
    const query = tableFilter.value.trim().toLowerCase();
    document.querySelectorAll("#directoryTable tbody tr").forEach(row => {{
      row.hidden = Boolean(query) && !row.dataset.name.includes(query);
    }});
    document.querySelectorAll("#cardsGrid .card").forEach(card => {{
      card.hidden = Boolean(query) && !card.dataset.name.includes(query);
    }});
  }});

  // --- List/Grid view toggle (remembers choice per viewer via localStorage) ---
  const viewToggle = document.getElementById("viewToggle");
  const listView = document.getElementById("listView");
  const gridView = document.getElementById("cardsGrid");

  function setView(view) {{
    listView.hidden = view !== "list";
    gridView.hidden = view !== "grid";
    viewToggle.querySelectorAll("button").forEach(b => b.classList.toggle("current", b.dataset.view === view));
    try {{ localStorage.setItem("ds-directory-view", view); }} catch (err) {{ /* private browsing etc. — fine to skip */ }}
  }}

  viewToggle.addEventListener("click", (e) => {{
    const button = e.target.closest("button[data-view]");
    if (button) setView(button.dataset.view);
  }});

  let savedView = "list";
  try {{ savedView = localStorage.getItem("ds-directory-view") || "list"; }} catch (err) {{ /* fine */ }}
  setView(savedView);

  // --- Pinned-column shadows: only shown when the table actually overflows
  // its container (scrollWidth > clientWidth) — a table narrow enough to
  // need no horizontal scrolling has nothing hidden under the pinned edges,
  // so showing the shadow there would be a false "there's more, scroll" cue.
  const tableWrapEl = document.getElementById("listView");
  function updateStickyShadow() {{
    if (!tableWrapEl) return;
    tableWrapEl.classList.toggle("has-overflow", tableWrapEl.scrollWidth > tableWrapEl.clientWidth + 1);
  }}
  updateStickyShadow();
  window.addEventListener("resize", updateStickyShadow);

  // --- Detail side panel: previews a system's systems/<slug>.html page
  // in-place, without leaving the list. Fetches and injects that same page's
  // .page content (plus its <style> block, once) rather than keeping a
  // second copy of the template, so the panel always matches the real page.
  const detailPanel = document.getElementById("detailPanel");
  const detailPanelOverlay = document.getElementById("detailPanelOverlay");
  const detailPanelBody = document.getElementById("detailPanelBody");
  const detailPanelExpand = document.getElementById("detailPanelExpand");
  const detailPanelClose = document.getElementById("detailPanelClose");
  let detailStylesInjected = false;

  async function openDetailPanel(slug) {{
    // Two different URLs on purpose: the visible "Open full page" link is
    // the clean, extension-less path (matches every other in-page link —
    // see netlify.toml's pretty_urls), while the fetch() below hits the
    // real .html file directly rather than depending on Netlify's pretty-url
    // rewriting also applying to a same-page XHR.
    detailPanelExpand.href = "systems/" + slug;
    const fetchHref = "systems/" + slug + ".html";
    detailPanel.classList.add("open");
    detailPanel.setAttribute("aria-hidden", "false");
    detailPanelOverlay.hidden = false;
    detailPanelBody.innerHTML = '<p class="empty-note">Loading…</p>';

    try {{
      const response = await fetch(fetchHref);
      const doc = new DOMParser().parseFromString(await response.text(), "text/html");

      if (!detailStylesInjected) {{
        const styleTag = doc.querySelector("style");
        if (styleTag) {{
          const injected = document.createElement("style");
          injected.textContent = styleTag.textContent;
          document.head.appendChild(injected);
        }}
        detailStylesInjected = true;
      }}

      const pageEl = doc.querySelector(".page");
      if (!pageEl) throw new Error("no .page content");

      // Relative image paths (e.g. "screenshots/x.jpg") resolve against
      // systems/<slug>.html there — rewrite them to work from this page's
      // own location instead. Absolute/protocol-relative/data URLs are left
      // alone.
      pageEl.querySelectorAll("img[src]").forEach((img) => {{
        const src = img.getAttribute("src");
        if (src && !/^([a-z]+:)?\/\//i.test(src) && !src.startsWith("data:") && !src.startsWith("/")) {{
          img.setAttribute("src", "systems/" + src);
        }}
      }});
      // Redundant inside the panel — the "All Systems" breadcrumb is what
      // the close button already does.
      const eyebrow = pageEl.querySelector(".eyebrow");
      if (eyebrow) eyebrow.remove();

      detailPanelBody.innerHTML = pageEl.innerHTML;
    }} catch (err) {{
      detailPanelBody.innerHTML = '<p class="empty-note">Couldn\\'t load this system\\'s details.</p>';
    }}
  }}

  function closeDetailPanel() {{
    detailPanel.classList.remove("open");
    detailPanel.setAttribute("aria-hidden", "true");
    detailPanelOverlay.hidden = true;
  }}

  document.querySelectorAll(".name-cell-link").forEach((link) => {{
    link.addEventListener("click", (e) => {{
      // Only intercept a plain left click — modifier/middle/right clicks keep
      // their normal browser behavior (open in new tab, etc.) via the real href.
      if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      e.preventDefault();
      openDetailPanel(link.dataset.slug);
    }});
  }});

  detailPanelClose.addEventListener("click", closeDetailPanel);
  detailPanelOverlay.addEventListener("click", closeDetailPanel);
  document.addEventListener("keydown", (e) => {{
    if (e.key === "Escape" && detailPanel.classList.contains("open")) closeDetailPanel();
  }});
</script>
</body>
</html>
"""


if __name__ == "__main__":
    systems = indexed_only(load_systems())
    OUTPUT_FILE.write_text(render_page(systems))
    print(f"Wrote {OUTPUT_FILE} with {len(systems)} systems.")
