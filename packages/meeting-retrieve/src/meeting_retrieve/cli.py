"""The ``meeting`` command: a meeting name, date and time in, one landed instance out.

A caller wires two things: the transport argv (see ``transport``) and the config files (see
``config``). Everything else is an option, parsed through skill_cli_foundation.

Exit codes are the transport's land exit (0 complete, 1 incomplete, 2 refused, 3 config, 4 not
authenticated), except for this command's own refusals (2: an ambiguous or unknown input the
caller must settle) and a broken config or transport (3).
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from datetime import date as date_type
from pathlib import Path
from typing import Annotated

import typer
from skill_cli_foundation import make_app

from . import config as cfg
from . import repo
from .config import TIME_RE, ConfigError
from .transport import (
    EXIT_CONFIG,
    EXIT_FAIL,
    EXIT_REFUSED,
    Transport,
    TransportBroken,
    TransportFailed,
)

MATCH_WINDOW_MIN = 45
WROTE_RE = re.compile(r"^[^:]+: wrote (?P<path>.+?) \(\d+ bytes\)")
KEPT_RE = re.compile(r"^[^:]+: kept existing (?P<path>.+)$")
PROBLEM_RE = re.compile(r"^[^:]+: (?:FAILED|MISSING)\b")
SUMMARY_MISSING_RE = re.compile(r"^[^:]+: MISSING\b")


class Refusal(Exception):
    """An input the caller has to settle (unknown, ambiguous or incomplete). Exit 2."""


@dataclass
class Target:
    meeting: str | None
    entry: cfg.Meeting | None
    conv: str
    chat: str | None
    title: str
    repo_path: Path
    meeting_root: Path
    tz: str | None


def _minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def meeting_slug(title: str) -> str:
    """The directory slug of an ad hoc meeting: its title lowercased, every run of characters
    that is not a letter or digit replaced by one hyphen ("Vendor Transition Review" ->
    "vendor-transition-review"). A configured, recurring meeting never uses it: its date
    directories sit under the entry's root."""
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def instance_stem(title: str, day: str, time: str | None) -> str:
    """The landed files' name stem: "<title> <date>", plus " HHMM" for one of several that day.

    STEM RULE, kept in step with the Teams transport's instance_stem (skills/teams/scripts/
    teams.py), which names the landed files from it; `time` is the --time this command passes
    to land, which it does only on a day with several instances. Change both together.
    """
    stem = f"{title} {day}"
    return f"{stem} {time.replace(':', '')}" if time else stem


def describe(instance: dict) -> str:
    name = instance.get("recording") or "(recording unnamed)"
    return f"  {instance['date']} {instance['time']}  {name}"


def pick_instance(
    instances: list[dict], day: str | None, time: str | None, default_time: str | None
) -> tuple[dict | None, int]:
    """The instance to land and how many instances share its day.

    No date: the newest instance. A date with one instance: that one. A date with several:
    the one within MATCH_WINDOW_MIN minutes of --time, or of the configured time. Anything
    else is ambiguous and refused, naming every candidate, so the caller can ask.
    """
    if day is None and not instances:
        raise Refusal(
            "the transport lists no recorded instance of this meeting; pass --date to land a day anyway"
        )
    newest = day is None
    day = day or instances[0]["date"]
    same_day = [i for i in instances if i["date"] == day]
    wanted = time or (default_time if len(same_day) > 1 else None)
    if wanted:
        hits = [
            i
            for i in same_day
            if abs(_minutes(i["time"]) - _minutes(wanted)) <= MATCH_WINDOW_MIN
        ]
        if len(hits) != 1:
            found = "no instance" if not hits else f"{len(hits)} instances"
            listing = "\n".join(describe(i) for i in (hits or same_day)) or "  (none)"
            raise Refusal(
                f"{found} on {day} starts within {MATCH_WINDOW_MIN} minutes of {wanted}; "
                f"the instances that day:\n{listing}"
            )
        return hits[0], len(same_day)
    if newest:
        return instances[0], len(same_day)
    if len(same_day) > 1:
        raise Refusal(
            f"{len(same_day)} instances on {day}; pass --time HH:MM to pick one:\n"
            + "\n".join(describe(i) for i in same_day)
        )
    return (same_day[0] if same_day else None), len(same_day)


