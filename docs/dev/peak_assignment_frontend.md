# Peak-Centric Assignment — Frontend Design & Implementation Plan

*The UI side of the peak-centric paradigm ([`peak_assignment_paradigm.md`](peak_assignment_paradigm.md)).
The backend inverts the unit of result from target to observed peak; this document is how the
Vue/PrimeVue frontend consumes that. It is weighted toward the technical wiring — stores, API,
socket/notification, join keys — and keeps net-new UI deliberately small.*

## Current state (2026-07-09) — shipped

> This section is the authoritative description of what shipped. The original plan (sections 0 to 7
> of this note: the settled decisions, the endpoint table, the store design, the component list, the B2
> contract and the task tables) was removed on 2026-10-22; its record is CHANGELOG 1.6.0 to 1.8.0 and
> this file's git history, and the endpoint surface it tabulated is the published OpenAPI document
> (`/docs/openapi.json`; the SDK README lists the peak-assignment routes). The work went past the
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
is a 3-pane nested splitter: the inspector in a column of its own, the tab's full height, beside
the spectrum over the time series. The column's width is saved under `sample-tab-assign-columns`
and the rows' split under `sample-tab-assign-split`; the column divider resizes both charts
through their exposed `resize()`, since it changes their width and not the `height` they watch.

- **left — inspector** ([`PanePeakAssign.vue`](../../server/frontend/src/lib/panes/PanePeakAssign/PanePeakAssign.vue)):
  a compact committed-assignment card for the focused peak — formula and its ionization,
  `BaseTierTag`, the ion formula · isotope · source line, evidence grid
  (fit, m/z error, abundance error), chemical **plausibility**, arbitration **confidence** + tie flag,
  and calibrated **P(correct)** (shown only for database-stage winners with `provenance.calibrated`;
  renders null as "uncalibrated", flags a provisional curve). Below: the isotopologue **family** table
  (M0 + children, theoretical rel. abundance, poor-match flag) and **close alternatives** (each with
  fit / m/z error / plausibility inline + on hover). No panel header; no "Verify fit" button. An
  Unassigned peak shows a minimal card with a Re-search button.
- **right, top — annotated spectrum** ([`ChartSampleSpectrum`](../../server/frontend/src/lib/charts/ChartSampleSpectrum/data.js)):
  one Plotly trace per confidence tier (+ reagent/artifact), the focused-peak and preview traces, and
  a **theoretical isotopologue envelope** overlay recovered from the stored errors. Clicking focuses
  the nearest peak; focus zooms to an **instrument-aware** m/z window (±0.05 Th orbi, ±0.3 Th tof).
- **right, bottom — assignment time series**
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
mirrors the isotopologue fold under the same two constraints, and now behind the same cog at the end
of its own tier strip — with one difference: a batch peak is a bare m/z
anchor, so the family link is **derived** (`isotopologue_of`, a member aggregate the fold rolls up;
[`peak_assignment_batch_primary.md`](peak_assignment_batch_primary.md) §12) rather than given, and it arrives ONE hop deep
pointing into a list the pane does not control — so the pane flattens a chain onto its root and leaves a
link it cannot follow at top level, rather than nesting a row under one that is never drawn. Both panes
own their sort (`lazy`) for the same reason; the batch one owns its column filtering with it, since
`lazy` switches off both. Row↔peak selection is two-way. The old
[`PaneBrowserPeak.vue`](../../server/frontend/src/lib/panes/PaneBrowserPeak/PaneBrowserPeak.vue) ledger
is not mounted **while the flag is on** — but it is still the Sample tab's ledger with the flag
off, so it is live code, not dead. Retiring it depends on the feature becoming the default.

