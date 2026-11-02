#!/usr/bin/env python3
"""Archive, validate and locate a rolling Teams message drafts doc.

A drafts doc (this skill's ``templates/TEMPLATE.md``) keeps its
pending entries as ``## [Pending] ...`` sections and files every sent or dropped entry
under ``## Archived Messages`` > ``### Sent Messages`` or ``### Dropped``. Five
``doc:region`` markers delimit what this script owns:

- ``toc`` and ``tracking-table`` are generated: their bodies are rebuilt from the headings
  and the entries' Metadata tables on every ``archive`` run.
- ``pending-messages``, ``sent-messages`` and ``dropped-messages`` hold entries. ``archive``
  files each entry into the region its heading tag names, newest first by draft date, and
  shifts its headings to that region's level (2 for pending, 4 in the archive).

Subcommands::

    teams_drafts.py resolve                 # where the drafts repo and doc are
    teams_drafts.py validate [FILE ...]     # exit 1 and name every offending entry
    teams_drafts.py set-status KEY TAG ...  # flip one entry's tag and Metadata
    teams_drafts.py archive [--dry-run]     # move, regenerate, validate, then write

``set-status`` and ``archive`` refuse a working tree with uncommitted or untracked
changes, so a wrong run is always undone with ``git restore`` alone, and refuse the
repo's main working tree. ``archive`` writes nothing unless its result validates.

Location comes from ``~/.config/teams-message-draft/config.toml`` (or the file
``TEAMS_DRAFTS_CONFIG`` names), read at call time, with the precedence
flags > environment > ``[profiles.<TEAMS_DRAFTS_PROFILE>]`` > ``[defaults]``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from markdown_headings import parse_headings, strip_inline_markdown  # noqa: E402

DEFAULT_CONFIG_PATH = Path("~/.config/teams-message-draft/config.toml")
ENV_CONFIG = "TEAMS_DRAFTS_CONFIG"
ENV_PROFILE = "TEAMS_DRAFTS_PROFILE"
ENV_PROJECT = "TEAMS_DRAFTS_PROJECT"
ENV_REPO = "TEAMS_DRAFTS_REPO"
ENV_FILE = "TEAMS_DRAFTS_FILE"

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_REFUSED = 2

TAGS = ("Pending", "Sent", "Dropped")
ENTRY_REGIONS = {
    "Pending": "pending-messages",
    "Sent": "sent-messages",
    "Dropped": "dropped-messages",
}
REGION_LEVEL = {"pending-messages": 2, "sent-messages": 4, "dropped-messages": 4}
REGION_KINDS = {
    "toc": "generated",
    "pending-messages": "entries",
    "sent-messages": "entries",
    "dropped-messages": "entries",
    "tracking-table": "generated",
}
REGION_ORDER = tuple(REGION_KINDS)
ARCHIVE_HEADING = "Archived Messages"
ARCHIVE_SUBHEADING = {"sent-messages": "Sent Messages", "dropped-messages": "Dropped"}
DRAFT = "AI-generated draft"
SUBSECTIONS = (
    "Metadata",
    DRAFT,
    "Message actually sent",
    "Notes on the differences",
)
REQUIRED_FIELDS = ("Status", "Drafted", "Last updated", "Sent")
EMPTY_REGION_LINE = "No entries yet."
SEPARATOR = "---"
TRACKING_HEADER = (
    "| Entry | Status | Drafted | Last updated | Sent |",
    "|---|---|---|---|---|",
)
TOC_MIN_LEVEL = 2
MAX_HEADING_LEVEL = 6

_FENCE = re.compile(r"^(```+|~~~+)")
_HEADING = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<text>.*?)\s*$")
_ENTRY = re.compile(
    r"^(?P<hashes>#{1,6}) \[(?P<tag>[A-Za-z]+)\] "
    r"(?P<date>\d{4}-\d{2}-\d{2}) - (?P<title>.+?)\s*$"
)
_MARKER_ANY = re.compile(r"^\s*<!--\s*/?\s*doc:region\b")
_MARKER_BEGIN = re.compile(
    r'^<!-- doc:region name="(?P<name>[^"]+)" kind="(?P<kind>[^"]+)" -->$'
)
_MARKER_END = re.compile(r'^<!-- /doc:region name="(?P<name>[^"]+)" -->$')
_SUBSECTION = re.compile(r"^(?P<name>.+?) \((?P<key>\d{4}-\d{2}-\d{2} [^()]+)\)$")
_ROW = re.compile(r"^\|\s*(?P<field>[^|]+?)\s*\|\s*(?P<value>.*?)\s*\|\s*$")
_LEADING_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})\b")
# A line that starts a new block, so it cannot be a lazy blockquote continuation.
_BLOCK_START = re.compile(r"^([-*+]\s|\d{1,9}[.)]\s|```|~~~|<|\|)")


class Refusal(Exception):
    """The command will not run against this target."""


# ---------------------------------------------------------------------------
# Configuration


@dataclass(frozen=True)
class Overrides:
    """The flag layer of the location precedence; None means "not given"."""

    config: str | None = None
    project: str | None = None
    profile: str | None = None
    repo: str | None = None
    drafts_file: str | None = None


@dataclass(frozen=True)
class Location:
    project: str
    repo: Path
    drafts_file: Path
    screenshots_dir: Path | None
    config_path: Path

    def as_dict(self) -> dict[str, str | None]:
        return {
            "project": self.project,
            "repo": str(self.repo),
            "drafts_file": str(self.drafts_file),
            "screenshots_dir": str(self.screenshots_dir)
            if self.screenshots_dir
            else None,
            "config": str(self.config_path),
        }


def _expand(value: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(value)))


def load_config(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError as exc:
        raise Refusal(f"config file not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise Refusal(f"config file {path} is not valid TOML: {exc}") from exc


def _project_settings(
    data: dict, flags: Overrides, env: dict[str, str]
) -> tuple[str, dict[str, str]]:
    defaults = data.get("defaults", {})
    profile_name = flags.profile or env.get(ENV_PROFILE, "")
    profile_table = (
        data.get("profiles", {}).get(profile_name, {}) if profile_name else {}
    )
    project = (
        flags.project
        or env.get(ENV_PROJECT)
        or profile_table.get("project")
        or defaults.get("project")
    )
    if not project:
        raise Refusal("no project given and no default project in the config")
    settings: dict[str, str] = {}
    settings.update(defaults.get("projects", {}).get(project, {}))
    settings.update(profile_table.get("projects", {}).get(project, {}))
    return project, settings


def resolve_location(
    flags: Overrides, environ: dict[str, str] | None = None
) -> Location:
    """Resolve the drafts repo and doc: flags > environment > [profiles.<p>] > [defaults]."""
    env = os.environ if environ is None else environ
    explicit_config = flags.config or env.get(ENV_CONFIG)
    config_path = _expand(explicit_config or str(DEFAULT_CONFIG_PATH))
    file_value = flags.drafts_file or env.get(ENV_FILE)
    repo_value = flags.repo or env.get(ENV_REPO)
    if explicit_config or config_path.is_file():
        project, settings = _project_settings(load_config(config_path), flags, env)
    elif repo_value and file_value:
        # Both locations given outright: no config file is needed to find the doc.
        project, settings = flags.project or env.get(ENV_PROJECT) or "", {}
    else:
        raise Refusal(
            f"config file not found: {config_path} (copy the skill's "
            "config.example.toml there, or pass --repo and --file)"
        )
    if not settings and not repo_value:
        raise Refusal(f"project {project!r} is not configured in {config_path}")

    if file_value and _expand(file_value).is_absolute() and not repo_value:
        file_path = _expand(file_value)
        repo_path = _git_toplevel(file_path.parent)
    else:
        repo_value = repo_value or settings.get("repo")
        relative = file_value or settings.get("drafts_file")
        if not repo_value or not relative:
            raise Refusal(
                f"project {project!r} needs both repo and drafts_file in {config_path}"
            )
        repo_path = _expand(repo_value)
        file_path = _expand(relative)
        if not file_path.is_absolute():
            file_path = repo_path / file_path
    screenshots = settings.get("screenshots_dir")
    return Location(
        project=project,
        repo=repo_path,
        drafts_file=file_path,
        screenshots_dir=(repo_path / screenshots) if screenshots else None,
        config_path=config_path,
    )


# ---------------------------------------------------------------------------
# Git guards


def _stdout(result: subprocess.CompletedProcess[str], where: Path) -> str:
    if result.returncode != 0:
        raise Refusal(
            f"{' '.join(result.args)} failed in {where}: {result.stderr.strip()}"
        )
    return result.stdout


def _git_toplevel(directory: Path) -> Path:
    result = subprocess.run(
        ["git", "-C", str(directory), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    return Path(_stdout(result, directory).strip())


def require_clean_worktree(repo: Path) -> None:
    """Refuse a main working tree, and any tree with uncommitted or untracked changes."""
    dirs = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--absolute-git-dir", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=False,
    )
    git_dir, common_dir = _stdout(dirs, repo).splitlines()
    common = Path(common_dir)
    if not common.is_absolute():
        common = repo / common
    if Path(git_dir).resolve() == common.resolve():
        raise Refusal(
            f"{repo} is the repo's main working tree; edit the drafts doc in a "
            "worktree of its repo (git -C <repo> worktree add ...) and pass "
            "--repo <worktree>"
        )
    porcelain = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all"],
        capture_output=True,
        text=True,
        check=False,
    )
    status = _stdout(porcelain, repo)
    if status.strip():
        listing = "\n".join(f"    {line}" for line in status.splitlines())
        raise Refusal(
            f"{repo} has uncommitted changes; commit them (Teams screenshots "
            "included) before running, so a wrong run can be reverted with git "
            f"restore alone:\n{listing}"
        )


# ---------------------------------------------------------------------------
# Parsing


def code_lines(lines: list[str]) -> set[int]:
    """Indices of lines inside fenced code blocks, fence lines included."""
    inside: set[int] = set()
    fence: str | None = None
    for idx, raw in enumerate(lines):
        match = _FENCE.match(raw.strip())
        if match:
            inside.add(idx)
            if fence is None:
                fence = match.group(1)[0] * 3
            elif raw.strip().startswith(fence):
                fence = None
            continue
        if fence is not None:
            inside.add(idx)
    return inside


@dataclass
class Region:
    name: str
    kind: str
    begin: int
    end: int


@dataclass
class Entry:
    tag: str
    date: str
    title: str
    level: int
    lines: list[str]
    order: int
    region: str
    line: int
    key: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    subsections: list[tuple[str, str | None]] = field(default_factory=list)

    @property
    def heading(self) -> str:
        return f"[{self.tag}] {self.date} - {self.title}"

    @property
    def label(self) -> str:
        return f"entry '{self.heading}' (line {self.line})"


@dataclass
class Doc:
    lines: list[str]
    regions: dict[str, Region]
    entries: list[Entry]
    errors: list[str]
    code: set[int]


@dataclass
class _RegionScan:
    regions: dict[str, Region] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    open: tuple[str, str, int] | None = None

    def begin(self, idx: int, name: str, kind: str) -> None:
        lineno = idx + 1
        if kind != REGION_KINDS[name]:
            self.errors.append(
                f"line {lineno}: region {name!r} has kind {kind!r}, "
                f"expected {REGION_KINDS[name]!r}"
            )
        if self.open is not None:
            self.errors.append(
                f"line {lineno}: region {name!r} opens inside unclosed "
                f"region {self.open[0]!r}"
            )
        elif name in self.regions:
            self.errors.append(f"line {lineno}: region {name!r} opened a second time")
        else:
            self.open = (name, kind, idx)

    def end(self, idx: int, name: str) -> None:
        if self.open is None or self.open[0] != name:
            self.errors.append(
                f"line {idx + 1}: end marker for {name!r} with no matching begin"
            )
            return
        self.regions[name] = Region(
            name=name, kind=self.open[1], begin=self.open[2], end=idx
        )
        self.open = None

    def finish(self) -> None:
        if self.open is not None:
            self.errors.append(
                f"line {self.open[2] + 1}: region {self.open[0]!r} is never closed"
            )
        for name in REGION_KINDS:
            if name not in self.regions and not any(
                repr(name) in e for e in self.errors
            ):
                self.errors.append(f"missing marker region {name!r}")
        present = [name for name in REGION_ORDER if name in self.regions]
        if sorted(present, key=lambda n: self.regions[n].begin) != present:
            self.errors.append(
                f"marker regions out of order: expected {' < '.join(REGION_ORDER)}"
            )


def parse_regions(
    lines: list[str], code: set[int]
) -> tuple[dict[str, Region], list[str]]:
    scan = _RegionScan()
    for idx, raw in enumerate(lines):
        if idx in code or not _MARKER_ANY.match(raw):
            continue
        begin = _MARKER_BEGIN.match(raw.strip())
        end = _MARKER_END.match(raw.strip())
        match = begin or end
        if match is None:
            scan.errors.append(
                f"line {idx + 1}: malformed doc:region marker: {raw.strip()}"
            )
        elif match.group("name") not in REGION_KINDS:
            scan.errors.append(
                f"line {idx + 1}: unknown marker region {match.group('name')!r}"
            )
        elif begin:
            scan.begin(idx, begin.group("name"), begin.group("kind"))
        else:
            scan.end(idx, match.group("name"))
    scan.finish()
    return scan.regions, scan.errors


def _metadata(chunk: list[str], meta_offset: int | None) -> dict[str, str]:
    fields: dict[str, str] = {}
    if meta_offset is None:
        return fields
    in_table = False
    for raw in chunk[meta_offset + 1 :]:
        if _HEADING.match(raw):
            break
        row = _ROW.match(raw)
        if row is None:
            if in_table:
                break
            continue
        in_table = True
        name = row.group("field")
        if name != "Field" and not set(name) <= set("-: "):
            fields.setdefault(name, row.group("value"))
    return fields


def _build_entry(chunk: list[str], region: str, line: int, order: int) -> Entry:
    match = _ENTRY.match(chunk[0])
    assert match is not None
    level = len(match.group("hashes"))
    entry = Entry(
        tag=match.group("tag"),
        date=match.group("date"),
        title=match.group("title"),
        level=level,
        lines=chunk,
        order=order,
        region=region,
        line=line,
    )
    chunk_code = code_lines(chunk)
    meta_offset = None
    for offset, raw in enumerate(chunk[1:], start=1):
        heading = None if offset in chunk_code else _HEADING.match(raw)
        if heading is None or len(heading.group("hashes")) != level + 1:
            continue
        sub = _SUBSECTION.match(heading.group("text"))
        name = sub.group("name") if sub else heading.group("text")
        key = sub.group("key") if sub else None
        entry.subsections.append((name, key))
        if name == "Metadata" and meta_offset is None:
            meta_offset = offset
            entry.key = key
    entry.metadata = _metadata(chunk, meta_offset)
    return entry


def _trim(chunk: list[str]) -> list[str]:
    out = list(chunk)
    while out and not out[-1].strip():
        out.pop()
    if out and out[-1].strip() == SEPARATOR:
        out.pop()
    while out and not out[-1].strip():
        out.pop()
    return out


def _unplaced_entries(
    lines: list[str], code: set[int], entry_regions: list[Region]
) -> list[str]:
    errors = []
    for idx, raw in enumerate(lines):
        match = None if idx in code else _ENTRY.match(raw)
        if match is None:
            continue
        if match.group("tag") not in TAGS:
            errors.append(
                f"line {idx + 1}: entry has unknown tag [{match.group('tag')}]: "
                f"{raw.strip()}"
            )
        if not any(r.begin < idx < r.end for r in entry_regions):
            errors.append(
                f"line {idx + 1}: entry is outside every entries region: {raw.strip()}"
            )
    return errors


def _split_region(
    lines: list[str], code: set[int], region: Region, entries: list[Entry]
) -> list[str]:
    """Append the region's entries to ``entries``; return its structural errors."""
    errors = []
    start: int | None = None
    level = 0
    for idx in range(region.begin + 1, region.end + 1):
        raw = lines[idx]
        is_entry = idx != region.end and idx not in code and _ENTRY.match(raw)
        if idx == region.end or is_entry:
            if start is not None:
                chunk = _trim(lines[start:idx])
                entries.append(
                    _build_entry(chunk, region.name, start + 1, len(entries))
                )
            if is_entry:
                start, level = idx, len(is_entry.group("hashes"))
            continue
        if start is None:
            if raw.strip() not in ("", SEPARATOR, EMPTY_REGION_LINE):
                errors.append(
                    f"line {idx + 1}: content in region {region.name!r} before "
                    "its first entry"
                )
            continue
        heading = None if idx in code else _HEADING.match(raw)
        if heading and len(heading.group("hashes")) <= level:
            errors.append(
                f"line {idx + 1}: heading {heading.group('text')!r} in region "
                f"{region.name!r} is not an entry and not inside one"
            )
    return errors


