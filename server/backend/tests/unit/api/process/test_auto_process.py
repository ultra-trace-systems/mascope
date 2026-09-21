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
from test_utils import captured_logs


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
async def test_derives_year_from_instrument_local_datetime():
    """Year comes from the instrument's local clock, like the batch inside it.

    This used to prefer `datetime_utc`, which put a file acquired just after
    local New Year midnight into the previous year's dataset - while the daily
    batch and the sample item inside that dataset were both named from the
    local `datetime`. One instrument-local day then owned batches in two
    year-datasets, which no uniqueness on (dataset, name, polarity) can merge.
    The container now follows the same clock as its contents.
    """
    from mascope_backend.api.controllers.sample.files.process.service import (
        auto_process_sample_file,
    )

    sample_file = _make_sample_file(
        datetime_utc=datetime(2024, 12, 31, 23, 30, 0, tzinfo=timezone.utc),
        datetime_local=datetime(2025, 1, 1, 0, 30, 0),
    )
    ion_mode = _make_ionization_mode()
    dataset = _make_dataset(dataset_id="ds-2025")
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

    # Local datetime year (2025) is used, not the datetime_utc year (2024)
    mocks["get_acquisition_dataset"].assert_called_once_with(
        instrument="Orbion",
        year=2025,
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
async def test_blank_file_skips_calibration_matching_and_assignment():
    """Blank files (instrument_function_id is None) skip everything that needs peaks.

    Matching used to run anyway, but match_compute_sample refuses a blank with
    a raised warning - so in production the pipeline never got as far as
    assignment: it failed on every blank, and each one reached the instrument
    room as a warning and error monitoring as an issue of its own. A blank is
    fully processed once its sample items exist.
    """
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

    result = await auto_process_sample_file(
        sample_file_id="sf-001",
        independent_transaction=True,
        user_id=42,
        process_id="proc-001",
    )

    mocks["calibrate"].assert_not_called()
    mocks["match"].assert_not_called()
    mocks["assign"].assert_not_called()
    # Completed rather than failed: the created sample is reported as usual.
    assert result["_notification_data"]["affected_sample_item_ids"] == ["si-001"]


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
    """Each ionization mode is matched; calibrating two modes of one file is
    skipped, since the file holds a single m/z calibration for both."""
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

    mocks["calibrate"].assert_not_called()
    assert mocks["match"].call_count == 2
    assert mocks["assign"].call_count == 2


def _start_dual_polarity(*, calibrate, neg_collection="cal-neg", pos_collection=None):
    """Start the base patches for a ``+-`` file, negative polarity first.

    The file carries one ACQUISITION sample item per polarity. ``calibrate``
    becomes the side effect of ``calibrate_with_retry`` and receives the shared
    file record to update, the way ``calibration_mz_apply`` stores its fit on
    the file rather than on the sample item.
    """
    sample_file = _make_sample_file(polarity="+-")
    sample_file.mz_calibration = None
    ion_modes = {
        "im-neg": _make_ionization_mode(
            ionization_mode_id="im-neg",
            ionization_mode_polarity="-",
            calibration_collection_id=neg_collection,
        ),
        "im-pos": _make_ionization_mode(
            ionization_mode_id="im-pos",
            ionization_mode_polarity="+",
            calibration_collection_id=pos_collection,
        ),
    }
    samples = [
        _make_sample_item(sample_item_id="si-neg", ionization_mode_id="im-neg"),
        _make_sample_item(sample_item_id="si-pos", ionization_mode_id="im-pos"),
    ]

    patches = _base_patches()
    mocks = {k: p.start() for k, p in patches.items()}
    mocks["fetch_sample_file"].return_value = sample_file
    mocks["get_acquisition_dataset"].return_value = {"data": _make_dataset()}
    mocks["create_batches"].return_value = (
        samples,
        [
            _make_batch(sample_batch_id="batch-neg"),
            _make_batch(sample_batch_id="batch-pos"),
        ],
    )
    mocks["fetch_affected"].return_value = _make_affected_data(samples)

    async def _calibrate(sample, **_):
        return await calibrate(sample, sample_file)

    mocks["calibrate"].side_effect = _calibrate

    async def _get_ion_mode(model_class, mode_id):
        return ion_modes[mode_id]

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(side_effect=_get_ion_mode)
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mocks["async_session"].return_value = mock_ctx
    return mocks, sample_file


@pytest.mark.asyncio
async def test_calibrating_one_polarity_keeps_the_others_matches():
    """
    Calibrating one polarity must not cost the other polarity its matches.

    The m/z calibration is per file: applying a fit rescales the whole peak
    store and removes the matches of every sample item on the file. Matching
    the first polarity before calibrating the second used to lose the first
    polarity's fresh matches to that apply.
    """
    from mascope_backend.api.controllers.sample.files.process.service import (
        auto_process_sample_file,
    )

    matches: dict[str, bool] = {}

    async def calibrate(sample, sample_file):
        # What calibration_mz_apply does to the file's sample items.
        matches.clear()
        sample_file.mz_calibration = {"mode": "one-point", "verified": True}
        return True

    mocks, _ = _start_dual_polarity(
        calibrate=calibrate, neg_collection=None, pos_collection="cal-pos"
    )

    async def match(sample_item_id, **_):
        matches[sample_item_id] = True

    mocks["match"].side_effect = match

    await auto_process_sample_file(
        sample_file_id="sf-001", independent_transaction=True, process_id="proc-001"
    )

    mocks["calibrate"].assert_called_once()
    assert matches == {"si-neg": True, "si-pos": True}
    assert mocks["assign"].call_count == 2


@pytest.mark.asyncio
async def test_both_polarities_with_calibrants_match_on_the_acquisition_axis():
    """
    A file with a calibrant on both polarities is not calibrated at all.

    The file holds one m/z calibration, so the polarity calibrated last would
    set the axis for both. Both samples are matched on the acquisition axis
    instead.
    """
    from mascope_backend.api.controllers.sample.files.process.service import (
        auto_process_sample_file,
    )

    async def calibrate(sample, sample_file):
        raise AssertionError("a shared-calibration file must not be calibrated")

    mocks, _ = _start_dual_polarity(
        calibrate=calibrate, neg_collection="cal-neg", pos_collection="cal-pos"
    )

    await auto_process_sample_file(
        sample_file_id="sf-001", independent_transaction=True, process_id="proc-001"
    )

    mocks["calibrate"].assert_not_called()
    matched = {c.kwargs["sample_item_id"] for c in mocks["match"].call_args_list}
    assert matched == {"si-neg", "si-pos"}
    assert mocks["assign"].call_count == 2


@pytest.mark.parametrize(
    ("record", "matched"),
    [
        # An earlier run's failure marker: the verified gate would refuse both.
        ({"status": "failed", "verified": False}, set()),
        # An earlier run's verified fit stays, and both match on it.
        ({"mode": "one-point", "verified": True}, {"si-neg", "si-pos"}),
        # A TOF converter record is the acquisition axis, not a failed fit.
        ({"mode": 1, "par": [1.0, 0.0], "status": "unfitted"}, {"si-neg", "si-pos"}),
    ],
)
@pytest.mark.asyncio
async def test_skipped_shared_calibration_judges_the_record_an_earlier_run_left(
    record, matched
):
    """
    Re-running the pipeline without a reset keeps an earlier run's record.

    A file with a calibrant on both polarities is not calibrated here, so it
    is matched on whatever that record describes - and an unverified one would
    trip the verified gate and fail the pipeline, as on the single-calibration
    path.
    """
    from mascope_backend.api.controllers.sample.files.process.service import (
        auto_process_sample_file,
    )

    async def calibrate(sample, sample_file):
        raise AssertionError("a shared-calibration file must not be calibrated")

    mocks, sample_file = _start_dual_polarity(
        calibrate=calibrate, neg_collection="cal-neg", pos_collection="cal-pos"
    )
    sample_file.mz_calibration = record

    result = await auto_process_sample_file(
        sample_file_id="sf-001", independent_transaction=True, process_id="proc-001"
    )

    mocks["calibrate"].assert_not_called()
    assert {
        c.kwargs["sample_item_id"] for c in mocks["match"].call_args_list
    } == matched
    assert mocks["assign"].call_count == len(matched)
    assert "Auto-processing complete" in result["message"]


@pytest.mark.asyncio
async def test_unverified_calibration_holds_back_the_other_polarity():
    """
    An unverified record on the file is shared by every sample on it.

    The verified gate in the match computation would refuse the uncalibrated
    polarity too, and fail the pipeline with it. Neither sample is matched,
    and the pipeline still completes.
    """
    from mascope_backend.api.controllers.sample.files.process.service import (
        auto_process_sample_file,
    )

    async def calibrate(sample, sample_file):
        sample_file.mz_calibration = {"status": "failed", "verified": False}
        return False

    mocks, _ = _start_dual_polarity(
        calibrate=calibrate, neg_collection=None, pos_collection="cal-pos"
    )

    result = await auto_process_sample_file(
        sample_file_id="sf-001", independent_transaction=True, process_id="proc-001"
    )

    mocks["calibrate"].assert_called_once()
    mocks["match"].assert_not_called()
    mocks["assign"].assert_not_called()
    assert "Auto-processing complete" in result["message"]


@pytest.mark.asyncio
async def test_failed_refit_keeps_the_earlier_fit_for_the_other_polarity():
    """
    A fit that fails before applying leaves an earlier verified fit in place.

    The failure marker never overwrites an applied fit, so on reprocessing the
    uncalibrated polarity is still matched on it; only the failed one is held
    back.
    """
    from mascope_backend.api.controllers.sample.files.process.service import (
        auto_process_sample_file,
    )

    async def calibrate(sample, sample_file):
        return False

    mocks, sample_file = _start_dual_polarity(
        calibrate=calibrate, neg_collection=None, pos_collection="cal-pos"
    )
    sample_file.mz_calibration = {"mode": "one-point", "verified": True}

    await auto_process_sample_file(
        sample_file_id="sf-001", independent_transaction=True, process_id="proc-001"
    )

    mocks["match"].assert_called_once()
    assert mocks["match"].call_args.kwargs["sample_item_id"] == "si-neg"
    mocks["assign"].assert_called_once()


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
    # Partial sample items are cleared before every attempt, the first included:
    # the file may carry items from an earlier pipeline a restart cut short.
    assert cleanup.call_count == 2
    assert all(call.args == ("sf-retry",) for call in cleanup.call_args_list)


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
    # Cleared once, before the single attempt - not once per failure.
    cleanup.assert_called_once_with("sf-bad")


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


async def _run_until_given_up(
    sample_file_id: str,
    error: Exception,
    independent_transaction: bool = False,
) -> tuple[list[str], list[str], AsyncMock]:
    """
    Run the pipeline wrapper with a body that always raises ``error``.

    As spawned in production (an independent transaction) the decorator ends
    the run itself; otherwise it re-raises, as ApiException, to the caller.

    :return: The give-up lines logged at INFO and at ERROR, and the body mock.
    """
    from mascope_backend.api.controllers.sample.files.process import service
    from mascope_backend.api.lib.exceptions.api_exceptions import ApiException

    body = AsyncMock(side_effect=error)

    async def run():
        await service.auto_process_sample_file(
            sample_file_id=sample_file_id,
            independent_transaction=independent_transaction,
        )

    with (
        patch(f"{_SVC}._auto_process_sample_file", new=body),
        patch(f"{_SVC}._delete_partial_acquisition_items", new=AsyncMock()),
        patch.object(service, "_AUTO_PROCESS_RETRY_DELAYS_S", (0, 0, 0)),
        patch(f"{_NOTIF}.handle_notifications", new_callable=AsyncMock),
        patch(f"{_UTILS}.handle_reloads", new_callable=AsyncMock),
        captured_logs() as records,
    ):
        if independent_transaction:
            await run()
        else:
            with pytest.raises(ApiException):
                await run()

    def gave_up(level: str) -> list[str]:
        return [
            record["message"]
            for record in records
            if record["level"].name == level and "gave up" in record["message"]
        ]

    return gave_up("INFO"), gave_up("ERROR"), body


@pytest.mark.asyncio
async def test_names_the_file_when_it_gives_up():
    """
    Exhausting the retries must name the file, not just the attempts.

    A file whose pipeline never completes keeps its sample_file row and its
    batch still settles `ready`, so without this the shortfall is only
    visible by counting rows afterwards - the demo bundle shipped incomplete
    goldens exactly that way (see the demo coverage guard). It is named at
    INFO, which reaches the container and file logs; see the grouping test
    below for why the ERROR does not name it.
    """
    from mascope_backend.api.controllers.sample.files.process import service
    from mascope_backend.api.lib.exceptions.api_exceptions import ApiException

    info, errors, body = await _run_until_given_up(
        "sf-gaveup", ApiException("busy", {}, 503)
    )

    assert body.call_count == service._AUTO_PROCESS_RETRIES + 1
    assert len(info) == 1 and "sf-gaveup" in info[0] and "busy" in info[0], info
    # Still a fault, so still an ERROR - saying which kind.
    assert len(errors) == 1 and "status 503" in errors[0], errors


@pytest.mark.asyncio
async def test_names_the_file_on_a_non_recoverable_error():
    """The give-up log covers the no-retry path too, not just exhaustion."""
    from mascope_backend.api.lib.exceptions.api_exceptions import ApiException

    info, errors, body = await _run_until_given_up(
        "sf-corrupt", ApiException("corrupt", {}, 400)
    )

    assert body.call_count == 1  # not retried
    assert len(info) == 1 and "sf-corrupt" in info[0], info
    assert len(errors) == 1 and "status 400" in errors[0], errors


@pytest.mark.asyncio
async def test_give_up_error_reads_the_same_for_every_file():
    """
    The ERROR text depends on the kind of fault only, never on the file.

    Error monitoring groups issues by the formatted message. The give-up ERROR
    used to carry the file id and the error text, which opened a new issue -
    and a new alert - for every file: over three hundred in three days from a
    single production server. The same fault on two files has to read
    identically, while different faults still read apart.
    """
    from mascope_backend.api.lib.exceptions.api_exceptions import ApiException

    _, first, _ = await _run_until_given_up(
        "sf-first", ApiException("Sample 'one' is corrupt", {}, 400)
    )
    _, second, _ = await _run_until_given_up(
        "sf-second", ApiException("Sample 'two' is corrupt", {}, 400)
    )
    _, other, _ = await _run_until_given_up("sf-third", RuntimeError("boom"))

    assert len(first) == 1 and first == second, (first, second)
    assert "sf-" not in first[0] and "corrupt" not in first[0], first
    assert len(other) == 1 and "RuntimeError" in other[0], other


@pytest.mark.asyncio
async def test_raised_warning_gives_up_at_info_only():
    """
    A raised warning is a data condition, not a fault: no ERROR for it.

    An m/z calibration the match gate will not accept, say. The decorator
    already hands the warning to the instrument room as a notification, and
    the file is still named at INFO.
    """
    from mascope_backend.api.lib.exceptions.api_exceptions import ApiException

    info, errors, body = await _run_until_given_up(
        "sf-unverified",
        ApiException("m/z calibration is not verified for sample file", {}, 200),
        independent_transaction=True,
    )

    assert body.call_count == 1  # not retried
    assert len(info) == 1 and "sf-unverified" in info[0], info
    assert errors == [], errors
