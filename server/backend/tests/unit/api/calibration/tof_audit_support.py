"""
A TOF sample file on disk for the calibration audit tests.

A plain module rather than conftest.py content, so the tests can import it
(see ``calibration_test_support``).

The file's peaks sit at the TOF index a known mode-0 axis gives each of a few
negative-ion calibrants, and its stored axis is that one moved by a few ppm,
as a file the node has not fitted yet reads.
"""

import json
import os

import numpy as np
import xarray as xr

import mascope_file.name as m_name
from mascope_backend.api.controllers.calibration.lib import (
    tof_calibration_audit as audit,
)
from mascope_backend.api.models.calibration.calibration_pydantic_model import (
    CalibrationFitParams,
    TofCalibrationParams,
)
from mascope_backend.db import IonizationMechanism
from mascope_tofwerk.calibration import tof_to_mass


FILENAME = "AuditTof_2026.10.20_12h00m00s_audit"

#: The axis the peaks were placed by: ``i = p1 * sqrt(m) + p2``.
TRUE_FIT = {"mode": 0, "par": [10850.0, -18500.0]}
#: The axis the file carries: the true one read 6 ppm low.
STORED_FIT = {"mode": 0, "par": [10850.0 * (1 + 3e-6), -18500.0]}

RADICAL_ANION = IonizationMechanism(
    ionization_mechanism_id="radical-",
    ionization_mechanism="[M]-.",
    ionization_mechanism_polarity="-",
)

#: Five lines in all: NO3-, NO3-.HNO3, NO3-.(HNO3)2 and both bromide lines.
CALIBRANTS = ["NO3", "HN2O6", "H2N3O9", "Br"]
#: Two more, which take a set past five matches: HSO4- and HSO4-.H2SO4.
MORE_CALIBRANTS = ["HSO4", "H3S2O8"]

N_SCANS = 4


def isotopes(formulas=CALIBRANTS):
    """The calibrants' isotopes, as an audit set holds them."""
    return audit.isotopes_of(formulas, [RADICAL_ANION])


def params(**update) -> CalibrationFitParams:
    """The node's TOF parameters for a negative mode."""
    return CalibrationFitParams(
        calibration_collection_id="collection",
        ionization_mechanism_ids=[RADICAL_ANION.ionization_mechanism_id],
        polarity="-",
        **TofCalibrationParams().model_dump(),
    ).model_copy(update=update)


def resolution(mz):
    """A resolving power of 30,000 everywhere: one FWHM is 33 ppm, so a peak
    50 ppm from a calibrant is a neighbour of its own inside the refine
    window, and one 20 ppm from it overlaps it."""
    return np.full_like(np.asarray(mz, dtype=np.float64), 30000.0)


def tof_of(mz: float, fit: dict = TRUE_FIT) -> float:
    """The TOF index a mode-0 fit puts an m/z at."""
    p1, p2 = fit["par"]
    return p1 * np.sqrt(mz) + p2


def write_file(
    root,
    peaks: list[dict],
    *,
    sparsity: bool = True,
    record: dict | None = None,
) -> str:
    """Write a TOF sample file whose store holds these peaks.

    :param root: The filestore
    :param peaks: One dict per peak: ``mz`` (exact, placed by
        :data:`TRUE_FIT`), and optionally ``ppm`` (how far off it sits),
        ``area`` (its mean peak area), ``snr``, ``polarity``, ``weak``,
        ``satellite``, ``computed`` (whether the store holds its timeseries).
    :param sparsity: Whether the store has the ``sparsity`` variable, which
        stores written before it existed lack
    :param record: The stored calibration record; :data:`STORED_FIT`
    :return: The file's name
    """
    path = os.path.join(root, *_relative_path())
    os.makedirs(path, exist_ok=True)
    peaks = sorted(peaks, key=lambda peak: peak["mz"])
    tof = np.array(
        [tof_of(peak["mz"] * (1 + peak.get("ppm", 0.0) * 1e-6)) for peak in peaks]
    )
    mz = np.asarray(tof_to_mass(tof, STORED_FIT["mode"], STORED_FIT["par"]))
    area = np.array([peak.get("area", 100.0) for peak in peaks])
    computed = np.array([peak.get("computed", True) for peak in peaks])
    times = np.arange(N_SCANS, dtype=np.float64)
    areas = np.where(computed[:, None], area[:, None], np.nan) * np.ones(N_SCANS)
    data_vars = {
        "peak_areas": (("mz", "time"), areas),
        "peak_heights": (("mz", "time"), areas / 10),
        "sum_peak_areas": ("mz", area * N_SCANS),
        "sum_peak_heights": ("mz", area * N_SCANS / 10),
        "signal_to_noise": ("mz", np.array([p.get("snr", 50.0) for p in peaks])),
        "polarity": ("mz", np.array([p.get("polarity", "-") for p in peaks])),
        "is_weak": ("mz", np.array([p.get("weak", False) for p in peaks])),
        "is_satellite": ("mz", np.array([p.get("satellite", False) for p in peaks])),
        "is_timeseries_computed": ("mz", computed),
        "peak_id": ("mz", np.array([f"p{i}" for i in range(len(peaks))])),
    }
    if sparsity:
        data_vars["sparsity"] = ("mz", np.zeros(len(peaks)))
    xr.Dataset(data_vars, coords={"mz": mz, "tof": ("mz", tof), "time": times}).to_zarr(
        os.path.join(path, "peak_timeseries.zarr"), mode="w", consolidated=True
    )
    xr.Dataset(
        {"signal": (("mz", "time"), np.ones((8, N_SCANS)))},
        coords={"mz": np.linspace(10.0, 300.0, 8), "time": times},
    ).to_zarr(os.path.join(path, "signal.zarr"), mode="w", consolidated=True)
    with open(os.path.join(path, ".props"), "w") as props:
        json.dump(
            {
                "instrument_type": "tof",
                "polarity": "-",
                "mz_calibration": record if record is not None else STORED_FIT,
            },
            props,
        )
    return FILENAME


def _relative_path() -> list[str]:
    return [
        m_name.get_instrument_name(FILENAME),
        "2026.10.20",
        FILENAME,
    ]


def calibrant_peaks(formulas=CALIBRANTS, off: dict | None = None) -> list[dict]:
    """One peak per calibrant line, each where the true axis puts it.

    :param off: How far some lines sit off it, in ppm, by their exact m/z
        rounded to whole Th
    """
    off = off or {}
    lines = isotopes(formulas)
    lines = lines[lines.relative_abundance >= 0.15]
    return [{"mz": float(mz), "ppm": off.get(round(mz), 0.0)} for mz in lines.mz]


def snapshot(root) -> dict:
    """Every file under the filestore, with its size and modification time."""
    found = {}
    for directory, _, files in os.walk(root):
        for name in files:
            full = os.path.join(directory, name)
            stat = os.stat(full)
            found[os.path.relpath(full, root)] = (stat.st_size, stat.st_mtime_ns)
    return found
