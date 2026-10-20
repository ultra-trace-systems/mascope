"""
The four loaders: the row cap held during the load, completeness from the
SDK's strict loader, no prompt ever, and each Frame kept in the session and
reloadable from its recorded query.

Hermetic: the workspace of ``conftest.py`` behind a real SDK client. Each
sample answers with the recorded peak list, ``api.peak_copies`` times over.
"""

import json
from pathlib import Path

import pytest

from mascope_ai import (
    InvalidRequest,
    NotFound,
    Ref,
    RowCapExceeded,
    Session,
    StaleFrame,
)
from mascope_ai import toolkit as toolkit_module


#: Rows one sample's recorded peak list gives, its matches expanded (a peak
#: matching two isotopes is two rows).
ROWS = sum(
    max(len(matches), 1)
    for matches in json.loads(
        (Path(__file__).parent / "recorded_api.json").read_text()
    )["peaks"]["data"]["match"]
)


class TestLoadPeaks:
    def test_a_batch(self, kit):
        frame = kit.load_peaks(batches="Spring 2026")

        assert frame.n_rows == 3 * ROWS
        assert sorted(set(frame.df["sample_item_id"])) == ["s21", "s22", "s23"]
        assert frame.complete is True and frame.failures == ()
        assert [ref.id for ref in frame.source_refs] == ["b2"]
        assert frame.provenance["deployment_id"] == "deployment-x"

    def test_a_dataset_loads_all_its_batches(self, kit):
        frame = kit.load_peaks(dataset="Campaign (2026)")

        assert sorted(set(frame.df["sample_item_id"])) == [
            "s11",
            "s21",
            "s22",
            "s23",
            "s31",
        ]
        assert [ref.id for ref in frame.source_refs] == ["d1"]

    def test_samples_are_what_is_loaded(self, kit, api):
        frame = kit.load_peaks(batches="Spring 2026", samples=["Sample 1", "s23"])

        assert sorted(set(frame.df["sample_item_id"])) == ["s21", "s23"]
        assert len(api.requests_to(r"samples/\w+/peaks")) == 2
        assert [ref.id for ref in frame.source_refs] == ["b2", "s21", "s23"]

    def test_a_sample_name_is_looked_for_in_the_batches_named(self, kit, api):
        # "Sample 1" is in b2 and in b3; of b1 and b2, only in b2
        frame = kit.load_peaks(batches=["b1", "b2"], samples="Sample 1")

        assert set(frame.df["sample_item_id"]) == {"s21"}
        listed = {c[2]["sample_batch_id"] for c in api.requests_to("samples")}
        assert listed == {"b1", "b2"}

    def test_a_sample_its_batch_does_not_list_is_not_found(self, kit, api):
        ghost = Ref("s99", "sample", "ghost", parent="b1")

        with pytest.raises(NotFound, match="s99"):
            kit.load_peaks(samples=[ghost, "s11"])
        assert api.requests_to(r"samples/\w+/peaks") == []

    def test_a_sample_outside_the_batches_named_is_refused(self, kit):
        with pytest.raises(InvalidRequest, match="none of the batches"):
            kit.load_peaks(batches="Spring 2026", samples="s41")

    def test_a_batch_outside_the_dataset_named_is_refused(self, kit):
        spring = kit.resolve("Spring 2026", "batch")

        with pytest.raises(InvalidRequest, match="is not in"):
            kit.load_peaks(dataset="Calibration runs", batches=spring)

    def test_nothing_named(self, kit):
        with pytest.raises(InvalidRequest, match="Name a dataset"):
            kit.load_peaks()

    def test_an_ambiguous_name_is_not_loaded(self, kit, api):
        from mascope_ai import AmbiguousName

        with pytest.raises(AmbiguousName):
            kit.load_peaks(batches="spring")
        assert api.requests_to(r"samples/\w+/peaks") == []

    def test_the_options_reach_the_server(self, kit, api):
        kit.load_peaks(samples="s21", matches=False, areas=False, average=False)

        ((_, _, params, _),) = api.requests_to(r"samples/s21/peaks")
        assert params == {
            "areas": "false",
            "heights": "true",
            "average": "false",
            "matches": "false",
        }

    def test_a_failed_sample_makes_the_frame_incomplete(self, kit, api):
        api.fail("GET", "samples/s22/peaks", 500)

        frame = kit.load_peaks(batches="Spring 2026")

        assert frame.complete is False
        assert [note.where["sample_item_id"] for note in frame.failures] == ["s22"]
        assert frame.failures[0].error_type == "ServerError"
        assert frame.n_rows == 2 * ROWS
        assert "INCOMPLETE" in repr(frame)

    def test_it_never_prompts(self, kit, api):
        # conftest's input() raises; 6 samples is above the SDK's prompt
        # threshold only if one is applied, which it must not be
        api.peak_copies = 1
        frame = kit.load_peaks(dataset="Campaign (2026)")

        assert frame.n_rows == 5 * ROWS


