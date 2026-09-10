"""Phase 1 ACs P1.1-P1.3, P1.6, P1.7 — make_app + Annotated aliases (PROJECT-1234)."""

from __future__ import annotations

import pytest
import typer
from skill_cli_foundation import (
    DryRunOption,
    Mode,
    ModelIdOption,
    ModelOption,
    ModeOption,
    SharedContext,
    make_app,
)
from skill_cli_foundation.options import validate_shared_short_aliases


def _single_command_app() -> typer.Typer:
    app = make_app("x")

    @app.command()
    def main(
        path: str = typer.Argument(...),
        kind: str = typer.Option(..., "--kind"),
        mode: ModeOption = None,
    ) -> None:
        typer.echo(f"mode={None if mode is None else mode.value}")

    return app


def test_make_app_when_single_command_then_configured_without_callback() -> None:
    """P1.1: single-command mode configures the Typer and wires no callback."""
    app = make_app("x")
    assert isinstance(app, typer.Typer)
    assert app._add_completion is False
    assert app.info.no_args_is_help is True
    assert app.registered_callback is None


@pytest.mark.parametrize("flag", ("-m", "--mode"))
def test_command_with_mode_alias_when_flag_given_then_receives_enum(
    cli_runner,
    flag: str,
) -> None:
    """P1.2: no-command-name invocation; command receives Mode.automated."""
    app = _single_command_app()
    result = cli_runner.invoke(app, ["out.md", "--kind", "plan", flag, "automated"])
    assert result.exit_code == 0
    assert "mode=automated" in result.output


def test_mode_alias_when_invalid_value_then_exits_nonzero_naming_value(
    cli_runner,
) -> None:
    """P1.3: invalid --mode exits non-zero and names the invalid value."""
    app = _single_command_app()
    result = cli_runner.invoke(app, ["out.md", "--kind", "plan", "--mode", "bogus"])
    assert result.exit_code != 0
    assert "bogus" in result.output


def test_cli_runner_fixture_when_two_invocations_then_no_state_leakage(
    cli_runner,
) -> None:
    """P1.6: fixture yields a CliRunner reusable with no cross-invocation leak."""
    app = _single_command_app()
    first = cli_runner.invoke(app, ["a.md", "--kind", "plan", "--mode", "automated"])
    second = cli_runner.invoke(app, ["b.md", "--kind", "plan"])
    assert first.exit_code == 0
    assert second.exit_code == 0
    assert "mode=automated" in first.output
    assert "mode=None" in second.output


def test_make_app_when_multi_command_then_callback_stashes_ctx_obj(
    cli_runner,
) -> None:
    """P1.7: multi_command wires a callback; subcommand reads ctx.obj mode."""
    app = make_app("x", multi_command=True)

    @app.command()
    def build(ctx: typer.Context) -> None:
        obj: SharedContext = ctx.obj
        typer.echo(f"ctx-mode={None if obj.mode is None else obj.mode.value}")

    assert app.registered_callback is not None
    result = cli_runner.invoke(app, ["--mode", "automated", "build"])
    assert result.exit_code == 0
    assert "ctx-mode=automated" in result.output


def test_mode_enum_values() -> None:
    """Guard: the shared Mode enum carries the interactive/automated variants."""
    assert Mode.interactive.value == "interactive"
    assert Mode.automated.value == "automated"


def test_make_app_when_multi_command_invalid_mode_then_exits_nonzero(
    cli_runner,
) -> None:
    """P1.7 failure path (F3-2/JA-3): the multi_command callback validates the
    shared Enum-typed --mode, so an invalid value exits non-zero before the
    subcommand runs — validation lives in the shared alias, not per-skill."""
    app = make_app("x", multi_command=True)

    @app.command()
    def build(ctx: typer.Context) -> None:  # pragma: no cover - must not run
        typer.echo("ran")

    result = cli_runner.invoke(app, ["--mode", "bogus", "build"])
    assert result.exit_code != 0
    assert "ran" not in result.output


@pytest.mark.parametrize(
    ("model_flag", "dry_run_flag"),
    (("-M", "-n"), ("--model", "--dry-run")),
)
def test_model_and_dry_run_aliases_parse_on_a_command(
    cli_runner, model_flag: str, dry_run_flag: str
) -> None:
    """F3-3: the ModelOption + DryRunOption aliases are usable on a command
    signature and parse to their expected types (str override / bool flag)."""
    app = make_app("x")

    @app.command()
    def main(
        model: ModelOption = None,
        dry_run: DryRunOption = False,
    ) -> None:
        typer.echo(f"model={model} dry={dry_run}")

    result = cli_runner.invoke(app, [model_flag, "opus", dry_run_flag])
    assert result.exit_code == 0
    assert "model=opus dry=True" in result.output

    default = cli_runner.invoke(app, [])
    assert default.exit_code == 0
    assert "model=None dry=False" in default.output


def test_model_id_alias_parses_a_concrete_deployment_without_model_spelling(
    cli_runner,
) -> None:
    """ModelIdOption reserves -M for a concrete deployment identifier."""
    app = make_app("x")

    @app.command()
    def main(model_id: ModelIdOption = None) -> None:
        typer.echo(f"model-id={model_id}")

    short = cli_runner.invoke(app, ["-M", "gpt-5.6-codex"])
    long = cli_runner.invoke(app, ["--model-id", "deployment-42"])
    legacy = cli_runner.invoke(app, ["--model", "legacy"])

    assert short.exit_code == 0
    assert "model-id=gpt-5.6-codex" in short.output
    assert long.exit_code == 0
    assert "model-id=deployment-42" in long.output
    assert legacy.exit_code != 0


def test_shared_alias_collision_is_rejected() -> None:
    """T-2.1 failure path: a duplicate shared short letter cannot ship silently."""
    with pytest.raises(ValueError, match="must be unique"):
        validate_shared_short_aliases({"mode": "-m", "model": "-m"})
