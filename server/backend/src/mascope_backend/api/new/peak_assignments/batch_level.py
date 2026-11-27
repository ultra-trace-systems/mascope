"""The batch's identification level: step 5.8c of the assignment quality plan.

A sample's run states each committed row's level from what the run records
(``identification_level``). Some facts only the batch states, recorded after
the runs stated their levels: the isotope lines step 4.2c stamped across the
track (``batch_isotopologues``) and step 4.2g's halogen lock and label twin
(``batch_checks``). This stage reads them, the first thing that does, and
states a level of the batch peak's own reading from them and from what its
members' runs stated (``docs/dev/assignment_confidence.md``, "P4 design: the
identification level", point 4; the plan's step 5.8c):

- **A halogen lock** (4.2g, ``halogen_lock`` locked) puts out each open rival
  whose ion carries none of the lock's halogen: 5a to 4.
- **A stamped 34S, 29Si or 30Si line** (4.2c, a ``diagnostic_isotopologues``
  entry stamped) puts out each open rival carrying none of its element.
- **The label twin read present** (4.2g, ``label_twin`` present) pins a
  labelled ion's split: 4b to 4a, and the split of a row the two tests above
  lift.
- **A stamped 37Cl or 81Br line refutes a reading that holds no such halogen**:
  a batch peak that 4.2c stamped as another batch peak's 37Cl or 81Br line,
  whose own reading's ion holds no chlorine or bromine to make that line,
  while the parent's reading does, reads 5b, the line and the parent's
  reading named. A parent's own lines are predicted from its own ion, so it is
  the line's reading the stamp refutes.

Each test is a rival test of the run's own vocabulary (``isotopes``), the
record it was read from named, and asks what the run asks of a rival: a rival
that reads the peak as another ion's isotope line is asked by mass and channel
alone, so none of these puts it out. A rival the row does not list, or a row
whose shortlist was not measured, stays as it was: the batch excludes nothing
it cannot name.

**The batch's level.** Each member whose run committed the consensus formula
is read under the batch's facts (:func:`overlay`); the batch's level is the
one most of them then hold, a tie reading as the level further down the scale
(``identification_level.members_level``), the rule the ledger read its
members' level by before. Its evidence, lift, facts and rivals are those of
the brightest member at that level. So a batch with no such fact states its
members' level, and the members' split stays beside it.

**3b and 2b.** The batch's lifts end at 4 and 4a; it never lowers a member's
level but by the refutation. A member whose run stated 3b or 2b keeps it, and
a consensus most of whose members read 3b reads 3b. A row the batch lifts to
4a reads no 3b or 2b: those are its run's readings of the run's chemistry
data at a 4a its run stated.

**On a sample's row** (:func:`row_batch_level`), the row's own record read
under the batch's facts, where they move its level, beside its own and marked
``borrowed``, as a batch-level verdict that reaches a sample is (step 5.13).

**A stage of the pass** (``batch_stages``), the last: after the isotope lines
and the batch checks, whose records it reads, and after the probable blends,
whose consensus recompute it reads the readings of. Every fact is read as the
ledger serves it (``standing_batch_checks_record``, which serves a lock only
while its stamping record stands; ``standing_diagnostic_isotopologues_record``;
a stamp only while its parent's record names it), so a stage before it that
runs again marks it stale, and the next pass states it again: the pass's
staleness rule is the one it is read under. The record names the reading it
was read for and stands while the consensus names it (``batch_peaks.
standing_batch_level_record``).

Nothing moves: no tier, role, member or per-sample run.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import select

from mascope_backend.api.lib.api_features import api_controller_background_task
from mascope_backend.api.new.peak_assignments.batch_lock import _acquire_batch_fold_lock
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    BATCH_LEVEL_KEY,
    ROLE_M0,
    batch_checks_record_reads,
    is_monoisotopic,
    is_stale,
    stamped_isotopologue_of_record,
    standing_batch_checks_record,
    standing_diagnostic_isotopologues_record,
)
from mascope_backend.api.new.peak_assignments.batch_runs import touch_ledger
from mascope_backend.api.new.peak_assignments.cross_channel import carries_label
from mascope_backend.api.new.peak_assignments.identification_level import (
    IDENTIFICATION_LEVEL,
    LEVEL_4A,
    LEVEL_4B,
    LEVEL_5A,
    LEVEL_5B,
    LEVELS,
    LIFT_4A,
    LIFT_RIVALS_UNMEASURED,
    LIFT_RIVALS_UNMEASURED_BEFORE,
    OPEN,
    SPLIT,
    TEST_ISOTOPES,
    TEST_MASS,
    TEST_SECOND_CHANNEL,
    engine_stated,
    is_level,
    members_level,
)
from mascope_backend.db import (
    BatchPeak,
    BatchPeakOccurrence,
    PeakAssignment,
    async_session,
)
from mascope_tools.composition.batch_checks import (
    HALOGEN_LINES,
    LOCK_LOCKED,
    TWIN_PRESENT,
    element_count,
)


#: The stamped lines whose element a rival must hold to make them, by label.
LINE_ELEMENTS = {"34S": "S", "29Si": "Si", "30Si": "Si"}

#: The halogen lines a stamp refutes a halogen-free reading by, by label.
HALOGEN_ELEMENTS = {label: element for element, label in HALOGEN_LINES.items()}

#: The words each element read here is named by.
_WORDS = {"Cl": "chlorine", "Br": "bromine", "S": "sulfur", "Si": "silicon"}

#: The records each fact is read from, as a level's ``facts`` name them.
RECORD_LOCK = "batch_checks.halogen_lock"
RECORD_TWIN = "batch_checks.label_twin"
RECORD_LINES = "diagnostic_isotopologues"
RECORD_STAMP = "stamped_isotopologue_of"

#: What moved a batch peak's level, as its record names them (``moved_by``).
BY_LOCK = "halogen_lock"
BY_LINE = "stamped_line"
BY_TWIN = "label_twin"
BY_STAMP = "stamped_halogen_line"


def _count_word(count: int) -> str:
    return {1: "one", 2: "two", 3: "three"}.get(count, str(count))


def _plural(count: int, word: str) -> str:
    return f"{_count_word(count)} {word}{'' if count == 1 else 's'}"


def holds(formula: Optional[str], element: str) -> Optional[bool]:
    """Whether an ion (or a neutral) holds a natural atom of an element; None
    where its formula cannot be read. A labelled atom is not its element's."""
    if not formula:
        return None
    try:
        return element_count(str(formula), element) > 0
    except Exception:  # noqa: BLE001 - a formula nothing reads holds nothing known
        return None


