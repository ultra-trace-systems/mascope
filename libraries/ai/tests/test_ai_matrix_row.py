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
            "value",
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
        answer, coerced = driver.lenient(
            {"2025": "2", "2026": "3", "name": "x", "list": ["1.5", None]}
        )

        assert answer == {"2025": 2, "2026": 3, "name": "x", "list": [1.5, None]}
        assert coerced
        assert driver.lenient({"2025": 2}) == ({"2025": 2}, False)
        assert driver.lenient(None) == (None, False)

    def test_a_text_number_scores_as_the_number_but_says_so(self, driver):
        question = ai_golden.question("M1")

        scored = driver.score(question, {"2025": "2", "2026": "3"})

        assert scored["verdict"] == "correct" and scored["coerced"]
        assert scored["answer_raw"] == {"2025": "2", "2026": "3"}
        assert driver.score(question, {"2025": "3", "2026": "3"})["verdict"] == "wrong"
        assert driver.score(question, None)["verdict"] == "missing"

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
