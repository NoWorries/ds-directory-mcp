# Remediation tasks — indexing & generation audit (2026-09-17)

Instructions for an implementing agent. Work top to bottom; each phase is one
commit (or one commit per task where marked). Every task names the file(s),
the exact change, and how to verify. Do not refactor beyond what a task says.
Verify with the commands given — never claim a task done without running them.

Conventions used below:
- "regen" = `python3 generate_components.py && python3 generate_systems.py && python3 generate_directory.py`
  (offline, seconds). Run it after any change to a generator, then look at the
  named output file.
- Anything needing `QDRANT_URL`/`QDRANT_API_KEY`/`JINA_API_KEY` is marked
  **[creds]** — write the code, verify it compiles and dry-runs, and leave the
  live run to the maintainer.
- `full_name(entry)` (text_utils.py) is "Org — System" and is the key used
  everywhere (pages_index.json, Qdrant payload `design_system_name`).

---

## Phase 0 — already implemented in the working tree; commit it first

These are done and verified. Do NOT redo them. Just review the diff and commit
as one commit ("Fix crawl scope, recursive-path trap, per-page embed failures;
split taxonomy into components/patterns/foundations").

- `ingest.py`: `path_scope()` + `LANDING_PAGE_SEGMENTS`, `scope_paths` registry
  override plumbed through `crawl()`/`ingest()`/`ingest_entry()`,
  `is_recursive_trap()` + `MAX_PATH_SEGMENTS`, `/posts/ /news/ /articles/
  /insights/` in `DEFAULT_EXCLUDE_PATTERNS`, `extract_title()` scoped to
  `soup.head`, `_chunk_and_upsert()` returns failed URLs and `ingest()` writes
  registry/pages_index only for successful pages, `ensure_collection()` creates
  the `url` payload index, `update_registry_stats(freshness_signal=)`,
  `consecutive_crawl_failures`, `--all` excludes `likely_spa`, `--refresh`.
- `component_taxonomy.py`: `COMPONENTS` / `PATTERNS` / `FOUNDATIONS`.
- `generate_components.py`: `ROUTES`, prefix matching, per-(canonical, system)
  dedupe, `render_system_badge()`.
- `page_shell.py`: Patterns/Foundations nav links.
- `check_crawl_health.py`, `.github/workflows/reindex-spa.yml`,
  `.github/workflows/reindex-signals-refresh.yml`, `resources.py`,
  `generate_systems.py`, `generate_directory.py` (side panel / activity dates).

Verify before committing: `python3 -m py_compile ingest.py generate_components.py`
and regen succeeds.

---

## Phase 1 — critical: broken deploy, data loss, security

### 1.0 Shards are killing their own runner (64 of 246 shards in run 35087624888)
Every failed shard in the 2026-09-16 `reindex-full` run died the same way:
`Process completed with exit code 143` (SIGTERM) and the GitHub annotation
**"The hosted runner lost communication with the server. Anything in your
workflow that … starves it for CPU/Memory … can cause this error."** Only
shards that did real crawling died; the 182 that "succeeded" in ~20 s were
filtered-out systems doing nothing. Shard 12 (Health Design System) was silent
for 7 minutes after its last logged fetch before being killed — one fetch/parse
consuming the machine. The `if: always()` artifact upload cannot run when the
VM itself is torn down, so a killed shard's work is lost entirely.
Cause is in `ingest.fetch_page()`: `requests.get(url, timeout=15)` with no
`stream=True`, no `Content-Type` check, no size cap, then
`BeautifulSoup(response.text, "html.parser")` on whatever came back. The
crawler follows links to `.js` bundles (shard 12 log: fetching
`/js/health/health.lazyload.js`), PDFs, zips, videos — a single 50–500 MB body
is read fully into memory and parsed as HTML. `timeout=15` is per socket read,
not total, so a slow large download never times out either.
Fix (all in `fetch_page`, shared by every workflow — this is the "same fix
for all runs"):
- Skip by extension BEFORE fetching (see 2.3 list) — in `extract_links`, not
  just here, so they never enter `to_visit`.
- `requests.get(url, timeout=(10, 20), stream=True, headers=...)`; check
  `response.headers.get("Content-Type", "")` — if it does not contain
  `text/html` or `application/xhtml`, `response.close()` and return
  `(None, f"non-html {ctype}", {})`.
- Read the body with `response.iter_content(65536)` accumulating up to
  `MAX_PAGE_BYTES = 3_000_000`; stop and return `(None, "too large", {})` if
  exceeded. Enforce a total wall-clock budget of 30 s for the read loop.
- Parse with `BeautifulSoup(body, "lxml")` if `lxml` is available (add to
  `requirements-ingest.txt`); it is ~10× faster and far lighter than
  `html.parser` on large documents. Keep `html.parser` as fallback.
- Belt-and-braces: at the top of `ingest.py` main, `resource.setrlimit(
  resource.RLIMIT_AS, (4 * 1024**3, ...))` so a runaway parse raises
  `MemoryError` inside `safe_run` instead of taking the runner down.
- In every `reindex-*.yml`, add `--memory` visibility: a step after ingest
  `run: free -m` is enough to see headroom in logs.
Verify: unit-test `fetch_page` against a mocked response with
`Content-Type: application/pdf` (returns None, no parse) and with a 5 MB
`text/html` body (returns None, "too large"). Then **[creds]** re-run
`python ingest.py --system "Australian Government, Department of Health — Health Design System" --force`
locally and watch `ps`/Activity Monitor memory stay flat.

### 1.1 `/patterns` and `/foundations` are generated but never deployed (site 404s)
Every workflow copies and commits only `components`.
Files and lines: `.github/workflows/build-site.yml:88`, `reindex-full.yml:171,208`,
`reindex-new.yml:159,194`, `reindex-shallow.yml:157,192`, `reindex-spa.yml:166,201`,
`reindex-signals-refresh.yml:129,161`, `submission-approve.yml:84,113`.
- Every `if [ -d components ]; then cp -r components site/components; fi` →
  add identical lines for `patterns` and `foundations`.
- Every `git add ... components systems ...` → add `patterns foundations`.
- `generate_sitemap.py`: add `/patterns`, `/foundations` to `STATIC_PATHS`, and
  emit every generated `components/*.html`, `patterns/*.html`,
  `foundations/*.html` page (iterate `generate_components.ROUTES` + the dirs).
- `generate_llms_txt.py` (~line 101) and `generate_home.py` (~line 149, the
  nav card that says "component or pattern"): mention all three routes.
Verify: `grep -c "patterns" .github/workflows/*.yml` shows every file ≥2;
`python3 generate_sitemap.py && grep -c "/foundations/" sitemap.xml` > 0.

### 1.2 `merge_shards.py` clobbers `pages_index.json` (46 indexed systems have no page list)
`merge_shards.py:70` does `pages_index.update(json.loads(shard_pages_path.read_text()))`
— each shard's file is a full copy of the whole index, so the last shard's
stale copy of system X overwrites the fresh list from the shard that crawled X.
The systems.yaml merge just above (lines 55-66) is per-entry and compares
against the base; the pages_index merge must do the same — but the base
comparison is itself unsafe (see 1.3). Do this instead:
- `ingest.py`: add `SHARD_TOUCHED_FILE = Path(__file__).parent / "shard_touched.json"`.
  In `ingest()` and `ingest_spa()`, after `update_registry_stats(...)`, append
  `design_system_name` to that JSON list (create if missing). Also append in
  `ingest_entry()`'s early-return "no changes detected" branch (it stamps
  `last_checked`).
- Every sharded workflow (`reindex-full/new/shallow/spa/signals-refresh.yml`):
  add `shard_touched.json` to the `actions/upload-artifact` `path:` list, and
  `rm -f shard_touched.json` before `ingest.py` runs so it starts empty.
- `merge_shards.py`: for each shard, read `shard_touched.json` (skip the shard
  if absent); for each touched name take BOTH that shard's systems.yaml entry
  and that shard's `pages_index[name]` (delete the key in base if the shard
  has no entry for it). Remove the `shard_entry != original_by_name[name]`
  comparison and the `pages_index.update(...)` line.
