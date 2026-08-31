# Surfacing assignment confidence in the UI

*A short guide for the frontend work on the peak-centric assignment views. The backend produces four
related confidence quantities per peak; the UI now surfaces all four. This explains what each one is,
where it lives on the record, and how the probability and the adduct-corroboration signal are
surfaced honestly. Backend science lives in [`assignment_confidence.md`](assignment_confidence.md);
this is the consumer's-eye view. See also the wiring plan in
[`peak_assignment_frontend.md`](peak_assignment_frontend.md).*

## The four quantities (and where they live)

Every `PeakAssignmentRecord` (one per observed peak) carries the confidence story in these fields.
Two are top-level; the rest ride inside the `provenance` JSON blob — `evidence` is additionally
flattened onto the record, because the tier chip displays it.

| Quantity | What it means | Range | Field path | Shown today |
|---|---|---|---|---|
| **fit_score** | How well the *measurement* matches this ion — mass error + intensity + isotope envelope. Pure signal, and stored unchanged; it is just no longer what buckets the row. | 0–1 | `fit_score` (top-level) | ✅ inspector `fit` stat + alternatives, a sortable column in the composition search, and the batch ledger's `best_fit_score` sort; the tier tag shows `evidence` now |
| **plausibility** | Chemistry sanity of the formula — the Seven Golden Rules (Kind & Fiehn 2007). Independent of the measurement. | 0–1 | `provenance.plausibility` | ✅ Plausibility column |
| **evidence** | `fit x plausibility` — the score arbitration ranks candidates by, and the quantity the **tier is banded on**: `tier_for_evidence` reads assigned / candidate / below off this, not off the fit. | 0–1 | `provenance.evidence`, flattened to `evidence` | ✅ the number in the tier tag |
| **p_correct** | **Calibrated probability the assignment is correct.** `evidence` mapped through a Platt curve to a real probability. | 0–1 | `provenance.p_correct` | ✅ inspector `P(correct)` + ledger column |

The number on a tier chip is that `evidence` and not the fit, because it is the quantity the band was
read off: the label and the figure beside it can then never disagree, and the tooltip spells the
pairing out as "Evidence: NN% (fit x plausibility)". The batch-peaks ledger is the one chip that
shows no number at all — a batch peak's consensus tier is a weighted vote over its members' tiers
rather than a threshold on any single quantity, so there is no figure that produced it to show, and
borrowing the best member's fit for the slot would read as one.

So the answer to "do we have the probability?" is **yes** — it is `provenance.p_correct`, already
computed and stored. The inspector and the assignments ledger now read it (gated on
`provenance.calibrated`, null shown as "uncalibrated", provisional-flagged). Example provenance blob
from a database assignment on the demo:

```json
{
  "evidence": 0.9077,
  "p_correct": 0.8636,
  "calibrated": true,
  "calibration": { "instrument": "orbi", "provisional": true,
                   "source": "demo goldens (Br/Ur, preliminary)" },
  "plausibility": 1.0,
  "confidence": 1.0,
  "is_tie": false
}
```

## Two honest caveats `p_correct` demands

`p_correct` is trickier to display than fit/plausibility, which are always present. Handle these or
it will mislead:

1. **It is database-stage + calibrated-instrument only.** Stage A (`source: "database"`) on an
   instrument with a calibration curve gets a number; **untargeted (`source: "untargeted"`, Stage B)
   and uncalibrated instruments (e.g. TOF) get `p_correct: null`** — deliberately. We do not
   fabricate a probability we can't back. **Render null as "-" / "uncalibrated", never as "0%".**
   Check `provenance.calibrated === true` before showing a percentage.

2. **The current curve is provisional.** `provenance.calibration.provisional === true` means the
   Orbitrap curve was fit on our preliminary demo goldens, not a curated reference set. It is
   directionally right (ECE ~= 0.03) but should not be presented as a hardened figure. When
   `provisional`, label it — e.g. `~86% correct (provisional)` — so users don't over-trust it.

### Display (implemented)

- The inspector ([`PanePeakAssign.vue`](../../server/frontend/src/lib/panes/PanePeakAssign/PanePeakAssign.vue))
  shows a `P(correct)` stat that reads `provenance.p_correct`, renders null as "uncalibrated" (gated on
  `provenance.calibrated`), and appends "prov." while `provenance.calibration.provisional` is true. It
  sits in the evidence grid beside fit / plausibility / confidence.
- The assignments ledger ([`PaneBrowserAssignment.vue`](../../server/frontend/src/lib/panes/PaneBrowserMatch/PaneBrowserAssignment.vue))
  has a sortable `P(correct)` column with the same null / provisional handling.
- The store returns records raw (no provenance flattening); both read `record.provenance?.p_correct`
  directly, and the ledger flattens it to a `pCorrect` field purely so the column can sort.

