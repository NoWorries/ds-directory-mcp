import os

from dotenv import load_dotenv
from qdrant_client import QdrantClient

load_dotenv()

# .get(), not os.environ[...] — this module also supplies CHUNK_SIZE/
# CHUNK_OVERLAP/CRAWL_DELAY_SECONDS to code paths that never touch Qdrant at
# all (merge_shards.py, dry_run_submission.py's crawl-only dry run, approve_
# submission.py's registry read/write) via ingest.py's/chunking.py's
# module-level imports. A hard-required os.environ[...] here meant merely
# *importing* those modules crashed with a KeyError in any workflow step
# that (correctly, deliberately) didn't set these secrets — e.g. the merge
# job and the sandboxed dry-run check, neither of which should need a live
# Qdrant connection in the first place. The real usage sites (ingest.py's
# actual crawl+embed, server.py) still fail clearly/loudly the moment
# they're actually called with these unset — this only defers the failure
# from "unrelated import" to "actual use", which is strictly better.
# (Embeddings themselves need no such credential at all now — see
# embeddings.py — so this concern is Qdrant-only these days.)
QDRANT_URL = os.environ.get("QDRANT_URL")
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY")

QDRANT_COLLECTION = os.environ.get("QDRANT_COLLECTION", "design_system_index")
# Local, self-hosted embeddings (see embeddings.py) — no API key, no request/
# token quota, no billing, after burning through two different cloud
# providers' free tiers in one session (Jina's tokens ran out mid-run;
# Gemini's 100-RPM/1,000-RPD caps were blown past by a handful of parallel
# reindex shards). bge-small-en-v1.5 specifically: small enough (~130MB) to
# run on both a GitHub Actions runner (ingest) and Render's free-tier query
# server (server.py's embed_query() calls), English-only docs being exactly
# this project's use case so the larger multilingual models (BGE-M3, Nomic)
# buy nothing here.
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "384"))

CHUNK_SIZE = 800
CHUNK_OVERLAP = 100
CRAWL_DELAY_SECONDS = 1.0

# Optional (not os.environ[...]) — the public submission form's /submit-system
# route degrades to a clear error instead of crashing server startup if this
# hasn't been configured yet. A fine-grained PAT scoped to just this repo's
# Issues: write is enough; classic PATs need the "repo" scope.
GITHUB_ISSUE_TOKEN = os.environ.get("GITHUB_ISSUE_TOKEN")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "NoWorries/ds-directory-mcp")


def get_qdrant_client() -> QdrantClient:
    """QdrantClient(url=QDRANT_URL, ...) alone defaults its REST calls to
    port 6333 whenever QDRANT_URL has no explicit port — confirmed live,
    that gets connection-reset on a network that otherwise handles plain
    HTTPS on 443 (which is the same API, also served by Qdrant Cloud) just
    fine. Forcing port=443 everywhere sidesteps that class of network/
    firewall problem instead of only fixing it on one machine."""
    return QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, port=443)
