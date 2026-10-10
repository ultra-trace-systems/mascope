"""
Unit tests for the auto-processing pipeline orchestrator.

Tests verify the orchestration logic of ``auto_process_sample_file``:
- Correct parameter threading (instrument, year, user_id)
- Workspace + dataset auto-creation via get_acquisition_dataset
- Batch and sample item creation
- Calibration skip logic (blank files, missing calibration collection)
- Match computation for each sample
- The processing status recorded at each stage
- Return structure

All external dependencies are mocked — no DB, file I/O, or Socket.IO required.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from test_utils import captured_logs

from mascope_backend.api.controllers.sample.files.process.bindings import (
    Declined,
)
from mascope_backend.api.controllers.sample.files.process.service import (
    ItemProvenance,
)

# Module path prefix for patching
from mascope_backend.api.controllers.sample.files.process.streams import (
    StoreStreams,
    StreamRows,
)
from mascope_backend.runtime import runtime
from mascope_runtime.logging import SENTRY_FINGERPRINT


_SVC = "mascope_backend.api.controllers.sample.files.process.service"
_NOTIF = "mascope_backend.socket.notifications"
_UTILS = "mascope_backend.api.lib.utils"
# The background-task decorator's module, which imports handle_notifications
# and handle_reloads by name: patched there, the decorator sees the mocks.
_FEATURES = "mascope_backend.api.lib.api_features"


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


def _outcome(verified: bool, reason: str | None = None):
    """What ``calibrate_with_retry`` returns: verified, or why not."""
    from mascope_backend.api.controllers.sample.files.process.service import (
        CalibrationOutcome,
    )

    return CalibrationOutcome(verified=verified, reason=reason)


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
        "resolve": patch(
            f"{_SVC}.resolve_ionization_modes_by_tokens",
            new_callable=AsyncMock,
            return_value=[_make_ionization_mode()],
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


@pytest.fixture(autouse=True)
def status():
    """The processing-status writer, recorded instead of written.

    Also stubs the scan stream census, which would read the file's props
    from the filestore, the method-binding learner, which would write to
    the database, and the reading of the file's acquisition record, which
    would query it: these files have none unless a test says so through
    ``status.declares``, and the rung that reads it is covered in
    test_auto_process_declared.py. Request the fixture by name to read what
    was recorded:
    ``[(status, detail), ...]`` in order via :func:`_recorded`.
    """
    with (
        patch(f"{_SVC}.record_processing_status", new_callable=AsyncMock) as record,
        patch(f"{_SVC}.read_scan_streams", new_callable=AsyncMock) as census,
        patch(f"{_SVC}.read_store_stream_keys", new_callable=AsyncMock) as stitched,
        patch(f"{_SVC}.pooled_streams_note") as note,
        patch(
            f"{_SVC}.read_overlap_readings_note",
            new_callable=AsyncMock,
            return_value=None,
        ) as overlaps,
        patch(f"{_SVC}.learn_method_bindings", new_callable=AsyncMock) as learn,
        patch(
            f"{_SVC}._modes_its_record_declares",
            new_callable=AsyncMock,
            return_value=([], None),
        ) as declares,
    ):
        census.return_value = []
        stitched.return_value = []
        note.return_value = None
        record.note = note
        record.overlaps = overlaps
        record.declares = declares
        record.census = census
        record.stitched = stitched
        record.learn = learn
        yield record


def _recorded(status) -> list[tuple[str, str | None]]:
    """The (status, detail) pairs recorded, in order."""
    return [(call.args[1].value, call.args[2]) for call in status.call_args_list]


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
    mocks["create_batches"].return_value = (
        [sample_item],
        [batch],
        StreamRows(),
        StoreStreams(),
    )
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
    mocks["create_batches"].return_value = (
        [sample_item],
        [batch],
        StreamRows(),
        StoreStreams(),
    )
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
    mocks["create_batches"].return_value = (
        [sample_item],
        [batch],
        StreamRows(),
        StoreStreams(),
    )
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
    mocks["create_batches"].return_value = (
        [sample_item],
        [batch],
        StreamRows(),
        StoreStreams(),
    )
    mocks["fetch_affected"].return_value = _make_affected_data([sample_item])
    mocks["calibrate"].return_value = _outcome(False, "The m/z calibration failed.")

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

    A blank is fully processed once its sample items exist. The real
    match_compute_sample refuses a blank with a raised warning, which would end
    the run and report a routine file to the instrument room as a warning; a
    mocked match cannot show that, so this pins that it is never called.
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
    mocks["create_batches"].return_value = (
        [sample_item],
        [batch],
        StreamRows(),
        StoreStreams(),
    )
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
    mocks["create_batches"].return_value = (
        [sample_item],
        [batch],
        StreamRows(),
        StoreStreams(),
    )
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
        StreamRows(),
        StoreStreams(),
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
        StreamRows(),
        StoreStreams(),
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
        return _outcome(True)

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
        # A TOF converter record, which the verified gate refuses as well.
        ({"mode": 1, "par": [1.0, 0.0], "status": "unfitted"}, set()),
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
    path. A TOF file's converter record is never verified, so it is held back
    too.
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
        return _outcome(False, "The m/z calibration failed.")

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
        return _outcome(False, "The m/z calibration failed.")

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
    mocks["create_batches"].return_value = (
        [sample_item],
        [batch],
        StreamRows(),
        StoreStreams(),
    )
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
        ionization_modes=mocks["resolve"].return_value,
        provenance={
            mode.ionization_mode_id: ItemProvenance("token")
            for mode in mocks["resolve"].return_value
        },
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
    mocks["create_batches"].return_value = (
        [sample_item],
        [batch],
        StreamRows(),
        StoreStreams(),
    )
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
async def test_every_attempt_of_a_run_shares_one_recorded_binding_set():
    """So a retried run teaches its method once, not once per attempt.

    The row-level guard cannot do this: the backoffs are tens of seconds, so
    another file of the same key is easily learned in between and the
    comparison with whatever the row last saw misses.
    """
    from mascope_backend.api.controllers.sample.files.process import service
    from mascope_backend.api.lib.exceptions.api_exceptions import ApiException

    ok = {"message": "done", "_notification_data": {}}
    body = AsyncMock(side_effect=[ApiException("busy", {}, 503), ok])

    with (
        patch(f"{_SVC}._auto_process_sample_file", new=body),
        patch(f"{_SVC}._delete_partial_acquisition_items", new=AsyncMock()),
        patch.object(service, "_AUTO_PROCESS_RETRY_DELAYS_S", (0, 0, 0)),
        patch(f"{_NOTIF}.handle_notifications", new_callable=AsyncMock),
        patch(f"{_UTILS}.handle_reloads", new_callable=AsyncMock),
    ):
        await service.auto_process_sample_file(
            sample_file_id="sf-retry", independent_transaction=True
        )

    passed = [call.kwargs["recorded_bindings"] for call in body.call_args_list]
    assert len(passed) == 2
    assert passed[0] is passed[1]


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


def _monitored(records: list) -> list:
    """Records that would reach error monitoring (WARNING and above)."""
    return [r for r in records if r["level"].no >= runtime.logger.level("WARNING").no]


def _lines(records: list) -> list[tuple[str, str]]:
    """Level and message of each record, for assertions that read."""
    return [(r["level"].name, r["message"]) for r in records]


def _gave_up_at_info(records: list) -> list[str]:
    """The give-up lines logged at INFO."""
    return [
        message
        for level, message in _lines(records)
        if level == "INFO" and "gave up" in message
    ]


async def _run_until_given_up(
    sample_file_id: str, error: Exception
) -> tuple[list, AsyncMock]:
    """
    Run the pipeline wrapper, as the routes spawn it, with a body that always
    raises ``error``.

    An independent transaction, so the decorator reports the outcome itself
    and nothing reaches the caller. It imports its notification and reload
    helpers by name, so they are patched where it looks them up.

    :return: The log records emitted meanwhile, and the body mock.
    """
    from mascope_backend.api.controllers.sample.files.process import service

    body = AsyncMock(side_effect=error)
    with (
        patch(f"{_SVC}._auto_process_sample_file", new=body),
        patch(f"{_SVC}._delete_partial_acquisition_items", new=AsyncMock()),
        patch.object(service, "_AUTO_PROCESS_RETRY_DELAYS_S", (0, 0, 0)),
        patch(f"{_FEATURES}.handle_notifications", new_callable=AsyncMock),
        patch(f"{_FEATURES}.handle_reloads", new_callable=AsyncMock),
        captured_logs() as records,
    ):
        await service.auto_process_sample_file(
            sample_file_id=sample_file_id, independent_transaction=True
        )
    return records, body


@pytest.mark.asyncio
async def test_names_the_file_when_it_gives_up():
    """
    Exhausting the retries must name the file, not just the attempts.

    A file whose pipeline never completes keeps its sample_file row and its
    batch still settles `ready`, so without this the shortfall is only
    visible by counting rows afterwards - the demo bundle shipped incomplete
    goldens exactly that way (see the demo coverage guard). It is named at
    INFO, which reaches the container and file logs, and so is each retry
    before it: monitoring gets one ERROR, and it names no file.
    """
    from mascope_backend.api.controllers.sample.files.process import service
    from mascope_backend.api.lib.exceptions.api_exceptions import ApiException

    records, body = await _run_until_given_up(
        "sf-gaveup", ApiException("busy", {}, 503)
    )

    assert body.call_count == service._AUTO_PROCESS_RETRIES + 1
    info = _gave_up_at_info(records)
    assert len(info) == 1 and "sf-gaveup" in info[0] and "busy" in info[0], info
    monitored = _lines(_monitored(records))
    assert len(monitored) == 1 and monitored[0][0] == "ERROR", monitored
    assert "status 503" in monitored[0][1], monitored
    assert "sf-gaveup" not in monitored[0][1], monitored


@pytest.mark.asyncio
async def test_names_the_file_on_a_non_recoverable_error():
    """The give-up log covers the no-retry path too, not just exhaustion."""
    from mascope_backend.api.lib.exceptions.api_exceptions import ApiException

    records, body = await _run_until_given_up(
        "sf-corrupt", ApiException("corrupt", {}, 500)
    )

    assert body.call_count == 1  # not retried
    info = _gave_up_at_info(records)
    assert len(info) == 1 and "sf-corrupt" in info[0], info
    monitored = _lines(_monitored(records))
    assert len(monitored) == 1 and "status 500" in monitored[0][1], monitored


@pytest.mark.asyncio
async def test_give_up_error_reads_the_same_for_every_file():
    """
    The ERROR text depends on the kind of fault only, never on the file.

    The same fault on two files has to read identically - the file and the
    error are named at INFO - while different faults still read apart, and
    error monitoring groups them apart too: by status code, not by the call
    site every give-up shares.
    """
    from mascope_backend.api.lib.exceptions.api_exceptions import ApiException

    first, _ = await _run_until_given_up(
        "sf-first", ApiException("Sample 'one' is corrupt", {}, 500)
    )
    second, _ = await _run_until_given_up(
        "sf-second", ApiException("Sample 'two' is corrupt", {}, 500)
    )
    other, _ = await _run_until_given_up("sf-third", ApiException("busy", {}, 503))

    first, second, other = (_monitored(r) for r in (first, second, other))
    fingerprints = [
        r[0]["extra"].get(SENTRY_FINGERPRINT) for r in (first, second, other)
    ]
    first, second, other = (_lines(r) for r in (first, second, other))
    assert len(first) == 1 and first == second, (first, second)
    assert "sf-" not in first[0][1] and "corrupt" not in first[0][1], first
    assert len(other) == 1 and other != first, other
    assert fingerprints[0] == fingerprints[1] != fingerprints[2], fingerprints
    assert fingerprints[0] is not None, fingerprints


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "message"),
    [
        (200, "m/z calibration is not verified for sample file"),
        (207, "Some samples could not be matched"),
        (404, "Sample file not found"),
    ],
)
async def test_a_routine_give_up_stays_out_of_monitoring(status_code, message):
    """
    A raised warning or a 4xx is routine, as the rest of the API classifies it.

    The decorator still hands it to the user and the file is still named at
    INFO, but nothing reaches monitoring: there is nothing to act on.
    """
    from mascope_backend.api.lib.exceptions.api_exceptions import ApiException

    records, body = await _run_until_given_up(
        "sf-routine", ApiException(message, {}, status_code)
    )

    assert body.call_count == 1  # not retried
    info = _gave_up_at_info(records)
    assert len(info) == 1 and "sf-routine" in info[0], info
    assert _monitored(records) == [], _lines(_monitored(records))


@pytest.mark.asyncio
async def test_a_fault_outside_the_api_is_reported_once():
    """
    An exception that is not an ApiException is left to the decorator.

    Its process_exception logs a fault with the traceback, so an ERROR from
    the give-up as well would be a second monitoring event for one incident.
    """
    records, _ = await _run_until_given_up("sf-boom", RuntimeError("boom"))

    info = _gave_up_at_info(records)
    assert len(info) == 1 and "sf-boom" in info[0], info
    monitored = _monitored(records)
    assert len(monitored) == 1, _lines(monitored)
    # The decorator's record, traceback and all - not the give-up.
    assert monitored[0]["exception"] is not None, _lines(monitored)
    assert "gave up" not in monitored[0]["message"], _lines(monitored)


@pytest.mark.asyncio
async def test_a_client_class_error_outside_the_api_stays_at_info():
    """
    A ValueError - a file name that matches no ionization mode, say - is a
    client-class error: the decorator logs it at INFO, and the give-up must
    not contradict that with an ERROR.
    """
    records, _ = await _run_until_given_up(
        "sf-no-token", ValueError("No ionization mode tokens found for file")
    )

    assert len(_gave_up_at_info(records)) == 1, _lines(records)
    assert _monitored(records) == [], _lines(_monitored(records))


# ---------------------------------------------------------------------------
# Processing status
# ---------------------------------------------------------------------------


def _start_single(
    *, calibration_collection_id="cal-001", instrument_function_id="ifunc-001"
):
    """Start the base patches for a one-polarity file bound to one mode."""
    sample_file = _make_sample_file(instrument_function_id=instrument_function_id)
    sample_file.mz_calibration = None
    ion_mode = _make_ionization_mode(
        calibration_collection_id=calibration_collection_id
    )
    sample_item = _make_sample_item()

    patches = _base_patches()
    mocks = {k: p.start() for k, p in patches.items()}
    mocks["fetch_sample_file"].return_value = sample_file
    mocks["get_acquisition_dataset"].return_value = {"data": _make_dataset()}
    mocks["resolve"].return_value = [ion_mode]
    mocks["create_batches"].return_value = (
        [sample_item],
        [_make_batch()],
        StreamRows(),
        StoreStreams(),
    )
    mocks["fetch_affected"].return_value = _make_affected_data([sample_item])

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=ion_mode)
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mocks["async_session"].return_value = mock_ctx
    return mocks, sample_file


async def _run_pipeline(**kwargs):
    from mascope_backend.api.controllers.sample.files.process.service import (
        auto_process_sample_file,
    )

    return await auto_process_sample_file(
        sample_file_id="sf-001",
        independent_transaction=True,
        user_id=42,
        process_id="proc-001",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_records_each_stage_a_calibrated_file_reaches(status):
    _start_single()

    await _run_pipeline()

    recorded = _recorded(status)
    assert [state for state, _ in recorded] == ["bound", "calibrated", "done"]
    assert recorded[0][1] == "Bound by file-name token to 'Bromide RI' (-)."
    assert recorded[-1][1] == "Matched 1 sample."
    assert all(call.args[0] == "sf-001" for call in status.call_args_list)


def _no_token(filename="2025.09.20_test_file.raw"):
    """What the token rule raises for a file no token names."""
    from mascope_backend.api.new.ionization.modes.util import NoTokenMatchError

    return NoTokenMatchError(
        f"No ionization mode tokens found for file {filename}. "
        "Configure tokens in ionization settings"
    )


def _routing(mode, binding_id="mb-000000000001"):
    from mascope_backend.api.controllers.sample.files.process.bindings import (
        MethodRouting,
    )

    return MethodRouting(mode=mode, binding_id=binding_id)


@pytest.mark.asyncio
async def test_a_token_less_file_parks_while_the_rung_is_off(status):
    """The default. Nothing about routing changes until a site switches it."""
    mocks, _ = _start_single()
    mocks["resolve"].side_effect = _no_token()

    with patch(f"{_SVC}.routes_on_method_binding", return_value=False):
        with patch(
            f"{_SVC}.resolve_modes_by_method_binding", new_callable=AsyncMock
        ) as rung:
            result = await _run_pipeline()

    assert result["status"] == "parked"
    # Not even consulted: a shadow deployment runs no extra query per file.
    rung.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_rung_binds_a_token_less_file_to_its_method(status):
    """What `method_binding = "route"` buys: this file parks on every other
    deployment."""
    mocks, _ = _start_single()
    mode = mocks["resolve"].return_value[0]
    mocks["resolve"].side_effect = _no_token()

    with patch(f"{_SVC}.routes_on_method_binding", return_value=True):
        with patch(
            f"{_SVC}.resolve_modes_by_method_binding",
            new_callable=AsyncMock,
            return_value=([_routing(mode)], None),
        ):
            await _run_pipeline()

    # Bound, not parked, and the items say which rung and which row.
    assert [state for state, _ in _recorded(status)] == [
        "bound",
        "calibrated",
        "done",
    ]
    assert _recorded(status)[0][1] == (
        "Bound to 'Bromide RI' (-) by its acquisition method."
    )
    assert _recorded_provenance(mocks) == {
        "im-001": ItemProvenance("method", "mb-000000000001")
    }
    # And the binding learns nothing from the file it routed itself: that
    # would be counting its own answer as evidence for itself.
    status.learn.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_park_detail_says_what_the_method_could_not_tell_either(status):
    """Two reasons, because a person reading it has two things to check."""
    mocks, _ = _start_single()
    mocks["resolve"].side_effect = _no_token()

    with patch(f"{_SVC}.routes_on_method_binding", return_value=True):
        with patch(
            f"{_SVC}.resolve_modes_by_method_binding",
            new_callable=AsyncMock,
            return_value=(
                [],
                Declined(
                    "its acquisition method has not been seen before",
                    "Choose a chemistry for one file of this method and the "
                    "rest will follow it.",
                ),
            ),
        ):
            result = await _run_pipeline()

    assert result["status"] == "parked"
    (state, detail) = _recorded(status)[0]
    assert state == "needs_chemistry"
    # Asserted as one string, not as two substrings: the token rule's message
    # ends without a full stop, so the two sentences ran together.
    assert detail == (
        "No ionization mode tokens found for file 2025.09.20_test_file.raw. "
        "Configure tokens in ionization settings. Its acquisition method does "
        "not say either: its acquisition method has not been seen before. "
        "Choose a chemistry for one file of this method and the rest will "
        "follow it."
    )


@pytest.mark.asyncio
async def test_the_remedy_comes_from_the_guard_that_declined(status):
    """Four of the six guards are not fixed by choosing one file's chemistry.

    The call site joins what the guard said rather than appending advice of
    its own, because the wrong version of that advice - "choose one and the
    rest follow" for an instrument that reports the same method name for every
    acquisition - is the one most people on the fleet would read.
    """
    mocks, _ = _start_single()
    mocks["resolve"].side_effect = _no_token()

    with patch(f"{_SVC}.routes_on_method_binding", return_value=True):
        with patch(
            f"{_SVC}.resolve_modes_by_method_binding",
            new_callable=AsyncMock,
            return_value=(
                [],
                Declined("it reports no method name", "Use a filename token."),
            ),
        ):
            await _run_pipeline()

    (_, detail) = _recorded(status)[0]
    assert detail.endswith(
        "Its acquisition method does not say either: it reports no method "
        "name. Use a filename token."
    )


@pytest.mark.asyncio
async def test_an_ambiguous_name_does_not_reach_the_rung(status):
    """A name carrying two tokens is a configuration to fix.

    The binding must not stand in for it: that would route a file whose own
    name says two contradictory things, and hide the thing to correct.
    """
    mocks, _ = _start_single()
    mocks["resolve"].side_effect = ValueError(
        "Ionization mode tokens must match exactly one mode per polarity"
    )

    with patch(f"{_SVC}.routes_on_method_binding", return_value=True):
        with patch(
            f"{_SVC}.resolve_modes_by_method_binding", new_callable=AsyncMock
        ) as rung:
            result = await _run_pipeline()

    assert result["status"] == "parked"
    rung.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_file_that_binds_to_nothing_waits_for_a_chemistry(status):
    """Routing found no mode: the file is parked, not failed."""
    mocks, _ = _start_single()
    message = (
        "No ionization mode tokens found for file 2025.09.20_test_file.raw. "
        "Configure tokens in ionization settings"
    )
    mocks["resolve"].side_effect = ValueError(message)

    with patch(f"{_FEATURES}.handle_notifications", new_callable=AsyncMock) as notify:
        result = await _run_pipeline()

    # The detail is the file's reason; the live notice says what to do.
    assert _recorded(status) == [("needs_chemistry", f"{message}.")]
    mocks["create_batches"].assert_not_called()
    mocks["calibrate"].assert_not_called()
    mocks["match"].assert_not_called()
    assert result["status"] == "parked"
    # Reported as a warning for the instrument, not as a failed run.
    (call,) = notify.call_args_list
    assert call.args[0] == ["instrument"]
    assert call.args[1].status == "warning"
    assert call.args[1].message == f"{message}. Or choose its chemistry in Raw files."
    assert result["_notification_data"]["instrument"] == "Orbion"


@pytest.mark.asyncio
async def test_a_failed_calibration_is_the_files_outcome(status):
    mocks, sample_file = _start_single()

    async def calibrate(**_):
        sample_file.mz_calibration = {
            "status": "failed",
            "verified": False,
            "error": "No calibration peaks found.",
        }
        return _outcome(
            False, "The m/z calibration failed: No calibration peaks found."
        )

    mocks["calibrate"].side_effect = calibrate

    await _run_pipeline()

    recorded = _recorded(status)
    assert [state for state, _ in recorded] == ["bound", "calibration_failed"]
    assert recorded[-1][1] == (
        "The m/z calibration failed: No calibration peaks found. "
        "Matching and peak assignment were skipped."
    )


@pytest.mark.asyncio
async def test_a_calibration_below_the_bar_names_its_reasons(status):
    mocks, sample_file = _start_single()

    async def calibrate(**_):
        sample_file.mz_calibration = {
            "status": "poor",
            "verified": False,
            "quality_issues": [{"message": "Only 2 calibration points."}],
        }
        return _outcome(
            False,
            "The m/z calibration is below the quality bar: Only 2 calibration points.",
        )

    mocks["calibrate"].side_effect = calibrate

    await _run_pipeline()

    state, detail = _recorded(status)[-1]
    assert state == "calibration_failed"
    assert detail.startswith(
        "The m/z calibration is below the quality bar: Only 2 calibration points."
    )


@pytest.mark.asyncio
async def test_a_fit_below_the_bar_that_the_gate_lets_through_is_done(status):
    """Under a gate that only warns, the file is matched and says why to look."""
    mocks, sample_file = _start_single()

    async def calibrate(**_):
        sample_file.mz_calibration = {
            "status": "poor",
            "verified": True,
            "quality_issues": [{"message": "Mean error 4.1 ppm."}],
        }
        return _outcome(True)

    mocks["calibrate"].side_effect = calibrate

    await _run_pipeline()

    assert _recorded(status)[-1] == (
        "done",
        "Matched 1 sample. The m/z calibration is below the quality bar: "
        "Mean error 4.1 ppm.",
    )


@pytest.mark.asyncio
async def test_done_says_why_a_file_was_not_calibrated(status):
    _start_single(calibration_collection_id=None)

    await _run_pipeline()

    assert _recorded(status)[-1] == (
        "done",
        "Matched 1 sample. Not m/z calibrated: ionization mode 'Bromide RI' "
        "has no calibration collection.",
    )


@pytest.mark.asyncio
async def test_a_blank_file_is_done_without_being_matched(status):
    """A blank has no peaks: nothing to calibrate or match, and nothing wrong."""
    mocks, _ = _start_single(instrument_function_id=None)

    await _run_pipeline()

    mocks["match"].assert_not_called()
    assert _recorded(status) == [
        ("bound", "Bound by file-name token to 'Bromide RI' (-)."),
        ("done", "Blank measurement: no peaks to calibrate, match or assign."),
    ]


@pytest.mark.asyncio
async def test_done_says_a_shared_calibration_was_skipped(status):
    async def calibrate(sample, sample_file):
        raise AssertionError("a shared-calibration file must not be calibrated")

    _start_dual_polarity(
        calibrate=calibrate, neg_collection="cal-neg", pos_collection="cal-pos"
    )

    await _run_pipeline()

    assert _recorded(status)[-1] == (
        "done",
        "Matched 2 samples. Not m/z calibrated: 2 of its samples have a "
        "calibration collection, and a file holds one m/z calibration for all "
        "of them. Matched on the acquisition axis.",
    )


@pytest.mark.asyncio
async def test_an_unverified_record_holds_back_the_whole_file(status):
    """The failure of one polarity's fit is the outcome for both."""

    async def calibrate(sample, sample_file):
        sample_file.mz_calibration = {"status": "failed", "verified": False}
        return _outcome(False, "The m/z calibration failed.")

    _start_dual_polarity(
        calibrate=calibrate, neg_collection=None, pos_collection="cal-pos"
    )

    await _run_pipeline()

    assert _recorded(status)[-1] == (
        "calibration_failed",
        "The m/z calibration failed. Matching and peak assignment were skipped.",
    )


