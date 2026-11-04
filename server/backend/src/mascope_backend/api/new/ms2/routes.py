from fastapi import APIRouter, Depends

from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.new.auth.dependencies import current_active_user
from mascope_backend.api.new.workspaces.dependencies import (
    require_batch_role,
    require_sample_role,
)
from mascope_backend.db import User

from .schema import (
    GetMs1CentroidsQueryParams,
    GetMs2CentroidsQueryParams,
    GetMs2ReadingsQueryParams,
    GetMs2ScansQueryParams,
    GetMs2SummaryQueryParams,
    GetMs2TimeseriesQueryParams,
    GetMs2WindowQueryParams,
)
from .service import (
    get_batch_ms2_groups,
    get_ms1_averaged_centroids,
    get_ms2_averaged_centroids,
    get_ms2_readings,
    get_ms2_scans,
    get_ms2_summary,
    get_ms2_timeseries,
    get_ms2_window,
)


ms2_router = APIRouter(tags=["MS2 Analysis"])
#: The batch-level MS2 routes, under ``/api/sample/batches``.
ms2_batch_router = APIRouter(tags=["MS2 Analysis"])


@ms2_router.get("/{sample_item_id}/ms2/summary")
@api_route(token_access=True)
async def get_ms2_summary_route(
    sample_item_id: str,
    query_params: GetMs2SummaryQueryParams = Depends(),
    user: User = Depends(current_active_user),
    membership=Depends(require_sample_role("guest")),
):
    """Retrieve MS2 summary (parent peaks, HCD map, isolation widths).

    Each group reports its ``isolation_width`` and ``isolation_widths``,
    every width its scans used with their scan counts. ``source`` says where
    the groups were read: ``"inventory"`` (the file's listed groups, for a
    sample that holds each of them whole), ``"store"`` (its MS2 store) or
    ``"raw"`` (its raw file); ``parent_peak_tolerance`` is what its groups
    were made at. ``data.reason`` says why no group is listed: ``"none"``
    for a raw Orbitrap file whose sample holds none, ``"unsupported"`` for
    any other file, such as a TOF sample, whose MS2 scans are not read; it
    is null when groups are listed.

    :param sample_item_id: The unique identifier of the sample.
    :param query_params: Query parameters for MS2 summary including parent peak tolerance.
    :param user: The current authenticated user. Requires workspace guest role.
    :type user: User
    :param membership: Workspace membership with guest role on the sample.
    :type membership: WorkspaceMember
    :return: MS2 summary data.
    """
    return await get_ms2_summary(
        sample_item_id=sample_item_id,
        **query_params.model_dump(),
    )


@ms2_router.get("/{sample_item_id}/ms2/ms1_centroids")
@api_route(token_access=True)
async def get_ms1_averaged_centroids_route(
    sample_item_id: str,
    query_params: GetMs1CentroidsQueryParams = Depends(),
    user: User = Depends(current_active_user),
    membership=Depends(require_sample_role("guest")),
):
    """Retrieve averaged MS1 centroids for a sample.

    ``reason`` is ``"unsupported"`` for a sample of a file that is not a raw
    Orbitrap file, such as a TOF sample, which answers empty arrays; it is
    null for a sample that is read.

    :param sample_item_id: The unique identifier of the sample.
    :param query_params: Query parameters for MS1 centroid retrieval.
    :param user: The current authenticated user. Requires workspace guest role.
    :type user: User
    :param membership: Workspace membership with guest role on the sample.
    :type membership: WorkspaceMember
    :return: Averaged MS1 centroid data.
    """
    return await get_ms1_averaged_centroids(
        sample_item_id=sample_item_id,
        **query_params.model_dump(),
    )


@ms2_router.get("/{sample_item_id}/ms2/centroids")
@api_route(token_access=True)
async def get_ms2_averaged_centroids_route(
    sample_item_id: str,
    query_params: GetMs2CentroidsQueryParams = Depends(),
    user: User = Depends(current_active_user),
    membership=Depends(require_sample_role("guest")),
):
    """Retrieve averaged MS2 centroids for each parent peak.

    With ``by_activation``, one spectrum per (parent peak, activation) group
    instead, so a stepped-energy acquisition keeps its steps apart.

    ``reason`` is ``"unsupported"`` for a sample of a file whose MS2 scans
    are not read, which is any but a raw Orbitrap file (a TOF sample): it
    answers no spectrum and ``source`` null. It is null for a sample that is
    read.

    :param sample_item_id: The unique identifier of the sample.
    :param query_params: Query parameters for MS2 centroid retrieval.
    :param user: The current authenticated user. Requires workspace guest role.
    :type user: User
    :param membership: Workspace membership with guest role on the sample.
    :type membership: WorkspaceMember
    :return: Averaged MS2 centroids per parent peak.
    """
    return await get_ms2_averaged_centroids(
        sample_item_id=sample_item_id,
        **query_params.model_dump(),
    )


