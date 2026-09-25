"""What a sample's other channels say about a reading, and the readings of one
ion that nothing measured can tell apart.

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

The same-ion rule
-----------------

``[M+NH4]+`` on a neutral M and ``[M+H]+`` on the neutral M+NH3 are the same ion
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
on a run where nothing else saw the neutral, which molecule the ion came from is
the prior's answer and no part of the spectrum's. So a committed reading whose
ion also reads as another molecule is capped at ``candidate`` - keeping its
formula - unless something settled which reading it is. The reason says what
the two readings disagree about. :data:`REASON_AMBIGUOUS_NITROGEN` is the
analyte's nitrogen count: an ammonium, urea or nitrate adduct against a plain
channel, or two such adducts against each other, since urea through its proton
is ammonium plus isocyanic acid. :data:`REASON_AMBIGUOUS_ADDUCT` is the rest: a
bromide cluster against the deprotonated molecule that holds the hydrogen
bromide, a water cluster against the hydrate.

Four things settle it, and a row that one of them settles records which
(:data:`SAME_ION_SETTLED`):

- **A second channel.** If the same neutral is also committed through any other
  channel, the alternative reading would need a different analyte for each ion,
  the two differing by exactly the difference of the two channels' moieties -
  one neutral explains both, two coincidences are needed to avoid it. Unless the
  sample shows the other reading's molecule too, committed through one of the
  mode's own channels: then each reading has its own second observation, the
  argument cuts both ways, and the rival stays the doubt it is.
- **The stronger partner.** Where the sample shows both molecules, the two are
  weighed the way the partner gate weighs two opportunistic readings
  (``engine.apply_partner_gates``, ``engine.outweighs``): where the row's
  molecule is committed through one of the mode's own channels on a peak
  ``engine.PARTNER_MARGIN`` times as bright as the other's or more, the ion is
  settled. Closer than that, the other reading is a rival the sample shows, as
  above.
- **The target library.** The workspace named that compound for the modes its
  collection is attached to, so which reading the ion is was its curation's
  decision.
- **A radical.** A neutral that breaks the nitrogen rule names a radical rather
  than a molecule, and the tiering pass refuses to hold one at the top tier on
  the measurement (``tiering.odd_electron_reason``: the reference engine confirms
  none of 1,794). A reading whose ion reads otherwise only as a radical has no
  rival. The carbonate radical anion's families are the common case - C5H8O5
  through ``[M-H]-`` is the radical C4H7O2 through ``[M+CO3]-`` - and exactly one
  reading of every such family is a radical.

The rule reaches every reagent, not only the ones that donate nitrogen. The
bromide prior looks borne out on the gate - the reference engine confirms 246 of
277 lone bromide readings against 79 of 409 lone nitrogen ones - but that engine
prefers the cluster reading by a policy of its own, so its agreement says the
two priors agree, not that the spectrum chose. A reading the spectrum cannot
tell from another molecule is not assigned on a prior (the plan owner's rule,
step 2.8).

A reference mirror's row
------------------------

A Stage A row is matched rather than elected. A reference mirror's row is a
list's formula matched against every sample, and the same arithmetic reaches
it - dimethylformamide through ``[M+H]+`` is the same ion as acrolein through
``[M+NH4]+``. Such a row is given the readings the untargeted search would have held
in its ion's family (``engine.record_mirror_same_ion_readings``) and asked what
an election is asked.

A labelled reagent donates no nitrogen, and that is the whole reason to run
one: the 15N of a ``[M+[15N]O3]-`` reagent is 0.997 Da from an analyte's own
nitrogen, so the two readings are two ions at two masses and the spectrum
chooses between them. The finder never proposes the labelled neutral, and a
reading whose neutral carries a labelled atom is not weighed if one ever
reaches a row (:func:`carries_label`): the label is the reagent's, and an
analyte does not carry it.
"""

from __future__ import annotations

import math
import re

