"""Behaviour of the teams-message-draft skill's scripts/teams_drafts.py, through its CLI.

Acceptance criteria, mapped one-to-one below:

1. When an entry becomes Sent or Dropped, ``archive`` moves the whole entry under
   Archived Messages > Sent Messages or Dropped and updates the TOC and the tracking table.
2. When the doc's structure is wrong, ``validate`` exits non-zero and names the entry.
3. When the target working tree has uncommitted changes, ``archive`` refuses and says so.
4. The drafts repo and file come from a config file read at call time, with
   flags > env > TOML profile table > TOML defaults.
5. An "AI-generated draft" is plain text under its heading: ``validate`` refuses one wrapped
   whole in a code fence or a blockquote, because the wrapper comes along when the sender copies
   it. A code block for one command inside a plain draft stays allowed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent / "skills" / "teams-message-draft"
sys.path.insert(0, str(SKILL / "scripts"))
import teams_drafts  # noqa: E402

DOC_REL = "docs/teams-chats/demo-teams-message-drafts.md"
MOVED_IN_FIXTURE = 3  # two Sent entries and one Dropped entry start in pending
REFUSED = teams_drafts.EXIT_REFUSED


def entry(tag: str, day: str, slug: str, title: str, **opts: str) -> str:
    sent = opts.get("sent", "Not sent")
    body = opts.get("body", "Draft text.")
    sent_body = opts.get("sent_body", "Not sent yet.")
    status = {"Pending": "Pending", "Sent": "Sent", "Dropped": "Dropped: superseded"}[
        tag
    ]
    return f"""---

## [{tag}] {day} - {title}

### Metadata ({day} {slug})

| Field | Value |
|---|---|
| Status | {status} |
| Drafted | {day} |
| Last updated | {day} |
| Sent | {sent} |
| Screenshot | Not captured |

Why it exists.

### AI-generated draft ({day} {slug})

{body}

### Message actually sent ({day} {slug})

{sent_body}

### Notes on the differences ({day} {slug})

Not sent yet.
"""


DISQOVER_BODY = """```text
## [Sent] 2026-01-01 - inside a fence: not an entry
```

First paragraph.

---

A transcribed horizontal rule above must stay inside this entry."""


def unarchived_doc() -> str:
    """The migrated layout before the first archive run: every entry still in pending."""
    entries = "\n".join(
        [
            entry("Pending", "2026-10-14", "new-ask", "Robin: a new ask"),
            entry(
                "Sent",
                "2026-10-13",
                "aims-key",
                "AIMS chat: the key",
                sent="2026-10-14 9:46 AM",
            ),
            entry("Dropped", "2026-10-12", "old-facts", "Robin: three facts"),
            entry(
                "Sent",
                "2026-10-09",
                "disqover",
                "DISQOVER chat: status field",
                sent="2026-10-09",
                sent_body=DISQOVER_BODY,
            ),
            entry("Pending", "2026-10-02", "ctms", "RDC team: CTMS partitions"),
        ]
    )
    return f"""# Teams message drafts - demo

Intro.

## Table of contents

<!-- doc:region name="toc" kind="generated" -->
<!-- /doc:region name="toc" -->

## Voice and style guide

### Learned from sent messages

1. A rule.

<!-- doc:region name="pending-messages" kind="entries" -->

{entries}
<!-- /doc:region name="pending-messages" -->

## Archived Messages

### Sent Messages

<!-- doc:region name="sent-messages" kind="entries" -->
<!-- /doc:region name="sent-messages" -->

### Dropped

<!-- doc:region name="dropped-messages" kind="entries" -->
<!-- /doc:region name="dropped-messages" -->

## Tracking table

<!-- doc:region name="tracking-table" kind="generated" -->
<!-- /doc:region name="tracking-table" -->

## Earlier drafts in this project

- older.md
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        timeout=60,
        capture_output=True,
        text=True,
    ).stdout


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    """A linked worktree of a repo whose drafts doc is committed, clean."""
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-b", "main")
    _git(main, "config", "user.email", "test@example.invalid")
    _git(main, "config", "user.name", "Test User")
    doc = main / DOC_REL
    doc.parent.mkdir(parents=True)
    doc.write_text(unarchived_doc(), encoding="utf-8")
    _git(main, "add", ".")
    _git(main, "commit", "-m", "drafts")
    wt = tmp_path / "wt"
    _git(main, "worktree", "add", "-b", "wt/drafts", str(wt))
    return wt


