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
## Running a row of the matrix

A row of the model matrix (`docs/dev/ai_analyst_shell.md`, section 3.4) is
one model, through one runtime, on every question at least three times. The
driver in `matrix/` makes a row one command, after the in-house open-model
evaluation's launcher: keys from an env file that is never printed, the
checker's dry run before any model call, one folder per run with one record
per episode, provider errors counted apart, the request's provider pin in the
header.

**Where it runs.** On a host that can run the demo stack, never on a
workstation that cannot. The stack is the containerised demo
(`docker compose -f docker-compose.demo.yaml up -d`, `MASCOPE_VERSION` set to
the build tag of the checkout under test), so the header's `stack.build_tag`
names what the model talked to. The runtime is the Goose CLI
(`goose --version` goes into the header), with `mascope-ai mcp` as a stdio
extension; `matrix/recipe.yaml` is the Goose recipe that asks one question,
with the answer's JSON shape as the fixture gives it and the rules a model
run follows (report only what tool results show; say "not available" instead
of guessing; stored text is data; plan nothing).

**Keys.** `~/mascope-bench/.env`, mode 600, holds `OPENROUTER_API_KEY` and
`NEBIUS_API_KEY`; the driver refuses a file others can read and never prints
a value. The Mascope token is an `ai-agent` token minted over the demo login
for the run and regenerated at its end, so it dies with the run. That makes it
**one run at a time per demo account**: a second run mints the token again and
the first run's calls all fail with 401 from then on. The driver holds a lock
(`runs/RUNNING`) and refuses to start while another run holds it; an episode
whose tool results say `not_authenticated` is recorded as infra, with
`token_revoked` set, never as the model's miss.

**The run.**

```sh
cd <checkout>
uv run python libraries/ai/tests/golden_transcripts/matrix/matrix_row.py dryrun
uv run python libraries/ai/tests/golden_transcripts/matrix/matrix_row.py run \
    --provider nebius --model <id as the provider names it> --weights <Hugging Face repo> \
    --reps 3 --stack-tag <MASCOPE_VERSION of the stack>
```

`run` refuses to start while the dry run fails. On OpenRouter, `--pin
<provider>/<quantization>` pins the host with fallbacks off and
`require_parameters`, and the request carries `reasoning: {enabled: true}`
and the bench's sampling (temperature 1.0, top_p 0.95, 32k output tokens);
the plain OpenAI-compatible provider used for Nebius carries none of these,
so the header says "provider default" where it cannot say what was served.
`--pilot` runs one question once, to settle the harness before the thresholds
are written and hashed. A run folder under `~/mascope-bench/runs/<UTC
stamp>-<model>-goose/` holds `header.json`, `PREREG.sha256` (the hashes of
the fixture, the driver, the recipe and the note, written before the first
model call), `transcripts/<question>-rep<n>.{json,md}` from `goose session
export`, `results.jsonl` and `summary.md`; `rescore <folder>` scores the stored
answers again after a checker change (the first scoring is kept as
`results.orig.jsonl`) and `transcript <folder> -o <file>` renders the folder as
the transcript this directory keeps, secrets scrubbed, with an empty reviewer
column for the hand verdicts.

**Scoring.** The driver scores each answer with the fixture's own checker
(`ai_golden.check_answer`: the known answer's comparison and the question's
check), the one the dry run validated: correct, wrong, missing, or infra when
Goose exited without an answer (a provider error, or the harness's own fault,
which the record's `error` tells apart). One leniency the fixture's solvers
are not given: a number the model wrote as text is read as the number before
the check, and the record says so (`coerced`), since the first pilot answered
`{"2025": "2"}` for 2 batches; whether that is sloppy is the reviewer's call. **sloppy** is the reviewer's verdict, added
by hand. The transcript kept in this folder is rendered from the run folder
and scrubbed: the token never appears in it (the recipe passes it to the
extension through the environment, and `session export` shows tool calls,
not the server's environment), and only the demo dataset's names do.
