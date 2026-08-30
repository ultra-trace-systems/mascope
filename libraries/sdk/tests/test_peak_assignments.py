"""
Hermetic unit tests for the ``peak_assignments`` resource and the
``load_assignments`` loader. These mock the HTTP layer (like
``test_http.py``) and do not need a running stack.
"""

import inspect
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from mascope_sdk import MascopeClient, ValidationError
from mascope_sdk._loaders import load_assignments
from mascope_sdk.resources.peak_assignments import PeakAssignmentsResource


SAMPLE_ID = "sample-1"

#: Runs newest-first, the way the endpoint serves them. The newest run failed,
#: so "latest completed" resolution must skip it.
RUNS = [
    {
        "peak_assignment_run_id": "run-failed",
        "sample_item_id": SAMPLE_ID,
        "engine_version": "1.2.0",
        "status": "failed",
        "config": {"run_untargeted": True},
        "error": "engine exploded",
        "peak_assignment_run_utc_created": "2026-08-18T10:00:00Z",
        "peak_assignment_run_utc_completed": None,
    },
    {
        "peak_assignment_run_id": "run-latest",
        "sample_item_id": SAMPLE_ID,
        "engine_version": "1.1.0",
        "status": "completed",
        "config": {"run_untargeted": True},
        "error": None,
        "peak_assignment_run_utc_created": "2026-08-17T10:00:00Z",
        "peak_assignment_run_utc_completed": "2026-08-17T10:05:00Z",
    },
    {
        "peak_assignment_run_id": "run-old",
        "sample_item_id": SAMPLE_ID,
        "engine_version": "1.0.0",
        "status": "completed",
        "config": {},
        "error": None,
        "peak_assignment_run_utc_created": "2026-08-10T10:00:00Z",
        "peak_assignment_run_utc_completed": "2026-08-10T10:04:00Z",
    },
]

TIERS = ("assigned", "candidate", "below_assignability", "unassigned")

#: The assignment sources the server's ``AssignmentSource`` enum accepts, in
#: the same order. ``manual`` is a row a person assigned by hand from the app's
#: peak inspector: it *leaves* ``database``/``untargeted`` rather than joining
#: them, so a client that knows only the two engine sources silently loses
#: every curated peak. Kept in step with
#: ``peak_assignments/schemas.py::AssignmentSource``.
SOURCES = ("database", "untargeted", "manual")


def _make_assignments(run_id: str, count: int) -> list[dict[str, Any]]:
    """Slim ledger rows for *run_id*, ordered by m/z like the endpoint."""
    return [
        {
            "peak_assignment_id": f"{run_id}-a{i:03d}",
            "peak_assignment_run_id": run_id,
            "sample_item_id": SAMPLE_ID,
            "sample_peak_id": f"peak-{i:03d}",
            "sample_peak_mz": 100.0 + i,
            "sample_peak_intensity": 1000.0 * (i + 1),
            "sample_peak_tof": None,
            "role": "M0",
            "assigned_formula": "C6H12O6",
            "ion_formula": "C6H13O6+",
            "ionization_mechanism_id": "mech-1",
            "isotope_label": None,
            "isotope_formula": None,
            "source": SOURCES[i % len(SOURCES)],
            "fit_score": 0.9,
            "mz_error_ppm": 0.5,
            "abundance_error": 0.1,
            "tier": TIERS[i % 4],
            "target_compound_id": None,
            "target_ion_id": None,
            "owner_peak_assignment_id": None,
            "p_correct": 0.8,
            "p_correct_provisional": True,
            "corroboration_adducts": 1,
        }
        for i in range(count)
    ]


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]):
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeServer:
    """A canned peak-assignments API behind the ``http_get`` seam.

    Serves the runs listing, the paged (``page_cap`` rows per page) slim
    ledger with enum-validated filters, and the per-assignment detail - the
    contract the SDK codes against.
    """

    def __init__(
        self,
        runs: list[dict] | None = None,
        assignments: list[dict] | None = None,
        page_cap: int = 2,
    ):
        self.runs = RUNS if runs is None else runs
        self.assignments = (
            _make_assignments("run-latest", 5) if assignments is None else assignments
        )
        self.page_cap = page_cap
        self.list_calls: list[dict[str, Any]] = []

    def http_get(self, url, path, access_token, params=None, **kwargs):
        if path.endswith("/runs"):
            return _FakeResponse(
                {
                    "status": "success",
                    "message": f"Retrieved {len(self.runs)} runs",
                    "results": len(self.runs),
                    "data": self.runs,
                }
            )
        if "/assignment/" in path:
            assignment_id = path.rsplit("/", 1)[-1]
            rows = [
                {**row, "alternatives": [{"formula": "C5H8O7"}], "provenance": {}}
                for row in self.assignments
                if row["peak_assignment_id"] == assignment_id
            ]
            return _FakeResponse(
                {
                    "status": "success",
                    "message": "Retrieved assignment",
                    "results": len(rows),
                    "data": rows,
                }
            )

        # The paged list endpoint
        params = dict(params or {})
        self.list_calls.append(params)
        for key, accepted in (
            ("tier", TIERS),
            ("role", ("M0", "iso_child", "reagent", "artifact", "unassigned")),
            ("source", SOURCES),
        ):
            value = params.get(key)
            if value is not None and value not in accepted:
                # Typed enum params: the server 422s a bad value, which the
                # SDK's HTTP layer surfaces as ValidationError.
                raise ValidationError(
                    message=f"Input should be one of {accepted}",
                    status_code=422,
                    url=f"{url}/api/{path}",
                )

        rows = [
            row
            for row in self.assignments
            if row["peak_assignment_run_id"] == params.get("peak_assignment_run_id")
        ]
        for key in ("tier", "role", "source"):
            if params.get(key) is not None:
                rows = [row for row in rows if row[key] == params[key]]

        offset = int(params.get("offset", 0))
        limit = min(int(params.get("limit", 1000)), self.page_cap)
        page = rows[offset : offset + limit]
        return _FakeResponse(
            {
                "status": "success",
                "message": f"Retrieved {len(page)} of {len(rows)} peak assignments",
                "results": len(page),
                "total": len(rows),
                "data": page,
            }
        )


