"""The bench's batch arm (``bench run --batch``, MAS-1140).

Over the run's fake instance, server and comparison
(``test_assignment_bench_run.py``): each set's control as a run without the
arm makes it, then a run of each sample for the pass to fold, one pass of
the batch stages on the set's batch, every sample again, the card's sets
naming both, the database put back whatever the ref, and the arm's facts in
``run.json``. No host, no server.
"""

import json

import pytest
import test_assignment_bench_reuse as reuse
import test_assignment_bench_run as base


bench = base.bench
brun = base.brun

DEVELOP = base.DEVELOP


@pytest.fixture
def rig(tmp_path):
    return base.Rig(tmp_path)


def _summary(rig):
    return json.loads((rig.card_dir() / "run.json").read_text())


def _mascope_runs(rig, sample):
    """A sample's in-app runs, oldest first, decoys left out."""
    return [r["peak_assignment_run_id"] for r in rig.server.controls(sample)][::-1]


# ------------------------------------------------------------------ the arm


def test_a_batch_run_passes_each_set_s_batch_and_runs_every_sample_again(rig):
    assert rig.run("topic", batch=True) == 0

    # Per sample: its control, a run for the pass to fold, then one after
    # the pass. Every pass folds every sample first, after both of its set's
    # samples ran twice and before any ran a third time.
    for sample in ("S1", "S2", "S3"):
        assert [c for s, _, c in rig.server.assigned if s == sample] == [None] * 3
        assert len(_mascope_runs(rig, sample)) == 3
    assert [(b, fold) for b, fold, _ in rig.server.passes] == [
        ("B1", "always"),
        ("B2", "always"),
    ]
    order = [s for s, _, _ in rig.server.assigned]
    b1 = rig.server.passes[0][2]
    assert sorted(order[:b1]) == ["S1", "S1", "S2", "S2"]
    assert sorted(order[b1 : b1 + 2]) == ["S1", "S2"]

    # The card reads the control by name, and the arm by name beside it.
    manifest = rig.compare.calls[-1]["manifest"]
    for name, samples in (("Db", ["S1", "S2"]), ("Et", ["S3"])):
        spec = manifest["sets"][name]
        assert spec["runs"] == {s: _mascope_runs(rig, s)[0] for s in samples}
        assert spec["batch_arm"] == {
            "runs": {s: _mascope_runs(rig, s)[2] for s in samples}
        }
        assert "batch_arm_off" not in spec
    # Every run of the arm is denied with the control's.
    deny = rig.compare.calls[-1]["deny"]
    for sample in ("S1", "S2", "S3"):
        assert set(_mascope_runs(rig, sample)) <= set(deny)

    summary = _summary(rig)
    assert summary["batch"] is True
    db = summary["sets"]["Db"]
    # The control and the arm both read every sample: nothing left out.
    assert db["read"] == 2 and db["left_out"] == {}
    arm = db["batch"]
    assert arm["on_card"] is True and arm["off"] is None and arm["why"] is None
    assert arm["runs"] == {"folded": {"completed": 2}, "after": {"completed": 2}}
    assert arm["minutes"] is not None
    [one] = arm["passes"]
    assert one["run_id"] == rig.server.batch_listing["B1"][0]["batch_peak_run_id"]
    assert one["status"] == "completed" and one["current"] is True
    assert one["state"] == "current" and one["stale_stages"] == []
    # Its time per stage that ran, and what its fold could not read.
    assert one["stages"] == {
        "fold": {"status": "done", "minutes": 1.5},
        "isotope_lines": {"status": "done", "minutes": 3.0},
    }
    assert (one["samples"], one["folded"], one["failed"], one["not_read"]) == (
        2,
        2,
        0,
        0,
    )
    assert rig.state()["cards"][-1]["batch"] is True


