"""Shared proof-gated actions used by the session-local ``sync-git`` skill.

The wrapper in :mod:`skills.sync-git.scripts.sync_git` deliberately contains no
Git mutation.  This module owns the narrow local actions: resolve registered
worktrees, screen a mechanical candidate before Git object creation, anchor
pre-change state, commit through an isolated index, and use a non-forcing push.
It is intentionally more conservative than ``git-review``'s fleet sweep:
anything it cannot prove mechanical is reported for a human rather than guessed.
"""

from __future__ import annotations

import fcntl
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import uuid4

try:  # Standalone-script and test-loader compatible imports.
    import git_review_core as core
except ModuleNotFoundError:  # pragma: no cover - exercised by script invocation.
    import importlib.util
    import sys

    _spec = importlib.util.spec_from_file_location(
        "git_review_core", Path(__file__).with_name("git_review_core.py")
    )
    assert _spec is not None and _spec.loader is not None
    core = importlib.util.module_from_spec(_spec)
    sys.modules["git_review_core"] = core
    _spec.loader.exec_module(core)


SyncState = Literal[
    "NO_CHANGE",
    "SYNCED",
    "PUSHED",
    "NEEDS_HUMAN",
    "REFUSED",
    "STALE",
    "ANCHOR_FAILED",
    "ACTION_FAILED",
    "READBACK_FAILED",
]

SENSITIVE_PATH = re.compile(
    r"(^|/)(?:\.env(?:\.[^/]+)?|credentials?(?:\.[^/]+)?|id_rsa|[^/]+\.pem|[^/]+\.key)$",
    re.IGNORECASE,
)
SECRET_BYTES = re.compile(
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    rb"AKIA[0-9A-Z]{16}|"
    rb"github_pat_[A-Za-z0-9_]{20,}|"
    rb"gh[pousr]_[A-Za-z0-9]{20,}",
)
HUMAN_ONLY_SUFFIXES = frozenset(
    {".lock", ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".tar", ".gz"}
)
MECHANICAL_JSONL_PATTERNS_ENV = "SYNC_GIT_MECHANICAL_JSONL_PATTERNS"


class SyncGitError(ValueError):
    """A target or proof cannot be safely interpreted."""


@dataclass(frozen=True)
class SyncTarget:
    """One registered worktree together with its shared Git identity."""

    repo: Path
    worktree: Path
    common_dir: Path
    branch: str
    origin: Literal["main", "session", "explicit"]


@dataclass(frozen=True)
class StatusEntry:
    code: str
    path: str
    old_path: str | None = None


@dataclass(frozen=True)
class CandidateAssessment:
    state: SyncState
    reason: str
    entries: tuple[StatusEntry, ...] = ()
    authorized_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class SyncAction:
    """Redaction-safe result of one shared sync action."""

    state: SyncState
    reason: str
    target: SyncTarget
    head_before: str | None = None
    head_after: str | None = None
    remote_before: str | None = None
    anchor_ref: str | None = None
    changed_paths: tuple[str, ...] = ()
    evidence: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PullRequestAcceptance:
    """Read-back provider result consumed by the post-merge resync only.

    Provider create/merge remains unavailable unless the shared provider engine
    holds an identity-bound broker transaction.  This record deliberately does
    not grant that outward action; it only proves the safe local handoff after a
    provider action has already been accepted and read back.
    """

    pr_id: str
    transaction_key: str
    head_oid: str
    base_branch: str
    result_oid: str


def _git(args: list[str], cwd: Path, *, strip: bool = True) -> tuple[int, str, str]:
    """Use the sibling's scrubbed Git runner for every shared action.

    ``strip`` is forwarded, not swallowed: a wrapper that dropped it would silently
    re-impose the porcelain corruption on every `-z` caller in this module.
    """
    return core._git(args, cwd, env_overrides={"GIT_OPTIONAL_LOCKS": "0"}, strip=strip)


def _required_git(args: list[str], cwd: Path, purpose: str) -> str:
    rc, output, error = _git(args, cwd)
    if rc != 0 or not output:
        raise SyncGitError(f"{purpose}: {error or 'Git returned no result'}")
    return output


def _common_dir(worktree: Path) -> Path:
    value = _required_git(
        ["rev-parse", "--path-format=absolute", "--git-common-dir"],
        worktree,
        "Git common directory is unavailable",
    )
    return Path(value).resolve()


def _branch(worktree: Path) -> str:
    return _required_git(
        ["symbolic-ref", "--quiet", "--short", "HEAD"],
        worktree,
        "detached HEAD is not a sync target",
    )


def _worktree_rows(repo: Path) -> list[tuple[Path, str | None]]:
    """Return registered physical worktrees; porcelain avoids name inference."""
    output = _required_git(
        ["worktree", "list", "--porcelain"], repo, "cannot list registered worktrees"
    )
    rows: list[tuple[Path, str | None]] = []
    current_path: Path | None = None
    current_branch: str | None = None
    for line in [*output.splitlines(), ""]:
        if not line:
            if current_path is not None:
                rows.append((current_path.resolve(), current_branch))
            current_path = None
            current_branch = None
            continue
        if line.startswith("worktree "):
            current_path = Path(line.removeprefix("worktree "))
        elif line.startswith("branch refs/heads/"):
            current_branch = line.removeprefix("branch refs/heads/")
    if not rows:
        raise SyncGitError("Git returned no registered worktrees")
    return rows


def _main_worktree(repo: Path) -> Path:
    return _worktree_rows(repo)[0][0]


def _target_for_worktree(
    worktree: Path, origin: Literal["main", "session", "explicit"]
):
    root = Path(
        _required_git(
            ["rev-parse", "--show-toplevel"], worktree, "target is not a Git worktree"
        )
    ).resolve()
    registered = {path for path, _branch_name in _worktree_rows(root)}
    if root not in registered:
        raise SyncGitError(f"{root} is not an on-disk registered worktree")
    return SyncTarget(
        _main_worktree(root), root, _common_dir(root), _branch(root), origin
    )


