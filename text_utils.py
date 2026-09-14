from __future__ import annotations

import re

# Common em/en-dash and smart-quote sequences that show up as mojibake when a
# UTF-8 page got decoded as Latin-1 (see ingest.py's fetch_page — fixed there
# for future crawls, but titles already stored in pages_index.json from before
# that fix still carry the garbled bytes). Repaired here on read so already-
# crawled titles display correctly without needing a re-crawl.
_MOJIBAKE_REPAIRS = {
    'â\x80\x94': '—',  # em dash
    'â\x80\x93': '–',  # en dash
    'â\x80\x99': '’',  # right single quote / apostrophe
    'â\x80\x98': '‘',  # left single quote
    'â\x80\x9c': '“',  # left double quote
    'â\x80\x9d': '”',  # right double quote
}


def repair_mojibake(text: str) -> str:
    for bad, good in _MOJIBAKE_REPAIRS.items():
        text = text.replace(bad, good)
    return text


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
    system's own page (repeating the system name there is just noise).
    Runs repair_mojibake() first since a garbled separator (see above)
    otherwise defeats the split and leaves the raw, un-shortened title."""
    return re.split(r"\s*[-|–—]\s*", repair_mojibake(title), maxsplit=1)[0].strip()


def dedupe_system_name(cleaned_title: str, *names: str) -> str:
    """A page title that survives clean_title() can still just BE the system
    name — some sites put their own name first in the title ("Mozaic Design
    System - Breadcrumb"), which clean_title's "keep the first segment" rule
    can't distinguish from the normal case. Used wherever the system name is
    already shown right next to the title (component pages, system page
    lists), so it isn't shown a second time for nothing. Falls back to the
    original if stripping every candidate name would leave nothing."""
    for name in filter(None, names):
        if cleaned_title.strip().lower() == name.strip().lower():
            return ""
    return cleaned_title