def test_a_batch_run_of_develop_dumps_and_restores_the_database(rig):
    assert rig.run("origin/develop", batch=True) == 0
    calls = rig.instance.calls
    # Develop's head migrates nothing, and still takes its restore point
    # before the backend starts, and puts it back once it stopped.
    assert "migrate" not in calls
    assert calls.index("snapshot") < calls.index(("start", True))
    assert calls[-3:] == ["stop", ("restore", "bench_0.dump"), "remove_worktree"]
    assert not rig.home.restore_point.exists()
    migration = _summary(rig)["migration"]
    assert migration["restore_point"] is True and migration["restored"] is True
    assert migration["dump_s"] >= 0 and migration["restore_s"] >= 0


def test_a_run_without_the_arm_runs_and_cards_as_it_always_did(rig):
    assert rig.run("origin/develop") == 0
    # No dump on develop's head, one run a sample, no pass, no named runs.
    assert "snapshot" not in rig.instance.calls
    assert len(rig.server.assigned) == 3 and rig.server.passes == []
    for spec in rig.compare.calls[-1]["manifest"]["sets"].values():
        assert not {"runs", "batch_arm", "batch_arm_off"} & set(spec)
    summary = _summary(rig)
    assert "batch" not in summary and "dump_s" not in summary["migration"]
    assert all("batch" not in s for s in summary["sets"].values())
    assert "batch" not in rig.state()["cards"][-1]


def test_a_branch_with_a_migration_and_the_arm_takes_one_restore_point(rig):
    assert rig.run("topic", batch=True) == 0
    calls = rig.instance.calls
    assert calls.count("snapshot") == 1
    assert calls.index("snapshot") == calls.index("migrate") - 1
    assert calls[-3:] == base.BRANCH_CLEANUP
    assert rig.instance.db == ["r2"]


# ----------------------------------------------------- an arm left off a set


def _off(rig, name="Db"):
    summary = _summary(rig)
    arm = summary["sets"][name]["batch"]
    spec = rig.compare.calls[-1]["manifest"]["sets"][name]
    return summary, arm, spec


@pytest.mark.parametrize(
    "end, code",
    [
        (("failed", "RuntimeError: boom"), "failed"),
        (("cancelled", None), "cancelled"),
    ],
)
def test_a_pass_that_fails_leaves_the_arm_off_and_the_control_stands(rig, end, code):
    rig.server.pass_end = end
    assert rig.run("topic", batch=True) == 1
    summary, arm, spec = _off(rig)
    assert arm["on_card"] is False and arm["off"] == code
    assert arm["why"] == brun.ARM_OFF[code]
    assert arm["passes"][0]["status"] == code
    # No sample runs after a pass that did not complete.
    assert all(len(_mascope_runs(rig, s)) == 2 for s in ("S1", "S2", "S3"))
    assert spec["batch_arm_off"] == brun.ARM_OFF[code]
    assert "batch_arm" not in spec
    # The control is read whole, by name, as the card it would be alone.
    assert spec["runs"] == {s: _mascope_runs(rig, s)[0] for s in ("S1", "S2")}
    assert summary["sets"]["Db"]["read"] == 2
    assert summary["sets"]["Db"]["left_out"] == {}
    assert f"set Db: no batch arm: {brun.ARM_OFF[code]}" in brun.run_problems(
        summary["sets"]
    )


def test_a_pass_that_ends_with_a_stage_stale_leaves_the_arm_off(rig):
    rig.server.status = {"state": "behind", "stale_stages": ["series"]}
    assert rig.run("topic", batch=True) == 1
    _, arm, spec = _off(rig)
    assert arm["off"] == "stale"
    assert arm["passes"][0]["current"] is False
    assert arm["passes"][0]["stale_stages"] == ["series"]
    assert spec["batch_arm_off"] == brun.ARM_OFF["stale"]


def test_a_refused_pass_leaves_the_arm_off(rig):
    rig.server.pass_refuse["B1"] = ("http_403", "peak assignment is off")
    assert rig.run("topic", batch=True) == 1
    _, arm, _ = _off(rig)
    assert arm["off"] == "refused" and arm["passes"][0]["run_id"] is None
    # The other set's batch is passed all the same.
    assert _summary(rig)["sets"]["Et"]["batch"]["on_card"] is True


