"""
Hermetic tests of the SDK's write methods.

Each method is a thin call over one write route, so what is pinned here is
the request it puts on the wire - method, path, body, query - and what it
makes of the answer: the route's ``data``, or its whole answer when there is
none, and a 403 raised as a :class:`PermissionDeniedError` that names the
object and the role. ``test_contract.py`` sends the same calls to a live
server.
"""

import json
from typing import Any

import pytest
import requests

from mascope_sdk import _http
from mascope_sdk.exceptions import (
    AuthenticationError,
    PermissionDeniedError,
    ValidationError,
)
from mascope_sdk.resources.batches import BatchesResource
from mascope_sdk.resources.collections import CollectionsResource
from mascope_sdk.resources.compounds import CompoundsResource
from mascope_sdk.resources.datasets import DatasetsResource
from mascope_sdk.resources.matching import MatchingResource
from mascope_sdk.resources.samples import SamplesResource


WORKSPACE_ID = "ws-1"


class _StubClient:
    """The attributes a resource reads off its client, and a cache to clear."""

    url = "http://server"
    access_token = "token"
    workspace_id = WORKSPACE_ID
    _timeout = (1, 5)
    _verify_ssl = False
    _service_name = "mascope_sdk"

    def __init__(self):
        self._cache: dict[str, Any] = {"datasets:ws-1": "a listing"}

    def clear_cache(self) -> None:
        self._cache.clear()


def _response(
    status: int, body: dict[str, Any], headers: dict[str, str] | None = None
) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(body).encode()
    response.headers.update(headers or {})
    return response


class _Wire:
    """Stands in for ``requests.request``: records each request, answers
    ``answer``."""

    def __init__(self):
        self.sent: list[dict[str, Any]] = []
        self.answer = _response(200, {"message": "ok", "data": {"id": "x"}})

    def __call__(self, method, url, **kwargs):
        self.sent.append(
            {
                "method": method,
                "path": url.removeprefix("http://server/api/"),
                "json": kwargs.get("json"),
                "params": kwargs.get("params"),
                "headers": kwargs.get("headers"),
            }
        )
        return self.answer

    @property
    def last(self) -> dict[str, Any]:
        return self.sent[-1]


@pytest.fixture
def wire(monkeypatch) -> _Wire:
    fake = _Wire()
    monkeypatch.setattr(_http.requests, "request", fake)
    return fake


@pytest.fixture
def client() -> _StubClient:
    return _StubClient()


def _resources(client):
    return {
        "datasets": DatasetsResource(client),
        "batches": BatchesResource(client),
        "samples": SamplesResource(client),
        "collections": CollectionsResource(client),
        "compounds": CompoundsResource(client),
        "matching": MatchingResource(client),
    }


