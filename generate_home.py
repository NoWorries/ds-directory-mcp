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
    FAVICON_LINK,
    FONT_LINK,
    GRID_ICON_SVG_LARGE,
    PACKAGE_ICON_SVG,
    TOKENS_CSS,
    routes_nav,
)

OUTPUT_FILE = Path(__file__).parent / "home.html"

# Update if the Render service URL ever changes.
MCP_URL = "https://ds-directory-mcp.onrender.com/mcp"
HEALTH_API_URL = "https://ds-directory-mcp.onrender.com/health"
MCP_INSTALL_COMMAND = f"claude mcp add ds-directory --transport http {MCP_URL}"


def render_page(entries: list[dict]) -> str:
    stats = compute_stats(entries)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Search {stats['total_systems']} design systems</title>
<meta name="build-date" content="{datetime.now().strftime('%Y-%m-%d')}">
<meta name="last-crawl-activity" content="{stats.get('last_checked', '')[:10]}">
<meta name="description" content="Semantic search across {stats['total_systems']} publicly documented design systems ({stats['total_pages']:,} pages indexed) — searchable in a browser or via MCP for AI agents.">
{FAVICON_LINK}
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

  .nav-cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 14px; margin: 20px 0 32px; }}
  .nav-card {{
    display: flex; align-items: center; gap: 14px; background: var(--surface); border: 1px solid var(--border);
    border-radius: 12px; padding: 18px 20px; text-decoration: none; color: var(--text);
    box-shadow: var(--shadow); transition: border-color 0.15s, transform 0.15s;
    overflow: hidden;
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

  @media (max-width: 640px) {{
    /* #q's font-size drove the placeholder's own character width — at
       1.2rem on a ~340px-wide input, "e.g. table column resizing, disabled
       button sta..." clipped after a handful of words with no visual hint
       there was more. Smaller text fits meaningfully more of it before the
       same clipping (the field's real limitation, not this one's to fully
       solve) sets in. */
    #q {{ font-size: 1rem; padding: 16px 18px; }}
    /* The 42px icon box read as disproportionately large once the card
       itself was full viewport width but still narrow in absolute terms —
       shrunk alongside the card's own tighter padding so the text gets
       more of the available room back. */
    .nav-card {{ padding: 14px 16px; gap: 12px; }}
    .nav-card-icon {{ width: 32px; height: 32px; }}
    .nav-card-icon svg {{ width: 16px; height: 16px; }}
  }}

  .mcp-bar {{
    display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 16px;
    background: var(--surface); border: 1px solid var(--border); border-left: 3px solid var(--accent);
    border-radius: 12px; padding: 20px 24px; margin-bottom: 36px; box-shadow: var(--shadow);
    overflow: hidden;
  }}
  .mcp-badge {{
    display: inline-block; font-family: "JetBrains Mono", monospace; font-size: 0.68rem; font-weight: 700;
    letter-spacing: 0.05em; text-transform: uppercase; color: var(--accent); background: var(--accent-soft);
    border-radius: 4px; padding: 2px 7px; margin-bottom: 4px;
  }}
  .mcp-card-text h2 {{ font-family: "JetBrains Mono", monospace; font-size: 0.82rem; margin: 0; font-weight: 700; color: var(--text); }}
  .mcp-card-text p {{ margin: 0; font-size: 0.78rem; color: var(--text-muted); max-width: 48ch; }}
  /* Every status state (checking/awake/sleeping/waking) renders the exact
     same three elements — dot, text, wake button — so the row never
     reflows between states; only their content/visibility changes (see
     renderMcpStatus() below). The button is hidden with `visibility`, not
     `hidden`/`display: none`, specifically so it keeps occupying its slot
     even when invisible — that's what keeps .mcp-bar's height constant
     across every state instead of the button's appearance/disappearance
     reshuffling the row. min-height covers the text wrapping to two lines
     on narrow widths, for the same reason. */
  .mcp-status {{ margin-top: 6px !important; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; line-height: 1.3; min-height: 2.6em; }}
  .status-dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%; flex: none; }}
  .status-dot.checking {{ background: var(--text-faint); }}
  .status-dot.awake {{ background: #22c55e; }}
  .status-dot.sleeping {{ background: #f59e0b; }}
  .status-dot.waking {{ background: var(--accent); animation: status-pulse 2s ease-in-out infinite; }}
  @keyframes status-pulse {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: 0.35; }} }}
  .wake-button {{
    font: inherit; font-size: 0.76rem; font-weight: 600; padding: 3px 10px; border-radius: 999px;
    border: 1px solid var(--border); background: var(--surface); color: var(--accent); cursor: pointer;
  }}
  .wake-button:hover {{ background: var(--accent-soft); }}
  .wake-button:disabled {{ opacity: 0.6; cursor: default; }}
  /* min-width: 0 is what actually lets .mcp-command shrink below its
     content's natural width inside this flex row — a flex item's default
     min-width is auto (its unshrunk content size), so without this the row
     just overflowed the card instead of the command scrolling internally
     as intended, squeezing Copy half off the edge. */
  .mcp-install {{ display: flex; align-items: center; gap: 8px; min-width: 0; }}
  .mcp-command {{
    font-family: "JetBrains Mono", monospace; font-size: 0.78rem; background: var(--surface-sunken);
    color: var(--text); border: 1px solid var(--border); padding: 8px 12px; border-radius: 6px;
    white-space: nowrap; overflow-x: auto; max-width: 380px; flex: 1 1 auto; min-width: 0;
  }}
  .mcp-copy {{
    display: inline-flex; align-items: center; justify-content: center; gap: 6px; flex: none;
    font-family: "IBM Plex Sans", sans-serif; font-size: 0.78rem; font-weight: 600; padding: 8px 12px;
    border-radius: 6px; border: 1px solid var(--border); background: var(--surface);
    color: var(--accent); cursor: pointer; white-space: nowrap;
  }}
  .mcp-copy svg {{ flex: none; }}
  .mcp-copy:hover {{ background: var(--accent-soft); }}
  @media (max-width: 640px) {{
    /* Side-by-side left too little room for both the command and a legible
       Copy button on a phone-width card — stacked, the command gets the
       full card width (still scrolling internally if it's still too long
       for that) and Copy becomes a proper full-width tap target instead of
       a sliver squeezed against it. */
    .mcp-install {{ flex-direction: column; align-items: stretch; }}
    .mcp-command {{ max-width: 100%; }}
  }}
</style>
</head>
<body>
{routes_nav("search")}
<div class="page">
  <section class="hero" id="search">
    <h1>Search {stats['total_systems']} design systems</h1>
    <p class="hero-subtitle">Find component patterns, tokens, and guidance across every publicly documented design system indexed here — or connect it to your AI agent via MCP.</p>
    <form id="searchForm" action="/search" method="get">
      <input id="q" name="q" type="text" placeholder="e.g. table column resizing, disabled button states...">
    </form>
  </section>

  <div class="nav-cards">
    <a class="nav-card textured" href="/directory">
      <span class="nav-card-icon">{GRID_ICON_SVG_LARGE}</span>
      <span class="nav-card-text">
        <h3>Browse all systems</h3>
        <p>Every indexed design system, as a sortable list or grid.</p>
      </span>
      <span class="nav-card-arrow">&rarr;</span>
    </a>
    <a class="nav-card textured" href="/components">
      <span class="nav-card-icon">{PACKAGE_ICON_SVG}</span>
      <span class="nav-card-text">
        <h3>Browse by component</h3>
        <p>See which systems have documented a given UI component.</p>
      </span>
      <span class="nav-card-arrow">&rarr;</span>
    </a>
    <a class="nav-card textured" href="/patterns">
      <span class="nav-card-icon">{PACKAGE_ICON_SVG}</span>
      <span class="nav-card-text">
        <h3>Browse by pattern</h3>
        <p>Task-level compositions built from several components.</p>
      </span>
      <span class="nav-card-arrow">&rarr;</span>
    </a>
    <a class="nav-card textured" href="/foundations">
      <span class="nav-card-icon">{PACKAGE_ICON_SVG}</span>
      <span class="nav-card-text">
        <h3>Browse by foundation</h3>
        <p>System-wide design principles that aren't components.</p>
      </span>
      <span class="nav-card-arrow">&rarr;</span>
    </a>
  </div>

  <div class="mcp-bar textured">
    <div class="mcp-card-text">
      <span class="mcp-badge">MCP Server</span>
      <h2>Connect this to Claude (or any MCP-compatible client)</h2>
      <p>One command adds this whole index as a tool your agent can call directly.</p>
      <p class="mcp-status" id="mcpStatus">
        <span class="status-dot checking"></span>
        <span class="status-text">Checking server status…</span>
        <button type="button" class="wake-button" id="wakeButton" style="visibility: hidden" disabled>Wake it up</button>
      </p>
    </div>
    <div class="mcp-install">
      <code class="mcp-command" id="mcpCommand">{MCP_INSTALL_COMMAND}</code>
      <button class="mcp-copy" id="mcpCopy" type="button">{COPY_ICON_SVG}<span id="mcpCopyLabel">Copy</span></button>
    </div>
  </div>
</div>

<script>
  const HEALTH_URL = {json.dumps(HEALTH_API_URL)};

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
  // Queried once — every state (checking/awake/sleeping/waking) reuses these
  // same three elements rather than replacing mcpStatusEl's innerHTML, so the
  // row's structure (and therefore its height) never changes between states;
  // setStatus() below only ever edits their class/text/visibility.
  const statusDotEl = mcpStatusEl.querySelector(".status-dot");
  const statusTextEl = mcpStatusEl.querySelector(".status-text");
  const wakeButtonEl = document.getElementById("wakeButton");
  let healthPollTimer = null;
  let waking = false;

  wakeButtonEl.addEventListener("click", () => wakeServer());

  function relativeTime(isoString) {{
    const diffMin = Math.round((Date.now() - new Date(isoString).getTime()) / 60000);
    if (diffMin < 1) return "just now";
    if (diffMin < 60) return `${{diffMin}}m ago`;
    const diffHr = Math.round(diffMin / 60);
    if (diffHr < 24) return `${{diffHr}}h ago`;
    return `${{Math.round(diffHr / 24)}}d ago`;
  }}

  // dotClass: "awake" | "sleeping" | "waking". showButton: whether the wake
  // button should actually be usable right now (it's only ever hidden via
  // `visibility`, never removed, so it keeps its layout slot regardless).
  function setStatus(dotClass, text, showButton) {{
    statusDotEl.className = `status-dot ${{dotClass}}`;
    statusTextEl.textContent = text;
    wakeButtonEl.style.visibility = showButton ? "visible" : "hidden";
    wakeButtonEl.disabled = !showButton;
  }}

  function renderMcpStatus(awake) {{
    if (awake) {{
      setStatus("awake", "MCP server awake", false);
      return;
    }}
    let lastAwake = null;
    try {{ lastAwake = localStorage.getItem("mcp-last-awake"); }} catch (err) {{ /* private browsing etc. */ }}
    const lastText = lastAwake ? ` (last awake ${{relativeTime(lastAwake)}})` : "";
    setStatus("sleeping", `MCP server sleeping — takes ~30-60s to wake up${{lastText}}`, true);
  }}

  async function checkMcpHealth() {{
    if (waking) return; // wakeServer() owns mcpStatusEl until it finishes
    try {{
      const res = await fetch(HEALTH_URL, {{ signal: AbortSignal.timeout(3000) }});
      if (!res.ok) throw new Error("not ok");
      try {{ localStorage.setItem("mcp-last-awake", new Date().toISOString()); }} catch (err) {{ /* fine */ }}
      renderMcpStatus(true);
    }} catch (err) {{
      renderMcpStatus(false);
    }}
  }}

  // A single 3s-timeout health check (fine for the background poll) isn't
  // enough to tell whether a click actually did anything — Render's real
  // cold start is 30-60s, so the first probe after clicking almost always
  // still fails and renderMcpStatus(false) would immediately overwrite the
  // button with an identical-looking "sleeping" state, making the click feel
  // like a no-op. Wake gets its own persistent, visibly-different state
  // (pulsing dot, explicit "waking" copy) that survives across several
  // retries instead of being wiped out by the first failed poll.
  async function wakeServer() {{
    if (waking) return;
    waking = true;
    setStatus("waking", "Waking the server up… (usually takes 30-60s)", false);

    const deadline = Date.now() + 75000;
    while (Date.now() < deadline) {{
      try {{
        const res = await fetch(HEALTH_URL, {{ signal: AbortSignal.timeout(5000) }});
        if (res.ok) {{
          try {{ localStorage.setItem("mcp-last-awake", new Date().toISOString()); }} catch (err) {{ /* fine */ }}
          waking = false;
          renderMcpStatus(true);
          return;
        }}
      }} catch (err) {{ /* still waking — keep polling until the deadline */ }}
      await new Promise(resolve => setTimeout(resolve, 4000));
    }}
    waking = false;
    setStatus("sleeping", "Still not responding — it may need another try.", false);
    setTimeout(() => renderMcpStatus(false), 2500);
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

  // Old links/bookmarks may still point at "/?q=..." (the nav's mini-search
  // used to run the search right here) — forward them to the real results
  // page instead of silently ignoring the query.
  const urlQuery = new URLSearchParams(window.location.search).get("q");
  if (urlQuery) {{
    window.location.replace("/search?q=" + encodeURIComponent(urlQuery));
  }}
</script>
</body>
</html>
"""


if __name__ == "__main__":
    systems = indexed_only(load_systems())
    OUTPUT_FILE.write_text(render_page(systems))
    print(f"Wrote {OUTPUT_FILE} with stats for {len(systems)} systems.")
