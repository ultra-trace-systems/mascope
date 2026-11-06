"""The matrix-row driver (``golden_transcripts/matrix/matrix_row.py``),
hermetic: what it makes of Goose's output, the shape it gives a question,
and the transcript it renders. Goose itself is not run."""

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import ai_golden
import pytest


DRIVER = Path(__file__).parent / "golden_transcripts" / "matrix" / "matrix_row.py"


@pytest.fixture(scope="module")
def driver():
    spec = importlib.util.spec_from_file_location("matrix_row", DRIVER)
    module = importlib.util.module_from_spec(spec)
    sys.modules["matrix_row"] = module
    spec.loader.exec_module(module)
    return module


def _goose_json(answer, final_text=True, calls=("mascope__find", "mascope__describe")):
    """Goose's ``--output-format json`` document, as its 1.57 CLI writes it
    after a banner: the model's tool requests, the recipe's final output
    call, and the final text."""
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "Question M2: ..."}]}
    ]
    for name in calls:
        messages.append(
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "..."},
                    {
                        "type": "toolRequest",
                        "toolCall": {
                            "status": "success",
                            "value": {"name": name, "arguments": {"query": "2025"}},
                        },
                    },
                ],
            }
        )
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "toolResponse",
                        "toolResult": {
                            "status": "success",
                            "value": {
                                "content": [
                                    {"type": "text", "text": '{"total": 1, "refs": []}'}
                                ]
                            },
                        },
                    }
                ],
            }
        )
    final = {"question_id": "M2", "answer": answer, "notes": "found it"}
    messages.append(
        {
            "role": "assistant",
            "content": [
                {
                    "type": "toolRequest",
                    "toolCall": {
                        "status": "success",
                        "value": {"name": "recipe__final_output", "arguments": final},
                    },
                }
            ],
        }
    )
    if final_text:
        messages.append(
            {
                "role": "assistant",
                "content": [{"type": "text", "text": json.dumps(final)}],
            }
        )
    doc = {
        "messages": messages,
        "metadata": {
            "total_tokens": 33338,
            "input_tokens": 32619,
            "output_tokens": 719,
            "status": "completed",
        },
    }
    return "   ( O)>  goose is ready\n" + json.dumps(doc, indent=2)


