`ULTRA TRACE MASCOPE - DESIGN DOC - SDK x PEAK-CENTRIC ASSIGNMENT`

# Making the SDK peak-assignment aware (read-first)

## Purpose

The [peak-centric assignment paradigm](peak_assignment_paradigm.md) inverts Mascope
from **target-anchored matching** to **peak-anchored assignment**: a composition is
assigned to *every* observed peak, database-known first (Stage A) then untargeted
(Stage B), arbitrated to a single owner per peak, and filed into a confidence
**tier**. Results persist as `PeakAssignmentRun` + `PeakAssignment` and are exposed
under `/api/peak-assignments/*` (see the epic's
[routes.py](../../server/backend/src/mascope_backend/api/new/peak_assignments/routes.py)).

Every user-facing surface has been reworked around this - the backend engine, the
Sample view, the assignment ledger, the inspector - **except the public Python SDK**
(`libraries/sdk`, `mascope_sdk`), which at the time of writing was entirely
targeted-shaped and knew nothing about peak assignments. This document closes that
gap in two phases: **v1** (read, shipped - §§1-7) and **v2** (write - §8).

It is an engineering design + phased plan, not a user guide.

> **Status (2026-08-03).** The one backend change this plan called for - dropping the
> dead `run` field from the ledger read (§4.1) - has **landed** on the epic branch (PR
> [#1696](https://github.com/ultra-trace-systems/mascope/pull/1696), commit `ee6217a9`). Since then
> the ledger read also gained **pagination** (`limit`/`offset` + `total`) and typed enum
> filters ([`f1e17f2c`]), and issue [#1725](https://github.com/ultra-trace-systems/mascope/issues/1725)
> will move `alternatives`/`provenance` out of the list rows into a per-peak detail
> fetch. This revision folds those in.

> **Status (2026-08-19). v1 has shipped** (issue
> [#1737](https://github.com/ultra-trace-systems/mascope/issues/1737), PR
> [#1865](https://github.com/ultra-trace-systems/mascope/pull/1865)): the SDK gained the
> `peak_assignments` resource (`get` + `list_runs` + `detail`), the
> `load_assignments` loader, hermetic unit tests plus contract tests (which
> **skip** until the demo bundle carries a completed run - §7's demo-dataset
> task is still open, now sequenced as §8.4 step 6), and a tutorial notebook,
> `10_peak_assignment.ipynb` (shipped briefly as `11`; the retirement of the
> client-side composition notebook, below, restored the planned slot).
> [#1725](https://github.com/ultra-trace-systems/mascope/issues/1725)
> landed first, so `get()` reads the slim ledger rows (with the flattened
> `p_correct` / `p_correct_provisional` / `corroboration_adducts` scalars) and
> the open decision in §9.5 resolved to a separate `detail(sample_id,
> peak_assignment_id)` accessor over the per-assignment detail route (it takes
> the sample id too, matching the route shape).

> **Status (v2 design). §8 is no longer a deferred sketch.** It is now the
> write-side **contract**: importing externally computed runs into the Mascope
> ledger (§8.2, a new backend endpoint), plus the v1-deferred SDK triggering,
> verification, and fit wrappers (§8.3). The old open question "should the SDK
> trigger runs?" (§9.1) is resolved **yes** - §8.3 records why the ground
> shifted. Decisions in §8 are settled unless marked otherwise; the genuinely
> open points are collected in §8.5.

> **Scope decision (v1): read-only.** v1 exposes only *reading* persisted assignment
> results. It does **not** trigger assignment runs or write verifications from the
> SDK; whether the SDK should launch runs at all was an open question at the time
> (runs are heavy, async background jobs and were launched only from the app). That
> question is now settled - see §8.3 - and the write surface is specified in §8.

> **Coexistence, not replacement.** Targeted matching stays a first-class SDK path.
> Peak assignment is added *alongside* it, not in place of it. This mirrors the
> paradigm doc's own "coexist" stance (a targeted result is just an assignment whose
> winner came from the curated library) and deliberately keeps user adaptation
> friction low.

---

## 1. Current SDK (as-is): targeted-shaped

Everything the SDK offers today is anchored on the **target list**:

- `mascope.matching.match_compound(s)` -
  [matching.py](../../libraries/sdk/src/mascope_sdk/resources/matching.py) - matches a
  sample against supplied target formulas (`/match/aggregate/sample/{id}/compounds`).
- `mascope.cheminfo.query_by_mz` -
  [cheminfo.py](../../libraries/sdk/src/mascope_sdk/resources/cheminfo.py) - per-m/z
  candidate formulas (`/cheminfo/mz/query`), on-demand, not persisted.
- `mascope.samples.get_peaks` -
  [samples.py](../../libraries/sdk/src/mascope_sdk/resources/samples.py) - peaks
  flattened with `target_*` match columns (`match_score_isotope/ion/compound`,
  `target_compound_*`, `target_ion_*`, `target_isotope_*`). A peak with no target is
  a single row with NaN match columns - it is never explained.
- `mascope.load_peaks` / `load_peak_timeseries` / `load_peaks_by_stage` - high-level
  loaders over the same targeted peak data.

And the tell-tale one, the since-retired `09_composition_assignment.ipynb`:
it hand-rolled untargeted assignment **client-side** - for each unmatched peak
it called `cheminfo.query_by_mz` -> scored each candidate with
`matching.match_compounds` -> picked the best -> marked isotope siblings. This
was *exactly* Stage B, but with **no arbitration, no plausibility, no tiers,
no calibration, and no persistence** - a per-notebook reimplementation of what
the engine now does properly server-side. With the read surface shipped, the
notebook was removed rather than kept as a fallback: two paths to the same
goal with different scores confuse more than they help.

**Nothing in the SDK references `/api/peak-assignments/*`.**

---

## 2. What "keep the two in parallel" means for the SDK

| Capability | Path | Status |
| --- | --- | --- |
| Targeted matching against a list | `matching.*`, `get_peaks` target cols | **keep unchanged** |
| Manual client-side untargeted loop | (retired notebook) | **removed** - superseded by the read surface (§6) |
| Read server-side peak assignments | *new* `peak_assignments.*` | **shipped (v1**, PR [#1865](https://github.com/ultra-trace-systems/mascope/pull/1865)**)** |
| Trigger assignment runs / verify / fit | *new* write wrappers | **v2** (§8.3) |
| Publish an externally computed run | *new* `runs/import` + `import_run` | **v2** (§8.2) |

The two paradigms coexist at the API level too: a `PeakAssignment` with
`target_compound_id IS NOT NULL` (source `database`, Stage A) *is* the targeted result,
now peak-anchored. So "targeted vs. peak-centric" in the SDK is not two engines - it is
the targeted resources (list-driven) beside a peak-assignment resource (peak-driven read
of a persisted run).

---

## 3. The API surface the SDK reads and writes

All under prefix `/api/peak-assignments`, token-accessible (SDK-reachable):

| Endpoint | SDK | Purpose |
| --- | --- | --- |
| `GET /sample/{id}` | **v1** | peaks-with-assignments (one row/peak); **paginated** (`limit` default 1000 / max 5000, `offset`; response carries `total`); filters `peak_assignment_run_id`, `tier`, `role`, `source` (typed enums - a bad value is a 422). Envelope standardized per §4.1 |
| `GET /sample/{id}/assignment/{assignment_id}` | **v1** | one assignment in full (`alternatives` + `provenance`), the detail fetch #1725 split out of the list |
| `GET /sample/{id}/runs` | **v1** | run history (status, engine_version, config), newest first |
| `GET /sample/{id}/verifications` | **v2** (§8.3) | recorded verdicts (read-only) |
| `POST /sample/{id}/assign` | **v2** (§8.3) | 202 launch run (async) |
| `POST /batch/{id}/assign` | **v2** (§8.3) | 202 launch run for a whole batch |
| `POST /sample/{id}/runs/import` | **v2** (§8.2) | **new endpoint**: publish an externally computed run |
| `POST /sample/{id}/fit/aggregate` | **v2** (§8.3) | isotope table for an arbitrary formula+adduct (POST, but non-mutating - verify an untargeted winner) |
| `POST /sample/{id}/fit/visualize` | optional | emits socket events, which a headless client cannot receive; wrapped only if a use appears (§8.3) |
| `POST /sample/{id}/verify` | **v2** (§8.3) | record a verdict (write) |
| `POST /calibration/{instrument}/recalibrate` | **never (SDK)** | superuser admin op |

Reference for the record shapes:
[schemas.py](../../server/backend/src/mascope_backend/api/new/peak_assignments/schemas.py),
[config.py](../../server/backend/src/mascope_backend/api/new/peak_assignments/config.py).

The response envelope is `{status, message, results, total, data}`: `results` is the
size of *this page*, `total` is the row count across all pages (how a client knows paging
is done).

**`PeakAssignmentRecord`** columns the ledger DataFrame carries:
`peak_assignment_id`, `peak_assignment_run_id`, `sample_peak_id`,
`sample_peak_mz/_intensity/_tof`, `role`
(`M0`/`iso_child`/`reagent`/`artifact`/`unassigned`), `assigned_formula`, `ion_formula`,
`ionization_mechanism_id`, `isotope_label`, `isotope_formula`, `source`
(`database`/`untargeted`), `fit_score`, `mz_error_ppm`, `abundance_error`, `tier`
(`identified`/`candidate`/`below_assignability`/`unassigned`), `target_compound_id`,
`target_ion_id`, `owner_peak_assignment_id`, plus the flattened provenance scalars
`p_correct` / `p_correct_provisional` / `corroboration_adducts`. The `alternatives`
(JSON list) and `provenance` (JSON) blobs live on the per-assignment detail record:

> **Landed (#1725).** `alternatives` + `provenance` are ~74% of the payload (2.8 KB/row
> vs 0.74 KB core) and are inspector-only. [#1725](https://github.com/ultra-trace-systems/mascope/issues/1725)
> dropped them from the list response and serves them via the per-assignment **detail**
> route (with the `p_correct` / `p_correct_provisional` / `corroboration_adducts`
> provenance scalars flattened onto the slim rows). The SDK fetches **core rows** in
> `get()` and the JSON detail via `detail()` (§5.1, §9).

**`PeakAssignmentRunRecord`** (run metadata): `peak_assignment_run_id`, `engine_version`,
`status` (`pending`->`running`-> terminal `completed`|`failed`|`cancelled` -
`cancelled` is terminal like `failed` but distinguishes an interrupted run from an
engine error), `config`, `error`,
`peak_assignment_run_utc_created/_completed`. Reads default to the **latest completed**
run when no run id is given (multiple completed runs can exist per sample; a run is
refused only while another for the same sample is *in flight* - concurrency admission
control, not a one-run-per-sample model).

---

## 4. Integration constraints

1. **Sequencing.** `/api/peak-assignments/*` ships in PR
   [#1696](https://github.com/ultra-trace-systems/mascope/pull/1696) (`epic/peak-centric-assignment`
   -> `develop`), not yet on a released `develop`. The SDK compat work must sit on top of
   the epic (or land after #1696 merges); it cannot be exercised against a plain,
   pre-epic `develop` demo stack. The SDK **contract tests** (§7) need a stack whose
   backend has the endpoints *and* at least one persisted run.

2. **The ledger read is paginated.** `GET /sample/{id}` returns at most `limit` rows
   (default 1000, max 5000) from `offset`, and reports `total` (all matching rows). A
   dense sample is tens of thousands of peaks, so the SDK **must page** - loop `offset`
   until it has `total` rows - not assume one response is the whole run. Ordering is
   stable (m/z with the primary key as tiebreak), so paging never drops or repeats a row.
   Filters `tier`/`role`/`source` are typed enums server-side: a misspelled value is a
   **422** naming the accepted set, not a silent empty page.

3. **Async is why v1 is read-only.** `POST .../assign` returns **202** + a `Process-ID`
   header and finishes in the background (the app learns completion via a socket reload
   event). The SDK has no polling/wait/header plumbing today. Reading avoids all of it;
   triggering (§8.3) takes on a poll-`GET /runs`-until-terminal loop plus `Process-ID`
   header exposure in `_http`.

### 4.1 Landed: dropped the `run` field from `GET /sample/{id}`

> **Shipped** as commit `ee6217a9` in PR
> [#1696](https://github.com/ultra-trace-systems/mascope/pull/1696). Kept here for the rationale.

The cleanest fit with the existing API was **not** to nest `run` into `data`, but to
**remove `run` from the `GET /sample/{id}` response entirely**, standardizing it on the
universal list envelope:

```jsonc
// now
{ "status", "message", "results", "run": {...}, "data": [ ...peaks... ] }
// proposed
{ "status", "message", "results", "data": [ ...peaks... ] }
```

**Why removal, and why it's safe** - three findings from the shipped code:

- **It is the only anomaly.** `PeakAssignmentsResponse` is the *sole* data-bearing
  response in the entire `api/new` surface that carries a field beside `data`.
  Match-records, the runs list, and the verifications list are all
  `{status, message, results, data}`
  ([match/records/schemas.py](../../server/backend/src/mascope_backend/api/new/match/records/schemas.py),
  [peak_assignments/schemas.py](../../server/backend/src/mascope_backend/api/new/peak_assignments/schemas.py)).

- **No client can read it, and the shipped one doesn't.** Both the frontend's shared
  `read` handler and the SDK's `_get` unwrap the response to `.data`, so a sibling
  `run` is invisible to both. The frontend's assignment store says so in a comment and
  takes run metadata from a **separate `/runs` store** instead
  ([peakAssignment/assignment.js](../../server/frontend/src/stores/data/modules/peakAssignment/assignment.js):
  *"The shared `read` handler unwraps the response to its `data` field, dropping the
  {run, data} envelope's `run`, so we take run metadata from the run store instead"*).
  So `run` is already **dead weight** - produced only in
  [service.py](../../server/backend/src/mascope_backend/api/new/peak_assignments/service.py)
  and read by nobody. No test asserts on it.

- **Nothing is lost.** Run metadata is already a first-class resource -
  `GET /sample/{id}/runs` - and the run identity is denormalized onto every assignment
  row (`peak_assignment_run_id`). "Which run did I get?" is answerable from the rows;
  "what was its config / engine version?" from `/runs`.

**Why not nest it as `data: {run, assignments}` (the literal "merge into data")?** That
would change `data` from a list to an object, which *breaks* the frontend's `useData`
list handler (it keys `data.list` by `sample_peak_id`, expecting an array) and
complicates the SDK's DataFrame construction - so it *increases* coupling and churn,
the opposite of the goal. Removal is strictly cleaner than nesting.

**Blast radius (as landed):** backend only, and small - dropped the field from
`PeakAssignmentsResponse` and the two `"run": ...` keys in
`service.get_peak_assignments`, plus two integration-test assertions that now read run
identity from the rows. **Zero frontend change** (it already ignored the field). The SDK
design below assumes the standardized envelope.

*(This does mean the SDK resolves "which run" itself - see §5.1 - exactly as the
frontend already does. Cheap, one extra GET, and it makes `get()` deterministic.)*

---

## 5. Proposed SDK design (v1)

### 5.1 New resource: `mascope.peak_assignments`

New `libraries/sdk/src/mascope_sdk/resources/peak_assignments.py`, a `BaseResource`
subclass, wired as a lazy property on `MascopeClient` (mirroring `matching`/`cheminfo`:
private slot, `@property`, `TYPE_CHECKING` import in
[client.py](../../libraries/sdk/src/mascope_sdk/client.py)).

```python
mascope.peak_assignments.get(
    sample_id,
    *,
    run_id=None,        # default: latest completed run
    tier=None,          # identified | candidate | below_assignability | unassigned
    role=None,          # M0 | iso_child | reagent | artifact | unassigned
    source=None,        # database | untargeted
) -> pd.DataFrame | None

mascope.peak_assignments.list_runs(sample_id) -> pd.DataFrame | None
```

**Return shape (decided):** `get()` returns a **DataFrame, one row per peak** for the
whole run, with the run metadata attached on **`df.attrs["run"]`** (a dict). This keeps
the SDK's "everything is a DataFrame" convention while still surfacing the run context.
`list_runs()` returns a plain runs DataFrame. Empty result -> `None` (matching
`get_peaks` semantics).

**Paging is internal.** The endpoint caps a response at `limit` rows (§4 constraint 2),
so `get()` loops `offset` accumulating pages until it has `total` rows, then
concatenates - the caller gets the entire run in one DataFrame and never sees a page
boundary. (An advanced `limit`/`offset` passthrough for streaming very large runs is a
later option; the default is fetch-all.) A misspelled `tier`/`role`/`source` surfaces as
the SDK's `ValidationError` (the server's 422), not a silently empty frame.

**Resolving the run.** Run identity is no longer echoed in the response (§4.1), so `get()`
sources run metadata the way the frontend does: when `run_id is None`, it calls
`list_runs`, picks the latest `completed` run, uses its id for the assignments fetch, and
attaches that run record to `df.attrs["run"]`. This makes the call deterministic (no
reliance on server-side "latest" resolution the caller can't see) at the cost of one
cheap extra GET, and the standardized envelope unwraps through the ordinary `_get` -
**no `_base.py`/`_http.py` change is needed**.

**`alternatives` / `provenance`.** [#1725](https://github.com/ultra-trace-systems/mascope/issues/1725)
slimmed them out of the list response, so the SDK sources them on demand via the per-peak
detail accessor - shipped as `peak_assignments.detail(sample_id, peak_assignment_id)`,
taking the sample id too to match the route shape - and the bulk `get()` stays cheap.
`get()` is **core-first**, which made #1725 an additive detail method rather than a
breaking column change (§9).

Datetime columns (`*_utc_*`) coerced via the existing `_coerce_datetime_columns`.

### 5.2 High-level loader: `load_assignments`

Parity with `load_peaks` - a `client.py` method delegating to `_loaders.py`:

```python
mascope.load_assignments(
    dataset, batches=None, *, samples=None, exact=False,
    run="latest", tier=None, source=None,
    confirm_above=100, max_workers=8,
) -> pd.DataFrame | None
```

Resolve dataset/batches/samples (reuse the existing resolution + `run_concurrent` +
tqdm + `confirm_above` machinery), call `peak_assignments.get` per sample, and
concatenate with `sample_batch_name` / `sample_item_name` / `datetime_utc` prepended -
exactly the shape `load_peaks` produces. This is the natural cross-sample analysis entry
point (tiered mass-defect, Van Krevelen, source breakdowns).

Because it only *reads*, `load_assignments` returns whatever runs already exist; samples
without a completed run contribute nothing (and are logged), rather than being assigned
on the fly. Each per-sample fetch pages internally (§5.1); across many samples payload
size matters, so `load_assignments` pulls **core rows only** and never the
inspector-detail JSON (aligned with #1725).

### 5.3 No changes to existing resources or shared SDK code

`matching`, `cheminfo`, `samples.get_peaks`, and the three existing loaders are
untouched. With the §4.1 cleanup already landed, the standardized envelope unwraps
through the existing `_get`, so the new resource needs **no change to `_base.py` or
`_http.py`** - it is purely additive on the SDK side.

---

## 6. Notebooks (the user-facing parallel story)

- **`10_peak_assignment.ipynb`** (shipped) - read + analyze a persisted run: pick a
  sample, `peak_assignments.list_runs` -> `peak_assignments.get`, inspect the `run`
  config on `.attrs`, filter by `tier` / `source`, plot a **tier-colored mass-defect**
  map and a **Van Krevelen**, break peaks down by `source` (database vs untargeted),
  and drill into `alternatives` for contested peaks. Assumes a run already exists
  (created in the app) - explicitly noted, since v1 doesn't trigger runs.
- **`09_composition_assignment.ipynb` retired** (reversing this doc's earlier
  "keep as client-side fallback" call): with the read surface shipped, a second
  hand-rolled path to the same goal - different scores, no ledger, no tiers -
  confuses more than it helps. `10_batch_stages.ipynb` moved into the `09` slot.
- **`01`-`08` unchanged** - they are the targeted workflow that coexists.
- Update the README notebook table + [`docs/user/sdk/index.md`](../user/sdk/index.md).

---

## 7. Tests & docs

- **Contract tests** (`libraries/sdk/tests/test_contract.py`): add ledger/run shape
  assertions - `list_runs` returns a run with `status == "completed"` and an
  `engine_version`; `get` returns one row per peak with the `tier`/`source`/`fit_score`
  columns and a populated `df.attrs["run"]`. Precondition: the **demo bundle must carry
  a completed run** (analogous to how it already stamps `match_score_version`), or the
  test provisions one out of band. Flag this as a demo-dataset task - it is the main new
  test-infra dependency, and it gates the epic's own e2e demo stack too. (Sequenced as
  §8.4 step 6: the shipped contract tests skip in CI until it lands.)
- **Unit tests**: the **paging loop** (accumulate pages to `total`, stable order),
  latest-completed run resolution + `.attrs` attachment, enum-filter 422 ->
  `ValidationError`, empty-run -> `None`. Hermetic (mock `http_get`), like
  `test_loaders.py`.
- **Docs**: README - new `#### mascope.peak_assignments` reference table, a
  "Peak assignments" section, project-structure entry, notebook table row; the SDK
  section of `developer_guide.md`; CHANGELOG + SDK version bump.

---

## 8. The write surface (v2): publish, trigger, verify

v1 shipped read-only (PR [#1865](https://github.com/ultra-trace-systems/mascope/pull/1865)).
v2 adds the writes - and its centre of gravity is not "the SDK can press the
assign button". It is **run import**: making Mascope's run ledger the canonical
store for assignment runs computed *outside* Mascope. Everything in this
section is decided unless the text says otherwise; the genuinely open points
are collected in §8.5.

### 8.1 Purpose: closing the peaky loop

peaky ([peak_assignment_paradigm.md](peak_assignment_paradigm.md) §2) is the
external, SDK-powered assignment research tool - its own repository, a
research-grade multi-pass engine built on the same `mascope_tools` scoring the
in-app engine uses. It reads peaks through this SDK and writes its ledgers as
**local files**. That last step is the gap: a peaky run's result is exactly the
shape of a `PeakAssignmentRun` plus its `PeakAssignment` rows, but it lives in
one researcher's filesystem, and sharing or managing those files is that
researcher's problem. Meanwhile Mascope has grown everything the files lack: a
persisted, retention-managed run ledger, a run selector, per-peak inspection,
verification capture, and the batch-peaks overview that folds per-sample runs
into cross-sample trends.

v2 closes the loop: **peaky publishes runs into Mascope**. The SDK that already
carries peaks out (`get_peaks`) carries finished ledgers back in, and the run
ledger becomes the canonical shared store for both engines:

```
Mascope peaks --(SDK read)--> peaky computes ledger --(SDK import)--> Mascope run ledger
                                                                        -> app run selector / inspector
                                                                        -> batch Assignments overview
                                                                        -> verification loop
```

The two engines stay what they are - the in-app engine the productized default,
peaky the research surface (the "harvest, don't depend" stance of the paradigm
doc §5.2 is unchanged). What changes is that their *results* share one store,
one read API, one UI, and one retention policy.

### 8.2 Part A - run import (the core)

#### Endpoint

**`POST /api/peak-assignments/sample/{sample_item_id}/runs/import`** accepts a
complete, externally computed assignment run for one sample.

Gating and admission are **identical to the other writes** (decided):

- `require_peak_assignment_enabled` - 403 while the `peak_assignment` flag is
  off, exactly like assign/verify/recalibrate. An opted-out deployment cannot
  accumulate imported ledgers any more than in-app ones.
- **Editor role** on the workspace (`require_sample_role("editor")`), the same
  role that launches an in-app run.
- The **advisory-lock admission claim** (`assignment_claim("sample", id)`,
  [admission.py](../../server/backend/src/mascope_backend/api/new/peak_assignments/admission.py)):
  one assignment *or import* per sample at a time, refused - not queued -
  across every worker sharing the database.

Request, top level:

| field | meaning |
| --- | --- |
| `engine` | external engine name (e.g. `"peaky"`), stamped on the run - see the `engine` column below |
| `engine_version` | the external engine's version string (the existing `engine_version` column) |
| `config` | the engine's full run configuration, **opaque JSON**, stored verbatim on the run - an imported run is as reproducible as an in-app one, on the engine's own terms |
| `calibration` | the client's calibration state, **required** - see the trust model below |
| `rows` | assignment rows (below) |
| `complete` | finalize marker for chunked imports (below) |

Each row is the `PeakAssignment` shape the read side already serves:
`sample_peak_id`, denormalized `sample_peak_mz` / `_intensity` / `_tof`,
`role`, `assigned_formula`, `ion_formula`, `ionization_mechanism_id`
(**nullable** - an external engine names adducts by notation, not by a
deployment's mechanism ids; see §8.5.2), `isotope_label`, `source`,
`fit_score`, `mz_error_ppm`, `abundance_error`, `tier`, an owner reference
(below), `alternatives` (JSON), `provenance` (JSON). `sample_item_id`,
`peak_assignment_id`, and `peak_assignment_run_id` are server-minted, never
client-supplied.

**Owner linkage is a client-side row reference resolved server-side.** The
client cannot supply `owner_peak_assignment_id` - those ids do not exist until
the server mints them - so an `iso_child` row references its owner as
`owner_sample_peak_id`: the owner row's `sample_peak_id`, which identifies a
row uniquely within the import because a run holds at most one row per peak. At
finalize the server resolves every reference to the minted
`owner_peak_assignment_id` (inserting owners before children, as the in-app
persist already does); an unresolvable reference - no such row in the import -
is a 422.

**One logical import, one or more requests.** A dense sample's full ledger with
`alternatives`/`provenance` is tens of thousands of rows at ~2.8 KB each - too
large for one request body, and an unbounded row list would serialize unbounded
work through Pydantic on the event loop (the same reasoning that paginated the
ledger *read*). So the endpoint caps `rows` per request and supports assembly:

- the first request creates the run (`status='running'`, engine + config +
  calibration stamped) and returns its `peak_assignment_run_id`;
- follow-up requests reference that run id and append rows;
- the request with `complete: true` - which may be the first and only one, the
  common case for slim ledgers - finalizes: payload-wide validation, owner
  resolution, `status='completed'`, batch fold-in.

The lifecycle needs no new machinery: the read model serves only `completed`
runs, so a half-imported run is invisible; an abandoned import is a stale
`running` run, exactly what the startup reaper
(`reset_running_peak_assignment_runs`) and the retention prune's
`keep_running_hours` grace already reclaim.

#### Attribution: the `engine` run column

One **additive** schema change: a nullable `engine` column on
`peak_assignment_run`. NULL reads as the in-app engine, so existing rows need
no backfill and the in-app write path does not change. Imports must stamp it.
The migration parents on the **current Alembic head, `c3a9e6f2b8d1`**
(`20260818_c3a9e6f2b8d1_add_mfa_columns.py` - the MFA migration, itself chained
onto the batch-peak head `f3b9c7a1e2d4`).

Imported runs are **first-class but always attributable**: same tables, same
read model, same retention, same fold-in - and the app's run selector shows
engine provenance (`engine` + `engine_version`, plus the calibration badge
below), so a user reading a ledger knows which engine produced it before
trusting a tier. `PeakAssignmentRunRecord` gains the `engine` field and the
SDK's `list_runs` frame carries it; rows read identically regardless of engine.

#### Validation (strict-lite)

The bar: reject what would corrupt the read model; accept what is merely the
importer's judgement.

- **Single owner per peak** - enforced server-side. At most one row per
  `sample_peak_id`: a duplicate within the payload is a 422, and the existing
  unique constraint `(peak_assignment_run_id, sample_peak_id)` backstops it at
  insert. This is the ledger invariant every consumer assumes.
- **Enum validity.** `role` / `tier` / `source` are validated against the same
  typed vocabularies the read filters use (§3); a bad value is a 422 naming
  the accepted set. **Tiers need no mapping**: the app's tier vocabulary
  (`identified` / `candidate` / `below_assignability` / `unassigned`) and its
  fit-score scale came *from* peaky, and both engines score with
  `mascope_tools` `score_pattern` - compatibility is by construction, not by
  translation ([peak_assignment_paradigm.md](peak_assignment_paradigm.md) §2,
  [fit_score.md](../../libraries/tools/docs/fit_score.md)).
- **Peak existence.** Every `sample_peak_id` must exist in the sample's peak
  file (one `extract_peaks` load per import - the same read the in-app engine
  does); a row for a peak the sample does not have is a 422. The denormalized
  mz/intensity/tof are stored as supplied: they are the importer's observed
  values, display-denormalized exactly as on `MatchIsotope`.
- **Row-count cap** per request (above). The total is bounded by construction:
  at most one row per peak the sample actually has.
- **Completeness NOT required.** An imported run may cover a subset of the
  sample's peaks. The in-app engine persists a complete ledger (unassigned
  rows included) because completeness is *its* contract; for an import,
  whether to fill unexplained peaks with `unassigned` rows is **the importer's
  choice**. Consumers already tolerate this: the read model serves whatever
  rows exist, and the batch fold-in folds whatever rows exist.

#### Trust model

- **Append-only.** An import always creates a *new* run; it never touches an
  app-computed run (or an earlier import). This is the property the in-app
  engine already has - runs are never superseded in place - so nothing an
  import does can destroy in-app results.
- **Retention applies identically.** The nightly prune's
  keep-newest-per-sample policy makes no engine distinction: imported and
  app-computed runs share the per-sample budget (newest completed runs kept,
  `prune_peak_assignment_runs`). The ledger is a store of runs, not an archive
  of every run ever; a published run that must stay current is republished.
- **Default-read consequence.** `get()` and the app's ledger read default to
  the *latest completed* run, whatever its engine, so a fresh import is what a
  reader sees by default. That is the point - published runs are first-class -
  and the engine badge is what keeps it honest.
- **Calibration: bypass, but record** (decided). In-app assignment refuses a
  sample whose m/z calibration is unverified (`ineligible_reason` in
  [service.py](../../server/backend/src/mascope_backend/api/new/peak_assignments/service.py)),
  because the in-app engine's mass errors - and therefore its fit scores and
  tiers - would mean nothing. An import **bypasses that refusal**: peaky does
  its own offset-aware calibration client-side, so the server-side
  verification state does not describe an imported ledger's mass accuracy. The
  flip side is mandatory disclosure: the required `calibration` object (the
  client's calibration method/state, plus the sample's Mascope-side
  verification state at import time) is persisted on the run - under a
  reserved key in the run's `config` JSON, which is already the run's
  reproducibility record, so no second column is needed - and the UI badges
  the run from it. An importer that did no calibration says so on the record.
  (The blank-sample refusal needs no bypass: a blank sample has no peaks, so
  peak-existence validation rejects such an import anyway.)
- **Verifiable like any other.** The verification endpoints do not care which
  engine produced an assignment, so the verification-calibration loop closes
  over published runs too.

#### After import: batch fold-in

Finalizing an import runs `fold_sample_into_batch_peaks`
([batch_peaks_controller.py](../../server/backend/src/mascope_backend/api/new/peak_assignments/batch_peaks_controller.py)),
exactly as an in-app run's completion does, so imported runs feed the batch
**Assignments** overview the moment they land. Failure isolation mirrors the
in-app path: the fold-in is **best-effort** - a fold-in failure is logged and
never fails (or un-completes) the import itself. The fold-in reads the sample's
latest completed run, which by then is the import.

### 8.3 Part B - triggering, verification, fit (the v1 deferrals, decided yes)

v1 deferred these behind one question - "should the SDK trigger heavy async
runs?" (§9.1). **Decided: yes.** The ground shifted after the question was
raised: the write routes are now gated on the `peak_assignment` flag (403 when
the deployment has not opted in), and admission control - the in-process
in-flight guards plus the cross-process advisory-lock claim - **refuses** a
duplicate run server-side rather than queueing it. The hazard behind the
question was an SDK loop cheaply scheduling unbounded heavy compute; that is
now bounded where it must be, on the server, so client discipline is no longer
the safety mechanism. (The config ceilings - `max_untargeted_peaks`, the
`formula_ranges` species cap, `max_alternatives` - bound each run's cost
independently.)

- **`assign(sample_id, *, config=None, wait=True, poll_interval=..., timeout=...)`**
  and **`assign_batch(batch_id, ...)`** - wrap the 202 endpoints. `config`
  mirrors `PeakAssignmentConfig` (`run_untargeted`, `mz_precision_ppm`,
  `formula_ranges`, `max_untargeted_peaks`, `peak_intensity_threshold`,
  `max_alternatives`, `identified_threshold`, `candidate_threshold`). The POST
  returns immediately (202; the process id is in the `Process-ID` response
  header - the one `_http` change this needs, since the shared helpers expose
  only bodies). Completion is observed by **polling `list_runs`** until the
  run created after the call reaches a terminal state - and every terminal
  state exists now: `completed` | `failed` | `cancelled`. `wait=True` blocks
  and returns `get(...)` of the finished run (raising on `failed`, carrying
  the run's `error`); `wait=False` returns a handle (process id, plus the run
  id once visible) for the caller to poll.

  **Refusal surfaces; it never spins.** A sample (or batch) with an assignment
  already in flight is **refused** by admission control server-side. The
  refusal payload (status `skipped` + the in-flight run id) travels over the
  socket notification channel, which a headless client does not have, so
  `assign()` detects it the observable way - no new run appears while a
  pre-existing run sits `pending`/`running` - and surfaces it as an explicit
  already-in-flight outcome carrying the in-flight run id, never as a timeout.
  A batch's server-side skips (blank samples, unverified calibration,
  Stage-A-only default) are documented behavior the wrapper inherits, not
  errors.
- **`verify(sample_id, peak_assignment_id, verdict, *, evidence_level=None, note=None)`**
  and **`list_verifications(sample_id)`** - thin wrappers over the shipped
  verification endpoints (§3): append-only verdicts, `evidence_level` required
  for `confirmed` (server-enforced).
- **`fit_aggregate(sample_id, formula, ionization_mechanism_id)`** - wraps
  `POST .../fit/aggregate`: the isotope table for an arbitrary composition,
  the untargeted analogue of `matching.match_compound`, for verifying a
  Stage-B winner. These endpoints were **deliberately kept as API/SDK
  surface** when the Fit view was retired
  ([#1736](https://github.com/ultra-trace-systems/mascope/issues/1736), PR
  [#1867](https://github.com/ultra-trace-systems/mascope/pull/1867)); their route
  docstrings say so ("API/SDK surface: no in-app view calls this endpoint").
  This wrapper is that surface materializing. Relates to
  [#1004](https://github.com/ultra-trace-systems/mascope/issues/1004) (score an arbitrary
  peak+composition). `fit/visualize` stays optional: its output is socket
  events, which a headless client cannot receive - it earns a wrapper only if
  a concrete use appears.
- **`import_run(sample_id, df, *, engine, engine_version, config, calibration)`**
  - the SDK face of §8.2. Validates the DataFrame client-side (required
  columns, enum values, owner references - fail fast before any bytes move),
  converts NaN to nulls, **chunks** the rows under the endpoint's per-request
  cap (create -> append -> `complete: true`), and returns the new run id.
  The round trip is: `get_peaks` out, compute externally, `import_run` back,
  `get` to confirm.
- **`recalibrate`** - still out of scope for the SDK (superuser admin op).

Corresponding notebook follow-up: rewrite `09` to *drive* the server engine
(`assign` -> `get`) now that triggering exists, demoting the client-side loop
to an appendix.

### 8.4 Sequencing

Each step is its own PR, and each leaves the system shippable:

1. **This document** - the contract.
2. **Backend import** - the `runs/import` endpoint, the `engine` column +
   migration (parented on `c3a9e6f2b8d1`), strict-lite validation, fold-in on
   completion, integration tests (exercising the write via the
   `MASCOPE_PEAK_ASSIGNMENT` env override, like the other write tests).
3. **SDK triggering + verification + fit** - `assign` / `assign_batch` /
   `verify` / `list_verifications` / `fit_aggregate`, the `Process-ID` header
   exposure in `_http`, the polling helper, hermetic unit tests.
4. **SDK `import_run`** + a round-trip tutorial notebook: read peaks, compute
   externally, publish, see the run in the app.
5. **`peaky publish`** - the consumer, in the peaky repository (out of this
   repo's scope; listed so the sequence has its end state).
6. **Demo bundle seeds a completed assignment run** - unblocks the v1
   contract tests that today **skip** in CI (§7), and doubles as the fixture
   an import contract test diffs against.

### 8.5 Open questions (v2)

Genuinely open - everything else in §8 is decided:

1. **Batch-level import.** peaky's batch pipeline merges per-sample ledgers
   into a batch-level result. Should import accept a batch manifest (one call,
   N samples' runs), or stay per-sample in v2? Per-sample is sufficient for
   the loop - the batch fold-in derives the batch view server-side from
   per-sample runs - so a manifest is a convenience with real transactional
   surface.
2. **Unknown ionization mechanisms.** An external engine may assign an adduct
   the deployment has no `IonizationMechanism` row for. Auto-register it on
   import, or leave `ionization_mechanism_id` null (the notation still lives
   in `ion_formula` and provenance)? Null is safe and loses no substance;
   auto-registration keeps the ledger joinable but lets imports grow a shared
   vocabulary table.

---

## 9. Open decisions (v1) - and how they resolved

1. **Should the SDK ever trigger runs?** *Resolved: yes (v2, §8.3).* The
   leaning at the time was read-only, because runs are heavy async jobs and
   client discipline was the only guard. Admission control (cross-process
   advisory-lock refusal of duplicates) and the `peak_assignment` write gating
   both landed after this was written, moving the safety property server-side -
   which is what settles it.
2. **`load_assignments` on samples with no run.** *Resolved: skip-and-log
   (shipped in v1).*
3. **Reference identity surfacing.** Still open. Once the reference x
   peak-centric convergence
   ([reference_peak_assignment_convergence.md](reference_peak_assignment_convergence.md))
   attaches reference identities to `source=database` assignments, decide how the SDK
   exposes them (a `provenance`/`identity` column vs. flattened name columns).
4. **Contract-test data.** *Resolved: seed the demo bundle* (§8.4 step 6); the
   shipped contract tests skip until it lands.
5. **`alternatives` / `provenance` after #1725.** *Resolved: a separate
   `detail(sample_id, peak_assignment_id)` accessor (shipped in v1)* - it takes
   the sample id too, matching the route shape; no `get(..., detail=True)`
   opt-in.

The genuinely open v2 questions live in §8.5.

**Related tracker issues:**
[#1725](https://github.com/ultra-trace-systems/mascope/issues/1725) (ledger response slimming -
reshapes the read),
[#1494](https://github.com/ultra-trace-systems/mascope/issues/1494) (SDK peak dataframe: add target
collection name),
[#1044](https://github.com/ultra-trace-systems/mascope/issues/1044) (batch-level aggregation /
assignment, notebook),
[#1004](https://github.com/ultra-trace-systems/mascope/issues/1004) (score an arbitrary
peak+composition - the `fit_aggregate` wrapper, §8.3),
[#1736](https://github.com/ultra-trace-systems/mascope/issues/1736) (retired the Fit view; the
composition-verify endpoints stay as API/SDK surface - PR
[#1867](https://github.com/ultra-trace-systems/mascope/pull/1867)).

---

## 10. Summary

v1 - deliberately **read-only and additive** - has shipped (PR
[#1865](https://github.com/ultra-trace-systems/mascope/pull/1865)): the `peak_assignments`
resource (`get` + `list_runs` + `detail`, DataFrame with run on `.attrs`, paging
hidden inside `get()`), the high-level `load_assignments` loader, and the
read/analyze notebook `10_peak_assignment.ipynb` - with no shared SDK plumbing
change. Targeted matching stays exactly as it is, so the two paradigms run in
parallel with no adaptation friction; the old client-side untargeted notebook
is retired in favour of the read surface (§6).

v2 (§8) makes the ledger **writable from outside**. Its core is run import:
`POST .../runs/import` accepts a complete externally computed run - engine name
and version, opaque config, one `PeakAssignment`-shaped row per covered peak -
gated exactly like every other write (feature flag, editor role, advisory-lock
admission), validated strict-lite (single owner per peak, typed enums, peak
existence, row cap; completeness deliberately not required), append-only under
the same retention as in-app runs, calibration-refusal-exempt but
calibration-disclosing, and folded into the batch peaks on completion. One
additive `engine` column on `PeakAssignmentRun` (migration parented on the
current head `c3a9e6f2b8d1`) keeps imported runs first-class yet always
attributable. Around it, the v1-deferred wrappers are now decided in:
`assign`/`assign_batch` (202 + poll-to-terminal, server-side refusal surfaced),
`verify`/`list_verifications`, `fit_aggregate` (#1736's deliberately kept
surface), and `import_run` (DataFrame -> chunked publish -> run id). The
sequence (§8.4) lands each piece as its own PR and ends with `peaky publish`
consuming the whole loop and the demo bundle carrying a completed run - which
also un-skips the v1 contract tests.
