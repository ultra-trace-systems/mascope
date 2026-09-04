"""Unit tests for the validation resolve_settings applies to config.toml.

Hermetic: a real config file in tmp_path, no wizard (the file is complete)
and no runtime.
"""

import os

import pytest

from mascope_file_agent import config, main


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


def test_start_refuses_an_instrument_the_server_would(monkeypatch):
    # The server files uploads under this name, so a wrong one misfiles data
    # rather than merely degrading a timestamp. Refusing at start puts the
    # message where the operator is looking.
    monkeypatch.setattr(main, "runtime", _Runtime("orbi lab 2"))

    with pytest.raises(main.ConfigError, match="letters, digits and hyphens"):
        main._validate_instrument()


def test_start_accepts_a_valid_or_absent_instrument(monkeypatch):
    for value in ("Orbi-Lab2", "", None):
        monkeypatch.setattr(main, "runtime", _Runtime(value))
        main._validate_instrument()


def test_dev_mode_checks_the_instrument_once_the_runtime_exists(monkeypatch):
    # The check reads the loaded runtime rather than the settings dict, so it
    # covers dev mode - where the CLI owns the config and resolve_settings
    # never runs - and it runs after Runtime() so the refusal reaches the
    # agent log, which a headless prod install has instead of a console.
    # Not frozen under pytest, so initialize() takes the dev branch.
    calls = []
    monkeypatch.setattr(main, "Runtime", lambda *a, **k: calls.append("runtime"))
    monkeypatch.setattr(
        main, "_validate_instrument", lambda: calls.append("validate_instrument")
    )

    main.initialize()

    assert calls == ["runtime", "validate_instrument"]
