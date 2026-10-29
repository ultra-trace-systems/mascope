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
- ``MASCOPE_SDK_TEST_EMAIL`` / ``MASCOPE_SDK_TEST_PASSWORD``: the token
  user's sign-in (default: the public demo login). The write tests sign in
  to make what a token may not - scratch workspaces and batches, and a
  second user, a guest, to be refused - and remove it all afterwards. The
  user must be an owner.
- ``MASCOPE_SDK_TEST_MS2_SAMPLE``: a sample with MS2 scans, for a stack where
  the probe in ``ms2_sample_id`` would not find one.

The suite also runs against an *older published* SDK (a compatibility check
can install mascope-sdk from PyPI and point it at a current server), so tests
of recently added surfaces probe for them with ``hasattr`` / signature checks
and skip when the installed SDK predates them - a missing attribute there
means "not released yet", not "contract broken".
"""

import os
import secrets

import pytest


BASE_URL = os.environ.get("MASCOPE_SDK_TEST_URL", "http://127.0.0.1:8080")
TOKEN = os.environ.get("MASCOPE_SDK_TEST_TOKEN", "mascope_demo_sdk_token")
# The same user's sign-in, for what a token may not do: the write tests make
# their scratch workspaces, batches and second user over a session.
TEST_EMAIL = os.environ.get("MASCOPE_SDK_TEST_EMAIL", "demo@mascope.app")
TEST_PASSWORD = os.environ.get("MASCOPE_SDK_TEST_PASSWORD", "mascope-demo")
#: Names the write tests give what they create, so a listing can tell them
#: from the demo data.
SCRATCH_PREFIX = "sdk-contract-"

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
        if workspace["workspace_name"].startswith(SCRATCH_PREFIX):
            # A scratch workspace of the write tests, left by a run that
            # could not remove it.
            continue
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


@pytest.fixture(scope="module")
def ms2_sample_id(mascope):
    """A sample with MS2 scans, else skip every test that needs one.

    ``MASCOPE_SDK_TEST_MS2_SAMPLE`` names one directly. Otherwise the MS2
    summary of the first sample in each batch is probed - one request per
    batch, not per sample, since an MS2 acquisition in the demo bundle goes in
    a batch of its own (docs/demo_dataset.md). A bundle without one (every
    one before 1.4.0) makes absence an environment gap rather than a contract
    violation.

    A probe the server cannot answer - a non-Orbitrap file, a raw missing from
    the filestore - is passed over rather than failed on: it says nothing
    about the sample the tests need. A broken summary route still fails
    ``test_summary_reports_scan_counts``, which calls it unguarded.
    """
    from mascope_sdk.exceptions import MascopeAPIError

    _skip_unless_attr(mascope.samples, "ms2")
    override = os.environ.get("MASCOPE_SDK_TEST_MS2_SAMPLE")
    if override:
        return override
    for _, dataset in mascope.datasets.list().iterrows():
        batches = mascope.batches.list(dataset["dataset_id"])
        if batches is None or batches.empty:
            continue
        for batch_id in batches["sample_batch_id"]:
            samples = mascope.samples.list(batch_id)
            if samples is None or samples.empty:
                continue
            sample_id = samples.iloc[0]["sample_item_id"]
            try:
                summary = mascope.samples.ms2(sample_id).get_summary()
            except MascopeAPIError:
                continue
            if summary and summary["ms2_scan_count"] > 0:
                return sample_id
    pytest.skip("stack carries no sample with MS2 scans")


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
        matched = peaks[peaks["target_isotope_id"].notna()]

        # `target_collection_names` is derived client-side, from the SDK's own
        # `match_keys` list - so an SDK older than the column produces peaks
        # without it against a perfectly healthy server. The nightly runs this
        # file against the *published* SDK, so this has to be a probe like the
        # other post-dating surfaces, not an assertion. The ids come from the
        # server and are asserted either way.
        assert "target_collection_ids" in peaks.columns
        ids = matched["target_collection_ids"].explode().dropna()
        assert not ids.empty, "matched peaks should carry their collection ids"

        _skip_unless_column(peaks, "target_collection_names")
        names = matched["target_collection_names"].explode().dropna()
        assert not names.empty, "matched peaks should name their collections"

    def test_a_sample_says_its_class_and_line_width(self, mascope):
        """Whether or not it has an assignment run: the demo has none."""
        sample = mascope.samples.get(_first_sample_id(mascope))

        width = sample["line_width"]
        # The demo samples are cut from raw Orbitrap files.
        assert width["instrument_class"] == "orbi"
        assert width["reference_mz"] == 200.0
        if width["resolution"] is not None:
            assert width["resolution"] > 0
            assert width["fwhm_ppm"] == pytest.approx(1e6 / width["resolution"])

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

    def test_list_runs_serves_the_width_each_run_scored_at(self, mascope):
        sample_id = _sample_with_completed_run(mascope)

        runs = mascope.peak_assignments.list_runs(sample_id)

        assert {"sigma_ppm", "sigma_source", "mu_ppm", "mu_source"} <= set(runs.columns)
        scored = runs[runs["sigma_ppm"].notna()]
        assert (scored["sigma_ppm"] > 0).all()
        assert scored["sigma_source"].notna().all()

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

    def test_centroids_carry_scan_numbers_and_flags(self, mascope):
        _skip_unless_param(mascope.samples.get_centroids, "exclude")
        sample_id = _first_sample_id(mascope)

        every = mascope.samples.get_centroids([sample_id])[sample_id]
        kept = mascope.samples.get_centroids(
            [sample_id], exclude=["reference", "exception"]
        )[sample_id]

        assert len(every["scan"]) == len(every["masses"]) > 0
        assert every["scan"] == sorted(set(every["scan"]))
        for masses, flags in zip(every["masses"], every["flags"]):
            assert len(flags) == len(masses)
        assert kept["scan"] == every["scan"]
        assert all(flag & 24 == 0 for flags in kept["flags"] for flag in flags)
        excluded = sum(flag & 24 != 0 for flags in every["flags"] for flag in flags)
        assert sum(map(len, kept["masses"])) == (
            sum(map(len, every["masses"])) - excluded
        )

    def test_centroids_say_the_factor_each_scan_is_on(self, mascope):
        sample_id = _first_sample_id(mascope)

        every = mascope.samples.get_centroids([sample_id])[sample_id]

        factors = every["mz_calibration_factor"]
        assert len(factors) == len(every["scan"]) > 0
        assert all(factor is None or factor > 0 for factor in factors)

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


# Every averaged MS2 spectrum carries these. parent_peak_mz and activation
# joined the original four in v1.8.0.
MS2_SPECTRUM_FIELDS = {
    "parent_peak_mz",
    "activation",
    "mz",
    "intensity",
    "resolution",
    "signal_to_noise",
}


@requires_stack
class TestMs2Contract:
    """MS2 read paths (`/api/samples/{id}/ms2/*`).

    ``get_averaged_centroids()`` hands the route's response back unparsed, and
    every published SDK documents its keys as the parent peak m/z as a string,
    so scripts parse them: the default key shape is the contract, and a server
    that changes it fails here against the published SDK. The per-step split
    is opt-in (``by_activation``) and keys each spectrum
    ``"<parent m/z>@<activation>"``.

    Against a bundle without an MS2 acquisition (every one before 1.4.0) only
    the summary runs; the centroid tests skip until the stack carries one.
    """

    def test_summary_reports_scan_counts(self, mascope):
        _skip_unless_attr(mascope.samples, "ms2")
        sample_id = _first_sample_id(mascope)

        summary = mascope.samples.ms2(sample_id).get_summary()

        assert isinstance(summary, dict)
        assert {
            "parent_peaks",
            "groups",
            "hcd_energy_map",
            "isolation_width",
            "ms1_scan_count",
            "ms2_scan_count",
        } <= set(summary)
        if summary["ms2_scan_count"] == 0:
            # An MS1-only acquisition answers with an empty summary, not an
            # error - the ms2_sample_id probe relies on that.
            assert summary["parent_peaks"] == []
            assert summary["groups"] == []
        else:
            assert summary["parent_peaks"]
        for group in summary["groups"]:
            # Every width the group's scans used; its one width only when
            # they share one.
            widths = [entry["isolation_width"] for entry in group["isolation_widths"]]
            assert widths == sorted(set(widths))
            assert (
                sum(e["scan_count"] for e in group["isolation_widths"])
                <= (group["scan_count"])
            )
            if len(widths) == 1:
                assert group["isolation_width"] == widths[0]
            else:
                assert group["isolation_width"] is None

    def test_averaged_centroids_are_keyed_by_parent_mz(self, mascope, ms2_sample_id):
        centroids = mascope.samples.ms2(ms2_sample_id).get_averaged_centroids()

        assert isinstance(centroids, dict) and centroids
        for key, spectrum in centroids.items():
            assert "@" not in key, f"default spectra must be keyed by m/z: {key!r}"
            assert MS2_SPECTRUM_FIELDS <= set(spectrum)
            # A script indexing by parent peak parses the key as its m/z.
            assert float(key) == spectrum["parent_peak_mz"]
            assert spectrum["activation"] == ""
            assert len(spectrum["mz"]) == len(spectrum["intensity"])

    def test_by_activation_keys_one_spectrum_per_group(self, mascope, ms2_sample_id):
        ms2 = mascope.samples.ms2(ms2_sample_id)
        _skip_unless_param(ms2.get_averaged_centroids, "by_activation")

        centroids = ms2.get_averaged_centroids(by_activation=True)

        assert isinstance(centroids, dict) and centroids
        for key, spectrum in centroids.items():
            assert MS2_SPECTRUM_FIELDS <= set(spectrum)
            # A group whose scan filter carried no activation keeps the bare
            # m/z, which partitions to an empty activation.
            parent_mz, _, activation = key.partition("@")
            assert float(parent_mz) == spectrum["parent_peak_mz"]
            assert activation == spectrum["activation"]


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

    def test_ionization_modes_shape(self, mascope):
        _skip_unless_attr(mascope.ionization, "modes")
        df = mascope.ionization.modes()

        # The demo's samples are processed, so a mode is there to name them.
        assert df is not None and not df.empty
        assert {
            "ionization_mode_id",
            "ionization_mode_name",
            "ionization_mode_polarity",
        } <= set(df.columns)

    def test_collections_listing_and_one_collection(self, mascope):
        _skip_unless_attr(mascope, "collections")
        _skip_unless_attr(mascope.collections, "list")
        df = mascope.collections.list()

        # The demo's samples are matched, so some collection is there to match
        # them against.
        assert df is not None and not df.empty
        assert {
            "target_collection_id",
            "target_collection_name",
            "target_collection_type",
            "workspace_id",
        } <= set(df.columns)

        collection = mascope.collections.get(df.iloc[0]["target_collection_id"])

        assert collection["target_collection_id"] == df.iloc[0]["target_collection_id"]
        assert {"target_compounds", "sample_batches"} <= set(collection)

    def test_collections_of_a_batch(self, mascope):
        _skip_unless_attr(mascope, "collections")
        _skip_unless_attr(mascope.collections, "list")
        batch_id = _first_batches(mascope).iloc[0]["sample_batch_id"]

        df = mascope.collections.list(batch_id)

        for collection_id in [] if df is None else df["target_collection_id"]:
            batches = mascope.collections.get(collection_id)["sample_batches"]
            assert batch_id in {batch["sample_batch_id"] for batch in batches}

    def test_find_structures_reads_the_reference_rows_of_a_formula(self, mascope):
        _skip_unless_attr(mascope, "compounds")
        _skip_unless_attr(mascope.compounds, "find_structures")

        # Any formula: the lists the demo ships name formulas without a
        # structure, so the answer may be empty, but it is an answer, read
        # with the SDK's token.
        rows = mascope.compounds.find_structures("C6H5NO3", limit=5)

        assert isinstance(rows, list)
        assert len(rows) <= 5
        for row in rows:
            assert {
                "name",
                "inchikey",
                "inchi",
                "smiles",
                "cas_number",
                "source",
                "license",
                "cas_match",
                "name_match",
            } <= set(row)

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

    def test_cheminfo_query_reads_an_isotopologue_line(self, mascope):
        # 126.9223 is the 81Br line of CH2BrO2-, formic acid's bromide
        # adduct: 2 Da above its monoisotopic line, so only a query that may
        # read the m/z at another line of the ion (the default) finds the
        # formula.
        _skip_unless_param(mascope.cheminfo.query_by_mz, "isotopologues")
        mechanisms = mascope.ionization.list()
        bromide = mechanisms[mechanisms["ionization_mechanism"] == "[M+Br]-"]
        if bromide.empty:
            pytest.skip("the stack carries no [M+Br]- mechanism")
        mech_ids = list(bromide["ionization_mechanism_id"])
        mz = 126.9223

        def formulas(**flags):
            results = mascope.cheminfo.query_by_mz(
                mz, ionization_mechanism_ids=mech_ids, mz_tolerance=5.0, **flags
            )
            return {r["target_compound_formula"]: r for r in results}

        assert "CH2O2" not in formulas(isotopologues=False)
        # The default call reads every isotopologue line.
        found = formulas()
        assert "CH2O2" in found
        # The candidate's m/z is the line it was read at, not its
        # monoisotopic line, and its error is against that line.
        assert abs(found["CH2O2"]["target_isotope_mz"] - mz) / mz * 1e6 <= 5


@requires_stack
class TestReferenceListsContract:
    """The reference lists the server has loaded, and what one holds."""

    def test_sources_lists_the_active_sources(self, mascope):
        sources = _reference_sources(mascope)

        assert {
            "name",
            "version",
            "label",
            "license",
            "polarity",
            "allow_radicals",
            "known_window",
            "record_count",
            "ingested_at",
            "admitted",
        } <= set(sources.columns)
        assert sources["name"].is_unique
        assert sources["admitted"].isin([True, False]).all()

    def test_sources_cite_the_publications_of_the_lists(self, mascope):
        sources = _reference_sources(mascope)
        # A server older than the citations gives no such column.
        if "references" not in sources.columns:
            pytest.skip("server predates the references of reference sources")

        references = [ref for refs in sources["references"] for ref in refs]
        assert all(isinstance(refs, list) for refs in sources["references"])
        # The demo stack loads shipped lists, and every one cites a DOI or an
        # ISBN.
        assert references
        assert all(ref["citation"] for ref in references)
        assert all(ref["doi"] or ref["isbn"] for ref in references)

    def test_compounds_reads_a_whole_version(self, mascope):
        source = _largest_reference_source(mascope)

        compounds = mascope.reference.compounds(source["name"])

        assert len(compounds) == source["record_count"]
        assert compounds.attrs["version"] == source["version"]
        assert {
            "name",
            "formula",
            "monoisotopic_mass",
            "inchikey",
            "source_native_id",
            "reference",
            "tags",
        } <= set(compounds.columns)
        assert compounds["monoisotopic_mass"].is_monotonic_increasing

    def test_compounds_pages_through_the_route(self, mascope, monkeypatch):
        from pandas.testing import assert_frame_equal

        source = _largest_reference_source(mascope)
        # After the probe in _largest_reference_source: a published SDK
        # without the resource has no module to import.
        from mascope_sdk.resources import reference

        page = 100
        if source["record_count"] <= page:
            pytest.skip("stack carries no reference source of more than one page")
        whole = mascope.reference.compounds(source["name"])

        monkeypatch.setattr(reference, "COMPOUNDS_PAGE_LIMIT", page)
        paged = mascope.reference.compounds(source["name"])

        assert len(paged) == source["record_count"]
        assert_frame_equal(paged, whole)

    def test_compounds_of_a_named_version(self, mascope):
        source = _largest_reference_source(mascope)

        compounds = mascope.reference.compounds(
            source["name"], version=source["version"]
        )

        assert compounds.attrs["version"] == source["version"]
        assert len(compounds) == source["record_count"]

    def test_an_unknown_source_raises_not_found(self, mascope):
        from mascope_sdk.exceptions import NotFoundError

        _skip_unless_attr(mascope, "reference")

        with pytest.raises(NotFoundError):
            mascope.reference.compounds(f"{SCRATCH_PREFIX}no-such-list")


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
            # Asked for: a batch read in one request leaves them out unasked
            matches=True,
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

    def test_load_peaks_reads_a_batch_in_one_request_as_its_samples_read(
        self, mascope, monkeypatch
    ):
        from pandas.testing import assert_frame_equal

        from mascope_sdk.resources import _base

        _skip_unless_attr(mascope.batch_peaks, "peak_lists")
        dataset, batches = _first_dataset_and_batches(mascope)
        batch = batches.iloc[0]
        real_get = _base.http_get
        paths: list[str] = []

        def counted(*args, **kwargs):
            paths.append(kwargs.get("path", args[1] if len(args) > 1 else ""))
            return real_get(*args, **kwargs)

        def load():
            paths.clear()
            return mascope.load_peaks(
                dataset=dataset["dataset_id"],
                batches=batch["sample_batch_name"],
                exact=True,
                confirm_above=None,
                matches=False,
            )

        monkeypatch.setattr(_base, "http_get", counted)
        bulk = load()
        bulk_reads = [p for p in paths if p.endswith("/peaks")]
        # The same load as a server older than the batch read answers it
        monkeypatch.setattr(mascope, "_server_has_batch_peak_lists", False)
        fan_out = load()
        sample_reads = [p for p in paths if p.endswith("/peaks")]

        assert bulk_reads == [f"batch-peaks/batch/{batch['sample_batch_id']}/peaks"]
        assert len(sample_reads) == fan_out["sample_item_id"].nunique() > 1
        assert bulk.attrs["complete"] and fan_out.attrs["complete"]
        key = ["sample_item_id", "peak_id"]
        columns = [*key, "mz", "height", "area", "sample_item_name", "datetime_utc"]
        assert_frame_equal(
            bulk[columns].sort_values(key, ignore_index=True),
            fan_out[columns].sort_values(key, ignore_index=True),
        )
        assert not bulk["is_satellite"].any()

    def test_load_peaks_reads_a_large_batch_in_parts_as_in_one(
        self, mascope, monkeypatch
    ):
        from pandas.testing import assert_frame_equal

        from mascope_sdk import _loaders
        from mascope_sdk.resources import _base

        _skip_unless_attr(mascope.batch_peaks, "peak_lists")
        # The SDK that reads a batch of over a hundred samples in parts
        _skip_unless_attr(_loaders, "_CONCURRENT_BATCH_READS")
        dataset, batches = _first_dataset_and_batches(mascope)
        batch = batches.iloc[0]
        real_get = _base.http_get
        named: list[list[str] | None] = []

        def counted(*args, **kwargs):
            path = kwargs.get("path", args[1] if len(args) > 1 else "")
            if path.startswith("batch-peaks/"):
                named.append((kwargs.get("params") or {}).get("sample_item_id"))
            return real_get(*args, **kwargs)

        def load():
            named.clear()
            return mascope.load_peaks(
                dataset=dataset["dataset_id"],
                batches=batch["sample_batch_name"],
                exact=True,
                confirm_above=None,
                matches=False,
            )

        monkeypatch.setattr(_base, "http_get", counted)
        whole = load()
        count = len(mascope.samples.list(batch["sample_batch_id"]))
        assert count > 1 and named == [None]
        # A demo batch fits in one read: cut it into two parts or more
        monkeypatch.setattr(_loaders, "_SAMPLES_PER_READ", max(1, count // 3))
        parts = load()

        assert len(named) >= 2 and all(named)
        assert sum(len(n) for n in named) == count
        assert parts.attrs["complete"] and whole.attrs["complete"]
        assert parts.attrs["segments"] == whole.attrs["segments"]
        assert_frame_equal(parts, whole)

    def test_peak_lists_of_named_samples_are_theirs_in_the_whole_batch(self, mascope):
        from pandas.testing import assert_frame_equal

        _skip_unless_attr(mascope.batch_peaks, "peak_lists")
        _skip_unless_param(mascope.batch_peaks.peak_lists, "samples")
        _, batches = _first_dataset_and_batches(mascope)
        batch_id = batches.iloc[0]["sample_batch_id"]
        whole = mascope.batch_peaks.peak_lists(batch_id)
        chosen = whole["sample_item_id"].iloc[-1]

        named = mascope.batch_peaks.peak_lists(
            batch_id, samples=[chosen, "no-such-sample"]
        )

        assert [s["sample_item_id"] for s in named.attrs["samples"]] == [chosen]
        assert [
            (f["sample_item_id"], f["status_code"]) for f in named.attrs["failures"]
        ] == [("no-such-sample", 404)]
        assert_frame_equal(
            named.reset_index(drop=True),
            whole[whole["sample_item_id"] == chosen].reset_index(drop=True),
        )

    def test_load_peaks_saves_a_snapshot_that_reads_back_equal(self, mascope, tmp_path):
        import pandas as pd
        from pandas.testing import assert_frame_equal

        _skip_unless_param(mascope.load_peaks, "save_path")
        dataset, batches = _first_dataset_and_batches(mascope)
        path = tmp_path / "batch.parquet"

        peaks = mascope.load_peaks(
            dataset=dataset["dataset_id"],
            batches=batches.iloc[0]["sample_batch_name"],
            exact=True,
            confirm_above=None,
            save_path=path,
        )

        again = pd.read_parquet(path)
        assert_frame_equal(again, peaks)
        assert again.attrs["provenance"] == peaks.attrs["provenance"]
        assert again.attrs["segments"] == peaks.attrs["segments"]

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
class TestProvenanceContract:
    """The provenance block the server reports, and the loaders stamp."""

    def test_the_server_names_its_deployment_and_build(self, mascope):
        _skip_unless_attr(mascope, "provenance")

        block = mascope.provenance()

        assert block is not None, "the server answered 404 for /api/provenance"
        assert block["provenance_version"] == 1
        # Generated on the stack's first start and kept in its filestore
        assert isinstance(block["deployment_id"], str) and block["deployment_id"]
        assert {
            "mascope_version",
            "match_score_version",
            "peak_assignment_engine_version",
        } <= set(block["produced_with"])

    def test_a_loaders_frame_carries_the_block(self, mascope):
        _skip_unless_attr(mascope, "provenance")
        dataset, batches = _first_dataset_and_batches(mascope)
        batch = batches.iloc[0]

        peaks = mascope.load_peaks(
            dataset=dataset["dataset_id"],
            batches=batch["sample_batch_name"],
            exact=True,
            matches=False,
            confirm_above=None,
        )

        assert peaks is not None and not peaks.empty
        provenance = peaks.attrs["provenance"]
        assert provenance["deployment_id"] == mascope.provenance()["deployment_id"]


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


@requires_stack
class TestSampleFileContract:
    """Shape of a sample file's scan table (`/api/sample/files/{id}/scans`).

    The demo samples are cut from raw Orbitrap files the stack stores, which
    is what a scan table is read from.
    """

    @pytest.fixture(scope="class")
    def sample_file_id(self, mascope) -> str:
        _skip_unless_attr(mascope, "sample_files")
        sample = mascope.samples.get(_first_sample_id(mascope))
        assert sample.get("sample_file_id"), "a sample names the file it was cut from"
        return sample["sample_file_id"]

    def test_scan_table_has_a_row_per_scan(self, mascope, sample_file_id):
        scans = mascope.sample_files.get_scans(sample_file_id)

        assert scans is not None and not scans.empty
        # The statistics, and the trailer under the instrument's own labels.
        assert {"ScanNumber", "StartTime", "TIC", "ScanType", "MsType"} <= set(
            scans.columns
        )
        assert "FT Resolution:" in scans.columns
        # The table describes the file: every scan, in order.
        assert scans["ScanNumber"].tolist() == list(range(1, len(scans) + 1))
        assert scans.attrs["num_of_scans"] == len(scans)
        assert scans.attrs["stats_per_file"]["Model"]

    def test_fields_and_a_scan_range_narrow_the_table(self, mascope, sample_file_id):
        scans = mascope.sample_files.get_scans(
            sample_file_id,
            fields=["FT Resolution:", "MsType"],
            first_scan=2,
            last_scan=3,
        )

        assert scans is not None
        assert list(scans.columns) == ["ScanNumber", "FT Resolution:", "MsType"]
        assert scans["ScanNumber"].tolist() == [2, 3]
        # The count stays the file's, whatever was asked for.
        assert scans.attrs["num_of_scans"] > len(scans)

    def test_a_window_no_scan_is_in_is_none(self, mascope, sample_file_id):
        assert mascope.sample_files.get_scans(sample_file_id, first_scan=10**9) is None

    def test_the_axis_fingerprint_is_the_files_document(self, mascope, sample_file_id):
        _skip_unless_attr(mascope.sample_files, "get_axis_fingerprint")
        document = mascope.sample_files.get_axis_fingerprint(sample_file_id)

        assert document["schema"] == "mascope.axis_fingerprint"
        assert document["version"] == 1
        # Missing where the stack's files were converted before the
        # fingerprint existed
        assert document["status"] in {
            "ok",
            "no_fingerprint",
            "unavailable",
            "missing",
            "error",
        }
        assert document["sample_file_id"] == sample_file_id
        assert "item" not in document

    def test_a_samples_axis_fingerprint_names_its_scans(self, mascope, sample_file_id):
        _skip_unless_attr(mascope.samples, "get_axis_fingerprint")
        sample_id = _first_sample_id(mascope)
        document = mascope.samples.get_axis_fingerprint(sample_id)

        assert document["sample_file_id"] == sample_file_id
        if document["status"] not in ("ok", "no_fingerprint"):
            # A document of no scans has nothing to say of the sample's
            assert "item" not in document
            return
        item = document["item"]
        assert item["sample_item_id"] == sample_id
        assert item["scans"] and item["scans"] == sorted(item["scans"])
        if item["mode_id"] is not None:
            assert document["modes"][0]["mode_id"] == item["mode_id"]


@requires_stack
class TestSeriesContract:
    """The per-batch series and the files listing, over the compact routes a
    campaign store fills itself from (`/api/match/records/ion/series`,
    `/api/batch-peaks/records/series`, `GET /api/sample/files`).

    Each is checked against the slower read it stands in for: the ion series
    against `load_peaks`, the batch-peak series against the ledger's
    members, the files listing against the files the batches' samples name.
    """

    def test_ion_series_is_the_peaks_heights_summed_per_sample_and_ion(self, mascope):
        import numpy as np
        import pandas as pd

        _skip_unless_attr(mascope.batches, "ion_series")
        dataset, batches = _first_dataset_and_batches(mascope)
        batch = batches.iloc[0]

        series = mascope.batches.ion_series(batch["sample_batch_id"])

        assert not series.empty, "expected matched ions in the demo batch"
        assert {
            "sample_item_id",
            "target_ion_id",
            "target_compound_name",
            "target_compound_formula",
            "ion_formula",
            "ionization_mechanism",
            "intensity",
            "match_category",
        } <= set(series.columns)
        key = ["sample_item_id", "target_ion_id"]
        assert not series.duplicated(key).any()
        assert series.attrs["provenance"]["deployment_id"]

        peaks = mascope.load_peaks(
            dataset=dataset["dataset_id"],
            batches=batch["sample_batch_name"],
            exact=True,
            confirm_above=None,
            matches=True,
        )
        heights = peaks.dropna(subset=["target_ion_id"]).groupby(key)["height"].sum()
        intensity = series.set_index(key)["intensity"]
        # Every matched peak's pair is a series row. A pair matched with no
        # peak (category 0) is a row of intensity 0, and no peak row.
        assert set(heights.index) <= set(intensity.index)
        both = pd.concat([heights.rename("height"), intensity], axis=1).fillna(0.0)
        np.testing.assert_allclose(both["intensity"], both["height"], rtol=1e-9)

    def test_batch_peak_series_are_the_ledgers_members(self, mascope):
        _skip_unless_attr(mascope.batch_peaks, "series")
        _, batches = _first_dataset_and_batches(mascope)

        for batch_id in batches["sample_batch_id"]:
            series = mascope.batch_peaks.series(batch_id, min_n_present=1)

            assert {
                "sample_item_id",
                "batch_peak_id",
                "sample_peak_id",
                "batch_mz",
                "consensus_formula",
                "consensus_ion_formula",
                "consensus_tier",
                "intensity",
                "tier",
            } <= set(series.columns)
            key = ["sample_item_id", "batch_peak_id"]
            assert not series.duplicated(key).any()
            runs = mascope.batch_peaks.runs(batch_id)
            current = (
                None
                if runs is None
                else runs.loc[runs["current"], "batch_peak_run_id"].iloc[0]
            )
            assert series.attrs["run_id"] == current
            assert series.attrs["provenance"]["deployment_id"]

            # Every point joins to its sample, with no loss.
            samples = mascope.samples.list(batch_id)
            joined = series.merge(
                samples[["sample_item_id", "datetime_utc"]],
                on="sample_item_id",
                validate="many_to_one",
            )
            assert len(joined) == len(series)

            # And the points are the members, as the paged ledger read has them.
            members = mascope.batch_peaks.members(batch_id)
            if members is None:
                assert series.empty
                continue
            expected = members.set_index(key)["intensity"].sort_index()
            assert series.set_index(key)["intensity"].sort_index().equals(expected)

    def test_the_files_listing_is_the_files_the_batches_hold(self, mascope):
        _skip_unless_attr(mascope.sample_files, "list")
        held = set()
        for _, dataset in mascope.datasets.list().iterrows():
            batches = mascope.batches.list(dataset["dataset_id"])
            if batches is None:
                continue
            for batch_id in batches["sample_batch_id"]:
                samples = mascope.samples.list(batch_id)
                if samples is not None:
                    held.update(samples["sample_file_id"])
        assert held, "expected samples in the demo batches"
        # The range is bounded by the files' own start times, which a
        # sample cut from later in its file does not share.
        everything = mascope.sample_files.list()
        starts = everything.loc[everything["sample_file_id"].isin(held), "datetime_utc"]
        assert len(starts) == len(held), "a held file is missing from the listing"

        files = mascope.sample_files.list(
            datetime_min=starts.min(), datetime_max=starts.max()
        )

        assert set(files["sample_file_id"]) == held
        assert not files["sample_file_id"].duplicated().any()
        assert str(files["datetime_utc"].dt.tz) == "UTC"
        assert "acquisition_timezone" in files.columns
        assert files["datetime_utc"].is_monotonic_increasing
        assert files.attrs["provenance"]["deployment_id"]

    def test_the_files_listing_pages_through_the_server_pages(self, mascope):
        _skip_unless_attr(mascope.sample_files, "list")
        whole = mascope.sample_files.list()
        assert len(whole) > 7, "expected more demo files than a page holds"

        paged = mascope.sample_files.list(limit=7)
        second = mascope.sample_files.list(page=1, limit=7)

        assert paged["sample_file_id"].tolist() == whole["sample_file_id"].tolist()
        assert (
            second["sample_file_id"].tolist() == whole["sample_file_id"][7:14].tolist()
        )


@pytest.fixture(scope="module")
def scratch(mascope):
    """Two scratch workspaces and a client on the first; removed afterwards.

    A token cannot create a workspace, nor a batch, so these are made over a
    session of the same user (``MASCOPE_SDK_TEST_EMAIL`` /
    ``MASCOPE_SDK_TEST_PASSWORD``); the writes under test then go through the
    SDK with the token. Depends on ``mascope`` so the demo client has picked
    its workspace before these exist. Deleting a workspace deletes its
    datasets, batches and samples with it.
    """
    from types import SimpleNamespace

    from mascope_sdk import MascopeClient

    session = _signed_in(TEST_EMAIL, TEST_PASSWORD)
    tag = secrets.token_hex(4)
    workspace_ids = []
    try:
        for name in ("a", "b"):
            workspace = _api(
                session,
                "POST",
                "workspaces",
                json={"workspace_name": f"{SCRATCH_PREFIX}{tag}-{name}"},
            )
            workspace_ids.append(workspace["workspace_id"])
        dataset = _api(
            session,
            "POST",
            f"workspaces/{workspace_ids[0]}/datasets",
            json={"dataset_name": "scratch"},
        )
        yield SimpleNamespace(
            session=session,
            tag=tag,
            workspace_id=workspace_ids[0],
            other_workspace_id=workspace_ids[1],
            dataset_id=dataset["dataset_id"],
            client=MascopeClient(
                url=BASE_URL, access_token=TOKEN, workspace=workspace_ids[0]
            ),
        )
    finally:
        for workspace_id in workspace_ids:
            session.delete(f"{BASE_URL}/api/workspaces/{workspace_id}", timeout=60)


@pytest.fixture(scope="module")
def scratch_matched(mascope, scratch):
    """``(batch ID, sample ID)``: a copy of a demo batch, matches and all.

    Copied over the session, so the matching tests do not hang on the SDK's
    own copy, and saved to only there: the demo data is never written to.

    Returned once the copy has finished. Its batch reads ``ready`` from the
    start, and its samples are listed before their matches are copied, one
    level and one sample at a time, for several seconds. A save sent before
    then races the copy, and can find every row it would write already
    stored by it, so it answers ``skipped``. The sample level is copied last
    for each sample, so the copy is done when it holds as many of those rows
    as the demo batch does.
    """
    demo_batch_id = _first_batches(mascope).iloc[0]["sample_batch_id"]
    demo_matches = len(_stored_match_samples(scratch, demo_batch_id))
    assert demo_matches, "the demo batch carries its matches"
    scratch.session.post(
        f"{BASE_URL}/api/sample/batches/{demo_batch_id}/copy",
        json={"dataset_id": scratch.dataset_id, "sample_batch_name": "matched"},
        timeout=60,
    ).raise_for_status()
    copy = _eventually(scratch.client, lambda: _ready_batch(scratch, "matched"))
    batch_id = copy["sample_batch_id"]
    _eventually(
        scratch.client,
        lambda: len(_stored_match_samples(scratch, batch_id)) == demo_matches,
    )
    samples = scratch.client.samples._list_by_id(batch_id)
    return batch_id, samples.iloc[0]["sample_item_id"]


@pytest.fixture(scope="module")
def guest(scratch):
    """A client of a second user who is a guest, globally and in the scratch
    workspace: what an unauthorised write is sent with. Removed afterwards.

    Made as an owner would make a colleague's account: registered with a
    temporary password, which the first sign-in replaces, and then the
    token, minted over that sign-in's session.
    """
    from mascope_sdk import MascopeClient

    user = _api(
        scratch.session,
        "POST",
        "users/owner/register",
        json={
            "email": f"{SCRATCH_PREFIX}{scratch.tag}@example.com",
            "username": f"{SCRATCH_PREFIX}{scratch.tag}",
            "role_id": 100,  # guest
        },
        whole=True,
    )
    user_id = user["data"]["id"]
    try:
        _api(
            scratch.session,
            "POST",
            f"workspaces/{scratch.workspace_id}/members",
            json={"user_id": user_id, "workspace_role": "guest"},
        )
        session = _signed_in(user["data"]["email"], user["temporary_password"])
        password = f"Contract-{secrets.token_urlsafe(16)}"
        _api(
            session,
            "PATCH",
            "users/me/creds",
            json={
                "current_password": user["temporary_password"],
                "new_password": password,
                "verify_new_password": password,
            },
        )
        token = _api(
            session,
            "POST",
            "auth/access_token/regenerate",
            json={"service_name": "mascope_sdk"},
            whole=True,
        )["access_token"]
        yield MascopeClient(
            url=BASE_URL, access_token=token, workspace=scratch.workspace_id
        )
    finally:
        scratch.session.delete(f"{BASE_URL}/api/users/owner/{user_id}", timeout=60)


@requires_stack
class TestWriteContract:
    """Every write method round-trips on the live stack, in scratch workspaces.

    A copy, a move or a batch delete runs in the background on the server,
    so those are waited for. Matching is saved only on a scratch copy of a
    demo sample, and the compounds a test adds it removes again.
    """

    def test_dataset_create_update_move_delete(self, scratch):
        datasets = scratch.client.datasets
        _skip_unless_attr(datasets, "create")

        created = datasets.create(f"made {scratch.tag}", description="by the SDK")
        dataset_id = created["dataset_id"]
        assert created["workspace_id"] == scratch.workspace_id
        assert dataset_id in datasets.list()["dataset_id"].tolist()

        updated = datasets.update(dataset_id, name=f"renamed {scratch.tag}")
        assert updated["dataset_name"] == f"renamed {scratch.tag}"
        assert updated["dataset_description"] == "by the SDK"
        # The listing read before the write is not served from the cache.
        listed = datasets.list().set_index("dataset_id")
        assert listed.loc[dataset_id, "dataset_name"] == f"renamed {scratch.tag}"

        moved = datasets.move(dataset_id, scratch.other_workspace_id)
        assert moved["workspace_id"] == scratch.other_workspace_id
        assert dataset_id not in datasets.list()["dataset_id"].tolist()

        datasets.delete(dataset_id, workspace_id=scratch.other_workspace_id)
        remaining = _api(
            scratch.session, "GET", f"workspaces/{scratch.other_workspace_id}/datasets"
        )
        assert dataset_id not in [row["dataset_id"] for row in remaining]

    def test_batch_update_copy_delete(self, scratch):
        batches = scratch.client.batches
        _skip_unless_attr(batches, "copy")
        batch_id = _scratch_batch(scratch, "batch")

        updated = batches.update(batch_id, [], name="renamed batch")
        assert updated["sample_batch_name"] == "renamed batch"

        started = batches.copy(batch_id, scratch.dataset_id, "copied batch")
        assert started["process_id"]
        copied = _eventually(
            scratch.client, lambda: _batch_named(scratch, "copied batch")
        )

        for doomed in (batch_id, copied["sample_batch_id"]):
            assert batches.delete(doomed)["process_id"]
        _eventually(
            scratch.client,
            lambda: (
                not {batch_id, copied["sample_batch_id"]} & set(_batch_ids(scratch))
            ),
        )

    def test_sample_copy_update_move(self, mascope, scratch):
        samples = scratch.client.samples
        _skip_unless_attr(samples, "copy")
        source = _scratch_batch(scratch, "sample source")
        target = _scratch_batch(scratch, "sample target")

        started = samples.copy([_first_sample_id(mascope)], source)
        assert started["process_id"]
        copy = _eventually(scratch.client, lambda: samples._list_by_id(source)).iloc[0]
        sample_id = copy["sample_item_id"]

        sample = samples.get(sample_id)
        updated = samples.update(
            sample_id, {**sample, "sample_item_name": "renamed sample"}
        )
        assert updated["sample_item_name"] == "renamed sample"

        assert samples.move([sample_id], target)["process_id"]
        moved = _eventually(scratch.client, lambda: samples._list_by_id(target))
        # A moved sample is a new row under a new ID; the name carries over.
        assert moved["sample_item_name"].tolist() == ["renamed sample"]
        _eventually(scratch.client, lambda: samples._list_by_id(source) is None)

    def test_collection_create_update_associate_delete(self, scratch):
        _skip_unless_attr(scratch.client, "collections")
        collections = scratch.client.collections
        batch_id = _scratch_batch(scratch, "collection")

        created = collections.create(
            f"collection {scratch.tag}",
            compounds=[
                {
                    "target_compound_formula": "C2H4O2",
                    "target_compound_name": f"{SCRATCH_PREFIX}{scratch.tag}",
                }
            ],
        )
        collection_id = created["target_collection_id"]
        assert created["workspace_id"] == scratch.workspace_id
        assert len(created["target_compound"]) == 1

        updated = collections.update(collection_id, name=f"renamed {scratch.tag}")
        assert updated["target_collection_name"] == f"renamed {scratch.tag}"

        associated = collections.associate(collection_id, [batch_id])
        assert [row["sample_batch_id"] for row in associated["sample_batch"]] == [
            batch_id
        ]
        assert collections.associate(collection_id, [])["sample_batch"] == []

        # Its compound is used by no other collection, so it goes with it.
        assert collections.delete(collection_id, delete_orphan_compounds=True)[
            "message"
        ]

    def test_compound_add_update_remove(self, scratch):
        _skip_unless_attr(scratch.client, "compounds")
        compounds = scratch.client.compounds
        name = f"{SCRATCH_PREFIX}{scratch.tag}-compound"

        added = compounds.add(
            [{"target_compound_formula": "C3H6O3", "target_compound_name": name}]
        )
        assert added["existing_compounds"] == []
        (compound,) = added["created_compounds"]
        compound_id = compound["target_compound_id"]
        try:
            compounds.update(
                [
                    {
                        "target_compound_id": compound_id,
                        "target_compound_name": name + "2",
                    }
                ]
            )
            stored = _api(scratch.session, "GET", f"target/compounds/{compound_id}")
            assert stored["target_compound_name"] == name + "2"
        finally:
            compounds.remove(compound_id)
        gone = scratch.session.get(
            f"{BASE_URL}/api/target/compounds/{compound_id}", timeout=30
        )
        assert gone.status_code == 404

    def test_matching_run_and_save(self, scratch, scratch_matched):
        matching = scratch.client.matching
        _skip_unless_attr(matching, "save_batch")
        batch_id, sample_id = scratch_matched
        levels = {"match_ions", "match_compounds", "match_collections", "match_samples"}

        for ran in (
            matching.aggregate_sample(sample_id),
            matching.aggregate_batch(batch_id),
        ):
            assert set(ran) == levels
            assert ran["match_samples"], "the copied batch carries its matches"

        for name, save in (
            ("save_sample", lambda: matching.save_sample(sample_id)),
            ("save_batch", lambda: matching.save_batch(batch_id)),
            (
                "resave_batch",
                lambda: matching.resave_batch(batch_id, match_ions=False),
            ),
        ):
            # Each save clears the sample level and writes it again, so it is
            # never "skipped"; it is "partial" when other levels are unchanged.
            saved = save()
            assert saved["status"] in ("success", "partial"), (name, saved)
            assert "created or updated" in saved["message"], (name, saved)

    def test_an_unauthorised_write_names_the_object_and_the_role(self, scratch, guest):
        """The server's 403 names neither; the SDK's error does."""
        import mascope_sdk

        if not hasattr(mascope_sdk, "PermissionDeniedError"):
            pytest.skip("installed mascope_sdk predates PermissionDeniedError")
        from mascope_sdk import AuthenticationError, PermissionDeniedError

        with pytest.raises(PermissionDeniedError) as refused:
            guest.datasets.create(f"refused {scratch.tag}")
        error = refused.value
        assert error.status_code == 403
        assert (error.object_type, error.object_id) == (
            "workspace",
            scratch.workspace_id,
        )
        assert "editor" in error.required_role
        assert isinstance(error, AuthenticationError)

        with pytest.raises(PermissionDeniedError) as refused:
            guest.batches.update(_scratch_batch(scratch, "refused"), [], name="x")
        assert refused.value.object_type == "batch"
        assert "editor on the batch" in refused.value.required_role

        with pytest.raises(PermissionDeniedError) as refused:
            guest.compounds.add([{"target_compound_formula": "CH4"}])
        assert "global editor role" in refused.value.required_role


@pytest.fixture(scope="module")
def launched_run(scratch, scratch_matched):
    """A run the SDK launched on a sample of the scratch copy, read back.

    ``(sample ID, run ID, the refusal of a second launch sent at once, the
    finished run's ledger)``. Launching needs the server's peak assignment
    switch (``MASCOPE_PEAK_ASSIGNMENT=1``, or ``peak_assignment = true``
    under ``[meta]``): a stack without it refuses with a 403, and the tests
    using this skip. Only Stage A runs, which is what keeps the run short.
    """
    from types import SimpleNamespace

    peak_assignments = scratch.client.peak_assignments
    _skip_unless_attr(peak_assignments, "assign")
    from mascope_sdk import (
        AssignmentInFlightError,
        PermissionDeniedError,
        SampleIneligibleError,
    )

    batch_id, _ = scratch_matched
    samples = scratch.client.samples._list_by_id(batch_id)
    reasons = []
    for sample_id in samples["sample_item_id"]:
        try:
            run_id = peak_assignments.assign(
                sample_id, config={"run_untargeted": False}, wait=False
            )
        except PermissionDeniedError as e:
            pytest.skip(f"peak assignment is switched off on the stack: {e.message}")
        except SampleIneligibleError as e:
            reasons.append(e.reason)
            continue
        break
    else:
        pytest.skip(f"no sample of the demo batch can be assigned: {reasons}")

    # At once, while the first is still pending: the server names it.
    try:
        second = peak_assignments.assign(sample_id, wait=False)
    except AssignmentInFlightError as e:
        refusal = e
    else:
        refusal = None  # the first ended before the second arrived
        peak_assignments.wait_for_run(sample_id, second, poll_interval=1)

    ledger = peak_assignments.wait_for_run(
        sample_id, run_id, poll_interval=1, timeout=600
    )
    return SimpleNamespace(
        sample_id=sample_id, run_id=run_id, refusal=refusal, ledger=ledger
    )


@pytest.fixture(scope="module")
def judged_copy(scratch, scratch_matched):
    """The scratch copy of a demo batch, assigned and judged.

    Stage A runs on its first six samples, each launch taking seconds. The
    first batch peak that three of them assign the consensus formula is
    judged: the first sample's peak confirmed and then rejected (the
    first verdict superseded), the second's rejected, and the batch peak
    confirmed at the batch level, which the third borrows. Launching and
    judging need the server's peak assignment switch: a stack without it
    answers 403 and the tests using this skip. Recorded over the session,
    since the SDK has no verdict writes of its own here; removed with the
    scratch workspace.
    """
    from types import SimpleNamespace

    _skip_unless_attr(scratch.client.peak_assignments, "verifications_for_batch")
    batch_id, _ = scratch_matched
    session = scratch.session
    samples = list(scratch.client.samples._list_by_id(batch_id)["sample_item_id"])[:6]
    for sample_id in samples:
        response = session.post(
            f"{BASE_URL}/api/peak-assignments/sample/{sample_id}/assign",
            json={"config": {"run_untargeted": False}},
            timeout=60,
        )
        if response.status_code == 403:
            pytest.skip("peak assignment is switched off on the stack")
        if response.status_code == 422:
            continue  # a sample the engine cannot read
        response.raise_for_status()
    ledgers = {}
    for sample_id in samples:
        ledger = _eventually(
            scratch.client,
            lambda sample_id=sample_id: _settled_ledger(scratch, sample_id),
            timeout=600,
        )
        if isinstance(ledger, dict) or ledger is None:
            continue
        ledgers[sample_id] = ledger

    members = scratch.client.batch_peaks.members(batch_id)
    if members is None:
        pytest.skip("the runs folded no batch peak")
    agree = members[
        members["consensus_formula"].notna()
        & (members["assigned_formula"] == members["consensus_formula"])
        & members["sample_item_id"].isin(list(ledgers))
    ]
    shared = agree.groupby("batch_peak_id")["sample_item_id"].nunique()
    shared = shared[shared >= 3]
    if shared.empty:
        pytest.skip("no batch peak is assigned the same in three samples")
    batch_peak_id = shared.index[0]
    peak_members = agree[agree["batch_peak_id"] == batch_peak_id].sort_values(
        "sample_item_id"
    )
    first, second, third = peak_members.head(3).itertuples(index=False)

    def verify(member, verdict, evidence_level=None):
        ledger = ledgers[member.sample_item_id]
        row = ledger[ledger["sample_peak_id"] == member.sample_peak_id].iloc[0]
        return _api(
            session,
            "POST",
            f"peak-assignments/sample/{member.sample_item_id}/verify",
            json={
                "peak_assignment_id": row["peak_assignment_id"],
                "verdict": verdict,
                "evidence_level": evidence_level,
            },
        )[0]

    replaced = verify(first, "confirmed", "visual")
    current = verify(first, "rejected")
    verify(second, "rejected")
    batch_verdict = _api(
        session,
        "POST",
        f"batch-peaks/batch/{batch_id}/verify",
        json={
            "batch_peak_id": batch_peak_id,
            "verdict": "confirmed",
            "evidence_level": "pattern",
            "expected_formula": first.consensus_formula,
        },
    )[0]
    return SimpleNamespace(
        batch_id=batch_id,
        batch_peak_id=batch_peak_id,
        first=first,
        second=second,
        third=third,
        replaced=replaced,
        current=current,
        batch_verdict=batch_verdict,
    )


def _settled_ledger(scratch, sample_id):
    """A sample's ledger once its newest run has ended; ``{"failed": ...}``
    when that run did not complete, so the wait ends either way."""
    runs = scratch.client.peak_assignments.list_runs(sample_id)
    if runs is None:
        return {"failed": "no run"}
    newest = runs.iloc[0]
    if newest["status"] in ("pending", "running"):
        return None
    if newest["status"] != "completed":
        return {"failed": newest["status"]}
    return scratch.client.peak_assignments.get(
        sample_id, run_id=newest["peak_assignment_run_id"]
    )


@requires_stack
class TestPeakAssignmentWriteContract:
    """The peak-assignment writes on the live stack, on the scratch copy.

    Scoring a composition needs nothing switched on; launching a run and
    recording a verdict skip on a stack whose peak assignment is off (see
    ``launched_run``).
    """

    def test_fit_aggregate_scores_a_matched_compound(self, mascope, matched_peaks):
        _skip_unless_attr(mascope.peak_assignments, "fit_aggregate")
        sample_id, matched = matched_peaks
        peak = matched.loc[matched["height"].idxmax()]
        # get_peaks resolves mechanism ids to names, so map the name back.
        mechanisms = mascope.ionization.list()
        name_to_id = dict(
            zip(
                mechanisms["ionization_mechanism"],
                mechanisms["ionization_mechanism_id"],
            )
        )

        fit = mascope.peak_assignments.fit_aggregate(
            sample_id,
            peak["target_compound_formula"],
            name_to_id[peak["ionization_mechanism"]],
        )

        assert set(fit) == {"matches", "match_ions", "match_isotopes"}
        assert fit["match_isotopes"], "a matched compound's lines match again"
        (ion,) = fit["match_ions"]
        assert {"target_ion_formula", "ionization_mechanism", "match"} <= set(ion)
        line = fit["match_isotopes"][0]
        assert {"target_isotope_formula", "mz", "relative_abundance"} <= set(line)
        assert {"sample_peak_mz", "match_mz_error", "match_score"} <= set(line["match"])

    def test_assign_launches_a_run_and_reads_it_back(self, launched_run):
        ledger = launched_run.ledger

        assert ledger is not None and not ledger.empty
        assert set(ledger["peak_assignment_run_id"]) == {launched_run.run_id}
        run = ledger.attrs["run"]
        assert run["status"] == "completed"
        assert run["engine"] == "mascope"
        assert run["config"]["run_untargeted"] is False

    def test_a_second_launch_names_the_run_in_flight(self, launched_run):
        if launched_run.refusal is None:
            pytest.skip("the first run ended before the second launch arrived")

        assert launched_run.refusal.status_code == 409
        assert launched_run.refusal.code == "assignment_in_flight"
        assert launched_run.refusal.peak_assignment_run_id == launched_run.run_id

    def test_verify_records_a_verdict_listed_back(self, scratch, launched_run):
        import pandas as pd

        peak_assignments = scratch.client.peak_assignments
        sample_id = launched_run.sample_id
        assigned = launched_run.ledger[launched_run.ledger["assigned_formula"].notna()]
        if assigned.empty:
            pytest.skip("the run assigned no formula to verify")
        row = assigned.iloc[0]

        first = peak_assignments.verify(
            sample_id,
            row["peak_assignment_id"],
            "confirmed",
            evidence_level="visual",
            note="contract test",
        )
        second = peak_assignments.verify(sample_id, row["peak_assignment_id"], "unsure")

        assert first["verdict"] == "confirmed"
        assert first["evidence_level"] == "visual"
        assert first["peak_assignment_id"] == row["peak_assignment_id"]
        assert first["assigned_formula"] == row["assigned_formula"]
        verdicts = peak_assignments.list_verifications(sample_id)
        by_id = verdicts.set_index("assignment_verification_id")
        # Both kept; the later one is current and stamped the earlier.
        assert pd.isna(
            by_id.loc[second["assignment_verification_id"], "superseded_utc"]
        )
        assert pd.notna(
            by_id.loc[first["assignment_verification_id"], "superseded_utc"]
        )
        assert {
            "peak_assignment_run_id",
            "sample_peak_id",
            "fit_score",
            "verified_utc",
        } <= set(verdicts.columns)


@requires_stack
class TestScopeVerificationsContract:
    """The verdicts of a batch or dataset in one read, on the scratch copy."""

    def test_a_batch_reads_one_current_verdict_per_judged_peak(
        self, scratch, judged_copy
    ):
        frame = scratch.client.peak_assignments.verifications_for_batch(
            judged_copy.batch_id
        )
        # One current row per judged peak.
        assert not frame.duplicated(["sample_item_id", "sample_peak_id"]).any()
        rows = frame.set_index(["sample_item_id", "sample_peak_id"])

        def row_of(member):
            return rows.loc[(member.sample_item_id, member.sample_peak_id)]

        first = row_of(judged_copy.first)
        assert first["verdict"] == "rejected" and not first["borrowed"]
        assert (
            first["assignment_verification_id"]
            == judged_copy.current["assignment_verification_id"]
        )
        second = row_of(judged_copy.second)
        assert second["verdict"] == "rejected" and not second["borrowed"]
        third = row_of(judged_copy.third)
        assert third["verdict"] == "confirmed" and third["borrowed"]
        assert third["batch_peak_id"] == judged_copy.batch_peak_id
        assert third["evidence_level"] == "pattern"
        assert {
            "filename",
            "datetime_utc",
            "sample_peak_mz",
            "evidence_level",
            "fit_score",
            "evidence",
            "p_correct",
            "borrowed",
        } <= set(frame.columns)
        assert frame["superseded_utc"].isna().all()

    def test_the_superseded_verdict_only_on_request(self, scratch, judged_copy):
        read = scratch.client.peak_assignments.verifications_for_batch
        frame = read(judged_copy.batch_id, include_superseded=True)

        replaced = frame[
            frame["assignment_verification_id"]
            == judged_copy.replaced["assignment_verification_id"]
        ]
        assert len(replaced) == 1
        assert replaced["superseded_utc"].notna().all()
        assert replaced["evidence_level"].tolist() == ["visual"]

    def test_the_dataset_read_is_the_batch_read(self, scratch, judged_copy):
        peak_assignments = scratch.client.peak_assignments
        batch = peak_assignments.verifications_for_batch(judged_copy.batch_id)
        dataset = peak_assignments.verifications_for_dataset(scratch.dataset_id)

        # The scratch dataset's other batches carry no verdict.
        assert dataset.reset_index(drop=True).equals(batch.reset_index(drop=True))

    def test_filters_are_applied_on_the_server(self, scratch, judged_copy):
        read = scratch.client.peak_assignments.verifications_for_batch
        confirmed = read(judged_copy.batch_id, verdict="confirmed")

        # The two judged samples keep their own rejections: the batch-level
        # confirmation reaches only the third.
        assert set(confirmed["sample_item_id"]) >= {judged_copy.third.sample_item_id}
        assert (
            not confirmed["sample_item_id"]
            .isin([judged_copy.first.sample_item_id, judged_copy.second.sample_item_id])
            .any()
        )
        assert confirmed["borrowed"].all()


@requires_stack
class TestPeakAssignmentImportContract:
    """An external engine's run published with ``import_run`` and read back,
    on the scratch copy. Skips on a stack whose peak assignment is off, as
    the import is refused there with a 403.
    """

    def test_a_two_row_import_reads_back_with_its_engine_provenance(
        self, scratch, scratch_matched
    ):
        import pandas as pd

        peak_assignments = scratch.client.peak_assignments
        _skip_unless_attr(peak_assignments, "import_run")
        from mascope_sdk import PermissionDeniedError

        batch_id, _ = scratch_matched
        # The last sample: the launch tests take the first they can assign.
        sample_id = scratch.client.samples._list_by_id(batch_id).iloc[-1][
            "sample_item_id"
        ]
        sample = scratch.client.samples.get(sample_id)
        intensity = "height" if sample["instrument_type"] == "orbi" else "area"
        peaks = scratch.client.samples.get_peaks(sample_id, matches=False)
        top, other = peaks.nlargest(2, intensity).to_dict("records")
        frame = pd.DataFrame(
            [
                {
                    "sample_peak_id": top["peak_id"],
                    "sample_peak_mz": top["mz"],
                    "sample_peak_intensity": top[intensity],
                    "role": "M0",
                    "assigned_formula": "C6H12O6",
                    "source": "untargeted",
                    "fit_score": 0.9,
                    "engine_tier": "candidate",
                    "provenance": {
                        # Reserved: the server strips them.
                        "p_correct": 0.99,
                        "calibration": {"provisional": False},
                        "corroboration": {"n_adducts": 3},
                        "engine_provenance": {"p_correct": 0.42},
                    },
                },
                {
                    "sample_peak_id": other["peak_id"],
                    "sample_peak_mz": other["mz"],
                    "sample_peak_intensity": other[intensity],
                    "role": "unassigned",
                },
            ]
        )

        try:
            run_id = peak_assignments.import_run(
                sample_id,
                frame,
                engine="sdk-contract",
                engine_version="1.0",
                tier_bands={"assigned": 0.75, "candidate": 0.45},
                calibration={"method": "none"},
                config={"purpose": "contract test"},
            )
        except PermissionDeniedError as e:
            pytest.skip(f"peak assignment is switched off on the stack: {e.message}")

        ledger = peak_assignments.get(sample_id, run_id=run_id)
        assert sorted(ledger["sample_peak_id"]) == sorted(frame["sample_peak_id"])
        run = ledger.attrs["run"]
        assert run["status"] == "completed"
        assert run["engine"] == "sdk-contract"
        assert run["engine_version"] == "1.0"
        assert run["calibration"] == {"method": "none"}
        assert run["tier_bands"] == {"assigned": 0.75, "candidate": 0.45}
        assert run["config"] == {"purpose": "contract test"}
        row = ledger.set_index("sample_peak_id").loc[top["peak_id"]]
        assert row["engine_tier"] == "candidate"
        assert row["tier"] in ("assigned", "candidate", "below_assignability")
        assert pd.isna(row["p_correct"])

        full = peak_assignments.detail(sample_id, row["peak_assignment_id"])
        provenance = full["provenance"]
        assert provenance["engine_provenance"] == {"p_correct": 0.42}
        assert not {"p_correct", "calibration", "corroboration"} & set(provenance)


def _signed_in(email: str, password: str):
    """A session signed in as *email*, for what a token may not do."""
    import requests

    session = requests.Session()
    session.post(
        f"{BASE_URL}/api/auth/login",
        data={"username": email, "password": password},
        timeout=30,
    ).raise_for_status()
    return session


def _api(session, method: str, path: str, whole: bool = False, **kwargs):
    """Send *method* over *session*; the answer's ``data``, or all of it."""
    response = session.request(method, f"{BASE_URL}/api/{path}", timeout=60, **kwargs)
    response.raise_for_status()
    body = response.json()
    return body if whole else body["data"]


