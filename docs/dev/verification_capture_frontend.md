# Labelling (assignment verification) UI — handover

*Backend V1 of the verification → calibration loop is shipped on the epic branch: a user confirms or
rejects an identification, and the verdict is stored as a labelled record (with a score snapshot)
that will later refit the confidence calibration. This is the handover for the **capture UI**.
Design rationale + guardrails: [`verification_calibration_loop.md`](verification_calibration_loop.md).*

> **Status: capture UI shipped.** All three surfaces below are implemented — the inspector control +
> verdict badge (`PanePeakAssign.vue` / `BaseVerdictBadge.vue`), the ledger badge column + filter
> (`PaneBrowserAssignment.vue`), and the `peakAssignment/verification` store (current verdict by
> stable identity, resolved to the isotopologue family's M0 — §4). Shared vocab in
> `src/lib/verification.js`. Verified against the live API (422 confirm-without-evidence guard, 201
> capture, snapshot record). Acceptance criteria (§7) all met.

**Scope is deliberately small: capture honest labels.** Build the three surfaces in §2. Do **not**
build a structured isotope checklist, an active-learning queue, or any "recalibrate" action — those
are later phases. There is no new science on the UI side; it's a form + a badge + a filter.

---

## 1. Backend contract (already live)

Two endpoints under `/api/peak-assignments` (routes on the epic branch):

| Method | Path | Auth | Body → returns |
|---|---|---|---|
| `POST` | `/sample/{sample_item_id}/verify` | **editor** | body below → `201` `{ status, message, results, data: [record] }` |
| `GET` | `/sample/{sample_item_id}/verifications` | guest | `{ status, message, results, data: [record] }` — all verdicts, **newest first** |

**POST body**

```json
{ "peak_assignment_id": "…", "verdict": "confirmed",
  "evidence_level": "reference_standard", "note": "optional free text" }
```

- `verdict`: `"confirmed" | "rejected" | "unsure"`.
- `evidence_level`: one of the five below. **Required when `verdict === "confirmed"`** — the API
  returns 422 otherwise (so disable/guard the Confirm submit until an evidence level is picked).
- `note`: optional.

**Record shape** (`AssignmentVerificationRecord`)

```
assignment_verification_id · sample_item_id · peak_assignment_id · peak_assignment_run_id
sample_peak_id · assigned_formula · ionization_mechanism_id
verdict · evidence_level · note
fit_score · evidence · p_correct     (snapshot taken at verification time — for display/audit)
verified_by · verified_utc
```

### Evidence levels (surface the meaning, not just the key)

Strongest → weakest:

| key | label | meaning |
|---|---|---|
| `reference_standard` | Reference standard | authentic standard — RT + mass match (strongest) |
| `msms` | MS/MS | MS/MS or diagnostic fragments |
| `orthogonal` | Orthogonal | RT or other orthogonal evidence |
| `pattern` | Isotope/adduct pattern | in-spectrum isotope + adduct corroboration only |
| `visual` | Visual | manual review, no independent evidence (weakest) |

---

## 2. What to build (three surfaces)

### 2a. Capture control — inspector
[`PanePeakAssign.vue`](../../server/frontend/src/lib/panes/PanePeakAssign/PanePeakAssign.vue), on the
committed-assignment card (only for a real assignment — hide for `unassigned` / empty peaks). With an
isotopologue focused the control judges the family's M0 instead, and says so (§4).

- Three buttons: **Confirm · Reject · Unsure**. Confirm and Reject get **equal visual weight**
  (Reject is a first-class negative label, not a destructive-styled afterthought).
- An **evidence-level** dropdown (the 5 options above, with labels). Required to enable Confirm.
- An optional **note** field.
- Submit → `POST …/verify`. On success, collapse to the badge (2b) showing the new verdict.

### 2b. Verdict badge
On the same card and on each ledger row: once an assignment has a current verdict, show a compact
badge — e.g. ✓ **Confirmed · reference standard**, ✕ **Rejected**, ? **Unsure** — with the evidence
level and (on hover) who/when. Clicking it re-opens the control to change the verdict (a new record;
see §4).

### 2c. Ledger filter
[`PaneBrowserAssignment.vue`](../../server/frontend/src/lib/panes/PaneBrowserMatch/PaneBrowserAssignment.vue):
add a **verified / rejected / unverified** filter (a chip strip like the existing tier histogram, or
a small select). "Unverified" = no current verdict. This makes a labelling pass efficient.

### Wireframe (inspector card)

```
┌ Assignment ─────────────────────────────────────────────┐
│ C10H14O9   [assigned]     fit 0.83 · plaus 1.0 · P 0.98p │
│ supported by 2 adducts                                   │
│                                                          │
│  Verify:  [ ✓ Confirm ]  [ ✕ Reject ]  [ ? Unsure ]      │
│  Evidence: ( Reference standard        ▾ )   ← req. to   │
│  Note:     [___________________________]        confirm  │
│                                                          │
│  — after submit —                                        │
│  ✓ Confirmed · reference standard   (you, 2m ago)  [edit]│
└──────────────────────────────────────────────────────────┘
```

---

## 3. Store + data flow

Add a `peakAssignment/verification` Pinia module alongside `run` / `peak`:

- **load**: `GET /peak-assignments/sample/{sample_item_id}/verifications` for the focused sample
  (`use: 'read'`), keyed by `assignment_verification_id`. Refetch on sample switch.
- **createVerification(body)**: `POST …/verify`; on success, push the returned record into the list
  (optimistic or refetch) so the badge updates without a full reload.
- expose a **`currentByIdentity`** map + a **`forAssignment(row)`** reader (see §4).

The peak store already exposes assignments by peak; join verdicts to them via §4.

---

## 4. Deriving the *current* verdict (important)

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

### One verdict per isotopologue family

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

---

## 5. Interaction states

| state | UI |
|---|---|
| idle, no verdict | the three buttons + evidence + note |
| Confirm chosen, no evidence | Confirm submit disabled (or inline "pick an evidence level") |
| submitting | disable buttons, spinner |
| success | collapse to the badge (2b); toast optional |
| error (403 non-editor) | inline "you need editor access to verify" — hide the control for guests |
| error (network/422) | inline error, keep the form populated for retry |
| already-verified | show badge; `edit` re-opens the control prefilled with the current verdict |

Guests (no editor role) see the **badge only**, no capture control.

---

## 6. Two UX guardrails (from the design — please honour)

1. **Don't anchor the judgment on `p_correct`.** During verification keep the model's probability
   de-emphasised (or out of the immediate eye-line) so the user judges the *data* (fit, isotopes,
   co-occurring adducts), not the number — otherwise the labels just echo the model and the eventual
   calibration learns nothing. Showing fit/isotopes/adducts is good; leading with "Mascope says 98%"
   is not.
2. **Reject is not second-class.** Equal prominence to Confirm; rejections are the negative labels the
   calibration needs.

---

## 7. Acceptance criteria

- [x] Editor can confirm/reject/unsure an assignment from the inspector; the verdict persists and the
      badge reflects it after reload.
- [x] Confirm is blocked until an evidence level is chosen; the five levels are labelled.
- [x] Reject and Unsure work without an evidence level.
- [x] The badge shows the **current** verdict derived by stable identity (survives a re-assign run).
- [x] One verdict covers the isotopologue family: every member shows it, and verifying from an
      isotopologue writes a single label against the M0 (§4).
- [x] Ledger filter narrows to verified / rejected / unverified.
- [x] Guests see the badge but no capture control (a 403 hides the control behind an "editor access
      required" note, consistent with the app's existing backend-enforced editor actions).
- [x] `p_correct` is not the visual anchor of the verification moment; Reject == Confirm in prominence.

---

## TL;DR

Build a confirm/reject/unsure + evidence-dropdown + note control in the inspector, a verdict badge,
and a verified/rejected ledger filter. `POST /sample/{id}/verify` (editor; confirm needs evidence),
`GET /sample/{id}/verifications`; current verdict = latest by `verified_utc` per
`sample_peak_id`+formula+adduct, taken on the **family's M0** so one verdict covers an M0 and all its
isotopologues (§4). Don't lead with `p_correct`; make Reject equal to Confirm. Nothing else — no
checklist, no recalibration.
