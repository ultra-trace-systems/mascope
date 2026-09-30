ELECTRON_MASS = 5.48579909065e-4

DEFAULT_MAXIMUM_ROWS = 100
DEFAULT_SEARCH_ELEMENT_COUNT_RANGES = "C0-100 H0-200 N0-20 O0-20"
# Default mass range threshold to look for formulas in ppm
DEFAULT_MASS_RANGE_THRESHOLD_PPM = 1

DEFAULT_MAXIMUM_UNSATURATION = 50.0
ISOTOPE_ABUNDANCE_THRESHOLD = 0.01
ISOTOPE_MATCHING_MZ_TOLERANCE_PPM = 5
ISOTOPE_MATCHING_INTENSITY_TOLERANCE = 0.4


# Wiley spectral database:
# H/C ratio 0.1...6.0 in 99.99% of all formulas.
DEFAULT_ELEMENTAL_RATIO_RANGE = {
    "H/C": (0.1, 6.0),
    "N/C": (0.0, 2.0),
    "O/C": (0.0, 2.0),
    "S/C": (0.0, 0.1),
    "P/C": (0.0, 0.05),
    "Cl/C": (0.0, 0.05),
    "Br/C": (0.0, 0.05),
    "F/C": (0.0, 0.05),
    "I/C": (0.0, 0.05),
}

# Carbon-equivalent count (C + Si) below which a chemistry context's Van Krevelen
# ratio windows say nothing and are not applied. A one- or two-carbon molecule
# sits outside every ambient window by construction - methane reads H/C 4.0, urea
# 4.0, formaldehyde 2.0 with O/C 1.0 - and those are exactly the small molecules a
# CIMS source is most sensitive to. Below the floor the windows give way to two
# valence-level checks (see `rule_context_ratios`).
CONTEXT_RATIO_MIN_CARBON = 3


UNSATURATION_COEFFICIENTS = {
    "C": 2,
    "H": -1,
    "N": 1,
    "O": 0,
    "F": -1,
    "Cl": -1,
    "Br": -1,
    "I": -1,
    "C[13]": 2,
    "O[18]": 0,
}
