# ds-directory-mcp

Self-hosted, free-tier semantic search (RAG) + MCP server for **external** design system
documentation. Replaces keyword-based search (e.g. Google Programmable Search) with
intent-based vector search over design tokens, component architectures, and code
patterns — exposed to any MCP-compatible AI assistant via one tool: `design_system_directory`.

## Scope: this is not the XUI MCP

XUI already has its own structured, purpose-built MCP server (`xui-components-mcp` —
`list_components`, `get_component_docs`). Use that for anything XUI: it knows props,
variants, and exact component structure directly from the source.

`ds-directory-mcp` covers everything XUI doesn't have a server for — Material, Atlassian,
Shopify Polaris, Carbon, Ant Design, Fluent, etc. — useful for cross-system research
("how do other systems handle X") and for informing XUI's own design decisions with
outside context. Register both MCP servers with your assistant and let it pick the
right one per query; don't index XUI here, and don't expect exact prop-level accuracy
from this server the way you'd get from `xui-components-mcp` — this is semantic
similarity search over crawled docs, not a structured component API.

## Architecture

```
[Phase 1: Ingestion]  ──>  [Phase 2: Storage & Search]  ──>  [Phase 3: MCP Gateway]
 ingest.py (crawl,             Qdrant Cloud                    server.py
 chunk, embed)                 (vector DB, free tier)          (FastMCP, Render free tier)
```

| Phase | Component | Free-tier service |
|---|---|---|
| 1 | Crawl & scrape | `requests` + `BeautifulSoup4` (local, or GitHub Actions) |
| 1 | Embeddings | Jina AI (`jina-embeddings-v2-base-en`, 1M free tokens/mo) |
| 2 | Vector DB | Qdrant Cloud (1GB RAM / 4GB disk, free forever) |
| 3 | MCP hosting | Render free web service |

## Setup

1. Create a free [Qdrant Cloud](https://cloud.qdrant.io) cluster and a [Jina AI](https://jina.ai) API key.
2. `python -m venv .venv && source .venv/bin/activate`
3. `pip install -r requirements.txt`
4. `cp .env.example .env` and fill in `QDRANT_URL`, `QDRANT_API_KEY`, `JINA_API_KEY`.

## Ingesting a design system

```bash
python ingest.py "Xero XUI" https://xui.xero.com/components/button
```

Crawls same-domain links from the given start URL(s), strips nav/footer/script noise,
chunks text (800 chars, 100 overlap), embeds each chunk, and upserts into the
`design_system_index` Qdrant collection with `{url, design_system_name, text_content}` payloads.

## Running the MCP server locally

```bash
python server.py
```

Serves `design_system_directory(user_query: str)` over Streamable HTTP.

## Adding design systems

Register each one in [`systems.yaml`](systems.yaml) (name + start URL(s)) instead of typing
the command by hand each time:

```bash
python ingest.py --system "Shopify Polaris"   # one entry from the registry
python ingest.py --all                        # every entry in the registry
```

Re-running a system deletes its previously indexed chunks first (matched by
`design_system_name`), so re-ingestion replaces stale content instead of piling up
duplicates. XUI is deliberately not registered here — see "Scope" above.

## Automated re-indexing (GitHub Actions, free)

`.github/workflows/reindex.yml` runs `python ingest.py --all` on a weekly cron
(also triggerable manually via "Run workflow"). This runs on GitHub's free Actions
minutes — no always-on server needed, unlike the MCP server itself. Set these as
**repo secrets** (Settings → Secrets and variables → Actions), never commit them:

- `QDRANT_URL`
- `QDRANT_API_KEY`
- `JINA_API_KEY`

Render hosts the always-on MCP query server; GitHub Actions handles the periodic
batch re-crawl — different lifecycles, so they're split across two free hosts.

## Deploying (Render free tier)

`render.yaml` is included — connect this repo in the Render dashboard ("New +" → "Blueprint"),
and set `QDRANT_URL`, `QDRANT_API_KEY`, `JINA_API_KEY` as secrets in the service's environment
settings (never commit them). Render's free tier sleeps on idle — the first request after
a period of inactivity will be slow ("cold start"); surface a loading state for this in any
client UI.

## Notes

- Secrets live only in environment variables (local `.env`, or the host's dashboard) — never in git.
- Ingestion sleeps 1s between page fetches to respect target servers.
- At 768 dimensions, ~100k vectors uses ~300MB — comfortably under Qdrant's 1GB RAM ceiling.
