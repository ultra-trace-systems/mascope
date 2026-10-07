"""The scan streams of a file as rows, and the one a sample item reads.

Every file whose census is read gets one row per scan stream in
``acquisition_stream`` (``docs/dev/ingest_routing_and_splitting.md``,
section 4.4). Where a polarity's streams were detected apart and stitched
into one spectrum, the polarity's composite is a row too: its segments point
at it, and it carries the map they were stitched by (4.5). A file's item of
a polarity reads one stream - the composite where the polarity has one, else
the polarity's one stream, else none: a polarity pooled from several
streams, or a file with no census, gives an item over every MS1 scan of its
polarity, which is what an item has always meant.

**The census the rows are made from is the file's own, read when the rows
are written.** A raw Orbitrap file is read for its streams here, with the
reader of the day, as peak detection reads it for the keys of a per-stream
store; the census stored in ``.props`` at conversion keeps serving the
status and the binding. The two readings then come from one reader, and a
store whose keys the file does not read back now is what it is: stale. Any
other file has only its stored census, which names its rows where it
carries the streams' identity; a census written before streams carried one
names no rows, and the file's items read nothing, as every item did before.

**The peak store decides what is stitched, not the deployment's setting.**
Whether a file's peaks were detected per stream was decided when they were,
and the store records it with the map (``mascope_signal.compute``
``peak_store_streams`` and ``peak_store_stitch_map``). A per-stream store
whose keys the file no longer reads back, or that carries no map, is stale:
its peaks are detected again when a match meets it, and until then the rows
describe the file's streams with nothing stitched, and the file's processing
detail says so (:func:`stale_store_note`).

**A rebuild updates the rows in place.** A bind and a process-on-request
rebuild a file that has a person's sample, and that sample may read one of
the file's streams, so the rows cannot be deleted and written again: each is
matched on its key and keeps its id. A row the new census no longer gives is
deleted, unless an item still reads it - then it is kept, with the composite
it points at where that composite is kept too, and the file's processing
detail names it (:func:`kept_rows_note`).
"""

import asyncio
from dataclasses import dataclass, field

from sqlalchemy import select

import mascope_file.io as m_io
import mascope_signal.compute as m_compute
from mascope_backend.db import AcquisitionStream, SampleItem, async_session
from mascope_backend.db.id import gen_id
from mascope_backend.method_keys import usable_streams
from mascope_backend.runtime import runtime


def composite_key(polarity: str) -> str:
    """The stream key of a polarity's composite row, the same on every run.

    A composite selects no scans, so its key names nothing in the file; it
    is the row's name among the file's rows, fixed by the polarity so that a
    rebuild finds the row it wrote before.
    """
    return f"composite {polarity}"


@dataclass(frozen=True)
class StoreStreams:
    """What a file's census and its peak store say of its streams."""

    #: The census, every stream in the file's order
    #: (``mascope_thermo.streams.scan_streams``); empty for a file without
    #: one, or whose stored census carries no identity.
    streams: list[dict] = field(default_factory=list)
    #: The keys of the streams the store holds a peak list for each of, in
    #: the store's order; empty for a pooled store, which is nearly every
    #: file, and for a stale one.
    per_stream: list[str] = field(default_factory=list)
    #: The store's stitch map (``mascope_signal.stitch.stitch_map``); empty
    #: for a pooled store.
    stitch: dict = field(default_factory=dict)
    #: Why a per-stream store was taken as pooled: the keys the file does
    #: not read back, or ``["no map"]``. Empty where the store is sound.
    stale: list[str] = field(default_factory=list)

    def polarity_of(self, key: str) -> str | None:
        for stream in self.streams:
            if stream.get("key") == key:
                return stream["signature"].get("polarity")
        return None

    def segments(self) -> dict[str, list[str]]:
        """The streams stitched into a composite, by polarity, in the
        store's order: every stream of a polarity the map stitches."""
        runs = self.stitch.get("runs") or {}
        return {
            polarity: [
                key for key in self.per_stream if self.polarity_of(key) == polarity
            ]
            for polarity in runs
        }

    def stitch_of(self, polarity: str) -> dict:
        """The map of one polarity as its composite row carries it: the
        rule, the runs with their owner named by stream key, where the runs
        came from, and the notes that concern this polarity or its
        segments."""
        keys = self.segments().get(polarity, [])
        return {
            "rule": self.stitch.get("rule"),
            "runs": [
                [lower, upper, self.per_stream[index]]
                for lower, upper, index in self.stitch["runs"][polarity]
            ],
            "source": (self.stitch.get("sources") or {}).get(polarity),
            "notes": [
                note
                for note in self.stitch.get("notes") or []
                if f"polarity {polarity}" in note or any(key in note for key in keys)
            ],
        }


