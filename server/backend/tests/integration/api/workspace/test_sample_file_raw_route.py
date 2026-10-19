"""
Tests: a sample file's source data file, ``GET /api/sample/files/{id}/raw``.

An API client (an SDK script) fetches the file it holds an id for with its
token, so the route is exercised end to end: the permission check runs, the
rate limit counts, and the body is the stored file byte for byte, with the
headers a client saves it by.

The records these tests read are rows without a filestore behind them, so
every sample file's directory is a temporary one the tests fill, and what kind
of file it keeps is what each test says.

Fixtures used:
- ``sample_file`` - linked to ``alpha_item`` (ws_alpha, all users are members)
- ``beta_sample_file`` - linked to ``beta_item`` (ws_beta, only owner is member)
- ``orphan_sample_file`` - no sample item references
"""

import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from mascope_backend.api.controllers.sample.files import sample_files_controller
from mascope_backend.api.routes.sample.files import sample_files_routes
from mascope_backend.app.fast import fast
from mascope_backend.db import AccessToken, SampleFile, hash_access_token
from mascope_backend.db.id import gen_id
from mascope_backend.socket.storage import redis_storage_client
from mascope_file import name as mascope_name


#: Not a valid raw file, and need not be: the route sends bytes, it reads none.
#: Every byte value, so a text-mode read or an encoding on the way shows.
RAW_BYTES = bytes(range(256)) * 64 + b"tail"


class _InMemoryRedis:
    """The Redis commands the rate limiter uses, over a plain dict."""

    def __init__(self):
        self.values: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    async def expire(self, key: str, seconds: int) -> bool:
        return True

    async def ttl(self, key: str) -> int:
        return 60


@pytest.fixture(autouse=True)
def limiter(monkeypatch) -> _InMemoryRedis:
    """A limiter of this test's own: the shared Redis keeps a window across
    runs, and a run soon after another would start part way into it."""
    stub = _InMemoryRedis()
    monkeypatch.setattr(redis_storage_client, "_client", stub)
    return stub


@pytest.fixture
def filestore(monkeypatch, tmp_path):
    """Put every sample file's directory under ``tmp_path``, as an Orbitrap
    file keeping its raw file unless a test says otherwise.

    :return: ``store(kind, data)``: make the sample keep ``kind``'s data file
        (``"orbi_raw"`` or ``"tof_h5"``) holding ``data``, or none at all with
        ``data=None``; it returns the file's path.
    """
    kind = {"type": "orbi_raw"}
    monkeypatch.setattr(mascope_name, "get_sample_file_type", lambda _f: kind["type"])
    monkeypatch.setattr(
        mascope_name,
        "parse_path_from_item_filename",
        lambda _f: str(tmp_path),
    )

    def store(sample_type: str = "orbi_raw", data: bytes | None = RAW_BYTES):
        kind["type"] = sample_type
        name = "data.h5" if sample_type.startswith("tof") else "data.raw"
        path = tmp_path / name
        if data is not None:
            path.write_bytes(data)
        return path

    return store


async def _filename(async_session_factory, sample_file_id: str) -> str:
    async with async_session_factory() as session:
        return (
            await session.execute(
                select(SampleFile.filename).where(
                    SampleFile.sample_file_id == sample_file_id
                )
            )
        ).scalar_one()


@pytest_asyncio.fixture
async def sdk_token(async_session_factory, test_users):
    """A real API token of the guest user, issued to the SDK's service name."""
    token = f"raw-{gen_id()}".ljust(43, "x")
    async with async_session_factory() as session:
        session.add(
            AccessToken(
                token_hash=hash_access_token(token),
                user_id=test_users["guest"].id,
                service_name="mascope_sdk",
            )
        )
        await session.commit()
    yield token
    async with async_session_factory() as session:
        await session.execute(
            delete(AccessToken).where(
                AccessToken.token_hash == hash_access_token(token)
            )
        )
        await session.commit()


def _client(headers: dict | None = None) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=fast), base_url="http://test", headers=headers
    )


def _token_headers(token: str) -> dict:
    """What the SDK sends: a bearer token and its service name."""
    return {"Authorization": f"Bearer {token}", "X-Service-Name": "mascope_sdk"}


# ============= The file =============


@pytest.mark.asyncio
async def test_an_api_token_downloads_the_stored_bytes(
    sdk_token, sample_file, alpha_item, filestore
):
    filestore("orbi_raw")
    async with _client(_token_headers(sdk_token)) as client:
        resp = await client.get(f"/api/sample/files/{sample_file}/raw")

    assert resp.status_code == 200, resp.text
    assert resp.content == RAW_BYTES


@pytest.mark.asyncio
async def test_the_headers_give_its_length_and_its_upload_name(
    guest_client, sample_file, alpha_item, filestore, async_session_factory
):
    filestore("orbi_raw")
    filename = await _filename(async_session_factory, sample_file)

    resp = await guest_client.get(f"/api/sample/files/{sample_file}/raw")

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/octet-stream"
    assert resp.headers["content-length"] == str(len(RAW_BYTES))
    assert resp.headers["content-disposition"] == (
        f'attachment; filename="{filename}.raw"'
    )


@pytest.mark.asyncio
async def test_a_tof_file_is_its_h5(
    guest_client, sample_file, alpha_item, filestore, async_session_factory
):
    # The extension follows the processor that converted the file.
    filestore("tof_h5", b"an hdf5 file")
    filename = await _filename(async_session_factory, sample_file)

    resp = await guest_client.get(f"/api/sample/files/{sample_file}/raw")

    assert resp.status_code == 200, resp.text
    assert resp.content == b"an hdf5 file"
    # Bytes to save, whatever the extension would suggest to a browser.
    assert resp.headers["content-type"] == "application/octet-stream"
    assert resp.headers["content-disposition"] == (
        f'attachment; filename="{filename}.h5"'
    )


