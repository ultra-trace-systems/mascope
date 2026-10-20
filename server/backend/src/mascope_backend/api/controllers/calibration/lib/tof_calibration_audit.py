"""
Refit a TOF file's stored peaks the way the calibration node does, reading only.

The audit behind ``audit_tof_calibration`` (``mascope_backend.db.scripts``):
the TOF mass calibration plan (``docs/dev/tof_calibration.md``) judges every
calibrant set and model against a baseline, and the baseline has to come from
the node's own rules or it measures something else.

So nothing here re-implements the node. :class:`AuditCalibrationHandler` *is*
the node's ``TofCalibrationHandler``, with only what it reads from the file
and the database swapped for reads that write nothing:

- the peaks come from the stored ``peak_timeseries`` store, without the
  sparsity backfill a load of an old store performs;
- a candidate peak whose timeseries the store does not hold yet is ranked by
  its stored sum instead of having its timeseries computed and stored;
- the calibrants are a named set's isotopes, generated in memory as the
  collection would generate them, or a collection's from the database;
- the instrument's resolution function is read once per file;
- the total ion current, which only feeds the calibrant share, is not read.

Matching, the overlap filter, the one-reading-per-peak rule, the 15 ppm
residual selection, the exact subset search for five matches or fewer and the
fit itself are the handler's, unchanged.

A TOF fit is absolute (the m/z of a peak is ``tof_to_mass`` of its stored TOF
index), so any fit can be evaluated on any file's peaks after the fact: an
ion's error under a fit is the fit's m/z of the peak it matched, against the
ion's exact m/z.
"""

import asyncio
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import xarray as xr

import mascope_file.io as m_io
import mascope_signal.compute as m_compute
from mascope_backend.api.controllers.calibration.lib.calibration_mz_fit import (
    TofCalibrationHandler,
)
from mascope_backend.api.controllers.target.lib.compute.target_ions_compute import (
    generate_target_ions_from_composition,
)
from mascope_backend.api.models.calibration.calibration_pydantic_model import (
    CalibrationFitParams,
)
from mascope_backend.api.models.target.compounds.target_compound_pydantic_model import (
    TargetCompoundBase,
)
from mascope_backend.db import IonizationMechanism
from mascope_tofwerk.calibration import mz_calibrate, tof_to_mass
from mascope_tools.composition.utils import composition_mass, parse_composition


#: The calibration models an audit can fit, by Tofwerk mode number. Mode 2
#: (``i = p1 * m**p3 + p2``) joins once ``mascope_tofwerk.calibration`` fits
#: it (milestone 2 of the plan); ``tof_to_mass`` already evaluates it.
MODELS: dict[int, Callable] = {0: mz_calibrate}

#: Default mass bands, as edges in Th: ``[40, 100)``, ..., ``[500, inf)``,
#: and what lies below the first edge as a band of its own.
DEFAULT_BAND_EDGES = (40.0, 100.0, 150.0, 200.0, 300.0, 500.0)

#: Default cluster series for the negative-ion reagents in use, each a core
#: ion and the neutral it clusters with: HSO4-.(H2SO4)n, NO3-.(HNO3)n and
#: Br-.(H2O)n, n = 0..4.
DEFAULT_CLUSTER_SERIES: dict[str, dict] = {
    "HSO4-.(H2SO4)n": {"core": "HSO4", "unit": "H2SO4", "n": 4, "charge": "-"},
    "NO3-.(HNO3)n": {"core": "NO3", "unit": "HNO3", "n": 4, "charge": "-"},
    "Br-.(H2O)n": {"core": "Br", "unit": "H2O", "n": 4, "charge": "-"},
}

#: The family lattice: CcHhOo neutrals with an integer number of double bonds
#: and rings from 0 to this. Members of one nominal mass then differ by O
#: against CH4, 36.4 mDa, and nothing else. Four or more would let three
#: O-for-CH4 swaps and one C-for-H12 swap (seven double bonds less) part two
#: members by 15.3 mDa, too close to tell at a TOF's resolution which one a
#: peak is.
DEFAULT_FAMILY_DBE_MAX = 3
#: A strong peak counts towards a family offset within this of its nearest
#: member; further off it is taken for something the family does not hold.
DEFAULT_FAMILY_WINDOW_PPM = 30.0
#: The peaks a family offset is read from: S/N at least this, the plan's
#: "strong peaks".
DEFAULT_FAMILY_SNR = 20.0
#: The lattice reaches this m/z, past the top of every TOF axis in the fleet.
_FAMILY_MZ_MAX = 2000.0
#: The neutral whose ion under a mechanism tells the mechanism's mass shift.
#: A mechanism is written around one molecule, so the shift is the same for
#: every neutral it ionises, and any CHO neutral will do.
_FAMILY_REFERENCE = "C10H20O10"
#: An adduct's isotope line is a lattice of its own when it is at least this
#: likely: both bromine lines, and not the reference's own 13C line.
_FAMILY_LINE_ABUNDANCE = 0.3


