#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "typer>=0.12",
#     "pydantic-settings>=2.2",
#     "skill-cli-foundation",
# ]
#
# [tool.uv.sources]
# # Path stays INSIDE the symlinked skills tree (never escapes via ../../../) --
# # resolves whether invoked by real path or through the ~/.claude/skills symlink
# # (PROJECT-1234 finding D). Editable so the shared lib is live-edited, no publish cycle.
# skill-cli-foundation = { path = "../../../packages/skill-cli-foundation", editable = true }
# ///
"""Deterministic half of the /meeting-analysis skill: kind selection and path derivation.

The skill owns the judgment (reading frames, settling a mishearing, writing the
analysis). This module owns the parts that must not be re-derived by hand every
meeting, because each of them has already been gotten wrong at least once:

  * WHICH of the two doc kinds applies. Two kinds share the meetings tree and have
    separate templates, and bending one into the other's shape is the known failure.
  * WHERE the doc goes, including the deliberately duplicated date and the author
    segment that keeps two writers from colliding.
  * WHETHER a doc is already there. A committed analysis is never renamed, so a
    second run must refuse rather than invent a free name.
  * WHAT each supplied source actually is, and which ones no step can consume.
  * WHETHER this checkout's copy of the unit still matches the canonical one.

Nothing here reads a recording, runs ffmpeg, or writes an analysis doc. It reports;
the caller decides.

Subcommands:
    plan          resolve kind + paths + source inventory for a meeting  (--json)
    extract       unpack a zip of sources into the meeting's data/ dir
    vendor-check  verify a vendored copy of this unit against its lock file
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import zipfile
from pathlib import Path
from typing import Annotated

import typer
from pydantic_settings import SettingsConfigDict
from skill_cli_foundation import BaseSkillSettings, make_app

app = make_app("meeting-analysis")

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "meeting-analysis" / "config.toml"

# The repo this script itself ships in, derived from its own resolved location.
# .resolve() is required, not cosmetic: ~/.claude/skills (or ~/.claude/skills/<skill>)
# is a symlink into a repo's skills/ tree, so without it the parents walk lands in
# ~/.claude and the skill's own templates are invisible.
SKILL_REPO_ROOT = Path(os.environ["CLAUDE_PLUGIN_ROOT"]) / "assets"

# --------------------------------------------------------------------------- kinds

RECORDING_ANALYSIS = "recording-analysis"
STANDUP_UPDATE = "standup-update"


@dataclasses.dataclass(frozen=True)
class Kind:
    """One of the two doc kinds that share the meetings directory tree."""

    name: str
    slug_stem: str
    meeting_type: str
    template_relpath: str
    template_version: str

    def slug(self, series: str, date: str) -> str:
        return f"{series}-{self.slug_stem}-{date}"


KINDS: dict[str, Kind] = {
    RECORDING_ANALYSIS: Kind(
        name=RECORDING_ANALYSIS,
        slug_stem="standup-analysis",
        meeting_type="standup",
        template_relpath="docs/meetings/TEMPLATE.md",
        template_version="meeting-analysis-1.1.0",
    ),
    STANDUP_UPDATE: Kind(
        name=STANDUP_UPDATE,
        slug_stem="standup-update",
        meeting_type="standup-update",
        template_relpath="docs/meetings/standups/TEMPLATE.md",
        template_version="standup-update-2.3.0",
    ),
}

TEMPLATE_RELPATHS = tuple(kind.template_relpath for kind in KINDS.values())

# ------------------------------------------------------------------------- sources

# Kept identical to the repo .gitignore's meeting block on purpose: "is a recording"
# and "is ignored" must be one list, or a new container type starts getting committed.
RECORDING_SUFFIXES = frozenset({".mp4", ".mov", ".mkv", ".wav", ".m4a", ".mp3"})
TRANSCRIPT_SUFFIXES = frozenset({".vtt"})
NOTES_SUFFIXES = frozenset({".pdf"})

# Reasons a supplied file cannot be used, so the skill can say so instead of
# quietly ignoring it. A silently dropped source reads as an analyzed one.
UNUSABLE_REASONS = {
    ".docx": "no extraction step is implemented for docx; workflow step 3 runs "
    "pdftotext -layout on a PDF. Export the recap as PDF, or read it by hand.",
    ".doc": "no extraction step is implemented for doc; export as PDF.",
    ".srt": "only WebVTT is parsed (transcript_diff.py reads <v Name> cue tags); convert to .vtt or treat as notes.",
    ".txt": "ambiguous: could be notes or an already-extracted transcript. Name "
    "its role explicitly rather than letting it be guessed.",
}

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class MeetingPathError(Exception):
    """A caller-facing failure: bad input, or a refusal to overwrite."""


@dataclasses.dataclass(frozen=True)
class Source:
    """One supplied file, classified."""

    path: Path
    role: str  # recording | transcript | notes | unusable
    reason: str = ""

    @property
    def size(self) -> int:
        return self.path.stat().st_size if self.path.is_file() else 0


def classify_source(path: Path) -> Source:
    """Classify one file by suffix, never by name.

    Vendor filenames are not a signal: one bundle can contain `[Team Sync] Weekly.vtt`
    with no date and `[Team Sync] Weekly 2026-07-30.pdf` with one. They are also
    never renamed, so the brackets and spaces stay.
    """
    suffix = path.suffix.lower()
    if suffix in RECORDING_SUFFIXES:
        return Source(path, "recording")
    if suffix in TRANSCRIPT_SUFFIXES:
        return Source(path, "transcript")
    if suffix in NOTES_SUFFIXES:
        return Source(path, "notes")
    reason = UNUSABLE_REASONS.get(suffix, f"unrecognized source type '{suffix or path.name}'")
    return Source(path, "unusable", reason)


def inventory(source_dir: Path) -> list[Source]:
    """Classify every file directly inside `source_dir`, sorted by name.

    Deliberately NOT recursive: a nested tree here means the zip carried a
    directory layout we have not seen, which the caller should look at rather
    than have flattened silently.
    """
    if not source_dir.is_dir():
        raise MeetingPathError(f"not a directory: {source_dir}")
    return [classify_source(p) for p in sorted(source_dir.iterdir()) if p.is_file()]


def select_kind(sources: list[Source], override: str | None = None) -> tuple[Kind, str]:
    """Pick the doc kind. Returns (kind, the reason it was picked).

    THE DISCRIMINATOR IS WHETHER THERE IS A MEETING RECORDING OR TRANSCRIPT TO
    ANALYZE. That is what the recording-analysis template presupposes: sections
    like "What the recording shows that the transcript does not" and "Terminology
    and transcription corrections" have no meaning without one, and filling them
    with "not applicable" is bending the template rather than choosing the other.

    A standup update is the kind with no meeting artifacts at all -- it is written
    BEFORE the meeting from our own repository (git history, the task store, audit
    output), which is why its discipline is a per-claim evidence tier rather than
    cross-source checking.

    An explicit override always wins: the operator may be writing tomorrow's update
    while yesterday's recording is still sitting on disk.
    """
    if override is not None:
        if override not in KINDS:
            raise MeetingPathError(f"unknown kind '{override}'; expected one of {sorted(KINDS)}")
        return KINDS[override], "explicit --kind override"

    analyzable = [s for s in sources if s.role in ("recording", "transcript")]
    if analyzable:
        roles = sorted({s.role for s in analyzable})
        return KINDS[RECORDING_ANALYSIS], f"a meeting {' and '.join(roles)} was supplied"
    if any(s.role == "notes" for s in sources):
        return (
            KINDS[RECORDING_ANALYSIS],
            (
                "only a notes export was supplied: still a meeting artifact, so this is "
                "a recording analysis with missing modalities, not an update. Treat the "
                "notes as untrusted and delete the sections nothing corroborates."
            ),
        )
    return KINDS[STANDUP_UPDATE], "no recording, transcript or notes was supplied"


def missing_modalities(sources: list[Source]) -> list[str]:
    """Which analysis inputs are absent, for the doc's degradation note."""
    present = {s.role for s in sources}
    return [role for role in ("recording", "transcript", "notes") if role not in present]