def parse_doc(text: str) -> Doc:
    lines = text.split("\n")
    code = code_lines(lines)
    regions, errors = parse_regions(lines, code)
    entry_regions = sorted(
        (r for r in regions.values() if r.kind == "entries"), key=lambda r: r.begin
    )
    errors.extend(_unplaced_entries(lines, code, entry_regions))
    entries: list[Entry] = []
    for region in entry_regions:
        errors.extend(_split_region(lines, code, region, entries))
    return Doc(lines=lines, regions=regions, entries=entries, errors=errors, code=code)


# ---------------------------------------------------------------------------
# Generation


def _shift_headings(entry: Entry, target: int) -> list[str]:
    delta = target - entry.level
    if delta == 0:
        return list(entry.lines)
    chunk_code = code_lines(entry.lines)
    out = []
    for offset, raw in enumerate(entry.lines):
        heading = None if offset in chunk_code else _HEADING.match(raw)
        if heading is None:
            out.append(raw)
            continue
        level = len(heading.group("hashes")) + delta
        if not 1 <= level <= MAX_HEADING_LEVEL:
            raise Refusal(
                f"{entry.label}: a heading would move to level {level}, past "
                f"{MAX_HEADING_LEVEL}"
            )
        out.append("#" * level + raw[len(heading.group("hashes")) :])
    return out


