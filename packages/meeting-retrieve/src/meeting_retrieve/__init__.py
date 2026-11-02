"""meeting-retrieve -- the named-meetings front end shared by every meeting transport.

It resolves a meeting name (or an explicit conversation or chat title with a date) to one
recorded instance and an output directory, then drives the caller's transport through its
``chats``, ``instances`` and ``land`` primitives. Options parse through skill_cli_foundation.
"""

from __future__ import annotations

from .cli import build_app, main

__all__ = ["build_app", "main"]
