"""
Integration tests: ``GET /api/sample/batches`` gives each batch's span.

Each listed batch carries ``first_sample_datetime_utc`` and
``last_sample_datetime_utc``, the earliest and latest start of its samples'
files, and ``sample_instruments``, those files' instruments.
``mascope_viz``'s ``resolve`` passes over a batch whose span misses its time
range without listing that batch's samples, so the span must be the samples
listing's own ``datetime_utc``: the file's.

Run against the real route and a real database, because the span is SQL.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete

from mascope_backend.db import (
    Dataset,
    SampleBatch,
    SampleFile,
    SampleItem,
    Workspace,
    WorkspaceMember,
)
from mascope_backend.db.id import gen_id


_NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)

#: batch: its samples' (file start, instrument). Two samples of the first
#: batch read one file; "empty" has no sample.
_BATCHES = {
    "spread": [
        (_NOW - timedelta(days=2), "instrument B"),
        (_NOW, "instrument A"),
        (_NOW, "instrument A"),
        (_NOW + timedelta(days=1), "instrument A"),
    ],
    "single": [(_NOW + timedelta(days=5), "instrument A")],
    "empty": [],
}


@pytest_asyncio.fixture
async def seeded(async_session_factory, test_users):
    """A dataset the editor may read, with the batches of ``_BATCHES``."""
    ids = {name: gen_id(16) for name in ("workspace", "dataset", *_BATCHES)}
    files: list[str] = []
    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=ids["workspace"],
                workspace_name=f"Batch spans {ids['workspace']}",
                workspace_status="active",
            )
        )
        await session.flush()
        session.add(
            WorkspaceMember(
                workspace_member_id=gen_id(16),
                workspace_id=ids["workspace"],
                user_id=test_users["editor"].id,
                workspace_role="editor",
            )
        )
        session.add(
            Dataset(
                dataset_id=ids["dataset"],
                workspace_id=ids["workspace"],
                dataset_name="Batch spans",
                dataset_type="ANALYSIS",
                dataset_utc_created=_NOW,
            )
        )
        for batch in _BATCHES:
            session.add(
                SampleBatch(
                    sample_batch_id=ids[batch],
                    dataset_id=ids["dataset"],
                    sample_batch_name=f"Batch spans {batch}",
                    sample_batch_utc_created=_NOW,
                )
            )
        await session.flush()
        for batch, samples in _BATCHES.items():
            file_of: dict[tuple, str] = {}
            for acquired, instrument in samples:
                if (acquired, instrument) not in file_of:
                    file_id = gen_id(16)
                    file_of[(acquired, instrument)] = file_id
                    files.append(file_id)
                    session.add(
                        SampleFile(
                            sample_file_id=file_id,
                            filename=f"batch-spans_{file_id}",
                            instrument=instrument,
                            instrument_type="orbi",
                            datetime=acquired.replace(tzinfo=None),
                            datetime_utc=acquired,
                            length=60.0,
                            range=[100.0, 500.0],
                            polarity="-",
                        )
                    )
                    await session.flush()
                session.add(
                    SampleItem(
                        sample_item_id=gen_id(16),
                        sample_batch_id=ids[batch],
                        sample_file_id=file_of[(acquired, instrument)],
                        sample_item_name=f"Batch spans {batch}",
                        sample_item_type="SAMPLE",
                        sample_item_attributes={},
                        polarity="-",
                        tic=1000.0,
                        t0=0.0,
                        t1=60.0,
                        sample_item_utc_created=_NOW,
                    )
                )
        await session.commit()
    yield ids
    async with async_session_factory() as session:
        # The workspace takes its dataset, batches and items with it.
        await session.execute(
            delete(Workspace).where(Workspace.workspace_id == ids["workspace"])
        )
        await session.execute(
            delete(SampleFile).where(SampleFile.sample_file_id.in_(files))
        )
        await session.commit()


async def _listed(client, ids: dict, **params) -> dict[str, dict]:
    """The listed batches by the name the tests know them under."""
    response = await client.get(
        "/api/sample/batches", params={"dataset_id": ids["dataset"], **params}
    )
    assert response.status_code == 200, response.text
    by_id = {ids[name]: name for name in _BATCHES}
    return {by_id[row["sample_batch_id"]]: row for row in response.json()["data"]}


def _instant(text: str | None) -> datetime | None:
    return None if text is None else datetime.fromisoformat(text)


@pytest.mark.asyncio
async def test_a_batch_spans_its_samples_files(editor_client, seeded):
    spread = (await _listed(editor_client, seeded))["spread"]

    assert _instant(spread["first_sample_datetime_utc"]) == _NOW - timedelta(days=2)
    assert _instant(spread["last_sample_datetime_utc"]) == _NOW + timedelta(days=1)
    assert spread["sample_instruments"] == ["instrument A", "instrument B"]


@pytest.mark.asyncio
async def test_each_batch_has_its_own_span(editor_client, seeded):
    single = (await _listed(editor_client, seeded))["single"]

    assert _instant(single["first_sample_datetime_utc"]) == _NOW + timedelta(days=5)
    assert _instant(single["last_sample_datetime_utc"]) == _NOW + timedelta(days=5)
    assert single["sample_instruments"] == ["instrument A"]


@pytest.mark.asyncio
async def test_a_batch_with_no_sample_has_no_span(editor_client, seeded):
    empty = (await _listed(editor_client, seeded))["empty"]

    assert empty["first_sample_datetime_utc"] is None
    assert empty["last_sample_datetime_utc"] is None
    assert empty["sample_instruments"] == []


@pytest.mark.asyncio
async def test_a_page_of_the_listing_has_its_spans(editor_client, seeded):
    """A page names other batches than the whole listing: each still gets
    its own span."""
    page = await _listed(
        editor_client, seeded, sort="sample_batch_name", page=1, limit=1
    )

    # By name: "empty", "single", "spread"
    assert list(page) == ["single"]
    assert _instant(page["single"]["first_sample_datetime_utc"]) == _NOW + timedelta(
        days=5
    )
