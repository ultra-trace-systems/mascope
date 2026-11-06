import numpy as np
from scipy.optimize import fsolve, least_squares


#: The modes :func:`mz_calibrate` fits, each with its number of parameters:
#: mode 0, ``i = p1 * sqrt(m) + p2``, and mode 2, ``i = p1 * m**p3 + p2``,
#: where ``i`` is the TOF sample index. :func:`tof_to_mass` evaluates more.
FITTED_MODES = {0: 2, 2: 3}

#: The scale of p3 a mode-2 fit steps in (``least_squares``' ``x_scale``),
#: beside p1 and p2 at their own size. The three differ by four orders of
#: magnitude, and a step of one in all of them alike - the default - ends
#: the fit while p3 is still far off. A change of 1/ln(m) in p3 moves a
#: peak's index as far as a change of p1 by its own size does, and ln(m) is
#: 3 to 7 over the m/z calibrants sit at.
MODE2_P3_SCALE = 0.25

_COUNT_WORDS = {2: "two", 3: "three"}


def _residuals_mode0(par: np.ndarray, m: np.ndarray, tof: np.ndarray) -> np.ndarray:
    return par[0] * np.sqrt(m) + par[1] - tof


def _residuals_mode2(par: np.ndarray, m: np.ndarray, tof: np.ndarray) -> np.ndarray:
    return par[0] * np.power(m, par[2]) + par[1] - tof


def _fit_mode0(peak_tof: np.ndarray, exact_mz: np.ndarray) -> np.ndarray:
    """Mode 0's parameters, from the linear fit of the index against sqrt(m)."""
    # Initial guess: linear fit between sqrt(m) and tof
    X = np.vstack([np.sqrt(exact_mz), np.ones_like(exact_mz)]).T
    p_init, _, _, _ = np.linalg.lstsq(X, peak_tof, rcond=None)

    result = least_squares(
        _residuals_mode0,
        x0=p_init,
        args=(exact_mz, peak_tof),
        method="trf",
        loss="soft_l1",
        max_nfev=1000,
    )
    if not result.success:
        raise ValueError(f"Calibration failed: {result.message}")
    return result.x


def _fit_mode2(
    peak_tof: np.ndarray, exact_mz: np.ndarray, mode0: np.ndarray
) -> np.ndarray:
    """Mode 2's parameters, started from mode 0's solution and p3 = 0.5.

    Mode 0 is mode 2 at p3 = 0.5, so the fit starts where mode 0 ended and
    bends the axis from there, under the same soft-L1 loss.
    """
    p1, p2 = mode0
    result = least_squares(
        _residuals_mode2,
        x0=np.array([p1, p2, 0.5]),
        args=(exact_mz, peak_tof),
        method="trf",
        loss="soft_l1",
        x_scale=np.array([max(abs(p1), 1.0), max(abs(p2), 1.0), MODE2_P3_SCALE]),
        max_nfev=1000,
    )
    if not result.success:
        raise ValueError(f"Calibration failed: {result.message}")
    return result.x


def mz_calibrate(peak_tof, peak_mz, exact_mz, mode: int = 0):
    """
    Calibrate m/z values using known peak positions and exact m/z values.

    :param peak_tof: Array of observed time-of-flight (TOF) values for calibration peaks.
    :type peak_tof: array-like
    :param peak_mz: Array of observed m/z values for calibration peaks.
    :type peak_mz: array-like
    :param exact_mz: Array of exact m/z values for calibration peaks.
    :type exact_mz: array-like
    :param mode: The Tofwerk mode to fit, a key of :data:`FITTED_MODES`: 0,
        ``i = p1 * sqrt(m) + p2``, or 2, ``i = p1 * m**p3 + p2``.
    :type mode: int
    :raise ValueError: If the mode is not one this function fits.
    :raise ValueError: If fewer calibration points are provided than the mode
        has parameters: two for mode 0, three for mode 2.
    :raise ValueError: If the calibration optimization fails.
    :return: Tuple containing the mass calibration parameters and calibration statistics.
    :rtype: tuple(dict, dict)
    """
    peak_tof = np.asarray(peak_tof, dtype=np.float64)
    peak_mz = np.asarray(peak_mz, dtype=np.float64)
    exact_mz = np.asarray(exact_mz, dtype=np.float64)

    if mode not in FITTED_MODES:
        raise ValueError(
            f"Mass calibration mode {mode} cannot be fitted; "
            f"fitted: {', '.join(str(fitted) for fitted in FITTED_MODES)}."
        )
    n_parameters = FITTED_MODES[mode]
    if peak_tof.shape[0] < n_parameters:
        raise ValueError(
            f"At least {_COUNT_WORDS[n_parameters]} calibration points are "
            f"required for mode {mode}."
        )

    calibration_parameters = _fit_mode0(peak_tof, exact_mz)
    if mode == 2:
        calibration_parameters = _fit_mode2(peak_tof, exact_mz, calibration_parameters)

    mz_calibration_result = {
        "mode": mode,
        "par": calibration_parameters.tolist(),
    }

    # Calculate new m/z for each TOF using the calibration
    new_peak_mz = tof_to_mass(peak_tof, mode=mode, par=calibration_parameters)

    delta_mass_before_calibration = (peak_mz - exact_mz) / exact_mz * 1e6
    delta_mass_after_calibration = (new_peak_mz - exact_mz) / exact_mz * 1e6

    stats = {
        "mz": exact_mz,
        "new_mz": new_peak_mz,
        "pre_dmz": delta_mass_before_calibration,
        "post_dmz": delta_mass_after_calibration,
    }

    return mz_calibration_result, stats


def tof_to_mass(tof: np.ndarray, mode: int, par: list) -> float | np.ndarray:
    """Convert between sample indices and mass.

    :param tof: Values to convert
    :type tof: np.ndarray
    :param mode: Mass calibration function to use
    :type mode: int
    :param par: List containing the calibration parameters (number depends on mode)
    :type par: list
    """

    def solve_numerically(objective, tof_val):
        m_initial_guess = 1.0
        (m_solution,) = fsolve(objective, m_initial_guess, args=(tof_val,))
        return m_solution

    match mode:
        case 0:
            # from i(m) = p1 * np.sqrt(m) + p2
            return ((tof - par[1]) / par[0]) ** 2
        case 1:
            # from i(m) = p1/np.sqrt(m) + p2
            return (par[0] / (tof - par[1])) ** 2
        case 2:
            # from i(m) = p1 * np.power(m, p3) + p2
            return ((tof - par[1]) / par[0]) ** (1 / par[2])
        case 3:
            objective = lambda m, tof_val: (
                par[0] * np.sqrt(m) + par[1] + par[2] * (m - par[3]) ** 2 - tof_val
            )
            return np.vectorize(lambda tof_val: solve_numerically(objective, tof_val))(
                tof
            )
        case 4:
            objective = lambda m, tof_val: (
                par[0] * np.sqrt(m)
                + par[1]
                + par[2] * m**2
                + par[3] * m
                + par[4]
                - tof_val
            )
            return np.vectorize(lambda tof_val: solve_numerically(objective, tof_val))(
                tof
            )
        case 5:
            return par[0] * tof**2 + par[1] * tof + par[2]
        case _:
            raise ValueError(f"Unknown mass calibration mode: {mode}")
