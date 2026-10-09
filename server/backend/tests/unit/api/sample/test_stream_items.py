"""
The sample item of a polarity points at the stream it reads.

Three seams, because the stream crosses all of them and each could drop it
without anything failing:

- the pipeline writes the file's stream rows and points each polarity's item
  at its composite, or at the polarity's one stream, or at nothing;
- the bulk create writes the stream for every row it inserts, refuses a
  stream of another file, and gives an item made through a route the row
  the pipeline's item of its file and polarity reads;
- a copy carries the stream of the item it was copied from.

And two things that must not change: an item is named and cut as it always
was - its window is its polarity's, and its TIC is its polarity's as its
sample reads it - and a request cannot name a stream
(``docs/dev/ingest_routing_and_splitting.md``, sections 4.4 and 4.5).
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from mascope_backend.api.controllers.sample.files.process.service import (
    ItemProvenance,
)
from mascope_backend.api.controllers.sample.files.process.streams import (
    StoreStreams,
    StreamRows,
)
from mascope_backend.api.controllers.sample.items import sample_items_controller
from mascope_backend.api.lib.exceptions.api_exceptions import NotFoundException
from mascope_backend.api.models.sample.items.sample_item_pydantic_model import (
    AcquisitionItemCreate,
    SampleItemCreate,
    StreamItemCreate,
)
from mascope_signal.compute import StalePeakStoreError


_SVC = "mascope_backend.api.controllers.sample.files.process.service"
_ITEMS = "mascope_backend.api.controllers.sample.items.sample_items_controller"

NEG = "FTMS - p NSI Full ms [40.0000-138.0000] R=120000"
LOW = "FTMS - p NSI Full ms [66.0000-124.0000] R=120000"
POS = "FTMS + p NSI Full ms [40.0000-600.0000] R=120000"
FILENAME = "ORBI-1_2026.10.01-09h30m00s_run"


def _stream(key, polarity):
    return {
        "key": key,
        "signature_key": key,
        "signature": {"ms_order": 1, "polarity": polarity},
        "scan_segment": 1,
        "scan_event": 1,
        "scans": 4,
        "blocks": 1,
        "t_first": 0.0,
        "t_last": 3.0,
    }


def _sample_file():
    sample_file = MagicMock()
    sample_file.sample_file_id = "sf-001"
    sample_file.filename = FILENAME
    sample_file.instrument = "instrument-A"
    sample_file.datetime = datetime(2026, 10, 1, 9, 30, 0)
    return sample_file


def _mode(mode_id, polarity):
    mode = MagicMock()
    mode.ionization_mode_id = mode_id
    mode.ionization_mode_name = "Nitrate" if polarity == "-" else "Ammonium"
    mode.ionization_mode_polarity = polarity
    mode.diagnostic_collection_id = None
    mode.calibration_collection_id = None
    return mode


async def _items_created(modes, found, rows=None, provenance=None):
    """The item models the pipeline would create, and how it got there."""
    from mascope_backend.api.controllers.sample.files.process.service import (
        create_acquisition_batches_and_items,
    )

    with (
        patch(
            f"{_SVC}.get_or_create_acquisition_batch",
            new_callable=AsyncMock,
            side_effect=lambda sample_batch: {
                "data": {"sample_batch_id": f"sb{sample_batch.polarity}"},
                "created": True,
            },
        ) as batch,
        patch(
            f"{_SVC}._with_live_bindings",
            new_callable=AsyncMock,
            side_effect=lambda held: held,
        ),
        patch(
            f"{_SVC}.read_store_streams", new_callable=AsyncMock, return_value=found
        ) as read,
        patch(
            f"{_SVC}.sync_stream_rows",
            new_callable=AsyncMock,
            return_value=rows or StreamRows(),
        ) as sync,
        patch(
            f"{_SVC}.create_sample_items", new_callable=AsyncMock
        ) as create_sample_items,
    ):
        create_sample_items.return_value = {"data": []}
        await create_acquisition_batches_and_items(
            sample_file=_sample_file(),
            dataset_id="ds-001",
            ionization_modes=modes,
            provenance=provenance or {},
        )
    return SimpleNamespace(
        items=create_sample_items.await_args.kwargs["sample_items"],
        read=read,
        sync=sync,
        batch=batch,
    )


# -- the pipeline --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_file_with_no_census_gets_an_item_per_polarity_pointing_at_nothing():
    made = await _items_created(
        [_mode("im-neg", "-"), _mode("im-pos", "+")], StoreStreams()
    )

    assert [(item.polarity, item.stream_id) for item in made.items] == [
        ("-", None),
        ("+", None),
    ]
    made.read.assert_awaited_once_with(FILENAME)
    # No row is written for a file that describes no streams
    made.sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_each_polaritys_item_points_at_the_row_it_reads():
    """A stitched polarity's item reads its composite; a polarity with one
    stream reads that stream. The rows are written once, for the file."""
    found = StoreStreams(
        [_stream(NEG, "-"), _stream(LOW, "-"), _stream(POS, "+")],
        [NEG, LOW, POS],
        {
            "rule": 1,
            "runs": {"-": [[40, 138, 0]]},
            "sources": {"-": "default"},
            "notes": [],
        },
    )
    rows = StreamRows(
        ids={NEG: "st-0", LOW: "st-1", POS: "st-2", "composite -": "st-c"},
        composites={"-": "st-c"},
        single={"+": "st-2"},
    )

    made = await _items_created(
        [_mode("im-neg", "-"), _mode("im-pos", "+")], found, rows
    )

    assert [(item.polarity, item.stream_id) for item in made.items] == [
        ("-", "st-c"),
        ("+", "st-2"),
    ]
    made.sync.assert_awaited_once_with("sf-001", found)


@pytest.mark.asyncio
async def test_a_polarity_pooled_from_several_streams_points_at_nothing():
    """Two streams, one peak list: the item spans every MS1 scan of its
    polarity, which is what nothing says."""
    found = StoreStreams([_stream(NEG, "-"), _stream(LOW, "-")])
    rows = StreamRows(ids={NEG: "st-0", LOW: "st-1"})

    made = await _items_created([_mode("im-neg", "-")], found, rows)

    assert [(item.polarity, item.stream_id) for item in made.items] == [("-", None)]
    made.sync.assert_awaited_once()


@pytest.mark.asyncio
async def test_an_item_is_named_cut_and_batched_as_before():
    """One item per polarity, named for the file's start, in the polarity's
    daily batch, with the polarity's provenance: the stream changes none of
    it."""
    found = StoreStreams([_stream(NEG, "-")])
    rows = StreamRows(ids={NEG: "st-0"}, single={"-": "st-0"})
    provenance = {"im-neg": ItemProvenance(bound_by="method", method_binding_id="mb-1")}

    made = await _items_created([_mode("im-neg", "-")], found, rows, provenance)

    (item,) = made.items
    assert (item.sample_item_name, item.sample_batch_id, item.stream_id) == (
        "2026-10-01 09:30:00",
        "sb-",
        "st-0",
    )
    assert (item.bound_by, item.method_binding_id) == ("method", "mb-1")


# -- the bulk create -----------------------------------------------------------------


COMMON = {
    "sample_batch_id": "sb-001",
    "sample_file_id": "sf-001",
    "sample_item_attributes": {},
    "polarity": "-",
    "ionization_mode_id": "im-001",
}


def _stream_row(stream_id="st-0", file_id="sf-001", key="composite -"):
    return SimpleNamespace(stream_id=stream_id, sample_file_id=file_id, stream_key=key)


class _Session:
    """A session that answers the file query, then the stream query, and
    keeps every statement it was handed."""

    def __init__(self, stream_rows):
        sample_file = MagicMock()
        sample_file.sample_file_id = "sf-001"
        sample_file.filename = FILENAME
        self._answers = [[sample_file], list(stream_rows)]
        self.statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, statement):
        self.statements.append(statement)
        result = MagicMock()
        rows = self._answers.pop(0) if self._answers else []
        result.scalars.return_value.all.return_value = rows
        result.scalars.return_value.__iter__ = lambda _self: iter(rows)
        return result

    async def commit(self):
        pass


# The controller as written, without the decorator that turns whatever it
# raises into the API's own exception: what it refuses, and how, is the point.
_create = sample_items_controller.create_sample_items.__wrapped__


async def _created(items, stream_rows=(), item_streams=None, sample_tic=None):
    """Run the bulk create; return the session and how the file was read.

    ``item_streams`` is the row an item made by hand reads, by polarity, as
    ``read_item_streams`` finds it - or the error that read raises;
    ``sample_tic`` what the read of the sample's TIC raises instead of
    answering.
    """
    session = _Session(stream_rows)
    rows_read = AsyncMock(return_value=item_streams or {})
    if isinstance(item_streams, Exception):
        rows_read.side_effect = item_streams
    ids = [f"si-{index:04d}" for index in range(len(items))]
    with (
        patch(f"{_ITEMS}.async_session", return_value=session),
        patch(f"{_ITEMS}.gen_id", side_effect=ids),
        patch(
            f"{_ITEMS}.fetch_affected_sample_data", new_callable=AsyncMock
        ) as affected,
        patch(
            f"{_ITEMS}.update_sample_batches_modified_timestamp",
            new_callable=AsyncMock,
        ),
        patch(f"{_ITEMS}.read_item_streams", rows_read),
        patch.object(
            sample_items_controller.m_compute,
            "get_sample_tic_per_scan",
            return_value=(None, [1.0, 2.0]),
            side_effect=sample_tic,
        ) as tic,
        patch.object(
            sample_items_controller.m_compute,
            "get_tic_per_scan",
            return_value=(None, [4.0, 5.0]),
        ) as pooled_tic,
        patch.object(
            sample_items_controller.m_compute,
            "get_acquisition_window",
            return_value=(2.0, 5.0),
        ) as window,
    ):
        affected.return_value = MagicMock(
            affected_sample_batch_ids=["sb-001"],
            affected_samples=[MagicMock(sample_item_id=item_id) for item_id in ids],
        )
        await _create(sample_items=items)
    return SimpleNamespace(
        session=session,
        tic=tic,
        pooled_tic=pooled_tic,
        window=window,
        rows_read=rows_read,
    )


def _inserted(session):
    return session.statements[-1].compile(dialect=postgresql.dialect()).params


@pytest.mark.asyncio
async def test_an_items_tic_is_its_polaritys_as_its_sample_reads_it():
    """Over the scans of the polarity's composite where the store stitches
    it (``get_sample_tic_per_scan``), which the store decides and not the
    row the item names. Its window is its polarity's, as before."""
    item = AcquisitionItemCreate(
        sample_item_name="the composite",
        sample_item_type="ACQUISITION",
        stream_id="st-0",
        **COMMON,
    )

    made = await _created([item], [_stream_row()])

    made.tic.assert_called_once_with(FILENAME, "-")
    made.pooled_tic.assert_not_called()
    made.window.assert_called_once_with(base_filename=FILENAME, polarity="-")
    row = _inserted(made.session)
    assert (row["tic_m0"], row["t0_m0"], row["t1_m0"]) == (3.0, 2.0, 5.0)
    assert row["stream_id_m0"] == "st-0"


