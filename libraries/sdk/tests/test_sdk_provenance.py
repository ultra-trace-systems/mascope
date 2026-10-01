"""
Hermetic tests for the provenance the SDK stamps onto its frames.

``MascopeClient.provenance()`` reads ``GET /api/provenance``, and the
high-level loaders stamp what it returns on ``df.attrs["provenance"]`` - so a
notebook, and whatever is published from it, keeps the deployment and build
its numbers came from. The SDK is on PyPI and talks to servers older than the
route, so a server without it must leave the frame unstamped, never fail the
load - nor hold it back: the stamping request gets a budget of its own. Frames
from one build must carry equal blocks, or ``pd.concat`` drops them. These
mock the HTTP layer and need no running stack.
"""

import copy
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
import requests

from mascope_sdk import MascopeAPIError, MascopeClient, NotFoundError, ServerError
from mascope_sdk.client import _STAMP_TIMEOUT
from mascope_sdk.exceptions import MascopeConnectionError


BLOCK = {
    "provenance_version": 1,
    "generated_utc": "2026-10-01T12:00:00Z",
    "deployment_id": "example-lab",
    "produced_with": {
        "mascope_version": "v1.10.1",
        "match_score_version": 1,
        "peak_assignment_engine_version": "0.5.0",
    },
}

#: Client loader -> the ``_loaders`` function it delegates to, and arguments
#: that satisfy its signature.
LOADERS = {
    "load_peaks": ("load_peaks", ("My Dataset",)),
    "load_peak_timeseries": ("load_peak_timeseries", ("My Dataset",)),
    "load_peaks_by_stage": ("load_peaks_by_stage", ("sample-1", [(0.0, 1.0)])),
    "load_batch_ledger": ("load_batch_ledger", ("My Dataset",)),
    "load_assignments": ("load_assignments", ("My Dataset",)),
}


class _Response:
    def __init__(self, payload=None, text=None):
        self._payload = payload
        self._text = text

    def json(self):
        if self._text is not None:
            # What requests raises for a body that is not JSON
            raise requests.exceptions.JSONDecodeError("Expecting value", self._text, 0)
        return self._payload


class FakeServer:
    """``http_get`` answering the provenance route as the server does.

    Each answer is generated afresh, one second after the last, so two answers
    from one build differ in ``generated_utc`` alone - unless ``body`` replaces
    the answer, or ``error`` is raised instead.
    """

    def __init__(self, error: Exception | None = None, body=None):
        self.error = error
        self.body = body
        self.mascope_version = "v1.10.1"
        self.calls: list[dict] = []

    def http_get(self, url, path, access_token, params=None, **kwargs):
        self.calls.append({"url": url, "path": path, "token": access_token, **kwargs})
        if self.error is not None:
            raise self.error
        if self.body is not None:
            return self.body
        block = copy.deepcopy(BLOCK)
        moment = datetime(2026, 10, 1, 12, tzinfo=timezone.utc) + timedelta(
            seconds=len(self.calls) - 1
        )
        block["generated_utc"] = moment.strftime("%Y-%m-%dT%H:%M:%SZ")
        block["produced_with"]["mascope_version"] = self.mascope_version
        return _Response({"message": "Provenance of this deployment.", "data": block})


@pytest.fixture
def client(monkeypatch):
    """A client whose workspace resolution needs no server."""
    monkeypatch.setattr(
        MascopeClient, "_resolve_workspace", lambda self, workspace: ("ws-1", "WS")
    )
    return MascopeClient(url="http://testserver/", access_token="token")


def _serve(monkeypatch, server: FakeServer) -> FakeServer:
    monkeypatch.setattr("mascope_sdk._http.http_get", server.http_get)
    return server


def _loader_returns(monkeypatch, loader: str, frame):
    monkeypatch.setattr(f"mascope_sdk._loaders.{loader}", lambda *a, **k: frame)


