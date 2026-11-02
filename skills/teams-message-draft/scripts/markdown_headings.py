"""Markdown heading parsing with GitHub-style anchor slugs, for teams_drafts.py.

Only what the drafts doc needs: every ATX heading outside fenced code blocks, its rendered
text, and the anchor GitHub would give it, with duplicate slugs numbered `-1`, `-2` in file
order the way GitHub numbers them.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_FENCE = re.compile(r"^(?P<fence>```+|~~~+)")
_HEADING = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<text>.*?)\s*$")
_HTML_TAG = re.compile(r"<[^>]+>")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_MD_LINK = re.compile(r"\[(?P<text>[^\]]*)\]\((?P<href>[^)]*)\)")
_INLINE_MARKS = re.compile(r"(\*\*|__|\*|`)")
# github-slugger keeps Unicode letters/digits/marks plus hyphen, underscore and space.
_SLUG_KEEP_EXTRA = {"-", "_", " "}


def strip_inline_markdown(text: str) -> str:
    """Reduce heading source to the text content a Markdown renderer would emit."""
    text = _HTML_COMMENT.sub("", text)
    text = _MD_LINK.sub(lambda m: m.group("text"), text)
    text = _HTML_TAG.sub("", text)
    text = _INLINE_MARKS.sub("", text)
    return text.strip()


def slugify(heading_text: str) -> str:
    """Derive the GitHub anchor slug for a heading's rendered text.

    Mirrors github-slugger: lowercase, drop every character that is not a Unicode
    letter/digit/mark, hyphen, underscore or space, then map each remaining space to a
    hyphen. Runs of spaces are deliberately NOT collapsed, which is why a heading such as
    ``Round 1 - Main`` yields a double hyphen.
    """
    text = strip_inline_markdown(heading_text).lower()
    kept = []
    for ch in text:
        if ch in _SLUG_KEEP_EXTRA:
            kept.append(ch)
            continue
        if unicodedata.category(ch)[0] in {"L", "N", "M"}:
            kept.append(ch)
    return "".join(kept).replace(" ", "-")


@dataclass
class Heading:
    level: int
    text: str
    slug: str
    line: int


def parse_headings(text: str) -> list[Heading]:
    """Every ATX heading outside fenced code blocks, with duplicate-slug suffixing."""
    headings: list[Heading] = []
    seen: dict[str, int] = {}
    fence: str | None = None
    for lineno, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        fence_match = _FENCE.match(stripped)
        if fence_match:
            marker = fence_match.group("fence")[0] * 3
            if fence is None:
                fence = marker
            elif stripped.startswith(fence):
                fence = None
            continue
        if fence is not None:
            continue
        match = _HEADING.match(raw)
        if match is None:
            continue
        base = slugify(match.group("text"))
        count = seen.get(base, 0)
        seen[base] = count + 1
        slug = base if count == 0 else f"{base}-{count}"
        headings.append(
            Heading(
                level=len(match.group("hashes")),
                text=strip_inline_markdown(match.group("text")),
                slug=slug,
                line=lineno,
            )
        )
    return headings
