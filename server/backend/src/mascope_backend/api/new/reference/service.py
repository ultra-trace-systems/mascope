"""Backend read path over the mirrored reference compounds.

Thin wrappers that bind the ``mascope_reference`` query interface to the app's
async session. The query logic (canonicalization, active-version filtering,
mass windows) lives in the library; this module only supplies a session and
returns plain dicts for the API/notification layers. :func:`list_sources` and
:func:`source_compounds` read the mirror for a person rather than for an
annotation: which sources are loaded, and what one of them holds.
"""

from dataclasses import asdict
from functools import cache

from mascope_backend.api.new.reference.licenses import reference_license_gate
from mascope_backend.db import async_session
from mascope_reference import active_sources as _active_sources
from mascope_reference import annotate_formulas as _annotate_formulas
from mascope_reference import by_mass_window as _by_mass_window
from mascope_reference import catalogue
from mascope_reference import known_listings as _known_listings
from mascope_reference import source_compounds as _source_compounds
from mascope_reference.dedup import collapse_by_inchikey
from mascope_reference.record import ReferenceRecord
from mascope_tools.composition.known_window import KnownWindow


#: The keys of a list-level reference, in the order the sources route gives
#: them.
_REFERENCE_KEYS = ("citation", "doi", "isbn", "note")


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


@cache
def shipped_labels() -> dict[str, str]:
    """The label of every list Mascope ships, by the source name it loads as.

    Read once per process: the lists are files inside the installed package and
    change only with an upgrade, which restarts the process.

    :return: Each shipped list's id mapped to its label; a list without one is
        left out.
    """
    return {
        peak_list.id: peak_list.label for peak_list in catalogue() if peak_list.label
    }


@cache
def shipped_references() -> dict[str, tuple[dict, ...]]:
    """The publications every list Mascope ships cites, by its source name.

    Read once per process, as :func:`shipped_labels` is. Each reference keeps
    the keys a list file may give one (``citation``, ``doi``, ``isbn``,
    ``note``), None where the file leaves one out.

    :return: Each shipped list's id mapped to its references, in the file's
        order.
    """
    return {
        peak_list.id: tuple(
            {key: reference.get(key) for key in _REFERENCE_KEYS}
            for reference in peak_list.references
        )
        for peak_list in catalogue()
    }


async def list_sources() -> dict:
    """The active reference sources, one row each, sorted by label.

    A shipped list is labelled as its file labels it, any other source by its
    name. ``references`` are the publications a shipped list cites, and empty
    for any other source: the database keeps no citation for a list loaded
    from elsewhere. ``admitted`` says whether the deployment's licence gate
    (:func:`reference_license_gate`) lets Stage A match the source: true for
    every source when no gate is configured, else whether the source's licence
    is one the gate names.

    :return: ``{"data": [...], "total": N}``.
    """
    async with async_session() as session:
        rows = await _active_sources(session)
    labels = shipped_labels()
    references = shipped_references()
    gate = reference_license_gate()
    data = [
        {
            "name": row.name,
            "version": row.version,
            "label": labels.get(row.name, row.name),
            "references": [dict(ref) for ref in references.get(row.name, ())],
            "license": row.license,
            "polarity": row.polarity,
            "allow_radicals": bool(row.allow_radicals),
            "known_window": row.known_window,
            "record_count": row.record_count,
            "ingested_at": row.ingested_at,
            "admitted": gate is None or row.license in gate,
        }
        for row in rows
    ]
    data.sort(key=lambda source: (source["label"].casefold(), source["name"]))
    return {"data": data, "total": len(data)}


async def source_compounds(
    name: str, *, version: str | None, page: int, limit: int
) -> dict | None:
    """One page of the compounds a source version holds.

    :param name: The source's name.
    :param version: The version to read; None for the active one.
    :param page: Zero-based page.
    :param limit: Rows per page.
    :return: ``{"name", "version", "data": [...], "total": N}``, each row with
        its name, formula, monoisotopic mass, InChIKey, native id, the DOI the
        list cites for it and its tags; None when the source has no such
        version.
    """
    async with async_session() as session:
        found = await _source_compounds(
            session, name, version=version, limit=limit, offset=page * limit
        )
    if found is None:
        return None
    return {
        "name": name,
        "version": found.version,
        "data": [
            {
                "name": record.name,
                "formula": record.formula,
                "monoisotopic_mass": record.monoisotopic_mass,
                "inchikey": record.inchikey,
                "source_native_id": record.source_native_id,
                "reference": record.xrefs.get("reference"),
                "tags": record.xrefs.get("tags") or [],
            }
            for record in found.records
        ],
        "total": found.total,
    }
