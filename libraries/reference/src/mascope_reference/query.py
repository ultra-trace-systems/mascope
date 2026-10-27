"""Indexed read path over the mirrored reference compounds.

The two entry points - :func:`by_formula` and :func:`by_mass_window` - are the
only way the backend reads reference data, so the mirror can be reindexed or
reshaped without touching callers. Both return normalized
:class:`ReferenceRecord` instances and only ever see the *active* version of
each source, so a re-synced source does not double annotations.
:func:`structures_by_formula` pages through the same rows for a person
choosing a compound's structure among them. :func:`active_sources` and
:func:`source_compounds` say which sources are loaded and what one of them
holds, for a person reading the lists rather than an annotation.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import case, func, or_, select
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


#: The cross-reference keys a CAS number is recorded under: CompTox's and
#: NORMAN's, then a custom list's.
CAS_XREF_KEYS = ("casrn", "cas")

#: Rows per page of :func:`structures_by_formula` by default, and at most.
STRUCTURE_PAGE_SIZE = 20
STRUCTURE_PAGE_CAP = 100

#: Rows per page of :func:`source_compounds` by default, and at most.
SOURCE_PAGE_SIZE = 100
SOURCE_PAGE_CAP = 1000


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


def xref_cas_number(xrefs: dict | None) -> str | None:
    """The CAS number a reference row's cross-references record, or None.

    :param xrefs: The row's ``xrefs``.
    :return: The first non-empty one under :data:`CAS_XREF_KEYS`, stripped.
    """
    for key in CAS_XREF_KEYS:
        value = (xrefs or {}).get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


@dataclass(frozen=True)
class StructureCandidate:
    """One reference row offered as the structure of a compound.

    :param record: The row.
    :param cas_match: Its CAS number is the one asked about, dashes aside.
    :param name_match: Its name is the one asked about, case aside.
    """

    record: ReferenceRecord
    cas_match: bool = False
    name_match: bool = False


@dataclass
class StructurePage:
    """One page of :func:`structures_by_formula`.

    :param candidates: The page's rows, in order.
    :param total: How many rows qualify, on every page.
    :param formula: The canonical formula they share, or None when the one
        asked about is not a formula any reference row can have.
    """

    candidates: list[StructureCandidate] = field(default_factory=list)
    total: int = 0
    formula: str | None = None


def _cas_is(digits: str):
    """SQL: the row records the CAS number ``digits`` under a CAS key.

    A stored number is compared without its dashes and surrounding blanks,
    so ``50-99-7`` and ``50997`` agree; both are spellings sources use.
    """
    return or_(
        *(
            func.replace(
                func.trim(reference_compound.c.xrefs[key].as_string()), "-", ""
            )
            == digits
            for key in CAS_XREF_KEYS
        )
    )


async def structures_by_formula(
    session: AsyncSession,
    formula: str,
    *,
    cas_number: str | None = None,
    name: str | None = None,
    limit: int = STRUCTURE_PAGE_SIZE,
    offset: int = 0,
) -> StructurePage:
    """The active reference rows that offer a structure for ``formula``.

    A row qualifies when its canonical formula is ``formula``'s and it carries
    a structure: an InChIKey, an InChI or a SMILES. One row per compound per
    source, not collapsed on InChIKey, since each row has its own licence.
    The rows recording ``cas_number`` come first, then those named ``name``,
    then the stable order, which is total, so a page boundary never moves
    between two reads of the same mirror.

    :param session: Active async session.
    :param formula: The compound's formula, in any notation.
    :param cas_number: The compound's CAS number, if any; compared as digits.
    :param name: The compound's name, if any; compared trimmed, case aside.
    :param limit: Rows per page, 1 to :data:`STRUCTURE_PAGE_CAP`.
    :param offset: Rows to skip.
    :return: The page, the total and the canonical formula.
    :raises ValueError: On a ``limit`` outside its range or a negative
        ``offset``.
    """
    if not 1 <= limit <= STRUCTURE_PAGE_CAP:
        raise ValueError(f"limit must be 1 to {STRUCTURE_PAGE_CAP}, not {limit}")
    if offset < 0:
        raise ValueError(f"offset must not be negative, not {offset}")
    canon = canonical_formula(formula)
    if canon is None:
        return StructurePage()

    qualifies = (
        reference_compound.c.formula == canon,
        or_(
            reference_compound.c.inchikey.is_not(None),
            reference_compound.c.inchi.is_not(None),
            reference_compound.c.smiles.is_not(None),
        ),
    )
    total = await session.scalar(
        select(func.count()).select_from(
            active_join(reference_compound.c.reference_compound_id)
            .where(*qualifies)
            .subquery()
        )
    )

    # A rank of 0 is a match. Only what was asked about is ranked on.
    ranks = []
    digits = re.sub(r"\D", "", cas_number or "")
    if digits:
        ranks.append(case((_cas_is(digits), 0), else_=1).label("cas_rank"))
    wanted = (name or "").strip().lower()
    if wanted:
        ranks.append(
            case(
                (func.lower(reference_compound.c.name) == wanted, 0),
                else_=1,
            ).label("name_rank")
        )
    stmt = (
        active_join(*_COMPOUND_COLUMNS, *ranks)
        .where(*qualifies)
        .order_by(
            *ranks,
            reference_source.c.name,
            reference_compound.c.source_native_id,
            reference_compound.c.reference_compound_id,
        )
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(stmt)).all()
    return StructurePage(
        candidates=[
            StructureCandidate(
                record=_row_to_record(row),
                cas_match=getattr(row, "cas_rank", 1) == 0,
                name_match=getattr(row, "name_rank", 1) == 0,
            )
            for row in rows
        ],
        total=total or 0,
        formula=canon,
    )


@dataclass(frozen=True)
class SourceRow:
    """One loaded source version, as its ``reference_source`` row records it.

    :param name: The source's name; a shipped list's id.
    :param version: The loaded version.
    :param license: The source's licence.
    :param record_count: How many compounds the load wrote.
    :param ingested_at: When it was loaded.
    :param known_window: The window its compounds may be matched in, as JSON
        whose null fields are unbounded; None for unbounded.
    :param allow_radicals: Whether its radicals may be matched.
    :param polarity: The polarity it is matched in; None for both.
    """

    name: str
    version: str
    license: str | None
    record_count: int | None
    ingested_at: datetime | None
    known_window: dict | None
    allow_radicals: bool | None
    polarity: str | None


@dataclass
class SourcePage:
    """One page of :func:`source_compounds`.

    :param version: The version the page is of.
    :param records: The page's rows, in order.
    :param total: How many rows the version holds, on every page.
    """

    version: str
    records: list[ReferenceRecord] = field(default_factory=list)
    total: int = 0


_SOURCE_COLUMNS = (
    reference_source.c.name,
    reference_source.c.version,
    reference_source.c.license,
    reference_source.c.record_count,
    reference_source.c.ingested_at,
    reference_source.c.known_window,
    reference_source.c.allow_radicals,
    reference_source.c.polarity,
)


async def active_sources(session: AsyncSession) -> list[SourceRow]:
    """The active version of every loaded source, by name.

    :param session: Active async session.
    :return: One row per active source version, ordered by name and version.
    """
    stmt = (
        select(*_SOURCE_COLUMNS)
        .where(reference_source.c.is_active.is_(True))
        .order_by(
            reference_source.c.name,
            reference_source.c.version,
            reference_source.c.reference_source_id,
        )
    )
    return [SourceRow(**row._mapping) for row in (await session.execute(stmt)).all()]


async def source_compounds(
    session: AsyncSession,
    name: str,
    *,
    version: str | None = None,
    limit: int = SOURCE_PAGE_SIZE,
    offset: int = 0,
) -> SourcePage | None:
    """One page of the compounds a source version holds.

    The version asked for is read whether it is active or not, so a person can
    look at a load before or after it was replaced; without one, the active
    version is. The rows come by mass, then formula and native id, which is a
    total order within one version, so a page boundary never moves between two
    reads of the same load.

    :param session: Active async session.
    :param name: The source's name.
    :param version: The version to read; None for the active one.
    :param limit: Rows per page, 1 to :data:`SOURCE_PAGE_CAP`.
    :param offset: Rows to skip.
    :return: The page, or None when the source has no such version, or no
        active one when none is asked for.
    :raises ValueError: On a ``limit`` outside its range or a negative
        ``offset``.
    """
    if not 1 <= limit <= SOURCE_PAGE_CAP:
        raise ValueError(f"limit must be 1 to {SOURCE_PAGE_CAP}, not {limit}")
    if offset < 0:
        raise ValueError(f"offset must not be negative, not {offset}")
    which = select(reference_source.c.reference_source_id, reference_source.c.version)
    which = which.where(reference_source.c.name == name)
    if version is None:
        which = which.where(reference_source.c.is_active.is_(True))
    else:
        which = which.where(reference_source.c.version == version)
    # Nothing keeps a name and version unique, so a reloaded version reads as
    # its newest load.
    found = (
        await session.execute(
            which.order_by(reference_source.c.reference_source_id.desc()).limit(1)
        )
    ).first()
    if found is None:
        return None

    of_version = reference_compound.c.reference_source_id == found.reference_source_id
    total = await session.scalar(
        select(func.count()).select_from(reference_compound).where(of_version)
    )
    stmt = (
        select(*_COMPOUND_COLUMNS, reference_source.c.name.label("source_name"))
        .select_from(
            reference_compound.join(
                reference_source,
                reference_compound.c.reference_source_id
                == reference_source.c.reference_source_id,
            )
        )
        .where(of_version)
        .order_by(
            reference_compound.c.monoisotopic_mass,
            reference_compound.c.formula,
            reference_compound.c.source_native_id,
            reference_compound.c.reference_compound_id,
        )
        .limit(limit)
        .offset(offset)
    )
    return SourcePage(
        version=found.version,
        records=[_row_to_record(row) for row in (await session.execute(stmt)).all()],
        total=total or 0,
    )
