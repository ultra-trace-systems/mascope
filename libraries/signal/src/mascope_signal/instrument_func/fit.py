from functools import partial

import numpy as np
from lmfit.model import ModelResult
from lmfit.models import SkewedGaussianModel, SplineModel
from scipy.interpolate import interp1d
from scipy.optimize import curve_fit
from scipy.signal import find_peaks
from scipy.stats import linregress

from mascope_file.name import get_instrument_type
from mascope_signal.compute import get_sum_signal
from mascope_signal.noise import max_peak_snr
from mascope_signal.runtime import runtime


class InsufficientPeaksError(ValueError):
    """Raised when a spectrum has too few quality peaks to fit instrument functions.

    Subclasses ValueError so existing ``except ValueError`` handlers keep working,
    while allowing callers to distinguish this recoverable, low-signal case (e.g. to
    fall back to blank-measurement handling) from other value errors.
    """


# Precompute sigma multiplier for peak generation
SIGMA_MULTIPLIER = 2 * np.sqrt(2 * np.log(2))
# Minimum number of peaks required to evaluate instrument functions
MIN_NUM_PEAKS = 3

#: Minimum samples inside the +/-dmz window for a peak to be fittable. The
#: skewed-Gaussian model has three free parameters for an Orbitrap (amplitude,
#: centre, sigma), so anything under this cannot constrain a fit - and a
#: single-sample window makes lmfit raise on min == max bounds.
MIN_REGION_POINTS = 5
NOISE_THRESHOLD_FACTOR = 5
AMBIENT_SNR_THRESHOLD = 100
AMBIENT_R_SQ_THRESHOLD = 0.85


def r_tof(mz: float | np.ndarray, a: float, b: float):
    """Calculate TOF resolution function

    :param mz: mz value(-s)
    :type mz: float or ndarray
    :param a: empirical coefficient
    :type a: float
    :param b: empirical coefficient
    :type b: float
    :return: resolution function value(-s)
    :rtype: float or ndarray
    """
    return mz / (a * mz + b)


def r_orbi(
    mz: float | np.ndarray,
    a: float,
):
    """Calculate Orbitrap resolution function

    :param mz: mz value(-s)
    :type mz: float or ndarray
    :param a: empirical coefficient
    :type a: float
    :return: resolution function value(-s)
    :rtype: float or ndarray
    """
    return a / np.sqrt(mz)


def fit_instrument_functions(filename: str, dmz=0.5, r_sq_thres=0.95) -> tuple:
    """Calculate instrument functions

    Compute the median peak shape from the normalized peak shapes (p_ys).
    Calculate the resolution function from pairs of peak positions (p_mzs) and FWHM (p_fwhms).

    Structure of the returned statistics:
    stats
        | peakshape
            | num_of_peaks: number of peak used for peakshape estimation
        | resolution_function
            | mz: m/z values of the peak used to fit resolution function
            | fwhm: widths of the peak used to fit resolution function

    :param filename: Sample file name
    :type filename: str
    :param dmz: m/z window width for peak selection, defaults to 0.5
    :type dmz: float, optional
    :param r_sq_thres: R-squared threshold for peak fitting, defaults to 0.95
    :type r_sq_thres: float, optional
    :raises ValueError: Not enough peaks for instrument function estimation
    :return: Tuple containing the peak shape as dict, resolution function as partial, statistics as dict
    :rtype: tuple
    """
    instrument_type = get_instrument_type(filename)

    sum_signal = get_sum_signal(filename)
    spec = sum_signal.values
    mz = sum_signal.mz.values

    effective_r_sq_thres = r_sq_thres
    if instrument_type == "tof":
        is_ambient, signal_to_noise = _is_ambient_tof_spectrum(spec)
        if is_ambient:
            effective_r_sq_thres = min(r_sq_thres, AMBIENT_R_SQ_THRESHOLD)
            if effective_r_sq_thres != r_sq_thres:
                # Routine data-driven parameter adjustment
                runtime.logger.info(
                    "Ambient TOF file detected: overriding r_sq_thres "
                    f"from {r_sq_thres:.3f} to {effective_r_sq_thres:.3f} "
                    f"(SNR={signal_to_noise:.2f}, ambient threshold={AMBIENT_SNR_THRESHOLD})."
                )

    # Get x-domain, normalized peak shapes and associated peak positions and FWHMs
    p_x, p_ys, p_mzs, p_fwhms = _process_peak_shapes(
        mz, spec, instrument_type, dmz, effective_r_sq_thres
    )
    # Check if there are enough peaks for peak shape estimation
    if len(p_mzs) < MIN_NUM_PEAKS:
        # No log here: InsufficientPeaksError is an expected data condition
        # handled by the caller (e.g. treated as a blank measurement)
        error_message = "Not enough quality peaks to evaluate instrument functions"
        raise InsufficientPeaksError(error_message)

    peak_shape, ps_stats = _calculate_peakshape(p_x, p_ys)
    resolution_function, resfun_stats = _fit_resolution_function(
        instrument_type, p_mzs, p_fwhms
    )

    # Merge peakshape and resolution function statistics
    stats = ps_stats | resfun_stats

    return peak_shape, resolution_function, stats


