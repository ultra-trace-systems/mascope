"""
Hermetic tests of the peak-assignment writes: ``assign``, ``wait_for_run``,
``verify``, ``list_verifications`` and ``fit_aggregate``, and the HTTP
plumbing under them.

A fake server stands in for ``requests`` itself, so what is pinned is the
whole path from the method to the wire: the request sent, how often it is
sent, and what each answer the server can give - 202, 409, 422, a lost answer
- becomes. The server keeps what a write stores, which is what the lost-answer
tests count: a write whose answer is lost must have been stored once, not once
per retry. ``test_contract.py`` sends the same calls to a live server.
"""

import json
from typing import Any

import pandas as pd
import pytest
import requests

from mascope_sdk import _http
from mascope_sdk.exceptions import (
    AssignmentInFlightError,
    AssignmentRunError,
    AssignmentTimeoutError,
    ConflictError,
    MascopeAPIError,
    MascopeConnectionError,
    MascopeTimeoutError,
    PermissionDeniedError,
    SampleIneligibleError,
    ServerError,
    ValidationError,
)
from mascope_sdk.resources import peak_assignments as module
from mascope_sdk.resources.peak_assignments import PeakAssignmentsResource


SAMPLE_ID = "sample-1"
BASE = "http://server/api/peak-assignments/sample/"


def _response(status: int, body: Any) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(body).encode()
    return response


def _error(status: int, message: str, detail: dict[str, Any]) -> requests.Response:
    """An error answer in the backend's shape."""
    return _response(status, {"error": message, "detail": {**detail, "error_id": "e"}})


class FakeServer:
    """The peak-assignment routes, behind ``requests.request`` and
    ``requests.get``.

    ``statuses`` is what each look at the launched run's listing reads, in
    order, the last one repeating. ``lose`` names a write whose answer is lost
    after the server stored it, the way a read timeout or a proxy's 502 to
    504 loses one: ``"timeout"``, ``"connection"``, ``"502"``, ``"503"`` or
    ``"504"``. ``refuse`` answers the
    assign route with a canned response instead.
    """

    def __init__(self):
        self.runs: list[dict[str, Any]] = []
        self.verifications: list[dict[str, Any]] = []
        self.statuses: list[str] = ["completed"]
        self.run_error: str | None = None
        self.lose: str | None = None
        self.refuse: requests.Response | None = None
        self.listing_fails = 0
        self.requests: list[dict[str, Any]] = []
        self.listings = 0

    # --- the wire -----------------------------------------------------------

    def request(self, method, url, json=None, params=None, **kwargs):
        path = url.removeprefix(BASE)
        self.requests.append({"method": method, "path": path, "json": json})
        if path.endswith("/assign"):
            if self.refuse is not None:
                return self.refuse
            run_id = f"run-{len(self.runs) + 1}"
            self.runs.append(
                {
                    "peak_assignment_run_id": run_id,
                    "sample_item_id": SAMPLE_ID,
                    "engine": "mascope",
                    "engine_version": "1.0.0",
                    "status": "pending",
                    "config": json.get("config") if json else None,
                    "error": None,
                    "peak_assignment_run_utc_created": "2026-10-28T10:00:00Z",
                    "peak_assignment_run_utc_completed": None,
                }
            )
            body = {
                "status": "success",
                "message": "Assigning peaks, please wait.",
                "results": 1,
                "data": [
                    {
                        "sample_item_id": SAMPLE_ID,
                        "peak_assignment_run_id": run_id,
                        "run_status": "pending",
                    }
                ],
            }
            return self._answer(_response(202, body))
        if path.endswith("/verify"):
            record = {
                "assignment_verification_id": f"v-{len(self.verifications) + 1}",
                "sample_item_id": SAMPLE_ID,
                "sample_peak_id": "peak-1",
                **json,
                "fit_score": 0.9,
                "verified_utc": "2026-10-28T10:00:00Z",
                "superseded_utc": None,
            }
            self.verifications.append(record)
            body = {"status": "success", "message": "ok", "results": 1}
            return self._answer(_response(201, {**body, "data": [record]}))
        if path.endswith("/fit/aggregate"):
            if self.lose == "503-once":
                self.lose = None
                return _error(503, "Server is busy", {})
            body = {
                "message": "Fit retrieved",
                "data": {
                    "matches": {"match_ions": 1, "match_isotopes": 1},
                    "match_ions": [{"target_ion_formula": json["assigned_formula"]}],
                    "match_isotopes": [{"mz": 181.07}],
                },
            }
            return _response(200, body)
        raise AssertionError(f"unexpected {method} {url}")

    def _answer(self, response: requests.Response) -> requests.Response:
        """The answer, or its loss once the write is stored."""
        lose, self.lose = self.lose, None
        if lose == "timeout":
            raise requests.exceptions.ReadTimeout("read timed out")
        if lose == "connection":
            raise requests.exceptions.ConnectionError("connection reset")
        if lose in ("502", "503", "504"):
            return _error(int(lose), "Bad Gateway", {})
        return response

    def get(self, url, params=None, **kwargs):
        path = url.removeprefix(BASE)
        if path.endswith("/runs"):
            self.listings += 1
            if self.listing_fails:
                self.listing_fails -= 1
                raise requests.exceptions.ConnectionError("unreachable")
            for run in self.runs:
                status = self.statuses[min(self.listings - 1, len(self.statuses) - 1)]
                run["status"] = status
                run["error"] = self.run_error if status == "failed" else None
            return _response(200, {"message": "runs", "data": self.runs})
        if path.endswith("/verifications"):
            return _response(200, {"message": "ok", "data": self.verifications})
        # The paged ledger of a run.
        run_id = params["peak_assignment_run_id"]
        rows = [
            {
                "peak_assignment_id": f"{run_id}-a{i}",
                "peak_assignment_run_id": run_id,
                "sample_peak_id": f"peak-{i}",
                "sample_peak_mz": 100.0 + i,
                "tier": "assigned",
            }
            for i in range(3)
        ]
        return _response(200, {"message": "ok", "total": len(rows), "data": rows})

    # --- what a test reads ----------------------------------------------------

    def sent(self, suffix: str) -> list[dict[str, Any]]:
        return [r for r in self.requests if r["path"].endswith(suffix)]


