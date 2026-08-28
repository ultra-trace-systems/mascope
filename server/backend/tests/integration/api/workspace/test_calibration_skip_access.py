"""
Tests: marking an m/z calibration as deliberately skipped.

The marker is written into ``SampleFile.mz_calibration``, the same column an
applied fit uses, so it has the same reach: every sample item referencing the
file, in any workspace, shows it. It therefore takes the same gate as
``/mz_apply`` - ``admin`` in the file's *instrument* workspace, in the strict
form that does not fall back to the workspace an item happens to sit in.

Fixtures come from ``tests/integration/api/workspace/conftest.py``;
``acquisitions_workspace`` covers the instrument ``test-orbion``, which the
per-test file below is registered under. That file is function-scoped on
purpose: unlike the ``/mz_apply`` cases, these requests really do write, so
sharing the session-scoped ``sample_file`` would leak state into other tests.
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from mascope_backend.db import SampleFile
from mascope_backend.db.id import gen_id


_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
_NOW_NAIVE = datetime(2026, 1, 1)


@pytest_asyncio.fixture
async def skip_file(async_session_factory, acquisitions_workspace):
    """A throwaway sample file on the instrument the Acquisitions workspace covers."""
    file_id = gen_id()
    filename = f"test-orbion_{file_id}.raw"
    async with async_session_factory() as session:
        session.add(
            SampleFile(
                sample_file_id=file_id,
                filename=filename,
                instrument="test-orbion",
                datetime=_NOW_NAIVE,
                datetime_utc=_NOW,
                length=60.0,
                # A list, not the ``{"min": ..., "max": ...}`` shape the
                # session-scoped ``sample_file`` fixture uses: these tests write
                # the row back through ``SampleFileUpdate``, whose schema (like
                # the real converter output) types this column as a list.
                range=[0.0, 500.0],
                polarity="+",
            )
        )
        await session.commit()

    yield filename

    async with async_session_factory() as session:
        stored = await session.get(SampleFile, file_id)
        if stored is not None:
            await session.delete(stored)
            await session.commit()


async def _record(async_session_factory, filename: str) -> dict | None:
    """The persisted ``mz_calibration`` for ``filename``."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(SampleFile.mz_calibration).where(SampleFile.filename == filename)
        )
        return result.scalar_one()


async def _mark_skipped(client, filename, reason="blank file"):
    return await client.post(
        f"/api/calibration/mz_skip?filename={filename}", json={"reason": reason}
    )


# ============= The marker itself =============


@pytest.mark.asyncio
async def test_skip_is_recorded_and_attributed(
    acq_admin_client, async_session_factory, skip_file
):
    """The state survives the request: it is a row, not a UI flag."""
    resp = await _mark_skipped(
        acq_admin_client, skip_file, reason="Blank file, no calibrants"
    )
    assert resp.status_code == 200

    record = await _record(async_session_factory, skip_file)
    assert record["status"] == "skipped"
    assert record["reason"] == "Blank file, no calibrants"
    assert record["skipped_by"] == "acq_admin_user"
    assert record["skipped_utc"]
    # Not a failure: the match computation reads this flag, and skipping must
    # not stop a sample from matching.
    assert record["verified"] is True


@pytest.mark.asyncio
async def test_skip_is_reversible(acq_admin_client, async_session_factory, skip_file):
    """Clearing the marker returns the file to "never calibrated"."""
    assert (await _mark_skipped(acq_admin_client, skip_file)).status_code == 200

    resp = await acq_admin_client.delete(
        f"/api/calibration/mz_skip?filename={skip_file}"
    )
    assert resp.status_code == 200
    assert await _record(async_session_factory, skip_file) is None


@pytest.mark.asyncio
async def test_clearing_a_file_that_was_not_skipped_is_refused(
    acq_admin_client, skip_file
):
    """Nothing to undo - and this route must never erase some other record."""
    resp = await acq_admin_client.delete(
        f"/api/calibration/mz_skip?filename={skip_file}"
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_skip_requires_a_reason(
    acq_admin_client, async_session_factory, skip_file
):
    """A blank label would restore the ambiguity the marker exists to end."""
    for reason in ["", "   "]:
        resp = await acq_admin_client.post(
            f"/api/calibration/mz_skip?filename={skip_file}", json={"reason": reason}
        )
        assert resp.status_code == 422, reason

    assert await _record(async_session_factory, skip_file) is None


@pytest.mark.asyncio
async def test_reason_is_trimmed(acq_admin_client, async_session_factory, skip_file):
    resp = await _mark_skipped(acq_admin_client, skip_file, reason="  blank file  ")
    assert resp.status_code == 200

    record = await _record(async_session_factory, skip_file)
    assert record["reason"] == "blank file"


# ============= Gating: the same instrument rule as /mz_apply =============


@pytest.mark.asyncio
async def test_skip_as_acquisitions_editor_forbidden(
    acq_editor_client, async_session_factory, skip_file
):
    """Editor of the instrument workspace may upload, not label the file."""
    resp = await _mark_skipped(acq_editor_client, skip_file)
    assert resp.status_code == 403
    assert await _record(async_session_factory, skip_file) is None


@pytest.mark.asyncio
async def test_clear_as_acquisitions_editor_forbidden(
    acq_admin_client, acq_editor_client, async_session_factory, skip_file
):
    """Clearing takes the same role as writing."""
    assert (await _mark_skipped(acq_admin_client, skip_file)).status_code == 200

    resp = await acq_editor_client.delete(
        f"/api/calibration/mz_skip?filename={skip_file}"
    )
    assert resp.status_code == 403
    assert (await _record(async_session_factory, skip_file))["status"] == "skipped"


@pytest.mark.asyncio
async def test_skip_as_data_workspace_editor_forbidden(editor_client, skip_file):
    """Membership of a workspace holding an item does not reach the file.

    ``editor_client`` is an editor of Alpha and no member of the instrument
    workspace. The marker is file-scoped, so Alpha membership must not carry
    it - the strict form calibration already takes.
    """
    resp = await _mark_skipped(editor_client, skip_file)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_skip_as_outsider_forbidden(outsider_client, skip_file):
    resp = await _mark_skipped(outsider_client, skip_file)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_skip_unknown_filename_forbidden(acq_admin_client):
    """Failing closed, like ``/mz_apply``: the route does not confirm which
    raw files exist to a caller who could not touch them anyway."""
    resp = await _mark_skipped(acq_admin_client, "no-such-file.raw")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_skip_unknown_filename_not_found_for_superuser(owner_client):
    """A caller who clears every instrument workspace gets the real answer."""
    resp = await _mark_skipped(owner_client, "no-such-file.raw")
    assert resp.status_code == 404
