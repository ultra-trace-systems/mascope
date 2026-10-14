"""A stitched file is calibrated scan stream by scan stream.

The streams of a file whose peaks are detected per stream carry an m/z
calibration factor each (``mascope_signal.mz_factor``;
``docs/dev/ingest_routing_and_splitting.md``, sections 4.3 and 4.5):

- an apply moves each stream's peaks and signals by that stream's own, and
  puts the store's axis back in order where two readings of one ion passed
  each other;
- a fit is one fit per stream of the sample's polarity, on the calibrants
  that stream holds, and a stream that holds none takes a neighbour's
  factor: across their overlap where they share enough ions, else as it is;
- each stream fitted on its own is held to the file's quality bar.

On the scripted composite of ``composite_acquisition``. The factors are far
larger than an instrument's, so that a move by the wrong one is seen.
"""

import asyncio
import os
import shutil
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import numpy as np
import pandas as pd
import pytest
import zarr
from composite_acquisition import (
    COMPOSITE,
    IN_HIGH,
    IN_LOW,
    KEYS,
    MICROSCANS,
    cached_sum_signals,
    store_rows,
)
from scripted_acquisition import SAMPLE_FILENAME

import mascope_file.io as m_io
import mascope_file.name as m_name
import mascope_signal.compute as m_compute
import mascope_signal.mz_factor as m_factor
import mascope_signal.peak as m_peak
from mascope_backend.api.controllers.calibration import calibration_controller
from mascope_backend.api.controllers.calibration.lib import calibration_mz_fit
from mascope_backend.api.controllers.calibration.lib.calibration_mz_fit import (
    OrbiCalibrationHandler,
    calibration_quality_issues,
)
from mascope_backend.api.models.calibration.calibration_pydantic_model import (
    CalibrationFitParams,
    MzCalibrationParams,
    OrbiCalibrationParams,
)
from mascope_backend.api.models.calibration.config import calibration_config
from mascope_signal.peak import MZ_ROW_SEPARATION


REAGENT, LOW, MID, HIGH = range(4)
LABELS = ["m/z 40-138", "m/z 66-124", "m/z 132-460", "m/z 440-900"]

#: The file's own factor and those of three of its streams. The high window
#: is named by no fit here, so it goes by the file's.
FILE = 1 + 10e-6
FACTORS = {REAGENT: 1 + 40e-6, LOW: 1 - 20e-6, MID: 1 + 5e-6}
ALL = np.array([FACTORS[REAGENT], FACTORS[LOW], FACTORS[MID], FILE])


def _fit(file=FILE, streams=FACTORS, **extra):
    """A fit as the handler hands one to apply."""
    fit = {
        "mode": "one-point",
        "par": {
            "old_factor": 1.0,
            "old_factor_scaling": file,
            "calibration_factor": file,
        },
        **extra,
    }
    if streams is not None:
        fit["streams"] = {
            KEYS[index]: {"calibration_factor": factor}
            for index, factor in streams.items()
        }
    return fit


def _apply(fit):
    """Apply as the handler does once it is off the event loop."""
    OrbiCalibrationHandler(SAMPLE_FILENAME)._guarded_apply(fit)
    return fit


def _record():
    return m_io.read_props(SAMPLE_FILENAME)["mz_calibration"]


# -- applying a fit ------------------------------------------------------------------


def test_each_streams_peaks_move_by_its_own_factor(composite):
    before = store_rows()

    _apply(_fit())
    after = store_rows()

    taken = before.composite
    assert (
        after.mz[taken].tolist()
        == (before.mz[taken] * ALL[before.stream[taken]]).tolist()
    )
    # Nothing else of the store changed
    assert after.peak_id.tolist() == before.peak_id.tolist()
    assert after.stream.tolist() == before.stream.tolist()
    assert after.composite.tolist() == before.composite.tolist()


def test_a_reading_that_passed_the_composites_gives_way_to_it(composite):
    """The reagent scan read the ion at 80 just under the low window's
    reading of it. Its factor takes it sixty ppm over."""
    before = store_rows()
    passed = int(np.flatnonzero((before.stream == REAGENT) & (before.mz == 80.0))[0])
    assert not before.composite[passed] and before.composite[passed + 1]

    _apply(_fit())
    after = store_rows()

    assert np.all(np.diff(after.mz) > 0)
    assert 80.0 * FACTORS[REAGENT] > after.mz[passed + 1]
    assert after.mz[passed] == pytest.approx(
        after.mz[passed + 1], rel=2 * MZ_ROW_SEPARATION
    )
    # The others the composite leaves out moved by their own factor
    for mz, index in ((134.0, REAGENT), (450.0, MID)):
        row = int(np.flatnonzero((before.stream == index) & (before.mz == mz))[0])
        assert after.mz[row] == mz * ALL[index]


def test_the_record_names_the_factor_of_each_stream_the_fit_named(composite):
    fit = _fit(streams={**FACTORS})
    fit["streams"][KEYS[LOW]]["source"] = "anchors"
    fit["streams"]["FTMS - p NSI Full ms [1.0000-2.0000] R=120000"] = {
        "calibration_factor": 3.0
    }

    _apply(fit)

    assert _record() == {
        "mode": "one-point",
        "par": {"calibration_factor": FILE},
        "streams": {
            KEYS[index]: {"calibration_factor": factor}
            for index, factor in FACTORS.items()
        },
    }
    assert m_factor.stream_factors(_record(), KEYS).tolist() == ALL.tolist()


def test_a_second_fit_moves_each_stream_from_where_the_first_left_it(composite):
    """By the new factor over the one the file holds, whatever scaling the
    fit states: it was computed when the fit was, and the file may have been
    calibrated since."""
    before = store_rows()
    _apply(_fit())
    again = {REAGENT: 1 - 3e-6, LOW: 1 + 7e-6, MID: 1.0}
    fit = _fit(file=1 + 2e-6, streams=again)
    fit["par"]["old_factor_scaling"] = 5.0

    _apply(fit)
    after = store_rows()

    factors = np.array([again[REAGENT], again[LOW], again[MID], 1 + 2e-6])
    taken = before.composite
    # From what each row recorded, so to the last digit: nothing of the
    # first calibration is left in the second
    assert (
        after.mz[taken].tolist()
        == (before.mz[taken] * factors[before.stream[taken]]).tolist()
    )
    assert after.recorded.tolist() == before.mz.tolist()
    assert m_factor.stream_factors(_record(), KEYS).tolist() == factors.tolist()


def _forget_readings():
    """Make the test sample's store one written before its rows kept what
    the instrument recorded."""
    path = m_name.filename_to_zarr_path(SAMPLE_FILENAME, "peak_timeseries")
    shutil.rmtree(os.path.join(path, "mz_recorded"))
    zarr.consolidate_metadata(path)
    assert store_rows().recorded is None


