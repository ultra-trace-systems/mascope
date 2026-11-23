"""Tests: the readiness route ``GET /ready``, which the backend's healthcheck asks.

The compose healthcheck used to only connect to the backend's port, so a
backend whose Postgres and Redis were stopped stayed ``healthy`` (#3470). The
route answers 200 only while this worker reaches both, through the app's own
engine and Redis client, each within its own timeout, and 503 otherwise. It
answers with no sign-in, says ``ready`` or ``not ready`` and nothing more, is
left out of the OpenAPI document, and logs why it is not ready once per change
rather than on every probe.

The database is the test database (``bind_db``). Redis is stood in for by
servers made here, reached with a real redis-py client built as the app builds
its own: CI's backend job runs no Redis.
"""

import asyncio
import contextlib
import time

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from redis.asyncio import from_url
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialWithJitterBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_utils import captured_logs

from mascope_backend import db
from mascope_backend.api.new.readiness import routes as readiness
from mascope_backend.app.fast import fast
from mascope_backend.socket.storage import redis_storage_client
from mascope_backend.socket.storage.client import COMMAND_RETRIES, SOCKET_TIMEOUT


@contextlib.asynccontextmanager
async def _server(answer: bytes | None):
    """
    A TCP server on a free port of this host.

    :param answer: What it writes back for each read; None to accept and never
        answer, as a hung store does.
    :yield: Its port.
    """
    held: list[asyncio.StreamWriter] = []

    async def handle(reader, writer):
        held.append(writer)
        while data := await reader.read(65536):
            if answer is not None:
                # One reply per command. redis-py sends one command per write
                # here, which is all these tests need of the protocol.
                writer.write(answer * max(1, data.count(b"*")))
                await writer.drain()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        yield server.sockets[0].getsockname()[1]
    finally:
        for writer in held:
            writer.close()
        server.close()
        await server.wait_closed()


def _closed_port() -> int:
    """A port of this host that nothing listens on."""
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _redis_at(port: int):
    """A Redis client for ``port``, with the app's own timeouts and retry."""
    return from_url(
        f"redis://127.0.0.1:{port}/0",
        decode_responses=True,
        socket_timeout=SOCKET_TIMEOUT,
        socket_connect_timeout=SOCKET_TIMEOUT,
        retry=Retry(ExponentialWithJitterBackoff(cap=1.0, base=0.1), COMMAND_RETRIES),
        retry_on_error=[RedisConnectionError, RedisTimeoutError],
    )


def _database_at(port: int):
    """A session maker whose engine points at ``port``, built as the app's is."""
    engine = create_async_engine(
        f"postgresql+asyncpg://nobody:nothing@127.0.0.1:{port}/nothing",
        pool_pre_ping=True,
    )
    return async_sessionmaker(engine)


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    """Each test starts as a worker does: ready, and nothing overrunning."""
    monkeypatch.setattr(readiness, "_last_failures", {})
    monkeypatch.setattr(readiness, "_overrunning", {})


@pytest_asyncio.fixture
async def redis_up(monkeypatch):
    """The app's Redis client, answering PONG to every command."""
    async with _server(b"+PONG\r\n") as port:
        client = _redis_at(port)
        monkeypatch.setattr(redis_storage_client, "_client", client)
        yield client
        await client.aclose()


@pytest_asyncio.fixture
async def anonymous():
    """A client with no session and no token."""
    async with AsyncClient(
        transport=ASGITransport(app=fast), base_url="http://test"
    ) as client:
        yield client


async def _timed_get(client) -> tuple[object, float]:
    started = time.perf_counter()
    resp = await client.get(readiness.READY_PATH)
    return resp, time.perf_counter() - started


def _says_only(resp, word: str) -> None:
    """The body is the one word, as plain text, and is never cached."""
    assert resp.text == word
    assert resp.headers["content-type"].startswith("text/plain")
    assert resp.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_ready_while_both_answer_with_no_sign_in(redis_up, anonymous):
    resp = await anonymous.get("/ready")

    assert resp.status_code == 200
    _says_only(resp, "ready")


@pytest.mark.asyncio
async def test_not_ready_while_the_database_refuses(redis_up, anonymous, monkeypatch):
    monkeypatch.setattr(db, "ASYNC_SESSION_MAKER", _database_at(_closed_port()))

    resp, took = await _timed_get(anonymous)

    assert resp.status_code == 503
    _says_only(resp, "not ready")
    assert took < readiness.CHECK_TIMEOUT_S + 1


@pytest.mark.asyncio
async def test_not_ready_within_its_timeout_while_the_database_hangs(
    redis_up, anonymous, monkeypatch
):
    """A database that accepts and never answers would hold a probe for as long
    as asyncpg's own connect timeout; the route answers in its own time."""
    async with _server(None) as port:
        monkeypatch.setattr(db, "ASYNC_SESSION_MAKER", _database_at(port))

        resp, took = await _timed_get(anonymous)

    assert resp.status_code == 503
    _says_only(resp, "not ready")
    assert readiness.CHECK_TIMEOUT_S - 0.05 <= took < readiness.CHECK_TIMEOUT_S + 1


