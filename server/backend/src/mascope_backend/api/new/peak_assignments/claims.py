"""Identification claims: a confirmed verdict that names a structure, and its level.

A verdict judges a formula and a mechanism. A claim is a confirmed verdict
that also names a structure, an InChIKey and the name that goes with it
(``docs/dev/identification_claims.md`` sections 5.1 and 5.2), or, at 3b, a
class. It lives on the two verification tables, so it is keyed, superseded and
overlaid exactly as a verdict is.

The level a verdict states follows from its evidence and from what the person
says that evidence showed (the note's section 5.4, version 2; the scale is
``docs/dev/assignment_confidence.md``, "P4 design: the identification level",
after Schymanski et al. 2014):

==================== ======================== ===== =========================
evidence_level       what the person states   level what it names
==================== ======================== ===== =========================
reference_standard   an independent line      1     a structure
reference_standard   no independent line      2b    a structure
msms                 ``library``              2a    a structure
msms                 ``fragments``            2b    a structure
msms                 ``loss``                 3b    a class, in claimed_name
orthogonal           ``separation``           2b    a structure
orthogonal           ``data_and_list``        3a    a structure
orthogonal           ``class``                3b    a class, in claimed_name
orthogonal           ``context_list``         3c    a structure
orthogonal           ``homologue``            3d    a structure
pattern              ``split_pinned``         4a    optional
pattern              ``split_open`` or none   4b    optional
visual               -                        none: the engine's
==================== ======================== ===== =========================

A reference standard is an authentic standard run on this instrument with the
same reagent chemistry (same mass, ionization and fragments); the independent
line beside it is MS/MS, a separation or another instrument
(:data:`INDEPENDENT_LINES`). Level 1 is the one level a person alone states,
and it is scoped: a per-sample claim to its sample, a batch-peak claim to the
time window it names (:func:`window_problem`, :func:`level_reaches`).

A level is a person's statement, so it is fixed when the verdict is recorded:
the number in ``level``, the letter in ``level_letter``, and in ``context`` the
mapping's version (:data:`LEVEL_MAPPING_VERSION`) and the statement it was
fixed from, with its evidence line and its lift (:func:`mapping_context`). A
claim recorded under version 1 keeps its level, its version and no letter. The
engine's level is recomputed per run under rules of its own
(``identification_level``), and the two meet only when a row is read, where
the live claim wins. A verdict that states no level (a visual confirmation, a
rejection, an unsure one) leaves the row to the engine's. A structure named on
a pattern or visual confirmation is kept, and raises nothing: the evidence is
about a formula.

Pure: no database, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional


#: The version of the evidence-to-level mapping below. A change to the
#: mapping bumps it, and a verdict recorded under the old one keeps its level.
#: Version 1 (MAS-359) took a reference standard alone to 1, any MS/MS to 2,
#: orthogonal evidence to 3 and a pattern to 4, with no letter; version 2 is
#: the shared scale's (step 5.8f).
LEVEL_MAPPING_VERSION = 2

#: The one level a person alone states, in review: a confirmed structure. No
#: engine path and no import writes it (``identification_level.ENGINE_LEVELS``).
REVIEW_LEVEL = "1"

#: The ``context`` key the mapping version is recorded under.
LEVEL_MAPPING_KEY = "level_mapping_version"

#: The ``context`` key the statement a level was fixed from is recorded under:
#: ``evidence_detail``, ``independent_line``, ``evidence`` and ``lift``.
CLAIM_KEY = "claim"

#: The ``context`` key a rejection on reference-standard evidence records the
#: confirmation it refutes under (:func:`refutation`).
REFUTES_KEY = "refutes"

#: Evidence that is a statement about a structure, or at 3b about a class.
STRUCTURE_EVIDENCE = ("reference_standard", "msms", "orthogonal")

#: The evidence a standard needs beside it for level 1: MS/MS, a separation, or
#: the same air on another instrument.
INDEPENDENT_LINES = ("msms", "separation", "instrument")

#: The words an independent line is named by in a claim's evidence line.
_LINE_WORDS = {
    "msms": "MS/MS",
    "separation": "a separation",
    "instrument": "another instrument",
}

#: The lift of a reference standard with no independent line (decided
#: 2026-11-17 by the plan owner): what would take it from 2b to 1.
LIFT_INDEPENDENT_LINE = "an independent line"

#: The lift of a claim at 2: what would take it to 1.
_LIFT_TO_ONE = (
    "an authentic standard on this instrument with the same reagent chemistry, "
    "and an independent line"
)

#: The lift of a claim at 3: what would name one structure.
_LIFT_TO_TWO = "a library spectrum, or two or more fragments that fit one structure"


@dataclass(frozen=True)
class Statement:
    """The level a confirmation states, with the two lines every level carries.

    :param level: The number, 1 to 4.
    :param letter: The letter of the scale beside it, None at 1.
    :param evidence: One line naming what earned it.
    :param lift: What would raise it; None at 1.
    :param names: ``structure``, ``class`` or ``formula``: what the claim
        names, which decides whether it takes an InChIKey.
    """

    level: int
    letter: Optional[str]
    evidence: str
    lift: Optional[str]
    names: str

    @property
    def label(self) -> str:
        """The level as the scale writes it: ``1``, ``2b``, ``4a``."""
        return f"{self.level}{self.letter or ''}"

    @property
    def is_class(self) -> bool:
        """A class claim: it names a class in ``claimed_name``, no InChIKey."""
        return self.names == "class"


#: evidence_level -> evidence_detail -> the statement it makes. The detail is
#: what the person says the evidence showed; a reference standard's is its
#: independent line (:data:`INDEPENDENT_LINES`) and has its own field.
EVIDENCE_DETAILS: dict[str, dict[str, Statement]] = {
    "msms": {
        "library": Statement(
            2, "a", "a library spectrum match", _LIFT_TO_ONE, "structure"
        ),
        "fragments": Statement(
            2,
            "b",
            "two or more fragments that fit one structure, rivals excluded",
            _LIFT_TO_ONE,
            "structure",
        ),
        "loss": Statement(3, "b", "one MS/MS loss: a class", _LIFT_TO_TWO, "class"),
    },
    "orthogonal": {
        "separation": Statement(
            2, "b", "a separation that leaves one structure", _LIFT_TO_ONE, "structure"
        ),
        "data_and_list": Statement(
            3, "a", "the data and a list agree", _LIFT_TO_TWO, "structure"
        ),
        "class": Statement(
            3, "b", "orthogonal evidence for a class", _LIFT_TO_TWO, "class"
        ),
        "context_list": Statement(3, "c", "a context list", _LIFT_TO_TWO, "structure"),
        "homologue": Statement(3, "d", "a homologue series", _LIFT_TO_TWO, "structure"),
    },
    "pattern": {
        "split_pinned": Statement(
            4,
            "a",
            "the isotope and adduct pattern; the molecule-reagent split pinned",
            "a context list for the run's chemistry, or one MS/MS loss",
            "formula",
        ),
        "split_open": Statement(
            4,
            "b",
            "the isotope and adduct pattern; the molecule-reagent split open",
            "a pinned molecule-reagent split",
            "formula",
        ),
    },
}

#: The evidence levels whose confirmation must say what it showed. A pattern
#: confirmation that does not say its split is pinned states the split open
#: (:data:`PATTERN_DEFAULT`), so a client that predates the detail still
#: records the formula it confirmed.
DETAIL_REQUIRED = ("msms", "orthogonal")

#: What a pattern confirmation states when it says nothing of the split: the
#: level does not assume a split nobody said was pinned.
PATTERN_DEFAULT = "split_open"


def _standard(independent_line: Optional[str]) -> Statement:
    """A reference standard's statement: 1 with an independent line, else 2b."""
    if independent_line is None:
        return Statement(
            2,
            "b",
            "an authentic standard on this instrument with the same reagent "
            "chemistry; no independent line",
            LIFT_INDEPENDENT_LINE,
            "structure",
        )
    return Statement(
        1,
        None,
        "an authentic standard on this instrument with the same reagent "
        f"chemistry, and {_LINE_WORDS.get(independent_line, independent_line)}",
        None,
        "structure",
    )


