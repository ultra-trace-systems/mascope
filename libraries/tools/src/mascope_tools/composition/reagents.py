"""Reagent-ion chemistry: which channels a source is actually running.

An ion source produces more channels than the mechanism panel of a deployment
usually lists. A urea-CIMS source makes ammonium adducts as well as the
protonated and urea-clustered ions the mode declares; a bromide source makes
carbonate and dibromide clusters. Searching those channels is what step 1.2 of
``docs/dev/assignment_quality_plan.md`` is for - 1,648 ``[M+NH4]+`` main peaks
on one gate set that the engine could otherwise only read as heavier N-free
neutrals.

Searching them *unconditionally* is the mistake this module exists to prevent.
Measured on the same spectra: the ammonium channel is real there - its cluster
is in every spectrum, four in five of the reference engine's ammonium readings
are corroborated by a second channel - while sodium is not. No sodium cluster
is present at all (one trace at 0.008% of the base peak in one sample), yet the
reference engine still commits 317 ``[M+Na]+`` readings, 81% of them candidates
on dim peaks. An adduct channel with no source chemistry behind it does not
read the sample; it absorbs unexplained mass.

So a secondary channel is switched on per sample **by its own fingerprint**:
the cluster ions the channel's carrier makes on its own, matched against the
spectrum above an intensity floor. Present, the channel is searched; absent, it
is not, whatever the mechanism table offers.

The module has two halves, and the difference between them is what a match is
allowed to do.

The **probes** are the narrow half: enough exact masses to answer "is this
carrier in this spectrum". A probe claims no peak and writes no row, so it can
afford to count evidence that would be unsafe to act on.

The **cluster library** is the wide half: the source's own ion ladder, with
hydrates and predicted isotopologues, matched against the peak list before
either assignment stage runs. A library ion CLAIMS the peak it matches - the
peak becomes a reagent row and leaves the analyte ledger - so it is held to
stricter rules: every atom in a library ion comes from the reagent, the solvent,
the air or the instrument, and none from the sample; and a work names the ion,
which it carries. The section comment on the library says where the two lists
disagree, and why that is deliberate rather than an inconsistency.

Why the library exists at all: on the gate's samples the reagent's own clusters
are the top ten peaks and most of the total signal, and with nowhere to put them
they land in the residual or, worse, get read as analytes whose formula happens
to fit.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Mapping, Sequence

import numpy as np

from mascope_tools.composition.config import ELECTRON_MASS
from mascope_tools.composition.heuristic_filter import (
    monoisotopic_index,
    predict_isotopes,
)
from mascope_tools.composition.utils import (
    composition_mass,
    parse_composition,
    to_hill_notation,
)


def ion_mz(formula: str, charge: int) -> float:
    """The m/z of an ion of this composition and charge.

    :param formula: The ion's elemental composition, charge excluded.
    :param charge: The ion's charge.
    :return: Its m/z: the composition's mass less the charge's electrons, over
        the charge's magnitude.
    """
    mass = composition_mass(parse_composition(formula))
    return (mass - charge * ELECTRON_MASS) / abs(charge)


#: Intensity a probe ion must reach, relative to the spectrum's base peak, for
#: its channel to count as present. The sodium counter-example sets it: the one
#: sodium trace found across the uronium gate set sits at 0.008% of the base
#: peak, which a 0.01% floor excludes, while the ammonium cluster that is
#: genuinely there is an order of magnitude above it.
DEFAULT_CHANNEL_MIN_RELATIVE_INTENSITY = 1e-4

#: Window a probe ion is looked for in, in ppm - deliberately far wider than the
#: window the same run searches compositions in.
#:
#: Finding a cluster ion is a detection question, not an assignment: the answer
#: is "this carrier is here", nothing is committed to a peak, and a probe that
#: matched a neighbouring ion by mistake still gives the right answer about the
#: carrier. So the window is set by how far a real cluster ion can sit from its
#: mass, which on the gate's bromide TOF set is about 11 ppm across the whole
#: ladder - a plain calibration offset, which a 3 ppm probe would miss entirely
#: while a composition search at 3 ppm is still right for the analytes.
#:
#: CAUTION, because this comment used to draw the opposite lesson and it was
#: wrong. A single ion sitting 20-30 ppm from a reagent mass is far more often a
#: DIFFERENT ion than a drifted one: the peaks 21 to 28 ppm above the urea
#: tetramer and pentamer masses on the dense uronium set are one ambient
#: compound read through three channels, each within a ppm of its own exact
#: mass, while that sample's reagent ladder sits within 5 ppm of its. A width
#: that is harmless for detection is not evidence of anything, and a pass that
#: CLAIMS peaks must not reuse it - see :func:`match_reagent_clusters`, which
#: uses this window only to find its anchors and then claims at the
#: instrument's own precision against the mass those anchors corrected.
#:
#: The observed error is recorded with every hit, so a probe that matched far
#: off its mass says so rather than passing silently.
DEFAULT_CHANNEL_MATCH_PPM = 20.0


@dataclass(frozen=True)
class ProbeIon:
    """One cluster ion whose presence is evidence for a channel.

    :param formula: The ion's elemental composition, charge excluded
        (``"C2H12N5O2"`` for ``[(CH4N2O)2+NH4]+``).
    :param charge: The ion's charge, ``+1`` or ``-1``.
    :param label: How the ion is written in a log or a run's provenance.
    """

    formula: str
    charge: int
    label: str

    @property
    def mz(self) -> float:
        """The ion's m/z: the composition's mass less the charge's electrons."""
        return ion_mz(self.formula, self.charge)


#: What a channel does when the spectrum could not have shown its fingerprint -
#: every probe below the acquisition's first mass or above its last. This is a
#: fact about the acquisition, not about the source, so it is not the same
#: question as "was the carrier there", and a channel says which answer it wants.
UNOBSERVABLE_OFF = "off"
UNOBSERVABLE_ON = "on"


@dataclass(frozen=True)
class SecondaryChannel:
    """An adduct channel a source can produce, and the evidence that it does.

    :param notation: The ionization mechanism the channel is searched under,
        in the standard adduct notation the mechanism table stores
        (``"[M+NH4]+"``).
    :param label: Display label.
    :param probes: Cluster ions of the channel's own carrier. One hit above the
        floor switches the channel on; a channel with no probes never switches
        on, which is deliberate - an unprovable channel is not searched.
    :param when_unobservable: What to do when no probe could have been seen at
        all. :data:`UNOBSERVABLE_OFF` by default - silence is not evidence -
        but a channel whose carrier is known to exist only below a mass some
        acquisitions start above can say :data:`UNOBSERVABLE_ON` instead, and
        then it is the acquisition rather than the source that failed to
        answer.
    :param declared_stays_secondary: Whether a mode that declares this channel
        itself still has it held to an opportunistic channel's rules - an
        uncorroborated winner capped at candidate, a tie lost to the mode's
        other channels. True by default, and the plan owner's ruling for
        carbonate: an opportunistic side channel is capped without
        corroboration whoever declares it. False where declaring the channel
        is the operator saying the source runs it as its own - proton transfer
        or deprotonation beside the electron transfer of a charge-transfer
        source - so the declared channel is searched as the mode's own and
        only the ones the profile adds are opportunistic.
    :param needs_partner: Whether a reading through this channel stands only
        where the sample commits the neutral it proposes through one of the
        mode's own channels. An ion the mode's channel also reads as a
        molecule is one measurement split two ways, and the finder's election
        prefers the heavier mechanism, which is this channel's every time: the
        C10 product with formate over the C11 acid, protonated C7H6 over
        toluene less a hydride. Where nothing else in the sample shows the
        neutral, that preference is a prior and the mode's own reading is the
        row's; where the sample does show it, the channel's reading stands
        and is corroborated by the same fact. False for carbonate, whose
        readings are measured under the earlier policy (a declared or opened
        channel's reading wins the family and is capped without corroboration)
        and stay there until re-measured.
    :param note: Why these probes are the right evidence, for the reader who
        wonders why a channel stayed off.
    """

    notation: str
    label: str
    probes: tuple[ProbeIon, ...] = ()
    when_unobservable: str = UNOBSERVABLE_OFF
    declared_stays_secondary: bool = True
    needs_partner: bool = False
    note: str = ""


def _urea_ammonium_clusters() -> tuple[ProbeIon, ...]:
    """``[(CH4N2O)n+NH4]+`` for n = 1, 2, 3.

    The monomer is included here and must NOT be in the reagent library step
    1.4 builds, and the difference is worth stating because it looks like an
    inconsistency. ``[urea+NH4]+`` is the same ion as ``[NH3+(urea)H]+``:
    ambient ammonia read through its urea adduct. That makes it an analyte, so
    a library that claims peaks must leave it alone - but it is still ammonium
    clustering with urea in the source region, which is exactly the question a
    fingerprint asks. A probe claims no peak and writes no row; it only decides
    whether a channel is worth searching.

    It carries the evidence on the sparse gate set, where the multimer clusters
    are absent from the spectrum entirely and the monomer sits at 0.14% of the
    base peak. On the dense set it is the other way round.
    """
    return (
        ProbeIon("CH8N3O", 1, "[(CH4N2O)+NH4]+"),
        ProbeIon("C2H12N5O2", 1, "[(CH4N2O)2+NH4]+"),
        ProbeIon("C3H16N7O3", 1, "[(CH4N2O)3+NH4]+"),
    )


def _urea_sodium_clusters() -> tuple[ProbeIon, ...]:
    """``[(CH4N2O)n+Na]+`` and the solvated sodium ions a source with sodium in
    it shows."""
    return (
        ProbeIon("CH4N2ONa", 1, "[(CH4N2O)+Na]+"),
        ProbeIon("C2H8N4O2Na", 1, "[(CH4N2O)2+Na]+"),
        ProbeIon("H2NaO", 1, "[Na+H2O]+"),
        ProbeIon("H4NaO2", 1, "[Na+2H2O]+"),
    )


def _solvated(symbol: str, label: str, waters: Iterable[int] = (1, 2)) -> tuple:
    """The solvated-cation series of an alkali or ammonium carrier."""
    base = parse_composition(symbol)
    probes = []
    for k in waters:
        counts = dict(base)
        counts["H"] = counts.get("H", 0) + 2 * k
        counts["O"] = counts.get("O", 0) + k
        formula = "".join(f"{el}{n}" if n > 1 else el for el, n in counts.items())
        probes.append(ProbeIon(formula, 1, f"[{label}+{k}H2O]+"))
    return tuple(probes)


def _carbonate_probes(reagent_formula: str | None) -> tuple[ProbeIon, ...]:
    """Carbonate's own ions, and its clusters with the reagent's acid.

    The bare radical anion and bicarbonate are the chemistry showing itself,
    but both sit near m/z 60 and plenty of acquisitions start above that. The
    reagent-acid clusters carry the same evidence 60-65 Da higher, which is
    what puts the channel within reach of a window starting at 120.

    They are built from the profile's own reagent formula rather than written
    out, so a labelled reagent's label follows into the cluster: the 15N-nitrate
    profile gets ``[CO3+H(15N)O3]-`` at m/z 124 and ``[HCO3+H(15N)O3]-`` at 125,
    0.997 Da above their unlabelled twins, without a second table to keep in
    step with the first.

    :param reagent_formula: The reagent ion's composition, or None.
    :return: The probe ions, the clusters omitted when there is no reagent.
    """
    probes = [
        ProbeIon("CO3", -1, "[CO3]-"),
        ProbeIon("CHO3", -1, "[HCO3]-"),
    ]
    if reagent_formula:
        acid = f"H{reagent_formula}"
        probes += [
            ProbeIon(f"CO3{acid}", -1, f"[CO3+{acid}]-"),
            ProbeIon(f"CHO3{acid}", -1, f"[HCO3+{acid}]-"),
        ]
    return tuple(probes)