def _is_ambient_tof_spectrum(spec: np.ndarray) -> tuple[bool, float]:
    """Detect if TOF spectrum should be treated as ambient using SNR.

    Uses the same MAD-based SNR measure as TOF blank detection - the shared
    ``max_peak_snr`` - but with an ambient-specific threshold, and it reads a
    missing noise floor the opposite way: a spectrum whose peaks are all the
    same height has an unbounded ratio here, not a blank one.

    :param spec: Spectrum counts / intensity
    :type spec: np.ndarray
    :return: Whether the spectrum is ambient, and the signal-to-noise ratio it
        was judged on
    :rtype: tuple[bool, float]
    """
    peak_indices, _ = find_peaks(spec)
    peak_heights = spec[peak_indices]

    signal_to_noise = max_peak_snr(peak_heights, NOISE_THRESHOLD_FACTOR)
    if signal_to_noise is None:
        # No noise floor to divide by, so the tallest peak is unbounded against
        # it - unless it does not rise above zero either, which is no signal at
        # all. A spectrum with no peaks never lands here: the shared measure
        # scores it 0.0, which is what keeps np.max() off an empty array.
        signal_to_noise = float("inf") if np.max(peak_heights) > 0 else 0.0

    return signal_to_noise < AMBIENT_SNR_THRESHOLD, signal_to_noise


