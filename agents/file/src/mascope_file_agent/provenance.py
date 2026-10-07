"""What goes with an upload to say where the file came from.

Two things, to a server that announces it keeps them. The file's SHA-256, so
that the file the server holds can be told to be the file the instrument
wrote. And its acquisition record, when the program that ran the acquisition
left one beside it: ``<file>.mascope.json``, which says which step of which
run the file belongs to and under which chemistry
(:mod:`mascope_sdk.acquisition`).

**Neither may cost a file its upload.** A record that cannot be used is left
behind with a line in the log and the file goes without it; a server that
keeps neither is sent neither.
"""

import hashlib
import os
from threading import Lock

from mascope_file_agent.capabilities import ServerCapabilities
from mascope_sdk import acquisition
from mascope_sdk.exceptions import MascopeConnectionError


def file_sha256(path: str) -> str:
    """The SHA-256 of a file's content, as lowercase hex.

    :param path: Path of the file
    :type path: str
    :rtype: str
    """
    with open(path, "rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


class UploadProvenance:
    """Find what goes with each upload, and whether the server takes it.

    :param server: Asked what the server can do.
    :param logger: Where the lines go.
    """

    def __init__(self, server: ServerCapabilities, logger):
        self._server = server
        self._logger = logger
        self._lock = Lock()
        # Sidecars already said to be unusable, so that the retries of one
        # upload say it once.
        self._unusable: set[str] = set()
        # Whether the log has said that the server keeps no records.
        self._said_not_kept = False

    def for_upload(self, filepath: str) -> tuple[bytes | None, str | None]:
        """The acquisition record and the hash to send with a file.

        :param filepath: Full path of the file about to be uploaded
        :type filepath: str
        :raises MascopeConnectionError: When the file has a record and the
            server could not be asked whether it keeps one. The upload is
            then tried again later, as when the server cannot be reached,
            rather than made without the record.
        :return: The record's document and the file's SHA-256; None for each
            that is not to be sent
        :rtype: tuple[bytes | None, str | None]
        """
        name = os.path.basename(filepath)
        record = self._record(filepath)
        kept = self._server.has(acquisition.CAPABILITY)
        if kept is None:
            if record is not None:
                raise MascopeConnectionError(
                    "The server could not be asked whether it keeps the "
                    f"acquisition record of {name}."
                )
            return None, None
        if not kept:
            if record is not None:
                self._say_not_kept(name)
            return None, None
        return record, file_sha256(filepath)

    def _record(self, filepath: str) -> bytes | None:
        """The document of the file's sidecar, when it has one that can be used."""
        try:
            sidecar = acquisition.read_sidecar(filepath)
        except acquisition.AcquisitionError as unusable:
            with self._lock:
                said = filepath in self._unusable
                self._unusable.add(filepath)
            if not said:
                self._logger.warning(
                    f"{os.path.basename(filepath)}: the acquisition record "
                    f"beside it is not sent, as {unusable}. The file is "
                    "uploaded without it."
                )
            return None
        return None if sidecar is None else sidecar.document

    def _say_not_kept(self, name: str) -> None:
        """Say, once, that this server keeps no acquisition records."""
        with self._lock:
            said, self._said_not_kept = self._said_not_kept, True
        if not said:
            self._logger.warning(
                f"{name} has an acquisition record beside it, and the server "
                "does not keep them: this file is uploaded without its record, "
                "and so are the ones after it until the server is updated."
            )
