"""The partner gates: a reading through a minor channel stands only with its partner.

A secondary-channel or partner-gated reading is kept where the channel's own
partner line shows beside it at the ratio the mechanism predicts, or a list
names the neutral; outweighed by the reading its peak's own channel gives, it
gives way (``apply_partner_gates``).
"""

import math

from mascope_backend.api.new.peak_assignments.engine import (
    _float_or_none,
    shows_molecule,
    tier_for_evidence,
    tier_under_ceiling,
)
from mascope_backend.api.new.peak_assignments.row_terms import (
    ROLE_ISO_CHILD,
    ROLE_M0,
    SOURCE_DATABASE,
)
from mascope_backend.api.new.peak_assignments.tiers import TIER_ASSIGNED, TIER_CANDIDATE
from mascope_tools.composition.heuristic_filter import neutral_is_closed_shell
from mascope_tools.composition.utils import calculate_mass, formula_identity


#: The key an alternative carries when it is an opportunistic reading of the
#: row's ion that the sample did not bear out: set aside by the partner gate,
#: kept on the row for the reader, and no rival in the cross-channel pass.
PARTNER_GATE_UNMET = "unmet"

#: The key an alternative carries when the sample bore it out, and bore the
#: row's own reading out decisively more strongly (:func:`outweighs`): kept on
#: the row, since the sample did show its molecule. The mark is the contest's
#: record for the reader. The cross-channel pass does not read it: it weighs
#: the same partners again (``cross_channel.weighed``) and settles the ion by
#: the stronger partner.
PARTNER_GATE_OUTWEIGHED = "outweighed"

#: The verdict a gated reading carries where its ion reads no other way and
#: the sample shows no partner: the row stays as the finder elected it, and
#: the second-channel cap after the gate holds it (:func:`cap_second_channel`).
PARTNER_GATE_KEPT = "no other reading of the ion"

#: The verdict a reference list's gated reading carries where the gate would
#: have made another reading of its ion the row's: the list named the compound,
#: so its reading stays the row's, under the second-channel cap, and the other
#: reading stays on the row for the cross-channel pass to weigh.
PARTNER_GATE_LIST = "a list names the compound"

#: How many times as bright the brighter of two partners' peaks has to be for
#: its reading to settle the ion. The brighter partner takes the ion at any
#: margin, since the sample shows that molecule more strongly; the margin
#: decides whether the other reading is still a doubt. On the certified
#: cylinder the benzyl and methylbenzyl cations are decided by 22 to 41 times
#: (toluene against C7H6, xylene against styrene) and C5H7+ by 7 to 10
#: (isoprene against C5H6): the better reading in every file, and under an
#: order of magnitude not a certain one, which is what the top tier is kept
#: for.
PARTNER_MARGIN = 10.0

#: How many rounds the gate walks the ledger. Every round judges every row
#: again against the partners the ledger holds at that moment - a reading's
#: partner is often a row the gate itself turned back to the mode's own
#: reading in an earlier round, or one it swapped back and so took away - and
#: the walk ends when a round changes nothing. Four rounds settled every
#: sample measured; the cap is a guard against a ledger with no settled
#: reading, not a budget, and the summary says when it was hit.
MAX_PARTNER_GATE_ROUNDS = 6


def partner_ratio(height: float, other: float) -> float | None:
    """How many times as bright one partner's peak is as another's.

    Rounded down to two decimals, a float's own noise absorbed first, and
    :func:`outweighs` reads the rounded number: a ratio short of
    :data:`PARTNER_MARGIN` never reads as the margin.

    :param height: The one partner's peak height.
    :param other: The other's.
    :return: The ratio, or None where either peak has no height.
    """
    if height <= 0 or other <= 0:
        return None
    return math.floor(round(height / other * 100, 6)) / 100


def outweighs(height: float, other: float) -> bool:
    """Whether a partner this bright settles an ion against one ``other`` bright.

    :param height: The one partner's peak height.
    :param other: The other's, 0 where that reading has none.
    :return: Whether :func:`partner_ratio` reaches :data:`PARTNER_MARGIN`, or
        the one partner has a height and the other none.
    """
    if height <= 0:
        return False
    ratio = partner_ratio(height, other)
    return ratio is None or ratio >= PARTNER_MARGIN


