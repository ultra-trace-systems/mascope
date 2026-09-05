# Temporal-Continuity Evidence and Anchor-Scoped Verdicts — Design Note

*A batch peak is one species measured repeatedly over acquisition time, and that
repetition carries evidence no single spectrum can carry: an isotopologue ratio,
or an adduct ratio, should hold constant as the source strength rises and falls.
This note defines **ratio stability** as a computed, **display-only** evidence
layer on the batch-peak anchor, and — separately — an **anchor-scoped verdict**
that lets a human judge a species once for a whole batch as an **overlay** on
the per-sample verdicts, which stay exactly as they are and win where present.
Both are designed against what the tree holds today; neither changes `p_correct`
or a tier.*

> **Status: proposal (revised 2026-08-31, triage item WP25).** A design for
> sign-off — no code until agreed. Three product decisions are on record and this
> note builds on them rather than reopening them (§0). Code anchors re-verified
> against `develop` on the revision date. The two work packages this note leans
> on — the batch ledger's `isotopologue_of` and `max_intensity` (WP21) and the
> assignment-copy implementation (WP24) — are now **merged**, and the note reads
> them as facts rather than designing around their absence; where a merged choice
> contradicts an earlier draft of this note, the note says so and gives ground.

A vocabulary note, since this document is about isotope peaks throughout: in
Mascope a **satellite** is a Fourier-transform sidelobe artifact next to an
intense centroid — `flag_satellite_peaks`, `is_satellite_peak`, and the
satellite-filtering section of
[`peak-detection.md`](../user/how-it-works/peak-detection.md) — and never an
isotope peak. The non-M0 members of an isotope family are **isotopologues**, or
**children** where the contrast with their M0 is the point; the engine's role is
`iso_child`.

---

## 0. What is settled, and what the tree actually holds

**Settled (product owner, 2026-08-30).**

1. **Temporal continuity is computed evidence at the anchor level**, for
   time-ordered batches. **Ratio stability is the primary metric**: an
   isotopologue or adduct ratio should stay constant over acquisition time
   regardless of source strength, so a flat ratio series is high-grade evidence
   and a drifting one a red flag. Residual correlation — co-variation beyond
   what the batch-wide common mode explains — is **secondary**.
2. **Display-only in v1.** The evidence surfaces as a badge and a score in the
   batch-peak ledger and the inspector. It does **not** feed `p_correct` or a
   tier. Folding it into confidence is a later, separate decision (§3, §8).
3. **Anchor-scoped verdicts are an overlay.** A human can confirm or reject a
   species once at the anchor level; per-sample verdicts stay as they are and
   **win where present**. The per-sample, family-scoped model is not replaced.

**Tree facts this design rests on** — each read out of the current tree, not
inherited from an earlier document:

- The per-(anchor, sample) intensity series already exists. `BatchPeakOccurrence`
  stores one row per (batch peak, sample), unique on that pair, carrying
  `intensity`, `tier`, `fit_score`, `assigned_formula`, `sample_peak_id` and
  `peak_assignment_id`
  ([`models.py`](../../server/backend/src/mascope_backend/db/models.py)). It
  carries **no timestamp**; time comes from the sample.
- The fold that writes those rows is serialized per batch by a
  transaction-scoped advisory lock and depends on READ COMMITTED
  ([`batch_peaks_controller.py`](../../server/backend/src/mascope_backend/api/new/peak_assignments/batch_peaks_controller.py)),
  and it runs best-effort after **every** completed in-app run
  ([`service.py`](../../server/backend/src/mascope_backend/api/new/peak_assignments/service.py))
  and after every import publish
  ([`import_service.py`](../../server/backend/src/mascope_backend/api/new/peak_assignments/import_service.py)) —
  a fold-in failure must never fail or un-complete the assignment itself.
- **The isotopologue family link is a stored column, not a derivation.**
  `batch_peak` carries `isotopologue_of` — the anchor whose family this one
  belongs to, a self-referential FK with `ON DELETE SET NULL`, indexed — and
  `max_intensity`, the brightest member. Both are member aggregates computed in
  `compute_consensus` and written by `_recompute_consensus`, i.e. inside the
  serialized per-batch fold, and both are served by `_batch_peak_meta`
  (migration `b6a4d1e83c7f`, the current single head). The ledger pane flattens
  chains and folds children under their M0 by default.
- **Every observed peak folds**, assigned or not: an unassigned row carries the
  peak's real measured intensity
  ([`engine.py`](../../server/backend/src/mascope_backend/api/new/peak_assignments/engine.py),
  `build_unassigned_assignments`), which the fold copies verbatim into the
  occurrence. There are now two producers of such rows: the copy service mints
  the same kind for every unmapped destination peak (`build_placeholder_rows`),
  so that a published copy is complete and the fold does not silently shrink the
  sample. Unassigned occurrences are therefore ordinary rows with real numbers,
  not stubs — §1.5 turns on this, and turns on it harder for a copy-heavy batch.
- **Assignment copy is implemented**, as the seeded re-score: it publishes each
  destination through the import channel under the reserved `mascope-copy`
  engine and folds at finalize like any other run. Its binding rule now holds in
  code as well as in design — verdicts do not copy
  ([`peak_assignment_copy.md`](peak_assignment_copy.md) §6), and nothing in the
  copy or import services so much as references `AssignmentVerification`.
- A verdict is marked current, not derived: `superseded_utc` is NULL on exactly
  one row per identity (`sample_item_id` + `sample_peak_id` + `assigned_formula`
  + `ionization_mechanism_id`), enforced by a partial unique index with
  `NULLS NOT DISTINCT`, and the calibration fit filters on that marker
  (`recalibrate_instrument`, `service.py`).
- Nothing named continuity, ratio stability or temporal evidence exists in the
  backend or the frontend today. This is a new layer, not a rework of one.

---

## 1. The metric

Every constant introduced in this section is **provisional**: a first-principles
starting point, not a measured one. They are collected in §1.4 with their
reasons, and tuning them on real time-ordered batches is an open question (§8).

### 1.1 Why ratio stability, and not correlation, is primary

The series for one anchor is `BatchPeakOccurrence.intensity` per sample: the
scan-count-averaged `sum_peak_heights` (Orbitrap) or `sum_peak_areas` (TOF).
Every per-sample effect that scales a whole spectrum — source strength,
ionization efficiency drift, scan count, dilution — multiplies **both** members
of a related pair. A **within-sample ratio therefore cancels it point by
point**, before any statistics are done. What is left is the physics: an
isotopologue ratio is fixed by isotopic abundance, and an adduct ratio by the ion
chemistry of the source, so a species that is what it claims to be produces a
**flat** ratio series even while its absolute trace swings by orders of
magnitude.