def _parse_explicit_target(cwd: Path, raw: str) -> SyncTarget:
    """Resolve the closed D-3 grammar; unknown target shapes do not degrade."""
    if raw.startswith("repo:"):
        named = Path(raw.removeprefix("repo:")).expanduser().resolve()
        root = Path(
            _required_git(
                ["rev-parse", "--show-toplevel"], named, "repo target is not Git"
            )
        ).resolve()
        if named != root:
            raise SyncGitError("repo: target must name the repository worktree root")
        return _target_for_worktree(_main_worktree(root), "explicit")
    if raw.startswith("worktree:"):
        supplied = Path(raw.removeprefix("worktree:")).expanduser().resolve()
        target = _target_for_worktree(supplied, "explicit")
        if supplied != target.worktree:
            raise SyncGitError(
                "worktree: target must name an exact registered worktree"
            )
        return target
    if raw.startswith("branch:"):
        value = raw.removeprefix("branch:")
        repo_text, separator, branch = value.rpartition("@")
        if not separator or not repo_text or not branch:
            raise SyncGitError("branch target must be branch:<repo-path>@<branch>")
        repo = Path(repo_text).expanduser().resolve()
        root = Path(
            _required_git(
                ["rev-parse", "--show-toplevel"],
                repo,
                "branch target repository is not Git",
            )
        ).resolve()
        matches = [
            path for path, candidate in _worktree_rows(root) if candidate == branch
        ]
        if len(matches) != 1:
            raise SyncGitError(
                f"branch target {branch!r} resolves to {len(matches)} registered worktrees"
            )
        return _target_for_worktree(matches[0], "explicit")
    supplied = Path(raw).expanduser().resolve()
    target = _target_for_worktree(supplied, "explicit")
    if supplied != target.worktree:
        raise SyncGitError("bare target must name an exact registered worktree")
    return target


def _all_worktrees(repo: Path) -> list[SyncTarget]:
    """Expand one repository to its registered, checked-out branches only."""
    root = Path(
        _required_git(
            ["rev-parse", "--show-toplevel"], repo, "all-worktrees target is not Git"
        )
    ).resolve()
    return [_target_for_worktree(path, "explicit") for path, _ in _worktree_rows(root)]


def _all_repositories(directory: Path) -> list[SyncTarget]:
    """Discover repository roots below an explicit directory, never the whole host by default."""
    if not directory.is_dir():
        raise SyncGitError("all-repos target must name an existing directory")
    repositories: list[SyncTarget] = []
    for root, directories, _files in os.walk(directory, followlinks=False):
        if ".git" not in directories:
            continue
        candidate = Path(root)
        try:
            repositories.extend(_all_worktrees(candidate))
        except SyncGitError:
            # A malformed nested .git directory is not a registered repository.
            pass
        directories.remove(".git")
    if not repositories:
        raise SyncGitError("all-repos target found no Git repositories")
    return repositories


def resolve_sync_targets(
    cwd: Path, raw_targets: list[str] | tuple[str, ...] = ()
) -> list[SyncTarget]:
    """Resolve D-3's default pair or explicit worktree-only target set.

    Paths are de-duplicated by both real worktree path and Git common directory,
    so aliases cannot cause the same checked-out branch to receive two actions.
    """
    cwd = cwd.resolve()
    current = _target_for_worktree(cwd, "session")
    targets: list[SyncTarget]
    if raw_targets:
        targets = []
        for raw in raw_targets:
            if raw.startswith("all-worktrees:"):
                targets.extend(
                    _all_worktrees(
                        Path(raw.removeprefix("all-worktrees:")).expanduser()
                    )
                )
            elif raw.startswith("all-repos:"):
                targets.extend(
                    _all_repositories(Path(raw.removeprefix("all-repos:")).expanduser())
                )
            else:
                targets.append(_parse_explicit_target(cwd, raw))
    else:
        main = _target_for_worktree(current.repo, "main")
        targets = [main, current]
    selected: list[SyncTarget] = []
    seen: set[tuple[Path, Path]] = set()
    for target in targets:
        identity = (target.worktree, target.common_dir)
        if identity not in seen:
            seen.add(identity)
            selected.append(target)
    return selected


def status_entries(target: SyncTarget) -> tuple[StatusEntry, ...]:
    """Read the complete porcelain inventory without updating the index."""
    rc, output, error = _git(
        ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
        target.worktree,
        strip=False,
    )
    if rc != 0:
        raise SyncGitError(f"cannot inventory worktree changes: {error}")
    fields = output.split("\0")
    result: list[StatusEntry] = []
    index = 0
    while index < len(fields):
        field = fields[index]
        index += 1
        if not field:
            continue
        if len(field) < 4:
            raise SyncGitError("malformed Git porcelain record")
        code, path = field[:2], field[3:]
        old_path = None
        if "R" in code or "C" in code:
            if index >= len(fields) or not fields[index]:
                raise SyncGitError("malformed rename/copy porcelain record")
            old_path = fields[index]
            index += 1
        result.append(StatusEntry(code, path, old_path))
    return tuple(result)


def _safe_repo_path(path: str) -> bool:
    candidate = PurePosixPath(path)
    return (
        not candidate.is_absolute()
        and ".." not in candidate.parts
        and path not in {"", "."}
    )


def _valid_jsonl(path: Path) -> bool:
    try:
        data = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    try:
        return all(
            isinstance(json.loads(line), dict)
            for line in data.splitlines()
            if line.strip()
        )
    except json.JSONDecodeError:
        return False


def mechanical_jsonl_patterns() -> tuple[str, ...]:
    """Return comma-separated shell patterns whose matching files require JSONL.

    The extension point is empty by default so installations without a generated
    JSONL store do not inherit a format-specific validation lane.
    """
    raw = os.environ.get(MECHANICAL_JSONL_PATTERNS_ENV, "")
    return tuple(pattern.strip() for pattern in raw.split(",") if pattern.strip())


def _scan_path_and_bytes(target: SyncTarget, path: str) -> str | None:
    if not _safe_repo_path(path):
        return f"unsafe path {path!r}"
    if SENSITIVE_PATH.search(path):
        return f"sensitive path {path}"
    source = target.worktree / path
    try:
        data = source.read_bytes()
    except OSError:
        return f"unreadable candidate path {path}"
    if SECRET_BYTES.search(data):
        return f"secret-like bytes in {path}"
    return None


