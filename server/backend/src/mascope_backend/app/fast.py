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

from mascope_backend.api.lib.exceptions.api_exceptions import handle_exception
from mascope_backend.api.lib.rate_limit import client_ip
from mascope_backend.api.routes import routers
from mascope_backend.db import init_db
from mascope_backend.origins import dev_origin_regex, dev_origins
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
