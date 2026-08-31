# Peak-Centric Assignment — Frontend Design & Implementation Plan

*The UI side of the peak-centric paradigm ([`peak_assignment_paradigm.md`](peak_assignment_paradigm.md)).
The backend inverts the unit of result from target to observed peak; this document is how the
Vue/PrimeVue frontend consumes that. It is weighted toward the technical wiring — stores, API,
socket/notification, join keys — and keeps net-new UI deliberately small.*

## Current state (2026-07-09) — shipped

> This section is the authoritative description of what is on the branch. The original plan (§0
> onward) is kept below as design record; where they disagree, this wins. The work went past the
> "keep a separate Fit view" plan and **consolidated everything onto the Sample view**.

> **Everything below is behind the feature flag.** The consolidation replaced the Sample tab
> rather than adding to it, which changed the app for users who never opened the feature. So
> all of it is gated on `peakAssignmentEnabled`
> ([`features.js`](../../server/frontend/src/lib/features.js)), read from `runtime.meta` — the
> same `peak_assignment` switch the backend reads
> ([paradigm doc §5.1](peak_assignment_paradigm.md)). **With the flag off the UI is the
> pre-feature app:** the Sample tab is spectrum-over-(peak ledger | composition search),
> `PaneBrowserPeak` is mounted again, the spectrum keeps its single grey peak trace, the
> single Targets/Assignments switch is hidden (the browser stays on targets and the Batch
> overview stays on the target-ion chart), and the search reports
> the legacy match score (the backend omits fit/tier/plausibility when the feature is off).
> The two layouts keep **separate saved splitter positions** — the legacy layout stays on the
> original `sample-tab-split` key so an existing user's stored layout survives; the assignment
> layout uses `sample-tab-assign-split`.

**The Sample tab is the single workspace** (flag on). [`PaneTabSample.vue`](../../server/frontend/src/lib/panes/PaneTabSample.vue)
is a 3-pane nested splitter:

- **top-left — inspector** ([`PanePeakAssign.vue`](../../server/frontend/src/lib/panes/PanePeakAssign/PanePeakAssign.vue)):
  a compact committed-assignment card for the focused peak — formula, `BaseTierTag`, evidence grid
  (fit, m/z error, abundance error), chemical **plausibility**, arbitration **confidence** + tie flag,
  and calibrated **P(correct)** (shown only for database-stage winners with `provenance.calibrated`;
  renders null as "uncalibrated", flags a provisional curve). Below: the isotopologue **family** table
  (M0 + children, theoretical rel. abundance, poor-match flag) and **close alternatives** (each with
  fit / m/z error / plausibility inline + on hover). No panel header; no "Verify fit" button. An
  Unassigned peak shows a minimal card with a Re-search button.
- **top-right — annotated spectrum** ([`ChartSampleSpectrum`](../../server/frontend/src/lib/charts/ChartSampleSpectrum/data.js)):
  one Plotly trace per confidence tier (+ reagent/artifact), the focused-peak and preview traces, and
  a **theoretical isotopologue envelope** overlay recovered from the stored errors. Clicking focuses
  the nearest peak; focus zooms to an **instrument-aware** m/z window (±0.05 Th orbi, ±0.3 Th tof).
- **bottom (spans both) — assignment time series**
  ([`ChartAssignmentTimeseries.vue`](../../server/frontend/src/lib/charts/ChartAssignmentTimeseries/ChartAssignmentTimeseries.vue)):
  the focused assignment's family (M0 + children), or the bare focused peak, plotted per member + a
  summed trace. Data comes from the **existing per-peak REST endpoint**
  `POST /samples/{id}/peaks/timeseries` (`{peak_id}` → `{peak_id, mz, height, time}`), fetched once per
  member — **not** the old HTTP-request → socket-event path. Guarded against the sample-switch race
  (waits for `peak.pending` to settle; only plots peaks in the current sample's list). The **Re-search**
  button in the inspector flips this pane to the composition search
  ([`PanePeakSearch.vue`](../../server/frontend/src/lib/panes/PanePeakAssign/PanePeakSearch.vue),
  mounted only while active), replacing the earlier modal-dialog attempt.

**The ledger** is the Match browser's **"Assignments"** tab
([`PaneBrowserAssignment.vue`](../../server/frontend/src/lib/panes/PaneBrowserMatch/PaneBrowserAssignment.vue)):
a clickable tier-histogram filter strip and a virtual-scrolled table (m/z · intensity · formula `+N` ·
ionization · tier · **P(correct)** · verdict). Its other two view options — an **"Isotopologues" toggle** that
unfolds each compound's `iso_child` isotopologues as indented rows (children inherit the parent's tier
rank so the stable sort keeps families grouped; rows stay fixed-height so virtual scrolling holds), and
a **verdict filter** — are behind a cog at the end of that same strip, so everything that narrows the
table is on one row. A `Popover` rather than a `Menu`, so the switch keeps its `<label for>` (its only
accessible name); the verdict filter is a chip strip rather than a `Select`, because PrimeVue's `Select`
calls `stopPropagation()` in its `onEscapeKey` unconditionally while both of `Popover`'s Escape handlers
listen on the bubble path — a `Select` in there would swallow the only key that closes a panel whose
focus trap `Tab` cannot leave either. Both settings are refs in the pane, not in the overlay, so closing
the menu cannot discard a choice made in it, and the panel carries no help card (a card behind its
`v-if` is unreachable in help mode while closed and leaks a `cards` entry per open — see
`stores/ui/help.js`); one merged card sits on the always-mounted trigger instead. The run selector and
the toolbar **Assign peaks** button are not here: they belong to the paradigm rather than to one of its
ledgers, and live a row up in the switch bar (below). The ledger keeps one *Assign peaks* button of its
own, the call to action in its no-runs empty state, which is why the bar hides its copy in exactly that
state. The batch-peak ledger
([`PaneBrowserBatchPeaks.vue`](../../server/frontend/src/lib/panes/PaneBrowserMatch/PaneBrowserBatchPeaks.vue))
mirrors the isotopologue fold under the same two constraints — its own toggle is still inline in its
panel header, not behind a cog — with one difference: a batch peak is a bare m/z
anchor, so the family link is **derived** (`isotopologue_of`, §5.4 of
[`peak_assignment_batch.md`](peak_assignment_batch.md)) rather than given, and it arrives ONE hop deep
pointing into a list the pane does not control — so the pane flattens a chain onto its root and leaves a
link it cannot follow at top level, rather than nesting a row under one that is never drawn. Both panes
own their sort (`lazy`) for the same reason; the batch one owns its column filtering with it, since
`lazy` switches off both. Row↔peak selection is two-way. The old
[`PaneBrowserPeak.vue`](../../server/frontend/src/lib/panes/PaneBrowserPeak/PaneBrowserPeak.vue) ledger
is not mounted **while the flag is on** — but it is still the Sample tab's ledger with the flag
off, so it is live code, not dead. Retiring it depends on the feature becoming the default.

