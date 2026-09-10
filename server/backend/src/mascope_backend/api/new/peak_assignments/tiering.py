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

The rules, and what each is worth on the 43-sample assignment gate
-----------------------------------------------------------------

``odd_electron`` - the committed neutral breaks the nitrogen rule, so it names a
radical rather than a molecule. Measured on the gate, this is the sharpest rule
the engine has and it needs no condition at all: of 1,794 assigned-tier rows
whose neutral is odd-electron, across all eight sets, the reference engine
confirms **none**. It is symmetric - the reference commits 629 such rows of its
own, and on the 462 peaks where both engines commit one, they agree on the
formula zero times. Decision 9 already prefers the closed-shell reading when two
readings make one ion; this says the same thing about a reading with no rival:
a radical is a tie-break, and a tie-break is not evidence.

``candidate_density`` - the run could not separate this peak's winner from other
formulas, and nothing outside the peak corroborates it. Density is measured in
the finder over the full candidate list (``arbitration.candidate_density``),
which is the only place it can be: the row stores at most
``max_alternatives`` of the competitors, so counting those counts the cap.

The implausibility signatures - ``carbon_cluster``, ``oxygen_lattice``,
``carbon_free`` - are named formula shapes that are the product of a mass search
rather than of a source (``mascope_tools.composition.implausibility``). They are
guards rather than levers: on the gate they reach a few hundred rows in total,
and the value of writing them is that a row demoted on one can say which shape
it has.

``envelope_neighbour`` - decision 11's rider. An M0 committed on a peak that a
committed neighbour's envelope predicts a line for, at a height that could
account for the peak, is more likely to be that line than a compound of its own.

Rules the earlier passes already applied
----------------------------------------

The mass gate (step 2.2) and the cross-channel pass (step 2.3) demote rows too,
and did so before this module existed. They keep their own provenance blocks,
which the inspector and the SDK read; this pass reads those blocks and folds
what they say into the same ``tier_reasons`` list, so a reader asking "why is
this row a candidate" gets one answer wherever the demote came from. It does not
re-apply them - a row they capped is already capped.
"""

from __future__ import annotations

import bisect
from typing import Any, Iterable

from mascope_backend.api.new.peak_assignments.cross_channel import (
    CHANNELS_FOR_CORROBORATION,
    REASON_AMBIGUOUS_NITROGEN,
)
from mascope_backend.api.new.peak_assignments.engine import (
    ROLE_ISO_CHILD,
    ROLE_M0,
    SOURCE_DATABASE,
)
from mascope_backend.api.new.peak_assignments.tiers import (
    TIER_ASSIGNED,
    TIER_CANDIDATE,
)
from mascope_tools.composition.arbitration import CANDIDATE_DENSITY
from mascope_tools.composition.heuristic_filter import (
    anchor_on_monoisotopic,
    neutral_is_closed_shell,
    predict_isotopes,
)
from mascope_tools.composition.implausibility import implausible_signatures


#: Bumped whenever a rule is added, removed or re-thresholded. Recorded on the
#: run, because a tier is only comparable across runs together with the rules
#: that produced it - the same statement the tier BANDS carry, for the same
#: reason.
TIERING_RULES_VERSION = 1

#: The row names a radical rather than a molecule.
REASON_ODD_ELECTRON = "odd_electron"

#: The peak is a line another committed reading's envelope predicts.
REASON_ENVELOPE_NEIGHBOUR = "envelope_neighbour"

#: The peak's own evidence could not separate the winner from other formulas,
#: and no second channel saw the neutral.
REASON_CANDIDATE_DENSITY = "candidate_density"

#: Reasons a row KEEPS its tier, so that every committed row carries one.
REASON_UNIQUE = "unique_in_the_searched_box"
REASON_CORROBORATED = "corroborated"
REASON_INHERITED = "inherited_from_owner"

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
    {REASON_OFF_CALIBRATION, REASON_AMBIGUOUS_NITROGEN, REASON_MINOR_CHANNEL}
)

#: How much taller than a neighbour's predicted line a peak may be and still
#: be read as that line. A predicted satellite and the peak it lands on rarely
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
    the two cannot disagree about the same ledger. A satellite carries an
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
    if mass_gate.get("capped"):
        z = mass_gate.get("mass_z", provenance.get("mass_z"))
        reasons.append(
            _reason(
                REASON_OFF_CALIBRATION,
                f"mass error sits {z} widths off the run's own fitted centre "
                "with nothing corroborating the reading",
                caps=True,
            )
        )
    cross_channel = provenance.get("cross_channel") or {}
    if cross_channel.get("capped"):
        ambiguity = cross_channel.get("ambiguous_nitrogen") or {}
        alternative = ambiguity.get("alternative")
        reasons.append(
            _reason(
                REASON_AMBIGUOUS_NITROGEN,
                "the same ion reads as "
                f"{alternative or 'a nitrogen-richer neutral'} through a channel "
                "donating no nitrogen, and no channel of this run fixes the count",
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
    gate the exemption spares 8 assigned rows, none of which the reference
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


def density_reason(row: dict) -> dict | None:
    """The evidence left rivals standing, and nothing else saw the neutral."""
    density = _provenance(row).get(CANDIDATE_DENSITY)
    if not isinstance(density, int) or density < DENSITY_LIMIT:
        return None
    if is_corroborated(row):
        return None
    return _reason(
        REASON_CANDIDATE_DENSITY,
        f"{density} formulas this peak's evidence could not separate, and no "
        "second channel of this run committed the same neutral",
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
    another most of the time; what distinguishes a satellite from a compound is
    that the satellite is no taller than the line predicts. Without that test the
    rule takes as many rows the reference confirms as rows it does not.

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
        ion = str(owner.get("ion_formula") or "")
        if len(ion) < 2 or ion[-1] not in "+-":
            continue
        charge = 1 if ion[-1] == "+" else -1
        owner_intensity = float(owner.get("sample_peak_intensity") or 0.0)
        if owner_intensity <= 0:
            continue
        try:
            predicted_mzs, predicted_intensities, labels = predict_isotopes(
                ion[:-1], charge, threshold=abundance_floor
            )
        except Exception:  # noqa: BLE001 - an unpredictable ion demotes nothing
            continue
        if predicted_mzs.size < 2:
            continue
        predicted_mzs, predicted_intensities, labels = anchor_on_monoisotopic(
            predicted_mzs, predicted_intensities, labels
        )
        base = float(predicted_intensities[0])
        if base <= 0:
            continue
        for index in range(1, len(predicted_mzs)):
            line_mz = float(predicted_mzs[index])
            share = float(predicted_intensities[index]) / base
            window = line_mz * mz_tolerance_ppm * 1e-6
            low = bisect.bisect_left(mzs, line_mz - window)
            high = bisect.bisect_right(mzs, line_mz + window)
            for candidate in ordered[low:high]:
                # A reading is never its own satellite. Nothing in THIS module
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
                    "predicted_share": round(share, 4),
                }
    return found


def envelope_reason(row: dict, on_a_neighbours_line: dict[str, dict]) -> dict | None:
    """The peak is a line a committed neighbour's envelope already accounts for."""
    hit = on_a_neighbours_line.get(str(row.get("peak_assignment_id")))
    if not hit:
        return None
    return _reason(
        REASON_ENVELOPE_NEIGHBOUR,
        f"this peak is the {hit['line']} line of {hit['neighbour_formula']}, "
        f"predicted at {hit['predicted_share']:.1%} of that reading's own line "
        "and tall enough here to account for the peak outright",
        caps=True,
    )


