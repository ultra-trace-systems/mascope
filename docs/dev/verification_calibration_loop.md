# Interactive verification → the calibration golden set

*A design for a human-in-the-loop layer where users confirm or reject peak-centric
identifications in the UI, and those verdicts become the labelled golden set that fits each
instrument's confidence calibration. This closes the loop opened by the P2 calibration pipeline
([`assignment_confidence.md`](assignment_confidence.md)) and the D6 calibration store
([`handoff_fit_score_and_assignment.md`](handoff_fit_score_and_assignment.md)).*

**Status.** V1 (capture) backend is **shipped**: the `assignment_verification` table (migration
`a1f8c25d9e47`), the `POST …/verify` + `GET …/verifications` API, and the frontend contract
([`verification_capture_frontend.md`](verification_capture_frontend.md), design branch). The V1 UI
is being built from that contract. V2 (close the loop) is **built** — `mascope_tools.recalibrate` +
`calibration_store.save_calibration` + `recalibrate_instrument` + `POST /calibration/{instrument}/recalibrate`
(superuser), verified on synthetic labels and live on the demo; it just waits on real V1-UI labels
to switch on. V3 (quality) is still design (below).

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
  `fit_calibration(scores, is_correct)` → a `Calibration` with held-out ECE; `MIN_CALIBRATION_LABELS`
  = 30 of each class.
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

Append-only (keep every verdict for audit); the **current** verdict for an assignment is the latest
by `verified_utc` among rows matching its stable identity (`sample_item_id` + `sample_peak_id` +
`assigned_formula` + `ionization_mechanism_id`).

**API** (`/api/peak-assignments`): `POST /sample/{id}/verify` (editor; body
`{peak_assignment_id, verdict, evidence_level?, note?}`; `confirmed` requires an `evidence_level` —
enforced in the schema) and `GET /sample/{id}/verifications` (newest first).

### 4.3 UI — minimal capture (V1)

Settled on a **minimal** control (no full checklist in V1): confirm / reject / unsure + an
evidence-level dropdown + optional note, in the peak-assignment inspector; a verdict badge on the
assignment; and a verified/rejected ledger filter. Full spec + guardrails in the handover
([`verification_capture_frontend.md`](verification_capture_frontend.md)). The structured checklist
and the active-learning queue (§5) are V3.

### 4.4 Feeding the calibration

Per instrument class, aggregate `confirmed` (positive) + `rejected` (negative) verifications, top up
negatives with near-mass decoys if needed, and when ≥ `MIN_CALIBRATION_LABELS` of each class exist,
run `fit_calibration` and write a new active `assignment_calibration` row (flipping the previous one
inactive). Show before/after held-out ECE so the user sees the improvement. The same accumulated
labels can refit the **per-adduct corroboration weights** (the offset-decoy benchmark generalises to
"confirmed multi-adduct compounds vs rejected ones").

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
   trustworthy labels. Keep `provisional=True` until enough L1-grade labels exist; only then does the
   curve claim to be real.
5. **Don't show `p_correct` as the anchor during verification** (or de-emphasise it) so the judgment
   is about the *data*, not the number.

If these hold, the loop is a genuine ground-truth flywheel; if they don't, it's a self-fulfilling
prophecy. Everything else here is secondary to getting this right.

## 6. Phased plan

- **V1 — capture. Backend DONE; UI in progress.** `assignment_verification` table (migration
  `a1f8c25d9e47`) + `POST …/verify` (editor, `confirmed` requires an evidence level) / `GET …/verifications`,
  with the score snapshot and the Schymanski-aligned evidence levels. The **minimal** capture UI —
  confirm/reject/unsure + evidence dropdown + note in the inspector, a verdict badge, and a
  verified/rejected ledger filter — is handed to the frontend in
  [`verification_capture_frontend.md`](verification_capture_frontend.md) (guardrails 1, 2, 4, 5 baked
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

- Verdict granularity: is the compound confirmed, or this specific adduct/ion? (Propose: the ion;
  aggregate to compound for corroboration.)
- Shared vs per-user calibration: whose labels fit the curve for a shared instrument? (Propose:
  workspace/instrument-scoped, with per-user attribution retained.)
- How to weight `visual` labels in the fit without letting them dominate (down-weight vs exclude).
- Re-verification when a re-run changes an assignment: keep the old snapshot label, or invalidate?

## References

- Schymanski, E. L. et al. *Identifying small molecules via high-resolution mass spectrometry:
  communicating confidence.* Environ. Sci. Technol. 2014, 48 (4), 2097–2098. [link][sch14]
- Platt, J. *Probabilistic outputs for support vector machines.* 1999 (Platt scaling).

[sch14]: https://pubs.acs.org/doi/10.1021/es5002105
