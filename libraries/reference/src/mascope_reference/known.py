"""Bulk known-composition provider for peak-centric Stage A.

Where :mod:`mascope_reference.query` answers "what is this formula" one lookup at
a time (annotation), this answers "give me the whole known set to match against":
the active reference compounds collapsed to **unique canonical formulas**, each
carrying the one-to-many identities that share it. This encodes the load-bearing
split from the convergence design - *matching is formula-based; identity is
one-to-many* - so Stage A pre-computes isotopologues once per formula and attaches
the (possibly several) names afterwards.

An optional atmospheric-window bound (element set / carbon count / mass) keeps
Stage A from expanding a large off-domain reference mirror (e.g. full CompTox)
into isotopologues per sample - the performance guard the convergence doc leaves
as an open decision. Callers can widen or disable it.
"""

from dataclasses import dataclass, field

from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession

from mascope_reference.query import _STABLE_ORDER, active_join
from mascope_reference.schema import reference_compound
from mascope_tools.composition.utils import parse_composition


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


# Default bound: the atmospheric-organics window. Elements a monoterpene-SOA /
# HOM study cares about, a generous carbon cap, and a mass ceiling above dimers.
DEFAULT_ELEMENTS = frozenset({"C", "H", "N", "O", "S"})
DEFAULT_MAX_CARBON = 40
DEFAULT_MAX_MASS = 700.0
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


def _within_bound(
    formula: str,
    mass: float | None,
    *,
    elements: frozenset[str] | None,
    max_carbon: int | None,
    max_mass: float | None,
) -> bool:
    """Whether a formula passes the (optional) atmospheric window."""
    if max_mass is not None and (mass is None or mass > max_mass):
        return False
    if elements is None and max_carbon is None:
        return True
    try:
        composition = parse_composition(formula)
    except Exception:
        return False
    present = {sym for sym, count in composition.items() if count > 0}
    if elements is not None and not present.issubset(elements):
        return False
    if max_carbon is not None and composition.get("C", 0) > max_carbon:
        return False
    return True


async def iter_known_compositions(
    session: AsyncSession,
    *,
    licenses: set[str] | None = None,
    elements: frozenset[str] | None = DEFAULT_ELEMENTS,
    max_carbon: int | None = DEFAULT_MAX_CARBON,
    max_mass: float | None = DEFAULT_MAX_MASS,
    max_identities: int = DEFAULT_MAX_IDENTITIES,
) -> list[KnownComposition]:
    """Return the active known-composition set, deduplicated on canonical formula.

    Reads only active reference sources (via :func:`query._active_join`), collapses
    rows to one :class:`KnownComposition` per canonical formula, and attaches up to
    ``max_identities`` identities per formula. Optionally bounds the set to an
    atmospheric window so Stage A does not expand an off-domain mirror.

    :param session: Active async session.
    :param licenses: If given, keep only compounds whose per-record license is in
        this set (commercial gating). ``None`` keeps all.
    :param elements: Allowed element symbols; a formula with any other element is
        dropped. ``None`` disables the element filter.
    :param max_carbon: Drop formulas with more carbons than this. ``None`` disables.
    :param max_mass: Drop compounds heavier than this monoisotopic mass (also drops
        rows with no stored mass). ``None`` disables.
    :param max_identities: Cap on identities retained per formula.
    :return: Known compositions, one per unique formula, ascending by formula.
    """
    stmt = active_join(*_KNOWN_COLUMNS)
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
    if max_mass is not None:
        stmt = stmt.where(reference_compound.c.monoisotopic_mass <= max_mass)
    # Totally ordered, so which identities survive ``max_identities`` is the same
    # on every run over the same data rather than whatever order the rows arrive in.
    stmt = stmt.order_by(*_STABLE_ORDER)

    by_formula: dict[str, KnownComposition] = {}
    rejected: set[str] = set()
    # Streamed, not materialized: only the kept set (bounded by the window below)
    # and the rejected-formula set stay in memory, so an off-domain mirror costs
    # a scan rather than the whole table resident in the worker.
    result = await session.stream(stmt.execution_options(yield_per=_STREAM_BATCH))
    async for row in result:
        formula = row.formula
        if formula in rejected:
            continue
        known = by_formula.get(formula)
        if known is None:
            if not _within_bound(
                formula,
                row.monoisotopic_mass,
                elements=elements,
                max_carbon=max_carbon,
                max_mass=max_mass,
            ):
                rejected.add(formula)
                continue
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