# --- the record -------------------------------------------------------------------


def batch_level_record_of(anchor: Any, *, stale: bool = False) -> Optional[dict]:
    """The batch level record an anchor carries under
    ``provenance.identification_level``, or None. A record names a level;
    anything else under that key is not one. A stale one is left out unless
    asked for (``batch_peaks.kept_record``)."""
    provenance = getattr(anchor, "provenance", None)
    record = provenance.get(BATCH_LEVEL_KEY) if isinstance(provenance, dict) else None
    if isinstance(record, dict) and record.get("level"):
        return record if stale or not is_stale(record) else None
    return None


def standing_batch_level_record(anchor: Any) -> Optional[dict]:
    """The batch level record an anchor carries, where it was read for the
    anchor's consensus reading - its ion, formula and mechanism, as a batch
    checks record is; None otherwise. What the ledger, the export and a
    sample's row serve; the consensus keeps it while it names that reading
    (``batch_peaks.compute_consensus``)."""
    record = batch_level_record_of(anchor)
    if record is None or not batch_checks_record_reads(
        record,
        getattr(anchor, "consensus_ion_formula", None),
        getattr(anchor, "consensus_formula", None),
        getattr(anchor, "ionization_mechanism_id", None),
    ):
        return None
    return record


# --- the batch's facts ------------------------------------------------------------


@dataclass(frozen=True)
class BatchFacts:
    """The batch's isotope checks a batch peak's reading is read under.

    :ivar lock: The halogen lock, where it locked: ``element``, ``label``,
        the line's ``batch_peak_id``, ``holds`` of ``judged`` samples, the
        ``stamping_run_id`` it read and the checks' ``batch_peak_run_id``.
    :ivar lines: The stamped 34S, 29Si and 30Si lines of the reading:
        ``label``, ``element``, ``batch_peak_id``, ``holds``, ``judged`` and
        the stamping's ``batch_peak_run_id``.
    :ivar twin: The label twin, where it was read present: ``present`` of
        ``judged`` samples, the twin's ``batch_peak_id`` and the checks' run.
    :ivar stamp: Where the batch peak was stamped as another's 37Cl or 81Br
        line: ``label``, ``element``, the parent's ``parent_batch_peak_id``,
        ``parent_formula`` and ``parent_ion_formula``, ``n_stamped`` and the
        stamping's run.
    """

    lock: Optional[dict] = None
    lines: tuple[dict, ...] = ()
    twin: Optional[dict] = None
    stamp: Optional[dict] = None

    def __bool__(self) -> bool:
        return bool(self.lock or self.lines or self.twin or self.stamp)

    def to_json(self) -> dict:
        """The facts as the batch level record carries them."""
        return {
            key: value
            for key, value in (
                ("halogen_lock", self.lock),
                ("stamped_lines", list(self.lines)),
                ("label_twin", self.twin),
                ("stamped_as", self.stamp),
            )
            if value
        }

    @classmethod
    def from_json(cls, record: Any) -> "BatchFacts":
        """The facts a batch level record carries (:meth:`to_json`)."""
        if not isinstance(record, dict):
            return cls()
        lines = record.get("stamped_lines")
        return cls(
            lock=_dict_or_none(record.get("halogen_lock")),
            lines=tuple(line for line in lines or [] if isinstance(line, dict))
            if isinstance(lines, list)
            else (),
            twin=_dict_or_none(record.get("label_twin")),
            stamp=_dict_or_none(record.get("stamped_as")),
        )


