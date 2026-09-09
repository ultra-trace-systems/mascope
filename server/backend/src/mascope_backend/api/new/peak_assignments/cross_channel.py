"""What a sample's other channels say about a reading, and the nitrogen a
reagent adduct can hide.

A compound in a reagent-ionization spectrum rarely appears once. It appears
through whichever of the mode's channels it can take - protonated and
ammoniated, deprotonated and clustered with the reagent anion - and those are
independent observations of one neutral. Grouping a run's committed winners by
their neutral and counting the channels each was seen through is the mechanical
form of the adduct corroboration Stage A already does for curated compounds
(``mascope_tools.composition.corroboration``), which reaches only rows that
matched the library: 25 of 2,062 committed rows on the sparse Orbitrap set and
none at all on three of the eight gate sets. This reaches every committed row,
because it needs nothing but the ledger.

Measured on the 43-sample gate, it is the strongest single separator the engine
has. Over the rows the reference also calls monoisotopic, the share it confirms
the formula of is 75.1% for a neutral seen in two or more channels against 11.8%
for a lone one on the sparse Orbitrap set, 89.9 against 44.7 on the nitrate set,
68.6 against 35.2 on the bromide set, 49.6 against 21.1 on the dense one. It is
not brightness in disguise: inside every intensity decile of those sets the two
populations stay as far apart as they are pooled.

Nothing here promotes a row on that evidence - the flag is recorded and step
2.4's mechanical tiers are where it is weighed. What this module does demote is
the one reading the flag's ABSENCE makes untenable, below.

The reagent-N rule
------------------

``+NH4+`` on a neutral M and ``+H+`` on the neutral M+NH3 are the same ion
formula. Not similar - the same, so the same exact mass, the same isotope
envelope, and the same fit score at every width. The Seven Golden Rules score
both 1.0 in almost every case, so ``arbitrate_candidates`` calls them a tie, and
the row sort that picks the winner falls through evidence and mass error to its
last key, which compares the two NEUTRAL FORMULAS AS STRINGS. On the gate's
assigned-tier rows that string comparison predicts the committed reading 92 to
100% of the time. Whether this engine reports a nitrogen on the analyte or on
the reagent is therefore not a measurement; it is where "C4H4" happens to sort
against "C4H7N".

The same holds for every reagent whose moiety carries an UNLABELLED nitrogen -
``+NO3-`` on M is the same ion as ``-H+`` on M+HNO3 - so the substitution is
derived from the mechanisms themselves rather than named here. A labelled
reagent is the exception, and the reason anyone labels one: the 15N of a
``+^NO3-`` reagent is 0.997 Da from an analyte's own nitrogen, so the two
readings are two ions at two masses and the spectrum decides between them.

What CAN fix the count is another channel. If the same neutral is also committed
through a channel that donates no nitrogen, its composition is observed rather
than inferred. If it is committed through two DIFFERENT nitrogen-donating
reagents, the alternative reading would need a different analyte for each, the
two differing by exactly the difference of the two reagents - so the pair fixes
the count as well. Without either, the row keeps its formula and loses its tier:
capped at ``candidate`` with the reason :data:`REASON_AMBIGUOUS_NITROGEN`.
"""

from __future__ import annotations

from dataclasses import dataclass

from mascope_backend.api.new.peak_assignments.engine import (
    ROLE_ISO_CHILD,
    ROLE_M0,
    SOURCE_DATABASE,
)
from mascope_backend.api.new.peak_assignments.mass_gate import is_committed
from mascope_backend.api.new.peak_assignments.tiers import TIER_CANDIDATE, TIER_RANK
from mascope_tools.composition.custom_elements import CUSTOM_ELEMENTS
from mascope_tools.composition.heuristic_filter import element_counts
from mascope_tools.composition.utils import (
    parse_atom_count_ranges,
    parse_ionization,
    to_hill_order,
)


#: How many distinct channels a neutral needs before the run calls it
#: corroborated. Two, because the second one is the whole of the evidence: one
#: channel is the observation being judged and any second channel is an
#: independent measurement of the same neutral through different chemistry.
CHANNELS_FOR_CORROBORATION = 2

#: The reason a row capped for an unfixable nitrogen count carries.
REASON_AMBIGUOUS_NITROGEN = "ambiguous_nitrogen"

#: The moiety a plain channel moves, which is what makes it plain: protonation
#: and deprotonation are the only mechanisms that add nothing to the ion but a
#: charge, so they are the readings every other channel's ion can be restated
#: as.
PLAIN_MOIETY = "H"


@dataclass(frozen=True)
class ReagentSubstitution:
    """One channel's reading restated as the plain channel's.

    :param donor: The mechanism notation the winner came through.
    :param plain: The protonation or deprotonation channel it restates as.
    :param delta: What the plain reading's neutral holds that this one does not.
    :param label: That difference as a formula, for the row's provenance.
    """

    donor: str
    plain: str
    delta: dict[str, int]
    label: str

    @property
    def donates_nitrogen(self) -> bool:
        """Whether the reagent could be carrying the analyte's reported N."""
        return self.delta.get("N", 0) > 0