@pytest.mark.asyncio
async def test_not_ready_while_redis_refuses(anonymous, monkeypatch):
    client = _redis_at(_closed_port())
    monkeypatch.setattr(redis_storage_client, "_client", client)

    resp, took = await _timed_get(anonymous)
    await client.aclose()

    assert resp.status_code == 503
    _says_only(resp, "not ready")
    assert took < readiness.CHECK_TIMEOUT_S + 1


@pytest.mark.asyncio
async def test_not_ready_within_its_timeout_while_redis_hangs(anonymous, monkeypatch):
    """The app's client waits 5 s twice for a reply; the route does not."""
    async with _server(None) as port:
        client = _redis_at(port)
        monkeypatch.setattr(redis_storage_client, "_client", client)

        resp, took = await _timed_get(anonymous)
        await client.aclose()

    assert resp.status_code == 503
    _says_only(resp, "not ready")
    assert readiness.CHECK_TIMEOUT_S - 0.05 <= took < readiness.CHECK_TIMEOUT_S + 1


@pytest.mark.asyncio
async def test_not_ready_while_redis_never_connected(anonymous, monkeypatch):
    """A backend that started without Redis has no client until it reconnects."""
    monkeypatch.setattr(redis_storage_client, "_client", None)

    resp = await anonymous.get("/ready")

    assert resp.status_code == 503
    _says_only(resp, "not ready")


@pytest.mark.asyncio
async def test_the_body_never_carries_the_error(anonymous, monkeypatch):
    """Whatever a store says goes to the log, not to the caller."""

    async def leaky():
        raise RuntimeError("password=hunter2 at db.internal:5432")

    monkeypatch.setitem(readiness.CHECKS, "database", leaky)
    monkeypatch.setitem(readiness.CHECKS, "redis", leaky)

    with captured_logs("WARNING") as records:
        resp = await anonymous.get("/ready")

    assert resp.status_code == 503
    _says_only(resp, "not ready")
    assert "hunter2" in records[0]["message"]


@pytest.mark.asyncio
async def test_the_reason_is_logged_once_per_change(anonymous, monkeypatch):
    """A probe every ten seconds must not write a line every ten seconds."""
    state = {"database": None, "redis": None}

    def check(name):
        async def run():
            if state[name] is not None:
                raise ConnectionError(state[name])

        return run

    for name in state:
        monkeypatch.setitem(readiness.CHECKS, name, check(name))

    async def probe_messages(times: int) -> list[tuple[str, str]]:
        with captured_logs("INFO") as records:
            for _ in range(times):
                await anonymous.get("/ready")
        return [
            (r["level"].name, r["message"])
            for r in records
            if r["name"] == readiness.__name__
        ]

    assert await probe_messages(3) == []

    state["database"] = "database down"
    down = await probe_messages(3)
    assert [level for level, _ in down] == ["WARNING"]
    assert "database: ConnectionError: database down" in down[0][1]
    assert "redis" not in down[0][1]

    state["redis"] = "redis down"
    both = await probe_messages(3)
    assert [level for level, _ in both] == ["WARNING"]
    assert "database" in both[0][1] and "redis: ConnectionError" in both[0][1]

    state["database"] = state["redis"] = None
    back = await probe_messages(3)
    assert [level for level, _ in back] == ["INFO"]


@pytest.mark.asyncio
async def test_a_check_winding_down_is_not_started_again(anonymous, monkeypatch):
    """A hung store can hold the clean-up of a cancelled check as long as the
    check: the answer does not wait for it, and the next probe does not start a
    second one beside it."""
    monkeypatch.setattr(readiness, "CHECK_TIMEOUT_S", 0.2)
    release = asyncio.Event()
    started = []

    async def hung():
        started.append(1)
        try:
            await asyncio.sleep(3600)
        finally:
            await release.wait()  # the clean-up a hung store stalls

    async def answers():
        pass

    monkeypatch.setitem(readiness.CHECKS, "database", hung)
    monkeypatch.setitem(readiness.CHECKS, "redis", answers)

    first, took = await _timed_get(anonymous)
    second = await anonymous.get("/ready")

    assert (first.status_code, second.status_code) == (503, 503)
    assert took < 1
    assert started == [1]

    release.set()
    for _ in range(50):
        if not readiness._overrunning:
            break
        await asyncio.sleep(0.01)
    monkeypatch.setitem(readiness.CHECKS, "database", answers)

    assert (await anonymous.get("/ready")).status_code == 200


def test_left_out_of_the_openapi_document():
    paths = fast.openapi()["paths"]

    assert "/ready" not in paths
    assert not any(path.endswith("/ready") for path in paths)


@pytest.mark.asyncio
async def test_left_out_of_the_access_log(redis_up, anonymous):
    with captured_logs("INFO") as records:
        await anonymous.get("/ready")
        await anonymous.get("/api/version/public")

    paths = [r["extra"].get("path") for r in records]
    assert "/api/version/public" in paths
    assert "/ready" not in paths
