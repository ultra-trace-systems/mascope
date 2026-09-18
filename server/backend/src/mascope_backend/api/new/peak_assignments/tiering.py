"""Why a committed row holds the tier it holds.

The evidence bands (``engine.tier_for_evidence``) bucket a row on one number:
its fit times the chemical plausibility of the formula. That number is the floor
and stays the floor - nothing here promotes anything. What it cannot say is
whether the peak had competitors the fit could not separate, whether the neutral
is a molecule at all, or whether anything but this one reading ever saw it. This
pass asks those questions of every committed row, records the answer as
``provenance.tier_reasons``, and caps at ``candidate`` the rows whose answer is
that the top tier was not earned.

Every rule is a DEMOTE. A row's tier after this pass is never above what the
bands gave it, so the pass cannot rescue a bad commit and cannot be tuned into
promoting one. And every committed row leaves with at least one reason, whether
it was capped or not: a row that keeps its tier says what it kept it on, which
is the difference between a ledger a reader can audit and one they have to take
on trust.

A row under the top band names its band first (``evidence_band``, from the run's
``tier_bands``). The band is not a rule of this pass - it is the floor the rules
lower from - and it is named because nothing else says why such a row stands
where it does: without it, a row its evidence holds below assignability lists
only what it stands on, a second channel or no close rival, and reads as a row
with nothing against it.

The rules, and what each is worth on the 43-sample assignment gate
-----------------------------------------------------------------

``odd_electron`` - the committed neutral breaks the nitrogen rule, so it names a
radical rather than a molecule. Measured on the gate, this is the sharpest rule
the engine has and it needs no condition at all: of 1,794 assigned-tier rows
whose neutral is odd-electron, across all eight sets, the reference engine
confirms **none**. It is symmetric - the reference commits 629 such rows of its
own - and wherever both engines commit an M0 on one peak, a radical on either
side never matches: not on the 954 peaks where this engine's commit is
odd-electron, not on the 462 where the reference's is, not on the 151 where both
are. Decision 9 already prefers the closed-shell reading when two
readings make one ion; this says the same thing about a reading with no rival:
a radical is a tie-break, and a tie-break is not evidence.

``oxygen_free_cluster`` - the row reads the peak as nitrate clustered with a
neutral that carries no oxygen, which the nitrate has nothing to hold on to
(:func:`oxygen_free_cluster_reason`). On the gate it names 134 rows, all on the
three nitrate sets, and takes the top tier from 10 of them, all on the TOF
nitrate set: the reference engine commits the same formula on one of the ten
and nothing on the other nine. Carbonate clusters are not asked, on the same
measurement.

``polyhalide_cluster`` - the row reads the peak as a halide attached to a
neutral made of halogens only, a polyhalide anion (IBr through bromide is
IBr2-) that a halide source makes from the air's halogens and from its own
(:func:`polyhalide_cluster_reason`). On the gate it names 10 rows, all from the
reactive iodine list on the three bromide sets - IBr on eight peaks and ICl on
two - and takes the top tier from six of them, none a row either reference
engine confirms. Neither reads IBr on any of the eight: the frozen one reads all
eight as the bromide source's own cluster and the refreshed one six, both by a
mass-defect rule rather than an identification. Both commit ICl on one of the
two ICl peaks, where the row already stood at candidate.

``candidate_density`` - the run could not separate this peak's winner from other
formulas, and nothing outside the peak corroborates it. Density is measured in
the finder over the full candidate list (``arbitration.candidate_density``),
which is the only place it can be: the row stores at most
``max_alternatives`` of the competitors, so counting those counts the cap. A
list hit's density counts the known set's formulas and, in a run that
searches, the formula search's closed-shell rivals for its peak
(``engine.record_grid_rivals``), so the rule reaches a list hit as it reaches
an election; the reason names the search's rivals.

The implausibility signatures - ``oxygen_lattice`` and ``carbon_free``; the
carbon cluster was withdrawn on the measurement - are named formula shapes that
are the product of a mass search rather than of a source
(``mascope_tools.composition.implausibility``). They are guards rather than
levers: on the gate they reach a few hundred rows in total, and the value of
writing them is that a row demoted on one can say which shape it has. A curated
row is not asked, for the radical rule's reason: it was matched to an identity
somebody authored, not arrived at by a mass search.

``envelope_neighbour`` - decision 11's rider. An M0 committed on a peak that a
committed neighbour's envelope predicts a line for, at a height that could
account for the peak, is more likely to be that line than a compound of its own.
The neighbour has to be a formula the run stands behind - candidate tier or
above - because the line is predicted FROM its formula, and a reading the run
itself calls below assignability is no ground for taking another row's tier.

``envelope_claim`` - where that neighbour is held at ``assigned``, the peak is
read as its line (:func:`find_envelope_claims`, :mod:`envelope_claims`): an
isotopologue of the neighbour at ``candidate``, with the reading it displaced
first among its alternatives. Not where the row is a compound of the target
library, where a second channel committed its neutral, where the neighbour
already holds a line there, or where the peak's mass error does not follow the
neighbour's even allowing for what the line can deliver; the row then stays as
it was and its envelope reason says which.

Rules the earlier passes already applied
----------------------------------------

The mass gate (step 2.2) and the cross-channel pass (step 2.3) demote rows too,
and did so before this module existed. They keep their own provenance blocks,
which the inspector and the SDK read; this pass reads those blocks and folds
what they say into the same ``tier_reasons`` list, so a reader asking "why is
this row a candidate" gets one answer wherever the demote came from. It does not
re-apply them - a row they capped is already capped. That includes what the
gate found of an isotopologue's line: that it tracks its parent only within
what its noise and neighbours explain, or not at all.

The cross-channel pass's same-ion rule (``ambiguous_nitrogen``,
``ambiguous_adduct``) is folded in wherever it found a rival molecule, whether
or not it lowered the tier. Where another reading of the row's ion is settled -
by a second channel, by the target library, or by being a radical - the row
says so among what it stands on (``same_ion_settled``), and its ``no_close_rival``
says the evidence separates the ion from the peak's other candidates, not the
readings of the ion from each other.
"""

from __future__ import annotations

import bisect
from collections import Counter, defaultdict
from typing import Any, Iterable

import numpy as np

