"""
Tests: calibration access control — per-instrument, not per-instance.

m/z calibration writes onto the ``SampleFile`` itself, so every sample item
referencing that file, in any workspace, sees the result. The gate is therefore
``admin`` in the file's *instrument* workspace — the same bar already applied to
deleting and reprocessing a raw file — and no longer the global ``admin`` role.

The distinction these tests exist to pin down:

- ``acq_admin_client`` is a global **editor** who is an **admin** of the
  Acquisitions workspace. It must be allowed. If this case ever regresses to
  403, calibration has drifted back onto the global role and operators are
  again forced to hand out user-administration rights to get a batch
  calibrated.
- ``acq_editor_client`` is an **editor** of the same workspace. It must be
  refused: editor is enough to upload a file, not to rewrite the calibration
  every other workspace reads through it.
- ``editor_client`` is an editor of *Alpha* (where the sample item lives) but
  no member of the instrument workspace. It may run a fit, which computes
  without writing, and must be refused the writes.

Fixtures come from ``tests/integration/api/workspace/conftest.py``;
``alpha_item`` sits in ``alpha_batch`` and references a ``sample_file`` whose
instrument is ``test-orbion``, the instrument ``acquisitions_workspace`` covers.
"""

import pytest


def _params():
    """A body that passes schema validation.

    ``refine_window`` is the only required field on ``MzCalibrationParams``.
    The body has to be valid even for the cases expected to be refused: the
    access checks run inside the handler, so an invalid body would short the
    request to 422 before any of them execute and the test would prove nothing.
    """
    return {"refine_window": 50}


# ============= Writes: allowed for the instrument workspace's admin =============


@pytest.mark.asyncio
async def test_calibrate_sample_as_acquisitions_admin(acq_admin_client, alpha_item):
    """A workspace admin of the instrument may calibrate without being a global admin.

    Asserts only "not forbidden": the route schedules a background task that
    would need a real raw file, so a later failure is not the subject here.
    """
    resp = await acq_admin_client.post(
        f"/api/calibration/mz_calibrate/sample/{alpha_item}", json=_params()
    )
    assert resp.status_code != 403


@pytest.mark.asyncio
async def test_calibrate_batch_as_acquisitions_admin(acq_admin_client, alpha_batch):
    """Same for the batch-wide route, which checks every instrument involved."""
    resp = await acq_admin_client.post(
        f"/api/calibration/mz_calibrate/batch/{alpha_batch}", json=_params()
    )
    assert resp.status_code != 403


# ============= Writes: refused below admin in the instrument workspace =============