def test_a_store_written_before_readings_were_kept_is_given_them_first(composite):
    """Calibrated by one factor since, as every such store was: its rows
    are taken back by it, kept, and only then moved apart."""
    before = store_rows()
    _apply(_fit(streams=None))
    _forget_readings()

    _apply(_fit())
    after = store_rows()

    assert after.recorded == pytest.approx(before.mz, rel=1e-15)
    taken = before.composite
    assert after.mz[taken] == pytest.approx(
        before.mz[taken] * ALL[before.stream[taken]], rel=1e-15
    )
    # And the row that gave way still says what it read
    passed = int(np.flatnonzero((before.stream == REAGENT) & (before.mz == 80.0))[0])
    assert after.recorded[passed] == pytest.approx(80.0, rel=1e-15)
    assert after.mz[passed] != pytest.approx(80.0 * FACTORS[REAGENT], rel=1e-9)


@pytest.mark.asyncio
async def test_a_reading_set_apart_is_read_back_at_what_it_recorded(composite):
    """The reagent scan's reading of the ion at 80 gave way to the low
    window's, sixty ppm from where its own factor puts it. Its timeseries is
    read from the file at the m/z the reagent scan recorded: at the row's
    place less the factor, the file holds nothing."""
    await asyncio.to_thread(_apply, _fit())
    rows = store_rows()
    passed = int(np.flatnonzero((rows.stream == REAGENT) & (rows.recorded == 80.0))[0])
    assert not rows.composite[passed]

    filled = await m_compute.load_peak_timeseries(
        SAMPLE_FILENAME, [float(rows.mz[passed])]
    )

    assert filled.peak_id.values.tolist() == [rows.peak_id[passed]]
    assert float(np.nansum(filled.peak_heights.values)) == pytest.approx(50.0)


def test_a_fit_the_file_already_has_moves_nothing(composite):
    _apply(_fit())
    before = store_rows()

    with patch.object(calibration_mz_fit.m_io, "update_zarr_array_coord") as moved:
        _apply(_fit())

    moved.assert_not_called()
    assert store_rows().mz.tolist() == before.mz.tolist()


def test_a_fit_that_differs_in_one_stream_is_applied(composite):
    _apply(_fit())
    before = store_rows()

    _apply(_fit(streams={**FACTORS, LOW: 1 - 21e-6}))
    after = store_rows()

    low = before.composite & (before.stream == LOW)
    assert after.mz[low] == pytest.approx(
        before.mz[low] * (1 - 21e-6) / FACTORS[LOW], rel=1e-15
    )
    assert (
        after.mz[before.stream == MID].tolist()
        == before.mz[before.stream == MID].tolist()
    )


def test_a_fit_that_names_no_stream_moves_the_file_as_one(composite):
    """A reset, and a fit made before the file's peaks were detected per
    stream: every stream goes to the file's factor."""
    before = store_rows()
    _apply(_fit())

    _apply(_fit(file=1.0, streams=None))
    after = store_rows()

    assert "streams" not in _record()
    assert _record()["par"] == {"calibration_factor": 1.0}
    assert after.mz[before.composite] == pytest.approx(
        before.mz[before.composite], rel=1e-15
    )
    assert np.all(np.diff(after.mz) > 0)


def test_a_file_calibrated_by_one_factor_is_moved_by_it_as_before(composite):
    """No stream named, none recorded: each row by the fit's own factor."""
    before = store_rows()

    _apply(_fit(streams=None))

    assert store_rows().mz.tolist() == (before.mz * FILE).tolist()
    assert _record() == {"mode": "one-point", "par": {"calibration_factor": FILE}}


@pytest.mark.asyncio
async def test_resetting_the_calibration_takes_every_stream_back(composite):
    before = store_rows()
    await asyncio.to_thread(_apply, _fit())
    sample_file = SimpleNamespace(
        sample_file_id="sf-1",
        filename=SAMPLE_FILENAME,
        mz_calibration=_record(),
        to_dict=lambda: {},
    )

    with (
        patch.object(calibration_controller, "update_sample_file", AsyncMock()),
        patch.object(calibration_controller, "SampleFileUpdate", lambda **kw: kw),
    ):
        assert await calibration_controller.reset_mz_calibration(sample_file)
    after = store_rows()

    assert _record() is None
    assert sample_file.mz_calibration is None
    assert after.mz[before.composite] == pytest.approx(
        before.mz[before.composite], rel=1e-15
    )


async def _reset_and_restore(sample_file):
    """Reset a file's calibration and put it back, as a re-processing does to
    a file whose peaks it then cannot detect again."""
    with (
        patch.object(calibration_controller, "update_sample_file", AsyncMock()),
        patch.object(calibration_controller, "SampleFileUpdate", lambda **kw: kw),
    ):
        cleared = await calibration_controller.reset_mz_calibration(sample_file)
        assert _record() is None
        await calibration_controller.restore_mz_calibration(sample_file, cleared)


@pytest.mark.asyncio
async def test_restoring_a_reset_calibration_puts_every_stream_back(composite):
    """Each stream on its own factor again, each row where it was - where
    the samples kept were matched - and the record as it was."""
    await asyncio.to_thread(_apply, _fit())
    m_compute.get_sum_signal(SAMPLE_FILENAME, stream=KEYS[LOW])
    calibrated, record = store_rows(), _record()
    recorded = {**record, "status": "ok", "verified": True}
    sample_file = SimpleNamespace(
        sample_file_id="sf-1",
        filename=SAMPLE_FILENAME,
        mz_calibration=recorded,
        to_dict=lambda: {},
    )

    await _reset_and_restore(sample_file)
    after = store_rows()

    assert after.mz == pytest.approx(calibrated.mz, rel=1e-15)
    assert after.peak_id.tolist() == calibrated.peak_id.tolist()
    assert _record() == record
    assert sample_file.mz_calibration == recorded
    by_tag = {str(signal.stream): signal for signal in cached_sum_signals().values()}
    assert by_tag[KEYS[LOW]].mz[0] == pytest.approx(66.0 * FACTORS[LOW], rel=1e-15)


def test_the_files_cached_signals_move_each_by_whose_it_is(composite):
    """The four streams' signals, cached as the stitched one was made from
    them; the stitched one; the polarity's pooled one; and the full one."""
    m_compute.get_composite_sum_signal(SAMPLE_FILENAME, "-")
    m_compute.get_sum_signal(SAMPLE_FILENAME, polarity="-")
    before = cached_sum_signals()
    assert sum(signal.stitched for signal in before.values()) == 1
    assert len(before) == 7

    _apply(_fit())
    after = cached_sum_signals()

    # A stream's own by its factor, the full signal by the file's
    by_tag = {str(signal.stream): signal for signal in after.values()}
    assert by_tag[KEYS[REAGENT]].mz[0] == 40.0 * FACTORS[REAGENT]
    assert by_tag[KEYS[LOW]].mz[0] == 66.0 * FACTORS[LOW]
    assert by_tag[KEYS[MID]].mz[0] == 132.0 * FACTORS[MID]
    assert by_tag[KEYS[HIGH]].mz[0] == 440.0 * FILE
    assert by_tag["None"].mz[0] == 40.0 * FILE
    # The stitched one is gone, and the pooled one, which names no stream
    assert not any(signal.stitched for signal in after.values())
    assert sorted(str(signal.stream) for signal in after.values()) == sorted(
        [*KEYS, "None"]
    )
    full = [name for name in after if not name.startswith("sum_signal_")]
    assert len(full) == 1 and len(after) == 5


