"""
Hermetic tests of ``peak_assignments.import_run`` and ``abandon_import``.

Two halves. The checks a frame goes through before anything is sent are
pinned one rule at a time, each with a frame that breaks it and nothing else.
The assembly is pinned against a fake of the import route that keeps the
server's own rules - a row offset applied once, a create resolved by its
``import_id``, the row cap and a finalize that answers a repeat with the
original success - so what is counted is what the server would hold: one run,
every row once, whatever answer was lost on the way.
"""

import json
from typing import Any

import pandas as pd
import pytest
import requests

from mascope_sdk import _http
from mascope_sdk.exceptions import (
    ConflictError,
    ImportValidationError,
    MascopeAPIError,
    MascopeConnectionError,
    ServerError,
    ValidationError,
)
from mascope_sdk.resources import _assignment_import
from mascope_sdk.resources.peak_assignments import PeakAssignmentsResource


SAMPLE_ID = "sample-1"
BASE = "http://server/api/peak-assignments/sample/"
IMPORT = f"{SAMPLE_ID}/runs/import"
BANDS = {"assigned": 0.75, "candidate": 0.45}
CALIBRATION = {"method": "none"}


def _response(status: int, body: Any) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(body).encode()
    return response


def _error(status: int, message: str) -> requests.Response:
    return _response(status, {"error": message, "detail": {"error_id": "e"}})


def _claim_held(*, coded: bool = True) -> requests.Response:
    """The 409 a request meets while another holds the sample's claim: as the
    server answers it now, or, *coded* False, as one from before the code."""
    detail = {"error_id": "e"}
    if coded:
        detail.update(code="assignment_claim_held", sample_item_id=SAMPLE_ID)
    message = (
        "Another peak assignment operation is in progress for sample 'S'. "
        "Retry when it has finished."
    )
    return _response(409, {"error": message, "detail": detail})


def frame(n: int = 3, **columns) -> pd.DataFrame:
    """*n* rows of a valid import, with *columns* added or replaced."""
    rows = pd.DataFrame(
        {
            "sample_peak_id": [f"p{i}" for i in range(n)],
            "sample_peak_mz": [100.0 + i for i in range(n)],
            "sample_peak_intensity": [1000.0 * (i + 1) for i in range(n)],
            "role": ["M0"] * n,
            "assigned_formula": [f"C{i + 1}H4" for i in range(n)],
            "fit_score": [0.9] * n,
        }
    )
    for name, values in columns.items():
        rows[name] = values
    return rows


class FakeImportRoute:
    """``POST .../runs/import``, ``DELETE .../runs/{id}`` and the reads
    ``import_run`` makes, behind ``requests``.

    ``lose`` maps a request's number (1 for the first import request) to how
    its answer is lost once the server applied it: ``"timeout"``,
    ``"connection"`` or ``"502"``. ``refuse`` maps a request's number to a
    response sent instead of applying it. ``latest_rows`` is how many rows the
    sample's latest completed run holds, None for a sample with no run.
    """

    def __init__(self, max_rows: int = 1000):
        self.max_rows = max_rows
        self.runs: dict[str, dict[str, Any]] = {}
        self.posts: list[dict[str, Any]] = []
        self.deletes: list[str] = []
        self.lose: dict[int, str] = {}
        self.refuse: dict[int, requests.Response] = {}
        self.latest_rows: int | None = None
        self.newer_failed = False
        self.mechanisms = [
            {"ionization_mechanism_id": "mech-h", "ionization_mechanism": "[M+H]+"},
            {"ionization_mechanism_id": "mech-na", "ionization_mechanism": "[M+Na]+"},
            {"ionization_mechanism_id": "mech-x1", "ionization_mechanism": "[M+K]+"},
            {"ionization_mechanism_id": "mech-x2", "ionization_mechanism": "[M+K]+"},
        ]
        self.mechanism_reads = 0

    # --- the wire -----------------------------------------------------------

    def request(self, method, url, json=None, params=None, **kwargs):
        path = url.removeprefix(BASE)
        if method == "DELETE":
            run_id = path.rsplit("/", 1)[1]
            self.deletes.append(run_id)
            run = self.runs.get(run_id)
            if run is None:
                return _error(404, "No such run.")
            if run["status"] != "importing":
                return _error(409, "Not an import in progress.")
            del self.runs[run_id]
            return self._state(run, f"Abandoned '{run_id}'.")
        assert method == "POST" and path == IMPORT, (method, url)
        self.posts.append(json)
        number = len(self.posts)
        # A client that never gets anywhere fails here rather than hangs.
        assert number <= 50, "the import kept sending"
        if number in self.refuse:
            return self.refuse[number]
        response = self._apply(json)
        how = self.lose.get(number)
        if how == "timeout":
            raise requests.exceptions.ReadTimeout("read timed out")
        if how == "connection":
            raise requests.exceptions.ConnectionError("connection reset")
        if how == "502":
            return _error(502, "Bad Gateway")
        return response

    def _apply(self, body: dict[str, Any]) -> requests.Response:
        """What the server does with one request, after the route's rules."""
        chunk, rows = body["chunk"], body["rows"]
        if len(rows) > self.max_rows:
            return _error(422, f"over {self.max_rows} rows")
        if chunk["run_id"] is None:
            run = next(
                (r for r in self.runs.values() if r["import_id"] == chunk["import_id"]),
                None,
            )
            if run is None:
                for key in ("tier_bands", "calibration"):
                    assert body[key] is not None, f"the create carries {key}"
                run = {
                    "id": f"run-{len(self.runs) + 1}",
                    "import_id": chunk["import_id"],
                    "status": "importing",
                    "rows": [],
                    "folds": 0,
                    "create": body,
                }
                self.runs[run["id"]] = run
        else:
            run = self.runs[chunk["run_id"]]
        staged, index = len(run["rows"]), chunk["index"]
        if run["status"] == "completed":
            if chunk["complete"] and index + len(rows) == staged:
                return self._state(run, "already completed")
            return _error(409, "already completed")
        replay = index < staged and index + len(rows) == staged
        if not replay:
            if index != staged:
                return _error(409, f"chunk index {index}, run holds {staged}")
            run["rows"].extend(rows)
        if chunk["complete"]:
            run["status"] = "completed"
            run["folds"] += 1
        return self._state(run, "ok")

    def _state(self, run: dict[str, Any], message: str) -> requests.Response:
        record = {
            "peak_assignment_run_id": run["id"],
            "run_status": run["status"],
            "rows": len(run["rows"]),
            "max_rows_per_request": self.max_rows,
        }
        body = {"status": "success", "message": message, "results": 1}
        return _response(200, {**body, "data": [record]})

    def get(self, url, params=None, **kwargs):
        path = url.removeprefix("http://server/api/")
        if path == "ionization_mechanisms":
            self.mechanism_reads += 1
            return _response(200, {"message": "ok", "data": self.mechanisms})
        path = path.removeprefix("peak-assignments/sample/")
        if path.endswith("/runs"):
            runs = []
            if self.newer_failed:
                runs.append(
                    {
                        "peak_assignment_run_id": "run-failed",
                        "engine": "mascope",
                        "status": "failed",
                        "peak_assignment_run_utc_created": "2026-10-29T10:00:00Z",
                    }
                )
            if self.latest_rows is not None:
                runs.append(
                    {
                        "peak_assignment_run_id": "run-in-app",
                        "engine": "mascope",
                        "status": "completed",
                        "peak_assignment_run_utc_created": "2026-10-28T10:00:00Z",
                    }
                )
            return _response(200, {"message": "runs", "data": runs})
        assert params["peak_assignment_run_id"] == "run-in-app"
        assert params["limit"] == 1
        page = [{"sample_peak_id": "p0"}]
        return _response(
            200, {"message": "ok", "total": self.latest_rows, "data": page}
        )

    # --- what a test reads ----------------------------------------------------

    def only_run(self) -> dict[str, Any]:
        (run,) = self.runs.values()
        return run


