"""
Hermetic tests of what a caller that resolves its own samples gets from the
loaders: the ``on_result`` hook of ``run_concurrent``, the per-task halves
of ``load_peaks`` and ``load_peak_timeseries``, and the collection reads.

The ``mascope_ai`` toolkit picks samples by ID, counts rows as they arrive
and stops a load at its cap; these pin the seams it does that through. They
stub the client and need no running stack.
"""

import json
import threading
import time
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest
import requests

from mascope_sdk import _http, _loaders
from mascope_sdk._concurrent import run_concurrent
from mascope_sdk.resources.collections import CollectionsResource


class _Stop(Exception):
    pass


class TestOnResult:
    def test_it_sees_each_result_with_its_task(self):
        seen: list[tuple[tuple, int]] = []

        results, failures = run_concurrent(
            lambda n: n * 10,
            [(1,), (2,), (3,)],
            max_workers=2,
            on_result=lambda args, result: seen.append((args, result)),
        )

        assert sorted(seen) == [((1,), 10), ((2,), 20), ((3,), 30)]
        assert results == [10, 20, 30]
        assert failures == []

    def test_it_is_not_called_for_a_failure_or_an_empty_result(self):
        seen: list[tuple] = []

        def task(n: int) -> int | None:
            if n == 1:
                raise ValueError("one")
            return None if n == 2 else n

        _, failures = run_concurrent(
            task,
            [(1,), (2,), (3,)],
            max_workers=1,
            on_result=lambda args, result: seen.append(args),
        )

        assert seen == [(3,)]
        assert [failure.args for failure in failures] == [(1,)]

    def test_it_runs_in_the_collecting_thread(self):
        threads: set[int] = set()

        run_concurrent(
            lambda n: n,
            [(n,) for n in range(6)],
            max_workers=3,
            on_result=lambda args, result: threads.add(threading.get_ident()),
        )

        assert threads == {threading.get_ident()}

    def test_what_it_raises_stops_the_run_and_propagates(self):
        started: list[int] = []

        def task(n: int) -> int:
            started.append(n)
            time.sleep(0.01)
            return n

        def stop_at_the_first(args, result):
            raise _Stop(result)

        with pytest.raises(_Stop):
            run_concurrent(
                task,
                [(n,) for n in range(40)],
                max_workers=1,
                on_result=stop_at_the_first,
            )

        # One worker: the first result stops the run, and the tasks not yet
        # started are cancelled rather than run.
        assert len(started) < 40


def _row(sample_id: str) -> pd.Series:
    return pd.Series(
        {
            "sample_item_id": sample_id,
            "sample_item_name": f"name {sample_id}",
            "datetime_utc": pd.Timestamp("2026-01-01", tz="UTC"),
        }
    )


TASKS = [(_row(sid), "batch A") for sid in ("s1", "s2", "s3")]


def _peaks(sample_id: str, **_: object) -> pd.DataFrame:
    if sample_id == "s2":
        raise RuntimeError("read of s2 failed")
    return pd.DataFrame(
        {
            "sample_item_id": [sample_id, sample_id],
            "peak_id": [f"p1-{sample_id}", f"p2-{sample_id}"],
            "mz": [100.0, 101.0],
            "target_compound_formula": ["CH4N2O", "C2H4O2"],
        }
    )


def _series(sample_id: str, peak_id: str) -> pd.DataFrame:
    return pd.DataFrame(
        {"peak_id": peak_id, "time": [0.0, 1.0, 2.0], "height": [1.0, 2.0, 3.0]}
    )


@pytest.fixture
def client():
    return SimpleNamespace(
        samples=SimpleNamespace(get_peaks=_peaks, get_peak_timeseries=_series)
    )


class TestLoadSamplePeaks:
    def test_it_loads_the_samples_given_and_says_what_failed(self, client):
        peaks = _loaders.load_sample_peaks(client, TASKS, max_workers=1)

        assert sorted(set(peaks["sample_item_id"])) == ["s1", "s3"]
        assert peaks["sample_batch_name"].unique().tolist() == ["batch A"]
        assert peaks["sample_item_name"].unique().tolist() == [
            "name s1",
            "name s3",
        ]
        assert peaks.attrs["complete"] is False
        assert [f["sample_item_id"] for f in peaks.attrs["failures"]] == ["s2"]

    def test_the_hook_sees_each_samples_enriched_rows(self, client):
        seen: dict[str, int] = {}

        def count(args, frame):
            seen[args[0]["sample_item_id"]] = len(frame)
            assert "sample_batch_name" in frame.columns

        _loaders.load_sample_peaks(client, TASKS, max_workers=1, on_result=count)

        assert seen == {"s1": 2, "s3": 2}

    def test_the_hook_stops_the_load(self, client):
        def stop(args, frame):
            raise _Stop()

        with pytest.raises(_Stop):
            _loaders.load_sample_peaks(client, TASKS, max_workers=1, on_result=stop)

    def test_load_peaks_goes_through_it(self, client, monkeypatch):
        monkeypatch.setattr(
            _loaders, "_collect_sample_tasks", lambda *a, **k: (TASKS, "d")
        )
        calls: list[dict[str, Any]] = []
        real = _loaders.load_sample_peaks

        def spy(client, tasks, **kwargs):
            calls.append(kwargs)
            return real(client, tasks, **kwargs)

        monkeypatch.setattr(_loaders, "load_sample_peaks", spy)

        _loaders.load_peaks(client, "d", matches=False, average=False, max_workers=1)

        assert calls == [
            {
                "matches": False,
                "areas": True,
                "heights": True,
                "average": False,
                "max_workers": 1,
            }
        ]


