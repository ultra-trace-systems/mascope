"""The m/z calibration factor of a scan stream, and how one is carried to a
stream that holds no calibrant.

A file whose peaks are detected per scan stream is calibrated stream by
stream: its calibration record names a factor for each stream beside the
file's own, a reading of one stream goes by that stream's, and a stream with
no calibrant of its own takes a neighbour's - shifted by what the two read
in their overlap, or as it is
(``mascope_signal.mz_factor``; ``docs/dev/ingest_routing_and_splitting.md``,
section 4.5).
"""

import numpy as np
import pytest

import mascope_signal.mz_factor as m_factor
import mascope_signal.stitch as m_stitch
from mascope_signal.mz_factor import ANCHORS, BORROWED, OVERLAP


LOW = "FTMS - p NSI SIM ms [72.0000-118.0000] R=120000"
MID = "FTMS - p NSI Full ms [132.0000-460.0000] R=120000"


def _record(file=1.000002, **streams):
    record = {"mode": "one-point", "par": {"calibration_factor": file}}
    if streams:
        record["streams"] = {
            key: {"calibration_factor": factor} for key, factor in streams.items()
        }
    return record


# -- the factor a reading goes by ---------------------------------------------------


def test_an_uncalibrated_file_reads_as_the_instrument_recorded_it():
    assert m_factor.file_factor(None) == 1.0
    assert m_factor.stream_factor(None, LOW) == 1.0
    assert m_factor.stream_factors(None, [LOW, MID]).tolist() == [1.0, 1.0]


def test_a_file_calibrated_by_one_factor_reads_every_stream_by_it():
    """Every file calibrated before streams were: its record names none."""
    record = _record(1.000002)

    assert m_factor.file_factor(record) == 1.000002
    assert m_factor.stream_factor(record, LOW) == 1.000002
    assert m_factor.stream_factor(record, None) == 1.000002


def test_a_stream_the_record_names_reads_by_its_own_factor():
    record = _record(1.000002, **{LOW: 1.0000009})

    assert m_factor.stream_factor(record, LOW) == 1.0000009
    # A stream it does not name, and the file as a whole, go by the file's
    assert m_factor.stream_factor(record, MID) == 1.000002
    assert m_factor.stream_factor(record, None) == 1.000002
    assert m_factor.file_factor(record) == 1.000002


def test_the_factors_of_a_stores_streams_are_in_the_order_of_its_keys():
    record = _record(1.000002, **{MID: 0.999999})

    factors = m_factor.stream_factors(record, [LOW, MID])

    assert factors.dtype == np.float64
    assert factors.tolist() == [1.000002, 0.999999]


def test_a_stream_named_with_no_factor_goes_by_the_files():
    record = _record(1.000002)
    record["streams"] = {LOW: {"calibration_factor": None}, MID: {}}

    assert m_factor.stream_factor(record, LOW) == 1.000002
    assert m_factor.stream_factor(record, MID) == 1.000002


# -- carrying a factor to a stream that holds no calibrant ---------------------------

# Four ranges of one polarity, as a composite method lays them: a reagent
# scan, a low window inside it, a mid and a high window.
EXTENTS = {0: (40.0, 131.0), 1: (72.0, 118.0), 2: (133.0, 450.0), 3: (444.0, 900.0)}


def _overlap(first, second, ppm, shared=10):
    """What two streams read where both measure: the second ``ppm`` above the
    first, over ``shared`` ions."""
    return {
        "streams": [first, second],
        "shared": shared,
        "ppm": [ppm - 0.1, ppm, ppm + 0.1] if shared else None,
    }


def _carried(anchored, overlaps=(), members=(0, 1, 2, 3), extents=EXTENTS):
    return m_factor.carry_across(anchored, members, list(overlaps), extents)


def test_a_stream_fitted_on_its_own_calibrants_keeps_its_fit():
    placed = _carried({0: 1.000002, 1: 1.000001, 2: 0.999999, 3: 1.000003})

    assert {index: entry["calibration_factor"] for index, entry in placed.items()} == {
        0: 1.000002,
        1: 1.000001,
        2: 0.999999,
        3: 1.000003,
    }
    assert {entry["source"] for entry in placed.values()} == {ANCHORS}
    assert all(entry["origin"] is None for entry in placed.values())


