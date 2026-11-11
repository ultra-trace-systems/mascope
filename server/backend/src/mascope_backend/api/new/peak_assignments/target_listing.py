"""What the target compounds a page of rows was matched to declare.

A row the run matched from the target library names its compound
(``target_compound_id``); the ledgers show what that compound declares, its
name and InChIKey, as a candidate beside what a list calls the formula
(:func:`~mascope_backend.api.new.peak_assignments.listing.target_candidate`).
Read when the row is read rather than recorded by the run, so a compound given
its structure after the run reads with it. Whether a list carries the key is
asked of the lists the deployment matches against
(:func:`~mascope_backend.api.new.reference.licenses.reference_license_gate`).
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select

from mascope_backend.api.new.peak_assignments.listing import (
    target_candidate,
    with_target,
)
from mascope_backend.api.new.reference.licenses import reference_license_gate
from mascope_backend.db import TargetCompound
from mascope_reference import listed_inchikeys


async def target_candidates(
    session, target_compound_ids: Iterable[object]
) -> dict[str, dict | None]:
    """The candidate each target compound stands for, by its id.

    Two indexed queries for the whole page: the compounds, then which of
    their keys a list carries.

    :param session: An open async session.
    :param target_compound_ids: The ids the rows name; None and repeats are
        dropped.
    :return: Each id that names a compound mapped to its candidate, None for
        a compound that declares neither a name nor a key; an id whose compound
        is gone is left out.
    """
    ids = {str(value) for value in target_compound_ids if value}
    if not ids:
        return {}
    compounds = (
        await session.execute(
            select(
                TargetCompound.target_compound_id,
                TargetCompound.target_compound_name,
                TargetCompound.inchikey,
            ).where(TargetCompound.target_compound_id.in_(ids))
        )
    ).all()
    licenses = reference_license_gate()
    listed = await listed_inchikeys(
        session,
        [compound.inchikey for compound in compounds if compound.inchikey],
        licenses=None if licenses is None else set(licenses),
    )
    return {
        compound.target_compound_id: target_candidate(
            compound.target_compound_name, compound.inchikey, listed
        )
        for compound in compounds
    }


async def with_target_listings(
    session,
    records: list[dict],
    target_of=lambda record: record.get("target_compound_id"),
) -> list[dict]:
    """Put each record's target candidate on its ``reference_listing``.

    :param session: An open async session.
    :param records: Rows carrying ``reference_listing``, modified in place.
    :param target_of: The target compound id a record names, or None.
    :return: ``records``.
    """
    targets = await target_candidates(session, (target_of(r) for r in records))
    if not targets:
        return records
    for record in records:
        target = targets.get(str(target_of(record) or ""))
        if target is not None:
            record["reference_listing"] = with_target(
                record.get("reference_listing"), target
            )
    return records