def _region_body(chunks: list[list[str]]) -> list[str]:
    if not chunks:
        return ["", EMPTY_REGION_LINE, ""]
    body = [""]
    for chunk in chunks:
        body.extend([SEPARATOR, "", *chunk, ""])
    return body


def expected_toc(lines: list[str], regions: dict[str, Region]) -> list[str]:
    """One line per heading after the TOC region, indented by level, in file order."""
    toc_end = regions["toc"].end + 1
    out = []
    entry_level: int | None = None
    for heading in parse_headings("\n".join(lines)):
        if heading.line <= toc_end or heading.level < TOC_MIN_LEVEL:
            continue
        label = heading.text
        if _ENTRY.match(lines[heading.line - 1]):
            entry_level = heading.level
        elif entry_level is not None and heading.level <= entry_level:
            entry_level = None
        elif entry_level is not None and heading.level == entry_level + 1:
            sub = _SUBSECTION.match(label)
            label = sub.group("name") if sub else label
        indent = "  " * (heading.level - TOC_MIN_LEVEL)
        out.append(f"{indent}- [{label}](#{heading.slug})")
    return out


def _cell(value: str) -> str:
    return value.replace("|", "\\|")


def expected_tracking(lines: list[str], entries: list[Entry]) -> list[str]:
    """The tracking table: one row per entry, in file order, derived from its Metadata."""
    slugs = {h.line: h.slug for h in parse_headings("\n".join(lines))}
    rows = list(TRACKING_HEADER)
    for entry in sorted(entries, key=lambda e: e.line):
        sent = _LEADING_DATE.match(entry.metadata.get("Sent", ""))
        cells = [
            f"[{_cell(strip_inline_markdown(entry.title))}](#{slugs.get(entry.line, '')})",
            entry.tag,
            _cell(entry.metadata.get("Drafted", "")),
            _cell(entry.metadata.get("Last updated", "")),
            sent.group(1) if sent else "Not sent",
        ]
        rows.append("| " + " | ".join(cells) + " |")
    return rows


