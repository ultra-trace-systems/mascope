"""What a sample's other channels say about a reading, and the nitrogen a
reagent adduct can hide.

A compound in a reagent-ionization spectrum rarely appears once. It appears
through whichever of the mode's channels it can take - protonated and
ammoniated, deprotonated and clustered with the reagent anion - and those are
independent observations of one neutral. Grouping a run's committed winners by
their neutral and counting the channels each was seen through is the mechanical
form of the adduct corroboration Stage A already does for curated compounds
(``mascope_tools.composition.corroboration``), which reaches only rows that
matched the library: 25 of 2,062 committed rows on the sparse Orbitrap set, 22
of 9,077 on the dense one, and none at all on the other six gate sets. This
reaches every committed row, because it needs nothing but the ledger.

Measured on the 43-sample gate, it is the strongest single separator the engine
has. Over the reference's own Assigned rows, the share this engine gets the
formula right on is 98.7% for a neutral seen in two or more channels against
54.7% for a lone one on the sparse Orbitrap set, 98.0 against 71.2 on the dense
one, 97.6 against 83.2 on the bromide set, 100 against 5.1 on one TOF set. It is
not brightness in disguise: inside every intensity decile of the sets big enough
to run one, the two populations stay as far apart as they are pooled.

Nothing here promotes a row on that evidence - the flag is recorded and step
2.4's mechanical tiers are where it is weighed. What this module does demote is
the one reading the flag's ABSENCE leaves resting on a prior, below.

The reagent-N rule
------------------

``+NH4+`` on a neutral M and ``+H+`` on the neutral M+NH3 are the same ion
formula. Not similar - the same, so the same exact mass, the same isotope
envelope, and the same fit score at every width. Nothing measured separates
them, and the engine does not pretend otherwise: the finder collapses the two
into one hypothesis before anything is ranked
(``heuristic_filter.elect_same_ion_families``, step 1.3) and elects a reading by
a stated policy - a closed-shell neutral first, then the mechanism carrying the
most mass - so the ammoniated reading of M beats the protonated reading of
M+NH3, and the nitrate cluster beats the deprotonated nitrate ester. The
readings it displaces stay on the row as ``same_ion`` alternatives, carrying the
winner's own fit and mass error, because they are the same measurement split
differently rather than weaker hypotheses.

That policy is a prior, and a defensible one. What it is not is an observation:
on a run where nothing else saw the neutral, the analyte's nitrogen count is the
prior's answer and no part of the spectrum's. So a winner through a
nitrogen-donating channel, whose own family holds a reading through a channel
that donates none, is capped at ``candidate`` with the reason
:data:`REASON_AMBIGUOUS_NITROGEN` - keeping its formula - unless a second
channel fixed the count.

Whether the prior deserves trusting is a question about the reagent, and the
gate answers it differently for different ones. On the bromide set, where
``+Br-`` on M is likewise ``-H+`` on M+HBr, 277 lone bromide readings sit at
assigned tier and the reference confirms 246 of them - 89%. On the uronium set
the same population is 409 rows and the reference confirms 79 - 19%. The
bromide prior is borne out and the nitrogen prior is not, which is why this rule
is scoped to nitrogen rather than to same-ion families in general.

What CAN fix the count is another channel. If the same neutral is also committed
through a channel that donates no nitrogen, its composition is observed rather
than assumed. If it is committed through two DIFFERENT nitrogen-donating
reagents, the alternative reading would need a different analyte for each, the
two differing by exactly the difference of the two reagents - so the pair fixes
the count as well.

A labelled reagent donates no nitrogen for this purpose, and that is the whole
reason to run one: the 15N of a ``+[15N]O3-`` reagent is 0.997 Da from an
analyte's own nitrogen, so the two readings are two ions at two masses and the
spectrum chooses between them. The finder never proposes the labelled neutral,
so such a row has no same-ion family at all - but the channel is excluded on its
own terms here rather than left to that.
"""