def run(capsys, *argv: str) -> tuple[int, str]:
    code = teams_drafts.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def archive(capsys, wt: Path, *extra: str) -> tuple[int, str]:
    return run(capsys, "archive", "--repo", str(wt), "--file", DOC_REL, *extra)


def headings(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("#")]


def section(text: str, start: str, end: str) -> str:
    return text[text.index(start) : text.index(end)]


def archived_text() -> str:
    return teams_drafts.archive_text(unarchived_doc()).text


# --- AC 1: archive moves Sent and Dropped entries and updates the TOC and tracking table


def test_sent_and_dropped_entries_in_pending_when_archived_then_filed_under_archive_with_toc_and_table(
    capsys, worktree
):
    code, out = archive(capsys, worktree)
    text = (worktree / DOC_REL).read_text(encoding="utf-8")

    assert code == 0, out
    assert out.count("moved:") == MOVED_IN_FIXTURE
    pending = section(text, 'name="pending-messages"', "## Archived Messages")
    sent = section(text, "### Sent Messages", "### Dropped")
    dropped = section(text, "### Dropped", "## Tracking table")
    assert [h for h in headings(pending) if "[" in h] == [
        "## [Pending] 2026-10-14 - Robin: a new ask",
        "## [Pending] 2026-10-02 - RDC team: CTMS partitions",
    ]
    assert [h for h in headings(sent) if h.startswith("#### ")] == [
        "#### [Sent] 2026-10-13 - AIMS chat: the key",
        "#### [Sent] 2026-10-09 - DISQOVER chat: status field",
    ]
    assert "##### Metadata (2026-10-13 aims-key)" in sent
    assert "#### [Dropped] 2026-10-12 - Robin: three facts" in dropped
    assert "- [Archived Messages](#archived-messages)" in text
    assert (
        "    - [[Sent] 2026-10-13 - AIMS chat: the key](#sent-2026-10-13---aims-chat-the-key)"
        in text
    )
    assert "      - [Metadata](#metadata-2026-10-13-aims-key)" in text
    assert (
        "| [AIMS chat: the key](#sent-2026-10-13---aims-chat-the-key) | Sent | 2026-10-13 | 2026-10-13 | 2026-10-14 |"
        in text
    )
    code, out = run(capsys, "validate", str(worktree / DOC_REL))
    assert code == 0, out


def test_entry_with_fenced_heading_and_rule_when_archived_then_moves_whole_and_intact(
    capsys, worktree
):
    archive(capsys, worktree)
    text = (worktree / DOC_REL).read_text(encoding="utf-8")

    sent = section(text, "### Sent Messages", "### Dropped")
    assert DISQOVER_BODY in sent
    assert (
        "## [Sent] 2026-01-01 - inside a fence" in sent
    )  # fenced: never shifted, never an entry


def test_archived_doc_when_archived_again_then_nothing_moves_and_file_is_unchanged(
    capsys, worktree
):
    archive(capsys, worktree)
    _git(worktree, "commit", "-am", "archived")
    before = (worktree / DOC_REL).read_text(encoding="utf-8")

    code, out = archive(capsys, worktree)

    assert code == 0
    assert "moved: nothing" in out and "unchanged" in out
    assert (worktree / DOC_REL).read_text(encoding="utf-8") == before


def test_archived_entry_set_back_to_pending_when_archived_then_returns_to_pending(
    capsys, worktree
):
    archive(capsys, worktree)
    _git(worktree, "commit", "-am", "archived")
    code, out = run(
        capsys,
        "set-status",
        "--repo",
        str(worktree),
        "--file",
        DOC_REL,
        "2026-10-12 old-facts",
        "Pending",
        "--date",
        "2026-10-20",
    )
    assert code == 0, out
    _git(worktree, "commit", "-am", "reopened")

    code, out = archive(capsys, worktree)
    text = (worktree / DOC_REL).read_text(encoding="utf-8")

    assert code == 0, out
    pending = section(text, 'name="pending-messages"', "## Archived Messages")
    assert "## [Pending] 2026-10-12 - Robin: three facts" in pending
    assert "| Last updated | 2026-10-20 |" in pending
    assert "No entries yet." in section(text, "### Dropped", "## Tracking table")