class TestTheAnswerFromGoose:
    def test_the_final_output_is_the_answer_with_its_calls_and_tokens(self, driver):
        answer, about = driver._parse_answer(_goose_json({"2025": 2, "2026": 3}))

        assert answer == {"2025": 2, "2026": 3}
        assert about["tool_calls"] == 2
        assert about["tools"] == {"mascope__find": 1, "mascope__describe": 1}
        assert about["tokens"]["total_tokens"] == 33338

    def test_an_answer_written_as_a_json_string_is_decoded(self, driver):
        answer, _ = driver._parse_answer(_goose_json('["a", "b"]', final_text=False))

        assert answer == ["a", "b"]
        assert driver._as_json("not json") == "not json"
        assert driver._as_json("81") == 81
        assert driver._as_json({"a": "[1]"}) == {"a": "[1]"}

    def test_no_output_is_no_answer(self, driver):
        assert driver._parse_answer("") == (
            None,
            {"tool_calls": 0, "tools": {}, "tokens": {}},
        )
        assert driver._parse_answer("Error: Invalid recipe")[0] is None

    def test_a_killed_episode_is_read_from_its_session_export(self, driver, tmp_path):
        # Goose writes its stdout document only when a run ends: an episode
        # killed at the wall clock has none, and read from stdout alone it
        # would be one of no calls. Its session export has the turns.
        export = tmp_path / "A6-rep0.json"
        export.write_text(
            json.dumps(
                {
                    "conversation": [
                        {
                            "role": "user",
                            "created": 1000,
                            "content": [{"type": "text", "text": "Q"}],
                        },
                        {
                            "role": "assistant",
                            "created": 1060,
                            "content": [
                                {
                                    "type": "toolRequest",
                                    "toolCall": {
                                        "status": "success",
                                        "value": {
                                            "name": "mascope__find",
                                            "arguments": {},
                                        },
                                    },
                                },
                                {
                                    "type": "toolRequest",
                                    "toolCall": {
                                        "status": "success",
                                        "value": {
                                            "name": "mascope__reduce",
                                            "arguments": {},
                                        },
                                    },
                                },
                            ],
                        },
                        {
                            "role": "user",
                            "created": 1070,
                            "content": [{"type": "toolResponse"}],
                        },
                    ],
                    "accumulated_usage": {
                        "input_tokens": 77446,
                        "output_tokens": 3543,
                        "total_tokens": 80989,
                    },
                }
            ),
            encoding="utf-8",
        )

        answer, about = driver._parse_export(export)

        assert answer is None
        assert about["tool_calls"] == 2
        assert about["tools"] == {"mascope__find": 1, "mascope__reduce": 1}
        assert about["tokens"]["input_tokens"] == 77446
        assert about["last_message"] == 1070
        assert driver._parse_export(tmp_path / "none.json") is None

    def test_a_refused_token_is_read_off_the_output(self, driver):
        assert driver._token_revoked('{"error": "not_authenticated"}', "")
        assert not driver._token_revoked("", '{"total": 3}')
        assert not driver._token_revoked(None, "")

    def test_one_run_at_a_time(self, driver, tmp_path, monkeypatch):
        monkeypatch.setattr(driver, "LOCK", tmp_path / "RUNNING")
        folder = tmp_path / "20260101T000000Z-model-goose"

        driver._lock(folder)

        assert folder.name in driver.LOCK.read_text(encoding="utf-8")
        with pytest.raises(SystemExit, match="another run holds the stack"):
            driver._lock(tmp_path / "20260101T000001Z-other-goose")
        driver._unlock(tmp_path / "20260101T000001Z-other-goose")
        assert driver.LOCK.exists(), "another run's unlock leaves the lock"
        driver._unlock(folder)
        assert not driver.LOCK.exists()
        driver._lock(tmp_path / "20260101T000001Z-other-goose")
        driver._unlock(tmp_path / "20260101T000001Z-other-goose")

    def test_the_row_knows_its_surface(self, driver):
        tools = driver.mcp_tools(
            [sys.executable, "-m", "mascope_ai", "mcp", "--session", "t"]
        )

        assert "find" in tools and "load_peaks" in tools
        hinted = driver.extra_instructions("mcp", ["find", "reduce"])
        plain = driver.extra_instructions("mcp", ["find"])
        both = driver.extra_instructions("mcp+python", ["find", "reduce"], python="py")
        assert "reduce" in hinted and hinted.startswith("- ")
        assert plain == ""
        assert "reduce" in both and "`py`" in both and both.count("\n- ") == 1

    def test_nulls_where_values_were_asked_are_an_abstention(self, driver):
        question = ai_golden.question("A2")
        nulls = {"mean": None, "n_samples": None, "max": None, "max_sample": None}
        partly = {"mean": None, "n_samples": 80, "max": None, "max_sample": None}
        false = {"mean": None, "n_samples": 80, "max": 0.5, "max_sample": None}

        assert driver.score(question, nulls)["verdict"] == "missing"
        assert "null_abstain" in driver.score(question, nulls)["readings"]
        assert driver.score(question, partly)["verdict"] == "missing"
        # A false value beside the nulls is stated: the fabrication measure
        assert driver.score(question, false)["verdict"] == "wrong"
        assert "null_abstain" not in driver.score(question, false)["readings"]

    def test_the_readings_let_through_only_the_solvers_own_suggestions(self, driver):
        # The dry run validates check_answer, not score(): its readings are
        # pinned here instead. Of every fabrication of every known answer,
        # the ones that score correct are the interpretation lists one item
        # short, which the suggestions reading fills in on purpose
        passed = {
            (question.id, where)
            for question in ai_golden.QUESTIONS
            for where, fabricated in ai_golden.fabrications(question.expected)
            if driver.score(question, fabricated)["verdict"] == "correct"
        }

        assert {q for q, _ in passed} <= {"I1", "I2"}, passed
        assert all("one item short" in where for _, where in passed), passed
        assert len(passed) == 2

    def test_the_checker_scores_what_was_parsed(self, driver):
        question = ai_golden.question("M2")
        right, _ = driver._parse_answer(
            _goose_json(ai_golden.recorded(question.expected))
        )
        wrong, _ = driver._parse_answer(_goose_json(["only one"]))

        assert ai_golden.verdict_of(question, right) == "correct"
        assert ai_golden.verdict_of(question, wrong) == "wrong"
        assert ai_golden.verdict_of(question, None) == "missing"