def assess_autonomous_candidate(
    target: SyncTarget, *, allow_deletions: bool = False
) -> CandidateAssessment:
    """Classify every worktree byte for autonomous sync.

    Configured JSONL paths receive an additional mechanical-export validation.
    Other paths are permitted only after the same path/byte and final-tree vetoes;
    no discard or ignore recommendation is an executable action in this engine.
    """
    jsonl_patterns = mechanical_jsonl_patterns()
    try:
        entries = status_entries(target)
    except SyncGitError as exc:
        return CandidateAssessment("REFUSED", str(exc))
    if not entries:
        return CandidateAssessment("NO_CHANGE", "working tree is clean")
    deletions = sorted(entry.path for entry in entries if "D" in entry.code)
    if deletions and not allow_deletions:
        return CandidateAssessment(
            "NEEDS_HUMAN",
            "deletion would discard tracked content from the remote; explicit human "
            f"approval via --allow-deletions is required: {', '.join(deletions)}",
            entries,
        )
    paths: list[str] = []
    for entry in entries:
        if entry.old_path is not None or "U" in entry.code:
            return CandidateAssessment(
                "NEEDS_HUMAN",
                f"rename or conflict requires human review: {entry.path}",
                entries,
            )
        if "D" not in entry.code:
            veto = _scan_path_and_bytes(target, entry.path)
            if veto:
                return CandidateAssessment("REFUSED", veto, entries)
            requires_jsonl = any(
                fnmatch.fnmatchcase(entry.path, pattern) for pattern in jsonl_patterns
            )
            if requires_jsonl and not _valid_jsonl(target.worktree / entry.path):
                return CandidateAssessment(
                    "REFUSED",
                    f"configured mechanical JSONL is invalid: {entry.path}",
                    entries,
                )
        paths.append(entry.path)
    return CandidateAssessment(
        "PUSHED",
        "all changed paths passed byte veto"
        + (
            "; configured JSONL paths remain mechanically validated"
            if jsonl_patterns
            else ""
        ),
        entries,
        tuple(sorted(paths)),
    )


def assess_conflict_regeneration(
    conflicted_paths: tuple[str, ...],
    *,
    recipe_paths: tuple[str, ...],
    regenerated_exactly: bool,
) -> CandidateAssessment:
    """Apply D-2 without ever selecting ``ours`` or ``theirs``.

    The caller may continue only after a canonical recipe has independently
    regenerated every conflict and its final tree has passed the ordinary gate.
    This pure boundary is shared by the real Git integration path and fixtures;
    it makes file/directory, rename, binary, and authored conflicts fail closed.
    """
    if not conflicted_paths:
        return CandidateAssessment(
            "REFUSED", "conflict resolver requires conflicted paths"
        )
    if any(not _safe_repo_path(path) for path in conflicted_paths):
        return CandidateAssessment("REFUSED", "conflict path escapes repository")
    if set(conflicted_paths) != set(recipe_paths):
        return CandidateAssessment(
            "NEEDS_HUMAN",
            "conflict is not wholly owned by one canonical deterministic recipe",
        )
    if not regenerated_exactly:
        return CandidateAssessment(
            "REFUSED", "canonical conflict recipe did not reproduce the final manifest"
        )
    return CandidateAssessment(
        "PUSHED", "canonical recipe may regenerate the conflicted output"
    )


def _head(target: SyncTarget, ref: str = "HEAD") -> str:
    return _required_git(
        ["rev-parse", "--verify", ref], target.worktree, f"cannot resolve {ref}"
    )


def _remote_tracking_ref(target: SyncTarget) -> str:
    return f"refs/remotes/origin/{target.branch}"


def _receive_ref_oid(target: SyncTarget) -> str:
    """Return the receive ref origin holds, or "" when origin has no such branch.

    ``ls-remote --exit-code`` separates the two cases that must never be
    conflated: exit 2 is a definite absence, and any other non-zero is a probe
    failure -- an outage, a revoked credential, a mistyped remote -- which stays
    an error rather than authorizing a first publication.
    """
    rc, output, error = _git(
        ["ls-remote", "--exit-code", "origin", f"refs/heads/{target.branch}"],
        target.worktree,
    )
    if rc == 0 and output:
        return output.split()[0]
    if rc == 2:
        return ""
    raise SyncGitError(f"cannot read the receive ref for {target.branch}: {error}")


def _fetch_and_base(target: SyncTarget) -> tuple[str, str, str]:
    """Return HEAD, the integration base, and the receive ref origin holds.

    Base and receive ref coincide once the branch has been published.  Before
    that the ref is absent and there is no remote history to integrate, so the
    base is HEAD itself while the lease value stays "".  Collapsing the two
    would make the compare-and-swap below read a missing ref as a lost race.
    """
    rc, _, error = _git(["remote", "get-url", "origin"], target.worktree)
    if rc != 0:
        raise SyncGitError(f"origin is required for synchronization: {error}")
    if not _receive_ref_oid(target):
        head = _head(target)
        return head, head, ""
    rc, _, error = _git(
        ["fetch", "--no-tags", "origin", target.branch], target.worktree
    )
    if rc != 0:
        raise SyncGitError(f"fetch failed: {error}")
    base = _head(target, _remote_tracking_ref(target))
    return _head(target), base, base


def _trunk_branch(target: SyncTarget) -> str:
    """Resolve the checked-out trunk rather than assuming a feature upstream."""
    return _branch(target.repo)


def _fetch_worktree_and_trunk(target: SyncTarget) -> tuple[str, str, str]:
    """Fetch current trunk, and the receive ref only when origin holds one.

    A branch that was never pushed has no remote ref by construction, which is
    the normal state of a fresh session slot -- so fetching it by name failed and
    refused exactly the worktrees most in need of reconciling.  Absence means
    "nothing to reconcile remotely", which every caller already models as "".
    """
    trunk = _trunk_branch(target)
    refs = [target.branch, trunk] if _receive_ref_oid(target) else [trunk]
    rc, _, error = _git(["fetch", "--no-tags", "origin", *refs], target.worktree)
    if rc != 0:
        raise SyncGitError(f"fetch failed: {error}")
    return (
        _head(target),
        _remote_oid(target),
        _head(target, f"refs/remotes/origin/{trunk}"),
    )


def _is_ancestor(target: SyncTarget, older: str, newer: str) -> bool:
    rc, _, _ = _git(["merge-base", "--is-ancestor", older, newer], target.worktree)
    return rc == 0


@contextmanager
def _lease(target: SyncTarget) -> Iterator[bool]:
    """A per-common-dir non-blocking lease; all failures are refusal evidence."""
    lock_path = target.common_dir / "sync-git.lock"
    try:
        with lock_path.open("a+", encoding="utf-8") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                yield False
                return
            try:
                yield True
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        yield False


