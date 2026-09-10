"""Phase 1 ACs P1.4, P1.5, P1.8 — BaseSkillSettings precedence (PROJECT-1234)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic_settings import SettingsConfigDict
from skill_cli_foundation import BaseSkillSettings, CallerSettings


def _settings_cls(config_path: Path, *, env_prefix: str = "AIH_TEST_"):
    class _Settings(BaseSkillSettings):
        model_config = SettingsConfigDict(
            env_prefix=env_prefix,
            toml_file=config_path,
            extra="ignore",
        )

        value: str = "default"

    return _Settings


@pytest.mark.parametrize(
    ("cli_value", "env_value", "file_value", "expected"),
    [
        (None, None, None, "default"),
        (None, None, "from_file", "from_file"),
        (None, "from_env", "from_file", "from_env"),
        ("from_cli", "from_env", "from_file", "from_cli"),
    ],
)
def test_settings_precedence_when_layered_then_highest_source_wins(
    tmp_path: Path,
    monkeypatch,
    cli_value,
    env_value,
    file_value,
    expected,
) -> None:
    """P1.4: init(CLI) > env > TOML > default across all four source levels."""
    cfg = tmp_path / "skill.toml"
    if file_value is not None:
        cfg.write_text(f'value = "{file_value}"\n', encoding="utf-8")
    if env_value is not None:
        monkeypatch.setenv("AIH_TEST_VALUE", env_value)
    else:
        monkeypatch.delenv("AIH_TEST_VALUE", raising=False)

    settings_cls = _settings_cls(cfg)
    kwargs = {"value": cli_value} if cli_value is not None else {}
    assert settings_cls(**kwargs).value == expected


def test_settings_when_required_field_unset_everywhere_then_validation_error(
    tmp_path: Path, monkeypatch
) -> None:
    """P1.5: a required field unset at every source raises ValidationError."""
    monkeypatch.delenv("AIH_TEST_REQUIRED", raising=False)
    cfg = tmp_path / "missing.toml"

    class _Settings(BaseSkillSettings):
        model_config = SettingsConfigDict(
            env_prefix="AIH_TEST_",
            toml_file=cfg,
            extra="ignore",
        )

        required: str

    with pytest.raises(ValidationError):
        _Settings()


def test_settings_when_distinct_env_prefixes_then_no_cross_read(
    tmp_path: Path, monkeypatch
) -> None:
    """P1.8: two subclasses with distinct env_prefix do not cross-read env vars."""
    cfg = tmp_path / "missing.toml"
    monkeypatch.setenv("AIH_ONE_VALUE", "one")
    monkeypatch.setenv("AIH_TWO_VALUE", "two")

    one_cls = _settings_cls(cfg, env_prefix="AIH_ONE_")
    two_cls = _settings_cls(cfg, env_prefix="AIH_TWO_")

    assert one_cls().value == "one"
    assert two_cls().value == "two"


def test_caller_settings_when_sources_are_built_then_omits_toml_layer() -> None:
    """PROJECT-1234: caller context is invocation metadata, never config state."""
    init_source = object()
    env_source = object()
    sources = CallerSettings.settings_customise_sources(
        CallerSettings,
        init_source,  # type: ignore[arg-type]
        env_source,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
    )
    assert sources == (init_source, env_source)
