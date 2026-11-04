"""The share of each averaged peak's per-scan centroids that carry a flag.

``get_centroid_flag_shares`` reads, over the scans ``get_centroids`` averages,
every centroid within half a width of each peak and the flags the instrument
set on them: how the scans averaged into a peak voted. Peak detection reads
the Exception share of each peak from it
(``mascope_tools.alignment.utils.flag_exception_peaks``).

The scans here are scripted: a reader that holds a few scans, each its own
centroids and flags. ``test_centroid_flags`` reads the shares through both
backends, and ``test_backend_parity`` holds the two real readers to the same
shares.
"""

from contextlib import contextmanager

import numpy as np
import pytest

import mascope_thermo.thermo as m_thermo


EXCEPTION = 8
REFERENCE = 16


class _Scans:
    """A reader of scripted scans, each ``{m/z: flags}``.

    It selects every scan unless a time window is asked for (scan ``n`` is at
    ``n - 1`` s), and keeps what each selection asked for.
    """

    def __init__(self, scans):
        self._scans = scans
        self.selections = []
        self.read = []

    def scan_indices(
        self, polarity=None, t_min=None, t_max=None, ms_type="Ms", stream=None
    ):
        self.selections.append(
            dict(polarity=polarity, t_min=t_min, t_max=t_max, stream=stream)
        )
        numbers = range(1, len(self._scans) + 1)
        return [
            n
            for n in numbers
            if (t_min is None or n - 1 >= t_min) and (t_max is None or n - 1 <= t_max)
        ]

    def _centroids(self, number):
        self.read.append(number)
        # Stored as the instrument writes them, not in m/z order
        return list(reversed(list(self._scans[number - 1].items())))

    def centroid_masses(self, number):
        return np.array([mz for mz, _ in self._centroids(number)], dtype=float)

    def centroid_flags(self, number):
        return np.array([flags for _, flags in self._centroids(number)], dtype=int)


@pytest.fixture
def scans(monkeypatch):
    """Open every file as the scripted scans given."""

    def _scans(*per_scan):
        reader = _Scans(list(per_scan))

        @contextmanager
        def scripted(_path):
            yield reader

        monkeypatch.setattr(m_thermo, "open_backend", scripted)
        return reader

    return _scans


def _shares(masses, resolutions, **kwargs):
    return m_thermo.get_centroid_flag_shares(
        "file.raw", np.asarray(masses, float), np.asarray(resolutions, float), **kwargs
    )


def test_a_peak_holds_the_share_of_its_centroids_the_scans_flagged(scans):
    # A lobe at 100.002 flagged in two of three scans, a line at 100.0 never
    scans(
        {100.0: 0, 100.002: EXCEPTION},
        {100.0: 0, 100.002: EXCEPTION},
        {100.0: 0, 100.002: 0},
    )

    shares = _shares([100.0, 100.002], [1e5, 1e5])

    assert shares.tolist() == pytest.approx([0.0, 2 / 3])


def test_the_centroids_are_those_within_half_a_width(scans):
    # A width of 0.01 at m/z 100: the window is 99.995 to 100.005
    scans({99.9949: EXCEPTION, 99.996: 0, 100.004: EXCEPTION, 100.0051: EXCEPTION})

    assert _shares([100.0], [1e4]).tolist() == [0.5]


def test_a_centroid_at_the_edge_of_the_width_is_counted(scans):
    # A width of 0.5: the window is 99.75 to 100.25, both exact floats
    scans({99.75: EXCEPTION, 100.25: EXCEPTION, 100.0: 0})

    assert _shares([100.0], [200.0]).tolist() == pytest.approx([2 / 3])


def test_a_centroid_carrying_the_flag_among_others_counts(scans):
    # A fragmented lobe that is also an exception (10), and a reference (16)
    scans({100.0: 10, 100.001: REFERENCE})

    assert _shares([100.0], [1e4]).tolist() == [0.5]


def test_the_share_is_of_the_flag_asked_for(scans):
    scans({100.0: EXCEPTION, 100.001: REFERENCE, 100.002: 0})

    assert _shares([100.001], [1e4], flag="reference").tolist() == pytest.approx(
        [1 / 3]
    )


def test_a_name_that_is_no_flag_is_refused(scans):
    scans({100.0: 0})

    with pytest.raises(ValueError, match="'lock'"):
        _shares([100.0], [1e4], flag="lock")


def test_a_peak_no_centroid_falls_under_has_no_share(scans):
    scans({100.0: EXCEPTION})

    assert np.isnan(_shares([101.0], [1e4])).all()


@pytest.mark.parametrize("resolution", [0.0, -1.0, np.nan])
def test_a_peak_without_a_width_has_no_share(scans, resolution):
    scans({100.0: EXCEPTION})

    assert np.isnan(_shares([100.0], [resolution])).all()


def test_a_scan_without_centroids_adds_nothing(scans):
    scans({100.0: EXCEPTION}, {})

    assert _shares([100.0], [1e4]).tolist() == [1.0]


def test_the_scans_are_those_the_averaged_centroids_take(scans):
    reader = scans({100.0: EXCEPTION}, {100.0: 0}, {100.0: 0})

    shares = _shares(
        [100.0], [1e4], polarity="-", stream="stream A", t_min=1.0, t_max=2.0
    )

    assert shares.tolist() == [0.0]
    assert reader.selections == [
        dict(polarity="-", t_min=1.0, t_max=2.0, stream="stream A")
    ]
    assert sorted(set(reader.read)) == [2, 3]


def test_shares_are_read_one_scan_at_a_time(scans):
    """A long acquisition is never held whole: each scan's centroids are read
    once it is its turn, as many times as the reader is asked for them."""
    reader = scans({100.0: EXCEPTION}, {100.0: EXCEPTION})

    _shares([100.0, 200.0], [1e4, 1e4])

    assert reader.read == [1, 1, 2, 2]
