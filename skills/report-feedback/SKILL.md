---
name: report-feedback
description: Prepare privacy-scoped bug reports or feature requests for public GitHub and authenticated Claude Code feedback, with an explicit human gate before any GitHub write.
license: Apache-2.0
disable-model-invocation: true
---

# report-feedback

Prepare reports; do not freehand them. This skill is self-contained: classify the request, then
read the matching bundled template before drafting:

- [bug template](templates/bug.md)
- [feature-request template](templates/feature-request.md)

**Usage:** `/report-feedback [--via feedback|claude] <bug description | feature request | issue reference | affected product>`

Before rendering, resolve the selected route:

```bash
uv run --script "${CLAUDE_SKILL_DIR}/scripts/report_feedback_route.py" \
  [--via feedback|claude] "<bug description | feature request | issue reference | affected product>"
```

`uv` runs the bundled resolver and `gh` is needed only for the opt-in `claude` route. The resolver
uses the bundled `skill-cli-foundation` package. `--via` defaults to `feedback`; its enum validation
rejects any other route before rendering starts.

## Drafting process

1. Classify the report as a bug or feature request, select its bundled template, and search the
   intended public repository (when known) for an existing matching issue or discussion before
   drafting a new report. Refine or comment on an existing report when that is the appropriate
   outcome; do not create a duplicate.
2. If the project has its own tracking location, search it for a matching report and refine that
   record in place. Otherwise, ask the human to name a local record file, or keep the draft in the
   response until they choose one. Do not assume a particular project directory exists. Wherever a
   record is kept, it is the source of truth; never leave the only draft in a temporary file.
3. Build both destination-specific drafts from the selected template. The public draft is available
   for review, and the ready-to-copy `/feedback` block is always available for the human to paste.
   The selected route decides only whether a direct GitHub write may be proposed.
4. Run a redaction check before any public delivery. Remove session IDs, account/org identifiers,
   private telemetry, authentication material, and other sensitive context from the public draft;
   retain relevant private details only in the `/feedback` block. Record or return the check result
   with the draft so it can be shown in the GitHub write gate.

When `--via claude` could proceed, save the reviewed public render in a project-owned file the human
has named (for example, beside its tracking record). Pass that exact file to `gh` with `--body-file`;
do not create an untracked or temporary body file. Keep the private `/feedback` render only in the
project's access-controlled record, if any, and the human's chat response.

### `--via feedback` (default and recommended)

Return the template-derived `/feedback` block as the primary next step for the human to paste
manually. Do not invoke `/feedback`: it is an interactive Claude Code command. Do not run
`gh issue create` or `gh issue comment` on this route.

### `--via claude`

This is the explicit opt-in for a direct GitHub write. Render both drafts, apply the unchanged
GitHub write gate below, and only after human confirmation run `gh issue create` or
`gh issue comment` in this session. Still return the separately scoped `/feedback` block for
optional manual submission; selecting this route never suppresses it.

This public skill intentionally has no `codex` route. An installation may add its own local delivery
adapter, but that adapter is outside this skill and must preserve the GitHub write gate below.

## Required output

1. Render a public-GitHub draft using the unmarked sections and only the
   `<!-- github-only -->` section. **Hard rule: strip every HTML comment**—destination markers and
   all field instructions—from every rendered artifact: the public draft, any saved public body,
   the project's durable record, and the ready-to-copy `/feedback` block. Never render a
   `Public-report preflight` checklist. Never include session IDs, account/org identifiers, private
   telemetry, or `/feedback` contents in a public draft.
2. Render a ready-to-copy `/feedback` block using the unmarked sections and only the
   `<!-- feedback-only -->` section. Include the session ID and account/org identifier only if
   available and relevant. If a public issue or comments already exist, link them in this private
   block; never make the public report refer back to `/feedback`.
3. Require product version and a complete OS-version field in both renders. For macOS, include the
   release, build, Darwin kernel, and architecture when available. Do not leave the OS field blank.
4. Never render `(optional)` or another authoring annotation in a section header. Omit a section
   that does not apply to the report instead of publishing an annotated, empty section.

When a report presents two or more alternative designs or options, write `Either of the following:`
on its own line and follow it with a numbered list (`1.`, `2.`, and so on). Do not use inline
lettered alternatives such as `(a)` and `(b)`.

## Optional tracking adapter

Tracking is optional and never blocks preparation, review, or delivery. To use a project-specific
tracker, set `REPORT_FEEDBACK_TRACKING_ADAPTER` to the absolute path of a project-owned executable.
After the draft is recorded or returned, invoke that executable with the report type, selected route,
record path when one exists, and intended destination when known. For example:

```bash
"$REPORT_FEEDBACK_TRACKING_ADAPTER" \
  --report-kind bug --route feedback --record "<record-file>" --destination "OWNER/REPO"
```

Invoke an adapter only when that variable is configured. When it is not configured, skip tracking
silently. If a configured adapter fails, report that adapter failure separately, but do not bypass or
block the GitHub write gate.

## GitHub write gate

GitHub is public by default and a wrong repository or unredacted body cannot be safely recalled.
Before any `gh issue create` or `gh issue comment` command, show the human:

- exact owner/repository and its visibility;
- issue title or comment target;
- exact rendered Markdown body and every attachment; and
- the redaction check result.

Then stop. The human explicitly confirms that exact destination and content. Only then use the
reviewed file for `--via claude`, for example:

```bash
gh issue create --repo OWNER/REPO --title "TITLE" --body-file "<reviewed-body-file>"
```

or:

```bash
gh issue comment NUMBER --repo OWNER/REPO --body-file "<reviewed-body-file>"
```

Do not invoke `/feedback`: it is an interactive Claude Code command. Return the feedback render for
the human to paste manually on every route.
