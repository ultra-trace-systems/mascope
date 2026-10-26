"""MS2 analysis service.

Provides MS2 data extraction and aggregation.

The MS2 reads - the summary, the averaged centroids, the fragment timeseries
and a group's scans - answer from the file's MS2 store when it has one
(:mod:`mascope_signal.ms2_store`), and from the raw file when it has none, and
each response says which in ``source`` (``"store"`` or ``"raw"``). A store's
groups are made at its own grouping tolerance, so a request at another one is
answered from them, and every response gives the tolerance its groups were
made at in ``parent_peak_tolerance``. The MS1 centroids are read from the raw
file's MS1 scans, as before.

Compute functions (in mascope_signal) use asyncio.to_thread internally, so the
event loop is not blocked; however, each request still awaits the result
synchronously—long-running operations may exceed client-side timeouts.

A per-request ``timeout`` parameter (default 120 s) guards against indefinite
waits: if the computation exceeds the limit, the server responds with 504.
"""

import asyncio
from typing import Literal

import mascope_signal.compute as m_compute
from mascope_backend.api.controllers.sample.files.process.census import (
    holds_only_fragmentation_scans,
)
from mascope_backend.api.controllers.samples.lib.samples_fetch import fetch_sample
from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.lib.exceptions.api_exceptions import (
    ApiException,
    NotFoundException,
)
from mascope_signal.ms2_store import Ms2Store, group_key, parse_group_key
from mascope_thermo.thermo import NoScansFoundError


DEFAULT_TIMEOUT = 120  # seconds

#: What ``source`` says of a response answered from the file's MS2 store.
SOURCE_STORE = "store"
#: What ``source`` says of a response read from the raw file.
SOURCE_RAW = "raw"


async def _open_store(filename: str) -> Ms2Store | None:
    """The file's MS2 store, or None when the raw file is to be read."""
    return await asyncio.to_thread(Ms2Store.open, filename)


def ms2_group_key(parent_peak_mz: float, activation: str) -> str:
    """JSON key for one (parent peak, activation) MS2 group.

    ``"137.096@hcd40.00"``, mirroring the instrument's scan-filter notation, so
    the step a spectrum belongs to is readable straight off the key. Falls back
    to the bare m/z when the group spans every activation of its precursor, or
    the filter carried none.
    """
    return f"{parent_peak_mz}@{activation}" if activation else f"{parent_peak_mz}"