def _process_peak_shapes(
    mz: np.ndarray,
    spec: np.ndarray,
    instrument_type: str,
    dmz: float,
    r_sq_thres: float,
    n_peaks=50,
) -> tuple:
    """Calculate normalized peak shapes and their parameters from a given spectrum

    1. Find indices of the potential peaks in the spectrum.
    2. Pick several peaks (50 by default) with the highest intensity.
    3. Predefine the common domain/x-scale for the peaks.
    4. Process each peak:
        a) Select and normalize narrow regions around the peak.
        b) Fit skewed Gaussian (+ baseline in case of TOF) in the region.
        c) Drop the peak if the fitting error exceeds a threshold.
        d) Extract fitted peak parameters: center, full-width half maximum (FWHM), sigma, height.
        e) Normalize peak region by these parameters.
        f) Interpolate the region into the predefined domain.
        g) Store refined peak shape (p_ys), raw (p_mzs) and fitted peak positions, fitted peak FWHM (p_fwhms).
    5. Filter out p_ys with centers of mass too far from median values among all peak shapes.

    :param mz: Spectrum m/z values
    :type mz: np.ndarray
    :param spec: Spectrum counts / intensity
    :type spec: np.ndarray
    :param instrument_type: Spectrometer type, tof or orbi
    :type instrument_type: str
    :param dmz: m/z window width for peak selection
    :type dmz: float
    :param r_sq_thres: R-squared threshold for peak fitting
    :type r_sq_thres: float
    :param n_peaks: Number of peaks with the highest intensity used in calculations, defaults to 100
    :type n_peaks: int
    :return: Tuple containing p_x, p_ys, p_mzs, and p_fwhms
    :rtype: tuple
    """
    # `distance` is the minimum peak separation in SAMPLES, so it must be at
    # least 1. On a spectrum whose own m/z spacing is coarser than dmz the
    # ratio is below 1 and int() floors it to 0, which find_peaks rejects. A
    # spacing that is NaN (an all-NaN or single-point m/z axis) floors to
    # nothing at all and int() raises. Both are the same condition - dmz is
    # finer than the data - and both mean "no separation to enforce", so clamp
    # to 1 rather than failing the whole instrument-function fit.
    median_spacing = np.median(np.diff(mz)) if mz.size > 1 else np.nan
    if not np.isfinite(median_spacing) or median_spacing <= 0:
        distance = 1
    else:
        distance = max(1, int(dmz / median_spacing))
    peak_indices = _choose_peaks(spec, distance=distance, n_peaks=n_peaks)

    p_x = np.linspace(-10, 10, 101)
    p_ys, p_mzs, p_fwhms, p_centers = [], [], [], []

    for p in peak_indices:
        p_mz_center = mz[p]

        # Select a narrow region (peak center +/- dmz) of the spectrum around the peak
        region_mask = np.where((mz > p_mz_center - dmz) & (mz < p_mz_center + dmz))
        p_spec = spec[region_mask]
        p_mz = mz[region_mask]

        if p_mz.size < MIN_REGION_POINTS:
            # The +/-dmz window holds too few samples to fit a peak shape. This
            # happens when the spectrum's own m/z spacing is coarser than dmz:
            # a single-sample window collapses the p_center bounds to
            # min == max and lmfit refuses the fit outright. Dismissing the
            # peak keeps that a data condition - too few quality peaks, handled
            # by the caller as a blank measurement - rather than a hard failure
            # of an otherwise readable file.
            continue

        p_height = spec[p]
        if np.max(p_spec) > p_height:
            # 'p' is not the biggest peak in range, dismiss
            continue

        # Normalize peak region: mz around 0 and spec to range [0, 1]
        p_mz_norm = p_mz - p_mz_center
        p_spec_norm = p_spec / p_height

        # Fit peak in the region
        fit = _fit_gaussian(instrument_type, dmz, p_mz_norm, p_spec_norm)
        p_spec_norm_fit = fit.eval_components()["p_"]

        if fit.rsquared < r_sq_thres:
            # fitting error too large, dismiss
            continue

        # Remove junk peaks arond main one
        noise_mask = np.where(fit.best_fit < 1e-9)
        p_spec_norm[noise_mask] = 0

        # Get peak top location
        if instrument_type == "tof":
            # Remove baseline if TOF
            p_spec_norm -= fit.eval_components()["bkg_"]
            p_spec_norm[np.where(p_spec_norm < 0)] = 0

        top_y = np.max(p_spec_norm_fit)
        top_x = _calculate_center_of_mass(p_mz_norm, p_spec_norm_fit)

        # Get and store Gaussian peak sigma and width
        try:
            p_fwhm = _calculate_fwhm(p_mz_norm, p_spec_norm_fit)
            p_sigma = p_fwhm / SIGMA_MULTIPLIER
        except ValueError:
            continue

        # Scale peak to width sigma=1
        p_mz_norm /= p_sigma
        # Refine peak position and height
        p_mz_norm -= top_x
        p_spec_norm /= top_y

        # Interpolate the normalized (both width and height) peak into predefined domain "p_x"
        p_y = np.interp(p_x, p_mz_norm, p_spec_norm, left=0, right=0)

        if np.all(np.isnan(p_y)):
            continue

        # Store peak positions, widths, and refined peak shape
        p_mzs.append(p_mz_center)
        p_centers.append(top_x)
        p_fwhms.append(p_fwhm)
        p_ys.append(p_y)

    # Clean shifted outliers
    p_centers = np.array(p_centers)
    center_mad = np.median(np.abs(p_centers - np.median(p_centers)))
    non_outlier_mask = np.where(
        np.abs(p_centers - np.median(p_centers)) < 3 * center_mad
    )[0]

    p_fwhms = [p_fwhms[i] for i in non_outlier_mask]
    p_ys = [p_ys[i] for i in non_outlier_mask]
    p_mzs = [p_mzs[i] for i in non_outlier_mask]

    return p_x, p_ys, p_mzs, p_fwhms