def _anchor(
    target: SyncTarget, head: str, run_id: str
) -> tuple[str | None, str | None]:
    ref = f"refs/backup/sync-git/{run_id}/{hashlib.sha256(str(target.worktree).encode()).hexdigest()[:12]}/committed"
    rc, _, error = _git(["update-ref", ref, head, "0" * 40], target.worktree)
    if rc != 0:
        return None, error
    rc, readback, error = _git(["rev-parse", "--verify", ref], target.worktree)
    if rc != 0 or readback != head:
        return None, error or "anchor readback differed"
    return ref, None


def _tree_from_manifest(
    target: SyncTarget, paths: tuple[str, ...], base_tree: str
) -> tuple[str | None, str]:
    """Build only from screened bytes in a private index, never the caller index."""
    with tempfile.TemporaryDirectory(prefix="sync-git-index-") as directory:
        index = Path(directory) / "index"
        env = {"GIT_INDEX_FILE": str(index), "GIT_OPTIONAL_LOCKS": "0"}
        rc, _, error = core._git(
            ["read-tree", base_tree], target.worktree, env_overrides=env
        )
        if rc != 0:
            return None, error
        for path in paths:
            rc, _, error = core._git(
                ["update-index", "--force-remove", "--", path],
                target.worktree,
                env_overrides=env,
            )
            if rc != 0:
                return None, error
            source = target.worktree / path
            if not source.exists() and not source.is_symlink():
                continue
            try:
                data = source.read_bytes()
                mode = "100755" if source.stat().st_mode & 0o111 else "100644"
            except OSError as exc:
                return None, str(exc)
            process = subprocess.run(
                ["git", "hash-object", "-w", "--stdin"],
                cwd=str(target.worktree),
                input=data,
                capture_output=True,
                check=False,
                env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
            )
            if process.returncode != 0:
                return None, process.stderr.decode("utf-8", "replace").strip()
            blob = process.stdout.decode("ascii", "replace").strip()
            rc, _, error = core._git(
                ["update-index", "--add", "--cacheinfo", f"{mode},{blob},{path}"],
                target.worktree,
                env_overrides=env,
            )
            if rc != 0:
                return None, error
        rc, tree, error = core._git(["write-tree"], target.worktree, env_overrides=env)
        if rc != 0:
            return None, error
    return tree, ""


def _scan_tree(target: SyncTarget, tree: str, parent_tree: str) -> str | None:
    """Veto only bytes introduced by this candidate, never safe legacy files."""
    rc, listing, error = _git(
        ["diff", "--name-only", "-z", parent_tree, tree], target.worktree, strip=False
    )
    if rc != 0:
        return error or "cannot enumerate candidate tree changes"
    for path in listing.split("\0"):
        if not path:
            continue
        if SENSITIVE_PATH.search(path):
            return f"sensitive path {path} in final tree"
        rc, blob, error = _git(["rev-parse", f"{tree}:{path}"], target.worktree)
        if rc != 0:  # A deletion has no final blob to scan.
            continue
        rc, content, error = _git(["cat-file", "-p", blob], target.worktree)
        if rc != 0:
            return error or f"cannot read final blob {path}"
        if SECRET_BYTES.search(content.encode("utf-8", "surrogateescape")):
            return f"secret-like bytes in final tree path {path}"
    return None


def _run_commit_msg_gate(target: SyncTarget, message: str) -> str | None:
    if not message.endswith("Delegated-to: none"):
        return "commit message lacks terminal Delegated-to: none trailer"
    hook = target.common_dir / "hooks" / "commit-msg"
    if not hook.is_file() or not os.access(hook, os.X_OK):
        return None
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
        handle.write(message + "\n")
        message_path = Path(handle.name)
    try:
        result = subprocess.run(
            [str(hook), str(message_path)],
            cwd=str(target.worktree),
            capture_output=True,
            text=True,
            check=False,
            env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
        )
        if result.returncode:
            return "canonical commit-msg gate refused the generated message"
    except OSError:
        return "canonical commit-msg gate is unavailable"
    finally:
        message_path.unlink(missing_ok=True)
    return None


def _commit_tree(
    target: SyncTarget, tree: str, parents: tuple[str, ...], message: str
) -> tuple[str | None, str]:
    parent_args = [part for parent in parents for part in ("-p", parent)]
    rc, commit, error = _git(
        ["commit-tree", tree, *parent_args, "-m", message], target.worktree
    )
    return (commit if rc == 0 else None), error


def _clean_merge_tree(
    target: SyncTarget, local: str, base: str
) -> tuple[str | None, str]:
    """Ask Git for its clean three-way result; never provide a side strategy."""
    rc, output, error = _git(
        ["merge-tree", "--write-tree", local, base], target.worktree
    )
    tree = output.splitlines()[0] if output else ""
    if rc != 0 or not re.fullmatch(r"[0-9a-f]{40,64}", tree):
        return None, error or "Git reported an integration conflict"
    return tree, ""


def _last_change_is_winning_deletion(
    target: SyncTarget, winning: str, path: str
) -> bool:
    """Prove the winner deliberately retired ``path`` in committed history.

    This is the same non-merge deletion evidence that ``sync_deletion_guard``
    requires before a trunk-originated loss can be accepted.  Looking at the
    final change (rather than merely any historical deletion) prevents an old
    delete/re-add sequence from authorizing a new loss.
    """
    if not _safe_repo_path(path):
        return False
    rc, commit, _ = _git(
        ["log", "-1", "--format=%H", winning, "--", path], target.worktree
    )
    if rc != 0 or not re.fullmatch(r"[0-9a-f]{40,64}", commit):
        return False
    rc, deleted, _ = _git(
        [
            "diff-tree",
            "--no-commit-id",
            "--no-renames",
            "--diff-filter=D",
            "--name-only",
            "-r",
            commit,
            "--",
            path,
        ],
        target.worktree,
    )
    return rc == 0 and deleted == path