#: (call, method, path, body, params) of every write method: the request it
#: must send.
REQUESTS = [
    (
        lambda r: r["datasets"].create("d1"),
        "POST",
        "workspaces/ws-1/datasets",
        {"dataset_name": "d1"},
        None,
    ),
    (
        lambda r: r["datasets"].create(
            "d1",
            description="about",
            dataset_type="ACQUISITION",
            instrument="instrument X",
            icon={"icon_id": "i", "color": "#000000"},
            workspace_id="ws-2",
        ),
        "POST",
        "workspaces/ws-2/datasets",
        {
            "dataset_name": "d1",
            "dataset_description": "about",
            "dataset_type": "ACQUISITION",
            "instrument": "instrument X",
            "icon": {"icon_id": "i", "color": "#000000"},
        },
        None,
    ),
    (
        lambda r: r["datasets"].update("ds-1", name="d2"),
        "PATCH",
        "workspaces/ws-1/datasets/ds-1",
        {"dataset_name": "d2"},
        None,
    ),
    (
        lambda r: r["datasets"].update(
            "ds-1",
            description="about",
            icon={"icon_id": "i", "color": "#000000"},
            workspace_id="ws-2",
        ),
        "PATCH",
        "workspaces/ws-2/datasets/ds-1",
        {
            "dataset_description": "about",
            "icon": {"icon_id": "i", "color": "#000000"},
        },
        None,
    ),
    (
        lambda r: r["datasets"].move("ds-1", "ws-2"),
        "POST",
        "workspaces/ws-1/datasets/ds-1/move",
        {"target_workspace_id": "ws-2"},
        None,
    ),
    (
        lambda r: r["datasets"].move("ds-1", "ws-3", workspace_id="ws-2"),
        "POST",
        "workspaces/ws-2/datasets/ds-1/move",
        {"target_workspace_id": "ws-3"},
        None,
    ),
    (
        lambda r: r["datasets"].delete("ds-1"),
        "DELETE",
        "workspaces/ws-1/datasets/ds-1",
        None,
        None,
    ),
    (
        lambda r: r["datasets"].delete("ds-1", workspace_id="ws-2"),
        "DELETE",
        "workspaces/ws-2/datasets/ds-1",
        None,
        None,
    ),
    (
        lambda r: r["batches"].update("b-1", ["c-1"]),
        "PATCH",
        "sample/batches/b-1",
        {"target_collection_ids": ["c-1"]},
        None,
    ),
    (
        lambda r: r["batches"].update("b-1", (), name="b2", description="about"),
        "PATCH",
        "sample/batches/b-1",
        {
            "target_collection_ids": [],
            "sample_batch_name": "b2",
            "sample_batch_description": "about",
        },
        None,
    ),
    (
        lambda r: r["batches"].copy("b-1", "ds-2", "b2"),
        "POST",
        "sample/batches/b-1/copy",
        {"dataset_id": "ds-2", "sample_batch_name": "b2"},
        None,
    ),
    (
        lambda r: r["batches"].copy("b-1", "ds-2", "b2", description="about"),
        "POST",
        "sample/batches/b-1/copy",
        {
            "dataset_id": "ds-2",
            "sample_batch_name": "b2",
            "sample_batch_description": "about",
        },
        None,
    ),
    (
        lambda r: r["batches"].delete("b-1"),
        "DELETE",
        "sample/batches/b-1",
        None,
        None,
    ),
    (
        lambda r: r["samples"].update("s-1", {"sample_item_name": "n"}),
        "PATCH",
        "sample/items/s-1",
        {"sample_item": {"sample_item_name": "n"}},
        None,
    ),
    (
        lambda r: r["samples"].move(("s-1", "s-2"), "b-2"),
        "POST",
        "sample/items/move",
        {"sample_batch_id": "b-2", "sample_item_ids": ["s-1", "s-2"]},
        None,
    ),
    (
        lambda r: r["samples"].copy(("s-1",), "b-2"),
        "POST",
        "sample/items/copy",
        {"sample_batch_id": "b-2", "sample_item_ids": ["s-1"]},
        None,
    ),
    (
        lambda r: r["collections"].create("c1"),
        "POST",
        "target/collections",
        {"target_collection_name": "c1", "workspace_id": "ws-1"},
        None,
    ),
    (
        lambda r: r["collections"].create(
            "c1",
            description="about",
            collection_type="DIAGNOSTICS",
            compounds=({"target_compound_formula": "C2H4O2"},),
            compound_ids=("t-1",),
            batch_ids=("b-1",),
            workspace_id="ws-2",
        ),
        "POST",
        "target/collections",
        {
            "target_collection_name": "c1",
            "workspace_id": "ws-2",
            "target_collection_description": "about",
            "target_collection_type": "DIAGNOSTICS",
            "target_compounds_create": [{"target_compound_formula": "C2H4O2"}],
            "target_compound_ids": ["t-1"],
            "sample_batch_ids": ["b-1"],
        },
        None,
    ),
    (
        lambda r: r["collections"].create("c1", workspace_id="ws-2", is_global=True),
        "POST",
        "target/collections",
        {"target_collection_name": "c1", "workspace_id": None},
        None,
    ),
    (
        lambda r: r["collections"].update("c-1", name="c2"),
        "PATCH",
        "target/collections/c-1",
        {"target_collection_name": "c2"},
        None,
    ),
    (
        lambda r: r["collections"].update(
            "c-1",
            description="about",
            collection_type="CALIBRANTS",
            compounds=({"target_compound_formula": "CH4"},),
            compound_ids=("t-1",),
        ),
        "PATCH",
        "target/collections/c-1",
        {
            "target_collection_description": "about",
            "target_collection_type": "CALIBRANTS",
            "target_compounds_create": [{"target_compound_formula": "CH4"}],
            "target_compound_ids": ["t-1"],
        },
        None,
    ),
    (
        # The batches-only payload is what makes the route ask for no more
        # than guest on the collection: nothing else may ride along.
        lambda r: r["collections"].associate("c-1", ("b-1", "b-2")),
        "PATCH",
        "target/collections/c-1",
        {"sample_batch_ids": ["b-1", "b-2"]},
        None,
    ),
    (
        lambda r: r["collections"].associate("c-1", ()),
        "PATCH",
        "target/collections/c-1",
        {"sample_batch_ids": []},
        None,
    ),
    (
        lambda r: r["collections"].delete("c-1"),
        "DELETE",
        "target/collections/c-1",
        None,
        {"delete_orphan_compounds": False},
    ),
    (
        lambda r: r["collections"].delete("c-1", delete_orphan_compounds=True),
        "DELETE",
        "target/collections/c-1",
        None,
        {"delete_orphan_compounds": True},
    ),
    (
        lambda r: r["compounds"].add(({"target_compound_formula": "CH4"},)),
        "POST",
        "target/compounds",
        [{"target_compound_formula": "CH4"}],
        None,
    ),
    (
        lambda r: r["compounds"].update(
            ({"target_compound_id": "t-1", "cas_number": "74-82-8"},)
        ),
        "PATCH",
        "target/compounds",
        [{"target_compound_id": "t-1", "cas_number": "74-82-8"}],
        None,
    ),
    (
        lambda r: r["compounds"].remove("t-1"),
        "DELETE",
        "target/compounds/t-1",
        None,
        None,
    ),
    (
        lambda r: r["matching"].aggregate_sample("s-1"),
        "POST",
        "match/aggregate/sample/s-1",
        {},
        None,
    ),
    (
        lambda r: r["matching"].aggregate_sample(
            "s-1", target_ion_id="i-1", match_params={"mz_tolerance": 5}
        ),
        "POST",
        "match/aggregate/sample/s-1",
        {"target_ion_id": "i-1", "match_params": {"mz_tolerance": 5}},
        None,
    ),
    (
        lambda r: r["matching"].aggregate_batch("b-1"),
        "POST",
        "match/aggregate/batch/b-1",
        {},
        None,
    ),
    (
        lambda r: r["matching"].aggregate_batch(
            "b-1", target_ion_id="i-1", match_params={"mz_tolerance": 5}
        ),
        "POST",
        "match/aggregate/batch/b-1",
        {"target_ion_id": "i-1", "match_params": {"mz_tolerance": 5}},
        None,
    ),
    (
        lambda r: r["matching"].save_sample("s-1"),
        "POST",
        "match/aggregate/sample/s-1/save",
        {},
        None,
    ),
    (
        lambda r: r["matching"].save_sample(
            "s-1", target_ion_id="i-1", match_params={"mz_tolerance": 5}
        ),
        "POST",
        "match/aggregate/sample/s-1/save",
        {"target_ion_id": "i-1", "match_params": {"mz_tolerance": 5}},
        None,
    ),
    (
        lambda r: r["matching"].save_batch("b-1", match_samples=False),
        "POST",
        "match/aggregate/batch/b-1/save",
        {"match_samples": False},
        None,
    ),
    (
        lambda r: r["matching"].save_batch(
            "b-1",
            target_ion_id="i-1",
            match_params={"mz_tolerance": 5},
            match_ions=False,
            match_compounds=False,
            match_collections=True,
        ),
        "POST",
        "match/aggregate/batch/b-1/save",
        {
            "target_ion_id": "i-1",
            "match_params": {"mz_tolerance": 5},
            "match_ions": False,
            "match_compounds": False,
            "match_collections": True,
        },
        None,
    ),
    (
        lambda r: r["matching"].resave_batch("b-1", match_ions=False),
        "POST",
        "match/aggregate/batch/b-1/resave",
        {"match_ions": False},
        None,
    ),
    (
        lambda r: r["matching"].resave_batch(
            "b-1",
            target_ion_id="i-1",
            match_params={"mz_tolerance": 5},
            match_compounds=False,
            match_collections=False,
            match_samples=False,
        ),
        "POST",
        "match/aggregate/batch/b-1/resave",
        {
            "target_ion_id": "i-1",
            "match_params": {"mz_tolerance": 5},
            "match_compounds": False,
            "match_collections": False,
            "match_samples": False,
        },
        None,
    ),
]


