"""A run's CPU-heavy stages run off the event loop.

A lag probe over a full run of a demo uronium sample held the loop for 9.5 s
of a 27 s run, 6.7 s of it in one stall: the tiering pass predicting and
asking every committed row's lines (MAS-947). On the loop, every other
request of the worker waited behind it, signed in or not. The judging rounds
(refit, judge, cap), the election, the identification level, the envelope
fit's own lines and the seeded re-score's gate and maps each run in a worker
thread (``asyncio.to_thread``), which carries the run's context along.
"""

import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from mascope_backend.api.new.peak_assignments import (
    envelope_fit,
    pipeline,
    seeded_scoring,
)
from mascope_backend.api.new.peak_assignments.envelope_fit import EnvelopePool
from mascope_backend.api.new.peak_assignments.pipeline import Proposal, Rules
from mascope_tools.composition.envelope_tolerance import (
    FORM_CURVE,
    LEVEL_OWN,
    EnvelopeLines,
    fit_envelope_tolerance,
)
from mascope_tools.composition.mass_accuracy import SampleMassAccuracy
from mascope_tools.composition.measurement_model import MeasurementModel


def _recording(threads: dict, name: str, result=None):
    """A stand-in that records which thread it ran on."""

    def run(*args, **kwargs):
        threads.setdefault(name, []).append(threading.current_thread())
        return result(*args, **kwargs) if callable(result) else result

    return MagicMock(side_effect=run)


def _off_the_loop(threads: dict, *names: str) -> None:
    assert set(threads) == set(names)
    for name in names:
        assert threads[name], name
        assert all(t is not threading.main_thread() for t in threads[name]), name


class _Judging:
    """The judging rounds, each method recording the thread it ran on."""

    def __init__(self, threads: dict, *, another_round: bool):
        self.refit = _recording(threads, "refit")
        self.judge = _recording(threads, "judge")
        self.cap = _recording(threads, "cap")
        self.another_round = lambda: another_round


def _ctx(threads: dict, rules: Rules = Rules.RUN, **fields):
    return SimpleNamespace(
        path=SimpleNamespace(rules=rules),
        judging=_Judging(threads, another_round=True),
        **{
            "config": SimpleNamespace(stage_b_confidence=False),
            "search": None,
            "shortlist": None,
            **fields,
        },
    )


@pytest.mark.asyncio
async def test_the_judging_rounds_run_off_the_loop():
    threads: dict = {}
    ctx = _ctx(threads)

    await pipeline.refit(ctx)
    await pipeline.judge(ctx)
    await pipeline.cap(ctx)

    _off_the_loop(threads, "refit", "judge", "cap")


@pytest.mark.asyncio
async def test_a_persons_choice_is_capped_by_the_spectrum_off_the_loop():
    threads: dict = {}

    with patch.object(
        pipeline, "_cap_by_the_spectrum", _recording(threads, "spectrum")
    ):
        await pipeline.cap(_ctx({}, Rules.SPECTRUM))

    _off_the_loop(threads, "spectrum")


async def _elect(threads: dict, ctx) -> tuple[MagicMock, MagicMock]:
    with (
        patch.object(pipeline, "_elect_given", _recording(threads, "given")) as given,
        patch.object(pipeline, "_elect_rows", _recording(threads, "rows")) as rows,
        patch.object(
            pipeline.given_rivals, "measure_given_rivals", AsyncMock()
        ) as rivals,
    ):
        await pipeline.elect(ctx)
    ctx.rivals = rivals
    return given, rows


@pytest.mark.asyncio
async def test_the_election_runs_off_the_loop():
    threads: dict = {}
    ctx = _ctx({})
    ctx.path.proposal = Proposal.SEARCH

    given, rows = await _elect(threads, ctx)

    _off_the_loop(threads, "rows")
    given.assert_not_called()
    rows.assert_called_once_with(ctx)


@pytest.mark.asyncio
async def test_given_readings_are_elected_off_the_loop():
    threads: dict = {}
    ctx = _ctx({}, seeded=({}, {}, {}, pd.DataFrame()))
    ctx.path.proposal = Proposal.GIVEN

    given, rows = await _elect(threads, ctx)

    _off_the_loop(threads, "given", "rows")
    given.assert_called_once_with(ctx)
    rows.assert_called_once_with(ctx)
    # Their rivals are read by the grid on the elected rows, each grid read
    # off the loop in its own right (``given_rivals.measure_given_rivals``).
    ctx.rivals.assert_awaited_once_with(ctx, pipeline.score_seeds)