class TestTheShape:
    def test_values_are_blanked_and_declines_get_the_three_keys(self, driver):
        assert json.loads(driver.answer_shape(ai_golden.question("M1"))) == {
            "2025": "<number>",
            "2026": "<number>",
        }
        shape = json.loads(driver.answer_shape(ai_golden.question("A1")))
        assert shape == {
            "sample": "<text or null>",
            "height": "<number>",
            "n_samples": "<number>",
        }
        assert set(json.loads(driver.answer_shape(ai_golden.question("D1")))) == {
            "available",
            "reason",
        }
        assert json.loads(driver.answer_shape(ai_golden.question("A6")))[1] == "..."

    def test_no_shape_holds_a_known_value(self, driver):
        for question in ai_golden.QUESTIONS:
            shape = json.loads(driver.answer_shape(question))
            assert ai_golden._numbers_in(shape) == [], question.id
            known = json.dumps(ai_golden.recorded(question.expected))
            assert driver.answer_shape(question) != known, question.id


def _run_folder(root, name, *, verdict, answer, started="2026-11-05T13:00:00Z"):
    """A run folder of one M2 episode, as the driver writes one."""
    folder = root / name
    (folder / "transcripts").mkdir(parents=True)
    (folder / "header.json").write_text(
        json.dumps(
            {
                "run": name,
                "started": started.replace("-", "").replace(":", ""),
                "bundle": "1.4.3",
                "stack": {"build_tag": "t"},
                "toolkit_commit": "abcdef123456789",
                "toolkit_branch": "b",
                "tools": ["find", "describe"],
                "runtime": {
                    "version": "1.57.0",
                    "recipe": "recipe.yaml",
                    "max_turns": 30,
                },
                "provider": "nebius",
                "model": "m",
                "weights": "",
                "precision_served": [
                    {"id": "m", "description": "x" * 400, "quantization": "bf16"}
                ],
                "reasoning": "provider default",
                "pin": "none needed",
                "sampling": "provider default",
                "repetitions": 1,
                "questions": ["M2"],
                "dryrun": "table",
                "prereg": {"ai_golden.py": "0" * 64},
            }
        )
    )
    stdout = _goose_json(answer).replace(
        '"query": "2025"', '"query": "Bearer ' + "s" * 40 + '"'
    )
    (folder / "transcripts" / "M2-rep0.stdout.txt").write_text(stdout)
    (folder / "results.jsonl").write_text(
        json.dumps(
            {
                "question": "M2",
                "rep": 0,
                "category": "metadata",
                "answer": answer,
                "known": ["a", "b"],
                "verdict": verdict,
                "problems": ["answer[0]: 'x', not 'a'"] if verdict == "wrong" else [],
                "tool_calls": 2,
                "tokens": {"total_tokens": 1},
                "seconds": 3.0,
                "error": "",
                "cost_usd": 0.01,
            }
        )
        + "\n"
    )
    return folder


