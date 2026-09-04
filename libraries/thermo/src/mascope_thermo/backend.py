"""Reader-backend seam for ``mascope_thermo``.

Selects the file-reading implementation behind a single switch so the public
functions in :mod:`mascope_thermo.thermo` can run on either the open-source
OpenTFRaw reader (the default) or Thermo's RawFileReader, without the callers
caring which.

Select with the ``MASCOPE_THERMO_BACKEND`` environment variable::

    "opentfraw"  -> OpenTFRawBackend (default; opentfraw wheel)
    "thermo"     -> ThermoBackend (opt-in; pythonnet + external .NET DLLs)

:class:`ReaderBackend` is a capability protocol (profile arrays, centroids,
multi-scan averaging, XIC, trailer, run header, ...) rather than an emulation of
the .NET RawFile object, so each backend implements it natively.

For an end-to-end explanation of the reading and averaging pipeline (why
averaging happens in the frequency domain, the real-vs-reconstructed profile
split, the averaged-centroid approximation), see ``libraries/thermo/docs/
reader_pipeline.md``.
"""

from __future__ import annotations

import math
import os
import re
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

import numpy as np


ENV_BACKEND = "MASCOPE_THERMO_BACKEND"

Polarity = Literal["+", "-"]
MsType = Literal["Ms", "Ms2"]

# Scan times are reported in minutes by both backends but the public API returns
# seconds (matching the Thermo path's StartTime * 60).
_SECONDS_PER_MINUTE = 60


