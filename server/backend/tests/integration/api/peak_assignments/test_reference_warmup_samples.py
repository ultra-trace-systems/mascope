"""Integration: the samples the reference lists' warm-up resolves (MAS-1281).

The warm-up builds a worker's reference entries from the newest sample with
peaks of each ionization mode and polarity in use
(``reference_warmup._representative_samples``). Here one mode holds an older
and a newer sample with peaks in one polarity, a still newer blank in it, and
one sample in the other polarity; a sample bound under no mode is never read.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete

from mascope_backend.api.new.peak_assignments import reference_warmup
from mascope_backend.db import (
    Dataset,
    InstrumentFunction,
    IonizationMode,
    SampleBatch,
    SampleFile,
    SampleItem,
    Workspace,
)
from mascope_backend.db.id import gen_id


pytestmark = pytest.mark.asyncio

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)

#: name: (polarity, hours after _NOW, has peaks, bound under the mode)
_SAMPLES = {
    "older": ("+", 0, True, True),
    "newer": ("+", 1, True, True),
    "blank": ("+", 2, False, True),
    "negative": ("-", 0, True, True),
    "unbound": ("+", 3, True, False),
}


@pytest_asyncio.fixture
async def world(async_session_factory, pa_sample_view):
    ids = {key: gen_id() for key in ("workspace", "dataset", "batch", "mode")}
    ids["function"] = gen_id(32)
    items = {name: (gen_id(), gen_id()) for name in _SAMPLES}
    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=ids["workspace"],
                workspace_name=f"Warm-up {ids['workspace']}",
                workspace_status="active",
                workspace_utc_created=_NOW,
                workspace_utc_modified=_NOW,
            )
        )
        session.add(
            Dataset(
                dataset_id=ids["dataset"],
                workspace_id=ids["workspace"],
                dataset_name="Warm-up",
                dataset_utc_created=_NOW,
            )
        )
        session.add(
            SampleBatch(
                sample_batch_id=ids["batch"],
                dataset_id=ids["dataset"],
                sample_batch_name="Warm-up",
                sample_batch_utc_created=_NOW,
            )
        )
        session.add(
            IonizationMode(
                ionization_mode_id=ids["mode"],
                ionization_mode_name=f"Warm-up {ids['mode']}",
                ionization_mode_token=f"wu{ids['mode']}",
                ionization_mode_polarity="+",
                ionization_mechanism_ids=[],
            )
        )
        session.add(
            InstrumentFunction(
                instrument_function_id=ids["function"],
                instrument="test-orbion",
                method_file=f"warm-up-{ids['function']}",
                datetime_utc=_NOW,
                peakshape={},
                resolution_function=[1.0],
            )
        )
        await session.flush()
        for name, (polarity, hours, peaks, bound) in _SAMPLES.items():
            item_id, file_id = items[name]
            session.add(
                SampleFile(
                    sample_file_id=file_id,
                    instrument_function_id=ids["function"] if peaks else None,
                    filename=f"test-orbion_{file_id}.raw",
                    instrument="test-orbion",
                    datetime=datetime(2026, 1, 1) + timedelta(hours=hours),
                    datetime_utc=_NOW + timedelta(hours=hours),
                    length=60.0,
                    range=[50.0, 500.0],
                    polarity=polarity,
                )
            )
            await session.flush()
            session.add(
                SampleItem(
                    sample_item_id=item_id,
                    sample_batch_id=ids["batch"],
                    sample_file_id=file_id,
                    sample_item_name=f"Warm-up {name}",
                    sample_item_type="SAMPLE",
                    sample_item_attributes={},
                    polarity=polarity,
                    ionization_mode_id=ids["mode"] if bound else None,
                    sample_item_utc_created=_NOW,
                )
            )
        await session.commit()

    yield ids, {name: item_id for name, (item_id, _) in items.items()}

    async with async_session_factory() as session:
        item_ids = [item_id for item_id, _ in items.values()]
        file_ids = [file_id for _, file_id in items.values()]
        await session.execute(
            delete(SampleItem).where(SampleItem.sample_item_id.in_(item_ids))
        )
        await session.execute(
            delete(SampleFile).where(SampleFile.sample_file_id.in_(file_ids))
        )
        await session.execute(
            delete(InstrumentFunction).where(
                InstrumentFunction.instrument_function_id == ids["function"]
            )
        )
        await session.execute(
            delete(IonizationMode).where(
                IonizationMode.ionization_mode_id == ids["mode"]
            )
        )
        await session.execute(
            delete(SampleBatch).where(SampleBatch.sample_batch_id == ids["batch"])
        )
        await session.execute(
            delete(Dataset).where(Dataset.dataset_id == ids["dataset"])
        )
        await session.execute(
            delete(Workspace).where(Workspace.workspace_id == ids["workspace"])
        )
        await session.commit()


async def test_the_newest_sample_with_peaks_of_each_mode_and_polarity(world):
    ids, items = world

    samples = await reference_warmup._representative_samples()

    ours = [s for s in samples if s.sample_batch_id == ids["batch"]]
    assert [s.sample_item_id for s in ours] == [items["newer"], items["negative"]]
    # The newest pair first, across every mode the database holds.
    stamps = [s.datetime_utc for s in samples if s.datetime_utc is not None]
    assert stamps == sorted(stamps, reverse=True)
