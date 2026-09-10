#!/usr/bin/env python3
"""Portable worktree resolution and non-process liveness probes for sync-git."""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


DEFAULT_RECENCY_SECONDS = 4 * 60 * 60
RECENCY_ENV_VAR = "AGENT_WORKTREE_RECENCY_SECONDS"
TRANSCRIPT_ROOT_ENV_VAR = "CLAUDE_PROJECTS_DIR"
META_SUFFIX = ".meta.json"
TRANSCRIPT_SUFFIX = ".jsonl"
WORKTREE_PATH_KEYS = ("worktreePath", "inheritedWorktreePath")
MAX_TAIL_BYTES = 4 * 1024 * 1024
GIT_TIMEOUT_SECONDS = 30


class GitUnavailable(RuntimeError):
    """Git could not be run, so the requested repository state is unknown."""


@dataclass(frozen=True)
class Signal:
    """One keep-biased liveness observation about a worktree path."""

    name: str
    live: bool
    reason: str


def _git(args: list[str], cwd: Path) -> tuple[int, str, str]:
    """Run Git with stable porcelain output and return stripped result fields."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "LC_ALL": "C", "LANG": "C"},
        )
    except OSError as exc:
        raise GitUnavailable(str(exc)) from exc
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def main_worktree_root(cwd: Path) -> Path:
    """Return the main checkout root when ``cwd`` is in any registered worktree."""
    rc, out, _ = _git(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd)
    if rc == 0 and out.endswith("/.git"):
        return Path(out[: -len("/.git")])
    rc, out, _ = _git(["rev-parse", "--show-toplevel"], cwd)
    if rc != 0:
        raise GitUnavailable(f"{cwd} is not inside a git repository")
    return Path(out)


def resolve_pushed_target(repo: Path) -> str | None:
    """Resolve the remote-tracking default branch without assuming its name."""
    rc, out, _ = _git(["symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"], repo)
    if rc == 0 and out:
        return out

    rc, configured, _ = _git(["config", "--get", "init.defaultBranch"], repo)
    candidates = [configured] if rc == 0 and configured else []
    candidates += ["main", "master"]
    for name in candidates:
        ref = f"refs/remotes/origin/{name}"
        rc, _, _ = _git(["rev-parse", "--verify", "--quiet", ref], repo)
        if rc == 0:
            return ref
    return None


def recency_seconds() -> float:
    """Return the positive configured transcript recency window or its safe default."""
    raw = os.environ.get(RECENCY_ENV_VAR)
    if raw is None:
        return float(DEFAULT_RECENCY_SECONDS)
    try:
        value = float(raw)
    except ValueError:
        return float(DEFAULT_RECENCY_SECONDS)
    if value <= 0:
        return float(DEFAULT_RECENCY_SECONDS)
    return value


def transcript_root() -> Path:
    """Return the configured Claude Code transcript directory."""
    override = os.environ.get(TRANSCRIPT_ROOT_ENV_VAR)
    if override:
        return Path(override)
    return Path.home() / ".claude" / "projects"


def _resolve(path: Path) -> Path | None:
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        return None


def _last_record_age(jsonl: Path, now: float) -> float | None:
    """Read only timestamps near the transcript tail and return the newest age."""
    try:
        size = jsonl.stat().st_size
    except OSError:
        return None
    if size == 0:
        return None
    chunk = 65536
    try:
        with jsonl.open("rb") as handle:
            while chunk <= MAX_TAIL_BYTES:
                handle.seek(max(0, size - chunk))
                lines = [line for line in handle.read().split(b"\n") if line.strip()]
                if chunk < size and len(lines) > 1:
                    lines = lines[1:]
                for line in reversed(lines):
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(record, dict):
                        continue
                    raw = record.get("timestamp")
                    if not isinstance(raw, str):
                        continue
                    try:
                        stamp = datetime.fromisoformat(raw)
                    except ValueError:
                        continue
                    if stamp.tzinfo is None:
                        stamp = stamp.replace(tzinfo=UTC)
                    reference = datetime.fromtimestamp(now, tz=UTC)
                    return max(0.0, (reference - stamp).total_seconds())
                if chunk >= size:
                    break
                chunk *= 8
    except OSError:
        return None
    return None


def _subdirectories(path: Path) -> list[Path]:
    """Return readable immediate subdirectories, propagating directory failures."""
    with os.scandir(path) as entries:
        found = []
        for entry in entries:
            try:
                if entry.is_dir():
                    found.append(Path(entry.path))
            except OSError:
                continue
    return sorted(found)


def _walk_transcript_metas(root: Path):
    """Yield sub-agent metadata files without hiding enumeration failures."""
    for slug in _subdirectories(root):
        for session in _subdirectories(slug):
            subagents = session / "subagents"
            if not subagents.is_dir():
                continue
            with os.scandir(subagents) as entries:
                for entry in sorted(entries, key=lambda item: item.name):
                    if entry.name.endswith(META_SUFFIX):
                        yield Path(entry.path)


def _claimed_worktrees(root: Path) -> tuple[dict[Path, Path] | None, str]:
    """Map transcript-recorded worktree paths to their transcript files."""
    claims: dict[Path, Path] = {}
    try:
        metas = list(_walk_transcript_metas(root))
    except OSError as exc:
        return None, f"{root} could not be fully enumerated ({exc})"
    for meta in metas:
        try:
            raw = meta.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        jsonl = meta.with_name(meta.name[: -len(META_SUFFIX)] + TRANSCRIPT_SUFFIX)
        for key in WORKTREE_PATH_KEYS:
            value = data.get(key)
            if not isinstance(value, str) or not value:
                continue
            claimed = _resolve(Path(value))
            if claimed is None:
                continue
            claims.setdefault(claimed, jsonl)
    return claims, f"{len(claims)} worktree path(s) claimed by agent transcripts"


def transcript_signal(
    path: Path,
    *,
    root: Path | None = None,
    window_seconds: float | None = None,
    now: float | None = None,
) -> Signal:
    """Report recent agent transcript activity, failing toward live when blind."""
    reference = time.time() if now is None else now
    window = recency_seconds() if window_seconds is None else window_seconds
    base = transcript_root() if root is None else root

    if not base.is_dir():
        return Signal(
            "agent-transcript",
            False,
            f"the agent transcript root {base} does not exist, so no sub-agent on "
            "this host can be holding this path",
        )

    target = _resolve(path)
    if target is None:
        return Signal(
            "agent-transcript",
            True,
            f"{path} could not be resolved, so it cannot be compared against the "
            "worktree paths agent transcripts claim",
        )

    claims, detail = _claimed_worktrees(base)
    if claims is None:
        return Signal("agent-transcript", True, f"blind: {detail}")

    jsonl = claims.get(target)
    if jsonl is None:
        return Signal(
            "agent-transcript",
            False,
            f"no agent transcript records this path as its worktree ({detail})",
        )

    try:
        mtime_age: float | None = max(0.0, reference - jsonl.stat().st_mtime)
    except OSError:
        mtime_age = None
    record_age = _last_record_age(jsonl, reference)
    ages = [age for age in (mtime_age, record_age) if age is not None]
    if not ages:
        return Signal(
            "agent-transcript",
            True,
            f"an agent transcript claims this path but its age could not be read "
            f"({jsonl}) — cannot distinguish a finished agent from a live one",
        )

    age = min(ages)
    clock = "transcript mtime" if age == mtime_age else "the transcript record clock"
    if age <= window:
        return Signal(
            "agent-transcript",
            True,
            f"an agent transcript for this worktree was written {age:.0f}s ago by "
            f"{clock}, inside the {window:.0f}s recency window — a live agent between "
            f"tool calls has no process of its own to find ({jsonl})",
        )
    return Signal(
        "agent-transcript",
        False,
        f"the agent transcript for this worktree was last written {age:.0f}s ago, "
        f"beyond the {window:.0f}s recency window ({jsonl})",
    )


def _git_worktree_list(repo: Path) -> tuple[str | None, str]:
    """Return porcelain worktree state with inherited Git context removed."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            env=env,
            check=False,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"`git worktree list` could not be run in {repo} ({exc})"
    if result.returncode != 0:
        return None, (
            f"`git worktree list` failed in {repo} "
            f"({result.stderr.strip() or 'git error'})"
        )
    return result.stdout, "read"


