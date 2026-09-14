"""
Builds home.html — the site's actual homepage. Kept deliberately simple: search
box (the primary action), the MCP connection callout, and links out to the
full directory (list/grid matrix) and component index.

Deploys as index.html — see the workflows' Netlify deploy step. Root-absolute
links ("/directory.html" etc., via page_shell.routes_nav) work correctly from
here since this *is* what "/" resolves to.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from generate_directory import compute_stats, indexed_only, load_systems
from page_shell import (
    COPY_ICON_SVG,
    FILTER_ICON_SVG,
    FONT_LINK,
    GRID_ICON_SVG_LARGE,
    PACKAGE_ICON_SVG,
    TOKENS_CSS,
    routes_nav,
)
from text_utils import full_name

OUTPUT_FILE = Path(__file__).parent / "home.html"

# Update if the Render service URL ever changes.
MCP_URL = "https://ds-directory-mcp.onrender.com/mcp"
SEARCH_API_URL = "https://ds-directory-mcp.onrender.com/search"
HEALTH_API_URL = "https://ds-directory-mcp.onrender.com/health"
MCP_INSTALL_COMMAND = f"claude mcp add ds-directory --transport http {MCP_URL}"


def render_page(entries: list[dict]) -> str:
    stats = compute_stats(entries)
    system_names_json = json.dumps(sorted(full_name(e) for e in entries))

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Search {stats['total_systems']} design systems</title>
<meta name="build-date" content="{datetime.now().strftime('%Y-%m-%d')}">
<meta name="last-crawl-activity" content="{stats.get('last_checked', '')[:10]}">
<meta name="description" content="Semantic search across {stats['total_systems']} publicly documented design systems ({stats['total_pages']:,} pages indexed) — searchable in a browser or via MCP for AI agents.">
{FONT_LINK}
<style>
{TOKENS_CSS}
  .hero {{ padding-top: 8px; margin-bottom: 36px; }}
  .hero h1 {{ margin-bottom: 10px; }}
  .hero-subtitle {{ font-size: 1.05rem; color: var(--text-muted); line-height: 1.5; max-width: 62ch; margin: 0 0 22px; }}
  #q {{
    width: 100%; padding: 20px 22px; font: inherit; font-size: 1.2rem; box-sizing: border-box;
    border: 1px solid var(--border); border-radius: 10px; background: var(--surface); color: var(--text);
    box-shadow: 0 2px 4px rgba(15,23,42,0.05), 0 12px 32px -14px rgba(15,23,42,0.18);
  }}
  #q:focus {{ outline: 2px solid var(--accent); outline-offset: 1px; }}
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

  .nav-cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 14px; margin: 20px 0 32px; }}
  .nav-card {{
    display: flex; align-items: center; gap: 14px; background: var(--surface); border: 1px solid var(--border);
    border-radius: 12px; padding: 18px 20px; text-decoration: none; color: var(--text);
    box-shadow: var(--shadow); transition: border-color 0.15s, transform 0.15s;
  }}
  .nav-card:hover {{ border-color: var(--accent); transform: translateY(-1px); }}
  .nav-card-icon {{
    flex: none; display: flex; align-items: center; justify-content: center; width: 42px; height: 42px;
    border-radius: 9px; background: var(--accent-soft); color: var(--accent);
  }}
  .nav-card-text {{ flex: 1; min-width: 0; }}
  .nav-card-text h3 {{ margin: 0 0 2px; font-size: 0.96rem; font-weight: 600; color: var(--text); }}
  .nav-card-text p {{ margin: 0; font-size: 0.8rem; color: var(--text-muted); }}
  .nav-card-arrow {{ flex: none; color: var(--text-faint); font-size: 1.2rem; }}
  .nav-card:hover .nav-card-arrow {{ color: var(--accent); }}

  .mcp-bar {{
    display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 16px;
    background: var(--surface); border: 1px solid var(--border); border-left: 3px solid var(--accent);
    border-radius: 12px; padding: 20px 24px; margin-bottom: 36px; box-shadow: var(--shadow);
  }}
  .mcp-badge {{
    display: inline-block; font-family: "JetBrains Mono", monospace; font-size: 0.68rem; font-weight: 700;
    letter-spacing: 0.05em; text-transform: uppercase; color: var(--accent); background: var(--accent-soft);
    border-radius: 4px; padding: 2px 7px; margin-bottom: 4px;
  }}
  .mcp-card-text h2 {{ font-family: "JetBrains Mono", monospace; font-size: 0.82rem; margin: 0; font-weight: 700; color: var(--text); }}
  .mcp-card-text p {{ margin: 0; font-size: 0.78rem; color: var(--text-muted); max-width: 48ch; }}
  .mcp-status {{ margin-top: 6px !important; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }}
  .status-dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%; flex: none; }}
  .status-dot.awake {{ background: #22c55e; }}
  .status-dot.sleeping {{ background: #f59e0b; }}
  .wake-button {{
    font: inherit; font-size: 0.76rem; font-weight: 600; padding: 3px 10px; border-radius: 999px;
    border: 1px solid var(--border); background: var(--surface); color: var(--accent); cursor: pointer;
  }}
  .wake-button:hover {{ background: var(--accent-soft); }}
  .wake-button:disabled {{ opacity: 0.6; cursor: default; }}
  .mcp-install {{ display: flex; align-items: center; gap: 8px; }}
  .mcp-command {{
    font-family: "JetBrains Mono", monospace; font-size: 0.78rem; background: var(--surface-sunken);
    color: var(--text); border: 1px solid var(--border); padding: 8px 12px; border-radius: 6px;
    white-space: nowrap; overflow-x: auto; max-width: 380px;
  }}
  .mcp-copy {{
    display: inline-flex; align-items: center; gap: 6px;
    font-family: "IBM Plex Sans", sans-serif; font-size: 0.78rem; font-weight: 600; padding: 8px 12px;
    border-radius: 6px; border: 1px solid var(--border); background: var(--surface);
    color: var(--accent); cursor: pointer; white-space: nowrap;
  }}
  .mcp-copy svg {{ flex: none; }}
  .mcp-copy:hover {{ background: var(--accent-soft); }}
</style>
</head>
<body>
{routes_nav("search")}
<div class="page">
  <section class="hero" id="search">
    <h1>Search {stats['total_systems']} design systems</h1>
    <p class="hero-subtitle">Find component patterns, tokens, and guidance across every publicly documented design system indexed here — or connect it to your AI agent via MCP.</p>
    <input id="q" type="text" placeholder="e.g. table column resizing, disabled button states...">

    <button type="button" class="restrict-toggle" id="restrictToggle">{FILTER_ICON_SVG}<span id="restrictToggleLabel">Filter by system</span></button>

    <div class="filter-wrap" id="filterWrap" hidden>
      <p class="filter-label">Optional: limit to specific systems</p>
      <div class="chips" id="chips"></div>
      <input id="systemFilter" type="text" placeholder="Type a system name...">
      <div class="filter-dropdown" id="filterDropdown" hidden></div>
      <p class="filter-hint">Leave blank to search across all indexed systems.</p>
    </div>

    <div id="results"></div>
  </section>

  <div class="nav-cards">
    <a class="nav-card" href="/directory.html">
      <span class="nav-card-icon">{GRID_ICON_SVG_LARGE}</span>
      <span class="nav-card-text">
        <h3>Browse all systems</h3>
        <p>Every indexed design system, as a sortable list or grid.</p>
      </span>
      <span class="nav-card-arrow">&rarr;</span>
    </a>
    <a class="nav-card" href="/components/index.html">
      <span class="nav-card-icon">{PACKAGE_ICON_SVG}</span>
      <span class="nav-card-text">
        <h3>Browse by component</h3>
        <p>See which systems have documented a given component or pattern.</p>
      </span>
      <span class="nav-card-arrow">&rarr;</span>
    </a>
  </div>

  <div class="mcp-bar">
    <div class="mcp-card-text">
      <span class="mcp-badge">MCP Server</span>
      <h2>Connect this to Claude (or any MCP-compatible client)</h2>
      <p>One command adds this whole index as a tool your agent can call directly.</p>
      <p class="mcp-status" id="mcpStatus">Checking server status…</p>
    </div>
    <div class="mcp-install">
      <code class="mcp-command" id="mcpCommand">{MCP_INSTALL_COMMAND}</code>
      <button class="mcp-copy" id="mcpCopy" type="button">{COPY_ICON_SVG}<span id="mcpCopyLabel">Copy</span></button>
    </div>
  </div>
</div>

<script>
  const API_URL = {json.dumps(SEARCH_API_URL)};
  const HEALTH_URL = {json.dumps(HEALTH_API_URL)};
  const ALL_SYSTEMS = {system_names_json};

  const input = document.getElementById("q");
  const resultsEl = document.getElementById("results");
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
  const mcpCopyLabel = document.getElementById("mcpCopyLabel");
  const mcpCommand = document.getElementById("mcpCommand");
  mcpCopy.addEventListener("click", async () => {{
    try {{
      await navigator.clipboard.writeText(mcpCommand.textContent);
      mcpCopyLabel.textContent = "Copied!";
      setTimeout(() => {{ mcpCopyLabel.textContent = "Copy"; }}, 1500);
    }} catch (err) {{
      // Clipboard API unavailable (e.g. insecure context) — text is still selectable manually.
    }}
  }});

  // --- MCP server status: awake/sleeping + when last confirmed awake, plus a
  // manual "wake it up" button so a visitor doesn't have to wait for their
  // first real search to trigger the cold start.
  // "Last awake" is remembered per-viewer in localStorage — there's no shared
  // backend for this, and it doesn't need to be. Polls every 2 minutes
  // (Render's sleep timeout is 15 min, so that's plenty granular) and ONLY
  // while this tab is actually visible — a backgrounded tab makes zero
  // requests, and switching back triggers an immediate re-check.
  const mcpStatusEl = document.getElementById("mcpStatus");
  let healthPollTimer = null;
  let waking = false;

  function relativeTime(isoString) {{
    const diffMin = Math.round((Date.now() - new Date(isoString).getTime()) / 60000);
    if (diffMin < 1) return "just now";
    if (diffMin < 60) return `${{diffMin}}m ago`;
    const diffHr = Math.round(diffMin / 60);
    if (diffHr < 24) return `${{diffHr}}h ago`;
    return `${{Math.round(diffHr / 24)}}d ago`;
  }}

  function renderMcpStatus(awake) {{
    let lastAwake = null;
    try {{ lastAwake = localStorage.getItem("mcp-last-awake"); }} catch (err) {{ /* private browsing etc. */ }}

    if (awake) {{
      mcpStatusEl.innerHTML = '<span class="status-dot awake"></span> MCP server awake';
      return;
    }}
    const lastText = lastAwake ? ` (last awake ${{relativeTime(lastAwake)}})` : "";
    mcpStatusEl.innerHTML =
      `<span class="status-dot sleeping"></span> MCP server sleeping — takes ~30-60s to wake up${{lastText}} ` +
      '<button type="button" class="wake-button" id="wakeButton">Wake it up</button>';
    const wakeButton = document.getElementById("wakeButton");
    if (wakeButton) {{
      wakeButton.addEventListener("click", () => {{
        if (waking) return;
        waking = true;
        wakeButton.disabled = true;
        wakeButton.textContent = "Waking…";
        checkMcpHealth().finally(() => {{ waking = false; }});
      }});
    }}
  }}

  async function checkMcpHealth() {{
    try {{
      const res = await fetch(HEALTH_URL, {{ signal: AbortSignal.timeout(3000) }});
      if (!res.ok) throw new Error("not ok");
      try {{ localStorage.setItem("mcp-last-awake", new Date().toISOString()); }} catch (err) {{ /* fine */ }}
      renderMcpStatus(true);
    }} catch (err) {{
      renderMcpStatus(false);
    }}
  }}

  function startMcpPolling() {{
    checkMcpHealth();
    if (healthPollTimer) return;
    healthPollTimer = setInterval(checkMcpHealth, 120000);
  }}

  function stopMcpPolling() {{
    if (healthPollTimer) {{
      clearInterval(healthPollTimer);
      healthPollTimer = null;
    }}
  }}

  document.addEventListener("visibilitychange", () => {{
    if (document.visibilityState === "visible") {{
      startMcpPolling();
    }} else {{
      stopMcpPolling();
    }}
  }});

  if (document.visibilityState === "visible") startMcpPolling();

  // --- Auto-run a search if arriving via the nav's mini-search (e.g. "/?q=button") ---
  const urlQuery = new URLSearchParams(window.location.search).get("q");
  if (urlQuery) {{
    input.value = urlQuery;
    runSearch(urlQuery);
  }}
</script>
</body>
</html>
"""


if __name__ == "__main__":
    systems = indexed_only(load_systems())
    OUTPUT_FILE.write_text(render_page(systems))
    print(f"Wrote {OUTPUT_FILE} with stats for {len(systems)} systems.")