@pytest.mark.asyncio
async def test_a_stale_store_gives_an_item_its_polaritys_pooled_tic():
    """A stale per-stream store is taken as a pooled one for the file's
    rows, and does not stop the run that makes its items: the TIC is read
    the same way, and the file's next processing makes the item anew."""
    item = AcquisitionItemCreate(
        sample_item_name="x", sample_item_type="ACQUISITION", **COMMON
    )

    made = await _created([item], sample_tic=StalePeakStoreError("stale"))

    made.pooled_tic.assert_called_once_with(FILENAME, polarity="-")
    assert _inserted(made.session)["tic_m0"] == 9.0


@pytest.mark.asyncio
async def test_an_item_made_by_hand_reads_the_row_of_its_file_and_polarity():
    """So that a person's sample and the pipeline's of one file and
    polarity read one spectrum."""
    plain = SampleItemCreate(sample_item_name="x", sample_item_type="UNKNOWN", **COMMON)

    made = await _created([plain], item_streams={"-": "st-0", "+": "st-9"})

    made.rows_read.assert_awaited_once_with("sf-001", FILENAME)
    assert _inserted(made.session)["stream_id_m0"] == "st-0"


@pytest.mark.asyncio
async def test_what_the_pipeline_names_stands_none_included():
    """The pipeline has just written the file's rows and says which one its
    item reads. None is an answer - a polarity pooled from several streams -
    and is not looked up again."""
    item = AcquisitionItemCreate(
        sample_item_name="x", sample_item_type="ACQUISITION", stream_id=None, **COMMON
    )

    made = await _created([item], item_streams={"-": "st-0"})

    made.rows_read.assert_not_awaited()
    assert _inserted(made.session)["stream_id_m0"] is None


