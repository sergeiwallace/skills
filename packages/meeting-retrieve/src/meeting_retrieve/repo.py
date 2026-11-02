"""Which repository a meeting's artefacts land in.

Order: an explicit path; the current git top level when its repository name is the
configured one; the directory of that name beside the current repository's main worktree.
A repository's name is its origin URL's basename without ``.git``, else the directory name of
its main worktree, so a linked worktree with any directory name still answers to its repo.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def _stdout(done: subprocess.CompletedProcess) -> str | None:
    return done.stdout.strip() if done.returncode == 0 else None


def git_facts(cwd: Path) -> tuple[Path | None, Path | None, str | None]:
    """(top level, main worktree, origin URL) of the repository holding `cwd`."""
    git = shutil.which("git")
    if not git:
        return None, None, None
    try:
        located = subprocess.run(  # noqa: S603 - fixed argv, git resolved via shutil.which
            [
                git,
                "-C",
                str(cwd),
                "rev-parse",
                "--show-toplevel",
                "--path-format=absolute",
                "--git-common-dir",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        origin = subprocess.run(  # noqa: S603 - fixed argv, git resolved via shutil.which
            [git, "-C", str(cwd), "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, None, None
    lines = (_stdout(located) or "").splitlines()
    if len(lines) != 2:  # noqa: PLR2004 - one line per rev-parse query
        return None, None, None
    top, common = Path(lines[0]), Path(lines[1])
    main = common.parent if common.name == ".git" else common
    return top, main, _stdout(origin) or None


def repo_name(main: Path, origin: str | None) -> str:
    if origin:
        tail = origin.rstrip("/").replace(":", "/").rsplit("/", 1)[-1]
        if tail.removesuffix(".git"):
            return tail.removesuffix(".git")
    return main.name


def work_tree(path: Path) -> tuple[Path, str] | None:
    """(top level, repository name) of the git work tree holding `path`, or None."""
    if not path.is_dir():
        return None
    top, main, origin = git_facts(path)
    if top is None or main is None:
        return None
    return top, repo_name(main, origin)


def resolve(name: str | None, explicit: Path | None, cwd: Path) -> Path | None:
    """The output repository's root, or None when no rule applies."""
    if explicit is not None:
        return explicit.expanduser().resolve()
    top, main, origin = git_facts(cwd)
    if top is None or main is None:
        return None
    if name is None or repo_name(main, origin) == name:
        return top
    sibling = main.parent / name
    return sibling if sibling.is_dir() else None
