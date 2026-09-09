"""Resolving a run onto an assignment profile, and recording what it resolved to.

The presets themselves are library data
(``mascope_tools.composition.profiles``); this module is the seam between them
and a run: it decides which preset a sample gets, turns the pair into the
untargeted stage's search parameters, and produces the snapshot that goes on the
run so the answer is reproducible.

Why resolution rather than configuration (decision 1 of the assignment quality
plan): the reagent chemistry is already recorded in the deployment, as the
mechanism panel of the sample's ionization mode. A mode carrying ``+Br-`` is a
bromide source whatever anyone types, so the engine reads the panel instead of
asking. A run may still name a profile explicitly, and naming ``none`` is what
turns the whole layer off - the identity profile reproduces the grid and window
the engine used before profiles existed.

The resolved content, not the name, is what lands in ``PeakAssignmentRun.config``:
a run whose profile said ``auto`` has to stay comparable with one run months
later, after the presets have been revised or a mode's mechanisms edited.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from mascope_backend.api.new.cheminfo.utils import to_explicit_isotope_format
from mascope_backend.api.new.peak_assignments.config import (
    DEFAULT_PROFILE,
    PeakAssignmentConfig,
)
from mascope_tools.composition.models import (
    CompositionSearchConfig,
    HeuristicFilterConfig,
)
from mascope_tools.composition.profiles import (
    ChemistryContext,
    ReagentProfile,
    detect_reagent_profile,
    get_chemistry_context,
    get_reagent_profile,
    resolve_element_ranges,
    resolve_fallback_sigma_ppm,
    resolve_mz_precision_ppm,
)
from mascope_tools.composition.reagents import (
    ChannelEvidence,
    detect_channels,
    evidence_records,
    present_notations,
    secondary_channels,
)


#: The config value asking the engine to work the profile out for itself. The
#: default, because a deployment that has configured its ionization modes has
#: already said what its chemistry is. Defined with the model that validates it.
AUTO = DEFAULT_PROFILE

#: The key the resolved profile is snapshotted under in ``run.config``. Beside
#: the requested config rather than replacing it: the request says what was
#: asked for ("auto"), the snapshot says what that meant on the day.
RESOLVED_PROFILE_KEY = "resolved_profile"

#: Where a resolved value came from, recorded per field in the snapshot so a
#: reader can tell a profile default from an explicit override without
#: re-deriving either.
SOURCE_PROFILE = "profile"
SOURCE_CONFIG = "config"


@dataclass(frozen=True)
class ResolvedProfile:
    """One run's chemistry, resolved: the presets and what they imply.

    :param profile: The reagent profile the sample resolved to.
    :param context: The chemistry context that goes with it.
    :param element_ranges: The neutral grid the untargeted stage enumerates.
    :param mz_precision_ppm: The untargeted stage's m/z window.
    :param fallback_sigma_ppm: The width the fit score's mass term falls back to
        where nothing has fitted the sample's own - the Gaussian sigma, not a
        second window. An order of magnitude tighter than ``mz_precision_ppm``
        on an Orbitrap, and a different statement: the window says where to
        look, this says how well a hit has to agree once it is there.
    :param requested_profile: What the run config asked for (``"auto"`` or a
        name), kept so the snapshot records the question as well as the answer.
    :param requested_context: The same for the context.
    :param element_ranges_source: :data:`SOURCE_PROFILE` when the grid came from
        the presets, :data:`SOURCE_CONFIG` when the run overrode it.
    :param mz_precision_source: The same for the window.
    :param channel_evidence: What the sample's spectrum said about each of the
        profile's secondary channels - present or not, and on what.
    :param unavailable_channels: Channels the spectrum showed but the
        deployment has no mechanism row for, so they could not be searched.
    """

    profile: ReagentProfile
    context: ChemistryContext
    element_ranges: str
    mz_precision_ppm: float
    fallback_sigma_ppm: float
    requested_profile: str
    requested_context: str
    element_ranges_source: str
    mz_precision_source: str
    channel_evidence: tuple[ChannelEvidence, ...] = ()
    unavailable_channels: tuple[str, ...] = ()

    @property
    def minor_channels(self) -> frozenset[str]:
        """The secondary channels this run searches beside the mode's own.

        A channel reaches this set only if the sample's spectrum showed its
        carrier *and* the deployment can express it as a mechanism; the two
        conditions are recorded separately, so a run says which of the two an
        absent channel failed.
        """
        unavailable = set(self.unavailable_channels)
        return frozenset(
            notation
            for notation in present_notations(self.channel_evidence)
            if notation not in unavailable
        )

    def heuristics_config(self, *, use_senior: bool = True) -> HeuristicFilterConfig:
        """The heuristic filter this run's context implies.

        The context's ratio windows ride along as a boolean gate; a context that
        constrains no ratio (``none``, and every profile that defaults to it)
        leaves the filter exactly as it was before profiles existed.

        :param use_senior: Whether to apply the Senior feasibility cut, which
            the peak-centric engine always does.
        :return: The filter configuration.
        """
        return HeuristicFilterConfig(
            use_senior=use_senior,
            context_ratio_windows=self.context.ratio_windows(),
        )

    def search_config(self, notations: list[str]) -> CompositionSearchConfig:
        """The finder's configuration for this run.

        The grid is converted to explicit-isotope notation here rather than in
        :func:`resolve_profile`, so what the run records stays the string a
        person would write (``^N0-1``) while the finder gets the one it parses
        (``[15N]0-1``).

        :param notations: The ionization notations to search, already in the
            finder's explicit-isotope form.
        :return: The composition search configuration.
        """
        element_ranges, _ = to_explicit_isotope_format(self.element_ranges)
        return CompositionSearchConfig(
            ionizations=",".join(notations),
            mass_range_ppm=self.mz_precision_ppm,
            element_count_ranges=element_ranges,
            use_unsaturation=True,
            min_unsaturation=-1000.0,
            max_unsaturation=10000.0,
        )

    def snapshot(self) -> dict:
        """What the run records about the chemistry it searched under.

        Everything a later reader needs to know what the run actually did, and
        nothing it would have to look up: the grid and window verbatim, the
        ratio windows that gated candidates, and which of them the run itself
        chose rather than inheriting.

        :return: A JSON-serializable dict.
        """
        return {
            "profile": self.profile.name,
            "profile_label": self.profile.label,
            "requested_profile": self.requested_profile,
            "context": self.context.name,
            "context_label": self.context.label,
            "requested_context": self.requested_context,
            "element_ranges": self.element_ranges,
            "element_ranges_source": self.element_ranges_source,
            "mz_precision_ppm": self.mz_precision_ppm,
            "mz_precision_source": self.mz_precision_source,
            "fallback_sigma_ppm": self.fallback_sigma_ppm,
            "ratio_windows": {
                key: list(window)
                for key, window in self.context.ratio_windows().items()
            },
            "secondary_channels": sorted(self.minor_channels),
            "channel_evidence": evidence_records(self.channel_evidence),
            "unavailable_channels": list(self.unavailable_channels),
        }


def with_secondary_channels(
    resolved: ResolvedProfile,
    mz,
    intensity,
    available_notations,
) -> ResolvedProfile:
    """Decide which of the profile's secondary channels this sample runs.

    The mechanism panel of a deployment says what an operator configured, not
    what the source produces, so neither answers this on its own: the spectrum
    is asked whether the channel's carrier is in it, and the mechanism table
    whether the result can be expressed at all. A channel needs both, and the
    resolution records each separately - "the source does not run it" and "this
    deployment cannot say it" are different facts about a missing channel, and
    only the second is worth fixing by configuration.

    Stage A is untouched throughout: a curated target carries its own ions.

    :param resolved: The profile resolution to extend.
    :param mz: The sample's peak m/z values.
    :param intensity: Their intensities, in the same order.
    :param available_notations: Mechanism notations the deployment holds for
        this sample's polarity.
    :return: A new resolution carrying the channel evidence.
    """
    channels = secondary_channels(resolved.profile.name)
    if not channels:
        return resolved
    # The detection window is the reagents module's own, not this run's search
    # window: a cluster ion's mass is known and uncontested, and the acquisitions
    # this runs on put those ions several ppm out.
    evidence = detect_channels(channels, mz, intensity)
    available = set(available_notations or ())
    unavailable = tuple(
        notation
        for notation in present_notations(evidence)
        if notation not in available
    )
    return replace(
        resolved,
        channel_evidence=tuple(evidence),
        unavailable_channels=unavailable,
    )


def resolve_profile(
    config: PeakAssignmentConfig,
    mechanism_notations: list[str] | tuple[str, ...] = (),
    instrument_type: str | None = None,
    polarity: str | None = None,
) -> ResolvedProfile:
    """Resolve one run's assignment profile.

    Pure: everything it reads about the sample arrives as an argument, so the
    resolution is testable without a database and identical on the per-sample
    and batch paths.

    :param config: The run configuration.
    :param mechanism_notations: The sample's ionization mechanism notations, as
        the mechanism table stores them (``"+Br-"``), not in the finder's
        explicit-isotope form - the fingerprint is written in the stored
        notation.
    :param instrument_type: ``"orbi"`` or ``"tof"``, deciding the default m/z
        window.
    :param polarity: The sample's polarity, consulted only when no mechanism
        is diagnostic.
    :raises KeyError: The config names a profile or context that does not exist.
    :return: The resolution, ready to configure the search and be snapshotted.
    """
    requested_profile = (config.profile or AUTO).strip()
    requested_context = (config.context or AUTO).strip()

    if requested_profile.lower() == AUTO:
        profile = detect_reagent_profile(mechanism_notations, polarity)
    else:
        profile = get_reagent_profile(requested_profile)

    if requested_context.lower() == AUTO:
        context = get_chemistry_context(profile.default_context)
    else:
        context = get_chemistry_context(requested_context)

    if config.formula_ranges is not None:
        element_ranges = config.formula_ranges
        element_ranges_source = SOURCE_CONFIG
    else:
        element_ranges = resolve_element_ranges(profile, context)
        element_ranges_source = SOURCE_PROFILE

    if config.mz_precision_ppm is not None:
        mz_precision_ppm = config.mz_precision_ppm
        mz_precision_source = SOURCE_CONFIG
    else:
        mz_precision_ppm = resolve_mz_precision_ppm(profile, instrument_type)
        mz_precision_source = SOURCE_PROFILE

    return ResolvedProfile(
        profile=profile,
        context=context,
        element_ranges=element_ranges,
        mz_precision_ppm=mz_precision_ppm,
        fallback_sigma_ppm=resolve_fallback_sigma_ppm(instrument_type),
        requested_profile=requested_profile,
        requested_context=requested_context,
        element_ranges_source=element_ranges_source,
        mz_precision_source=mz_precision_source,
    )
