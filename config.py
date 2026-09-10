import os

from dotenv import load_dotenv

load_dotenv()

QDRANT_URL = os.environ["QDRANT_URL"]
QDRANT_API_KEY = os.environ["QDRANT_API_KEY"]
JINA_API_KEY = os.environ["JINA_API_KEY"]

QDRANT_COLLECTION = os.environ.get("QDRANT_COLLECTION", "design_system_index")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "jina-embeddings-v2-base-en")
EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "768"))

JINA_EMBED_URL = "https://api.jina.ai/v1/embeddings"

CHUNK_SIZE = 800
CHUNK_OVERLAP = 100
CRAWL_DELAY_SECONDS = 1.0
