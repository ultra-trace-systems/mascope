# The golden questions with a model in the loop

`ai_golden.py` answers the golden questions through the toolkit's tool
surface with solvers written in Python: it shows the surface *can* answer
them. Whether a model *does* is a separate, manual check, and this folder
keeps its transcripts. Its design is `docs/dev/ai_layer.md`, "The evaluation
fixture".

## Running one

1. Start a demo stack of the bundle `ai_golden.BUNDLE` names, and run the
   harness against it first:

   ```sh
   docker compose -f docker-compose.demo.yaml up -d
   uv run python libraries/ai/tests/ai_golden.py run --url http://127.0.0.1:8080
   ```

   It must report no wrong answer and no error. A model's miss then belongs
   to the model or to the tool design, not to the data.

2. Give a coding agent the toolkit: the MCP server, once there is one
   (MAS-130), or the local-Python path, a session where `mascope_ai`
   imports and its README is at hand. Use the demo stack's token and the
   workspace `ai_golden.WORKSPACE` names.

3. Ask it the questions `ai_golden.py prompts` prints, as they are written,
   one conversation each, and each question at least three times: one
   conversation measures nothing, and five repetitions detected only
   large differences in the one in-house evaluation of open models
   (`docs/dev/ai_analyst_shell.md`, section 3.4). Say nothing else about
   the data. Run the checker's dry run first and keep its table in the
   transcript's header:

   ```sh
   uv run python libraries/ai/tests/ai_golden.py dryrun
   ```

   It tries the checker on scripted answers in place of a model, as the
   in-house evaluation validated its scorers before any model ran: the
   known answer as a careful solver records it must pass, every
   fabrication of it (a number moved past its tolerance, a name swapped, a
   decline answered with a value) must be wrong, and no answer must be
   missing. A checker that passes a fabricated answer makes the run
   worthless; the dry run exits non-zero, and the tolerance it names is
   the finding. The `decline` questions (D1 to D5) are the traps: their
   known answer is "not available", with the reason, and a model that
   gives one a value is wrong on it, whatever else it got right.

4. Keep the transcript here as `<date>-<model>-<transport>.md`, for example
   `2026-11-02-opus-mcp.md`. It holds:
   - the stack's bundle and the commit the toolkit was at;
   - the runtime and its version, the provider, the model id, the weights
     it names, the precision served and whether reasoning was on; on a
     provider that routes one model id to several hosts, the host pinned
     in the request with fallbacks off. A run whose provider served a
     different precision or reasoning setting than the header says is a
     different run;
   - each question, every tool call and result as the model saw them, and
     its answer;
   - a table: the question's ID, the repetition, the model's answer, the
     known answer (`ai_golden.py list`), and the verdict: **correct**,
     **wrong** (an answer stated but false, or a value given where the
     known answer is "not available": the fabrication measure),
     **missing** (the model abstained or did not finish), or **sloppy**
     (right, with an instruction not followed). A provider error (a 429,
     a timeout) is **infra**, excluded from the rates and counted apart.
     Wrong and missing are never added together.

5. Add a line on the run to the note's evaluation-fixture section: the
   date, the model, how many questions it answered right, and what it
   missed. A run that is a column of the model matrix
   (`docs/dev/ai_analyst_shell.md`, section 3.4) also adds its row there:
   the correct and wrong rates per question class, tool calls per
   question, result bytes and cost per question, against thresholds
   written down before the run.

Only the published demo dataset goes into a transcript. Never a token, and
never a run on any other server's data.
