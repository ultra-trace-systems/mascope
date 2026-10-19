`ULTRA TRACE MASCOPE - DESIGN DOC - AI LAYER`

# Mascope AI layer: Design & Plan

## Purpose

Mascope computes; users interrogate. Today the interrogation surface is the web
app and, for power users, `mascope_sdk` in a notebook. This document designs a
third surface: a natural-language agent that answers questions about
mass-spectrometry data grounded in Mascope's own computations, and that can
delegate tedious data organisation (arranging batches and datasets, building
and associating target collections, launching matching runs) behind an explicit
approval gate.

It is an engineering design and phased plan, not a user guide. It supersedes
an uncommitted preliminary draft of 2026-08-11: the two
open questions that draft flagged are resolved here against the backend source,
its appendix is corrected, and the staging is revised around a constraint the
draft missed (section 1.3).

> **Status (2026-10-19).** Design revision after codebase verification. No code
> written. Written and verified against the tree on 2026-08-11 and committed
> 2026-10-14 as written. Section 1 and the appendix were re-read against
> `develop` on 2026-10-19, the first step of the plan: the SDK's write path
> (the batch-ledger import), the provenance block on SDK frames, unified name
> matching, the token-reachable assignment and upload routes, device-token
> lifetimes and `/save`'s upsert are now described; file links are the
> evidence. Sections 2 to 9 were not re-read and still speak from 2026-08-11
> where they restate section 1.

> **Scope decision (v1): read-only.** Phases 0-2 ship a read-only toolkit and
> agent. The write path (plan/apply, section 5) requires backend auth work and
> lands as its own phase, independently useful, after the read surface has
> proven itself.

> **Coexistence, not replacement.** The AI layer is a client of the existing
> API. It adds no new computation and no new authorization; every number it
> reports comes from Mascope, and every permission it exercises is the user's
> own.

---

## 1. Current state (as-is)

### 1.1 The SDK: read-mostly, notebook-shaped

[mascope_sdk](../../libraries/sdk/) wraps the REST API for notebook use:
workspace/dataset/batch/sample listings with an in-memory cache, peak and
time-series loaders with concurrent per-sample fetches, spectra, MS2, a sample
file's scan table
([sample_files.py](../../libraries/sdk/src/mascope_sdk/resources/sample_files.py)),
and the assignment results: a sample's peak-assignment runs and ledgers
([peak_assignments.py](../../libraries/sdk/src/mascope_sdk/resources/peak_assignments.py))
and a batch's batch ledger
([batch_peaks.py](../../libraries/sdk/src/mascope_sdk/resources/batch_peaks.py)).
Four RPC-style POSTs are queries, not mutations
([match_compound / match_compounds](../../libraries/sdk/src/mascope_sdk/resources/matching.py),
[peaks/timeseries](../../libraries/sdk/src/mascope_sdk/resources/samples.py),
[cheminfo/mz/query](../../libraries/sdk/src/mascope_sdk/resources/cheminfo.py)),
though `peaks/timeseries` computes and stores a peak's time series the first
time it is asked for one, which `compute_peak_timeseries` drives on purpose
([samples.py:671-778](../../libraries/sdk/src/mascope_sdk/resources/samples.py)).
The high-level loaders stamp the server's provenance block (deployment id,
`mascope_version`, `match_score_version`, `peak_assignment_engine_version`) on
every frame they return as `df.attrs["provenance"]`: the five loaders stamp
it on return
([client.py:648, 755, 859, 928, 1036](../../libraries/sdk/src/mascope_sdk/client.py)),
through a helper that fetches the block once
([client.py:460-485](../../libraries/sdk/src/mascope_sdk/client.py)). That is
what an agent's answer cites (section 3.4), but it is best effort: an older
server without the provenance route, or a failed call, returns the frame
unstamped, so the toolkit must check for the key rather than assume it. There is still no collections
resource: collections surface only as the `target_collection_ids` and
`target_collection_names` columns on peaks
([samples.py:332-345](../../libraries/sdk/src/mascope_sdk/resources/samples.py)).

**One write path exists: the batch-ledger import.**
`batch_peaks.import_run` posts an external engine's batch-level result to
`POST /api/batch-peaks/batch/{id}/runs/import` and polls until the run
completes
([batch_peaks.py:199-280](../../libraries/sdk/src/mascope_sdk/resources/batch_peaks.py)).
The server matches each row to the nearest batch peak, measures the engine's
formula against that peak's members, and lands the result as a new batch run
under the engine's name; curated and isotopologue batch peaks are left alone,
and the name `mascope` is reserved so an import cannot pass as the server's
own run. The `BatchPeaksResource` class docstring's example lands a peaky
(1.4) result
([batch_peaks.py:55-58](../../libraries/sdk/src/mascope_sdk/resources/batch_peaks.py)).
(The module's own docstring still calls the resource read-only.) Besides it,
the agent helpers in
[_agents.py](../../libraries/sdk/src/mascope_sdk/_agents.py) - the uploads
`api_post_file` and `api_post_file_tus`, and the device-token renewal
`api_renew_agent_token`, which posts to `POST /api/auth/devices/token`
([_agents.py:276-301](../../libraries/sdk/src/mascope_sdk/_agents.py)) - are
exported but documented as not part of the public SDK; the File Agent uses
them under its own service name.

How phase 3 relates to it: `import_run` is additive (a new run beside the
existing ones, nothing deleted or moved), engine-specific, and reaches a route
that was opened to the SDK for it. Phase 3's wrappers are the other kind: they
reorganise or overwrite the user's own data (datasets, batches, samples,
collections, compounds, `/save`). `import_run` is the shape to copy for them -
one thin method over one route, keyword-only options, the server's record
returned, an asynchronous job polled - but not the risk profile. The toolkit
treats it as a write all the same: if `mascope_ai` ever exposes it, it goes
through plan/apply (section 5) with a soft gate, like any other write.

