"""
Hermetic tests for what the SDK owes a caller that cannot watch it work.

A script or an agent reads what a load returns, not the log. So a load that
lost some samples on the way says so on the frame (``attrs["complete"]`` and
``attrs["failures"]``), a load that wants confirming stops a caller with no
stdin instead of waving it through, a name with regex metacharacters selects
the same thing wherever it is passed, and a listing handed out is the
caller's own to edit.

These stub the client and need no running stack.
"""

import re
import time
from types import SimpleNamespace

import pandas as pd
import pytest

from mascope_sdk import ConfirmationRequiredError, _loaders
from mascope_sdk._concurrent import run_concurrent
from mascope_sdk._loaders import _name_mask, concat_frames
from mascope_sdk._resolve import resolve_id
from mascope_sdk.resources.batches import BatchesResource
from mascope_sdk.resources.datasets import DatasetsResource
from mascope_sdk.resources.ionization import IonizationResource
from mascope_sdk.resources.samples import SamplesResource
from mascope_sdk.resources.workspaces import WorkspacesResource


def _row(sample_id: str) -> pd.Series:
    return pd.Series(
        {
            "sample_item_id": sample_id,
            "sample_item_name": f"name {sample_id}",
            "datetime_utc": pd.Timestamp("2026-01-01", tz="UTC"),
        }
    )


#: Three samples in one batch; ``s2`` is the one whose read fails.
TASKS = [(_row(sid), "batch A") for sid in ("s1", "s2", "s3")]

S2_FAILED = {
    "sample_batch_name": "batch A",
    "sample_item_id": "s2",
    "sample_item_name": "name s2",
    "error_type": "RuntimeError",
    "error": "read of s2 failed",
}


def _peaks(sample_id: str, **_: object) -> pd.DataFrame:
    if sample_id == "s2":
        raise RuntimeError("read of s2 failed")
    return pd.DataFrame(
        {
            "sample_item_id": [sample_id],
            "peak_id": [f"p-{sample_id}"],
            "mz": [100.0],
            "target_compound_formula": ["CH4N2O"],
        }
    )


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(_loaders, "_collect_sample_tasks", lambda *a, **k: (TASKS, "d"))
    monkeypatch.setattr(_loaders, "_resolve_sample", lambda client, sample: sample)
    return SimpleNamespace(
        samples=SimpleNamespace(get_peaks=_peaks),
        peak_assignments=SimpleNamespace(get=lambda sample_id, **k: _peaks(sample_id)),
    )


class TestRunConcurrent:
    def test_failures_are_returned_beside_the_results(self):
        def task(n: int) -> int:
            if n == 2:
                raise ValueError("two")
            return n * 10

        results, failures = run_concurrent(task, [(1,), (2,), (3,)], max_workers=2)

        assert sorted(results) == [10, 30]
        assert len(failures) == 1
        assert failures[0].args == (2,)
        assert isinstance(failures[0].error, ValueError)

    def test_failures_come_in_task_order_not_completion_order(self):
        def task(n: int) -> None:
            time.sleep(0.05 * (3 - n))  # the first task finishes last
            raise ValueError(n)

        _, failures = run_concurrent(task, [(0,), (1,), (2,)], max_workers=3)

        assert [failure.args for failure in failures] == [(0,), (1,), (2,)]

    def test_results_come_in_task_order_not_completion_order(self):
        def task(n: int) -> int:
            time.sleep(0.05 * (3 - n))  # the first task finishes last
            return n

        results, _ = run_concurrent(task, [(0,), (1,), (2,)], max_workers=3)

        assert results == [0, 1, 2]

    def test_a_run_without_failures_returns_an_empty_list(self):
        assert run_concurrent(lambda n: n, [(1,)]) == ([1], [])

    def test_no_tasks(self):
        assert run_concurrent(lambda n: n, []) == ([], [])


