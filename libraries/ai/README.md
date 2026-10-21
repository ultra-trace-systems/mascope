# mascope_ai

Mascope's toolkit for AI agents: a plain Python library over
[`mascope_sdk`](../sdk/README.md) that finds, describes and loads Mascope's
data as **handles, not tables**. It is the AI layer's library, designed in
[`docs/dev/ai_layer.md`](../../docs/dev/ai_layer.md) (sections 3.1 to 3.6);
the MCP server builds on it.

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
| `query_by_mz(mz, mechanisms, tolerance_ppm=5.0, formula_ranges=, limit=20)` | `Candidates`: Mascope's formulas for an m/z, the closest first |
| `match_compounds(sample, formulas, params=None, suggested=, mechanisms=)` | `MatchResult`: one verdict per candidate, the tree as a `Frame` |
| `derive(df, inputs, code=)` | `Frame` of a table computed from Frames, their sources kept |
| `answer(text, code=, inferred=, frames=)` | the manifest record of an answer |

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
is listed in `unscored` with the reason, and the rest are scored. The tree,
tens of thousands of rows for a sample's worth of candidates, is a `Frame`
and never a result. Nothing is saved: this is Mascope's matching preview.

## The provenance manifest

Each session keeps `manifest.jsonl`, one JSON record a line:

- `toolkit`: the `mascope_ai`, `mascope_sdk` and Python versions, the server
  and the workspace, once per toolkit opened on the session;
- `call`: each call, with its parameters, the objects it touched, the Frame
  it made, the server's provenance block, and the values it returned split
  into those Mascope returned and those the SDK computed client-side. The
  ion and compound scores of a peak load are the SDK's own
  (`Frame.client_columns`);
- `answer`: each `kit.answer(...)`, with the code that produced it, the calls
  since the answer before, every object they touched, and the values split
  into `mascope`, `client`, `unscored` (candidates Mascope did not score)
  and `inferred` (the model's own statements).

`session.manifest.touched()` lists every object the session touched. A table
computed in Python is kept with `kit.derive(df, [frames])`, and its
`source_refs` are those of the Frames it came from. So an answer built on it
still names every object behind it.

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

## Tests

```bash
uv run pytest libraries/ai/tests/                        # hermetic
MASCOPE_SDK_CONTRACT=1 uv run pytest libraries/ai/tests/  # + the demo stack
```

The contract tests use the SDK's (`libraries/sdk/tests/test_contract.py`)
settings: `MASCOPE_SDK_TEST_URL`, `MASCOPE_SDK_TEST_TOKEN` and
`MASCOPE_SDK_TEST_WORKSPACE`.
