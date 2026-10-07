"""The bare numbers an ion-focus trace puts on the wire.

A trace's arrays travel as bytes, which Socket.IO lifts out into binary
attachments, but the two-point markers - the expected height at the target
m/z, the matched peak - are plain lists, and those go through the JSON codec.
A numpy `float64` passes there because it subclasses `float`. A `float32`
does not: the encoder raises `TypeError: Object of type float32 is not JSON
serializable` out of the emit, and the user gets an error instead of a plot.

The profile is held as float32 for transport, so a height read off it is such
a scalar unless it is converted. That is the fall-back for a main isotope with
profile signal in its window but no detected peak there - a target that is
absent from the sample, or matched only far off its expected m/z.

No DB, file I/O or Socket.IO server: `_process_isotope` is pure.
"""

from types import SimpleNamespace

import numpy as np
import xarray as xr

from mascope_backend.api.controllers.visualization.visualization_controller import (
    DMZ_ORBI,
    IsotopeContext,
    _process_isotope,
)
from mascope_backend.socket.server import _NanSafeJson
from mascope_match.params import unmatched_isotope_params


MAIN_MZ = 158.97352
SECOND_MZ = 160.96932
RELATIVE_ABUNDANCES = [0.9, 0.045]
# Exactly representable as float32, so the height can be compared with ==.
PROFILE_HEIGHT = 13.5


def _unmatched_isotope(mz: float, relative_abundance: float) -> SimpleNamespace:
    """An isotope with no stored match, as `_fetch_isotopes` rebuilds it."""
    return SimpleNamespace(
        target_isotope_id="isotope",
        mz=mz,
        relative_abundance=relative_abundance,
        **{**unmatched_isotope_params.model_dump(), "sample_peak_mz": mz},
    )


def _profile_bump(center: float, height: float) -> xr.DataArray:
    """A few profile points around `center`, well inside the +-DMZ_ORBI window."""
    mz = center + np.linspace(-0.002, 0.002, 5)
    values = height * np.array([0.1, 0.6, 1.0, 0.6, 0.1])
    return xr.DataArray(values, coords={"mz": mz}, dims="mz", name="sum_signal")


def _context(index: int, main_isotope_height: float) -> IsotopeContext:
    """A sample whose only detected peak sits outside both isotope windows."""
    averaged_signal = xr.concat(
        [_profile_bump(MAIN_MZ, PROFILE_HEIGHT), _profile_bump(SECOND_MZ, 2.0)],
        dim="mz",
    )
    detected_peaks = xr.DataArray(
        np.array([594.5]),
        coords={"mz": [MAIN_MZ + 0.014]},
        dims="mz",
        name="peak_heights",
    )
    return IsotopeContext(
        index=index,
        main_isotope_height=main_isotope_height,
        relative_abundances=RELATIVE_ABUNDANCES,
        averaged_signal=averaged_signal,
        peak_timeseries=detected_peaks,
        mean_peak_heights=detected_peaks,
        peak_min_intensity=0.0,
        mz_tolerance=5.0,
        isotope_ratio_tolerance=0.2,
        dmz=DMZ_ORBI,
    )


def _expected_height(traces: list[dict]) -> float:
    """The top of the red expected-height marker among `traces`."""
    (marker,) = [trace for trace in traces if trace["name"] == "target m/z"]
    return marker["y"][1]


def _encode(traces: list[dict]) -> str:
    """Render traces as an emit does, with the server's own JSON codec.

    Socket.IO takes `bytes` values out as attachments before it encodes, so
    only the rest has to be JSON.
    """
    return _NanSafeJson.dumps(
        [
            {key: value for key, value in trace.items() if not isinstance(value, bytes)}
            for trace in traces
        ]
    )


def test_a_main_isotope_without_a_detected_peak_encodes():
    """The regression as a client would have seen it: the emit raised."""
    main = _unmatched_isotope(MAIN_MZ, RELATIVE_ABUNDANCES[0])

    result = _process_isotope(main, _context(0, 0), None)

    _encode(result.spectrum_traces)
    assert type(result.main_isotope_height) is float
    assert result.main_isotope_height == PROFILE_HEIGHT
    assert _expected_height(result.spectrum_traces) == PROFILE_HEIGHT


def test_the_isotopes_that_follow_scale_an_encodable_height():
    """The main isotope's height is carried into every later isotope's marker."""
    main = _unmatched_isotope(MAIN_MZ, RELATIVE_ABUNDANCES[0])
    second = _unmatched_isotope(SECOND_MZ, RELATIVE_ABUNDANCES[1])
    main_result = _process_isotope(main, _context(0, 0), None)

    result = _process_isotope(
        second, _context(1, main_result.main_isotope_height), None
    )

    _encode(result.spectrum_traces)
    expected = _expected_height(result.spectrum_traces)
    assert type(expected) is float
    assert expected == PROFILE_HEIGHT * (
        RELATIVE_ABUNDANCES[1] / RELATIVE_ABUNDANCES[0]
    )