def _dict_or_none(value: Any) -> Optional[dict]:
    return value if isinstance(value, dict) and value else None


def read_facts(anchor: Any, anchors_by_id: dict[str, Any]) -> BatchFacts:
    """The batch's isotope checks of an anchor's reading, each read as the
    ledger serves it, so only while the record it came from stands.

    :param anchor: The batch peak.
    :param anchors_by_id: The batch's anchors, where a stamp's parent is.
    """
    checks = standing_batch_checks_record(anchor) or {}
    run_id = checks.get("batch_peak_run_id")
    lock = checks.get("halogen_lock")
    lock_fact = None
    if (
        isinstance(lock, dict)
        and lock.get("verdict") == LOCK_LOCKED
        and lock.get("element") in HALOGEN_LINES
    ):
        lock_fact = {
            "element": lock["element"],
            "label": lock.get("label") or HALOGEN_LINES[lock["element"]],
            "batch_peak_id": lock.get("batch_peak_id"),
            "holds": lock.get("holds"),
            "judged": lock.get("judged"),
            "stamping_run_id": lock.get("stamping_run_id"),
            "batch_peak_run_id": run_id,
        }
    twin = checks.get("label_twin")
    twin_fact = None
    if isinstance(twin, dict) and twin.get("verdict") == TWIN_PRESENT:
        twin_fact = {
            "present": twin.get("present"),
            "judged": twin.get("judged"),
            "batch_peak_id": twin.get("batch_peak_id"),
            "batch_peak_run_id": run_id,
        }
    stamping = standing_diagnostic_isotopologues_record(anchor) or {}
    lines = tuple(
        {
            "label": entry["label"],
            "element": LINE_ELEMENTS[entry["label"]],
            "batch_peak_id": entry.get("batch_peak_id"),
            "holds": entry.get("holds"),
            "judged": entry.get("judged"),
            "batch_peak_run_id": stamping.get("batch_peak_run_id"),
        }
        for entry in stamping.get("predicted") or []
        if isinstance(entry, dict)
        and entry.get("stamped")
        and entry.get("label") in LINE_ELEMENTS
    )
    return BatchFacts(
        lock=lock_fact,
        lines=lines,
        twin=twin_fact,
        stamp=stamped_as(anchor, anchors_by_id),
    )


def stamped_as(anchor: Any, anchors_by_id: dict[str, Any]) -> Optional[dict]:
    """Where an anchor was stamped as another batch peak's 37Cl or 81Br line,
    while that stamp stands: its parent's standing diagnostic isotopologues
    record, of the same stamping run, names the anchor as that line, stamped.
    None otherwise."""
    stamp = stamped_isotopologue_of_record(anchor)
    if stamp is None or stamp.get("label") not in HALOGEN_ELEMENTS:
        return None
    parent = anchors_by_id.get(stamp["batch_peak_id"])
    record = (
        standing_diagnostic_isotopologues_record(parent) if parent is not None else None
    )
    if record is None or record.get("batch_peak_run_id") != stamp.get(
        "batch_peak_run_id"
    ):
        return None
    if not any(
        isinstance(entry, dict)
        and entry.get("stamped")
        and entry.get("label") == stamp["label"]
        and entry.get("batch_peak_id") == anchor.batch_peak_id
        for entry in record.get("predicted") or []
    ):
        return None
    return {
        "label": stamp["label"],
        "element": HALOGEN_ELEMENTS[stamp["label"]],
        "parent_batch_peak_id": stamp["batch_peak_id"],
        "parent_formula": parent.consensus_formula,
        "parent_ion_formula": parent.consensus_ion_formula,
        "n_stamped": len(stamp.get("stamps") or {}),
        "batch_peak_run_id": stamp.get("batch_peak_run_id"),
    }


# --- one row under the batch's facts ----------------------------------------------


def _fact(fact: str, record: str, detail: Optional[str] = None) -> dict:
    return {"fact": fact, "record": record, **({"detail": detail} if detail else {})}


def _track(fact: dict, held: str = "holds") -> str:
    """How many samples a fact held in, in words."""
    return f"{fact.get(held)} of {fact.get('judged')} samples"


def batch_test(rival: dict, facts: BatchFacts) -> Optional[dict]:
    """The batch's test that puts an open rival out, or None.

    :param rival: The rival's entry in a row's level record.
    :param facts: The batch's facts.
    :return: The rival's ``test``, ``record``, ``detail`` and ``by``.
    """
    if rival.get("monoisotopic") is False:
        return None
    ion = rival.get("ion_formula") or rival.get("formula")
    lock = facts.lock
    if lock is not None and holds(ion, lock["element"]) is False:
        return {
            "test": TEST_ISOTOPES,
            "record": RECORD_LOCK,
            "by": BY_LOCK,
            "detail": (
                f"the batch's halogen lock: the reading's {lock['label']} line is "
                f"stamped in {_track(lock)} at the exact spacing, and {ion} "
                f"carries no {_WORDS[lock['element']]} to make it"
            ),
        }
    for line in facts.lines:
        if holds(ion, line["element"]) is False:
            return {
                "test": TEST_ISOTOPES,
                "record": RECORD_LINES,
                "by": BY_LINE,
                "detail": (
                    f"the batch stamped the reading's {line['label']} line in "
                    f"{_track(line)}, and {ion} carries no "
                    f"{_WORDS[line['element']]} to make it"
                ),
            }
    return None


