"""
Builds three component-first routes through the same dataset
generate_directory.py already renders system-first: /components,
/patterns, and /foundations each list canonical names grouped
alphabetically, and <route>/<slug>.html lists exactly which systems
document one of them — same underlying data (pages_index.json +
systems.yaml), three more entry points alongside system-first
(directory.html) and search-first (the embedded search box).

Deliberately three separate routes, not one shared "components" bucket —
see component_taxonomy.py's module docstring for why lumping them together
was itself a bug (a Typography foundations page showing up mixed in with
Button/Modal/Table).
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from component_taxonomy import COMPONENTS, FOUNDATIONS, NEGATIVE_TITLE_PATTERNS, PATTERNS
from generate_directory import favicon_html, load_pages_index, load_systems
from page_shell import EXTERNAL_LINK_ICON_SVG, FAVICON_LINK, FONT_LINK, NAV_HEIGHT, TOKENS_CSS, routes_nav
from slug import slugify
from text_utils import clean_title, dedupe_system_name, full_name, in_any_scope, path_scope, split_org_name

# One taxonomy -> one route, each with its own output dir, nav-current key,
# and page copy. Loop over this instead of writing the same three functions
# three times.
ROUTES = [
    {
        "key": "components",
        "dir": "components",
        "taxonomy": COMPONENTS,
        "title": "Components",
        "heading": "Browse by component",
        "subtitle": "The same indexed design systems, entered by component name instead of by system or search query. Pick one to see which systems document it.",
    },
    {
        "key": "patterns",
        "dir": "patterns",
        "taxonomy": PATTERNS,
        "title": "Patterns",
        "heading": "Browse by pattern",
        "subtitle": "Bigger, task-oriented compositions — usually built from several components — like onboarding, search, or empty states. Distinct from single-widget components: this is how systems assemble them to solve a real user task.",
    },
    {
        "key": "foundations",
        "dir": "foundations",
        "taxonomy": FOUNDATIONS,
        "title": "Foundations",
        "heading": "Browse by foundation",
        "subtitle": "System-wide design principles that aren't components at all — typography, color, iconography, spacing. The rules everything else is built on top of.",
    },
]

HEAD = f"""{FAVICON_LINK}
{FONT_LINK}
<style>
{TOKENS_CSS}"""


# One combined alias table across all three taxonomies, not three
# independent ones — matching COMPONENTS/PATTERNS/FOUNDATIONS in isolation
# meant a title could win an alias in more than one taxonomy at once with no
# tiebreak (e.g. "icon" (Icon, a component) vs "iconography" (Foundations)
# both matching an "Iconography" page), and a short alias could beat a
# longer, more specific one purely because it was checked first ("grid"
# matching before "layout grid" got round to it). Sorted longest-alias-first
# so the FIRST match found is always the most specific one available.
_ALIAS_TABLE: list[tuple[str, str, str]] = sorted(
    (
        (alias, canonical, route["key"])
        for route in ROUTES
        for canonical, aliases in route["taxonomy"].items()
        for alias in aliases
    ),
    key=lambda t: len(t[0]),
    reverse=True,
)

# alias -> compiled whole-word-at-start pattern, built once. "tab" must not
# match "Table" (an entirely different component) just because it's a string
# prefix — requiring a word boundary right after an optional plural suffix
# means "tab"/"tabs" both match "Tabs..." but neither matches "Table...".
_ALIAS_PATTERNS = {alias: re.compile(rf"{re.escape(alias)}(s|es)?\b") for alias, _, _ in _ALIAS_TABLE}


def classify_title(head: str) -> tuple[str, str] | None:
    """(canonical, route_key) for the single most specific alias this
    (already cleaned + lowercased) title starts with, or None. Longest alias
    wins when more than one matches — see _ALIAS_TABLE."""
    for alias, canonical, route_key in _ALIAS_TABLE:
        if _ALIAS_PATTERNS[alias].match(head):
            return canonical, route_key
    return None


def build_all_indexes(pages_index: dict, systems_by_name: dict[str, dict]) -> dict[str, dict[str, list[dict]]]:
    """{route_key: {canonical: [entries]}} — one entry per (canonical,
    system), never more than one even if a system has several crawled pages
    matching the same canonical (e.g. a component's separate Usage/Style/
    Accessibility/Code subpages) — that used to add one duplicate-looking
    entry per subpage instead of recognizing they're all the same system
    documenting the same thing. The first (cleaned-title-sorted) page found
    is kept as the representative link.

    A page outside the system's own crawl scope (path_scope(), the same
    domain+path check ingest.py's crawler itself uses) is skipped entirely —
    this is what actually fixes most of the remaining false positives after
    whole-word matching: BBC GEL's mobile-accessibility guide "matching"
    Color/Accessibility, Microsoft's Copilot marketing "matching" Onboarding,
    Designers Italia's community "Accessibility Days 20XX" posts — none of
    these are on-topic docs for the matched canonical, they're just
    off-scope pages that happened to get crawled (see resources.py/ingest.py
    for why an off-scope page can still end up in pages_index.json at all:
    a redirect, an included external link, or content crawled before the
    path-scoping fix existed). A page from a system with no recorded
    start_urls (shouldn't happen, but not fatal) is kept rather than dropped."""
    indexes: dict[str, dict[str, list[dict]]] = {route["key"]: {} for route in ROUTES}
    seen: set[tuple[str, str, str]] = set()  # (route_key, canonical, system_name)

    for system_name, pages in pages_index.items():
        site_titles = [p["title"] for p in pages]
        entry = systems_by_name.get(system_name)
        start_urls = (entry.get("start_urls") if entry else None) or []
        scopes = {path_scope(u) for u in start_urls}

        for page in sorted(pages, key=lambda p: clean_title(p["title"], site_titles=site_titles)):
            if scopes and not in_any_scope(page["url"], scopes):
                continue
            head = clean_title(page.get("title", ""), site_titles=site_titles).lower()
            match = classify_title(head)
            if not match:
                continue
            canonical, route_key = match
            negative_patterns = NEGATIVE_TITLE_PATTERNS.get(canonical)
            if negative_patterns and any(re.search(p, page["title"], re.IGNORECASE) for p in negative_patterns):
                continue
            key = (route_key, canonical, system_name)
            if key in seen:
                continue
            seen.add(key)
            indexes[route_key].setdefault(canonical, []).append(
                {"system": system_name, "url": page["url"], "title": page["title"]}
            )

    return indexes


def group_by_letter(component_index: dict[str, list[dict]]) -> dict[str, list[tuple[str, list[dict]]]]:
    groups: dict[str, list[tuple[str, list[dict]]]] = {}
    for name, entries in sorted(component_index.items(), key=lambda kv: kv[0].lower()):
        letter = name[0].upper()
        groups.setdefault(letter, []).append((name, entries))
    return groups


def render_system_badge(system_name: str, systems_by_name: dict[str, dict]) -> str:
    """Favicon + org + design-system name, the exact same arrangement
    generate_directory.py's render_name_block uses for the "All Systems"
    list — templated here as its own small function (rather than copy-pasted
    per taxonomy) so a visitor recognizes a system on a component/pattern/
    foundation page the same way they would anywhere else on the site,
    instead of a bare text link with no visual identity."""
    entry = systems_by_name.get(system_name)
    if not entry:
        return f'<span class="ds-name">{html.escape(system_name)}</span>'
    org, ds_name = split_org_name(entry)
    favicon = favicon_html((entry.get("start_urls") or [None])[0])
    org_html = f'<span class="org">{html.escape(org)}</span>' if org else ""
    return f'<div class="name-block">{favicon}<div class="name-text">{org_html}<span class="ds-name">{html.escape(ds_name)}</span></div></div>'


def render_index_page(component_index: dict[str, list[dict]], route: dict) -> str:
    groups = group_by_letter(component_index)

    jump_links = " ".join(f'<a href="#letter-{letter}">{letter}</a>' for letter in groups)

    sections = ""
    for letter, items in groups.items():
        rows = "".join(
            f'<li><a href="/{route["dir"]}/{slugify(name)}">{html.escape(name)}</a> '
            f'<span class="count">{len(entries)} system{"s" if len(entries) != 1 else ""}</span></li>'
            for name, entries in items
        )
        sections += f"""
        <section class="letter-group" id="letter-{letter}">
          <h2>{letter}</h2>
          <ul class="component-list">{rows}</ul>
        </section>
        """

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{route["title"]} — Design Systems Directory</title>
{HEAD}
  .jump-nav {{ display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 32px; font-family: "JetBrains Mono", monospace; font-size: 0.82rem; }}
  .jump-nav a {{ display: inline-block; width: 26px; height: 26px; line-height: 26px; text-align: center; border-radius: 4px; text-decoration: none; color: var(--text-muted); }}
  .jump-nav a:hover {{ background: var(--accent-soft); color: var(--accent); }}
  .letter-group {{ margin-bottom: 28px; }}
  .letter-group h2 {{
    font-family: "JetBrains Mono", monospace; font-size: 0.85rem; font-weight: 700; color: var(--accent);
    margin: 0 0 6px; padding-bottom: 6px; border-bottom: 2px solid var(--border);
  }}
  .component-list {{ list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 4px 24px; }}
  .component-list li {{ display: flex; justify-content: space-between; align-items: baseline; padding: 9px 0; border-bottom: 1px solid var(--border); font-size: 0.95rem; }}
  .component-list a {{ text-decoration: none; font-weight: 600; }}
  .component-list a:hover {{ text-decoration: underline; }}
  .count {{ color: var(--text-faint); font-size: 0.78rem; font-family: "JetBrains Mono", monospace; white-space: nowrap; margin-left: 8px; }}
</style>
</head>
<body>
{routes_nav(route["key"])}
<div class="page">
  <p class="eyebrow">{route["title"]}</p>
  <h1>{route["heading"]}</h1>
  <p class="subtitle">{route["subtitle"]}</p>
  <nav class="jump-nav">{jump_links}</nav>
  {sections}
</div>
</body>
</html>
"""


def render_sidebar(component_index: dict[str, list[dict]], current: str, route: dict) -> str:
    """Two renderings of the same list sharing one set of <li> markup:
    .component-sidebar (a plain always-visible list — the desktop treatment,
    a sticky column beside the main content) and .component-sidebar-mobile
    (a <details> dropdown whose closed <summary> just names the current
    selection — the small-viewport treatment, since dumping all ~30+ entries
    inline above the page content it's showing was the mobile bug here).
    Only one of the two is ever visible at a given viewport width (see the
    CSS below) — the mobile one needs its own copy of the <li> markup since
    a single element can't render twice in two different places at once."""
    items = "".join(
        f'<li><a href="/{route["dir"]}/{slugify(n)}" class="{"current" if n == current else ""}">'
        f'{html.escape(n)} <span class="sidebar-count">{len(entries)}</span></a></li>'
        for n, entries in sorted(component_index.items(), key=lambda kv: kv[0].lower())
    )
    return f"""
    <nav class="component-sidebar">
      <p class="sidebar-label">All {html.escape(route["key"])}</p>
      <ul>{items}</ul>
    </nav>
    <details class="component-sidebar-mobile">
      <summary aria-label="Browse all {html.escape(route["key"])}, currently viewing {html.escape(current)}">
        <span class="sidebar-mobile-current">{html.escape(current)}</span>
      </summary>
      <ul>{items}</ul>
    </details>"""


def render_component_page(
    name: str,
    entries: list[dict],
    component_index: dict[str, list[dict]],
    route: dict,
    systems_by_name: dict[str, dict],
    pages_index: dict,
) -> str:
    def page_title_html(e: dict) -> str:
        system_entry = systems_by_name.get(e["system"])
        org, ds_name = split_org_name(system_entry) if system_entry else ("", "")
        site_titles = [p["title"] for p in pages_index.get(e["system"], [])]
        title = clean_title(e["title"], org, ds_name, e["system"], site_titles=site_titles)
        title = dedupe_system_name(title, e["system"]) or title
        return html.escape(title) if title else ""

    # Two separate destinations per row, same split as generate_directory.py's
    # own table (render_name_block + render_docs_cell): the name links to
    # THIS site's own system page (resources, freshness, every indexed page —
    # everything else a visitor would want right after landing here), while
    # the external-link pill goes straight to the actual page on the
    # system's own docs site that documents this component. Previously only
    # the external link existed at all — there was no way to get to this
    # system's own page from a component/pattern/foundation listing.
    def system_row_html(e: dict) -> str:
        title = page_title_html(e)
        external_label = f' — {title}' if title else ""
        return (
            f'<li>'
            f'<a class="name-block-link" href="/systems/{slugify(e["system"])}">{render_system_badge(e["system"], systems_by_name)}</a>'
            f'<a class="page-title" href="{html.escape(e["url"])}" target="_blank" rel="noopener" '
            f'title="Open the actual page on {html.escape(e["system"])}\'s site{external_label}">'
            f'{EXTERNAL_LINK_ICON_SVG}{f"<span>{title}</span>" if title else ""}</a>'
            f'</li>'
        )

    by_system = "".join(system_row_html(e) for e in sorted(entries, key=lambda e: e["system"].lower()))

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(name)} — Design Systems Directory</title>
{HEAD}
  .component-layout {{ display: flex; gap: 32px; align-items: flex-start; }}
  .component-sidebar {{
    flex: 0 0 190px; position: sticky; top: calc({NAV_HEIGHT} + 24px);
    max-height: calc(100vh - {NAV_HEIGHT} - 48px); overflow-y: auto;
    border-right: 1px solid var(--border); padding-right: 16px;
  }}
  .sidebar-label {{
    font-family: "JetBrains Mono", monospace; font-size: 0.7rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.05em; color: var(--text-faint); margin: 0 0 10px;
  }}
  .component-sidebar ul {{ list-style: none; margin: 0; padding: 0; }}
  .component-sidebar li {{ margin-bottom: 2px; }}
  /* Was padding: 5px 8px; margin: 0 -8px — a "bleed" trick meant to let the
     hover/current pill extend slightly past the link's own text, which only
     works when the CONTAINER has matching horizontal padding to bleed into.
     This container has none on the left, so the negative margin instead
     pushed the pill's edges flush against (and slightly past) the sidebar's
     own boundary — the highlight rendered edge-to-edge with no visible
     inset or corner rounding. Plain padding with no counteracting margin
     keeps the pill properly inset within the row, rounded like every other
     pill on the site. */
  .component-sidebar a {{
    display: flex; justify-content: space-between; align-items: baseline; gap: 8px;
    padding: 6px 10px; border-radius: 8px; text-decoration: none;
    font-size: 0.88rem; color: var(--text-muted);
  }}
  .component-sidebar a:hover {{ background: var(--surface-sunken); color: var(--text); }}
  .component-sidebar a.current {{ background: var(--accent-soft); color: var(--accent); font-weight: 600; }}
  .sidebar-count {{ color: var(--text-faint); font-size: 0.76rem; font-family: "JetBrains Mono", monospace; }}
  .component-sidebar a.current .sidebar-count {{ color: var(--accent); }}
  /* --- Mobile "submenu": a closed-by-default dropdown naming just the
     current selection, instead of the desktop sidebar's full always-open
     list dumped inline above the very content it's a menu for. Hidden at
     desktop widths (see .component-sidebar-mobile below and the .component-
     sidebar's own display:none at the same breakpoint) — exactly one of the
     two ever renders at a given width. */
  .component-sidebar-mobile {{ display: none; }}
  .component-main {{ flex: 1; min-width: 0; }}
  .system-list {{ list-style: none; margin: 0; padding: 0; }}
  /* flex-wrap so a long page title (nowrap, can't shrink) and a long system
     name compete for space by the whole ROW dropping the title onto its own
     line, not by squeezing the name's column width until its text wraps
     instead — confirmed live on "Vanilla framework" + "cards with aside and
     toolbar", where the name wrapped to two lines while the title stayed
     put on the first. */
  .system-list li {{ display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 4px 12px; padding: 12px 4px; border-bottom: 1px solid var(--border); }}
  .system-list a {{ text-decoration: none; color: inherit; }}
  /* Was gap: 8px — read as cramped against the favicon (which itself has no
     visual padding of its own, being a plain small square/circle image), so
     the icon looked like it was crowding straight into the text next to it. */
  .system-list .name-block {{ display: flex; align-items: center; gap: 12px; }}
  .system-list .name-text {{ display: flex; flex-direction: column; line-height: 1.25; }}
  .system-list .org {{ font-size: 0.66rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; color: var(--text-faint); }}
  .system-list .ds-name {{ font-weight: 600; font-size: 0.92rem; }}
  .system-list a:hover .ds-name {{ text-decoration: underline; }}
  .system-list .favicon {{ border-radius: 3px; flex: none; }}
  /* margin-left: auto keeps this right-aligned against the name whether it
     shares the first line (space-between already puts it there) or, once
     that line is too narrow for both, drops to a line of its own below —
     right-aligned there too, rather than snapping to the left edge. Its own
     real link now (opens the actual page on the system's site — the name
     link above goes to this site's own system page instead), so it gets a
     hover color of its own rather than inheriting .ds-name's underline. */
  .page-title {{
    display: inline-flex; align-items: center; gap: 5px;
    color: var(--text-muted); font-size: 0.85rem; font-family: "JetBrains Mono", monospace;
    white-space: nowrap; margin-left: auto;
  }}
  .page-title svg {{ flex: none; }}
  .page-title:hover {{ color: var(--accent); }}
  @media (max-width: 760px) {{
    .component-layout {{ display: block; }}
    .component-sidebar {{ display: none; }}
    /* The shared sitewide h1 (page_shell.py's clamp()) reads as a hero
       heading on pages built around one big statement (home's "Search 119
       design systems") — right below the compact mobile submenu here, that
       same weight/size instead just crowded a page whose real content is
       the list underneath it. Knocked down a step for this page only. */
    .component-main h1 {{ font-size: 1.2rem; }}
    /* position: relative so the open panel (position: absolute below) anchors
       to this element and overlays the page instead of pushing it down —
       native <details> content is normally static, in-flow, and would
       otherwise shove the eyebrow/heading/list further down the page every
       time it opened. */
    .component-sidebar-mobile {{
      position: relative; display: block; border: 1px solid var(--border); border-radius: 10px; margin-bottom: 20px;
    }}
    .component-sidebar-mobile summary {{
      display: flex; align-items: center; gap: 10px;
      padding: 12px 14px; cursor: pointer; list-style: none; font-size: 0.9rem;
    }}
    .component-sidebar-mobile summary::-webkit-details-marker {{ display: none; }}
    .sidebar-mobile-current {{ font-weight: 600; color: var(--accent); margin-right: auto; }}
    /* Same drawn-chevron technique as the resource groups on a system's own
       detail page (generate_systems.py) — rotates open/closed with the
       <details>'s own [open] state, no separate icon asset needed. */
    .component-sidebar-mobile summary::after {{
      content: ""; width: 7px; height: 7px; flex: none;
      border-right: 1.5px solid var(--text-faint); border-bottom: 1.5px solid var(--text-faint);
      transform: rotate(45deg); transition: transform 0.15s;
    }}
    .component-sidebar-mobile[open] summary::after {{ transform: rotate(225deg); }}
    .component-sidebar-mobile[open] summary {{ border-bottom: 1px solid var(--border); }}
    /* Overlays below the closed summary bar (see the position: relative
       parent above) rather than expanding the document flow — the page
       underneath stays exactly where it was, open or closed. */
    .component-sidebar-mobile ul {{
      position: absolute; top: 100%; left: 0; right: 0; z-index: 60;
      background: var(--surface); border: 1px solid var(--border); border-top: none;
      border-radius: 0 0 10px 10px; box-shadow: var(--shadow);
      list-style: none; margin: 0; padding: 8px; max-height: 50vh; overflow-y: auto;
    }}
    .component-sidebar-mobile li {{ margin-bottom: 2px; }}
    .component-sidebar-mobile a {{
      display: flex; justify-content: space-between; align-items: baseline; gap: 8px;
      padding: 8px 10px; border-radius: 8px; text-decoration: none; font-size: 0.92rem; color: var(--text-muted);
    }}
    .component-sidebar-mobile a:hover {{ background: var(--surface-sunken); color: var(--text); }}
    .component-sidebar-mobile a.current {{ background: var(--accent-soft); color: var(--accent); font-weight: 600; }}
    .component-sidebar-mobile a.current .sidebar-count {{ color: var(--accent); }}
  }}
  /* Phone-narrow: the anchored dropdown above has too little room to be
     useful as a small floating panel, so open takes over the whole screen
     instead — the <details> element itself (summary included) becomes the
     fixed-position surface, summary pinned at top (still the native close
     toggle) with the list filling the rest of the viewport, scrollable. */
  @media (max-width: 640px) {{
    .component-sidebar-mobile[open] {{
      position: fixed; inset: 0; z-index: 300; display: flex; flex-direction: column;
      border: none; border-radius: 0; margin: 0; background: var(--surface);
    }}
    .component-sidebar-mobile[open] summary {{ flex: none; padding: 16px; }}
    .component-sidebar-mobile[open] ul {{
      position: static; flex: 1; min-height: 0; max-height: none;
      border: none; border-radius: 0; box-shadow: none; padding: 8px 16px 16px;
    }}
  }}
</style>
</head>
<body>
{routes_nav(route["key"])}
<div class="page">
  <p class="eyebrow"><a href="/{route["dir"]}">{route["title"]}</a></p>
  <div class="component-layout">
    {render_sidebar(component_index, name, route)}
    <div class="component-main">
      <h1>{html.escape(name)}</h1>
      <p class="subtitle">{len(entries)} indexed system{"s" if len(entries) != 1 else ""} document {html.escape(name).lower()} — click through to the actual page.</p>
      <ul class="system-list">{by_system}</ul>
    </div>
  </div>
</div>
</body>
</html>
"""


def main() -> None:
    pages_index = load_pages_index()
    systems_by_name = {full_name(e): e for e in load_systems()}
    indexes = build_all_indexes(pages_index, systems_by_name)

    total_pages = 0
    for route in ROUTES:
        component_index = indexes[route["key"]]
        out_dir = Path(__file__).parent / route["dir"]
        out_dir.mkdir(exist_ok=True)
        # Clear stale pages from a previous run first — a canonical that no
        # longer matches anything (a taxonomy edit, a re-crawl that lost the
        # matching pages) otherwise leaves its old <slug>.html sitting around
        # forever, deployed and linked from nowhere but still reachable.
        wanted_slugs = {slugify(name) for name in component_index}
        for existing in out_dir.glob("*.html"):
            if existing.stem != "index" and existing.stem not in wanted_slugs:
                existing.unlink()
        (out_dir / "index.html").write_text(render_index_page(component_index, route))
        for name, entries in component_index.items():
            (out_dir / f"{slugify(name)}.html").write_text(
                render_component_page(name, entries, component_index, route, systems_by_name, pages_index)
            )
        total_pages += len(component_index)
        print(f"Wrote {route['dir']}/index.html + {len(component_index)} {route['key']} pages.")

    print(f"Total: {total_pages} taxonomy pages across {len(ROUTES)} routes.")


if __name__ == "__main__":
    main()
