"""`distance` passed to find_peaks must stay >= 1 however coarse the spectrum is.

`_process_peak_shapes` derives a minimum peak separation in SAMPLES from
``dmz / median(diff(mz))``. When a spectrum's own m/z spacing is coarser than
``dmz`` that ratio drops below 1 and ``int()`` floors it to 0, which
``scipy.signal.find_peaks`` rejects outright - so the whole instrument-function
fit of an otherwise readable file dies.

Found on a real production acquisition (m/z 50-750 over only 8856 points,
median spacing 0.01266 against the fitter's ``dmz`` of 0.01). The symptom is
numpy-version dependent, which is why it is pinned here rather than by the
error text: numpy 1.26 surfaced scipy's "`distance` must be greater or equal
to 1", numpy 2.5 surfaces "cannot convert float NaN to integer".
"""

import numpy as np
import pytest

from mascope_signal.instrument_func.fit import _process_peak_shapes


def _spectrum(spacing, n=400):
    """A spectrum with evenly spaced m/z and a few clean Gaussian peaks."""
    mz = 50.0 + np.arange(n) * spacing
    spec = np.full(n, 10.0)
    for centre in (n // 5, n // 2, 4 * n // 5):
        spec += 1000.0 * np.exp(-0.5 * ((np.arange(n) - centre) / 1.5) ** 2)
    return mz, spec


def test_spacing_coarser_than_dmz_does_not_raise():
    """The production case: median spacing 0.01266 with dmz 0.01 -> ratio 0.79."""
    mz, spec = _spectrum(spacing=0.01266)
    # Guard the premise: without a clamp this is int(0.79) == 0.
    assert int(0.01 / np.median(np.diff(mz))) == 0
    _process_peak_shapes(mz, spec, "orbi", 0.01, 0.98)


def test_spacing_far_coarser_than_dmz_does_not_raise():
    mz, spec = _spectrum(spacing=0.5)
    _process_peak_shapes(mz, spec, "orbi", 0.01, 0.98)


@pytest.mark.parametrize("mz", [np.array([]), np.array([100.0])])
def test_degenerate_mz_axis_does_not_raise(mz):
    """An empty or single-point axis makes median(diff(mz)) NaN."""
    spec = np.ones(mz.size)
    _process_peak_shapes(mz, spec, "orbi", 0.01, 0.98)


def test_all_nan_mz_axis_does_not_raise():
    mz = np.full(50, np.nan)
    spec = np.ones(50)
    _process_peak_shapes(mz, spec, "orbi", 0.01, 0.98)


def test_fine_spacing_still_uses_a_real_distance(monkeypatch):
    """The clamp must not flatten a genuine separation down to 1.

    A spacing of 0.001 against dmz 0.01 should still ask find_peaks for 10
    samples of separation - otherwise the fix would silently change the fit on
    every normal file.
    """
    seen = {}

    import mascope_signal.instrument_func.fit as fit_mod

    real = fit_mod._choose_peaks

    def spy(spec, distance, n_peaks=100):
        seen["distance"] = distance
        return real(spec, distance=distance, n_peaks=n_peaks)

    monkeypatch.setattr(fit_mod, "_choose_peaks", spy)
    mz, spec = _spectrum(spacing=0.001, n=2000)
    _process_peak_shapes(mz, spec, "orbi", 0.01, 0.98)
    assert seen["distance"] == 10
