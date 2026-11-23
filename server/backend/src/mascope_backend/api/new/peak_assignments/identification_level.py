"""What a reader may state of a committed row: its identification level.

The tier says how confident the run is in a formula reading. It cannot say
what a reader may state of the row, or which test is missing for more. The
identification level does, in the scale peaky and Mascope share (decision 38
of the assignment quality plan; ``docs/dev/assignment_confidence.md``, "P4
design: the identification level"): Schymanski's five numbers with their 2014
meaning, and a letter for the kind of evidence that earned each.

== =========================================================================
5b  A measured mass, the reading refuted by its isotopes, chemically
    implausible, or untestable.
5a  A measured mass; a rival on the shortlist that no test excluded, listed.
4b  The ion's formula: every rival excluded, the molecule-reagent split open.
4a  The neutral formula: every rival excluded, the split pinned.
3a  Data and list agree.          (later steps: 5.8b, 5.8d)
3b  One functional group.         (5.8b, 5.8e)
3c  A context list.               (5.8b)
3d  A homologue series.           (5.8d)
2a  A library spectrum.           (5.8e, offered to a person)
2b  Diagnostic evidence.          (5.8b, 5.8e)
1   Confirmed, by a person only.  (5.8f)
== =========================================================================

This module states the formula levels a sample's run can state (step 5.8):
5b, 5a, 4b and 4a. The letters name the kind of evidence, not a rank inside a
number. Every one is read from facts the run already records; nothing here
measures anything new about the winner, and the level moves no tier, role,
member or claim (the plan owner's call of 2026-11-17: the level and the tier
cross, and the crossing is read on the bench's card before any tier rule
follows from it, MAS-945).

Each committed monoisotopic row is read in order:

- **5b, refuted or untestable.** Refuted by its isotopes: a line of its
  predicted envelope the detectability gate says the spectrum should show is
  absent (``tiering.REASON_ABSENT_LINE``, step 3.4e). Implausible: its
  plausibility is under the floor of the P1 layer
  (``heuristic_filter.PLAUSIBILITY_FLOOR``), a graph no molecule has.
  Untestable: the instrument did not separate its peak from a neighbour
  (``tiering.REASON_UNRESOLVED_PEAK``, step 3.4f), so neither its mass nor its
  height was measured. A stamped line under a halogen-free reading is the
  batch's fact, read from step 5.8c.
- **5a, rivals remain.** The row's measured shortlist holds a rival no test
  excluded; the row lists each. The shortlist is the row's rival set
  (``rival_set.RIVAL_SET``), the one the density and the arbitration read:
  every other ion the run measured on the peak, at the model its caps read.
  A Stage A row's is complete where the formula search measured its peak
  (``engine.GRID_RIVALS``); one the search did not measure was competed
  against the lists' formulas alone. An untargeted winner's is complete only
  where step 4.15 measured its formula-only shortlist
  (``engine.MEASURED_SHORTLIST``), which is off by default; a row without one
  reads 5a with "rivals not measured" and the lift "measure the shortlist",
  since the level does not assume an exclusion it did not see. Nor does it
  assume one of a rival the set does not name, or, on an untargeted winner, a
  formula the cap on stored alternatives left out (``engine.RIVALS_PAST_CAP``).
- **4, every rival excluded.** Each rival is out on a named test:

  - ``mass``: its fit at the run's width, mass and isotope pattern together,
    is under the winner's by more than the arbitration's tie margin
    (``arbitration.tie_gap``), or no channel of the run places it on the peak
    at all. The test reads the fits, where the density reads the evidence
    (fit x plausibility): a test excludes a rival on what the spectrum
    measured, and plausibility excludes nothing (below);
  - ``isotopes``: a line of the winner's own pattern is committed with it and
    tracks it (``tiering.follows_its_owner``) and names a heavy isotope of an
    element the rival's ion lacks, or a line the rival's own envelope predicts
    is absent where the detectability gate says it would show (the absent-line
    rule asked of the rival);
  - ``second_channel``: another channel of the run commits the winner's
    neutral on a separated peak and no channel commits the rival's (step 2.3).
    Not on a TOF, where a second reagent lifts a row only where the channels
    co-vary (step 5.8d).

  A rival held back only by a prior (step 3.6) or by plausibility is not out:
  it stays listed, *open*, with what held it. ``no_close_rival`` is not read
  as an exclusion: it says the peak's evidence separated the winner, not that
  each rival failed a test, so the mapping reads the shortlist.
- **4b or 4a.** 4b where the cross-channel pass found the ion reads as another
  molecule too and nothing settled which (``ambiguous_nitrogen``,
  ``ambiguous_adduct``, a reference mirror row's other readings included). 4a
  where the split is pinned: something settled it (``same_ion_settled``), the
  run's reagent is labelled, a second channel commits the neutral (read as
  the rival test reads one: on a separated peak, and not on a TOF), or no
  other split of the ion was found under the run's channels.

An isotopologue row shows its owner's level. Reagent and artifact rows commit
no formula and carry none.

Each row records its level under :data:`IDENTIFICATION_LEVEL`: ``level``,
``evidence`` (one line naming what earned it), ``lift`` (written from the
tests it is missing), the ``facts`` read, each named with the record it was
read from, the ``rivals``, each with the ``test`` that excluded it or
``open``, and ``rivals_unlisted``. The run records the rules' version
(``level_rules_version``) and the count per level with its tiering record.

The rules are versioned (:data:`LEVEL_RULES_VERSION`) and bumped when a fact
they read, a rule in them, or the order that picks one letter of a number
changes, as the tier rule set is. It is not ``level_mapping_version``, the
published name of the claims' evidence-to-level mapping.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from mascope_backend.api.new.peak_assignments.cross_channel import (
    AMBIGUITY_REASONS,
    CHANNELS_FOR_CORROBORATION,
    SAME_ION,
    SAME_ION_SETTLED,
    SETTLED_BY_PARTNER,
    SETTLED_BY_RADICAL,
    SETTLED_BY_SECOND_CHANNEL,
    SETTLED_BY_TARGET_LIBRARY,
    carries_label,
    channels_by_neutral,
)
from mascope_backend.api.new.peak_assignments.engine import (
    GRID_RIVALS,
    MEASURED_SHORTLIST,
    RIVALS_PAST_CAP,
    formula_identity,
    ion_identity,
)
from mascope_backend.api.new.peak_assignments.mass_gate import SpectrumLines
from mascope_backend.api.new.peak_assignments.rival_set import RIVAL_SET
from mascope_backend.api.new.peak_assignments.row_terms import (
    ROLE_ISO_CHILD,
    ROLE_M0,
    SOURCE_DATABASE,
    SOURCE_UNTARGETED,
)
from mascope_backend.api.new.peak_assignments.tiering import (
    LINE_ABSENT,
    PREDICTED_ENVELOPE,
    REASON_ABSENT_LINE,
    REASON_UNRESOLVED_PEAK,
    SEPARATED_THROUGH,
    LineGate,
    ambiguity_detail,
    expected_lines,
    follows_its_owner,
    isotope_lines,
    settled_detail,
)
from mascope_tools.composition.arbitration import tie_gap as arbitration_tie_gap
from mascope_tools.composition.heuristic_filter import (
    PLAUSIBILITY_FLOOR,
    element_counts,
    formula_plausibility,
)
from mascope_tools.composition.mechanism_notation import parse_mechanism
from mascope_tools.composition.profiles import ChemistryContext


#: Bumped whenever a fact the rules read, a rule, or the order that picks one
#: letter of a number changes. Recorded on the run beside the tier rule set
#: (``tiering.TIERING_RULES_VERSION``), as ``level_rules_version``, because a
#: level is only comparable across runs together with the rules that stated
#: it. 2 (MAS-1072): the shortlist is the row's rival set, the rivals its
#: density counts, at the model its caps read.
LEVEL_RULES_VERSION = 2

#: The provenance key a committed row's level is recorded under.
IDENTIFICATION_LEVEL = "identification_level"

#: The key the run's tiering record carries the level pass's summary under.
LEVEL_SUMMARY = "identification_level"

#: The row's column the level alone is stored in (``PeakAssignment.level``),
#: beside its tier, for the ledgers to filter and count on.
LEVEL_COLUMN = "level"

#: The scale's vocabulary, every level a row, an import or a claim may state:
#: the number first, then its letter. The letters name a kind of evidence, not
#: a rank inside the number, so this order sorts and nothing more.
LEVELS = ("1", "2a", "2b", "3a", "3b", "3c", "3d", "4a", "4b", "5a", "5b")

#: The levels this step states; the rest come with the steps that read the
#: evidence they name (5.8b to 5.8f).
LEVEL_4A = "4a"
LEVEL_4B = "4b"
LEVEL_5A = "5a"
LEVEL_5B = "5b"

#: The tests a rival can be excluded on, and the word for one none excluded.
TEST_MASS = "mass"
TEST_ISOTOPES = "isotopes"
TEST_SECOND_CHANNEL = "second_channel"
OPEN = "open"

#: What held an open rival back without excluding it: the plausibility its
#: evidence was weighted by, or a prior of the run's context (step 3.6).
HELD_BY_PLAUSIBILITY = "plausibility"
HELD_BY_PRIOR = "prior"

#: The words each element is named by in a sentence.
_ELEMENT_WORDS = {
    "C": "carbon",
    "H": "hydrogen",
    "N": "nitrogen",
    "O": "oxygen",
    "S": "sulfur",
    "Cl": "chlorine",
    "Br": "bromine",
    "Si": "silicon",
    "P": "phosphorus",
    "F": "fluorine",
    "I": "iodine",
}

#: One part of an isotopologue's label (``"34S"``, ``"13C2"``): its mass number
#: and its element, as ``tiering`` reads it.
_LABEL_PART = re.compile(r"^(\d+)([A-Z][a-z]?)\d*$")

#: Each element's lightest isotope, whose mass number never names a line.
_LIGHTEST = {
    "H": 1,
    "C": 12,
    "N": 14,
    "O": 16,
    "S": 32,
    "Cl": 35,
    "Br": 79,
    "Si": 28,
}


def is_level(value: Any) -> bool:
    """Whether a value is a level of the scale (:data:`LEVELS`)."""
    return isinstance(value, str) and value in LEVELS


def _provenance(row: dict) -> dict:
    provenance = row.get("provenance")
    if not isinstance(provenance, dict):
        provenance = {}
        row["provenance"] = provenance
    return provenance


def _fit(value: Any) -> float | None:
    """A fit as a finite number in [0, 1], or None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _count_word(count: int) -> str:
    return {1: "one", 2: "two", 3: "three"}.get(count, str(count))


