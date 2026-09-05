from dataclasses import dataclass

import numpy as np

import mascope_sdk as msdk
from mascope_tools.alignment.calibration import CentroidedSpectrum

from .config import DEFAULT_NOISE_THRESHOLD, DEFAULT_PARENT_PEAK_TOLERANCE


@dataclass(frozen=True, order=True)
class Ms2Group:
    """One MS2 group: a precursor measured at one activation.

    The unit an MS2 spectrum belongs to. A precursor alone does not identify a
    spectrum, because a stepped-energy acquisition measures one precursor at
    several collision energies - so this carries the activation too, and it is
    what every per-spectrum mapping here is keyed by. ``parent_peak_mz`` is the
    number to do m/z arithmetic with.
    """

    parent_peak_mz: float
    activation: str = ""

    @property
    def key(self) -> str:
        """The server's key for this group, ``"<parent m/z>@<activation>"``."""
        if not self.activation:
            return f"{self.parent_peak_mz}"
        return f"{self.parent_peak_mz}@{self.activation}"

    def __str__(self) -> str:
        label = f"{self.parent_peak_mz:.4f} m/z"
        return f"{label} ({self.activation})" if self.activation else label


class DataExtractor:
    """Thin client for MS2 analysis.

    Uses the Mascope SDK's MS2 sub-resource to fetch pre-processed data.
    """

    def __init__(
        self,
        mascope: msdk.MascopeClient,
        sample_item_id: str,
        params: dict | None = None,
    ):
        """
        Initialize the DataExtractor by fetching MS2 analysis data from the server.

        :param mascope: An instance of the MascopeClient to use for API calls
        :type mascope: msdk.MascopeClient
        :param sample_item_id: ID of the sample item to analyze
        :type sample_item_id: str
        :param params: Optional parameters for data extraction and processing:
            - noise_threshold: Intensity threshold for filtering out noise peaks (default: 10)
            - parent_peak_tolerance: Tolerance in Da for merging parent peaks (default: 0.001)
        :type params: dict, optional
        :raises ValueError: If the server returns no data for the sample.
        """
        if isinstance(params, dict):
            self.params = params
        else:
            self.params = {}

        self._mascope = mascope
        self._sample_item_id = sample_item_id
        self._ms2 = mascope.samples.ms2(sample_item_id)

        noise_threshold = self.params.get("noise_threshold", DEFAULT_NOISE_THRESHOLD)
        parent_peak_tolerance = self.params.get(
            "parent_peak_tolerance", DEFAULT_PARENT_PEAK_TOLERANCE
        )

        summary = self._ms2.get_summary(parent_peak_tolerance=parent_peak_tolerance)
        if summary is None:
            raise ValueError("Failed to retrieve MS2 summary for the sample.")

        summary_groups = summary.get("groups", [])
        isolation_width = summary.get("isolation_width", None)
        if not summary_groups or isolation_width is None:
            raise ValueError(
                "No MS2 scans were found for the sample; MS2 analysis requires "
                "at least one parent peak group and a valid isolation width."
            )

        self.groups = [
            Ms2Group(float(g["parent_peak_mz"]), g.get("activation", ""))
            for g in summary_groups
        ]
        self.parent_peaks = np.array(summary.get("parent_peaks", []))
        self.isolation_width = isolation_width
        # Keyed per group, not per precursor: a stepped-energy acquisition uses
        # a different collision energy in each of its groups.
        self.hcd_energy_map: dict[Ms2Group, list[float]] = {
            group: list(g.get("hcd_energy", []))
            for group, g in zip(self.groups, summary_groups, strict=True)
        }

        centroids_data = self._ms2.get_averaged_centroids(
            noise_threshold=noise_threshold,
            parent_peak_tolerance=parent_peak_tolerance,
        )
        self.ms2_spectra: dict[Ms2Group, CentroidedSpectrum] = {}
        if centroids_data:
            for data in centroids_data.values():
                group = Ms2Group(
                    float(data["parent_peak_mz"]), data.get("activation", "")
                )
                mz = np.array(data["mz"])
                intensity = np.array(data["intensity"])
                resolution = np.array(data.get("resolution", []))
                signal_to_noise = np.array(data.get("signal_to_noise", []))
                if resolution.size != mz.size:
                    resolution = np.zeros_like(mz)
                if signal_to_noise.size != mz.size:
                    signal_to_noise = np.zeros_like(mz)
                self.ms2_spectra[group] = CentroidedSpectrum(
                    mz=mz,
                    intensity=intensity,
                    signal_to_noise=signal_to_noise,
                    resolution=resolution,
                )
        # Ensure every group has an entry
        for group in self.groups:
            if group not in self.ms2_spectra:
                self.ms2_spectra[group] = CentroidedSpectrum(
                    mz=np.array([]),
                    intensity=np.array([]),
                    signal_to_noise=np.array([]),
                    resolution=np.array([]),
                )

        # MS2 TIC per group
        self.ms2_tic: dict[Ms2Group, float] = {
            group: float(spec.intensity.sum())
            for group, spec in self.ms2_spectra.items()
        }

        # Lazy-loaded properties
        self._ms1_spectrum: CentroidedSpectrum | None = None
        self._parent_peak_intensities: dict | None = None
        self._ms1_isolation_tic: dict | None = None

    @property
    def ms1_spectrum(self) -> CentroidedSpectrum:
        """Averaged MS1 spectrum."""
        if self._ms1_spectrum is None:
            self._load_ms1_spectrum()
        assert self._ms1_spectrum is not None
        return self._ms1_spectrum

    @property
    def ms1_tic(self) -> float:
        """Total ion count from averaged MS1 spectrum."""
        return float(self.ms1_spectrum.intensity.sum())

    @property
    def parent_peak_intensities(self) -> dict:
        """Parent peak intensity from the averaged MS1 spectrum, per group."""
        if self._parent_peak_intensities is None:
            mz = self.ms1_spectrum.mz
            intensity = self.ms1_spectrum.intensity
            if mz.size == 0 or intensity.size == 0:
                self._parent_peak_intensities = {
                    group: float("nan") for group in self.groups
                }
                return self._parent_peak_intensities
            result = {}
            for group in self.groups:
                idx = np.argmin(np.abs(mz - group.parent_peak_mz))
                result[group] = float(intensity[idx])
            self._parent_peak_intensities = result
        return self._parent_peak_intensities

    @property
    def ms1_isolation_tic(self) -> dict:
        """Sum MS1 intensities within the isolation window, per group."""
        if self._ms1_isolation_tic is None:
            mz = self.ms1_spectrum.mz
            intensity = self.ms1_spectrum.intensity
            if mz.size == 0 or intensity.size == 0:
                self._ms1_isolation_tic = {group: float("nan") for group in self.groups}
                return self._ms1_isolation_tic
            half_iso = self.isolation_width / 2
            self._ms1_isolation_tic = {
                group: float(
                    intensity[np.abs(mz - group.parent_peak_mz) <= half_iso].sum()
                )
                for group in self.groups
            }
        return self._ms1_isolation_tic

    def _load_ms1_spectrum(self):
        """Load averaged MS1 centroided spectrum from the server."""
        ms1_data = self._ms2.get_ms1_centroids()
        if ms1_data is None or len(ms1_data.get("mz", [])) == 0:
            self._ms1_spectrum = CentroidedSpectrum(
                mz=np.array([]),
                intensity=np.array([]),
                signal_to_noise=np.array([]),
                resolution=np.array([]),
            )
            return

        self._ms1_spectrum = CentroidedSpectrum(
            mz=np.array(ms1_data["mz"]),
            intensity=np.array(ms1_data["intensity"]),
            resolution=np.array(ms1_data.get("resolution", [])),
            signal_to_noise=np.array(ms1_data.get("signal_to_noise", [])),
        )
