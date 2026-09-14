"""Bulk known-composition provider for peak-centric Stage A.

Where :mod:`mascope_reference.query` answers "what is this formula" one lookup at
a time (annotation), this answers "give me the whole known set to match against":
the active reference compounds collapsed to **unique canonical formulas**, each
carrying the one-to-many identities that share it. This encodes the load-bearing
split from the convergence design - *matching is formula-based; identity is
one-to-many* - so Stage A pre-computes isotopologues once per formula and attaches
the (possibly several) names afterwards.

What each source contributes is bounded by what its row records
(:mod:`mascope_reference.scope`), so a curated list brings in the silicon,
phosphorus and halogen families no formula grid reaches while a mirror the size
of CompTox stays inside the atmospheric window rather than being expanded into
isotopologues per sample:

- **its window**, intersected with the chemistry context's ceiling;
- **its radical allowance**: an odd-electron formula is matched only from a
  source whose row allows radicals;
- **its polarity**: a source detected in one polarity is not matched against a
  sample measured in the other.
"""

import json
from dataclasses import dataclass, field

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from mascope_reference.peaklist import is_odd_electron
from mascope_reference.query import _STABLE_ORDER, active_join
from mascope_reference.schema import reference_compound, reference_source
from mascope_reference.scope import SourceScope
from mascope_tools.composition.known_window import KnownWindow


# Rows are consumed in batches rather than materialized in one list: this runs
# once per Stage A, and a mirror the size of CompTox is over a million rows.
_STREAM_BATCH = 2_000

# The columns an identity needs. Deliberately excludes smiles and inchi, which
# are unbounded text this provider never reads - Stage A matches on formula and
# shows a name.
_KNOWN_COLUMNS = (
    reference_compound.c.formula,
    reference_compound.c.monoisotopic_mass,
    reference_compound.c.inchikey,
    reference_compound.c.name,
    reference_compound.c.source_native_id,
    reference_compound.c.xrefs,
    reference_compound.c.license,
)

# What a row's source records about how its compounds may be matched, selected
# beside every compound row: the source table is a handful of rows, so reading
# the scope off the join costs nothing and keeps one stream in one order.
_SCOPE_COLUMNS = (
    reference_source.c.reference_source_id.label("source_id"),
    reference_source.c.known_window,
    reference_source.c.allow_radicals,
    reference_source.c.polarity,
)

# Cap identities kept per formula so provenance JSON stays bounded even when a
# formula is shared by thousands of compounds in a large mirror.
DEFAULT_MAX_IDENTITIES = 25


@dataclass(frozen=True)
class KnownIdentity:
    """One named compound backing a known formula."""

    name: str | None
    source: str
    license: str
    inchikey: str | None
    source_native_id: str
    xrefs: dict


@dataclass
class KnownComposition:
    """One unique neutral formula plus the identities that share it."""

    formula: str  # canonical Hill order
    monoisotopic_mass: float | None
    identities: list[KnownIdentity] = field(default_factory=list)


async def known_state_fingerprint(session: AsyncSession) -> tuple:
    """Cheap identity of the active reference state, for caching derived data.

    One row per active source suffices: ingest writes a new
    ``reference_source`` row per (source, version) and flips ``is_active``, and
    what a row records about how its compounds may be matched is part of the
    row's entry, so any change to what :func:`iter_known_compositions` would
    return also changes this tuple - a seed that brings a list's window up to
    date without reloading it included. An empty tuple means no active sources -
    the known set is empty without scanning ``reference_compound``.

    :param session: Active async session.
    :return: Sorted tuple of (source id, ingestion timestamp, window, radical
        allowance, polarity), one per active source.
    """
    rows = (
        await session.execute(
            select(
                reference_source.c.reference_source_id,
                reference_source.c.ingested_at,
                reference_source.c.known_window,
                reference_source.c.allow_radicals,
                reference_source.c.polarity,
            )
            .where(reference_source.c.is_active.is_(True))
            .order_by(reference_source.c.reference_source_id)
        )
    ).all()
    return tuple(
        (
            row.reference_source_id,
            row.ingested_at,
            json.dumps(row.known_window, sort_keys=True),
            bool(row.allow_radicals),
            row.polarity,
        )
        for row in rows
    )


