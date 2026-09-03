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
> task is still open, now sequenced as §8.4 step 8), and a tutorial notebook,
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
> shifted. Decisions in §8 are settled unless marked otherwise; the open points
> are collected in §8.5. The three that were **decisions pending** - holding
> sequenced steps until answered - are all resolved and shipped in step 2; what
> remains in §8.5 is two **open questions**, which hold nothing.

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
| `GET /sample/{id}` | **v1** | peaks-with-assignments (one row/peak); **paginated** (`limit` default 1000 / max 5000, `offset`; response carries `total`); filters `peak_assignment_run_id`, `tier`, `engine_tier`, `role`, `source` (typed enums - a bad value is a 422) and `tier_disagrees` (bool; rows carrying no `engine_tier` are excluded from both answers). Envelope standardized per §4.1 |
| `GET /sample/{id}/assignment/{assignment_id}` | **v1** | one assignment in full (`alternatives` + `provenance`), the detail fetch #1725 split out of the list |
| `GET /sample/{id}/runs` | **v1** | run history (status, engine_version, config), newest first |
| `GET /sample/{id}/verifications` | **v2** (§8.3) | recorded verdicts (read-only) |
| `POST /sample/{id}/assign` | **v2** (§8.3) | 202 launch run (async) |
| `POST /batch/{id}/assign` | **v2** (§8.3) | 202 launch run for a whole batch |
| `POST /sample/{id}/runs/import` | **v2** (§8.2) | **new endpoint**: publish an externally computed run |
| `DELETE /sample/{id}/runs/{run_id}` | **v2** (§8.2) | **new endpoint**: abandon an `importing` run, releasing the sample before the prune's grace |
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
(`database`/`untargeted`/`manual` - the last for a row a person assigned by hand;
null on a peak nothing explained), `fit_score`, `mz_error_ppm`, `abundance_error`, `tier`
(`assigned`/`candidate`/`below_assignability`/`unassigned`), `engine_tier` (the same
vocabulary or null - the producing engine's own verdict, on imported runs only),
`target_compound_id`,
`target_ion_id`, `owner_peak_assignment_id`, plus the flattened provenance scalars
`evidence` / `p_correct` / `p_correct_provisional` / `corroboration_adducts`. The
`alternatives` (JSON list) and `provenance` (JSON) blobs live on the per-assignment
detail record:

`fit_score` and `tier` are two different quantities and the ledger serves both.
`fit_score` is the pure measurement - how well the isotope envelope matches - and is
unchanged. The `tier` is not read off it: it is read off **`evidence`**, the fit
weighted by the chemical plausibility of `assigned_formula`, which is the quantity
both engine stages already arbitrate a contested peak in. `evidence` is flattened
onto every row (out of `provenance.evidence`) because the tier chip displays it
beside the tier it produced.

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
control, not a one-run-per-sample model). v2 adds `engine` and `calibration` to
this list - to the table *and* to the record, which is a typed projection that
silently drops any column it does not name (§8.2, §8.4 step 4).

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
   event). The SDK has no polling/wait plumbing today. Reading avoids all of
   it; triggering (§8.3) takes on a poll-to-terminal loop over the run id the 202
   returns - which is why §8.3 has the assign endpoints answer synchronously first.

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
    tier=None,          # assigned | candidate | below_assignability | unassigned
    role=None,          # M0 | iso_child | reagent | artifact | unassigned
    source=None,        # database | untargeted | manual
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
  map and a **Van Krevelen**, break peaks down by `source` (database, untargeted,
  and manual - see below), and drill into `alternatives` for contested peaks. Assumes
  a run already exists (created in the app) - explicitly noted, since v1 doesn't
  trigger runs.

  The source breakdown groups **one** frame rather than issuing one `get()` per
  source, and that is load-bearing: `source` grew a third value when manual
  curation landed, and a curated row *leaves* `database`/`untargeted` instead of
  joining them. Two filtered reads would have stopped summing to the run the day
  someone hand-assigned a peak, and the curated rows - the ones a curator is most
  likely to be looking for - would have been the ones missing. A local groupby
  cannot go stale that way, so prefer it wherever a notebook partitions a run by a
  server-side enum.
- **`09_composition_assignment.ipynb` retired** (reversing this doc's earlier
  "keep as client-side fallback" call): with the read surface shipped, a second
  hand-rolled path to the same goal - different scores, no ledger, no tiers -
  confuses more than it helps. `10_batch_stages.ipynb` moved into the `09` slot.
- **`01`-`08` unchanged** - they are the targeted workflow that coexists.
- Update **both** notebook tables, which have to move together:
  [`libraries/sdk/README.md`](../../libraries/sdk/README.md) and
  [`docs/user/sdk/getting-started.md`](../user/sdk/getting-started.md).
  [`docs/user/sdk/index.md`](../user/sdk/index.md) carries no table - it
  deep-links to the README - so it is not the file to edit, and step 6 adds a
  notebook, which is when a stale second table starts costing something.

---

## 7. Tests & docs

- **Contract tests** (`libraries/sdk/tests/test_contract.py`): add ledger/run shape
  assertions - `list_runs` returns a run with `status == "completed"` and an
  `engine_version`; `get` returns one row per peak with the `tier`/`source`/`fit_score`
  columns and a populated `df.attrs["run"]`. Precondition: the **demo bundle must carry
  a completed run** (analogous to how it already stamps `match_score_version`), or the
  test provisions one out of band. Flag this as a demo-dataset task - it is the main new
  test-infra dependency, and it gates the epic's own e2e demo stack too. (Sequenced as
  §8.4 step 8: the shipped contract tests skip in CI until it lands.)
- **Unit tests**: the **paging loop** (accumulate pages to `total`, stable order),
  latest-completed run resolution + `.attrs` attachment, enum-filter 422 ->
  `ValidationError`, empty-run -> `None`. Hermetic (mock `http_get`), like
  `test_loaders.py`.
- **Docs**: README - new `#### mascope.peak_assignments` reference table, a
  "Peak assignments" section, project-structure entry, notebook table row; the
  duplicate notebook table in `docs/user/sdk/getting-started.md`; CHANGELOG +
  SDK version bump. Not `developer_guide.md` - its SDK section is PyPI publish
  mechanics and enumerates no notebooks or resources, so there is nothing there
  to keep in step.

---

## 8. The write surface (v2): publish, trigger, verify