class TestALoadSaysWhetherItIsWhole:
    def test_load_peaks_reports_the_failed_sample(self, client):
        peaks = _loaders.load_peaks(client, "d")

        assert sorted(peaks["sample_item_id"]) == ["s1", "s3"]
        assert peaks.attrs["complete"] is False
        assert peaks.attrs["failures"] == [S2_FAILED]

    def test_a_whole_load_is_complete_with_no_failures(self, client, monkeypatch):
        monkeypatch.setattr(
            _loaders, "_collect_sample_tasks", lambda *a, **k: (TASKS[:1], "d")
        )

        peaks = _loaders.load_peaks(client, "d")

        assert peaks.attrs["complete"] is True
        assert peaks.attrs["failures"] == []

    def test_a_load_where_every_sample_failed_is_an_empty_frame_not_none(
        self, client, monkeypatch
    ):
        # None reads as "no peaks there"; the failures must still reach the caller.
        monkeypatch.setattr(
            _loaders, "_collect_sample_tasks", lambda *a, **k: (TASKS[1:2], "d")
        )

        peaks = _loaders.load_peaks(client, "d")

        assert peaks is not None and peaks.empty
        assert peaks.attrs["complete"] is False
        assert peaks.attrs["failures"] == [S2_FAILED]
        assert peaks.attrs["warnings"] == []

    def test_load_assignments_reports_the_failed_sample(self, client):
        assignments = _loaders.load_assignments(client, "d")

        assert sorted(assignments["sample_item_id"]) == ["s1", "s3"]
        assert assignments.attrs["complete"] is False
        assert assignments.attrs["failures"] == [S2_FAILED]

    def test_load_assignments_logs_only_the_samples_that_had_nothing(self, client):
        from loguru import logger

        def get(sample_id, **_):
            if sample_id == "s3":
                return None  # no completed run: nothing, not a failure
            return _peaks(sample_id)

        client.peak_assignments.get = get
        seen: list[str] = []
        handler = logger.add(seen.append, level="INFO", format="{message}")
        try:
            _loaders.load_assignments(client, "d")
        finally:
            logger.remove(handler)

        # s2 failed and s3 had nothing: one sample contributed nothing, not two
        assert any(
            "1 sample(s) contributed no assignments" in message for message in seen
        )

    def test_load_assignments_where_every_sample_failed(self, client, monkeypatch):
        monkeypatch.setattr(
            _loaders, "_collect_sample_tasks", lambda *a, **k: (TASKS[1:2], "d")
        )

        assignments = _loaders.load_assignments(client, "d")

        assert assignments.empty
        assert assignments.attrs["failures"] == [S2_FAILED]

    def test_load_peaks_by_stage_reports_the_failed_stage(self, client):
        def get_peaks(sample_id, *, t_min, **_):
            if t_min == 30:
                raise RuntimeError("stage read failed")
            return _peaks(sample_id)

        client.samples.get_peaks = get_peaks

        peaks = _loaders.load_peaks_by_stage(
            client, "s1", [(0, 30, "blank"), (30, 60, "sample"), (60, 90)]
        )

        assert peaks["stage"].tolist() == [0, 2]
        assert peaks.attrs["complete"] is False
        assert peaks.attrs["failures"] == [
            {
                "sample_item_id": "s1",
                "stage": 1,
                "stage_name": "sample",
                "t_min": 30,
                "t_max": 60,
                "error_type": "RuntimeError",
                "error": "stage read failed",
            }
        ]

    def test_load_peaks_by_stage_where_every_stage_failed(self, client):
        def get_peaks(sample_id, **_):
            raise RuntimeError("down")

        client.samples.get_peaks = get_peaks

        peaks = _loaders.load_peaks_by_stage(client, "s1", [(0, 30)])

        assert peaks.empty
        assert [f["stage"] for f in peaks.attrs["failures"]] == [0]

    def test_load_peak_timeseries_reports_failed_samples_and_peaks(self, client):
        def timeseries(sample_id, peak_id):
            if peak_id == "p-s3":
                raise RuntimeError("series of p-s3 failed")
            return pd.DataFrame({"time": [0.0, 1.0], "height": [1.0, 2.0]})

        client.samples.get_peak_timeseries = timeseries

        ts = _loaders.load_peak_timeseries(client, "d", compound="CH4N2O")

        assert ts["sample_item_id"].unique().tolist() == ["s1"]
        assert ts.attrs["complete"] is False
        assert ts.attrs["failures"] == [
            S2_FAILED,
            {
                "sample_batch_name": "batch A",
                "sample_item_id": "s3",
                "sample_item_name": "name s3",
                "peak_id": "p-s3",
                "error_type": "RuntimeError",
                "error": "series of p-s3 failed",
            },
        ]

    def test_load_peak_timeseries_where_discovery_found_nothing_but_failed(
        self, client, monkeypatch
    ):
        monkeypatch.setattr(
            _loaders, "_collect_sample_tasks", lambda *a, **k: (TASKS[1:2], "d")
        )

        ts = _loaders.load_peak_timeseries(client, "d", compound="CH4N2O")

        assert ts.empty
        assert ts.attrs["failures"] == [S2_FAILED]

    def test_load_peak_timeseries_where_every_series_failed(self, client):
        def get_peaks(sample_id, **_):
            if sample_id == "s1":
                time.sleep(0.1)  # s1's peaks are found after s3's
            return _peaks(sample_id)

        def timeseries(sample_id, peak_id):
            raise RuntimeError("down")

        client.samples.get_peaks = get_peaks
        client.samples.get_peak_timeseries = timeseries

        ts = _loaders.load_peak_timeseries(client, "d", compound="CH4N2O")

        assert ts.empty
        assert [f.get("peak_id") for f in ts.attrs["failures"]] == [
            None,
            "p-s1",
            "p-s3",
        ]

    def test_the_run_still_warns_in_the_log(self, client):
        from loguru import logger

        seen: list[str] = []
        handler = logger.add(seen.append, level="WARNING", format="{message}")
        try:
            _loaders.load_peaks(client, "d")
        finally:
            logger.remove(handler)

        assert any("1 of 3 task(s) failed" in message for message in seen)


