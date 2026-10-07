"""The ``mascope-file-agent`` program: settings, runtime, then the agent.

Everything that watches and uploads is :class:`mascope_file_agent.Agent`.
What is here is what only the console program does: find the settings (and
run the guided setup when they are missing), start the Mascope runtime that
carries the log, and run one agent until it is interrupted.
"""

import os
import sys
from dataclasses import fields

from mascope_file_agent import __version__
from mascope_file_agent import config as agent_config
from mascope_file_agent.agent import SERVICE_NAME, Agent, identity
from mascope_file_agent.config import ConfigError
from mascope_file_agent.credentials import ConsoleRepair
from mascope_file_agent.settings import AgentSettings, validate_instrument
from mascope_file_agent.uploader import mkdir
from mascope_file_agent.wizard import SetupCancelled, run_setup_wizard
from mascope_runtime import Runtime


def resolve_settings(mascope_path: str, env_path: str) -> dict:
    """Load the agent settings, running the guided setup when needed.

    Settings come from the single user-facing ``config.toml`` at the root
    of `mascope_path`. When it is missing, settings from a pre-config.toml
    install are migrated; when required settings are still missing (or the
    agent was started with ``--setup``), the interactive wizard collects
    them and writes the file.

    :param mascope_path: The agent's data directory (MASCOPE_PATH)
    :type mascope_path: str
    :param env_path: Path of the ``.runtime/env/prod`` directory
    :type env_path: str
    :return: Complete, validated settings dict
    :rtype: dict
    :raises ConfigError: When settings are missing and the wizard cannot run,
        or the watched folder does not exist
    """
    config_path = os.path.join(mascope_path, agent_config.CONFIG_FILENAME)
    if os.path.exists(config_path):
        settings = agent_config.load_user_config(config_path)
    else:
        settings = agent_config.load_legacy_config(env_path)
        if settings:
            agent_config.write_user_config(config_path, settings)
            print(f"Migrated existing settings to {config_path}")
        else:
            settings = agent_config.merge_settings({})

    if "--setup" in sys.argv[1:] or agent_config.missing_settings(settings):
        if not (sys.stdin and sys.stdin.isatty()):
            raise ConfigError(
                "The agent is not configured. Start it in a console to use "
                "the guided setup, or fill in host, access_token and source "
                f"in:\n  {config_path}"
            )
        try:
            settings = run_setup_wizard(settings)
        except SetupCancelled as cancelled:
            # Pairing comes last and needs a second person at a browser, so
            # walking away to find one must not cost the answers given here.
            # They are written without a token, which leaves the settings
            # incomplete, so the next start runs the guided setup again - now
            # with every earlier answer offered as its default.
            agent_config.write_user_config(config_path, cancelled.settings)
            print(f"\nThe answers given so far were saved to {config_path}.")
            raise
        agent_config.write_user_config(config_path, settings)
        print(f"Settings saved to {config_path}\n")

    if not os.path.isdir(settings["source"]):
        raise ConfigError(
            f"The watched folder does not exist: {settings['source']}\n"
            f"Update 'source' in {config_path}, or restart the agent "
            "with --setup to run the guided setup again."
        )
    return settings


def _validate_instrument(runtime) -> None:
    """Refuse to start without an instrument name the server will accept.

    Checked against the loaded runtime config rather than the settings dict,
    so it covers dev mode too, where the CLI owns the configuration and
    :func:`resolve_settings` never runs.

    :param runtime: The loaded runtime
    :raises ConfigError: When the instrument name is missing or not valid
    """
    validate_instrument(getattr(runtime.config, "instrument", ""))


