"""Identification claims: a confirmed verdict that names a structure, and its level.

A verdict judges a formula and a mechanism. A claim is a confirmed verdict
that also names a structure, an InChIKey and the name that goes with it
(``docs/dev/identification_claims.md`` sections 5.1 and 5.2). It lives on the
two verification tables, so it is keyed, superseded and overlaid exactly as a
verdict is.

The level a verdict states follows from its evidence (the note's section 5.4,
after Schymanski et al. 2014):

==================== ================== =====
evidence_level       structure named    level
==================== ================== =====
reference_standard   required           1
msms                 required           2
orthogonal           required           3
pattern              optional           4
visual               optional           none: the engine's
==================== ================== =====

A level is a person's statement, so it is fixed when the verdict is recorded,
with the mapping's version in ``context`` (:data:`LEVEL_MAPPING_VERSION`); the
engine's level is recomputed per run under a mapping of its own, and the two
meet only when a row is read, where the live claim wins. A verdict that states
no level (a visual confirmation, a rejection, an unsure one) leaves the row to
the engine's. A structure named on a pattern or visual confirmation is kept,
and raises nothing: the evidence is about a formula.

Pure: no database, no I/O.
"""

from __future__ import annotations

from typing import Optional


#: The version of the evidence-to-level mapping below. A change to the
#: mapping bumps it, and a verdict recorded under the old one keeps its level.
LEVEL_MAPPING_VERSION = 1

#: The ``context`` key the mapping version is recorded under.
LEVEL_MAPPING_KEY = "level_mapping_version"

#: Evidence that is a statement about a structure: a confirmation on it names one.
STRUCTURE_EVIDENCE = ("reference_standard", "msms", "orthogonal")

#: evidence_level -> the level a confirmation on it states; visual states none.
_LEVEL_OF_EVIDENCE = {
    "reference_standard": 1,
    "msms": 2,
    "orthogonal": 3,
    "pattern": 4,
}


def claim_problem(
    verdict: str,
    evidence_level: Optional[str],
    claimed_inchikey: Optional[str],
    claimed_name: Optional[str],
) -> Optional[str]:
    """Why a verdict's claim cannot be recorded, or None when it can.

    The rules the request bodies apply, each refusal naming its rule:

    - a structure is named only by a confirmation;
    - a name goes with an InChIKey;
    - a reference-standard, MS/MS or orthogonal confirmation names a structure.

    :param verdict: confirmed | rejected | unsure.
    :param evidence_level: The verdict's basis, or None.
    :param claimed_inchikey: The structure named, or None.
    :param claimed_name: The name given with it, or None.
    :return: The refusal, or None.
    """
    if claimed_inchikey is not None and verdict != "confirmed":
        return (
            "claimed_inchikey requires verdict 'confirmed': only a confirmation "
            "names a structure"
        )
    if claimed_name is not None and claimed_inchikey is None:
        return "claimed_name requires claimed_inchikey: a name goes with a structure"
    if (
        verdict == "confirmed"
        and evidence_level in STRUCTURE_EVIDENCE
        and claimed_inchikey is None
    ):
        return (
            f"a confirmation on evidence_level '{evidence_level}' requires "
            "claimed_inchikey: reference_standard, msms and orthogonal are "
            "statements about a structure"
        )
    return None


def claim_level(
    verdict: str, evidence_level: Optional[str], claimed_inchikey: Optional[str]
) -> Optional[int]:
    """The level a verdict states, under :data:`LEVEL_MAPPING_VERSION`.

    :param verdict: confirmed | rejected | unsure.
    :param evidence_level: The verdict's basis, or None.
    :param claimed_inchikey: The structure named, or None.
    :return: 1 to 4, or None where the verdict states no level and a reader
        takes the engine's.
    :raises ValueError: On a level 1 to 3 that names no structure, which
        :func:`claim_problem` refuses before a write gets here.
    """
    if verdict != "confirmed":
        return None
    level = _LEVEL_OF_EVIDENCE.get(evidence_level)
    if level is not None and level <= 3 and claimed_inchikey is None:
        raise ValueError(f"A level {level} names a structure; none was given")
    return level


def mapping_context(context: Optional[dict] = None) -> dict:
    """``context`` with the mapping version a verdict's level was fixed under.

    :param context: What else the verdict's context records, or None.
    :return: A new dict.
    """
    return {**(context or {}), LEVEL_MAPPING_KEY: LEVEL_MAPPING_VERSION}