from mascope_backend.api.new.peak_assignments.engine import (
    PARTNER_GATE_UNMET,
    PARTNER_MARGIN,
    ROLE_ISO_CHILD,
    ROLE_M0,
    is_reference_mirror_row,
    is_target_library_row,
    outweighs,
    partner_ratio,
)
from mascope_backend.api.new.peak_assignments.mass_gate import is_committed
from mascope_backend.api.new.peak_assignments.tiers import (
    TIER_ASSIGNED,
    TIER_CANDIDATE,
    TIER_RANK,
)
from mascope_tools.composition.custom_elements import CUSTOM_ELEMENTS
from mascope_tools.composition.heuristic_filter import (
    element_counts,
    neutral_is_closed_shell,
)
from mascope_tools.composition.utils import parse_composition, parse_ionization


#: How many distinct channels a neutral needs before the run calls it
#: corroborated. Two, because the second one is the whole of the evidence: one
#: channel is the observation being judged and any second channel is an
#: independent measurement of the same neutral through different chemistry.
CHANNELS_FOR_CORROBORATION = 2

#: The reason a row carries whose ion also reads as a molecule with another
#: nitrogen count, when nothing settled which.
REASON_AMBIGUOUS_NITROGEN = "ambiguous_nitrogen"

#: The reason a row carries whose ion also reads as another molecule with the
#: same nitrogen count, when nothing settled which.
REASON_AMBIGUOUS_ADDUCT = "ambiguous_adduct"

#: The two reasons, in the order a record is read for them.
AMBIGUITY_REASONS = (REASON_AMBIGUOUS_NITROGEN, REASON_AMBIGUOUS_ADDUCT)

#: The flag the finder puts on a same-ion alternative: not a weaker hypothesis
#: but this row's own measurement, split differently between analyte and
#: mechanism (``engine.untargeted_matches_to_peak_assignments``).
SAME_ION = "same_ion"

#: An explicitly labelled atom in a formula: the bracketed isotope form the
#: finder writes a labelled reagent's atom in (``[15N]``).
_LABELLED_ATOM = re.compile(r"\[\d+[A-Z][a-z]?\]")

#: The record a row carries whose ion reads another way, where something
#: settled which reading it is - and what did.
SAME_ION_SETTLED = "same_ion_settled"
SETTLED_BY_SECOND_CHANNEL = "second_channel"
SETTLED_BY_PARTNER = "partner"
SETTLED_BY_TARGET_LIBRARY = "target_library"
SETTLED_BY_RADICAL = "radical"


