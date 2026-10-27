from fastapi import APIRouter, Depends, Query

from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.lib.exceptions.api_exceptions import NotFoundException
from mascope_backend.api.new.auth.dependencies import guest_user
from mascope_backend.api.new.reference.service import list_sources, source_compounds
from mascope_reference.query import SOURCE_PAGE_CAP, SOURCE_PAGE_SIZE


reference_router = APIRouter(prefix="/api/reference", tags=["Reference"])


@reference_router.get("/sources")
@api_route(token_access=True)
async def get_reference_sources_route(user=Depends(guest_user)):
    """
    List the reference sources this deployment has loaded.

    One row per active source: its name, version, label, licence, polarity,
    whether its radicals may be matched, the window its compounds are matched
    in, how many compounds it holds, when it was loaded, and ``admitted``,
    whether the deployment's ``[backend] reference_licenses`` lets Stage A of
    peak assignment match it. A shipped list carries the label its file gives
    it; any other source is labelled by its name. ``references`` are the
    publications the source comes from, as recorded when it was loaded: each a
    ``citation`` with its ``doi`` or ``isbn``, and a ``note``. Sorted by label.

    Reference data belongs to the deployment, not to a workspace, so any
    signed-in user reads it. Open to an API token with its service name, because
    the SDK reads it.

    :param user: The currently authenticated user.
    :return: ``{"data": [...], "total": N}``.
    :rtype: dict
    """
    return await list_sources()


@reference_router.get("/sources/{name}/compounds")
@api_route(token_access=True)
async def get_reference_source_compounds_route(
    name: str,
    version: str | None = Query(
        None, description="The version to read; the active one when omitted."
    ),
    page: int = Query(0, ge=0, description="Zero-based page."),
    limit: int = Query(
        SOURCE_PAGE_SIZE, ge=1, le=SOURCE_PAGE_CAP, description="Rows per page."
    ),
    user=Depends(guest_user),
):
    """
    Page through the compounds one version of a reference source holds.

    Each row has the compound's name, formula, monoisotopic mass, InChIKey and
    the source's own id for it, the DOI the list cites for it (``reference``)
    and its tags. Rows come by mass. A named version is read whether it is
    active or not; without one, the active version is.

    Any signed-in user, as for the source list.

    :param name: The source's name.
    :param version: The version to read; the active one when omitted.
    :param page: Zero-based page number.
    :param limit: Rows per page.
    :param user: The currently authenticated user.
    :return: ``{"name", "version", "data": [...], "total": N}``.
    :raises NotFoundException: When the source has no such version, or no
        active one when none is named.
    :rtype: dict
    """
    found = await source_compounds(name, version=version, page=page, limit=limit)
    if found is None:
        what = f"version {version}" if version is not None else "active version"
        raise NotFoundException(f"Reference source {name} has no {what}")
    return found
