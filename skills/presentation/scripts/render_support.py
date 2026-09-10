"""Shared, dependency-free primitives for the presentation render/QA commands."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

try:
    from .presentation import LAYOUT_NAMES, PresentationError, layout_limits
except ImportError:  # Direct execution from the installed skill's scripts directory.
    from presentation import LAYOUT_NAMES, PresentationError, layout_limits

METADATA = re.compile(
    r"<!--\s*presentation:\s*id=([^;\s]+)\s*;\s*layout=([^\s;]+)\s*-->"
)


def canonical_json(value: Any) -> bytes:
    # Python codec names are case-insensitive, so the uppercase UTF-8 spelling is equivalent.
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest_path(path: Path) -> str:
    digest = hashlib.sha256()
    if path.is_file():
        digest.update(path.read_bytes())
    else:
        for item in sorted(path.rglob("*")):
            if item.is_file():
                digest.update(item.relative_to(path).as_posix().encode() + b"\0")
                digest.update(item.read_bytes())
    return digest.hexdigest()


def digest_render_artifacts(render: Path) -> str:
    """Digest rendered output, excluding its self-describing result record."""
    digest = hashlib.sha256()
    for item in sorted(render.rglob("*")):
        if item.is_file() and item.name != "render-result.json":
            digest.update(item.relative_to(render).as_posix().encode() + b"\0")
            digest.update(item.read_bytes())
    return digest.hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    atomic_bytes(path, canonical_json(value) + b"\n")


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def validate_source_metadata(source: Path, manifest: dict[str, Any]) -> None:
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise PresentationError(f"source is unreadable: {source}") from exc
    found = METADATA.findall(text)
    expected = [(slide["id"], slide["layout"]) for slide in manifest["slides"]]
    if len(found) != len(expected):
        raise PresentationError("source metadata is absent, duplicated, or incomplete")
    ids = [item[0] for item in found]
    if len(set(ids)) != len(ids):
        raise PresentationError("source metadata has duplicated slide id")
    for slide_id, layout in found:
        if layout not in LAYOUT_NAMES:
            raise PresentationError(
                f"source metadata names unregistered layout: {layout}"
            )
    if found != expected:
        raise PresentationError(
            "source metadata does not match manifest slide IDs/layouts"
        )


def materialize_slidev_source(
    source: Path, manifest: dict[str, Any], destination: Path
) -> Path:
    """Add generated Slidev frontmatter while preserving canonical authored Markdown."""
    validate_source_metadata(source, manifest)
    text = source.read_text(encoding="utf-8")
    matches = list(METADATA.finditer(text))
    if text[: matches[0].start()].strip():
        raise PresentationError("source content before first presentation metadata")
    blocks: list[str] = []
    registry = layout_limits()
    total = len(manifest["slides"])
    for index, (match, slide) in enumerate(
        zip(matches, manifest["slides"], strict=True)
    ):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[match.start() : end].strip()
        block = re.sub(r"\n\s*---\s*$", "", block).rstrip()
        named_slots = set(re.findall(r"(?m)^::([a-z][a-z0-9-]*)::\s*$", block))
        slots = {"body", *named_slots}
        contract = registry[slide["layout"]]
        missing = set(contract["required_slots"]) - slots
        if missing:
            raise PresentationError(
                f"slide {slide['id']} missing required named slot(s): "
                + ", ".join(sorted(missing))
            )
        undeclared = named_slots - set(slide["regions"])
        if undeclared:
            raise PresentationError(
                f"slide {slide['id']} uses undeclared named slot(s): "
                + ", ".join(sorted(undeclared))
            )
        metadata = {
            "layout": slide["layout"],
            "presentationId": slide["id"],
            "section": slide.get("section", ""),
            "timeCue": slide.get("time_cue", ""),
            "presentationOrder": slide["order"],
            "presentationTotal": total,
        }
        frontmatter = "\n".join(
            f"{key}: {value if key == 'layout' else json.dumps(value)}"
            for key, value in metadata.items()
        )
        blocks.append(f"---\n{frontmatter}\n---\n\n{block}\n")
    destination.write_text("\n".join(blocks), encoding="utf-8")
    return destination


def commit_directory(source: Path, target: Path) -> None:
    """Atomically add a new immutable directory without replacing an existing one."""
    if target.exists():
        raise PresentationError(f"immutable output already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, target)
