#!/usr/bin/env bash
# Assembles ./site from whatever generate_*.py already wrote to the repo
# root this run, then deploys it to Netlify. Shared by every workflow that
# deploys the site (build-site.yml, reindex-full/new/shallow/signals-
# refresh/spa.yml, submission-approve.yml) — this exact 19-line block used
# to be copy-pasted into each one, which meant a fix here (a new file to
# copy, a changed deploy flag) had to be applied 7 times by hand and
# inevitably drifted between them.
#
# Requires NETLIFY_AUTH_TOKEN and NETLIFY_SITE_ID in the environment (set as
# `env:` on the calling workflow step, as before). Run from the repo root,
# after the generate_*.py steps.
set -euo pipefail

mkdir -p site
cp favicon.svg site/favicon.svg
cp favicon-fallback.svg site/favicon-fallback.svg
cp home.html site/index.html
cp search.html site/search.html
cp suggest.html site/suggest.html
cp report.html site/report.html
cp llms.txt site/llms.txt
if [ -f sitemap.xml ]; then cp sitemap.xml site/sitemap.xml; fi
mkdir -p site/.well-known && cp .well-known/mcp.json site/.well-known/mcp.json
if [ -f .well-known/security.txt ]; then cp .well-known/security.txt site/.well-known/security.txt; fi
cp robots.txt site/robots.txt
cp systems.json site/systems.json
cp design-system.schema.json site/design-system.schema.json
cp directory.html site/directory.html
if [ -d screenshots ]; then cp -r screenshots site/screenshots; fi
if [ -d components ]; then cp -r components site/components; fi
if [ -d patterns ]; then cp -r patterns site/patterns; fi
if [ -d foundations ]; then cp -r foundations site/foundations; fi
if [ -d systems ]; then cp -r systems site/systems; fi

# --site pinned explicitly (not just via the NETLIFY_SITE_ID env var) and the
# CLI version pinned too — an unpinned `npx netlify-cli` can pick up a newer
# major version whose deploy-target resolution changed, which is a known way
# for --prod to land somewhere other than the site your custom domain
# actually points at (the domain then 404s even though the deploy itself
# "succeeded").
npx --yes netlify-cli@17 deploy --site "$NETLIFY_SITE_ID" --dir=site --prod