def test_a_signal_stitched_after_an_apply_is_on_the_new_factors(composite):
    m_compute.get_composite_sum_signal(SAMPLE_FILENAME, "-")

    _apply(_fit())
    stitched = m_compute.get_composite_sum_signal(SAMPLE_FILENAME, "-")
    mz, segment = stitched.mz.values, stitched.segment.values

    for index, lower in ((LOW, 67.0), (MID, 133.0), (HIGH, 444.0)):
        assert mz[segment == index][0] == lower * ALL[index]


@pytest.mark.asyncio
async def test_a_moved_peak_is_read_back_from_the_file_by_its_streams_factor(composite):
    """Its timeseries is filled after the apply: the file is asked at the
    peak's m/z less its own stream's factor."""
    await asyncio.to_thread(_apply, _fit())
    rows = store_rows()
    low_100 = float(rows.mz[(rows.stream == LOW) & rows.composite][1])
    assert low_100 == 100.0 * FACTORS[LOW]

    filled = await m_compute.load_peak_timeseries(SAMPLE_FILENAME, [low_100])

    assert float(np.nansum(filled.peak_heights.values)) == pytest.approx(150.0)


def test_a_file_detected_whole_is_calibrated_by_the_files_factor_alone(
    acquire, instrument_functions
):
    """What a fit says of streams the store does not tell apart is neither
    applied nor recorded."""
    acquire(COMPOSITE, MICROSCANS)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=False)
    before = m_io.load_array(SAMPLE_FILENAME, "peak_timeseries").mz.values

    _apply(_fit())

    after = m_io.load_array(SAMPLE_FILENAME, "peak_timeseries").mz.values
    assert after.tolist() == (before * FILE).tolist()
    assert _record() == {"mode": "one-point", "par": {"calibration_factor": FILE}}


def _one_factor(new, old=FILE):
    """A fit of the file as one, as the handler hands one to apply."""
    return {
        "mode": "one-point",
        "par": {
            "old_factor": old,
            "old_factor_scaling": new / old,
            "calibration_factor": new,
        },
    }


@pytest.fixture
def since_detected_whole(composite, instrument_functions):
    """The composite calibrated stream by stream, its signals cached, and
    its peaks then detected again as one: what a rebuild that cannot read the
    streams back leaves. The record still names the streams, and each one's
    cached signal sits on that stream's factor."""
    _apply(_fit())
    m_compute.get_composite_sum_signal(SAMPLE_FILENAME, "-")
    m_compute.get_sum_signal(SAMPLE_FILENAME, polarity="-")
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=False)
    cached = {str(signal.stream): signal for signal in cached_sum_signals().values()}
    assert cached[KEYS[LOW]].mz[0] == 66.0 * FACTORS[LOW]
    assert set(_record()["streams"]) == {KEYS[REAGENT], KEYS[LOW], KEYS[MID]}
    return composite


def test_a_streams_cached_signal_follows_the_record_through_an_apply_as_one(
    since_detected_whole,
):
    """Which way an apply goes is the store's to say; where a cached signal
    sits is the record's. Moved by the file's scaling with the rest, a
    stream's signal would stay off the file's factor by what the two
    differed, under a record that no longer says so."""
    again = 1 + 30e-6

    _apply(_one_factor(again))

    assert _record() == {"mode": "one-point", "par": {"calibration_factor": again}}
    after = cached_sum_signals()
    by_tag = {str(signal.stream): signal for signal in after.values()}
    for index, lower in ((REAGENT, 40.0), (LOW, 66.0), (MID, 132.0), (HIGH, 440.0)):
        assert by_tag[KEYS[index]].mz[0] == pytest.approx(lower * again, rel=1e-15)
    assert by_tag["None"].mz[0] == pytest.approx(40.0 * again, rel=1e-15)
    # The stitched one is gone, and the pooled one, which names no stream
    assert not any(signal.stitched for signal in after.values())
    assert sorted(str(signal.stream) for signal in after.values()) == sorted(
        [*KEYS, "None"]
    )
    # A stream read again is where the record says a stream now reads
    low = m_compute.get_sum_signal(SAMPLE_FILENAME, stream=KEYS[LOW])
    assert low.mz.values[0] == pytest.approx(66.0 * again, rel=1e-15)


@pytest.mark.asyncio
async def test_a_reset_takes_such_a_files_stream_signals_back_too(
    since_detected_whole,
):
    sample_file = SimpleNamespace(
        sample_file_id="sf-1",
        filename=SAMPLE_FILENAME,
        mz_calibration=_record(),
        to_dict=lambda: {},
    )

    with (
        patch.object(calibration_controller, "update_sample_file", AsyncMock()),
        patch.object(calibration_controller, "SampleFileUpdate", lambda **kw: kw),
    ):
        assert await calibration_controller.reset_mz_calibration(sample_file)

    assert _record() is None
    by_tag = {str(signal.stream): signal for signal in cached_sum_signals().values()}
    for index, lower in ((REAGENT, 40.0), (LOW, 66.0), (MID, 132.0), (HIGH, 440.0)):
        assert by_tag[KEYS[index]].mz[0] == pytest.approx(lower, rel=1e-15)


@pytest.mark.asyncio
async def test_restoring_such_a_files_calibration_puts_it_on_the_files_factor(
    since_detected_whole,
):
    """Its store is detected whole, so its peaks go back on the file's factor,
    where they were. The record keeps the file's factor and names no stream,
    as an apply of one factor leaves it, and each stream's cached signal sits
    where such a record says it reads."""

    def store_mz():
        # A whole store labels no streams, which store_rows reads
        return m_io.load_array(SAMPLE_FILENAME, "peak_timeseries").mz.values

    before = store_mz()
    sample_file = SimpleNamespace(
        sample_file_id="sf-1",
        filename=SAMPLE_FILENAME,
        mz_calibration=_record(),
        to_dict=lambda: {},
    )

    await _reset_and_restore(sample_file)

    assert store_mz() == pytest.approx(before, rel=1e-15)
    assert _record() == {"mode": "one-point", "par": {"calibration_factor": FILE}}
    by_tag = {str(signal.stream): signal for signal in cached_sum_signals().values()}
    for index, lower in ((REAGENT, 40.0), (LOW, 66.0), (MID, 132.0), (HIGH, 440.0)):
        assert by_tag[KEYS[index]].mz[0] == pytest.approx(lower * FILE, rel=1e-15)
    low = m_compute.get_sum_signal(SAMPLE_FILENAME, stream=KEYS[LOW])
    assert low.mz.values[0] == pytest.approx(66.0 * FILE, rel=1e-15)


