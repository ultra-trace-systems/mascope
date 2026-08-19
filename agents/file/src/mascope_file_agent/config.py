"""Configuration handling for the bundled (production) File Agent.

The user edits exactly one file: ``config.toml`` at the root of the agent's
data directory (``%APPDATA%\\Mascope\\FileAgent``). On every start the agent
merges that file over built-in defaults and regenerates the runtime-format
config (``.runtime/env/prod/prod.mascope.toml``) that the mascope_runtime
config loader expects. The nested runtime file is an implementation detail;
users never need to touch it, and schema changes are absorbed by the merge
instead of requiring a config reset.
"""

import contextlib
import os
import tempfile
import time
import tomllib
from urllib.parse import urlsplit


CONFIG_FILENAME = "config.toml"

#: Windows keeps a file locked while another process has it open, so the
#: atomic replace in write_user_config retries briefly before giving up.
_REPLACE_ATTEMPTS = 5
_REPLACE_RETRY_DELAY_S = 0.2

#: Settings the agent cannot run without.
REQUIRED_SETTINGS = ("host", "access_token", "source")

#: Built-in defaults for the optional settings.
DEFAULT_SETTINGS = {
    "mask": "*.raw",
    "timeout": 3,
    "recursive": False,
    # TLS verification is on by default; a self-signed or plain-HTTP dev
    # deployment turns it off. Kept as a real boolean so False survives the
    # merge (an empty string would fall back to the default).
    "verify_tls": True,
    # IANA timezone of this instrument PC, sent with each upload so the
    # converter can resolve the acquisition time to UTC. Empty means
    # auto-detect from the operating system.
    "timezone": "",
    "filename_prefix": "",
    "filename_suffix": "",
}

USER_CONFIG_HEADER = """\
# Mascope File Agent configuration
#
#   host         - Mascope server address, e.g. "mascope.example.com"
#   access_token - API access token. To create one: log in to Mascope in the
#                  browser, click your profile icon to open the sidebar, and
#                  under "API Access Tokens" generate a "File Agent" token
#                  (editor role or higher required).
#   source       - full path of the folder to watch for new data files
#   mask         - pattern of the files to upload, e.g. "*.raw"
#   recursive    - true to also watch subfolders of source (the agent's own
#                  failed_uploads folder is always excluded)
#   verify_tls   - true to verify the server's TLS certificate (the default);
#                  set false only for a self-signed or plain-HTTP dev server
#   timezone     - IANA timezone of this machine, e.g. "Europe/Helsinki",
#                  reported with each upload so acquisition times are stored
#                  in UTC correctly. Leave empty to detect it automatically;
#                  set it when detection is wrong, which happens because
#                  Windows names a group of zones rather than a city
#
# Restart the agent after changing this file. To re-run the guided setup,
# start the agent with the --setup flag.
"""


class ConfigError(Exception):
    """A configuration problem the user must fix; message is user-facing."""


def toml_str(value: str) -> str:
    """Format a Python string as a TOML string value.

    Prefers literal (single-quoted) strings so Windows paths need no
    escaping; falls back to a basic string with escapes when the value
    contains a single quote or control characters.

    :param value: The string to format
    :type value: str
    :return: A TOML representation including quotes
    :rtype: str
    """
    if "'" not in value and not any(ord(c) < 32 for c in value):
        return f"'{value}'"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    for char, escape in (("\n", "\\n"), ("\r", "\\r"), ("\t", "\\t")):
        escaped = escaped.replace(char, escape)
    return f'"{escaped}"'


def normalize_host(raw: str) -> str:
    """Normalize a user-entered server address.

    Accepts bare hostnames, full URLs and addresses with trailing slashes
    or paths. An explicit ``http://`` scheme is preserved (for plain-HTTP
    servers); ``https://`` is stripped since it is the production default.

    :param raw: The address as typed by the user
    :type raw: str
    :return: A normalized host, e.g. ``mascope.example.com``
    :rtype: str
    """
    host = raw.strip()
    if not host:
        return ""
    if "://" in host:
        parts = urlsplit(host)
        if parts.scheme.lower() == "http":
            return f"http://{parts.netloc}"
        return parts.netloc
    return host.split("/")[0].rstrip()


def base_url(host: str) -> str:
    """Build the server base URL from a configured host.

    :param host: Configured host, with or without an explicit scheme
    :type host: str
    :return: Base URL, defaulting to https when no scheme is given
    :rtype: str
    """
    if host.startswith(("http://", "https://")):
        return host
    return f"https://{host}"


def merge_settings(user_settings: dict) -> dict:
    """Overlay user settings on the built-in defaults.

    Unknown keys are dropped, so removed settings never break an upgrade;
    missing keys fall back to defaults, so new settings never require a
    config reset.

    :param user_settings: The ``[file-agent]`` section of the user config
    :type user_settings: dict
    :return: Complete settings dict
    :rtype: dict
    """
    settings = dict(DEFAULT_SETTINGS)
    for key in (*REQUIRED_SETTINGS, *DEFAULT_SETTINGS):
        value = user_settings.get(key)
        if value not in (None, ""):
            settings[key] = value
    for key in REQUIRED_SETTINGS:
        settings.setdefault(key, "")
    return settings


def missing_settings(settings: dict) -> list[str]:
    """List the required settings that are absent or empty.

    :param settings: Settings dict as returned by :func:`merge_settings`
    :type settings: dict
    :return: Names of missing required settings
    :rtype: list[str]
    """
    return [key for key in REQUIRED_SETTINGS if not settings.get(key)]


