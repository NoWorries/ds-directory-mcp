"""
Shared visual tokens and chrome for every generated page (home.html,
directory.html, components/*.html, systems/*.html) — one definition so all
four pages share the same width, the same fixed top nav bar (with its
collapsed search-icon-to-input), and the same design tokens, and so
`@view-transition` is declared consistently for smooth cross-document
navigation between them (Chrome/Edge; other browsers just navigate normally —
this is a progressive enhancement, not a dependency).
"""

FONT_LINK = (
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@500;700&'
    'family=IBM+Plex+Sans:wght@400;500;600&display=swap">'
)

# One width for every page — a table-heavy page just scrolls its own
# .table-wrap horizontally rather than getting a special wider page.
PAGE_MAX_WIDTH = "1040px"
NAV_HEIGHT = "60px"

TOKENS_CSS = f"""
  @view-transition {{ navigation: auto; }}

  :root {{
    --bg: #ffffff; --surface: #ffffff; --surface-sunken: #f4f6f8; --border: #e2e6ea;
    --text: #12151a; --text-muted: #5b6472; --text-faint: #9aa3af;
    --accent: #1d4ed8; --accent-strong: #1638a8; --accent-soft: #e8edfc; --accent-soft-text: #1d4ed8;
    --shadow: 0 1px 2px rgba(15,23,42,0.04), 0 6px 18px -10px rgba(15,23,42,0.14);
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #0d1117; --surface: #151a21; --surface-sunken: #1b212a; --border: #2a323d;
      --text: #e7eaee; --text-muted: #98a2b0; --text-faint: #626b78;
      --accent: #7ea2ff; --accent-strong: #a4c0ff; --accent-soft: rgba(126,162,255,0.12); --accent-soft-text: #a4c0ff;
      --shadow: 0 1px 2px rgba(0,0,0,0.4), 0 6px 18px -10px rgba(0,0,0,0.6);
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: "IBM Plex Sans", -apple-system, BlinkMacSystemFont, sans-serif;
    background: var(--bg); color: var(--text); margin: 0; padding: calc({NAV_HEIGHT} + 32px) 24px 80px;
  }}
  a {{ color: var(--accent); }}
  .page {{ max-width: {PAGE_MAX_WIDTH}; margin: 0 auto; }}

  .eyebrow {{
    font-family: "JetBrains Mono", monospace; font-size: 0.72rem; font-weight: 700;
    letter-spacing: 0.06em; text-transform: uppercase; color: var(--accent); margin: 0 0 12px;
  }}
  .eyebrow a {{ color: inherit; text-decoration: none; }}
  .eyebrow a:hover {{ text-decoration: underline; }}
  h1 {{
    font-family: "JetBrains Mono", monospace; font-weight: 700;
    font-size: clamp(1.4rem, 1.1rem + 1.2vw, 2rem); letter-spacing: -0.01em; line-height: 1.25;
    margin: 0 0 12px; text-wrap: balance;
  }}
  .subtitle {{ color: var(--text-muted); font-size: 0.98rem; line-height: 1.55; max-width: 64ch; margin: 0 0 28px; }}

  /* --- Fixed global top nav --- */
  .site-nav {{
    position: fixed; top: 0; left: 0; right: 0; height: {NAV_HEIGHT}; z-index: 100;
    background: var(--surface); border-bottom: 1px solid var(--border);
    display: flex; align-items: center;
  }}
  .site-nav-inner {{
    max-width: {PAGE_MAX_WIDTH}; margin: 0 auto; width: 100%; padding: 0 24px;
    display: flex; align-items: center; gap: 24px;
  }}
  .site-brand {{
    font-family: "JetBrains Mono", monospace; font-weight: 700; font-size: 0.86rem;
    color: var(--text); text-decoration: none; white-space: nowrap;
  }}
  .site-nav-links {{ display: flex; gap: 4px; flex: 1; }}
  .site-nav-links a {{
    font-size: 0.86rem; font-weight: 500; color: var(--text-muted); text-decoration: none;
    padding: 6px 12px; border-radius: 6px;
  }}
  .site-nav-links a:hover {{ background: var(--surface-sunken); color: var(--text); }}
  .site-nav-links a.current {{ color: var(--accent); background: var(--accent-soft); font-weight: 600; }}

  .nav-search {{ display: flex; align-items: center; }}
  .nav-search-toggle {{
    background: none; border: none; padding: 6px; cursor: pointer; color: var(--text-muted);
    display: flex; align-items: center; border-radius: 6px;
  }}
  .nav-search-toggle:hover {{ background: var(--surface-sunken); color: var(--text); }}
  /* Same [hidden]-vs-explicit-display fix needed a third time here — see the
     .cards-grid/.card comments in generate_directory.py for the full story. */
  .nav-search-form[hidden] {{ display: none; }}
  .nav-search-form {{ display: flex; align-items: center; }}
  .nav-search-form input {{
    width: 220px; font: inherit; font-size: 0.85rem; padding: 6px 10px; margin-left: 6px;
    border: 1px solid var(--border); border-radius: 6px; background: var(--surface-sunken); color: var(--text);
  }}
  .nav-search-form input:focus {{ outline: 2px solid var(--accent); outline-offset: 1px; background: var(--surface); }}
  @media (max-width: 640px) {{
    .site-nav-links a span {{ display: none; }}
    .nav-search-form input {{ width: 140px; }}
  }}
"""

SEARCH_ICON_SVG = (
    '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>'
)

# Submitting always navigates to "/?q=..." — the homepage reads that query
# param on load and runs the search immediately. Keeps the nav's mini-search
# simple and identical on every page, instead of duplicating full search
# results UI (API calls, rendering, restrict-filter) on every single page.
NAV_SEARCH_JS = """
(function () {
  var toggle = document.getElementById("navSearchToggle");
  var form = document.getElementById("navSearchForm");
  var input = document.getElementById("navSearchInput");
  if (!toggle || !form) return;
  toggle.addEventListener("click", function () {
    form.hidden = !form.hidden;
    if (!form.hidden) input.focus();
  });
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var q = input.value.trim();
    if (q) window.location.href = "/?q=" + encodeURIComponent(q);
  });
})();
"""


def routes_nav(current: str) -> str:
    """Root-absolute paths on purpose: whatever page is home always deploys as
    index.html — see the workflows' Netlify deploy step — so any relative or
    filename-literal link breaks once live. "/" always resolves to it
    correctly regardless of how deep the current page is nested.

    current: "search" (home), "directory" (the full list/grid), or "components".
    """
    return f"""
    <nav class="site-nav">
      <div class="site-nav-inner">
        <a href="/" class="site-brand">Design Systems Directory</a>
        <div class="site-nav-links">
          <a href="/directory.html" class="{'current' if current == 'directory' else ''}">Directory</a>
          <a href="/components/index.html" class="{'current' if current == 'components' else ''}">Components</a>
        </div>
        <div class="nav-search">
          <button type="button" class="nav-search-toggle" id="navSearchToggle" aria-label="Search">{SEARCH_ICON_SVG}</button>
          <form class="nav-search-form" id="navSearchForm" hidden>
            <input type="text" id="navSearchInput" placeholder="Search design systems...">
          </form>
        </div>
      </div>
    </nav>
    <script>{NAV_SEARCH_JS}</script>
    """
