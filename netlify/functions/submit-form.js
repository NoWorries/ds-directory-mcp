// Files a GitHub issue on behalf of a visitor who has no GitHub account of
// their own — the "Suggest a system" and "Report an issue" pages (see
// generate_forms.py) POST here instead of linking to a GitHub Issue Form
// directly. Uses a bot PAT (ISSUE_BOT_TOKEN, set in Netlify's env vars, never
// committed) so the visitor never touches GitHub at all.
//
// The issue body is built as "### Field label\n\nvalue" blocks — the exact
// shape parse_issue_form.py expects from a real GitHub Issue Form submission
// — and the label matches what submission-check.yml filters on
// (design-system-submission), so the existing dry-run-crawl bot picks these
// up identically to one submitted the old way. No separate wiring needed.

const REPO = process.env.GITHUB_REPO || "NoWorries/ds-directory-mcp";
const MAX_FIELD_LENGTH = 2000;

function buildIssueBody(fields) {
  return fields
    .map(([label, value]) => `### ${label}\n\n${value ? value : "_No response_"}`)
    .join("\n\n");
}

function clean(value) {
  return String(value || "").trim().slice(0, MAX_FIELD_LENGTH);
}

function badRequest(message) {
  return new Response(JSON.stringify({ error: message }), {
    status: 400,
    headers: { "content-type": "application/json" },
  });
}

export default async (request) => {
  if (request.method !== "POST") {
    return new Response("Method not allowed", { status: 405 });
  }

  let payload;
  try {
    payload = await request.json();
  } catch {
    return badRequest("Invalid request body.");
  }

  // Honeypot: a hidden field real visitors never fill in (see generate_forms.py) —
  // any bot that blindly fills every field trips it, and we silently pretend
  // success rather than telling it what worked.
  if (clean(payload.website)) {
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  }

  const token = process.env.ISSUE_BOT_TOKEN;
  if (!token) {
    return new Response(JSON.stringify({ error: "Form submission isn't configured yet — missing ISSUE_BOT_TOKEN." }), {
      status: 500,
      headers: { "content-type": "application/json" },
    });
  }

  let title;
  let label;
  let fields;

  if (payload.type === "new-system") {
    const org = clean(payload.organization);
    const name = clean(payload.design_system_name);
    const startUrl = clean(payload.start_url);
    if (!name || !startUrl) {
      return badRequest("Design system name and start URL are required.");
    }
    if (!/^https?:\/\//i.test(startUrl)) {
      return badRequest("Start URL must be a full http(s) URL.");
    }
    title = `[New design system] ${org ? org + " — " : ""}${name}`;
    label = "design-system-submission";
    fields = [
      ["Organization name", org],
      ["Design system name", name],
      ["Start URL", startUrl],
      ["GitHub repository (optional)", clean(payload.github_url)],
      ["Storybook URL (optional)", clean(payload.storybook_url)],
      ["Figma file URL (optional)", clean(payload.figma_url)],
      ["npm package (optional)", clean(payload.npm_url)],
      ["Anything else we should know? (optional)", clean(payload.notes)],
      ["Email for approval notification (optional)", clean(payload.notify_email)],
    ];
  } else if (payload.type === "report-issue") {
    const systemName = clean(payload.system_name);
    const issueType = clean(payload.issue_type);
    const details = clean(payload.details);
    if (!systemName || !issueType || !details) {
      return badRequest("System name, issue type, and details are required.");
    }
    title = `[System issue] ${systemName}`;
    label = "system-issue";
    fields = [
      ["Design system", systemName],
      ["Link to the system's detail page", clean(payload.page_url)],
      ["What's wrong?", issueType],
      ["Details", details],
    ];
  } else {
    return badRequest("Unknown form type.");
  }

  let response;
  try {
    response = await fetch(`https://api.github.com/repos/${REPO}/issues`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "ds-directory-mcp-forms",
      },
      body: JSON.stringify({ title, body: buildIssueBody(fields), labels: [label] }),
    });
  } catch (err) {
    return new Response(JSON.stringify({ error: "Couldn't reach GitHub — please try again." }), {
      status: 502,
      headers: { "content-type": "application/json" },
    });
  }

  if (!response.ok) {
    return new Response(JSON.stringify({ error: "GitHub rejected the submission — please try again later." }), {
      status: 502,
      headers: { "content-type": "application/json" },
    });
  }

  const issue = await response.json();
  return new Response(JSON.stringify({ ok: true, issue_url: issue.html_url }), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
};

export const config = { path: "/.netlify/functions/submit-form" };
