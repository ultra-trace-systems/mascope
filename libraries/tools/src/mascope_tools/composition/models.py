from collections.abc import Callable
from dataclasses import asdict, dataclass, field

import numpy as np

from mascope_tools.composition import config
from mascope_tools.composition.envelope_tolerance import EnvelopeTolerance


@dataclass(frozen=True)
class Atom:
    symbol: str
    min_count: int
    max_count: int
    mass: float


@dataclass(frozen=True)
class IonizationMechanism:
    """An ionization mechanism as the composition finder searches through it.

    The ion holds ``multimer`` molecules of mass ``M``, gains or loses the
    species and loses the neutral, and carries ``charge``: its m/z is
    ``(multimer * M + shift) / |charge|`` (:meth:`ion_mz`), and the neutral a
    peak reads as is the inverse (:meth:`neutral_mass`).

    :param mascope_notation: The mechanism as it was given.
    :param addition: Whether the species is added (True) or removed.
    :param formula: The species' formula in Hill order; empty for the
        electron of electron transfer, ``H2`` for the two protons of
        ``[M+2H]2+``.
    :param charge: The ion's charge, signed.
    :param mass: The species' mass less the electrons its charge stands for,
        unsigned; the electron's own mass for electron transfer.
    :param multimer: How many molecules the ion holds: 2 for ``[2M+H]+``.
    :param neutral_loss: The neutral the ion loses, in Hill order; empty for
        none.
    :param loss_mass: The neutral loss's mass.
    """

    mascope_notation: str
    addition: bool
    formula: str
    charge: int
    mass: float
    multimer: int = 1
    neutral_loss: str = ""
    loss_mass: float = 0.0

    @property
    def shift(self) -> float:
        """What the mechanism adds to the mass of the molecules it ionizes,
        signed, its neutral loss taken off
        (:func:`mascope_tools.composition.utils.mass_shift`)."""
        return (self.mass if self.addition else -self.mass) - self.loss_mass

    @property
    def simple(self) -> bool:
        """Whether the ion is one molecule of a single charge that loses
        nothing: its m/z is the molecule's mass plus :attr:`shift`."""
        return self.multimer == 1 and abs(self.charge) == 1 and not self.neutral_loss

    def ion_mz(self, neutral_mass: float) -> float:
        """The m/z of the ion this mechanism makes of a neutral of this mass.

        :param neutral_mass: The molecule's monoisotopic mass.
        :return: ``(multimer * M + shift) / |charge|``.
        """
        return (self.multimer * neutral_mass + self.shift) / abs(self.charge)

    def neutral_mass(self, ion_mz: float) -> float:
        """The mass of the neutral whose ion through this mechanism is at this
        m/z: the inverse of :meth:`ion_mz`.

        :param ion_mz: The ion's m/z.
        :return: ``(|charge| * m/z - shift) / multimer``.
        """
        return (abs(self.charge) * ion_mz - self.shift) / self.multimer

    def neutral_tolerance(self, mz_tolerance: float) -> float:
        """How far a neutral's mass may move while its ion stays within an
        m/z tolerance: ``|charge| / multimer`` of it.

        :param mz_tolerance: The tolerance in m/z, in Da.
        :return: The tolerance in neutral mass, in Da.
        """
        return mz_tolerance * abs(self.charge) / self.multimer

    @property
    def charge_signs(self) -> str:
        """The ion's charge as an ion formula ends in it: its sign once per
        charge, ``+`` or ``--``."""
        return ("+" if self.charge > 0 else "-") * abs(self.charge)


@dataclass(frozen=True)
class CompositionSearchConfig:
    """Composition search parameters requested by the user"""

    ionizations: str
    min_unsaturation: float = 0.0
    max_unsaturation: float = config.DEFAULT_MAXIMUM_UNSATURATION
    only_integer_unsaturation: bool = False
    max_result_rows: int = config.DEFAULT_MAXIMUM_ROWS
    use_unsaturation: bool = False
    element_count_ranges: str = config.DEFAULT_SEARCH_ELEMENT_COUNT_RANGES
    mass_range_ppm: float = config.DEFAULT_MASS_RANGE_THRESHOLD_PPM
    peak_height_threshold: float = 0.0


