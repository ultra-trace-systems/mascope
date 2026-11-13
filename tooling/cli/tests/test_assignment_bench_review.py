"""The assignment bench's ``review`` (``tooling/assignment_bench/review.py``).

The sheet of the engines' top tier disagreements, and the recording of a
filled one. The database read is a fake that answers rows as the query's
columns name them; the instance and its API are fakes that log every call.
No server, no database.
"""

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2] / "assignment_bench"


def _load():
    spec = importlib.util.spec_from_file_location("assignment_bench", ROOT / "bench.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bench = _load()
review = bench.bench_review


def _row(sample, peak, formula, intensity, mechanism="[M+Br]-", **extra):
    return {
        "sample_item_id": sample,
        "sample_peak_id": peak,
        "mz": 124.924373111,
        "intensity": intensity,
        "profile": "BR",
        "peak_assignment_id": f"pa-{sample}-{peak}",
        "formula": formula,
        "mechanism": mechanism,
        "tier": "candidate",
        "reference_formula": formula,
        "reference_mechanism": mechanism,
        "reference_engine_tier": "assigned",
        "reference_tier": "candidate",
        **extra,
    }


def _state():
    return {
        "sets": {
            "Db": {"instrument_class": "orbitrap", "samples": {}},
            "Ft": {"instrument_class": "tof", "samples": {}},
        },
        "reference": {
            "runs": {
                "Db": {"s1": {"run_id": "r1"}, "s2": {"run_id": "r2"}, "s3": {}},
                "Ft": {"t1": {"run_id": "r9"}},
            }
        },
    }


class FakeInstance:
    def __init__(self):
        self.calls = []

    def check_env(self):
        self.calls.append("check_env")

    def bind(self):
        self.calls.append("bind")

    def start(self, peak_assignment=False):
        self.calls.append(("start", peak_assignment))
        return "http://bench"

    def stop(self):
        self.calls.append("stop")


class FakeApi:
    def __init__(self, refuse=(), answers=None):
        self.posts = []
        self.errors = []
        self.refuse = set(refuse)
        self.answers = answers or {}

    def post(self, path, payload):
        self.posts.append((path, payload))
        if path in self.refuse:
            self.errors.append(f"POST {path}: 404 not found")
            return None
        return self.answers.get(path.split("?")[0], {})


def _review(tmp_path, *, query=None, api=None, instance=None, state=None):
    written = {}

    def write_private(path, text):
        path.write_text(text, encoding="utf-8")
        written[path] = text

    said = []
    r = review.Review(
        reviews=tmp_path / "reviews",
        instance=instance or FakeInstance(),
        query=query or (lambda sql: []),
        connect=lambda base: api,
        load_state=lambda: state or _state(),
        write_private=write_private,
        say=said.append,
    )
    return r, said, written


# ----------------------------------------------------------------- the sheet


def test_top_rows_takes_each_reading_once_from_its_brightest_sample():
    rows = [
        _row("s1", "p1", "CH2O2", 100.0),
        _row("s2", "p1", "CH2O2", 300.0),
        _row("s1", "p2", "C3H6O3", 200.0),
        _row("s2", "p3", "C3H6O3", 50.0, mechanism="[M-H]-"),
        _row("s1", "p4", "HNO3", 10.0),
    ]
    top = review.top_rows(rows, 3)
    assert [(r["sample_item_id"], r["formula"], r["mechanism"]) for r in top] == [
        ("s2", "CH2O2", "[M+Br]-"),
        ("s1", "C3H6O3", "[M+Br]-"),
        ("s2", "C3H6O3", "[M-H]-"),
    ]
    assert top[0]["mz"] == 124.92437
    assert top[0]["intensity"] == 300


def test_the_query_quotes_only_ids():
    sql = review.disagreements_sql(["s1", "s2"], ["r1"])
    assert "in ('s1', 's2')" in sql and "in ('r1')" in sql
    # the reference's tier against this server's, and no judged reading again
    assert "ref.engine_tier <> ref.tier" in sql
    assert "and not exists (" in sql and "v.superseded_utc is null" in sql
    with pytest.raises(review.ReviewError):
        review.disagreements_sql(["s1'; drop table x; --"], ["r1"])


def test_the_sheet_reads_each_sets_reference_runs_and_writes_the_rows(tmp_path):
    queries = []

    def query(sql):
        queries.append(sql)
        if "'r9'" in sql:
            return [_row("t1", "p1", "C9H14", 5.0, mechanism="[M+H]+")]
        return [_row("s1", "p1", "CH2O2", 100.0), _row("s2", "p2", "CH2O2", 300.0)]

    r, said, written = _review(tmp_path, query=query)
    path = r.sheet()

    assert path.parent == tmp_path / "reviews"
    # the samples with a reference run, and those runs; s3 has none
    assert "in ('s1', 's2')" in queries[0] and "in ('r1', 'r2')" in queries[0]
    assert "in ('t1')" in queries[1]
    lines = written[path].splitlines()
    assert lines[0].split(",") == list(review.COLUMNS)
    assert len(lines) == 3
    assert lines[1].startswith("Db,s2,p2,")
    assert lines[2].startswith("Ft,t1,p1,")
    # the reviewer's columns are empty, and nothing shows a score
    filled = review.read_filled(written[path])
    assert filled.rows == [] and filled.problems == []
    assert "evidence," not in lines[0].replace("evidence_level", "")
    assert "p_correct" not in written[path]
    assert any("2 peak(s) of 1 reading(s)" in line for line in said)


def test_the_sheet_refuses_an_unknown_set_and_a_bench_with_no_reference(tmp_path):
    r, _, _ = _review(tmp_path)
    with pytest.raises(review.ReviewError, match="no set X"):
        r.sheet(["X"])
    state = _state()
    state["reference"] = None
    r, _, _ = _review(tmp_path, state=state)
    with pytest.raises(review.ReviewError, match="no reference runs"):
        r.sheet()


# --------------------------------------------------------------- the record


def _sheet(tmp_path, rows):
    rows = [{**{c: "" for c in review.COLUMNS}, **row} for row in rows]
    path = tmp_path / "filled.csv"
    path.write_text(review.write_sheet(rows), encoding="utf-8")
    return path


def test_a_filled_row_is_checked_before_anything_is_posted():
    text = review.write_sheet(
        [
            {
                "sample_item_id": "s1",
                "peak_assignment_id": "a1",
                "verdict": "Confirmed",
                "evidence_level": "msms",
                "claimed_inchikey": " BDAGIHXWWSANSR-UHFFFAOYSA-N ",
                "claimed_name": "formic acid",
            },
            {"sample_item_id": "s1", "peak_assignment_id": "a2", "verdict": ""},
            {
                "sample_item_id": "s1",
                "peak_assignment_id": "a3",
                "verdict": "confirmed",
            },
            {"sample_item_id": "s1", "peak_assignment_id": "a4", "verdict": "maybe"},
            {
                "sample_item_id": "s1",
                "peak_assignment_id": "a5",
                "verdict": "rejected",
                "evidence_level": "hunch",
            },
            {"sample_item_id": "s1", "peak_assignment_id": "a6", "verdict": "rejected"},
            # the verify route's claim rules
            {
                "sample_item_id": "s1",
                "peak_assignment_id": "a7",
                "verdict": "confirmed",
                "evidence_level": "orthogonal",
            },
            {
                "sample_item_id": "s1",
                "peak_assignment_id": "a8",
                "verdict": "rejected",
                "claimed_inchikey": "BDAGIHXWWSANSR-UHFFFAOYSA-N",
            },
            {
                "sample_item_id": "s1",
                "peak_assignment_id": "a9",
                "verdict": "confirmed",
                "evidence_level": "pattern",
                "claimed_name": "formic acid",
            },
        ]
    )
    filled = review.read_filled(text)
    assert [
        (
            r["peak_assignment_id"],
            r["verdict"],
            r["evidence_level"],
            r["claimed_inchikey"],
            r["claimed_name"],
        )
        for r in filled.rows
    ] == [
        ("a1", "confirmed", "msms", "BDAGIHXWWSANSR-UHFFFAOYSA-N", "formic acid"),
        ("a6", "rejected", None, None, None),
    ]
    assert filled.problems == [
        "line 4: a confirmation needs an evidence level",
        "line 5: verdict 'maybe' is none of confirmed, rejected, unsure",
        "line 6: evidence level 'hunch' is none of reference_standard, msms, "
        "orthogonal, pattern, visual",
        "line 8: a confirmation on orthogonal names its structure in claimed_inchikey",
        "line 9: only a confirmation names a structure",
        "line 10: claimed_name goes with claimed_inchikey",
    ]
    with pytest.raises(review.ReviewError, match="not a review sheet"):
        review.read_filled("a,b\n1,2\n")


def test_record_posts_each_verdict_and_refits_each_class_once(tmp_path):
    api = FakeApi(
        answers={
            "/api/peak-assignments/calibration/orbi/recalibrate": {
                "message": "Recalibrated",
                "reagent_profile": "BR",
                "before_ece": 0.2,
                "after_ece": 0.1,
            }
        }
    )
    instance = FakeInstance()
    prepared = []
    sheet = _sheet(
        tmp_path,
        [
            {
                "set": "Db",
                "sample_item_id": "s1",
                "peak_assignment_id": "a1",
                "profile": "BR",
                "verdict": "confirmed",
                "evidence_level": "msms",
                "claimed_inchikey": "BDAGIHXWWSANSR-UHFFFAOYSA-N",
                "note": " lobe ",
            },
            {
                "set": "Db",
                "sample_item_id": "s2",
                "peak_assignment_id": "a2",
                "profile": "BR",
                "verdict": "rejected",
            },
            {
                "set": "Ft",
                "sample_item_id": "t1",
                "peak_assignment_id": "a3",
                "profile": "UR",
                "verdict": "unsure",
            },
        ],
    )
    r, said, _ = _review(tmp_path, api=api, instance=instance)
    r.prepare = lambda: prepared.append(True)

    assert r.record(sheet, recalibrate=True) == 0

    assert instance.calls == ["check_env", "bind", ("start", True), "stop"]
    assert prepared == [True]
    assert api.posts[:3] == [
        (
            "/api/peak-assignments/sample/s1/verify",
            {
                "peak_assignment_id": "a1",
                "verdict": "confirmed",
                "evidence_level": "msms",
                "note": "lobe",
                "claimed_inchikey": "BDAGIHXWWSANSR-UHFFFAOYSA-N",
            },
        ),
        (
            "/api/peak-assignments/sample/s2/verify",
            {
                "peak_assignment_id": "a2",
                "verdict": "rejected",
                "evidence_level": None,
                "note": None,
            },
        ),
        (
            "/api/peak-assignments/sample/t1/verify",
            {
                "peak_assignment_id": "a3",
                "verdict": "unsure",
                "evidence_level": None,
                "note": None,
            },
        ),
    ]
    # an unsure verdict is no label, so its class is not refit
    assert api.posts[3:] == [("/api/peak-assignments/calibration/orbi/recalibrate", {})]
    assert "recorded 3 of 3 verdict(s)" in said
    assert any(
        "recalibration of orbi (profile BR)" in line and "ECE 0.2 -> 0.1" in line
        for line in said
    )


def test_two_profiles_in_one_class_are_refit_once_pooled(tmp_path):
    """The app keeps one curve per class: verdicts of two Orbitrap sets under
    two profiles make one refit of the class, with no profile asked for, which
    the output calls pooled. A second refit would replace the first."""
    state = _state()
    state["sets"]["Ur"] = {"instrument_class": "orbitrap", "samples": {}}
    api = FakeApi(
        answers={
            "/api/peak-assignments/calibration/orbi/recalibrate": {
                "message": "Recalibrated",
                "reagent_profile": None,
            },
        }
    )
    rows = [
        ("Db", "s1", "BR", "rejected"),
        ("Ur", "u1", "UR", "rejected"),
        ("Ur", "u2", "UR", "confirmed"),
        ("Ft", "t1", "UR", "rejected"),
    ]
    sheet = _sheet(
        tmp_path,
        [
            {
                "set": name,
                "sample_item_id": sample,
                "peak_assignment_id": f"a-{sample}",
                "profile": profile,
                "verdict": verdict,
                "evidence_level": "pattern",
            }
            for name, sample, profile, verdict in rows
        ],
    )
    r, said, _ = _review(tmp_path, api=api, state=state)

    assert r.record(sheet, recalibrate=True) == 0

    assert [path for path, _ in api.posts[4:]] == [
        "/api/peak-assignments/calibration/orbi/recalibrate",
        "/api/peak-assignments/calibration/tof/recalibrate",
    ]
    assert any(
        "recalibration of orbi (pooled across reagent profiles" in line for line in said
    )


def test_record_without_recalibrate_refits_nothing(tmp_path):
    api = FakeApi()
    sheet = _sheet(
        tmp_path,
        [
            {
                "set": "Db",
                "sample_item_id": "s1",
                "peak_assignment_id": "a1",
                "profile": "BR",
                "verdict": "rejected",
            }
        ],
    )
    r, _, _ = _review(tmp_path, api=api)
    assert r.record(sheet) == 0
    assert [path for path, _ in api.posts] == ["/api/peak-assignments/sample/s1/verify"]


def test_a_refused_verdict_fails_the_record_and_the_backend_still_stops(tmp_path):
    api = FakeApi(refuse={"/api/peak-assignments/sample/s1/verify"})
    instance = FakeInstance()
    sheet = _sheet(
        tmp_path,
        [
            {
                "set": "Db",
                "sample_item_id": "s1",
                "peak_assignment_id": "a1",
                "verdict": "rejected",
            },
            {
                "set": "Db",
                "sample_item_id": "s2",
                "peak_assignment_id": "a2",
                "verdict": "rejected",
            },
        ],
    )
    r, said, _ = _review(tmp_path, api=api, instance=instance)
    assert r.record(sheet) == 1
    assert "recorded 1 of 2 verdict(s)" in said
    assert any("404 not found" in line for line in said)
    assert instance.calls[-1] == "stop"

    class Broken(FakeApi):
        def post(self, path, payload):
            raise RuntimeError("connection reset")

    instance = FakeInstance()
    r, _, _ = _review(tmp_path, api=Broken(), instance=instance)
    with pytest.raises(RuntimeError):
        r.record(sheet)
    assert instance.calls[-1] == "stop"


def test_a_sheet_with_a_problem_posts_nothing_and_starts_nothing(tmp_path):
    instance = FakeInstance()
    sheet = _sheet(
        tmp_path,
        [
            {
                "set": "Db",
                "sample_item_id": "s1",
                "peak_assignment_id": "a1",
                "verdict": "rejected",
            },
            {
                "set": "Db",
                "sample_item_id": "s2",
                "peak_assignment_id": "a2",
                "verdict": "confirmed",
            },
        ],
    )
    r, said, _ = _review(tmp_path, api=FakeApi(), instance=instance)
    assert r.record(sheet) == 1
    assert instance.calls == []
    assert "nothing posted: fix the sheet and record it again" in said


@pytest.mark.parametrize("top", ["0", "-1", "two"])
def test_top_is_one_or_more(tmp_path, capsys, top):
    with pytest.raises(SystemExit) as raised:
        bench.main(["--home", str(tmp_path), "review", "--top", top])
    assert raised.value.code == 2
    assert "--top" in capsys.readouterr().err


@pytest.mark.parametrize("top", [0, -1])
def test_a_sheet_of_no_rows_is_refused(tmp_path, top):
    r, _, _ = _review(tmp_path)
    with pytest.raises(review.ReviewError, match="top must be 1 or more"):
        r.sheet(top=top)


def test_recalibrate_needs_record(tmp_path, capsys):
    code = bench.main(["--home", str(tmp_path), "review", "--recalibrate"])
    assert code == 2
    assert "--recalibrate goes with --record" in capsys.readouterr().err
