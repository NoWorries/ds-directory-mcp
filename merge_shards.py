"""
Recombines the systems.yaml/pages_index.json produced by each parallel shard
of reindex-full.yml (or reindex-new/reindex-shallow/reindex-refresh/reindex-
spa.yml) into one merged file.

Merges by shard_touched.json — the list of design_system_names each shard's
ingest.py run actually processed this run (written by ingest.mark_shard_touched
as it goes; see ingest.py). For each name a shard touched, that shard's copy
of the systems.yaml entry AND its pages_index.json entry are both taken
verbatim; anything a shard didn't touch is left as this job's own checkout
already has it.

This replaces an earlier "diff each shard's full copy against a freshly
checked-out base" approach that was unreliable two ways at once:
- The base checkout happens well after shards started, so any commit that
  landed in between (submission-approve, reindex-new, fetch-screenshots all
  push to this same branch) made every untouched entry in a shard's copy
  look "changed" and get wrongly reverted to the shard's (now stale) view.
  One real run logged "689 entries updated" from 182 shard artifacts on a
  241-entry registry — at most ~60 genuine updates were possible.
- pages_index.json was merged with a flat dict.update(), and every shard's
  copy of that file is a full copy of the WHOLE index (ingest.update_pages_
  index() rewrites the entire file on every call) — so the LAST shard
  processed in the loop always won, silently overwriting other shards'
  fresh crawls of systems it never touched with its own stale copy of them.
  46 indexed systems ended up with no page list in pages_index.json at all.

Usage: python merge_shards.py <total_shards>
Expects ./shards/shard-<N>/{systems.yaml,pages_index.json,shard_touched.json}
for each N (shard_touched.json absent means that shard touched nothing —
e.g. it was filtered out by 1.8's eligible-count fix, or died before writing
anything).
"""

import json
import sys
from pathlib import Path

import yaml

from ingest import save_registry
from text_utils import full_name

SHARDS_DIR = Path(__file__).parent / "shards"


def main() -> None:
    total = int(sys.argv[1])

    with open("systems.yaml") as f:
        base = yaml.safe_load(f) or []
    name_to_index = {full_name(e): i for i, e in enumerate(base)}

    pages_index = {}
    pages_index_path = Path("pages_index.json")
    if pages_index_path.exists():
        pages_index = json.loads(pages_index_path.read_text())

    updated_count = 0
    touched_by_shard: dict[int, list[str]] = {}
    for shard_index in range(total):
        shard_dir = SHARDS_DIR / f"shard-{shard_index}"
        touched_path = shard_dir / "shard_touched.json"
        if not touched_path.exists():
            continue  # this shard processed nothing (filtered out, or died before writing anything)
        touched = json.loads(touched_path.read_text())
        touched_by_shard[shard_index] = touched
        if not touched:
            continue

        shard_yaml_path = shard_dir / "systems.yaml"
        shard_entries_by_name = {}
        if shard_yaml_path.exists():
            with open(shard_yaml_path) as f:
                shard_entries_by_name = {full_name(e): e for e in (yaml.safe_load(f) or [])}

        shard_pages = {}
        shard_pages_path = shard_dir / "pages_index.json"
        if shard_pages_path.exists():
            shard_pages = json.loads(shard_pages_path.read_text())

        for name in touched:
            if name in shard_entries_by_name and name in name_to_index:
                base[name_to_index[name]] = shard_entries_by_name[name]
                updated_count += 1
            # A touched system with no pages_index entry (e.g. it failed to
            # fetch its start URL and was never actually crawled) has
            # nothing to take here — its systems.yaml entry above already
            # carries the crawl_error/consecutive_crawl_failures update.
            if name in shard_pages:
                pages_index[name] = shard_pages[name]
            elif name in pages_index and shard_yaml_path.exists():
                # The shard touched this system but its crawl produced no
                # persisted pages this run (see ingest()'s empty-crawl
                # guard) — that guard already leaves pages_index.json
                # untouched on the shard's own disk, so there's nothing to
                # do here either; the existing entry (from base) stands.
                pass

    save_registry(base)
    pages_index_path.write_text(json.dumps(pages_index, indent=2, ensure_ascii=False))
    total_touched = sum(len(v) for v in touched_by_shard.values())
    print(
        f"Merged {total} shards into systems.yaml + pages_index.json "
        f"({updated_count} entries updated, {total_touched} systems touched across "
        f"{len(touched_by_shard)} shards that reported any)."
    )


if __name__ == "__main__":
    main()
