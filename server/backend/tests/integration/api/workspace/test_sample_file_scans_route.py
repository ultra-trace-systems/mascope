"""
Tests: a sample file's scan table, ``GET /api/sample/files/{id}/scans``, and
``GET /api/sample/files/{id}/metadata`` beside it.

Both are read by API clients, which index into the response by the names the
instrument gave its trailer, so they go through the route here: the query
string is parsed, the permission check runs and the body is encoded the way a
client receives it. The read is real, too. Every sample file of this module
resolves to the committed KORBI raw file (two MS1 scans), so the rows are the
reader's own and ``/metadata``'s streamed body is compared with the document
it used to send whole.

Fixtures used:
- ``sample_file`` - linked to ``alpha_item`` (ws_alpha, all users are members)
- ``beta_sample_file`` - linked to ``beta_item`` (ws_beta, only owner is member)
- ``orphan_sample_file`` - no sample item references
"""

import asyncio
import json
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from test_utils import captured_logs

import mascope_file.name as m_name
import mascope_thermo.thermo as m_thermo
from mascope_backend.api.controllers.sample.files import sample_files_controller
from mascope_backend.api.routes.sample.files import sample_files_routes
from mascope_backend.app.fast import fast
from mascope_backend.db import AccessToken
from mascope_backend.db.id import gen_id
from mascope_thermo.backend import OpenTFRawBackend


_REPO_ROOT = Path(__file__).resolve().parents[6]
KORBI_POS = (
    _REPO_ROOT / "libraries/thermo/tests/test_files/KORBI2_AMB_POS_20260109174345.raw"
)

#: The scans of the committed file.
SCANS = ["1", "2"]


@pytest.fixture(autouse=True)
def raw_file(monkeypatch):
    """Resolve every sample file to the committed raw file.

    The records these tests read are rows without a filestore behind them.
    Both routes find the data file through ``mascope_file.name``, so that is
    where the committed file is put in its place. The controller asks that
    module what a file is under its own name too, before it reads anything.
    """
    monkeypatch.delenv("MASCOPE_THERMO_BACKEND", raising=False)
    monkeypatch.setattr(m_name, "get_sample_file_type", lambda _filename: "orbi_raw")
    monkeypatch.setattr(
        sample_files_controller, "get_sample_file_type", lambda _filename: "orbi_raw"
    )
    monkeypatch.setattr(
        m_name, "filename_to_datafile_path", lambda _filename: str(KORBI_POS)
    )


@pytest_asyncio.fixture
async def sdk_token(async_session_factory, test_users):
    """A real API token of the guest user, issued to the SDK's service name."""
    token = f"scans-{gen_id()}".ljust(43, "x")
    async with async_session_factory() as session:
        session.add(
            AccessToken(
                token=token,
                user_id=test_users["guest"].id,
                service_name="mascope_sdk",
            )
        )
        await session.commit()
    yield token
    async with async_session_factory() as session:
        await session.execute(delete(AccessToken).where(AccessToken.token == token))
        await session.commit()


def _client(headers: dict | None = None) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=fast), base_url="http://test", headers=headers
    )


def _as_json(value):
    """``value`` as a client reads it back: int keys are text, tuples lists."""
    return json.loads(json.dumps(value, allow_nan=False))


def _document(include_centroids: bool = True) -> dict:
    """The metadata document of the committed file, read without the route."""
    return _as_json(
        m_thermo.RawFileMetadataLegacy(str(KORBI_POS)).to_dict(
            include_centroids=include_centroids
        )
    )


# ============= The scan table =============


@pytest.mark.asyncio
async def test_the_table_holds_every_scan_and_no_centroid(
    guest_client, sample_file, alpha_item
):
    resp = await guest_client.get(f"/api/sample/files/{sample_file}/scans")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["results"] == 2
    data = body["data"]
    assert set(data) == {"num_of_scans", "stats_per_scan", "stats_per_file"}
    assert data["num_of_scans"] == 2
    assert list(data["stats_per_scan"]) == SCANS
    for scan, row in data["stats_per_scan"].items():
        # A row carries its scan number, its statistics and its trailer under
        # the instrument's own labels.
        assert row["ScanNumber"] == int(scan)
        assert row["MsType"] == "Ms"
        assert {"TIC", "StartTime", "ScanType", "FT Resolution:"} <= set(row)
    assert data["stats_per_file"]["Value"]["Model"]


