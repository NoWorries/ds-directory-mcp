"""
Flags four crawl-health problems and reports them to the maintainer — via
the GitHub Actions job summary and a file the workflow hands to
`gh issue create`/`gh issue edit`:

1. Broken (the start URL itself couldn't be fetched — DNS failure, connection
   refused, timeout) — usually means the site moved, was renamed, or is down.
2. Low coverage (fewer than LOW_COVERAGE_THRESHOLD pages indexed, and not
   already caught by #1) — the crawl ran but likely got blocked or found a
   JS-rendered page it can't see through.
3. Capped (hit ingest.py's max_pages ceiling with pages still queued) — the
   crawl worked, but there's real content that wasn't indexed because the
   site is bigger than max_pages allows.
4. Unverified resources (a discovered github/npm link whose name doesn't
   obviously relate to the system itself) — needs a human to actually look
   and decide (keep it, or it's an unrelated dependency swept up from the
   page).

Deliberately NOT surfaced on the public directory page — see
generate_directory.py's find_broken()/find_low_coverage()/find_capped()/
find_unverified_resources() docstrings for why.
"""

import os
from pathlib import Path

from generate_directory import (
    LOW_COVERAGE_THRESHOLD,
    find_broken,
    find_capped,
    find_low_coverage,
    find_unverified_resources,
    load_systems,
)
from text_utils import full_name

REPORT_FILE = Path(__file__).parent / "health_report.md"


def render_report(broken: list[dict], low_coverage: list[dict], capped: list[dict], unverified: list[dict]) -> str:
    sections = []

    if broken:
        lines = [
            f"### 🔴 {len(broken)} system{'s' if len(broken) != 1 else ''} failed to fetch at all — url may have changed",
            "",
            "The start URL itself couldn't be reached (DNS failure, connection refused, "
            "timeout, etc). Usually the site moved, was renamed, or the URL in "
            "`systems.yaml` is just wrong — worth checking each one manually before "
            "assuming it's a temporary blip.",
            "",
        ]
        for entry in broken:
            start_url = (entry.get("start_urls") or [""])[0]
            lines.append(f"- **{full_name(entry)}** — {start_url}\n  `{entry['crawl_error']}`")
        sections.append("\n".join(lines))

    if low_coverage:
        lines = [
            f"### ⚠ {len(low_coverage)} system{'s' if len(low_coverage) != 1 else ''} likely failed to crawl properly",
            "",
            f"Fewer than {LOW_COVERAGE_THRESHOLD} pages indexed usually means a JS-rendered site the "
            "crawler can't see through, a robots.txt block, a redirect, or a start_url that needs "
            "`include_patterns`/`max_pages` tuning in `systems.yaml` — not that the system genuinely "
            "has almost no documentation.",
            "",
        ]
        for entry in low_coverage:
            pages = entry["pages_indexed"]
            start_url = (entry.get("start_urls") or [""])[0]
            spa_note = " — likely a JS-rendered SPA (plain HTML crawling can't see its content)" if entry.get("likely_spa") else ""
            lines.append(f"- **{full_name(entry)}** — {pages} page{'s' if pages != 1 else ''} — {start_url}{spa_note}")
        sections.append("\n".join(lines))

    if capped:
        lines = [
            f"### 📈 {len(capped)} system{'s' if len(capped) != 1 else ''} hit the max_pages ceiling",
            "",
            "The crawl stopped with pages still queued to visit — there's more real "
            "documentation on these sites than got indexed. Raise `max_pages` for these "
            "entries in `systems.yaml` if full coverage matters for them.",
            "",
        ]
        for entry in capped:
            pages = entry["pages_indexed"]
            start_url = (entry.get("start_urls") or [""])[0]
            lines.append(f"- **{full_name(entry)}** — {pages} pages indexed — {start_url}")
        sections.append("\n".join(lines))

    if unverified:
        lines = [
            f"### 🔍 {len(unverified)} system{'s' if len(unverified) != 1 else ''} "
            f"{'have' if len(unverified) != 1 else 'has'} unverified resource links",
            "",
            "A discovered GitHub/npm link's name doesn't obviously match the design "
            "system it was found on — could be a dependency or unrelated tool swept up "
            "from a footer/credits link rather than the system's own repo/package. "
            "Worth a quick manual check: if it's wrong, remove it from `resources` in "
            "`systems.yaml` (the next crawl will just rediscover it otherwise) or add an "
            "`exclude_patterns` entry so it stops getting picked up.",
            "",
        ]
        for entry in unverified:
            for key, urls in entry["unverified_resources"].items():
                for url in urls:
                    lines.append(f"- **{full_name(entry)}** — {key}: {url}")
        sections.append("\n".join(lines))

    return "\n\n".join(sections)


def main() -> None:
    entries = load_systems()
    broken = find_broken(entries)
    low_coverage = find_low_coverage(entries)
    capped = find_capped(entries)
    unverified = find_unverified_resources(entries)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")

    if not broken and not low_coverage and not capped and not unverified:
        print("No crawl-health issues detected.")
        if REPORT_FILE.exists():
            REPORT_FILE.unlink()
        if summary_path:
            with open(summary_path, "a") as f:
                f.write("### ✅ No crawl-health issues detected\n")
        return

    report = render_report(broken, low_coverage, capped, unverified)
    print(report)
    REPORT_FILE.write_text(report)

    if summary_path:
        with open(summary_path, "a") as f:
            f.write(report + "\n")


if __name__ == "__main__":
    main()