def _plural(count: int, word: str) -> str:
    return f"{_count_word(count)} {word}{'' if count == 1 else 's'}"


def tie_gap(best: float) -> float:
    """The arbitration's tie margin below a best value
    (``arbitration.tie_gap``): relative, floored in absolute."""
    return arbitration_tie_gap(best)


@dataclass(frozen=True)
class Rival:
    """One rival of a row's reading, as the row recorded it.

    :param formula: The rival's neutral.
    :param ion_formula: Its ion, where the record names one.
    :param fit: Its fit at the run's width, None where it was not measured.
    :param against: The winner's fit on the same scale, which the tie margin
        is read against.
    :param mz_error_ppm: Its mass error, where measured.
    :param monoisotopic: Whether it reads the peak as its ion's monoisotopic
        line; a reading of another ion's isotope line is asked by mass and
        channel alone.
    :param placed: False where no channel of the run placed it on the peak.
    :param record: The provenance record it was read from.
    """

    formula: str
    ion_formula: str | None
    fit: float | None
    against: float | None
    mz_error_ppm: float | None
    monoisotopic: bool
    placed: bool
    record: str


def _key(rival: Rival) -> tuple:
    """A rival's identity: its ion where one is named, else its formula."""
    return ion_identity(rival.ion_formula) or (
        "formula",
        formula_identity(rival.formula),
    )