def _scratch_batch(scratch, name: str) -> str:
    """A new, empty batch in the scratch dataset."""
    batch = _api(
        scratch.session,
        "POST",
        "sample/batches",
        json={
            "dataset_id": scratch.dataset_id,
            "sample_batch_name": f"{name} {secrets.token_hex(3)}",
            "target_collection_ids": [],
        },
    )
    return batch["sample_batch_id"]


def _batch_ids(scratch) -> list[str]:
    batches = scratch.client.batches._list_by_id(scratch.dataset_id)
    return [] if batches is None else batches["sample_batch_id"].tolist()


def _stored_match_samples(scratch, batch_id: str) -> list:
    """The sample-level match rows stored for batch *batch_id*."""
    return _api(
        scratch.session,
        "GET",
        "match/samples",
        params={"sample_batch_id": batch_id},
    )


def _ready_batch(scratch, name: str):
    """The batch called *name* once the server has finished making it."""
    batch = _batch_named(scratch, name)
    return batch if batch is not None and batch["status"] == "ready" else None


def _batch_named(scratch, name: str):
    batches = scratch.client.batches._list_by_id(scratch.dataset_id)
    if batches is None:
        return None
    named = batches[batches["sample_batch_name"] == name]
    return None if named.empty else named.iloc[0]


