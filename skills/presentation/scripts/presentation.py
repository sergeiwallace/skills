"""Phase-1 contracts for the local Slidev presentation skill.

This module deliberately has no renderer dependency.  It validates inputs before
touching a deck directory, then provides the lock-backed atomic brief commit that
later workflow stages build on.
"""

from __future__ import annotations

import errno
import json
import os
import platform
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self
from urllib.parse import unquote, urlparse

from jsonschema import Draft202012Validator

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = SKILL_ROOT / "schemas" / "deck.schema.json"
LAYOUTS_PATH = SKILL_ROOT / "layouts" / "layouts.json"
REQUIRED_BRIEF_FIELDS = (
    "audience",
    "outcome",
    "duration_minutes",
    "delivery",
    "classification",
)
REQUIRED_STORY_FIELDS = (
    "audience_problem",
    "promise",
    "sections",
    "example_or_demo",
    "transition",
    "callback",
    "closing_action",
)
LAYOUT_NAMES = (
    "title",
    "thesis",
    "comparison",
    "process",
    "evidence",
    "code",
    "demo",
    "close",
)


class PresentationError(ValueError):
    """A user-correctable presentation contract failure."""


class LockUnavailable(PresentationError):
    """The workflow lock could not be acquired."""


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _parse_frontmatter(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            break
        key, separator, value = line.partition(":")
        if separator and key.strip():
            result[key.strip()] = value.strip()
    return result


def normalize_brief(brief: str | Path) -> str:
    """Read and validate a brief entirely in memory."""
    if isinstance(brief, Path):
        try:
            text = brief.read_text(encoding="utf-8")
        except OSError as exc:
            raise PresentationError(f"brief file is unreadable: {brief}") from exc
    else:
        text = brief
    fields = _parse_frontmatter(text)
    missing = [field for field in REQUIRED_BRIEF_FIELDS if not fields.get(field)]
    if missing:
        raise PresentationError(
            "brief missing required field(s): " + ", ".join(missing)
        )
    try:
        if int(fields["duration_minutes"]) <= 0:
            raise ValueError
    except ValueError as exc:
        raise PresentationError("brief duration_minutes must be positive") from exc
    if fields["audience"] not in {"internal", "external"}:
        raise PresentationError("brief audience must be internal or external")
    if fields["classification"] not in {"local-only", "approved-external"}:
        raise PresentationError(
            "brief classification must be local-only or approved-external"
        )
    return text


def validate_story_contract(
    story: dict[str, Any], duration_minutes: int, exception: str = ""
) -> dict[str, Any]:
    """Require a stage-ready narrative arc for talks longer than twenty minutes."""
    if duration_minutes <= 20 or exception.strip():
        return story
    missing = [
        field
        for field in REQUIRED_STORY_FIELDS
        if not story.get(field)
        or (field == "sections" and not isinstance(story[field], list))
    ]
    if missing:
        raise PresentationError(
            "long-talk narrative missing required beat(s): " + ", ".join(missing)
        )
    if len(story["sections"]) < 2 or any(
        not isinstance(section, dict)
        or any(
            not isinstance(section.get(key), str) or not section[key].strip()
            for key in ("name", "principle", "example")
        )
        for section in story["sections"]
    ):
        raise PresentationError(
            "long-talk narrative sections must name at least two "
            "principle/example beats"
        )
    return story


def validate_research_paths(paths: list[Path]) -> None:
    for path in paths:
        try:
            # The wrapped public error intentionally hides this synthetic OSError's details.
            if not path.is_file():
                raise OSError(errno.ENOENT, "not a regular file")
            path.open("rb").close()
        except OSError as exc:
            raise PresentationError(
                f"research path is missing or unreadable: {path}"
            ) from exc


def probe_platform(system: str | None = None, release: str | None = None) -> None:
    """Reject native Windows before any deck-root mutation."""
    detected = system or platform.system()
    # WSL is accepted through the Linux branch; this retained probe has no behavioral effect.
    is_wsl = (
        detected == "Linux" and "microsoft" in (release or platform.release()).lower()
    )
    # Membership rejection already rejects native Windows, so the explicit comparison is redundant.
    if detected == "Windows" or (detected not in {"Linux", "Darwin"}):
        raise PresentationError(f"unsupported platform for flock(2): {detected}")
    # Linux (including WSL) and macOS expose flock(2).
    _ = is_wsl


def _read_epoch(owner_path: Path) -> int:
    try:
        return int(json.loads(owner_path.read_text(encoding="utf-8")).get("epoch", 0))
    except (OSError, ValueError, json.JSONDecodeError):
        return 0


@dataclass
class DeckLock:
    deck_root: Path
    timeout: float = 30.0
    fd: int | None = None
    token: str | None = None
    epoch: int | None = None

    def _acquire_fd(self, *, create_deck: bool) -> None:
        # Native Windows has no fcntl.  Keep this import after probe_platform() so
        # callers receive the documented platform diagnostic instead of an import crash.
        probe_platform()
        import fcntl

        if create_deck:
            self.deck_root.mkdir(parents=True, exist_ok=True)
        elif not self.deck_root.is_dir():
            raise PresentationError("presentation deck directory is missing")
        lock_path = self.deck_root / ".presentation-workflow.lock"
        self.fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        os.set_inheritable(self.fd, False)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(self.fd)
                    self.fd = None
                    raise LockUnavailable("presentation workflow lock timeout")
                time.sleep(0.01)

    def acquire(self) -> DeckLock:
        self._acquire_fd(create_deck=True)
        owner = self.deck_root / ".presentation-workflow.owner.json"
        self.epoch = _read_epoch(owner) + 1
        self.token = uuid.uuid4().hex
        record = {"token": self.token, "epoch": self.epoch, "pid": os.getpid()}
        _atomic_write(owner, json.dumps(record, sort_keys=True).encode() + b"\n")
        return self

    def reattach(self, token: str, epoch: int) -> DeckLock:
        """Acquire the kernel lock without replacing a candidate's owner identity."""
        self._acquire_fd(create_deck=False)
        owner = self.deck_root / ".presentation-workflow.owner.json"
        try:
            record = json.loads(owner.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self.release()
            raise PresentationError("invalid workflow owner metadata") from exc
        if (record.get("token"), record.get("epoch")) != (token, epoch):
            self.release()
            raise PresentationError("stale workflow owner token or epoch")
        self.token = token
        self.epoch = epoch
        return self

    def release(self) -> None:
        if self.fd is not None:
            import fcntl

            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None

    def __enter__(self) -> Self:
        return self.acquire()

    def __exit__(self, *_: object) -> None:
        self.release()


def prepare_brief(
    brief: str | Path, deck_root: Path, research_paths: list[Path] | None = None
) -> DeckLock:
    """Validate privately, then lock and atomically commit the normalized brief."""
    normalized = normalize_brief(brief)
    validate_research_paths(research_paths or [])
    probe_platform()
    lock = DeckLock(deck_root).acquire()
    try:
        # Python codec names are case-insensitive, so the uppercase UTF-8 spelling is equivalent.
        _atomic_write(deck_root / "brief.md", normalized.encode("utf-8"))
        (deck_root / "current.json").unlink(missing_ok=True)
        return lock
    except BaseException:
        lock.release()
        raise


def _reject_path(value: str, root: Path, label: str) -> Path:
    decoded = unquote(value)
    candidate = Path(decoded)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise PresentationError(f"{label} path must be relative and contained: {value}")
    resolved_root = root.resolve()
    resolved = (root / candidate).resolve()
    if resolved_root not in (resolved, *resolved.parents) or not resolved.is_file():
        raise PresentationError(
            f"{label} path is not a contained regular file: {value}"
        )
    return resolved


def _validate_uri(value: str) -> None:
    if urlparse(value).scheme not in {"http", "https"}:
        raise PresentationError(f"citation URI must use http or https: {value}")


def validate_manifest(
    manifest: dict[str, Any], deck_root: Path, skill_root: Path = SKILL_ROOT
) -> dict[str, Any]:
    """Validate JSON shape and the cross-field/path semantics the schema cannot express."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema).iter_errors(manifest),
        key=lambda item: list(item.path),
    )
    if errors:
        raise PresentationError(
            "manifest schema invalid: " + "; ".join(error.message for error in errors)
        )
    slides = manifest["slides"]
    for key in ("id", "title", "order"):
        values = [slide[key] for slide in slides]
        # Schema validation makes each list homogeneous, so `key=str` is ordering-equivalent.
        duplicates = sorted(
            {value for value in values if values.count(value) > 1}, key=str
        )
        if duplicates:
            raise PresentationError(f"duplicate slide {key}: {duplicates}")
    if [slide["order"] for slide in slides] != list(range(1, len(slides) + 1)):
        raise PresentationError(
            "slide order must be unique, contiguous, and start at 1"
        )
    asset_ids = [asset["id"] for asset in manifest["assets"]]
    duplicates = sorted({value for value in asset_ids if asset_ids.count(value) > 1})
    if duplicates:
        raise PresentationError(f"duplicate asset id: {duplicates}")
    theme = manifest["theme"]
    _reject_path(
        theme["tokens"],
        deck_root if theme.get("root") == "deck" else skill_root,
        "theme",
    )
    if "variant" in theme:
        _reject_path(
            theme["variant"],
            deck_root if theme.get("root") == "deck" else skill_root,
            "theme variant",
        )
    for asset in manifest["assets"]:
        _reject_path(asset["path"], deck_root, "asset")
        if "source_path" in asset:
            _reject_path(asset["source_path"], deck_root, "asset source")
    for slide in slides:
        if slide["layout"] not in LAYOUT_NAMES:
            raise PresentationError(f"unregistered layout: {slide['layout']}")
        if len(slide["regions"]) > layout_limits()[slide["layout"]]["regions"]:
            raise PresentationError(f"layout region limit exceeded: {slide['layout']}")
        contract = layout_limits()[slide["layout"]]
        regions = set(slide["regions"])
        disallowed = regions - set(contract["allowed_slots"])
        if disallowed:
            raise PresentationError(
                f"layout has disallowed slot(s): {slide['layout']}: "
                + ", ".join(sorted(disallowed))
            )
        missing = set(contract["required_slots"]) - regions
        if missing:
            raise PresentationError(
                f"layout missing required slot(s): {slide['layout']}: "
                + ", ".join(sorted(missing))
            )
        # Schema rejection of unknown slide properties makes this defense-in-depth check unreachable.
        if "css" in slide:
            raise PresentationError("direct CSS overrides are not permitted")
        # Schema validation guarantees evidence is a list, so alternate get defaults are unreachable.
        for evidence in slide.get("evidence", []):
            _validate_uri(evidence["source"])
    if manifest["deck"]["duration_minutes"] > 20 and not manifest["deck"].get(
        "narrative_exception"
    ):
        missing_sections = [
            slide["id"] for slide in slides if not slide.get("section", "").strip()
        ]
        if missing_sections:
            raise PresentationError(
                "long-talk slides missing visible section metadata: "
                + ", ".join(missing_sections)
            )
        layouts = {slide["layout"] for slide in slides}
        missing_beats = {"title", "thesis", "demo", "close"} - layouts
        if missing_beats:
            raise PresentationError(
                "long-talk manifest missing hook/route/demo/close layout beat(s): "
                + ", ".join(sorted(missing_beats))
            )
    return manifest


def layout_limits() -> dict[str, dict[str, Any]]:
    return json.loads(LAYOUTS_PATH.read_text(encoding="utf-8"))
