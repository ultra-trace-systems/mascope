"""A scan stream of a stitched file is read by its own m/z calibration factor.

Two ranges of one file trap different ion populations and read one ion up to
a ppm apart, so the streams of a file whose peaks are detected per stream
carry a factor each, in the file's calibration record
(``mascope_signal.mz_factor``; ``docs/dev/ingest_routing_and_splitting.md``,
sections 4.3 and 4.5). Everything that reads one stream of the raw file again
goes by that stream's factor, the stitched signal places each run by its
owner's, and a detection cuts the file in the same places whatever its
streams carry.

Moving each stream's peaks by its own factor carries two readings of one ion
past each other, and the store finds a row by its m/z, so its axis is put
back in order: ``rows_set_apart``.

On the scripted composite of ``composite_acquisition``. The factors are far
larger than an instrument's, so that a reading by the wrong one is seen.
"""

import numpy as np
import pytest
from composite_acquisition import (
    IN_LOW,
    KEYS,
    cached_sum_signals,
    store_rows,
)
from scripted_acquisition import SAMPLE_FILENAME

import mascope_file.io as m_io
import mascope_signal.compute as m_compute
import mascope_signal.peak as m_peak
from mascope_backend.api.controllers.samples.lib.samples_segments import (
    read_sample_segments,
)
from mascope_signal.peak import MZ_ROW_SEPARATION, rows_set_apart


REAGENT, LOW, MID, HIGH = range(4)

#: The file's own factor and those of three of its streams. The high window
#: is named by no record here, so it goes by the file's.
FILE = 1 + 10e-6
FACTORS = {REAGENT: 1 + 40e-6, LOW: 1 - 20e-6, MID: 1 + 5e-6}
ALL = np.array([FACTORS[REAGENT], FACTORS[LOW], FACTORS[MID], FILE])


# -- the axis put back in order ----------------------------------------------------


def test_an_axis_in_order_is_left_as_it_is():
    mz = np.array([62.0, 80.0, 80.00004, 100.0])

    out = rows_set_apart(mz, np.array([True, False, True, True]))

    assert out.tolist() == mz.tolist()


def test_a_reading_the_composite_leaves_out_gives_way_to_one_it_takes():
    """Moved by its own factor it has passed the composite's reading of the
    same ion, which came after it. It is set just under that one."""
    mz = np.array([62.0, 80.0002, 80.0001, 100.0])

    out = rows_set_apart(mz, np.array([True, False, True, True]))

    assert out[[0, 2, 3]].tolist() == [62.0, 80.0001, 100.0]
    assert out[1] < 80.0001
    assert out[1] == pytest.approx(80.0001, rel=2 * MZ_ROW_SEPARATION)
    assert 80.0001 / out[1] - 1 >= MZ_ROW_SEPARATION / 2


def test_it_gives_way_upward_where_it_came_after():
    mz = np.array([62.0, 80.0002, 80.0001, 100.0])

    out = rows_set_apart(mz, np.array([True, True, False, True]))

    assert out[[0, 1, 3]].tolist() == [62.0, 80.0002, 100.0]
    assert out[2] > 80.0002
    assert out[2] == pytest.approx(80.0002, rel=2 * MZ_ROW_SEPARATION)


def test_several_readings_between_two_of_the_composite_stay_in_their_order():
    mz = np.array([80.0, 80.0009, 80.0008, 80.0007, 80.0002])
    fixed = np.array([True, False, False, False, True])

    out = rows_set_apart(mz, fixed)

    assert out[[0, 4]].tolist() == [80.0, 80.0002]
    assert np.all(np.diff(out) > 0)
    assert np.all(out[1:] / out[:-1] - 1 >= MZ_ROW_SEPARATION / 2)


def test_two_readings_of_the_composite_that_passed_each_other_move_the_later():
    """Across a boundary of the map, or an ion of each polarity. The later
    one leaves room for what lies between the two."""
    mz = np.array([444.001, 444.0005, 444.0])
    fixed = np.array([True, False, True])

    out = rows_set_apart(mz, fixed)

    assert out[0] == 444.001
    assert np.all(np.diff(out) > 0)
    assert out[2] == pytest.approx(444.001, rel=4 * MZ_ROW_SEPARATION)


def test_the_first_and_the_last_row_have_one_neighbour_to_keep_clear_of():
    mz = np.array([80.0002, 80.0001, 100.0, 99.0])
    fixed = np.array([False, True, True, False])

    out = rows_set_apart(mz, fixed)

    assert out[[1, 2]].tolist() == [80.0001, 100.0]
    assert out[0] < 80.0001 and out[3] > 100.0


def test_an_axis_of_no_fixed_rows_is_set_apart_as_a_detection_sets_it():
    mz = np.array([80.0, 80.0, 79.0])

    out = rows_set_apart(mz, np.zeros(3, dtype=bool))

    assert np.all(np.diff(out) > 0)


