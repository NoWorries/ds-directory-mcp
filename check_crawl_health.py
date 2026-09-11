"""
Flags design systems whose crawl looks like it failed or was incomplete
(fewer than LOW_COVERAGE_THRESHOLD pages indexed) and reports it to the
maintainer — via the GitHub Actions job summary and a file the workflow hands
to `gh issue create`/`gh issue edit`.

Deliberately NOT surfaced on the public directory page — see
generate_directory.py's find_low_coverage() docstring for why.
"""

import os
from pathlib import Path

from generate_directory import LOW_COVERAGE_THRESHOLD, find_low_coverage, load_systems

REPORT_FILE = Path(__file__).parent / "health_report.md"


def render_report(low_coverage: list[dict]) -> str:
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
        lines.append(f"- **{entry['name']}** — {pages} page{'s' if pages != 1 else ''} — {start_url}")
    return "\n".join(lines)


def main() -> None:
    entries = load_systems()
    low_coverage = find_low_coverage(entries)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")

    if not low_coverage:
        print("No low-coverage systems detected.")
        if REPORT_FILE.exists():
            REPORT_FILE.unlink()
        if summary_path:
            with open(summary_path, "a") as f:
                f.write("### ✅ No low-coverage systems detected\n")
        return

    report = render_report(low_coverage)
    print(report)
    REPORT_FILE.write_text(report)

    if summary_path:
        with open(summary_path, "a") as f:
            f.write(report + "\n")


if __name__ == "__main__":
    main()
