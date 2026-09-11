import re


def parse_issue_form(body: str) -> dict:
    """Parses the markdown a GitHub issue form renders into an issue body:
    alternating '### Field label' headers and the submitter's answer beneath each."""
    parts = re.split(r"^### (.+)$", body, flags=re.MULTILINE)
    fields = {}
    for i in range(1, len(parts), 2):
        header = parts[i].strip()
        value = parts[i + 1].strip() if i + 1 < len(parts) else ""
        if value == "_No response_":
            value = ""
        fields[header] = value
    return fields