v1 shipped read-only (PR [#1865](https://github.com/ultra-trace-systems/mascope/pull/1865)).
v2 adds the writes - and its centre of gravity is not "the SDK can press the
assign button". It is **run import**: making Mascope's run ledger the canonical
store for assignment runs computed *outside* Mascope. Everything in this
section is decided unless the text says otherwise; the open points are collected
in §8.5, and the three that a sequenced step cannot be built around are marked
there. The three decisions that once held steps are resolved and shipped;
§8.4 closes with what that leaves safe to start.

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

**What an import is trusted with, and what it is not.** An imported ledger is
data a workspace editor asserts, not a computation the server performed. The
line this section draws: an import may say what it *found* - formulas, roles,
fit scores, tiers under declared bands - but it may not write the values the
server presents as *its own* calibrated judgement (the ledger's P(correct)
scalars, "Validation" below), and it may not silently feed the instrument-wide
confidence calibration ("Trust model" below). Most of the rules that follow are
consequences of that line.

### 8.2 Part A - run import (the core)

#### Endpoint

**`POST /api/peak-assignments/sample/{sample_item_id}/runs/import`** accepts a
complete, externally computed assignment run for one sample.

Gating is the same as the other writes; **admission cannot be**:

- `require_peak_assignment_enabled` - 403 while the `peak_assignment` flag is
  off, exactly like assign/verify/recalibrate. An opted-out deployment cannot
  accumulate imported ledgers any more than in-app ones.
- **Editor role** on the workspace (`require_sample_role("editor")`), the same
  role that launches an in-app run. It resolves sample -> batch -> dataset ->
  workspace, so an import cannot reach a sample the caller could not already
  edit.
- **Sample eligibility, stated rather than inherited by accident.** An import is
  refused for a **blank sample** on the rule the in-app path uses -
  `sample.instrument_function_id is None` (`ineligible_reason` in
  [service.py](../../server/backend/src/mascope_backend/api/new/peak_assignments/service.py)) -
  and for the same reason: a blank carries no measured peaks to assign. Do
  **not** let peak-existence validation produce this refusal indirectly; that
  argument fails for a zero-row payload, and "a blank has no peaks" is an
  ingestion consequence (`_process_as_blank` writes an empty peak timeseries),
  not an invariant anything enforces. The **calibration** clause of
  `ineligible_reason` is the one an import deliberately bypasses - see the trust
  model.
- **At least one row** across the whole import. A run with no rows is a 422, not
  a completed empty run. Completeness is not required (below), but emptiness is
  not a ledger.
- **Admission is derived from durable run state, not the advisory claim.**
  `assignment_claim("sample", id)`
  ([admission.py](../../server/backend/src/mascope_backend/api/new/peak_assignments/admission.py))
  is a *session-level* Postgres advisory lock pinned to one connection for the
  lifetime of one in-process task. An import spans several HTTP requests at the
  client's pace, possibly on different workers, so that claim **cannot** span
  one: held across requests it would pin a pooled connection to a remote
  client's think-time; taken per request it leaves the sample unclaimed between
  chunks. Import admission is therefore a query on run state - a non-terminal
  (`pending`/`running`/`importing`) run for the sample refuses a new import or
  in-app assign with **409** naming the in-flight run id - with the advisory
  claim kept only as the cross-worker guard *within* a single request. This is
  genuinely new
  machinery (an indexed status query, plus the in-app assign path adopting the
  same check so the two paths refuse each other); §8.4 step 2 carries it.

Request, top level:

| field | meaning |
| --- | --- |
| `engine` | external engine name (e.g. `"peaky"`), stamped on the run - see the `engine` column below. Reserved values are rejected (422) |
| `engine_version` | the external engine's version string (the existing `engine_version` column, `String(64)`) |
| `config` | the engine's full run configuration, **opaque JSON**, stored **verbatim** - the server never reads it and never writes into it. Size-capped (below) |
| `tier_bands` | the `assigned` / `candidate` thresholds the engine tiered with, on the **evidence** scale (`fit_score` x chemical plausibility - see "Tier coherence" in Validation), **required** - a first-class run field, *not* buried in opaque `config`, because the server validates rows against it. Same two keys, same `{tier: threshold}` shape as before; only the scale the numbers are read on moved. The legacy key `identified` is accepted and normalised to `assigned` (see "Enum validity" in Validation) |
| `calibration` | the client's calibration state, **required** - its own nullable JSON column on the run, *not* a reserved key inside `config` |
| `rows` | assignment rows (below) |
| `chunk` | assembly control: `{run_id, import_id, index, complete}`, **required** (`import_id` with it) - see "One logical import" |

Each row is **`PeakAssignmentRecord` (§3) minus the server-owned fields**. That
is the whole definition: do not re-enumerate the columns here, because a second
list drifts from the first (an earlier draft of this section did exactly that,
silently dropping `isotope_formula` and the target ids). Concretely, every
read-side column - including `isotope_formula`, `target_compound_id` and
`target_ion_id`, so an imported `source='database'` row can satisfy §2's
targeted/untargeted equivalence - **except**:

- **server-minted, never client-supplied**: `sample_item_id`,
  `peak_assignment_id`, `peak_assignment_run_id`;
- **replaced by a row reference**: `owner_peak_assignment_id` becomes
  `owner_sample_peak_id` (below);
- **server-owned, ignored if sent**: the flattened provenance scalars
  `evidence` / `p_correct` / `p_correct_provisional` / `corroboration_adducts`
  (see "Validation"). `evidence` is server-owned for a different reason than the
  other three: it is not withheld but **derived**, from the row's own
  `fit_score` and `assigned_formula`, and written back onto the stored row.

`ionization_mechanism_id` is **nullable** - an external engine names adducts by
notation, not by a deployment's mechanism ids (§8.5.2) - but a *supplied* id
must **exist on the deployment** and must **carry the sample's polarity**. A
nonexistent or polarity-mismatched id is a **422 at validation time**, not an
`IntegrityError` surfacing as a 500 out of the bulk insert after the whole
payload has been uploaded. The same applies to `target_compound_id` and
`target_ion_id`, the other two ids a row carries into a foreign key.

**Membership of the sample's ionization *mode* is deliberately not required on
top of polarity**, though the in-app engine does satisfy it structurally (it
draws mechanisms from the sample's own mode and then filters them on
`sample.polarity`). A mode is a deployment's narrowing of *what to search for* -
which adducts this lab expects to see - rather than a property of the
measurement, and a sample may carry no mode at all. Requiring it would refuse a
correct adduct for a reason that says nothing about whether the ledger is right:
an external engine that found `[M+Na]+` in a sample whose mode lists only
protonation has made a claim about the data, not a configuration error. Polarity
is the opposite kind of fact - a negative-mode adduct on a positive-mode sample
cannot be true of that measurement - so that is the line the check draws.

`alternatives` and `provenance` (JSON) are accepted as inspector detail, subject
to the provenance rule below.

**Reserved provenance keys, and where an engine's own numbers go.** The app
reads a handful of keys out of the `provenance` blob and presents them as its
own judgement: the peak inspector renders `provenance.p_correct` under
"Calibrated probability the assignment is correct" with a provisional flag off
`provenance.calibration.provisional`, and the batch fold-in reads
`provenance["p_correct"]` when it rolls a batch peak's consensus. Nulling the
flattened columns therefore does not achieve what it is for - nothing reads
those columns - so the keys the server derives those values from are **reserved
at the top level of an imported blob** and **stripped** there before the row is
stored.

Three keys, which are the ones `_provenance_scalars` derives those confidence
columns from and not the names of the columns it renders: `p_correct`, plus the
two objects it reaches into - `calibration` (for `.provisional`) and
`corroboration` (for `.n_adducts`). The rendered names `p_correct_provisional`
and `corroboration_adducts` are *not* reserved, because nothing reads them out
of the blob; sending them is harmless and they are simply never looked at.

Two further keys are **overwritten rather than stripped**, and they are the two
factors of one product. `_provenance_scalars` reads `evidence`: it is the number
the ledger shows beside the tier chip, and the tier was validated against the
evidence the server derived (see "Tier coherence"), so the server writes its own
derived value over whatever the payload carried under that name - and removes
the key outright on a row with no `fit_score` to derive one from. `plausibility`
is the other factor, and is treated identically: the peak inspector renders it
as *this server's* reading of the formula's chemistry, right beside the fit and
the evidence, so an importer's own figure under that name would be presented as
ours. It is derived from `assigned_formula` and written over the payload's, and
is absent on a row with no formula or one this server could not parse - a dash
being the honest rendering of "could not read it", where 1.0 would assert
perfect chemistry for a string nothing could read.

Leaving an importer's figures there would put numbers on screen that did not
produce the tier next to them; dropping them would blank the columns on every
imported row. An engine that wants its own product kept keeps it the same way it
keeps its own `p_correct`: under a name of its own, `provenance.engine_provenance`
by this document's convention.

Stripped rather than rejected. An external engine that shares this one's scoring
lineage will plausibly use these names for its *own* numbers, so a payload
carrying them is not malformed - it is just not authoritative about them, and
refusing the whole import over a naming collision would be the wrong trade. What
the server guarantees is only that an imported row's rendered confidence is
empty, not that the importer was careful. An engine that wants its own numbers
kept should put them under names of its own - `provenance.engine_provenance` is
the convention this document uses elsewhere - which the server stores verbatim
and never interprets. The
distinction the contract needs is not "foreign numbers are unwelcome" but
"whose number is this" - keeping both, under names that say which engine
produced them, is what makes an in-app run and an imported run comparable at
all (§8.3).

The consequence at batch level follows and is deliberate: consensus weight is
`fit_score` scaled by log intensity and never `p_correct`, so an imported
member carries its evidence normally, while the batch peak's own `p_correct` -
the maximum across members that have one - reflects only Mascope-calibrated
members, and is absent when a batch peak is supported by imported members
alone.

**Owner linkage is a client-side row reference resolved server-side.** The
client cannot supply `owner_peak_assignment_id` - those ids do not exist until
the server mints them - so an `iso_child` row references its owner as
`owner_sample_peak_id`: the owner row's `sample_peak_id`, which identifies a
row uniquely within the import because a run holds at most one row per peak.

Rows are inserted in payload order and the reference is **staged** on
`owner_sample_peak_id`, not resolved on the way in - an owner may arrive in a
later chunk than its children, so "insert owners first" is not something the
server can arrange across requests. At finalize a single set-based
`UPDATE ... FROM` joins each staged reference to its owner within the run and
writes the minted `owner_peak_assignment_id`; an unresolvable reference - no
such row in the import - is a 422.

**The link is one level deep, and two rules keep it that way.** A row carrying
an owner must have `role='iso_child'` (checked per chunk), and an `iso_child`
may not itself be named as an owner (checked at finalize, since the owner may
be in another chunk). Depth follows from the pair, and so does acyclicity: a
cycle needs every row in it to be both a child and an owner, which the two rules
forbid together. Checking only the direct self-reference - the first
implementation - left `A owns B` with `B owns A` to resolve happily into a shape
no in-app run can produce.

**One logical import, one or more requests.** A dense sample's full ledger with
`alternatives`/`provenance` is tens of thousands of rows at ~2.8 KB each - too
large for one request body, and an unbounded row list would serialize unbounded
work through Pydantic on the event loop (the same reasoning that paginated the
ledger *read*). So the endpoint caps `rows` per request and supports assembly:

- the first request (`chunk.index == 0`, no `run_id`) creates the run
  (`status='importing'`, engine + config + tier bands + calibration stamped) and
  returns its `peak_assignment_run_id`;
- follow-up requests carry that `run_id` and set `chunk.index` to the number of
  rows the server has already accepted. The index is a row **offset**, not a
  sequence counter: the second request's index is the first chunk's row count,
  not `1`;
- the request with `chunk.complete: true` - which may be the first and only one,
  the common case for slim ledgers - finalizes: payload-wide validation, owner
  resolution, `status='completed'`, batch fold-in.

Every accepted chunk answers with the standard list envelope
(`{status, message, results, data}`), and the import's state is the single
record in `data[0]` - four fields, which is the whole state a client needs to
continue or resume:

| field | meaning |
| --- | --- |
| `peak_assignment_run_id` | the run, minted on the create and repeated on every chunk |
| `rows` | rows staged so far - and therefore the next `chunk.index`, since the index is an offset |
| `max_rows_per_request` | the deployment's effective per-request row cap |
| `run_status` | `importing` while assembling, `completed` after the finalizing chunk |

**Read the run's state from `data[0].run_status`, not from the envelope's
`status`.** The envelope carries its own top-level `status`, and it is the
literal `"success"` on every accepted request - the same field every other
endpoint's envelope has. A client that reads `status` expecting
`importing`/`completed` gets `"success"` forever and never learns the import
finished.

**The per-request cap is a number, and it has a byte consequence the client
cannot see.** The cap is **1000 rows** - the ledger read's `DEFAULT_PAGE_LIMIT`,
for the same reason it was chosen there. At the measured ~2.8 KB for a row
carrying `alternatives` + `provenance` that is a request body of roughly 2.8 MB
(a slim row is ~0.74 KB, so ~0.75 MB), which is well over nginx's compiled-in
1 MB default for `client_max_body_size` - a default that currently governs
`location /api/`, because the deployed config sets a limit only on the two
upload locations. So the import route needs its own allowance, **8 MB**, and
step 2 carries that config change alongside the endpoint. It is not optional
polish: a dev stack talks to the backend port directly and never sees the proxy,
so a chunk sized from the row cap alone works everywhere except production,
where it returns a 413 that `_raise_for_status` treats as terminal, with nginx's
HTML error page as the message, mid-assembly, leaving an `importing` run holding
the sample.

The cap is the deployment's to lower, so the client is told rather than left
guessing: `max_rows_per_request` comes back on the create and on every chunk,
and a chunk over it is a 422 naming the cap rather than a silent truncation.
Only the first request has to size itself blind - it uses the documented 1000
and adjusts from the response. Rows differ in size by an order of magnitude
depending on whether they carry `alternatives`/`provenance`, so `import_run`
sizes chunks by **serialized bytes as well as row count**: the row cap is the
ceiling, not the target.

**Chunks must be idempotent, because the SDK retries POSTs.** `_http.http_post`
wraps `requests.post` in its own retry loop (4 attempts, on `Timeout`,
`ConnectionError` and 502/503/504) and exposes no way to disable it, so a read
timeout after the server has applied a chunk *will* re-send it. The protocol
therefore makes `chunk.index` a monotonic, server-checked row offset: a chunk
whose index equals the run's `rows` is applied; a chunk that repeats an offset
already applied - the retry case, where the server committed and the client
never saw the response - is an **idempotent no-op returning the current row
count**; an index ahead of `rows` (a gap) or one that lands inside an
applied chunk rather than on its boundary (a rewind) is a 409. The client
resynchronises from the `rows` the last response it saw reported. Without this, a retried append duplicates
rows straight onto `uq_peak_assignment_run_id_sample_peak_id` and fails an
otherwise healthy import. This is the same problem the repo already solved for
file upload: the tus path is offset-addressed and re-syncs from the
server-confirmed offset (`api_post_file_tus`), whose own comment warns that
otherwise "the caller's outer retry creates a fresh resource per attempt,
multiplying them". `chunk.index` is that offset, in rows.

The offset covers the **appends**, and only those. It cannot make the create
idempotent, because a retried create is byte-identical to a fresh one - offset
0, no run id - so there is nothing in the request itself for the server to
dedupe on; and it says nothing about re-entering finalize, which is a different
kind of request.

Both are answered **server-side, and required rather than advisory**, because
this is an HTTP endpoint and the SDK is not its only caller: a third-party
engine, a retrying proxy, or a load balancer can replay a create that no
client-side policy of ours governs. So the create carries `chunk.import_id` - a
client-chosen key, persisted as `import_key`, unique per sample - and a repeat
resolves to the run already made for it; finalize is idempotent on run id plus
terminal state. `import_id` is **required**, not recommended, because the
consequence of omitting it is shape-dependent and the worse case is silent: a
chunked create leaves the second run non-terminal, so admission refuses it
loudly, but a single-request create finishes as `completed` - which admission
does not refuse - so the retry lands a duplicate ledger and a second batch
fold-in with nothing raised anywhere.

Taking create and finalize off the SDK's blind retry loop is still worth doing
and remains §8.3's business - it saves the wasted round trip and the duplicate
work even when the server would dedupe - but it is now a courtesy on top of a
guarantee rather than the guarantee itself. §8.3 carries it, since the retry
loop is SDK plumbing shared with `verify` and `assign`.

**A 409's body is advisory, not machine-readable - recover state, do not parse
prose.** `_raise_for_status` maps 409 to the generic `MascopeAPIError` and keeps
only `_extract_error_message`'s flattened string, so neither the in-flight run
id in an admission refusal nor the accepted-row count in a resync 409 can be
read off the exception. Recovery is therefore by state, not by message: the run
id comes from `list_runs`, which applies no status filter and so already
surfaces an `importing` run and its id - which is also how a client that lost
its run id reaches the abandon endpoint - and the accepted-row count comes from
the last chunk response the client did see. §8.3 makes retaining the parsed
error body part of step 5, which demotes this from the only route to the
fallback.

**Assembly needs a state of its own; the existing lifecycle does not cover it.**
An earlier draft claimed the import needed no new machinery, reusing `running`
plus the startup reaper. That is wrong in both directions:

- `reset_running_peak_assignment_runs` marks **every** `running` run `failed` at
  **every** startup, with no age or engine filter, and it is correct to do so
  only because "startup runs in the main process before any worker is spawned,
  so nothing can legitimately be `running` at that moment". A chunked import is
  legitimately in flight with no server task attached, so a routine deploy would
  fail a live upload and the client's next chunk would target a `failed` run.
- A run mid-assembly is **not invisible** either. Only the *default* ledger read
  resolves to the latest `completed` run; `GET /sample/{id}/runs` lists runs of
  every status (that is what the run selector reads), and a read with an
  explicit `peak_assignment_run_id` serves that run's rows whatever its status -
  and §8.2's own flow hands the client that id on the first request.

So imports assemble under a distinct non-terminal status, **`importing`**, which
the startup reaper skips and which the read paths treat as non-servable (the
explicit-run-id read returns 409 for an `importing` run rather than a partial
ledger). Staleness is the prune's job, under its own grace: an `importing` run
older than `keep_importing_hours` is deleted with its rows, the same shape as
today's `keep_running_hours` sweep. Note the consequence for the run selector:
an import in progress is *visible* as an in-flight run, which is the honest
display and is what makes the 409 admission refusal legible to a user.

**An abandoned assembly can be cleared without waiting for that grace.**
`DELETE /sample/{sample_item_id}/runs/{run_id}` deletes an `importing` run
with its staged rows, for an editor of the sample's workspace. The grace alone
is not enough of an answer: admission refuses on any non-terminal run and the
reaper deliberately skips `importing`, so a client that dies mid-upload - or
that simply loses the run id it was handed - blocks every later import *and*
in-app assign for that sample until the next nightly prune, up to about a day
for an upload abandoned just after one runs. The client that still holds the
run id has always been able to carry on appending to it; this is the way out
for the one that cannot. Deliberately restricted to `importing`: a
`completed` run is ledger data, and removing that is retention's business
(§8.2's per-(sample, engine) budget), not a client's.

**Row-count bounds.** The per-request cap bounds one request. The *total* is
**not** bounded by construction until finalize, because peak-existence and
duplicate checks are payload-wide: until then a client could append chunks of
nonexistent `sample_peak_id`s indefinitely. So the run also carries a **total
row cap** (`<= the sample's peak count`, checked as each chunk lands) and the
staged rows are reclaimed by the `importing` grace above. After finalize the
by-construction bound does hold: at most one row per peak the sample has.

#### Attribution: the `engine` run column

One **additive** schema change: an `engine` column on `peak_assignment_run`,
backfilled to `'mascope'` for existing rows in the same migration and stamped by
the in-app write path from then on. The earlier NULL-means-in-app sentinel is
dropped: it pushed a tri-state onto every consumer (the run selector, the SDK
frame, every SQL predicate, where `engine <> 'peaky'` silently drops NULLs), and
it left the in-app identity unreserved. The table is prune-bounded to a handful
of runs per sample, so the backfill is trivial.

The value space is **constrained, not free text**: `String(64)`, matching
`engine_version`, and the reserved in-app identity (`mascope`) is rejected from
client payloads with a 422. Without that, an importer could stamp `engine:
"mascope"` and defeat the one mechanism the rest of this section leans on -
"first-class but always attributable" and "the engine badge is what keeps it
honest" are only true if the badge cannot be forged.

The migration parents on the **current Alembic head, `c3a9e6f2b8d1`**
(`20260818_c3a9e6f2b8d1_add_mfa_columns.py` - the MFA migration, itself chained
onto the batch-peak head `f3b9c7a1e2d4`), and carries `calibration`,
`tier_bands` and the `importing` status alongside `engine`.

Imported runs are **first-class but always attributable**: same tables, same
read model, same fold-in - and the app's run selector shows engine provenance
(`engine` + `engine_version`, plus a calibration badge), so a user reading a
ledger knows which engine produced it before trusting a tier. None of that
surface exists today: **`engine` and `calibration`** must be added to
`PeakAssignmentRunRecord` and to the SDK's `list_runs` frame, and the badge must
be built in the run selector. §8.4 sequences all three - the trust model below
is only as good as the badge that carries it, so it is not optional dressing.

Both fields, not just `engine`: the runs read is a typed projection, not a
passthrough. The service hands the route every column of the run row, but the
route validates through `PeakAssignmentRunsResponse`, and `PeakAssignmentRunRecord`
is a plain pydantic model under the default `extra='ignore'` - so a column the
record does not name is dropped before the response leaves the API, and both
consumers take exactly its field set (the app stores whatever the endpoint
returns, and the SDK builds its frame with `pd.DataFrame(data)`). Adding
`engine` alone makes the engine name observable and leaves the calibration badge
with nothing behind it.

#### Validation (strict-lite)

The bar: reject what would corrupt the read model or forge a server judgement;
accept what is merely the importer's judgement.

- **Single owner per peak** - enforced server-side. At most one row per
  `sample_peak_id`: a duplicate within the payload is a 422, and the existing
  unique constraint `(peak_assignment_run_id, sample_peak_id)` backstops it at
  insert. A duplicate arriving *across* chunks is caught by the same constraint
  and reported as the same 422 (which is why chunk idempotency, above, must
  prevent a retry from manufacturing one). This is the ledger invariant every
  consumer assumes.
- **Enum validity.** `role` / `tier` / `source` are validated against the same
  typed vocabularies the read filters use (§3); a bad value is a 422 naming
  the accepted set. The *vocabulary* needs no mapping: the app's tier names
  (`assigned` / `candidate` / `below_assignability` / `unassigned`) and its
  fit-score scale came *from* peaky, and both engines score with `mascope_tools`
  `score_pattern` - compatibility is by construction, not by translation
  ([peak_assignment_paradigm.md](peak_assignment_paradigm.md) §2,
  [fit_score.md](../../libraries/tools/docs/fit_score.md)). The one translation
  the server does perform is historical: the top tier was called `identified`
  before it was called `assigned`, and that spelling is still accepted - on an
  imported row's `tier`, on the read filter, and as a `tier_bands` key -
  normalised to `assigned` on the way in. An engine built against the old
  vocabulary keeps publishing without a version negotiation, and because the
  normalisation happens at the edge, nothing downstream ever sees two names for
  one tier.
- **Tier coherence against declared bands.** A shared fit scale does not make
  the *bands* shared, and it is no longer even the scale the bands sit on. A
  tier is read off the row's **evidence** - `fit_score` weighted by the chemical
  plausibility of `assigned_formula` - which is the quantity both engine stages
  already arbitrate a contested peak in, so the band a row lands in agrees with
  the number that won the peak its formula. In-app tiers come from thresholding
  that product against `assigned_threshold` and `candidate_threshold`, which are
  **run config**, not engine constants. Those two keys are unchanged; only the
  scale they are read on moved, and the in-app pair moved with it, from 0.8/0.5
  on the bare fit to 0.75/0.45 on the evidence. Since an import's `config` is
  opaque, the bands are lifted out as the required `tier_bands` field; the
  server then derives each row's evidence itself -
  `engine.evidence_for(fit_score, assigned_formula)` - and requires the row's
  `tier` to be the one those declared bands put that evidence in. An incoherent
  row is a 422, and it names both numbers, the
  evidence the check used and the fit it came from, so an engine that tiered on
  the bare fit can see exactly how far the plausibility moved it. Without the
  check an engine tiering at 0.6/0.3 publishes `assigned` rows at 0.62 that sort
  and filter beside in-app `assigned` rows meaning something stricter - and
  outrank them in the cross-sample `TIER_RANK` roll-up in `compute_consensus`.

  **`tier` is optional, and better omitted.** Everything the check needs is
  already server-side - the row's `fit_score` and `assigned_formula`, the run's
  declared bands - and the server computes the answer anyway in order to check
  a supplied one. So a row that states no tier gets the derived one, and the
  invariant above holds *by construction* rather than by refusal. Sending a
  tier means reproducing this deployment's `formula_plausibility` exactly; the
  two implementations then drift, and the drift refuses a whole import over a
  number the client had no reason to hold. A supplied tier is still accepted
  and still checked, so nothing that worked before stops working - but the
  documented advice is to leave it out. This is what makes the promise below
  true rather than merely nearly true.

  **This asks nothing new of an importer.** There is no new required field: a
  row carries the same columns it always did, `fit_score` and `assigned_formula`
  among them, and the run declares the same `tier_bands` in the same shape. There is
  deliberately no `evidence` field on a row and no declared-plausibility field
  either, because plausibility is a pure function of the formula (the Seven
  Golden Rules heuristics in `mascope_tools`) - so the server recomputes it from
  `assigned_formula` on every row rather than trusting a number a payload could
  assert, exactly as manual curation recomputes it. An engine holding only a fit
  score therefore has nothing extra to compute: it declares its bands on the
  evidence scale and is checked against a product the server can always rebuild.
  The recomputation also fails open - a formula that is absent or that will not
  parse leaves the evidence equal to the bare fit - so an unusual composition is
  never refused for being unusual, and a formula whose plausibility is 1.0, the
  common case, has evidence equal to its fit exactly.

  **The number shown beside a tier is the number that was validated.**
  `import_service._row_values` writes the server-derived evidence into the
  stored row's `provenance["evidence"]`, over whatever the payload carried
  there, and `_provenance_scalars` flattens it onto the ledger row as the
  `evidence` column the tier chip renders (§3). So the pairing a reader sees is
  the pairing the check enforced. `fit_score` is untouched by any of this: it is
  stored and served exactly as supplied, as the pure measurement it always was.

  The rule has **one stated exemption, and it covers most of a complete
  ledger**: a null `fit_score` is not banded, and it admits **two** tiers,
  because the in-app ledger writes both. The exemption is still exactly the
  null-fit case and has not widened - `evidence_for` returns None when, and only
  when, there is no fit score to weigh. `build_unassigned_assignments` writes
  `tier='unassigned'` with a null score for a peak no stage explained; and
  `tier_for_evidence(None, ...)` answers `below_assignability`, which the engine
  pairs with a null `fit_score` on an *assigned* row whose score came back
  non-finite (`_score_or_none` nulls it, `tier_for_evidence` bands it). Neither
  is a claim about confidence, so neither is worth refusing.

  **What the rule does NOT cover, and where that verdict goes.** The check binds
  `tier` to a band function, so a tier an engine reached any other way has no
  home in it - and a *demotion* is refused as firmly as an inflation, which is
  the case that matters. peaky tiers mechanically: window uniqueness,
  isotopologue corroboration, a mass-degeneracy audit, composition heuristics.
  Every one of those can put a peak below what its evidence alone earns, and
  none of them is expressible as a threshold on that evidence. Under this rule
  alone such a run either loses its own judgement or is refused outright, which
  defeats the point of putting two engines in one store.

  So a row may also carry **`engine_tier`**: the tier the producing engine
  concluded, stored as supplied, validated only against the tier vocabulary
  (legacy `identified` included, so a rename cannot manufacture a
  disagreement), and read by **nothing** - not `compute_consensus`, not
  `TIER_RANK`, not the batch fold-in. That exemption is the field's whole
  purpose and is not a loophole in the rule above: `tier` still means what the
  declared bands say it means, and remains the only tier anything ranks on. The
  pair reads as *what this server's banding says* beside *what the engine
  concluded*, and a row where the two differ is the interesting one. Null means
  the engine stated no tier for the row - which is the in-app case, the
  `mascope-copy` case, and the usual case for rows an external engine leaves
  untiered - and absence is not agreement: the ledger's `tier_disagrees` filter
  excludes such rows from both answers.

  Batch level is deliberately out: a batch peak's consensus is an
  evidence-weighted vote over members whose runs may declare different bands
  *and* different engines, so a second consensus over `engine_tier` would be a
  vote over verdicts with no shared yardstick.

  An earlier revision of this section called `below_assignability` "the wrong
  tier for such a row" and required `tier='unassigned'` for a null score. That
  was implemented and it was wrong in a specific, costly way: it refused rows
  Mascope's own engine produces, so an external engine that read a ledger
  through the SDK could not publish it back - the round trip this whole section
  exists to enable. The check now delegates to `tier_for_evidence` rather than
  restating it, so the two cannot drift again; a null score accepts
  `unassigned` or `below_assignability`, and every other row is banded.
- **Provenance is inspector detail, never a server judgement.** The ledger
  renders a calibrated `p_correct` (with its provisional marker) and an adduct
  corroboration count on every row; `_provenance_scalars` flattens those out of
  `provenance` today. For imported runs the server **does not** read them from
  the payload - they are stored as NULL and the ledger shows them empty. An
  importer's own confidence belongs in `fit_score` and in the `provenance` blob
  the inspector shows; it does not get to populate a column the UI presents as
  Mascope's calibrated probability, on a run that may have disclosed no
  calibration at all.

  **What nulling the columns does not reach.** The flattened scalars are a
  convenience projection, not the only path to those numbers, so the rule is
  narrower than it first reads. The peak inspector resolves the stored
  `provenance` blob verbatim off the detail record and renders
  `provenance.p_correct` under "Calibrated probability the assignment is
  correct", with a provisional flag taken from `provenance.calibration.provisional`;
  and `_recompute_consensus` selects `PeakAssignment.provenance` and feeds
  `prov.get("p_correct")` into `compute_consensus` - it never reads the
  flattened column at all. (An earlier draft asserted the opposite here, citing
  the consensus pass as evidence *for* the column rule; that parenthetical is
  retracted.) Since this section positively directs an importer's own confidence
  *into* the blob, and peaky shares the scoring lineage that builds those key
  names, a collision is likely by accident rather than by malice. Closing it
  needs a key policy on the blob, and the one chosen is **reserve and strip**
  (§8.5.5): step 2's validation drops the three keys the reading surfaces
  derive from, so an imported row renders no confidence of this server's.
- **Peak existence.** Every `sample_peak_id` must exist in the sample's peak
  file; a row for a peak the sample does not have is a 422. Use the **id-only**
  read - `extract_peaks(..., areas=False, heights=False, average=False)` - not
  the in-app engine's full load: validation needs the id set, while `average=True`
  additionally opens the *raw* data source per import (and again on every retry)
  just to compute an averaging divisor. The denormalized mz/intensity/tof are
  stored as supplied: they are the importer's observed values,
  display-denormalized exactly as on `MatchIsotope`.

  **Which intensity, though, is not the importer's choice.** An import must
  supply the quantity the in-app engine supplies for that instrument type -
  **peak heights for Orbitrap files, peak areas for TOF files**
  (`_load_sample_peaks`). The value is not display-only: it lands on
  `BatchPeakOccurrence.intensity` and scales that member's consensus vote
  (`_vote_weight` weights fit by `1 + log1p(intensity)`), while the batch peak's
  *declared* unit is derived independently from the file's instrument type
  (`_intensity_variable`). Supply the other quantity and the stored number
  disagrees with the label above it and shifts the sample's weight against its
  batch peers - silently, since nothing on either side can detect the swap.
- **`config` size cap.** `config` is opaque, so nothing bounds it the way the
  closed `PeakAssignmentConfig` model bounds an in-app run's. It needs an
  explicit byte cap (422 above it), because `GET /sample/{id}/runs` re-serves
  every run's full config on a hot path - the SDK's `get()` calls `list_runs` on
  every ledger read, §8.3's wait loop polls it, and the run selector reads it.
  That is the blob-on-the-list-read shape #1725 just removed from ledger rows;
  do not reintroduce it on the run record.
- **Completeness NOT required - but understand what a partial import replaces.**
  An imported run may cover a subset of the sample's peaks; whether to fill
  unexplained peaks with `unassigned` rows is the importer's choice, and the
  *ledger* read serves whatever rows exist. The **batch view does not merely
  tolerate** a subset, though: `fold_sample_into_batch_peaks` deletes all of the
  sample's prior `BatchPeakOccurrence` rows before re-inserting from the new
  latest-completed run, and `_recompute_consensus` then deletes any `BatchPeak`
  left with no members. So publishing 20 rows of interest on a 30,000-peak
  sample silently withdraws that sample's other 29,980 peaks from the batch
  overview and can delete anchors it alone supported. Decided: partial imports
  are allowed, the API documents this replacement explicitly, and `import_run`
  warns when a frame covers materially fewer peaks than the sample's current
  latest-completed run. (Requiring completeness for batch members was
  considered and rejected: it would make the common single-sample research loop
  pay for a batch-level concern.)

#### Trust model

- **Append-only per write.** An import always creates a *new* run; it never
  touches an app-computed run (or an earlier import), matching the in-app
  engine: runs are never superseded in place.
- **Retention is where "append-only" stops protecting in-app runs.** The nightly
  prune keeps the newest `keep_per_sample` completed runs per sample -
  **3 by default** - and makes no engine distinction today, so three republished
  imports would evict every in-app run for that sample, ledger rows cascading
  with them. That directly contradicts what a reader takes from "append-only",
  and the fix is small: the prune's keep-newest budget becomes **per (sample,
  engine)**, so imports and in-app runs age out of their own quotas and neither
  can starve the other. §8.4 step 2 carries the prune change with the endpoint;
  the doc's earlier "imported and app-computed runs share the per-sample budget"
  is retracted, because sharing it is what made publishing destructive. A second
  budget, `keep_per_sample_total` (**12** by default), bounds the sum across
  engines: `engine` is free text the client supplies, so a name that varies per
  build would otherwise mint a fresh quota on every import. **The in-app engine
  is exempt from that total**, which is what keeps the guarantee absolute - an
  import can never evict a sample's in-app history, whatever it calls itself.
  The ledger is still a store of runs rather than an archive of every run ever:
  a published run that must stay current is republished, and that costs the
  importer's own quota and, across enough engines, the shared outer bound -
  never the app's.
- **Default-read consequence.** `get()` and the app's ledger read default to
  the *latest completed* run, whatever its engine, so a fresh import is what a
  reader sees by default. That is the point - published runs are first-class -
  and the engine badge is what keeps it honest.
- **Calibration: bypass, but record** (decided). In-app assignment refuses a
  sample whose m/z calibration **exists but is unverified** (`ineligible_reason`
  checks `sample.mz_calibration and not ...get("verified")`; a sample with no
  calibration at all is *not* refused), because the in-app engine's mass errors -
  and therefore its fit scores and tiers - would mean nothing. An import
  **bypasses that refusal**: peaky does its own offset-aware calibration
  client-side, so the server-side verification state does not describe an
  imported ledger's mass accuracy. The flip side is mandatory disclosure: the
  required `calibration` object (the client's calibration method/state, plus the
  sample's Mascope-side verification state at import time) is persisted in **its
  own column** on the run, and the UI badges the run from it. It is deliberately
  *not* a reserved key inside `config`: `config` is specified as stored
  verbatim, injecting a server key would break that and make the stored config
  no longer what the engine ran with, and `calibration` is a plausible key in a
  client-side-calibrating engine's own config - a silent collision on exactly
  the field the badge depends on. The migration is being written anyway; a
  column is cheaper than the ambiguity. An importer that did no calibration says
  so on the record.
- **Verifications on imported rows do not feed instrument recalibration.**
  Verifying an imported assignment is fine and useful - the verification
  endpoints are engine-agnostic and the human verdict is what matters. But
  `create_verification` snapshots `provenance["evidence"]` from the judged row
  into `AssignmentVerification.evidence`, and `recalibrate_instrument` fits the
  instrument-wide Platt curve over **every** confirmed/rejected verification for
  that instrument type, filtered by nothing else. That curve is what every
  assignment's P(correct) reads from, which is why the route is superuser-only -
  so an editor-supplied `evidence` value must not reach it. Decided: the
  recalibration query joins through the run's `engine` and uses **in-app runs
  only**; imported-run verifications are retained, listed, and shown, but
  excluded from the label pool. (§8.5.3 asks whether they can be admitted later
  under a declared-scale rule.)

#### After import: batch fold-in

Finalizing an import runs `fold_sample_into_batch_peaks`
([batch_peaks_controller.py](../../server/backend/src/mascope_backend/api/new/peak_assignments/batch_peaks_controller.py)),
exactly as an in-app run's completion does, so imported runs feed the batch
**Assignments** overview the moment they land. Failure isolation mirrors the
in-app path: the fold-in is **best-effort** - a fold-in failure is logged and
never fails (or un-completes) the import itself. The fold-in reads the sample's
latest completed run, which by then is the import, and *replaces* that sample's
contribution (see "Completeness" above).

Two consequences worth stating, because the import path reaches them in ways the
in-app path does not:

- The per-sample offset `mu_ppm` is the median `mz_error_ppm` of the run's
  assigned rows, and it shifts that sample's peaks onto the shared batch axis.
  For an import those residuals are relative to the *client's* calibration -
  another reason the calibration disclosure is mandatory.
- Publishing a whole batch sample-by-sample is superlinear: `_recompute_consensus`
  re-reads every occurrence of every touched anchor across all samples folded so
  far, so N similar samples cost roughly N times what one deferred pass would.
  The in-app sequential batch pays this too, but there each fold feeds a live
  UI. A `fold_in: false` import option plus one deferred consensus pass is the
  cheap fix if publishing whole batches becomes routine; it is not required for
  the loop to work, and is not the same question as §8.5.1.

### 8.3 Part B - triggering, verification, fit (the v1 deferrals, decided yes)

v1 deferred these behind one question - "should the SDK trigger heavy async
runs?" (§9.1). **Decided: yes.** The ground shifted after the question was
raised: the write routes are now gated on the `peak_assignment` flag (403 when
the deployment has not opted in), and admission control refuses a duplicate run
server-side rather than queueing it. The hazard behind the question was an SDK
loop cheaply scheduling unbounded heavy compute; that is now bounded where it
must be, on the server, so client discipline is no longer the safety mechanism.
(The config ceilings - `max_untargeted_peaks`, the `formula_ranges` species cap,
`max_alternatives` - bound each run's cost independently.)

**One server-side change comes first, because the wrappers are unimplementable
without it.** Today `POST .../assign` returns 202 unconditionally and decides
everything afterwards, inside the background task: the admission refusal, and
the eligibility skip for blank or unverified-calibration samples, are both
reached *after* the response is sent. Neither reaches a headless client. The
refusal is not merely socket-only, as an earlier draft of this section claimed -
it is not machine-readable **anywhere**: `api_controller_background_task` stamps
`notification.status = "success"` on any non-exception return and reads
`notification.data` from a `_notification_data` key the refusal never sets, and
`skipped` is not even a legal notification status. What actually reaches the
socket is a success-status toast string.

So v2 moves both decisions in front of the response, which is cheap because both
are already synchronous computations: the route fetches the sample anyway,
`ineligible_reason(sample)` is a pure function of that row, and the in-flight
check is the indexed run-state query §8.2 introduces. The assign endpoints then
answer honestly:

- **202** with the newly created run row's `peak_assignment_run_id` in the body
  (created `pending` in the request, as the import path already does), so a
  client polls *one known run* instead of diffing run sets and guessing which
  new run is its own;
- **409** with the in-flight run id when admission refuses;
- **422** naming the reason when the sample is ineligible.

**Any non-terminal status the admission check refuses on must have something
that reclaims it.** Today nothing writes `pending` at all - `_create_run`
hardcodes `running`, and `pending` is only the column's server default - and the
startup reaper resets exactly `running`. That is not an accident of scope: the
prune's `_stale_runs_statement` deliberately denies in-flight rows the
null-timestamp fallback *because* "the startup reaper moves it to 'failed'". A
run created `pending` in the request breaks that invariant. FastAPI background
tasks run in-process after the response is sent, so a deploy, a worker restart
or an OOM kill between the 202 and the task's first line leaves a row nothing
touches, while the prune classes `pending` as in-flight and holds it for
`keep_running_hours` (72h by default, floored at 12) and §8.2's abandon endpoint
is deliberately restricted to `importing`. Under step 2's durable admission that
row 409s every later assign *and* import for the sample for the whole grace.

So step 3 ships the reclamation together with the status. Which of the two ways
it does that is a **step-3 implementation choice**, not an open product
question - both are correct, and they trade the same small cost differently:

- **create the run `running` in the request.** The reaper's coverage and the
  prune's stated invariant are untouched, and nothing else has to learn a new
  state. The cost is a row that is `running` with no task attached for the width
  of the response-to-task gap, and a `running` status that no longer implies
  "engine is executing".
- **create it `pending` and widen the reaper** to `status.in_(("pending",
  "running"))`, leaving `importing` alone. `pending` keeps its honest meaning -
  admitted, not yet started - at the cost of a second status in every place that
  reasons about the reaper's invariant, including that prune docstring.

Whichever is picked, `assign(wait=True)` must state what it hands back when the
poll times out: the run id, so the caller can resume or abandon rather than lose
the run it just launched. A timeout is now a reachable outcome that is not the
caller's fault.

That is the whole basis for the wrappers below. Without it, `assign()` would
have to infer refusal from the *absence* of a new run, which is racy (the
in-flight run can terminate before the first poll) and simply blind to the
ineligible case (no run is ever created, so there is nothing to observe) - the
wrapper would spin to a timeout in exactly the cases the server already knew
about at request time.

- **`assign(sample_id, *, config=None, wait=True, poll_interval=..., timeout=...)`**
  - wraps `POST .../assign`. `config` mirrors `PeakAssignmentConfig`
  (`run_untargeted`, `mz_precision_ppm`, `formula_ranges`, `max_untargeted_peaks`,
  `peak_intensity_threshold`, `max_alternatives`, `assigned_threshold`,
  `candidate_threshold`; the old `identified_threshold` spelling is still
  accepted). The 202 body carries the run id, so completion is
  observed by polling **that run** to a terminal state - all three of which
  exist: `completed` | `failed` | `cancelled`. `wait=True` blocks and returns
  `get(...)` of the finished run; it raises on `failed` (carrying the run's
  `error`) and on `cancelled` (which yields no rows at all, since the engine
  writes its ledger in one insert immediately before finalizing - so returning
  an empty frame would be indistinguishable from a real empty result).
  `wait=False` returns the run id. A 409 raises an explicit already-in-flight
  error carrying the in-flight run id; a 422 raises the ineligibility reason.
  Neither is ever reported as a timeout.

  The `Process-ID` response header is **not** part of this contract. No endpoint
  accepts a process id, nothing persists it, and its only consumer is the socket
  notification grouping a headless client cannot receive - so exposing it would
  hand callers an identity they can do nothing with. (For the record, reading it
  would need no `_http` change either: `http_get`/`http_post` already return the
  full `requests.Response`; it is `_base.py` that unwraps to `.json()["data"]`,
  and v1 already shipped the bypass for exactly this reason -
  `PeakAssignmentsResource._get_envelope` calls `http_get` directly. That holds
  for a **success** response only; on a non-2xx nothing returns a `Response` at
  all, which is the subject of the plumbing note after this list.)
- **`assign_batch(batch_id, *, config=None, wait=True, ...)`** - wraps
  `POST /batch/{id}/assign`. A batch needs its **own** observable, and today has
  none: there is no batch-level run row (`PeakAssignmentRun`'s only FK is
  `sample_item_id`), no batch status endpoint, samples are assigned sequentially
  behind a concurrency-1 gate, ineligible samples produce no run at all, and
  completion is reported only in the socket notification of the aggregate
  result. Polling per-sample `list_runs` cannot fix that on its own: the wrapper
  would have to reproduce the server's eligibility partition to know how many
  runs to expect, and an all-skipped batch produces zero runs - which is
  indistinguishable from a refusal.

  So the batch endpoint's 202 body returns **the eligibility partition** - the
  admitted `sample_item_id`s, and the skipped ones with their reasons - and
  **not** per-sample run ids. The partition is computable in the request (the
  batch's samples plus `ineligible_reason` per row, no engine work), which the
  run ids are not: `_create_run` mints each id inside the engine as that
  sample's turn arrives, behind a concurrency-1 gate, minutes after the
  response. Returning ids would mean pre-creating runs in the request, and a
  pre-created run is a non-terminal run for its sample - so it is refused by
  §8.2's own admission rule, or duplicated by the engine, and a batch that stops
  early (`_assign_eligible_samples` deliberately lets `CancelledError`
  propagate) strands one blocking row per sample it never reached. The
  partition removes that whole class: nothing is pre-created, so nothing
  orphans, strands, or forces the admission rule to be widened.

  `wait=True` therefore polls **by sample**, over the admitted set the body
  named, for a completed run created after the request. The wrapper captures the
  request time and requires a newer run, so a sample's *previous* run is never
  mistaken for this batch's; skips are reported data, not errors. The ambiguity
  this replaces is gone either way, because the body lists the skipped samples
  explicitly rather than leaving zero runs to be interpreted.
  (Stage-A-only remains the batch default, `run_untargeted=False`.)
- **`verify(sample_id, peak_assignment_id, verdict, *, evidence_level=None, note=None)`**
  and **`list_verifications(sample_id)`** - thin wrappers over the shipped
  verification endpoints (§3): append-only verdicts, `evidence_level` required
  for `confirmed` (server-enforced). Verifying an imported assignment is
  supported; see the trust model for why those verdicts stay out of the
  recalibration label pool.
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
- **`import_run(sample_id, df, *, engine, engine_version, config, tier_bands, calibration)`**
  - the SDK face of §8.2. Validates the DataFrame client-side (required
  columns, enum values, owner references, and - only where the frame carries a
  `tier` column at all - tier coherence against `tier_bands` including the
  null-score exemption: fail fast before any bytes move),
  **chunks** the rows under the endpoint's per-request cap - by serialized bytes
  as well as row count, tracking the row offset `chunk.index` it resynchronises
  from - and returns the new run id. It warns when the frame covers
  materially fewer peaks than the sample's current latest-completed run, since
  the fold-in replaces rather than merges. The round trip is: `get_peaks` out,
  compute externally, `import_run` back, `get` to confirm.

  **A frame with no `tier` column is the preferred shape**, and the local check
  simply does not run on one: the server derives each row's tier from
  `fit_score`, `assigned_formula` and the declared bands (see "Tier coherence"),
  which is the whole reason the field became optional. The check below is for a
  frame that does carry one - a re-import of an exported ledger, or an engine
  that wants its own banding stated and refused if it disagrees.

  **The local tier check is one-sided, deliberately.** The bands are on the
  evidence scale and the SDK cannot compute the plausibility half of the
  product: `formula_plausibility` lives in `mascope_tools`, and the SDK depends
  on nothing beyond requests, loguru, pandas, tqdm and python-dotenv - the same
  boundary that stops adduct resolution where §8.5.2 leaves it. What can be
  checked without any chemistry is the direction that holds for every
  plausibility: evidence is never *above* the fit, so a row claiming a tier its
  bare `fit_score` already fails to reach cannot be coherent under any formula,
  and `import_run` rejects it locally. A row that clears its band on the fit is
  sent, and the server's derived evidence has the last word. Checking the other
  direction locally would be worse than not checking: a legitimately demoted
  row - strong fit, weak formula, correctly tiered low - would be refused
  before it left the client for agreeing with the server.

  **Missing values are handled per column, not by a blanket NaN sweep.** A NaN
  cannot simply be shipped and nulled server-side: `requests` serializes the
  body with `allow_nan=False`, and the resulting `InvalidJSONError` is a
  `RequestException`, so `http_post` reports a data bug as a
  `MascopeConnectionError` - a network failure for a bad cell. But nulling is
  equally wrong for two columns: `sample_peak_mz` and `sample_peak_intensity`
  are non-nullable on both the record and the table, and the in-app path fills
  0.0 rather than nulling. So the policy is per column. NaN in a nullable column
  - `fit_score`, `mz_error_ppm`, `abundance_error`, the formula, isotope and
  target-id columns - becomes null. NaN in `sample_peak_mz` or
  `sample_peak_intensity` is the caller's call to make, not the SDK's:
  `import_run` raises a typed validation error naming the column and the row,
  and never lets one reach the serializer.

  **Adduct resolution is best-effort, and the wording above is the whole
  promise.** `import_run` matches adduct notations against
  `mascope.ionization.list()` by exact string and leaves every non-match null;
  it does not guarantee that a supplied id is joinable, because it cannot
  determine that. The reason is **normalization**, not scope: the mapping the
  server applies - `to_mascope_ion_mech`, `to_custom_element_format`,
  `to_explicit_isotope_format` and the `CUSTOM_ELEMENTS` table - is backend-only
  code, and the SDK depends on nothing beyond requests, loguru, pandas, tqdm and
  python-dotenv, so a mechanism the deployment stores in custom-element notation
  resolves to null under plain string matching.

  The *scope* half of this argument no longer holds, and it is worth being
  precise about why. It used to read: validity is per sample rather than per
  deployment, because the server intersects the sample's ionization mode's
  mechanisms with the sample's polarity, while `IonizationResource.list()` is a
  deployment-global cached read. The mode clause is gone - §8.2 requires
  existence and polarity only - and polarity is something the client *can*
  check: the mechanism listing carries `ionization_mechanism_polarity` on every
  row (the endpoint even filters on it), and the sample's polarity is on the
  sample. So a client that wants to pre-empt the 422 has everything it needs to
  do so, and the residual gap is the notation mapping alone. The server's check
  stays the authority, and how much further the client goes is settled at the
  floor (§8.5.2): exact-string match, null on any mismatch.
- **`recalibrate`** - still out of scope for the SDK (superuser admin op).

**Step 5 is not purely additive: two pieces of shared SDK plumbing come with
it.** v1 was able to ship with no change to `_base.py` or `_http.py` (§5.3)
because reads are safe to retry and read errors only ever need a message. The
write wrappers are neither.

- **The mutating wrappers do not go through the blind POST retry.** `http_post`
  re-sends the same POST up to `RETRY_MAX_ATTEMPTS` (4) on `Timeout`,
  `ConnectionError` and 502/503/504, with no per-call opt-out, and
  `_base._post` routes every resource POST through it. For reads and for the
  idempotent import chunks that is exactly right; for anything not idempotent it
  is a data hazard, and one that only fires when a response is *lost* - so it
  will not show up in testing. `create_verification` is a bare insert with a
  server-minted id, and `AssignmentVerification` carries no unique constraint
  (it is deliberately append-only), so a single lost response after commit
  writes two to four identical verdicts, each of which then counts as its own
  label in `recalibrate_instrument`'s Platt fit and its `n_strong_positives`
  gate - the one failure here that silently corrupts stored data rather than
  misreporting. `assign` misreports: attempt 1 commits the run, its response is
  lost, attempt 2 is refused by the admission rule, and the wrapper raises
  "already in flight" for its own successful launch. The import's create has the
  same shape - a retried create is byte-identical to a fresh one (offset 0, no
  run id), so there is nothing to dedupe on - and its finalize is the request
  most exposed, since it runs payload-wide validation, owner resolution and the
  fold-in synchronously while both the SDK's 300 s read timeout and a reverse
  proxy's own timeout map to a retry. So `verify`, `assign`, and the import's
  create and finalize take a controlled path: no blind retry, and a lost
  response fails loudly instead of duplicating. The pattern already exists in
  the repo - `_agents.py`'s tus helper issues its mutating requests directly
  with `_raise_for_status` and its own policy - so what step 5 adds is an
  attempts/no-retry knob on `http_post` rather than four private copies.
  Finalize is additionally specified idempotent on run id plus terminal state:
  re-finalizing a `completed` run returns that run's success body rather than
  re-running the fold-in.
- **The parsed error body survives onto the exception.** A 409 "carrying the
  in-flight run id" and a 422 "naming the ineligibility reason" are not
  buildable against today's plumbing, whichever body step 3 sends.
  `_raise_for_status` fires before `http_post` can return the response and
  constructs exceptions from `(message, status_code, url)` alone;
  `_extract_error_message` short-circuits on the body's `error` string and never
  reads `detail`, which is exactly where the backend puts machine-readable data
  - `CodedHTTPException`'s own docstring says a client reacting to a specific
  condition must branch on the code, not on the user-facing prose. And 409 maps
  to no dedicated class at all, falling through to the generic
  `MascopeAPIError`, while an ineligibility 422 is indistinguishable from
  FastAPI's own body-validation 422. Step 5 therefore retains the parsed error
  body and its code on `MascopeAPIError` and adds a conflict type for 409. Step
  3 pins the other half of the handshake: the 202 nests the run id under `data`
  (the envelope's top level is not a safe place for it - `api_route` pops a
  top-level `process_id` into a response header, and `_base._post` returns
  `.json()["data"]`), and the 409/422 carry a stable code plus the in-flight run
  id in `detail`. Both halves are far cheaper to settle before the endpoints
  exist than after: ship step 3 prose-only and step 5 either parses English or
  sends step 3 back.

Corresponding notebook follow-up: the round-trip notebook of §8.4 step 6 is
where triggering gets its worked example (`assign` -> `get`, then
`import_run` -> `get`). No existing notebook is rewritten for this - the
client-side composition loop that used to live in the `09` slot is retired
(§6), so there is nothing left to demote to an appendix.

### 8.4 Sequencing

Each step is its own PR, and each leaves the system shippable:

1. **This document** - the contract.
2. **Backend import** - the `runs/import` endpoint; the migration (parented on
   `c3a9e6f2b8d1`) adding `engine` (backfilled `'mascope'`, reserved from client
   payloads), `calibration`, `tier_bands` and the `importing` status; durable
   run-state admission (409) adopted by the import *and* in-app assign paths;
   the reaper skipping `importing` and the prune gaining its grace; the abandon
   endpoint that releases a stuck assembly before that grace; the prune's
   keep-newest budget becoming per (sample, engine); strict-lite validation;
   fold-in on completion; recalibration restricted to in-app runs; and the
   import route's own `client_max_body_size` in the frontend nginx config, since
   the compiled-in 1 MB default sits below one capped chunk (§8.2). Integration
   tests exercise the write via the `MASCOPE_PEAK_ASSIGNMENT` env override, like
   the other write tests, and must cover the retention and recalibration
   changes, which are the two places an import could otherwise damage in-app
   data.
3. **Synchronous assign outcomes** - for the per-sample endpoint, the 202 body
   carrying the new run id, 409 on admission refusal, 422 on ineligibility; for
   the batch endpoint, the 202 body carrying the **eligibility partition** and a
   409 derived from the admitted samples' run state, with no run pre-created
   (§8.3, §8.5.4). It also carries the reclamation path for whichever
   non-terminal status the request creates, and pins the machine-readable shape
   of all three bodies (§8.3); both are cheap here and expensive once the
   endpoints exist. The app's two launchers currently `await` these calls inside
   a `try/finally` with no `catch`, so a refusal that used to arrive as a
   success notification becomes a rejected promise: step 3 therefore includes
   catching 409/422 at those two call sites and reporting the reason inline,
   rather than being backend-only.
4. **Run provenance in the API and the app** - `engine` **and `calibration`** on
   `PeakAssignmentRunRecord` and in the SDK's `list_runs` frame; the run
   selector showing engine, version and the calibration badge. Nothing in the
   trust model is observable to a user until this lands, so it precedes the
   first import reaching a real deployment. **Shipped.** `BaseRunProvenance`
   renders the engine chip (in-app runs included, so absence of a badge is
   never the only signal) and, for an imported run, its calibration
   disclosure; the run selector fills both its `#value` and `#option` slots
   with it, so the engine is visible on the auto-selected run without opening
   the list. The declared `tier_bands` ride on the engine chip's tooltip. The
   SDK needed no plumbing - `list_runs` is a passthrough - only its documented
   column list.
5. **SDK triggering + verification + fit** - `assign` / `assign_batch` /
   `verify` / `list_verifications` / `fit_aggregate`, the polling helper,
   hermetic unit tests.
6. **SDK `import_run`** + a round-trip tutorial notebook: read peaks, compute
   externally, publish, see the run in the app - and then **compare the two
   engines on the same sample**, which needs no further backend work: both runs
   live in the same table, so `get(sample_id, run_id=...)` twice and a join on
   `sample_peak_id` puts them side by side. Per peak that yields where the
   engines agree on a formula and where they do not, their `fit_score`s beside
   the `evidence` each tier was actually read off, their tiers (interpretable
   against each run's declared evidence-scale `tier_bands` rather than assumed
   to share thresholds), and this engine's `p_correct` beside the
   external one's under `provenance.engine_provenance`. This is the comparison
   the reserved-key rule exists to keep honest, and the notebook is where it is
   demonstrated.
7. **`peaky publish`** - the consumer, in the peaky repository (out of this
   repo's scope; listed so the sequence has its end state).
8. **Demo bundle seeds a completed assignment run** - unblocks the v1
   contract tests that today **skip** in CI (§7), and doubles as the fixture
   an import contract test diffs against.

**Start/hold status.** The list above is the order; this is what is safe to pick
up today. No step is held: §8.5's remaining entries are research questions that
can be answered once real imported ledgers exist, not prerequisites.

- **Steps 1-2 proceed.** Step 2 now also carries the import route's body
  allowance, and the reserved-provenance-key rule (§8.5.5) that its validation
  enforces.
- **Step 3 proceeds.** §8.5.4 is settled: the batch endpoint answers with the
  eligibility partition and derives its 409 from the admitted samples' run
  state, so both halves of its 202 now have machinery. The step includes the two
  frontend call sites, which stop treating a refusal as a success.
- **Step 4 has shipped.** Its prerequisite - `calibration` beside
  `engine` on `PeakAssignmentRunRecord`, without which the calibration badge has
  no data - was met: step 2 serves `engine`, `tier_bands` and `calibration` on
  the run listing. That is earlier than this plan put the API half of run
  provenance, and deliberately so. The *data* is what an import's trust model
  rests on - an import bypasses the m/z verification gate and discloses what it
  calibrated against instead, which is not a disclosure while nothing returns it
  - whereas the *badge* is what step 4 is actually about. Withholding the fields
  until the UI that renders them would have shipped an unauditable bypass in
  between. Step 4 keeps the run selector, the engine badge and the
  calibration badge. It needs no peak-inspector work: §8.5.5 cleans the blob at
  import instead, so what the inspector renders is Mascope's own or nothing.
- **Step 5 proceeds** once step 3 lands, since `assign_batch` wraps its
  partition body and polls by sample against the request timestamp. The
  retry-safety and error-body plumbing (§8.3) is independent of both and can be
  built first; the verify half of it is the only item in this revision that
  silently corrupts stored data, so it should not wait.
- **Step 6 proceeds.** §8.5.2 is settled at the floor, so `import_run` resolves
  mechanisms by exact-string match and sends null otherwise. Its other
  prerequisites are stated rather than open: the numeric chunk cap and the body
  allowance, the per-column null policy, and the null-`fit_score` exemption.
- **Step 8 is implementable, but it is not a code PR.** The demo stack fetches
  bundle content from a published Zenodo version - the init container refuses a
  registry entry with no download URL, and CI boots that same stack before
  running the SDK contract tests - so the PR cannot go green from repo content
  alone. It is publish-then-register: cut the bundle, sign off the
  de-identification, publish a new Zenodo version, then land one PR bumping
  `DEFAULT_BUNDLE_VERSION` and un-skipping the tests. That makes it maintainer
  work rather than something to hand a contributor, and the version bump forces
  a fresh full-bundle download for every demo user and every demo-stack CI job.
  Safe to schedule at any point; it does not gate the others.

### 8.5 Open questions and pending decisions (v2)

Everything in §8 that is not listed here is decided. Two kinds of thing live in
this list, and they are not interchangeable. A **decision pending** is a choice
a sequenced step cannot be implemented around: it has options, each with a
stated cost, and §8.4 says which steps it holds. An **open question** can be
answered after the loop works. Numbering is stable because the rest of the
document cites it.

1. **Batch-level import.** *(Open.)* peaky's batch pipeline merges per-sample
   ledgers into a batch-level result. Should import accept a batch manifest (one call,
   N samples' runs), or stay per-sample in v2? Per-sample is sufficient for
   the loop - the batch fold-in derives the batch view server-side from
   per-sample runs - so a manifest is a convenience with real transactional
   surface. (It is not the answer to the fold-in cost noted in §8.2; a manifest
   that loops the same fold gains nothing there.)
2. **Ionization mechanisms: how far client-side resolution goes.** *Resolved:
   keep the floor.* `import_run` resolves an adduct by exact-string match
   against `mascope.ionization.list()` and sends `ionization_mechanism_id` null
   on any mismatch; the server resolves and validates authoritatively, and its
   422 is the only fail-fast worth the name.

   The alternative was to port the deployment's notation normalizers
   (`to_mascope_ion_mech`, `to_custom_element_format`,
   `to_explicit_isotope_format`, the `CUSTOM_ELEMENTS` table) into the SDK and
   add a sample-scoped mechanism accessor. It is not worth it: it would
   duplicate chemistry into a package that depends on nothing but requests,
   loguru, pandas, tqdm and python-dotenv, and leave a second copy of the
   notation rules to keep in step with the server's - and even then a
   client-resolved id could be invalid for the *sample*, because mechanism
   validity is sample-scoped while the SDK's only handle is a deployment-global
   list. Paying that to gain a pre-check that still cannot be trusted is the
   wrong trade.

   A null costs nothing substantive: the adduct still travels in `ion_formula`
   and in provenance, so the assignment is complete and only the join is
   deferred to the server. The decision is also additive to reverse - if real
   imported ledgers arrive with mostly-null mechanism ids and that proves to
   hurt, porting the normalizers later changes no stored data.

   Consequently, **client-side validation in §8.3 does not claim to pre-empt the
   mechanism 422** - it catches shape errors, not chemistry.

   Downstream of this answer, and genuinely open: the mechanism the deployment
   does *not* know at all - auto-register it on import, or leave
   `ionization_mechanism_id` null (the notation still lives in `ion_formula` and
   provenance)? Null is safe and loses no substance; auto-registration keeps the
   ledger joinable but lets imports grow a shared vocabulary table.
3. **Admitting imported verifications to calibration.** *(Open.)* §8.2 excludes
   imported-run verifications from the instrument recalibration pool because the
   `evidence` a verdict snapshots would then rest on an editor-supplied number,
   on a superuser-gated curve. One of the two remedies this question offered has
   since landed on its own account: the server no longer takes `evidence` from
   the payload at all - it derives it from `fit_score` and `assigned_formula`
   (§8.2, "Tier coherence") - so what stays editor-supplied is the fit
   underneath, not the product, and the exclusion now stands on that alone. The
   open half is the other remedy: could imported verdicts be admitted under a
   declared-scale rule - the run's `calibration` disclosure plus a stated fit
   scale? Worth revisiting once real imported ledgers exist; not needed for
   the loop.
4. **The batch write contract.** *Resolved: the batch answers with the
   eligibility partition, and derives its refusal from per-sample run state.*
   The batch endpoint returns no run ids, and creates no runs in the request
   (§8.3). Both halves follow from one property: the partition is computable in
   the request, and run ids are not.

   **The refusal** is one indexed query over the admitted samples - the same
   non-terminal-run check §8.2 introduces per sample, as a single `IN` clause -
   answered before the 202 and naming the samples that hold it up. It needs no
   batch-level durable state, which is what makes it possible at all:
   `PeakAssignmentRun`'s only foreign key is `sample_item_id`, and today's batch
   guard (an in-process in-flight set plus the `assignment_claim` advisory lock)
   is acquired *inside* the background task, so it cannot answer a request. That
   guard stays where it is, covering the window between the response and the
   first run's creation.

   **No run is pre-created**, which is what dissolves the rest. A pre-created
   run is a non-terminal run for its own sample, so the admission rule refuses
   the batch's own samples, or the engine mints a second run and orphans the
   first; and because the batch loop deliberately lets `CancelledError`
   propagate, a batch that stops early would strand one blocking row per sample
   it never reached. Returning the partition instead leaves nothing to orphan,
   nothing to strand, and no reason to widen the admission rule everything else
   depends on.

   The cost is that `wait=True` polls by sample rather than by run id, which
   §8.3 pins with a request timestamp so a sample's earlier run cannot be
   mistaken for this batch's.
5. **Reserved keys in an imported `provenance` blob.** *Resolved: the keys the
   server derives its own judgement from are stripped at import, and an
   engine's own numbers keep a sanctioned home beside them* (§8.2, "Reserved
   provenance keys").

   Nulling the flattened P(correct) columns is necessary and not sufficient,
   because nothing reads those columns: the peak inspector renders
   `provenance.p_correct` from the stored blob under "Calibrated probability the
   assignment is correct", and the batch fold-in reads `provenance["p_correct"]`
   when rolling up consensus. Stripping is enforced once, at the write boundary,
   where it cannot be forgotten; making each reading surface engine-aware would
   have to be re-implemented correctly at every present and future read site,
   and the site that matters most is not a display but the consensus roll-up.

   Note what the server does and does not do here. It strips the three reserved
   keys and stores the rest of the blob verbatim - it does **not** relocate
   them, and a value sent under a reserved name is gone. Keeping an engine's own
   numbers is therefore the importer's job: send them under a name of your own,
   for which `provenance.engine_provenance` is this document's convention.
   Comparing this engine's calibrated probability against an external one's is a
   first-class use of the import path, so both have to survive under names that
   say which engine produced them - but only one side of that is enforced. The
   server guarantees an imported row renders no confidence of *its* own; it does
   not guarantee the importer was careful with *theirs*.

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
4. **Contract-test data.** *Resolved: seed the demo bundle* (§8.4 step 8); the
   shipped contract tests skip until it lands.
5. **`alternatives` / `provenance` after #1725.** *Resolved: a separate
   `detail(sample_id, peak_assignment_id)` accessor (shipped in v1)* - it takes
   the sample id too, matching the route shape; no `get(..., detail=True)`
   opt-in.

The open v2 questions live in §8.5. The three decisions that used to hold
sequenced steps are resolved and implemented in step 2, so nothing there gates a
step any more.

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
and version, verbatim opaque config, declared tier bands on the evidence scale,
a required calibration disclosure, and one `PeakAssignment`-shaped row per
covered peak (the read record minus the server-owned fields, so the two lists
cannot drift). It is gated like every other write on the feature flag and the
editor role, but admission is a query on durable run state rather than the
advisory claim, which cannot span a chunked upload; assembly runs under its own
`importing` status with idempotent, index-checked chunks, because the SDK
retries POSTs. Validation is strict-lite (single owner per peak, typed enums,
tier coherence against the declared bands - the server deriving each row's
evidence from its `fit_score` and `assigned_formula`, so an importer declares no
new field - peak existence via the id-only read, blank-sample and
at-least-one-row rules, per-request and total row caps; completeness
deliberately not required) with one
firm line: an import may not write the ledger's calibrated P(correct) scalars,
and verifications on imported runs stay out of the instrument recalibration
pool. Retention keeps its newest-per-sample budget **per engine**, so publishing
can no longer evict a sample's in-app history. One migration adds `engine`
(backfilled `mascope` and reserved from client payloads, so the badge cannot be
forged), `calibration`, `tier_bands` and the new status, parented on the current
head `c3a9e6f2b8d1`. Around it the v1-deferred wrappers are decided in:
`assign`/`assign_batch` (202 carrying the run id, 409 on refusal, 422 on
ineligibility, then poll-to-terminal), `verify`/`list_verifications`,
`fit_aggregate` (#1736's deliberately kept surface), and `import_run`
(DataFrame -> chunked publish -> run id). The sequence (§8.4) lands each piece
as its own PR - provenance in the app before the first real import, since the
trust model is only as good as the badge that shows it - and ends with `peaky
publish` consuming the whole loop and the demo bundle carrying a completed run,
which also un-skips the v1 contract tests. Three decisions are still pending and
hold parts of that sequence - the batch write contract, how far client-side
ionization-mechanism resolution goes, and the reserved-key policy for an
imported `provenance` blob; §8.5 states the options and their costs, and §8.4's
start/hold status maps them onto the steps they block.
