"""
Phase 3: MCP Gateway.

Exposes design_system_directory over Streamable HTTP so a conversational
frontend or embedded agent can query the indexed design systems.
"""
import os
from mcp.server.fastmcp import FastMCP
from qdrant_client import QdrantClient

from config import QDRANT_API_KEY, QDRANT_COLLECTION, QDRANT_URL
from embeddings import embed_query

mcp = FastMCP(
    "DesignSystemKnowledgeBase",
    host="0.0.0.0",
    port=int(os.environ.get("PORT", 8000)),
)
qdrant_client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)


@mcp.tool()
def design_system_directory(user_query: str) -> str:
    """Semantic search over design systems' public docs (e.g. Atlassian, Shopify
    Polaris, Material, Carbon) for component structures, patterns, tokens, or styles.

    Use for cross-system research: "how does X handle Y", comparing patterns across
    design systems, or finding prior art before designing something new."""
    query_vector = embed_query(user_query)

    response = qdrant_client.query_points(
        collection_name=QDRANT_COLLECTION,
        query=query_vector,
        limit=3,
    )
    search_results = response.points

    if not search_results:
        return "No matching design system documentation found."

    formatted = []
    for hit in search_results:
        meta = hit.payload or {}
        formatted.append(
            f"Source URL: {meta.get('url')}\n"
            f"System: {meta.get('design_system_name')}\n"
            f"Score: {hit.score:.3f}\n"
            f"Documentation:\n{meta.get('text_content')}"
        )

    return "\n\n---\n\n".join(formatted)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")