class _Ionization:
    def __init__(self, route: FakeImportRoute):
        self.route = route

    def list(self) -> pd.DataFrame:
        response = self.route.get("http://server/api/ionization_mechanisms")
        return pd.DataFrame(response.json()["data"])


class _StubClient:
    url = "http://server"
    access_token = "token"
    _timeout = (1, 5)
    _verify_ssl = False
    _service_name = "mascope_sdk"

    def __init__(self, route: FakeImportRoute):
        self.ionization = _Ionization(route)

    def clear_cache(self) -> None:
        pass


@pytest.fixture
def route(monkeypatch) -> FakeImportRoute:
    fake = FakeImportRoute()
    monkeypatch.setattr(_http.requests, "request", fake.request)
    monkeypatch.setattr(_http.requests, "get", fake.get)
    monkeypatch.setattr(_http.time, "sleep", lambda seconds: None)
    return fake


@pytest.fixture
def resource(route) -> PeakAssignmentsResource:
    return PeakAssignmentsResource(_StubClient(route))


def small_chunks(monkeypatch, route: FakeImportRoute, n: int) -> None:
    """Chunks of *n* rows: the cap the first request is sized by, and the one
    every answer reports."""
    monkeypatch.setattr(_assignment_import, "DEFAULT_MAX_ROWS", n)
    route.max_rows = n


def publish(resource, rows: pd.DataFrame, **kwargs) -> str:
    arguments = {
        "engine": "peaky",
        "engine_version": "0.7.0",
        "tier_bands": BANDS,
        "calibration": CALIBRATION,
        **kwargs,
    }
    return resource.import_run(SAMPLE_ID, rows, **arguments)


def refused(resource, rows: pd.DataFrame, route, **kwargs) -> ImportValidationError:
    """The local refusal of *rows*, having checked nothing was sent."""
    with pytest.raises(ImportValidationError) as caught:
        publish(resource, rows, **kwargs)
    assert route.posts == [], "a frame refused locally sends nothing"
    return caught.value


# --- one request ----------------------------------------------------------------


class TestOneRequest:
    def test_a_slim_frame_is_one_request_that_creates_and_finalizes(
        self, resource, route
    ):
        run_id = publish(resource, frame(), config={"window_ppm": 3})

        run = route.only_run()
        assert run_id == run["id"]
        assert run["status"] == "completed"
        assert run["folds"] == 1
        (body,) = route.posts
        assert body["engine"] == "peaky"
        assert body["engine_version"] == "0.7.0"
        assert body["tier_bands"] == BANDS
        assert body["calibration"] == CALIBRATION
        assert body["config"] == {"window_ppm": 3}
        assert body["chunk"]["run_id"] is None
        assert body["chunk"]["index"] == 0
        assert body["chunk"]["complete"] is True
        assert len(body["chunk"]["import_id"]) >= 1
        assert [r["sample_peak_id"] for r in body["rows"]] == ["p0", "p1", "p2"]

    def test_a_row_carries_the_route_fields_only(self, resource, route):
        rows = frame(
            evidence=[0.5] * 3,
            p_correct=[0.9] * 3,
            peak_assignment_id=["a0", "a1", "a2"],
        )

        publish(resource, rows)

        row = route.posts[0]["rows"][0]
        assert set(row) == {
            "sample_peak_id",
            "sample_peak_mz",
            "sample_peak_intensity",
            "role",
            "assigned_formula",
            "fit_score",
        }

    def test_each_import_has_an_import_id_of_its_own(self, resource, route):
        publish(resource, frame())
        publish(resource, frame())

        first, second = (body["chunk"]["import_id"] for body in route.posts)
        assert first != second
        assert len(route.runs) == 2

    def test_an_unknown_column_is_left_out_with_a_warning(self, resource, route):
        warnings = []
        from loguru import logger

        handle = logger.add(warnings.append, level="WARNING")
        try:
            publish(resource, frame(fitscore=[0.1] * 3))
        finally:
            logger.remove(handle)

        assert "fitscore" not in route.posts[0]["rows"][0]
        assert any("fitscore" in str(message) for message in warnings)


# --- chunks -----------------------------------------------------------------------


