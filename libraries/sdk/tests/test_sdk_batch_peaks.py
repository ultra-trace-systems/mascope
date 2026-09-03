"""
Hermetic unit tests for the ``batch_peaks`` resource and the
``load_batch_ledger`` loader. These mock the HTTP layer (like
``test_peak_assignments.py``) and do not need a running stack. Named apart from the
backend's ``test_batch_peaks.py`` so the two can be collected in one run.
"""

from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from mascope_sdk._loaders import load_batch_ledger
from mascope_sdk.resources.batch_peaks import BatchPeaksResource


BATCH_ID = "batch-1"

ANCHORS = [
    {
        "batch_peak_id": "bp-1",
        "sample_batch_id": BATCH_ID,
        "mz": 181.0707,
        "consensus_formula": "C6H12O6",
        "consensus_tier": "assigned",
        "n_present": 2,
        "curated": False,
    },
    {
        "batch_peak_id": "bp-2",
        "sample_batch_id": BATCH_ID,
        "mz": 200.1234,
        "consensus_formula": None,
        "consensus_tier": "unassigned",
        "n_present": 1,
        "curated": False,
    },
]


def _members(count: int) -> list[dict[str, Any]]:
    return [
        {
            "sample_batch_id": BATCH_ID,
            "batch_peak_id": f"bp-{i}",
            "batch_mz": 100.0 + i,
            "consensus_formula": "C6H12O6",
            "sample_item_id": f"s{i % 2}",
            "sample_item_name": f"Sample {i % 2}",
            "sample_peak_id": f"p{i}",
            "mz": 100.0 + i,
            "intensity": 1000.0 * (i + 1),
            "assigned_formula": "C6H12O6",
            "tier": "assigned",
            "role": "M0",
        }
        for i in range(count)
    ]


VERDICTS = [
    {
        "batch_peak_verification_id": "v1",
        "batch_peak_id": "bp-1",
        "assigned_formula": "C6H12O6",
        "verdict": "confirmed",
        "verified_utc": "2026-09-03T10:00:00Z",
        "superseded_utc": None,
        "stale": False,
    }
]


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeServer:
    """A canned batch-peaks API behind the ``http_get`` seam: the ledger, the
    paged members and the verdicts of one batch."""

    def __init__(self, page_cap: int = 2):
        self.anchors = list(ANCHORS)
        self.members = _members(5)
        self.verdicts = list(VERDICTS)
        self.page_cap = page_cap
        self.member_calls: list[dict[str, Any]] = []
        self.list_calls: list[dict[str, Any]] = []

    def http_get(self, url, path, access_token, params=None, **kwargs):
        params = dict(params or {})
        if path.endswith("/members"):
            self.member_calls.append(params)
            rows = self.members
            if params.get("sample_item_id"):
                rows = [
                    r for r in rows if r["sample_item_id"] == params["sample_item_id"]
                ]
            offset = int(params.get("offset", 0))
            limit = min(int(params.get("limit", 1000)), self.page_cap)
            page = rows[offset : offset + limit]
            return _FakeResponse(
                {
                    "status": "success",
                    "message": "ok",
                    "results": len(page),
                    "total": len(rows),
                    "limit": limit,
                    "offset": offset,
                    "data": page,
                }
            )
        if path.endswith("/verdicts"):
            return _FakeResponse(
                {
                    "status": "success",
                    "message": "ok",
                    "results": 1,
                    "data": self.verdicts,
                }
            )
        self.list_calls.append(params)
        rows = self.anchors
        if params.get("tier"):
            rows = [r for r in rows if r["consensus_tier"] == params["tier"]]
        rows = [
            r for r in rows if r["n_present"] >= int(params.get("min_n_present", 1))
        ]
        return _FakeResponse(
            {"status": "success", "message": "ok", "results": len(rows), "data": rows}
        )


class _StubClient:
    url = "http://testserver"
    access_token = "token"
    _timeout = (1, 5)
    _verify_ssl = False
    _service_name = "mascope_sdk"


@pytest.fixture()
def server(monkeypatch):
    fake = FakeServer()
    monkeypatch.setattr("mascope_sdk.resources._base.http_get", fake.http_get)
    return fake


@pytest.fixture()
def resource(server):
    return BatchPeaksResource(_StubClient())


class TestList:
    def test_returns_one_row_per_batch_peak(self, resource):
        df = resource.list(BATCH_ID)
        assert df is not None and len(df) == 2
        assert df["batch_peak_id"].tolist() == ["bp-1", "bp-2"]
        assert "curated" in df.columns

    def test_forwards_the_filters(self, server, resource):
        df = resource.list(BATCH_ID, tier="assigned", min_n_present=2)
        assert len(df) == 1
        assert server.list_calls[-1] == {"min_n_present": 2, "tier": "assigned"}

    def test_returns_none_without_a_ledger(self, server, resource):
        server.anchors = []
        assert resource.list(BATCH_ID) is None


