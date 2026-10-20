"""
A Mascope server in a dict, for the toolkit's hermetic tests.

The toolkit reads through a real ``MascopeClient``; only ``requests`` is
replaced, by :class:`FakeAPI`, which answers the routes the toolkit reads
with the answers a demo stack gave to them (``recorded_api.json``: trimmed
to twelve peaks and a few points, names and files replaced). From those it
builds a small workspace whose names are chosen to test matching::

    Workspace A (editor)        Workspace B (guest)
      Dataset "Campaign (2026)"
        Batch "Blank (A)"            Blank 1
        Batch "Spring 2026"          Sample 1, Sample 2, Sample 3 (unmatched)
        Batch "Spring 2026 repeat"   Sample 1
      Dataset "Calibration runs"
        Batch "Calibration"          Cal 1
    Collections: "Organic acids" (Workspace A; Blank (A), Spring 2026),
                 "Calibrants" (global; Calibration)
"""

import copy
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import requests

from mascope_ai import Session, Toolkit


RECORDED = json.loads((Path(__file__).parent / "recorded_api.json").read_text())

DATASETS = {"d1": "Campaign (2026)", "d2": "Calibration runs"}
BATCHES = {
    "b1": ("d1", "Blank (A)"),
    "b2": ("d1", "Spring 2026"),
    "b3": ("d1", "Spring 2026 repeat"),
    "b4": ("d2", "Calibration"),
}
SAMPLES = {
    "s11": ("b1", "Blank 1"),
    "s21": ("b2", "Sample 1"),
    "s22": ("b2", "Sample 2"),
    "s23": ("b2", "Sample 3"),
    "s31": ("b3", "Sample 1"),
    "s41": ("b4", "Cal 1"),
}
#: Samples whose matching has not run.
UNMATCHED = {"s23"}
COLLECTIONS = {
    "c1": ("ws1", "Organic acids", ["b1", "b2"]),
    "c2": (None, "Calibrants", ["b4"]),
}


def _envelope(data: Any, **extra: Any) -> dict[str, Any]:
    return {"message": "ok", "data": data, **extra}