class TestChunks:
    def test_offsets_are_row_counts_and_follow_the_answers(
        self, resource, route, monkeypatch
    ):
        small_chunks(monkeypatch, route, 2)

        publish(resource, frame(5))

        chunks = [body["chunk"] for body in route.posts]
        assert [c["index"] for c in chunks] == [0, 2, 4]
        assert [c["complete"] for c in chunks] == [False, False, True]
        assert chunks[0]["run_id"] is None
        assert {c["run_id"] for c in chunks[1:]} == {"run-1"}
        assert {c["import_id"] for c in chunks} == {chunks[0]["import_id"]}
        # Only the create carries the run's metadata.
        assert "tier_bands" in route.posts[0]
        assert "tier_bands" not in route.posts[1]
        assert route.posts[1]["engine"] == "peaky"

    def test_the_cap_the_server_answers_with_sizes_the_next_chunk(
        self, resource, route, monkeypatch
    ):
        monkeypatch.setattr(_assignment_import, "DEFAULT_MAX_ROWS", 3)
        route.max_rows = 3
        answers = iter([2, 2, 2, 2])
        original = route._state

        def lowered(run, message):
            route.max_rows = next(answers)
            return original(run, message)

        monkeypatch.setattr(route, "_state", lowered)

        publish(resource, frame(7))

        assert [len(body["rows"]) for body in route.posts] == [3, 2, 2]

    @pytest.mark.parametrize("room, per_chunk", [(2, [2, 2]), (1, [1, 1, 1, 1])])
    def test_bytes_cut_a_chunk_before_the_row_cap_does(
        self, resource, route, monkeypatch, room, per_chunk
    ):
        rows = frame(4, provenance=[{"engine_provenance": {"note": "x" * 400}}] * 4)
        one = len(json.dumps(route_row(rows)))
        # The largest body beside its rows: the create's, its run id at 32
        # characters, which is as long as an id gets.
        overhead = len(
            json.dumps(
                {
                    "engine": "peaky",
                    "engine_version": "0.7.0",
                    "tier_bands": BANDS,
                    "calibration": CALIBRATION,
                    "config": None,
                    "chunk": {
                        "run_id": "x" * 32,
                        "import_id": "0" * 32,
                        "index": 0,
                        "complete": False,
                    },
                    "rows": [],
                }
            )
        )
        # Two rows take their two sizes and the ", " between them: room for
        # exactly that, or a byte less.
        budget = overhead + 2 * one + 2 - (2 - room)
        monkeypatch.setattr(_assignment_import, "MAX_BODY_BYTES", budget)

        publish(resource, rows)

        assert [len(body["rows"]) for body in route.posts] == per_chunk
        for body in route.posts:
            assert len(json.dumps(body)) <= _assignment_import.MAX_BODY_BYTES

    def test_a_row_no_request_can_carry_is_refused_before_sending(
        self, resource, route, monkeypatch
    ):
        monkeypatch.setattr(_assignment_import, "MAX_BODY_BYTES", 2000)
        rows = frame(3)
        rows["provenance"] = [None, {"engine_provenance": "x" * 3000}, None]

        error = refused(resource, rows, route)

        assert error.rows == [1]

    def test_a_retried_chunk_applied_twice_resyncs_from_the_answer(
        self, resource, route, monkeypatch
    ):
        # The server applied the second chunk, the answer was lost, and the
        # HTTP layer sent it again: the server answers the replay with the
        # rows it holds, and the import carries on from there.
        small_chunks(monkeypatch, route, 2)
        route.lose = {2: "timeout"}

        publish(resource, frame(5))

        run = route.only_run()
        assert len(run["rows"]) == 5
        assert run["status"] == "completed"
        assert [body["chunk"]["index"] for body in route.posts] == [0, 2, 2, 4]

    def test_an_answer_ahead_of_the_chunk_moves_the_offset_on(
        self, resource, route, monkeypatch
    ):
        small_chunks(monkeypatch, route, 2)
        original = route._apply

        def ahead(body):
            # The second chunk's rows were applied by an earlier attempt the
            # client never heard back from, the third's too.
            response = original(body)
            if body["chunk"]["index"] == 2:
                run = route.only_run()
                run["rows"].extend(route_rows(frame(5))[4:5])
                response = route._state(run, "ok")
            return response

        monkeypatch.setattr(route, "_apply", ahead)

        publish(resource, frame(5))

        assert [body["chunk"]["index"] for body in route.posts] == [0, 2, 5]
        assert route.posts[-1]["rows"] == []
        assert route.posts[-1]["chunk"]["complete"] is True

    def test_an_answer_that_does_not_advance_abandons_the_import(
        self, resource, route, monkeypatch
    ):
        small_chunks(monkeypatch, route, 2)
        original = route._state

        def stuck(run, message):
            response = original(run, message)
            body = response.json()
            body["data"][0]["rows"] = 0
            return _response(200, body)

        monkeypatch.setattr(route, "_state", stuck)

        with pytest.raises(MascopeAPIError, match="abandoned"):
            publish(resource, frame(5))

        assert route.deletes == ["run-1"]
        assert route.runs == {}

    def test_a_finalize_answered_importing_abandons_the_import(
        self, resource, route, monkeypatch
    ):
        original = route._state

        def unfinished(run, message):
            run["status"] = "importing"
            return original(run, message)

        monkeypatch.setattr(route, "_state", unfinished)

        with pytest.raises(MascopeAPIError, match="to finish it"):
            publish(resource, frame(3))

        assert len(route.posts) == 1
        assert route.deletes == ["run-1"]


def route_row(rows: pd.DataFrame) -> dict[str, Any]:
    return route_rows(rows)[0]


def route_rows(rows: pd.DataFrame) -> list[dict[str, Any]]:
    prepared, _, _ = _assignment_import.prepare_rows(rows, BANDS, lambda: None)
    return prepared


# --- what is sent once ------------------------------------------------------------


