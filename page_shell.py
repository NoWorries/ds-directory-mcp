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

# Root-absolute — see routes_nav()'s docstring for why every path here is
# (deploys as / regardless of how deep the current page is nested). SVG
# favicons render standalone in the browser tab/bookmark chrome, with no
# access to the page's own CSS custom properties, so favicon.svg hardcodes
# its stroke color directly rather than using currentColor + an external
# rule — there's no page for that rule to live on. Without this at all,
# every page fell back to the browser's own generic default tab icon
# (blurry at the small size browsers render a favicon).
FAVICON_LINK = '<link rel="icon" type="image/svg+xml" href="/favicon.svg">'

# One width for every page — a table-heavy page just scrolls its own
# .table-wrap horizontally rather than getting a special wider page.
PAGE_MAX_WIDTH = "1040px"
NAV_HEIGHT = "60px"

# Real, no-login pages (see generate_forms.py) — not the GitHub Issue Forms
# these used to point at directly, which required the visitor to have their
# own GitHub account. Both POST to netlify/functions/submit-form.js, which
# files the actual GitHub issue on a bot account instead.
SUBMISSION_URL = "/suggest"
REPORT_ISSUE_URL = "/report"

TOKENS_CSS = f"""
  @view-transition {{ navigation: auto; }}

  /* Named transition (view-transition-name: thumb-<slug>, tagged
     view-transition-class: thumb — set inline in generate_directory.py's
     render_card/generate_systems.py's thumb_html, since the name itself must
     be unique per element but the *class* lets every card share one
     animation rule) gets a slightly slower, eased morph than the default
     page crossfade, so a card's thumbnail visibly glides into the detail
     page's hero image instead of just cutting; everything else keeps the
     plain default root crossfade, intentionally quick. (A matching
     title-<slug> transition on the heading was tried and dropped — morphing
     text at a different size/weight read as glitchy, not smooth.)
     Chrome/Edge only (view-transition-class needs Chrome 125+) — a no-op
     elsewhere, same as @view-transition itself. */
  ::view-transition-group(*) {{ animation-duration: 0.3s; }}
  ::view-transition-group(*.thumb) {{
    animation-duration: 0.45s; animation-timing-function: cubic-bezier(0.2, 0, 0, 1);
  }}

  :root {{
    --bg: #ffffff; --surface: #ffffff; --surface-sunken: #f4f6f8; --border: #e2e6ea;
    --text: #12151a; --text-muted: #5b6472; --text-faint: #9aa3af;
    --accent: #1d4ed8; --accent-strong: #1638a8; --accent-soft: #e8edfc; --accent-soft-text: #1d4ed8;
    --shadow: 0 1px 2px rgba(15,23,42,0.04), 0 6px 18px -10px rgba(15,23,42,0.14);
    /* Faint fractal-noise grain (alpha-only, so it reads as texture rather
       than color) — the shared look for .textured containers below. Kept as
       a token so light/dark could each tune it without touching every
       container's markup. */
    --noise: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='180' height='180'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' stitchTiles='stitch'/%3E%3CfeColorMatrix type='matrix' values='0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0.05 0'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");
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
  /* A faint grain treatment for card-like containers (the MCP callout, nav
     cards, grid cards) — applied via ::before so it layers over each
     container's own background without fighting its other background-image
     uses (e.g. a card's own thumbnail). Needs the container to have
     `position: relative` (each usage sets it) and `overflow: hidden`
     (usually already there) so it respects rounded corners instead of
     bleeding past them. */
  .textured {{ position: relative; }}
  .textured::before {{
    content: ""; position: absolute; inset: 0; pointer-events: none; border-radius: inherit;
    background-image: var(--noise);
    background-repeat: repeat;
    opacity: 0.7;
  }}
  .textured > * {{ position: relative; }}
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
  /* Two text variants swapped by viewport rather than one truncated/wrapped
     string — "Design Systems Directory" is the real name everywhere there's
     room for it; only the mobile bar (already tight with search+menu on the
     same row) gets the shortened form. Both live in the DOM at once but
     only one is ever visible, so a screen reader announces exactly one
     (the other is display:none, which removes it from the accessibility
     tree — not a duplicate-announcement risk). */
  .site-brand-short {{ display: none; }}
  /* NAV_OVERFLOW_JS's own, width-independent trigger for the same swap —
     shortening the title is the first, least disruptive space-saving move
     it tries whenever the nav doesn't fit, before it ever touches Suggest
     or collapses a route into the "More" menu. Separate from (and additive
     to) the phone-only media query below, which still applies its own
     floor regardless of what JS decides. */
  .site-nav-inner.brand-compact .site-brand-full {{ display: none; }}
  .site-nav-inner.brand-compact .site-brand-short {{ display: inline; }}
  /* nowrap rather than wrapping to a second line — a wrapped ragged row was
     the original bug report here. NAV_OVERFLOW_JS keeps this from actually
     overflowing: it measures whenever the row doesn't fit and moves the
     lowest-priority route link into the "More" dropdown one at a time until
     what's left fits on one line. Deliberately NOT `overflow: hidden` here
     even as a pre-JS safety net — the "More" dropdown menu below is a
     descendant of this element, and hidden overflow on an ancestor clips an
     absolutely-positioned descendant's paint regardless of its own
     position/containing-block, which made the open dropdown invisible.
     min-width: 0 overrides the flex default of `auto`, which otherwise
     refuses to shrink a flex item below its content's own min-content size
     — with several nowrap links inside, that min-content size is the full
     unwrapped width of every link combined, so without this override the
     row never actually shrank at all (scrollWidth == clientWidth always),
     and NAV_OVERFLOW_JS's overflow check never had anything real to catch. */
  .site-nav-links {{ display: flex; flex-wrap: nowrap; align-items: center; gap: 4px; flex: 1; min-width: 0; }}
  .site-nav-links a {{
    font-size: 0.86rem; font-weight: 500; color: var(--text-muted); text-decoration: none;
    padding: 6px 12px; border-radius: 6px; white-space: nowrap;
  }}
  .site-nav-links a:hover {{ background: var(--surface-sunken); color: var(--text); }}
  .site-nav-links a.current {{ color: var(--accent); background: var(--accent-soft); font-weight: 600; }}
  /* "Suggest a system" lives in the same link list as the routes (so the
     mobile menu below doesn't need a second, separately-toggled element)
     but keeps its own accent treatment so it still reads as a distinct
     call-to-action rather than one more route. */
  .site-nav-links a.nav-suggest {{
    display: inline-flex; align-items: center; gap: 5px;
    color: var(--accent); font-weight: 600; margin-left: auto;
  }}
  .site-nav-links a.nav-suggest svg {{ flex: none; }}
  /* Second space-saving move NAV_OVERFLOW_JS tries (after shortening the
     title, before collapsing any route into "More") — the label hides but
     the link keeps its aria-label, so the CTA stays a real, accessible
     target at every width, just icon-only when space is tight. */
  .site-nav-links a.nav-suggest.compact .nav-suggest-label {{ display: none; }}
  /* Deliberately the same neutral hover as every other link (not
     accent-soft) — accent-soft is reserved for .current below, so hovering
     Suggest never reads as "this is the page you're on" the way it would if
     hover and current shared a background. */
  .site-nav-links a.nav-suggest:hover {{ background: var(--surface-sunken); }}

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

  /* Hamburger toggle — hidden entirely above the mobile breakpoint, where
     .site-nav-links already fits inline. Fixed box size (rather than sizing
     to whichever icon is currently showing) matters here: the menu glyph
     and the close glyph aren't the same size, so without a fixed box the
     button itself resized by a couple of px on toggle — and since it's the
     last item in a row right-aligned via nav-search's margin-left:auto,
     that resize shifted the search icon beside it on every open/close.
     Both icon layers are stacked via position:absolute inset:0 inside that
     fixed box so swapping which one is visible never changes the box. */
  .nav-menu-toggle {{
    display: none; position: relative; width: 32px; height: 32px; background: none; border: none;
    padding: 0; margin-left: 4px; cursor: pointer; color: var(--text-muted); border-radius: 6px; flex: none;
  }}
  .nav-menu-toggle:hover {{ background: var(--surface-sunken); color: var(--text); }}
  .nav-menu-toggle .icon-menu, .nav-menu-toggle .icon-close {{
    position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
  }}
  .nav-menu-toggle .icon-close {{ display: none; }}
  .nav-menu-toggle[aria-expanded="true"] .icon-menu {{ display: none; }}
  .nav-menu-toggle[aria-expanded="true"] .icon-close {{ display: flex; }}

  /* --- "More" overflow menu (see NAV_OVERFLOW_JS) — the in-between state
     for widths too narrow to fit every route inline but not phone-narrow
     enough to need the full hamburger sheet below. Hidden by default (both
     the [hidden] attribute and having nothing in the menu yet); JS reveals
     it only once it's actually moved a link in. */
  .nav-more {{ position: relative; }}
  .nav-more-toggle {{
    display: inline-flex; align-items: center; gap: 4px; font: inherit; font-size: 0.86rem; font-weight: 500;
    color: var(--text-muted); background: none; border: none; padding: 6px 12px; border-radius: 6px;
    cursor: pointer; white-space: nowrap;
  }}
  .nav-more-toggle:hover {{ background: var(--surface-sunken); color: var(--text); }}
  /* Reuses the same chevron as everywhere else on the site (see .name-chevron
     in generate_directory.py) rotated to point down at rest, up when open —
     one icon asset, two orientations, instead of a second glyph. */
  .nav-more-toggle svg {{ flex: none; transform: rotate(90deg); transition: transform 0.15s; }}
  .nav-more-toggle[aria-expanded="true"] svg {{ transform: rotate(270deg); }}
  .nav-more-menu {{
    display: none; position: absolute; top: 100%; left: 0; margin-top: 4px; min-width: 170px;
    flex-direction: column; gap: 2px; background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; box-shadow: var(--shadow); padding: 6px; z-index: 110;
  }}
  .nav-more-menu.open {{ display: flex; }}
  .nav-more-menu a {{
    padding: 8px 10px; border-radius: 6px; font-size: 0.86rem; font-weight: 500;
    color: var(--text-muted); text-decoration: none; white-space: nowrap;
  }}
  .nav-more-menu a:hover {{ background: var(--surface-sunken); color: var(--text); }}
  .nav-more-menu a.current {{ color: var(--accent); background: var(--accent-soft); font-weight: 600; }}

  /* Collapsed into a hamburger only below this TRUE phone-narrow width.
     Above it (see NAV_OVERFLOW_JS), a route link that no longer fits inline
     moves into the "More" dropdown above one at a time instead — so getting
     narrower on a tablet/small-laptop first thins out the inline row, and
     only falls back to the full mobile sheet once there's no longer room
     for even brand + search + one route + Suggest. Below this width the
     link list itself becomes the toggled panel: hidden by default, dropped
     down as a full-width sheet under the fixed bar once opened. */
  @media (max-width: 640px) {{
    .nav-menu-toggle {{ display: flex; }}
    .site-nav-links {{
      display: none; position: absolute; top: 100%; left: 0; right: 0;
      flex-direction: column; flex-wrap: nowrap; align-items: stretch; gap: 2px; margin: 0;
      background: var(--surface); border-bottom: 1px solid var(--border);
      box-shadow: var(--shadow); padding: 8px 16px 14px; max-height: calc(100vh - {NAV_HEIGHT});
      overflow-y: auto;
    }}
    .site-nav-links.nav-open {{ display: flex; }}
    .site-nav-links a {{ padding: 11px 10px; font-size: 0.95rem; }}
    /* Still visually separated from the plain routes above it (margin-top +
       border-top divider) but keeps the same rounded-pill shape as every
       other item — an unrounded, edge-to-edge row here read as a leftover
       fragment of a different component, not an intentional design. */
    .site-nav-links a.nav-suggest {{ margin-left: 0; margin-top: 6px; border-top: 1px solid var(--border); padding-top: 14px; }}
    /* The "More" button never applies at this width (NAV_OVERFLOW_JS bails
       out and restores every link to the flat sheet whenever this same
       breakpoint matches), so it never needs its own mobile styling. */
    .nav-more {{ display: none; }}
    /* .site-nav-links no longer occupies flex:1 once it's the hidden/
       toggled panel above, so without this, search+menu just sat right
       after the brand text with a dead gap stretching to the bar's far edge
       instead of anchoring there — margin-left: auto on the first remaining
       flex item claims all that leftover space for itself, pushing
       everything from here on right-aligned as a group. */
    .nav-search {{ margin-left: auto; }}
    /* The wide desktop inter-item gap (used for spacing out the full link
       row) is much too loose for a two-icon cluster once the link list
       stops being an in-flow sibling here — this is what actually put
       daylight between the search and menu icons, not their own margins. */
    .site-nav-inner {{ gap: 8px; }}
    .nav-menu-toggle {{ margin-left: 0; }}
    .nav-search-form input {{ width: 140px; }}
    .site-brand-full {{ display: none; }}
    .site-brand-short {{ display: inline; }}
  }}
"""