Five behaviors matter to an agent wrapper. Four are still wrong for it as-is;
the fifth has been fixed since the draft:

- **`confirm_above` does not guard agents.** The interactive prompt
  ([_loaders.py:179-204](../../libraries/sdk/src/mascope_sdk/_loaders.py))
  catches `EOFError` on non-interactive stdin and **proceeds silently** — the
  one caller it was meant to slow down is the one it waves through. On a TTY or
  notebook it blocks, which is worse. Unchanged.
- **Partial results are silent.** The concurrent loader
  ([_concurrent.py:26-85](../../libraries/sdk/src/mascope_sdk/_concurrent.py))
  swallows per-task failures and returns partial data with only a log
  warning; there is no programmatic completeness signal. An agent summarising a
  batch cannot tell "March had low signal" from "half of March failed to load."
  The loaders now attach the server's own warnings to the frame as
  `df.attrs["warnings"]`
  ([_loaders.py:16-37](../../libraries/sdk/src/mascope_sdk/_loaders.py)), but
  those say which peaks a read left out, not which reads failed. Unchanged in
  substance.
- **Name resolution: one contract now, with a fallback.** Both `resolve_id`
  and the loaders' filters match a plain string as a case-insensitive literal
  substring and a compiled `re.Pattern` as a regex
  ([_resolve.py:15-50](../../libraries/sdk/src/mascope_sdk/_resolve.py)), so a
  name containing `(` matches the same way everywhere. Name resolution keeps a
  deprecation shim: when the literal match finds nothing and the string works
  as a regex that does match, that match is returned with a
  `DeprecationWarning`
  ([_resolve.py:54-86](../../libraries/sdk/src/mascope_sdk/_resolve.py)). An
  agent wrapper should pass names it means literally and treat that warning as
  an error until the shim is removed.
- **Cache hands out references.** Cached listing DataFrames are returned
  by object, not copy: workspaces, datasets, batches and ionization
  mechanisms ([datasets.py:41-52](../../libraries/sdk/src/mascope_sdk/resources/datasets.py)
  and its siblings) and the per-batch sample cache
  ([samples.py:241-251](../../libraries/sdk/src/mascope_sdk/resources/samples.py));
  a mutating caller corrupts the session cache. That includes the common
  single-batch call: `samples.list(batch=...)` returns the cached frame itself
  ([samples.py:200-202](../../libraries/sdk/src/mascope_sdk/resources/samples.py)).
  Only the multi-batch `samples.list(batches=...)` copies each batch's frame
  before adding a column
  ([samples.py:221](../../libraries/sdk/src/mascope_sdk/resources/samples.py)),
  so only its result is safe to mutate.
- **Score columns are client-side.** `match_score_ion` and
  `match_score_compound` are computed in the SDK from per-isotope scores
  ([samples.py:479-513](../../libraries/sdk/src/mascope_sdk/resources/samples.py)),
  not returned by the API — relevant to any provenance claim about "values
  returned by Mascope". The provenance block's `match_score_version` is the
  server's scoring switch, not a version of this aggregation
  ([provenance.py](../../server/backend/src/mascope_backend/provenance.py)).
  Unchanged.

### 1.2 The backend: full write surface, workspace ACL

Every write the AI layer could want already exists (corrected inventory in the
appendix). Authorization is uniform: handler-body `check_*_access` calls and
`require_*_role` dependencies in
[workspaces/dependencies.py](../../server/backend/src/mascope_backend/api/new/workspaces/dependencies.py),
over role levels guest 100 / editor 200 / admin 300 / owner 400
([roles.py](../../server/backend/src/mascope_backend/roles.py)), resolving
sample → batch → dataset → workspace. Superusers bypass. An agent holding the
user's token cannot exceed the user's permissions, so the AI layer makes no
authorization decisions of its own.

Two verified facts settle the draft's open questions:

- **Collection↔batch association is a payload field, not an endpoint.**
  `sample_batch_ids` on `TargetCollectionCreate`/`TargetCollectionUpdate`
  ([target_collection_pydantic_model.py:103-150](../../server/backend/src/mascope_backend/api/models/target/collections/target_collection_pydantic_model.py));
  the association router is GET-only. The update model forbids changing
  compounds and batches in one request, and a batches-only update is
  deliberately cheap: guest on the collection, editor only on the *changed*
  batches
  ([target_collections_routes.py:122-152](../../server/backend/src/mascope_backend/api/routes/target/collections/target_collections_routes.py),
  [check_collection_batch_change_access](../../server/backend/src/mascope_backend/api/new/workspaces/dependencies.py)),
  so a workspace editor can attach a global collection to their own batches.
  `plan_associate` maps to a batches-only PATCH.