@pytest.mark.asyncio
async def test_a_routed_file_teaches_its_method_binding(status):
    """What the file bound to is recorded against its acquisition method."""
    census = [{"key": "FTMS - p NSI Full ms", "signature": {"polarity": "-"}}]
    status.census.return_value = census
    _start_single()

    await _run_pipeline()

    status.learn.assert_awaited_once()
    call = status.learn.await_args
    assert call.kwargs["source"] == "token"
    assert call.kwargs["streams"] == census


@pytest.mark.asyncio
async def test_a_file_a_person_routed_teaches_its_method_binding(status):
    """A chosen mode is evidence about the method too, on a stronger rung."""
    _start_single()
    chosen = _make_ionization_mode(ionization_mode_name="Nitrate")

    with patch(
        f"{_SVC}.fetch_ionization_modes",
        new_callable=AsyncMock,
        return_value=[chosen],
    ):
        await _run_pipeline(ionization_mode_ids=["im-001"])

    assert status.learn.await_args.kwargs["source"] == "explicit"


def _recorded_provenance(mocks) -> dict:
    """The provenance the run passed for each mode it bound."""
    return mocks["create_batches"].await_args.kwargs["provenance"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "chosen, rung",
    [(None, "token"), (["im-001"], "explicit")],
    ids=["a token routed it", "a person chose"],
)
async def test_the_items_record_the_rung_the_binding_was_taught(status, chosen, rung):
    """One rung, written to the items and taught to the binding.

    Asserted together rather than in two tests, because the thing worth
    pinning is that they agree: a report counting items by rung is read
    beside a table learned from those same rungs, and the two drifting apart
    is the failure that would not look like one.
    """
    mocks, _ = _start_single()

    with patch(
        f"{_SVC}.fetch_ionization_modes",
        new_callable=AsyncMock,
        return_value=mocks["resolve"].return_value,
    ):
        await _run_pipeline(ionization_mode_ids=chosen)

    assert _recorded_provenance(mocks) == {
        "im-001": ItemProvenance(rung),
    }
    assert status.learn.await_args.kwargs["source"] == rung


