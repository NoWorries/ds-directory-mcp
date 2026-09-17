"""
Appends an approved design system submission to systems.yaml. Reads the
issue body from stdin (same as run_submission_check.py) so the maintainer's
approval (adding the `approved` label) is all that's needed to trigger this.

Seeds the entry's resources from any submitter-provided hints — the next real
ingest.py run overwrites these with freshly auto-discovered ones regardless.
"""

import os
import re
import sys

from ingest import load_registry, save_registry
from parse_issue_form import parse_issue_form
from run_submission_check import submitter_provided_resources
from text_utils import full_name

# Belt-and-braces alongside submission-approve.yml passing this value through
# env: rather than interpolating it directly into a run: script — reject a
# submitted org/system name outright if it contains shell metacharacters or a
# newline (the latter could otherwise inject extra keys into $GITHUB_OUTPUT).
# A legitimate design system name never needs any of these.
DANGEROUS_NAME_CHARS = re.compile(r'[$;|&<>"\'\\\n\r]')


def reject_if_dangerous(name: str) -> None:
    if DANGEROUS_NAME_CHARS.search(name):
        sys.exit(
            f"Refusing to process submission: name {name!r} contains a character "
            "that isn't allowed in an organization/design-system name."
        )


def main() -> None:
    body = sys.stdin.read()
    fields = parse_issue_form(body)

    org = fields.get("Organization name", "").strip()
    ds_name = fields.get("Design system name", "").strip()
    start_url = fields.get("Start URL", "").strip()
    notify_email = fields.get("Email for approval notification (optional)", "").strip()

    reject_if_dangerous(org)
    reject_if_dangerous(ds_name)

    entries = load_registry()
    new_entry = {"organization": org, "design_system": ds_name, "start_urls": [start_url]}
    display_name = full_name(new_entry)

    if any(full_name(e) == display_name for e in entries):
        print(f"{display_name} already exists in systems.yaml — skipping.")
    else:
        resources = submitter_provided_resources(fields)
        if resources:
            new_entry["resources"] = resources
        entries.append(new_entry)
        save_registry(entries)
        print(f"Added {display_name} ({start_url}) to systems.yaml.")

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"full_name={display_name}\n")
            f.write(f"notify_email={notify_email}\n")


if __name__ == "__main__":
    main()