def _eventually(client, probe, timeout: float = 120):
    """What *probe* returns once there is something, for a write the server
    runs in the background; fails after *timeout* seconds.

    The client caches its listings, and a write that finishes in the
    background cannot clear that, so the cache is cleared before each probe.
    """
    import time

    deadline = time.monotonic() + timeout
    while True:
        client.clear_cache()
        result = probe()
        if hasattr(result, "empty"):
            present = not result.empty
        else:
            present = bool(result)
        if present:
            return result
        if time.monotonic() > deadline:
            raise AssertionError(f"still waiting after {timeout} s")
        time.sleep(1)


def _skip_unless_attr(obj, name: str) -> None:
    """Skip when the installed SDK predates attribute *name* on *obj*."""
    if not hasattr(obj, name):
        pytest.skip(f"installed mascope_sdk predates .{name}")


def _skip_unless_column(frame, name: str) -> None:
    """Skip when the installed SDK predates a client-derived column.

    The frame comes back from a healthy server either way: columns the SDK
    assembles itself (see `resources/samples.py`'s `match_keys`) are absent
    only because the installed SDK is older than they are, which is exactly
    what the nightly published-SDK run must not report as a regression.
    """
    if name not in frame.columns:
        pytest.skip(f"installed mascope_sdk predates the {name} column")


def _skip_unless_param(func, name: str) -> None:
    """Skip when the installed SDK's *func* predates the *name* parameter."""
    import inspect

    if name not in inspect.signature(func).parameters:
        pytest.skip(f"installed mascope_sdk predates {func.__name__}({name}=...)")


def _reference_sources(mascope):
    """The server's reference sources, else skip: a stack may load none."""
    _skip_unless_attr(mascope, "reference")
    sources = mascope.reference.sources()
    if sources.empty:
        pytest.skip("stack carries no reference source")
    return sources


def _largest_reference_source(mascope):
    """The reference source with the most compounds, as a row of sources()."""
    sources = _reference_sources(mascope)
    return sources.loc[sources["record_count"].idxmax()]


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
