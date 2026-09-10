---
name: graphic
description: Provider-neutral pipeline for small graphic assets (icons, favicons, simple marks) — inspect/brief, route to reuse or constrained SVG primitives, sanitize+normalize through an SVG allowlist and SVGO, render a contact sheet, and gate on human selection before packaging. Phase one only; vector generation, tracing, and raster generation are deliberately out of scope.
license: Apache-2.0
---

# graphic

Design and ship a small graphic asset (an icon, a favicon, a simple mark) through a deterministic,
security-reviewed pipeline instead of hand-authoring SVG ad hoc each time. Phase one deliberately
limits itself to reusing existing assets or authoring constrained SVG primitives: both routes are
reviewable, work without a generation provider, and keep the dependency surface small. The
allowlist rejects unsafe SVG rather than attempting to strip it, because a partial sanitizer can
miss parser-level evasions.

**Usage:** `/graphic <asset request>` — e.g. `/graphic a 32px monochrome briefcase icon for the
favicon of a small open-source project, reticle-framed`.

## When to use

Any request for a small, functional graphic asset: a favicon, an app icon, a simple in-repo mark.
**Not** for illustrations, marketing art, or anything genuinely needing novel raster generation —
this skill's phase-one scope is reuse-or-hand-authored-vector only. Use an image-generation tool
available in the caller's environment for work outside that scope.

## Prerequisites

Use `SKILL_ROOT=.` when running from the installed directory containing this `SKILL.md`, or set
`SKILL_ROOT` to that directory when running elsewhere. The commands below use `$SKILL_ROOT` so they
do not depend on a catalog-specific install path. `python3` is sufficient for the allowlist, which
uses only the Python standard library. For rendering, install the local Node dependency once with
`(cd "$SKILL_ROOT/scripts" && npm install)`. See [DEPENDENCIES.md](DEPENDENCIES.md) for the declared
dependency and its license.

## Process

1. **Inspect and brief.** Read the target repo's existing icon usage (`grep -r "<svg" --include
   "*.html" --include "*.svg"`, look for a `static/` or `assets/` icon directory, and any
   design-token file if one exists). Write one explicit paragraph: subject, target render sizes,
   palette/background constraints, and whether an existing reusable icon already fits.
2. **Route — phase one supports exactly two routes:**
   - **Reuse:** if an existing, already-used icon library asset in the repo fits the brief
     semantically, point at it directly. Do not generate a new asset when reuse fits.
   - **Constrained SVG primitives:** for a simple glyph, hand-author a small, reviewable SVG using
     only basic shapes (`rect`, `circle`, `ellipse`, `line`, `polygon`, `polyline`, simple `path`
     arcs/lines). Never freehand-generate a complex, hard-to-review path. **If the brief includes
     any text/wordmark content, use a `<text>` element — never hand-draw letterforms as bezier
     paths.** A freehand approximation of a letter's shape is not a reliable way to spell a word
     and can become illegible at small sizes.
   - Any request that genuinely needs novel vector generation, raster tracing, or raster generation
     is **out of scope for this skill's phase one** — say so explicitly and use an available
     image-generation tool or another appropriate workflow.
3. **Normalize and secure — never skip this step.** Run every SVG candidate through
   `python3 "$SKILL_ROOT/scripts/svg_allowlist.py" <path>` before it is rendered, shown, or
   committed. A candidate that fails is rejected outright — regenerate from a fresh, reviewable
   source; do not attempt to "clean" untrusted SVG by stripping the offending nodes (a best-effort
   strip can miss an evasion the parser itself doesn't catch). Then run it through SVGO:
   `npx --yes svgo <path> -o <path>`.
4. **Render and inspect.** Run `node "$SKILL_ROOT/scripts/resvg_contact_sheet.mjs" <svg-path> --sizes
   16,32,48,128 --out <png-path>` to render one PNG per size/background-color combination (a real
   compositor into one flattened strip image is a Phase-2 item — see below) plus a
   `<png-path-base>.manifest.json` index of every cell, across the standard favicon review sizes
   on both light and dark backgrounds. Look at every rendered cell yourself before presenting them
   — a mark that reads fine at 128px can fail at 16px. **If the brief supplied a reference image,
   view the rendered cells
   side by side with it and check specifically: does any text spell the intended word correctly
   and legibly, and does the overall shape/composition read as the same subject as the reference?**
   A candidate that fails either check is not done — regenerate it, or report the specific gap and
   why it could not be closed within this skill's current scope. Do not present a candidate you
   have not actually looked at rendered.
5. **Human gate — hard stop.** Present the contact sheet and every candidate considered. Do not
   proceed to packaging without an explicit human pick. Never auto-apply a candidate.
6. **Package.** For a favicon-shaped deliverable, add the chosen SVG as a static file and reference
   it with a `<link rel="icon">` tag in the target application's document head. Phase one does not
   invoke the `favicons` npm package for full ICO/PWA/manifest bundling — see Phase 2.
7. **Receipt.** Append one entry to a session-local `provenance.md` (not repo-tracked in phase
   one): request text, route taken, source file(s), SVGO before/after byte count, allowlist
   result, contact-sheet path, and the human's selection.

## Phase 1 vs. Phase 2

**Phase 1 (this implementation):** reuse + constrained-primitive routing, the SVG allowlist, the
SVGO wrapper, the `@resvg/resvg-js` per-cell renderer (individual PNGs + a manifest, not yet a
single flattened contact-sheet image), manual favicon-style packaging, a prose provenance log.
**Wordmark text:** a constrained `<text>`/`<tspan>` element is permitted for
short in-icon labels (e.g. a favicon spelling out a word), with `font-family` restricted to a
fixed list of widely-available bold sans-serif/serif/monospace stacks in
`scripts/svg_allowlist.py`'s `ALLOWED_FONT_FAMILIES` (no external or arbitrary font reference can
pass). Hand-drawing letterforms as freehand bezier paths should not be the default for text
content because real type is more legible and easier to review.

**Phase 2 (deliberately not built here):** novel vector generation, raster tracing, raster
generation, full ICO/PWA/manifest packaging through the `favicons` npm package, a real single-image
contact-sheet compositor (which would need a raw-pixel-to-PNG library such as `pngjs` or `sharp`),
and a structured (non-prose) receipt schema. Keep these routes out of phase one to retain its
small, reviewable dependency surface.

## Rules

- **The allowlist is not optional.** Every SVG candidate — reused, hand-authored, or (in a future
  phase) generated — passes through `scripts/svg_allowlist.py` before render or commit. SVG is
  executable XML in a browser context; `<script>`, `<foreignObject>`, `<image>`, and any external
  `href`/`xlink:href` are unconditionally rejected, not stripped.
- **No auto-apply.** The human-selection gate (step 5) is a hard stop for every run — this skill
  never wires a candidate into a target codebase without an explicit human pick recorded first.
- **Phase-2 routes stay out of scope.** Do not use novel-vector, tracing, or raster-generation
  tooling merely because a request calls for it; name the gap instead (see step 2).
- **Fail closed on missing tooling.** If `node`, `npx`, or the `@resvg/resvg-js` package are
  unavailable, the render step reports that plainly rather than skipping the contact sheet or
  guessing at what the asset looks like.