# --------------------------------------------------------------------------- paths


def kebab(name: str) -> str:
    """Kebab-case an author name: 'Rivera, Ada' -> 'rivera-ada'."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", name.strip().lower())
    return cleaned.strip("-")


def validate_date(date: str) -> str:
    """ISO dates only, so the directory and the filename both sort as text."""
    if not DATE_RE.match(date):
        raise MeetingPathError(f"date must be YYYY-MM-DD, got '{date}'")
    return date


def singular_group(group: str) -> str:
    """'standups' -> 'standup'. The directory segment is plural, frontmatter is not."""
    return group.removesuffix("s")


@dataclasses.dataclass(frozen=True)
class MeetingPaths:
    """Every path a run needs, all derived from one (group, author, date, slug)."""

    repo_root: Path
    group: str
    author: str
    date: str
    slug: str
    kind: Kind

    @property
    def date_dir(self) -> Path:
        return self.repo_root / "docs" / "meetings" / self.group / self.author / self.date

    @property
    def doc(self) -> Path:
        # The date appears in the directory AND the filename, deliberately. The
        # directory needs it so a series sorts and so the doc sits beside its own
        # data/ and derived/. The filename needs it because the filename is what
        # survives leaving the directory -- in an editor tab, a link, a PDF export
        # or an attachment, the parent directory is no longer visible.
        return self.date_dir / f"{self.slug}.md"

    @property
    def data_dir(self) -> Path:
        return self.date_dir / "data"

    @property
    def derived_dir(self) -> Path:
        return self.date_dir / "derived"

    @property
    def frames_dir(self) -> Path:
        return self.date_dir / "frames"

    @property
    def meeting_type(self) -> str:
        """Frontmatter meeting_type. Not the same string as the directory segment.

        A recording analysis takes the singular of its group (reviews/ -> review);
        an update is always `standup-update` regardless of where it sits.
        """
        if self.kind.name == STANDUP_UPDATE:
            return self.kind.meeting_type
        return singular_group(self.group)


def derive_paths(
    repo_root: Path,
    kind: Kind,
    author: str,
    date: str,
    series: str,
    group: str = "standups",
    slug: str | None = None,
) -> MeetingPaths:
    """Build the path set, refusing anything the convention does not cover."""
    date = validate_date(date)
    author_seg = kebab(author)
    if not author_seg:
        raise MeetingPathError("author must contain at least one alphanumeric character")
    if slug is None:
        # Only the standups group has documented slug stems. Inventing one for
        # reviews/ or workshops/ would create a series name nobody agreed to, and
        # a slug is what makes a series greppable -- so make the caller say it.
        if group != "standups":
            raise MeetingPathError(
                f"no documented slug stem for group '{group}'; pass an explicit --slug "
                f"(the documented stems are '{KINDS[RECORDING_ANALYSIS].slug_stem}' and "
                f"'{KINDS[STANDUP_UPDATE].slug_stem}', both under 'standups')"
            )
        if not series:
            raise MeetingPathError("--series is required to derive a slug (e.g. 'project-sync')")
        slug = kind.slug(kebab(series), date)
    return MeetingPaths(
        repo_root=repo_root,
        group=group,
        author=author_seg,
        date=date,
        slug=slug,
        kind=kind,
    )


def existing_doc_conflict(paths: MeetingPaths) -> str | None:
    """Report an already-written doc for this slot, or None.

    A committed doc is NEVER renamed: it is cited from design docs, audits, other
    analyses and PR descriptions, several of them by line number. So a second run
    on the same slot is either a refine-in-place or a mistake, and it is not this
    tool's call which. Say so and stop.
    """
    if paths.doc.exists():
        return (
            f"{paths.doc} already exists. A committed analysis doc is never renamed "
            f"or duplicated -- refine it in place, or pick the correct date/author. "
            f"Check inbound links before touching its name: "
            f"grep -rn '{paths.slug}' --include='*.md'"
        )
    return None


# ----------------------------------------------------------------- zip extraction


def safe_members(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """Regular file members with no path traversal and no directory nesting.

    Vendor bundles are flat. Anything absolute, or containing '..', or nested, is
    rejected rather than normalized: a zip that does not look like a vendor bundle
    is a thing to look at, not to silently reshape.
    """
    keep: list[zipfile.ZipInfo] = []
    for info in zf.infolist():
        if info.is_dir():
            continue
        name = info.filename
        as_path = Path(name)
        if as_path.is_absolute() or ".." in as_path.parts:
            raise MeetingPathError(f"refusing unsafe zip member path: {name!r}")
        keep.append(info)
    return keep


def extract_sources(archive: Path, dest: Path, overwrite: bool = False) -> list[Path]:
    """Extract a flat zip of vendor sources into `dest`, keeping vendor filenames.

    Filenames are preserved exactly, brackets, spaces and all, so each stays
    traceable to the vendor artifact it came from. Only the basename is kept, so a
    bundle that nests one directory deep still lands flat.
    """
    if not zipfile.is_zipfile(archive):
        raise MeetingPathError(f"not a zip archive: {archive}")
    dest.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    with zipfile.ZipFile(archive) as zf:
        for info in safe_members(zf):
            target = dest / Path(info.filename).name
            if target.exists() and not overwrite:
                raise MeetingPathError(
                    f"{target} already exists; pass --overwrite to replace it. "
                    f"Vendor sources are the source of record -- overwriting one "
                    f"silently changes what every claim in the doc was checked against."
                )
            with zf.open(info) as src, open(target, "wb") as out:
                out.write(src.read())
            written.append(target)
    return written


# --------------------------------------------------------- template resolution


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_template(kind: Kind, repo_root: Path) -> dict:
    """Resolve which template file to fill, and say where it came from.

    Two copies can exist and the CURRENT repo's committed one wins. That is
    deliberate and narrow: a repo that publishes its meeting docs to teammates MUST
    keep a real committed template, because its meetings README links it relatively
    for readers who have no checkout of this skill's own repo at all, and a relative
    link out of a repo is dead on GitHub. So this reports a `drifted` flag whenever
    both exist and differ, rather than silently preferring one.

    The fallback is the SKILL'S OWN repo, found from this file's resolved location
    rather than by searching the filesystem or reading an environment variable:
    whichever checkout is answering the call is the one whose templates the caller
    already trusted by running this script.
    """
    local = repo_root / kind.template_relpath
    canonical = SKILL_REPO_ROOT / kind.template_relpath
    result = {
        "kind": kind.name,
        "template_version": kind.template_version,
        "canonical": str(canonical),
        "canonical_exists": canonical.is_file(),
        "local": str(local),
        "local_exists": local.is_file(),
    }
    if local.is_file():
        result["use"] = str(local)
        result["source"] = "repo-local"
        if canonical.is_file():
            result["drifted"] = _digest(local) != _digest(canonical)
    elif canonical.is_file():
        result["use"] = str(canonical)
        result["source"] = "skill-repo"
        result["drifted"] = False
    else:
        raise MeetingPathError(f"no template found for kind '{kind.name}' at either {local} or {canonical}")
    return result


# ------------------------------------------------------------- vendor lock check

VENDOR_LOCK_RELPATH = "skills/vendor-lock.json"


def _excluded_from_lock(rel: str) -> bool:
    """Paths a lock never records, and this sweep must therefore not demand.

    Kept identical to the sync tool's exclusion set. If the two disagree, every
    `vendor-check` reports the difference as an UNLOCKED file and the check becomes
    noise the operator learns to ignore. `__pycache__` and dot-directories
    (`.ruff_cache`, `.pytest_cache`) are gitignored build residue that no fresh clone
    would contain, and a lock cannot record its own digest.
    """
    if rel == VENDOR_LOCK_RELPATH:
        return True
    return any(part == "__pycache__" or part.startswith(".") for part in Path(rel).parts)


def _lock_covered_files(target_root: Path, unit_globs: list[str]) -> list[str]:
    """Every file in `target_root` a unit glob matches, as sorted posix relpaths.

    The walk is scoped to each glob's own prefix rather than sweeping the whole repo:
    a consumer root holds `.git`, a virtualenv and its own source tree, none of which
    a unit glob can match, and walking them costs seconds for no possible hit.

    Deliberately self-contained rather than importing the sync tool: the sync tool
    lives in the canonical repo and is NOT part of the vendored unit, so a consumer
    checkout running `vendor-check` has no copy of it to import.
    """
    matches: set[str] = set()
    for pattern in unit_globs:
        if pattern.endswith("/**"):
            base = target_root / pattern[: -len("/**")]
            candidates = base.rglob("*") if base.is_dir() else []
        else:
            candidates = target_root.glob(pattern)
        for path in candidates:
            if not path.is_file():
                continue
            rel = path.relative_to(target_root).as_posix()
            if not _excluded_from_lock(rel):
                matches.add(rel)
    return sorted(matches)


def vendor_check(target_root: Path) -> tuple[int, list[str]]:
    """Verify a vendored copy of this unit against its lock. Returns (exit, lines)."""
    lock_path = target_root / VENDOR_LOCK_RELPATH
    if not lock_path.is_file():
        return 0, [
            (
                f"no {VENDOR_LOCK_RELPATH} in {target_root}: this is a canonical checkout, "
                "not a vendored copy, so there is nothing to verify against. A lock file is "
                "written only into a consumer repo by the sync tool."
            ),
        ]
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MeetingPathError(f"cannot read {lock_path}: {exc}") from exc

    entries = lock.get("files") or []
    unit_globs = lock.get("unit_globs") or []
    problems: list[str] = []
    locked_paths: set[str] = set()
    for entry in entries:
        rel = str(entry.get("path", ""))
        locked_paths.add(rel)
        actual = target_root / rel
        if not actual.is_file():
            problems.append(f"MISSING   {rel}")
            continue
        if _digest(actual) != str(entry.get("sha256", "")):
            problems.append(f"MODIFIED  {rel}")
    # The glob sweep is what catches an ADDED file. A per-entry loop can only ever
    # find what the lock already knows about, so on its own it reports a fork that
    # gained a file as a clean tree.
    for rel in _lock_covered_files(target_root, unit_globs):
        if rel not in locked_paths:
            problems.append(f"UNLOCKED  {rel}")

    if problems:
        source = lock.get("source_commit", "unknown")
        return 1, [
            f"vendored unit does not match {VENDOR_LOCK_RELPATH} (source_commit {source}):",
            *sorted(problems),
            (
                "Re-sync from the canonical repo, or land the change there first -- the "
                "canonical copy is the one every other consumer is verified against."
            ),
        ]
    return 0, [f"{len(entries)} file(s) match {VENDOR_LOCK_RELPATH}"]


# ------------------------------------------------------------------------ settings


class MeetingResolveSettings(BaseSkillSettings):
    """Layered defaults for the fields an operator repeats every meeting.

    Precedence (CLI > env `MEETING_ANALYSIS_*` > TOML > default) comes from
    `BaseSkillSettings`. Only the repeated identity fields are layered: the date and
    the source directory are per-run facts and are never defaulted, because a stale
    default date would write a document into the wrong day's directory.
    """

    model_config = SettingsConfigDict(
        env_prefix="MEETING_ANALYSIS_",
        toml_file=DEFAULT_CONFIG_PATH,
        extra="ignore",
    )

    author: str = ""
    series: str = ""
    group: str = "standups"


def resolve_settings(
    *,
    author: str | None = None,
    series: str | None = None,
    group: str | None = None,
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> MeetingResolveSettings:
    """Resolve the layered fields through CLI > env > TOML > default.

    `config_path` is injectable so tests drive the real precedence chain instead of
    a stub of it.
    """

    class _Pinned(MeetingResolveSettings):
        model_config = SettingsConfigDict(
            env_prefix="MEETING_ANALYSIS_",
            toml_file=config_path,
            extra="ignore",
        )

    given = {"author": author, "series": series, "group": group}
    return _Pinned(**{k: v for k, v in given.items() if v is not None})


# ----------------------------------------------------------------------------- cli


def _fail(message: str) -> typer.Exit:
    typer.echo(f"error: {message}", err=True)
    return typer.Exit(1)


RepoRootOption = Annotated[Path, typer.Option("--repo-root", help="Repo the doc will live in")]
SourcesOption = Annotated[Path | None, typer.Option("--sources", help="Directory of already-extracted sources")]
AuthorOption = Annotated[str | None, typer.Option("--author", help="Author name, kebab-cased for the path")]
DateOption = Annotated[str, typer.Option("--date", help="Meeting date, YYYY-MM-DD")]
SeriesOption = Annotated[str | None, typer.Option("--series", help="Series name for the slug, e.g. project-sync")]
GroupOption = Annotated[str | None, typer.Option("--group", help="Meeting-type directory, e.g. standups")]
SlugOption = Annotated[
    str | None, typer.Option("--slug", help="Override the derived slug (required outside standups/)")
]
KindOption = Annotated[
    str | None, typer.Option("--kind", help=f"Override the discriminator: {' | '.join(sorted(KINDS))}")
]
JsonOption = Annotated[bool, typer.Option("--json", help="Machine-readable output")]
ConfigOption = Annotated[Path, typer.Option("--config", help="Per-user TOML holding the layered defaults")]


def build_plan(
    *,
    repo_root: Path,
    sources_dir: Path | None,
    author: str,
    date: str,
    series: str,
    group: str,
    slug: str | None,
    kind_override: str | None,
) -> dict:
    """Resolve kind, template, paths and source inventory into one report."""
    sources: list[Source] = []
    if sources_dir is not None:
        if sources_dir.is_file() and zipfile.is_zipfile(sources_dir):
            raise MeetingPathError(
                f"{sources_dir} is a zip; run `extract` first so the sources land in "
                f"the meeting's data/ directory under their vendor filenames"
            )
        sources = inventory(sources_dir)
    kind, why = select_kind(sources, kind_override)
    paths = derive_paths(
        repo_root=repo_root,
        kind=kind,
        author=author,
        date=date,
        series=series,
        group=group,
        slug=slug,
    )
    return {
        "kind": kind.name,
        "kind_reason": why,
        "meeting_type": paths.meeting_type,
        "template": resolve_template(kind, repo_root),
        "doc": str(paths.doc),
        "date_dir": str(paths.date_dir),
        "data_dir": str(paths.data_dir),
        "derived_dir": str(paths.derived_dir),
        "frames_dir": str(paths.frames_dir),
        "conflict": existing_doc_conflict(paths),
        "missing_modalities": missing_modalities(sources),
        "sources": [{"path": str(s.path), "role": s.role, "reason": s.reason, "bytes": s.size} for s in sources],
    }


def render_plan(report: dict) -> list[str]:
    """Human-readable rendering of a plan report."""
    lines = [
        f"kind          : {report['kind']}  ({report['kind_reason']})",
        f"meeting_type  : {report['meeting_type']}",
        f"template      : {report['template']['use']}  [{report['template']['source']}]",
    ]
    if report["template"].get("drifted"):
        lines.append("  WARNING     : this repo's template differs from the skill repo's copy")
    lines += [
        f"doc           : {report['doc']}",
        f"data dir      : {report['data_dir']}",
        f"derived dir   : {report['derived_dir']}",
    ]
    if report["missing_modalities"]:
        lines.append(f"missing       : {', '.join(report['missing_modalities'])}")
    for source in report["sources"]:
        suffix = f"  -- {source['reason']}" if source["reason"] else ""
        lines.append(f"  [{source['role']:10}] {Path(source['path']).name}{suffix}")
    if report["conflict"]:
        lines.append(f"CONFLICT      : {report['conflict']}")
    return lines


@app.command("plan")
def plan(
    date: DateOption,
    repo_root: RepoRootOption = Path("."),
    sources: SourcesOption = None,
    author: AuthorOption = None,
    series: SeriesOption = None,
    group: GroupOption = None,
    slug: SlugOption = None,
    kind: KindOption = None,
    json_output: JsonOption = False,
    config: ConfigOption = DEFAULT_CONFIG_PATH,
) -> None:
    """Resolve kind, paths and source inventory for one meeting."""
    settings = resolve_settings(author=author, series=series, group=group, config_path=config)
    try:
        report = build_plan(
            repo_root=repo_root.resolve(),
            sources_dir=sources,
            author=settings.author,
            date=date,
            series=settings.series,
            group=settings.group,
            slug=slug,
            kind_override=kind,
        )
    except MeetingPathError as exc:
        raise _fail(str(exc)) from exc
    if json_output:
        typer.echo(json.dumps(report, indent=2))
    else:
        for line in render_plan(report):
            typer.echo(line)
    if report["conflict"]:
        # Exit 2, not 1: a conflict is a well-formed answer the caller must act on,
        # not a bad invocation, and the skill branches on the difference.
        raise typer.Exit(2)


@app.command("extract")
def extract(
    archive: Annotated[Path, typer.Argument(help="The zip of vendor sources")],
    dest: Annotated[Path, typer.Argument(help="The meeting's data/ directory")],
    overwrite: Annotated[bool, typer.Option("--overwrite", help="Replace existing files")] = False,
) -> None:
    """Unpack a zip of vendor sources into a meeting's data/ directory."""
    try:
        written = extract_sources(archive, dest, overwrite=overwrite)
    except MeetingPathError as exc:
        raise _fail(str(exc)) from exc
    for path in written:
        typer.echo(f"extracted: {path}")
    typer.echo(f"{len(written)} file(s) into {dest}")


@app.command("vendor-check")
def vendor_check_command(
    target: Annotated[Path, typer.Option("--target", help="Root of the repo holding the vendored unit")] = Path("."),
) -> None:
    """Verify a vendored copy of this skill against its lock file."""
    try:
        code, lines = vendor_check(target.resolve())
    except MeetingPathError as exc:
        raise _fail(str(exc)) from exc
    for line in lines:
        typer.echo(line)
    if code:
        raise typer.Exit(code)


if __name__ == "__main__":
    app()
