"""
Builds .well-known/security.txt (RFC 9116) — a standard, machine-readable way
to say how to report a security vulnerability, rather than a bug hunter
having to guess or dig through the repo for a contact. Cheap good practice
for any public site, and this one accepts user-submitted content (the
suggest/report forms), so it's not purely theoretical here.

Like sitemap.xml, RFC 9116 requires the Canonical field to be this file's own
absolute URL, so this reads the deploy domain from the same SITE_URL env var
and skips generation with a clear message when it's unset — a wrong domain
baked into a security contact file is worse than not having one yet.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent / ".well-known"
OUTPUT_FILE = OUTPUT_DIR / "security.txt"

# No dedicated security contact mailbox exists for this project — its own
# GitHub Issues is the real, already-monitored channel, and RFC 9116 allows
# Contact to be a URL, not just a mailto:.
CONTACT_URL = "https://github.com/NoWorries/ds-directory-mcp/issues"

# RFC 9116 requires Expires so a stale, unmaintained file doesn't linger
# looking current forever — a year out is the spec's own suggested ceiling.
EXPIRES_AFTER = timedelta(days=365)


def render_security_txt(site_url: str) -> str:
    expires = (datetime.now(timezone.utc) + EXPIRES_AFTER).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        f"Contact: {CONTACT_URL}\n"
        f"Expires: {expires}\n"
        "Preferred-Languages: en\n"
        f"Canonical: {site_url.rstrip('/')}/.well-known/security.txt\n"
    )


if __name__ == "__main__":
    site_url = os.environ.get("SITE_URL", "").strip()
    if not site_url:
        print(
            "SITE_URL not set — skipping security.txt (its Canonical field must be "
            "an absolute URL). Set it once this site has a real domain to enable this."
        )
    else:
        OUTPUT_DIR.mkdir(exist_ok=True)
        OUTPUT_FILE.write_text(render_security_txt(site_url))
        print(f"Wrote {OUTPUT_FILE}")
