"""
Unit tests for the auto-processing pipeline orchestrator.

Tests verify the orchestration logic of ``auto_process_sample_file``:
- Correct parameter threading (instrument, year, user_id)
- Workspace + dataset auto-creation via get_acquisition_dataset
- Batch and sample item creation
- Calibration skip logic (blank files, missing calibration collection)
- Match computation for each sample
- Return structure

All external dependencies are mocked — no DB, file I/O, or Socket.IO required.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# Module path prefix for patching
_SVC = "mascope_backend.api.controllers.sample.files.process.service"
_NOTIF = "mascope_backend.socket.notifications"
_UTILS = "mascope_backend.api.lib.utils"


# ---------------------------------------------------------------------------
# Helpers — fake data builders
# ---------------------------------------------------------------------------


def _make_sample_file(
    *,
    sample_file_id="sf-001",
    instrument="Orbion",
    filename="2025.09.20_test_file.raw",
    datetime_local=None,
    datetime_utc=None,
    instrument_function_id="ifunc-001",
    polarity="-",
):
    """Build a fake SampleFile ORM-like object."""
    sf = MagicMock()
    sf.sample_file_id = sample_file_id
    sf.instrument = instrument
    sf.filename = filename
    sf.polarity = polarity
    sf.instrument_function_id = instrument_function_id
    sf.datetime = datetime_local or datetime(2025, 9, 20, 10, 30, 0)
    sf.datetime_utc = datetime_utc or datetime(
        2025, 9, 20, 8, 30, 0, tzinfo=timezone.utc
    )
    return sf


def _make_ionization_mode(
    *,
    ionization_mode_id="im-001",
    ionization_mode_name="Bromide RI",
    ionization_mode_polarity="-",
    calibration_collection_id="cal-001",
):
    """Build a fake IonizationMode ORM-like object."""
    im = MagicMock()
    im.ionization_mode_id = ionization_mode_id
    im.ionization_mode_name = ionization_mode_name
    im.ionization_mode_polarity = ionization_mode_polarity
    im.calibration_collection_id = calibration_collection_id
    return im


def _make_dataset(*, dataset_id="ds-001"):
    return {"dataset_id": dataset_id, "dataset_name": "2025", "instrument": "Orbion"}


def _make_batch(
    *,
    sample_batch_id="batch-001",
    sample_batch_name="2025-09-20 Bromide RI acquisition",
):
    return {"sample_batch_id": sample_batch_id, "sample_batch_name": sample_batch_name}


def _make_sample_item(
    *,
    sample_item_id="si-001",
    ionization_mode_id="im-001",
    sample_item_name="2025-09-20 10:30:00",
    filename="2025.09.20_test_file.raw",
):
    return {
        "sample_item_id": sample_item_id,
        "ionization_mode_id": ionization_mode_id,
        "sample_item_name": sample_item_name,
        "filename": filename,
    }


def _make_affected_data(sample_items):
    """Build a fake AffectedSampleData return value."""
    ad = MagicMock()
    ad.affected_samples = sample_items
    return ad


# ---------------------------------------------------------------------------
# Shared patch context
# ---------------------------------------------------------------------------


def _base_patches():
    """Return dict of patches common to every test.

    Caller starts them with ``for p in patches.values(): p.start()``
    and stops with ``patch.stopall()`` (or use as context managers).
    """
    return {
        "fetch_sample_file": patch(f"{_SVC}.fetch_sample_file", new_callable=AsyncMock),
        "get_acquisition_dataset": patch(
            f"{_SVC}.get_acquisition_dataset", new_callable=AsyncMock
        ),
        "create_batches": patch(
            f"{_SVC}.create_acquisition_batches_and_items", new_callable=AsyncMock
        ),
        "calibrate": patch(f"{_SVC}.calibrate_with_retry", new_callable=AsyncMock),
        "match": patch(f"{_SVC}.match_compute_sample", new_callable=AsyncMock),
        "assign": patch(f"{_SVC}.auto_assign_sample_peaks", new_callable=AsyncMock),
        "fetch_affected": patch(
            f"{_SVC}.fetch_affected_sample_data", new_callable=AsyncMock
        ),
        "async_session": patch(f"{_SVC}.async_session"),
        "handle_notifications": patch(
            f"{_NOTIF}.handle_notifications", new_callable=AsyncMock
        ),
        "handle_reloads": patch(f"{_UTILS}.handle_reloads", new_callable=AsyncMock),
        "gen_id": patch(f"{_SVC}.gen_id", return_value="test-id-0001"),
    }


@pytest.fixture(autouse=True)
def _stop_all_patches():
    """Safety net — stop all patches after each test."""
    yield
    patch.stopall()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_passes_instrument_year_and_user_to_get_acquisition_dataset():
    """get_acquisition_dataset receives instrument, year from file datetime, and user_id."""
    from mascope_backend.api.controllers.sample.files.process.service import (
        auto_process_sample_file,
    )

    sample_file = _make_sample_file()
    ion_mode = _make_ionization_mode()
    dataset = _make_dataset()
    batch = _make_batch()
    sample_item = _make_sample_item()

    patches = _base_patches()
    mocks = {k: p.start() for k, p in patches.items()}

    mocks["fetch_sample_file"].return_value = sample_file
    mocks["get_acquisition_dataset"].return_value = {"data": dataset}
    mocks["create_batches"].return_value = ([sample_item], [batch])
    mocks["fetch_affected"].return_value = _make_affected_data([sample_item])

    # Mock the async context manager for session.get(IonizationMode, ...)
    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=ion_mode)
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mocks["async_session"].return_value = mock_ctx

    result = await auto_process_sample_file(
        sample_file_id="sf-001",
        independent_transaction=True,
        user_id=42,
        process_id="proc-001",
    )

    mocks["get_acquisition_dataset"].assert_called_once_with(
        instrument="Orbion",
        year=2025,
        user_id=42,
    )
    assert result["_notification_data"]["instrument"] == "Orbion"


@pytest.mark.asyncio
async def test_derives_year_from_datetime_utc():
    """Year is derived from datetime_utc (preferred over datetime)."""
    from mascope_backend.api.controllers.sample.files.process.service import (
        auto_process_sample_file,
    )

    sample_file = _make_sample_file(
        datetime_utc=datetime(2024, 12, 31, 23, 30, 0, tzinfo=timezone.utc),
        datetime_local=datetime(2025, 1, 1, 0, 30, 0),
    )
    ion_mode = _make_ionization_mode()
    dataset = _make_dataset(dataset_id="ds-2024")
    batch = _make_batch()
    sample_item = _make_sample_item()

    patches = _base_patches()
    mocks = {k: p.start() for k, p in patches.items()}

    mocks["fetch_sample_file"].return_value = sample_file
    mocks["get_acquisition_dataset"].return_value = {"data": dataset}
    mocks["create_batches"].return_value = ([sample_item], [batch])
    mocks["fetch_affected"].return_value = _make_affected_data([sample_item])

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=ion_mode)
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mocks["async_session"].return_value = mock_ctx

    await auto_process_sample_file(
        sample_file_id="sf-001",
        independent_transaction=True,
        user_id=42,
        process_id="proc-001",
    )

    # datetime_utc year (2024) should be used, not datetime year (2025)
    mocks["get_acquisition_dataset"].assert_called_once_with(
        instrument="Orbion",
        year=2024,
        user_id=42,
    )


@pytest.mark.asyncio
async def test_calibrates_when_calibration_collection_is_set():
    """Calibration runs when ionization mode has a calibration_collection_id and file is not blank."""
    from mascope_backend.api.controllers.sample.files.process.service import (
        auto_process_sample_file,
    )

    sample_file = _make_sample_file(instrument_function_id="ifunc-001")
    ion_mode = _make_ionization_mode(calibration_collection_id="cal-001")
    dataset = _make_dataset()
    batch = _make_batch()
    sample_item = _make_sample_item()

    patches = _base_patches()
    mocks = {k: p.start() for k, p in patches.items()}

    mocks["fetch_sample_file"].return_value = sample_file
    mocks["get_acquisition_dataset"].return_value = {"data": dataset}
    mocks["create_batches"].return_value = ([sample_item], [batch])
    mocks["fetch_affected"].return_value = _make_affected_data([sample_item])

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=ion_mode)
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mocks["async_session"].return_value = mock_ctx

    await auto_process_sample_file(
        sample_file_id="sf-001",
        independent_transaction=True,
        user_id=42,
        process_id="proc-001",
    )

    mocks["calibrate"].assert_called_once_with(
        sample=sample_item,
        sample_file_id=sample_file.sample_file_id,
        user_id=42,
        process_id="proc-001",
    )
    mocks["match"].assert_called_once()
    # Stage-A peak assignment runs for the processed sample
    mocks["assign"].assert_called_once()


@pytest.mark.asyncio
async def test_failed_calibration_skips_matching_and_assignment():
    """A given-up calibration must not match/assign on the uncalibrated axis."""
    from mascope_backend.api.controllers.sample.files.process.service import (
        auto_process_sample_file,
    )

    sample_file = _make_sample_file(instrument_function_id="ifunc-001")
    ion_mode = _make_ionization_mode(calibration_collection_id="cal-001")
    dataset = _make_dataset()
    batch = _make_batch()
    sample_item = _make_sample_item()

    patches = _base_patches()
    mocks = {k: p.start() for k, p in patches.items()}

    mocks["fetch_sample_file"].return_value = sample_file
    mocks["get_acquisition_dataset"].return_value = {"data": dataset}
    mocks["create_batches"].return_value = ([sample_item], [batch])
    mocks["fetch_affected"].return_value = _make_affected_data([sample_item])
    mocks["calibrate"].return_value = False

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=ion_mode)
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mocks["async_session"].return_value = mock_ctx

    await auto_process_sample_file(
        sample_file_id="sf-001",
        independent_transaction=True,
        user_id=42,
        process_id="proc-001",
    )

    mocks["calibrate"].assert_called_once()
    mocks["match"].assert_not_called()
    mocks["assign"].assert_not_called()


@pytest.mark.asyncio
async def test_skips_calibration_for_blank_file():
    """Blank files (instrument_function_id is None) skip calibration but still match."""
    from mascope_backend.api.controllers.sample.files.process.service import (
        auto_process_sample_file,
    )

    sample_file = _make_sample_file(instrument_function_id=None)
    ion_mode = _make_ionization_mode(calibration_collection_id="cal-001")
    dataset = _make_dataset()
    batch = _make_batch()
    sample_item = _make_sample_item()

    patches = _base_patches()
    mocks = {k: p.start() for k, p in patches.items()}

    mocks["fetch_sample_file"].return_value = sample_file
    mocks["get_acquisition_dataset"].return_value = {"data": dataset}
    mocks["create_batches"].return_value = ([sample_item], [batch])
    mocks["fetch_affected"].return_value = _make_affected_data([sample_item])

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=ion_mode)
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mocks["async_session"].return_value = mock_ctx

    await auto_process_sample_file(
        sample_file_id="sf-001",
        independent_transaction=True,
        user_id=42,
        process_id="proc-001",
    )

    mocks["calibrate"].assert_not_called()
    # Match should still run
    mocks["match"].assert_called_once()
    # Stage-A peak assignment runs even for a blank file (the engine skips it internally)
    mocks["assign"].assert_called_once()


@pytest.mark.asyncio
async def test_skips_calibration_when_no_calibration_collection():
    """Non-blank files skip calibration when ionization mode has no calibration_collection_id."""
    from mascope_backend.api.controllers.sample.files.process.service import (
        auto_process_sample_file,
    )

    sample_file = _make_sample_file(instrument_function_id="ifunc-001")
    ion_mode = _make_ionization_mode(calibration_collection_id=None)
    dataset = _make_dataset()
    batch = _make_batch()
    sample_item = _make_sample_item()

    patches = _base_patches()
    mocks = {k: p.start() for k, p in patches.items()}

    mocks["fetch_sample_file"].return_value = sample_file
    mocks["get_acquisition_dataset"].return_value = {"data": dataset}
    mocks["create_batches"].return_value = ([sample_item], [batch])
    mocks["fetch_affected"].return_value = _make_affected_data([sample_item])

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=ion_mode)
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mocks["async_session"].return_value = mock_ctx

    await auto_process_sample_file(
        sample_file_id="sf-001",
        independent_transaction=True,
        user_id=42,
        process_id="proc-001",
    )

    mocks["calibrate"].assert_not_called()
    mocks["match"].assert_called_once()
    mocks["assign"].assert_called_once()


@pytest.mark.asyncio
async def test_processes_multiple_ionization_modes():
    """Each ionization mode produces its own calibration + match cycle."""
    from mascope_backend.api.controllers.sample.files.process.service import (
        auto_process_sample_file,
    )

    sample_file = _make_sample_file()

    ion_mode_neg = _make_ionization_mode(
        ionization_mode_id="im-neg",
        ionization_mode_name="Bromide RI",
        ionization_mode_polarity="-",
        calibration_collection_id="cal-neg",
    )
    ion_mode_pos = _make_ionization_mode(
        ionization_mode_id="im-pos",
        ionization_mode_name="H3O RI",
        ionization_mode_polarity="+",
        calibration_collection_id="cal-pos",
    )

    dataset = _make_dataset()
    batch_neg = _make_batch(
        sample_batch_id="batch-neg",
        sample_batch_name="2025-09-20 Bromide RI acquisition",
    )
    batch_pos = _make_batch(
        sample_batch_id="batch-pos", sample_batch_name="2025-09-20 H3O RI acquisition"
    )

    sample_neg = _make_sample_item(sample_item_id="si-neg", ionization_mode_id="im-neg")
    sample_pos = _make_sample_item(sample_item_id="si-pos", ionization_mode_id="im-pos")

    patches = _base_patches()
    mocks = {k: p.start() for k, p in patches.items()}

    mocks["fetch_sample_file"].return_value = sample_file
    mocks["get_acquisition_dataset"].return_value = {"data": dataset}
    mocks["create_batches"].return_value = (
        [sample_neg, sample_pos],
        [batch_neg, batch_pos],
    )
    mocks["fetch_affected"].return_value = _make_affected_data([sample_neg, sample_pos])

    # session.get returns the correct ionization mode for each ID
    async def _get_ion_mode(model_class, mode_id):
        return {"im-neg": ion_mode_neg, "im-pos": ion_mode_pos}[mode_id]

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(side_effect=_get_ion_mode)
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mocks["async_session"].return_value = mock_ctx

    await auto_process_sample_file(
        sample_file_id="sf-001",
        independent_transaction=True,
        user_id=42,
        process_id="proc-001",
    )

    assert mocks["calibrate"].call_count == 2
    assert mocks["match"].call_count == 2
    assert mocks["assign"].call_count == 2


@pytest.mark.asyncio
async def test_creates_batches_with_correct_dataset_id():
    """create_acquisition_batches_and_items receives the dataset_id from get_acquisition_dataset."""
    from mascope_backend.api.controllers.sample.files.process.service import (
        auto_process_sample_file,
    )

    sample_file = _make_sample_file()
    ion_mode = _make_ionization_mode()
    dataset = _make_dataset(dataset_id="ds-specific")
    batch = _make_batch()
    sample_item = _make_sample_item()

    patches = _base_patches()
    mocks = {k: p.start() for k, p in patches.items()}

    mocks["fetch_sample_file"].return_value = sample_file
    mocks["get_acquisition_dataset"].return_value = {"data": dataset}
    mocks["create_batches"].return_value = ([sample_item], [batch])
    mocks["fetch_affected"].return_value = _make_affected_data([sample_item])

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=ion_mode)
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mocks["async_session"].return_value = mock_ctx

    await auto_process_sample_file(
        sample_file_id="sf-001",
        independent_transaction=True,
        user_id=42,
        process_id="proc-001",
    )

    mocks["create_batches"].assert_called_once_with(
        sample_file=sample_file,
        dataset_id="ds-specific",
    )


@pytest.mark.asyncio
async def test_return_structure():
    """Result contains expected keys and notification data."""
    from mascope_backend.api.controllers.sample.files.process.service import (
        auto_process_sample_file,
    )

    sample_file = _make_sample_file()
    ion_mode = _make_ionization_mode()
    dataset = _make_dataset()
    batch = _make_batch(sample_batch_id="batch-ret")
    sample_item = _make_sample_item(sample_item_id="si-ret")

    patches = _base_patches()
    mocks = {k: p.start() for k, p in patches.items()}

    mocks["fetch_sample_file"].return_value = sample_file
    mocks["get_acquisition_dataset"].return_value = {"data": dataset}
    mocks["create_batches"].return_value = ([sample_item], [batch])
    mocks["fetch_affected"].return_value = _make_affected_data([sample_item])

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=ion_mode)
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mocks["async_session"].return_value = mock_ctx

    result = await auto_process_sample_file(
        sample_file_id="sf-001",
        independent_transaction=True,
        user_id=42,
        process_id="proc-001",
    )

    assert "message" in result
    assert "data" in result
    assert "_notification_data" in result

    notif = result["_notification_data"]
    assert notif["instrument"] == "Orbion"
    assert "batch-ret" in notif["affected_sample_batch_ids"]
    assert "si-ret" in notif["affected_sample_item_ids"]


@pytest.mark.asyncio
async def test_retries_recoverable_error_then_succeeds():
    """A 503 (pool starvation) attempt is retried and the retry's result returned."""
    from mascope_backend.api.controllers.sample.files.process import service
    from mascope_backend.api.lib.exceptions.api_exceptions import ApiException

    ok = {"message": "done", "_notification_data": {}}
    body = AsyncMock(
        side_effect=[ApiException("busy", {}, 503), ok],
    )
    cleanup = AsyncMock()

    with (
        patch(f"{_SVC}._auto_process_sample_file", new=body),
        patch(f"{_SVC}._delete_partial_acquisition_items", new=cleanup),
        patch.object(service, "_AUTO_PROCESS_RETRY_DELAYS_S", (0, 0, 0)),
        patch(f"{_NOTIF}.handle_notifications", new_callable=AsyncMock),
        patch(f"{_UTILS}.handle_reloads", new_callable=AsyncMock),
    ):
        result = await service.auto_process_sample_file(
            sample_file_id="sf-retry", independent_transaction=True
        )

    assert result == ok
    assert body.call_count == 2
    # Partial sample items are cleaned up before the retry, not the first try.
    cleanup.assert_called_once_with("sf-retry")