def _replace_regions(
    lines: list[str], regions: dict[str, Region], bodies: dict[str, list[str]]
) -> list[str]:
    out: list[str] = []
    cursor = 0
    for region in sorted(regions.values(), key=lambda r: r.begin):
        if region.name not in bodies:
            continue
        out.extend(lines[cursor : region.begin + 1])
        out.extend(bodies[region.name])
        cursor = region.end
    out.extend(lines[cursor:])
    return out


def _errors_block(heading: str, errors: list[str]) -> str:
    return heading + "\n" + "\n".join(f"  {e}" for e in errors)


@dataclass
class ArchiveResult:
    text: str
    moved: list[tuple[str, str, str]]


def archive_text(text: str) -> ArchiveResult:
    """File every entry into its tag's region, then regenerate the TOC and tracking table."""
    doc = parse_doc(text)
    if doc.errors:
        raise Refusal(
            _errors_block("the doc's structure cannot be archived safely:", doc.errors)
        )
    grouped: dict[str, list[Entry]] = {name: [] for name in ENTRY_REGIONS.values()}
    moved = []
    for entry in doc.entries:
        target = ENTRY_REGIONS[entry.tag]
        grouped[target].append(entry)
        if target != entry.region:
            moved.append((entry.heading, entry.region, target))
    bodies = {}
    for name, members in grouped.items():
        members.sort(key=lambda e: (-int(e.date.replace("-", "")), e.order))
        bodies[name] = _region_body(
            [_shift_headings(e, REGION_LEVEL[name]) for e in members]
        )
    stage = parse_doc("\n".join(_replace_regions(doc.lines, doc.regions, bodies)))
    if stage.errors:
        raise Refusal(
            _errors_block("archiving produced an unparseable doc:", stage.errors)
        )
    generated = {
        "toc": ["", *expected_toc(stage.lines, stage.regions), ""],
        "tracking-table": ["", *expected_tracking(stage.lines, stage.entries), ""],
    }
    lines = _replace_regions(stage.lines, stage.regions, generated)
    return ArchiveResult(text="\n".join(lines), moved=moved)


