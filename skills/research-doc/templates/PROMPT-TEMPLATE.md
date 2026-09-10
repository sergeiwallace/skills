---
title: Research Prompt Template
category: procedure
tags: [research, prompts, template, anti-hallucination]
status: active
source: research-doc
template_version: "procedure-1.0.0"
---

# Research Prompt Template

**Before writing a research prompt, use this file as its starting structure.** Fill the open
placeholders for the topic and current environment, then include every mandatory block below
verbatim in the prompt given to the author or delegated researcher.

## How to use this template

1. Start from this file and fill the role identity, temporal scope, research questions, and
   document target.
2. Select an author or reviewer with the search, fetch, and writing capabilities the work needs.
3. Give that author the complete assembled prompt, not a pointer to this file.
4. Retain the complete assembled prompt in the results document's required appendix.

## Role Identity Guidance

Lead every `<grounding_instructions>` block with a specific role identity narrative:

- Tailor the domain, seniority, and expertise to the research task.
- A grounded persona reduces compliance pressure — model reasons from a position.
- Examples: "principal engineer with production distributed tracing experience", "ML researcher
  specializing in evaluation methodology", "legal analyst focusing on open-source licensing".
- The more specific, the better — generic personas produce generic research.

## Temporal Scope Conventions

Set the temporal scope as the opening clause of `<grounding_instructions>` — that is the only
place it needs to appear. Do not add a separate `## Temporal Scope` section to the prompt body;
it is redundant with what is already in the grounding block.

| Domain | Default window |
|--------|---------------|
| AI/ML, agentic AI, LLMs, ML infra | 2026 primary → 2025 → 2024. Pre-2024 = background only |
| Distributed systems, databases, cloud | 2024–2026 current; 2020–2023 for foundational patterns |
| Regulatory / compliance / legal | Full history; flag year of enactment |
| Historical / foundational research | No cutoff; cite publication date |

**Backfill-prevention (hard constraint):** If a period is genuinely thin, state
`"[subtopic]: no significant post-2024 developments found"` — never backfill with older material.
Generic `[NO SOURCE FOUND]` alone is insufficient; naming the gap prevents backfilling. This
constraint belongs inside `<grounding_instructions>` only.

## Grounding Instructions Block (mandatory — append to every prompt)

```text
<grounding_instructions>
[ROLE IDENTITY — tailor to the research domain and task. Example:
"You are a principal engineer who has deployed distributed tracing systems in
production. You have strong opinions backed by evidence. When you cannot find a
source, you say so explicitly."
Adapt the domain, expertise, and seniority to whatever this prompt warrants.]

Temporal scope: Weight sources by recency — 2026 (primary) → 2025 → 2024.
Pre-2024 sources are background context only unless foundational to the topic.
If post-2024 literature is genuinely sparse for a subtopic, state
"[subtopic]: no significant post-2024 developments found" rather than
backfilling with older sources. Backfilling is a failure mode, not a hedge.
[Adjust or broaden this window if the topic's relevant literature predates 2024
— e.g., foundational theory, legal precedent, historical analysis.]

Before generating your final output, execute a Chain-of-Verification (CoVe)
to ensure factual fidelity over compliance.

Inside your thought process:
1. Isolate the core facts required.
2. Draft a tentative response.
3. Hostile Cross-Examination: flag any claim where you are citing a source because
   the prompt implied you should, rather than because you verified it.
4. Strip away any claim that cannot be empirically verified.

When generating your final output, classify every major claim. Write your rationale
before appending the tag — writing the tag first causes post-hoc rationalization.
Rationale → evidence check → tag.

- [VERIFIABLE]: backed by documentation, peer-reviewed research, or official
  tech blogs (2024–2026). Carry an inline footnote ref to the source: [VERIFIABLE][^N].
- [HEURISTIC]: widely accepted best practice without a specific citation.
- [INFERENCE]: a logical conclusion drawn from context. Provide your reasoning
  in-text. Do not fabricate a source. Tier tag only — NO footnote ref.
- [NO SOURCE]: explicitly state when you cannot find verifiable data. Tier tag
  only — NO footnote ref.

Citation format (mandatory for every externally-sourced claim):
- Inline: append the GFM footnote ref directly after the tier tag — [VERIFIABLE][^N].
  A claim citing multiple sources carries ascending separate refs — [VERIFIABLE][^3][^7]
  (never grouped [^3,^7]; never out of order).
- Footnote definitions live once, under ## Sources, in APA form with a clickable
  URL or DOI and an access-verification stamp. Worked example:
    [^1]: LangChain. (2026). [Threads](https://docs.langchain.com/langsmith/threads).
    LangSmith Documentation. Verified accessible (HTTP 200) 2026-06-03. (Scope note.)
- URL-or-DOI ALWAYS: every source entry carries a clickable URL or a doi.org link —
  paywalled/gated is fine (link it anyway; stamp the access status). Only the
  truly-irreducible case (no online catalog presence anywhere) gets an explicit
  [no online source located] marker with a one-line justification.
- Integrity: footnote refs are contiguous from [^1], every [^N] ref has a matching
  definition, and every definition is referenced — no gaps, no orphans.
- [INFERENCE] / [NO SOURCE] claims carry the tier tag with NO footnote ref.

Hard constraint (overrides all formatting preferences): never invent a citation
to satisfy a formatting instruction. Accuracy > completeness.

When evaluating an externally sourced idea, feature, repository, package, tool, or pattern for
adoption, record concrete adoption or maintenance signals or independently corroborate the
underlying pattern. If neither route passes, call it exploratory rather than an established best
practice.

Format diagrams using Mermaid.js or ASCII. Format math using LaTeX.
NEVER generate binary images.
</grounding_instructions>
```