def test_with_no_stream_fitted_there_is_nothing_to_carry():
    assert _carried({}, [_overlap(0, 1, 0.5)]) == {}


def test_a_factor_crosses_an_overlap_shifted_by_what_both_read_there():
    """The low window reads the shared ions half a ppm above the reagent
    scan, so it takes half a ppm less to put them on the same m/z."""
    placed = _carried({0: 1.000002}, [_overlap(0, 1, 0.5)], members=(0, 1))

    assert placed[1]["source"] == OVERLAP
    assert placed[1]["origin"] == 0
    assert placed[1]["shared"] == 10
    assert placed[1]["calibration_factor"] == pytest.approx(
        1.000002 / (1 + 0.5e-6), rel=1e-15
    )
    assert placed[1]["shift_ppm"] == pytest.approx(-0.5, abs=1e-6)
    # Both then put an ion they share on one m/z
    recorded = 80.0
    assert recorded * (1 + 0.5e-6) * placed[1]["calibration_factor"] == pytest.approx(
        recorded * 1.000002, rel=1e-15
    )


def test_it_crosses_the_other_way_by_the_same_offset():
    """A reading is of the stream that starts higher against the other. The
    one that starts lower takes the offset the other way."""
    placed = _carried({1: 1.000001}, [_overlap(0, 1, 0.5)], members=(0, 1))

    assert placed[0]["source"] == OVERLAP
    assert placed[0]["origin"] == 1
    assert placed[0]["calibration_factor"] == pytest.approx(
        1.000001 * (1 + 0.5e-6), rel=1e-15
    )
    assert placed[0]["shift_ppm"] == pytest.approx(0.5, abs=1e-6)


def test_it_goes_from_stream_to_stream():
    overlaps = [_overlap(0, 1, 0.5), _overlap(1, 2, -0.2), _overlap(2, 3, 0.4)]

    placed = _carried({0: 1.000002}, overlaps)

    assert [placed[index]["origin"] for index in (1, 2, 3)] == [0, 1, 2]
    assert [placed[index]["source"] for index in (1, 2, 3)] == [OVERLAP] * 3
    assert placed[3]["calibration_factor"] == pytest.approx(
        1.000002 / (1 + 0.5e-6) / (1 - 0.2e-6) / (1 + 0.4e-6), rel=1e-15
    )


def test_a_stream_fitted_itself_takes_nothing_from_an_overlap():
    placed = _carried({0: 1.000002, 1: 1.000009}, [_overlap(0, 1, 0.5)], members=(0, 1))

    assert placed[1]["source"] == ANCHORS
    assert placed[1]["calibration_factor"] == 1.000009


def test_an_overlap_of_too_few_shared_ions_carries_nothing():
    few = m_factor.OVERLAP_SHIFT_MIN_SHARED - 1

    placed = _carried({0: 1.000002}, [_overlap(0, 1, 0.5, shared=few)], members=(0, 1))

    assert placed[1]["source"] == BORROWED
    assert placed[1]["calibration_factor"] == 1.000002
    assert placed[1]["shift_ppm"] is None


def test_an_overlap_of_just_enough_shared_ions_carries():
    enough = m_factor.OVERLAP_SHIFT_MIN_SHARED

    placed = _carried(
        {0: 1.000002}, [_overlap(0, 1, 0.5, shared=enough)], members=(0, 1)
    )

    assert placed[1]["source"] == OVERLAP


def test_an_overlap_no_ion_is_shared_in_carries_nothing():
    placed = _carried({0: 1.000002}, [_overlap(0, 1, 0.5, shared=0)], members=(0, 1))

    assert placed[1]["source"] == BORROWED


def test_of_two_overlaps_that_reach_a_stream_the_one_sharing_more_carries():
    overlaps = [_overlap(0, 2, 0.9, shared=6), _overlap(1, 2, -0.3, shared=20)]

    placed = _carried({0: 1.000002, 1: 1.000001}, overlaps, members=(0, 1, 2))

    assert placed[2]["origin"] == 1
    assert placed[2]["shared"] == 20
    assert placed[2]["calibration_factor"] == pytest.approx(
        1.000001 / (1 - 0.3e-6), rel=1e-15
    )