Correlation cannot do this. It is undefined on a series with no variation —
precisely the well-behaved batch — it is inflated by the common mode every trace
shares, which is what a TIC reference trace on the batch overview lets a user
eyeball today
([`ChartBatchAssignments/data.js`](../../server/frontend/src/lib/charts/ChartBatchAssignments/data.js)),
and it needs dynamic range before it says anything. So correlation is kept, but
as a **secondary, tooltip-only** number computed after removing the common mode
(§1.3). Decision 1 makes it secondary; this note goes further and keeps it out of
the grade entirely, because it carries no measured weight (§3) — the strictest
reading of "secondary", and reversible without a schema change, since the value
is persisted either way.

The honest limit: ratio stability says *these two peaks behave as one species*.
It cannot say *which* species. It corroborates an assignment's internal
consistency; it never identifies.

### 1.2 The pairs

Pairs are keyed on `batch_peak_id`, never on a formula string — identity is m/z
and formula is an annotation
([`peak_assignment_batch.md`](peak_assignment_batch.md) §2, §8).

**(a) Isotopologue pairs (primary).** An M0 anchor and one of its isotopologue
children. The pairing is a column read, not a derivation: `batch_peak` carries
`isotopologue_of`, the anchor whose family this one belongs to, voted out of the
members' per-sample assignments and materialized on the row at fold time
(`resolve_isotopologue_of`,
[`batch_peaks.py`](../../server/backend/src/mascope_backend/api/new/peak_assignments/batch_peaks.py)).
The pass reads the child's row and never touches the occurrence table to form the
pair — which is not only cheaper but the only durable option: the derivation this
note once proposed hangs off `BatchPeakOccurrence.peak_assignment_id`, and that
FK is `ON DELETE SET NULL`, so a pruned run would not make the link stale, it
would erase it.

A NULL `isotopologue_of` forms no pair, and the pass must not read it as "this is
an M0". The column is NULL under five conditions the row cannot tell apart: no
member carries a formula at all, so the resolver never ran; no member has role
`iso_child`; every candidate member's owner resolved to no anchor (the engine
leaves `owner_peak_assignment_id` NULL whenever the ion's M0 peak was not won by
the same ion in that run, and an owner assignment with no occurrence resolves to
nothing either); a member named this anchor itself, which the self-link guard
drops; or the leading owner missed the strict majority. Only the second means M0.
Count NULLs in the evidence blob (§2) as `n_no_parent` — a species whose children
rarely resolve a parent is worth seeing — but do not report them as a rate of
anything, because four of the five reasons are silence rather than a finding.

Disagreement between samples is already settled, and not the way this note first
proposed. `resolve_isotopologue_of` counts one vote per `iso_child` member over
the **assigned** members — an unassigned member is skipped before the denominator
is incremented, so it neither votes nor dilutes — and requires a strict majority,
`2 * n_votes > n_assigned`. The strict majority is also what makes the winner
unique, which is why there is no tie-break. There is no minimum-count floor and
no warning: a split family resolves silently to NULL, indistinguishable from the
four other NULLs above. So `mixed_parent` is no longer a by-product of forming a
pair. If it is still wanted — and one child claimed by two different M0 anchors
across a batch is a binning or blend symptom worth a tooltip rather than a log —
the continuity pass has to earn it with a second look at the members of its own.
§8 asks whether it should.

**What the column does not give the pass.** It is deliberately one hop: an
isotopologue whose owner anchor is itself an isotopologue is stored as observed,
and flattening the chain needs the whole ledger and belongs to the reader that
holds it. The batch-peak pane is that reader today — `rootParentId` walks to the
family root, returns null on a link it cannot follow so the row is drawn at top
level rather than lost, and terminates on a cycle through a `seen` set. The pass
must use that rule, not merely a correct one: a chain member paired against its
stored parent while the ledger draws it under the family root would hang a badge
on a row the two disagree about, and neither would show the disagreement.

**(b) Adduct pairs.** Two anchors in the same batch and the same
`ionization_mode_id`, with equal non-NULL `consensus_formula` and **different**
`ionization_mechanism_id`.

One rule here is mandatory rather than a refinement. An `iso_child` row is built
from the same winner as its M0 and therefore carries the **same
`assigned_formula` and the same `ionization_mechanism_id`** (`engine.py`), and an
anchor's consensus mechanism is the mode over its winner members
(`batch_peaks.py`). A child anchor therefore shares its M0 anchor's exact
(formula, mechanism) pair. Grouping naively would pair the *M+1* of adduct A
against the *M0* of adduct B — a ratio mixing isotope abundance with adduct
partitioning, which means nothing. The representative is a column test rather
than a heuristic: **one representative anchor per (formula, mechanism), the one
whose `isotopologue_of` is NULL**, and pair representatives across mechanisms
only. Two consequences of the merged rule are worth stating. A family whose
children split their votes leaves more than one anchor with a NULL link, so the
rule still needs a tie-break — take the largest `max_intensity`, which is already
on the row and costs no join. And a NULL link on an *unassigned* anchor is not a
representative claim at all; the pair-formation gate below already excludes
those.

**Pair-formation gate.** Both anchors must have `consensus_tier` in
{`assigned`, `candidate`}; adduct pairs additionally require `is_ambiguous` false
on both. This is deliberately *symmetric*, unlike the corroboration layer, where
only confident winners contribute but anything may receive. A ratio is a joint
statistic — both members are contributors — so requiring both to be trustworthy
is what stops a noise anchor manufacturing a stability claim.

**Units.** The intensity unit is a property of the **folded sample**, derived per
file at fold time (`_intensity_variable`, `batch_peaks_controller.py`) and only
snapshotted onto an anchor when that anchor is minted; the consensus recompute
never revisits it. A within-sample ratio is always unit-consistent, so the real
rule is to refuse a pair whose contributing samples do not all share one
instrument type — not to compare the two anchors' stamps, which can differ
harmlessly or agree misleadingly. Note that mixed-instrument batches are not
actually refused at fold-in today, despite
[`peak_assignment_batch.md`](peak_assignment_batch.md) §7 saying they should be.

**Caps.** The per-anchor rollup (§1.3) is computed over **all** eligible pairs;
only the persisted display list is capped, at four pairs per anchor, alarm-first
and then best-supported — capping by support alone could hide a drifting fifth
pair behind four stable ones and turn a red flag green. A per-batch ceiling of
20,000 pairs bounds the pass; anchors beyond it are marked `partial` with a
reason in the blob, so a truncated batch reads as truncated in the UI and not
only in a log.

