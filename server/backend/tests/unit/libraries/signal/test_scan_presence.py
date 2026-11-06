"""How many of a peak's scans hold it, kept per peak in a raw Orbitrap store.

An Orbitrap scan keeps a centroid only where it clears that scan's noise, and
a peak's stored sums are over every scan averaged, so a dim line missing from
part of the scans is stored diluted by the share it missed. Detection counts,
per peak, the scans with a centroid within half its width
(``mascope_thermo.thermo.get_centroid_presence``) and the scans averaged, and
the store keeps both (``mascope_signal.peak.N_SCANS``, ``N_SCANS_TOTAL``).

On the scripted acquisition, whose scans say which centroids each recorded.
"""

from scripted_acquisition import SAMPLE_FILENAME

import mascope_file.io as m_io
import mascope_signal.compute as m_compute
import mascope_signal.peak as m_peak


NEG = "FTMS - p NSI Full ms [40.0000-600.0000]"
POS = "FTMS + p NSI Full ms [40.0000-600.0000]"
# Two experiments of one polarity, told apart by their scan ranges
NEG_LOW = "FTMS - p NSI Full ms [100.0000-200.0000]"
NEG_WIDE = "FTMS - p NSI Full ms [100.0000-400.0000]"

ION = 158.153
DIM = 159.157

BOTH = {ION: 1e6, DIM: 2e4}
ALONE = {ION: 1e6}


def _store():
    """The whole peak store, weak and satellite peaks included."""
    return m_io.load_peak_data(
        SAMPLE_FILENAME, drop_bad_peaks=False, composite=False
    ).compute()


def _counts(store, **where):
    """``{(m/z rounded, label): (n_scans, n_scans_total)}``, the label the
    peak's polarity or stream."""
    label = store.stream.values if "stream" in store else store.polarity.values
    return {
        (round(float(mz), 3), label[i].item()): (
            float(store[m_peak.N_SCANS].values[i]),
            int(store[m_peak.N_SCANS_TOTAL].values[i]),
        )
        for i, mz in enumerate(store.mz.values)
    }


def test_a_dim_line_counts_the_scans_that_kept_it(acquire, instrument_functions):
    acquire([(NEG, 1, BOTH), (NEG, 1, ALONE), (NEG, 1, BOTH), (NEG, 1, ALONE)])

    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions)

    assert _counts(_store()) == {
        (round(ION, 3), "-"): (4.0, 4),
        (round(DIM, 3), "-"): (2.0, 4),
    }


def test_each_polarity_counts_its_own_scans(acquire, instrument_functions):
    """The dim line in both negative scans and in one of three positive ones."""
    acquire(
        [
            (NEG, 1, BOTH),
            (POS, 2, BOTH),
            (NEG, 1, BOTH),
            (POS, 2, ALONE),
            (POS, 2, ALONE),
        ]
    )

    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions)

    counts = _counts(_store())
    assert counts[(round(DIM, 3), "-")] == (2.0, 2)
    assert counts[(round(DIM, 3), "+")] == (1.0, 3)
    assert counts[(round(ION, 3), "+")] == (3.0, 3)


def test_each_stream_counts_its_own_scans(acquire, instrument_functions):
    """Two experiments of one polarity, detected per stream: the dim line in
    both scans of one and in one of the other's three."""
    acquire(
        [
            (NEG_LOW, 1, BOTH),
            (NEG_WIDE, 2, BOTH),
            (NEG_LOW, 1, BOTH),
            (NEG_WIDE, 2, ALONE),
            (NEG_WIDE, 2, ALONE),
        ]
    )

    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)

    store = _store()
    keys = m_compute.peak_store_streams(store)
    low = next(i for i, key in enumerate(keys) if "200.0000" in key)
    wide = next(i for i, key in enumerate(keys) if "400.0000" in key)
    counts = _counts(store)
    assert counts[(round(DIM, 3), low)] == (2.0, 2)
    assert counts[(round(DIM, 3), wide)] == (1.0, 3)
    assert counts[(round(ION, 3), wide)] == (3.0, 3)