from mascope_backend.api.new.peak_assignments.cross_channel import (
    AMBIGUITY_REASONS,
    CHANNELS_FOR_CORROBORATION,
    REASON_AMBIGUOUS_ADDUCT,
    REASON_AMBIGUOUS_NITROGEN,
    SAME_ION_SETTLED,
    SETTLED_BY_RADICAL,
    SETTLED_BY_SECOND_CHANNEL,
    SETTLED_BY_TARGET_LIBRARY,
)
from mascope_backend.api.new.peak_assignments.engine import (
    GRID_RIVALS,
    ROLE_ISO_CHILD,
    ROLE_M0,
    SOURCE_DATABASE,
    is_target_library_row,
)
from mascope_backend.api.new.peak_assignments.envelope_claims import (
    ENVELOPE_CLAIM,
    ClaimedLine,
    EnvelopeClaim,
)
from mascope_backend.api.new.peak_assignments.mass_gate import (
    REASON_ISOTOPOLOGUE_IN_DOUBT,
    REASON_ISOTOPOLOGUE_UNTRACKED,
    TRACKING_IN_DOUBT,
    TRACKING_UNTRACKED,
    UNREAD_LINE,
    SpectrumLines,
    tracking_of,
)
from mascope_backend.api.new.peak_assignments.tiers import (
    TIER_ASSIGNED,
    TIER_BELOW_ASSIGNABILITY,
    TIER_CANDIDATE,
)
from mascope_tools.composition.arbitration import CANDIDATE_DENSITY
from mascope_tools.composition.heuristic_filter import (
    anchor_on_monoisotopic,
    element_counts,
    neutral_is_closed_shell,
    oxygen_free_cluster,
    polyhalide_cluster,
    predict_isotopes,
)
from mascope_tools.composition.implausibility import implausible_signatures


#: Bumped whenever a rule is added, removed or re-thresholded. Recorded on the
#: run, because a tier is only comparable across runs together with the rules
#: that produced it - the same statement the tier BANDS carry, for the same
#: reason.
TIERING_RULES_VERSION = 6

#: The row's evidence is under the band its tier would need. Not a rule of
#: this pass: the band is the floor every rule here lowers from, and naming it
#: first is what lets a row's reasons explain the tier it holds.
REASON_EVIDENCE_BAND = "evidence_band"

#: The row names a radical rather than a molecule.
REASON_ODD_ELECTRON = "odd_electron"

#: The row clusters an anion that holds on to oxygen with a neutral that has
#: none.
REASON_OXYGEN_FREE_CLUSTER = "oxygen_free_cluster"

#: The row attaches a halide to a neutral made of halogens only.
REASON_POLYHALIDE_CLUSTER = "polyhalide_cluster"

#: The peak is a line another committed reading's envelope predicts.
REASON_ENVELOPE_NEIGHBOUR = "envelope_neighbour"

#: The peak was committed as a compound of its own and is read as an assigned
#: neighbour's line instead.
REASON_ENVELOPE_CLAIM = "envelope_claim"

#: Why a row on an assigned neighbour's line was not read as that line.
HELD_NEIGHBOUR_NOT_ASSIGNED = "neighbour_not_assigned"
HELD_TARGET_LIBRARY = "target_library"
HELD_CORROBORATED = "corroborated"
HELD_LINE_TAKEN = "line_taken"
HELD_UNTRACKED = "untracked"

#: What each of those says on the row, after the envelope reason's own sentence.
_HELD_SENTENCES = {
    HELD_NEIGHBOUR_NOT_ASSIGNED: "that reading is not held at assigned",
    HELD_TARGET_LIBRARY: "this row is a compound of the target library",
    HELD_CORROBORATED: "another channel of this run committed its neutral",
    HELD_LINE_TAKEN: "that reading already holds a line there",
    HELD_UNTRACKED: (
        "its mass error does not follow that reading's, even allowing for what "
        "its line can deliver"
    ),
}

#: The peak's own evidence could not separate the winner from other formulas,
#: and no second channel saw the neutral.
REASON_CANDIDATE_DENSITY = "candidate_density"

#: Reasons a row KEEPS its tier, so that every committed row carries one.
#: ``no_close_rival`` and not "unique": a density of 1 says the evidence
#: SEPARATED the winner from the peak's other candidates, not that the run's
#: element box held no other formula for the mass. Uniqueness over a box is what
#: ``mascope_tools.composition.degeneracy`` measures, and it is a wider question
#: than this pass asks.
REASON_NO_CLOSE_RIVAL = "no_close_rival"
REASON_CORROBORATED = "corroborated"
REASON_INHERITED = "inherited_from_owner"

#: A row whose ion reads another way, where something settled which reading it
#: is (the cross-channel pass's record of the same name).
REASON_SAME_ION_SETTLED = SAME_ION_SETTLED

#: Reasons the earlier passes recorded, folded into this list rather than
#: re-derived. ``off_calibration`` is the mass gate's (step 2.2) and
#: ``minor_channel`` the reagent-channel cap's.
REASON_OFF_CALIBRATION = "off_calibration"
REASON_MINOR_CHANNEL = "minor_channel"

#: How many formulas the evidence may leave unseparated before a lone reading
#: stops being assignable. Two: the winner and one rival the run ranked its
#: equal is already a coin toss, and the corroboration escape is what keeps a
#: contested peak that something else saw.
DENSITY_LIMIT = 2

#: The rules another pass already applied, listed so this one records them
#: without capping a second time.
_EARLIER_RULES = frozenset(
    {
        REASON_OFF_CALIBRATION,
        REASON_AMBIGUOUS_NITROGEN,
        REASON_AMBIGUOUS_ADDUCT,
        REASON_MINOR_CHANNEL,
        REASON_ISOTOPOLOGUE_IN_DOUBT,
        REASON_ISOTOPOLOGUE_UNTRACKED,
    }
)

#: How much taller than a neighbour's predicted line a peak may be and still
#: be read as that line. A predicted isotope line and the peak it lands on rarely
#: agree closely - the matcher's own intensity tolerance is 40% - so the test is
#: deliberately one that only fires when the line could account for the peak
#: OUTRIGHT, not when it merely contributes to it.
ENVELOPE_HEIGHT_TOLERANCE = 2.0

#: What a row says when it was committed before this pass existed, or by an
#: engine that measures none of what the rules read. Honest about being an
#: absence rather than a finding, and it keeps "every committed row carries a
#: reason" true rather than nearly true.
REASON_NOT_MEASURED = "not_measured"


def _provenance(row: dict) -> dict:
    provenance = row.get("provenance")
    if not isinstance(provenance, dict):
        provenance = {}
        row["provenance"] = provenance
    return provenance