def statement(
    verdict: str,
    evidence_level: Optional[str],
    evidence_detail: Optional[str] = None,
    independent_line: Optional[str] = None,
) -> Optional[Statement]:
    """What a verdict states under :data:`LEVEL_MAPPING_VERSION`, before any
    structure is checked.

    :param verdict: confirmed | rejected | unsure.
    :param evidence_level: The verdict's basis, or None.
    :param evidence_detail: What the evidence showed (:data:`EVIDENCE_DETAILS`).
    :param independent_line: A reference standard's independent line.
    :return: The statement, or None where the verdict states no level: not a
        confirmation, a visual look, or a detail the mapping does not know.
    """
    if verdict != "confirmed":
        return None
    if evidence_level == "reference_standard":
        return _standard(independent_line)
    details = EVIDENCE_DETAILS.get(evidence_level)
    if details is None:
        return None
    if evidence_detail is None and evidence_level == "pattern":
        evidence_detail = PATTERN_DEFAULT
    return details.get(evidence_detail)


def claim_problem(
    verdict: str,
    evidence_level: Optional[str],
    claimed_inchikey: Optional[str],
    claimed_name: Optional[str],
    evidence_detail: Optional[str] = None,
    independent_line: Optional[str] = None,
) -> Optional[str]:
    """Why a verdict's claim cannot be recorded, or None when it can.

    The rules the request bodies apply, each refusal naming its rule:

    - what the evidence showed, and an independent line, are said only of a
      confirmation; the line only of a reference standard, and a detail only
      in its evidence level's words; an MS/MS or orthogonal confirmation says
      what it showed;
    - a structure is named only by a confirmation;
    - a class claim (3b) names its class in ``claimed_name`` and no InChIKey;
    - otherwise a name goes with an InChIKey;
    - a confirmation that states 1, 2 or a 3 naming a candidate names a
      structure.

    :param verdict: confirmed | rejected | unsure.
    :param evidence_level: The verdict's basis, or None.
    :param claimed_inchikey: The structure named, or None.
    :param claimed_name: The name given with it, or a class's at 3b.
    :param evidence_detail: What the evidence showed, or None.
    :param independent_line: A reference standard's independent line, or None.
    :return: The refusal, or None.
    """
    confirmed = verdict == "confirmed"
    if independent_line is not None:
        if not confirmed or evidence_level != "reference_standard":
            return (
                "independent_line goes with a 'confirmed' verdict on "
                "evidence_level 'reference_standard': it is the line beside "
                "the standard"
            )
        if independent_line not in INDEPENDENT_LINES:
            return f"independent_line is one of {', '.join(INDEPENDENT_LINES)}"
    if evidence_detail is not None:
        details = EVIDENCE_DETAILS.get(evidence_level)
        if not confirmed or details is None:
            return (
                "evidence_detail goes with a 'confirmed' verdict on evidence_level "
                "'msms', 'orthogonal' or 'pattern': it says what that evidence showed"
            )
        if evidence_detail not in details:
            return (
                f"evidence_detail on '{evidence_level}' is one of {', '.join(details)}"
            )
    if confirmed and evidence_level in DETAIL_REQUIRED and evidence_detail is None:
        return (
            f"a confirmation on evidence_level '{evidence_level}' requires "
            f"evidence_detail, one of {', '.join(EVIDENCE_DETAILS[evidence_level])}: "
            "what it showed decides the level"
        )
    if claimed_inchikey is not None and not confirmed:
        return (
            "claimed_inchikey requires verdict 'confirmed': only a confirmation "
            "names a structure"
        )
    stated = statement(verdict, evidence_level, evidence_detail, independent_line)
    if stated is not None and stated.is_class:
        if claimed_inchikey is not None:
            return (
                f"a class claim ({stated.label}, evidence_detail '{evidence_detail}') "
                "names a class, not a structure: send no claimed_inchikey"
            )
        if claimed_name is None:
            return (
                f"a class claim ({stated.label}, evidence_detail '{evidence_detail}') "
                "requires claimed_name: the class it names"
            )
        return None
    if claimed_name is not None and claimed_inchikey is None:
        return "claimed_name requires claimed_inchikey: a name goes with a structure"
    if stated is not None and stated.names == "structure" and claimed_inchikey is None:
        return (
            f"a confirmation that states level {stated.label} requires "
            "claimed_inchikey: levels 1, 2 and a 3 naming a candidate are "
            "statements about a structure"
        )
    return None