@pytest.mark.parametrize(("call", "method", "path", "body", "params"), REQUESTS)
def test_a_write_sends_its_route_what_it_was_given(
    wire, client, call, method, path, body, params
):
    call(_resources(client))

    assert wire.last["method"] == method
    assert wire.last["path"] == path
    assert wire.last["json"] == body
    assert wire.last["params"] == params
    assert wire.last["headers"]["Authorization"] == "Bearer token"
    assert wire.last["headers"]["X-Service-Name"] == "mascope_sdk"


def test_a_write_returns_the_routes_data(wire, client):
    wire.answer = _response(201, {"message": "made", "data": {"dataset_id": "d"}})

    assert DatasetsResource(client).create("d1") == {"dataset_id": "d"}


def test_a_background_write_returns_the_message_and_the_process(wire, client):
    """The server moves the process ID of a background task into a header;
    it is the one handle on the task the caller gets."""
    wire.answer = _response(
        202, {"message": "Copying 1 samples"}, {"Process-ID": "p-1"}
    )

    answer = SamplesResource(client).copy(["s-1"], "b-2")

    assert answer == {"message": "Copying 1 samples", "process_id": "p-1"}


def test_an_answer_without_data_is_returned_whole(wire, client):
    """A save reports its outcome in ``status``, which must not be lost."""
    wire.answer = _response(200, {"status": "failed", "message": "no peaks"})

    answer = MatchingResource(client).save_batch("b-1")

    assert answer == {"status": "failed", "message": "no peaks"}