@pytest.mark.asyncio
async def test_calibrate_sample_as_acquisitions_editor_forbidden(
    acq_editor_client, alpha_item
):
    """Editor of the instrument workspace is not enough to write a calibration."""
    resp = await acq_editor_client.post(
        f"/api/calibration/mz_calibrate/sample/{alpha_item}", json=_params()
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_calibrate_batch_as_acquisitions_editor_forbidden(
    acq_editor_client, alpha_batch
):
    """Same for the batch-wide route."""
    resp = await acq_editor_client.post(
        f"/api/calibration/mz_calibrate/batch/{alpha_batch}", json=_params()
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_calibrate_sample_as_data_workspace_editor_forbidden(
    editor_client, alpha_item
):
    """Access to the workspace holding the item does not grant access to its file.

    ``editor_client`` is an editor of Alpha, where ``alpha_item`` lives, but is
    no member of the instrument workspace. Calibration reaches past the item to
    the shared file, so Alpha membership must not carry it.
    """
    resp = await editor_client.post(
        f"/api/calibration/mz_calibrate/sample/{alpha_item}", json=_params()
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_calibrate_sample_as_outsider_forbidden(outsider_client, alpha_item):
    """A user with no memberships at all is refused."""
    resp = await outsider_client.post(
        f"/api/calibration/mz_calibrate/sample/{alpha_item}", json=_params()
    )
    assert resp.status_code == 403


# ============= Names are withheld from a caller who cannot read them =============


@pytest.mark.asyncio
async def test_calibrate_batch_does_not_name_an_unreadable_batch(
    acq_admin_client, alpha_batch
):
    """The instrument role authorises the write, not a read of the batch.

    ``acq_admin_client`` is admin of the instrument workspace and no member of
    Alpha, so it may calibrate this batch but could not have learned its name
    through any read route. The confirmation must not hand the name over.
    """
    resp = await acq_admin_client.post(
        f"/api/calibration/mz_calibrate/batch/{alpha_batch}", json=_params()
    )
    assert resp.status_code != 403
    assert "Alpha Batch" not in resp.text


@pytest.mark.asyncio
async def test_calibrate_batch_names_a_readable_batch(admin_client, alpha_batch):
    """A caller who is a member of the batch's workspace still gets the name.

    ``admin_client`` is a global admin - so it clears the instrument check -
    and a workspace admin of Alpha, so withholding the name here would only
    make the confirmation worse.
    """
    resp = await admin_client.post(
        f"/api/calibration/mz_calibrate/batch/{alpha_batch}", json=_params()
    )
    assert resp.status_code != 403
    assert "Alpha Batch" in resp.text


@pytest.mark.asyncio
async def test_calibrate_sample_does_not_name_an_unreadable_sample(
    acq_admin_client, alpha_item
):
    """Same for the per-sample route."""
    resp = await acq_admin_client.post(
        f"/api/calibration/mz_calibrate/sample/{alpha_item}", json=_params()
    )
    assert resp.status_code != 403
    assert "Alpha Item" not in resp.text


@pytest.mark.asyncio
async def test_mz_fit_does_not_name_an_unreadable_sample(acq_admin_client, alpha_item):
    """And for the fit, which admits the instrument admin for the same reason."""
    resp = await acq_admin_client.post(
        f"/api/calibration/mz_fit?sample_item_id={alpha_item}", json=_params()
    )
    assert resp.status_code != 403
    assert "Alpha Item" not in resp.text


# ============= mz_apply: keyed on the filename, same instrument rule =============


@pytest.mark.asyncio
async def test_mz_apply_as_acquisitions_admin(
    acq_admin_client, async_session_factory, sample_file
):
    """Applying a fit resolves the filename to its instrument workspace."""
    from mascope_backend.db import SampleFile

    async with async_session_factory() as session:
        filename = (await session.get(SampleFile, sample_file)).filename

    resp = await acq_admin_client.post(
        f"/api/calibration/mz_apply?filename={filename}", json={"fit": {}}
    )
    assert resp.status_code != 403


@pytest.mark.asyncio
async def test_mz_apply_as_acquisitions_editor_forbidden(
    acq_editor_client, async_session_factory, sample_file
):
    """Editor of the instrument workspace cannot apply a fit."""
    from mascope_backend.db import SampleFile

    async with async_session_factory() as session:
        filename = (await session.get(SampleFile, sample_file)).filename

    resp = await acq_editor_client.post(
        f"/api/calibration/mz_apply?filename={filename}", json={"fit": {}}
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_mz_apply_unknown_filename_forbidden(acq_admin_client):
    """An unresolvable filename is refused rather than reported as missing.

    Failing closed keeps the route from confirming which raw files exist to a
    caller who would not be allowed to touch them anyway.
    """
    resp = await acq_admin_client.post(
        "/api/calibration/mz_apply?filename=no-such-file.raw", json={"fit": {}}
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_mz_apply_unknown_filename_not_found_for_superuser(owner_client):
    """A caller who bypasses the ACL entirely gets 404, not 403.

    There is nothing to fail closed about for someone who clears every
    instrument workspace: hiding the miss behind a 403 would only stop the
    route saying what is actually wrong. The check has to answer this before it
    resolves the filename, which is the ordering being pinned here.
    """
    resp = await owner_client.post(
        "/api/calibration/mz_apply?filename=no-such-file.raw", json={"fit": {}}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_calibrate_sample_unknown_id_not_found_for_superuser(owner_client):
    """Same ordering for the per-sample route."""
    resp = await owner_client.post(
        "/api/calibration/mz_calibrate/sample/no-such-item", json=_params()
    )
    assert resp.status_code == 404


# ============= mz_fit: computes without writing, so scoped to the sample =============


@pytest.mark.asyncio
async def test_mz_fit_as_data_workspace_editor(editor_client, alpha_item):
    """An editor of the item's own workspace may fit; the fit is never written."""
    resp = await editor_client.post(
        f"/api/calibration/mz_fit?sample_item_id={alpha_item}", json=_params()
    )
    assert resp.status_code != 403


@pytest.mark.asyncio
async def test_mz_fit_as_acquisitions_admin(acq_admin_client, alpha_item):
    """An admin of the file's instrument workspace may fit without joining Alpha.

    ``acq_admin_client`` is no member of the workspace holding ``alpha_item``,
    so the sample-workspace path refuses it - but it may write a calibration
    onto that file outright, and the calibration dialog will not enable Save
    until a fit comes back. Scoping the fit to the sample's workspace alone
    would leave the dialog permanently unusable for exactly this operator.
    """
    resp = await acq_admin_client.post(
        f"/api/calibration/mz_fit?sample_item_id={alpha_item}", json=_params()
    )
    assert resp.status_code != 403


@pytest.mark.asyncio
async def test_mz_fit_as_data_workspace_guest_forbidden(guest_client, alpha_item):
    """A guest of the item's workspace may not; fitting is a write-class action."""
    resp = await guest_client.post(
        f"/api/calibration/mz_fit?sample_item_id={alpha_item}", json=_params()
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_mz_fit_as_outsider_forbidden(outsider_client, alpha_item):
    """A non-member of the item's workspace is refused."""
    resp = await outsider_client.post(
        f"/api/calibration/mz_fit?sample_item_id={alpha_item}", json=_params()
    )
    assert resp.status_code == 403