def _frames(monkeypatch, loader="load_peaks"):
    """Each call of the loader gives a new one-row frame."""
    monkeypatch.setattr(
        f"mascope_sdk._loaders.{loader}",
        lambda *a, **k: pd.DataFrame({"mz": [100.0]}),
    )


def _without_time(block):
    return {key: value for key, value in block.items() if key != "generated_utc"}


def test_provenance_reads_the_route_with_the_clients_credentials(client, monkeypatch):
    server = _serve(monkeypatch, FakeServer())

    block = client.provenance()

    assert _without_time(block) == _without_time(BLOCK)
    (call,) = server.calls
    assert call["url"] == "http://testserver"
    assert call["path"] == "provenance"
    assert call["token"] == "token"
    assert call["service_name"] == "mascope_sdk"


def test_provenance_asked_directly_has_the_clients_own_budget(client, monkeypatch):
    """Its failure is the caller's to see, so it gets the retries and timeout
    every other request of the client gets."""
    server = _serve(monkeypatch, FakeServer())

    client.provenance()

    (call,) = server.calls
    assert call["timeout"] == client._timeout
    assert call["max_attempts"] is None


def test_provenance_is_asked_afresh_on_every_call(client, monkeypatch):
    """So ``generated_utc`` says when, and a server updated in the meantime is
    reported as it now is."""
    server = _serve(monkeypatch, FakeServer())

    first = client.provenance()
    second = client.provenance()

    assert len(server.calls) == 2
    assert first["generated_utc"] != second["generated_utc"]


def test_a_server_without_the_route_has_no_provenance_and_is_not_asked_again(
    client, monkeypatch
):
    server = _serve(
        monkeypatch, FakeServer(NotFoundError("Not Found", status_code=404))
    )

    assert client.provenance() is None
    assert client.provenance() is None
    assert len(server.calls) == 1


def test_any_other_error_from_provenance_is_raised(client, monkeypatch):
    """Asked directly, a failure is the caller's to see - only the loaders
    treat provenance as best effort."""
    _serve(monkeypatch, FakeServer(ServerError("boom", status_code=500)))

    with pytest.raises(ServerError):
        client.provenance()


@pytest.mark.parametrize(
    "body",
    [
        _Response(text="<html>Sign in to the proxy</html>"),
        _Response(["not", "an", "object"]),
        _Response({"message": "no data"}),
        _Response({"data": ["not", "a", "block"]}),
    ],
    ids=["not-json", "a-list", "no-data", "data-not-a-block"],
)
def test_an_answer_that_is_no_provenance_block_is_an_api_error(
    client, monkeypatch, body
):
    """A proxy in front of the server can answer 200 with a page of its own;
    that is reported in the SDK's own terms, not as a decoding error."""
    _serve(monkeypatch, FakeServer(body=body))

    with pytest.raises(MascopeAPIError):
        client.provenance()


@pytest.mark.parametrize("method", sorted(LOADERS))
def test_every_loader_stamps_its_frame(client, monkeypatch, method):
    loader, args = LOADERS[method]
    _serve(monkeypatch, FakeServer())
    _loader_returns(monkeypatch, loader, pd.DataFrame({"mz": [100.0]}))

    frame = getattr(client, method)(*args)

    assert _without_time(frame.attrs["provenance"]) == _without_time(BLOCK)


def test_stamping_has_a_short_budget_of_its_own(client, monkeypatch):
    """The frame is already loaded; a restarting or stalled server must not
    hold it back for the load's own retries and read timeout."""
    server = _serve(monkeypatch, FakeServer())
    _frames(monkeypatch)

    client.load_peaks("My Dataset")

    (call,) = server.calls
    assert call["timeout"] == _STAMP_TIMEOUT
    assert call["max_attempts"] == 1


@pytest.mark.parametrize("method", sorted(LOADERS))
def test_a_loader_that_found_nothing_asks_nothing(client, monkeypatch, method):
    loader, args = LOADERS[method]
    server = _serve(monkeypatch, FakeServer())
    _loader_returns(monkeypatch, loader, None)

    assert getattr(client, method)(*args) is None
    assert server.calls == []


