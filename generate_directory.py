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

from page_shell import FONT_LINK, TOKENS_CSS, routes_nav
from slug import slugify

SYSTEMS_REGISTRY = Path(__file__).parent / "systems.yaml"
OUTPUT_FILE = Path(__file__).parent / "directory.html"
SCREENSHOTS_DIR = Path(__file__).parent / "screenshots"
PAGES_INDEX_FILE = Path(__file__).parent / "pages_index.json"

# Update if the Render service URL ever changes.
MCP_URL = "https://designsystems.onrender.com/mcp"
SEARCH_API_URL = "https://designsystems.onrender.com/search"
MCP_INSTALL_COMMAND = f"claude mcp add ds-directory --transport http {MCP_URL}"

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
    """Systems registered in systems.yaml but never actually crawled yet
    (pages_indexed is None) don't get a public page — "not yet indexed" is
    internal pipeline state, not something a visitor should see. They stay in
    systems.yaml so `ingest.py --new` still picks them up; they just don't
    render anywhere on the site until that happens."""
    return [e for e in entries if e.get("pages_indexed") is not None]


def find_low_coverage(entries: list[dict]) -> list[dict]:
    """Systems that look like their crawl failed or was incomplete. Used by
    check_crawl_health.py to notify the maintainer — deliberately NOT surfaced
    on the public directory page, which stays clean of internal crawl-health
    noise for visitors. See check_crawl_health.py for where this goes instead."""
    return [
        e for e in entries
        if e.get("pages_indexed") is not None and e["pages_indexed"] < LOW_COVERAGE_THRESHOLD
    ]


def split_org_name(name: str) -> tuple[str, str]:
    """"Adobe — Spectrum" -> ("Adobe", "Spectrum"). Falls back to no org label
    if the registry entry doesn't use the " — " separator (e.g. hand-added)."""
    if " — " in name:
        org, ds_name = name.split(" — ", 1)
        return org.strip(), ds_name.strip()
    return "", name


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
    return f'<td class="cell cell-found"><a href="{url}" target="_blank" rel="noopener" title="{url}">●</a></td>'


def found_count(entry: dict) -> int:
    resources = entry.get("resources") or {}
    return sum(1 for key, _ in COLUMNS if resources.get(key))


def render_name_block(entry: dict, link_to_detail: bool = False) -> str:
    org, ds_name = split_org_name(entry["name"])
    favicon = favicon_html((entry.get("start_urls") or [None])[0])
    org_html = f'<span class="org">{html.escape(org)}</span>' if org else ""
    ds_name_html = html.escape(ds_name)
    if link_to_detail:
        ds_name_html = f'<a href="systems/{slugify(entry["name"])}.html">{ds_name_html}</a>'
    return f'<div class="name-block">{favicon}<div class="name-text">{org_html}<span class="ds-name">{ds_name_html}</span></div></div>'