# ---------------------------------------------------------------------------
# Validation


def _body(lines: list[str], region: Region) -> list[str]:
    return [line for line in lines[region.begin + 1 : region.end] if line.strip()]


def _nearest_heading(
    lines: list[str], code: set[int], idx: int, max_level: int
) -> tuple[int, str] | None:
    for i in range(idx - 1, -1, -1):
        heading = None if i in code else _HEADING.match(lines[i])
        if heading and len(heading.group("hashes")) <= max_level:
            return len(heading.group("hashes")), heading.group("text")
    return None


def _placement_findings(doc: Doc) -> list[str]:
    findings = []
    for name, subheading in ARCHIVE_SUBHEADING.items():
        idx = doc.regions[name].begin
        if _nearest_heading(doc.lines, doc.code, idx, MAX_HEADING_LEVEL) != (
            3,
            subheading,
        ):
            findings.append(
                f"region {name!r} must sit directly under '### {subheading}'"
            )
        if _nearest_heading(doc.lines, doc.code, idx, 2) != (2, ARCHIVE_HEADING):
            findings.append(f"region {name!r} must sit under '## {ARCHIVE_HEADING}'")
    return findings


def _shape_findings(entry: Entry, keys: dict[str, str]) -> list[str]:
    findings = []
    expected_region = ENTRY_REGIONS[entry.tag]
    if entry.region != expected_region:
        where = (
            "inside the archive" if entry.tag == "Pending" else "outside the archive"
        )
        findings.append(
            f"{entry.label} is [{entry.tag}] but {where}, in region {entry.region!r}"
        )
    if entry.level != REGION_LEVEL[entry.region]:
        findings.append(
            f"{entry.label} is a level-{entry.level} heading; region "
            f"{entry.region!r} needs level {REGION_LEVEL[entry.region]}"
        )
    names = [name for name, _ in entry.subsections]
    if names != list(SUBSECTIONS):
        findings.append(
            f"{entry.label} has subsections {names}, expected {list(SUBSECTIONS)}"
        )
    if entry.key is None or len({key for _, key in entry.subsections}) != 1:
        findings.append(f"{entry.label} subsections do not all end in one entry key")
    elif not entry.key.startswith(entry.date):
        findings.append(
            f"{entry.label} key {entry.key!r} does not start with its draft date"
        )
    elif entry.key in keys:
        findings.append(
            f"{entry.label} reuses the entry key {entry.key!r} of '{keys[entry.key]}'"
        )
    else:
        keys[entry.key] = entry.heading
    return findings


