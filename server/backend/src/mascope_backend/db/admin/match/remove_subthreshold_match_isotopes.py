"""
Database operation to remove negligible (sub-threshold) match isotopes.

Deletes ``match_isotope`` rows whose target isotope relative abundance is below the
effective isotope abundance threshold for the row's instrument. The effective
threshold honors per-ion overrides stored in ``target_ion.filter_params`` (keyed by
instrument name) and falls back to the instrument default, mirroring the go-forward
match-time filter in ``fetch_sample_unmatched_target_isotopes``.

By default the operation is lossless: if any matched (match_score > 0) row would be
deleted it aborts without changing anything, so higher-level aggregates
(match_ion / match_compound / ...) remain valid and need no re-aggregation. Pass
``allow_matched_loss=True`` to override (aggregates would then need recomputation).

Entry Points:
- Async: `remove_subthreshold_match_isotopes()` for async callers
- CLI:   `mascope dev/prod db script run remove_subthreshold_match_isotopes`
"""

import asyncio

from sqlalchemy import text

from mascope_backend.api.new.match.params.lib import instrument_default_match_params
from mascope_backend.db import async_session
from mascope_backend.runtime import runtime


# Shared predicate: a match_isotope row is sub-threshold when its target isotope's
# relative abundance is below the effective (ion-override-or-default) threshold.
_SUBTHRESHOLD_FROM_WHERE = """
    FROM match_isotope mi
    JOIN sample_item si ON si.sample_item_id = mi.sample_item_id
    JOIN sample_file sf ON sf.sample_file_id = si.sample_file_id
    JOIN target_isotope ti ON ti.target_isotope_id = mi.target_isotope_id
    JOIN target_ion tion ON tion.target_ion_id = ti.target_ion_id
    WHERE sf.instrument = :instrument
      AND ti.relative_abundance < COALESCE(
            (tion.filter_params -> :instrument ->> 'isotope_abundance_threshold')
                ::float8,
            :default_threshold
      )
"""


async def remove_subthreshold_match_isotopes(
    allow_matched_loss: bool = False,
    dry_run: bool = False,
) -> dict:
    """
    Remove negligible match isotopes below the effective abundance threshold.

    Assumes the database engine is already configured.

    :param allow_matched_loss: When False (default), abort without deleting if any
        matched (match_score > 0) row would be removed. When True, delete regardless
        (higher-level aggregates would then require recomputation).
    :param dry_run: When True, only report what would be deleted; make no changes.
    :return: Summary with per-instrument counts and the action taken.
    :rtype: dict
    """
    async with async_session() as session:
        instruments = (
            await session.execute(
                text("SELECT DISTINCT instrument, instrument_type FROM sample_file")
            )
        ).all()

        per_instrument: list[dict] = []
        total_subthreshold = 0
        total_matched = 0
        skipped_instruments: list[str] = []

        for instrument, instrument_type in instruments:
            try:
                params = instrument_default_match_params(
                    instrument, instrument_type=instrument_type
                )
            except ValueError:
                # resolve_instrument_type raises on unrecognized instrument names.
                params = None
            if params is None:
                # Unknown instrument type: no defined threshold, leave untouched.
                skipped_instruments.append(instrument)
                continue

            bind = {
                "instrument": instrument,
                "default_threshold": params.isotope_abundance_threshold,
            }
            row = (
                await session.execute(
                    text(
                        "SELECT COUNT(*) AS total, "
                        "COUNT(*) FILTER (WHERE mi.match_score > 0) AS matched "
                        + _SUBTHRESHOLD_FROM_WHERE
                    ),
                    bind,
                )
            ).one()
            per_instrument.append(
                {
                    "instrument": instrument,
                    "threshold": params.isotope_abundance_threshold,
                    "subthreshold_rows": int(row.total),
                    "matched_rows": int(row.matched),
                }
            )
            total_subthreshold += int(row.total)
            total_matched += int(row.matched)

        # Lossless safety check: refuse to delete matched rows unless allowed.
        if total_matched > 0 and not allow_matched_loss:
            message = (
                f"Aborted: {total_matched} matched (score > 0) rows would be deleted. "
                "Re-run with allow_matched_loss=True to proceed (aggregates would then "
                "need recomputation)."
            )
            runtime.logger.warning(message)
            return {
                "status": "aborted",
                "message": message,
                "deleted": 0,
                "total_subthreshold": total_subthreshold,
                "total_matched": total_matched,
                "per_instrument": per_instrument,
                "skipped_instruments": skipped_instruments,
            }

        deleted = 0
        if not dry_run and total_subthreshold > 0:
            for entry in per_instrument:
                if entry["subthreshold_rows"] == 0:
                    continue
                result = await session.execute(
                    text(
                        "DELETE FROM match_isotope mi "
                        "USING sample_item si, sample_file sf, target_isotope ti, "
                        "target_ion tion "
                        "WHERE mi.sample_item_id = si.sample_item_id "
                        "AND si.sample_file_id = sf.sample_file_id "
                        "AND ti.target_isotope_id = mi.target_isotope_id "
                        "AND tion.target_ion_id = ti.target_ion_id "
                        "AND sf.instrument = :instrument "
                        "AND ti.relative_abundance < COALESCE("
                        "(tion.filter_params -> :instrument ->> "
                        "'isotope_abundance_threshold')::float8, :default_threshold)"
                    ),
                    {
                        "instrument": entry["instrument"],
                        "default_threshold": entry["threshold"],
                    },
                )
                deleted += result.rowcount or 0
            await session.commit()

    status = "dry_run" if dry_run else "success"
    message = (
        f"{'Would delete' if dry_run else 'Deleted'} {total_subthreshold} "
        f"sub-threshold match isotopes ({total_matched} matched) across "
        f"{len(per_instrument)} instrument(s)."
    )
    if skipped_instruments:
        message += f" Skipped unknown instruments: {skipped_instruments}."
    runtime.logger.info(message)

    return {
        "status": status,
        "message": message,
        "deleted": deleted,
        "total_subthreshold": total_subthreshold,
        "total_matched": total_matched,
        "per_instrument": per_instrument,
        "skipped_instruments": skipped_instruments,
    }


def run_remove_subthreshold_match_isotopes(
    allow_matched_loss: bool = False,
    dry_run: bool = False,
) -> dict:
    """
    Synchronous entry point for removing sub-threshold match isotopes.

    :param allow_matched_loss: Allow deletion of matched rows (see async variant).
    :param dry_run: Report only; make no changes.
    :return: Operation summary.
    :rtype: dict
    """
    return asyncio.run(
        remove_subthreshold_match_isotopes(
            allow_matched_loss=allow_matched_loss,
            dry_run=dry_run,
        )
    )