def corroborating_channels(row: dict) -> int:
    """How many of the run's channels committed this row's neutral.

    Read off what the cross-channel pass recorded rather than recomputed, so
    the two cannot disagree about the same ledger. An isotopologue row carries an
    inherited block with no channels of its own and answers 0; it is judged
    through its owner instead.

    :param row: A committed assignment.
    :return: The channel count, or 0 when the pass recorded none.
    """
    channels = (_provenance(row).get("cross_channel") or {}).get("channels")
    return len(channels) if isinstance(channels, list) else 0


def is_corroborated(row: dict) -> bool:
    """Whether anything outside this reading saw the same neutral."""
    return corroborating_channels(row) >= CHANNELS_FOR_CORROBORATION


def _reason(rule: str, detail: str, *, caps: bool) -> dict:
    """One entry of ``tier_reasons``.

    ``caps`` rather than "demoted" because a rule that fires on a row already
    below the top tier changes nothing and still has something to say about it:
    the flag records what the rule WOULD take, and the row's tier records what
    was actually taken.
    """
    return {"rule": rule, "detail": detail, "caps": caps}


def earlier_reasons(row: dict) -> list[dict]:
    """What the passes that ran before this one already decided about a row.

    They record their own provenance blocks, which the inspector and the SDK
    read; this restates them in one vocabulary so a reader asking why a row is a
    candidate does not have to know which pass to ask.
    """
    provenance = _provenance(row)
    reasons: list[dict] = []
    mass_gate = provenance.get("mass_gate") or {}
    if mass_gate.get("capped") and mass_gate.get("reason", REASON_OFF_CALIBRATION) == (
        REASON_OFF_CALIBRATION
    ):
        z = mass_gate.get("mass_z", provenance.get("mass_z"))
        reasons.append(
            _reason(
                REASON_OFF_CALIBRATION,
                f"mass error sits {z} widths off the run's own fitted centre "
                "at its m/z with nothing corroborating the reading",
                caps=True,
            )
        )
    # What the gate found of an isotopologue's line, whether or not it lowered
    # the tier: a finding about the line, like every rule here.
    if mass_gate.get("tracking") == TRACKING_IN_DOUBT:
        reasons.append(
            _reason(
                REASON_ISOTOPOLOGUE_IN_DOUBT,
                "its mass error misses its monoisotopic row's by more than the "
                "instrument's precision, and by no more than this line's own "
                "noise and the peaks close beside it explain",
                caps=True,
            )
        )
    elif mass_gate.get("tracking") == TRACKING_UNTRACKED:
        reasons.append(
            _reason(
                REASON_ISOTOPOLOGUE_UNTRACKED,
                "its mass error does not follow its monoisotopic row's, even "
                "allowing for this line's own noise and the peaks close beside it",
                caps=True,
            )
        )
    cross_channel = provenance.get("cross_channel") or {}
    # Recorded whether or not the pass lowered the tier: a row its evidence
    # already put lower says what the reading is in doubt with all the same.
    for rule in AMBIGUITY_REASONS:
        ambiguity = cross_channel.get(rule)
        if isinstance(ambiguity, dict):
            reasons.append(
                _reason(rule, ambiguity_detail(row, rule, ambiguity), caps=True)
            )
    # An isotopologue the pass capped with its monoisotopic row carries the
    # row's reason and not its record.
    if cross_channel.get("inherited_from") and cross_channel.get("reason"):
        reasons.append(
            _reason(
                str(cross_channel["reason"]),
                "its monoisotopic row's ion reads as another molecule too, and no "
                "second channel of this run settles which",
                caps=True,
            )
        )
    minor_channel = provenance.get("minor_channel") or {}
    if minor_channel.get("capped"):
        reasons.append(
            _reason(
                REASON_MINOR_CHANNEL,
                "committed through a channel this mode treats as secondary, "
                "with no isotopologue and no second channel behind it",
                caps=True,
            )
        )
    return reasons


def _nitrogen(formula) -> int:
    return (element_counts(str(formula or "")) or {}).get("N", 0)


def _count_word(count: int) -> str:
    return {1: "one", 2: "two", 3: "three"}.get(count, str(count))


def ambiguity_detail(row: dict, rule: str, ambiguity: dict) -> str:
    """What a reading its ion shares with another molecule says about the row.

    :param row: A committed monoisotopic row.
    :param rule: One of the cross-channel pass's ambiguity reasons.
    :param ambiguity: Its record: the rival molecule and its channel.
    :return: The reason's sentence.
    """
    alternative = ambiguity.get("alternative") or "another molecule"
    via = ambiguity.get("via") or "another channel"
    if rule == REASON_AMBIGUOUS_NITROGEN:
        difference = _nitrogen(alternative) - _nitrogen(row.get("assigned_formula"))
        moved = (
            f", which puts {_count_word(abs(difference))} "
            f"{'more' if difference > 0 else 'fewer'} nitrogen on the analyte"
            if difference
            else ""
        )
        return (
            f"the same ion reads as {alternative} through {via}{moved}, and no "
            "second channel of this run settles the count"
        )
    return (
        f"the same ion reads as {alternative} through {via}, another molecule the "
        "spectrum cannot tell from this one, and no second channel of this run "
        "settles which"
    )


def settled_detail(row: dict, settled: dict) -> str:
    """Why another reading of the row's ion takes nothing from it.

    :param row: A committed monoisotopic row.
    :param settled: The cross-channel pass's record: the other reading, its
        channel, what settled it and, for a second channel, which.
    :return: The reason's sentence.
    """
    reading = (
        f"the same ion also reads as {settled.get('alternative') or 'another neutral'}"
        f" through {settled.get('via') or 'another channel'}"
    )
    by = settled.get("by")
    if by == SETTLED_BY_RADICAL:
        return f"{reading}, a radical rather than a molecule, so it is no rival"
    if by == SETTLED_BY_TARGET_LIBRARY:
        return (
            f"{reading}; this row is a compound of the target library, whose "
            "curation chose the reading"
        )
    through = ", ".join(settled.get("through") or []) or "another channel"
    if by == SETTLED_BY_SECOND_CHANNEL:
        return (
            f"{reading}; {row.get('assigned_formula')} is also committed through "
            f"{through}, which settles it"
        )
    return reading


def _digits(value: float, against: float) -> int:
    """How many decimals a percentage needs not to read as ``against``."""
    for digits in (0, 1):
        if f"{value:.{digits}%}" != f"{against:.{digits}%}":
            return digits
    return 2