@ms2_router.get("/{sample_item_id}/ms2/timeseries")
@api_route(token_access=True)
async def get_ms2_timeseries_route(
    sample_item_id: str,
    query_params: GetMs2TimeseriesQueryParams = Depends(),
    user: User = Depends(current_active_user),
    membership=Depends(require_sample_role("guest")),
):
    """Retrieve fragment timeseries for a single parent peak.

    ``reason`` is ``"unsupported"`` for a sample of a file whose MS2 scans
    are not read, which is any but a raw Orbitrap file (a TOF sample): it
    answers empty arrays and ``source`` null. It is null for a sample that
    is read.

    :param sample_item_id: The unique identifier of the sample.
    :param query_params: Query parameters for MS2 timeseries retrieval.
    :param user: The current authenticated user. Requires workspace guest role.
    :type user: User
    :param membership: Workspace membership with guest role on the sample.
    :type membership: WorkspaceMember
    :return: Fragment timeseries data.
    """
    return await get_ms2_timeseries(
        sample_item_id=sample_item_id,
        **query_params.model_dump(),
    )


@ms2_router.get("/{sample_item_id}/ms2/scans")
@api_route(token_access=True)
async def get_ms2_scans_route(
    sample_item_id: str,
    query_params: GetMs2ScansQueryParams = Depends(),
    user: User = Depends(current_active_user),
    membership=Depends(require_sample_role("guest")),
):
    """Retrieve the scans of one MS2 group: their records and centroids.

    The group is named by its key, ``"<parent m/z>@<activation>"``
    (``137.0960@hcd25.00``). Each scan carries the reader's record of it - its
    time, precursor, activation, energies, isolation width, scan range,
    injection time and TIC, and where each was read - and its centroids.

    ``reason`` is ``"unsupported"`` for a sample of a file whose MS2 scans
    are not read, which is any but a raw Orbitrap file (a TOF sample): it
    answers the group with no scan and ``source`` null, where a sample that
    is read and holds no scan of the group answers 404. It is null for a
    sample that is read.

    :param sample_item_id: The unique identifier of the sample.
    :param query_params: Query parameters naming the group.
    :param user: The current authenticated user. Requires workspace guest role.
    :type user: User
    :param membership: Workspace membership with guest role on the sample.
    :type membership: WorkspaceMember
    :return: The group's scans.
    """
    return await get_ms2_scans(
        sample_item_id=sample_item_id,
        **query_params.model_dump(),
    )


@ms2_router.get("/{sample_item_id}/ms2/window")
@api_route(token_access=True)
async def get_ms2_window_route(
    sample_item_id: str,
    query_params: GetMs2WindowQueryParams = Depends(),
    user: User = Depends(current_active_user),
    membership=Depends(require_sample_role("guest")),
):
    """Retrieve the isolation-window inventory of each MS2 group.

    Per group, the sample's MS1 centroids within +/-(width / 2 + 5 mDa) of
    its parent key, each with its ``offset`` from the key, ``intensity``,
    ``share`` of the window, ``resolution``, ``signal_to_noise`` and
    ``is_parent``, the one that is the precursor; and the group's precursor
    as its MS2 spectra locate it: ``located_precursor_mz``,
    ``located_precursor_intensity`` and ``located_precursor_activation``,
    found once per parent peak in its lowest-energy spectrum that holds it,
    and ``precursor_mz`` and ``precursor_intensity``, the group's own peak
    followed from there, null where the precursor is spent. All m/z are on
    the file's calibrated axis.

    ``width`` draws every window at that width instead of each group's own,
    the widest its scans used. A group with no width and none asked for
    lists only its target, with no share, and its ``note`` says so.
    ``source`` says where the groups and their precursors were read
    (``"store"`` or ``"raw"``). ``reason`` is ``"unsupported"`` for a sample
    of a file whose MS2 scans are not read (a TOF sample), ``"none"`` for a
    sample that holds no group, and ``"no_ms1_scans"`` for a sample with no
    MS1 scans, an acquisition of fragmentation scans alone, whose windows
    hold no centroid; null otherwise.

    :param sample_item_id: The unique identifier of the sample.
    :param query_params: Query parameters including the width override.
    :param user: The current authenticated user. Requires workspace guest role.
    :type user: User
    :param membership: Workspace membership with guest role on the sample.
    :type membership: WorkspaceMember
    :return: The isolation windows of the sample's MS2 groups.
    """
    return await get_ms2_window(
        sample_item_id=sample_item_id,
        **query_params.model_dump(),
    )


