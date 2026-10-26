"""
The golden questions (``ai_golden.py``): the acceptance test of the tool
surface.

Hermetic: the harness on the fake server of ``conftest.py``, the note's list
of the questions, and - when the demo bundle is on disk - the known answers
held to the bundle's goldens.

Against a demo stack, with the contract tests' switch and settings
(``MASCOPE_SDK_CONTRACT=1``, ``MASCOPE_SDK_TEST_URL``,
``MASCOPE_SDK_TEST_TOKEN``): every question answered through the tool
surface. One the surface cannot answer is an expected failure that says
what it lacks, and the session ends with the run's report::

    docker compose -f docker-compose.demo.yaml up -d
    MASCOPE_SDK_CONTRACT=1 uv run pytest libraries/ai/tests/test_ai_golden.py -v
"""

import importlib.util
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import ai_golden
import pandas as pd
import pytest
from ai_golden import (
    QUESTIONS,
    SURFACE,
    Close,
    Goldens,
    Instant,
    Question,
    Surface,
    Unanswerable,
    compare,
)

from mascope_ai import Found, NotFound, Ref, Session, Toolkit


ROOT = Path(__file__).resolve().parents[3]
NOTE = ROOT / "docs" / "dev" / "ai_layer.md"
#: The lines the note's generated list of the questions sits between.
LIST_START = "<!-- golden questions: start -->"
LIST_END = "<!-- golden questions: end -->"

BASE_URL = os.environ.get("MASCOPE_SDK_TEST_URL", "http://127.0.0.1:8080")
TOKEN = os.environ.get("MASCOPE_SDK_TEST_TOKEN", "mascope_demo_sdk_token")

needs_stack = pytest.mark.skipif(
    os.environ.get("MASCOPE_SDK_CONTRACT") != "1",
    reason=(
        "the golden questions need a running demo stack; start one "
        "(docker compose -f docker-compose.demo.yaml up -d) and set "
        "MASCOPE_SDK_CONTRACT=1"
    ),
)


def _registry():
    """The demo bundle registry, loaded by path: the CLI's package itself
    pulls in far more than its standard-library registry needs."""
    name = "_ai_golden_demo_bundles"
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "tooling/cli/src/mascope_cli/cmd/demo/bundles.py"
    )
    module = importlib.util.module_from_spec(spec)
    # dataclasses finds a class's module by name while building it
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        del sys.modules[name]
    return module


class TestTheQuestionSet:
    def test_ids_are_unique_and_kinds_known(self):
        ids = [question.id for question in QUESTIONS]

        assert len(ids) == len(set(ids))
        assert {question.category for question in QUESTIONS} == set(
            ai_golden.CATEGORIES
        )
        for question in QUESTIONS:
            assert question.id[0] == question.category[0].upper(), question.id

    def test_solvers_call_only_the_tool_surface(self):
        for question in QUESTIONS:
            assert set(question.tools) <= set(SURFACE), question.id
            assert list(question.tools) == sorted(question.tools), question.id

    def test_the_note_lists_them(self):
        text = NOTE.read_text(encoding="utf-8")
        assert LIST_START in text and LIST_END in text, (
            f"{NOTE.name} has no list of the golden questions between "
            f"{LIST_START!r} and {LIST_END!r}"
        )
        listed = text.split(LIST_START, 1)[1].split(LIST_END, 1)[0].strip()

        assert listed == ai_golden.markdown(), (
            "The note's list of the golden questions is not the set's: replace "
            "it with the output of "
            "'uv run python libraries/ai/tests/ai_golden.py list'"
        )

    def test_the_answers_are_of_the_bundle_ci_loads(self):
        default = _registry().DEFAULT_BUNDLE_VERSION

        assert ai_golden.BUNDLE == default, (
            f"The demo stack now loads bundle {default}, the golden answers are "
            f"of {ai_golden.BUNDLE}: run the questions against the new bundle "
            "(and the goldens test with it on disk), correct the answers that "
            "changed, then set BUNDLE in ai_golden.py"
        )


@pytest.fixture(scope="module")
def goldens() -> Goldens:
    bundle = ai_golden.bundle_dir()
    if bundle is None:
        pytest.skip(
            f"demo bundle {ai_golden.BUNDLE} is not on disk; fetch it "
            f"(uv run mascope demo fetch --bundle {ai_golden.BUNDLE}) or name it "
            "in MASCOPE_DEMO_BUNDLE_DIR"
        )
    return Goldens.load(bundle)