@pytest.mark.parametrize(
    "level", ["match_ions", "match_compounds", "match_collections", "match_samples"]
)
def test_a_sample_save_takes_no_level_flags(wire, client, level):
    """The sample save route saves every level whatever the body says."""
    with pytest.raises(TypeError):
        MatchingResource(client).save_sample("s-1", **{level: False})


def test_a_write_clears_the_clients_cache(wire, client):
    """A listing read before the write would show the old state."""
    DatasetsResource(client).update("ds-1", name="d2")

    assert client._cache == {}


def test_a_refused_write_leaves_the_cache(wire, client):
    wire.answer = _response(422, {"error": "name taken"})

    with pytest.raises(ValidationError):
        DatasetsResource(client).create("d1")

    assert client._cache != {}


def _forbidden() -> requests.Response:
    return _response(
        403, {"detail": "You do not have permission to perform this action."}
    )


#: (call, operation, object type, object id, a phrase of the role) of the
#: writes whose 403 names its object and role.
REFUSALS = [
    (
        lambda r: r["datasets"].create("d1"),
        "create a dataset in",
        "workspace",
        WORKSPACE_ID,
        "editor",
    ),
    (
        lambda r: r["datasets"].update("ds-1", name="d2"),
        "update",
        "dataset",
        "ds-1",
        "editor in the workspace",
    ),
    (
        lambda r: r["datasets"].move("ds-1", "ws-2"),
        "move",
        "dataset",
        "ds-1",
        "in workspace 'ws-2'",
    ),
    (
        lambda r: r["datasets"].delete("ds-1"),
        "delete",
        "dataset",
        "ds-1",
        "global admin role",
    ),
    (
        lambda r: r["batches"].update("b-1", []),
        "update",
        "batch",
        "b-1",
        "editor on the batch",
    ),
    (
        lambda r: r["batches"].copy("b-1", "ds-2", "b2"),
        "copy",
        "batch",
        "b-1",
        "dataset 'ds-2' (a locked dataset: none)",
    ),
    (
        lambda r: r["batches"].delete("b-1"),
        "delete",
        "batch",
        "b-1",
        "owner role",
    ),
    (
        lambda r: r["samples"].update("s-1", {}),
        "update",
        "sample",
        "s-1",
        "editor on the sample",
    ),
    (
        lambda r: r["samples"].move(["s-1"], "b-2"),
        "move samples into",
        "batch",
        "b-2",
        "editor on the samples",
    ),
    (
        lambda r: r["samples"].copy(["s-1"], "b-2"),
        "copy samples into",
        "batch",
        "b-2",
        "guest on the samples",
    ),
    (
        lambda r: r["collections"].create("c1"),
        "create a target collection in",
        "workspace",
        WORKSPACE_ID,
        "editor in the workspace",
    ),
    (
        lambda r: r["collections"].create("c1", is_global=True),
        "create a target collection in",
        "workspace",
        "(global)",
        "global admin role",
    ),
    (
        lambda r: r["collections"].create("c1", batch_ids=["b-1"]),
        "create a target collection in",
        "workspace",
        WORKSPACE_ID,
        "editor on the batches",
    ),
    (
        lambda r: r["collections"].update("c-1", name="c2"),
        "update",
        "target collection",
        "c-1",
        "editor in the collection's workspace",
    ),
    (
        lambda r: r["collections"].associate("c-1", ["b-1"]),
        "associate batches with",
        "target collection",
        "c-1",
        "guest on the collection",
    ),
    (
        lambda r: r["collections"].delete("c-1"),
        "delete",
        "target collection",
        "c-1",
        "editor in the collection's workspace",
    ),
    (
        lambda r: r["compounds"].add([{"target_compound_formula": "CH4"}]),
        "add",
        "target compounds",
        "CH4",
        "global editor role",
    ),
    (
        lambda r: r["compounds"].update([{"target_compound_id": "t-1"}]),
        "update",
        "target compounds",
        "t-1",
        "global editor role",
    ),
    (
        lambda r: r["compounds"].remove("t-1"),
        "remove",
        "target compound",
        "t-1",
        "global editor role",
    ),
    (
        lambda r: r["matching"].aggregate_sample("s-1"),
        "run matching on",
        "sample",
        "s-1",
        "guest on the sample",
    ),
    (
        lambda r: r["matching"].aggregate_batch("b-1"),
        "run matching on",
        "batch",
        "b-1",
        "guest on the batch",
    ),
    (
        lambda r: r["matching"].save_sample("s-1"),
        "save the matches of",
        "sample",
        "s-1",
        "editor on the sample",
    ),
    (
        lambda r: r["matching"].save_batch("b-1"),
        "save the matches of",
        "batch",
        "b-1",
        "editor on the batch",
    ),
    (
        lambda r: r["matching"].resave_batch("b-1"),
        "rebuild the matches of",
        "batch",
        "b-1",
        "editor on the batch",
    ),
]


