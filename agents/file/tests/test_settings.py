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


def test_resolve_settings_refuses_an_instrument_the_server_would(tmp_path, monkeypatch):
    # The name only rides along as metadata today, so a bad one would be
    # accepted in silence and refused the day the server files uploads under
    # it. Refusing it at start puts the message where the operator is looking.
    monkeypatch.setattr(main.sys, "argv", ["agent"])
    _write_config(tmp_path, instrument="orbi lab 2")

    with pytest.raises(main.ConfigError, match="letters, digits and hyphens"):
        main.resolve_settings(str(tmp_path), str(tmp_path))