def test_a_file_never_calibrated_by_stream_keeps_every_cached_signal(
    acquire, instrument_functions
):
    """The record names no stream, so every cached signal sits on the
    file's factor and moves by its scaling, as it always has: none is removed
    to be made again."""
    acquire(COMPOSITE, MICROSCANS)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=False)
    _apply(_fit(streams=None))
    m_compute.get_sum_signal(SAMPLE_FILENAME, polarity="-")
    m_compute.get_sum_signal(SAMPLE_FILENAME, stream=KEYS[LOW])
    before = cached_sum_signals()
    again = 1 + 30e-6

    _apply(_one_factor(again))
    after = cached_sum_signals()

    assert sorted(after) == sorted(before)
    for name, signal in after.items():
        assert signal.mz[0] == pytest.approx(before[name].mz[0] / FILE * again)


def test_the_seal_of_a_fit_covers_its_streams_factors():
    with patch.object(calibration_controller, "derive_token_secret", lambda _: "key"):
        seal = calibration_controller._fit_seal
        sealed = seal(_fit(), SAMPLE_FILENAME)

        assert seal(_fit(), SAMPLE_FILENAME) == sealed
        assert seal(_fit(streams={**FACTORS, LOW: 1.0}), SAMPLE_FILENAME) != sealed
        # A fit of one factor is sealed as it was before streams had theirs
        without = _fit(streams=None)
        assert seal({**without, "streams": None}, SAMPLE_FILENAME) == seal(
            without, SAMPLE_FILENAME
        )
        assert seal(without, SAMPLE_FILENAME) != sealed


# -- fitting stream by stream --------------------------------------------------------

#: Calibrants only one stream each holds: the reagent ion in the reagent
#: scan, one ion in the low window and one in the high. The mid window holds
#: none. Each target is a few ppm off what its stream recorded.
ASKED = {REAGENT: 1 + 2e-6, LOW: 1 - 1e-6, HIGH: 1 + 3e-6}
TARGETS = [
    ("ion-reagent", "NO3-", 62.0 * ASKED[REAGENT]),
    ("ion-low", "C3H3O4-", 100.0 * ASKED[LOW]),
    ("ion-high", "C30H50O18-", 700.0 * ASKED[HIGH]),
]


def _isotopes(targets=TARGETS):
    return pd.DataFrame(
        [(ion, formula, mz, 1.0) for ion, formula, mz in targets],
        columns=["target_ion_id", "target_isotope_formula", "mz", "relative_abundance"],
    )


@pytest.fixture
def fitted(composite):
    """Fit the composite's negative polarity against the given calibrants."""

    async def _fitted(targets=TARGETS, polarity="-"):
        params = CalibrationFitParams(
            calibration_collection_id="tc-1",
            ionization_mechanism_ids=["im-1"],
            polarity=polarity,
            **OrbiCalibrationParams().model_dump(),
        )
        handler = OrbiCalibrationHandler(SAMPLE_FILENAME, params)
        handler._resolve_calibration_isotopes = AsyncMock(
            return_value=_isotopes(targets)
        )
        resolution = AsyncMock(return_value=(None, lambda mz: np.full_like(mz, 1e5)))
        with patch.object(calibration_mz_fit, "read_instrument_functions", resolution):
            await handler.fit()
        return handler

    return _fitted


@pytest.mark.asyncio
async def test_each_stream_is_fitted_on_the_calibrants_it_holds(fitted):
    handler = await fitted()

    assert handler.warning is None and handler.error is None
    streams = handler.fit_result["streams"]
    assert set(streams) == set(KEYS)
    for index, asked in ASKED.items():
        assert streams[KEYS[index]] == {
            "calibration_factor": pytest.approx(asked, rel=1e-15)
        }
    by_label = {segment["label"]: segment for segment in handler.segments}
    assert [segment["label"] for segment in handler.segments] == LABELS
    for index in ASKED:
        segment = by_label[LABELS[index]]
        assert segment["source"] == "anchors"
        assert segment["origin"] is None
        assert segment["quality"]["n_points"] == 1
        assert segment["quality"]["axis_correction_ppm"] == pytest.approx(
            (ASKED[index] - 1) * 1e6, abs=1e-6
        )


@pytest.mark.asyncio
async def test_a_stream_with_no_calibrant_takes_the_nearest_fit_as_it_is(fitted):
    """The mid window shares one ion with the high window, too few to carry
    a calibration across. Its scan range overlaps the reagent scan's and the
    high window's, and its middle lies nearer the reagent scan's."""
    handler = await fitted()

    mid = handler.segments[MID]
    assert handler.fit_result["streams"][KEYS[MID]] == {
        "calibration_factor": pytest.approx(ASKED[REAGENT], rel=1e-15)
    }
    assert mid["source"] == "borrowed"
    assert mid["origin"] == LABELS[REAGENT]
    assert mid["shift_ppm"] is None
    assert mid["quality"] is None
    assert mid["note"] == "No calibration peaks found"


@pytest.mark.asyncio
async def test_it_takes_it_across_their_overlap_where_they_share_enough(
    fitted, monkeypatch
):
    """The high window reads the ion both hold 0.4 ppm above the mid one."""
    monkeypatch.setattr(m_factor, "OVERLAP_SHIFT_MIN_SHARED", 1)

    handler = await fitted()

    mid = handler.segments[MID]
    assert mid["source"] == "overlap"
    assert mid["origin"] == LABELS[HIGH]
    assert mid["shared_ions"] == 1
    assert mid["shift_ppm"] == pytest.approx(0.4, abs=1e-3)
    assert handler.fit_result["streams"][KEYS[MID]][
        "calibration_factor"
    ] == pytest.approx(ASKED[HIGH] * IN_HIGH / 450.0, rel=1e-12)


@pytest.mark.asyncio
async def test_the_files_own_factor_is_the_median_of_what_the_calibrants_ask(fitted):
    handler = await fitted()

    par = handler.fit_result["par"]
    assert par["calibration_factor"] == pytest.approx(ASKED[REAGENT], rel=1e-15)
    assert par["old_factor"] == 1.0
    assert par["old_factor_scaling"] == par["calibration_factor"]
    assert handler.fit_result["mode"] == "one-point"


