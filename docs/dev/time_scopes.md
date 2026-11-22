`ULTRA TRACE MASCOPE - DESIGN DOC - TIME SCOPES AND DASHBOARDS`

# Time scopes and dashboards: viewing data by time, not by batch

- **Status:** design
- **As of:** 2026-11-18
- **Tracked in:** [Time scopes and dashboards](https://linear.app/ultra-trace/project/time-scopes-and-dashboards-e5387f5d08e6)
  (Linear, P-MAS-22: the decisions MAS-1020 to MAS-1024 and MAS-1038, the steps MAS-1025
  to MAS-1037 across milestones 0 to 5, filed 2026-11-18; decision MAS-218 of
  *Visual digests* is answered by this note)
- **What:** The scope as the unit of viewing beside the batch as the unit of analysis: a time window, a range across batches or the live present, drawn by the visual digests' Figure spec in the app, and saved as a dashboard.

Status: **design, nothing built** (2026-11-18). The owner set the direction on
2026-11-18: users ask about time, not about the hierarchy, and the ionization
chemistry is a measurement mode to overlay, not a wall between figures. Code
anchors were read on `develop` at 571e05051 and are named where a claim rests
on them. Section 9 lists what is open, each item with a recommendation; the
Linear project carries each as a Decision issue that gates the step it decides.

## Picking this up

The feature, as a person meets it:

- **A time control on every view.** Under the top bar a scope strip names the
  instrument, the chemistries and the time range, with presets (30 minutes,
  24 hours, 7 days, 30 days, 6 months, this batch, now, custom) and a coverage
  strip to brush on that shows where files exist and where they do not.
- **Explore.** A destination in the navigation sidebar that opens an unsaved
  scope: the species seen in the range on the left, with the days they appear
  on, their median and a trend; the charts of the species a person selects on
  the right, binned to the range, the engine run and the gaps marked. No batch
  is built. *Make a batch of this range* hands the scope to the sample finder
  when curation is wanted.
- **Chemistries overlaid.** A compound measured with two reagent ions is one
  row with two series; a scope may span the chemistries of an instrument and
  a chart may draw them in one figure.
- **Dashboards.** *Save as dashboard* stores the scope and the chart sections
  as a recipe. A saved dashboard renders live from today's data, is listed in
  the sidebar, can be shared to a workspace, and renders the same recipe to
  the PDF report the visual digests package produces.
- **Now.** A scope whose end is open follows the instrument: the latest sample
  or the last N minutes, refreshing as files arrive.
- **Two modes, one rule.** The batch view is where results are made and
  corrected; Explore and Dashboards are where they are read. Data is changed
  in a batch and read in a scope, and three bridges join the two (section
  4.6).

The design in one paragraph. Mascope's hierarchy workspace -> dataset -> batch
-> sample is right for what it does: a batch is where anchors are frozen, a
ledger is folded, verdicts are recorded and target collections attach. It
should stop deciding what a person may look at. A **scope** is the unit of
viewing: this batch, a window inside it, a range across batches, or the live
present. A scope owns no results; it reads the ledgers of the batches it
resolves to, by a series identity that survives the batch boundary. Server-side
rollups written at the ingest fold make a six-month range as cheap to draw as
a day. A dashboard is a saved scope with chart sections, which is the visual
digests' recipe, so one Figure spec serves the app, the HTML dashboard and the
PDF report.

---

## 1. Why

Every chart in the web app is scoped to one batch, and in the acquisition
workspaces a batch is one instrument-day of one chemistry
([`visual_digest.md`](visual_digest.md) section 1.1; `GET /api/samples`
refuses a call that names neither a batch nor a sample,
[samples_routes.py](../../server/backend/src/mascope_backend/api/routes/samples/samples_routes.py)).
The questions users bring have another shape:

| Question | Who | Scope |
|---|---|---|
| How did this compound behave over the last six months? | atmospheric site | a range across about 180 daily batches |
| What happened in the last thirty minutes? | chamber study | a window inside one batch |
| What is in the sample that just arrived? | object headspace | the live present |

Today each is answered by building a batch for it, which the sample finder
makes easier but which its own note calls the cost of its first decision: "a
trend over six months is seen only after a batch exists and has been matched"
([`sample_filters_and_metadata.md`](sample_filters_and_metadata.md) section 8).
The visual digests package answers the first question outside the app, from a
notebook or a report, and left whether the app itself should as decision 8
(MAS-218). This note answers it: yes, and the window and live scopes come with
it.

## 2. Current state

### 2.1 What exists and is reused

- **The hierarchy and the ledger.** Acquisition datasets per (workspace,
  instrument, year), daily acquisition batches per (dataset, day, ionization
  mode) with polarity in the natural key
  ([`sample_filters_and_metadata.md`](sample_filters_and_metadata.md)
  section 2.2); the ingest design's next cut adds one batch per scan range.
  Every ingested sample folds into its batch ledger at
  `fold_sample_into_batch_peaks`, the fold that reads the sample's assignment
  rows, writes its occurrences and recomputes each touched anchor's consensus
  (`fold_in_sample` is its pure, in-memory anchor step)
  ([batch_peaks.py](../../server/backend/src/mascope_backend/api/new/peak_assignments/batch_peaks.py),
  [batch_peaks_controller.py](../../server/backend/src/mascope_backend/api/new/peak_assignments/batch_peaks_controller.py)),
  so a batch ledger holds one row per (batch peak, member sample) with the
  assigned formula, adduct and intensity.
- **Two compact series routes per batch**, token-accessible since the visual
  digests' phase 0: target-ion series (`POST /api/match/records/ion/series`)
  and batch-peak series (`POST /api/batch-peaks/records/series`).
- **A scope and a series identity**, in `mascope_viz.data`
  ([`visual_digest.md`](visual_digest.md) sections 2.2 and 2.3): `Scope`
  (dataset, batches, instrument, start, end, polarity, mode, collection, tz),
  resolved on the client through the SDK; series keys `ion:<target_ion_id>`
  and `formula:<formula>|<adduct>`; a client-side parquet campaign store with
  hourly and daily rollups.
- **A Figure spec** of eight primitives rendered by matplotlib and plotly,
  and a plan for the browser to render it too (section 2.8 of that note: a
  figure endpoint and a Vue renderer, neither built).
- **The location chain** (`lib/location/`,
  [`ui_state_persistence.md`](ui_state_persistence.md)) that a reload and a
  shared link restore, and the continuous URL mirror filed as MAS-717.
- **A recipe on a batch** built from a filter (`sample_batch.recipe`, MAS-306)
  and the finder's filter grammar.
- **A socket event per file**, `acquisition_updated`, consumed by the Raw
  files list
  ([acquisition.js](../../server/frontend/src/stores/data/modules/acquisition.js)).
- **The navigation sidebar and chemistry-first main area** decided under
  MAS-161 ([`ux_review_2026_10.md`](ux_review_2026_10.md) section 4.4): a
  sidebar of destinations and the outline, the ledger as the first content
  column, charts beside it.

### 2.2 What is missing

Nothing reads across batches on the server. A range of 180 days is 180 calls
to each series route and millions of points; the campaign store does the
binning on the client, which the app cannot do for every user on every view.
No route resolves a time range to batches. No component draws a Figure spec.
Nothing stores a saved view. No view listens to the file socket to follow an
instrument.

---

## 3. Decisions

Set by the owner on 2026-11-18, from the review that proposed this design;
8 and 9 follow from decisions already taken elsewhere. MAS-1020 is the record.
Decisions 10 to 13 were set on 2026-11-22; each answers an open question of
section 9.

| # | Decision |
|---|---|
| 1 | **The batch is the unit of analysis; the scope is the unit of viewing.** A scope is this batch, a window inside it, a range across batches, or the live present. Anchors, ledgers, verdicts and target collections stay on the batch. |
| 2 | **A scope owns no results.** It reads the ledgers of the batches it resolves to. The finder's decision 1 stands: curation across a range still means a batch, made from the scope with one action. |
| 3 | **Chemistry is a facet, not a partition.** For atmospheric work the reagent ion is a measurement mode of the instrument, not a property of the air. A scope spans any set of the instrument's chemistries, defaulting to all; the series of a compound is its neutral formula, and each chemistry's adduct is a facet of it that a chart overlays, splits or filters. A chart that mixes chemistries names them and never normalises across them silently. |
| 4 | **Rollups live on the server and are written at the fold.** Hourly and daily statistics per series per batch, maintained where a sample folds into its ledger, backfilled once. The client-side campaign store stays for notebooks and scheduled reports; the app never downloads it. |
| 5 | **A dashboard is a saved recipe rendered live.** One Figure spec, one recipe schema, three outputs: the app, the HTML dashboard, the PDF report ([`visual_digest.md`](visual_digest.md) section 2.8). A recipe stores chart kinds, parameters and a scope, never data. |
| 6 | **Saved recipes are the user's, shareable to a workspace.** Stored on the server; the sidebar lists them. The package's shipped recipes stay files. |
| 7 | **The live scope is asked for.** A person switches a scope to *now*; nothing follows the instrument unasked, and a dashboard remembers whether it follows. |
| 8 | **The scope strip shows the instrument's acquisition time zone** by default, the zone on the axis, a setting to switch: the visual digests' decision 6, applied to the app. |
| 9 | **A scope spans one instrument** until the tier-3 `comparison` chart exists (MAS-244); two sites side by side is that chart's job, not phase 2's. |
| 10 | **The scope routes are the only cross-batch read.** `GET /api/samples` keeps its contract and gains no time-range form; the finder's search route is the sample-level query over a range (decided 2026-11-22, MAS-1021). |
| 11 | **A dashboard is shared as a target collection is.** Any editor of the workspace shares; the owner edits; anyone else copies it to change it, so a shared view never changes under its readers (decided 2026-11-22, MAS-1022). |
| 12 | **A dashboard follows the batches' current runs.** The run is named on each chart, and a marker on the axis shows where it changes; a report pins by being a snapshot (decided 2026-11-22, MAS-1023). |
| 13 | **The finder's time histogram and the scope strip's coverage strip are one component** with two data sources. Whichever of the two lands first builds it; the other adopts it (decided 2026-11-22, MAS-1024). |

Not chosen, and why:

- **A batch per view.** The finder note's remedy, "make a batch cheap to build
  and to throw away", does not scale to a six-month question asked daily; its
  own row-growth risk says why.
- **Embedding stored digest bundles in the app** (MAS-218's option b alone).
  A bundle is a snapshot; a dashboard that cannot follow today's data is a
  report. The renderer that would embed one is the same component that draws
  live, so (b) is inside (a).
- **Re-keying results by (sample, context)** to make a scope own results. The
  finder note rejected it as a large refactor for a narrow gain; decision 2
  keeps that.

---

## 4. The model

### 4.1 The scope

```json
{
  "v": 1,
  "instruments": ["<instrument_id>"],
  "modes": ["<ionization_mode_id>", "..."],
  "polarity": null,
  "range": {"kind": "window", "batch": "<sample_batch_id>", "t0": "…", "t1": "…"},
  "tz": "instrument"
}
```

`range.kind` is one of:

| kind | Fields | Resolves to |
|---|---|---|
| `batch` | `batch` | that batch; the app today |
| `window` | `batch`, `t0`, `t1` | that batch, narrowed to the samples whose mid-time falls in the window |
| `range` | `start`, `end` | the acquisition batches of the instruments and modes whose day intersects the range, and the files in the range for coverage |
| `now` | `last` (a duration) or `samples` (a count) | the batches of the trailing window, re-resolved as files arrive |

`modes` empty means every chemistry of the instruments (decision 3). A scope
with one instrument, one mode and `kind: batch` is exactly a focused batch, so
the existing location chain is a scope with the default kind and nothing the
app does today changes shape. The scope joins the serialised location
(`lib/location/schema.js`) so a reload, a shared link and the URL mirror carry
it. The finder's filter grammar and the scope share the time range, the
instruments and the modes; the finder adds m/z and metadata clauses and
materialises, the scope does not.

### 4.2 Series identity across batches and chemistries

The visual digests' two keys stay, and a third groups them (decision 3):

| Series key | Identity | Facets |
|---|---|---|
| `ion:<target_ion_id>` | a target ion of a collection | the collection's mode |
| `formula:<formula>\|<adduct>` | an assigned formula with its adduct, from the ledger | the adduct, hence the mode |
| `compound:<neutral formula>` | the neutral compound, across adducts | one series per adduct and mode present in the scope |

A chart over a `compound:` key draws one line per adduct by default, with the
mode in the legend, and offers *overlay*, *split* and *one mode* as its
facet control. A cross-chemistry ratio or a normalisation that spans modes is
a chart's explicit choice, named in its title, never a default. A series
carries the batch's engine run per bin; a change of run is a marker on the
axis, as the package's charts already draw it.

### 4.3 Rollups at the fold

A table `series_rollup`: one row per (batch, series key, bin width, bin
start) with `n`, `median`, `mean`, `p10`, `p90`, `max`, the mode and the run
id, at two widths, one hour and one day. A table `series_catalog`: one row per
(instrument, mode, series key) with the neutral formula, adduct, first and last
seen, days seen, so the species-in-range list is a lookup.

Both are written from two hooks in
[batch_peaks_controller.py](../../server/backend/src/mascope_backend/api/new/peak_assignments/batch_peaks_controller.py),
and the rule has three parts:

1. **A fold writes the folded sample's bins** for every series the sample
   carries. The hook is in `fold_sample_into_batch_peaks`, the fold that reads
   the sample's assignment rows and writes its occurrences.
2. **A consensus change rewrites the batch peak.** A `formula:` series is a
   batch peak's *consensus* formula and adduct, and they move on a new
   sample's vote, a curation, an import, an untargeted or series pass, a
   known-species, water-ladder or merged-isotopologue pass, or a re-fold.
   Those paths reach the consensus by two routes: the fold and the
   known-species, water-ladder and merged-isotopologue passes call
   `_recompute_consensus`; curation, import, the untargeted and series passes
   and the deferred backfill call `recompute_batch_consensus`. Neither calls
   the other, and both chunk their anchors through
   `_recompute_consensus_chunk`, the one caller of `_apply_consensus`,
   inside the caller's transaction. That is where the hook sits. It reads
   each anchor's `consensus_formula` and `ionization_mechanism_id` before
   `_apply_consensus` runs, since that function returns only whether any of
   its columns was written and overwrites the old values in place; after it,
   the hook collects the anchors whose formula or mechanism moved, and the
   anchors the chunk deleted for having no members left, whose old
   key loses their intensities too. For each it recomputes the rollups of
   the old and the new key over the whole batch, and the catalog's two rows
   follow. Hooking the fold alone, or either of the two callers above the
   chunk, would miss a path, and a six-month chart would drift from the
   ledger with nothing to show it.
3. **A ledger rebuild or a run change recomputes the batch.** A one-off
   backfill walks every acquisition batch through the same code.

Raw cadence inside a window comes from the existing series routes, which a
day's scope uses as it does today.

Size: 300 series at hourly bins is about 2.6 million rows per instrument, per
mode, per year, and 110 thousand at daily bins; well inside Postgres on the
fleet's servers, measured before the backfill is run on a production server
(section 7).

### 4.4 Routes

| Route | Takes | Returns |
|---|---|---|
| `POST /api/scopes/resolve` | a scope | the batches, the files in range with gaps, the modes present, the runs present |
| `POST /api/scopes/species` | a scope, a filter (targets, tier, text) | the catalog rows in range with days seen, median and a trend sketch |
| `POST /api/figures` | a chart kind, a scope, parameters | a Figure spec with binned data, from `mascope_viz.data` and `charts` run on the server (the renderers excluded; the package split exists for this) |
| `GET`/`POST`/`PATCH`/`DELETE /api/dashboards` | a recipe: name, scope, sections, sharing | the user's and the workspace's dashboards |

All four are token-accessible, so the SDK and the AI tools use them; the
figure route is what `mascope_ai.chart` calls for a live figure.

### 4.5 The UI

Within the shell decided under MAS-161 (the sidebar, the chemistry-first main
area; `docs/dev/frontend_design_system.md` section 9 has the components):

- **The scope strip** under the top bar on every view: instrument and mode
  chips, the range presets, the coverage strip with a brush, the count of
  batches and gaps. On a batch view it reads *this batch*; brushing it makes a
  window.
- **Explore**, a sidebar destination: the species-in-range list in the
  ledger's shape (compound, modes present, days, median, trend) on the left;
  the selected species' charts on the right through the Figure renderer, with
  the facet control of 4.2; *Save as dashboard* and *Make a batch of this
  range* in the top bar.
- **Dashboards**, a sidebar section listing the saved recipes; a dashboard
  view is a grid of Figure-renderer panels under its scope strip, with
  *Edit* (add, remove, reorder sections; change parameters), *Share* (to a
  workspace) and *Report as PDF*.
- **Now** as a preset on the strip, with a following indicator; the view
  refreshes on the file socket.
- **The Figure renderer**, one Vue component drawing a Figure spec with
  plotly.js beside the hand-written navigation charts, sharing the theme.

The mockups are frames F and G of the navigation review of 2026-11-05, kept
in the owner's artifacts; the frames are the design, the measures are the
implementation's to move.

### 4.6 Two modes, one invariant

The design has two modes, and the shell should say so rather than let a
person infer them from what the main area happens to hold.

- **The batch view** is where results are made and corrected. Its unit is one
  batch because that is the unit the engine computes over; its evidence is per
  sample and per peak, a spectrum, an isotopologue envelope, a fit; its
  controls write: verdicts, re-search, calibration, target collections. It
  follows the data model closely because it has to.
- **Explore and Dashboards** are where results are read. A scope resolves to
  batches and draws their ledgers through the series identity; it owns
  nothing and writes nothing.

The invariant a user learns once: **data is changed in a batch and read in a
scope.** The names users meet are places, not modes: *Explore* and
*Dashboards* in the sidebar, and a batch by its name in the outline. There is
no mode switch, because the sidebar already is one.

The shell signals the mode in three ways. The breadcrumb's root: the workspace
chain in a batch, *Explore* or the dashboard's name otherwise. The scope
strip's form: *Batch* and *Window* are live on a batch, the range presets are
live in Explore, and picking a range on a batch hands over to Explore with the
batch's instrument and modes carried along. The write controls: verdict cells,
the inspector and re-search are present in a batch and absent in Explore.

Three bridges keep the two modes one app:

1. **From Explore to a batch.** A click on a series point opens that sample
   in its batch with the peak focused; a species row's menu offers *Open in a
   batch*, the brightest sample of the species in the range.
2. **From a batch to Explore.** A ledger row or the inspector offers *Explore
   this species over time*, opening Explore with the compound preselected and
   the batch's instrument and modes as the scope.
3. **From Explore to curation across a range.** *Make a batch of this range*
   hands the scope to the finder. Whether a verdict may instead fan out from
   Explore to every batch peak in the range, behind an approval, is open
   question 5; until it is answered the first bridge is the curation path,
   one batch at a time.

The user documentation's concepts page states the two modes and the invariant
when the shell ships (MAS-712, MAS-713, MAS-1031).

---

## 5. API and SDK surface

The SDK gains `mascope.scopes.resolve(scope)`, `mascope.scopes.species(scope)`
and `mascope.figures.render(kind, scope, **params)` returning the Figure spec,
and `mascope.dashboards` for the saved recipes. `mascope_viz` gains a server
mode in which `Scope` resolution and the views read the rollup routes instead
of the campaign store, so a chart kind runs the same code on both sides.
`mascope_ai.chart` renders live through the figure route and `digest` keeps
the bundle.

## 6. Schema changes

`series_rollup` and `series_catalog` (4.3); `dashboard` (id, owner, workspace
or null, name, recipe JSON, created, modified); the scope in the location
schema (`v` bumped). No change to `sample_batch`, the ledger tables or the
finder's recipe column.

---

## 7. Phases

Each lands independently and leaves the app usable. Where a phase depends on
another project, the dependency is a `blockedBy`, filed when the phase is.

| Phase | What ships | Depends on |
|---|---|---|
| 0 · The window (MAS-1025) | The scope strip on the batch view with the brush; the window in the location; the ledger and charts narrowed to it. Client-side. | the main area of MAS-713 (or the batch view as it is, if earlier) |
| 1 · The renderer on a day (MAS-1026, MAS-1027) | The Figure renderer component; the figure route over a `batch` or `window` scope, reading the series routes the batch charts use today. | visual digests phase 2 (the spec and the tier-1 charts) |
| 2 · The range (MAS-1028 to MAS-1031) | `series_rollup`, `series_catalog`, the fold hook and the backfill; the resolve and species routes; Explore over a range with the facet control. The piece that is beyond the UI. | phase 1; measured per section 8 before the backfill runs on a production server |
| 3 · Dashboards (MAS-1032 to MAS-1035) | The `dashboard` table and routes; the Dashboards section; Edit, Share, Report as PDF; `mascope_ai` saving a recipe from a conversation. | phase 2; the visual digests' digest and PDF (phase 3 there) |
| 4 · Now (MAS-1036) | The `now` kind, re-resolved on the file socket; the following indicator. | phase 2 |
| 5 · The acquisition outline as a calendar (MAS-1037) | The sidebar shows an acquisition workspace as a coverage calendar rather than year and day nodes. Optional. | MAS-712 and phase 2 |

Phase 0 is small and independent; phases 1 and 2 are the project; 3 to 5
follow.

---

## 8. Costs and risks

- **Rollup volume and the fold's latency.** Section 4.3's estimate is per
  instrument and mode; a fleet server with four instruments and three
  chemistries at hourly bins holds about 30 million rows a year. Measure the
  fold's added time, the consensus recompute's cost (a changed consensus
  rewrites one batch peak's rows for every member under two keys, up to a
  few thousand rows, and a curation can change many peaks at once) and the
  table's size on a copy of a production database before the backfill is
  scheduled; daily-only rollups beyond a year are the fallback.
- **Series drift across runs.** A `formula:` series is stable while the
  engine agrees with itself; a run change can rename a compound. The run is a
  marker on the axis and a filter; a chart never joins two runs silently.
- **Mixed chemistries read as one.** Decision 3 makes overlay the default
  for a `compound:` key. The legend names the mode on every line and a
  normalisation across modes is never applied by default; the risk is a
  reader comparing intensities that are not comparable, and the mitigation is
  the labelling, not a refusal.
- **A live scope as a load.** `now` re-resolves per file event; one instrument
  at one sample a minute is one request a minute per open dashboard. A
  debounce and a shared resolution per scope key keep it bounded; measured in
  phase 4.
- **Two time axes.** Within-file time (seconds since the file's start) and
  acquisition time are different axes; a window is in acquisition time and
  the within-file series stays what it is today.

---

## 9. Open questions

| # | Question | Recommendation | Decision issue |
|---|---|---|---|
| 1 | Does `GET /api/samples` get a time-range form, or is the scope route the only cross-batch read? | The scope route only; the samples route keeps its contract and the finder's search route is the sample-level query. | MAS-1021: decided 2026-11-22 as recommended, decision 10 |
| 2 | Who may share a dashboard to a workspace, and who may edit a shared one? | As target collections: any editor of the workspace shares; the owner edits; a shared one is copied to edit. | MAS-1022: decided 2026-11-22 as recommended, decision 11 |
| 3 | Does a dashboard pin the engine run, or follow? | Follows, with the run on its face; a report pins by being a snapshot. | MAS-1023: decided 2026-11-22 as recommended, decision 12 |
| 4 | Does the finder's time histogram and the scope strip's coverage strip become one component? | Yes; the finder's histogram is the coverage strip with counts. | MAS-1024: decided 2026-11-22 as recommended, decision 13 |
| 5 | May a verdict fan out from Explore to every batch peak of a range, behind an approval, or is a batch the only place to curate? | Not before the dashboards' pilot shows the need; until then a series point opens its sample's batch. The batches would still own the results. | MAS-1038, gates nothing yet |

The time zone and the single-instrument scope, open in the first draft, are
decisions 8 and 9: both followed from choices already made.

---

## 10. How this touches the related designs

- [`visual_digest.md`](visual_digest.md): answers decision 8 (MAS-218) as
  (a) with (b)'s renderer inside it; its section 2.8 is the figure endpoint
  and renderer this note builds in phase 1; its campaign store stays for the
  notebooks and scheduled reports (decision 4 here); its open question 6 is
  decision 6 here. The `compound:` key of 4.2 is new to it.
- [`sample_filters_and_metadata.md`](sample_filters_and_metadata.md): its
  decision 1 stands and its section 8 cost is paid by the read path here, not
  by throwaway batches; *Make a batch of this range* is its "Create batch
  from this filter" with the scope as the filter; its histogram and the
  coverage strip are one component (decision 13).
- [`ux_review_2026_10.md`](ux_review_2026_10.md) and MAS-712 / MAS-713: Explore
  and Dashboards are sidebar destinations; the scope strip sits under the top
  bar on every view; the acquisition outline as a calendar is phase 5.
- [`ui_state_persistence.md`](ui_state_persistence.md) and MAS-717: the scope
  joins the location schema and rides in the URL.
- [`peak_assignment_batch_primary.md`](peak_assignment_batch_primary.md): the
  fold gains the rollup write; the ledger is unchanged.
- [`ai_analyst_shell.md`](ai_analyst_shell.md): the in-app panel renders the
  same Figure spec and saves recipes from a conversation.
