"""
Builds llms.txt — this site's own entry in the exact AI-affordance taxonomy
it crawls other design systems for (see resources.py's WELL_KNOWN_PATHS/
RESOURCE_PATTERNS, and https://state-of-ai-in-design-systems.netlify.app/).
A directory built to make design systems legible to agents had no llms.txt
of its own until now — this is that fix.

Points an agent straight at the MCP server (the fast path: one tool call,
no HTML to parse) and, as a fallback for an agent that only fetches plain
URLs, the two ways to use the site without it (the /search HTTP API, and
the plain directory/search pages).
"""

from __future__ import annotations

from pathlib import Path

from generate_data_export import SCHEMA_FILE, SYSTEMS_JSON_FILE
from generate_directory import compute_stats, indexed_only, load_systems
from generate_home import MCP_INSTALL_COMMAND, MCP_URL
from generate_search import SEARCH_API_URL

OUTPUT_FILE = Path(__file__).parent / "llms.txt"


def _human_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    return f"{num_bytes / 1024:.0f} KB"


def render_llms_txt(entries: list[dict]) -> str:
    stats = compute_stats(entries)
    # "An index, not a dump" — list the real files with their measured size
    # so an agent can budget context before fetching, rather than just
    # describing that data exists (the state-of-ai-in-design-systems.
    # netlify.app/ai pattern this whole file follows). Run generate_data_
    # export.py first (see the workflows) so these sizes are real, not 0.
    data_files = [
        (SYSTEMS_JSON_FILE, "Every indexed system, merged into one file — the same records the HTML pages render."),
        (SCHEMA_FILE, "JSON Schema for systems.json and each systems/<slug>.json."),
    ]
    data_files_section = "\n".join(
        f"- /{f.name} ({_human_size(f.stat().st_size)}) — {desc}" if f.exists() else f"- /{f.name} — {desc}"
        for f, desc in data_files
    )
    # The OLDEST last_checked, not the newest — an honest staleness bound for
    # the whole corpus (some systems refresh weekly, some haven't been
    # re-crawled in longer; the newest date alone would overstate freshness).
    checked_dates = [e["last_checked"] for e in entries if e.get("last_checked")]
    freshness_note = f"oldest record last checked {min(checked_dates)[:10]}" if checked_dates else "freshness not yet recorded"
    return f"""# Design Systems Directory

> This is dated research about a fast-moving corpus — {freshness_note}, refreshed on \
a rolling basis. A system's own detail page always shows its own last-checked date.

> Semantic search across {stats['total_systems']} publicly documented design systems \
({stats['total_pages']:,} pages indexed) — GitHub, Storybook, Figma, npm packages, \
tokens, icons, and full-text content, all in one place.

This directory exists specifically to be used by AI coding agents, not just \
browsed by humans. The MCP server below is the intended way to query it — the \
same semantic search index, no HTML to parse, callable as a single tool.

## MCP server (preferred)

Add it to any MCP-compatible client:

```
{MCP_INSTALL_COMMAND}
```

Server URL: {MCP_URL}

## HTTP search API (no MCP client available)

```
GET {SEARCH_API_URL}?q=<query>&system=<optional design system name, repeatable>
```

Returns `{{"results": [{{"design_system_name", "url", "text", "score"}}, ...]}}` — \
semantically ranked chunks of real page content, not just keyword matches.

## Data files (to count with, not just read)

Same records as the HTML pages, as plain JSON — for anything that wants to \
recompute a stat itself rather than trust a rendered number. Excludes archived/\
dead systems and anything with zero pages indexed, same as every page below.

{data_files_section}
- /systems/<slug>.json — one system's own record (matches systems.json's shape)

## Pages (for a plain crawl with no query to run)

Relative to this file's own URL (llms.txt lives at the site root):

- /directory — every indexed system, sortable/filterable, with per-system \
resource coverage (GitHub, npm, Storybook, Figma, MCP server, agent instructions, etc.)
- /systems/<slug> — one system's full detail: resources, indexed pages, \
freshness, and any detected accessibility/tokens/framework/governance signals
- /components — which systems document a given component or pattern

## Notes for an agent reading this file

- Every system record here was itself crawled and classified for exactly the kind \
of AI-affordance signal this file represents (see each system's detail page) — \
this project eats its own dog food.
- This file describes the directory itself, not any one design system in it — \
it makes no claim about which system you should use, only how to query what's here.
- Data refreshes on a rolling basis; a system's own detail page (and each JSON \
record's last_checked field) shows when it was last checked and whether the \
crawl looks complete.
- This is a read-only public index. No authentication, no rate limit beyond fair use.
"""


if __name__ == "__main__":
    systems = indexed_only(load_systems())
    OUTPUT_FILE.write_text(render_llms_txt(systems))
    print(f"Wrote {OUTPUT_FILE}")