def twin_pin(ion_formula: Optional[str], facts: BatchFacts) -> Optional[str]:
    """The evidence a present label twin pins a labelled ion's split by, or
    None where it pins nothing: no twin read present, or an ion that carries
    no label."""
    twin = facts.twin
    if twin is None or not carries_label(ion_formula):
        return None
    return (
        "the split is pinned by the label twin: the batch reads the 14N twin "
        f"present in {_track(twin, 'present')} at the label's impurity share, "
        "so the label tells the reagent's atoms from the molecule's"
    )


def _excluded(rivals: Sequence[dict]) -> str:
    """How many rivals each kind of test put out, in one clause."""
    out = [rival for rival in rivals if rival.get("test") != OPEN]
    by_batch = sum(1 for rival in out if rival.get("by"))
    kinds = Counter(rival.get("test") for rival in out if not rival.get("by"))
    words = {
        TEST_MASS: "by mass",
        TEST_ISOTOPES: "by isotopes",
        TEST_SECOND_CHANNEL: "by a second channel",
    }
    parts = [
        f"{_count_word(kinds[test])} {words[test]}" for test in words if kinds[test]
    ]
    if by_batch:
        parts.append(f"{_count_word(by_batch)} by the batch's isotope checks")
    return f"{_plural(len(out), 'rival')} out: {', '.join(parts)}"


#: A 5a lift's part naming the open rivals no pass measured, in the words of
#: the run that stored it: the current run's and an older run's.
_UNMEASURED_LIFTS = (LIFT_RIVALS_UNMEASURED, LIFT_RIVALS_UNMEASURED_BEFORE)

#: What a 5a row whose run recorded no split would need to read at 4.
_LIFT_NO_SPLIT = "assign the sample again, so its run records the split"


def _narrowed_lift(record: dict, still: Sequence[dict]) -> Optional[str]:
    """A 5a row's lift once the batch put some of its open rivals out: the
    run's own lift (``identification_level._open_lift``) asking only of the
    rivals left open.

    The part naming the measured open rivals names those still open, and goes
    where none is; the part naming the unmeasured rivals goes where every
    unmeasured rival is out. The parts on the rivals the row does not list stay: the batch tests
    none of those.

    :param record: The row's record at 5a, its rivals as its run judged them.
    :param still: The rivals left open under the batch's tests.
    """
    opened = [
        rival
        for rival in record.get("rivals") or []
        if isinstance(rival, dict) and rival.get("test") == OPEN
    ]
    # Named as ``_open_lift`` names them: the first three measured ones.
    named, left = (
        ", ".join([str(rival.get("formula")) for rival in rivals if "fit" in rival][:3])
        for rivals in (opened, still)
    )
    parts: list[str] = []
    for part in str(record.get("lift") or "").split("; "):
        if not part:
            continue
        if part in _UNMEASURED_LIFTS:
            if any("fit" not in rival for rival in still):
                parts.append(part)
        elif named and (
            part.startswith(f"exclude {named}:") or part.endswith(f"exclude {named}")
        ):
            if left:
                parts.append(part.replace(f"exclude {named}", f"exclude {left}", 1))
        else:
            parts.append(part)
    return "; ".join(parts) or None


def _refuted(
    record: dict, ion_formula: Optional[str], facts: BatchFacts
) -> Optional[dict]:
    """A row's record at 5b where the batch stamped its peak as another's
    37Cl or 81Br line its own ion cannot make; None otherwise."""
    stamp = facts.stamp
    if stamp is None or holds(ion_formula, stamp["element"]) is not False:
        return None
    parent = stamp.get("parent_ion_formula") or stamp.get("parent_formula")
    line = (
        f"the batch stamped this peak as the {stamp['label']} line of {parent} in "
        f"{stamp.get('n_stamped')} samples, a line {ion_formula} cannot make, "
        f"holding no {_WORDS[stamp['element']]}"
    )
    own = record.get("level")
    evidence = f"refuted by the batch's isotopes: {line}"
    if record.get("evidence"):
        evidence += (
            f"; {record['evidence']}"
            if own == LEVEL_5B
            else f"; its run stated {own}: {record['evidence']}"
        )
    rival = {
        "formula": stamp.get("parent_formula"),
        "ion_formula": stamp.get("parent_ion_formula"),
        "record": RECORD_STAMP,
        "test": OPEN,
        "monoisotopic": False,
        "line": stamp["label"],
        "detail": (
            f"the batch reads the peak as the {stamp['label']} line of {parent}, "
            "which that reading makes"
        ),
    }
    return {
        **{key: value for key, value in record.items() if key != SPLIT},
        "level": LEVEL_5B,
        "evidence": evidence,
        "lift": (
            f"another reading of the peak: the {stamp['label']} line of {parent}, "
            "as the batch stamped it"
        ),
        "facts": [
            *(record.get("facts") or []),
            _fact(
                "stamped_halogen_line",
                RECORD_STAMP,
                f"{stamp['label']} of {stamp.get('parent_batch_peak_id')}",
            ),
        ],
        "rivals": [rival, *(record.get("rivals") or [])],
        "stated": own,
        "moved_by": [BY_STAMP],
    }