# Feather icons (github.com/feathericons/feather), inlined so the site has no
# icon-font/CDN dependency — the "class=feather..." attribute Feather ships
# with is dropped since nothing here relies on it. width/height set per usage
# site rather than baked in, so the same markup can be reused at different sizes.
def _icon(path_markup: str, size: int = 18, stroke_width: int = 2) -> str:
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        f'stroke-width="{stroke_width}" stroke-linecap="round" stroke-linejoin="round">{path_markup}</svg>'
    )


SEARCH_ICON_SVG = _icon('<circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line>')
GRID_ICON_SVG = _icon(
    '<rect x="3" y="3" width="7" height="7"></rect><rect x="14" y="3" width="7" height="7"></rect>'
    '<rect x="14" y="14" width="7" height="7"></rect><rect x="3" y="14" width="7" height="7"></rect>',
    size=15,
)
LIST_ICON_SVG = _icon(
    '<line x1="8" y1="6" x2="21" y2="6"></line><line x1="8" y1="12" x2="21" y2="12"></line>'
    '<line x1="8" y1="18" x2="21" y2="18"></line><line x1="3" y1="6" x2="3.01" y2="6"></line>'
    '<line x1="3" y1="12" x2="3.01" y2="12"></line><line x1="3" y1="18" x2="3.01" y2="18"></line>',
    size=15,
)
COPY_ICON_SVG = _icon(
    '<rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>'
    '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>',
    size=14,
)
FILTER_ICON_SVG = _icon('<polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"></polygon>', size=14)
CHECK_ICON_SVG = _icon('<polyline points="20 6 9 17 4 12"></polyline>', size=15, stroke_width=3)
PACKAGE_ICON_SVG = _icon(
    '<line x1="16.5" y1="9.4" x2="7.5" y2="4.21"></line>'
    '<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path>'
    '<polyline points="3.27 6.96 12 12.01 20.73 6.96"></polyline><line x1="12" y1="22.08" x2="12" y2="12"></line>',
    size=20,
)
EXTERNAL_LINK_ICON_SVG = _icon(
    '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path>'
    '<polyline points="15 3 21 3 21 9"></polyline><line x1="10" y1="14" x2="21" y2="3"></line>',
    size=13,
)
CHEVRON_RIGHT_ICON_SVG = _icon('<polyline points="9 18 15 12 9 6"></polyline>', size=16)
CLOSE_ICON_SVG = _icon('<line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line>', size=18)
MENU_ICON_SVG = _icon(
    '<line x1="3" y1="6" x2="21" y2="6"></line><line x1="3" y1="12" x2="21" y2="12"></line>'
    '<line x1="3" y1="18" x2="21" y2="18"></line>',
    size=20,
)
FLAG_ICON_SVG = _icon(
    '<path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"></path>'
    '<line x1="4" y1="22" x2="4" y2="15"></line>',
    size=16,
)
PLUS_ICON_SVG = _icon('<line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line>', size=15)
# Generic outline version — kept as a fallback/for any future use, but
# generate_systems.py's resource list uses the real brand mark
# (FIGMA_LOGO_SVG below) instead, the same as GitHub/npm/Storybook/MCP.
FIGMA_ICON_SVG = _icon(
    '<path d="M5 5.5A3.5 3.5 0 0 1 8.5 2H12v7H8.5A3.5 3.5 0 0 1 5 5.5z"></path>'
    '<path d="M12 2h3.5a3.5 3.5 0 1 1 0 7H12V2z"></path>'
    '<path d="M12 12.5a3.5 3.5 0 1 1 7 0 3.5 3.5 0 1 1-7 0z"></path>'
    '<path d="M5 19.5A3.5 3.5 0 0 1 8.5 16H12v3.5a3.5 3.5 0 1 1-7 0z"></path>'
    '<path d="M5 12.5A3.5 3.5 0 0 1 8.5 9H12v7H8.5A3.5 3.5 0 0 1 5 12.5z"></path>',
    size=15,
)