def claim_level(
    verdict: str,
    evidence_level: Optional[str],
    claimed_inchikey: Optional[str],
    evidence_detail: Optional[str] = None,
    independent_line: Optional[str] = None,
) -> Optional[Statement]:
    """The level a verdict states, under :data:`LEVEL_MAPPING_VERSION`.

    :param verdict: confirmed | rejected | unsure.
    :param evidence_level: The verdict's basis, or None.
    :param claimed_inchikey: The structure named, or None.
    :param evidence_detail: What the evidence showed, or None.
    :param independent_line: A reference standard's independent line, or None.
    :return: The statement (its number, letter, evidence and lift), or None
        where the verdict states no level and a reader takes the engine's.
    :raises ValueError: On a statement about a structure that names none, or a
        class claim that names one, which :func:`claim_problem` refuses before
        a write gets here.
    """
    stated = statement(verdict, evidence_level, evidence_detail, independent_line)
    if stated is None:
        return None
    if stated.names == "structure" and claimed_inchikey is None:
        raise ValueError(f"A level {stated.label} names a structure; none was given")
    if stated.is_class and claimed_inchikey is not None:
        raise ValueError(f"A level {stated.label} names a class, not a structure")
    return stated


def window_problem(
    stated: Optional[Statement],
    window_start: Optional[datetime],
    window_end: Optional[datetime],
) -> Optional[str]:
    """Why a batch-peak claim's time window cannot be recorded, or None.

    A level-1 claim on a batch peak is scoped to the acquisitions its standard
    covers: the window is its first and last acquisition time, both given,
    the first not after the last. Only a level-1 claim names one, since the
    lower levels read the evidence of the data itself.

    :param stated: What the verdict states (:func:`statement`), or None.
    :param window_start: The first acquisition time the claim covers.
    :param window_end: The last.
    :return: The refusal, or None.
    """
    given = (window_start is not None, window_end is not None)
    level_one = stated is not None and stated.level == 1
    if given == (False, False):
        if level_one:
            return (
                "a level-1 claim on a batch peak requires window_start_utc and "
                "window_end_utc: the first and last acquisition time its "
                "standard covers"
            )
        return None
    if not level_one:
        return (
            "window_start_utc and window_end_utc scope a level-1 claim (a "
            "reference standard with an independent line), and only one"
        )
    if given != (True, True):
        return "window_start_utc and window_end_utc go together"
    if window_start > window_end:
        return "window_start_utc is after window_end_utc"
    return None


