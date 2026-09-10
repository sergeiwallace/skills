---
name: sync-git
description: Safely synchronize safe uncommitted work and local/remote branch state across selected repositories and worktrees; discard and gitignore recommendations always require human approval.
license: Apache-2.0
---

# sync-git

This skill may be self-invoked. With no target it assesses the repository main worktree first and
then the worktree containing the current session. A linked worktree is integrated with the current
fetched trunk (`main` by default) before its own remote branch is reconciled, so an abandoned
feature ref cannot become its integration base. It can also discover an explicitly bounded broader
scope.

```bash
uv run --script "${CLAUDE_SKILL_DIR}/scripts/sync_git.py" sync
uv run --script "${CLAUDE_SKILL_DIR}/scripts/sync_git.py" sync --dry-run
uv run --script "${CLAUDE_SKILL_DIR}/scripts/sync_git.py" sync --allow-deletions
uv run --script "${CLAUDE_SKILL_DIR}/scripts/sync_git.py" sync worktree:/absolute/worktree
uv run --script "${CLAUDE_SKILL_DIR}/scripts/sync_git.py" sync all-worktrees:/absolute/repo
uv run --script "${CLAUDE_SKILL_DIR}/scripts/sync_git.py" sync all-repos:~/projects
```

Extra targets must be explicit: `repo:<path>`, `worktree:<path>`,
`branch:<repo-path>@<branch>`, an exact registered worktree path, `all-worktrees:<repo-path>`,
or `all-repos:<directory>`. Discovery never defaults to the fleet: `all-repos` requires the
directory explicitly. The command has no force, discard, ignore, side-selection, or
administrative override.

It may commit all byte-vetted uncommitted changes, non-force-push local commits, and fast-forward
or cleanly merge fetched remote commits after its byte veto, anchor, isolated-index, local
readback, and remote readback checks. When `git merge-tree` reports a conflict, it first proves
whether the final trees already converge or whether every loss is a non-merge deletion already
committed on fetched trunk; the resulting integration commit records its proof and
`Sync-deletion-accepted:` trailers. It uses an exact force-with-lease only to reconcile an obsolete
worktree receive ref after that proof. Same-path divergent edits, renames, and unproved deletions
remain human checkpoints. Installations may add deterministic JSONL validation for selected paths
with the comma-separated `SYNC_GIT_MECHANICAL_JSONL_PATTERNS` environment variable. For example,
`.beads/*.jsonl` preserves that mechanical-export validation for a Beads store; when the variable is
unset, no format-specific path lane is applied.
New or modified sensitive-pattern paths and newly introduced secret-shaped bytes remain refused;
an unchanged, already committed safe template such as `.env.example` does not veto a later commit.
Tracked-file deletions are surfaced for explicit human approval by default because publishing one
discards remote content. Only pass `--allow-deletions` after that approval; the flag authorizes
deletions only and does not relax any other gate.

The skill has no discard or `.gitignore` mutation route. If analysis ever identifies either as a
possible recommendation, stop and present the exact paths, proposed action, and reasoning for
explicit user approval; do not continue that target until approval is received. This mandatory
human gate is why this otherwise side-effecting skill can be self-invoked.

Before changing general task work, record its pre-change scope (this does not make the later bytes
autonomous):

```bash
uv run --script "${CLAUDE_SKILL_DIR}/scripts/sync_git.py" prepare \
  --target worktree:/absolute/worktree --issue PROJECT-1234 --design docs/designs/example.md \
  --audit docs/audits/example.md path/that/will/change
```