class TestRowCap:
    def test_the_estimate_refuses_an_obviously_oversized_load(self, kit, api):
        with pytest.raises(RowCapExceeded) as caught:
            kit.load_peaks(batches="Spring 2026", max_rows=250)

        error = caught.value
        assert error.estimated is True
        assert error.rows == 3 * toolkit_module.ROWS_PER_SAMPLE_ESTIMATE
        assert error.cap == 250 and error.done == 0
        assert [item["sample_item_id"] for item in error.left] == ["s21", "s22", "s23"]
        assert api.requests_to(r"samples/\w+/peaks") == []

    def test_the_running_count_stops_the_load_at_the_cap(self, kit, api, monkeypatch):
        monkeypatch.setattr(toolkit_module, "ROWS_PER_SAMPLE_ESTIMATE", 1)
        kit.max_workers = 1
        api.peak_copies = 10  # 10 * ROWS rows a sample

        with pytest.raises(RowCapExceeded) as caught:
            kit.load_peaks(dataset="Campaign (2026)", max_rows=15 * ROWS)

        error = caught.value
        assert error.estimated is False
        assert error.rows == 20 * ROWS  # two samples read, the second passed it
        assert error.done == 2
        assert len(error.left) == 3
        assert error.to_dict()["n_left"] == 3
        assert "max_rows" in str(error)
        # One worker: it may have started the next sample by the time the
        # count passed the cap, but no other
        assert len(api.requests_to(r"samples/\w+/peaks")) <= 3

    def test_no_frame_is_made_above_the_cap(self, kit, api, monkeypatch):
        monkeypatch.setattr(toolkit_module, "ROWS_PER_SAMPLE_ESTIMATE", 1)
        api.peak_copies = 10

        with pytest.raises(RowCapExceeded):
            kit.load_peaks(batches="Spring 2026", max_rows=ROWS)

        assert kit.session.frames() == []

    def test_a_load_at_the_cap_is_kept(self, kit, monkeypatch):
        monkeypatch.setattr(toolkit_module, "ROWS_PER_SAMPLE_ESTIMATE", 1)

        frame = kit.load_peaks(batches="Spring 2026", max_rows=3 * ROWS)

        assert frame.n_rows == 3 * ROWS

    @pytest.mark.parametrize("cap", [0, -1, 1.5, True, "10"])
    def test_a_cap_is_a_positive_int(self, kit, cap):
        with pytest.raises(InvalidRequest):
            kit.load_peaks(batches="Spring 2026", max_rows=cap)


class TestOtherLoaders:
    def test_timeseries(self, kit):
        formula = kit.load_peaks(samples="s21").df["target_compound_formula"].dropna()

        frame = kit.load_timeseries(compound=formula.iloc[0], batches="Spring 2026")

        assert set(frame.df["sample_item_id"]) == {"s21", "s22", "s23"}
        assert frame.complete is True
        assert frame.query["params"]["level"] == "compound"

    def test_timeseries_cap_names_the_samples_left(self, kit, api):
        formula = kit.load_peaks(samples="s21").df["target_compound_formula"].dropna()
        kit.max_workers = 1

        with pytest.raises(RowCapExceeded) as caught:
            kit.load_timeseries(
                compound=formula.iloc[0], batches="Spring 2026", max_rows=1
            )

        assert caught.value.done == 1
        assert [item["sample_item_id"] for item in caught.value.left] == ["s22", "s23"]
        assert "no peak matched" in str(caught.value)

    def test_timeseries_wants_one_target_kind(self, kit):
        with pytest.raises(InvalidRequest, match="exactly one"):
            kit.load_timeseries(compound="C2H4O2", ion="C2H3O2-", batches="Spring 2026")

    def test_spectrum(self, kit, api):
        frame = kit.load_spectrum("s21", mz_range=(100, 101), t_range=(0, 5))

        assert frame.columns == ("mz", "intensity")
        ((_, _, params, _),) = api.requests_to(r"samples/s21/spectrum")
        assert params == {"mz_min": 100, "mz_max": 101, "t_min": 0, "t_max": 5}

    def test_spectrum_above_the_cap_is_not_kept(self, kit):
        with pytest.raises(RowCapExceeded) as caught:
            kit.load_spectrum("s21", max_rows=3)

        assert caught.value.unit == "spectrum" and caught.value.rows == 7
        assert kit.session.frames() == []

    def test_stages(self, kit, api):
        frame = kit.load_peaks_by_stage("s21", [(0, 5, "first"), (5, 10)])

        assert frame.n_rows == 2 * ROWS
        assert set(frame.df["stage_name"].dropna()) == {"first"}
        ranged = [c[2] for c in api.requests_to(r"samples/s21/peaks")]
        assert sorted((p["t_min"], p["t_max"]) for p in ranged) == [(0, 5), (5, 10)]

    def test_stages_cap(self, kit, monkeypatch):
        monkeypatch.setattr(toolkit_module, "ROWS_PER_SAMPLE_ESTIMATE", 1)
        kit.max_workers = 1

        with pytest.raises(RowCapExceeded) as caught:
            kit.load_peaks_by_stage("s21", [(0, 5), (5, 10), (10, 15)], max_rows=ROWS)

        assert caught.value.unit == "stage" and caught.value.done == 2
        assert [item["stage"] for item in caught.value.left] == [2]

    def test_stages_estimate(self, kit):
        with pytest.raises(RowCapExceeded) as caught:
            kit.load_peaks_by_stage("s21", [(0, 5), (5, 10)], max_rows=150)

        assert caught.value.estimated is True

    def test_stages_of_a_sample_gone(self, kit, api):
        api.fail("GET", "samples/s99", 404)

        with pytest.raises(NotFound, match="s99"):
            kit.load_peaks_by_stage(Ref("s99", "sample", "gone", parent="b1"), [(0, 5)])

    @pytest.mark.parametrize("stages", [[], [(1,)], ["ab"]])
    def test_bad_stages(self, kit, stages):
        with pytest.raises(InvalidRequest):
            kit.load_peaks_by_stage("s21", stages)


