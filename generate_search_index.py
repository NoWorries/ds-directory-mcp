"""
Builds search-index.json — a small, curated list of every real destination
page this site has (systems, components, patterns, foundations), used for
instant client-side typeahead suggestions as a visitor types into any search
box (see page_shell.py's SEARCH_TYPEAHEAD_JS): "here's a page we already
have" surfaced before they commit to a full semantic search, the same
pattern a help site's search box uses to suggest a knowledgebase article
before someone files a ticket.

Deliberately NOT the raw scraped page titles from pages_index.json (tens of
thousands of entries across every indexed system, uneven title quality, no
canonical page of its own on this site) — just the couple hundred things
this site itself has a real, curated page for, matching instantly and
cheaply against a query as the visitor types.
"""

from __future__ import annotations

import json
from pathlib import Path

from generate_components import ROUTES, build_all_indexes
from generate_directory import indexed_only, load_pages_index, load_systems, visible_by_default
from slug import slugify
from text_utils import full_name

OUTPUT_FILE = Path(__file__).parent / "search-index.json"


def build_index(systems: list[dict], pages_index: dict) -> list[dict]:
    systems_by_name = {full_name(e): e for e in systems}
    items = [
        {
            "type": "system",
            "name": full_name(e),
            "url": f"/systems/{slugify(full_name(e))}",
            "count": e.get("pages_indexed") or 0,
        }
        for e in systems
    ]

    taxonomy_indexes = build_all_indexes(pages_index, systems_by_name)
    for route in ROUTES:
        for name, entries in taxonomy_indexes.get(route["key"], {}).items():
            items.append(
                {
                    "type": route["key"],
                    "name": name,
                    "url": f"/{route['dir']}/{slugify(name)}",
                    "count": len(entries),
                }
            )

    items.sort(key=lambda i: i["name"].lower())
    return items


if __name__ == "__main__":
    systems = visible_by_default(indexed_only(load_systems()))
    pages_index = load_pages_index()
    index = build_index(systems, pages_index)
    OUTPUT_FILE.write_text(json.dumps(index))
    print(f"Wrote {OUTPUT_FILE} with {len(index)} entries.")
