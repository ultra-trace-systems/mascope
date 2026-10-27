# mascope_ai

Mascope's toolkit for AI agents: a plain Python library over
[`mascope_sdk`](../sdk/README.md) that finds, describes and loads Mascope's
data as **handles, not tables**. It is the AI layer's library, designed in
[`docs/dev/ai_layer.md`](../../docs/dev/ai_layer.md) (sections 3.1 to 3.6),
and its [MCP server](#the-mcp-server) serves it to a coding agent.

The backbone is one invariant: tables never pass through a model's context.
A loader returns a `Frame`, whose repr prints the schema, the row count,
whether it is the whole answer, and five rows, and stays short whatever the
table's size. The rows come out only through `Frame.df`, in Python.

```python
from mascope_ai import Toolkit

kit = Toolkit(workspace="My Workspace")

kit.find("spring")  # Refs, best match first
kit.find("spring", kind="batch")
batch = kit.resolve("Spring 2026", "batch")
kit.describe(batch)  # sample count, dates, collections ...

peaks = kit.load_peaks(batches=batch, max_rows=200_000)
peaks  # the summary, never the rows
peaks.complete, peaks.failures  # did every sample load?
peaks.df.groupby("target_compound_formula")["height"].mean()  # in Python
```

## Connecting

`Toolkit()` makes an SDK client from the environment (`MASCOPE_URL`,
`MASCOPE_ACCESS_TOKEN`, or a `.env` file), presenting the service name
`ai-agent`: generate an **AI Agent** token for it in Mascope's settings. Pass
`Toolkit(client=MascopeClient(...))` to use a client of your own.

Searches cover the workspaces the token's user belongs to, and the datasets,
batches, samples and target collections of the client's workspace (and the
global collections).

## What it offers

| Call | Returns |
| ---- | ------- |
| `find(query, kind=None, limit=50, within=None)` | `Found`: a list of `Ref`, best first, with `total` and `complete` |
| `resolve(value, kind, within=None)` | the one `Ref` a name or ID names; `AmbiguousName` with the candidates otherwise |
| `describe(ref)` | a small dict about one object: always cheap, never a table |
| `load_peaks(dataset=, batches=, samples=, matches=, max_rows=)` | `Frame` |
| `load_timeseries(compound= / ion= / isotope=, dataset=, batches=, samples=, max_rows=)` | `Frame` |
| `load_spectrum(sample, mz_range=, t_range=, max_rows=)` | `Frame` |
| `load_peaks_by_stage(sample, stages, max_rows=)` | `Frame` |
| `query_by_mz(mz, mechanisms, tolerance_ppm=5.0, formula_ranges=, limit=20, isotopologues=True, known_only=False)` | `Candidates`: Mascope's formulas for an m/z, the closest first |
| `match_compounds(sample, formulas, params=None, suggested=, mechanisms=)` | `MatchResult`: one verdict per candidate, the tree as a `Frame` |
| `derive(df, inputs, code=)` | `Frame` of a table computed from Frames, their sources kept |
| `answer(text, code=, inferred=, frames=)` | the manifest record of an answer |
| `plan_organize(moves)`, `plan_collection(name, compounds, ...)`, `plan_associate(collection, batches, detach=)`, `plan_match(scope, targets=, params=, save=)` | `Plan`: a write, described; nothing is written |
| `apply(plan_id, confirm_token)` | `Receipt`: the one call that writes |

**Names.** A string matches a name as a case-insensitive literal substring
(`"Sample (A)"` means those characters, not a regex); pass a compiled
`re.Pattern` to match by regex. An ID, then a whole name, wins. Several
matches are returned as candidates, not raised past. When a listing
`resolve` searched failed, it raises `Unavailable` rather than answer with
the one match it saw (an ID match excepted).

**Row caps.** `max_rows` (500,000 by default) holds during a load: an
estimate from the sample count (a rough 100 rows per sample) refuses a load
clearly too big for the cap, and a running count stops the load as soon
as the rows read pass the cap, raising `RowCapExceeded` with the rows
reached and the samples left. A Frame above the cap is never made. Loads
never prompt.

