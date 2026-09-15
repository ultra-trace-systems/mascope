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

    # Debounce delay in milliseconds for frontend API requests
    DEBOUNCE_DELAY_MS: int = 800


# Global config instance for composition search (cheminfo)
cheminfo_config = ChemInfoConfig()