def test_pending_entry_marked_sent_when_set_status_then_archive_files_it_under_sent(
    capsys, worktree
):
    code, out = run(
        capsys,
        "set-status",
        "--repo",
        str(worktree),
        "--file",
        DOC_REL,
        "2026-10-14 new-ask",
        "Sent",
        "--sent",
        "2026-10-15 10:02 AM",
        "--date",
        "2026-10-15",
    )
    assert code == 0, out
    _git(worktree, "commit", "-am", "sent")

    archive(capsys, worktree)
    text = (worktree / DOC_REL).read_text(encoding="utf-8")

    sent = section(text, "### Sent Messages", "### Dropped")
    assert sent.index("#### [Sent] 2026-10-14 - Robin: a new ask") < sent.index(
        "#### [Sent] 2026-10-13"
    )
    assert "| Sent | 2026-10-15 10:02 AM |" in sent
    assert (
        "| [Robin: a new ask](#sent-2026-10-14---robin-a-new-ask) | Sent | 2026-10-14 | 2026-10-15 | 2026-10-15 |"
        in text
    )


def test_sent_without_send_date_when_set_status_then_refused_and_file_unchanged(
    capsys, worktree
):
    before = (worktree / DOC_REL).read_text(encoding="utf-8")

    code, out = run(
        capsys,
        "set-status",
        "--repo",
        str(worktree),
        "--file",
        DOC_REL,
        "2026-10-14 new-ask",
        "Sent",
    )

    assert code == REFUSED and "--sent" in out
    assert (worktree / DOC_REL).read_text(encoding="utf-8") == before


def test_doc_whose_result_would_not_validate_when_archived_then_nothing_written(
    capsys, worktree
):
    doc = worktree / DOC_REL
    doc.write_text(
        doc.read_text(encoding="utf-8").replace(
            "| Status | Dropped: superseded |", "| Status | Pending |"
        ),
        encoding="utf-8",
    )
    _git(worktree, "commit", "-am", "inconsistent status")
    before = doc.read_text(encoding="utf-8")

    code, out = archive(capsys, worktree)

    assert code == 1
    assert "Robin: three facts" in out and "disagrees with Metadata Status" in out
    assert "not written" in out
    assert doc.read_text(encoding="utf-8") == before


# --- AC 2: the validator exits non-zero and names the offending entry


def test_archived_doc_when_validated_then_passes(capsys, tmp_path):
    path = tmp_path / "d.md"
    path.write_text(archived_text(), encoding="utf-8")

    code, out = run(capsys, "validate", str(path))

    assert code == 0 and f"PASS {path}" in out


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        pytest.param(
            lambda t: t.replace(
                "## Earlier drafts",
                '<!-- doc:region name="bogus" kind="entries" -->\n\n## Earlier drafts',
            ),
            "unknown marker region 'bogus'",
            id="unknown-marker",
        ),
        pytest.param(
            lambda t: t.replace('<!-- /doc:region name="dropped-messages" -->', ""),
            "'dropped-messages' is never closed",
            id="unbalanced-marker",
        ),
        pytest.param(
            lambda t: t.replace(
                "## [Pending] 2026-10-14 - Robin: a new ask",
                "## [Sent] 2026-10-14 - Robin: a new ask",
            ).replace("| Status | Pending |", "| Status | Sent |", 1),
            "Robin: a new ask' (line 54) is [Sent] but outside the archive",
            id="sent-outside-archive",
        ),
        pytest.param(
            lambda t: t.replace(
                "#### [Dropped] 2026-10-12", "#### [Pending] 2026-10-12"
            ).replace("| Status | Dropped: superseded |", "| Status | Pending |"),
            "three facts' (line 188) is [Pending] but inside the archive",
            id="pending-inside-archive",
        ),
        pytest.param(
            lambda t: t.replace(
                "  - [Message actually sent](#message-actually-sent-2026-10-14-new-ask)\n",
                "",
            ),
            "table of contents is missing: - [Message actually sent](#message-actually-sent-2026-10-14-new-ask)",
            id="toc-row-missing",
        ),
        pytest.param(
            lambda t: t.replace(
                "| Dropped | 2026-10-12 | 2026-10-12 |",
                "| Pending | 2026-10-12 | 2026-10-12 |",
            ),
            "tracking table is missing: | [Robin: three facts]",
            id="tracking-row-disagrees",
        ),
    ],
)
def test_broken_structure_when_validated_then_exit_nonzero_naming_the_entry(
    capsys, tmp_path, mutate, expected
):
    good = archived_text()
    bad = mutate(good)
    assert bad != good
    path = tmp_path / "d.md"
    path.write_text(bad, encoding="utf-8")

    code, out = run(capsys, "validate", str(path))

    assert code == 1
    assert expected in out