**The switch bar** above the browser
([`PaneBrowserMatch.vue`](../../server/frontend/src/lib/panes/PaneBrowserMatch/PaneBrowserMatch.vue))
is one row holding everything that outlives a single ledger: the Targets/Assignments switch, and — in
the assignments paradigm — the **run selector** (which **auto-selects the latest completed run**, on
load and on sample switch) beside the **Assign peaks** button
([`AssignmentRunBar.vue`](../../server/frontend/src/lib/panes/PaneBrowserMatch/AssignmentRunBar.vue)).
The run bar renders *inside* the flag-gated bar rather than beside it, so the `peakAssignmentEnabled`
gate and the column's height arithmetic (`.browser-switch > :not(.switch-bar)` takes what is left)
keep covering it without a second rule each. The bar shows the launch button in exactly the states the
ledger's empty state does not, so there is never a second copy of it a row below; the dialog it opens,
the run configuration and any refusal stay with the ledger, sharing only an open/closed flag
([`stores/assignmentLauncher.js`](../../server/frontend/src/lib/panes/PaneBrowserMatch/stores/assignmentLauncher.js)).

**The switch.** One control picks the paradigm, app-wide.
Its value is [`app.ui.matchMode`](../../server/frontend/src/stores/ui/matchMode.js) — a small
persisted UI store (`localStorage` key `mascope.browserMatch.mode`, pinned to `targets` and not
written while the flag is off) — and both the browser's panes and the **Batch** overview chart
([`PaneTabBatch.vue`](../../server/frontend/src/lib/panes/PaneTabBatch.vue)) read it. They used to
own a toggle each, and the batch tab's was unpersisted — it came back on Targets on every page
load while the browser came back where it was left, and either could be flipped without the other
moving, so the browser could sit in Assignments while the chart plotted Targets. Since the assignments chart
plots exactly what the batch-peaks ledger has selected, the ledger drove a chart that was not on
screen. An unrecognised stored value falls back to `targets` rather than being carried, so the two
consumers (which branch on opposite comparisons) cannot land on opposite sides.

**Stores** ([`peakAssignment/`](../../server/frontend/src/stores/data/modules/peakAssignment/)):
`run` (auto-focus latest completed via a list-membership watcher in the store itself;
`peak_assignment_reload` event), `peak` (`byPeakId`/`forPeak`, `childrenOf`/`familyOf`/`m0Of`,
`tierCounts` excluding iso_child; loads the ledger itself page by page — see §2.2) and `verification`
(the append-only verdict history: `currentByIdentity`/`forAssignment` keyed on the stable
`sample_peak_id|assigned_formula|ionization_mechanism_id` identity — not `peak_assignment_id`, which
every run regenerates — plus `verify()`). Registered nested (not spread) under
`app.data.peakAssignment.{run,peak,verification}`.

**Focus across a sample switch.** Switching samples used to clear the focused peak: the peak store's
dependency watcher resets the persisted selection and reloads, and its refocus falls through to
`unfocus()` because the old sample's peak ids match nothing in the new list. It now follows instead.
On a sample-to-sample switch the store resolves the focused peak's **batch-peak anchor** in the newly
focused sample — `GET /api/batch-peaks/records/counterpart`, two hops over `BatchPeakOccurrence` (see
[`peak_assignment_batch.md`](peak_assignment_batch.md) §6) — and focuses the counterpart once the
reload settles. Sameness is the anchor, never m/z proximity. The mapping is not resident in the
browser (the ledger store carries no `peak_series`, and the chart holds series only for ticked peaks),
which is why it is a read rather than a join over loaded records.

