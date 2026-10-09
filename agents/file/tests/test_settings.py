"""Unit tests for the validation resolve_settings applies to config.toml.

Hermetic: a real config file in tmp_path, no wizard (the file is complete)
and no runtime.
"""

import os

import pytest

from mascope_file_agent import AgentSettings, config, main


def _write_config(tmp_path, **overrides):
    settings = config.merge_settings(
        {
            "host": "mascope.example.com",
            "access_token": "tok",
            "source": str(tmp_path),
            **overrides,
        }
    )
    config.write_user_config(os.path.join(tmp_path, config.CONFIG_FILENAME), settings)


def test_resolve_settings_accepts_a_valid_instrument(tmp_path, monkeypatch):
    monkeypatch.setattr(main.sys, "argv", ["agent"])
    _write_config(tmp_path, instrument="Orbi-Lab2")

    settings = main.resolve_settings(str(tmp_path), str(tmp_path))

    assert settings["instrument"] == "Orbi-Lab2"


class _Runtime:
    """Stand-in for the loaded runtime, holding only what the check reads."""

    def __init__(self, instrument):
        self.config = type("Config", (), {"instrument": instrument})()


def test_start_refuses_an_instrument_the_server_would():
    # The server files uploads under this name, so a wrong one misfiles data
    # rather than merely degrading a timestamp. Refusing at start puts the
    # message where the operator is looking.
    with pytest.raises(main.ConfigError, match="letters, digits and hyphens"):
        main._validate_instrument(_Runtime("orbi lab 2"))


def test_start_accepts_a_valid_instrument():
    main._validate_instrument(_Runtime("Orbi-Lab2"))


def test_start_refuses_a_missing_instrument():
    # The server files this machine's uploads under the name, so an agent
    # without one has nowhere to put its data and says so instead of running.
    for value in ("", "   ", None):
        with pytest.raises(main.ConfigError, match="needs one"):
            main._validate_instrument(_Runtime(value))


def test_dev_mode_checks_the_instrument_once_the_runtime_exists(monkeypatch):
    # The check reads the loaded runtime rather than the settings dict, so it
    # covers dev mode - where the CLI owns the config and resolve_settings
    # never runs - and it runs after Runtime() so the refusal reaches the
    # agent log, which a headless prod install has instead of a console.
    # Not frozen under pytest, so initialize() takes the dev branch.
    calls = []
    monkeypatch.setattr(main, "Runtime", lambda *a, **k: calls.append("runtime"))
    monkeypatch.setattr(
        main,
        "_validate_instrument",
        lambda runtime: calls.append("validate_instrument"),
    )

    main.initialize()

    assert calls == ["runtime", "validate_instrument"]


def test_settings_are_read_from_a_config_toml(tmp_path):
    """A program that embeds the agent reads the file the program wrote."""
    _write_config(tmp_path, instrument="Orbi-Lab2", recursive=True, timeout=7)

    settings = AgentSettings.from_file(os.path.join(tmp_path, config.CONFIG_FILENAME))

    assert settings == AgentSettings(
        host="mascope.example.com",
        access_token="tok",
        source=str(tmp_path),
        instrument="Orbi-Lab2",
        recursive=True,
        timeout=7,
    )
    # What the file leaves out takes the defaults the program gives it.
    assert settings.mask == config.DEFAULT_SETTINGS["mask"]
    assert settings.verify_tls is True
    settings.validate()


def test_settings_from_a_broken_config_toml_say_so(tmp_path):
    path = tmp_path / config.CONFIG_FILENAME
    path.write_text("[file-agent\nhost = ", encoding="utf-8")

    with pytest.raises(config.ConfigError, match="TOML syntax error"):
        AgentSettings.from_file(str(path))
