"""A file's scan streams as rows, and the one a sample item reads.

Every file whose census names its streams gets a row per stream; a polarity
whose streams the peak store stitched gets a composite row too, its segments
pointing at it and the map on it. ``read_store_streams`` reads the census -
the file's own for a raw Orbitrap file - and the store, ``sync_stream_rows``
keeps the rows, and ``StreamRows.item_stream`` says which row an item of a
polarity reads: the composite, else the polarity's one stream, else none
(``docs/dev/ingest_routing_and_splitting.md``, sections 4.4 and 4.5).
"""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text
from test_utils import gen_test_id

from mascope_backend.api.controllers.sample.files.process import streams
from mascope_backend.api.controllers.sample.files.process.streams import (
    StoreStreams,
    StreamRows,
    composite_key,
    kept_rows_note,
    stale_store_note,
)
from mascope_backend.db import AcquisitionStream, SampleFile
from mascope_signal.compute import StalePeakStoreError


REAGENT = "FTMS - p NSI Full ms [40.0000-138.0000] R=120000"
LOW = "FTMS - p NSI Full ms [66.0000-124.0000] R=120000"
POS = "FTMS + p NSI Full ms [40.0000-600.0000] R=120000"
POS_LOW = "FTMS + p NSI Full ms [66.0000-124.0000] R=120000"
FRAGMENTS = "FTMS - c NSI d Full ms2 188.9300@hcd30.00 [50.0000-200.0000] R=15000"
SETTLING = f"{REAGENT} event=1"
MEASURING = f"{REAGENT} event=2"
FILENAME = "ORBI-1_2026.10.05-12h00m00s_run"

#: The map the store records for the two negative streams, by their index
#: in the store's order (REAGENT, LOW).
MAP = {
    "rule": 1,
    "runs": {"-": [[40, 67, 0], [67, 122, 1], [122, 138, 0]]},
    "sources": {"-": "default"},
    "notes": [],
}


def _stream(
    key, polarity="-", event=1, scans=4, t_first=0.0, t_last=3.0, blocks=1, ms_order=1
):
    """A census entry, as ``mascope_thermo.streams.scan_streams`` gives one."""
    return {
        "key": key,
        "signature_key": key.split(" event=")[0],
        "signature": {"ms_order": ms_order, "polarity": polarity, "resolution": 120000},
        "scan_segment": 1,
        "scan_event": event,
        "scans": scans,
        "blocks": blocks,
        "t_first": t_first,
        "t_last": t_last,
        "filters": 1,
        "acquisition_params": {
            "source": "scripted",
            "scans_sampled": scans,
            "constant": {"Micro Scan Count:": 10},
            "varying": [],
        },
    }


def _released(key, polarity="-", scans=4):
    """A census entry as v1.10.1 wrote it: the key, the signature and the
    counts, and no identity."""
    return {
        "key": key,
        "signature": {"ms_order": 1, "polarity": polarity, "resolution": 120000},
        "scans": scans,
        "blocks": 1,
        "t_first": 0.0,
        "t_last": 3.0,
        "filters": 1,
        "acquisition_params": {},
    }


COMPOSITE_CENSUS = [
    _stream(REAGENT),
    _stream(LOW, event=2, t_first=4.0, t_last=9.0),
    _stream(POS, "+"),
]


def _read(census=None, props=None, raw=True, store=False, keys=(), stitch=None):
    """``read_store_streams`` for a file: ``census`` is what the raw file
    reads as, ``props`` what its ``.props`` holds; ``store`` False is a file
    with no peak store, otherwise the store's keys and map are what the
    signal library would read off it, a map given as an exception being
    raised by that read."""

    def _load(filename, var):
        if store is False:
            raise FileNotFoundError(var)
        return object()

    def _map(_store):
        if isinstance(stitch, Exception):
            raise stitch
        return stitch

    with (
        patch.object(streams.m_compute, "has_scan_streams", return_value=raw),
        patch.object(
            streams.m_compute, "get_scan_streams", return_value=list(census or [])
        ) as read_file,
        patch.object(
            streams.m_io, "read_props", return_value=props or {}
        ) as read_props,
        patch.object(streams.m_io, "load_array", side_effect=_load) as opened,
        patch.object(streams.m_compute, "peak_store_streams", return_value=list(keys)),
        patch.object(streams.m_compute, "peak_store_stitch_map", side_effect=_map),
    ):
        found = streams._read_store_streams(FILENAME)
    return found, read_file, read_props, opened