- **Non-save aggregate matching is genuinely side-effect free.**
  `POST /api/match/aggregate/sample/{id}` and `/batch/{id}` run
  [aggregate_matches](../../server/backend/src/mascope_backend/api/controllers/match/aggregate/match_aggregate_controller.py)
  — pure select + pandas, guest-gated, no commits, no sentinels, no advisory
  locks. The token-reachable sample `/compound(s)` previews build their
  transient targets in a session that is never committed
  ([match_aggregate_sample_controller.py](../../server/backend/src/mascope_backend/api/controllers/match/aggregate/sample/match_aggregate_sample_controller.py)).
  The match-refresh sentinels and locks live exclusively on the `/save`,
  `/resave`, `/compute`, `/rematch` paths. `/save` overwrites the scope's
  stored aggregates: it clears the scope's sample-level match rows up front as
  a crash-safe completeness marker, then upserts the ion, compound, collection
  and sample levels, in bounded sample chunks for a batch save
  ([`aggregate_and_create_matches`, match_aggregate_controller.py:578](../../server/backend/src/mascope_backend/api/controllers/match/aggregate/match_aggregate_controller.py)).
  `/resave` (batch only) is still a **destructive rebuild**: it removes the
  ion, compound, collection and sample levels before recreating them
  (`aggregate_and_recreate_matches`, the same file, `:823`). So: preview freely, gate `/save`
  and `/resave` hard.

One asymmetry needs designing around: **target compounds have no workspace
ACL.** The compound routes check only global editor
([target_compounds_routes.py](../../server/backend/src/mascope_backend/api/routes/target/compounds/target_compounds_routes.py)),
and a collection with `workspace_id IS NULL` is global — readable by everyone,
mutable by global admins. An agent must treat global collections and the
compound namespace as shared infrastructure, not the user's sandbox
(section 6).

### 1.3 The constraint the draft missed: token access is per-route

Access-token auth is doubly gated. The token's stored service name must match
the `X-Service-Name` header
([backend.py:106-124](../../server/backend/src/mascope_backend/api/new/auth/backend.py))
— **and the endpoint must be decorated `token_access=True`, or the request is
401'd** ([backend.py:147-157](../../server/backend/src/mascope_backend/api/new/auth/backend.py);
the flag is set by `@api_route`,
[api_features.py:261](../../server/backend/src/mascope_backend/api/lib/api_features.py)).
The allowlist is enforced when a token is minted, not on each request: the
request model refuses a service outside it
([access_token/schemas.py:15-24](../../server/backend/src/mascope_backend/api/new/auth/access_token/schemas.py))
and an admin script (`clean_access_tokens`) purges stored tokens whose
service is not on it
([models.py:362](../../server/backend/src/mascope_backend/db/models.py)); a
stored token is not re-checked against the list per request. The
allowlist is `mascope_sdk`, the internal `file-converter`, and four agents
(`tof-agent`, `file-agent`, `export-agent`, `instrument-agent`)
([access_token/config.py:16-23](../../server/backend/src/mascope_backend/api/new/auth/access_token/config.py)).
62 handlers carry the decorator, plus the six generated resumable-upload (tus)
operations stamped by hand
([sample_files_routes.py:1059-1067](../../server/backend/src/mascope_backend/api/routes/sample/files/sample_files_routes.py)):
68 operations in the OpenAPI schema, 35 of them GETs.

Consequences:

- **The organisation routes are not token-reachable.** Datasets, batches,
  sample items, target collections, target compounds, and aggregate `/save`
  and `/resave` all still refuse a token (appendix). Phase 3 is therefore still
  backend work (auditing and opening routes) before it is SDK work (wrapping
  them). The draft's "roughly 15 thin wrapper methods" stands, but as the
  second half of the phase.
- **Other write routes are, now.** Since the draft, 33 non-GET operations take
  a token (31 POST, PATCH or DELETE, plus the tus HEAD and OPTIONS). Some are queries (peak and sample-file time series,
  `cheminfo/mz/query`, sample `match/aggregate/.../compound(s)`, the
  peak-assignment fits). The real writes are uploads and sample-file creation
  and bulk delete
  ([sample_files_routes.py](../../server/backend/src/mascope_backend/api/routes/sample/files/sample_files_routes.py)),
  `POST /api/instrument_configs`, device-token renewal, and the
  peak-assignment surface: per-sample verify, edit, assign, run import, run
  delete and recalibrate
  ([peak_assignments/routes.py](../../server/backend/src/mascope_backend/api/new/peak_assignments/routes.py)),
  and the batch ledger's backfill, untargeted search, verify, retract, curate,
  release-curation, export and run import
  ([batch_peaks_routes.py](../../server/backend/src/mascope_backend/api/new/peak_assignments/batch_peaks_routes.py)).
  Each was opened route by route, the model decision 7.5 recommends for
  phase 3. The SDK wraps one of them
  (`batch_peaks.import_run`, 1.1); the per-sample run import
  (`POST /api/peak-assignments/sample/{id}/runs/import`) has no SDK method.
  The toolkit must not call any of them outside plan/apply.
- `POST /api/match/rematch/sample/{id}` **is** still token-accessible
  ([match_routes.py:334-335](../../server/backend/src/mascope_backend/api/routes/match/match_routes.py))
  — a destructive recompute reachable with any SDK token whose user is an
  editor on the sample. Its token access deserves a review of its own.