def _lifted(
    record: dict, ion_formula: Optional[str], facts: BatchFacts
) -> Optional[dict]:
    """A row at 5a read under the batch's tests: 4a or 4b where they put every
    rival it lists out, its split as its run read it (:data:`SPLIT`) unless
    the label twin pins it; 5a with its rivals re-read where some are left;
    None where the batch puts none out."""
    rivals = record.get("rivals")
    if not isinstance(rivals, list):
        return None
    judged: list[dict] = []
    moved_by: set[str] = set()
    for rival in rivals:
        if not isinstance(rival, dict):
            continue
        test = batch_test(rival, facts) if rival.get("test") == OPEN else None
        if test is not None:
            moved_by.add(test["by"])
            rival = {**{k: v for k, v in rival.items() if k != "held_by"}, **test}
        judged.append(rival)
    if not moved_by:
        return None
    still = [rival for rival in judged if rival.get("test") == OPEN]
    unlisted = int(record.get("rivals_unlisted") or 0)
    added = [
        _fact(rival["test"], rival["record"], rival.get("formula"))
        for rival in judged
        if rival.get("by")
    ]
    base = {key: value for key, value in record.items() if key != SPLIT}
    split = record.get(SPLIT)
    if (
        still
        or unlisted
        or not isinstance(split, dict)
        or not is_level(split.get("level"))
    ):
        clauses = []
        if still:
            names = ", ".join(str(rival.get("formula")) for rival in still[:3])
            more = len(still) - 3
            clauses.append(
                f"{_plural(len(still), 'rival')} left open: {names}"
                + (f" and {more} more" if more > 0 else "")
            )
        if unlisted:
            clauses.append(f"{_plural(unlisted, 'rival')} not listed, not tested")
        if not clauses:
            clauses.append("its run did not record the split it would read at 4")
        clauses.append(_excluded(judged))
        return {
            **base,
            "level": LEVEL_5A,
            "evidence": "; ".join(clauses),
            "lift": _narrowed_lift(record, still)
            if still or unlisted
            else _LIFT_NO_SPLIT,
            "rivals": judged,
            "facts": [*(record.get("facts") or []), *added],
            "stated": record.get("level"),
            "moved_by": sorted(moved_by),
        }
    level, evidence, lift = split["level"], split.get("evidence"), split.get("lift")
    pinned = twin_pin(ion_formula, facts) if level == LEVEL_4B else None
    if pinned is not None:
        level, evidence, lift = LEVEL_4A, pinned, LIFT_4A
        moved_by.add(BY_TWIN)
    return {
        **base,
        "level": level,
        "evidence": f"{_excluded(judged)}; {evidence}",
        "lift": lift,
        "rivals": judged,
        "facts": [
            *(record.get("facts") or []),
            *added,
            *([split["fact"]] if isinstance(split.get("fact"), dict) else []),
            *([_fact(BY_TWIN, RECORD_TWIN)] if pinned is not None else []),
        ],
        "stated": record.get("level"),
        "moved_by": sorted(moved_by),
    }


def overlay(
    record: Any, ion_formula: Optional[str], facts: BatchFacts
) -> Optional[dict]:
    """A row's level record read under the batch's facts; None where they
    move nothing.

    The refutation first (a 5b goes before every other letter, point 4), then
    a 5a's rivals put out by the lock and the stamped lines, then a 4b's split
    pinned by the label twin. A row at 4a or above keeps its level: the
    batch's lifts end at 4 and 4a. A 5b stays 5b.

    :param record: The row's record (``identification_level``).
    :param ion_formula: The row's ion.
    :param facts: The batch's facts (:func:`read_facts`).
    :return: The record under the batch's facts, with ``stated``, the level
        its run stated, and ``moved_by``, what moved it.
    """
    if not isinstance(record, dict) or not is_level(record.get("level")) or not facts:
        return None
    refuted = _refuted(record, ion_formula, facts)
    if refuted is not None:
        return engine_stated(refuted)
    level = record["level"]
    if level == LEVEL_5A:
        lifted = _lifted(record, ion_formula, facts)
        return engine_stated(lifted) if lifted is not None else None
    if level == LEVEL_4B:
        pinned = twin_pin(ion_formula, facts)
        if pinned is None:
            return None
        return engine_stated(
            {
                **record,
                "level": LEVEL_4A,
                "evidence": f"{pinned}; its run stated 4b: {record.get('evidence')}",
                "lift": LIFT_4A,
                "facts": [*(record.get("facts") or []), _fact(BY_TWIN, RECORD_TWIN)],
                "stated": LEVEL_4B,
                "moved_by": [BY_TWIN],
            }
        )
    return None


