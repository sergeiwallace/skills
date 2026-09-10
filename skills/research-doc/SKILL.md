---
name: research-doc
description: Write a self-contained, template-conformant research document with Chain-of-Verification grounding, adversarial review, live citation validation, a provenance ledger, and claim-tier tags. Use for a scoped "research X and write it up" request, inline or through an installation-provided research adapter.
license: Apache-2.0
---

# research-doc

Write a research document using the bundled prompt template and document stub. The methodology
prioritizes factual grounding over coverage: adversarial critics, live citation validation,
claim-to-quote-to-verdict provenance, Chain-of-Verification (CoVe), and explicit claim tiers.

## Invocation and optional integrations

**Usage:** `/research-doc <topic | path-to-research-doc> [--model <model-preference>] [--inline] [--refine]`

- `--inline` writes the document in the driving session. It is always available and is the default
  when no delegation adapter is configured.
- `--model` records a model preference for the current environment or its optional delegation
  adapter. Use only a value supported by that installation.
- `--refine` updates an existing document. For a bare topic, first look for a substantively related
  document in the project locations available to the current installation; refine it instead of
  creating a near-duplicate when one exists.

Before authoring, this bundled resolver can validate the documented options and emit their JSON
envelope. It performs no research, routing, or file creation:

```sh
uv run --script "${CLAUDE_SKILL_DIR}/scripts/research_doc_resolve.py" \
  "<topic-or-path>" [--model <model-preference>] [--inline] [--refine]
```

### Optional tracking adapter

Tracking is optional and never blocks research. To connect an installation's tracker, set
`RESEARCH_DOC_TRACKING_ADAPTER` to the absolute path of a project-owned executable. Invoke it only
when configured, after the research document is prepared or returned:

```sh
"$RESEARCH_DOC_TRACKING_ADAPTER" \
  --topic "<topic>" --document "<document-path>" --mode "author|refine"
```

When the variable is unset, skip tracking silently. If a configured adapter fails, report that
failure separately without discarding or blocking the research document.

### Optional delegation adapter

An installation may set `RESEARCH_DOC_DELEGATION_ADAPTER` to an absolute path for its own
delegation route. This skill does not prescribe a provider or command. A delegated researcher must
be able to search and fetch sources, receive the complete assembled prompt, apply the retrieval and
provenance rules below, and write the named document target.

Invoke the adapter only when it is configured and inline authoring was not requested. Give it the
topic, existing document target, assembled prompt, selected model preference, and mode; for example:

```sh
"$RESEARCH_DOC_DELEGATION_ADAPTER" \
  --topic "<topic>" --target "<document-path>" --prompt-file "<assembled-prompt-file>" \
  --model "<model-preference>" --mode "author|refine"
```

The adapter is outside this skill and must not weaken the prompt or output checklist. If it is
unset, author inline. If it fails, report its failure and use inline authoring rather than assuming
another route exists.

## Find or scaffold the document

For a bare topic, search the current project and any project locations your installation makes
available for `docs/research/*.md` titles, tags, filenames, and content. Refine a substantively
related document in place; otherwise create a new `docs/research/<slug>.md` in the project that
owns the topic.

Always scaffold a new document from the bundled stub, never from a blank file:

```sh
cp "${CLAUDE_SKILL_DIR}/templates/DOC-STUB.md" "docs/research/<slug>.md"
```

Fill the frontmatter and write in the stub's required section order. The bundled prompt template is
`${CLAUDE_SKILL_DIR}/templates/PROMPT-TEMPLATE.md`; open both bundled files from disk at the start
of every run rather than relying on a cached conversation copy. The stub supplies the document
structure, region markers, table-of-contents discipline, and the required research-prompt appendix.

If your project has an equivalent convention for document ownership, landing worktree changes, or
tracking progress, follow it. Such conventions are optional integrations, not prerequisites for
this research workflow.

## Capabilities and method

Use native `WebSearch` and `WebFetch` tools when they are available. Otherwise use the search and
fetch capability provided by the current environment. The tool names may differ, but the retrieval,
validation, and provenance requirements below do not.

### 1. Assemble the prompt from the bundled template

Open `${CLAUDE_SKILL_DIR}/templates/PROMPT-TEMPLATE.md` and reproduce in full every block it marks
mandatory. Tailor only its open placeholders, such as role identity, temporal window, topic, and
questions. A prompt that merely points a researcher at a file is insufficient: inline and delegated
research alike must physically contain the CoVe anchor, temporal guidance, tier and citation rules,
independent-exploration block, retrieval-enforcement directives, and appendix format.

### 2. Researcher fan-out and retrieval floor

Use at least three distinct search framings: mainstream or consensus, contrarian or minority,
adjacent-field, and recency-first when the topic is current. Search, fetch the most relevant pages,
cross-reference each load-bearing claim across independent sources, and pursue disconfirming and
adjacent evidence. Continue until coverage is adequate, not until the first defensible source.

