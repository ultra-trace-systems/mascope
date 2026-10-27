"""
Tests: the axis fingerprint of a sample file and of a sample,
``GET /api/sample/files/{id}/axis_fingerprint`` and
``GET /api/samples/{id}/axis_fingerprint``.

Both are read by API clients through a token, so they go through the route:
the permission check runs on real rows and the body is encoded the way a
client receives it. The document is the one the converter stores, read from
a scripted ``.props``; the sample's scans are read from the committed KORBI
raw file (two positive MS1 scans, one stream), and the document is that
file's own, computed here as the converter computes it. Every document served
is checked against the schema (the request's acceptance test T2).

Fixtures used:
- ``make_item`` - a sample file of its own, and a sample of it in ws_alpha
  (every user is a member), optionally reading a stream row or a composite
- ``beta_sample_file`` / ``beta_item`` - in ws_beta, only the owner is member
"""

import copy
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

import mascope_file.io as m_io
import mascope_file.name as m_name
import mascope_thermo.thermo as m_thermo
from mascope_backend.api.new.axis_fingerprint import routes, service
from mascope_backend.app.fast import fast
from mascope_backend.db import (
    AccessToken,
    AcquisitionStream,
    SampleFile,
    SampleItem,
    hash_access_token,
)
from mascope_backend.db.id import gen_id
from mascope_thermo.axis_fingerprint import (
    LOCK_KEYS,
    compute_axis_fingerprint,
    reference,
    validate_document,
)
from mascope_thermo.backend import OpenTFRawBackend


_REPO_ROOT = Path(__file__).resolve().parents[6]
KORBI_POS = (
    _REPO_ROOT / "libraries/thermo/tests/test_files/KORBI2_AMB_POS_20260109174345.raw"
)

_NOW = datetime.now(timezone.utc)

FILE_ROUTE = "/api/sample/files/{}/axis_fingerprint"
ITEM_ROUTE = "/api/samples/{}/axis_fingerprint"

NO_LOCK = {
    "used_share": {},
    "used_count": {},
    "m_lock_eff": None,
    "s_lock_eff_ppm": None,
}

#: A calibration that gives the committed file's stream a factor of its own.
FILE_FACTOR = 1.000002
STREAM_FACTOR = 1.000003


@pytest.fixture(scope="module")
def document():
    """The committed file's fingerprint, as the converter stores it."""
    with OpenTFRawBackend(str(KORBI_POS)) as backend:
        computed = compute_axis_fingerprint(backend, reader_name="opentfraw")
    assert computed["status"] in service.DESCRIBED
    return computed


@pytest.fixture(scope="module")
def stream_key(document):
    """The key of the committed file's one stream."""
    (mode,) = document["modes"]
    (key,) = mode["stream_keys"]
    return key


@pytest.fixture(scope="module")
def scan_times():
    """The start time [s] of each scan of the committed file."""
    with OpenTFRawBackend(str(KORBI_POS)) as backend:
        return [float(t) for t in backend.scan_times(ms_type=None)]


def calibration(stream_key):
    """A calibration record that names the file's stream."""
    return {
        "mode": "one-point",
        "par": {"calibration_factor": FILE_FACTOR},
        "streams": {stream_key: {"calibration_factor": STREAM_FACTOR}},
    }


@pytest.fixture
def props(monkeypatch, document):
    """The ``.props`` every sample file reads, for a test to change; and every
    sample file resolved to the committed raw file, whose reads are counted
    in ``props["opened"]``."""
    stored = {"axis_fingerprint": document, "mz_calibration": None}
    opened = []
    real_open = m_thermo.open_backend

    def read_props(_filename):
        return copy.deepcopy(stored)

    def open_backend(path):
        opened.append(path)
        return real_open(path)

    monkeypatch.delenv("MASCOPE_THERMO_BACKEND", raising=False)
    monkeypatch.setattr(m_io, "read_props", read_props)
    monkeypatch.setattr(m_thermo, "open_backend", open_backend)
    monkeypatch.setattr(m_name, "get_sample_file_type", lambda _filename: "orbi_raw")
    monkeypatch.setattr(
        m_name, "filename_to_datafile_path", lambda _filename: str(KORBI_POS)
    )
    return SimpleNamespace(stored=stored, opened=opened)