class TestSentOnce:
    @pytest.mark.parametrize("how", ["timeout", "connection", "502"])
    def test_a_lost_create_is_not_sent_again(self, resource, route, monkeypatch, how):
        small_chunks(monkeypatch, route, 2)
        route.lose = {1: how}

        with pytest.raises((MascopeConnectionError, ServerError)) as caught:
            publish(resource, frame(5))

        assert len(route.posts) == 1
        assert "list_runs('sample-1') shows the run if it was created" in str(
            caught.value
        )
        assert "abandon_import" in str(caught.value)
        assert route.deletes == [], "the run's id never arrived"

    @pytest.mark.parametrize("how", ["timeout", "connection", "502"])
    def test_a_lost_finalize_is_not_sent_again(self, resource, route, monkeypatch, how):
        small_chunks(monkeypatch, route, 2)
        route.lose = {3: how}

        with pytest.raises((MascopeConnectionError, ServerError)) as caught:
            publish(resource, frame(5))

        assert len(route.posts) == 3
        run = route.only_run()
        assert run["folds"] == 1
        assert "run 'run-1' 'completed' if the import finished" in str(caught.value)
        assert route.deletes == [], "the import may well have finished"

    def test_a_lost_single_request_import_is_not_sent_again(self, resource, route):
        route.lose = {1: "timeout"}

        with pytest.raises(MascopeConnectionError):
            publish(resource, frame(3))

        assert len(route.posts) == 1
        assert route.only_run()["folds"] == 1

    def test_a_chunk_between_is_sent_again_and_applied_once(
        self, resource, route, monkeypatch
    ):
        small_chunks(monkeypatch, route, 2)
        route.lose = {2: "502"}

        publish(resource, frame(5))

        assert len(route.only_run()["rows"]) == 5

    @pytest.mark.parametrize("how", ["timeout", "connection", "502"])
    def test_a_chunk_between_lost_on_every_try_abandons_the_run(
        self, resource, route, monkeypatch, how
    ):
        # An append cannot finish an import, so whatever became of it the run
        # is still importing, and would hold the sample.
        small_chunks(monkeypatch, route, 2)
        route.lose = {number: how for number in range(2, 2 + _http.RETRY_MAX_ATTEMPTS)}

        with pytest.raises((MascopeConnectionError, ServerError)) as caught:
            publish(resource, frame(5))

        assert len(route.posts) == 1 + _http.RETRY_MAX_ATTEMPTS
        assert route.deletes == ["run-1"]
        assert route.runs == {}
        message = str(caught.value)
        assert "cannot have finished it; the import was abandoned" in message
        assert "'completed' if the import finished" not in message

    def test_a_chunk_lost_whose_run_cannot_be_abandoned_says_how_to(
        self, resource, route, monkeypatch
    ):
        small_chunks(monkeypatch, route, 2)
        route.lose = {
            number: "502" for number in range(2, 2 + _http.RETRY_MAX_ATTEMPTS)
        }
        original = route.request

        def no_delete(method, url, **kwargs):
            if method == "DELETE":
                route.deletes.append(url)
                return _error(500, "boom")
            return original(method, url, **kwargs)

        monkeypatch.setattr(_http.requests, "request", no_delete)

        with pytest.raises(ServerError) as caught:
            publish(resource, frame(5))

        assert route.only_run()["status"] == "importing"
        assert "abandon_import('sample-1', 'run-1') deletes it" in str(caught.value)
        assert "was abandoned" not in str(caught.value)


# --- the sample's claim -------------------------------------------------------------


class TestTheClaim:
    """A 409 that met another request on the sample applied nothing.

    The route takes the sample's claim for each request, and an append
    retried after a timeout meets its own first attempt still running there.
    """

    @pytest.fixture
    def pauses(self, monkeypatch) -> list[float]:
        slept: list[float] = []
        monkeypatch.setattr(_http.time, "sleep", slept.append)
        return slept

    @pytest.mark.parametrize("coded", [True, False])
    def test_a_retried_chunk_waits_out_its_first_attempt(
        self, resource, route, monkeypatch, coded
    ):
        small_chunks(monkeypatch, route, 2)
        # The second chunk is applied and its answer lost; the retry meets the
        # first attempt still holding the sample, and the one after that is
        # answered as a replay.
        route.lose = {2: "timeout"}
        route.refuse = {3: _claim_held(coded=coded)}

        publish(resource, frame(5))

        run = route.only_run()
        assert run["status"] == "completed"
        assert len(run["rows"]) == 5
        assert route.deletes == []
        assert [body["chunk"]["index"] for body in route.posts] == [0, 2, 2, 2, 4]
        assert route.posts[3] == route.posts[2], "the same chunk goes again"

    @pytest.mark.parametrize("number", [1, 3], ids=["create", "finalize"])
    def test_a_request_sent_once_waits_out_the_claim_too(
        self, resource, route, monkeypatch, number
    ):
        small_chunks(monkeypatch, route, 2)
        route.refuse = {number: _claim_held()}

        publish(resource, frame(5))

        run = route.only_run()
        assert (run["status"], run["folds"], len(run["rows"])) == ("completed", 1, 5)
        assert len(route.posts) == 4
        assert route.deletes == []

    def test_the_pauses_double_up_to_a_ceiling(
        self, resource, route, monkeypatch, pauses
    ):
        small_chunks(monkeypatch, route, 2)
        route.refuse = {number: _claim_held() for number in range(2, 9)}

        publish(resource, frame(5))

        assert pauses == [2.0, 4.0, 8.0, 15.0, 15.0, 15.0, 15.0]

    def test_a_claim_held_throughout_abandons_the_run(
        self, resource, route, monkeypatch, pauses
    ):
        from mascope_sdk.resources import peak_assignments

        small_chunks(monkeypatch, route, 2)
        monkeypatch.setattr(peak_assignments, "CLAIM_ATTEMPTS", 3)
        route.refuse = {number: _claim_held() for number in range(2, 5)}

        with pytest.raises(ConflictError):
            publish(resource, frame(5))

        assert len(route.posts) == 4
        assert route.deletes == ["run-1"]

    def test_another_409_is_not_sent_again(self, resource, route, monkeypatch):
        small_chunks(monkeypatch, route, 2)
        route.refuse = {2: _error(409, "chunk index 2, run holds 4")}

        with pytest.raises(ConflictError):
            publish(resource, frame(5))

        assert len(route.posts) == 2
        assert route.deletes == ["run-1"]

    def test_another_code_is_not_the_claim(self, resource, route, monkeypatch):
        # A coded 409 is read by its code alone, whatever its prose says.
        small_chunks(monkeypatch, route, 2)
        coded = _claim_held()
        body = coded.json()
        body["detail"]["code"] = "assignment_in_flight"
        route.refuse = {2: _response(409, body)}

        with pytest.raises(ConflictError):
            publish(resource, frame(5))

        assert len(route.posts) == 2

    def test_the_abandon_waits_out_the_claim(self, resource, route, monkeypatch):
        # A chunk refused, and the delete after it meeting a request still
        # running on the sample: the run goes all the same.
        small_chunks(monkeypatch, route, 2)
        route.refuse = {2: _error(422, "peak 'p3' is not a peak of the sample")}
        original = route.request
        held = iter([True])

        def claimed_once(method, url, **kwargs):
            if method == "DELETE" and next(held, False):
                return _claim_held()
            return original(method, url, **kwargs)

        monkeypatch.setattr(_http.requests, "request", claimed_once)

        with pytest.raises(ValidationError):
            publish(resource, frame(5))

        assert route.deletes == ["run-1"]
        assert route.runs == {}


