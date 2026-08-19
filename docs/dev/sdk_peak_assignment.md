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
(`libraries/sdk`, `mascope_sdk`). The SDK is still entirely targeted-shaped and knows
nothing about peak assignments. This document plans closing that gap.

It is an engineering design + phased plan, not a user guide.

> **Status (2026-08-03).** The one backend change this plan called for - dropping the
> dead `run` field from the ledger read (§4.1) - has **landed** on the epic branch (PR
> [#1696](https://github.com/ultra-trace-systems/mascope/pull/1696), commit `ee6217a9`). Since then
> the ledger read also gained **pagination** (`limit`/`offset` + `total`) and typed enum
> filters ([`f1e17f2c`]), and issue [#1725](https://github.com/ultra-trace-systems/mascope/issues/1725)
> will move `alternatives`/`provenance` out of the list rows into a per-peak detail
> fetch. This revision folds those in.

> **Scope decision (v1): read-only.** v1 exposes only *reading* persisted assignment
> results. It does **not** trigger assignment runs or write verifications from the
> SDK; whether the SDK should launch runs at all is an open question (runs are heavy,
> async background jobs and are launched today from the app). The write surface is
> designed here as a deferred phase (§8) so v1 doesn't foreclose it.

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

And the tell-tale one:

- **[`09_composition_assignment.ipynb`](../../libraries/sdk/src/mascope_sdk/examples/09_composition_assignment.ipynb)**
  hand-rolls untargeted assignment **client-side**: for each unmatched peak it calls
  `cheminfo.query_by_mz` -> scores each candidate with `matching.match_compounds` ->
  picks the best -> marks isotope siblings. This is *exactly* Stage B, but with **no
  arbitration, no plausibility, no tiers, no calibration, and no persistence** - a
  per-notebook reimplementation of what the engine now does properly server-side.

**Nothing in the SDK references `/api/peak-assignments/*`.**

---

## 2. What "keep the two in parallel" means for the SDK

| Capability | Path | Status |
| --- | --- | --- |
| Targeted matching against a list | `matching.*`, `get_peaks` target cols | **keep unchanged** |
| Manual client-side untargeted loop | notebook `09` | **keep** as a self-contained fallback (needs no server run) |
| Read server-side peak assignments | *new* `peak_assignments.*` | **add (this doc)** |
| Trigger assignment runs / verify | *new* write surface | **deferred** (§8) |

The two paradigms coexist at the API level too: a `PeakAssignment` with
`target_compound_id IS NOT NULL` (source `database`, Stage A) *is* the targeted result,
now peak-anchored. So "targeted vs. peak-centric" in the SDK is not two engines - it is
the targeted resources (list-driven) beside a peak-assignment resource (peak-driven read
of a persisted run).

---

## 3. The API surface v1 will read

All under prefix `/api/peak-assignments`, token-accessible (SDK-reachable):

| Endpoint | v1 | Purpose |
| --- | --- | --- |
| `GET /sample/{id}` | **yes** | peaks-with-assignments (one row/peak); **paginated** (`limit` default 1000 / max 5000, `offset`; response carries `total`); filters `peak_assignment_run_id`, `tier`, `role`, `source` (typed enums - a bad value is a 422). Envelope standardized per §4.1 |
| `GET /sample/{id}/runs` | **yes** | run history (status, engine_version, config), newest first |
| `GET /sample/{id}/verifications` | later | recorded verdicts (read-only, trivial to add) |
| `POST /sample/{id}/assign` | **deferred** | 202 launch run (async) |
| `POST /batch/{id}/assign` | **deferred** | 202 launch run for a whole batch |
| `POST /sample/{id}/fit/aggregate` | **deferred** | isotope table for an arbitrary formula+adduct (POST, but non-mutating - verify an untargeted winner) |
| `POST /sample/{id}/fit/visualize` | **never** | emits socket events; UI-only, useless headless |
| `POST /sample/{id}/verify` | **deferred** | record a verdict (write) |
| `POST /calibration/{instrument}/recalibrate` | **never (SDK)** | superuser admin op |

Reference for the record shapes:
[schemas.py](../../server/backend/src/mascope_backend/api/new/peak_assignments/schemas.py),
[config.py](../../server/backend/src/mascope_backend/api/new/peak_assignments/config.py).

The response envelope is `{status, message, results, total, data}`: `results` is the
size of *this page*, `total` is the row count across all pages (how a client knows paging
is done).

**`PeakAssignmentRecord`** columns the ledger DataFrame will carry:
`peak_assignment_id`, `peak_assignment_run_id`, `sample_peak_id`,
`sample_peak_mz/_intensity/_tof`, `role`
(`M0`/`iso_child`/`reagent`/`artifact`/`unassigned`), `assigned_formula`, `ion_formula`,
`ionization_mechanism_id`, `isotope_label`, `isotope_formula`, `source`
(`database`/`untargeted`), `fit_score`, `mz_error_ppm`, `abundance_error`, `tier`
(`identified`/`candidate`/`below_assignability`/`unassigned`), `target_compound_id`,
`target_ion_id`, `owner_peak_assignment_id`, `alternatives` (JSON list),
`provenance` (JSON).

> **Pending #1725.** `alternatives` + `provenance` are ~74% of the payload (2.8 KB/row
> vs 0.74 KB core) and are inspector-only. [#1725](https://github.com/ultra-trace-systems/mascope/issues/1725)
> will drop them from the list response and serve them via a per-peak **detail** fetch.
> The SDK design below fetches **core rows** and treats the two JSON columns as
> best-effort - present today, sourced from a detail call once #1725 lands (§5.1, §9).

**`PeakAssignmentRunRecord`** (run metadata): `peak_assignment_run_id`, `engine_version`,
`status` (`pending`->`running`->`completed`|`failed`), `config`, `error`,
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
   if we later add triggering (§8) we take on a poll-`GET /runs`-until-`completed` loop
   plus header exposure in `_http`.

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

**`alternatives` / `provenance`.** Nested-object columns today. Once
[#1725](https://github.com/ultra-trace-systems/mascope/issues/1725) slims the list response they
leave the ledger, and the SDK will source them on demand via a per-peak detail accessor
(e.g. `peak_assignments.detail(assignment_id)`) so the bulk `get()` stays cheap. Design
`get()` **core-first** now, so #1725 is an additive detail method rather than a breaking
column change (§9).

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

- **New `10_peak_assignment.ipynb`** - read + analyze a persisted run: pick a sample,
  `peak_assignments.list_runs` -> `peak_assignments.get`, inspect the `run` config on
  `.attrs`, filter by `tier` / `source`, plot a **tier-colored mass-defect** map and a
  **Van Krevelen**, break peaks down by `source` (database vs untargeted), and drill
  into `alternatives` for contested peaks. Assumes a run already exists (created in the
  app) - explicitly noted, since v1 doesn't trigger runs.
- **Keep `09_composition_assignment.ipynb`** as the **client-side untargeted fallback** -
  the roll-your-own path that needs no server run. Add a header note pointing to `10`
  as the persisted, arbitrated, tiered alternative, and framing `09` as "under the hood
  / offline" so users understand the difference. (No rewrite required, since the SDK
  doesn't trigger the server engine in v1.)
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
  test-infra dependency, and it gates the epic's own e2e demo stack too.
- **Unit tests**: the **paging loop** (accumulate pages to `total`, stable order),
  latest-completed run resolution + `.attrs` attachment, enum-filter 422 ->
  `ValidationError`, empty-run -> `None`. Hermetic (mock `http_get`), like
  `test_loaders.py`.
- **Docs**: README - new `#### mascope.peak_assignments` reference table, a
  "Peak assignments" section, project-structure entry, notebook table row; the SDK
  section of `developer_guide.md`; CHANGELOG + SDK version bump.

---

## 8. Deferred: the write surface (design so v1 doesn't foreclose it)

Left out of v1 pending the "should the SDK trigger runs?" decision. If taken up:

- **`assign(sample_id, *, wait=True, poll_interval, timeout, **config)`** and
  **`assign_batch(batch_id, ...)`** - POST the 202, then (if `wait`) poll
  `GET /runs` until the new run reaches `completed`/`failed`, returning `get(...)`.
  `config` mirrors `PeakAssignmentConfig` (`run_untargeted`, `mz_precision_ppm`,
  `formula_ranges`, `max_untargeted_peaks`, `peak_intensity_threshold`,
  `max_alternatives`, `identified_threshold`, `candidate_threshold`). Needs: header
  exposure in `_http` (Process-ID) and a polling helper. A second run for a sample
  already *in flight* is refused server-side (concurrency admission control, `f1e17f2c`),
  so `assign()` must surface that rejection rather than spin.
- **`fit_aggregate(sample_id, formula, ionization_mechanism_id)`** - isotope table for a
  composition; the untargeted analogue of `matching.match_compound`, for verifying a
  Stage-B winner. POST but non-mutating, so it could even ship in a read-plus release.
  Relates to [#1004](https://github.com/ultra-trace-systems/mascope/issues/1004) (score an arbitrary
  peak+composition) and [#1736](https://github.com/ultra-trace-systems/mascope/issues/1736)
  (retired the Fit view; kept the `fit/aggregate` + `fit/visualize` endpoints as the
  composition-verify API/SDK surface).
- **`verify(...)` / `list_verifications(...)`** - the verification-calibration capture
  loop (append-only labels). `list_verifications` is a pure read and can come first.
- **`recalibrate`** - superuser admin op; out of scope for the SDK.

Corresponding notebook follow-up: rewrite `09` to *drive* the server engine
(`assign` -> `get`) once triggering exists, demoting the client-side loop to an appendix.

---

## 9. Open decisions

1. **Should the SDK ever trigger runs?** (Drives whether §8 happens and whether `_http`
   grows async/header support.) Leaning: read-only until a concrete notebook/user need
   appears; runs are heavy and better launched from the app.
2. **`load_assignments` on samples with no run.** Skip-and-log (recommended) vs. raise.
3. **Reference identity surfacing.** Once the reference x peak-centric convergence
   ([reference_peak_assignment_convergence.md](reference_peak_assignment_convergence.md))
   attaches reference identities to `source=database` assignments, decide how the SDK
   exposes them (a `provenance`/`identity` column vs. flattened name columns).
4. **Contract-test data.** Seed a persisted run into the demo bundle vs. provision one
   in the test - coordinate with the epic's demo-stack e2e.
5. **`alternatives` / `provenance` after #1725.** Confirm the detail-fetch shape (keyed by
   `peak_assignment_id`? batched?) and whether the SDK exposes a separate
   `detail()` accessor or a `get(..., detail=True)` opt-in. Track with
   [#1725](https://github.com/ultra-trace-systems/mascope/issues/1725).

**Related tracker issues:**
[#1725](https://github.com/ultra-trace-systems/mascope/issues/1725) (ledger response slimming -
reshapes the read),
[#1494](https://github.com/ultra-trace-systems/mascope/issues/1494) (SDK peak dataframe: add target
collection name),
[#1044](https://github.com/ultra-trace-systems/mascope/issues/1044) (batch-level aggregation /
assignment, notebook),
[#1004](https://github.com/ultra-trace-systems/mascope/issues/1004) (score an arbitrary
peak+composition - the deferred `fit_aggregate`),
[#1736](https://github.com/ultra-trace-systems/mascope/issues/1736) (retired the Fit view; the
composition-verify endpoints stay as API/SDK surface).

---

## 10. Summary

The SDK is the last untouched surface of the peak-centric epic. v1 is deliberately
**read-only and additive**: one new `peak_assignments` resource (`get` + `list_runs`,
DataFrame with run on `.attrs`, paging hidden inside `get()`), one high-level
`load_assignments` loader, and one new read/analyze notebook - with **no shared SDK
plumbing change**. The one backend edit this plan needed - dropping the dead `run` field
from the ledger read (§4.1) - has already **landed** in PR #1696. Targeted matching and
the client-side untargeted notebook stay exactly as they are, so the two paradigms run in
parallel with no adaptation friction. Triggering runs and verification are designed here
but deferred behind the "should the SDK trigger heavy async runs?" question. Live
dependencies: sequencing on #1696, the paginated read (§4 constraint 2), the pending
ledger slimming (#1725) that moves `alternatives`/`provenance` to a detail fetch, and a
demo stack that carries a completed run for the contract tests.