def _moiety_counts(notation: str) -> tuple[dict[str, int], int, int] | None:
    """A mechanism's moiety, its sign on the neutral, and the ion's charge.

    A moiety carrying a LABELLED atom answers None, and that is the whole point
    of labelling the reagent: the 15N in a ``+^NO3-`` reagent is 0.997 Da from
    an analyte's own nitrogen, so the deprotonated nitrate ester is a different
    ion at a different mass rather than the same one read differently. Two of
    the gate's sets run that reagent, and treating its label as ordinary
    nitrogen would have capped 297 of their readings - 93 of which the reference
    confirms - for an ambiguity the labelling exists to remove.
    """
    try:
        mechanism = parse_ionization(notation)
    except Exception:  # noqa: BLE001 - a mechanism nobody can parse gates nothing
        return None
    if any(symbol in (mechanism.formula or "") for symbol in CUSTOM_ELEMENTS):
        return None
    counts = element_counts(mechanism.formula) if mechanism.formula else {}
    if counts is None:
        return None
    return counts, (1 if mechanism.addition else -1), int(mechanism.charge)


def substitution_for(donor: str, plain: str) -> ReagentSubstitution | None:
    """The neutral a plain-channel reading of the donor's ion would need.

    An ion is its neutral plus or minus the mechanism's moiety, so two
    mechanisms explain one peak with neutrals that differ by the difference of
    their moieties: ``M + NH4`` and ``M' + H`` are the same ion exactly when
    ``M' = M + NH3``. The arithmetic is done on the mechanisms rather than on a
    table of known reagents, so a deployment that adds a channel gets the rule
    for free.

    :param donor: The mechanism the winner came through.
    :param plain: A protonation or deprotonation channel of the same polarity.
    :return: The substitution, or None where the two cannot explain one peak or
        the plain reading would need a LIGHTER neutral (which is a different
        claim - the reagent would have to be taking atoms off the analyte - and
        is not what this rule is about).
    """
    left, right = _moiety_counts(donor), _moiety_counts(plain)
    if left is None or right is None:
        return None
    donor_counts, donor_sign, donor_charge = left
    plain_counts, plain_sign, plain_charge = right
    if donor_charge != plain_charge:
        return None
    delta: dict[str, int] = {}
    for element in set(donor_counts) | set(plain_counts):
        difference = donor_sign * donor_counts.get(element, 0) - (
            plain_sign * plain_counts.get(element, 0)
        )
        if difference:
            delta[element] = difference
    if not delta or any(count < 0 for count in delta.values()):
        return None
    return ReagentSubstitution(
        donor=donor, plain=plain, delta=delta, label=to_hill_order(delta)
    )


def reagent_substitutions(notations: list[str]) -> dict[str, ReagentSubstitution]:
    """Every channel of this sample that could be carrying a reported nitrogen.

    :param notations: The mechanism notations the run searched.
    :return: The nitrogen-donating channels, keyed by notation. A channel absent
        from this map donates no nitrogen, which is what makes it able to fix
        another channel's count.
    """
    plains = [
        notation
        for notation in notations
        if (moiety := _moiety_counts(notation)) is not None
        and moiety[0] == {PLAIN_MOIETY: 1}
    ]
    substitutions: dict[str, ReagentSubstitution] = {}
    for notation in notations:
        for plain in plains:
            if notation == plain:
                continue
            substitution = substitution_for(notation, plain)
            if substitution is not None and substitution.donates_nitrogen:
                substitutions[notation] = substitution
                break
    return substitutions


def channels_by_neutral(
    assignments: list[dict], notation_by_id: dict[str, str]
) -> dict[str, frozenset[str]]:
    """Which channels each committed neutral was seen through in this sample.

    Monoisotopic rows only. A satellite is its parent's ion measured on a second
    line of the same envelope, not a second channel, so counting it would let
    one observation corroborate itself.

    :param assignments: Every row built for this sample.
    :param notation_by_id: The run's mechanisms, by the id the rows carry.
    :return: The channel set of each committed neutral.
    """
    seen: dict[str, set[str]] = {}
    for row in assignments:
        if not is_committed(row) or row.get("role") != ROLE_M0:
            continue
        notation = notation_by_id.get(str(row.get("ionization_mechanism_id")))
        if not notation:
            continue
        seen.setdefault(str(row["assigned_formula"]), set()).add(notation)
    return {formula: frozenset(channels) for formula, channels in seen.items()}


