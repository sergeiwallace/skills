"""Behaviour tests for the named-meetings front end, driven through its Typer app.

The transport is a real subprocess, tests/fake_transport.py, answering from a fixture file and
logging the argv it was given, so every assertion about what was landed is an assertion about
the exact command the front end ran. Every name, id and path is synthetic.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from meeting_retrieve import cli
from meeting_retrieve.transport import EXIT_AUTH, EXIT_CONFIG, EXIT_REFUSED

FAKE = Path(__file__).resolve().parent / "fake_transport.py"
CONV = "19:meeting_example@thread.v2"
REPO = "notes-repo"
ROOT = "docs/meetings/team"
TITLE = "Team Stand-up"

INSTANCES = [
    {
        "date": "2026-10-20",
        "time": "10:01",
        "start_utc": "2026-10-20T14:01:00Z",
        "recording": "Team Stand-up-20261020_140100UTC-Meeting Recording.mp4",
        "message_id": "m3",
    },
    {
        "date": "2026-10-19",
        "time": "14:02",
        "start_utc": "2026-10-19T18:02:00Z",
        "recording": "Team Stand-up-20261019_180200UTC-Meeting Recording.mp4",
        "message_id": "m2",
    },
    {
        "date": "2026-10-19",
        "time": "10:00",
        "start_utc": "2026-10-19T14:00:00Z",
        "recording": "Team Stand-up-20261019_140000UTC-Meeting Recording.mp4",
        "message_id": "m1",
    },
]

CONFIG = f"""
[defaults]
tz = "America/New_York"
author = "Ada Rivera"
ai_summary_request = "teams.py recap-summary --chat '{{chat}}' --date {{date}} --out '{{inbox}}' ({{title}})"

[meetings.team-standup]
conv = "{CONV}"
chat = "Team Stand-up"
title = "{TITLE}"
repo = "{REPO}"
root = "{ROOT}"
meeting_type = "standup"
series = "team"
aliases = ["standup"]