@pytest_asyncio.fixture
async def make_item(async_session_factory, alpha_batch, stream_key):
    """Make a sample file and a sample of it in ws_alpha.

    ``reads``: None for a sample over its whole polarity, ``"stream"`` for one
    reading the committed file's stream, ``"composite"`` for one reading a
    composite the stream is the one owner of, or of the owners ``runs`` names.
    """
    files = []

    async def make(
        *,
        polarity="+",
        t0=0.0,
        t1=60.0,
        reads=None,
        instrument_type="orbi",
        runs=None,
    ):
        runs = runs or [[40.0, 500.0, stream_key]]
        file_id, item_id = gen_id(), gen_id()
        async with async_session_factory() as session:
            session.add(
                SampleFile(
                    sample_file_id=file_id,
                    filename=f"test-orbion_{file_id}.raw",
                    instrument="test-orbion",
                    instrument_type=instrument_type,
                    datetime=_NOW.replace(tzinfo=None),
                    datetime_utc=_NOW,
                    length=60.0,
                    range={"min": 0, "max": 500},
                    polarity="+",
                )
            )
            await session.flush()
            stream_id = None
            if reads is not None:
                stream_id = gen_id()
                composite = reads == "composite"
                if composite:
                    session.add(
                        AcquisitionStream(
                            stream_id=stream_id,
                            sample_file_id=file_id,
                            stream_key="composite +",
                            signature={"polarity": "+", "composite": True},
                            stitch={
                                "rule": 1,
                                "runs": runs,
                                "source": "rule",
                                "notes": [],
                            },
                        )
                    )
                    await session.flush()
                owners = dict.fromkeys(owner for *_edges, owner in runs)
                for key in owners if composite else [stream_key]:
                    session.add(
                        AcquisitionStream(
                            stream_id=gen_id() if composite else stream_id,
                            sample_file_id=file_id,
                            stream_key=key,
                            signature_key=key,
                            signature={"polarity": "+"},
                            scan_count=2,
                            blocks=1,
                            t_first=0.0,
                            t_last=60.0,
                            composite_stream_id=stream_id if composite else None,
                        )
                    )
                await session.flush()
            session.add(
                SampleItem(
                    sample_item_id=item_id,
                    sample_batch_id=alpha_batch,
                    sample_file_id=file_id,
                    sample_item_name="Fingerprint Item",
                    sample_item_type="ANALYSIS",
                    sample_item_attributes={},
                    polarity=polarity,
                    tic=1000.0,
                    t0=t0,
                    t1=t1,
                    stream_id=stream_id,
                    sample_item_utc_created=_NOW,
                )
            )
            await session.commit()
        files.append(file_id)
        return SimpleNamespace(item=item_id, file=file_id)

    yield make
    async with async_session_factory() as session:
        for file_id in files:
            await session.execute(
                delete(SampleItem).where(SampleItem.sample_file_id == file_id)
            )
            await session.execute(
                delete(AcquisitionStream)
                .where(AcquisitionStream.sample_file_id == file_id)
                .where(AcquisitionStream.composite_stream_id.is_not(None))
            )
            await session.execute(
                delete(SampleFile).where(SampleFile.sample_file_id == file_id)
            )
        await session.commit()


@pytest_asyncio.fixture
async def sdk_token(async_session_factory, test_users):
    """A real API token of the guest user, issued to the SDK's service name."""
    token = f"fingerprint-{gen_id()}".ljust(43, "x")
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


def served(resp) -> dict:
    """The document of a successful response, checked against the schema."""
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"message", "data"}
    validate_document(body["data"])
    return body["data"]


# ============= The sample file's document =============


@pytest.mark.asyncio
async def test_a_file_is_served_its_stored_document(
    guest_client, make_item, props, document
):
    made = await make_item()

    data = served(await guest_client.get(FILE_ROUTE.format(made.file)))

    assert data == {**document, "sample_file_id": made.file}
    # Nothing is read from the raw file for it
    assert props.opened == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stored",
    [
        reference.minimal_document("unavailable"),
        reference.minimal_document("error", "ValueError: a profile without m/z"),
    ],
    ids=["unavailable", "error"],
)
async def test_a_file_is_served_its_document_whatever_its_status(
    guest_client, make_item, props, stored
):
    made = await make_item()
    props.stored["axis_fingerprint"] = stored

    data = served(await guest_client.get(FILE_ROUTE.format(made.file)))

    assert data == {**stored, "sample_file_id": made.file}