#: The bare dihalide radical anions. In a halide source these are rungs of the
#: reagent's own cluster ladder, so their presence is exactly the statement that
#: the source clusters its reagent - which is what the cluster adduct channel
#: needs.
_DIBROMIDE_PROBES = (ProbeIon("Br2", -1, "[Br2]-"),)
_DIIODIDE_PROBES = (ProbeIon("I2", -1, "[I2]-"),)


# Every formate channel needs a partner (``SecondaryChannel.needs_partner``,
# read by the engine's partner gate): every deprotonated acid also reads as
# the molecule 46 Da lighter with formate, and measured on the chamber
# dataset the election alone took that reading for 1,239 acids of six
# samples and capped them all. Only where the lighter molecule is itself
# seen through a mode channel is the formate reading the row's; otherwise
# the acid is, with the formate reading set aside on it. Of the 240 C11
# pseudo-acid rows, 193 have such a partner.
def _formate_probes(reagent_formula: str | None) -> tuple[ProbeIon, ...]:
    """Formate's own ions, its cluster with the reagent's acid, and its hydrate.

    The bare anion at m/z 45 and its dimer with formic acid at 91 are the
    carrier showing itself: on the chamber dataset that measured this channel
    they sit at 14-40% and 11-65% of the base peak of every spectrum acquired
    from m/z 42. The cluster with the reagent's acid carries the same evidence
    63 Da higher, and it is built from the profile's own reagent so a labelled
    reagent's label follows into it: the 15N-nitrate profile probes
    ``[HCOO+H(15N)O3]-`` at 108.99, where that dataset shows it at 0.1-0.6% of
    the base peak - and where the engine, lacking the channel, read it as
    formic acid through the nitrate adduct, which is the same ion. The
    cluster's hydrate, 18 Da above it, is the highest formate carrier the
    source makes (127.00 on the labelled source), so a window that starts
    between the cluster and the hydrate still answers for itself.

    :param reagent_formula: The reagent ion's composition, or None.
    :return: The probe ions, the cluster and its hydrate omitted when there
        is no reagent.
    """
    probes = [
        ProbeIon("CHO2", -1, "[HCOO]-"),
        ProbeIon("C2H3O4", -1, "[HCOO+HCOOH]-"),
    ]
    if reagent_formula:
        acid = f"H{reagent_formula}"
        probes.append(ProbeIon(f"CHO2{acid}", -1, f"[HCOO+{acid}]-"))
        probes.append(ProbeIon(f"CHO2{acid}H2O", -1, f"[HCOO+{acid}+H2O]-"))
    return tuple(probes)


#: Why the nitrate profiles default their formate channel ON where a spectrum
#: could not have shown it, as they do carbonate. The batch that carries the
#: C11 pseudo-acids is acquired from m/z 130, above every formate carrier the
#: source makes: on the same source's wide-window batch the anion, the dimer
#: and the reagent-acid cluster are bright, the hydrate of that cluster sits at
#: 127 and is the last probe, and above 130 there is nothing - no cluster with
#: two acids at 173, the cluster with two formic acids at 137 at 0.01-0.05% of
#: the base peak, the height of an analyte rather than a carrier. So a window
#: starting at 130 cannot show this channel however loud the source runs it,
#: and reading its silence as absence would read a fact about the window as
#: a fact about the chemistry. What the channel may then do is bounded: its
#: reading of an ion the mode's own channel also reads stands only where the
#: neutral it proposes is itself seen through a mode channel, and otherwise
#: the mode's own reading is the row's with the formate one set aside (the
#: partner gate, above); and it commits as assigned only with corroboration.
_NITRATE_FORMATE_NOTE = (
    "formate, its dimer, its cluster with the reagent's acid and that "
    "cluster's hydrate; the source makes no formate carrier above m/z 127, "
    "so a window starting higher cannot show the channel and its silence is "
    "not evidence"
)

#: The halide profiles keep the evidence they can show, as they do for
#: carbonate: the bare formate ions only, and a window that cannot show them
#: leaves the channel off.
_HALIDE_FORMATE_NOTE = "formate and its dimer with formic acid"


#: The fluoranthene ion of a charge-transfer (EASY-IC) source, in the polarity
#: the source runs. Its presence is the statement that the fluoranthene beam is
#: what ionizes this sample, which is what the source's abstraction channel
#: needs: the reagent cation is the hydride acceptor, so where it is, so is
#: hydride abstraction.
_FLUORANTHENE_CATION_PROBES = (
    ProbeIon("C16H10", 1, "[C16H10]+"),
    ProbeIon("C16H9", 1, "[C16H10-H]+"),
)

#: Evidence that a charge-transfer source has protons to give: hydronium and
#: its first hydrate, the two water ions a dry source still shows (on the
#: reference engine's low-mass acquisition of this source both are present
#: while the larger hydrates are exactly zero, which is why the series stops
#: at two). Protonated fluoranthene is deliberately not a probe: it sits 22 ppm
#: above the beam's own 13C line, inside the probe window, so a spectrum
#: reading two ppm high would switch the channel on with no protonated
#: reagent in it at all.
_PROTON_TRANSFER_PROBES = (
    ProbeIon("H3O", 1, "[H3O]+"),
    ProbeIon("H5O2", 1, "[H3O+H2O]+"),
)

#: The deprotonated acids a negative charge-transfer source makes on its own:
#: bicarbonate, nitrate, formate and nitrite, the anions of the carbonic,
#: nitric, formic and nitrous acids in any air sample. Where the source's
#: anions have taken a proton off those, they take one off the sample's acids.
_DEPROTONATED_ACID_PROBES = (
    ProbeIon("CHO3", -1, "[HCO3]-"),
    ProbeIon("NO3", -1, "[NO3]-"),
    ProbeIon("CHO2", -1, "[HCOO]-"),
    ProbeIon("NO2", -1, "[NO2]-"),
)

#: Why the charge-transfer channels default ON where a spectrum could not have
#: shown them. The fluoranthene ion sits at m/z 202, and the acquisitions this
#: source is used for are narrow: the chamber batches that measured this
#: profile were acquired at m/z 42 to 160, where no fluoranthene ion, no
#: hydronium and no reagent-derived cluster could appear. The source ran all
#: the same, so the window's silence is a fact about the window.
_CHARGE_TRANSFER_NOTE = (
    "the fluoranthene beam is the source's own reagent and this acquisition "
    "cannot show it below m/z 202, so its silence is not evidence"
)

#: Why the methyl-loss channel is read off the same beam as hydride abstraction.
#: The channel is not the beam's chemistry: it is what charge transfer leaves of
#: a radical cation that received more energy than its weakest bond holds, and
#: a methyl group on a quaternary carbon or on silicon is that bond. The cyclic
#: siloxanes' spectra carry their methyl-loss ion as the base peak, and
#: alpha-pinene's carries it at m/z 121 [nist]. So the evidence that the
#: channel runs is the evidence that the charge-transfer source runs, which is
#: the beam, and a window that cannot show the beam leaves it on for the same
#: reason it leaves hydride abstraction on. Like hydride abstraction it reads an
#: ion the mode's own channel also reads as another molecule (pinene less a
#: methyl is protonated C9H12), so a reading through it stands only where the
#: sample commits the molecule it proposes through one of the mode's own
#: channels.
_METHYL_LOSS_NOTE = (
    "charge transfer breaks a methyl group off a radical cation with energy to "
    "spare; read off the fluoranthene beam, which this acquisition cannot show "
    "below m/z 202, so its silence is not evidence"
)


#: Why the nitrate profiles default their carbonate channel ON where a spectrum
#: could not have shown it. Measured on a broad-window (m/z 50-650) run of the
#: same chemistry as the narrow gate set: every sample carries [CO3]- at 0.5-1.0%
#: of the base peak and the labelled acid clusters at 0.1-0.44%, and there is no
#: carbonate carrier above m/z 126 at all - no dimer at 188, no trimer at 191,
#: because the source declusters beyond the dimer. So an acquisition starting
#: above 126 cannot show this channel however hard the source runs it, and
#: reading its silence as absence would be reading a fact about the window as a
#: fact about the chemistry. What the channel may then do is still bounded: it
#: takes no peak from a declared mechanism, and commits as assigned only with
#: corroboration.
_NITRATE_CARBONATE_NOTE = (
    "carbonate and its clusters with the reagent's acid; the source makes no "
    "carbonate carrier above m/z 126, so a window starting higher cannot show "
    "the channel and its silence is not evidence"
)

#: Secondary channels per reagent profile name. The panel is a fact about the
#: source chemistry; whether a given sample ran a channel is the fingerprint's
#: answer, and whether the deployment can express it is the mechanism table's.
SECONDARY_CHANNELS: dict[str, tuple[SecondaryChannel, ...]] = {
    "UR": (
        SecondaryChannel(
            notation="[M+NH4]+",
            label="Ammonium adduct",
            probes=_urea_ammonium_clusters(),
            note=(
                "the urea-ammonium multimer; present in every uronium spectrum "
                "of the gate set"
            ),
        ),
    ),
    "BR": (
        SecondaryChannel(
            notation="[M+CO3]-",
            label="Carbonate adduct",
            # The bare ions only. The reagent-acid cluster is measured for
            # nitrate and not for bromide, and on the bromide gate set the
            # candidate [HCO3+HBr]- line is an order of magnitude weaker than
            # nitrate's clusters (0.06% of base against 0.1-0.44%) - weak enough
            # to be an analyte at that mass rather than a carrier. Reading it as
            # a fingerprint switched the channel on and cost 24 of the set's
            # same-formula agreements with the reference, so bromide keeps the
            # evidence it can actually show.
            probes=_carbonate_probes(None),
            note="the source's own carbonate ions",
        ),
        SecondaryChannel(
            notation="[M+Br2]-",
            label="Dibromide cluster",
            probes=_DIBROMIDE_PROBES,
            note="the reagent's own second cluster rung",
        ),
        SecondaryChannel(
            notation="[M+HCOO]-",
            label="Formate adduct",
            needs_partner=True,
            probes=_formate_probes(None),
            note=_HALIDE_FORMATE_NOTE,
        ),
    ),
    "NO3": (
        SecondaryChannel(
            notation="[M+CO3]-",
            label="Carbonate adduct",
            probes=_carbonate_probes("NO3"),
            when_unobservable=UNOBSERVABLE_ON,
            note=_NITRATE_CARBONATE_NOTE,
        ),
        SecondaryChannel(
            notation="[M+HCOO]-",
            label="Formate adduct",
            needs_partner=True,
            probes=_formate_probes("NO3"),
            when_unobservable=UNOBSERVABLE_ON,
            note=_NITRATE_FORMATE_NOTE,
        ),
    ),
    "NO3_15N": (
        SecondaryChannel(
            notation="[M+CO3]-",
            label="Carbonate adduct",
            # Built from the labelled reagent, so the clusters land 0.997 Da
            # above the unlabelled ones. Carbonate itself carries no reagent
            # nitrogen, which is why the bare ions are the same either way.
            probes=_carbonate_probes("^NO3"),
            when_unobservable=UNOBSERVABLE_ON,
            note=_NITRATE_CARBONATE_NOTE,
        ),
        SecondaryChannel(
            notation="[M+HCOO]-",
            label="Formate adduct",
            needs_partner=True,
            probes=_formate_probes("^NO3"),
            when_unobservable=UNOBSERVABLE_ON,
            note=_NITRATE_FORMATE_NOTE,
        ),
    ),
    "IODIDE": (
        SecondaryChannel(
            notation="[M+I2]-",
            label="Diiodide cluster",
            probes=_DIIODIDE_PROBES,
            note="the reagent's own second cluster rung",
        ),
        SecondaryChannel(
            notation="[M+HCOO]-",
            label="Formate adduct",
            needs_partner=True,
            probes=_formate_probes(None),
            note=_HALIDE_FORMATE_NOTE,
        ),
    ),
    "EASYIC_POS": (
        SecondaryChannel(
            notation="[M-H]+",
            label="Hydride abstraction",
            probes=_FLUORANTHENE_CATION_PROBES,
            when_unobservable=UNOBSERVABLE_ON,
            declared_stays_secondary=False,
            needs_partner=True,
            note=_CHARGE_TRANSFER_NOTE,
        ),
        SecondaryChannel(
            notation="[M+H]+",
            label="Proton transfer",
            probes=_PROTON_TRANSFER_PROBES,
            when_unobservable=UNOBSERVABLE_ON,
            declared_stays_secondary=False,
            needs_partner=True,
            note=(
                "hydronium: the source has protons to give; the acquisitions "
                "this source is used for start above it, and its silence there "
                "is the window's"
            ),
        ),
        SecondaryChannel(
            notation="[M-CH3]+",
            label="Methyl loss",
            probes=_FLUORANTHENE_CATION_PROBES,
            when_unobservable=UNOBSERVABLE_ON,
            declared_stays_secondary=False,
            needs_partner=True,
            note=_METHYL_LOSS_NOTE,
        ),
    ),
    "EASYIC_NEG": (
        SecondaryChannel(
            notation="[M-H]-",
            label="Deprotonation",
            probes=_DEPROTONATED_ACID_PROBES,
            when_unobservable=UNOBSERVABLE_ON,
            declared_stays_secondary=False,
            needs_partner=True,
            note=(
                "the source's anions deprotonate the acids of air; where "
                "bicarbonate, nitrate, formate or nitrite is in the spectrum "
                "they deprotonate the sample's acids too"
            ),
        ),
    ),
    "ESI_POS": (
        SecondaryChannel(
            notation="[M+NH4]+",
            label="Ammonium adduct",
            probes=_solvated("N1H4", "NH4"),
            note="solvated ammonium from the sprayed solution",
        ),
        SecondaryChannel(
            notation="[M+Na]+",
            label="Sodium adduct",
            probes=_solvated("Na", "Na"),
            note="solvated sodium from residual salt",
        ),
        SecondaryChannel(
            notation="[M+K]+",
            label="Potassium adduct",
            probes=_solvated("K", "K"),
            note="solvated potassium from residual salt",
        ),
    ),
}