- A `mascope_ai` toolkit speaking through the SDK today must present
  `service_name="mascope_sdk"`; a token minted for one service is refused under
  another name, and a name outside the allowlist cannot be minted.
  Attributable agent traffic requires adding an `ai-agent` service to the
  allowlist and an arm to the minting-role gate
  ([access_token/routes.py:44-60](../../server/backend/src/mascope_backend/api/new/auth/access_token/routes.py)),
  which refuses any service it does not name.
- SDK tokens are 360-day
  ([access_token/config.py:25-27](../../server/backend/src/mascope_backend/api/new/auth/access_token/config.py)),
  full-user-rights, one active per service (minting removes the user's earlier
  tokens for it,
  [access_token/service.py:267-270](../../server/backend/src/mascope_backend/api/new/auth/access_token/service.py)),
  and stored in clear
  ([models.py:335](../../server/backend/src/mascope_backend/db/models.py)).
  Hashing them and shortening the default lifetime is in progress (MAS-75); the
  number is still open. One short-lived facility exists since the draft, for
  paired devices only: a device-bound agent token expires after 30 days and the
  agent renews it before then through `POST /api/auth/devices/token`
  ([devices/routes.py:40-42](../../server/backend/src/mascope_backend/api/new/auth/devices/routes.py);
  the SDK's `api_renew_agent_token`, 1.1); an expired one is refused
  ([access_token/config.py:56](../../server/backend/src/mascope_backend/api/new/auth/access_token/config.py),
  [validation.py:75-103](../../server/backend/src/mascope_backend/api/new/auth/access_token/validation.py)).
  **There is still no per-session or scoped token for a person** — the in-app
  surface (phase 4) needs one built.

### 1.4 The peaky precedent

The [peaky](https://github.com/ultra-trace-systems/peaky) skill (formula assignment
over Mascope, driven from Claude Code) is the working prototype of the
division of labour this design generalises, and it sharpened three principles:

- **No LLM in the numeric loop.** In peaky, the model does not even propose
  candidate formulas — a deterministic grid enumerator generates candidates,
  Mascope scores them, and local arbitration picks winners. The model chooses
  context (reagent, polarity, flags) and narrates results. That is why a run is
  reproducible and auditable, and it is the standard the AI layer's
  interpretation path must meet.
- **Tables never transit context.** Peaks live in local parquet; a single
  in-memory ledger DataFrame is the only authoritative state; only CLI stdout
  summaries and artifact paths reach the model.
- **The oracle's payload shape matters, not just context safety.**
  `match_compounds` returns the full compound→ion→isotopologue tree; one
  peaky pass returned 36k-141k isotopologue rows per sample to assign ~600-1300
  peaks, which drove peaky to in-process `mascope_tools` scoring. The AI
  layer's oracle wrapper must condense trees to verdicts *inside Python* and
  treat local scoring as a known optimisation path.

Also directly reusable: provenance as "the scientific content of a run is a
pure function of its inputs" (run manifests with package versions, config
fingerprint, input hashes), a `list` CLI as the model's discovery surface, and
an idempotent `setup` preflight.

---

## 2. Assets already in the tree

| Asset | Reused as |
|---|---|
| `mascope_sdk` read surface, retries, error taxonomy | the transport under `mascope_ai`; fix, don't fork (section 3.2) |
| Backend write endpoints + `check_*_access` ACL | the entire authorization model, inherited |
| `POST /match/aggregate/...` (non-save) | the free-to-call matching preview |
| `cheminfo/mz/query` | `query_by_mz` oracle entry |
| peaky's SKILL.md + docs | first skill to fold in; template for the skill set |
| Demo stack + published demo dataset | the evaluation fixture (section 4, phase 1) |
| `X-Service-Name` header + backend logging | agent-traffic attribution, once `ai-agent` is allowlisted |

---

## 3. Architecture

The toolkit is the asset; transports are disposable adapters.

```
              mascope_ai  (plain Python library)
      typed ops - caps - workspace scoping - handle returns
      write plans - provenance records - session store
                  |                     |
        +---------+                     +---------+
   MCP server (thin)                    PTC custom tools (thin)
        |                                        |
   Claude Code                          backend agent service
   near-term                                 long-term
```

Near-term: an MCP server driven by Claude Code, heavy compute in local Python
via Bash, the user's own token and subscription. Long-term: a chat panel in the
web app over Anthropic's code-execution sandbox with programmatic tool calling
(PTC) — the sandbox has no network access, so `mascope_ai` calls pause the
container, execute server-side with the user's credentials, and return data
*into the running script*, never into model context. Both transports share the
invariant that only printed summaries and artifacts reach the model.

**The trap to avoid** is designing MCP tools that return DataFrames serialised
as text — it burns context locally and produces a tool surface that cannot
migrate. "Handles, not tables" must be a property of the toolkit's types, not a
convention in a prompt.

### 3.1 Types

```python
Ref(id, kind, name, parent, modified)     # kind: workspace|dataset|batch|sample|collection

Frame(id, n_rows, columns, dtypes, head5, source_refs, query,
      complete: bool, failures: list[FailureNote])
    .df -> pd.DataFrame        # materialises; only ever touched inside Python

Plan(id, kind, summary, changes: list[Change], affected: list[Ref],
     reversible: bool, estimated_cost: str | None, expires_at)
Change(ref, field, before, after)
Receipt(plan_id, applied_at, results: list[Ref], service_name)
```

