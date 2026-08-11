"""
Fixtures for peak assignments API integration tests.

Seeds a workspace (all test users as members) with the full entity chain
down to a sample item, plus peak assignment runs and per-peak assignment
rows. Also creates the `sample_view` database view, which the peak
assignment read services rely on via `fetch_sample` and which
`Base.metadata.create_all` does not create.
"""

from datetime import datetime, timedelta, timezone

import pytest_asyncio
from sqlalchemy import text

from mascope_backend.db import (
    Dataset,
    PeakAssignment,
    PeakAssignmentRun,
    SampleBatch,
    SampleFile,
    SampleItem,
    Workspace,
    WorkspaceMember,
)
from mascope_backend.db.id import gen_id
from mascope_backend.db.views import Sample


@pytest_asyncio.fixture(scope="session")
async def pa_sample_view(async_session_factory):
    """Create the sample_view database view in the integration test DB.

    The view is normally created by migrations; test databases are built via
    `Base.metadata.create_all`, which only covers tables.
    """
    async with async_session_factory() as session:
        await session.execute(text(Sample.drop_view()))
        await session.execute(text(Sample.create_view()))
        await session.commit()


@pytest_asyncio.fixture(scope="session")
async def pa_test_data(async_session_factory, test_users, pa_sample_view):
    """Seed workspace -> dataset -> batch -> sample -> assignment runs.

    Creates two runs for the sample: an older completed run with three
    assignment rows (M0, iso_child, unassigned) and a newer run that is
    still 'running' - so "latest completed" resolution is exercised.

    :return: Dict with the ids needed by the tests
    """
    now = datetime.now(timezone.utc)
    workspace_id = gen_id()
    dataset_id = gen_id()
    sample_batch_id = gen_id()
    sample_file_id = gen_id()
    sample_item_id = gen_id()
    completed_run_id = gen_id()
    running_run_id = gen_id()
    m0_assignment_id = gen_id(32)

    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=workspace_id,
                workspace_name="Peak Assignments Test Workspace",
                workspace_status="active",
                workspace_utc_created=now,
                workspace_utc_modified=now,
            )
        )
        for role_name, user in test_users.items():
            session.add(
                WorkspaceMember(
                    workspace_member_id=gen_id(),
                    workspace_id=workspace_id,
                    user_id=user.id,
                    workspace_role=role_name,
                    granted_at=now,
                    granted_by=user.id,
                )
            )
        session.add(
            Dataset(
                dataset_id=dataset_id,
                workspace_id=workspace_id,
                dataset_name="Peak Assignments Test Dataset",
                dataset_utc_created=now,
            )
        )
        session.add(
            SampleBatch(
                sample_batch_id=sample_batch_id,
                dataset_id=dataset_id,
                sample_batch_name="Peak Assignments Test Batch",
                sample_batch_utc_created=now,
            )
        )
        session.add(
            SampleFile(
                sample_file_id=sample_file_id,
                filename=f"peak-assignments-test-{sample_file_id}.zarr",
                instrument="orbi-test",
                datetime=datetime(2026, 7, 4, 12, 0, 0),
                datetime_utc=now,
                length=60.0,
                range=[50.0, 500.0],
                polarity="+",
            )
        )
        session.add(
            SampleItem(
                sample_item_id=sample_item_id,
                sample_batch_id=sample_batch_id,
                sample_file_id=sample_file_id,
                sample_item_name="Peak Assignments Test Sample",
                sample_item_type="sample",
                polarity="+",
                sample_item_utc_created=now,
            )
        )

        session.add(
            PeakAssignmentRun(
                peak_assignment_run_id=completed_run_id,
                sample_item_id=sample_item_id,
                engine_version="0.1.0-test",
                status="completed",
                config={"run_untargeted": True},
                peak_assignment_run_utc_created=now - timedelta(hours=1),
                peak_assignment_run_utc_completed=now - timedelta(minutes=55),
            )
        )
        session.add(
            PeakAssignmentRun(
                peak_assignment_run_id=running_run_id,
                sample_item_id=sample_item_id,
                engine_version="0.1.0-test",
                status="running",
                config={"run_untargeted": True},
                peak_assignment_run_utc_created=now,
            )
        )

        session.add(
            PeakAssignment(
                peak_assignment_id=m0_assignment_id,
                peak_assignment_run_id=completed_run_id,
                sample_item_id=sample_item_id,
                sample_peak_id="peak-1",
                sample_peak_mz=181.0707,
                sample_peak_intensity=5000.0,
                role="M0",
                assigned_formula="C6H12O6",
                ion_formula="C6H13O6+",
                isotope_label="M0",
                source="database",
                fit_score=0.95,
                mz_error_ppm=1.2,
                abundance_error=0.05,
                tier="identified",
            )
        )
        session.add(
            PeakAssignment(
                peak_assignment_id=gen_id(32),
                peak_assignment_run_id=completed_run_id,
                sample_item_id=sample_item_id,
                sample_peak_id="peak-2",
                sample_peak_mz=182.0741,
                sample_peak_intensity=350.0,
                role="iso_child",
                assigned_formula="C6H12O6",
                ion_formula="C6H13O6+",
                isotope_label="M+1",
                source="database",
                fit_score=0.88,
                mz_error_ppm=1.5,
                abundance_error=0.08,
                tier="identified",
                owner_peak_assignment_id=m0_assignment_id,
            )
        )
        session.add(
            PeakAssignment(
                peak_assignment_id=gen_id(32),
                peak_assignment_run_id=completed_run_id,
                sample_item_id=sample_item_id,
                sample_peak_id="peak-3",
                sample_peak_mz=250.5,
                sample_peak_intensity=42.0,
                role="unassigned",
                tier="unassigned",
            )
        )
        await session.commit()

    return {
        "workspace_id": workspace_id,
        "sample_batch_id": sample_batch_id,
        "sample_item_id": sample_item_id,
        "completed_run_id": completed_run_id,
        "running_run_id": running_run_id,
        "m0_assignment_id": m0_assignment_id,
    }
