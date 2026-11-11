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
names the run matched. The first name's InChIKey rides along, so a ledger's
verdict form can offer that compound as the structure a confirmation names. The batch ledger carries the same on the registry entry
of each identity a member brought matched from a list, and shows the one its
consensus names (:func:`consensus_listing`).

A row the run matched to a target compound adds what the target declares
beside it (:func:`target_candidate`, :func:`with_target`): the compound's name
and InChIKey, and whether a list carries that key. It is what the target stands
for, a candidate as a list's name is, not what the peak is. The batch ledger
finds it off the registry entry its consensus names
(:func:`consensus_target_id`).

Pure: no database, no I/O.
"""

from __future__ import annotations

from typing import Any, Optional


def reference_listing(identities: Any) -> Optional[dict]:
    """The flattened listing of the identities a row matched, or None.

    :param identities: A row's ``provenance.reference_identities``: a list of
        identity dicts, or anything else (absent, malformed) for a row no list
        names.
    :return: ``{"name", "source", "inchikey", "tags", "total"}`` - the first
        identity's name, list and InChIKey (None where the list gives none),
        the tags every identity's list carries, each once, in the order first
        met, and how many identities the run matched - or None where the row
        matched none.
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
        "inchikey": _text(first.get("inchikey")),
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
    entry = _consensus_entry(
        candidates,
        formula,
        ion_formula,
        ionization_mechanism_id,
        lambda entry: isinstance(entry.get("listing"), dict),
    )
    return entry["listing"] if entry else None


def consensus_target_id(
    candidates: Any,
    formula: Optional[str],
    ion_formula: Optional[str] = None,
    ionization_mechanism_id: Optional[str] = None,
) -> Optional[str]:
    """The target compound a batch peak's consensus was matched to, off its registry.

    Chosen as :func:`consensus_listing` chooses: the entry naming the consensus
    identity exactly, else another entry of the same formula that a member
    brought from the target library. A target compound is a neutral, so a
    member that matched it through one channel names the compound the
    consensus names through another.

    :param candidates: The anchor's registry (``BatchPeak.candidates``).
    :param formula: The consensus formula; None names no target.
    :param ion_formula: The consensus ion formula.
    :param ionization_mechanism_id: The consensus mechanism.
    :return: The ``target_compound_id``, or None where no member brought one.
    """
    entry = _consensus_entry(
        candidates,
        formula,
        ion_formula,
        ionization_mechanism_id,
        lambda entry: bool(_text(entry.get("target_compound_id"))),
    )
    return entry["target_compound_id"] if entry else None


def _consensus_entry(candidates, formula, ion_formula, mechanism, carries):
    if not formula or not isinstance(candidates, list):
        return None
    same_formula = [
        entry
        for entry in candidates
        if isinstance(entry, dict)
        and entry.get("formula") == formula
        and carries(entry)
    ]
    for entry in same_formula:
        if (
            entry.get("ion_formula") == ion_formula
            and entry.get("ionization_mechanism_id") == mechanism
        ):
            return entry
    return same_formula[0] if same_formula else None


def target_candidate(
    name: Any, inchikey: Any, listed_inchikeys: Optional[set] = None
) -> Optional[dict]:
    """What a target compound declares, as a candidate beside the listing.

    :param name: The compound's name, or None.
    :param inchikey: The InChIKey it declares, or None for a compound with no
        structure.
    :param listed_inchikeys: The keys an active list carries, of the ones asked
        about; None where nobody asked.
    :return: ``{"label": "target", "name", "inchikey", "listed"}``: ``listed``
        True where a list carries the key, False where none does (the
        inspector reads that *unlisted*), and None where the compound declares
        no key or nobody asked. None for a compound that declares neither a
        name nor a key: it stands for nothing beyond the row's formula.
    """
    key = _text(inchikey)
    if key is None and _text(name) is None:
        return None
    listed = None
    if key is not None and listed_inchikeys is not None:
        listed = key in listed_inchikeys
    return {"label": "target", "name": _text(name), "inchikey": key, "listed": listed}


def with_target(listing: Optional[dict], target: Optional[dict]) -> Optional[dict]:
    """A flattened listing with the target's candidate beside it.

    A row no list names but that names a target gets a listing all the same,
    with no list name and a ``total`` of 0, so the field still says what the
    target stands for. A row with no target is left as it is.

    :param listing: The row's :func:`reference_listing`, or None.
    :param target: The row's :func:`target_candidate`, or None.
    :return: The listing with ``target`` on it, or ``listing`` unchanged.
    """
    if target is None:
        return listing
    base = listing or {
        "name": None,
        "source": None,
        "inchikey": None,
        "tags": [],
        "total": 0,
    }
    return {**base, "target": target}


def _text(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None
