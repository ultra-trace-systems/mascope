from fastapi import APIRouter, Depends, Query

from mascope_backend.api.controllers.samples.samples_controller import (
    get_sample,
    get_sample_peak_timeseries,
    get_sample_peaks,
    get_samples,
    get_samples_centroids,
)
from mascope_backend.api.controllers.samples.spectrum_service import (
    get_sample_spectrum_for,
    get_samples_spectra_for,
)
from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.models.samples.sample_pydantic_model import (
    GetSamplePeaksQueryParams,
    GetSamplePeakTimeseriesBody,
    GetSampleSpectrumQueryParams,
    GetSamplesQueryParams,
)
from mascope_backend.api.new.auth.dependencies import current_active_user
from mascope_backend.api.new.ms2.routes import ms2_router
from mascope_backend.api.new.workspaces.dependencies import (
    check_batch_access,
    check_sample_access,
    check_sample_access_bulk,
    require_sample_role,
)
from mascope_backend.db import User


samples_router = APIRouter(prefix="/api/samples", tags=["Samples Loading"])
samples_router.include_router(ms2_router)


@samples_router.get("")
@api_route(token_access=True)
async def get_samples_route(
    query_params: GetSamplesQueryParams = Query(),
    user: User = Depends(current_active_user),
):
    """Retrieve a list of samples based on query filters.

    :param query_params: Query parameters for filtering, sorting, and pagination.
    :param user: The current authenticated user. Requires workspace guest role.
    :type user: User
    :return: A dictionary containing the total count and list of samples.
    """
    if query_params.sample_batch_id:
        await check_batch_access(query_params.sample_batch_id, user, "guest")
    elif query_params.sample_item_id:
        await check_sample_access(query_params.sample_item_id, user, "guest")
    else:
        raise ValueError("Either sample_batch_id or sample_item_id must be provided.")
    return await get_samples(**query_params.model_dump())


@samples_router.get("/centroids")
@api_route(token_access=True)
async def get_samples_centroids_route(
    sample_item_ids: list[str] = Query(..., description="List of sample item IDs"),
    user: User = Depends(current_active_user),
) -> dict:
    """Retrieve centroids for multiple sample items.

    :param sample_item_ids: List of sample item IDs to retrieve centroids for.
    :param user: The current authenticated user. Requires workspace guest role.
    :type user: User
    :return: A dictionary containing the process ID for retrieving centroids.
    """
    await check_sample_access_bulk(sample_item_ids, user, "guest")
    return await get_samples_centroids(sample_item_ids=sample_item_ids)


@samples_router.get("/spectra")
@api_route(token_access=True)
async def get_samples_spectra_route(
    sample_item_ids: list[str] = Query(..., description="List of sample item IDs"),
    t_min: float | None = Query(None),
    t_max: float | None = Query(None),
    mz_min: float | None = Query(None),
    mz_max: float | None = Query(None),
    user: User = Depends(current_active_user),
):
    """Retrieve spectra for multiple samples with optional filtering.

    :param sample_item_ids: List of sample item IDs to retrieve spectra for.
    :type sample_item_ids: list[str]
    :param t_min: Minimum time value for filtering spectra, defaults to Query(None)
    :type t_min: float | None, optional
    :param t_max: Maximum time value for filtering spectra, defaults to Query(None)
    :type t_max: float | None, optional
    :param mz_min: Minimum m/z value for filtering spectra, defaults to Query(None)
    :type mz_min: float | None, optional
    :param mz_max: Maximum m/z value for filtering spectra, defaults to Query(None)
    :type mz_max: float | None, optional
    :param user: The current authenticated user. Requires workspace guest role.
    :type user: User
    :return: A dictionary containing the spectra for the specified samples,
        one entry per sample in the order asked. A sample whose file's peak
        data has to be rebuilt first has an empty spectrum and is listed
        under ``stale``; the rebuild is asked for where the user is an
        editor of the sample's workspace.
    :rtype: dict
    """
    await check_sample_access_bulk(sample_item_ids, user, "guest")
    return await get_samples_spectra_for(
        user,
        sample_item_ids=sample_item_ids,
        t_min=t_min,
        t_max=t_max,
        mz_min=mz_min,
        mz_max=mz_max,
    )


