"""
MS2 parts: an acquisition of fragmentation scans alone, filed without a chemistry.

A file's fragmentation scans are read through a sample item: every MS2 route
selects the scans of an item's file, polarity and time window, and the SDK
addresses them the same way. The pipeline makes a file's items by binding it
to an ionization mode, and a file no rung of the binding ladder binds waits
as ``needs_chemistry`` with no item at all.

For a file with MS1 scans that is right, MS2 scans or not: a chemistry is
what its MS1 peaks are calibrated, matched and assigned under, and only a
person can say which one it was acquired under. A file that records no MS1
scans (#2068) has no peaks to do any of that to, so no chemistry would change
anything about it - and waiting for one would leave its MS2 data unreachable
(``docs/dev/ingest_routing_and_splitting.md``, section 4.2).

So a file nothing binds whose scan-stream census shows fragmentation scans
and no MS1 scans is filed as it is: one ACQUISITION item per polarity that
holds fragmentation scans, bound to no ionization mode, in the day's MS2
batch of the instrument's acquisition dataset. This is the note's MS2-only
part - an MS2 stream with no parent item to be reached through.

The item is the pipeline's own, like every ACQUISITION item of an ACQUISITION
batch, so re-processing the file - after a token is added for it, say -
replaces it rather than leaving a second sample of the file beside it.

**Read from the census, never from the file.** Which polarities hold
fragmentation scans is what the converter recorded in ``.props``
(``SampleFileProps.scan_streams``). A file with no census - converted before
the census existed, or by a reader that takes none - says nothing either way,
and waits for a chemistry as it always did.
"""

from mascope_backend.api.controllers.sample.batches.sample_batches_controller import (
    get_or_create_acquisition_batch,
)
from mascope_backend.api.controllers.sample.items.sample_items_controller import (
    create_sample_items,
)
from mascope_backend.api.models.sample.batches.sample_batch_pydantic_model import (
    SampleBatchCreate,
)
from mascope_backend.api.models.sample.items.sample_item_pydantic_model import (
    AcquisitionItemCreate,
)
from mascope_backend.db import SampleFile


#: What the day's batch of MS2 parts is called, where a bound item's batch
#: names its ionization mode: ``"<date> MS2 acquisition"``.
MS2_BATCH_LABEL = "MS2"


def _ms_order(stream: dict) -> int | None:
    """A census stream's MS order, or None when it is not a whole number."""
    order = stream["signature"].get("ms_order")
    # bool is an int, and True would read as MS1.
    return order if isinstance(order, int) and not isinstance(order, bool) else None


def records_ms1_scans(streams: list[dict] | None) -> bool:
    """Whether the census shows any MS1 scan.

    :param streams: The file's scan-stream census, as
        :func:`~...process.status.read_scan_streams` returns it.
    :return: True when a stream of MS order 1 is recorded. False for a file
        with no census too, so read :func:`records_only_fragmentation_scans`
        where "no census" must not be taken for "no MS1 scans".
    :rtype: bool
    """
    return any(_ms_order(stream) == 1 for stream in streams or [])


def records_only_fragmentation_scans(streams: list[dict] | None) -> bool:
    """Whether the census shows scans, and no MS1 scan among them.

    What tells an acquisition of fragmentation scans alone from a blank
    measurement. Both are stored with no peaks and no instrument config, and
    only the first has data that peaks are simply not detected in.

    :param streams: The file's scan-stream census.
    :return: True when it holds streams and none is of MS order 1.
    :rtype: bool
    """
    return bool(streams) and not records_ms1_scans(streams)


def fragmentation_polarities(
    streams: list[dict] | None, polarities: str | None
) -> list[str]:
    """The polarities of a file whose scans include fragmentation scans.

    :param streams: The file's scan-stream census.
    :param polarities: ``sample_file.polarity``: the polarities the file
        holds, which also gives the order the answer comes in. A polarity the
        census names and the file does not is left out, because an item is
        made per polarity of the file.
    :return: Those of the file's polarities that hold a stream of MS order 2
        or higher; empty when there is no census.
    :rtype: list[str]
    """
    # Compared as text, one stream at a time: a census whose polarity is not a
    # string names no polarity of the file, and must not fail the file either.
    fragmented = [
        stream["signature"].get("polarity")
        for stream in streams or []
        if (_ms_order(stream) or 0) > 1
    ]
    return [polarity for polarity in polarities or "" if polarity in fragmented]


def ms2_batch_name(sample_file: SampleFile) -> str:
    """The name of the daily batch a file's MS2 parts are filed in.

    Dated off the file's instrument-local time, as the batches of bound items
    are, so one day's MS2 parts and bound samples carry the same date.
    """
    return f"{sample_file.datetime.strftime('%Y-%m-%d')} {MS2_BATCH_LABEL} acquisition"


async def file_fragmentation_scans(
    sample_file: SampleFile, dataset_id: str, polarities: list[str]
) -> tuple[list[dict], list[dict]]:
    """File a file's fragmentation scans as MS2 parts, one per polarity.

    Each is an ACQUISITION item with no ionization mode and no binding rung:
    nothing bound it, which is what NULL in both columns already means. The
    item covers the whole acquisition in its polarity, so the MS2 routes read
    every fragmentation scan of that polarity through it.

    The batch carries no target collections: the collections of an
    ACQUISITION batch are its mode's, and there is no mode.

    :param sample_file: The file.
    :param dataset_id: The instrument's ACQUISITION dataset for the file's year.
    :param polarities: The polarities to file, from
        :func:`fragmentation_polarities`.
    :return: The created sample items and the batches they were filed in.
    :rtype: tuple[list[dict], list[dict]]
    """
    batch_name = ms2_batch_name(sample_file)
    batches: list[dict] = []
    sample_items: list[AcquisitionItemCreate] = []
    for polarity in polarities:
        # The same natural key as a bound item's batch - (dataset, name,
        # polarity) - so files of one day converge on one batch per polarity
        # whichever worker handles them (get_or_create_acquisition_batch).
        batch = (
            await get_or_create_acquisition_batch(
                sample_batch=SampleBatchCreate(
                    dataset_id=dataset_id,
                    sample_batch_name=batch_name,
                    sample_batch_description=(
                        "Auto-generated daily batch of fragmentation "
                        f"acquisitions filed without a chemistry for "
                        f"{sample_file.instrument}"
                    ),
                    sample_batch_type="ACQUISITION",
                    polarity=polarity,
                    target_collection_ids=[],
                )
            )
        ).get("data")
        batches.append(batch)
        sample_items.append(
            AcquisitionItemCreate(
                sample_batch_id=batch["sample_batch_id"],
                sample_file_id=sample_file.sample_file_id,
                sample_item_name=sample_file.datetime.strftime("%Y-%m-%d %H:%M:%S"),
                sample_item_type="ACQUISITION",
                sample_item_attributes={},
                polarity=polarity,
                ionization_mode_id=None,
                bound_by=None,
                method_binding_id=None,
            )
        )
    samples = (
        await create_sample_items(
            sample_items=sample_items, independent_transaction=True
        )
    ).get("data", [])
    return samples, batches


def filed_sentence(batches: list[dict]) -> str:
    """Say where a file's MS2 parts were filed, for its processing detail.

    :param batches: The batches, as :func:`file_fragmentation_scans` returns
        them.
    :return: One clause naming each batch and its polarity.
    :rtype: str
    """
    named = " and ".join(
        f"'{batch.get('sample_batch_name')}' ({batch.get('polarity')})"
        for batch in batches
    )
    samples = "an MS2 sample" if len(batches) == 1 else "MS2 samples"
    return f"filed as {samples} in {named}"