# -- what the census and the store say ---------------------------------------------


def test_a_raw_files_census_is_read_from_the_file_and_not_its_props():
    found, read_file, read_props, _opened = _read(
        COMPOSITE_CENSUS, props={"scan_streams": [_released(REAGENT)]}, store=False
    )

    assert found.streams == COMPOSITE_CENSUS
    read_file.assert_called_once_with(FILENAME)
    read_props.assert_not_called()


def test_a_raw_file_that_reads_as_no_streams_has_none():
    """A file whose reader yields no census: nothing to make rows from, and
    no store is opened to ask."""
    found, _read_file, _read_props, opened = _read([], store=True, keys=[REAGENT])

    assert found == StoreStreams()
    opened.assert_not_called()


def test_a_stored_census_without_identity_names_no_rows():
    """Every census a released version wrote: the key, the signature and
    the counts, and no signature_key. Such a file's items read nothing, as
    every item did before, and no store is opened."""
    found, _read_file, _read_props, opened = _read(
        raw=False,
        props={"scan_streams": [_released(REAGENT), _released(POS, "+")]},
        store=True,
        keys=[],
    )

    assert found == StoreStreams()
    opened.assert_not_called()


def test_a_stored_census_with_identity_names_rows():
    """A file kept without its raw file, converted since streams carried an
    identity: its stored census is what the rows are made from."""
    found, _read_file, read_props, _opened = _read(
        raw=False, props={"scan_streams": COMPOSITE_CENSUS}, store=False
    )

    assert found.streams == COMPOSITE_CENSUS
    read_props.assert_called_once_with(FILENAME)


def test_a_file_with_no_census_and_no_props_has_no_streams():
    """TofDaq files and files converted before the census."""
    found, _read_file, _read_props, opened = _read(
        raw=False, props={"mz_calibration": None}
    )

    assert found == StoreStreams()
    opened.assert_not_called()


def test_a_file_with_no_peak_store_is_its_census_alone():
    found, *_ = _read(COMPOSITE_CENSUS, store=False)

    assert found.streams == COMPOSITE_CENSUS
    assert (found.per_stream, found.stitch, found.stale) == ([], {}, [])


def test_a_pooled_store_adds_nothing_to_the_census():
    """Nearly every file: one peak list per polarity."""
    found, *_ = _read(COMPOSITE_CENSUS, store=True, keys=[])

    assert found.streams == COMPOSITE_CENSUS
    assert (found.per_stream, found.stitch, found.stale) == ([], {}, [])


def test_a_per_stream_store_gives_its_keys_and_its_map():
    found, *_ = _read(
        COMPOSITE_CENSUS, store=True, keys=[REAGENT, LOW, POS], stitch=MAP
    )

    assert found.per_stream == [REAGENT, LOW, POS]
    assert found.stitch == MAP
    assert found.stale == []
    # The streams stitched, by polarity, in the store's order; the positive
    # polarity has one stream and is not stitched
    assert found.segments() == {"-": [REAGENT, LOW]}


def test_a_composites_map_names_its_owners_by_stream_key():
    """The store's runs own by index into its keys; a row names the stream."""
    found, *_ = _read(
        COMPOSITE_CENSUS, store=True, keys=[REAGENT, LOW, POS], stitch=MAP
    )

    assert found.stitch_of("-") == {
        "rule": 1,
        "runs": [[40, 67, REAGENT], [67, 122, LOW], [122, 138, REAGENT]],
        "source": "default",
        "notes": [],
    }