@pytest.mark.asyncio
async def test_the_rows_are_the_ones_metadata_serves(
    guest_client, sample_file, alpha_item
):
    scans = await guest_client.get(f"/api/sample/files/{sample_file}/scans")
    metadata = await guest_client.get(
        f"/api/sample/files/{sample_file}/metadata?include_centroids=false"
    )

    assert scans.status_code == metadata.status_code == 200
    assert scans.json()["data"] == metadata.json()["data"]


@pytest.mark.asyncio
async def test_fields_keep_the_named_entries_and_the_scan_number(
    guest_client, sample_file, alpha_item
):
    resp = await guest_client.get(
        f"/api/sample/files/{sample_file}/scans",
        params={"fields": ["FT Resolution:", "ScanType", "No Such Label:"]},
    )

    assert resp.status_code == 200, resp.text
    full = _document(include_centroids=False)["stats_per_scan"]
    assert list(resp.json()["data"]["stats_per_scan"]) == SCANS
    for scan, row in resp.json()["data"]["stats_per_scan"].items():
        # In the order asked for, behind the scan number; a label the file
        # does not use is left out rather than refused.
        assert list(row) == ["ScanNumber", "FT Resolution:", "ScanType"]
        assert row == {name: full[scan][name] for name in row}
    # The file's own statistics are not per-scan entries, and stay whole.
    assert resp.json()["data"]["stats_per_file"]["Value"]["Model"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "scans"),
    [
        pytest.param("first_scan=2", ["2"], id="from-a-scan"),
        pytest.param("last_scan=1", ["1"], id="up-to-a-scan"),
        pytest.param("first_scan=1&last_scan=1", ["1"], id="one-scan"),
        pytest.param("first_scan=3", [], id="past-the-file"),
        pytest.param("t_min=0&t_max=100000", SCANS, id="every-time"),
        pytest.param("t_min=100000", [], id="after-the-run"),
        # Both scans start after 0 s; a scan recorded at 0 would be found.
        pytest.param("t_max=0", [], id="at-time-zero"),
        # One instant is a range: asked for, and answered, not refused.
        pytest.param("t_min=0&t_max=0", [], id="the-instant-zero"),
        pytest.param("polarity=%2B", SCANS, id="polarity"),
        # A client that does not encode the plus sends a space.
        pytest.param("polarity=+", SCANS, id="polarity-unencoded"),
        pytest.param("polarity=-", [], id="other-polarity"),
    ],
)
async def test_a_window_narrows_the_scans(
    guest_client, sample_file, alpha_item, query, scans
):
    resp = await guest_client.get(f"/api/sample/files/{sample_file}/scans?{query}")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert list(body["data"]["stats_per_scan"]) == scans
    assert body["results"] == len(scans)
    # The count is the file's, whatever was asked for.
    assert body["data"]["num_of_scans"] == 2


@pytest.mark.asyncio
async def test_a_time_window_takes_the_scan_that_starts_on_its_bound(
    guest_client, sample_file, alpha_item
):
    full = _document(include_centroids=False)["stats_per_scan"]
    # StartTime is in minutes, as the instrument reports it; the window is in
    # seconds, as every time range of the API is.
    second_scan_s = full["2"]["StartTime"] * 60

    resp = await guest_client.get(
        f"/api/sample/files/{sample_file}/scans", params={"t_min": second_scan_s}
    )

    assert resp.status_code == 200, resp.text
    assert list(resp.json()["data"]["stats_per_scan"]) == ["2"]