# --- Peaks, read only ---


@dataclass
class StoredPeaks:
    """One file's kept peaks as a refit reads them, and how it read them."""

    filename: str
    #: The peaks a load leaves in (not weak, not satellites), every polarity.
    peaks: xr.Dataset
    #: The scans a peak's intensity is averaged over, as the node reads them.
    scan_times: np.ndarray
    #: Peaks ranked by their stored sum because the store holds no
    #: timeseries for them, over every refit of the file.
    estimated: set = field(default_factory=set)


def read_stored_peaks(filename: str) -> StoredPeaks:
    """Read a file's kept peaks without writing anything.

    The peaks are on the axis the file carries now, which is what a
    re-calibration fits on. The node fits a new file on the converter's axis
    instead; the fit depends on the TOF index alone, and the axis only
    decides which peaks fall in the refine window and the pre-fit error the
    subset search breaks ties by.

    Synchronous: it reads the filestore, so it runs in a worker thread.

    :param filename: Sample file filename
    :raises FileNotFoundError: When the file has no peak store.
    """
    # The store as it is: ``load_peak_data`` would first add a sparsity
    # variable to a store written before it existed
    store = m_io.load_array(filename, var="peak_timeseries")
    bad = (store.is_weak | store.is_satellite).values
    peaks = store.isel(mz=np.flatnonzero(~bad))
    scan_times = m_compute.get_scan_timestamps(filename)
    return StoredPeaks(filename=filename, peaks=peaks, scan_times=scan_times)


# --- The handler ---


class AuditCalibrationHandler(TofCalibrationHandler):
    """The node's TOF calibration handler on stored data, writing nothing.

    :param stored: The file's peaks, from :func:`read_stored_peaks`
    :param params: The fit parameters, the node's for the file's mode
    :param isotopes: The calibrants: target isotopes as the database answers
        them, plus an ``ion_label`` column that names each line in a report
    :param resolution_function: The instrument's, for the overlap filter
    :param model: A key of :data:`MODELS`
    """

    def __init__(
        self,
        stored: StoredPeaks,
        params: CalibrationFitParams,
        isotopes: pd.DataFrame,
        resolution_function: Callable,
        model: int = 0,
    ):
        super().__init__(stored.filename, params, notification=None)
        if model not in MODELS:
            raise ValueError(
                f"Model {model} cannot be fitted; fittable: {sorted(MODELS)}"
            )
        self._stored = stored
        self._isotopes = isotopes
        self._resolution_function = resolution_function
        self.model = model
        #: Every reading that passed the node's match filters, retained by
        #: the fit or not: what presence and held-out accuracy are read from.
        self.good_matches: pd.DataFrame | None = None

    @property
    def _has_peaks(self):
        return self._stored.peaks.mz.size > 0

    async def _resolve_calibration_isotopes(self) -> pd.DataFrame | None:
        if self._isotopes.empty:
            self._nothing_to_calibrate_against(
                "The calibrant set has no isotopes for this ionization mode."
            )
            return None
        return self._isotopes.copy()

    def _total_ion_current(self) -> float:
        return float("nan")

    def _peaks_to_match(self, stream: str | None = None):
        return self._stored.peaks

    async def _filter_overlapping_peaks(
        self, peak_mzs: np.ndarray, stream: str | None = None
    ) -> np.ndarray:
        if peak_mzs.size <= 1:
            return peak_mzs
        return self._remove_overlapping_mzs(peak_mzs, self._resolution_function)

    async def _load_peak_timeseries(
        self, peak_mzs: np.ndarray, stream: str | None = None
    ):
        return await asyncio.to_thread(self._stored_timeseries, peak_mzs)

    def _stored_timeseries(self, peak_mzs: np.ndarray) -> xr.Dataset:
        """The candidates' timeseries over the scans, as the node reads them.

        The node computes and stores a timeseries the store does not hold
        yet. Here such a peak gets its stored sum spread evenly over the
        store's scans instead: the mean the node ranks candidates by, as near
        as the store tells it without reading the file.
        """
        rows = self._stored.peaks.sel(mz=np.unique(peak_mzs), method="nearest")
        _, first = np.unique(rows.mz.values, return_index=True)
        rows = rows.isel(mz=np.sort(first))
        missing = ~rows.is_timeseries_computed.values.astype(bool)
        if missing.any():
            areas = rows.peak_areas.values.copy()
            n_times = max(rows.sizes["time"], 1)
            areas[missing, :] = (rows.sum_peak_areas.values[missing] / n_times)[:, None]
            rows = rows.assign(peak_areas=(rows.peak_areas.dims, areas))
            self._stored.estimated.update(rows.mz.values[missing].tolist())
        return rows.sel(time=self._stored.scan_times, method="nearest")

    async def _match_calibration_compounds(self, target_isotopes_df, stream=None):
        matches, good = await super()._match_calibration_compounds(
            target_isotopes_df, stream
        )
        self.good_matches = good
        return matches, good

    def _fit_matches(self, matches_df: pd.DataFrame) -> tuple[dict, dict]:
        return MODELS[self.model](
            matches_df["sample_peak_tof"],
            matches_df["sample_peak_mz"],
            matches_df["mz"],
        )