@dataclass(frozen=True)
class PatternScoring:
    """How a candidate's isotope envelope is predicted, matched and scored.

    Every field is a property of the sample being searched rather than a
    constant of the search, which is what makes the finder's ranking
    instrument-correct: the mass width the score is judged against, the window
    a predicted line is matched in, and how deep the envelope is predicted
    before the spectrum stops being able to hold a line at all.

    The defaults reproduce the fixed constants the finder used before a caller
    could say anything about the sample - a 5 ppm window and a 1% envelope,
    both Orbitrap-shaped - so a caller that passes nothing searches exactly as
    it did.

    :param sigma_ppm: The sample's fitted mass-error width, the Gaussian width
        of the fit score's mass term. ``None`` falls back to
        ``score_pattern_v2``'s own Orbitrap-appropriate default, which is wrong
        for a TOF.
    :param mu_ppm: The sample's fitted mass-error offset, subtracted before
        scoring. Reported mass errors stay raw: the offset is a property of the
        calibration, not of the assignment, and a reader comparing a row against
        the instrument's own accuracy needs the number the instrument produced.
    :param mz_tolerance_ppm: How far from its prediction a line may sit and
        still be this ion's. The sample's match tolerance, so a peak claimed
        here would be claimed by the targeted matcher too.
    :param abundance_floor: The deepest the envelope is ever predicted,
        relative to the ion's own line - the sample's match-params abundance
        floor, the same floor Stage A generates its isotopes at. How deep it
        actually goes is decided per peak by what that peak's noise allows
        (:func:`heuristic_filter.envelope_floor_for_peak`); this only bounds it.
    :param resolution: The file's resolving power as a function of m/z. Where
        it is known, the predicted lines one peak of that width cannot separate
        are merged into one line before matching and scoring
        (:func:`heuristic_filter.merge_unresolved_lines`): on a TOF the peak at
        the 13C position also holds the 15N, 2H and 17O lines. ``None`` keeps
        every line apart, as a caller that knows nothing of the file did.
    :param envelope_tolerance: How well the sample's run measures an isotope
        line's share, fitted on its own committed envelopes
        (:mod:`envelope_tolerance`): the band of share a matched line scores as
        a match in, and the expected signal-to-noise from which an absent line
        counts. ``None`` scores at today's numbers, a 5% tolerance and a
        threshold of 3.
    :param centre: The sample's centre as a function of m/z, where it follows
        one (``measurement_model.MeasurementModel.centres_ppm``): each line's
        mass error is then read from the centre at that line's m/z instead of
        from ``mu_ppm``. ``None`` reads every line from ``mu_ppm``.
    """

    sigma_ppm: float | None = None
    mu_ppm: float = 0.0
    mz_tolerance_ppm: float = config.ISOTOPE_MATCHING_MZ_TOLERANCE_PPM
    abundance_floor: float = config.ENVELOPE_ABUNDANCE_FLOOR
    resolution: Callable[[float], float] | None = None
    envelope_tolerance: EnvelopeTolerance | None = None
    centre: Callable[[np.ndarray], np.ndarray] | None = None

    def centres_at(self, mz) -> np.ndarray | float:
        """The centre each line's mass error is read from, at the lines' m/z.

        :param mz: The lines' predicted m/z.
        :return: :attr:`mu_ppm` where the sample follows no centre function,
            else the function's value at each line.
        """
        if self.centre is None:
            return self.mu_ppm
        return np.asarray(self.centre(np.asarray(mz, dtype=float)), dtype=float)


@dataclass(frozen=True)
class HeuristicFilterConfig:
    """Heuristic filter parameters

    carbon_element_ratio_range uses default values from
    mascope_tools.composition.config.DEFAULT_ELEMENTAL_RATIO_RANGE if not provided,
    while non_carbon_element_ratio_range defaults to an empty dict, meaning no
    filtering based on non-carbon element ratios.

    Parameter examples:
    carbon_element_ratio_range = {
        "H/C": (1.0, 3.0),
        "N/C": (0.0, 2),
        "O/C": (0.0, 2),
        "S/C": (0.001, 1)
    }
    non_carbon_element_ratio_range = {
        "K/Na": (0, 5.0),
        "H/N": (0, 5.0),
        "H/S": (0, 5.0),
    }
    """

    non_carbon_element_ratio_range: dict[str, tuple[float, float]] = field(
        default_factory=dict
    )

    carbon_element_ratio_range: dict[str, tuple[float, float]] = field(
        default_factory=lambda: config.DEFAULT_ELEMENTAL_RATIO_RANGE.copy()
    )

    # Apply the Lewis/Senior structural feasibility cut (Rule 2). Off by default
    # because `rule_senior` used to be a no-op placeholder: enabling it for every
    # caller would silently narrow the results of the long-standing composition
    # search. The peak-centric engine opts in explicitly.
    use_senior: bool = False

    # A chemistry context's Van Krevelen ratio windows, keyed by the constants in
    # `profiles` ("H/C", "O/C", "N/C", "DBE/C") and evaluated on EFFECTIVE counts
    # rather than raw ones -- see `rule_context_ratios`, which applies them.
    #
    # Deliberately separate from `carbon_element_ratio_range`, which carries the
    # universal Wiley bands and is applied per raw element pair: a context window
    # is a much tighter statement about one matrix, and reading it through the
    # raw-count rule would reject urea (H/C 4.0) and trichloroacetic acid
    # (H/C 0.5) on ratios neither of them violates. Empty by default, so a caller
    # that names no context filters exactly as it did before contexts existed.
    context_ratio_windows: dict[str, tuple[float, float]] = field(default_factory=dict)

    # Read the context windows on the carbon skeleton of a formula's
    # organonitrate groups as well as on the formula (step 3.6,
    # `heuristic_filter.context_skeleton_groups`). A run switch, on only where
    # nitrate is the reagent on an Orbitrap and the context makes
    # organonitrates (`profiles.nox_skeleton_applies`).
    context_nox_skeleton: bool = False


@dataclass
class Result:
    formula: str
    neutral_mass: float
    # Signed, (observed - predicted)/predicted * 1e6: the targeted matcher's
    # match_mz_error convention. Rank candidates on its magnitude.
    composition_error_ppm: float
    ion: str | None
    ionization_mechanism: str | None
    observed_mass: float
    unsaturation: float | None = None
    other_candidates: list[str] | None = None
    # Set only on a reading of the peak as another line of the ion than its
    # monoisotopic one (`isotopologues.isotopologue_readings`): that line's
    # m/z, which the mass error above is taken against.
    isotope_mz: float | None = None

    def to_dict(self):
        d = asdict(self)
        # Remove None values for optional fields
        return {k: v for k, v in d.items() if v is not None}