@pytest.mark.asyncio
async def test_the_statistics_are_every_streams_calibrants_under_one_summary(fitted):
    handler = await fitted()

    *points, summary = handler.stats
    assert [(point["segment"], point["segment_label"]) for point in points] == [
        (REAGENT, LABELS[REAGENT]),
        (LOW, LABELS[LOW]),
        (HIGH, LABELS[HIGH]),
    ]
    assert [point["target_ion_id"] for point in points] == [
        "ion-reagent",
        "ion-low",
        "ion-high",
    ]
    # Each judged by its own stream's fit, which a single point fits exactly
    assert [point["calibration_mz_error"] for point in points] == pytest.approx(
        [0.0] * 3, abs=1e-9
    )
    assert "mz" not in summary
    assert summary["match_mz_error"] == pytest.approx((2 + 1 + 3) / 3, abs=1e-4)


def _tic(stream=None):
    return float(np.sum(m_compute.get_tic_per_scan(SAMPLE_FILENAME, stream=stream)[1]))


@pytest.mark.asyncio
async def test_a_streams_calibrants_are_measured_against_its_own_scans(fitted):
    """A calibrant's intensity is averaged over its stream's scans, and its
    share is of the ion current of those scans. Against the file's, a narrow
    window's calibrants would be measured by scans that are mostly other
    streams', and its block held to a bar by them."""
    handler = await fitted()

    *points, summary = handler.stats
    by_segment = {segment["index"]: segment for segment in handler.segments}
    for point in points:
        own = _tic(KEYS[point["segment"]])
        assert own < _tic()
        assert point["calibrant_to_tic"] == pytest.approx(
            point["sample_peak_intensity"] / own, rel=1e-12
        )
        assert by_segment[point["segment"]]["quality"]["calibrant_to_tic"] == (
            pytest.approx(point["calibrant_to_tic"], rel=1e-12)
        )
    # The file's is over the scans its calibrants were kept in: no sum of
    # shares of three different ion currents
    kept_in = sum(_tic(KEYS[index]) for index in ASKED)
    assert summary["calibrant_to_tic"] == pytest.approx(
        sum(point["sample_peak_intensity"] for point in points) / kept_in, rel=1e-12
    )
    assert summary["calibrant_to_tic"] < sum(
        point["calibrant_to_tic"] for point in points
    )


@pytest.mark.asyncio
async def test_a_file_of_one_fitted_stream_has_that_streams_share(fitted):
    handler = await fitted([TARGETS[0]])

    (point, summary) = handler.stats
    assert summary["calibrant_to_tic"] == pytest.approx(
        point["calibrant_to_tic"], rel=1e-12
    )


@pytest.mark.asyncio
async def test_a_stream_is_fitted_on_a_calibrant_its_composite_takes_elsewhere(fitted):
    """The reagent scan holds the ion at 80, which the low window owns: it
    is a calibrant of the reagent scan all the same, and of the low window."""
    handler = await fitted([("ion-80", "C2HO4-", 80.0 * (1 + 4e-6))])

    assert [point["segment"] for point in handler.stats[:-1]] == [REAGENT, LOW]
    streams = handler.fit_result["streams"]
    assert streams[KEYS[REAGENT]]["calibration_factor"] == pytest.approx(
        1 + 4e-6, rel=1e-15
    )
    assert streams[KEYS[LOW]]["calibration_factor"] == pytest.approx(
        80.0 * (1 + 4e-6) / IN_LOW, rel=1e-15
    )
    # Fitted each on its own reading, they put the ion on one m/z
    assert 80.0 * streams[KEYS[REAGENT]]["calibration_factor"] == pytest.approx(
        IN_LOW * streams[KEYS[LOW]]["calibration_factor"], rel=1e-15
    )


@pytest.mark.asyncio
async def test_with_no_calibrant_in_any_stream_there_is_no_fit(fitted):
    handler = await fitted([("ion-none", "C9H9O9-", 333.0)])

    assert handler.fit_result is None
    assert handler.warning == "No calibration peaks found"
    assert handler.segments is None


def _turned_down(reasons):
    """Have the fit of each stream named turn its calibrants down, saying why."""
    fit_retained = OrbiCalibrationHandler._fit_retained_matches

    def turned_down(self, matches_df):
        labels = set(matches_df["target_ion_id"])
        for ion, reason in reasons.items():
            if ion in labels:
                self.warning = reason
                return None, matches_df.iloc[0:0]
        return fit_retained(self, matches_df)

    return patch.object(OrbiCalibrationHandler, "_fit_retained_matches", turned_down)


NO_SUBSET = "No suitable subset of calibration peaks found; skipping calibration."


@pytest.mark.asyncio
async def test_with_no_stream_fitted_the_reason_a_stream_gave_is_answered(fitted):
    """The one stream that holds calibrants had them turned down. That is
    what a file fitted as one answers, and what the dialog and the pipeline
    are told: not that none were found."""
    with _turned_down({"ion-reagent": NO_SUBSET}):
        handler = await fitted([TARGETS[0]])

    assert handler.fit_result is None and handler.segments is None
    assert handler.warning == NO_SUBSET


@pytest.mark.asyncio
async def test_streams_that_gave_different_reasons_are_each_named(fitted):
    after_filtering = "No calibration peaks found after filtering."
    with _turned_down({"ion-reagent": NO_SUBSET, "ion-high": after_filtering}):
        handler = await fitted([TARGETS[0], TARGETS[2]])

    assert handler.fit_result is None
    assert handler.warning == (
        f"{LABELS[REAGENT]}: {NO_SUBSET}; {LABELS[HIGH]}: {after_filtering}"
    )


@pytest.mark.asyncio
async def test_a_stream_fitted_beside_one_turned_down_carries_the_fit(fitted):
    """One stream's reason is no reason of the file's while another is
    fitted: it is that stream's note."""
    with _turned_down({"ion-reagent": NO_SUBSET}):
        handler = await fitted()

    assert handler.fit_result is not None and handler.warning is None
    assert handler.segments[REAGENT]["note"] == NO_SUBSET
    assert handler.segments[REAGENT]["source"] != "anchors"


@pytest.mark.asyncio
async def test_a_fit_starts_from_the_factor_each_stream_already_has(fitted):
    """A file calibrated before: each stream's fit is over its own factor."""
    _apply(_fit())

    handler = await fitted()

    streams = handler.fit_result["streams"]
    for index, asked in ASKED.items():
        assert streams[KEYS[index]]["calibration_factor"] == pytest.approx(
            asked, rel=1e-12
        )
    assert handler.fit_result["par"]["old_factor"] == FILE
    assert handler.fit_result["par"]["calibration_factor"] == pytest.approx(
        ASKED[REAGENT], rel=1e-12
    )


