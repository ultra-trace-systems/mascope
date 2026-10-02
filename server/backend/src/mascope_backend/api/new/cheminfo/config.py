"""
Configuration settings for composition search (cheminfo).
"""

from pydantic import BaseModel


class ChemInfoConfig(BaseModel):
    """
    Configuration settings for composition search (cheminfo).
    """

    # Base URL for the ChemInfo website (kept for reference)
    BASE_URL: str = "https://info.cheminfo.org"

    # Timeout in seconds for HTTP requests (legacy, kept for reference)
    REQUEST_TIMEOUT: float = 10.0

    # Default precision for m/z matching in ppm
    DEFAULT_MZ_PRECISION: float = 3.0

    # Default formula range for queries
    DEFAULT_FORMULA_RANGE: str = "C0-80 H0-160 O0-50 N0-20"

    # The least share of its ion's brightest line a line may have for a search
    # asked for isotopologues (`isotopologues=True`) to read a peak as that
    # line. 1% is the depth the composition finder predicts an envelope to
    # for scoring (`mascope_tools.composition.config.ISOTOPE_ABUNDANCE_THRESHOLD`):
    # the 13C line of any carbon compound, a single 34S or 37Cl, the 18O line of
    # an oxygen-rich ion and the 2% unlabelled remainder of a 15N reagent are all
    # above it.
    ISOTOPOLOGUE_FLOOR: float = 0.01

    # Debounce delay in milliseconds for frontend API requests
    DEBOUNCE_DELAY_MS: int = 800

    # How long a finished match search keeps its result for the user who ran it
    # (`match_results`). The pane fetches it the moment the completion
    # notification lands, so this only has to outlast that round trip; what it
    # bounds is how long Redis holds a result nobody came for.
    MATCH_RESULT_TTL_SECONDS: int = 300


# Global config instance for composition search (cheminfo)
cheminfo_config = ChemInfoConfig()
