"""
Maintenance script to fix corrupted m/z range values in sample_file records
and their corresponding filestore .props files.

A bug in H5Processor.range caused the full mass axis array to be stored
instead of [first, last]. This script:

1. Queries the database for sample_file rows where range has > 2 elements.
2. Fixes the database rows to [first, last].
3. Uses the affected filenames to locate and patch the .props files.

Usage:
    mascope dev db script run fix_tofwerk_range
    mascope prod db script run fix_tofwerk_range

Date: 2026-05-25
"""

import asyncio

from sqlalchemy import text

from mascope_backend.db import async_session, configure_database_engine
from mascope_backend.runtime import runtime


# Maximum number of affected rows to log individually
_PREVIEW_LIMIT = 20

# A row the bug left: its range a list of more than two values. Nested, since
# Postgres does not promise to evaluate the parts of an AND in order, and the
# length of a range that is not a list is an error that would stop the fix.
_AFFECTED = (
    "CASE WHEN json_typeof(range) = 'array' "
    "THEN json_array_length(range) > 2 ELSE false END"
)

# The last element of an affected row's range, and whether both its ends are
# numbers: the m/z bounds are read off the two ends only when both are, as
# range_bounds and the migration that made the bounds read a range.
_LAST = "range->(json_array_length(range) - 1)"
_ENDS_ARE_NUMBERS = (
    f"json_typeof(range->0) = 'number' AND json_typeof({_LAST}) = 'number'"
)


async def _fix_database() -> dict:
    """Find and fix rows in ``sample_file`` where ``range`` has > 2 elements.

    :return: ``{"affected": int, "updated": int, "filenames": list[str]}``
    :rtype: dict
    """
    async with async_session() as session:
        # --- Count affected rows ---
        count_result = await session.execute(
            text(f"""
                SELECT COUNT(*) FROM sample_file
                WHERE {_AFFECTED}
            """)
        )
        affected = count_result.scalar()

        if affected == 0:
            runtime.logger.info("No rows with corrupted range found.")
            return {"affected": 0, "updated": 0, "filenames": []}

        runtime.logger.info(f"Total affected rows: {affected}")

        # --- Preview a subset ---
        preview_result = await session.execute(
            text(f"""
                SELECT filename,
                       json_array_length(range) AS range_len
                FROM sample_file
                WHERE {_AFFECTED}
                ORDER BY datetime
                LIMIT :lim
            """),
            {"lim": _PREVIEW_LIMIT},
        )
        for row in preview_result:
            runtime.logger.info(f"  {row.filename}: range has {row.range_len} elements")
        if affected > _PREVIEW_LIMIT:
            runtime.logger.info(f"  ... and {affected - _PREVIEW_LIMIT} more")

        # --- Collect all affected filenames for filestore fix ---
        filenames_result = await session.execute(
            text(f"""
                SELECT filename FROM sample_file
                WHERE {_AFFECTED}
            """)
        )
        filenames = [row.filename for row in filenames_result]

        # --- Fix: keep only first and last element ---
        # The bounds beside the range are its two ends, written in the same
        # statement: SQL passes by the model, which keeps them in step
        # wherever else the range is set (SampleFile.mz_min, range_bounds).
        update_result = await session.execute(
            text(f"""
                UPDATE sample_file
                SET range = (
                    SELECT json_build_array(range->0, range->(json_array_length(range) - 1))
                ),
                    mz_min = CASE WHEN {_ENDS_ARE_NUMBERS}
                        THEN (range->>0)::float8 END,
                    mz_max = CASE WHEN {_ENDS_ARE_NUMBERS}
                        THEN (range->>(json_array_length(range) - 1))::float8 END
                WHERE {_AFFECTED}
            """)
        )

        await session.commit()
        runtime.logger.info(f"Updated {update_result.rowcount} database rows.")

        return {
            "affected": affected,
            "updated": update_result.rowcount,
            "filenames": filenames,
        }


def _fix_filestore(filenames: list[str]) -> dict:
    """Patch ``.props`` files for the given sample filenames.

    Uses ``mascope_file.io.update_props`` to read, patch, and write each
    ``.props`` file consistently with the rest of the codebase.

    :param filenames: List of sample filenames whose ``.props`` may need fixing.
    :return: ``{"checked": int, "updated": int, "errors": list}``
    :rtype: dict
    """
    from mascope_file.io import read_props, update_props

    checked = 0
    updated = 0
    errors = []

    for filename in filenames:
        checked += 1
        try:
            props = read_props(filename)
        except FileNotFoundError:
            runtime.logger.warning(f"  .props not found for {filename}")
            errors.append(filename)
            continue
        except Exception as exc:
            runtime.logger.warning(f"  Cannot read .props for {filename}: {exc}")
            errors.append(filename)
            continue

        mz_range = props.get("range")
        if not isinstance(mz_range, list) or len(mz_range) <= 2:
            continue  # Already correct

        try:
            update_props(filename, {"range": [mz_range[0], mz_range[-1]]})
            updated += 1
            runtime.logger.info(f"  Fixed {filename}: {len(mz_range)} -> 2 elements")
        except Exception as exc:
            runtime.logger.warning(f"  Cannot write .props for {filename}: {exc}")
            errors.append(filename)

    runtime.logger.info(
        f"Filestore: checked={checked}, updated={updated}, errors={len(errors)}"
    )

    return {
        "checked": checked,
        "updated": updated,
        "errors": errors,
    }


async def run() -> None:
    """Initialize database and run the range fix."""
    await configure_database_engine()

    db_result = await _fix_database()
    fs_result = _fix_filestore(db_result["filenames"])

    runtime.logger.info("=" * 80)
    runtime.logger.info("FIX TOFWERK RANGE COMPLETE")
    runtime.logger.info(
        f"Database — affected: {db_result['affected']}, updated: {db_result['updated']}"
    )
    runtime.logger.info(
        f"Filestore — checked: {fs_result['checked']}, "
        f"updated: {fs_result['updated']}, errors: {len(fs_result['errors'])}"
    )
    runtime.logger.info("=" * 80)


def main() -> None:
    """Entry point for the TofWerk range fix script."""
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        runtime.logger.info("Cancelled by user (Ctrl+C)")
    except Exception:
        runtime.logger.exception("Script failed")
        raise


if __name__ == "__main__":
    main()