class TestSamplesListOfSeveralBatches:
    @pytest.fixture
    def resource(self, monkeypatch):
        resource = SamplesResource(SimpleNamespace(_cache={}))
        batches = pd.DataFrame(
            {"sample_batch_id": ["b1", "b2"], "sample_batch_name": ["one", "two"]}
        )
        monkeypatch.setattr(resource, "_get_all_batches", lambda dataset: batches)

        def list_by_id(batch_id):
            if batch_id == "b2":
                raise RuntimeError("listing b2 failed")
            return pd.DataFrame({"sample_item_id": ["s1"]})

        monkeypatch.setattr(resource, "_list_by_id", list_by_id)
        return resource

    def test_a_failed_batch_is_reported(self, resource):
        listed = resource.list(batches=re.compile("."))

        assert listed["sample_item_id"].tolist() == ["s1"]
        assert listed.attrs["complete"] is False
        assert listed.attrs["failures"] == [
            {
                "sample_batch_id": "b2",
                "sample_batch_name": "two",
                "error_type": "RuntimeError",
                "error": "listing b2 failed",
            }
        ]

    def test_every_batch_failed(self, resource):
        listed = resource.list(batches="two")

        assert listed.empty
        assert [f["sample_batch_id"] for f in listed.attrs["failures"]] == ["b2"]


class TestCombiningLoads:
    def test_failures_are_gathered_and_complete_only_when_all_are(self):
        whole = pd.DataFrame({"mz": [1.0]})
        whole.attrs = {"complete": True, "failures": [], "provenance": {"v": 1}}
        partial = pd.DataFrame({"mz": [2.0]})
        partial.attrs = {
            "complete": False,
            "failures": [S2_FAILED],
            "provenance": {"v": 1},
        }

        combined = concat_frames([whole, partial])

        assert combined.attrs["complete"] is False
        assert combined.attrs["failures"] == [S2_FAILED]
        assert combined.attrs["provenance"] == {"v": 1}

    def test_whole_loads_combine_whole(self):
        frames = []
        for mz in (1.0, 2.0):
            frame = pd.DataFrame({"mz": [mz]})
            frame.attrs = {"complete": True, "failures": []}
            frames.append(frame)

        combined = concat_frames(frames)

        assert combined.attrs["complete"] is True
        assert combined.attrs["failures"] == []

    def test_frames_that_do_not_say_add_nothing(self):
        combined = concat_frames([pd.DataFrame({"mz": [1.0]})])

        assert "complete" not in combined.attrs
        assert "failures" not in combined.attrs