# --- the batch peak's level -------------------------------------------------------


@dataclass(frozen=True)
class MemberLevel:
    """What a member's run stated of the consensus formula.

    :ivar level: The run row's ``level`` column.
    :ivar record: Its ``provenance.identification_level``, where it carries
        one.
    :ivar ion_formula: The run row's ion.
    :ivar intensity: The member's height, which picks the member whose lines
        the batch's level shows.
    """

    sample_item_id: str
    level: Optional[str]
    record: Optional[dict]
    ion_formula: Optional[str]
    intensity: Optional[float] = None


def batch_level(
    anchor: Any,
    members: Sequence[MemberLevel],
    facts: BatchFacts,
    batch_peak_run_id: Optional[str] = None,
) -> Optional[dict]:
    """The level a batch peak's reading states across the batch (module
    docstring); None where no member's run stated one.

    :param anchor: The batch peak.
    :param members: Its members whose run committed the consensus formula.
    :param facts: The batch's facts (:func:`read_facts`).
    :param batch_peak_run_id: The run doing it, for the record.
    :return: The record ``provenance.identification_level`` holds: the
        level, the lines, facts and rivals of the member shown (``read_from``),
        the members' own levels (``members``) and, where the batch moved any,
        how many (``members_moved``), their levels under the batch's facts
        (``members_under_batch``) and what moved those at its level
        (``moved_by``).
    """
    stated: list[tuple[MemberLevel, dict]] = []
    for member in members:
        if not is_level(member.level):
            continue
        record = member.record
        if not isinstance(record, dict) or record.get("level") != member.level:
            record = {"level": member.level}
        stated.append((member, overlay(record, member.ion_formula, facts) or record))
    if not stated:
        return None
    own = Counter(member.level for member, _ in stated)
    under = Counter(record["level"] for _, record in stated)
    level = members_level(under)
    # The lines shown are a member's the batch moved to that level where one
    # is, so the evidence names the fact that earned it; else any member's.
    at_level = [pair for pair in stated if pair[1]["level"] == level]
    moved_here = [pair for pair in at_level if pair[1]["level"] != pair[0].level]
    member, shown = max(
        moved_here or at_level,
        key=lambda pair: (pair[0].intensity or 0.0, pair[0].sample_item_id),
    )
    moved = sum(1 for member_, record in stated if record["level"] != member_.level)
    moved_by = sorted({by for _, record in moved_here for by in record["moved_by"]})
    clause = (
        f"{under[level]} of {len(stated)} members read {level} under the batch's "
        "isotope checks"
        if moved
        else f"{under[level]} of {len(stated)} members' runs state {level}"
    )
    evidence = shown.get("evidence")
    record = {
        "formula": anchor.consensus_formula,
        "ion_formula": anchor.consensus_ion_formula,
        "ionization_mechanism_id": anchor.ionization_mechanism_id,
        "level": level,
        "evidence": f"{evidence}; {clause}" if evidence else clause,
        "lift": shown.get("lift"),
        "facts": shown.get("facts") or [],
        "rivals": shown.get("rivals") or [],
        "members": {key: own[key] for key in LEVELS if own[key]},
        "members_moved": moved,
        "read_from": member.sample_item_id,
        "batch_facts": facts.to_json(),
        "batch_peak_run_id": batch_peak_run_id,
    }
    if shown.get("rivals_unlisted"):
        record["rivals_unlisted"] = shown["rivals_unlisted"]
    if moved_by:
        record["moved_by"] = moved_by
    if moved:
        record["members_under_batch"] = {
            key: under[key] for key in LEVELS if under[key]
        }
    return engine_stated(record)


def row_batch_level(row: dict, anchor: Any) -> Optional[dict]:
    """The batch's level on a sample's row, where its facts move the row's own.

    The row's own record read under the facts its batch peak's level read
    (:func:`overlay`), on a monoisotopic row committing the batch peak's
    consensus formula: beside the row's own level, marked ``borrowed`` and
    naming the batch peak and the batch's own level. None where the batch
    moves nothing, the row reads another formula, or no level of the batch
    stands.

    :param row: The sample's row, with ``role``, ``assigned_formula``,
        ``ion_formula``, ``level`` and ``provenance``.
    :param anchor: The anchor its peak sits in, or None.
    """
    if anchor is None or row.get("role") != ROLE_M0:
        return None
    record = standing_batch_level_record(anchor)
    if record is None or record.get("formula") != row.get("assigned_formula"):
        return None
    own = (row.get("provenance") or {}).get(IDENTIFICATION_LEVEL)
    if not isinstance(own, dict) or own.get("level") != row.get("level"):
        return None
    under = overlay(
        own, row.get("ion_formula"), BatchFacts.from_json(record.get("batch_facts"))
    )
    if under is None or under["level"] == own["level"]:
        return None
    return {
        **under,
        "borrowed": True,
        "batch_peak_id": anchor.batch_peak_id,
        "batch_level": record["level"],
    }