def _metadata_findings(entry: Entry) -> list[str]:
    findings = [
        f"{entry.label} Metadata has no {name} row"
        for name in REQUIRED_FIELDS
        if name not in entry.metadata
    ]
    status = entry.metadata.get("Status", "")
    if status and not status.startswith(entry.tag):
        findings.append(
            f"{entry.label} heading tag [{entry.tag}] disagrees with Metadata "
            f"Status {status!r}"
        )
    drafted = entry.metadata.get("Drafted")
    if drafted and drafted != entry.date:
        findings.append(
            f"{entry.label} heading date {entry.date} disagrees with Metadata "
            f"Drafted {drafted}"
        )
    if entry.tag == "Sent" and not _LEADING_DATE.match(entry.metadata.get("Sent", "")):
        findings.append(
            f"{entry.label} is [Sent] but its Metadata Sent row does not start with a date"
        )
    return findings


def _strip_comment(raw: str, open_comment: bool) -> tuple[str, bool]:
    """``raw`` with every HTML comment span removed, and whether one is still open."""
    kept = []
    rest = raw
    while rest:
        if open_comment:
            close = rest.find("-->")
            if close < 0:
                return "".join(kept), True
            rest, open_comment = rest[close + 3 :], False
        else:
            begin = rest.find("<!--")
            if begin < 0:
                kept.append(rest)
                break
            kept.append(rest[:begin])
            rest, open_comment = rest[begin + 4 :], True
    return "".join(kept), open_comment


def _draft_lines(entry: Entry) -> list[str]:
    """The AI-generated draft's body lines, HTML comments removed."""
    fenced = code_lines(entry.lines)
    out: list[str] = []
    inside = False
    comment = False
    for offset, raw in enumerate(entry.lines[1:], start=1):
        is_code = offset in fenced
        heading = None if comment or is_code else _HEADING.match(raw)
        if heading and len(heading.group("hashes")) <= entry.level + 1:
            sub = _SUBSECTION.match(heading.group("text"))
            inside = (sub.group("name") if sub else heading.group("text")) == DRAFT
            continue
        if not is_code:
            raw, comment = _strip_comment(raw, comment)
        if inside:
            out.append(raw)
    return out


def _paragraphs(lines: list[str]) -> list[list[int]]:
    """Indices of each run of non-blank lines."""
    paragraphs: list[list[int]] = [[]]
    for idx, raw in enumerate(lines):
        if raw.strip():
            paragraphs[-1].append(idx)
        elif paragraphs[-1]:
            paragraphs.append([])
    return [p for p in paragraphs if p]


def _quoted_whole(lines: list[str], fenced: set[int]) -> bool:
    """Every paragraph is a blockquote; an unquoted line may only be a lazy continuation."""
    for paragraph in _paragraphs(lines):
        for position, idx in enumerate(paragraph):
            text = lines[idx].lstrip()
            if text.startswith(">"):
                continue
            lazy = (
                position > 0
                and idx not in fenced
                and not _HEADING.match(text)
                and not _BLOCK_START.match(text)
            )
            if not lazy:
                return False
    return True