def render_row(entry: dict) -> str:
    name_text = entry["name"]
    row_id = slugify(name_text)
    pages = entry.get("pages_indexed")
    pages_text = "—" if pages is None else str(pages)
    n_found = found_count(entry)
    cells = "".join(render_cell(entry, key) for key, _ in COLUMNS)

    return f"""
    <tr id="{row_id}" data-name="{html.escape(name_text.lower())}">
      <td class="name-cell">{render_name_block(entry, link_to_detail=True)}</td>
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
    """A lightweight preview only — thumbnail, name, one-line stats, and a
    link through to the system's own detail page for everything else
    (resources, freshness, full page list). Cards are a preview, not a
    second copy of the full detail."""
    name_text = entry["name"]
    start_url = (entry.get("start_urls") or [None])[0]
    detail_href = f"systems/{slugify(name_text)}.html"

    thumb_html = ""
    if start_url:
        src = thumbnail_src(name_text, start_url)
        thumb_html = f'<a href="{detail_href}" class="card-thumb-link"><img class="card-thumb" src="{src}" alt="" loading="lazy"></a>'

    pages = entry.get("pages_indexed", 0)
    n_found = found_count(entry)
    stats = f"{pages} pages · {n_found}/{len(COLUMNS)} resources"

    return f"""
    <div class="card" data-name="{html.escape(name_text.lower())}">
      {thumb_html}
      <div class="card-body">
        {render_name_block(entry)}
        <div class="meta">{stats}</div>
        <a class="card-details-link" href="{detail_href}">View details →</a>
      </div>
    </div>
    """


def render_page(entries: list[dict]) -> str:
    entries_sorted = sorted(entries, key=lambda e: e["name"].lower())

    # Column indices: 0 = name (text sort), 1 = pages (number), 2..2+len(COLUMNS)-1
    # = resource dots (found), last = found-count (number, pinned).
    header_cells = "".join(
        f'<th data-col="{i + 2}" data-sort="found">{label}</th>' for i, (_, label) in enumerate(COLUMNS)
    )
    last_col = len(COLUMNS) + 2
    rows = "".join(render_row(e) for e in entries_sorted)
    cards = "".join(render_card(e) for e in entries_sorted)
    system_names_json = json.dumps([e["name"] for e in entries_sorted])

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Design System Directory</title>
{FONT_LINK}
<style>
{TOKENS_CSS}
  .page {{ max-width: 1180px; margin: 0 auto; }}

  .mcp-card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-left: 3px solid var(--accent);
    color: var(--text);
    border-radius: 8px;
    padding: 16px 20px;
    margin-bottom: 36px;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
  }}
  .mcp-card-text h2 {{ font-family: "JetBrains Mono", monospace; font-size: 0.88rem; margin: 0 0 3px; font-weight: 700; color: var(--accent); }}
  .mcp-card-text p {{ margin: 0; font-size: 0.82rem; color: var(--text-muted); max-width: 48ch; }}
  .mcp-install {{ display: flex; align-items: center; gap: 8px; }}
  .mcp-command {{
    font-family: "JetBrains Mono", monospace; font-size: 0.78rem; background: var(--surface-sunken);
    color: var(--text); border: 1px solid var(--border); padding: 8px 12px; border-radius: 6px;
    white-space: nowrap; overflow-x: auto; max-width: 380px;
  }}
  .mcp-copy {{
    font-family: "IBM Plex Sans", sans-serif; font-size: 0.78rem; font-weight: 600; padding: 8px 12px;
    border-radius: 6px; border: 1px solid var(--border); background: var(--surface);
    color: var(--accent); cursor: pointer; white-space: nowrap;
  }}
  .mcp-copy:hover {{ background: var(--accent-soft); }}

  .search-card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    box-shadow: 0 2px 4px rgba(15,23,42,0.05), 0 12px 32px -14px rgba(15,23,42,0.18);
    padding: 26px;
    margin-bottom: 20px;
  }}
  .search-title {{
    font-family: "IBM Plex Sans", sans-serif; font-size: 0.95rem; font-weight: 600;
    color: var(--text); margin: 0 0 14px;
  }}
  #q {{
    width: 100%;
    padding: 15px 18px;
    font: inherit;
    font-size: 1.05rem;
    box-sizing: border-box;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--surface-sunken);
    color: var(--text);
  }}
  #q:focus {{ outline: 2px solid var(--accent); outline-offset: 1px; background: var(--surface); }}
  #results {{ margin-top: 8px; }}
  .result {{ padding: 16px 0; border-top: 1px solid var(--border); }}
  .result:first-child {{ border-top: none; }}
  .result .meta {{ font-size: 0.82rem; color: var(--text-muted); margin-bottom: 8px; }}
  .result .meta a {{ color: var(--text-muted); }}
  .result .system-name {{ color: var(--text); font-weight: 600; }}
  .result pre {{
    white-space: pre-wrap; font-family: "IBM Plex Sans", sans-serif; font-size: 0.92rem;
    background: var(--surface-sunken); padding: 12px 14px; border-radius: 6px; margin: 0; line-height: 1.5;
  }}
  .status {{ color: var(--text-muted); font-size: 0.9rem; }}

  .restrict-toggle {{
    display: inline-block; margin-top: 12px; background: none; border: none; padding: 0; cursor: pointer;
    font: inherit; font-size: 0.82rem; color: var(--text-muted); text-decoration: underline; text-underline-offset: 2px;
  }}
  .restrict-toggle:hover {{ color: var(--accent); }}
  .filter-wrap {{ position: relative; margin-top: 16px; }}
  .chips {{ display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 6px; }}
  .chip {{ display: inline-flex; align-items: center; gap: 6px; background: var(--accent-soft); color: var(--accent-soft-text); border-radius: 6px; padding: 4px 6px 4px 12px; font-size: 0.82rem; font-weight: 500; }}
  .chip button {{ background: none; border: none; color: inherit; cursor: pointer; font-size: 0.95rem; line-height: 1; padding: 2px 4px; opacity: 0.75; }}
  .chip button:hover {{ opacity: 1; }}
  .filter-label {{ font-size: 0.72rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-faint); margin: 0 0 8px; }}
  #systemFilter {{
    width: 260px; max-width: 100%; padding: 6px 12px 6px 30px; font: inherit; font-size: 0.85rem;
    box-sizing: border-box; border: 1px solid var(--border); border-radius: 6px;
    background: var(--surface-sunken) url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="%235b6472" stroke-width="2"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>') no-repeat 10px center;
    color: var(--text);
  }}
  #systemFilter:focus {{ background-color: var(--surface); border-color: var(--accent); outline: none; }}
  .filter-dropdown {{
    position: absolute; z-index: 10; top: 100%; left: 0; width: 260px; max-width: 100%;
    background: var(--surface); border: 1px solid var(--border); border-radius: 8px; margin-top: 6px;
    max-height: 220px; overflow-y: auto; box-shadow: var(--shadow);
  }}
  .filter-option {{ padding: 8px 12px; font-size: 0.85rem; cursor: pointer; }}
  .filter-option:hover {{ background: var(--surface-sunken); }}
  .filter-hint {{ font-size: 0.78rem; color: var(--text-faint); margin: 6px 0 0; }}

  .table-toolbar {{ display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; margin-bottom: 8px; }}
  .legend {{ font-size: 0.82rem; color: var(--text-faint); margin: 0 0 12px; }}
  #tableFilter {{
    padding: 8px 12px; font: inherit; font-size: 0.85rem; border: 1px solid var(--border);
    border-radius: 6px; width: 220px; max-width: 100%; background: var(--surface); color: var(--text);
  }}
  #tableFilter:focus {{ outline: 2px solid var(--accent); outline-offset: 1px; }}

  .view-toggle {{ display: flex; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }}
  .view-toggle button {{
    font: inherit; font-size: 0.82rem; font-weight: 600; padding: 7px 14px; border: none; cursor: pointer;
    background: var(--surface); color: var(--text-muted);
  }}
  .view-toggle button + button {{ border-left: 1px solid var(--border); }}
  .view-toggle button.current {{ background: var(--accent-soft); color: var(--accent); }}

  .table-wrap {{
    overflow-x: auto; border: 1px solid var(--border); border-radius: 10px; background: var(--surface);
    box-shadow: var(--shadow);
  }}
  table {{ border-collapse: collapse; width: 100%; font-size: 0.86rem; }}
  th {{
    text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--border); font-weight: 600;
    white-space: nowrap; color: var(--text-muted); font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.03em;
    font-family: "JetBrains Mono", monospace;
  }}
  td {{ padding: 10px 12px; border-bottom: 1px solid var(--border); vertical-align: middle; }}
  tbody tr:last-child td {{ border-bottom: none; }}
  tbody tr:hover td {{ background: var(--surface-sunken); }}
  .name-cell {{ min-width: 200px; }}
  .name-block {{ display: flex; align-items: center; gap: 8px; }}
  .name-text {{ display: flex; flex-direction: column; line-height: 1.25; }}
  .org {{ font-size: 0.68rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; color: var(--text-faint); }}
  .ds-name {{ font-weight: 600; font-size: 0.92rem; }}
  .ds-name a {{ color: inherit; text-decoration: none; }}
  .ds-name a:hover {{ text-decoration: underline; }}
  .favicon {{ border-radius: 3px; flex: none; }}
  .meta {{ font-size: 0.78rem; color: var(--text-muted); margin-top: 3px; font-variant-numeric: tabular-nums; font-family: "JetBrains Mono", monospace; }}
  .cell {{ text-align: center; font-variant-numeric: tabular-nums; }}
  .cell-pages {{ font-family: "JetBrains Mono", monospace; color: var(--text-muted); }}
  .cell-found a {{ color: var(--accent); text-decoration: none; font-size: 1.1rem; }}
  .cell-none {{ color: var(--text-faint); }}
  .cell-found-count {{
    font-family: "JetBrains Mono", monospace; font-weight: 600; color: var(--text);
    position: sticky; right: 0; background: var(--surface);
    box-shadow: -6px 0 8px -8px rgba(15,23,42,0.25);
  }}
  th:last-child {{ position: sticky; right: 0; background: var(--surface); box-shadow: -6px 0 8px -8px rgba(15,23,42,0.25); }}
  tbody tr:hover td.cell-found-count {{ background: var(--surface-sunken); }}
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
  .card {{
    background: var(--surface); border: 1px solid var(--border); border-radius: 8px; overflow: hidden;
    box-shadow: var(--shadow); display: flex; flex-direction: column;
  }}
  .card .name-block {{ align-items: flex-start; }}
  .card-thumb-link {{ display: block; }}
  .card-thumb {{
    width: 100%; aspect-ratio: 16 / 9; object-fit: cover; object-position: top;
    background: var(--surface-sunken); display: block;
  }}
  .card-body {{ padding: 14px; }}
  .card-details-link {{ display: inline-block; margin-top: 10px; font-size: 0.82rem; font-weight: 600; text-decoration: none; }}
  .card-details-link:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<div class="page">
  <p class="eyebrow">design-system-directory</p>
  <h1>Find what the community has already published</h1>
  <p class="subtitle">{len(entries_sorted)} external design systems, semantically searchable and cross-referenced by the resources each one has published — GitHub, Storybook, Figma, tokens, and more. Regenerated weekly.</p>

  {routes_nav("system")}

  <section class="search-card" id="search">
    <h2 class="search-title">Search component patterns, tokens, and guidance across every indexed design system</h2>
    <input id="q" type="text" placeholder="e.g. table column resizing, disabled button states...">

    <button type="button" class="restrict-toggle" id="restrictToggle">Restrict search results</button>

    <div class="filter-wrap" id="filterWrap" hidden>
      <p class="filter-label">Optional: limit to specific systems</p>
      <div class="chips" id="chips"></div>
      <input id="systemFilter" type="text" placeholder="Type a system name...">
      <div class="filter-dropdown" id="filterDropdown" hidden></div>
      <p class="filter-hint">Leave blank to search across all indexed systems.</p>
    </div>

    <div id="results"></div>
  </section>

  <div class="mcp-card">
    <div class="mcp-card-text">
      <h2>Use this from Claude (or any MCP client)</h2>
      <p>One command connects it as an MCP server — then just ask your assistant design-system questions directly.</p>
    </div>
    <div class="mcp-install">
      <code class="mcp-command" id="mcpCommand">{MCP_INSTALL_COMMAND}</code>
      <button class="mcp-copy" id="mcpCopy" type="button">Copy</button>
    </div>
  </div>

  <div class="table-toolbar">
    <input id="tableFilter" type="text" placeholder="Filter by name...">
    <div class="view-toggle" id="viewToggle" role="group" aria-label="View">
      <button type="button" data-view="list" class="current">List</button>
      <button type="button" data-view="grid">Grid</button>
    </div>
  </div>
  <p class="legend" id="listLegend">● = resource found and linked · — = none found · click a column header to sort</p>

  <div class="table-wrap" id="listView">
  <table id="directoryTable">
    <thead>
      <tr>
        <th data-col="0" data-sort="text">System</th>
        <th data-col="1" data-sort="number">Pages</th>
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
  const API_URL = {json.dumps(SEARCH_API_URL)};
  const ALL_SYSTEMS = {system_names_json};

  const input = document.getElementById("q");
  const resultsEl = document.getElementById("results");
  const filterInput = document.getElementById("systemFilter");
  const dropdown = document.getElementById("filterDropdown");
  const chipsEl = document.getElementById("chips");
  const restrictToggle = document.getElementById("restrictToggle");
  const filterWrap = document.getElementById("filterWrap");
  let debounceTimer;
  const selectedSystems = new Set();

  restrictToggle.addEventListener("click", () => {{
    const nowHidden = !filterWrap.hidden;
    filterWrap.hidden = nowHidden;
    restrictToggle.textContent = nowHidden ? "Restrict search results" : "Hide restriction";
    if (!nowHidden) filterInput.focus();
  }});

  input.addEventListener("input", () => {{
    clearTimeout(debounceTimer);
    const query = input.value.trim();
    if (!query) {{
      resultsEl.innerHTML = "";
      return;
    }}
    debounceTimer = setTimeout(() => runSearch(query), 400);
  }});

  filterInput.addEventListener("input", () => renderDropdown(filterInput.value));
  filterInput.addEventListener("focus", () => renderDropdown(filterInput.value));
  document.addEventListener("click", (e) => {{
    if (!e.target.closest(".filter-wrap")) dropdown.hidden = true;
  }});

  function renderDropdown(text) {{
    const query = text.trim().toLowerCase();
    const matches = ALL_SYSTEMS
      .filter(name => !selectedSystems.has(name))
      .filter(name => !query || name.toLowerCase().includes(query))
      .slice(0, 8);

    if (!matches.length) {{
      dropdown.hidden = true;
      return;
    }}
    dropdown.innerHTML = matches.map(name =>
      `<div class="filter-option" data-name="${{escapeHtml(name)}}">${{escapeHtml(name)}}</div>`
    ).join("");
    dropdown.hidden = false;
  }}

  dropdown.addEventListener("click", (e) => {{
    const option = e.target.closest(".filter-option");
    if (!option) return;
    selectedSystems.add(option.dataset.name);
    filterInput.value = "";
    dropdown.hidden = true;
    renderChips();
    if (input.value.trim()) runSearch(input.value.trim());
  }});

  function renderChips() {{
    chipsEl.innerHTML = [...selectedSystems].map(name => `
      <span class="chip">${{escapeHtml(name)}}<button type="button" data-name="${{escapeHtml(name)}}" aria-label="Remove filter">×</button></span>
    `).join("");
  }}

  chipsEl.addEventListener("click", (e) => {{
    const button = e.target.closest("button");
    if (!button) return;
    selectedSystems.delete(button.dataset.name);
    renderChips();
    if (input.value.trim()) runSearch(input.value.trim());
  }});

  async function runSearch(query) {{
    resultsEl.innerHTML = '<p class="status">Searching… (first request may take up to a minute if the server was idle)</p>';
    try {{
      const params = new URLSearchParams({{ q: query }});
      selectedSystems.forEach(name => params.append("system", name));
      const res = await fetch(`${{API_URL}}?${{params.toString()}}`);
      const data = await res.json();
      renderResults(data.results || []);
    }} catch (err) {{
      waitAndRetry(query, 10);
    }}
  }}

  function waitAndRetry(query, seconds) {{
    if (seconds <= 0) {{
      resultsEl.innerHTML = '<p class="status">Retrying now…</p>';
      runSearch(query);
      return;
    }}
    resultsEl.innerHTML = `<p class="status">Just warming up the search server — retrying in ${{seconds}}…</p>`;
    setTimeout(() => waitAndRetry(query, seconds - 1), 1000);
  }}

  function renderResults(results) {{
    if (!results.length) {{
      resultsEl.innerHTML = '<p class="status">No matches found.</p>';
      return;
    }}
    resultsEl.innerHTML = results.map(r => `
      <div class="result">
        <div class="meta">
          <span class="system-name">${{escapeHtml(r.design_system_name || "Unknown system")}}</span>
          &middot; score ${{r.score.toFixed(3)}}
          &middot; <a href="${{escapeHtml(r.url || "#")}}" target="_blank" rel="noopener">${{escapeHtml(r.url || "")}}</a>
        </div>
        <pre>${{escapeHtml(r.text || "")}}</pre>
      </div>
    `).join("");
  }}

  function escapeHtml(str) {{
    return String(str).replace(/[&<>"']/g, c => ({{
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }}[c]));
  }}

  // --- Copy MCP install command ---
  const mcpCopy = document.getElementById("mcpCopy");
  const mcpCommand = document.getElementById("mcpCommand");
  mcpCopy.addEventListener("click", async () => {{
    try {{
      await navigator.clipboard.writeText(mcpCommand.textContent);
      mcpCopy.textContent = "Copied!";
      setTimeout(() => {{ mcpCopy.textContent = "Copy"; }}, 1500);
    }} catch (err) {{
      // Clipboard API unavailable (e.g. insecure context) — text is still selectable manually.
    }}
  }});

  // --- Table sort ---
  let currentSort = {{ col: null, dir: 1 }};

  function cellValue(row, col, type) {{
    if (col === 0) return row.dataset.name || "";
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
  const listLegend = document.getElementById("listLegend");
  const gridView = document.getElementById("cardsGrid");

  function setView(view) {{
    listView.hidden = view !== "list";
    listLegend.hidden = view !== "list";
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