Verify: build two fake shard dirs in a temp folder each with a
`shard_touched.json` naming a different system and differing pages_index
contents; run `python3 merge_shards.py <dir>`; confirm each system took its
own shard's list.

### 1.3 Merge compares against a fresh checkout, reverting mid-run commits
Same file, lines 41-45: `base` is loaded from the merge job's checkout hours
after shards started. `submission-approve`, `reindex-new`, `fetch-screenshots`
all push to master mid-run, so untouched entries look "changed" and get
reverted. 1.2's touched-list approach removes the comparison entirely, which
fixes this. Nothing extra to do beyond 1.2 — just confirm the comparison is gone.
**Evidence the comparison is unreliable**: run 35087624888's merge step logged
`Merged 246 shards into systems.yaml + pages_index.json (689 entries updated)`
from only 182 artifacts on a 241-entry registry. Each shard touches one
system, so ≤ ~60 genuine updates were possible; the rest are false
"changed" detections (likely YAML round-trip differences such as timestamp
vs string, or key-order/None normalisation). While implementing 1.2, add a
debug line per shard listing which names it contributed, so a future count
like this is explainable.

### 1.4 A failed or empty crawl deletes the system's entire index
`ingest.py` ~line 744: `removed_urls = set(previous_pages) - set(pages)`, then
`delete_urls(...)` and `update_registry_stats(pages_indexed=len(persisted_pages))`.
If the start URL 503s / rate-limits / returns an SPA shell, `pages` is empty →
every previous page is deleted from Qdrant and `pages_indexed` becomes 0.
**Confirmed live**: merge commit `db41594` (2026-09-16, reindex-full run
35087624888) set `pages_indexed` 1 → 0 on 40 systems (Adobe Spectrum, Apple
HIG, Datadog Druids, Backbase, Altinn, BT Arc, Catho Quantum, …) whose shards
completed with an empty crawl.
- In `ingest()`, right after `crawl()` returns: if `previous_pages` is
  non-empty and (`start_url_error` is set OR `len(pages) == 0`):
  print a clear line, call `update_registry_stats` with the PREVIOUS
  `pages_indexed` (use `get_registry_entry(name).get("pages_indexed")`),
  `crawl_error=start_url_error or "crawl returned 0 pages"`, and the
  freshness signal; do NOT call `delete_urls`, do NOT rewrite the pages_index
  entry; `return`.