def _fit_gaussian(instrument_type, dmz, x: np.ndarray, y: np.ndarray) -> ModelResult:
    """Fit the spectrum range with a skewed Gaussian peak-shape using lmfit

    :param x: mz scale
    :type x: array-like
    :param y: counts
    :type y: array-like
    :return: ModelResult (see lmfit doc)
    :rtype: ModelResult
    """
    # Initialize fitting parameters for the main peak
    model = SkewedGaussianModel(prefix="p_")
    params = model.make_params()
    if instrument_type == "tof":
        # Fitting parameters for the background
        knot_xvals = np.array([-dmz, -dmz / 2, -dmz / 3, dmz / 3, dmz / 2, dmz])
        bkg = SplineModel(prefix="bkg_", xknots=knot_xvals)
        params.update(bkg.guess(y, x))
        # Total model
        model = model + bkg

    max_ind = np.argmax(y)
    params.add("p_amplitude", value=0.8 * y[max_ind], min=0, max=y[max_ind])
    params.add("p_center", value=x[max_ind], min=min(x), max=max(x))

    if instrument_type == "orbi":
        # Keep symmetric Gaussian
        params.add("p_gamma", value=0, vary=False)
        params.add("p_sigma", value=dmz / 5 / SIGMA_MULTIPLIER)
    if instrument_type == "tof":
        # Allow for slight skewed Gaussian
        params.add("p_gamma", value=0.1, min=0)
        params.add("p_sigma", value=0.01)

    fit = model.fit(y, params, x=x)
    return fit


def _choose_peaks(spec: np.ndarray, distance: int, n_peaks=100) -> np.ndarray:
    """Select peaks from a spectrum based on a specified quartile threshold

    This function finds peaks in a given spectrum and returns n_peaks highest.

    :param spec: The spectrum data from which peaks are to be selected
    :type spec: np.ndarray
    :param distance: Required minimal horizontal distance (>= 1) in samples between
        neighbouring peaks.
    :type distance: int
    :param n_peaks: Number of peaks to return, defaults to 100
    :type n_peaks: int, optional
    :return: Array of indices where peaks are located in the spectrum
    :rtype: np.ndarray
    """
    peak_indices, _ = find_peaks(spec, distance=distance)

    # Extract the values at the peak indices
    peak_values = spec[peak_indices]

    # Sort the peak values in descending order and get the indices of the sorted values
    sorted_indices = np.argsort(peak_values)[::-1]

    # Select the top n_peaks highest peaks (or all if less than n_peaks)
    top_n_indices = sorted_indices[:n_peaks]

    # Get the corresponding peak indices in the original spectrum
    top_n_peak_indices = peak_indices[top_n_indices]

    return top_n_peak_indices


def _calculate_center_of_mass(x: np.ndarray, y: np.ndarray) -> float:
    """Calculate the center of mass for given x and y values

    :param x: Array of x-values of the peak
    :type x: np.ndarray
    :param y: Array of y-values of the peak
    :type y: np.ndarray
    :return: The center of mass of the distribution
    :rtype: float
    """
    center_of_mass = np.sum(x * y) / np.sum(y)
    return center_of_mass