def donates_nitrogen(notation: str | None) -> bool:
    """Whether this channel's moiety carries nitrogen an analyte could carry.

    Asked of the mechanism rather than of a table of known reagents, so a
    deployment that adds a channel is described for free. The run records the
    answer (``reagent_channels``); the same-ion rule itself reads the nitrogen
    of the two readings' neutrals, which says the same thing for a pair of
    channels and also covers two donors against each other.

    A LABELLED moiety answers False. ``[M+[15N]O3]-`` carries a nitrogen no analyte
    has - 0.997 Da from the ordinary one - so the deprotonated nitrate ester is
    a different ion at a different mass, and the spectrum, not a prior, chooses
    between them. That is the whole point of running a labelled reagent. The
    check is explicit here rather than left to the parser: ``parse_ionization``
    re-spells the bracketed form production sends into the caret form, which
    ``element_counts`` then cannot read, so such a row would fall out of the rule
    anyway - but for a reason that reads like an accident and would go silently
    wrong the day either of those two behaviours changed.

    :param notation: A mechanism, in either notation (``"[M+NH4]+"``,
        ``"[M-H]-"``).
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
    """The readings of this row's ion that the row's own reading displaced.

    Read off the row rather than re-derived. On an election the finder already
    decided which readings of one ion exist - it enumerated them, elected one
    and kept the rest - so asking the row is asking what this run actually
    proposed, where rebuilding a candidate from the element grid would ask what
    it might have. A reference mirror's row carries the family the same search
    would have held, written onto it before this pass runs.

    :param row: A committed assignment row.
    :param notation_by_id: The run's mechanisms, by the id the rows carry.
    :return: The displaced readings, each with its channel notation resolved.
    """
    readings = []
    for alternative in row.get("alternatives") or []:
        if not alternative.get(SAME_ION):
            continue
        # An opportunistic reading the partner gate displaced: the sample was
        # asked to bear it out and did not, so it is no doubt about the reading
        # it did bear out (``engine.apply_partner_gates``). One its contest
        # outweighed the sample did bear out, and is weighed again here.
        if alternative.get("partner_gate") == PARTNER_GATE_UNMET:
            continue
        notation = notation_by_id.get(str(alternative.get("ionization_mechanism_id")))
        if notation:
            readings.append({**alternative, "channel": notation})
    return readings


def other_readings(row: dict, notation_by_id: dict[str, str]) -> list[dict]:
    """The readings of this row's ion that name another neutral.

    A reading of the row's own neutral is the row's reading again, whatever
    channel it names: one ion and one neutral leave one moiety between them. A
    family can hold one - a channel searched twice proposes every neutral
    through it twice, and the election keeps the twin - and it is no other
    reading of the ion.

    :param row: A committed monoisotopic row.
    :param notation_by_id: The run's mechanisms, by the id the rows carry.
    :return: The displaced readings that name another neutral.
    """
    own_neutral = neutral_key(row.get("assigned_formula"))
    return [
        reading
        for reading in same_ion_readings(row, notation_by_id)
        if neutral_key(reading.get("assigned_formula")) != own_neutral
    ]


def carries_label(formula: str | None) -> bool:
    """Whether a neutral carries an explicitly labelled atom.

    A labelled reagent's atom, written ``[15N]`` or ``^N``: a reading that puts
    one on the analyte names an analyte the source does not make.
    """
    text = str(formula or "")
    return bool(_LABELLED_ATOM.search(text)) or any(
        symbol in text for symbol in CUSTOM_ELEMENTS
    )


def is_molecule(formula: str | None) -> bool:
    """Whether a reading's neutral is a closed-shell molecule.

    A formula nothing can read is not one: the rule that asks this caps a row
    on the answer, and a rule that caps fails open.
    """
    text = str(formula or "")
    return element_counts(text) is not None and neutral_is_closed_shell(text)


def nitrogen_count(formula: str | None) -> int:
    """How many nitrogen atoms a neutral carries; 0 for one nothing can read."""
    return (element_counts(str(formula or "")) or {}).get("N", 0)


def ambiguity_of(row: dict, reading: dict) -> str:
    """What two readings of one ion disagree about.

    :param row: A committed monoisotopic row.
    :param reading: Another reading of its ion, a molecule.
    :return: :data:`REASON_AMBIGUOUS_NITROGEN` where the two put a different
        number of nitrogen atoms on the analyte, else
        :data:`REASON_AMBIGUOUS_ADDUCT`.
    """
    if nitrogen_count(row.get("assigned_formula")) != nitrogen_count(
        reading.get("assigned_formula")
    ):
        return REASON_AMBIGUOUS_NITROGEN
    return REASON_AMBIGUOUS_ADDUCT


def sharpest(row: dict, readings: list[dict]) -> dict | None:
    """The reading of one ion that doubts the row most sharply.

    One that puts a different number of nitrogen atoms on the analyte: the
    count is the sharper doubt, and the order the family was stored in does
    not decide which doubt the row records. Otherwise the first.

    :param row: A committed monoisotopic row.
    :param readings: Other readings of its ion, molecules.
    :return: The reading, or None where there are none.
    """
    return next(
        (
            reading
            for reading in readings
            if ambiguity_of(row, reading) == REASON_AMBIGUOUS_NITROGEN
        ),
        readings[0] if readings else None,
    )


def weighed(
    row: dict, reading: dict, partners: dict[str, dict[int, float]]
) -> dict | None:
    """How strongly the sample shows the row's molecule against a reading's.

    Each read as the partner gate reads a partner (``engine.apply_partner_gates``):
    the brightest row that commits the neutral through one of the mode's own
    channels (:func:`partner_heights`). The row itself is left out of its own
    molecule's: its peak is the one ion both readings explain, and it is no
    evidence for either.

    :param row: A committed monoisotopic row.
    :param reading: Another reading of its ion, a molecule.
    :param partners: :func:`partner_heights`.
    :return: None where the sample does not commit the reading's molecule
        through a mode channel. Otherwise the ratio of the two partners' peaks,
        the row's over the reading's (``engine.partner_ratio``, None where the
        row's molecule has no partner of its own), and whether it settles the
        reading (``engine.outweighs``).
    """
    theirs = partners.get(neutral_key(reading.get("assigned_formula")))
    if not theirs:
        return None
    other = max(theirs.values())
    own = max(
        (
            height
            for key, height in partners.get(
                neutral_key(row.get("assigned_formula")), {}
            ).items()
            if key != id(row)
        ),
        default=0.0,
    )
    return {"ratio": partner_ratio(own, other), "decisive": outweighs(own, other)}


def same_ion_question(
    row: dict,
    notation_by_id: dict[str, str],
    *,
    corroborated: bool,
    partners: dict[str, dict[int, float]] | None = None,
) -> tuple[str, dict] | None:
    """Whether this row's ion reads another way, and what that leaves it.

    Only readings this can weigh are asked: one whose formula nothing can
    parse, or that puts a labelled atom on the analyte (:func:`carries_label`),
    is neither a molecule nor a radical. A row whose own channel the run cannot
    name is not asked at all, as a formula nothing can read is not.

    Where the ion reads as more than one molecule, the rival is the one that
    doubts it most sharply (:func:`sharpest`).

    A reading whose molecule the sample also commits through one of the mode's
    own channels is weighed against the row's own (:func:`weighed`): where the
    sample shows the row's molecule ``engine.PARTNER_MARGIN`` times as brightly
    or more, the stronger partner settles it. Otherwise it is a doubt that a
    second channel does not settle, since the reading has a second observation
    of its own and the row's is no longer the whole of the evidence; so a
    corroborated row records the rival the sample shows, marked ``shown``.

    :param row: A committed monoisotopic row.
    :param notation_by_id: The run's mechanisms, by the id the rows carry.
    :param corroborated: Whether a second channel committed the row's neutral.
    :param partners: :func:`partner_heights`; nothing is shown where it is not
        given.
    :return: None where the ion has no other reading this can weigh. Otherwise
        the reason and its record: one of :data:`AMBIGUITY_REASONS` with the
        rival molecule where nothing settled it - on a corroborated row with
        ``shown`` and the partners' ``ratio`` - or :data:`SAME_ION_SETTLED` with
        the reading and what settled it (``by``, and the ``ratio`` where it was
        the stronger partner).
    """
    if notation_by_id.get(str(row.get("ionization_mechanism_id"))) is None:
        return None
    others = [
        reading
        for reading in other_readings(row, notation_by_id)
        if element_counts(str(reading.get("assigned_formula") or "")) is not None
        and not carries_label(reading.get("assigned_formula"))
    ]
    if not others:
        return None
    molecules = [
        reading for reading in others if is_molecule(reading.get("assigned_formula"))
    ]
    if not molecules:
        return SAME_ION_SETTLED, {**_named(others[0]), "by": SETTLED_BY_RADICAL}
    if is_target_library_row(row):
        return SAME_ION_SETTLED, {
            **_named(sharpest(row, molecules)),
            "by": SETTLED_BY_TARGET_LIBRARY,
        }
    weight = {
        id(reading): weighed(row, reading, partners or {}) for reading in molecules
    }

    def settles(reading: dict) -> bool:
        found = weight[id(reading)]
        return found is not None and found["decisive"]

    doubts = [
        reading
        for reading in molecules
        if not settles(reading)
        # A second channel settles every rival the sample does not show.
        and not (corroborated and weight[id(reading)] is None)
    ]
    if not doubts:
        outweighed = [reading for reading in molecules if settles(reading)]
        if outweighed:
            settled = sharpest(row, outweighed)
            return SAME_ION_SETTLED, {
                **_named(settled),
                "by": SETTLED_BY_PARTNER,
                "ratio": weight[id(settled)]["ratio"],
            }
        return SAME_ION_SETTLED, {
            **_named(sharpest(row, molecules)),
            "by": SETTLED_BY_SECOND_CHANNEL,
        }
    rival = sharpest(row, doubts)
    record = _named(rival)
    if corroborated:
        record["shown"] = True
        record["ratio"] = weight[id(rival)]["ratio"]
    return ambiguity_of(row, rival), record


def _named(reading: dict) -> dict:
    """The reading a record names: its neutral and its channel."""
    return {"alternative": reading.get("assigned_formula"), "via": reading["channel"]}


def neutral_key(formula: str | None) -> str:
    """A neutral's identity, independent of how its formula was written.

    A formula the element counter cannot read is read as the partner gate reads
    one (``engine.formula_identity``): a target library holds what a person
    typed, and ``C6H4(CH3)2`` is the xylene a search writes ``C8H10``.
    """
    text = str(formula or "")
    counts = element_counts(text)
    if counts is None:
        try:
            counts = {
                symbol: count
                for symbol, count in parse_composition(text).items()
                if count
            }
        except Exception:  # noqa: BLE001 - an unreadable formula keeps its own text
            counts = None
    if not counts:
        return text
    return " ".join(f"{element}{counts[element]}" for element in sorted(counts))


def channels_by_neutral(
    assignments: list[dict], notation_by_id: dict[str, str]
) -> dict[str, dict[str, str | None]]:
    """Which channels each committed neutral was seen through, and how well.

    Monoisotopic rows only. An isotopologue is its parent's ion measured on a
    second line of the same envelope, not a second channel, so counting it would
    let one observation corroborate itself.

    Keyed on the neutral's element counts rather than on its written formula: a
    curated row spells one explicit ("C1H4N2O1") where an untargeted row does
    not ("CH4N2O"), so the same neutral seen through Stage A and Stage B would
    otherwise fail to group. It changes three rows on the gate - urea, seen
    through the carbonate channel and through the curated rows on the bromide
    TOF set, whose corroborated count is 184 where the written key gave 181.

    The tier travels with the channel because this map is also the index the
    partner lookup reads. Built in one pass over the ledger, it answers "what
    else saw this neutral, and how confidently" with a dict read; asking the
    ledger itself per row made the pass quadratic and doubled the run time on
    the dense sets (one TOF set 236 -> 452 s over its six samples).

    :param assignments: Every row built for this sample.
    :param notation_by_id: The run's mechanisms, by the id the rows carry.
    :return: Per normalized neutral, the best tier committed on each channel
        (None where a row carried a tier this module does not rank).
    """
    seen: dict[str, dict[str, str | None]] = {}
    for row in assignments:
        if not is_committed(row) or row.get("role") != ROLE_M0:
            continue
        notation = notation_by_id.get(str(row.get("ionization_mechanism_id")))
        if not notation:
            continue
        by_channel = seen.setdefault(neutral_key(row.get("assigned_formula")), {})
        tier = row.get("tier")
        tier = tier if tier in TIER_RANK else None
        if notation not in by_channel or _outranks(tier, by_channel[notation]):
            by_channel[notation] = tier
    return seen


def _outranks(tier: str | None, other: str | None) -> bool:
    """Whether ``tier`` is the stronger of two tiers, unrankable counting last."""
    if tier is None:
        return False
    return other is None or TIER_RANK[tier] > TIER_RANK[other]


def partner_tier(channels: dict[str, str | None], own: str | None) -> str | None:
    """The best tier any channel other than this row's committed the neutral at.

    A partner's own confidence is most of what the corroboration flag is worth -
    measured on the gate, an assigned partner and a candidate one corroborate
    about equally on an Orbitrap while a below-assignability one is weaker, and
    on the TOF sets most partners sit below assignability - so the row records it
    and step 2.4 weighs it rather than counting rows.

    :param channels: This neutral's channels and their best tiers.
    :param own: The channel of the row being described, excluded whole: another
        row of the same neutral through the same channel is the same evidence.
    :return: The strongest partner tier, or None where there is no partner.
    """
    best: str | None = None
    for channel, tier in channels.items():
        if channel != own and _outranks(tier, best):
            best = tier
    return best


def _height(row: dict) -> float:
    """A row's peak height, 0 for one that has none."""
    try:
        height = float(row.get("sample_peak_intensity") or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return height if math.isfinite(height) else 0.0


def partner_heights(
    assignments: list[dict],
    notation_by_id: dict[str, str],
    minor_channels: frozenset[str],
) -> dict[str, dict[int, float]]:
    """How the sample shows each neutral through the mode's own channels.

    Per neutral, the peak height of every monoisotopic row that commits it at
    candidate or better through one of the mode's own channels: the partner
    gate's own bar for a partner (``engine.apply_partner_gates``). A reading of
    one of these names a molecule the sample itself bears out, which no prior
    sets aside; an opportunistic channel shows nothing on its own. The bar is
    one that the passes after the gate cannot take a row back under.

    :param assignments: Every row built for this sample.
    :param notation_by_id: The run's mechanisms, by the id the rows carry.
    :param minor_channels: The run's opportunistic channels.
    :return: Per neutral (:func:`neutral_key`), each such row's height, keyed by
        the row's identity so that a row can be left out of its own neutral's.
    """
    heights: dict[str, dict[int, float]] = {}
    for row in assignments:
        if (
            not is_committed(row)
            or row.get("role") != ROLE_M0
            or row.get("tier") not in (TIER_ASSIGNED, TIER_CANDIDATE)
        ):
            continue
        notation = notation_by_id.get(str(row.get("ionization_mechanism_id")))
        if not notation or notation in minor_channels:
            continue
        neutral = neutral_key(row.get("assigned_formula"))
        heights.setdefault(neutral, {})[id(row)] = _height(row)
    return heights


def apply_cross_channel(
    assignments: list[dict],
    *,
    notation_by_id: dict[str, str],
    minor_channels: frozenset[str] = frozenset(),
) -> dict:
    """Record each commit's channels, and cap the readings nothing settled.

    Modifies the rows in place, after both stages have built them: which
    channels a neutral was seen through is a property of the whole ledger.

    Every committed monoisotopic row whose ion reads another way records what
    that leaves it (:func:`same_ion_question`): the rival molecule where nothing
    settled it, capped at ``candidate``, or what settled it. The rival is
    recorded whether or not the cap lowered the tier - a row its evidence
    already put below says so too - and only the cap counts as ``capped``.

    :param assignments: Every row built for this sample, modified in place.
    :param notation_by_id: The run's mechanisms, by the id the rows carry.
    :param minor_channels: The run's opportunistic channels. A neutral committed
        through any other channel is one the sample shows
        (:func:`partner_heights`); none by default, where every channel is the
        mode's own.
    :return: A JSON-serializable summary for the run's config.
    """
    channels_searched = sorted(set(notation_by_id.values()))
    donors = nitrogen_donating_channels(channels_searched)
    channels = channels_by_neutral(assignments, notation_by_id)
    partners = partner_heights(assignments, notation_by_id, minor_channels)
    summary = {
        "channels": channels_searched,
        "reagent_channels": sorted(donors),
        "min_channels": CHANNELS_FOR_CORROBORATION,
        # How much brighter the row's molecule has to be shown than a rival's
        # the sample also shows for the stronger partner to settle it.
        "partner_margin": PARTNER_MARGIN,
        "committed_m0": 0,
        "neutrals": len(channels),
        "corroborated": 0,
        # Rows whose ion also reads as another molecule that nothing settled,
        # by what the two readings disagree about.
        REASON_AMBIGUOUS_NITROGEN: 0,
        REASON_AMBIGUOUS_ADDUCT: 0,
        "capped": 0,
        # Of the counts above, the rows a reference mirror committed: a list's
        # formula is matched rather than elected, so its reach is a different
        # question from the search's.
        "ambiguous_nitrogen_mirror": 0,
        "ambiguous_adduct_mirror": 0,
        "capped_mirror": 0,
        # Of the rows those count, the ones a second channel corroborates,
        # which it does not settle against a rival the sample also shows.
        "shown_rival": 0,
        # Rows whose ion reads another way and that something settled, by what.
        "settled": {
            SETTLED_BY_SECOND_CHANNEL: 0,
            SETTLED_BY_PARTNER: 0,
            SETTLED_BY_TARGET_LIBRARY: 0,
            SETTLED_BY_RADICAL: 0,
        },
        # Stored as `capped_satellites` on runs written by earlier builds. Only
        # this summary's own log line reads the count back, so a stored run is
        # never translated; a reader of old run configs has to accept both.
        "capped_isotopologues": 0,
        # Whether a channel of this mode donates nitrogen - which is what the
        # nitrogen reason can be about. The same-ion rule itself runs on every
        # sample, whatever this says.
        "reagent_rule_applied": bool(donors),
    }

    # Owner id -> the reason its cap carried, and whether its rival is one the
    # sample shows, which its isotopologues carry too.
    capped_owners: dict[str, tuple[str, bool]] = {}
    for row in assignments:
        if not is_committed(row) or row.get("role") != ROLE_M0:
            continue
        summary["committed_m0"] += 1
        seen = channels.get(neutral_key(row.get("assigned_formula")), {})
        corroborated = len(seen) >= CHANNELS_FOR_CORROBORATION
        summary["corroborated"] += corroborated
        own_channel = notation_by_id.get(str(row.get("ionization_mechanism_id")))
        record: dict = {
            "channels": sorted(seen),
            "corroborated": corroborated,
            "partner_tier": partner_tier(seen, own_channel),
        }
        question = same_ion_question(
            row, notation_by_id, corroborated=corroborated, partners=partners
        )
        if question is not None:
            reason, found = question
            if reason == SAME_ION_SETTLED:
                if found["by"] == SETTLED_BY_SECOND_CHANNEL:
                    # The channels that settled it, which is what the row's
                    # reason names.
                    found["through"] = sorted(set(seen) - {own_channel})
                record[SAME_ION_SETTLED] = found
                summary["settled"][found["by"]] += 1
            else:
                mirror = is_reference_mirror_row(row)
                shown = bool(found.get("shown"))
                record[reason] = found
                summary[reason] += 1
                summary[f"{reason}_mirror"] += mirror
                summary["shown_rival"] += shown
                if _cap(row, record, reason):
                    summary["capped"] += 1
                    summary["capped_mirror"] += mirror
                    capped_owners[str(row["peak_assignment_id"])] = (reason, shown)
        row.setdefault("provenance", {})["cross_channel"] = record

    # An isotopologue is its parent's ion on a second line of one envelope, so it
    # carries the parent's neutral and the parent's doubt. Counted apart because
    # a rule's reach over analytes and its reach over their isotopologues are
    # different numbers and reporting the sum as one hides which it moved.
    for row in assignments:
        if not is_committed(row) or row.get("role") != ROLE_ISO_CHILD:
            continue
        owner_id = str(row.get("owner_peak_assignment_id"))
        if owner_id not in capped_owners:
            continue
        reason, shown = capped_owners[owner_id]
        record = {"inherited_from": owner_id}
        if shown:
            record["shown"] = True
        if _cap(row, record, reason):
            summary["capped_isotopologues"] += 1
        row.setdefault("provenance", {})["cross_channel"] = record
    return summary


def _cap(row: dict, record: dict, reason: str) -> bool:
    """Cap a row at ``candidate``, downwards only, and say so on the row."""
    if TIER_RANK[row["tier"]] <= TIER_RANK[TIER_CANDIDATE]:
        return False
    row["tier"] = TIER_CANDIDATE
    record["capped"] = TIER_CANDIDATE
    record["reason"] = reason
    return True
