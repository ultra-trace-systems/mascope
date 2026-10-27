"""
The toolkit against a live demo stack: find, describe, and the published
demo dataset's peaks loaded through the loaders.

Opt-in, with the same switch and settings as the SDK's contract tests
(``libraries/sdk/tests/test_contract.py``): a running demo stack and
``MASCOPE_SDK_CONTRACT=1``; ``MASCOPE_SDK_TEST_URL``,
``MASCOPE_SDK_TEST_TOKEN`` and ``MASCOPE_SDK_TEST_WORKSPACE`` point it
elsewhere. Locally::

    docker compose -f docker-compose.demo.yaml up -d
    MASCOPE_SDK_CONTRACT=1 uv run pytest libraries/ai/tests/ -v

The inputs come from the demo data itself and the assertions are on shapes
and agreement with the SDK, not on values, so they survive bundle updates.
"""

import os

import pytest

from mascope_ai import AmbiguousName, Frame, RowCapExceeded, Session, Toolkit


BASE_URL = os.environ.get("MASCOPE_SDK_TEST_URL", "http://127.0.0.1:8080")
TOKEN = os.environ.get("MASCOPE_SDK_TEST_TOKEN", "mascope_demo_sdk_token")

pytestmark = pytest.mark.skipif(
    os.environ.get("MASCOPE_SDK_CONTRACT") != "1",
    reason=(
        "contract tests need a running demo stack; start one "
        "(docker compose -f docker-compose.demo.yaml up -d) and set "
        "MASCOPE_SDK_CONTRACT=1"
    ),
)


def _workspace_with_data() -> str | None:
    """The first workspace with datasets (the demo stack also has a system
    workspace, so the client cannot pick one by itself)."""
    import requests

    override = os.environ.get("MASCOPE_SDK_TEST_WORKSPACE")
    if override:
        return override
    headers = {"Authorization": f"Bearer {TOKEN}", "X-Service-Name": "mascope_sdk"}
    workspaces = requests.get(
        f"{BASE_URL}/api/workspaces", headers=headers, timeout=30
    ).json()["data"]
    for workspace in workspaces:
        datasets = requests.get(
            f"{BASE_URL}/api/workspaces/{workspace['workspace_id']}/datasets",
            headers=headers,
            timeout=30,
        ).json()
        if datasets.get("data"):
            return workspace["workspace_name"]
    return workspaces[0]["workspace_name"] if workspaces else None


@pytest.fixture(scope="module")
def kit(tmp_path_factory) -> Toolkit:
    from mascope_sdk import MascopeClient

    client = MascopeClient(
        url=BASE_URL,
        access_token=TOKEN,
        workspace=_workspace_with_data(),
        service_name="mascope_sdk",
    )
    return Toolkit(client, session=Session(root=tmp_path_factory.mktemp("sessions")))


@pytest.fixture(scope="module")
def batch(kit):
    """The demo's first batch that has samples."""
    for batch in kit.find("", kind="batch", limit=1000):
        if not kit._sample_rows(batch).empty:
            return batch
    pytest.fail("the demo stack has no batch with samples")


@pytest.fixture(scope="module")
def dataset(kit, batch):
    return kit.resolve(batch.parent, "dataset")


@pytest.fixture(scope="module")
def peaks(kit, dataset) -> Frame:
    """The demo dataset's peaks, loaded through the toolkit."""
    return kit.load_peaks(dataset=dataset)


class TestFindAndDescribe:
    def test_find_lists_every_kind_but_samples(self, kit):
        found = kit.find("")

        assert found.complete
        assert {"workspace", "dataset", "batch"} <= {ref.kind for ref in found}

    def test_find_covers_collections(self, kit):
        collections = kit.find("", kind="collection")

        assert collections, "the demo matches its samples against some collection"
        assert kit.describe(collections[0])["n_compounds"] >= 0

    def test_a_name_resolves_to_its_object_or_to_candidates(self, kit, batch):
        try:
            assert kit.resolve(batch.name, "batch").id == batch.id
        except AmbiguousName as e:
            assert batch.id in {ref.id for ref in e.candidates}

    def test_describe_a_batch(self, kit, batch):
        out = kit.describe(batch)

        assert out["ref"]["id"] == batch.id
        assert out["n_samples"] > 0
        assert isinstance(out["collections"], list)
        # Every demo sample is processed under a mode the server names
        assert out["ionization_modes"]
        assert all(
            mode["name"] and mode["mechanisms"] for mode in out["ionization_modes"]
        )

    def test_describe_a_sample(self, kit, batch):
        sample = kit.find("", kind="sample", within=batch, limit=1)[0]

        out = kit.describe(sample)

        assert out["batch_id"] == batch.id
        assert out["ionization_mode"] and out["ionization_mechanisms"]


