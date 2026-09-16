"""
Builds suggest.html and report.html — real, no-login HTML forms replacing
the raw GitHub Issue Form links (SUBMISSION_URL/REPORT_ISSUE_URL in
page_shell.py used to be the only way to do either, and both required the
visitor to have their own GitHub account). Both forms POST as JSON to
netlify/functions/submit-form.js, which files the actual GitHub issue on a
bot account — the existing dry-run-crawl bot (submission-check.yml) and
report-issue label still work exactly as before, since the filed issue's
shape (labels + "### Field\\n\\nvalue" body) is unchanged.

Deploys as top-level pages, same as search.html — see the workflows' Netlify
deploy step and netlify.toml's pretty_urls.
"""

from __future__ import annotations

from pathlib import Path

from page_shell import FONT_LINK, TOKENS_CSS, routes_nav

SUGGEST_OUTPUT_FILE = Path(__file__).parent / "suggest.html"
REPORT_OUTPUT_FILE = Path(__file__).parent / "report.html"

FORM_CSS = """
  .form-page { max-width: 640px; }
  .form-intro { color: var(--text-muted); font-size: 0.95rem; line-height: 1.55; margin: 0 0 28px; }
  .field { margin-bottom: 18px; }
  .field label {
    display: block; font-size: 0.82rem; font-weight: 600; color: var(--text); margin-bottom: 5px;
  }
  .field .hint { font-size: 0.78rem; color: var(--text-faint); margin: 4px 0 0; }
  .field input[type="text"], .field input[type="url"], .field input[type="email"],
  .field textarea, .field select {
    width: 100%; box-sizing: border-box; padding: 9px 12px; font: inherit; font-size: 0.92rem;
    border: 1px solid var(--border); border-radius: 7px; background: var(--surface); color: var(--text);
  }
  .field input:focus, .field textarea:focus, .field select:focus {
    outline: 2px solid var(--accent); outline-offset: 1px;
  }
  .field textarea { min-height: 100px; resize: vertical; font-family: inherit; }
  .field-optional-label { color: var(--text-faint); font-weight: 500; }
  .field-row { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
  @media (max-width: 560px) { .field-row { grid-template-columns: 1fr; } }
  .honeypot { position: absolute; left: -9999px; width: 1px; height: 1px; overflow: hidden; }
  .submit-row { display: flex; align-items: center; gap: 14px; margin-top: 26px; }
  .submit-button {
    font: inherit; font-size: 0.92rem; font-weight: 600; padding: 10px 20px; border-radius: 8px;
    border: none; background: var(--accent); color: #fff; cursor: pointer;
  }
  .submit-button:hover { background: var(--accent-strong); }
  .submit-button:disabled { opacity: 0.6; cursor: default; }
  .form-status { font-size: 0.88rem; }
  .form-status.error { color: #dc2626; }
  .form-status.success { color: #16a34a; }
  .form-success-box {
    background: var(--surface-sunken); border: 1px solid var(--border); border-radius: 10px;
    padding: 20px 22px; margin-top: 8px;
  }
  .form-success-box h2 { font-size: 0.98rem; margin: 0 0 6px; }
  .form-success-box p { margin: 0; font-size: 0.88rem; color: var(--text-muted); }
  .form-success-box a { font-weight: 600; }
"""