@pytest.mark.asyncio
async def test_a_file_whose_streams_cannot_be_read_gives_its_item_no_row():
    """The row is not worth the sample: it is made as every sample was
    before the rows existed."""
    plain = SampleItemCreate(sample_item_name="x", sample_item_type="UNKNOWN", **COMMON)

    made = await _created([plain], item_streams=OSError("the raw file is gone"))

    assert _inserted(made.session)["stream_id_m0"] is None


@pytest.mark.asyncio
async def test_the_insert_names_the_stream_for_every_row():
    """One multi-row INSERT takes its columns from the first row: a plain
    item among stream items is padded with no stream rather than dropping
    the column, whichever comes first."""
    plain = SampleItemCreate(sample_item_name="x", sample_item_type="UNKNOWN", **COMMON)
    pointed = AcquisitionItemCreate(
        sample_item_name="y", sample_item_type="ACQUISITION", stream_id="st-0", **COMMON
    )

    made = await _created([plain, pointed], [_stream_row()])

    row = _inserted(made.session)
    assert (row["stream_id_m0"], row["stream_id_m1"]) == (None, "st-0")
    # The plain item's file had no row for it to read
    made.rows_read.assert_awaited_once_with("sf-001", FILENAME)


@pytest.mark.asyncio
async def test_a_stream_of_another_file_is_refused():
    item = AcquisitionItemCreate(
        sample_item_name="y", sample_item_type="ACQUISITION", stream_id="st-0", **COMMON
    )

    with pytest.raises(ValueError, match="is not a stream of the file"):
        await _created([item], [_stream_row(file_id="sf-other")])