class _StubClient:
    """The attributes a resource reads off its client, and a cache to clear."""

    url = "http://server"
    access_token = "token"
    _timeout = (1, 5)
    _verify_ssl = False
    _service_name = "mascope_sdk"

    def clear_cache(self) -> None:
        pass


class _Clock:
    """``time.monotonic`` and ``time.sleep`` of the polling loop, without
    waiting: each sleep moves the clock on."""

    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def server(monkeypatch) -> FakeServer:
    fake = FakeServer()
    monkeypatch.setattr(_http.requests, "request", fake.request)
    monkeypatch.setattr(_http.requests, "get", fake.get)
    # The HTTP layer's backoff between retries: recorded, never waited.
    backoff: list[float] = []
    monkeypatch.setattr(_http.time, "sleep", backoff.append)
    fake.backoff = backoff
    return fake


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    # The module's own name for ``time``, not ``time.sleep`` itself: the HTTP
    # layer's backoff sleeps through the same module, and is counted apart.
    fake = _Clock()
    monkeypatch.setattr(module, "time", fake)
    return fake


@pytest.fixture
def resource(server, clock) -> PeakAssignmentsResource:
    return PeakAssignmentsResource(_StubClient())


# --- the HTTP plumbing --------------------------------------------------------


class TestErrorBody:
    """The parsed body survives onto the exception, its code included."""

    def test_the_detail_and_its_code_are_kept(self):
        response = _error(422, "No.", {"code": "sample_ineligible", "reason": "blank"})

        with pytest.raises(ValidationError) as caught:
            _http._raise_for_status(response, "http://server/api/x")

        assert caught.value.message == "No."
        assert caught.value.code == "sample_ineligible"
        assert caught.value.detail["reason"] == "blank"
        assert caught.value.body["error"] == "No."

    def test_a_409_is_a_conflict_carrying_its_detail(self):
        response = _error(409, "Busy.", {"peak_assignment_run_id": "run-1"})

        with pytest.raises(ConflictError) as caught:
            _http._raise_for_status(response, "http://server/api/x")

        assert caught.value.detail["peak_assignment_run_id"] == "run-1"
        # A conflict is not retried: the state holding it has to change first.
        assert not _http._is_retryable(caught.value)

    @pytest.mark.parametrize("status", [401, 403, 404, 413, 503])
    def test_every_status_keeps_the_body(self, status):
        response = _error(status, "No.", {"code": "some_code"})

        with pytest.raises(MascopeAPIError) as caught:
            _http._raise_for_status(response, "http://server/api/x")

        assert caught.value.code == "some_code"

    def test_a_body_that_is_not_json_leaves_no_detail(self):
        response = requests.Response()
        response.status_code = 502
        response._content = b"<html>Bad Gateway</html>"

        with pytest.raises(ServerError) as caught:
            _http._raise_for_status(response, "http://server/api/x")

        assert caught.value.body is None
        assert caught.value.detail == {}
        assert caught.value.code is None

    def test_a_refused_write_keeps_the_body_beside_what_it_needs(
        self, resource, server
    ):
        server.refuse = _error(403, "Peak assignment is disabled.", {"code": "off"})

        with pytest.raises(PermissionDeniedError) as caught:
            resource.assign(SAMPLE_ID)

        assert caught.value.code == "off"
        assert caught.value.object_id == SAMPLE_ID
        assert "editor on the sample" in caught.value.required_role
        assert "peak assignment enabled" in caught.value.required_role


