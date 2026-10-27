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
   one conversation each. Say nothing else about the data.

4. Keep the transcript here as `<date>-<model>-<transport>.md`, for example
   `2026-11-02-opus-mcp.md`. It holds:
   - the stack's bundle and the commit the toolkit was at;
   - each question, every tool call and result as the model saw them, and
     its answer;
   - a table: the question's ID, the model's answer, the known answer
     (`ai_golden.py list`), and the verdict: right, wrong, or not answered.

5. Add a line on the run to the note's evaluation-fixture section: the
   date, the model, how many questions it answered right, and what it
   missed.

Only the published demo dataset goes into a transcript. Never a token, and
never a run on any other server's data.