class TestConnecting:
    def test_a_client_of_its_own_presents_the_ai_agent_service(
        self, monkeypatch, tmp_path
    ):
        made: list[dict] = []

        class Client:
            def __init__(self, **kwargs):
                made.append(kwargs)

        monkeypatch.setattr(toolkit_module, "MascopeClient", Client)

        toolkit_module.Toolkit(workspace="Workspace A", session=Session(root=tmp_path))

        assert made == [{"workspace": "Workspace A", "service_name": "ai-agent"}]


class TestSessionRoundTrip:
    def test_a_frame_is_kept_and_reopened(self, kit):
        frame = kit.load_peaks(batches="Spring 2026")

        again = kit.session.frame(frame.id)

        assert again.to_dict() == frame.to_dict()
        assert again.df.shape == frame.df.shape

    def test_evicted_rows_come_back_through_the_recorded_query(self, kit, api):
        frame = kit.load_peaks(batches="Spring 2026", samples="Sample 2", matches=False)
        kit.session.evict(frame.id)
        frame.evict()
        api.calls.clear()

        rows = frame.df

        assert len(rows) == frame.n_rows
        # Loaded again by ID, with the recorded options: no name resolved
        assert [c[1] for c in api.requests_to(r"samples/\w+/peaks")] == [
            "samples/s22/peaks"
        ]
        assert api.requests_to(r"samples/\w+/peaks")[0][2]["matches"] == "false"
        assert api.requests_to("samples/s22") == []

    @pytest.mark.parametrize(
        "load",
        [
            lambda kit: kit.load_spectrum("s21", mz_range=(100, 101)),
            lambda kit: kit.load_peaks_by_stage("s21", [(0, 5, "a")]),
            lambda kit: kit.load_timeseries(ion="nothing-matches", samples="s21"),
        ],
    )
    def test_every_loader_reloads(self, kit, load):
        frame = load(kit)
        kit.session.evict(frame.id)
        frame.evict()

        assert len(frame.df) == frame.n_rows

    def test_rows_that_changed_are_not_kept_as_the_frame(self, kit, api):
        frame = kit.load_peaks(batches="Spring 2026")
        kit.session.evict(frame.id)
        frame.evict()
        api.peak_copies = 2

        with pytest.raises(StaleFrame, match=f"{6 * ROWS:,} rows"):
            frame.df  # noqa: B018
        assert not (kit.session.path / "frames" / f"{frame.id}.parquet").exists()

    def test_a_read_that_fails_on_reload_is_not_kept_as_the_frame(self, kit, api):
        frame = kit.load_peaks(batches="Spring 2026")
        kit.session.evict(frame.id)
        frame.evict()
        api.fail("GET", "samples/s22/peaks", 500)

        with pytest.raises(StaleFrame) as caught:
            frame.df  # noqa: B018
        assert any("a read failed" in d for d in caught.value.differences)

    def test_a_query_the_toolkit_did_not_record(self, kit):
        with pytest.raises(InvalidRequest):
            kit._rerun({"op": "delete_everything", "params": {}})