@pytest.mark.parametrize(
    ("call", "operation", "object_type", "object_id", "role"), REFUSALS
)
def test_a_refused_write_names_its_object_and_role(
    wire, client, call, operation, object_type, object_id, role
):
    wire.answer = _forbidden()

    with pytest.raises(PermissionDeniedError) as refused:
        call(_resources(client))

    error = refused.value
    assert error.status_code == 403
    assert error.operation == operation
    assert error.object_type == object_type
    assert error.object_id == object_id
    assert role in error.required_role
    # Said in the message too, for whoever reads only that.
    assert f"{object_type} '{object_id}'" in str(error)
    assert role in str(error)
    assert "You do not have permission" in str(error)


def test_a_403_is_still_an_authentication_error(wire, client):
    """An ``except AuthenticationError`` written for the 403 of before the
    new class still catches it."""
    wire.answer = _forbidden()

    with pytest.raises(AuthenticationError):
        BatchesResource(client).delete("b-1")


def test_a_403_on_a_read_has_no_object():
    """Only a write knows what it needs; a read's 403 says what the server
    said, without the token hint a 401 gets - the token was accepted."""
    with pytest.raises(PermissionDeniedError) as refused:
        _http._raise_for_status(_forbidden(), "http://server/api/x")

    assert refused.value.operation is None
    assert refused.value.required_role is None
    assert "check your API token" not in str(refused.value)


def test_a_401_is_not_a_permission_refusal():
    with pytest.raises(AuthenticationError) as refused:
        _http._raise_for_status(_response(401, {"error": "bad"}), "http://x/api/y")

    assert not isinstance(refused.value, PermissionDeniedError)
    assert "check your API token" in str(refused.value)


