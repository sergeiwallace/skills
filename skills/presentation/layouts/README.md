# Presentation layouts

Only title, thesis, comparison, process, evidence, code, demo, and close may be named in a
manifest. layouts.json declares each layout's real Slidev composition, allowed and required named
slots, region limit, and layout-aware density range. body is the default slot; additional manifest
regions must appear in slides.md as Slidev named slots such as ::left::.

The render command validates those slots, generates layout frontmatter in an immutable render-only
source copy, and selects the corresponding local Vue layout. A presentation metadata comment alone
is never sufficient. Direct CSS overrides remain outside the contract.
