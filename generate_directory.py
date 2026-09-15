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

from page_shell import CHECK_ICON_SVG, EXTERNAL_LINK_ICON_SVG, FONT_LINK, GRID_ICON_SVG, LIST_ICON_SVG, TOKENS_CSS, routes_nav
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
    noise for visitors. See check_crawl_health.py for where this goes instead."""
    return [
        e for e in entries
        if e.get("pages_indexed") is not None and e["pages_indexed"] < LOW_COVERAGE_THRESHOLD
    ]


def find_capped(entries: list[dict]) -> list[dict]:
    """The opposite problem to find_low_coverage(): systems whose crawl hit
    ingest.py's max_pages ceiling with more pages still queued (see
    ingest.py's crawl()) — real content that exists but wasn't indexed,
    rather than a failed/blocked crawl. Also maintainer-only, for the same
    reason find_low_coverage() is."""
    return [e for e in entries if e.get("hit_max_pages")]


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
    url = html.escape(urls[0])
    return f'<td class="cell cell-found"><a href="{url}" target="_blank" rel="noopener" title="{url}">{CHECK_ICON_SVG}</a></td>'


def found_count(entry: dict) -> int:
    resources = entry.get("resources") or {}
    return sum(1 for key, _ in COLUMNS if resources.get(key))


def render_name_block(entry: dict, link_to_detail: bool = False) -> str:
    org, ds_name = split_org_name(entry)
    favicon = favicon_html((entry.get("start_urls") or [None])[0])
    org_html = f'<span class="org">{html.escape(org)}</span>' if org else ""
    ds_name_html = html.escape(ds_name)
    if link_to_detail:
        detail_href = f"systems/{slugify(full_name(entry))}.html"
        ds_name_html = f'<a href="{detail_href}">{ds_name_html}</a>'
    return f'<div class="name-block">{favicon}<div class="name-text">{org_html}<span class="ds-name">{ds_name_html}</span></div></div>'


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
    n_found = found_count(entry)
    cells = "".join(render_cell(entry, key) for key, _ in COLUMNS)

    return f"""
    <tr id="{row_id}" data-name="{html.escape(name_text.lower())}" data-sort-name="{html.escape(ds_name.lower())}">
      <td class="name-cell">{render_name_block(entry, link_to_detail=True)}</td>
      {render_docs_cell(entry)}
      <td class="cell cell-pages" data-sort-value="{pages if pages is not None else -1}">{pages_text}</td>
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
    detail_href = f"systems/{slugify(name_text)}.html"

    thumb_html = ""
    if start_url:
        src = thumbnail_src(name_text, start_url)
        thumb_html = f'<img class="card-thumb" src="{src}" alt="" loading="lazy">'

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
  .name-cell {{ min-width: 200px; }}
  .name-block {{ display: flex; align-items: center; gap: 8px; }}
  .name-text {{ display: flex; flex-direction: column; line-height: 1.25; }}
  .org {{ font-size: 0.68rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; color: var(--text-faint); }}
  .ds-name {{ font-weight: 600; font-size: 0.92rem; }}
  .ds-name a {{ color: inherit; text-decoration: none; text-underline-offset: 2px; }}
  .ds-name a:hover {{ text-decoration: underline; }}
  .favicon {{ border-radius: 3px; flex: none; }}
  .meta {{ font-size: 0.78rem; color: var(--text-muted); margin-top: 3px; font-variant-numeric: tabular-nums; font-family: "JetBrains Mono", monospace; }}
  .cell {{ text-align: center; font-variant-numeric: tabular-nums; }}
  .cell-pages {{ font-family: "JetBrains Mono", monospace; color: var(--text-muted); }}
  /* A bare checkmark glyph doesn't read as clickable until you hover it —
     giving it a pill background (same visual language as .badge/.chip
     elsewhere) makes "this is a link" obvious at rest, not just on hover. */
  .cell-found a {{
    display: inline-flex; align-items: center; justify-content: center;
    color: var(--accent); background: var(--accent-soft); text-decoration: none;
    width: 26px; height: 22px; border-radius: 999px;
  }}
  .cell-found a:hover {{ background: var(--accent); color: #fff; }}
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
  .cell-found-count {{
    font-family: "JetBrains Mono", monospace; font-weight: 600; color: var(--text);
    position: sticky; right: 0; background: var(--surface);
    box-shadow: -6px 0 8px -8px rgba(15,23,42,0.25);
  }}
  th:last-child {{ position: sticky; right: 0; background: var(--surface); box-shadow: -6px 0 8px -8px rgba(15,23,42,0.25); }}
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
</script>
</body>
</html>
"""


if __name__ == "__main__":
    systems = indexed_only(load_systems())
    OUTPUT_FILE.write_text(render_page(systems))
    print(f"Wrote {OUTPUT_FILE} with {len(systems)} systems.")
