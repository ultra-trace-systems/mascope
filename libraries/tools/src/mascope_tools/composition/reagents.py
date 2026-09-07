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

The probes here are the narrow half of the reagent-cluster library that step
1.4 builds in this module: enough exact masses to answer "is this carrier in
this spectrum", not the full cluster grammar with isotopologues and hydrates
that the reagent pre-pass will enumerate. 1.4 grows the module; it does not
replace it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

import numpy as np

from mascope_tools.composition.config import ELECTRON_MASS
from mascope_tools.composition.utils import composition_mass, parse_composition


#: Intensity a probe ion must reach, relative to the spectrum's base peak, for
#: its channel to count as present. The sodium counter-example sets it: the one
#: sodium trace found across the uronium gate set sits at 0.008% of the base
#: peak, which a 0.01% floor excludes, while the ammonium cluster that is
#: genuinely there is an order of magnitude above it.
DEFAULT_CHANNEL_MIN_RELATIVE_INTENSITY = 1e-4

#: Window a probe ion is looked for in, in ppm - deliberately far wider than the
#: window the same run searches compositions in.
#:
#: Finding a cluster ion is a detection question, not an assignment: its mass is
#: known exactly and nothing competes with it, so the only thing a tight window
#: buys is missing it. And it does miss it. On the gate's dense uronium set the
#: reagent's own cluster ladder sits +4.7, +6.6 and +27.5 ppm out - the
#: low-mass end of that acquisition is calibrated against the analytes, not
#: against ions this bright - so a 3 ppm probe finds none of the three while a
#: composition search at 3 ppm is still right for the analytes.
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
        mass = composition_mass(parse_composition(self.formula))
        return mass - self.charge * ELECTRON_MASS


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