class TestAttempts:
    """A POST is retried unless its caller says it may be sent only once."""

    def _unavailable(self, monkeypatch, method: str) -> list[float]:
        sent: list[str] = []
        backoff: list[float] = []
        monkeypatch.setattr(
            _http.requests,
            method,
            lambda *a, **k: sent.append(method) or _error(503, "Busy.", {}),
        )
        monkeypatch.setattr(_http.time, "sleep", backoff.append)
        return sent, backoff

    def test_a_post_is_retried_by_default(self, monkeypatch):
        sent, backoff = self._unavailable(monkeypatch, "post")

        with pytest.raises(ServerError):
            _http.http_post("http://server", "x", "token", {})

        assert len(sent) == _http.RETRY_MAX_ATTEMPTS
        assert len(backoff) == _http.RETRY_MAX_ATTEMPTS - 1

    def test_a_post_sent_once_is_sent_once(self, monkeypatch):
        sent, backoff = self._unavailable(monkeypatch, "post")

        with pytest.raises(ServerError):
            _http.http_post("http://server", "x", "token", {}, max_attempts=1)

        assert sent == ["post"]
        assert backoff == []

    def test_a_request_sent_once_is_sent_once(self, monkeypatch):
        sent, backoff = self._unavailable(monkeypatch, "request")

        with pytest.raises(ServerError):
            _http.http_request("POST", "http://server", "x", "token", max_attempts=1)

        assert sent == ["request"]
        assert backoff == []


# --- assign -------------------------------------------------------------------


class TestAssignLaunch:
    def test_without_waiting_it_returns_the_new_run_id(self, resource, server):
        run_id = resource.assign(SAMPLE_ID, wait=False)

        assert run_id == "run-1"
        assert server.listings == 0
        (sent,) = server.sent("/assign")
        assert sent["method"] == "POST"
        assert sent["path"] == f"{SAMPLE_ID}/assign"
        assert sent["json"] == {}

    def test_the_config_is_sent_as_given(self, resource, server):
        config = {"run_untargeted": False, "assigned_threshold": 0.8}

        resource.assign(SAMPLE_ID, config=config, wait=False)

        assert server.sent("/assign")[0]["json"] == {"config": config}

    def test_it_waits_for_the_run_and_returns_its_ledger(self, resource, server, clock):
        server.statuses = ["pending", "running", "completed"]

        ledger = resource.assign(SAMPLE_ID, poll_interval=2.0)

        assert isinstance(ledger, pd.DataFrame)
        assert set(ledger["peak_assignment_run_id"]) == {"run-1"}
        assert ledger.attrs["run"]["peak_assignment_run_id"] == "run-1"
        assert ledger.attrs["run"]["status"] == "completed"
        # Two looks found it going, each followed by one pause.
        assert clock.sleeps == [2.0, 2.0]

    def test_a_failed_run_raises_with_its_error(self, resource, server):
        server.statuses = ["running", "failed"]
        server.run_error = "engine stopped"

        with pytest.raises(AssignmentRunError) as caught:
            resource.assign(SAMPLE_ID)

        assert caught.value.peak_assignment_run_id == "run-1"
        assert caught.value.status == "failed"
        assert caught.value.error == "engine stopped"
        assert "engine stopped" in str(caught.value)

    def test_a_cancelled_run_raises_rather_than_reading_empty(self, resource, server):
        server.statuses = ["cancelled"]

        with pytest.raises(AssignmentRunError) as caught:
            resource.assign(SAMPLE_ID)

        assert caught.value.status == "cancelled"
        assert caught.value.error is None

    def test_a_wait_that_runs_out_hands_back_the_run_id(self, resource, server, clock):
        server.statuses = ["running"]

        with pytest.raises(AssignmentTimeoutError) as caught:
            resource.assign(SAMPLE_ID, poll_interval=4.0, timeout=10.0)

        assert caught.value.peak_assignment_run_id == "run-1"
        assert caught.value.status == "running"
        # The last pause is cut to what is left of the wait.
        assert clock.sleeps == [4.0, 4.0, 2.0]
        # The run was launched once and left going.
        assert len(server.sent("/assign")) == 1


