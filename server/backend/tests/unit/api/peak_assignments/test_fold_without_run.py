"""Unit tests for the run-less ingest fold.

``fold_sample_peaks_without_run`` is what the ingest hook calls under
``peak_assignment_ingest_ledger = "batch"``: Stage A in memory, the unexplained
peaks as placeholders, and the whole handed to the batch fold with nothing
written to ``peak_assignment``. Everything it reads is patched, so these pin
the contract between the hook and the fold rather than the engine.
"""

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

from mascope_backend.api.new.peak_assignments.fold_view import fold_run_id


_SVC = "mascope_backend.api.new.peak_assignments.service"
_CTL = "mascope_backend.api.new.peak_assignments.batch_peaks_controller"


def _stage_a_row(sample_item_id: str, run_id: str) -> dict:
    """One row as Stage A shapes it for the bulk insert it will never see."""
    return {
        "peak_assignment_id": "a" * 32,
        "peak_assignment_run_id": run_id,
        "sample_item_id": sample_item_id,
        "sample_peak_id": "p1",
        "sample_peak_mz": 181.0707,
        "sample_peak_intensity": 5000.0,
        "sample_peak_tof": None,
        "role": "M0",
        "assigned_formula": "C6H12O6",
        "ion_formula": "C6H13O6+",
        "ionization_mechanism_id": None,
        "isotope_label": "M0",
        "isotope_formula": None,
        "source": "database",
        "fit_score": 0.95,
        "mz_error_ppm": 1.0,
        "abundance_error": 0.0,
        "tier": "assigned",
        "target_compound_id": None,
        "target_ion_id": None,
        "owner_peak_assignment_id": None,
        "alternatives": None,
        "provenance": {"p_correct": 0.9},
    }


def _patched(ineligible: str | None = None):
    """Patch everything the fold reads; return the stack and its mocks."""
    stack = ExitStack()
    sample = SimpleNamespace(
        sample_item_id="si-1", sample_item_name="S1", filename="f.zarr", polarity="+"
    )
    mocks = {
        "fetch": stack.enter_context(
            patch(f"{_SVC}.fetch_sample", new_callable=AsyncMock, return_value=sample)
        ),
        "ineligible": stack.enter_context(
            patch(f"{_SVC}.ineligible_reason", return_value=ineligible)
        ),
        "params": stack.enter_context(
            patch(
                f"{_SVC}.default_match_params",
                new_callable=AsyncMock,
                return_value=SimpleNamespace(isotope_abundance_threshold=0.01),
            )
        ),
        "peaks": stack.enter_context(
            patch(
                f"{_SVC}.load_sample_peaks",
                return_value=pd.DataFrame(
                    {
                        "sample_peak_id": ["p1", "p2"],
                        "mz": [181.0707, 250.1],
                        "intensity": [5000.0, 300.0],
                    }
                ),
            )
        ),
        "mechanisms": stack.enter_context(
            patch(
                f"{_SVC}.fetch_sample_mechanisms",
                new_callable=AsyncMock,
                return_value=(["m-h"], []),
            )
        ),
        "stage_a": stack.enter_context(
            patch(f"{_SVC}._stage_a_assignments", new_callable=AsyncMock)
        ),
        "fold": stack.enter_context(
            patch(
                f"{_CTL}.fold_sample_into_batch_peaks",
                new_callable=AsyncMock,
                return_value="batch-1",
            )
        ),
    }
    mocks["stage_a"].return_value = ([_stage_a_row("si-1", fold_run_id("si-1"))], None)
    return stack, mocks


@pytest.mark.asyncio
async def test_folds_stage_a_and_the_placeholders_without_persisting():
    from mascope_backend.api.new.peak_assignments.service import (
        fold_sample_peaks_without_run,
    )

    stack, mocks = _patched()
    with stack:
        assert await fold_sample_peaks_without_run("si-1") == "batch-1"

    fold = mocks["fold"]
    fold.assert_awaited_once()
    args, kwargs = fold.call_args
    assert args == ("si-1",)
    # Nothing was written to peak_assignment, and the fold is told so.
    assert kwargs["persisted"] is False
    rows = kwargs["rows"]
    assert [row.sample_peak_id for row in rows] == ["p1", "p2"]
    assert rows[0].assigned_formula == "C6H12O6"
    assert rows[0].provenance == {"p_correct": 0.9}
    # The unexplained peak is a placeholder, as it is in a run's ledger.
    assert rows[1].role == "unassigned" and rows[1].assigned_formula is None
    assert rows[1].sample_peak_intensity == 300.0
    # Every row is stamped with the derived run's id: the run it will never be.
    assert {row.peak_assignment_run_id for row in rows} == {fold_run_id("si-1")}

    # Stage A ran database-first, with the same id to stamp its rows with.
    stage_a = mocks["stage_a"]
    stage_a.assert_awaited_once()
    assert stage_a.call_args.args[1].run_untargeted is False
    assert stage_a.call_args.args[5] == fold_run_id("si-1")


@pytest.mark.asyncio
async def test_an_ineligible_sample_is_skipped_and_nothing_is_written():
    """A blank, or a sample whose calibration is unverified, is refused a run;
    it is refused a fold on the same grounds, and nothing reaches the batch."""
    from mascope_backend.api.new.peak_assignments.service import (
        fold_sample_peaks_without_run,
    )

    stack, mocks = _patched(ineligible="the sample is a blank")
    with stack:
        assert await fold_sample_peaks_without_run("si-1") is None

    mocks["stage_a"].assert_not_called()
    mocks["fold"].assert_not_called()
