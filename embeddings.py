import random
import time

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
        response = requests.post(JINA_EMBED_URL, json=payload, headers=headers, timeout=60)
        if response.status_code == 429 and attempt < MAX_RETRIES:
            default_wait = BASE_BACKOFF_SECONDS * (2**attempt) + random.uniform(0, JITTER_SECONDS)
            wait = float(response.headers.get("Retry-After", default_wait))
            print(f"  Jina rate-limited (429) — waiting {wait:.0f}s before retry {attempt + 1}/{MAX_RETRIES}")
            time.sleep(wait)
            continue
        response.raise_for_status()
        data = response.json()["data"]
        # Jina returns results possibly out of input order; each item carries its index.
        data.sort(key=lambda item: item["index"])
        return [item["embedding"] for item in data]


def embed_query(query: str) -> list[float]:
    return embed_texts([query])[0]
