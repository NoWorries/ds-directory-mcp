# Working in this repo

Project-specific operating principles that aren't obvious from reading any
single file. Read this before making changes to `ingest.py` or anything
touching `systems.yaml` / `pages_index.json` / the Qdrant collection.

## Ingest is additive by default — never lose indexed content between runs

A crawl that only reaches part of a site (hit a page cap, got killed by an
external actor before finishing, was deliberately run shallow to fit a time
budget) must never cause the system to *look* smaller than it did before
that run. Real sites get crawled in installments — a slow/large/flaky site
may take several separate passes, each reaching a different subset of
pages, before its full content is captured. Each pass should only ever grow
that system's indexed coverage, never reset it.

**Why this matters concretely:** re-crawling is expensive (time, and this
project has repeatedly run into an external killer — likely endpoint
security on the dev machine — that SIGKILLs long-running crawls
unpredictably). The whole point of a capped/shallow retry strategy is to
trade "everything in one shot" for "something now, more later" — that
tradeoff only pays off if "more later" actually accumulates instead of each
run stomping the last one's results.

**How this is enforced in `ingest.py` — do not regress these:**

- `crawl()`'s `removed_urls` is deliberately computed as `set()` whenever
  `hit_max_pages` is `True` — a capped crawl only proves a page wasn't
  *visited* this run, never that it's gone. Only an uncapped (complete)
  crawl that genuinely didn't find a previously-indexed page is allowed to
  treat it as removed.
- `ingest()`'s `carried_over_pages` folds forward any `previous_pages` entry
  that (a) wasn't re-fetched this run and (b) isn't in `removed_urls`, so
  `pages_indexed` and `pages_index.json`'s listing reflect the real
  accumulated total — not just whatever this one run happened to fetch. Its
  Qdrant vectors are already left untouched by the same `removed_urls`
  logic; this is what keeps the *metadata* honest to match.
- `crawl()`'s seed sort (`sorted(sitemap_seeds + llms_seeds, key=lambda u:
  u in previous_pages)`) deliberately prioritizes reaching pages NOT already
  indexed over re-treading ones from last time — this is what makes
  successive capped runs of the same system actually reach *new* content
  each time instead of re-fetching the same first N pages forever.

If you're touching any of this logic, the question to ask is: "does a
system that gets crawled in three separate 40-page passes end up with
~120 pages indexed, or does it end up with whatever the *last* pass alone
found?" It must be the former.

## Removal is only ever deliberate, never an accident of a partial run

The only legitimate ways a page or system should actually disappear from
the index:

1. **A complete (non-capped) crawl no longer finds it** — real evidence the
   page was removed from the live site (`removed_urls` when `hit_max_pages`
   is `False`).
2. **An explicit, hand-set or computed exclusion flag** — `archived: true`
   (a human decided this system is gone/superseded) or
   `likely_unmaintained: true` (`compute_likely_unmaintained()`, based on
   real GitHub/npm activity signals, not a crawl failure).
3. **An explicit user request** — "remove this system", "exclude pages
   matching X" (`exclude_patterns`), etc. — always something requested or
   otherwise clearly indicated, never a side effect of a smaller page
   budget, a timeout, or a crawl that got killed partway through.

A failed or partial crawl attempt should leave a system's existing data
exactly as it was (see `ingest()`'s early-exit branch for a crawl that came
back completely empty on an already-indexed system) — not clear it, not
shrink it, not mark it unmaintained. If you're about to write code that
deletes, overwrites, or resets indexed content, check which of the three
cases above actually applies before doing it.