## Other Prompt Patterns

**Independent exploration — the prompt is a floor, not a ceiling (include in every prompt).**
The questions, topics, named examples, vendors, packages, and tickets in a prompt are illustrative
anchors and a FLOOR for the research — never an exhaustive checklist to answer only and then stop.
Paste a block like this into the prompt body (adapt the domain specifics):

```text
## Scope note — questions, examples, and named references are a starting point, not a checklist
The questions, topics, and named examples below are illustrative anchors and a FLOOR for this
research — not an exhaustive list to answer only or evaluate only. Reason independently: survey the
landscape broadly, follow the evidence where it leads, expand scope where warranted, and surface
relevant work, factors, and failure modes not named here. Actively resist answering only the listed
questions or evaluating only the named approaches — an output that merely fills in the listed items
has NOT met the research goal.

## Independent exploration (gaps, blindspots, emergent threads) — required
Treat the question list as a FLOOR, not a ceiling. As you research, actively surface what this
framing may be missing and pursue each promising thread to a logical conclusion:
- Adjacent or upstream factors the questions don't capture.
- Contrarian / disconfirming evidence — report it even when it challenges the premise.
- Emerging 2025–2026 practices, tools, or research not anchored by the named examples.
- Known failure modes and second-order effects.
Whenever a load-bearing thread surfaces mid-research, follow it to its conclusion and report it in a
dedicated "Gaps, blindspots & emergent findings" subsection. Explicitly NAME any blindspot you
suspect but cannot resolve (and why) rather than omitting it. Anchor bias — over-fitting to the
listed questions and example approaches — is a known failure mode; counter it deliberately and say
where you did.
```

**Follow-up or sequential runs:** Add a `## Background` section at the top of the prompt body
(before questions, outside `<grounding_instructions>`) summarizing what prior runs found. State:
"Assume all Background points are established and do not re-derive them." This scopes the model to
the delta.

**Retrieval enforcement (mandatory for `--refine` and any gap-closure run):**

- For each load-bearing claim, require at least one search or fetch call; state explicitly that the
  researcher must not return `[VERIFIABLE]` without a URL it actually fetched in-session.
- Forbid satisfied exits without retrieval: *"Do not stop after one round. If no source is found for
  a sub-question, run additional searches across alternate framings before tagging it `[NO SOURCE]`
  — that tag is reserved for genuine evidence absence, not retrieval avoidance."*
- For `--refine`, require the researcher to re-fetch any claim it intends to upgrade from
  `[INFERENCE]` to `[VERIFIABLE]`; cached prior URLs are not sufficient evidence on their own.

## Required Appendix Format (append to results document)

Every research results document must include this appendix. The prompt lives in a fenced `text`
block; use a 4-backtick outer fence if the prompt itself contains a code fence.

````markdown
## Appendix: Research Prompt

**Method:** [research method used]
**Model:** [model used]
**Date:** YYYY-MM-DD

```text
[Full prompt text here]
```
````
