"""MS2 analysis sub-resource for the Mascope SDK."""

from typing import Any

from ._base import BaseResource


class Ms2Resource(BaseResource):
    """Sub-resource for MS2 analysis on a specific sample.

    Accessed via ``mascope.samples.ms2(sample_id)``. Provides methods for
    MS2 data extraction including summaries, averaged centroids per parent peak,
    and normalized fragment timeseries.

    Example::

        from mascope_sdk import MascopeClient

        mascope = MascopeClient()
        ms2 = mascope.samples.ms2("sample-456")

        summary = ms2.get_summary()
        centroids = ms2.get_averaged_centroids()
        timeseries = ms2.get_timeseries(parent_peak_mz=285.0789)
    """

    def __init__(self, client: "Any", sample_item_id: str):
        """Initialize the MS2 sub-resource for a specific sample.

        :param client: The MascopeClient instance.
        :param sample_item_id: The sample item ID to query.
        """
        super().__init__(client)
        self._sample_item_id = sample_item_id

    @property
    def _base_path(self) -> str:
        return f"samples/{self._sample_item_id}/ms2"

    def get_summary(
        self,
        *,
        parent_peak_tolerance: float = 0.001,
    ) -> dict | None:
        """Retrieve MS2 summary: parent peaks, per-group detail, HCD energy map,
        isolation width, and scan counts.

        ``groups`` holds one record per (parent peak, activation) pair --
        ``parent_peak_mz``, ``activation``, ``hcd_energy``, ``scan_count``,
        ``t_min``, ``t_max`` -- so a stepped-energy acquisition is reported step
        by step. ``hcd_energy_map`` lists the energies each precursor was
        measured at, one per step.

        :param parent_peak_tolerance: Tolerance in Da for merging near-duplicate
                                      parent peaks.
        :type parent_peak_tolerance: float
        :return: Dictionary with MS2 summary data, or None if unavailable.
        :rtype: dict | None

        Example::

            summary = mascope.samples.ms2("sample-456").get_summary()
            print(summary["parent_peaks"])
            for group in summary["groups"]:
                print(group["activation"], group["scan_count"], group["hcd_energy"])
        """
        params: dict[str, Any] = {
            "parent_peak_tolerance": parent_peak_tolerance,
        }
        return self._get(f"{self._base_path}/summary", params=params)

    def get_ms1_centroids(
        self,
        *,
        ppm: int = 1,
    ) -> dict | None:
        """Retrieve averaged MS1 centroids for the sample.

        :param ppm: Mass tolerance in ppm for centroid binning.
        :type ppm: int
        :return: Dictionary with 'mz', 'intensity', 'resolution', and
                 'signal_to_noise' lists, or None if unavailable.
        :rtype: dict | None

        Example::

            ms1 = mascope.samples.ms2("sample-456").get_ms1_centroids()
            print(f"MS1 peaks: {len(ms1['mz'])}")
        """
        params: dict[str, Any] = {"ppm": ppm}
        return self._get(f"{self._base_path}/ms1_centroids", params=params)

    def get_averaged_centroids(
        self,
        *,
        noise_threshold: float = 10.0,
        parent_peak_tolerance: float = 0.001,
    ) -> dict | None:
        """Retrieve averaged MS2 centroids for each (parent peak, activation) group.

        A stepped-energy acquisition returns one spectrum per collision energy.

        :param noise_threshold: Minimum signal-to-noise ratio threshold.
        :type noise_threshold: float
        :param parent_peak_tolerance: Tolerance in Da for merging parent peaks.
        :type parent_peak_tolerance: float
        :return: Dictionary keyed by ``"<parent m/z>@<activation>"`` (e.g.
                 ``"137.096@hcd40.00"``), each value containing
                 'parent_peak_mz', 'activation', 'mz', 'intensity',
                 'resolution', and 'signal_to_noise'.
        :rtype: dict | None

        Example::

            centroids = mascope.samples.ms2("sample-456").get_averaged_centroids()
            for key, data in centroids.items():
                print(f"{key}: {len(data['mz'])} fragments")
        """
        params: dict[str, int | float] = {
            "noise_threshold": noise_threshold,
            "parent_peak_tolerance": parent_peak_tolerance,
        }
        return self._get(f"{self._base_path}/centroids", params=params)

    def get_timeseries(
        self,
        parent_peak_mz: float,
        *,
        noise_threshold: float = 10.0,
        parent_peak_tolerance: float = 0.001,
        normalize_by: str | None = None,
        activation: str | None = None,
    ) -> dict | None:
        """Retrieve fragment timeseries for a single parent peak.

        :param parent_peak_mz: The parent peak m/z to get fragment timeseries for.
        :type parent_peak_mz: float
        :param noise_threshold: Minimum signal-to-noise ratio threshold.
        :type noise_threshold: float
        :param parent_peak_tolerance: Tolerance in Da for matching parent peaks.
        :type parent_peak_tolerance: float
        :param normalize_by: Normalization mode: ``"tic"`` normalizes by scan TIC,
            ``None`` returns raw intensities.
        :type normalize_by: str
        :param activation: Restrict to one activation, e.g. ``"hcd40.00"``.
            Defaults to every activation of the parent peak, which is what makes
            a stepped-energy run's fragments visible changing across the steps.
        :type activation: str | None
        :return: Dictionary with 'mz_values' (list of fragment m/z), 'time'
                 (list of timestamps), and 'values' (2D list of intensities).
        :rtype: dict | None

        Example::

            ms2 = mascope.samples.ms2(\"sample-456\")
            ts = ms2.get_timeseries(parent_peak_mz=285.0789)
            print(f"Fragments: {ts['mz_values']}")
            print(f"Timepoints: {len(ts['time'])}")
        """
        params: dict[str, Any] = {
            "parent_peak_mz": parent_peak_mz,
            "noise_threshold": noise_threshold,
            "parent_peak_tolerance": parent_peak_tolerance,
            "normalize_by": normalize_by,
            "activation": activation,
        }
        return self._get(f"{self._base_path}/timeseries", params=params)
