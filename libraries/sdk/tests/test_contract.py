"""
SDK contract tests against a live demo stack.

These exercise ``MascopeClient`` end to end over HTTP, so they double as a
breaking-change detector for the REST API surface the SDK (and every external
SDK user) depends on: response envelopes, field names, and the data hierarchy
walk (workspace -> dataset -> batch -> sample -> peaks).

Opt-in: they need a running demo stack (snapshot mode) and are skipped
otherwise. Locally:

    docker compose -f docker-compose.demo.yaml up -d
    MASCOPE_SDK_CONTRACT=1 uv run pytest libraries/sdk/tests/ -v

In CI they run inside the demo-stack e2e job. Configuration:

- ``MASCOPE_SDK_TEST_URL``: app origin (default ``http://127.0.0.1:8080``).
- ``MASCOPE_SDK_TEST_TOKEN``: access token (default: the public demo token).
"""

import os

import pytest


BASE_URL = os.environ.get("MASCOPE_SDK_TEST_URL", "http://127.0.0.1:8080")
TOKEN = os.environ.get("MASCOPE_SDK_TEST_TOKEN", "mascope_demo_sdk_token")

requires_stack = pytest.mark.skipif(
    os.environ.get("MASCOPE_SDK_CONTRACT") != "1",
    reason=(
        "SDK contract tests need a running demo stack; start one "
        "(docker compose -f docker-compose.demo.yaml up -d) and set "
        "MASCOPE_SDK_CONTRACT=1"
    ),
)


def _workspace_with_data() -> str | None:
    """
    Name of the first workspace that has datasets (demo: 'Acquisitions Orbion').

    Auto-selection in ``MascopeClient`` refuses to pick when several
    workspaces exist (the demo stack also carries a system workspace), so the
    fixture resolves the data-bearing one explicitly. Uses the same headers
    the SDK sends.
    """
    import requests

    override = os.environ.get("MASCOPE_SDK_TEST_WORKSPACE")
    if override:
        return override

    headers = {"Authorization": f"Bearer {TOKEN}", "X-Service-Name": "mascope_sdk"}
    workspaces = requests.get(
        f"{BASE_URL}/api/workspaces", headers=headers, timeout=30
    ).json()["data"]
    for workspace in workspaces:
        datasets = (
            requests.get(
                f"{BASE_URL}/api/workspaces/{workspace['workspace_id']}/datasets",
                headers=headers,
                timeout=30,
            )
            .json()
            .get("data", [])
        )
        if datasets:
            return workspace["workspace_name"]
    return workspaces[0]["workspace_name"] if workspaces else None


@pytest.fixture(scope="module")
def mascope():
    """One authenticated client for the whole module, on the demo workspace."""
    from mascope_sdk import MascopeClient

    return MascopeClient(
        url=BASE_URL, access_token=TOKEN, workspace=_workspace_with_data()
    )


@requires_stack
class TestClientContract:
    def test_client_resolves_a_workspace(self, mascope):
        assert mascope.workspace_id
        assert mascope.workspace_name

    def test_workspaces_listing_shape(self, mascope):
        df = mascope.workspaces.list()

        assert df is not None and not df.empty
        assert {"workspace_id", "workspace_name"} <= set(df.columns)

    def test_datasets_listing_shape(self, mascope):
        df = mascope.datasets.list()

        assert df is not None and not df.empty, "demo stack should have datasets"
        assert {"dataset_id", "dataset_name"} <= set(df.columns)

    def test_batches_listing_shape(self, mascope):
        df = _first_batches(mascope)

        assert "sample_batch_id" in df.columns
        assert "sample_batch_name" in df.columns

    def test_samples_listing_shape(self, mascope):
        batch = _first_batches(mascope).iloc[0]

        df = mascope.samples.list(batch["sample_batch_id"])

        assert df is not None and not df.empty, "demo batch should have samples"
        assert {"sample_item_id", "filename"} <= set(df.columns)

    def test_sample_peaks_include_match_data(self, mascope):
        # The demo data ships fully matched, so peaks must carry flattened
        # match columns and at least one isotope attribution.
        sample_id = _first_sample_id(mascope)

        peaks = mascope.samples.get_peaks(sample_id)

        assert peaks is not None and not peaks.empty
        assert {"mz", "height", "target_isotope_id", "target_isotope_formula"} <= set(
            peaks.columns
        )
        assert peaks["target_isotope_id"].notna().any(), "expected matched peaks"
        assert "target_collection_names" in peaks.columns
        matched = peaks[peaks["target_isotope_id"].notna()]
        names = matched["target_collection_names"].explode().dropna()
        assert not names.empty, "matched peaks should name their collections"

    def test_get_single_sample(self, mascope):
        sample_id = _first_sample_id(mascope)

        sample = mascope.samples.get(sample_id)

        assert sample is not None
        assert sample.get("sample_item_id") == sample_id