class _StubClient:
    """The attributes ``BaseResource`` reads off ``MascopeClient``."""

    url = "http://testserver"
    access_token = "token"
    _timeout = (1, 5)
    _verify_ssl = False
    _service_name = "mascope_sdk"


@pytest.fixture()
def server(monkeypatch):
    fake = FakeServer()
    monkeypatch.setattr(
        "mascope_sdk.resources.peak_assignments.http_get", fake.http_get
    )
    monkeypatch.setattr("mascope_sdk.resources._base.http_get", fake.http_get)
    return fake


@pytest.fixture()
def resource(server):
    return PeakAssignmentsResource(_StubClient())


class TestListRuns:
    def test_returns_runs_newest_first_with_datetime_columns(self, resource):
        runs = resource.list_runs(SAMPLE_ID)

        assert runs is not None and len(runs) == 3
        assert runs.iloc[0]["peak_assignment_run_id"] == "run-failed"
        assert pd.api.types.is_datetime64_any_dtype(
            runs["peak_assignment_run_utc_created"]
        )

    def test_returns_none_when_sample_has_no_runs(self, server, resource):
        server.runs = []

        assert resource.list_runs(SAMPLE_ID) is None


class TestGet:
    def test_pages_to_total_and_preserves_order(self, server, resource):
        # 5 rows behind a 2-row page cap -> three requests, one DataFrame.
        df = resource.get(SAMPLE_ID)

        assert df is not None and len(df) == 5
        assert df["sample_peak_mz"].tolist() == [100.0, 101.0, 102.0, 103.0, 104.0]
        assert [call.get("offset") for call in server.list_calls] == [0, 2, 4]
        # The resolved run id is sent explicitly on every page request.
        assert {call.get("peak_assignment_run_id") for call in server.list_calls} == {
            "run-latest"
        }

    def test_resolves_latest_completed_run_and_attaches_metadata(self, resource):
        # The newest run failed; resolution must pick the newest *completed*.
        df = resource.get(SAMPLE_ID)

        run = df.attrs["run"]
        assert run["peak_assignment_run_id"] == "run-latest"
        assert run["status"] == "completed"
        assert run["engine_version"] == "1.1.0"
        assert run["config"] == {"run_untargeted": True}

    def test_explicit_run_id_is_used_and_its_metadata_attached(self, server, resource):
        server.assignments = _make_assignments("run-old", 3)

        df = resource.get(SAMPLE_ID, run_id="run-old")

        assert len(df) == 3
        assert df.attrs["run"]["peak_assignment_run_id"] == "run-old"
        assert df.attrs["run"]["engine_version"] == "1.0.0"

    def test_returns_none_when_no_completed_run_exists(self, server, resource):
        server.runs = [run for run in RUNS if run["status"] != "completed"]

        assert resource.get(SAMPLE_ID) is None
        # No ledger request was made without a run to read.
        assert server.list_calls == []

    def test_returns_none_when_filters_match_nothing(self, server, resource):
        server.assignments = [
            row
            for row in _make_assignments("run-latest", 4)
            if row["tier"] != "candidate"
        ]

        assert resource.get(SAMPLE_ID, tier="candidate") is None

    def test_filters_are_passed_through(self, server, resource):
        df = resource.get(SAMPLE_ID, tier="assigned", source="database")

        assert set(df["tier"]) == {"assigned"}
        assert server.list_calls[0]["tier"] == "assigned"
        assert server.list_calls[0]["source"] == "database"
        assert "role" not in server.list_calls[0]

    def test_manual_source_is_a_first_class_filter_value(self, server, resource):
        # A hand-curated row is reachable on its own, not only by reading the
        # whole run. If 'manual' were still treated as a bad enum value this
        # would raise ValidationError instead of returning rows.
        df = resource.get(SAMPLE_ID, source="manual")

        assert df is not None and set(df["source"]) == {"manual"}
        assert server.list_calls[0]["source"] == "manual"

    def test_the_three_sources_account_for_every_assigned_row(self, resource):
        # The regression this guards: a curated row *leaves* database and
        # untargeted rather than joining them, so a caller who partitions a run
        # into those two frames loses exactly the hand-assigned peaks. Reading
        # the run once and splitting locally cannot drift that way.
        whole_run = resource.get(SAMPLE_ID)

        per_source = whole_run.groupby("source", dropna=False).size()
        assert per_source.sum() == len(whole_run)
        assert "manual" in per_source.index
        engine_only = int(per_source.get("database", 0)) + int(
            per_source.get("untargeted", 0)
        )
        assert engine_only < len(whole_run)

    def test_misspelled_enum_filter_raises_validation_error(self, resource):
        with pytest.raises(ValidationError):
            resource.get(SAMPLE_ID, tier="assinged")