def _safe_tree_supersession(
    target: SyncTarget, losing: str, winning: str
) -> tuple[str | None, tuple[str, ...], str]:
    """Prove that replacing ``losing``'s final tree with ``winning`` is safe.

    Equal trees are different convergence histories, not a conflict.  Otherwise
    the only permitted loss is a path the winning, fetched trunk already retired
    in a non-merge commit.  Additions retained by the winner are mechanical.  A
    same-path content edit, rename, or unproved deletion remains human-owned.
    """
    rc, output, error = _git(
        ["diff", "--name-status", "--no-renames", "-z", losing, winning],
        target.worktree,
        strip=False,
    )
    if rc != 0:
        return None, (), error or "cannot compare final integration trees"
    fields = [field for field in output.split("\0") if field]
    if not fields:
        return _head(target, f"{winning}^{{tree}}"), (), "identical final trees"
    if len(fields) % 2:
        return None, (), "malformed final-tree comparison"
    accepted_deletions: list[str] = []
    for status, path in zip(fields[::2], fields[1::2], strict=True):
        if not _safe_repo_path(path):
            return None, (), f"unsafe integration path {path!r}"
        if status == "A":  # Present only on the winning side; preserve it.
            continue
        if status == "D" and _last_change_is_winning_deletion(target, winning, path):
            accepted_deletions.append(path)
            continue
        return None, (), f"unproved final-tree difference at {path} ({status})"
    return (
        _head(target, f"{winning}^{{tree}}"),
        tuple(sorted(accepted_deletions)),
        "winning tree retains all additions and proves every accepted deletion",
    )


def _integration_message(
    subject: str, *, resolution: str | None = None, deletions: tuple[str, ...] = ()
) -> str:
    lines = [subject]
    if resolution:
        lines += ["", f"Sync-integration-resolution: {resolution}"]
    lines += [f"Sync-deletion-accepted: {path}" for path in deletions]
    lines += ["", "Delegated-to: none"]
    return "\n".join(lines)


def _remote_oid(target: SyncTarget) -> str:
    rc, output, _ = _git(
        ["ls-remote", "--exit-code", "origin", f"refs/heads/{target.branch}"],
        target.worktree,
    )
    return output.split()[0] if rc == 0 and output else ""


def _reconcile_local(target: SyncTarget, old: str, new: str) -> str | None:
    rc, _, error = _git(
        ["update-ref", f"refs/heads/{target.branch}", new, old], target.worktree
    )
    if rc != 0:
        return error or "branch compare-and-swap failed"
    rc, _, error = _git(["read-tree", "--reset", "-u", new], target.worktree)
    if rc != 0:
        return error or "checkout reconciliation failed"
    try:
        if _head(target) != new:
            return "HEAD readback differs after reconciliation"
    except SyncGitError as exc:
        return str(exc)
    return None


def _push_existing_commits(
    target: SyncTarget, local: str, base: str, *, dry_run: bool
) -> SyncAction:
    """Publish an ahead branch through anchor, final veto, and receive readback."""
    if dry_run:
        return SyncAction("PUSHED", "would non-force-push unique local commits", target)
    with _lease(target) as held:
        if not held:
            return SyncAction("REFUSED", "common-dir sync lease is unavailable", target)
        anchor, failure = _anchor(target, local, uuid4().hex)
        if failure:
            return SyncAction("ANCHOR_FAILED", failure, target, head_before=local)
        if _remote_oid(target) != base:
            return SyncAction(
                "STALE",
                "receive ref changed before publication",
                target,
                anchor_ref=anchor,
            )
        veto = _scan_tree(
            target,
            _head(target, "HEAD^{tree}"),
            _head(target, f"{base}^{{tree}}"),
        )
        if veto:
            return SyncAction("REFUSED", veto, target, anchor_ref=anchor)
        rc, _, error = _git(
            ["push", "origin", f"{local}:refs/heads/{target.branch}"], target.worktree
        )
        if rc != 0:
            return SyncAction(
                "STALE", f"non-forcing push refused: {error}", target, anchor_ref=anchor
            )
        if _remote_oid(target) != local:
            return SyncAction(
                "READBACK_FAILED",
                "receive ref differs after push",
                target,
                anchor_ref=anchor,
            )
        return SyncAction(
            "PUSHED",
            "unique local commits non-force-pushed and read back",
            target,
            head_before=local,
            head_after=local,
            remote_before=base,
            anchor_ref=anchor,
        )


def _merge_and_push_clean(
    target: SyncTarget, local: str, base: str, *, dry_run: bool
) -> SyncAction:
    """Create only Git's clean merge result, then publish it with normal proofs."""
    tree, error = _clean_merge_tree(target, local, base)
    resolution: str | None = None
    accepted_deletions: tuple[str, ...] = ()
    if tree is None:
        tree, accepted_deletions, proof = _safe_tree_supersession(target, local, base)
        if tree is None:
            return SyncAction(
                "NEEDS_HUMAN",
                f"Git integration conflict requires human review: {error}; {proof}",
                target,
                head_before=local,
                remote_before=base,
            )
        resolution = proof
    if dry_run:
        return SyncAction(
            "PUSHED", "would cleanly merge and non-force-push branch divergence", target
        )
    with _lease(target) as held:
        if not held:
            return SyncAction("REFUSED", "common-dir sync lease is unavailable", target)
        if _head(target) != local or _remote_oid(target) != base:
            return SyncAction(
                "STALE", "branch or receive ref changed before anchor", target
            )
        anchor, failure = _anchor(target, local, uuid4().hex)
        if failure:
            return SyncAction("ANCHOR_FAILED", failure, target, head_before=local)
        veto = _scan_tree(target, tree, _head(target, f"{local}^{{tree}}"))
        if veto:
            return SyncAction("REFUSED", veto, target, anchor_ref=anchor)
        message = _integration_message(
            "chore(sync-git): merge remote branch",
            resolution=resolution,
            deletions=accepted_deletions,
        )
        gate_error = _run_commit_msg_gate(target, message)
        if gate_error:
            return SyncAction("REFUSED", gate_error, target, anchor_ref=anchor)
        commit, error = _commit_tree(target, tree, (local, base), message)
        if commit is None:
            return SyncAction(
                "ACTION_FAILED",
                f"commit construction failed: {error}",
                target,
                anchor_ref=anchor,
            )
        if _remote_oid(target) != base:
            return SyncAction(
                "STALE",
                "receive ref changed before publication",
                target,
                anchor_ref=anchor,
            )
        reconcile_error = _reconcile_local(target, local, commit)
        if reconcile_error:
            return SyncAction(
                "READBACK_FAILED", reconcile_error, target, anchor_ref=anchor
            )
        rc, _, error = _git(
            ["push", "origin", f"{commit}:refs/heads/{target.branch}"], target.worktree
        )
        if rc != 0:
            return SyncAction(
                "STALE", f"non-forcing push refused: {error}", target, anchor_ref=anchor
            )
        if _remote_oid(target) != commit:
            return SyncAction(
                "READBACK_FAILED",
                "receive ref differs after push",
                target,
                anchor_ref=anchor,
            )
        return SyncAction(
            "PUSHED",
            "remote and local commits integrated and read back"
            if resolution
            else "remote and local commits cleanly merged and read back",
            target,
            head_before=local,
            head_after=commit,
            remote_before=base,
            anchor_ref=anchor,
        )