class TestTheKnownAnswersAreTheGoldens:
    @pytest.mark.parametrize(
        "question",
        [question for question in QUESTIONS if question.golden is not None],
        ids=lambda question: question.id,
    )
    def test_the_answer_agrees_with_the_goldens(self, goldens, question):
        from_goldens = question.golden(goldens)
        held_to = (
            question.expected if question.near_golden is None else question.near_golden
        )

        assert from_goldens, question.id
        assert compare(held_to, from_goldens, partial=True) == []


def _bundle(root: Path, peaks: list[dict], ions: list[dict]) -> Path:
    """A demo bundle of goldens alone, in *root*."""
    expected = root / "expected"
    expected.mkdir(parents=True)
    pd.DataFrame(peaks).to_parquet(expected / "peaks.parquet")
    pd.DataFrame(ions).to_parquet(expected / "ions.parquet")
    return root


#: Raw files' names as the demo's are made: a local acquisition time, then
#: the polarity; an MS2 acquisition's has neither.
NEG = "inst_2025.08.11-14h34m57s_neg_x.raw"
POS_LATE = "inst_2025.08.11-14h34m03s_pos_x.raw"
POS_EARLY = "inst_2025.08.11-14h23m12s_pos_x.raw"
MS2 = "inst_MS2_manual_x.raw"


class TestTheGoldensFiles:
    def test_rows_are_read_by_sample_and_polarity(self, tmp_path):
        def peak(filename, isotope, height):
            return {
                "filename": filename,
                "target_isotope_id": "i",
                "target_isotope_formula": isotope,
                "mz": 169.12,
                "height": height,
                "match_score": 0.99,
            }

        bundle = _bundle(
            tmp_path,
            [
                peak(POS_LATE, "C10H17O2+", 2.0),
                peak(POS_EARLY, "C10H17O2+", 1.0),
                peak(NEG, "C10H17O2+", 9.0),
                peak(MS2, "C10H17O2+", 7.0),
            ],
            [
                {
                    "filename": POS_LATE,
                    "target_ion_id": "n",
                    "target_ion_formula": "C10H17O2+",
                    "match_score": 0.99,
                    "match_category": 2,
                },
                {
                    "filename": MS2,
                    "target_ion_id": "n",
                    "target_ion_formula": "C10H17O2+",
                    "match_score": 0.5,
                    "match_category": 1,
                },
            ],
        )

        goldens = Goldens.load(bundle)

        heights = goldens.heights("C10H17O2+", "pos")
        assert list(heights.index) == ["2025-08-11 14:23:12", "2025-08-11 14:34:03"]
        assert list(heights) == [1.0, 2.0]
        assert goldens.peak("C10H17O2+", "2025-08-11 14:34:57")["height"] == 9.0
        assert goldens.ion("C10H17O2+", "2025-08-11 14:34:03")["match_category"] == 2
        assert MS2 not in set(goldens.peaks["filename"]) | set(goldens.ions["filename"])

    def test_where_the_bundle_is_looked_for(self, tmp_path, monkeypatch):
        named = _bundle(tmp_path / "named", [], [])
        home = tmp_path / "home"
        cached = _bundle(home / ".runtime" / "demo" / ai_golden.BUNDLE, [], [])
        checkout = tmp_path / "checkout"
        in_checkout = _bundle(checkout / ".runtime" / "demo" / ai_golden.BUNDLE, [], [])
        monkeypatch.setattr(
            ai_golden, "__file__", str(checkout / "libraries/ai/tests/ai_golden.py")
        )
        monkeypatch.setenv("MASCOPE_DEMO_BUNDLE_DIR", str(named))
        monkeypatch.setenv("MASCOPE_PATH", str(home))

        assert ai_golden.bundle_dir() == named
        monkeypatch.setenv("MASCOPE_DEMO_BUNDLE_DIR", str(tmp_path / "elsewhere"))
        assert ai_golden.bundle_dir() == cached
        monkeypatch.delenv("MASCOPE_PATH")
        assert ai_golden.bundle_dir() == in_checkout
        monkeypatch.setattr(ai_golden, "__file__", str(tmp_path / "a/b/c/d.py"))
        assert ai_golden.bundle_dir() is None