def level_reaches(record: Any, acquired_utc: Optional[datetime]) -> bool:
    """Whether a batch-peak claim's structure and level reach a sample
    acquired at a time.

    A claim with no window reaches every sample its verdict does. A level-1
    claim's reaches only the samples acquired inside its window, its ends
    included; outside it, and on a sample with no acquisition time, the
    sample's row names no structure and reads the engine's level. The verdict
    reaches the sample either way: the window scopes the structure, not the
    formula confirmed.

    :param record: A ``BatchPeakVerification`` or its dict.
    :param acquired_utc: The sample's acquisition time, or None.
    :return: True where the claim's level holds for the sample.
    """
    get = record.get if isinstance(record, dict) else lambda key: getattr(record, key)
    start, end = get("window_start_utc"), get("window_end_utc")
    if start is None or end is None:
        return True
    if acquired_utc is None:
        return False
    return start <= acquired_utc <= end


def mapping_context(
    context: Optional[dict] = None,
    *,
    stated: Optional[Statement] = None,
    evidence_detail: Optional[str] = None,
    independent_line: Optional[str] = None,
    refutes: Optional[dict] = None,
) -> dict:
    """``context`` with the mapping version a verdict's level was fixed under.

    :param context: What else the verdict's context records, or None.
    :param stated: What the verdict states; recorded under :data:`CLAIM_KEY`
        with what the person said and the evidence line and lift that follow.
    :param evidence_detail: What the evidence showed, as the person said it.
    :param independent_line: A reference standard's independent line.
    :param refutes: The confirmation a rejection refutes (:func:`refutation`).
    :return: A new dict.
    """
    out = {**(context or {}), LEVEL_MAPPING_KEY: LEVEL_MAPPING_VERSION}
    if stated is not None:
        out[CLAIM_KEY] = {
            "evidence_detail": evidence_detail,
            "independent_line": independent_line,
            "evidence": stated.evidence,
            "lift": stated.lift,
        }
    if refutes is not None:
        out[REFUTES_KEY] = refutes
    return out


def refutation(
    verdict: str, evidence_level: Optional[str], live: Any, id_field: str
) -> Optional[dict]:
    """What a rejection on reference-standard evidence refutes: the live
    confirmation it supersedes.

    A standard that disagrees is a result: the claim is rejected, and the row
    reads the engine's level again, since a rejection states none. The
    rejection records which confirmation it refuted - its level, its structure
    and when it was made - so the record says what the standard took back
    rather than only that something was rejected.

    :param verdict: The new verdict.
    :param evidence_level: Its basis.
    :param live: The live verdict on the same identity, or None.
    :param id_field: The name of ``live``'s id column.
    :return: The refuted claim, or None where this is no refutation: another
        verdict or basis, or no live confirmation to refute.
    """
    if verdict != "rejected" or evidence_level != "reference_standard":
        return None
    if live is None or live.verdict != "confirmed":
        return None
    when = live.verified_utc
    return {
        id_field: getattr(live, id_field),
        "level": live.level,
        "level_letter": getattr(live, "level_letter", None),
        "evidence_level": live.evidence_level,
        "claimed_inchikey": live.claimed_inchikey,
        "claimed_name": live.claimed_name,
        "verified_utc": when.isoformat() if when is not None else None,
    }
