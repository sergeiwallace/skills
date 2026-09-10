"""Lock-fenced, append-only workflow primitives for presentation candidates."""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from .presentation import (
        DeckLock, PresentationError, _parse_frontmatter, normalize_brief, probe_platform,
        validate_research_paths, validate_story_contract,
    )
    from .qa_support import QAAssemblyError, validate_final_review
    from .render_support import atomic_bytes, atomic_json
except ImportError:  # Direct execution from the installed skill's scripts directory.
    from presentation import (
        DeckLock, PresentationError, _parse_frontmatter, normalize_brief, probe_platform,
        validate_research_paths, validate_story_contract,
    )
    from qa_support import QAAssemblyError, validate_final_review
    from render_support import atomic_bytes, atomic_json


class WorkflowError(PresentationError):
    """A workflow stage cannot safely continue."""


def file_digest(path: Path) -> str:
    if not path.is_file():
        raise WorkflowError(f"required artifact is missing: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _owner_path(deck: Path) -> Path:
    return deck / ".presentation-workflow.owner.json"


def _record(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowError(f"invalid workflow metadata: {path.name}") from exc


@dataclass
class Workflow:
    """One lock-held candidate.  All mutating methods fence on its owner record."""

    deck: Path
    lock: DeckLock
    run_id: str

    @property
    def run(self) -> Path:
        return self.deck / "runs" / self.run_id

    @property
    def token(self) -> str:
        assert self.lock.token is not None
        return self.lock.token

    @property
    def epoch(self) -> int:
        assert self.lock.epoch is not None
        return self.lock.epoch

    def fence(self, token: str | None = None, epoch: int | None = None) -> None:
        """Reject an old coordinator before it can make a visible mutation."""
        if self.lock.fd is None:
            raise WorkflowError("workflow owner no longer holds the deck lock")
        owner = _record(_owner_path(self.deck))
        if (owner.get("token"), owner.get("epoch")) != (
            token or self.token,
            epoch if epoch is not None else self.epoch,
        ) or owner.get("run_id") != self.run_id:
            raise WorkflowError("stale workflow owner token or epoch")

    def write(self, relative: str, content: bytes | str) -> Path:
        self.fence()
        path = self.deck / relative
        if isinstance(content, str):
            content = content.encode("utf-8")
        atomic_bytes(path, content)
        return path

    def candidate_write(self, relative: str, content: bytes | str) -> Path:
        self.fence()
        if ".." in Path(relative).parts or Path(relative).is_absolute():
            raise WorkflowError("candidate artifact path must be relative")
        path = self.run / relative
        if path.exists():
            raise WorkflowError(f"candidate artifact is immutable: {relative}")
        if isinstance(content, str):
            content = content.encode("utf-8")
        atomic_bytes(path, content)
        return path

    def approval(self, checkpoint: str, artifact: Path, approved: bool = True) -> None:
        self.fence()
        if checkpoint not in {
            "story-outline",
            "representative-visual-system",
            "final-rehearsal-export",
        }:
            raise WorkflowError(f"wrong checkpoint type: {checkpoint}")
        if checkpoint == "story-outline" and approved:
            fields = _parse_frontmatter(
                (self.deck / "brief.md").read_text(encoding="utf-8")
            )
            duration = int(fields["duration_minutes"])
            story_path = self.deck / "story.contract.json"
            try:
                story = (
                    json.loads(story_path.read_text(encoding="utf-8"))
                    if story_path.is_file()
                    else {}
                )
                validate_story_contract(
                    story, duration, fields.get("narrative_exception", "")
                )
            except (OSError, json.JSONDecodeError, PresentationError) as exc:
                raise WorkflowError(str(exc)) from exc
        entry = {
            "checkpoint": checkpoint,
            "digest": file_digest(artifact),
            "approved": approved,
            "run_id": self.run_id,
            "epoch": self.epoch,
        }
        approvals = self.deck / "approvals.jsonl"
        previous = approvals.read_bytes() if approvals.exists() else b""
        atomic_bytes(
            approvals, previous + json.dumps(entry, sort_keys=True).encode() + b"\n"
        )

    def require_approval(self, checkpoint: str, artifact: Path) -> None:
        self.fence()
        approvals = self.deck / "approvals.jsonl"
        if not approvals.is_file():
            raise WorkflowError(f"missing approval for {checkpoint}")
        digest = file_digest(artifact)
        records = []
        try:
            records = [
                json.loads(line) for line in approvals.read_text().splitlines() if line
            ]
        except json.JSONDecodeError as exc:
            raise WorkflowError("invalid approval record") from exc
        matches = [item for item in records if item.get("checkpoint") == checkpoint]
        if not matches:
            wrong = [item for item in records if item.get("digest") == digest]
            if wrong:
                raise WorkflowError(f"wrong checkpoint type for {checkpoint}")
            raise WorkflowError(f"missing approval for {checkpoint}")
        latest = matches[-1]
        if not latest.get("approved"):
            raise WorkflowError(f"rejected approval for {checkpoint}")
        if latest.get("digest") != digest:
            raise WorkflowError(f"stale digest approval for {checkpoint}")

    def require_render_approval(self, representative: Path | None) -> None:
        if representative is not None:
            self.require_approval("representative-visual-system", representative)

    def seal(self, final_review: Path) -> Path:
        self.fence()
        self.require_approval("final-rehearsal-export", final_review)
        if not final_review.is_file():
            raise WorkflowError("required artifact is missing: final QA report")
        try:
            validate_final_review(final_review, self.run_id)
        except QAAssemblyError as exc:
            raise WorkflowError(str(exc)) from exc
        seal = self.run / "run.seal.json"
        if seal.exists():
            raise WorkflowError("run is already sealed")
        files = {
            path.relative_to(self.run).as_posix(): file_digest(path)
            for path in sorted(self.run.rglob("*"))
            if path.is_file() and path != seal
        }
        if not files:
            raise WorkflowError("candidate run has no artifacts")
        payload = {
            "run_id": self.run_id,
            "epoch": self.epoch,
            "owner_token": self.token,
            "artifacts": files,
            "final_review_digest": file_digest(final_review),
        }
        atomic_json(seal, payload)
        return seal

    def verify_seal(self, run: Path | None = None) -> dict[str, Any]:
        candidate = run or self.run
        seal = candidate / "run.seal.json"
        payload = _record(seal)
        actual = {
            path.relative_to(candidate).as_posix(): file_digest(path)
            for path in sorted(candidate.rglob("*"))
            if path.is_file() and path != seal
        }
        if payload.get("artifacts") != actual:
            raise WorkflowError("sealed run has been modified after sealing")
        return payload

    def publish(
        self, final_review: Path, token: str | None = None, epoch: int | None = None
    ) -> Path:
        self.fence(token, epoch)
        self.require_approval("final-rehearsal-export", final_review)
        try:
            validate_final_review(final_review, self.run_id)
        except QAAssemblyError as exc:
            raise WorkflowError(str(exc)) from exc
        seal = self.run / "run.seal.json"
        if seal.exists():
            payload = self.verify_seal()
            if payload.get("final_review_digest") != file_digest(final_review):
                raise WorkflowError("sealed final approval digest is invalid")
        else:
            seal = self.seal(final_review)
            payload = self.verify_seal()
        # Invalidating the pointer only after a complete candidate has passed every gate makes a
        # failed later candidate safe without withdrawing an unrelated complete publication.
        (self.deck / "current.json").unlink(missing_ok=True)
        current = {
            "run_id": self.run_id,
            "epoch": self.epoch,
            "seal_digest": file_digest(seal),
            "final_review_digest": payload["final_review_digest"],
            "artifacts": payload["artifacts"],
        }
        self.fence(token, epoch)
        atomic_json(self.deck / "current.json", current)
        return self.deck / "current.json"

    def rollback(
        self, run_id: str, final_review_relative: str = "qa/report.json"
    ) -> Path:
        self.fence()
        candidate = self.deck / "runs" / run_id
        payload = self.verify_seal(candidate)
        final_review = candidate / final_review_relative
        if file_digest(final_review) != payload.get("final_review_digest"):
            raise WorkflowError("rollback final approval digest is invalid")
        # Matching approval is intentionally required even for an old sealed run.
        self.require_approval("final-rehearsal-export", final_review)
        atomic_json(
            self.deck / "current.json",
            {
                "run_id": run_id,
                "epoch": self.epoch,
                "seal_digest": file_digest(candidate / "run.seal.json"),
                "final_review_digest": payload["final_review_digest"],
                "artifacts": payload["artifacts"],
            },
        )
        return self.deck / "current.json"

    def close(self) -> None:
        self.lock.release()


def start_workflow(
    deck: Path,
    brief: str | Path,
    research_paths: list[Path] | None = None,
    timeout: float = 30.0,
) -> Workflow:
    """Validate without deck writes, then acquire and initialize one fenced candidate."""
    normalized = normalize_brief(brief)
    validate_research_paths(research_paths or [])
    probe_platform()
    lock = DeckLock(deck, timeout=timeout).acquire()
    workflow = Workflow(deck=deck, lock=lock, run_id=uuid.uuid4().hex)
    try:
        # Path.mkdir treats None and an omitted exist_ok as False, so these mutations are equivalent.
        workflow.run.mkdir(parents=True, exist_ok=False)
        # Bind the run before calling any fenced method, so a later CLI invocation can only attach
        # to this exact candidate identity.
        owner = _record(_owner_path(deck))
        owner["run_id"] = workflow.run_id
        atomic_json(_owner_path(deck), owner)
        workflow.write("brief.md", normalized)
        (deck / "current.json").unlink(missing_ok=True)
        return workflow
    except BaseException:
        workflow.close()
        raise


def reattach_workflow(
    deck: Path, run_id: str, token: str, epoch: int, timeout: float = 30.0
) -> Workflow:
    """Reacquire a candidate's lock while proving its persisted owner identity."""
    lock = DeckLock(deck, timeout=timeout).reattach(token, epoch)
    workflow = Workflow(deck=deck, lock=lock, run_id=run_id)
    try:
        if not workflow.run.is_dir():
            raise WorkflowError(f"candidate run is missing: {run_id}")
        workflow.fence(token, epoch)
        return workflow
    except BaseException:
        workflow.close()
        raise


def discard_unsealed_run(run: Path) -> None:
    """Used only by a coordinator while it still owns the lock after a failed stage."""
    if run.exists() and not (run / "run.seal.json").exists():
        shutil.rmtree(run)