@pytest.mark.asyncio
async def test_a_kept_mode_carries_the_rung_that_bound_it_before(status):
    """Re-processing a file no token binds is nobody's decision.

    ``ionization_mode_ids`` is not only the choose-chemistry route. A file a
    token bound months ago reaches the same path once that token has been
    renamed, and its modes are then copied from its own previous samples. The
    rung has to come with them: calling that "explicit" would say a person
    vouched for a chemistry nobody was asked about, and the count of explicit
    bindings in any report would climb every time a batch was re-processed.
    """
    mocks, _ = _start_single()
    kept = {"im-001": ItemProvenance("token", "mb-000000000001")}

    with patch(
        f"{_SVC}.fetch_ionization_modes",
        new_callable=AsyncMock,
        return_value=mocks["resolve"].return_value,
    ):
        await _run_pipeline(ionization_mode_ids=["im-001"], kept_provenance=kept)

    assert _recorded_provenance(mocks) == kept
    # And the method learns nothing: the observation was recorded when the
    # mode was first matched or chosen, and repeating it would both claim a
    # strength nobody gave and let a re-processed batch drag the binding
    # back toward the row those files were bound under.
    status.learn.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_kept_mode_from_before_the_columns_invents_no_rung(status):
    """NULL carries forward as NULL, which is the one honest answer."""
    mocks, _ = _start_single()

    with patch(
        f"{_SVC}.fetch_ionization_modes",
        new_callable=AsyncMock,
        return_value=mocks["resolve"].return_value,
    ):
        await _run_pipeline(
            ionization_mode_ids=["im-001"],
            kept_provenance={"im-001": ItemProvenance()},
        )

    assert _recorded_provenance(mocks) == {"im-001": ItemProvenance(None, None)}
    status.learn.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_mode_missing_from_the_kept_provenance_records_nothing(status):
    """A mode whose own item predates the columns is not given a rung."""
    mocks, _ = _start_single()

    with patch(
        f"{_SVC}.fetch_ionization_modes",
        new_callable=AsyncMock,
        return_value=mocks["resolve"].return_value,
    ):
        await _run_pipeline(ionization_mode_ids=["im-001"], kept_provenance={})

    assert _recorded_provenance(mocks) == {"im-001": ItemProvenance()}