@dataclass(frozen=True)
class StreamRows:
    """A file's stream rows, by what an item needs of them."""

    #: ``stream_id`` by stream key, composites under their own key.
    ids: dict[str, str] = field(default_factory=dict)
    #: The composite's ``stream_id`` by polarity, for the polarities stitched.
    composites: dict[str, str] = field(default_factory=dict)
    #: The one MS1 stream's ``stream_id`` by polarity, for the polarities
    #: that hold exactly one.
    single: dict[str, str] = field(default_factory=dict)
    #: Rows the file no longer describes, kept because an item still reads
    #: them: the item ids by stream key.
    kept: dict[str, list[str]] = field(default_factory=dict)
    #: Composites the file no longer describes, kept because a kept segment
    #: still points at them: the segments' keys by the composite's key.
    kept_for: dict[str, list[str]] = field(default_factory=dict)

    def item_stream(self, polarity: str) -> str | None:
        """The stream an item of this polarity reads: its composite, else
        its one stream, else none."""
        return self.composites.get(polarity) or self.single.get(polarity)


def kept_rows_note(rows: StreamRows) -> str | None:
    """What the file's processing detail says of the rows kept.

    A sample is named by its id: it is what the API, the SDK and a sample's
    address speak, and it holds where a name changes; a batch's name may
    belong to people who cannot list this file.

    :param rows: The file's rows, as :func:`sync_stream_rows` returns them.
    :return: One sentence per kept row, or None when none was kept.
    """
    notes = [
        f"Scan stream '{key}' is no longer among the file's streams and is "
        f"kept because {'a sample' if len(items) == 1 else 'samples'} "
        f"{', '.join(items)} still {'reads' if len(items) == 1 else 'read'} it."
        for key, items in rows.kept.items()
    ] + [
        f"Scan stream '{key}' is no longer among the file's streams and is "
        f"kept because its {'segment' if len(segments) == 1 else 'segments'} "
        f"{', '.join(repr(segment) for segment in segments)} "
        f"{'is' if len(segments) == 1 else 'are'} kept."
        for key, segments in rows.kept_for.items()
    ]
    return " ".join(notes) or None


def stale_store_note(found: StoreStreams) -> str | None:
    """What the file's processing detail says of a stale per-stream store.

    :param found: The file's streams, as :func:`read_store_streams` returns
        them.
    :return: The sentence, or None where the store is sound.
    """
    if not found.stale:
        return None
    reason = (
        "carries no stitch map"
        if found.stale == ["no map"]
        else "holds scan streams the file does not read back now: "
        + "; ".join(found.stale)
    )
    return (
        f"The file's peak store {reason}. Its peaks are detected again when a "
        "match meets it; until then its streams are recorded as pooled."
    )


def _census(filename: str) -> list[dict]:
    """The census the rows are made from: the file's own for a raw Orbitrap
    file, else the stored one where it carries the streams' identity."""
    if m_compute.has_scan_streams(filename):
        return m_compute.get_scan_streams(filename)
    try:
        streams = usable_streams(m_io.read_props(filename).get("scan_streams"))
    except Exception:  # noqa: BLE001 - a missing census is not a processing error
        # A file whose .props cannot be read is processed as every file was
        # before streams: nothing that reads this may cost it its processing.
        runtime.logger.opt(exception=True).debug(
            f"Could not read the .props of {filename}"
        )
        return []
    if streams and not all("signature_key" in stream for stream in streams):
        # A census from before streams carried an identity (#2273), as every
        # released version wrote them: it names no rows
        runtime.logger.debug(
            f"The stored census of {filename} carries no stream identity; "
            "no stream rows are written for it"
        )
        return []
    return streams


def _read_store_streams(filename: str) -> StoreStreams:
    """Synchronous body of :func:`read_store_streams`: it reads the filestore."""
    streams = _census(filename)
    if not streams:
        return StoreStreams()
    try:
        store = m_io.load_array(filename, var="peak_timeseries")
    except FileNotFoundError:
        # No peak store yet: its census alone describes the file
        return StoreStreams(streams)
    keys = m_compute.peak_store_streams(store)
    if not keys:
        return StoreStreams(streams)
    known = {stream.get("key") for stream in streams}
    if missing := [key for key in keys if key not in known]:
        return StoreStreams(streams, stale=missing)
    try:
        stitch = m_compute.peak_store_stitch_map(store) or {}
    except m_compute.StalePeakStoreError:
        return StoreStreams(streams, stale=["no map"])
    return StoreStreams(streams, keys, stitch)


async def read_store_streams(filename: str) -> StoreStreams:
    """A file's streams, as its census and its peak store give them.

    :param filename: The sample file's stored name.
    :return: The census, the store's keys and the store's map; empty where
        the file has no census that names its streams; the keys and the map
        empty, and ``stale`` saying why, where the store is stale.
    """
    return await asyncio.to_thread(_read_store_streams, filename)


def _census_columns(stream: dict) -> dict:
    """A stream's row: its census, and no map."""
    return {
        "signature_key": stream["signature_key"],
        "scan_segment": stream.get("scan_segment"),
        "scan_event": stream.get("scan_event"),
        "signature": stream["signature"],
        "acquisition_params": stream.get("acquisition_params"),
        "scan_count": stream["scans"],
        "blocks": stream["blocks"],
        "t_first": stream["t_first"],
        "t_last": stream["t_last"],
        "stitch": None,
    }


