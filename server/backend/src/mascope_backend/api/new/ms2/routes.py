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
    GetMs2ScansQueryParams,
    GetMs2SummaryQueryParams,
    GetMs2TimeseriesQueryParams,
)
from .service import (
    get_batch_ms2_groups,
    get_ms1_averaged_centroids,
    get_ms2_averaged_centroids,
    get_ms2_scans,
    get_ms2_summary,
    get_ms2_timeseries,
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
    scan count, time span, scan range and located precursor, and ``whole``,
    false where the sample's window cuts the group. An MS1-only batch
    answers an empty list.

    :param sample_batch_id: The unique identifier of the sample batch.
    :param user: The current authenticated user. Requires workspace guest role.
    :type user: User
    :param membership: Workspace membership with guest role on the batch.
    :type membership: WorkspaceMember
    :return: The batch's MS2 groups.
    """
    return await get_batch_ms2_groups(sample_batch_id=sample_batch_id)