def test_a_write_retries_a_gateway_error(monkeypatch, client):
    """The writes share the reads' retries: a 503 is sent again."""
    answers = [
        _response(503, {"error": "busy"}),
        _response(200, {"message": "ok", "data": {"id": "x"}}),
    ]
    sent = []

    def fake(method, url, **kwargs):
        sent.append(method)
        return answers.pop(0)

    monkeypatch.setattr(_http.requests, "request", fake)
    monkeypatch.setattr(_http.time, "sleep", lambda seconds: None)

    assert DatasetsResource(client).delete("ds-1") == {"id": "x"}
    assert sent == ["DELETE", "DELETE"]


def test_a_refusal_message_keeps_the_url(wire, client):
    wire.answer = _forbidden()

    with pytest.raises(PermissionDeniedError) as refused:
        BatchesResource(client).delete("b-1")

    assert str(refused.value).endswith("(URL: http://server/api/sample/batches/b-1)")


def test_a_refusal_without_an_operation_reads_as_any_api_error():
    error = PermissionDeniedError("no", url="http://server/api/x")

    assert str(error) == "[HTTP 403] no (URL: http://server/api/x)"


def test_a_writes_warnings_are_logged(wire, client):
    from loguru import logger

    records: list[str] = []
    handler = logger.add(
        lambda message: records.append(message.record["message"]),
        level="WARNING",
        filter="mascope_sdk",
    )
    wire.answer = _response(
        200, {"message": "ok", "warnings": ["2 rows left out"], "data": {}}
    )
    try:
        BatchesResource(client).update("b-1", [])
    finally:
        logger.remove(handler)

    assert records == ["API warning: 2 rows left out"]


@pytest.mark.parametrize(
    ("failure", "raised"),
    [
        (requests.exceptions.Timeout("slow"), "MascopeTimeoutError"),
        (requests.exceptions.ConnectionError("down"), "MascopeConnectionError"),
    ],
)
def test_a_write_retries_a_lost_connection_then_raises(
    monkeypatch, client, failure, raised
):
    """A timeout or a dropped connection is tried again, and the last one
    raised as the SDK's own error; the shared loop logs each retry."""
    from mascope_sdk import exceptions

    sent, slept = [], []

    def fake(method, url, **kwargs):
        sent.append(method)
        raise failure

    monkeypatch.setattr(_http.requests, "request", fake)
    monkeypatch.setattr(_http.time, "sleep", slept.append)

    with pytest.raises(getattr(exceptions, raised)) as error:
        DatasetsResource(client).delete("ds-1")

    assert type(error.value).__name__ == raised
    assert len(sent) == _http.RETRY_MAX_ATTEMPTS
    assert slept == [
        _http.RETRY_BACKOFF_BASE**attempt
        for attempt in range(1, _http.RETRY_MAX_ATTEMPTS)
    ]


def test_a_write_that_cannot_be_sent_is_not_retried(monkeypatch, client):
    """An invalid URL, a TLS failure: sending it again cannot help."""
    from mascope_sdk.exceptions import MascopeConnectionError

    sent = []

    def fake(method, url, **kwargs):
        sent.append(method)
        raise requests.exceptions.InvalidURL("no")

    monkeypatch.setattr(_http.requests, "request", fake)

    with pytest.raises(MascopeConnectionError, match="Request failed"):
        DatasetsResource(client).delete("ds-1")
    assert sent == ["DELETE"]


def test_a_retry_is_logged(monkeypatch, client):
    from loguru import logger

    records: list[str] = []
    handler = logger.add(
        lambda message: records.append(message.record["message"]),
        level="INFO",
        filter="mascope_sdk._http",
    )
    answers = [
        _response(503, {"error": "busy"}),
        _response(200, {"message": "ok", "data": {}}),
    ]
    monkeypatch.setattr(
        _http.requests, "request", lambda method, url, **kwargs: answers.pop(0)
    )
    monkeypatch.setattr(_http.time, "sleep", lambda seconds: None)
    try:
        DatasetsResource(client).delete("ds-1")
    finally:
        logger.remove(handler)

    assert len(records) == 1
    assert records[0].startswith("DELETE http://server/api/workspaces/ws-1/")
    assert "(attempt 1/4)" in records[0]


def test_a_refusal_without_a_url_says_none():
    error = PermissionDeniedError(
        "no",
        operation="delete",
        object_type="batch",
        object_id="b-1",
        required_role="editor on the batch",
    )

    assert str(error) == (
        "[HTTP 403] Refused to delete batch 'b-1': it needs editor on the batch."
        " The server said: no"
    )
