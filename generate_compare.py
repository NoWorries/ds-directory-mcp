"""
Renders systems.yaml into compare.html — the resource-comparison matrix
every indexed design system against the resources discovered for it
(GitHub, Storybook, Figma, MCP server, agent instructions, etc.), plus
pages-indexed count.

Split out from generate_directory.py's own page (which used to hold this
as a "List" view alongside a card "Grid" view): browsing "what design
systems exist" and cross-referencing "which of them publish which
resources" are two different jobs a visitor comes in with, not two views
of the same task — this page is the second one, a different lens on the
same registry, not a mode of the plain system list.

Run after ingest.py --all, as part of the weekly reindex workflow. No live
backend needed to view the result — it's a self-contained static page.
"""

from __future__ import annotations

from pathlib import Path

from generate_directory import COLUMNS, found_count, indexed_only, load_systems, render_row
from page_shell import CLOSE_ICON_SVG, EXPAND_ICON_SVG, FAVICON_LINK, FONT_LINK, TOKENS_CSS, routes_nav
from text_utils import full_name

OUTPUT_FILE = Path(__file__).parent / "compare.html"


def render_page(entries: list[dict]) -> str:
    # Default sort: highest Found count first, not alphabetical — this page's
    # whole point is "which systems publish the most resources", so leading
    # with the ones that publish the least buried the actually interesting
    # rows below the fold. Name (secondary key) just keeps ties stable/
    # deterministic between regenerations rather than reflecting yaml order.
    entries_sorted = sorted(entries, key=lambda e: (-found_count(e), full_name(e).lower()))

    # Column indices: 0 = name (text sort), 1 = docs link (not sortable),
    # 2 = pages (number), 3..3+len(COLUMNS)-1 = resource dots (found),
    # last = found-count (number, pinned).
    header_cells = "".join(
        f'<th data-col="{i + 3}" data-sort="found">{label}</th>' for i, (_, label) in enumerate(COLUMNS)
    )
    last_col = len(COLUMNS) + 3
    rows = "".join(render_row(e) for e in entries_sorted)

    # See generate_directory.py's identical computation/reasoning.
    unmaintained_count = sum(1 for e in entries_sorted if e.get("likely_unmaintained"))
    unmaintained_toggle_html = (
        f'<label class="unmaintained-toggle">'
        f'<input type="checkbox" id="unmaintainedToggle"> Show {unmaintained_count} unmaintained'
        f'</label>'
        if unmaintained_count else ""
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Compare Design Systems — Design Systems Directory</title>
{FAVICON_LINK}
{FONT_LINK}
<style>
{TOKENS_CSS}
  /* .page (eyebrow/heading/subtitle) deliberately stays at the shared
     PAGE_MAX_WIDTH, same as every other page — no reason for the intro here
     to look different from the rest of the site. Only the table itself
     needs more room (with the full ~250-system registry and 9 resource
     columns, it wants noticeably more than this sample data's 4 rows show),
     so it lives in its own .table-breakout below: a separate block, centered
     independently at a wider fixed cap. */
  .table-breakout {{ max-width: 1600px; margin: 0 auto; }}
  .table-toolbar {{ display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; margin-bottom: 8px; }}
  #tableFilter {{
    padding: 8px 12px; font: inherit; font-size: 0.85rem; border: 1px solid var(--border);
    border-radius: 6px; width: 280px; max-width: 100%; background: var(--surface); color: var(--text);
  }}
  #tableFilter:focus {{ outline: 2px solid var(--accent); outline-offset: 1px; }}

  /* Classic CSS-only "scroll shadow": two background-attachment:local layers
     scroll WITH the content (so they only show at the true start/end, never
     appearing over content past the very edges) and two background-attachment:
     scroll shadow layers stay fixed to the viewport — the shadow only becomes
     visible once the local layer has scrolled out from under it, i.e. exactly
     when there's more table to the left/right than currently visible. */
  .table-wrap {{
    overflow-x: auto; border: 1px solid var(--border); border-radius: 10px;
    box-shadow: var(--shadow);
    background-color: var(--surface);
    background-image:
      linear-gradient(to right, var(--surface) 30%, rgba(0,0,0,0)),
      linear-gradient(to left, var(--surface) 30%, rgba(0,0,0,0)),
      linear-gradient(to right, rgba(15,23,42,0.15), rgba(15,23,42,0)),
      linear-gradient(to left, rgba(15,23,42,0.15), rgba(15,23,42,0));
    background-position: left center, right center, left center, right center;
    background-repeat: no-repeat;
    background-size: 24px 100%, 24px 100%, 10px 100%, 10px 100%;
    background-attachment: local, local, scroll, scroll;
  }}
  table {{ border-collapse: collapse; width: 100%; font-size: 0.86rem; }}
  th {{
    text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--border); font-weight: 600;
    white-space: nowrap; color: var(--text-muted); font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.03em;
    font-family: "JetBrains Mono", monospace;
  }}
  td {{ padding: 10px 12px; border-bottom: 1px solid var(--border); vertical-align: middle; }}
  /* Skips layout/paint for off-screen rows entirely — the load-bearing fix for
     hundreds of rows staying cheap without pagination or virtualization JS.
     contain-intrinsic-size is a rough guess at row height so the page doesn't
     jump around as rows are measured for the first time while scrolling. */
  tbody tr {{ content-visibility: auto; contain-intrinsic-size: auto 46px; }}
  tbody tr:last-child td {{ border-bottom: none; }}
  .name-cell {{ min-width: 200px; padding: 0; }}
  /* The whole column is one tap target (see render_name_block) — the <a>
     fills the cell itself (hence padding moved here from .name-cell) so
     there's no dead strip of cell around the name text that looks
     clickable but isn't. */
  .name-cell-link {{
    display: flex; align-items: center; justify-content: space-between; gap: 8px;
    padding: 10px 12px; color: inherit; text-decoration: none;
  }}
  .name-cell-link:hover {{ background: var(--surface-sunken); }}
  .name-cell-link:hover .name-chevron {{ color: var(--accent); transform: translateX(2px); }}
  .name-chevron {{ flex: none; display: flex; color: var(--text-faint); transition: transform 0.12s, color 0.12s; }}
  .name-block {{ display: flex; align-items: center; gap: 8px; min-width: 0; }}
  .name-text {{ display: flex; flex-direction: column; line-height: 1.25; min-width: 0; }}
  .org {{ font-size: 0.68rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; color: var(--text-faint); }}
  .ds-name {{ font-weight: 600; font-size: 0.92rem; }}
  .favicon {{ border-radius: 3px; flex: none; }}
  .cell {{ text-align: center; font-variant-numeric: tabular-nums; }}
  .cell-pages {{ font-family: "JetBrains Mono", monospace; color: var(--text-muted); text-align: left; }}
  /* coverage_status()'s three states — never shown as "unknown" here since
     indexed_only() already excludes those entries from every public page,
     but the rule stays complete for consistency with check_crawl_health.py. */
  .pages-value {{ display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }}
  .coverage-dot {{ font-size: 1.05rem; line-height: 1; }}
  .coverage-dot.coverage-full {{ color: #22c55e; }}
  .coverage-dot.coverage-partial {{ color: #f59e0b; }}
  .coverage-dot.coverage-unknown {{ color: var(--text-faint); }}
  /* A bare checkmark glyph doesn't read as clickable until you hover it —
     giving it a pill background (same visual language as .badge/.chip
     elsewhere) makes "this is a link" obvious at rest, not just on hover. */
  .cell-found a {{
    display: inline-flex; align-items: center; justify-content: center;
    color: var(--accent); background: var(--accent-soft); text-decoration: none;
    width: 26px; height: 22px; border-radius: 999px;
  }}
  .cell-found a:hover {{ background: var(--accent); color: #fff; }}
  /* Small count badge for when more than one link was classified under the
     same resource type (e.g. two npm packages) — sits at the pill's corner
     so it reads as "there's more here" without needing its own column. */
  .cell-multi a {{ position: relative; }}
  .cell-count {{
    position: absolute; top: -5px; right: -5px; min-width: 14px; height: 14px; padding: 0 3px;
    display: inline-flex; align-items: center; justify-content: center; border-radius: 999px;
    background: var(--accent); color: #fff; font-size: 0.6rem; font-weight: 700; line-height: 1;
    font-family: "JetBrains Mono", monospace;
  }}
  /* Distinct from the name link (which goes to this system's own detail
     page): same pill treatment, but muted rather than accent-colored so it
     doesn't read as another "resource found" indicator — this one always
     goes straight out to the system's real docs site. */
  .cell-docs a {{
    display: inline-flex; align-items: center; justify-content: center;
    color: var(--text-muted); background: var(--surface-sunken); text-decoration: none;
    width: 26px; height: 22px; border-radius: 999px; border: 1px solid var(--border);
  }}
  .cell-docs a:hover {{ color: var(--accent); border-color: var(--accent); }}
  .cell-none {{ color: var(--text-faint); }}
  /* Both edge columns are always pinned (sticky) — the shadow that signals
     "there's scrolled content hidden under here" is the part that's
     conditional, toggled by JS only when the table actually overflows its
     container (see the has-overflow class set near the bottom of the page's
     script). Without that, every row showed a shadow implying hidden
     content even on a table narrow enough to need no scrolling at all. */
  .name-cell, th[data-col="0"] {{ position: sticky; left: 0; background: var(--surface); z-index: 2; }}
  .cell-found-count, th:last-child {{ position: sticky; right: 0; background: var(--surface); z-index: 2; }}
  .table-wrap.has-overflow .name-cell,
  .table-wrap.has-overflow th[data-col="0"] {{ box-shadow: 6px 0 8px -8px rgba(15,23,42,0.25); }}
  .table-wrap.has-overflow .cell-found-count,
  .table-wrap.has-overflow th:last-child {{ box-shadow: -6px 0 8px -8px rgba(15,23,42,0.25); }}
  /* Small viewports: the 9 individual resource-dot columns (GitHub, npm,
     Storybook, ...) are the whole reason this table needs horizontal scroll
     at all — dropping them leaves System/Docs/Pages/Found, the columns
     visitors actually scan, and each system's full resource breakdown is
     still one tap away via the name link's detail panel. nth-child counts
     the *rendered* column position (1-based), not the data-col attribute:
     1=System, 2=Docs, 3=Pages, 4-12=the 9 resource columns, 13=Found. */
  @media (max-width: 640px) {{
    #compareTable th:nth-child(n+4):nth-child(-n+12),
    #compareTable td:nth-child(n+4):nth-child(-n+12) {{ display: none; }}
    /* With the resource columns gone, System/Docs/Pages/Found comfortably
       fit without horizontal scroll — drop the desktop layout's min-width
       and sticky pinning (built for a much wider table) so the sticky Found
       column doesn't sit pinned mid-row and overlap Pages while nothing
       actually needs scrolling into view. */
    #compareTable .name-cell {{ min-width: 0; }}
    #compareTable .name-cell, #compareTable th[data-col="0"],
    #compareTable .cell-found-count, #compareTable th:last-child {{ position: static; }}
    #compareTable th, #compareTable td {{ padding: 8px 6px; }}
    #compareTable .name-cell-link {{ padding: 8px 6px; }}
  }}
  th[data-col] {{ cursor: pointer; user-select: none; }}
  th[data-col]:hover {{ color: var(--accent); }}
  th.sorted-asc::after {{ content: " ▲"; font-size: 0.65em; }}
  th.sorted-desc::after {{ content: " ▼"; font-size: 0.65em; }}

  .unmaintained-toggle {{
    display: flex; align-items: center; gap: 6px; font-size: 0.82rem; color: var(--text-muted); cursor: pointer;
  }}
  .unmaintained-toggle:hover {{ color: var(--text); }}
  /* See generate_directory.py's identical rules for the full reasoning —
     hidden regardless of the name-filter's own [hidden] toggling, unless
     this toggle has also revealed it. */
  .is-unmaintained {{ display: none !important; }}
  .show-unmaintained tr.is-unmaintained {{ display: table-row !important; }}

  /* --- Detail side panel: lets a visitor preview a system without leaving
     the list (see the name-cell-link click handler below). Its content is
     the same systems/<slug>.html page, fetched and injected — not a second
     template to keep in sync — so it always matches the standalone page;
     "Open full page" just navigates there normally (and gets the .thumb
     view-transition morph on the way in, same as any other link to it).
     Deliberately no dimming overlay behind the panel — it stays open while
     the visitor clicks around the rest of the table, so they can open a
     different system without closing this one first. The currently-open
     system is highlighted instead (.is-active-entry below). */
  tr.is-active-entry {{ background: var(--accent-soft); }}
  tr.is-active-entry:hover {{ background: var(--accent-soft); }}
  .detail-panel {{
    position: fixed; top: 0; right: 0; height: 100%; width: min(480px, 92vw); z-index: 200;
    background: var(--surface); border-left: 1px solid var(--border);
    box-shadow: -12px 0 30px -12px rgba(15,23,42,0.3);
    transform: translateX(100%); transition: transform 0.22s ease;
    display: flex; flex-direction: column;
  }}
  .detail-panel.open {{ transform: translateX(0); }}
  .detail-panel-header {{
    display: flex; align-items: center; justify-content: space-between; gap: 12px; flex: none;
    padding: 14px 20px; border-bottom: 1px solid var(--border);
  }}
  .detail-panel-expand {{
    display: inline-flex; align-items: center; gap: 6px; font-size: 0.85rem; font-weight: 600;
    color: var(--accent); text-decoration: none;
  }}
  .detail-panel-expand:hover {{ text-decoration: underline; }}
  .detail-panel-close {{
    background: none; border: none; padding: 6px; margin: -6px; color: var(--text-muted); cursor: pointer;
    border-radius: 6px; display: flex;
  }}
  .detail-panel-close:hover {{ background: var(--surface-sunken); color: var(--text); }}
  .detail-panel-body {{ flex: 1; overflow-y: auto; padding: 8px 24px 32px; }}
  /* The injected content is the detail page's own `.page` div, which is
     normally centered at a max-width for a full page — inside the narrower
     panel it should just fill the available width instead. */
  .detail-panel-body .page {{ max-width: none; margin: 0; }}
  .detail-panel-body .page-list {{ columns: 1; }}
  @media (max-width: 640px) {{ .detail-panel {{ width: 100vw; }} }}
</style>
</head>
<body>
{routes_nav("compare")}
<div class="page">
  <p class="eyebrow">Compare</p>
  <h1>Cross-reference every system by resource</h1>
  <p class="subtitle">{len(entries_sorted)} external design systems, cross-referenced by the resources each one has published — GitHub, Storybook, Figma, tokens, and more. Looking for a plain, browsable list instead? See <a href="/directory">All Systems</a>. Regenerated weekly.</p>
</div>

<div class="table-breakout" id="tableBreakout">
  <div class="table-toolbar">
    <input id="tableFilter" type="text" placeholder="Filter by system or company name...">
    {unmaintained_toggle_html}
  </div>

  <div class="table-wrap" id="tableWrap">
  <table id="compareTable">
    <thead>
      <tr>
        <th data-col="0" data-sort="text">System</th>
        <th>Docs</th>
        <th data-col="2" data-sort="number">Pages</th>
        {header_cells}
        <th data-col="{last_col}" data-sort="number" class="sorted-desc">Found</th>
      </tr>
    </thead>
    <tbody>
      {rows}
    </tbody>
  </table>
  </div>
</div>

<aside class="detail-panel" id="detailPanel" aria-hidden="true">
  <div class="detail-panel-header">
    <a class="detail-panel-expand" id="detailPanelExpand" href="#">{EXPAND_ICON_SVG} Open full page</a>
    <button type="button" class="detail-panel-close" id="detailPanelClose" aria-label="Close">{CLOSE_ICON_SVG}</button>
  </div>
  <div class="detail-panel-body" id="detailPanelBody"></div>
</aside>

<script>
  // --- Table sort ---
  // Rows arrive from the server already sorted by Found descending (see
  // render_page()'s entries_sorted) — seeding currentSort to match means a
  // click on the Found header toggles to ascending first, same as clicking
  // any other already-sorted column a second time, instead of re-sorting
  // descending again because the JS didn't know a sort was already applied.
  let currentSort = {{ col: {last_col}, dir: -1 }};

  function cellValue(row, col, type) {{
    // Sorting the System column by the visible design-system name (e.g.
    // "Mozaic Design System"), not by row.dataset.name — that's the "Org —
    // Name" identity string used for the name-filter box, and sorting by it
    // instead would order rows by the org prefix, which isn't what's visually
    // prominent in the name cell and made the sort look wrong/arbitrary.
    if (col === 0) return row.dataset.sortName || "";
    const cell = row.children[col];
    if (!cell) return "";
    if (type === "found") return cell.classList.contains("cell-found") ? 1 : 0;
    if (type === "number") return parseFloat(cell.dataset.sortValue ?? cell.textContent) || 0;
    return (cell.textContent || "").trim().toLowerCase();
  }}

  function sortTable(th) {{
    const col = parseInt(th.dataset.col, 10);
    const type = th.dataset.sort;
    const tbody = document.querySelector("#compareTable tbody");
    const rows = Array.from(tbody.querySelectorAll("tr"));

    const dir = (currentSort.col === col && currentSort.dir === 1) ? -1 : 1;
    currentSort = {{ col, dir }};

    rows.sort((a, b) => {{
      const va = cellValue(a, col, type);
      const vb = cellValue(b, col, type);
      if (va < vb) return -1 * dir;
      if (va > vb) return 1 * dir;
      return 0;
    }});
    rows.forEach(row => tbody.appendChild(row));

    document.querySelectorAll("#compareTable th[data-col]").forEach(h => h.classList.remove("sorted-asc", "sorted-desc"));
    th.classList.add(dir === 1 ? "sorted-asc" : "sorted-desc");
  }}

  document.querySelectorAll("#compareTable th[data-col]").forEach(th => {{
    th.addEventListener("click", () => sortTable(th));
  }});

  // --- Name filter (plain substring match) ---
  const tableFilter = document.getElementById("tableFilter");
  tableFilter.addEventListener("input", () => {{
    const query = tableFilter.value.trim().toLowerCase();
    document.querySelectorAll("#compareTable tbody tr").forEach(row => {{
      row.hidden = Boolean(query) && !row.dataset.name.includes(query);
    }});
  }});

  // --- Unmaintained toggle (see .is-unmaintained/.show-unmaintained CSS) ---
  const unmaintainedToggle = document.getElementById("unmaintainedToggle");
  if (unmaintainedToggle) {{
    unmaintainedToggle.addEventListener("change", () => {{
      document.getElementById("tableBreakout").classList.toggle("show-unmaintained", unmaintainedToggle.checked);
    }});
  }}

  // --- Pinned-column shadows: only shown when the table actually overflows
  // its container (scrollWidth > clientWidth) — a table narrow enough to
  // need no horizontal scrolling has nothing hidden under the pinned edges,
  // so showing the shadow there would be a false "there's more, scroll" cue.
  const tableWrapEl = document.getElementById("tableWrap");
  function updateStickyShadow() {{
    if (!tableWrapEl) return;
    tableWrapEl.classList.toggle("has-overflow", tableWrapEl.scrollWidth > tableWrapEl.clientWidth + 1);
  }}
  updateStickyShadow();
  window.addEventListener("resize", updateStickyShadow);

  // --- Detail side panel: previews a system's systems/<slug>.html page
  // in-place, without leaving the table. Fetches and injects that same
  // page's .page content (plus its <style> block, once) rather than
  // keeping a second copy of the template, so the panel always matches the
  // real page.
  const detailPanel = document.getElementById("detailPanel");
  const detailPanelBody = document.getElementById("detailPanelBody");
  const detailPanelExpand = document.getElementById("detailPanelExpand");
  const detailPanelClose = document.getElementById("detailPanelClose");
  let detailStylesInjected = false;
  let activeSlug = null;

  function setActiveEntry(slug) {{
    if (activeSlug) {{
      document.querySelectorAll('.panel-link[data-slug="' + activeSlug + '"]').forEach((el) => {{
        (el.closest("tr") || el).classList.remove("is-active-entry");
      }});
    }}
    activeSlug = slug;
    if (activeSlug) {{
      document.querySelectorAll('.panel-link[data-slug="' + activeSlug + '"]').forEach((el) => {{
        (el.closest("tr") || el).classList.add("is-active-entry");
      }});
    }}
  }}

  async function openDetailPanel(link) {{
    const slug = link.dataset.slug;
    setActiveEntry(slug);
    // Two different URLs on purpose: the visible "Open full page" link is
    // the clean, extension-less path (matches every other in-page link —
    // see netlify.toml's pretty_urls), while the fetch() below hits the
    // real .html file directly rather than depending on Netlify's pretty-url
    // rewriting also applying to a same-page XHR.
    detailPanelExpand.href = "systems/" + slug;
    const fetchHref = "systems/" + slug + ".html";
    detailPanel.classList.add("open");
    detailPanel.setAttribute("aria-hidden", "false");
    detailPanelBody.innerHTML = '<p class="empty-note">Loading…</p>';

    try {{
      const response = await fetch(fetchHref);
      const doc = new DOMParser().parseFromString(await response.text(), "text/html");

      if (!detailStylesInjected) {{
        const styleTag = doc.querySelector("style");
        if (styleTag) {{
          const injected = document.createElement("style");
          injected.textContent = styleTag.textContent;
          document.head.appendChild(injected);
        }}
        detailStylesInjected = true;
      }}

      const pageEl = doc.querySelector(".page");
      if (!pageEl) throw new Error("no .page content");

      // Relative image paths (e.g. "screenshots/x.jpg") resolve against
      // systems/<slug>.html there — rewrite them to work from this page's
      // own location instead. Absolute/protocol-relative/data URLs are left
      // alone.
      pageEl.querySelectorAll("img[src]").forEach((img) => {{
        const src = img.getAttribute("src");
        if (src && !/^([a-z]+:)?\\/\\//i.test(src) && !src.startsWith("data:") && !src.startsWith("/")) {{
          img.setAttribute("src", "systems/" + src);
        }}
      }});
      // Redundant inside the panel — the "All Systems" breadcrumb is what
      // the close button already does.
      const eyebrow = pageEl.querySelector(".eyebrow");
      if (eyebrow) eyebrow.remove();

      detailPanelBody.innerHTML = pageEl.innerHTML;
    }} catch (err) {{
      detailPanelBody.innerHTML = '<p class="empty-note">Couldn\\'t load this system\\'s details.</p>';
    }}
  }}

  function closeDetailPanel() {{
    detailPanel.classList.remove("open");
    detailPanel.setAttribute("aria-hidden", "true");
    setActiveEntry(null);
  }}

  document.querySelectorAll(".panel-link").forEach((link) => {{
    link.addEventListener("click", (e) => {{
      // Only intercept a plain left click — modifier/middle/right clicks keep
      // their normal browser behavior (open in new tab, etc.) via the real href.
      if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      e.preventDefault();
      openDetailPanel(link);
    }});
  }});

  detailPanelClose.addEventListener("click", closeDetailPanel);
  document.addEventListener("keydown", (e) => {{
    if (e.key === "Escape" && detailPanel.classList.contains("open")) closeDetailPanel();
  }});
</script>
</body>
</html>
"""


if __name__ == "__main__":
    systems = indexed_only(load_systems())
    OUTPUT_FILE.write_text(render_page(systems))
    print(f"Wrote {OUTPUT_FILE} with {len(systems)} systems.")
