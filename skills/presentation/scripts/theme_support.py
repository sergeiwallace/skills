"""Manifest-selected, offline-safe Slidev theme materialization."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

try:
    from .presentation import SKILL_ROOT, PresentationError
except ImportError:  # Direct execution from the installed skill's scripts directory.
    from presentation import SKILL_ROOT, PresentationError

THEME_TEMPLATE = SKILL_ROOT / "themes" / "default"
BASE_CSS = SKILL_ROOT / "themes" / "default.css"
EXTERNAL_CSS = re.compile(r"(?:https?:)?//|@import\s+url", re.IGNORECASE)


def _theme_file(theme: dict[str, Any], deck_root: Path, key: str, label: str) -> Path:
    root = deck_root if theme["root"] == "deck" else SKILL_ROOT
    value = theme[key]
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise PresentationError(f"{label} path must be relative and contained: {value}")
    resolved_root = root.resolve()
    resolved = (root / candidate).resolve()
    if resolved_root not in (resolved, *resolved.parents) or not resolved.is_file():
        raise PresentationError(
            f"{label} path is not a contained regular file: {value}"
        )
    return resolved


def resolve_theme(
    manifest: dict[str, Any], deck_root: Path
) -> tuple[dict[str, Any], str]:
    """Load the selected token JSON and its optional local CSS variant."""
    theme = manifest["theme"]
    token_path = _theme_file(theme, deck_root, "tokens", "theme")
    try:
        tokens = json.loads(token_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PresentationError("theme tokens are missing or invalid JSON") from exc
    required = {
        "colors": ("background", "foreground", "accent", "muted", "panel", "positive"),
        "fonts": ("body", "display", "mono"),
        "spacing": ("base",),
    }
    missing = [
        f"{group}.{key}"
        for group, keys in required.items()
        for key in keys
        if not tokens.get(group, {}).get(key)
    ]
    if not tokens.get("radius"):
        missing.append("radius")
    if missing:
        raise PresentationError(
            "theme tokens missing required value(s): " + ", ".join(missing)
        )
    css_parts = [BASE_CSS.read_text(encoding="utf-8")]
    if theme.get("variant"):
        css_parts.append(
            _theme_file(theme, deck_root, "variant", "theme variant").read_text(
                encoding="utf-8"
            )
        )
    css = "\n".join(css_parts)
    if EXTERNAL_CSS.search(css):
        raise PresentationError("theme CSS must not load external URLs")
    return tokens, css


def _css_value(value: object) -> str:
    return str(value).replace("\n", " ").replace("\r", " ").replace(";", "")


def materialize_theme(
    manifest: dict[str, Any], deck_root: Path, destination: Path
) -> Path:
    """Create a render-local Slidev theme from manifest-selected tokens and variant."""
    tokens, css = resolve_theme(manifest, deck_root)
    if destination.exists():
        raise PresentationError(f"theme destination already exists: {destination}")
    shutil.copytree(THEME_TEMPLATE, destination)
    colors = tokens["colors"]
    fonts = tokens["fonts"]
    spacing = tokens["spacing"]["base"]
    radius = tokens["radius"]
    variables = {
        "background": colors["background"],
        "foreground": colors["foreground"],
        "accent": colors["accent"],
        "muted": colors["muted"],
        "panel": colors["panel"],
        "positive": colors["positive"],
        "font-body": fonts["body"],
        "font-display": fonts["display"],
        "font-mono": fonts["mono"],
        "space": f"{spacing}px",
        "radius": f"{radius}px",
    }
    token_css = (
        ":root {\n"
        + "".join(
            f"  --presentation-{name}: {_css_value(value)};\n"
            for name, value in variables.items()
        )
        + "}\n"
    )
    (destination / "styles/tokens.css").write_text(token_css, encoding="utf-8")
    (destination / "styles/theme.css").write_text(css, encoding="utf-8")
    return destination
