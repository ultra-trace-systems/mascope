"""MS2 analysis sub-resource for the Mascope SDK."""

from collections.abc import Sequence
from typing import Any

import pandas as pd

from ._base import BaseResource, _log_api_message


#: The columns of :meth:`Ms2Resource.get_readings`' frame, in order: the
#: spectrum each reading is of, then the route's fields of a reading.
READINGS_COLUMNS: tuple[str, ...] = (
    "group",
    "peak",
    "mz",
    "mz_cal",
    "intensity",
    "signal_to_noise",
    "role",
    "window_ion",
    "formula",
    "parity",
    "dbe",
    "ppm",
    "loss",
    "pieces",
    "unnamed_pieces",
    "radical_loss",
    "rank",
    "n_candidates",
    "n_parents",
    "ladder_support",
    "note",
)
#: What the frame's ``attrs`` hold of the response beside the readings.
_READINGS_ATTRS = (
    "source",
    "cached",
    "core_version",
    "parent_peak_tolerance",
    "reason",
)


class Ms2Resource(BaseResource):
    """Sub-resource for MS2 analysis on a specific sample.

    Accessed via ``mascope.samples.ms2(sample_id)``. Provides methods for
    MS2 data extraction including summaries, averaged centroids per parent peak,
    normalized fragment timeseries and each group's isolation window.

    Example::

        from mascope_sdk import MascopeClient

        mascope = MascopeClient()
        ms2 = mascope.samples.ms2("sample-456")

        summary = ms2.get_summary()
        centroids = ms2.get_averaged_centroids()
        timeseries = ms2.get_timeseries(parent_peak_mz=285.0789)
        windows = ms2.get_window()
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
        ``parent_peak_mz``, ``activation``, ``hcd_energy``, ``isolation_width``,
        ``isolation_widths``, ``scan_count``, ``t_min``, ``t_max`` -- so a
        stepped-energy acquisition is reported step by step, ordered by
        precursor and then by acquisition.

        ``isolation_width`` is the one width every MS2 scan shares, and
        ``None`` when the events of a targeted method isolate with different
        widths; each group's own is in ``groups`` (``None`` when its scans
        disagree). A group's ``isolation_widths`` lists every width its scans
        used, narrowest first, each with its scan count
        (``[{"isolation_width": 1.0, "scan_count": 12}]``), so a group whose
        scans isolate at two widths answers both under its one key. A server
        before 1.13 reports no per-group width, and an older server than this
        SDK may send no ``isolation_widths``.

        ``hcd_energy_map`` lists the energies each precursor was measured at,
        flattened across its steps. A step whose scans carry no trailer energy
        contributes nothing to it, so it does not index against ``groups`` --
        read ``groups`` when the step an energy belongs to matters.

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
        by_activation: bool = False,
    ) -> dict | None:
        """Retrieve averaged MS2 centroids for each parent peak.

        By default each parent peak is one spectrum averaged over all of its
        scans, keyed by its m/z (e.g. ``"137.096"``). A stepped-energy
        acquisition measures one precursor at several collision energies, and
        that average blends the steps: pass ``by_activation=True`` for one
        spectrum per (parent peak, activation) group instead, keyed
        ``"<parent m/z>@<activation>"`` (e.g. ``"137.096@hcd40.00"``).

        :param noise_threshold: Minimum signal-to-noise ratio threshold.
        :type noise_threshold: float
        :param parent_peak_tolerance: Tolerance in Da for merging parent peaks.
        :type parent_peak_tolerance: float
        :param by_activation: Split each parent peak by activation. A server that
                              predates the option ignores it and answers per
                              parent peak.
        :type by_activation: bool
        :return: Dictionary of spectra keyed as above, each value containing
                 'mz', 'intensity', 'resolution' and 'signal_to_noise', plus
                 'parent_peak_mz' and 'activation' (empty for a spectrum that
                 spans every activation) from a server that knows the option.
        :rtype: dict | None

        Example::

            ms2 = mascope.samples.ms2("sample-456")
            for key, data in ms2.get_averaged_centroids(by_activation=True).items():
                print(f"{key}: {len(data['mz'])} fragments")
        """
        params: dict[str, int | float | bool] = {
            "noise_threshold": noise_threshold,
            "parent_peak_tolerance": parent_peak_tolerance,
            "by_activation": by_activation,
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

    def get_window(
        self,
        *,
        width: float | None = None,
        parent_peak_tolerance: float = 0.001,
    ) -> dict:
        """Retrieve the isolation window of each MS2 group and its precursor.

        ``data`` holds one record per (parent peak, activation) group, in the
        summary's order: ``group`` (its key, ``"137.0960@hcd25.00"``),
        ``polarity``, ``parent_peak_mz``, ``activation``, ``width`` (the one
        its window was drawn at, ``None`` for none) and ``note``. Its
        ``centroids`` are the sample's MS1 centroids within +/-(width / 2 +
        ``window_pad``) of the parent key, as columns: ``mz``, ``offset``
        from the key, ``intensity``, ``share`` of the window, ``resolution``,
        ``signal_to_noise`` and ``is_parent``, true for the precursor's own.
        Beside them the precursor as the group's MS2 spectra locate it:
        ``located_precursor_mz``, ``located_precursor_intensity`` and
        ``located_precursor_activation``, found once per parent peak in its
        lowest-energy spectrum that holds it, and ``precursor_mz`` and
        ``precursor_intensity``, the group's own peak followed from there,
        ``None`` where the precursor is spent. All m/z are on the file's
        calibrated axis.

        ``reason`` says why ``data`` is empty or its windows hold nothing:
        ``"unsupported"`` for a sample of a file whose MS2 scans are not read
        (a TOF sample), ``"none"`` for a sample that holds no group, and
        ``"no_ms1_scans"`` for one with no MS1 scans, whose windows hold no
        centroid; ``None`` otherwise. ``source`` says where the groups were
        read (``"store"`` or ``"raw"``, ``None`` with ``"unsupported"``), and
        ``parent_peak_tolerance`` what they were made at.

        :param width: The isolation width in Da to draw every window at.
                      Defaults to each group's own, the widest its scans
                      used; a group with neither lists only its target, with
                      no share, and its ``note`` says so.
        :type width: float | None
        :param parent_peak_tolerance: Tolerance in Da for merging near-duplicate
                                      parent peaks, for a file read raw; a
                                      file's MS2 store answers from its own
                                      grouping.
        :type parent_peak_tolerance: float
        :return: Dictionary with ``data``, ``reason``, ``source``,
                 ``parent_peak_tolerance`` and ``window_pad``.
        :rtype: dict

        Example::

            window = mascope.samples.ms2("sample-456").get_window(width=1.0)
            for group in window["data"]:
                listed = len(group["centroids"]["mz"])
                print(group["group"], group["located_precursor_mz"], listed)
        """
        params: dict[str, Any] = {"parent_peak_tolerance": parent_peak_tolerance}
        if width is not None:
            params["width"] = width
        body = self._get_envelope(f"{self._base_path}/window", params=params)
        _log_api_message(body)
        return {
            "data": body.get("data"),
            "reason": body.get("reason"),
            "source": body.get("source"),
            "parent_peak_tolerance": body.get("parent_peak_tolerance"),
            "window_pad": body.get("window_pad"),
        }

    def get_readings(
        self,
        group: str,
        ions: str | Sequence[str],
        *,
        noise_threshold: float = 10.0,
        parent_peak_tolerance: float = 0.001,
    ) -> pd.DataFrame:
        """Read one MS2 group's fragments as formulas, over the ions named.

        The server reads the group's window whole - every activation of its
        parent peak in the sample, one averaged spectrum each - against the
        named window ions: the group's precursor and the ions co-isolated
        with it, which must lie in its isolation window, one of them at its
        key. Every peak is read against every window ion, harmonic ghosts
        and side lobes flagged, by the composition core peaky also uses.
        A named ion the window does not hold is refused with the key it
        belongs to (:class:`~mascope_sdk.exceptions.ValidationError`), and a
        group the sample does not hold is not found
        (:class:`~mascope_sdk.exceptions.NotFoundError`, as from a server
        that predates the readings).

        One row per (spectrum, peak, window ion, reading), every peak at
        least once: ``group`` names the spectrum's group; ``role`` is
        ``precursor``, ``fragment``, ``unexplained``, ``harmonic`` or
        ``satellite``; ``rank`` 1 is a peak's best reading against its
        window ion. ``df.attrs`` holds ``source``, ``cached``,
        ``core_version`` and ``parent_peak_tolerance``, ``reason`` (None
        unless the sample's file is not read), and ``window``: the group,
        its ``ions`` (the target first), the aliases ``left_out``, the
        ``recalibration`` and each spectrum's counts (``spectra``: ``tic``,
        ``n_peaks``, ``n_harmonic``, ...).

        :param group: The group's key, ``"<parent m/z>@<activation>"``
            (``"195.0877@hcd20.00"``), as the summary's groups give it.
        :type group: str
        :param ions: The window ions: ion formulas, as a list or one
            comma-separated string (``"C8H11N4O2+,C10H11O4+"``); 15N as
            ``[15N]``, peaky's ``^N`` accepted.
        :type ions: str | Sequence[str]
        :param noise_threshold: Minimum signal-to-noise ratio of the peaks
            read.
        :type noise_threshold: float
        :param parent_peak_tolerance: Tolerance in Da for merging parent
            peaks, for a file the server reads raw.
        :type parent_peak_tolerance: float
        :return: The readings, with the columns of :data:`READINGS_COLUMNS`
            whatever the content.
        :rtype: pandas.DataFrame

        Example::

            ms2 = mascope.samples.ms2("sample-456")
            readings = ms2.get_readings(
                "195.0877@hcd20.00", ["C8H11N4O2+", "C10H11O4+"]
            )
            best = readings[readings["rank"] == 1]
            print(best[["group", "mz", "formula", "loss"]])
            print(readings.attrs["window"]["recalibration"]["offset_ppm"])
        """
        if not isinstance(ions, str):
            ions = ",".join(ions)
        params: dict[str, Any] = {
            "group": group,
            "ions": ions,
            "noise_threshold": noise_threshold,
            "parent_peak_tolerance": parent_peak_tolerance,
        }
        body = self._get_envelope(f"{self._base_path}/readings", params=params)
        _log_api_message(body)
        data = body.get("data") or {}
        spectra = data.get("spectra") or []
        frame = pd.DataFrame(
            [
                {"group": spectrum["group"], **row}
                for spectrum in spectra
                for row in spectrum["readings"]
            ],
            columns=list(READINGS_COLUMNS),
        )
        for name in _READINGS_ATTRS:
            frame.attrs[name] = body.get(name)
        frame.attrs["window"] = {
            **{name: value for name, value in data.items() if name != "spectra"},
            "spectra": [
                {name: value for name, value in s.items() if name != "readings"}
                for s in spectra
            ],
        }
        return frame
