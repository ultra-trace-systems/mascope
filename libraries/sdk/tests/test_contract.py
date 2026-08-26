"""
SDK contract tests against a live demo stack.

These exercise ``MascopeClient`` end to end over HTTP, so they double as a
breaking-change detector for the REST API surface the SDK (and every external
SDK user) depends on: response envelopes, field names, the data hierarchy
walk (workspace -> dataset -> batch -> sample -> peaks), the spectrum /
timeseries / matching read paths, the high-level loaders, and the mapping of
HTTP error statuses onto SDK exceptions.

The tests derive their inputs (formulas, m/z values, time ranges) from the
demo data itself instead of hardcoding demo names, and assert on shapes and
keys rather than values, so they survive demo-bundle updates.

Opt-in: they need a running demo stack (snapshot mode) and are skipped
otherwise. Locally:

    docker compose -f docker-compose.demo.yaml up -d
    MASCOPE_SDK_CONTRACT=1 uv run pytest libraries/sdk/tests/ -v

In CI they run inside the demo-stack e2e job. Configuration:

- ``MASCOPE_SDK_TEST_URL``: app origin (default ``http://127.0.0.1:8080``).
- ``MASCOPE_SDK_TEST_TOKEN``: access token (default: the public demo token).

The suite also runs against an *older published* SDK (a compatibility check
can install mascope-sdk from PyPI and point it at a current server), so tests
of recently added surfaces probe for them with ``hasattr`` / signature checks
and skip when the installed SDK predates them - a missing attribute there
means "not released yet", not "contract broken".
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


@pytest.fixture(scope="module")
def matched_peaks(mascope):
    """``(sample_id, matched peak rows)`` of the first demo sample.

    ``get_peaks`` is not cached client-side, so the tests that need a real
    matched peak (matching, cheminfo, timeseries) share one fetch.
    """
    sample_id = _first_sample_id(mascope)
    peaks = mascope.samples.get_peaks(sample_id)
    assert peaks is not None and not peaks.empty
    matched = peaks[
        peaks["target_isotope_id"].notna() & peaks["target_compound_formula"].notna()
    ]
    assert not matched.empty, "expected matched peaks in the demo data"
    return sample_id, matched


@pytest.fixture(scope="module")
def top_peak_timeseries(mascope, matched_peaks):
    """``(peak row, timeseries)`` of the most intense matched peak."""
    sample_id, matched = matched_peaks
    peak = matched.loc[matched["height"].idxmax()]
    ts = mascope.samples.get_peak_timeseries(sample_id, peak_id=peak["peak_id"])
    assert ts is not None and not ts.empty
    return peak, ts


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


@requires_stack
class TestSpectraContract:
    """Spectrum, multi-sample spectra, and per-peak timeseries read paths."""

    def test_spectrum_shape(self, mascope):
        sample_id = _first_sample_id(mascope)

        spectrum = mascope.samples.get_spectrum(sample_id)

        assert spectrum is not None and not spectrum.empty
        assert list(spectrum.columns) == ["mz", "intensity"]
        assert (spectrum["mz"] > 0).all()

    def test_spectrum_applies_mz_window(self, mascope):
        sample_id = _first_sample_id(mascope)
        full = mascope.samples.get_spectrum(sample_id)
        anchor = float(full["mz"].iloc[len(full) // 2])

        windowed = mascope.samples.get_spectrum(
            sample_id, mz_min=anchor - 1.0, mz_max=anchor + 1.0
        )

        assert windowed is not None and not windowed.empty
        assert windowed["mz"].between(anchor - 1.0, anchor + 1.0).all()
        assert len(windowed) < len(full)

    def test_spectra_cover_all_requested_samples(self, mascope):
        batch = _first_batches(mascope).iloc[0]
        samples = mascope.samples.list(batch["sample_batch_id"])
        ids = samples["sample_item_id"].head(2).tolist()

        spectra = mascope.samples.get_spectra(ids)

        assert spectra is not None and not spectra.empty
        assert {"sample_item_id", "mz", "intensity"} <= set(spectra.columns)
        assert set(spectra["sample_item_id"]) == set(ids)

    def test_centroids_keyed_by_sample(self, mascope):
        sample_id = _first_sample_id(mascope)

        centroids = mascope.samples.get_centroids([sample_id])

        assert isinstance(centroids, dict)
        assert sample_id in centroids
        assert {"masses", "intensities", "resolutions"} <= set(centroids[sample_id])

    def test_peak_timeseries_by_peak_id(self, top_peak_timeseries):
        peak, ts = top_peak_timeseries

        assert list(ts.columns) == ["peak_id", "time", "height", "mz"]
        assert (ts["peak_id"] == peak["peak_id"]).all()
        assert len(ts) > 1

    def test_peak_timeseries_by_mz_resolves_nearest_peak(
        self, mascope, matched_peaks, top_peak_timeseries
    ):
        sample_id, _ = matched_peaks
        peak, _ = top_peak_timeseries

        ts = mascope.samples.get_peak_timeseries(
            sample_id, mz=float(peak["mz"]), mz_tolerance_ppm=5.0
        )

        assert ts is not None and not ts.empty
        assert (ts["peak_id"] == peak["peak_id"]).all()


@requires_stack
class TestMatchingContract:
    """On-the-fly compound matching (`/api/match/aggregate/sample/...`).

    Matches a formula the demo data is already matched to, so a non-empty
    result is guaranteed without hardcoding demo compounds.
    """

    def test_match_compound_finds_a_demo_compound(self, mascope, matched_peaks):
        sample_id, matched = matched_peaks
        peak = matched.loc[matched["height"].idxmax()]
        formula = peak["target_compound_formula"]

        result = mascope.matching.match_compound(
            sample_id, formula, name=str(peak["target_compound_name"])
        )

        assert isinstance(result, dict)
        assert {"match_compounds", "match_ions", "match_isotopes"} <= set(result)
        compounds = result["match_compounds"]
        assert compounds, f"demo compound {formula} should re-match"
        assert compounds[0]["target_compound_formula"] == formula
        assert result["match_isotopes"], "expected isotope-level match rows"

    def test_match_compounds_nests_ions_and_isotopes(self, mascope, matched_peaks):
        sample_id, matched = matched_peaks
        formula = matched.loc[matched["height"].idxmax(), "target_compound_formula"]

        result = mascope.matching.match_compounds(sample_id, [formula])

        assert isinstance(result, list) and result
        compound = result[0]
        assert compound["target_compound_formula"] == formula
        ions = compound.get("children")
        assert ions, "compound record should nest matched ions as children"
        assert ions[0].get("children"), "ion record should nest isotopes as children"


@requires_stack
class TestReferenceDataContract:
    """Server-wide reference surfaces: ionization mechanisms and cheminfo."""

    def test_ionization_mechanisms_shape(self, mascope):
        df = mascope.ionization.list()

        assert df is not None and not df.empty
        assert {
            "ionization_mechanism_id",
            "ionization_mechanism",
            "ionization_mechanism_polarity",
        } <= set(df.columns)

    def test_cheminfo_query_around_a_matched_peak(self, mascope, matched_peaks):
        # A CHON-only compound is guaranteed to be found again by the default
        # composition search (its formula ranges cover C/H/O/N only).
        sample_id, matched = matched_peaks
        chon = matched[matched["target_compound_formula"].str.fullmatch(r"[CHON0-9]+")]
        if chon.empty:
            pytest.skip("demo data carries no CHON-only matched compound")
        peak = chon.loc[chon["height"].idxmax()]
        # get_peaks resolves mechanism ids to names, so map the name back.
        mechanisms = mascope.ionization.list()
        name_to_id = dict(
            zip(
                mechanisms["ionization_mechanism"],
                mechanisms["ionization_mechanism_id"],
            )
        )
        mech_id = name_to_id[peak["ionization_mechanism"]]

        results = mascope.cheminfo.query_by_mz(
            float(peak["mz"]), ionization_mechanism_ids=[mech_id]
        )

        assert isinstance(results, list)
        assert results, "a matched peak's m/z should yield candidate compositions"
        record = results[0]
        assert {
            "target_compound_formula",
            "ionization_mechanism",
            "target_isotope_mz",
        } <= set(record)
        # Every candidate must sit within the default tolerance (30 ppm) of
        # the queried m/z. Exact formula strings are not asserted: the
        # composition search and the target database may format the same
        # formula differently.
        for candidate in results:
            error_ppm = (
                abs(candidate["target_isotope_mz"] - float(peak["mz"]))
                / float(peak["mz"])
                * 1e6
            )
            assert error_ppm <= 31, candidate


@requires_stack
class TestLoaderContract:
    """High-level loaders end to end - the surface notebooks actually use.

    Scoped to a single batch (``exact=True``) to keep request counts bounded
    on any demo bundle.
    """

    def test_load_peaks_enriches_batch_and_sample_context(self, mascope):
        _skip_unless_param(mascope.load_peaks, "exact")
        dataset, batches = _first_dataset_and_batches(mascope)
        batch = batches.iloc[0]

        peaks = mascope.load_peaks(
            dataset=dataset["dataset_id"],
            batches=batch["sample_batch_name"],
            exact=True,
            confirm_above=None,
        )

        assert peaks is not None and not peaks.empty
        assert {
            "sample_batch_name",
            "sample_item_id",
            "sample_item_name",
            "datetime_utc",
            "mz",
            "height",
            "target_isotope_formula",
        } <= set(peaks.columns)
        assert (peaks["sample_batch_name"] == batch["sample_batch_name"]).all()
        batch_samples = mascope.samples.list(batch["sample_batch_id"])
        assert set(peaks["sample_item_id"]) <= set(batch_samples["sample_item_id"])

    def test_load_peak_timeseries_for_a_matched_compound(self, mascope, matched_peaks):
        _skip_unless_param(mascope.load_peak_timeseries, "exact")
        sample_id, matched = matched_peaks
        dataset, batches = _first_dataset_and_batches(mascope)
        batch = batches.iloc[0]
        formula = matched.loc[matched["height"].idxmax(), "target_compound_formula"]

        ts = mascope.load_peak_timeseries(
            dataset=dataset["dataset_id"],
            batches=batch["sample_batch_name"],
            exact=True,
            compound=formula,
            confirm_above=None,
        )

        assert ts is not None and not ts.empty
        assert {
            "sample_batch_name",
            "sample_item_id",
            "sample_item_name",
            "datetime_utc",
            "peak_id",
            "mz",
            "time",
            "height",
            "target_compound_formula",
        } <= set(ts.columns)
        assert (ts["target_compound_formula"] == formula).all()
        # The sample the formula came from is in the loaded batch, so it must
        # contribute timeseries rows.
        assert sample_id in set(ts["sample_item_id"])

    def test_load_peaks_by_stage_labels_stages(
        self, mascope, matched_peaks, top_peak_timeseries
    ):
        sample_id, _ = matched_peaks
        _, ts = top_peak_timeseries
        t_end = float(ts["time"].max())
        mid = t_end / 2

        peaks = mascope.load_peaks_by_stage(
            sample=sample_id,
            stages=[(0, mid, "early"), (mid, t_end, "late")],
        )

        assert peaks is not None and not peaks.empty
        assert {"stage", "stage_name", "t_min", "t_max", "mz"} <= set(peaks.columns)
        assert set(peaks["stage"]) == {0, 1}
        assert set(peaks["stage_name"]) == {"early", "late"}

    def test_load_assignments_across_a_batch(self, mascope):
        _skip_unless_attr(mascope, "load_assignments")
        sample_id = _sample_with_completed_run(mascope)
        dataset, batches = _first_dataset_and_batches(mascope)
        batch = batches.iloc[0]

        assignments = mascope.load_assignments(
            dataset=dataset["dataset_id"],
            batches=batch["sample_batch_name"],
            exact=True,
            confirm_above=None,
        )

        assert assignments is not None and not assignments.empty
        assert {
            "sample_batch_name",
            "sample_item_id",
            "sample_item_name",
            "datetime_utc",
            "tier",
            "role",
            "source",
        } <= set(assignments.columns)
        # The sample with a completed run is in the loaded batch, so it must
        # contribute rows.
        assert sample_id in set(assignments["sample_item_id"])


@requires_stack
class TestErrorContract:
    """HTTP error statuses must surface as the documented SDK exceptions."""

    def test_bad_token_raises_authentication_error(self):
        from mascope_sdk import MascopeClient
        from mascope_sdk.exceptions import AuthenticationError

        with pytest.raises(AuthenticationError):
            # Workspace resolution happens in the constructor, so the 401
            # from the first API call surfaces here.
            MascopeClient(url=BASE_URL, access_token="not-a-valid-token")

    def test_unknown_sample_raises_not_found(self, mascope):
        from mascope_sdk.exceptions import NotFoundError

        with pytest.raises(NotFoundError):
            mascope.samples.get("00000000-0000-0000-0000-000000000000")

    def test_invalid_request_body_raises_validation_error(self, mascope):
        from mascope_sdk.exceptions import ValidationError

        with pytest.raises(ValidationError):
            # `mz` is required by the endpoint's request model; sending None
            # exercises the live 422 -> ValidationError mapping.
            mascope.cheminfo.query_by_mz(
                mz=None,  # type: ignore[arg-type]
                ionization_mechanism_ids=[],
            )


def _skip_unless_attr(obj, name: str) -> None:
    """Skip when the installed SDK predates attribute *name* on *obj*."""
    if not hasattr(obj, name):
        pytest.skip(f"installed mascope_sdk predates .{name}")


def _skip_unless_param(func, name: str) -> None:
    """Skip when the installed SDK's *func* predates the *name* parameter."""
    import inspect

    if name not in inspect.signature(func).parameters:
        pytest.skip(f"installed mascope_sdk predates {func.__name__}({name}=...)")