def implausibility_reasons(row: dict) -> list[dict]:
    """The named shapes of a formula that fits a mass and not a chemistry."""
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
    density = _provenance(row).get(CANDIDATE_DENSITY)
    if isinstance(density, int) and density < DENSITY_LIMIT:
        reasons.append(
            _reason(
                REASON_UNIQUE,
                "no other formula in the box this run searched explains the peak "
                "as well",
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
    :return: What the run should record about this pass: its rule version, what
        each rule capped, and the thresholds it capped on - a tier is only
        comparable across runs together with the rules that produced it.
    """
    rows = list(assignments)
    committed = [row for row in rows if row.get("assigned_formula")]
    m0 = [row for row in committed if row.get("role") == ROLE_M0]

    on_a_neighbours_line = envelope_neighbours(
        m0, mz_tolerance_ppm=mz_tolerance_ppm, abundance_floor=abundance_floor
    )

    capped_by_rule: dict[str, int] = {}
    capped_ids: set[str] = set()
    capped = 0

    for row in m0:
        reasons = earlier_reasons(row)
        for reason in (
            odd_electron_reason(row),
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
        if not any(reason["caps"] for reason in reasons):
            reasons.extend(standing_reasons(row))
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
        if any(reason["caps"] for reason in reasons):
            capped_ids.add(str(row.get("peak_assignment_id")))

    capped_satellites = 0
    for row in committed:
        if row.get("role") != ROLE_ISO_CHILD:
            continue
        owner_id = str(row.get("owner_peak_assignment_id") or "")
        if not owner_id:
            continue
        # A satellite is its owner's ion on a second line of one envelope, so
        # every question this pass asks was answered about the owner. It carries
        # the answer rather than a copy of the reasoning.
        owner_capped = owner_id in capped_ids
        _provenance(row)["tier_reasons"] = [
            _reason(
                REASON_INHERITED,
                "an isotopologue of a reading judged on its own monoisotopic row",
                caps=owner_capped,
            )
        ]
        if owner_capped and _cap(row):
            capped_satellites += 1

    return {
        "version": TIERING_RULES_VERSION,
        "committed_m0": len(m0),
        "capped": capped,
        "capped_satellites": capped_satellites,
        "capped_by_rule": capped_by_rule,
        "density_limit": DENSITY_LIMIT,
        "envelope_height_tolerance": ENVELOPE_HEIGHT_TOLERANCE,
    }
