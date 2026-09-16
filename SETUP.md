# Account setup

Five services needed, all with free tiers. For each: create the account, then copy the
listed value(s) into your local `.env` (`cp .env.example .env`), GitHub repo secrets, and
Render's environment settings — the same three values go in all three places.

---

## 1. Qdrant Cloud (vector database)

1. Go to https://cloud.qdrant.io and sign up (Google/GitHub login or email).
2. Create a **Free Tier cluster** (1GB RAM / 4GB disk, free forever — no card required).
   Pick any region close to you or your other services.
3. Once the cluster is running, open it and find:
   - **Cluster URL** — looks like `https://xxxxxxxx-xxxx-xxxx.region.aws.cloud.qdrant.io`
   - **API Key** — generate one from the cluster's "API Keys" tab if not shown automatically.

Copy into `.env` as:
```
QDRANT_URL=<cluster URL>
QDRANT_API_KEY=<api key>
```

---

## 2. Jina AI (embeddings)

1. Go to https://jina.ai and sign up.
2. Go to the API key / dashboard section (sometimes under "Embeddings" or "API").
3. Generate an API key. Free tier is 1M tokens/month, no card required.

Copy into `.env` as:
```
JINA_API_KEY=<api key>
```

---

## 3. GitHub (already have this — just add secrets)

This repo needs to exist on GitHub for the scheduled re-indexing workflow to run.

1. Push this repo to GitHub if you haven't: `gh repo create ds-directory-mcp --private --source=. --push`
   (or create it manually on github.com and `git push` to it).
2. In the repo: **Settings → Secrets and variables → Actions → New repository secret**.
3. Add three secrets, using the same values as your `.env`:
   - `QDRANT_URL`
   - `QDRANT_API_KEY`
   - `JINA_API_KEY`

No new account needed here, just repo secrets — nothing else to sign up for.

---

## 4. Render (MCP server hosting)

1. Go to https://render.com and sign up (GitHub login is easiest — it can read your repos directly).
2. **New + → Blueprint**, then select this GitHub repo. Render will detect `render.yaml`
   and propose the `ds-directory-mcp` web service on the free plan.
3. Before/during creation, Render will ask for the environment variables marked
   `sync: false` in `render.yaml` — enter the same three values:
   - `QDRANT_URL`
   - `QDRANT_API_KEY`
   - `JINA_API_KEY`
4. Deploy. Render will build (`pip install -r requirements.txt`) and run (`python server.py`).
5. Copy the service's public URL (e.g. `https://ds-directory-mcp.onrender.com`) — this is
   what you'll point an MCP client at.

Note: Render's free tier sleeps after ~15 min of no traffic; the next request cold-starts
(30-60s). Fine for occasional use, just don't expect instant responses after idle periods.

---

## 5. Netlify (static directory site hosting)

The searchable listing page (`directory.html`) and its `components/`/`systems/` subpages
are static — they don't need Render, and deploy separately via the workflows to a Netlify
site so browsing has no cold start.

1. Go to https://app.netlify.com and sign up (GitHub login is easiest), then create a site
   (any placeholder deploy is fine — the workflows will overwrite it).
2. Get a **Personal Access Token**: click your avatar (top-right) → **User settings** →
   **Applications** → **Personal access tokens** → **New access token**. Copy it immediately —
   it's not shown again.
3. Get the site's **Site ID**: open your site in Netlify → **Site configuration** →
   **General** → **Site details** → copy **Site ID** (a UUID, not the site name).
4. Add both as **GitHub repo secrets** (Settings → Secrets and variables → Actions):
   - `NETLIFY_AUTH_TOKEN`
   - `NETLIFY_SITE_ID`
5. Add the site's actual public URL as a **GitHub repo *variable*** (same Settings page, but
   the **Variables** tab, not **Secrets** — it's not sensitive, and workflows read it as
   `${{ vars.SITE_URL }}`): `SITE_URL`, e.g. `https://designsystems-mcp.netlify.app`
   (current value at time of writing — update this if/when the site moves to a custom
   domain, e.g. `https://designsystems.directory`). Only `generate_sitemap.py` uses this
   (a sitemap's URLs must be absolute) — until it's set, sitemap.xml is skipped rather than
   generated with a wrong domain baked in.

### 5a. Issue-filing bot token (for the "Suggest a system" / "Report an issue" forms)

`/suggest` and `/report` (see `generate_forms.py`) are real forms, not GitHub Issue Forms —
a visitor doesn't need their own GitHub account. They POST to
`netlify/functions/submit-form.js`, which files the actual GitHub issue on a bot account.

1. Create a **fine-grained Personal Access Token** (GitHub → Settings → Developer settings →
   Personal access tokens → Fine-grained tokens) scoped to just this repo, with **Issues:
   read and write** permission — nothing else.
2. In Netlify: **Site configuration** → **Environment variables** → add `ISSUE_BOT_TOKEN`
   with that token as the value. (This one lives in Netlify, not GitHub secrets — it's the
   function's own runtime, not a workflow.)

Until this is set, both forms will show a "Form submission isn't configured yet" error
instead of silently failing.

No local Netlify CLI login needed — the workflows authenticate with these two secrets when
they run `netlify-cli deploy --prod`.

---

## Summary: what goes where

| Value | Source | Goes into |
|---|---|---|
| `QDRANT_URL` | Qdrant Cloud cluster page | `.env`, GitHub secrets, Render env vars |
| `QDRANT_API_KEY` | Qdrant Cloud → API Keys | `.env`, GitHub secrets, Render env vars |
| `JINA_API_KEY` | Jina AI dashboard | `.env`, GitHub secrets, Render env vars |
| Render service URL | Render dashboard, after deploy | Your MCP client config (Claude Code/Desktop) |
| `NETLIFY_AUTH_TOKEN` | Netlify → User settings → Applications | GitHub secrets only |
| `NETLIFY_SITE_ID` | Netlify → Site configuration → General | GitHub secrets only |

Nothing here should ever be committed to git — `.env` is already in `.gitignore`.