**The switch bar** above the browser
([`PaneBrowserMatch.vue`](../../server/frontend/src/lib/panes/PaneBrowserMatch/PaneBrowserMatch.vue))
is one row holding everything that outlives a single ledger: the Targets/Assignments switch, left-aligned,
and — in the assignments paradigm — the action that fills whichever ledger is below it, pushed to the
right. Which action that is swaps on the same `sample.focused` the ledgers themselves swap on, so the
two are never both on screen: for a focused sample the **run selector** (which **auto-selects the latest
completed run**, on load and on sample switch) beside the **Assign peaks** button
([`AssignmentRunBar.vue`](../../server/frontend/src/lib/panes/PaneBrowserMatch/AssignmentRunBar.vue)),
and at batch level **Compute batch peaks**
([`BatchPeakComputeBar.vue`](../../server/frontend/src/lib/panes/PaneBrowserMatch/BatchPeakComputeBar.vue)).
Both render *inside* the flag-gated bar rather than beside it, so the `peakAssignmentEnabled`
gate and the column's height arithmetic (`.browser-switch > :not(.switch-bar)` takes what is left)
keep covering them without a second rule each. The bar shows *Assign peaks* in exactly the states the
ledger's empty state does not, so there is never a second copy of it a row below; the dialog it opens,
the run configuration and any refusal stay with the ledger, sharing only an open/closed flag
([`stores/assignmentLauncher.js`](../../server/frontend/src/lib/panes/PaneBrowserMatch/stores/assignmentLauncher.js)).
*Compute batch peaks* shares more, because it launches directly instead of opening a dialog: its
disabled reason, its loading state and the refusal it can return live in
([`stores/batchPeakCompute.js`](../../server/frontend/src/lib/panes/PaneBrowserMatch/stores/batchPeakCompute.js)),
which the batch ledger reads to render that refusal below the table it is about. Registering the
task's completion notification in that store rather than in a pane is also what lets a compute survive
the user focusing a sample mid-run. It is imported by path rather than through the folder's `./stores`
barrel: it is the only store there that reaches the HTTP client, and the barrel is imported by panes
whose specs mount without one.

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
`tierCounts` excluding iso_child; loads the ledger itself page by page) and `verification`
(the append-only verdict history: `currentByIdentity`/`forAssignment` keyed on the stable
`sample_peak_id|assigned_formula|ionization_mechanism_id` identity — not `peak_assignment_id`, which
every run regenerates — plus `verify()`). Registered nested (not spread) under
`app.data.peakAssignment.{run,peak,verification}`.

**Focus across a sample switch.** Switching samples used to clear the focused peak: the peak store's
dependency watcher resets the persisted selection and reloads, and its refocus falls through to
`unfocus()` because the old sample's peak ids match nothing in the new list. It now follows instead.
On a sample-to-sample switch the store resolves the focused peak's **batch-peak anchor** in the newly
focused sample — `GET /api/batch-peaks/records/counterpart`, two hops over `BatchPeakOccurrence` (the
counterpart read in [`batch_peaks_records.py`](../../server/backend/src/mascope_backend/api/new/peak_assignments/batch_peaks_records.py)) — and focuses the counterpart once the
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
(shared constants in [`lib/verification.js`](../../server/frontend/src/lib/verification.js)) and
the three verdict buttons, each opening a `Popover` that posts through `verification.verify()`:
every verdict takes an optional note, and Confirm's also asks for the evidence level (radio
buttons, required). **One verdict covers the isotopologue
family**: both the read and the write resolve a row to its family's M0 (`peak.m0Of`), so an isotopologue
shows its compound's verdict and verifying from one writes a single label against the M0. Backend
surface: `GET /sample/{id}/verifications`, `POST /sample/{id}/verify` (editor), and the superuser
`POST /calibration/{instrument}/recalibrate` that refits the confidence calibration from the
accumulated labels. Details in [`verification_calibration_loop.md`](verification_calibration_loop.md)
(§4.5 records the capture UI and how the current verdict is derived).

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
person chose a formula for and the isotopologues the same act stripped. The chip renders the **hand**
(`ph-hand-pointing`, `data-testid="manual-mark"`) only for the first, and an **eraser**
(`ph-eraser`, `data-testid="demoted-mark"`) for a manual row sitting at the `unassigned` tier, which is
the second: nobody chose that row's formula, and it has none to show. It tells the two apart by the tier
it is already displaying rather than by `provenance.manual.action` — the ledger serves slim rows with no
provenance on them at all, so the action is unreadable on most of the surfaces the chip renders on, and
the tier is exact here anyway, since both curation actions commit a formula and `tier_for_evidence`
never returns `unassigned`. Three things the server owns rather than the caller: the **tier** is
recomputed with `tier_for_evidence` under *the run's own* `tier_bands`, and what it bands is the
row's recomputed **evidence** — the committed fit weighted by the plausibility of the formula being
committed (below) — not that fit on its own; the engine's judgement of the displaced
winner — all nine `_ENGINE_JUDGEMENT_KEYS` (`p_correct`, `calibrated`, `calibration`, `corroboration`,
`confidence`, `n_candidates`, `is_tie`, `evidence`, `reference_identities`), not merely the calibrated
four — is archived with the winner it describes, and the curated row's provenance is rebuilt from the
candidate being committed rather than edited, so none of it is inherited: it was the engine's reading of
an arbitration that is no longer the row's. Two of the nine are then re-established for the *new* winner
out of its own record — `evidence` recomputed from the committed fit and plausibility,
`reference_identities` taken from the committed candidate — and the rest simply go. And
**isotopologues of the replaced formula are demoted** to `unassigned` (their own
previous winner kept in their `alternatives`), since an isotopologue is the same compound as its M0
and that compound is no longer what the M0 carries. Isotopologues are stripped only when the *committed*
(formula, mechanism) pair differs from the one the row held — a family belongs to a compound, and a
compound is a formula under an adduct.

