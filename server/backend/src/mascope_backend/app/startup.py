"""
Main process startup initialization.

This module contains one-time initialization logic that runs once in the main
process before any workers are spawned. Running tasks here prevents per-worker
race conditions on shared state.

Tasks:
- File system cleanup and setup
- Application state reset (stuck batch recovery, interrupted file processing)
- Idempotent data initialization (acquisition datasets, the ionization
  mechanisms and modes Mascope ships)
"""

import os
import shutil

from mascope_backend.api.controllers.dataset.acquisition.service import (
    create_acquisition_datasets,
)
from mascope_backend.api.new.notifications.service import (
    purge_read_notifications,
)
from mascope_backend.db import configure_database_engine, dispose_engine
from mascope_backend.db.admin.batch.reset_processing_status import (
    reset_stuck_processing_batches,
)
from mascope_backend.db.admin.ionization.ensure_system_modes import (
    ensure_system_ionization,
)
from mascope_backend.db.admin.peak_assignments.reset_running_runs import (
    reset_running_batch_peak_runs,
    reset_running_peak_assignment_runs,
)
from mascope_backend.db.admin.sample_file.reset_interrupted_processing import (
    reset_interrupted_processing,
)
from mascope_backend.runtime import runtime
from mascope_file.gc import gc_filestore


async def init_main_process() -> None:
    """
    Runs once per server startup in the main process, before workers are spawned.

    Initialization order:
    - Reset temp directory
    - Garbage collect orphaned files from filestore
    - Configure a short-lived DB engine for one-time startup tasks
    - Reset any batches stuck in 'processing' from a previous run
    - Mark sample files whose processing a restart interrupted as failed, and
      keep a notification of it for the people answerable for each
    - Purge notifications read long ago
    - Auto-create missing acquisition datasets for all instruments
    - Seed the ionization mechanisms and modes Mascope ships, building the
      target ions of the library's compounds for a mechanism it lacked
    - Dispose the engine — each worker initialises its own independently

    :raises Exception: If any critical initialization step fails
    :return: None
    """
    # --- Filesystem ---
    # Reset temp directory
    runtime.logger.info("Main process: initializing temp directory")
    temp_dir = runtime.env.path("temp")
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)
    os.mkdir(temp_dir)

    # Clean filestore
    runtime.logger.info("Main process: garbage collecting filestore")
    gc_filestore()

    # --- Database ---
    # Configure a short-lived engine so startup tasks can use async_session.
    # Disposed after tasks complete; workers configure their own engines in lifespan.
    runtime.logger.info("Main process: configuring database engine")
    await configure_database_engine()

    try:
        runtime.logger.info("Main process: resetting stuck processing batches")
        await reset_stuck_processing_batches()

        runtime.logger.info("Main process: resetting interrupted assignment runs")
        await reset_running_peak_assignment_runs()

        runtime.logger.info("Main process: resetting interrupted batch peak runs")
        await reset_running_batch_peak_runs()

        runtime.logger.info("Main process: resetting interrupted file processing")
        await reset_interrupted_processing()

        runtime.logger.info("Main process: purging notifications read long ago")
        await purge_read_notifications()

        runtime.logger.info("Main process: initializing acquisition datasets")
        await create_acquisition_datasets()

        # Logged and carried, not raised: without them a server still serves,
        # its runs recording each channel they could not search, and the next
        # start tries again. The steps above are different - a reset left
        # undone would leave work looking like it is still running.
        runtime.logger.info(
            "Main process: seeding the ionization mechanisms and modes Mascope ships"
        )
        try:
            await ensure_system_ionization()
        except Exception as e:
            runtime.logger.error(
                f"Main process: could not seed the system ionization chemistry: {e}"
            )
    finally:
        # Dispose engine regardless of task outcome; catch disposal errors
        # so they never mask the original startup exception
        try:
            await dispose_engine()
        except Exception as e:
            runtime.logger.error(
                f"Main process: failed to dispose database engine: {e}"
            )