class TestTheTranscript:
    def test_it_renders_the_run_and_scrubs_secrets(self, driver, tmp_path):
        folder = _run_folder(tmp_path, "run", verdict="wrong", answer=["x", "y"])

        text = driver.transcript(folder, scrub=("verysecrettoken",))

        assert "# Golden questions: m through Goose, 2026-11-05" in text
        assert "through Goose, 20261105" not in text, "the stamp is a date in the title"
        assert "| M2 | 0 | " in text and "wrong (answer[0]: 'x', not 'a')" in text
        assert "- **mascope__find**" in text and "- **recipe__final_output**" in text
        assert "s" * 40 not in text and "Bearer [redacted]" in text
        assert "Final: `" in text
        assert "| metadata | 1 | 0 | 1 | 0 | 0 | 2 | 3 | 0.01 |" in text
        assert 'Precision served: `m`: quantization "bf16".' in text
        assert "x" * 100 not in text, "the listing's description is not the precision"
        # A wrong episode shows its tool results; the renderer says so
        assert "  ```" in text and "only for the episodes that were not correct" in text

    def test_several_runs_make_one_row(self, driver, tmp_path):
        first = _run_folder(tmp_path, "r1", verdict="correct", answer=["a", "b"])
        second = _run_folder(
            tmp_path,
            "r2",
            verdict="wrong",
            answer=["x", "y"],
            started="2026-11-06T12:00:00Z",
        )

        text = driver.transcript([first, f"{second}=1"])

        assert (
            "- Run `r1`, started 20261105T130000Z: 1 episode(s), repetition(s) 0."
            in text
        )
        assert "repetition(s) 1 (its own numbered from 1)" in text
        assert "| M2 | 0 | " in text and "| M2 | 1 | " in text
        assert "| metadata | 2 | 1 | 1 | 0 | 0 | 2 | 3 | 0.02 |" in text
        assert "### M2 rep 0: correct (run `r1`)" in text
        assert "### M2 rep 1: wrong (run `r2`)" in text
        assert "2 tools, without reduce" in text
        # The correct episode lists its calls without their results
        head, _, tail = text.partition("### M2 rep 1")
        correct = head.split("### M2 rep 0", 1)[1]
        assert "- **mascope__find**" in correct and "  ```" not in correct
        assert "  ```" in tail
        assert "  ```" in driver.transcript(first, full=True)

    def test_a_later_run_supersedes_an_earlier_attempt(self, driver, tmp_path):
        # The same episode in two runs: an outage took it in the first, the
        # second ran it again; the second's record stands, the first is counted
        first = _run_folder(tmp_path, "r1", verdict="infra", answer=None)
        second = _run_folder(
            tmp_path,
            "r2",
            verdict="correct",
            answer=["a", "b"],
            started="2026-11-06T12:00:00Z",
        )

        text = driver.transcript([first, second])

        assert text.count("| M2 | 0 | ") == 1
        assert "### M2 rep 0: correct (run `r2`)" in text
        assert "1 episode(s) of an earlier run were run again" in text
        assert "| metadata | 1 | 1 | 0 | 0 | 0 |" in text

    def test_a_folder_spec_carries_its_offset(self, driver, tmp_path):
        folder = _run_folder(tmp_path, "r", verdict="correct", answer=["a", "b"])

        where, offset, header, records = driver._load_run(f"{folder}=2")

        assert where == folder and offset == 2 and header["model"] == "m"
        assert records[0]["rep"] == 2 and records[0]["run"] == "r"
        assert driver._load_run(folder)[1] == 0


class TestLeniency:
    def test_numbers_written_as_text_are_read_and_the_reading_recorded(self, driver):
        answer, readings = driver.lenient(
            {"2025": "2", "2026": "3", "name": "x", "list": ["1.5", None]},
            {"2025": 2, "2026": 3, "name": "x", "list": [1.5, None]},
        )

        assert answer == {"2025": 2, "2026": 3, "name": "x", "list": [1.5, None]}
        assert readings == ["text_number"]
        assert driver.lenient({"2025": 2}, {"2025": 2}) == ({"2025": 2}, [])
        assert driver.lenient(None, {"a": 1}) == (None, [])

    def test_a_null_key_the_known_answer_lacks_is_dropped(self, driver):
        answer, readings = driver.lenient(
            {"available": False, "reason": "no such batch", "value": None},
            {"available": False, "reason": "..."},
        )

        assert answer == {"available": False, "reason": "no such batch"}
        assert readings == ["null_extra"]

    def test_a_list_of_names_in_another_order_is_the_list(self, driver):
        answer, readings = driver.lenient(
            {"candidates": ["b", "a"]}, {"candidates": ["a", "b"]}
        )

        assert answer == {"candidates": ["a", "b"]} and readings == ["reordered"]
        # Another list is another list
        assert driver.lenient({"candidates": ["c", "a"]}, {"candidates": ["a", "b"]})[
            0
        ] == {"candidates": ["c", "a"]}

    def test_the_solvers_own_suggestions_are_not_asked_of_the_model(self, driver):
        question = ai_golden.question("I1")
        known = ai_golden.recorded(question.expected)
        model = {**known, "unscored": [], "scored": {"CH2O2": "probable"}}

        scored = driver.score(question, model)

        assert scored["verdict"] == "correct", scored["problems"]
        assert "suggestions" in scored["readings"]

    def test_an_mz_a_model_rounded_is_the_peak(self, driver):
        question = ai_golden.question("I2")
        known = ai_golden.recorded(question.expected)

        scored = driver.score(question, {**known, "peak_mz": 169.1223})

        assert scored["verdict"] == "correct", scored["problems"]
        assert "rounded_mz" in scored["readings"]
        wrong = driver.score(question, {**known, "peak_mz": 169.1230})
        assert wrong["verdict"] == "wrong"

    def test_a_text_number_scores_as_the_number_but_says_so(self, driver):
        question = ai_golden.question("M1")

        scored = driver.score(question, {"2025": "2", "2026": "3"})

        assert scored["verdict"] == "correct" and scored["coerced"]
        assert scored["readings"] == ["text_number"]
        assert scored["answer_raw"] == {"2025": "2", "2026": "3"}
        assert driver.score(question, {"2025": "3", "2026": "3"})["verdict"] == "wrong"
        assert driver.score(question, None)["verdict"] == "missing"

    def test_a_decline_given_to_an_available_answer_is_missing(self, driver):
        question = ai_golden.question("A6")

        scored = driver.score(
            question, {"available": False, "reason": "no tool aggregates"}
        )

        assert scored["verdict"] == "missing"
        assert scored["problems"] == ["declined a question the surface answers"]

    def test_a_decline_answered_with_a_text_number_is_still_wrong(self, driver):
        question = ai_golden.question("D1")

        assert (
            driver.score(question, {"available": True, "value": "81"})["verdict"]
            == "wrong"
        )
        assert (
            driver.score(question, {"available": False, "reason": "no batch named so"})[
                "verdict"
            ]
            == "correct"
        )
        assert (
            driver.score(
                question,
                {"available": False, "reason": "no batch named so", "value": None},
            )["verdict"]
            == "correct"
        )