@pytest.mark.asyncio
async def test_a_stream_that_does_not_exist_is_refused():
    item = AcquisitionItemCreate(
        sample_item_name="y",
        sample_item_type="ACQUISITION",
        stream_id="st-gone",
        **COMMON,
    )

    with pytest.raises(NotFoundException, match="Scan streams not found"):
        await _created([item], [])


def test_a_request_cannot_name_a_stream():
    """The routes take SampleItemCreate, which has no such field: whatever a
    request sends under that name is not part of what gets written."""
    item = SampleItemCreate(
        sample_item_name="x", sample_item_type="UNKNOWN", stream_id="st-0", **COMMON
    )

    assert "stream_id" not in item.model_dump()
    assert (
        "stream_id"
        in StreamItemCreate(
            sample_item_name="x", sample_item_type="UNKNOWN", stream_id="st-0", **COMMON
        ).model_dump()
    )


# -- a copy --------------------------------------------------------------------------


class _CopySession:
    """A session that answers the source query with samples, as the view
    gives them, and the stream query with ``(item, stream)`` rows."""

    def __init__(self, sources, streams):
        self._sources, self._streams = sources, streams

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, _statement):
        result = MagicMock()
        result.scalars.return_value.all.return_value = list(self._sources)
        result.all.return_value = list(self._streams)
        return result


def _source(item_id, **overrides):
    """A sample as the view gives it, which carries no stream."""
    fields = {
        "sample_item_id": item_id,
        "sample_batch_id": "sb-source",
        "sample_file_id": "sf-001",
        "sample_item_name": "2026-10-01 09:31:31",
        "sample_item_type": "ACQUISITION",
        "sample_item_attributes": {},
        "filter_id": None,
        "tic": 3.0,
        "polarity": "-",
        "ionization_mode_id": "im-001",
        "t0": 91.4,
        "t1": 120.0,
        "filename": FILENAME,
    }
    return SimpleNamespace(**(fields | overrides))


@pytest.mark.asyncio
async def test_a_copy_reads_the_stream_its_source_read():
    """Its TIC and window are copied from the source, and so is what it
    reads. The view the sources are read from does not carry the stream."""
    sources = [_source("si-stream"), _source("si-pooled")]
    created = {
        "data": [
            {"sample_item_id": "copy-1", "filename": FILENAME, "polarity": "-"},
            {"sample_item_id": "copy-2", "filename": FILENAME, "polarity": "-"},
        ]
    }

    with (
        patch(
            f"{_ITEMS}.async_session",
            lambda: _CopySession(sources, [("si-stream", "st-0"), ("si-pooled", None)]),
        ),
        patch(
            f"{_ITEMS}.fetch_sample_batch",
            AsyncMock(return_value=SimpleNamespace(sample_batch_name="Target")),
        ),
        patch(
            f"{_ITEMS}.create_sample_items", AsyncMock(return_value=created)
        ) as create,
        # Copied into another batch, whose matches then want refreshing
        patch(f"{_ITEMS}.update_sample_batch_status", AsyncMock()),
    ):
        await sample_items_controller.copy_sample_items.__wrapped__(
            sample_item_ids=["si-stream", "si-pooled"],
            sample_batch_id="sb-target",
        )

    copies = create.await_args.kwargs["sample_items"]
    assert [copy.stream_id for copy in copies] == ["st-0", None]
    assert [(copy.tic, copy.t0, copy.t1) for copy in copies] == [(3.0, 91.4, 120.0)] * 2
    # Still not an item the pipeline routed: a copy records no rung
    assert all(not hasattr(copy, "bound_by") for copy in copies)