@pytest.mark.asyncio
async def test_a_calibrant_set_apart_does_not_move_its_stream_at_the_next_fit(fitted):
    """The reagent scan's reading of the ion at 80 passed the low window's
    when each was moved by its own factor, and gave way: its row lies twenty
    ppm from where the reagent scan's factor puts it. That ion is then a
    calibrant, with its target exactly there. The reagent scan read it
    right, and its factor stays; fitted on the row's place it would be moved
    by the twenty ppm the two streams disagree."""
    reagent, low = 1 + 10e-6, 1 - 10e-6
    await asyncio.to_thread(
        _apply, _fit(file=1.0, streams={REAGENT: reagent, LOW: low})
    )
    rows = store_rows()
    passed = int(np.flatnonzero((rows.stream == REAGENT) & (rows.recorded == 80.0))[0])
    assert rows.mz[passed] == pytest.approx(IN_LOW * low, rel=1e-9)

    handler = await fitted([("ion-80", "C2HO4-", 80.0 * reagent)])

    streams = handler.fit_result["streams"]
    assert streams[KEYS[REAGENT]]["calibration_factor"] == pytest.approx(
        reagent, rel=1e-13
    )
    point = next(row for row in handler.stats[:-1] if row["segment"] == REAGENT)
    assert point["sample_peak_mz"] == pytest.approx(80.0 * reagent, rel=1e-13)
    assert point["match_mz_error"] == pytest.approx(0.0, abs=1e-6)


def _weaken(index):
    """Flag every peak of one stream weak, as a range that read nothing
    above the noise has them."""
    path = m_name.filename_to_zarr_path(SAMPLE_FILENAME, "peak_timeseries")
    store = zarr.open_group(path, mode="r+")
    weak = store["is_weak"][:]
    weak[store["stream"][:] == index] = True
    store["is_weak"][:] = weak


@pytest.mark.asyncio
async def test_a_range_that_reads_nothing_above_the_noise_still_takes_a_factor(fitted):
    """It is a stream of the file whatever a load of the store keeps of it:
    its signal and its rows are moved with a neighbour's, and the fit says
    so, where left out it would go by the file's factor unsaid."""
    _weaken(MID)

    handler = await fitted()

    assert set(handler.fit_result["streams"]) == set(KEYS)
    mid = handler.segments[MID]
    assert [segment["label"] for segment in handler.segments] == LABELS
    assert (mid["source"], mid["origin"]) == ("borrowed", LABELS[REAGENT])
    assert mid["note"] == "No calibration peaks found"
    assert handler.fit_result["streams"][KEYS[MID]]["calibration_factor"] == (
        pytest.approx(ASKED[REAGENT], rel=1e-15)
    )


@pytest.mark.asyncio
async def test_a_range_with_no_kept_peak_is_not_searched_for_calibrants(
    fitted, monkeypatch
):
    _weaken(MID)
    searched = []
    original = OrbiCalibrationHandler._match_calibration_compounds

    async def counted(self, isotopes, stream=None):
        searched.append(stream)
        return await original(self, isotopes, stream=stream)

    monkeypatch.setattr(OrbiCalibrationHandler, "_match_calibration_compounds", counted)

    await fitted()

    assert searched == [KEYS[REAGENT], KEYS[LOW], KEYS[HIGH]]


@pytest.mark.asyncio
async def test_a_fit_applied_puts_every_calibrant_on_its_target(fitted):
    handler = await fitted()

    await asyncio.to_thread(_apply, handler.fit_result)
    rows = store_rows()

    for _ion, _formula, target in TARGETS:
        nearest = rows.mz[np.argmin(np.abs(rows.mz - target))]
        assert nearest == pytest.approx(target, rel=1e-13)
    assert m_factor.stream_factor(_record(), KEYS[MID]) == pytest.approx(
        ASKED[REAGENT], rel=1e-15
    )


@pytest.mark.asyncio
async def test_a_polarity_the_store_holds_one_stream_of_names_that_stream(
    acquire, instrument_functions
):
    """The file's other polarity: one stream, fitted as one, and named, so
    that the fit says whose factor it is."""
    positive = "FTMS + p NSI Full ms [40.0000-600.0000]"
    acquire(COMPOSITE + [(positive, 5, {200.0: 80.0})] * 2, {**MICROSCANS, 5: 1})
    # Off the loop: a detection runs one of its own
    await asyncio.to_thread(
        m_peak.compute_peaks, SAMPLE_FILENAME, instrument_functions, None, True
    )
    params = CalibrationFitParams(
        calibration_collection_id="tc-1",
        ionization_mechanism_ids=["im-1"],
        polarity="+",
        **OrbiCalibrationParams().model_dump(),
    )
    handler = OrbiCalibrationHandler(SAMPLE_FILENAME, params)
    handler._resolve_calibration_isotopes = AsyncMock(
        return_value=_isotopes([("ion-p", "C9H9O5+", 200.0 * (1 + 2e-6))])
    )
    resolution = AsyncMock(return_value=(None, lambda mz: np.full_like(mz, 1e5)))

    with patch.object(calibration_mz_fit, "read_instrument_functions", resolution):
        await handler.fit()

    assert list(handler.fit_result["streams"]) == [f"{positive} R=120000"]
    assert [segment["source"] for segment in handler.segments] == ["anchors"]


# -- what the fit answers ------------------------------------------------------------


class _Session:
    """A database session that knows one ionization mode."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, _model, _key):
        return SimpleNamespace(
            ionization_mode_id="im-1",
            calibration_collection_id="tc-1",
            ionization_mechanism_ids=["mech-1"],
            ionization_mode_polarity="-",
        )


async def _answered(segments, correction=2.0):
    """What ``calibration_mz_fit`` answers for a handler that fitted one
    calibrant and says ``segments`` of the file's streams."""
    point = {
        "mz": 62.0,
        "target_ion_id": "ion-reagent",
        "match_mz_error": correction,
        "calibration_mz_error": 0.0,
        "calibrant_to_tic": 0.1,
    }
    summary = {key: value for key, value in point.items() if key != "mz"}
    handler = SimpleNamespace(
        fit=AsyncMock(),
        segments=segments,
        to_dict=lambda: {
            "fit": _fit(file=1 + correction * 1e-6),
            "stats": [point, summary],
            "error": None,
            "warning": None,
        },
    )
    return await _answer(handler)


async def _answer(handler):
    """What ``calibration_mz_fit`` answers for the fit ``handler`` holds."""
    sample = SimpleNamespace(
        sample_item_id="si-1",
        sample_item_name="composite",
        sample_file_id="sf-1",
        filename=SAMPLE_FILENAME,
        ionization_mode_id="im-1",
    )
    with (
        patch.object(
            calibration_controller, "fetch_sample", AsyncMock(return_value=sample)
        ),
        patch.object(calibration_controller, "async_session", _Session),
        patch.object(
            calibration_controller,
            "fetch_affected_sample_data",
            AsyncMock(return_value=([], [], [], [])),
        ),
        patch.object(
            calibration_controller, "get_calibration_handler", lambda *a: handler
        ),
        patch.object(calibration_controller, "derive_token_secret", lambda _: "key"),
    ):
        answer = await calibration_controller.calibration_mz_fit(
            sample_item_id="si-1",
            mz_calibration_params=MzCalibrationParams(refine_window=50),
        )
    return answer["data"]["fit"]