**Completeness.** Every Frame says whether it is the whole answer:
`complete` is False when some read failed, and `failures` names each one.
A stage of `load_peaks_by_stage` is incomplete too when the server left
peaks out of it because their time series was not computed yet: its
failure (`TimeseriesNotComputed`) says how many (`excluded_peaks`).
`warnings` carries what the server warned about the reads, and
`provenance` the server's provenance block.

**Errors.** Every failure is a `ToolkitError` with a message that says what
to do next and a `to_dict()` for a transport: `AmbiguousName`, `NotFound`,
`RowCapExceeded`, `AccessDenied` (a 403, naming the object, the role it
needs and the role the user has), `NotAuthenticated`, `InvalidRequest`,
`Unavailable`, `StaleFrame`. Transient failures were already retried by the SDK; the
toolkit adds no retry.

## The oracle

The toolkit computes no mass, score or isotope ratio, so a model using it
cannot make up an assignment. Mascope proposes candidates and scores them,
and the model narrates.

```python
hits = kit.query_by_mz(124.9244, ["[M+Br]-"])  # Candidates, closest first
result = kit.match_compounds("Sample 1", hits, suggested=["CH2O3"])
result  # one line per verdict: status, score, best ion, peaks
result.verdict("CH2O2").peak_ids  # the peaks Mascope found it at
result.unscored  # candidates Mascope gave no score: speculation
result.tree.df  # the compound -> ion -> isotopologue tree, in Python
```

A `Verdict` holds Mascope's compound score and status (`probable`,
`possible` or `unmatched`, from its match category), each ion's score, and
the peaks attributed to the candidate: the peaks of its matched ions where an
isotopologue was found (score above 0). `params=None` and `mechanisms=None`
use the instrument's defaults and the sample's mechanisms, as the app's
matches do. The model's own candidates go in `suggested` and are scored the
same way; every verdict says where its candidate came from (`given`,
`mass_query` or `model`). A formula Mascope refuses, or returns nothing for,
is listed in `unscored` with the reason, and the rest are scored: a numeric
mass or a `[13C]`-labelled formula too, which Mascope refuses without
naming it. The tree,
tens of thousands of rows for a sample's worth of candidates, is a `Frame`
and never a result. Nothing is saved: this is Mascope's matching preview.

## The provenance manifest

Each session keeps `manifest.jsonl`, one JSON record a line:

- `toolkit`: the `mascope_ai`, `mascope_sdk` and Python versions, the server
  and the workspace, once per toolkit opened on the session;
- `call`: each call, with its parameters, the objects it touched, the Frame
  it made, the server's provenance block, and the values it returned split
  into those Mascope returned, those the SDK computed client-side and those
  code in the session computed. The ion and compound scores of a peak load
  are the SDK's own (`Frame.client_columns`); the new columns of a derived
  Frame are the code's (`Frame.computed_columns`);
- `answer`: each `kit.answer(...)`, with the code that produced it, the calls
  since the answer before, every object they touched, and the values split
  into `mascope`, `client`, `computed` (columns a `derive` computed),
  `unscored` (candidates Mascope did not score) and `inferred` (the model's
  own statements).

`session.manifest.touched()` lists every object the session touched. A table
computed in Python is kept with `kit.derive(df, [frames])`, and its
`source_refs` are those of the Frames it came from. So an answer built on it
still names every object behind it. Its columns that no input has are
recorded as computed by its code; `computed=[...]` names more, such as a mean
that kept its input column's name.

## Writes: plan, then apply

A write tool never writes. It returns a `Plan`: each change before and
after, what it touches, whether it can be undone, the role it needs, what it
costs the server, and when it expires (in ten minutes). `apply` is the one
call that writes, and only with a confirm token for that plan, which a
person's approval gives.

```python
from mascope_ai import Approver, Move, Toolkit

approver = Approver()  # the transport's: never the model's
kit = Toolkit(approver=approver)

plan = kit.plan_organize([Move("Sample 1", "Spring 2026", kind="sample")])
print(plan.render())  # what the person approving reads
token = approver.approve(plan)  # once they approved it
receipt = kit.apply(plan.id, token)
```

