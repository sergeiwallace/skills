"""`meeting_resolve.py plan --meeting-root`: a meeting directory outside docs/meetings/<group>/<author>.

Driven through the Typer app, the entry point the skill runs.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "skills" / "meeting-analysis" / "scripts"))
meeting_resolve = importlib.import_module("meeting_resolve")
runner = pytest.importorskip("typer.testing").CliRunner()

DAY = "2026-10-20"
EXIT_CONFLICT = 2


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in ("MEETING_ANALYSIS_AUTHOR", "MEETING_ANALYSIS_SERIES", "MEETING_ANALYSIS_GROUP"):
        monkeypatch.delenv(key, raising=False)


def plan(tmp_path: Path, *extra: str):
    template = tmp_path / "docs/meetings/TEMPLATE.md"
    template.parent.mkdir(parents=True, exist_ok=True)
    template.write_text("template\n", encoding="utf-8")
    argv = [
        "plan",
        "--repo-root",
        str(tmp_path),
        "--date",
        DAY,
        "--series",
        "team",
        "--kind",
        "recording-analysis",
        "--config",
        str(tmp_path / "absent.toml"),
        "--json",
        *extra,
    ]
    return runner.invoke(meeting_resolve.app, argv)


def test_given_a_meeting_root_when_plan_runs_then_the_doc_and_its_dirs_sit_under_root_date(
    tmp_path,
):
    result = plan(tmp_path, "--meeting-root", "notes/team", "--author", "Ada Rivera")
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    date_dir = tmp_path.resolve() / "notes/team" / DAY
    assert report["doc"] == str(date_dir / f"team-standup-analysis-{DAY}.md")
    assert report["data_dir"] == str(date_dir / "data")
    assert report["derived_dir"] == str(date_dir / "derived")
    assert report["conflict"] is None


def test_given_a_meeting_root_and_an_explicit_slug_when_plan_runs_then_the_slug_names_the_doc(
    tmp_path,
):
    slug = f"weekly-review-analysis-{DAY}"
    result = plan(
        tmp_path,
        "--meeting-root",
        "notes/reviews",
        "--group",
        "reviews",
        "--slug",
        slug,
    )
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["doc"] == str(
        tmp_path.resolve() / "notes/reviews" / DAY / f"{slug}.md"
    )
    assert report["meeting_type"] == "review"


def test_given_a_meeting_root_and_no_author_when_plan_runs_then_no_author_is_required(
    tmp_path,
):
    result = plan(tmp_path, "--meeting-root", "notes/team")
    assert result.exit_code == 0, result.output


def test_given_no_meeting_root_when_plan_runs_then_the_author_derivation_is_unchanged(
    tmp_path,
):
    result = plan(tmp_path, "--author", "Ada Rivera")
    assert result.exit_code == 0, result.output
    expected = (
        tmp_path.resolve()
        / "docs/meetings/standups/ada-rivera"
        / DAY
        / f"team-standup-analysis-{DAY}.md"
    )
    assert json.loads(result.output)["doc"] == str(expected)


def test_given_a_doc_under_the_meeting_root_when_plan_runs_then_it_reports_the_conflict(
    tmp_path,
):
    doc = tmp_path / "notes/team" / DAY / f"team-standup-analysis-{DAY}.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("# written\n", encoding="utf-8")
    result = plan(tmp_path, "--meeting-root", "notes/team")
    assert result.exit_code == EXIT_CONFLICT
    assert "already exists" in json.loads(result.output)["conflict"]


def test_given_a_meeting_dir_and_a_slug_when_plan_runs_then_the_ad_hoc_doc_sits_in_that_one_directory(
    tmp_path,
):
    slug = f"vendor-transition-review-{DAY}"
    result = plan(tmp_path, "--meeting-dir", f"docs/meetings/{slug}", "--slug", slug)
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    meeting_dir = tmp_path.resolve() / "docs/meetings" / slug
    assert report["doc"] == str(meeting_dir / f"{slug}.md")
    assert report["data_dir"] == str(meeting_dir / "data")
    assert report["derived_dir"] == str(meeting_dir / "derived")
    assert report["conflict"] is None


def test_given_a_meeting_dir_without_a_slug_when_plan_runs_then_it_refuses_naming_the_slug(
    tmp_path,
):
    result = plan(tmp_path, "--meeting-dir", f"docs/meetings/topic-{DAY}")
    assert result.exit_code != 0
    assert "--slug" in result.output


def test_given_both_a_meeting_dir_and_a_meeting_root_when_plan_runs_then_it_refuses(
    tmp_path,
):
    result = plan(
        tmp_path,
        "--meeting-dir",
        f"docs/meetings/topic-{DAY}",
        "--slug",
        "topic",
        "--meeting-root",
        "notes",
    )
    assert result.exit_code != 0
    assert "not both" in result.output