@pytest.mark.asyncio
async def test_the_note_is_told_which_streams_the_store_stitched(status):
    """A file whose peaks were detected per stream is not described as
    pooled: the note is given the census and the store's streams."""
    census = [{"key": "A", "signature": {"polarity": "-", "ms_order": 1}}]
    status.census.return_value = census
    status.stitched.return_value = ["A", "B"]
    _start_single()

    await _run_pipeline()

    status.note.assert_called_once_with(census, ["A", "B"])


@pytest.mark.asyncio
async def test_a_row_kept_for_a_sample_is_named_in_every_later_status(status):
    """A stream row the file no longer describes, kept because a sample still
    reads it, is reported from the status after the items on."""
    mocks, _sample_file = _start_single()
    samples, batches, _rows, _found = mocks["create_batches"].return_value
    mocks["create_batches"].return_value = (
        samples,
        batches,
        StreamRows(
            kept={"FTMS - p NSI Full ms [40.0000-138.0000] R=120000": ["si-copy"]}
        ),
        StoreStreams(),
    )

    await _run_pipeline()

    details = [detail for _status, detail in _recorded(status)]
    after_items = details[
        details.index(next(d for d in details if d and "is kept because" in d)) :
    ]
    assert after_items and all(
        "Scan stream 'FTMS - p NSI Full ms [40.0000-138.0000] R=120000' is no longer "
        "among the file's streams and is kept because a sample si-copy still reads it."
        in detail
        for detail in after_items
    )


