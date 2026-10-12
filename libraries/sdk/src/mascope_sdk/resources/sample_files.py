"""Sample files resource for the Mascope SDK."""

from collections.abc import Iterable
from typing import Any

import pandas as pd
from loguru import logger

from ._base import BaseResource


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
        other raises :class:`~mascope_sdk.exceptions.NotFoundError`.

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
        :raises NotFoundError: If the file holds no raw Orbitrap data.
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