class TestMembers:
    def test_pages_to_total_and_preserves_order(self, server, resource):
        df = resource.members(BATCH_ID)
        assert df is not None and len(df) == 5
        assert df["sample_peak_id"].tolist() == ["p0", "p1", "p2", "p3", "p4"]
        assert [call["offset"] for call in server.member_calls] == [0, 2, 4]
        assert {call["limit"] for call in server.member_calls} == {5000}

    def test_narrows_to_a_sample(self, server, resource):
        df = resource.members(BATCH_ID, sample_id="s1")
        assert df["sample_item_id"].unique().tolist() == ["s1"]
        assert all(call["sample_item_id"] == "s1" for call in server.member_calls)

    def test_returns_none_without_members(self, server, resource):
        server.members = []
        assert resource.members(BATCH_ID) is None


class TestVerdicts:
    def test_returns_the_verdicts_with_datetimes(self, resource):
        df = resource.verdicts(BATCH_ID)
        assert df is not None and len(df) == 1
        assert pd.api.types.is_datetime64_any_dtype(df["verified_utc"])

    def test_returns_none_when_nothing_was_judged(self, server, resource):
        server.verdicts = []
        assert resource.verdicts(BATCH_ID) is None


class TestLoadBatchLedger:
    def _batches(self):
        return pd.DataFrame(
            [
                {"sample_batch_id": "b1", "sample_batch_name": "Batch A"},
                {"sample_batch_id": "b2", "sample_batch_name": "Batch B"},
                {"sample_batch_id": "b3", "sample_batch_name": "Batch C"},
            ]
        )

    def _client(self, anchors_by_batch, members_by_batch):
        def _list(batch_id, *, tier=None, min_n_present=1):
            frame = anchors_by_batch.get(batch_id)
            return None if frame is None else frame.copy()

        def _members(batch_id, *, sample_id=None):
            frame = members_by_batch.get(batch_id)
            return None if frame is None else frame.copy()

        return SimpleNamespace(
            batch_peaks=SimpleNamespace(list=_list, members=_members)
        )

    def test_concatenates_the_members_of_every_batch_with_a_ledger(self, monkeypatch):
        monkeypatch.setattr(
            "mascope_sdk._loaders._resolve_batches",
            lambda *args, **kwargs: (self._batches(), "ds-1"),
        )
        anchors = {
            "b1": pd.DataFrame(ANCHORS),
            "b2": pd.DataFrame(ANCHORS[:1]),
            "b3": None,  # no ledger yet
        }
        members = {
            "b1": pd.DataFrame(_members(3)),
            "b2": pd.DataFrame(_members(2)),
            "b3": None,
        }
        result = load_batch_ledger(self._client(anchors, members), dataset="My Dataset")
        assert result is not None and len(result) == 5
        assert result.columns.get_loc("sample_batch_name") == 0
        assert result["sample_batch_name"].tolist() == ["Batch A"] * 3 + ["Batch B"] * 2
        species = result.attrs["batch_peaks"]
        assert len(species) == 3
        assert species["sample_batch_name"].tolist() == [
            "Batch A",
            "Batch A",
            "Batch B",
        ]

    def test_species_only_when_members_are_not_wanted(self, monkeypatch):
        monkeypatch.setattr(
            "mascope_sdk._loaders._resolve_batches",
            lambda *args, **kwargs: (self._batches().iloc[:1], "ds-1"),
        )
        client = self._client({"b1": pd.DataFrame(ANCHORS)}, {})
        result = load_batch_ledger(client, dataset="My Dataset", members=False)
        assert len(result) == 2
        assert result["sample_batch_name"].unique().tolist() == ["Batch A"]
        assert "batch_peaks" not in result.attrs

    def test_returns_none_when_no_batch_has_a_ledger(self, monkeypatch):
        monkeypatch.setattr(
            "mascope_sdk._loaders._resolve_batches",
            lambda *args, **kwargs: (self._batches(), "ds-1"),
        )
        assert load_batch_ledger(self._client({}, {}), dataset="My Dataset") is None

    def test_returns_none_when_no_batch_matches(self, monkeypatch):
        monkeypatch.setattr(
            "mascope_sdk._loaders._resolve_batches",
            lambda *args, **kwargs: (None, "ds-1"),
        )
        assert load_batch_ledger(self._client({}, {}), dataset="My Dataset") is None
