# Peak-Centric Batch View: Batch Peaks & the Merged Ledger — Design & Plan

*The batch-level layer of the peak-centric paradigm
([`peak_assignment_paradigm.md`](peak_assignment_paradigm.md) Phase 4; the frontend's
item **E** in [`peak_assignment_frontend.md`](peak_assignment_frontend.md) §6). Where the
sample view assigns a composition to every peak in one sample, this document defines how
those per-sample assignments become a **batch-level object** that the "batch overview"
chart can visualize, and a queryable **merged ledger** across the batch.*

> Status: design. Decisions marked **[settled]** are product-owner calls made during design
> review; **[open]** items carry a recommendation. No code has been written yet.

---

## 1. Why this is the remaining gap

The batch overview is one of the main figures in the app. Today it is driven **entirely by
the legacy targeted pipeline**: [`ChartBatchOverview/data.js`](../../server/frontend/src/lib/charts/ChartBatchOverview/data.js)
draws **one Plotly trace per `target_ion_id`**, x = a per-sample field (default `datetime`),
y = `MatchIon.sample_peak_intensity_sum` per sample, confidence encoded in the marker shape
(`match_category`). Its data comes from `POST /match/records/ion/series`
([`records/ion/service.py`](../../server/backend/src/mascope_backend/api/new/match/records/ion/service.py))
scoped by `sample_batch_id`.

That works because **a target ion has a stable identity in every sample** (`target_ion_id`),
so a series is just "this ion's value in each sample." **Peak assignments have no such
cross-sample identity.** Each sample is assigned independently (one `PeakAssignmentRun` per
`sample_item_id`); the same neutral formula in samples A and B is two unrelated
[`PeakAssignment`](../../server/backend/src/mascope_backend/db/models.py) rows keyed by
different `sample_peak_id`s. There is no batch-level peak-assignment model, query, or read
endpoint — only a write orchestrator that fans out per-sample runs
([`peak_assignments/batch.py`](../../server/backend/src/mascope_backend/api/new/peak_assignments/batch.py)).

So the batch overview cannot plot per-sample assignments: there is nothing to draw a trace
*of*. The missing keystone is a cross-sample identity for an assigned species — the
**batch peak** — and its aggregate, the **merged ledger**.

```
TARGETED (works today)                    PEAK-CENTRIC (the gap)
TargetIon  (1 id, every sample)           PeakAssignment rows, per sample, no shared id
  │ MatchIon per (ion, sample)               sample A: C10H16O3  (peak_id 812)
  ▼ sample_peak_intensity_sum                sample B: C10H16O3  (peak_id 149)  ← unrelated
one trace per target_ion_id                  sample C: C10H16O3  (peak_id 933)
                                          ??? no cross-sample identity, no series endpoint
```

---

## 2. The core idea

