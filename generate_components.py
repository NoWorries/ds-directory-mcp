"""
Builds a component-first route through the same dataset generate_directory.py
already renders system-first: components/index.html lists canonical component
names (Button, Table, Modal, ...) grouped alphabetically, and
components/<slug>.html lists exactly which systems document one component.

Same underlying data (pages_index.json + systems.yaml), three routes in:
system-first (directory.html), search-first (the embedded search box), and
component-first (this file) — same dataset, different entry points.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from component_taxonomy import TAXONOMY
from generate_directory import load_pages_index
from page_shell import FONT_LINK, TOKENS_CSS, routes_nav
from slug import slugify
from text_utils import clean_title

COMPONENTS_DIR = Path(__file__).parent / "components"

HEAD = f"""{FONT_LINK}
<style>
{TOKENS_CSS}
  .page {{ max-width: 900px; margin: 0 auto; }}"""


def build_component_index(pages_index: dict) -> dict[str, list[dict]]:
    index: dict[str, list[dict]] = {name: [] for name in TAXONOMY}

    for system_name, pages in pages_index.items():
        for page in pages:
            head = clean_title(page.get("title", "")).lower()
            for canonical, aliases in TAXONOMY.items():
                if any(re.search(rf"\b{re.escape(alias)}\b", head) for alias in aliases):
                    index[canonical].append({"system": system_name, "url": page["url"], "title": page["title"]})

    return {name: entries for name, entries in index.items() if entries}


def group_by_letter(component_index: dict[str, list[dict]]) -> dict[str, list[tuple[str, list[dict]]]]:
    groups: dict[str, list[tuple[str, list[dict]]]] = {}
    for name, entries in sorted(component_index.items(), key=lambda kv: kv[0].lower()):
        letter = name[0].upper()
        groups.setdefault(letter, []).append((name, entries))
    return groups


def render_index_page(component_index: dict[str, list[dict]]) -> str:
    groups = group_by_letter(component_index)

    jump_links = " ".join(f'<a href="#letter-{letter}">{letter}</a>' for letter in groups)

    sections = ""
    for letter, items in groups.items():
        rows = "".join(
            f'<li><a href="{slugify(name)}.html">{html.escape(name)}</a> '
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
<title>Components — Design System Directory</title>
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
<div class="page">
  <p class="eyebrow"><a href="../directory.html">design-system-directory</a> / components</p>
  <h1>Browse by component</h1>
  <p class="subtitle">The same indexed design systems, entered by component name instead of by system or search query. Pick one to see which systems document it.</p>
  {routes_nav("component", prefix="../")}
  <nav class="jump-nav">{jump_links}</nav>
  {sections}
</div>
</body>
</html>
"""


def render_component_page(name: str, entries: list[dict]) -> str:
    by_system = "".join(
        f'<li><a href="{html.escape(e["url"])}" target="_blank" rel="noopener">{html.escape(e["system"])}</a> '
        f'<span class="page-title">{html.escape(clean_title(e["title"]))}</span></li>'
        for e in sorted(entries, key=lambda e: e["system"].lower())
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(name)} — Design System Directory</title>
{HEAD}
  .system-list {{ list-style: none; margin: 0; padding: 0; }}
  .system-list li {{ display: flex; justify-content: space-between; align-items: baseline; gap: 12px; padding: 12px 0; border-bottom: 1px solid var(--border); }}
  .system-list a {{ text-decoration: none; font-weight: 600; }}
  .system-list a:hover {{ text-decoration: underline; }}
  .page-title {{ color: var(--text-muted); font-size: 0.85rem; font-family: "JetBrains Mono", monospace; white-space: nowrap; }}
</style>
</head>
<body>
<div class="page">
  <p class="eyebrow"><a href="../directory.html">design-system-directory</a> / <a href="index.html">components</a></p>
  <h1>{html.escape(name)}</h1>
  <p class="subtitle">{len(entries)} indexed system{"s" if len(entries) != 1 else ""} document {html.escape(name).lower()} — click through to the actual page.</p>
  {routes_nav("component", prefix="../")}
  <ul class="system-list">{by_system}</ul>
</div>
</body>
</html>
"""


def main() -> None:
    pages_index = load_pages_index()
    component_index = build_component_index(pages_index)

    COMPONENTS_DIR.mkdir(exist_ok=True)
    (COMPONENTS_DIR / "index.html").write_text(render_index_page(component_index))
    for name, entries in component_index.items():
        (COMPONENTS_DIR / f"{slugify(name)}.html").write_text(render_component_page(name, entries))

    print(f"Wrote components/index.html + {len(component_index)} component pages.")


if __name__ == "__main__":
    main()
