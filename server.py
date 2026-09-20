"""
Phase 3: MCP Gateway.

Exposes design_system_directory over Streamable HTTP so a conversational
frontend or embedded agent can query the indexed design systems.
"""

import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml
from mcp.server.fastmcp import FastMCP
from qdrant_client.models import FieldCondition, Filter, MatchAny
from starlette.requests import Request
from starlette.responses import JSONResponse

from config import GITHUB_ISSUE_TOKEN, GITHUB_REPO, QDRANT_COLLECTION, get_qdrant_client
from embeddings import embed_query
from text_utils import full_name

mcp = FastMCP(
    "DesignSystemKnowledgeBase",
    host="0.0.0.0",
    port=int(os.environ.get("PORT", 8000)),
)
qdrant_client = get_qdrant_client()

# Set once, at process start — Render's free tier spins up a brand new process
# on every cold start, so this timestamp doubles as "how long has the current
# instance been awake" for the /health endpoint below.
SERVER_STARTED_AT = datetime.now(timezone.utc).isoformat(timespec="seconds")

SYSTEMS_REGISTRY = Path(__file__).parent / "systems.yaml"

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "*",
}

# Field label -> exact text GitHub's issue-form UI renders for that field, so
# a synthetic issue body built here parses identically to a real form
# submission through parse_issue_form.py (used by run_submission_check.py and
# approve_submission.py) — nothing downstream needs to know these issues
# didn't come from the GitHub UI.
SUBMISSION_FIELDS = [
    ("organization", "Organization name", True),
    ("design_system", "Design system name", True),
    ("start_url", "Start URL", True),
    ("github_url", "GitHub repository (optional)", False),
    ("storybook_url", "Storybook URL (optional)", False),
    ("figma_url", "Figma file URL (optional)", False),
    ("npm_url", "npm package (optional)", False),
    ("notes", "Anything else we should know? (optional)", False),
    ("notify_email", "Email for approval notification (optional)", False),
]

# Bare-minimum abuse mitigation for a public, unauthenticated,
# issue-creating endpoint — an in-memory sliding window (fine for a
# single-process Render instance; resets on every cold start/redeploy,
# which is an acceptable trade-off for how low-traffic this form is).
_SUBMIT_WINDOW_SECONDS = 3600
_SUBMIT_MAX_PER_WINDOW = 5
_submit_timestamps: dict[str, list[float]] = defaultdict(list)


def _rate_limited(client_ip: str) -> bool:
    now = time.time()
    recent = [t for t in _submit_timestamps[client_ip] if now - t < _SUBMIT_WINDOW_SECONDS]
    _submit_timestamps[client_ip] = recent
    if len(recent) >= _SUBMIT_MAX_PER_WINDOW:
        return True
    recent.append(now)
    return False


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
        if full_name(entry) == design_system_name:
            return entry.get("resources", {})
    return {}


@mcp.tool()
def design_system_directory(user_query: str) -> str:
    """Semantic search over external design systems' public docs (e.g. Atlassian, Shopify
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


@mcp.custom_route("/health", methods=["GET", "OPTIONS"])
async def health(request: Request) -> JSONResponse:
    """Cheap liveness probe for the directory page's "is the MCP server awake"
    indicator — no Qdrant/embedding-API calls, just confirms the process itself is up
    and reports when this instance started (i.e. since the last cold start)."""
    return JSONResponse({"status": "awake", "server_started_at": SERVER_STARTED_AT}, headers=CORS_HEADERS)


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
