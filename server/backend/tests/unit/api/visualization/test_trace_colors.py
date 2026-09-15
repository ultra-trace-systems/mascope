"""The colour components a visualization trace puts on the wire.

`colorcet.glasbey_hv` supplies colours as 0-1 floats, but an `rgb()` string is
read by CSS as 0-255 components, so the two need converting between. plotly.js
3 hid the mismatch - TinyColor treated a 0-1 fraction as a fraction of 255 -
and plotly.js 4 does not, because culori follows the spec: an unscaled
`rgb(0.188235,0.635294,0.854902)` renders as black. These pin the scaling so
the fractions cannot reach a client again.

No DB, file I/O or Socket.IO: the function under test is pure.
"""

import re

import pytest
from colorcet import glasbey_hv as colormap

from mascope_backend.api.controllers.visualization.visualization_controller import (
    _css_rgb,
)


def test_colorcet_still_supplies_fractions():
    """The premise of the conversion: without it there is nothing to fix.

    If colorcet ever starts handing out 0-255 components, `_css_rgb` becomes
    wrong rather than unnecessary, and this is the test that says so.
    """
    assert all(0.0 <= component <= 1.0 for component in colormap[0])
    assert any(0.0 < component < 1.0 for component in colormap[0])


def test_a_fraction_is_scaled_to_a_css_component():
    assert _css_rgb((0.188235, 0.635294, 0.854902)) == (48, 162, 218)


@pytest.mark.parametrize("index", [0, 1, 5, 42, 255])
def test_every_palette_entry_lands_in_range(index):
    """Whatever the palette holds, the result is a valid CSS component."""
    assert all(
        isinstance(component, int) and 0 <= component <= 255
        for component in _css_rgb(colormap[index])
    )


def test_the_extremes_survive_the_round_trip():
    assert _css_rgb((0.0, 0.0, 0.0)) == (0, 0, 0)
    assert _css_rgb((1.0, 1.0, 1.0)) == (255, 255, 255)


def test_the_formatted_string_carries_no_decimal_point():
    """The regression as a client would have seen it.

    A trace colour is built by formatting the triple straight into an `rgb()`
    string, so a surviving fraction shows up there as a decimal point.
    """
    formatted = "rgb({},{},{})".format(*_css_rgb(colormap[5]))
    assert "." not in formatted
    assert re.fullmatch(r"rgb\(\d{1,3},\d{1,3},\d{1,3}\)", formatted)
