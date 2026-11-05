"""The matrix-row driver (``golden_transcripts/matrix/matrix_row.py``),
hermetic: what it makes of Goose's output, the shape it gives a question,
and the transcript it renders. Goose itself is not run."""

import importlib.util
import json
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


class TestTheTranscript:
    def test_it_renders_the_run_and_scrubs_secrets(self, driver, tmp_path):
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
                    "precision_served": [{"quantization": "bf16"}],
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
        stdout = _goose_json(["x", "y"]).replace(
            '"query": "2025"', '"query": "Bearer ' + "s" * 40 + '"'
        )
        (folder / "transcripts" / "M2-rep0.stdout.txt").write_text(stdout)
        (folder / "results.jsonl").write_text(
            json.dumps(
                {
                    "question": "M2",
                    "rep": 0,
                    "category": "metadata",
                    "answer": ["x", "y"],
                    "known": ["a", "b"],
                    "verdict": "wrong",
                    "problems": ["answer[0]: 'x', not 'a'"],
                    "tool_calls": 2,
                    "tokens": {"total_tokens": 1},
                    "seconds": 3.0,
                    "error": "",
                }
            )
            + "\n"
        )

        text = driver.transcript(folder, scrub=("verysecrettoken",))

        assert "# Golden questions: m through Goose, 2026-11-05" in text
        assert "| M2 | 0 | " in text and "wrong (answer[0]: 'x', not 'a')" in text
        assert "- **mascope__find**" in text and "- **recipe__final_output**" in text
        assert "s" * 40 not in text and "Bearer [redacted]" in text
        assert "Final: `" in text


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
