"""
Builds one detail page per system (systems/<slug>.html): full-size preview,
complete resource list, freshness info (when the site itself last changed vs.
when we last checked it), and the full list of crawled pages — replacing the
cramped in-card "Browse N pages" expander with its own page.

The directory page's cards become lightweight previews linking here, the way
a search-results snippet links to the real page rather than inlining it.
"""

from __future__ import annotations

import html
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote, urlparse

from generate_directory import (
    COLUMNS,
    COVERAGE_GLYPH,
    COVERAGE_LABEL,
    DETAIL_ONLY_COLUMNS,
    coverage_status,
    favicon_html,
    indexed_only,
    load_pages_index,
    load_systems,
    thumbnail_src,
)
from page_shell import CHECK_ICON_SVG, EXTERNAL_LINK_ICON_SVG, FONT_LINK, GITHUB_ICON_SVG, REPORT_ISSUE_URL, TOKENS_CSS, routes_nav
from slug import slugify
from text_utils import clean_title, dedupe_system_name, full_name, split_org_name

SYSTEMS_DIR = Path(__file__).parent / "systems"


def format_http_date(value: str | None) -> str:
    if not value:
        return "unknown"
    try:
        return parsedate_to_datetime(value).strftime("%-d %b %Y")
    except (TypeError, ValueError):
        return value


def format_iso(value: str | None) -> str:
    if not value:
        return "never"
    try:
        dt = datetime.fromisoformat(value)
        return dt.strftime("%-d %b %Y, %H:%M UTC")
    except ValueError:
        return value


def format_site_updated(entry: dict) -> str:
    """Prefer sitemap_last_modified (resources.probe_sitemap) over the
    homepage's own Last-Modified header when both exist — a sitemap's
    <lastmod> reflects whatever page the site itself claims changed, not
    just the front door, so it catches a changed sub-page the header alone
    would miss entirely (see ingest.py's content_unchanged())."""
    if entry.get("sitemap_last_modified"):
        return f'{format_iso(entry["sitemap_last_modified"])} (via sitemap)'
    return format_http_date(entry.get("last_modified"))