The wiring is [`peakFocusFollow.js`](../../server/frontend/src/stores/data/modules/peakFocusFollow.js),
a factory the peak store instantiates behind `peakAssignmentEnabled`. Two things carry state: an
**anchor** — the focused peak *and the sample it belongs to*, kept by a `flush: 'sync'` watcher on
`peak.focusedId`, because through a burst of switches the focused peak still belongs to the sample it
was focused in, several switches back — and a **focus epoch**, a count of focus transitions that is
how a person clearing the selection is told apart from the reload's own unfocus (the reload clears
while `pending` is still true, which is also why the watcher is sync). A follow writes only when its
generation is still the newest, the target sample is still focused, the store is neither pending nor
in error (the backstop in [`lib/store/settle.js`](../../server/frontend/src/lib/store/settle.js)
*resolves* on timeout, and a failed sync deliberately keeps
the previous sample's rows), nothing else has taken the focus, and no more than the one expected focus
transition has happened. A miss at any clause degrades to the old behaviour, silently. Lookups
supersede rather than abort — an aborted request reaches the interceptor with no response and is
console-logged as a timeout before the `errors: 'inline'` check, which is too much noise for a
one-row background read. Note that recomputing a sample's peaks mints fresh `peak_id`s, so its stored
occurrences go stale until it is folded again and the follow quietly stops working for it.

**Verification.** Assignments can be hand-labelled confirm / reject / unsure: the inspector renders
the current verdict as a [`BaseVerdictBadge`](../../server/frontend/src/lib/base/BaseVerdictBadge.vue)
(shared constants in [`lib/verification.js`](../../server/frontend/src/lib/verification.js)) with a
small verdict form posting through `verification.verify()`. **One verdict covers the isotopologue
family**: both the read and the write resolve a row to its family's M0 (`peak.m0Of`), so an isotopologue
shows its compound's verdict and verifying from one writes a single label against the M0. Backend
surface: `GET /sample/{id}/verifications`, `POST /sample/{id}/verify` (editor), and the superuser
`POST /calibration/{instrument}/recalibrate` that refits the confidence calibration from the
accumulated labels. Details in [`verification_capture_frontend.md`](verification_capture_frontend.md)
and [`verification_calibration_loop.md`](verification_calibration_loop.md).

**Manual curation.** An assignment can be replaced by hand, and the change persists in the ledger
marked as human-made. Two entry points, one endpoint
(`PATCH /sample/{id}/assignment/{peak_assignment_id}`, editor + flag): **"use this"** on a close
alternative in the inspector (`promote_alternative`, by index, guarded with the formula the card was
showing) and the **hand button** on a re-search hit (`set_assignment`, for the usual case of an
`unassigned` placeholder row with no runner-ups). The row is edited **in place** — same
`peak_assignment_id`, same peak — with the displaced winner pushed to the head of `alternatives`, so
promoting it back is the undo. `source` becomes `"manual"` (a third value in the shared
`AssignmentSource` literal, so overrides are filterable and survive an import), `BaseTierTag` marks it
on every surface, and `provenance.manual` records the user, the time, the action and the whole previous
winner. **Two marks, not one**, because `source: "manual"` covers both halves of an override — the row a
person chose a formula for and the satellites the same act stripped. The chip renders the **hand**
(`ph-hand-pointing`, `data-testid="manual-mark"`) only for the first, and an **eraser**
(`ph-eraser`, `data-testid="demoted-mark"`) for a manual row sitting at the `unassigned` tier, which is
the second: nobody chose that row's formula, and it has none to show. It tells the two apart by the tier
it is already displaying rather than by `provenance.manual.action` — the ledger serves slim rows with no
provenance on them at all, so the action is unreadable on most of the surfaces the chip renders on, and
the tier is exact here anyway, since both curation actions commit a formula and `tier_for_score` never
returns `unassigned`. Three things the server owns rather than the caller: the **tier** is recomputed
with `tier_for_score` under *the run's own* `tier_bands`; the engine's judgement of the displaced
winner — all nine `_ENGINE_JUDGEMENT_KEYS` (`p_correct`, `calibrated`, `calibration`, `corroboration`,
`confidence`, `n_candidates`, `is_tie`, `evidence`, `reference_identities`), not merely the calibrated
four — is archived with the winner it describes, and the curated row's provenance is rebuilt from the
candidate being committed rather than edited, so none of it is inherited: it was the engine's reading of
an arbitration that is no longer the row's. Two of the nine are then re-established for the *new* winner
out of its own record — `evidence` recomputed from the committed fit and plausibility,
`reference_identities` taken from the committed candidate — and the rest simply go. And
**isotopologue satellites of the replaced formula are demoted** to `unassigned` (their own
previous winner kept in their `alternatives`), since a satellite is the same compound as its M0 and
that compound is no longer what the M0 carries. Satellites are stripped only when the *committed*
(formula, mechanism) pair differs from the one the row held — a family belongs to a compound, and a
compound is a formula under an adduct.

**The undo is a real undo.** Each stripped satellite's previous state is archived on the M0's
`provenance.manual.demoted`, keyed by the (formula, mechanism) it belonged to, and committing that
compound back onto the M0 **restores them onto their own rows**. Without it, promoting the previous
winner back would return the M0 to its formula and leave the family behind as orphaned `unassigned`
peaks that only a full re-run could re-attach. A restore deliberately skips any satellite a person has
curated since the demotion (matched on `action == "demote_satellite"` plus the override's own
timestamp). It reports **three** outcomes, on the curated row's `provenance.manual` and in the
response `message`: `restored` (ids put back), `restore_skipped` (ids left alone because a hand has
claimed that row since — restraint, not failure) and `restore_failed` (ids the undo could not put back
at all: the row is gone from this run or belongs to another, or the state archived for it will not go
into the columns). The last two are kept apart deliberately — reporting a failure as a skip would tell
a person their satellite was spared on purpose when in truth the undo never reached it, and silence
would report an undo while a satellite stayed demoted with nothing anywhere saying why. The two kinds
of failure part company in the *archive* rather than in the report: an entry naming a row that is gone
or is not this run's is **consumed**, since nothing later turns it back into a restorable satellite and
keeping it would hold one of the archive's slots to offer an undo that can only fail again; an entry
whose row is still standing and only whose archived state is unusable is **kept**, because that archive
is the one copy of a live row's previous state a curator can act on from the M0. The archive is capped
at 32 entries.

**A candidate with no adduct cannot be committed** — 422, symmetric with `set_assignment`, where
`ionization_mechanism_id` is a required field. A stored alternative that names no mechanism falls back
to the mechanism of its own target ion, so an engine runner-up from the database stage still promotes
to a complete assignment; what cannot resolve one is the untargeted stage's `other_candidates`
shortlist, whose entries carry a formula and a plausibility and nothing else. A formula without its
adduct is half an assignment and cannot carry a verification identity (`sample_peak_id` +
`assigned_formula` + `ionization_mechanism_id`), so the route to committing such a formula is the
re-search hand button, which supplies one. A mechanism the client *does* name is checked for existence
and against the sample's polarity (422 either way) — the engine only ever searches the sample's own
adducts, so a hand-supplied id is the one way an opposite-polarity one could reach the column.

Store action: `peak.curate(id, action)`, which reloads the run — an override rewrites rows the caller
never named. Deliberately **not** redirected to the family M0 the way `verify()` is: an alternative
index only means something against one row's list.
**Curation never writes a verdict**, and an existing verdict does not follow the row: verification is
keyed on (`sample_peak_id`, `assigned_formula`, `ionization_mechanism_id`), so it stays attached to the
formula it judged and a curated row comes back with no current verdict. An override lives in the run it
edits and a later run supersedes it; the durable record of the judgement is still a verdict the user
records with the evidence level they have.

**Confidence.** fit, plausibility and calibrated P(correct) are surfaced (see
[`peak_assignment_confidence_frontend.md`](peak_assignment_confidence_frontend.md)). Untargeted winners
carry `plausibility` too; alternatives carry `plausibility` (database ones also fit + m/z error). Adduct
**corroboration** exists on the record (`provenance.corroboration` / `n_adducts`, folded into
`p_correct` by the backend) and is surfaced as a **"Supported by N adducts" badge** — a teal pill in the
inspector (adduct list on hover) and a compact link-icon + count beside `P(correct)` in the ledger, shown
only when `n_adducts > 1`. (The demo dataset has no multi-adduct co-occurrence, so it stays hidden there.)
The backend writes it onto **M0 winners only, by construction** — an isotopologue is the same ion measured at
another isotope, not a second sighting of the compound — so an `iso_child` row's own count is always null.
The evidence is about the formula the family shares, so the frontend resolves an isotopologue's badge from its
M0 (`owner_peak_assignment_id`; in the ledger the parent row is already in hand, in the inspector via
`familyOf`) and renders it **inherited**, saying so on its face — "Supported by N adducts **via M0**" in the
inspector (dashed pill), a **parenthesised** count in the ledger. It is deliberately not merely dimmed:
this column already spends opacity on "no calibrated value here", and a borrowed count is the opposite of
absent. Only the count carries across — the corroborating adducts are named in the M0's `provenance`, and
detail is fetched for the focused assignment alone. The inherited tooltip is also careful that the **boost
lives in the M0's `p_correct`, never in a child's**: `_fold_adduct_corroboration` rewrites M0 winners
only, so a child must not claim the probability beside it already accounts for the other adducts. The
M0's own badge now also resolves from the flattened `corroboration_adducts` before its detail arrives, so
it no longer pops in a moment late.

**The Match tab is NOT gated on the flag (reversed; was #1736).** It was retired under the flag
for one release cycle, on the reading that the Sample view's spectrum-envelope and time-series
duties covered it. They do not: the tab is the only home of the match-parameter drawer
(`SidebarMatchParams`, which persists per-ion/instrument parameters) and of *Rate Match*
(`ToolbarMatchRating`), so retiring it removed both from every deployment the flag reached —
which contradicts the "coexist, don't replace" principle the flag exists to enforce. The tab and
every navigation into it are therefore unconditional again: the ion table's "visualize ion match"
expander, the peak table's matched-isotope buttons, the batch-overview click-through, the
shared-link visualization restore, and the tab store's auto-switch. The tab stays `disabled`
until an ion is visualized, so it costs an assignment-first user nothing. Two frontend unit tests
pin the coexistence (`tab.spec.js`, `location.spec.js`). The
never-wired composition-fit UI entry point (`useMatchVisualized.verifyAssignment`) stays removed.
The B2 endpoints (`POST …/fit/aggregate`, `…/fit/visualize`) are **kept**: they work, and they
are API/SDK surface without in-app UI — the designed entry point for SDK-side assignment
verification (see [`sdk_peak_assignment.md`](sdk_peak_assignment.md), deferred `fit_aggregate`).
With the flag off the Match tab and its targeted visualization behave exactly as before the
feature landed.

**Open threads.** **Tier is fit-based** (`tier_for_score(fit_score, …)`); moving it onto
`p_correct` needs universal calibration coverage (untargeted + all instruments) — backend/science, still
deferred. Both `tier` and `P(correct)` are now shown side-by-side so the discrepancy is inspectable.

**Launching a run.** Two launchers share one form
([`PeakAssignConfigForm.vue`](../../server/frontend/src/lib/dialogs/PeakAssignConfigForm.vue)):
the per-sample dialog in the Assignments browser, and
[`DialogPeakAssignBatch.vue`](../../server/frontend/src/lib/dialogs/DialogPeakAssignBatch.vue)
opened from the sample browser's batch context menu (`dialog.assign`, rendered in
`BatchContextMenu.vue` beside the other batch dialogs). They differ only in where the
untargeted stage starts: **on** per sample, where the user is looking at one spectrum and
the cost is seconds; **off** per batch, matching `default_batch_config()` on the backend,
because batch cost scales with the number of samples. The batch dialog says so, and warns
again if the untargeted stage is switched on.

The form's bounds come from `GET /params` (`peak_assignment` defaults +
`peak_assignment_limits`), which publishes the same constants `PeakAssignmentConfig`
validates against - so an input cannot offer a value the API then rejects. Both launchers
leave untouched fields `null` and strip them before posting, so the backend default
applies rather than a null overriding it.