@pytest.mark.asyncio
async def test_the_file_is_sent_from_where_it_is_stored(
    guest_client, sample_file, alpha_item, filestore, monkeypatch
):
    # No temporary copy: the response is built on the stored path itself.
    stored = filestore("orbi_raw")
    sent = []
    real = sample_files_controller.FileResponse

    def recording(path, *args, **kwargs):
        sent.append(os.fspath(path))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(sample_files_controller, "FileResponse", recording)

    resp = await guest_client.get(f"/api/sample/files/{sample_file}/raw")

    assert resp.status_code == 200, resp.text
    assert sent == [str(stored)]


@pytest.mark.asyncio
@pytest.mark.parametrize("sample_type", ["orbi_zarr", "tof_zarr"])
async def test_a_file_that_keeps_no_source_file_is_not_found(
    guest_client, sample_file, alpha_item, filestore, sample_type
):
    filestore(sample_type, data=None)

    resp = await guest_client.get(f"/api/sample/files/{sample_file}/raw")

    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_a_source_file_missing_from_the_filestore_is_not_found(
    guest_client, sample_file, alpha_item, filestore
):
    # The record says raw, but the file is gone from disk.
    filestore("orbi_raw", data=None)

    resp = await guest_client.get(f"/api/sample/files/{sample_file}/raw")

    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_a_file_whose_kind_cannot_be_told_is_not_found(
    guest_client, sample_file, alpha_item, filestore, monkeypatch
):
    def cannot_tell(_filename):
        raise ValueError("Failed to get instrument type")

    monkeypatch.setattr(mascope_name, "get_sample_file_type", cannot_tell)

    resp = await guest_client.get(f"/api/sample/files/{sample_file}/raw")

    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_an_unknown_file_is_not_found_for_an_admin(admin_client, filestore):
    resp = await admin_client.get(f"/api/sample/files/{gen_id()}/raw")

    assert resp.status_code == 404


# ============= Who may download it =============


@pytest.mark.asyncio
async def test_an_api_token_is_held_to_its_users_access(
    sdk_token, beta_sample_file, beta_item, filestore
):
    filestore("orbi_raw")
    async with _client(_token_headers(sdk_token)) as client:
        resp = await client.get(f"/api/sample/files/{beta_sample_file}/raw")

    assert resp.status_code == 403
    assert RAW_BYTES not in resp.content


@pytest.mark.asyncio
async def test_an_outsider_is_refused(
    outsider_client, sample_file, alpha_item, filestore
):
    filestore("orbi_raw")

    resp = await outsider_client.get(f"/api/sample/files/{sample_file}/raw")

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_an_unknown_file_is_refused_like_one_out_of_reach(
    guest_client, filestore
):
    # As on every sample file route: whether an id exists is not told to a
    # user who could not read it if it did.
    resp = await guest_client.get(f"/api/sample/files/{gen_id()}/raw")

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_an_acquisitions_member_downloads_a_file_no_sample_uses(
    acq_guest_client, orphan_sample_file, filestore
):
    filestore("orbi_raw")

    resp = await acq_guest_client.get(f"/api/sample/files/{orphan_sample_file}/raw")

    assert resp.status_code == 200, resp.text
    assert resp.content == RAW_BYTES


@pytest.mark.asyncio
async def test_anonymous_callers_are_challenged(sample_file, filestore):
    async with _client() as client:
        resp = await client.get(f"/api/sample/files/{sample_file}/raw")

    assert resp.status_code == 401


def test_the_route_is_declared_token_access():
    """Without the flag the auth layer refuses every bearer token before the
    handler runs - invisible from the browser, which signs in with a cookie."""
    assert sample_files_routes.get_sample_file_raw_route.token_access is True


# ============= The rate limit =============


@pytest.mark.asyncio
async def test_an_account_past_its_downloads_is_told_to_wait(
    sdk_token, sample_file, alpha_item, filestore, monkeypatch
):
    filestore("orbi_raw")
    monkeypatch.setattr(sample_files_routes, "RAW_DOWNLOADS_PER_WINDOW", 2)

    async with _client(_token_headers(sdk_token)) as client:
        answers = [
            await client.get(f"/api/sample/files/{sample_file}/raw") for _ in range(3)
        ]

    assert [resp.status_code for resp in answers] == [200, 200, 429]
    assert RAW_BYTES not in answers[-1].content


@pytest.mark.asyncio
async def test_the_limit_is_counted_per_account(
    guest_client, admin_client, sample_file, alpha_item, filestore, monkeypatch
):
    filestore("orbi_raw")
    monkeypatch.setattr(sample_files_routes, "RAW_DOWNLOADS_PER_WINDOW", 1)

    first = await guest_client.get(f"/api/sample/files/{sample_file}/raw")
    other = await admin_client.get(f"/api/sample/files/{sample_file}/raw")
    again = await guest_client.get(f"/api/sample/files/{sample_file}/raw")

    assert (first.status_code, other.status_code, again.status_code) == (
        200,
        200,
        429,
    )


@pytest.mark.asyncio
async def test_the_limit_has_a_scope_of_its_own(
    guest_client, sample_file, alpha_item, filestore, limiter
):
    filestore("orbi_raw")

    await guest_client.get(f"/api/sample/files/{sample_file}/raw")

    [key] = limiter.values
    assert key.startswith("mascope:ratelimit:sample-file-raw:user:")
    assert limiter.values[key] == 1