# -- a stream read by its own factor ------------------------------------------------


@pytest.fixture
def named(composite):
    """The composite with a calibration record that names three streams, as
    an apply leaves one - and nothing moved, for the tests of what reads."""
    m_io.update_props(
        SAMPLE_FILENAME,
        {
            "mz_calibration": {
                "mode": "one-point",
                "par": {"calibration_factor": FILE},
                "streams": {
                    KEYS[index]: {"calibration_factor": factor}
                    for index, factor in FACTORS.items()
                },
            }
        },
    )
    return composite


def test_a_streams_sum_signal_is_on_its_own_factor(named):
    low = m_compute.get_sum_signal(SAMPLE_FILENAME, stream=KEYS[LOW])
    high = m_compute.get_sum_signal(SAMPLE_FILENAME, stream=KEYS[HIGH])
    pooled = m_compute.get_sum_signal(SAMPLE_FILENAME, polarity="-")

    assert low.mz.values[0] == 66.0 * FACTORS[LOW]
    # A stream the record does not name, and the file pooled, go by the file's
    assert high.mz.values[0] == 440.0 * FILE
    assert pooled.mz.values[0] == 40.0 * FILE


def test_a_streams_cached_signal_says_whose_it_is(named):
    m_compute.get_sum_signal(SAMPLE_FILENAME, stream=KEYS[LOW])
    m_compute.get_sum_signal(SAMPLE_FILENAME, polarity="-")
    m_compute.get_sum_signal(SAMPLE_FILENAME)

    tags = sorted(str(signal.stream) for signal in cached_sum_signals().values())

    assert tags == sorted([KEYS[LOW], "None", "None"])


@pytest.mark.asyncio
async def test_a_streams_centroids_are_on_its_own_factor(named):
    low, *_ = await m_compute.get_orbi_centroids(SAMPLE_FILENAME, stream=KEYS[LOW])
    pooled, *_ = await m_compute.get_orbi_centroids(SAMPLE_FILENAME, polarity="-")

    assert low.tolist() == [
        IN_LOW * FACTORS[LOW],
        100.0 * FACTORS[LOW],
        121.0 * FACTORS[LOW],
    ]
    assert pooled[0] == 62.0 * FILE


def test_a_streams_scans_are_on_its_own_factor(named, monkeypatch):
    def per_scan(path, *args, stream=None, **kwargs):
        return [{"masses": np.array([62.0, 80.0])}]

    monkeypatch.setattr(m_compute.m_thermo, "get_centroids_per_scan", per_scan)

    (reagent,) = m_compute.get_orbi_centroids_per_scan(
        SAMPLE_FILENAME, stream=KEYS[REAGENT]
    )
    (pooled,) = m_compute.get_orbi_centroids_per_scan(SAMPLE_FILENAME, polarity="-")

    assert reagent["masses"].tolist() == [
        62.0 * FACTORS[REAGENT],
        80.0 * FACTORS[REAGENT],
    ]
    assert pooled["masses"].tolist() == [62.0 * FILE, 80.0 * FILE]


@pytest.mark.asyncio
async def test_a_stream_is_read_back_at_what_its_factor_takes_off(named):
    """The low window's ion at 100, asked for where its factor put it, is
    found; asked for where the file's would have, it is thirty ppm off."""
    found = await m_compute.get_peak_timeseries(
        SAMPLE_FILENAME, [100.0 * FACTORS[LOW]], stream=KEYS[LOW]
    )
    missed = await m_compute.get_peak_timeseries(
        SAMPLE_FILENAME, [100.0 * FILE], stream=KEYS[LOW]
    )

    assert found.values.sum() == pytest.approx(150.0)
    assert found.mz.values.tolist() == [100.0 * FACTORS[LOW]]
    assert missed.values.sum() == 0.0


def test_each_run_of_the_stitched_signal_is_placed_by_its_owner(named):
    stitched = m_compute.get_composite_sum_signal(SAMPLE_FILENAME, "-")
    mz, segment = stitched.mz.values, stitched.segment.values

    # The low window owns from 67, the mid from 133, the high from 444
    for index, lower in ((LOW, 67.0), (MID, 133.0), (HIGH, 444.0)):
        own = mz[segment == index]
        assert own[0] == lower * ALL[index]
    assert np.all(np.diff(mz) > 0)


def test_a_run_starts_above_the_one_before_where_their_factors_cross(composite):
    """The reagent scan's last sample under 67 is moved above the low
    window's first ones. Those are left out, so that the axis only rises."""
    m_io.update_props(
        SAMPLE_FILENAME,
        {
            "mz_calibration": {
                "par": {"calibration_factor": 1.0},
                "streams": {KEYS[REAGENT]: {"calibration_factor": 1.005}},
            }
        },
    )

    stitched = m_compute.get_composite_sum_signal(SAMPLE_FILENAME, "-")
    mz, segment = stitched.mz.values, stitched.segment.values

    assert np.all(np.diff(mz) > 0)
    assert mz[segment == REAGENT][mz[segment == REAGENT] < 100].max() == 66.75 * 1.005
    assert mz[segment == LOW][0] == 67.25