- Also: when `hit_max_pages` is True, set `removed_urls = set()` (a capped BFS
  crawl proves nothing about pages it never reached — see 2.6).
Verify: unit-test style — monkeypatch `crawl` to return empty pages with
`start_url_error="boom"`, `load_previous_pages` to return 3 urls, and assert
`delete_urls` is not called. (`python3 -c` with `unittest.mock` is fine.)

### 1.5 `ingest_spa()` has the ordering bug already fixed in `ingest()`
`ingest.py` ~lines 930-960: `delete_existing(...)` → registry + pages_index
written → `_chunk_and_upsert(...)` return value ignored. A Jina failure leaves
`pages_indexed: 1` with zero vectors.
- Reorder exactly like `ingest()`: call `_chunk_and_upsert` first, capture
  `failed_urls`, exclude them from both `update_registry_stats(pages_indexed=)`
  and `update_pages_index(...)`.
- Sticky render mode: on a successful SPA render (≥1 page persisted) write
  `render_mode: "spa"` via the same `update_registry_stats` call (add the
  kwarg). In `--all`'s filter (~line 1052 and the matching `entries = [...]`
  line) exclude `e.get("render_mode") == "spa"`; in `--spa`'s filter include
  `render_mode == "spa"` as well as `likely_spa`. Otherwise a successful `--spa`
  flips `likely_spa` to False, the next monthly `--all` plain-crawls it, finds 0
  pages, and (pre-1.4) wipes it — with 1.4 it merely fails every month.
- Document `render_mode` in `REGISTRY_HEADER`.
Verify: `python3 -m py_compile ingest.py`; grep that both filters mention
`render_mode`.

### 1.6 Shell injection in `submission-approve.yml`
Lines 37, 52, 86, 132, 136 interpolate `${{ steps.approve.outputs.full_name }}`
(built from the submitter's free-text org/system names in
`approve_submission.py:26-33`) straight into `run:` scripts and the email.
The job holds `contents: write` plus Qdrant/Jina secrets.
- For each `run:` step using it, add `env: FULL_NAME: ${{ steps.approve.outputs.full_name }}`
  and reference `"$FULL_NAME"` in the script (the workflow already does this
  for `ISSUE_BODY` — copy that pattern).