def level_columns(record: Optional[dict]) -> dict:
    """What the batch ledger serves of a batch peak's own level: ``level``,
    ``level_evidence`` and ``level_lift``, as a run's row carries them, and
    the whole record as ``identification_level``; each None where no level of
    the batch stands for the consensus reading
    (:func:`standing_batch_level_record`). The ledger's ``member_level`` and
    ``member_levels`` stay beside them: the members' split."""
    if not isinstance(record, dict) or not is_level(record.get("level")):
        return {
            "level": None,
            "level_evidence": None,
            "level_lift": None,
            "identification_level": None,
        }
    return {
        "level": record["level"],
        "level_evidence": record.get("evidence"),
        "level_lift": record.get("lift"),
        "identification_level": record,
    }


def batch_level_fields(anchor: Any) -> dict:
    """The batch export's three columns of an anchor's own level:
    ``batch_level``, ``batch_level_evidence`` and ``batch_level_lift``."""
    record = standing_batch_level_record(anchor) or {}
    return {
        "batch_level": record.get("level"),
        "batch_level_evidence": record.get("evidence"),
        "batch_level_lift": record.get("lift"),
    }


# --- the stage ---------------------------------------------------------------------


def reads_a_formula(anchor: Any) -> bool:
    """Whether an anchor's reading commits a formula a level is stated of: a
    monoisotopic reading of a formula that no source role accounts for."""
    return (
        is_monoisotopic(anchor)
        and bool(anchor.consensus_formula)
        and not anchor.consensus_role
    )


#: Anchors per member read: one row per (anchor, sample), each with its level
#: record, so the read is bounded however large the batch.
_CHUNK = 500


async def _members(session, anchors: Sequence[Any]) -> dict[str, list[MemberLevel]]:
    """Each anchor's members whose run committed its consensus formula, with
    the level record their row carries: one statement per chunk of anchors,
    the record read out of the provenance alone."""
    found: dict[str, list[MemberLevel]] = defaultdict(list)
    ids = sorted(anchor.batch_peak_id for anchor in anchors)
    for start in range(0, len(ids), _CHUNK):
        rows = (
            await session.execute(
                select(
                    BatchPeakOccurrence.batch_peak_id,
                    BatchPeakOccurrence.sample_item_id,
                    BatchPeakOccurrence.intensity,
                    PeakAssignment.level,
                    PeakAssignment.ion_formula,
                    PeakAssignment.provenance[IDENTIFICATION_LEVEL].label("record"),
                )
                .join(
                    BatchPeak,
                    BatchPeak.batch_peak_id == BatchPeakOccurrence.batch_peak_id,
                )
                .join(
                    PeakAssignment,
                    PeakAssignment.peak_assignment_id
                    == BatchPeakOccurrence.peak_assignment_id,
                )
                .where(
                    BatchPeak.batch_peak_id.in_(ids[start : start + _CHUNK]),
                    PeakAssignment.level.is_not(None),
                    PeakAssignment.assigned_formula == BatchPeak.consensus_formula,
                )
            )
        ).all()
        for row in rows:
            found[row.batch_peak_id].append(
                MemberLevel(
                    sample_item_id=row.sample_item_id,
                    level=row.level,
                    record=row.record if isinstance(row.record, dict) else None,
                    ion_formula=row.ion_formula,
                    intensity=row.intensity,
                )
            )
    return found


def read_levels(
    anchors: Sequence[Any],
    members: dict[str, list[MemberLevel]],
    *,
    batch_peak_run_id: Optional[str] = None,
) -> tuple[dict[str, dict], dict]:
    """Every batch peak's level, and the run's counts.

    :param anchors: The batch's anchors.
    :param members: Anchor id -> its members (:func:`_members`).
    :param batch_peak_run_id: The run doing it, for the records.
    :return: Anchor id -> its record, and the counts.
    """
    by_id = {anchor.batch_peak_id: anchor for anchor in anchors}
    records: dict[str, dict] = {}
    counts: Counter = Counter()
    levels: Counter = Counter()
    for anchor in anchors:
        if not reads_a_formula(anchor):
            continue
        facts = read_facts(anchor, by_id)
        record = batch_level(
            anchor,
            members.get(anchor.batch_peak_id, []),
            facts,
            batch_peak_run_id=batch_peak_run_id,
        )
        if record is None:
            continue
        records[anchor.batch_peak_id] = record
        levels[record["level"]] += 1
        counts["members_moved"] += record["members_moved"]
        before = members_level(record["members"])
        if record["level"] == before:
            continue
        counts["refuted" if record["level"] == LEVEL_5B else "lifted"] += 1
        for by in record.get("moved_by") or []:
            counts[f"by_{by}"] += 1
    summary = {
        "anchors_read": len(records),
        "levels": {key: levels[key] for key in LEVELS if levels[key]},
        **{
            key: counts[key]
            for key in (
                "lifted",
                "refuted",
                "members_moved",
                f"by_{BY_LOCK}",
                f"by_{BY_LINE}",
                f"by_{BY_TWIN}",
                f"by_{BY_STAMP}",
            )
        },
    }
    return records, summary


