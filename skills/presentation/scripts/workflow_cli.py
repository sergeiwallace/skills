"""Shared argparse and lock-reattachment support for public workflow-stage CLIs."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

try:
    from .presentation import LockUnavailable, PresentationError
    from .workflow_support import Workflow, reattach_workflow
except ImportError:  # Direct execution from the installed skill's scripts directory.
    from presentation import LockUnavailable, PresentationError
    from workflow_support import Workflow, reattach_workflow


def add_identity_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--deck", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--epoch", type=int, required=True)
    parser.add_argument("--timeout", type=float, default=30.0)


def run_stage(args: argparse.Namespace, action: Callable[[Workflow], Path]) -> int:
    try:
        workflow = reattach_workflow(
            args.deck, args.run_id, args.token, args.epoch, args.timeout
        )
    except (PresentationError, LockUnavailable) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        artifact = action(workflow)
    except (PresentationError, LockUnavailable) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        workflow.close()
    print(
        json.dumps(
            {
                "run_id": workflow.run_id,
                "epoch": workflow.epoch,
                "artifact": str(artifact),
            }
        )
    )
    return 0


def approve_main(argv: Sequence[str] | None = None) -> int:
    """Parse and dispatch the approve stage without requiring a subprocess."""
    parser = argparse.ArgumentParser()
    add_identity_arguments(parser)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    args = parser.parse_args(argv)
    return run_stage(
        args,
        lambda workflow: (
            workflow.approval(args.checkpoint, args.artifact) or args.artifact
        ),
    )


def publish_main(argv: Sequence[str] | None = None) -> int:
    """Parse and dispatch the publish stage without requiring a subprocess."""
    parser = argparse.ArgumentParser()
    add_identity_arguments(parser)
    parser.add_argument("--final-review", type=Path, required=True)
    args = parser.parse_args(argv)
    return run_stage(args, lambda workflow: workflow.publish(args.final_review))


def rollback_main(argv: Sequence[str] | None = None) -> int:
    """Parse and dispatch the rollback stage without requiring a subprocess."""
    parser = argparse.ArgumentParser()
    add_identity_arguments(parser)
    parser.add_argument("--target-run-id", required=True)
    parser.add_argument("--final-review-relative", default="qa/report.json")
    args = parser.parse_args(argv)
    return run_stage(
        args,
        lambda workflow: workflow.rollback(
            args.target_run_id, args.final_review_relative
        ),
    )


def seal_main(argv: Sequence[str] | None = None) -> int:
    """Parse and dispatch the seal stage without requiring a subprocess."""
    parser = argparse.ArgumentParser()
    add_identity_arguments(parser)
    parser.add_argument("--final-review", type=Path, required=True)
    args = parser.parse_args(argv)
    return run_stage(args, lambda workflow: workflow.seal(args.final_review))
