from __future__ import annotations

from config import EMBEDDING_DIM, EMBEDDING_MODEL

# Local, self-hosted embeddings (sentence-transformers) — no API key, no
# quota, no billing, no network call at all once the model weights are
# cached. Switched here after burning through two different cloud
# providers' free tiers in one session (Jina's tokens ran out mid-run;
# Gemini's 100-RPM/1,000-RPD free-tier caps were blown past by a handful of
# parallel reindex shards) — a model small enough to run on a GitHub
# Actions runner's CPU (and on Render's free-tier query server, see
# server.py's embed_query() call) sidesteps that whole category of problem
# permanently, at the cost of needing the `sentence-transformers` package
# installed (see requirements.txt/requirements-ingest.txt) and a few
# hundred MB downloaded once and cached (see the reindex workflows'
# actions/cache step, keyed on EMBEDDING_MODEL).
#
# Loaded lazily (not at import time) and cached in a module-level global —
# constructing a SentenceTransformer loads the model weights from disk/
# HuggingFace's cache, which is slow enough (seconds) that doing it once per
# process rather than once per embed_texts() call matters. Every caller in
# this codebase (ingest.py's per-batch embed calls, server.py's per-query
# embed) already goes through embed_texts()/embed_query() below, so a single
# shared instance is transparent to all of them.
_model = None


def _get_model():
    global _model
    if _model is None:
        # Deferred import — sentence-transformers (and its torch dependency)
        # is a heavy enough import that modules which never actually embed
        # anything (generate_*.py's static-page rendering, merge_shards.py)
        # shouldn't pay for it just by importing this module's embed_texts/
        # embed_query for type-checking purposes or via a shared import chain.
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(EMBEDDING_MODEL)
    return _model


# A reasonable chunk size for encode() calls — unlike the old cloud-API
# providers, there's no hard per-request limit to respect locally, but
# encoding many hundreds of chunks in one Python call still uses more
# memory at once than encoding them in smaller batches, and batching is
# also how ingest.py's _chunk_and_upsert() amortizes one call across many
# small pages' chunks rather than one call per page. 100 mirrors the limit
# the previous (cloud) provider enforced, kept for continuity rather than
# any actual local constraint.
MAX_BATCH_SIZE = 100


# A local model failing to load (missing package, corrupted/incomplete
# HuggingFace cache download, out-of-memory) is the equivalent failure mode
# to the old cloud providers' "your API key/quota is broken" — it affects
# every remaining embed call in this run identically, not just one page, so
# it's raised as its own type rather than a generic Exception. See
# ingest.py's _chunk_and_upsert()/safe_run(), which let this propagate
# instead of treating it as a per-page/per-system retryable failure.
class EmbeddingFatalError(Exception):
    pass


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed any number of text chunks locally, in batches of
    <=MAX_BATCH_SIZE (mainly for memory headroom — see above), returning one
    vector per input text in order."""
    if not texts:
        return []
    try:
        model = _get_model()
    except Exception as exc:
        raise EmbeddingFatalError(
            f"Failed to load the local embedding model ({EMBEDDING_MODEL!r}): {exc}. Check that "
            f"sentence-transformers is installed (see requirements.txt) and that the model weights "
            f"downloaded correctly (first run needs network access to HuggingFace; after that they're "
            f"cached locally)."
        ) from exc

    vectors: list[list[float]] = []
    for i in range(0, len(texts), MAX_BATCH_SIZE):
        batch = texts[i : i + MAX_BATCH_SIZE]
        try:
            # normalize_embeddings=True: Qdrant's collection here uses cosine
            # distance, which assumes unit-length vectors.
            batch_vectors = model.encode(batch, normalize_embeddings=True, show_progress_bar=False)
        except Exception as exc:
            raise EmbeddingFatalError(f"Local embedding inference failed: {exc}") from exc
        vectors.extend(vector.tolist() for vector in batch_vectors)

    if len(vectors) != len(texts):
        raise ValueError(f"Local model returned {len(vectors)} embeddings for {len(texts)} input texts")
    if vectors and len(vectors[0]) != EMBEDDING_DIM:
        raise ValueError(
            f"Local model {EMBEDDING_MODEL!r} produced {len(vectors[0])}-dim vectors, expected "
            f"EMBEDDING_DIM={EMBEDDING_DIM} — update EMBEDDING_DIM (and re-embed the corpus) to match."
        )
    return vectors


def embed_query(query: str) -> list[float]:
    return embed_texts([query])[0]
