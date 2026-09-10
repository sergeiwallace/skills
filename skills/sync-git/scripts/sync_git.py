#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "typer>=0.12",
#     "pydantic-settings>=2.2",
#     "skill-cli-foundation",
# ]
#
# [tool.uv.sources]
# skill-cli-foundation = { path = "../../../packages/skill-cli-foundation", editable = true }
# ///
"""CLI-only orchestration for the session-local sync transaction.

Git mutations live in ``scripts/git_review_sync.py``.  Keeping this file to
target parsing, intent recording, and report rendering makes it possible to
audit that no second mutation authority has slipped into the skill wrapper.
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

import typer
from pydantic_settings import SettingsConfigDict
from skill_cli_foundation import BaseSkillSettings, DryRunOption, make_app

PLUGIN_ROOT = Path(os.environ["CLAUDE_PLUGIN_ROOT"])


def _load_shared():
    spec = importlib.util.spec_from_file_location(
        "git_review_sync", PLUGIN_ROOT / "assets/git_review_sync.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["git_review_sync"] = module
    spec.loader.exec_module(module)
    return module


shared = _load_shared()


def _load_binding_registry():
    spec = importlib.util.spec_from_file_location(
        "sync_git_session_binding_registry",
        Path(__file__).with_name("session_binding_registry.py"),
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["sync_git_session_binding_registry"] = module
    spec.loader.exec_module(module)
    return module


bindings = _load_binding_registry()
app = make_app("sync-git", multi_command=True)


class SyncGitSettings(BaseSkillSettings):
    """Presentation-only settings; policy and target authority remain canonical."""

    model_config = SettingsConfigDict(
        env_prefix="SYNC_GIT_",
        extra="ignore",
    )

    json_output: bool = False


TargetArgument = typer.Argument(None, metavar="TARGET")
JsonOption = typer.Option(False, "--json", help="Render the typed report as JSON")
AllowDeletionsOption = typer.Option(
    False,
    "--allow-deletions",
    help="Explicitly approve committing and pushing tracked-file deletions",
)
PrepareTargetOption = typer.Option(..., "--target", help="One D-3 TARGET")
IssueOption = typer.Option(..., "--issue", help="Canonical Beads issue identity")
DocumentOption = typer.Option(..., "--design", exists=True, readable=True)
AuditOption = typer.Option(..., "--audit", exists=True, readable=True)
DeclaredPathsArgument = typer.Argument(..., metavar="DECLARED_PATH")


def _json_print(value: object) -> None:
    typer.echo(json.dumps(value, sort_keys=True))


def _render_action(action) -> str:
    target = action.target
    paths = ", ".join(action.changed_paths) if action.changed_paths else "-"
    return " | ".join(
        (
            action.state,
            f"{target.worktree} ({target.branch})",
            paths,
            action.reason,
        )
    )


def _intent_dir(target) -> Path:
    return target.common_dir / "sync-git-intents-v1"


def _path_baseline(target, path: str) -> dict[str, str | None]:
    if not shared._safe_repo_path(path):
        raise shared.SyncGitError(f"declared path escapes repository: {path!r}")
    rc, entry, error = shared._git(["ls-files", "-s", "--", path], target.worktree)
    if rc != 0:
        raise shared.SyncGitError(f"cannot inspect declared path {path}: {error}")
    if entry:
        fields = entry.split()[0:2]
        if len(fields) != 2:
            raise shared.SyncGitError(f"malformed index entry for {path}")
        return {"mode": fields[0], "blob": fields[1], "absence": None}
    if (target.worktree / path).exists() or (target.worktree / path).is_symlink():
        raise shared.SyncGitError(f"untracked declared path must be absent: {path}")
    return {"mode": None, "blob": None, "absence": "confirmed"}


def create_intent_record(
    target,
    *,
    issue: str,
    design: Path,
    audit: Path,
    declared_paths: list[str],
) -> dict[str, object]:
    """Create D-7's immutable pre-change scope record.

    It deliberately does not authorize arbitrary later bytes.  General task work
    remains a ``NEEDS_HUMAN`` outcome in the shared classifier.
    """
    if not issue.strip() or not declared_paths:
        raise shared.SyncGitError(
            "prepare requires an issue and at least one declared path"
        )
    if not design.is_file() or not audit.is_file():
        raise shared.SyncGitError(
            "prepare design and audit paths must be readable files"
        )
    entries = shared.status_entries(target)
    declared = set(declared_paths)
    dirty = sorted(entry.path for entry in entries if entry.path in declared)
    if dirty:
        raise shared.SyncGitError(
            f"declared paths are already dirty: {', '.join(dirty)}"
        )
    head = shared._head(target)
    tree = shared._head(target, "HEAD^{tree}")
    baseline = {path: _path_baseline(target, path) for path in declared_paths}
    record = {
        "api_version": 1,
        "record_id": uuid4().hex,
        "repository": str(target.repo),
        "worktree": str(target.worktree),
        "common_dir": str(target.common_dir),
        "baseline_head": head,
        "baseline_tree": tree,
        "declared_paths": declared_paths,
        "baseline": baseline,
        "issue": issue,
        "design": str(design.resolve()),
        "audit": str(audit.resolve()),
        "completion_rule": "R3/PASS_PREPARED",
    }
    directory = _intent_dir(target)
    directory.mkdir(mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    destination = directory / f"{record['record_id']}.json"
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=".intent-", dir=directory)
        temporary = Path(name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(record, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.link(temporary, destination)
        temporary.unlink()
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except (
        FileExistsError
    ) as exc:  # UUID collision or hostile precreation: do not replace.
        raise shared.SyncGitError(
            "intent record already exists; refusing replacement"
        ) from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return record


def read_intent_record(target, record_id: str) -> dict[str, object]:
    """Read only a private, regular, owner-readable record without following links."""
    if not record_id.isalnum():
        raise shared.SyncGitError("invalid intent record id")
    path = _intent_dir(target) / f"{record_id}.json"
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise shared.SyncGitError("intent record is not a regular file")
    if metadata.st_mode & 0o077:
        raise shared.SyncGitError("intent record permissions are not private")
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise shared.SyncGitError("intent record is unreadable") from exc
    if not isinstance(record, dict) or record.get("api_version") != 1:
        raise shared.SyncGitError("intent record schema is unsupported")
    return record


@app.command()
def sync(
    ctx: typer.Context,
    targets: list[str] = TargetArgument,
    dry_run: DryRunOption = False,
    json_output: bool = JsonOption,
    allow_deletions: bool = AllowDeletionsOption,
) -> None:
    """Assess and safely synchronize the default pair or explicit registered targets."""
    del ctx
    try:
        selected = shared.resolve_sync_targets(Path.cwd(), targets or [])
    except shared.SyncGitError as exc:
        raise typer.Exit(typer.echo(f"REFUSED | {exc}", err=True) or 2)
    actions = []
    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    for selected_target in selected:
        if selected_target.origin == "session" and not dry_run:
            try:
                bindings.verify_session_binding(session_id, selected_target)
            except bindings.BindingError as exc:
                actions.append(
                    shared.SyncAction(
                        "REFUSED",
                        f"owner-session binding is not proved: {exc}",
                        selected_target,
                    )
                )
                continue
        actions.append(
            shared.sync_target(
                selected_target,
                dry_run=dry_run,
                allow_deletions=allow_deletions,
            )
        )
    if json_output or SyncGitSettings().json_output:
        _json_print(
            {
                "schema_version": 1,
                "targets": [shared.action_json(action) for action in actions],
            }
        )
    else:
        for action in actions:
            typer.echo(_render_action(action))
    if any(
        action.state in {"REFUSED", "ANCHOR_FAILED", "ACTION_FAILED", "READBACK_FAILED"}
        for action in actions
    ):
        raise typer.Exit(2)


@app.command()
def prepare(
    ctx: typer.Context,
    target: str = PrepareTargetOption,
    issue: str = IssueOption,
    design: Path = DocumentOption,
    audit: Path = AuditOption,
    declared_paths: list[str] = DeclaredPathsArgument,
) -> None:
    """Record pre-change scope; this is never arbitrary-byte commit authority."""
    del ctx
    try:
        selected = shared.resolve_sync_targets(Path.cwd(), [target])
        if len(selected) != 1:
            raise shared.SyncGitError(
                "prepare target must resolve exactly one worktree"
            )
        record = create_intent_record(
            selected[0],
            issue=issue,
            design=design,
            audit=audit,
            declared_paths=declared_paths,
        )
    except shared.SyncGitError as exc:
        raise typer.Exit(typer.echo(f"REFUSED | {exc}", err=True) or 2)
    _json_print(record)


if (
    __name__ == "__main__"
):  # pragma: no cover - exercised by the installed skill runtime.
    app()