from __future__ import annotations

from mascope_backend.api.new.peak_assignments.engine import (
    ROLE_ISO_CHILD,
    ROLE_M0,
    SOURCE_DATABASE,
)
from mascope_backend.api.new.peak_assignments.mass_gate import is_committed
from mascope_backend.api.new.peak_assignments.tiers import TIER_CANDIDATE, TIER_RANK
from mascope_tools.composition.custom_elements import CUSTOM_ELEMENTS
from mascope_tools.composition.heuristic_filter import element_counts
from mascope_tools.composition.utils import parse_ionization


#: How many distinct channels a neutral needs before the run calls it
#: corroborated. Two, because the second one is the whole of the evidence: one
#: channel is the observation being judged and any second channel is an
#: independent measurement of the same neutral through different chemistry.
CHANNELS_FOR_CORROBORATION = 2

#: The reason a row capped for an unfixable nitrogen count carries.
REASON_AMBIGUOUS_NITROGEN = "ambiguous_nitrogen"

#: The flag the finder puts on a same-ion alternative: not a weaker hypothesis
#: but this row's own measurement, split differently between analyte and
#: mechanism (``engine.untargeted_matches_to_peak_assignments``).
SAME_ION = "same_ion"


def donates_nitrogen(notation: str | None) -> bool:
    """Whether this channel's moiety carries nitrogen an analyte could carry.

    The question the reagent-N rule turns on, asked of the mechanism rather than
    of a table of known reagents, so a deployment that adds a channel gets the
    rule for free.

    A LABELLED moiety answers False. ``+[15N]O3-`` carries a nitrogen no analyte
    has - 0.997 Da from the ordinary one - so the deprotonated nitrate ester is
    a different ion at a different mass, and the spectrum, not a prior, chooses
    between them. That is the whole point of running a labelled reagent. The
    check is explicit here rather than left to the parser: ``parse_ionization``
    re-spells the bracketed form production sends into the caret form, which
    ``element_counts`` then cannot read, so such a row would fall out of the rule
    anyway - but for a reason that reads like an accident and would go silently
    wrong the day either of those two behaviours changed.

    :param notation: A mechanism's Mascope notation (``"+NH4+"``, ``"-H+"``).
    :return: Whether a reported analyte nitrogen could be this channel's instead.
    """
    if not notation:
        return False
    try:
        moiety = parse_ionization(notation).formula
    except Exception:  # noqa: BLE001 - a mechanism nobody can parse gates nothing
        return False
    if not moiety or any(symbol in moiety for symbol in CUSTOM_ELEMENTS):
        return False
    counts = element_counts(moiety)
    return bool(counts) and counts.get("N", 0) > 0


def nitrogen_donating_channels(notations: list[str]) -> frozenset[str]:
    """Which of this sample's channels could be carrying a reported nitrogen."""
    return frozenset(notation for notation in notations if donates_nitrogen(notation))


def same_ion_readings(row: dict, notation_by_id: dict[str, str]) -> list[dict]:
    """The readings of this row's ion that the finder's policy displaced.

    Read off the row rather than re-derived. The finder already decided which
    readings of one ion exist - it enumerated them, elected one and kept the
    rest - so asking the row is asking what this run actually proposed, where
    rebuilding a candidate from the element grid would ask what it might have.

    :param row: A committed assignment row.
    :param notation_by_id: The run's mechanisms, by the id the rows carry.
    :return: The displaced readings, each with its channel notation resolved.
    """
    readings = []
    for alternative in row.get("alternatives") or []:
        if not alternative.get(SAME_ION):
            continue
        notation = notation_by_id.get(str(alternative.get("ionization_mechanism_id")))
        if notation:
            readings.append({**alternative, "channel": notation})
    return readings


