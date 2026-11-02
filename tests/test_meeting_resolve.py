"""Tests for skills/meeting-analysis/scripts/meeting_resolve.py.

Covers the five things the resolver exists to stop being re-derived by hand, each of
which has been gotten wrong at least once in practice:
which doc kind applies, where the doc goes, whether one is already there, what each
supplied source is, and whether a vendored copy of the unit is still intact.

Nothing is mocked. The kind matrix, the path derivation, the zip refusals, the template
preference and the vendor-lock verification all run against real files in `tmp_path`,
and the settings precedence chain runs through the real pydantic-settings sources with
an injected TOML path rather than a stub of them.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "skills" / "meeting-analysis" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

meeting_resolve = importlib.import_module("meeting_resolve")

KINDS = meeting_resolve.KINDS
RECORDING_ANALYSIS = meeting_resolve.RECORDING_ANALYSIS
STANDUP_UPDATE = meeting_resolve.STANDUP_UPDATE
MeetingPathError = meeting_resolve.MeetingPathError
Source = meeting_resolve.Source
VENDOR_LOCK_RELPATH = meeting_resolve.VENDOR_LOCK_RELPATH

# importorskip on `typer.testing` rather than a module-level import: the skip must
# happen when typer is absent, and importing the submodule imports typer itself, so
# this is the same guard in one statement instead of a statement plus a deferred
# import. The old `typer` binding was never read.
CliRunner = pytest.importorskip("typer.testing").CliRunner

runner = CliRunner()

_ENV_KEYS = ("MEETING_ANALYSIS_AUTHOR", "MEETING_ANALYSIS_SERIES", "MEETING_ANALYSIS_GROUP")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """No ambient `MEETING_ANALYSIS_*` leaks into a precedence assertion."""
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture()
def missing_toml(tmp_path: Path) -> Path:
    return tmp_path / "does-not-exist.toml"


def _source(name: str) -> Source:
    return meeting_resolve.classify_source(Path(name))


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# Kind selection: the discriminator is whether there is a meeting to analyze
# --------------------------------------------------------------------------


class TestSelectKind:
    def test_kind_when_recording_supplied_then_recording_analysis(self) -> None:
        kind, why = meeting_resolve.select_kind([_source("meeting.mp4")])
        assert kind.name == RECORDING_ANALYSIS
        assert "recording" in why

    def test_kind_when_only_transcript_supplied_then_recording_analysis(self) -> None:
        kind, why = meeting_resolve.select_kind([_source("[Team Sync] Weekly.vtt")])
        assert kind.name == RECORDING_ANALYSIS
        assert "transcript" in why

    def test_kind_when_only_notes_supplied_then_analysis_with_stated_degradation(
        self,
    ) -> None:
        """A notes export is still a meeting artifact, so it is not an update."""
        kind, why = meeting_resolve.select_kind([_source("recap.pdf")])
        assert kind.name == RECORDING_ANALYSIS
        assert "notes" in why
        assert "untrusted" in why

    def test_kind_when_no_meeting_artifacts_then_standup_update(self) -> None:
        kind, why = meeting_resolve.select_kind([])
        assert kind.name == STANDUP_UPDATE
        assert "no recording" in why

    def test_kind_when_override_given_then_override_beats_the_artifacts(self) -> None:
        """The operator may write tomorrow's update while yesterday's recording sits there."""
        kind, why = meeting_resolve.select_kind(
            [_source("meeting.mp4")], override=STANDUP_UPDATE
        )
        assert kind.name == STANDUP_UPDATE
        assert "override" in why

    def test_kind_when_override_unknown_then_refuses_and_lists_the_valid_ones(
        self,
    ) -> None:
        with pytest.raises(MeetingPathError) as excinfo:
            meeting_resolve.select_kind([], override="standup-prep")
        assert "unknown kind" in str(excinfo.value)
        assert RECORDING_ANALYSIS in str(excinfo.value)

    def test_missing_modalities_when_transcript_only_then_names_the_two_absent(
        self,
    ) -> None:
        assert meeting_resolve.missing_modalities([_source("a.vtt")]) == [
            "recording",
            "notes",
        ]


class TestClassifySource:
    def test_role_when_docx_supplied_then_unusable_with_an_actionable_reason(
        self,
    ) -> None:
        source = _source("recap.docx")
        assert source.role == "unusable"
        assert "PDF" in source.reason

    def test_role_when_srt_supplied_then_unusable_because_only_webvtt_is_parsed(
        self,
    ) -> None:
        assert "WebVTT" in _source("captions.srt").reason

    def test_role_when_suffix_unknown_then_unusable_names_the_suffix(self) -> None:
        assert ".xyz" in _source("thing.xyz").reason


# --------------------------------------------------------------------------
# The current slug stems and template versions
# --------------------------------------------------------------------------


class TestKindConstants:
    def test_slug_when_recording_analysis_then_current_stem(self) -> None:
        assert (
            KINDS[RECORDING_ANALYSIS].slug("project-sync", "2026-08-25")
            == "project-sync-standup-analysis-2026-08-25"
        )

    def test_slug_when_standup_update_then_current_stem(self) -> None:
        assert (
            KINDS[STANDUP_UPDATE].slug("project-sync", "2026-08-25")
            == "project-sync-standup-update-2026-08-25"
        )

    def test_versions_when_read_then_match_the_committed_templates(self) -> None:
        """The versions are load-bearing: a doc's frontmatter is selected on them."""
        for kind in KINDS.values():
            template = (meeting_resolve.SKILL_REPO_ROOT / kind.template_relpath).read_text(encoding="utf-8")
            assert kind.template_version in template


