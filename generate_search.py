"""
Builds search.html — the actual results page. Split out from home.html
(which now just holds the search box) because dumping raw match snippets
straight onto the homepage read as noise dumped under the hero rather than a
real destination: no room to breathe, no way to link back to it, and the
homepage's job (a clean landing point) got muddled with the results page's
job (scanning a list of matches). This page owns that job on its own URL
(/search.html?q=...), reachable by submitting the search box on home.html or
the nav's mini-search.

Deploys as a normal top-level page (see the workflows' Netlify deploy step) —
root-absolute links via page_shell.routes_nav work the same as any other page.
"""

from __future__ import annotations

import json
from pathlib import Path

from generate_directory import indexed_only, load_systems
from generate_home import MCP_INSTALL_COMMAND
from page_shell import COPY_ICON_SVG, FILTER_ICON_SVG, FONT_LINK, TOKENS_CSS, routes_nav
from slug import slugify
from text_utils import full_name

OUTPUT_FILE = Path(__file__).parent / "search.html"

SEARCH_API_URL = "https://ds-directory-mcp.onrender.com/search"


def render_page(entries: list[dict]) -> str:
    system_names_json = json.dumps(sorted(full_name(e) for e in entries))
    # Maps a result's design_system_name (as the API returns it) to its own
    # detail page slug, so a result can link to systems/<slug>.html instead of
    # only ever linking out to the raw matched URL.
    slug_by_name_json = json.dumps({full_name(e): slugify(full_name(e)) for e in entries})

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Search results — Design Systems Directory</title>
<meta name="robots" content="noindex">
{FONT_LINK}
<style>
{TOKENS_CSS}
  .search-header {{ padding-top: 8px; margin-bottom: 28px; }}
  #q {{
    width: 100%; padding: 16px 20px; font: inherit; font-size: 1.05rem; box-sizing: border-box;
    border: 1px solid var(--border); border-radius: 10px; background: var(--surface); color: var(--text);
    box-shadow: 0 2px 4px rgba(15,23,42,0.05), 0 12px 32px -14px rgba(15,23,42,0.18);
  }}
  #q:focus {{ outline: 2px solid var(--accent); outline-offset: 1px; }}

  .restrict-toggle {{
    display: inline-flex; align-items: center; gap: 5px; margin-top: 12px; background: none; border: none; padding: 0; cursor: pointer;
    font: inherit; font-size: 0.82rem; color: var(--text-muted); text-decoration: underline; text-underline-offset: 2px;
  }}
  .restrict-toggle svg {{ flex: none; }}
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

  .results-count {{ font-size: 0.82rem; color: var(--text-muted); margin: 24px 0 12px; }}
  #results {{ display: flex; flex-direction: column; gap: 14px; }}
  .status {{ color: var(--text-muted); font-size: 0.9rem; }}

  /* One match: system name + a discreet source link up top, the cleaned
     snippet below as an ordinary paragraph — no raw score, no monospace
     <pre> dump. See cleanSnippet() for what turns a raw chunk into this. */
  .result {{
    background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 16px 18px;
  }}
  .result .result-head {{ display: flex; align-items: baseline; flex-wrap: wrap; gap: 4px 10px; margin-bottom: 8px; }}
  .result .system-name {{ color: var(--text); font-weight: 600; font-size: 0.94rem; text-decoration: none; }}
  .result .system-name:hover {{ text-decoration: underline; }}
  .result .source-link {{
    font-size: 0.78rem; color: var(--text-faint); text-decoration: none; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis; max-width: 100%;
  }}
  .result .source-link:hover {{ color: var(--accent); }}
  .result .snippet {{ margin: 0; font-size: 0.9rem; line-height: 1.6; color: var(--text); }}
  .result .snippet.is-partial {{ color: var(--text-muted); }}

  /* These raw-chunk snippets are inherently a lossy view of the real docs —
     an agent over MCP instead reads whole pages and reasons across them, a
     genuinely better result for anything non-trivial, not just a cross-sell.
     Placed above the results (not buried below them) so it's seen before,
     not after, scanning through fragments — but kept to one compact line so
     it doesn't compete with the results themselves. */
  .mcp-promo {{
    display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px 16px;
    background: var(--accent-soft); border: 1px solid var(--border); border-radius: 8px;
    padding: 10px 14px; margin-bottom: 18px; font-size: 0.85rem; color: var(--accent-soft-text);
  }}
  .mcp-promo-text {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }}
  .mcp-promo-badge {{
    font-family: "JetBrains Mono", monospace; font-size: 0.66rem; font-weight: 700; letter-spacing: 0.04em;
    text-transform: uppercase; background: var(--surface); color: var(--accent); border-radius: 4px; padding: 2px 6px;
  }}
  .mcp-promo-copy {{
    display: inline-flex; align-items: center; gap: 6px; font: inherit; font-size: 0.8rem; font-weight: 600;
    padding: 6px 10px; border-radius: 6px; border: 1px solid var(--border); background: var(--surface);
    color: var(--accent); cursor: pointer; white-space: nowrap;
  }}
  .mcp-promo-copy:hover {{ background: var(--accent); color: #fff; border-color: var(--accent); }}
  .mcp-promo-copy svg {{ flex: none; }}
</style>
</head>
<body>
{routes_nav("search")}
<div class="page">
  <div class="search-header">
    <input id="q" type="text" placeholder="e.g. table column resizing, disabled button states...">

    <button type="button" class="restrict-toggle" id="restrictToggle">{FILTER_ICON_SVG}<span id="restrictToggleLabel">Filter by system</span></button>

    <div class="filter-wrap" id="filterWrap" hidden>
      <p class="filter-label">Optional: limit to specific systems</p>
      <div class="chips" id="chips"></div>
      <input id="systemFilter" type="text" placeholder="Type a system name...">
      <div class="filter-dropdown" id="filterDropdown" hidden></div>
      <p class="filter-hint">Leave blank to search across all indexed systems.</p>
    </div>
  </div>

  <div class="mcp-promo">
    <div class="mcp-promo-text">
      <span class="mcp-promo-badge">MCP</span>
      These are raw text snippets — an AI agent connected over MCP reads full pages and reasons across systems for a much better answer.
    </div>
    <button type="button" class="mcp-promo-copy" id="mcpPromoCopy">{COPY_ICON_SVG}<span id="mcpPromoCopyLabel">Copy install command</span></button>
  </div>

  <div id="resultsCount" class="results-count"></div>
  <div id="results"></div>
</div>

<script>
  const API_URL = {json.dumps(SEARCH_API_URL)};
  const ALL_SYSTEMS = {system_names_json};
  const SLUG_BY_NAME = {slug_by_name_json};

  const input = document.getElementById("q");
  const resultsEl = document.getElementById("results");
  const resultsCountEl = document.getElementById("resultsCount");
  const filterInput = document.getElementById("systemFilter");
  const dropdown = document.getElementById("filterDropdown");
  const chipsEl = document.getElementById("chips");
  const restrictToggle = document.getElementById("restrictToggle");
  const restrictToggleLabel = document.getElementById("restrictToggleLabel");
  const filterWrap = document.getElementById("filterWrap");
  let debounceTimer;
  const selectedSystems = new Set();

  restrictToggle.addEventListener("click", () => {{
    const nowHidden = !filterWrap.hidden;
    filterWrap.hidden = nowHidden;
    restrictToggleLabel.textContent = nowHidden ? "Filter by system" : "Hide system filter";
    if (!nowHidden) filterInput.focus();
  }});

  input.addEventListener("input", () => {{
    clearTimeout(debounceTimer);
    const query = input.value.trim();
    updateUrl(query);
    if (!query) {{
      resultsEl.innerHTML = "";
      resultsCountEl.textContent = "";
      return;
    }}
    debounceTimer = setTimeout(() => runSearch(query), 400);
  }});

  function updateUrl(query) {{
    const url = new URL(window.location.href);
    if (query) url.searchParams.set("q", query); else url.searchParams.delete("q");
    window.history.replaceState({{}}, "", url);
  }}

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
    resultsCountEl.textContent = "";
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

  // Turns a raw indexed chunk (often a mid-sentence/mid-list slice of a
  // scraped page — see content it's pulled from) into one readable
  // paragraph: blank/broken lines collapsed to spaces, capped to a sane
  // reading length at a word boundary, with a leading "…" when the chunk
  // itself doesn't look like it started a sentence (lowercase first
  // letter/mid-word) — an honest cue that this is a fragment of a larger
  // page, not a garbled result.
  const MAX_SNIPPET_CHARS = 420;

  function cleanSnippet(raw) {{
    const collapsed = String(raw || "").split(/\\n+/).map(line => line.trim()).filter(Boolean).join(" ");
    if (!collapsed) return {{ text: "", partial: false }};

    const looksMidSentence = !/^[A-Z0-9"'“‘(\\[]/.test(collapsed);
    let text = collapsed;
    let truncated = false;
    if (text.length > MAX_SNIPPET_CHARS) {{
      const cut = text.slice(0, MAX_SNIPPET_CHARS);
      const lastSpace = cut.lastIndexOf(" ");
      text = lastSpace > 200 ? cut.slice(0, lastSpace) : cut;
      truncated = true;
    }}
    return {{
      text: (looksMidSentence ? "… " : "") + text + (truncated ? " …" : ""),
      partial: looksMidSentence || truncated,
    }};
  }}

  function renderResults(results) {{
    if (!results.length) {{
      resultsCountEl.textContent = "";
      resultsEl.innerHTML = '<p class="status">No matches found.</p>';
      return;
    }}
    resultsCountEl.textContent = `${{results.length}} match${{results.length === 1 ? "" : "es"}}`;
    resultsEl.innerHTML = results.map(r => {{
      const name = r.design_system_name || "Unknown system";
      const slug = SLUG_BY_NAME[name];
      const nameHtml = slug
        ? `<a class="system-name" href="/systems/${{escapeHtml(slug)}}">${{escapeHtml(name)}}</a>`
        : `<span class="system-name">${{escapeHtml(name)}}</span>`;
      const snippet = cleanSnippet(r.text);
      return `
        <div class="result">
          <div class="result-head">
            ${{nameHtml}}
            <a class="source-link" href="${{escapeHtml(r.url || "#")}}" target="_blank" rel="noopener">${{escapeHtml(r.url || "")}}</a>
          </div>
          <p class="snippet${{snippet.partial ? " is-partial" : ""}}">${{escapeHtml(snippet.text)}}</p>
        </div>
      `;
    }}).join("");
  }}

  function escapeHtml(str) {{
    return String(str).replace(/[&<>"']/g, c => ({{
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }}[c]));
  }}

  // --- Copy MCP install command ---
  const mcpPromoCopy = document.getElementById("mcpPromoCopy");
  const mcpPromoCopyLabel = document.getElementById("mcpPromoCopyLabel");
  mcpPromoCopy.addEventListener("click", async () => {{
    try {{
      await navigator.clipboard.writeText({json.dumps(MCP_INSTALL_COMMAND)});
      mcpPromoCopyLabel.textContent = "Copied!";
      setTimeout(() => {{ mcpPromoCopyLabel.textContent = "Copy install command"; }}, 1500);
    }} catch (err) {{
      // Clipboard API unavailable (e.g. insecure context) — nothing to fall back to here.
    }}
  }});

  const urlQuery = new URLSearchParams(window.location.search).get("q");
  if (urlQuery) {{
    input.value = urlQuery;
    runSearch(urlQuery);
  }} else {{
    input.focus();
  }}
</script>
</body>
</html>
"""


if __name__ == "__main__":
    systems = indexed_only(load_systems())
    OUTPUT_FILE.write_text(render_page(systems))
    print(f"Wrote {OUTPUT_FILE} with {len(systems)} systems.")