@pytest.mark.asyncio
@pytest.mark.parametrize("stored", [None, {}], ids=["no-key", "empty"])
async def test_a_file_without_a_document_is_missing(
    guest_client, make_item, props, stored
):
    made = await make_item()
    if stored is None:
        del props.stored["axis_fingerprint"]
    else:
        props.stored["axis_fingerprint"] = stored

    data = served(await guest_client.get(FILE_ROUTE.format(made.file)))

    assert data == {
        "schema": "mascope.axis_fingerprint",
        "version": 1,
        "status": "missing",
        "sample_file_id": made.file,
    }


@pytest.mark.asyncio
async def test_a_file_that_is_not_an_orbitrap_file_is_not_found(
    guest_client, make_item, props
):
    made = await make_item(instrument_type="tof")

    resp = await guest_client.get(FILE_ROUTE.format(made.file))

    assert resp.status_code == 404, resp.text
    assert "not an Orbitrap file" in resp.json()["error"]


# ============= The sample's document =============


@pytest.mark.asyncio
async def test_a_sample_over_every_scan_has_the_modes_lock(
    guest_client, make_item, props, document
):
    made = await make_item()

    data = served(await guest_client.get(ITEM_ROUTE.format(made.item)))

    (mode,) = document["modes"]
    item = data.pop("item")
    assert data == {**document, "sample_file_id": made.file}
    assert item == {
        "sample_item_id": made.item,
        "sample_file_id": made.file,
        "polarity": "+",
        "t0_s": 0.0,
        "t1_s": 60.0,
        "stream_key": None,
        "scans": [1, 2],
        "mode_id": mode["mode_id"],
        "lock": {key: mode["lock"][key] for key in LOCK_KEYS},
        "mz_calibration_factor": None,
        "file_mz_calibration_factor": None,
        # Its streams are read pooled, on the file's factor
        "streams": None,
    }
    assert props.opened == [str(KORBI_POS)]


@pytest.mark.asyncio
async def test_a_window_takes_the_scans_starting_in_it(
    guest_client, make_item, props, scan_times
):
    # Both ends included: a window from the second scan's start to its own
    made = await make_item(t0=scan_times[1], t1=scan_times[1])

    item = served(await guest_client.get(ITEM_ROUTE.format(made.item)))["item"]

    assert item["scans"] == [2]
    assert item["t0_s"] == item["t1_s"] == scan_times[1]
    assert item["lock"]["used_count"] == {"none": 1}


@pytest.mark.asyncio
async def test_a_sample_of_a_polarity_the_file_lacks_reads_no_scan(
    guest_client, make_item, props, document
):
    made = await make_item(polarity="-")

    data = served(await guest_client.get(ITEM_ROUTE.format(made.item)))

    item = data["item"]
    assert item["polarity"] == "-"
    assert item["scans"] == []
    assert item["mode_id"] is None
    assert item["lock"] == NO_LOCK


@pytest.mark.asyncio
async def test_a_sample_of_a_stream_reads_its_stream_and_its_factor(
    guest_client, make_item, props, stream_key
):
    made = await make_item(reads="stream")
    props.stored["mz_calibration"] = calibration(stream_key)

    item = served(await guest_client.get(ITEM_ROUTE.format(made.item)))["item"]

    assert item["stream_key"] == stream_key
    assert item["scans"] == [1, 2]
    assert item["mz_calibration_factor"] == STREAM_FACTOR
    assert item["file_mz_calibration_factor"] == FILE_FACTOR
    assert item["streams"] == [
        {
            "stream_key": stream_key,
            "scans": [1, 2],
            "mz_calibration_factor": STREAM_FACTOR,
        }
    ]


@pytest.mark.asyncio
async def test_a_stitched_sample_reads_the_streams_that_own_it(
    guest_client, make_item, props, stream_key
):
    made = await make_item(reads="composite")
    props.stored["mz_calibration"] = calibration(stream_key)

    item = served(await guest_client.get(ITEM_ROUTE.format(made.item)))["item"]

    assert item["stream_key"] == "composite +"
    assert item["scans"] == [1, 2]
    # The calibration names the composite's segment, not the composite
    assert item["mz_calibration_factor"] == FILE_FACTOR
    assert item["file_mz_calibration_factor"] == FILE_FACTOR
    # while the segment's peaks carry its own
    assert item["streams"] == [
        {
            "stream_key": stream_key,
            "scans": [1, 2],
            "mz_calibration_factor": STREAM_FACTOR,
        }
    ]