def _configured_names(config: cfg.Config) -> str:
    names = []
    for table, meeting in sorted(config.meetings.items()):
        names.append(
            table
            + (f" (aliases: {', '.join(meeting.aliases)})" if meeting.aliases else "")
        )
    return ", ".join(names) or "(none configured)"


def _lookup_chat(transport: Transport, chat: str) -> str:
    rows = transport.chats(chat)
    exact = [r for r in rows if r["topic"] == chat]
    if len(exact) == 1:
        return exact[0]["conv"]
    listing = (
        "\n".join(f"  {r['topic']}\t{r['conv']}" for r in (exact or rows))
        or "  (no chat topic contains it)"
    )
    what = f"{len(exact)} chats are" if exact else "no chat is"
    raise Refusal(
        f"{what} titled exactly {chat!r}; pass --conv ID or the exact --chat title. Near matches:\n{listing}"
    )


def check_destination(defaults: cfg.Defaults, repo_root: Path, root: str) -> str:
    """Refuse an output that is not a git work tree of an allowed repository, or whose
    meeting directory falls outside the configured meetings prefix. Return its repository name.

    Landed artefacts are evidence a meeting doc cites, so they go only where a repository
    keeps them, never to a scratch directory.
    """
    allowed = ", ".join(defaults.allowed_repos) or "any repository"
    prefix = defaults.meetings_prefix or "(any path)"
    rule = f"allowed repositories: {allowed}; meetings prefix: {prefix}"
    facts = repo.work_tree(repo_root)
    if facts is None:
        raise Refusal(f"{repo_root} is not inside a git work tree ({rule})")
    repo_real = repo_root.resolve()
    meeting_real = (repo_root / root).resolve()
    if Path(root).is_absolute() or repo_real not in meeting_real.parents:
        raise Refusal(
            f"--root {root!r} resolves to {meeting_real}, which is not inside the repository "
            f"{repo_real}; pass a repo-relative --root that stays inside it ({rule})"
        )
    name = facts[1]
    if defaults.allowed_repos and name not in defaults.allowed_repos:
        raise Refusal(
            f"{repo_root} is repository {name!r}, not an allowed one ({rule})"
        )
    if defaults.meetings_prefix:
        base = (repo_root / defaults.meetings_prefix).resolve()
        target = (repo_root / root).resolve()
        if target != base and base not in target.parents:
            raise Refusal(
                f"the meeting directory {target} is not under {base} ({rule}); pass --root inside it"
            )
    return name


def _fixed_repo(table: str | None, entry: cfg.Meeting, what: str) -> Refusal:
    return Refusal(
        f"meeting {table!r} always lands in repository {entry.repo!r}, and {what}; an exception "
        "the operator asks for is landed with the ad hoc form: --conv or --chat, --date, --root, --repo"
    )


def _output_repo(  # noqa: PLR0913 - every argument is one resolved input
    config: cfg.Config,
    *,
    table: str | None,
    entry: cfg.Meeting | None,
    repo_override: str | None,
    repo_path: Path | None,
    root: str,
) -> Path:
    """The checked output repository root. A configured meeting's repository is its entry's,
    whatever --repo or --repo-path says; only the ad hoc form chooses another."""
    if entry is not None and repo_override not in {None, entry.repo}:
        raise _fixed_repo(table, entry, f"--repo names {repo_override!r}")
    repo_name = entry.repo if entry else repo_override
    repo_root = repo.resolve(repo_name, repo_path, Path.cwd())
    if repo_root is None:
        which = f"repository {repo_name!r}" if repo_name else "output repository"
        raise Refusal(
            f"cannot resolve the {which}: the current directory is not in it and no directory of "
            "that name sits beside it; pass --repo-path PATH"
        )
    landed_repo = check_destination(config.defaults, repo_root, root)
    if entry is not None and landed_repo != entry.repo:
        raise _fixed_repo(
            table, entry, f"{repo_root} is a checkout of repository {landed_repo!r}"
        )
    return repo_root


