"""Fixtures shared by the auth unit tests.

``launch_probe`` measures how long a sign-in dependency holds its admission
permit, with no database: the session maker opens nothing, and the worker's
``db_semaphore`` is replaced by one with a single permit, so ``locked()``
says whether the request's session is still open.
"""

import asyncio

import pytest
from fastapi import BackgroundTasks, Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from mascope_backend import db as db_module


class _NoDatabase:
    """A session maker whose sessions touch nothing."""

    async def __aenter__(self):
        return object()

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def launch_probe(monkeypatch):
    """Launch a background task behind a sign-in dependency, as a run's route does.

    Returns an async callable taking the dependency. It builds an app with one
    ``POST /launch`` route that takes the dependency and hands a task to
    ``background_tasks``, as ``POST .../assign`` does, posts to it once, and
    answers whether the permit was held while the endpoint ran and while the
    task ran: ``{"endpoint": bool, "task": bool}``.

    The post is bounded by a timeout: a dependency that opened a second
    session would wait for a permit the first one holds.
    """
    permit = asyncio.Semaphore(1)
    monkeypatch.setattr(db_module, "db_semaphore", permit)
    monkeypatch.setattr(db_module, "ASYNC_SESSION_MAKER", _NoDatabase)

    async def _launch(dependency) -> dict[str, bool]:
        held: dict[str, bool] = {}
        app = FastAPI()

        @app.post("/launch")
        async def launch(
            background_tasks: BackgroundTasks, _signed_in=Depends(dependency)
        ):
            held["endpoint"] = permit.locked()

            async def run():
                held["task"] = permit.locked()

            background_tasks.add_task(run)
            return {}

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await asyncio.wait_for(client.post("/launch"), timeout=5)
        assert response.status_code == 200, response.text
        return held

    return _launch