@dataclass
class Refit:
    """What one refit of one file came to."""

    fit: dict | None
    warning: str | None
    error: str | None
    #: Every reading that passed the match filters: ``ion_label``, ``mz``
    #: (exact), ``sample_peak_tof``, ``sample_peak_mz``.
    readings: pd.DataFrame
    #: The ``ion_label`` of each reading the fit kept.
    retained: frozenset

    @property
    def span(self) -> tuple[float, float] | None:
        """The exact m/z of the lowest and highest calibrant the fit kept."""
        kept = self.readings[self.readings.ion_label.isin(self.retained)]
        if self.fit is None or kept.empty:
            return None
        return float(kept.mz.min()), float(kept.mz.max())


_READING_COLUMNS = ["ion_label", "mz", "sample_peak_tof", "sample_peak_mz"]


async def refit(
    stored: StoredPeaks,
    params: CalibrationFitParams,
    isotopes: pd.DataFrame,
    resolution_function: Callable,
    model: int = 0,
) -> Refit:
    """Fit one file under one calibrant set and model, as the node would."""
    handler = AuditCalibrationHandler(
        stored, params, isotopes, resolution_function, model=model
    )
    await handler.fit()
    good = handler.good_matches
    readings = (
        pd.DataFrame(columns=_READING_COLUMNS)
        if good is None or good.empty
        else good[_READING_COLUMNS].reset_index(drop=True)
    )
    retained = frozenset()
    if handler.fit_result is not None:
        retained = frozenset(
            row["ion_label"] for row in handler.stats if "ion_label" in row
        )
    return Refit(
        fit=handler.fit_result,
        warning=handler.warning,
        error=handler.error,
        readings=readings,
        retained=retained,
    )


async def match_only(
    stored: StoredPeaks,
    params: CalibrationFitParams,
    isotopes: pd.DataFrame,
    resolution_function: Callable,
) -> pd.DataFrame:
    """Match ions as the node matches calibrants, without fitting.

    How the cluster series are found: by the node's rule, so that a series
    member and a calibrant are read alike.
    """
    if isotopes.empty or stored.peaks.mz.size == 0:
        return pd.DataFrame(columns=_READING_COLUMNS)
    handler = AuditCalibrationHandler(stored, params, isotopes, resolution_function)
    _, good = await handler._match_calibration_compounds(isotopes)
    if good.empty:
        return pd.DataFrame(columns=_READING_COLUMNS)
    return good[_READING_COLUMNS].reset_index(drop=True)


def ppm_error(tof: np.ndarray, exact_mz: np.ndarray, fit: dict) -> np.ndarray:
    """Each peak's m/z error under a fit, in ppm of the exact m/z."""
    tof = np.asarray(tof, dtype=np.float64)
    exact_mz = np.asarray(exact_mz, dtype=np.float64)
    observed = np.asarray(tof_to_mass(tof, fit["mode"], fit["par"]), dtype=np.float64)
    return 1e6 * (observed - exact_mz) / exact_mz