def test_a_fitted_stream_carries_before_one_that_was_carried_to():
    """The mid window is reached from the fitted reagent scan directly, and
    from the low window, which itself took its factor across an overlap and
    shares more with it. One overlap from a fit is taken before two."""
    overlaps = [
        _overlap(0, 1, 0.5, shared=25),
        _overlap(0, 2, 0.9, shared=6),
        _overlap(1, 2, -0.3, shared=20),
    ]

    placed = _carried({0: 1.000002}, overlaps, members=(0, 1, 2))

    assert placed[2]["origin"] == 0


def test_a_stream_no_overlap_reaches_takes_the_nearest_factor_as_it_is():
    """Nothing reaches the mid window, which lies against the reagent scan
    and far from the low window inside it."""
    placed = _carried({0: 1.000002, 1: 1.000001}, members=(0, 1, 2))

    assert placed[2] == {
        "calibration_factor": 1.000002,
        "source": BORROWED,
        "origin": 0,
        "shift_ppm": None,
        "shared": None,
    }


def test_the_nearest_is_by_the_gap_between_what_the_two_hold():
    extents = {0: (40.0, 60.0), 1: (200.0, 300.0), 2: (310.0, 400.0)}

    placed = _carried({0: 1.000002, 1: 1.000007}, members=(0, 1, 2), extents=extents)

    assert placed[2]["origin"] == 1


def test_of_two_that_overlap_a_stream_the_more_central_is_nearer():
    extents = {0: (40.0, 900.0), 1: (100.0, 200.0), 2: (120.0, 180.0)}

    placed = _carried({0: 1.000002, 1: 1.000007}, members=(0, 1, 2), extents=extents)

    assert placed[2]["origin"] == 1


def test_an_overlap_carries_on_from_a_stream_that_took_a_factor_unshifted():
    """The mid window borrows the reagent scan's, and the high window, which
    shares ions with the mid one, keeps its measured distance from it."""
    placed = _carried({0: 1.000002}, [_overlap(2, 3, 0.4)], members=(0, 2, 3))

    assert placed[2]["source"] == BORROWED
    assert placed[3]["source"] == OVERLAP
    assert placed[3]["origin"] == 2
    assert placed[3]["calibration_factor"] == pytest.approx(
        1.000002 / (1 + 0.4e-6), rel=1e-15
    )


def test_what_is_left_is_borrowed_one_stream_at_a_time_the_nearest_first():
    extents = {0: (40.0, 60.0), 1: (61.0, 100.0), 2: (500.0, 600.0)}

    placed = _carried({0: 1.000002}, members=(0, 1, 2), extents=extents)

    assert placed[1]["origin"] == 0
    # The far one then finds the middle one nearer than the fitted one
    assert placed[2]["origin"] == 1
    assert placed[2]["calibration_factor"] == 1.000002


def test_only_the_streams_of_the_polarity_take_part():
    """An overlap with a stream of the other polarity is none, and a fit of
    such a stream is not this polarity's."""
    placed = _carried({0: 1.000002, 3: 1.000009}, [_overlap(1, 3, 0.5)], members=(0, 1))

    assert set(placed) == {0, 1}
    assert placed[1]["source"] == BORROWED
    assert placed[1]["origin"] == 0


def test_what_is_carried_does_not_depend_on_the_order_things_are_given_in():
    overlaps = [_overlap(0, 1, 0.5), _overlap(1, 2, -0.2), _overlap(2, 3, 0.4)]

    forward = _carried({0: 1.000002}, overlaps)
    backward = _carried({0: 1.000002}, overlaps[::-1], members=(3, 2, 1, 0))

    assert forward == backward


# -- a factor per peak in the stitch -------------------------------------------------

RUNS = [[40, 67, 0], [67, 122, 1], [122, 135, 0]]