def _calculate_fwhm(x: np.ndarray, y: np.ndarray) -> float:
    """Calculate FWHM of the peak

    :param x: Array of x-values of the peak
    :type x: np.ndarray
    :param y: Array of y-values of the peak
    :type y: np.ndarray
    :raises ValueError: If half maximum is not crossed on both sides of the peak
    :return: FWHM value
    :rtype: float
    """
    peak_index = np.argmax(y)
    peak_value = y[peak_index]

    half_max = peak_value / 2.0

    # Find indices where y crosses the half maximum
    indices_below = np.where(y[:peak_index] < half_max)[0]
    indices_above = np.where(y[peak_index:] < half_max)[0] + peak_index

    # Interpolate to find the exact crossing points
    if indices_below.size > 0 and indices_above.size > 0:
        f_left = interp1d(
            y[indices_below[-1] : peak_index + 1], x[indices_below[-1] : peak_index + 1]
        )
        f_right = interp1d(
            y[peak_index : indices_above[0] + 1],
            x[peak_index : indices_above[0] + 1],
            kind="slinear",
        )

        x_left = f_left(half_max)
        x_right = f_right(half_max)

        fwhm = x_right - x_left
    else:
        raise ValueError(
            "Cannot calculate FWHM: half maximum not crossed on both sides of the peak"
        )

    return fwhm


def _calculate_peakshape(p_x: np.ndarray, p_ys: np.ndarray) -> tuple:
    """Calculate the median peak shape array from a 2D array of peaks

    :param p_x: array of normalized x-values corresponding to the peaks
    :type p_x: np.ndarray
    :param p_ys: 2D array where each row represents the y-values of a peak
    :type p_ys: np.ndarray
    :raises ValueError: If the median peak shape is empty (all NaN values)
    :return: Tuple with dictionaries containing x and y values of the median peak shape
             and statistics
    :rtype: tuple
    """
    num_of_peaks = len(p_ys)
    if num_of_peaks < 10:
        # Routine for low-signal files
        runtime.logger.info(
            f"Only {num_of_peaks} peaks will be used to estimate median peak shape!"
        )
    else:
        runtime.logger.info(f"Peak shape will be averaged from {num_of_peaks} peaks")
    stats = {"num_of_peaks": num_of_peaks}

    # Calculate median peak shape
    p_median = np.median(np.array([p_y for p_y in p_ys]), axis=0)

    is_peak_shape_empty = np.all(np.isnan(p_median))
    if is_peak_shape_empty:
        raise ValueError(
            "Failed to compute instrument functions: median peak shape is empty"
        )

    # Shift x-values so that the max y is at x=0
    max_index = np.argmax(p_median)
    x = p_x - p_x[max_index]

    # Normalize y-values to range from 0 to 1
    max_y = p_median[max_index]
    y = p_median / max_y

    # Normalize width
    fwhm = _calculate_fwhm(x, y)
    sigma = fwhm / SIGMA_MULTIPLIER
    y /= sigma

    peak_shape = {"x": x, "y": y}
    return peak_shape, {"peakshape": stats}