def shortlist(row: dict) -> tuple[list[Rival] | None, int]:
    """The rivals a row's measured shortlist holds, and how many it leaves unlisted.

    The row's rival set (``rival_set.RIVAL_SET``): every other ion the run
    measured on its peak, at the model its caps read, each with its fit beside
    the row's own in the same frame. The level reads the rivals the density
    and the arbitration read, and measures none of its own.

    Where the set is all of a row's rivals is the search's to say. A Stage A
    row's set holds the search's rivals only where the search measured its
    peak (``engine.GRID_RIVALS``): a row with no such record - a run that did
    not search, or a reading the search could not read - was competed against
    the formulas a list named and no others, so it reads as not measured. An
    untargeted winner's is complete only where step 4.15 measured its
    formula-only shortlist (``engine.MEASURED_SHORTLIST``), off by default; a
    formula of it that no channel placed on the peak is listed as such. A
    reading of the row's own ion is no rival: it is the same measurement split
    another way, and the split is the 4b question.

    :param row: A committed monoisotopic row.
    :return: The rivals, best fit first per ion, or None where the shortlist
        was not measured; and how many rivals the row knows of and does not
        list (past the set's named ones, or, on an untargeted winner, past the
        stored cap, whose formula-only entries no pass measured).
    """
    provenance = _provenance(row)
    source = row.get("source")
    measured = provenance.get(MEASURED_SHORTLIST)
    if source == SOURCE_UNTARGETED:
        if not isinstance(measured, list):
            return None, 0
    elif source != SOURCE_DATABASE or not isinstance(provenance.get(GRID_RIVALS), dict):
        # A hand row, a source no stage of this engine is, or a list row the
        # search did not measure.
        return None, 0
    record = provenance.get(RIVAL_SET)
    if not isinstance(record, dict):
        return None, 0
    against = _fit(record.get("fit_score"))
    own = formula_identity(row.get("assigned_formula"))
    found = [
        Rival(
            str(entry["formula"]),
            entry.get("ion_formula"),
            _fit(entry.get("fit_score")),
            against,
            _fit(entry.get("mz_error_ppm")),
            bool(entry.get("monoisotopic", True)),
            True,
            RIVAL_SET,
        )
        for entry in record.get("rivals") or []
        if isinstance(entry, dict)
        and entry.get("formula")
        and formula_identity(entry["formula"]) != own
    ]
    found += [
        Rival(str(entry["formula"]), None, None, against, None, True, False, RIVAL_SET)
        for entry in measured or []
        if isinstance(entry, dict)
        and entry.get("formula")
        and entry.get("placed") is False
        and formula_identity(entry["formula"]) != own
    ]
    # A stored alternative no frame measured is a rival nobody tested: it
    # stays listed, open, its fit not measured.
    tested = {formula_identity(rival.formula) for rival in found} | {
        formula_identity(entry.get("formula"))
        for entry in measured or []
        if isinstance(entry, dict)
    }
    for entry in row.get("alternatives") or []:
        formula = entry.get("assigned_formula") if isinstance(entry, dict) else None
        if (
            not formula
            or entry.get(SAME_ION)
            or formula_identity(formula) in tested | {own}
        ):
            continue
        found.append(
            Rival(
                str(formula),
                entry.get("ion_formula"),
                None,
                against,
                None,
                entry.get("isotope_label") in (None, "", "M0"),
                True,
                "alternatives",
            )
        )
    unlisted = int(record.get("rivals_unlisted") or 0)
    if source == SOURCE_UNTARGETED:
        unlisted += int(provenance.get(RIVALS_PAST_CAP) or 0)
    winner_ion = ion_identity(row.get("ion_formula"))
    best: dict[tuple, Rival] = {}
    for rival in found:
        key = _key(rival)
        if winner_ion is not None and key == winner_ion:
            continue
        held = best.get(key)
        if held is None or _rank(rival) > _rank(held):
            best[key] = rival
    return sorted(best.values(), key=lambda r: (-_rank(r), r.formula)), unlisted


def _rank(rival: Rival) -> float:
    """A rival's fit for picking one entry per ion: a measured fit, 0.0
    included, before one that was not measured."""
    return -1.0 if rival.fit is None else rival.fit


def _heavy_elements(label: str | None) -> list[str]:
    """The elements an isotopologue's label names a heavy isotope of.

    ``"34S"`` names sulfur, ``"13C+37Cl"`` carbon and chlorine; a line merged
    at the file's resolving power (``"M+2"``) names none, and neither does an
    element's lightest isotope. Each part is read as the tiering pass reads it
    (``tiering.names_element``).
    """
    elements = []
    for part in str(label or "").split("+"):
        match = _LABEL_PART.match(part.strip())
        if match and int(match.group(1)) != _LIGHTEST.get(match.group(2)):
            elements.append(match.group(2))
    return elements


