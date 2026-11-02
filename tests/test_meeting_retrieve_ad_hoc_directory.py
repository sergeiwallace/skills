"""An ad hoc meeting lands in one `<slug>-<date>/` directory with `data/` inside it.

A configured, recurring meeting keeps `<root>/<date>/`. The slug is the title lowercased
with every run of non-alphanumerics replaced by one hyphen.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
for pkg in ("meeting-retrieve", "skill-cli-foundation"):
    sys.path.insert(0, str(REPO_ROOT / "packages" / pkg / "src"))

cli = pytest.importorskip("meeting_retrieve.cli")
from meeting_retrieve.transport import LandResult  # noqa: E402

DAY = "2026-10-22"
TITLE = "Vendor Transition Review"
CONV = "19:meeting_adhoc@thread.v2"


class FakeTransport:
    def __init__(self) -> None:
        self.land_args: list[str] = []

    def chats(self, query: str) -> list[dict]:  # noqa: ARG002 - the Transport interface
        return [
            {"topic": TITLE, "conv": CONV},
            {"topic": "[Team] Stand-up", "conv": "19:standup@thread.v2"},
        ]

    def instances(self, conv: str, date: str | None, tz: str | None) -> list[dict]:  # noqa: ARG002 - the Transport interface
        return [
            {
                "date": DAY,
                "time": "10:00",
                "start_utc": f"{DAY}T14:00:00Z",
                "recording": "rec.mp4",
                "message_id": "m1",
            }
        ]

    def land(self, args: list[str], *, echo_to_stderr: bool) -> LandResult:  # noqa: ARG002 - the Transport interface
        self.land_args = list(args)
        out_root = Path(args[args.index("--out-root") + 1])
        if "--dir-name" in args:
            dir_name = args[args.index("--dir-name") + 1]
        else:
            dir_name = args[args.index("--date") + 1]
        landed = out_root / dir_name / "data" / "x.vtt"
        return LandResult(code=0, lines=[f"vtt: wrote {landed} (3 bytes)"])


@pytest.fixture
def world(tmp_path):
    repo = tmp_path / "notes-repo"
    (repo / "docs" / "meetings").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    config = tmp_path / "meetings.toml"
    config.write_text(
        "[defaults]\n"
        f'allowed_repos = ["{repo.name}"]\n'
        'meetings_prefix = "docs/meetings"\n'
        'author = "Ada Rivera"\n'
        "[meetings.team-standup]\n"
        'chat = "[Team] Stand-up"\n'
        f'repo = "{repo.name}"\n'
        'root = "docs/meetings/standups/ada-rivera"\n'
        'series = "team"\n'
        'meeting_type = "standup"\n',
        encoding="utf-8",
    )
    return repo, config


def run(transport: FakeTransport, config: Path, repo: Path, capsys, **kw) -> dict:
    options = {
        "name": None,
        "day": DAY,
        "time": None,
        "conv": None,
        "chat": None,
        "title": None,
        "root": None,
        "repo_path": repo,
        "ai_summary": None,
        "force": False,
        "json_output": True,
        "list_meetings": False,
    }
    options.update(kw)
    code = cli.run(transport, [config], **options)
    captured = capsys.readouterr()
    assert code == 0, captured.out + captured.err
    return json.loads(captured.out)


def test_given_a_title_when_slugged_then_it_is_lowercase_hyphenated_alphanumerics():
    assert cli.meeting_slug(TITLE) == "vendor-transition-review"
    assert cli.meeting_slug("[Team] Review: Q4 / plans") == "team-review-q4-plans"


def test_given_an_ad_hoc_chat_when_meeting_runs_then_it_lands_in_slug_date_under_the_meetings_prefix(
    world, capsys
):
    repo, config = world
    transport = FakeTransport()
    report = run(transport, config, repo, capsys, chat=TITLE)
    slug = f"vendor-transition-review-{DAY}"
    assert "--dir-name" in transport.land_args
    assert transport.land_args[transport.land_args.index("--dir-name") + 1] == slug
    meeting_dir = repo.resolve() / "docs" / "meetings" / slug
    assert report["meeting_dir"] == str(meeting_dir)
    assert report["data_dir"] == str(meeting_dir / "data")
    assert report["slug"] == slug
    assert report["ad_hoc"] is True


def test_given_an_ad_hoc_chat_and_a_root_when_meeting_runs_then_the_root_is_the_slug_dirs_parent(
    world, capsys
):
    repo, config = world
    report = run(
        FakeTransport(), config, repo, capsys, chat=TITLE, root="docs/meetings/reviews"
    )
    expected = (
        repo.resolve() / "docs/meetings/reviews" / f"vendor-transition-review-{DAY}"
    )
    assert report["meeting_dir"] == str(expected)


def test_given_a_configured_meeting_when_meeting_runs_then_the_date_directory_shape_is_unchanged(
    world, capsys
):
    repo, config = world
    transport = FakeTransport()
    report = run(transport, config, repo, capsys, name="team-standup")
    assert "--dir-name" not in transport.land_args
    assert report["meeting_dir"] == str(
        repo.resolve() / "docs/meetings/standups/ada-rivera" / DAY
    )
    assert report["slug"] is None
    assert report["ad_hoc"] is False