def _fit_resolution_function(
    instrument_type: str, p_mzs: list | np.ndarray, p_fwhms: list | np.ndarray, ndev=1
) -> tuple[partial, dict]:
    """Calculate the resolution function for a given instrument type

    The function fits a resolution function based on the type of the instrument
    and the provided peak position in m/z and FWHM values. If the fit has failed,
    return the resolution function with default coefficients

    :param instrument_type: type of the instrument, tof or orbi
    :type instrument_type: str
    :param p_mzs: List or array of m/z values
    :type p_mzs: list | np.ndarray
    :param p_fwhms: List of FWHM values corresponding to the m/z values
    :type p_fwhms: list | np.ndarray
    :param ndev: Number of standard deviations used to filter out FWHM outliers
    :type ndev: int, optional
    :return: Resolution function as partial and dictionary with m/z and fitted FWHM lists
    :rtype: tuple[partial, dict]
    """
    stats = {}
    p_mzs = np.array(p_mzs)
    p_fwhms = np.array(p_fwhms)

    if instrument_type == "tof":
        # log-space filtering
        log_f = np.log(p_fwhms)
        log_f_med = np.median(log_f)
        lof_f_mad = np.median(np.abs(log_f - log_f_med)) or 1e-9
        is_outlier = np.abs(log_f - log_f_med) >= 4 * lof_f_mad
    else:
        # Fit FWHM vs m/z pairs
        p_fwhms_fit = _fit_fwhm(instrument_type, p_mzs, p_fwhms)
        residuals = p_fwhms - p_fwhms_fit
        std_dev = np.std(residuals)
        is_outlier = (residuals > ndev * std_dev) | (residuals < -ndev * std_dev)

    # Remove outliers
    p_fwhms_filt = p_fwhms[~is_outlier]
    mass = p_mzs[~is_outlier]

    resolution = mass / p_fwhms_filt

    stats["mz"] = mass.tolist()
    stats["fwhm"] = p_fwhms_filt.tolist()

    # Fit resolution function based on the instrument type
    try:
        if instrument_type == "tof":
            meta = _fit_tof_rational(mass, p_fwhms_filt)
            a = meta["a"]
            b = meta["b"]
            resolution_function = partial(r_tof, a=a, b=b)
            stats.update(
                {
                    "model": "rational_polynome",
                    "coefficients": [a, b],
                    "method": meta["method"],
                    "dynamic_range": meta["dynamic_range"],
                    "n_points": meta["n_points"],
                }
            )
            runtime.logger.info(
                f"TOF resolution a={a:.3e} b={b:.3e} "
                f"dyn_range={meta['dynamic_range'] if meta['dynamic_range'] is not None else 'NA'} "
                f"points={meta['n_points']} method={meta['method']}"
            )

        else:
            fit_res = curve_fit(_inverse_sqrt, mass, resolution)
            a = fit_res[0][0]
            resolution_function = partial(r_orbi, a=a)
            stats.update(
                {
                    "model": "inverse_sqrt",
                    "coefficients": [a],
                    "method": "nonlinear",
                    "dynamic_range": None,
                    "n_points": int(mass.size),
                }
            )
            runtime.logger.info(f"Orbi resolution a={a:.3e} points={mass.size}")

    except ValueError as e:
        # No log here: the raised ValueError is logged by the caller's handler
        raise ValueError("Resolution function fitting failed") from e

    return resolution_function, {"resolution_function": stats}


def _fit_fwhm(
    instrument_type: str, p_mzs: np.ndarray, p_fwhms: np.ndarray
) -> np.ndarray:
    """Fit FWHM vs mz data points based on instrument type

    TOF data points are fitted with a line
    Orbi - with a 2nd order polynome

    :param instrument_type: type of the mass spectrometer
    :type instrument_type: str
    :param p_mzs: array of m/z values
    :type p_mzs: np.ndarray
    :param p_fwhms: azrray of FWHM values corresponding to p_mzs
    :type p_fwhms: np.ndarray
    :return: Fitted m/z values
    :rtype: np.ndarray
    """
    if instrument_type == "tof":
        regres = linregress(p_mzs, p_fwhms)
        p_fwhms_fit = _line(p_mzs, regres.slope, regres.intercept)
    else:
        coefs = np.polyfit(p_mzs, p_fwhms, 2)
        p_fwhms_fit = _polynome(p_mzs, *coefs)
    return p_fwhms_fit


def _huber_weights(residuals: np.ndarray, c: float = 1.345) -> np.ndarray:
    s = 1.4826 * np.median(np.abs(residuals - np.median(residuals)))
    if s <= 0:
        return np.ones_like(residuals)
    r = residuals / (c * s)
    w = np.ones_like(residuals)
    mask = np.abs(r) > 1
    w[mask] = 1 / np.abs(r[mask])
    return w


def _weighted_linear_fit(
    mass: np.ndarray, fwhm: np.ndarray, max_iter: int = 10
) -> tuple[float, float]:
    """Linear fit of FWHM vs m/z with Huber weights"""
    # Solve fwhm ~ alpha*mz + beta with iterative Huber weighting
    X = np.vstack([mass, np.ones_like(mass)]).T
    alpha, beta = np.polyfit(mass, fwhm, 1)  # init
    for _ in range(max_iter):
        pred = alpha * mass + beta
        w = _huber_weights(fwhm - pred)
        # Weighted least squares: solve (W^(1/2) X) coeffs = W^(1/2) y
        WX = X * np.sqrt(w[:, None])
        Wy = fwhm * np.sqrt(w)
        try:
            coeffs, *_ = np.linalg.lstsq(WX, Wy, rcond=None)
        except Exception:
            break
        alpha_new, beta_new = coeffs
        if np.allclose([alpha, beta], [alpha_new, beta_new], rtol=1e-4, atol=1e-6):
            alpha, beta = alpha_new, beta_new
            break
        alpha, beta = alpha_new, beta_new
    return alpha, beta


