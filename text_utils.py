from __future__ import annotations

import re


def split_org_name(entry: dict) -> tuple[str, str]:
    """A systems.yaml entry's (organization, design_system) fields, e.g.
    ("Adobe", "Spectrum"). organization is "" for entries with no real org
    (e.g. a standalone framework like "Foundation")."""
    return entry.get("organization", ""), entry.get("design_system", "")


def full_name(entry: dict) -> str:
    """"Adobe" + "Spectrum" -> "Adobe — Spectrum" — the single display/identity
    string used for Qdrant's design_system_name payload, pages_index.json keys,
    and slugs. Falls back to just the design_system when there's no org."""
    org, ds_name = split_org_name(entry)
    return f"{org} — {ds_name}" if org else ds_name


def clean_title(title: str) -> str:
    """"Button - Components - Atlassian Design" -> "Button". Site doc-page
    titles are almost always "<actual title> <separator> <site name...>" —
    the first segment is what's worth showing once we're already on that
    system's own page (repeating the system name there is just noise)."""
    return re.split(r"\s*[-|–—]\s*", title, maxsplit=1)[0].strip()