@api_controller()
async def get_ms2_summary(
    sample_item_id: str,
    parent_peak_tolerance: float = 0.001,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """Retrieve MS2 summary for a sample.

    Returns parent peaks, HCD energy map, isolation widths, and scan counts.
    Each group carries its own ``isolation_width``, ``None`` when its scans do
    not share one, and ``isolation_widths``, every width its scans used with
    their scan counts (``[{"isolation_width": 1.0, "scan_count": 12}]``), so a
    group isolated at two widths answers both under its one key. The top-level
    ``isolation_width`` is ``None`` when the scans do not share one, as a
    targeted method's events may not.

    :param sample_item_id: Unique identifier for the sample.
    :param parent_peak_tolerance: Tolerance in Da for merging duplicate parent peaks.
    :param timeout: Maximum seconds to wait for the computation.
    :return: Dictionary with MS2 summary data, ``source`` and the
        ``parent_peak_tolerance`` its groups were made at.
    """
    sample = await fetch_sample(sample_item_id)

    async def read() -> tuple[dict, str, float]:
        store = await _open_store(sample.filename)
        if store is not None:
            summary = await asyncio.to_thread(
                store.summary, sample.t0, sample.t1, sample.polarity
            )
            if summary is not None:
                return summary, SOURCE_STORE, store.parent_peak_tolerance
        summary = await m_compute.get_ms2_summary(
            sample.filename,
            t_min=sample.t0,
            t_max=sample.t1,
            polarity=sample.polarity,
            parent_peak_tolerance=parent_peak_tolerance,
        )
        return summary, SOURCE_RAW, parent_peak_tolerance

    try:
        summary, source, tolerance = await asyncio.wait_for(read(), timeout=timeout)
    except asyncio.TimeoutError:
        raise ApiException(
            f"MS2 summary timed out after {timeout}s for sample",
            f"asyncio.TimeoutError in get_ms2_summary (timeout={timeout}s)",
            504,
        )

    return {
        "message": f"MS2 summary for sample '{sample.sample_item_name}'.",
        "source": source,
        "parent_peak_tolerance": tolerance,
        "data": summary,
    }


@api_controller()
async def get_ms2_averaged_centroids(
    sample_item_id: str,
    noise_threshold: float = 10.0,
    parent_peak_tolerance: float = 0.001,
    by_activation: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """Retrieve averaged MS2 centroids for each parent peak.

    Performs centroid extraction, noise filtering, grouping by parent peak, and
    centroid averaging. By default a parent peak is one spectrum over all of its
    scans, keyed by its m/z (``"137.096"``) - the response shape clients written
    against this route read. A stepped-energy acquisition measures one precursor
    at several collision energies, and that average blends the steps, so
    ``by_activation`` groups by activation as well and keys each spectrum
    ``"<parent m/z>@<activation>"`` (e.g. ``"137.096@hcd40.00"``), mirroring the
    instrument's own scan-filter notation.

    The file's MS2 store holds each group's average over all of its scans,
    so it answers a sample whose window holds every scan of each group it
    holds a scan of - a sample of the whole acquisition does. A sample whose
    window cuts a group is averaged from the raw file, over its own scans.

    :param sample_item_id: Unique identifier for the sample.
    :param noise_threshold: Minimum signal-to-noise ratio threshold.
    :param parent_peak_tolerance: Tolerance in Da for merging parent peaks.
    :param by_activation: Split each parent peak by activation.
    :param timeout: Maximum seconds to wait for the computation.
    :return: Dictionary with averaged MS2 centroids keyed by group,
        ``source`` and the ``parent_peak_tolerance`` the groups were made at.
    """
    sample = await fetch_sample(sample_item_id)

    def stored(store: Ms2Store) -> dict | None:
        spectra = store.averaged_centroids(
            sample.t0, sample.t1, sample.polarity, by_activation=by_activation
        )
        if spectra is None:
            return None
        return m_compute.ms2_centroids_on_file_axis(sample.filename, spectra)

    async def read() -> tuple[dict, str, float]:
        store = await _open_store(sample.filename)
        if store is not None:
            spectra = await asyncio.to_thread(stored, store)
            if spectra is not None:
                return spectra, SOURCE_STORE, store.parent_peak_tolerance
        spectra = await m_compute.get_orbi_ms2_centroids_by_parent(
            sample.filename,
            t_min=sample.t0,
            t_max=sample.t1,
            polarity=sample.polarity,
            parent_peak_tolerance=parent_peak_tolerance,
            by_activation=by_activation,
        )
        return spectra, SOURCE_RAW, parent_peak_tolerance

    try:
        ms2_by_parent, source, tolerance = await asyncio.wait_for(
            read(), timeout=timeout
        )
    except asyncio.TimeoutError:
        raise ApiException(
            f"MS2 centroid extraction timed out after {timeout}s for sample",
            f"asyncio.TimeoutError in get_ms2_averaged_centroids (timeout={timeout}s)",
            504,
        )

    # Convert to serializable format with noise filtering
    averaged = {}
    for (pp, activation), (
        masses,
        intensities,
        resolutions,
        signal_to_noise,
    ) in ms2_by_parent.items():
        mask = signal_to_noise >= noise_threshold
        averaged[ms2_group_key(pp, activation)] = {
            "parent_peak_mz": pp,
            "activation": activation,
            "mz": masses[mask].tolist(),
            "intensity": intensities[mask].tolist(),
            "resolution": resolutions[mask].tolist(),
            "signal_to_noise": signal_to_noise[mask].tolist(),
        }

    groups = "parent peak groups" if by_activation else "parent peaks"
    return {
        "message": (
            f"Averaged MS2 centroids for {len(ms2_by_parent)} {groups}"
            f" in sample '{sample.sample_item_name}'."
        ),
        "results": len(ms2_by_parent),
        "source": source,
        "parent_peak_tolerance": tolerance,
        "data": averaged,
    }


@api_controller()
async def get_ms1_averaged_centroids(
    sample_item_id: str,
    ppm: int = 1,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """Retrieve averaged MS1 centroids for a sample.

    Uses Thermo's native scan averaging with ppm-based binning to compute
    averaged centroided MS1 spectrum over the sample's time range.

    A sample with no MS1 scans - an acquisition of fragmentation scans alone,
    as its file's scan census says - answers with empty arrays: it has no MS1
    centroids, which is a fact about the acquisition rather than a fault in
    the request. A client that reads
    the precursor's intensity from here can then tell "no MS1 spectrum" from
    a failed read, as the MS2 notebook toolkit already does.

    :param sample_item_id: Unique identifier for the sample.
    :param ppm: Mass tolerance in ppm for centroid binning.
    :param timeout: Maximum seconds to wait for the computation.
    :return: Dictionary with mz, intensity, resolution, and signal_to_noise arrays.
    """
    sample = await fetch_sample(sample_item_id)

    try:
        (
            masses,
            intensities,
            resolutions,
            signal_to_noise,
        ) = await asyncio.wait_for(
            m_compute.get_orbi_centroids(
                sample.filename,
                t_min=sample.t0,
                t_max=sample.t1,
                polarity=sample.polarity,
                ppm=ppm,
                average=True,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        raise ApiException(
            f"MS1 centroid extraction timed out after {timeout}s for sample",
            f"asyncio.TimeoutError in get_ms1_averaged_centroids (timeout={timeout}s)",
            504,
        )
    except NoScansFoundError:
        # Only the sample of an acquisition of fragmentation scans alone has
        # no MS1 scan to centroid; a selection of an MS1 sample that holds
        # nothing is the request's, and fails as it did.
        if not await holds_only_fragmentation_scans(sample.filename):
            raise
        return {
            "message": (
                f"Sample '{sample.sample_item_name}' has no MS1 scans, so it "
                "has no MS1 centroids."
            ),
            "data": {
                "mz": [],
                "intensity": [],
                "resolution": [],
                "signal_to_noise": [],
            },
        }

    return {
        "message": f"Averaged MS1 centroids for sample '{sample.sample_item_name}'.",
        "data": {
            "mz": masses.tolist(),
            "intensity": intensities.tolist(),
            "resolution": resolutions.tolist(),
            "signal_to_noise": signal_to_noise.tolist(),
        },
    }


@api_controller()
async def get_ms2_timeseries(
    sample_item_id: str,
    parent_peak_mz: float,
    noise_threshold: float = 10.0,
    parent_peak_tolerance: float = 0.001,
    normalize_by: Literal["tic"] | None = None,
    activation: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """Retrieve fragment timeseries for a single parent peak.

    :param sample_item_id: Unique identifier for the sample.
    :param parent_peak_mz: The parent peak m/z to get timeseries for.
    :param noise_threshold: Minimum signal-to-noise ratio threshold.
    :param parent_peak_tolerance: Tolerance in Da for matching parent peaks:
        the parent peak nearest ``parent_peak_mz`` within it is read. Read
        raw, the parent peaks are merged at it too; the file's MS2 store
        answers from its own, which the response's ``parent_peak_tolerance``
        gives.
    :param normalize_by: Normalization mode. ``"tic"`` normalizes by scan TIC,
        ``None`` returns raw intensities.
    :param activation: Restrict to one activation (e.g. ``"hcd40.00"``);
        defaults to every activation of the parent peak, so a stepped-energy
        run shows its fragments changing as the energy steps.
    :param timeout: Maximum seconds to wait for the computation.
    :return: Dictionary with fragment timeseries data, ``source`` and the
        ``parent_peak_tolerance`` the parent peaks were merged at.
    """
    sample = await fetch_sample(sample_item_id)

    def stored(store: Ms2Store) -> dict | None:
        scans = store.fragment_scans(
            parent_peak_mz,
            sample.t0,
            sample.t1,
            sample.polarity,
            parent_peak_tolerance=parent_peak_tolerance,
            activation=activation,
        )
        if scans is None:
            return None
        return m_compute.ms2_fragment_timeseries(
            sample.filename, *scans, noise_threshold, normalize_by
        )

    async def read() -> tuple[dict, str, float]:
        store = await _open_store(sample.filename)
        if store is not None:
            timeseries = await asyncio.to_thread(stored, store)
            if timeseries is not None:
                return timeseries, SOURCE_STORE, store.parent_peak_tolerance
        timeseries = await m_compute.get_ms2_fragment_timeseries(
            sample.filename,
            parent_peak_mz,
            t_min=sample.t0,
            t_max=sample.t1,
            polarity=sample.polarity,
            noise_threshold=noise_threshold,
            parent_peak_tolerance=parent_peak_tolerance,
            normalize_by=normalize_by,
            activation=activation,
        )
        return timeseries, SOURCE_RAW, parent_peak_tolerance

    try:
        timeseries, source, tolerance = await asyncio.wait_for(read(), timeout=timeout)
    except asyncio.TimeoutError:
        raise ApiException(
            f"MS2 timeseries extraction timed out after {timeout}s for sample",
            f"asyncio.TimeoutError in get_ms2_timeseries (timeout={timeout}s)",
            504,
        )

    return {
        "message": (
            f"MS2 timeseries for parent peak {parent_peak_mz:.4f} m/z"
            f" in sample '{sample.sample_item_name}'."
        ),
        "source": source,
        "parent_peak_tolerance": tolerance,
        "data": timeseries,
    }


@api_controller()
async def get_ms2_scans(
    sample_item_id: str,
    group: str,
    noise_threshold: float = 10.0,
    parent_peak_tolerance: float = 0.001,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """Retrieve the scans of one MS2 group: its scan table and their centroids.

    What a client otherwise rebuilds from the file's metadata and one
    timeseries per spectrum: each scan of the (parent peak, activation) group
    in the sample, in scan order, with the reader's record of it (the fields
    of ``mascope_thermo.backend.MS2_SCAN_RECORD_SOURCES`` and ``sources``,
    which says whether the scan's filter, its trailer or the scan index gave
    each) and its centroids at or above ``noise_threshold``, on the file's
    calibrated axis.

    :param sample_item_id: Unique identifier for the sample.
    :param group: The group's key, ``"<parent m/z>@<activation>"``
        (:func:`mascope_signal.ms2_store.group_key`).
    :param noise_threshold: Minimum signal-to-noise ratio of the centroids.
    :param parent_peak_tolerance: Tolerance in Da for merging parent peaks,
        for a file read raw; a file's MS2 store answers from its own grouping.
    :param timeout: Maximum seconds to wait for the computation.
    :return: The group (``group``, its key in the one form; ``polarity``,
        ``parent_peak_mz``, ``activation``) and ``scans``, each a record with
        its ``centroids`` (``mz``, ``intensity``, ``resolution``,
        ``signal_to_noise``); ``source`` and the ``parent_peak_tolerance``
        the groups were made at.
    :raises NotFoundException: When the sample holds no scan of such a group.
    """
    sample = await fetch_sample(sample_item_id)
    try:
        parent_peak_mz, activation = parse_group_key(group)
    except ValueError as e:
        raise ApiException(str(e), f"ValueError in get_ms2_scans: {e}", 422) from e

    def stored(store: Ms2Store) -> tuple | None:
        scans = store.group_scans(group, sample.t0, sample.t1, sample.polarity)
        if scans is None:
            return None
        described, records, centroids = scans
        return (
            described,
            records,
            centroids,
            m_compute.ms2_calibration_factor(sample.filename),
        )

    async def read() -> tuple[tuple | None, str, float]:
        store = await _open_store(sample.filename)
        if store is not None and sample.polarity in ("+", "-"):
            # The store's groups are its file's: one it does not hold is not
            # the sample's, and the raw grouping is not asked for another.
            return (
                await asyncio.to_thread(stored, store),
                SOURCE_STORE,
                store.parent_peak_tolerance,
            )
        scans = await m_compute.get_ms2_group_scans(
            sample.filename,
            parent_peak_mz,
            activation,
            t_min=sample.t0,
            t_max=sample.t1,
            polarity=sample.polarity,
            parent_peak_tolerance=parent_peak_tolerance,
        )
        if scans is not None:
            records, centroids = scans
            factor = await asyncio.to_thread(
                m_compute.ms2_calibration_factor, sample.filename
            )
            described = {
                "polarity": sample.polarity,
                "parent_peak_mz": parent_peak_mz,
                "activation": activation,
                "key": group_key(parent_peak_mz, activation),
            }
            scans = described, records, centroids, factor
        return scans, SOURCE_RAW, parent_peak_tolerance

    try:
        scans, source, tolerance = await asyncio.wait_for(read(), timeout=timeout)
    except asyncio.TimeoutError:
        raise ApiException(
            f"MS2 scans read timed out after {timeout}s for sample",
            f"asyncio.TimeoutError in get_ms2_scans (timeout={timeout}s)",
            504,
        )
    if scans is None:
        raise NotFoundException(
            f"Sample '{sample.sample_item_name}' holds no scan of an MS2 group "
            f"'{group}'."
        )

    described, records, centroids, factor = scans
    served = []
    for record, scan in zip(records, centroids, strict=True):
        mask = scan["signal_to_noise"] >= noise_threshold
        masses = scan["masses"][mask]
        served.append(
            {
                **record,
                "centroids": {
                    "mz": (masses if factor is None else masses * factor).tolist(),
                    "intensity": scan["intensities"][mask].tolist(),
                    "resolution": scan["resolutions"][mask].tolist(),
                    "signal_to_noise": scan["signal_to_noise"][mask].tolist(),
                },
            }
        )
    return {
        "message": (
            f"{len(served)} scans of the MS2 group '{described['key']}'"
            f" in sample '{sample.sample_item_name}'."
        ),
        "source": source,
        "parent_peak_tolerance": tolerance,
        "data": {
            "group": described["key"],
            "polarity": described["polarity"],
            "parent_peak_mz": described["parent_peak_mz"],
            "activation": described["activation"],
            "scans": served,
        },
    }