def test_a_composite_carries_the_notes_that_concern_its_polarity():
    """The store's notes are the whole file's; a composite's row keeps those
    about its polarity or its own segments."""
    census = COMPOSITE_CENSUS + [_stream(POS_LOW, "+", event=2)]
    layout_note = (
        "The layout given for polarity + was not applied: it names the scan "
        "range 40-700, which none of the file's streams measures. Its map is "
        "the default one."
    )
    range_note = (
        f"Scan stream '{LOW}' states no scan range, so it owns no m/z and its "
        "peaks are in no composite."
    )
    stitch = {
        "rule": 1,
        "runs": {"-": [[40, 138, 0]], "+": [[40, 600, 2]]},
        "sources": {"-": "layout", "+": "default"},
        "notes": [layout_note, range_note],
    }
    found, *_ = _read(
        census, store=True, keys=[REAGENT, LOW, POS, POS_LOW], stitch=stitch
    )

    assert found.stitch_of("-")["notes"] == [range_note]
    assert found.stitch_of("+")["notes"] == [layout_note]


def test_a_store_holding_streams_the_file_does_not_read_back_is_stale():
    """Taken as pooled for the rows, and said to be stale: its peaks are
    detected again when a match meets it."""
    found, *_ = _read(
        [_stream(REAGENT), _stream(POS, "+")],
        store=True,
        keys=[REAGENT, LOW, POS],
        stitch=MAP,
    )

    assert found.streams == [_stream(REAGENT), _stream(POS, "+")]
    assert (found.per_stream, found.stitch) == ([], {})
    assert found.stale == [LOW]
    assert stale_store_note(found) == (
        f"The file's peak store holds scan streams the file does not read back "
        f"now: {LOW}. Its peaks are detected again when a match meets it; until "
        "then its streams are recorded as pooled."
    )


def test_a_per_stream_store_without_a_map_is_stale():
    found, *_ = _read(
        COMPOSITE_CENSUS,
        store=True,
        keys=[REAGENT, LOW, POS],
        stitch=StalePeakStoreError("no map"),
    )

    assert (found.per_stream, found.stitch, found.stale) == ([], {}, ["no map"])
    assert stale_store_note(found) == (
        "The file's peak store carries no stitch map. Its peaks are detected "
        "again when a match meets it; until then its streams are recorded as pooled."
    )


def test_a_sound_store_is_not_said_to_be_stale():
    found, *_ = _read(
        COMPOSITE_CENSUS, store=True, keys=[REAGENT, LOW, POS], stitch=MAP
    )

    assert stale_store_note(found) is None


def test_an_unreadable_props_costs_the_file_nothing():
    with (
        patch.object(streams.m_compute, "has_scan_streams", return_value=False),
        patch.object(streams.m_io, "read_props", side_effect=FileNotFoundError("gone")),
    ):
        assert streams._read_store_streams(FILENAME) == StoreStreams()


# -- which row an item reads, and what the detail says ------------------------------


def test_a_kept_row_is_named_with_the_samples_that_read_it():
    assert kept_rows_note(StreamRows()) is None
    assert kept_rows_note(StreamRows(kept={"k": ["si-1"]})) == (
        "Scan stream 'k' is no longer among the file's streams and is kept "
        "because a sample si-1 still reads it."
    )
    assert kept_rows_note(StreamRows(kept={"k": ["si-1", "si-2"]})) == (
        "Scan stream 'k' is no longer among the file's streams and is kept "
        "because samples si-1, si-2 still read it."
    )


def test_a_composite_kept_for_its_segment_says_so():
    """No sample reads it: it is kept for the segment that points at it."""
    assert kept_rows_note(
        StreamRows(kept={"low": ["si-1"]}, kept_for={"composite -": ["low"]})
    ) == (
        "Scan stream 'low' is no longer among the file's streams and is kept "
        "because a sample si-1 still reads it. Scan stream 'composite -' is no "
        "longer among the file's streams and is kept because its segment 'low' "
        "is kept."
    )


def test_an_item_reads_its_composite_else_its_one_stream_else_nothing():
    rows = StreamRows(
        ids={"a": "st-a", "b": "st-b", "p": "st-p", composite_key("-"): "st-c"},
        composites={"-": "st-c"},
        single={"+": "st-p"},
    )

    assert rows.item_stream("-") == "st-c"
    assert rows.item_stream("+") == "st-p"
    assert StreamRows().item_stream("-") is None


# -- their rows ----------------------------------------------------------------------


