"""The library's class defaults and the matcher's are the same numbers.

`mascope_tools` states the instrument class's matching window so that an engine
outside this server - a reference run, a re-analysis - can pair a predicted
line with a peak the way Mascope pairs it. The matcher states the same window
as its own class default, and the two tables cannot be one: the matcher's
library is not a dependency of the public one. So they are pinned here, where
both are importable, and a change to either side fails until it is made on
both.
"""

from mascope_match.params import (
    ORBI_DEFAULT_MZ_TOLERANCE,
    TOF_DEFAULT_MZ_TOLERANCE,
    OrbiMatchParams,
    TofMatchParams,
)
from mascope_tools.composition.profiles import (
    INSTRUMENT_MATCH_TOLERANCE_PPM,
    resolve_match_tolerance_ppm,
)


def test_the_class_matching_windows_agree_with_the_matchers():
    assert INSTRUMENT_MATCH_TOLERANCE_PPM == {
        "orbi": float(ORBI_DEFAULT_MZ_TOLERANCE),
        "tof": float(TOF_DEFAULT_MZ_TOLERANCE),
    }


def test_the_resolved_window_is_the_one_a_default_match_would_use():
    assert resolve_match_tolerance_ppm("orbi") == OrbiMatchParams().mz_tolerance
    assert resolve_match_tolerance_ppm("tof") == TofMatchParams().mz_tolerance


def test_an_unknown_class_is_matched_at_the_wider_window():
    # An instrument nobody classified must not be held to an Orbitrap's
    # pairing: that would drop the real line and charge the candidate for it.
    assert resolve_match_tolerance_ppm(None) == float(TOF_DEFAULT_MZ_TOLERANCE)
    assert resolve_match_tolerance_ppm("quadrupole") == float(TOF_DEFAULT_MZ_TOLERANCE)