@pytest.mark.asyncio
@pytest.mark.parametrize("scan", SCANS)
async def test_a_time_window_of_one_instant_takes_the_scan_that_starts_then(
    guest_client, sample_file, alpha_item, scan
):
    # Both ends of a range are included, so the same time at both ends is the
    # way to ask for the scan that starts exactly then - a scan recorded at
    # 0 s among them, which no other time window singles out.
    full = _document(include_centroids=False)["stats_per_scan"]
    start_s = full[scan]["StartTime"] * 60

    resp = await guest_client.get(
        f"/api/sample/files/{sample_file}/scans",
        params={"t_min": start_s, "t_max": start_s},
    )

    assert resp.status_code == 200, resp.text
    assert list(resp.json()["data"]["stats_per_scan"]) == [scan]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        "first_scan=0",
        "first_scan=2&last_scan=1",
        "t_min=10&t_max=5",
        "t_min=-1",
        "polarity=positive",
        "first_scan=one",
    ],
)
async def test_a_window_that_cannot_be_is_refused(
    guest_client, sample_file, alpha_item, query
):
    resp = await guest_client.get(f"/api/sample/files/{sample_file}/scans?{query}")

    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize("sample_type", ["tof_h5", "tof_zarr", "orbi_zarr"])
async def test_a_file_with_no_raw_orbitrap_data_has_no_scan_table(
    guest_client, sample_file, alpha_item, monkeypatch, sample_type
):
    monkeypatch.setattr(m_name, "get_sample_file_type", lambda _filename: sample_type)

    resp = await guest_client.get(f"/api/sample/files/{sample_file}/scans")

    assert resp.status_code == 404, resp.text
    assert "no raw Orbitrap data" in resp.json()["error"]


@pytest.mark.asyncio
async def test_a_file_that_cannot_be_read_is_a_server_error_without_its_path(
    guest_client, sample_file, alpha_item, monkeypatch, tmp_path
):
    missing = tmp_path / "private-filestore" / "data.raw"
    monkeypatch.setattr(
        m_name, "filename_to_datafile_path", lambda _filename: str(missing)
    )

    resp = await guest_client.get(f"/api/sample/files/{sample_file}/scans")

    assert resp.status_code == 500, resp.text
    assert "Could not read the scans" in resp.json()["error"]
    assert "private-filestore" not in resp.text


# ============= Who may read it =============


@pytest.mark.asyncio
async def test_an_outsider_is_refused(outsider_client, sample_file, alpha_item):
    resp = await outsider_client.get(f"/api/sample/files/{sample_file}/scans")

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_a_member_of_another_workspace_is_refused(
    guest_client, beta_sample_file, beta_item
):
    resp = await guest_client.get(f"/api/sample/files/{beta_sample_file}/scans")

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_an_acquisitions_member_reads_a_file_no_sample_uses(
    acq_guest_client, orphan_sample_file
):
    resp = await acq_guest_client.get(f"/api/sample/files/{orphan_sample_file}/scans")

    assert resp.status_code == 200, resp.text
    assert list(resp.json()["data"]["stats_per_scan"]) == SCANS


@pytest.mark.asyncio
async def test_an_unknown_file_is_refused_like_one_out_of_reach(guest_client):
    # As on every sample file route: whether an id exists is not told to a
    # user who could not read it if it did.
    resp = await guest_client.get(f"/api/sample/files/{gen_id()}/scans")

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_an_unknown_file_is_not_found_for_an_admin(admin_client):
    resp = await admin_client.get(f"/api/sample/files/{gen_id()}/scans")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_anonymous_callers_are_challenged(sample_file):
    async with _client() as client:
        resp = await client.get(f"/api/sample/files/{sample_file}/scans")

    assert resp.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["scans", "metadata?include_centroids=false"])
async def test_an_api_token_reads_it(sdk_token, sample_file, alpha_item, route):
    """What the SDK sends: a bearer token and its service name."""
    headers = {"Authorization": f"Bearer {sdk_token}", "X-Service-Name": "mascope_sdk"}
    async with _client(headers) as client:
        resp = await client.get(f"/api/sample/files/{sample_file}/{route}")

    assert resp.status_code == 200, resp.text
    assert list(resp.json()["data"]["stats_per_scan"]) == SCANS