def _sync_clean_worktree_to_trunk(target: SyncTarget, *, dry_run: bool) -> SyncAction:
    """Synchronize a clean linked worktree with trunk before its stale receive ref.

    A feature branch's remote is publication state, not its integration base.
    Resolving against ``origin/<feature>`` first lets an abandoned pre-squash
    ref manufacture a conflict that trunk has already resolved.  We therefore
    establish the local worktree at current trunk (or a proven clean merge) and
    only then update its receive ref, with an exact force-with-lease when the
    remote history is obsolete but its final content is already accounted for.
    """
    try:
        local, remote, trunk = _fetch_worktree_and_trunk(target)
    except SyncGitError as exc:
        return SyncAction("REFUSED", str(exc), target)
    # A separately diverged receive ref cannot be reconciled after changing the
    # checkout: prove or surface it before any local ref/worktree mutation.  The
    # stale-ref case we can repair has ``remote == local`` and is handled below.
    if remote and remote != local and not _is_ancestor(target, remote, local):
        return SyncAction(
            "NEEDS_HUMAN",
            "own remote branch diverged from the local worktree before trunk "
            "integration",
            target,
            head_before=local,
            remote_before=remote,
        )
    if local == trunk:
        tree = _head(target, f"{trunk}^{{tree}}")
        accepted_deletions: tuple[str, ...] = ()
        resolution: str | None = None
    elif _is_ancestor(target, local, trunk):
        tree = _head(target, f"{trunk}^{{tree}}")
        accepted_deletions = ()
        resolution = None
    else:
        tree, merge_error = _clean_merge_tree(target, local, trunk)
        accepted_deletions = ()
        resolution = None
        if tree is None:
            tree, accepted_deletions, proof = _safe_tree_supersession(
                target, local, trunk
            )
            if tree is None:
                return SyncAction(
                    "NEEDS_HUMAN",
                    "Git integration conflict requires human review: "
                    f"{merge_error}; {proof}",
                    target,
                    head_before=local,
                    remote_before=remote,
                )
            resolution = proof
    trunk_tree = _head(target, f"{trunk}^{{tree}}")
    direct_trunk = tree == trunk_tree and not accepted_deletions
    if dry_run:
        return SyncAction(
            "SYNCED" if remote == trunk and direct_trunk else "PUSHED",
            "would synchronize clean worktree with current trunk before its own remote ref",
            target,
            head_before=local,
            head_after=trunk if direct_trunk else None,
            remote_before=remote,
            evidence={"trunk": trunk, "resolution": resolution or "clean merge"},
        )
    with _lease(target) as held:
        if not held:
            return SyncAction("REFUSED", "common-dir sync lease is unavailable", target)
        if _head(target) != local or _remote_oid(target) != remote:
            return SyncAction(
                "STALE", "branch or receive ref changed before anchor", target
            )
        anchor, failure = _anchor(target, local, uuid4().hex)
        if failure:
            return SyncAction("ANCHOR_FAILED", failure, target, head_before=local)
        if direct_trunk:
            new = trunk
        else:
            veto = _scan_tree(target, tree, _head(target, f"{local}^{{tree}}"))
            if veto:
                return SyncAction("REFUSED", veto, target, anchor_ref=anchor)
            message = _integration_message(
                "chore(sync-git): synchronize worktree with trunk",
                resolution=resolution,
                deletions=accepted_deletions,
            )
            gate_error = _run_commit_msg_gate(target, message)
            if gate_error:
                return SyncAction("REFUSED", gate_error, target, anchor_ref=anchor)
            new, error = _commit_tree(target, tree, (local, trunk), message)
            if new is None:
                return SyncAction(
                    "ACTION_FAILED",
                    f"commit construction failed: {error}",
                    target,
                    anchor_ref=anchor,
                )
        reconcile_error = _reconcile_local(target, local, new)
        if reconcile_error:
            return SyncAction(
                "READBACK_FAILED", reconcile_error, target, anchor_ref=anchor
            )
        if remote == new:
            return SyncAction(
                "SYNCED",
                "clean worktree synchronized with trunk; own remote ref already matched",
                target,
                head_before=local,
                head_after=new,
                remote_before=remote,
                anchor_ref=anchor,
                evidence={"trunk": trunk, "resolution": resolution or "clean merge"},
            )
        if not remote or _is_ancestor(target, remote, new):
            push_args = ["push", "origin", f"{new}:refs/heads/{target.branch}"]
        else:
            # The only force route is a tree-equivalent or already-proved trunk
            # supersession.  The exact observed ref is the lease, never a blind
            # force push.
            remote_tree, remote_deletions, proof = _safe_tree_supersession(
                target, remote, new
            )
            if remote_tree != _head(target, f"{new}^{{tree}}"):
                return SyncAction(
                    "NEEDS_HUMAN",
                    f"own remote branch cannot safely converge on trunk: {proof}",
                    target,
                    head_before=local,
                    head_after=new,
                    remote_before=remote,
                    anchor_ref=anchor,
                )
            if remote_deletions:
                return SyncAction(
                    "NEEDS_HUMAN",
                    "own remote branch needs a deletion-acceptance commit before "
                    f"force-with-lease: {', '.join(remote_deletions)}",
                    target,
                    head_before=local,
                    head_after=new,
                    remote_before=remote,
                    anchor_ref=anchor,
                )
            push_args = [
                "push",
                f"--force-with-lease=refs/heads/{target.branch}:{remote}",
                "origin",
                f"{new}:refs/heads/{target.branch}",
            ]
        rc, _, error = _git(push_args, target.worktree)
        if rc != 0:
            return SyncAction(
                "STALE", f"publication refused: {error}", target, anchor_ref=anchor
            )
        if _remote_oid(target) != new:
            return SyncAction(
                "READBACK_FAILED",
                "receive ref differs after push",
                target,
                anchor_ref=anchor,
            )
        return SyncAction(
            "PUSHED",
            "clean worktree synchronized with current trunk and own remote ref read back",
            target,
            head_before=local,
            head_after=new,
            remote_before=remote,
            anchor_ref=anchor,
            evidence={"trunk": trunk, "resolution": resolution or "clean merge"},
        )