def _logo(inner_markup: str, viewbox: str, height: int, fill_rule: bool = False) -> str:
    """A solid-color BRAND mark (GitHub octocat, npm block, Storybook,
    Claude, MCP) — deliberately NOT built through _icon()'s stroke/
    currentColor convention like every other icon here: these carry their
    own real, recognizable brand colors (npm red, Storybook pink, Claude's
    terracotta), which is the whole point of using the real logo instead of
    a generic outline glyph, so nothing here should end up recolored by the
    surrounding link's color or the light/dark theme. width is derived from
    the source's own aspect ratio so each mark keeps its true proportions
    rather than getting squashed into a uniform square like the line icons."""
    vb_parts = viewbox.split()
    width = round(height * (float(vb_parts[2]) / float(vb_parts[3])))
    return f'<svg width="{width}" height="{height}" viewBox="{viewbox}" xmlns="http://www.w3.org/2000/svg">{inner_markup}</svg>'


GITHUB_LOGO_SVG = _logo(
    '<path d="M128.00106,0 C57.3172926,0 0,57.3066942 0,128.00106 C0,184.555281 36.6761997,232.535542 87.534937,249.460899 '
    'C93.9320223,250.645779 96.280588,246.684165 96.280588,243.303333 C96.280588,240.251045 96.1618878,230.167899 96.106777,219.472176 '
    'C60.4967585,227.215235 52.9826207,204.369712 52.9826207,204.369712 C47.1599584,189.574598 38.770408,185.640538 38.770408,185.640538 '
    'C27.1568785,177.696113 39.6458206,177.859325 39.6458206,177.859325 C52.4993419,178.762293 59.267365,191.04987 59.267365,191.04987 '
    'C70.6837675,210.618423 89.2115753,204.961093 96.5158685,201.690482 C97.6647155,193.417512 100.981959,187.77078 104.642583,184.574357 '
    'C76.211799,181.33766 46.324819,170.362144 46.324819,121.315702 C46.324819,107.340889 51.3250588,95.9223682 59.5132437,86.9583937 '
    'C58.1842268,83.7344152 53.8029229,70.715562 60.7532354,53.0843636 C60.7532354,53.0843636 71.5019501,49.6441813 95.9626412,66.2049595 '
    'C106.172967,63.368876 117.123047,61.9465949 128.00106,61.8978432 C138.879073,61.9465949 149.837632,63.368876 160.067033,66.2049595 '
    'C184.49805,49.6441813 195.231926,53.0843636 195.231926,53.0843636 C202.199197,70.715562 197.815773,83.7344152 196.486756,86.9583937 '
    'C204.694018,95.9223682 209.660343,107.340889 209.660343,121.315702 C209.660343,170.478725 179.716133,181.303747 151.213281,184.472614 '
    'C155.80443,188.444828 159.895342,196.234518 159.895342,208.176593 C159.895342,225.303317 159.746968,239.087361 159.746968,243.303333 '
    'C159.746968,246.709601 162.05102,250.70089 168.53925,249.443941 C219.370432,232.499507 256,184.536204 256,128.00106 '
    'C256,57.3066942 198.691187,0 128.00106,0 Z" fill="#161614"></path>',
    "0 0 256 250", height=16,
)
NPM_LOGO_SVG = _logo(
    '<path d="M157.538462,164.102564 L223.179487,164.102564 L223.179487,131.282051 L288.820513,131.282051 L288.820513,0 '
    'L157.538462,0 L157.538462,164.102564 Z M223.179487,32.8205128 L256,32.8205128 L256,98.4615385 L223.179487,98.4615385 '
    'L223.179487,32.8205128 Z M315.076923,0 L315.076923,131.282051 L380.717949,131.282051 L380.717949,32.8205128 '
    'L413.538462,32.8205128 L413.538462,131.282051 L446.358974,131.282051 L446.358974,32.8205128 L479.179487,32.8205128 '
    'L479.179487,131.282051 L512,131.282051 L512,0 L315.076923,0 Z M0,131.282051 L65.6410256,131.282051 L65.6410256,32.8205128 '
    'L98.4615385,32.8205128 L98.4615385,131.282051 L131.282051,131.282051 L131.282051,0 L0,0 L0,131.282051 Z" fill="#C12127"></path>',
    "0 0 512 165", height=13,
)
MCP_LOGO_SVG = _logo(
    '<path d="M184.912588,14.4984503 C196.363516,25.9487671 201.031836,41.6132025 198.917593,56.499546 '
    'C213.803648,54.3850952 229.469497,59.054364 240.923381,70.5077614 L241.500743,71.085945 '
    'C260.833056,90.4174329 260.833056,121.760183 241.500921,141.091287 L140.199441,242.393592 '
    'C138.910969,243.681981 138.910969,245.770603 140.199887,247.059438 L161.000619,267.861491 '
    'C164.866781,271.727898 164.866582,277.996382 161.000175,281.862544 C157.133769,285.728705 150.865284,285.728506 146.999123,281.862099 '
    'L126.198836,261.060493 C117.177199,252.039433 117.177199,237.413151 126.198584,228.392344 L227.500027,127.090076 '
    'C239.099324,115.491398 239.099324,96.6861357 227.494887,85.0821908 L226.917525,84.5040072 '
    'C215.462946,73.0499167 196.981217,72.9067406 185.351143,84.0742949 L184.912802,84.5038305 L100.323674,169.093619 '
    'C96.4574047,172.959918 90.1889199,172.959943 86.3226209,169.093674 C82.456322,165.227405 82.4562975,158.95892 86.3225663,155.092621 '
    'L170.911872,70.5026559 C182.511206,58.9038165 182.511206,40.0985546 170.911498,28.4994655 '
    'C159.313616,16.900841 140.508239,16.900841 128.909262,28.4996948 L16.9007603,140.508031 '
    'C13.0344736,144.374312 6.76598883,144.374308 2.89970777,140.508021 C-0.966573285,136.641734 -0.966568668,130.373249 2.89971809,126.506968 '
    'L114.908252,14.4985997 C134.239899,-4.83284167 165.582534,-4.83284167 184.912588,14.4984503 Z M156.911612,42.5006526 '
    'C160.777889,46.3669442 160.777876,52.6354289 156.911585,56.5017051 L74.0716022,139.341358 '
    'C62.4729435,150.939769 62.4729435,169.745376 74.0714528,181.344504 C85.6703838,192.942693 104.476251,192.942693 116.074944,181.344742 '
    'L198.914535,98.5048208 C202.780811,94.6385292 209.049296,94.6385167 212.915588,98.5047929 '
    'C216.781879,102.371069 216.781892,108.639554 212.915616,112.505845 L130.075786,195.346005 '
    'C110.74439,214.676164 79.4022447,214.676164 60.0704376,195.345594 C40.7393604,176.013486 40.7393604,144.671082 60.0706383,125.340216 '
    'L142.91056,42.5006247 C146.776851,38.6343485 153.045336,38.634361 156.911612,42.5006526 Z" fill="currentColor"></path>',
    "0 0 256 285", height=16,
)
CLAUDE_LOGO_SVG = _logo(
    '<path d="M50.2278481,170.321013 L100.585316,142.063797 L101.427848,139.601013 L100.585316,138.24 L98.1225316,138.24 '
    'L89.6972152,137.721519 L60.921519,136.943797 L35.9696203,135.906835 L11.795443,134.610633 L5.70329114,133.31443 '
    'L0,125.796456 L0.583291139,122.037468 L5.70329114,118.602532 L13.0268354,119.250633 L29.2293671,120.352405 '
    'L53.5331646,122.037468 L71.161519,123.07443 L97.28,125.796456 L101.427848,125.796456 L102.011139,124.111392 '
    'L100.585316,123.07443 L99.4835443,122.037468 L74.3372152,104.992405 L47.116962,86.9751899 L32.8587342,76.6055696 '
    'L25.1463291,71.3559494 L21.2577215,66.4303797 L19.5726582,55.6718987 L26.5721519,47.9594937 L35.9696203,48.6075949 '
    'L38.3675949,49.2556962 L47.8946835,56.5792405 L68.2450633,72.3281013 L94.8172152,91.9007595 L98.7058228,95.1412658 '
    'L100.261266,94.0394937 L100.455696,93.2617722 L98.7058228,90.3453165 L84.2531646,64.2268354 L68.8283544,37.6546835 '
    'L61.958481,26.636962 L60.1437975,20.0263291 C59.4956962,17.3043038 59.0420253,15.0359494 59.0420253,12.2491139 '
    'L67.0136709,1.42582278 L71.4207595,0 L82.0496203,1.42582278 L86.521519,5.31443038 L93.1321519,20.4151899 '
    'L103.825823,44.2005063 L120.417215,76.5407595 L125.277975,86.1326582 L127.87038,95.0116456 L128.842532,97.7336709 '
    'L130.527595,97.7336709 L130.527595,96.1782278 L131.888608,77.9665823 L134.416203,55.6070886 L136.878987,26.8313924 '
    'L137.721519,18.7301266 L141.739747,9.00860759 L149.711392,3.75898734 L155.933165,6.74025316 L161.053165,14.0637975 '
    'L160.340253,18.7949367 L157.294177,38.5620253 L151.331646,69.5412658 L147.443038,90.2805063 L149.711392,90.2805063 '
    'L152.303797,87.6881013 L162.803038,73.7539241 L180.431392,51.718481 L188.208608,42.9691139 L197.282025,33.3124051 '
    'L203.114937,28.7108861 L214.132658,28.7108861 L222.233924,40.7655696 L218.604557,53.2091139 L207.262785,67.596962 '
    'L197.865316,79.7812658 L184.38481,97.9281013 L175.959494,112.44557 L176.737215,113.612152 L178.746329,113.417722 '
    'L209.207089,106.936709 L225.668861,103.955443 L245.306329,100.585316 L254.185316,104.733165 L255.157468,108.945823 '
    'L251.657722,117.56557 L230.659241,122.75038 L206.031392,127.675949 L169.348861,136.360506 L168.89519,136.684557 '
    'L169.413671,137.332658 L185.940253,138.888101 L193.004557,139.276962 L210.308861,139.276962 L242.519494,141.674937 '
    'L250.94481,147.248608 L256,154.053671 L255.157468,159.238481 L242.195443,165.849114 L224.696709,161.701266 '
    'L183.866329,151.979747 L169.867342,148.48 L167.923038,148.48 L167.923038,149.646582 L179.588861,161.053165 '
    'L200.976203,180.366582 L227.742785,205.253671 L229.103797,211.410633 L225.668861,216.271392 L222.039494,215.752911 '
    'L198.513418,198.059747 L189.44,190.088101 L168.89519,172.783797 L167.534177,172.783797 L167.534177,174.598481 '
    'L172.265316,181.533165 L197.282025,219.123038 L198.578228,230.659241 L196.763544,234.418228 L190.282532,236.686582 '
    'L183.153418,235.39038 L168.506329,214.84557 L153.40557,191.708354 L141.221266,170.969114 L139.730633,171.811646 '
    'L132.536709,249.259747 L129.166582,253.213165 L121.389367,256.19443 L114.908354,251.268861 L111.473418,243.297215 '
    'L114.908354,227.548354 L119.056203,207.003544 L122.426329,190.671392 L125.472405,170.385823 L127.287089,163.64557 '
    'L127.157468,163.191899 L125.666835,163.386329 L110.371646,184.38481 L87.1048101,215.817722 L68.6987342,235.52 '
    'L64.2916456,237.269873 L56.6440506,233.316456 L57.356962,226.252152 L61.6344304,219.96557 L87.1048101,187.560506 '
    'L102.46481,167.469367 L112.380759,155.868354 L112.315949,154.183291 L111.732658,154.183291 L44.0708861,198.124557 '
    'L32.0162025,199.68 L26.8313924,194.819241 L27.4794937,186.847595 L29.9422785,184.25519 L50.2926582,170.256203 '
    'L50.2278481,170.321013 Z" fill="#D97757"></path>',
    "0 0 256 257", height=16,
)
STORYBOOK_LOGO_SVG = _logo(
    '<defs><path d="M9.87245893,293.324145 L0.0114611411,30.5732167 C-0.314208957,21.8955842 6.33948896,14.5413918 '
    '15.0063196,13.9997149 L238.494389,0.0317105427 C247.316188,-0.519651867 254.914637,6.18486163 255.466,15.0066607 '
    'C255.486773,15.339032 255.497167,15.6719708 255.497167,16.0049907 L255.497167,302.318596 '
    'C255.497167,311.157608 248.331732,318.323043 239.492719,318.323043 C239.253266,318.323043 239.013844,318.317669 238.774632,318.306926 '
    'L25.1475605,308.712253 C16.8276309,308.338578 10.1847994,301.646603 9.87245893,293.324145 L9.87245893,293.324145 Z" '
    'id="sb-path"></path></defs>'
    '<mask id="sb-mask" fill="white"><use xlink:href="#sb-path"></use></mask>'
    '<use fill="#FF4785" fill-rule="nonzero" xlink:href="#sb-path"></use>'
    '<path d="M188.665358,39.126973 L190.191903,2.41148534 L220.883535,0 L222.205755,37.8634126 '
    'C222.251771,39.1811466 221.22084,40.2866846 219.903106,40.3327009 C219.338869,40.3524045 218.785907,40.1715096 218.342409,39.8221376 '
    'L206.506729,30.4984116 L192.493574,41.1282444 C191.443077,41.9251106 189.945493,41.7195021 189.148627,40.6690048 '
    'C188.813185,40.2267976 188.6423,39.6815326 188.665358,39.126973 Z M149.413703,119.980309 '
    'C149.413703,126.206975 191.355678,123.222696 196.986019,118.848893 C196.986019,76.4467826 174.234041,54.1651411 132.57133,54.1651411 '
    'C90.9086182,54.1651411 67.5656805,76.7934542 67.5656805,110.735941 C67.5656805,169.85244 147.345341,170.983856 147.345341,203.229219 '
    'C147.345341,212.280549 142.913138,217.654777 133.162291,217.654777 C120.456641,217.654777 115.433477,211.165914 116.024438,189.103298 '
    'C116.024438,184.317101 67.5656805,182.824962 66.0882793,189.103298 C62.3262146,242.56887 95.6363019,257.990394 133.753251,257.990394 '
    'C170.688279,257.990394 199.645341,238.303123 199.645341,202.663511 C199.645341,139.304202 118.683759,141.001326 118.683759,109.604526 '
    'C118.683759,96.8760922 128.139127,95.178968 133.753251,95.178968 C139.662855,95.178968 150.300143,96.2205679 149.413703,119.980309 Z" '
    'fill="#FFFFFF" fill-rule="nonzero" mask="url(#sb-mask)"></path>',
    "0 0 256 319", height=16,
)
EXPAND_ICON_SVG = _icon(
    '<polyline points="15 3 21 3 21 9"></polyline><polyline points="9 21 3 21 3 15"></polyline>'
    '<line x1="21" y1="3" x2="14" y2="10"></line><line x1="3" y1="21" x2="10" y2="14"></line>',
    size=15,
)
GITHUB_ICON_SVG = _icon(
    '<path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 '
    '5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 '
    '0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22"></path>',
    size=16,
)
# Larger variant of the grid icon for use as a nav-card visual (vs. the compact
# size used in the list/grid view-toggle button).
GRID_ICON_SVG_LARGE = _icon(
    '<rect x="3" y="3" width="7" height="7"></rect><rect x="14" y="3" width="7" height="7"></rect>'
    '<rect x="14" y="14" width="7" height="7"></rect><rect x="3" y="14" width="7" height="7"></rect>',
    size=20,
)