def _ion_counts(ion_formula: str | None) -> dict[str, int] | None:
    """An ion's element counts, its charge signs aside."""
    if not ion_formula:
        return None
    return element_counts(str(ion_formula).strip().rstrip("+-"))


@dataclass
class LevelContext:
    """What the level pass reads of the run beside the row.

    :param neutral_channels: Per normalized neutral, the channels the run
        committed it through (``cross_channel.channels_by_neutral``).
    :param notation_by_id: The run's mechanisms, by the id the rows carry.
    :param tof: Whether the run's instrument class is a TOF.
    :param context: The run's chemistry context, whose priors an open rival
        is named as held by; None names none.
    :param lines: The sample's peaks, which a rival's own lines are asked
        of; None asks none.
    :param gate: What the run asked its rows' lines at (``tiering.LineGate``).
    :param resolution: The file's resolving power as a function of m/z, at
        which a rival's envelope is predicted one line per peak the file
        resolves, as the run's own rows' are.
    """

    neutral_channels: dict[str, dict]
    notation_by_id: dict[str, str]
    tof: bool = False
    context: ChemistryContext | None = None
    lines: SpectrumLines | None = None
    gate: LineGate | None = None
    resolution: Callable[[float], float] | None = None


def _absent_line_of(row: dict, rival: Rival, ctx: LevelContext) -> dict | None:
    """A line the rival's own envelope predicts that the spectrum lacks.

    The absent-line rule of step 3.4e, asked of the rival at the gate the run
    asked its own rows at: the rival read as the peak's monoisotopic line, at
    the peak's height and signal-to-noise, shifted by the rival's own mass
    error. A window only the artifact pre-pass's peaks hold reads as found
    here rather than untestable, which comes to the same: neither is absent.
    """
    if ctx.lines is None or ctx.gate is None or not rival.ion_formula:
        return None
    record = expected_lines(
        {
            "sample_peak_id": row.get("sample_peak_id"),
            "ion_formula": rival.ion_formula,
            "sample_peak_intensity": row.get("sample_peak_intensity"),
            "mz_error_ppm": rival.mz_error_ppm,
        },
        ctx.lines,
        abundance_floor=ctx.gate.abundance_floor,
        mz_tolerance_ppm=ctx.gate.mz_tolerance_ppm,
        detect_snr=ctx.gate.detect_snr,
        rel_detect_no_snr=ctx.gate.rel_detect_no_snr,
        resolution=ctx.resolution,
        tof=ctx.gate.tof,
    )
    absent = [
        line
        for line in (record or {}).get("lines") or []
        if line.get("state") == LINE_ABSENT
    ]
    if not absent:
        return None
    return max(absent, key=lambda line: float(line.get("predicted_share") or 0.0))


def _corroborating_channels(row: dict, ctx: LevelContext) -> list[str]:
    """The run's other channels committing the row's neutral: those on a peak
    the instrument separated, where the run read its peaks' widths."""
    cross_channel = _provenance(row).get("cross_channel") or {}
    channels = cross_channel.get("channels")
    if not isinstance(channels, list) or len(channels) < CHANNELS_FOR_CORROBORATION:
        return []
    separated = cross_channel.get(SEPARATED_THROUGH)
    if isinstance(separated, list):
        return [str(channel) for channel in separated]
    own = ctx.notation_by_id.get(str(row.get("ionization_mechanism_id")))
    return [str(channel) for channel in channels if channel != own]