class TestAssignRefused:
    """A refusal is decided before the answer: an error, never a wait."""

    def test_a_run_in_flight_is_named(self, resource, server):
        server.refuse = _error(
            409,
            "Peak assignment is already running for sample 'S'.",
            {
                "code": "assignment_in_flight",
                "sample_item_id": SAMPLE_ID,
                "peak_assignment_run_id": "run-other",
            },
        )

        with pytest.raises(AssignmentInFlightError) as caught:
            resource.assign(SAMPLE_ID)

        assert isinstance(caught.value, ConflictError)
        assert caught.value.peak_assignment_run_id == "run-other"
        assert caught.value.sample_id == SAMPLE_ID
        assert server.listings == 0
        assert len(server.sent("/assign")) == 1

    def test_an_ineligible_sample_names_its_reason(self, resource, server):
        server.refuse = _error(
            422,
            "Peak assignment is not possible for sample 'S': blank sample.",
            {
                "code": "sample_ineligible",
                "sample_item_id": SAMPLE_ID,
                "reason": "blank sample (no peaks)",
            },
        )

        with pytest.raises(SampleIneligibleError) as caught:
            resource.assign(SAMPLE_ID)

        assert isinstance(caught.value, ValidationError)
        assert caught.value.reason == "blank sample (no peaks)"
        assert server.listings == 0

    def test_a_server_from_before_the_code_is_read_by_its_reason(
        self, resource, server
    ):
        server.refuse = _error(
            422, "Not possible.", {"sample_item_id": SAMPLE_ID, "reason": "blank"}
        )

        with pytest.raises(SampleIneligibleError) as caught:
            resource.assign(SAMPLE_ID)

        assert caught.value.reason == "blank"

    def test_a_config_the_server_refuses_stays_a_validation_error(
        self, resource, server
    ):
        server.refuse = _error(422, "max_alternatives: must be >= 0", {})

        with pytest.raises(ValidationError) as caught:
            resource.assign(SAMPLE_ID, config={"max_alternatives": -1})

        assert not isinstance(caught.value, SampleIneligibleError)

    def test_another_code_is_not_read_as_ineligible(self, resource, server):
        server.refuse = _error(422, "No.", {"code": "other", "reason": "x"})

        with pytest.raises(ValidationError) as caught:
            resource.assign(SAMPLE_ID)

        assert not isinstance(caught.value, SampleIneligibleError)


class TestAssignAnswerLost:
    """The launch is stored and its answer lost: sent once, said loudly."""

    @pytest.mark.parametrize(
        "lose, raised",
        [
            ("timeout", MascopeTimeoutError),
            ("connection", MascopeConnectionError),
            ("502", ServerError),
            ("503", ServerError),
            ("504", ServerError),
        ],
    )
    def test_it_is_not_sent_again(self, resource, server, lose, raised):
        server.lose = lose

        with pytest.raises(raised) as caught:
            resource.assign(SAMPLE_ID)

        assert len(server.sent("/assign")) == 1
        assert len(server.runs) == 1
        assert server.backoff == []
        assert f"list_runs('{SAMPLE_ID}')" in str(caught.value)


class TestWaitForRun:
    def test_a_look_that_fails_is_skipped(self, resource, server, clock):
        resource.assign(SAMPLE_ID, wait=False)
        server.listings = 0
        # Every attempt of the first look fails; the second look is answered.
        server.listing_fails = _http.RETRY_MAX_ATTEMPTS

        ledger = resource.wait_for_run(SAMPLE_ID, "run-1", poll_interval=1.0)

        assert ledger is not None
        assert clock.sleeps == [1.0]

    def test_a_run_no_longer_listed_raises(self, resource, server):
        with pytest.raises(AssignmentRunError) as caught:
            resource.wait_for_run(SAMPLE_ID, "run-gone")

        assert caught.value.status is None
        assert "no longer listed" in str(caught.value)

    def test_a_run_the_server_could_not_name_is_refused(self, resource, server):
        """A 409 from a launch that has not created its run yet names none."""
        server.refuse = _error(
            409,
            "Peak assignment already in progress for this sample",
            {"code": "assignment_in_flight", "sample_item_id": SAMPLE_ID},
        )
        with pytest.raises(AssignmentInFlightError) as caught:
            resource.assign(SAMPLE_ID)
        assert caught.value.peak_assignment_run_id is None

        with pytest.raises(ValueError, match="list_runs"):
            resource.wait_for_run(SAMPLE_ID, caught.value.peak_assignment_run_id)

        assert server.listings == 0

    def test_without_a_timeout_it_waits_as_long_as_the_run(self, resource, server):
        resource.assign(SAMPLE_ID, wait=False)
        server.listings = 0
        server.statuses = ["running"] * 1000 + ["completed"]

        ledger = resource.wait_for_run(SAMPLE_ID, "run-1", timeout=None)

        assert ledger is not None
        assert server.listings == 1002  # 1001 looks, and get()'s own listing


