"""
Reads a "Submit a design system" issue's body from stdin, runs the dry-run
crawl check, and writes dry_run_report.md for the workflow to post as a
comment. Also writes GITHUB_OUTPUT vars (full_name, start_url).
"""

import os
import sys

from dry_run_submission import run_dry_check
from parse_issue_form import parse_issue_form

# Issue form field label -> resources.py key, for optional submitter-provided hints.
OPTIONAL_RESOURCE_FIELDS = {
    "GitHub repository (optional)": "github",
    "Storybook URL (optional)": "storybook",
    "Figma file URL (optional)": "figma",
    "npm package (optional)": "npm",
}


def build_full_name(org: str, ds_name: str) -> str:
    if org and ds_name and org.strip().lower() != ds_name.strip().lower():
        return f"{org} — {ds_name}"
    return ds_name or org


def submitter_provided_resources(fields: dict) -> dict[str, list[str]]:
    resources = {}
    for label, key in OPTIONAL_RESOURCE_FIELDS.items():
        value = fields.get(label, "").strip()
        if value:
            resources[key] = [value]
    return resources


def main() -> None:
    body = sys.stdin.read()
    fields = parse_issue_form(body)

    org = fields.get("Organization name", "").strip()
    ds_name = fields.get("Design system name", "").strip()
    start_url = fields.get("Start URL", "").strip()
    full_name = build_full_name(org, ds_name)
    notify_email = fields.get("Email for approval notification (optional)", "").strip()

    if not full_name or not start_url:
        report = (
            "❌ Missing required fields — expected an organization/design system name and a "
            "start URL. Please edit the issue with the missing details."
        )
    else:
        report = run_dry_check(full_name, start_url)
        provided = submitter_provided_resources(fields)
        if provided:
            lines = ["", "### Submitter-provided (unverified until crawled)"]
            lines += [f"- **{key}**: {urls[0]}" for key, urls in provided.items()]
            report += "\n" + "\n".join(lines)

    with open("dry_run_report.md", "w") as f:
        f.write(report)

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"full_name={full_name}\n")
            f.write(f"start_url={start_url}\n")
            f.write(f"notify_email={notify_email}\n")


if __name__ == "__main__":
    main()