def band_reason(row: dict, tier_bands: dict | None) -> dict | None:
    """The evidence band a row sits in, where it is under the top one.

    The tier is read off the evidence first (``engine.tier_for_evidence``) and
    every rule of this pass only lowers it from there, so a row under the top
    band is where it is for this reason before any other. Named first on the
    row, because without it the reasons a row keeps say what it stands on and
    not why it stands low.

    :param row: A committed monoisotopic row.
    :param tier_bands: The run's bands, ``assigned`` and ``candidate``, or None
        where the caller has none to state.
    :return: The reason, carrying the band the evidence reaches, or None where
        the evidence clears the top band or the row has none.
    """
    provenance = _provenance(row)
    evidence = provenance.get("evidence")
    if not tier_bands or not isinstance(evidence, (int, float)):
        return None
    assigned = float(tier_bands["assigned"])
    candidate = float(tier_bands["candidate"])
    if evidence >= assigned:
        return None
    if evidence >= candidate:
        band, name, edge = TIER_CANDIDATE, "assigned", assigned
    else:
        band, name, edge = TIER_BELOW_ASSIGNABILITY, "candidate", candidate
    # One precision for every number in the sentence, as fine as it takes for
    # the evidence not to read as the band it misses.
    digits = _digits(evidence, edge)
    fit, plausibility = row.get("fit_score"), provenance.get("plausibility")
    product = (
        f" (fit {fit:.{digits}%} x plausibility {plausibility:.{digits}%})"
        if isinstance(fit, (int, float)) and isinstance(plausibility, (int, float))
        else ""
    )
    return {
        **_reason(
            REASON_EVIDENCE_BAND,
            f"evidence {evidence:.{digits}%}{product} is under the {name} band of "
            f"{edge:.{digits}%}",
            caps=True,
        ),
        "band": band,
    }


def odd_electron_reason(row: dict) -> dict | None:
    """A committed neutral that breaks the nitrogen rule names a radical.

    No corroboration rescues one, so no corroboration escape: on the gate the
    reference confirms 0 of 1,794 such rows whether or not a second channel saw
    the neutral, and it commits 629 of its own that this engine never agrees
    with either.

    A CURATED row is exempt, because the rule's own justification does not reach
    it. What this doubts is an election - decision 9's prior choosing a radical
    reading of an ion over a closed-shell one - and a curated row was not
    elected from a grid, it was matched to an identity somebody authored. Real
    radical anions exist in these chemistries and are exactly what such a
    library holds: the dibromide and carbonate reagent ions, trisulfur. On the
    gate the exemption spares 12 assigned rows, none of which the reference
    confirms, so it is taken on the principle rather than on the count.

    Fails open on a formula that cannot be parsed, like every other chemistry
    rule here.
    """
    if row.get("source") == SOURCE_DATABASE:
        return None
    formula = row.get("assigned_formula")
    if not formula or neutral_is_closed_shell(str(formula)):
        return None
    return _reason(
        REASON_ODD_ELECTRON,
        f"{formula} is an odd-electron neutral - a radical rather than a "
        "molecule, and the reading that made it was elected over a "
        "closed-shell one rather than measured against it",
        caps=True,
    )


def oxygen_free_cluster_reason(
    row: dict, notation_by_id: dict[str, str]
) -> dict | None:
    """A cluster of an anion that holds on to oxygen, around a neutral with none.

    Nitrate holds on to a neutral by hydrogen bonds from its oxygen-bearing
    groups (``heuristic_filter.OXYGEN_BOUND_ANIONS``), so a reading that puts
    nitrate on a neutral with no oxygen names an ion the source is unlikely to
    make. What was measured on the peak - its mass and its envelope - still fits
    the reading, so the row keeps it at candidate. Refusing the reading in the
    search instead hands the peak to its next one, and on the gate's unlabelled
    nitrate set that is mostly the same ion without its proton, an organic
    nitrate the doubt does not reach: the refusal was measured, and it put more
    such readings at assigned than the cap takes.

    No corroboration lifts it, for the radical rule's reason: the doubt is
    about the ion, and a second channel's reading of the neutral says nothing
    about whether nitrate holds on to it.

    A row of the target library is exempt: the workspace named that compound
    for the modes its collection is attached to. A reference list's row is not,
    since a list names a compound and not the channel it is seen through.

    :param row: A committed monoisotopic row.
    :param notation_by_id: The run's mechanisms, by the id the rows carry.
    :return: The reason, or None where the reading is not such a cluster.
    """
    if is_target_library_row(row):
        return None
    formula = row.get("assigned_formula")
    notation = notation_by_id.get(str(row.get("ionization_mechanism_id")))
    if not oxygen_free_cluster(formula, notation):
        return None
    return _reason(
        REASON_OXYGEN_FREE_CLUSTER,
        f"{formula} carries no oxygen, and a {notation} cluster holds on to a "
        "neutral by hydrogen bonds from its oxygen-bearing groups, so the source "
        "is unlikely to make this ion",
        caps=True,
    )


def polyhalide_cluster_reason(row: dict, notation_by_id: dict[str, str]) -> dict | None:
    """A halide attached to a neutral made of halogens only.

    The ion is a polyhalide anion, and a halide source makes those from any
    halogen molecule that reaches it: the air's, which is how a bromide source
    measures I2, IBr and ICl, and its own, from impurities of a halogen supply
    and from species the walls give back
    (``heuristic_filter.polyhalide_cluster``). The mass and the envelope fit
    both origins alike, so the reading is kept at candidate rather than refused:
    only how the peak moves over time can tell the air from the source.

    No corroboration lifts it, for the oxygen-free cluster's reason: the doubt
    is about where the ion came from, and a second channel's reading of the
    neutral does not say.

    A row of the target library is exempt, as it is from the oxygen-free
    cluster: the workspace named that compound for the modes its collection is
    attached to.

    :param row: A committed monoisotopic row.
    :param notation_by_id: The run's mechanisms, by the id the rows carry.
    :return: The reason, or None where the reading is not such a cluster.
    """
    if is_target_library_row(row):
        return None
    formula = row.get("assigned_formula")
    notation = notation_by_id.get(str(row.get("ionization_mechanism_id")))
    if not polyhalide_cluster(formula, notation):
        return None
    return _reason(
        REASON_POLYHALIDE_CLUSTER,
        f"{formula} is made of halogens only, and through {notation} it makes a "
        "polyhalide anion that a halide source also makes from its own halogens, "
        "so the peak cannot say whether it came from the air or from the source",
        caps=True,
    )