class TestCompare:
    def test_a_number_within_its_tolerance(self):
        assert Close(100.0, rel=0.01).matches(100.9)
        assert not Close(100.0, rel=0.01).matches(101.1)
        assert Close(0.5, abs=1e-6).matches(0.5000005)
        assert not Close(1.0, rel=0.5).matches(True)
        assert not Close(1.0, rel=0.5).matches("1.0")

    def test_an_instant_however_written(self):
        when = Instant("2025-08-11T11:23:12.911572+00:00")

        assert when.matches("2025-08-11 11:23:12.911572Z")
        assert when.matches("2025-08-11T14:23:12.911572+03:00")
        assert not when.matches("2025-08-11T11:23:12+00:00")
        assert not when.matches(None)

    def test_where_an_answer_differs(self):
        expected = {"sample": "s", "height": Close(10.0, rel=0.01), "list": [1, 2]}

        assert compare(expected, {"sample": "s", "height": 10.05, "list": [1, 2]}) == []
        assert compare(expected, {"sample": "t", "height": 11.0, "list": [1]}) == [
            "answer['sample']: 't', not 's'",
            "answer['height']: 11.0, not 10 (within 1%)",
            "answer['list']: [1], not 2 item(s) like [1, 2]",
        ]

    def test_keys_missing_or_extra(self):
        assert compare({"a": 1, "b": 2}, {"a": 1, "c": 3}) == [
            "answer: has 'c', which the known answer has not",
            "answer: lacks 'b'",
        ]

    def test_part_of_an_answer(self):
        expected = {"a": 1, "items": [{"x": 1, "y": Close(2.0, abs=0.1)}]}

        assert compare(expected, {"items": [{"y": 2.05}]}, partial=True) == []
        assert compare(expected, {"items": [{"y": 3.0}]}, partial=True) == [
            "answer['items'][0]['y']: 3.0, not 2 (within 0.1)"
        ]


def _samples_in_spring(kit):
    return kit.describe(kit.resolve("Spring 2026", "batch"))["n_samples"]


def _cannot(kit):
    kit.find("Spring", kind="batch")
    raise Unanswerable("the surface has no way to tell")


def _reaches_for_the_client(kit):
    return kit.client.workspaces.list()


def _names_a_missing_batch(kit):
    return kit.describe(kit.resolve("Autumn 2031", "batch"))


def _counts_found_peaks(kit):
    return len(ai_golden._found(kit.load_peaks(batches="Spring 2026")))


def _asked(solve, expected, check=None) -> Question:
    return Question(
        "M0", "metadata", "A question", solve, expected, "", (), check=check
    )