class TestConfirmAbove:
    def _answer(self, monkeypatch, answer):
        asked: list[str] = []

        def fake_input(prompt=""):
            asked.append(prompt)
            if isinstance(answer, BaseException):
                raise answer
            return answer

        monkeypatch.setattr("builtins.input", fake_input)
        return asked

    def test_no_stdin_raises_with_the_count(self, monkeypatch, client):
        self._answer(monkeypatch, EOFError())
        read: list[str] = []
        client.samples.get_peaks = lambda sample_id, **k: read.append(sample_id)

        with pytest.raises(ConfirmationRequiredError, match="3 samples") as caught:
            _loaders.load_peaks(client, "d", confirm_above=2)

        assert (caught.value.count, caught.value.threshold) == (3, 2)
        assert read == []  # raised before loading anything

    @pytest.mark.parametrize(
        "loader", ["load_peaks", "load_assignments", "load_peak_timeseries"]
    )
    def test_every_loader_that_confirms_stops_a_caller_with_no_stdin(
        self, monkeypatch, client, loader
    ):
        self._answer(monkeypatch, EOFError())
        kwargs = {"compound": "x"} if loader == "load_peak_timeseries" else {}

        with pytest.raises(ConfirmationRequiredError):
            getattr(_loaders, loader)(client, "d", confirm_above=1, **kwargs)

    def test_declining_still_interrupts(self, monkeypatch, client):
        self._answer(monkeypatch, "n")

        with pytest.raises(KeyboardInterrupt):
            _loaders.load_peaks(client, "d", confirm_above=2)

    def test_confirming_loads(self, monkeypatch, client):
        self._answer(monkeypatch, "y")

        peaks = _loaders.load_peaks(client, "d", confirm_above=2)

        assert len(peaks) == 2

    def test_none_loads_without_asking(self, monkeypatch, client):
        asked = self._answer(monkeypatch, EOFError())

        peaks = _loaders.load_peaks(client, "d", confirm_above=None)

        assert len(peaks) == 2
        assert asked == []

    def test_under_the_threshold_nobody_is_asked(self, monkeypatch, client):
        asked = self._answer(monkeypatch, EOFError())

        _loaders.load_peaks(client, "d", confirm_above=3)

        assert asked == []


class TestOneNameOneMatchEverywhere:
    NAMES = ["Sample (A)", "Sample A", "Blank [1]"]

    @pytest.mark.parametrize("name", ["Sample (A)", "(A)", "Blank [1]", "[1]"])
    def test_resolution_and_the_loader_filters_select_the_same_name(self, name):
        items = pd.DataFrame({"id": ["a", "b", "c"], "name": self.NAMES})

        resolved = resolve_id(
            name, items, id_column="id", name_column="name", entity_label="batch"
        )
        filtered = items[_name_mask(items["name"], name, exact=False)]

        assert filtered["id"].tolist() == [resolved]

    def test_a_compiled_pattern_is_a_regex_in_both(self):
        items = pd.DataFrame({"id": ["a", "b", "c"], "name": self.NAMES})
        pattern = re.compile(r"^Sample A$")

        resolved = resolve_id(
            pattern, items, id_column="id", name_column="name", entity_label="batch"
        )
        filtered = items[_name_mask(items["name"], pattern, exact=False)]

        assert filtered["id"].tolist() == [resolved] == ["b"]


class TestTheListingCacheHandsOutCopies:
    @staticmethod
    def _client():
        return SimpleNamespace(_cache={}, workspace_id="ws")

    @pytest.mark.parametrize(
        ("resource_class", "call", "column"),
        [
            (WorkspacesResource, lambda r: r.list(), "workspace_name"),
            (DatasetsResource, lambda r: r.list(), "dataset_name"),
            (IonizationResource, lambda r: r.list(), "ionization_mechanism"),
            (BatchesResource, lambda r: r._list_by_id("d"), "sample_batch_name"),
            (SamplesResource, lambda r: r._list_by_id("b"), "sample_item_name"),
        ],
    )
    def test_editing_a_listing_leaves_the_cache_as_fetched(
        self, monkeypatch, resource_class, call, column
    ):
        resource = resource_class(self._client())
        gets: list[str] = []

        def get(path, params=None):
            gets.append(path)
            return [{column: "original"}]

        monkeypatch.setattr(resource, "_get", get)

        first = call(resource)
        first.loc[0, column] = "edited by the caller"
        second = call(resource)
        second[column] = "edited again"
        third = call(resource)

        assert len(gets) == 1  # still served from the cache
        assert third[column].tolist() == ["original"]
        assert first is not second is not third