# Shared client-side submit handling — reads every named form field, strips
# the ones left blank, POSTs as JSON, and swaps the form out for a success
# box linking to the filed issue (or an inline error, left submittable again).
FORM_JS = """
function wireForm(formEl, buildPayload) {
  const statusEl = formEl.querySelector(".form-status");
  const submitButton = formEl.querySelector(".submit-button");

  formEl.addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = buildPayload();
    if (payload.error) {
      statusEl.textContent = payload.error;
      statusEl.className = "form-status error";
      return;
    }

    submitButton.disabled = true;
    statusEl.textContent = "Submitting…";
    statusEl.className = "form-status";

    try {
      const res = await fetch("/.netlify/functions/submit-form", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload.data),
      });
      const result = await res.json();
      if (!res.ok || result.error) {
        statusEl.textContent = result.error || "Something went wrong — please try again.";
        statusEl.className = "form-status error";
        submitButton.disabled = false;
        return;
      }
      formEl.outerHTML = `
        <div class="form-success-box">
          <h2>Thanks — that's filed.</h2>
          <p>You can follow along at <a href="${result.issue_url}" target="_blank" rel="noopener">${result.issue_url}</a>.</p>
        </div>
      `;
    } catch (err) {
      statusEl.textContent = "Couldn't reach the server — please try again.";
      statusEl.className = "form-status error";
      submitButton.disabled = false;
    }
  });
}
"""


def render_suggest_page() -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Suggest a design system — Design Systems Directory</title>
{FONT_LINK}
<style>
{TOKENS_CSS}
{FORM_CSS}
</style>
</head>
<body>
{routes_nav("search")}
<div class="page form-page">
  <p class="eyebrow">Suggest a system</p>
  <h1>Suggest a design system</h1>
  <p class="form-intro">
    Know a publicly documented design system that isn't indexed yet? Add it here — a bot
    runs a quick trial crawl and a maintainer reviews it before it's added.
  </p>

  <form id="suggestForm">
    <div class="honeypot" aria-hidden="true">
      <label for="suggestWebsite">Leave this field empty</label>
      <input type="text" id="suggestWebsite" name="website" tabindex="-1" autocomplete="off">
    </div>

    <div class="field-row">
      <div class="field">
        <label for="organization">Organization name</label>
        <input type="text" id="organization" placeholder="e.g. Adobe">
      </div>
      <div class="field">
        <label for="designSystemName">Design system name</label>
        <input type="text" id="designSystemName" placeholder="e.g. Spectrum" required>
      </div>
    </div>

    <div class="field">
      <label for="startUrl">Start URL</label>
      <input type="url" id="startUrl" placeholder="https://spectrum.adobe.com/" required>
      <p class="hint">The main documentation/components page to start crawling from.</p>
    </div>

    <div class="field-row">
      <div class="field">
        <label for="githubUrl"><span class="field-optional-label">GitHub repository (optional)</span></label>
        <input type="url" id="githubUrl" placeholder="https://github.com/org/repo">
      </div>
      <div class="field">
        <label for="npmUrl"><span class="field-optional-label">npm package (optional)</span></label>
        <input type="url" id="npmUrl" placeholder="https://www.npmjs.com/package/...">
      </div>
      <div class="field">
        <label for="storybookUrl"><span class="field-optional-label">Storybook URL (optional)</span></label>
        <input type="url" id="storybookUrl" placeholder="https://org.github.io/repo/storybook">
      </div>
      <div class="field">
        <label for="figmaUrl"><span class="field-optional-label">Figma file URL (optional)</span></label>
        <input type="url" id="figmaUrl" placeholder="https://www.figma.com/file/...">
      </div>
    </div>

    <div class="field">
      <label for="notes"><span class="field-optional-label">Anything else we should know? (optional)</span></label>
      <textarea id="notes"></textarea>
    </div>

    <div class="field">
      <label for="notifyEmail"><span class="field-optional-label">Email for approval notification (optional)</span></label>
      <input type="email" id="notifyEmail" placeholder="you@example.com">
      <p class="hint">Only used to email you once this is approved and published — not stored anywhere else.</p>
    </div>

    <div class="submit-row">
      <button type="submit" class="submit-button">Submit for review</button>
      <span class="form-status"></span>
    </div>
  </form>
</div>