def test_a_run_starts_past_a_sample_the_one_before_put_exactly_on_its_own(composite):
    """The reagent scan's last sample, moved onto the low window's first to
    the last digit: one of the two is left out, so that no m/z is there
    twice."""
    factor = 67.0 / 66.75
    assert 66.75 * factor == 67.0
    m_io.update_props(
        SAMPLE_FILENAME,
        {
            "mz_calibration": {
                "par": {"calibration_factor": 1.0},
                "streams": {KEYS[REAGENT]: {"calibration_factor": factor}},
            }
        },
    )

    stitched = m_compute.get_composite_sum_signal(SAMPLE_FILENAME, "-")
    mz, segment = stitched.mz.values, stitched.segment.values

    assert np.all(np.diff(mz) > 0)
    assert mz[segment == LOW][0] == 67.25


def test_a_stitched_signal_is_cached_under_the_factors_it_was_placed_by():
    runs = [[40, 67, 0], [67, 122, 1]]
    name = lambda factors: m_compute._composite_sum_signal_name(  # noqa: E731
        None, None, "-", "orbi_raw", runs, KEYS, factors
    )

    assert name(np.array([1.0, 1.0, 1.0, 1.0])) != name(None)
    assert name(ALL) != name(np.array([1.0, 1.0, 1.0, 1.0]))
    # Only the streams the runs name are in it
    other = ALL.copy()
    other[HIGH] = 1.5
    assert name(ALL) == name(other)


def test_the_segments_of_a_sample_carry_each_streams_factor(named):
    segments = read_sample_segments(SAMPLE_FILENAME, "-")

    assert segments.factors == ALL.tolist()
    assert [
        (run["segment"], run["mz_lower"], run["mz_upper"])
        for run in segments.boundaries()
    ] == [
        (REAGENT, 40 * ALL[REAGENT], 67 * ALL[REAGENT]),
        (LOW, 67 * ALL[LOW], 122 * ALL[LOW]),
        (REAGENT, 122 * ALL[REAGENT], 133 * ALL[REAGENT]),
        (MID, 133 * ALL[MID], 444 * ALL[MID]),
        (HIGH, 444 * ALL[HIGH], 900 * ALL[HIGH]),
    ]


def test_a_run_takes_its_own_samples_where_its_edge_falls_among_anothers(composite):
    """The reagent scan's edge at 67, placed by its own large factor, lies
    among the low window's samples. The samples say whose they are."""
    m_io.update_props(
        SAMPLE_FILENAME,
        {
            "mz_calibration": {
                "par": {"calibration_factor": 1.0},
                "streams": {KEYS[REAGENT]: {"calibration_factor": 1.005}},
            }
        },
    )
    stitched = m_compute.get_composite_sum_signal(SAMPLE_FILENAME, "-")
    mz, segment = stitched.mz.values, stitched.segment.values
    segments = read_sample_segments(SAMPLE_FILENAME, "-")

    exact = segments.runs_of(mz, segment)
    by_edges = segments.runs_of(mz)

    for run in exact:
        assert set(segment[run["from"] : run["to"]]) == {run["segment"]}
    assert [run["to"] for run in exact][:-1] == [run["from"] for run in exact][1:]
    assert exact[0]["to"] == by_edges[0]["to"] - 1


# -- peaks detected again on a file calibrated stream by stream ----------------------


def test_a_file_is_cut_in_the_same_places_once_its_streams_are_calibrated(
    composite, instrument_functions
):
    before = store_rows()
    recorded = {
        (int(stream), round(float(mz), 4)): bool(taken)
        for mz, stream, taken in zip(before.mz, before.stream, before.composite)
    }
    m_io.update_props(
        SAMPLE_FILENAME,
        {
            "mz_calibration": {
                "par": {"calibration_factor": FILE},
                "streams": {
                    KEYS[index]: {"calibration_factor": factor}
                    for index, factor in FACTORS.items()
                },
            }
        },
    )

    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions)
    after = store_rows()

    assert np.all(np.diff(after.mz) > 0)
    assert {
        (int(stream), round(float(mz / ALL[stream]), 4)): bool(taken)
        for mz, stream, taken in zip(after.mz, after.stream, after.composite)
    } == recorded
    # What the overlaps read is of the two streams as recorded
    assert [reading["ppm"] for reading in after.attrs["stitch_overlaps"]] == [
        pytest.approx(reading["ppm"], abs=1e-6) if reading["ppm"] else None
        for reading in before.attrs["stitch_overlaps"]
    ]
    assert before.attrs["stitch_overlaps"][0]["ppm"] == pytest.approx([0.5] * 3)
