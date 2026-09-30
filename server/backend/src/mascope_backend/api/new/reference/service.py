"""Backend read path over the mirrored reference compounds.

Thin wrappers that bind the ``mascope_reference`` query interface to the app's
async session. The query logic (canonicalization, active-version filtering,
mass windows) lives in the library; this module only supplies a session and
returns plain dicts for the API/notification layers.
"""

from dataclasses import asdict

from mascope_backend.db import async_session
from mascope_reference import annotate_formulas as _annotate_formulas
from mascope_reference import by_mass_window as _by_mass_window
from mascope_reference import known_listings as _known_listings
from mascope_reference.dedup import collapse_by_inchikey
from mascope_reference.record import ReferenceRecord
from mascope_tools.composition.known_window import KnownWindow


def _to_dict(record: ReferenceRecord) -> dict:
    """Serialize a reference record for API responses."""
    return record.model_dump()


async def annotate_formulas(
    formulas: list[str], collapse: bool = True
) -> dict[str, list[dict]]:
    """Look up known compounds for many formulas in one indexed query.

    By default the per-source records for each formula are collapsed on
    InChIKey into one identity per compound (contributing sources preserved in
    ``xrefs['sources']``), which is what an analyst wants to read. Pass
    ``collapse=False`` to keep the raw one-row-per-(compound, source) records -
    needed for license-aware filtering.

    :param formulas: Assigned/neutral formulas to annotate (any notation).
    :param collapse: Collapse each formula's records on InChIKey. Defaults True.
    :return: Mapping of each input formula to its known-compound records.
    """
    async with async_session() as session:
        annotated = await _annotate_formulas(session, formulas)
    return {
        formula: [
            _to_dict(record)
            for record in (collapse_by_inchikey(records) if collapse else records)
        ]
        for formula, records in annotated.items()
    }


async def by_mass_window(mz: float, ppm: float) -> list[dict]:
    """Return known compounds within ``ppm`` of neutral mass ``mz``.

    :param mz: Neutral monoisotopic mass to search around.
    :param ppm: Half-width of the window in parts per million.
    :return: Matching known-compound records ordered by mass.
    """
    async with async_session() as session:
        records = await _by_mass_window(session, mz, ppm)
    return [_to_dict(record) for record in records]


async def known_listings(
    formulas: list[str],
    *,
    licenses: list[str] | None = None,
    ceiling: KnownWindow | None = None,
    polarity: str | None = None,
) -> dict[str, dict]:
    """What the reference lists name each formula as, where a run would match it.

    Stage A's own scope over a handful of formulas
    (:func:`mascope_reference.known.known_listings`): neutral records of the
    sample's polarity, inside each source's window under the ceiling, radicals
    only where a source allows them. The structure columns are not read.

    :param formulas: Neutral formulas, in any notation.
    :param licenses: Keep only records under these licences; None keeps all.
    :param ceiling: The chemistry context's ceiling, or None for none.
    :param polarity: ``positive`` or ``negative``, or None for any.
    :return: Per formula a list names, its identities as dicts and the count of
        records that name it, the ones past the identity cap included.
    """
    async with async_session() as session:
        listings = await _known_listings(
            session,
            formulas,
            licenses=None if licenses is None else set(licenses),
            ceiling=ceiling,
            polarity=polarity,
        )
    return {
        formula: {
            "identities": [asdict(identity) for identity in listing.identities],
            "total": listing.total,
        }
        for formula, listing in listings.items()
    }