@pytest.mark.asyncio
async def test_the_level_is_stated_off_the_loop():
    threads: dict = {}
    summary = {"level_rules_version": 1, "levels": {}, "rivals_not_measured": 0}
    ctx = _ctx(
        {},
        sample=SimpleNamespace(sample_item_name="Sample One"),
        judged=SimpleNamespace(rows=[]),
        search=None,
        config=SimpleNamespace(stage_b_confidence=False),
        searched_mechanisms=[],
        instrument_type="orbi",
        resolved_profile=SimpleNamespace(context=None, profile=None),
        lines=None,
        tiering={},
        resolution=None,
    )

    with patch.object(pipeline, "apply_levels", _recording(threads, "level", summary)):
        await pipeline.level(ctx)

    _off_the_loop(threads, "level")
    assert ctx.tiering[pipeline.LEVEL_SUMMARY] is summary


class _Sample:
    sample_item_id = "si-1"
    sample_item_name = "Sample One"
    sample_batch_id = "sb-1"
    polarity = "-"
    filename = "orbi-sample.raw"


def _thin_fit():
    return fit_envelope_tolerance(FORM_CURVE, EnvelopeLines())


def _own_fit():
    return fit_envelope_tolerance(
        FORM_CURVE, EnvelopeLines(np.full(30, 15.0), np.full(30, 0.75))
    )


async def _fit_the_run(threads: dict, fit, *, settings):
    with (
        patch.object(
            envelope_fit,
            "envelope_lines",
            _recording(threads, "lines", EnvelopeLines()),
        ),
        patch.object(
            envelope_fit, "fit_envelope_tolerance", _recording(threads, "fit", fit)
        ),
        patch.object(
            envelope_fit, "acquisition_settings", AsyncMock(return_value=settings)
        ),
        patch.object(
            envelope_fit, "read_pool", AsyncMock(return_value=EnvelopePool())
        ) as read_pool,
    ):
        result = await envelope_fit.fit_run_envelope_tolerance(
            _Sample(),
            pd.DataFrame(),
            [],
            model=MeasurementModel.v1(SampleMassAccuracy(), 0.6),
            read_neighbour=AsyncMock(),
        )
    return result, read_pool


@pytest.mark.asyncio
async def test_the_runs_own_lines_are_read_and_fitted_off_the_loop():
    threads: dict = {}
    fit = _own_fit()
    assert fit.level == LEVEL_OWN

    result, read_pool = await _fit_the_run(threads, fit, settings={"agc": 1})

    assert result.fit is fit
    read_pool.assert_not_awaited()
    _off_the_loop(threads, "lines", "fit")


@pytest.mark.asyncio
async def test_a_thin_files_pooled_curve_is_fitted_off_the_loop():
    threads: dict = {}
    fit = _thin_fit()
    assert fit.level != LEVEL_OWN

    result, read_pool = await _fit_the_run(threads, fit, settings={"agc": 1})

    read_pool.assert_awaited_once()
    # Its own fit, then the pooled one.
    assert len(threads["fit"]) == 2
    assert result.pool is not None
    _off_the_loop(threads, "lines", "fit")


@pytest.mark.asyncio
async def test_the_seeded_re_score_gates_and_maps_off_the_loop():
    threads: dict = {}
    frame = pd.DataFrame({"target_ion_id": ["ion-1"], "sample_peak_id": ["p1"]})
    maps = ({"ion-1": 0.9}, {("ion-1", "p1"): {}})

    with (
        patch.object(
            seeded_scoring, "fetch_mechanisms_by_id", AsyncMock(return_value={})
        ),
        patch.object(
            seeded_scoring,
            "build_seeded_isotopes_df",
            return_value=(frame, {("C2H4O2", "m-1"): "ion-1"}),
        ),
        patch.object(
            seeded_scoring, "compute_match_isotopes", AsyncMock(return_value=frame)
        ),
        patch.object(
            seeded_scoring, "gate_and_score", _recording(threads, "gate", frame)
        ),
        patch.object(seeded_scoring, "scored_maps", _recording(threads, "maps", maps)),
    ):
        _ion_by_seed, fit_by_ion, errors, scored = await seeded_scoring._score_seeds(
            _Sample(),
            {("C2H4O2", "m-1")},
            SimpleNamespace(isotope_abundance_threshold=0.01),
            model=object(),
            config=None,
            resolution=None,
        )

    assert (fit_by_ion, errors) == maps
    assert scored is frame
    _off_the_loop(threads, "gate", "maps")