def density_reason(row: dict) -> dict | None:
    """The evidence left rivals standing, and nothing else saw the neutral.

    The only escape is a second channel. A committed isotopologue envelope is
    one of the corroboration flags the plan lists, and it is deliberately not
    an escape here, on the measurement: of the 2,636 assigned rows this rule
    takes on the assignment gate, 54 own a committed isotopologue row, and the
    reference confirms 6 of those and contradicts 13. An envelope is already
    inside the fit that failed to separate the rivals, so it cannot break a tie
    the fit left; a second channel is evidence from outside the peak.
    """
    density = _provenance(row).get(CANDIDATE_DENSITY)
    if not isinstance(density, int) or density < DENSITY_LIMIT:
        return None
    if is_corroborated(row):
        return None
    grid = _provenance(row).get(GRID_RIVALS)
    added = grid.get("added") if isinstance(grid, dict) else None
    searched = ""
    if isinstance(added, int) and added:
        named = ", ".join(
            str(rival.get("formula")) for rival in (grid.get("rivals") or [])[:3]
        )
        searched = f", {added} of them from the formula search" + (
            f" ({named}{', ...' if added > 3 else ''})" if named else ""
        )
    return _reason(
        REASON_CANDIDATE_DENSITY,
        f"{density} formulas this peak's evidence could not separate{searched}, "
        "and no second channel of this run committed the same neutral",
        caps=True,
    )


def envelope_neighbours(
    m0_rows: list[dict],
    *,
    mz_tolerance_ppm: float,
    abundance_floor: float,
) -> dict[str, dict]:
    """Which committed rows sit on a line another committed row predicts.

    Decision 11's rider. Two committed readings a mass apart that is exactly one
    of the first one's isotope spacings are not independent claims: the second
    peak is the line the first one's envelope already accounts for, and
    committing a compound on it says the spectrum holds two things where the
    evidence holds one.

    The envelope has to be predicted here rather than read off the ledger. A
    predicted line that landed on a peak ANOTHER reading had already claimed
    never became an isotopologue row - peaks are claimed once - so by the time
    the ledger exists the collision is exactly the thing that was dropped.

    Height is what makes the rule a rule rather than a mass coincidence. A
    spectrum is dense enough that some committed peak sits one 13C spacing above
    another most of the time; what distinguishes an isotope line from a compound is
    that the line is no taller than its prediction. Without that test the
    rule takes more rows the reference confirms than rows it does not: on the
    step 2.3 ledgers it flags 342 of set B's rows and 203 are confirmed.

    :param m0_rows: The run's committed monoisotopic rows.
    :param mz_tolerance_ppm: The matcher's own window, so a line this claims
        would have been claimed by the targeted matcher too.
    :param abundance_floor: How deep an envelope is predicted, relative to the
        ion's own line - the run's own floor.
    :return: Peak assignment id -> the neighbour and line explaining it.
    """
    ordered = sorted(m0_rows, key=lambda row: float(row.get("sample_peak_mz") or 0.0))
    mzs = [float(row.get("sample_peak_mz") or 0.0) for row in ordered]
    found: dict[str, dict] = {}
    for owner in ordered:
        # Only a reading the run stands behind predicts lines that can take
        # another row's tier. The line's height comes from the owner's formula -
        # its carbon count, its halogens - and a formula below assignability is
        # one whose envelope the run itself does not believe. Measured on the
        # gate before this condition, those owners took 52 of the rule's 218
        # rows and were wrong on 4 of them, the rule's worst precision by owner
        # tier (assigned owners: 1 of 76).
        if owner.get("tier") not in (TIER_ASSIGNED, TIER_CANDIDATE):
            continue
        owner_intensity = float(owner.get("sample_peak_intensity") or 0.0)
        if owner_intensity <= 0:
            continue
        envelope = predicted_envelope(owner.get("ion_formula"), abundance_floor)
        if envelope is None:
            continue
        predicted_mzs, shares, labels = envelope
        for index in range(1, len(predicted_mzs)):
            line_mz = float(predicted_mzs[index])
            share = float(shares[index])
            window = line_mz * mz_tolerance_ppm * 1e-6
            low = bisect.bisect_left(mzs, line_mz - window)
            high = bisect.bisect_right(mzs, line_mz + window)
            for candidate in ordered[low:high]:
                # A reading is never its own isotope line. Nothing in THIS module
                # makes that true: it holds because every substitution the
                # predictor returns adds mass, so a line cannot land back on the
                # peak it was predicted from - a fact of `predict_isotopes` two
                # modules away, and the reason to state it here rather than rely
                # on it. Keyed on the peak so that two rows committed on ONE
                # peak, which an imported ledger may hold, are also not read as
                # each other's line.
                if candidate.get("sample_peak_id") == owner.get("sample_peak_id"):
                    continue
                key = str(candidate.get("peak_assignment_id"))
                if key in found:
                    continue
                intensity = float(candidate.get("sample_peak_intensity") or 0.0)
                if intensity > owner_intensity * share * ENVELOPE_HEIGHT_TOLERANCE:
                    continue
                found[key] = {
                    "neighbour": str(owner.get("peak_assignment_id")),
                    "neighbour_formula": owner.get("assigned_formula"),
                    "line": labels[index],
                    "line_mz": round(line_mz, 6),
                    "predicted_share": round(share, 6),
                }
    return found


def predicted_envelope(
    ion_formula, abundance_floor: float
) -> tuple[np.ndarray, np.ndarray, list[str]] | None:
    """An ion's predicted lines, its own first, with heights relative to it.

    :param ion_formula: The ion, charge last (``C6H13O6+``).
    :param abundance_floor: How deep to predict, relative to the ion's own line.
    :return: The lines' m/z, relative heights and labels, or None for an ion
        with nothing beyond its own line, or none the predictor can read.
    """
    ion = str(ion_formula or "")
    if len(ion) < 2 or ion[-1] not in "+-":
        return None
    try:
        mzs, intensities, labels = predict_isotopes(
            ion[:-1], 1 if ion[-1] == "+" else -1, threshold=abundance_floor
        )
    except Exception:  # noqa: BLE001 - an unpredictable ion predicts nothing
        return None
    if mzs.size < 2:
        return None
    mzs, intensities, labels = anchor_on_monoisotopic(mzs, intensities, labels)
    base = float(intensities[0])
    if base <= 0:
        return None
    return mzs, intensities / base, labels