# --- verify -------------------------------------------------------------------


class TestVerify:
    def test_it_sends_the_verdict_and_returns_the_record(self, resource, server):
        record = resource.verify(
            SAMPLE_ID,
            "pa-1",
            "confirmed",
            evidence_level="reference_standard",
            note="standard run on the day",
        )

        (sent,) = server.sent("/verify")
        assert sent["method"] == "POST"
        assert sent["path"] == f"{SAMPLE_ID}/verify"
        assert sent["json"] == {
            "peak_assignment_id": "pa-1",
            "verdict": "confirmed",
            "evidence_level": "reference_standard",
            "note": "standard run on the day",
        }
        assert record["assignment_verification_id"] == "v-1"
        assert record["verdict"] == "confirmed"

    def test_what_is_not_given_is_not_sent(self, resource, server):
        resource.verify(SAMPLE_ID, "pa-1", "rejected")

        assert server.sent("/verify")[0]["json"] == {
            "peak_assignment_id": "pa-1",
            "verdict": "rejected",
        }

    def test_a_confirmation_without_evidence_is_not_sent(self, resource, server):
        with pytest.raises(ValueError, match="evidence_level"):
            resource.verify(SAMPLE_ID, "pa-1", "confirmed")

        assert server.requests == []

    @pytest.mark.parametrize(
        "lose, raised",
        [
            ("timeout", MascopeTimeoutError),
            ("connection", MascopeConnectionError),
            ("502", ServerError),
            ("503", ServerError),
            ("504", ServerError),
        ],
    )
    def test_a_lost_answer_stores_no_duplicate(self, resource, server, lose, raised):
        """The one failure here that would corrupt stored data: each copy of
        a verdict is a label in the confidence calibration's refit."""
        server.lose = lose

        with pytest.raises(raised) as caught:
            resource.verify(
                SAMPLE_ID, "pa-1", "confirmed", evidence_level="reference_standard"
            )

        assert len(server.verifications) == 1
        assert len(server.sent("/verify")) == 1
        assert server.backoff == []
        assert f"list_verifications('{SAMPLE_ID}')" in str(caught.value)


class TestListVerifications:
    def test_it_lists_the_verdicts_with_their_times(self, resource, server):
        resource.verify(SAMPLE_ID, "pa-1", "rejected")
        resource.verify(SAMPLE_ID, "pa-1", "unsure")

        verdicts = resource.list_verifications(SAMPLE_ID)

        assert list(verdicts["verdict"]) == ["rejected", "unsure"]
        assert pd.api.types.is_datetime64_any_dtype(verdicts["verified_utc"])
        assert str(verdicts["verified_utc"].dt.tz) == "UTC"

    def test_none_when_the_sample_has_none(self, resource):
        assert resource.list_verifications(SAMPLE_ID) is None


# --- fit_aggregate ------------------------------------------------------------


class TestFitAggregate:
    def test_it_scores_the_composition(self, resource, server):
        fit = resource.fit_aggregate(SAMPLE_ID, "C6H12O6", "mech-1")

        (sent,) = server.sent("/fit/aggregate")
        assert sent["method"] == "POST"
        assert sent["json"] == {
            "assigned_formula": "C6H12O6",
            "ionization_mechanism_id": "mech-1",
        }
        assert fit["matches"] == {"match_ions": 1, "match_isotopes": 1}
        assert fit["match_isotopes"] == [{"mz": 181.07}]

    def test_it_is_retried_as_the_read_it_is(self, resource, server):
        server.lose = "503-once"

        fit = resource.fit_aggregate(SAMPLE_ID, "C6H12O6", "mech-1")

        assert fit["match_ions"]
        assert len(server.sent("/fit/aggregate")) == 2
