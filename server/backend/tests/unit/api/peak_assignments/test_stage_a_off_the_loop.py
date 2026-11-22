"""Stage A's heavy pandas steps run off the event loop.

Dropping the ions the pre-passes claimed and arbitrating the frame into rows
take seconds each on a large frame. On the event loop they held every other
request of the server behind a run, and a thin Orbitrap file's pool runs them
once per neighbour as well (MAS-721). They run in a worker thread
(``asyncio.to_thread``), which carries the run's context - the labelled purity,
a decoy's shifted axis - along.
"""

import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from mascope_backend.api.new.peak_assignments import pipeline, stage_a_pass
from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig
from mascope_backend.api.new.peak_assignments.engine import SampleMassAccuracy
from mascope_backend.api.new.peak_assignments.envelope_fit import RunEnvelopeFit
from mascope_tools.composition.calibration import Calibration
from mascope_tools.composition.envelope_tolerance import (
    FORM_CURVE,
    EnvelopeLines,
    fit_envelope_tolerance,
)


FRAME = pd.DataFrame({"target_ion_id": ["ion-1"], "sample_peak_id": ["p1"]})


def _recording(threads: dict, name: str, result):
    """A stand-in that records which thread it ran on."""

    def run(*args, **kwargs):
        threads[name] = threading.current_thread()
        return result(*args) if callable(result) else result

    return MagicMock(side_effect=run)


def _first_pass_patches(
    threads: dict, rows: list[dict], calibration: Calibration | None = None
):
    return [
        patch(
            "mascope_backend.api.new.peak_assignments.stage_a_pass._fetch_known_isotopes",
            AsyncMock(return_value=FRAME.copy()),
        ),
        patch(
            "mascope_backend.api.new.peak_assignments.stage_a_pass.compute_match_isotopes",
            AsyncMock(return_value=FRAME.copy()),
        ),
        patch(
            "mascope_backend.api.new.peak_assignments.stage_a_pass.drop_ions_claimed_elsewhere",
            _recording(threads, "drop", lambda df, *_: df),
        ),
        patch(
            "mascope_backend.api.new.peak_assignments.stage_a_pass.gate_lines",
            side_effect=lambda df, *_args, **_kwargs: df,
        ),
        patch(
            "mascope_backend.api.new.peak_assignments.stage_a_pass.score_ions_by_fit",
            side_effect=lambda df, **_kwargs: df,
        ),
        patch(
            "mascope_backend.api.new.peak_assignments.stage_a_pass.sample_mass_accuracy",
            return_value=SampleMassAccuracy(),
        ),
        patch(
            "mascope_backend.api.new.peak_assignments.stage_a_pass.get_instrument_type",
            return_value="orbi",
        ),
        patch(
            "mascope_backend.api.new.peak_assignments.stage_a_pass.load_calibration",
            AsyncMock(return_value=calibration),
        ),
        patch(
            "mascope_backend.api.new.peak_assignments.stage_a_pass.invert_matches_to_peak_assignments",
            _recording(threads, "arbitrate", rows),
        ),
    ]


async def _first_pass(
    threads: dict, rows: list[dict], calibration: Calibration | None = None
):
    patches = _first_pass_patches(threads, rows, calibration)
    for started in patches:
        started.start()
    try:
        return await stage_a_pass._stage_a_first_pass(
            SimpleNamespace(
                sample_item_id="si-1", filename="orbi-sample.raw", polarity="-"
            ),
            PeakAssignmentConfig(),
            SimpleNamespace(isotope_abundance_threshold=0.01),
            ["m-1"],
            [],
            "run-1",
            excluded_peak_ids={"p-reagent"},
            fallback_sigma_ppm=0.6,
            known_window=None,
        )
    finally:
        for started in patches:
            started.stop()


@pytest.mark.asyncio
async def test_the_first_pass_drops_claimed_ions_and_arbitrates_off_the_loop():
    threads: dict = {}
    rows = [{"role": "M0", "sample_peak_id": "p1"}]

    first = await _first_pass(threads, rows)

    assert first.rows == rows
    assert set(threads) == {"drop", "arbitrate"}
    assert all(t is not threading.main_thread() for t in threads.values())


@pytest.mark.asyncio
async def test_the_first_pass_keeps_the_curve_its_rows_were_read_off():
    # A re-score at v2 reads the rows' P(correct) again off the same curve.
    calibration = Calibration(a=5.74, b=-3.36, instrument="orbi")

    first = await _first_pass({}, [{"role": "M0"}], calibration)
    none = await _first_pass({}, [{"role": "M0"}])

    assert first.curve.calibration is calibration
    assert none.curve is None


@pytest.mark.asyncio
async def test_the_rescore_at_the_fitted_tolerance_arbitrates_off_the_loop():
    threads: dict = {}
    rescored = [{"role": "M0", "sample_peak_id": "p1", "fit_score": 0.9}]
    first = stage_a_pass._StageAFirstPass(
        rows=[{"role": "M0", "sample_peak_id": "p1", "fit_score": 0.5}],
        scored=FRAME,
        matched=FRAME,
        mass_accuracy=SampleMassAccuracy(),
        confidence_calibration=None,
        listed_formulas=frozenset(),
        assignments_of=_recording(threads, "arbitrate", rescored),
        curve=stage_a_pass.ConfidenceCurve(Calibration(a=1.0, b=0.0), {}),
    )
    fitted = RunEnvelopeFit(
        fit_envelope_tolerance(
            FORM_CURVE, EnvelopeLines(np.full(30, 15.0), np.full(30, 0.75))
        )
    )
    with (
        patch(
            "mascope_backend.api.new.peak_assignments.pipeline._stage_a_first_pass",
            AsyncMock(return_value=first),
        ),
        patch(
            "mascope_backend.api.new.peak_assignments.pipeline.fit_run_envelope_tolerance",
            AsyncMock(return_value=fitted),
        ),
        patch(
            "mascope_backend.api.new.peak_assignments.pipeline.gate_lines",
            side_effect=lambda df, *_args, **_kwargs: df,
        ),
        patch(
            "mascope_backend.api.new.peak_assignments.pipeline.score_ions_by_fit",
            side_effect=lambda df, **_kwargs: df,
        ),
    ):
        rows, _, _, envelope, _, _, curve = await pipeline._stage_a_assignments(
            SimpleNamespace(sample_item_id="si-1", filename="orbi-sample.raw"),
            PeakAssignmentConfig(),
            SimpleNamespace(isotope_abundance_threshold=0.01),
            ["m-1"],
            [],
            "run-1",
            fallback_sigma_ppm=0.6,
            known_window=None,
        )

    assert (rows, envelope) == (rescored, fitted)
    # The curve the rows' P(correct) was read off goes on to the run.
    assert curve is first.curve
    assert threads["arbitrate"] is not threading.main_thread()
