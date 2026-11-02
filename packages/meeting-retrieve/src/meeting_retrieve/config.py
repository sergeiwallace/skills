"""The named-meetings config: one or more TOML files, read at call time and validated.

A file holds a ``[defaults]`` table and one ``[meetings.<name>]`` table per meeting. Later
files override earlier ones: ``[defaults]`` key by key, a meeting table as a whole. The
``[defaults]`` table may be shared with a transport's own settings, so unknown keys there are
ignored; a meeting table is this module's alone, so an unknown key there is an error.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationError,
    field_validator,
    model_validator,
)

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
REQUEST_FIELDS = {"chat": "", "date": "", "title": "", "inbox": ""}


class ConfigError(Exception):
    """A config file that cannot be read or does not validate. Exit 3."""


def check_time(value: str | None) -> str | None:
    if value is not None and not TIME_RE.match(value):
        raise ValueError(f"must be HH:MM (24-hour), got {value!r}")
    return value


def check_tz(value: str | None) -> str | None:
    if value is None:
        return value
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"not a known IANA zone: {value!r}") from None
    return value


class Defaults(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tz: str | None = None
    author: str = ""
    ai_summary_request: str = ""
    # Where landed artefacts may go: the output repository's name must be one of these (empty:
    # any), and the meeting directory must sit under this repo-relative path (empty: any).
    allowed_repos: list[str] = []
    meetings_prefix: str = ""

    _tz = field_validator("tz")(check_tz)

    @field_validator("meetings_prefix")
    @classmethod
    def _relative_prefix(cls, value: str) -> str:
        if value and (value.startswith("/") or ".." in value.split("/")):
            raise ValueError("must be a repo-relative path that does not climb out")
        return value

    @field_validator("ai_summary_request")
    @classmethod
    def _request_placeholders(cls, value: str) -> str:
        try:
            value.format_map(REQUEST_FIELDS)
        except (KeyError, IndexError, ValueError) as exc:
            raise ValueError(
                f"placeholders are {{chat}} {{date}} {{title}} {{inbox}}: {exc}"
            ) from None
        return value


class Meeting(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chat: str | None = None
    conv: str | None = None
    title: str | None = None
    repo: str
    root: str
    meeting_type: str = ""
    series: str = ""
    time: str | None = None
    tz: str | None = None
    aliases: list[str] = []
    slug: str | None = None

    _time = field_validator("time")(check_time)
    _tz = field_validator("tz")(check_tz)

    @field_validator("slug")
    @classmethod
    def _slug_has_date(cls, value: str | None) -> str | None:
        if value is not None and "{date}" not in value:
            raise ValueError("must contain {date}")
        if value is not None:
            try:
                value.format(date="")
            except (KeyError, IndexError, ValueError) as exc:
                raise ValueError(f"the only placeholder is {{date}}: {exc}") from None
        return value

    @model_validator(mode="after")
    def _chat_or_conv(self) -> Meeting:
        if not self.chat and not self.conv:
            raise ValueError("needs chat or conv")
        return self


class FileModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    defaults: Defaults = Defaults()
    meetings: dict[str, Meeting] = {}


@dataclass
class Config:
    defaults: Defaults = field(default_factory=Defaults)
    meetings: dict[str, Meeting] = field(default_factory=dict)
    sources: list[Path] = field(default_factory=list)

    def resolve(self, name: str) -> tuple[str, Meeting] | None:
        """The table a name or alias selects, or None."""
        if name in self.meetings:
            return name, self.meetings[name]
        for table, meeting in self.meetings.items():
            if name in meeting.aliases:
                return table, meeting
        return None


def _location(error: dict) -> str:
    return ".".join(str(part) for part in error["loc"]) or "(top level)"


def read_file(path: Path) -> tuple[FileModel, dict]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"{path}: config file not found") from None
    except OSError as exc:
        raise ConfigError(f"{path}: unreadable: {type(exc).__name__}") from None
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: not valid TOML: {exc}") from None
    try:
        model = FileModel.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise ConfigError(f"{path}: key {_location(first)}: {first['msg']}") from None
    return model, raw.get("defaults", {}) or {}


def load(paths: list[Path]) -> Config:
    """Read every file in order; a later file overrides an earlier one."""
    merged_defaults: dict = {}
    meetings: dict[str, Meeting] = {}
    origin: dict[str, Path] = {}
    for path in paths:
        model, raw_defaults = read_file(path)
        merged_defaults.update(model.defaults.model_dump(include=set(raw_defaults)))
        meetings.update(model.meetings)
        origin.update(dict.fromkeys(model.meetings, path))
    seen: dict[str, str] = {}
    for table, meeting in meetings.items():
        for alias in meeting.aliases:
            other = seen.get(alias) or (
                alias if alias in meetings and alias != table else None
            )
            if other:
                raise ConfigError(
                    f"{origin[table]}: key meetings.{table}.aliases: {alias!r} also names meeting {other!r}"
                )
            seen[alias] = table
    return Config(Defaults.model_validate(merged_defaults), meetings, list(paths))