def _rational_dynamic_range(mass: np.ndarray, a: float, b: float) -> float:
    vals = _rational_polynome(mass, a, b)
    return (np.max(vals) - np.min(vals)) / (np.mean(vals) + 1e-12)


def _adjust_coefficients(
    mass: np.ndarray, a: float, b: float, target_dr: float = 0.05
) -> tuple[float, float]:
    """Adjust rational polynome coefficients to ensure reasonable dynamic range"""
    # Enforce positivity
    a = max(a, 1e-12)
    b = max(b, 1e-12)
    med_mz = np.median(mass)
    # Ensure intercept contributes early curvature
    min_b = 0.05 * a * med_mz
    if b < min_b:
        b = min_b
    # Increase curvature if dynamic range too low
    dr = _rational_dynamic_range(mass, a, b)
    if dr < target_dr:
        # Boost b to push out of premature plateau
        scale = target_dr / (dr + 1e-12)
        b *= min(scale, 10.0)
    return a, b


def _fit_tof_rational(mass: np.ndarray, fwhm: np.ndarray) -> dict:
    """Fit TOF resolution function with rational polynome"""
    if mass.size < 5:
        # Fallback: approximate plateau from median resolution
        if mass.size == 0:
            a = 1e-5
            b = 1.0
        else:
            resolution = mass / fwhm
            plateau = np.median(resolution)
            a = 1 / max(plateau, 1e-9)
            # Intercept so that resolution at min(mass) reduced by ~10%
            b = 0.1 * a * np.min(mass)
        return {
            "a": float(a),
            "b": float(b),
            "method": "fallback_small_sample",
            "n_points": int(mass.size),
            "dynamic_range": None,
        }

    # Linear fit of FWHM
    alpha, beta = _weighted_linear_fit(mass, fwhm)
    alpha = max(alpha, 1e-12)
    beta = max(beta, 1e-12)

    # Initial rational params
    a0, b0 = alpha, beta
    a0, b0 = _adjust_coefficients(mass, a0, b0)

    # Refine with curve_fit
    resolution = mass / fwhm
    try:
        popt, _ = curve_fit(
            _rational_polynome,
            mass,
            resolution,
            p0=(a0, b0),
            bounds=([1e-12, 1e-12], [1.0, np.inf]),
            maxfev=20_000,
        )
        a_fit, b_fit = popt
    except Exception:
        a_fit, b_fit = a0, b0
        refine_status = "nonlinear_failed"
    else:
        refine_status = "nonlinear_success"

    # Sanity adjustments
    a_final, b_final = _adjust_coefficients(mass, a_fit, b_fit)
    dr = _rational_dynamic_range(mass, a_final, b_final)

    # If after adjustment still near-flat but raw data vary more, inflate b
    raw_dr = (np.max(resolution) - np.min(resolution)) / (np.mean(resolution) + 1e-12)
    if dr < 0.02 and raw_dr > 0.05:
        b_final = max(b_final, 0.2 * a_final * np.median(mass))
        dr = _rational_dynamic_range(mass, a_final, b_final)

    return {
        "a": float(a_final),
        "b": float(b_final),
        "method": f"linear+{refine_status}",
        "n_points": int(mass.size),
        "dynamic_range": float(dr),
    }


# Fitting support functions
def _line(x, a, b):
    """Calculates the output of the linear function
    for given values of x (m/z), a (slope), and b (intercept)"""
    return a * x + b


def _polynome(x, a, b, c):
    """Calculates the output of the 2nd order polynomial
    for given values of x, a, b, and c"""
    return a * x**2 + b * x + c


def _rational_polynome(x, a, b):
    """Calculates the output of the rational polynome
    for given values of x, a, and b"""
    return x / (a * x + b)


def _inverse_sqrt(x, a):
    """Calculates the output of the inverse square root
    for given values of x and a"""
    return a / np.sqrt(x)
