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
from datetime import datetime, timezone
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
from page_shell import (
    CHECK_ICON_SVG,
    EXTERNAL_LINK_ICON_SVG,
    FAVICON_LINK,
    FIGMA_ICON_SVG,
    FLAG_ICON_SVG,
    FONT_LINK,
    GITHUB_LOGO_SVG,
    MCP_LOGO_SVG,
    NPM_LOGO_SVG,
    REPORT_ISSUE_URL,
    STORYBOOK_LOGO_SVG,
    TOKENS_CSS,
    routes_nav,
)
from slug import slugify
from text_utils import clean_title, dedupe_system_name, full_name, humanize_url_path, split_org_name

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


# How long since the last commit before a repo reads as possibly-inactive
# rather than just quiet — a design system genuinely can go this long
# between releases without being abandoned, so this is a cue to look closer,
# not a claim the system is dead (compare archived_status, which is a human
# verdict after actually checking, not a date threshold).
STALE_REPO_THRESHOLD_DAYS = 365


def format_short_date(value: str | None) -> str:
    """Compact absolute date for a resource badge — e.g. "12 Aug 2025", unlike
    format_iso's full date+time. A relative "2mo ago" reads nicer but is
    computed at *build* time and baked into static HTML, so it silently goes
    stale between rebuilds (and would be flatly wrong if a rebuild ever
    failed for a while) — an absolute date has no such expiry, and matches
    every other date already shown on this page. Empty string for anything
    unparseable so a caller can just omit the suffix entirely."""
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return dt.strftime("%-d %b %Y")


