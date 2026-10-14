"""
Integration tests: a file of fragmentation scans alone, filed without a chemistry.

A file that binds to no ionization mode has no samples, and its MS2 data is
read through a sample. A file that records no MS1 scans needs no chemistry -
it has no peaks to calibrate, match or assign under one - so it is filed as
an MS2 part instead: an ACQUISITION item with no ionization mode, in the
day's MS2 batch of the instrument's acquisition dataset
(``process/ms2_parts.py``).

The unit tests cover which files are filed and what the file's status says.
Here the rows meet a real database, because three things about them are only
true there: the batch's natural key gathers a day's files into one batch per
polarity, an item with no ionization mode is one the schema takes, and the
part counts as the pipeline's own - so that a run that binds the file later
replaces it rather than leaving two samples of one file behind.

Item creation is also where an item's time window is taken, and every MS2
route selects within it, so the window of a file that holds an MS1 block and
fragment scans after it is pinned here too.

No raw file is read: the item's TIC and window come from stand-ins.

Item creation reads the rows it wrote back through the ``sample_view``
database VIEW, which ``Base.metadata.create_all`` does not create, so the
view is created here as the match integration package creates it for itself.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import numpy as np
import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text

from mascope_backend.api.controllers.sample.batches import sample_batches_controller
from mascope_backend.api.controllers.sample.batches.sample_batches_controller import (
    get_or_create_acquisition_batch,
)
from mascope_backend.api.controllers.sample.files.process import (
    service as process_service,
)
from mascope_backend.api.controllers.sample.files.process import status
from mascope_backend.api.controllers.sample.items import sample_items_controller
from mascope_backend.api.models.sample.batches.sample_batch_pydantic_model import (
    SampleBatchCreate,
)
from mascope_backend.api.models.sample.items.sample_item_pydantic_model import (
    AcquisitionItemCreate,
)
from mascope_backend.db import (
    Dataset,
    IonizationMode,
    Sample,
    SampleBatch,
    SampleFile,
    SampleItem,
    Workspace,
)
from mascope_backend.db.id import gen_id
from mascope_thermo.thermo import NoScansFoundError


INSTRUMENT = "ms2-orbi"
WORKSPACE = f"Acquisitions {INSTRUMENT}"
_NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)

#: Scan times [s] the stand-ins describe. A file with an MS1 block has it
#: over the first half minute, and fragment scans for three minutes after it;
#: a file of fragmentation scans alone has those fragment scans only.
_MS1_FIRST, _MS1_LAST = 0.54, 32.49
_FIRST_FRAGMENT_SCAN, _LAST_FRAGMENT_SCAN = 33.10, 206.16

#: Files of fragmentation scans alone are named with this, which is how the
#: stand-ins tell them apart.
_MS2_ONLY = "ms2-only"


@pytest_asyncio.fixture(scope="session", autouse=True)
async def sample_view(async_engine):
    """Create the sample_view VIEW in the integration test database."""
    async with async_engine.begin() as conn:
        await conn.execute(text(Sample.drop_view()))
        await conn.execute(text(Sample.create_view()))


@pytest_asyncio.fixture
async def setup(async_session_factory):
    """The instrument's workspace with its acquisition dataset, and a mode."""
    ids = {"workspace": gen_id(16), "dataset": gen_id(16), "mode": gen_id(16)}
    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=ids["workspace"],
                workspace_name=WORKSPACE,
                workspace_status="active",
                is_system=True,
            )
        )
        await session.flush()
        session.add(
            Dataset(
                dataset_id=ids["dataset"],
                workspace_id=ids["workspace"],
                dataset_name="2026",
                dataset_type="ACQUISITION",
                instrument=INSTRUMENT,
                dataset_utc_created=_NOW,
            )
        )
        session.add(
            IonizationMode(
                ionization_mode_id=ids["mode"],
                ionization_mode_name=f"MS2 parts positive {ids['mode']}",
                ionization_mode_token=None,
                ionization_mode_polarity="+",
                ionization_mechanism_ids=[],
            )
        )
        await session.commit()
    yield ids
    async with async_session_factory() as session:
        await session.execute(
            delete(SampleFile).where(SampleFile.instrument == INSTRUMENT)
        )
        await session.execute(
            delete(Workspace).where(Workspace.workspace_id == ids["workspace"])
        )
        await session.execute(
            delete(IonizationMode).where(
                IonizationMode.ionization_mode_id == ids["mode"]
            )
        )
        await session.commit()


def _tic_per_scan(base_filename, polarity=None, scan_type="Ms"):
    """The reader's TIC, for the two shapes of acquisition above.

    As the reader does, a selection that finds no scans raises rather than
    answering empty: a file of fragmentation scans alone has no MS1 TIC.
    """
    if _MS2_ONLY in base_filename:
        if scan_type == "Ms":
            raise NoScansFoundError("No MS1 scans found in the file")
        return np.array([_FIRST_FRAGMENT_SCAN, _LAST_FRAGMENT_SCAN]), np.array(
            [4.0, 5.0]
        )
    return np.array([_MS1_FIRST, _MS1_LAST]), np.array([10.0, 20.0])