def judge_rival(
    row: dict, rival: Rival, own_lines: Sequence[dict], ctx: LevelContext
) -> dict:
    """The test that excluded a rival, or *open* with what held it.

    :param row: The committed monoisotopic row.
    :param rival: One rival of its shortlist (:func:`shortlist`).
    :param own_lines: The row's committed isotopologue rows.
    :param ctx: What the pass reads of the run.
    :return: The rival's entry: ``formula``, ``ion_formula``, ``test`` (one
        of :data:`TEST_MASS`, :data:`TEST_ISOTOPES`,
        :data:`TEST_SECOND_CHANNEL` or :data:`OPEN`), ``detail``, the
        ``record`` it was read from, its ``fit`` where measured, and on an
        open rival what ``held_by`` it, where anything did.
    """
    entry: dict = {
        "formula": rival.formula,
        "ion_formula": rival.ion_formula,
        "record": rival.record,
    }
    if rival.fit is not None:
        entry["fit"] = round(rival.fit, 4)
    if not rival.placed:
        return {
            **entry,
            "test": TEST_MASS,
            "detail": "no channel of the run places it on this peak at the run's width",
        }
    if rival.fit is None:
        return {
            **entry,
            "test": OPEN,
            "detail": "its fit on this peak was not measured",
        }
    against = rival.against
    if against is not None and rival.fit < against - tie_gap(against):
        return {
            **entry,
            "test": TEST_MASS,
            "detail": (
                f"its fit at the run's width, mass and isotope pattern together, "
                f"is {rival.fit:.2f} against the reading's {against:.2f}, under it "
                "by more than the tie margin"
            ),
        }
    if rival.monoisotopic:
        counts = _ion_counts(rival.ion_formula) or _ion_counts(rival.formula)
        if counts is not None:
            for line in own_lines:
                if not follows_its_owner(line):
                    continue
                for element in _heavy_elements(line.get("isotope_label")):
                    if counts.get(element, 0) == 0:
                        word = _ELEMENT_WORDS.get(element, element)
                        return {
                            **entry,
                            "test": TEST_ISOTOPES,
                            "detail": (
                                f"the reading's {line.get('isotope_label')} line is "
                                f"committed and tracks it, and "
                                f"{rival.ion_formula or rival.formula} carries no "
                                f"{word} to make it"
                            ),
                        }
        absent = _absent_line_of(row, rival, ctx)
        if absent is not None:
            return {
                **entry,
                "test": TEST_ISOTOPES,
                "detail": (
                    f"its {absent['line']} line, predicted at m/z "
                    f"{float(absent['predicted_mz']):.4f}, is not in the spectrum "
                    "where the detectability gate says it would show"
                ),
            }
    if not ctx.tof:
        through = _corroborating_channels(row, ctx)
        if through and formula_identity(rival.formula) not in ctx.neutral_channels:
            return {
                **entry,
                "test": TEST_SECOND_CHANNEL,
                "detail": (
                    f"{row.get('assigned_formula')} is also committed through "
                    f"{', '.join(through)}, and {rival.formula} through no channel "
                    "of the run"
                ),
            }
    held: list[str] = []
    detail = "no test of the run excluded it"
    if against is not None and rival.fit >= against - tie_gap(against):
        detail = (
            f"its fit at the run's width, {rival.fit:.2f}, is within the tie "
            f"margin of the reading's {against:.2f}"
        )
        winner_evidence = against * formula_plausibility(
            str(row.get("assigned_formula") or "")
        )
        rival_evidence = rival.fit * formula_plausibility(rival.formula)
        if rival_evidence < winner_evidence - tie_gap(winner_evidence):
            held.append(HELD_BY_PLAUSIBILITY)
            detail += (
                "; its plausibility weighs it under the reading, which excludes nothing"
            )
    if ctx.context is not None and ctx.context.priors_met(rival.formula):
        held.append(HELD_BY_PRIOR)
        detail += (
            f"; the {ctx.context.label.lower()} context's prior on its shape holds "
            "it back, which excludes nothing"
        )
    return {
        **entry,
        "test": OPEN,
        "detail": detail,
        **({"held_by": held} if held else {}),
    }


def _reason(row: dict, rule: str) -> dict | None:
    for reason in _provenance(row).get("tier_reasons") or []:
        if isinstance(reason, dict) and reason.get("rule") == rule:
            return reason
    return None


def _fact(fact: str, record: str, detail: str | None = None) -> dict:
    return {"fact": fact, "record": record, **({"detail": detail} if detail else {})}


def _refuted(row: dict) -> tuple[list[str], list[str], list[dict]]:
    """What puts a row at 5b: its evidence lines, its lifts and its facts."""
    evidence: list[str] = []
    lift: list[str] = []
    facts: list[dict] = []
    absent = _reason(row, REASON_ABSENT_LINE)
    if absent is not None:
        evidence.append(f"refuted by its isotopes: {absent.get('detail')}")
        lift.append("another reading of the peak, whose lines the spectrum holds")
        facts.append(_fact(REASON_ABSENT_LINE, "tier_reasons", absent.get("detail")))
    formula = str(row.get("assigned_formula") or "")
    plausibility = _fit(_provenance(row).get("plausibility"))
    if plausibility is None:
        plausibility = float(formula_plausibility(formula))
    if plausibility < PLAUSIBILITY_FLOOR:
        evidence.append(
            f"implausible: {formula} has a plausibility of {plausibility:.2f}, "
            f"under the floor of {PLAUSIBILITY_FLOOR:g} a possible molecule holds"
        )
        lift.append("another reading of the peak: no molecule has this formula")
        facts.append(_fact("plausibility", "plausibility", f"{plausibility:.4f}"))
    unresolved = _reason(row, REASON_UNRESOLVED_PEAK)
    if unresolved is not None:
        evidence.append(f"untestable: {unresolved.get('detail')}")
        lift.append(
            "a separated peak: more resolving power, or the neutral through "
            "another channel on a peak the instrument separates"
        )
        facts.append(
            _fact(REASON_UNRESOLVED_PEAK, "tier_reasons", unresolved.get("detail"))
        )
    return evidence, lift, facts


