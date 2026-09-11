import re


def clean_title(title: str) -> str:
    """"Button - Components - Atlassian Design" -> "Button". Site doc-page
    titles are almost always "<actual title> <separator> <site name...>" —
    the first segment is what's worth showing once we're already on that
    system's own page (repeating the system name there is just noise)."""
    return re.split(r"\s*[-|–—]\s*", title, maxsplit=1)[0].strip()
