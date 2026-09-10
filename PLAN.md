# Plan: ds-directory-mcp

Semantic search (RAG) + MCP server over external design system documentation
(everything except XUI, which has its own `xui-components-mcp`).

## Architecture

```
[Ingestion]              [Storage & Search]        [MCP Gateway]           [Consumption]
ingest.py            ──> Qdrant Cloud          ──> server.py           ──> Claude / any MCP client
(crawl, chunk,            (vector DB)               (FastMCP tool:          (interpreted answers)
 embed via Jina)                                     design_system_directory)
     ▲
     │ scheduled by
GitHub Actions
(reindex.yml, weekly)
```

Optional add-on (not yet built): a small static search page + `/search` endpoint on the
same Render host, for viewing raw matched chunks without going through an LLM.

## Status

| Piece | State |
|---|---|
| Ingestion script (crawl/chunk/embed/upsert) | ✅ built — [ingest.py](ingest.py) |
| Systems registry | ✅ built — [systems.yaml](systems.yaml), 2 entries (Atlassian, Shopify Polaris) |
| MCP server (`design_system_directory`) | ✅ built — [server.py](server.py) |
| Scheduled re-indexing | ✅ built — [.github/workflows/reindex.yml](.github/workflows/reindex.yml) |
| Render deploy config | ✅ built — [render.yaml](render.yaml) |
| Accounts/services provisioned | ⬜ **not started — this is the next step** |
| First real ingestion run | ⬜ blocked on accounts |
| MCP server deployed to Render | ⬜ blocked on accounts |
| Connect Claude to the deployed MCP server | ⬜ blocked on deploy |
| Optional search-only web UI | ⬜ not built, deferred |

## Remaining steps, in order

1. **Create accounts** for Qdrant Cloud, Jina AI, and (if not already) GitHub + Render.
   See [SETUP.md](SETUP.md) for exact steps and what to copy from each.
2. **Fill in `.env` locally** from `.env.example` with the Qdrant/Jina credentials.
3. **Run a test ingestion** for one system locally: `python ingest.py --system "Shopify Polaris"`.
4. **Add the same secrets to GitHub** (repo → Settings → Secrets and variables → Actions)
   so the scheduled reindex workflow can run.
5. **Deploy `server.py` to Render** using `render.yaml`, with the same secrets set in
   Render's environment settings.
6. **Connect the deployed MCP server URL to Claude** (Claude Code / Claude Desktop MCP config).
7. Expand `systems.yaml` with more design systems as needed.