def _split(row: dict, ctx: LevelContext) -> tuple[str, str, str, dict]:
    """Whether the molecule-reagent split is pinned: 4a or 4b, with the
    evidence line, the lift and the fact it was read from."""
    cross_channel = _provenance(row).get("cross_channel")
    if not isinstance(cross_channel, dict):
        return (
            LEVEL_4B,
            "the split of the ion into molecule and reagent was not read",
            "read the split: a second channel committing the neutral, a labelled "
            "reagent, or MS/MS",
            _fact("split", "cross_channel", "not recorded"),
        )
    for rule in AMBIGUITY_REASONS:
        ambiguity = cross_channel.get(rule)
        if isinstance(ambiguity, dict):
            return (
                LEVEL_4B,
                f"the split is open: {ambiguity_detail(row, rule, ambiguity)}",
                f"settle the split: a second channel committing "
                f"{row.get('assigned_formula')} that the other reading cannot "
                "share, a labelled reagent, or MS/MS",
                _fact(rule, f"cross_channel.{rule}"),
            )
    lift = "a context list naming a compound of this formula, or MS/MS"
    settled = cross_channel.get(SAME_ION_SETTLED)
    if isinstance(settled, dict):
        by = {
            SETTLED_BY_SECOND_CHANNEL: "a second channel",
            SETTLED_BY_PARTNER: "the stronger partner",
            SETTLED_BY_TARGET_LIBRARY: "the target library",
            SETTLED_BY_RADICAL: "the other reading being a radical",
        }.get(settled.get("by"), "the cross-channel pass")
        return (
            LEVEL_4A,
            f"the split is pinned by {by}: {settled_detail(row, settled)}",
            lift,
            _fact(
                SAME_ION_SETTLED, f"cross_channel.{SAME_ION_SETTLED}", settled.get("by")
            ),
        )
    notation = ctx.notation_by_id.get(str(row.get("ionization_mechanism_id")))
    if carries_label(notation) or carries_label(row.get("ion_formula")):
        return (
            LEVEL_4A,
            f"the split is pinned by the labelled reagent of {notation or 'its channel'}, "
            "whose label tells the reagent's atoms from the molecule's",
            lift,
            _fact("labelled_reagent", "ionization_mechanism", notation),
        )
    # A second channel is read here as the rival test reads it
    # (:func:`judge_rival`): not on a TOF, where a second reagent pins a row
    # only where the channels co-vary (step 5.8d), and only through a
    # channel on a peak the instrument separated, where the run recorded it.
    through = [] if ctx.tof else _corroborating_channels(row, ctx)
    if through:
        return (
            LEVEL_4A,
            f"the split is pinned by a second channel: the same neutral is "
            f"also committed through {', '.join(through)}",
            lift,
            _fact("second_channel", "cross_channel", ", ".join(through)),
        )
    held = held_units(row, notation, ctx.notation_by_id.values())
    settle = (
        f"settle the split: a second channel committing "
        f"{row.get('assigned_formula')}, a labelled reagent, or MS/MS"
    )
    if held is None:
        return (
            LEVEL_4B,
            "the split is open: the ion's formula could not be read for the "
            "reagent units it holds",
            settle,
            _fact("reagent_units", "ion_formula", "unread"),
        )
    if held:
        named = ", ".join(f"the {unit} of {channel}" for channel, unit in held)
        return (
            LEVEL_4B,
            f"the split is open: the ion could hold {named}, so it may be another "
            "molecule through another channel, and nothing settled which",
            settle,
            _fact("reagent_units", "ionization_mechanism", named),
        )
    return (
        LEVEL_4A,
        "the split is pinned: one plausible decomposition, the ion holding no "
        "reagent unit of the run's channels",
        lift,
        _fact("one_decomposition", "ionization_mechanism"),
    )


def reagent_unit(notation: str | None) -> str | None:
    """The unit a channel adds to the molecule, or None for one that adds none.

    A cluster or adduct channel adds a reagent unit (``NO3`` under
    ``[M+NO3]-``, ``Br`` under ``[M+Br]-``, the urea of ``[M+CH4N2O+H]+``);
    a proton, a removal and an electron transfer add none.
    """
    if not notation:
        return None
    try:
        parts = parse_mechanism(notation)
    except Exception:  # noqa: BLE001 - a notation nothing reads adds no unit here
        return None
    if not parts.addition or not parts.moiety:
        return None
    unit = re.sub(r"^\((.+)\)H$", r"\1", parts.moiety)
    if re.fullmatch(r"[()H\d]*", unit):
        return None
    return unit.replace("(", "").replace(")", "")


def held_units(
    row: dict, own: str | None, notations: Iterable[str]
) -> list[tuple[str, str]] | None:
    """The run's reagent units the row's ion could be read as holding.

    The 4a decomposition rule (the confidence note's point 4): the ion reads
    only as the molecule's own where its formula cannot hold the unit any
    other channel of the run adds - no N under nitrate, since the cluster's
    NO3 brings one; no Br under bromide; fewer than two N under uronium,
    since urea brings two. An ion read through a channel that adds a unit
    holds that unit, and reads another way wherever the run has another
    channel to read it through.

    :param row: The committed monoisotopic row.
    :param own: The row's own channel, by its notation.
    :param notations: The run's channels.
    :return: ``(channel, unit)`` per unit the ion could hold, or None where
        the ion's formula cannot be read.
    """
    ion = _ion_counts(row.get("ion_formula"))
    if ion is None:
        return None
    channels = sorted({str(n) for n in notations if n})
    others = [channel for channel in channels if channel != own]
    held: list[tuple[str, str]] = []
    own_unit = reagent_unit(own)
    if own_unit is not None and others:
        held.append((str(own), own_unit))
    for channel in others:
        unit = reagent_unit(channel)
        counts = element_counts(unit) if unit else None
        if counts and all(ion.get(element, 0) >= n for element, n in counts.items()):
            held.append((channel, unit))
    return held


def _tested_summary(tested: Sequence[dict]) -> str:
    """How many rivals each test put out, in one clause."""
    counts = Counter(entry["test"] for entry in tested if entry["test"] != OPEN)
    words = {
        TEST_MASS: "by mass",
        TEST_ISOTOPES: "by isotopes",
        TEST_SECOND_CHANNEL: "by a second channel",
    }
    parts = [
        f"{_count_word(counts[test])} {words[test]}"
        for test in (TEST_MASS, TEST_ISOTOPES, TEST_SECOND_CHANNEL)
        if counts[test]
    ]
    total = sum(counts.values())
    return f"{_plural(total, 'rival')} out: {', '.join(parts)}"