@pytest.mark.asyncio
async def test_the_fits_answer_says_how_each_stream_came_by_its_factor():
    segments = [_segment(REAGENT, points=1, ions=1), _segment(MID, source="borrowed")]

    fit = await _answered(segments)

    assert fit["quality"]["segments"] == segments
    assert fit["quality"]["n_points"] == 1
    assert fit["quality_issues"] == []
    # Sealed with them, so the verdict an apply reaches is of this answer
    with patch.object(calibration_controller, "derive_token_secret", lambda _: "key"):
        assert calibration_controller.fit_seal_valid(fit, SAMPLE_FILENAME)
        fit["quality"]["segments"][1]["source"] = "anchors"
        assert not calibration_controller.fit_seal_valid(fit, SAMPLE_FILENAME)


@pytest.mark.asyncio
async def test_the_fits_answer_previews_a_streams_issue():
    """Before the fit is applied, the dialog is told what it would be stored
    with: a stream fitted on one point thirty ppm off among them."""
    segments = [_segment(HIGH, points=1, ions=1, correction=30.0)]

    fit = await _answered(segments)

    (issue,) = fit["quality_issues"]
    assert issue["segment"] == KEYS[HIGH]


@pytest.mark.asyncio
async def test_a_file_of_one_fitted_stream_previews_its_issue_once(fitted, monkeypatch):
    """The reagent scan alone holds a calibrant, so the file's calibrants
    are the reagent scan's, and a correction its one point cannot vouch
    for is the reagent scan's issue: said once, where it is named."""
    monkeypatch.setattr(calibration_config, "LOW_POINT_MAX_AXIS_CORRECTION_PPM", 1.0)
    handler = await fitted([TARGETS[0]])
    handler.fit = AsyncMock()

    fit = await _answer(handler)

    segments = fit["quality"]["segments"]
    assert [s["label"] for s in segments if s["source"] == "anchors"] == [
        LABELS[REAGENT]
    ]
    (issue,) = fit["quality_issues"]
    assert issue["code"] == "points"
    assert issue["segment"] == KEYS[REAGENT]
    assert issue["message"].startswith(
        f"{LABELS[REAGENT]}: Fitted on 1 calibration point but moves the m/z "
        "axis 2.00 ppm"
    )


@pytest.mark.asyncio
async def test_a_file_fitted_as_one_answers_no_segments():
    fit = await _answered(None)

    assert "segments" not in fit["quality"]


# -- the quality bar -----------------------------------------------------------------


def _block(points=3, ions=3, residual=0.1, correction=1.0):
    return {
        "n_points": points,
        "n_ions": ions,
        "pre_fit_mz_error_ppm": abs(correction),
        "post_fit_mz_error_ppm": residual,
        "calibrant_to_tic": 0.1,
        "axis_correction_ppm": correction,
    }


def _segment(index, source="anchors", **block):
    return {
        "index": index,
        "key": KEYS[index],
        "label": LABELS[index],
        "source": source,
        "quality": _block(**block) if source == "anchors" else None,
    }


def test_a_fit_whose_streams_all_clear_the_bar_has_no_issue():
    quality = {
        **_block(),
        "segments": [
            _segment(REAGENT, points=1, ions=1),
            _segment(LOW, points=1, ions=1),
            _segment(MID, source="borrowed"),
            _segment(HIGH, source="overlap"),
        ],
    }

    assert calibration_quality_issues(quality, SAMPLE_FILENAME) == []


def test_a_stream_fitted_on_one_point_far_off_is_an_issue_of_the_file():
    """One stream anchored to a wrong peak moves that stream's peaks,
    whatever the file's calibrants as a whole say: here no stream fitted on
    calibrants of several ions agrees with it."""
    quality = {
        **_block(),
        "segments": [
            _segment(REAGENT, points=1, ions=1),
            _segment(HIGH, points=1, ions=1, correction=30.0),
        ],
    }

    (issue,) = calibration_quality_issues(quality, SAMPLE_FILENAME)

    assert issue["code"] == "points"
    assert issue["segment"] == KEYS[HIGH]
    assert issue["message"].startswith("m/z 440-900: Fitted on 1 calibration point")


# -- a stream vouched for by another of its file -------------------------------
#
# An instrument whose axis is 22 ppm off reads that offset in every scan
# range; a stream fitted on one calibrant that reads it too found the right
# peak, where one anchored to a wrong peak lands tens of ppm away.


def test_a_stream_on_one_point_is_vouched_for_by_one_on_several_ions_that_agrees():
    """The file that showed the drift badge as one fit: two ranges on one
    calibrant each, both moved 22 ppm, as the ranges fitted on several ions
    were."""
    quality = {
        **_block(points=6, ions=5, correction=22.44),
        "segments": [
            _segment(REAGENT, points=2, ions=2, correction=22.72),
            _segment(LOW, points=1, ions=1, correction=22.43),
            _segment(MID, points=2, ions=2, correction=22.53),
            _segment(HIGH, points=1, ions=1, correction=21.94),
        ],
    }

    assert calibration_quality_issues(quality, SAMPLE_FILENAME) == []


def test_a_stream_that_does_not_agree_is_not_vouched_for():
    quality = {
        **_block(),
        "segments": [
            _segment(REAGENT, points=2, ions=2, correction=22.5),
            _segment(HIGH, points=1, ions=1, correction=25.0),
        ],
    }

    (issue,) = calibration_quality_issues(quality, SAMPLE_FILENAME)

    assert issue["code"] == "points"
    assert issue["segment"] == KEYS[HIGH]


def test_the_agreement_limit_itself_vouches():
    limit = calibration_config.STREAM_CORROBORATION_PPM
    quality = {
        **_block(),
        "segments": [
            _segment(REAGENT, points=2, ions=2, correction=22.5),
            _segment(HIGH, points=1, ions=1, correction=22.5 + limit),
            _segment(LOW, points=1, ions=1, correction=22.5 - limit - 0.01),
        ],
    }

    (issue,) = calibration_quality_issues(quality, SAMPLE_FILENAME)

    assert issue["segment"] == KEYS[LOW]


def test_a_stream_on_one_point_does_not_vouch_for_another():
    """Two lone points that agree are two fits nothing checks."""
    quality = {
        **_block(),
        "segments": [
            _segment(REAGENT, points=1, ions=1, correction=22.0),
            _segment(HIGH, points=1, ions=1, correction=22.3),
        ],
    }

    issues = calibration_quality_issues(quality, SAMPLE_FILENAME)

    assert [issue["segment"] for issue in issues] == [KEYS[REAGENT], KEYS[HIGH]]


def test_a_lone_point_that_clears_the_bar_does_not_vouch_either():
    """One point within the correction a lone point may make clears the
    bar, but nothing checks it either, so it cannot vouch for a neighbour
    just beyond that correction."""
    quality = {
        **_block(),
        "segments": [
            _segment(REAGENT, points=1, ions=1, correction=9.5),
            _segment(HIGH, points=1, ions=1, correction=11.0),
        ],
    }

    (issue,) = calibration_quality_issues(quality, SAMPLE_FILENAME)

    assert issue["segment"] == KEYS[HIGH]


