"""
Shared visual tokens and chrome for every generated page (directory.html,
components/*.html, systems/*.html) — one definition so the three routes
through the dataset (system-first, component-first, search-first) stay
visually identical, and so `@view-transition` is declared consistently for
smooth cross-document navigation between them (Chrome/Edge; other browsers
just navigate normally — this is a progressive enhancement, not a dependency).
"""

FONT_LINK = (
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@500;700&'
    'family=IBM+Plex+Sans:wght@400;500;600&display=swap">'
)

# Everything except `.page` (each page sets its own max-width/reading measure).
TOKENS_CSS = """
  @view-transition { navigation: auto; }

  :root {
    --bg: #ffffff; --surface: #ffffff; --surface-sunken: #f4f6f8; --border: #e2e6ea;
    --text: #12151a; --text-muted: #5b6472; --text-faint: #9aa3af;
    --accent: #1d4ed8; --accent-strong: #1638a8; --accent-soft: #e8edfc; --accent-soft-text: #1d4ed8;
    --shadow: 0 1px 2px rgba(15,23,42,0.04), 0 6px 18px -10px rgba(15,23,42,0.14);
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #0d1117; --surface: #151a21; --surface-sunken: #1b212a; --border: #2a323d;
      --text: #e7eaee; --text-muted: #98a2b0; --text-faint: #626b78;
      --accent: #7ea2ff; --accent-strong: #a4c0ff; --accent-soft: rgba(126,162,255,0.12); --accent-soft-text: #a4c0ff;
      --shadow: 0 1px 2px rgba(0,0,0,0.4), 0 6px 18px -10px rgba(0,0,0,0.6);
    }
  }
  * { box-sizing: border-box; }
  body {
    font-family: "IBM Plex Sans", -apple-system, BlinkMacSystemFont, sans-serif;
    background: var(--bg); color: var(--text); margin: 0; padding: 48px 24px 80px;
  }
  a { color: var(--accent); }
  .eyebrow {
    font-family: "JetBrains Mono", monospace; font-size: 0.72rem; font-weight: 700;
    letter-spacing: 0.06em; text-transform: uppercase; color: var(--accent); margin: 0 0 12px;
  }
  .eyebrow a { color: inherit; text-decoration: none; }
  .eyebrow a:hover { text-decoration: underline; }
  h1 {
    font-family: "JetBrains Mono", monospace; font-weight: 700;
    font-size: clamp(1.4rem, 1.1rem + 1.2vw, 2rem); letter-spacing: -0.01em; line-height: 1.25;
    margin: 0 0 12px; text-wrap: balance;
  }
  .subtitle { color: var(--text-muted); font-size: 0.98rem; line-height: 1.55; max-width: 64ch; margin: 0 0 28px; }

  .routes { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 28px; font-size: 0.82rem; }
  .routes a {
    display: inline-block; padding: 6px 12px; border: 1px solid var(--border); border-radius: 999px;
    text-decoration: none; color: var(--text-muted);
  }
  .routes a.current { color: var(--accent); border-color: var(--accent); font-weight: 600; }
"""


def routes_nav(current: str, prefix: str = "") -> str:
    """prefix: relative path back to the repo root, e.g. "" from directory.html
    itself, "../" from components/*.html or systems/*.html."""
    return f"""
    <nav class="routes">
      <a href="{prefix}directory.html" class="{'current' if current == 'system' else ''}">System-first</a>
      <a href="{prefix}components/index.html" class="{'current' if current == 'component' else ''}">Component-first</a>
      <a href="{prefix}directory.html#search" class="{'current' if current == 'search' else ''}">Search</a>
    </nav>
    """