def still_read(anchors: Iterable[Any], records: dict[str, dict]) -> dict[str, dict]:
    """The records whose batch peak still reads what each was read for, as
    the write finds the ledger: a fold or a curation can land between the read
    and the write, outside and under the fold lock."""
    by_id = {anchor.batch_peak_id: anchor for anchor in anchors}
    return {
        anchor_id: record
        for anchor_id, record in records.items()
        if anchor_id in by_id
        and batch_checks_record_reads(
            record,
            by_id[anchor_id].consensus_ion_formula,
            by_id[anchor_id].consensus_formula,
            by_id[anchor_id].ionization_mechanism_id,
        )
    }


async def _write_records(sample_batch_id: str, records: dict[str, dict]) -> int:
    """Replace the batch's level records with this reading's, under the fold
    lock; every record an earlier one left comes off first.

    :return: How many batch peaks' records were left out, their reading having
        moved or gone since it was read.
    """
    async with async_session() as session:
        await _acquire_batch_fold_lock(session, sample_batch_id)
        anchors = (
            (
                await session.execute(
                    select(BatchPeak).where(
                        BatchPeak.sample_batch_id == sample_batch_id
                    )
                )
            )
            .scalars()
            .all()
        )
        kept = still_read(anchors, records)
        for anchor in anchors:
            record = kept.get(anchor.batch_peak_id)
            provenance = dict(anchor.provenance or {})
            if record is None:
                if BATCH_LEVEL_KEY not in provenance:
                    continue
                provenance.pop(BATCH_LEVEL_KEY)
            else:
                provenance[BATCH_LEVEL_KEY] = record
            anchor.provenance = provenance
        await touch_ledger(session, sample_batch_id)
        await session.commit()
    return len(records) - len(kept)


async def run_batch_levels(
    sample_batch_id: str, batch_peak_run_id: Optional[str] = None
) -> dict:
    """State every batch peak's level and record it on the anchors.

    :return: The run's summary (:func:`read_levels`).
    """
    async with async_session() as session:
        anchors = (
            (
                await session.execute(
                    select(BatchPeak).where(
                        BatchPeak.sample_batch_id == sample_batch_id
                    )
                )
            )
            .scalars()
            .all()
        )
        members = await _members(
            session, [anchor for anchor in anchors if reads_a_formula(anchor)]
        )
    records, summary = read_levels(
        anchors, members, batch_peak_run_id=batch_peak_run_id
    )
    summary["anchors_moved"] = await _write_records(sample_batch_id, records)
    return summary


def levels_outcome(counts: dict, sample_batch_id: str) -> dict:
    """The controller result the background-task decorator reports from."""
    by = ", ".join(
        f"{counts[f'by_{key}']} by {word}"
        for key, word in (
            (BY_LOCK, "a halogen lock"),
            (BY_LINE, "a stamped line"),
            (BY_TWIN, "the label twin"),
        )
        if counts.get(f"by_{key}")
    )
    return {
        "status": "success",
        "message": (
            f"Read the identification level of {counts['anchors_read']} batch "
            f"peak{'s' if counts['anchors_read'] != 1 else ''}: {counts['lifted']} "
            "lifted by the batch's isotope checks"
            + (f" ({by})" if by else "")
            + f", {counts['refuted']} refuted by a stamped halogen line. "
            "No tier moved."
        ),
        "data": counts,
        "_notification_data": {"sample_batch_id": sample_batch_id},
    }


@api_controller_background_task(
    success_notification_rooms=["sample_batch_id"],
    success_reload=[("peak_assignment", "sample_batch_id")],
    error_notification_rooms=["sample_batch_id"],
)
async def read_batch_levels(
    sample_batch_id: str,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
    batch_peak_run_id: str | None = None,
) -> dict:
    """State the batch's identification levels, inside the pass's batch run
    (``batch_peak_run_id``), which every record names. Writes no member and
    no per-sample run; emits ``peak_assignment_reload`` so the ledgers
    refresh."""
    counts = await run_batch_levels(
        sample_batch_id, batch_peak_run_id=batch_peak_run_id
    )
    return levels_outcome(counts, sample_batch_id)


__all__ = [
    "BatchFacts",
    "MemberLevel",
    "batch_level",
    "batch_level_fields",
    "batch_level_record_of",
    "batch_test",
    "level_columns",
    "levels_outcome",
    "overlay",
    "read_batch_levels",
    "read_facts",
    "read_levels",
    "row_batch_level",
    "run_batch_levels",
    "stamped_as",
    "standing_batch_level_record",
    "still_read",
    "twin_pin",
]