# --- refusals ---------------------------------------------------------------------


class TestRefusals:
    def test_a_refused_create_leaves_nothing_to_abandon(self, resource, route):
        route.refuse = {1: _error(409, "Run 'run-9' is already in flight.")}

        with pytest.raises(ConflictError):
            publish(resource, frame())

        assert route.deletes == []

    def test_a_chunk_refused_after_the_create_abandons_the_run(
        self, resource, route, monkeypatch
    ):
        small_chunks(monkeypatch, route, 2)
        route.refuse = {2: _error(422, "peak 'p3' is not a peak of the sample")}

        with pytest.raises(ValidationError, match="p3"):
            publish(resource, frame(5))

        assert route.deletes == ["run-1"]
        assert route.runs == {}

    def test_a_refused_finalize_abandons_the_run(self, resource, route, monkeypatch):
        small_chunks(monkeypatch, route, 2)
        route.refuse = {3: _error(422, "unresolved owner")}

        with pytest.raises(ValidationError):
            publish(resource, frame(5))

        assert route.deletes == ["run-1"]

    def test_an_abandon_that_fails_leaves_the_refusal_raised(
        self, resource, route, monkeypatch
    ):
        small_chunks(monkeypatch, route, 2)
        route.refuse = {2: _error(422, "no")}
        original = route.request

        def no_delete(method, url, **kwargs):
            if method == "DELETE":
                route.deletes.append(url)
                return _error(500, "boom")
            return original(method, url, **kwargs)

        monkeypatch.setattr(_http.requests, "request", no_delete)

        with pytest.raises(ValidationError):
            publish(resource, frame(5))

        assert len(route.deletes) == 1
        assert route.only_run()["status"] == "importing"

    def test_a_403_names_the_import_and_the_role(self, resource, route):
        from mascope_sdk.exceptions import PermissionDeniedError

        route.refuse = {1: _error(403, "Access denied.")}

        with pytest.raises(PermissionDeniedError) as caught:
            publish(resource, frame())

        assert caught.value.operation == "import an assignment run into"
        assert caught.value.object_id == SAMPLE_ID
        assert "editor on the sample" in caught.value.required_role


# --- abandon_import -----------------------------------------------------------------


class TestAbandonImport:
    def test_it_deletes_an_importing_run(self, resource, route, monkeypatch):
        small_chunks(monkeypatch, route, 2)
        route.lose = {3: "timeout"}
        with pytest.raises(MascopeConnectionError):
            publish(resource, frame(5))
        route.only_run()["status"] = "importing"  # the finalize never arrived

        state = resource.abandon_import(SAMPLE_ID, "run-1")

        assert state["peak_assignment_run_id"] == "run-1"
        assert state["rows"] == 5
        assert route.runs == {}

    def test_a_completed_run_is_refused(self, resource, route):
        publish(resource, frame())

        with pytest.raises(ConflictError):
            resource.abandon_import(SAMPLE_ID, "run-1")

        assert route.only_run()["status"] == "completed"

    def test_a_403_names_the_abandon_and_the_role(self, resource, route, monkeypatch):
        from mascope_sdk.exceptions import PermissionDeniedError

        monkeypatch.setattr(
            _http.requests, "request", lambda *a, **k: _error(403, "Access denied.")
        )

        with pytest.raises(PermissionDeniedError) as caught:
            resource.abandon_import(SAMPLE_ID, "run-1")

        assert caught.value.operation == "abandon an import into"
        assert "editor on the sample" in caught.value.required_role

    def test_it_is_sent_once(self, resource, route, monkeypatch):
        calls = []

        def lost(method, url, **kwargs):
            calls.append(method)
            raise requests.exceptions.ReadTimeout("read timed out")

        monkeypatch.setattr(_http.requests, "request", lost)

        with pytest.raises(MascopeConnectionError, match="no longer lists"):
            resource.abandon_import(SAMPLE_ID, "run-1")

        assert calls == ["DELETE"]


# --- the coverage warning -------------------------------------------------------------


class TestCoverageWarning:
    @pytest.fixture
    def warnings(self):
        from loguru import logger

        caught: list[str] = []
        handle = logger.add(lambda m: caught.append(str(m)), level="WARNING")
        yield caught
        logger.remove(handle)

    def test_a_frame_well_short_of_the_latest_run_is_warned_about(
        self, resource, route, warnings
    ):
        route.latest_rows = 100

        publish(resource, frame(3))

        assert any("holds 3 row(s)" in w and "holds 100" in w for w in warnings)
        assert route.only_run()["status"] == "completed", "published all the same"

    def test_a_frame_near_the_latest_run_is_not(self, resource, route, warnings):
        route.latest_rows = 3

        publish(resource, frame(3))

        assert not [w for w in warnings if "batch overview" in w]

    def test_the_line_is_the_share_of_the_latest_run(self, resource, route, warnings):
        route.latest_rows = 10

        publish(resource, frame(9))
        assert not [w for w in warnings if "batch overview" in w]
        publish(resource, frame(8))
        assert [w for w in warnings if "batch overview" in w]

    def test_the_run_measured_against_is_the_latest_completed(
        self, resource, route, warnings
    ):
        route.latest_rows = 100
        route.newer_failed = True

        publish(resource, frame(3))

        assert any("holds 100" in w for w in warnings)

    def test_a_sample_with_no_run_is_not(self, resource, route, warnings):
        publish(resource, frame(3))

        assert not [w for w in warnings if "batch overview" in w]


# --- the frame's checks -----------------------------------------------------------------