def _open_lift(row: dict, open_rivals: Sequence[dict], ctx: LevelContext) -> str:
    """What would exclude the rivals left open."""
    unmeasured = [entry for entry in open_rivals if "fit" not in entry]
    measured = [entry for entry in open_rivals if "fit" in entry]
    parts: list[str] = []
    if unmeasured:
        parts.append("measure the shortlist")
    if measured:
        names = ", ".join(entry["formula"] for entry in measured[:3])
        if ctx.tof:
            parts.append(
                f"exclude {names}: a heavy-isotope twin one of them cannot make, "
                "or a second channel that co-varies with this one"
            )
        else:
            envelope = _provenance(row).get(PREDICTED_ENVELOPE)
            asked = (
                (envelope or {}).get("lines") if isinstance(envelope, dict) else None
            )
            if isinstance(envelope, dict) and not asked:
                parts.append(
                    f"more signal: the reading's isotope lines are under the file's "
                    f"detectability threshold, so none can exclude {names}"
                )
            else:
                parts.append(
                    f"exclude {names}: an isotope line one of them cannot make, or "
                    f"a second channel committing {row.get('assigned_formula')} and "
                    "not it"
                )
    return "; ".join(parts)


def level_of(row: dict, own_lines: Sequence[dict], ctx: LevelContext) -> dict:
    """The level of one committed monoisotopic row (module docstring).

    :param row: The row.
    :param own_lines: Its committed isotopologue rows.
    :param ctx: What the pass reads of the run.
    :return: The record :data:`IDENTIFICATION_LEVEL` holds.
    """
    rivals, unlisted = shortlist(row)
    return _level(row, own_lines, ctx, rivals, unlisted)


def _level(
    row: dict,
    own_lines: Sequence[dict],
    ctx: LevelContext,
    rivals: list[Rival] | None,
    unlisted: int,
) -> dict:
    """:func:`level_of` over the row's shortlist (:func:`shortlist`)."""
    record: dict = {}
    evidence, lift, facts = _refuted(row)
    tested = (
        [judge_rival(row, rival, own_lines, ctx) for rival in rivals]
        if rivals is not None
        else []
    )
    record["rivals"] = tested
    if unlisted:
        record["rivals_unlisted"] = unlisted
    # 5a's statement, where the row earns it: rivals not measured, or ones
    # left open. Shown as the level unless the row also earns 5b, which goes
    # first; then it is named in the evidence line (the order, point 4).
    five_a: tuple[list[str], list[str]] | None = None
    if rivals is None:
        five_a = (["rivals not measured"], ["measure the shortlist"])
    else:
        open_rivals = [entry for entry in tested if entry["test"] == OPEN]
        if open_rivals or unlisted:
            named = ", ".join(entry["formula"] for entry in open_rivals[:3])
            more = len(open_rivals) - 3
            clauses = []
            if open_rivals:
                clauses.append(
                    f"{_plural(len(open_rivals), 'rival')} left open: {named}"
                    + (f" and {more} more" if more > 0 else "")
                )
            if unlisted:
                clauses.append(
                    f"{_plural(unlisted, 'rival')} past the stored shortlist, "
                    "not tested"
                )
            if len(open_rivals) < len(tested):
                clauses.append(_tested_summary(tested))
            lifts = []
            if open_rivals:
                lifts.append(_open_lift(row, open_rivals, ctx))
            if unlisted:
                lifts.append("measure the rivals past the stored shortlist")
            five_a = (clauses, [part for part in lifts if part])
    if evidence:
        if five_a is not None:
            evidence.append(f"it earns 5a too: {'; '.join(five_a[0])}")
        return {
            **record,
            "level": LEVEL_5B,
            "evidence": "; ".join(evidence),
            "lift": "; ".join(lift),
            "facts": facts,
        }
    if rivals is None:
        return {
            **record,
            "level": LEVEL_5A,
            "evidence": "; ".join(five_a[0]),
            "lift": "; ".join(five_a[1]),
            "facts": [_fact("shortlist", MEASURED_SHORTLIST, "not measured")],
        }
    facts.append(
        _fact(
            "shortlist",
            RIVAL_SET,
            f"{len(rivals)} rival{'s' if len(rivals) != 1 else ''} listed"
            + (f", {unlisted} unlisted" if unlisted else ""),
        )
    )
    for entry in tested:
        if entry["test"] != OPEN:
            facts.append(_fact(entry["test"], entry["record"], entry["formula"]))
    if five_a is not None:
        return {
            **record,
            "level": LEVEL_5A,
            "evidence": "; ".join(five_a[0]),
            "lift": "; ".join(five_a[1]),
            "facts": facts,
        }
    level, split, split_lift, split_fact = _split(row, ctx)
    facts.append(split_fact)
    excluded = (
        _tested_summary(tested) if tested else "no rival on the measured shortlist"
    )
    return {
        **record,
        "level": level,
        "evidence": f"{excluded}; {split}",
        "lift": split_lift,
        "facts": facts,
    }


def inherited_level(owner_level: dict | None, owner_id: str) -> dict:
    """An isotopologue row's level: its owner's, said to be."""
    if not isinstance(owner_level, dict):
        return {
            "level": LEVEL_5A,
            "evidence": "an isotopologue whose monoisotopic row the run does not record",
            "lift": "assign the sample again",
            "inherited_from": owner_id or None,
        }
    return {
        "level": owner_level.get("level"),
        "evidence": owner_level.get("evidence"),
        "lift": owner_level.get("lift"),
        "inherited_from": owner_id,
    }


