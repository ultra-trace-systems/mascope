"""
A raw Orbitrap acquisition that exists only as its scans, and the sample it
is read as.

A plain module rather than conftest.py content, so the tests can import it:
``from conftest import ...`` binds to whichever conftest pytest loaded last,
which in a run spanning several test directories is another directory's.
"""

import numpy as np

from mascope_thermo.streams import scan_stream_keys
from mascope_thermo.thermo import NoScansFoundError, UnknownStreamError


SAMPLE_FILENAME = "OrbiTest_1001.01.01_12h00m00s_TestFile"


class ScriptedAcquisition:
    """A raw Orbitrap acquisition that exists only as its scans.

    The reader ``mascope_signal`` reads a raw file through, scripted: each
    scan is its filter, its scan event and the centroids it recorded, one
    second after the scan before. No committed file holds more than one scan
    stream, and the files that do cannot be committed, so this is what the
    per-stream behaviour is tested on. It selects a stream the way the real
    backends do - by the key ``mascope_thermo.streams`` gives each scan, and
    refusing a key no scan carries - so a test cannot pass on a selection
    the real reader would not make.

    The reader's first-scan outlier rule is not scripted: it is the reader's
    own, and its tests are in ``libraries/thermo/tests``.

    :param scans: One ``(filter, event, {m/z: intensity})`` per scan.
    :param microscans: ``{event: count}``, the microscan count the trailers
        of an event's scans report. An event it leaves out reports none.
    """

    RESOLUTION = 120000

    def __init__(self, scans, microscans=None):
        self._scans = list(scans)
        self._microscans = dict(microscans or {})
        self.trailer_reads = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    # -- what the census reads --

    def scan_filters(self):
        return [
            {
                "scan": number,
                "time_s": float(number - 1),
                "filter": text,
                "segment": None if event is None else 1,
                "event": event,
            }
            for number, (text, event, _peaks) in enumerate(self._scans, start=1)
        ]

    def scan_trailer(self, scan_number):
        self.trailer_reads += 1
        return {"FT Resolution:": self.RESOLUTION}

    def acquisition_parameters(self, max_scans=5, scan_numbers=None):
        """What the trailers of these scans agree on: their microscan count,
        where their events were scripted with one and it is the same."""
        counts = {
            self._microscans.get(self._scans[number - 1][1])
            for number in scan_numbers or []
        }
        constant = {}
        if len(counts) == 1 and None not in counts:
            constant["Micro Scan Count:"] = counts.pop()
        return {
            "source": "scripted",
            "scans_sampled": 0,
            "constant": constant,
            "varying": [],
        }

    # -- selection --

    def _selected(self, polarity=None, t_min=None, t_max=None, stream=None):
        # Like the real backends: no key is read unless a stream is asked
        # for, and one the file holds no stream under is refused as such.
        rows = self.scan_filters()
        keys = scan_stream_keys(self) if stream is not None else [None] * len(rows)
        if stream is not None and rows and stream not in keys:
            raise UnknownStreamError(stream, keys)
        selected = []
        for row, key in zip(rows, keys):
            text = row["filter"]
            if " ms " not in f"{text} ":
                continue
            if polarity and f" {polarity} " not in text:
                continue
            if t_min is not None and row["time_s"] < t_min:
                continue
            if t_max is not None and row["time_s"] > t_max:
                continue
            if stream is not None and key != stream:
                continue
            selected.append(row)
        if not selected:
            raise NoScansFoundError(
                f"No scans found: polarity={polarity!r}, stream={stream!r}"
            )
        return selected

    def scan_indices(
        self, polarity=None, t_min=None, t_max=None, ms_type="Ms", stream=None
    ):
        return [row["scan"] for row in self._selected(polarity, t_min, t_max, stream)]

    def scan_times(
        self, polarity=None, t_min=None, t_max=None, ms_type="Ms", stream=None
    ):
        return np.array(
            [row["time_s"] for row in self._selected(polarity, t_min, t_max, stream)]
        )

    # -- data --

    def _peaks(self, scan_number):
        return self._scans[scan_number - 1][2]

    def average_centroids(self, scan_indices, ppm=1, average=False):
        """Every m/z any of the scans recorded, summed over the scans, as the
        real reader returns it with ``average=False``."""
        masses = sorted({mz for n in scan_indices for mz in self._peaks(n)})
        intensities = np.array(
            [sum(self._peaks(n).get(mz, 0.0) for n in scan_indices) for mz in masses]
        )
        if average:
            intensities = intensities / len(scan_indices)
        return (
            np.array(masses, dtype=float),
            intensities,
            np.full(len(masses), float(self.RESOLUTION)),
            np.full(len(masses), 100.0),
        )

    def average_profile(self, scan_indices, ppm=1, average=False):
        """A flat profile over the acquisition's m/z range: nothing here reads
        its shape, only its axis."""
        everything = [mz for _text, _event, peaks in self._scans for mz in peaks]
        axis = np.linspace(min(everything) - 1.0, max(everything) + 1.0, 2000)
        return axis, np.ones_like(axis), len(scan_indices)

    def xic(
        self,
        mzs,
        ppm=5,
        polarity=None,
        t_min=None,
        t_max=None,
        ms_type="Ms",
        stream=None,
    ):
        rows = self._selected(polarity, t_min, t_max, stream)
        out = np.zeros((len(mzs), len(rows)))
        for j, row in enumerate(rows):
            for mz, intensity in self._peaks(row["scan"]).items():
                for i, target in enumerate(mzs):
                    if abs(mz - target) <= target * ppm / 1e6:
                        out[i, j] += intensity
        return out, np.array([row["time_s"] for row in rows])