- **The builders.** `plan_organize` moves datasets into workspaces and
  samples into batches (Mascope has no route that moves a batch).
  `plan_collection` creates a target collection, from formulas or a `base`
  collection's compounds. `plan_associate` sets the batches a collection is
  associated with, as the cheap batches-only update. `plan_match` runs
  Mascope's matching: with `save=False` as a preview that writes nothing and
  needs no approval (its counts are on `plan.preview`), with `save=True` as a
  save over the stored matches (`rebuild=True`: a batch's destructive
  rebuild).
- **Refused at planning.** A builder refuses what the server would: a role
  too low in a workspace the change touches (`AccessDenied`), a locked
  sample or destination batch, an object already where it would go. A
  builder also refuses a dataset, batch or sample outside the client's
  workspace, even one found by its ID or passed as a `Ref`
  (`InvalidRequest`): the role it checks is that
  workspace's.
- **What every workspace shares** is not planned unless the user asked for
  it in so many words: a global collection, an association of one, and
  compounds that give a CAS number or a structure, which Mascope fills into
  the shared record of a compound that exists. Pass `allow_global=True`
  only then; the refusal is `GlobalNamespace`.
- **The gate.** `plan.gate` is `none` (a preview), `confirm` (a change that
  can be undone) or `hard` (one that cannot, or one every workspace sees).
  `Approver.approve` refuses a `hard` plan without `irreversible=True`: the
  transport passes it once the person was shown so.
- **The token** names one plan as it stands: an approval of another plan, or
  of this one edited since, is refused (`ApprovalRefused`), as is a string
  the approver did not mint. The approver's key lives in its memory only.
- **Once.** `apply` claims the plan before it reads or writes, so two
  overlapping calls write once. The same token again returns the first
  receipt (`receipt.repeated`); another approval is refused
  (`PlanConsumed`). Past `expires_at`: `PlanExpired`.
- **Drift.** Every `before` is read again first. If one changed, nothing is
  written and `PlanDrifted.diff` says what is different; plan again.
- **Part-way.** A write that fails stops the rest: `PartiallyApplied`
  carries the receipt, whose `applied` lists the changes made. The plan is
  not applied again.