# Submitting always navigates to "/search?q=..." — that page reads the
# query param on load and runs the search immediately (see generate_search.py).
# Keeps the nav's mini-search simple and identical on every page, instead of
# duplicating full search results UI (API calls, rendering, restrict-filter)
# on every single page.
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
    if (q) window.location.href = "/search?q=" + encodeURIComponent(q);
  });
})();
"""

# Toggles .site-nav-links' "nav-open" class (see TOKENS_CSS's mobile
# breakpoint) rather than a plain show/hide flag, so the exact same markup
# also gets the wide-viewport inline layout for free — nothing here runs
# above 640px since the button itself is display:none there and never gets
# clicked. Closes on Escape or an outside click/tap so it doesn't linger
# open over page content the visitor then can't get to without first
# finding the toggle again; closes on a route link click too, since
# navigating away should always leave the menu in its default (closed) state
# for whenever the visitor comes back to a fixed nav bar.
NAV_MENU_JS = """
(function () {
  var toggle = document.getElementById("navMenuToggle");
  var panel = document.getElementById("siteNavLinks");
  if (!toggle || !panel) return;

  function setOpen(open) {
    panel.classList.toggle("nav-open", open);
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
  }

  toggle.addEventListener("click", function () {
    setOpen(!panel.classList.contains("nav-open"));
  });
  panel.addEventListener("click", function (e) {
    if (e.target.closest("a")) setOpen(false);
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") setOpen(false);
  });
  document.addEventListener("click", function (e) {
    if (panel.classList.contains("nav-open") && !panel.contains(e.target) && e.target !== toggle && !toggle.contains(e.target)) {
      setOpen(false);
    }
  });
})();
"""

# Priority+ nav: at any width above the true mobile breakpoint (see
# TOKENS_CSS's 640px media query, where NAV_MENU_JS's full hamburger sheet
# takes over instead), a route link that no longer fits on one line moves
# into a "More" dropdown one at a time rather than the row wrapping to a
# second line or the whole nav jumping straight to the mobile sheet just
# because a tablet-width viewport can't fit all five routes. Runs on load
# and on resize; every run starts by undoing the previous run's moves
# (cheaper and more robust than tracking incremental deltas) and bails out
# immediately once the media query matches, so the mobile sheet always gets
# every link back in the flat list rather than some stuck invisible in a
# "More" menu that mode never renders.
NAV_OVERFLOW_JS = """
(function () {
  var inner = document.getElementById("siteNavInner");
  var links = document.getElementById("siteNavLinks");
  var moreWrap = document.getElementById("navMore");
  var moreToggle = document.getElementById("navMoreToggle");
  var moreMenu = document.getElementById("navMoreMenu");
  var suggest = links ? links.querySelector(".nav-suggest") : null;
  if (!inner || !links || !moreWrap || !moreToggle || !moreMenu || !suggest) return;

  var mobileQuery = window.matchMedia("(max-width: 640px)");

  function fits() {
    return links.scrollWidth <= links.clientWidth + 1;
  }

  function routeLinks() {
    return Array.prototype.slice.call(links.children).filter(function (el) {
      return el.tagName === "A" && el !== suggest;
    });
  }

  function closeMoreMenu() {
    moreMenu.classList.remove("open");
    moreToggle.setAttribute("aria-expanded", "false");
  }

  // Three space-saving measures, applied in order from least to most
  // disruptive, each only reached once the previous one still isn't enough:
  // (1) shorten the site title, (2) shrink "Suggest a system" to its icon,
  // (3) move a route link into the "More" dropdown. Every run starts by
  // undoing all three (cheaper and more robust than tracking incremental
  // deltas) and re-measures from a clean slate.
  function layout() {
    closeMoreMenu();
    Array.prototype.slice.call(moreMenu.children).forEach(function (a) {
      links.insertBefore(a, moreWrap);
    });
    moreWrap.hidden = true;
    inner.classList.remove("brand-compact");
    suggest.classList.remove("compact");

    if (mobileQuery.matches) return; // the full hamburger sheet handles this range on its own

    if (fits()) return;
    inner.classList.add("brand-compact");

    if (fits()) return;
    suggest.classList.add("compact");

    var guard = 0;
    while (!fits() && guard < 20) {
      guard++;
      var candidates = routeLinks();
      if (candidates.length <= 1) break; // always leave at least one route visible inline
      var victim = null;
      for (var i = candidates.length - 1; i >= 0; i--) {
        if (!candidates[i].classList.contains("current")) { victim = candidates[i]; break; }
      }
      if (!victim) victim = candidates[candidates.length - 1];
      moreMenu.insertBefore(victim, moreMenu.firstChild);
      moreWrap.hidden = false;
    }
  }

  var resizeTimer;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(layout, 100);
  });
  if (mobileQuery.addEventListener) mobileQuery.addEventListener("change", layout);
  else mobileQuery.addListener(layout); // Safari <14

  moreToggle.addEventListener("click", function () {
    var open = !moreMenu.classList.contains("open");
    moreMenu.classList.toggle("open", open);
    moreToggle.setAttribute("aria-expanded", open ? "true" : "false");
  });
  moreMenu.addEventListener("click", function (e) {
    if (e.target.closest("a")) closeMoreMenu();
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeMoreMenu();
  });
  document.addEventListener("click", function (e) {
    if (moreMenu.classList.contains("open") && !moreWrap.contains(e.target)) closeMoreMenu();
  });

  layout();
})();
"""


def routes_nav(current: str) -> str:
    """Root-absolute paths on purpose: whatever page is home always deploys as
    index.html — see the workflows' Netlify deploy step — so any relative or
    filename-literal link breaks once live. "/" always resolves to it
    correctly regardless of how deep the current page is nested.

    current: "search" (home), "directory" (the full list/grid), "components",
    "patterns", or "foundations" (see generate_components.py — three
    separate taxonomy browse routes, not one shared bucket). "search" is the
    internal identifier every existing caller already passes for the
    homepage — kept as-is rather than renamed to "home" alongside the label
    below, so this stays a label change only, not a signature change every
    caller would need updating for.
    Nav labels: "Home" (the "search" route above — it's the homepage, and
    "Search" read as one more nav item rather than the site's own front
    door), "All Systems" (directory — avoids repeating "Directory" from the
    site name), "Components", "Patterns", "Foundations".
    """
    return f"""
    <nav class="site-nav">
      <div class="site-nav-inner" id="siteNavInner">
        <a href="/" class="site-brand">
          <span class="site-brand-full">Design Systems Directory</span>
          <span class="site-brand-short">DS Directory</span>
        </a>
        <div class="site-nav-links" id="siteNavLinks">
          <a href="/" class="{'current' if current == 'search' else ''}">Home</a>
          <a href="/directory" class="{'current' if current == 'directory' else ''}">All Systems</a>
          <a href="/components" class="{'current' if current == 'components' else ''}">Components</a>
          <a href="/patterns" class="{'current' if current == 'patterns' else ''}">Patterns</a>
          <a href="/foundations" class="{'current' if current == 'foundations' else ''}">Foundations</a>
          <div class="nav-more" id="navMore" hidden>
            <button type="button" class="nav-more-toggle" id="navMoreToggle" aria-haspopup="true" aria-expanded="false">More{CHEVRON_RIGHT_ICON_SVG}</button>
            <div class="nav-more-menu" id="navMoreMenu"></div>
          </div>
          <a class="nav-suggest" href="{SUBMISSION_URL}" aria-label="Suggest a system">{PLUS_ICON_SVG}<span class="nav-suggest-label">Suggest a system</span></a>
        </div>
        <div class="nav-search">
          <button type="button" class="nav-search-toggle" id="navSearchToggle" aria-label="Search">{SEARCH_ICON_SVG}</button>
          <form class="nav-search-form" id="navSearchForm" hidden>
            <input type="text" id="navSearchInput" placeholder="Search design systems...">
          </form>
        </div>
        <button type="button" class="nav-menu-toggle" id="navMenuToggle" aria-label="Toggle menu" aria-expanded="false" aria-controls="siteNavLinks">
          <span class="icon-menu">{MENU_ICON_SVG}</span><span class="icon-close">{CLOSE_ICON_SVG}</span>
        </button>
      </div>
    </nav>
    <script>{NAV_SEARCH_JS}</script>
    <script>{NAV_MENU_JS}</script>
    <script>{NAV_OVERFLOW_JS}</script>
    """
