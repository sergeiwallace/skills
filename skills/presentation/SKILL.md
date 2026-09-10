---
name: presentation
description: Author a local, review-gated Slidev presentation from a normalized event brief and optional research.
license: Apache-2.0
argument-hint: "--brief <path|text> [--research <path> ...] [--audience internal|external] [--output <deck-directory>] [--mode new|revise]"
---

# presentation

Use this skill to create a local Slidev deck workflow. Normalize and validate the brief and every
research path before probing the platform or acquiring the deck lock. v1 supports only Slidev and
uses artifact-gated researcher, narrative editor, design planner, builder, QA critic, and presenter
roles. Every deck requires digest-bound story-outline, representative-visual-system, and
final-rehearsal-export approvals. Do not create an external transfer without approved-external
classification. Keep generated artifacts under the deck directory rather than the skill.

## Narrative contract

The narrative editor may transform an authoritative content outline into a stage-ready arc while
preserving its claims, sources, and required coverage. For talks over 20 minutes, write
story.contract.json before story-outline approval with non-empty audience_problem, promise,
sections, example_or_demo, transition, callback, and closing_action. The arc must open with an
audience promise or tension, show a visible route map, establish section beats, alternate principle
and example, include one concrete end-to-end demonstration, call back to the opening, and end with
a specific action. Only an explicit narrative_exception in brief.md may opt out; the reason remains
reviewable in the committed brief.

## Design and QA contract

Use only the registered layouts and declare every required region. body is the default Slidev slot;
write other regions as named slots such as ::left::. Add section and optional time_cue metadata to
slides so the theme can show movement, progress, and timing. Follow
references/voice-and-density.md for layout-aware density and move explanatory detail into speaker
notes. The render command resolves manifest.theme.root/tokens/variant into an offline local theme;
do not add remote fonts or CDN assets.

The critic review must bind the render digest and explicitly judge hierarchy, composition variety,
information density, narrative continuity, audience fit, accessibility, and rehearsal readiness
at deck level and on representative slides. An empty findings list is clean only with complete
passing coverage. A comparison_target additionally requires an identified human to judge
hierarchy, pacing, examples, narrative continuity, actionability, and source rigor. Rendered lint
fails closed on missing pages/text, mismatched slide IDs/layouts/slots, token drift, overflow,
off-canvas content, low contrast, broken links, or external requests.

After render and contact-sheet, configure `PRESENTATION_REVIEWER_DISPATCH` and invoke
`scripts/critic --run <run> --input-digest <digest> --render-digest <digest>` without `--review`.
That command dispatches the configured visual-review adapter with the contact sheet when present
and every ordered per-slide PNG attached via repeatable `-i`, constrains the response with
`schemas/critic-review.schema.json`, validates complete rubric coverage, and records the
attached-image hashes in `review_basis` before committing `critic.fragment.json`. The adapter
contract and complete local bootstrap instructions are in [DEPENDENCIES.md](DEPENDENCIES.md). If
the variable is unset, the critic fails; it never skips or fabricates the required visual gate.
Never derive the critic JSON from `inspect_dom.cjs`; DOM inspection is deterministic lint evidence,
not visual review. `--review` exists only for tests and importing an independently pixel-grounded
review.