def nitrogen_ambiguity(
    row: dict, notation_by_id: dict[str, str], donors: frozenset[str]
) -> dict | None:
    """The reading that would put this row's nitrogen on the analyte instead.

    The row is ambiguous exactly when the finder proposed both splits: this one,
    through a channel that donates nitrogen, and another of the same ion through
    a channel that does not. Then the count on the reported neutral is the
    election policy's answer rather than the spectrum's.

    :param row: A committed monoisotopic row.
    :param notation_by_id: The run's mechanisms, by the id the rows carry.
    :param donors: The channels that donate nitrogen.
    :return: The displaced reading, or None where the count is not in question.
    """
    channel = notation_by_id.get(str(row.get("ionization_mechanism_id")))
    if channel not in donors:
        return None
    for reading in same_ion_readings(row, notation_by_id):
        if reading["channel"] not in donors:
            return reading
    return None


def neutral_key(formula: str | None) -> str:
    """A neutral's identity, independent of how its formula was written."""
    counts = element_counts(str(formula or ""))
    if not counts:
        return str(formula or "")
    return " ".join(f"{element}{counts[element]}" for element in sorted(counts))


def channels_by_neutral(
    assignments: list[dict], notation_by_id: dict[str, str]
) -> dict[str, frozenset[str]]:
    """Which channels each committed neutral was seen through in this sample.

    Monoisotopic rows only. A satellite is its parent's ion measured on a second
    line of the same envelope, not a second channel, so counting it would let
    one observation corroborate itself.

    Keyed on the neutral's element counts rather than on its written formula: a
    curated row spells one explicit ("C1H4N2O1") where an untargeted row does
    not ("CH4N2O"), so the same neutral seen through Stage A and Stage B would
    otherwise fail to group. No row on the gate changes today; the key is the
    normalized one so that none can.

    :param assignments: Every row built for this sample.
    :param notation_by_id: The run's mechanisms, by the id the rows carry.
    :return: The channel set of each committed neutral, by normalized formula.
    """
    seen: dict[str, set[str]] = {}
    for row in assignments:
        if not is_committed(row) or row.get("role") != ROLE_M0:
            continue
        notation = notation_by_id.get(str(row.get("ionization_mechanism_id")))
        if not notation:
            continue
        seen.setdefault(neutral_key(row.get("assigned_formula")), set()).add(notation)
    return {formula: frozenset(channels) for formula, channels in seen.items()}


def fixes_nitrogen(channels: frozenset[str], donors: frozenset[str]) -> bool:
    """Whether this neutral's own channels settle how many nitrogens it has.

    Either of the two ways named in the module docstring: a channel that donates
    no nitrogen observed the neutral directly, or two different nitrogen donors
    did and the alternative reading cannot hold for both.

    :param channels: The channels the neutral was committed through.
    :param donors: This run's nitrogen-donating channels.
    :return: Whether the count is fixed by observation.
    """
    seen_donors = channels & donors
    return (
        bool(channels - seen_donors) or len(seen_donors) >= CHANNELS_FOR_CORROBORATION
    )