def test_a_sample_whose_run_after_the_pass_fails_leaves_the_arm_off(rig):
    rig.server.nth_end[("S2", 3)] = ("failed", "boom")
    assert rig.run("topic", batch=True) == 1
    summary, arm, spec = _off(rig)
    assert arm["off"] == "runs_failed"
    assert arm["runs"]["after"] == {"completed": 1, "failed": 1}
    assert spec["batch_arm_off"] == brun.ARM_OFF["runs_failed"]
    # The control stands: S2's newest completed run is the pass's, the
    # arm's own, and its control is still listed.
    assert summary["sets"]["Db"]["read"] == 2


def test_a_sample_whose_run_for_the_pass_fails_runs_no_pass(rig):
    rig.server.nth_end[("S1", 2)] = ("failed", "boom")
    assert rig.run("topic", batch=True) == 1
    summary, arm, _ = _off(rig)
    assert arm["off"] == "runs_failed" and arm["passes"] == []
    assert arm["runs"]["folded"] == {"completed": 1, "failed": 1}
    assert [b for b, _, _ in rig.server.passes] == ["B2"]
    assert summary["sets"]["Db"]["read"] == 2


def test_a_sample_in_no_batch_leaves_the_arm_off(rig):
    del rig.instance.batches["S2"]
    assert rig.run("topic", batch=True) == 1
    _, arm, _ = _off(rig)
    assert arm["off"] == "no_batch" and arm["passes"] == []


def test_two_sets_in_one_batch_get_no_arm_and_run_no_pass(rig):
    rig.instance.batches["S3"] = "B1"
    assert rig.run("topic", batch=True) == 1
    summary = _summary(rig)
    for name in ("Db", "Et"):
        arm = summary["sets"][name]["batch"]
        assert arm["off"] == "shared" and arm["runs"] == {"folded": {}, "after": {}}
    # Neither set's samples run for a pass that will not come.
    assert rig.server.passes == []
    assert len(rig.server.assigned) == 3


def test_a_pass_over_the_set_s_budget_leaves_the_arm_off_and_the_control_on(rig):
    rig.server.pass_end = None  # it never ends
    assert rig.run("topic", batch=True, budget_s=100.0) == 1
    summary, arm, spec = _off(rig)
    assert arm["off"] == "over_budget"
    assert arm["passes"][0]["status"] == "over_budget"
    assert spec["batch_arm_off"] == brun.ARM_OFF["over_budget"]
    assert (
        summary["sets"]["Db"]["read"] == 2 and not summary["sets"]["Db"]["over_budget"]
    )


def test_a_set_over_its_budget_on_its_control_runs_no_arm(rig):
    rig.server.ends[("S1", None)] = None  # S1's runs never end
    assert rig.run("topic", batch=True, budget_s=30.0) == 1
    summary = _summary(rig)
    assert summary["sets"]["Db"]["over_budget"] is True
    assert summary["sets"]["Db"]["batch"] is None
    # The other set's arm runs as ever.
    assert [b for b, _, _ in rig.server.passes] == ["B2"]
    assert summary["sets"]["Et"]["batch"]["on_card"] is True


# ----------------------------------------------------------------- reuse


def test_a_card_with_the_arm_serves_a_request_without_it(rig):
    assert rig.run("topic", batch=True) == 0
    reuse._docs_head(rig)
    assert rig.run("topic") == 0
    record = rig.state()["cards"][-1]
    assert record["reused"] == "topic/20261112-120000" and record["batch"] is True
    assert len(rig.compare.calls) == 1


def test_a_request_with_the_arm_is_not_served_by_a_card_without_it(rig):
    assert rig.run("topic") == 0
    reuse._docs_head(rig)
    assert rig.run("topic", batch=True) == 0
    record = rig.state()["cards"][-1]
    assert record["reused"] is None and record["batch"] is True
    assert len(rig.compare.calls) == 2


def test_a_card_whose_arm_is_off_serves_no_request_with_it_but_one_without(rig):
    rig.server.pass_end = ("failed", "boom")
    assert rig.run("topic", batch=True) == 1
    rig.server.pass_end = ("completed", None)
    reuse._docs_head(rig)
    assert rig.run("topic", batch=True) == 0
    assert rig.state()["cards"][-1]["reused"] is None
    # The control of the card that missed its arm is whole.
    assert brun.passing_problems(_summary(rig)["sets"], batch=False) == []
    assert brun.passing_problems(
        {"Db": {"left_out": {}, "decoys": {}, "batch": {"off": "failed"}}}, batch=True
    ) == ["set Db: no batch arm (failed)"]