- For the email step (`subject:`/body), interpolation into `with:` inputs is
  not shell, so it's fine to leave — but `approve_submission.py` should also
  reject names containing any of `` `$;|&<>"'\ `` (fail the step with a clear
  message) as belt-and-braces.
Verify: `grep -n 'steps.approve.outputs.full_name' .github/workflows/submission-approve.yml`
shows only `env:` lines and `with:` lines, no `run:` lines.

### 1.7 `embeddings.py` Retry-After handling can crash and defeats backoff
Line 37: `float(response.headers.get("Retry-After", default_wait))` —
`Retry-After` may be an HTTP-date → `ValueError` (uncaught → page fails, and
so does every following page while the limit lasts). When numeric it bypasses
the jitter, so parallel shards re-synchronise.
- Parse int-or-HTTP-date (`email.utils.parsedate_to_datetime`); on parse
  failure use `default_wait`.
- `wait = max(parsed, default_wait_without_jitter) + jitter`.
- Also retry on 5xx and `requests.Timeout`/`ConnectionError` with the same
  backoff (currently `raise_for_status` fails hard on a 502).
- Guard `response.json()["data"]` (KeyError → treat as retryable).
Verify: `python3 -c` calling the retry path with a fake response whose
`Retry-After` is `"Wed, 21 Oct 2026 07:28:00 GMT"` does not raise.

### 1.8 `reindex-full.yml` spins up ~72 empty shards
`reindex-full.yml:47-49` sets `total = len(yaml.safe_load(f))` (246) but
`ingest.py --all` filters out `archived` + `likely_spa` (+ `render_mode: spa`
after 1.5). Shards past the eligible count install deps and do nothing.
- In the `prepare` job compute the eligible list with the SAME filter
  ingest.py uses (import nothing — just replicate the three conditions), and
  set `shard_count = min(len(eligible), 256)`.
- Do the same in `reindex-new.yml` (eligible = no `pages_indexed`, not
  archived), `reindex-shallow.yml`, `reindex-signals-refresh.yml`
  (`pages_indexed` set, not archived), `reindex-spa.yml` (`likely_spa` or
  `render_mode: spa`, not archived).
Verify: run each prepare snippet locally with `python3 -c` against
systems.yaml and confirm counts match `ingest.py`'s own "Shard i/N: x of Y
systems" line for the corresponding mode.

### 1.9 `--shallow` runs record `hit_max_pages: true`, polluting the health report
48 of 53 "capped" systems have 6-8 pages (`SHALLOW_MAX_PAGES = 8`), and
`check_crawl_health.py:103-116` tells the maintainer to raise `max_pages`.
- In `ingest()` add a `shallow: bool = False` kwarg; `ingest_entry()` passes
  `shallow=max_pages_override is not None`; when `shallow`, pass
  `hit_max_pages=False` to `update_registry_stats` (a shallow cap is not
  evidence of anything).
- One-off data fix: `python3 -c` over systems.yaml setting `hit_max_pages: False`
  where `pages_indexed <= 8 and hit_max_pages` (use `save_registry` from
  ingest.py to keep the header).
Verify: `python3 check_crawl_health.py` capped section shrinks to systems with
`pages_indexed >= 30`.

---

## Phase 2 — crawl quality (ingest.py unless stated)

### 2.1 URL normalisation (268 near-duplicate pairs, 189 query-string URLs indexed)
Add `normalize_url(url: str) -> str`: lowercase scheme+host, `http`→`https`,
strip default port, strip fragment, strip trailing `index.html`/`index.htm`,
canonical trailing slash (choose: strip it except for the bare root `/`),
drop ALL query params except an allowlist (`page` is NOT allowed — Skyline
indexed 101× `/feedback/?page=N`; start with an empty allowlist and add
`version`/`v` only if a real system needs it).
- Apply in `extract_links()` to `absolute` before scope/trap checks, to
  `start_urls` at the top of `crawl()`, and to `fetch_page()`'s final
  `response.url` (see 2.2). `visited`/`to_visit` then dedupe naturally.
- Keep a `to_visit_set` alongside the list so `link in to_visit` is O(1).
Verify: `python3 -c` asserting `normalize_url("HTTP://Www.X.com/a/index.html?utm_source=1#f") == "https://www.x.com/a"`,
and that `/a/` and `/a` normalise identically.

### 2.2 Follow redirects into scope, key by final URL, honour canonical
`fetch_page()` (~line 246) follows redirects silently; the page is stored
under the requested URL even if it landed on gs.com's corporate site.
- Return `response.url` (normalised) from `fetch_page`; in `crawl()` if the
  final URL is outside every `root_scopes` prefix, treat as fetched-but-skip
  (print "redirected out of scope"). If in scope, store the page under the
  final URL.
- If `<link rel="canonical" href=...>` exists and is in scope, use it as the
  page key instead.
- Skip embedding when `<meta name="robots" content="...noindex...">` is present.
Verify: synthetic BeautifulSoup test for the canonical + noindex branches;
`py_compile`.

### 2.3 Reject non-HTML, oversized, and binary URLs
`fetch_page` downloads and parses everything (`/llms.txt`, `tablesort.js`,
`.aspx` are in the index as pages; `llms.txt` appears as a "page" for 6
systems).
- In `extract_links()` skip hrefs ending in
  `.pdf .zip .png .jpg .jpeg .gif .svg .webp .mp4 .webm .woff .woff2 .ttf .css .js .json .xml .txt .ico`
  (still record them in `all_links_seen` for resource discovery — only skip
  from `to_visit`).
- In `fetch_page` use `stream=True`, read `Content-Type`; if it doesn't
  contain `text/html` return `(None, "non-html", {})`; cap body at 3 MB.
- Keep the `llms.txt` fallback in `ingest()` (that's deliberate) but ALSO
  always include `llms-full.txt`/`llms.txt` as an extra page when discovered,
  not only when the crawl found 0 pages.
Verify: synthetic test; `py_compile`.

### 2.4 Login / consent pages indexed as content
MYOB Feelix: 18/18 pages titled "Sign in to GitHub · GitHub"
(`/signup?return_to=…&nonce=…`, 16 unique nonces). FT Origami: "Sign in -
Google Accounts".
- Add `AUTH_TITLE_PATTERNS = [r"^sign in", r"^log ?in", r"^login", r"^sign up",
  r"^signup", r"just a moment", r"access denied", r"^404", r"page not found",
  r"^redirecting", r"attention required", r"^untitled$"]` (case-insensitive) and
  skip the page (don't add to `pages`) when the extracted title matches.
- 2.1's query stripping removes the nonce duplicates.
- `check_crawl_health.py`: new section "systems where >50% of pages have an
  auth/error title" using `pages_index.json`.
Verify: `python3 check_crawl_health.py` lists MYOB Feelix under the new
section (until re-crawled).

### 2.5 Title extraction and cleaning
Evidence: 143 titles are bare URLs (Carbon 119); Loom Lens titles are
`skeleton<!` (`title_tag.string` stops at a `<!-- -->` inside `<title>`);
72 Vanilla titles contain `"\n        documentation"`; `clean_title`
(text_utils.py:48) keeps the FIRST separator segment, so "Components — Button |
PIE" becomes "Components" (PIE 49/78 pages, BBC GEL 238/296 → "BBC GEL",
GOLD 300/300 → "Home", Polaris 9/9 → "Polaris references") and those pages
never match any taxonomy alias.
- `extract_title()`: use `title_tag.get_text()`; `re.sub(r"\s+", " ", …).strip()`;
  strip a trailing `<!`; if empty, fall back to `<meta property="og:title">`,
  then first `<h1>` text, then a humanised last URL path segment
  (`"progress-bar"` → `"Progress bar"`). Never return the raw URL.
- `text_utils.clean_title(title, site_titles=None)`: split on ` | `, ` — `,
  ` – `, ` - `, ` · `, ` :: `; drop segments that equal (case-insensitive) the
  org name, system name, or a generic word (`home`, `components`, `docs`,
  `documentation`, `overview`); additionally, when `site_titles` (all titles
  for that system) is given, drop any segment appearing in >30% of them (the
  site-name suffix/prefix). Return the LONGEST remaining segment, or the
  original if nothing remains. Update callers in `generate_components.py`
  (`build_component_index`) and `generate_systems.py` (`render_page_list`)
  to pass the per-system title list.
- `render_page_list` (generate_systems.py): if the cleaned title repeats
  within a system, or starts with `http`, show the humanised URL path instead.
Verify: regen; `grep -c "https://" systems/ibm-carbon-design-system.html`
drops to ~0 link texts; `components/button.html` gains PIE / BBC GEL; Loom
Lens shows "Skeleton" not `skeleton&lt;!`.

### 2.6 Capped crawls never progress; BFS churn deletes live pages
`crawl()` always starts from the same URL in the same link order, so the same
300 pages are fetched monthly and the queued remainder never is.
- Seed `to_visit` from the sitemap: `resources.probe_sitemap()` (resources.py
  ~114) already fetches it — extend it to return the in-scope `<loc>` URLs
  (normalised) and append them after `start_urls`.
- Order `to_visit` so URLs NOT in `previous_pages` are visited before ones that
  are (stable sort, keep BFS order within each group).
- With 1.4's `hit_max_pages → removed_urls = set()` rule, capped crawls stop
  deleting unvisited pages.
Verify: `py_compile`; synthetic test that a previously-seen URL sorts after an
unseen one.

### 2.7 `likely_spa` only fires at exactly zero pages; `max_pages` counts fetches
Atlassian: `max_pages: 150`, `pages_indexed: 6`, `hit_max_pages: true` —
144 fetches under `MIN_CONTENT_LENGTH`.
- Track `fetched` and `indexed` counts in `crawl()`; return both. In
  `ingest()` set `likely_spa = True` when `fetched >= 20 and indexed / fetched < 0.1`
  (in addition to the existing zero-pages rule), and record
  `thin_page_ratio` on the entry for the health report.
Verify: `py_compile`; `check_crawl_health.py` still runs.

### 2.8 `extract_text` boilerplate
`STRIP_TAGS` strips every `header` (Docusaurus/MDX put `<header><h1>` inside
`<article>`, losing the title/intro); it doesn't strip `svg`, `aside`,
`button`, `form`, `iframe`, `[aria-hidden=true]`, `[role=navigation]`;
`BOILERPLATE_LINES` exact-match catches almost nothing.
- Prefer `main` / `article` / `[role=main]` content when present; strip
  `header` only when it is a direct child of `body`; add the tags above.
- Cross-page boilerplate: in `ingest()` after `crawl()`, compute line
  frequency across `pages`; drop any line that appears in >50% of a system's
  pages when that system has ≥5 pages (sidebars, footers, cookie banners).
  Do this BEFORE `content_signals`/embedding.
- `get_text(separator="\n")` splits sentences at inline `<code>`/`<a>`; use
  `" "` for inline elements (unwrap inline tags first, then `get_text("\n")`).
Verify: synthetic test with a repeated footer line across 6 fake pages.

### 2.10 Resource discovery lists one Figma file 60 times, one Storybook 90 times
Live evidence (AgDS shard log): `figma` → 60 URLs that are all
`figma.com/file/MivOblJvHi3nJ4bQMDfnf3/AgDS---gallery?node-id=…`; `storybook`
→ ~90 URLs that are all `…/storybook/index.html?path=/story/…`; `github` →
includes `jquense/yup` and `changesets/changesets` (dependencies, not the
system's repos); `icons` → GitHub `/edit/main/...` links.
In `resources.py` `classify_links()` (or a new `canonicalize_resource(kind, url)`
applied before dedupe):
- figma: keep scheme+host+`/file/<key>/<name>` or `/design/<key>/<name>`;
  drop query and fragment. One entry per file key.
- storybook: keep scheme+host+path up to and including `index.html` (or the
  directory if no `index.html`); drop `?path=`. One entry per storybook root.
- github: reduce to `https://github.com/<owner>/<repo>`; drop `/blob/…`,
  `/tree/…`, `/edit/…`, `/issues/…`. Then keep only repos whose `<owner>`
  matches (case-insensitive, hyphens/spaces removed) the org, the system
  name, or the start URL's second-level domain (`agriculturegovau` ↔
  `agriculture.gov.au`); if none match, keep the single most-linked repo and
  drop the rest. `enrich_resources()` only enriches index 0, so ordering
  matters — put the best match first.