def _sample_with_completed_run(mascope) -> str:
    """A sample carrying a completed peak assignment run, else skip.

    Scans the samples of the first data-bearing batch; the demo bundle does
    not seed assignment runs yet, so absence is an environment gap rather
    than a contract violation.
    """
    _skip_unless_attr(mascope, "peak_assignments")
    batch = _first_batches(mascope).iloc[0]
    samples = mascope.samples.list(batch["sample_batch_id"])
    assert samples is not None and not samples.empty, "demo batch should have samples"
    for _, row in samples.iterrows():
        runs = mascope.peak_assignments.list_runs(row["sample_item_id"])
        if runs is not None and (runs["status"] == "completed").any():
            return row["sample_item_id"]
    pytest.skip("demo stack carries no completed peak assignment run")


def _first_dataset_and_batches(mascope):
    """
    ``(dataset row, its batches)`` of the first demo dataset that has any.

    Dataset ordering is not part of the contract, and the workspace can carry
    empty datasets (e.g. auto-created acquisition pools), so scan rather than
    trust ``iloc[0]``.
    """
    for _, dataset in mascope.datasets.list().iterrows():
        batches = mascope.batches.list(dataset["dataset_id"])
        if batches is not None and not batches.empty:
            return dataset, batches
    raise AssertionError("demo stack has no dataset with batches")


def _first_batches(mascope):
    """Batches of the first demo dataset that has any."""
    return _first_dataset_and_batches(mascope)[1]


def _first_sample_id(mascope) -> str:
    """A sample from the first batch of the first data-bearing dataset."""
    batch = _first_batches(mascope).iloc[0]
    samples = mascope.samples.list(batch["sample_batch_id"])
    assert samples is not None and not samples.empty, "demo batch should have samples"
    return samples.iloc[0]["sample_item_id"]