def format_repo_activity(github_meta: dict) -> str:
    """GitHub's own pushed_at (last commit push, to any branch) — a
    genuinely more direct "is this actively maintained" signal than stars or
    license, which say nothing about whether anyone still touches the code.
    Empty string (not "unknown") when there's no github_meta at all, so the
    freshness box just omits the row entirely rather than showing a claim
    with nothing behind it."""
    last_pushed = github_meta.get("last_pushed")
    if not last_pushed:
        return ""
    try:
        dt = datetime.fromisoformat(last_pushed.replace("Z", "+00:00"))
    except ValueError:
        return html.escape(last_pushed)
    formatted = dt.strftime("%-d %b %Y")
    days_since = (datetime.now(timezone.utc) - dt).days
    if days_since > STALE_REPO_THRESHOLD_DAYS:
        return f'<span class="stale-repo" title="No commits in over a year — worth checking whether this is still actively maintained">{formatted}</span>'
    return formatted


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
    "owner/repo" for GitHub, the package name for npm, else the path (plus
    query string, when there is one). The path is what's actually different
    between two links under the same type sharing the same domain — e.g.
    "/llms.txt" vs "/llms-full.txt" vs "/.well-known/mcp.json" all on the
    same wordpress.com blog — so it's shown in preference to the domain,
    which was identical (and so useless as a distinguisher) across every
    single resource pill on a system's page. Falls back to the domain only
    when there's really no path to show (a bare "https://example.com/")."""
    match = re.search(r"github\.com/([\w.-]+/[\w.-]+)", url, re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(r"npmjs\.com/package/([\w.@/-]+)", url, re.IGNORECASE)
    if match:
        return match.group(1)
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return path or parsed.netloc


# Real brand marks for the resource types common enough to have one — the
# same recognizable logo a visitor already knows from those tools' own
# sites, rather than one generic arrow-out-of-box glyph for every resource
# type regardless of what it actually is. Anything not listed here (MCP
# server icon aside, which get their own dedicated look below) falls back to
# EXTERNAL_LINK_ICON_SVG.
_RESOURCE_ICONS = {
    "GitHub": GITHUB_LOGO_SVG,
    "npm": NPM_LOGO_SVG,
    "Storybook": STORYBOOK_LOGO_SVG,
    "MCP server": MCP_LOGO_SVG,
    "Figma": FIGMA_ICON_SVG,
}


def resource_activity(entry: dict, label: str, url: str) -> str:
    """Last-activity date for a GitHub/npm resource pill, when it's known —
    enrich_resources() only ever fetches metadata for the FIRST url under
    each of those keys (one API call per system, not one per discovered
    link), so this only has an answer for that one representative link;
    every other same-type link (e.g. a second npm package) just gets no
    suffix rather than a wrong or duplicated one."""
    resources = entry.get("resources") or {}
    if label == "GitHub" and resources.get("github") and url == resources["github"][0]:
        last_pushed = (entry.get("github_meta") or {}).get("last_pushed")
        date = format_short_date(last_pushed)
        return f' <span class="resource-activity" title="Last commit: {html.escape(last_pushed or "")}">{date}</span>' if date else ""
    if label == "npm" and resources.get("npm") and url == resources["npm"][0]:
        last_published = (entry.get("npm_meta") or {}).get("last_published")
        date = format_short_date(last_published)
        return f' <span class="resource-activity" title="Last published: {html.escape(last_published or "")}">{date}</span>' if date else ""
    return ""


# A system with a real monorepo/multi-framework setup can turn up a couple
# dozen GitHub links or Figma files under one resource type (confirmed live
# on IBM Carbon: 18+ repos, 20+ Figma files) — a flat wall of pills that long
# reads as noise, not signal. Grouped by type with a per-group count is the
# fix either way; a group past this size also starts collapsed so the page
# opens showing what's THERE (which types, how many) rather than every link.
RESOURCE_GROUP_COLLAPSE_THRESHOLD = 4

# How many trailing characters of a resource link's label always stay fully
# visible (see item_html's head/tail split below) — enough for a filename
# plus extension ("llms-full.txt", ".well-known/mcp.json") to survive
# truncation intact even when the row is too narrow for the whole path.
_TRUNCATE_TAIL_CHARS = 24


def render_resource_list(entry: dict) -> str:
    resources = entry.get("resources") or {}
    groups = [(label, resources[key]) for key, label in COLUMNS + DETAIL_ONLY_COLUMNS if resources.get(key)]
    if not groups:
        return '<p class="empty-note">No secondary resources discovered yet.</p>'

    def item_html(label: str, url: str) -> str:
        # The group header below already names the type (GitHub, Figma, ...),
        # so each link just needs to distinguish this URL from its siblings
        # under the same type — the repo/package name, or the path for
        # anything else (see resource_url_label). Split into a shrink-to-
        # ellipsis "head" and an always-visible "tail" (see .link-text* below)
        # so a long path truncates in the MIDDLE rather than the end — the
        # end (".../llms-full.txt", ".../mcp.json") is exactly the part that
        # actually distinguishes one link from another under the same type,
        # so a plain end-ellipsis would cut off the one part that matters.
        text = resource_url_label(url)
        # Negative-index slicing already does the right thing when text is
        # shorter than the tail budget (head comes back "", tail comes back
        # the whole string) — no separate short-text branch needed.
        head, tail = text[:-_TRUNCATE_TAIL_CHARS], text[-_TRUNCATE_TAIL_CHARS:]
        text_html = (
            f'<span class="link-text"><span class="link-text-head">{html.escape(head)}</span>'
            f'<span class="link-text-tail">{html.escape(tail)}</span></span>'
        )
        return f'<li><a href="{html.escape(url)}" target="_blank" rel="noopener">{text_html}</a>{resource_activity(entry, label, url)}</li>'

    def group_html(label: str, urls: list[str]) -> str:
        count = len(urls)
        icon = _RESOURCE_ICONS.get(label, EXTERNAL_LINK_ICON_SVG)
        items = "".join(item_html(label, url) for url in urls)
        open_attr = "" if count > RESOURCE_GROUP_COLLAPSE_THRESHOLD else " open"
        return (
            f'<details class="resource-group"{open_attr}>'
            f'<summary>{icon} <span class="resource-group-label">{html.escape(label)}</span>'
            f'<span class="resource-group-count">{count}</span></summary>'
            f'<ul class="resource-list">{items}</ul>'
            f"</details>"
        )

    return "".join(group_html(label, urls) for label, urls in groups)


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


def _signal_pill(label: str, value, source_url: str | None = None) -> str:
    # A True-valued signal (dark mode, governance documented, ...) has
    # nothing to attach an eyebrow label to — the label itself IS the whole
    # claim, so it renders as normal-weight .signal-value text like any
    # other confirmed detection, not the faint uppercase .signal-label
    # style. That faint style is reserved for an eyebrow that introduces an
    # actual attached value (e.g. "ACCESSIBILITY" before "WCAG 2.1 AA") —
    # applying it to a bare boolean flag's only text made every one of these
    # pills read as muted/disabled rather than as a positive, confirmed hit.
    if value is True:
        inner = f'<span class="signal-value">{html.escape(label)}</span>'
    else:
        inner = f'<span class="signal-label">{html.escape(label)}</span><span class="signal-value">{html.escape(str(value))}</span>'
    if source_url:
        inner += (
            f'<a class="signal-source" href="{html.escape(source_url)}" target="_blank" rel="noopener" '
            f'title="Source: {html.escape(source_url)}">{EXTERNAL_LINK_ICON_SVG}</a>'
        )
    return f'<span class="signal-pill">{inner}</span>'


def _signal_section(title: str, signals: dict, labels: dict, sources: dict | None = None) -> str:
    sources = sources or {}
    pills = []
    for key, label in labels.items():
        value = signals.get(key)
        if not value:
            continue
        if key == "frameworks":
            # Multi-value — one pill per framework rather than one combined
            # "FRAMEWORKS  React, Vue, ..." pill, so each can carry its own
            # source link (a system's React docs and Svelte docs are
            # genuinely different pages) instead of one link standing in
            # for all of them.
            framework_sources = sources.get(key) or {}
            for framework in value:
                pills.append(_signal_pill(framework, True, framework_sources.get(framework)))
        else:
            pills.append(_signal_pill(label, value, sources.get(key)))
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


def _detect_ai_native(entry: dict) -> tuple[dict[str, bool], dict[str, str]]:
    """Maps this system's already-detected resources/content_signals onto
    the 11-category taxonomy above. Five categories are genuinely new
    detectors (see content_signals.py); the rest reuse a resource or signal
    that was already being tracked for a different reason, just re-read
    through this particular lens. Returns (flags, sources) — sources gives
    the one real URL backing each detected flag (a resource link already
    has one; a content_signals-based flag uses the crawled page
    content_signals.py recorded it on) so a visitor can check the actual
    evidence instead of taking a checkmark's word for it."""
    resources = entry.get("resources") or {}
    signals = entry.get("content_signals") or {}
    signal_sources = entry.get("content_signal_sources") or {}
    agent_instruction_urls = resources.get("agent_instructions") or []

    # llms.txt/llms-full.txt specifically — the "condensed for a context
    # window" half of agent_instructions, as distinct from the
    # AGENTS.md/CLAUDE.md/editor-rules half below.
    curated_context_url = next(
        (url for url in agent_instruction_urls if re.search(r"/llms(-full)?\.txt$", url, re.IGNORECASE)), None
    )
    # AGENTS.md/CLAUDE.md (the other half of agent_instructions) plus the
    # editor-specific equivalents, which are their own resource keys.
    instruction_files_url = (
        next((url for url in agent_instruction_urls if re.search(r"/(agents|claude)\.md$", url, re.IGNORECASE)), None)
        or (resources.get("copilot_instructions") or [None])[0]
        or (resources.get("cursor_rules") or [None])[0]
    )
    registry_url = (resources.get("registry") or [None])[0]

    flags = {
        "validation_loop": bool(signals.get("validation_loop")),
        "prohibition": bool(signals.get("prohibition")),
        "curated_context": bool(curated_context_url),
        "tool_gating": bool(signals.get("tool_gating")),
        "token_enforcement": bool(signals.get("token_enforcement")),
        "exemplars": bool(signals.get("exemplars")),
        "instruction_files": bool(instruction_files_url),
        "registry_metadata": bool(registry_url),
        "scaffolding": bool(signals.get("cli_scaffolding")),
        "design_code_mapping": bool(signals.get("figma_code_connect")),
    }
    sources = {
        "validation_loop": signal_sources.get("validation_loop"),
        "prohibition": signal_sources.get("prohibition"),
        "curated_context": curated_context_url,
        "tool_gating": signal_sources.get("tool_gating"),
        "token_enforcement": signal_sources.get("token_enforcement"),
        "exemplars": signal_sources.get("exemplars"),
        "instruction_files": instruction_files_url,
        "registry_metadata": registry_url,
        "scaffolding": signal_sources.get("cli_scaffolding"),
        "design_code_mapping": signal_sources.get("figma_code_connect"),
    }
    return flags, sources


def render_ai_native(entry: dict) -> str:
    flags, sources = _detect_ai_native(entry)
    if not any(flags.values()):
        return ""

    def item_html(key: str, label: str) -> str:
        detected = flags.get(key)
        mark = CHECK_ICON_SVG if detected else "–"
        source_url = sources.get(key)
        label_html = (
            f'<a href="{html.escape(source_url)}" target="_blank" rel="noopener" title="Source: {html.escape(source_url)}">{html.escape(label)}</a>'
            if detected and source_url
            else html.escape(label)
        )
        return f'<li class="ai-native-item{"" if detected else " is-absent"}"><span class="ai-native-mark">{mark}</span> {label_html}</li>'

    items = "".join(item_html(key, label) for key, label in _AI_NATIVE_LABELS.items())
    return f"""
  <section class="block">
    <h2>AI-Native</h2>
    <p class="section-note">Techniques this system uses to keep a model from inventing components or tokens \
instead of using real ones — see <a href="https://state-of-ai-in-design-systems.netlify.app/techniques" target="_blank" rel="noopener">the survey</a> \
this taxonomy is drawn from. Detected independently from this system's own crawled pages, not copied from that \
survey's own per-system data. A dash means not detected, not confirmed absent; a linked item names the page it was found on.</p>
    <ul class="ai-native-list">{items}</ul>
  </section>"""


def render_content_signals(entry: dict) -> str:
    """Best-effort extras detected from the system's own crawled text (see
    content_signals.py) — niche/lower-confidence than the hard resource
    links above, so they live here on the detail page rather than the
    summary matrix. Only fields actually detected are shown; nothing here
    implies a "no" for anything absent. Each pill links back to the actual
    crawled page it was detected on when that's known (content_signals.py
    records it going forward — older entries crawled before that existed
    just show no link, same as any other signal with no source on file)."""
    signals = entry.get("content_signals") or {}
    if not signals:
        return ""
    sources = entry.get("content_signal_sources") or {}
    return (
        _signal_section("Tech stack", signals, _TECH_STACK_LABELS, sources)
        + _signal_section("Capabilities", signals, _CAPABILITY_LABELS, sources)
        + _signal_section("Governance", signals, _GOVERNANCE_LABELS, sources)
        + _signal_section("AI tooling", signals, _AI_TOOLING_LABELS, sources)
    )


def render_page_list(name: str, pages_index: dict, org: str = "", ds_name: str = "") -> str:
    pages = pages_index.get(name) or []
    if not pages:
        return '<p class="empty-note">No pages indexed yet.</p>'

    site_titles = [p["title"] for p in pages]
    seen_titles: set[str] = set()

    def page_title(p: dict, url: str) -> str:
        cleaned = clean_title(p["title"], org, ds_name, name, site_titles=site_titles)
        # A page whose title IS the system name (already shown in the H1
        # above) would otherwise show as a link with no distinguishing label
        # of its own — fall back to the raw cleaned title rather than an
        # empty link.
        cleaned = dedupe_system_name(cleaned, name) or cleaned
        # extract_title() itself only falls back to a bare URL when there's
        # truly nothing else on the page (no <title>, no og:title, no <h1>)
        # — pages_index.json entries written before that fallback chain
        # existed can still carry a raw URL as their stored title, which
        # clean_title() has nothing to clean out of it. Same fallback for a
        # title that clean_title() couldn't distinguish from another page on
        # this same system (a repeated "Home"/site-name-only title, seen
        # verbatim hundreds of times on some systems) — a list of identical
        # link texts is as useless as the raw URL was.
        if cleaned.lower().startswith(("http://", "https://")) or cleaned in seen_titles:
            cleaned = humanize_url_path(url)
        seen_titles.add(cleaned)
        return cleaned

    items = "".join(
        f'<li><a href="{html.escape(p["url"])}" target="_blank" rel="noopener">{html.escape(page_title(p, p["url"]))}</a></li>'
        for p in sorted(pages, key=lambda p: clean_title(p["title"], org, ds_name, name, site_titles=site_titles).lower())
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
    if entry.get("has_localized_docs"):
        # The crawl itself only ever indexes the English side (see
        # LANGUAGE_EXCLUDE_PATTERNS in ingest.py) — this just tells a visitor
        # the system's docs exist in other languages too, at the same site.
        meta_parts.append("also documented in other languages (English indexed here)")

    slug = slugify(name_text)
    thumb_html = ""
    if start_url:
        large_src = thumbnail_src(name_text, start_url, size="detail")
        small_src = thumbnail_src(name_text, start_url, size="card")
        # Three-tier fallback, poorest-first: the large (800w) hero shot is
        # the common case, but a system with only the small (320w) stored
        # screenshot committed — or an mshots URL that renders one size and
        # not the other — otherwise showed nothing at all. Second onerror
        # (small failed too — e.g. Feelix's feelix.myob.com, a GitHub Pages
        # site gated behind GitHub SSO, so even mshots' live fetch just
        # renders a login wall/fails outright) drops the src entirely rather
        # than leaving the browser's own broken-image icon on screen — the
        # element stays an <img>, so .hero-thumb's own background/border/
        # aspect-ratio (all apply with no image loaded) show through as a
        # plain placeholder box instead.
        thumb_html = (
            f'<a href="{html.escape(start_url)}" target="_blank" rel="noopener">'
            f'<img class="hero-thumb" src="{large_src}" alt="" loading="lazy" '
            f'data-fallback="{html.escape(small_src)}" '
            f"onerror=\"if(this.dataset.fallback){{this.src=this.dataset.fallback;delete this.dataset.fallback;}}"
            f"else{{this.removeAttribute('src');}}\" "
            f'style="view-transition-name: thumb-{slug}; view-transition-class: thumb"></a>'
        )

    domain = urlparse(start_url).netloc if start_url else ""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(ds_name)} — Design Systems Directory</title>
{FAVICON_LINK}
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
  /* No commits in over a year (see format_repo_activity's threshold) — a
     cue to look closer, not a claim the system is dead. */
  .stale-repo {{ color: #f59e0b; }}

  .hero-thumb {{
    display: block; width: 100%; aspect-ratio: 16 / 9; object-fit: cover; object-position: top;
    border-radius: 10px; border: 1px solid var(--border); background: var(--surface-sunken); margin-bottom: 28px;
  }}
  /* Visible even when the hero-thumb below it fails to load (an access-gated
     site, e.g. a GitHub Pages site behind SSO, or a slow/never-rendered
     mshots screenshot) — that image's own <a> wrapper is otherwise the
     ONLY way to reach the real site from this page, and a broken image has
     no visible click target at all. */
  .visit-site {{
    display: inline-flex; align-items: center; gap: 5px; font-size: 0.85rem;
    color: var(--text-muted); text-decoration: none; margin: 4px 0 14px;
  }}
  .visit-site:hover {{ color: var(--accent); text-decoration: underline; }}
  .visit-site svg {{ flex: none; }}

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
  /* Small linked-source icon riding along on a pill that has one — muted at
     rest so it doesn't compete with the pill's own text, accent on hover so
     it still reads as clickable. */
  .signal-source {{ display: inline-flex; color: var(--text-faint); }}
  .signal-source:hover {{ color: var(--accent); }}
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
  /* A detected item with a known source page is a real link, not just bold
     text — underlined so it reads as clickable evidence, same visual
     language as .section-note's own link to the survey above. */
  .ai-native-item a {{ color: inherit; text-decoration: underline; text-underline-offset: 2px; }}
  .ai-native-item a:hover {{ color: var(--accent); }}
  .resource-list, .page-list {{ list-style: none; margin: 0; padding: 0; }}
  /* One full-width row per link rather than wrapped inline pills — a path
     like ".well-known/mcp.json" needs real width to stay readable, and a
     fixed-width pill either clipped it outright or wrapped the row in a way
     that made a handful of links look like a much longer list. */
  .resource-list {{ display: flex; flex-direction: column; gap: 4px; margin-top: 10px; }}
  .resource-list li {{ display: flex; align-items: center; gap: 8px; }}
  .resource-list li a {{
    display: flex; align-items: center; gap: 8px; min-width: 0; flex: 1;
    padding: 8px 12px; border: 1px solid var(--border); border-radius: 8px;
    text-decoration: none; font-size: 0.85rem; font-weight: 500;
  }}
  .resource-list li a svg {{ flex: none; }}
  .resource-list li a:hover {{ border-color: var(--accent); color: var(--accent); }}
  /* Middle truncation: .link-text-head shrinks and ellipsizes on the right
     as space runs out, while .link-text-tail (the last _TRUNCATE_TAIL_CHARS
     characters — see item_html) never shrinks and always renders in full,
     so a long path reads as "start/of/the/path…file.json" rather than
     losing the filename/extension at the end, which is usually the one
     part that actually distinguishes it from a sibling link. */
  .link-text {{ display: flex; min-width: 0; overflow: hidden; }}
  .link-text-head {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }}
  .link-text-tail {{ white-space: nowrap; flex: none; }}
  .resource-activity {{ font-size: 0.76rem; color: var(--text-faint); white-space: nowrap; flex: none; }}
  /* One collapsible <details> per resource type — see render_resource_list.
     A system with a couple dozen GitHub repos or Figma files no longer
     dumps every single one into view; the count in the header tells you
     what's there before you open it. */
  .resource-group {{ border: 1px solid var(--border); border-radius: 8px; padding: 10px 14px; margin-bottom: 8px; }}
  .resource-group summary {{
    display: flex; align-items: center; gap: 8px; cursor: pointer; font-size: 0.88rem; font-weight: 600;
    list-style: none;
  }}
  .resource-group summary::-webkit-details-marker {{ display: none; }}
  .resource-group summary svg {{ flex: none; }}
  /* A plain chevron drawn from the summary's own box model rather than an
     inline SVG — rotates open/closed via the <details>'s [open] state,
     matching the name-cell chevron's language elsewhere on the site
     without needing a second icon asset. */
  .resource-group summary::after {{
    content: ""; width: 7px; height: 7px; margin-left: auto;
    border-right: 1.5px solid var(--text-faint); border-bottom: 1.5px solid var(--text-faint);
    transform: rotate(-45deg); transition: transform 0.15s;
  }}
  .resource-group[open] summary::after {{ transform: rotate(45deg); }}
  .resource-group-label {{ color: var(--text); }}
  .resource-group-count {{
    font-family: "JetBrains Mono", monospace; font-size: 0.72rem; font-weight: 700; color: var(--text-muted);
    background: var(--surface-sunken); border-radius: 999px; padding: 1px 8px;
  }}
  .page-list {{ columns: 2; column-gap: 24px; }}
  .page-list li {{ padding: 6px 0; border-bottom: 1px solid var(--border); break-inside: avoid; }}
  .page-list a {{ text-decoration: none; }}
  .page-list a:hover {{ text-decoration: underline; }}
  .empty-note {{ color: var(--text-faint); font-size: 0.88rem; }}
  .report-issue {{ margin-top: 36px; padding-top: 16px; border-top: 1px solid var(--border); }}
  .report-issue a {{
    display: inline-flex; align-items: center; gap: 5px;
    font-size: 0.82rem; color: var(--text-faint); text-decoration: underline; text-underline-offset: 2px;
  }}
  .report-issue a svg {{ flex: none; }}
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
  {f'<a class="visit-site" href="{html.escape(start_url)}" target="_blank" rel="noopener">Visit {html.escape(domain)}{EXTERNAL_LINK_ICON_SVG}</a>' if start_url else ""}
  <p class="meta-line">{coverage_html}{" · ".join(html.escape(m) for m in meta_parts)}</p>
  {thumb_html}

  <dl class="freshness">
    <div><dt>Site last updated</dt><dd>{format_site_updated(entry)}</dd></div>
    <div><dt>Last checked here</dt><dd>{format_iso(entry.get("last_checked"))}</dd></div>
    {f'<div><dt>Repo last updated</dt><dd>{format_repo_activity(github_meta)}</dd></div>' if format_repo_activity(github_meta) else ""}
  </dl>

  <section class="block" id="resources">
    <h2>Resources</h2>
    {render_resource_list(entry)}
  </section>
{render_content_signals(entry)}
{render_ai_native(entry)}

  <section class="block">
    <h2>Indexed pages</h2>
    {render_page_list(name_text, pages_index, org, ds_name)}
  </section>

  <p class="report-issue">
    <a href="{REPORT_ISSUE_URL}?system={quote(name_text)}" id="reportIssueLink">{FLAG_ICON_SVG} Report an issue with this system</a>
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
    wanted_slugs = {slugify(full_name(entry)) for entry in entries}
    # A system that's since been archived, renamed, or dropped back to zero
    # pages_indexed (indexed_only() then excludes it) otherwise leaves its
    # old <slug>.html sitting in systems/ forever — still deployed, still
    # reachable by anyone with the old link, describing a system the
    # directory no longer lists at all. Confirmed live: brightcore-ui,
    # datadog-druids, gusto-workbench.
    for existing in SYSTEMS_DIR.glob("*.html"):
        if existing.stem not in wanted_slugs:
            existing.unlink()
    for entry in entries:
        out_path = SYSTEMS_DIR / f"{slugify(full_name(entry))}.html"
        out_path.write_text(render_system_page(entry, pages_index))

    print(f"Wrote {len(entries)} system detail pages to {SYSTEMS_DIR}/")


if __name__ == "__main__":
    main()
