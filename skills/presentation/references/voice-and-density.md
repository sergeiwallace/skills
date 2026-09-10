# Voice, narrative, and density

## Narrative shape

Use action titles and preserve every supported claim from the authoritative outline, but reshape
the sequence for live comprehension. A talk over 20 minutes needs:

1. an audience problem and promise in the opening hook;
2. a visible route map and named section beats;
3. principle/example alternation instead of a run of abstract recommendations;
4. one concrete demonstration with observable input, action, and result;
5. transitions that explain why the next section follows;
6. a callback that resolves the opening tension; and
7. a close naming a specific audience action.

Record these in story.contract.json. A brief may opt out only with a non-empty
narrative_exception. The narrative editor may change sequence and framing, while claims, sources,
mandatory coverage, and constraints remain authoritative.

## Layout-aware density

The machine-readable ranges and hard ceilings live in layouts/layouts.json. Use them as purposeful
pacing guidance:

| Layout | Guidance | Role |
|---|---:|---|
| title | 3-42 words | Sparse cover or section opener |
| thesis | 18-62 words | Moderate thesis, agenda, or route map |
| comparison | 32-105 words | Selectively dense comparison |
| process | 22-72 words | Moderate sequence or mechanism |
| evidence | 24-88 words | Quantitative claim plus context |
| code | 28-115 words | Selectively dense code/example |
| demo | 28-100 words | Observable input/action/result |
| close | 5-48 words | Sparse callback and CTA |

Hard ceilings remain higher than the guidance maxima to separate a pacing warning from an
unreadable slide. Put the detail removed from sparse visuals in speaker notes. Notes should contain
roughly one word per two seconds of estimated delivery at minimum; a 45-minute deck with only terse
one-line notes is not rehearsal-ready.

Source every factual claim, provide alt text for meaningful assets, and keep normal text at least
18 pt (24 CSS px). Do not use unapproved assets, unsupported direct CSS, remote fonts, or CDN
dependencies.