def axis_difference_ppm(
    fit: dict, other: dict, tof_span: tuple[float, float], points: int = 201
) -> float:
    """The largest distance between two fits' axes over a TOF span, in ppm."""
    tof = np.linspace(tof_span[0], tof_span[1], points)
    mine = np.asarray(tof_to_mass(tof, fit["mode"], fit["par"]), dtype=np.float64)
    theirs = np.asarray(tof_to_mass(tof, other["mode"], other["par"]), dtype=np.float64)
    return float(np.max(np.abs(1e6 * (mine - theirs) / theirs)))


# --- Calibrant sets and ions, generated in memory ---


def _label(ion_formula: str, mz: float) -> str:
    return f"{ion_formula} {mz:.4f}"


def isotopes_of(
    formulas: Iterable[str],
    mechanisms: Sequence[IonizationMechanism],
) -> pd.DataFrame:
    """The low-resolution isotopes of neutrals under mechanisms, in memory.

    What the database would hold for these compounds entered in a collection
    of a mode with these mechanisms: the same generator, not stored.

    :raises ValueError: For a formula no mechanism makes an ion of.
    """
    rows = []
    for formula in formulas:
        ions, isotopes = generate_target_ions_from_composition(
            TargetCompoundBase(target_compound_formula=formula), list(mechanisms)
        )
        if not ions:
            raise ValueError(
                f"{formula!r} makes no ion under the mechanisms "
                f"{', '.join(m.ionization_mechanism for m in mechanisms)}"
            )
        ion_formula = {ion.target_ion_id: ion.target_ion_formula for ion in ions}
        for isotope in isotopes:
            if isotope.resolution != "LOW":
                continue
            rows.append(
                {
                    "target_isotope_id": isotope.target_isotope_id,
                    "target_ion_id": isotope.target_ion_id,
                    "mz": float(isotope.mz),
                    "relative_abundance": float(isotope.relative_abundance),
                    "resolution": isotope.resolution,
                    "target_isotope_formula": isotope.target_isotope_formula,
                    "ion_label": _label(
                        ion_formula[isotope.target_ion_id], float(isotope.mz)
                    ),
                }
            )
    return pd.DataFrame(rows)


def label_isotopes(
    isotopes: pd.DataFrame, ion_formulas: dict[str, str]
) -> pd.DataFrame:
    """Name the database's isotopes of a collection by ion and m/z."""
    return isotopes.assign(
        ion_label=[
            _label(ion_formulas.get(ion_id, ion_id), mz)
            for ion_id, mz in zip(isotopes.target_ion_id, isotopes.mz)
        ]
    )


def series_formulas(core: str, unit: str, n: int) -> list[tuple[str, int]]:
    """The neutral formulas of a cluster series core.(unit)k, k = 0..n."""
    core_counts = parse_composition(core)
    unit_counts = parse_composition(unit)
    formulas = []
    for k in range(n + 1):
        counts = dict(core_counts)
        for element, count in unit_counts.items():
            counts[element] = counts.get(element, 0) + k * count
        formulas.append(
            (
                "".join(
                    f"{element}{count if count > 1 else ''}"
                    for element, count in sorted(counts.items())
                    if count
                ),
                k,
            )
        )
    return formulas


def series_isotopes(series: dict[str, dict], polarity: str) -> pd.DataFrame:
    """The isotopes of the cluster series of a polarity, labelled by series.

    A member is the core radical and its units taking the charge: an electron
    gained (``[M]-.``) or lost (``[M]+.``), so ``core`` is written as the
    neutral the ion is made of (``HSO4`` for HSO4-).
    """
    frames = []
    for name, spec in series.items():
        if spec.get("charge", "-") != polarity:
            continue
        mechanism = IonizationMechanism(
            ionization_mechanism_id=f"series{polarity}",
            ionization_mechanism=f"[M]{polarity}.",
            ionization_mechanism_polarity=polarity,
        )
        for formula, k in series_formulas(spec["core"], spec["unit"], spec["n"]):
            isotopes = isotopes_of([formula], [mechanism])
            frames.append(isotopes.assign(series=name, series_n=k))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