def secondary_channels(profile_name: str) -> tuple[SecondaryChannel, ...]:
    """The secondary channels a reagent profile can produce.

    :param profile_name: The profile's name.
    :return: Its channels, empty when it declares none.
    """
    return SECONDARY_CHANNELS.get(profile_name, ())


#: A probe matched a peak above the floor.
STATUS_FOUND = "found"
#: Every probe was inside the acquisition and none matched: the carrier is not
#: there, which is evidence.
STATUS_NOT_FOUND = "not_found"
#: No probe was inside the acquisition at all, so the spectrum was never asked.
#: Silence here says nothing about the source; what happens next is the
#: channel's ``when_unobservable``.
STATUS_UNOBSERVABLE = "unobservable"


@dataclass(frozen=True)
class ChannelEvidence:
    """What a spectrum said about one secondary channel.

    ``present`` is the decision - was the channel searched - and ``status`` is
    the reason. They come apart exactly once: an unobservable channel whose
    profile defaults it on is present on no evidence, and the record says so
    rather than implying a fingerprint nobody found.

    :param notation: The channel's mechanism notation.
    :param present: Whether the channel is searched for this sample.
    :param status: One of :data:`STATUS_FOUND`, :data:`STATUS_NOT_FOUND`,
        :data:`STATUS_UNOBSERVABLE`.
    :param probe: The probe ion that matched, when one did.
    :param mz: The observed m/z of that match.
    :param mz_error_ppm: How far that observation sat from the probe's mass.
    :param relative_intensity: Its intensity relative to the base peak.
    """

    notation: str
    present: bool
    status: str = STATUS_NOT_FOUND
    probe: str | None = None
    mz: float | None = None
    mz_error_ppm: float | None = None
    relative_intensity: float | None = None

    def as_dict(self) -> dict:
        """A JSON-serializable record for a run's config snapshot."""
        record: dict = {
            "channel": self.notation,
            "present": self.present,
            "status": self.status,
        }
        if self.probe is not None:
            record["probe"] = self.probe
            record["mz"] = round(self.mz, 5) if self.mz is not None else None
            record["mz_error_ppm"] = (
                round(self.mz_error_ppm, 2) if self.mz_error_ppm is not None else None
            )
            record["relative_intensity"] = (
                float(f"{self.relative_intensity:.2e}")
                if self.relative_intensity is not None
                else None
            )
        return record


def detect_channels(
    channels: Sequence[SecondaryChannel],
    mz: Sequence[float] | np.ndarray,
    intensity: Sequence[float] | np.ndarray,
    *,
    ppm: float = DEFAULT_CHANNEL_MATCH_PPM,
    min_relative_intensity: float = DEFAULT_CHANNEL_MIN_RELATIVE_INTENSITY,
) -> list[ChannelEvidence]:
    """Which of these channels this spectrum shows the fingerprint of.

    A channel is present when at least one of its probe ions matches a peak
    within ``ppm`` whose intensity reaches ``min_relative_intensity`` of the
    spectrum's base peak. The brightest qualifying match is the one recorded, so
    the evidence a run stores is the strongest the spectrum offered.

    A channel none of whose probes falls inside the acquisition's own mass range
    is a third case and is recorded as one: the spectrum was never asked, so its
    silence is not evidence of absence, and the channel's
    ``when_unobservable`` decides. That is the difference between a source that
    does not run a channel and an acquisition that could not have shown it.

    :param channels: The candidate channels, from :func:`secondary_channels`.
    :param mz: The spectrum's m/z values.
    :param intensity: Their intensities, in the same order.
    :param ppm: Match tolerance; see :data:`DEFAULT_CHANNEL_MATCH_PPM` for why
        it is not the run's composition-search window.
    :param min_relative_intensity: The floor, relative to the base peak.
    :return: One :class:`ChannelEvidence` per channel, in the order given.
    """
    mz_array = np.asarray(mz, dtype=float)
    intensity_array = np.asarray(intensity, dtype=float)
    base_peak = float(intensity_array.max()) if intensity_array.size else 0.0
    usable = base_peak > 0.0 and mz_array.size > 0
    # The acquisition's own range, which is what makes a probe answerable at
    # all. Read off the peaks rather than configured: it is the mass range this
    # sample actually produced peaks over.
    low, high = (float(mz_array.min()), float(mz_array.max())) if usable else (0.0, 0.0)
    evidence: list[ChannelEvidence] = []
    for channel in channels:
        best: ChannelEvidence | None = None
        observable = False
        if usable:
            for probe in channel.probes:
                target = probe.mz
                window = target * ppm * 1e-6
                if not (low - window <= target <= high + window):
                    continue
                observable = True
                hits = np.flatnonzero(np.abs(mz_array - target) <= window)
                for hit in hits:
                    relative = float(intensity_array[hit]) / base_peak
                    if relative < min_relative_intensity:
                        continue
                    if best is None or relative > (best.relative_intensity or 0.0):
                        observed = float(mz_array[hit])
                        best = ChannelEvidence(
                            notation=channel.notation,
                            present=True,
                            status=STATUS_FOUND,
                            probe=probe.label,
                            mz=observed,
                            mz_error_ppm=(observed - target) / target * 1e6,
                            relative_intensity=relative,
                        )
        if best is not None:
            evidence.append(best)
        elif channel.probes and not observable:
            # The spectrum was never asked, so its silence is not an answer.
            evidence.append(
                ChannelEvidence(
                    channel.notation,
                    present=channel.when_unobservable == UNOBSERVABLE_ON,
                    status=STATUS_UNOBSERVABLE,
                )
            )
        else:
            evidence.append(
                ChannelEvidence(
                    channel.notation, present=False, status=STATUS_NOT_FOUND
                )
            )
    return evidence


def present_notations(evidence: Iterable[ChannelEvidence]) -> list[str]:
    """The notations of the channels a spectrum switched on, in order."""
    return [item.notation for item in evidence if item.present]


def evidence_records(evidence: Iterable[ChannelEvidence]) -> list[dict]:
    """The evidence as a run records it: every channel considered, with its
    verdict, so a run says why a channel was off as well as why one was on."""
    return [item.as_dict() for item in evidence]


def channel_notes(channels: Sequence[SecondaryChannel]) -> Mapping[str, str]:
    """Notation to the note explaining what its fingerprint means."""
    return {channel.notation: channel.note for channel in channels}


# --- the reagent cluster library ----------------------------------------------
#
# The wide half of this module. A probe above asks whether a carrier is in the
# spectrum; a cluster here CLAIMS the peak it matches, so what belongs in this
# table is a stricter question than what belongs in a fingerprint.
#
# Two rules decide it. The first is that an entry must be reagent all the way
# through: every atom in it comes from the reagent, the solvent, the air the
# source ionizes, or the instrument, and none from the sample. A cluster of the
# reagent with something the sample supplied is not a reagent ion at all - it
# is the analyte, measured through its adduct channel, which is the primary
# thing this engine is for. So:
#
# - the reagent's clusters with organic acids are OUT. ``[Br+HCOOH]-`` is
#   ``[formic acid+Br]-``: the [M+Br]- analyte channel, spelled backwards. The
#   reference engine carried those for a while and they cost it real ambient
#   acids, formic acid among them, buried as "reagent";
# - ``[(CH4N2O)+NH4]+`` is OUT for the same reason one level up. It is the same
#   ion as ``[NH3+(CH4N2O)H]+`` - ambient ammonia read through its urea adduct -
#   so the analyte is the NH3. The same ion is a probe above, where claiming
#   nothing makes it safe evidence;
# - the oxides split by halogen. ``BrO3-`` is reagent; ``IO3-`` is iodate, the
#   deprotonated iodic acid that an iodide source is usually deployed to
#   measure, so the iodine oxides are left for the assignment stages.
#
# The second is that the claim can be checked: a work names the ion
# (:data:`SOURCE_ION_REFERENCES`), and the ion carries the works that do. Being
# reagent all the way through is not enough on its own, because a claim takes
# the peak before either stage sees it. A few rungs no work found names are in
# on the evidence of Mascope's test spectra, and say so in ``observed``:
# bromide's trimer, its cluster with HBr and its two oxides, and urea's
# protonated trimer, each in every file of at least one set. The rest of what a
# grammar of these ladders would enumerate - the higher bromide and iodide
# clusters and their hydrates, the urea multimers above the trimer and those
# carrying ammonium, the iodine oxide clusters, the bromide precursors' anions -
# no work names and the test spectra do not show, so it is left to the stages.


#: A bare cluster of the reagent with itself.
KIND_CLUSTER = "cluster"
#: The reagent clustered with water or with the acid it sheds.
KIND_ADDUCT = "adduct"
#: A reagent-halogen oxide anion.
KIND_OXIDE = "oxide"
#: A fragment of the reagent ion itself.
KIND_FRAGMENT = "fragment"

# --- the families -------------------------------------------------------------
#
# Where an ion the pass claims comes from, which is what a reader checking a
# claim against the literature needs first. Three families are claimed before
# the stages, each with the references that name its ions
# (:data:`SOURCE_ION_REFERENCES`); the fourth is claimed after them
# (:class:`FragmentLadder`). A contaminant is not a family here: the ones a
# chemist recognises at sight, the cyclic siloxanes first, are analytes on an
# indoor-air deployment, so they are named from their reference list, which
# reads them as background, rather than claimed before the sample is asked.

#: The reagent's own ladder and the companions it makes: the ion a CIMS source
#: is dosed with, its clusters, hydrates and oxides.
FAMILY_REAGENT = "reagent"
#: The discharge's own ions in air: what an ionizer makes of nitrogen, oxygen,
#: water and carbon dioxide before any reagent or analyte is involved. On a
#: charge-transfer source these are the source; on a reagent source, its
#: background.
FAMILY_AIR = "air"
#: The calibrant beam of an Orbitrap's internal-calibration source: the
#: fluoranthene ion and what the beam does to itself.
FAMILY_CALIBRANT = "calibrant"
#: A fragment of an analyte's ion, claimed only where the analyte is committed
#: (:class:`FragmentLadder`).
FAMILY_FRAGMENT = "fragment"

