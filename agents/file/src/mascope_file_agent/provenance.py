"""What goes with an upload to say where the file came from.

Two things, to a server that announces it keeps them. The file's SHA-256, so
that the file the server holds can be told to be the file the instrument
wrote. And its acquisition record, when the program that ran the acquisition
left one beside it: ``<file>.mascope.json``, which says which step of which
run the file belongs to and under which chemistry
(:mod:`mascope_sdk.acquisition`).

**Neither may cost a file its upload.** A record that cannot be used is left
behind with a line in the log and the file goes without it, whatever it was
that reading it raised; a server that keeps neither is sent neither.

**A file is hashed once for an upload**, however many attempts the upload
takes: an outage is ten attempts, and a file of a few GB read whole for each
would be the instrument computer's disk kept busy for nothing.
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
        # The hash of each file being uploaded, with the size and the time of
        # last change it was taken at, so that the retries of one upload hash
        # it once. Both are dropped when the upload is over (forget).
        self._hashed: dict[str, tuple[tuple[int, int], str]] = {}
        # What the log has said, once each, of a server that keeps no records.
        self._said: set[str] = set()

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
                self._say_not_kept(name, refused=self._server.refused)
            return None, None
        return record, self._sha256(filepath)

    def forget(self, filepath: str) -> None:
        """Drop what was kept for a file's upload, now that it is over.

        :param filepath: Full path of the file
        :type filepath: str
        """
        with self._lock:
            self._hashed.pop(filepath, None)
            self._unusable.discard(filepath)

    def _sha256(self, filepath: str) -> str:
        """The file's hash, taken once for as long as the file stays as it is."""
        stat = os.stat(filepath)
        state = (stat.st_size, stat.st_mtime_ns)
        with self._lock:
            hashed = self._hashed.get(filepath)
        if hashed is not None and hashed[0] == state:
            return hashed[1]
        digest = file_sha256(filepath)
        with self._lock:
            self._hashed[filepath] = (state, digest)
        return digest

    def _record(self, filepath: str) -> bytes | None:
        """The document of the file's sidecar, when it has one that can be used."""
        try:
            sidecar = acquisition.read_sidecar(filepath)
        except Exception as raised:
            # Whatever it is. The faults a record can have are an
            # AcquisitionError, and anything else is a fault in reading one:
            # let out of here, it would be taken for an upload that failed,
            # and the file set aside without one upload having been made.
            why = (
                f"as {raised}"
                if isinstance(raised, acquisition.AcquisitionError)
                else f"as reading it failed ({type(raised).__name__}: {raised})"
            )
            with self._lock:
                said = filepath in self._unusable
                self._unusable.add(filepath)
            if not said:
                self._logger.warning(
                    f"{os.path.basename(filepath)}: the acquisition record "
                    f"beside it is not sent, {why}. The file is uploaded "
                    "without it."
                )
            return None
        return None if sidecar is None else sidecar.document

    def _say_not_kept(self, name: str, refused: bool) -> None:
        """Say, once, why a file's record is not sent to this server.

        :param name: The file's name
        :param refused: Whether the server refused to say what it keeps, as
            against saying and not keeping records
        """
        said = "refused" if refused else "not kept"
        with self._lock:
            before = said in self._said
            self._said.add(said)
        if before:
            return
        if refused:
            # A server that predates the question, or one that refused this
            # machine's credential. The second is not a server to update, and
            # the upload's own refusal is what gets the machine paired again.
            self._logger.warning(
                f"{name} has an acquisition record beside it, and the server "
                "refused to say whether it keeps them: it is too old to be "
                "asked, or it refused this machine's credential. This attempt "
                "to upload the file goes without its record."
            )
            return
        self._logger.warning(
            f"{name} has an acquisition record beside it, and the server "
            "does not keep them: this file is uploaded without its record, "
            "and so are the ones after it until the server is updated."
        )