class TestRequiredColumns:
    @pytest.mark.parametrize(
        "column",
        ["sample_peak_id", "sample_peak_mz", "sample_peak_intensity", "role"],
    )
    def test_a_missing_required_column_is_refused(self, resource, route, column):
        error = refused(resource, frame().drop(columns=[column]), route)

        assert error.column == column

    def test_an_empty_frame_is_refused(self, resource, route):
        refused(resource, frame().iloc[0:0], route)

    def test_a_frame_is_required(self, resource, route):
        with pytest.raises(TypeError):
            publish(resource, [{"sample_peak_id": "p0"}])

    @pytest.mark.parametrize("column", ["sample_peak_id", "role"])
    def test_a_null_id_or_role_is_refused(self, resource, route, column):
        rows = frame()
        rows.loc[1, column] = None

        error = refused(resource, rows, route)

        assert (error.column, error.rows) == (column, [1])

    def test_a_peak_twice_is_refused(self, resource, route):
        rows = frame(sample_peak_id=["p0", "p1", "p0"])

        error = refused(resource, rows, route)

        assert (error.column, error.rows) == ("sample_peak_id", [0, 2])

    def test_peak_ids_go_as_strings(self, resource, route):
        publish(resource, frame(sample_peak_id=[10, 11, 12]))

        assert [r["sample_peak_id"] for r in route.posts[0]["rows"]] == [
            "10",
            "11",
            "12",
        ]


class TestMissingValues:
    @pytest.mark.parametrize("column", ["sample_peak_mz", "sample_peak_intensity"])
    def test_nan_in_a_non_null_column_is_refused_naming_the_row(
        self, resource, route, column
    ):
        rows = frame()
        rows.loc[2, column] = float("nan")

        error = refused(resource, rows, route)

        assert (error.column, error.rows) == (column, [2])
        assert column in str(error) and "2" in str(error)

    @pytest.mark.parametrize(
        "column",
        [
            "fit_score",
            "mz_error_ppm",
            "abundance_error",
            "sample_peak_tof",
            "assigned_formula",
            "ion_formula",
            "isotope_formula",
            "isotope_label",
            "target_compound_id",
            "target_ion_id",
            "ionization_mechanism_id",
            "source",
            "engine_tier",
            "alternatives",
            "provenance",
        ],
    )
    def test_nan_in_a_nullable_column_goes_as_null(self, resource, route, column):
        rows = frame()
        if column not in rows.columns:
            rows[column] = None
        rows[column] = rows[column].astype(object)
        rows.loc[1, column] = float("nan")

        publish(resource, rows)

        sent = route.posts[0]["rows"][1]
        assert sent[column] is None

    @pytest.mark.parametrize(
        "column", ["sample_peak_mz", "fit_score", "mz_error_ppm", "sample_peak_tof"]
    )
    def test_an_infinite_number_is_refused(self, resource, route, column):
        rows = frame()
        rows[column] = 0.5
        rows.loc[0, column] = float("inf")

        error = refused(resource, rows, route)

        assert (error.column, error.rows) == (column, [0])

    def test_numbers_go_as_numbers(self, resource, route):
        publish(
            resource,
            frame(sample_peak_intensity=[1, 2, 3], mz_error_ppm=["1.5", "-0.25", "2"]),
        )

        sent = route.posts[0]["rows"]
        assert [r["mz_error_ppm"] for r in sent] == [1.5, -0.25, 2.0]
        assert [type(r["sample_peak_intensity"]) for r in sent] == [float] * 3

    def test_a_number_column_holding_text_is_refused(self, resource, route):
        error = refused(resource, frame(mz_error_ppm=["1.2", "high", "0.3"]), route)

        assert error.column == "mz_error_ppm"

    @pytest.mark.parametrize("value", [-0.1, 1.2])
    def test_a_fit_score_outside_0_to_1_is_refused(self, resource, route, value):
        rows = frame()
        rows.loc[1, "fit_score"] = value

        error = refused(resource, rows, route)

        assert (error.column, error.rows) == ("fit_score", [1])

    def test_provenance_no_json_can_carry_is_refused(self, resource, route):
        rows = frame(
            provenance=[None, {"engine_provenance": {"x": float("nan")}}, None]
        )

        error = refused(resource, rows, route)

        assert error.rows == [1]


class TestVocabularies:
    @pytest.mark.parametrize(
        "column, value",
        [
            ("role", "parent"),
            ("source", "library"),
            ("tier", "confident"),
            ("engine_tier", "maybe"),
        ],
    )
    def test_a_value_outside_the_vocabulary_is_refused(
        self, resource, route, column, value
    ):
        rows = frame()
        if column not in rows.columns:
            rows[column] = None
        rows[column] = rows[column].astype(object)
        rows.loc[2, column] = value

        error = refused(resource, rows, route)

        assert (error.column, error.rows) == (column, [2])

    @pytest.mark.parametrize("column", ["tier", "engine_tier"])
    def test_the_legacy_tier_spelling_goes_as_the_current_one(
        self, resource, route, column
    ):
        publish(resource, frame(**{column: ["identified", None, "candidate"]}))

        sent = [r[column] for r in route.posts[0]["rows"]]
        assert sent == ["assigned", None, "candidate"]

    @pytest.mark.parametrize(
        "source", ["database", "untargeted", "manual", "reagent", "artifact"]
    )
    def test_every_source_the_server_reads_goes(self, resource, route, source):
        publish(resource, frame(source=[source] * 3))