#: Every family, in the order a reader meets them.
FAMILIES = (
    FAMILY_REAGENT,
    FAMILY_AIR,
    FAMILY_CALIBRANT,
    FAMILY_FRAGMENT,
)

#: The families whose claimed lines say where a spectrum puts its masses, where
#: the target library is too thin to (the reagent-line offset). The reagent's
#: ladder and the calibrant beam are a source's brightest lines and the lines
#: that offset was measured on. The air's ions sit at the low-mass end, where
#: an Orbitrap's axis bends away from its centre, so counting them would pull
#: the offset towards the bend rather than read the axis.
REAGENT_LINE_FAMILIES = frozenset({FAMILY_REAGENT, FAMILY_CALIBRANT})


@dataclass(frozen=True)
class SourceReference:
    """A publication that names ions the pass claims.

    :param citation: Authors, year, title, journal, volume and first page.
    :param doi: Its DOI; None for a work that has none.
    :param url: Where to read a work that has no DOI.
    """

    citation: str
    doi: str | None = None
    url: str | None = None


#: The works that name the library's ions, by the key its entries carry, each
#: cited for an ion only where it names that ion. The how-it-works page lists
#: the same works.
SOURCE_ION_REFERENCES: dict[str, SourceReference] = {
    "good70": SourceReference(
        "Good, A.; Durden, D. A.; Kebarle, P. (1970). Ion-molecule reactions in "
        "pure nitrogen and nitrogen containing traces of water at total "
        "pressures 0.5-4 torr. Kinetics of clustering reactions forming "
        "H+(H2O)n. J. Chem. Phys. 52, 212-221.",
        doi="10.1063/1.1672667",
    ),
    "good70b": SourceReference(
        "Good, A.; Durden, D. A.; Kebarle, P. (1970). Mechanism and rate "
        "constants of ion-molecule reactions leading to formation of H+(H2O)n "
        "in moist oxygen and air. J. Chem. Phys. 52, 222-229.",
        doi="10.1063/1.1672668",
    ),
    "sha66": SourceReference(
        "Shahin, M. M. (1966). Mass-spectrometric studies of corona discharges "
        "in air at atmospheric pressures. J. Chem. Phys. 45, 2600-2605.",
        doi="10.1063/1.1727980",
    ),
    "sha69": SourceReference(
        "Shahin, M. M. (1969). Nature of charge carriers in negative coronas. "
        "Appl. Opt. 8(S1), 106-110.",
        doi="10.1364/AO.8.S1.000106",
    ),
    "sab12": SourceReference(
        "Sabo, M.; Matejcik, S. (2012). Corona discharge ion mobility "
        "spectrometry with orthogonal acceleration time of flight mass "
        "spectrometry for monitoring of volatile organic compounds. Anal. Chem. "
        "84, 5327-5334.",
        doi="10.1021/ac300722s",
    ),
    "sab13": SourceReference(
        "Sabo, M.; Matejcik, S. (2013). A corona discharge atmospheric pressure "
        "chemical ionization source with selective NO+ formation and its "
        "application for monoaromatic VOC detection. Analyst 138, 6907-6912.",
        doi="10.1039/c3an00964e",
    ),
    "kol04": SourceReference(
        "Kolakowski, B. M.; Grossert, J. S.; Ramaley, L. (2004). Studies on the "
        "positive-ion mass spectra from atmospheric pressure chemical "
        "ionization of gases and solvents used in liquid chromatography and "
        "direct liquid injection. J. Am. Soc. Mass Spectrom. 15, 311-324.",
        doi="10.1016/j.jasms.2003.10.019",
    ),
    "dus25": SourceReference(
        "Dusanter, S.; Holzinger, R.; Klein, F.; Salameh, T.; Jamar, M. "
        "Measurement guidelines for VOC analysis by PTR-MS. ACTRIS.",
        url="https://actris.eu/sites/default/files/inline-files/PTRMS%20SOP%20(April2025).pdf",
    ),
    "han95": SourceReference(
        "Hansel, A.; Jordan, A.; Holzinger, R.; Prazeller, P.; Vogel, W.; "
        "Lindinger, W. (1995). Proton transfer reaction mass spectrometry: "
        "on-line trace gas analysis at the ppb level. Int. J. Mass Spectrom. Ion "
        "Processes 149-150, 609-619.",
        doi="10.1016/0168-1176(95)04294-U",
    ),
    "pfe20": SourceReference(
        "Pfeifer, J.; Simon, M.; Heinritzi, M.; Piel, F.; Weitz, L.; Wang, D.; "
        "Granzin, M.; Muller, T.; Brakling, S.; Kirkby, J.; Curtius, J.; "
        "Kurten, A. (2020). Measurement of ammonia, amines and iodine compounds "
        "using protonated water cluster chemical ionization mass spectrometry. "
        "Atmos. Meas. Tech. 13, 2501-2522.",
        doi="10.5194/amt-13-2501-2020",
    ),
    "ska04": SourceReference(
        "Skalny, J. D.; Mikoviny, T.; Matejcik, S.; Mason, N. J. (2004). An "
        "analysis of mass spectrometric study of negative ions extracted from "
        "negative corona discharge in air. Int. J. Mass Spectrom. 233, 317-324.",
        doi="10.1016/j.ijms.2004.01.012",
    ),
    "ska07": SourceReference(
        "Skalny, J. D.; Horvath, G.; Mason, N. J. (2007). Mass spectrometric "
        "analysis of small negative ions (e/m < 100) produced by Trichel pulse "
        "negative corona discharge fed by ozonised air. J. Optoelectron. Adv. "
        "Mater. 9, 887-893.",
        url="https://oro.open.ac.uk/11208/",
    ),
    "nag06": SourceReference(
        "Nagato, K.; Matsui, Y.; Miyata, T.; Yamauchi, T. (2006). An analysis of "
        "the evolution of negative ions produced by a corona ionizer in air. "
        "Int. J. Mass Spectrom. 248, 142-147.",
        doi="10.1016/j.ijms.2005.12.001",
    ),
    "sek11": SourceReference(
        "Sekimoto, K.; Takayama, M. (2011). Observations of different core "
        "water cluster ions Y-(H2O)n (Y = O2, HOx, NOx, COx) and magic number "
        "in atmospheric pressure negative corona discharge mass spectrometry. "
        "J. Mass Spectrom. 46, 50-60.",
        doi="10.1002/jms.1870",
    ),
    "sek12": SourceReference(
        "Sekimoto, K.; Sakai, M.; Takayama, M. (2012). Specific interaction "
        "between negative atmospheric ions and organic compounds in atmospheric "
        "pressure corona discharge ionization mass spectrometry. J. Am. Soc. "
        "Mass Spectrom. 23, 1109-1119.",
        doi="10.1007/s13361-012-0363-5",
    ),
    "fuj23": SourceReference(
        "Fujishima, S.; Sekimoto, K.; Takayama, M. (2023). Identification of "
        "negative ion at m/z 20 produced by atmospheric pressure corona "
        "discharge ionization under ambient air. Mass Spectrom. 12, A0124.",
        doi="10.5702/massspectrometry.A0124",
    ),
    "asa23": SourceReference(
        'Asakawa, D.; Hiraoka, K. (2023). Comments on "Identification of '
        "negative ion at m/z 20 produced by atmospheric pressure corona "
        'discharge ionization under ambient air". Mass Spectrom. 12, A0140.',
        doi="10.5702/massspectrometry.A0140",
    ),
    "tak26": SourceReference(
        'Takayama, M. (2026). Reply to comment on "Identification of negative '
        "ion at m/z 20 produced by atmospheric pressure corona discharge "
        'ionization under ambient air". Mass Spectrom. 15, A0185.',
        doi="10.5702/massspectrometry.A0185",
    ),
    "mat23": SourceReference(
        "Matas, E.; Moravsky, L.; Ilbeigi, V.; Matejcik, S. (2023). Negative "
        "atmospheric pressure chemical ionisation of NO2 by O2-.CO2.(H2O)n "
        "studied by ion mobility spectrometry. Eur. Phys. J. D 77, 21.",
        doi="10.1140/epjd/s10053-023-00603-x",
    ),
    "ewi09": SourceReference(
        "Ewing, R. G.; Waltman, M. J. (2009). Mechanisms for negative reactant "
        "ion formation in an atmospheric pressure corona discharge. Int. J. Ion "
        "Mobil. Spectrom. 12, 65-72.",
        doi="10.1007/s12127-009-0019-8",
    ),
    "jok12": SourceReference(
        "Jokinen, T.; Sipila, M.; Junninen, H.; Ehn, M.; Lonn, G.; Hakala, J.; "
        "Petaja, T.; Mauldin, R. L.; Kulmala, M.; Worsnop, D. R. (2012). "
        "Atmospheric sulphuric acid and neutral cluster measurements using "
        "CI-APi-TOF. Atmos. Chem. Phys. 12, 4117-4125.",
        doi="10.5194/acp-12-4117-2012",
    ),
    "zha26": SourceReference(
        "Zhang, J.; Zhang, Y.; Koskenvaara, H.; Zhao, J.; Ehn, M. (2026). "
        "Gas-phase products from nitrate radical oxidation of five "
        "monoterpenes: insights from free-jet flow-tube experiments. Atmos. "
        "Chem. Phys. 26, 3933-3949.",
        doi="10.5194/acp-26-3933-2026",
    ),
    "san16": SourceReference(
        "Sanchez, J.; Tanner, D. J.; Chen, D.; Huey, L. G.; Ng, N. L. (2016). A "
        "new technique for the direct detection of HO2 radicals using bromide "
        "chemical ionization mass spectrometry (Br-CIMS): initial "
        "characterization. Atmos. Meas. Tech. 9, 3851-3861.",
        doi="10.5194/amt-9-3851-2016",
    ),
    "ris19": SourceReference(
        "Rissanen, M. P.; Mikkila, J.; Iyer, S.; Hakala, J. (2019). Multi-scheme "
        "chemical ionization inlet (MION) for fast switching of reagent ion "
        "chemistry in atmospheric pressure chemical ionization mass "
        "spectrometry (CIMS) applications. Atmos. Meas. Tech. 12, 6635-6646.",
        doi="10.5194/amt-12-6635-2019",
    ),
    "wa21": SourceReference(
        "Wang, M. et al. (2021). Measurement of iodine species and sulfuric acid "
        "using bromide chemical ionization mass spectrometers. Atmos. Meas. "
        "Tech. 14, 4187-4202.",
        doi="10.5194/amt-14-4187-2021",
    ),
    "dor21": SourceReference(
        "Dorich, R.; Eger, P.; Lelieveld, J.; Crowley, J. N. (2021). Iodide CIMS "
        "and m/z 62: the detection of HNO3 as NO3- in the presence of PAN, "
        "peroxyacetic acid and ozone. Atmos. Meas. Tech. 14, 5319-5332.",
        doi="10.5194/amt-14-5319-2021",
    ),
    "gom22": SourceReference(
        "Gomez Martin, J. C.; Lewis, T. R.; James, A. D.; Saiz-Lopez, A.; "
        "Plane, J. M. C. (2022). Insights into the chemistry of iodine new "
        "particle formation: the role of iodine oxides and the source of iodic "
        "acid. J. Am. Chem. Soc. 144, 9240-9253.",
        doi="10.1021/jacs.1c12957",
    ),
    "shc24": SourceReference(
        "Shcherbinin, A.; Finkenzeller, H.; Mikkila, J.; Kontro, J.; Vinkvist, N.; "
        "Kangasluoma, J.; Rissanen, M. (2024). From hydrocarbons to highly "
        "functionalized molecules in a single measurement: comprehensive "
        "analysis of complex gas mixtures by multi-pressure chemical "
        "ionization mass spectrometry. Anal. Chem. 96, 19926-19932.",
        doi="10.1021/acs.analchem.4c03859",
    ),
    "shc25": SourceReference(
        "Shcherbinin, A. et al. (2025). Uronium from X-ray-desorbed urea enables "
        "sustainable ultrasensitive detection of amines and semivolatiles. "
        "Anal. Chem. 97, 21282-21290.",
        doi="10.1021/acs.analchem.5c02239",
    ),
    "easyic": SourceReference(
        "Thermo Fisher Scientific (2018). EASY-ETD and EASY-IC Ion Sources User "
        "Guide, for the Orbitrap Tribrid series mass spectrometer. Document "
        "80000-97515, Revision A.",
        url="https://documents.thermofisher.com/TFS-Assets/CMD/manuals/man-80000-97515-easy-etd-ic-ion-sources-user-man8000097515-en.pdf",
    ),
    "leb23": SourceReference(
        "Leborgne, C.; Meudec, E.; Sommerer, N.; Masson, G.; Mouret, J.-R.; "
        "Cheynier, V. (2023). Untargeted metabolomics approach using UHPLC-HRMS "
        "to unravel the impact of fermentation on color and phenolic "
        "composition of rose wines. Molecules 28, 5748.",
        doi="10.3390/molecules28155748",
    ),
    "ash26": SourceReference(
        "Ashbacher, S. M.; Xie, D.-Y.; Muddiman, D. C. (2026). Differentiation of "
        "wild-type and PAP1-overexpressing tobacco by volatile organic compound "
        "profiling using TP-SESI mass spectrometry. Anal. Bioanal. Chem. 418, "
        "5577-5585.",
        doi="10.1007/s00216-026-06630-y",
    ),
    "mar16": SourceReference(
        "Martens, J.; Berden, G.; Oomens, J. (2016). Structures of fluoranthene "
        "reagent anions used in electron transfer dissociation and proton "
        "transfer reaction tandem mass spectrometry. Anal. Chem. 88, 6126-6129.",
        doi="10.1021/acs.analchem.6b01483",
    ),
    "wes18": SourceReference(
        "West, B.; Rodriguez Castillo, S.; Sit, A.; Mohamad, S.; Lowe, B.; "
        "Joblin, C.; Bodi, A.; Mayer, P. M. (2018). Unimolecular reaction "
        "energies for polycyclic aromatic hydrocarbon ions. Phys. Chem. Chem. "
        "Phys. 20, 7195-7205.",
        doi="10.1039/c7cp07369k",
    ),
    "nist": SourceReference(
        "Linstrom, P. J.; Mallard, W. G. (eds.). NIST Chemistry WebBook, NIST "
        "Standard Reference Database Number 69; electron-ionization spectra "
        "from the NIST Mass Spectrometry Data Center.",
        doi="10.18434/T4D303",
    ),
    "wan03": SourceReference(
        "Wang, T.; Spanel, P.; Smith, D. (2003). Selected ion flow tube, SIFT, "
        "studies of the reactions of H3O+, NO+ and O2+ with eleven C10H16 "
        "monoterpenes. Int. J. Mass Spectrom. 228, 117-126.",
        doi="10.1016/S1387-3806(03)00271-9",
    ),
    "scn03": SourceReference(
        "Schoon, N.; Amelynck, C.; Vereecken, L.; Arijs, E. (2003). A selected "
        "ion flow tube study of the reactions of H3O+, NO+ and O2+ with a series "
        "of monoterpenes. Int. J. Mass Spectrom. 229, 231-240.",
        doi="10.1016/S1387-3806(03)00343-9",
    ),
    "mat17": SourceReference(
        "Materic, D.; Lanza, M.; Sulzer, P.; Herbig, J.; Bruhn, D.; Gauci, V.; "
        "Mason, N.; Turner, C. (2017). Selective reagent ion-time of flight-mass "
        "spectrometry study of six common monoterpenes. Int. J. Mass Spectrom. "
        "421, 40-50.",
        doi="10.1016/j.ijms.2017.06.003",
    ),
    "tan03": SourceReference(
        "Tani, A.; Hayward, S.; Hewitt, C. N. (2003). Measurement of "
        "monoterpenes and related compounds by proton transfer reaction-mass "
        "spectrometry (PTR-MS). Int. J. Mass Spectrom. 223-224, 561-578.",
        doi="10.1016/S1387-3806(02)00880-1",
    ),
    "kar18": SourceReference(
        "Kari, E.; Miettinen, P.; Yli-Pirila, P.; Virtanen, A.; Faiola, C. L. "
        "(2018). PTR-ToF-MS product ion distributions and humidity-dependence "
        "of biogenic volatile organic compounds. Int. J. Mass Spectrom. 430, "
        "87-97.",
        doi="10.1016/j.ijms.2018.05.003",
    ),
    "ish26": SourceReference(
        "Ishihara, R.; Fukuyama, D.; Sekimoto, K. (2026). Interpretation of "
        "alpha-pinene mass spectra in APCI-like ambient mass spectrometry using "
        "GC-coupled atmospheric pressure corona discharge ionization. Mass "
        "Spectrom. 15, A0190.",
        doi="10.5702/massspectrometry.A0190",
    ),
}