## Alternatives now carry plausibility too

As of the latest engine change, each entry in `alternatives` (the runner-ups) carries its own
`plausibility`. Entries the engine *competed* for the peak — from either stage — also carry
`fit_score` + `mz_error_ppm`; what carries neither is the untargeted finder's `other_candidates`
shortlist, which is formula + plausibility only. So the inspector can rank and tooltip competitors by
a real stat, consistently across both stages. `p_correct` is **not** on alternatives — it is a
winner-only, arbitrated quantity.

The shortlist entries are **measured on request** rather than left unscored: the inspector calls
`GET .../assignment/{id}/alternative-scores`, which seeds each formula against every adduct of the
sample and reports the fit, mass error and adduct of the one whose M0 lands on the peak (see
[`peak_assignment_frontend.md`](peak_assignment_frontend.md)). Those numbers are per request and are
never stored on the row, so they do not feed the confidence layer — Stage B confidence stays deferred
for the reason below, which is about what the *run* can afford, not what the inspector can.

## Adduct corroboration — now folded into `p_correct` (shipped)

A real compound usually appears through several adducts (`[M+H]+`, `[M+NH4]+`, `[M+Br]-` ...).
Their co-occurrence is independent corroborating evidence (the basis of CAMERA / IPA) and a strong
indicator of a correct formula. It is now **measured and folded directly into `p_correct`** — you do
**not** compute anything. When a compound is assigned via several confident adducts, each winner's
`p_correct` is raised by a measured, per-adduct, bounded amount (bromide lifts a lot; generic
ammonium/protonation barely). For a winner that got a lift, provenance carries:

```json
"corroboration": { "adducts": ["+Br-", "-H+"], "n_adducts": 2, "boost": 0.46 }
```

- `adducts` — the distinct adducts the compound was seen via (notations).
- `n_adducts` — how many.
- `boost` — how much corroboration raised `p_correct` (the value already includes it).

`provenance.corroboration` is **absent** when a compound had only one adduct (no co-occurrence). It
is winner-only (M0), and only present on calibrated assignments (uncalibrated `p_correct: null`
gets no boost). **Display idea:** show `p_correct` as the single number (it already reflects
corroboration), and optionally a small "corroborated by N adducts" badge from
`provenance.corroboration` — don't add the boost yourself, it's already in `p_correct`.

Winner-only means **M0-only**: an `iso_child` row's `corroboration_adducts` is always null, because an
isotopologue is the same ion measured at another isotope rather than a second sighting of the compound.
The evidence is about the formula the whole family shares, though, so the frontend resolves an
isotopologue's badge from its family's M0 and renders it *inherited*, labelled as such on its face ("via M0"
in the inspector, a parenthesised count in the ledger). Only the count crosses over: `adducts` lives in
the M0's `provenance`, and detail is fetched for the focused assignment alone.

**The boost does not cross over at all.** `_fold_adduct_corroboration` rewrites `p_correct` for the M0
winners it collects and nothing else, so a child's `p_correct` is whatever `apply_calibration` gave
it — uncorroborated. An inherited badge therefore says the boost is in the *M0's* P(correct), not in the
row it sits beside; reusing the M0's "already folded into P(correct)" wording there would be false, and
false about the exact number rendered next to it. See
[`peak_assignment_frontend.md`](peak_assignment_frontend.md) → "Confidence".

One asymmetry to know before reading `n_adducts` as "how many adducts is this compound seen via": the
engine's tier gate filters which channels *contribute* to the co-occurrence set, not which rows
*receive* a boost. A confident winner's own channel is in the set, so its `n_adducts` counts itself
plus its corroborators; a `below_assignability` winner's is not, so its `n_adducts` counts only the
corroborators and can legitimately be 1. Both badges gate on `n_adducts > 1`, which hides that case -
keep the gate if you add a third surface. Evidence-tiering leaves the asymmetry itself untouched but
moves what feeds it: a tier is now read off `fit x plausibility` rather than off the fit alone, so a
formula whose chemistry drags its evidence under the band drops out of a co-occurrence set it used to
join, and a compound's `n_adducts` can differ from the count fit-tiering would have given it.

## TL;DR for the implementer

- The probability exists and is **wired in**: `provenance.p_correct` shows in the inspector and the
  ledger (gated on `provenance.calibrated`, null → "uncalibrated", provisional-flagged).
- Alternatives now have `plausibility` and it is shown (inline + on hover); use it for competitor
  ranking.
- Corroboration is **already baked into `p_correct`**; `provenance.corroboration` (when present) drives
  the **"Supported by N adducts" badge** (inspector pill + ledger marker, shown when `n_adducts > 1`) —
  never add its `boost` on top. Isotopologue children carry none of their own and inherit the
  family M0's count, labelled "via M0" — the count crosses over, the boost does not.
