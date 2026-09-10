"""Deterministic QA decisions for presentation lint output."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    from .presentation import layout_limits
except ImportError:  # Direct execution from the installed skill's scripts directory.
    from presentation import layout_limits


def check(
    check_id: str, severity: str, status: str, message: str, slide: str | None = None
) -> dict[str, str]:
    result = {
        "id": check_id,
        "severity": severity,
        "status": status,
        "message": message,
    }
    if slide:
        result["slide"] = slide
    return result


def _expected_tokens(tokens: dict[str, Any]) -> dict[str, str]:
    try:
        return {
            "background": str(tokens["colors"]["background"]),
            "foreground": str(tokens["colors"]["foreground"]),
            "accent": str(tokens["colors"]["accent"]),
            "fontBody": str(tokens["fonts"]["body"]),
            "spacing": f"{tokens['spacing']['base']}px",
            "radius": f"{tokens['radius']}px",
        }
    except (KeyError, TypeError):
        return {}


def compute_checks(
    manifest: dict[str, Any],
    tokens: dict[str, Any],
    inspection: dict[str, Any] | list[dict[str, Any]],
    link_exists_fn: Callable[[str], bool],
) -> list[dict[str, str]]:
    """Compute lint findings from rendered pages, elements, and declared inputs."""
    if isinstance(inspection, list):
        elements = inspection
        pages: list[dict[str, Any]] = []
    else:
        elements = inspection.get("elements", [])
        pages = inspection.get("pages", [])
    checks: list[dict[str, str]] = []
    slides = manifest["slides"]
    by_slide: dict[str, list[dict[str, Any]]] = {slide["id"]: [] for slide in slides}
    unmatched_elements = []
    for element in elements:
        index = element.get("slideIndex")
        if isinstance(index, int) and 0 <= index < len(slides):
            by_slide[slides[index]["id"]].append(element)
        else:
            unmatched_elements.append(element)
    pages_by_index = {
        page["slideIndex"]: page
        for page in pages
        if isinstance(page.get("slideIndex"), int)
    }
    expected_token_values = _expected_tokens(tokens)
    registry = layout_limits()
    asset_names = {Path(asset["path"]).name for asset in manifest["assets"]}
    rendered_assets = {
        Path(urlparse(element["image"]).path).name
        for element in elements
        if element.get("image")
    }

    for index, slide in enumerate(slides):
        sid = slide["id"]
        page = pages_by_index.get(index)
        mapped = bool(
            page
            and page.get("presentationId") == sid
            and page.get("layout") == slide.get("layout")
        )
        checks.append(
            check(
                "page-mapping",
                "error",
                "pass" if mapped else "error",
                "rendered page maps to manifest ID and layout",
                sid,
            )
        )
        required_slots = set(
            registry.get(slide.get("layout"), {}).get("required_slots", ["body"])
        )
        actual_slots = set(page.get("slots", [])) if page else set()
        checks.append(
            check(
                "layout-slots",
                "error",
                "pass" if mapped and required_slots <= actual_slots else "error",
                "rendered layout exposes every required named slot",
                sid,
            )
        )
        actual_tokens = page.get("tokenValues", {}) if page else {}
        tokens_match = bool(
            expected_token_values
            and all(
                str(actual_tokens.get(key, "")).strip().lower() == value.strip().lower()
                for key, value in expected_token_values.items()
            )
        )
        checks.extend(
            [
                check("schema", "error", "pass", "manifest is valid", sid),
                check(
                    "title",
                    "error",
                    "pass" if slide["title"].strip() else "error",
                    "action title present",
                    sid,
                ),
                check(
                    "evidence",
                    "error",
                    "pass"
                    if slide.get("evidence")
                    or slide["intent"] in {"opinion", "transition", "demo"}
                    else "error",
                    "evidence present where required",
                    sid,
                ),
                check(
                    "assets",
                    "error",
                    "pass" if asset_names <= rendered_assets else "error",
                    "declared assets are rendered",
                    sid,
                ),
                check(
                    "tokens",
                    "error",
                    "pass" if tokens_match else "error",
                    "computed DOM theme tokens match the manifest selection",
                    sid,
                ),
            ]
        )

        slide_elements = by_slide[sid]
        text_elements = [element for element in slide_elements if element.get("text")]
        words = sum(len(element["text"].split()) for element in text_elements)
        density = registry.get(slide.get("layout"), {}).get(
            "density", {"minimum": 0, "maximum": 90, "hard_maximum": 90}
        )
        if not text_elements:
            checks.append(
                check(
                    "density",
                    "error",
                    "error",
                    "text-bearing manifest slide has no inspectable rendered text",
                    sid,
                )
            )
            for check_id in (
                "font-size",
                "overflow",
                "off-canvas",
                "contrast",
                "links",
            ):
                checks.append(
                    check(
                        check_id,
                        "error",
                        "error",
                        "no inspectable text elements found",
                        sid,
                    )
                )
            continue
        checks.append(
            check(
                "density",
                "error",
                "pass" if words <= density["hard_maximum"] else "error",
                f"rendered slide has {words} words "
                f"(hard maximum {density['hard_maximum']})",
                sid,
            )
        )
        in_guidance = density["minimum"] <= words <= density["maximum"]
        checks.append(
            check(
                "density-guidance",
                "warning",
                "pass" if in_guidance else "error",
                f"{slide['layout']} guidance is {density['minimum']}-"
                f"{density['maximum']} words; rendered slide has {words}",
                sid,
            )
        )
        notes_words = len(slide.get("speaker_notes", "").split())
        minimum_notes = max(1, slide.get("estimated_seconds", 1) // 2)
        checks.append(
            check(
                "notes-timing",
                "warning",
                "pass" if notes_words >= minimum_notes else "error",
                f"speaker notes have {notes_words} words; "
                f"{minimum_notes} support {slide.get('estimated_seconds', 0)} seconds",
                sid,
            )
        )
        for element in text_elements:
            checks.extend(
                [
                    check(
                        "font-size",
                        "error",
                        "error" if element["fontSize"] < 24 else "pass",
                        "normal text must be at least 24 CSS px",
                        sid,
                    ),
                    check(
                        "overflow",
                        "error",
                        "error" if element["overflow"] else "pass",
                        "element must remain on-canvas",
                        sid,
                    ),
                    check(
                        "off-canvas",
                        "error",
                        "error" if min(element["left"], element["top"]) < 0 else "pass",
                        "element must not be off-canvas",
                        sid,
                    ),
                    check(
                        "contrast",
                        "error",
                        "error" if element["contrast"] < 4.5 else "pass",
                        "normal text must meet 4.5:1 contrast",
                        sid,
                    ),
                ]
            )
        links = [element for element in slide_elements if element.get("href")]
        if not links:
            checks.append(
                check("links", "error", "pass", "no inspectable links found", sid)
            )
        for element in links:
            href = element["href"]
            parsed = urlparse(href)
            local = not parsed.scheme and not href.startswith("#")
            exists = not local or link_exists_fn(parsed.path)
            checks.append(
                check(
                    "links",
                    "error",
                    "pass" if exists else "error",
                    "link target resolves" if exists else f"broken local link: {href}",
                    sid,
                )
            )

    if unmatched_elements or len(pages) != len(slides):
        checks.append(
            check(
                "page-mapping",
                "error",
                "error",
                "DOM inspection contains unmatched pages or elements",
            )
        )
    total_words = sum(
        len(element.get("text", "").split())
        for element in elements
        if element.get("text")
    )
    checks.append(
        check(
            "deck-density",
            "error",
            "pass" if total_words else "error",
            f"rendered deck has {total_words} inspectable words",
        )
    )
    external_requests = (
        inspection.get("externalRequests", []) if isinstance(inspection, dict) else []
    )
    checks.append(
        check(
            "external-requests",
            "error",
            "error" if external_requests else "pass",
            "rendered deck makes no external font or CDN requests",
        )
    )
    return checks