class FakeAPI:
    """Stands in for ``requests.get`` and ``requests.post`` under the SDK.

    :ivar calls: Each request, as ``(method, path, params, body)``.
    :ivar failures: ``(method, path regex) -> HTTP status`` to answer with
        instead, set through :meth:`fail`.
    :ivar peak_copies: How many copies of the recorded peak list each
        sample answers with (each copy's peaks with IDs of their own), to
        make loads as large as a test needs.
    """

    def __init__(self):
        self.calls: list[tuple[str, str, dict[str, Any], Any]] = []
        self.failures: list[tuple[str, re.Pattern, int]] = []
        self.peak_copies = 1

    def fail(self, method: str, path: str, status: int) -> None:
        """Answer requests whose path fully matches regex *path* with *status*."""
        self.failures.append((method, re.compile(path), status))

    def requests_to(self, path: str, method: str = "GET") -> list[tuple]:
        return [
            call
            for call in self.calls
            if call[0] == method and re.fullmatch(path, call[1])
        ]

    # --- the wire ------------------------------------------------------------

    def get(self, url: str, params=None, **kwargs) -> requests.Response:
        return self._answer("GET", url, params, None)

    def post(self, url: str, json=None, params=None, **kwargs) -> requests.Response:
        return self._answer("POST", url, params, json)

    def _answer(self, method, url, params, body) -> requests.Response:
        path = url.split("/api/", 1)[1]
        params = dict(params or {})
        self.calls.append((method, path, params, body))
        status, payload = self._route(method, path, params, body)
        for fail_method, pattern, fail_status in self.failures:
            if fail_method == method and pattern.fullmatch(path):
                status, payload = fail_status, {"error": "refused by the test"}
        response = requests.Response()
        response.status_code = status
        response.url = url
        response._content = json.dumps(payload).encode()
        response.headers["Content-Type"] = "application/json"
        return response

    # --- the server ----------------------------------------------------------

    def _route(self, method, path, params, body) -> tuple[int, Any]:
        parts = path.split("/")
        if method == "POST":
            if re.fullmatch(r"samples/\w+/peaks/timeseries", path):
                return 200, self._timeseries(parts[1], body)
            return 404, {"error": "no route"}
        if path == "workspaces":
            return 200, RECORDED["workspaces"]
        if path == "workspaces/ws1/datasets":
            rows = [
                {
                    **RECORDED["dataset_row"],
                    "dataset_id": d,
                    "dataset_name": name,
                    "workspace_id": "ws1",
                }
                for d, name in DATASETS.items()
            ]
            return 200, _envelope(rows, results=len(rows))
        if path == "sample/batches":
            rows = [
                {
                    **RECORDED["batch_row"],
                    "sample_batch_id": b,
                    "sample_batch_name": name,
                    "dataset_id": d,
                }
                for b, (d, name) in BATCHES.items()
                if d == params.get("dataset_id")
            ]
            return 200, _envelope(rows, results=len(rows))
        if path == "samples":
            rows = [
                self._sample(s)
                for s, (b, _) in SAMPLES.items()
                if b == params.get("sample_batch_id")
            ]
            return 200, _envelope(rows, results=len(rows))
        if re.fullmatch(r"samples/\w+", path):
            if parts[1] not in SAMPLES:
                return 404, {"error": f"Sample {parts[1]} not found"}
            return 200, _envelope(self._sample(parts[1]))
        if re.fullmatch(r"samples/\w+/peaks", path):
            return 200, self._peaks(parts[1])
        if re.fullmatch(r"samples/\w+/spectrum", path):
            return 200, RECORDED["spectrum"]
        if path == "ionization_mechanisms":
            return 200, RECORDED["ionization_mechanisms"]
        if path == "provenance":
            return 200, RECORDED["provenance"]
        if path == "target/collections":
            rows = [
                self._collection(c)
                for c, (ws, _, batches) in COLLECTIONS.items()
                if (
                    ws is None
                    or "workspace_id" not in params
                    or ws == params["workspace_id"]
                )
                and (
                    "sample_batch_id" not in params
                    or params["sample_batch_id"] in batches
                )
            ]
            return 200, _envelope(rows, results=len(rows))
        if re.fullmatch(r"target/collections/\w+", path):
            return 200, _envelope(self._collection(parts[2], full=True))
        return 404, {"error": "no route"}

    def _sample(self, sample_id: str) -> dict[str, Any]:
        batch, name = SAMPLES[sample_id]
        record = {
            **copy.deepcopy(RECORDED["sample_row"]),
            "sample_item_id": sample_id,
            "sample_item_name": name,
            "sample_batch_id": batch,
        }
        if sample_id in UNMATCHED:
            record["match"] = {**record["match"], "match_sample_id": None}
        return record

    def _peaks(self, sample_id: str) -> dict[str, Any]:
        recorded = RECORDED["peaks"]["data"]
        data: dict[str, Any] = {}
        for key, value in recorded.items():
            if not isinstance(value, list):
                data[key] = value
                continue
            copies = []
            for n in range(self.peak_copies):
                if key == "peak_id":
                    copies += [f"{sample_id}-{n}-{peak}" for peak in value]
                else:
                    copies += copy.deepcopy(value)
            data[key] = copies
        return {**RECORDED["peaks"], "results": len(data["peak_id"]), "data": data}

    def _timeseries(self, sample_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return {
            **RECORDED["timeseries"],
            "data": {**RECORDED["timeseries"]["data"], "peak_id": body["peak_id"]},
        }

    def _collection(self, collection_id: str, full: bool = False) -> dict[str, Any]:
        workspace, name, batches = COLLECTIONS[collection_id]
        record: dict[str, Any] = {
            "target_collection_id": collection_id,
            "target_collection_name": name,
            "target_collection_description": None,
            "target_collection_type": "CALIBRANTS" if workspace is None else "TARGETS",
            "workspace_id": workspace,
        }
        if full:
            compounds = [
                {
                    "target_compound_id": f"tc{i}",
                    "target_compound_formula": f"C{i}H{2 * i}O2",
                    "target_compound_name": f"compound {i}",
                }
                for i in range(1, 31)
            ]
            record.update(
                target_compounds=compounds,
                target_compounds_count=len(compounds),
                sample_batches=[
                    {
                        "dataset_id": BATCHES[b][0],
                        "sample_batch_id": b,
                        "sample_batch_name": BATCHES[b][1],
                    }
                    for b in batches
                ],
                sample_batches_count=len(batches),
            )
        return record


@pytest.fixture
def api(monkeypatch) -> FakeAPI:
    from mascope_sdk import _http

    fake = FakeAPI()
    monkeypatch.setattr(_http.requests, "get", fake.get)
    monkeypatch.setattr(_http.requests, "post", fake.post)
    # The SDK backs off between attempts at a transient failure
    monkeypatch.setattr(_http.time, "sleep", lambda seconds: None)
    return fake


@pytest.fixture
def kit(api, tmp_path, monkeypatch) -> Iterator[Toolkit]:
    from mascope_sdk import MascopeClient

    # A load never prompts; were it to, the test fails here
    def no_prompt(*args):
        raise AssertionError("the toolkit asked for confirmation")

    monkeypatch.setattr("builtins.input", no_prompt)
    empty_env = tmp_path / ".env"
    empty_env.write_text("")
    client = MascopeClient(
        url="http://mascope.test",
        access_token="token",
        workspace="Workspace A",
        env_file=empty_env,
        service_name="ai-agent",
    )
    yield Toolkit(client, session=Session("test", root=tmp_path / "sessions"))
    _drop_the_sdks_log_handler()


def _drop_the_sdks_log_handler() -> None:
    """Undo the loguru handler a MascopeClient adds, and the SDK's note of it.

    The SDK remembers its handler's ID and removes it by that ID when the
    next client is made. A test elsewhere in the library run that removes
    every loguru handler would leave that ID stale, and the next client made
    would fail - in another library's tests.
    """
    from loguru import logger

    from mascope_sdk import client as sdk_client

    if sdk_client._log_handler_id is not None:
        try:
            logger.remove(sdk_client._log_handler_id)
        except ValueError:
            pass
        sdk_client._log_handler_id = None