# --------------------------------------------------------------------------
# Path derivation
# --------------------------------------------------------------------------


class TestDerivePaths:
    def test_author_when_given_as_display_name_then_kebab_cased_segment(
        self, tmp_path: Path
    ) -> None:
        paths = meeting_resolve.derive_paths(
            repo_root=tmp_path,
            kind=KINDS[RECORDING_ANALYSIS],
            author="Rivera, Ada",
            date="2026-08-25",
            series="project-sync",
        )
        assert paths.author == "rivera-ada"
        assert paths.doc == (
            tmp_path
            / "docs/meetings/standups/rivera-ada/2026-08-25/project-sync-standup-analysis-2026-08-25.md"
        )

    def test_doc_when_derived_then_date_appears_in_directory_and_filename(
        self, tmp_path: Path
    ) -> None:
        paths = meeting_resolve.derive_paths(
            repo_root=tmp_path,
            kind=KINDS[STANDUP_UPDATE],
            author="ada-rivera",
            date="2026-08-25",
            series="project-sync",
        )
        assert paths.doc.parent.name == "2026-08-25"
        assert paths.doc.name.endswith("2026-08-25.md")

    def test_meeting_type_when_group_is_reviews_then_singular_group(
        self, tmp_path: Path
    ) -> None:
        paths = meeting_resolve.derive_paths(
            repo_root=tmp_path,
            kind=KINDS[RECORDING_ANALYSIS],
            author="ada-rivera",
            date="2026-08-25",
            series="project-sync",
            group="reviews",
            slug="project-sync-design-review-2026-08-25",
        )
        assert paths.meeting_type == "review"

    def test_meeting_type_when_update_then_always_standup_update(
        self, tmp_path: Path
    ) -> None:
        paths = meeting_resolve.derive_paths(
            repo_root=tmp_path,
            kind=KINDS[STANDUP_UPDATE],
            author="ada-rivera",
            date="2026-08-25",
            series="project-sync",
        )
        assert paths.meeting_type == "standup-update"

    def test_author_when_it_has_no_alphanumerics_then_refuses(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(MeetingPathError) as excinfo:
            meeting_resolve.derive_paths(
                repo_root=tmp_path,
                kind=KINDS[RECORDING_ANALYSIS],
                author="   ---  ",
                date="2026-08-25",
                series="project-sync",
            )
        assert "author" in str(excinfo.value)

    def test_date_when_not_iso_then_refuses(self, tmp_path: Path) -> None:
        with pytest.raises(MeetingPathError) as excinfo:
            meeting_resolve.derive_paths(
                repo_root=tmp_path,
                kind=KINDS[RECORDING_ANALYSIS],
                author="ada-rivera",
                date="08/25/2026",
                series="project-sync",
            )
        assert "YYYY-MM-DD" in str(excinfo.value)

    def test_slug_when_group_is_not_standups_then_requires_an_explicit_one(
        self, tmp_path: Path
    ) -> None:
        """Inventing a series name for reviews/ would create one nobody agreed to."""
        with pytest.raises(MeetingPathError) as excinfo:
            meeting_resolve.derive_paths(
                repo_root=tmp_path,
                kind=KINDS[RECORDING_ANALYSIS],
                author="ada-rivera",
                date="2026-08-25",
                series="project-sync",
                group="reviews",
            )
        assert "--slug" in str(excinfo.value)

    def test_conflict_when_doc_already_exists_then_reported_with_the_never_rename_rule(
        self, tmp_path: Path
    ) -> None:
        paths = meeting_resolve.derive_paths(
            repo_root=tmp_path,
            kind=KINDS[STANDUP_UPDATE],
            author="ada-rivera",
            date="2026-08-25",
            series="project-sync",
        )
        assert meeting_resolve.existing_doc_conflict(paths) is None
        _write(paths.doc, "# already written\n")
        conflict = meeting_resolve.existing_doc_conflict(paths)
        assert conflict is not None
        assert "never renamed" in conflict
        assert paths.slug in conflict


# --------------------------------------------------------------------------
# Template resolution: the current repo's copy wins, and drift is reported
# --------------------------------------------------------------------------


class TestResolveTemplate:
    def test_template_when_both_exist_then_repo_local_is_preferred(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        kind = KINDS[RECORDING_ANALYSIS]
        local = _write(tmp_path / "repo" / kind.template_relpath, "local\n")
        _write(tmp_path / "skill" / kind.template_relpath, "canonical\n")
        monkeypatch.setattr(meeting_resolve, "SKILL_REPO_ROOT", tmp_path / "skill")
        result = meeting_resolve.resolve_template(kind, tmp_path / "repo")
        assert result["use"] == str(local)
        assert result["source"] == "repo-local"

    def test_template_when_copies_differ_then_drifted_is_true(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        kind = KINDS[RECORDING_ANALYSIS]
        _write(tmp_path / "repo" / kind.template_relpath, "local\n")
        _write(tmp_path / "skill" / kind.template_relpath, "canonical\n")
        monkeypatch.setattr(meeting_resolve, "SKILL_REPO_ROOT", tmp_path / "skill")
        assert (
            meeting_resolve.resolve_template(kind, tmp_path / "repo")["drifted"] is True
        )

    def test_template_when_copies_identical_then_drifted_is_false(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        kind = KINDS[RECORDING_ANALYSIS]
        _write(tmp_path / "repo" / kind.template_relpath, "same\n")
        _write(tmp_path / "skill" / kind.template_relpath, "same\n")
        monkeypatch.setattr(meeting_resolve, "SKILL_REPO_ROOT", tmp_path / "skill")
        assert (
            meeting_resolve.resolve_template(kind, tmp_path / "repo")["drifted"]
            is False
        )

    def test_template_when_repo_has_none_then_falls_back_to_the_skill_repo(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        kind = KINDS[STANDUP_UPDATE]
        canonical = _write(tmp_path / "skill" / kind.template_relpath, "canonical\n")
        monkeypatch.setattr(meeting_resolve, "SKILL_REPO_ROOT", tmp_path / "skill")
        result = meeting_resolve.resolve_template(kind, tmp_path / "repo")
        assert result["use"] == str(canonical)
        assert result["source"] == "skill-repo"

    def test_template_when_neither_exists_then_refuses_naming_both_paths(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        kind = KINDS[STANDUP_UPDATE]
        monkeypatch.setattr(meeting_resolve, "SKILL_REPO_ROOT", tmp_path / "skill")
        with pytest.raises(MeetingPathError) as excinfo:
            meeting_resolve.resolve_template(kind, tmp_path / "repo")
        assert "no template found" in str(excinfo.value)

    def test_template_when_resolved_from_this_repo_then_the_real_copy_is_found(
        self,
    ) -> None:
        """Positive control: with no template in the repo, the bundled copy is found."""
        for kind in KINDS.values():
            result = meeting_resolve.resolve_template(kind, REPO_ROOT)
            assert Path(result["use"]).is_file()


# --------------------------------------------------------------------------
# Zip extraction refusals
# --------------------------------------------------------------------------


def _zip_with(tmp_path: Path, name: str, member: str, payload: bytes = b"x") -> Path:
    archive = tmp_path / name
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(member, payload)
    return archive


class TestExtractSources:
    def test_extract_when_flat_bundle_then_vendor_filenames_are_preserved(
        self, tmp_path: Path
    ) -> None:
        archive = _zip_with(tmp_path, "sources.zip", "[Team Sync] Weekly.vtt")
        written = meeting_resolve.extract_sources(archive, tmp_path / "data")
        assert [p.name for p in written] == ["[Team Sync] Weekly.vtt"]

    def test_extract_when_member_escapes_the_destination_then_refuses(
        self, tmp_path: Path
    ) -> None:
        archive = _zip_with(tmp_path, "evil.zip", "../escaped.vtt")
        with pytest.raises(MeetingPathError) as excinfo:
            meeting_resolve.extract_sources(archive, tmp_path / "data")
        assert "unsafe zip member" in str(excinfo.value)
        assert not (tmp_path / "escaped.vtt").exists()

    @pytest.mark.parametrize("member", ["bundle/.gitignore", ".gitattributes", ".GIT"])
    def test_given_a_bundle_carrying_a_git_control_file_when_extracted_then_it_refuses_before_writing(
        self, tmp_path: Path, member: str
    ) -> None:
        # data/ holds committed evidence; a vendor bundle's
        # `.gitignore` landing there would hide the transcript the doc cites, with no error.
        archive = tmp_path / "sources.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("[Team Sync] Weekly.vtt", b"WEBVTT")
            zf.writestr(member, b"*.vtt\n")
        with pytest.raises(MeetingPathError) as excinfo:
            meeting_resolve.extract_sources(archive, tmp_path / "data")
        assert "git control file" in str(excinfo.value)
        assert list((tmp_path / "data").iterdir()) == []

    def test_extract_when_member_is_absolute_then_refuses(self, tmp_path: Path) -> None:
        archive = _zip_with(tmp_path, "abs.zip", "/etc/passwd")
        with pytest.raises(MeetingPathError):
            meeting_resolve.extract_sources(archive, tmp_path / "data")

    def test_extract_when_target_exists_and_no_overwrite_then_refuses(
        self, tmp_path: Path
    ) -> None:
        archive = _zip_with(tmp_path, "sources.zip", "notes.pdf", b"new")
        _write(tmp_path / "data" / "notes.pdf", "original")
        with pytest.raises(MeetingPathError) as excinfo:
            meeting_resolve.extract_sources(archive, tmp_path / "data")
        assert "--overwrite" in str(excinfo.value)
        assert (tmp_path / "data" / "notes.pdf").read_text(
            encoding="utf-8"
        ) == "original"

    def test_extract_when_overwrite_passed_then_replaces(self, tmp_path: Path) -> None:
        archive = _zip_with(tmp_path, "sources.zip", "notes.pdf", b"new")
        _write(tmp_path / "data" / "notes.pdf", "original")
        meeting_resolve.extract_sources(archive, tmp_path / "data", overwrite=True)
        assert (tmp_path / "data" / "notes.pdf").read_bytes() == b"new"

    def test_extract_when_not_a_zip_then_refuses(self, tmp_path: Path) -> None:
        plain = _write(tmp_path / "sources.zip", "not a zip")
        with pytest.raises(MeetingPathError) as excinfo:
            meeting_resolve.extract_sources(plain, tmp_path / "data")
        assert "not a zip archive" in str(excinfo.value)


# --------------------------------------------------------------------------
# Layered settings: CLI > env MEETING_ANALYSIS_* > TOML > default
# --------------------------------------------------------------------------


class TestResolveSettingsPrecedence:
    def test_fields_when_nothing_set_then_documented_defaults(
        self, missing_toml: Path
    ) -> None:
        settings = meeting_resolve.resolve_settings(config_path=missing_toml)
        assert settings.author == ""
        assert settings.series == ""
        assert settings.group == "standups"

    def test_author_when_only_toml_set_then_file_beats_default(
        self, tmp_path: Path
    ) -> None:
        cfg = _write(
            tmp_path / "meeting-analysis.toml",
            'author = "Ada Rivera"\nseries = "project-sync"\n',
        )
        settings = meeting_resolve.resolve_settings(config_path=cfg)
        assert settings.author == "Ada Rivera"
        assert settings.series == "project-sync"

    def test_author_when_env_and_toml_set_then_env_wins(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        cfg = _write(tmp_path / "meeting-analysis.toml", 'author = "From TOML"\n')
        monkeypatch.setenv("MEETING_ANALYSIS_AUTHOR", "From Env")
        assert meeting_resolve.resolve_settings(config_path=cfg).author == "From Env"

    def test_series_when_cli_and_env_set_then_cli_wins(
        self, missing_toml: Path, monkeypatch
    ) -> None:
        monkeypatch.setenv("MEETING_ANALYSIS_SERIES", "from-env")
        assert (
            meeting_resolve.resolve_settings(
                series="from-cli", config_path=missing_toml
            ).series
            == "from-cli"
        )

    def test_group_when_toml_absent_then_no_crash(self, tmp_path: Path) -> None:
        """Failure path: a missing per-user config is a no-op layer, not an error."""
        missing = tmp_path / "sub" / "meeting-analysis.toml"
        assert not missing.exists()
        assert meeting_resolve.resolve_settings(config_path=missing).group == "standups"


# --------------------------------------------------------------------------
# vendor-check against a lock file
# --------------------------------------------------------------------------


UNIT_GLOBS = ["skills/meeting-analysis/**", "docs/meetings/TEMPLATE.md"]


def _vendored_tree(root: Path) -> dict[str, Path]:
    files = {
        "skills/meeting-analysis/SKILL.md": _write(
            root / "skills/meeting-analysis/SKILL.md", "skill\n"
        ),
        "skills/meeting-analysis/scripts/local_asr.py": _write(
            root / "skills/meeting-analysis/scripts/local_asr.py", "print('asr')\n"
        ),
        "docs/meetings/TEMPLATE.md": _write(
            root / "docs/meetings/TEMPLATE.md", "template\n"
        ),
    }
    return files


def _write_lock(
    root: Path, files: dict[str, Path], *, globs: list[str] | None = None
) -> Path:
    lock = {
        "source_repo": "skills",
        "source_commit": "0" * 40,
        "generated": "2026-08-25T00:00:00Z",
        "unit_globs": globs if globs is not None else UNIT_GLOBS,
        "files": [
            {"path": rel, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for rel, path in sorted(files.items())
        ],
    }
    return _write(root / VENDOR_LOCK_RELPATH, json.dumps(lock, indent=2) + "\n")


class TestVendorCheck:
    def test_check_when_copy_matches_the_lock_then_passes(self, tmp_path: Path) -> None:
        files = _vendored_tree(tmp_path)
        _write_lock(tmp_path, files)
        code, lines = meeting_resolve.vendor_check(tmp_path)
        assert code == 0
        assert "3 file(s) match" in lines[0]

    def test_check_when_a_locked_file_was_edited_then_reports_it_modified(
        self, tmp_path: Path
    ) -> None:
        files = _vendored_tree(tmp_path)
        _write_lock(tmp_path, files)
        files["docs/meetings/TEMPLATE.md"].write_text(
            "locally edited\n", encoding="utf-8"
        )
        code, lines = meeting_resolve.vendor_check(tmp_path)
        assert code == 1
        assert any(
            line.startswith("MODIFIED") and "TEMPLATE.md" in line for line in lines
        )

    def test_check_when_a_locked_file_is_gone_then_reports_it_missing(
        self, tmp_path: Path
    ) -> None:
        files = _vendored_tree(tmp_path)
        _write_lock(tmp_path, files)
        files["skills/meeting-analysis/SKILL.md"].unlink()
        code, lines = meeting_resolve.vendor_check(tmp_path)
        assert code == 1
        assert any(line.startswith("MISSING") and "SKILL.md" in line for line in lines)

    def test_check_when_an_unlocked_file_was_added_then_reports_it_unlocked(
        self, tmp_path: Path
    ) -> None:
        """The glob sweep is the only thing that can see an ADDED file."""
        files = _vendored_tree(tmp_path)
        _write_lock(tmp_path, files)
        _write(
            tmp_path / "skills/meeting-analysis/scripts/local_patch.py",
            "print('mine')\n",
        )
        code, lines = meeting_resolve.vendor_check(tmp_path)
        assert code == 1
        assert any(
            line.startswith("UNLOCKED") and "local_patch.py" in line for line in lines
        )

    def test_check_when_build_residue_present_then_it_is_not_reported(
        self, tmp_path: Path
    ) -> None:
        """Gitignored residue is not part of the unit; flagging it trains operators to ignore the check."""
        files = _vendored_tree(tmp_path)
        _write_lock(tmp_path, files)
        _write(
            tmp_path / "skills/meeting-analysis/scripts/__pycache__/local_asr.pyc",
            "bytecode\n",
        )
        _write(
            tmp_path / "skills/meeting-analysis/scripts/.ruff_cache/CACHEDIR.TAG",
            "cache\n",
        )
        assert meeting_resolve.vendor_check(tmp_path)[0] == 0

    def test_check_when_no_lock_present_then_passes_saying_it_is_canonical(
        self, tmp_path: Path
    ) -> None:
        code, lines = meeting_resolve.vendor_check(tmp_path)
        assert code == 0
        assert "canonical checkout" in lines[0]

    def test_check_when_lock_is_not_json_then_refuses_naming_the_file(
        self, tmp_path: Path
    ) -> None:
        _write(tmp_path / VENDOR_LOCK_RELPATH, "{not json")
        with pytest.raises(MeetingPathError) as excinfo:
            meeting_resolve.vendor_check(tmp_path)
        assert VENDOR_LOCK_RELPATH in str(excinfo.value)


# --------------------------------------------------------------------------
# The CLI surface
# --------------------------------------------------------------------------


class TestCli:
    def test_plan_when_run_on_a_fresh_slot_then_reports_kind_and_paths(
        self, tmp_path: Path, missing_toml: Path
    ) -> None:
        data = tmp_path / "data"
        _write(data / "[Team Sync] Weekly.vtt", "WEBVTT\n")
        _write(tmp_path / "docs/meetings/TEMPLATE.md", "template\n")
        result = runner.invoke(
            meeting_resolve.app,
            [
                "plan",
                "--repo-root",
                str(tmp_path),
                "--sources",
                str(data),
                "--author",
                "Ada Rivera",
                "--date",
                "2026-08-25",
                "--series",
                "project-sync",
                "--config",
                str(missing_toml),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "recording-analysis" in result.output
        assert "project-sync-standup-analysis-2026-08-25.md" in result.output

    def test_plan_when_the_doc_already_exists_then_exit_code_is_two(
        self, tmp_path: Path, missing_toml: Path
    ) -> None:
        """Exit 2, not 1: a conflict is a well-formed answer the skill branches on."""
        _write(tmp_path / "docs/meetings/TEMPLATE.md", "template\n")
        _write(
            tmp_path
            / "docs/meetings/standups/ada-rivera/2026-08-25/project-sync-standup-analysis-2026-08-25.md",
            "# written\n",
        )
        result = runner.invoke(
            meeting_resolve.app,
            [
                "plan",
                "--repo-root",
                str(tmp_path),
                "--author",
                "Ada Rivera",
                "--date",
                "2026-08-25",
                "--series",
                "project-sync",
                "--kind",
                "recording-analysis",
                "--config",
                str(missing_toml),
            ],
        )
        assert result.exit_code == 2, result.output
        assert "CONFLICT" in result.output

    def test_plan_when_date_is_invalid_then_exit_code_is_one(
        self, tmp_path: Path, missing_toml: Path
    ) -> None:
        _write(tmp_path / "docs/meetings/standups/TEMPLATE.md", "template\n")
        result = runner.invoke(
            meeting_resolve.app,
            [
                "plan",
                "--repo-root",
                str(tmp_path),
                "--author",
                "Ada Rivera",
                "--date",
                "yesterday",
                "--series",
                "project-sync",
                "--config",
                str(missing_toml),
            ],
        )
        assert result.exit_code == 1
        assert "YYYY-MM-DD" in result.output

    def test_plan_when_json_requested_then_output_parses(
        self, tmp_path: Path, missing_toml: Path
    ) -> None:
        _write(tmp_path / "docs/meetings/standups/TEMPLATE.md", "template\n")
        result = runner.invoke(
            meeting_resolve.app,
            [
                "plan",
                "--repo-root",
                str(tmp_path),
                "--author",
                "Ada Rivera",
                "--date",
                "2026-08-25",
                "--series",
                "project-sync",
                "--json",
                "--config",
                str(missing_toml),
            ],
        )
        assert result.exit_code == 0, result.output
        report = json.loads(result.output)
        assert report["kind"] == STANDUP_UPDATE
        assert report["meeting_type"] == "standup-update"

    def test_plan_when_sources_is_a_zip_then_says_to_extract_first(
        self, tmp_path: Path, missing_toml: Path
    ) -> None:
        archive = _zip_with(tmp_path, "sources.zip", "notes.pdf")
        _write(tmp_path / "docs/meetings/TEMPLATE.md", "template\n")
        result = runner.invoke(
            meeting_resolve.app,
            [
                "plan",
                "--repo-root",
                str(tmp_path),
                "--sources",
                str(archive),
                "--author",
                "Ada Rivera",
                "--date",
                "2026-08-25",
                "--series",
                "project-sync",
                "--config",
                str(missing_toml),
            ],
        )
        assert result.exit_code == 1
        assert "extract" in result.output

    def test_vendor_check_when_no_lock_then_exit_code_is_zero(
        self, tmp_path: Path
    ) -> None:
        result = runner.invoke(
            meeting_resolve.app, ["vendor-check", "--target", str(tmp_path)]
        )
        assert result.exit_code == 0, result.output
        assert "canonical checkout" in result.output

    def test_vendor_check_when_a_file_was_edited_then_exit_code_is_one(
        self, tmp_path: Path
    ) -> None:
        files = _vendored_tree(tmp_path)
        _write_lock(tmp_path, files)
        files["skills/meeting-analysis/SKILL.md"].write_text(
            "forked\n", encoding="utf-8"
        )
        result = runner.invoke(
            meeting_resolve.app, ["vendor-check", "--target", str(tmp_path)]
        )
        assert result.exit_code == 1
        assert "MODIFIED" in result.output
