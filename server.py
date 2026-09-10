"""
Phase 3: MCP Gateway.

Exposes semantic_design_search over Streamable HTTP so a conversational
frontend or embedded agent can query the indexed design systems.
"""

from mcp.server.fastmcp import FastMCP
from qdrant_client import QdrantClient

from config import QDRANT_API_KEY, QDRANT_COLLECTION, QDRANT_URL
from embeddings import embed_query

mcp = FastMCP("DesignSystemKnowledgeBase")
qdrant_client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)


@mcp.tool()
def semantic_design_search(user_query: str) -> str:
    """Search the indexed design systems for component structures, patterns, tokens, or styles matching the query."""
    query_vector = embed_query(user_query)

    search_results = qdrant_client.search(
        collection_name=QDRANT_COLLECTION,
        query_vector=query_vector,
        limit=3,
    )

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
