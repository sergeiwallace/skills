"""skills/teams/scripts/meeting.py: the shared meeting front end wired to this skill's teams.py.

Run as shipped, through `uv run --script`. The transport is the real teams.py; a token file
that does not exist makes its first call exit 4 before any request, so these tests reach no
network.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL = REPO_ROOT / "skills" / "teams"
WRAPPER = SKILL / "scripts" / "meeting.py"
EXAMPLE_CONFIG = SKILL / "config.example.toml"
EXIT_AUTH = 4

pytestmark = pytest.mark.skipif(shutil.which("uv") is None, reason="uv runs the PEP 723 script")


def run_wrapper(tmp_path: Path, config: Path, *argv: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "TEAMS_CONFIG": str(config), "HOME": str(tmp_path / "home")}
    env.pop("TEAMS_PROFILE", None)
    return subprocess.run(
        [shutil.which("uv"), "run", "--script", str(WRAPPER), *argv],
        cwd=tmp_path,
        env=env,
        timeout=600,
        capture_output=True,
        text=True,
        check=False,
    )


def test_given_the_example_config_when_meeting_lists_then_its_example_meetings_print(
    tmp_path,
):
    result = run_wrapper(tmp_path, EXAMPLE_CONFIG, "--list")
    assert result.returncode == 0, result.stderr
    names = [line.split("\t")[0] for line in result.stdout.splitlines()]
    assert names == ["team-standup", "weekly-review"]


def test_given_no_config_file_when_meeting_lists_then_it_exits_3_naming_the_path(tmp_path):
    missing = tmp_path / "nowhere" / "teams.toml"
    result = run_wrapper(tmp_path, missing, "--list")
    assert result.returncode == 3, result.stderr  # noqa: PLR2004 - the config exit code
    assert str(missing) in result.stderr


def test_given_no_token_when_meeting_runs_then_teams_py_is_the_transport_and_its_exit_4_passes_through(
    tmp_path,
):
    teams_config = tmp_path / "teams.toml"
    teams_config.write_text(
        "[defaults]\n"
        'login_hint = "Run: example-broker login"\n'
        "[defaults.json-file]\n"
        f'path = "{tmp_path / "no-such-tokens.json"}"\n',
        encoding="utf-8",
    )
    subprocess.run([shutil.which("git"), "init", "-q", str(tmp_path)], timeout=60, check=True)
    result = run_wrapper(
        tmp_path,
        teams_config,
        "--conv",
        "19:example@thread.v2",
        "--date",
        "2026-10-19",
        "--root",
        "notes",
        "--title",
        "Example",
        "--repo-path",
        str(tmp_path),
    )
    assert result.returncode == EXIT_AUTH, result.stderr
    assert "not authenticated" in result.stderr
    assert "Run: example-broker login" in result.stderr