@pytest.mark.asyncio
async def test_retries_raw_pool_timeout():
    """An unwrapped SQLAlchemy pool timeout is recoverable too."""
    from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

    from mascope_backend.api.controllers.sample.files.process import service

    ok = {"message": "done", "_notification_data": {}}
    body = AsyncMock(side_effect=[SQLAlchemyTimeoutError("pool timeout"), ok])

    with (
        patch(f"{_SVC}._auto_process_sample_file", new=body),
        patch(f"{_SVC}._delete_partial_acquisition_items", new=AsyncMock()),
        patch.object(service, "_AUTO_PROCESS_RETRY_DELAYS_S", (0, 0, 0)),
        patch(f"{_NOTIF}.handle_notifications", new_callable=AsyncMock),
        patch(f"{_UTILS}.handle_reloads", new_callable=AsyncMock),
    ):
        result = await service.auto_process_sample_file(
            sample_file_id="sf-timeout", independent_transaction=True
        )

    assert result == ok
    assert body.call_count == 2


@pytest.mark.asyncio
async def test_does_not_retry_non_recoverable_error():
    """A 400 (e.g. data corruption) fails immediately - retrying cannot help."""
    from mascope_backend.api.controllers.sample.files.process import service
    from mascope_backend.api.lib.exceptions.api_exceptions import ApiException

    body = AsyncMock(side_effect=ApiException("corrupt", {}, 400))
    cleanup = AsyncMock()

    with (
        patch(f"{_SVC}._auto_process_sample_file", new=body),
        patch(f"{_SVC}._delete_partial_acquisition_items", new=cleanup),
        patch.object(service, "_AUTO_PROCESS_RETRY_DELAYS_S", (0, 0, 0)),
        patch(f"{_NOTIF}.handle_notifications", new_callable=AsyncMock),
        patch(f"{_UTILS}.handle_reloads", new_callable=AsyncMock),
    ):
        with pytest.raises(ApiException) as excinfo:
            await service.auto_process_sample_file(
                sample_file_id="sf-bad", independent_transaction=False
            )

    assert excinfo.value.status_code == 400
    assert body.call_count == 1
    cleanup.assert_not_called()