@pytest.mark.asyncio
async def test_an_api_token_is_held_to_its_users_access(
    sdk_token, beta_sample_file, beta_item
):
    headers = {"Authorization": f"Bearer {sdk_token}", "X-Service-Name": "mascope_sdk"}
    async with _client(headers) as client:
        resp = await client.get(f"/api/sample/files/{beta_sample_file}/scans")

    assert resp.status_code == 403


def test_the_route_is_declared_token_access():
    """Without the flag the auth layer refuses every bearer token before the
    handler runs - invisible from the browser, which signs in with a cookie."""
    assert sample_files_routes.get_sample_file_scans_route.token_access is True


# ============= /metadata beside it =============


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["", "?include_centroids=true"])
async def test_metadata_with_centroids_is_the_document_it_always_was(
    guest_client, sample_file, alpha_item, query
):
    resp = await guest_client.get(f"/api/sample/files/{sample_file}/metadata{query}")

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/json"
    body = resp.json()
    assert set(body) == {"message", "data"}
    assert "retrieved successfully" in body["message"]
    assert body["data"] == _document()
    centroids = body["data"]["centroids_meta"]
    assert len(centroids["time"]) == len(centroids["data"]) == 2
    assert any(scan["mzs"] for scan in centroids["data"])


@pytest.mark.asyncio
async def test_metadata_with_centroids_is_written_in_pieces(
    guest_client, sample_file, alpha_item
):
    # A body sent whole says how long it is before its first byte; one written
    # scan by scan cannot.
    streamed = await guest_client.get(f"/api/sample/files/{sample_file}/metadata")
    whole = await guest_client.get(
        f"/api/sample/files/{sample_file}/metadata?include_centroids=false"
    )

    assert "content-length" not in streamed.headers
    assert "content-length" in whole.headers


