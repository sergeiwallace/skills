"""Read-only primitives shared by the git-review sibling engines.

These implementations were physically extracted from ``git_review_sweep.py``.
The sweep imports them from here, so each shared primitive has one definition.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

try:
    import psutil
except ImportError:  # pragma: no cover - exercised by the liveness fail-closed guard
    psutil = None

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent


def _load(module_name: str, path: Path):
    """Load a script by absolute path.

    By path, not by import: these run as standalone scripts, so a plain import would
    depend on the caller's ``sys.path``. Registered in ``sys.modules`` BEFORE exec
    because ``@dataclass`` resolves its annotations through ``sys.modules``.
    """
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


sync_git_support = _load(
    "_git_review_sync_worktree_state",
    REPO_ROOT / "skills" / "sync-git" / "scripts" / "worktree_state.py",
)

DEFAULT_REGISTRY = REPO_ROOT / "config" / "fleet-projects.toml"
ALWAYS_PROTECTED = frozenset({"main", "master", "HEAD HEAD", "HEAD"})
SKIP_DIRS = frozenset({".git", ".worktrees", "node_modules", "__pycache__"})
LSOF_TIMEOUT_SECONDS = 10


class GatedBranch(RuntimeError):
    """Raised when an operation targets a branch behind a hard human gate (AC-5)."""


class RegistryUnreadable(RuntimeError):
    """Raised when the registry file EXISTS but cannot be read as a project list.

    Distinct from an absent registry, which is a supported configuration (PROJECT-1234 F-2).
    """


@dataclass
class Repo:
    name: str
    path: Path
    registered: bool = True


@dataclass
class Skip:
    """Something deliberately left out of the sweep. AC-9: never silent."""

    subject: str
    reason: str


@dataclass
class TargetResolution:
    """The repo's integration target — from config, or origin/HEAD, or nothing."""

    ref: str | None
    source: str
    reason: str = ""


@dataclass
class Liveness:
    live: bool
    pids: list[int]
    probe_ok: bool
    reason: str