@pytest.mark.asyncio
async def test_gives_up_after_max_retries():
    """Persistent congestion stops after _AUTO_PROCESS_RETRIES retries."""
    from mascope_backend.api.controllers.sample.files.process import service
    from mascope_backend.api.lib.exceptions.api_exceptions import ApiException

    body = AsyncMock(side_effect=ApiException("busy", {}, 503))

    with (
        patch(f"{_SVC}._auto_process_sample_file", new=body),
        patch(f"{_SVC}._delete_partial_acquisition_items", new=AsyncMock()),
        patch.object(service, "_AUTO_PROCESS_RETRY_DELAYS_S", (0, 0, 0)),
        patch(f"{_NOTIF}.handle_notifications", new_callable=AsyncMock),
        patch(f"{_UTILS}.handle_reloads", new_callable=AsyncMock),
    ):
        with pytest.raises(ApiException) as excinfo:
            await service.auto_process_sample_file(
                sample_file_id="sf-stuck", independent_transaction=False
            )

    assert excinfo.value.status_code == 503
    assert body.call_count == service._AUTO_PROCESS_RETRIES + 1


@pytest.mark.asyncio
async def test_concurrent_pipelines_are_bounded():
    """
    A burst of auto-process calls runs at most _AUTO_PROCESS_CONCURRENCY
    pipelines at once - an unbounded burst exhausts the worker's database
    connection pool and kills pipelines with pool timeouts.
    """
    import asyncio

    from mascope_backend.api.controllers.sample.files.process import service

    peak = 0
    running = 0

    async def fake_body(**kwargs):
        nonlocal peak, running
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        return {"_notification_data": {}}

    with (
        patch(f"{_SVC}._auto_process_sample_file", new=fake_body),
        patch(f"{_NOTIF}.handle_notifications", new_callable=AsyncMock),
        patch(f"{_UTILS}.handle_reloads", new_callable=AsyncMock),
    ):
        await asyncio.gather(
            *(
                service.auto_process_sample_file(sample_file_id=f"sf-{i}")
                for i in range(10)
            )
        )

    assert peak == service._AUTO_PROCESS_CONCURRENCY


