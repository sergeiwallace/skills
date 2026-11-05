<!-- markdownlint-disable MD013 -->

# AI Harness public skills

A curated public catalog of portable Agent Skills for planning, documentation, repository work,
presentations, meetings, and day-to-day agent workflows. Install one skill at a time with
[GitHub's Agent Skills CLI](https://agentskills.io/specification), or browse the source before
deciding.

> **Provenance and maturity:** This repository is a generated public projection of a privately
> maintained canonical catalog. Only explicitly approved skills and their resolved dependencies
> are copied here. Publishing is active, but the catalog and preview installation tooling may
> continue to evolve. Each projection records its source revision and file digests in
> [`PUBLIC-PROJECTION-PROVENANCE.json`](PUBLIC-PROJECTION-PROVENANCE.json).

## Choose and install one skill

The primary supported path is [GitHub's Agent Skills CLI](https://agentskills.io/specification),
which discovers skills directly from this repository's `skills/*/SKILL.md` layout — no
marketplace registration step required:

```bash
gh skill install sergeiwallace/skills spec --agent claude-code --scope user
```

Invoke the installed skill literally in Claude Code:

```text
/spec describe how to add a health-check endpoint
```

`/spec` should return an implementation plan and wait for your approval before changing anything.
Replace `spec` with any name in the catalog below, and `--agent claude-code` with your own host
from `gh skill install --help` (Cursor, Codex, Gemini CLI, and many others are supported).

Preview a skill before installing it:

```bash
gh skill preview sergeiwallace/skills spec
```

### Install every skill as one Claude Code plugin

This repository is also a Claude Code plugin marketplace named `skills`, with one plugin, also
named `skills`, that carries every skill in the catalog. Install it from inside Claude Code
(version 2.1.275 or later):

```text
/plugin install skills --marketplace sergeiwallace/skills
```

Or from your shell:

```bash
claude plugin marketplace add sergeiwallace/skills
claude plugin install skills@skills
```

The plugin's version is the source commit recorded in
[`PUBLIC-PROJECTION-PROVENANCE.json`](PUBLIC-PROJECTION-PROVENANCE.json), so
`claude plugin update skills@skills` picks up each new projection.

### Other hosts

For other hosts, follow the host's own [Agent Skills](https://agentskills.io/specification)
installation instructions and review the skill's requirements first. Some skills use repository-level shared packages or
assets, so copying only a `skills/<name>/` directory is not a universal installation method.

## Included skills

The descriptions below come from each skill's `SKILL.md` frontmatter. Every public skill is licensed
under Apache-2.0; requirements are shown only when they materially affect use.

<!-- GENERATED:CATALOG:START -->

| Skill | Description | Requirements | Install |
| --- | --- | --- | --- |
| [`clean-task-panel`](skills/clean-task-panel/) | Remove all completed tasks from the CC TUI task panel. | Claude Code task-panel tools | `gh skill install sergeiwallace/skills clean-task-panel --agent claude-code --scope user` |
| [`explain`](skills/explain/) | Build a grounded mental model of a design or plan document, issue, code target, or technical concept. Use for explanation, refresher, rationale, comparison, trace, consequence, or learning-mode requests where faithful relationships matter more than compression. | — | `gh skill install sergeiwallace/skills explain --agent claude-code --scope user` |
| [`graphic`](skills/graphic/) | Provider-neutral pipeline for small graphic assets (icons, favicons, simple marks) — inspect/brief, route to reuse or constrained SVG primitives, sanitize+normalize through an SVG allowlist and SVGO, render a contact sheet, and gate on human selection before packaging. Phase one only; vector generation, tracing, and raster generation are deliberately out of scope. | Node.js and npm | `gh skill install sergeiwallace/skills graphic --agent claude-code --scope user` |
| [`jira`](skills/jira/) | Jira operations — create issues, search, update, link, comment, sprint overview | Jira MCP connector and access | `gh skill install sergeiwallace/skills jira --agent claude-code --scope user` |
| [`meeting-analysis`](skills/meeting-analysis/) | Turn a meeting's sources (recording, vendor transcript, notes export, chat) into a template-conformant analysis doc, either from a zip or directory you give it or, with --retrieve, by first landing a named Microsoft Teams meeting's artefacts through the teams skill. Cross-checks the vendor transcript against an independent local ASR pass to recover mangled product names, reads screen-share frames natively, consolidates every action item, and records what did not work. Use for "analyze this meeting", "write up the standup" or "retrieve and analyze today's stand-up". | `ffmpeg`, `ffprobe`, and `pdftotext`; `uv` | `gh skill install sergeiwallace/skills meeting-analysis --agent claude-code --scope user` |
| [`presentation`](skills/presentation/) | Author a local, review-gated Slidev presentation from a normalized event brief and optional research. | Node.js, npm, and Python 3 | `gh skill install sergeiwallace/skills presentation --agent claude-code --scope user` |
| [`report-feedback`](skills/report-feedback/) | Prepare privacy-scoped bug reports or feature requests for public GitHub and authenticated Claude Code feedback, with an explicit human gate before any GitHub write. | `uv`; `gh` for the optional GitHub route | `gh skill install sergeiwallace/skills report-feedback --agent claude-code --scope user` |
| [`research-doc`](skills/research-doc/) | Write a self-contained, template-conformant research document with Chain-of-Verification grounding, adversarial review, live citation validation, a provenance ledger, and claim-tier tags. Use for a scoped "research X and write it up" request, inline or through an installation-provided research adapter. | `uv` and a live research/fetch capability | `gh skill install sergeiwallace/skills research-doc --agent claude-code --scope user` |
| [`spec`](skills/spec/) | Present an implementation plan for review before making changes | — | `gh skill install sergeiwallace/skills spec --agent claude-code --scope user` |
| [`summarize`](skills/summarize/) | Faithfully compress caller-selected documents, issues, code, topic packets, or explicit session batches for a declared reader and purpose. | — | `gh skill install sergeiwallace/skills summarize --agent claude-code --scope user` |
| [`sync-git`](skills/sync-git/) | Safely synchronize safe uncommitted work and local/remote branch state across selected repositories and worktrees; discard and gitignore recommendations always require human approval. | Git and `uv` | `gh skill install sergeiwallace/skills sync-git --agent claude-code --scope user` |
| [`teams`](skills/teams/) | Read Microsoft Teams chats, meeting threads, transcripts (.vtt), recordings and Loop notes with the Teams web client's own tokens from a token provider you plug in, land a named meeting's artefacts (the latest, or a date and time) in a dated folder from a named-meetings config, read a meeting Recap's AI summary from a Teams tab you keep open, and send a Teams message only after you approve its exact text and destination. Use for "read the team chat", "get the stand-up transcript" or "download the meeting recording and notes". | `uv`; a Teams token broker; Chromium for the PDF and Recap steps | `gh skill install sergeiwallace/skills teams --agent claude-code --scope user` |
| [`teams-message-draft`](skills/teams-message-draft/) | Draft or revise a Microsoft Teams message in the user's own voice, in a rolling drafts doc per project, after reading the doc's voice guide and every AI-draft-versus-sent comparison in full. When the user says they sent a message, ask for a screenshot, transcribe it, write down what changed and grow the voice guide with citations; a script then archives the sent or dropped entry and validates the doc. Drafts into a file only and never posts. Use for "draft a Teams reply to the review thread" or "I sent the release note, here is the screenshot". | Python 3.11+ and Git | `gh skill install sergeiwallace/skills teams-message-draft --agent claude-code --scope user` |

<!-- GENERATED:CATALOG:END -->

## How skills work

Each skill is a directory centered on a `SKILL.md` file, with optional scripts, templates,
references, and assets beside it. The [Agent Skills specification](https://agentskills.io/specification)
defines the portable structure and metadata that `gh skill install` and other Agent
Skills-compatible tooling discover automatically. A skill with repository-level shared package
dependencies (see [`packages/`](packages/)) resolves them as part of its own closure — install
the skill itself rather than copying files by hand.

Skill instructions do not override your responsibility to review proposed commands, file access,
network access, credentials, or external writes. Host support also does not imply that every
optional external tool used by every skill is installed.

## Trust and safety

**Curated does not mean guaranteed safe.** Skills can contain instructions and executable scripts,
and dependencies can change risk. Before installation, inspect the linked skill directory and its
generated plugin, confirm that you trust this repository and the recorded source revision, and
check requested tools and permissions. Use least-privilege credentials and keep secrets out of
prompts, fixtures, issue bodies, logs, and generated artifacts.

Public candidates are reviewed for portability, private references, dependency closure, license
consistency, executable content, secrets, compatibility, and relevant evals before projection.
Those gates reduce known risks; they are not a security certification. The authoritative projection
record is [`PUBLIC-PROJECTION-PROVENANCE.json`](PUBLIC-PROJECTION-PROVENANCE.json), and each
generated plugin carries its own `RELEASE-PROVENANCE.json`.

For a suspected vulnerability or malicious skill, use GitHub's private
[security advisory flow](https://github.com/sergeiwallace/skills/security/advisories/new). Do not
disclose sensitive details in a public issue.

## Contributing

This public repository is a generated projection, so a direct edit to projected skill content may
be overwritten. For a new skill, behavioral change, or catalog correction, open a
[public proposal](https://github.com/sergeiwallace/skills/issues/new) describing the use case,
portable behavior, requirements, and license/provenance.

Maintainers review accepted changes in the private canonical source, add eligible skills to the
private `public-candidates.toml` allowlist, run the portability, licensing, security, secret,
dependency, and eval gates, and then regenerate this repository. Public eligibility is an explicit
review decision; adding a folder or opening a proposal does not add it to the catalog automatically.

Use the [issue tracker](https://github.com/sergeiwallace/skills/issues) for ordinary bugs and
questions. Security reports follow the private route above.

## License

Apache License 2.0 for the projected skill content, code, and generated plugins. See
[LICENSE](LICENSE) and [NOTICE](NOTICE). Source revisions, dependency closure, and file digests are
recorded in [`PUBLIC-PROJECTION-PROVENANCE.json`](PUBLIC-PROJECTION-PROVENANCE.json).
