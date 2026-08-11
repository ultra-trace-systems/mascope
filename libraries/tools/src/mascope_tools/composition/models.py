from dataclasses import asdict, dataclass, field

from mascope_tools.composition import config


@dataclass(frozen=True)
class Atom:
    symbol: str
    min_count: int
    max_count: int
    mass: float


@dataclass(frozen=True)
class IonizationMechanism:
    mascope_notation: str
    addition: bool
    formula: str
    charge: int
    mass: float


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


@dataclass()
class CompositionSearchState:
    """Current state of the composition search for a given m/z"""

    ion_shift: float
    mz_tolerance_da: float
    atoms: list[Atom]
    min_inner_mass: list[float]
    max_inner_mass: list[float]
    ionization_mechanism: IonizationMechanism
    results_found: int = 0


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


@dataclass
class Result:
    formula: str
    neutral_mass: float
    composition_error_ppm: float
    ion: str | None
    ionization_mechanism: str | None
    observed_mass: float
    unsaturation: float | None = None
    other_candidates: list[str] | None = None

    def to_dict(self):
        d = asdict(self)
        # Remove None values for optional fields
        return {k: v for k, v in d.items() if v is not None}