def fixes_nitrogen(
    channels: frozenset[str], substitutions: dict[str, ReagentSubstitution]
) -> bool:
    """Whether this neutral's own channels settle how many nitrogens it has.

    Either of the two ways named in the module docstring: a channel that donates
    no nitrogen observed the neutral directly, or two different nitrogen donors
    did and the alternative reading cannot hold for both.

    :param channels: The channels the neutral was committed through.
    :param substitutions: This run's nitrogen-donating channels.
    :return: Whether the count is fixed by observation.
    """
    donors = {channel for channel in channels if channel in substitutions}
    return bool(channels - donors) or len(donors) >= CHANNELS_FOR_CORROBORATION


def alternative_neutral(
    formula: str, substitution: ReagentSubstitution
) -> dict[str, int] | None:
    """The neutral the plain-channel reading of this row would name."""
    counts = element_counts(formula)
    if counts is None:
        return None
    alternative = dict(counts)
    for element, difference in substitution.delta.items():
        alternative[element] = alternative.get(element, 0) + difference
    return alternative


def within_search_space(
    counts: dict[str, int], ranges: dict[str, tuple[int, int]]
) -> bool:
    """Whether the run's own untargeted grid could have proposed this neutral.

    A reading is only ambiguous against an alternative the run could have
    written down. Where the grid does not reach the alternative - the nitrogen
    it would need is past the profile's ceiling - the channel is the only
    reading available and the count it implies is the run's answer rather than a
    coin toss.

    :param counts: The alternative neutral's elements.
    :param ranges: The profile's element window.
    :return: Whether it sits inside that window.
    """
    if not ranges:
        return False
    for element, count in counts.items():
        low, high = ranges.get(element, (0, 0))
        if not low <= count <= high:
            return False
    return all(counts.get(element, 0) >= low for element, (low, _) in ranges.items())


def search_ranges(element_ranges: str | None) -> dict[str, tuple[int, int]]:
    """The profile's element window, as counts by element."""
    if not element_ranges:
        return {}
    try:
        atoms = parse_atom_count_ranges(element_ranges)
    except Exception:  # noqa: BLE001 - an unreadable window gates nothing
        return {}
    return {atom.symbol: (atom.min_count, atom.max_count) for atom in atoms}


def apply_cross_channel(
    assignments: list[dict],
    *,
    notation_by_id: dict[str, str],
    element_ranges: str | None,
) -> dict:
    """Record each commit's channels and cap the readings nothing but a string
    decided.

    Modifies the rows in place, after both stages have built them: which
    channels a neutral was seen through is a property of the whole ledger.

    A curated row is exempt. Its formula came from a library that named the
    compound, so the nitrogen sits where the curation put it rather than where
    the sort key did.

    :param assignments: Every row built for this sample, modified in place.
    :param notation_by_id: The run's mechanisms, by the id the rows carry.
    :param element_ranges: The resolved profile's grid, which decides whether
        the alternative reading is one this run could have written.
    :return: A JSON-serializable summary for the run's config.
    """
    substitutions = reagent_substitutions(sorted(set(notation_by_id.values())))
    channels = channels_by_neutral(assignments, notation_by_id)
    ranges = search_ranges(element_ranges)
    summary = {
        "channels": sorted(set(notation_by_id.values())),
        "reagent_channels": {
            donor: substitution.label for donor, substitution in substitutions.items()
        },
        "min_channels": CHANNELS_FOR_CORROBORATION,
        "committed_m0": 0,
        "neutrals": len(channels),
        "corroborated": 0,
        "ambiguous_nitrogen": 0,
        "capped": 0,
        "capped_satellites": 0,
        "applied": bool(substitutions) and bool(ranges),
    }

    capped_owners: set[str] = set()
    for row in assignments:
        if not is_committed(row) or row.get("role") != ROLE_M0:
            continue
        summary["committed_m0"] += 1
        seen = channels.get(str(row["assigned_formula"]), frozenset())
        corroborated = len(seen) >= CHANNELS_FOR_CORROBORATION
        summary["corroborated"] += corroborated
        record: dict = {"channels": sorted(seen), "corroborated": corroborated}
        substitution = substitutions.get(
            notation_by_id.get(str(row.get("ionization_mechanism_id")), "")
        )
        if (
            substitution is not None
            and row.get("source") != SOURCE_DATABASE
            and not fixes_nitrogen(seen, substitutions)
        ):
            alternative = alternative_neutral(
                str(row["assigned_formula"]), substitution
            )
            if alternative is not None and within_search_space(alternative, ranges):
                record["ambiguous_nitrogen"] = {
                    "alternative": to_hill_order(alternative),
                    "via": substitution.plain,
                    "reagent": substitution.label,
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


def _cap(row: dict, record: dict) -> bool:
    """Cap a row at ``candidate``, downwards only, and say so on the row."""
    if TIER_RANK[row["tier"]] <= TIER_RANK[TIER_CANDIDATE]:
        return False
    row["tier"] = TIER_CANDIDATE
    record["capped"] = TIER_CANDIDATE
    record["reason"] = REASON_AMBIGUOUS_NITROGEN
    return True
