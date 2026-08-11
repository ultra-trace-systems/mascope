"""Indexed read path over the mirrored reference compounds.

The two entry points - :func:`by_formula` and :func:`by_mass_window` - are the
only way the backend reads reference data, so the mirror can be reindexed or
reshaped without touching callers. Both return normalized
:class:`ReferenceRecord` instances and only ever see the *active* version of
each source, so a re-synced source does not double annotations.
"""

from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mascope_reference.normalize import canonical_formula
from mascope_reference.record import ReferenceRecord
from mascope_reference.schema import reference_compound, reference_source


# The compound columns a query selects, in the order they are read back.
_COMPOUND_COLUMNS = (
    reference_compound.c.formula,
    reference_compound.c.monoisotopic_mass,
    reference_compound.c.inchikey,
    reference_compound.c.name,
    reference_compound.c.smiles,
    reference_compound.c.inchi,
    reference_compound.c.source_native_id,
    reference_compound.c.xrefs,
    reference_compound.c.license,
)


# A total order over reference rows. (source, source_native_id) is unique within
# a source, so this never leaves two rows tied and therefore free to swap places
# between two runs of the same query.
_STABLE_ORDER = (
    reference_compound.c.formula,
    reference_source.c.name,
    reference_compound.c.source_native_id,
)


def active_join(*columns):
    """SELECT of the given compound columns + source name, active sources only.

    Callers that do not need the structure columns should say so: ``smiles`` and
    ``inchi`` are unbounded text, and a bulk read that selects them pulls tens of
    megabytes it never looks at. Defaults to every compound column.

    :param columns: Compound columns to select; all of them when omitted.
    :return: A SELECT restricted to active sources, ready to be filtered further.
    """
    return (
        select(
            *(columns or _COMPOUND_COLUMNS),
            reference_source.c.name.label("source_name"),
        )
        .select_from(
            reference_compound.join(
                reference_source,
                reference_compound.c.reference_source_id
                == reference_source.c.reference_source_id,
            )
        )
        .where(reference_source.c.is_active.is_(True))
    )


def _active_join():
    """SELECT of every compound column + source name, active sources only."""
    return active_join()


def _row_to_record(row: Any) -> ReferenceRecord:
    """Map a result row (compound columns + source_name) to a record."""
    return ReferenceRecord(
        formula=row.formula,
        monoisotopic_mass=row.monoisotopic_mass,
        inchikey=row.inchikey,
        name=row.name,
        smiles=row.smiles,
        inchi=row.inchi,
        source=row.source_name,
        source_native_id=row.source_native_id,
        xrefs=row.xrefs or {},
        license=row.license,
    )


def _mass_bounds(mz: float, ppm: float) -> tuple[float, float]:
    """Symmetric absolute mass window for a ppm tolerance around ``mz``."""
    delta = abs(mz) * ppm * 1e-6
    return mz - delta, mz + delta


async def annotate_formulas(
    session: AsyncSession,
    formulas: Iterable[str],
) -> dict[str, list[ReferenceRecord]]:
    """Batch-annotate many formulas in a single indexed query.

    Canonicalizes each input formula, looks them all up with one ``IN`` query,
    and buckets the results back under the *input* formula string so callers can
    attach identities without re-canonicalizing. Formulas that canonicalize to
    the same key share results; unmatched formulas map to an empty list.

    :param session: Active async session.
    :param formulas: Formula strings to annotate (any notation).
    :return: Mapping of each input formula to its matching records.
    """
    formulas = list(formulas)
    # Map canonical form -> the input strings that produced it, so results can be
    # handed back keyed the way the caller asked. The inputs are de-duplicated
    # first: the result dict is keyed by the raw string, so a formula appearing
    # twice in the input would otherwise append every matching record twice into
    # the one list behind that key. Callers repeat formulas as a matter of course
    # - the composition search passes one entry per (composition, ionization
    # mechanism), so the same neutral formula arrives once per adduct.
    canon_to_inputs: dict[str, list[str]] = {}
    for raw in dict.fromkeys(formulas):
        canon = canonical_formula(raw)
        if canon is not None:
            canon_to_inputs.setdefault(canon, []).append(raw)

    result: dict[str, list[ReferenceRecord]] = {raw: [] for raw in formulas}
    if not canon_to_inputs:
        return result

    # Ordered so the same query returns the same records in the same order on
    # every call: without it the identities attached to an assignment - and which
    # of them survive de-duplication or a per-formula cap - vary run to run,
    # which defeats the point of pinning a source to a version.
    stmt = (
        _active_join()
        .where(reference_compound.c.formula.in_(canon_to_inputs.keys()))
        .order_by(*_STABLE_ORDER)
    )
    for row in (await session.execute(stmt)).all():
        record = _row_to_record(row)
        for raw in canon_to_inputs.get(row.formula, []):
            result[raw].append(record)
    return result


async def by_formula(
    session: AsyncSession,
    formula: str,
) -> list[ReferenceRecord]:
    """Return every active reference compound whose canonical formula matches.

    :param session: Active async session.
    :param formula: Formula to look up, in any notation.
    :return: Matching records across all active sources.
    """
    canon = canonical_formula(formula)
    if canon is None:
        return []
    return (await annotate_formulas(session, [formula]))[formula]


async def by_mass_window(
    session: AsyncSession,
    mz: float,
    ppm: float,
) -> list[ReferenceRecord]:
    """Return active reference compounds within ``ppm`` of neutral mass ``mz``.

    :param session: Active async session.
    :param mz: Center mass (neutral monoisotopic mass to search around).
    :param ppm: Half-width of the window in parts per million.
    :return: Matching records ordered by ascending mass.
    """
    low, high = _mass_bounds(mz, ppm)
    stmt = (
        _active_join()
        .where(reference_compound.c.monoisotopic_mass.between(low, high))
        # Mass first, as the caller expects, then the stable tiebreak: isomers
        # share a mass exactly, so mass alone leaves them free to swap order.
        .order_by(reference_compound.c.monoisotopic_mass, *_STABLE_ORDER)
    )
    return [_row_to_record(row) for row in (await session.execute(stmt)).all()]
