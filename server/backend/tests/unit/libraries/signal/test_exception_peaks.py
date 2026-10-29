"""The peaks the instrument marked as side lobes, taken out with the satellites.

An Orbitrap flags each centroid it reads as a weak side lobe of a brighter
peak (Exception). A raw file's peak detection reads, for every peak of each
spectrum it averages, the share of the per-scan centroids under it that carry
the flag, and marks a peak most of whose centroids do as a satellite, unless
it sits on an isotopologue line of a peak that is not one
(``mascope_tools.alignment.utils.flag_exception_peaks``). A load of the store
then leaves it out, as it does every satellite.

On the scripted acquisition, whose scans say which of their centroids the
instrument flagged.
"""

import numpy as np
import pytest
from pyteomics.mass import nist_mass
from scripted_acquisition import SAMPLE_FILENAME

import mascope_file.io as m_io
import mascope_signal.compute as m_compute
import mascope_signal.peak as m_peak


NEG = "FTMS - p NSI Full ms [40.0000-600.0000]"
POS = "FTMS + p NSI Full ms [40.0000-600.0000]"
# Two experiments of one polarity, told apart by their scan ranges
NEG_LOW = "FTMS - p NSI Full ms [100.0000-200.0000]"
NEG_WIDE = "FTMS - p NSI Full ms [100.0000-400.0000]"

EXCEPTION = 8

ION = 158.153
# A lobe 33 ppm above the ion at 0.6 % of it: too far out and unpaired for
# the ratio test of the satellite flag to take it
LOBE = ION * (1 + 33e-6)
C13 = ION + nist_mass["C"][13][0] - nist_mass["C"][12][0]
H2 = ION + nist_mass["H"][2][0] - nist_mass["H"][1][0]

PEAKS = {ION: 1e6, LOBE: 6e3}


def _store():
    """The whole peak store, weak and satellite peaks included."""
    return m_io.load_peak_data(
        SAMPLE_FILENAME, drop_bad_peaks=False, composite=False
    ).compute()


def _satellites(store=None):
    """``{m/z rounded: is_satellite}`` of every peak of the store."""
    store = _store() if store is None else store
    return {
        round(float(mz), 4): bool(flag)
        for mz, flag in zip(store.mz.values, store.is_satellite.values)
    }


def _flagged(scans, *masses):
    """Every scan in ``scans`` flags each of ``masses``."""
    return {scan: {mz: EXCEPTION for mz in masses} for scan in scans}


def test_a_peak_the_instrument_flagged_in_most_scans_is_a_satellite(
    acquire, instrument_functions
):
    acquire([(NEG, 1, PEAKS)] * 3, flags=_flagged([1, 2], LOBE))

    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions)

    assert _satellites() == {round(ION, 4): False, round(LOBE, 4): True}


def test_a_peak_flagged_in_a_minority_of_scans_is_kept(acquire, instrument_functions):
    acquire([(NEG, 1, PEAKS)] * 3, flags=_flagged([1], LOBE))

    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions)

    assert _satellites() == {round(ION, 4): False, round(LOBE, 4): False}


def test_a_load_leaves_the_instruments_lobes_out(acquire, instrument_functions):
    acquire([(NEG, 1, PEAKS)] * 3, flags=_flagged([1, 2, 3], LOBE))

    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions)

    loaded = m_io.load_peak_data(SAMPLE_FILENAME)
    assert loaded.mz.values.tolist() == [ION]


def test_the_instruments_mark_is_not_kept_in_the_store(acquire, instrument_functions):
    acquire([(NEG, 1, PEAKS)] * 3, flags=_flagged([1, 2, 3], LOBE))

    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions)

    assert m_peak.EXCEPTION_PEAK not in _store().data_vars


def test_a_2h_line_the_instrument_flagged_beside_its_13c_line_is_kept(
    acquire, instrument_functions
):
    """The 2H line of a C9H18 ion lies 2.92 mDa above its 13C line, where the
    instrument expects a lobe of it, and is flagged in every scan."""
    peaks = {ION: 1e6, C13: 0.097e6, H2: 0.002e6}
    acquire([(NEG, 1, peaks)] * 3, flags=_flagged([1, 2, 3], H2))

    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions)

    assert not any(_satellites().values())


