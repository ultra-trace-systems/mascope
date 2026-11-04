"""
Give the copies made before the origin key one, where their window says which
part they are.

A copy records which part of the acquisition record it came from in
``sample_item.origin_key``, so that a batch refreshed from its filter does not
add what it already holds (``docs/dev/sample_filters_and_metadata.md``,
sections 4.3 and 6). Copies made before the column have none, and hold no
reference to the sample they were copied from: what is left to go on is their
own file, polarity and window. A copy whose window is still an acquisition
sample's of its file and polarity is that part, and is given the key a copy
of it is made with now (``origin_key``). Every other one is left without a
key and counted:

- **edited**: its file and polarity have an acquisition sample, but none
  with its window. Its window was changed on the copy, or it was cut by hand,
  or the file was re-processed under another window since.
- **no source**: its file and polarity have no acquisition sample at all.

A copy without a key is what every copy was before, so leaving one out loses
nothing: a refresh counts its part as new and offers to add it again. The
key is never edited once set, so a copy that has one is not looked at.

Copies are the items of ``ANALYSIS`` batches. A sample a person made from a
file in such a batch is among them; where its window is the acquisition
sample's it is the same part, and the key is as true of it.

Entry Points:
- Async: ``backfill_origin_keys()`` for async callers
- CLI:   ``mascope dev/prod db script run backfill_origin_keys``
"""

from sqlalchemy import String, column, exists, func, select, update, values
from sqlalchemy.orm import aliased

from mascope_backend.api.controllers.sample.lib.origin_key import origin_key
from mascope_backend.db import SampleBatch, SampleItem, async_session
from mascope_backend.runtime import runtime


DEFAULT_BATCH_SIZE = 5_000

_source = aliased(SampleItem)
_source_batch = aliased(SampleBatch)


def _acquisition_sample(*, same_window: bool):
    """Whether the candidate's file and polarity have an acquisition sample -
    of the candidate's own window too, where ``same_window``."""
    same = [
        _source.sample_file_id == SampleItem.sample_file_id,
        _source.polarity.is_not_distinct_from(SampleItem.polarity),
    ]
    if same_window:
        same += [
            _source.t0.is_not_distinct_from(SampleItem.t0),
            _source.t1.is_not_distinct_from(SampleItem.t1),
        ]
    return exists().where(
        _source_batch.sample_batch_id == _source.sample_batch_id,
        _source_batch.sample_batch_type == "ACQUISITION",
        *same,
    )


def _copies():
    """The items that are copies with no key yet, with their batch joined."""
    return (
        select()
        .select_from(SampleItem)
        .join(SampleBatch, SampleBatch.sample_batch_id == SampleItem.sample_batch_id)
        .where(
            SampleBatch.sample_batch_type != "ACQUISITION",
            SampleItem.origin_key.is_(None),
        )
    )


async def count_origin_keys() -> dict:
    """How many copies would be given a key, and how many not and why.

    :return: ``{"set", "edited", "no_source"}``: copies whose window is an
        acquisition sample's, copies whose file and polarity have
        acquisition samples of other windows only, and copies whose file and
        polarity have none.
    """
    unedited = _acquisition_sample(same_window=True)
    sourced = _acquisition_sample(same_window=False)
    async with async_session() as session:
        row = (
            await session.execute(
                _copies().add_columns(
                    func.count().filter(unedited),
                    func.count().filter(~unedited, sourced),
                    func.count().filter(~sourced),
                )
            )
        ).one()
    return {"set": row[0], "edited": row[1], "no_source": row[2]}


async def backfill_origin_keys(
    dry_run: bool = False, batch_size: int = DEFAULT_BATCH_SIZE
) -> dict:
    """Give a key to every copy whose window is an acquisition sample's.

    Assumes the database engine is already configured. The copies are read
    and written in pages by id, each page committed on its own, so a run
    stopped part way keeps what it wrote and the next run takes up the rest.
    Each page starts after the last id of the one before, so that a page is
    not found by walking past every copy already keyed. Two runs at once
    write the same key to a copy, which is a digest of what it holds.

    :param dry_run: When True, only count; write nothing.
    :param batch_size: Copies read and written per committed page.
    :return: The counts of :func:`count_origin_keys` before the run, the
        number written (``updated``), and ``status``.
    """
    if batch_size <= 0:
        raise ValueError("batch_size must be a positive integer")

    counts = await count_origin_keys()
    message = (
        f"{'Would set' if dry_run else 'Setting'} the origin key on "
        f"{counts['set']} copies; skipping {counts['edited'] + counts['no_source']} "
        f"({counts['edited']} with an edited window, {counts['no_source']} "
        "with no acquisition sample of their file and polarity)."
    )
    runtime.logger.info(message)
    if dry_run or counts["set"] == 0:
        return {
            **counts,
            "updated": 0,
            "status": "dry_run" if dry_run else "success",
            "message": message,
        }

    updated = 0
    after = ""
    while True:
        async with async_session() as session:
            page = (
                await session.execute(
                    _copies()
                    .add_columns(
                        SampleItem.sample_item_id,
                        SampleItem.sample_file_id,
                        SampleItem.polarity,
                        SampleItem.t0,
                        SampleItem.t1,
                    )
                    .where(
                        SampleItem.sample_item_id > after,
                        _acquisition_sample(same_window=True),
                    )
                    .order_by(SampleItem.sample_item_id)
                    .limit(batch_size)
                )
            ).all()
            if not page:
                break
            keys = values(
                column("item_id", String), column("key", String), name="keys"
            ).data(
                [
                    (item_id, origin_key(sample_file_id, polarity, t0, t1))
                    for item_id, sample_file_id, polarity, t0, t1 in page
                ]
            )
            # One statement a page, so its row count is the database's own.
            outcome = await session.execute(
                update(SampleItem)
                .where(SampleItem.sample_item_id == keys.c.item_id)
                .values(origin_key=keys.c.key)
                .execution_options(synchronize_session=False)
            )
            await session.commit()
        updated += outcome.rowcount
        after = page[-1][0]
        runtime.logger.info(f"Set the origin key on {updated} copies so far.")

    return {
        **counts,
        "updated": updated,
        "status": "success",
        "message": f"Set the origin key on {updated} copies.",
    }