@dataclass(frozen=True)
class ReagentCluster:
    """One ion the source makes on its own, and may claim a peak.

    :param formula: The ion's elemental composition, charge excluded.
    :param charge: The ion's charge, ``+1`` or ``-1``.
    :param label: How the ion is written in a row's provenance.
    :param kind: Which part of the grammar produced it, for the reader of a
        claim rather than for the matching.
    :param anchor: Whether this ion may be used to calibrate the pass. True for
        the base ions of a source - the bare halide monomer and dimer, the
        protonated urea monomer and dimer, the nitrate core and its first
        rung - which are the brightest things the source makes and which
        nothing else shares a mass with. Those two properties are what let them be found in a wide
        window and then say where this spectrum puts the reagent's masses; a
        rung that is neither bright nor unambiguous must not.
    :param family: Where the ion comes from (``FAMILY_*``).
    :param references: Keys into :data:`SOURCE_ION_REFERENCES`: the works that
        name this ion, so a claim can be checked against a paper rather than
        against this table's word. A work is cited for an ion only where it
        names that ion, not its family.
    :param observed: Where no work names the ion: what shows it. Said in so
        many words rather than covered by a citation that does not name it.
    """

    formula: str
    charge: int
    label: str
    kind: str = KIND_CLUSTER
    anchor: bool = False
    family: str = FAMILY_REAGENT
    references: tuple[str, ...] = ()
    observed: str = ""

    @property
    def mz(self) -> float:
        """The ion's m/z."""
        return ion_mz(self.formula, self.charge)


def _ion_formula(*parts: tuple[str, int]) -> str:
    """Sum ``(formula, multiplier)`` parts into one Hill-ordered formula.

    Composed rather than written out so a labelled reagent's label follows into
    every cluster built from it, the way it already does for the probes: the
    15N-nitrate profile's ladder is ``^NO3``, ``H^N2O6``, ``H2^N3O9`` without a
    second table to keep in step with the first.
    """
    counts = parse_composition("")
    for formula, multiplier in parts:
        counts += parse_composition(formula, multiplier)
    return to_hill_notation(dict(counts))


def _rung(
    formula: str,
    charge: int,
    label: str,
    kind: str,
    *references: str,
    anchor: bool = False,
    observed: str = "",
) -> ReagentCluster:
    """A rung of a reagent's own ladder, with the works that name it.

    :param formula: The ion's composition, in any element order.
    :param references: The works that name it.
    :param observed: Where no work names it: what shows it.
    """
    return ReagentCluster(
        _ion_formula((formula, 1)),
        charge,
        label,
        kind,
        anchor=anchor,
        family=FAMILY_REAGENT,
        references=references,
        observed=observed,
    )


def _seen(evidence: str) -> str:
    """What shows a rung no work found names, said in so many words."""
    return f"no work found names it; {evidence}"


#: How many waters to put on the nitrate core: its mono- and dihydrate, the
#: rungs the corona-discharge studies name beside the bare anion.
DEFAULT_MAX_NEUTRAL = 2


#: The bromide reagent's ladder. The bare monomer and dimer anchor the pass:
#: they are the two brightest ions a bromide source makes and no analyte shares
#: their mass. Both parities of the bare ladder are real ions - the trimer is a
#: closed-shell anion, the dimer a radical one - and both are pure reagent, so
#: the closed-shell preference a same-ion family is ranked by (see
#: ``heuristic_filter``) has no bearing here.
#:
#: The monomer, its hydrates and the dimer are named in the literature. The
#: trimer, the monomer's cluster with the HBr the source sheds, and the two
#: oxides are named by no work found; Mascope's bromide test spectra - fifteen
#: files, in three sets - show them, and each says how often.
_BROMIDE_LADDER: tuple[ReagentCluster, ...] = (
    _rung("Br", -1, "[Br]-", KIND_CLUSTER, "san16", "ris19", "wa21", anchor=True),
    _rung("BrH2O", -1, "[Br+H2O]-", KIND_ADDUCT, "san16", "ris19", "wa21"),
    _rung("BrH4O2", -1, "[Br+2xH2O]-", KIND_ADDUCT, "ris19"),
    _rung("Br2", -1, "[Br2]-", KIND_CLUSTER, "san16", anchor=True),
    _rung(
        "Br3",
        -1,
        "[Br3]-",
        KIND_CLUSTER,
        observed=_seen(
            "Mascope's bromide test spectra show it in all 15 files, at 1.5 to "
            "35% of the base peak"
        ),
    ),
    _rung(
        "Br2H",
        -1,
        "[Br+HBr]-",
        KIND_ADDUCT,
        observed=_seen(
            "Mascope's bromide test spectra show it in every file of one of "
            "their three sets, at 0.06% of the base peak"
        ),
    ),
    _rung(
        "BrO",
        -1,
        "[BrO]-",
        KIND_OXIDE,
        observed=_seen(
            "Mascope's bromide test spectra show it in 11 of their 15 files, at "
            "0.01 to 1.8% of the base peak"
        ),
    ),
    _rung(
        "BrO3",
        -1,
        "[BrO3]-",
        KIND_OXIDE,
        observed=_seen(
            "Mascope's bromide test spectra show it in every file of one of "
            "their three sets, at 0.5 to 0.6% of the base peak"
        ),
    ),
)

#: The iodide reagent's ladder: the bare ion, its hydrate, and the dimer and
#: trimer, the dimer anchoring beside the monomer as bromide's does. The iodine
#: oxides are not here - they are the iodine oxyacids' analyte channel - and
#: neither are HOI2- and I2NO2-, which are the [M+I]- readings of HOI and INO2,
#: reactive iodine species that vary in time and are exactly what an iodide
#: deployment is measuring.
_IODIDE_LADDER: tuple[ReagentCluster, ...] = (
    _rung("I", -1, "[I]-", KIND_CLUSTER, "dor21", anchor=True),
    _rung("H2IO", -1, "[I+H2O]-", KIND_ADDUCT, "dor21"),
    _rung("I2", -1, "[I2]-", KIND_CLUSTER, "gom22", anchor=True),
    _rung("I3", -1, "[I3]-", KIND_CLUSTER, "gom22"),
)


def _nitrate_clusters(
    reagent: str,
    *,
    max_n: int = 2,
    max_neutral: int = DEFAULT_MAX_NEUTRAL,
) -> tuple[ReagentCluster, ...]:
    """The nitrate reagent's own ladder: ``(HNO3)n.NO3-`` and its hydrates.

    The acid the ladder is built from is the reagent's own, so a labelled
    reagent's ladder is labelled throughout - which is what makes the 15N
    profile's rungs land 0.997 Da per nitrogen above the unlabelled ones.

    :param reagent: The reagent ion's composition (``"NO3"``, ``"^NO3"``).
    :param max_n: How many acid molecules to hang on the core.
    :param max_neutral: Copies of water on the bare core.
    """
    acid = f"H{reagent}"
    clusters = [
        ReagentCluster(
            _ion_formula((reagent, 1)), -1, f"[{reagent}]-", KIND_CLUSTER, anchor=True
        )
    ]
    for n in range(1, max_n + 1):
        copies = f"{n}x" if n > 1 else ""
        clusters.append(
            ReagentCluster(
                _ion_formula((reagent, 1), (acid, n)),
                -1,
                f"[{reagent}+{copies}{acid}]-",
                KIND_CLUSTER,
                # The core and its first acid rung are the ions a nitrate source
                # is loudest in; higher rungs decluster away and are not certain
                # enough to calibrate on.
                anchor=n == 1,
            )
        )
    for k in range(1, max_neutral + 1):
        copies = f"{k}x" if k > 1 else ""
        clusters.append(
            ReagentCluster(
                _ion_formula((reagent, 1), ("H2O", k)),
                -1,
                f"[{reagent}+{copies}H2O]-",
                KIND_ADDUCT,
            )
        )
    return tuple(clusters)