def hand_level(row: dict) -> dict:
    """The level of a row no stage of its run elected: one a person assigned
    by hand, or one a batch pass rewrote as the listed compound its batch
    decided present (``known_species.listed_species_row``).

    What the mapping can state of such a formula: 5b where its lines
    (``tiering.ask_absent_line``, which curation asks) or its plausibility
    refute it, else 5a with its rivals not measured, since no shortlist of the
    run was competed against it. A hand isotopologue has no monoisotopic row
    on the run whose level it could show.

    :param row: The row: its ``role``, ``source``, ``assigned_formula`` and
        ``provenance``.
    :return: The record :data:`IDENTIFICATION_LEVEL` holds.
    """
    if row.get("role") == ROLE_ISO_CHILD:
        return inherited_level(None, "")
    return level_of(row, [], LevelContext(neutral_channels={}, notation_by_id={}))


def apply_levels(
    assignments: Iterable[dict],
    *,
    notation_by_id: dict[str, str] | None = None,
    instrument_type: str | None = None,
    context: ChemistryContext | None = None,
    lines: SpectrumLines | None = None,
    tiering: dict | None = None,
    resolution: Callable[[float], float] | None = None,
) -> dict:
    """Give every committed row of a run its identification level.

    Runs once the ledger is finished: after the passes that judge it and after
    step 4.15's measurement of the untargeted winners' shortlists, which the
    level reads. Writes provenance alone; no tier, role or formula moves.

    :param assignments: The run's rows, mutated in place; rows that commit no
        formula are left untouched.
    :param notation_by_id: The run's mechanisms, by the id the rows carry.
    :param instrument_type: The run's instrument class; on ``"tof"`` a second
        channel excludes no rival.
    :param context: The run's chemistry context, whose priors an open rival is
        named as held by.
    :param lines: The sample's peaks, which a rival's own lines are asked of.
    :param tiering: The run's tiering record, whose absent-line gate the
        rivals' lines are asked at (``tiering.LineGate.from_record``).
    :param resolution: The file's resolving power as a function of m/z.
    :return: What the run records: the mapping version, the count per level of
        the monoisotopic rows and of the isotopologue rows, and how many rows
        had no measured shortlist, at whichever level they show.
    """
    notation_by_id = notation_by_id or {}
    rows = [row for row in assignments if row.get("assigned_formula")]
    m0 = [row for row in rows if row.get("role") == ROLE_M0]
    ctx = LevelContext(
        neutral_channels=channels_by_neutral(m0, notation_by_id),
        notation_by_id=notation_by_id,
        tof=instrument_type == "tof",
        context=context,
        lines=lines,
        gate=LineGate.from_record(tiering) if tiering is not None else None,
        resolution=resolution,
    )
    lines_of = isotope_lines(rows)
    levels: Counter = Counter()
    unmeasured = 0
    by_id: dict[str, dict] = {}
    for row in m0:
        row_id = str(row.get("peak_assignment_id"))
        rivals, unlisted = shortlist(row)
        record = _level(row, lines_of.get(row_id, []), ctx, rivals, unlisted)
        _provenance(row)[IDENTIFICATION_LEVEL] = record
        row[LEVEL_COLUMN] = record["level"]
        by_id[row_id] = record
        levels[record["level"]] += 1
        # Whatever level it shows: a refuted row may lack one too.
        unmeasured += rivals is None
    inherited: Counter = Counter()
    for row in rows:
        if row.get("role") != ROLE_ISO_CHILD:
            continue
        owner_id = str(row.get("owner_peak_assignment_id") or "")
        record = inherited_level(by_id.get(owner_id), owner_id)
        _provenance(row)[IDENTIFICATION_LEVEL] = record
        row[LEVEL_COLUMN] = record["level"]
        inherited[record["level"]] += 1
    return {
        "level_rules_version": LEVEL_RULES_VERSION,
        "levels": {level: levels[level] for level in LEVELS if levels[level]},
        "isotopologue_levels": {
            level: inherited[level] for level in LEVELS if inherited[level]
        },
        "rivals_not_measured": unmeasured,
    }


def members_level(counts: dict[str, int] | None) -> str | None:
    """The level most of a batch peak's members' runs stated.

    The batch ledger's reading of the level until the batch states one of its
    own (step 5.8c): of the members whose run committed the consensus
    formula, the level the most of them carry. A tie reads as the level
    further down the scale (:data:`LEVELS`), so a batch peak is never shown at
    more than its members stated.

    :param counts: Level -> how many such members carry it.
    :return: The level, or None where no member states one.
    """
    stated = {
        level: count
        for level, count in (counts or {}).items()
        if is_level(level) and count > 0
    }
    if not stated:
        return None
    return max(stated, key=lambda level: (stated[level], LEVELS.index(level)))


def level_lines(provenance: dict | None) -> dict:
    """The two lines every surface shows beside a row's level.

    :param provenance: The row's stored provenance.
    :return: ``level_evidence`` and ``level_lift``, each None where the row
        carries no level of the scale (an unassigned peak, a reagent or
        artifact row, a row from before the level, an imported row).
    """
    block = (provenance or {}).get(IDENTIFICATION_LEVEL)
    if not isinstance(block, dict) or not is_level(block.get("level")):
        return {"level_evidence": None, "level_lift": None}
    return {
        "level_evidence": block.get("evidence"),
        "level_lift": block.get("lift"),
    }


def level_fields(level: Any, provenance: dict | None) -> dict:
    """The three fields of a row's level, as the exports carry them.

    :param level: The row's ``level`` column.
    :param provenance: The row's stored provenance.
    :return: ``level``, ``level_evidence`` and ``level_lift``; all None where
        the column holds no level of the scale.
    """
    if not is_level(level):
        return {"level": None, "level_evidence": None, "level_lift": None}
    return {"level": level, **level_lines(provenance)}
