"""
Maintenance script to backfill instrument_function_id for sample files where it is None.

For each affected sample file, the script uses the fallback lookup that used to be in
fetch_instrument_config_by_filename (method_file + instrument, or instrument alone)
to find the most recent matching instrument config, creates a new InstrumentFunction
row (copy) with a fresh ID, and assigns it to the sample file.

Usage:
    mascope dev db script run populate_none_instrument_function_ids
    mascope prod db script run populate_none_instrument_function_ids

Date: 2026-04-01
Issue: #1299
"""

import asyncio

from sqlalchemy import desc, select

from mascope_backend.db import (
    InstrumentFunction,
    SampleFile,
    async_session,
    configure_database_engine,
)
from mascope_backend.db.id import gen_id
from mascope_backend.runtime import runtime


async def populate_none_instrument_function_ids() -> dict:
    """Backfill instrument_function_id for all sample files where it is None.

    For each affected sample file the most recent matching InstrumentFunction is
    located via the method_file + instrument fallback, a fresh copy is inserted,
    and the new ID is assigned to the sample file.

    Assumes the database engine is already configured.

    :return: Summary counts {"total": int, "updated": int, "skipped": int}
    :rtype: dict
    """
    async with async_session() as session:
        result = await session.execute(
            select(SampleFile).where(SampleFile.instrument_function_id.is_(None))
        )
        affected = list(result.scalars().all())
        total = len(affected)

        runtime.logger.info(
            f"Found {total} sample file(s) with instrument_function_id=None"
        )

        if total == 0:
            return {"total": 0, "updated": 0, "skipped": 0}

        updated = 0
        skipped = 0

        for sf in affected:
            if sf.method_file:
                stmt = (
                    select(InstrumentFunction)
                    .where(
                        InstrumentFunction.method_file == sf.method_file,
                        InstrumentFunction.instrument == sf.instrument,
                    )
                    .order_by(desc(InstrumentFunction.datetime_utc))
                    .limit(1)
                )
            else:
                stmt = (
                    select(InstrumentFunction)
                    .where(InstrumentFunction.instrument == sf.instrument)
                    .order_by(desc(InstrumentFunction.datetime_utc))
                    .limit(1)
                )

            source = (await session.execute(stmt)).scalar_one_or_none()

            if source is None:
                runtime.logger.warning(
                    f"No instrument config found for instrument={sf.instrument!r}, "
                    f"method_file={sf.method_file!r} — skipping {sf.filename!r}"
                )
                skipped += 1
                continue

            new_id = gen_id(32)
            new_config = InstrumentFunction(
                instrument_function_id=new_id,
                instrument=source.instrument,
                instrument_id=source.instrument_id,
                method_file=source.method_file,
                datetime_utc=source.datetime_utc,
                peakshape=source.peakshape,
                resolution_function=source.resolution_function,
            )
            session.add(new_config)
            await session.flush()

            sf.instrument_function_id = new_id
            updated += 1

            runtime.logger.info(
                f"Created config {new_id!r} (source: {source.instrument_function_id!r}), "
                f"assigned to {sf.filename!r}"
                f" [instrument={sf.instrument!r}, method_file={sf.method_file!r}]"
            )

        await session.commit()

    return {"total": total, "updated": updated, "skipped": skipped}


async def run() -> None:
    """Initialise the database and run the backfill."""
    await configure_database_engine()

    result = await populate_none_instrument_function_ids()

    runtime.logger.info("=" * 80)
    runtime.logger.info("POPULATE INSTRUMENT_FUNCTION_ID COMPLETE")
    runtime.logger.info(
        f"Total sample files with None instrument_function_id : {result['total']}"
    )
    runtime.logger.info(
        f"Updated                                              : {result['updated']}"
    )
    runtime.logger.info(
        f"Skipped (no matching config found)                   : {result['skipped']}"
    )
    runtime.logger.info("=" * 80)


def main() -> None:
    """Entry point for the backfill script."""
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        runtime.logger.info("Cancelled by user (Ctrl+C)")
    except Exception as e:
        runtime.logger.exception(f"Script failed: {e}")
        raise


if __name__ == "__main__":
    main()