def envelope_reason(row: dict, on_a_neighbours_line: dict[str, dict]) -> dict | None:
    """The peak is a line a committed neighbour's envelope already accounts for.

    Carries the neighbour and the line beside the sentence, which is what
    :func:`find_envelope_claims` reads the row's claim off.
    """
    hit = on_a_neighbours_line.get(str(row.get("peak_assignment_id")))
    if not hit:
        return None
    return {
        **_reason(
            REASON_ENVELOPE_NEIGHBOUR,
            f"this peak is the {hit['line']} line of {hit['neighbour_formula']}, "
            f"predicted at {hit['predicted_share']:.1%} of that reading's own line "
            "and tall enough here to account for the peak outright",
            caps=True,
        ),
        "neighbour": hit["neighbour"],
        "line": hit["line"],
        "line_mz": hit["line_mz"],
        "predicted_share": hit["predicted_share"],
    }


def implausibility_reasons(row: dict) -> list[dict]:
    """The named shapes of a formula that fits a mass and not a chemistry.

    A CURATED row is exempt, for the reason the radical rule gives: every
    signature names what a mass search produces, and a curated row was matched
    to an identity somebody authored rather than found by one. The library
    leaves this check to its caller, because a signature is a function of the
    formula alone and the source belongs to the row. Where the signatures reach
    curated rows on the gate, the shape is the library's identity and not a
    fit's: carbon-free on trisulfur and on the bromine of the dibromide reagent
    ion, which took 7 assigned rows the reference confirms none of, and the
    oxygen lattice on peroxyacetyl nitrate, a species these sources are built
    to see.
    """
    if row.get("source") == SOURCE_DATABASE:
        return []
    return [
        _reason(
            signature,
            f"{row.get('assigned_formula')} carries the {signature.replace('_', ' ')} "
            "signature of a formula arrived at by mass rather than by chemistry",
            caps=True,
        )
        for signature in implausible_signatures(row.get("assigned_formula"))
    ]


def standing_reasons(row: dict) -> list[dict]:
    """Why a row that nothing capped holds the tier it holds.

    Never empty for a committed row, which is what makes "every committed row
    carries a reason" true rather than nearly true.
    """
    reasons: list[dict] = []
    channels = corroborating_channels(row)
    if channels >= CHANNELS_FOR_CORROBORATION:
        reasons.append(
            _reason(
                REASON_CORROBORATED,
                f"the same neutral is committed through {channels} of the run's "
                "ionization channels",
                caps=False,
            )
        )
    settled = (_provenance(row).get("cross_channel") or {}).get(SAME_ION_SETTLED)
    if isinstance(settled, dict):
        reasons.append(
            _reason(REASON_SAME_ION_SETTLED, settled_detail(row, settled), caps=False)
        )
    density = _provenance(row).get(CANDIDATE_DENSITY)
    if isinstance(density, int) and density < DENSITY_LIMIT:
        reasons.append(
            _reason(
                REASON_NO_CLOSE_RIVAL,
                # The density counts the formulas the run weighed against each
                # other, and another reading of this row's ion is not one of
                # them: it is the same measurement, which the evidence cannot
                # separate from the row by construction.
                "the evidence separates this ion from every other the run "
                "competed for the peak; which reading of the ion it is, the "
                "evidence cannot say"
                if isinstance(settled, dict)
                else "the evidence separates this formula from every other "
                "candidate the run competed for the peak",
                caps=False,
            )
        )
    if not reasons:
        reasons.append(
            _reason(
                REASON_NOT_MEASURED,
                "nothing this pass reads was recorded for this row - it holds "
                "the tier its evidence earned and nothing more is claimed",
                caps=False,
            )
        )
    return reasons


def _cap(row: dict) -> bool:
    """Take a row's top tier, if it still has one. Never promotes."""
    if row.get("tier") != TIER_ASSIGNED:
        return False
    row["tier"] = TIER_CANDIDATE
    return True


