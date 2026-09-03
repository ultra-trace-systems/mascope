# Batch-Primary Peak Assignment: the Batch Ledger as the Durable Object — Design Note

*Ingest-time assignment writes a per-sample ledger for every processed sample, and the
batch ledger ([`peak_assignment_batch.md`](peak_assignment_batch.md)) is derived from it
afterwards. This note proposes inverting that: the batch ledger becomes the object that is
written and kept, samples fold into it at ingest without a per-sample ledger row, and the
per-sample run becomes an opt-in deep dive. It is motivated by disk, argued from
measurements, and justified by the product shape it produces.*

> **Status: proposal (2026-09-03).** A design for sign-off; no code until agreed. Tier 1 of
> the disk-footprint work has shipped
> ([#2029](https://github.com/ultra-trace-systems/mascope/pull/2029), migration
> `97c42c48e011`); this note is what the assessment behind that PR called tier 3, taken to
> its conclusion. Decisions marked **[settled]** are already on record in earlier design
> docs; **[open]** items carry a recommendation. Numbers are measured unless marked
> *estimate*.

---

## 1. The problem: ingest-time growth

With peak assignment on by default, every processed sample is assigned at ingest
([`auto_assign_sample_peaks`](../../server/backend/src/mascope_backend/api/new/peak_assignments/service.py),
`service.py:1666`): a Stage-A run writes one `peak_assignment` row per detected peak, and
the fold writes one `batch_peak_occurrence` per peak. Both are permanent — a sample's ingest
run is its only run until someone re-assigns it, so the nightly prune, which trims re-runs,
never reclaims it.

Measured on two ledgers on the shared dev Postgres (after `VACUUM FULL`, so these are the
true row shapes; live tables sit 10-20 % above them):

| table | ledger | bytes per row, compacted / live |
| --- | --- | --- |
| `peak_assignment` | Stage A+B, 161 samples, 213k rows, 37 % assigned | 545 / 630 |
| `peak_assignment` | Stage-A-style, 32 samples, 29k rows, 19 % assigned | 492 / 624 |
| `batch_peak_occurrence` | the same 32-sample batch, 29k rows | 398 / 601 (289 after #2029's natural key) |
| `batch_peak` | 1,699 anchors | 408 |

So **about 1 KB per detected peak per sample, live**, before #2029, and about 0.84 KB after
it. A typical sample holds 1,300-3,000 peaks (fleet medians), instrument means run
1,900-7,700, and the tail is heavy: one instrument averages ~42k peaks per sample and the
largest single sample found holds ~500k. That is 2-8 MB of database per typical sample —
about as much as, or more than, the raw data the sample brings.

Where the bytes go:

- **60-81 % of ledger rows are `unassigned` placeholders** (168 B tuple + ~195 B of index
  each), and each is folded into a second placeholder, the occurrence. Roughly three
  quarters of an ingest-time ledger records that a peak exists, which the peak file already
  records.
- **`alternatives` + `provenance` JSON are 42 % of the ledger heap** — mostly key names,
  run-constant calibration metadata repeated on every row, and every isotopologue row
  carrying its owner's alternatives verbatim.
- **Indexes were 36-50 % of both tables** (part of what #2029 trimmed).

The fleet acquires roughly 80k samples a month. At today's row shape that is
**210-350 GB of database growth a month fleet-wide**. Three high-throughput deployments
(15-30k samples a month each) would add 30-75 GB a month against 0.6-1.2 TB of free
database volume, and on one of them the database shares the operating-system volume while
the large volume holds only the filestore. #2029's `peak_assignment_on_ingest = false` is
the emergency brake for those; it keeps the feature but gives up the promise that every
sample is assigned as it arrives.

### What #2029 did and did not fix

#2029 is hygiene: redundant and mostly-NULL indexes trimmed, the occurrence keyed by
(batch peak, sample), the consensus written only when it changed, the deferred backfill
recompute scoped to the anchors the folds touched (follow-up `c105da2b2`), and the two
ingest guards. **-16 % per peak.** Tier 2 of the same assessment (a JSON diet, -10 % more)
is worth doing but changes nothing structural. Tier 3 — not materializing placeholder rows —
was called the only order-of-magnitude lever. This note is tier 3 examined properly, and
it turns out that "drop the placeholders" is the wrong way to take it (§2).

---

## 2. What the data says about the batch ledger

The batch ledger's shape decides what a batch-primary design can save. Measured 2026-09-03
on the 32-sample Stage-A-style batch above:

| | measured |
| --- | --- |
| anchors (`batch_peak`) | 1,699 |
| occurrences (`batch_peak_occurrence`) | 28,810 |
| anchors as a share of occurrences | 5.9 % |
| singleton anchors (`n_present = 1`) | 159 — 9 % of anchors, 0.6 % of occurrences |
| anchors present in 20-32 samples | 818, covering 83 % of occurrences |
| anchors with consensus tier `unassigned` | 1,232 — 73 % |
| occurrences with tier `unassigned` | 23,424 — 81 % |
| occurrence tuple, average | 190 B (formula column ~8 B, peak id 20 chars) |

Membership distribution:

| samples present | anchors | occurrences covered |
| --- | --- | --- |
| 1 | 159 | 159 |
| 2 | 226 | 452 |
| 3-4 | 162 | 534 |
| 5-9 | 151 | 1,047 |
| 10-19 | 183 | 2,618 |
| 20-32 | 818 | 24,000 |

Earlier evidence in the batch design doc agrees: on the demo golden table, 80 samples'
14,710 matched peaks fold into 268-349 anchors, and ~90 % of a batch's final anchors already
exist after 10 samples.

Three conclusions:

1. **The unique set is small.** The anchor set is about six percent of the member rows on
   this batch and grows sub-linearly with samples. Anything stored once per anchor instead
   of once per member is a 17x reduction here, and the same factor applies to compute done
   once per anchor.
2. **Membership is dense, not a noise tail.** Half the anchors recur in 20 or more of the
   32 samples and carry 83 % of the occurrences; singletons are 9 % of anchors. Membership
   is the batch overview's data — it is what a trace is drawn from — and it cannot be
   dropped. It has to be made cheap.
3. **Most recurring species are unassigned.** 73 % of anchors carry no formula, and they
   recur. The settled rule that an unassigned anchor is a first-class, drawable trace
   ([`peak_assignment_batch.md`](peak_assignment_batch.md) §5.1) is right, and it rules out
   the naive tier 3: "no occurrences for unassigned peaks" measures -80 % of occurrences
   precisely because it deletes the trend data of most recurring species in the batch.

---

## 3. The core change

Today the fold reads the run:

```
ingest:  process -> match -> Stage A run (peak_assignment, one row per peak)
                          -> fold (one occurrence per peak; consensus on the anchor)
durable: per-sample ledger (assignment + evidence + placeholders)  +  batch ledger (derived)
```

Proposed — the assignment lives on the anchor, the sample contributes membership:

```
ingest:  process -> match -> Stage A in memory
                          -> fold: snap peaks to anchors, write slim member rows,
                                   annotate the anchor from real member spectra
durable: batch ledger (anchor = identity + annotation + curation; member = slim membership)
opt-in:  per-sample run (deep dive, import, copy) - unchanged tables, retained by the prune
```

Three principles:

- **The anchor owns the annotation.** Formula, ion formula, mechanism, candidate list,
  provenance, calibrated probability, verification and manual curation live on the batch
  peak. Today they are consensus copies of per-sample rows; they become the record.
- **Membership is slim and complete.** Every detected peak of every sample still folds in —
  the "assign all samples, omit nothing" principle stands — but a member row carries only
  what a trace point and an agreement vote need.
- **Per-sample runs are opt-in.** The full per-sample ledger — alternatives per peak,
  per-sample provenance, run config — stays exactly as it is for an explicit run, for an
  import through the SDK, and for a copy. It is retained by the existing prune. It is no
  longer written at ingest.

The scientific constraint is unchanged. The batch design doc's settled rule is that a fit
score needs a real per-sample isotope envelope and SNR, so a *synthetic consensus spectrum*
must never be scored. Nothing here scores one. Stage A still runs per sample at ingest (it
reuses the matcher that already ran for the sample and is cheap), so every member gets a
real per-sample fit and tier; what changes is that the result is stored on the anchor and
the member rather than as a ledger row. Stage B, the expensive stage, runs once per anchor
on a *real* member's spectrum (§5.3).

What it buys beyond disk:

- **Curate once per batch.** An override or a verdict on the anchor covers every sample
  the species was seen in. The copy feature exists to approximate exactly this today.
- **Identity survives peak recomputes.** Anchors are m/z identities. Per-sample rows go
  inert when a sample's peak ids change
  ([#2025](https://github.com/ultra-trace-systems/mascope/issues/2025)).
- **Untargeted assignment becomes affordable.** Stage B is off for batches today because
  its cost multiplies by the sample count; per anchor it is bounded by the unassigned
  anchor count (§5.3).
- **It is the batch design doc's own logic, one step further.** Identity is m/z and formula
  is annotation there already; making the anchor the owner of the annotation is the
  consequence.

---

## 4. Data model

### 4.1 `batch_peak` gains the annotation

| column | today | proposed |
| --- | --- | --- |
| `consensus_formula`, `consensus_ion_formula`, `ionization_mechanism_id`, `consensus_tier`, `best_fit_score`, `support_fraction`, `n_present`, `is_ambiguous`, `max_intensity`, `isotopologue_of` | consensus of per-sample rows | unchanged in meaning; the vote runs over member rows (§4.2) |
| `alternatives` | runner-up consensus formulas | the anchor's **candidate list**, index 0 = the winner: `[{formula, ion_formula, ionization_mechanism_id, plausibility, fit_score, source, target_compound_id?, target_ion_id?}]`. Member rows reference candidates by index |
| `provenance` | vote statistics | plus what a per-sample row carried: `score_version`, `calibration`, `evidence`, `n_candidates`, `is_tie`, `p_correct`, `reference_identities` — once per anchor, not once per member |
| `source` (new) | — | `database` / `untargeted` / `manual` for the winner, as on a ledger row |
| `representative_sample_item_id`, `representative_sample_peak_id` (new) | — | the member whose real spectrum scored the winner (§5.2); what the inspector opens |
| per-batch fold record (new, see open decision 2) | — | the equivalent of `PeakAssignmentRun.config`: engine version, tier bands, search ranges, reference-licence gate — one per batch and ionization mode, not one per sample |

Curation and verification move here too (§6.3).

### 4.2 `batch_peak_occurrence` becomes slim

> *Status:* the new member fields (`candidate`, `role`, `owner_batch_peak_id`, `p_correct`) and
> the anchor's `candidates` registry shipped as the first implementation step (migration
> `c4d2e8a1b7f3`), additively: the existing columns and their types are unchanged until phase 4.

| column | today | proposed |
| --- | --- | --- |
| `batch_peak_id`, `sample_item_id` | primary key (since #2029) | unchanged |
| `sample_peak_id` | `String(20)` | unchanged — the way back to the peak file |
| `peak_assignment_id` | FK to the ledger row, indexed | NULL for a fold-at-ingest member and set only by an explicit or imported run (§6.4); keep the column, index it partially (`WHERE ... IS NOT NULL`) |
| `sample_peak_mz` | float8 | `mz_delta_ppm` float4 — the member's offset from the anchor after the per-sample correction, which is what jitter/QC uses; the absolute m/z is `anchor.mz * (1 + delta/1e6)` |
| `intensity` | float8 | float4 — the chart y-value, drawn in single precision anyway |
| `tier` | `String(24)` | small integer code (the four tiers plus `unassigned`) |
| `fit_score` | float8 | float4 |
| `assigned_formula` | `String(256)` | `candidate` small integer — index into the anchor's candidate list; 0 = agrees with the winner, NULL = unassigned in this sample |
| `role`, `owner_batch_peak_id` (new) | on the ledger row | the family link as this member observed it, so `isotopologue_of` no longer needs the ledger |
| `p_correct` | in ledger provenance | not per member — the anchor's calibrated probability is the record (§5.2) |

*Estimate:* ~0.2 KB per member live (a ~100 B tuple plus the two indexes), against 601 B
live today (398 compacted; 289 after #2029). What the consensus recompute reads from the
ledger today
([`_recompute_consensus`](../../server/backend/src/mascope_backend/api/new/peak_assignments/batch_peaks_controller.py),
`batch_peaks_controller.py:467-471`: ion formula, mechanism, provenance, role, owner) is
exactly the set that moves onto the anchor and the member, so the join disappears rather
than being re-pointed.

Two things are deliberately kept per member: the `sample_peak_id`, without which the Sample
view cannot colour a spectrum and the counterpart read cannot follow a peak across samples;
and the intensity, without which the series read would have to open every sample's peak
file.

### 4.3 What is *not* changed

`peak_assignment_run` and `peak_assignment` keep their shape. An explicit run, an import
and a copy write them as today, the read routes serve them as today, and the prune bounds
them as today. The one thing that stops is ingest writing them.

---

## 5. Engine

### 5.1 Ingest folds without a run

`auto_assign_sample_peaks` runs Stage A exactly as now — peaks from the file, the
known-isotope set, `compute_match_isotopes`, `apply_match_params`, `score_ions_by_fit`,
`invert_matches_to_peak_assignments` — but hands the in-memory result to the fold instead of
inserting it. The fold snaps every detected peak of the sample, assigned or not, to the
batch's anchors as today
([`fold_in_sample`](../../server/backend/src/mascope_backend/api/new/peak_assignments/batch_peaks.py)),
writes one slim member per peak, and updates the annotation of the touched anchors. No run
row, no ledger rows, no placeholders.

The per-batch advisory lock, the μ correction, the resolution-adaptive tolerance and the
idempotent re-fold stay as they are. The two #2029 guards keep their meaning:
`peak_assignment_on_ingest = false` skips the fold, and the peak ceiling skips a
pathological sample.

### 5.2 The anchor's assignment is scored on real spectra

An anchor's winner is decided as today, by the evidence-weighted vote over its members'
Stage-A results, so a formula that only one low-SNR sample supports does not become the
batch's answer. What is new is that the anchor records *which member's real spectrum* the
evidence comes from (`representative_*`): the brightest member with a complete envelope,
re-picked when a brighter one arrives. The inspector opens that spectrum; the calibrated
probability is the representative's, folded with the capped multi-sample lift the batch
design doc already specifies (§5.4 there).

Blend and degeneracy detection is unchanged: the ~10 % of anchors whose members disagree
along the intensity or time axis are flagged `is_ambiguous`, and the runner-up stays in the
candidate list. Because every member still carries its own Stage-A fit and candidate index,
the disagreement is visible per member exactly as it is now.

### 5.3 Stage B runs once per anchor

Untargeted assignment is where the compute cost is: it is off for batches today because it
multiplies by the sample count. Per anchor it is bounded by the number of unassigned
anchors — 1,232 on the 32-sample batch against ~23,400 unassigned member peaks, about 19x
less work, and the factor grows with the batch.

Implementation shape: group the unassigned anchors by their representative sample, and for
each such sample run `assign_compositions` on that sample's own peaks — the real spectrum,
with its real neighbours for the isotope envelope — restricted to the anchors it represents.
The results annotate the anchors; each member's candidate index is then refreshed by
checking its peak against the winner's predicted isotopologues, which is the seeded
re-score chain the copy service already runs (one `compute_match_isotopes` pass per sample,
[`copy_service.py`](../../server/backend/src/mascope_backend/api/new/peak_assignments/copy_service.py)).
Stage B stays user-triggered per batch **[settled]**; running it at ingest for *new* anchors
only becomes affordable and is an **[open]** follow-up (decision 5).

### 5.4 Per-sample evidence on demand

A sample's full evidence — mass error, abundance error, per-candidate fits — is computed
rather than stored for a fold-only sample: the seeded re-score chain scores the anchor's
candidate list against that sample's peak file in one pass, and the result is served to the
inspector without being persisted. A user who wants a whole ledger persisted for one sample
runs an explicit assignment, which is unchanged.

---

## 6. Read paths, curation, verification, copy, import

### 6.1 The Sample view

> *Status:* shipped as the second implementation step (`fold_view.py`). The runs listing carries
> one derived run per folded sample (engine `batch`, listed last); the ledger and detail reads
> answer for it from members plus anchors; a verdict can be recorded against a derived row,
> curation is refused with a coded 409, and the inspector withholds the controls.

The per-sample read
([`get_peak_assignments`](../../server/backend/src/mascope_backend/api/new/peak_assignments/service.py),
`service.py:235`) serves a completed run if the sample has one, else it derives the ledger
from the sample's members joined to their anchors: m/z and intensity from the member,
formula, ion formula and mechanism from the candidate the member points at, tier and fit
from the member, P(correct) and evidence from the anchor. The response shape is unchanged,
so the assignment ledger
([`PaneBrowserAssignment.vue`](../../server/frontend/src/lib/panes/PaneBrowserMatch/PaneBrowserAssignment.vue)),
the spectrum tier colouring
([`ChartSampleSpectrum/data.js`](../../server/frontend/src/lib/charts/ChartSampleSpectrum/data.js))
and the SDK's `peak_assignments.get`
([`peak_assignments.py`](../../libraries/sdk/src/mascope_sdk/resources/peak_assignments.py))
keep working. The runs endpoint lists a synthetic run for the fold (engine `batch`, open
decision 3) so the run selector can label it. The detail read
([`get_peak_assignment_detail`](../../server/backend/src/mascope_backend/api/new/peak_assignments/service.py),
`service.py:359`) serves the anchor's candidate list and provenance plus the on-demand
per-sample evidence (§5.4).

### 6.2 The batch views

The ledger, series and counterpart reads
([`batch_peaks_records.py`](../../server/backend/src/mascope_backend/api/new/peak_assignments/batch_peaks_records.py))
already read anchors and occurrences only. They change by column types, not by shape.

### 6.3 Curation and verification move to the anchor

Manual curation today is run-local
([`curation.py`](../../server/backend/src/mascope_backend/api/new/peak_assignments/curation.py)):
it edits one row of one run, and a later run supersedes it. On the anchor it is batch-wide:
overriding the winner rewrites the head of the candidate list, archives the displaced winner
in `provenance.manual` exactly as the row-level path does, and every member that points at
index 0 now reads the curated formula. That is what the copy feature
([`peak_assignment_copy.md`](peak_assignment_copy.md)) exists to approximate today, and for
the in-batch case it stops being needed.

Verification is keyed on `(sample_item_id, sample_peak_id)` today
([`AssignmentVerification`](../../server/backend/src/mascope_backend/db/models.py)). The
continuity note ([`peak_assignment_continuity.md`](peak_assignment_continuity.md)) already
proposes an anchor-scoped verdict that overlays per-sample verdicts; under this design the
anchor verdict is the primary one, and a per-sample verdict remains possible on an explicit
run. The calibration label pool keeps counting one label per anchor, never one per member,
for the reason that note gives.

### 6.4 Import and copy

The SDK import contract ([`sdk_peak_assignment.md`](sdk_peak_assignment.md) §8.2)
publishes a per-sample run and is untouched: an imported run folds into the batch as today,
and its members link to their ledger rows through `peak_assignment_id`. Copy remaps
through the occurrence join it already prefers; with every ingest sample folded, that fast
path becomes the common case rather than the exception.

---

## 7. Cost

Per detected peak per sample, live, with fleet growth at the last year's acquisition rate.
The first three rows are measured; the rest are *estimates* from the measured row shapes
(§1, §2, §4.2).

| design | KB per peak | fleet GB/month | notes |
| --- | --- | --- | --- |
| before #2029 | 1.00 | ~350 | ledger row + occurrence |
| after #2029 | 0.84 | ~290 | shipped |
| + tier 2 JSON diet | 0.74 | ~260 | worth doing regardless; API unchanged |
| **no per-sample ledger at ingest**, today's occurrence row | ~0.35 | ~120 | §5.1 alone |
| **+ slim member row**, annotation on the anchor | ~0.20 | ~70 | §4.2; the anchor's share is ~0.05 |
| + columnar membership (arrays per anchor) | ~0.02 | ~7 | reserved, §8 |
| raw data the same samples bring, for scale | | ~80-300 | |

The slim-member design puts assignment growth below raw-data growth. That is the target: a
deployment that can store its samples can store their assignments, at the volume ratio it
has today.

---

## 8. What NOT to do

- **Don't drop occurrences for unassigned peaks.** It is tier 3 as first written, it
  measures -80 % of occurrences, and it deletes the traces of the 73 % of recurring anchors
  that carry no formula (§2). Make membership cheap instead.
- **Don't score a synthetic consensus spectrum.** Unchanged from the batch design doc; the
  anchor's evidence comes from a real member's envelope (§5.2).
- **Don't assign a representative subset of samples.** Every sample still folds in and
  every member still carries its own Stage-A result; the change is where the annotation is
  stored, not which samples are examined.
- **Don't reach for columnar membership first.** Arrays per anchor (sample index, peak
  index, intensity, fit, tier — ~17 B per member) are the true orders-of-magnitude option,
  but they give up row-level indexing for the counterpart read and the per-sample slice,
  and turn each fold into array rewrites on ~80 % of the batch's anchors. Keep it in
  reserve for a deployment whose database volume still binds after §7's slim rows.
- **Don't expect compression or `jsonb` to help.** Measured: TOAST/lz4 never engage below
  ~2 KB tuples, and `jsonb` is +13 % on rows this small.
- **Don't replace the string ids with `bigint`.** ~13 % of the ledger, but
  `peak_assignment_id` and `batch_peak_id` are strings in the API and SDK contract.

---

## 9. Phased plan

Each phase ships on its own and is useful on its own.

1. **Tier 2 JSON diet** (small, mechanical): run-constant provenance onto the run row,
   reference identities resolved at read, positional alternatives, no owner-alternatives
   copy on isotopologue rows. -10 %, and it also shrinks what §4.1 moves onto the anchor.
2. **Read model first: derive the Sample view from members + anchors.** The per-sample read
   serves a fold-only sample; the consensus recompute stops joining the ledger (the five
   fields move); the SDK read and the UI tier counts follow. Nothing changes for a sample
   that has a run. This is the prerequisite for everything after it, and it can ship dark. *The
   consensus half - members carry the five fields, the recompute no longer joins the ledger -
   shipped as migration `c4d2e8a1b7f3`.*
3. **Ingest folds without a run** (§5.1). The big cut, ~-65 %. Behind a setting, so a
   deployment can go back to writing ingest runs while the derived Sample view is being
   proven. User docs and `docs/maintaining.md` follow: "every completed run folds into the
   batch peaks" becomes "every processed sample folds into the batch peaks".
4. **Slim member row + annotation on the anchor** (§4). One migration; the fold and the
   reads switch to the candidate index; curation and verification on the anchor (§6.3).
   ~-80 % overall.
5. **Stage B per anchor** (§5.3), batch-triggered.
6. **Later, only if a volume still binds:** columnar membership.

Backfill: existing per-sample runs fold as today. Once the anchor carries the annotation,
the ingest runs of already-folded samples are redundant, and a one-time reclaim can prune
them while keeping explicit runs. Production deployments predate the batch tables
entirely, so for them this is the shape ingest-time assignment arrives in, not a migration
of existing ledgers.

---

## 10. Decisions

**Settled in earlier design docs, still standing:** Stage A folds on every arrival; Stage B
is user-triggered; anchor tolerance is resolution-adaptive; an unassigned anchor is a
first-class trace; every sample is assigned, never a subset; no synthetic spectrum is
scored.

**Superseded by this note, if agreed:** "assignment stays per-sample; the batch peak only
aggregates" ([`peak_assignment_batch.md`](peak_assignment_batch.md) §2). The per-sample
*computation* stays; the per-sample *record* becomes opt-in.

**Open, with recommendations:**

1. **Candidate list on the anchor as JSON vs a `batch_peak_candidate` table.** JSON,
   index-addressed, as `alternatives` already is; a table only if candidates need indexing
   (search by formula across batches). *Recommend JSON.*
2. **Where the fold's configuration lives.** A per-batch fold record (engine version, tier
   bands, ranges, licence gate) rather than a `peak_assignment_run` with a NULL sample.
   *Recommend a small `batch_peak_fold` row per batch and ionization mode.*
3. **How a derived per-sample ledger identifies itself** to the run selector and the SDK:
   a synthetic run object with engine `batch`, or a run-less response. *Recommend the
   synthetic run object; every reader already handles a run.*
4. **Singletons on the anchor.** Carrying the first member's ids on the anchor and
   materializing a member row only from the second sample on saves 9 % of member rows here,
   more on noisy TOF data. *Recommend later; measure on a TOF batch first.*
5. **Stage B at ingest for new anchors.** Affordable once per anchor; whether it should run
   unasked is a product call. *Recommend no until the batch-triggered path has been used.*

---

## 11. Related documents

- [`peak_assignment_batch.md`](peak_assignment_batch.md) — batch peaks, the fold and the
  consensus this note builds on.
- [`peak_assignment_paradigm.md`](peak_assignment_paradigm.md) — the per-sample ledger and
  the two-stage engine.
- [`peak_assignment_copy.md`](peak_assignment_copy.md) — the seeded re-score chain §5.3 and
  §5.4 reuse.
- [`peak_assignment_continuity.md`](peak_assignment_continuity.md) — anchor-scoped
  verdicts.
- [`sdk_peak_assignment.md`](sdk_peak_assignment.md) — the import contract that stays.
- [`maintaining.md`](../maintaining.md) — the operator view of the cost and the two ingest
  settings.