def resolve_target(  # noqa: PLR0913 - every argument is one CLI option
    config: cfg.Config,
    transport: Transport,
    *,
    name: str | None,
    day: str | None,
    conv: str | None,
    chat: str | None,
    title: str | None,
    root: str | None,
    repo_path: Path | None,
    repo_override: str | None = None,
) -> Target:
    entry = None
    table = None
    if name is not None:
        found = config.resolve(name)
        if found is None:
            raise Refusal(
                f"no meeting is named {name!r}; configured: {_configured_names(config)}"
            )
        table, entry = found
    elif conv or chat:
        if day is None:
            raise Refusal(
                "--date is required with --conv or --chat and no meeting name"
            )
    else:
        raise Refusal(
            f"name a meeting, or pass --conv or --chat with --date; configured: {_configured_names(config)}"
        )

    chat_title = chat or (entry.chat if entry else None)
    final_title = title or (entry.title if entry else None) or chat_title
    if not final_title:
        raise Refusal(
            "--title is required with --conv and no chat title to default it from"
        )
    # An ad hoc meeting (no configured entry) lands directly under the meetings prefix in
    # its own `<slug>-<date>/` directory unless --root names another parent.
    final_root = root or (entry.root if entry else config.defaults.meetings_prefix)
    if not final_root:
        raise Refusal("--root is required when no configured meeting names one")
    repo_root = _output_repo(
        config,
        table=table,
        entry=entry,
        repo_override=repo_override,
        repo_path=repo_path,
        root=final_root,
    )
    conv_id = conv or (entry.conv if entry and not chat else None)
    if conv_id is None:
        conv_id = _lookup_chat(transport, chat_title or "")
    return Target(
        meeting=table,
        entry=entry,
        conv=conv_id,
        chat=chat_title,
        title=final_title,
        repo_path=repo_root,
        meeting_root=(repo_root / final_root).absolute(),
        tz=(entry.tz if entry and entry.tz else None) or config.defaults.tz,
    )


def _artefacts(lines: list[str]) -> list[str]:
    found = []
    for line in lines:
        match = WROTE_RE.match(line) or KEPT_RE.match(line)
        if match:
            found.append(match.group("path"))
    return list(dict.fromkeys(found))


def _list_meetings(config: cfg.Config, json_output: bool) -> None:
    rows = [
        {
            "name": table,
            "title": meeting.title or meeting.chat,
            "chat": meeting.chat,
            "conv": meeting.conv,
            "aliases": meeting.aliases,
            "repo": meeting.repo,
            "root": meeting.root,
        }
        for table, meeting in sorted(config.meetings.items())
    ]
    if json_output:
        typer.echo(json.dumps(rows, indent=2))
        return
    for row in rows:
        aliases = f"  aliases: {', '.join(row['aliases'])}" if row["aliases"] else ""
        typer.echo(
            f"{row['name']}\t{row['title']}\t{row['repo']}/{row['root']}{aliases}"
        )


def _check_inputs(day: str | None, time: str | None, ai_summary: Path | None) -> None:
    if day is not None:
        try:
            valid = date_type.fromisoformat(day).isoformat() == day
        except ValueError:
            valid = False
        if not valid:
            raise Refusal(f"--date must be YYYY-MM-DD, got {day!r}")
    if time is not None and not TIME_RE.match(time):
        raise Refusal(f"--time must be HH:MM (24-hour), got {time!r}")
    if ai_summary is not None and not ai_summary.is_file():
        raise Refusal(f"--ai-summary is not a file: {ai_summary}")


