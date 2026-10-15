"""The settings an agent runs on, as one typed object.

``config.toml`` is read into a plain dict (see :mod:`mascope_file_agent.config`);
:class:`AgentSettings` is that dict with its keys named, which is what
:class:`mascope_file_agent.Agent` is built from. A program that embeds the
agent reads a File Agent ``config.toml`` with :meth:`AgentSettings.from_file`,
or fills the fields in itself.
"""

import os
from dataclasses import asdict, dataclass, fields
from typing import Callable, Mapping

import mascope_sdk
from mascope_file_agent.config import (
    DEFAULT_SETTINGS,
    SERVICE_NAME,
    ConfigError,
    is_valid_instrument,
    load_user_config,
    merge_settings,
    write_user_config,
)


@dataclass(frozen=True)
class AgentSettings:
    """What an agent needs to know to watch a folder and upload from it.

    The fields are the keys of the ``[file-agent]`` section of ``config.toml``,
    with the same meaning and the same defaults.
    """

    #: Mascope server address, with or without a scheme.
    host: str
    #: This machine's device token, obtained by pairing.
    access_token: str
    #: Folder to watch for new data files.
    source: str
    #: Name of the instrument this machine watches. The server files the
    #: uploads under it, and an agent does not start without one.
    instrument: str = DEFAULT_SETTINGS["instrument"]
    #: Pattern of the files to upload.
    mask: str = DEFAULT_SETTINGS["mask"]
    #: Seconds a file must have been left alone before it is uploaded.
    timeout: float = DEFAULT_SETTINGS["timeout"]
    #: Whether subfolders of ``source`` are watched too.
    recursive: bool = DEFAULT_SETTINGS["recursive"]
    #: Whether the server's TLS certificate is verified.
    verify_tls: bool = DEFAULT_SETTINGS["verify_tls"]
    #: IANA timezone of this machine; empty to detect it.
    timezone: str = DEFAULT_SETTINGS["timezone"]
    #: Text added in front of each file name on upload.
    filename_prefix: str = DEFAULT_SETTINGS["filename_prefix"]
    #: Text added behind each file name's stem on upload.
    filename_suffix: str = DEFAULT_SETTINGS["filename_suffix"]
    #: The service ``access_token`` was paired as, saved with every token;
    #: empty for a token saved before it was recorded. See :meth:`service_name`.
    paired_as: str = DEFAULT_SETTINGS["paired_as"]

    @classmethod
    def from_mapping(cls, settings: Mapping) -> "AgentSettings":
        """Build the settings from a ``[file-agent]`` section or the like.

        Missing and empty keys take the built-in defaults and unknown keys are
        dropped, exactly as when ``config.toml`` is loaded.

        :param settings: The settings by name
        :type settings: Mapping
        :return: The settings
        :rtype: AgentSettings
        """
        merged = merge_settings(dict(settings))
        return cls(**{field.name: merged[field.name] for field in fields(cls)})

    @classmethod
    def from_file(cls, config_path: str) -> "AgentSettings":
        """Read the settings from a File Agent ``config.toml``.

        :param config_path: Full path of the file
        :type config_path: str
        :return: The settings
        :rtype: AgentSettings
        :raises ConfigError: If the file is not valid TOML
        :raises OSError: If the file cannot be read
        """
        return cls.from_mapping(load_user_config(config_path))

    def service_name(self, unpaired: str = SERVICE_NAME) -> str:
        """The service name to give ``identity()`` for this machine.

        The server accepts a token under the service it was paired as and no
        other, so a machine that holds one stays that service, whatever a
        program would pair a new machine as: the name pairing recorded, or
        ``file-agent`` for a token paired before the name was recorded (a
        File Agent's own, or one a program took over from it). Only a machine
        with no token yet pairs as ``unpaired``.

        :param unpaired: The service to pair as when there is no token yet
        :type unpaired: str
        :return: The service name
        :rtype: str
        """
        if self.paired_as:
            return self.paired_as
        if self.access_token:
            return SERVICE_NAME
        return unpaired

    def validate(self) -> None:
        """Refuse settings an agent cannot run on.

        :raises ConfigError: When the instrument name is missing or not valid,
            or the watched folder does not exist
        """
        validate_instrument(self.instrument)
        if not os.path.isdir(self.source):
            raise ConfigError(f"The watched folder does not exist: {self.source}")

    def token_writer(self, config_path: str) -> Callable[[str], None]:
        """A function that saves a new access token to ``config.toml``.

        Pass it to :class:`mascope_file_agent.Agent` as ``persist_token`` so
        that a renewed token survives a restart. It rewrites the whole file
        from these settings, so it belongs to a file that holds nothing but
        the ``[file-agent]`` section.

        Each token is saved with the service name ``identity()`` set, as
        ``paired_as``: the agent obtains a token only under that name, by a
        renewal the server grants under the name the token was paired as or
        by a re-pair under it. So a re-pair under a name other than the one
        recorded at the start is recorded as what it is.

        :param config_path: Full path of the file
        :type config_path: str
        :return: Writes the file with the token it is given
        :rtype: Callable[[str], None]
        """
        values = asdict(self)

        def write(token: str) -> None:
            values["access_token"] = token
            values["paired_as"] = mascope_sdk.SERVICE_NAME
            write_user_config(config_path, values)

        return write


def validate_instrument(instrument: str | None) -> None:
    """Refuse an instrument name the server would not accept.

    Refused rather than ignored the way an unresolvable timezone is: the
    server files this machine's uploads under this name, so a missing or wrong
    one misfiles data instead of merely degrading a timestamp.

    :param instrument: The configured name, possibly empty
    :type instrument: str | None
    :raises ConfigError: When the instrument name is missing or not valid
    """
    instrument = (instrument or "").strip()
    if not instrument:
        raise ConfigError(
            "No instrument name is configured, and the agent needs one: the "
            "server files this machine's uploads under it. Set 'instrument' in "
            "the agent configuration (letters, digits and hyphens, for example "
            "Orbi-Lab2), or start the agent with --setup to enter it in the "
            "guided setup."
        )
    if not is_valid_instrument(instrument):
        raise ConfigError(
            f"The instrument name is not valid: {instrument!r}\n"
            "Use letters, digits and hyphens only (for example Orbi-Lab2). "
            "Start the agent with --setup to fix it in the guided setup."
        )