def apply_tiering(
    assignments: Iterable[dict[str, Any]],
    *,
    mz_tolerance_ppm: float,
    abundance_floor: float,
    notation_by_id: dict[str, str] | None = None,
    tier_bands: dict[str, float] | None = None,
) -> dict:
    """Give every committed row its reasons, and cap the rows that earned it.

    Runs last, over the committed rows of both stages together, because three of
    the questions it asks - what else saw this neutral, whose envelope already
    predicts this peak, what the earlier passes decided - are properties of the
    finished ledger rather than of either stage.

    :param assignments: Every assignment of the run, mutated in place. Rows that
        commit no formula are left untouched.
    :param mz_tolerance_ppm: The run's own match window, for the envelope rule.
    :param abundance_floor: The run's own envelope floor, for the same rule.
    :param notation_by_id: The run's mechanisms, by the id the rows carry, for
        the rule that reads a row's channel. Without them no row names a channel.
    :param tier_bands: The run's evidence bands, ``assigned`` and ``candidate``,
        which a row under the top one names first (:func:`band_reason`). Without
        them no row names its band.
    :return: What the run should record about this pass: its rule version, what
        each rule capped, and the thresholds it capped on - a tier is only
        comparable across runs together with the rules that produced it.
    """
    notation_by_id = notation_by_id or {}
    rows = list(assignments)
    committed = [row for row in rows if row.get("assigned_formula")]
    m0 = [row for row in committed if row.get("role") == ROLE_M0]

    on_a_neighbours_line = envelope_neighbours(
        m0, mz_tolerance_ppm=mz_tolerance_ppm, abundance_floor=abundance_floor
    )

    capped_by_rule: dict[str, int] = {}
    # Owners whose tier THIS pass took, and owners an earlier pass had already
    # capped - kept apart so the run can say which of the two its isotopologue rows
    # followed.
    capped_here: set[str] = set()
    capped_earlier: set[str] = set()
    capped = 0
    under_band = 0

    for row in m0:
        reasons = earlier_reasons(row)
        for reason in (
            odd_electron_reason(row),
            oxygen_free_cluster_reason(row, notation_by_id),
            polyhalide_cluster_reason(row, notation_by_id),
            density_reason(row),
            envelope_reason(row, on_a_neighbours_line),
        ):
            if reason:
                reasons.append(reason)
        reasons.extend(implausibility_reasons(row))
        mine = [
            reason
            for reason in reasons
            if reason["caps"] and reason["rule"] not in _EARLIER_RULES
        ]
        held_down = any(reason["caps"] for reason in reasons)
        if not held_down:
            reasons.extend(standing_reasons(row))
        # The band leads, and it is not a rule: it is the floor the rules lower
        # from, so it neither counts as a cap here nor hides what the row
        # stands on - a row the band holds low still says what it has.
        band = band_reason(row, tier_bands)
        if band:
            reasons.insert(0, band)
            under_band += 1
        if mine and _cap(row):
            capped += 1
            for reason in mine:
                # A row can carry more than one rule's name, so these sum ABOVE
                # `capped`: they count what each rule found, not how many rows
                # it alone was responsible for.
                capped_by_rule[reason["rule"]] = (
                    capped_by_rule.get(reason["rule"], 0) + 1
                )
        _provenance(row)["tier_reasons"] = reasons
        row_id = str(row.get("peak_assignment_id"))
        if mine:
            capped_here.add(row_id)
        elif held_down:
            capped_earlier.add(row_id)

    capped_isotopologues = 0
    capped_isotopologues_after_earlier_pass = 0
    claimed = carried = 0
    for row in committed:
        if row.get("role") != ROLE_ISO_CHILD:
            continue
        owner_id = str(row.get("owner_peak_assignment_id") or "")
        if not owner_id:
            # Committed with no owner recorded, so there is no owner's answer to
            # carry. It says that rather than nothing, and keeps the tier its
            # evidence earned.
            _provenance(row)["tier_reasons"] = [
                _reason(
                    REASON_NOT_MEASURED,
                    "an isotopologue with no owner recorded on the run, so no "
                    "monoisotopic row's judgement reaches it - it holds the tier "
                    "its evidence earned and nothing more is claimed",
                    caps=False,
                )
            ]
            continue
        # An isotopologue row is its owner's ion on another line of its envelope, so
        # every question this pass asks was answered about the owner. It carries
        # the answer rather than a copy of the reasoning - and follows its owner
        # down whichever pass took the owner's tier. That is uniform where the
        # earlier passes were not: the mass gate and the reagent-N rule cap
        # their own isotopologue rows, while the minor-channel cap touches M0 rows
        # only (it cannot reach an owner WITH an isotopologue, since a committed
        # isotopologue is its escape). On the assignment gate no isotopologue of
        # an earlier-capped owner was left standing, so this adds no demote
        # there; it is the rule stated once rather than three ways.
        #
        # What the gate found of the row's own line comes first, and a line a
        # claim read as the owner's says so: both are about this peak rather
        # than about the owner.
        owner_capped = owner_id in capped_here or owner_id in capped_earlier
        reasons = earlier_reasons(row)
        claim = _provenance(row).get(ENVELOPE_CLAIM)
        if isinstance(claim, dict):
            reasons.append(claim_reason(row, claim))
            if claim.get("carried_with"):
                carried += 1
            else:
                claimed += 1
        reasons.append(
            _reason(
                REASON_INHERITED,
                "an isotopologue of a reading judged on its own monoisotopic row",
                caps=owner_capped,
            )
        )
        _provenance(row)["tier_reasons"] = reasons
        if owner_capped and _cap(row):
            if owner_id in capped_here:
                capped_isotopologues += 1
            else:
                capped_isotopologues_after_earlier_pass += 1

    return {
        "version": TIERING_RULES_VERSION,
        "committed_m0": len(m0),
        "capped": capped,
        "capped_isotopologues": capped_isotopologues,
        # Isotopologue rows of an owner an EARLIER pass capped, which that pass left
        # standing. Separate from the above, whose owners this pass capped.
        "capped_isotopologues_after_earlier_pass": capped_isotopologues_after_earlier_pass,
        "capped_by_rule": capped_by_rule,
        # Rows read as an assigned neighbour's line, and the isotopologues they
        # had carried as compounds of their own that went with them.
        "claimed": claimed,
        "claimed_with_their_lines": carried,
        # Monoisotopic rows whose evidence is under the top band, which name
        # the band first.
        "under_band": under_band,
        "density_limit": DENSITY_LIMIT,
        "envelope_height_tolerance": ENVELOPE_HEIGHT_TOLERANCE,
    }


def claim_reason(row: dict, claim: dict) -> dict:
    """What a line a claim read as its owner's says about it."""
    displaced = claim.get("displaced") or {}
    formula = displaced.get("assigned_formula") or "another formula"
    line = claim.get("line")
    if claim.get("carried_with"):
        detail = (
            f"committed as an isotopologue of {formula}, a reading this run gave "
            f"up for a line of {row.get('assigned_formula')}; the {line} line of "
            f"{row.get('assigned_formula')} is predicted on this peak too"
        )
    else:
        predicted, observed = claim.get("predicted_share"), claim.get("observed_share")
        heights = (
            f", predicted at {predicted:.2%} of that reading's own line and found at "
            f"{observed:.2%}"
            if isinstance(predicted, (int, float))
            and isinstance(observed, (int, float))
            else ""
        )
        detail = (
            f"this peak is the {line} line of {row.get('assigned_formula')}, which "
            f"the run holds at assigned{heights}; it was committed as {formula} "
            "first, which is what puts the line in doubt, so it is read as that "
            "isotopologue at candidate"
        )
    return _reason(REASON_ENVELOPE_CLAIM, detail, caps=True)