class TestTheHarness:
    def test_a_known_answer(self, kit):
        outcome = ai_golden.run_question(_asked(_samples_in_spring, 3), kit)

        assert outcome.status == "answered", outcome.describe()
        assert outcome.answer == 3
        assert outcome.problems == []
        assert outcome.tools == ("describe", "resolve")

    def test_another_answer_is_wrong_and_says_where(self, kit):
        outcome = ai_golden.run_question(_asked(_samples_in_spring, 4), kit)

        assert outcome.status == "wrong"
        assert outcome.problems == ["answer: 3, not 4"]
        assert "answer: 3" in outcome.describe()

    def test_a_question_the_surface_cannot_answer(self, kit):
        outcome = ai_golden.run_question(_asked(_cannot, 1), kit)

        assert outcome.status == "unanswerable"
        assert outcome.reason == "the surface has no way to tell"
        assert outcome.answer is None
        assert outcome.tools == ("find",)

    def test_a_solver_cannot_reach_past_the_surface(self, kit):
        outcome = ai_golden.run_question(_asked(_reaches_for_the_client, []), kit)

        assert outcome.status == "error"
        assert "'client' is not on the tool surface" in outcome.reason

    def test_the_surface_is_only_read(self, kit):
        surface = Surface(kit)

        with pytest.raises(AttributeError, match="read-only"):
            surface.find = None
        with pytest.raises(AttributeError, match="not on the tool surface"):
            surface.session  # noqa: B018
        assert surface.find("Spring", kind="batch")

    def test_a_failing_call_is_an_error(self, kit):
        outcome = ai_golden.run_question(_asked(_names_a_missing_batch, {}), kit)

        assert outcome.status == "error"
        assert outcome.reason.startswith("NotFound:")

    def test_an_answer_on_an_incomplete_frame_is_an_error(self, kit, api):
        api.fail("GET", "samples/s22/peaks", 403)

        outcome = ai_golden.run_question(_asked(_counts_found_peaks, 0), kit)

        assert outcome.status == "error"
        assert outcome.reason.startswith("Incomplete: not the whole answer")

    def test_a_check_beyond_the_known_answer(self, kit):
        def check(answer, record):
            return [] if answer == 3 else ["not three"]

        assert ai_golden.run_question(
            _asked(_samples_in_spring, 3, check), kit
        ).status == ("answered")
        outcome = ai_golden.run_question(
            _asked(_samples_in_spring, 3, lambda answer, record: ["no"]), kit
        )
        assert outcome.status == "wrong"
        assert outcome.problems == ["no"]

    def test_each_outcome_closes_an_answer_in_the_manifest(self, kit):
        report = ai_golden.run(kit, [_asked(_samples_in_spring, 3), _asked(_cannot, 1)])

        # Each outcome's tools are its own question's calls, not the session's
        assert [outcome.tools for outcome in report.outcomes] == [
            ("describe", "resolve"),
            ("find",),
        ]
        answers = kit.session.manifest.records("answer")
        calls = {
            call["seq"]: call["op"] for call in kit.session.manifest.records("call")
        }
        assert [answer["text"] for answer in answers] == [
            "[M0] 3",
            "[M0] The tool surface gave no answer (unanswerable): "
            "the surface has no way to tell",
        ]
        assert [[calls[seq] for seq in answer["calls"]] for answer in answers] == [
            ["resolve", "describe"],
            ["find"],
        ]
        assert "def _samples_in_spring" in answers[0]["code"]

    def test_the_report_names_the_gaps_and_the_failures(self, kit):
        report = ai_golden.run(
            kit,
            [
                _asked(_samples_in_spring, 3),
                _asked(_samples_in_spring, 4),
                _asked(_cannot, 1),
            ],
        )

        assert [outcome.status for outcome in report.outcomes] == [
            "answered",
            "wrong",
            "unanswerable",
        ]
        assert not report.passed
        table = report.table()
        assert "3 question(s): 1 answered, 1 wrong, 1 unanswerable, 0 error" in table
        assert (
            "The tool surface cannot answer:\n  M0: the surface has no way to tell"
            in (table)
        )
        assert "Answered wrongly or failed:\n  M0 wrong: A question" in table
        assert report.to_dict()["outcomes"][1]["problems"] == ["answer: 3, not 4"]

    def test_gaps_alone_pass(self, kit):
        report = ai_golden.run(kit, [_asked(_samples_in_spring, 3), _asked(_cannot, 1)])

        assert report.passed


class _Batch:
    """A tool surface over one batch's samples, for :func:`ai_golden._sample`."""

    def __init__(self, *samples: Ref):
        self.samples = samples

    def resolve(self, value, kind, *, within=None):
        if kind == "batch":
            return Ref("b1", "batch", value)
        raise NotFound(value, kind)

    def find(self, query, kind=None, limit=50, *, within=None):
        found = [ref for ref in self.samples if query in ref.name]
        return Found(found, total=len(found))


def _sampled(name: str, sample_id: str, modified: str | None) -> Ref:
    return Ref(
        sample_id,
        "sample",
        name,
        "b1",
        datetime.fromisoformat(modified) if modified else None,
    )


class TestASampleByName:
    def test_the_sample_of_that_name(self):
        surface = _Batch(
            _sampled("14:34:57 repeat", "a", "2026-10-26T12:00:00+00:00"),
            _sampled("14:34:57", "b", "2026-10-26T18:00:00+00:00"),
        )

        assert ai_golden._sample(surface, "batch", "14:34:57").id == "b"

    def test_of_two_copies_the_one_recorded_first(self):
        surface = _Batch(
            _sampled("14:34:57", "copy", "2026-10-26T18:00:00+00:00"),
            _sampled("14:34:57", "published", "2026-10-26T12:00:00+00:00"),
            _sampled("14:34:57", "undated", None),
        )

        assert ai_golden._sample(surface, "batch", "14:34:57").id == "published"

    def test_none_of_that_name(self):
        surface = _Batch(_sampled("14:34:57 2", "a", None))

        with pytest.raises(NotFound):
            ai_golden._sample(surface, "batch", "14:34:57")


#: What a solver for "what is the peak at m/z X" answers, and the manifest's
#: record of it.
PEAK_ANSWER = {
    "peak_mz": 124.9244,
    "assigned": [{"formula": "CH2O2", "status": "probable"}],
    "candidates": ["CH2O2"],
    "scored": {"CH2O2": "probable", "C2H4O2": "probable"},
    "unscored": ["[13C]H2O2"],
}
PEAK_RECORD = {"values": {"unscored": [{"formula": "[13C]H2O2", "origin": "model"}]}}


