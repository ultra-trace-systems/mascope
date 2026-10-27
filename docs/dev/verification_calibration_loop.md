# Interactive verification → the calibration golden set

- **Status:** building
- **As of:** 2026-10-22
- **Tracked in:** [Assignment quality plan](https://linear.app/ultra-trace/project/assignment-quality-plan-553f4854192c) (Linear; MAS-63 covers V2 activation and the corroboration refit; the V3 active-learning queue is MAS-329, with the three section 7 decisions recorded inside it)
- **Supersedes:** `verification_capture_frontend.md` (retired 2026-10-22; section 4.5)
- **What:** Human verdicts on assignments feeding the confidence calibration.

*A design for a human-in-the-loop layer where users confirm or reject peak-centric
identifications in the UI, and those verdicts become the labelled golden set that fits each
instrument's confidence calibration. This closes the loop opened by the P2 calibration pipeline
([`assignment_confidence.md`](assignment_confidence.md)) and the D6 calibration store
(the same note, P2).*

**Status.** V1 (capture) is **shipped**, backend and UI: the `assignment_verification` table (migration
`a1f8c25d9e47`), the `POST …/verify` + `GET …/verifications` API, and the capture UI (§4.5).
V2 (close the loop) is **built** — `mascope_tools.recalibrate` +
`calibration_store.save_calibration` + `recalibrate_instrument` + `POST /calibration/{instrument}/recalibrate`
(superuser), verified on synthetic labels and live on the demo; it just waits on real V1-UI labels
to switch on real labels. V3 (quality) is still design (§6).

## 1. Why

The confidence layer produces a calibrated probability `p_correct`, but its curve is only as good as
the labels it was fit on. Today one **provisional** Orbitrap curve ships, fit on a preliminary
demo set. The principled, sustainable source of labels is the users themselves: as an analyst
verifies identifications in the normal course of work, each verdict is a `(score, correct?)` label
for *their* instrument. Accumulated, those labels refit the calibration so `p_correct` means what it
says — per instrument, per reagent chemistry, and improving over time.

This is exactly the source `fit_calibration` already anticipates: positives are **confident
identifications** — most strongly, a compound confirmed against a **reference standard**
(Schymanski Level 1, [Schymanski et al. 2014][sch14]); negatives are confirmed-wrong assignments and
near-mass decoys.

## 2. What already exists (build on, don't reinvent)

- **A verification UX.** `MatchRating` (`db/models.py`: per `sample_item` + `target_ion`, a
  `rating` 0–2 plus `checklist` / `environment` JSON) and
  [`DialogMatchRating.vue`](../../server/frontend/src/lib/dialogs/DialogMatchRating.vue) already walk
  a user through a checklist ("is there a clear peak for each target isotope? do the timeseries
  agree?") and store a verdict. It targets the **legacy target-Match** workflow, not the new
  peak-centric assignments.
- **The calibration pipeline that consumes these labels.**
  [`calibration.py`](../../libraries/tools/src/mascope_tools/composition/calibration.py):
  `fit_calibration(scores, is_correct)` → a `Calibration` with held-out ECE; it refuses to fit below
  `MIN_CALIBRATION_LABELS` = 30 labels in total *and* `MIN_CALIBRATION_CLASS_LABELS` = 10 of each class.
- **The D6 store + loader.** `assignment_calibration` table (migration `a1f8c25d9e47`) and
  `calibration_store.load_calibration` (active row → in-code fallback). Writing a new active row
  *is* "recalibrate this instrument".
- **The score-eval decoy machinery** (`tooling/score_eval/`) for synthetic negatives.

So this feature is mostly **connecting existing parts into a loop**, plus one new table and one UI
control.

## 3. The loop

```
   assignment (p_correct, evidence)
        │
        ▼
   user verifies in the inspector  ──►  assignment_verification row
   (confirm / reject / unsure,           (verdict, evidence level,
    checklist, evidence level)            SCORE SNAPSHOT, verified_by, utc)
        ▲                                        │
        │                          aggregate per instrument (+ decoys for negatives)
        │                                        │
        │                                        ▼
   load_calibration  ◄── new active row ──  fit_calibration → Calibration (a,b,ece)
   (D6 store)              (assignment_calibration)
```

## 4. Design

### 4.1 Label semantics

A verification attaches to a specific assignment — the `(sample_peak, assigned formula, adduct)` —
and records:

- **verdict**: `confirmed` | `rejected` | `unsure`. `confirmed`/`rejected` are the calibration
  positives/negatives; `unsure` is captured but excluded from fitting.
- **evidence level** — *why* the user is confident, not just that they are. Roughly the Schymanski
  ladder: authentic reference standard (L1) › MS/MS or diagnostic fragments (L2) › RT + isotope +
  adduct corroboration (L2–L3) › visual review only (weak). This is load-bearing (see §5).
- **score snapshot** — the `fit_score`, `evidence`, and `p_correct` **at the moment of
  verification**. Assignments get recomputed on re-runs; the calibration pair must be pinned to the
  score the user actually judged, or the `(score, label)` mapping drifts.
- **checklist** JSON (reuse the Match dialog's structure), `verified_by`, `verified_utc`, and the
  `peak_assignment_run_id` for provenance.

### 4.2 Data model — `assignment_verification` (shipped)

The table (migration `a1f8c25d9e47`; mirrors `MatchRating` but keyed to the peak-centric result so
it also covers untargeted/Stage-B formulas that have no `target_ion`):

```
assignment_verification_id (pk, str32)
sample_item_id            (fk sample_item CASCADE, index)
peak_assignment_id        (fk peak_assignment SET NULL, index) — provenance link; SET NULL so the
                            label outlives a re-run that deletes the row
peak_assignment_run_id    — provenance of the snapshot
sample_peak_id            (index) — STABLE identity (an observed-peak id, survives re-runs)
assigned_formula, ionization_mechanism_id  — the judged formula + adduct (also stable)
verdict                   — confirmed | rejected | unsure           (check constraint)
evidence_level            — reference_standard | msms | orthogonal | pattern | visual (check, nullable)
fit_score, evidence, p_correct  — snapshot at verification time
note (text), context (JSON, reserved)
verified_by (fk user SET NULL, index), verified_utc
```

Append-only (keep every verdict for audit), with the current one **marked rather than derived**:
`superseded_utc` is null on exactly one row per stable identity (`sample_item_id` + `sample_peak_id`
+ `assigned_formula` + `ionization_mechanism_id`), and recording a verdict stamps the row it replaces
in the same transaction. A partial unique index (`WHERE superseded_utc IS NULL`, `NULLS NOT
DISTINCT`, since both identity halves are nullable) enforces that invariant in the database, so a
consumer filters on the marker instead of re-deriving the answer and no reader can silently count a
retracted verdict.

**API** (`/api/peak-assignments`): `POST /sample/{id}/verify` (editor; body
`{peak_assignment_id, verdict, evidence_level?, note?}`; `confirmed` requires an `evidence_level` —
enforced in the schema) and `GET /sample/{id}/verifications` (newest first).

### 4.3 UI — minimal capture (V1)

Settled on a **minimal** control (no full checklist in V1): confirm / reject / unsure + an
evidence-level dropdown + optional note, in the peak-assignment inspector; a verdict badge on the
assignment; and a verified/rejected ledger filter. Shipped as §4.5 describes. The structured checklist
and the active-learning queue (§5) are V3.

### 4.4 Feeding the calibration

Per instrument class, aggregate `confirmed` (positive) + `rejected` (negative) verifications, top up
negatives with near-mass decoys if needed, and when ≥ `MIN_CALIBRATION_LABELS` of each class exist,
run `fit_calibration` and write a new active `assignment_calibration` row (flipping the previous one
inactive). Show before/after held-out ECE so the user sees the improvement. The same accumulated
labels can refit the **per-adduct corroboration weights** (the offset-decoy benchmark generalises to
"confirmed multi-adduct compounds vs rejected ones").

### 4.5 Shipped: the capture UI, and how the current verdict is derived

*Carried over on 2026-10-22 from the frontend handover `verification_capture_frontend.md`
(its status and section 4), retired that day.*

All three capture surfaces are implemented: the inspector control and verdict badge
(`PanePeakAssign.vue` / `BaseVerdictBadge.vue`), the ledger badge column and filter
(`PaneBrowserAssignment.vue`), and the `peakAssignment/verification` store (current verdict by
stable identity, resolved to the isotopologue family's M0). Shared vocabulary in
`src/lib/verification.js`. Verified against the live API (422 confirm-without-evidence guard,
201 capture, snapshot record).

The GET returns the **append-only history** (every verdict ever recorded, superseded ones included).
The current verdict for an assignment is the record whose **`superseded_utc` is null** among those
sharing its **stable identity**:

```
key = `${sample_peak_id}|${assigned_formula}|${ionization_mechanism_id}`
```

Use that identity — **not `peak_assignment_id`**, which is regenerated on every assignment run, so a
label made against last run must still light up this run's matching assignment. Group the list by
that key, keep the live record per group → `currentByIdentity`. Badge + ledger filter read from it.

Recording a verdict stamps `superseded_utc` on the one it replaces, in the same transaction, and a
partial unique index on the identity (`WHERE superseded_utc IS NULL`, `NULLS NOT DISTINCT` — both
identity halves are nullable) guarantees there is **exactly one** live record per identity. Filtering
on that field is therefore enough; the older max-by-`verified_utc` reduction gives the same answer
but is no longer what defines it.

A superseded record is history, not noise. It keeps the `fit_score` / `evidence` / `p_correct` the
user judged at the time, which stays a valid calibration pair for *that* score even once a later
verdict replaces it as the current answer — so it is stamped rather than deleted. It is excluded
from the UI and from the label pool (`recalibrate_instrument` fits on live verdicts only).

#### One verdict per isotopologue family

**A verdict covers the M0 and every `iso_child` in its family, within the sample.** Not batch-wide,
not across adducts — an M+1 peak is not a second finding to judge, it is the same compound seen
through one heavy atom, and a chemist does not confirm the carbon-12 form while leaving the
carbon-13 form open.

The engine copies the M0's `assigned_formula` and `ionization_mechanism_id` onto each isotopologue, so
**`sample_peak_id` is the only one of the three identity fields that differs across a family** — two
thirds already match, and the lookup still misses. That single divergence is what used to give a
focused M+1 an empty verify form beside a confirmed M0, and blank verdict cells on unfolded child
rows sitting under a badge that said "Confirmed".

The rule is therefore **resolve to the M0 first, then take the identity** — on read and on write
alike, and inside the store rather than at each call site:

| | where | what it does |
|---|---|---|
| resolution | `familyM0(row, byId)` / `m0Of(row)` in [`peakAssignment/assignment.js`](../../server/frontend/src/stores/data/modules/peakAssignment/assignment.js) | a row that is not an `iso_child` is its own anchor; one that is resolves through `owner_peak_assignment_id` |
| read | `forAssignment(row)` in [`peakAssignment/verification.js`](../../server/frontend/src/stores/data/modules/peakAssignment/verification.js) | looks up `identityOf(m0Of(row))`, so every family member reports the family's verdict |
| write | `verify(body)`, same file | rewrites `body.peak_assignment_id` to the family's M0 before POSTing |

Consequences worth knowing:

- **`AssignmentVerification` is unchanged** — no migration. `sample_peak_id` stays NOT NULL and now
  always holds an *M0's* peak id. A formula-scoped key would have needed nullable identity halves and
  would have collided with the per-peak score snapshot and the `(sample_item_id, sample_peak_id)`
  index; this needs none of that.
- **Resolution is strict, not a preference order.** An isotopologue reads its M0's verdict even when an
  older build left a record against the isotopologue itself. Those historical records stay in the
  append-only history for audit and are simply not surfaced — preferring an isotopologue's own record is
  how a family would come to disagree with itself row by row.
- **An isotopologue with no owner resolves to itself**, and that is an ordinary case rather than a
  corrupt one: the engine leaves `owner_peak_assignment_id` null when the ion's M0 peak was won by a
  different ion in that run
  ([`engine.py`](../../server/backend/src/mascope_backend/api/new/peak_assignments/engine.py), see
  `test_child_owner_stays_none_when_ions_m0_lost_its_peak`). Such a row keeps a badge and stays
  verifiable rather than being treated as no row at all — but its verdict genuinely covers only
  itself, so the inspector's "one verdict per compound" note is shown **only when the resolution
  actually redirected**, never on a row standing for itself. It never reaches the ledger at all:
  `childrenByOwner` skips null owners, so it is neither a parent row nor attached under one.
- **The recalibration flow is untouched.** Labels still snapshot the M0's scores, so
  `recalibrate_instrument` gets one label per family per judgment instead of N correlated ones.
- **The ledger filter was already family-scoped** and stays that way: it runs over parents only
  (isotopologues are re-attached under whichever parent survived), so a family has always been one unit
  there. What changed is that the badge column now agrees with it.

## 5. The central risk: the confirmation-bias loop

**This is the design's whole point of failure.** If users confirm/reject based on the `p_correct`
Mascope shows them, the labels just echo the model; calibrating on them makes the curve *look*
perfect while learning nothing, and amplifies existing bias. Guardrails:

1. **Prefer evidence independent of the score.** Weight `reference_standard` (L1) labels far above
   `visual`-only ones — a standard is truth external to the fit. Consider fitting on L1/L2 only, and
   treating `visual` labels as low-weight or exploratory. The `evidence_level` field exists for this.
2. **Rejections are first-class.** Calibration needs confirmed-wrong, not only confirmations. Make
   "reject" as easy as "confirm", and combine user rejections with synthetic decoys for negatives.
3. **Label across the score range, not just the top.** The curve is under-constrained near the
   decision boundary; an **active-learning queue** should surface *mid-confidence* assignments to
   verify, not rubber-stamps of the obvious. Random spot-checks guard against blind spots.
4. **Audit + provisional gating.** Append-only, with who/when/evidence, so a fit can filter to
   trustworthy labels - and it fits on *current* verdicts only, so a user who changes their mind
   replaces their label rather than contributing one to each class. Keep `provisional=True` until enough L1-grade labels exist; only then does the
   curve claim to be real.
5. **Don't show `p_correct` as the anchor during verification** (or de-emphasise it) so the judgment
   is about the *data*, not the number.

If these hold, the loop is a genuine ground-truth flywheel; if they don't, it's a self-fulfilling
prophecy. Everything else here is secondary to getting this right.

## 6. Phased plan

- **V1 — capture. Done.** `assignment_verification` table (migration
  `a1f8c25d9e47`) + `POST …/verify` (editor, `confirmed` requires an evidence level) / `GET …/verifications`,
  with the score snapshot and the Schymanski-aligned evidence levels. The **minimal** capture UI —
  confirm/reject/unsure + evidence dropdown + note in the inspector, a verdict badge, and a
  verified/rejected ledger filter — shipped as §4.5 records (guardrails 1, 2, 4, 5 baked
  in server-side + UX). No auto-calibration yet — just accumulate honest labels.
- **V2 — close the loop. Built.** `mascope_tools.recalibrate` turns confirmed/rejected verdicts
  (evidence snapshot = score) into a new Platt curve, carries the corroboration weights forward, and
  reports before/after ECE; `calibration_store.save_calibration` writes it as the new active D6 row
  (old rows kept as history); `recalibrate_instrument` gathers an instrument's labels and fits;
  `POST /calibration/{instrument}/recalibrate` (superuser) drives it. The **provisional gate** is the
  guardrail: the curve stays provisional unless ≥ `MIN_CALIBRATION_LABELS` positives carry strong
  (reference-standard/MS-MS) evidence — visual-only confirmations can't graduate it. Verified on
  synthetic labels + live on the demo. *Real value waits on V1-UI labels accumulating.*
- **V3 — quality & scale.** Active-learning queue (guardrail 3), evidence-level weighting in the fit,
  refit of the corroboration weights, Schymanski-level surfacing on the assignment, and (optionally)
  cross-linking confirmations to `reference_compound` standards.

## 7. Open questions

- Verdict granularity. *Answered:* one verdict per isotopologue family, within the sample - the
  ion, not the compound, and not across adducts (§4.5). Aggregating to the compound for
  corroboration stays open.
- Shared vs per-user calibration: whose labels fit the curve for a shared instrument? (Propose:
  workspace/instrument-scoped, with per-user attribution retained.)
- How to weight `visual` labels in the fit without letting them dominate (down-weight vs exclude).
- Re-verification when a re-run changes an assignment: keep the old snapshot label, or invalidate?
  (Partly settled: a user *retracting* their own verdict supersedes it, and only the current one is
  fit on. Whether a re-run that changes the assignment should also invalidate the label it was
  snapshotted against is still open - that one is about the score drifting, not about the judgment
  being withdrawn.)

## References

- Schymanski, E. L. et al. *Identifying small molecules via high-resolution mass spectrometry:
  communicating confidence.* Environ. Sci. Technol. 2014, 48 (4), 2097–2098. [link][sch14]
- Platt, J. *Probabilistic outputs for support vector machines.* 1999 (Platt scaling).

[sch14]: https://pubs.acs.org/doi/10.1021/es5002105