def _land_args(  # noqa: PLR0913 - every argument is one resolved input
    target: Target,
    day: str,
    time: str | None,
    summary: Path | None,
    force: bool,
    *,
    dir_name: str | None = None,
) -> list[str]:
    args = ["--conv", target.conv, "--date", day]
    if time:
        args += ["--time", time]
    args += ["--title", target.title, "--out-root", str(target.meeting_root)]
    if dir_name:
        args += ["--dir-name", dir_name]
    if target.tz:
        args += ["--tz", target.tz]
    if summary:
        args += ["--ai-summary", str(summary)]
    if force:
        args.append("--force")
    return args


def run(  # noqa: PLR0913 - every argument is one CLI option
    transport: Transport,
    config_paths: list[Path],
    *,
    name: str | None,
    day: str | None,
    time: str | None,
    conv: str | None,
    chat: str | None,
    title: str | None,
    root: str | None,
    repo_path: Path | None,
    ai_summary: Path | None,
    force: bool,
    json_output: bool,
    list_meetings: bool,
    repo_name: str | None = None,
) -> int:
    config = cfg.load(config_paths)
    if list_meetings:
        _list_meetings(config, json_output)
        return 0
    _check_inputs(day, time, ai_summary)
    target = resolve_target(
        config,
        transport,
        name=name,
        day=day,
        conv=conv,
        chat=chat,
        title=title,
        root=root,
        repo_path=repo_path,
        repo_override=repo_name,
    )
    instances = transport.instances(target.conv, day, target.tz)
    default_time = target.entry.time if target.entry else None
    chosen, same_day = pick_instance(instances, day, time, default_time)
    landed_day = day or chosen["date"]  # pick_instance refuses no date with no instance
    time_arg = chosen["time"] if chosen is not None and same_day > 1 else None
    # A configured, recurring meeting: <root>/<date>/. An ad hoc one: <root>/<slug>-<date>/,
    # the one directory that names the meeting, with data/ inside it.
    ad_hoc = target.entry is None
    dir_name = f"{meeting_slug(target.title)}-{landed_day}" if ad_hoc else landed_day
    date_dir = target.meeting_root / dir_name
    data_dir = date_dir / "data"
    stem = instance_stem(target.title, landed_day, time_arg)
    inbox = data_dir / f"{stem} AI summary.txt"
    summary = (
        ai_summary.absolute() if ai_summary else (inbox if inbox.is_file() else None)
    )

    args = _land_args(
        target,
        landed_day,
        time_arg,
        summary,
        force,
        dir_name=dir_name if ad_hoc else None,
    )
    result = transport.land(args, echo_to_stderr=json_output)

    note = sys.stderr if json_output else sys.stdout
    if result.code == EXIT_FAIL and any(
        SUMMARY_MISSING_RE.match(line) for line in result.lines
    ):
        request = config.defaults.ai_summary_request.format_map(
            {
                "chat": target.chat or target.title,
                "date": landed_day,
                "title": target.title,
                "inbox": str(inbox),
            }
        )
        note.write(
            f"AI Summary request: {request or '(no ai_summary_request in [defaults])'}\n"
        )
        note.write(f"AI Summary inbox: {inbox}\n")

    entry = target.entry
    if entry and entry.slug:
        slug = entry.slug.format(date=landed_day)
    else:
        slug = (
            dir_name if ad_hoc else None
        )  # the ad hoc doc is <slug>-<date>/<slug>-<date>.md
    report = {
        "meeting": target.meeting,
        "date": landed_day,
        "time": chosen["time"] if chosen else None,
        "conv": target.conv,
        "title": target.title,
        "repo_path": str(target.repo_path),
        "meeting_dir": str(date_dir),
        "data_dir": str(data_dir),
        "ad_hoc": ad_hoc,
        "artefacts": _artefacts(result.lines),
        "ai_summary": str(summary) if summary else None,
        "complete": result.code == 0,
        "missing": [line for line in result.lines if PROBLEM_RE.match(line)],
        "meeting_type": entry.meeting_type if entry else "",
        "series": entry.series if entry else "",
        "author": config.defaults.author,
        "slug": slug,
    }
    if json_output:
        sys.stdout.write(json.dumps(report, indent=2) + "\n")
    else:
        state = "complete" if report["complete"] else f"INCOMPLETE (exit {result.code})"
        sys.stdout.write(
            f"meeting {target.meeting or target.conv} {landed_day}: {state}, in {data_dir}\n"
        )
    return result.code


