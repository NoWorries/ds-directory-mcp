"""
Recombines the systems.yaml/pages_index.json produced by each parallel shard
of reindex-full.yml's (or reindex-shallow.yml's) matrix into one merged file.

Merges by system identity (full_name), not list position. Every shard's
ingest.py run reloads and rewrites the COMPLETE systems.yaml on every single
entry it touches (see update_registry_fields/save_registry in ingest.py) — so
each shard's local systems.yaml already contains all ~250 entries, with only
the ones that shard actually processed showing updated fields. Merging is
"for each entry, did any shard's copy of it change from what it originally
was — if so, take that shard's version."

Position-based merging (i % total == shard_index) only works when sharding
splits the ORIGINAL list by position — true for `--all --shard`, but NOT for
`--new --shard`, which shards a pre-filtered (unindexed-only) subset, so a
shard's real updates land at scattered, unrelated positions in its own copy
of the full list. Using position-based merging there silently discarded
almost every shard's actual results while still "succeeding" and reporting
no changes — this rewrite fixes that regardless of which sharding scheme
produced the shard files.

Usage: python merge_shards.py <total_shards>
Expects ./shards/shard-<N>/{systems.yaml,pages_index.json} for each N.
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
    original_by_name = {name: base[i] for name, i in name_to_index.items()}

    pages_index = {}
    pages_index_path = Path("pages_index.json")
    if pages_index_path.exists():
        pages_index = json.loads(pages_index_path.read_text())

    updated_count = 0
    for shard_index in range(total):
        shard_dir = SHARDS_DIR / f"shard-{shard_index}"

        shard_yaml_path = shard_dir / "systems.yaml"
        if shard_yaml_path.exists():
            with open(shard_yaml_path) as f:
                shard_entries = yaml.safe_load(f) or []
            for shard_entry in shard_entries:
                name = full_name(shard_entry)
                if name not in name_to_index:
                    continue  # entry this shard doesn't recognize (shouldn't happen) — skip rather than append
                if shard_entry != original_by_name[name]:
                    base[name_to_index[name]] = shard_entry
                    updated_count += 1

        shard_pages_path = shard_dir / "pages_index.json"
        if shard_pages_path.exists():
            pages_index.update(json.loads(shard_pages_path.read_text()))

    save_registry(base)
    pages_index_path.write_text(json.dumps(pages_index, indent=2, ensure_ascii=False))
    print(f"Merged {total} shards into systems.yaml + pages_index.json ({updated_count} entries updated).")


if __name__ == "__main__":
    main()
