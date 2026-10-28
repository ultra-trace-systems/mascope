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

The summary's groups are read from the group inventory where it can answer
for the sample (``source: "inventory"``, :mod:`.inventory`), which is also
what lists the groups of every sample of a batch in one query.

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
from mascope_backend.api.new.ms2.inventory import (
    batch_ms2_groups,
    sample_ms2_inventory,
)
from mascope_file.name import get_sample_file_type
from mascope_signal.ms2_store import Ms2Store, group_key, parse_group_key
from mascope_thermo.thermo import NoScansFoundError, ms2_summary


DEFAULT_TIMEOUT = 120  # seconds

#: What ``source`` says of a response answered from the file's MS2 store.
SOURCE_STORE = "store"
#: What ``source`` says of a response read from the raw file.
SOURCE_RAW = "raw"
#: What ``source`` says of a summary whose groups are the group inventory's.
SOURCE_INVENTORY = "inventory"

#: Why a summary lists no group: a raw Orbitrap file whose sample holds none,
#: and a file whose MS2 scans are not read, which is any other (a TOF one).
REASON_NONE = "none"
REASON_UNSUPPORTED = "unsupported"

#: A group's fields in the summary, as ``mascope_thermo.thermo.ms2_group_summary``
#: makes them.
_SUMMARY_GROUP_FIELDS = (
    "parent_peak_mz",
    "activation",
    "hcd_energy",
    "isolation_width",
    "isolation_widths",
    "scan_count",
    "t_min",
    "t_max",
)


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


def _inventory_record(group: dict) -> dict:
    """A group of the inventory as the summary gives a group."""
    return {name: group[name] for name in _SUMMARY_GROUP_FIELDS}


async def _summary_from_inventory(sample, store: Ms2Store) -> dict | None:
    """The sample's summary with its groups read from the group inventory.

    Its groups are the inventory's, which are what the samples listing
    counts and the batch's groups list, and the rest of the summary - the
    scan counts and the one width every MS2 scan shares - is read from the
    store's scan index. None when the inventory cannot answer, and the store
    is read instead: a sample with no polarity, one whose window cuts a
    group, one that holds no group, and a file whose inventory does not list
    every group its store holds, or lists one it does not.
    """
    if sample.polarity not in ("+", "-"):
        return None
    groups, listed = await sample_ms2_inventory(sample)
    stored = {(group["polarity"], group["key"]) for group in store.attrs["groups"]}
    if not groups or listed != stored or not all(g["whole"] for g in groups):
        return None
    counts = await asyncio.to_thread(
        store.scan_counts, sample.t0, sample.t1, sample.polarity
    )
    return ms2_summary(
        [_inventory_record(group) for group in groups],
        counts[2],
        counts[0],
        counts[1],
        store.parent_peak_tolerance,
    )


def _reads_raw_ms2(filename: str) -> bool:
    """Whether a file's MS2 scans can be read from its raw file: a raw
    Orbitrap file, kept beside its sample."""
    try:
        return get_sample_file_type(filename) == "orbi_raw"
    except ValueError:
        # Of no instrument class this can tell: not a raw Orbitrap file
        return False


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

    The groups are the group inventory's (``source: "inventory"``) for a
    sample whose window holds each of its groups whole, which is a sample of
    the whole acquisition; else the store's, else the raw file's.
    ``reason`` says why a summary lists no group: ``"none"`` for a raw
    Orbitrap file whose sample holds none, ``"unsupported"`` for a file whose
    MS2 scans Mascope does not read, which is any but a raw Orbitrap file (a
    TOF sample), and which answers with no scan counted; None when it lists
    some.

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
            summary = await _summary_from_inventory(sample, store)
            if summary is not None:
                return summary, SOURCE_INVENTORY, store.parent_peak_tolerance
            summary = await asyncio.to_thread(
                store.summary, sample.t0, sample.t1, sample.polarity
            )
            if summary is not None:
                return summary, SOURCE_STORE, store.parent_peak_tolerance
        if not await asyncio.to_thread(_reads_raw_ms2, sample.filename):
            # Nothing to read: the inventory holds no group of such a file,
            # and its scans are not counted
            unsupported = ms2_summary([], [], 0, 0, parent_peak_tolerance)
            unsupported["ms1_scan_count"] = None
            unsupported["reason"] = REASON_UNSUPPORTED
            return unsupported, SOURCE_INVENTORY, parent_peak_tolerance
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

    if "reason" not in summary:
        summary = {**summary, "reason": None if summary["groups"] else REASON_NONE}
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
    and each parent peak's over all of its activations, so it answers a
    sample whose window holds every scan of each group it holds a scan of -
    a sample of the whole acquisition does - and, by parent peak, every
    activation of each one or one alone. A sample whose window cuts a group,
    or holds several activations of a parent peak and not all when they are
    merged, is averaged from the raw file, over its own scans.

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


@api_controller()
async def get_batch_ms2_groups(sample_batch_id: str) -> dict:
    """Retrieve the MS2 groups of every sample of a batch.

    Read from the group inventory in one query, whatever the batch's size:
    one record per (sample, group), with the sample's id
    (:func:`.inventory.group_record`), ordered by the samples' acquisition
    time and within a sample as its summary orders its groups. A group the
    sample's window cuts has ``whole`` false: its counts and span are its
    file's. Empty for a batch none of whose samples holds a group - an MS1
    batch, a TOF one, or one whose files' stores the backfill has not
    listed yet.

    :param sample_batch_id: The batch.
    :return: The groups, and how many.
    """
    groups = await batch_ms2_groups(sample_batch_id)
    samples = len({group["sample_item_id"] for group in groups})
    return {
        "message": f"{len(groups)} MS2 groups of {samples} samples of the batch.",
        "results": len(groups),
        "data": groups,
    }
