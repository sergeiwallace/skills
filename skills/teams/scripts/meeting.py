#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "typer>=0.12",
#     "pydantic>=2.6",
#     "pydantic-settings>=2.2",
#     "skill-cli-foundation",
#     "meeting-retrieve",
# ]
#
# [tool.uv.sources]
# # Relative to this file, so they resolve from a checkout of the skills repository.
# # Editable, so the shared packages are live.
# skill-cli-foundation = { path = "../../../packages/skill-cli-foundation", editable = true }
# meeting-retrieve = { path = "../../../packages/meeting-retrieve", editable = true }
# ///
"""Land a named Teams meeting: the shared meeting front end over this skill's teams.py.

The front end (packages/meeting-retrieve) resolves the meeting name, date and time;
teams.py, run as `uv run --script`, lists the instances and lands the chosen one. The
meetings are the [meetings.*] tables of the teams settings TOML, read at call time:
$TEAMS_CONFIG when it is set, else ~/.config/teams-skill/teams.toml.
"""

from __future__ import annotations

import os
from pathlib import Path

from meeting_retrieve import main

SCRIPTS = Path(__file__).resolve().parent
TRANSPORT = ["uv", "run", "--script", str(SCRIPTS / "teams.py")]
CONFIG = Path(os.environ.get("TEAMS_CONFIG") or "~/.config/teams-skill/teams.toml").expanduser()

if __name__ == "__main__":
    main(TRANSPORT, [CONFIG])