class TestLoaders:
    def test_the_demo_datasets_peaks(self, kit, dataset, peaks):
        assert peaks.n_rows > 0
        assert peaks.complete, peaks.failures
        assert {"peak_id", "mz", "height", "sample_item_id"} <= set(peaks.columns)
        assert [ref.id for ref in peaks.source_refs] == [dataset.id]
        assert peaks.provenance and "produced_with" in peaks.provenance

    def test_they_are_the_sdks_rows(self, kit, dataset, peaks):
        direct = kit.client.load_peaks(dataset.id, confirm_above=None)

        assert peaks.n_rows == len(direct)
        assert sorted(peaks.df["peak_id"].unique()) == sorted(
            direct["peak_id"].unique()
        )

    def test_the_cap_stops_the_load(self, kit, dataset, peaks):
        samples = peaks.df["sample_item_id"].nunique()
        if samples < 2:
            pytest.skip("the cap needs a dataset of two samples or more to stop")
        cap = peaks.n_rows // 2

        with pytest.raises(RowCapExceeded) as caught:
            kit.load_peaks(dataset=dataset, max_rows=cap)

        error = caught.value
        assert error.rows > cap or error.estimated
        assert error.done + len(error.left) <= samples

    def test_a_spectrum(self, kit, peaks):
        sample_id = peaks.df["sample_item_id"].iloc[0]
        mz = float(peaks.df["mz"].iloc[0])

        spectrum = kit.load_spectrum(sample_id, mz_range=(mz - 0.5, mz + 0.5))

        assert spectrum.columns == ("mz", "intensity")
        assert spectrum.n_rows > 0

    def test_stages(self, kit, peaks):
        sample_id = peaks.df["sample_item_id"].iloc[0]
        listed = peaks.df.loc[peaks.df["sample_item_id"] == sample_id, "peak_id"]

        frame = kit.load_peaks_by_stage(sample_id, [(0, 1e6, "all")])

        assert "stage_name" in frame.columns or frame.n_rows == 0
        staged = frame.df["peak_id"].nunique() if frame.n_rows else 0
        # A stage over the whole acquisition is the sample's peak list, or
        # says which of its peaks it left out: those without a time series
        left_out = [note.where.get("excluded_peaks") for note in frame.failures]
        assert all(
            note.error_type == "TimeseriesNotComputed" for note in frame.failures
        ), frame.failures
        assert frame.complete == (not left_out)
        assert staged + sum(left_out) == listed.nunique()

    def test_timeseries_of_a_matched_compound(self, kit, peaks):
        df = peaks.df
        matched = df[df["target_compound_formula"].notna()]
        if matched.empty:
            pytest.skip("demo data carries no matched peak")
        row = matched.iloc[0]

        frame = kit.load_timeseries(
            compound=row["target_compound_formula"], samples=row["sample_item_id"]
        )

        assert frame.n_rows > 0 and frame.complete

    def test_a_frame_reloads_from_its_query(self, kit, peaks):
        sample_id = peaks.df["sample_item_id"].iloc[0]
        frame = kit.load_peaks(samples=sample_id)
        kit.session.evict(frame.id)
        frame.evict()

        assert len(frame.df) == frame.n_rows


@pytest.fixture(scope="module")
def best_match(peaks):
    """The stored match the demo is surest of: the matched peak whose
    compound scores highest, monoisotopic where one is."""
    df = peaks.df
    matched = df[
        df["target_compound_formula"].notna() & df["match_score_isotope"].gt(0)
    ]
    if matched.empty:
        pytest.skip("demo data carries no matched peak")
    mono = matched[matched["target_isotope_formula"] == matched["target_ion_formula"]]
    pool = mono if not mono.empty else matched
    return pool.loc[pool["match_score_compound"].idxmax()]


class TestOracle:
    def test_a_verdict_matches_the_apps_match_of_a_peak(self, kit, peaks, best_match):
        sample = best_match["sample_item_id"]

        result = kit.match_compounds(sample, [best_match["target_compound_formula"]])

        (verdict,) = result.verdicts
        ions = {ion.formula: ion for ion in verdict.ions}
        ion = ions[best_match["target_ion_formula"]]
        assert ion.mechanism == best_match["ionization_mechanism"]
        assert best_match["peak_id"] in ion.peak_ids
        assert verdict.status != "unmatched"
        assert best_match["peak_id"] in verdict.peak_ids
        # The SDK sums a peak load's ion scores itself, as Mascope's first
        # scoring does; a server on another scoring scores otherwise
        if (peaks.provenance or {}).get("produced_with", {}).get(
            "match_score_version"
        ) == 1:
            assert verdict.score == pytest.approx(best_match["match_score_compound"])
        assert result.tree.n_rows > 0
        assert not result.unscored

    def test_a_suggestion_mascope_cannot_read_is_unscored(self, kit, best_match):
        result = kit.match_compounds(
            best_match["sample_item_id"],
            [best_match["target_compound_formula"]],
            suggested=["Qq2"],
        )

        assert len(result.verdicts) == 1
        (unscored,) = result.unscored
        assert (unscored.formula, unscored.origin) == ("Qq2", "model")
        assert unscored.reason.startswith("Mascope refused it")

    def test_the_mass_query_proposes_a_matched_peaks_formula(self, kit, peaks):
        df = peaks.df
        chon = df[
            df["target_compound_formula"].fillna("").str.fullmatch(r"[CHON0-9]+")
            & (df["target_isotope_formula"] == df["target_ion_formula"])
        ]
        if chon.empty:
            pytest.skip("demo data carries no monoisotopic CHON match")
        peak = chon.loc[chon["height"].idxmax()]

        found = kit.query_by_mz(
            float(peak["mz"]), [peak["ionization_mechanism"]], tolerance_ppm=30
        )

        assert peak["target_compound_formula"] in {c.formula for c in found}
        assert all(abs(c.error_ppm) <= 30 for c in found)


class TestManifest:
    def test_it_lists_every_object_a_session_touched(self, kit, best_match, tmp_path):
        own = Toolkit(kit.client, session=Session(root=tmp_path))
        sample = own.resolve(best_match["sample_item_id"], "sample")
        batch = own.resolve(sample.parent, "batch")

        own.load_peaks(batches=batch, max_rows=10_000_000)
        own.match_compounds(sample, [best_match["target_compound_formula"]])
        record = own.answer("a verdict", code="...")

        touched = own.session.manifest.touched()
        assert [(ref.kind, ref.id) for ref in touched] == [
            ("sample", sample.id),
            ("batch", batch.id),
        ]
        assert {ref["id"] for ref in record["refs"]} == {batch.id, sample.id}
        assert record["values"]["client"][0]["op"] == "load_peaks"
