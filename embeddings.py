from __future__ import annotations

import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

from config import EMBEDDING_MODEL, GEMINI_API_KEY, GEMINI_EMBED_URL

# The Gemini API's free tier is generous (1,500 requests/minute at time of
# writing) but still finite, and a full reindex fires a lot of embedding
# calls back to back — a 429 here used to crash the whole shard (see
# ingest.py's --shard workflow) rather than just slowing down. Retry with
# exponential backoff instead of a fixed/linear wait, so repeated 429s space
# out fast (20s, 40s, 80s, 160s, 320s) rather than hammering the API at a
# near-constant cadence — and add jitter so the 4 parallel reindex-shard
# runners don't all wake up and retry in the same instant, which would just
# trip the rate limit again. Retry-After from the API (a direct signal of
# the real window) always wins when present.
MAX_RETRIES = 5
BASE_BACKOFF_SECONDS = 20
JITTER_SECONDS = 5

# Response codes/exceptions worth retrying with the same backoff as a 429 —
# a transient 5xx or a dropped connection is exactly as recoverable as a rate
# limit, and previously crashed the whole shard just the same.
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# 401/403 are a different category of failure from the retryable ones
# above: an invalid/expired API key, a disabled API, or an exhausted account
# quota (confirmed live on the previous embedding provider — Jina returned
# 403 once its key ran out of free tokens, and every remaining embed call in
# the shard failed the exact same way, one page at a time, each just logged
# as "will retry next run" while the crawl for every remaining system still
# ran to completion first — wasting the whole run's worth of time before
# the exact same wall got hit again on the next scheduled run). No amount
# of retrying or waiting fixes this; it needs a human to go check the key/
# quota/billing. EmbeddingAuthError lets callers tell this apart from a
# per-page retryable failure and stop the whole run immediately instead of
# ploughing through every other system only to hit this same error hundreds
# more times. Named for the failure mode, not the provider, since whichever
# embedding API is behind config.py next hits the same category of error.
#
# 400 is deliberately NOT included here even though it sounds similar —
# confirmed live, Gemini also returns 400 for a well-formed request that's
# simply too big ("at most 100 requests can be in one batch", hit on
# Atlassian's 570KB llms-full.txt, which chunks into 800+ pieces). That's a
# per-call shape problem MAX_BATCH_SIZE below already prevents, not an
# account problem — treating every 400 as fatal would abort the whole run
# over something batching alone fixes.
class EmbeddingAuthError(Exception):
    pass


def _parse_retry_after(value: str | None, default_wait: float) -> float:
    """Retry-After is usually a plain integer seconds count, but per RFC 7231
    it may instead be an HTTP-date — float(...) on one of those raises
    ValueError, which used to propagate uncaught and fail every remaining
    page in the batch while the rate limit was still in effect. Falls back
    to the computed exponential-backoff wait on anything that doesn't parse
    either way."""
    if not value:
        return default_wait
    try:
        return float(value)
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max((dt - datetime.now(timezone.utc)).total_seconds(), 0.0)
    except (TypeError, ValueError, OverflowError):
        return default_wait


# Gemini's real, confirmed-live hard limit for batchEmbedContents ("at most
# 100 requests can be in one batch") — NOT the 2,048 figure floating around
# in some third-party write-ups, which does not hold for this endpoint. A
# single big page (Atlassian's llms-full.txt: 570KB, 800+ chunks at
# CHUNK_SIZE=800) blows past this in one call otherwise.
MAX_BATCH_SIZE = 100


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed any number of text chunks, splitting into <=MAX_BATCH_SIZE calls
    to Gemini's batchEmbedContents endpoint as needed — transparent to the
    caller, which just gets back one vector per input text in order, same
    as the single-call-per-page pattern the rest of ingest.py relies on."""
    vectors: list[list[float]] = []
    for i in range(0, len(texts), MAX_BATCH_SIZE):
        vectors.extend(_embed_batch(texts[i : i + MAX_BATCH_SIZE]))
    return vectors


def _embed_batch(texts: list[str]) -> list[list[float]]:
    """One HTTP call, <=MAX_BATCH_SIZE texts."""
    if not texts:
        return []

    headers = {
        "x-goog-api-key": GEMINI_API_KEY,
        "Content-Type": "application/json",
    }
    model_name = f"models/{EMBEDDING_MODEL}"
    # Deliberately NOT requesting a truncated outputDimensionality — see
    # config.py's EMBEDDING_DIM comment for why this uses the model's native
    # 3072-dim output as-is rather than trying to get Google's truncation
    # parameter shape exactly right with no way to test it live.
    payload = {
        "requests": [
            {
                "model": model_name,
                "content": {"parts": [{"text": text}]},
            }
            for text in texts
        ]
    }

    for attempt in range(MAX_RETRIES + 1):
        default_wait = BASE_BACKOFF_SECONDS * (2**attempt)
        jitter = random.uniform(0, JITTER_SECONDS)
        try:
            response = requests.post(GEMINI_EMBED_URL, json=payload, headers=headers, timeout=60)
        except (requests.Timeout, requests.ConnectionError) as exc:
            if attempt >= MAX_RETRIES:
                raise
            wait = default_wait + jitter
            print(f"  Gemini request failed ({exc}) — waiting {wait:.0f}s before retry {attempt + 1}/{MAX_RETRIES}")
            time.sleep(wait)
            continue

        if response.status_code in RETRYABLE_STATUS_CODES and attempt < MAX_RETRIES:
            # A numeric/parsed Retry-After is a real signal from the server
            # about the actual window, so it takes priority over our own
            # backoff estimate — but jitter is still added on top so parallel
            # shards retrying off the same Retry-After value don't
            # re-synchronise and immediately re-trip the limit together.
            wait = max(_parse_retry_after(response.headers.get("Retry-After"), default_wait), default_wait) + jitter
            print(f"  Gemini returned {response.status_code} — waiting {wait:.0f}s before retry {attempt + 1}/{MAX_RETRIES}")
            time.sleep(wait)
            continue

        if response.status_code in (401, 403):
            raise EmbeddingAuthError(
                f"Gemini returned {response.status_code} — this means an invalid/expired GEMINI_API_KEY, the "
                f"Generative Language API not being enabled for this key's project, or an exhausted quota, not "
                f"a transient error, so it won't be retried. Check https://aistudio.google.com/apikey. "
                f"Response body: {response.text[:500]}"
            )

        response.raise_for_status()
        try:
            embeddings = response.json()["embeddings"]
            vectors = [item["values"] for item in embeddings]
        except (ValueError, KeyError) as exc:
            if attempt >= MAX_RETRIES:
                raise
            wait = default_wait + jitter
            print(f"  Gemini response malformed ({exc}) — waiting {wait:.0f}s before retry {attempt + 1}/{MAX_RETRIES}")
            time.sleep(wait)
            continue
        if len(vectors) != len(texts):
            raise ValueError(f"Gemini returned {len(vectors)} embeddings for {len(texts)} input texts")
        return vectors


def embed_query(query: str) -> list[float]:
    return embed_texts([query])[0]
