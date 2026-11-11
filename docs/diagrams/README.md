# README diagrams

The root README embeds a checked-in PNG so its diagram does not depend on a
JavaScript-enabled Mermaid renderer. The SVG is the scalable alternative, and
the Graphviz DOT file is the editable source. Text and arrow labels carry the
meaning; color is only a visual aid.

## Regenerate

From the repository root, with Graphviz and DejaVu Sans installed:

```sh
dot -Tsvg docs/diagrams/skills-release-workflow.dot -o docs/diagrams/skills-release-workflow.svg
dot -Tpng -Gdpi=144 docs/diagrams/skills-release-workflow.dot -o docs/diagrams/skills-release-workflow.png
```

The checked-in images were rendered with Graphviz 2.42.4. Rendering can vary
slightly with the Graphviz and font versions. Inspect the PNG, the SVG and the
GitHub-rendered README before merging changes; confirm that all labels, arrow
directions, branches and the revision loop are readable without clipping.

## Projection boundary

These files are public-repository documentation assets, not an installed skill or
a generated dependency. Preserve `docs/diagrams/` when refreshing the public
projection, just as other repository-local files are preserved. The materializer
writes a fresh staging directory; it does not copy these assets or update an
existing checkout in place. Do not replace the entire public tree with that
staging directory.

Pass the current README as the materializer's `--readme-base` so its prose and
diagram links survive while the marked catalog is regenerated. Regenerate the
README provenance through that supported workflow; do not edit recorded digests
by hand. These repository-local images are deliberately outside the generated
file inventory in `PUBLIC-PROJECTION-PROVENANCE.json`.