<script>
{FORM_JS}
  wireForm(document.getElementById("suggestForm"), () => {{
    const name = document.getElementById("designSystemName").value.trim();
    const startUrl = document.getElementById("startUrl").value.trim();
    if (!name || !startUrl) {{
      return {{ error: "Design system name and start URL are required." }};
    }}
    return {{
      data: {{
        type: "new-system",
        website: document.getElementById("suggestWebsite").value,
        organization: document.getElementById("organization").value.trim(),
        design_system_name: name,
        start_url: startUrl,
        github_url: document.getElementById("githubUrl").value.trim(),
        storybook_url: document.getElementById("storybookUrl").value.trim(),
        figma_url: document.getElementById("figmaUrl").value.trim(),
        npm_url: document.getElementById("npmUrl").value.trim(),
        notes: document.getElementById("notes").value.trim(),
        notify_email: document.getElementById("notifyEmail").value.trim(),
      }},
    }};
  }});
</script>
</body>
</html>
"""


def render_report_page() -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Report an issue — Design Systems Directory</title>
{FONT_LINK}
<style>
{TOKENS_CSS}
{FORM_CSS}
</style>
</head>
<body>
{routes_nav("search")}
<div class="page form-page">
  <p class="eyebrow">Report an issue</p>
  <h1>Report an issue with a design system</h1>
  <p class="form-intro">
    Flag a broken/moved link, a stale crawl, or a resource that doesn't belong to a system
    already in the directory — a maintainer looks into that system's entry directly.
  </p>

  <form id="reportForm">
    <div class="honeypot" aria-hidden="true">
      <label for="reportWebsite">Leave this field empty</label>
      <input type="text" id="reportWebsite" name="website" tabindex="-1" autocomplete="off">
    </div>

    <div class="field">
      <label for="systemName">Design system</label>
      <input type="text" id="systemName" placeholder="e.g. Adobe Spectrum" required>
    </div>

    <div class="field">
      <label for="pageUrl"><span class="field-optional-label">Link to the system's detail page (optional)</span></label>
      <input type="url" id="pageUrl" placeholder="https://<site>/systems/adobe-spectrum">
    </div>

    <div class="field">
      <label for="issueType">What's wrong?</label>
      <select id="issueType" required>
        <option value="">Choose one…</option>
        <option>Docs site URL is broken or redirects elsewhere</option>
        <option>Site has moved to a new URL</option>
        <option>Design system appears discontinued/archived</option>
        <option>Crawl looks incomplete or stale</option>
        <option>A linked resource (GitHub/npm/Storybook/etc) doesn't belong to this system</option>
        <option>Something else</option>
      </select>
    </div>

    <div class="field">
      <label for="details">Details</label>
      <textarea id="details" placeholder="What did you see, and what would you expect instead?" required></textarea>
    </div>

    <div class="submit-row">
      <button type="submit" class="submit-button">Submit report</button>
      <span class="form-status"></span>
    </div>
  </form>
</div>

<script>
{FORM_JS}
  const params = new URLSearchParams(window.location.search);
  if (params.get("system")) document.getElementById("systemName").value = params.get("system");
  if (params.get("url")) document.getElementById("pageUrl").value = params.get("url");

  wireForm(document.getElementById("reportForm"), () => {{
    const systemName = document.getElementById("systemName").value.trim();
    const issueType = document.getElementById("issueType").value;
    const details = document.getElementById("details").value.trim();
    if (!systemName || !issueType || !details) {{
      return {{ error: "Design system, issue type, and details are all required." }};
    }}
    return {{
      data: {{
        type: "report-issue",
        website: document.getElementById("reportWebsite").value,
        system_name: systemName,
        page_url: document.getElementById("pageUrl").value.trim(),
        issue_type: issueType,
        details: details,
      }},
    }};
  }});
</script>
</body>
</html>
"""


if __name__ == "__main__":
    SUGGEST_OUTPUT_FILE.write_text(render_suggest_page())
    REPORT_OUTPUT_FILE.write_text(render_report_page())
    print(f"Wrote {SUGGEST_OUTPUT_FILE} and {REPORT_OUTPUT_FILE}")
