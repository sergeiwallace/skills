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
"""Resolve `/research-doc` options without performing the research workflow."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass

import typer
from skill_cli_foundation import make_app

app = make_app("research-doc-resolve")


@dataclass(frozen=True)
class ResolutionError(Exception):
    """A user-facing invalid option combination."""

    message: str


def resolve_request(
    *,
    topic: str,
    model: str | None = None,
    inline: bool = False,
    refine: bool = False,
) -> dict[str, str | bool | None]:
    """Return the documented decision surface as a closed JSON-ready mapping."""
    if model is not None and not model.strip():
        raise ResolutionError("--model must not be empty")
    return {
        "topic": topic,
        "model": model,
        "inline": inline,
        "refine": refine,
    }


@app.command()
def main(
    topic: str = typer.Argument(
        ..., help="Research topic or path to a research document"
    ),
    model: str | None = typer.Option(
        None, "--model", help="Installation-supplied model preference"
    ),
    inline: bool = typer.Option(
        False, "--inline", help="Keep authoring in this session"
    ),
    refine: bool = typer.Option(False, "--refine", help="Refine an existing document"),
) -> None:
    """Resolve options only; the attending skill owns all workflow side effects."""
    try:
        resolution = resolve_request(
            topic=topic,
            model=model,
            inline=inline,
            refine=refine,
        )
    except ResolutionError as exc:
        typer.echo(f"[research-doc] {exc.message}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(json.dumps(resolution))


if __name__ == "__main__":
    sys.exit(app())