@pytest_asyncio.fixture
async def sample_file_id(async_session_factory):
    """A stored file to hang streams on, removed with them afterwards."""
    file_id = gen_test_id()
    async with async_session_factory() as session:
        session.add(
            SampleFile(
                sample_file_id=file_id,
                filename=f"stream-rows-{file_id}.raw",
                instrument="test-orbi-streams",
                datetime=datetime(2026, 10, 5, 12, 0),
                datetime_utc=datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc),
                length=6.0,
                range=[40.0, 600.0],
                polarity="-",
            )
        )
        await session.commit()
    yield file_id
    async with async_session_factory() as session:
        await session.execute(
            delete(SampleFile).where(SampleFile.sample_file_id == file_id)
        )
        await session.commit()


async def _rows(async_session_factory, file_id):
    async with async_session_factory() as session:
        found = (
            await session.execute(
                select(AcquisitionStream).where(
                    AcquisitionStream.sample_file_id == file_id
                )
            )
        ).scalars()
        return {row.stream_key: row for row in found}


def _composite_file():
    return StoreStreams(COMPOSITE_CENSUS, [REAGENT, LOW, POS], MAP)


@pytest.mark.asyncio
async def test_each_stream_gets_a_row_with_its_census(
    async_session_factory, sample_file_id
):
    """A pooled file: a row per stream, no composite, and the positive
    polarity's one stream is what its item reads."""
    found = StoreStreams(
        [
            _stream(SETTLING, scans=2, t_last=1.0),
            _stream(MEASURING, event=2, t_first=2.0, t_last=5.0),
            _stream(POS, "+"),
        ]
    )

    rows = await streams.sync_stream_rows(sample_file_id, found)

    stored = await _rows(async_session_factory, sample_file_id)
    assert rows.ids == {key: row.stream_id for key, row in stored.items()}
    assert set(stored) == {SETTLING, MEASURING, POS}
    measuring = stored[MEASURING]
    assert (measuring.signature_key, measuring.scan_segment, measuring.scan_event) == (
        REAGENT,
        1,
        2,
    )
    assert measuring.signature == {"ms_order": 1, "polarity": "-", "resolution": 120000}
    assert (
        measuring.scan_count,
        measuring.blocks,
        measuring.t_first,
        measuring.t_last,
    ) == (4, 1, 2.0, 5.0)
    assert (measuring.composite_stream_id, measuring.stitch) == (None, None)
    assert measuring.acquisition_params["constant"] == {"Micro Scan Count:": 10}
    assert rows.composites == {}
    assert (rows.kept, rows.kept_for) == ({}, {})
    # Two negative streams pooled: their item reads nothing; one positive: its own
    assert rows.item_stream("-") is None
    assert rows.item_stream("+") == stored[POS].stream_id


@pytest.mark.asyncio
async def test_a_stitched_polarity_gets_a_composite_its_segments_point_at(
    async_session_factory, sample_file_id
):
    rows = await streams.sync_stream_rows(sample_file_id, _composite_file())

    stored = await _rows(async_session_factory, sample_file_id)
    assert set(stored) == {REAGENT, LOW, POS, composite_key("-")}
    composite = stored[composite_key("-")]
    assert rows.composites == {"-": composite.stream_id}
    assert rows.item_stream("-") == composite.stream_id
    assert rows.item_stream("+") == stored[POS].stream_id
    # Its segments point at it, and nothing else does
    assert stored[REAGENT].composite_stream_id == composite.stream_id
    assert stored[LOW].composite_stream_id == composite.stream_id
    assert stored[POS].composite_stream_id is None
    assert composite.composite_stream_id is None
    # It carries the map and its polarity, and no census of its own
    assert (composite.scan_segment, composite.scan_event) == (None, None)
    assert composite.signature == {"ms_order": 1, "polarity": "-", "composite": True}
    assert (
        composite.signature_key,
        composite.acquisition_params,
        composite.scan_count,
        composite.blocks,
        composite.t_first,
        composite.t_last,
    ) == (None,) * 6
    assert composite.stitch["runs"] == [
        [40, 67, REAGENT],
        [67, 122, LOW],
        [122, 138, REAGENT],
    ]
    assert composite.stitch["source"] == "default"
    assert stored[REAGENT].stitch is None