def initialize() -> tuple:
    """Initialize the application and runtime depending on dev/prod mode

    If in prod mode, check if runtime directory structure exists, and create if not.

    :return: The runtime, then the path of the user-facing ``config.toml``
        and the settings read from it - both None in dev mode, where the CLI
        owns the configuration
    :rtype: tuple
    :raises ConfigError: When the settings are missing, or not ones the agent
        can run on
    """
    config_path = settings = None
    # check if we are running in a pyinstaller bundle
    bundled = getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")
    if bundled:
        # prod mode
        # set MASCOPE_PATH as %AppData%\Mascope\FileAgent
        mascope_path = mkdir(os.environ["APPDATA"], "Mascope", "FileAgent")
        os.environ.setdefault("MASCOPE_PATH", mascope_path)
        # setup runtime environment
        env_path = mkdir(mascope_path, ".runtime", "env", "prod")
        mkdir(mascope_path, "logs")
        # resolve user settings (guided setup on first run) and regenerate
        # the runtime-format config the mascope_runtime loader reads
        settings = resolve_settings(mascope_path, env_path)
        agent_config.write_runtime_config(env_path, settings, mascope_path)
        # Where the config lives, so a rotated token can be written back to it.
        config_path = os.path.join(mascope_path, agent_config.CONFIG_FILENAME)
        # initialize the runtime in production mode
        runtime = Runtime("file-agent", env="prod", mode="prod", path=mascope_path)
    else:
        # dev mode
        # runtime state inherited from the CLI
        runtime = Runtime("file-agent")
    # After the runtime exists, so the refusal reaches the agent log as well as
    # the console - a headless prod install has no console to read.
    try:
        _validate_instrument(runtime)
    except ConfigError as e:
        runtime.logger.error(f"Configuration error: {e}")
        raise
    return runtime, config_path, settings


def pause_before_exit() -> None:
    """Keep the console window open so double-click users can read the error."""
    if getattr(sys, "frozen", False) and sys.stdin and sys.stdin.isatty():
        try:
            input("Press Enter to exit...")
        except EOFError:
            pass


def build_agent(
    runtime, config_path: str | None = None, user_settings: dict | None = None
) -> Agent:
    """The agent this program runs, from the loaded runtime.

    :param runtime: The loaded runtime, whose config holds the settings
    :param config_path: Path of the user-facing ``config.toml`` a rotated
        token is written back to; None in dev mode
    :type config_path: str | None
    :param user_settings: The settings as ``config.toml`` holds them; None in
        dev mode
    :type user_settings: dict | None
    :return: The agent, not started
    :rtype: Agent
    :raises RuntimeError: When no server or no watched folder is configured
    """
    host = runtime.config.host
    match runtime.mode:
        case "dev":
            url = f"http://{host}:{runtime.meta.api_port}"
        case "prod":
            # https unless the host is configured with an explicit scheme
            url = agent_config.base_url(host) if host else None
        case _:
            url = None
    if not url:
        runtime.logger.error(
            "Mascope host not defined, please check configuration. Exiting..."
        )
        raise RuntimeError("Mascope host not defined, please check configuration.")

    if not os.path.isdir(runtime.config.source):
        raise RuntimeError(f"Invalid source directory {runtime.config.source}")

    settings = AgentSettings.from_mapping(
        {
            field.name: getattr(runtime.config, field.name, None)
            for field in fields(AgentSettings)
        }
    )
    persist_token = None
    if config_path and user_settings is not None:
        # From the settings as the file holds them, not as the runtime loaded
        # them: a rotated token must not rewrite the rest of the user's file.
        persist_token = AgentSettings.from_mapping(user_settings).token_writer(
            config_path
        )
    return Agent(
        settings,
        url,
        logger=runtime.logger,
        repair=ConsoleRepair(),
        persist_token=persist_token,
    )


def run() -> None:
    """Main function of the application

    Build the agent and run it until it is interrupted
    """
    # Before the guided setup, which pairs the machine under this identity.
    identity(SERVICE_NAME, __version__)

    # Initialize runtime
    try:
        runtime, config_path, user_settings = initialize()
    except ConfigError as e:
        print(f"\nConfiguration error:\n{e}\n")
        pause_before_exit()
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nSetup cancelled.")
        sys.exit(1)

    runtime.logger.info(f"Mascope File Agent {__version__}")

    agent = build_agent(runtime, config_path, user_settings)
    # Again, now that the configuration says whether to verify the server's
    # TLS certificate.
    identity(SERVICE_NAME, __version__, verify_tls=agent.settings.verify_tls)
    agent.run_until_complete()


if __name__ == "__main__":
    run()