def apply_cross_channel(
    assignments: list[dict],
    *,
    notation_by_id: dict[str, str],
) -> dict:
    """Record each commit's channels and cap the nitrogen counts nothing observed.

    Modifies the rows in place, after both stages have built them: which
    channels a neutral was seen through is a property of the whole ledger.

    A curated row is exempt. Its formula came from a library that named the
    compound, so the nitrogen sits where the curation put it rather than where
    the election policy did.

    :param assignments: Every row built for this sample, modified in place.
    :param notation_by_id: The run's mechanisms, by the id the rows carry.
    :return: A JSON-serializable summary for the run's config.
    """
    channels_searched = sorted(set(notation_by_id.values()))
    donors = nitrogen_donating_channels(channels_searched)
    channels = channels_by_neutral(assignments, notation_by_id)
    summary = {
        "channels": channels_searched,
        "reagent_channels": sorted(donors),
        "min_channels": CHANNELS_FOR_CORROBORATION,
        "committed_m0": 0,
        "neutrals": len(channels),
        "corroborated": 0,
        "ambiguous_nitrogen": 0,
        "capped": 0,
        "capped_satellites": 0,
        # Whether the REAGENT-N RULE had anything to gate. The corroboration half
        # above runs on every sample and is recorded whatever this says, so the
        # two are named apart: a bromide run records hundreds of corroborated
        # readings with no nitrogen rule to apply.
        "reagent_rule_applied": bool(donors),
    }

    capped_owners: set[str] = set()
    for row in assignments:
        if not is_committed(row) or row.get("role") != ROLE_M0:
            continue
        summary["committed_m0"] += 1
        seen = channels.get(neutral_key(row.get("assigned_formula")), frozenset())
        corroborated = len(seen) >= CHANNELS_FOR_CORROBORATION
        summary["corroborated"] += corroborated
        record: dict = {
            "channels": sorted(seen),
            "corroborated": corroborated,
            # The strongest tier any OTHER channel's reading of this neutral
            # holds. A partner's own confidence is most of what the flag is
            # worth - measured on the gate, an assigned partner and a candidate
            # one corroborate about equally on an Orbitrap while a
            # below-assignability one is weaker, and on the TOF sets most
            # partners sit below - so 2.4 can weigh it rather than count rows.
            "partner_tier": _partner_tier(row, assignments, notation_by_id),
        }
        if row.get("source") != SOURCE_DATABASE and not fixes_nitrogen(seen, donors):
            displaced = nitrogen_ambiguity(row, notation_by_id, donors)
            if displaced is not None:
                record["ambiguous_nitrogen"] = {
                    "alternative": displaced.get("assigned_formula"),
                    "via": displaced["channel"],
                }
                summary["ambiguous_nitrogen"] += 1
                if _cap(row, record):
                    summary["capped"] += 1
                    capped_owners.add(str(row["peak_assignment_id"]))
        row.setdefault("provenance", {})["cross_channel"] = record

    # A satellite is its parent's ion on a second line of one envelope, so it
    # carries the parent's neutral and the parent's doubt. Counted apart because
    # a rule's reach over analytes and its reach over their satellites are
    # different numbers and reporting the sum as one hides which it moved.
    for row in assignments:
        if not is_committed(row) or row.get("role") != ROLE_ISO_CHILD:
            continue
        owner_id = str(row.get("owner_peak_assignment_id"))
        if owner_id not in capped_owners:
            continue
        record = {"inherited_from": owner_id}
        if _cap(row, record):
            summary["capped_satellites"] += 1
        row.setdefault("provenance", {})["cross_channel"] = record
    return summary


def _partner_tier(
    row: dict, assignments: list[dict], notation_by_id: dict[str, str]
) -> str | None:
    """The best tier another channel's reading of this row's neutral holds."""
    key = neutral_key(row.get("assigned_formula"))
    channel = notation_by_id.get(str(row.get("ionization_mechanism_id")))
    best: str | None = None
    for other in assignments:
        if other is row or not is_committed(other) or other.get("role") != ROLE_M0:
            continue
        if neutral_key(other.get("assigned_formula")) != key:
            continue
        if notation_by_id.get(str(other.get("ionization_mechanism_id"))) == channel:
            continue
        tier = other.get("tier")
        if tier in TIER_RANK and (best is None or TIER_RANK[tier] > TIER_RANK[best]):
            best = tier
    return best


def _cap(row: dict, record: dict) -> bool:
    """Cap a row at ``candidate``, downwards only, and say so on the row."""
    if TIER_RANK[row["tier"]] <= TIER_RANK[TIER_CANDIDATE]:
        return False
    row["tier"] = TIER_CANDIDATE
    record["capped"] = TIER_CANDIDATE
    record["reason"] = REASON_AMBIGUOUS_NITROGEN
    return True
