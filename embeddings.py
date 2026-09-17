from __future__ import annotations

import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

from config import EMBEDDING_MODEL, JINA_API_KEY, JINA_EMBED_URL

# Jina's free tier has a fairly low requests-per-minute cap, and a full reindex
# fires a lot of embedding calls back to back — a 429 here used to crash the
# whole shard (see ingest.py's --shard workflow) rather than just slowing
# down. Retry with exponential backoff instead of a fixed/linear wait, so
# repeated 429s space out fast (20s, 40s, 80s, 160s, 320s) rather than
# hammering the API at a near-constant cadence — and add jitter so the 4
# parallel reindex-shard runners don't all wake up and retry in the same
# instant, which would just trip the rate limit again. Retry-After from the
# API (a direct signal of the real window) always wins when present.
MAX_RETRIES = 5
BASE_BACKOFF_SECONDS = 20
JITTER_SECONDS = 5

# Response codes/exceptions worth retrying with the same backoff as a 429 —
# a transient 5xx or a dropped connection is exactly as recoverable as a rate
# limit, and previously crashed the whole shard just the same.
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


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


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of text chunks via the Jina AI embeddings API."""
    if not texts:
        return []

    headers = {
        "Authorization": f"Bearer {JINA_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"model": EMBEDDING_MODEL, "input": texts}

    for attempt in range(MAX_RETRIES + 1):
        default_wait = BASE_BACKOFF_SECONDS * (2**attempt)
        jitter = random.uniform(0, JITTER_SECONDS)
        try:
            response = requests.post(JINA_EMBED_URL, json=payload, headers=headers, timeout=60)
        except (requests.Timeout, requests.ConnectionError) as exc:
            if attempt >= MAX_RETRIES:
                raise
            wait = default_wait + jitter
            print(f"  Jina request failed ({exc}) — waiting {wait:.0f}s before retry {attempt + 1}/{MAX_RETRIES}")
            time.sleep(wait)
            continue

        if response.status_code in RETRYABLE_STATUS_CODES and attempt < MAX_RETRIES:
            # A numeric/parsed Retry-After is a real signal from the server
            # about the actual window, so it takes priority over our own
            # backoff estimate — but jitter is still added on top so parallel
            # shards retrying off the same Retry-After value don't
            # re-synchronise and immediately re-trip the limit together.
            wait = max(_parse_retry_after(response.headers.get("Retry-After"), default_wait), default_wait) + jitter
            print(f"  Jina returned {response.status_code} — waiting {wait:.0f}s before retry {attempt + 1}/{MAX_RETRIES}")
            time.sleep(wait)
            continue

        response.raise_for_status()
        try:
            data = response.json()["data"]
        except (ValueError, KeyError) as exc:
            if attempt >= MAX_RETRIES:
                raise
            wait = default_wait + jitter
            print(f"  Jina response malformed ({exc}) — waiting {wait:.0f}s before retry {attempt + 1}/{MAX_RETRIES}")
            time.sleep(wait)
            continue
        # Jina returns results possibly out of input order; each item carries its index.
        data.sort(key=lambda item: item["index"])
        return [item["embedding"] for item in data]


def embed_query(query: str) -> list[float]:
    return embed_texts([query])[0]