@runtime_checkable
class ReaderBackend(Protocol):
    """Capability interface the :mod:`mascope_thermo.thermo` functions depend on.

    Implementations are context managers: open the file in ``__enter__`` and
    release it in ``__exit__``. All methods return backend-neutral Python/NumPy
    data (never .NET objects), so callers are backend-agnostic.
    """

    def __enter__(self) -> ReaderBackend: ...
    def __exit__(self, *exc) -> None: ...

    def polarities(self) -> set[str]:
        """Set of polarities present in the file, as ``"+"`` / ``"-"``."""
        ...

    def scan_times(
        self,
        polarity: Polarity | None = None,
        t_min: float | None = None,
        t_max: float | None = None,
        ms_type: MsType | None = "Ms",
    ) -> np.ndarray:
        """Scan start times [s] for the scans matching the given filters."""
        ...

    def tic_per_scan(
        self,
        polarity: Polarity | None = None,
        t_min: float | None = None,
        t_max: float | None = None,
        ms_type: MsType | None = "Ms",
    ) -> tuple[np.ndarray, np.ndarray]:
        """``(scan_times_s, tic)`` for the scans matching the given filters."""
        ...

    def instrument_details(self) -> dict:
        """Instrument metadata dict (Name, Model, SerialNumber, ...)."""
        ...

    def num_scans(self) -> int:
        """Total number of scans (spectra) in the file."""
        ...

    def created(self) -> "datetime | None":
        """File creation (acquisition) timestamp, or ``None`` if the backend
        cannot provide it."""
        ...

    def scan_acquisition_settings(
        self,
        polarity: Polarity | None = None,
        t_min: float | None = None,
        t_max: float | None = None,
        ms_type: MsType | None = "Ms",
    ) -> dict:
        """Per-scan trailer table ``{"header_labels": [...], "settings": {...}}``."""
        ...

    def acquisition_parameters(self, max_scans: int = ...) -> dict:
        """Method-level acquisition parameters sampled from the per-scan trailer.

        Returns ``{"source", "scans_sampled", "constant", "varying"}`` -- see
        :func:`_summarize_acquisition_parameters`. This is deliberately an
        untyped capture of whatever the instrument reports rather than a fixed
        field set: it exists to record what acquisitions actually carry, so a
        structured acquisition-method schema can later be designed from
        evidence instead of guesswork.
        """
        ...

    def scan_statistics(
        self,
        polarity: Polarity | None = None,
        t_min: float | None = None,
        t_max: float | None = None,
        ms_type: MsType | None = "Ms",
    ) -> dict:
        """Per-scan statistics keyed by 1-based scan number."""
        ...

    def scan_indices(
        self,
        polarity: Polarity | None = None,
        t_min: float | None = None,
        t_max: float | None = None,
        ms_type: MsType | None = "Ms",
    ) -> list[int]:
        """1-based scan numbers matching the given filters."""
        ...

    def centroids_per_scan(
        self,
        polarity: Polarity | None = None,
        t_min: float | None = None,
        t_max: float | None = None,
        ms_type: MsType | None = None,
        mz_min: float | None = None,
        mz_max: float | None = None,
    ) -> list[dict]:
        """Per-scan centroids: list of dicts with ``masses``, ``intensities``,
        ``resolutions``, ``signal_to_noise``, ``timestamp``."""
        ...

    def average_centroids(
        self,
        scan_indices: list[int],
        ppm: int = 1,
        average: bool = False,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Multi-scan ppm-binned averaged centroids:
        ``(masses, intensities, resolutions, signal_to_noise)``.

        The Thermo backend uses ``Extensions.AverageScans``; the OpenTFRaw
        backend reimplements ppm binning in NumPy."""
        ...

    def centroids_meta(self) -> dict:
        """All-scan centroid arrays for legacy metadata: ``{"time": [...],
        "data": [{"mzs", "intensities", "resolutions", "noises"}]}``."""
        ...

    def mass_range(self) -> tuple[float, float]:
        """Run-level ``(low_mass, high_mass)`` from the run header."""
        ...

    def profile_per_scan(
        self,
        polarity: Polarity | None = None,
        t_min: float | None = None,
        t_max: float | None = None,
        ms_type: MsType | None = "Ms",
        mz_min: float | None = None,
        mz_max: float | None = None,
    ) -> tuple[list[np.ndarray], list[np.ndarray], np.ndarray]:
        """Per-scan profile spectra: ``(scan_mzs, scan_intensities, scan_times)``,
        the m/z and intensity arrays already restricted to ``[mz_min, mz_max]``.

        The profile arrays come from the ``opentfraw`` profile accessor."""
        ...

    def average_profile(
        self,
        scan_indices: list[int],
        ppm: int = 1,
        average: bool = False,
        reconstruct: bool = False,
    ) -> tuple[np.ndarray, np.ndarray, int]:
        """Multi-scan ppm-binned averaged profile spectrum:
        ``(mz, intensities, scans_combined)``. With ``average=False`` the
        intensities are scaled back up by the combined-scan count (sum signal).

        ``reconstruct=True`` returns a Thermo-style profile reconstructed as one
        Gaussian per centroid (overlays the centroids exactly; matches Thermo,
        which also reconstructs) -- intended for display. The default
        ``reconstruct=False`` returns the real measured profile, which the
        instrument-function fit needs.

        Builds on the profile accessor and the NumPy ppm averaging above."""
        ...

    def xic(
        self,
        mzs,
        ppm: float = 5,
        polarity: Polarity | None = None,
        t_min: float | None = None,
        t_max: float | None = None,
        ms_type: MsType | None = "Ms",
    ) -> tuple[np.ndarray, np.ndarray]:
        """Extracted-ion chromatograms for each target m/z within ``ppm``:
        ``(intensities[n_mz, n_scans], scan_times)``.

        The Thermo backend uses ``GetChromatogramData``; the OpenTFRaw backend
        reimplements m/z-window summation in NumPy."""
        ...

    def ms2_precursor_by_scan(
        self,
        polarity: Polarity | None = None,
        t_min: float | None = None,
        t_max: float | None = None,
    ) -> dict[int, float]:
        """``{scan_number: precursor_mz}`` for MS2 scans (only those whose
        precursor is resolvable).

        Both backends parse the precursor from the rendered scan-filter string;
        on Exploris this relies on ``opentfraw``'s scan-event decoding."""
        ...

    def ms2_acquisition_info(
        self,
        polarity: Polarity | None = None,
        t_min: float | None = None,
        t_max: float | None = None,
    ) -> tuple[float | None, dict[int, str]]:
        """``(isolation_width, {scan_number: hcd_energy_string})`` for MS2 scans.

        ``isolation_width`` is the single MS2 isolation width across the scans;
        the HCD energy strings may be comma-separated (step dissociation). Both
        backends read these from the trailer (``"MS2 Isolation Width:"`` /
        ``"HCD Energy V:"``)."""
        ...

    def ms2_centroids_for_scans(
        self, scan_indices: list[int]
    ) -> tuple[list[dict], list[float]]:
        """Per-scan centroids + TIC for explicit scan numbers:
        ``([{masses, intensities, resolutions, signal_to_noise, timestamp}], tics)``."""
        ...


# Field set returned by GetInstrumentData (excluding non-serializable
# ChannelLabels / Units). Kept here so every backend reports the same shape.
INSTRUMENT_FIELDS = (
    "Name",
    "Model",
    "SerialNumber",
    "SoftwareVersion",
    "HardwareVersion",
    "Flags",
    "AxisLabelX",
    "AxisLabelY",
    "IsValid",
    "HasAccurateMassPrecursors",
)

# Per-scan statistics fields read from Thermo's ScanStats. (OpenTFRaw currently
# exposes a subset of these fields; see the metadata-remap follow-up issue.)
SCAN_STAT_FIELDS = (
    "HighMass",
    "LowMass",
    "LongWavelength",
    "ShortWavelength",
    "BasePeakIntensity",
    "BasePeakMass",
    "TIC",
    "StartTime",
    "PacketCount",
    "NumberOfChannels",
    "ScanNumber",
    "ScanEventNumber",
    "SegmentNumber",
    "IsCentroidScan",
    "Frequency",
    "IsUniformTime",
    "AbsorbanceUnitScale",
    "WavelengthStep",
    "ScanType",
    "CycleNumber",
)

# Per-scan acquisition fields OpenTFRaw decodes (from its typed scan dict),
# surfaced as a trailer-like table. The label strings are descriptive and
# intentionally differ from Thermo's trailer labels, which OpenTFRaw does not
# expose; the (key, label) pairs map an OpenTFRaw scan-dict key to a column.
_OTF_TRAILER_FIELDS = (
    ("ion_injection_time_ms", "Ion Injection Time (ms)"),
    ("charge", "Charge State"),
    ("precursor_mz", "Precursor m/z"),
    ("isolation_width", "Isolation Width (m/z)"),
    ("collision_energy", "Collision Energy"),
)

# Default number of scans sampled by acquisition_parameters(). The trailer is
# read per scan, so this is a cost/confidence trade: enough spread to catch a
# value that drifts over the run, few enough to stay negligible against peak
# detection.
_ACQUISITION_PARAM_SCANS = 5


def _json_safe(value):
    """Coerce a trailer value into something ``json.dump`` can write.

    Trailer values arrive as plain scalars from OpenTFRaw, as boxed .NET types
    from the Thermo DLLs, and occasionally as numpy scalars. ``.props`` is
    written with ``json.dump``, so anything not natively serializable is
    stringified rather than allowed to fail the whole file.
    """
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    # numpy (and .NET) scalars expose .item(); unwrap before the isinstance
    # checks below, since np.int64/np.bool_ do not subclass int/bool.
    if hasattr(value, "item") and not isinstance(value, (str, bool, int, float)):
        try:
            value = value.item()
        except Exception:
            return str(value)
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        # NaN/Infinity are accepted by Python's json but are not valid JSON.
        return value if math.isfinite(value) else str(value)
    return str(value)


def _sample_evenly(items: list, count: int) -> list:
    """Up to *count* items spread evenly across *items*, endpoints included."""
    if count <= 0 or not items:
        return []
    if len(items) <= count:
        return list(items)
    step = (len(items) - 1) / (count - 1) if count > 1 else 0
    return [items[round(i * step)] for i in range(count)]


def _summarize_acquisition_parameters(source: str, per_scan: list[dict]) -> dict:
    """Split sampled per-scan trailers into method-level and per-scan parameters.

    A trailer mixes settings that describe the acquisition *method* (resolution,
    AGC target, source voltages, application mode) with values measured per
    *scan* (ion injection time, charge state, precursor m/z). Only keys whose
    value is identical across every sampled scan are reported under
    ``constant`` -- those are the method-level candidates. Keys that differed
    are reported by name only under ``varying``, because one scan's measurement
    is not method metadata and storing it would invite exactly that mistake.

    Values are kept verbatim, so their type depends on the backend: Thermo's
    ``GetTrailerExtraInformation().Values`` is a .NET ``string[]`` and reports
    everything as text, while OpenTFRaw parses the same trailer into typed
    scalars. Normalising them here would mean deciding the type of 70+
    heterogeneous instrument fields, which is the schema call this capture
    exists to defer -- hence ``source``, so a reader can normalise per backend.

    :param source: Backend that produced the trailers ("opentfraw"/"thermo").
    :param per_scan: Trailer dicts, one per sampled scan.
    :return: ``{"source", "scans_sampled", "constant", "varying"}``
    :rtype: dict
    """
    per_scan = [scan for scan in per_scan if scan]
    if not per_scan:
        return {"source": source, "scans_sampled": 0, "constant": {}, "varying": []}

    first, *rest = per_scan
    constant = {key: _json_safe(value) for key, value in first.items()}
    varying: set[str] = set()

    for scan in rest:
        for key in list(constant):
            if key not in scan or _json_safe(scan[key]) != constant[key]:
                del constant[key]
                varying.add(key)
        # A key absent from the first scan cannot be treated as constant.
        varying.update(key for key in scan if key not in constant)

    return {
        "source": source,
        "scans_sampled": len(per_scan),
        "constant": constant,
        "varying": sorted(varying),
    }


# Output grid resolution (constant ppm) for average_profile. Fine enough to
# sample per-peak FWHM (~4-8 ppm on Orbitrap) at many points while collapsing
# the between-scan jitter duplicates into one cell per native position.
_AVG_PROFILE_GRID_PPM = 0.2

# average_profile m/z calibration (align the profile axis to the centroid
# labels; see _align_profile_grid_to_centroids). Sample a few scans for the
# reference centroids, anchor on well-separated strong peaks, reject matches
# whose offset is far from the median, and fit a low-order correction.
_AVG_PROFILE_CALIB_SCANS = 8  # scans sampled for the reference centroids
_AVG_PROFILE_CALIB_SEP_PPM = 60  # min spacing between anchor peaks
_AVG_PROFILE_CALIB_TIGHT_PPM = 5  # max residual to keep a match after pass 1
_AVG_PROFILE_CALIB_MIN_ANCHORS = 6  # below this, leave the grid uncorrected
_AVG_PROFILE_CALIB_MAX_ANCHORS = 60  # cap anchors (a linear fit needs few)
_AVG_PROFILE_FREQ_NEWTON = 4  # Newton iterations for the m/z -> frequency inverse
_AVG_PROFILE_GAP_DF = 2.0  # zero a scan's interp beyond this * FFT bin from its samples
_RECON_SIGMA = 5.0  # reconstructed-profile half-window / sample span, in sigma
_RECON_PTS = 15  # samples per peak across +-_RECON_SIGMA sigma (~Thermo's density;
# keep odd so the centroid is sampled exactly -> the profile apex lands on it)
_AVG_CENTROID_HEIGHT_PPM = 3.0  # window to source centroid height from profile apex
_AVG_CENTROID_HEIGHT_BAND = (0.85, 1.15)  # apply the apex only as a modest refinement
_AVG_CENTROID_MERGE_FWHM = 0.5  # merge centroids whose gap is below this * local FWHM
# ... or below this, if the scans hold one or the other side, not both:
_AVG_CENTROID_EXCLUSIVE_MERGE_FWHM = 1.5
_AVG_CENTROID_EXCLUSIVE_OVERLAP = 0.2  # max share of the smaller side in shared scans
_AVG_CENTROID_EXCLUSIVE_COVERAGE = 0.6  # min fraction of the scans the two sides span
_AVG_CENTROID_EXCLUSIVE_MIN_SIDE = 0.35  # min fraction of the scans on each side (>= 2)
_ZEROFILL_GAP_FACTOR = 4.0  # profile m/z gap > this * median = a cluster boundary
_ZEROFILL_EDGE_PPM = 2.0  # place baseline zeros this far outside each cluster edge


def _label_peaks(
    centroid_scan,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Extract ``(masses, intensities, resolutions, signal_to_noise)`` from a
    Thermo CentroidScan's label peaks, or four empty arrays if there are none.
    """
    if centroid_scan is None or centroid_scan.Length == 0:
        return (
            np.array([], dtype=np.float64),
            np.array([], dtype=np.float64),
            np.array([], dtype=np.float64),
            np.array([], dtype=np.float64),
        )
    peaks = centroid_scan.GetLabelPeaks()
    n = len(peaks)
    return (
        np.fromiter((c.Mass for c in peaks), dtype=np.float64, count=n),
        np.fromiter((c.Intensity for c in peaks), dtype=np.float64, count=n),
        np.fromiter((c.Resolution for c in peaks), dtype=np.float64, count=n),
        np.fromiter((c.SignalToNoise for c in peaks), dtype=np.float64, count=n),
    )


def _ppm_bin(
    mz: np.ndarray,
    intensity: np.ndarray,
    extras: list[np.ndarray],
    ppm: float,
    groups: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, list[np.ndarray], np.ndarray, list | None]:
    """Greedily cluster ``(mz, intensity)`` points within ``ppm`` and aggregate.

    Points (pooled across scans) are sorted by m/z; a new bin starts wherever
    the gap to the previous point exceeds ``ppm`` (relative to the lower m/z).
    Per bin: intensity-weighted mean m/z, summed intensity, and an
    intensity-weighted mean of each array in ``extras`` (e.g. resolution, S:N).
    Vectorized with ``np.add.reduceat`` so it scales to the hundreds of
    thousands of profile points a multi-scan window produces.

    Returns ``(binned_mz, summed_intensity, [binned_extra, ...], counts,
    members)`` where ``counts`` is the number of pooled points in each bin (=
    scans contributing a centroid, used to scale the averaged S:N) and
    ``members`` holds, per bin, the ``groups`` id (the scan) and the intensity
    of each of its points as a pair of arrays -- or None when ``groups`` is
    not given.
    """
    empty = np.array([], dtype=np.float64)
    if mz.size == 0:
        return (
            empty,
            empty,
            [empty for _ in extras],
            empty,
            [] if groups is not None else None,
        )

    order = np.argsort(mz, kind="stable")
    mz = mz[order]
    intensity = intensity[order]
    extras = [e[order] for e in extras]

    if mz.size == 1:
        starts = np.array([0])
    else:
        gap_ppm = np.diff(mz) / mz[:-1] * 1e6
        starts = np.concatenate(([0], np.flatnonzero(gap_ppm > ppm) + 1))

    members = (
        None
        if groups is None
        else list(
            zip(np.split(groups[order], starts[1:]), np.split(intensity, starts[1:]))
        )
    )
    counts = np.diff(np.append(starts, mz.size))
    isum = np.add.reduceat(intensity, starts)
    # Guard against zero-intensity bins (fall back to a plain mean for m/z and
    # the extras there).
    safe = np.where(isum > 0, isum, 1.0)
    nonzero = isum > 0

    wmz = np.add.reduceat(mz * intensity, starts) / safe
    plain_mz = np.add.reduceat(mz, starts) / counts
    binned_mz = np.where(nonzero, wmz, plain_mz)

    binned_extras = []
    for e in extras:
        we = np.add.reduceat(e * intensity, starts) / safe
        plain_e = np.add.reduceat(e, starts) / counts
        binned_extras.append(np.where(nonzero, we, plain_e))

    return binned_mz, isum, binned_extras, counts.astype(np.float64), members


class ThermoBackend:
    """:class:`ReaderBackend` backed by Thermo RawFileReader via pythonnet.

    Wraps the existing ``RawFileManager`` + ``ScanSelector`` machinery in
    :mod:`mascope_thermo.thermo` (imported lazily to avoid an import cycle).
    """

    def __init__(self, datafile_path: str):
        self.datafile_path = datafile_path
        self._mgr = None
        self._raw = None

    def __enter__(self) -> ThermoBackend:
        from mascope_thermo.thermo import RawFileManager

        self._mgr = RawFileManager(self.datafile_path)
        self._raw = self._mgr.__enter__()
        return self

    def __exit__(self, *exc) -> None:
        try:
            if self._mgr is not None:
                self._mgr.__exit__(*exc)
        finally:
            self._mgr = None
            self._raw = None

    def _selector(
        self,
        polarity: Polarity | None = None,
        t_min: float | None = None,
        t_max: float | None = None,
        ms_type: MsType | None = "Ms",
    ):
        from mascope_thermo.thermo import ScanSelector

        return ScanSelector(
            self._raw,
            polarity=polarity,
            t_min=t_min,
            t_max=t_max,
            ms_type=ms_type,
        )

    def polarities(self) -> set[str]:
        out: set[str] = set()
        for scan_filter in self._selector(ms_type=None).raw_scan_filters:
            verbose = scan_filter.Polarity.ToString()
            if verbose == "Positive":
                out.add("+")
            elif verbose == "Negative":
                out.add("-")
        return out

    def scan_times(
        self,
        polarity: Polarity | None = None,
        t_min: float | None = None,
        t_max: float | None = None,
        ms_type: MsType | None = "Ms",
    ) -> np.ndarray:
        return self._selector(polarity, t_min, t_max, ms_type).scan_times

    def tic_per_scan(
        self,
        polarity: Polarity | None = None,
        t_min: float | None = None,
        t_max: float | None = None,
        ms_type: MsType | None = "Ms",
    ) -> tuple[np.ndarray, np.ndarray]:
        selector = self._selector(polarity, t_min, t_max, ms_type)
        times = selector.scan_times
        tic = np.asarray(
            [
                self._raw.GetScanStatsForScanNumber(i).TIC
                for i in selector.scan_indices_1based
            ],
            dtype=np.float64,
        )
        return times, tic

    def instrument_details(self) -> dict:
        data = self._raw.GetInstrumentData()
        return {field: getattr(data, field) for field in INSTRUMENT_FIELDS}

    def num_scans(self) -> int:
        return self._raw.RunHeaderEx.SpectraCount

    def created(self):
        from datetime import datetime

        d = self._raw.CreationDate
        return datetime(d.Year, d.Month, d.Day, d.Hour, d.Minute, d.Second)

    def scan_acquisition_settings(
        self,
        polarity: Polarity | None = None,
        t_min: float | None = None,
        t_max: float | None = None,
        ms_type: MsType | None = "Ms",
    ) -> dict:
        selector = self._selector(polarity, t_min, t_max, ms_type)
        settings: dict[int, list] = {}
        header_labels = None
        for i in selector.scan_indices_1based:
            header = self._raw.GetTrailerExtraInformation(i)
            if header_labels is None:
                header_labels = list(header.Labels)
            settings[i] = list(header.Values)
        return {"header_labels": header_labels, "settings": settings}

    def acquisition_parameters(self, max_scans: int = _ACQUISITION_PARAM_SCANS) -> dict:
        selector = self._selector(None, None, None, "Ms")
        per_scan = []
        for i in _sample_evenly(list(selector.scan_indices_1based), max_scans):
            header = self._raw.GetTrailerExtraInformation(i)
            per_scan.append(dict(zip(list(header.Labels), list(header.Values))))
        return _summarize_acquisition_parameters("thermo", per_scan)

    def scan_statistics(
        self,
        polarity: Polarity | None = None,
        t_min: float | None = None,
        t_max: float | None = None,
        ms_type: MsType | None = "Ms",
    ) -> dict:
        selector = self._selector(polarity, t_min, t_max, ms_type)
        return {
            scan_index: {
                **{
                    name: getattr(selector.raw_scan_stats[scan_index - 1], name)
                    for name in SCAN_STAT_FIELDS
                },
                # Scan type isn't in ScanStats; read it from the filter.
                "MsType": selector.raw_scan_filters[scan_index - 1].MSOrder.ToString(),
            }
            for scan_index in selector.scan_indices_1based
        }

    def scan_indices(
        self,
        polarity: Polarity | None = None,
        t_min: float | None = None,
        t_max: float | None = None,
        ms_type: MsType | None = "Ms",
    ) -> list[int]:
        return self._selector(polarity, t_min, t_max, ms_type).scan_indices_1based

    def centroids_per_scan(
        self,
        polarity: Polarity | None = None,
        t_min: float | None = None,
        t_max: float | None = None,
        ms_type: MsType | None = None,
        mz_min: float | None = None,
        mz_max: float | None = None,
    ) -> list[dict]:
        from mascope_thermo.thermo import _validate_mz_range

        mz_min, mz_max = _validate_mz_range(self._raw, mz_min, mz_max)
        selector = self._selector(polarity, t_min, t_max, ms_type)

        out: list[dict] = []
        for scan, timestamp in zip(selector.scans, selector.scan_times):
            masses, intensities, resolutions, signal_to_noise = _label_peaks(
                scan.CentroidScan
            )
            mz_mask = np.logical_and(mz_min <= masses, masses <= mz_max)
            out.append(
                {
                    "masses": masses[mz_mask],
                    "intensities": intensities[mz_mask],
                    "resolutions": resolutions[mz_mask],
                    "signal_to_noise": signal_to_noise[mz_mask],
                    "timestamp": timestamp,
                }
            )
        return out

    def average_centroids(
        self,
        scan_indices: list[int],
        ppm: int = 1,
        average: bool = False,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        from mascope_thermo.thermo import _average_scans_centroids

        return _average_scans_centroids(
            self._raw, scan_indices, ppm=ppm, average=average
        )

    def centroids_meta(self) -> dict:
        result = {"time": [], "data": []}
        selector = self._selector(ms_type=None)
        for timestamp, scan in zip(selector.scan_times, selector.scans):
            centroid_scan = scan.CentroidScan
            if centroid_scan is not None and centroid_scan.Length > 0:
                mzs = np.frombuffer(centroid_scan.Masses)
                intensities = np.frombuffer(centroid_scan.Intensities)
                resolutions = np.frombuffer(centroid_scan.Resolutions)
                noises = np.frombuffer(centroid_scan.Noises)

                valid = (
                    np.isfinite(resolutions)
                    & (resolutions > 0)
                    & np.isfinite(intensities)
                    & (intensities > 0)
                )
                mzs = mzs[valid].tolist()
                intensities = intensities[valid].tolist()
                resolutions = resolutions[valid].tolist()
                noises = noises[valid].tolist()
            else:
                mzs = []
                intensities = []
                resolutions = []
                noises = []
            result["time"].append(timestamp)
            result["data"].append(
                {
                    "intensities": intensities,
                    "mzs": mzs,
                    "resolutions": resolutions,
                    "noises": noises,
                }
            )
        return result

    def mass_range(self) -> tuple[float, float]:
        header = self._raw.RunHeaderEx
        return header.LowMass, header.HighMass

    def profile_per_scan(
        self,
        polarity: Polarity | None = None,
        t_min: float | None = None,
        t_max: float | None = None,
        ms_type: MsType | None = "Ms",
        mz_min: float | None = None,
        mz_max: float | None = None,
    ) -> tuple[list[np.ndarray], list[np.ndarray], np.ndarray]:
        from mascope_thermo.thermo import _validate_mz_range

        mz_min, mz_max = _validate_mz_range(self._raw, mz_min, mz_max)
        selector = self._selector(polarity, t_min, t_max, ms_type)

        scan_mzs: list[np.ndarray] = []
        scan_specs: list[np.ndarray] = []
        for scan in selector.scans:
            positions = np.frombuffer(scan.SegmentedScan.Positions)
            intensities = np.frombuffer(scan.SegmentedScan.Intensities)
            mz_mask = np.logical_and(mz_min <= positions, positions <= mz_max)
            scan_mzs.append(positions[mz_mask])
            scan_specs.append(intensities[mz_mask])
        return scan_mzs, scan_specs, selector.scan_times

    def average_profile(
        self,
        scan_indices: list[int],
        ppm: int = 1,
        average: bool = False,
        reconstruct: bool = False,
    ) -> tuple[np.ndarray, np.ndarray, int]:
        # ``reconstruct`` is accepted for protocol parity but has no effect:
        # Thermo's AverageScans profile is always a reconstruction (one Gaussian
        # per centroid). The real measured averaged profile is only available
        # from the OpenTFRaw backend (reconstruct=False there).
        from System.Collections.Generic import List
        from ThermoFisher.CommonCore.Data import Extensions, ToleranceUnits
        from ThermoFisher.CommonCore.Data.Business import MassOptions

        dotnet_indices = List[int]()
        for index in scan_indices:
            dotnet_indices.Add(index)

        average_scan = Extensions.AverageScans(
            self._raw, dotnet_indices, MassOptions(ppm, ToleranceUnits.ppm)
        )
        segmented = average_scan.SegmentedScan
        num_combined = average_scan.ScansCombined
        mz = np.frombuffer(segmented.Positions)
        intensities = np.frombuffer(segmented.Intensities)
        if not average:
            intensities = intensities * num_combined
        return mz, intensities, num_combined

    def xic(
        self,
        mzs,
        ppm: float = 5,
        polarity: Polarity | None = None,
        t_min: float | None = None,
        t_max: float | None = None,
        ms_type: MsType | None = "Ms",
    ) -> tuple[np.ndarray, np.ndarray]:
        from ThermoFisher.CommonCore.Data.Business import (
            ChromatogramSignal,
            ChromatogramTraceSettings,
            Range,
            TraceType,
        )

        mzs = np.asarray(mzs, dtype=float)
        selector = self._selector(polarity, t_min, t_max, ms_type)
        selected_scans = selector.scan_indices_1based

        intensities = np.zeros((len(mzs), len(selected_scans)), dtype=np.float64)
        mz_lows = mzs - (mzs * ppm / 1e6)
        mz_highs = mzs + (mzs * ppm / 1e6)

        settings = []
        for mz_low, mz_high in zip(mz_lows, mz_highs):
            mz_range = Range()
            mz_range.Low = mz_low
            mz_range.High = mz_high
            setting = ChromatogramTraceSettings(TraceType.MassRange)
            setting.MassRanges = [mz_range]
            settings.append(setting)

        # -1, -1 -> the chromatogram's full scan range. Align each trace to the
        # selected scans by scan number, not positionally: on some files Thermo's
        # GetChromatogramData under-covers the scan list (it can return points for
        # only a subset, even a single scan), which made positional slicing
        # overrun. Scans a trace does not cover are filled below.
        chromatogram = self._raw.GetChromatogramData(settings, -1, -1)
        traces = ChromatogramSignal.FromChromatogramData(chromatogram)
        rows: list[np.ndarray] = []
        has_gaps = False
        for trace in traces:
            scans = np.fromiter(
                trace.Scans, dtype=np.int64, count=len(trace.Scans)
            ).tolist()
            values = np.fromiter(
                trace.Intensities, dtype=np.float64, count=len(trace.Intensities)
            ).tolist()
            by_scan = dict(zip(scans, values))
            row = np.array(
                [by_scan.get(sn, np.nan) for sn in selected_scans], dtype=np.float64
            )
            has_gaps = has_gaps or bool(np.isnan(row).any())
            rows.append(row)

        # Where GetChromatogramData under-covered scans, fill the gaps with the
        # per-scan centroid-window sum -- the same quantity it returns where it
        # does have data -- so the XIC spans every selected scan. Only read the
        # centroids when there is actually a gap (the common path has none).
        if has_gaps:
            per_scan = self.centroids_per_scan(
                polarity=polarity, t_min=t_min, t_max=t_max, ms_type=ms_type
            )
            scan_centroids = [(d["masses"], d["intensities"]) for d in per_scan]
            for i, row in enumerate(rows):
                for j in np.flatnonzero(np.isnan(row)):
                    mz_arr, int_arr = scan_centroids[j]
                    in_window = (mz_arr >= mz_lows[i]) & (mz_arr <= mz_highs[i])
                    row[j] = float(int_arr[in_window].sum())

        for i, row in enumerate(rows):
            intensities[i] = np.nan_to_num(row, nan=0.0)

        return intensities, selector.scan_times

    def ms2_precursor_by_scan(
        self,
        polarity: Polarity | None = None,
        t_min: float | None = None,
        t_max: float | None = None,
    ) -> dict[int, float]:
        selector = self._selector(polarity, t_min, t_max, ms_type="Ms2")
        out: dict[int, float] = {}
        for scan_idx, scan_filter in zip(
            selector.scan_indices_1based, selector.scan_filters, strict=True
        ):
            match = re.search(r"ms2 ([\d.]+)@", scan_filter.ToString())
            if match:
                out[scan_idx] = float(match.group(1))
        return out

    def ms2_acquisition_info(
        self,
        polarity: Polarity | None = None,
        t_min: float | None = None,
        t_max: float | None = None,
    ) -> tuple[float | None, dict[int, str]]:
        acq = self.scan_acquisition_settings(polarity, t_min, t_max, ms_type="Ms2")
        trailer_labels = acq["header_labels"]
        isolation_width_idx = trailer_labels.index("MS2 Isolation Width:")
        hcd_label_idx = trailer_labels.index("HCD Energy V:")

        isolation_widths = set()
        scan_idx_to_hcd: dict[int, str] = {}
        for scan_idx, trailer_values in acq["settings"].items():
            isolation_widths.add(trailer_values[isolation_width_idx])
            scan_idx_to_hcd[scan_idx] = trailer_values[hcd_label_idx]

        isolation_widths.discard(None)
        isolation_widths.discard("")
        if len(isolation_widths) == 1:
            isolation_width = float(isolation_widths.pop().replace(",", "."))
        else:
            raise ValueError("Multiple isolation widths found for MS2 scans.")
        return isolation_width, scan_idx_to_hcd

    def ms2_centroids_for_scans(
        self, scan_indices: list[int]
    ) -> tuple[list[dict], list[float]]:
        from ThermoFisher.CommonCore.Data import Extensions

        from mascope_thermo.thermo import SECONDS_PER_MINUTE

        centroids: list[dict] = []
        tic_values: list[float] = []
        for scan_idx in scan_indices:
            scan_obj = list(Extensions.GetScans(self._raw, scan_idx, scan_idx))[0]
            stats = self._raw.GetScanStatsForScanNumber(scan_idx)
            masses, intensities, resolutions, signal_to_noise = _label_peaks(
                scan_obj.CentroidScan
            )
            centroids.append(
                {
                    "masses": masses,
                    "intensities": intensities,
                    "resolutions": resolutions,
                    "signal_to_noise": signal_to_noise,
                    "timestamp": stats.StartTime * SECONDS_PER_MINUTE,
                }
            )
            tic_values.append(float(stats.TIC))
        return centroids, tic_values


class OpenTFRawBackend:
    """:class:`ReaderBackend` backed by the open-source OpenTFRaw reader.

    Reads centroids, profiles and metadata directly from OpenTFRaw, and
    reimplements in NumPy the operations Thermo's .NET library computes natively
    (ppm-binned averaging, the XIC). The profile, per-peak label and scan-event
    accessors it relies on are provided by the ``opentfraw`` package.
    """

    def __init__(self, datafile_path: str):
        self.datafile_path = datafile_path
        self._raw = None
        self._scans: list[dict] | None = None

    def __enter__(self) -> OpenTFRawBackend:
        import opentfraw

        self._raw = opentfraw.RawFile(self.datafile_path)
        return self

    def __exit__(self, *exc) -> None:
        self._raw = None
        self._scans = None

    # -- scan selection: mirrors thermo.ScanSelector over OpenTFRaw scan dicts --

    def _all_scans(self) -> list[dict]:
        if self._scans is None:
            self._scans = list(self._raw.iter_scans())
        return self._scans

    @staticmethod
    def _bad_first_scan(scans: list[dict]) -> bool:
        """Mirror the ThermoBackend first-scan-outlier workaround (thermo.py
        ``_bad_first_scan``): a Mascope-side guard for a common Thermo raw-file
        quirk where the first scan has an abnormally high TIC. True when the
        first scan's TIC is >= 5x the median TIC of the rest. Replicated here so
        both backends select the same scan set (computed over all scans, as the
        Thermo path does)."""
        if len(scans) <= 1:
            return False
        tic = np.array([s["total_ion_current"] for s in scans], dtype=np.float64)
        return bool(tic[0] >= 5 * np.median(tic[1:]))

    def _selected(
        self,
        polarity: Polarity | None = None,
        t_min: float | None = None,
        t_max: float | None = None,
        ms_type: MsType | None = "Ms",
    ) -> list[dict]:
        from mascope_thermo.thermo import (
            InvalidRangeError,
            NoScansFoundError,
            PolarityError,
            ScanTypeError,
        )

        scans = self._all_scans()
        # dtype=bool on every mask below: for a file with no scans the list
        # comprehensions are empty and numpy would otherwise infer float64,
        # making `mask &=` raise TypeError instead of letting the empty
        # selection fall through to the NoScansFoundError this method exists
        # to raise.
        mask = np.ones(len(scans), dtype=bool)

        if polarity:
            if polarity not in ("-", "+"):
                raise PolarityError(
                    f"Invalid polarity '{polarity}' provided. "
                    "Polarity must be '+' or '-'."
                )
            mask &= np.array([s["polarity"] == polarity for s in scans], dtype=bool)

        if t_min is not None or t_max is not None:
            start_s = np.array(
                [s["retention_time"] * _SECONDS_PER_MINUTE for s in scans]
            )
            low = start_s.min() if t_min is None else t_min
            high = start_s.max() if t_max is None else t_max
            if low > high:
                raise InvalidRangeError(
                    f"Invalid time range: t_min={low} s > t_max={high} s"
                )
            eps = np.finfo(np.float64).eps * high
            mask &= (low - eps < start_s) & (start_s < high + eps)

        if ms_type:
            level = {"Ms": 1, "Ms2": 2}.get(ms_type)
            if level is None:
                raise ScanTypeError(
                    f"Invalid scan type '{ms_type}' provided. "
                    "MS scan type must be 'Ms' or 'Ms2'."
                )
            mask &= np.array([int(s["ms_level"]) == level for s in scans], dtype=bool)

        # Mirror the ThermoBackend first-scan-outlier exclusion (thermo.py
        # scan_indices_1based) so both backends select the same scan set. The
        # check and mask[0] are over the full file scan list, as on the Thermo
        # path.
        if self._bad_first_scan(scans):
            from mascope_thermo.runtime import runtime

            # INFO: a data quirk of the file, re-evaluated on every scan
            # selection (see thermo.py scan_indices_1based)
            runtime.logger.info(
                "The first scan appears to be an outlier with abnormally high "
                "TIC. Excluding the first scan from selection."
            )
            mask[0] = False

        selected = [s for s, keep in zip(scans, mask) if keep]
        if not selected:
            raise NoScansFoundError(
                "No scans found matching the specified filters: "
                f"polarity='{polarity}', time_range=({t_min}, {t_max}), "
                f"ms_type='{ms_type}'"
            )
        return selected

    # -- clean mappings (implemented) --

    def polarities(self) -> set[str]:
        return {s["polarity"] for s in self._all_scans() if s["polarity"] in ("+", "-")}

    def scan_times(
        self,
        polarity: Polarity | None = None,
        t_min: float | None = None,
        t_max: float | None = None,
        ms_type: MsType | None = "Ms",
    ) -> np.ndarray:
        return np.array(
            [
                s["retention_time"] * _SECONDS_PER_MINUTE
                for s in self._selected(polarity, t_min, t_max, ms_type)
            ]
        )

    def tic_per_scan(
        self,
        polarity: Polarity | None = None,
        t_min: float | None = None,
        t_max: float | None = None,
        ms_type: MsType | None = "Ms",
    ) -> tuple[np.ndarray, np.ndarray]:
        selected = self._selected(polarity, t_min, t_max, ms_type)
        times = np.array([s["retention_time"] * _SECONDS_PER_MINUTE for s in selected])
        tic = np.array([s["total_ion_current"] for s in selected], dtype=np.float64)
        return times, tic

    def num_scans(self) -> int:
        return int(self._raw.num_scans)

    def created(self):
        from datetime import datetime, timezone

        # ``RawFile.created`` is the Xcalibur audit timestamp: the instrument's
        # local wall-clock encoded as a Windows FILETIME, with no timezone in the
        # file. Interpret it as UTC to recover that exact wall-clock independent of
        # this machine's timezone, then drop tzinfo to match the legacy (naive)
        # CreationDate. Returns None for files without an audit timestamp.
        ts = self._raw.created
        if ts is None:
            return None
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).replace(tzinfo=None)

    def scan_indices(
        self,
        polarity: Polarity | None = None,
        t_min: float | None = None,
        t_max: float | None = None,
        ms_type: MsType | None = "Ms",
    ) -> list[int]:
        return [
            int(s["scan_number"])
            for s in self._selected(polarity, t_min, t_max, ms_type)
        ]

    def mass_range(self) -> tuple[float, float]:
        from mascope_thermo.thermo import NoScansFoundError

        scans = self._all_scans()
        if not scans:
            # min()/max() over an empty file would raise a bare ValueError
            # ("arg is an empty sequence"), which reads as a fault. Every other
            # accessor reports a scanless file with NoScansFoundError, and
            # callers are built on that.
            raise NoScansFoundError("No scans found: the file holds no scans.")
        return (
            float(min(s["low_mz"] for s in scans)),
            float(max(s["high_mz"] for s in scans)),
        )

    # -- Phase-4 metadata remap --

    def instrument_details(self) -> dict:
        # OpenTFRaw detects only the instrument model (it does not parse the
        # structured InstID block), so Model / Name are populated and the rest
        # (serial number, software/hardware version, axis labels, flags) are
        # absent. Model is the field downstream relies on. The same keys as the
        # Thermo backend are returned so the shape is stable; missing values are
        # None. (Populating the rest needs OpenTFRaw InstID parsing -- ticket.)
        model = self._raw.instrument_model
        details = {field: None for field in INSTRUMENT_FIELDS}
        details["Model"] = model or ""
        details["Name"] = model or ""
        details["IsValid"] = model is not None
        return details

    def scan_acquisition_settings(
        self,
        polarity: Polarity | None = None,
        t_min: float | None = None,
        t_max: float | None = None,
        ms_type: MsType | None = "Ms",
    ) -> dict:
        # OpenTFRaw exposes typed per-scan params rather than Thermo's
        # trailer-label table, so surface the subset OpenTFRaw decodes under
        # descriptive labels. Shape matches ThermoBackend (header_labels +
        # settings rows aligned 1:1); the label *names* differ from Thermo's.
        selected = self._selected(polarity, t_min, t_max, ms_type)
        header_labels = [label for _, label in _OTF_TRAILER_FIELDS]
        settings = {
            int(s["scan_number"]): [s.get(key) for key, _ in _OTF_TRAILER_FIELDS]
            for s in selected
        }
        return {"header_labels": header_labels, "settings": settings}

    def acquisition_parameters(self, max_scans: int = _ACQUISITION_PARAM_SCANS) -> dict:
        # scan_parameters() is the instrument's own trailer-extra table -- far
        # richer than the typed subset _OTF_TRAILER_FIELDS surfaces (tens of
        # entries: application mode, FT resolution, AGC target, S-Lens RF, FAIMS
        # state, source CID). Read it directly rather than widening
        # _OTF_TRAILER_FIELDS, whose shape scan_acquisition_settings() pins.
        scan_numbers = [int(s["scan_number"]) for s in self._selected(ms_type="Ms")]
        per_scan = [
            self._raw.scan_parameters(n) or {}
            for n in _sample_evenly(scan_numbers, max_scans)
        ]
        return _summarize_acquisition_parameters("opentfraw", per_scan)

    def scan_statistics(
        self,
        polarity: Polarity | None = None,
        t_min: float | None = None,
        t_max: float | None = None,
        ms_type: MsType | None = "Ms",
    ) -> dict:
        # Map the per-scan stats OpenTFRaw decodes onto Thermo's ScanStats field
        # names. Fields OpenTFRaw does not provide (LongWavelength, Frequency,
        # PacketCount, ...) are omitted rather than faked. StartTime is in
        # minutes, matching Thermo's ScanStats.StartTime. MsType mirrors Thermo's
        # MSOrder.ToString() ("Ms" / "Ms2").
        selected = self._selected(polarity, t_min, t_max, ms_type)
        return {
            int(s["scan_number"]): {
                "TIC": float(s["total_ion_current"]),
                "StartTime": float(s["retention_time"]),
                "BasePeakMass": float(s["base_peak_mz"]),
                "BasePeakIntensity": float(s["base_peak_intensity"]),
                "LowMass": float(s["low_mz"]),
                "HighMass": float(s["high_mz"]),
                "ScanNumber": int(s["scan_number"]),
                "MsType": "Ms"
                if int(s["ms_level"]) == 1
                else f"Ms{int(s['ms_level'])}",
            }
            for s in selected
        }

    def _validate_mz_range(
        self, mz_min: float | None, mz_max: float | None
    ) -> tuple[float, float]:
        from mascope_thermo.thermo import InvalidRangeError

        low, high = self.mass_range()
        low = low if mz_min is None else mz_min
        high = high if mz_max is None else mz_max
        if low > high:
            raise InvalidRangeError(
                f"Invalid m/z range: mz_min={low}, mz_max={high}, where mz_min > mz_max"
            )
        return low, high

    def centroids_per_scan(
        self,
        polarity: Polarity | None = None,
        t_min: float | None = None,
        t_max: float | None = None,
        ms_type: MsType | None = None,
        mz_min: float | None = None,
        mz_max: float | None = None,
    ) -> list[dict]:
        mz_min, mz_max = self._validate_mz_range(mz_min, mz_max)
        selected = self._selected(polarity, t_min, t_max, ms_type)

        out: list[dict] = []
        for s in selected:
            labels = self._raw.centroid_labels(int(s["scan_number"]))
            masses = np.asarray(labels["mz"], dtype=np.float64)
            intensities = np.asarray(labels["intensity"], dtype=np.float64)
            resolutions = np.asarray(labels["resolution"], dtype=np.float64)
            signal_to_noise = np.asarray(labels["signal_to_noise"], dtype=np.float64)
            # Keep only FT label peaks (finite resolution / S:N), mirroring
            # Thermo, where non-FT scans return no centroid label peaks at all.
            mask = (
                (mz_min <= masses)
                & (masses <= mz_max)
                & np.isfinite(resolutions)
                & np.isfinite(signal_to_noise)
            )
            out.append(
                {
                    "masses": masses[mask],
                    "intensities": intensities[mask],
                    "resolutions": resolutions[mask],
                    "signal_to_noise": signal_to_noise[mask],
                    "timestamp": s["retention_time"] * _SECONDS_PER_MINUTE,
                }
            )
        return out

    def average_centroids(
        self,
        scan_indices: list[int],
        ppm: int = 1,
        average: bool = False,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        # NumPy reimplementation of Thermo's AverageScans over centroids: pool
        # the per-scan FT label peaks, key them by physical frequency, and
        # ppm-bin them for m/z (sub-ppm exact), resolution and S:N
        # (approximate). The HEIGHT, however, is then sourced from the
        # frequency-averaged profile apex (below): Thermo re-centroids the
        # averaged profile, whose apex incurs an interpolation loss that a
        # per-scan centroid-apex sum does not, so the ppm-bin sum runs ~5-6%
        # high; the profile apex matches Thermo to ~1-2%.
        if ppm <= 0:
            raise ValueError(f"Invalid ppm value: {ppm}. ppm must be > 0.")

        mz_parts, int_parts, res_parts, sn_parts, scan_parts = [], [], [], [], []
        for ordinal, scan_number in enumerate(scan_indices):
            labels = self._raw.centroid_labels(int(scan_number))
            mz = np.asarray(labels["mz"], dtype=np.float64)
            intensity = np.asarray(labels["intensity"], dtype=np.float64)
            resolution = np.asarray(labels["resolution"], dtype=np.float64)
            signal_to_noise = np.asarray(labels["signal_to_noise"], dtype=np.float64)
            keep = np.isfinite(resolution) & np.isfinite(signal_to_noise)
            mz_parts.append(mz[keep])
            int_parts.append(intensity[keep])
            res_parts.append(resolution[keep])
            sn_parts.append(signal_to_noise[keep])
            scan_parts.append(np.full(int(keep.sum()), ordinal, dtype=np.intp))

        num_combined = len(scan_indices)
        mz_all = np.concatenate(mz_parts) if mz_parts else np.array([])
        int_all = np.concatenate(int_parts) if int_parts else np.array([])
        res_all = np.concatenate(res_parts) if res_parts else np.array([])
        sn_all = np.concatenate(sn_parts) if sn_parts else np.array([])
        scan_all = (
            np.concatenate(scan_parts) if scan_parts else np.array([], dtype=np.intp)
        )

        # Bin by frequency, report by label. The bin key is each scan's m/z
        # re-expressed on one calibration (_labels_on_one_calibration), so a
        # calibration step between scans cannot put one peak's centroids into
        # two bins; the reported m/z stays the intensity-weighted mean of the
        # labels as the instrument wrote them, which is what a merged peak has
        # always reported. Labels bin as written when a scan carries no B/C.
        # Each bin also remembers which scans its centroids came from, for the
        # scan-exclusive merge below.
        key_parts = self._labels_on_one_calibration(scan_indices, mz_parts)
        if key_parts is None:
            masses, summed, (resolutions, signal_to_noise), present, members = _ppm_bin(
                mz_all, int_all, [res_all, sn_all], ppm, groups=scan_all
            )
        else:
            key_all = np.concatenate(key_parts) if key_parts else np.array([])
            _, summed, (resolutions, signal_to_noise, masses), present, members = (
                _ppm_bin(key_all, int_all, [res_all, sn_all, mz_all], ppm, scan_all)
            )
            # Bins come out in key order. Two close bins with different scan
            # membership can have their label means in the other order, and
            # the merge and the profile-apex lookup below need sorted masses.
            order = np.argsort(masses, kind="stable")
            masses, summed, present = masses[order], summed[order], present[order]
            resolutions, signal_to_noise = resolutions[order], signal_to_noise[order]
            members = [members[k] for k in order]
        # Merge jitter-splits: the residual between-scan m/z wobble can still
        # exceed the ppm bin, splitting one peak's per-scan centroids into
        # adjacent bins. Collapse neighbours whose gap is well below the local
        # FWHM (so a real peak's split merges, while genuinely-resolved peaks
        # stay separate) -- mirroring Thermo's profile re-centroid, which never
        # splits one peak -- and, up to a wider gap, neighbours the scans hold
        # one or the other of, which is how one ion looks when its position
        # jitters from scan to scan by about its own width.
        (
            masses,
            summed,
            resolutions,
            signal_to_noise,
            present,
        ) = self._merge_split_centroids(
            masses, summed, resolutions, signal_to_noise, present, members, num_combined
        )
        # Scale the pooled per-scan S:N up to the averaged-spectrum S:N. Thermo
        # reads S:N off the noise-reduced *averaged* profile: averaging N scans
        # drops the noise ~sqrt(N), so a peak present in n of the N scans has
        # averaged S:N ~= (mean per-scan S:N) * n / sqrt(N) (the averaged peak
        # height carries n/N of the signal, the noise floor sqrt(N) less). The
        # intensity-weighted per-scan mean alone omits this and runs ~sqrt(N) too
        # low, which would drop near-threshold peaks the weak-peak filter keeps
        # under Thermo. S:N is intensity-scale-invariant, so this is independent
        # of the sum/mean (`average`) choice.
        if num_combined > 0:
            signal_to_noise = signal_to_noise * present / np.sqrt(num_combined)
        intensities = summed / num_combined if (average and num_combined) else summed

        # Source the height from the frequency-averaged profile apex (matches
        # Thermo's re-centroid-of-the-averaged-profile), falling back to the
        # ppm-bin value where the profile has no peak. Use the real measured
        # profile (reconstruct=False) -- a reconstruction is built *from* these
        # heights, and average_profile defaults to it, so this must be explicit
        # to avoid recursion.
        if masses.size:
            grid_mz, profile, _ = self.average_profile(
                scan_indices, ppm=ppm, average=average, reconstruct=False
            )
            if grid_mz.size:
                intensities = self._heights_from_profile_apex(
                    masses, intensities, grid_mz, profile
                )
        return masses, intensities, resolutions, signal_to_noise

    def _labels_on_one_calibration(
        self, scan_indices: list[int], mz_parts: list[np.ndarray]
    ) -> list[np.ndarray] | None:
        """Re-express each scan's centroid m/z on one scan's calibration.

        An ion's frequency is the same in every scan; what moves its centroid
        m/z between scans is the per-scan frequency->m/z calibration, i.e. the
        Conversion Parameters B/C, which carry the lock-mass and other
        compensations. Usually that is a sub-ppm wobble, but when a lock mass
        engages part-way through a file (the instrument finds the lock-mass ion
        only after the first scans) the calibration steps by a few ppm at once.
        At low m/z, where the FWHM is itself only a few ppm, such a step is
        wider than the ppm bin and than half a FWHM, so one peak's centroids
        land in two bins and the jitter-split merge cannot recover them.
        Converting every scan's labels to frequency with its own B/C and back
        with one reference calibration removes the step -- the profile
        averaging does the same, see ``_average_profile_in_frequency`` -- so the
        bin groups a peak's centroids by physical frequency.

        Returns one array per scan, aligned with ``mz_parts``, or None when a
        selected scan carries no B/C (non-FTMS data), in which case the caller
        bins the labels as written.
        """
        params = [self._profile_conversion_params(int(n)) for n in scan_indices]
        if not params or any(b is None for b, _ in params):
            return None
        # The reference only fixes the key's scale, so any scan serves; the
        # densest one, as the profile averaging picks.
        ref = max(range(len(mz_parts)), key=lambda i: mz_parts[i].size)
        b_ref, c_ref = params[ref]
        keys = []
        for (b, c), mz in zip(params, mz_parts):
            if mz.size == 0 or (b == b_ref and c == c_ref):
                keys.append(mz)
                continue
            key = mz.astype(np.float64, copy=True)
            ok = np.isfinite(mz) & (mz > 0)
            f2 = self._mz_to_freq(mz[ok], b, c) ** 2
            key[ok] = b_ref / f2 + c_ref / (f2 * f2)
            keys.append(key)
        return keys

    @staticmethod
    def _merge_split_centroids(
        masses: np.ndarray,
        intensities: np.ndarray,
        resolutions: np.ndarray,
        sn: np.ndarray,
        present: np.ndarray,
        members: list[tuple[np.ndarray, np.ndarray]] | None = None,
        n_scans: int | None = None,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Merge adjacent centroids that are a jitter-split of one peak.

        Cluster where the m/z gap to the neighbour is below
        ``_AVG_CENTROID_MERGE_FWHM`` * the local FWHM (= m/z / resolution): a
        peak's split (gap << FWHM) collapses, while genuinely-resolved peaks
        (gap >= FWHM) stay separate. Per cluster: intensity-weighted m/z, summed
        intensity, intensity-weighted resolution / S:N, and summed ``present``
        (the split halves' scans add up to the merged peak's scan count).

        With ``members`` (per bin, the scan and the intensity of each of its
        centroids) and ``n_scans`` (how many scans were pooled) a second, wider
        rule applies, up to ``_AVG_CENTROID_EXCLUSIVE_MERGE_FWHM`` * the local
        FWHM: neighbours are merged when the scans hold one side or the other,
        not both. An ion whose measured position jitters from scan to scan by
        about its own width (seen on a dominant ion at a few million counts per
        scan, with the calibration steady) yields one centroid per scan,
        landing in one of two bins, so the bins alternate over the scans; two
        ions that close are resolved within a scan, so their bins share scans
        and stay apart. Concretely, at most ``_AVG_CENTROID_EXCLUSIVE_OVERLAP``
        of the smaller side's intensity may sit in scans where the other side
        has a centroid too -- a stray weak label in one scan does not veto the
        merge, while a real minor ion beside a major one, present in the same
        scans, does -- the two sides together must cover at least
        ``_AVG_CENTROID_EXCLUSIVE_COVERAGE`` of the scans, so that two noise
        labels from different scans, which are trivially exclusive, are left
        alone (a transient ion's jitter split is therefore left as it was),
        and each side must hold at least ``_AVG_CENTROID_EXCLUSIVE_MIN_SIDE``
        of the scans, two at the least: one ion alternating between two
        positions fills both sides, whereas the wobbling fragments beside an
        intense peak come and go within a couple of scans, and Thermo reports
        those as the separate peaks they look like. The per-scan intensities
        are carried across the cluster being built, so a fence of satellites
        cannot chain through a peak pair by pair.
        """
        if masses.size <= 1:
            return masses, intensities, resolutions, sn, present
        fwhm_ppm = np.where(resolutions > 0, 1e6 / resolutions, np.inf)
        gap_ppm = np.diff(masses) / masses[:-1] * 1e6
        local = np.minimum(fwhm_ppm[:-1], fwhm_ppm[1:])
        merge = gap_ppm < _AVG_CENTROID_MERGE_FWHM * local
        if members is not None and n_scans:
            exclusive = gap_ppm < _AVG_CENTROID_EXCLUSIVE_MERGE_FWHM * local

            def per_scan(first: int, last: int) -> np.ndarray:
                scans = np.concatenate([members[k][0] for k in range(first, last + 1)])
                weights = np.concatenate(
                    [members[k][1] for k in range(first, last + 1)]
                )
                return np.bincount(scans, weights=weights, minlength=n_scans)

            for i in np.flatnonzero(exclusive & ~merge):
                # The cluster bin i belongs to runs back over merged pairs;
                # decisions made earlier in this loop are already in `merge`.
                start = i
                while start > 0 and merge[start - 1]:
                    start -= 1
                cluster = per_scan(start, i)
                nxt = per_scan(i + 1, i + 1)
                smaller = min(cluster.sum(), nxt.sum())
                shared = np.minimum(cluster, nxt).sum()
                covered = np.count_nonzero((cluster > 0) | (nxt > 0)) / n_scans
                side = min(np.count_nonzero(cluster > 0), np.count_nonzero(nxt > 0))
                if (
                    smaller > 0
                    and shared <= _AVG_CENTROID_EXCLUSIVE_OVERLAP * smaller
                    and covered >= _AVG_CENTROID_EXCLUSIVE_COVERAGE
                    and side >= max(2, _AVG_CENTROID_EXCLUSIVE_MIN_SIDE * n_scans)
                ):
                    merge[i] = True
        starts = np.concatenate(([0], np.flatnonzero(~merge) + 1))
        isum = np.add.reduceat(intensities, starts)
        counts = np.diff(np.append(starts, masses.size))
        safe = np.where(isum > 0, isum, 1.0)
        nonzero = isum > 0

        def agg(values: np.ndarray) -> np.ndarray:
            wmean = np.add.reduceat(values * intensities, starts) / safe
            pmean = np.add.reduceat(values, starts) / counts
            return np.where(nonzero, wmean, pmean)

        present_merged = np.add.reduceat(present, starts)
        return agg(masses), isum, agg(resolutions), agg(sn), present_merged

    @staticmethod
    def _heights_from_profile_apex(
        masses: np.ndarray,
        fallback: np.ndarray,
        grid_mz: np.ndarray,
        profile: np.ndarray,
    ) -> np.ndarray:
        """Re-centroid the averaged profile for height: detect profile local
        maxima, take each one's parabolic-vertex apex (which recovers the
        continuous apex the discrete max under-shoots), and assign it to its
        nearest centroid within a tight window. Each profile peak maps to one
        centroid (via maximum.at), so dense centroids cannot share a neighbour's
        apex; centroids with no matched profile peak keep the ppm-bin `fallback`.
        """
        out = fallback.astype(np.float64).copy()
        if profile.size < 3 or masses.size == 0:
            return out
        # profile local maxima
        mid = profile[1:-1]
        pk = np.where((mid > profile[:-2]) & (mid >= profile[2:]) & (mid > 0))[0] + 1
        if pk.size == 0:
            return out
        # parabolic-vertex apex height per peak (the continuous apex the discrete
        # max under-shoots), clamped so a flat/noisy top (denom -> 0) cannot blow
        # the vertex up far above the sampled max.
        y0, y1, y2 = profile[pk - 1], profile[pk], profile[pk + 1]
        denom = y0 - 2.0 * y1 + y2
        corr = np.where(denom < 0, -0.125 * (y2 - y0) ** 2 / denom, 0.0)
        apex = y1 + np.minimum(corr, 0.25 * y1)
        # nearest centroid to each profile peak, kept only within the window
        pmz = grid_mz[pk]
        j = np.clip(np.searchsorted(masses, pmz), 1, masses.size - 1)
        left_closer = np.abs(pmz - masses[j - 1]) <= np.abs(pmz - masses[j])
        nidx = np.where(left_closer, j - 1, j)
        within = np.abs(masses[nidx] - pmz) / pmz * 1e6 <= _AVG_CENTROID_HEIGHT_PPM
        # one height per centroid (tallest matched peak wins)
        cand = np.full(masses.size, -np.inf)
        np.maximum.at(cand, nidx[within], apex[within])
        # Apply the profile apex only where it is a MODEST refinement of the
        # ppm-bin (the interpolation-loss regime, ~0.93x). A large disagreement
        # means an intermittent/weak peak (whose averaged-profile apex is far
        # below the per-scan-apex sum) or a noise maximum -- there the ppm-bin is
        # the safer estimate. This corrects the systematic ~5-6% high on real
        # peaks without destabilising the weak-peak aggregate.
        valid = (cand > -np.inf) & (fallback > 0)
        ratio = np.zeros(masses.size)
        ratio[valid] = cand[valid] / fallback[valid]
        apply = (
            valid
            & (ratio >= _AVG_CENTROID_HEIGHT_BAND[0])
            & (ratio <= _AVG_CENTROID_HEIGHT_BAND[1])
        )
        out[apply] = cand[apply]
        return out

    def centroids_meta(self) -> dict:
        result = {"time": [], "data": []}
        for s in self._all_scans():
            labels = self._raw.centroid_labels(int(s["scan_number"]))
            mzs = np.asarray(labels["mz"], dtype=np.float64)
            intensities = np.asarray(labels["intensity"], dtype=np.float64)
            resolutions = np.asarray(labels["resolution"], dtype=np.float64)
            noises = np.asarray(labels["noise"], dtype=np.float64)

            valid = (
                np.isfinite(resolutions)
                & (resolutions > 0)
                & np.isfinite(intensities)
                & (intensities > 0)
            )
            result["time"].append(s["retention_time"] * _SECONDS_PER_MINUTE)
            result["data"].append(
                {
                    "intensities": intensities[valid].tolist(),
                    "mzs": mzs[valid].tolist(),
                    "resolutions": resolutions[valid].tolist(),
                    "noises": noises[valid].tolist(),
                }
            )
        return result

    def profile_per_scan(
        self,
        polarity: Polarity | None = None,
        t_min: float | None = None,
        t_max: float | None = None,
        ms_type: MsType | None = "Ms",
        mz_min: float | None = None,
        mz_max: float | None = None,
    ) -> tuple[list[np.ndarray], list[np.ndarray], np.ndarray]:
        mz_min, mz_max = self._validate_mz_range(mz_min, mz_max)
        selected = self._selected(polarity, t_min, t_max, ms_type)

        scan_mzs: list[np.ndarray] = []
        scan_specs: list[np.ndarray] = []
        for s in selected:
            mz, intensities = self._raw.profile(int(s["scan_number"]))
            mz = np.asarray(mz, dtype=np.float64)
            intensities = np.asarray(intensities, dtype=np.float64)
            mask = np.logical_and(mz_min <= mz, mz <= mz_max)
            scan_mzs.append(mz[mask])
            scan_specs.append(intensities[mask])
        times = np.array([s["retention_time"] * _SECONDS_PER_MINUTE for s in selected])
        return scan_mzs, scan_specs, times

    def average_profile(
        self,
        scan_indices: list[int],
        ppm: int = 1,
        average: bool = False,
        reconstruct: bool = False,
    ) -> tuple[np.ndarray, np.ndarray, int]:
        # reconstruct=True returns a Thermo-style profile reconstructed as one
        # Gaussian per centroid (center=m/z, height=intensity, FWHM=m/z/res).
        # Thermo's AverageScans profile *is* such a reconstruction (verified:
        # profile local-maxima count == centroid count exactly, baseline floor
        # ~1e-10 of base peak, peaks Gaussian to <1%); it overlays the centroids
        # exactly and is the right choice for *display*. The default
        # reconstruct=False returns the real measured profile, which is what the
        # instrument-function fit needs -- the fit gets too few quality peaks off
        # the reconstruction (its idealised shape/grid), so the real, faithful
        # signal must drive the quantitative path.
        if reconstruct:
            num_combined = len(scan_indices)
            masses, intensities, resolutions, _ = self.average_centroids(
                scan_indices, ppm=ppm, average=average
            )
            grid, summed = self._reconstruct_profile(masses, intensities, resolutions)
            return grid, summed, num_combined
        # NumPy reimplementation of Thermo's AverageScans over profile data.
        # AverageScans averages in the FREQUENCY domain: an ion's
        # physical frequency is identical across scans, and the between-scan
        # "jitter" lives only in the per-scan freq->m/z calibration. Averaging in
        # frequency therefore aligns the peaks (no broadening: FWHM == single
        # scan) and yields the mean profile on a native-density grid -- whereas a
        # constant-ppm m/z grid + interpolate-and-sum inflates the apex (~+8%, no
        # interpolation loss) and leaves peaks asymmetric (~2 ppm). Steps:
        #   1. Convert each scan's profile m/z back to frequency using that
        #      scan's Conversion Parameter B/C (m/z = B/f^2 + C/f^4).
        #   2. Output grid = the union of the scans' frequencies, quantized to a
        #      native-density (FFT-bin) grid -- occupied cells only, so it is
        #      bounded and matches the density Thermo emits (~30k points).
        #   3. Linear-interpolate each scan onto the freq grid and sum. The peaks
        #      are aligned, so this reproduces Thermo's apex (= mean *
        #      ScansCombined) and FWHM; no integral rescale is needed.
        #   4. Convert the freq grid back to m/z (reference calibration), then
        #      calibrate the axis to the centroid labels: the freq->m/z conversion
        #      still omits Thermo's per-scan calibration compensations (~10-20
        #      ppm), which the exact centroid m/z carry.
        # Falls back to a constant-ppm m/z grid when the Conversion Parameters
        # are unavailable (non-FTMS data).
        if ppm <= 0:
            raise ValueError(f"Invalid ppm value: {ppm}. ppm must be > 0.")

        num_combined = len(scan_indices)
        scans: list[tuple[np.ndarray, np.ndarray, float | None, float | None]] = []
        for scan_number in scan_indices:
            mz, intensity = self._raw.profile(int(scan_number))
            mz = np.asarray(mz, dtype=np.float64)
            intensity = np.asarray(intensity, dtype=np.float64)
            if mz.size:
                b, c = self._profile_conversion_params(int(scan_number))
                scans.append((mz, intensity, b, c))
        if not scans:
            return np.array([]), np.array([]), num_combined

        if all(b is not None for (_, _, b, _) in scans):
            grid, summed = self._average_profile_in_frequency(scans)
        else:
            grid, summed = self._average_profile_in_mz(scans)

        if average and num_combined:
            summed = summed / num_combined

        grid = self._align_profile_grid_to_centroids(scan_indices, grid, summed)
        grid, summed = self._zerofill_baseline(grid, summed)
        return grid, summed, num_combined

    @staticmethod
    def _zerofill_baseline(
        grid: np.ndarray, summed: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        """Insert baseline zeros around peak clusters. OpenTFRaw's profile()
        omits the zeros Thermo's SegmentedScan carries around each peak, so the
        averaged profile (occupied cells only) never returns to 0 between
        clusters. Add a zero just outside each cluster edge -- at every m/z gap
        large versus the within-cluster spacing -- so the profile drops to
        baseline between peaks, matching Thermo and the pipeline's other paths
        (which fillna(0))."""
        if grid.size < 2:
            return grid, summed
        gap_ppm = np.diff(grid) / grid[:-1] * 1e6
        med = float(np.median(gap_ppm))
        if not np.isfinite(med) or med <= 0:
            return grid, summed
        boundary = np.flatnonzero(gap_ppm > _ZEROFILL_GAP_FACTOR * med)
        if boundary.size == 0:
            return grid, summed
        off = _ZEROFILL_EDGE_PPM / 1e6
        left_z = grid[boundary] * (1 + off)
        right_z = grid[boundary + 1] * (1 - off)
        new_mz = np.concatenate([grid, left_z, right_z])
        new_v = np.concatenate([summed, np.zeros(left_z.size + right_z.size)])
        order = np.argsort(new_mz, kind="stable")
        return new_mz[order], new_v[order]

    def _profile_conversion_params(
        self, scan_number: int
    ) -> tuple[float | None, float | None]:
        """Per-scan freq->m/z Conversion Parameter B/C from the trailer, or
        (None, None) if unavailable (non-FTMS data)."""
        params = self._raw.scan_parameters(scan_number)
        if not params:
            return None, None
        b = params.get("Conversion Parameter B:")
        c = params.get("Conversion Parameter C:")
        if isinstance(b, (int, float)) and isinstance(c, (int, float)) and b:
            return float(b), float(c)
        return None, None

    @staticmethod
    def _reconstruct_profile(
        masses: np.ndarray,
        intensities: np.ndarray,
        resolutions: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Build a Thermo-style profile: one Gaussian per centroid (center =
        m/z, height = intensity, FWHM = m/z / resolution), summed on a per-peak
        sample grid. Matches Thermo's reconstructed AverageScans profile and
        overlays the centroids exactly (display parity). See ``average_profile``.
        """
        sigma_per_fwhm = 1.0 / (2.0 * np.sqrt(2.0 * np.log(2.0)))
        valid = (resolutions > 0) & (intensities > 0) & (masses > 0)
        cm, ci = masses[valid], intensities[valid]
        sigma = (cm / resolutions[valid]) * sigma_per_fwhm
        if cm.size == 0:
            return np.array([]), np.array([])
        offs = np.linspace(-_RECON_SIGMA, _RECON_SIGMA, _RECON_PTS)
        grid = np.unique((cm[:, None] + sigma[:, None] * offs).ravel())
        summed = np.zeros_like(grid)
        for c, h, s in zip(cm, ci, sigma):
            lo = int(np.searchsorted(grid, c - _RECON_SIGMA * s))
            hi = int(np.searchsorted(grid, c + _RECON_SIGMA * s))
            if hi > lo:
                summed[lo:hi] += h * np.exp(-0.5 * ((grid[lo:hi] - c) / s) ** 2)
        return grid, summed

    @staticmethod
    def _mz_to_freq(mz: np.ndarray, b: float, c: float) -> np.ndarray:
        """Invert the Orbitrap m/z = B/f^2 + C/f^4 conversion (Newton's method).

        Frequency is calibration-independent, so this recovers the physical
        frequency axis on which a peak aligns across scans.
        """
        f = np.sqrt(b / mz)
        for _ in range(_AVG_PROFILE_FREQ_NEWTON):
            f2 = f * f
            g = b / f2 + c / (f2 * f2) - mz
            dg = -2.0 * b / (f2 * f) - 4.0 * c / (f2 * f2 * f)
            f = f - g / dg
        return f

    def _average_profile_in_frequency(
        self, scans: list[tuple[np.ndarray, np.ndarray, float, float]]
    ) -> tuple[np.ndarray, np.ndarray]:
        """Average profiles in the frequency domain (see average_profile)."""
        freqs = [self._mz_to_freq(mz, b, c) for (mz, _, b, c) in scans]
        ref = max(range(len(freqs)), key=lambda i: freqs[i].size)  # densest scan
        d = np.diff(np.sort(freqs[ref]))
        d = d[d > 0]
        # native FFT bin spacing = the within-cluster spacing (robust to the
        # large inter-cluster gaps via the lower half of the diffs).
        df = float(np.median(d[d <= np.median(d)])) if d.size else 0.0
        if df <= 0:
            return self._average_profile_in_mz(scans)

        f_all = np.concatenate(freqs)
        f0 = float(f_all.min())
        occupied = np.unique(np.floor((f_all - f0) / df).astype(np.int64))
        fgrid = f0 + (occupied + 0.5) * df

        summed = np.zeros(fgrid.shape, dtype=np.float64)
        for f, (_, intensity, _, _) in zip(freqs, scans):
            order = np.argsort(f, kind="stable")
            f_sorted, int_sorted = f[order], intensity[order]
            lo = int(np.searchsorted(fgrid, f_sorted[0], side="left"))
            hi = int(np.searchsorted(fgrid, f_sorted[-1], side="right"))
            if hi <= lo:
                continue
            seg = fgrid[lo:hi]
            vals = np.interp(seg, f_sorted, int_sorted)
            # Zero this scan's contribution in its inter-cluster gaps. OpenTFRaw's
            # profile() omits points between peak clusters, so np.interp would ramp
            # straight across a gap; summed over scans those spurious ramps inflate
            # and flat-top sparse/intermittent peaks (a peak present in one scan
            # picks up 11 ramps from the others). Keep only grid points within a
            # couple of FFT bins of an actual sample of this scan.
            j = np.clip(np.searchsorted(f_sorted, seg), 1, f_sorted.size - 1)
            near = np.minimum(np.abs(seg - f_sorted[j - 1]), np.abs(seg - f_sorted[j]))
            vals[near > _AVG_PROFILE_GAP_DF * df] = 0.0
            summed[lo:hi] += vals

        # Convert the freq grid back to m/z with the reference scan's calibration.
        b_ref, c_ref = scans[ref][2], scans[ref][3]
        f2 = fgrid * fgrid
        mz_grid = b_ref / f2 + c_ref / (f2 * f2)
        order = np.argsort(mz_grid)
        return mz_grid[order], summed[order]

    def _average_profile_in_mz(
        self,
        scans: list[tuple[np.ndarray, np.ndarray, float | None, float | None]],
    ) -> tuple[np.ndarray, np.ndarray]:
        """Fallback: constant-ppm m/z grid + integral-conserving interpolation
        sum, used when the freq->m/z Conversion Parameters are unavailable."""
        mz_all = np.concatenate([mz for (mz, _, _, _) in scans])
        lo = float(mz_all.min())
        log_step = np.log1p(_AVG_PROFILE_GRID_PPM / 1e6)
        occupied = np.unique(np.floor(np.log(mz_all / lo) / log_step).astype(np.int64))
        grid = lo * np.exp((occupied + 0.5) * log_step)

        summed = np.zeros(grid.shape, dtype=np.float64)
        target_integral = 0.0
        for mz, intensity, _, _ in scans:
            order = np.argsort(mz, kind="stable")
            mz_sorted, int_sorted = mz[order], intensity[order]
            target_integral += float(np.trapz(int_sorted, mz_sorted))
            a = int(np.searchsorted(grid, mz_sorted[0], side="left"))
            b = int(np.searchsorted(grid, mz_sorted[-1], side="right"))
            if b > a:
                summed[a:b] += np.interp(grid[a:b], mz_sorted, int_sorted)
        grid_integral = float(np.trapz(summed, grid))
        if grid_integral > 0:
            summed *= target_integral / grid_integral
        return grid, summed

    def _align_profile_grid_to_centroids(
        self,
        scan_indices: list[int],
        grid: np.ndarray,
        summed: np.ndarray,
    ) -> np.ndarray:
        """Correct the profile m/z axis to match the file's centroid labels.

        OpenTFRaw converts the frequency-domain profile to m/z with the base
        polynomial coefficients only; Thermo additionally applies per-scan
        calibration compensations, leaving OpenTFRaw's profile m/z offset by
        ~10-20 ppm (m/z dependent) while the centroid labels carry the fully
        calibrated m/z. We use the centroids as a reference: match the strongest
        well-separated profile peaks to their nearest centroid, reject outliers,
        and fit a low-order m/z correction. Returns the corrected grid, or the
        original grid unchanged when there is too little signal to fit reliably.
        """
        if grid.size == 0:
            return grid

        # Reference m/z from centroid labels of a sample of the selected scans
        # (strong peaks appear in every scan, so a sample keeps this cheap on
        # large files).
        step = max(1, len(scan_indices) // _AVG_PROFILE_CALIB_SCANS)
        ref_parts = []
        for scan_number in scan_indices[::step][:_AVG_PROFILE_CALIB_SCANS]:
            mz = np.asarray(
                self._raw.centroid_labels(int(scan_number))["mz"], dtype=np.float64
            )
            if mz.size:
                ref_parts.append(mz)
        if not ref_parts:
            return grid
        ref_mz = np.unique(np.concatenate(ref_parts))

        # Anchors: the strongest, well-separated profile peaks.
        anchor_prof = []
        for k in np.argsort(summed)[::-1]:
            if summed[k] <= 0:
                break
            c = grid[k]
            if all(
                abs(c - p) / c * 1e6 >= _AVG_PROFILE_CALIB_SEP_PPM for p in anchor_prof
            ):
                anchor_prof.append(c)
            if len(anchor_prof) >= _AVG_PROFILE_CALIB_MAX_ANCHORS:
                break
        anchor_prof = np.asarray(anchor_prof)
        if anchor_prof.size < _AVG_PROFILE_CALIB_MIN_ANCHORS:
            return grid

        def nearest(vals: np.ndarray) -> np.ndarray:
            idx = np.clip(np.searchsorted(ref_mz, vals), 1, ref_mz.size - 1)
            left, right = ref_mz[idx - 1], ref_mz[idx]
            return np.where(np.abs(vals - left) <= np.abs(vals - right), left, right)

        # Pass 1: nearest centroid gives the gross (median) offset. Pass 2:
        # re-match each anchor to the centroid nearest its offset-corrected
        # position and keep only tight matches -- this locks onto the right peak
        # and drops mismatches that a single nearest-search would let through.
        n1 = nearest(anchor_prof)
        med = np.median((n1 - anchor_prof) / anchor_prof * 1e6)
        expected = anchor_prof * (1.0 + med / 1e6)
        anchor_ref = nearest(expected)
        resid_ppm = np.abs(anchor_ref - expected) / expected * 1e6
        keep = resid_ppm <= _AVG_PROFILE_CALIB_TIGHT_PPM
        anchor_prof, anchor_ref = anchor_prof[keep], anchor_ref[keep]
        if anchor_prof.size < _AVG_PROFILE_CALIB_MIN_ANCHORS:
            return grid

        # Fit centroid_mz = a + b*profile_mz (LINEAR) and remap the grid. The Da
        # offset is ~linear in m/z; a quadratic over-fits the (dense, mid-m/z)
        # anchors and mis-extrapolates the low-m/z curvature -- it left a ~5 ppm
        # systematic below m/z 120 while a line keeps low and high m/z balanced
        # (within ~1.5 ppm). Equivalent to the physical A + B/f^2 calibration form
        # since profile m/z is ~ B/f^2.
        coeffs = np.polyfit(anchor_prof, anchor_ref, 1)
        return np.polyval(coeffs, grid)

    def xic(
        self,
        mzs,
        ppm: float = 5,
        polarity: Polarity | None = None,
        t_min: float | None = None,
        t_max: float | None = None,
        ms_type: MsType | None = "Ms",
    ) -> tuple[np.ndarray, np.ndarray]:
        # NumPy reimplementation of the Thermo MassRange chromatogram: for each
        # target m/z, sum the centroid intensities falling in its ppm
        # window [mz(1-ppm), mz(1+ppm)], per selected scan.
        #
        # Vectorized per scan via a sorted prefix sum: peaks in [low, high] are
        # the half-open index range [searchsorted(left), searchsorted(right)),
        # so the window sum is cumsum[right] - cumsum[left] for all targets at
        # once. Scales to "all peaks as targets" on large files.
        mzs = np.asarray(mzs, dtype=float)
        selected = self._selected(polarity, t_min, t_max, ms_type)
        lows = mzs - mzs * ppm / 1e6
        highs = mzs + mzs * ppm / 1e6

        intensities = np.zeros((len(mzs), len(selected)), dtype=np.float64)
        for j, scan in enumerate(selected):
            scan_mz = np.asarray(scan["mz"], dtype=np.float64)
            scan_int = np.asarray(scan["intensity"], dtype=np.float64)
            order = np.argsort(scan_mz)
            scan_mz = scan_mz[order]
            prefix = np.concatenate(([0.0], np.cumsum(scan_int[order])))
            left = np.searchsorted(scan_mz, lows, side="left")
            right = np.searchsorted(scan_mz, highs, side="right")
            intensities[:, j] = prefix[right] - prefix[left]

        times = np.array([s["retention_time"] * _SECONDS_PER_MINUTE for s in selected])
        return intensities, times

    def ms2_precursor_by_scan(
        self,
        polarity: Polarity | None = None,
        t_min: float | None = None,
        t_max: float | None = None,
    ) -> dict[int, float]:
        # Mirror the Thermo path: parse the precursor from the rendered filter
        # string. OpenTFRaw's build_filter now renders it for Exploris too once
        # the scan-event reaction is decoded (e.g. "... ms2 100.0757@hcd3.00").
        out: dict[int, float] = {}
        for s in self._selected(polarity, t_min, t_max, ms_type="Ms2"):
            scan_number = int(s["scan_number"])
            filter_string = self._raw.scan_filter(scan_number) or s.get("filter_string")
            if not filter_string:
                continue
            match = re.search(r"ms2 ([\d.]+)@", filter_string)
            if match:
                out[scan_number] = float(match.group(1))
        return out

    def ms2_acquisition_info(
        self,
        polarity: Polarity | None = None,
        t_min: float | None = None,
        t_max: float | None = None,
    ) -> tuple[float | None, dict[int, str]]:
        # The calibrated "HCD Energy V:" and "MS2 Isolation Width:" live in the
        # per-scan trailer (generic record), surfaced by the scan_parameters()
        # accessor.

        def _as_float(value) -> float:
            if isinstance(value, (int, float)):
                return float(value)
            return float(str(value).replace(",", "."))

        isolation_widths: set[float] = set()
        scan_idx_to_hcd: dict[int, str] = {}
        for s in self._selected(polarity, t_min, t_max, ms_type="Ms2"):
            scan_number = int(s["scan_number"])
            params = self._raw.scan_parameters(scan_number) or {}
            width = params.get("MS2 Isolation Width:")
            if width is not None and str(width) != "":
                # round away the f32->f64 noise so identical widths dedupe.
                isolation_widths.add(round(_as_float(width), 6))
            hcd = params.get("HCD Energy V:")
            scan_idx_to_hcd[scan_number] = "" if hcd is None else str(hcd)

        isolation_widths.discard(None)
        if len(isolation_widths) == 1:
            isolation_width = float(isolation_widths.pop())
        else:
            raise ValueError("Multiple isolation widths found for MS2 scans.")
        return isolation_width, scan_idx_to_hcd

    def ms2_centroids_for_scans(
        self, scan_indices: list[int]
    ) -> tuple[list[dict], list[float]]:
        by_num = {int(s["scan_number"]): s for s in self._all_scans()}
        centroids: list[dict] = []
        tic_values: list[float] = []
        for scan_idx in scan_indices:
            s = by_num[int(scan_idx)]
            labels = self._raw.centroid_labels(int(scan_idx))
            masses = np.asarray(labels["mz"], dtype=np.float64)
            intensities = np.asarray(labels["intensity"], dtype=np.float64)
            resolutions = np.asarray(labels["resolution"], dtype=np.float64)
            signal_to_noise = np.asarray(labels["signal_to_noise"], dtype=np.float64)
            # Keep only FT label peaks (finite resolution / S:N), as elsewhere.
            mask = np.isfinite(resolutions) & np.isfinite(signal_to_noise)
            centroids.append(
                {
                    "masses": masses[mask],
                    "intensities": intensities[mask],
                    "resolutions": resolutions[mask],
                    "signal_to_noise": signal_to_noise[mask],
                    "timestamp": s["retention_time"] * _SECONDS_PER_MINUTE,
                }
            )
            tic_values.append(float(s["total_ion_current"]))
        return centroids, tic_values


def open_backend(datafile_path: str) -> ReaderBackend:
    """Open ``datafile_path`` with the backend selected by ``MASCOPE_THERMO_BACKEND``.

    Returns a context manager. Defaults to the OpenTFRaw backend; set
    ``MASCOPE_THERMO_BACKEND=thermo`` to use the legacy Thermo RawFileReader.
    """
    name = os.environ.get(ENV_BACKEND, "opentfraw").lower()
    if name == "thermo":
        return ThermoBackend(datafile_path)
    if name == "opentfraw":
        return OpenTFRawBackend(datafile_path)
    raise ValueError(
        f"Unknown {ENV_BACKEND}={name!r}; expected 'thermo' or 'opentfraw'."
    )
