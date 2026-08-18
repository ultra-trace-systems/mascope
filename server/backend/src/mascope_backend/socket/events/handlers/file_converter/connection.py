"""
File converter service connection lifecycle.

Handles socket connections on the ``/file-converter`` namespace. The converter is
an internal, same-stack service with no user identity of its own, so the
connection is authenticated with a per-deployment service secret shared by the
backend and the converter (``FILE_CONVERTER_SERVICE_TOKEN``) rather than a user
login. Per-event handlers still validate the acting user's access token (see
``file_converter_socket_auth``), because one converter connection serves uploads
triggered by many different users.
"""

import hmac
import os

from mascope_backend.api.new.auth.service_token import FILE_CONVERTER_SERVICE_TOKEN
from mascope_backend.runtime import runtime
from mascope_backend.socket import sio
from mascope_backend.socket.storage.services import register_service, unregister_service


@sio.event(namespace="/file-converter")
async def connect(sid: str, environ: dict, auth: dict | None) -> bool:
    """
    Handle file converter service connections.

    Accepts the connection only when the client both names itself as the
    file-converter service and presents the shared service secret, so an
    arbitrary client that guessed the public ``X-Service-Name`` string cannot
    subscribe to the namespace and receive its (token-bearing) payloads.

    :param sid: Socket session ID
    :type sid: str
    :param environ: WSGI environment containing request data
    :type environ: dict
    :param auth: Client-supplied auth payload; carries ``service_token``
    :type auth: dict | None
    :return: Connection acceptance status
    :rtype: bool
    """
    worker_pid = os.getpid()
    try:
        service_name = environ.get("HTTP_X_SERVICE_NAME")
        if service_name != "file-converter":
            runtime.logger.warning(
                f"Unexpected connection to file-converter namespace: {service_name} [Worker {worker_pid}]"
            )
            return False

        service_token = (auth or {}).get("service_token")
        if not service_token or not hmac.compare_digest(
            service_token, FILE_CONVERTER_SERVICE_TOKEN
        ):
            runtime.logger.warning(
                f"File-converter connection rejected: missing or invalid service token [Worker {worker_pid}]"
            )
            return False

        await register_service("file-converter", sid)
        runtime.logger.debug(
            f"File converter service connected with sid {sid} [Worker {worker_pid}]"
        )
        return True
    except Exception as e:
        runtime.logger.error(
            f"Error in file converter connection: {str(e)} [Worker {worker_pid}]"
        )
        return False


@sio.event(namespace="/file-converter")
async def disconnect(sid: str) -> None:
    """
    Handle file converter service disconnections.

    :param sid: Socket session ID
    :type sid: str
    """
    worker_pid = os.getpid()
    await unregister_service("file-converter", sid)
    runtime.logger.debug(
        f"File converter service disconnected: {sid} [Worker {worker_pid}]"
    )