@pytest.mark.asyncio
async def test_both_polarities_stitched_get_a_composite_each(
    async_session_factory, sample_file_id
):
    """With a fragmentation stream first in the census: the composites'
    owners are named by the store's keys, not by the census's places, and
    each polarity's composite has its own key, map and segments."""
    census = [
        _stream(FRAGMENTS, ms_order=2),
        _stream(REAGENT),
        _stream(LOW, event=2),
        _stream(POS, "+"),
        _stream(POS_LOW, "+", event=2),
    ]
    found = StoreStreams(
        census,
        [REAGENT, LOW, POS, POS_LOW],
        {
            "rule": 1,
            "runs": {
                "-": [[40, 67, 0], [67, 138, 1]],
                "+": [[40, 67, 2], [67, 600, 3]],
            },
            "sources": {"-": "default", "+": "default"},
            "notes": [],
        },
    )

    rows = await streams.sync_stream_rows(sample_file_id, found)

    stored = await _rows(async_session_factory, sample_file_id)
    assert set(stored) == {
        FRAGMENTS,
        REAGENT,
        LOW,
        POS,
        POS_LOW,
        composite_key("-"),
        composite_key("+"),
    }
    negative, positive = stored[composite_key("-")], stored[composite_key("+")]
    assert rows.composites == {"-": negative.stream_id, "+": positive.stream_id}
    assert negative.stitch["runs"] == [[40, 67, REAGENT], [67, 138, LOW]]
    assert positive.stitch["runs"] == [[40, 67, POS], [67, 600, POS_LOW]]
    assert {key: row.composite_stream_id for key, row in stored.items()} == {
        FRAGMENTS: None,
        REAGENT: negative.stream_id,
        LOW: negative.stream_id,
        POS: positive.stream_id,
        POS_LOW: positive.stream_id,
        composite_key("-"): None,
        composite_key("+"): None,
    }
    assert rows.single == {}


@pytest.mark.asyncio
async def test_a_fragmentation_stream_is_a_row_and_counts_toward_no_polarity(
    async_session_factory, sample_file_id
):
    """One MS1 stream beside an MS2 stream: the polarity holds one stream,
    and its item reads it."""
    found = StoreStreams([_stream(REAGENT), _stream(FRAGMENTS, ms_order=2)])

    rows = await streams.sync_stream_rows(sample_file_id, found)

    stored = await _rows(async_session_factory, sample_file_id)
    assert set(stored) == {REAGENT, FRAGMENTS}
    assert rows.item_stream("-") == stored[REAGENT].stream_id


@pytest.mark.asyncio
async def test_a_stream_and_its_composite_keep_their_rows_when_processed_again(
    async_session_factory, sample_file_id
):
    """Their ids stay, so whatever points at them still does; the census is
    brought up to date."""
    first = await streams.sync_stream_rows(sample_file_id, _composite_file())

    again = StoreStreams(
        [
            _stream(REAGENT, scans=6),
            _stream(LOW, event=2, t_first=4.0, t_last=9.0),
            _stream(POS, "+"),
        ],
        [REAGENT, LOW, POS],
        MAP,
    )
    second = await streams.sync_stream_rows(sample_file_id, again)

    assert second == first
    stored = await _rows(async_session_factory, sample_file_id)
    assert stored[REAGENT].scan_count == 6
    assert stored[composite_key("-")].scan_count is None


@pytest.mark.asyncio
async def test_a_polarity_no_longer_stitched_loses_its_composite(
    async_session_factory, sample_file_id
):
    """Processed again with a pooled store: the composite row goes, and its
    segments stop pointing at it first."""
    await streams.sync_stream_rows(sample_file_id, _composite_file())

    rows = await streams.sync_stream_rows(
        sample_file_id, StoreStreams(COMPOSITE_CENSUS)
    )

    stored = await _rows(async_session_factory, sample_file_id)
    assert set(stored) == {REAGENT, LOW, POS}
    assert all(row.composite_stream_id is None for row in stored.values())
    assert rows.composites == {}
    assert rows.item_stream("-") is None