def test_each_mz_is_placed_by_the_factor_it_carries():
    """An ion the low window recorded just under its lower boundary, moved
    over it by the window's own factor, is still under it; the same m/z of
    the reagent scan, which carries none, is over."""
    factor = 1 + 3e-6
    recorded = 67.0 * (1 - 1e-6)
    mz = np.array([recorded * factor, recorded * factor, 80.0])
    factors = np.array([factor, 1.0, 1.0])

    assert mz[0] > 67.0
    assert m_stitch.owners(mz, RUNS, factors).tolist() == [0, 1, 1]
    # One factor for all of them places all by it, as before
    assert m_stitch.owners(mz, RUNS, factor).tolist() == [0, 0, 1]


def test_a_peak_is_in_its_composite_by_its_own_streams_factor():
    stitch = {"runs": {"-": RUNS}}
    factor = 1 + 3e-6
    recorded = 67.0 * (1 - 1e-6)
    mz = np.array([recorded * factor, recorded * factor])
    stream = np.array([0, 1])
    polarity = np.array(["-", "-"])

    per_peak = m_stitch.composite_mask(
        mz, stream, polarity, stitch, np.array([factor, factor])
    )
    apart = m_stitch.composite_mask(
        mz, stream, polarity, stitch, np.array([factor, 1.0])
    )

    # Both moved by the factor: the reagent scan's reading is under the
    # boundary, in its own run. The low window's is in the reagent scan's run.
    assert per_peak.tolist() == [True, False]
    # The low window's reading unmoved: it is past the boundary, in its own
    assert apart.tolist() == [True, True]


def _streams():
    def stream(lower, upper, microscans):
        return {
            "key": f"FTMS - p NSI Full ms [{lower:.4f}-{upper:.4f}] R=120000",
            "ms_level": 1,
            "signature": {
                "polarity": "-",
                "scan_mode": "Full",
                "scan_ranges": [[lower, upper]],
            },
            "acquisition_params": {"Micro Scan Count": microscans},
        }

    return [stream(40.0, 138.0, 1), stream(66.0, 124.0, 10)]


def test_an_overlap_reads_its_offset_as_the_instrument_recorded_it():
    """The two streams read three ions half a ppm apart. Calibrated each by
    its own factor they lie on one m/z, and the reading still says half a
    ppm: it is what carries a calibration across, so it holds none."""
    ions = np.array([80.0, 90.0, 100.0])
    first, second = 1 + 2e-6, (1 + 2e-6) / (1 + 0.5e-6)
    mz = np.concatenate([ions * first, ions * (1 + 0.5e-6) * second])
    stream = np.array([0, 0, 0, 1, 1, 1])
    order = np.argsort(mz, kind="stable")
    arguments = (
        mz[order],
        stream[order],
        np.full(6, 100.0),
        np.ones(6, dtype=bool),
        np.array([1, 1]),
    )

    (apart,) = m_stitch.overlap_readings(
        _streams(), *arguments, calibration=np.array([first, second])[stream[order]]
    )
    (as_one,) = m_stitch.overlap_readings(_streams(), *arguments, calibration=first)

    assert apart["shared"] == 3
    assert apart["ppm"] == pytest.approx([0.5, 0.5, 0.5], abs=1e-6)
    # Read as if one factor moved both, the offset is the calibration's own
    assert as_one["ppm"] == pytest.approx([0.0, 0.0, 0.0], abs=1e-6)


def test_an_overlap_under_one_factor_reads_as_it_did():
    ions = np.array([80.0, 90.0, 100.0])
    mz = np.sort(np.concatenate([ions, ions * (1 + 0.5e-6)]))
    arguments = (
        np.array([0, 1, 0, 1, 0, 1]),
        np.full(6, 100.0),
        np.ones(6, dtype=bool),
        np.array([1, 1]),
    )
    factor = 1 + 3e-6

    (recorded,) = m_stitch.overlap_readings(_streams(), mz, *arguments)
    (calibrated,) = m_stitch.overlap_readings(
        _streams(), mz * factor, *arguments, calibration=factor
    )
    (per_peak,) = m_stitch.overlap_readings(
        _streams(), mz * factor, *arguments, calibration=np.full(6, factor)
    )

    assert calibrated["ppm"] == pytest.approx(recorded["ppm"], abs=1e-9)
    assert per_peak["ppm"] == pytest.approx(recorded["ppm"], abs=1e-9)
    assert calibrated["shared"] == per_peak["shared"] == recorded["shared"] == 3