NEW_ASK_DRAFT = "### AI-generated draft (2026-10-14 new-ask)\n\nDraft text."


@pytest.mark.parametrize(
    "wrapped",
    [
        pytest.param("```text\nDraft text.\n\nSecond paragraph.\n```", id="fenced"),
        pytest.param("~~~\nDraft text.\n~~~", id="tilde-fenced"),
        pytest.param("> Draft text.\n>\n> Second paragraph.", id="blockquoted"),
        pytest.param(
            "> Draft text that runs on\nlazily to a second line.\n\n> Second paragraph.",
            id="lazy-blockquote",
        ),
        pytest.param("    Draft text.\n\n    Second paragraph.", id="indented-code"),
        pytest.param(
            "<!-- reviewer note -->\n\n```text\nDraft text.\n```\n\n<!-- end -->",
            id="fenced-between-comments",
        ),
    ],
)
def test_wrapped_ai_draft_when_validated_then_exit_nonzero_naming_the_entry(
    capsys, tmp_path, wrapped
):
    good = archived_text()
    assert NEW_ASK_DRAFT in good
    path = tmp_path / "d.md"
    path.write_text(
        good.replace(
            NEW_ASK_DRAFT,
            f"### AI-generated draft (2026-10-14 new-ask)\n\n{wrapped}",
        ),
        encoding="utf-8",
    )

    code, out = run(capsys, "validate", str(path))

    assert code == 1
    assert "Robin: a new ask" in out
    assert "AI-generated draft" in out and "plain text" in out


@pytest.mark.parametrize(
    "plain",
    [
        pytest.param(
            '```shell\n# the domain lookup\nmdb query "cognito domain"\n```\n\n'
            "Can you run this and send me the output?\n\n"
            "> quoted line from the ticket\n\nThanks.",
            id="command-block-first",
        ),
        pytest.param(
            "> quoted line from the ticket\n```shell\nmdb query x\n```",
            id="quote-then-fence-no-blank",
        ),
        pytest.param("> quoted line\n- a list item after it", id="quote-then-list"),
        pytest.param(
            "<!-- note --> Plain text after a comment.\n\n> quoted line",
            id="text-after-inline-comment",
        ),
    ],
)
def test_plain_ai_draft_with_message_code_or_quote_when_validated_then_passes(
    capsys, tmp_path, plain
):
    good = archived_text()
    path = tmp_path / "d.md"
    path.write_text(
        good.replace(
            NEW_ASK_DRAFT,
            f"### AI-generated draft (2026-10-14 new-ask)\n\n{plain}",
        ),
        encoding="utf-8",
    )

    code, out = run(capsys, "validate", str(path))

    assert code == 0, out


# --- AC 3: refuse a dirty working tree (and a main tree)


@pytest.mark.parametrize("change", ["modified", "untracked"])
def test_uncommitted_change_when_archive_runs_then_refuses_and_writes_nothing(
    capsys, worktree, change
):
    doc = worktree / DOC_REL
    if change == "modified":
        doc.write_text(doc.read_text(encoding="utf-8") + "\nedit\n", encoding="utf-8")
    else:
        (worktree / "uploads").mkdir()
        (worktree / "uploads" / "screenshot.png").write_bytes(b"png")
    before = doc.read_text(encoding="utf-8")

    code, out = archive(capsys, worktree)

    assert code == REFUSED
    assert "uncommitted changes" in out
    assert doc.read_text(encoding="utf-8") == before


def test_main_working_tree_when_archive_runs_then_refuses(capsys, worktree):
    main = worktree.parent / "main"
    before = (main / DOC_REL).read_text(encoding="utf-8")

    code, out = archive(capsys, main)

    assert code == REFUSED and "main working tree" in out
    assert (main / DOC_REL).read_text(encoding="utf-8") == before


# --- AC 4: location comes from a config file read at call time


def _config(tmp_path: Path) -> Path:
    path = tmp_path / "drafts.toml"
    path.write_text(
        """
[defaults]
project = "demo"
[defaults.projects.demo]
repo = "/defaults/repo"
drafts_file = "docs/teams-chats/demo.md"
[profiles.box-a.projects.demo]
repo = "/profile/repo"
""",
        encoding="utf-8",
    )
    return path