def lock_signal(path: Path, *, repo: Path | None = None) -> Signal:
    """Report a Git worktree lock, failing toward live when lock state is unknown."""
    target = _resolve(path)
    if target is None:
        return Signal(
            "worktree-lock",
            True,
            f"{path} could not be resolved, so its lock state is unknown",
        )

    base = repo if repo is not None else path
    output, detail = _git_worktree_list(base)
    if output is None:
        return Signal("worktree-lock", True, f"blind: {detail}")

    current: Path | None = None
    for line in output.splitlines():
        if line.startswith("worktree "):
            current = _resolve(Path(line[len("worktree ") :].strip()))
            continue
        if not line.startswith("locked"):
            continue
        if current is None or current != target:
            continue
        reason = line[len("locked") :].strip()
        return Signal(
            "worktree-lock",
            True,
            "this worktree holds a `git worktree lock`"
            + (f" ({reason})" if reason else "")
            + " — a session or Claude Code's own reaper has claimed it",
        )
    return Signal("worktree-lock", False, "this worktree holds no `git worktree lock`")


def signals(
    path: Path,
    *,
    repo: Path | None = None,
    root: Path | None = None,
    window_seconds: float | None = None,
    now: float | None = None,
) -> list[Signal]:
    """Return non-process liveness signals in report order."""
    return [
        transcript_signal(path, root=root, window_seconds=window_seconds, now=now),
        lock_signal(path, repo=repo),
    ]
