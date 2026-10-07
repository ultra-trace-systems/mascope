"""
Uvicorn server entry point with main process initialization.
"""

import asyncio
import os

import uvicorn

from mascope_backend.app.startup import init_main_process
from mascope_backend.runtime import runtime


def run():
    """
    Entry point to run Mascope server.

    Initialization flow:
    1. Main process initialization (runs once, always)
       - Database migrations
       - File system cleanup
       - Application state reset
    2. Start uvicorn with configured workers
    3. Each worker runs its own lifespan initialization
       - Redis connection
       - Per-worker application setup

    :raises Exception: If main process initialization fails
    """
    expose = os.environ.get("MASCOPE_DEVHOST")
    host = "0.0.0.0" if (runtime.mode == "prod" or expose) else "localhost"

    # --- Main process initialization ---
    # Run database migrations and cleanup ONCE before starting uvicorn
    asyncio.run(init_main_process())

    workers = runtime.config.get_worker_count()

    if runtime.mode == "dev" and workers > 1:
        runtime.logger.warning(
            f"You are using {workers} uvicorn workers which may cause memory issues on Windows "
            f"due to port-sharing issues on Windows which may lead to occasional http timeouts. "
            f"Consider using a single worker in development."
        )

    # Hot reload only works with single worker
    enable_reload = runtime.mode == "dev" and workers == 1

    runtime.logger.info(
        f"Starting uvicorn at {host}:{runtime.meta.api_port} "
        f"in {runtime.mode} mode with {workers} worker(s)"
    )

    if enable_reload:
        runtime.logger.info("Hot reload: ENABLED")
    elif runtime.mode == "dev":
        runtime.logger.info("Hot reload: DISABLED (multi-worker mode)")

    if expose:
        runtime.logger.warning("Exposing dev server to the network")

    uvicorn.run(
        "mascope_backend.app.socket_app:sio_app",
        host=host,
        port=runtime.meta.api_port,
        workers=workers,
        reload=enable_reload,
        reload_excludes=["libraries/sdk/**"] if enable_reload else None,
        log_level="critical",
        use_colors=True,
        # An upload's acquisition record travels in the headers of the
        # request that creates the upload: up to 16 KB of JSON, 22 KB once
        # encoded. By default the parser gives up on a request whose headers
        # have not all arrived within 16 KB, which is how a network delivers
        # ones this long.
        h11_max_incomplete_event_size=64 * 1024,
    )