def test_each_polarity_reads_its_own_scans_flags(acquire, instrument_functions):
    """The same ion and lobe in both polarities, flagged in the positive
    scans only: one peak list per polarity, each voted on by its own scans."""
    acquire(
        [(NEG, 1, PEAKS), (POS, 2, PEAKS)] * 3,
        flags=_flagged([2, 4, 6], LOBE),
    )

    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions)

    store = _store()
    lobes = np.isclose(store.mz.values, LOBE, rtol=1e-9)
    assert dict(
        zip(store.polarity.values[lobes].tolist(), store.is_satellite.values[lobes])
    ) == {"+": True, "-": False}
    assert not store.is_satellite.values[~lobes].any()


def test_each_stream_reads_its_own_scans_flags(acquire, instrument_functions):
    """Two experiments of one polarity, detected per stream, flagged in the
    scans of one of them only."""
    acquire(
        [(NEG_LOW, 1, PEAKS), (NEG_WIDE, 2, PEAKS)] * 3,
        flags=_flagged([1, 3, 5], LOBE),
    )

    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)

    store = _store()
    keys = m_compute.peak_store_streams(store)
    assert len(keys) == 2
    lobes = np.isclose(store.mz.values, LOBE, rtol=1e-9)
    flagged = {
        keys[stream]: bool(satellite)
        for stream, satellite in zip(
            store.stream.values[lobes], store.is_satellite.values[lobes]
        )
    }
    assert flagged == {
        next(key for key in keys if "200.0000" in key): True,
        next(key for key in keys if "400.0000" in key): False,
    }


# A calibration far wider than the lobe's window, which is under 5 ppm: read
# where the calibration put the peaks, no scan's centroid would fall under one
SHIFT = 1 + 300e-6


@pytest.mark.parametrize("per_stream", [False, True], ids=["whole", "per-stream"])
def test_a_calibrated_files_flags_are_read_where_the_scans_recorded_them(
    acquire, instrument_functions, per_stream
):
    acquire(
        [(NEG_LOW, 1, PEAKS), (NEG_WIDE, 2, PEAKS)] * 3,
        flags=_flagged([1, 2, 3, 4, 5, 6], LOBE),
    )
    record = {"par": {"calibration_factor": SHIFT}}
    if per_stream:
        keys = [stream["key"] for stream in m_compute.get_peak_streams(SAMPLE_FILENAME)]
        record["streams"] = {
            key: {"calibration_factor": SHIFT * (1 + 100e-6 * index)}
            for index, key in enumerate(keys)
        }
    m_io.update_props(SAMPLE_FILENAME, {"mz_calibration": record})

    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=per_stream)

    store = _store()
    # The peaks are where the calibration put them, the lobes flagged all the same
    assert store.mz.values.min() > ION * (1 + 250e-6)
    lobes = store.sum_peak_heights.values < 1e5
    assert lobes.sum() == (2 if per_stream else 1)
    assert store.is_satellite.values[lobes].all()
    assert not store.is_satellite.values[~lobes].any()


def test_the_flags_are_read_by_the_record_the_centroids_were_placed_by(
    acquire, instrument_functions, monkeypatch
):
    """The record is read once for both reads of a spectrum. Read again for
    the flags, a calibration applied between the two would take the peaks
    back by another factor than they came by, and no centroid would lie
    under them."""
    acquire([(NEG, 1, PEAKS)] * 3, flags=_flagged([1, 2, 3], LOBE))
    m_io.update_props(
        SAMPLE_FILENAME, {"mz_calibration": {"par": {"calibration_factor": SHIFT}}}
    )
    read_centroids = m_compute.get_orbi_centroids

    async def then_an_apply_lands(*args, **kwargs):
        centroids = await read_centroids(*args, **kwargs)
        m_io.update_props(
            SAMPLE_FILENAME,
            {"mz_calibration": {"par": {"calibration_factor": 1 - 300e-6}}},
        )
        return centroids

    monkeypatch.setattr(m_compute, "get_orbi_centroids", then_an_apply_lands)

    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions)

    store = _store()
    assert store.mz.values.min() > ION * (1 + 250e-6)
    assert _satellites(store) == {
        round(ION * SHIFT, 4): False,
        round(LOBE * SHIFT, 4): True,
    }