@pytest.mark.asyncio
async def test_a_stale_peak_store_is_named_in_every_later_status(status):
    """A per-stream store the file no longer reads back is not refused: the
    rows are written pooled, and the statuses from the items on say why."""
    mocks, _sample_file = _start_single()
    samples, batches, rows, _found = mocks["create_batches"].return_value
    mocks["create_batches"].return_value = (
        samples,
        batches,
        rows,
        StoreStreams(streams=[{"key": "A", "signature": {}}], stale=["A event=2"]),
    )

    await _run_pipeline()

    details = [detail for _status, detail in _recorded(status)]
    first = details.index(next(d for d in details if d and "peak store" in d))
    assert all(
        "The file's peak store holds scan streams the file does not read back "
        "now: A event=2. Its peaks are detected again when a match meets it; "
        "until then its streams are recorded as pooled." in detail
        for detail in details[first:]
    )


#: What a record that named a chemistry nothing here answers to leaves in
#: the file's status (``_modes_its_record_declares``).
UNANSWERED = (
    "Its acquisition record names the chemistry 'Br', but no ionization mode "
    "of this instrument has that token in a polarity the file holds"
)


@pytest.mark.asyncio
async def test_a_file_its_name_bound_still_says_what_its_record_named_when_done(
    status,
):
    """The record names one chemistry and the name bound the file to another.
    Whoever opens the file reads the status its run ended on, so the sentence
    is in every status from the binding on, and not in the first alone."""
    status.declares.return_value = ([], UNANSWERED)
    _start_single()

    await _run_pipeline()

    assert _recorded(status) == [
        ("bound", f"Bound by file-name token to 'Bromide RI' (-). {UNANSWERED}."),
        ("calibrated", f"{UNANSWERED}."),
        ("done", f"Matched 1 sample. {UNANSWERED}."),
    ]