**The undo is a real undo.** Each stripped isotopologue's previous state is archived on the M0's
`provenance.manual.demoted`, keyed by the (formula, mechanism) it belonged to, and committing that
compound back onto the M0 **restores them onto their own rows**. Without it, promoting the previous
winner back would return the M0 to its formula and leave the family behind as orphaned `unassigned`
peaks that only a full re-run could re-attach. A restore deliberately skips any isotopologue a person has
curated since the demotion (matched on `action == "demote_isotopologue"`, or the retired
`"demote_satellite"` that rows demoted by earlier builds carry, plus the override's own
timestamp). It reports **three** outcomes, on the curated row's `provenance.manual` and in the
response `message`: `restored` (ids put back), `restore_skipped` (ids left alone because a hand has
claimed that row since — restraint, not failure) and `restore_failed` (ids the undo could not put back
at all: the row is gone from this run or belongs to another, or the state archived for it will not go
into the columns). The last two are kept apart deliberately — reporting a failure as a skip would tell
a person their isotopologue was spared on purpose when in truth the undo never reached it, and silence
would report an undo while an isotopologue stayed demoted with nothing anywhere saying why. The two kinds
of failure part company in the *archive* rather than in the report: an entry naming a row that is gone
or is not this run's is **consumed**, since nothing later turns it back into a restorable isotopologue and
keeping it would hold one of the archive's slots to offer an undo that can only fail again; an entry
whose row is still standing and only whose archived state is unusable is **kept**, because that archive
is the one copy of a live row's previous state a curator can act on from the M0. The archive is capped
at 32 entries.

**A candidate with no adduct cannot be committed** — 422, symmetric with `set_assignment`, where
`ionization_mechanism_id` is a required field. A stored alternative that names no mechanism falls back
to the mechanism of its own target ion, so an engine runner-up from the database stage still promotes
to a complete assignment; what stores none at all is the untargeted stage's `other_candidates`
shortlist, whose entries carry a formula and a plausibility and nothing else. A formula without its
adduct is half an assignment and cannot carry a verification identity (`sample_peak_id` +
`assigned_formula` + `ionization_mechanism_id`). A mechanism the client *does* name is checked for
existence and against the sample's polarity (422 either way) — the engine only ever searches the
sample's own adducts, so a hand-supplied id is the one way an opposite-polarity one could reach the
column.