## 0. Decisions settled

| Question | Decision |
|---|---|
| Match tab (spectrum + isotope timeseries) | **Keep it, rename to "Fit view".** It is the visual verification that a signal fit is good. |
| Match **browser / ion table** (bottom-left) | Peak assignments go **here**. Coexist with the target/ion tables at first; **aim to retire** the `match_ion` table. |
| Tier band recalibration | Real, but **backend work** (`tier_for_score`); the UI just renders whatever tier the API returns. |
| `match_score` naming | UI labels say **"fit"** everywhere new. The `PeakAssignment` surface already carries `fit_score`. |

## 1. The backend contract (what we consume)

Thirteen endpoints, all under `/api/peak-assignments` (see
[`routes.py`](../../server/backend/src/mascope_backend/api/new/peak_assignments/routes.py)). The
seven write routes — assign (per sample and per batch), verify, **curate**, recalibrate, import, and
abandon-an-import — are additionally gated on the feature flag: they carry
`Depends(require_peak_assignment_enabled)`, which returns 403 with the feature off. The reads stay
open so ledgers written while it was on remain inspectable, and so do the two `fit/…` endpoints,
which persist nothing:

| Method | Path | Returns | Notes |
|---|---|---|---|
| `GET` | `/sample/{sample_item_id}` | `{ data: PeakAssignment[], total, results }` | Query: `peak_assignment_run_id?`, `tier?`, `role?`, `source?`, `limit?`, `offset?`. No run id ⇒ **latest completed** run. **Slim rows** — no `alternatives` / `provenance`. |
| `GET` | `/sample/{sample_item_id}/assignment/{peak_assignment_id}` | `{ data: [PeakAssignmentDetail] }` | One assignment in full (`alternatives` + `provenance`); fetched by the inspector on peak selection. |
| `GET` | `/sample/{sample_item_id}/runs` | `{ data: PeakAssignmentRun[] }` | Newest first. |
| `GET` | `/sample/{sample_item_id}/verifications` | `{ data: AssignmentVerification[] }` | Append-only verdict history, newest first. |
| `POST` | `/sample/{sample_item_id}/verify` | `201` | Record confirm / reject / unsure. Requires `editor` + flag. |
| `PATCH` | `/sample/{sample_item_id}/assignment/{peak_assignment_id}` | `{ data: PeakAssignmentDetail[] }` | Manual curation. Body is one of two actions: `promote_alternative` (`alternative_index`, optional `expected_formula` guard → 409 on a mismatch) or `set_assignment` (`assigned_formula` + `ionization_mechanism_id`, both required, plus the search's own `ion_formula` / `isotope_label` / `isotope_formula` / `fit_score` / `mz_error_ppm`). `data[0]` is the curated row, **followed by every satellite row the edit moved** — the isotopologue satellites it demoted, then the ones it restored — as full detail records, so a client can refresh what it holds without a second read. Requires `editor` + flag. |
| `POST` | `/calibration/{instrument}/recalibrate` | `{ recalibrated, ... }` | Refit the confidence calibration from labels. Superuser + flag. |
| `POST` | `/sample/{sample_item_id}/assign` | `202 { message, process_id }` | Body `{ config?: PeakAssignmentConfig }`. Requires `editor` + flag. |
| `POST` | `/batch/{sample_batch_id}/assign` | `202 { message, process_id }` | One run per eligible sample; Stage A only by default. Requires `editor` + flag. |
| `POST` | `/sample/{sample_item_id}/runs/import` | `{ data: [ImportState] }` | Publish an externally computed run, assembled over one or more chunks. `data[0]` carries `peak_assignment_run_id`, `rows`, `max_rows_per_request`, `run_status`. Requires `editor` + flag. |
| `DELETE` | `/sample/{sample_item_id}/runs/{run_id}` | `200` | Abandon an `importing` run and its staged rows, releasing the sample. Requires `editor` + flag. |
| `POST` | `/sample/{sample_item_id}/fit/aggregate` | `{ match_ions, match_isotopes }` | B2a: non-persisting composition fit (isotope table). |
| `POST` | `/sample/{sample_item_id}/fit/visualize` | `202` | B2b: composition Fit visualization over the socket. |

**Record shape** (`PeakAssignmentRecord`, one row per observed peak):

```
peak_assignment_id · peak_assignment_run_id · sample_item_id
sample_peak_id · sample_peak_mz · sample_peak_intensity · sample_peak_tof
role            M0 | iso_child | reagent | artifact | unassigned
tier            assigned | candidate | below_assignability | unassigned
source          database | untargeted | manual | null
assigned_formula · ion_formula · ionization_mechanism_id · isotope_label · isotope_formula
fit_score · mz_error_ppm · abundance_error
target_compound_id · target_ion_id        (nullable — set when the winner came from the library)
owner_peak_assignment_id                   (an iso_child points at its M0)
p_correct · p_correct_provisional · corroboration_adducts   (flattened for the ledger columns)
alternatives (JSON list) · provenance (JSON)    — detail endpoint only (~74% of a full row's bytes)
```

> **Confidence fields.** `provenance` carries the confidence story — including the calibrated
> **probability** `provenance.p_correct`. How to surface fit / plausibility / probability (and the
> upcoming adduct-corroboration signal) honestly is written up in
> [`peak_assignment_confidence_frontend.md`](peak_assignment_confidence_frontend.md).

> **`source: "manual"` and the `provenance.manual` block.** A curated row is not an engine row with a
> flag on it — `manual` is a third value of the same `AssignmentSource` literal that types the ledger's
> `source=` filter and an imported row, so an override is filterable and survives an export/import
> round trip. What made the row is under `provenance.manual` (detail endpoint only):
>
> ```
> action           promote_alternative | set_assignment | demote_satellite
> scored_by        run_alternative | composition_search   (where the row's numbers came from)
> user_id · at     who curated it, and when
> previous_formula · previous     the displaced winner, verbatim, in the `alternatives` shape —
>                                 including previous.engine_judgement, where the calibrated fields
>                                 (p_correct, calibrated, calibration, corroboration, confidence,
>                                 n_candidates, is_tie, evidence, reference_identities) are archived
> demoted          the isotopologue satellites this override stripped, each with enough state to be
>                  put back; capped at 32 entries (MAX_DEMOTED_ARCHIVE)
> restored                        what a restoring edit put back,
> restore_skipped                 what it left to a later hand (that satellite has been curated since),
> restore_failed                  and what it could not put back at all: the row is gone from this run
>                                 or belongs to another, or its archived state cannot be committed
>                                 (all three audit only — see "The undo is a real undo" under
>                                 Current state)
> ```
>
> A demoted satellite gets its own thinner block: `action: "demote_satellite"`, `reason:
> "owner_overridden"`, and `previous_owner_formula` beside its own `previous`. A curated row still
> carries `p_correct` / `p_correct_provisional` / `corroboration_adducts` as flattened record fields,
> but all three read **null** on it. They are not columns — `PeakAssignment` in
> [`models.py`](../../server/backend/src/mascope_backend/db/models.py) has no such field, and
> `_provenance_scalars` ([`service.py`](../../server/backend/src/mascope_backend/api/new/peak_assignments/service.py))
> derives them from `provenance` on every read — which is the actual mechanism by which they vanish:
> curation archives the calibrated block into `manual.previous.engine_judgement` and leaves nothing in
> `provenance` for the flattener to pick up. The calibration described an arbitration that is no
> longer the row's.

### 1.1 Two facts that drive the wiring

1. **Join key.** The peak list (`GET /samples/{id}/peaks`) returns `peak_id`; the engine stringifies
   the same column into `sample_peak_id` (`_load_sample_peaks`,
   [`service.py`](../../server/backend/src/mascope_backend/api/new/peak_assignments/service.py)).
   So the peak↔assignment join is **`String(peak.peak_id) === assignment.sample_peak_id`**. Coerce to
   string on both sides.

2. **Completion is a *notification*, not a record socket event.** `rematch_sample` emits a
   `match_reload` socket event that the `useData` events framework auto-handles. The assignment task
   does **not** — it only sends `user_notification` of `type: "assign_sample_peaks"` (a `pending`
   progress stream 0.1→1.0, then a terminal `success`/`error`), whose `data` carries
   `sample_item_id` and `peak_assignment_run_id`. So a run store cannot rely on `*_reload`; it must
   either watch that notification or we add a socket event (see §2.3, **one backend ask**).

## 2. New Pinia stores

Three modules under `src/stores/data/modules/peakAssignment/`, mirroring `modules/match/`: `run`,
`assignment` and `verification` (the last added with verification capture — see
[`verification_capture_frontend.md`](verification_capture_frontend.md); this section describes the
first two). All use the existing [`useData`](../../server/frontend/src/lib/store/data.js) composable
(deps-driven reload, selection, socket CRUD auto-registration).

### 2.1 `usePeakAssignmentRun` — the run list + selector

```js
// modules/peakAssignment/run.js
export const usePeakAssignmentRun = defineStore('app.data.peakAssignment.run', () => {
  const name = 'peak_assignment_run'
  const key = 'peak_assignment_run_id'

  const data = useData(
    name,
    ({ sample_item_id }) =>
      sample_item_id
        ? api.http.get(`/peak-assignments/sample/${sample_item_id}/runs`, {
            use: 'read', type: 'load_peak_assignment_runs'
          }).then((r) => r.data)     // handler unwraps to the array
        : [],
    {
      key,
      deps: () => ({ sample_item_id: useSample().focusedId }),
      selection: true,               // focused run == the run being viewed
      events: ['peak_assignment_reload']   // backend emits on run finalize (B1)
    }
  )

  // default focus = latest COMPLETED run (list is newest-first)
  const latestCompleted = computed(() =>
    data.list.value.find((run) => run.status === 'completed') ?? null)

  // launch a run; returns process_id, completion arrives via notification (§2.3)
  const assign = (sampleItemId, config) =>
    api.http.post(`/peak-assignments/sample/${sampleItemId}/assign`, { config },
      { use: 'process', type: 'assign_sample_peaks' })

  return { ...data, latestCompleted, assign }
})
```

### 2.2 `usePeakAssignment` — the ledger for the focused sample + selected run

**Handler caveat (updated to the shipped store).** The shared `read` handler returns
`response.data.data` — it unwraps to the body's `data` field and drops the envelope's siblings
([`handlers.js`](../../server/frontend/src/api/handlers.js)). The shipped assignment store therefore
**bypasses the `read` handler deliberately**: a run is one row per detected peak, so the endpoint
pages the ledger and the loader needs the envelope's `total` to know when it has the whole run
(`fetchAssignmentPage` + `loadAssignments` in
[`assignment.js`](../../server/frontend/src/stores/data/modules/peakAssignment/assignment.js),
`PAGE_SIZE = 1000`, hard-capped at `MAX_PAGES = 200`, dedup-by-`sample_peak_id` so a server ignoring
`offset` terminates the loop). **Run metadata comes from the run store**
(`usePeakAssignmentRun().focused`, which already holds each run's full `to_dict()`), not the
envelope. The auto-focus of `latestCompleted` lives **in the run store itself** (a list-membership
watcher in [`run.js`](../../server/frontend/src/stores/data/modules/peakAssignment/run.js)), not in
the Assignments browser, and the assignment store's deps guard against a stale run id from the
previously focused sample. The snippet below is the original design record; the shipped store
differs as described here.

```js
// modules/peakAssignment/assignment.js
export const usePeakAssignment = defineStore('app.data.peakAssignment', () => {
  const name = 'peak_assignment'
  const key = 'sample_peak_id'       // unique within a run; the peak-join key

  const data = useData(
    name,
    ({ sample_item_id, peak_assignment_run_id }) => {
      if (!sample_item_id || !peak_assignment_run_id) return []
      return api.http.get(`/peak-assignments/sample/${sample_item_id}`, {
        params: { peak_assignment_run_id },
        use: 'read', type: 'load_peak_assignments'   // → assignments array
      })
    },
    {
      key,
      deps: () => ({
        sample_item_id: useSample().focusedId,
        peak_assignment_run_id: usePeakAssignmentRun().focusedId ?? null
      }),
      selection: true
    }
  )

  // run metadata for the current view (status, config, timestamps)
  const run = computed(() => usePeakAssignmentRun().focused)

  // peak-join map + tier histogram: consumed by the ledger AND the spectrum
  const byPeakId = computed(() => {
    const m = new Map()
    for (const a of data.list.value) m.set(String(a.sample_peak_id), a)
    return m
  })
  const tierCounts = computed(() => {
    const c = { assigned: 0, candidate: 0, below_assignability: 0, unassigned: 0, reagent: 0 }
    for (const a of data.list.value) {
      if (a.role === 'reagent' || a.role === 'artifact') c.reagent++
      else c[a.tier] = (c[a.tier] ?? 0) + 1
    }
    return c
  })

  return { ...data, run, byPeakId, tierCounts }
})
```

Register both in [`stores/data/index.js`](../../server/frontend/src/stores/data/index.js) as a
namespace (nested, **not** spread — spreading a Pinia store snapshots its refs and breaks reactivity):

```js
peakAssignment: {
  run: usePeakAssignmentRun(),   // app.data.peakAssignment.run.{list,focused,assign,latestCompleted}
  peak: usePeakAssignment(),     // app.data.peakAssignment.peak.{list,byPeakId,forPeak,tierCounts,run}
  verification: usePeakAssignmentVerification() // .{currentByIdentity,forAssignment,verify}
}
```

The selection state of both stores is registered in
[`stores/data/filter.js`](../../server/frontend/src/stores/data/filter.js)
(`peak_assignment_run`, `peak_assignment`) so `useSelection` binds to the shared filter store
rather than a local fallback ref.

**Filtering** (tier/role/source) is **client-side** off `data.list` — the full ledger is already in
memory, so filter chips are instant. The server's `limit`/`offset` params are used by the store's
paging loop (§2.2); the tier/role/source query params stay unused by the UI.

### 2.3 Run-completion refresh — `peak_assignment_reload` event (decided)

The backend emits a **`peak_assignment_reload`** cross-store event when a run finalizes, mirroring the
way `rematch_sample` emits `match_reload` (backend task **B1**, §7 — implemented as
`success_reload=[("peak_assignment", "sample_batch_id")]` on the `assign_sample_peaks` decorator; the
room id resolves from the returned `_notification_data.sample_batch_id`, the same room the client
already joins for match). The run store refreshes through the existing `useData` events framework with
no component-scoped notification watcher — hence `events: ['peak_assignment_reload']` in §2.1. The event name is deliberately semantic (not
`peak_assignment_run_reload`) to match the `match_reload` precedent and to let both stores subscribe if
needed.

On the event: `usePeakAssignmentRun` re-syncs its runs; the Assignments browser then **selects the
newly completed run when the user launched it this session, otherwise surfaces a "new run available"
affordance** rather than yanking the view off a run they were inspecting. Selecting a run changes
`usePeakAssignment`'s deps, which cascades the ledger reload. (The earlier notification-watch fallback
is dropped now that the event exists; the `assign_sample_peaks` progress notification is still used
purely for the progress bar via the existing `PaneProgress`.)

## 3. Component changes (kept small)

Layout is unchanged. Most work is reframing three existing panes + one new tag + one config dialog.

| File | Change | Effort |
|---|---|---|
| [`PaneBrowserPeak.vue`](../../server/frontend/src/lib/panes/PaneBrowserPeak/PaneBrowserPeak.vue) | The **ledger**. Replace the header ratio with `tierCounts`; add a **formula + tier + source + fit** column set read from `byPeakId.get(String(data.peak_id))`; keep the legacy `match[]` buttons behind the coexistence flag. | M |
| [`PanePeakAssign.vue`](../../server/frontend/src/lib/panes/PanePeakAssign/PanePeakAssign.vue) | The **inspector**. When the focused peak has an assignment, render committed winner + evidence + `alternatives` + known-compound; demote the existing on-demand `/cheminfo/mz/match` search to a **"Re-search"** action. (The whole current file becomes the fallback path.) | M |
| [`ChartSampleSpectrum/data.js`](../../server/frontend/src/lib/charts/ChartSampleSpectrum/data.js) | **Annotated spectrum.** Split the single grey `Peak` trace into one trace per tier (color from `byPeakId`), plus a reagent/artifact trace. Focus/preview traces unchanged. Legend = trace names. | S |
| [`PaneBrowserMatch.vue`](../../server/frontend/src/lib/panes/PaneBrowserMatch/PaneBrowserMatch.vue) | Add an **"Assignments"** tab beside the existing Targets/collections view: run selector + `tierCounts` histogram + a per-peak list backed by `usePeakAssignment`. Row click ⇒ `app.data.peak.focused = <matching peak>` (drives the Sample tab). Existing `MatchIonTable` stays under a "Targets" tab. | M |
| `BaseTierTag.vue` **(new)** | 4-tier chip + `fit_score` + role icon. One shared component; keep `BaseMatchTag` for the legacy targeted view. | S |
| Run-config dialog **(new)** | `run_untargeted`, `mz_precision_ppm`, `formula_ranges`, `max_untargeted_peaks`, `peak_intensity_threshold`, `max_alternatives`. Reuse `SidebarMatchParams` patterns; submit ⇒ `run.assign(...)`. | S |
| `Dashboard.vue` tab label | `"Match"` → `"Fit"` (see §4). Help text updated. | XS |

The **inspector reads from the focused peak**, not its own selection: `app.data.peakAssignment.byPeakId
.get(String(app.data.peak.focused?.peak_id))`. No new selection wiring needed for the common path.

## 4b. B2 — composition-driven Fit visualization: endpoint contract

The Fit view makes **two** calls today, both keyed on `target_ion_id`
([`visualized.js`](../../server/frontend/src/stores/data/modules/match/visualized.js)): a synchronous
**aggregate** (isotope-table data) and a **background visualization** (spectra + timeseries pushed over
the socket). B2 adds a **composition** variant of each — keyed on `assigned_formula` +
`ionization_mechanism_id` instead of a persisted ion — so untargeted winners can be verified.

**Do not reuse `POST /match/aggregate/sample/{id}/compound`.** It calls `create_target_ions`, which
**persists** ions to the DB (verified live: it returns `400 "Failed to create target ions"` for an
ephemeral formula). B2 must be **non-persisting**: build ions/isotopes in memory with
`generate_target_ions_from_composition(TargetCompound(gen_id(), formula), [mechanism])`
([`target_ions_compute.py`](../../server/backend/src/mascope_backend/api/controllers/target/lib/compute/target_ions_compute.py)),
never `session.add` them.

### B2a — composition aggregate (isotope table)

```
POST /api/peak-assignments/aggregate/sample/{sample_item_id}
body: { assigned_formula: str, ionization_mechanism_id: str, match_params?: BaseMatchParams }
→ { match_ions: [ion], match_isotopes: [isotope] }   # NESTED shape, see below
```

Implementation (new controller; mirrors `aggregate_sample_match_ion`'s **nested** output, NOT
`aggregate_sample_match_compound`'s flat `to_dict("records")`):
1. `sample = fetch_sample(id)`; fetch the one `IonizationMechanism` by `ionization_mechanism_id`.
2. `ions, isotopes = generate_target_ions_from_composition(TargetCompound(gen_id(), norm(formula)), [mech])`
   — in memory, no persistence.
3. `target_isotopes_df = DataFrame([iso.to_dict() for iso in isotopes])`, filtered to the sample's
   resolution (`HIGH` for orbi, `LOW` for tof — `get_instrument_type(sample.filename)`).
4. `match_isotope_df = await compute_match_isotopes(sample.filename, target_isotopes_df, match_params, sample.polarity)`
   then `apply_match_params(...)`. `match_params` defaults via `default_match_params(id)`.
5. Emit the **nested** shape the Fit view consumes (copy the row-building from
   `aggregate_sample_match_ion`, lines ~139–206): `match_ions[0]` = the synthetic ion
   (`target_ion_id` = the generated id, `target_ion_formula`, `ionization_mechanism`, `match:{match_score,
   match_category, sample_peak_intensity_sum}`); each `match_isotopes[i]` = `{target_ion_id,
   target_isotope_id, target_isotope_formula, mz, relative_abundance, resolution, match:{sample_peak_mz,
   sample_peak_intensity, match_mz_error, match_abundance_error, match_score, match_category}}`. Carry the
   generated `target_isotope_id`s through so the frontend has stable keys and color-sync.

### B2b — composition visualization (spectra + timeseries)

```
POST /api/peak-assignments/visualize/sample/{sample_item_id}
body: { assigned_formula, ionization_mechanism_id, peak_min_intensity, mz_tolerance, isotope_ratio_tolerance }
→ 202 (background); emits `visualization_signal_sum_spectrum` + `visualization_signal_timeseries`
  (same socket events + trace shape ChartMatchSpectra/ChartMatchTimeseries already consume)
```

Reuse the visualization internals verbatim
([`visualization_controller.py`](../../server/backend/src/mascope_backend/api/controllers/visualization/visualization_controller.py)):
`_load_peaks_and_averaged_signal`, `_process_isotope`, the sum-timeseries logic, and the two
`sio.emit`s. The **only** change is the isotope source: instead of `_fetch_isotopes` (DB by
`target_ion_id`), build the same `list[SimpleNamespace]` from steps 2–4 above, sorted by
`relative_abundance` desc (main isotope first). Each SimpleNamespace must carry the fields
`_process_isotope` reads: `mz`, `relative_abundance`, `target_isotope_id` (the generated id, or `None`),
plus the matched fields from `compute_match_isotopes` — `sample_peak_mz`, `sample_peak_intensity`,
`match_score`, `match_mz_error`, `match_abundance_error` (`sample_peak_mz = None` for unmatched
isotopes). Recommended: extract the isotope-building (steps 2–4) into a shared helper used by both
B2a and B2b. Consider factoring `visualize_ion_focus`'s body into a
`_visualize_isotopes(sample, isotopes, …)` that both the target-ion and composition entry points call.

### F6 — frontend wiring (blocked on B2)

In `useMatchVisualized.set(...)`, branch on whether the focused assignment has a `target_ion_id`:
present → today's path; absent (untargeted) → call B2a for the isotope table (`ion`/`isotopes`) and B2b
for the charts, passing `assigned_formula` + `ionization_mechanism_id` from the `PeakAssignment` row.
Add a **"Verify fit"** action (assignments browser row / inspector) that calls
`app.data.match.visualized.set({ assignment })` and switches to the Fit tab. The chart components need
no change — B2 returns the same shapes.

**Status: backend implemented; the UI wiring is settled as retired (#1736).**
`api/new/peak_assignments/visualization.py` holds the non-persisting `aggregate_composition_fit`
(B2a) and `visualize_composition_focus` (B2b); the visualization core was extracted from
`visualize_ion_focus` into the shared `emit_isotope_visualization`. Routes:
`POST /api/peak-assignments/sample/{id}/fit/aggregate` and `.../fit/visualize`. The frontend
wiring stopped at a store function (`useMatchVisualized.verifyAssignment`) that no UI control
ever invoked; it was removed when the Fit view's retirement was settled (see Current state).
The endpoints remain API/SDK surface without in-app UI. Verified live at the API level: aggregate
returns the nested match_ions/match_isotopes for an untargeted formula; visualize emits both socket
events without error.

## 4. The Fit view rename & composition-driven visualization (decided)

Renaming the tab is cosmetic. The **functional** change: the Fit view
([`visualized.js`](../../server/frontend/src/stores/data/modules/match/visualized.js)) is driven
entirely by `target_ion_id` + `target_collection_id` (`/match/aggregate/.../ion`,
`/visualization/ion_focus`), which **untargeted winners don't carry**. Decision: the **Fit
visualization will accept a composition** (formula + ionization mechanism + sample), not only a
`target_ion_id` (backend task **B2**, §7). With it the Fit view works for *every* assignment and the
Assignments browser can offer "Verify fit" on any row.

Frontend consequence (task **F6**): `useMatchVisualized.set(...)` gains a composition branch —
when the focused assignment has a `target_ion_id` it takes today's path; otherwise it calls the new
composition endpoint with `assigned_formula` + `ionization_mechanism_id` from the `PeakAssignment`
row. The chart components (`ChartMatchSpectra`, `ChartMatchTimeseries`) are unchanged as long as B2
returns the same `{ match_ions, match_isotopes }` shape they consume today.

## 5. Labels

- New surfaces say **"Fit"** / **"Fit score"**; the tag renders `fit_score`.
- `BaseTierTag` replaces the 0/1/2 severity of `BaseMatchTag` with the 4 tiers; `BaseMatchTag` stays
  only where the legacy `match_category` is still shown (targeted view during coexistence).

## 6. Phased checklist

- **A — Read the run (ships first, GET-only).** `usePeakAssignmentRun` + `usePeakAssignment` + index
  registration; `BaseTierTag`; ledger columns in `PaneBrowserPeak`; spectrum coloring. No writes, no
  new science. Depends only on endpoints already on the epic branch.
- **B — Launch & watch.** Run-config dialog + `run.assign()`; completion refresh via
  `peak_assignment_reload` (§2.3); run selector in the Assignments browser.
- **C — Inspect & act.** Inspector `alternatives` + commit-alternative + add-to-target-list; "Re-search"
  fallback; "Verify fit" via the composition Fit view (§4). *Commit-alternative shipped later than the
  rest of C, as the manual-curation write path — see **Current state**.*
- **D — Retire the match_ion table.** Fold the Targets view into a `source=database` /
  `target_compound_id != null` filter over the ledger; remove `MatchIonTable` once parity is reached.
- **E — Batch level.** Batch-overview coloring by tier; GKA / Van Krevelen (backend Phase 4).

## 7. Work distribution

### Implementation status

All F1–F6, B1, B2 landed and **merged to `epic`** (build + lint + 119 frontend unit tests + 32 backend
engine tests green). See the **Current state** section at the top for the shipped behaviour; the table
below records the original plan items plus the consolidation that followed.

| ID | Status | Notes |
|---|---|---|
| **F1** store spine + tier tag | ✅ done | `peakAssignment/{run,assignment}.js` + `BaseTierTag`. |
| **F2** peak ledger | ✅ done, then **relocated** | With the flag on the ledger lives in the Assignments tab (`PaneBrowserAssignment`); `PaneBrowserPeak` is still the Sample-tab ledger with the flag off, so it stays live code. |
| **F3** peak inspector | ✅ done, since trimmed | `PanePeakAssign` is a compact card (no header, no Verify-fit); Re-search is a bottom-pane takeover. |
| **F4** annotated spectrum | ✅ done | Per-tier traces + theoretical envelope; instrument-aware focus zoom. |
| **F5** assignments browser + config dialog | ✅ done | + auto-select latest run, P(correct) column, unfold-isotopologues toggle. The config form was later extracted to `dialogs/PeakAssignConfigForm.vue` and shared with the batch launcher (below). |
| **F6** Fit-view rename + composition wiring | ✅ done, retired (#1736), then **un-retired** | Renamed + wired to B2. The composition-fit entry point (`verifyAssignment`) was redundant post-consolidation and is gone for good. Retiring the whole Match **tab** under the flag went too far - it took the match-parameter drawer and *Rate Match* with it - so the tab is unconditional again (see Current state). |
| **B1** `peak_assignment_reload` event | ✅ done | `success_reload=[("peak_assignment","sample_batch_id")]`. |
| **B2** composition Fit visualization | ✅ done | `visualization.py`: `aggregate_composition_fit` + `visualize_composition_focus`; kept as API/SDK surface without in-app UI (#1736). |
| **Consolidation** onto the Sample view | ✅ done | Time series via REST, 3-pane layout, Re-search takeover, inspector trim, ledger unfold, sample-switch race fix. |
| **C** commit-alternative (manual curation) | ✅ done | The one phase-C item that had no row here and no code anywhere. `PATCH …/assignment/{id}` with `promote_alternative` / `set_assignment`, backend `api/new/peak_assignments/curation.py`, store `peakAssignment/assignment.js` → `peak.curate()`, "use this" in the inspector and the hand button on a search hit. See **Current state**. |

**Verified live** against the isolated instance stack (`mascope dev run backend frontend --instance
--skip-migrations`; env `wt-…`, backend :8090, frontend :5173, seeded from the demo DB): read contract
and the `String(peak_id) === sample_peak_id` join (1:1); `POST …/assign` creating a run through to
`completed`; the REST timeseries shape; and the provenance/plausibility/alternatives fields on a fresh
run. Note: uvicorn `--reload` is unreliable in this setup — hard-restart the backend after engine
changes before trusting a live run.

### Full task list

Tasks are cut so they can be handed to separate agents with minimal collision. **F1 is the foundation**
— it freezes the store API and delivers `BaseTierTag`, which every other frontend task consumes — so it
lands first. The two backend tasks are independent of F1 and of each other and can start immediately.
Once F1 is in, F2–F5 touch disjoint files and parallelize freely.

| ID | Task | Files (primary) | Depends on | Notes |
|---|---|---|---|---|
| **F1** | Store spine + tier tag | `stores/data/modules/peakAssignment/{run,assignment}.js`, `stores/data/index.js`, `lib/base/BaseTierTag.vue` | GET endpoints (landed) | **The shared contract.** §2. Do first. |
| **B1** | `peak_assignment_reload` event | `api/new/peak_assignments/service.py` (finalize path) + the socket emit helper used by `match_reload` | — | Small. Mirror `match_reload`. §2.3. |
| **B2** | Composition-driven Fit visualization | new endpoint(s) beside `/match/aggregate/.../ion` + `/visualization/ion_focus` | — | Larger. Accept formula + mechanism + sample; return the same `{match_ions, match_isotopes}` shape. §4. |
| **F2** | Peak ledger | `PaneBrowserPeak.vue` | F1 | Reads `byPeakId`, `tierCounts`. §3. |
| **F3** | Peak inspector | `PanePeakAssign.vue` | F1 | Winner + evidence + `alternatives`; existing search → "Re-search". §3. |
| **F4** | Annotated spectrum | `ChartSampleSpectrum/data.js` | F1 | Per-tier Plotly traces from `byPeakId`. §3. |
| **F5** | Assignments browser + run selector + config dialog | `PaneBrowserMatch.vue`, new run-config dialog | F1; **B1** for live refresh | Coexist "Targets"/"Assignments"; row click focuses the peak. §3. |
| **F6** | Fit view rename + composition wiring | `Dashboard.vue` (label), `match/visualized.js` | **B2** | Rename now; composition branch when B2's contract is fixed. §4. |

**Suggested sequencing**

1. **Now, in parallel:** F1 (foundation), B1, B2.
2. **After F1:** F2, F3, F4, F5 in parallel; F5 stubs the refresh until B1 lands.
3. **After B2:** F6.

**Collision map.** The only shared frontend surfaces are `stores/data/index.js` and `BaseTierTag.vue`,
both **owned by F1** and consumed read-only thereafter. F2 (`PaneBrowserPeak`), F4
(`ChartSampleSpectrum`), and F5 (`PaneBrowserMatch`) are disjoint files; F3 (`PanePeakAssign`) is
disjoint from all of them. So post-F1 the frontend work has no file overlap.

**Branching.** Backend tasks (B1, B2) branch off `epic/peak-centric-assignment`. Frontend tasks branch
off `design/peak-centric-frontend` (which already carries this doc), or off F1's branch once it lands,
then merge back to epic. Keep each task a `feat(peak-assignments): …` / `feat(frontend): …` commit.

**Read-path note.** No backend change is needed for reads: the `read` handler's `data.data` unwrap means
run metadata comes from the runs endpoint (via the run store), not the `{run, data}` envelope. If a
future call wants the envelope's `run` inline, add a dedicated handler rather than reusing `read`.