@pytest.mark.asyncio
async def test_a_failed_calibration_says_what_the_record_named_as_well(status):
    status.declares.return_value = ([], UNANSWERED)
    mocks, _ = _start_single()
    mocks["calibrate"].return_value = _outcome(
        False, "The m/z calibration failed: No calibration peaks found."
    )

    await _run_pipeline()

    assert _recorded(status)[-1] == (
        "calibration_failed",
        "The m/z calibration failed: No calibration peaks found. Matching and "
        f"peak assignment were skipped. {UNANSWERED}.",
    )


@pytest.mark.asyncio
async def test_a_blank_file_says_what_its_record_named_as_well(status):
    status.declares.return_value = ([], UNANSWERED)
    _start_single(instrument_function_id=None)

    await _run_pipeline()

    assert _recorded(status)[-1] == (
        "done",
        f"Blank measurement: no peaks to calibrate, match or assign. {UNANSWERED}.",
    )


@pytest.mark.asyncio
async def test_what_the_record_named_comes_before_what_the_streams_say(status):
    note = "Polarity - pools 2 MS1 scan streams into one peak list: A; B."
    status.note.return_value = note
    status.declares.return_value = ([], UNANSWERED)
    _start_single()

    await _run_pipeline()

    assert _recorded(status)[-1] == (
        "done",
        f"Matched 1 sample. {UNANSWERED}. {note}",
    )


@pytest.mark.asyncio
async def test_every_status_carries_the_pooled_streams_note(status):
    """The note describes the file, so no stage may drop it."""
    note = "Polarity - pools 2 MS1 scan streams into one peak list: A; B."
    status.note.return_value = note
    _start_single()

    await _run_pipeline()

    recorded = _recorded(status)
    assert [state for state, _ in recorded] == ["bound", "calibrated", "done"]
    assert all(detail.endswith(note) for _, detail in recorded)
    assert recorded[1][1] == note


@pytest.mark.asyncio
async def test_every_status_says_what_a_stitched_files_ranges_read(status):
    """Beside the streams themselves: it is as much a fact about the file."""
    streams = "Polarity - stitches 2 MS1 scan streams into one spectrum: A; B."
    overlaps = "Where two scan ranges overlap: B reads the 26 ions it shares with A."
    status.note.return_value = streams
    status.overlaps.return_value = overlaps
    _start_single()

    await _run_pipeline()

    recorded = _recorded(status)
    assert [state for state, _ in recorded] == ["bound", "calibrated", "done"]
    assert all(detail.endswith(f"{streams} {overlaps}") for _, detail in recorded)


@pytest.mark.asyncio
async def test_a_file_that_is_not_stitched_says_nothing_of_overlaps(status):
    status.note.return_value = "Polarity - pools 2 MS1 scan streams: A; B."
    _start_single()

    await _run_pipeline()

    assert _recorded(status)[-1] == (
        "done",
        "Matched 1 sample. Polarity - pools 2 MS1 scan streams: A; B.",
    )


@pytest.mark.asyncio
async def test_done_names_the_ranges_that_run_on_a_neighbours_calibration(status):
    """A stitched file is calibrated range by range, and a range that holds
    no calibrant of the collection is matched on a neighbour's calibration:
    the file says which, so that a collection that falls short of a window
    is seen."""
    mocks, sample_file = _start_single()

    async def calibrate(**_):
        sample_file.mz_calibration = {
            "status": "ok",
            "verified": True,
            "quality": {
                "segments": [
                    {"label": "m/z 40-138", "source": "anchors"},
                    {
                        "label": "m/z 132-460",
                        "source": "borrowed",
                        "origin": "m/z 40-138",
                    },
                ]
            },
        }
        return _outcome(True)

    mocks["calibrate"].side_effect = calibrate

    await _run_pipeline()

    assert _recorded(status)[-1] == (
        "done",
        "Matched 1 sample. m/z 132-460 holds no calibrant: given the calibration "
        "of m/z 40-138 as it is.",
    )


@pytest.mark.asyncio
async def test_done_names_them_after_what_the_calibration_fell_short_of(status):
    mocks, sample_file = _start_single()

    async def calibrate(**_):
        sample_file.mz_calibration = {
            "status": "poor",
            "verified": True,
            "quality_issues": [{"message": "Mean error 4.1 ppm."}],
            "quality": {
                "segments": [
                    {
                        "label": "m/z 66-124",
                        "source": "overlap",
                        "origin": "m/z 40-138",
                        "shared_ions": 26,
                    }
                ]
            },
        }
        return _outcome(True)

    mocks["calibrate"].side_effect = calibrate

    await _run_pipeline()

    assert _recorded(status)[-1] == (
        "done",
        "Matched 1 sample. The m/z calibration is below the quality bar: "
        "Mean error 4.1 ppm. m/z 66-124 holds no calibrant: calibrated from "
        "m/z 40-138 across the 26 ions both measure.",
    )


