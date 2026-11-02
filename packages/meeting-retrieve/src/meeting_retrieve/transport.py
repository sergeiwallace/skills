"""The transport contract: an argv prefix the caller supplies, driven through three primitives.

    <transport> chats --query TITLE --json        -> [{"conv", "topic"}, ...]
    <transport> instances --conv ID [--date D] [--tz TZ] --json
                                                   -> [{"date", "time", "start_utc",
                                                        "recording", "message_id"}, ...]
                                                      newest first
    <transport> land --conv ID --date D [--time HH:MM] --title T --out-root R
                     [--tz TZ] [--ai-summary FILE] [--force]
                                                   -> exit 0 complete, 1 incomplete,
                                                      2 refused, 3 config, 4 not authenticated

The transport redacts its own secrets, so its stdout and stderr are passed through as they
are. Nothing here knows which service the transport talks to.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date as date_type

EXIT_OK, EXIT_FAIL, EXIT_REFUSED, EXIT_CONFIG, EXIT_AUTH = 0, 1, 2, 3, 4
INSTANCE_KEYS = ("date", "time", "start_utc", "recording", "message_id")


FIELD_FORMATS = {
    "date": re.compile(r"^\d{4}-\d{2}-\d{2}$"),
    "time": re.compile(r"^([01]\d|2[0-3]):[0-5]\d$"),
}


# What a malformed field should have been. Errors name the field and this shape, never the
# value, because a transport that misplaced a token in a field would otherwise print it.
FIELD_SHAPES = {
    "date": "a YYYY-MM-DD string",
    "time": "an HH:MM string",
    "start_utc": "a string",
    "recording": "a string",
    "message_id": "a string",
}


def _valid_field(key: str, value: object) -> bool:
    """Every instance field is a string; date and time also have a fixed form."""
    if not isinstance(value, str):
        return False
    if key == "date":
        try:
            date_type.fromisoformat(value)
        except ValueError:
            return False
    pattern = FIELD_FORMATS.get(key)
    return pattern is None or bool(pattern.match(value))


class TransportBroken(Exception):
    """The transport is missing or broke its contract. Exit 3, naming the primitive."""


class TransportFailed(Exception):
    """The transport answered a read with a non-zero exit; the front end exits with it."""

    def __init__(self, primitive: str, code: int) -> None:
        super().__init__(f"transport {primitive} exited {code}")
        self.code = code


@dataclass
class LandResult:
    code: int
    lines: list[str] = field(default_factory=list)


class Transport:
    def __init__(self, argv: Sequence[str]) -> None:
        self.argv = list(argv)

    def _read(self, primitive: str, args: list[str]) -> list[dict]:
        try:
            done = subprocess.run(  # noqa: S603 - argv list, no shell
                [*self.argv, primitive, *args],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            raise TransportBroken(
                f"transport for {primitive} could not run ({self.argv!r}): {exc}"
            ) from None
        if done.stderr:
            sys.stderr.write(done.stderr)
        if done.returncode != EXIT_OK:
            raise TransportFailed(primitive, done.returncode)
        try:
            data = json.loads(done.stdout)
        except ValueError:
            raise TransportBroken(
                f"transport {primitive} printed output that is not JSON"
            ) from None
        if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
            raise TransportBroken(
                f"transport {primitive} printed JSON that is not a list of objects"
            )
        return data

    def chats(self, query: str) -> list[dict]:
        rows = self._read("chats", ["--query", query, "--json"])
        if not all(
            isinstance(r.get("conv"), str) and isinstance(r.get("topic"), str)
            for r in rows
        ):
            raise TransportBroken(
                "transport chats returned an entry without string conv and topic"
            )
        return rows

    def instances(self, conv: str, date: str | None, tz: str | None) -> list[dict]:
        args = ["--conv", conv]
        if date:
            args += ["--date", date]
        if tz:
            args += ["--tz", tz]
        rows = self._read("instances", [*args, "--json"])
        for row in rows:
            missing = [key for key in INSTANCE_KEYS if key not in row]
            if missing:
                raise TransportBroken(
                    f"transport instances returned an entry without {', '.join(missing)}"
                )
            bad = [key for key in INSTANCE_KEYS if not _valid_field(key, row[key])]
            if bad:
                raise TransportBroken(
                    "transport instances returned an entry with a malformed "
                    + ", ".join(f"{key} (expected {FIELD_SHAPES[key]})" for key in bad)
                )
        return rows

    def land(self, args: list[str], *, echo_to_stderr: bool) -> LandResult:
        """Run land, echoing its stdout line by line as it arrives, and keep the lines."""
        out = sys.stderr if echo_to_stderr else sys.stdout
        try:
            proc = subprocess.Popen(  # noqa: S603 - argv list, no shell
                [*self.argv, "land", *args], stdout=subprocess.PIPE, text=True
            )
        except OSError as exc:
            raise TransportBroken(
                f"transport for land could not run ({self.argv!r}): {exc}"
            ) from None
        result = LandResult(code=EXIT_FAIL)
        assert proc.stdout is not None  # noqa: S101 - stdout=PIPE guarantees it
        for line in proc.stdout:
            out.write(line)
            out.flush()
            result.lines.append(line.rstrip("\n"))
        result.code = proc.wait()
        return result