[meetings.weekly-review]
chat = "Weekly Review"
repo = "{REPO}"
root = "docs/meetings/reviews"
meeting_type = "review"
series = "weekly"
slug = "weekly-review-analysis-{{date}}"
"""


def _git(cwd: Path, *args: str) -> None:
    subprocess.run([shutil.which("git"), "-C", str(cwd), *args], timeout=60, check=True)


class Env:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner) -> None:
        self.tmp = tmp_path
        self.runner = runner
        self.repo = tmp_path / "work" / REPO
        self.repo.mkdir(parents=True)
        _git(self.repo, "init", "-q")
        _git(
            self.repo,
            "remote",
            "add",
            "origin",
            f"https://example.invalid/org/{REPO}.git",
        )
        self.config = tmp_path / "meetings.toml"
        self.config.write_text(CONFIG, encoding="utf-8")
        self.fixture_path = tmp_path / "fixture.json"
        self.log = tmp_path / "argv.jsonl"
        self.fixture = {
            "chats": [],
            "instances": INSTANCES,
            "land": {"lines": [], "exit": 0},
        }
        self.transport = [sys.executable, str(FAKE)]
        monkeypatch.setenv("FAKE_TRANSPORT_FIXTURE", str(self.fixture_path))
        monkeypatch.setenv("FAKE_TRANSPORT_LOG", str(self.log))
        monkeypatch.chdir(self.repo)

    def run(self, *argv: str):
        self.fixture_path.write_text(json.dumps(self.fixture), encoding="utf-8")
        app = cli.build_app(self.transport, [self.config])
        return self.runner.invoke(app, list(argv))

    def calls(self, primitive: str | None = None) -> list[list[str]]:
        if not self.log.exists():
            return []
        rows = [
            json.loads(line)
            for line in self.log.read_text(encoding="utf-8").splitlines()
        ]
        return [r for r in rows if primitive is None or r[0] == primitive]

    def land_argv(self) -> list[str]:
        lands = self.calls("land")
        assert len(lands) == 1, lands
        return lands[0]

    def meeting_root(self) -> Path:
        return (self.repo / ROOT).absolute()


def opt(argv: list[str], flag: str) -> str | None:
    return argv[argv.index(flag) + 1] if flag in argv else None


@pytest.fixture
def env(tmp_path, monkeypatch, cli_runner) -> Env:
    return Env(tmp_path, monkeypatch, cli_runner)


# --------------------------------------------------------------------- instance selection


def test_given_a_configured_meeting_when_run_with_no_date_then_the_newest_instance_lands(
    env,
):
    result = env.run("team-standup")
    assert result.exit_code == 0, result.output
    argv = env.land_argv()
    assert opt(argv, "--date") == "2026-10-20"
    assert "--time" not in argv
    assert opt(argv, "--conv") == CONV
    assert opt(argv, "--title") == TITLE
    assert opt(argv, "--out-root") == str(env.meeting_root())
    assert opt(argv, "--tz") == "America/New_York"


def test_given_the_newest_day_has_two_instances_when_run_with_no_date_then_the_newest_lands_with_its_time(
    env,
):
    env.fixture["instances"] = INSTANCES[1:]
    result = env.run("team-standup")
    assert result.exit_code == 0, result.output
    argv = env.land_argv()
    assert opt(argv, "--date") == "2026-10-19"
    assert opt(argv, "--time") == "14:02"


def test_given_two_instances_on_a_date_when_run_without_time_then_it_exits_2_naming_both(
    env,
):
    result = env.run("team-standup", "--date", "2026-10-19")
    assert result.exit_code == EXIT_REFUSED
    assert "10:00" in result.stderr and "14:02" in result.stderr
    assert "Team Stand-up-20261019_140000UTC-Meeting Recording.mp4" in result.stderr
    assert "Team Stand-up-20261019_180200UTC-Meeting Recording.mp4" in result.stderr
    assert "--time" in result.stderr
    assert env.calls("land") == []


def test_given_two_instances_on_a_date_when_time_matches_one_then_only_it_lands_with_time_passed(
    env,
):
    result = env.run("team-standup", "--date", "2026-10-19", "--time", "14:30")
    assert result.exit_code == 0, result.output
    argv = env.land_argv()
    assert opt(argv, "--date") == "2026-10-19"
    assert opt(argv, "--time") == "14:02"


def test_given_two_instances_on_a_date_when_time_matches_neither_then_it_exits_2_listing_them(
    env,
):
    result = env.run("team-standup", "--date", "2026-10-19", "--time", "12:00")
    assert result.exit_code == EXIT_REFUSED
    assert "no instance" in result.stderr
    assert "10:00" in result.stderr and "14:02" in result.stderr
    assert env.calls("land") == []


def test_given_a_configured_time_when_a_date_has_two_instances_then_it_picks_like_time(
    env,
):
    env.config.write_text(
        CONFIG.replace('series = "team"', 'series = "team"\ntime = "10:15"'),
        encoding="utf-8",
    )
    result = env.run("team-standup", "--date", "2026-10-19")
    assert result.exit_code == 0, result.output
    assert opt(env.land_argv(), "--time") == "10:00"


def test_given_one_instance_on_a_date_when_run_then_no_time_is_passed(env):
    result = env.run("standup", "--date", "2026-10-20")
    assert result.exit_code == 0, result.output
    argv = env.land_argv()
    assert opt(argv, "--date") == "2026-10-20"
    assert "--time" not in argv


def test_given_no_instance_at_all_when_run_with_no_date_then_it_exits_2(env):
    env.fixture["instances"] = []
    result = env.run("team-standup")
    assert result.exit_code == EXIT_REFUSED
    assert "--date" in result.stderr
    assert env.calls("land") == []


# --------------------------------------------------------------------- AI Summary


MISSING_LINES = [
    "vtt: wrote {data}/{title} {date}.vtt (10 bytes) from OneDrive transcript t1",
    "AI Summary: MISSING (the Loop page has no such heading and no --ai-summary file was given)",
    "notes pdf: wrote {data}/{title} {date} (no AI Summary).pdf (20 bytes) from Graph content?format=pdf",
]


def test_given_land_reports_the_ai_summary_missing_when_run_then_it_exits_1_with_request_and_inbox(
    env,
):
    env.fixture["land"] = {"lines": MISSING_LINES, "exit": 1}
    result = env.run("team-standup", "--date", "2026-10-20")
    inbox = (
        env.meeting_root()
        / "2026-10-20"
        / "data"
        / f"{TITLE} 2026-10-20 AI summary.txt"
    )
    assert result.exit_code == 1
    lines = result.stdout.splitlines()
    assert any(line.startswith("AI Summary: MISSING") for line in lines)
    assert (
        f"AI Summary request: teams.py recap-summary --chat 'Team Stand-up' --date 2026-10-20 --out '{inbox}' ({TITLE})"
        in lines
    )
    assert f"AI Summary inbox: {inbox}" in lines
    assert inbox.is_absolute()


def test_given_land_fails_for_another_reason_when_run_then_no_request_is_printed(env):
    env.fixture["land"] = {
        "lines": ["vtt: FAILED no transcript route answered with WEBVTT"],
        "exit": 1,
    }
    result = env.run("team-standup", "--date", "2026-10-20")
    assert result.exit_code == 1
    assert "AI Summary request:" not in result.output


def test_given_an_inbox_file_when_run_then_it_is_passed_as_ai_summary(env):
    inbox = (
        env.meeting_root()
        / "2026-10-20"
        / "data"
        / f"{TITLE} 2026-10-20 AI summary.txt"
    )
    inbox.parent.mkdir(parents=True)
    inbox.write_text("- a point\n", encoding="utf-8")
    result = env.run("team-standup", "--date", "2026-10-20")
    assert result.exit_code == 0, result.output
    assert opt(env.land_argv(), "--ai-summary") == str(inbox)


def test_given_one_of_several_instances_when_run_then_the_inbox_name_carries_its_time(
    env,
):
    data = env.meeting_root() / "2026-10-19" / "data"
    data.mkdir(parents=True)
    (data / f"{TITLE} 2026-10-19 AI summary.txt").write_text("day\n", encoding="utf-8")
    timed = data / f"{TITLE} 2026-10-19 1402 AI summary.txt"
    timed.write_text("- a point\n", encoding="utf-8")
    result = env.run("team-standup", "--date", "2026-10-19", "--time", "14:00")
    assert result.exit_code == 0, result.output
    assert opt(env.land_argv(), "--ai-summary") == str(timed)


def test_given_one_of_several_instances_missing_its_summary_when_run_then_the_timed_inbox_is_printed(
    env,
):
    env.fixture["land"] = {"lines": MISSING_LINES, "exit": 1}
    result = env.run("team-standup", "--date", "2026-10-19", "--time", "10:00")
    timed = (
        env.meeting_root()
        / "2026-10-19"
        / "data"
        / f"{TITLE} 2026-10-19 1000 AI summary.txt"
    )
    assert result.exit_code == 1
    assert f"AI Summary inbox: {timed}" in result.stdout.splitlines()


def test_given_no_inbox_file_when_run_then_no_ai_summary_is_passed(env):
    assert env.run("team-standup", "--date", "2026-10-20").exit_code == 0
    assert "--ai-summary" not in env.land_argv()


def test_given_an_explicit_ai_summary_and_an_inbox_when_run_then_the_explicit_file_wins(
    env,
):
    inbox = (
        env.meeting_root()
        / "2026-10-20"
        / "data"
        / f"{TITLE} 2026-10-20 AI summary.txt"
    )
    inbox.parent.mkdir(parents=True)
    inbox.write_text("inbox\n", encoding="utf-8")
    explicit = env.tmp / "pasted.txt"
    explicit.write_text("pasted\n", encoding="utf-8")
    result = env.run(
        "team-standup", "--date", "2026-10-20", "--ai-summary", str(explicit)
    )
    assert result.exit_code == 0, result.output
    assert opt(env.land_argv(), "--ai-summary") == str(explicit)


# --------------------------------------------------------------------- explicit conv or chat


def test_given_conv_and_date_with_no_config_entry_when_run_then_that_date_lands(env):
    env.config.write_text("[defaults]\n", encoding="utf-8")
    result = env.run(
        "--conv",
        "19:other@thread.v2",
        "--date",
        "2026-10-20",
        "--root",
        "notes",
        "--title",
        "Other",
    )
    assert result.exit_code == 0, result.output
    argv = env.land_argv()
    assert opt(argv, "--conv") == "19:other@thread.v2"
    assert opt(argv, "--out-root") == str((env.repo / "notes").absolute())
    assert opt(argv, "--title") == "Other"


def test_given_chat_and_date_with_no_config_entry_when_run_then_the_exact_topic_lands_titled_by_it(
    env,
):
    env.config.write_text("[defaults]\n", encoding="utf-8")
    env.fixture["chats"] = [
        {"conv": "19:near@thread.v2", "topic": "Design Sync (old)"},
        {"conv": "19:exact@thread.v2", "topic": "Design Sync"},
    ]
    result = env.run("--chat", "Design Sync", "--date", "2026-10-20", "--root", "notes")
    assert result.exit_code == 0, result.output
    assert env.calls("chats")[0][1:] == ["--query", "Design Sync", "--json"]
    argv = env.land_argv()
    assert opt(argv, "--conv") == "19:exact@thread.v2"
    assert opt(argv, "--title") == "Design Sync"


def test_given_a_chat_with_no_exact_topic_when_run_then_it_exits_2_listing_near_matches(
    env,
):
    env.fixture["chats"] = [{"conv": "19:near@thread.v2", "topic": "Design Sync (old)"}]
    result = env.run("--chat", "Design Sync", "--date", "2026-10-20", "--root", "notes")
    assert result.exit_code == EXIT_REFUSED
    assert "Design Sync (old)" in result.stderr
    assert env.calls("land") == []


def test_given_conv_without_date_or_name_when_run_then_it_exits_2(env):
    result = env.run("--conv", CONV, "--root", "notes", "--title", "T")
    assert result.exit_code == EXIT_REFUSED
    assert "--date" in result.stderr


def test_given_conv_without_root_and_no_entry_when_run_then_it_exits_2_naming_root(env):
    result = env.run("--conv", CONV, "--date", "2026-10-20", "--title", "T")
    assert result.exit_code == EXIT_REFUSED
    assert "--root" in result.stderr


# --------------------------------------------------------------------- names and repo


def test_given_an_unknown_name_when_run_then_it_exits_2_listing_every_configured_name(
    env,
):
    result = env.run("no-such-meeting")
    assert result.exit_code == EXIT_REFUSED
    assert "team-standup" in result.stderr
    assert "weekly-review" in result.stderr
    assert "standup" in result.stderr
    assert env.calls() == []


def test_given_an_alias_when_run_then_its_meeting_lands(env):
    assert env.run("standup").exit_code == 0
    assert opt(env.land_argv(), "--conv") == CONV


def test_given_list_when_run_then_every_configured_meeting_prints(env):
    result = env.run("--list")
    assert result.exit_code == 0
    assert "team-standup" in result.stdout and "weekly-review" in result.stdout
    assert env.calls() == []


def test_given_an_unresolvable_repo_when_run_then_it_exits_2_naming_repo_path(env):
    env.config.write_text(
        CONFIG.replace(f'repo = "{REPO}"', 'repo = "absent-repo"'), encoding="utf-8"
    )
    result = env.run("team-standup")
    assert result.exit_code == EXIT_REFUSED
    assert "--repo-path" in result.stderr
    assert env.calls("land") == []


def test_given_the_repo_beside_the_current_one_when_run_then_it_lands_there(env):
    sibling = env.repo.parent / "absent-repo"
    sibling.mkdir()
    _git(sibling, "init", "-q")
    env.config.write_text(
        CONFIG.replace(f'repo = "{REPO}"', 'repo = "absent-repo"'), encoding="utf-8"
    )
    assert env.run("team-standup").exit_code == 0
    assert opt(env.land_argv(), "--out-root") == str((sibling / ROOT).absolute())


def _commit(repo: Path) -> None:
    _git(
        repo,
        "-c",
        "user.name=T",
        "-c",
        "user.email=t@example.invalid",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "s",
    )


def test_given_repo_path_to_a_worktree_of_the_configured_repo_when_run_then_it_lands_there(
    env,
):
    _commit(env.repo)
    linked = env.tmp / "any-directory-name"
    _git(env.repo, "worktree", "add", "-q", str(linked))
    result = env.run("team-standup", "--repo-path", str(linked))
    assert result.exit_code == 0, result.output
    assert opt(env.land_argv(), "--out-root") == str((linked / ROOT).resolve())


def test_given_a_name_and_repo_path_to_another_repository_when_run_then_it_exits_2_naming_the_configured_repo(
    env,
):
    other = env.tmp / "elsewhere"
    other.mkdir()
    _git(other, "init", "-q")
    _git(
        other,
        "remote",
        "add",
        "origin",
        "https://example.invalid/org/team-workspace.git",
    )
    result = env.run("team-standup", "--repo-path", str(other))
    assert result.exit_code == EXIT_REFUSED, result.output
    assert f"'{REPO}'" in result.stderr
    assert "--conv or --chat" in result.stderr
    assert CONV not in result.stderr
    assert env.calls() == []


def test_given_a_name_and_repo_naming_another_repository_when_run_then_it_exits_2_naming_the_configured_repo(
    env,
):
    sibling = env.repo.parent / "team-workspace"
    sibling.mkdir()
    _git(sibling, "init", "-q")
    restrict(env, [REPO, "team-workspace"], "docs/meetings")
    result = env.run("team-standup", "--repo", "team-workspace")
    assert result.exit_code == EXIT_REFUSED, result.output
    assert f"'{REPO}'" in result.stderr
    assert "--conv or --chat" in result.stderr
    assert "--date" in result.stderr and "--root" in result.stderr
    assert CONV not in result.stderr
    assert env.calls() == []


def test_given_a_name_and_repo_naming_its_own_repository_when_run_then_it_lands(env):
    assert env.run("team-standup", "--repo", REPO).exit_code == 0
    assert opt(env.land_argv(), "--out-root") == str(env.meeting_root())


# --------------------------------------------------------------------- where artefacts may land


def restrict(env, allowed: list[str], prefix: str) -> None:
    rules = (
        f"allowed_repos = {json.dumps(allowed)}\nmeetings_prefix = {json.dumps(prefix)}"
    )
    env.config.write_text(
        CONFIG.replace('author = "Ada Rivera"', f'author = "Ada Rivera"\n{rules}'),
        encoding="utf-8",
    )


def test_given_a_repo_path_outside_any_git_work_tree_when_run_then_it_exits_2(env):
    scratch = env.tmp / "scratch"
    scratch.mkdir()
    result = env.run("team-standup", "--repo-path", str(scratch))
    assert result.exit_code == EXIT_REFUSED
    assert "not inside a git work tree" in result.stderr
    assert env.calls() == []


def test_given_an_allowlist_without_the_repo_when_run_then_it_exits_2_naming_the_allowed_and_prefix(
    env,
):
    restrict(env, ["meeting-notes", "team-workspace"], "docs/meetings")
    result = env.run("team-standup")
    assert result.exit_code == EXIT_REFUSED
    assert "meeting-notes, team-workspace" in result.stderr
    assert "docs/meetings" in result.stderr
    assert env.calls() == []


def test_given_an_allowlist_with_the_repo_and_root_under_the_prefix_when_run_then_it_lands(
    env,
):
    restrict(env, [REPO, "team-workspace"], "docs/meetings")
    assert env.run("team-standup").exit_code == 0
    assert opt(env.land_argv(), "--out-root") == str(env.meeting_root())


def test_given_a_root_outside_the_meetings_prefix_when_run_then_it_exits_2(env):
    restrict(env, [], "docs/meetings")
    result = env.run("team-standup", "--root", "tmp/landing")
    assert result.exit_code == EXIT_REFUSED
    assert "docs/meetings" in result.stderr
    assert env.calls() == []


def test_given_explicit_conv_root_and_repo_path_when_they_break_the_rules_then_it_exits_2(
    env,
):
    restrict(env, [REPO], "docs/meetings")
    other = env.tmp / "other-repo"
    other.mkdir()
    _git(other, "init", "-q")
    argv = ["--conv", CONV, "--date", "2026-10-20", "--title", "T"]
    outside = env.run(*argv, "--root", "notes", "--repo-path", str(env.repo))
    assert outside.exit_code == EXIT_REFUSED
    wrong_repo = env.run(*argv, "--root", ROOT, "--repo-path", str(other))
    assert wrong_repo.exit_code == EXIT_REFUSED
    assert "'other-repo'" in wrong_repo.stderr
    assert env.calls() == []


def test_given_the_ad_hoc_form_and_repo_naming_the_other_allowed_repository_when_run_then_it_lands_there(
    env,
):
    sibling = env.repo.parent / "team-workspace"
    sibling.mkdir()
    _git(sibling, "init", "-q")
    restrict(env, [REPO, "team-workspace"], "docs/meetings")
    argv = ["--conv", CONV, "--date", "2026-10-20", "--title", "T", "--root", ROOT]
    result = env.run(*argv, "--repo", "team-workspace")
    assert result.exit_code == 0, result.output
    assert opt(env.land_argv(), "--out-root") == str((sibling / ROOT).absolute())


def test_given_the_ad_hoc_form_and_repo_path_to_another_repository_when_run_then_it_lands_there(
    env,
):
    other = env.tmp / "elsewhere"
    other.mkdir()
    _git(other, "init", "-q")
    argv = ["--conv", CONV, "--date", "2026-10-20", "--title", "T", "--root", ROOT]
    result = env.run(*argv, "--repo-path", str(other))
    assert result.exit_code == 0, result.output
    assert opt(env.land_argv(), "--out-root") == str((other / ROOT).resolve())


def test_given_repo_naming_a_repository_outside_the_allowlist_when_run_then_it_exits_2(
    env,
):
    sibling = env.repo.parent / "personal-notes"
    sibling.mkdir()
    _git(sibling, "init", "-q")
    restrict(env, [REPO], "")
    argv = ["--conv", CONV, "--date", "2026-10-20", "--title", "T", "--root", ROOT]
    result = env.run(*argv, "--repo", "personal-notes")
    assert result.exit_code == EXIT_REFUSED
    assert env.calls() == []


@pytest.mark.parametrize("bad_root", ["ABSOLUTE", "../outside", "docs/../../outside"])
def test_given_a_root_leaving_the_repository_when_run_with_no_prefix_then_it_exits_2(
    env, bad_root
):
    root = str(env.tmp / "outside") if bad_root == "ABSOLUTE" else bad_root
    result = env.run("team-standup", "--root", root)
    assert result.exit_code == EXIT_REFUSED, result.output
    assert "inside the repository" in result.stderr
    assert env.calls() == []


def test_given_a_root_through_a_symlink_out_of_the_repository_when_run_then_it_exits_2(
    env,
):
    outside = env.tmp / "outside"
    outside.mkdir()
    (env.repo / "docs").mkdir()
    (env.repo / "docs" / "escape").symlink_to(outside, target_is_directory=True)
    restrict(env, [REPO], "docs")
    result = env.run("team-standup", "--root", "docs/escape/team")
    assert result.exit_code == EXIT_REFUSED, result.output
    assert "inside the repository" in result.stderr
    assert env.calls() == []


def test_given_a_meetings_prefix_that_climbs_out_when_run_then_it_exits_3(env):
    restrict(env, [], "../elsewhere")
    result = env.run("team-standup")
    assert result.exit_code == EXIT_CONFIG
    assert "defaults.meetings_prefix" in result.stderr


def test_given_a_linked_worktree_without_origin_when_run_then_the_main_worktree_name_matches(
    env, monkeypatch
):
    _git(env.repo, "remote", "remove", "origin")
    _commit(env.repo)
    linked = env.tmp / "linked-checkout"
    _git(env.repo, "worktree", "add", "-q", str(linked))
    monkeypatch.chdir(linked)
    result = env.run("team-standup")
    assert result.exit_code == 0, result.output
    assert opt(env.land_argv(), "--out-root") == str(
        (linked.resolve() / ROOT).absolute()
    )


# --------------------------------------------------------------------- --json


def test_given_json_when_run_then_one_object_carries_every_field(env):
    env.fixture["land"] = {"lines": MISSING_LINES, "exit": 1}
    env.fixture["chats"] = [{"conv": "19:weekly@thread.v2", "topic": "Weekly Review"}]
    result = env.run("weekly-review", "--date", "2026-10-20", "--json")
    assert result.exit_code == 1
    report = json.loads(result.stdout)
    data = (env.repo / "docs/meetings/reviews").absolute() / "2026-10-20" / "data"
    assert report == {
        "meeting": "weekly-review",
        "date": "2026-10-20",
        "time": "10:01",
        "conv": "19:weekly@thread.v2",
        "title": "Weekly Review",
        "repo_path": str(env.repo),
        "meeting_dir": str(data.parent),
        "data_dir": str(data),
        "artefacts": [
            f"{data}/Weekly Review 2026-10-20.vtt",
            f"{data}/Weekly Review 2026-10-20 (no AI Summary).pdf",
        ],
        "ai_summary": None,
        "complete": False,
        "missing": [MISSING_LINES[1]],
        "meeting_type": "review",
        "series": "weekly",
        "author": "Ada Rivera",
        "slug": "weekly-review-analysis-2026-10-20",
        "ad_hoc": False,
    }
    assert "AI Summary inbox:" in result.stderr


def test_given_json_and_an_entry_without_slug_when_run_then_slug_is_null(env):
    result = env.run("team-standup", "--json")
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["slug"] is None
    assert report["complete"] is True
    assert report["time"] == "10:01"


def test_given_land_exits_4_when_run_then_the_front_end_exits_4(env):
    env.fixture["land"] = {"lines": [], "exit": 4}
    assert env.run("team-standup").exit_code == EXIT_AUTH


# --------------------------------------------------------------------- config and transport faults


@pytest.mark.parametrize(
    ("text", "key"),
    [
        (
            '[meetings.x]\nchat = "c"\nrepo = "r"\nroot = "d"\ntime = "25:99"\n',
            "meetings.x.time",
        ),
        (
            '[meetings.x]\nchat = "c"\nrepo = "r"\nroot = "d"\ncolour = "red"\n',
            "meetings.x.colour",
        ),
        ('[meetings.x]\nrepo = "r"\nroot = "d"\n', "meetings.x"),
        (
            '[meetings.x]\nchat = "c"\nrepo = "r"\nroot = "d"\nslug = "no-date"\n',
            "meetings.x.slug",
        ),
        ('[defaults]\ntz = "Mars/Olympus"\n', "defaults.tz"),
        ('[defaults]\nai_summary_request = "{nope}"\n', "defaults.ai_summary_request"),
    ],
)
def test_given_a_malformed_config_when_run_then_it_exits_3_naming_file_and_key(
    env, text, key
):
    bad = env.tmp / "bad.toml"
    bad.write_text(text, encoding="utf-8")
    result = env.run("team-standup", "--config", str(bad))
    assert result.exit_code == EXIT_CONFIG
    assert str(bad) in result.stderr
    assert key in result.stderr
    assert env.calls() == []


def test_given_a_config_that_is_not_toml_when_run_then_it_exits_3_naming_the_file(env):
    env.config.write_text("[defaults\n", encoding="utf-8")
    result = env.run("team-standup")
    assert result.exit_code == EXIT_CONFIG
    assert str(env.config) in result.stderr


def test_given_a_missing_transport_when_run_then_it_exits_3_naming_the_primitive(env):
    env.transport = [str(env.tmp / "no-such-transport")]
    result = env.run("team-standup")
    assert result.exit_code == EXIT_CONFIG
    assert "instances" in result.stderr


def test_given_a_transport_printing_non_json_when_run_then_it_exits_3_naming_the_primitive(
    env,
):
    env.fixture["instances"] = "not json at all"
    result = env.run("team-standup")
    assert result.exit_code == EXIT_CONFIG
    assert "instances" in result.stderr


def test_given_a_secret_looking_value_in_a_bad_field_when_run_then_it_is_never_printed(
    env,
):
    secret = "eyJhbGciOiJSUzI1NiJ9.fake-bearer-token-0123456789"
    env.fixture["instances"] = [{**INSTANCES[0], "time": secret}]
    result = env.run("team-standup")
    assert result.exit_code == EXIT_CONFIG
    assert "time" in result.stderr
    assert secret not in result.stdout
    assert secret not in result.stderr


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("time", 1001),
        ("time", "10h01"),
        ("date", "20-10-2026"),
        ("date", None),
        ("recording", None),
        ("start_utc", 0),
        ("message_id", ["m3"]),
    ],
)
def test_given_an_instance_with_a_bad_field_when_run_then_it_exits_3_naming_instances(
    env, key, value
):
    env.fixture["instances"] = [{**INSTANCES[0], key: value}]
    result = env.run("team-standup")
    assert result.exit_code == EXIT_CONFIG, result.output
    assert "instances" in result.stderr
    assert key in result.stderr
    assert env.calls("land") == []


# --------------------------------------------------------------------- the shared library


def test_given_the_app_when_built_then_it_comes_from_skill_cli_foundation(
    env, monkeypatch
):
    import skill_cli_foundation  # noqa: PLC0415

    built = []

    def spy(name, **kwargs):
        app = skill_cli_foundation.make_app(name, **kwargs)
        built.append(app)
        return app

    monkeypatch.setattr(cli, "make_app", spy)
    app = cli.build_app(env.transport, [env.config])
    assert built == [app]
    assert env.runner.invoke(app, ["--list"]).exit_code == 0
