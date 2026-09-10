---
title: "[FILL: Series] Standup Update - YYYY-MM-DD"
category: meeting-notes
tags: [meeting, standup, standup-update, progress-report, "[FILL: project]"]
status: draft
date: YYYY-MM-DD
source: "[FILL: tracker key, e.g. PROJ-123]"
meeting_type: standup-update
window: "[FILL: YYYY-MM-DDTHH:MMZ to YYYY-MM-DDTHH:MMZ]"
sources:
  - "[FILL: each source, specific enough that someone else could re-run it: which account, which region, which date]"
template_version: "standup-update-2.3.0"
---

<!-- WHICH KIND OF DOCUMENT IS THIS? This template is for the PRE-MEETING PROGRESS REPORT: written
     BEFORE the meeting, from our own repository (git history, the task store, audit output), with
     an evidence tier on every claim. Its slug is `<series>-standup-update-<date>`.

     If you are writing up a meeting that has already happened, from its transcript, notes export
     and recording, you want a different template and a different slug: `../TEMPLATE.md` and
     `<series>-standup-analysis-<date>`. The two are not interchangeable. The discriminator is whether
     a recording exists to analyze.

     Full convention and directory layout: README.md one level up. -->

# [FILL: Series] Standup Update - YYYY-MM-DD

**Status:** DRAFT

**Created:** YYYY-MM-DD

<!-- doc:region name="header" kind="replaceable" -->

**Window**: [FILL] to [FILL].

**Written from**: [FILL: git history, the task store, read-only AWS inspection, audit output], before
the meeting.

**Evidence tiers**: every claim below is tagged **executed** (something was run and observed),
**read** (code or a file was read), **asserted** (recorded somewhere, not re-verified), or
**blocked** (with what unblocks it and who owns that).

<!-- /doc:region name="header" -->

## Table of Contents

<!-- List EVERY `## ` heading and every MEANINGFUL `### ` heading in the real doc, with GitHub-style
     anchors (lowercase, spaces to hyphens, punctuation stripped) so they navigate in-window, in
     both the VS Code preview (including Remote-SSH) and on GitHub. Include a `###` when it has
     child headings, its body runs past roughly 8-10 lines, or a reader would plausibly jump
     straight to it.

     HAND-MAINTAINED, so it drifts the moment you add a heading without updating it. This repo has
     no TOC checker. Until something gates it here, re-read this list against the headings before
     shipping. -->