def _composite_columns(found: StoreStreams, polarity: str) -> dict:
    """A composite's row: the map, its polarity, and no census - it has no
    scans of its own (``census_or_map``)."""
    return {
        "signature_key": None,
        "scan_segment": None,
        "scan_event": None,
        "signature": {"ms_order": 1, "polarity": polarity, "composite": True},
        "acquisition_params": None,
        "scan_count": None,
        "blocks": None,
        "t_first": None,
        "t_last": None,
        "stitch": found.stitch_of(polarity),
    }


async def sync_stream_rows(sample_file_id: str, found: StoreStreams) -> StreamRows:
    """Make a file's stream rows describe these streams, in place.

    A stream the file already has a row for keeps that row, and its id: the
    census columns are brought up to date and nothing that points at the row
    has to move. A composite keeps its row the same way, under its own key.
    A row whose stream is no longer among them is deleted, unless an item
    still reads it: that row is kept as it is, and so is the composite it
    points at where that composite is no longer among them either; a kept
    segment whose composite the file still has is unpointed, since that
    composite's map no longer names it. Both are reported through
    :attr:`StreamRows.kept` and :attr:`StreamRows.kept_for`.

    :param sample_file_id: The file the streams are of.
    :param found: The streams, as :func:`read_store_streams` returns them.
    :return: The rows, by what an item needs of them.
    """
    segments = found.segments()
    wanted: dict[str, dict] = {
        stream["key"]: _census_columns(stream) for stream in found.streams
    }
    for polarity in segments:
        wanted[composite_key(polarity)] = _composite_columns(found, polarity)

    async with async_session() as session:
        rows = {
            row.stream_key: row
            for row in (
                await session.execute(
                    select(AcquisitionStream).where(
                        AcquisitionStream.sample_file_id == sample_file_id
                    )
                )
            ).scalars()
        }
        kept: dict[str, AcquisitionStream] = {}
        for key, columns in wanted.items():
            row = rows.pop(key, None)
            if row is None:
                row = AcquisitionStream(
                    stream_id=gen_id(),
                    sample_file_id=sample_file_id,
                    stream_key=key,
                    composite_stream_id=None,
                    **columns,
                )
                session.add(row)
            else:
                for column, value in columns.items():
                    setattr(row, column, value)
                row.composite_stream_id = None
            kept[key] = row

        # The rows the file no longer describes: those an item still reads
        # stay, with the composite they point at where it is stale too, and
        # the rest go
        stale = dict(rows)
        readers: dict[str, list[str]] = {}
        if stale:
            for item_id, stream_id in (
                await session.execute(
                    select(SampleItem.sample_item_id, SampleItem.stream_id).where(
                        SampleItem.stream_id.in_(
                            [row.stream_id for row in stale.values()]
                        )
                    )
                )
            ).all():
                readers.setdefault(stream_id, []).append(item_id)
        by_id = {row.stream_id: row for row in stale.values()}
        keeping = {row.stream_id for row in stale.values() if row.stream_id in readers}
        kept_for: dict[str, list[str]] = {}
        for stream_id in list(keeping):
            parent = by_id[stream_id].composite_stream_id
            if parent in by_id:
                keeping.add(parent)
                kept_for.setdefault(by_id[parent].stream_key, []).append(
                    by_id[stream_id].stream_key
                )
            elif parent is not None:
                # Its composite is still the file's, with a map that no
                # longer names this segment: pointed no more
                by_id[stream_id].composite_stream_id = None
        going = [row for row in stale.values() if row.stream_id not in keeping]

        # No row may point at one about to go, and a composite has to exist
        # before its segments point at it: the references are written after
        # every row is, and cleared before any is deleted.
        for row in going:
            row.composite_stream_id = None
        await session.flush()
        for polarity, keys in segments.items():
            composite_id = kept[composite_key(polarity)].stream_id
            for key in keys:
                kept[key].composite_stream_id = composite_id
        for row in going:
            await session.delete(row)
        await session.commit()
        ids = {key: row.stream_id for key, row in kept.items()}
        kept_stale = {
            row.stream_key: sorted(readers[row.stream_id])
            for row in stale.values()
            if row.stream_id in readers
        }

    by_polarity: dict[str, list[str]] = {}
    for stream in found.streams:
        signature = stream["signature"]
        if signature.get("ms_order") == 1:
            by_polarity.setdefault(signature.get("polarity"), []).append(stream["key"])
    return StreamRows(
        ids=ids,
        composites={polarity: ids[composite_key(polarity)] for polarity in segments},
        single={
            polarity: ids[keys[0]]
            for polarity, keys in by_polarity.items()
            if len(keys) == 1
        },
        kept=kept_stale,
        kept_for={key: sorted(value) for key, value in kept_for.items()},
    )
