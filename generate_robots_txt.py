"""
Builds robots.txt — a plain site-root file (not under .well-known/, despite
the common confusion; RFC 8615's well-known prefix doesn't cover it) pointing
crawlers at sitemap.xml. This is the one file a bot conventionally checks
before it knows llms.txt or anything else here exists, so it's the natural
pairing for the sitemap this project only just added.

The Sitemap directive needs an absolute URL (same requirement as sitemap.xml
itself), so it's only included when the SITE_URL env var is set — but unlike
sitemap.xml/security.txt, robots.txt itself is still worth generating without
it (the Allow-everything line needs no domain), just without that one line.
"""

from __future__ import annotations

import os
from pathlib import Path

OUTPUT_FILE = Path(__file__).parent / "robots.txt"


def render_robots_txt(site_url: str | None) -> str:
    lines = ["User-agent: *", "Allow: /"]
    if site_url:
        lines += ["", f"Sitemap: {site_url.rstrip('/')}/sitemap.xml"]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    site_url = os.environ.get("SITE_URL", "").strip() or None
    OUTPUT_FILE.write_text(render_robots_txt(site_url))
    if site_url:
        print(f"Wrote {OUTPUT_FILE} (with Sitemap directive)")
    else:
        print(f"Wrote {OUTPUT_FILE} (SITE_URL unset — no Sitemap directive yet)")
