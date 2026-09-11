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
peak becomes a reagent row and leaves the analyte ledger - so it is held to a
stricter rule: every atom in a library ion comes from the reagent, the solvent
or the instrument background, and none from the sample. The section comment on
the library says where the two lists disagree, and why that is deliberate
rather than an inconsistency.

Why the library exists at all: on the gate's samples the reagent's own clusters
are the top ten peaks and most of the total signal, and with nowhere to put them
they land in the residual or, worse, get read as analytes whose formula happens
to fit.
"""

from __future__ import annotations

from dataclasses import dataclass
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

    :param notation: The ionization mechanism notation the channel is searched
        under, as the mechanism table stores it (``"+NH4+"``).
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
    :param note: Why these probes are the right evidence, for the reader who
        wonders why a channel stayed off.
    """

    notation: str
    label: str
    probes: tuple[ProbeIon, ...] = ()
    when_unobservable: str = UNOBSERVABLE_OFF
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
            notation="+NH4+",
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
            notation="+CO3-",
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
            notation="+Br2-",
            label="Dibromide cluster",
            probes=_DIBROMIDE_PROBES,
            note="the reagent's own second cluster rung",
        ),
    ),
    "NO3": (
        SecondaryChannel(
            notation="+CO3-",
            label="Carbonate adduct",
            probes=_carbonate_probes("NO3"),
            when_unobservable=UNOBSERVABLE_ON,
            note=_NITRATE_CARBONATE_NOTE,
        ),
    ),
    "NO3_15N": (
        SecondaryChannel(
            notation="+CO3-",
            label="Carbonate adduct",
            # Built from the labelled reagent, so the clusters land 0.997 Da
            # above the unlabelled ones. Carbonate itself carries no reagent
            # nitrogen, which is why the bare ions are the same either way.
            probes=_carbonate_probes("^NO3"),
            when_unobservable=UNOBSERVABLE_ON,
            note=_NITRATE_CARBONATE_NOTE,
        ),
    ),
    "IODIDE": (
        SecondaryChannel(
            notation="+I2-",
            label="Diiodide cluster",
            probes=_DIIODIDE_PROBES,
            note="the reagent's own second cluster rung",
        ),
    ),
    "ESI_POS": (
        SecondaryChannel(
            notation="+NH4+",
            label="Ammonium adduct",
            probes=_solvated("N1H4", "NH4"),
            note="solvated ammonium from the sprayed solution",
        ),
        SecondaryChannel(
            notation="+Na+",
            label="Sodium adduct",
            probes=_solvated("Na", "Na"),
            note="solvated sodium from residual salt",
        ),
        SecondaryChannel(
            notation="+K+",
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
# The rule is that an entry must be reagent all the way through: every atom in
# it comes from the reagent, the solvent, or the instrument background, and none
# from the sample. A cluster of the reagent with something the sample supplied
# is not a reagent ion at all - it is the analyte, measured through its adduct
# channel, which is the primary thing this engine is for. So:
#
# - bare reagent clusters, hydrates, and the hydrogen halide the reagent sheds
#   are in;
# - the reagent's clusters with organic acids are OUT. ``[Br+HCOOH]-`` is
#   ``[formic acid+Br]-``: the [M+Br]- analyte channel, spelled backwards. The
#   reference engine carried those for a while and they cost it real ambient
#   acids, formic acid among them, buried as "reagent";
# - ``[(CH4N2O)+NH4]+`` is OUT for the same reason one level up. It is the same
#   ion as ``[NH3+(CH4N2O)H]+`` - ambient ammonia read through its urea adduct -
#   so the analyte is the NH3. The urea MULTIMERS carrying an ammonium are in:
#   there the ammonium charges a cluster the source built, and no sample atom is
#   involved. The same ion is a probe above, where claiming nothing makes it
#   safe evidence; the two lists disagree on purpose, and that is where.
#
# The oxides split by halogen for the same reason. ``BrO3-`` is reagent; ``IO3-``
# is iodate, the deprotonated iodic acid that an iodide source is usually
# deployed to measure, so the iodine oxides are left for the assignment stages.


#: A bare cluster of the reagent with itself.
KIND_CLUSTER = "cluster"
#: The reagent clustered with water or with the acid it sheds.
KIND_ADDUCT = "adduct"
#: A reagent-halogen oxide anion.
KIND_OXIDE = "oxide"
#: A bright ion the source throws that is not a rung of any ladder.
KIND_BACKGROUND = "background"


@dataclass(frozen=True)
class ReagentCluster:
    """One reagent ion the source makes on its own, and may claim a peak.

    :param formula: The ion's elemental composition, charge excluded.
    :param charge: The ion's charge, ``+1`` or ``-1``.
    :param label: How the ion is written in a row's provenance.
    :param kind: Which part of the grammar produced it, for the reader of a
        claim rather than for the matching.
    :param anchor: Whether this ion may be used to calibrate the pass. True for
        the base ions of a source - the bare halide clusters, the protonated
        urea monomer and dimer, the nitrate core and its first rung - which are
        the brightest things the source makes and which nothing else shares a
        mass with. Those two properties are what let them be found in a wide
        window and then say where this spectrum puts the reagent's masses; a
        rung that is neither bright nor unambiguous must not.
    """

    formula: str
    charge: int
    label: str
    kind: str = KIND_CLUSTER
    anchor: bool = False

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


#: How many rungs of a reagent's own cluster ladder to enumerate. Four covers
#: every rung that carries signal on the gate's halide sets; a fifth would cost
#: nothing but has never matched.
DEFAULT_MAX_CLUSTER = 4

#: How many copies of a clustering neutral to put on each rung. Two, because the
#: dihydrate of a halide reagent is an ordinary bright ion in a humid source.
DEFAULT_MAX_NEUTRAL = 2


def _halide_clusters(
    symbol: str,
    *,
    max_n: int = DEFAULT_MAX_CLUSTER,
    max_neutral: int = DEFAULT_MAX_NEUTRAL,
    oxides: bool = True,
) -> tuple[ReagentCluster, ...]:
    """The ladder of a halide reagent: bare clusters, hydrates, oxides.

    Both parities of the bare ladder are real ions - odd ``n`` are closed-shell
    anions, even ``n`` are radical anions - and all of them are pure reagent, so
    the closed-shell preference a same-ion family is ranked by (see
    ``heuristic_filter``) has no bearing here. Nothing is being chosen between:
    the composition is known exactly and it contains no sample atom.

    :param symbol: The halogen's symbol.
    :param max_n: Rungs of the bare ladder.
    :param max_neutral: Copies of each clustering neutral per rung.
    :param oxides: Whether the halogen's oxide anions are reagent ions. False
        for iodine, whose oxides are the iodine oxyacids' analyte channel.
    """
    clusters: list[ReagentCluster] = []
    for n in range(1, max_n + 1):
        core = _ion_formula((symbol, n))
        rung = f"{symbol}{n}" if n > 1 else symbol
        # The bare monomer and dimer anchor the pass: they are the two brightest
        # ions a halide source makes and no analyte shares their mass.
        clusters.append(
            ReagentCluster(core, -1, f"[{rung}]-", KIND_CLUSTER, anchor=n <= 2)
        )
        # Water, and the hydrogen halide the reagent itself sheds - HBr on a
        # bromide source, HI on an iodide one, resolved from the reagent rather
        # than fixed, so an iodide library carries no phantom [In+HBr]-.
        for neutral in ("H2O", f"H{symbol}"):
            for k in range(1, max_neutral + 1):
                copies = f"{k}x" if k > 1 else ""
                clusters.append(
                    ReagentCluster(
                        _ion_formula((symbol, n), (neutral, k)),
                        -1,
                        f"[{rung}+{copies}{neutral}]-",
                        KIND_ADDUCT,
                    )
                )
    if oxides:
        for oxygens in (1, 2, 3):
            clusters.append(
                ReagentCluster(
                    _ion_formula((symbol, 1), ("O", oxygens)),
                    -1,
                    f"[{symbol}O{oxygens if oxygens > 1 else ''}]-",
                    KIND_OXIDE,
                )
            )
    return tuple(clusters)


#: The pure-iodine oxide clusters an iodide source throws, bright and stable in
#: time. They are background rather than chemistry anyone measures, so they are
#: claimed instead of being left to be read as exotic organoiodines.
#:
#: HOI2- and I2NO2- are deliberately absent: those are the [M+I]- readings of
#: HOI and INO2, reactive iodine species that vary in time and are exactly what
#: an iodide deployment is measuring. The same ruling as the oxides.
_IODINE_BACKGROUND: tuple[ReagentCluster, ...] = (
    ReagentCluster("I2O", -1, "[I2O]-", KIND_BACKGROUND),
    ReagentCluster("I3O", -1, "[I3O]-", KIND_BACKGROUND),
)

#: The bromide source's own precursors, deprotonated. These are the only
#: carbon-bearing ions in a halide library, and they are here for the same
#: reason everything else is: dibromomethane and bromoform are what the source
#: is dosed with, so their ions are the reagent's, not the sample's. The carbon
#: is the precursor's own - it is not a cluster with something the sample
#: supplied, which is the line the rest of this section draws.
_BROMIDE_PRECURSORS: tuple[ReagentCluster, ...] = (
    ReagentCluster("CHBr2", -1, "[CH2Br2-H]-", KIND_BACKGROUND),
    ReagentCluster("CBr3", -1, "[CHBr3-H]-", KIND_BACKGROUND),
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


def _urea_clusters(
    unit: str = "CH4N2O", *, max_n: int = 6
) -> tuple[ReagentCluster, ...]:
    """The protonated urea ladder, and the ammonium on its multimers.

    ``[(CH4N2O)n+H]+`` at 61.04 / 121.07 / 181.10 / 241.14 are the dominant ions
    of a uronium source and otherwise sit at the top of the residual.

    The ammonium series starts at ``n = 2`` on purpose; the reason is in the
    section comment above, and the same ion at ``n = 1`` is a channel probe.
    """
    clusters: list[ReagentCluster] = []
    for n in range(1, max_n + 1):
        rung = f"({unit}){n}" if n > 1 else unit
        clusters.append(
            ReagentCluster(
                _ion_formula((unit, n), ("H", 1)),
                1,
                f"[{rung}+H]+",
                KIND_CLUSTER,
                # The protonated monomer and dimer: the base peak of a uronium
                # spectrum and the ion beside it. The higher rungs are what the
                # anchors are there to protect, so they cannot anchor.
                anchor=n <= 2,
            )
        )
        if n >= 2:
            clusters.append(
                ReagentCluster(
                    _ion_formula((unit, n), ("NH4", 1)),
                    1,
                    f"[{rung}+NH4]+",
                    KIND_CLUSTER,
                )
            )
    return tuple(clusters)


#: The reagent-cluster library per profile, keyed the way
#: :data:`SECONDARY_CHANNELS` is. A profile with no single reagent species -
#: an electrospray - has no library, which is not an omission: there is no one
#: carrier whose clusters could be enumerated.
REAGENT_CLUSTERS: dict[str, tuple[ReagentCluster, ...]] = {
    "BR": _halide_clusters("Br") + _BROMIDE_PRECURSORS,
    "IODIDE": _halide_clusters("I", oxides=False) + _IODINE_BACKGROUND,
    "NO3": _nitrate_clusters("NO3"),
    "NO3_15N": _nitrate_clusters("^NO3"),
    "UR": _urea_clusters(),
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