def _resolve(capsys, *argv: str) -> dict:
    code, out = run(capsys, "resolve", "--json", *argv)
    assert code == 0, out
    return json.loads(out)


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(({}, (), "/defaults/repo"), id="defaults"),
        pytest.param(
            ({"TEAMS_DRAFTS_PROFILE": "box-a"}, (), "/profile/repo"),
            id="profile-over-defaults",
        ),
        pytest.param(
            ({"TEAMS_DRAFTS_PROFILE": "box-a"}, ("--profile", "none"), "/defaults/repo"),
            id="profile-flag-over-profile-env",
        ),
        pytest.param(
            (
                {"TEAMS_DRAFTS_PROFILE": "box-a", "TEAMS_DRAFTS_REPO": "/env/repo"},
                (),
                "/env/repo",
            ),
            id="env-over-profile",
        ),
        pytest.param(
            (
                {"TEAMS_DRAFTS_REPO": "/env/repo"},
                ("--repo", "/flag/repo"),
                "/flag/repo",
            ),
            id="flag-over-env",
        ),
    ],
)
def test_config_layers_when_resolved_then_flags_over_env_over_profile_over_defaults(
    capsys, monkeypatch, tmp_path, case
):
    env, argv, repo = case
    for name in (
        "TEAMS_DRAFTS_PROFILE",
        "TEAMS_DRAFTS_REPO",
        "TEAMS_DRAFTS_FILE",
        "TEAMS_DRAFTS_PROJECT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TEAMS_DRAFTS_CONFIG", str(_config(tmp_path)))
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    resolved = _resolve(capsys, *argv)

    assert resolved["repo"] == repo
    assert resolved["drafts_file"] == f"{repo}/docs/teams-chats/demo.md"


def test_config_edited_between_calls_when_resolved_then_second_call_sees_the_edit(
    capsys, monkeypatch, tmp_path
):
    monkeypatch.delenv("TEAMS_DRAFTS_PROFILE", raising=False)
    monkeypatch.delenv("TEAMS_DRAFTS_REPO", raising=False)
    path = _config(tmp_path)
    monkeypatch.setenv("TEAMS_DRAFTS_CONFIG", str(path))
    assert _resolve(capsys)["repo"] == "/defaults/repo"

    path.write_text(
        path.read_text(encoding="utf-8").replace("/defaults/repo", "/moved/repo"),
        encoding="utf-8",
    )

    assert _resolve(capsys)["repo"] == "/moved/repo"


def test_unknown_project_when_resolved_then_refused(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("TEAMS_DRAFTS_CONFIG", str(_config(tmp_path)))

    code, out = run(capsys, "resolve", "--project", "nope")

    assert code == REFUSED and "'nope' is not configured" in out


def _clear_location_env(monkeypatch) -> None:
    for name in (
        "TEAMS_DRAFTS_PROFILE",
        "TEAMS_DRAFTS_CONFIG",
        "TEAMS_DRAFTS_REPO",
        "TEAMS_DRAFTS_FILE",
        "TEAMS_DRAFTS_PROJECT",
    ):
        monkeypatch.delenv(name, raising=False)


def test_given_the_example_config_when_resolved_then_it_names_the_example_drafts_doc(
    capsys, monkeypatch, tmp_path
):
    _clear_location_env(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TEAMS_DRAFTS_CONFIG", str(SKILL / "config.example.toml"))

    resolved = _resolve(capsys)

    assert resolved["project"] == "example"
    assert resolved["repo"] == f"{tmp_path}/work/example-notes"
    assert resolved["drafts_file"].endswith(
        "example-notes/docs/teams-chats/example-teams-message-drafts.md"
    )


def test_given_no_config_file_at_the_default_path_when_resolved_then_refused_naming_it(
    capsys, monkeypatch, tmp_path
):
    _clear_location_env(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))

    code, out = run(capsys, "resolve")

    assert code == REFUSED
    assert f"config file not found: {tmp_path}/.config/teams-message-draft/config.toml" in out


def test_given_no_config_file_and_both_locations_as_flags_when_resolved_then_the_flags_locate_the_doc(
    capsys, monkeypatch, tmp_path
):
    _clear_location_env(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))

    resolved = _resolve(capsys, "--repo", "/flag/repo", "--file", "docs/drafts.md")

    assert resolved["repo"] == "/flag/repo"
    assert resolved["drafts_file"] == "/flag/repo/docs/drafts.md"


def test_shipped_template_when_validated_then_passes(capsys):
    template = SKILL / "templates" / "TEMPLATE.md"

    code, out = run(capsys, "validate", str(template))

    assert code == 0, out