def _git(
    args: list[str],
    cwd: Path,
    *,
    env_overrides: dict[str, str] | None = None,
    strip: bool = True,
) -> tuple[int, str, str]:
    """Run git with a scrubbed environment. Never raises on a non-zero exit.

    ``strip=False`` is MANDATORY for any NUL-separated stream (`-z`), because a
    ``git status --porcelain`` code legitimately BEGINS WITH A SPACE -- `` M`` is
    "modified in the worktree, not staged". Stripping eats that space and shifts the
    FIRST record left by one byte, which corrupts two things at once and reports
    neither: the path loses its first character (``.beads/x`` -> ``beads/x``, then
    unreadable), and the code inverts from unstaged to staged (`` M`` -> ``M ``).
    Measured 2026-08-31: that is why ``/sync-git`` refused its only autonomous
    capability with ``unreadable candidate path beads/interactions.jsonl`` for a file
    present at 578,449 bytes, and two of the five ``-z`` sites are in the ship/merge
    path, where a wrong cleanliness verdict decides whether work ships.

    Only the FIRST record is hit -- later records' leading spaces sit behind a NUL that
    ``strip()`` never reaches -- so the victim is chosen by git's output ORDER, and the
    bug hides completely whenever the first entry happens to be staged.

    The default stays ``True`` because ~31 callers rely on it: ``rev-parse`` output
    would otherwise carry its trailing newline into a path.

    GIT_* is stripped because git exports GIT_DIR into every hook subprocess and
    ``cwd=`` does not override it (PROJECT-1234) — inherited, a sweep launched from a hook
    would operate on the hook's repo rather than the one it was asked about.

    ``env_overrides`` re-adds specific variables AFTER the scrub, for the one caller that
    needs a deliberate identity (the annotated ``rejected/*`` tag). Passing that identity
    here rather than as leading ``git -c`` flags keeps the git SUBCOMMAND at argv[0],
    which is what the AST allowlist test inspects.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    if env_overrides:
        env.update(env_overrides)
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
    except OSError as exc:
        return 127, "", str(exc)
    out = result.stdout.strip() if strip else result.stdout
    return result.returncode, out, result.stderr.strip()


def load_registry(path: Path = DEFAULT_REGISTRY) -> dict[str, dict]:
    """Per-repo config keyed by repo name, from ``config/fleet-projects.toml``.

    ABSENT and UNREADABLE are different answers (PROJECT-1234 F-2). An absent registry
    yields ``{}``: the registry supplies target branches and protections, and its
    absence must degrade to "discover from disk and resolve targets from git", never to
    an assumed default branch. A registry that EXISTS but cannot be parsed RAISES.

    PROBE ADEQUACY: ``protected_branches`` and ``gated_branches`` are both read out of
    this dict, and an empty dict is indistinguishable from "this repo declares nothing
    protected". So the four degraded paths that used to return ``{}`` — unreadable file,
    invalid TOML, no ``projects`` key, ``projects`` of the wrong type — silently removed
    EVERY protection in the fleet, including a repository's own hard-gated ``main``. A one-character
    typo in the registry therefore un-gated the single branch that must never be
    touched, and the sweep would have logged its ordinary proofs the whole way.
    """
    if not path.is_file():
        return {}
    try:
        import tomllib

        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RegistryUnreadable(
            f"{path} exists but could not be read as TOML ({exc}). Refusing to sweep: "
            "branch protections and hard gates are declared here, so proceeding with an "
            "empty registry would silently un-protect every branch in the fleet."
        ) from exc
    entries = data.get("projects")
    if not isinstance(entries, list):
        raise RegistryUnreadable(
            f"{path} parsed but has no `projects` array (found "
            f"{type(entries).__name__}). Refusing to sweep: an empty registry is "
            "indistinguishable from 'nothing is protected'."
        )
    return {
        entry["name"]: entry
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("name"), str)
    }


def default_projects_dir(start: Path | None = None) -> Path:
    """The fleet's projects root, resolved from the MAIN checkout, not a worktree.

    ``Path(__file__).parent.parent.parent`` is wrong whenever this runs from a linked
    worktree: measured on a live dry run launched from
    ``<repo>/.claude/worktrees/agent-<hex>``, it took that checkout's parent as the
    projects root, discovered eight sibling AGENT WORKTREES as fleet repos, and
    reported a real, on-disk repository as "in the registry but not on disk".
    ``main_worktree_root`` follows ``--git-common-dir`` back to the main checkout, so
    the default is correct from anywhere.
    """
    anchor = start or Path(__file__).resolve().parent
    try:
        return sync_git_support.main_worktree_root(anchor).parent
    except sync_git_support.GitUnavailable:
        return REPO_ROOT.parent


def discover_repos(
    projects_dir: Path,
    registry: dict[str, dict],
    *,
    only: list[str] | None = None,
) -> tuple[list[Repo], list[Skip]]:
    """Every git repo directly under ``projects_dir``, plus what was left out.

    DISCOVERED, not read from the registry (AC-1): the registry is a prefix map that
    lags reality, and a repo it does not name still holds real work. Registry entries
    are used to *annotate* what was found and to report what is missing — a name in
    the registry with no checkout is a stated skip, never a silent omission.
    """
    repos: list[Repo] = []
    skips: list[Skip] = []
    if not projects_dir.is_dir():
        return repos, [Skip(str(projects_dir), "projects directory does not exist")]

    for child in sorted(projects_dir.iterdir()):
        if not child.is_dir() or child.name in SKIP_DIRS or child.name.startswith("."):
            continue
        if not (child / ".git").exists():
            continue
        if only is not None and child.name not in only:
            skips.append(Skip(child.name, "excluded by --only"))
            continue
        repos.append(Repo(child.name, child, child.name in registry))

    found = {repo.name for repo in repos}
    excluded = {skip.subject for skip in skips}
    for name in sorted(registry):
        if name not in found and name not in excluded:
            if only is not None and name not in only:
                continue
            skips.append(Skip(name, "in the registry but not on disk in this machine"))
    return repos, skips


def resolve_target(repo: Path, config: dict) -> TargetResolution:
    """The repo's integration target: config first, ``origin/HEAD`` as the fallback.

    Never defaults to ``main``. A configured branch that does not exist on the remote
    is UNRESOLVED rather than quietly falling through to origin/HEAD — silently
    substituting a different target would compute every downstream proof against the
    wrong ref, which is the failure AC-4 names.

    PROBE ADEQUACY: ``ref=None`` is the answer that makes a repo report-only. If this
    function wrongly defaulted, a repo with no remote would still return
    ``refs/remotes/origin/main`` and every ``--is-ancestor`` against it would exit
    128, which is indistinguishable on stdout from "not merged".
    """
    configured = config.get("target_branch")
    if isinstance(configured, str) and configured:
        ref = f"refs/remotes/origin/{configured}"
        rc, _, _ = _git(["rev-parse", "--verify", "--quiet", ref], repo)
        if rc == 0:
            return TargetResolution(ref, "config", f"config names {configured}")
        return TargetResolution(
            None,
            "unresolved",
            f"config names target `{configured}` but {ref} does not exist — "
            "surfacing rather than falling back",
        )

    ref = sync_git_support.resolve_pushed_target(repo)
    if ref:
        return TargetResolution(ref, "origin-head", f"resolved {ref} from git")
    return TargetResolution(
        None,
        "unresolved",
        "no config target and no origin/HEAD or origin/<default> — this repo is "
        "report-only; nothing is deletable",
    )


def protected_branches(config: dict, target: str | None) -> frozenset[str]:
    """Branch names that survive any merge proof (predicate (b)).

    The target itself, main/master, and every branch a repo's config declares
    long-lived. A team's own shared integration or workspace branches are common
    instances of this.
    """
    names = set(ALWAYS_PROTECTED)
    if target:
        names.add(target.rsplit("/", 1)[-1])
        names.add(target.removeprefix("refs/remotes/origin/"))
    declared = config.get("protected_branches")
    if isinstance(declared, list):
        names.update(name for name in declared if isinstance(name, str))
    return frozenset(names)


def gated_branches(config: dict) -> frozenset[str]:
    """Branches behind a HARD HUMAN GATE — never pushed to, merged into, or deleted.

    A hard-gated `main` is the motivating case (AC-5): the rest of that repo is in
    normal scope, and only its main is untouchable.
    """
    declared = config.get("gated_branches")
    if isinstance(declared, list):
        return frozenset(name for name in declared if isinstance(name, str))
    return frozenset()


def _assert_not_gated(branch: str, config: dict, operation: str) -> None:
    if branch in gated_branches(config):
        raise GatedBranch(
            f"`{branch}` is a hard human gate in this repo — refusing to {operation} "
            "it. Route through a PR, or get an explicit human override."
        )


def _macos_lsof_cwds() -> list[tuple[int, Path]]:
    """Return cwd values from macOS's native ``lsof`` fallback.

    Some macOS sandboxes deny psutil's ``sysctl(KERN_PROC_ALL)`` enumeration but
    permit lsof to inspect cwd file descriptors. The fallback is macOS-only; psutil
    remains the portable implementation on Linux and Windows.
    """
    try:
        result = subprocess.run(
            ["lsof", "-n", "-d", "cwd", "-Fpn0"],
            capture_output=True,
            check=False,
            timeout=LSOF_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise OSError(f"lsof timed out after {LSOF_TIMEOUT_SECONDS} seconds") from exc
    if result.returncode != 0:
        raise OSError(
            os.fsdecode(result.stderr).strip() or f"lsof exited {result.returncode}"
        )

    cwds: list[tuple[int, Path]] = []
    pid: int | None = None
    cwd_file = False
    # With `-F...0`, fields are NUL-delimited. lsof still inserts a newline
    # between records, so remove only that record separator — never path content.
    for raw_field in result.stdout.split(b"\0"):
        field = raw_field.removeprefix(b"\n")
        if field.startswith(b"p"):
            if cwd_file:
                raise OSError(f"lsof did not return a cwd path for pid {pid}")
            try:
                pid = int(field[1:])
            except ValueError:
                pid = None
            cwd_file = False
        elif field == b"fcwd":
            cwd_file = True
        elif cwd_file and field.startswith(b"n"):
            if pid is None:
                raise OSError("could not parse lsof pid for a cwd path")
            try:
                cwds.append((pid, Path(os.fsdecode(field[1:])).resolve()))
            except (OSError, RuntimeError) as exc:
                raise OSError(
                    f"could not resolve lsof cwd for pid {pid}: {exc}"
                ) from exc
            cwd_file = False
        elif field.startswith(b"f"):
            if cwd_file:
                raise OSError(f"lsof did not return a cwd path for pid {pid}")
            cwd_file = False
    if cwd_file:
        raise OSError(f"lsof did not return a cwd path for pid {pid}")
    return cwds


def _ancestor_pids() -> frozenset[int]:
    """The pids in this process's own parent chain, or empty when it cannot be walked.

    Empty on failure is the fail-closed direction: an ancestor we cannot identify is
    simply not discounted, so the probe keeps its existing blind-and-veto behavior.
    """
    if psutil is None:
        return frozenset()
    pids: set[int] = set()
    try:
        process = psutil.Process()
        while True:
            process = process.parent()
            if process is None:
                break
            pids.add(process.pid)
    except (psutil.Error, OSError):
        return frozenset(pids)
    return frozenset(pids)


def _is_user_session_manager(pid: int, uid: int) -> bool:
    """Whether ``pid`` is the registered manager of ``uid``'s systemd user session.

    A user manager occupies its unit's ``init.scope`` rather than an application or
    login ``session-*.scope``.  This is a cgroup identity, not a process-name list,
    so it remains true regardless of the prober's parent chain (for example when a
    tmux server is attached directly to the system manager).

    Anything unreadable or not exactly this unit remains a denial: reclamation must
    not infer that an ordinary same-uid process is harmless.
    """
    expected_tail = (
        "user.slice",
        f"user-{uid}.slice",
        f"user@{uid}.service",
        "init.scope",
    )
    try:
        records = Path(f"/proc/{pid}/cgroup").read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return False
    for record in records:
        fields = record.split(":", 2)
        if len(fields) != 3:
            continue
        components = tuple(part for part in fields[2].split("/") if part)
        if components[-4:] == expected_tail:
            return True
    return False


def liveness(path: Path, *, repo: Path | None = None) -> Liveness:
    """Whether a live process's cwd is inside ``path`` OR in an ancestor of it.

    FAILS CLOSED to live.

    This is what the audit tool's lock-pid signal cannot see: a live agent between
    commits holds a clean tree and may hold no lock at all, so every name- and
    merge-based predicate calls its worktree stale.

    BOTH DIRECTIONS (PROJECT-1234). Asking only "is a pid at or below this path" misses a live
    PARENT, and worktrees nest — ``<session>/<task>/<leaf>``. Measured on the real box: a
    canonical session home came back ``live=True`` with three pids while a task worktree
    NESTED INSIDE IT came back ``live=False``, and the sweep called the nested one
    deletable. Deleting it would have removed a directory underneath a working session,
    and the canonical-name veto did not cover it because the nested name matches no
    canonical pattern.

    THE WALK IS BOUNDED, and the bound is the point. ``repo`` stops it at (and excluding)
    the repository root, so a session sitting in a repo's top level — the common case —
    does not make every worktree in that repo permanently undeletable. A SIBLING of a live
    worktree shares an ancestor with it and is still correctly not live. Without a bound
    the veto degrades into "everything under the projects root is live", which is useless
    in the opposite direction. With no ``repo`` given the walk covers ancestors strictly
    inside ``path``'s own parents, which is why callers pass it.

    NOT FOR A REPO ROOT — use ``git_pull_sweep.tree_liveness`` instead. ``path`` is answered
    as "is a pid at or below here", which is what DELETING a directory needs (an open shell
    in a deleted directory is unrecoverable) and wrong for a MAIN TREE: linked worktrees sit
    physically below the repo root, so every worktree's pids read as the main tree's own and
    it is permanently live. Every caller in this module and in ``worktree_audit`` passes a
    worktree or orphan path, never the root — the audit's repair loop inherits
    ``flag_mistracked_upstreams``' ``is_main`` exclusion — so the bug cannot reach them.
    ``tree_liveness`` is where a main tree is targeted, and it subtracts each nested
    worktree's pids before judging. Calling this function directly on a repo root
    reproduces the over-veto, which is an artifact of the probe, not a sweep defect
    (PROJECT-1234).

    PROBE ADEQUACY — the reason ``probe_ok`` exists. Enumerating ``/proc`` returns an
    EMPTY list both when nothing is running there and when procfs is absent or every
    ``cwd`` symlink is unreadable. Those are the same observation and opposite
    conclusions, so an unusable probe reports ``live=True``: a wrong "dead" deletes a
    checkout somebody is working in, and a wrong "alive" costs one skipped cleanup.

    PROBE ADEQUACY, THE OTHER EDGE (PROJECT-1234). Failing closed on *any* unreadable cwd
    made the veto useless in the opposite direction: one short-lived process — usually a
    ``git`` child of the caller itself — denies its cwd, and every target in the sweep
    comes back ``live=True`` on no evidence. Measured on this box: ~1 denial per 65-81
    processes seen, the denied pid ALREADY GONE when looked up, 15 of 45 rows skipped on
    a live ``/pull-git`` run, and the sibling negative control red 3/3 under load. So a
    denial is re-examined once: a process that has EXITED is positive evidence of absence
    and is discounted, while one still running, or whose existence cannot be established,
    still blinds the probe. ``readable == 0`` below is what keeps a fully-discounted
    inventory from reading as "quiet".

    HARDENED PROCESSES AND OUR OWN LINEAGE (PROJECT-1234). On a systemd user session the
    two rules above are not enough: ``systemd --user`` and ``(sd-pam)`` are same-uid,
    alive, not zombies and not setuid, yet their cwd is genuinely unreadable — measured
    via both psutil and ``os.readlink``, so there is no ``/proc`` fallback. Left
    fail-closed they blind the probe for EVERY path on every such desktop, permanently,
    so the sweep can never conclude a worktree is quiet.

    Three causal discounts, deliberately not a list of process names — a name list
    was tried first and the very next process on the same box (``(sd-pam)``) already
    escaped it. A denial is discounted when the process is KERNEL-HARDENED (it cleared
    its dumpable flag, which the kernel exposes by reassigning the ``/proc/<pid>`` entry
    ownership), is the registered SYSTEMD USER SESSION MANAGER (its cgroup is the user
    unit's ``init.scope``), or is an ANCESTOR OF THIS PROCESS. The session-manager
    discriminator is independent of ancestry because tmux can descend from PID 1 rather
    than the user manager. Neither can describe a shell, editor or coding agent working
    in the target: those are dumpable, so their cwd reads fine and they arrive as positive
    hits that veto normally. All fail closed when the signal is unavailable, which is
    what preserves today's behavior on macOS and Windows.
    """
    if psutil is None:
        return Liveness(
            True,
            [],
            False,
            "process liveness could not be determined: psutil is unavailable, so "
            "an empty result would be indistinguishable from 'nothing running'",
        )

    try:
        resolved = path.resolve()
    except (OSError, RuntimeError) as exc:
        return Liveness(True, [], False, f"could not resolve {path}: {exc}")

    # The enclosing directories a live cwd would make this path unsafe to touch: those
    # strictly between the repo root and `path`. The root itself is EXCLUDED, so a session
    # sitting in the repo's top level vetoes nothing.
    #
    # The pid's cwd must BE one of these, not merely sit somewhere beneath one. Matching
    # "at or below an ancestor" is the over-broad version, and it is wrong in the other
    # direction: two worktrees share the `.worktrees` parent, so a pid in one would veto
    # its SIBLING. The negative control caught exactly that.
    enclosing: set[Path] = set()
    if repo is not None:
        try:
            boundary = repo.resolve()
        except (OSError, RuntimeError):
            boundary = None
        if boundary is not None:
            enclosing = {
                parent
                for parent in resolved.parents
                if parent != boundary and parent.is_relative_to(boundary)
            }

    pids: list[int] = []
    ancestor_hits: set[Path] = set()
    readable = 0
    seen = 0
    vanished = 0
    discounted_denials = 0
    discounted_hardened = 0
    denied_pid: int | None = None
    denied_reason: str | None = None
    readable_before_denial = False
    denied_process_liveness_unavailable = False
    unreadable_cwd = object()
    # Windows has neither uid accessor. Without comparable process identities,
    # preserve the fail-closed behavior rather than assuming an unreadable cwd is
    # irrelevant.
    current_uid = os.getuid() if hasattr(os, "getuid") else None
    current_euid = os.geteuid() if hasattr(os, "geteuid") else None
    ancestor_pids = _ancestor_pids()

    def is_excluded_denied_process(process) -> bool:
        """Whether a denied cwd definitely cannot belong to this ordinary session.

        An inaccessible uid is deliberately NOT evidence that the process is another
        user: it could be the current user's sandboxed shell, editor, or agent. A
        same-real-uid process is excluded only when it has an effective uid different
        from both its real uid and this process's effective uid: that is a setuid
        helper, not an ordinary shell, editor, or coding agent.
        """
        if current_uid is None:
            return False
        try:
            uids = process.uids()
            if uids.real != current_uid:
                return True
            return (
                current_euid is not None
                and uids.effective != uids.real
                and uids.effective != current_euid
            )
        except (AttributeError, psutil.AccessDenied, psutil.NoSuchProcess):
            return False

    def holds_no_cwd(process) -> bool:
        """Whether an unreadable cwd is provable ABSENCE rather than blindness (PROJECT-1234).

        Two conditions qualify, and the second is the one measured on this box:

        * EXITED. ``is_running()`` compares creation time as well as pid, so a reused pid
          counts as exited — which is the intent: the process whose cwd we failed to read
          is gone, and a gone process occupies nothing.
        * ZOMBIE. A reaped-but-not-waited child still has a ``/proc`` entry, still reports
          ``is_running() == True``, and still carries our uid — but it has NO cwd, so
          ``readlink`` fails and psutil raises ``ZombieProcess``. This is what actually
          produced the ~1-in-70 denial: transient ``git`` children of the caller awaiting
          a ``wait()``. Exit-only checks miss it entirely.

        A check that cannot answer returns False, so the denial stands.
        """
        try:
            return not process.is_running() or process.status() == psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            # Includes ZombieProcess. Either way it IS the answer: nothing holding a cwd.
            return True
        except (AttributeError, psutil.Error, OSError):
            return False

    def confirmed_pid(process) -> int | None:
        """``process``'s pid, but only once it is confirmed to still BE that process.

        Both discounts below read kernel state keyed by pid, and a pid is not an
        identity: the number can be recycled, and a stale one points at whatever now
        holds it — on a Linux box, very often a root-owned kernel thread, which would
        read as "hardened" and discount a denial that should have stood. Comparing
        creation time is what psutil itself uses to make a pid an identity. Anything
        unverifiable returns None, so the denial stands.
        """
        try:
            pid = process.pid
            created = process.create_time()
        except (AttributeError, psutil.Error, OSError):
            return None
        try:
            if psutil.Process(pid).create_time() != created:
                return None
        except (psutil.Error, OSError):
            return None
        return pid

    def is_hardened_process(pid: int) -> bool:
        """Whether the kernel itself marked this pid unreadable, not merely private.

        A same-uid process denies ``/proc/<pid>/cwd`` only when it has cleared its
        dumpable flag, and the kernel signals that by reassigning the ownership of the
        entries under ``/proc/<pid>`` away from the real uid. An ordinary shell, editor
        or coding agent is dumpable, so its cwd IS readable and it never reaches this
        path at all — which is what makes the signal safe to act on. Measured on a
        Linux host: ``(sd-pam)`` presents exactly this shape.
        """
        if current_uid is None:
            return False
        try:
            return (
                os.stat(f"/proc/{pid}/cwd", follow_symlinks=False).st_uid != current_uid
            )
        except OSError:
            return False

    def is_ancestor_of_this_process(pid: int) -> bool:
        """Whether ``pid`` is in this process's own parent chain.

        Our own lineage is session infrastructure — the login manager, the terminal, the
        shell that launched the sweep. It cannot be an independent worker occupying the
        target: a lineage member that genuinely sits in the target has a READABLE cwd
        (measured: an ordinary bash's cwd reads fine at the same uid), so it arrives as a
        positive hit and vetoes normally. Only a HARDENED ancestor reaches the denial
        path, and a hardened ancestor is a session manager. Measured on a Linux host:
        ``systemd --user`` is exactly that and nothing else was.
        """
        return pid in ancestor_pids

    def record_denial(process, reason: str) -> None:
        """Account for one cwd we could not use, discounting the two harmless cases.

        Only the FIRST surviving denial is kept: one is already enough to make the
        inventory incomplete, and the pid is reported for diagnosis, not for counting.
        """
        nonlocal denied_pid, denied_reason, discounted_denials
        nonlocal discounted_hardened
        nonlocal readable_before_denial, denied_process_liveness_unavailable
        if is_excluded_denied_process(process):
            return
        verified = confirmed_pid(process)
        if verified is not None and (
            is_hardened_process(verified)
            or (
                current_uid is not None
                and _is_user_session_manager(verified, current_uid)
            )
            or is_ancestor_of_this_process(verified)
        ):
            discounted_hardened += 1
            return
        if holds_no_cwd(process):
            discounted_denials += 1
            return
        if denied_pid is None:
            denied_pid = process.pid
            denied_reason = reason
            # A partial inventory is only suitable for replacement after psutil has
            # already supplied at least one cwd.  The missing-accessor case is the
            # protected/kernel-adjacent shape that prompted the macOS lsof fallback.
            readable_before_denial = readable > 0
            denied_process_liveness_unavailable = not (
                callable(getattr(process, "is_running", None))
                and callable(getattr(process, "status", None))
            )

    try:
        for process in psutil.process_iter(["cwd"], ad_value=unreadable_cwd):
            seen += 1
            try:
                cwd = process.info["cwd"]
            except psutil.NoSuchProcess:
                vanished += 1
                continue
            except psutil.AccessDenied:
                record_denial(process, "AccessDenied")
                continue
            if cwd is unreadable_cwd:
                record_denial(process, "AccessDenied")
                continue
            if not isinstance(cwd, str):
                record_denial(process, "cwd attribute was not a string")
                continue
            try:
                cwd_path = Path(cwd).resolve()
            except (OSError, RuntimeError) as exc:
                record_denial(process, f"could not resolve cwd: {exc}")
                continue
            readable += 1
            if cwd_path == resolved or resolved in cwd_path.parents:
                pids.append(process.pid)
            elif cwd_path in enclosing:
                pids.append(process.pid)
                ancestor_hits.add(cwd_path)
    except (psutil.Error, OSError, RuntimeError) as exc:
        if sys.platform != "darwin":
            return Liveness(
                True,
                [],
                False,
                "process liveness could not be determined: process enumeration "
                f"failed: {exc}",
            )
        try:
            lsof_cwds = _macos_lsof_cwds()
        except (OSError, RuntimeError) as lsof_exc:
            return Liveness(
                True,
                [],
                False,
                "process liveness could not be determined: psutil enumeration "
                f"failed ({exc}); macOS lsof fallback failed: {lsof_exc}",
            )
        seen = len(lsof_cwds)
        readable = seen
        pids = []
        for pid, cwd_path in lsof_cwds:
            if cwd_path == resolved or resolved in cwd_path.parents:
                pids.append(pid)
            elif cwd_path in enclosing:
                pids.append(pid)
                ancestor_hits.add(cwd_path)

    if (
        not pids
        and denied_pid is not None
        and sys.platform == "darwin"
        and (readable_before_denial or denied_process_liveness_unavailable)
    ):
        try:
            lsof_cwds = _macos_lsof_cwds()
        except (OSError, RuntimeError) as lsof_exc:
            return Liveness(
                True,
                [],
                False,
                "process liveness could not be determined: process cwd was "
                f"unreadable (pid {denied_pid}: {denied_reason}); macOS lsof "
                f"fallback failed: {lsof_exc}",
            )
        seen = len(lsof_cwds)
        readable = seen
        pids = []
        ancestor_hits.clear()
        denied_pid = None
        for pid, cwd_path in lsof_cwds:
            if cwd_path == resolved or resolved in cwd_path.parents:
                pids.append(pid)
            elif cwd_path in enclosing:
                pids.append(pid)
                ancestor_hits.add(cwd_path)

    if pids:
        # A directly observed match is dispositive on its own — an unrelated
        # process's cwd being unreadable elsewhere in the same scan does not
        # make this positive finding any less certain.
        listed = ", ".join(str(pid) for pid in sorted(pids))
        where = (
            f"inside it or in an enclosing worktree ({', '.join(sorted(str(p) for p in ancestor_hits))})"
            if ancestor_hits
            else "inside it"
        )
        return Liveness(
            True,
            sorted(pids),
            True,
            f"pid(s) {listed} have their cwd {where} — a live session or agent "
            "is working here; never rebase, merge into, or delete it",
        )
    # Every discount is reported, in every branch. A silently swallowed denial is how a
    # degraded probe comes to look like a clean one.
    discounted = (
        f"; unreadable-but-holding-no-cwd (exited or zombie)={discounted_denials}"
        f"; unreadable-because-hardened-or-own-lineage={discounted_hardened}"
    )
    if denied_pid is not None:
        classification = (
            "AccessDenied counted, fail-closed"
            if denied_reason == "AccessDenied"
            else denied_reason
        )
        return Liveness(
            True,
            [],
            False,
            f"process cwd was unreadable (pid {denied_pid}: {classification}; "
            f"NoSuchProcess skipped={vanished}{discounted}; {readable} readable of "
            f"{seen} processes seen) — cannot prove nothing is running there",
        )
    if readable == 0:
        return Liveness(
            True,
            [],
            False,
            f"no process cwd was readable ({seen} processes seen{discounted}) — "
            "cannot distinguish 'nothing running' from 'cannot look'",
        )
    return Liveness(
        False,
        [],
        True,
        f"no process cwd is inside it or any enclosing worktree "
        f"({readable} pids inspected{discounted})",
    )


def reclamation_liveness(
    path: Path,
    *,
    repo: Path | None = None,
    root: Path | None = None,
    window_seconds: float | None = None,
    now: float | None = None,
) -> Liveness:
    """Every liveness signal a DELETION must clear. LIVE if ANY of them fires.

    THIS, not ``liveness``, is what a reclamation path calls. ``liveness`` answers one
    question -- "does a live process have its cwd here" -- and PROJECT-1234 measured that
    question being structurally unanswerable for the population this fleet deletes: a
    Claude Code sub-agent runs inside its PARENT session's process, so between tool
    calls its worktree holds no cwd and a fully live agent reads as idle. A sweep with
    only that signal deleted a four-minute-old agent's working directory and killed it.

    So the requirement is at least two INDEPENDENT signals, live if either fires, and a
    guard that fails toward keeping. The signals, all keyed on ``path`` and none able to
    fire for an unrelated one:

    * process cwd (``liveness``) -- a human's shell, editor, or an agent's tool call in
      flight. Its ``probe_ok=False`` degradation already means live and is preserved.
    * agent transcript recency -- the only positive evidence a live-but-idle sub-agent
      emits. Sees an agent blocked in a 57-minute commit, which nothing else does.
    * ``git worktree lock`` -- what Claude Code's own reaper uses as ITS liveness
      signal, so honouring it makes the two reapers agree instead of race.

    The ``pids`` list carries the cwd probe's pids ONLY, because that is the only signal
    that identifies a process. A caller wanting per-pid attribution (``tree_liveness``
    subtracting nested worktrees' pids) must keep calling ``liveness`` directly, and
    deliberately does: a main tree is not reclaimable and recency there would report
    every repo on the host permanently live.

    ``probe_ok`` stays the CWD probe's own adequacy flag rather than a summary of all
    three, because that is what every existing caller reads it for -- "could the process
    inventory be trusted". A blind non-cwd signal reports itself through ``live``, which
    is the fail-closed direction, so nothing is lost by not folding it in here.
    """
    process = liveness(path, repo=repo)
    extra = sync_git_support.signals(
        path, repo=repo, root=root, window_seconds=window_seconds, now=now
    )
    fired = [signal for signal in extra if signal.live]
    if process.live or not process.probe_ok:
        # The cwd probe already refuses. Anything the other signals add is reported so
        # the row explains itself, but it cannot change the verdict.
        notes = "".join(f"; {signal.name}: {signal.reason}" for signal in fired)
        return Liveness(
            True, process.pids, process.probe_ok, f"{process.reason}{notes}"
        )
    if fired:
        return Liveness(
            True,
            process.pids,
            process.probe_ok,
            "; ".join(f"{signal.name}: {signal.reason}" for signal in fired)
            + f" (the process-cwd probe found nothing: {process.reason})",
        )
    return Liveness(
        False,
        process.pids,
        process.probe_ok,
        "; ".join([process.reason, *(f"{s.name}: {s.reason}" for s in extra)]),
    )


class RenderableRepoResult(Protocol):
    """Structural sweep result accepted by the shared renderer."""

    repo: Repo
    target: TargetResolution
    worktrees: list[object]
    orphan_dirs: list[object]
    branches: list[tuple[str, object]]
    local_orphans: list[object]
    stashes: list[object]
    actions: list[object]
    skips: list[Skip]
    recommendations: list[object]


def _render(
    results: list[RenderableRepoResult], skips: list[Skip], *, dry_run: bool
) -> str:
    lines = [
        "git-review sweep — "
        + (
            "DRY RUN (nothing changed)"
            if dry_run
            else "LIVE — reclaiming the provably-redundant class"
        )
    ]
    for skip in skips:
        lines.append(f"  SKIPPED {skip.subject}: {skip.reason}")

    for result in results:
        target = result.target.ref or f"UNRESOLVED ({result.target.reason})"
        lines.append(f"\n{result.repo.name} — target: {target}")

        # An orphan appears in BOTH lists — the audit surfaces it as `unregistered` and
        # the sweep re-reports it with its content proof. Only the richer line is
        # printed, so one directory is not two findings a reader must reconcile.
        orphan_paths = {str(orphan.path) for orphan in result.orphan_dirs}
        for assessment in result.worktrees:
            if assessment.disposition in ("main",):
                continue
            if str(assessment.worktree.path) in orphan_paths:
                continue
            mark = "DELETABLE" if assessment.deletable else "keep"
            lines.append(
                f"  worktree [{assessment.disposition}/{mark}] "
                f"{assessment.worktree.path}: {'; '.join(assessment.reasons)}"
            )

        for orphan in result.orphan_dirs:
            mark = "PROVEN-REDUNDANT" if orphan.auto_deletable else "keep"
            lines.append(
                f"  orphan-dir [{mark}] {orphan.path}: {'; '.join(orphan.reasons)}"
            )

        for branch, verdict in result.branches:
            mark = "REDUNDANT" if verdict.redundant else "keep"
            lines.append(f"  branch [{mark}] origin/{branch}: {verdict.reason}")

        # Its own section, distinct from the remote-branch lines above, because the two
        # answer different questions and the remote pass structurally cannot see these.
        if result.local_orphans:
            actionable = [o for o in result.local_orphans if o.actionable]
            lines.append(
                f"  local orphaned branches (never pushed, no worktree): "
                f"{len(result.local_orphans)} found, {len(actionable)} actionable"
            )
            for orphan in result.local_orphans:
                mark = "REJECTED" if orphan.rejected else orphan.classification.upper()
                lines.append(f"    orphan [{mark}] {orphan.branch}: {orphan.reason}")

        for entry in result.stashes:
            mark = "REDUNDANT" if entry.verdict.redundant else "keep"
            lines.append(
                f"  stash [{mark}] {entry.ref} ({entry.message}): {entry.verdict.reason}"
            )

        for recommendation in result.recommendations:
            lines.append(
                f"  HUMAN GATE [{recommendation.kind}] {recommendation.subject}: "
                f"{recommendation.proposal}"
            )

        for action in result.actions:
            lines.append(
                f"  DID {action.kind} {action.subject} — proof: {action.proof}"
            )

        for skip in result.skips:
            lines.append(f"  SKIPPED {skip.subject}: {skip.reason}")

    return "\n".join(lines)
