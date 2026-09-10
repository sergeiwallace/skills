#!/usr/bin/env python3
"""Reject an SVG that carries anything outside a small, browser-safe element/attribute allowlist.

SVG is executable XML in a browser context. This script rejects a candidate outright on any
violation rather than attempting to strip offending nodes -- a best-effort strip can miss an
evasion the parser itself doesn't catch, so a rejected candidate must be regenerated from a fresh,
reviewable source instead.
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ALLOWED_ELEMENTS = frozenset(
    {
        "svg",
        "g",
        "path",
        "rect",
        "circle",
        "ellipse",
        "line",
        "polygon",
        "polyline",
        "defs",
        "use",
        "title",
        "desc",
        "text",
        "tspan",
    }
)

# Wordmark-style icon content (e.g. a short label baked into a logo/favicon) needs real type;
# hand-drawn bezier approximations of letterforms are unreliable. `font-family` is restricted to
# this fixed allowlist of widely available system font stacks so no external font resource can be
# referenced.
ALLOWED_FONT_FAMILIES = frozenset(
    {
        "Arial, Helvetica, sans-serif",
        "Arial Black, Arial, sans-serif",
        "Helvetica, Arial, sans-serif",
        "Georgia, 'Times New Roman', serif",
        "'Courier New', Courier, monospace",
    }
)

ALLOWED_ATTRIBUTES = frozenset(
    {
        "id",
        "class",
        "viewBox",
        "width",
        "height",
        "x",
        "y",
        "x1",
        "y1",
        "x2",
        "y2",
        "cx",
        "cy",
        "r",
        "rx",
        "ry",
        "points",
        "d",
        "fill",
        "stroke",
        "stroke-width",
        "stroke-linecap",
        "stroke-linejoin",
        "stroke-dasharray",
        "opacity",
        "fill-opacity",
        "stroke-opacity",
        "transform",
        "xmlns",
        "role",
        "aria-label",
        "aria-hidden",
        "font-family",
        "font-weight",
        "font-size",
        "font-style",
        "text-anchor",
        "dominant-baseline",
        "letter-spacing",
    }
)

BLOCKED_ELEMENTS = frozenset({"script", "foreignObject", "image", "iframe", "style"})


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _local_attr(attr: str) -> str:
    return attr.rsplit("}", 1)[-1] if "}" in attr else attr


class AllowlistViolation(ValueError):
    """The SVG carries an element or attribute outside the allowlist."""


def check_svg(svg_text: str) -> None:
    """Raise AllowlistViolation naming the first offending element or attribute found."""
    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError as exc:
        raise AllowlistViolation(f"not well-formed XML: {exc}") from exc

    for element in root.iter():
        tag = _local_name(element.tag)
        if tag in BLOCKED_ELEMENTS:
            raise AllowlistViolation(f"blocked element <{tag}>")
        if tag not in ALLOWED_ELEMENTS:
            raise AllowlistViolation(f"element <{tag}> is not in the allowlist")
        for attr, value in element.attrib.items():
            local = _local_attr(attr)
            if local in {"href", "xlink:href"} or attr.endswith("href"):
                if not value.startswith("#"):
                    raise AllowlistViolation(
                        f"external href on <{tag}>: {value!r} (only same-document '#...' allowed)"
                    )
                continue
            if local not in ALLOWED_ATTRIBUTES:
                raise AllowlistViolation(
                    f"attribute {local!r} on <{tag}> is not in the allowlist"
                )
            if local == "font-family" and value not in ALLOWED_FONT_FAMILIES:
                raise AllowlistViolation(
                    f"font-family {value!r} on <{tag}> is not in the fixed safe-font allowlist "
                    f"({sorted(ALLOWED_FONT_FAMILIES)})"
                )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    try:
        svg_text = args.path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"svg_allowlist: cannot read {args.path}: {exc}", file=sys.stderr)
        return 2
    try:
        check_svg(svg_text)
    except AllowlistViolation as exc:
        print(f"svg_allowlist: REJECTED — {exc}", file=sys.stderr)
        return 1
    print(f"svg_allowlist: PASS — {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