class StreamScans:
    """A reader that answers each stream's selection with the scans given
    for it, out of order, and raises as the reader does for a stream none
    of whose scans is selected."""

    def __init__(self, scans: dict):
        self.scans = scans

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def scan_indices(self, *args, stream=None, **kwargs):
        if not self.scans.get(stream):
            raise m_thermo.NoScansFoundError(f"no scans of {stream}")
        return self.scans[stream]


@pytest.mark.asyncio
async def test_a_stitched_sample_says_each_owners_scans_and_factor(
    guest_client, make_item, props, monkeypatch
):
    # Two owners, the one the record names first in m/z and again above the
    # other: a stream is listed once, where the map first names it
    runs = [[40.0, 100.0, "stream A"], [100.0, 200.0, "stream B"]]
    runs.append([200.0, 500.0, "stream A"])
    monkeypatch.setattr(
        m_thermo,
        "open_backend",
        lambda _path: StreamScans({"stream A": [7, 1, 5], "stream B": [6, 2]}),
    )
    made = await make_item(reads="composite", runs=runs)
    props.stored["mz_calibration"] = calibration("stream A")

    item = served(await guest_client.get(ITEM_ROUTE.format(made.item)))["item"]

    assert item["scans"] == [1, 2, 5, 6, 7]
    assert item["mz_calibration_factor"] == FILE_FACTOR
    # The stream the record does not name goes by the file's factor
    assert item["streams"] == [
        {
            "stream_key": "stream A",
            "scans": [1, 5, 7],
            "mz_calibration_factor": STREAM_FACTOR,
        },
        {
            "stream_key": "stream B",
            "scans": [2, 6],
            "mz_calibration_factor": FILE_FACTOR,
        },
    ]