@pytest.mark.asyncio
async def test_metadata_without_centroids_is_the_rest_of_the_document(
    guest_client, sample_file, alpha_item
):
    resp = await guest_client.get(
        f"/api/sample/files/{sample_file}/metadata?include_centroids=false"
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert "centroids_meta" not in data
    assert data == _document(include_centroids=False)


@pytest.mark.asyncio
async def test_metadata_of_a_file_that_cannot_be_read_is_an_error_status(
    guest_client, sample_file, alpha_item, monkeypatch, tmp_path
):
    # The scan table is read before the first byte is sent, so a file that
    # cannot be read is still answered with a status and a message.
    missing = tmp_path / "private-filestore" / "data.raw"
    monkeypatch.setattr(
        m_name, "filename_to_datafile_path", lambda _filename: str(missing)
    )

    resp = await guest_client.get(f"/api/sample/files/{sample_file}/metadata")

    assert resp.status_code == 500, resp.text
    assert "Could not read metadata" in resp.json()["error"]
    assert "private-filestore" not in resp.text


class _ScanReadError(RuntimeError):
    pass


@pytest.mark.asyncio
async def test_a_read_that_fails_part_way_cuts_the_body_short_and_is_logged(
    guest_client, sample_file, alpha_item, monkeypatch
):
    # Once the first piece has gone out there is no status left to send. The
    # body must then end short, so a client's JSON parser fails instead of
    # reading a document that silently lacks scans - and the server must say
    # why, since no error handler runs after the response has started.
    def one_scan_then_fail(_self):
        yield (
            1,
            0.5,
            {
                "intensities": [1.0],
                "mzs": [100.0],
                "resolutions": [1.0],
                "noises": [1.0],
            },
        )
        raise _ScanReadError("scan 2 could not be decoded")

    monkeypatch.setattr(
        m_thermo.RawFileMetadataLegacy, "iter_centroids_meta", one_scan_then_fail
    )

    with captured_logs("ERROR") as records, pytest.raises(_ScanReadError):
        await guest_client.get(f"/api/sample/files/{sample_file}/metadata")

    [record] = [r for r in records if "cut short" in r["message"]]
    assert record["exception"] is not None
    assert record["exception"].type is _ScanReadError


def _counted_readers(monkeypatch) -> dict:
    """Count the OpenTFRaw readers opened and closed inside the test."""
    readers = {"opened": 0, "closed": 0}
    enter, leave = OpenTFRawBackend.__enter__, OpenTFRawBackend.__exit__

    def counting_enter(self):
        readers["opened"] += 1
        return enter(self)

    def counting_exit(self, *exc):
        readers["closed"] += 1
        return leave(self, *exc)

    monkeypatch.setattr(OpenTFRawBackend, "__enter__", counting_enter)
    monkeypatch.setattr(OpenTFRawBackend, "__exit__", counting_exit)
    return readers


def _metadata_scope(client, path: str, spec_version: str) -> dict:
    """A bare ASGI request for ``path``, signed in as ``client`` is."""
    cookie = "; ".join(f"{k}={v}" for k, v in client.cookies.items())
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": spec_version},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"test"), (b"cookie", cookie.encode())],
        "client": ("127.0.0.1", 1),
        "server": ("test", 80),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "spec_version",
    [
        # The server says the client is gone when the app next listens, and
        # the response is cancelled: the way the reader was left open.
        pytest.param("2.3", id="disconnect-received"),
        # The server fails the app's next send. That closed the reader before
        # the response closed it itself, and is held so that it goes on to.
        pytest.param("2.4", id="send-refused"),
    ],
)
async def test_a_client_that_hangs_up_leaves_no_reader_open(
    guest_client, sample_file, alpha_item, monkeypatch, spec_version
):
    # Starlette stops asking the body for pieces and leaves its generator
    # suspended inside the reader's `with`. Nothing closes such a generator
    # but the response itself, and until it does the raw file stays open.
    readers = _counted_readers(monkeypatch)
    path = f"/api/sample/files/{sample_file}/metadata"
    gone = asyncio.Event()
    bodies = []
    asked = 0

    async def receive():
        nonlocal asked
        asked += 1
        if asked == 1:
            return {"type": "http.request", "body": b"", "more_body": False}
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] != "http.response.body":
            return
        if gone.is_set():
            raise OSError("the client has gone")
        bodies.append(message)
        # The client goes away once the first scan's centroids have arrived.
        if len(bodies) == 2:
            gone.set()

    try:
        await fast(_metadata_scope(guest_client, path, spec_version), receive, send)
    except Exception:
        # How the server is told of a refused send is its own business; the
        # reader is this test's.
        pass

    # It was a hang-up: the document was not sent to its end.
    sent = b"".join(message.get("body", b"") for message in bodies)
    assert sent and not sent.endswith(b"}}}")
    assert readers["opened"] > 0
    assert readers["closed"] == readers["opened"]


@pytest.mark.asyncio
async def test_metadata_sent_to_its_end_leaves_no_reader_open(
    guest_client, sample_file, alpha_item, monkeypatch
):
    readers = _counted_readers(monkeypatch)

    resp = await guest_client.get(f"/api/sample/files/{sample_file}/metadata")

    assert resp.status_code == 200
    assert readers["opened"] > 0
    assert readers["closed"] == readers["opened"]


@pytest.mark.asyncio
async def test_metadata_is_refused_to_an_outsider_before_anything_is_read(
    outsider_client, sample_file, alpha_item, monkeypatch
):
    def no_read(*_args, **_kwargs):
        raise AssertionError("the file was read for a user who may not see it")

    monkeypatch.setattr(m_thermo.RawFileMetadataLegacy, "to_dict", no_read)
    monkeypatch.setattr(m_thermo.RawFileMetadataLegacy, "iter_json", no_read)

    resp = await outsider_client.get(f"/api/sample/files/{sample_file}/metadata")

    assert resp.status_code == 403