def _acquisition_window(base_filename, polarity=None):
    """The reader's window across every scan type, for the two shapes above."""
    if _MS2_ONLY in base_filename:
        return _FIRST_FRAGMENT_SCAN, _LAST_FRAGMENT_SCAN
    return _MS1_FIRST, _LAST_FRAGMENT_SCAN


@pytest.fixture(autouse=True)
def rig(monkeypatch) -> None:
    """Nothing is sent to browsers, and no raw file is opened for an item's
    TIC and window."""
    monkeypatch.setattr(status, "emit_record_updated", AsyncMock())
    monkeypatch.setattr(sample_items_controller, "emit_record_created", AsyncMock())
    monkeypatch.setattr(sample_batches_controller, "emit_record_created", AsyncMock())
    monkeypatch.setattr(
        sample_items_controller.m_compute, "get_tic_per_scan", _tic_per_scan
    )
    # The MS1 read goes through the polarity as its sample reads it, which
    # opens the file's peak store first; these files have none.
    monkeypatch.setattr(
        sample_items_controller.m_compute,
        "get_sample_tic_per_scan",
        lambda base_filename, polarity: _tic_per_scan(base_filename, polarity),
    )
    monkeypatch.setattr(
        sample_items_controller.m_compute,
        "get_acquisition_window",
        _acquisition_window,
    )


async def _file(
    async_session_factory, name: str = _MS2_ONLY, polarity: str = "+"
) -> SampleFile:
    """A converted file with no token in its name, acquired on one day."""
    sample_file_id = gen_id()
    async with async_session_factory() as session:
        session.add(
            SampleFile(
                sample_file_id=sample_file_id,
                filename=f"{INSTRUMENT}_{name}_{sample_file_id}.raw",
                instrument=INSTRUMENT,
                instrument_type="orbi",
                datetime=datetime(2026, 9, 7, 12, 27, 59),
                datetime_utc=_NOW,
                length=_LAST_FRAGMENT_SCAN,
                range=[40.0, 600.0],
                polarity=polarity,
                processing_status="converted",
            )
        )
        await session.commit()
        return await session.get(SampleFile, sample_file_id)


async def _file_ms2(
    sample_file: SampleFile, setup: dict, polarities: tuple[str, ...] = ("+",)
) -> dict:
    return await process_service._file_without_chemistry(
        sample_file, setup["dataset"], list(polarities), None
    )


async def _items(async_session_factory, sample_file_id: str) -> list[SampleItem]:
    async with async_session_factory() as session:
        return list(
            await session.scalars(
                select(SampleItem).where(SampleItem.sample_file_id == sample_file_id)
            )
        )


async def _ms2_batches(async_session_factory, setup: dict) -> list[SampleBatch]:
    async with async_session_factory() as session:
        return list(
            await session.scalars(
                select(SampleBatch)
                .where(SampleBatch.dataset_id == setup["dataset"])
                .order_by(SampleBatch.polarity)
            )
        )


async def _row(async_session_factory, sample_file_id: str) -> SampleFile:
    async with async_session_factory() as session:
        return await session.get(SampleFile, sample_file_id)


@pytest.mark.asyncio
async def test_the_file_gets_an_ms2_part_in_the_days_ms2_batch(
    async_session_factory, setup
):
    sample_file = await _file(async_session_factory)

    result = await _file_ms2(sample_file, setup)

    (batch,) = await _ms2_batches(async_session_factory, setup)
    assert (batch.sample_batch_name, batch.polarity, batch.sample_batch_type) == (
        "2026-09-07 MS2 acquisition",
        "+",
        "ACQUISITION",
    )
    (item,) = await _items(async_session_factory, sample_file.sample_file_id)
    assert item.sample_batch_id == batch.sample_batch_id
    assert (item.sample_item_type, item.polarity) == ("ACQUISITION", "+")
    assert item.sample_item_name == "2026-09-07 12:27:59"
    # Nothing bound it: no mode, and no rung that could have.
    assert (item.ionization_mode_id, item.bound_by, item.method_binding_id) == (
        None,
        None,
        None,
    )
    # Locked, as every sample of an ACQUISITION batch is.
    assert item.locked == 1
    assert result["_notification_data"]["affected_sample_item_ids"] == [
        item.sample_item_id
    ]


@pytest.mark.asyncio
async def test_the_parts_tic_and_window_are_its_fragment_scans(
    async_session_factory, setup
):
    """The file has no MS1 TIC, which the reader refuses rather than
    answering empty, so the item's TIC is that of the scans it has."""
    sample_file = await _file(async_session_factory)

    await _file_ms2(sample_file, setup)

    (item,) = await _items(async_session_factory, sample_file.sample_file_id)
    assert item.tic == 9.0
    assert (item.t0, item.t1) == (_FIRST_FRAGMENT_SCAN, _LAST_FRAGMENT_SCAN)


