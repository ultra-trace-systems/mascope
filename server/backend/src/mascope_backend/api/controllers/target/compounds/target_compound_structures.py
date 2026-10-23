"""
The structures a target compound could have: the compound editor's *Find
structure*.

A target compound's InChIKey, InChI and SMILES say what the user's record
stands for (``docs/dev/identification_claims.md``, section 4.3). Rather than
paste an identifier, possibly of the wrong isomer, a user picks one of the
active reference rows whose formula is the compound's, and the editor fills
the CAS number and the structure from it. Picking stores nothing here: the
pick is saved through the compound update, where the InChI is checked against
the formula as for any other (``target_compound_identity``).
"""

from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.db import async_session
from mascope_reference import (
    StructureCandidate,
    structures_by_formula,
    xref_cas_number,
)
from mascope_reference.query import STRUCTURE_PAGE_SIZE


def _structure(candidate: StructureCandidate) -> dict:
    """One reference row as the route lists it."""
    record = candidate.record
    return {
        "name": record.name,
        "inchikey": record.inchikey,
        "inchi": record.inchi,
        "smiles": record.smiles,
        "cas_number": xref_cas_number(record.xrefs),
        "source": record.source,
        "source_native_id": record.source_native_id,
        "license": record.license,
        "cas_match": candidate.cas_match,
        "name_match": candidate.name_match,
    }


@api_controller()
async def find_target_compound_structures(
    formula: str,
    cas_number: str | None = None,
    name: str | None = None,
    page: int = 0,
    limit: int = STRUCTURE_PAGE_SIZE,
) -> dict:
    """List the active reference rows that offer a structure for ``formula``.

    The rows recording the compound's CAS number come first, then the ones of
    its name; see :func:`mascope_reference.structures_by_formula`.

    :param formula: The compound's formula, in any notation.
    :param cas_number: The compound's CAS number, to rank on.
    :param name: The compound's name, to rank on.
    :param page: The page, counted from 0.
    :param limit: Rows per page.
    :return: ``message``, ``results`` (the rows on every page), ``formula``
        (the canonical formula, None for one no reference row can have) and
        ``data``, one dict per row: ``name``, ``inchikey``, ``inchi``,
        ``smiles``, ``cas_number`` (from the row's cross-references),
        ``source``, ``source_native_id``, ``license``, ``cas_match`` and
        ``name_match``.
    :rtype: dict
    """
    async with async_session() as session:
        found = await structures_by_formula(
            session,
            formula,
            cas_number=cas_number,
            name=name,
            limit=limit,
            offset=page * limit,
        )
    if found.total:
        message = (
            f"{found.total} reference compound(s) of formula {found.formula} "
            "have a structure."
        )
    else:
        message = (
            f"No active reference list has a structure for the formula '{formula}'."
        )
    return {
        "message": message,
        "results": found.total,
        "formula": found.formula,
        "data": [_structure(candidate) for candidate in found.candidates],
    }
