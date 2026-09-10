import requests

from config import EMBEDDING_MODEL, JINA_API_KEY, JINA_EMBED_URL


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of text chunks via the Jina AI embeddings API."""
    if not texts:
        return []

    headers = {
        "Authorization": f"Bearer {JINA_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"model": EMBEDDING_MODEL, "input": texts}

    response = requests.post(JINA_EMBED_URL, json=payload, headers=headers, timeout=60)
    response.raise_for_status()
    data = response.json()["data"]

    # Jina returns results possibly out of input order; each item carries its index.
    data.sort(key=lambda item: item["index"])
    return [item["embedding"] for item in data]


def embed_query(query: str) -> list[float]:
    return embed_texts([query])[0]
