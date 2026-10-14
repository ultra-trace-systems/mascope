"""Unit tests for the validation resolve_settings applies to config.toml.

Hermetic: a real config file in tmp_path, no wizard (the file is complete)
and no runtime.
"""

import os

import pytest

from mascope_file_agent import AgentSettings, config, identity, main


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


@pytest.mark.parametrize(
    "access_token, paired_as, expected",
    [
        ("tok", "instrument-agent", "instrument-agent"),
        ("tok", "file-agent", "file-agent"),
        # Paired before the name was recorded: a File Agent's credential.
        ("tok", "", "file-agent"),
        # Not paired yet: whatever the program pairs a new machine as.
        ("", "", "instrument-agent"),
    ],
)
def test_a_paired_machine_stays_the_service_it_was_paired_as(
    access_token, paired_as, expected
):
    settings = AgentSettings(
        host="mascope.example.com",
        access_token=access_token,
        source="C:/data",
        paired_as=paired_as,
    )

    assert settings.service_name(unpaired="instrument-agent") == expected


def test_a_file_agent_configuration_taken_over_stays_a_file_agent(tmp_path):
    """A File Agent's config.toml, as a program that embeds the agent adopts it.

    Written before the name was recorded, it has a credential and no
    ``paired_as``; the credential is a File Agent's, and is refused under any
    other name.
    """
    _write_config(tmp_path, instrument="Orbi-Lab2")

    settings = AgentSettings.from_file(os.path.join(tmp_path, config.CONFIG_FILENAME))

    assert settings.paired_as == ""
    assert settings.service_name(unpaired="instrument-agent") == "file-agent"


def test_a_renewed_token_keeps_the_service_it_was_paired_as(tmp_path):
    # A renewal is granted only under the name the token was paired as.
    identity("instrument-agent")
    path = os.path.join(tmp_path, config.CONFIG_FILENAME)
    _write_config(tmp_path, instrument="Orbi-Lab2", paired_as="instrument-agent")

    AgentSettings.from_file(path).token_writer(path)("renewed-token")

    reread = AgentSettings.from_file(path)
    assert reread.access_token == "renewed-token"
    assert reread.paired_as == "instrument-agent"


@pytest.mark.parametrize(
    "recorded, process, expected_on_file",
    [
        # A taken-over File Agent configuration, moved by a program that
        # names instrument-agent and lets the refusal re-pair the machine.
        ("", "instrument-agent", "instrument-agent"),
        # A standalone File Agent re-pairing on a file a program wrote.
        ("instrument-agent", "file-agent", "file-agent"),
    ],
)
def test_a_token_re_paired_under_another_name_is_saved_with_that_name(
    tmp_path, recorded, process, expected_on_file
):
    """The running agent re-pairs under the process's name, not the file's.

    Saved with the name read at the start, the new token would be named
    wrongly: the next start would present it under the old name, be refused,
    and pair back, leaving another machine under Paired machines each round.
    """
    path = os.path.join(tmp_path, config.CONFIG_FILENAME)
    _write_config(tmp_path, instrument="Orbi-Lab2", paired_as=recorded)
    writer = AgentSettings.from_file(path).token_writer(path)
    identity(process)

    writer("re-paired-token")

    reread = AgentSettings.from_file(path)
    assert reread.access_token == "re-paired-token"
    assert reread.paired_as == expected_on_file
    assert reread.service_name(unpaired="instrument-agent") == expected_on_file


def test_settings_from_a_broken_config_toml_say_so(tmp_path):
    path = tmp_path / config.CONFIG_FILENAME
    path.write_text("[file-agent\nhost = ", encoding="utf-8")

    with pytest.raises(config.ConfigError, match="TOML syntax error"):
        AgentSettings.from_file(str(path))