Never conclude a section with zero retrieval. If a claim would come from memory, search or fetch
first. Fetch at least one live source for every load-bearing claim tagged `[VERIFIABLE]`.

### 3. Adversarial critic pass

After drafting, run explicit passes from five perspectives:

1. **conventional** — does it align with or contradict mainstream consensus?
2. **contrarian** — what are the strongest counterarguments or minority positions?
3. **historical** — what prior art or earlier solutions are missing?
4. **adjacent** — what can neighboring fields add?
5. **skeptic** — demand evidence for every claim; every finding without a direct citation is suspect.

Record high, medium, and low findings. Resolve or explicitly concede every high and medium finding.
Run another round when critics find high-severity gaps or the draft barely grows.

### 4. Citation validation and access failures

Fetch every citation. Never accept a citation from model memory alone. Classify access failures:

| Failure | Meaning | Action |
|---|---|---|
| DNS failure or malformed URL | likely fabricated | downgrade hard and flag loudly |
| 404 or 410 | dead | remove from `[VERIFIABLE]`; try the Wayback availability API |
| 401 or 403 | paywall or WAF | keep and flag; use the bot-block ladder |
| timeout or 5xx | transient | keep and flag; retry once |

For scholarly claims, check metadata services such as Crossref or OpenAlex before trusting a
publisher page that returns 403. For a blocked or JavaScript-rendered source, first confirm that
the work exists through metadata, then try a full-header HTTP request with a real browser user
agent, and finally use a logged-in browser session when one is available. Never call a source dead
without completing the applicable ladder.

Stamp every source `Verified accessible (HTTP <code>) <date>` or explicitly record
`[no online source located]`; never silently drop a citation.

### 5. Provenance ledger

For every claim tagged `[VERIFIABLE]`, add a row under `## Appendix: Provenance Ledger`:

| Claim | Source URL | Verbatim quote (from the fetched page) | Verdict | Live? |
|---|---|---|---|---|

- The quote must literally appear in fetched text; check after whitespace normalization rather
  than trusting a self-report.
- The URL must have been fetched during this research session, not recalled from memory.
- Make a second entailment pass: `SUPPORTED`, `PLAUSIBLE`, `UNVERIFIABLE`, or `CONTRADICTED`.
  Default to `UNVERIFIABLE` when uncertain.
- Tag a claim `[VERIFIABLE]` only when the quote is present, the entailment verdict is
  `SUPPORTED`, and the URL is live.

### 6. Synthesis and tier tags

Write the document in the stub's section order. Citations are GFM footnotes, never inline URLs:
`[VERIFIABLE][^N]` directly after the tag, with ascending footnotes and one APA-shaped `[^N]:`
definition per URL under `## Sources`. Keep gaps and open questions explicit.

Write the rationale before applying a tier to every major claim:

- `[VERIFIABLE]` — backed by documentation, peer-reviewed research, or an official source in the
  stated window. Carries `[^N]`.
- `[HEURISTIC]` — a widely accepted practice without a specific citation. No footnote.
- `[INFERENCE]` — a logical conclusion from context; state the reasoning in text. No fabricated
  source and no footnote.
- `[NO SOURCE]` — an explicit admission that no verifiable data was found. No footnote.

## Prompt checklist

- [ ] Opened both bundled templates from disk and reproduced every mandatory prompt block verbatim.
- [ ] Tailored only open placeholders; no mandatory block is summarized, replaced, or left as a
      pointer to a file.
- [ ] A delegation adapter, if used, receives the fully assembled prompt rather than a file path.
- [ ] The prompt requires a fetch for every load-bearing `[VERIFIABLE]` claim and prohibits that
      tag without a URL actually fetched in this session.
- [ ] The prompt prohibits stopping without retrieval. If a sub-question has no source, it requires
      additional searches using alternate framings before `[NO SOURCE]` is used.
- [ ] For refinement, the prompt requires re-fetching any claim upgraded from `[INFERENCE]` to
      `[VERIFIABLE]`; cached URLs alone are insufficient.

## Output checklist

- [ ] Scaffolded from the bundled `DOC-STUB.md` rather than a blank file; frontmatter is filled.
- [ ] Required region markers and table of contents are present; every table-of-contents anchor resolves.
- [ ] Every `[VERIFIABLE]` claim has a live-checked footnote and a provenance-ledger row with a
      literally present quote.
- [ ] The failure taxonomy was applied to unreachable sources; scholarly claims have a metadata
      cross-check when relevant; no citation was silently dropped.
- [ ] The five-perspective critic pass ran; high and medium findings were resolved or conceded.
- [ ] `## Appendix: Research Prompt` contains method, model, date, and the complete prompt text.
- [ ] Three to five citations were re-opened and confirmed to support their claims.
- [ ] Diagrams use Mermaid or ASCII and math uses LaTeX; no binary images were generated.
