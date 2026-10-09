"""Unit tests for the console entry: the agent ``main.run()`` builds and runs.

Hermetic: the runtime is a stand-in holding the configuration the real one
would have loaded, and the agent is built but never run.
"""

from types import SimpleNamespace

import pytest

import mascope_sdk
from mascope_file_agent import Agent, ConsoleRepair, __version__, config, main


class StubLogger:
    def __init__(self):
        self.errors = []

    def error(self, message):
        self.errors.append(message)

    def info(self, message):
        pass


def _runtime(tmp_path, mode="prod", **configured):
    """The loaded runtime, as far as the entry point reads it."""
    return SimpleNamespace(
        mode=mode,
        logger=StubLogger(),
        meta=SimpleNamespace(api_port=8090),
        config=SimpleNamespace(
            **{
                "host": "mascope.example.com",
                "access_token": "tok",
                "source": str(tmp_path),
                "instrument": " Orbi-Lab2 ",
                "mask": "*.d",
                "timeout": 10,
                "recursive": True,
                "verify_tls": False,
                "timezone": "Europe/Helsinki",
                "filename_prefix": None,
                "filename_suffix": "_a",
                **configured,
            }
        ),
    )


@pytest.fixture
def run(monkeypatch):
    """Run the entry point on a given runtime; the agent it would have run."""

    def run(runtime, config_path=None, user_settings=None):
        ran = []
        monkeypatch.setattr(
            main, "initialize", lambda: (runtime, config_path, user_settings)
        )
        monkeypatch.setattr(Agent, "run_until_complete", lambda self: ran.append(self))
        main.run()
        (agent,) = ran
        return agent

    return run


def test_run_builds_the_agent_the_configuration_describes(run, tmp_path):
    runtime = _runtime(tmp_path)

    agent = run(runtime)

    assert agent.url == "https://mascope.example.com"
    assert agent.logger is runtime.logger
    assert agent.settings.source == str(tmp_path)
    assert agent.settings.mask == "*.d"
    assert agent.settings.timeout == 10
    assert agent.settings.recursive is True
    assert agent.settings.verify_tls is False
    assert agent.settings.filename_prefix == ""
    assert agent.settings.filename_suffix == "_a"
    assert agent.watcher.path == str(tmp_path)
    assert agent.watcher.mask == "*.d"
    assert agent.watcher.recursive is True
    assert agent.credentials.current_access_token() == "tok"
    assert agent.credentials.url == "https://mascope.example.com"
    assert agent.credentials.verify_tls is False
    # Reported with each upload, as it was resolved at start.
    assert agent.uploader.instrument == "Orbi-Lab2"
    assert agent.uploader.timezone == "Europe/Helsinki"
    # The console program asks at the console when a credential is refused.
    assert isinstance(agent.credentials.repair, ConsoleRepair)


def test_run_says_who_the_process_is(run, tmp_path):
    """Every request reports the File Agent at this version, and uploads follow
    the configured TLS setting."""
    run(_runtime(tmp_path, verify_tls=False))

    assert mascope_sdk.SERVICE_NAME == "file-agent"
    assert mascope_sdk.AGENT_VERSION == __version__
    assert mascope_sdk.VERIFY_TLS is False

    run(_runtime(tmp_path, verify_tls=True))

    assert mascope_sdk.VERIFY_TLS is True


def test_run_is_identified_before_the_guided_setup_pairs(monkeypatch, tmp_path):
    """Pairing happens while the settings are resolved, under this identity."""
    monkeypatch.setattr(mascope_sdk, "SERVICE_NAME", "mascope_sdk")
    monkeypatch.setattr(mascope_sdk, "AGENT_VERSION", None)
    seen = []

    def initialize():
        seen.append((mascope_sdk.SERVICE_NAME, mascope_sdk.AGENT_VERSION))
        return _runtime(tmp_path), None, None

    monkeypatch.setattr(main, "initialize", initialize)
    monkeypatch.setattr(Agent, "run_until_complete", lambda self: None)

    main.run()

    assert seen == [("file-agent", __version__)]


def test_a_development_run_talks_to_the_backend_port(run, tmp_path):
    agent = run(_runtime(tmp_path, mode="dev", host="localhost"))

    assert agent.url == "http://localhost:8090"
    # The check at start and a pairing ask there too, not https://localhost.
    assert agent.credentials.url == "http://localhost:8090"


def test_a_production_run_saves_a_new_token_to_config_toml(run, tmp_path):
    """A renewed token has to survive a restart, with the settings beside it.

    The file is rewritten from the settings it held, not from what the runtime
    made of them: the runtime resolves a relative folder to an absolute one,
    and a token renewal is no occasion to rewrite what the user typed.
    """
    config_path = tmp_path / config.CONFIG_FILENAME
    in_the_file = config.merge_settings(
        {
            "host": "mascope.example.com",
            "access_token": "tok",
            "source": "./data",
            "instrument": "Orbi-Lab2",
            "mask": "*.d",
            "recursive": True,
            "verify_tls": False,
            "filename_suffix": "_a",
        }
    )

    agent = run(
        _runtime(tmp_path), config_path=str(config_path), user_settings=in_the_file
    )
    agent.credentials.persist("renewed-token")

    saved = config.load_user_config(str(config_path))
    assert saved == {**in_the_file, "access_token": "renewed-token"}
    # While the agent itself watches the folder the runtime resolved.
    assert agent.settings.source == str(tmp_path)


def test_a_development_run_has_no_config_toml_to_save_to(run, tmp_path):
    """The CLI owns the configuration there; the agent writes none of its own."""
    agent = run(_runtime(tmp_path, mode="dev", host="localhost"))

    agent.credentials.persist("renewed-token")

    assert not (tmp_path / config.CONFIG_FILENAME).exists()


def test_run_refuses_a_configuration_without_a_host(monkeypatch, tmp_path):
    runtime = _runtime(tmp_path, host="")
    monkeypatch.setattr(main, "initialize", lambda: (runtime, None, None))

    with pytest.raises(RuntimeError, match="Mascope host not defined"):
        main.run()

    assert runtime.logger.errors == [
        "Mascope host not defined, please check configuration. Exiting..."
    ]


def test_run_refuses_a_watched_folder_that_is_not_there(monkeypatch, tmp_path):
    runtime = _runtime(tmp_path, source=str(tmp_path / "gone"))
    monkeypatch.setattr(main, "initialize", lambda: (runtime, None, None))

    with pytest.raises(RuntimeError, match="Invalid source directory"):
        main.run()


def test_run_exits_on_a_configuration_error(monkeypatch, capsys):
    def refuse():
        raise main.ConfigError("The agent is not configured.")

    monkeypatch.setattr(main, "initialize", refuse)

    with pytest.raises(SystemExit) as exit_:
        main.run()

    assert exit_.value.code == 1
    assert (
        "Configuration error:\nThe agent is not configured." in capsys.readouterr().out
    )


def test_a_refused_instrument_reaches_the_agent_log(monkeypatch):
    """A headless install has the log and no console to read the refusal on."""
    runtime = SimpleNamespace(
        logger=StubLogger(), config=SimpleNamespace(instrument="orbi lab 2")
    )
    monkeypatch.setattr(main, "Runtime", lambda *a, **k: runtime)

    with pytest.raises(main.ConfigError, match="letters, digits and hyphens"):
        main.initialize()

    assert len(runtime.logger.errors) == 1
    assert runtime.logger.errors[0].startswith(
        "Configuration error: The instrument name is not valid"
    )
