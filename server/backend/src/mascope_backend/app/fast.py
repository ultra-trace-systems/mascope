"""
FastAPI application with per-worker initialization.

This module defines the FastAPI application instance and its lifespan
context, which handles per-worker startup and shutdown tasks. The
lifespan runs once per worker process, after main process initialization
is complete.
"""

import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from mascope_backend.api.lib.exceptions.api_exceptions import (
    ApiException,
    api_e_response_json,
    handle_exception,
)
from mascope_backend.api.lib.rate_limit import client_ip
from mascope_backend.api.routes import routers
from mascope_backend.db import init_db
from mascope_backend.origins import (
    dev_origin_regex,
    dev_origins,
    is_trusted_write_origin,
    origin_of,
    own_request_origins,
)
from mascope_backend.runtime import runtime
from mascope_backend.socket.storage import redis_storage_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Per-worker lifespan context manager for FastAPI application.

    Each worker process runs this independently after the main process
    has completed initialization.

    Worker startup tasks:
    - Configure database engine and connection pool for this worker
    - Connect to Redis for cross-worker session storage

    Worker shutdown tasks:
    - Drain detached auto-processing pipelines (waits, then cancels what
      overruns, so a truncated pipeline still names its file)
    - Disconnect Redis client

    :param app: FastAPI application instance
    :raises ConnectionError: If Redis connection fails (logged as warning)
    """
    worker_pid = os.getpid()

    # --- STARTUP TASKS ---
    runtime.logger.info(
        f"Fast App startup: starting initialization [Worker {worker_pid}]"
    )
    # Initialize database
    runtime.logger.info(
        f"Fast App startup: initializing database [Worker {worker_pid}]"
    )
    await init_db()

    # Initialize Redis storage client for cross-worker socket state
    runtime.logger.info(
        f"Fast App startup: connecting Redis storage client [Worker {worker_pid}]"
    )
    try:
        await redis_storage_client.connect()
    except ConnectionError:
        # One record with the traceback (not an ERROR + WARNING pair)
        runtime.logger.exception(
            f"Fast App startup: Redis storage client failed to connect -"
            f" multi-worker storage sharing will not work [Worker {worker_pid}]"
        )

    # Yield control back to FastAPI
    yield

    # --- SHUTDOWN TASKS ---
    # Auto-processing runs detached from the request that scheduled it, so uvicorn
    # does not wait on it the way it waits on connection tasks. Drain it here or a
    # deploy truncates in-flight pipelines mid-file (#1844).
    from mascope_backend.api.controllers.sample.files.process.service import (
        drain_auto_process_tasks,
    )

    runtime.logger.info(
        f"Fast App shutdown: draining background tasks [Worker {worker_pid}]"
    )
    await drain_auto_process_tasks()

    runtime.logger.info(
        f"Fast App shutdown: closing Redis storage client [Worker {worker_pid}]"
    )
    await redis_storage_client.disconnect()


def _docs_kwargs(mode: str) -> dict[str, str | None]:
    """
    FastAPI docs kwargs for the given runtime mode.

    The interactive API docs and the OpenAPI schema are dev-only: they expose
    the full API surface and are never proxied to users, so they stay off in
    prod to remove needless recon value for a directly-reachable backend.
    ``openapi_url=None`` also disables ``/docs`` and ``/redoc``, which depend
    on it, but all three are set explicitly for clarity.

    :param mode: Runtime mode ("dev" or "prod").
    :return: Kwargs to pass to ``FastAPI(...)``.
    """
    if mode == "dev":
        return {
            "docs_url": "/docs",
            "redoc_url": "/redoc",
            "openapi_url": "/openapi.json",
        }
    return {"docs_url": None, "redoc_url": None, "openapi_url": None}


# Initialize FastAPI with the lifespan.
fast = FastAPI(lifespan=lifespan, **_docs_kwargs(runtime.mode))


#: Methods a cross-site page could use to change state with the victim's
#: ambient cookie. Safe methods stay unchecked, as does OPTIONS: the CORS
#: preflight must be answerable from anywhere, and neither changes state.
_STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: Read once, like the docs and CORS wiring above and below: a mode change
#: requires a restart anyway, and the backend's ``runtime.mode`` is a
#: file-backed read that must not run per request.
_DEV_MODE = runtime.mode == "dev"


# Cross-site write guard. Defined before the logging middleware on purpose:
# earlier-added middleware sits closer to the app, so refusals travel back out
# through the logger and appear in the access log.
@fast.middleware("http")
async def origin_guard(request: Request, call_next):
    """
    Refuse a state-changing request that declares a foreign origin.

    CSRF defence in depth. The auth cookie is ``SameSite=lax``, so browsers
    already withhold it on cross-site writes - but that is one cookie
    attribute deep, says nothing about non-browser clients, and vanishes
    silently the day the cookie needs ``SameSite=None`` (an embedding or a
    cross-subdomain client). This check does not depend on the visitor's
    browser: a write whose ``Origin`` (or, when absent, ``Referer``) is not
    this deployment's own is answered 403 before routing, so every endpoint -
    current and future - inherits it.

    A request declaring no origin at all passes. The CSRF vector is a browser,
    and browsers do send ``Origin`` on cross-site state-changing requests;
    what sends neither header is the non-browser clients - the instrument
    agents' token-authenticated uploads, the file converter, curl - for which
    an absent declaration is not a mismatch. A present-but-empty ``Origin``
    is a declaration, though, and is refused like any other unrecognized
    value. Socket.IO traffic never reaches this guard (the ASGI wrapper
    answers it first); its handshake makes the same check itself, from the
    same policy module.
    """
    # upper(): the ASGI spec promises an uppercased method, but uvicorn's h11
    # implementation forwards the request-line token verbatim, and a raw
    # "patch" must not slip the net.
    if request.method.upper() in _STATE_CHANGING_METHODS:
        claimed = request.headers.get("Origin")
        if claimed is None:
            claimed = origin_of(request.headers.get("Referer"))
        if claimed is not None and not is_trusted_write_origin(
            claimed,
            own_request_origins(
                request.url.scheme,
                request.url.netloc,
                request.headers.get("X-Forwarded-Proto"),
                request.headers.get("X-Forwarded-Host"),
            ),
            dev=_DEV_MODE,
        ):
            exc = HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"{claimed} is not an accepted origin for "
                "state-changing requests",
            )
            return handle_exception(exc, "Access denied", response_type="http")
    return await call_next(request)


# Logging middleware
@fast.middleware("http")
async def logger_middleware(request: Request, call_next):
    worker_pid = os.getpid()

    # Make the request and receive a response, timing the server-side handling
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = round((time.perf_counter() - start) * 1000, 1)

    # Surface the timing to browser devtools / API clients
    response.headers["Server-Timing"] = f"app;dur={duration_ms}"

    # Log the real client address, not the direct peer (the nginx container).
    # nginx sets X-Real-IP to the restored client and overwrites it, so through
    # the proxy it cannot be spoofed; the helper falls back to the transport
    # peer for direct/dev connections. Shared with the rate limiter's
    # per-client key so the access log and the limiter agree on the client.
    client_host = client_ip(request)

    with runtime.logger.contextualize(
        path=request.url.path,
        method=request.method,
        client_host=client_host,
        request_id=str(uuid.uuid4()),
        status_code=response.status_code,
        worker_pid=worker_pid,
        duration_ms=duration_ms,
    ):
        # Log full URL with query params in debug mode
        if runtime.config.log_level == "debug":
            full_url = f"{request.url.scheme}://{client_host}{request.url.path}"
            if request.query_params:
                full_url += f"?{request.url.query}"
            runtime.logger.debug(f"{full_url} [Worker {worker_pid}]")

        # Access log, always INFO. 4xx responses are routine client behavior
        # (expired sessions, validation errors) and 5xx responses are already
        # logged with their traceback by process_exception - logging either
        # above INFO here would duplicate that record without the traceback.
        # The status code stays queryable via the contextualized extra.
        runtime.logger.info(request.url.path)

    return response


# CORS only for dev — in prod, frontend and backend share the same origin
if runtime.mode == "dev":
    fast.add_middleware(
        CORSMiddleware,
        # Shared with the Socket.IO origin check (mascope_backend.origins), so
        # the two surfaces cannot drift into accepting different origins.
        allow_origins=dev_origins(),
        allow_origin_regex=dev_origin_regex(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[
            # Mascope custom
            "Process-ID",
            # tus chunked uploads
            "Location",
            "Upload-Offset",
            "Tus-Resumable",
            "Tus-Version",
            "Tus-Extension",
            "Tus-Max-Size",
            "Upload-Expires",
            "Upload-Length",
        ],
    )

# Routing
for router in routers:
    fast.include_router(router)


# Exception handlers
@fast.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """
    Handle Pydantic validation errors on incoming request data.

    :param request: The incoming request.
    :param exc: The validation error.
    :return: Structured JSON error response.
    :rtype: JSONResponse
    """
    # The request body is deliberately not included in the response or the
    # logs: it can carry credentials (e.g. login forms) and other user data.
    context_message = f"Validation error on route {request.method} {request.url.path}"
    return handle_exception(exc, context_message, response_type="http")


@fast.exception_handler(ValueError)
async def value_exception_handler(request: Request, exc: ValueError) -> JSONResponse:
    """
    Handle ValueError — right type, inappropriate value.

    :param request: The incoming request.
    :param exc: The value error.
    :return: Structured JSON error response.
    :rtype: JSONResponse
    """
    return handle_exception(exc, "Invalid value", response_type="http")


@fast.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """
    Handle FastAPI HTTP exceptions (401, 403, 400, etc.) uniformly.

    :param request: The incoming request.
    :param exc: The HTTP exception.
    :return: Structured JSON error response.
    :rtype: JSONResponse
    """
    if exc.status_code == status.HTTP_401_UNAUTHORIZED:
        context_message = "Authorization failed"
    elif exc.status_code == status.HTTP_403_FORBIDDEN:
        context_message = "Access denied"
    elif exc.status_code == status.HTTP_400_BAD_REQUEST:
        context_message = "Bad request"
    else:
        # Keep the user-facing context generic: the exception's detail is
        # appended by process_exception, and str(exc) (status + detail) is
        # already part of the server-side log line. Embedding the detail here
        # as well would duplicate it in the client message and leak the
        # internal "HTTPException on METHOD /path" wording.
        context_message = "Request failed"
    return handle_exception(exc, context_message, response_type="http")


@fast.exception_handler(ApiException)
async def api_exception_handler(request: Request, exc: ApiException) -> JSONResponse:
    """
    Return an ApiException that escaped a route as its own JSON response.

    ``@api_route`` already converts ApiException into a response itself, so
    this handler covers the routes Mascope does not own - the tus upload
    routes in particular, plus the undecorated auth routes (login, logout,
    mfa/verify, pairing, access-token regenerate). The tus routes reach
    ``@api_controller``-decorated code that *raises* ApiException, but
    tuspyserver generates the routes themselves, so they cannot carry
    ``@api_route`` and nothing converted it. Without a handler for the type,
    the exception fell through to ``global_exception_handler``, and
    Starlette's ServerErrorMiddleware re-raises after that handler responds -
    so a routine 401 (an upload token expiring mid-transfer) was captured by
    error monitoring as an unhandled server fault.

    The routes are attached with ``include_router``, not ``mount``: a mounted
    sub-application carries its own ExceptionMiddleware and would not be
    covered by a handler registered here.

    The exception carries its own status code and user message, and is
    serialized exactly as ``@api_route`` serializes one. Every ApiException
    that can reach this handler today is built by ``process_exception``, which
    logs it at its proper level; one raised directly would arrive unlogged, so
    keep that in mind before raising ApiException from an undecorated route.

    :param request: The incoming request.
    :param exc: The escaped ApiException.
    :return: The exception's structured JSON response.
    :rtype: JSONResponse
    """
    return api_e_response_json(exc)


@fast.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Catch-all handler for any unhandled exception during request processing.

    :param request: The incoming request.
    :param exc: The unhandled exception.
    :return: Structured JSON error response.
    :rtype: JSONResponse
    """
    context_message = f"Unhandled exception on {request.method} {request.url.path}"
    return handle_exception(exc, context_message, response_type="http")
