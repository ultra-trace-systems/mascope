"""
Unit tests for the PeakAssignmentRun and PeakAssignment SQLAlchemy models.
Tests creation, the single-owner-per-peak unique constraint, and cascades.
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from test_utils import gen_test_id

from mascope_backend.db import (
    PeakAssignment,
    PeakAssignmentRun,
    SampleFile,
    SampleItem,
)


@pytest_asyncio.fixture
async def db_test_sample_item(session, db_test_sample_batch):
    """Create a sample file + sample item pair for peak assignment tests."""
    sample_file = SampleFile(
        sample_file_id=gen_test_id(),
        filename=f"peak-assign-test-{gen_test_id()}.zarr",
        instrument="orbi-test",
        datetime=datetime(2026, 7, 4, 12, 0, 0),
        datetime_utc=datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc),
        length=60.0,
        range=[50.0, 500.0],
        polarity="+",
    )
    session.add(sample_file)
    await session.flush()

    sample_item = SampleItem(
        sample_item_id=gen_test_id(),
        sample_batch_id=db_test_sample_batch.sample_batch_id,
        sample_file_id=sample_file.sample_file_id,
        sample_item_name="Peak Assignment Test Sample",
        sample_item_type="sample",
        sample_item_utc_created=datetime.now(timezone.utc),
    )
    session.add(sample_item)
    await session.flush()
    yield sample_item


@pytest_asyncio.fixture
async def db_test_run(session, db_test_sample_item):
    """Create a peak assignment run linked to `db_test_sample_item`."""
    run = PeakAssignmentRun(
        peak_assignment_run_id=gen_test_id(),
        sample_item_id=db_test_sample_item.sample_item_id,
        engine_version="0.1.0-test",
        status="running",
        config={"run_untargeted": True, "mz_precision_ppm": 10.0},
        peak_assignment_run_utc_created=datetime.now(timezone.utc),
    )
    session.add(run)
    await session.flush()
    yield run


def _assignment(run, sample_item_id, sample_peak_id, **overrides) -> PeakAssignment:
    values = {
        "peak_assignment_id": gen_test_id(32),
        "peak_assignment_run_id": run.peak_assignment_run_id,
        "sample_item_id": sample_item_id,
        "sample_peak_id": sample_peak_id,
        "sample_peak_mz": 181.0707,
        "sample_peak_intensity": 1234.5,
        "role": "M0",
        "tier": "identified",
        "source": "database",
        "assigned_formula": "C6H12O6",
        "fit_score": 0.95,
    }
    values.update(overrides)
    return PeakAssignment(**values)


@pytest.mark.asyncio
async def test_create_run_and_assignments(session, db_test_run, db_test_sample_item):
    """A run persists its config and its per-peak assignment rows."""
    m0 = _assignment(db_test_run, db_test_sample_item.sample_item_id, "peak-1")
    child = _assignment(
        db_test_run,
        db_test_sample_item.sample_item_id,
        "peak-2",
        role="iso_child",
        isotope_label="M+1",
        owner_peak_assignment_id=m0.peak_assignment_id,
    )
    session.add_all([m0, child])
    await session.flush()

    run = await session.get(PeakAssignmentRun, db_test_run.peak_assignment_run_id)
    assert run is not None
    assert run.engine_version == "0.1.0-test"
    assert run.config["mz_precision_ppm"] == 10.0

    result = await session.execute(
        select(PeakAssignment)
        .where(PeakAssignment.peak_assignment_run_id == run.peak_assignment_run_id)
        .order_by(PeakAssignment.sample_peak_id)
    )
    assignments = result.scalars().all()
    assert len(assignments) == 2
    assert assignments[0].role == "M0"
    assert assignments[1].role == "iso_child"
    assert assignments[1].owner_peak_assignment_id == assignments[0].peak_assignment_id


@pytest.mark.asyncio
async def test_single_owner_per_peak_is_enforced(
    session, db_test_run, db_test_sample_item
):
    """The unique constraint rejects a second row for the same peak in a run."""
    session.add(_assignment(db_test_run, db_test_sample_item.sample_item_id, "peak-1"))
    await session.flush()

    session.add(
        _assignment(
            db_test_run,
            db_test_sample_item.sample_item_id,
            "peak-1",
            assigned_formula="C7H16O5",
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.asyncio
async def test_fit_score_range_is_enforced(session, db_test_run, db_test_sample_item):
    """The check constraint rejects scores outside [0, 1] but allows NULL."""
    session.add(
        _assignment(
            db_test_run,
            db_test_sample_item.sample_item_id,
            "peak-null-score",
            fit_score=None,
            source=None,
            role="unassigned",
            tier="unassigned",
            assigned_formula=None,
        )
    )
    await session.flush()

    session.add(
        _assignment(
            db_test_run,
            db_test_sample_item.sample_item_id,
            "peak-bad-score",
            fit_score=1.5,
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.asyncio
async def test_deleting_run_cascades_to_assignments(
    session, db_test_run, db_test_sample_item
):
    """Deleting a run removes its assignment rows via ON DELETE CASCADE."""
    assignment = _assignment(db_test_run, db_test_sample_item.sample_item_id, "peak-1")
    session.add(assignment)
    await session.flush()

    await session.delete(db_test_run)
    await session.flush()

    # passive_deletes=True hands the cascade to the database, so expunge the
    # cached child and re-query for the real DB state.
    session.expunge(assignment)
    assert await session.get(PeakAssignment, assignment.peak_assignment_id) is None