#: The protonated urea ladder, the dominant ions of a uronium source. The
#: monomer and dimer are the base peak of a uronium spectrum and the ion beside
#: it, and anchor the pass; the trimer is named by no work found, and Mascope's
#: uronium test spectra show it. No rung carries an ammonium: the monomer's is
#: ammonia's reading, the section comment says why, and the multimers' are named
#: by no work found and absent from the test spectra.
_UREA_LADDER: tuple[ReagentCluster, ...] = (
    _rung("CH5N2O", 1, "[CH4N2O+H]+", KIND_CLUSTER, "shc25", anchor=True),
    _rung("C2H9N4O2", 1, "[(CH4N2O)2+H]+", KIND_CLUSTER, "shc25", anchor=True),
    _rung(
        "C3H13N6O3",
        1,
        "[(CH4N2O)3+H]+",
        KIND_CLUSTER,
        observed=_seen(
            "Mascope's uronium test spectra show it in every file of one of "
            "their two sets, at 0.08% of the base peak"
        ),
    ),
)


def _fluoranthene_ladder() -> tuple[ReagentCluster, ...]:
    """The charge-transfer source's own ions: the fluoranthene beam.

    The reagent cation ``[C16H10]+`` at m/z 202.078 anchors the pass - the
    brightest thing an EASY-IC source makes in a window that reaches it, and
    an aromatic no chamber or ambient sample shows at that height. Around it
    sit what the beam does to itself: the hydrogen- and dihydrogen-loss ions,
    the protonated ion and the ions two and three hydrogens heavier that a
    humid beam carries beside it, and the acetylene-loss fragments, the minor
    channels of the cation's own fragmentation. The dimer is not here: no work
    found names it, and no test spectrum's window reaches it.

    Every atom here is the reagent's own. The ions the discharge makes of the
    air are the air family's (:data:`_AIR_CATIONS`).
    """
    return (
        ReagentCluster("C16H10", 1, "[C16H10]+", KIND_CLUSTER, anchor=True),
        ReagentCluster("C16H9", 1, "[C16H10-H]+", KIND_FRAGMENT),
        ReagentCluster("C16H8", 1, "[C16H10-H2]+", KIND_FRAGMENT),
        ReagentCluster("C16H11", 1, "[C16H10+H]+", KIND_ADDUCT),
        ReagentCluster("C16H12", 1, "[C16H12]+", KIND_ADDUCT),
        ReagentCluster("C16H13", 1, "[C16H13]+", KIND_ADDUCT),
        ReagentCluster("C14H8", 1, "[C16H10-C2H2]+", KIND_FRAGMENT),
        ReagentCluster("C12H6", 1, "[C16H10-2xC2H2]+", KIND_FRAGMENT),
    )


#: The negative source's reagent: the fluoranthene radical anion, the negative
#: EASY-IC lock mass at m/z 202.079. The anions it goes on to make from air are
#: the air family's (:data:`_AIR_ANIONS`), not the reagent's own ladder.
_FLUORANTHENE_ANION: tuple[ReagentCluster, ...] = (
    ReagentCluster("C16H10", -1, "[C16H10]-", KIND_CLUSTER, anchor=True),
)


# --- the air's ions -----------------------------------------------------------
#
# What an ionizer makes of the gas it sits in before any reagent or analyte is
# involved: nitrogen, oxygen, water and carbon dioxide, and the nitrogen oxides
# a discharge makes of them. On a charge-transfer source they are the source; on
# a reagent source they are its background, beside the reagent's own ladder.
# The library's rule, restated for the air: every atom comes from the bulk gas
# the source ionizes or what its discharge makes of it, not from a trace species
# in the sample. Nitrate is the one ion both could make, and on a discharge's
# source it is the charge's terminal sink whatever the sample holds, so a
# nitrate peak there is the discharge's before it is anyone's nitric acid.
#
# Only ions a work names are here. A discharge makes more - bicarbonate's
# hydrate among them - that no work found names, and those are left to the
# stages. What is deliberately not here, each because a work reads the ion as a
# trace species': the ammonium ladder NH4+(H2O)n, which is ammonia protonated
# and how a water-cluster source measures it; HSO4-, sulfuric acid deprotonated
# and the analyte nitrate CIMS was built for; and formate, acetate and every
# other organic acid's anion.


def _ion(formula: str, charge: int, label: str, *references: str) -> ReagentCluster:
    """An air ion, with the works that name it."""
    kind = KIND_ADDUCT if "H2O]" in label else KIND_CLUSTER
    return ReagentCluster(
        _ion_formula((formula, 1)),
        charge,
        label,
        kind,
        family=FAMILY_AIR,
        references=references,
    )


#: The positive ions of air: a discharge's cations and protonated water. N2+ is
#: a discharge's primary ion and N4+ what it becomes in nitrogen, N3+ its
#: companion; O2+ and NO+ are where the charge passes next, to the molecules
#: that give an electron up most easily; NO2+ is the discharge's nitrogen
#: oxides charged. Water takes the proton from all of them, and the protonated
#: water ladder is what a humid source's positive ions end as.
_AIR_CATIONS: tuple[ReagentCluster, ...] = (
    _ion("N2", 1, "[N2]+.", "good70", "kol04", "shc24"),
    _ion("N3", 1, "[N3]+", "good70", "kol04"),
    _ion("N4", 1, "[N4]+.", "good70", "kol04"),
    _ion("O2", 1, "[O2]+.", "good70b", "shc24", "dus25"),
    _ion("H2O3", 1, "[O2+H2O]+", "sha66"),
    _ion("NO", 1, "[NO]+", "sha66", "sab12", "sab13", "dus25"),
    _ion("H2NO2", 1, "[NO+H2O]+", "sha66", "sab13"),
    _ion("NO2", 1, "[NO2]+", "sha66", "shc24"),
    _ion("H2NO3", 1, "[NO2+H2O]+", "sha66"),
    _ion("H3O", 1, "[H3O]+", "good70", "han95", "kol04", "dus25"),
    _ion("H5O2", 1, "[H3O+H2O]+", "good70", "pfe20", "shc24", "dus25"),
    _ion("H7O3", 1, "[H3O+2xH2O]+", "good70", "sha66", "pfe20"),
    _ion("H9O4", 1, "[H3O+3xH2O]+", "good70", "sha66", "pfe20"),
    _ion("H11O5", 1, "[H3O+4xH2O]+", "good70", "sha66"),
)

#: The negative ions of air: the terminal ions of a corona discharge in humid
#: air, where carbonate and nitrate are the sinks the charge ends in -
#: hydroxide, superoxide, ozonide, carbonate and bicarbonate, nitrite and
#: nitrate, their hydrates, superoxide carrying carbon dioxide, and nitrate
#: clustered with the nitric acid the discharge makes. Hydroxide's water
#: clusters are read in one ambient corona source and argued in another to
#: turn into bicarbonate before they could reach the analyser; named, they are
#: claimed where a spectrum shows them.
_AIR_ANIONS: tuple[ReagentCluster, ...] = (
    _ion("HO", -1, "[OH]-", "fuj23"),
    _ion("H3O2", -1, "[OH+H2O]-", "fuj23", "sek11", "tak26"),
    _ion("H5O3", -1, "[OH+2xH2O]-", "fuj23", "sek11", "tak26"),
    _ion("H7O4", -1, "[OH+3xH2O]-", "fuj23", "sek11", "tak26"),
    _ion("O2", -1, "[O2]-", "ska04", "ska07", "sek12", "asa23"),
    _ion("H2O3", -1, "[O2+H2O]-", "sek11"),
    _ion("O3", -1, "[O3]-", "sha69", "asa23"),
    _ion("H2O4", -1, "[O3+H2O]-", "sha69"),
    _ion("CO4", -1, "[O2+CO2]-", "mat23"),
    _ion("CO3", -1, "[CO3]-", "ska04", "ska07", "sha69", "asa23"),
    _ion("CH2O4", -1, "[CO3+H2O]-", "ska04", "ska07", "sha69", "tak26"),
    _ion("CH4O5", -1, "[CO3+2xH2O]-", "ska07", "tak26"),
    _ion("CHO3", -1, "[HCO3]-", "nag06", "sek12", "asa23"),
    _ion("NO2", -1, "[NO2]-", "ska07", "sek12", "ewi09", "mat23"),
    _ion("H2NO3", -1, "[NO2+H2O]-", "sek11", "mat23"),
    _ion("H4NO4", -1, "[NO2+2xH2O]-", "mat23"),
    _ion("NO3", -1, "[NO3]-", "ska04", "nag06", "sek12", "ewi09"),
    _ion("H2NO4", -1, "[NO3+H2O]-", "ska04", "ska07", "tak26"),
    _ion("H4NO5", -1, "[NO3+2xH2O]-", "ska04", "ska07", "tak26"),
    _ion("HN2O6", -1, "[NO3+HNO3]-", "nag06", "sek12", "ewi09"),
    _ion("CH2NO6", -1, "[HCO3+HNO3]-", "nag06"),
)


def _relabelled(formula: str, element: str, isotope: str) -> str:
    """The composition with every atom of one element written as its label."""
    counts = dict(parse_composition(formula))
    if counts.get(element):
        counts[isotope] = counts.get(isotope, 0) + counts.pop(element)
    return to_hill_notation(counts)


def _library(
    *groups: tuple[ReagentCluster, ...], label_isotope: tuple[str, str] | None = None
) -> tuple[ReagentCluster, ...]:
    """One profile's library: its families, each ion once, the first entry winning.

    An ion two families name is the first family's: nitrate on a nitrate source
    is the reagent, not the air's. On a labelled reagent's source an air ion is
    left out where the reagent's own ladder already holds it with its label -
    the 15N-nitrate source's plain nitrate is first the reagent's 14N
    remainder, which the ladder's envelope claims as that where its height
    fits the label's purity.

    :param groups: The families, in the order they take an ion.
    :param label_isotope: ``(element, label)`` for a labelled reagent
        (``("N", "^N")``).
    """
    taken: set[str] = set()
    library: list[ReagentCluster] = []
    for group in groups:
        for cluster in group:
            if cluster.formula in taken:
                continue
            if label_isotope is not None and cluster.family == FAMILY_AIR:
                if _relabelled(cluster.formula, *label_isotope) in taken:
                    continue
            taken.add(cluster.formula)
            library.append(cluster)
    return tuple(library)


def _cite(
    clusters: Iterable[ReagentCluster],
    family: str,
    named: Mapping[str, tuple[str, ...]],
) -> tuple[ReagentCluster, ...]:
    """The clusters as one family, each carrying the works that name it.

    Every ion a builder makes has to be named: one missing from ``named`` is a
    ``KeyError`` when this module loads, not an uncited claim.

    :param clusters: The ions a builder made.
    :param family: Their family.
    :param named: The works that name each ion, by its label.
    """
    return tuple(
        replace(cluster, family=family, references=named[cluster.label])
        for cluster in clusters
    )


