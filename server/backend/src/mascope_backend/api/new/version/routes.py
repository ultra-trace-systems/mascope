from fastapi import APIRouter, Depends

from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.new.auth.dependencies import admin_user
from mascope_backend.runtime import runtime


version_router = APIRouter(prefix="/api/version", tags=["Version"])


@version_router.get("")
@api_route()
async def get_version_route(user=Depends(admin_user)):
    """
    Report the version of the running deployment.

    Lets an operator, an audit, or an automated security assessment attribute
    what it is looking at to a specific artifact without shell access to the
    host. That attribution is the point: a finding against an unknown build is
    not actionable, because a control that appears missing may simply predate
    the image.

    The reported string is ``MASCOPE_VERSION``, which compose also interpolates
    into the ``image:`` tag it pulls, so this always names the tag that was
    actually deployed rather than a separately maintained constant. For a
    pinned release that is the release version; a deployment tracking ``latest``
    reports ``latest``, which names its tag but not the build behind it. It is
    ``unknown`` only where the variable is unset, i.e. a source checkout run
    outside compose.

    Admin-gated as least privilege for an operational endpoint, not as a
    confidentiality boundary: the frontend bundle already renders this same
    version on the login screen, so the value is not secret. The gate exists so
    the API adds no new anonymous, machine-readable surface, matching the
    deployment's ``server_tokens off``.

    :param user: The currently authenticated admin user.
    :type user: User
    :return: A message and the running version.
    :rtype: dict
    """
    version = runtime.version or "unknown"
    return {
        "message": f"Mascope version '{version}'.",
        "data": {"version": version},
    }
