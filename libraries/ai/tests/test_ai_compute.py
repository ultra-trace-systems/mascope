"""
``compute_timeseries``: the time series a stage load left out, computed
under a cap, so that the stage loaded again is whole.

Hermetic: the workspace of ``conftest.py`` behind a real SDK client. A
sample's peaks lose their series through ``api.drop_series``, and each
time-series request computes one again.
"""

import pytest

from mascope_ai import DEFAULT_MAX_PEAKS, InvalidRequest, Session, Toolkit


TIMESERIES = r"samples/s21/peaks/timeseries"


def computed_ids(api) -> list[str]:
    return [call[3]["peak_id"] for call in api.requests_to(TIMESERIES, "POST")]


class TestComputeTimeseries:
    def test_a_stage_short_of_peaks_is_whole_once_they_are_computed(self, kit, api):
        dropped = api.drop_series("s21")
        before = kit.load_peaks_by_stage("s21", [(0, 5)], matches=False)
        assert before.complete is False and before.n_rows == 0
        assert before.failures[0].where["excluded_peaks"] == len(dropped)
        assert "compute_timeseries" in before.failures[0].error

        out = kit.compute_timeseries("s21")

        assert out == {
            "ref": kit.resolve("s21", "sample").to_dict(),
            "mz_range": None,
            "peaks": len(dropped),
            "had_timeseries": 0,
            "computed": len(dropped),
        }
        assert sorted(computed_ids(api)) == sorted(dropped)
        after = kit.load_peaks_by_stage("s21", [(0, 5)], matches=False)
        assert after.complete is True and after.n_rows == len(dropped)

    def test_only_the_peaks_without_a_series_are_computed(self, kit, api):
        dropped = api.drop_series("s21", below_mz=100)

        out = kit.compute_timeseries("s21")

        assert sorted(computed_ids(api)) == sorted(dropped)
        assert out["computed"] == len(dropped) == 4
        assert out["had_timeseries"] == out["peaks"] - 4

    def test_a_second_call_computes_nothing(self, kit, api):
        api.drop_series("s21", below_mz=100)
        kit.compute_timeseries("s21")

        again = kit.compute_timeseries("s21")

        assert again["computed"] == 0 and again["had_timeseries"] == again["peaks"]
        assert len(computed_ids(api)) == 4

    def test_an_mz_range_computes_the_peaks_within_it(self, kit, api):
        api.drop_series("s21")

        out = kit.compute_timeseries("s21", (100, 125))

        peak_reads = [c[2] for c in api.requests_to(r"samples/s21/peaks")]
        assert {"mz_min": 100.0, "mz_max": 125.0}.items() <= peak_reads[0].items()
        # m/z 101.06, 103.00 and 124.92 of the recorded twelve
        assert out["peaks"] == out["computed"] == len(computed_ids(api)) == 3
        assert out["mz_range"] == [100.0, 125.0]

    def test_over_the_cap_nothing_is_computed(self, kit, api):
        dropped = api.drop_series("s21")

        with pytest.raises(InvalidRequest) as caught:
            kit.compute_timeseries("s21", max_peaks=len(dropped) - 1)

        assert f"{len(dropped)} peak(s)" in caught.value.message
        assert "Narrow mz_range" in caught.value.message
        assert computed_ids(api) == []

    def test_the_cap_counts_only_the_peaks_without_a_series(self, kit, api):
        api.drop_series("s21", below_mz=100)

        assert kit.compute_timeseries("s21", max_peaks=4)["computed"] == 4

    def test_the_default_cap_is_the_toolkits_ceiling(self, kit, api):
        assert kit.max_peaks == DEFAULT_MAX_PEAKS == 500
        api.peak_copies = 42  # 504 peaks
        api.drop_series("s21")

        with pytest.raises(InvalidRequest, match="504 peak"):
            kit.compute_timeseries("s21")
        assert computed_ids(api) == []

    def test_a_call_cannot_raise_the_ceiling(self, kit, api, tmp_path):
        capped = Toolkit(kit.client, session=Session("c", root=tmp_path), max_peaks=5)
        api.drop_series("s21", below_mz=100)

        with pytest.raises(InvalidRequest, match="at most 5 on this toolkit"):
            capped.compute_timeseries("s21", max_peaks=6)
        assert capped.compute_timeseries("s21", max_peaks=5)["computed"] == 4
        api.drop_series("s21")
        with pytest.raises(InvalidRequest, match="more than max_peaks \\(5\\)"):
            capped.compute_timeseries("s21")
        assert len(computed_ids(api)) == 4

    @pytest.mark.parametrize("cap", [0, -1, True, 2.5, "10"])
    def test_a_bad_cap_is_refused(self, kit, api, tmp_path, cap):
        with pytest.raises(InvalidRequest, match="max_peaks must be"):
            kit.compute_timeseries("s21", max_peaks=cap)
        with pytest.raises(InvalidRequest, match="max_peaks must be"):
            Toolkit(kit.client, session=Session("c", root=tmp_path), max_peaks=cap)

    @pytest.mark.parametrize(
        "mz_range", [(125, 100), (100, 100), (100,), "100-125", (100, None)]
    )
    def test_a_bad_mz_range_is_refused(self, kit, api, mz_range):
        with pytest.raises(InvalidRequest, match="mz_range is"):
            kit.compute_timeseries("s21", mz_range)
        assert api.requests_to(r"samples/s21/peaks") == []

    def test_the_call_is_in_the_manifest(self, kit, api):
        api.drop_series("s21")
        kit.compute_timeseries("s21", (100, 125))
        with pytest.raises(InvalidRequest):
            kit.compute_timeseries("s21", max_peaks=1)

        ok, refused = [
            record
            for record in kit.session.manifest.records()
            if record.get("op") == "compute_timeseries"
        ]
        assert ok["outcome"] == "ok"
        assert ok["params"]["mz_range"] == [100, 125]
        assert [ref["id"] for ref in ok["refs"]] == ["s21"]
        assert refused["outcome"]["error"] == "invalid_request"