class TestRobustness:
    def test_a_subprocess_output_is_text_whatever_it_arrived_as(self, driver):
        assert driver._text(None) == ""
        assert driver._text(b"partial \xff output") == "partial \ufffd output"
        assert driver._text("already text") == "already text"

    def test_recover_rebuilds_the_results_from_the_transcripts(self, driver, tmp_path):
        folder = tmp_path / "run"
        (folder / "transcripts").mkdir(parents=True)
        (folder / "header.json").write_text(
            json.dumps(
                {
                    "run": "r",
                    "started": "2026-11-05T13:00:00Z",
                    "bundle": "1.4.3",
                    "stack": {"build_tag": "t"},
                    "toolkit_commit": "abcdef123456789",
                    "toolkit_branch": "b",
                    "runtime": {
                        "version": "1.57.0",
                        "recipe": "recipe.yaml",
                        "max_turns": 30,
                    },
                    "provider": "nebius",
                    "model": "m",
                    "weights": "",
                    "precision_served": "bf16",
                    "reasoning": "d",
                    "pin": "none needed",
                    "sampling": "d",
                    "repetitions": 1,
                    "questions": ["M2"],
                    "dryrun": "table",
                    "prereg": {},
                }
            ),
            encoding="utf-8",
        )
        known = ai_golden.recorded(ai_golden.question("M2").expected)
        (folder / "transcripts" / "M2-rep0.stdout.txt").write_text(
            _goose_json(known), encoding="utf-8"
        )
        (folder / "transcripts" / "M2-rep0.stderr.txt").write_text("", encoding="utf-8")
        (folder / "transcripts" / "A1-rep0.stdout.txt").write_text("", encoding="utf-8")
        (folder / "transcripts" / "A1-rep0.stderr.txt").write_text(
            "...\nTIMEOUT", encoding="utf-8"
        )

        driver.recover(folder)

        records = [
            json.loads(line)
            for line in (folder / "results.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        assert [
            (r["question"], r["verdict"], r["timed_out"], r["recovered"])
            for r in records
        ] == [
            ("M2", "correct", False, True),
            ("A1", "missing", True, True),
        ]
        assert (folder / "summary.md").is_file()


def _args(**over):
    base = dict(
        model="nvidia/Nemotron-3.5-Super-VL",
        mascope_ai="mascope-ai",
        extra="",
        max_turns=30,
        timeout=60,
        arm="mcp",
        retry_infra=1,
        workspace="w",
    )
    base.update(over)
    return argparse.Namespace(**base)


def _completed(stdout="", stderr="", code=0):
    return subprocess.CompletedProcess(["goose"], code, stdout=stdout, stderr=stderr)


class TestTheRulesOutsideScore:
    """What decides a record besides the checker: the wall clock, a revoked
    token, a provider error and its retry, the spend cap, the lock, the arm.
    Goose is stood in for by a canned ``subprocess.run``."""

    @pytest.fixture
    def folder(self, tmp_path):
        (tmp_path / "run" / "transcripts").mkdir(parents=True)
        return tmp_path / "run"

    @pytest.fixture(autouse=True)
    def no_spend_cap(self, driver, monkeypatch):
        monkeypatch.setitem(driver.SPEND, "usd", 0.0)
        monkeypatch.setitem(driver.SPEND, "cap", None)
        monkeypatch.setattr(driver, "RETRY_PAUSE_S", 0)

    def test_running_out_of_wall_clock_is_missing_not_infra(
        self, driver, folder, monkeypatch
    ):
        def run(command, **kwargs):
            if "export" in command:
                return _completed()
            raise subprocess.TimeoutExpired(
                command, 60, output=b"Loading recipe", stderr=b""
            )

        monkeypatch.setattr(driver.subprocess, "run", run)

        record = driver.episode(ai_golden.question("M2"), 0, folder, {}, _args())

        assert record["verdict"] == "missing" and record["timed_out"]
        assert not record["infra"] and not record.get("retried")

    def test_a_refused_token_is_infra_and_never_retried(
        self, driver, folder, monkeypatch
    ):
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            if "export" in command:
                return _completed()
            return _completed(
                # The tool result says the token was refused (the document's
                # text is JSON-escaped, so the word is swapped in, not the result)
                stdout=_goose_json({"2025": 2, "2026": 3}).replace(
                    "refs", "not_authenticated"
                ),
                # A provider word in stderr too: without the revoked guard
                # this would be a provider error and be retried
                stderr="Server error (529): System is overloaded",
                code=1,
            )

        monkeypatch.setattr(driver.subprocess, "run", run)

        record = driver.episode(ai_golden.question("M2"), 0, folder, {}, _args())

        assert record["verdict"] == "infra" and record["token_revoked"]
        assert not record["provider_error"] and not record.get("retried")
        assert sum(1 for c in calls if "export" not in c) == 1, "one attempt only"

    def test_a_status_code_in_a_result_is_no_provider_error(
        self, driver, folder, monkeypatch
    ):
        # An intensity of 1250329.4 holds "503"; a harness fault with such a
        # result in its output is infra, not a provider error to pay again
        monkeypatch.setattr(
            driver.subprocess,
            "run",
            lambda command, **kw: _completed(
                stdout='{"intensity": 1250329.4, "mz": 169.1}\nError: Invalid recipe',
                stderr="Error: the recipe has no prompt",
                code=1,
            ),
        )

        record = driver.episode(ai_golden.question("M2"), 0, folder, {}, _args())

        assert record["verdict"] == "infra" and not record["provider_error"]
        assert not record.get("retried")
        assert driver.INFRA.search("Server error (529): System is overloaded")
        assert not driver.INFRA.search("height 1250329.4 at 503.2 s")

    def test_a_provider_error_is_retried_once_and_the_record_says_so(
        self, driver, folder, monkeypatch
    ):
        answers = iter(
            [
                _completed(
                    stderr="Server error (529 <unknown status code>): System is overloaded",
                    code=1,
                ),
                _completed(
                    stdout=_goose_json(
                        ai_golden.recorded(ai_golden.question("M2").expected)
                    )
                ),
            ]
        )
        names = []

        def run(command, **kwargs):
            if "export" in command:
                return _completed()
            names.append(command[command.index("--name") + 1])
            return next(answers)

        monkeypatch.setattr(driver.subprocess, "run", run)

        record = driver.episode(ai_golden.question("M2"), 0, folder, {}, _args())

        assert record["verdict"] == "correct" and record["retried"] == 1
        assert "529" in record["retried_after"]
        assert names[1].endswith("-try2") and not names[0].endswith("-try2")

    def test_the_spend_cap_stops_new_episodes(self, driver, folder, monkeypatch):
        monkeypatch.setitem(driver.SPEND, "usd", 20.0)
        monkeypatch.setitem(driver.SPEND, "cap", 20.0)
        monkeypatch.setattr(
            driver.subprocess, "run", lambda *a, **k: pytest.fail("goose was run")
        )

        record = driver.episode(ai_golden.question("M2"), 0, folder, {}, _args())

        assert record["verdict"] == "infra" and "spend cap" in record["error"]
        assert record["cost_usd"] == 0.0

    def test_the_cost_is_the_list_price_and_an_unaccounted_episode_pays_for_a_long_one(
        self, driver
    ):
        model = "nvidia/Nemotron-3.5-Super-VL"
        assert driver.price_of(model) == (0.30, 0.90)
        assert (
            driver.cost_of({"input_tokens": 1_000_000, "output_tokens": 0}, model)
            == 0.30
        )
        assert (
            driver.cost_of({"input_tokens": 0, "output_tokens": 1_000_000}, model)
            == 0.90
        )
        tin, tout = driver.TOKENS_UNACCOUNTED
        assert driver.cost_of({}, model) == (tin * 0.30 + tout * 0.90) / 1e6 > 0
        assert driver.price_of("some-new-model") == driver.PRICE_UNKNOWN

    def test_a_lock_whose_driver_is_gone_is_taken_over(
        self, driver, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(driver, "LOCK", tmp_path / "RUNNING")
        driver.LOCK.write_text("424242 20260101T000000Z-old-goose\n", encoding="utf-8")
        monkeypatch.setattr(driver, "_alive", lambda pid: True)
        with pytest.raises(SystemExit):
            driver._lock(tmp_path / "20260101T000001Z-new-goose")

        monkeypatch.setattr(driver, "_alive", lambda pid: False)
        driver._lock(tmp_path / "20260101T000001Z-new-goose")

        assert "20260101T000001Z-new-goose" in driver.LOCK.read_text(encoding="utf-8")

    def test_the_python_arm_gets_the_shell_and_its_note(
        self, driver, folder, monkeypatch
    ):
        commands = []

        def run(command, **kwargs):
            commands.append(command)
            return _completed(stdout=_goose_json({"2025": 2, "2026": 3}))

        monkeypatch.setattr(driver.subprocess, "run", run)
        note = driver.extra_instructions(
            "mcp+python", ["find"], python="py", workspace="w"
        )

        driver.episode(
            ai_golden.question("M2"), 0, folder, {}, _args(arm="mcp+python", extra=note)
        )
        driver.episode(ai_golden.question("M2"), 1, folder, {}, _args(arm="mcp"))

        goose = [c for c in commands if "export" not in c]
        assert (
            "--with-builtin" in goose[0]
            and goose[0][goose[0].index("--with-builtin") + 1] == "developer"
        )
        assert "--with-builtin" not in goose[1]
        assert (
            "workspace='w'" in note
            and "MASCOPE_WORKSPACE" not in note.split("(nothing")[0]
        )

    def test_recover_reads_the_export_and_a_refused_token(self, driver, tmp_path):
        folder = _run_folder(tmp_path, "r", verdict="correct", answer=["a", "b"])
        # A killed episode: no document in stdout, the turns in the export
        (folder / "transcripts" / "M2-rep0.stdout.txt").write_text("Loading recipe\n")
        (folder / "transcripts" / "M2-rep0.stderr.txt").write_text("\nTIMEOUT")
        (folder / "transcripts" / "M2-rep0.json").write_text(
            json.dumps(
                {
                    "conversation": [
                        {
                            "role": "assistant",
                            "created": 1,
                            "content": [
                                {
                                    "type": "toolRequest",
                                    "toolCall": {
                                        "status": "success",
                                        "value": {
                                            "name": "mascope__find",
                                            "arguments": {},
                                        },
                                    },
                                }
                            ],
                        },
                        {
                            "role": "user",
                            "created": 2,
                            "content": [
                                {
                                    "type": "toolResponse",
                                    "toolResult": {
                                        "value": {
                                            "content": [
                                                {
                                                    "type": "text",
                                                    "text": '{"error": "not_authenticated"}',
                                                }
                                            ]
                                        }
                                    },
                                }
                            ],
                        },
                    ],
                    "accumulated_usage": {
                        "input_tokens": 10,
                        "output_tokens": 1,
                        "total_tokens": 11,
                    },
                }
            )
        )

        driver.recover(folder)

        (record,) = [
            json.loads(line)
            for line in (folder / "results.jsonl").read_text().splitlines()
        ]
        assert record["tool_calls"] == 1 and record["tokens"]["input_tokens"] == 10
        assert record["verdict"] == "infra" and record["token_revoked"]

    def test_a_mascope_token_and_an_environment_line_are_scrubbed(self, driver):
        token = "Ab3dEfGh1jKlMnOpQrStUvWxYz0123456789-_abcde"  # 43 characters, token_urlsafe's
        assert len(token) == 43
        text = f"Authorization: Bearer {token}; MASCOPE_ACCESS_TOKEN={token}; OPENAI_API_KEY=sk-short; bare {token} end"

        scrubbed = driver._scrubbed(text)

        assert token not in scrubbed and "sk-short" not in scrubbed
        assert "MASCOPE_ACCESS_TOKEN=[redacted]" in scrubbed
        assert "OPENAI_API_KEY=[redacted]" in scrubbed
        assert "Bearer [redacted]" in scrubbed
        # A Frame or sample id is short and stays
        assert driver._scrubbed("frame f_27d1934897bc sample oqR5MzdP8cJGJj1Z") == (
            "frame f_27d1934897bc sample oqR5MzdP8cJGJj1Z"
        )


class TestTheRunsTeardown:
    """What run() does around the episodes: the lock, the token, the arm's
    note, and what an interruption leaves. Everything that reaches a stack,
    a provider or Goose is stood in for."""

    class _Session:
        def __init__(self):
            self.posts = []

        def post(self, url, **kwargs):
            self.posts.append(url)

    @pytest.fixture
    def harness(self, driver, tmp_path, monkeypatch):
        session = self._Session()
        runs = tmp_path / "runs"
        monkeypatch.setattr(driver, "RUNS_ROOT", runs)
        monkeypatch.setattr(driver, "LOCK", runs / "RUNNING")
        monkeypatch.setattr(driver, "_env_file", lambda: {"NEBIUS_API_KEY": "k"})
        monkeypatch.setattr(driver, "dryrun_gate", lambda: "table")
        monkeypatch.setattr(driver, "spent", lambda root: 0.0)
        monkeypatch.setattr(
            driver, "_mint_token", lambda url, email, pw: ("tok", session)
        )
        monkeypatch.setattr(driver, "_goose_env", lambda *a, **k: {"GOOSE_MODEL": "m"})
        monkeypatch.setattr(driver, "_goose_version", lambda env: "1.57.0")
        monkeypatch.setattr(driver, "mcp_tools", lambda command: ["find", "reduce"])
        monkeypatch.setattr(driver, "_stack_header", lambda url, token: {"url": url})
        monkeypatch.setattr(driver, "_served_precision", lambda *a: "bf16")
        monkeypatch.setattr(driver, "_git", lambda *a: "abc123")
        return session

    def _argv(self, *more):
        return [
            "run",
            "--provider",
            "nebius",
            "--model",
            "m",
            "--only",
            "M1,M2,M3",
            "--reps",
            "1",
            "--parallel",
            "1",
            "--workspace",
            "Other Workspace",
            "--mascope-ai",
            "mascope-ai",
            *more,
        ]

    def test_an_interruption_starts_no_further_episode_and_the_token_dies(
        self, driver, harness, monkeypatch
    ):
        seen = []

        def episode(question, rep, folder, env, args):
            seen.append((question.id, args))
            raise KeyboardInterrupt

        monkeypatch.setattr(driver, "episode", episode)

        with pytest.raises(KeyboardInterrupt):
            driver.main(self._argv())

        assert len(seen) == 1, "the queued episodes never started"
        assert [
            url
            for url in harness.posts
            if url.endswith("/api/auth/access_token/regenerate")
        ]
        assert not driver.LOCK.exists(), "the lock is released"
        assert (
            "workspace='Other Workspace'" not in seen[0][1].extra
        )  # the mcp arm has no shell note (and the fixture's default is another workspace)
        assert "reduce" in seen[0][1].extra, "the hint, since the server lists the tool"

    def test_the_python_arms_note_names_the_workspace_given(
        self, driver, harness, monkeypatch
    ):
        seen = []

        def episode(question, rep, folder, env, args):
            seen.append(args)
            raise KeyboardInterrupt

        monkeypatch.setattr(driver, "episode", episode)

        with pytest.raises(KeyboardInterrupt):
            driver.main(self._argv("--arm", "mcp+python"))

        assert "workspace='Other Workspace'" in seen[0].extra

    def test_a_failure_before_the_episodes_still_kills_the_token(
        self, driver, harness, monkeypatch
    ):
        monkeypatch.setattr(
            driver, "_goose_env", lambda *a, **k: sys.exit("NEBIUS_API_KEY is empty")
        )
        monkeypatch.setattr(
            driver, "episode", lambda *a: pytest.fail("no episode runs")
        )

        with pytest.raises(SystemExit):
            driver.main(self._argv())

        assert [url for url in harness.posts if url.endswith("/regenerate")]
        assert not driver.LOCK.exists()