def resource_url_label(url: str) -> str:
    """Short human-readable suffix distinguishing one URL from another
    classified under the same resource type (e.g. two npm packages) —
    "owner/repo" for GitHub, the package name for npm, else just the host."""
    match = re.search(r"github\.com/([\w.-]+/[\w.-]+)", url, re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(r"npmjs\.com/package/([\w.@/-]+)", url, re.IGNORECASE)
    if match:
        return match.group(1)
    return urlparse(url).netloc


_IDENTITY_LABELS = {"GitHub", "npm"}


def render_resource_list(entry: dict) -> str:
    resources = entry.get("resources") or {}
    # Any key can have more than one discovered link (e.g. two npm packages)
    # — every one is shown, not just the first, distinguished by a short
    # label when there's more than one so they don't look identical.
    found = [
        (label, url, len(resources[key]) > 1)
        for key, label in COLUMNS + DETAIL_ONLY_COLUMNS
        for url in resources.get(key, [])
    ]
    if not found:
        return '<p class="empty-note">No secondary resources discovered yet.</p>'

    def link_text(label: str, url: str, show_detail: bool) -> str:
        # GitHub/npm links already carry their icon, so the generic label
        # ("GitHub") is redundant — showing the actual repo/package name
        # instead is more useful than showing the type twice.
        if label in _IDENTITY_LABELS:
            return html.escape(resource_url_label(url))
        return f'{label}{f" — {html.escape(resource_url_label(url))}" if show_detail else ""}'

    items = "".join(
        f'<li><a href="{html.escape(url)}" target="_blank" rel="noopener">'
        f'{GITHUB_ICON_SVG if label == "GitHub" else EXTERNAL_LINK_ICON_SVG} '
        f'{link_text(label, url, show_detail)}</a></li>'
        for label, url, show_detail in found
    )
    return f'<ul class="resource-list">{items}</ul>'


# Split in two: frameworks/tokens describe what the system is BUILT WITH
# (implementation detail), while a11y/dark-mode/multi-brand describe what it
# DOES for a consumer (product-level capability) — different questions, so
# they get their own grouped section rather than one mixed bag of pills.
_TECH_STACK_LABELS = {
    "frameworks": "Frameworks",
    "tokens_format": "Tokens",
}
_CAPABILITY_LABELS = {
    "accessibility_conformance": "Accessibility",
    "dark_mode": "Dark mode",
    "multi_brand": "Multi-brand",
}
# A third, distinct question from either above: not what it's built with, nor
# what it does for a consumer, but how the system itself is run — does it
# document who gets to decide things, and what happens when something's
# retired. (Inspired by what dedicated design-system-audit tools — e.g.
# Murphy Trueman's design-system-ops, Brad Frost's ds-inspection skill —
# treat as first-class checks, alongside tokens/a11y/coverage.)
_GOVERNANCE_LABELS = {
    "governance_model": "Governance documented",
    "deprecation_policy": "Deprecation policy",
}
# A fourth question, from the same State of AI in Design Systems taxonomy
# resources.py's DETAIL_ONLY_COLUMNS draws from — AI-specific tooling beyond
# the hard MCP-server/skill/agent-instructions resources already tracked as
# proper resource links: things only ever mentioned in prose, not published
# at a fixed discoverable path.
_AI_TOOLING_LABELS = {
    "cli_scaffolding": "CLI scaffolding",
    "codemods": "Codemods",
    "figma_code_connect": "Figma Code Connect",
    "prompt_library": "Prompt library",
}


def _signal_pill(label: str, value) -> str:
    label_html = f'<span class="signal-label">{html.escape(label)}</span>'
    if value is True:
        return f'<span class="signal-pill">{label_html}</span>'
    value_text = ", ".join(html.escape(v) for v in value) if isinstance(value, list) else html.escape(str(value))
    return f'<span class="signal-pill">{label_html}<span class="signal-value">{value_text}</span></span>'


def _signal_section(title: str, signals: dict, labels: dict) -> str:
    pills = [_signal_pill(label, signals[key]) for key, label in labels.items() if signals.get(key)]
    if not pills:
        return ""
    return f"""
  <section class="block">
    <h2>{html.escape(title)}</h2>
    <div class="signal-pills">{"".join(pills)}</div>
  </section>"""


# The 11-category "techniques" taxonomy from
# https://state-of-ai-in-design-systems.netlify.app/techniques — ways a
# design system keeps a model from inventing components/tokens instead of
# using real ones. Unlike every other section on this page, absence here is
# shown explicitly (not just omitted) — the point is to see the system's
# coverage against the FULL taxonomy at a glance, the way the survey's own
# coverage table does, not just whatever happened to be detected. "Other" is
# excluded: it's explicitly a catch-all for techniques that don't fit any
# category, which by definition isn't something a fixed detector can catch.
_AI_NATIVE_LABELS = {
    "validation_loop": "Validation loop",
    "prohibition": "Prohibition",
    "curated_context": "Curated context",
    "tool_gating": "Tool-gating",
    "token_enforcement": "Token enforcement",
    "exemplars": "Exemplars",
    "instruction_files": "Instruction files",
    "registry_metadata": "Registry metadata",
    "scaffolding": "Scaffolding",
    "design_code_mapping": "Design–code mapping",
}


def _detect_ai_native(entry: dict) -> dict[str, bool]:
    """Maps this system's already-detected resources/content_signals onto
    the 11-category taxonomy above. Five categories are genuinely new
    detectors (see content_signals.py); the rest reuse a resource or signal
    that was already being tracked for a different reason, just re-read
    through this particular lens."""
    resources = entry.get("resources") or {}
    signals = entry.get("content_signals") or {}
    agent_instruction_urls = resources.get("agent_instructions") or []

    return {
        "validation_loop": bool(signals.get("validation_loop")),
        "prohibition": bool(signals.get("prohibition")),
        # llms.txt/llms-full.txt specifically — the "condensed for a context
        # window" half of agent_instructions, as distinct from the
        # AGENTS.md/CLAUDE.md/editor-rules half below.
        "curated_context": any(
            re.search(r"/llms(-full)?\.txt$", url, re.IGNORECASE) for url in agent_instruction_urls
        ),
        "tool_gating": bool(signals.get("tool_gating")),
        "token_enforcement": bool(signals.get("token_enforcement")),
        "exemplars": bool(signals.get("exemplars")),
        # AGENTS.md/CLAUDE.md (the other half of agent_instructions) plus the
        # editor-specific equivalents, which are their own resource keys.
        "instruction_files": (
            any(re.search(r"/(agents|claude)\.md$", url, re.IGNORECASE) for url in agent_instruction_urls)
            or bool(resources.get("copilot_instructions"))
            or bool(resources.get("cursor_rules"))
        ),
        "registry_metadata": bool(resources.get("registry")),
        "scaffolding": bool(signals.get("cli_scaffolding")),
        "design_code_mapping": bool(signals.get("figma_code_connect")),
    }


def render_ai_native(entry: dict) -> str:
    flags = _detect_ai_native(entry)
    if not any(flags.values()):
        return ""
    items = "".join(
        f'<li class="ai-native-item{"" if flags.get(key) else " is-absent"}">'
        f'<span class="ai-native-mark">{CHECK_ICON_SVG if flags.get(key) else "–"}</span> {html.escape(label)}</li>'
        for key, label in _AI_NATIVE_LABELS.items()
    )
    return f"""
  <section class="block">
    <h2>AI-Native</h2>
    <p class="section-note">Techniques this system uses to keep a model from inventing components or tokens \
instead of using real ones — see <a href="https://state-of-ai-in-design-systems.netlify.app/techniques" target="_blank" rel="noopener">the survey</a> \
this taxonomy is drawn from. A dash means not detected, not confirmed absent.</p>
    <ul class="ai-native-list">{items}</ul>
  </section>"""


def render_content_signals(entry: dict) -> str:
    """Best-effort extras detected from the system's own crawled text (see
    content_signals.py) — niche/lower-confidence than the hard resource
    links above, so they live here on the detail page rather than the
    summary matrix. Only fields actually detected are shown; nothing here
    implies a "no" for anything absent."""
    signals = entry.get("content_signals") or {}
    if not signals:
        return ""
    return (
        _signal_section("Tech stack", signals, _TECH_STACK_LABELS)
        + _signal_section("Capabilities", signals, _CAPABILITY_LABELS)
        + _signal_section("Governance", signals, _GOVERNANCE_LABELS)
        + _signal_section("AI tooling", signals, _AI_TOOLING_LABELS)
    )


def render_page_list(name: str, pages_index: dict) -> str:
    pages = pages_index.get(name) or []
    if not pages:
        return '<p class="empty-note">No pages indexed yet.</p>'

    def page_title(p: dict) -> str:
        cleaned = clean_title(p["title"])
        # A page whose title IS the system name (already shown in the H1
        # above) would otherwise show as a link with no distinguishing label
        # of its own — fall back to the raw cleaned title rather than an
        # empty link.
        return dedupe_system_name(cleaned, name) or cleaned

    items = "".join(
        f'<li><a href="{html.escape(p["url"])}" target="_blank" rel="noopener">{html.escape(page_title(p))}</a></li>'
        for p in sorted(pages, key=lambda p: clean_title(p["title"]).lower())
    )
    return f'<ul class="page-list">{items}</ul>'


def render_system_page(entry: dict, pages_index: dict) -> str:
    name_text = full_name(entry)
    org, ds_name = split_org_name(entry)
    start_url = (entry.get("start_urls") or [None])[0]
    favicon = favicon_html(start_url)

    pages_count = entry.get("pages_indexed")
    github_meta = entry.get("github_meta") or {}
    npm_meta = entry.get("npm_meta") or {}

    status = coverage_status(entry)
    coverage_html = (
        f'<span class="coverage-dot coverage-{status}" title="{html.escape(COVERAGE_LABEL[status])}">'
        f'{COVERAGE_GLYPH[status]}</span> '
        if pages_count is not None
        else ""
    )
    meta_parts = []
    if pages_count is not None:
        meta_parts.append(f"{pages_count} pages indexed")
    if github_meta.get("license"):
        meta_parts.append(github_meta["license"])
    if github_meta.get("stars") is not None:
        meta_parts.append(f'{github_meta["stars"]:,}★')
    if npm_meta.get("latest_version"):
        meta_parts.append(f'v{npm_meta["latest_version"]}')

    slug = slugify(name_text)
    thumb_html = ""
    if start_url:
        src = thumbnail_src(name_text, start_url, size="detail")
        # Shared view-transition-name with this same card's .card-thumb on the
        # directory grid (see generate_directory.py's render_card) — the
        # browser morphs one into the other on navigation instead of a hard cut.
        thumb_html = (
            f'<a href="{html.escape(start_url)}" target="_blank" rel="noopener">'
            f'<img class="hero-thumb" src="{src}" alt="" loading="lazy" style="view-transition-name: thumb-{slug}; view-transition-class: thumb"></a>'
        )

    domain = urlparse(start_url).netloc if start_url else ""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(ds_name)} — Design Systems Directory</title>
{FONT_LINK}
<style>
{TOKENS_CSS}
  .system-header {{ display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }}
  .org-label {{ font-size: 0.78rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; color: var(--text-faint); }}
  .meta-line {{ font-family: "JetBrains Mono", monospace; font-size: 0.85rem; color: var(--text-muted); margin: 0 0 20px; }}
  .meta-line .badge {{ background: var(--accent-soft); color: var(--accent-soft-text); border-radius: 4px; padding: 1px 6px; }}
  .coverage-dot {{ font-size: 1.05rem; line-height: 1; vertical-align: -1px; }}
  .coverage-dot.coverage-full {{ color: #22c55e; }}
  .coverage-dot.coverage-partial {{ color: #f59e0b; }}
  .coverage-dot.coverage-unknown {{ color: var(--text-faint); }}

  .hero-thumb {{
    display: block; width: 100%; aspect-ratio: 16 / 9; object-fit: cover; object-position: top;
    border-radius: 10px; border: 1px solid var(--border); background: var(--surface-sunken); margin-bottom: 28px;
  }}

  .freshness {{
    display: flex; gap: 24px; flex-wrap: wrap; background: var(--surface-sunken); border: 1px solid var(--border);
    border-radius: 8px; padding: 14px 18px; margin-bottom: 32px; font-size: 0.85rem;
  }}
  .freshness dt {{ font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.03em; color: var(--text-faint); margin: 0; }}
  .freshness dd {{ margin: 2px 0 0; font-family: "JetBrains Mono", monospace; color: var(--text); }}

  section.block {{ margin-bottom: 32px; }}
  section.block h2 {{
    font-family: "JetBrains Mono", monospace; font-size: 0.95rem; font-weight: 700; margin: 0 0 12px;
    padding-bottom: 8px; border-bottom: 1px solid var(--border);
  }}
  .signal-pills {{ display: flex; flex-wrap: wrap; gap: 8px; }}
  .signal-pill {{
    display: inline-flex; align-items: center; gap: 6px; padding: 6px 12px; border-radius: 999px;
    background: var(--surface-sunken); border: 1px solid var(--border); font-size: 0.82rem;
  }}
  .signal-label {{
    font-size: 0.72rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; color: var(--text-faint);
  }}
  .signal-value {{ color: var(--text); font-weight: 500; }}
  .section-note {{ font-size: 0.82rem; color: var(--text-muted); margin: -6px 0 14px; max-width: 68ch; }}
  .section-note a {{ color: inherit; text-decoration: underline; text-underline-offset: 2px; }}
  /* Deliberately shows every taxonomy item, detected or not (see
     render_ai_native()) — a muted dash for "not detected" instead of hiding
     the row, so the list reads as a coverage checklist against the whole
     taxonomy rather than just a list of hits. */
  .ai-native-list {{
    list-style: none; margin: 0; padding: 0; display: grid;
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 4px 16px;
  }}
  .ai-native-item {{ display: flex; align-items: center; gap: 8px; font-size: 0.88rem; padding: 4px 0; }}
  .ai-native-mark {{ flex: none; display: flex; align-items: center; justify-content: center; width: 18px; color: #22c55e; }}
  .ai-native-item.is-absent {{ color: var(--text-faint); }}
  .ai-native-item.is-absent .ai-native-mark {{ color: var(--text-faint); }}
  .resource-list, .page-list {{ list-style: none; margin: 0; padding: 0; }}
  .resource-list {{ display: flex; flex-wrap: wrap; gap: 8px; }}
  .resource-list li a {{
    display: inline-flex; align-items: center; gap: 6px; padding: 6px 12px; border: 1px solid var(--border);
    border-radius: 999px; text-decoration: none; font-size: 0.85rem; font-weight: 500;
  }}
  .resource-list li a svg {{ flex: none; }}
  .resource-list li a:hover {{ border-color: var(--accent); color: var(--accent); }}
  .page-list {{ columns: 2; column-gap: 24px; }}
  .page-list li {{ padding: 6px 0; border-bottom: 1px solid var(--border); break-inside: avoid; }}
  .page-list a {{ text-decoration: none; }}
  .page-list a:hover {{ text-decoration: underline; }}
  .empty-note {{ color: var(--text-faint); font-size: 0.88rem; }}
  .report-issue {{ margin-top: 36px; padding-top: 16px; border-top: 1px solid var(--border); }}
  .report-issue a {{ font-size: 0.82rem; color: var(--text-faint); text-decoration: underline; text-underline-offset: 2px; }}
  .report-issue a:hover {{ color: var(--accent); }}
  @media (max-width: 600px) {{ .page-list {{ columns: 1; }} }}
</style>
</head>
<body>
{routes_nav("directory")}
<div class="page">
  <p class="eyebrow"><a href="/directory">All Systems</a></p>
  <div class="system-header">{favicon}<span class="org-label">{html.escape(org) if org else html.escape(domain)}</span></div>
  <h1>{html.escape(ds_name)}</h1>
  <p class="meta-line">{coverage_html}{" · ".join(html.escape(m) for m in meta_parts)}</p>
  {thumb_html}

  <dl class="freshness">
    <div><dt>Site last updated</dt><dd>{format_site_updated(entry)}</dd></div>
    <div><dt>Last checked here</dt><dd>{format_iso(entry.get("last_checked"))}</dd></div>
  </dl>

  <section class="block" id="resources">
    <h2>Resources</h2>
    {render_resource_list(entry)}
  </section>
{render_content_signals(entry)}
{render_ai_native(entry)}

  <section class="block">
    <h2>Indexed pages</h2>
    {render_page_list(name_text, pages_index)}
  </section>

  <p class="report-issue">
    <a href="{REPORT_ISSUE_URL}?system={quote(name_text)}" id="reportIssueLink">Report an issue with this system</a>
  </p>
</div>
<script>
  // The page's own live URL can't be known at build time (the site's actual
  // domain isn't fixed here) — filled in from what the visitor is looking
  // at, so /report arrives with its "Link to the system's detail page"
  // field already prefilled.
  (function () {{
    var link = document.getElementById("reportIssueLink");
    if (link) link.href += "&url=" + encodeURIComponent(window.location.href);
  }})();
</script>
</body>
</html>
"""


def main() -> None:
    entries = indexed_only(load_systems())
    pages_index = load_pages_index()

    SYSTEMS_DIR.mkdir(exist_ok=True)
    for entry in entries:
        out_path = SYSTEMS_DIR / f"{slugify(full_name(entry))}.html"
        out_path.write_text(render_system_page(entry, pages_index))

    print(f"Wrote {len(entries)} system detail pages to {SYSTEMS_DIR}/")


if __name__ == "__main__":
    main()
