"""The comparison tool's decoy report (``tooling/assignment_compare``, ``--decoy``).

A decoy arm is read against the run it names as its control, never against the
sample's latest run, and what it reports is the share of that control's
assigned rows and intensity the arm still holds at assigned, below and above
m/z 350; on the adduct arm, how many of those rows read a new ion rather than
another split of one the control reads. The server is replaced by a client that
serves canned runs and ledgers.
"""

import importlib.util
from pathlib import Path

import pandas as pd
import pytest

from mascope_sdk import NotFoundError


SCRIPT = Path(__file__).resolve().parents[2] / "assignment_compare" / "compare_runs.py"


def _load():
    spec = importlib.util.spec_from_file_location("compare_runs", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compare_runs = _load()


def _row(peak, mz, intensity, *, role="M0", tier="assigned", ion="C6H13O6+"):
    return {
        "sample_peak_id": peak,
        "sample_peak_mz": mz,
        "sample_peak_intensity": intensity,
        "role": role,
        "tier": tier,
        "ion_formula": ion if role == "M0" else None,
    }


#: The control: three assigned rows below m/z 350 and one above, and a
#: candidate that is no assigned row.
CONTROL = pd.DataFrame(
    [
        _row("p1", 181.07, 4000.0, ion="C6H13O6+"),
        _row("p2", 203.05, 3000.0, ion="C8H11O5+"),
        _row("p3", 250.10, 2000.0, ion="C10H19O6+"),
        _row("p4", 401.20, 1000.0, ion="C20H33O7+"),
        _row("p5", 120.00, 500.0, tier="candidate", ion="C8H9N+"),
    ]
)


def _run(run_id, *, decoy=None, status="completed", control=None, error=None):
    config = {"decoy_arm": {"control_run_id": control}} if control else {}
    return {
        "peak_assignment_run_id": run_id,
        "engine": "mascope",
        "status": status,
        "decoy": decoy,
        "config": config,
        "error": error,
    }


class _Client:
    """The two SDK reads the report makes, over canned runs and ledgers."""

    def __init__(self, runs: dict, ledgers: dict):
        self.runs, self.ledgers = runs, ledgers
        self.asked: list[tuple] = []
        self.peak_assignments = self

    def list_runs(self, sample_id, include_decoys=False):
        self.asked.append((sample_id, include_decoys))
        return pd.DataFrame(self.runs[sample_id])

    def get(self, sample_id, run_id=None):
        # A run the sample no longer has is a 404, as on the server
        if run_id not in {
            run["peak_assignment_run_id"] for run in self.runs[sample_id]
        }:
            raise NotFoundError(f"no run {run_id}", status_code=404)
        return self.ledgers[run_id]


def _report(runs, ledgers, samples=("s1",)):
    client = _Client(runs, ledgers)
    return client, compare_runs.decoy_report(client, list(samples))


def test_a_shifted_arm_is_read_against_the_run_it_names_as_its_control():
    # The arm holds one row below 350 of the control's three, and none above;
    # a newer run of the sample is not what the arm was scored against.
    decoy = pd.DataFrame(
        [
            _row("p1", 181.0716, 4000.0, ion="C9H9O4+"),
            _row("p3", 250.1023, 2000.0, tier="candidate"),
        ]
    )
    runs = {
        "s1": [
            _run("newer"),
            _run("arm", decoy="mz_shift_ppm=+9", control="control"),
            _run("control"),
        ]
    }
    ledgers = {"control": CONTROL, "arm": decoy, "newer": CONTROL.iloc[:0]}

    client, result = _report(runs, ledgers)

    assert client.asked == [("s1", True)]
    arm = result["per_sample"]["s1"]["mz_shift_ppm=+9"]
    assert arm["control_run"] == "control"
    assert (arm["control"]["rows"], arm["decoy"]["rows"]) == (4, 1)
    assert arm["held_pct"] == 25.0
    assert arm["held_lt_350_pct"] == pytest.approx(33.3)
    assert arm["held_ge_350_pct"] == 0.0
    assert arm["held_intensity_pct"] == 40.0
    assert arm["decoy"]["by_bin"] == {"150": 1}
    assert "new_ion" not in arm["decoy"]


def test_an_arm_counts_its_committed_rows_at_level_4_or_above():
    # Step 5.8: of the arm's three monoisotopic rows that state a level, one
    # states a formula; a row with none, and an isotopologue, count nowhere.
    decoy = pd.DataFrame(
        [
            {**_row("p1", 181.0716, 4000.0), "level": "4a"},
            {**_row("p2", 203.0518, 3000.0, tier="candidate"), "level": "5a"},
            {**_row("p3", 250.1023, 2000.0, tier="candidate"), "level": "5b"},
            {**_row("p4", 401.2036, 1000.0), "level": None},
            {**_row("p5", 182.0750, 300.0, role="iso_child"), "level": "4a"},
        ]
    )
    runs = {
        "s1": [_run("arm", decoy="mz_shift_ppm=+9", control="control"), _run("control")]
    }

    _, result = _report(runs, {"control": CONTROL, "arm": decoy})

    arm = result["per_sample"]["s1"]["mz_shift_ppm=+9"]
    assert (arm["decoy"]["levelled"], arm["decoy"]["at_4_or_above"]) == (3, 1)
    assert arm["at_4_or_above_pct"] == pytest.approx(33.3)
    assert result["pooled"]["mz_shift_ppm=+9"]["at_4_or_above_pct"] == pytest.approx(
        33.3
    )


def test_an_arm_from_before_the_level_reads_none_at_4():
    decoy = pd.DataFrame([_row("p1", 181.0716, 4000.0)])
    runs = {
        "s1": [_run("arm", decoy="mz_shift_ppm=+9", control="control"), _run("control")]
    }

    _, result = _report(runs, {"control": CONTROL, "arm": decoy})

    arm = result["per_sample"]["s1"]["mz_shift_ppm=+9"]
    assert arm["decoy"]["levelled"] == 0
    assert arm["at_4_or_above_pct"] is None


def test_the_adduct_arm_counts_a_new_ion_apart_from_a_resplit_one():
    # p1: the same ion the control reads, as another neutral - a re-split.
    # p2: another ion on a peak the control read. p6: a peak it left alone,
    # read as an ion the control reads on another peak, which makes it no less
    # new on this one.
    decoy = pd.DataFrame(
        [
            _row("p1", 181.07, 4000.0, ion="C6H13O6+"),
            _row("p2", 203.05, 3000.0, ion="C7H11NaO4+"),
            _row("p6", 410.00, 800.0, ion="C8H11O5+"),
        ]
    )
    runs = {
        "s1": [_run("arm", decoy="swap_adducts", control="control"), _run("control")]
    }

    _, result = _report(runs, {"control": CONTROL, "arm": decoy})

    arm = result["per_sample"]["s1"]["swap_adducts"]
    assert (arm["decoy"]["same_ion"], arm["decoy"]["new_ion"]) == (1, 2)
    assert arm["decoy"]["new_ion_lt_350"] == 1
    assert arm["new_ion_pct"] == 50.0
    assert arm["held_pct"] == 75.0


def test_a_refused_arm_is_named_with_its_reason_and_counts_nowhere():
    runs = {
        "s1": [
            _run(
                "refused",
                decoy="mz_shift_ppm=+9",
                status="failed",
                error="no decoy: a shift of +9 ppm is inside the 15 ppm window",
            ),
            _run("pending", decoy="mz_shift_da=+0.35", status="running"),
            _run("control"),
        ]
    }

    _, result = _report(runs, {"control": CONTROL})

    assert result["per_sample"] == {}
    assert result["skipped"]["s1"] == {
        "mz_shift_ppm=+9": "no decoy: a shift of +9 ppm is inside the 15 ppm window",
        "mz_shift_da=+0.35": "run running",
    }
    assert "inside the 15 ppm window" in compare_runs.decoy_markdown(result)


def test_an_arm_whose_control_is_gone_is_named_and_the_set_goes_on():
    # Retention pruned the first sample's control: its ledger read would be a
    # 404, and the second sample is still read.
    runs = {
        "s1": [_run("arm-s1", decoy="mz_shift_ppm=+9", control="pruned")],
        "s2": [
            _run("arm-s2", decoy="mz_shift_ppm=+9", control="control"),
            _run("control"),
        ],
    }
    ledgers = {"control": CONTROL, "arm-s1": CONTROL, "arm-s2": CONTROL.iloc[:1]}

    _, result = _report(runs, ledgers, samples=("s1", "s2"))

    assert result["skipped"] == {"s1": {"mz_shift_ppm=+9": "its control run is gone"}}
    assert list(result["per_sample"]) == ["s2"]
    assert result["pooled"]["mz_shift_ppm=+9"]["samples"] == 1


def test_the_set_pools_its_samples_arm_by_arm():
    one = pd.DataFrame([_row("p1", 181.07, 4000.0)])
    runs = {
        sample: [
            _run(f"arm-{sample}", decoy="mz_shift_ppm=-9", control="control"),
            _run("control"),
        ]
        for sample in ("s1", "s2")
    }
    ledgers = {"control": CONTROL, "arm-s1": one, "arm-s2": one.iloc[:0]}

    _, result = _report(runs, ledgers, samples=("s1", "s2"))

    pooled = result["pooled"]["mz_shift_ppm=-9"]
    assert pooled["samples"] == 2
    assert (pooled["control"]["rows"], pooled["decoy"]["rows"]) == (8, 1)
    assert pooled["held_pct"] == 12.5
    assert pooled["control"]["by_bin"]["150"] == 2
    markdown = compare_runs.decoy_markdown(result)
    assert "| mz_shift_ppm=-9 | 2 | 8 | 1 | 12.5 |" in markdown


def test_a_row_on_a_peak_the_control_read_as_the_source_s_is_counted_apart():
    # A dalton shift moves the reagent's lines out of the pre-pass's reach,
    # and the brightest of them can then be assigned as an analyte.
    control = pd.concat(
        [CONTROL, pd.DataFrame([_row("r1", 241.10, 1.0e6, role="reagent")])]
    )
    decoy = pd.DataFrame([_row("r1", 241.45, 1.0e6, ion="C11H19N2O2S+")])
    runs = {
        "s1": [
            _run("arm", decoy="mz_shift_da=+0.35", control="control"),
            _run("control"),
        ]
    }

    _, result = _report(runs, {"control": control, "arm": decoy})

    arm = result["per_sample"]["s1"]["mz_shift_da=+0.35"]
    assert arm["decoy"]["on_control_reagent"] == 1
    assert arm["decoy"]["intensity_on_control_reagent"] == 1.0e6
    assert arm["held_intensity_pct"] == 10000.0


def test_the_newest_completed_run_of_an_arm_is_the_one_read():
    runs = pd.DataFrame(
        [
            _run("failed-newest", decoy="swap_adducts", status="failed"),
            _run("completed", decoy="swap_adducts", control="c"),
            _run("completed-older", decoy="swap_adducts", control="c"),
            _run("c"),
        ]
    )

    newest = compare_runs.newest_decoys(runs)

    assert list(newest) == ["swap_adducts"]
    assert newest["swap_adducts"]["peak_assignment_run_id"] == "completed"


def test_a_card_reads_only_the_arms_of_the_run_it_reads():
    # The newest +9 arm was refused before it ran, so the newest one the store
    # keeps is an older ref's, against the control that ref made. A card that
    # reads the newer control must not read that arm; the swap arm made
    # against the newer control is read.
    arm = pd.DataFrame([_row("p1", 181.0716, 4000.0, ion="C9H9O4+")])
    runs = {
        "s1": [
            _run("swap", decoy="swap_adducts", control="new"),
            _run("new"),
            _run("old-arm", decoy="mz_shift_ppm=+9", control="old"),
            _run("old"),
        ]
    }
    ledgers = {"new": CONTROL, "old": CONTROL, "old-arm": arm, "swap": arm}
    client = _Client(runs, ledgers)

    result = compare_runs.decoy_report(client, ["s1"], controls={"s1": "new"})

    assert list(result["per_sample"]["s1"]) == ["swap_adducts"]
    assert result["skipped"] == {
        "s1": {"mz_shift_ppm=+9": "its control is not the run the card reads"}
    }
    # A sample the card did not read has no arm it reads either.
    result = compare_runs.decoy_report(client, ["s1"], controls={})
    assert result["per_sample"] == {}
    # Outside a card every arm is read against its own control, as before.
    result = compare_runs.decoy_report(client, ["s1"])
    assert set(result["per_sample"]["s1"]) == {"swap_adducts", "mz_shift_ppm=+9"}


# --- The measurement model each control scored at -----------------------------------


def _at(version):
    """A run config's measurement model record at ``version``; None for a run
    from before the record, which kept only its scoring snapshot."""
    if version is None:
        return {"pattern_scoring": {"mz_tolerance_ppm": 3.0}}
    return {"measurement_model": {"version": version, "width_ppm": 1.2}}


def _modelled(run_id, version, **kwargs):
    run = _run(run_id, **kwargs)
    run["config"] = {**run["config"], **_at(version)}
    return run


def test_a_run_from_before_the_model_record_is_counted_apart_from_version_1():
    assert compare_runs.model_version(_at(1)) == "v1"
    assert compare_runs.model_version(_at(2)) == "v2"
    assert compare_runs.model_version(_at(None)) == "before"
    assert compare_runs.model_version({}) == "before"
    assert compare_runs.model_version(None) == "before"
    # A record that states no version is no version of it.
    assert compare_runs.model_version({"measurement_model": {}}) == "before"


def test_an_arm_is_split_by_the_model_version_of_each_sample_s_control():
    # s1's control took the refit, s2's kept v1, s3's predates the record.
    # Each decoy reads its control's model, so none differs.
    held_one = pd.DataFrame([_row("p1", 181.0716, 4000.0, ion="C9H9O4+")])
    held_two = pd.DataFrame(
        [
            _row("p1", 181.0716, 4000.0, ion="C9H9O4+"),
            _row("p2", 203.0518, 3000.0, ion="C9H9O4+"),
        ]
    )
    runs = {
        sample: [
            _modelled(
                f"arm-{sample}", v, decoy="mz_shift_ppm=+9", control=f"c-{sample}"
            ),
            _modelled(f"c-{sample}", v),
        ]
        for sample, v in (("s1", 2), ("s2", 1), ("s3", None))
    }
    ledgers = {
        "c-s1": CONTROL,
        "c-s2": CONTROL,
        "c-s3": CONTROL,
        "arm-s1": held_two,
        "arm-s2": held_one,
        "arm-s3": CONTROL.iloc[:0],
    }

    _, result = _report(runs, ledgers, samples=("s1", "s2", "s3"))

    per = result["per_sample"]
    assert [per[s]["mz_shift_ppm=+9"]["model"] for s in ("s1", "s2", "s3")] == [
        "v2",
        "v1",
        "before",
    ]
    assert per["s1"]["mz_shift_ppm=+9"]["decoy_model"] == "v2"
    arm = result["pooled"]["mz_shift_ppm=+9"]
    assert arm["samples"] == 3 and arm["model_differs"] == 0
    assert arm["held_pct"] == 25.0
    # Each version's part holds its own samples' rows: two of four at v2,
    # one of four at v1, none of four before the record.
    by_model = arm["by_model"]
    assert sorted(by_model) == ["before", "v1", "v2"]
    assert {v: part["samples"] for v, part in by_model.items()} == {
        "before": 1,
        "v1": 1,
        "v2": 1,
    }
    assert by_model["v2"]["held_pct"] == 50.0
    assert by_model["v1"]["held_pct"] == 25.0
    assert by_model["before"]["held_pct"] == 0.0
    assert by_model["v2"]["control"]["rows"] == 4
    assert by_model["v2"]["decoy"]["rows"] == 2


def test_a_decoy_at_another_model_version_than_its_control_is_counted():
    runs = {
        "s1": [
            _modelled("arm", 1, decoy="mz_shift_ppm=+9", control="control"),
            _modelled("control", 2),
        ]
    }
    ledgers = {"control": CONTROL, "arm": CONTROL.iloc[:1]}

    _, result = _report(runs, ledgers)

    one = result["per_sample"]["s1"]["mz_shift_ppm=+9"]
    assert (one["model"], one["decoy_model"]) == ("v2", "v1")
    arm = result["pooled"]["mz_shift_ppm=+9"]
    # The arm is split by its control's version, and the mismatch is counted.
    assert list(arm["by_model"]) == ["v2"]
    assert arm["model_differs"] == 1
