"""Sample files resource for the Mascope SDK."""

import os
from collections.abc import Iterable
from email.message import Message
from pathlib import Path
from typing import Any

import pandas as pd
from loguru import logger
from requests.exceptions import RequestException

from ..exceptions import MascopeConnectionError
from ._base import BaseResource


#: How much of a downloaded file is held in memory at once.
_DOWNLOAD_CHUNK_BYTES = 1024 * 1024


def _attachment_name(content_disposition: str | None) -> str | None:
    """The file name a ``Content-Disposition`` header gives, without a path.

    Both forms are read: ``filename="..."`` and the RFC 5987
    ``filename*=utf-8''...`` a server sends for a name that is not ASCII.

    :param content_disposition: The header, or None when it was not sent.
    :return: The name's last path component, or None when there is none.
    """
    if not content_disposition:
        return None
    header = Message()
    header["Content-Disposition"] = content_disposition
    name = header.get_filename()
    # The name is the server's, but it is used as a path here: keep only its
    # last component, so it cannot write outside the directory asked for.
    name = os.path.basename((name or "").replace("\\", "/"))
    return name if name not in ("", ".", "..") else None


class SampleFilesResource(BaseResource):
    """Resource for sample file operations.

    A sample file is the acquisition a sample was cut from, as the instrument
    wrote it. A sample names its file in ``sample_file_id``
    (``mascope.samples.get(sample_id)["sample_file_id"]``).

    Example::

        from mascope_sdk import MascopeClient

        mascope = MascopeClient(workspace="My Workspace")

        file_id = mascope.samples.get("sample-456")["sample_file_id"]
        scans = mascope.sample_files.get_scans(
            file_id, fields=["FT Resolution:", "ScanType"]
        )
    """

    def get_scans(
        self,
        sample_file_id: str,
        *,
        fields: Iterable[str] | None = None,
        first_scan: int | None = None,
        last_scan: int | None = None,
        t_min: float | None = None,
        t_max: float | None = None,
        polarity: str | None = None,
    ) -> pd.DataFrame | None:
        """Get the scan table of a sample file: one row per scan, without its
        centroids.

        A row holds the scan's statistics (``ScanNumber``, ``StartTime`` in
        minutes, ``TIC``, ``ScanType``, ``MsType``, ...) and its trailer under
        the instrument's own labels, colon included (``"FT Resolution:"``,
        ``"Conversion Parameter B:"``). The table describes the file: scans of
        every MS order are in it and none is left out, so narrow it by
        ``MsType`` for the MS1 scans alone. No peak is read for it, which
        keeps it small and quick for a file whose centroids run to hundreds
        of megabytes.

        Only a file whose raw Orbitrap data is stored has a scan table; any
        other is refused, as the other Orbitrap-only reads are, with
        :class:`~mascope_sdk.exceptions.ValidationError`. A server older than
        the route answers any path it does not know with a 404, raised as
        :class:`~mascope_sdk.exceptions.NotFoundError`.

        :param sample_file_id: The ID of the sample file.
        :type sample_file_id: str
        :param fields: The columns to return: statistics fields and trailer
            labels, spelled as the table spells them. ``ScanNumber`` is always
            returned. A name the file does not use is left out, with a
            warning; every column is returned when this is omitted.
        :type fields: Iterable[str], optional
        :param first_scan: Only the scans from this scan number on.
        :type first_scan: int, optional
        :param last_scan: Only the scans up to this scan number.
        :type last_scan: int, optional
        :param t_min: Only the scans starting at this time [s] or later.
        :type t_min: float, optional
        :param t_max: Only the scans starting at this time [s] or earlier.
        :type t_max: float, optional
        :param polarity: Only the scans of this polarity, ``"+"`` or ``"-"``.
        :type polarity: str, optional
        :return: A DataFrame with one row per scan in scan order, or None if
                 no scan matches. ``df.attrs["num_of_scans"]`` is the number
                 of scans in the file, whatever was asked for, and
                 ``df.attrs["stats_per_file"]`` the file's own statistics
                 (instrument model and name among them).
        :rtype: pd.DataFrame | None
        :raises ValidationError: If the file holds no raw Orbitrap data.
        :raises NotFoundError: If the server predates the scan table.
        :raises AuthenticationError: If authentication fails.
        :raises MascopeAPIError: If the API request fails.

        Example::

            scans = mascope.sample_files.get_scans(
                file_id,
                fields=[
                    "Conversion Parameter B:",
                    "Conversion Parameter C:",
                    "FT Resolution:",
                    "MsType",
                ],
                first_scan=1,
                last_scan=100,
            )
            ms1 = scans[scans["MsType"] == "Ms"]
        """
        if isinstance(fields, str):
            fields = [fields]
        elif fields is not None:
            # A query string cannot carry an empty list, so "no column but
            # the scan number" is asked for by naming that column.
            fields = list(fields) or ["ScanNumber"]

        params: dict[str, Any] = {
            name: value
            for name, value in {
                "fields": fields,
                "first_scan": first_scan,
                "last_scan": last_scan,
                "t_min": t_min,
                "t_max": t_max,
                "polarity": polarity,
            }.items()
            if value is not None
        }
        logger.debug("Fetching scans of sample file {}", sample_file_id)
        data = self._get(f"sample/files/{sample_file_id}/scans", params=params)
        if not data or not data.get("stats_per_scan"):
            return None

        df = pd.DataFrame(list(data["stats_per_scan"].values()))
        df.attrs["num_of_scans"] = data.get("num_of_scans")
        df.attrs["stats_per_file"] = (data.get("stats_per_file") or {}).get("Value")

        missing = [name for name in fields or [] if name not in df.columns]
        if missing:
            # WARNING: the frame lacks columns the caller named, and a trailer
            # label written without its colon is the usual reason
            logger.warning(
                "Sample file {} has no scan entry named {}; trailer labels "
                "end with a colon, as in 'FT Resolution:'.",
                sample_file_id,
                ", ".join(repr(name) for name in missing),
            )
        return df

    def download_raw(self, sample_file_id: str, path: str | os.PathLike) -> Path:
        """Download the source data file of a sample file to disk.

        The file is the one the instrument wrote, byte for byte as the server
        stores it: an Orbitrap ``.raw`` or a TOF ``.h5``. It is streamed to
        disk a megabyte at a time, so a file of hundreds of megabytes is never
        held in memory.

        The download is not resumed: a connection lost part way raises, and
        the next call starts again from the first byte. Nothing is left at the
        destination by a download that did not finish - the file is written
        beside it under a ``.part`` name and moved into place once complete.

        A failure before the first byte (the server busy, the account past
        its download budget) is retried as every other read is. The server
        limits how many files one account may download in a while; past that
        it answers 429, raised as
        :class:`~mascope_sdk.exceptions.MascopeAPIError` once the retries are
        spent.

        :param sample_file_id: The ID of the sample file.
        :type sample_file_id: str
        :param path: Where to write the file. An existing directory takes the
            file under the name it was uploaded with; any other path is the
            file itself, replaced if it exists.
        :type path: str | os.PathLike
        :return: The path the file was written to.
        :rtype: pathlib.Path
        :raises NotFoundError: If the sample file does not exist (for an
            administrator), keeps no source data file, or the server predates
            the route.
        :raises AuthenticationError: If authentication fails, or the token's
            user may not read the file.
        :raises MascopeConnectionError: If the connection is lost part way, or
            fewer bytes arrive than the server announced.
        :raises MascopeAPIError: If the API request fails.

        Example::

            file_id = mascope.samples.get(sample_id)["sample_file_id"]
            raw = mascope.sample_files.download_raw(file_id, "downloads/")
        """
        url_path = f"sample/files/{sample_file_id}/raw"
        logger.debug("Downloading the source file of sample file {}", sample_file_id)
        response = self._get(url_path, stream=True)
        with response:
            target = Path(path)
            if target.is_dir():
                name = _attachment_name(response.headers.get("Content-Disposition"))
                target = target / (name or sample_file_id)
            partial = target.with_name(f"{target.name}.part")
            expected = response.headers.get("Content-Length")
            written = 0
            try:
                with open(partial, "wb") as file:
                    for chunk in response.iter_content(_DOWNLOAD_CHUNK_BYTES):
                        file.write(chunk)
                        written += len(chunk)
                if expected is not None and written != int(expected):
                    raise MascopeConnectionError(
                        f"The download of sample file {sample_file_id} stopped "
                        f"after {written} of {expected} bytes",
                        url=response.url,
                    )
            except RequestException as e:
                partial.unlink(missing_ok=True)
                raise MascopeConnectionError(
                    f"The download of sample file {sample_file_id} was cut "
                    f"off after {written} bytes: {e}",
                    url=response.url,
                ) from e
            except BaseException:
                partial.unlink(missing_ok=True)
                raise
        os.replace(partial, target)
        return target