class TestOwners:
    def owned(self, **changes) -> pd.DataFrame:
        rows = frame(4)
        rows["role"] = ["M0", "iso_child", "M0", "iso_child"]
        rows["owner_sample_peak_id"] = [None, "p0", None, "p2"]
        for column, values in changes.items():
            rows[column] = values
        return rows

    def test_an_isotopologue_naming_its_owner_row_goes(self, resource, route):
        publish(resource, self.owned())

        owners = [r["owner_sample_peak_id"] for r in route.posts[0]["rows"]]
        assert owners == [None, "p0", None, "p2"]

    def test_an_owner_outside_the_frame_is_refused(self, resource, route):
        error = refused(
            resource, self.owned(owner_sample_peak_id=[None, "p9", None, "p2"]), route
        )

        assert (error.column, error.rows) == ("owner_sample_peak_id", [1])

    def test_an_owner_on_a_row_not_an_iso_child_is_refused(self, resource, route):
        error = refused(
            resource, self.owned(role=["M0", "M0", "M0", "iso_child"]), route
        )

        assert error.rows == [1]

    def test_a_row_owning_itself_is_refused(self, resource, route):
        error = refused(
            resource, self.owned(owner_sample_peak_id=[None, "p1", None, "p2"]), route
        )

        assert error.rows == [1]
        assert "names its own row" in str(error)

    def test_an_iso_child_as_owner_is_refused(self, resource, route):
        error = refused(
            resource, self.owned(owner_sample_peak_id=[None, "p0", None, "p1"]), route
        )

        assert error.rows == [3]

    def test_an_exported_ledgers_owner_ids_go_as_peak_references(self, resource, route):
        rows = self.owned().drop(columns=["owner_sample_peak_id"])
        rows["peak_assignment_id"] = ["a0", "a1", "a2", "a3"]
        rows["owner_peak_assignment_id"] = [None, "a0", None, "a2"]

        publish(resource, rows)

        sent = route.posts[0]["rows"]
        assert [r["owner_sample_peak_id"] for r in sent] == [None, "p0", None, "p2"]
        assert all("owner_peak_assignment_id" not in r for r in sent)

    def test_stated_peak_references_win_over_exported_owner_ids(self, resource, route):
        rows = self.owned()
        rows["peak_assignment_id"] = ["a0", "a1", "a2", "a3"]
        rows["owner_peak_assignment_id"] = [None, "a2", None, "a0"]

        publish(resource, rows)

        sent = route.posts[0]["rows"]
        assert [r["owner_sample_peak_id"] for r in sent] == [None, "p0", None, "p2"]

    def test_an_exported_owner_id_outside_the_frame_is_refused(self, resource, route):
        rows = self.owned().drop(columns=["owner_sample_peak_id"])
        rows["peak_assignment_id"] = ["a0", "a1", "a2", "a3"]
        rows["owner_peak_assignment_id"] = [None, "a0", None, "a7"]

        error = refused(resource, rows, route)

        assert (error.column, error.rows) == ("owner_peak_assignment_id", [3])


class TestAnExportedLedger:
    """A frame read with ``get()`` goes back in as it is."""

    def exported(self, **changes) -> pd.DataFrame:
        """An in-app run's ledger as ``get()`` returns it: a monoisotopic row,
        its isotopologue, a demoted row and a reagent's isotope line."""
        rows = frame(5)
        rows["role"] = ["M0", "iso_child", "M0", "reagent", "reagent"]
        rows["source"] = ["database", "database", "untargeted", "reagent", "reagent"]
        rows["peak_assignment_id"] = ["a0", "a1", "a2", "a3", "a4"]
        rows["peak_assignment_run_id"] = "run-in-app"
        rows["sample_item_id"] = SAMPLE_ID
        rows["owner_peak_assignment_id"] = [None, "a0", None, None, "a3"]
        # The third row is held below what its fit earns, as an in-app pass
        # holds some, and says why in its tier_reasons.
        rows["tier"] = ["assigned", "assigned", "candidate", "assigned", "assigned"]
        rows["engine_tier"] = None
        rows["evidence"] = 0.9
        rows["p_correct"] = 0.8
        rows["batch_peak_id"] = ["bp0", "bp1", None, "bp3", "bp4"]
        rows["known_compounds"] = [[{"name": "x"}], None, None, None, None]
        rows["known_compounds_total"] = [1, 0, 0, 0, 0]
        rows["reference_listing"] = None
        for column, values in changes.items():
            rows[column] = values
        return rows

    @pytest.fixture
    def warnings(self):
        from loguru import logger

        caught: list[str] = []
        handle = logger.add(caught.append, level="WARNING")
        yield caught
        logger.remove(handle)

    def test_it_goes_with_no_warning_and_only_row_fields(
        self, resource, route, warnings
    ):
        publish(resource, self.exported())

        assert warnings == []
        sent = route.posts[0]["rows"]
        assert set(sent[0]) <= set(_assignment_import.ROW_COLUMNS)
        assert all("batch_peak_id" not in row for row in sent)

    @pytest.mark.parametrize("stated", [True, False], ids=["null", "no column"])
    def test_its_tier_goes_as_the_engine_tier(self, resource, route, stated):
        rows = self.exported()
        if not stated:
            rows = rows.drop(columns=["engine_tier"])

        publish(resource, rows)

        sent = route.posts[0]["rows"]
        assert all("tier" not in row for row in sent)
        assert [row["engine_tier"] for row in sent] == [
            "assigned",
            "assigned",
            "candidate",
            "assigned",
            "assigned",
        ]

    def test_an_imported_runs_engine_tier_is_kept(self, resource, route):
        # A run an engine published, read back: its engine tiers are its own,
        # null where it stated none, and its tier is the server's.
        publish(
            resource,
            self.exported(engine_tier=["candidate", None, "assigned", None, None]),
        )

        sent = route.posts[0]["rows"]
        assert all("tier" not in row for row in sent)
        assert [row["engine_tier"] for row in sent] == [
            "candidate",
            None,
            "assigned",
            None,
            None,
        ]

    def test_a_frame_not_read_keeps_its_tier(self, resource, route):
        publish(resource, frame(1, tier=["candidate"], engine_tier=[None]))

        (row,) = route.posts[0]["rows"]
        assert (row["tier"], row["engine_tier"]) == ("candidate", None)

    def test_owner_links_go_from_isotopologues_only(self, resource, route):
        publish(resource, self.exported())

        sent = route.posts[0]["rows"]
        assert [row["owner_sample_peak_id"] for row in sent] == [
            None,
            "p0",
            None,
            None,
            None,
        ]

    def test_a_ledger_of_reagent_links_alone_sends_none(self, resource, route):
        publish(
            resource,
            self.exported(owner_peak_assignment_id=[None, None, None, None, "a3"]),
        )

        assert all("owner_sample_peak_id" not in row for row in route.posts[0]["rows"])

    def test_a_reagent_link_outside_the_frame_is_not_refused(self, resource, route):
        # It is not sent, so it names nothing the import has to find.
        publish(
            resource,
            self.exported(owner_peak_assignment_id=[None, "a0", None, None, "a9"]),
        )

        assert route.only_run()["status"] == "completed"