_NITRATE_NAMED = {
    "[NO3]-": ("jok12",),
    "[NO3+HNO3]-": ("jok12",),
    "[NO3+2xHNO3]-": ("jok12",),
    "[NO3+H2O]-": ("ska04", "ska07"),
    "[NO3+2xH2O]-": ("ska04", "ska07"),
}
# The labelled reagent's ladder is the same ions with its nitrogen labelled;
# the 15N source's own paper names its core and first rung.
_NITRATE_15N_NAMED = {
    "[^NO3]-": ("jok12", "zha26"),
    "[^NO3+H^NO3]-": ("jok12", "zha26"),
    "[^NO3+2xH^NO3]-": ("jok12",),
    "[^NO3+H2O]-": ("ska04", "ska07"),
    "[^NO3+2xH2O]-": ("ska04", "ska07"),
}
_FLUORANTHENE_NAMED = {
    "[C16H10]+": ("easyic", "shc24", "ash26", "leb23"),
    "[C16H10-H]+": ("shc24", "wes18", "nist"),
    "[C16H10-H2]+": ("nist",),
    "[C16H10+H]+": ("shc24",),
    "[C16H12]+": ("shc24",),
    "[C16H13]+": ("shc24",),
    "[C16H10-C2H2]+": ("nist",),
    "[C16H10-2xC2H2]+": ("nist",),
    "[C16H10]-": ("easyic", "mar16", "leb23"),
}


#: The source-ion library per profile, keyed the way :data:`SECONDARY_CHANNELS`
#: is. A profile with no single reagent species - an electrospray - has no
#: library, which is not an omission: there is no one carrier whose clusters
#: could be enumerated, and no discharge whose air ions could be named.
REAGENT_CLUSTERS: dict[str, tuple[ReagentCluster, ...]] = {
    "BR": _library(_BROMIDE_LADDER, _AIR_ANIONS),
    "IODIDE": _library(_IODIDE_LADDER, _AIR_ANIONS),
    "NO3": _library(
        _cite(_nitrate_clusters("NO3"), FAMILY_REAGENT, _NITRATE_NAMED),
        _AIR_ANIONS,
    ),
    "NO3_15N": _library(
        _cite(_nitrate_clusters("^NO3"), FAMILY_REAGENT, _NITRATE_15N_NAMED),
        _AIR_ANIONS,
        label_isotope=("N", "^N"),
    ),
    "UR": _library(_UREA_LADDER, _AIR_CATIONS),
    "EASYIC_POS": _library(
        _cite(_fluoranthene_ladder(), FAMILY_CALIBRANT, _FLUORANTHENE_NAMED),
        _AIR_CATIONS,
    ),
    "EASYIC_NEG": _library(
        _cite(_FLUORANTHENE_ANION, FAMILY_CALIBRANT, _FLUORANTHENE_NAMED),
        _AIR_ANIONS,
    ),
}


def reagent_library(profile_name: str) -> tuple[ReagentCluster, ...]:
    """The reagent ions a profile's source makes on its own.

    :param profile_name: The profile's name.
    :return: Its cluster library, empty when the profile has no reagent.
    """
    return REAGENT_CLUSTERS.get(profile_name, ())


#: Window the ANCHOR ions are looked for in, in ppm. The same width and the
#: same reason as :data:`DEFAULT_CHANNEL_MATCH_PPM`: an anchor is being detected,
#: not assigned, and a spectrum whose calibration sits ten ppm out still has to
#: be able to find its own reagent.
#:
#: This is the only wide window in the pass, and it is wide only for the two or
#: three ions that cannot be anything else. Everything the pass goes on to claim
#: is matched at the instrument's own precision against a mass the anchors have
#: corrected - see :func:`match_reagent_clusters`.
DEFAULT_ANCHOR_PPM = DEFAULT_CHANNEL_MATCH_PPM

#: Intensity a peak must reach, relative to the base peak, to be claimed as a
#: reagent ion in its own right. The same floor the channel probes use, for the
#: same reason: a reagent ion is one of the brightest things in the spectrum, so
#: a trace at the noise floor sitting on a reagent mass is a coincidence rather
#: than the ion. Isotopologues are exempt - their evidence is the parent's
#: envelope, which is a stronger statement than a height threshold.
DEFAULT_REAGENT_MIN_RELATIVE_INTENSITY = DEFAULT_CHANNEL_MIN_RELATIVE_INTENSITY

#: Intensity an ANCHOR must reach, relative to the base peak - an order of
#: magnitude above the floor for an ordinary claim, because an anchor does more
#: than claim its own peak: it moves every other mass in the pass.
#:
#: A stray peak inside an absent anchor's wide window would pull the median
#: offset and widen the tolerance by half its spread. Nothing real is lost by
#: refusing dim ones: measured across the eight gate sets, the urea monomer and
#: dimer run 28-100% of the base peak, ``Br-`` IS the base peak, ``Br2-`` 1.1-
#: 1.5%, and the nitrate core and its dimer 100% and 5-60%. An anchor is by
#: definition one of the loudest things a source makes.
DEFAULT_ANCHOR_MIN_RELATIVE_INTENSITY = 1e-3

#: Predicted isotopologues below this share of their parent are not looked for,
#: and the abundance the envelope itself is predicted down to.
#:
#: Set by the 18O line, which is where a floor that looks generous stops being
#: one: a two-oxygen ion's 18O isotopologue is 0.401% of its parent and a
#: one-oxygen ion's is 0.200%, so a 0.4% floor sits exactly on top of the first
#: and below the second. On the gate that mattered - the urea dimer's 18O line
#: is the 19th brightest peak of a set A sample at 7e4 counts, and once the
#: pre-pass claimed its parent and Stage A's target went with it, the untargeted
#: stage read the freed peak as ethylene glycol on the urea channel.
#:
#: What protects an analyte is the excess gate below, not this floor: a peak
#: taller than the envelope predicts is left alone whatever its predicted share.
#: So the floor is set low enough to SEE the lines a reagent ion really makes.
#: At a 2e7-count base peak a 0.1% isotopologue is still 2e4 counts.
DEFAULT_ISOTOPOLOGUE_MIN_RELATIVE = 1e-3

#: How far above its predicted height an isotopologue may be observed and still
#: be claimed. A reagent ion is bright, so its isotopologues are large in absolute
#: terms and worth claiming - but a peak several times TALLER than the envelope
#: predicts has an analyte co-eluting on top of it, and claiming that peak would
#: bury the analyte. Above this ratio the peak is left for the stages.
DEFAULT_ISOTOPOLOGUE_MAX_EXCESS = 3.0


@dataclass(frozen=True)
class ReagentHit:
    """One peak claimed by the reagent library.

    :param cluster: The library ion that claimed it.
    :param index: The peak's position in the spectrum arrays it was matched in.
    :param mz: The observed m/z.
    :param intensity: The observed intensity.
    :param mz_error_ppm: How far the observation sat from the ion's own exact
        mass - the raw error, not the residual after the anchor correction, so
        a reader sees what the spectrum did rather than what the pass assumed.
    :param isotope_label: Which isotopologue of the cluster this peak is;
        ``None`` on the cluster's own monoisotopic peak.
    :param parent_index: The peak the isotopologue belongs to; ``None`` on a
        monoisotopic hit.
    :param predicted_relative: The isotopologue's predicted height relative to the
        monoisotopic peak, recorded so a claim can be read back.
    """

    cluster: ReagentCluster
    index: int
    mz: float
    intensity: float
    mz_error_ppm: float
    isotope_label: str | None = None
    parent_index: int | None = None
    predicted_relative: float | None = None

    @property
    def is_isotopologue(self) -> bool:
        """Whether this peak is an isotopologue of a claimed cluster."""
        return self.parent_index is not None


@dataclass(frozen=True)
class ReagentCalibration:
    """Where this spectrum puts the reagent's own masses.

    :param offset_ppm: The offset to add to a library mass before looking for
        it, in ppm. Zero when no anchor was found, which is the conservative
        reading: an unanchored spectrum is searched at its nominal masses.
    :param tolerance_ppm: The window a corrected mass is claimed in.
    :param anchors: Label and observed error of each anchor that matched, so a
        run can say what the correction was derived from.
    """

    offset_ppm: float
    tolerance_ppm: float
    anchors: tuple[tuple[str, float], ...] = ()


def _brightest_in_window(
    mz_array: np.ndarray,
    intensity_array: np.ndarray,
    target: float,
    ppm: float,
    taken: set[int],
    floor: float = 0.0,
) -> int | None:
    """The brightest unclaimed peak within ``ppm`` of ``target``, if any."""
    window = target * ppm * 1e-6
    hits = [
        index
        for index in np.flatnonzero(np.abs(mz_array - target) <= window)
        if int(index) not in taken and float(intensity_array[index]) >= floor
    ]
    if not hits:
        return None
    return int(max(hits, key=lambda index: float(intensity_array[index])))


def calibrate_on_anchors(
    library: Sequence[ReagentCluster],
    mz_array: np.ndarray,
    intensity_array: np.ndarray,
    *,
    anchor_ppm: float,
    claim_ppm: float,
    floor: float,
) -> ReagentCalibration:
    """Where this spectrum puts the reagent ions it cannot be wrong about.

    The anchors are the library's own base ions - the bare halide clusters, the
    protonated urea monomer and dimer, the nitrate core and its first rung.
    They are the brightest ions a reagent source makes and nothing else has
    their mass, so they can be found in a wide window and then say where the
    calibration puts this mass range.

    Every other rung is claimed against a mass corrected by what they said,
    which is what separates a reagent ion from an analyte that merely lands
    nearby: a rung sitting 25 ppm off when the anchors sit at 4 ppm is not the
    reagent's, however alone it is in a wide window.

    :param library: The reagent ions.
    :param mz_array: The spectrum's m/z values.
    :param intensity_array: Their intensities.
    :param anchor_ppm: Window the anchors themselves are found in.
    :param claim_ppm: The instrument's own precision, the floor of the
        tolerance the correction is used with.
    :param floor: Intensity an anchor must reach.
    :return: The correction, with the anchors it came from.
    """
    offsets: list[float] = []
    anchors: list[tuple[str, float]] = []
    for cluster in library:
        if not cluster.anchor:
            continue
        target = cluster.mz
        index = _brightest_in_window(
            mz_array, intensity_array, target, anchor_ppm, set(), floor
        )
        if index is None:
            continue
        error = (float(mz_array[index]) - target) / target * 1e6
        offsets.append(error)
        anchors.append((cluster.label, error))
    if not offsets:
        # Nothing to correct against. Searching the nominal masses at the
        # instrument's precision is the conservative reading: a well-calibrated
        # spectrum whose anchors are simply below its first mass still gets its
        # ladder, and a drifted one claims nothing rather than guessing.
        return ReagentCalibration(0.0, claim_ppm)
    offset = float(np.median(offsets))
    # The anchors' own spread is the natural bound on how tightly a corrected
    # mass can be held: where the lock mass jitters across the bright low-mass
    # ions, the ladder inherits that jitter and a window narrower than it would
    # start dropping real rungs.
    spread = (max(offsets) - min(offsets)) / 2.0 if len(offsets) > 1 else 0.0
    return ReagentCalibration(offset, claim_ppm + spread, tuple(anchors))


def _isotopologue_hits(
    cluster: ReagentCluster,
    parent: ReagentHit,
    mz_array: np.ndarray,
    intensity_array: np.ndarray,
    taken: set[int],
    *,
    ppm: float,
    purity: float | None,
    min_relative: float,
    max_excess: float,
) -> list[ReagentHit]:
    """The isotopologue peaks of one claimed cluster.

    The envelope is predicted from the cluster's own known ion formula, so the
    heavy-halogen, 13C, 15N and 34S isotopologues all come out of one code path
    rather than a hand-written table of isotopologue combinations. It is then
    shifted onto the parent's OWN observed mass, so each isotopologue is looked
    for at the instrument's precision around where this ion actually sits rather
    than where its formula says it should.
    """
    # Predicted down to the floor this pass will actually look for, not to the
    # scoring path's 1%: a line the envelope omits is not a rounding error here,
    # it is a peak left in the residual for another stage to explain.
    predicted_mz, predicted_intensity, labels = predict_isotopes(
        cluster.formula, cluster.charge, purity, min_relative
    )
    if len(predicted_mz) == 0:
        return []
    predicted_mz = np.asarray(predicted_mz, dtype=float)
    predicted_intensity = np.asarray(predicted_intensity, dtype=float)
    monoisotopic = monoisotopic_index(predicted_mz, labels)
    base = float(predicted_intensity[monoisotopic])
    if base <= 0.0:
        return []
    # What the parent's own mass error was, applied to the whole envelope.
    shift = parent.mz - float(predicted_mz[monoisotopic])
    hits: list[ReagentHit] = []
    for position in range(len(predicted_mz)):
        if position == monoisotopic:
            continue
        relative = float(predicted_intensity[position]) / base
        if relative < min_relative:
            continue
        target = float(predicted_mz[position]) + shift
        index = _brightest_in_window(mz_array, intensity_array, target, ppm, taken)
        if index is None:
            continue
        observed = float(intensity_array[index])
        expected = relative * parent.intensity
        if expected > 0.0 and observed > max_excess * expected:
            # An analyte is sitting on this mass as well; leave the peak.
            continue
        taken.add(index)
        exact = float(predicted_mz[position])
        hits.append(
            ReagentHit(
                cluster=cluster,
                index=index,
                mz=float(mz_array[index]),
                intensity=observed,
                mz_error_ppm=(float(mz_array[index]) - exact) / exact * 1e6,
                isotope_label=labels[position] if position < len(labels) else None,
                parent_index=parent.index,
                predicted_relative=relative,
            )
        )
    return hits