def apply_partner_gates(
    assignments: list[dict],
    *,
    notation_by_id: dict[str, str],
    minor_channels: frozenset[str],
    partner_gated_channels: frozenset[str],
    tier_bands: dict[str, float] | None,
) -> dict:
    """Let an opportunistic reading stand only where the sample shows its neutral.

    One ion, read two ways, is one measurement split differently between the
    analyte and the mechanism, and the finder elects the reading whose
    mechanism carries the most mass. Through an opportunistic channel that
    prior is wrong as often as it is right: every deprotonated acid also reads
    as the molecule 46 Da lighter with formate, and the C11 acid the chamber
    dataset does not contain and the C10 acid it does are told apart by one
    fact only - whether the C10 product is seen through one of the mode's own
    channels. Tropylium is the same case in positive mode: protonated C7H6
    outweighs toluene less a hydride in the election, and toluene is what the
    sample shows through electron transfer.

    Runs over the judged ledger, both stages' rows together, because the
    partner is as often a reference list's row as the search's: on the
    chamber dataset most C10 products are Stage A matches, and a gate that
    read the search's rows alone left 142 of 240 pseudo-acids standing. It
    changes readings, so it runs before every cap: after the mass gate has
    measured the run, so that a partner is a reading that gate's ceiling
    leaves committed (:func:`tier_under_ceiling`), and before the
    cross-channel pass reads the ledger, so that a reading it sets aside is no
    rival there. Every path of the stage list that judges as a run does reads
    it so, in its judge stage (``judging_loop.Judging``): a batch search over the
    rows it read in a sample, a batch decision measured on a sample over the
    rows measured there.

    A partner is a row through which the sample shows the reading's neutral
    (:func:`shows_molecule`): a monoisotopic row committed at candidate or
    better through one of the mode's own channels, compared as compositions
    (:func:`formula_identity`), so a target library's ``CH3COOH`` partners a
    formate reading of ``C2H4O2``. How strongly the sample shows a
    neutral is the height of its brightest partner's peak. The tier is the
    bar a partner clears, not a measure of it: a partner's tier here is read
    before the caps that can still lower it, and a contest the tier decided
    would corroborate the very partner it chose.

    A reading the gate gives a row by a swap takes the tier its own evidence
    earns, as a stage would have built it. The gate applies no cap: the caps
    judge the readings it settled after it, the second-channel cap
    (:func:`cap_second_channel`) among them, and the mass gate's ceiling is
    the ion's line's, which every reading of it shares.

    For a monoisotopic row whose reading is through a partner-gated channel:

    - the reading stands where it has a partner;
    - otherwise the mode's own reading of the ion, where the family holds one
      whose neutral is a molecule, becomes the row's, keeping the fit and mass
      error that are the ion's, with its tier read off its own plausibility;
      failing that, the opportunistic reading whose neutral the sample shows
      most strongly; failing that, the row stays as it is, and the
      second-channel cap after the gate holds it at candidate.

    A reference list's row through a gated channel, which Stage A reads through
    the channels the run opened, is held to the same bar and never moved: the
    list named the compound, and taking another reading of the ion would put
    the list's name on a formula it does not hold. Where it has no partner it
    keeps its reading (:data:`PARTNER_GATE_LIST` where the gate would have
    taken another reading), and the reading the gate would have taken stays
    on the row for the cross-channel pass to weigh.

    The ledger is walked to a fixed point: every round judges every row
    against the partners the ledger holds at that moment, a swap changes the
    partners the next row sees at once, a row swapped to the mode's reading
    whose set-aside reading a later round bears out swaps back, and a row
    that stood on a partner a swap back took is judged again. The walk ends
    when a round changes nothing (:data:`MAX_PARTNER_GATE_ROUNDS`).

    Then the contest, once, against the partners the walk settled on. A row
    standing on a partner through a gated channel is weighed against every
    other reading of its ion through a gated channel whose molecule has a
    partner too: between two readings the sample bears out, the election's
    prior for the heavier mechanism does not decide, and how strongly the
    sample bears out each does. The reading with the brighter partner is the
    row's, swapped in as above, and the row names the reading it took the ion
    from (``took_from``); at a tie the row keeps its reading. A contest swap
    moves a row between two gated readings, and a gated reading is nobody's
    partner, so it changes nothing the walk decided and the contest does not
    depend on the order of the rows. Only an opportunistic reading is
    contested: a reading through one of the mode's own channels keeps its
    formula, and so does a list's.

    The row records every reading the contest weighed (``contest``: the
    reading, its channel, the ratio of the partners' peaks and whether it is
    decisive). Where the brighter partner is :data:`PARTNER_MARGIN` times as
    bright or more (:func:`outweighs`), the other reading is marked
    :data:`PARTNER_GATE_OUTWEIGHED`, and the cross-channel pass, weighing the
    same partners again, settles the ion by the stronger partner. Within the
    margin it is left unmarked: a rival whose molecule the sample also shows,
    which the cross-channel pass weighs as the doubt it is.

    For every monoisotopic row, a same-ion reading through a gated channel is
    marked :data:`PARTNER_GATE_UNMET` exactly where its neutral has no partner
    on the settled ledger: a reference list's acid is not doubted for a
    formate reading nothing bore out, and a reading a swap earlier in the walk
    set aside that the ledger now bears out is a reading of the ion again,
    whichever reading the row ended on. The opportunistic reading a swap
    displaced for want of a partner is marked the same way, kept on the row
    for the reader, and the cross-channel pass counts neither as a rival. The
    mode's own reading a swap back displaced is not marked: it is a reading of
    the ion the sample settled, and the cross-channel pass records by what. An
    isotopologue follows its owner's reading, as it does everywhere.

    :param assignments: Both stages' rows, modified in place.
    :param notation_by_id: The run's mechanisms, by the id the rows carry.
    :param minor_channels: The notations that are secondary in this run.
    :param partner_gated_channels: The secondary channels held to a partner,
        a subset of ``minor_channels``: a gated reading is never a partner,
        which is what lets the contest be read once, after the walk.
    :param tier_bands: The run's evidence bands, for a swapped reading's tier.
    :return: A JSON-serializable summary of the ledger the gate left: rows
        standing on a partner, of them the swaps undone, rows swapped to
        another reading, rows left to the cap, rows given a tier the mass
        gate's ceiling holds
        lower (which that gate's cap holds there), readings set aside, rows
        that weighed another partnered reading and of them the ones the
        contest moved, the readings outweighed and those within
        the margin, the list rows kept on the list's reading where the walk
        or the contest would have moved them, the margin itself, the rounds
        the walk took, and whether a round changed nothing before the cap.
    :raises ValueError: Where a gated channel is not one of the minor ones.
    """
    summary = {
        "partnered": 0,
        "swapped": 0,
        "swapped_back": 0,
        "kept": 0,
        "held": 0,
        "set_aside": 0,
        "contested": 0,
        "contest_swapped": 0,
        "outweighed": 0,
        "within_margin": 0,
        "list_kept": 0,
        "margin": PARTNER_MARGIN,
        "rounds": 0,
        "settled": True,
    }
    if not partner_gated_channels:
        return summary
    if not partner_gated_channels <= minor_channels:
        raise ValueError(
            "a partner-gated channel has to be one of the run's minor channels: "
            f"{sorted(partner_gated_channels - minor_channels)}"
        )
    minor_ids = {mid for mid, n in notation_by_id.items() if n in minor_channels}
    gated_ids = {
        mid for mid, n in notation_by_id.items() if n in partner_gated_channels
    }
    if not gated_ids:
        return summary
    bands = tier_bands or {}
    candidate_threshold = bands.get(TIER_CANDIDATE)
    assigned_threshold = bands.get(TIER_ASSIGNED)

    # The rows whose reading the gate gave a tier the mass gate's ceiling
    # holds lower: its cap, after the gate, holds them there.
    held: set[str] = set()

    def earned(evidence: float) -> str | None:
        """The tier the evidence earns, before any cap or ceiling."""
        if candidate_threshold is None or assigned_threshold is None:
            return None
        return tier_for_evidence(
            evidence,
            candidate_threshold=candidate_threshold,
            assigned_threshold=assigned_threshold,
        )

    def tier_of(evidence: float, row: dict) -> str | None:
        """The tier the evidence earns, noting where the mass gate's ceiling
        is lower: the gate's cap holds the row there, not this gate."""
        tier = earned(evidence)
        if tier is None:
            return None
        row_id = str(row.get("peak_assignment_id"))
        if tier_under_ceiling({**row, "tier": tier}) != tier:
            held.add(row_id)
        else:
            held.discard(row_id)
        return tier

    def partner_key(row: dict) -> str | None:
        """The neutral a row shows the sample's molecule as, or None
        (:func:`shows_molecule`)."""
        if shows_molecule(row, notation_by_id, minor_channels):
            return formula_identity(str(row["assigned_formula"]))
        return None

    # Per neutral, the peak height of every row that commits it through a mode
    # channel - kept current through every swap, so a row is judged against
    # the ledger as it is, not as it was when the round began.
    partners: dict[str, dict[int, float]] = {}

    def track(row: dict, was: str | None) -> None:
        """Keep a row's entry current once its reading moved."""
        if was is not None:
            partners.get(was, {}).pop(id(row), None)
        now = partner_key(row)
        if now is not None:
            partners.setdefault(now, {})[id(row)] = (
                _float_or_none(row.get("sample_peak_intensity")) or 0.0
            )

    for row in assignments:
        track(row, None)

    def strongest(formula: str | None) -> float:
        """The height of a neutral's brightest partner, 0 where it has none."""
        found = partners.get(formula_identity(str(formula or "")))
        return max(found.values()) if found else 0.0

    def partnered(formula: str | None) -> bool:
        return bool(formula) and bool(
            partners.get(formula_identity(str(formula or "")))
        )

    def same_neutral(formula: str | None, other: str | None) -> bool:
        return formula_identity(str(formula or "")) == formula_identity(
            str(other or "")
        )

    def stands(row: dict) -> bool:
        """Whether a row's reading is through a gated channel, on a partner."""
        return row.get("ionization_mechanism_id") in gated_ids and partnered(
            row.get("assigned_formula")
        )

    children: dict[str, list[dict]] = {}
    for row in assignments:
        if row.get("role") == ROLE_ISO_CHILD and row.get("owner_peak_assignment_id"):
            children.setdefault(str(row["owner_peak_assignment_id"]), []).append(row)

    def molecule(alternative: dict) -> bool:
        formula = alternative.get("assigned_formula")
        return bool(
            alternative.get("same_ion")
            and formula
            and alternative.get("ionization_mechanism_id")
            and neutral_is_closed_shell(str(formula))
        )

    def contends(row: dict, alternative: dict) -> bool:
        """Whether a reading of the row's ion is weighed against the row's.

        A molecule through a gated channel, of another neutral, with a partner
        - whatever mark the walk left on it: a reading it set aside for want
        of a partner that the settled ledger bears out is weighed like any
        other.
        """
        return (
            molecule(alternative)
            and alternative["ionization_mechanism_id"] in gated_ids
            and not same_neutral(
                alternative["assigned_formula"], row.get("assigned_formula")
            )
            and partnered(alternative["assigned_formula"])
        )

    def weigh(row: dict, alternative: dict) -> dict:
        """The row's partner against another reading's, and whether decisively."""
        own = strongest(row.get("assigned_formula"))
        other = strongest(alternative.get("assigned_formula"))
        return {
            "reading": alternative.get("assigned_formula"),
            "via": notation_by_id.get(str(alternative.get("ionization_mechanism_id"))),
            "ratio": partner_ratio(own, other),
            "decisive": outweighs(own, other),
        }

    def swap(row: dict, chosen: dict, provenance: dict, gate: dict) -> None:
        """Make ``chosen`` the row's reading and set the current one aside."""
        was = partner_key(row)
        displaced = {
            "assigned_formula": row["assigned_formula"],
            "ion_formula": row.get("ion_formula"),
            "ionization_mechanism_id": row["ionization_mechanism_id"],
            "isotope_label": row.get("isotope_label"),
            "fit_score": row.get("fit_score"),
            "mz_error_ppm": row.get("mz_error_ppm"),
            "plausibility": provenance.get("plausibility"),
            "same_ion": True,
            "source": row.get("source"),
        }
        if row["ionization_mechanism_id"] in gated_ids:
            # An opportunistic reading the sample did not bear out, or one the
            # contest weighs, which marks it again.
            displaced["partner_gate"] = PARTNER_GATE_UNMET
        plausibility = float(chosen.get("plausibility") or 0.0)
        fit = float(row.get("fit_score") or 0.0)
        evidence = round(fit * plausibility, 4)
        row["assigned_formula"] = chosen["assigned_formula"]
        row["ionization_mechanism_id"] = chosen["ionization_mechanism_id"]
        # A new reading, at the tier its own evidence earns, as a stage would
        # have built it: the caps after the gate judge it from there.
        restored = tier_of(evidence, row)
        if restored is not None:
            row["tier"] = restored
        provenance["plausibility"] = plausibility
        provenance["evidence"] = evidence
        if "neutral_mass" in provenance:
            provenance["neutral_mass"] = float(
                calculate_mass(formula=str(chosen["assigned_formula"]))
            )
        provenance.pop("unsaturation", None)
        provenance["partner_gate"] = gate
        row["alternatives"] = [displaced] + [
            alt for alt in row.get("alternatives") or [] if alt is not chosen
        ]
        for child in children.get(str(row.get("peak_assignment_id")), ()):
            child["assigned_formula"] = row["assigned_formula"]
            child["ionization_mechanism_id"] = row["ionization_mechanism_id"]
        track(row, was)

    for _round in range(MAX_PARTNER_GATE_ROUNDS):
        summary["rounds"] += 1
        changed = False
        for row in assignments:
            if row.get("role") != ROLE_M0:
                continue
            provenance = row.setdefault("provenance", {})
            gate = provenance.get("partner_gate") or {}
            # A row an earlier round swapped, whose set-aside reading the
            # ledger now bears out: swap back.
            if gate.get("displaced") and partnered(gate["displaced"]):
                back = next(
                    (
                        alt
                        for alt in row.get("alternatives") or []
                        if alt.get("partner_gate") == PARTNER_GATE_UNMET
                        and alt.get("ionization_mechanism_id") in gated_ids
                        and same_neutral(alt.get("assigned_formula"), gate["displaced"])
                    ),
                    None,
                )
                if back is not None:
                    chosen = dict(back)
                    chosen.pop("partner_gate", None)
                    own = notation_by_id.get(chosen["ionization_mechanism_id"])
                    swap(
                        row,
                        chosen,
                        provenance,
                        {"channel": own, "partner": True, "returned": True},
                    )
                    row["alternatives"] = [
                        alt for alt in row["alternatives"] if alt is not back
                    ]
                    changed = True
                    continue
            if row.get("ionization_mechanism_id") not in gated_ids:
                continue
            own = notation_by_id.get(row["ionization_mechanism_id"])
            if stands(row):
                record = {key: value for key, value in gate.items() if key != "kept"}
                record.setdefault("channel", own)
                record["partner"] = True
                if record != gate:
                    provenance["partner_gate"] = record
                    changed = True
                continue
            family = [alt for alt in row.get("alternatives") or [] if molecule(alt)]
            chosen = next(
                (
                    alt
                    for alt in family
                    if alt["ionization_mechanism_id"] not in minor_ids
                ),
                None,
            ) or max(
                (
                    alt
                    for alt in family
                    if alt["ionization_mechanism_id"] in minor_ids
                    and partnered(alt["assigned_formula"])
                ),
                key=lambda alt: strongest(alt["assigned_formula"]),
                default=None,
            )
            if chosen is None or row.get("source") == SOURCE_DATABASE:
                record = {
                    "channel": own,
                    "partner": False,
                    "kept": PARTNER_GATE_KEPT if chosen is None else PARTNER_GATE_LIST,
                }
                if record != gate:
                    provenance["partner_gate"] = record
                    changed = True
                continue
            swap(
                row,
                chosen,
                provenance,
                {
                    "channel": own,
                    "partner": False,
                    "displaced": row["assigned_formula"],
                    "through": notation_by_id.get(chosen["ionization_mechanism_id"]),
                },
            )
            changed = True
        if not changed:
            break
    else:
        summary["settled"] = False

    # The contest, once, against the partners the walk settled on.
    for row in assignments:
        if row.get("role") != ROLE_M0 or not stands(row):
            continue
        provenance = row.setdefault("provenance", {})
        stronger = max(
            (alt for alt in row.get("alternatives") or [] if contends(row, alt)),
            key=lambda alt: strongest(alt["assigned_formula"]),
            default=None,
        )
        if stronger is not None and strongest(stronger["assigned_formula"]) > strongest(
            row["assigned_formula"]
        ):
            if row.get("source") == SOURCE_DATABASE:
                # The list's reading stays, and the stronger one is weighed
                # below and by the cross-channel pass as the rival it is.
                provenance["partner_gate"] = {
                    **(provenance.get("partner_gate") or {}),
                    "kept": PARTNER_GATE_LIST,
                }
            else:
                swap(
                    row,
                    stronger,
                    provenance,
                    {
                        "channel": notation_by_id.get(
                            stronger["ionization_mechanism_id"]
                        ),
                        "partner": True,
                        "took_from": row["assigned_formula"],
                    },
                )
                summary["contest_swapped"] += 1
        contest = []
        for alternative in row.get("alternatives") or []:
            if not contends(row, alternative):
                continue
            entry = weigh(row, alternative)
            contest.append(entry)
            if entry["decisive"]:
                alternative["partner_gate"] = PARTNER_GATE_OUTWEIGHED
                summary["outweighed"] += 1
            else:
                alternative.pop("partner_gate", None)
                summary["within_margin"] += 1
        if contest:
            provenance["partner_gate"] = {
                **(provenance.get("partner_gate") or {}),
                "contest": contest,
            }
            summary["contested"] += 1
    summary["held"] = len(held)

    # Same-ion readings through a gated channel that nothing bore out are set
    # aside on every row, a reference list's included - read last, against
    # the partners the walk settled on - and the ledger is tallied as it is.
    for row in assignments:
        if row.get("role") != ROLE_M0:
            continue
        gate = (row.get("provenance") or {}).get("partner_gate") or {}
        if row.get("ionization_mechanism_id") in gated_ids:
            if gate.get("partner") is True:
                summary["partnered"] += 1
                summary["swapped_back"] += bool(gate.get("returned"))
            elif gate.get("kept"):
                summary["kept"] += 1
            summary["list_kept"] += gate.get("kept") == PARTNER_GATE_LIST
        if gate.get("displaced") and gate.get("partner") is False:
            summary["swapped"] += 1
        for alternative in row.get("alternatives") or []:
            if alternative.get("same_ion") and (
                alternative.get("ionization_mechanism_id") in gated_ids
            ):
                if not partnered(alternative.get("assigned_formula")):
                    alternative.setdefault("partner_gate", PARTNER_GATE_UNMET)
                elif alternative.get("partner_gate") == PARTNER_GATE_UNMET:
                    # The settled ledger bears it out, whatever a swap earlier
                    # in the walk marked it: a reading of the ion again.
                    del alternative["partner_gate"]
            if alternative.get("partner_gate") == PARTNER_GATE_UNMET:
                summary["set_aside"] += 1
    return summary
