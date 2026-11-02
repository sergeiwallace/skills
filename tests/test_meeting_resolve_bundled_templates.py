"""`meeting_resolve.py plan` run the documented way, outside any plugin, finds a template.

The resolver once read its fallback template root from `CLAUDE_PLUGIN_ROOT`, so a skill
installed on its own (not as a plugin), or run from a checkout, died with a KeyError
before printing anything. The templates now ship inside the skill directory.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "skills" / "meeting-analysis" / "scripts" / "meeting_resolve.py"
UV = shutil.which("uv")

pytestmark = pytest.mark.skipif(UV is None, reason="uv runs the PEP 723 script")


def _plan(repo: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if key != "CLAUDE_PLUGIN_ROOT"}
    env["HOME"] = str(repo.parent / "home")
    return subprocess.run(
        [
            str(UV),
            "run",
            "--script",
            str(SCRIPT),
            "plan",
            "--repo-root",
            str(repo),
            "--author",
            "Ada Rivera",
            "--date",
            "2026-08-25",
            "--series",
            "team",
            "--json",
            *extra,
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=600,
        check=False,
    )


def test_given_no_plugin_root_and_no_repo_template_when_planned_then_the_bundled_template_is_used(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "notes"
    repo.mkdir()
    result = _plan(repo, "--kind", "recording-analysis")
    assert result.returncode == 0, result.stderr
    template = json.loads(result.stdout)["template"]
    assert template["source"] == "skill-repo"
    bundled = REPO_ROOT / "skills" / "meeting-analysis" / "assets" / "docs" / "meetings" / "TEMPLATE.md"
    assert Path(template["use"]) == bundled


def test_given_a_repo_template_when_planned_then_the_repo_copy_wins_and_drift_is_reported(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "notes"
    local = repo / "docs" / "meetings" / "standups" / "TEMPLATE.md"
    local.parent.mkdir(parents=True)
    local.write_text("a locally edited template\n", encoding="utf-8")
    result = _plan(repo, "--kind", "standup-update")
    assert result.returncode == 0, result.stderr
    template = json.loads(result.stdout)["template"]
    assert template["source"] == "repo-local"
    assert template["drifted"] is True