def _draft_wrapper(lines: list[str]) -> str | None:
    """The block that wraps the whole draft, or None when the draft is plain text.

    A code block for one command inside an otherwise plain draft is message content,
    so only a draft that is entirely fenced, entirely indented code, or entirely quoted
    (lazy paragraph continuations included) counts as wrapped.
    """
    fenced = code_lines(lines)
    content = [i for i, raw in enumerate(lines) if raw.strip()]
    if not content:
        return None
    if all(i in fenced for i in content):
        return "a code fence"
    if all(lines[i].startswith(("    ", "\t")) for i in content):
        return "an indented code block"
    if _quoted_whole(lines, fenced):
        return "a blockquote"
    return None


def _wrapped_draft_findings(entry: Entry) -> list[str]:
    """A draft wrapped whole in a code block or blockquote: the wrapper pastes into Teams."""
    wrapper = _draft_wrapper(_draft_lines(entry))
    if wrapper is None:
        return []
    return [
        f"{entry.label}: its {DRAFT} is wrapped in {wrapper}; write the draft as plain "
        "text directly under its heading, so a copy pastes into Teams with no wrapper"
    ]


def _order_findings(entries: list[Entry]) -> list[str]:
    findings = []
    for name in ENTRY_REGIONS.values():
        members = [e for e in entries if e.region == name]
        for earlier, later in zip(members, members[1:], strict=False):
            if later.date > earlier.date:
                findings.append(
                    f"{later.label} is newer than the entry above it in {name!r}; "
                    "entries go newest first by draft date"
                )
    return findings


def _diff_findings(label: str, actual: list[str], expected: list[str]) -> list[str]:
    if actual == expected:
        return []
    missing = [line for line in expected if line not in actual]
    extra = [line for line in actual if line not in expected]
    findings = [f"{label} is missing: {line.strip()}" for line in missing]
    findings += [
        f"{label} has a line matching no entry or heading: {line.strip()}"
        for line in extra
    ]
    if not findings:
        pairs = zip(actual, expected, strict=False)
        first = next(i for i, (a, e) in enumerate(pairs) if a != e)
        findings.append(
            f"{label} is out of order at row {first + 1}: expected "
            f"{expected[first].strip()}"
        )
    return findings


def validate_text(text: str) -> list[str]:
    """Every structural problem in a drafts doc, each naming the offending entry or line."""
    doc = parse_doc(text)
    findings = list(doc.errors)
    if any(name not in doc.regions for name in REGION_KINDS):
        return findings
    findings.extend(_placement_findings(doc))
    keys: dict[str, str] = {}
    for entry in doc.entries:
        if entry.tag in TAGS:
            findings.extend(_shape_findings(entry, keys))
            findings.extend(_metadata_findings(entry))
            findings.extend(_wrapped_draft_findings(entry))
    findings.extend(_order_findings(doc.entries))
    findings.extend(
        _diff_findings(
            "table of contents",
            _body(doc.lines, doc.regions["toc"]),
            expected_toc(doc.lines, doc.regions),
        )
    )
    findings.extend(
        _diff_findings(
            "tracking table",
            _body(doc.lines, doc.regions["tracking-table"]),
            expected_tracking(doc.lines, doc.entries),
        )
    )
    return findings


# ---------------------------------------------------------------------------
# set-status


@dataclass(frozen=True)
class StatusChange:
    key: str
    tag: str
    today: str
    note: str | None = None
    sent: str | None = None


