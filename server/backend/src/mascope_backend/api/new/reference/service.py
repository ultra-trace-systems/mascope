"""Backend read path over the mirrored reference compounds.

Thin wrappers that bind the ``mascope_reference`` query interface to the app's
async session. The query logic (canonicalization, active-version filtering,
mass windows) lives in the library; this module only supplies a session and
returns plain dicts for the API/notification layers.
"""

from mascope_backend.db import async_session
from mascope_reference import annotate_formulas as _annotate_formulas
from mascope_reference import by_mass_window as _by_mass_window
from mascope_reference.dedup import collapse_by_inchikey
from mascope_reference.record import ReferenceRecord


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