@requires_stack
class TestPeakAssignmentContract:
    """Shape of the peak-assignment read surface (`/api/peak-assignments/*`).

    These need a sample with a **completed** assignment run. Runs are not part
    of the demo bundle yet (see docs/dev/sdk_peak_assignment.md section 7), so
    the tests skip when the stack carries none rather than fail.
    """

    def test_list_runs_shape(self, mascope):
        sample_id = _sample_with_completed_run(mascope)

        runs = mascope.peak_assignments.list_runs(sample_id)

        assert runs is not None and not runs.empty
        assert {
            "peak_assignment_run_id",
            "engine_version",
            "status",
            "config",
            "peak_assignment_run_utc_created",
        } <= set(runs.columns)
        completed = runs[runs["status"] == "completed"]
        assert not completed.empty
        assert completed.iloc[0]["engine_version"]

    def test_get_returns_one_row_per_peak_with_run_attrs(self, mascope):
        sample_id = _sample_with_completed_run(mascope)

        df = mascope.peak_assignments.get(sample_id)

        assert df is not None and not df.empty
        assert {
            "peak_assignment_id",
            "sample_peak_id",
            "sample_peak_mz",
            "tier",
            "role",
            "source",
            "fit_score",
            "p_correct",
        } <= set(df.columns)
        # One row per observed peak: no peak repeats.
        assert df["sample_peak_id"].is_unique
        run = df.attrs["run"]
        assert run["status"] == "completed"
        assert run["peak_assignment_run_id"] in set(df["peak_assignment_run_id"])

    def test_detail_serves_the_inspector_json(self, mascope):
        sample_id = _sample_with_completed_run(mascope)
        df = mascope.peak_assignments.get(sample_id)

        record = mascope.peak_assignments.detail(
            sample_id, df.iloc[0]["peak_assignment_id"]
        )

        assert record is not None
        assert record["peak_assignment_id"] == df.iloc[0]["peak_assignment_id"]
        # The detail row is a superset of the list row: the inspector-only
        # JSON keys exist (values may be None for unassigned peaks).
        assert "alternatives" in record
        assert "provenance" in record


def _sample_with_completed_run(mascope) -> str:
    """A sample carrying a completed peak assignment run, else skip.

    Scans the samples of the first data-bearing batch; the demo bundle does
    not seed assignment runs yet, so absence is an environment gap rather
    than a contract violation.
    """
    batch = _first_batches(mascope).iloc[0]
    samples = mascope.samples.list(batch["sample_batch_id"])
    assert samples is not None and not samples.empty, "demo batch should have samples"
    for _, row in samples.iterrows():
        runs = mascope.peak_assignments.list_runs(row["sample_item_id"])
        if runs is not None and (runs["status"] == "completed").any():
            return row["sample_item_id"]
    pytest.skip("demo stack carries no completed peak assignment run")


def _first_batches(mascope):
    """
    Batches of the first demo dataset that has any.

    Dataset ordering is not part of the contract, and the workspace can carry
    empty datasets (e.g. auto-created acquisition pools), so scan rather than
    trust ``iloc[0]``.
    """
    for _, dataset in mascope.datasets.list().iterrows():
        batches = mascope.batches.list(dataset["dataset_id"])
        if batches is not None and not batches.empty:
            return batches
    raise AssertionError("demo stack has no dataset with batches")


def _first_sample_id(mascope) -> str:
    """A sample from the first batch of the first data-bearing dataset."""
    batch = _first_batches(mascope).iloc[0]
    samples = mascope.samples.list(batch["sample_batch_id"])
    assert samples is not None and not samples.empty, "demo batch should have samples"
    return samples.iloc[0]["sample_item_id"]