@pytest.mark.asyncio
async def test_the_file_is_done(async_session_factory, setup):
    sample_file = await _file(async_session_factory)

    result = await _file_ms2(sample_file, setup)

    row = await _row(async_session_factory, sample_file.sample_file_id)
    assert row.processing_status == "done"
    assert row.processing_detail == (
        "Filed as an MS2 sample in '2026-09-07 MS2 acquisition' (+). It records "
        "no MS1 scans, so no peaks were detected and there is nothing to "
        "calibrate, match or assign."
    )
    assert "status" not in result


@pytest.mark.asyncio
async def test_a_days_files_share_one_batch(async_session_factory, setup):
    """The natural key - dataset, name, polarity - is the daily batch's."""
    first = await _file(async_session_factory)
    second = await _file(async_session_factory)

    await _file_ms2(first, setup)
    await _file_ms2(second, setup)

    (batch,) = await _ms2_batches(async_session_factory, setup)
    for sample_file in (first, second):
        (item,) = await _items(async_session_factory, sample_file.sample_file_id)
        assert item.sample_batch_id == batch.sample_batch_id


@pytest.mark.asyncio
async def test_each_polarity_that_fragments_has_its_own_batch(
    async_session_factory, setup
):
    """One name for both, which the polarity in the natural key allows."""
    sample_file = await _file(async_session_factory, polarity="+-")

    await _file_ms2(sample_file, setup, polarities=("+", "-"))

    batches = await _ms2_batches(async_session_factory, setup)
    assert [(b.sample_batch_name, b.polarity) for b in batches] == [
        ("2026-09-07 MS2 acquisition", "+"),
        ("2026-09-07 MS2 acquisition", "-"),
    ]
    items = await _items(async_session_factory, sample_file.sample_file_id)
    assert sorted(item.polarity for item in items) == ["+", "-"]


@pytest.mark.asyncio
async def test_the_part_is_the_pipelines_own_so_a_rebuild_replaces_it(
    async_session_factory, setup
):
    """Every run clears the pipeline's items for the file before it starts.

    That is what lets a run that binds the file - once a token names it -
    replace the MS2 part with the bound sample instead of adding one beside
    it, and it only works if the part counts as the pipeline's own, which is
    decided by where it lives.
    """
    sample_file = await _file(async_session_factory)
    await _file_ms2(sample_file, setup)
    assert len(await _items(async_session_factory, sample_file.sample_file_id)) == 1

    await process_service._delete_partial_acquisition_items(sample_file.sample_file_id)

    assert await _items(async_session_factory, sample_file.sample_file_id) == []


@pytest.mark.asyncio
async def test_filing_again_does_not_add_a_second_part(async_session_factory, setup):
    """As a re-processed file is: cleared, then filed again."""
    sample_file = await _file(async_session_factory)
    await _file_ms2(sample_file, setup)

    await process_service._delete_partial_acquisition_items(sample_file.sample_file_id)
    await _file_ms2(sample_file, setup)

    assert len(await _items(async_session_factory, sample_file.sample_file_id)) == 1
    assert len(await _ms2_batches(async_session_factory, setup)) == 1


@pytest.mark.asyncio
async def test_the_part_gives_re_processing_no_mode_to_keep(
    async_session_factory, setup
):
    """A file is re-processed under the modes its samples hold, and it has none.

    So it goes to the token rule again, and to the rungs below it - rather
    than being rebuilt under "no mode", which is not a chemistry anyone chose.
    """
    sample_file = await _file(async_session_factory)
    await _file_ms2(sample_file, setup)

    held = await process_service._acquisition_item_provenance(
        sample_file.sample_file_id
    )

    assert held == {}
    assert await process_service._kept_modes(sample_file) is None


@pytest.mark.asyncio
async def test_a_bound_items_window_reaches_the_fragment_scans_after_its_ms1_block(
    async_session_factory, setup
):
    """Every MS2 route selects within the item's window, so this decides
    whether a bound file's MS2 data can be read at all.

    A window taken from the TIC would end with the last MS1 scan - half a
    minute in - and hold none of the fragment scans recorded after it, which
    is how a stepped-energy acquisition once came back as MS1 only. The
    window is the acquisition's, across every scan type; the TIC is still
    the MS1 scans'.
    """
    sample_file = await _file(async_session_factory, "stepped")
    batch = (
        await get_or_create_acquisition_batch(
            sample_batch=SampleBatchCreate(
                dataset_id=setup["dataset"],
                sample_batch_name="2026-09-07 MS2 parts positive acquisition",
                sample_batch_type="ACQUISITION",
                polarity="+",
                target_collection_ids=[],
            )
        )
    ).get("data")

    await sample_items_controller.create_sample_items(
        sample_items=[
            AcquisitionItemCreate(
                sample_batch_id=batch["sample_batch_id"],
                sample_file_id=sample_file.sample_file_id,
                sample_item_name="2026-09-07 12:27:59",
                sample_item_type="ACQUISITION",
                sample_item_attributes={},
                polarity="+",
                ionization_mode_id=setup["mode"],
                bound_by="token",
            )
        ],
        independent_transaction=True,
    )

    (item,) = await _items(async_session_factory, sample_file.sample_file_id)
    assert (item.t0, item.t1) == (_MS1_FIRST, _LAST_FRAGMENT_SCAN)
    assert item.t1 > _MS1_LAST
    assert item.tic == 30.0