def test_a_stream_below_the_bar_does_not_vouch():
    quality = {
        **_block(),
        "segments": [
            _segment(REAGENT, points=2, ions=2, residual=2.5, correction=22.5),
            _segment(HIGH, points=1, ions=1, correction=22.0),
        ],
    }

    issues = calibration_quality_issues(quality, SAMPLE_FILENAME)

    assert [(issue["segment"], issue["code"]) for issue in issues] == [
        (KEYS[REAGENT], "residual"),
        (KEYS[HIGH], "points"),
    ]


def test_a_stream_that_took_a_neighbours_factor_does_not_vouch():
    """Its factor is a copy of another's, not a reading of its own."""
    quality = {
        **_block(),
        "segments": [
            {
                **_segment(MID, source="borrowed"),
                "quality": _block(points=2, ions=2, correction=22.4),
            },
            _segment(HIGH, points=1, ions=1, correction=22.0),
        ],
    }

    (issue,) = calibration_quality_issues(quality, SAMPLE_FILENAME)

    assert issue["segment"] == KEYS[HIGH]


def test_isotopes_of_one_ion_are_vouched_for_as_one_point_is():
    quality = {
        **_block(),
        "segments": [
            _segment(REAGENT, points=2, ions=2, correction=22.5),
            _segment(LOW, points=2, ions=1, correction=22.1),
            _segment(HIGH, points=4, ions=1, correction=22.2),
        ],
    }

    assert calibration_quality_issues(quality, SAMPLE_FILENAME) == []


def test_a_vouched_stream_is_still_judged_on_its_residual():
    quality = {
        **_block(),
        "segments": [
            _segment(REAGENT, points=2, ions=2, correction=22.5),
            _segment(HIGH, points=2, ions=1, residual=2.5, correction=22.0),
        ],
    }

    (issue,) = calibration_quality_issues(quality, SAMPLE_FILENAME)

    assert (issue["segment"], issue["code"]) == (KEYS[HIGH], "residual")


def test_a_stream_whose_correction_is_not_recorded_is_not_vouched_for():
    """Its pre-fit error stands in for the shift it is judged on, but a
    refit reads that on the axis an earlier fit set, not from the acquisition
    axis the other streams' corrections are measured from."""
    unrecorded = _segment(HIGH, points=1, ions=1, correction=22.0)
    unrecorded["quality"]["axis_correction_ppm"] = None
    quality = {
        **_block(),
        "segments": [_segment(REAGENT, points=2, ions=2, correction=22.5), unrecorded],
    }

    (issue,) = calibration_quality_issues(quality, SAMPLE_FILENAME)

    assert issue["segment"] == KEYS[HIGH]


def test_the_fit_as_a_whole_is_vouched_for_by_nothing():
    """Vouching is between the streams of a file; the file's own block is
    judged on its calibrants as before."""
    quality = {
        **_block(points=1, ions=1, correction=22.0),
        "segments": [_segment(REAGENT, points=2, ions=2, correction=22.0)],
    }

    (issue,) = calibration_quality_issues(quality, SAMPLE_FILENAME)

    assert issue["code"] == "points"
    assert "segment" not in issue


def test_a_streams_residual_is_judged_by_the_files_bound():
    quality = {**_block(), "segments": [_segment(LOW, residual=2.5)]}

    (issue,) = calibration_quality_issues(quality, SAMPLE_FILENAME)

    assert issue["code"] == "residual"
    assert issue["segment"] == KEYS[LOW]


def test_a_stream_that_took_a_neighbours_factor_has_no_fit_to_judge():
    quality = {
        **_block(),
        "segments": [
            {**_segment(MID, source="borrowed"), "quality": _block(residual=9.0)}
        ],
    }

    assert calibration_quality_issues(quality, SAMPLE_FILENAME) == []


def test_the_fit_as_a_whole_is_judged_as_before():
    quality = {**_block(residual=2.5), "segments": [_segment(LOW)]}

    (issue,) = calibration_quality_issues(quality, SAMPLE_FILENAME)

    assert issue["code"] == "residual"
    assert "segment" not in issue


# -- a file of one fitted stream -----------------------------------------------
#
# With one stream fitted on calibrants of its own, the file's calibrants are
# that stream's: the file's block is the stream's, and so are its issues.
# A file's block that reads otherwise than its one stream's is none a fit
# makes; the tests that hand it one hold that what the stream does not say
# is kept all the same.


def test_the_issue_of_a_file_of_one_fitted_stream_is_said_once_by_the_stream():
    quality = {
        **_block(residual=1.27),
        "segments": [_segment(LOW, residual=1.27), _segment(HIGH, source="overlap")],
    }

    assert calibration_quality_issues(quality, SAMPLE_FILENAME) == [
        {
            "code": "residual",
            "message": (
                f"{LABELS[LOW]}: Mean m/z error after calibration is 1.27 ppm "
                "(limit 1 ppm)."
            ),
            "segment": KEYS[LOW],
        }
    ]


def test_a_file_of_two_fitted_streams_keeps_its_own_issue():
    """Its block is over both streams' calibrants, a reading of its own."""
    quality = {
        **_block(residual=1.27),
        "segments": [_segment(REAGENT, residual=1.27), _segment(LOW, residual=1.27)],
    }

    issues = calibration_quality_issues(quality, SAMPLE_FILENAME)

    assert [(issue.get("segment"), issue["code"]) for issue in issues] == [
        (None, "residual"),
        (KEYS[REAGENT], "residual"),
        (KEYS[LOW], "residual"),
    ]


def test_an_issue_of_the_file_its_one_fitted_stream_does_not_have_is_kept():
    quality = {
        **_block(points=1, ions=1, residual=1.27, correction=22.0),
        "segments": [_segment(LOW, points=2, ions=2, residual=1.27, correction=22.0)],
    }

    issues = calibration_quality_issues(quality, SAMPLE_FILENAME)

    assert [(issue.get("segment"), issue["code"]) for issue in issues] == [
        (None, "points"),
        (KEYS[LOW], "residual"),
    ]


def test_an_issue_of_the_file_that_reads_otherwise_than_its_streams_is_kept():
    """The same criterion read otherwise is not the stream's issue."""
    quality = {**_block(residual=2.5), "segments": [_segment(LOW, residual=1.27)]}

    issues = calibration_quality_issues(quality, SAMPLE_FILENAME)

    assert [issue["message"] for issue in issues] == [
        "Mean m/z error after calibration is 2.50 ppm (limit 1 ppm).",
        f"{LABELS[LOW]}: Mean m/z error after calibration is 1.27 ppm (limit 1 ppm).",
    ]