NameArg = Annotated[
    str | None,
    typer.Argument(metavar="NAME", help="A configured meeting name or alias"),
]
DateOpt = Annotated[
    str | None,
    typer.Option("--date", help="Meeting date, YYYY-MM-DD (default: the newest)"),
]
TimeOpt = Annotated[
    str | None,
    typer.Option(
        "--time", help="Local start HH:MM, picks one of several instances on a date"
    ),
]
ConvOpt = Annotated[
    str | None,
    typer.Option("--conv", help="Conversation id; needs --date without a NAME"),
]
ChatOpt = Annotated[
    str | None,
    typer.Option("--chat", help="Exact chat title; needs --date without a NAME"),
]
TitleOpt = Annotated[
    str | None,
    typer.Option("--title", help="File name stem (default: the configured title)"),
]
RootOpt = Annotated[
    str | None,
    typer.Option(
        "--root",
        help="The meeting directories' parent, relative to the repo (ad hoc default: the meetings prefix)",
    ),
]
RepoPathOpt = Annotated[
    Path | None,
    typer.Option(
        "--repo-path",
        help="Output repository root; with a NAME, a checkout of its repo",
    ),
]
RepoOpt = Annotated[
    str | None,
    typer.Option(
        "--repo",
        help="Output repository name; a NAME always lands in its configured repo",
    ),
]
AiSummaryOpt = Annotated[
    Path | None, typer.Option("--ai-summary", help="The Recap AI Summary text file")
]
ForceOpt = Annotated[
    bool, typer.Option("--force", help="Replace artefacts already landed")
]
JsonOpt = Annotated[bool, typer.Option("--json", help="Print one JSON object")]
ListOpt = Annotated[bool, typer.Option("--list", help="List the configured meetings")]
ConfigOpt = Annotated[
    list[Path] | None, typer.Option("--config", help="Another meetings TOML, read last")
]


def build_app(
    transport_argv: list[str], config_paths: list[Path], name: str = "meeting"
) -> typer.Typer:
    """The Typer app for a caller's transport and config files."""
    app = make_app(name)
    transport = Transport(transport_argv)

    @app.command(name=name)
    def meeting(  # noqa: PLR0913, PLR0917 - Typer binds each option by parameter
        meeting_name: NameArg = None,
        date: DateOpt = None,
        time: TimeOpt = None,
        conv: ConvOpt = None,
        chat: ChatOpt = None,
        title: TitleOpt = None,
        root: RootOpt = None,
        repo: RepoOpt = None,
        repo_path: RepoPathOpt = None,
        ai_summary: AiSummaryOpt = None,
        force: ForceOpt = False,
        json_output: JsonOpt = False,
        list_meetings: ListOpt = False,
        config: ConfigOpt = None,
    ) -> None:
        """Land one recorded instance of a meeting: the newest, or the one --date and --time pick."""
        try:
            code = run(
                transport,
                [*config_paths, *(config or [])],
                name=meeting_name,
                day=date,
                time=time,
                conv=conv,
                chat=chat,
                title=title,
                root=root,
                repo_path=repo_path,
                ai_summary=ai_summary,
                force=force,
                json_output=json_output,
                list_meetings=list_meetings,
                repo_name=repo,
            )
        except Refusal as exc:
            typer.echo(f"refused: {exc}", err=True)
            raise typer.Exit(EXIT_REFUSED) from None
        except (ConfigError, TransportBroken) as exc:
            typer.echo(f"config: {exc}", err=True)
            raise typer.Exit(EXIT_CONFIG) from None
        except TransportFailed as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(exc.code) from None
        raise typer.Exit(code)

    return app


def main(transport_argv: list[str], config_paths: list[Path]) -> None:
    build_app(transport_argv, config_paths)(prog_name="meeting")
