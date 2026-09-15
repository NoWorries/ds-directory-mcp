import os

from dotenv import load_dotenv

load_dotenv()

# .get(), not os.environ[...] — this module also supplies CHUNK_SIZE/
# CHUNK_OVERLAP/CRAWL_DELAY_SECONDS to code paths that never touch Qdrant or
# Jina at all (merge_shards.py, dry_run_submission.py's crawl-only dry run,
# approve_submission.py's registry read/write) via ingest.py's/chunking.py's
# module-level imports. A hard-required os.environ[...] here meant merely
# *importing* those modules crashed with a KeyError in any workflow step that
# (correctly, deliberately) didn't set these secrets — e.g. the merge job and
# the sandboxed dry-run check, neither of which should need live Qdrant/Jina
# credentials in the first place. The real usage sites (embeddings.py,
# ingest.py's actual crawl+embed, server.py) still fail clearly/loudly the
# moment they're actually called with these unset — this only defers the
# failure from "unrelated import" to "actual use", which is strictly better.
QDRANT_URL = os.environ.get("QDRANT_URL")
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY")
JINA_API_KEY = os.environ.get("JINA_API_KEY")

QDRANT_COLLECTION = os.environ.get("QDRANT_COLLECTION", "design_system_index")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "jina-embeddings-v2-base-en")
EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "768"))

JINA_EMBED_URL = "https://api.jina.ai/v1/embeddings"

CHUNK_SIZE = 800
CHUNK_OVERLAP = 100
CRAWL_DELAY_SECONDS = 1.0

# Optional (not os.environ[...]) — the public submission form's /submit-system
# route degrades to a clear error instead of crashing server startup if this
# hasn't been configured yet. A fine-grained PAT scoped to just this repo's
# Issues: write is enough; classic PATs need the "repo" scope.
GITHUB_ISSUE_TOKEN = os.environ.get("GITHUB_ISSUE_TOKEN")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "NoWorries/ds-directory-mcp")