@pytest.mark.asyncio
async def test_names_the_file_when_it_gives_up():
    """
    Exhausting the retries must name the file, not just the attempts.

    A file whose pipeline never completes keeps its sample_file row and its
    batch still settles `ready`, so without this the shortfall is only
    visible by counting rows afterwards - the demo bundle shipped incomplete
    goldens exactly that way (see the demo coverage guard).
    """
    from mascope_backend.api.controllers.sample.files.process import service
    from mascope_backend.api.lib.exceptions.api_exceptions import ApiException

    body = AsyncMock(side_effect=ApiException("busy", {}, 503))
    logged: list[str] = []

    with (
        patch(f"{_SVC}._auto_process_sample_file", new=body),
        patch(f"{_SVC}._delete_partial_acquisition_items", new=AsyncMock()),
        patch.object(service, "_AUTO_PROCESS_RETRY_DELAYS_S", (0, 0, 0)),
        patch.object(service.runtime.logger, "error", side_effect=logged.append),
        patch(f"{_NOTIF}.handle_notifications", new_callable=AsyncMock),
        patch(f"{_UTILS}.handle_reloads", new_callable=AsyncMock),
    ):
        with pytest.raises(ApiException):
            await service.auto_process_sample_file(
                sample_file_id="sf-gaveup", independent_transaction=False
            )

    assert any("sf-gaveup" in line for line in logged), logged
    assert any("gave up" in line for line in logged), logged


@pytest.mark.asyncio
async def test_names_the_file_on_a_non_recoverable_error():
    """The give-up log covers the no-retry path too, not just exhaustion."""
    from mascope_backend.api.controllers.sample.files.process import service
    from mascope_backend.api.lib.exceptions.api_exceptions import ApiException

    body = AsyncMock(side_effect=ApiException("corrupt", {}, 400))
    logged: list[str] = []

    with (
        patch(f"{_SVC}._auto_process_sample_file", new=body),
        patch(f"{_SVC}._delete_partial_acquisition_items", new=AsyncMock()),
        patch.object(service, "_AUTO_PROCESS_RETRY_DELAYS_S", (0, 0, 0)),
        patch.object(service.runtime.logger, "error", side_effect=logged.append),
        patch(f"{_NOTIF}.handle_notifications", new_callable=AsyncMock),
        patch(f"{_UTILS}.handle_reloads", new_callable=AsyncMock),
    ):
        with pytest.raises(ApiException):
            await service.auto_process_sample_file(
                sample_file_id="sf-corrupt", independent_transaction=False
            )

    assert any("sf-corrupt" in line for line in logged), logged
    assert body.call_count == 1  # not retried