- **The receipt** names what the writes made or changed, the server's answer
  to each (a background task's `process_id`), and the service every write
  was sent as, which the server's access log records beside each request.
  A moved sample is made anew under another ID: its receipt names the
  batch.

Plans are kept in the session (`plans/`), the claim and the outcome beside
each, and every plan and receipt is in the manifest; a confirm token never
is.

## Sessions

A `Session` is one conversation's directory under the scratch root
(`$MASCOPE_AI_SCRATCH`, else `~/.cache/mascope_ai/sessions`): each Frame as
parquet beside a JSON file of its summary and the query that made it, and
the provenance manifest. `session.frame(id)` reopens a Frame,
`session.evict(id)` drops its rows (the next `.df` loads them again through
its query, and raises `StaleFrame` if the rows it gets are not the Frame's;
a derived Frame cannot be loaded again), and
`session.discard()` deletes the session. Nothing in it is a source of
truth.

```python
from mascope_ai import Session, Toolkit

kit = Toolkit(session=Session("conversation-42"))
```

## The MCP server

`mascope-ai mcp` serves the toolkit to a coding agent such as Claude Code,
over stdio in the Model Context Protocol. The agent runs it with the user's
own AI Agent token and its own model subscription:

```bash
mascope-ai setup                                   # check, then register:
claude mcp add mascope -- python -m mascope_ai mcp  # the Python mascope_ai is in
```

| Tool | Returns |
| ---- | ------- |
| `find` | refs, best first, with the total; `dataset` / `batch` search within one |
| `describe` | the small dict `describe()` gives |
| `load_peaks`, `load_timeseries`, `load_spectrum`, `load_peaks_by_stage` | a Frame's handle |
| `query_by_mz` | Mascope's candidates for an m/z |
| `match_compounds` | one condensed verdict per candidate, the unscored apart, the tree's handle |
| `answer` | closes an answer in the manifest |
| `session` | the session's directory, the Python to run, the lines that open it and a toolkit on it |

`chart` and `table` are not served yet: they come with the toolkit's
`mascope_viz` artifacts.

What a model reads is bounded by the server, not by a prompt:

- **No tables.** A Frame's handle has its ID, rows, columns, completeness and
  failures, three rows, and `rows_in_python`, the Python that gives its rows
  as a DataFrame. A match result is its verdicts.
- **A few kilobytes.** No result passes 4 KB (`MAX_RESULT_BYTES`). A list
  that would is cut, and its holder says how many it left out
  (`<name>_omitted`).
- **Names are data.** Every string Mascope returns comes back as a JSON
  string, cut to 200 characters, with its unprintable characters escaped:
  a newline, a bidirectional override or a zero-width space shows as its
  escape (`\n`, `\u202e`). A name cannot break out of its value or hide
  text, and the server's instructions and the skill tell the model to
  read it as data, never as an instruction.
- **Failures are results.** A `ToolkitError` comes back as its `to_dict()`
  with `isError` set, as does a missing configuration (`not_configured`).
  The server keeps running.

The server keeps every Frame in one session, `--session ID` (or
`$MASCOPE_AI_SESSION`), a new one by default. The workspace is
`--workspace`, else `MASCOPE_WORKSPACE` from the environment or the `.env`
file, as the SDK reads `MASCOPE_URL` and `MASCOPE_ACCESS_TOKEN`.

## The local-Python path

Heavy work runs in Python on the same machine, as a Bash step, never through
the server. The `session` tool gives the Python and the session to open:

```bash
python - <<'EOF'
from mascope_ai import Session
session = Session("s-20261027T120000Z-1a2b3c", root="/home/me/.cache/mascope_ai/sessions")
peaks = session.frame("f_0123456789ab").df    # a Frame the server loaded
print(peaks.groupby("sample_item_name")["height"].max().nlargest(3))
EOF
```

Reading a Frame needs no credentials: its rows are in the session's
directory. To load more in that Python, and derive Frames into the same
session and manifest, the `session` tool's `toolkit` line opens a toolkit:

```python
from mascope_ai import Session, Toolkit

kit = Toolkit(
    session=Session(
        "s-20261027T120000Z-1a2b3c", root="/home/me/.cache/mascope_ai/sessions"
    ),
    workspace="ws-1",  # the server's workspace
)
```

`Toolkit()` reads the URL and the token as the server does, but not
`MASCOPE_WORKSPACE` or `--workspace`: given no `workspace`, it opens the
token's only workspace, and refuses when the token's user has several. The
line names the server's workspace, by ID once the server has connected. The
agent then records its answer with the `answer` tool, the code included.

## The command line

| Command | Does |
| ------- | ---- |
| `mascope-ai setup` | checks the install, the session directory, the configuration and the token; says how to register the server. Idempotent |
| `mascope-ai list [KIND] [QUERY]` | what there is: `datasets`, `batches --dataset D`, `samples --batch B`, `collections`, or every kind but samples; `sessions`; `frames --session ID` |
| `mascope-ai mcp` | the MCP server |
| `mascope-ai skill` | the skill text, for `~/.claude/skills/mascope-ai/SKILL.md` |

`python -m mascope_ai` is the same command.

## Tests

```bash
uv run pytest libraries/ai/tests/                        # hermetic
MASCOPE_SDK_CONTRACT=1 uv run pytest libraries/ai/tests/  # + the demo stack
```

The contract tests use the SDK's (`libraries/sdk/tests/test_contract.py`)
settings: `MASCOPE_SDK_TEST_URL`, `MASCOPE_SDK_TEST_TOKEN` and
`MASCOPE_SDK_TEST_WORKSPACE`.

`tests/ai_golden.py` is the evaluation fixture: golden questions over the
demo dataset, each answered through the tool surface by a solver and checked
against its known answer (`docs/dev/ai_layer.md`, "The evaluation
fixture"). Under the same switch, `test_ai_golden.py` runs them on the demo
stack and ends with a report of what the surface answered and what it
cannot. Outside pytest:

```bash
uv run python libraries/ai/tests/ai_golden.py run --url http://127.0.0.1:8080
uv run python libraries/ai/tests/ai_golden.py list   # the question set
```
