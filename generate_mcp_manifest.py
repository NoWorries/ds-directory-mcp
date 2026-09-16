"""
Builds .well-known/mcp.json — this site's own entry in the exact well-known
discovery path resources.py checks every OTHER system for (see
WELL_KNOWN_PATHS). We ship a real MCP server (server.py, at MCP_URL) and
never advertised it at the one path an agent would actually check for it
without already knowing our llms.txt exists.

There's no single ratified schema for mcp.json yet (unlike RFC 8615 itself,
which standardizes the .well-known/ prefix, not what any individual file
under it contains) — this uses the "mcpServers" shape Claude Desktop/Claude
Code/Cursor already use for their own local MCP config, since that's the
closest thing to a de facto convention and the format most MCP-aware
tooling can already parse without guessing.
"""

from __future__ import annotations

import json
from pathlib import Path

from generate_home import MCP_INSTALL_COMMAND, MCP_URL

OUTPUT_DIR = Path(__file__).parent / ".well-known"
OUTPUT_FILE = OUTPUT_DIR / "mcp.json"


def build_manifest() -> dict:
    return {
        "mcpServers": {
            "ds-directory": {
                "url": MCP_URL,
                "transport": "http",
                "description": (
                    "Semantic search across every publicly documented design system indexed "
                    "at this site — GitHub, Storybook, Figma, npm packages, tokens, icons, and "
                    "full-text page content."
                ),
                "install": MCP_INSTALL_COMMAND,
            }
        }
    }


if __name__ == "__main__":
    OUTPUT_DIR.mkdir(exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(build_manifest(), indent=2))
    print(f"Wrote {OUTPUT_FILE}")