@ms2_router.get("/{sample_item_id}/ms2/readings")
@api_route(token_access=True)
async def get_ms2_readings_route(
    sample_item_id: str,
    query_params: GetMs2ReadingsQueryParams = Depends(),
    user: User = Depends(current_active_user),
    membership=Depends(require_sample_role("guest")),
):
    """Read one MS2 group's fragments as formulas, over the ions named.

    The group's window is read whole - every activation of its parent peak
    in the sample, one averaged spectrum each at or above ``noise_threshold``
    - against the named ``ions``: the group's precursor and the ions
    co-isolated with it. Each must lie in the isolation window, within
    +/-(width / 2 + 5 mDa) of the key, and one of them, the target, within
    max(10 ppm, 5 mDa) of it; one that does not is refused (422) with the
    nearest key and its offset from it. Of two named ions within the reading
    tolerance of each other only the first is read, and ``left_out`` names
    the other. The window is recalibrated once, by one constant-ppm offset on
    the target's located precursor (``recalibration``).

    Per spectrum (``spectra``, each keyed by its ``group``), its TIC and peak
    counts with the harmonic ghosts and side lobes out of them, and
    ``readings``: one row per (peak, window ion, reading), every peak at
    least once - ``peak`` (its place among the spectrum's peaks at the S/N
    floor, as ``ms2/centroids`` serves them), ``mz``, ``mz_cal`` (on the
    recalibrated axis), ``intensity``, ``signal_to_noise``, ``role``
    (``precursor``, ``fragment``, ``unexplained``, ``harmonic``,
    ``satellite``), ``window_ion``, ``formula``, ``parity``, ``dbe``,
    ``ppm``, ``loss`` and its ``pieces``, ``unnamed_pieces``,
    ``radical_loss``, ``rank``, ``n_candidates``, ``n_parents``,
    ``ladder_support`` and ``note``, null where the row has none.
    ``ions_source`` says whose ions were read: ``named``, the caller's.

    The readings are cached in the file's MS2 store, keyed by the ions, the
    spectra and the composition core's version (``core_version``), and
    ``cached`` says whether they were read from it. ``source`` says where
    the spectra were read (``"store"`` or ``"raw"``). ``reason`` is
    ``"unsupported"`` for a sample of a file whose MS2 scans are not read (a
    TOF sample), which answers the group with no spectrum and ``source``
    null; null otherwise. A sample that holds no such group answers 404.

    :param sample_item_id: The unique identifier of the sample.
    :param query_params: Query parameters naming the group and its ions.
    :param user: The current authenticated user. Requires workspace guest role.
    :type user: User
    :param membership: Workspace membership with guest role on the sample.
    :type membership: WorkspaceMember
    :return: The group's readings.
    """
    return await get_ms2_readings(
        sample_item_id=sample_item_id,
        **query_params.model_dump(),
    )


@ms2_batch_router.get("/{sample_batch_id}/ms2/groups")
@api_route(token_access=True)
async def get_batch_ms2_groups_route(
    sample_batch_id: str,
    user: User = Depends(current_active_user),
    membership=Depends(require_batch_role("guest")),
):
    """Retrieve the MS2 groups of every sample of a batch, in one query.

    One record per (sample, group), read from the group inventory: the
    sample's id, the group's file, polarity and ``precursor_key``
    (``137.0960@hcd25.00``, the key ``ms2/scans?group=`` takes), parent peak
    m/z, activation with its kind and NCE, HCD energies, isolation widths,
    scan count, time span, scan range, and ``whole``, false where the
    sample's window cuts the group. And its precursor, located when the
    file's MS2 store was written: ``located_precursor_mz``,
    ``located_precursor_intensity`` and ``located_precursor_activation``,
    where its parent peak's precursor was found, and ``precursor_mz`` and
    ``precursor_intensity``, the group's own peak followed from there, null
    where the precursor is spent. These m/z are on the instrument's axis, as
    the key is; ``ms2/window`` gives them on the sample's calibrated axis.
    An MS1-only batch answers an empty list.

    :param sample_batch_id: The unique identifier of the sample batch.
    :param user: The current authenticated user. Requires workspace guest role.
    :type user: User
    :param membership: Workspace membership with guest role on the batch.
    :type membership: WorkspaceMember
    :return: The batch's MS2 groups.
    """
    return await get_batch_ms2_groups(sample_batch_id=sample_batch_id)