class TestLoadSampleTimeseries:
    def test_it_loads_the_series_of_the_matched_peaks(self, client):
        ts = _loaders.load_sample_timeseries(
            client, TASKS, "compound", ["CH4N2O"], max_workers=1
        )

        assert sorted(set(ts["peak_id"])) == ["p1-s1", "p1-s3"]
        assert ts.attrs["complete"] is False
        assert [f["sample_item_id"] for f in ts.attrs["failures"]] == ["s2"]

    def test_the_hook_sees_each_peaks_series(self, client):
        seen: list[tuple[str, str, int]] = []

        def count(args, frame):
            seen.append((args[0]["sample_item_id"], args[2], len(frame)))

        _loaders.load_sample_timeseries(
            client, TASKS, "compound", ["CH4N2O"], max_workers=1, on_result=count
        )

        assert sorted(seen) == [("s1", "p1-s1", 3), ("s3", "p1-s3", 3)]

    def test_an_unknown_level_is_refused(self, client):
        with pytest.raises(ValueError, match="formula_level"):
            _loaders.load_sample_timeseries(client, TASKS, "element", ["C"])

    def test_load_peak_timeseries_goes_through_it(self, client, monkeypatch):
        monkeypatch.setattr(
            _loaders, "_collect_sample_tasks", lambda *a, **k: (TASKS, "d")
        )
        calls: list[tuple] = []
        real = _loaders.load_sample_timeseries

        def spy(client, tasks, level, values, **kwargs):
            calls.append((level, values, kwargs))
            return real(client, tasks, level, values, **kwargs)

        monkeypatch.setattr(_loaders, "load_sample_timeseries", spy)

        _loaders.load_peak_timeseries(client, "d", ion="CH5N2O+", max_workers=1)

        assert calls == [("ion", ["CH5N2O+"], {"max_workers": 1})]


class TestLoadPeaksByStageHook:
    def test_the_hook_sees_each_stage(self, monkeypatch):
        monkeypatch.setattr(_loaders, "_resolve_sample", lambda client, s: s)
        client = SimpleNamespace(samples=SimpleNamespace(get_peaks=_peaks))
        seen: list[tuple[int, int]] = []

        _loaders.load_peaks_by_stage(
            client,
            "s1",
            [(0, 10, "a"), (10, 20, "b")],
            max_workers=1,
            on_result=lambda args, frame: seen.append((args[0], len(frame))),
        )

        assert sorted(seen) == [(0, 2), (1, 2)]


class _StubClient:
    url = "http://server"
    access_token = "token"
    workspace_id = "ws-1"
    _timeout = (1, 5)
    _verify_ssl = False
    _service_name = "mascope_sdk"

    def __init__(self):
        self._cache: dict[str, Any] = {}


class _Wire:
    """Stands in for ``requests.get``: records each request, answers
    ``answer``."""

    def __init__(self):
        self.sent: list[dict[str, Any]] = []
        self.answer: dict[str, Any] = {"data": []}

    def __call__(self, url, **kwargs):
        self.sent.append(
            {
                "path": url.removeprefix("http://server/api/"),
                "params": kwargs.get("params"),
            }
        )
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(self.answer).encode()
        return response


@pytest.fixture
def wire(monkeypatch) -> _Wire:
    fake = _Wire()
    monkeypatch.setattr(_http.requests, "get", fake)
    return fake


COLLECTIONS = [
    {
        "target_collection_id": "c1",
        "target_collection_name": "Organic acids",
        "target_collection_description": None,
        "target_collection_type": "TARGETS",
        "workspace_id": "ws-1",
    },
    {
        "target_collection_id": "c2",
        "target_collection_name": "Calibrants (global)",
        "target_collection_description": "shared",
        "target_collection_type": "CALIBRANTS",
        "workspace_id": None,
    },
]


class TestCollectionsList:
    def test_it_lists_the_clients_workspace_by_default(self, wire):
        wire.answer = {"data": COLLECTIONS, "results": 2}

        df = CollectionsResource(_StubClient()).list()

        assert wire.sent == [
            {"path": "target/collections", "params": {"workspace_id": "ws-1"}}
        ]
        assert df["target_collection_id"].tolist() == ["c1", "c2"]

    def test_it_filters_by_batch_and_workspace(self, wire):
        wire.answer = {"data": COLLECTIONS[:1]}

        CollectionsResource(_StubClient()).list("b1", workspace_id="ws-2")

        assert wire.sent[0]["params"] == {
            "workspace_id": "ws-2",
            "sample_batch_id": "b1",
        }

    def test_all_workspaces_sends_no_workspace(self, wire):
        CollectionsResource(_StubClient()).list(all_workspaces=True)

        assert wire.sent[0]["params"] is None

    def test_an_empty_listing_is_none(self, wire):
        assert CollectionsResource(_StubClient()).list() is None

    def test_it_is_cached_per_filter_and_handed_out_as_a_copy(self, wire):
        wire.answer = {"data": COLLECTIONS}
        resource = CollectionsResource(_StubClient())

        # Both the listing that fills the cache and one served from it
        fetched = resource.list()
        fetched.loc[0, "target_collection_name"] = "edited first"
        cached = resource.list()
        assert cached.loc[0, "target_collection_name"] == "Organic acids"
        cached.loc[0, "target_collection_name"] = "edited"
        again = resource.list()
        resource.list("b1")

        assert again.loc[0, "target_collection_name"] == "Organic acids"
        assert len(wire.sent) == 2  # the plain listing was asked for once


class TestCollectionsGet:
    def test_it_reads_one_collection(self, wire):
        wire.answer = {"data": {**COLLECTIONS[0], "target_compounds_count": 0}}

        got = CollectionsResource(_StubClient()).get("c1")

        assert wire.sent == [{"path": "target/collections/c1", "params": None}]
        assert got["target_collection_name"] == "Organic acids"
