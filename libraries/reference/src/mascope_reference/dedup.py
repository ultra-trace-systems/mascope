"""Cross-source de-duplication on InChIKey.

The mirror keeps one row per (compound, source) to preserve provenance and the
per-record license. When a single answer per compound is wanted - collapsing
the same molecule reported by PubChem, ChEBI, CompTox, ... into one identity -
this module collapses records on their InChIKey.

Collapse happens at query time over the small result set an annotation returns
(a handful of records per formula), rather than as a materialized SQL view, so
source priority stays configurable and the logic is unit-testable without a
database. Records with no InChIKey cannot be safely merged and pass through
individually.
"""

from collections.abc import Iterable

from mascope_reference.record import ReferenceRecord


# Preferred source order when collapsing. Curated identity/nomenclature sources
# rank ahead of universal-coverage PubChem, which ranks ahead of the suspect
# lists - so the surviving name/structure is the best-curated one available.
DEFAULT_SOURCE_PRIORITY = (
    "chebi",
    "lipidmaps",
    "hmdb",
    "comptox",
    "pubchem",
    "coconut",
    "norman",
)


def _priority_rank(source: str, priority: tuple[str, ...]) -> int:
    """Rank of a source (lower is preferred); unlisted sources sort last."""
    try:
        return priority.index(source)
    except ValueError:
        return len(priority)


def collapse_by_inchikey(
    records: Iterable[ReferenceRecord],
    source_priority: tuple[str, ...] = DEFAULT_SOURCE_PRIORITY,
) -> list[ReferenceRecord]:
    """Collapse records sharing an InChIKey into one preferred record each.

    For each InChIKey group the highest-priority source wins the identity
    fields; any field it leaves blank is backfilled from the other members, and
    every member's ``xrefs`` are merged into the survivor (with a ``sources``
    entry listing all contributing sources). Records without an InChIKey are
    returned unchanged, since they cannot be confidently de-duplicated.

    Input order is otherwise preserved: the first appearance of each InChIKey
    (and each keyless record) fixes its position in the output.

    :param records: Records to collapse (typically one formula's annotations).
    :param source_priority: Source names best-first; unlisted sources sort last.
    :return: De-duplicated records.
    """
    groups: dict[str, list[ReferenceRecord]] = {}
    order: list[str | None] = []
    passthrough: dict[int, ReferenceRecord] = {}

    for index, record in enumerate(records):
        key = record.inchikey or None
        if key is None:
            passthrough[index] = record
            order.append(None)
            continue
        if key not in groups:
            order.append(key)
        groups.setdefault(key, []).append(record)

    result: list[ReferenceRecord] = []
    seen_keys: set[str] = set()
    passthrough_iter = iter(passthrough.values())
    for entry in order:
        if entry is None:
            result.append(next(passthrough_iter))
            continue
        if entry in seen_keys:
            continue
        seen_keys.add(entry)
        result.append(_merge_group(groups[entry], source_priority))
    return result


def _merge_group(
    group: list[ReferenceRecord], source_priority: tuple[str, ...]
) -> ReferenceRecord:
    """Merge one InChIKey group into a single record."""
    ranked = sorted(group, key=lambda r: _priority_rank(r.source, source_priority))
    primary = ranked[0]

    # Backfill identity fields the primary leaves blank from lower-priority members.
    merged: dict = {}
    for field in ("name", "smiles", "inchi", "monoisotopic_mass"):
        value = getattr(primary, field)
        if value in (None, ""):
            for other in ranked[1:]:
                candidate = getattr(other, field)
                if candidate not in (None, ""):
                    value = candidate
                    break
        merged[field] = value

    # Merge every member's xrefs; record which sources contributed.
    xrefs: dict = {}
    for record in ranked:
        xrefs.update(record.xrefs or {})
    xrefs["sources"] = [record.source for record in ranked]
    merged["xrefs"] = xrefs

    return primary.model_copy(update=merged)