def find_envelope_claims(
    assignments: Iterable[dict[str, Any]],
    *,
    mz_tolerance_ppm: float,
    abundance_floor: float,
    precision_ppm: float,
    lines: SpectrumLines | None = None,
) -> tuple[list[EnvelopeClaim], dict[str, int]]:
    """The rows on a neighbour's line that are read as that line.

    Reads what :func:`apply_tiering` left: the envelope reason on each row it
    found on a neighbour's line, and every row's final tier. So it is asked
    after that pass, over the same rows.

    A row is read as the neighbour's line when the neighbour holds ``assigned``
    and none of these holds it back: the row is a compound of the workspace's
    own target library, which somebody named for this data; a second channel of
    the run committed its neutral, which is evidence from outside the peak; the
    neighbour already holds a line within the window of the predicted one; or
    the peak's mass error does not follow the neighbour's even allowing for what
    its line can deliver (``mass_gate.tracking_of``), which is the tracking
    test's own word for a peak that is not this ion's line. A row held back
    keeps its reading, and its envelope reason says which of these held it.

    Where two rows sit on one predicted line, the nearer one is read as it. The
    row's own isotopologues go with it where the neighbour's envelope predicts
    their lines too, by the same tests, and leave the ledger where it does not.

    :param assignments: Every row of the run, after :func:`apply_tiering`.
    :param mz_tolerance_ppm: The run's own match window.
    :param abundance_floor: The run's own envelope floor.
    :param precision_ppm: The instrument class's precision.
    :param lines: The sample's peaks, read for how well each places its line.
    :return: The claims, and how many rows each reason held back.
    """
    committed = [row for row in assignments if row.get("assigned_formula")]
    by_id = {str(row.get("peak_assignment_id")): row for row in committed}
    lines_of: dict[str, list[dict]] = defaultdict(list)
    for row in committed:
        owner_id = row.get("owner_peak_assignment_id")
        if row.get("role") == ROLE_ISO_CHILD and owner_id:
            lines_of[str(owner_id)].append(row)

    flagged: list[tuple[float, dict, dict, dict]] = []
    for row in committed:
        if row.get("role") != ROLE_M0:
            continue
        entry = next(
            (
                reason
                for reason in _provenance(row).get("tier_reasons") or []
                if reason.get("rule") == REASON_ENVELOPE_NEIGHBOUR
            ),
            None,
        )
        owner = by_id.get(str((entry or {}).get("neighbour")))
        if entry is None or owner is None or entry.get("line_mz") is None:
            continue
        line_mz = float(entry["line_mz"])
        distance = abs(_mz(row) - line_mz) / line_mz
        flagged.append((distance, row, owner, entry))

    envelopes: dict[str, tuple | None] = {}
    taken: set[tuple[str, str]] = set()
    claims: list[EnvelopeClaim] = []
    held: Counter = Counter()
    for _, row, owner, entry in sorted(flagged, key=lambda item: item[0]):
        owner_id = str(owner.get("peak_assignment_id"))
        why = None
        line = None
        if owner.get("tier") != TIER_ASSIGNED:
            why = HELD_NEIGHBOUR_NOT_ASSIGNED
        elif is_target_library_row(row):
            why = HELD_TARGET_LIBRARY
        elif is_corroborated(row):
            why = HELD_CORROBORATED
        else:
            line = _as_line(
                row,
                owner,
                str(entry.get("line")),
                float(entry["line_mz"]),
                float(entry.get("predicted_share") or 0.0),
                precision_ppm=precision_ppm,
                lines=lines,
            )
            if (owner_id, line.label) in taken or _holds_line_near(
                lines_of.get(owner_id, ()), line.line_mz, mz_tolerance_ppm
            ):
                why = HELD_LINE_TAKEN
            elif line.tracking == TRACKING_UNTRACKED:
                why = HELD_UNTRACKED
        if why is not None:
            held[why] += 1
            entry["detail"] = (
                f"{entry.get('detail', '')}; it is not read as that line, because "
                f"{_HELD_SENTENCES[why]}"
            )
            continue
        taken.add((owner_id, line.label))
        if owner_id not in envelopes:
            envelopes[owner_id] = predicted_envelope(
                owner.get("ion_formula"), abundance_floor
            )
        carried: list[ClaimedLine] = []
        released: list[str] = []
        for child in lines_of.get(str(row.get("peak_assignment_id")), ()):
            child_line = _predicted_line_for(
                child,
                owner,
                envelopes[owner_id],
                mz_tolerance_ppm=mz_tolerance_ppm,
                precision_ppm=precision_ppm,
                lines=lines,
            )
            if (
                child_line is None
                or child_line.tracking == TRACKING_UNTRACKED
                or (owner_id, child_line.label) in taken
                or _holds_line_near(
                    lines_of.get(owner_id, ()), child_line.line_mz, mz_tolerance_ppm
                )
            ):
                released.append(str(child.get("peak_assignment_id")))
                continue
            taken.add((owner_id, child_line.label))
            carried.append(child_line)
        claims.append(
            EnvelopeClaim(
                owner_id=owner_id,
                line=line,
                carried=tuple(carried),
                released=tuple(released),
            )
        )
    return claims, dict(held)


def _mz(row: dict) -> float:
    return float(row.get("sample_peak_mz") or 0.0)


def _as_line(
    row: dict,
    owner: dict,
    label: str,
    line_mz: float,
    share: float,
    *,
    precision_ppm: float,
    lines: SpectrumLines | None,
) -> ClaimedLine:
    """A peak read as one predicted line of the owner, with its tracking."""
    return ClaimedLine(
        row_id=str(row.get("peak_assignment_id")),
        label=label,
        line_mz=line_mz,
        share=share,
        tracking=tracking_of(
            (_mz(row) - line_mz) / line_mz * 1e6,
            owner.get("mz_error_ppm"),
            precision_ppm=precision_ppm,
            child=UNREAD_LINE if lines is None else lines.of(row.get("sample_peak_id")),
            parent=(
                UNREAD_LINE if lines is None else lines.of(owner.get("sample_peak_id"))
            ),
        ),
    )


def _predicted_line_for(
    child: dict,
    owner: dict,
    envelope: tuple | None,
    *,
    mz_tolerance_ppm: float,
    precision_ppm: float,
    lines: SpectrumLines | None,
) -> ClaimedLine | None:
    """The owner's predicted line a peak sits on, by the envelope rule's tests."""
    if envelope is None:
        return None
    predicted_mzs, shares, labels = envelope
    mz = _mz(child)
    intensity = float(child.get("sample_peak_intensity") or 0.0)
    owner_intensity = float(owner.get("sample_peak_intensity") or 0.0)
    best = None
    for index in range(1, len(predicted_mzs)):
        line_mz = float(predicted_mzs[index])
        if abs(mz - line_mz) > line_mz * mz_tolerance_ppm * 1e-6:
            continue
        if (
            intensity
            > owner_intensity * float(shares[index]) * ENVELOPE_HEIGHT_TOLERANCE
        ):
            continue
        if best is None or abs(mz - line_mz) < abs(mz - best[1]):
            best = (index, line_mz)
    if best is None:
        return None
    index, line_mz = best
    return _as_line(
        child,
        owner,
        str(labels[index]),
        line_mz,
        float(shares[index]),
        precision_ppm=precision_ppm,
        lines=lines,
    )


def _holds_line_near(
    owned: Iterable[dict], line_mz: float, tolerance_ppm: float
) -> bool:
    """Whether an owner already committed a line within the window of this one."""
    window = line_mz * tolerance_ppm * 1e-6
    return any(abs(_mz(line) - line_mz) <= window for line in owned)