### 1.3 The statistic

For a pair (A, B), over the samples where **both** are present and eligible
(§1.5), take `x_s = ln(I_B / I_A)`. Logs make the ratio symmetric — a factor of
two up and a factor of two down are the same distance — and turn multiplicative
noise, which is what MS intensity noise is, into additive noise.

- **Center**: `ratio_median = exp(median(x))`.
- **Spread**: `sigma_log = 1.4826 * MAD(x)`, displayed as
  `ratio_spread = exp(sigma_log) - 1` and phrased as a factor rather than as a
  symmetric percentage: at `SPREAD_STABLE = 0.15` that reads *"typically within
  a factor of 1.16 either way (+16% / −14%)"*. It is deliberately **not** called
  an RSD — for a lognormal the coefficient of variation is
  `sqrt(exp(sigma_log^2) - 1)`, which agrees only to first order and diverges
  where it matters (53% against 65% at `SPREAD_RED`). The MAD is chosen over a
  standard deviation for its 50% breakdown point: one spiked sample — a
  co-elution, a saturated scan — cannot flip a grade.
- **Drift** requires **both** a monotonic signal and an effect size, and both
  legs work in the same log metric as the spread so that a rise and an equal
  fall are treated alike. Fit a Theil–Sen slope of `x_s` against **elapsed time**
  `t_s − t_0` in seconds, taken from the acquisition timestamps of §1.6, and let
  `span = t_max − t_0`. Then drift fires when Spearman `|rho|` between `x` and
  acquisition-time rank is at or above `RHO_DRIFT` **and**
  `drift_log = |theil_sen_slope * span|` is at or above its threshold. (For
  display, convert to a symmetric fold change: with `f = exp(slope * span)`,
  show `max(f, 1/f)`.) A one-sided form such as `|exp(slope*span) − 1|` would be
  bounded by 1 for any falling ratio and could never flag a collapsing adduct
  ratio at all — the log form avoids that trap. Theil–Sen is a median-of-slopes
  estimator, robust in the same spirit as the MAD though at a lower breakdown
  point (~29% against 50%) — enough that a single spiked sample cannot invent a
  trend.
- **Secondary, tooltip-only**: `residual_rho`. Regress `ln I_A` and `ln I_B`
  separately on `ln TIC` (Theil–Sen, for consistency with the drift leg) and
  Spearman-correlate the fitted residuals. Simply subtracting `ln TIC` is only
  the unit-elasticity special case: where the true coefficient is not 1, a shared
  term survives in both residuals and *induces* correlation between unrelated
  peaks. `residual_rho` is NULL when any sample's `tic` is NULL (it is nullable)
  or when either residual has no variation — and note that a dominant anchor is a
  large share of its own TIC, so its residual is degenerate by construction. That
  is the same weakness §1.1 raises against raw correlation, and a further reason
  this number stays out of the grade. It is recorded because it is the quantity a
  future capped noisy-OR would need as its correlation discount (§3).

**Per-pair grade**, evaluated in this order:

| # | condition | grade |
|---|---|---|
| 1 | any §1.4 gate unmet | `insufficient` |
| 2 | the drift rule fires (both legs, `n >= MIN_DRIFT_SAMPLES`) | `drifting` |
| 3 | `sigma_log >= SPREAD_RED` | `noisy` |
| 4 | `sigma_log <= SPREAD_STABLE`, `MAD(x) > 0`, **and the drift test actually ran** | `stable` |
| 5 | otherwise | `inconclusive` |

Rule 4's two extra conditions are the point of the table. A stability claim needs
both legs, so a pair with too few samples to test for drift grades
`inconclusive`, never `stable` — otherwise the metric's strongest positive claim
would be made on exactly the data its red flag was never run against. And a
degenerate `MAD(x) = 0`, reachable when a majority of log-ratios tie, is not
perfect precision: it grades `inconclusive` too.

**Per-anchor rollup** is alarm-dominant and uniform across pair kinds:
`drifting` > `noisy` > `stable` > `inconclusive` > `insufficient`; NULL when an
anchor has no pairs. Drift deliberately outranks a tight spread — "looks tight
but is trending" is the red flag this metric exists to raise.

### 1.4 The gates

| gate | value | why |
|---|---|---|
| `MIN_PAIRED_SAMPLES` | 6 | Below this a MAD is not a spread estimate. No claim is made at all — not a weak claim. |
| `MIN_DISTINCT_TIMES` | 5 | A `SampleItem` has no timestamp of its own; several time-windowed segments of one file share that file's `datetime_utc`. Without this gate six segments of one file read as six time points. |
| `MIN_DRIFT_SAMPLES` | 8 | A slope over seven points is not a trend. Between this and `MIN_PAIRED_SAMPLES` a pair can be graded, but never `stable` (§1.3, rule 4). |
| `RHO_DRIFT` | 0.6 | Monotonicity leg. |
| `DRIFT_LOG_RED` | 0.405 = ln 1.5 isotopologue / 0.693 = ln 2 adduct | Effect-size leg, in log units so a rise and an equal fall trip alike: a 1.5x end-to-end change in a fixed isotope ratio is chemically loud; adduct ratios legitimately move more. |
| `SPREAD_STABLE` | `sigma_log` <= 0.15 isotopologue / 0.25 adduct | Isotope physics pins the ratio tighter than source chemistry pins an adduct partition. |
| `SPREAD_RED` | `sigma_log` >= 0.50 | Roughly a factor-of-1.65 typical swing. |
| `MAX_PAIRS_PER_ANCHOR` | 4 | Display cap only; the rollup sees every pair. |
| `MAX_PAIRS_PER_BATCH` | 20,000 | Bounds the pass; excess is surfaced as `partial`, not hidden. |
| `MAX_GAP_FACTOR` | 10 | Time-ordering gate (§1.6). |

**None of these numbers is measured.** They are first-principles starting points,
to be tuned on real time-ordered batches before anyone leans on a grade (§8).
They belong beside the consensus constants in `batch_peaks.py`, not scattered
through the pass.

### 1.5 Which samples count