class TestDetail:
    def test_returns_full_record_with_inspector_json(self, resource):
        record = resource.detail(SAMPLE_ID, "run-latest-a001")

        assert record is not None
        assert record["peak_assignment_id"] == "run-latest-a001"
        assert record["alternatives"] == [{"formula": "C5H8O7"}]
        assert "provenance" in record

    def test_returns_none_when_response_is_empty(self, resource):
        assert resource.detail(SAMPLE_ID, "no-such-assignment") is None


class TestLoadAssignments:
    @staticmethod
    def _sample_tasks():
        return [
            (
                pd.Series(
                    {
                        "sample_item_id": "s1",
                        "sample_item_name": "Sample 1",
                        "datetime_utc": pd.Timestamp("2026-08-01T00:00:00Z"),
                    }
                ),
                "Batch A",
            ),
            (
                pd.Series(
                    {
                        "sample_item_id": "s2",
                        "sample_item_name": "Sample 2",
                        "datetime_utc": pd.Timestamp("2026-08-02T00:00:00Z"),
                    }
                ),
                "Batch A",
            ),
        ]

    def _stub_client(self, frames_by_sample: dict[str, pd.DataFrame | None]):
        def _get(sample_id, *, tier=None, source=None, run_id=None):
            frame = frames_by_sample.get(sample_id)
            return None if frame is None else frame.copy()

        return SimpleNamespace(peak_assignments=SimpleNamespace(get=_get))

    def test_concatenates_and_enriches_skipping_runless_samples(self, monkeypatch):
        monkeypatch.setattr(
            "mascope_sdk._loaders._collect_sample_tasks",
            lambda *args, **kwargs: (self._sample_tasks(), "ds-1"),
        )
        s1 = pd.DataFrame(_make_assignments("run-latest", 2)).assign(
            sample_item_id="s1"
        )
        client = self._stub_client({"s1": s1, "s2": None})  # s2 has no run

        result = load_assignments(client, dataset="My Dataset")

        assert result is not None and len(result) == 2
        assert result["sample_batch_name"].unique().tolist() == ["Batch A"]
        assert result["sample_item_name"].unique().tolist() == ["Sample 1"]
        # Context columns sit in load_peaks order at the front
        assert result.columns.get_loc("sample_batch_name") == 0
        assert (
            result.columns.get_loc("sample_item_name")
            == result.columns.get_loc("sample_item_id") + 1
        )
        assert "datetime_utc" in result.columns

    def test_returns_none_when_no_sample_has_assignments(self, monkeypatch):
        monkeypatch.setattr(
            "mascope_sdk._loaders._collect_sample_tasks",
            lambda *args, **kwargs: (self._sample_tasks(), "ds-1"),
        )
        client = self._stub_client({"s1": None, "s2": None})

        assert load_assignments(client, dataset="My Dataset") is None

    def test_rejects_run_values_other_than_latest(self):
        with pytest.raises(ValueError, match="latest"):
            load_assignments(SimpleNamespace(), dataset="My Dataset", run="run-abc123")

    def test_signature_matches_loader_conventions(self):
        # Parity with the other loaders: `exact` is exposed and there is no
        # **kwargs to swallow typos.
        params = inspect.signature(MascopeClient.load_assignments).parameters
        assert "exact" in params
        assert not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