def sync_target(
    target: SyncTarget, *, dry_run: bool = False, allow_deletions: bool = False
) -> SyncAction:
    """Synchronize all safe local changes and local/remote branch divergence."""
    assessment = assess_autonomous_candidate(target, allow_deletions=allow_deletions)
    # The default session worktree is integrated with the current trunk, not an
    # arbitrarily stale remote feature ref.  Dirty candidates retain the normal
    # byte-vetting path below; once clean, a subsequent invocation takes this
    # trunk-first route.
    if assessment.state == "NO_CHANGE" and target.branch != _trunk_branch(target):
        return _sync_clean_worktree_to_trunk(target, dry_run=dry_run)
    try:
        local, base, receive = _fetch_and_base(target)
    except SyncGitError as exc:
        return SyncAction(
            "REFUSED", str(exc), target, changed_paths=assessment.authorized_paths
        )

    if local == base and assessment.state == "NO_CHANGE":
        return SyncAction(
            "NO_CHANGE",
            "already equal to fetched integration base",
            target,
            head_before=local,
            head_after=local,
            remote_before=receive,
        )
    if assessment.state not in {"NO_CHANGE", "PUSHED"}:
        return SyncAction(
            assessment.state,
            assessment.reason,
            target,
            head_before=local,
            remote_before=receive,
            changed_paths=tuple(entry.path for entry in assessment.entries),
        )
    if assessment.state == "NO_CHANGE":
        if _is_ancestor(target, local, base):
            if dry_run:
                return SyncAction(
                    "SYNCED",
                    "would fast-forward clean worktree",
                    target,
                    head_before=local,
                    head_after=base,
                    remote_before=receive,
                )
            with _lease(target) as held:
                if not held:
                    return SyncAction(
                        "REFUSED",
                        "common-dir sync lease is unavailable",
                        target,
                        head_before=local,
                        remote_before=receive,
                    )
                anchor, failure = _anchor(target, local, uuid4().hex)
                if failure:
                    return SyncAction(
                        "ANCHOR_FAILED",
                        failure,
                        target,
                        head_before=local,
                        remote_before=receive,
                    )
                rc, _, error = _git(["merge", "--ff-only", base], target.worktree)
                if rc != 0:
                    return SyncAction(
                        "STALE",
                        f"fast-forward refused: {error}",
                        target,
                        head_before=local,
                        remote_before=receive,
                        anchor_ref=anchor,
                    )
                after = _head(target)
                if after != base:
                    return SyncAction(
                        "READBACK_FAILED",
                        "fast-forward readback differs",
                        target,
                        head_before=local,
                        head_after=after,
                        remote_before=receive,
                        anchor_ref=anchor,
                    )
                return SyncAction(
                    "SYNCED",
                    "clean worktree fast-forwarded and read back",
                    target,
                    head_before=local,
                    head_after=after,
                    remote_before=receive,
                    anchor_ref=anchor,
                )
        if _is_ancestor(target, base, local):
            return _push_existing_commits(target, local, base, dry_run=dry_run)
        return _merge_and_push_clean(target, local, base, dry_run=dry_run)

    integration_tree = local
    parents = (local,)
    # For a behind dirty worktree, Git may construct a clean three-way base in
    # isolation.  If that fails, only the independently proved final-tree
    # supersession below can continue.
    accepted_deletions: tuple[str, ...] = ()
    resolution: str | None = None
    if local != base and _is_ancestor(target, local, base):
        integration_tree, merge_error = _clean_merge_tree(target, local, base)
        if integration_tree is None:
            integration_tree, accepted_deletions, proof = _safe_tree_supersession(
                target, local, base
            )
            if integration_tree is None:
                return SyncAction(
                    "NEEDS_HUMAN",
                    "Git integration conflict requires canonical regeneration: "
                    f"{merge_error}; {proof}",
                    target,
                    head_before=local,
                    remote_before=receive,
                    changed_paths=assessment.authorized_paths,
                )
            resolution = proof
        parents = (local, base)
    elif local != base:
        return SyncAction(
            "NEEDS_HUMAN",
            "dirty candidate needs a PR or an ambiguous integration decision",
            target,
            head_before=local,
            remote_before=receive,
            changed_paths=assessment.authorized_paths,
        )
    if dry_run:
        return SyncAction(
            "PUSHED",
            "would commit all byte-vetted changes and non-force-push",
            target,
            head_before=local,
            remote_before=receive,
            changed_paths=assessment.authorized_paths,
        )
    with _lease(target) as held:
        if not held:
            return SyncAction(
                "REFUSED",
                "common-dir sync lease is unavailable",
                target,
                head_before=local,
                remote_before=receive,
            )
        # Reassessment under the lease prevents an old report from authorizing a
        # later edit.  It also keeps the first byte veto before tree construction.
        current = assess_autonomous_candidate(target, allow_deletions=allow_deletions)
        if (
            current.state != "PUSHED"
            or current.authorized_paths != assessment.authorized_paths
        ):
            return SyncAction(
                "STALE",
                "candidate changed after assessment",
                target,
                head_before=local,
                remote_before=receive,
            )
        current_head = _head(target)
        current_remote = _remote_oid(target)
        if current_head != local or current_remote != receive:
            return SyncAction(
                "STALE",
                "branch or receive ref changed before anchor",
                target,
                head_before=local,
                remote_before=receive,
            )
        anchor, failure = _anchor(target, local, uuid4().hex)
        if failure:
            return SyncAction(
                "ANCHOR_FAILED",
                failure,
                target,
                head_before=local,
                remote_before=receive,
            )
        tree, error = _tree_from_manifest(
            target, assessment.authorized_paths, integration_tree
        )
        if tree is None:
            return SyncAction(
                "ACTION_FAILED",
                f"isolated tree construction failed: {error}",
                target,
                head_before=local,
                remote_before=receive,
                anchor_ref=anchor,
            )
        veto = _scan_tree(target, tree, _head(target, f"{integration_tree}^{{tree}}"))
        if veto:
            return SyncAction(
                "REFUSED",
                veto,
                target,
                head_before=local,
                remote_before=receive,
                anchor_ref=anchor,
            )
        message = _integration_message(
            "chore(sync-git): synchronize working tree",
            resolution=resolution,
            deletions=accepted_deletions,
        )
        gate_error = _run_commit_msg_gate(target, message)
        if gate_error:
            return SyncAction(
                "REFUSED",
                gate_error,
                target,
                head_before=local,
                remote_before=receive,
                anchor_ref=anchor,
            )
        commit, error = _commit_tree(target, tree, parents, message)
        if commit is None:
            return SyncAction(
                "ACTION_FAILED",
                f"commit construction failed: {error}",
                target,
                head_before=local,
                remote_before=receive,
                anchor_ref=anchor,
            )
        # The remote has an independent CAS slot.  Do not substitute the local
        # tracking ref, a worktree upstream spelling, or the integration base for
        # this receive ref: on a never-pushed branch the base is HEAD while the
        # ref is legitimately absent.
        if _remote_oid(target) != receive:
            return SyncAction(
                "STALE",
                "receive ref changed before publication",
                target,
                head_before=local,
                remote_before=receive,
                anchor_ref=anchor,
            )
        reconcile_error = _reconcile_local(target, local, commit)
        if reconcile_error:
            return SyncAction(
                "READBACK_FAILED",
                reconcile_error,
                target,
                head_before=local,
                head_after=commit,
                remote_before=receive,
                anchor_ref=anchor,
            )
        rc, _, error = _git(
            ["push", "origin", f"{commit}:refs/heads/{target.branch}"], target.worktree
        )
        if rc != 0:
            return SyncAction(
                "STALE",
                f"non-forcing push refused: {error}",
                target,
                head_before=local,
                head_after=commit,
                remote_before=receive,
                anchor_ref=anchor,
            )
        if _remote_oid(target) != commit:
            return SyncAction(
                "READBACK_FAILED",
                "receive ref differs after push",
                target,
                head_before=local,
                head_after=commit,
                remote_before=receive,
                anchor_ref=anchor,
            )
        return SyncAction(
            "PUSHED",
            "screened candidate committed, published, and read back",
            target,
            local,
            commit,
            base,
            anchor,
            assessment.authorized_paths,
            {"candidate_tree": tree, "commit": commit},
        )