`Frame.__repr__` prints the schema, row count, completeness, and five rows —
so a model that carelessly echoes a Frame leaks a summary, not 400k rows. That
invariant is what makes the design survive a careless prompt.

`complete`/`failures` exists because of the SDK's silent-partial behavior
(1.1): the toolkit either loads with its own strict concurrency or lands a
small SDK change exposing per-task failures, and every Frame states whether it
is the whole answer. A provenance layer that can't distinguish "low signal"
from "failed to load" is not a provenance layer.

Frames are backed per transport behind one API: locally, parquet files in a
session directory addressable from Bash Python; in-app, objects in the sandbox
process returned directly into the running script by PTC.

### 3.2 Tool surface: reads

```python
find(query, kind=None, limit=50) -> list[Ref]
describe(ref) -> dict
load_peaks(dataset=None, batches=None, samples=None, *,
           matches=True, max_rows=500_000) -> Frame
load_timeseries(compound=None, ion=None, isotope=None, *,
                dataset=None, batches=None) -> Frame
load_spectrum(sample, mz_range=None) -> Frame
load_peaks_by_stage(sample, stages) -> Frame
```

`find` is one fuzzy entry point rather than four `list()` calls; it is backed
by the SDK's listings and cache but **unifies name matching to
escaped-literal-by-default** (compiled regex allowed explicitly), healing the
1.1 inconsistency. On ambiguity it returns the candidate list rather than
raising — for an agent, candidates are an answer, not an error. It must also
cover collections, which means a small `collections.list()` addition to the SDK
(read route token-access permitting; phase 0 audits this).

`describe` returns everything about one object and is always context-safe: for
a batch — sample count, date range, ionization mode, associated collections,
match status. It is the model's orientation tool and must stay cheap enough to
call freely.

`max_rows` replaces `confirm_above` (which the toolkit always disables). Sample
counts give only an estimate: samples carry different numbers of peaks,
`matches=True` multiplies rows, and the peak endpoint has no row limit of its
own. So the cap is enforced during the load, not before it: the toolkit
estimates from sample counts first and refuses an obviously oversized query
outright; otherwise it loads sample by sample through the SDK's concurrent
fetch, keeps a running row count, and stops at the cap, raising with the count
reached and the samples left unloaded so the model narrows the query itself.
A Frame above the cap is never materialised. Loads never prompt.

### 3.3 Tool surface: the oracle

```python
match_compounds(sample, formulas, params=None) -> MatchResult   # condensed verdicts
query_by_mz(mz, mechanisms, tolerance_ppm=5.0) -> list[Candidate]
```

The model has no tool that computes a mass, a score, or an isotope ratio, so
it structurally cannot invent an assignment. In an analytical-instrument
product, a model that confidently fabricates a formula assignment is worse
than no feature at all; this is a property of the tool surface, not an
instruction.

Two refinements over the draft, both from peaky (1.4):

- **Candidate generation should be deterministic wherever possible.** For
  "what is the peak at 180.063?" the path is: `query_by_mz` (or a local grid
  enumeration) proposes, `match_compounds` scores, the model narrates. The
  model may *suggest* additional candidates from chemical context — that is
  legitimate use of a language model — but every suggestion goes through the
  same scoring, and the answer distinguishes scored verdicts from unscored
  speculation.
- **`MatchResult` is a condensed verdict, not the raw tree.** The wrapper
  reduces the compound→ion→isotopologue payload to per-candidate scores plus
  the attributed peak ids inside Python. The full tree is available as a Frame
  for analysis, never as a tool result.

### 3.4 Output and provenance

```python
chart(frame, kind, x, y, **kw) -> Artifact
table(frame, max_rows=50) -> Artifact
```

Worth having over free-styled matplotlib: consistent rendering across both
transports, and artifacts are the natural unit for the in-app panel.

Every answer carries a provenance record: the code that produced it, the
Mascope objects touched (`source_refs` accumulate through every Frame
operation), toolkit and SDK versions, and a visible split between values
returned by Mascope, values computed client-side (the SDK's ion/compound
scores, 1.1), and statements inferred by the model. Session-level, this is a
`manifest.jsonl` in the session directory — peaky's run-manifest pattern at
conversation granularity.

### 3.5 Sessions

A session = one conversation = one directory under the toolkit's scratch root:
Frames as parquet, the provenance manifest, and pending Plans as JSON. Frames
are cheap caches — evictable anytime, reloadable from their recorded query.
Plans expire (`expires_at`, default minutes not hours) and `apply` re-validates
before executing: the plan must exist, be unexpired, and its recorded `before`
values must still match reality; drift aborts with a fresh diff. Sessions are
disposable; nothing in them is a source of truth.

### 3.6 Errors