@pytest.mark.asyncio
async def test_segments_go_with_their_composite_when_nothing_reads_them(
    async_session_factory, sample_file_id
):
    """A composite file synced again with only its other polarity left:
    the composite and both its segments go together, unpointed first."""
    await streams.sync_stream_rows(sample_file_id, _composite_file())

    rows = await streams.sync_stream_rows(
        sample_file_id, StoreStreams([_stream(POS, "+")])
    )

    stored = await _rows(async_session_factory, sample_file_id)
    assert set(stored) == {POS}
    assert (rows.kept, rows.kept_for) == ({}, {})
    assert rows.item_stream("+") == stored[POS].stream_id


@pytest.mark.asyncio
async def test_a_stream_the_file_no_longer_holds_loses_its_row(
    async_session_factory, sample_file_id
):
    await streams.sync_stream_rows(
        sample_file_id,
        StoreStreams([_stream(SETTLING), _stream(MEASURING, event=2)]),
    )

    rows = await streams.sync_stream_rows(
        sample_file_id, StoreStreams([_stream(MEASURING, event=2)])
    )

    stored = await _rows(async_session_factory, sample_file_id)
    assert set(stored) == {MEASURING}
    assert set(rows.ids) == {MEASURING}


async def _sample_reading(async_session_factory, sample_file_id, stream_id):
    """A person's sample of the file, reading one of its streams; returns
    what removes it and its batch again."""
    tag = stream_id[-8:]
    ids = {
        "workspace": f"ws-kept-{tag}",
        "dataset": f"ds-kept-{tag}",
        "batch": f"sb-kept-{tag}",
        "item": f"si-kept-{tag}",
        "file": sample_file_id,
        "stream": stream_id,
    }
    async with async_session_factory() as session:
        for statement in (
            "INSERT INTO workspace (workspace_id, workspace_name)"
            " VALUES (:workspace, 'kept')",
            "INSERT INTO dataset (dataset_id, workspace_id, dataset_name)"
            " VALUES (:dataset, :workspace, 'kept')",
            "INSERT INTO sample_batch (sample_batch_id, dataset_id, sample_batch_name)"
            " VALUES (:batch, :dataset, 'kept')",
            "INSERT INTO sample_item (sample_item_id, sample_batch_id, sample_file_id,"
            " sample_item_name, sample_item_type, polarity, stream_id)"
            " VALUES (:item, :batch, :file, 'a copy', 'UNKNOWN', '-', :stream)",
        ):
            await session.execute(text(statement), ids)
        await session.commit()

    async def _remove():
        async with async_session_factory() as session:
            await session.execute(
                text("DELETE FROM sample_item WHERE sample_item_id = :item"), ids
            )
            await session.execute(
                text("DELETE FROM workspace WHERE workspace_id = :workspace"), ids
            )
            await session.commit()

    return ids["item"], _remove


@pytest.mark.asyncio
async def test_a_row_a_sample_still_reads_is_kept_and_reported(
    async_session_factory, sample_file_id
):
    """A person's copy of an item, then a rebuild under which the polarity is
    no longer stitched: the composite the copy reads stays, with its id, and
    is reported; the segment nobody reads goes."""
    first = await streams.sync_stream_rows(sample_file_id, _composite_file())
    item_id, remove = await _sample_reading(
        async_session_factory, sample_file_id, first.composites["-"]
    )
    try:
        rows = await streams.sync_stream_rows(
            sample_file_id, StoreStreams([_stream(REAGENT), _stream(POS, "+")])
        )

        stored = await _rows(async_session_factory, sample_file_id)
        assert set(stored) == {REAGENT, POS, composite_key("-")}
        assert stored[composite_key("-")].stream_id == first.composites["-"]
        assert stored[REAGENT].composite_stream_id is None
        assert rows.kept == {composite_key("-"): [item_id]}
        assert rows.kept_for == {}
        assert rows.composites == {}
        # The polarity's item reads the composite no longer: two streams
        # gone to one, the one stream is what it reads
        assert rows.item_stream("-") == stored[REAGENT].stream_id
    finally:
        await remove()