def resync_merged_worktree(
    target: SyncTarget, acceptance: PullRequestAcceptance, *, dry_run: bool = False
) -> SyncAction:
    """Detach a feature worktree at a provider-accepted, read-back PR result.

    This is intentionally post-action only: it cannot create or merge a PR.
    The provider transaction owner supplies the complete acceptance tuple; this
    function preserves the feature branch before the local detached handoff.
    """
    if not acceptance.pr_id or not acceptance.transaction_key:
        return SyncAction(
            "REFUSED", "PR acceptance lacks identity-bound transaction data", target
        )
    if not re.fullmatch(r"[0-9a-f]{40,64}", acceptance.head_oid) or not re.fullmatch(
        r"[0-9a-f]{40,64}", acceptance.result_oid
    ):
        return SyncAction("REFUSED", "PR acceptance OIDs are malformed", target)
    try:
        current = _head(target)
        entries = status_entries(target)
    except SyncGitError as exc:
        return SyncAction("REFUSED", str(exc), target)
    if entries:
        return SyncAction(
            "STALE",
            "worktree is not clean for post-merge handoff",
            target,
            head_before=current,
        )
    if current != acceptance.head_oid:
        return SyncAction(
            "STALE",
            "feature branch changed after PR acceptance",
            target,
            head_before=current,
        )
    if dry_run:
        return SyncAction(
            "SYNCED",
            "would detach feature worktree at accepted PR result",
            target,
            head_before=current,
            head_after=acceptance.result_oid,
        )
    with _lease(target) as held:
        if not held:
            return SyncAction(
                "REFUSED",
                "common-dir sync lease is unavailable",
                target,
                head_before=current,
            )
        rc, _, error = _git(
            ["fetch", "--no-tags", "origin", acceptance.base_branch], target.worktree
        )
        if rc != 0:
            return SyncAction(
                "READBACK_FAILED",
                f"post-merge fetch failed: {error}",
                target,
                head_before=current,
            )
        try:
            observed_result = _head(
                target, f"refs/remotes/origin/{acceptance.base_branch}"
            )
        except SyncGitError as exc:
            return SyncAction("READBACK_FAILED", str(exc), target, head_before=current)
        if observed_result != acceptance.result_oid:
            return SyncAction(
                "STALE",
                "provider result differs from fetched base",
                target,
                head_before=current,
            )
        anchor, failure = _anchor(target, current, uuid4().hex)
        if failure:
            return SyncAction("ANCHOR_FAILED", failure, target, head_before=current)
        if _head(target) != current:
            return SyncAction(
                "STALE",
                "feature branch changed before detach",
                target,
                head_before=current,
                anchor_ref=anchor,
            )
        rc, _, error = _git(
            ["checkout", "--detach", acceptance.result_oid], target.worktree
        )
        if rc != 0:
            return SyncAction(
                "ACTION_FAILED",
                f"detach failed: {error}",
                target,
                head_before=current,
                anchor_ref=anchor,
            )
        try:
            detached = _head(target)
            feature = _head(target, f"refs/heads/{target.branch}")
        except SyncGitError as exc:
            return SyncAction(
                "READBACK_FAILED",
                str(exc),
                target,
                head_before=current,
                anchor_ref=anchor,
            )
        if detached != acceptance.result_oid or feature != acceptance.head_oid:
            return SyncAction(
                "READBACK_FAILED",
                "detached result or preserved feature ref differs",
                target,
                head_before=current,
                head_after=detached,
                anchor_ref=anchor,
            )
        return SyncAction(
            "SYNCED",
            "accepted PR result read back and feature worktree detached",
            target,
            head_before=current,
            head_after=detached,
            anchor_ref=anchor,
            evidence={
                "pr_id": acceptance.pr_id,
                "transaction_key": acceptance.transaction_key,
            },
        )


def action_json(action: SyncAction) -> dict[str, object]:
    """Render only explicit fields; no command output or environment is retained."""
    data = asdict(action)
    data["target"] = {
        "repo": str(action.target.repo),
        "worktree": str(action.target.worktree),
        "common_dir": str(action.target.common_dir),
        "branch": action.target.branch,
        "origin": action.target.origin,
    }
    return data