What the model sees on failure decides whether it recovers or hallucinates.
The toolkit maps the SDK's exception taxonomy to structured, actionable
results: a 403 names the object and the missing role ("you have guest on
workspace X; moving a dataset needs editor") rather than leaking a stack
trace; an oversize load returns the actual count and the cap; ambiguous names
return candidates (3.2). Transient errors are already retried by the SDK
(4 attempts, exponential backoff) — the toolkit adds no retry layer on top.

### 3.7 Models and routing

The draft proposed routing question types to different models at session
start. Dropped for now: near-term the session model is whatever the user runs
Claude Code with, and mid-conversation switching invalidates the prompt cache.
The three-path distinction (metadata / analysis / interpretation) survives as
*tool design* — metadata questions resolve in one cheap `find`/`describe`
call, analysis runs in Python, interpretation goes through the oracle — not as
infrastructure. Revisit for the in-app surface when cost data from phase 1
exists.

### 3.8 Domain knowledge as skills

Not one large system prompt: separate skills for CIMS reagent chemistry,
normalisation, background subtraction, Kendrick/GKA, van Krevelen. Peaky's
SKILL.md is the prototype and first entry. Progressive disclosure keeps the
cached prefix small; the same files serve Claude Code now and the Skills API
later.

---

## 4. Phased plan

Each phase is intended to land independently and leave the system shippable.

### Phase 0 - Foundations and audits (no user-visible behavior)

- **Token-access audit**: enumerate every read route the toolkit needs
  (listings, peaks, spectra, timeseries, aggregate, cheminfo, collections
  read) and verify or add `token_access=True`. Produce the same table for the
  write routes as input to phase 3.
- **SDK strictness PR**: expose per-task failures from `run_concurrent`
  (completeness signal for `Frame`), and add a documented way to disable
  `confirm_above` non-interactively (or deprecate it).
- **`ai-agent` service**: add to `ALLOWED_SERVICES` with a guest minting gate,
  so agent traffic is attributable from the first real session.
- Decide the review of `POST /api/match/rematch/sample/{id}`'s token access
  (1.3) — tracked as its own backend issue regardless of this project.

### Phase 1 - `mascope_ai` toolkit + MCP server, read-only

- The library: `Ref`/`Frame` types, session store, `find`/`describe`, the four
  loaders with `max_rows`, oracle wrappers with condensed verdicts, `chart`/
  `table`, provenance manifest.
- MCP server exposing the thin tool set; heavy work documented as the
  local-Python path (Bash + `mascope_ai` imports), mirroring peaky.
- **Evaluation fixture**: a golden-question set against the demo dataset
  (metadata, analysis, and interpretation questions with known answers),
  runnable as pytest against the demo stack. This is the acceptance test for
  the tool surface — "validates the tool design against real questions" needs
  a definition of validate.

### Phase 2 - Skills

- Fold peaky in as the assignment skill; extract the reusable preamble
  (three-layer mental model, handles-not-tables rule) into a shared base.
- Add normalisation, background subtraction, reagent chemistry as separate
  skills, each with the eval set extended to cover it.

### Phase 3 - Writes: backend token access, SDK methods, plan/apply

- Backend: open the audited write routes to token auth (per-route review, not
  blanket), including `/save` variants.
- SDK: the ~15 thin write wrappers (datasets create/update/move/delete;
  batches update/copy/delete; samples move/copy/update; collections
  create/update/delete; compounds add/update/remove; matching run/save).
  Independent of the AI layer and useful on its own.
- Toolkit: `plan_*` builders and `apply` (section 5), with the transport-level
  confirmation gate.

### Phase 4 - Productization: the in-app agent

- Second transport over the proven toolkit: PTC custom tools, backend agent
  service.
- Short-lived per-session tokens (new auth facility, scoped from the 360-day
  SDK token).
- Approval cards rendering `Plan` objects; artifact rendering in the panel;
  per-workspace cost attribution.
- Revisit model routing (3.7) and self-hosted vs Anthropic sandbox with phase
  1-3 usage data in hand.

Phases 0-3 are worth having even if the in-app panel never ships.

---

## 5. Writes: the plan/apply protocol

Confirmation is a property of the API, not the UI. A write tool never mutates;
it returns a `Plan`. A separate call applies it.

```python
plan_organize(moves: list[Move]) -> Plan       # datasets, batches, samples
plan_collection(name, compounds=[...], workspace=None, base=None) -> Plan
plan_associate(collection, batches) -> Plan    # batches-only PATCH (1.2)
plan_match(scope, targets=None, params=None, save=False) -> Plan

apply(plan_id: str, confirm_token: str) -> Receipt
```

- `apply` is the sole mutating function: one audit point, one approval point,
  one place to stamp the service name.
- **Approval is consumed, and `apply` is idempotent.** Before any write, the
  plan's stored state moves from pending to applying by a compare-and-set, so
  two overlapping calls cannot both pass: the first to win writes, the other
  waits for its Receipt. A later `apply` with the same plan and token returns
  the existing Receipt without writing again, so a retry after a lost response
  is safe. A plan that fails part-way records in its Receipt which changes
  applied and is not re-appliable; the operator plans again from the new
  state. Both transports inherit this from the toolkit, so neither can repeat
  a write.
- **The confirm token is minted by the transport, never the model.** Near-term
  the MCP `apply` tool renders the plan and the Claude Code permission prompt
  is the human gate; the token binds the approval to the specific plan
  (id + hash of its changes), so approving one plan cannot authorise another.
  In-app, the approval card returns the token. The model cannot fabricate a
  token because it never sees one until the human acts.
- `reversible` drives how hard the gate presses: renaming a batch and deleting
  one are different risks; `/save` and `/resave` are destructive-rebuild (1.2)
  and always gate hard.
- `estimated_cost` surfaces expensive operations (matching a 200-sample batch)
  before approval, not in a progress bar.
- `plan_match(save=False)` needs **no** gate at all — verified pure (1.2).
  Preview-then-persist is the backend's existing model; the toolkit keeps it.
- Stale plans: `apply` re-validates `before` values and expiry (3.5).

Every write maps to a verified endpoint + ACL (appendix); the plan builder
surfaces the required role in the plan itself so a doomed apply fails at
planning time, not approval time.

---

## 6. Security

- **Authorization is inherited, not implemented.** The backend's
  `check_*_access` family is the entire model; the agent holds the user's
  token and cannot exceed the user. The AI layer makes no authorization
  decisions.
- **Attribution**: `ai-agent` service name (phase 0) makes agent traffic
  distinguishable in backend logs on both transports.
- **Global namespace guardrail**: global collections (`workspace_id IS NULL`)
  and the target-compound namespace are shared across all users (1.2). Plan
  builders refuse to target them unless the user explicitly overrides, even
  when the user's global role would permit it. The ACL allows it; the toolkit
  still shouldn't default to it.
- **Prompt injection is a live vector.** Sample names, batch names, dataset
  names, and uploaded filenames are user-controlled strings that flow into
  model context via `find`/`describe`. Treat every string Mascope returns as
  data, never instruction; skills state this, and plan rendering quotes
  object names inertly.
- **In-app tokens**: phase 4 must not reuse the 360-day SDK token; short-lived
  per-session minting is a prerequisite, not an optimisation.

---

## 7. Key design decisions

These are the forks that shape the work. Current recommendations are recorded
so the plan is actionable; revisit as phases land.

1. **Handles vs serialised tables.** *Recommend handles (`Frame`), enforced by
   type.* The repr-leaks-a-summary invariant is the design's backbone; both
   transports depend on it.
2. **Where candidates come from in interpretation.** *Recommend deterministic
   generation (cheminfo / grid) with model suggestions allowed but always
   scored.* Peaky's no-LLM-in-the-loop is the reproducibility standard; pure
   model proposal is the fabrication path.
3. **Fix the SDK vs wrap around it.** *Recommend fixing in place* (strict
   concurrency signal, name-matching unification, `confirm_above`) — the fixes
   are small, benefit notebook users too, and forking the load path would
   double the contract-test surface.
4. **Model routing.** *Recommend dropping for v1* (3.7); the distinction
   lives in tool design. Reconsider in phase 4 with real cost data.
5. **Write-route token access: blanket or per-route.** *Recommend per-route
   audit and opening* (phase 0/3); blanket `token_access=True` would silently
   widen the exposure of a 360-day-token model that was scoped to reads by
   default.
6. **Oracle transport.** *Recommend API-first* (`match/aggregate` +
   `cheminfo`), with peaky-style in-process `mascope_tools` scoring as a
   measured optimisation if payload sizes bite — not a v1 dependency.

---

## 8. Scope summary

The genuinely new engineering:

- `mascope_ai`: the types (`Ref`, `Frame`, `Plan`), session store, provenance
  manifest, oracle condensation, plan/apply, and the MCP adapter.
- Backend: `ai-agent` service entry, per-route token-access opening, and
  (phase 4) short-lived session tokens.
- SDK: failure-signal + name-matching fixes, `collections.list()`, ~15 write
  wrappers.
- The evaluation fixture over the demo dataset.

Everything else reuses what exists: the API and its ACL, the SDK's transport
and error taxonomy, peaky's skill and its patterns, the demo stack.

---

## 9. Open decisions

1. **Cost attribution for the in-app surface** — per workspace, per user, or
   pooled. (Drives whether token budgets are enforced per session.) Leaning:
   per workspace, matching how Mascope already scopes everything else; defer
   until phase 4.
2. **Anthropic-hosted vs self-hosted sandbox for in-app.** Self-hosting
   removes the no-network constraint (the worker imports `mascope_ai`
   directly; PTC unnecessary) at the cost of running the infrastructure.
   Leaning: decide on phase 1-3 query-mix data; the toolkit is transport-
   agnostic either way, so nothing is blocked on this.
3. **Should `find` fan out server-side?** A single backend search endpoint
   would beat N cached listings for large deployments. Leaning: client-side
   over the SDK cache for v1; measure before adding API surface.
4. **How much of peaky merges into `mascope_ai` vs stays a consumer.**
   Leaning: peaky remains a standalone package and skill; `mascope_ai` adopts
   its patterns, not its code. Revisit if the assignment skill needs toolkit
   internals.

---

## Appendix: verified backend write surface

Re-verified 2026-10-19 against `server/backend/src/mascope_backend/api/`
(first verified 2026-08-11). Routers are listed in
[api/routes/\_\_init\_\_.py](../../server/backend/src/mascope_backend/api/routes/__init__.py)
and mounted without a further prefix. Changes since 2026-08-11: locked
datasets, batches and sample items take a higher role or refuse the write;
batches gained an import route; `/save` upserts instead of deleting first
(1.2); and the peak-assignment, batch-ledger and upload surfaces exist and take
a token.

| Capability | Endpoints | ACL | Token |
|---|---|---|---|
| Datasets ([dataset_routes.py](../../server/backend/src/mascope_backend/api/routes/dataset/dataset_routes.py)) | `POST /api/workspaces/{ws}/datasets`, `PATCH/DELETE .../{id}`, `POST .../{id}/move` | workspace editor (move: editor on both workspaces); a locked dataset needs global admin to update, delete or move | no |
| Batches ([sample_batches_routes.py](../../server/backend/src/mascope_backend/api/routes/sample/batches/sample_batches_routes.py)) | `POST /api/sample/batches`, `PATCH /{id}`, `PATCH /status`, `POST /{id}/import`, `POST /{id}/copy`, `DELETE /{id}`; background jobs `POST /{id}/peaks` (batch-peak aggregation), `POST /{id}/export_peaks` and `POST /{id}/export/spreadsheet` ([batches/export/routes.py](../../server/backend/src/mascope_backend/api/routes/sample/batches/export/routes.py), included in this router) | dataset editor to create; batch editor otherwise, the background jobs included (status: editor on every listed batch; copy: also editor on the destination dataset); locked: update needs global admin, delete needs owner, import into it and copy into a locked dataset are refused | no |
| Samples ([sample_items_routes.py](../../server/backend/src/mascope_backend/api/routes/sample/items/sample_items_routes.py)) | `POST /api/sample/items`, `PATCH /{id}`, `POST /move`, `/copy`, `/delete`, `/process`, `/{id}/export_peak_data`, `DELETE /{id}` | editor (copy: **guest** on source, editor on destination batch; move: editor on both); locked: update and delete need owner, move refuses a locked source item or destination, copy a locked destination | no |
| Target collections ([target_collections_routes.py](../../server/backend/src/mascope_backend/api/routes/target/collections/target_collections_routes.py)) | `POST/PATCH/DELETE /api/target/collections[/{id}]`; association via the `sample_batch_ids` payload field | workspace editor (global collection: global admin or superuser); batches-only update: guest on collection + editor on changed batches | no |
| Target compounds ([target_compounds_routes.py](../../server/backend/src/mascope_backend/api/routes/target/compounds/target_compounds_routes.py)) | `POST/PATCH /api/target/compounds`, `DELETE /{id}` | **global editor only — no workspace ACL** | no |
| Matching ([aggregate/](../../server/backend/src/mascope_backend/api/routes/match/aggregate/), [match_routes.py](../../server/backend/src/mascope_backend/api/routes/match/match_routes.py)) | `POST /api/match/aggregate/{sample\|batch}/{id}` and its previews (sample: `/isotope`, `/ion`, `/compound(s)`; batch: `/isotope` only), +`/save` (`/resave` batch-only); `/api/match/{rematch,compute,remove}/...`; `/remove/all`; the direct match-row writers `POST`/`DELETE /api/match/{isotopes,ions,compounds,collections,samples}` ([match/*/match_*_routes.py](../../server/backend/src/mascope_backend/api/routes/match/)) | aggregate: guest (pure); save/resave: editor (save upserts, resave rebuilds); rematch/compute/remove: editor on the scope; remove/all: global admin; direct writers: editor on every sample written (`DELETE`: on the sample or batch) | sample `/compound(s)` previews and **`POST /rematch/sample/{id}`** only |
| Peak assignments ([peak_assignments/routes.py](../../server/backend/src/mascope_backend/api/new/peak_assignments/routes.py)) | `POST /api/peak-assignments/sample/{id}/verify`, `PATCH .../assignment/{id}`, `POST .../assign`, `POST .../runs/import`, `DELETE .../runs/{id}`, `POST /calibration/{instrument}/recalibrate`, `POST .../fit/{aggregate,visualize}` | sample editor (recalibrate: superuser; fits: guest, compute only); behind the peak-assignment feature flag, except the two fits | **yes**, all |
| Batch ledger ([batch_peaks_routes.py](../../server/backend/src/mascope_backend/api/new/peak_assignments/batch_peaks_routes.py)) | `POST /api/batch-peaks/batch/{id}/{backfill,search-untargeted,verify,retract,curate,release-curation,export,runs/import}` | batch editor (export: guest, writes a CSV to the user's temp store); behind the feature flag, except export, which stays open so a ledger outlives an opt-out | **yes**, these eight; the router's `POST /records/series` is a guest query and takes no token |
| Sample files ([sample_files_routes.py](../../server/backend/src/mascope_backend/api/routes/sample/files/sample_files_routes.py)) | `POST /api/sample/files`, `POST /upload`, tus `/upload/tus[/{id}]`, `POST /delete`; `PATCH/DELETE /{id}`, `POST /{id}/peaks/compute`, `/{id}/process`, `/bind`, `/reprocess` | upload and create: editor in the instrument's workspace; `process` and `bind`: instrument editor; bulk delete, `PATCH`/`DELETE /{id}`, `peaks/compute` and `reprocess`: instrument **admin** | upload, create and bulk delete only |

**The data-organisation rows (datasets to matching `/save`) accept no
access-token auth** (`token_access=True` absent); the token-reachable writes are
the rematch route and the assignment and upload surfaces (section 1.3).

Left out on purpose, as edits to configuration or reference data rather than
to the data an agent organises: `PATCH /api/target/ions/{id}`, the
`/api/calibration/mz_*` POSTs, `POST /api/match_ratings`, and the
workspace-setup routes (workspaces and members, ionization mechanisms and
modes, attribute templates, acquisition datasets, and instrument configs,
whose create takes a token, 1.3). The write-route table of phase 0's token
audit (section 4) should still list them.