# ----------------------------------------------------------- the pieces


def test_a_pass_s_stage_minutes_and_fold_counts_are_read_off_its_summary():
    summary = {
        "samples": ["a", "b", "c"],
        "stages": [
            {
                "stage": "fold",
                "status": "done",
                "utc_started": "2026-11-23T10:00:00Z",
                "utc_ended": "2026-11-23T10:00:30Z",
                "outcomes": [{"data": {"samples_folded": 2, "samples_failed": 1}}],
            },
            {"stage": "series", "status": "failed", "utc_started": "x"},
            {"stage": "blends", "status": "not_run"},
        ],
    }
    assert brun.stage_minutes(summary) == {
        "fold": {"status": "done", "minutes": 0.5},
        "series": {"status": "failed", "minutes": None},
    }
    assert brun.fold_counts(summary) == {
        "samples": 3,
        "folded": 2,
        "failed": 1,
        "not_read": 1,
    }
    assert brun.fold_counts(None) == {
        "samples": 0,
        "folded": None,
        "failed": None,
        "not_read": None,
    }


@pytest.mark.parametrize(
    "status, body, launch",
    [
        (
            202,
            {"data": [{"batch_peak_run_id": "BP1"}]},
            brun.Launch(run_id="BP1"),
        ),
        (409, {"detail": {"code": "batch_run_in_flight"}}, None),
        (200, {"data": [], "message": "nothing to run"}, None),
        (403, {"error": "off"}, brun.Launch(code="http_403", reason="off")),
    ],
)
def test_a_pass_s_answer_is_a_launch(status, body, launch):
    got = brun.batch_launch_of(status, body)
    if status == 409:
        assert got.busy and got.code == "batch_run_in_flight"
    elif status == 200:
        assert got.code == "nothing_to_run" and got.run_id is None
    else:
        assert got == launch


def test_the_command_s_help_names_the_arm(capsys):
    with pytest.raises(SystemExit):
        bench.main(["run", "--help"])
    assert "--batch" in capsys.readouterr().out


# ------------------------------------------------------------- the comment


def test_the_comment_says_which_sets_the_arm_reads_and_why_one_has_none(rig):
    rig.server.pass_end = None

    def arm(card):
        # What the real comparison writes from the sets file's arm.
        card["batch_arm"] = {
            "columns": {"Et": {"readings": {}, "moves": {}}},
            "off": {"Db": brun.ARM_OFF["over_budget"]},
        }

    rig.compare.edit = arm
    gh = reuse.base_post.FakeGh(head=base.BRANCH)
    assert reuse._post(rig, gh, batch=True, budget_s=100.0) == 1
    [body] = [c["body"] for c in gh.comments]
    assert "; batch arm;" in body
    assert (
        "**After the batch pass: read on Et; Db has none: the set's budget ran "
        f"out.** {bench.bench_post.BATCH_ARM_TAIL}"
    ) in body
    assert "- set Db: no batch arm: the set's budget ran out" in body


def test_a_card_without_the_arm_has_no_arm_line():
    assert bench.bench_post.batch_arm_lines({"columns": {}}) == []


def test_a_control_no_longer_listed_is_not_read_though_the_arm_s_run_is_newest(rig):
    def prune(server, sample):
        # The store let S1's control go once its arm's runs were made.
        runs = [r for r in server.listing[sample] if r["engine"] == "mascope"]
        if sample == "S1" and len(runs) == 3 and runs[0]["status"] == "completed":
            server.listing[sample].remove(runs[-1])

    rig.server.after = prune
    assert rig.run("topic", batch=True) == 1
    summary = _summary(rig)
    assert summary["sets"]["Db"]["left_out"] == {"not_latest": 1}
    assert summary["sets"]["Db"]["read"] == 1