def load_user_config(config_path: str) -> dict:
    """Load and merge the user-facing ``config.toml``.

    :param config_path: Full path of the user config file
    :type config_path: str
    :return: Complete settings dict
    :rtype: dict
    :raises ConfigError: If the file is not valid TOML
    """
    try:
        with open(config_path, "rb") as file:
            raw = tomllib.load(file)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(
            f"Could not read the configuration file:\n  {config_path}\n"
            f"TOML syntax error: {e}\n"
            "Fix the file, or delete it and restart the agent to run "
            "the guided setup again."
        ) from e
    return merge_settings(raw.get("file-agent", {}))


def load_legacy_config(env_path: str) -> dict | None:
    """Read settings from a pre-config.toml install, if one exists.

    Earlier agent versions had users edit
    ``.runtime/env/prod/prod.mascope.toml`` directly. If that file exists
    and was actually configured (a token was filled in), its settings are
    carried over so existing installs upgrade without re-entering anything.

    :param env_path: Path of the ``.runtime/env/prod`` directory
    :type env_path: str
    :return: Settings dict, or None if there is nothing to migrate
    :rtype: dict | None
    """
    legacy_path = os.path.join(env_path, "prod.mascope.toml")
    if not os.path.exists(legacy_path):
        return None
    try:
        with open(legacy_path, "rb") as file:
            raw = tomllib.load(file)
    except tomllib.TOMLDecodeError:
        return None
    section = raw.get("file-agent", {})
    if not section.get("access_token"):
        return None  # never configured; just an initialized template
    return merge_settings(section)


def write_user_config(config_path: str, settings: dict) -> None:
    """Write the user-facing ``config.toml``.

    :param config_path: Full path of the user config file
    :type config_path: str
    :param settings: Complete settings dict
    :type settings: dict
    """
    lines = [
        USER_CONFIG_HEADER,
        "[file-agent]",
        f"host = {toml_str(settings['host'])}",
        f"access_token = {toml_str(settings['access_token'])}",
        f"source = {toml_str(settings['source'])}",
        f"mask = {toml_str(settings['mask'])}",
        f"recursive = {'true' if settings['recursive'] else 'false'}",
        f"verify_tls = {'true' if settings.get('verify_tls', True) else 'false'}",
        f"timeout = {settings['timeout']}",
    ]
    for key in ("timezone", "filename_prefix", "filename_suffix"):
        if settings.get(key):
            lines.append(f"{key} = {toml_str(settings[key])}")
        else:
            lines.append(f"# {key} = ''")

    # Written to a temporary file and moved into place, so an interrupted write
    # cannot leave a truncated config. Token renewal rewrites this file
    # unattended on a running instrument PC, where a power cut mid-write would
    # otherwise cost the machine its credential and its settings, and the agent
    # would come back up needing a full on-site re-pair. os.replace is atomic
    # within a directory on both POSIX and Windows.
    directory = os.path.dirname(os.path.abspath(config_path)) or "."
    os.makedirs(directory, exist_ok=True)
    handle, temp_path = tempfile.mkstemp(
        dir=directory, prefix=".config.", suffix=".toml.tmp"
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            file.write("\n".join(lines) + "\n")
            file.flush()
            os.fsync(file.fileno())
        # On Windows the replace fails while anything else holds the file open -
        # an editor, an indexer, a virus scanner mid-scan - and those handles are
        # transient. Retrying briefly turns "the rotated token was not saved"
        # into a pause, which matters because the unsaved token is what the
        # agent needs at its next restart.
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                os.replace(temp_path, config_path)
                break
            except PermissionError:
                if attempt == _REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(_REPLACE_RETRY_DELAY_S)
    except BaseException:
        # Leave the existing config untouched and take the partial file with us.
        with contextlib.suppress(OSError):
            os.unlink(temp_path)
        raise


def write_runtime_config(env_path: str, settings: dict, mascope_path: str) -> None:
    """Generate the runtime-format config consumed by mascope_runtime.

    Regenerated on every start from ``config.toml``; never edited by hand.
    Paths are written absolute so nothing resolves into the nested runtime
    env directory (logs land in ``<mascope_path>/logs``, visible next to
    the user config).

    :param env_path: Path of the ``.runtime/env/prod`` directory
    :type env_path: str
    :param settings: Complete settings dict
    :type settings: dict
    :param mascope_path: The agent's data directory (MASCOPE_PATH)
    :type mascope_path: str
    """
    log_path = os.path.join(mascope_path, "logs")
    lines = [
        "# GENERATED FILE - DO NOT EDIT",
        f"# Regenerated on every agent start from {CONFIG_FILENAME} in",
        f"# {mascope_path}",
        "",
        "[meta]",
        "log_level = 'info'",
        "description = 'Mascope File Agent'",
        "",
        "[file-agent]",
        "log_level = 'info'",
        f"log_path = {toml_str(log_path)}",
        "color = 'purple'",
        f"mask = {toml_str(settings['mask'])}",
        f"timeout = {settings['timeout']}",
        f"source = {toml_str(settings['source'])}",
        f"recursive = {'true' if settings['recursive'] else 'false'}",
        f"verify_tls = {'true' if settings.get('verify_tls', True) else 'false'}",
        f"host = {toml_str(settings['host'])}",
        f"access_token = {toml_str(settings['access_token'])}",
    ]
    # The agent reads its settings from this generated file, not from
    # config.toml, so anything the user can set has to be carried across here
    # too - a key written to config.toml but missing from this list is silently
    # inert.
    for key in ("timezone", "filename_prefix", "filename_suffix"):
        if settings.get(key):
            lines.append(f"{key} = {toml_str(settings[key])}")
    runtime_config_path = os.path.join(env_path, "prod.mascope.toml")
    with open(runtime_config_path, "w", encoding="utf-8") as file:
        file.write("\n".join(lines) + "\n")
