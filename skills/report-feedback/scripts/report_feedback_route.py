#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "typer>=0.12",
#     "skill-cli-foundation",
# ]
#
# [tool.uv.sources]
# # Path stays inside the symlinked skills tree, so it resolves from any invocation CWD.
# skill-cli-foundation = { path = "../../../packages/skill-cli-foundation", editable = true }
# ///
"""Resolve the `/report-feedback` delivery route before the skill renders a report."""

from __future__ import annotations

import json
import sys
from enum import Enum
from typing import Annotated

import typer
from skill_cli_foundation import make_app


class Via(str, Enum):
    """Supported report-delivery routes."""

    feedback = "feedback"
    claude = "claude"


app = make_app("report-feedback-route")
ViaOption = Annotated[Via, typer.Option("--via", help="feedback | claude")]


@app.command()
def main(
    description: str = typer.Argument(
        ...,
        help="Bug description, feature request, issue reference, or affected product",
    ),
    via: ViaOption = Via.feedback,
) -> None:
    """Print the validated route envelope consumed by the attending skill."""
    typer.echo(json.dumps({"description": description, "via": via.value}))


if __name__ == "__main__":
    sys.exit(app())