@pytest.mark.asyncio
async def test_an_owner_with_no_scans_in_the_window_is_listed_without_any(
    guest_client, make_item, props, monkeypatch
):
    runs = [[40.0, 100.0, "stream A"], [100.0, 500.0, "stream B"]]
    monkeypatch.setattr(
        m_thermo, "open_backend", lambda _path: StreamScans({"stream B": [4, 3]})
    )
    made = await make_item(reads="composite", runs=runs)
    props.stored["mz_calibration"] = calibration("stream A")

    item = served(await guest_client.get(ITEM_ROUTE.format(made.item)))["item"]

    assert item["scans"] == [3, 4]
    # It still owns its m/z, and its factor is still the one it carries
    assert item["streams"] == [
        {"stream_key": "stream A", "scans": [], "mz_calibration_factor": STREAM_FACTOR},
        {
            "stream_key": "stream B",
            "scans": [3, 4],
            "mz_calibration_factor": FILE_FACTOR,
        },
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("reads", ["stream", "composite"])
async def test_the_streams_of_a_file_never_calibrated_have_no_factor(
    guest_client, make_item, props, stream_key, reads
):
    made = await make_item(reads=reads)

    item = served(await guest_client.get(ITEM_ROUTE.format(made.item)))["item"]

    assert item["mz_calibration_factor"] is None
    assert item["streams"] == [
        {"stream_key": stream_key, "scans": [1, 2], "mz_calibration_factor": None}
    ]


@pytest.mark.asyncio
async def test_a_sample_over_its_polarity_goes_by_the_files_factor(
    guest_client, make_item, props, stream_key
):
    made = await make_item()
    props.stored["mz_calibration"] = calibration(stream_key)

    item = served(await guest_client.get(ITEM_ROUTE.format(made.item)))["item"]

    assert item["mz_calibration_factor"] == FILE_FACTOR
    assert item["file_mz_calibration_factor"] == FILE_FACTOR
    assert item["streams"] is None


def _other_mode(mode: dict, polarity: str, resolution: int, streams: list) -> dict:
    """The committed file's mode under another polarity, resolution and
    streams: a mode the file does not have, which no scan of it is in."""
    return {
        **copy.deepcopy(mode),
        "mode_id": reference.mode_id(polarity, resolution, *mode["scan_range"]),
        "polarity": polarity,
        "resolution": resolution,
        "stream_keys": streams,
    }


@pytest.mark.asyncio
async def test_the_modes_of_the_samples_polarity_and_stream_come_first(
    guest_client, make_item, props, document, stream_key
):
    (own,) = document["modes"]
    other_polarity = _other_mode(own, "-", 120000, [stream_key])
    other_stream = _other_mode(own, "+", 60000, ["another stream"])
    same = _other_mode(own, "+", 30000, [stream_key])
    props.stored["axis_fingerprint"] = {
        **document,
        "modes": [other_polarity, other_stream, same, own],
        "dominant_mode": other_polarity["mode_id"],
    }
    made = await make_item(reads="stream")

    data = served(await guest_client.get(ITEM_ROUTE.format(made.item)))

    assert [mode["mode_id"] for mode in data["modes"]] == [
        own["mode_id"],
        same["mode_id"],
        other_polarity["mode_id"],
        other_stream["mode_id"],
    ]
    assert data["item"]["mode_id"] == own["mode_id"]
    # The file's own word on its modes stays
    assert data["dominant_mode"] == other_polarity["mode_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stored",
    [
        None,
        reference.minimal_document("unavailable"),
        reference.minimal_document("error", "ValueError: a profile without m/z"),
    ],
    ids=["missing", "unavailable", "error"],
)
async def test_a_document_of_no_scans_is_served_without_an_item(
    guest_client, make_item, props, stored
):
    made = await make_item()
    props.stored["axis_fingerprint"] = stored

    data = served(await guest_client.get(ITEM_ROUTE.format(made.item)))

    expected = stored or reference.minimal_document("missing")
    assert data == {**expected, "sample_file_id": made.file}
    assert props.opened == []


@pytest.mark.asyncio
async def test_a_sample_whose_raw_file_is_not_kept_is_refused(
    guest_client, make_item, props, monkeypatch
):
    made = await make_item()
    monkeypatch.setattr(m_name, "get_sample_file_type", lambda _filename: "orbi_zarr")

    resp = await guest_client.get(ITEM_ROUTE.format(made.item))

    assert resp.status_code == 422, resp.text
    assert props.opened == []


@pytest.mark.asyncio
async def test_a_raw_file_that_cannot_be_read_is_a_server_error_without_its_path(
    guest_client, make_item, props, monkeypatch
):
    made = await make_item()

    def unreadable(path):
        raise FileNotFoundError(f"Could not open raw file: /private-filestore/{path}")

    monkeypatch.setattr(m_thermo, "open_backend", unreadable)

    resp = await guest_client.get(ITEM_ROUTE.format(made.item))

    assert resp.status_code == 500
    assert "Could not read the scans" in resp.json()["error"]
    assert "private-filestore" not in resp.text


@pytest.mark.asyncio
async def test_a_sample_whose_file_is_not_an_orbitrap_file_is_not_found(
    guest_client, make_item, props
):
    made = await make_item(instrument_type="tof")

    resp = await guest_client.get(ITEM_ROUTE.format(made.item))

    assert resp.status_code == 404, resp.text
    assert "not an Orbitrap file" in resp.json()["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reads", "streams"),
    [(None, [None]), ("stream", ["key"]), ("composite", ["key"])],
    ids=["polarity", "stream", "composite"],
)
async def test_the_scans_are_selected_as_the_samples_reads_select_them(
    guest_client, make_item, props, monkeypatch, stream_key, reads, streams
):
    asked = []

    class Backend:
        """Records each selection, and answers scans out of order: a set of
        these two iterates them as given."""

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def scan_indices(self, *args, **kwargs):
            asked.append((args, kwargs))
            return [10, 3]

    monkeypatch.setattr(m_thermo, "open_backend", lambda _path: Backend())
    made = await make_item(polarity="+", t0=1.5, t1=42.0, reads=reads)

    item = served(await guest_client.get(ITEM_ROUTE.format(made.item)))["item"]

    # MS1 scans of its polarity and window, of each stream apart
    streams = [stream_key if key == "key" else key for key in streams]
    assert asked == [
        (("+", 1.5, 42.0), {"ms_type": "Ms", "stream": stream}) for stream in streams
    ]
    # In ascending order
    assert item["scans"] == [3, 10]


def test_a_sample_reads_its_stream_or_the_owners_of_its_composite():
    assert service.read_streams(None, None) == [None]
    assert service.read_streams("stream A", None) == ["stream A"]
    # The owners in the map's order, each once, and not the composite itself
    stitch = {
        "runs": [[40.0, 100.0, "stream B"], [100.0, 200.0, "stream A"]]
        + [[200.0, 300.0, "stream B"]]
    }
    assert service.read_streams("composite +", stitch) == ["stream B", "stream A"]