@pytest.mark.asyncio
async def test_a_kept_segment_keeps_a_composite_the_file_no_longer_has(
    async_session_factory, sample_file_id
):
    """A sample reading a segment keeps that segment; the composite it
    points at is no longer the file's either, so it is kept for the segment
    and said to be, and not deleted from under it."""
    first = await streams.sync_stream_rows(sample_file_id, _composite_file())
    item_id, remove = await _sample_reading(
        async_session_factory, sample_file_id, first.ids[LOW]
    )
    try:
        rows = await streams.sync_stream_rows(
            sample_file_id, StoreStreams([_stream(POS, "+")])
        )

        stored = await _rows(async_session_factory, sample_file_id)
        assert set(stored) == {POS, LOW, composite_key("-")}
        assert stored[LOW].composite_stream_id == stored[composite_key("-")].stream_id
        assert rows.kept == {LOW: [item_id]}
        assert rows.kept_for == {composite_key("-"): [LOW]}
    finally:
        await remove()


@pytest.mark.asyncio
async def test_a_kept_segment_is_unpointed_from_a_composite_the_file_still_has(
    async_session_factory, sample_file_id
):
    """The polarity stays stitched but no longer from this segment: its
    composite keeps its id and takes the new map, and the kept segment stops
    pointing at it, since the map no longer names it."""
    first = await streams.sync_stream_rows(sample_file_id, _composite_file())
    item_id, remove = await _sample_reading(
        async_session_factory, sample_file_id, first.ids[LOW]
    )
    try:
        narrow = "FTMS - p NSI SIM ms [72.0000-118.0000] R=120000"
        again = StoreStreams(
            [_stream(REAGENT), _stream(narrow, event=3), _stream(POS, "+")],
            [REAGENT, narrow, POS],
            {
                "rule": 1,
                "runs": {"-": [[40, 73, 0], [73, 116, 1], [116, 138, 0]]},
                "sources": {"-": "default"},
                "notes": [],
            },
        )
        rows = await streams.sync_stream_rows(sample_file_id, again)

        stored = await _rows(async_session_factory, sample_file_id)
        composite = stored[composite_key("-")]
        assert composite.stream_id == first.composites["-"]
        assert composite.stitch["runs"] == [
            [40, 73, REAGENT],
            [73, 116, narrow],
            [116, 138, REAGENT],
        ]
        assert stored[LOW].composite_stream_id is None
        assert {key for key, row in stored.items() if row.composite_stream_id} == {
            REAGENT,
            narrow,
        }
        assert rows.kept == {LOW: [item_id]}
        assert rows.kept_for == {}
    finally:
        await remove()


@pytest.mark.asyncio
async def test_a_file_that_records_no_experiment_has_neither_segment_nor_event(
    async_session_factory, sample_file_id
):
    stream = _stream(REAGENT, event=None)
    del stream["scan_segment"], stream["scan_event"]

    await streams.sync_stream_rows(sample_file_id, StoreStreams([stream]))

    row = (await _rows(async_session_factory, sample_file_id))[REAGENT]
    assert (row.scan_segment, row.scan_event) == (None, None)


@pytest.mark.asyncio
async def test_another_files_streams_are_left_alone(
    async_session_factory, sample_file_id
):
    """The same key in two files is two streams, and syncing one file's rows
    neither reuses nor deletes the other's."""
    other_id = gen_test_id()
    async with async_session_factory() as session:
        session.add(
            SampleFile(
                sample_file_id=other_id,
                filename=f"stream-rows-{other_id}.raw",
                instrument="test-orbi-streams",
                datetime=datetime(2026, 10, 5, 13, 0),
                datetime_utc=datetime(2026, 10, 5, 13, 0, tzinfo=timezone.utc),
                length=6.0,
                range=[40.0, 600.0],
                polarity="-",
            )
        )
        await session.commit()
    try:
        other = await streams.sync_stream_rows(
            other_id, StoreStreams([_stream(SETTLING)])
        )

        mine = await streams.sync_stream_rows(
            sample_file_id, StoreStreams([_stream(SETTLING)])
        )
        await streams.sync_stream_rows(
            sample_file_id, StoreStreams([_stream(MEASURING, event=2)])
        )

        assert mine.ids[SETTLING] != other.ids[SETTLING]
        assert set(await _rows(async_session_factory, other_id)) == {SETTLING}
    finally:
        async with async_session_factory() as session:
            await session.execute(
                delete(SampleFile).where(SampleFile.sample_file_id == other_id)
            )
            await session.commit()