class TestNothingUnscoredIsAVerdict:
    check = staticmethod(ai_golden._nothing_unscored_is_a_verdict)

    def test_unscored_kept_apart(self):
        assert self.check(PEAK_ANSWER, PEAK_RECORD) == []

    def test_an_unscored_candidate_among_the_scored(self):
        answer = {
            **PEAK_ANSWER,
            "scored": {**PEAK_ANSWER["scored"], "[13C]H2O2": "probable"},
        }

        assert self.check(answer, PEAK_RECORD) == [
            "'[13C]H2O2' is unscored, yet presented as a verdict"
        ]

    def test_an_unscored_candidate_assigned_the_peak(self):
        answer = {
            **PEAK_ANSWER,
            "assigned": [{"formula": "[13C]H2O2", "status": "probable"}],
        }

        assert self.check(answer, PEAK_RECORD) == [
            "'[13C]H2O2' is unscored, yet presented as a verdict",
            "'[13C]H2O2' is assigned, yet Mascope gave it no score",
        ]

    def test_an_assignment_mascope_did_not_score(self):
        answer = {
            **PEAK_ANSWER,
            "assigned": [{"formula": "C3H6O3", "status": "probable"}],
        }

        assert self.check(answer, PEAK_RECORD) == [
            "'C3H6O3' is assigned, yet Mascope gave it no score"
        ]

    def test_the_manifest_records_what_was_unscored(self):
        record = {"values": {"unscored": []}}

        assert self.check(PEAK_ANSWER, record) == [
            "the manifest records [] as unscored, the answer ['[13C]H2O2']"
        ]


class TestTheCommandLine:
    def test_run_reports_and_fails_on_an_error(self, kit, monkeypatch, capsys):
        monkeypatch.setattr(ai_golden, "_toolkit", lambda *args: kit)

        # The fake workspace has no dataset named 2025
        assert ai_golden.main(["run", "--only", "M2, M7"]) == 1

        out = capsys.readouterr().out
        assert "2 question(s): 0 answered, 0 wrong, 0 unanswerable, 2 error" in out
        assert "M2 error: Which batches are in the 2025 dataset?" in out
        assert f"its manifest included: {kit.session.path}" in out

    def test_run_passes_with_the_known_answers(self, kit, monkeypatch, capsys):
        monkeypatch.setattr(ai_golden, "_toolkit", lambda *args: kit)
        monkeypatch.setattr(
            ai_golden, "QUESTIONS", (_asked(_samples_in_spring, 3), _asked(_cannot, 1))
        )

        assert ai_golden.main(["run", "--json"]) == 0

        report = json.loads(capsys.readouterr().out)
        assert report["bundle"] == ai_golden.BUNDLE
        assert [o["status"] for o in report["outcomes"]] == ["answered", "unanswerable"]

    def test_list_prints_the_notes_table(self, capsys):
        assert ai_golden.main(["list"]) == 0

        assert capsys.readouterr().out.strip() == ai_golden.markdown()

    def test_prompts_are_the_questions(self, capsys):
        assert ai_golden.main(["prompts"]) == 0

        lines = capsys.readouterr().out.splitlines()
        assert lines == [f"{q.id}. {q.text}" for q in QUESTIONS]


@pytest.fixture(scope="module")
def stack_kit(tmp_path_factory) -> Toolkit:
    from mascope_sdk import MascopeClient

    client = MascopeClient(
        url=BASE_URL,
        access_token=TOKEN,
        workspace=ai_golden.WORKSPACE,
        service_name="mascope_sdk",
    )
    return Toolkit(client, session=Session(root=tmp_path_factory.mktemp("golden")))


@needs_stack
@pytest.mark.parametrize("question", QUESTIONS, ids=lambda question: question.id)
def test_the_tool_surface_answers(stack_kit, question):
    outcome = ai_golden.run_question(question, stack_kit)
    ai_golden.OUTCOMES.append(outcome)

    if outcome.status == "unanswerable":
        pytest.xfail(f"the tool surface cannot answer this: {outcome.reason}")
    assert outcome.status == "answered", outcome.describe()
    assert outcome.tools == question.tools, (
        "the solver's calls are not those the note lists for it"
    )