def set_status_text(text: str, change: StatusChange) -> str:
    """Flip one entry's heading tag and update its Status, Last updated and Sent rows."""
    if change.tag not in TAGS:
        raise Refusal(f"unknown tag {change.tag!r}; use one of {', '.join(TAGS)}")
    if change.tag == "Sent" and not (change.sent and _LEADING_DATE.match(change.sent)):
        raise Refusal(
            "a Sent entry needs --sent starting with the send date, "
            "e.g. '2026-10-14 9:46 AM'"
        )
    if change.tag == "Dropped" and not change.note:
        raise Refusal("a Dropped entry needs --note with the reason it was not sent")
    doc = parse_doc(text)
    if doc.errors:
        raise Refusal(_errors_block("the doc's structure is broken:", doc.errors))
    matches = [e for e in doc.entries if e.key == change.key]
    if len(matches) != 1:
        raise Refusal(
            f"{len(matches)} entries have the key {change.key!r}; pass the key "
            "from the entry's subsection headings"
        )
    entry = matches[0]
    lines = list(doc.lines)
    start = entry.line - 1
    lines[start] = f"{'#' * entry.level} [{change.tag}] {entry.date} - {entry.title}"
    updates = {
        "Status": f"{change.tag}: {change.note}" if change.note else change.tag,
        "Last updated": change.today,
    }
    if change.sent:
        updates["Sent"] = change.sent
    seen: set[str] = set()
    for offset in range(1, len(entry.lines)):
        row = _ROW.match(lines[start + offset])
        name = row.group("field") if row else None
        if name in updates and name not in seen:
            seen.add(name)
            lines[start + offset] = f"| {name} | {_cell(updates[name])} |"
    if missing := set(updates) - seen:
        raise Refusal(f"{entry.label} Metadata has no rows for {sorted(missing)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI


def _location(args: argparse.Namespace) -> Location:
    return resolve_location(
        Overrides(
            config=args.config,
            project=args.project,
            profile=args.profile,
            repo=args.repo,
            drafts_file=args.file,
        )
    )


def _cmd_resolve(args: argparse.Namespace) -> int:
    location = _location(args)
    if args.json:
        print(json.dumps(location.as_dict(), indent=2))
    else:
        for name, value in location.as_dict().items():
            print(f"{name}: {value}")
    return EXIT_OK


def _report(path: Path, findings: list[str]) -> None:
    if not findings:
        print(f"PASS {path}")
        return
    print(f"FAIL {path} ({len(findings)} findings)")
    for finding in findings:
        print(f"     {finding}")


def _cmd_validate(args: argparse.Namespace) -> int:
    if args.paths:
        paths = [Path(p) for p in args.paths]
    else:
        paths = [_location(args).drafts_file]
    failed = 0
    for path in paths:
        findings = validate_text(path.read_text(encoding="utf-8"))
        _report(path, findings)
        failed += bool(findings)
    print(f"--- {len(paths) - failed} passed, {failed} failed")
    return EXIT_INVALID if failed else EXIT_OK


def _cmd_set_status(args: argparse.Namespace) -> int:
    location = _location(args)
    require_clean_worktree(location.repo)
    path = location.drafts_file
    change = StatusChange(
        key=args.key,
        tag=args.tag,
        today=args.date or date.today().isoformat(),
        note=args.note,
        sent=args.sent,
    )
    path.write_text(set_status_text(path.read_text(encoding="utf-8"), change), "utf-8")
    print(f"set {args.key} to [{args.tag}] in {path}")
    print("next: read the diff, commit it, then run `archive`")
    return EXIT_OK


def _cmd_archive(args: argparse.Namespace) -> int:
    location = _location(args)
    if not args.dry_run:
        require_clean_worktree(location.repo)
    path = location.drafts_file
    text = path.read_text(encoding="utf-8")
    result = archive_text(text)
    findings = validate_text(result.text)
    for heading, source, target in result.moved:
        print(f"moved: {heading}  [{source} -> {target}]")
    if not result.moved:
        print("moved: nothing")
    if findings:
        _report(path, findings)
        print(
            "not written: the archived doc does not validate; fix the doc "
            "(or this script) and run again"
        )
        return EXIT_INVALID
    if result.text == text:
        print(f"unchanged: {path} is already archived and consistent")
    elif args.dry_run:
        print(f"dry run: {path} would change; nothing written")
    else:
        path.write_text(result.text, encoding="utf-8")
        print(f"written: {path}")
        print(
            f"next: read `git -C {location.repo} diff` yourself; if it is wrong, "
            f"`git -C {location.repo} restore -- {path}`"
        )
    print(f"PASS {path}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help=f"config TOML (env {ENV_CONFIG})")
    common.add_argument("--project", help=f"project key (env {ENV_PROJECT})")
    common.add_argument(
        "--profile", help=f"profile table to apply (env {ENV_PROFILE})"
    )
    common.add_argument("--repo", help=f"drafts repo worktree (env {ENV_REPO})")
    common.add_argument(
        "--file", help=f"drafts doc, abs or repo-relative (env {ENV_FILE})"
    )

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    resolve = sub.add_parser("resolve", parents=[common], help="where the doc is")
    resolve.add_argument("--json", action="store_true")
    resolve.set_defaults(func=_cmd_resolve)

    validate = sub.add_parser("validate", parents=[common], help="check structure")
    validate.add_argument("paths", nargs="*", help="docs to check (default: resolved)")
    validate.set_defaults(func=_cmd_validate)

    status = sub.add_parser("set-status", parents=[common], help="flip one entry's tag")
    status.add_argument("key", help="entry key, e.g. '2026-10-14 nesting-test'")
    status.add_argument("tag", choices=TAGS)
    status.add_argument("--note", help="reason, written as '<Tag>: <note>'")
    status.add_argument("--sent", help="Sent row value, starting with the date")
    status.add_argument("--date", help="Last updated date (default: today)")
    status.set_defaults(func=_cmd_set_status)

    archive = sub.add_parser("archive", parents=[common], help="file entries by tag")
    archive.add_argument("--dry-run", action="store_true", help="write nothing")
    archive.set_defaults(func=_cmd_archive)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Refusal as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
