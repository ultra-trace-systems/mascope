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
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import numpy as np
import pandas as pd
import pytest
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
    assert after.mz[taken] == pytest.approx(
        before.mz[taken] * factors[before.stream[taken]], rel=1e-15
    )
    assert m_factor.stream_factors(_record(), KEYS).tolist() == factors.tolist()


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
    a calibration across. It lies against the high window."""
    handler = await fitted()

    mid = handler.segments[MID]
    assert handler.fit_result["streams"][KEYS[MID]] == {
        "calibration_factor": pytest.approx(ASKED[HIGH], rel=1e-15)
    }
    assert mid["source"] == "borrowed"
    assert mid["origin"] == LABELS[HIGH]
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
async def test_a_fit_applied_puts_every_calibrant_on_its_target(fitted):
    handler = await fitted()

    await asyncio.to_thread(_apply, handler.fit_result)
    rows = store_rows()

    for _ion, _formula, target in TARGETS:
        nearest = rows.mz[np.argmin(np.abs(rows.mz - target))]
        assert nearest == pytest.approx(target, rel=1e-13)
    assert m_factor.stream_factor(_record(), KEYS[MID]) == pytest.approx(
        ASKED[HIGH], rel=1e-15
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
    whatever the file's other calibrants say."""
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
