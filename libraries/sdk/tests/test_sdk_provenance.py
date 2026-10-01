"""
Hermetic tests for the provenance the SDK stamps onto its frames.

``MascopeClient.provenance()`` reads ``GET /api/provenance``, and the
high-level loaders stamp what it returns on ``df.attrs["provenance"]`` - so a
notebook, and whatever is published from it, keeps the deployment and build
its numbers came from. The SDK is on PyPI and talks to servers older than the
route, so a server without it must leave the frame unstamped, never fail the
load. These mock the HTTP layer and need no running stack.
"""

import copy

import pandas as pd
import pytest

from mascope_sdk import MascopeClient, NotFoundError, ServerError
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
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeServer:
    """``http_get`` answering the provenance route, or failing as told."""

    def __init__(self, error: Exception | None = None):
        self.error = error
        self.calls: list[dict] = []

    def http_get(self, url, path, access_token, params=None, **kwargs):
        self.calls.append({"url": url, "path": path, "token": access_token, **kwargs})
        if self.error is not None:
            raise self.error
        # A fresh object per answer, as parsing a response body gives
        return _Response(
            {"message": "Provenance of this deployment.", "data": copy.deepcopy(BLOCK)}
        )


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


def test_provenance_reads_the_route_with_the_clients_credentials(client, monkeypatch):
    server = _serve(monkeypatch, FakeServer())

    assert client.provenance() == BLOCK
    (call,) = server.calls
    assert call["url"] == "http://testserver"
    assert call["path"] == "provenance"
    assert call["token"] == "token"
    assert call["service_name"] == "mascope_sdk"


def test_provenance_is_asked_afresh_on_every_call(client, monkeypatch):
    """So ``generated_utc`` says when, and a server updated in the meantime is
    reported as it now is."""
    server = _serve(monkeypatch, FakeServer())

    client.provenance()
    client.provenance()

    assert len(server.calls) == 2


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


@pytest.mark.parametrize("method", sorted(LOADERS))
def test_every_loader_stamps_its_frame(client, monkeypatch, method):
    loader, args = LOADERS[method]
    _serve(monkeypatch, FakeServer())
    _loader_returns(monkeypatch, loader, pd.DataFrame({"mz": [100.0]}))

    frame = getattr(client, method)(*args)

    assert frame.attrs["provenance"] == BLOCK


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
    "error",
    [
        ServerError("boom", status_code=500),
        MascopeConnectionError("connection refused"),
    ],
)
def test_a_failure_to_ask_never_costs_the_frame(client, monkeypatch, error):
    _serve(monkeypatch, FakeServer(error))
    _loader_returns(monkeypatch, "load_peaks", pd.DataFrame({"mz": [100.0]}))

    frame = client.load_peaks("My Dataset")

    assert list(frame["mz"]) == [100.0]
    assert "provenance" not in frame.attrs


def test_each_frame_carries_its_own_block(client, monkeypatch):
    """A block edited on one frame must not change another's."""
    _serve(monkeypatch, FakeServer())
    monkeypatch.setattr(
        "mascope_sdk._loaders.load_peaks",
        lambda *a, **k: pd.DataFrame({"mz": [100.0]}),
    )

    first = client.load_peaks("My Dataset")
    second = client.load_peaks("My Dataset")
    first.attrs["provenance"]["deployment_id"] = "edited"

    assert second.attrs["provenance"]["deployment_id"] == "example-lab"


def test_the_ledgers_species_table_keeps_its_place_beside_the_provenance(
    client, monkeypatch
):
    _serve(monkeypatch, FakeServer())
    ledger = pd.DataFrame({"mz": [100.0]})
    ledger.attrs["batch_peaks"] = pd.DataFrame({"batch_peak_id": ["bp-1"]})
    _loader_returns(monkeypatch, "load_batch_ledger", ledger)

    frame = client.load_batch_ledger("My Dataset")

    assert list(frame.attrs["batch_peaks"]["batch_peak_id"]) == ["bp-1"]
    assert frame.attrs["provenance"] == BLOCK
