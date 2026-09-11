import os
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.lib.exceptions.api_exceptions import ApiException
from mascope_backend.api.new.auth.dependencies import guest_user
from mascope_backend.runtime import runtime


version_router = APIRouter(prefix="/api/version", tags=["Version"])

# Written by the backend image build (server/backend/Dockerfile), from the
# distributions installed into the image, by tooling/third-party-notices.py.
THIRD_PARTY_NOTICES = "THIRD_PARTY_NOTICES.txt"


def third_party_notices_path() -> Path:
    """Where the image build leaves this backend's third-party notices."""
    return Path(os.environ.get("MASCOPE_PATH", ".")) / THIRD_PARTY_NOTICES


@version_router.get("")
@api_route()
async def get_version_route(user=Depends(guest_user)):
    """
    Report the version of the running deployment.

    Lets an operator, an audit, or an automated security assessment attribute
    what it is looking at to a specific artifact without shell access to the
    host. That attribution is the point: a finding against an unknown build is
    not actionable, because a control that appears missing may simply predate
    the image. The web app's About dialog reads it for the same reason: it
    shows the server's build next to its own, for a support request, and flags
    a mismatch between the two.

    The reported string is ``MASCOPE_VERSION``, which compose also interpolates
    into the ``image:`` tag it pulls, so this always names the tag that was
    actually deployed rather than a separately maintained constant. For a
    pinned release that is the release version; a deployment tracking ``latest``
    reports ``latest``, which names its tag but not the build behind it. It is
    ``unknown`` only where the variable is unset, i.e. a source checkout run
    outside compose.

    Open to every signed-in user, because the About dialog is. Not a
    confidentiality boundary: the frontend bundle already renders this same
    version on the login screen, so the value is not secret. It stays behind
    authentication so the API adds no new anonymous, machine-readable surface,
    matching the deployment's ``server_tokens off``.

    :param user: The currently authenticated user.
    :type user: User
    :return: A message and the running version.
    :rtype: dict
    """
    version = runtime.version or "unknown"
    return {
        "message": f"Mascope version '{version}'.",
        "data": {"version": version},
    }


@version_router.get("/third-party-notices")
@api_route()
async def get_third_party_notices_route(user=Depends(guest_user)):
    """
    Serve the attributions for the third-party Python packages in this backend.

    The backend image redistributes a few hundred open-source distributions,
    and their licences - the notice clauses of MIT and BSD, Apache-2.0 section
    4(d) - ask for their copyright and licence text to travel with them. The
    image build writes that text out of the installed distributions themselves
    (``tooling/third-party-notices.py``), and the About dialog renders it from
    here. The web app's npm attributions are baked into the frontend image and
    served as a static file instead: each image carries the notices for what it
    ships.

    A source checkout has no such file unless someone generated one, which is
    answered with 404 rather than an empty document that would read as "no
    third-party code".

    :param user: The currently authenticated user.
    :type user: User
    :return: The notices, as UTF-8 plain text.
    :rtype: FileResponse
    """
    path = third_party_notices_path()
    if not path.is_file():
        raise ApiException(
            "Third-party notices are generated when the server image is built, "
            "and this server has none.",
            f"{path} does not exist; generate it with tooling/third-party-notices.py",
            404,
        )
    return FileResponse(path, media_type="text/plain; charset=utf-8")
