"""
Recombines the systems.yaml/pages_index.json produced by each parallel shard
of reindex-full.yml's matrix into one merged file. Each shard only owns
entries at index i where i % total == shard_index, so merging is a simple
overlay — no name matching needed, since sharding never reorders the list.

Usage: python merge_shards.py <total_shards>
Expects ./shards/shard-<N>/{systems.yaml,pages_index.json} for each N.
"""

import json
import sys
from pathlib import Path

import yaml

from ingest import save_registry

SHARDS_DIR = Path(__file__).parent / "shards"


def main() -> None:
    total = int(sys.argv[1])

    with open("systems.yaml") as f:
        base = yaml.safe_load(f) or []

    pages_index = {}
    pages_index_path = Path("pages_index.json")
    if pages_index_path.exists():
        pages_index = json.loads(pages_index_path.read_text())

    for shard_index in range(total):
        shard_dir = SHARDS_DIR / f"shard-{shard_index}"

        shard_yaml_path = shard_dir / "systems.yaml"
        if shard_yaml_path.exists():
            with open(shard_yaml_path) as f:
                shard_entries = yaml.safe_load(f) or []
            for i in range(len(base)):
                if i % total == shard_index and i < len(shard_entries):
                    base[i] = shard_entries[i]

        shard_pages_path = shard_dir / "pages_index.json"
        if shard_pages_path.exists():
            pages_index.update(json.loads(shard_pages_path.read_text()))

    save_registry(base)
    pages_index_path.write_text(json.dumps(pages_index, indent=2, ensure_ascii=False))
    print(f"Merged {total} shards into systems.yaml + pages_index.json.")


if __name__ == "__main__":
    main()