# ============= Size and time =============


@pytest.mark.asyncio
@pytest.mark.parametrize("route", [FILE_ROUTE, ITEM_ROUTE], ids=["file", "sample"])
async def test_a_stored_document_answers_small_and_quick(
    guest_client, make_item, props, route
):
    made = await make_item()
    target = made.file if route == FILE_ROUTE else made.item

    started = time.perf_counter()
    resp = await guest_client.get(route.format(target))
    elapsed = time.perf_counter() - started

    served(resp)
    assert len(resp.content) < 64 * 1024
    assert elapsed < 1.0


# ============= Who may read them =============


@pytest.mark.asyncio
@pytest.mark.parametrize("route", [FILE_ROUTE, ITEM_ROUTE], ids=["file", "sample"])
async def test_an_api_token_reads_them(sdk_token, make_item, props, route):
    """What the SDK sends: a bearer token and its service name."""
    made = await make_item()
    target = made.file if route == FILE_ROUTE else made.item
    headers = {"Authorization": f"Bearer {sdk_token}", "X-Service-Name": "mascope_sdk"}
    async with _client(headers) as client:
        data = served(await client.get(route.format(target)))

    assert data["sample_file_id"] == made.file


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route", "target"),
    [(FILE_ROUTE, "beta_sample_file"), (ITEM_ROUTE, "beta_item")],
    ids=["file", "sample"],
)
async def test_an_api_token_is_held_to_its_users_access(
    sdk_token, beta_sample_file, beta_item, props, route, target
):
    target = {"beta_sample_file": beta_sample_file, "beta_item": beta_item}[target]
    headers = {"Authorization": f"Bearer {sdk_token}", "X-Service-Name": "mascope_sdk"}
    async with _client(headers) as client:
        resp = await client.get(route.format(target))

    assert resp.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route", "target"),
    [(FILE_ROUTE, "beta_sample_file"), (ITEM_ROUTE, "beta_item")],
    ids=["file", "sample"],
)
async def test_a_member_of_another_workspace_is_refused(
    guest_client, beta_sample_file, beta_item, props, route, target
):
    target = {"beta_sample_file": beta_sample_file, "beta_item": beta_item}[target]

    resp = await guest_client.get(route.format(target))

    assert resp.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("route", [FILE_ROUTE, ITEM_ROUTE], ids=["file", "sample"])
async def test_an_outsider_is_refused(outsider_client, make_item, props, route):
    made = await make_item()
    target = made.file if route == FILE_ROUTE else made.item

    resp = await outsider_client.get(route.format(target))

    assert resp.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("route", [FILE_ROUTE, ITEM_ROUTE], ids=["file", "sample"])
async def test_an_unknown_id_is_refused_like_one_out_of_reach(guest_client, route):
    # As on /metadata: whether an id exists is not told to a user who could
    # not read it if it did.
    resp = await guest_client.get(route.format(gen_id()))

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_an_unknown_file_is_not_found_for_an_admin(admin_client, props):
    resp = await admin_client.get(FILE_ROUTE.format(gen_id()))

    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_an_unknown_sample_is_not_found_for_a_superuser(owner_client, props):
    # Every sample is read by a superuser alone, as on every route of one
    # sample; a global admin is refused one outside their workspaces.
    resp = await owner_client.get(ITEM_ROUTE.format(gen_id()))

    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize("route", [FILE_ROUTE, ITEM_ROUTE], ids=["file", "sample"])
async def test_anonymous_callers_are_challenged(make_item, route):
    made = await make_item()
    target = made.file if route == FILE_ROUTE else made.item
    async with _client() as client:
        resp = await client.get(route.format(target))

    assert resp.status_code == 401


def test_the_routes_are_declared_token_access():
    """Without the flag the auth layer refuses every bearer token before the
    handler runs - invisible from the browser, which signs in with a cookie."""
    assert routes.get_sample_file_axis_fingerprint_route.token_access is True
    assert routes.get_sample_axis_fingerprint_route.token_access is True


@pytest.mark.parametrize(
    "path",
    [
        "/api/sample/files/{sample_file_id}/axis_fingerprint",
        "/api/samples/{sample_item_id}/axis_fingerprint",
    ],
)
def test_the_openapi_document_marks_them_with_the_token_scheme(path):
    operation = fast.openapi()["paths"][path]["get"]

    assert {"APIToken": [], "ServiceName": []} in operation["security"]