**Those shortlist entries are measured on demand** (`GET .../assignment/{id}/alternative-scores`,
`alternatives_scoring.py`). The inspector fires it when it loads a row that has any, and the server
seeds every one of the sample's adducts against every such formula in one pass of the shared Stage-A
chain (`seeded_scoring.py`, the same chain the batch ledger's propagation uses) — the adduct whose
**monoisotopic** peak lands on this row's peak with the strongest evidence is reported, ranked on
`fit x plausibility` and tie-broken by mass error. A formula no adduct places on the peak comes back
with a `blocked_reason` saying which of the three cases it is in (the sample has no adducts recorded,
the formula makes no ion, or nothing landed within tolerance), and the card says that instead of a fit.

The scores are **session data, never written onto the run** — a run is the record of what the engine
did, and this measurement is not something it did. So committing a measured entry is a
`set_assignment`, not a `promote_alternative`: its numbers are the caller's declaration, they are
re-tiered under the run's own bands, and provenance records `scored_by: composition_search`. An entry
the run itself scored still promotes out of the stored list, where the server reads the numbers rather
than being told them. Re-search remains the way past a blocked entry: it searches the peak's
composition from scratch rather than measuring the formulas this shortlist happens to name.

Store action: `peak.curate(id, action)`, which reloads the run — an override rewrites rows the caller
never named. Deliberately **not** redirected to the family M0 the way `verify()` is: an alternative
index only means something against one row's list.
**Curation never writes a verdict**, and an existing verdict does not follow the row: verification is
keyed on (`sample_peak_id`, `assigned_formula`, `ionization_mechanism_id`), so it stays attached to the
formula it judged and a curated row comes back with no current verdict. An override lives in the run it
edits and a later run supersedes it; the durable record of the judgement is still a verdict the user
records with the evidence level they have.

**Confidence.** fit, plausibility and calibrated P(correct) are surfaced (the four quantities and what
each means: [`assignment_confidence.md`](assignment_confidence.md) and the help card
[`assignment-evidence.md`](../user/_help/assignment-evidence.md)). Untargeted winners
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

One asymmetry to know before reading `n_adducts` as "how many adducts is this compound seen via": the
engine's tier gate filters which channels *contribute* to the co-occurrence set, not which rows
*receive* a boost. A confident winner's own channel is in the set, so its `n_adducts` counts itself
plus its corroborators; a `below_assignability` winner's is not, so its `n_adducts` counts only the
corroborators and can legitimately be 1. Both badges gate on `n_adducts > 1`, which hides that case -
keep the gate if you add a third surface. Evidence-tiering leaves the asymmetry itself untouched but
moves what feeds it: a tier is now read off `fit x plausibility` rather than off the fit alone, so a
formula whose chemistry drags its evidence under the band drops out of a co-occurrence set it used to
join, and a compound's `n_adducts` can differ from the count fit-tiering would have given it. (Carried over on 2026-10-22 from the retired
`peak_assignment_confidence_frontend.md`.)

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

**Open threads.** **Tier is evidence-based** (`tier_for_evidence(evidence, …)`, evidence being
fit × chemical plausibility) — settled, at both engine stages and every other site that derives a tier;
the chip's number moved with it. What is still open is the step past it: binding the tier to a
calibrated `p_correct` needs universal calibration coverage (untargeted + all instruments) —
backend/science, still deferred, and evidence-tiering is a step toward it rather than its arrival. Both
`tier` and `P(correct)` are now shown side-by-side so the discrepancy is inspectable.

**Launching a run.** One launcher, the per-sample dialog in the Assignments browser
([`PeakAssignConfigForm.vue`](../../server/frontend/src/lib/dialogs/PeakAssignConfigForm.vue)),
with the untargeted stage on: the user is looking at one spectrum and the cost is seconds.
The batch launcher (`DialogPeakAssignBatch.vue`) and the assignment copy
(`DialogCopyAssignments.vue`) were retired in the batch-primary epic: with the batch ledger
primary every processed sample folds in without a run, *Rebuild batch ledger* covers a batch
that predates it or was imported, the untargeted search runs once per batch peak, and a
species is curated once at its anchor from a derived row's inspector.


The form's bounds come from `GET /params` (`peak_assignment` defaults +
`peak_assignment_limits`), which publishes the same constants `PeakAssignmentConfig`
validates against - so an input cannot offer a value the API then rejects. Both launchers
leave untouched fields `null` and strip them before posting, so the backend default
applies rather than a null overriding it.