class TestTiers:
    def tiered(self, tier, fit, formula="C6H12O6") -> pd.DataFrame:
        return frame(1, tier=[tier], fit_score=[fit], assigned_formula=[formula])

    @pytest.mark.parametrize(
        "tier, fit",
        [
            ("assigned", 0.75),
            ("assigned", 0.9),
            ("candidate", 0.9),  # a demotion the formula may explain
            ("below_assignability", 0.9),
            ("candidate", 0.45),
            ("below_assignability", 0.1),
            ("below_assignability", 0.0),
            ("assigned", 0.74996),  # the server rounds the product to 4 places
        ],
    )
    def test_a_tier_the_fit_can_reach_goes(self, resource, route, tier, fit):
        publish(resource, self.tiered(tier, fit))

        assert route.posts[0]["rows"][0]["tier"] == tier

    @pytest.mark.parametrize(
        "tier, fit",
        [
            ("assigned", 0.74),
            ("candidate", 0.44),
            ("assigned", 0.0),
            ("unassigned", 0.9),
            ("unassigned", 0.1),
        ],
    )
    def test_a_tier_the_fit_cannot_reach_is_refused(self, resource, route, tier, fit):
        error = refused(resource, self.tiered(tier, fit), route)

        assert (error.column, error.rows) == ("tier", [0])
        assert "0.75" in str(error) and "0.45" in str(error)

    @pytest.mark.parametrize("tier", ["unassigned", "below_assignability"])
    @pytest.mark.parametrize("fit, formula", [(None, "C6H12O6"), (0.9, None)])
    def test_a_row_with_no_evidence_may_be_unassigned_or_below(
        self, resource, route, tier, fit, formula
    ):
        publish(resource, self.tiered(tier, fit, formula))

    @pytest.mark.parametrize("tier", ["assigned", "candidate"])
    @pytest.mark.parametrize("fit, formula", [(None, "C6H12O6"), (0.9, None)])
    def test_a_row_with_no_evidence_cannot_be_banded(
        self, resource, route, tier, fit, formula
    ):
        refused(resource, self.tiered(tier, fit, formula), route)

    def test_no_fit_reaches_a_band_set_at_zero(self, resource, route):
        # The server bands evidence of 0 below any band, a zero one included.
        bands = {"assigned": 0.5, "candidate": 0.0}

        refused(resource, self.tiered("candidate", 0.0), route, tier_bands=bands)
        publish(resource, self.tiered("candidate", 0.01), tier_bands=bands)

    def test_bands_the_frame_is_checked_under(self, resource, route):
        rows = self.tiered("assigned", 0.62)

        refused(resource, rows, route)
        publish(resource, rows, tier_bands={"assigned": 0.6, "candidate": 0.3})

    def test_a_frame_without_tier_is_not_checked(self, resource, route):
        publish(resource, frame(fit_score=[0.0, 0.0, 0.0]))

        assert "tier" not in route.posts[0]["rows"][0]

    def test_a_null_tier_is_left_to_the_server(self, resource, route):
        publish(resource, frame(tier=[None, "assigned", None]))

        assert [r["tier"] for r in route.posts[0]["rows"]] == [None, "assigned", None]

    def test_engine_tier_is_checked_against_nothing_but_its_vocabulary(
        self, resource, route
    ):
        publish(resource, frame(engine_tier=["assigned"] * 3, fit_score=[0.1] * 3))


class TestBands:
    @pytest.mark.parametrize(
        "bands",
        [
            {"assigned": 0.75},
            {"candidate": 0.45},
            {"assigned": 1.2, "candidate": 0.45},
            {"assigned": 0.75, "candidate": -0.1},
            {"assigned": "high", "candidate": 0.45},
            {"assigned": True, "candidate": 0.45},
            {"assigned": 0.4, "candidate": 0.45},
            [0.75, 0.45],
        ],
    )
    def test_bands_the_server_would_refuse_are_refused(self, resource, route, bands):
        refused(resource, frame(), route, tier_bands=bands)

    def test_the_legacy_band_name_goes_as_the_current_one(self, resource, route):
        publish(resource, frame(), tier_bands={"identified": 0.8, "candidate": 0.5})

        assert route.posts[0]["tier_bands"] == {"assigned": 0.8, "candidate": 0.5}

    def test_the_current_band_name_wins_over_the_legacy_one(self, resource, route):
        publish(
            resource,
            frame(),
            tier_bands={"assigned": 0.8, "identified": 0.9, "candidate": 0.5},
        )

        assert route.posts[0]["tier_bands"]["assigned"] == 0.8

    def test_calibration_is_required_as_a_mapping(self, resource, route):
        refused(resource, frame(), route, calibration=None)


class TestAdducts:
    def test_an_adduct_resolves_by_its_exact_notation(self, resource, route):
        from loguru import logger

        warnings: list[str] = []
        handle = logger.add(lambda m: warnings.append(str(m)), level="WARNING")
        try:
            publish(resource, frame(adduct=["[M+H]+", "[M+Na]+", "[M+H]+"]))
        finally:
            logger.remove(handle)

        assert not [w for w in warnings if "not row fields" in w]

        sent = route.posts[0]["rows"]
        assert [r["ionization_mechanism_id"] for r in sent] == [
            "mech-h",
            "mech-na",
            "mech-h",
        ]
        assert all("adduct" not in r for r in sent)

    @pytest.mark.parametrize(
        "notation",
        [
            "+H+",  # a legacy spelling
            "[M+H]",  # no charge
            "[m+h]+",  # another case
            "[M+Cl]-",  # no such mechanism
            "[M+K]+",  # two mechanisms share it
        ],
    )
    def test_anything_but_one_exact_match_goes_as_null(self, resource, route, notation):
        publish(resource, frame(adduct=["[M+H]+", notation, None]))

        sent = [r["ionization_mechanism_id"] for r in route.posts[0]["rows"]]
        assert sent == ["mech-h", None, None]

    def test_a_given_mechanism_id_is_kept(self, resource, route):
        publish(
            resource,
            frame(
                adduct=["[M+H]+", "[M+H]+", "[M+H]+"],
                ionization_mechanism_id=["mech-na", None, "mech-own"],
            ),
        )

        sent = [r["ionization_mechanism_id"] for r in route.posts[0]["rows"]]
        assert sent == ["mech-na", "mech-h", "mech-own"]

    def test_a_server_listing_no_mechanism_resolves_nothing(self, resource, route):
        route.mechanisms = []

        publish(resource, frame(adduct=["[M+H]+", None, None]))

        assert route.posts[0]["rows"][0]["ionization_mechanism_id"] is None

    def test_mechanisms_are_read_only_for_an_adduct_to_resolve(self, resource, route):
        publish(resource, frame())
        publish(resource, frame(adduct=[None] * 3))
        assert route.mechanism_reads == 0

        publish(resource, frame(adduct=["[M+H]+", None, None]))
        assert route.mechanism_reads == 1
