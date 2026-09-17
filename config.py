import os

from dotenv import load_dotenv

load_dotenv()

# .get(), not os.environ[...] — this module also supplies CHUNK_SIZE/
# CHUNK_OVERLAP/CRAWL_DELAY_SECONDS to code paths that never touch Qdrant or
# the embedding API at all (merge_shards.py, dry_run_submission.py's crawl-
# only dry run, approve_submission.py's registry read/write) via ingest.py's/
# chunking.py's module-level imports. A hard-required os.environ[...] here
# meant merely *importing* those modules crashed with a KeyError in any
# workflow step that (correctly, deliberately) didn't set these secrets —
# e.g. the merge job and the sandboxed dry-run check, neither of which should
# need live Qdrant/embedding credentials in the first place. The real usage
# sites (embeddings.py, ingest.py's actual crawl+embed, server.py) still fail
# clearly/loudly the moment they're actually called with these unset — this
# only defers the failure from "unrelated import" to "actual use", which is
# strictly better.
QDRANT_URL = os.environ.get("QDRANT_URL")
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY")
# Switched from Jina to Google's Gemini Embedding API (see embeddings.py) —
# Jina's free tier ran out mid-session with no warning short of the account
# dashboard, and Gemini's free tier (1,500 requests/minute, no credit card)
# is a better fit for this project's actual scale.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

QDRANT_COLLECTION = os.environ.get("QDRANT_COLLECTION", "design_system_index")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "gemini-embedding-001")
# gemini-embedding-001's NATIVE output size — confirmed live: an attempt to
# request a truncated 768-dim output via embedContentConfig.outputDimension-
# ality was silently ignored (Qdrant rejected every single upsert with
# "expected dim: 768, got 3072", meaning the API returned full-size vectors
# regardless of that parameter). Rather than keep guessing at the exact
# request shape Google wants for truncation with no way to test it live,
# this just uses the model's real native size — 4x the storage of a
# truncated 768-dim vector, but trivial at this project's scale (a few
# thousand chunks total) and well within Qdrant's free tier either way.
EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "3072"))

GEMINI_EMBED_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{EMBEDDING_MODEL}:batchEmbedContents"

CHUNK_SIZE = 800
CHUNK_OVERLAP = 100
CRAWL_DELAY_SECONDS = 1.0

# Optional (not os.environ[...]) — the public submission form's /submit-system
# route degrades to a clear error instead of crashing server startup if this
# hasn't been configured yet. A fine-grained PAT scoped to just this repo's
# Issues: write is enough; classic PATs need the "repo" scope.
GITHUB_ISSUE_TOKEN = os.environ.get("GITHUB_ISSUE_TOKEN")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "NoWorries/ds-directory-mcp")