async def iter_known_compositions(
    session: AsyncSession,
    *,
    licenses: set[str] | None = None,
    ceiling: KnownWindow | None = None,
    polarity: str | None = None,
    max_identities: int = DEFAULT_MAX_IDENTITIES,
) -> list[KnownComposition]:
    """Return the active known-composition set, deduplicated on canonical formula.

    Reads only active reference sources (via :func:`query.active_join`), keeps each
    compound its source admits, collapses them to one :class:`KnownComposition`
    per canonical formula, and attaches up to ``max_identities`` identities per
    formula. A formula carries the identities of the sources that admit it, and
    only those: one another source holds outside its window lends it nothing.

    :param session: Active async session.
    :param licenses: If given, keep only compounds whose per-record license is in
        this set (commercial gating). ``None`` keeps all.
    :param ceiling: The chemistry context's ceiling on every source's window.
        ``None`` sets none, so each source is bounded by its own row alone.
    :param polarity: The sample's polarity, ``positive`` or ``negative``. A
        source whose row records the other polarity contributes nothing;
        ``None`` matches every source.
    :param max_identities: Cap on identities retained per formula.
    :return: Known compositions, one per unique formula, ascending by formula.
    """
    stmt = active_join(*_KNOWN_COLUMNS, *_SCOPE_COLUMNS)
    # Charged species are recorded, not matched: Stage A pairs a NEUTRAL formula
    # with an ionization mechanism, so an intrinsically charged row has no
    # neutral precursor to expand and could only ever produce a false identity
    # (issue #1726). NULL is both the pre-charge-column state and the neutral
    # default, so it passes.
    stmt = stmt.where(
        or_(
            reference_compound.c.charge.is_(None),
            reference_compound.c.charge == 0,
        )
    )
    if licenses is not None:
        stmt = stmt.where(reference_compound.c.license.in_(licenses))
    if polarity is not None:
        stmt = stmt.where(
            or_(
                reference_source.c.polarity.is_(None),
                reference_source.c.polarity == polarity,
            )
        )
    if ceiling is not None and ceiling.max_mass is not None:
        # The ceiling bounds every source, so its mass cap can be left to the
        # index; each source's own cap is applied per row below.
        stmt = stmt.where(reference_compound.c.monoisotopic_mass <= ceiling.max_mass)
    # Totally ordered, so which identities survive ``max_identities`` is the same
    # on every run over the same data rather than whatever order the rows arrive in.
    stmt = stmt.order_by(*_STABLE_ORDER)

    by_formula: dict[str, KnownComposition] = {}
    # What each source admits, read once per source: its window under the
    # ceiling, and whether its radicals may be matched.
    admits_by_source: dict[int, tuple[KnownWindow, bool]] = {}
    # Decided once per (source, formula): a formula repeats within a source only
    # as isomers, and across sources each has its own window to answer to.
    decided: dict[tuple[int, str], bool] = {}
    # Streamed, not materialized: only the kept set and the decisions stay in
    # memory, so an off-domain mirror costs a scan rather than the whole table
    # resident in the worker.
    result = await session.stream(stmt.execution_options(yield_per=_STREAM_BATCH))
    async for row in result:
        formula = row.formula
        key = (row.source_id, formula)
        admitted = decided.get(key)
        if admitted is None:
            scope = admits_by_source.get(row.source_id)
            if scope is None:
                source = SourceScope.from_row(row)
                scope = (source.known_window.intersect(ceiling), source.allow_radicals)
                admits_by_source[row.source_id] = scope
            window, allow_radicals = scope
            admitted = window.admits(formula, row.monoisotopic_mass) and (
                allow_radicals or not is_odd_electron(formula)
            )
            decided[key] = admitted
        if not admitted:
            continue
        known = by_formula.get(formula)
        if known is None:
            known = KnownComposition(
                formula=formula,
                monoisotopic_mass=row.monoisotopic_mass,
                identities=[],
            )
            by_formula[formula] = known
        if len(known.identities) < max_identities:
            known.identities.append(
                KnownIdentity(
                    name=row.name,
                    source=row.source_name,
                    license=row.license,
                    inchikey=row.inchikey,
                    source_native_id=row.source_native_id,
                    xrefs=row.xrefs or {},
                )
            )
    return list(by_formula.values())
