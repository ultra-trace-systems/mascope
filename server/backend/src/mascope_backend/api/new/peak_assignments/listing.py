"""What a reference list calls an assignment's formula, flattened for the ledgers.

A row the run matched from a reference list records the list's identities in
``provenance.reference_identities``: every compound the list names for the
formula, up to the cap a run keeps, each with its name, the list it comes from
and the cross-references the list carries, a list's tags among them
(``xrefs.tags``, ``background`` for the cyclic siloxanes). The inspector reads
the whole list; the ledgers read one flattened field, ``reference_listing``,
because a ledger row serves no provenance (``service._provenance_scalars``).

The field says what the inspector's *listed as* field says: the first name, the
list it comes from, the tags of the lists naming the formula, and how many
names the run matched. The batch ledger carries the same on the registry entry
of each identity a member brought matched from a list, and shows the one its
consensus names (:func:`consensus_listing`).

Pure: no database, no I/O.
"""

from __future__ import annotations

from typing import Any, Optional


def reference_listing(identities: Any) -> Optional[dict]:
    """The flattened listing of the identities a row matched, or None.

    :param identities: A row's ``provenance.reference_identities``: a list of
        identity dicts, or anything else (absent, malformed) for a row no list
        names.
    :return: ``{"name", "source", "tags", "total"}`` - the first identity's
        name and list, the tags every identity's list carries, each once, in
        the order first met, and how many identities the run matched - or None
        where the row matched none.
    """
    if not isinstance(identities, list):
        return None
    records = [identity for identity in identities if isinstance(identity, dict)]
    if not records:
        return None
    tags: list[str] = []
    for identity in records:
        xrefs = identity.get("xrefs")
        listed = xrefs.get("tags") if isinstance(xrefs, dict) else None
        for tag in listed if isinstance(listed, list) else []:
            if isinstance(tag, str) and tag and tag not in tags:
                tags.append(tag)
    first = records[0]
    return {
        "name": _text(first.get("name")),
        "source": _text(first.get("source")),
        "tags": tags,
        "total": len(records),
    }


def consensus_listing(
    candidates: Any,
    formula: Optional[str],
    ion_formula: Optional[str] = None,
    ionization_mechanism_id: Optional[str] = None,
) -> Optional[dict]:
    """The listing a batch peak's consensus shows, off its registry.

    The registry entry naming the consensus identity exactly comes first. A list
    names a neutral formula, not the channel it was read through, so where that
    entry carries no listing another entry of the same formula that does stands
    in for it: a member that matched the compound from a list through one
    channel says the list holds the formula the consensus names through
    another.

    :param candidates: The anchor's registry (``BatchPeak.candidates``).
    :param formula: The consensus formula; None names no listing.
    :param ion_formula: The consensus ion formula.
    :param ionization_mechanism_id: The consensus mechanism.
    :return: The listing, or None where no member brought one for the formula.
    """
    if not formula or not isinstance(candidates, list):
        return None
    same_formula = [
        entry
        for entry in candidates
        if isinstance(entry, dict)
        and entry.get("formula") == formula
        and isinstance(entry.get("listing"), dict)
    ]
    for entry in same_formula:
        if (
            entry.get("ion_formula") == ion_formula
            and entry.get("ionization_mechanism_id") == ionization_mechanism_id
        ):
            return entry["listing"]
    return same_formula[0]["listing"] if same_formula else None


def _text(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None