- npm: reduce to `https://www.npmjs.com/package/<name>` (scoped names keep
  the `@scope/`).
- Apply the same canonicaliser in `generate_systems.py` when rendering pills
  so already-indexed entries render deduped before the next crawl.
Verify: `python3 -c` feeding the AgDS URL list above through
`classify_links` returns 1 figma, 1 storybook, ≤2 github (agriculturegovau/*).

### 2.11 Crawl budget burned on preview/query URLs (AgDS: 300 fetches → 134 pages)
Same log: ~160 of 300 fetches were `/responsive-preview?src=…&title=…` and
`/storybook/index.html?path=…`, every one skipped as "too little content",
yet each cost a fetch + `CRAWL_DELAY_SECONDS` and a slot in `max_pages`; the
crawl then hit the cap with 474 real pages still queued. 2.1's query
stripping collapses all of these to one URL each. Additionally add
`r"/responsive-preview"`, `r"/playroom"`, `r"/storybook/"`, `r"/iframe\.html"`,
`r"/sandbox"` to `DEFAULT_EXCLUDE_PATTERNS` (Storybook/Playroom are still
discovered as *resources* via `all_links_seen`; they just aren't crawled as
pages). Verify with `is_excluded` on the two URL shapes above.

### 2.9 robots.txt (lower priority)
Per-netloc cached `urllib.robotparser.RobotFileParser`; skip disallowed URLs;
honour `Crawl-delay` over `CRAWL_DELAY_SECONDS` when larger.

---

## Phase 3 — taxonomy & generation

### 3.1 Whole-word alias matching + cross-taxonomy precedence
`generate_components.build_component_index` uses `head.startswith(alias)`:
`"tab"` matches "Table(s)" (Skyline, Designers Italia under both Tabs and
Table); `"icon"` matches "Iconography" (Morningstar, NZ MoE in both
`components/icon.html` and `foundations/iconography.html`); `"list"` matches
"List Group"/"Listbox"; `"notification"` (Alert) matches the Notifications
pattern; Accessibility foundation matches "Accessibility Days 2023"; Grid
foundation matches "Layout Grid" component.
- Match with `re.match(rf"{re.escape(alias)}(s|es)?\b", head)` (alias must be
  a whole word at the start).
- Build one combined alias table across all three taxonomies; for each title
  pick the LONGEST matching alias only (so "iconography" beats "icon",
  "layout grid" beats "grid", "notification pattern" beats "notification"),
  and assign the title to that alias's taxonomy only.
- Add explicit negative patterns per canonical where needed:
  Accessibility: skip titles containing `days|20\d\d|event|conference|technology`;
  Onboarding: skip `for businesses|resources`; Color: skip `contrast|meaning`
  when the system is BBC GEL — better: skip when the page URL is outside the
  system's start_url scope (compute with `ingest.path_scope`), which removes
  every off-scope false positive at once.
Verify: regen; `components/tabs.html` no longer lists "Tables";
`components/icon.html` no longer lists Iconography pages;
`foundations/accessibility.html` has no "Accessibility Days".

### 3.2 Taxonomy coverage
Zero-system routes are already dropped; but Drawer, Navigation Bar, Tree have
0; Toast, Combobox, Carousel, Slider have 1; Patterns has 5 pages with 1
system each; Elevation's only hit is the Loom `shadows<!` junk.
- Add COMPONENTS: `Form` (`form`, `forms`, `form field`), `Header`,
  `Footer`, `Rating`, `Textarea` (`textarea`, `text area`), `File Upload`
  (`file upload`, `file uploader`, `upload`), `Search Field` (`search`,
  `search input`, `search field` — move "search" OUT of PATTERNS aliases),
  `Number Input`, `Tile`, `Content Switcher` (`content switcher`,
  `segmented control`), `Toggletip`, `Structured List`.
- Add FOUNDATIONS: `Layout` (`layout`), `Images` (`images`, `imagery`,
  `photography`), `Data Visualization` (`data visualization`, `data
  visualisation`, `charts`).
- Hide a taxonomy page when it has <2 systems ONLY on the index page listing
  (still generate the page so links don't 404) — add a "1 system" muted badge
  instead if simpler. Maintainer's call; default to showing all.
Verify: regen; count of pages per route printed by `generate_components.py`
increases; `components/form.html` exists with ≥3 systems.

### 3.3 Stale outputs in `systems/`
`brightcore-ui`, `datadog-druids`, `gusto-workbench` `.html`+`.json` no longer
correspond to indexed systems but remain and deploy.
- `generate_systems.main()`: before writing, delete every `systems/*.html` and
  `systems/*.json` whose slug is not in the set about to be written. Same for
  `generate_components.main()` per route dir.
Verify: regen; `ls systems | wc -l` equals 2 × indexed count.

### 3.4 Registry identity & naming (data fixes in `systems.yaml`)
Exact duplicate `full_name` (both entries share one pages_index key and one
Qdrant `design_system_name` — one silently wins): GOV.UK Design System ×2,
MongoDB Design System ×2, Priceline One ×2, Hewlett Packard Grommet ×2,
Skyscanner Backpack ×2. Near-duplicates crawling the same site: Intuit "Design
system" vs "QuickBooks Design System" (`design.intuit.com/quickbooks` vs
`…/quickbooks/`, identical 33-page lists), Material 2 vs Material Design,
Mailchimp Pattern Library vs Patterns, Oracle Alta vs Alta UI, Scania Tegel vs
Scania DDS, Seek Braid vs Seek Style Guide.
- For each exact-duplicate pair: keep the entry whose `start_urls[0]`
  currently resolves (check with `curl -sI`); if both resolve to different
  live docs sites, rename one's `design_system` to disambiguate (e.g. "Grommet
  (v1 docs)"); if one is dead, set `archived: true` with the companion fields.
  Delete the loser or archive it — never leave two live entries with the same
  `full_name`.
- Intuit: delete "Intuit — Design system"; keep "QuickBooks Design System".
- Add an assertion in `ingest.load_registry()` and `generate_directory.load_systems()`:
  `assert len({full_name(e) for e in entries}) == len(entries)` with a message
  listing the duplicates. `check_crawl_health.py` reports them too.
- Org/name swapped: "Helios Design System — Hashicorp" → org `HashiCorp`,
  system `Helios`; "Volvo Cars Web Design System — Volvo" → org `Volvo Cars`,
  system `Web Design System`.
- Generic `design_system: Design system` (Intuit, Wise, Backbase,
  Arbetsförmedlingen, VA, Neurodiversity, NZ MoE): rename to `<Org> Design
  System` so `<title>`/`<h1>` aren't "Design system".
- Odd orgs: "Arts — Artsy Palette" → org `Artsy`, system `Palette`;
  "Semantic — Semantic UI" → org blank; "Chakra team — Chakra UI" → org blank;
  "Estonia — Brand Estonia" → org `Republic of Estonia`;
  "KoliBri — KoliBri - Public UI" → system `KoliBri`.
- Scope pins (`scope_paths`) for systems whose start URL is a landing page on
  a domain that also hosts unrelated content: `Goldman Sachs` — check whether
  `design.gs.com` still serves a design system at all (38/39 indexed pages are
  gs.com corporate); if not, archive it. `Louder Than Ten — Manual`:
  `scope_paths: ["/manual/"]`. `BBC GEL`: `scope_paths: ["/gel/"]`. `GOV.UK`:
  `scope_paths: ["/design-system/"]`. `Intuit QuickBooks`:
  `scope_paths: ["/quickbooks/"]`. (These match what `path_scope` now infers;
  pinning them makes the intent explicit and survives future heuristic
  changes.)
Verify: `python3 -c "import ingest; ingest.load_registry()"` passes the
uniqueness assertion; regen; `systems/hashicorp-helios.html` exists.

---

## Phase 4 — embedding, resumability, workflows

### 4.1 Deterministic point IDs; upsert before delete
`_chunk_and_upsert` uses `uuid.uuid4()` per chunk, so `delete_urls` must run
BEFORE embedding (ingest.py ~line 744) — a page whose embed fails has no
vectors until the next run.
- `id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{design_system_name}|{url}|{i}"))`;
  add `chunk_index: i`, `title`, `indexed_at` to the payload (pass
  `page_titles` into `_chunk_and_upsert`).
- Upsert first (overwrites in place). Then delete: (a) stale tail chunks per
  upserted URL — points where `url == u and chunk_index >= new_count` (needs a
  payload index on `chunk_index`, INTEGER, in `ensure_collection`), and (b)
  `removed_urls` — both AFTER the upserts, and only for URLs NOT in
  `failed_urls`.
- `server.py`: use `title` in results; group hits by `url`; cache the parsed
  `systems.yaml` in `get_resources` (module-level `lru_cache` keyed on mtime).
Verify: `py_compile`; **[creds]** `python3 ingest.py --system "<small system>" --force`
twice and confirm the point count in Qdrant is unchanged between runs.

### 4.2 Batch embeddings across pages; rate limit
One Jina request per page (2,586+ per full run × parallel shards) causes the
429/324 s backoffs seen in CI.
- In `_chunk_and_upsert`, accumulate chunks across pages up to ~100 chunks or
  ~60k chars per request; embed the batch; then upsert per page. A failed batch
  marks all its pages failed.
- Module-level minimum interval between Jina requests (e.g. 0.5 s) in
  `embeddings.py`.
- `chunking.py:15` splits only on `" "` though text is `\n`-separated: split
  on `\n\n`, then `\n`, then space; raise chunk size to ~1,400 chars with
  ~200 overlap; prefix each chunk with `"{title}\n\n"`. Dedupe identical chunk
  texts within a system.
Verify: `python3 -c` on a fake 10-page dict asserts ≤2 embed calls with a
mocked `embed_texts`.

### 4.3 Content-hash freshness + pipeline version
33% of pages have neither ETag nor Last-Modified (22 systems have none), and
changing `extract_text`/chunking never re-embeds "unchanged" pages.
- Store `content_hash: sha256(text)[:16]` in pages_index entries; `page_unchanged`
  returns True only if headers match OR hash matches.
- `PIPELINE_VERSION = 2` constant in ingest.py stored per pages_index entry;
  mismatch ⇒ not unchanged. Add `--reembed` flag = ignore `unchanged_urls`.
Verify: `py_compile`; synthetic `page_unchanged` test.

### 4.4 Workflow hygiene
- Add `concurrency: { group: index-pipeline, cancel-in-progress: false }` to
  every workflow that commits (`reindex-*.yml`, `fetch-screenshots.yml`,
  `submission-approve.yml`, `build-site.yml`).
- `reindex-full.yml:180-184` push-retry loop: on final failure run
  `git rebase --abort` and upload `systems.yaml pages_index.json` as a
  30-day artifact so the run isn't lost.
- `reindex-spa.yml:85`: cache `~/.cache/ms-playwright` with `actions/cache`
  keyed on the playwright version from `requirements-spa.txt`.
- Extract the 25-line duplicated "build site" block from 7 workflows into
  `scripts/build_site.sh` and call it. Remove the no-op `force` input from
  `reindex-full.yml` (`--all` always forces).
- `merge-and-deploy` `if: always()` crashes when `prepare` failed (empty
  `shard_count`) — guard with `if: needs.prepare.result == 'success'`.
- Pin `qdrant-client` in `requirements-ingest.txt` to the server's major
  version (CI log shows client 1.16 vs server 1.19 warning).
Verify: `actionlint` if available, else YAML-parse each file with `python3 -c "import yaml,glob;[yaml.safe_load(open(f)) for f in glob.glob('.github/workflows/*.yml')]"`.

### 4.5 Per-run summary
`safe_run` (ingest.py ~944) prints and swallows; nothing reaches the health
report. Write `run_summary.json` per shard (per system: fetched / indexed /
thin / unchanged / failed-embed / 429 count / seconds / exception text);
`safe_run` also stamps `crawl_error` on the entry via `update_registry_fields`.
Upload with the artifact; `merge_shards.py` aggregates and appends a table to
`$GITHUB_STEP_SUMMARY`.

---

## Phase 5 — live remediation **[creds]** (maintainer runs; agent prepares the script)

Write `prune_index.py`:
1. Load `systems.yaml` + `pages_index.json`.
2. For every system, compute its scopes (`scope_paths` or `ingest.path_scope`)
   and drop any page whose URL is out of scope, matches
   `DEFAULT_EXCLUDE_PATTERNS` + entry `exclude_patterns`, matches
   `is_recursive_trap`, or has an auth/error title (2.4 list).
3. Print a per-system table (kept / dropped) and, with `--apply`, call
   `ingest.delete_urls(client, name, dropped)` and rewrite `pages_index.json`
   + `pages_indexed`. Without `--apply` it is a dry run.
Currently affected (from the audit): Microsoft — Fluent (284/285 pages), GOLD
Design System (299/300), Goldman Sachs (38/39), Louder Than Ten (29/30), Intuit
QuickBooks (31/33), BBC GEL (~69/296), GOV.UK (2/3), Brand Estonia (5/41), MYOB
Feelix (18/18 auth pages).

Then the maintainer re-crawls the ones that should have real content so titles
and scope are rebuilt with the fixed code:
```
python ingest.py --system "Microsoft — Fluent" --force
python ingest.py --system "Australian Open Source Community — GOLD Design System" --force
python ingest.py --system "IBM — Carbon Design System" --force     # 135 garbage titles
python ingest.py --system "Loom — Lens" --force                     # "<!" titles
python ingest.py --system "BBC — Global Experience Language (GEL)" --force
python ingest.py --system "Louder Than Ten — Manual" --force
```
and regenerates + commits the site.

---

## Not a bug (checked, no action)
- `DEFAULT_EXCLUDE_PATTERNS` `r"/terms"` does NOT match `/foundations/terminology`.
- Favicons (Google s2) and screenshots for all 119 indexed slugs render fine.
- No empty `href=""` or unescaped markup found in generated HTML.