def match_reagent_clusters(
    library: Sequence[ReagentCluster],
    mz: Sequence[float] | np.ndarray,
    intensity: Sequence[float] | np.ndarray,
    *,
    claim_ppm: float,
    anchor_ppm: float = DEFAULT_ANCHOR_PPM,
    purity: float | None = None,
    min_relative_intensity: float = DEFAULT_REAGENT_MIN_RELATIVE_INTENSITY,
    anchor_min_relative_intensity: float = DEFAULT_ANCHOR_MIN_RELATIVE_INTENSITY,
    isotopologue_min_relative: float = DEFAULT_ISOTOPOLOGUE_MIN_RELATIVE,
    isotopologue_max_excess: float = DEFAULT_ISOTOPOLOGUE_MAX_EXCESS,
) -> tuple[list[ReagentHit], ReagentCalibration]:
    """Which peaks of this spectrum the reagent library accounts for.

    Two passes, and the first is what makes the second safe. The anchors - the
    library's base ions, which nothing else can be - are found in a wide window
    and say where this spectrum puts the reagent's masses. Every claim is then
    made against a corrected mass at the instrument's own precision.

    The alternative, a single wide window, does not work and the gate says why:
    on a uronium set the peaks 21 to 28 ppm above the urea tetramer and pentamer
    masses are one ambient compound read through three channels, each within a
    ppm of its own exact mass, while that sample's real reagent ions sit within
    5 ppm of theirs. Being alone in a wide window is not evidence that a peak is
    the reagent; being on the reagent's mass, as the sample's own anchors define
    it, is.

    A peak is claimed at most once, and the precedence is deliberate: every
    monoisotopic claim is made before any isotopologue claim, so a peak sitting
    on a library ion's own mass is read as that ion rather than as some other
    cluster's isotopologue. Within each round the library is walked in mass
    order, so the outcome does not depend on the table's order. A cluster whose
    monoisotopic peak is absent claims no isotopologues at all: the evidence for an
    isotopologue is the parent it is an isotopologue of.

    :param library: The reagent ions, from :func:`reagent_library`.
    :param mz: The spectrum's m/z values.
    :param intensity: Their intensities, in the same order.
    :param claim_ppm: The instrument's m/z precision, the window a corrected
        mass is claimed in (the resolved profile's ``mz_precision_ppm``).
    :param anchor_ppm: Window the anchors are found in.
    :param purity: The labelled reagent's isotopic purity, passed to the
        envelope prediction; ``None`` for an unlabelled reagent.
    :param min_relative_intensity: Height floor for a claim in its own right,
        relative to the base peak.
    :param anchor_min_relative_intensity: The higher floor an anchor must clear,
        since an anchor moves every other mass in the pass rather than only
        claiming its own peak.
    :param isotopologue_min_relative: Predicted-height floor for an isotopologue.
    :param isotopologue_max_excess: How far above prediction an isotopologue may be
        observed and still be claimed.
    :return: The hits, monoisotopic before their isotopologues, and the correction
        the anchors gave.
    """
    mz_array = np.asarray(mz, dtype=float)
    intensity_array = np.asarray(intensity, dtype=float)
    if mz_array.size == 0 or not library:
        return [], ReagentCalibration(0.0, claim_ppm)

    base_peak = float(intensity_array.max()) if intensity_array.size else 0.0
    floor = base_peak * min_relative_intensity
    calibration = calibrate_on_anchors(
        library,
        mz_array,
        intensity_array,
        anchor_ppm=anchor_ppm,
        claim_ppm=claim_ppm,
        floor=base_peak * anchor_min_relative_intensity,
    )
    scale = 1.0 + calibration.offset_ppm * 1e-6

    taken: set[int] = set()
    parents: list[ReagentHit] = []
    for cluster in sorted(library, key=lambda item: item.mz):
        exact = cluster.mz
        index = _brightest_in_window(
            mz_array,
            intensity_array,
            exact * scale,
            calibration.tolerance_ppm,
            taken,
            floor,
        )
        if index is None:
            continue
        taken.add(index)
        observed = float(mz_array[index])
        parents.append(
            ReagentHit(
                cluster=cluster,
                index=index,
                mz=observed,
                intensity=float(intensity_array[index]),
                mz_error_ppm=(observed - exact) / exact * 1e6,
            )
        )

    hits: list[ReagentHit] = []
    for parent in parents:
        hits.append(parent)
        hits.extend(
            _isotopologue_hits(
                parent.cluster,
                parent,
                mz_array,
                intensity_array,
                taken,
                ppm=claim_ppm,
                purity=purity,
                min_relative=isotopologue_min_relative,
                max_excess=isotopologue_max_excess,
            )
        )
    return hits, calibration


# --- fragment ladders ---------------------------------------------------------
#
# The fourth family, and the one the pass cannot claim before the stages run. A
# source that ionizes by charge transfer or proton transfer breaks some of the
# analytes it ionizes, and the fragments are ions of ordinary composition: the
# grid reads them as molecules of their own, and a fragment read as a molecule
# is a partner of the wrong kind - it corroborates, doubts and outweighs other
# readings on the strength of an analyte that is not there.
#
# But a fragment's mass is often a component's ion too. C6H7+ is a monoterpene's
# fragment and protonated benzene; C5H7+ is one and isoprene less a hydride. So
# a fragment is claimed only on what a claim before the stages cannot see: its
# parent committed in the sample, the fragment no taller than the literature
# lets the parent make it, and no reading of the fragment's ion naming a
# molecule the sample shows on a peak of its own.


#: How far above the literature's ratio a fragment may stand to its parent and
#: still be claimed as the parent's: the allowance an isotopologue gets over its
#: prediction (:data:`DEFAULT_ISOTOPOLOGUE_MAX_EXCESS`), for the same reason. A
#: peak several times taller than the parent can make it carries something else.
DEFAULT_FRAGMENT_MAX_EXCESS = DEFAULT_ISOTOPOLOGUE_MAX_EXCESS


@dataclass(frozen=True)
class FragmentIon:
    """One ion a source breaks an analyte's ion into.

    :param formula: The fragment ion's composition, charge excluded.
    :param charge: Its charge.
    :param label: How a claim names it.
    :param literature_ratio: The largest height the references report for this
        fragment, relative to the parent's own ion. An upper bound: a softer
        source breaks the parent less, so the fragment may be far weaker than
        this and never much taller.
    :param references: Keys into :data:`SOURCE_ION_REFERENCES`: the works that
        name this fragment of this parent.
    """

    formula: str
    charge: int
    label: str
    literature_ratio: float
    references: tuple[str, ...] = ()

    @property
    def mz(self) -> float:
        """The fragment's m/z."""
        return ion_mz(self.formula, self.charge)

    @property
    def max_ratio(self) -> float:
        """The tallest the fragment may stand to its parent's ion and be claimed."""
        return self.literature_ratio * DEFAULT_FRAGMENT_MAX_EXCESS


@dataclass(frozen=True)
class FragmentLadder:
    """The fragments a source makes of one analyte.

    :param parent: The analyte's neutral formula. A ladder is keyed on the
        formula, so it reads for every isomer that formula holds, which is
        right where the isomers break alike, as the monoterpenes do.
    :param label: The analyte, as a claim names it.
    :param fragments: Its fragment ions.
    :param references: Keys into :data:`SOURCE_ION_REFERENCES`.
    """

    parent: str
    label: str
    fragments: tuple[FragmentIon, ...]
    references: tuple[str, ...]


#: Alpha-pinene's radical cation in the NIST electron-ionization spectrum, as a
#: share of that spectrum's base peak at m/z 93: the parent the monoterpene
#: ladder's ratios are read against.
_PINENE_EI_PARENT = 7.4


def _ei(share: float) -> float:
    """A fragment's height over alpha-pinene's radical cation in the NIST
    electron-ionization spectrum, from its share of the base peak."""
    return round(share / _PINENE_EI_PARENT, 2)


#: Alpha-pinene's C6H9+ over its protonated molecule, the fragment proton
#: transfer makes most of: 47.7% against 48.3% of the product ions at 130 Td,
#: the harder of the two drift fields it was measured at (Kari et al. 2018).
_PINENE_PTR_C6H9 = round(47.7 / 48.3, 2)

#: The monoterpenes' fragments, keyed on C10H16 since the isomers break alike.
#: Electron ionization is the hardest ionization a monoterpene meets, and its
#: spectrum bounds what a charge-transfer source makes of one: the ratios are
#: alpha-pinene's in the NIST spectrum, where the radical cation is 7.4% of the
#: base peak. Charge transfer from O2+ and NO+ gives the same ions (C7H9+,
#: C6H8+.). Proton transfer from H3O+ gives C6H9+ beside the protonated
#: molecule, measured in drift tubes and at atmospheric pressure alike, and its
#: ratio is proton transfer's.
#:
#: Deliberately not on the ladder, though the NIST spectrum has them strong:
#: C7H8+. (92), C7H7+ (91) and C8H9+ (105), which are toluene's radical cation,
#: toluene less a hydride and xylene less a hydride. A fragment the claim takes
#: must leave the molecule it could also be a peak of its own to show on, and
#: every ion toluene makes through a charge-transfer source's channels would
#: otherwise sit on the ladder. The methyl-loss ion C9H13+ (121) is read by the
#: methyl-loss channel instead, as pinene less a methyl, where pinene is shown.
_MONOTERPENE_LADDER = FragmentLadder(
    parent="C10H16",
    label="monoterpene",
    fragments=(
        FragmentIon(
            "C7H9", 1, "[C7H9]+", _ei(100.0), ("nist", "wan03", "mat17", "kar18")
        ),
        FragmentIon(
            "C6H9", 1, "[C6H9]+", _PINENE_PTR_C6H9, ("kar18", "tan03", "mat17", "ish26")
        ),
        FragmentIon("C6H8", 1, "[C6H8]+.", _ei(10.0), ("nist", "mat17")),
        FragmentIon("C6H7", 1, "[C6H7]+", _ei(29.8), ("nist",)),
        FragmentIon("C6H5", 1, "[C6H5]+", _ei(36.6), ("nist", "mat17")),
        FragmentIon("C5H7", 1, "[C5H7]+", _ei(11.1), ("nist", "tan03")),
    ),
    references=("nist", "wan03", "scn03", "mat17", "tan03", "kar18", "ish26"),
)

#: The fragment ladders per profile, keyed the way :data:`REAGENT_CLUSTERS` is.
FRAGMENT_LADDERS: dict[str, tuple[FragmentLadder, ...]] = {
    "EASYIC_POS": (_MONOTERPENE_LADDER,),
}


def fragment_ladders(profile_name: str) -> tuple[FragmentLadder, ...]:
    """The analytes a profile's source breaks, and what into.

    :param profile_name: The profile's name.
    :return: Its fragment ladders, empty when it names none.
    """
    return FRAGMENT_LADDERS.get(profile_name, ())