**Identity is m/z; formula is an annotation.** A **batch peak** is a cross-sample m/z
cluster (an *anchor*). Every per-sample peak — assigned or not — belongs to exactly one batch
peak, so even unassigned m/z get a batch identity and a trend (this is the "keep all the
data, don't omit anything" requirement). The batch peak's **formula and tier** are a
**consensus of the per-sample `PeakAssignment` rows** that fall in it — never a fresh
assignment of a synthetic spectrum.

Two properties make this work and are validated below:

1. The batch peak's m/z anchors are **append-only and frozen** — a new sample can join an
   existing anchor or create a new one, but never re-draws existing anchors. This gives a
   **stable `batch_peak_id`** that genuinely replaces `target_ion_id` as the thing the chart
   draws one trace per, and it makes the aggregate correct under **incremental arrival**.
2. Assignment stays **per-sample**; the batch peak only **aggregates** it. The fit score
   needs a real per-sample isotope envelope + SNR, so a bare consensus m/z cannot be scored
   honestly — the formula must come from the members' own per-sample fits.

---

## 3. Evidence base

These numbers come from driving the **real production binning code**
([`Spectra._cluster_and_map_peaks`](../../libraries/tools/src/mascope_tools/alignment/calibration.py))
on the demo bundle's golden peak table (`.runtime/demo/1.1.0/expected/peaks.parquet`):
161 real Orbitrap samples over ~1.5 h (80 neg_Br + 81 pos_Ur), 76–373 matched peaks each,
with the target formula per peak as ground truth.

**(a) m/z alone cannot define a batch peak.** Resolution/bin-width sweep on the 80 neg_Br
samples (14,710 peaks):

| bin width | batch peaks | mixed (≥2 formulas collide) | split (1 formula fragmented) |
|---|---|---|---|
| wide (R≈30k) | 268 | 58 clusters (22%) | 14.7% of formulas |
| mid (R≈60k) | 307 | 52 (17%) | 21.4% |
| narrow (R≈240k) | 349 | 35 (10%) | 25.4% |

Widen the window and distinct formulas **collide**; narrow it and one formula **splits**
across adjacent bins. No width is clean on both — **m/z must be disambiguated by the
assigned formula.**

**(b) Greedy re-binning is unstable under streaming; append-only anchors are not.** Re-running
the greedy clusterer on time-ordered prefixes, the representative m/z of *existing* batch
peaks drifts with median 0.01–0.1 ppm but **p95–max of 23–87 ppm** — i.e. a tail of bins
jump identity when a new sample arrives (the greedy leftmost-leader window re-anchors and
cascades). Replacing it with **frozen append-only anchors** (snap-or-create, symmetric ±tol):
existing traces have **zero drift by construction**; a new sample only *adds* traces. After
10 samples ~90% of all final traces already exist and are immutable; the rest are genuinely
late-appearing species plus a noise-singleton tail.

**(c) The per-sample assignment consensus resolves (a).** With append-only anchors
(±4 ppm) + an **evidence-weighted** consensus (weight each member vote by `fit_score` × signal):
**92% of anchors get a clean dominant formula** (consensus support ≥ 0.9). Collapsing anchors
that share a consensus formula heals the split (393 raw m/z anchors → **295 clean species**);
the ~10% mixed anchors are flagged as blends/degeneracies to **split**, not averaged. The
same profile reproduces on pos_Ur (574 → 427 species). Per-sample calibration on this
Orbitrap demo is negligible (offset spread 0.12 ppm; residual jitter 2% better than raw), but
the design keeps a per-sample offset step for TOF / long runs.

**Conclusion:** batch peak = **frozen m/z anchor (identity) + evidence-weighted per-sample
consensus (formula/tier), append-only under arrival.**

---

## 4. Existing assets to reuse

The building blocks already exist; the new work is a persisted, stable identity + the
per-sample fold-in + the read/chart wiring.

- **Cross-sample binning primitive.**
  [`Spectra._cluster_and_map_peaks`](../../libraries/tools/src/mascope_tools/alignment/calibration.py) /
  `compute_sum_spectrum` (calibration.py:167) / `get_timeseries` (calibration.py:224) already
  do resolution-adaptive FWHM clustering and produce a cluster-m/z × sample matrix with
  per-bin membership. `get_sample_batch_peaks`
  ([`sample_batches_controller.py:965`](../../server/backend/src/mascope_backend/api/controllers/sample/batches/sample_batches_controller.py))
  wraps it with virtual-lock-mass alignment and a Zarr cache. **Use these as the *offline /
  backfill* clustering seed — not as the live identity** (see §8).
- **Per-sample mass offset (μ).** Targeted matching fits a per-sample median-ppm offset
  (`fit_sample_mass_accuracy`,
  [`match/lib/match_score_v2.py`](../../server/backend/src/mascope_backend/api/controllers/match/lib/match_score_v2.py)),
  and it runs on the arrival path **immediately before** assignment, so μ is available at
  fold-in with no extra work.
- **The join is free.** `BatchPeakOccurrence.sample_peak_id` == `PeakAssignment.sample_peak_id`
  (`String(20)`, [`models.py:1134`](../../server/backend/src/mascope_backend/db/models.py));
  attaching a member's per-sample assignment is a 1:1 join, no re-computation.
- **Append-on-arrival precedent.** The targeted batch overview stays fresh via a per-sample
  `sample_match_created` event → `handleNewSample` in-place series append (data.js:100-139).
  Copy this exactly (§6).
- **Columnar series shape.** `get_match_ion_series` is the response template for the new
  endpoint (§6).
- **Scoring reality.** `score_pattern_v2`
  ([`heuristic_filter.py:881`](../../libraries/tools/src/mascope_tools/composition/heuristic_filter.py))
  returns 0 without a real monoisotopic peak + envelope + SNR (docstring at :901-906) — the
  hard reason a consensus spectrum cannot be scored.

---

## 5. Design

### 5.1 The batch peak = frozen m/z anchor; formula = consensus annotation

A batch peak is identified by a **frozen anchor m/z** with a **resolution-adaptive tolerance**
**[settled]** (half-FWHM from the per-file resolution function already loaded in
[`util.py`](../../server/backend/src/mascope_backend/api/controllers/sample/batches/lib/util.py),
plus a small calibration-drift margin). Membership uses the frozen tolerance so a past
sample's peak never flips bins when a later sample arrives; the *display* center may track an
intensity-weighted running mean, but the *boundary* does not move. Formula/tier are an
annotation layer; an **unassigned batch peak is a first-class, drawable trace**.

### 5.2 Data model

Two tables, mirroring the `PeakAssignmentRun` / `PeakAssignment` reproducibility stance.

**`BatchPeak`** — the stable cross-sample identity that replaces `target_ion_id`:

| column | meaning |
| --- | --- |
| `batch_peak_id` (PK) | stable id; the chart trace key |
| `sample_batch_id` (FK) | scope |
| `ionization_mode` | batch peaks are **partitioned per mode/instrument** (intensity units differ) |
| `mz` | frozen anchor center |
| `mz_tol` | resolution-adaptive membership tolerance (captured at creation) |
| `intensity_variable` | `sum_peak_heights` (orbi) / `sum_peak_areas` (tof) |
| `consensus_formula`, `consensus_ion_formula`, `ionization_mechanism_id` | materialized consensus |
| `consensus_tier` | rolled up over **detected** samples |
| `best_fit_score`, `support_fraction`, `is_ambiguous` | evidence + honesty flags |
| `n_present` | prevalence (how many samples) — kept **separate** from confidence |
| `alternatives` (JSON) | runner-up consensus formulas (ties / blends) |

**`BatchPeakOccurrence`** — the sparse per-sample matrix and source of truth:

`(batch_peak_id FK, sample_item_id FK, sample_peak_id, intensity, tier, fit_score,
assigned_formula, peak_assignment_id FK)`, unique on `(batch_peak_id, sample_item_id)`.

At ~thousands of peaks × thousands of samples this is ~10–25M rows — **stored sparse only;
the dense P×N matrix (1–2 GB) is never materialized.** `BatchPeak` carries the materialized
consensus; occurrences are the truth it is derived from.

### 5.3 Stage A on arrival — the append-only fold-in **[settled]**

Stage A runs for the batch on **every sample arrival**. On the arrival path a sample is
processed → targeted match runs (yielding μ) → per-sample Stage-A assignment runs
(`auto_assign_sample_peaks`,
[`service.py:842`](../../server/backend/src/mascope_backend/api/new/peak_assignments/service.py)).
Immediately after, fold that sample into the batch peaks:

```
for each peak p in the arriving sample:
    p_mz' = p_mz * (1 - mu_sample / 1e6)          # per-sample offset onto the batch axis
    a = nearest BatchPeak of this batch+mode with |a.mz - p_mz'| within a.mz_tol   # binary search
    if a exists:  insert BatchPeakOccurrence(a, sample, p, join PeakAssignment)
    else:         create BatchPeak(anchor = p_mz'),  insert its first occurrence
recompute consensus for the TOUCHED batch peaks only
```

Cost is **O(P·log A)** per sample (P peaks, A anchors) — bounded per arrival, not the
O(N²) full re-bin the Zarr path would incur. No global re-cluster, no cache wipe. The hard
"virtual lock mass present in every sample" requirement of the VLM aligner is **dropped** for
the live path (it is a single point of failure that worsens as the batch grows); the per-sample
μ replaces it, and VLM stays only as an optional offline refinement.

**Seeding [open, recommended].** Where a target-ion m/z list exists for the batch, seed the
initial anchors from it, so bins are not hostage to the first sample's calibration and line up
with existing target identities; data-created anchors fill the rest.

**Backfill.** For batches assigned before this feature, a one-time fold-in walks each sample's
latest completed run in time order through the same routine.

### 5.4 Consensus roll-up

Per touched batch peak, over its **detected** members (never over absent samples):

- **Formula:** evidence-weighted vote — weight each member by `fit_score` (and signal), so a
  high-confidence identified member outweighs several low-SNR candidate flips. Report the
  winning formula's **support fraction**.
- **Tier:** `identified` only when an evidence-weighted majority of detected members agree at
  `identified` on the same formula; else `candidate`; else `below_assignability`.
- **p_correct:** reuse the per-sample corroboration fold-in as the template but **cap the
  multi-sample lift** (noisy-OR with a correlation discount) and **preserve `None`** for
  uncalibrated instruments — never fabricate a probability.
- **Honesty:** if the top-two consensus candidates tie (reuse `DEFAULT_TIE_TOL` /
  [`engine.py`](../../server/backend/src/mascope_backend/api/new/peak_assignments/engine.py)),
  mark `is_ambiguous` and keep both as `alternatives`. If disagreement partitions cleanly
  along the intensity/time axis, that is a **co-eluting blend** — flag it (and, later,
  consider splitting the batch peak) rather than averaging two chemistries into one trace.
- **Prevalence is orthogonal to confidence.** `n_present` / `support_fraction` answer "how
  often" and never dilute "how sure." A chart gap at an absent sample is not evidence against
  the formula.

### 5.5 Stage B is user-triggered **[settled]**

Untargeted assignment (Stage B) is computationally heavy, so it is **not** run on arrival. The
batch overview is fully usable with Stage-A annotations plus unassigned traces. A user-triggered
batch Stage-B pass enriches the annotations: it re-derives consensus for the affected batch
peaks from the newly untargeted per-sample assignments. Because identity is m/z (not formula),
Stage B changes *labels*, never the set of traces.

---

## 6. Read path

**Series endpoint** — a shape-for-shape clone of `get_match_ion_series`:

```
POST /api/batch-peaks/records/series
body: { sample_batch_id | sample_item_ids, batch_peak_ids?, tier?, source? }
→ data: [ { batch_peak_id, mz, consensus_formula, consensus_tier, n_present,
            peak_series: { sample_item_ids[], intensities[], tiers[] } } ]
```

`batch_peak_id` ↔ `target_ion_id`, `peak_series` ↔ `match_series`, `tier` ↔ `match_category`.
Reads the materialized `BatchPeak` consensus + the occurrence arrays for the selected peaks —
the cheap **MatchIon-style materialized read**, not the O(samples×targets) on-the-fly aggregate.

**Append-on-arrival event.** Add a per-sample `sample_batch_peak_created`
(operation `created`, payload `{sample_item_id, sample_batch_id}`, room = `sample_batch_id`),
mirroring `sample_match_created`
([`match_aggregate_controller.py`](../../server/backend/src/mascope_backend/api/controllers/match/aggregate/match_aggregate_controller.py)).
Ensure `_notification_data` carries the room key or the reload is silently dropped. The current
coarse `peak_assignment_reload` (full resync) is the wrong granularity for streaming. **Coalesce**
these during bulk `re_process` so a 5,000-sample import does not emit 5,000 events + fetches.

**Chart.** A new `chart.batch.assignments` store clones
[`ChartBatchOverview/data.js`](../../server/frontend/src/lib/charts/ChartBatchOverview/data.js):
rename `target_ion_id`→`batch_peak_id`, `match_series`→`peak_series`, keep the `shallowRef` +
splice-stale + push + `triggerRef` merge and the `handleNewSample` append **verbatim**; tier
drives the marker. It **coexists** with the Targets overview via a mode toggle in
[`PaneTabBatch.vue`](../../server/frontend/src/lib/panes/PaneTabBatch.vue). The x-axis machinery,
TIC trace, sum/avg + log/lin scale, and click-to-focus are reused unchanged. Species are chosen
from a **batch Assignments browser** (the merged-ledger table: species · support · consensus
tier · agreement/QC), the batch analog of the sample-view Assignments tab.

---

## 7. Scale & performance

- **Sparse only.** Occurrences ≈ pooled peak count (~15M for 5,000 samples, ~6% density).
  Never build the dense matrix.
- **Occupancy filter [open, recommended default: present-in ≥ 2].** The batch-peak axis grows
  toward 10^5 with the noise/satellite tail; a min-present-fraction gate decides which batch
  peaks are *drawable* by default (singletons kept in the ledger, out of the default chart
  scope). Adjustable.
- **Indexes.** `BatchPeak(sample_batch_id, mz)` for the fold-in range scan (hot path);
  `BatchPeakOccurrence(batch_peak_id)` for series fan-out and `(sample_item_id)` for the
  single-sample slice fetch. Model after
  `ix_match_ion_target_ion_id_match_score` (models.py:983). Chunk series requests at 100 batch
  peaks (reuse the existing 100-ion chunking).
- **Per mode/instrument.** Partition batch peaks by `(sample_batch_id, ionization_mode)`;
  refuse or segregate mixed orbi+tof batches at fold-in (height vs area units cannot share a
  trace) — the alignment path already rejects mixed instruments.
- **Benchmarks.** Extend `server/backend/tests/system/benchmark/` with: fold-in latency as the
  batch grows 100→5,000 (assert bounded per-arrival cost), series-endpoint latency for P
  selected × 5,000 samples, and the batch-peak axis-growth curve validating the occupancy filter.

---

## 8. What NOT to do (rejected options, with reasons)

- **Don't reuse the `get_sample_batch_peaks` Zarr cache as the identity.** It is positional
  (no stable key), wiped+recomputed on every batch change, stores membership as lossy
  comma-joined `peak_id` strings, and its greedy clustering re-anchors under arrival. Good
  algorithm to seed/backfill from; wrong identity.
- **Don't score a summed/consensus spectrum.** `score_pattern_v2` needs real per-peak SNR (the
  alignment path's SNR is a `np.ones` placeholder) and a single-offset mass axis; the consensus
  axis is a mixture. Consensus m/z is for candidate *enumeration* only.
- **Don't use greedy or single-linkage clustering as the live identity.** Any partition
  "defined by the data seen so far" changes on every arrival. Use frozen append-only anchors.
- **Don't key the chart trace on formula.** That drops every unassigned peak — exactly the data
  the m/z-keyed design keeps.
- **Don't require a VLM in every sample.** `min_fraction=1.0` throws away the whole batch if one
  sample lacks the shared lock mass; use the per-sample μ instead.

---

## 9. Phased plan

Each phase is intended to land independently.

1. **Model + fold-in.** `BatchPeak` / `BatchPeakOccurrence` + Alembic migration; the
   append-only snap-or-create helper (resolution-adaptive tolerance, μ correction); hook into
   the arrival path after `auto_assign_sample_peaks`; a backfill over existing runs;
   consensus materialization for touched bins.
2. **Read + event.** The `.../records/series` endpoint; the `sample_batch_peak_created` event
   (room wiring + bulk coalescing); the occupancy filter.
3. **Chart.** `chart.batch.assignments` store (clone), mode toggle in `PaneTabBatch`,
   tier-marker, `handleNewSample` append.
4. **Merged-ledger browser + confidence.** The batch Assignments table (species · support ·
   agreement/QC); ambiguity/blend surfacing; the distribution roll-up.
5. **Stage-B enrichment + hardening.** User-triggered batch Stage-B re-consensus; per-mode
   partitioning; indexes + benchmark cases; optional VLM refinement for well-behaved single-mode
   batches.

---

## 10. Design decisions

**Settled (product-owner):**

1. **Stage A folds into the batch on every sample arrival** (incremental, per-sample).
2. **Stage B is user-triggered** (too heavy for arrival); it re-annotates, never changes the
   trace set.
3. **Anchor tolerance is resolution-adaptive** (half-FWHM + drift margin), not fixed ppm.

**Open (recommendations):**

1. **Seed anchors from the batch's target-ion m/z list** where present (avoids first-sample
   calibration bias; aligns with target identities). *Recommend yes.*
2. **Occupancy filter default = present-in ≥ 2 samples**, user-adjustable. *Recommend this.*
3. **Blend splitting.** V1 flags intensity-correlated formula disagreement as a probable blend;
   actually splitting a batch peak into two traces can follow once the flag proves useful.
4. **μ now vs later.** μ is negligible on the Orbitrap demo but essential for TOF/long runs;
   recommend wiring the μ step from the start (it is free on the arrival path) even if its effect
   is small today.

---

## 11. Related documents

- [`peak_assignment_paradigm.md`](peak_assignment_paradigm.md) — the paradigm; this is its
  Phase 4 (batch level).
- [`peak_assignment_frontend.md`](peak_assignment_frontend.md) — the sample-view UI; item **E**
  (batch overview coloring) is realized here.
- [`assignment_confidence.md`](assignment_confidence.md) — the per-sample confidence layer whose
  fit / tier / p_correct this rolls up.
- [`fit_score.md`](../../libraries/tools/docs/fit_score.md) — why a bare consensus m/z cannot be
  scored (the per-sample envelope + SNR requirement).
