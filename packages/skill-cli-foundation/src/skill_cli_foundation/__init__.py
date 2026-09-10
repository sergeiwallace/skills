"""skill-cli-foundation -- shared Typer CLI primitives for ai-harness skills (PROJECT-1234).

Exports the shared primitives (design D-1): ``make_app`` (app factory,
opt-in callback), the ``Annotated`` option aliases (``ModeOption``,
``ModelOption``, ``ModelIdOption``, ``DryRunOption``), ``BaseSkillSettings`` (config precedence),
and the ``cli_runner`` pytest fixture (via ``skill_cli_foundation.testing``).
"""

from __future__ import annotations

from .app import SharedContext, make_app
from .options import DryRunOption, Mode, ModelIdOption, ModelOption, ModeOption
from .settings import BaseSkillSettings, CallerSettings

__all__ = [
    "BaseSkillSettings",
    "CallerSettings",
    "DryRunOption",
    "Mode",
    "ModeOption",
    "ModelIdOption",
    "ModelOption",
    "SharedContext",
    "make_app",
]
