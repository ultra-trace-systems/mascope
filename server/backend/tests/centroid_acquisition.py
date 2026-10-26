"""The committed MS1 file, read as an acquisition that stored centroids alone.

An Orbitrap method can store each scan's centroids without its profile: the
filter then reads ``c`` where it reads ``p`` otherwise, and the file holds no
profile to average. No committed file was acquired that way, and the files
that were cannot be committed, so the committed MS1 file is read as one: the
OpenTFRaw backend's scan list gives every scan a ``c`` filter, and every
scan's profile reads as empty, as OpenTFRaw reads a centroid scan's. The
centroids are the file's own, so each scan keeps the m/z, intensity,
resolution and signal-to-noise its instrument reported for it.

Importable by the library tests too (``thermo_test_support``): nothing here
imports the backend.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import numpy as np

from mascope_thermo.backend import OpenTFRawBackend


#: The committed MS1 file the acquisition is read from.
SOURCE_FILE = (
    Path(__file__).resolve().parents[3]
    / "libraries/thermo/tests/test_files/KORBI2_AMB_POS_20260109174345.raw"
)

#: A profile scan's data type in its filter, after the analyzer and polarity.
_PROFILE = re.compile(r"^(\S+ [+-] )p ")


def as_centroid_filter(text: str) -> str:
    """A profile scan's filter, as the same scan stored as centroids reads.

    :param text: A scan filter, e.g. ``FTMS + p NSI Full ms [40.0000-500.0000]``.
    :return: The filter with ``c`` for ``p``; any other filter unchanged.
    """
    return _PROFILE.sub(r"\1c ", text or "")


@contextmanager
def presented() -> Iterator[Path]:
    """Read the committed MS1 file, and any copy of it, as centroid scans.

    Every file the OpenTFRaw backend opens while this is entered reads so,
    so a copy the converter makes in its filestore does too; the backend is
    chosen for the duration.

    :return: The path of the committed file.
    """
    all_scans = OpenTFRawBackend._all_scans

    def as_centroids(self):
        return [
            {
                **scan,
                "filter_string": as_centroid_filter(scan["filter_string"]),
                "scan_mode": "centroid",
            }
            for scan in all_scans(self)
        ]

    def no_profile(self, scan_number):
        return np.array([], dtype=np.float64), np.array([], dtype=np.float64)

    with (
        patch.dict(os.environ, {"MASCOPE_THERMO_BACKEND": "opentfraw"}),
        patch.object(OpenTFRawBackend, "_all_scans", as_centroids),
        patch.object(OpenTFRawBackend, "_profile", no_profile),
    ):
        yield SOURCE_FILE
