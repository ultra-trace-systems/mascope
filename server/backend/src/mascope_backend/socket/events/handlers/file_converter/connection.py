"""
File converter service connection lifecycle.

Handles socket connections on the ``/file-converter`` namespace. The converter is
an internal, same-stack service with no user identity of its own, so the
connection is authenticated with a per-deployment service secret shared by the
backend and the converter (``FILE_CONVERTER_SERVICE_TOKEN``) rather than a user
login. Per-event handlers still validate the acting user's access token (see
``file_converter_socket_auth``), because one converter connection serves uploads
triggered by many different users.

An accepted converter joins ``FILE_CONVERTER_ROOM``, which is what the backend
emitters target: room membership lives in the Socket.IO client manager, which
keeps it current across connects and disconnects, so payload delivery never
depends on the Redis presence key. The presence key (``register_service``)
backs the coarse availability checks that let API routes return 503 instead of
accepting work no converter would receive.
"""

import hmac
import os

from mascope_backend.runtime import runtime
from mascope_backend.service_token import FILE_CONVERTER_SERVICE_TOKEN
from mascope_backend.socket import sio
from mascope_backend.socket.events.emitters.file_converter import FILE_CONVERTER_ROOM
from mascope_backend.socket.storage.services import register_service, unregister_service


@sio.event(namespace="/file-converter")
async def connect(sid: str, environ: dict, auth: dict | None) -> bool:
    """
    Handle file converter service connections.

    Accepts the connection only when the client both names itself as the
    file-converter service and presents the shared service secret, so an
    arbitrary client that guessed the public ``X-Service-Name`` string cannot
    subscribe to the namespace and receive its (token-bearing) payloads.

    The auth payload is client-controlled JSON of any shape; the gates below
    treat a wrong type as a plain rejection (WARNING), reserving the ERROR path
    for genuine server faults.

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

        service_token = auth.get("service_token") if isinstance(auth, dict) else None
        if not isinstance(service_token, str) or not service_token:
            runtime.logger.warning(
                f"File-converter connection rejected: no service token supplied [Worker {worker_pid}]"
            )
            return False

        # Compare as bytes: compare_digest raises on non-ASCII str input, and
        # the client-supplied token can be any unicode string.
        if not hmac.compare_digest(
            service_token.encode("utf-8"), FILE_CONVERTER_SERVICE_TOKEN.encode("utf-8")
        ):
            runtime.logger.warning(
                "File-converter connection rejected: the supplied service token "
                "does not match this deployment's derived secret. If this is "
                "the deployment's own converter, it is likely running with a "
                f"stale jwt_secret_key and needs a restart. [Worker {worker_pid}]"
            )
            return False

        if not await register_service("file-converter", sid):
            # Refuse rather than accept an unregistered connection: the
            # availability checks would report the converter absent for the
            # socket's whole lifetime. The converter client retries with
            # backoff, and the next attempt registers afresh.
            runtime.logger.warning(
                f"File-converter connection refused: presence registration failed [Worker {worker_pid}]"
            )
            return False

        await sio.enter_room(sid, FILE_CONVERTER_ROOM, namespace="/file-converter")
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

    Room membership needs no cleanup here - the client manager evicts the sid
    from its rooms on disconnect. Only the Redis presence key is ours to clear.

    :param sid: Socket session ID
    :type sid: str
    """
    worker_pid = os.getpid()
    await unregister_service("file-converter", sid)
    runtime.logger.debug(
        f"File converter service disconnected: {sid} [Worker {worker_pid}]"
    )
