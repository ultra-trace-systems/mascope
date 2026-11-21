# Assigning a batch in one action

- **Status:** design
- **As of:** 2026-11-18
- **Tracked in:** [Assignment quality plan](https://linear.app/ultra-trace/project/assignment-quality-plan-553f4854192c)
  (Linear, milestone 8 "One button: assigning a batch from the app": MAS-1011 to
  MAS-1017, among them the Decision MAS-1012, decided; it builds on MAS-983 in
  milestone 7)
- **What:** One control that assigns a whole batch, runs the batch stages in a fixed order
  as one batch run and says whether the batch needs it, replacing the row of nine
  per-step buttons.

*The batch ledger's steps were built one at a time, each as a manual batch run with a
button of its own ([`assignment_quality_plan.md`](assignment_quality_plan.md), stage 4).
Each brief placed its pass "in the fold" or "after the fold", and each was built as a
button instead because reading a whole batch on every arrival costs too much. The
2026-11-17 review of the pipeline (MAS-974) made the passes one entry in a fixed order,
on the server (MAS-983). This note is the app's side of that change: the one action a
user presses and what it tells them.*

## 1. Where it stands

At batch level the switch bar
([`BatchPeakComputeBar.vue`](../../server/frontend/src/lib/panes/PaneBrowserMatch/BatchPeakComputeBar.vue))
holds the run selector and nine buttons, in the order they were built: *Rebuild batch
ledger*, *Search untargeted*, *Detect series*, *Read co-variation*, *Stamp isotopologues*,
*Strip isotopologue rows*, *Read batch checks*, *Decide known species* and *Measure water
ladders*. Each posts to a route of its own under `/batch-peaks/batch/{id}/` and becomes a
`batch_peak_run` with an action of its own
([`batch_runs.py`](../../server/backend/src/mascope_backend/api/new/peak_assignments/batch_runs.py)).
Six problems follow from that.

1. **The user has to know the order, and the row gets it wrong.** *Read batch checks*
   reads the records of *Stamp isotopologues* and *Read co-variation*, pinned to their
   run ids, and with neither it reports "Run Stamp isotopologues and Read co-variation
   first". Re-running either one hides the checks until they are read again.
   *Decide known species* reads the claims of *Measure water ladders*, yet the row puts
   it first. *Rebuild batch ledger* replaces every sample's members, which wipes what
   *Search untargeted* and *Detect series* wrote, so pressing it after them undoes them.
   This knowledge lives only in the help cards and in
   [the user page](../user/how-it-works/peak-assignment.md#batch-peaks).
2. **The history counts clicks, not passes.** Every step snapshots the whole ledger when
   it starts, and `BATCH_RUN_KEEP = 5` keeps five runs besides the current one. One pass
   of nine clicks therefore prunes the previous pass from the run selector, including the
   rebuild that began the same pass. The selector then reads *#2 · Rebuild · current*,
   which tells a user nothing about the batch.
3. **Nothing says whether the batch needs a run.** Samples fold in at ingest with known
   compositions only (`auto_assign_sample_peaks`, `peak_assignment_ingest_ledger =
   "batch"`). A fold can knock a step's record off any batch peak whose reading it moves.
   Only ratio stability tracks whether it needs re-reading: the fold re-grades it and
   `continuity_stale` marks a grade stale on the ledger and the snapshot. For every
   other step the only signal is `ledger_utc_modified`.
4. **The labels name mechanisms, not outcomes, and none of them is primary.** The
   design system allows one filled primary per surface
   ([`frontend_design_system.md`](frontend_design_system.md) section 5). The bar fills
   *Rebuild*, which is the recovery operation, while *Strip isotopologue rows* and *Stamp
   isotopologues* sit beside it at the same weight.
5. **The concurrency rules live in the browser.** The server already refuses a second batch
   run with 409 `batch_run_in_flight`, but the buttons cross-disable only within one
   tab, and *Rebuild* is not disabled while the others run. A task that is cancelled
   rather than failed escapes the `except Exception` around it and leaves its run
   `running`, which blocks the batch until the startup reaper runs.
6. **Progress is uneven.** *Rebuild*, *Search untargeted* and *Detect series* send
   progress packets. *Detect series* packets carry no percent, because its type is
   missing from `send_progress_user_notification`. The other six send none.

MAS-983 fixes the order and the staleness on the server. Problems 2 to 6 are what this
note's issues fix.

## 2. The design in one paragraph

The batch gets one verb, **Assign batch**. The server runs MAS-983's stages in their
fixed order as a single batch run with one snapshot, and reports progress per stage. Beside the
button the bar states where the batch stands: known compositions only, up to date, or
behind and why. The button is filled only when there is something to do. The stages,
their results and the run history move into a **Details** popover. Parameters move onto
the batch as its **assignment settings**. Starting the pass at a later stage stays
reachable under the button's menu, as **Advanced › Run from a stage**, for whoever is
checking a stage. It is no longer the way anyone assigns a batch.

## 3. What the user meets

### 3.1 The control

The right end of today's switch bar shows a status block followed by a split button. The
main half runs the pass and the chevron opens the menu (3.3). The rules for the button:

- It is filled when the batch needs a pass and outlined when it does not.
- It turns into *Cancel* while a pass of yours runs.
- It disappears while somebody else's pass runs, since the server would refuse the request.

| State | Status line | Button |
|---|---|---|
| No batch peaks (the batch predates the ledger, or was imported) | *Not assigned* | **Assign batch**, filled; the ledger's empty state carries the same button, and the bar hides its own copy then, as `AssignmentRunBar.vue` does for a sample |
| Known compositions only (folded at ingest; no pass has run) | *Known compositions only*, and below it: the untargeted search and the batch checks have not run | **Assign batch**, filled |
| Running, your pass | spinner, *Assigning the batch · Find compositions*, *Step 3 of 4* | **Cancel** |
| Running, another user's pass | spinner, *Another user is assigning this batch*, step and start time | none |
| Up to date | check, *Assigned today 14:02 · up to date*, the sample count and the change since the previous pass (*+181 assigned*) | **Re-assign**, outlined |
| Behind: new samples | *12 samples added since the last assignment* | **Update assignment**, filled |
| Behind: a library, the settings or the engine changed | *Target collections changed since the last assignment*, or *Assigned by an older engine (0.7.0)* | **Re-assign**, filled |
| Failed or cancelled | *Stopped at Homologous series*, and below it: the stages before it were applied | **Retry**, filled; it starts the pass at the stopped stage |
| Viewing an earlier run | *Viewing run #3 · 16 Nov · read-only* | **Back to current** |
| Viewer role | the status as above | the button disabled, with the reason as the action that enables it: *Ask an editor of this workspace to update it* |

Every state except the first two also offers **Details** (3.2) beside the status line.
The status reads from one server endpoint (5.3), not from per-tab flags, so two users and
two tabs see the same state.

### 3.2 Details

A popover from **Details**. It holds:

- the four phases and their stages as a checklist: done, running with its percent, failed
  with the error, stale with its reason, or waiting;
- a one-line result per finished stage, in the words of that stage's help card
  ("38 rows folded under their compound", "14 facts recorded"). The stage's run summary
  already holds these counts;
- the changes the pass made to the tier counts, shown as chips;
- the settings the pass ran under, with **Change settings**;
- **Run history**.

Run history replaces the run selector in the bar. It lists the batch's runs newest
first:

- a pass is labelled *Assignment*;
- a pass started at a later stage is labelled *Assignment from a stage (Homologous
  series)*;
- a stage run alone through its own route, which the SDK and the bench still use, keeps
  its stage's name;
- imports and the implicit *Folded samples* run keep their labels.

Picking an earlier run shows the ledger as that run left it, read-only, as the selector
does today ([`BatchPeakRunSelect.vue`](../../server/frontend/src/lib/panes/PaneBrowserMatch/BatchPeakRunSelect.vue)),
and the bar shows the *Viewing an earlier run* state.

### 3.3 The menu

The chevron opens:

- **Assignment settings…** (3.4)
- **Re-assign from scratch**: folds every sample again before the pass. Slower; for an
  imported ledger, an old one, or a suspicion that the ledger is wrong.
- **Run history**
- **Export ledger**, the same task the ledger's view-options popover offers today.
- An **Advanced** group with **Run from a stage ›**. The submenu lists MAS-983's stages
  under their phases; each starts the pass at that stage and runs it to the end, as
  MAS-983's start-from-stage does. A stage is never run alone from the app, because the
  stages after it read what it writes.

Who sees Advanced is a call for the plan owner in MAS-1015. The issue builds it behind a
user setting, *Show assignment stages*, off by default.

### 3.4 Assignment settings

The only stage with parameters today is the untargeted search. It reads them from the
per-user client store `usePeakAssignParams`, so two users re-running one batch can run it
under different settings, and neither run says so until its config is compared in the run
selector. Here the settings become the batch's own (5.4): chemistry profile and context
(each *Auto*, showing what *Auto* resolves to), m/z precision, formula range, the peak
ceiling, the intensity threshold and the alternatives kept. The dialog is the existing
`PeakAssignConfigForm` on the dialog grammar of MAS-156: *Edit assignment settings*, then
*Cancel* and *Save*. Saving does not start a pass. It marks the batch behind, and the
next *Re-assign* uses the new settings. The per-sample dialog opens on the batch's
settings and may override them for that one run. That the settings are the batch's was
decided in MAS-1012 on 2026-11-18.

### 3.5 Feedback

A pass is one entry in the progress strip, *Assigning 2025-08-11 Batch A · Find
compositions (3/4)*. It is not a toast per stage
([`frontend_design_system.md`](frontend_design_system.md) section 7). When it ends, the
status line and the tier chips update in place, and the result is silent while the pane
that asked for it is on screen. If the user has left the pane, one toast reports the pass,
titled *Batch assignment* with the change in counts. A pass that stops at a stage is a
warning toast naming the stage; the rest of its message is in Details.

### 3.6 Where it lives

Today the control is the batch half of the switch bar, in the place
`BatchPeakComputeBar` takes. MAS-713 retires the Targets/Assignments switch for a *This
sample* / *Whole batch* scope switch in the ledger header. The control then moves into
that header beside the scope switch: *Whole batch* shows this control, and *This sample*
shows the sample's run and **Assign sample** (today's *Assign peaks*, renamed in MAS-1015
so the two verbs pair). The MAS-712 sidebar gives each batch a status dot, and the dot
can carry the *behind* state. MAS-157's batch "…" menu can offer *Assign batch* too.
Nothing here waits for those issues.

### 3.7 Words

The stages keep their names in Details and Advanced. The help cards and the user page
already use those names, and a reader who meets a record must be able to find the stage
that wrote it. The phases are the only new words. They group MAS-983's stages without
reordering them:

| Phase | Stages |
|---|---|
| Gather samples | Fold the samples (today's *Rebuild batch ledger*, only when it is needed: 4.2) |
| Recognise source ions | Water ladders, Known species |
| Find compositions | Untargeted search, Isotope lines (strip, stamp and the search's merge, one pass on the line model of MAS-977), Homologous series |
| Cross-check across the batch | Co-variation, Ratio stability, Batch checks, Probable blends, and the identification level once it is a stage |

## 4. The pipeline

### 4.1 Order

MAS-983 decides the order. It is restated here because Details lists it. Two rules
produce it. First, a stage runs after every stage whose records or claims it reads.
Second, every stage that rewrites members or rows runs before every stage that only
records a reading. Records stand only while the batch peak's consensus names the reading
they were made for (`compute_consensus` and the `standing_*` readers in
[`batch_peaks.py`](../../server/backend/src/mascope_backend/api/new/peak_assignments/batch_peaks.py)),
so a rewrite placed after a recording would knock it off the rows it moves.

| # | Stage | Rewrites or records | Reads from earlier stages |
|---|---|---|---|
| 1 | Fold the samples (when needed) | rewrites: every member, the consensus | nothing |
| 2 | Water ladders | rewrites: members and in-app rows to the reagent's rung | the fold |
| 3 | Known species | rewrites: members and in-app rows of species present | the ladders' claims |
| 4 | Untargeted search | rewrites: members of unassigned batch peaks | skips members claimed in 2 and 3 |
| 5 | Isotope lines | rewrites: one owner link per isotope line | committed rows from 4 |
| 6 | Homologous series | rewrites: members that take a proposal | assigned batch peaks; a line owned in 5 is no start |
| 7 | Co-variation | records only | channels from 4 to 6; normaliser from 2 and 3 |
| 8 | Ratio stability | records only | 7 (a drift graded without it reads `red`) |
| 9 | Batch checks | records only | the stamps of 5 and the reading of 7 |
| 10 | Probable blends, levels | records only | the consensus |

Two consequences matter to the user. Ratio stability moves from the end of the rebuild to
after co-variation: the rebuild grades continuity before any co-variation reading exists,
so a channel drift reads `red` instead of `drift`. MAS-983 also judges what stages 2 to 6
write the way a run judges, so a pass can move tiers that a stage alone used to set
directly. Those moves are on the bench card of MAS-983, not of the issues here.

### 4.2 When the samples are folded again

The fold of every sample is the expensive stage, and it wipes the member-level work of
the stages after it. Ingest already keeps the fold current, so a pass skips stage 1 unless
the ledger cannot be a starting point (MAS-1011's `needs_fold`):

- the batch has no batch peaks;
- a target collection or reference list changed since the last pass, which only a fold
  reads;
- the current run is an import, and the pass is meant to put Mascope's own view back;
- the last full pass ran under another engine version;
- the user chose **Re-assign from scratch**.

Details shows a skipped fold as "no rebuild needed", with the reason when it was not
skipped.

### 4.3 Update after new samples

A sample folded after a pass holds known compositions only, and MAS-983's staleness rule
marks every stage stale from the first one the fold feeds. Re-running from there has to
bring the new members up to the batch's readings, not only re-read the batch peaks still
unassigned: the search reads only batch peaks whose consensus is unassigned
(`_unassigned_anchors_and_members` in
[`batch_untargeted.py`](../../server/backend/src/mascope_backend/api/new/peak_assignments/batch_untargeted.py)),
so a new member of a batch peak the search already assigned would otherwise never be
measured. MAS-983's Verify asks for exactly that ("the pipeline restores its search,
strip and ladder results"), and its stage list (MAS-976) is the path that measures them.

### 4.4 Failure, retry and cancel

Each stage commits on its own. A stage that stops partway writes the records it has and
is marked stale (MAS-983). A pass that stops records the stage and its error in its run
and does not become current. *Retry* starts a new pass at the stopped stage.

*Cancel* stops between stages, and inside the search between samples. It does not roll
back: a cancelled pass is recorded as cancelled and keeps what it wrote, with its
unfinished stages stale. The pass catches cancellation itself and never leaves a run
`running` (MAS-1011).

## 5. Backend

### 5.1 What MAS-983 builds

One batch entry that runs the stages of 4.1 in order. What stages 2 to 6 write is judged
as a run judges, through the per-sample stage list (MAS-976). Every batch decision
becomes a versioned record, which a sample's run reads as an input (MAS-985). A fold marks
stages stale, and the pipeline re-runs from the first stale one. Each existing button
starts the pipeline from its own stage until MAS-1015 replaces the buttons.

As built (`batch_stages.py`):

- Nine stages, the order of 4.1 without the fold, which stays *Rebuild batch ledger*
  until MAS-1011's `needs_fold`: `water_ladders`, `known_species`, `search`,
  `isotope_lines` (strip, then stamp), `series`, `co_variation`, `continuity`,
  `batch_checks` and `blends` (the consensus of every batch peak read again; the level
  joins it as a stage once 5.8c makes one). `POST /batch-peaks/batch/{id}/run-stages`
  takes `from_stage`, or starts at the first stage that is not current. A named stage
  after one that is not current (stale, or never run on the batch) starts the pass at
  that one instead, and so does each step's button: a stage that ran before the stages it
  reads leaves a ledger that depends on the click order, which the Verify's two-order test
  found (*Decide known species* pressed before the water ladders ever ran claims a rung for
  another source ion on one file's own test, and the ladder after it finds the rung taken).
- 4.3 as built: the strip reads a row it stripped before again where a member does not
  read the parent's line yet (`batch_merged_isotopologues.strip_again`), and the search
  proposes the reading it assigned a batch peak to a member of it that reads nothing
  (`batch_untargeted._readings_to_measure_again`). The water ladders and known species
  claim per member already, and the series measures a corroborated proposal on the
  members that do not read it (MAS-769).
- Each stage recorded itself as its step's batch run, so a pass made up to eight runs;
  MAS-1011 made it one (5.2).
- `batch_peak_stage` holds each stage's `version` (its runs) and `state` (current,
  running, stale) with `stale_reason` (`fold`, `rebuild`, `upstream:<stage>`,
  `interrupted`, `partial`, `consensus`). The ledger read serves `stages` and
  `stale_stages`, which MAS-1013's status read builds on. A record a stage writes
  carries the version it was written at.
- A member a stage rewrote carries `batch_written`: the stage, the version and what the
  member read before. A withdrawn decision (a known species no longer present, a rung no
  longer passing) puts that back, and the in-app rows from what they kept under
  `provenance.batch_written_before`, but for a row assigned by hand since.
- After a rewriting stage, every sample it wrote in is judged again through the stage
  list (`batch_rejudge.py`), and after the series every sample whose member a series
  record corroborates.
- A sample's own run reads the records (MAS-985, `batch_decisions.py`): once, in its
  resolve stage, as a declared input of the stage list (`pipeline.BATCH_INPUTS`). A known
  species and a water-ladder rung enter at the claim stage, a listed compound at the
  propose stage (elected after the search, as the known-species claim takes a member), a
  strip and a series at the judging. A rung reaches a sample acquired inside a stretch it
  passed in, which the ladder's record now bounds by its first and last spectrum's times;
  `claimed_samples` places only a sample the times cannot (no time, or a record from
  before them). A strip's record now keeps the line's m/z offset. A listed compound or a
  strip on a peak the run's frame does not hold (an earlier detection's peak id, which a
  member names until the sample's next fold) is left, as `peak_absent`. A decoy arm reads
  the source ions and the listed compounds and no strip or series. The run records, under
  `config.batch_decisions`, each stage's version and state, read before the records, how
  many it read at which versions, and what they did to its rows.

### 5.2 One run per pass (MAS-1011)

A new action, `assign`. A pass:

- takes the lock with one `start_run` and snapshots the ledger once;
- runs the stages inside that run, without starting runs of their own;
- writes the stages' summaries into the run's `summary` under `stages`, one entry per
  stage with its status, start, end and result counts;
- records the samples it covered, the engine version and the library it read, for the
  status read;
- becomes current at the end with one `complete_run`.

A pass started at a later stage is the same action and records its first stage.
`BATCH_RUN_KEEP` then counts passes. Progress goes out as one `assign_batch` process,
one pending packet per stage start with `stage`, `stage_index`, `n_stages`, `phase` and
`stage_progress`, each stage's own packets nested under it. The stages that send nothing
today send at least their start. A cancel route sets a flag the pass reads between stages,
and the pass catches `asyncio.CancelledError`. The SDK gets
`mascope.batch_peaks.assign()` and `cancel_assign()`.

As built (`batch_stages.assign_batch`, `batch_runs.in_batch_run`):

- Every step's controller takes the run it writes into (`batch_peak_run_id`) and opens a
  run of its own only when called outside a pass. The run's `summary` lists the fold and
  the nine stages in order, each with `status` (`done`, `failed`, `cancelled`,
  `not_run`), `utc_started`, `utc_ended`, the stage row's `state` and `version`, and each
  step's outcome: its message, counts and the thresholds or settings it ran on. Beside
  them: `from_stage`, `first_stage`, `fold` (`ran`, `reasons`), `samples` and `library`.
  The run's `config` is what the pass was asked for: `from_stage`, `fold` and the
  `settings` given.
- A pass a step's own route starts keeps that step's action (`search_untargeted`, ...;
  the ratio stability, which never had one, is an `assign`). Run history can label it
  by its first stage either way.
- The library is a digest of each target collection the batch names (its compounds and
  their ions: a collection keeps no modification time) and each active reference list's
  load (id, name, version, `ingested_at`). `needs_fold` compares it with the newest
  *completed* pass's: a failed or cancelled pass need not have folded what it read. No
  pass yet compares nothing, and the ledger the folds at ingest built is the start.
- A pass with nothing to fold and every stage current opens no run, so it prunes no pass
  from the history. The `assign` route answers so at once; otherwise it opens the run
  itself, as the import route does, and hands it to the pass. A second press is then
  refused with a 409 on the request, and the run the answer names exists when the SDK
  polls for it.
- The cancel flag is `batch_peak_run.cancel_requested_utc`, in the database, since the
  request can reach another backend process. The pass reads it before each stage hands
  over to the next (a stage asked to stop begins no next stage, and the stages after it
  are marked `upstream:<stage>` stale) and before each of the search's samples (the
  search stops there, marked `interrupted`). A task the event loop cancels is closed as
  `cancelled` with an `error` saying it was interrupted, shielded as the stage rows are.
- The routes `POST /batch-peaks/batch/{id}/assign` and `.../assign/cancel` are opened to
  API tokens for the SDK, editor role as every batch-stage route.

### 5.3 The status read (MAS-1013)

`GET /batch-peaks/batch/{id}/assignment-status` returns:

- `state`: `none`, `folded_only`, `running`, `current`, `behind`, `failed` or
  `cancelled`;
- the last pass: run id, first stage, time, user, engine version and settings hash;
- the running pass, if any: stage and progress, whoever started it;
- `stale_stages`, from MAS-983;
- `needs_fold`, from MAS-1011;
- `behind`, a list of reasons, each with its count or detail:
  - `new_samples` and `removed_samples`: the batch's samples against the set the last
    pass covered;
  - `resampled`: samples whose run read older batch decisions than the current ones
    (MAS-985 records the versions a run read, `config.batch_decisions.stages`);
  - `library_changed`, `settings_changed` and `engine_changed`;
  - `stages_stale`, for any other stale mark.

The bar reads it on focus and on every `peak_assignment_reload`. A curation, a verdict or
an import is not a reason: each acts on the current run by design, and an import shows as
its own run. The SDK gets `assignment_status()`.

As built (`batch_assignment_status.py`):

- `state` is the first that holds: `running` (a batch run in flight, or a stage row
  running), `failed` or `cancelled` (the newest pass, served as `stopped_pass` with the
  stage it stopped at), `none`, `folded_only` (no pass has completed), `behind`, `current`.
  A rebuild or an import completed after a stopped pass does not clear `failed`.
- The reasons are measured against the newest completed pass (`batch_stages.last_pass`),
  as `needs_fold` is, and `needs_fold` is `fold_reasons` on the same reads, so the two
  never disagree. A failed or cancelled pass is compared with nothing.
- `resampled` until MAS-985 merges: the samples the pass covered with an in-app run of
  their own completed after the pass started; decoy runs and imported runs are not
  counted, and a new sample is new, not resampled.
- `stages_stale` leaves out the marks that are not reasons: `curation` and `import`, a
  `consensus` mark made after the pass ended (the recompute of a fold, a pin or an
  import), and a `fold` mark where a sample reason holds. A `consensus` mark made inside
  the pass is its own leftover and counts.
- `running` comes from the pass itself: `assign_batch` writes its progress into its
  run's `summary` under `progress` as each stage starts (`batch_runs.record_progress`):
  the process id, the stage, its index of the planned stages, the fold counted first. Its
  end writes the summary whole. `progress` is the share of stages finished; a stage's own
  progress stays in its packets. A rebuild or import on its own serves no stage.
- `settings_changed` compares the hash of the batch's settings with the one the last pass
  recorded (MAS-1014, 5.4). A pass from before then recorded none and compares nothing.
- About a dozen queries whatever the batch's size: the read of a 5,000-sample batch sends
  the statements a two-sample one does.

### 5.4 Settings on the batch (MAS-1014, decided in MAS-1012)

A nullable `assignment_settings` JSON on `sample_batch`, validated as a
`PeakAssignmentConfig` without its decoy block. Null means the defaults. It is edited
through `PATCH /api/sample/batches/{id}`, as `seed_batch_peaks` is. The pass records the
settings it ran under in its run's `config`, and the status read compares their hash.

As built (`config.BatchAssignmentSettings`, `batch_stages.pass_settings`):

- The column stores the fields that were set, so a field left out follows the engine's
  default; null, or an empty object, is the defaults. A field the model does not know is
  refused rather than dropped, and each refusal names its field in the message, since the
  API's 422 body carries the validators' messages alone.
- Every stage of a pass runs on the batch's settings, not the search and the series
  alone: every stage that rewrites members judges its samples again under their tier
  bands. The fallback MAS-983 built, each stage on the settings its own last run recorded,
  is gone: it was the per-user settings by another road.
- The run's `config` holds `settings` (the whole config the pass ran on) and
  `settings_source`, `batch` or `request`. `request` is a pass whose route was given a
  `config`: a step's own route, `run-stages` or `assign`. The summary holds
  `settings_hash`, a digest of that config, equal for a field sent as its default and one
  left out.
- Saving marks no stage stale. A pass whose settings hash otherwise than the last
  completed pass's runs from the first stage instead (`plan_pass`), by the test the status
  read's `settings_changed` makes (`settings_moved`), so the two agree. A pass on a
  request's settings leaves the batch behind until a pass on its own.
- Settings the batch saved that no longer validate (a field since removed or bounded
  tighter) run as the defaults, logged.

### 5.5 What stays

- **The per-stage routes stay.** They serve the SDK and the bench. MAS-983 points them at
  its entry.
- **The fold at ingest stays.** It still runs Stage A only; the pass does not run on
  arrival.

## 6. What does not change

- **Stage B per batch stays user-triggered** ([`peak_assignment_batch_primary.md`](peak_assignment_batch_primary.md),
  settled). The pass is a press, not a hook on ingest: running it on arrival is decision
  10.5 of that note (MAS-322), which is still open.
- **What moves a tier is MAS-983's and the plan's business, not this note's.**
  Co-variation and ratio stability stay display-only (decision 35 of the plan), and the
  isotopologue stamps and batch checks stay recorded facts (steps 4.2c and 4.2g) until a
  later step counts them in the tiers. The issues here change when and how a pass is
  started and shown, never what it writes.
- **The per-sample run stays the deep dive.** Curation stays at the batch peak, and
  verdicts and curations still act on the current run.
- **The tier vocabulary, the provisional marker and the hidden P(correct) are untouched.**

## 7. Decisions for the plan owner

1. **Settings on the batch or per user** (3.4, 5.4): decided in MAS-1012 on
   2026-11-18, on the batch, so a pass is reproducible by anyone. The cost is a column
   and a migration (MAS-1014).
2. **Who sees Advanced** (3.3): a call for the plan owner in MAS-1015. The options are
   every editor, a user setting (*Show assignment stages*), or superusers only. The
   recommendation is the user setting: the plan's own testers need it, and a lab user
   never meets ten stages they did not ask for.
3. **How *Known compositions only* is shown**: a call in MAS-1015. That state is every
   batch ingest builds. Showing it in the accent colour would mark nearly every batch at
   once, so the recommendation is a neutral dot with a filled button.
4. **Whether a pass may ever start by itself.** For example, after N new samples, or after
   a quiet hour on a batch that an instrument is still writing to. This waits on decision
   10.5 (MAS-322) and on the fold's cost (MAS-899). Until then the *behind* state asks
   instead.

The order of the stages, which an earlier draft of this note left open, is MAS-983's.

## 8. Docs

Each issue updates the pages its change touches, in its own PR:

- [`peak-assignment.md`](../user/how-it-works/peak-assignment.md), *Batch peaks*:
  rewritten around *Assign batch* (MAS-1015). The stages become sub-sections of one
  pass, in the order of 4.1, each keeping its own explanation and references. *Batch
  runs* describes passes and their stages (MAS-1011, MAS-1016); what *behind* means
  (MAS-1013); the settings (MAS-1014, MAS-1017).
- `_help/batch-runs.md` and `_help/batch-peaks.md` change to match, and
  `_help/batch-assignment.md` is new (MAS-1015, MAS-1016).
- [`peak_assignment_frontend.md`](peak_assignment_frontend.md) still names *Compute batch
  peaks* and a P(correct) column. Its switch-bar paragraph points here once MAS-1015
  merges.

## 9. Steps

Milestone 8 of the [Assignment quality plan](https://linear.app/ultra-trace/project/assignment-quality-plan-553f4854192c).
The `blockedBy` relations hold the order.

| Issue | Step | Blocked by | Size |
|---|---|---|---|
| MAS-1011 | A pass of the batch stages is one batch run: one snapshot, progress per stage, a cancel that leaves no run running | MAS-983 | M |
| MAS-1013 | The batch's assignment status: where the batch stands and why it is behind | MAS-1011, MAS-983 | M |
| MAS-1012 | Decision: the settings on the batch, or each user's (decided 2026-11-18: on the batch) | none | |
| MAS-1014 | The settings saved on the batch and read by every pass | MAS-1012, MAS-1013, MAS-1011 | S |
| MAS-1015 | *Assign batch*: the control, replacing the nine buttons | MAS-1011, MAS-1013 | M |
| MAS-1016 | Details and Run history, in place of the run selector | MAS-1015, MAS-1011 | M |
| MAS-1017 | *Edit assignment settings*: the dialog | MAS-1014, MAS-1015 | S |

MAS-983 is blocked in turn by MAS-976, MAS-977, MAS-890 and MAS-763 (milestones 7 and 4),
so nothing here starts before the stage list and the line model land. The UI issues relate
to MAS-713 and MAS-157 (the ledger header and the batch menu of the Dashboard UX
project) and MAS-1017 to MAS-156 (the dialog grammar); none of those blocks them.