@pytest.mark.asyncio
async def test_a_file_held_back_from_matching_does_not_name_them(status):
    """Its samples were not matched on any calibration, which is what it says."""
    mocks, sample_file = _start_single()

    async def calibrate(**_):
        sample_file.mz_calibration = {
            "status": "poor",
            "verified": False,
            "quality_issues": [{"message": "Mean error 4.1 ppm."}],
            "quality": {
                "segments": [
                    {
                        "label": "m/z 132-460",
                        "source": "borrowed",
                        "origin": "m/z 40-138",
                    }
                ]
            },
        }
        return _outcome(False)

    mocks["calibrate"].side_effect = calibrate

    await _run_pipeline()

    state, detail = _recorded(status)[-1]
    assert state == "calibration_failed"
    assert "holds no calibrant" not in detail


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "record",
    [
        {"mode": 1, "par": [1.0, 0.0], "status": "unfitted", "verified": False},
        # Registered before the converter's record was stamped.
        {"mode": 1, "par": [1.0, 0.0]},
    ],
)
async def test_a_tof_file_without_calibrants_is_held_back(status, record):
    """The match gate refuses a converter record, so the file is not matched.

    Left to the gate, the refusal would end the run on a warning and the file
    would be recorded as failed; it is a calibration the pipeline could not
    make, and says what is missing.
    """
    mocks, sample_file = _start_single(calibration_collection_id=None)
    sample_file.mz_calibration = record

    result = await _run_pipeline()

    mocks["match"].assert_not_called()
    mocks["assign"].assert_not_called()
    assert "Auto-processing complete" in result["message"]
    assert _recorded(status)[-1] == (
        "calibration_failed",
        "Not m/z calibrated: ionization mode 'Bromide RI' has no calibration "
        "collection. Matching needs a verified m/z calibration. Matching and "
        "peak assignment were skipped.",
    )


@pytest.mark.asyncio
async def test_the_reason_comes_from_the_calibration_not_the_record(status):
    """A failure never overwrites an applied fit, so the record can be stale."""
    mocks, sample_file = _start_single()
    sample_file.mz_calibration = {"mode": "one-point", "verified": True}
    mocks["calibrate"].return_value = _outcome(
        False, "The m/z calibration failed: Not enough calibration peaks."
    )

    await _run_pipeline()

    assert _recorded(status)[-1] == (
        "calibration_failed",
        "The m/z calibration failed: Not enough calibration peaks. Matching "
        "and peak assignment were skipped.",
    )


@pytest.mark.asyncio
async def test_a_file_matched_in_part_is_not_done(status):
    """One polarity's fit failed while the other matched on an earlier fit."""

    async def calibrate(sample, sample_file):
        return _outcome(False, "The m/z calibration failed: No calibration peaks.")

    mocks, sample_file = _start_dual_polarity(
        calibrate=calibrate, neg_collection=None, pos_collection="cal-pos"
    )
    sample_file.mz_calibration = {"mode": "one-point", "verified": True}

    await _run_pipeline()

    mocks["match"].assert_called_once()
    assert _recorded(status)[-1] == (
        "calibration_failed",
        "The m/z calibration failed: No calibration peaks. Matching and peak "
        "assignment were skipped for 1 of its 2 samples.",
    )


def _db_error(text: str):
    from sqlalchemy.exc import OperationalError

    return OperationalError(text, {"sample_file_id": "sf-001"}, Exception("gone"))


@pytest.mark.parametrize(
    ("error", "detail"),
    [
        (
            "api",
            "The peak store is corrupt",
        ),
        (ValueError("must include one per polarity"), "must include one per polarity"),
        (
            "timeout",
            "The server was too busy to process the file. Process it again.",
        ),
        (
            "database",
            "A database operation failed while the file was processed.",
        ),
        (
            RuntimeError("SELECT * FROM sample_file WHERE id = 'sf-001'"),
            "Processing stopped on an unexpected error.",
        ),
    ],
)
def test_a_failure_detail_keeps_internals_out(error, detail):
    """The detail is served to every reader of the file list."""
    from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

    from mascope_backend.api.controllers.sample.files.process.service import (
        _failure_detail,
    )
    from mascope_backend.api.lib.exceptions.api_exceptions import ApiException

    error = {
        "api": ApiException("The peak store is corrupt", {}, 400),
        "timeout": SQLAlchemyTimeoutError("QueuePool limit of size 5 overflow 10"),
        "database": _db_error("SELECT * FROM sample_file WHERE id = $1"),
    }.get(error, error)

    assert _failure_detail(error) == detail


@pytest.mark.asyncio
async def test_a_pipeline_that_gives_up_is_marked_failed(status):
    from mascope_backend.api.controllers.sample.files.process import service
    from mascope_backend.api.lib.exceptions.api_exceptions import ApiException

    body = AsyncMock(side_effect=ApiException("The peak store is corrupt", {}, 400))

    with (
        patch(f"{_SVC}._auto_process_sample_file", new=body),
        patch(f"{_SVC}._delete_partial_acquisition_items", new=AsyncMock()),
        patch(f"{_FEATURES}.handle_notifications", new_callable=AsyncMock),
        patch(f"{_FEATURES}.handle_reloads", new_callable=AsyncMock),
    ):
        await service.auto_process_sample_file(
            sample_file_id="sf-corrupt", independent_transaction=True
        )

    assert status.call_args.args[0] == "sf-corrupt"
    assert _recorded(status) == [("failed", "The peak store is corrupt")]


@pytest.mark.asyncio
async def test_a_retry_that_recovers_records_no_failure(status):
    from mascope_backend.api.controllers.sample.files.process import service
    from mascope_backend.api.lib.exceptions.api_exceptions import ApiException

    body = AsyncMock(side_effect=[ApiException("busy", {}, 503), {"message": "ok"}])

    with (
        patch(f"{_SVC}._auto_process_sample_file", new=body),
        patch(f"{_SVC}._delete_partial_acquisition_items", new=AsyncMock()),
        patch.object(service, "_AUTO_PROCESS_RETRY_DELAYS_S", (0, 0, 0)),
        patch(f"{_FEATURES}.handle_notifications", new_callable=AsyncMock),
        patch(f"{_FEATURES}.handle_reloads", new_callable=AsyncMock),
    ):
        await service.auto_process_sample_file(
            sample_file_id="sf-retry", independent_transaction=True
        )

    status.assert_not_called()