- [Blockers](#blockers)
- [TL;DR](#tldr)
- [Decisions needed from you](#decisions-needed-from-you)
- [Proposals and recommendations](#proposals-and-recommendations)
- [Risks](#risks)
- [Since the last standup](#since-the-last-standup)
- [In progress and to do](#in-progress-and-to-do)
- [Still open](#still-open)
- [Detail and evidence](#detail-and-evidence)
- [Revision Log](#revision-log)

<!-- THE ORDER ABOVE IS THE STANDARD, NOT A SUGGESTION. Blockers, then decision, ask and risk, all
     before the evidence for them. Do NOT insert a background, provenance or evidence section between
     the TL;DR and the decisions table: that is the most common structural failure in these documents,
     and it happens by accretion rather than by choice, because a writer adds a section where they
     were working rather than where the reader needs it. Everything a reader may want to verify but
     does not need to read goes in Detail and evidence, second from last. -->

<!-- doc:region name="blockers" kind="replaceable" -->

## Blockers

<!-- FIRST SECTION IN THE BODY, above the TL;DR, because a blocker is the strongest form of
     reader-required action: nothing moves until the reader decides.

     A BLOCKER IS A CRITICAL DESIGN CHANGE, OR A CHANGE TO THE GENERAL WAY WE WORK THAT IS SIGNIFICANT
     AND CARRIES SIGNIFICANT POTENTIAL CONSEQUENCES, WHERE THE DECISION IS GENUINELY THE STAKEHOLDER'S
     TO MAKE. Nothing else qualifies. THE BAR IS DELIBERATELY HIGH, and the consequence is that "None."
     IS THE EXPECTED NORMAL ANSWER, not a sign you missed something. Exactly one item has met this bar
     over the life of the project. Do not pad, and do not go looking for a candidate.

     AN OPERATOR AUTHORIZATION GATE IS NOT A BLOCKER. This is the distinction the section keeps getting
     wrong, so apply it mechanically by asking what KIND of act the reader must perform:
       - Authorising something we ALREADY DECIDED to do (deploying to a shared environment, pushing to
         a shared repo, opening a pull request, sending something outbound) is an OPERATOR GATE. The
         person giving it is INSIDE the delivery work, so it is internal sequencing. It goes in In
         progress and to do with the pending authorization named inside the item. NOT a blocker, and the
         stakeholder is NOT its owner.
       - Choosing between paths whose consequences are strategically different or hard to reverse is a
         STAKEHOLDER DECISION. That, and only that, belongs here.

     Worked examples, one on each side:
       NOT a blocker: "your go-ahead to dispatch the unified infrastructure pipeline." The dispatch is
         ours to sequence and the choice to deploy was made long ago. In progress and to do.
       A blocker, and the calibration bar: whether to place a shared API gateway in front of a
         consumer API. It changes who owns a production entry point and is costly to reverse. A
         settled decision is an example of something that met the bar, not a pending ask. Never
         re-raise it; that is the noise this rule exists to remove.

     ROUTINE DELIVERY NEEDS NO STAKEHOLDER APPROVAL, including merging to main and deploying to dev. The
     stakeholder assigns broad workstreams and expects us to execute them; he is not a decision maker on
     the detail of any of them, and his sign-off is not routinely needed at all. The implicit standard for
     our own choices is work that is robust, best practice and secure and does not add a lot of friction;
     if you can show a change meets that, you do not need to ask. (This governs how the DOCUMENT
     classifies an item. It grants nobody permission to skip an authorization gate: those live in the
     project context files and are unchanged.)

     REVIEW AND AWARENESS ITEMS DO NOT GO HERE. Anything surfaced so the reader KNOWS what we are doing
     goes in Since the last standup, In progress and to do, or Detail and evidence. Awareness is not an
     act, and a blockers section used as a review queue is unusable as an escalation list.

     UNBLOCK OURSELVES FIRST, THEN SURFACE WITH A WAY FORWARD. Try to clear the obstacle before it
     becomes an item here. When it genuinely cannot be cleared, bring what we ALREADY TRIED and what we
     PROPOSE, never a bare question. An item naming an obstacle with no attempt and no recommendation
     is incomplete whatever its severity.

     Test each candidate: if we did everything right from now on, would it clear? If yes, it is NOT a
     blocker, however severe it is. When something severe is entirely ours, say so in Risks or Still
     open rather than forcing it in here. A blockers section that absorbs our own backlog stops being
     usable as an escalation list.

     LABEL SEVERITY, NEVER PRIORITY. Severity is the consequence if nothing is done. Priority folds in
     cost to fix and our own sequencing, which is not what the reader is deciding. The two words are
     not interchangeable, though they are commonly used that way.

     Four bands, and the definitions are what make them assignable:
       Critical - losing data, down, or exposing data to someone who should not see it, RIGHT NOW.
       High     - no loss yet, but the exposure or risk GROWS ON ITS OWN while nobody acts, or the
                  blocker makes an action we will certainly take unsafe.
       Medium   - real work stopped or unverifiable, consequence bounded and static.
       Low      - inconvenient, with a working route around it today.

     Two rules that keep the bands honest. State the consequence in the item, because a band with no
     stated consequence is a feeling rather than a label. And when an item sits between two bands, take
     the HIGHER one and move on: the standup is not the place to litigate a label.

     AN EMPTY BAND IS INFORMATIVE. Nothing in Critical is a claim that nothing is currently causing
     loss, outage or exposure, and that is worth the reader's attention. Never invent a Critical to
     fill the band.

     Every item carries six things or it is not actionable: what is blocked (led with the function, not
     the mechanism), the band with its justifying consequence, WHAT WE ALREADY TRIED and why it did not
     work, WHAT WE PROPOSE as the specific way forward, WHO OWNS the decision as a named person team or
     role, and the evidence tier. An owner of "TBD" means the item is not ready to be here.

     If there are genuinely no blockers, write "None." and delete the placeholder. That is the usual
     case. Do not pad. -->

Ordered by severity, most severe first.

1. **[FILL: the decision that is the reader's to make, led with the function]** -
   **[FILL: Critical | High | Medium | Low]**. [FILL: the consequence if nobody decides, which is what
   justifies the band.]
   - **Already tried:** [FILL: what we did to clear this ourselves, and why it did not work.]
   - **We propose:** [FILL: the specific act we recommend, not "a decision".]
   - **Owner:** [FILL: named person, team or role whose decision this genuinely is.]
     *(FILL: evidence tier)*

<!-- /doc:region name="blockers" -->

<!-- doc:region name="tldr" kind="replaceable" -->

## TL;DR

<!-- AT MOST 6 BULLETS, ORDERED MOST CONSEQUENTIAL FIRST. Not chronological, not grouped by
     subsystem. Each bullet: what the thing IS (official name), why it matters, and the decision or
     risk attached. Sub-bullets for detail so the section stays scannable instead of turning into
     paragraphs. A reader who stops here must still be able to act. -->

1. **[FILL: most consequential item, named officially]** [FILL: why it matters, one clause.]
   - [FILL: supporting fact, with a number where one exists.]
   - [FILL: the decision or risk this creates.]

<!-- /doc:region name="tldr" -->

<!-- doc:region name="decisions" kind="replaceable" -->

## Decisions needed from you

<!-- Only decisions that are genuinely the reader's to make. If we can decide it, it does not belong
     here. Always carry a recommendation: an unrecommended decision costs the reader more time than
     it saves us.

     NUMBER THE ROWS in the `#` column, from 1, and cite them in the body as "decision 2". Same reason
     the other sections are numbered lists: it gives the meeting a way to refer to one row out loud,
     and it forces a visible ordering decision. Renumbering a row means updating every cross-reference
     to it in the body, so prefer appending.

     TWO THINGS THAT LOOK LIKE DECISIONS AND ARE NOT, both of which have been filed here wrongly:
       - Something WE can settle by measuring or investigating. If the honest next step is research, it
         is an update plus a to-do, not a question for the reader.
       - Something the reader ALREADY DECIDED. Re-raising a settled decision is the noise the blocker
         threshold exists to remove, and it reads as not having listened. -->

| # | Decision | Why it matters | Blocked until decided | Our recommendation |
|---|---|---|---|---|
| 1 | [FILL] | [FILL] | [FILL] | [FILL] |

<!-- /doc:region name="decisions" -->

<!-- doc:region name="proposals" kind="replaceable" -->

## Proposals and recommendations

Things that should change, each with a recommendation and its named cost, but which nothing is
currently waiting on. This section exists so a recommendation does not have to masquerade as a
blocker to get attention. Ordered by how much each one constrains later choices.

**A blocker is a critical design change, or a significant change to the general way we work, that is
genuinely the stakeholder's decision. If the item is something we think is a good idea, it belongs here
instead** - miscategorising it inflates the blocker list and trains the reader to skim it.

1. **<what should change, led by the outcome not the mechanism>.** <One or two sentences of the
   current state, with every significant thing named officially: the resource name, its identifier,
   and a link.>
   - **Recommendation:** <what to do, and when it is cheapest to do it>
   - **Cost:** <the named drawback, including the case where the recommendation is wrong>

<!-- /doc:region name="proposals" -->

<!-- doc:region name="risks" kind="replaceable" -->

## Risks

<!-- Ordered by severity. State the concrete consequence if nothing is done, not a category label.
     "Data loss" is a label; "the only copy lives on one machine" is a consequence. -->

Ordered by severity.

1. **[FILL: risk, named]** [FILL: the concrete consequence if nothing is done.]
   - [FILL: the evidence it is real, not hypothetical.] *(FILL: evidence tier)*

<!-- /doc:region name="risks" -->

<!-- doc:region name="what_moved" kind="replaceable" -->

## Since the last standup

<!-- WINDOW-SCOPED ONLY: what changed since the last meeting. This is NOT a cumulative inventory of
     everything built to date, and conflating the two is how this document doubles in length. If the
     reader also needs the standing picture of what exists and where it is deployed, that is a
     SEPARATE section, and each fact belongs in exactly one of them. When in doubt, ask whether the
     item would still appear here next week; if it would, it is standing state, not news.

     Lead each item with the OUTCOME, not the activity: what is now true that was not true before.
     Every claim carries its evidence tier. Prefer a compact table to three paragraphs. -->

Ordered by [FILL: significance / impact -- or say "chronologically" and why, if sequence is the point].

1. **[FILL: what is now true that was not true before]** [FILL: why that matters, one clause.]
   - [FILL: the evidence, with a number where one exists.] *(FILL: evidence tier)*
   - [FILL: a consequence or caveat, if there is one worth a line.]

<!-- /doc:region name="what_moved" -->

<!-- doc:region name="in_progress" kind="replaceable" -->

## In progress and to do

Work that is ours to build or fix, with no stakeholder decision in the way. Ordered by what unblocks
the most other work. Where an item has an external prerequisite, name it inside the item, so a delay
is attributed to the unmet prerequisite rather than to a person.

**An item with an external prerequisite still belongs here, not in Blockers, when there is work we
can do about it** - raising and tracking the request is work. **An item waiting only on an operator
authorization to deploy, push, open a pull request or send something outbound also belongs here**, with
the pending authorization named inside it: that is our own sequencing, not a stakeholder decision. Move
an item to Blockers only when the stakeholder must choose between strategically different paths.

1. **<what will be true when this is done, led by the capability>.** <Current state, and what is
   left.> **External prerequisite:** <only if one exists, and what is being done about it.>

<!-- /doc:region name="in_progress" -->

<!-- doc:region name="still_open" kind="replaceable" -->

## Still open

<!-- Honestly. Anything unresolved, unverified, or blocked, with who owns the unblock. A reader who
     discovers an omission here stops trusting every section above it. -->

Ordered by [FILL: what would hurt most if left alone].

1. **[FILL: the open item, stated as what is not true yet]** [FILL: the consequence of leaving it.]
   - [FILL: what unblocks it, and who owns that.] *(FILL: evidence tier)*
   - [FILL: where a finding is partial, what IS established and what is NOT.]

<!-- /doc:region name="still_open" -->

<!-- doc:region name="detail" kind="replaceable" -->

## Detail and evidence

<!-- The home for everything cut from the sections above. This section exists so that shortening the
     document never means destroying evidence: elaboration gets cut, evidence gets moved here or to a
     linked document, and a fact a decision turns on stays above instead.

     What belongs here: provenance of a named product or dependency, verification method, per-item
     measurement tables, version inventories, command output, and anything a reader might want to
     check but would not read in the meeting.

     What does NOT belong here: any measurement a decision in the table above turns on. That is not
     detail; keep it above. If you move something out of this document entirely, LINK it - evidence
     relocated without a link has been deleted.

     Prefer a table to paragraphs. A table keeps every value and removes the prose that narrated
     them, which is the cheapest way to shorten a document without losing anything.

     Delete this section if there is nothing to put in it. An empty section is elaboration too. -->

(nothing yet)

<!-- /doc:region name="detail" -->

<!-- doc:region name="revision_log" kind="append_only" -->

## Revision Log

| Date | Change | Notes |
|------|--------|-------|

<!-- /doc:region name="revision_log" -->

<!-- AUTHORING GUIDANCE - not content. Wrapped in an HTML comment so it is invisible
     in rendered Markdown and in a PDF export, while staying readable in the source for whoever
     writes the next one. Leave it in place; there is nothing to delete before shipping.

## How to write this document

The full rulebook, the sourced basis for each rule, the conflicts in the established guidance, and an
honest list of which rules are house style rather than research, all live in
docs/operations/WRITING-STANDARD.md. This block is the working subset for this document kind. Where
the two disagree, the standard wins and this block is stale and should be fixed.

### Lead every item with what it does, never with how it was done

**This applies to every heading, every numbered item, and every bullet, not just the summary.** The
first sentence states the functional change and why it is worth the reader's attention. Mechanism,
API names, file paths, commit counts, branch names, byte sizes and verification method go afterwards,
in sub-bullets, and only if they earn their place.

The test: read the first sentence alone. Does the reader now know what changed for a user or a
developer, and why it matters? If it only tells them which files or which API were involved, it is
written backwards.

Measured examples from this project, all real first drafts that had to be rewritten:

- Wrong: "The load fix and read path are on main and deployed to dev. They went as a scoped change:
  application code, tests and docs only, with no infrastructure definitions." That describes *which file types a
  diff touched* and tells the reader nothing.
- Right: "Adding data to a graph that already had data has never worked, and now it does. One person's
  upload took the shared graph offline for four minutes on its way to failing, so it broke everyone
  else's queries."
- Wrong: "The one thing still open is whether we adopt the security kit." Names the thing without ever
  saying what it does, so the reader cannot weigh the decision.
- Right: "The open question is whether we let the platform team own the front door of our consumer API."
- Wrong, in a bullet: "Verified four ways: the stage's access log settings point at a real log group,
  the group exists, the stack reached UPDATE_COMPLETE, and the format carries source IP." Leads with
  *how it was checked*.
- Right: "Every future call now names its caller. The log captures source IP, user agent and API key
  id, so the next real request tells us who is calling." Verification becomes a later bullet.

A useful symptom: if a first sentence contains a branch name, a file name, an API call or a count
before it contains a verb describing a capability, rewrite it.

### Blockers lead the document, and only stakeholder decisions qualify

The section guidance in the Blockers region carries the working rules: a blocker is a critical design
change or a significant change to the way we work that is genuinely the stakeholder's decision, an
operator authorization gate is not one, review and awareness items are not either, and "None." is the
expected answer. Three points belong here because they are about the document as a whole.

- **The threshold is the whole point of the section, so do not soften it item by item.** A document that
  reports no blockers and explains clearly what we are doing is doing its job. A document that finds
  something to put in the section every week has redefined the word, and the reader learns to skim the
  one section that must not be skimmed.

- **Be honest about where this rule comes from.** No source places a blockers section at the top of a
  written status report. It is derived from the answer-first rule: a blocker is the strongest form of
  reader-required action, so answer-first puts it first. Also know what does NOT support it, so nobody
  defends the section with a bad argument: the 2020 Scrum Guide does not prescribe the "what is blocking
  me" question at all. It mentions impediments twice and leaves the Daily Scrum's structure to the team.
- **Severity and priority are different axes and the sources are explicit about it.** Severity measures
  impact; priority describes importance relative to other items and folds in cost to fix, so a
  low-severity easy fix can outrank a moderate-severity expensive one. We label severity only. If you
  find yourself wanting to say "this is high priority", you mean either "the consequence is severe",
  which is the band, or "we plan to do it next", which belongs in Since the last standup.

The four-band count is house style, since no source supports a particular number. What the evidence does
support is that the definitions carry the consistency, not the count, and that ambiguity is settled by a
tie-break rather than by finer wording.

### Structure every multi-item section as a numbered list with sub-bullets

Not paragraphs, and not a flat bullet list. Numbered top level, sub-bullets beneath, in every section
that carries more than one item: the summary, the window section, risks, open items, all of them.

- **Numbering is not decoration.** It gives the reader and the meeting a way to refer to an item
  ("point 3"), and it forces a visible ordering decision rather than letting items accumulate in
  arrival order.
- **The bold first line of each numbered item is the claim.** Sub-bullets carry the evidence, the
  mechanism and the numbers. If an item has no sub-bullets it is usually fine as one line; if it has
  more than about four, it is two items.
- **Order by priority, impact and significance, and say so in one line under the heading** so the reader
  knows the first item is the most consequential rather than the earliest. Never order by when it
  happened, and never group by subsystem.
- **The one exception is when sequence IS the content** - an incident timeline, a reproduction, a
  migration order. Then order chronologically and say that instead. If you cannot state which ordering
  you used, you have not chosen one.
- A paragraph of prose in a status section is a smell: it means several claims were merged and the
  reader has to separate them.

### Be concise, and sort by impact

The reader has minutes, not an hour. Target the shortest document that still supports a decision.
When a section grows past a screen, cut it rather than tightening sentences: removing whole sections
is what shortens a document, and prose polishing is not.

That distinction is measured, not merely asserted. In the usability study behind the usual
be-concise advice, the gains came from halving the word count and from imposing scannable structure;
nothing in it measured any benefit from tightening prose at constant length and constant structure.
So if the document is too long, the honest move is to delete a section.

The test for every line: **would the reader act differently for having read it?** If not, cut it.
Sort by priority, significance and impact, never chronologically and never by subsystem. The most
consequential item is the first thing on the page.

What to cut, in order: anything the reader would not act differently for; verification method where
the result is what matters; narration of process (what was tried, in what order, how long it took);
restatement of a summary fact in the same words; and counts of internal artifacts unless the count
is itself the finding.

No line-count ceiling is set, because nothing in the research supports one for this kind of
document. Treat "halve it" as an instinct borrowed from web-page research, not a standard.

### Short is not the goal, informative is

The correction that stops this guidance being applied stupidly. Every change description Google's
engineering guide rejects as inadequate is **short**: "Fix bug", "Add patch.", "Phase 1." They fail
anyway, and the stated verdict is that although short, they do not provide enough useful
information.

So brevity is not the property being asked for. The property is that the reader learns what changed
and why it matters, and that sometimes costs words. A one-line item that leaves the reader unable to
act is not concise, it is incomplete. Cut elaboration, not substance.

### Cut elaboration, never evidence

Concision and auditability genuinely conflict, and the conflict does not dissolve if you write more
carefully. Handle it structurally rather than by judgement each time:

- A fact a decision turns on **stays in the body**. It is not detail.
- The method by which that fact was established **can move** to a sub-bullet or to the Detail and
  evidence section.
- If you move something out of the document entirely, **link it**. Evidence relocated without a link
  has been deleted.
- Keep the evidence tier attached to the claim wherever the claim ends up.

Symptom: a measurement that lives in a commit message or a linked document but not in the document
asking the reader to decide on it.

### Attribute a delay to the prerequisite, not the person

Where the document reports a slip, describe the sequencing and the dependency that was not met. Do
not explain a delay by naming who had not decided, and do not tally who decided what.

The first reason is evidentiary rather than social: a dependency is a checkable fact about
sequencing, while an account resting on someone's attention usually is not. The dependency version is
better evidence as well as fairer. The second is the blameless-postmortem finding, that blame changes
what gets written down, and a culture which indicts individuals is one where problems get concealed.
You cannot fix people; you can fix systems and sequencing.

This is not permission to omit the failure. Blameless is not soft: name the slip, name what it cost,
and name the mechanism.

The boundary is judgement, not a symptom. Naming who owns a **live, open** decision is required,
because otherwise nobody owns it. Explaining a **past** slip by who had not decided is the failure
mode.

### Every decision carries a recommendation and a named drawback

An unrecommended decision costs the reader more time than it saved the writer. A recommendation with
no stated cost is not a recommendation, it is advocacy. Google's guidance is explicit that where an
approach has shortcomings they should be mentioned; a document that recommends and names no drawback
is incomplete rather than clean.

Symptom: a decisions row with an empty recommendation column, or one hedged until it commits to
nothing.

### Before shipping, run this checklist

Mechanical, in order, so it does not depend on how the writer feels about the draft.

1. The ask, the risk and the recommendation are all above the first screen.
2. Every blockers item is a decision that is genuinely the reader's to make - not an operator
   authorization gate, not a review item - and carries what we already tried and what we propose.
   "None." is a normal answer.
3. No background, provenance or evidence section sits between the TL;DR and the decisions table.
4. Every decisions row has a recommendation and a named drawback.
5. No first sentence opens with a file, branch, API, count, or the words Verified, Confirmed or
   Checked.
6. Every delay is attributed to a prerequisite, not a person.
7. Every load-bearing measurement is still in the body, and everything moved out is linked.
8. Every significant thing carries its official name, its identifier and a link.
9. ASCII only, and no internal tracking identifiers. The reference for teammates is the Jira key.
10. The table of contents lists every heading that matters, and its anchors resolve.
11. A sample relative link resolves **from this document's own directory**, not from the repo root.
12. The revision log has a new row.

### Name every significant thing, officially

**A document that describes things instead of naming them cannot be used in a live conversation.**
This has actually happened on this project: a report referred to "the security kit" and "the
unidentified consumer", and neither could be shown to the stakeholder because neither carried a name
or a location.

For anything that matters:

- Use the **official name**, spelled as the owning system spells it. Not a paraphrase.
- Add the **identifier** where one exists: stack name, API id, resource name, version.
- Add a **link** to wherever it can be read.

Wrong: "the API on dev, fronted by the standard kit". Right: "the API Gateway REST API
`project-sync-consumer-api-dev` (api id `example123`, stage `dev`), fronted by the shared gateway
template v2.3".

### Link so both VS Code and GitHub resolve

These documents are read in the VS Code Markdown preview and on GitHub. Both must work.

- **Same-repo documents: a plain relative Markdown link with real display text.** Resolves in both.
  Example, from a document at `standups/<author>/<YYYY-MM-DD>/`, which is four levels below `docs/`:
  `[the consumer API caller attribution](../../../../analysis/consumer-api-caller-attribution.md)`
- **Never wrap link text in backticks.** A code span overrides link styling, so it renders as
  monospace and looks unclickable. Put the path in the display text as plain words instead.
- **Always give display text.** A bare URL does not tell the reader why they would click it.
- **External URLs: link the name, not the address.** Example:
  `[the kit template on the PoC branch](https://github.com/...)`
- **Cross-repo documents carry BOTH destinations**, because a relative path that works locally is
  dead on GitHub:
  `[name (local)](../../../other-repo/path.md) ([GitHub](https://github.com/owner/other-repo/blob/main/path.md))`
- **Verify a sample resolves** from this document's own directory before shipping. A broken link in a
  document being screen-shared is worse than no link.

### State findings precisely, including the part not yet known

Where a question is partly answered, give both halves: what is established, and what is not. Half an
answer presented as none wastes work already done; half presented as whole misleads. When a signal
points at a person or team without proving it, say exactly what it does and does not establish -
that distinction is usually the reader's next question.

### Audience rules

Teammate-visible, so: ASCII only, no internal dev-tracking identifiers (use the Jira key), and no
raw resource identifiers where a name reads better. Plain direct language, short paragraphs.
-->