# --- Families ---


@dataclass(frozen=True)
class Family:
    """A composition-free yardstick: CcHhOo under one mechanism."""

    mechanism: str
    #: Sorted m/z of every member, over every adduct line.
    lattice: np.ndarray

    def offsets_ppm(
        self, mz: np.ndarray, window_ppm: float
    ) -> tuple[np.ndarray, np.ndarray]:
        """Each peak's offset from its nearest member, in ppm.

        :return: The m/z of the peaks within the window of a member, and
            their offsets.
        """
        mz = np.asarray(mz, dtype=np.float64)
        if mz.size == 0 or self.lattice.size < 2:
            return np.array([], dtype=np.float64), np.array([], dtype=np.float64)
        right = np.clip(np.searchsorted(self.lattice, mz), 1, self.lattice.size - 1)
        left = right - 1
        nearest = np.where(
            np.abs(self.lattice[left] - mz) <= np.abs(self.lattice[right] - mz),
            self.lattice[left],
            self.lattice[right],
        )
        offsets = 1e6 * (mz - nearest) / nearest
        within = np.abs(offsets) <= window_ppm
        return mz[within], offsets[within]


def cho_neutral_masses(dbe_max: int, mz_max: float = _FAMILY_MZ_MAX) -> np.ndarray:
    """Monoisotopic masses of the CcHhOo neutrals of the family lattice."""
    carbon, hydrogen, oxygen = (
        composition_mass(parse_composition(symbol)) for symbol in ("C", "H", "O")
    )
    masses = []
    for c in range(1, int(mz_max // 12) + 1):
        for dbe in range(0, min(dbe_max, c + 1) + 1):
            h = 2 * (c + 1 - dbe)
            base = c * carbon + h * hydrogen
            if base > mz_max:
                continue
            o_max = int((mz_max - base) // oxygen)
            masses.extend(base + oxygen * np.arange(0, o_max + 1))
    return np.unique(np.asarray(masses, dtype=np.float64))


def mechanism_shifts(mechanism: IonizationMechanism) -> list[float] | None:
    """The m/z shifts a mechanism adds to a neutral, one per adduct line.

    None for a mechanism that makes no ion of a CHO neutral.
    """
    neutral = composition_mass(parse_composition(_FAMILY_REFERENCE))
    ions, isotopes = generate_target_ions_from_composition(
        TargetCompoundBase(target_compound_formula=_FAMILY_REFERENCE), [mechanism]
    )
    if not ions:
        return None
    return sorted(
        float(isotope.mz) - neutral
        for isotope in isotopes
        if isotope.resolution == "HIGH"
        and isotope.relative_abundance >= _FAMILY_LINE_ABUNDANCE
    )


def family_of(
    mechanism: IonizationMechanism, neutral_masses: np.ndarray
) -> Family | None:
    """The CHO family of one mechanism, or None where it has none."""
    shifts = mechanism_shifts(mechanism)
    if not shifts:
        return None
    lattice = np.unique(np.concatenate([neutral_masses + shift for shift in shifts]))
    return Family(
        mechanism=mechanism.ionization_mechanism, lattice=lattice[lattice > 0]
    )


# --- Bands and summaries ---


def band_labels(edges: Sequence[float]) -> list[str]:
    """Labels of the bands the edges make, the lowest first."""
    labels = [f"<{edges[0]:g}"]
    labels += [f"{lo:g}-{hi:g}" for lo, hi in zip(edges[:-1], edges[1:])]
    labels.append(f"{edges[-1]:g}+")
    return labels


def band_of(mz: np.ndarray, edges: Sequence[float]) -> np.ndarray:
    """The index into :func:`band_labels` of each m/z."""
    return np.searchsorted(np.asarray(edges, dtype=np.float64), mz, side="right")


def spread(values: Iterable[float]) -> dict | None:
    """Median, quartiles, RMS and count of a set of errors."""
    values = np.asarray([v for v in values if np.isfinite(v)], dtype=np.float64)
    if values.size == 0:
        return None
    q25, median, q75 = np.percentile(values, [25, 50, 75])
    return {
        "median": float(median),
        "q25": float(q25),
        "q75": float(q75),
        "iqr": float(q75 - q25),
        "rms": float(np.sqrt(np.mean(values**2))),
        "n": int(values.size),
    }