@pytest.mark.asyncio
async def test_modes_chosen_for_a_file_bind_it_without_its_tokens(status):
    mocks, _ = _start_single()
    chosen = _make_ionization_mode(ionization_mode_name="Nitrate")

    with patch(
        f"{_SVC}.fetch_ionization_modes",
        new_callable=AsyncMock,
        return_value=[chosen],
    ) as fetch:
        await _run_pipeline(ionization_mode_ids=["im-001"])

    fetch.assert_awaited_once_with(["im-001"])
    mocks["resolve"].assert_not_called()
    assert mocks["create_batches"].call_args.kwargs["ionization_modes"] == [chosen]
    assert _recorded(status)[0] == (
        "bound",
        "Bound to 'Nitrate' (-) without a file-name token.",
    )
    assert _recorded(status)[-1][0] == "done"


@pytest.mark.asyncio
async def test_chosen_modes_that_no_longer_fit_park_the_file(status):
    """A mode chosen for it changed while it waited: it can be given another."""
    mocks, sample_file = _start_single()
    sample_file.polarity = "+-"

    with patch(
        f"{_SVC}.fetch_ionization_modes",
        new_callable=AsyncMock,
        return_value=[_make_ionization_mode()],
    ):
        result = await _run_pipeline(ionization_mode_ids=["im-001"])

    mocks["create_batches"].assert_not_called()
    assert result["status"] == "parked"
    ((state, detail),) = _recorded(status)
    assert state == "needs_chemistry"
    assert "must include one per polarity" in detail
    assert "no mode matches polarity +" in detail


def _mode_of(polarity: str, name: str):
    return _make_ionization_mode(
        ionization_mode_id=f"im-{name}",
        ionization_mode_name=name,
        ionization_mode_polarity=polarity,
    )


def test_each_polarity_of_a_file_takes_the_chosen_mode_of_its_polarity():
    from mascope_backend.api.controllers.sample.files.process.service import (
        choose_ionization_modes,
    )

    negative, positive = _mode_of("-", "neg"), _mode_of("+", "pos")

    both = choose_ionization_modes(
        _make_sample_file(polarity="+-"), [negative, positive]
    )
    only = choose_ionization_modes(
        _make_sample_file(polarity="-"), [negative, positive]
    )

    assert both == [positive, negative]
    assert only == [negative]


@pytest.mark.parametrize(
    ("modes", "problem"),
    [
        ([], "no mode matches polarity -"),
        ([("-", "a"), ("-", "b")], "2 modes match polarity -"),
    ],
)
def test_a_polarity_without_exactly_one_chosen_mode_is_refused(modes, problem):
    from mascope_backend.api.controllers.sample.files.process.service import (
        choose_ionization_modes,
    )

    with pytest.raises(ValueError, match=problem):
        choose_ionization_modes(
            _make_sample_file(polarity="-"),
            [_mode_of(polarity, name) for polarity, name in modes],
        )


async def _give_up():
    """Run the wrapper, as the routes spawn it, to a give-up on a bad file."""
    from mascope_backend.api.controllers.sample.files.process import service
    from mascope_backend.api.lib.exceptions.api_exceptions import ApiException

    body = AsyncMock(side_effect=ApiException("The peak store is corrupt", {}, 400))
    with (
        patch(f"{_SVC}._auto_process_sample_file", new=body),
        patch(f"{_SVC}._delete_partial_acquisition_items", new=AsyncMock()),
        patch.object(service, "_FAILED_STATUS_TIMEOUT_S", 0.05),
        patch(f"{_FEATURES}.handle_notifications", new_callable=AsyncMock),
        patch(f"{_FEATURES}.handle_reloads", new_callable=AsyncMock),
    ):
        await service.auto_process_sample_file(
            sample_file_id="sf-slow", independent_transaction=True
        )


@pytest.mark.asyncio
async def test_a_cancellation_while_recording_the_failure_is_reported(status):
    """Raised inside the except clause, it would bypass the cancel handler."""
    import asyncio

    status.side_effect = asyncio.CancelledError()

    with captured_logs() as records:
        with pytest.raises(asyncio.CancelledError):
            await _give_up()

    assert any(
        "sf-slow was cancelled while recording that it failed" in message
        for _, message in _lines(records)
    ), _lines(records)


@pytest.mark.asyncio
async def test_a_failure_that_cannot_be_recorded_in_time_is_logged(status):
    """The write waits on the pool the run may have given up on."""
    import asyncio

    async def record(*_):
        await asyncio.sleep(1)

    status.side_effect = record

    with captured_logs() as records:
        await _give_up()

    assert any(
        level == "INFO"
        and "Could not record within" in message
        and "sf-slow" in message
        for level, message in _lines(records)
    ), _lines(records)


@pytest.mark.asyncio
@pytest.mark.parametrize(("asked", "resets"), [(True, 1), (False, 0)])
async def test_a_rebuild_resets_the_calibration_once_before_it_starts(asked, resets):
    """As re-processing does, and not again on a retry."""
    from mascope_backend.api.controllers.sample.files.process import service
    from mascope_backend.api.lib.exceptions.api_exceptions import ApiException

    reset = AsyncMock()
    body = AsyncMock(side_effect=[ApiException("busy", {}, 503), {"message": "ok"}])
    with (
        patch(f"{_SVC}._reset_calibration", reset),
        patch(f"{_SVC}._auto_process_sample_file", body),
        patch(f"{_SVC}._delete_partial_acquisition_items", AsyncMock()),
        patch.object(service, "_AUTO_PROCESS_RETRY_DELAYS_S", (0, 0, 0)),
        patch(f"{_FEATURES}.handle_notifications", new_callable=AsyncMock),
        patch(f"{_FEATURES}.handle_reloads", new_callable=AsyncMock),
    ):
        await service.auto_process_sample_file(
            sample_file_id="sf-rebind",
            independent_transaction=True,
            reset_calibration=asked,
        )

    assert body.await_count == 2
    assert reset.await_count == resets
