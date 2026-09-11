"""
Phase 3: MCP Gateway.

Exposes design_system_directory over Streamable HTTP so a conversational
frontend or embedded agent can query the indexed design systems.
"""

import os
from pathlib import Path

import yaml
from mcp.server.fastmcp import FastMCP
from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchAny
from starlette.requests import Request
from starlette.responses import JSONResponse

from config import QDRANT_API_KEY, QDRANT_COLLECTION, QDRANT_URL
from embeddings import embed_query

mcp = FastMCP(
    "DesignSystemKnowledgeBase",
    host="0.0.0.0",
    port=int(os.environ.get("PORT", 8000)),
)
qdrant_client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)

SYSTEMS_REGISTRY = Path(__file__).parent / "systems.yaml"

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "*",
}


def get_resources(design_system_name: str) -> dict:
    """Look up the auto-discovered resources (GitHub, Storybook, Figma, etc.) for a
    system from systems.yaml — kept out of Qdrant payloads to avoid duplicating
    per-chunk storage."""
    try:
        with open(SYSTEMS_REGISTRY) as f:
            entries = yaml.safe_load(f) or []
    except FileNotFoundError:
        return {}
    for entry in entries:
        if entry.get("name") == design_system_name:
            return entry.get("resources", {})
    return {}


@mcp.tool()
def design_system_directory(user_query: str) -> str:
    """Semantic search over EXTERNAL design systems' public docs (e.g. Atlassian, Shopify
    Polaris, Material, Carbon) for component structures, patterns, tokens, or styles.

    Use for cross-system research: "how does X handle Y", comparing patterns across
    design systems, or finding prior art before designing something new.
    """
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
        system_name = meta.get("design_system_name")
        resources = get_resources(system_name)
        resources_line = f"\nResources: {resources}" if resources else ""
        formatted.append(
            f"Source URL: {meta.get('url')}\n"
            f"System: {system_name}\n"
            f"Score: {hit.score:.3f}"
            f"{resources_line}\n"
            f"Documentation:\n{meta.get('text_content')}"
        )

    return "\n\n---\n\n".join(formatted)


@mcp.custom_route("/search", methods=["GET", "OPTIONS"])
async def search(request: Request) -> JSONResponse:
    """Plain HTTP JSON search endpoint for the raw search-page frontend (no LLM involved)."""
    if request.method == "OPTIONS":
        return JSONResponse({}, headers=CORS_HEADERS)

    query = request.query_params.get("q", "").strip()
    if not query:
        return JSONResponse({"error": "missing query param 'q'"}, status_code=400, headers=CORS_HEADERS)

    # Optional: ?system=Name+A&system=Name+B to scope results to specific design
    # systems (matches the multi-select filter on the directory page's search UI).
    systems = [s for s in request.query_params.getlist("system") if s.strip()]
    query_filter = (
        Filter(must=[FieldCondition(key="design_system_name", match=MatchAny(any=systems))])
        if systems
        else None
    )

    query_vector = embed_query(query)
    response = qdrant_client.query_points(
        collection_name=QDRANT_COLLECTION,
        query=query_vector,
        query_filter=query_filter,
        limit=5,
    )

    results = []
    for hit in response.points:
        meta = hit.payload or {}
        system_name = meta.get("design_system_name")
        results.append({
            "url": meta.get("url"),
            "design_system_name": system_name,
            "score": hit.score,
            "text": meta.get("text_content"),
            "resources": get_resources(system_name),
        })

    return JSONResponse({"results": results}, headers=CORS_HEADERS)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
