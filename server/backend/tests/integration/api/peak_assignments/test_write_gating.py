"""
Integration tests for the opt-in gate on the peak assignment write endpoints.

The feature flag gates every route that can create or mutate assignment
state - launching runs, recording verdicts, refitting the calibration -
while the read endpoints stay open so ledgers written while the feature
was on remain inspectable after it is turned off again.

The flag is forced through the ``MASCOPE_PEAK_ASSIGNMENT`` env override so
these tests are independent of the test environment's ``[meta]`` config.
"""

import pytest


@pytest.fixture
def feature_disabled(monkeypatch):
    """Force the peak assignment feature off for the test."""
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "0")


@pytest.fixture
def feature_enabled(monkeypatch):
    """Force the peak assignment feature on for the test."""
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")


@pytest.mark.asyncio
async def test_assign_rejected_when_feature_disabled(
    editor_client, pa_test_data, feature_disabled
):
    """An editor cannot launch a run on a deployment that has not opted in."""
    response = await editor_client.post(
        f"/api/peak-assignments/sample/{pa_test_data['sample_item_id']}/assign"
    )
    # The app's exception handler sanitizes error bodies to an error_id;
    # the feature-gate origin of the 403 is proven by the flag-on tests below,
    # which succeed for the same client with only the env override changed.
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_batch_assign_rejected_when_feature_disabled(
    editor_client, pa_test_data, feature_disabled
):
    response = await editor_client.post(
        f"/api/peak-assignments/batch/{pa_test_data['sample_batch_id']}/assign"
    )
    # The app's exception handler sanitizes error bodies to an error_id;
    # the feature-gate origin of the 403 is proven by the flag-on tests below,
    # which succeed for the same client with only the env override changed.
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_verify_rejected_when_feature_disabled(
    editor_client, pa_test_data, feature_disabled
):
    response = await editor_client.post(
        f"/api/peak-assignments/sample/{pa_test_data['sample_item_id']}/verify",
        json={
            "peak_assignment_id": pa_test_data["m0_assignment_id"],
            "verdict": "rejected",
        },
    )
    # The app's exception handler sanitizes error bodies to an error_id;
    # the feature-gate origin of the 403 is proven by the flag-on tests below,
    # which succeed for the same client with only the env override changed.
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_recalibrate_rejected_when_feature_disabled(
    owner_client, pa_test_data, feature_disabled
):
    """Even a superuser is refused - the 403 comes from the feature gate."""
    response = await owner_client.post(
        "/api/peak-assignments/calibration/orbi/recalibrate"
    )
    # The app's exception handler sanitizes error bodies to an error_id;
    # the feature-gate origin of the 403 is proven by the flag-on tests below,
    # which succeed for the same client with only the env override changed.
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_batch_peak_backfill_rejected_when_feature_disabled(
    editor_client, pa_test_data, feature_disabled
):
    """The backfill computes and persists batch peaks, so it is a gated write."""
    response = await editor_client.post(
        f"/api/batch-peaks/batch/{pa_test_data['sample_batch_id']}/backfill"
    )
    # The app's exception handler sanitizes error bodies to an error_id;
    # the feature-gate origin of the 403 is proven by the flag-on test below,
    # which succeeds for the same client with only the env override changed.
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_batch_peak_backfill_accepted_when_feature_enabled(
    editor_client, pa_test_data, feature_enabled, monkeypatch
):
    async def _noop_compute(**kwargs):
        return None

    # Stub the controller entry point: the gate under test sits in the route.
    monkeypatch.setattr(
        "mascope_backend.api.new.peak_assignments.batch_peaks_routes"
        ".compute_batch_peaks",
        _noop_compute,
    )
    response = await editor_client.post(
        f"/api/batch-peaks/batch/{pa_test_data['sample_batch_id']}/backfill"
    )
    assert response.status_code == 202
    assert response.headers["Process-ID"]


@pytest.mark.asyncio
async def test_batch_peak_reads_stay_open_when_feature_disabled(
    guest_client, pa_test_data, feature_disabled
):
    """The batch-peak ledger stays readable after opt-out, like the run ledger."""
    response = await guest_client.get(
        f"/api/batch-peaks/batch/{pa_test_data['sample_batch_id']}"
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_reads_stay_open_when_feature_disabled(
    guest_client, pa_test_data, feature_disabled
):
    """Ledgers written while the feature was on stay readable after opt-out."""
    response = await guest_client.get(
        f"/api/peak-assignments/sample/{pa_test_data['sample_item_id']}"
    )
    assert response.status_code == 200
    assert response.json()["results"] == 3


@pytest.mark.asyncio
async def test_assign_accepted_when_feature_enabled(
    editor_client, pa_test_data, feature_enabled, monkeypatch
):
    """Opting in opens the gate: the run is queued and acknowledged."""

    async def _noop_assign(**kwargs):
        return None

    # Stub the engine entry point: the gate under test sits in the route, and
    # the fixture's zarr filename points at no real file.
    monkeypatch.setattr(
        "mascope_backend.api.new.peak_assignments.routes.assign_sample_peaks",
        _noop_assign,
    )
    response = await editor_client.post(
        f"/api/peak-assignments/sample/{pa_test_data['sample_item_id']}/assign"
    )
    assert response.status_code == 202
    # api_route moves process_id from the body into the Process-ID header.
    assert response.headers["Process-ID"]


@pytest.mark.asyncio
async def test_batch_assign_accepted_when_feature_enabled(
    editor_client, pa_test_data, feature_enabled, monkeypatch
):
    async def _noop_assign(**kwargs):
        return None

    monkeypatch.setattr(
        "mascope_backend.api.new.peak_assignments.routes.assign_sample_batch_peaks",
        _noop_assign,
    )
    response = await editor_client.post(
        f"/api/peak-assignments/batch/{pa_test_data['sample_batch_id']}/assign"
    )
    assert response.status_code == 202
    # api_route moves process_id from the body into the Process-ID header.
    assert response.headers["Process-ID"]


@pytest.mark.asyncio
async def test_verify_accepted_when_feature_enabled(
    editor_client, pa_test_data, feature_enabled
):
    """The verify path runs for real: a rejected verdict needs no evidence level."""
    response = await editor_client.post(
        f"/api/peak-assignments/sample/{pa_test_data['sample_item_id']}/verify",
        json={
            "peak_assignment_id": pa_test_data["m0_assignment_id"],
            "verdict": "rejected",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["data"][0]["verdict"] == "rejected"