- **Blanks are excluded on two unlinked axes.** A *signal* blank —
  `instrument_function_id IS NULL` — is refused by `ineligible_reason` ("blank
  sample (no peaks)", `service.py`), never completes a run, and so folds nothing;
  the pass also skips occurrences whose sample resolves that way, since the
  guard is a converter-derived property of the file. A *label* blank —
  `sample_item_type = 'BLANK'` — is excluded by the pass itself, and that filter
  is **load-bearing rather than defensive**: nothing in the tree ties the label to
  the file's instrument function (`import_service.py` says as much), so a
  user-labelled blank whose file has one is assigned, completes runs and folds.
  Auto-created items, meanwhile, are always typed `ACQUISITION`, so the label
  filter never sees the auto case. (`sample_item_type` already drives behaviour
  elsewhere — auto-locking, stale-item scoping, filter-id validation — but the
  `BLANK` value specifically appears only in the filter-id-optional list today.)
- **No wash concept exists** anywhere in the tree, and this note does not invent
  one. Whether washes deserve a sample type rather than a naming convention is an
  open question (§8), not something to encode in a metric.
- **Zero and NULL intensities are dropped**: a zero is the NaN fill the peak read
  applies (`service.py`), not a measurement.
- **Formula-dissenting samples are dropped.** A sample counts for a pair only
  when both occurrences carry a non-NULL `assigned_formula` equal to their
  anchor's `consensus_formula` — the occurrence already denormalizes it. This,
  not the intensity filter, is what keeps unassigned and dissenting peaks out of
  a ratio: they fold with real intensities, from two producers now (§0), so an
  unexplained co-eluting peak at the same m/z would otherwise slip in as a ratio
  point. Record the dissenting count in the blob; it is a useful signal in its
  own right.
- **Absence is not negative evidence.** A sample where an anchor was not observed
  is a gap in the trace, never a point against the formula — the same rule the
  batch design already applies to prevalence.

### 1.6 The acquisition-time-ordering gate

Ordering is strictly by `SampleFile.datetime_utc`, which is non-nullable and is
the column designated for calculations. It is emphatically **not** the
instrument-local `datetime` that the existing batch-peak backfill happens to
order by — a nearby wrinkle this feature must not copy. Segments of one file
share their file's timestamp; `SampleItem.t0` breaks that tie for ordering, and
`MIN_DISTINCT_TIMES` (§1.4) is what keeps them from being counted as separate
time points.

A batch qualifies as time-ordered when:

- its `sample_batch_type` is `ACQUISITION` — those are auto-created per day of
  continuous acquisition, so they are the intended case (and the day scoping
  means grades describe a day-long window, a real v1 limitation worth stating);
  or
- it is an `ANALYSIS` batch whose largest gap **between distinct acquisition
  times** is at most `MAX_GAP_FACTOR` times the median distinct gap. The
  statistics must run over distinct timestamps, not over samples: segments make
  zero-length gaps routine, and a median gap of zero would collapse the rule into
  "largest gap <= 0" and reject an ordinary batch of segmented files. The rule is
  skipped where fewer than `MIN_DISTINCT_TIMES` distinct values exist, since the
  batch cannot qualify anyway. A false rejection costs a badge; a false
  acceptance costs a wrong claim.

A batch that stops qualifying has its evidence **withdrawn** — grades nulled with
a reason — never left frozen and stale.

---

## 2. Where the computation runs, and what is stored

**Decision: a fold-triggered, post-commit pass over the whole batch, persisting
its result.**

**Not inside the locked fold — and the merged precedent makes that a line, not a
rule.** WP21 put two computations *inside* the fold: `max_intensity` and
`isotopologue_of` are rolled up in `compute_consensus` and written by
`_recompute_consensus`, between the advisory lock and the commit. So "the fold is
for folding" is not the distinction, and this note does not claim it. The
distinction is what the computation's cost scales with. Both WP21 aggregates are
functions of *one anchor's members* — rows `_recompute_consensus` has already
loaded — so they add a dict increment per member and one batched owner lookup to
work already being done, over only the anchors that sample touched. Continuity is
none of those things: its unit is a *pair* of anchors, so it cannot be computed
from one anchor's members; a new sample moves every pair's series, so there is no
touched-anchor subset to restrict it to; and it needs the acquisition-time axis
(§1.6), a join the fold does not make. That is order (pairs × samples) over the
whole batch, inside a best-effort critical section that every completed run and
every import publish enters, holding a lock the batch's other samples are queued
on.

Two concessions, because the distinction is thinner than that reads. First,
WP21's own stated reason for materializing was not cost: a read-time derivation
of the family link would have been *destroyed* by run pruning, since
`BatchPeakOccurrence.peak_assignment_id` is `ON DELETE SET NULL` and the
derivation needs the `PeakAssignment` row it points at. That argument does not
reach this pass — the metric's inputs are the occurrence's own denormalized
`intensity` and `assigned_formula`, which a prune leaves untouched — but it does
reach §1.2, and it is why the pairing reads `isotopologue_of` instead of
re-deriving it. Second, the pass reads a column the fold writes, so under READ
COMMITTED it can pair against a link a concurrent fold changes a moment later.
That is survived rather than dismissed: the pass is a full-batch idempotent
overwrite that re-runs after the fold, and a grade one fold behind is a marked
state (below), not a silent one.

**Not at read time.** The ledger read is deliberately metadata-only — it never
touches the occurrence table — against a design ceiling of order 10^5 anchors and
10^7 occurrences ([`peak_assignment_batch.md`](peak_assignment_batch.md) §7);
grades would jitter between two refetches during a backfill; and, decisively, a
human verdict should snapshot **the evidence the human actually saw** (§4), which
requires that evidence to exist as a stable artifact.

**Mechanics.** `recompute_batch_continuity(sample_batch_id)`: a full-batch,
idempotent overwrite in a new module, under its **own** advisory-lock namespace,
transaction-scoped like the fold's. The fold takes its lock late because it must
first open other sessions to read the instrument function, and holding a lock
while checking out more connections is the hold-and-wait shape that deadlocks a
worker (`batch_peaks_controller.py`); the continuity pass opens nothing before
its lock, so it may take it first.

**Coalescing happens in the database, not in the process.** Production runs
several parallel workers, which no in-process gate reaches — that is exactly why
the fold needed a database lock in the first place. So the pass takes
`pg_try_advisory_xact_lock`: a worker that loses **skips** rather than queues,
and a recompute whose `computed_utc` is already newer than the triggering fold is
a no-op. Three trigger sites, named explicitly: after each of the two fold call
sites, and **inside `backfill_sample_batch_peaks` itself** — the backfill calls
the fold directly in a loop rather than through either call site, so "once at the
end of a backfill" has to be wired there. That site now has a progress bar over
it: the backfill emits `compute_batch_peaks` pending packets around each
reporting sample, and the bar is ended by the background task's own terminal
packet, so a recompute wired after the loop would run while the bar already reads
full — the exact shape a user reads as a hang. The pass emits its own pending
packets on that channel, reusing the `process_id` and `parent_id` the backfill
already threads through, rather than leaving a long tail unannounced. A manual
endpoint (`POST /api/batch-peaks/batch/{id}/recompute-continuity`, editor,
flag-gated, 202) completes the set.

**Rollout.** v1 ships with the manual endpoint and the backfill trigger only; the
automatic fold triggers enable once the pass has a measured cost at ceiling scale
in `server/backend/tests/system/benchmark/`. What budget is acceptable is an open
question (§8), not something this note fixes.

**What persists, and where.** Two nullable columns on `batch_peak`:
`continuity_grade` (String(16), CHECK-constrained) and `continuity` (JSON):

```
{ v, computed_utc, eligible, reason,
  n_present_at_compute, n_eligible, n_paired,
  n_excluded_blank, n_excluded_dissenting, n_no_parent,
  span_seconds, warnings: ["partial", ...],
  pairs: [ { kind, partner_batch_peak_id, partner_mz, n,
             ratio_median, ratio_spread, drift_log, drift_fold_display,
             residual_rho, grade } ],
  rollup }
```

JSON follows the `alternatives` / `provenance` precedent on the same table. The
pass must **not** touch `batch_peak_utc_modified`, which is fold bookkeeping.

Explicitly **not** in v1: a per-pair table (nothing needs per-pair queryability,
and a versioned table is a migration-and-backfill treadmill while the constants
are provisional — recorded as a deferred option); a timestamp column on
`batch_peak_occurrence` (it would churn on every wholesale re-fold; the pass
joins the sample view instead).

One composite index on `batch_peak_occurrence (sample_item_id, sample_peak_id)`
is **no longer proposed**, and the reasoning is worth keeping rather than
deleting. WP21 landed and added no index on that table at all: its owner hop goes
through `peak_assignment_id` and rides the index already there, so it was never
the same hop this note assumed it shared. The counterpart read that has since
landed *does* perform this exact lookup and deliberately declines to index it —
it is one indexed scan per sample switch, not per frame. The `anchor-context`
endpoint (§4) is scoped to one sample the same way: it rides
`ix_batch_peak_occurrence_sample_item_id`, with `sample_peak_id` a filter on that
scan rather than a key. So the continuity work owns no index on
`batch_peak_occurrence`, and if a third caller ever makes the composite worth
adding, it will be that caller's to justify.

**Serving it.** `_batch_peak_meta` gains both the grade and the blob. It has two
call sites — the series endpoint spreads it beside `peak_series`, the ledger
endpoint returns it alone — so a ledger row and a series record carry the same
evidence structurally rather than by agreement, and the ledger tooltip needs no
second fetch; those rows are untyped dicts that flow to the frontend untouched.
Two constraints ride along. The ledger read is *defined* by reading `batch_peak`
alone and never joining the occurrence table, which is exactly why WP21
materialized its two aggregates onto the row — so the grade and the blob have to
be anchor columns too, which is what this section stores. And the blob would be
the first JSON field this reader serves; everything it returns today is a scalar,
so a row's size stops being bounded by its column list, which is the reason the
blob is capped at four displayed pairs (§1.2).

Completion emits the existing coarse `peak_assignment_reload` through the
background-task decorator, which the ledger and chart stores already listen for.
One standing caveat: a field served but not rendered is invisible, so the UI work
(§5) is its own increment, not a consequence of this one.

**Staleness is marked, not implied.** The blob records `n_present_at_compute`, and
a badge is stale when the anchor's current `n_present` differs from it — a
comparison against the paired count would fire on every anchor, since paired
samples are always a subset. Staleness shows as a clock icon plus a tooltip line,
**not** as dimming: dimming is the "no value here" idiom the uncalibrated states
already own, and the inspector explicitly declined to overload it.

---

## 3. The within-sample precedent, mirrored honestly

Adduct corroboration is this feature's sibling one level down, and the note
mirrors it deliberately — including where the mirror stops.

| | adduct corroboration (P3, shipped) | temporal continuity (this note) |
|---|---|---|
| signal | one compound seen via several adducts in **one** sample | a ratio holding constant across **many** samples |
| computed | in the engine, per run, winner-only (`_fold_adduct_corroboration`) | in a post-fold batch pass, per anchor pair |
| stored | `provenance.corroboration = {adducts, n_adducts, boost}` on the M0 row | `continuity_grade` + `continuity` on the anchor |
| confidence | **folded into `p_correct`** as a measured per-adduct log-odds update, capped | **nothing** — display only (decision 2) |
| UI grammar | badge on the M0; an isotopologue row shows the inherited **count** in parentheses, not the boost | badge on the anchor; per-sample rows show it as inherited context |

Two asymmetries are the point, and the note states them rather than implying an
equivalence:

1. **Corroboration earned its fold.** Its weights come from a measured
   target–decoy benchmark with an anchor-swap null — which is what turned an
   apparent 104x likelihood ratio into a defensible ~2.5x
   ([`assignment_confidence.md`](assignment_confidence.md) §4, P3). Continuity has
   **no measured weight**, and producing one is precisely the gate for ever
   promoting it (§8).
2. **Corroboration is frozen; continuity is live.** A corroboration boost is
   computed once per run and pinned in that row's provenance. A continuity grade
   changes as samples arrive. That alone disqualifies it from folding into
   per-sample `p_correct`, which is a snapshot. If it is ever promoted, the target
   is the **consensus** `p_correct` slot — today a max over the members backing
   the consensus formula, with the capped multi-sample lift explicitly deferred
   there.

**Copy discipline.** Every continuity tooltip ends with *"Display-only evidence —
not folded into P(correct) or tier."* That is the deliberate inverse of the M0
corroboration tooltip's *"already folded into P(correct)"*. Note that the
existing inherited variant already models a third case — evidence that is real
but folded into *someone else's* number ("via the M0 of this isotopologue family
(folded into the M0's P(correct), not into this row's)") — and the
inherited-overlay copy in §5 should mirror that three-way distinction rather than
collapsing it into a two-way one.

---

## 4. The anchor verdict record

> **Status (2026-09-03): implemented** in the batch-primary epic
> (`epic/batch-primary-assignment`), as specified here, with three notes. The `context`
> snapshot carries the consensus (tier, best fit, support fraction, `n_present`,
> ambiguity, m/z) but no continuity grade, since sections 1-3 are not built. `retract` is
> kept. `expected_formula` on the verify route is mandatory for `confirmed` and
> `rejected`, and section 5's filter-semantics change shipped with it, release-noted.

A new record kind — `batch_peak_verification` — not rows in
`assignment_verification`, and never N fabricated per-sample verdicts.

**Shape.** `batch_peak_verification_id` (PK); `sample_batch_id` (FK
`sample_batch`, CASCADE, indexed — lifecycle and scoping); **`batch_peak_id`,
indexed, with no foreign key**; `assigned_formula` NOT NULL;
`ionization_mechanism_id` nullable; `verdict` and `evidence_level` reusing the
existing CHECK vocabularies verbatim (`confirmed | rejected | unsure`;
`reference_standard | msms | orthogonal | pattern | visual`); `note`; a `context`
JSON snapshotting what the human saw (consensus tier, best fit, support fraction,
`n_present`, ambiguity, and the continuity grade and blob); `verified_by` (FK
user, SET NULL); `verified_utc`; `superseded_utc`.

**Why the reference to `batch_peak` is deliberately unenforced.** A re-fold that
leaves an anchor memberless deletes it, and anchor ids are random, minted
append-only and never reused — so a dangling id can never re-attach to a
different species, and a verdict outlives the machine lifecycle of the anchor it
judged. (§7 records why neither `CASCADE` nor `SET NULL` is right here, including
how this differs from the `isotopologue_of` self-FK the ledger just shipped.)

**Identity.** A partial unique index on
`(batch_peak_id, assigned_formula, ionization_mechanism_id)`
`WHERE superseded_utc IS NULL`, `NULLS NOT DISTINCT` — the same construction the
per-sample table uses, for the same reason.

Two properties follow, both intended. It is **anchor-scoped**, which is what
decision 3 asks for; and it is **claim-pinned**, so if the consensus later flips
from F to G the verdict does not silently transfer to G — it stays live but
visibly stale, and the UI says so. A machine recompute never supersedes a human
label, in the same spirit as fitting the calibration on current verdicts only.
And because a child anchor shares its M0's (formula, mechanism), keying on the
anchor rather than on batch-plus-species is what keeps a child-specific rejection
— an interference at the M+1's m/z — expressible at all. One cost lands with the
merged ledger and belongs on the record: expressible, but only with the pane's
Isotopologues toggle on, since a folded child has no row to carry the write (§5).

**Writing.** The supersede-then-insert machinery is reused verbatim under a new
lock namespace keyed by anchor and claim: stamp the live row's `superseded_utc`
with a NULL-safe match, then insert. Append-only; history is preserved.

A **retract** verb is added — a stamp-only supersede returning the identity to
unverified. This is a deliberate divergence from per-sample parity, where no
withdraw exists, and it is justified by blast radius: one anchor verdict
annotates up to `n_present` per-sample rows, so "I should not have said that"
needs an exit that is not "say something else instead". It is droppable to
unsure-as-neutral if reviewers prefer strict parity — with the caveat that an
`unsure` still badges every member row.

**Calibration semantics: excluded from the label pool, structurally.** This is
the constraint the whole record shape answers to, and the argument has three
legs.

1. **There is no honest score to pair with the label.** The pool's score axis is
   the arbitration `evidence` snapshotted on the judged per-sample row. An anchor
   has no such scalar — `best_fit_score` is a max over the fits of the members
   backing the consensus formula, and the consensus `p_correct` a max over those
   same members' probabilities; a scalar that already discards the dissenters.
   Manufacturing one would fabricate a number, which the confidence layer's own
   rules forbid.
2. **Fan-out would distort the pool.** Every live `confirmed`/`rejected`
   verification of an in-app run, on the instrument being recalibrated, with a
   non-NULL evidence is one label — no dedup, no grouping, no weighting. Those
   labels feed two floors: 30 in total (`MIN_CALIBRATION_LABELS`) and 10 of
   *each* class (`MIN_CALIBRATION_CLASS_LABELS`). One confirmation fanned out
   over a 50-sample batch would be 50 highly correlated positives. They cannot
   open the gate alone — the fit refuses fewer than ten rejections — but once ten
   rejections exist anywhere they swamp a positive class whose total floor is
   only 30. Worse, the **provisional** gate counts strong positives only, against
   a threshold that defaults to `MIN_CALIBRATION_LABELS` — thirty of them — so a
   single reference-standard judgment fanned across any batch of thirty samples
   or more could graduate an instrument's curve from provisional to real on one
   human opinion; the fifty-sample batch above clears that comfortably. That is
   the label-echo failure the verification design names as its central risk,
   arriving through the side door.
3. **A separate table makes the exclusion structural.** The pool selects from
   `assignment_verification` alone, so exclusion needs no `WHERE`-clause
   discipline that a future contributor must remember. Both tables' docstrings
   should state the invariant, because a table-unification refactor is the one
   path that could silently reopen it.

**One-label semantics** — admitting an anchor verdict as a single label — is
deferred to the same future decision as confidence promotion, and gated on the
same missing thing: a defensible anchor-level evidence score.

**Endpoints** (writes editor-gated behind the feature flag, reads open, per the
existing pattern):

- `POST /api/batch-peaks/batch/{sample_batch_id}/verify` → 201. Body carries
  `batch_peak_id`, `verdict`, an `evidence_level` (required to confirm), a `note`,
  and `expected_formula` — **required for `confirmed` and `rejected`**, optional
  only for `unsure`. The server validates that the anchor belongs to the batch and
  **snapshots the claim from the current consensus itself**, never from the
  client. A NULL `consensus_formula` is a 422: an unassigned anchor has no species
  claim to verify. A mismatched `expected_formula` is a 409. Making the token
  mandatory is what makes it a guard: unlike a per-sample identity, an anchor's
  claim can flip under *another sample's* fold between the user reading the row
  and the POST landing, and an optional token would protect only well-behaved
  clients.
- `POST .../retract`.
- `GET /api/batch-peaks/batch/{sample_batch_id}/verdicts` — full history, newest
  first, each live row carrying the current consensus claim so staleness is
  visible.
- `GET /api/batch-peaks/sample/{sample_item_id}/anchor-context` — one indexed
  query returning, per occurrence of the focused sample, the sparse set of rows
  that actually carry a grade or a verdict. This single endpoint serves both the
  per-sample ledger overlay and the inspector.

No bulk verify: a batch of anchors confirmed in one gesture is exactly the
correlated judgment the pool exclusion exists to keep from meaning more than it
does.

---

## 5. Overlay precedence and UI

**Precedence, in order:**

1. A live **per-sample** verdict wins — including a per-sample *rejection* over an
   anchor *confirmation*. Exceptions are the point of decision 3. The existing
   family resolution through the M0 is untouched.
2. Otherwise the anchor verdict applies **iff** the family M0's occurrence in that
   sample maps to the verdicted anchor **and** the claim matches NULL-safely on
   (formula, mechanism). The claim-equality clause is load-bearing: a dissenting
   row whose own assignment says G gets no overlay from a verdict about F. The
   anchor-id join keeps coverage exactly to the samples whose peak actually folded
   into the judged anchor.
3. Otherwise unverified.

Family scoping composes with **no new machinery**: isotopologue rows already
inherit their M0's badge through the existing family resolution, so an anchor
verdict that reaches the M0 reaches the family. In the batch ledger the fold is
now the mechanism rather than a redirect, and it is stronger than a redirect: the
pane folds children under their M0 by default, a folded child is not drawn, and
the selection invariant strips every row with a `parentId` out of
`ledger.selected` whenever the toggle is off — so there is no selected child row
for a write to be redirected *from*. The M0 is the only row, which matches the
inspector's existing "One verdict per compound: recorded on the M0". Unfolded, a
child is drawn indented and labelled `M+1` rather than repeating the family
formula, and that is the only state in which the child-specific rejection §4 keys
the identity on can be recorded. Worth saying plainly at sign-off: the capability
§4 preserves lives behind a toggle that is off by default.

**Batch-peak ledger** (`PaneBrowserBatchPeaks.vue`): the table already carries a
selection checkbox plus m/z, Intensity, Formula, Tier and Samples, so these are
additions on the right — **Continuity** after Samples, since it is evidence about
the trace the prevalence column counts, and **Verdict** last. Continuity renders
a badge only for actual claims — stable, drifting, noisy — and nothing for
inconclusive, insufficient or NULL, with the pair detail, any `partial` warning,
the `mixed_parent` warning if §8 decides it is earned, and the display-only
sentence in its tooltip. Sorting is the pane's, not PrimeVue's: the table is
`lazy`, so a `continuityRank` joins `tierRank` in the `decorated` projection and
the column sorts through `compareBy` with `byConfidence` breaking ties, which
also puts NULL grades last in both directions through `isBlank`. It belongs in
the pane rather than the store, which deliberately holds no derived population
because deciding which anchors are species means resolving `isotopologue_of`
against the whole loaded list. Any Continuity filter chip must count `parents` —
the population the breadcrumb and the tier strip already count — or the three
disagree by the size of the folded tail. A **Verdict** column with a compact
badge and a popover carrying Confirm / Reject / Unsure, the evidence-level select
that confirming requires, a note, and Retract; editor gating reuses the pane's
existing blocked-reason pattern. The popover states the scope in words: *one
verdict per species at this anchor; it covers every sample in this batch that has
no verdict of its own; per-sample verdicts always win.* A stale claim renders
warn-outlined: *"Confirmed as F — the consensus is now G. Re-judge or retract."*

**Per-sample ledger**: the established grammar for inherited-not-owned evidence
there is **parenthesising the borrowed value plus a tooltip naming where it came
from** (`PaneBrowserAssignment.vue`, the adduct-corroboration count an
isotopologue row inherits from its M0); there is no dashed or outlined idiom on
those rows, and the overlay must **not** invent one — dashed already reads
"unassigned" via `BaseTierTag` on the same rows. Because the ledger's verdict
badge is a borderless icon in compact mode (`BaseVerdictBadge.vue`), an overlay
marker needs its own affordance: a distinct icon, or the parenthesis idiom, with
a tooltip naming the batch-level verdict, who set it and when, and saying that
verifying this sample records an exception. Where both exist and disagree, one
extra tooltip line on the per-sample badge.

**Inspector** (`PanePeakAssign.vue`): here the dashed/outlined pill *is* the
established grammar for borrowed evidence, so the overlay state uses it, shown
above the capture controls — *"Confirmed at batch level — a verdict here records a
per-sample exception"* — plus one continuity line, both from the single
anchor-context fetch.

**Filter semantics change, and get release-noted**: the per-sample verdict filter
switches to the *effective* verdict, so "Unverified" stops listing rows that
visibly carry a badge. A filter that contradicts what the user can see is worse
than a documented change. Keeping the filter strictly per-sample is the
conservative fallback if reviewers disagree.

---

## 6. Interaction with assignment copy

Continuity is anchor-level, so copied runs' anchors inherit it **automatically**:
a copy publishes through the import channel under the reserved `mascope-copy`
engine and folds at finalize like any other run, and the next continuity pass
simply sees more samples. What shipped is the seeded re-score, so every ratio
point is a genuine per-destination measurement.

**The caveat this note carried is wrong, and is withdrawn.** A literal copy would
not echo the source's intensities either: `build_copied_rows` takes the peak
identity — id, m/z and intensity — from the destination's own peak file *outside*
the `rescore` branch, and the fold writes that same `sample_peak_intensity` onto
the occurrence. Only the evidence columns (fit, mass error, abundance error,
tier) are mode-dependent, and the literal mode survives solely as an internal
`rescore=False` the launch route never forwards. So there is no circularity to
mitigate. Two things follow instead. What a literal copy *would* distort is the
evidence columns, and this note consumes those only through the pair-formation
gate (`consensus_tier` in {`assigned`, `candidate`}) — it would change which pairs
form, never what a formed pair measures. And had the exclusion been needed, it
would not have worked: the hop occurrence → `PeakAssignment` → run engine dies on
`prune_peak_assignment_runs`, which deletes superseded runs, cascades to their
rows and SET NULLs the occurrence's `peak_assignment_id`, so a pruned link reads
as *unknown* rather than as *not copied* — and copied runs are exactly what
pruning reaches, since only the in-app engine is exempt from
`keep_per_sample_total`. The per-row `copied_from` provenance is one hop instead
of two but dies in the same cascade, so it is no way out either.

Three consequences are live rather than hypothetical. **Placeholders**: every
unmapped destination peak gets an `unassigned` row carrying the destination's
real intensity (`build_placeholder_rows`, the copy's mirror of
`build_unassigned_assignments`), so a copy-heavy batch is largely placeholders
and §1.5's formula-dissent filter — not the intensity filter — is what keeps them
out of every ratio. **Supersession**: the fold is engine-blind and latest-wins,
so a copy replaces the destination's prior contribution to the batch view and
every series through that sample can change wholesale; that is a staleness event
for the grades (§2), not a corruption. And a copy-heavy batch's "stable" is
evidence of measurement consistency, not of N independent human judgments.

"Verifications do not copy" is now implemented rather than merely designed:
neither the copy service nor the import services reference
`AssignmentVerification` at all. Two reinforcements land in this design's favour.
Copied rows carry no calibrated `p_correct` — the import strips `p_correct`,
`calibration` and `corroboration` unconditionally at staging, with no bypass for
the reserved engine — and a verdict recorded on a copied run is already excluded
from the calibration label pool, which admits only
`engine IS NULL OR engine = 'mascope'`. An anchor verdict is a distinct record
kind, is never produced by copying, and never crosses batches; §4's exclusion is
the same instinct one level up, and worth an explicit nod at sign-off.

---

## 7. Rejected alternatives

Read-time computation, computation inside the locked fold, and a timestamp column
on `batch_peak_occurrence` are rejected in §2 and not re-argued here.

- **Replacing per-sample verdicts with anchor verdicts.** Violates decision 3,
  and it would starve the calibration pool, whose score axis only exists per
  sample. The per-sample verdict is the labelled ground truth the confidence loop
  is built on; the anchor verdict is a convenience layer above it.
- **Fanning an anchor verdict out into per-sample rows** (including the
  "apply to all samples" button variant). It fabricates human judgments nobody
  made and distorts the label pool with correlated labels (§4). This is the single
  most important thing this design refuses to do.
- **Adding a nullable scope column to `assignment_verification`** instead of a new
  table. Pool safety would become forgettable `WHERE`-clause discipline in a query
  that filters neither scope nor batch today.
- **Raw correlation as the primary metric.** Undefined without dynamic range,
  inflated by the common mode, and it answers a weaker question than ratio
  stability does (§1.1). Kept as a secondary, tooltip-only number.
- **A verdict identity of (batch, formula, mechanism)** rather than the anchor.
  A child anchor shares its M0's formula and mechanism verbatim, so one verdict
  would indistinguishably cover the M0 and every isotopologue in the family, and
  a rejection aimed at one child's m/z would be inexpressible.
- **A bare anchor-id identity with no claim pin.** A routine consensus flip would
  re-target a human confirmation onto a formula nobody judged.
- **`ON DELETE CASCADE` from the verdict to `batch_peak`.** It destroys human
  labels on a routine refold. **`ON DELETE SET NULL`** — the house pattern for
  exactly this concern, used by `AssignmentVerification.peak_assignment_id` so a
  label outlives a re-run, and now by `batch_peak.isotopologue_of`, a
  self-referential FK to the very column a verdict would point at — is rejected
  too, for a different reason: nulling `batch_peak_id` erases *which anchor was
  judged*, and that is the verdict's whole identity. The two references are not
  alike. A nulled `isotopologue_of` loses a derived link that the next fold
  recomputes, and degrades to the correct reading "not an isotopologue"; a nulled
  verdict reference loses the only record of what a human looked at, and nothing
  recomputes it. Hence the unenforced reference (§4).
- **A one-sided drift statistic** such as `|exp(slope * span) − 1|`. Bounded by 1
  for any falling ratio, so a collapsing adduct ratio could never trip a threshold
  above it, and equal rises and falls would score differently — discarding the
  symmetry the log metric is chosen for (§1.3).
- **Folding continuity into `consensus_tier` or `is_ambiguous`.** Decision 2 by
  the back door, and it mixes evidence axes the design keeps separate on purpose.

---

## 8. Open questions

1. **The promotion path.** What measurement would let continuity earn its way into
   confidence? The honest answer mirrors P3: a null-model benchmark
   (anchor-swap style) yielding a likelihood ratio per grade, then a capped
   noisy-OR at the **consensus** `p_correct` slot, possibly using `residual_rho`
   as the correlation discount. Until that exists, display-only is not a staging
   decision — it is the truthful one.
2. **Threshold tuning.** Every constant in §1.4 is provisional; they need a pass
   over real time-ordered batches before anyone treats a badge as a fact.
3. **The rollout budget**: what per-batch runtime at ceiling scale (order 10^5
   anchors) is acceptable before the automatic fold triggers are enabled?
4. **Do anchor verdicts ever earn one-label entry to the calibration pool, and
   against which score?**
5. **Is the ANALYSIS-batch gap heuristic enough**, or should time-ordering be an
   explicit per-batch opt-in?
6. **Washes**: a sample type, or a naming convention the metric should not know
   about?
7. **What survives of `mixed_parent`, and how much a NULL link means.** The merged
   pairing rule resolves a split family silently to NULL and surfaces no warning,
   so a disagreement signal is now a second pass over the members rather than a
   by-product of forming the pair. Is one worth the read — and should the pass
   distinguish the five reasons `isotopologue_of` is NULL (§1.2), only one of
   which means "this is an M0"?
8. **Day-scoped ACQUISITION batches** mean per-day grades; is a cross-day window
   ever wanted?
9. **Staleness signalling**: the passive clock marker, or an event when a
   recompute changes a grade a user is looking at?
10. **Sign-off on the copy reading** in §6, and on the effective-verdict filter
    change in §5.
11. **What `unsure` means at anchor level**, and which evidence levels are even
    meaningful for a verdict judged from a trace rather than from a spectrum.

---

## 9. Suggested increments

Four independently landable slices, once this note is signed off:

- **A — backend evidence**: migration (two anchor columns on `batch_peak`,
  parented onto `b6a4d1e83c7f`, the current head; no index on
  `batch_peak_occurrence`), the pure metric module, the pass and its three
  trigger sites with the manual-and-backfill rollout and the backfill's progress
  packets, the read-model extension, and unit tests over synthetic series — flat,
  drift-injected (both directions), spiked, tied-values (degenerate MAD), chained
  and split isotopologue links, duplicate-timestamp, and dissenting-formula
  samples. The link fixtures extend the existing isotopologue fold tests and the
  migration test the existing ledger-column test, rather than starting new files.
- **B — backend verdicts**: the table, the endpoints, supersede / retract /
  stale-claim / 409 tests, and a race test through the lock seam.
- **C — batch-ledger UI**: both columns, added to the pane's `decorated`
  projection beside `tierRank` rather than to the ledger store, the verdict
  popover, and the fold interaction — a child row carries a verdict only while
  the Isotopologues toggle is on — extending the pane's existing spec.
- **D — per-sample overlay**: precedence rules, claim-mismatch behaviour, and the
  filter release note.

---

## 10. Related documents

- [`peak_assignment_batch.md`](peak_assignment_batch.md) — batch peaks, the fold
  and the consensus; this note adds an evidence layer on top of its anchors.
- [`assignment_confidence.md`](assignment_confidence.md) — the confidence layers,
  and the P3 corroboration this feature mirrors (§3).
- [`verification_calibration_loop.md`](verification_calibration_loop.md) — the
  verdict machinery and the calibration label pool this design must not pollute.
- [`verification_capture_frontend.md`](verification_capture_frontend.md) — the
  per-sample capture UI the overlay renders beside.
- [`peak_assignment_copy.md`](peak_assignment_copy.md) — the copy design whose
  "verdicts do not copy" rule §6 preserves.