@samples_router.get("/{sample_item_id}")
@api_route(token_access=True)
async def get_sample_route(
    sample_item_id: str,
    user: User = Depends(current_active_user),
    membership=Depends(require_sample_role("guest")),
):
    """Retrieve details of a specific sample by ID.

    :param sample_item_id: The unique identifier of the sample.
    :param user: The current authenticated user. Requires workspace guest role.
    :type user: User
    :param membership: Workspace membership with guest role on the sample.
    :type membership: WorkspaceMember
    :return: A dictionary containing the sample details.
    :rtype: dict
    """
    return await get_sample(sample_item_id=sample_item_id)


@samples_router.get("/{sample_item_id}/peaks")
@api_route(token_access=True)
async def get_sample_peaks_route(
    sample_item_id: str,
    query_params: GetSamplePeaksQueryParams = Depends(),
    user: User = Depends(current_active_user),
    membership=Depends(require_sample_role("guest")),
):
    """
    Retrieve peak data from a sample with automatic polarity filtering and optional
    range filtering.

    This endpoint extracts peak areas and/or heights for a sample, automatically
    filtered by the sample's polarity so that only scans matching the sample's polarity
    are included. Supports optional time range filtering within the sample's acquisition
    window (t0/t1) and m/z range filtering. The peak data is aggregated across the time
    dimension (averaged or summed) after applying all filters.

    :param sample_item_id: The unique identifier of the sample
    :param query_params: Query parameters for peak filtering including time range and
        data selection
    :param user: The current authenticated user. Requires workspace guest role.
    :type user: User
    :param membership: Workspace membership with guest role on the sample.
    :type membership: WorkspaceMember
    :return: Peak data filtered by sample's polarity and time range
    :rtype: dict
    """
    return await get_sample_peaks(
        sample_item_id=sample_item_id, **query_params.model_dump()
    )


@samples_router.post("/{sample_item_id}/peaks/timeseries")
@api_route(token_access=True)
async def get_sample_peak_timeseries_route(
    sample_item_id: str,
    body: GetSamplePeakTimeseriesBody,
    user: User = Depends(current_active_user),
    membership=Depends(require_sample_role("guest")),
):
    """
    Retrieve timeseries data for a specific peak in a sample.

    This endpoint extracts timeseries data for the closest peak to a given m/z value
    within the specified tolerance, filtered by the sample's polarity and time range.

    :param sample_item_id: The unique identifier of the sample
    :param body: Request body containing peak m/z, tolerance, and optional time filters
    :param user: The current authenticated user. Requires workspace guest role.
    :type user: User
    :param membership: Workspace membership with guest role on the sample.
    :type membership: WorkspaceMember
    :return: Timeseries data for the specified peak including m/z, height, and time
        coordinates
    :rtype: dict
    """
    return await get_sample_peak_timeseries(
        sample_item_id=sample_item_id, **body.model_dump()
    )


@samples_router.get("/{sample_item_id}/spectrum")
@api_route(token_access=True)
async def get_sample_spectrum_route(
    sample_item_id: str,
    query_params: GetSampleSpectrumQueryParams = Depends(),
    user: User = Depends(current_active_user),
    membership=Depends(require_sample_role("guest")),
):
    """
    Retrieve spectrum data from a sample with automatic polarity filtering and optional
    range filtering.

    This endpoint extracts time-averaged spectrum data for a sample, automatically
    filtered by the sample's polarity. Supports optional time range filtering
    (t_min/t_max) within the sample's acquisition window (t0/t1) and m/z range filtering

    :param sample_item_id: The unique identifier of the sample
    :param query_params: Query parameters for spectrum filtering including optional time
        and m/z ranges
    :param user: The current authenticated user. Requires workspace guest role.
    :type user: User
    :param membership: Workspace membership with guest role on the sample.
    :type membership: WorkspaceMember
    :return: Spectrum data with m/z values and intensities, filtered by sample polarity
    :rtype: dict
    """
    return await get_sample_spectrum_for(
        user, sample_item_id=sample_item_id, **query_params.model_dump()
    )