def test_a_frame_from_an_older_server_comes_back_unstamped(client, monkeypatch):
    """The SDK on PyPI talks to servers that predate the route: their frames
    are returned whole, just without the attribute."""
    _serve(monkeypatch, FakeServer(NotFoundError("Not Found", status_code=404)))
    _loader_returns(monkeypatch, "load_peaks", pd.DataFrame({"mz": [100.0]}))

    frame = client.load_peaks("My Dataset")

    assert list(frame["mz"]) == [100.0]
    assert "provenance" not in frame.attrs


@pytest.mark.parametrize(
    "server",
    [
        FakeServer(ServerError("boom", status_code=500)),
        FakeServer(MascopeConnectionError("connection refused")),
        FakeServer(RuntimeError("anything at all")),
        FakeServer(body=_Response(text="<html>Sign in to the proxy</html>")),
        FakeServer(body=_Response(["not", "an", "object"])),
    ],
    ids=["server-error", "connection", "unexpected", "not-json", "a-list"],
)
def test_a_failure_to_ask_never_costs_the_frame(client, monkeypatch, server):
    _serve(monkeypatch, server)
    _loader_returns(monkeypatch, "load_peaks", pd.DataFrame({"mz": [100.0]}))

    frame = client.load_peaks("My Dataset")

    assert list(frame["mz"]) == [100.0]
    assert "provenance" not in frame.attrs


def test_frames_from_one_build_concatenate_with_their_provenance(client, monkeypatch):
    """``pd.concat`` keeps ``attrs`` only when every input's are equal, and two
    answers from one build differ in ``generated_utc``: the frames carry the
    first answer's block, so their concatenation keeps it."""
    _serve(monkeypatch, FakeServer())
    _frames(monkeypatch)

    first = client.load_peaks("My Dataset")
    second = client.load_peaks("My Dataset")
    combined = pd.concat([first, second], ignore_index=True)

    assert combined.attrs["provenance"] == first.attrs["provenance"]
    assert combined.attrs["provenance"]["generated_utc"] == "2026-10-01T12:00:00Z"


def test_frames_from_different_builds_concatenate_without_it(client, monkeypatch):
    """A server updated between two loads is reported as it now is, and no
    single build produced their concatenation."""
    server = _serve(monkeypatch, FakeServer())
    _frames(monkeypatch)

    before = client.load_peaks("My Dataset")
    server.mascope_version = "v1.11.0"
    after = client.load_peaks("My Dataset")

    assert after.attrs["provenance"]["produced_with"]["mascope_version"] == "v1.11.0"
    assert pd.concat([before, after], ignore_index=True).attrs == {}


def test_each_frame_carries_its_own_block(client, monkeypatch):
    """A block edited on one frame must not change another's."""
    _serve(monkeypatch, FakeServer())
    _frames(monkeypatch)

    first = client.load_peaks("My Dataset")
    second = client.load_peaks("My Dataset")
    first.attrs["provenance"]["deployment_id"] = "edited"

    assert second.attrs["provenance"]["deployment_id"] == "example-lab"
    assert client.load_peaks("My Dataset").attrs["provenance"]["deployment_id"] == (
        "example-lab"
    )


def test_the_ledgers_species_table_keeps_its_place_beside_the_provenance(
    client, monkeypatch
):
    _serve(monkeypatch, FakeServer())
    ledger = pd.DataFrame({"mz": [100.0]})
    ledger.attrs["batch_peaks"] = pd.DataFrame({"batch_peak_id": ["bp-1"]})
    _loader_returns(monkeypatch, "load_batch_ledger", ledger)

    frame = client.load_batch_ledger("My Dataset")

    assert list(frame.attrs["batch_peaks"]["batch_peak_id"]) == ["bp-1"]
    assert frame.attrs["provenance"]["deployment_id"] == "example-lab"
