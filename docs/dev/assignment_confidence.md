# Peak Assignment & Identification Confidence — Study, Design and Plan

*A science-based, layered architecture for going from a peak to a confident chemical
identification. This document sets the scientific framing, surveys the relevant
literature, states where we are today, defines how the work is organized, and lays out
phased next steps.*

> **Status of today's heuristics.** peaky's current assignment logic (formula
> enumeration + a partial set of chemical filters + a heuristic ranking) is a
> **proof-of-concept spike**. It works, but it is not yet the structured, evidence-based
> system described here. This document is the target architecture it should grow into.

## 0. How this work is organized

This work lands on the **`epic/peak-centric-assignment`** integration branch, where it is
the science layer of the peak-centric paradigm
([`peak_assignment_paradigm.md`](peak_assignment_paradigm.md)). Two cleanly separable
tracks sit on top of epic:

| Track | Scope | Maps to |
|---|---|---|
| **Fit score** (the measurement) | the consolidated fit score (`score_pattern_v2`), its backend wiring, SNR/satellite plumbing, and [`fit_score.md`](../../libraries/tools/docs/fit_score.md) | the **scoring engine** for peak-centric Stage A/B; the `fit_score` column |
| **Assignment confidence** (this doc + the layers) | §2: chemistry, spectral-neighbourhood, instrument/context, probabilistic integration, arbitration, level reporting | the **tier + arbitration layer** (paradigm-doc **Phase 3** "harvest peaky's arbitration/tiers/calibration") |

**Design rule that keeps them separable:** the fit score never imports from the confidence
layers, and competitor-awareness / chemistry / context never get folded back into the
score. The dependency points one way — confidence builds on fit, not the reverse.

**Legacy coexistence.** The legacy targeted match keeps its v1 behaviour by default
(`MASCOPE_MATCH_SCORE_VERSION=1`); the fit score is adopted *deliberately* as the
peak-centric engine's scoring, per the epic's "coexist, don't replace" principle — it is
not a silent flip of the legacy default.

Coexistence is enforced by a feature flag rather than left to discipline: the whole
peak-centric feature stays behind one switch, `peak_assignment` in the runtime `[meta]`
config; see [`peak_assignment_paradigm.md`](peak_assignment_paradigm.md) §5.1 for what it
gates and why). Two consequences for the confidence layers specifically:

- **`rule_senior` is opt-in** (`HeuristicFilterConfig.use_senior`). It replaced a no-op
  placeholder, so applying it to every caller would silently narrow the results of the
  pre-existing composition search. Stage B sets it; the legacy search does not. The
  *graded* `chemical_plausibility` is unaffected — it only ever fed arbitration, which is
  peak-centric-only code.
- **The on-demand search is only rescored when the feature is on.** With it off,
  `api/new/cheminfo` reports the legacy match score and category, and the fit /
  plausibility / tier fields are absent from the response.

## 1. The core premise

A mass spectrometer measures *mass and intensity*, not *identity*. The foundational
result of the field is that **accurate mass alone — even sub-ppm — cannot uniquely
determine an elemental composition**, and that isotope-pattern information is worth more
than another order of magnitude of mass accuracy
([Kind & Fiehn 2006](https://bmcbioinformatics.biomedcentral.com/articles/10.1186/1471-2105-7-234)).
It follows that identification is fundamentally a problem of **accumulating independent
evidence** and **arbitrating between candidates that all fit the mass**.

This gives us a clean separation of concerns:

```
   measurement              evidence layers                       decision
 ┌──────────────┐   ┌───────────────────────────────┐   ┌──────────────────────┐
 │  FIT SCORE   │ → │ chemistry · spectral context · │ → │  identification:     │
 │ (pure math,  │   │ instrument · orthogonal data   │   │  ranked candidate +  │
 │ per candidate│   │ (each a calibrated likelihood) │   │  confidence + level  │
 │  likelihood) │   └───────────────────────────────┘   └──────────────────────┘
 └──────────────┘
```

- The **fit score** ([`fit_score.md`](../../libraries/tools/docs/fit_score.md)) is the
  reproducible measurement: *how well does the data fit this candidate's predicted
  pattern?* It is competitor-blind and makes no probability claim.
- The **confidence layers** add everything else we know — chemistry, the spectral
  neighbourhood, the instrument, orthogonal measurements — each ideally expressed as a
  likelihood or a calibrated probability.
- The **decision layer** combines the evidence into a posterior over candidates, picks
  the assignment, and reports a **calibrated confidence and an identification level**.

The output semantics should follow the community-standard **confidence-level schemes**:
the HR-MS levels of
[Schymanski et al. 2014](https://pubs.acs.org/doi/10.1021/es5002105) (Level 1 confirmed by
reference standard → Level 5 exact mass of interest) and the broader Metabolomics
Standards Initiative reporting standards
([Sumner et al. 2007](https://doi.org/10.1007/s11306-007-0082-2)). We should report a
*level*, not just a number, because that is how the field communicates identification
confidence.

## 2. The layers

Each layer below states its **scientific basis** (with references), **where we are
today**, and **the gap**.

### L0 — Candidate generation (formula enumeration)
*Basis.* Enumerate every elemental composition whose ion m/z lands within the mass
tolerance, per ionization channel. This is the classic "money-changing problem" of mass
decomposition ([Böcker & Lipták 2007](https://doi.org/10.1007/s00453-007-0162-8); used in
SIRIUS, [Böcker et al. 2009](https://academic.oup.com/bioinformatics/article/25/2/218/218950)).
*Today.* `mascope_tools.composition.finder.find_compositions` — bounded recursive
tree-search with mass-domain pruning (see
[`composition_assignment.md`](../../libraries/tools/docs/composition_assignment.md)). **Solid.**
*Gap.* None fundamental; ensure adduct/ionization channels are complete and configurable.

### L1 — Chemical plausibility (the Seven Golden Rules)
*Basis.* Most mass-degenerate formulas are chemically impossible or implausible. The
**Seven Golden Rules** ([Kind & Fiehn 2007](https://bmcbioinformatics.biomedcentral.com/articles/10.1186/1471-2105-8-105))
codify this: (1) element-count limits, (2) LEWIS/SENIOR valence rules (integer,
non-negative ring-and-double-bond equivalents; a chemically connectable graph must exist),
(3) isotope pattern, (4) H/C ratio bounds, (5) N/O/P/S-to-C ratio bounds, (6) element-ratio
*probabilities* from large compound databases, (7) presence/co-occurrence of certain
elements.
*Today.* `heuristic_filter.py` has the boolean gates (`rule_element_ratio`, Rules 4–5;
`rule_senior`, a real RDBE + Senior valence-graph check, Rule 2) **and** a graded
`chemical_plausibility` in $[0,1]$ that composes the Senior/RDBE, element-ratio (Table 2)
and heteroatom co-occurrence (Table 3, Rule 6) factors as referenced likelihoods — the
first real evidence layer. **Landed (see §4 P1 progress).**
*Gap.* None fundamental for the chemistry layer itself. What remains is *consuming* the
plausibility in arbitration (P2): calibrating how it combines with the fit score per
instrument, and the target–decoy FDR. The isotope rule (3) stays in the fit score, not here.

### L2 — Spectral-neighbourhood corroboration (adducts, isotopes, fragments)
*Basis.* A real compound rarely appears as a single peak. Its isotopologues, its different
adducts ($[M+H]^+$, $[M+NH_4]^+$, $[M+Na]^+$, $[M+Br]^-$ …), and its in-source fragments
**co-occur**, share a retention/elution profile, and have predictable intensity
relationships. Grouping these and checking their mutual consistency is strong corroborating
evidence and resolves many ambiguities. Tools: **CAMERA**
([Kuhl et al. 2012](https://pubs.acs.org/doi/10.1021/ac202450g)) groups isotopes/adducts by
peak-shape correlation; **Integrated Probabilistic Annotation**
([Del Carratore et al. 2019](https://pubs.acs.org/doi/10.1021/acs.analchem.9b02354)) puts
isotopes, adducts, and biochemical relations into a single Bayesian model.
*Today.* Adduct co-occurrence corroboration is **measured and folded into `p_correct`** (§4, P3
progress): a compound seen via several confident adducts lifts each winner by a measured per-adduct
log-odds (bounded odds update), with the weights carried in the per-instrument calibration store
(D6). Isotope corroboration already lives in the fit score.
*Gap.* Intensity-consistency across adducts and in-source-fragment grouping are not yet there (the
fuller CAMERA/IPA model).

### L3 — Instrument & acquisition context
*Basis.* The instrument and method constrain identity. **Mass resolution** sets the mass
term's width (already in the fit score via the fitted $\sigma$). **Retention time** is a
strong orthogonal axis: predicted-vs-observed RT consistency markedly improves annotation
([Broeckling et al. 2016, *MS1 spectrum + time prediction*](https://pubs.acs.org/doi/10.1021/acs.analchem.6b02479)).
**Ionization mode and reagent chemistry** make some adducts/compounds (im)plausible.
*Today.* Resolution-aware mass is in the fit score. RT and ionization-behaviour priors are
**not** used as confidence evidence.
*Gap.* A retention-time consistency term (where RT data exists) and ionization/reagent
priors as candidate-level evidence.

### L4 — Probabilistic integration & calibration
*Basis.* Combine the independent evidence streams into a single posterior over candidates.
Two complementary tools:
- **Calibration** — map a raw score to a true probability via Platt scaling
  ([Platt 1999](https://en.wikipedia.org/wiki/Platt_scaling)) or isotonic regression. We
  already prototyped this (`calibrate_score`, the Platt curve on the demo goldens).
- **False-discovery-rate control** — estimate annotation reliability with **target–decoy**
  methods, the established approach for large-scale MS annotation
  ([Scheubert et al. 2017](https://www.nature.com/articles/s41467-017-01318-5)). Our
  near-mass *decoy* candidate generator (`tooling/score_eval/make_candidates.py`) is the
  seed of this.
*Today.* A single Platt calibration fitted on the Orbitrap demo set; a decoy generator and
an evaluation harness exist (`score_eval`).
*Gap.* **Per-instrument / per-context calibration**; a real FDR estimate; principled
combination of L1–L3 evidence (Bayesian product of likelihoods, or a learned model) rather
than the fit score alone.

### L5 — Arbitration & reporting
*Basis.* For each peak, rank the surviving candidates by combined evidence, assign the
best, and **report a confidence and a level** ([Schymanski 2014](https://pubs.acs.org/doi/10.1021/es5002105)).
Where candidates are genuinely indistinguishable, say so (report the tie, not a false
winner). This is exactly the "which of the well-fitting compositions is most likely"
problem — **peaky's purpose.**
*Today.* A heuristic ranking/selection (the spike).
*Gap.* A structured arbitration that consumes calibrated per-candidate evidence, emits a
confidence + level, and is honest about unresolved ties.

## 3. Design principles

1. **The fit score stays pure.** Competitor-awareness, chemistry, and context never get
   folded back into the measurement. They are *layers*, each separately inspectable. This
   is also what keeps the two branches/PRs cleanly separable (§0).
2. **Every layer is a likelihood or a calibrated probability**, so they compose by
   multiplication (Bayesian) and the final number is interpretable.
3. **Everything is validated against goldens.** The demo bundle + `score_eval` harness
   (ranking AUC, calibration ECE, target–decoy FDR) is the test bench for every layer.
4. **Calibration is per-instrument/context**, never a universal constant.
5. **Report levels, not just scores** — align outputs with Schymanski/MSI so confidence is
   communicated the way the field expects.
6. **Reproducibility first.** The measurement is deterministic; learned/calibrated pieces
   live in the upper layers where they can evolve without disturbing the measurement.

## 4. Phased plan

| Phase | Deliverable | Anchored in |
|---|---|---|
| **P1 — Chemistry, completed & scored** | Finish the Seven Golden Rules as a graded per-candidate plausibility (RDBE/Senior valence-graph check, DB-derived element-ratio probabilities, isotope rule), replacing the partial boolean filter. | Kind & Fiehn 2007 |
| **P2 — Candidate arbitration + FDR** | For each peak, compete candidates by fit × plausibility; calibrate to $P(\text{correct})$ per instrument; estimate FDR with the target–decoy harness. Emit a confidence. | Scheubert 2017; Platt 1999 |
| **P3 — Spectral-neighbourhood corroboration** | Adduct/isotope/in-source-fragment grouping; reward candidates corroborated by consistent companion peaks. | CAMERA 2012; IPA 2019 |
| **P4 — Context & levels** | Retention-time consistency and ionization/reagent priors as evidence; assign a Schymanski/MSI identification **level** alongside the confidence. | Broeckling 2016; Schymanski 2014 |
| **P5 — Unified probabilistic model (optional)** | Replace the hand-combined layers with one Bayesian (or learned) model over all evidence, dataset-wide. | IPA 2019; SIRIUS/ZODIAC |

**Immediate next step (P1).** Lift the chemistry filter from boolean to a graded, referenced
plausibility: implement a proper **RDBE (ring + double-bond equivalents)** and **Senior
parity / valence-graph feasibility** check (completing the stubbed `rule_senior`), plus the
DB-derived element-ratio probabilities (Rule 6). Deliverable: a per-candidate
`plausibility ∈ [0,1]` with unit tests, measured on the decoy harness (does it rank true
formulas above implausible mass-degenerate ones?). This is the smallest, fully
self-contained step that turns the spike into the first real evidence layer — and it needs
no backend or DB, only `mascope_tools` + tests.

### P1 progress

- **`rule_senior` implemented** (RDBE ≥ 0 + Senior connectivity), replacing the stub, with
  unit tests (`test_rule_senior.py`). It is conservative and **fails open**: only
  over-saturated (negative-RDBE, impossible-for-any-structure) and disconnectable formulas
  are rejected; **odd-electron radicals pass** (they can be genuine in APCI/APPI), and any
  element outside the standard valence table passes. Still **boolean** for now; the move to a
  graded `plausibility ∈ [0,1]` and the DB element-ratio probabilities (Rule 6) are the
  remaining P1 work.
- **Validated against all 92 demo target compounds** (confirmed they are genuine
  `target_compound.target_compound_formula` *neutral* formulas — ions are built `compound +
  ionization`, e.g. `C10H15O5` + `+H+` → `C10H16O5+`). After the radical fail-open, the rule
  flags exactly **1**: `C6H17NO4` (17 H on a C6NO4 skeleton; max is 15 — impossible for any
  neutral structure), almost certainly a data error.
- **Data-quality finding for later chemical review** (`analyze carefully afterwards`): four
  demo compounds are **odd-electron radicals as neutrals** — `C9H15O6`, `C10H15O5`,
  `C10H17O7` (odd H, no N) and `Br` (a lone halogen, the bromide reagent). These now *pass*
  the rule (fail-open), but it is worth confirming whether they are legitimate radical
  species or off-by-one-H / reagent-representation artefacts in the test data.
- **Graded plausibility landed (P1 complete).** `chemical_plausibility` /
  `formula_plausibility` (`heuristic_filter.py`) return a per-candidate plausibility in
  $[0,1]$ — the **product** of three referenced factors (Kind & Fiehn 2007), one per
  design-rule *likelihood layer*:
    1. **Senior/RDBE** (Rule 2): `1.0` for any feasible neutral (radicals included —
       fail-open), `0.0` only for a *provably impossible* graph (negative-RDBE /
       disconnectable).
    2. **Element ratio** (Rules 4–5): graded across the paper's **common / extended /
       extreme** X/C bands (Table 2, verbatim). `1.0` inside the common range, tapering to
       `0.5` at the extended edge, then to a non-zero `PLAUSIBILITY_FLOOR`. Scored only when
       both X and C are present (fail-open on carbon-free formulas).
    3. **Heteroatom co-occurrence** (Rule 6): graded against the multi-element count
       restrictions (Table 3, verbatim).
  It **grades, it does not gate** — the boolean `rule_element_ratio` / `rule_senior` still do
  the hard enumeration cut; this plausibility is the *weight* the arbitration layer (P2) will
  multiply against the fit score. Fully deterministic and fail-open; unit-tested per rule in
  `test_plausibility.py`.
- **Validated against the 91 demo target formulas** (embedded in the test, hermetic): every
  genuine formula scores `> 0.5` (the tiny molecules `CH2O2`/`CH4N2O` are the lowest at
  ~0.74–0.78, graded down only because X/C ratios are noisy for one-carbon species), and the
  radicals stay at `1.0`. Only `C6H17NO4` (the over-saturated data error) is driven to `0.0`,
  reproducing the `rule_senior` finding. **Nothing real is wrongly rejected.**
- **Remaining (moves to P2):** the *isotope rule* (Rule 3) is intentionally NOT folded in —
  it is already the fit score's job (`score_pattern_v2`), kept separate per the design rule
  that chemistry never re-imports the measurement. Per-instrument calibration of how
  plausibility combines with fit is P2 (arbitration + FDR).

### P2 progress

- **Candidate arbitration landed** (`mascope_tools.composition.arbitration`,
  `arbitrate_candidates`). For a single peak it competes the candidates by
  **evidence = fit × plausibility** (the fit score `score_pattern_v2` × the graded
  Seven Golden Rules plausibility), ranks them, reports a per-candidate **confidence**
  (evidence normalised across the peak's candidates), and is **honest about ties** —
  candidates within `tie_tol` of the best evidence are flagged rather than forced into a
  false winner (Schymanski L5). Deterministic; unit-tested (`test_arbitration.py`),
  including the key property that a spectrally-good but chemically-impossible formula
  (e.g. over-saturated `C6H17NO4`) loses to a plausible one.
- **Dependency direction preserved (§3):** arbitration imports the fit score's *values*
  and the chemistry plausibility; neither imports arbitration. The fit score stays pure.
- **FDR helpers landed** (`arbitration.fdr_curve`, `threshold_at_fdr`). Given arbitrated
  winners' confidences and whether each was correct (a labelled golden set, or a
  target-decoy search where a decoy winning = wrong), they report FDR vs acceptance and
  pick a confidence cut for a tolerated FDR, with q-values so the threshold is unambiguous
  (Scheubert 2017). Ties in confidence are ordered conservatively. Unit-tested.
- **Measured on the full demo decoy pools** (`tooling/score_eval/arbitration_eval.py`,
  scratch; 16,386 anchors / 9,313 contested / 152 files, real-SNR full spectrum). Ranking
  by **fit × plausibility** beats **fit alone** decisively on the hard *contested* anchors:

  | metric | fit only | **fit × plausibility** |
  |---|---|---|
  | top-1 all | 0.833 | **0.880** |
  | top-1 **contested** | 0.706 | **0.789** |
  | false-winner rate (accept-all FDR) | 0.167 | **0.120** |
  | winners kept at ≤5% FDR | 12,263 | **13,301** |

  This is exactly the fit-score study's thesis, now quantified: the fit score's contested
  ceiling (~0.71) comes from mass-degenerate decoys it is (by design) blind to; **chemistry
  breaks those ties** — +0.083 contested top-1, a third fewer false winners, and ~1,000 more
  assignments surviving at the same FDR. The fit score stays the pure measurement; the
  arbitration layer adds the chemistry.
- **Wired into the peak-centric engine (Stage A).** `invert_matches_to_peak_assignments`
  now selects a peak's winner by **evidence = fit × plausibility** (not fit alone), so a
  chemically implausible formula cannot win a peak on mass fit, and stores the winner's
  arbitration **confidence**, **plausibility** and an **is_tie** flag in the assignment's
  `provenance` (no schema change; the `fit_score` column stays the pure measurement and the
  tier stays on the fit scale). Unit-tested (the over-saturated `C6H17NO4` loses its peak to
  glucose). Re-ran on the live demo dataset so the stored assignments reflect it.
- **Confidence calibration landed (pipeline; data provisional).**
  `mascope_tools.composition.calibration` turns the arbitration evidence into a calibrated
  **P(correct)** via Platt scaling ([Platt 1999][platt]): `P = sigmoid(a·evidence + b)`, a
  logistic map fit on labelled `(evidence, is_correct)` pairs. The design has two
  commitments that make it safe to ship before the datasets are final:

  1. **A calibration is a provenance-carrying object, not a bare constant.** `Calibration`
     records the instrument, the label counts, its **held-out ECE** (expected calibration
     error — the average gap between predicted probability and observed correctness), when
     and from what dataset it was fit, and a `provisional` flag. "We don't have curated data
     yet" becomes *metadata*, not hidden risk.
  2. **Never fabricate a probability we can't back up.** `calibration_for(instrument)`
     returns `None` when an instrument has no calibration (**TOF today**); the engine then
     stores `p_correct = null`, `calibrated = false` so the UI shows *uncalibrated* rather
     than a borrowed Orbitrap number. `fit_calibration` refuses to fit on too little /
     single-class data (`InsufficientCalibrationData`).

  **The label sourcing — why this is tied to the reference dataset.** The positives are
  *confident identifications*, most strongly compounds confirmed by a **reference standard**
  (Schymanski Level 1) — exactly what the reference dataset encodes; the negatives are
  near-mass **decoys** (`tooling/score_eval`). So a per-instrument calibration is literally
  "how well the score predicts reference-confirmed identity **on this instrument**". This is
  also the basis of **user self-calibration**: a user runs their known standards, Mascope
  scores them + decoys and fits *their* instrument's curve. Per-instrument because the
  score's ingredients — mass accuracy, resolution, noise — differ by instrument, so the same
  raw evidence means different confidence on an Orbitrap vs a TOF. The interactive
  verification-driven version of this — users confirm/reject identifications in the UI to build
  the golden set — is designed in
  [`verification_calibration_loop.md`](verification_calibration_loop.md).

  **Status.** One **provisional Orbitrap** curve, fit from the demo bundle via
  `arbitration_eval.py --fit-calibration` (scratch, untracked — like the harness above;
  evidence → P(correct) over all candidates, true vs decoy). It is a placeholder pending a curated dataset; no TOF curve exists.
  `invert_matches_to_peak_assignments` stores `p_correct` / `calibrated` / `calibration`
  in the assignment `provenance`. Unit-tested (`test_calibration_layer.py`, engine tests).

- **Remaining P2 / next:** replace the provisional Orbitrap curve with a curated fit and add
  a TOF curve once a TOF golden set exists; the **persisted, user-refittable per-instrument
  store** (a DB table — deferred); then Stage B arbitration once its candidates carry
  comparable per-candidate fits.

### P3 progress

- **Adduct co-occurrence corroboration (first increment) landed**
  (`mascope_tools.composition.corroboration.adduct_corroboration`). A real compound rarely
  appears as a single ion — it shows up through several adducts ($[M+H]^+$, $[M+NH_4]^+$,
  $[M+Br]^-$ …) that co-occur in one spectrum; their co-occurrence is **independent
  corroborating evidence** beyond any peak's fit (CAMERA 2012; IPA 2019). Per compound it
  counts the distinct adduct channels that independently support it and returns a bounded,
  saturating signal `corroboration = 1 - 2^-(n_adducts-1)` (0 for a lone adduct, 0.5 for two,
  0.75 for three …). Cross-peak but **competitor-blind within a compound** — it reads only
  *which* adducts were assigned, never the fit score, so it stays a separate, inspectable
  layer (design rule §3). Unit-tested (`test_corroboration.py`).
- **Validated on the refreshed demo** (read-only): of the confident (identified/candidate)
  compound-instances, **~66% are seen via ≥2 adducts** (2,108 via two, 3,017 via three;
  2,620 lone) — the signal is both abundant and discriminating.
- **Weighting the corroboration — measured (offset-decoy benchmark).** The first attempt, on the
  labelled decoy set (`corroboration_eval.py`), was **inconclusive**: `candidates.parquet` predates
  the adduct-rich v1.2 library and has almost no true multi-adduct chemistry (76/17,253 true
  (file, neutral) groups span ≥2 adducts; corroboration logistic coef ≈ **−0.045**). So a second,
  purpose-built instrument was written — `tooling/score_eval/corroboration_benchmark.py` — using a
  **target-decoy design on the real demo spectra**: for each true detection (its primary
  `[M+H]+`/`[M-H]-` envelope fits a real peak at m/z `p`), check whether a peak sits at each of the
  compound's *other* adduct m/z's, versus two nulls. Choice of null is everything:
  - **random offset** (a peak at `p + random Δ`): P = 0.008 → an inflated **~104×** LR.
  - **anchor-swap** (the *same* real adduct offset re-anchored on a random real peak): P = 0.251 →
    a defensible **~2.5×** LR. Adduct offsets like +NH₃ (17.027 Da) are generic mass differences
    that recur throughout a spectrum, so the random null is far too easy. The anchor-swap null is
    the honest one.

  **Adduct panel — use the operative one.** The panel must be the mechanisms actually assigned in
  the data, verified against the `ionization_mechanism` library — **not** the guessed `CHANNELS`
  in `make_candidates.py`. Only five mechanisms are ever assigned in the demo: `+H+`, `+NH4+`,
  `+(CH4N2O)H+` (pos) and `-H+`, `+Br-` (neg). An earlier run wrongly included `+HBrBr-` (**not a
  real mechanism** — a scratch-script invention, ~1 Da off the real `+Br2-`) and `+CO3-` (defined
  but **never assigned** here); both are excluded below. `+Br2-`/`+Br3-`/`+NO3-`/`+CO3-` are defined
  but unused in this dataset. `+NH4+` is real — in fact the *most common* positive adduct.

  Full run (152 files, 13,270 detections). The weight is strongly **adduct-specific** — the
  chemically distinctive bromide corroborates strongly, the generic pos adducts barely:

  | adduct | P(real) | P(swap) | LR | log-odds |
  |---|---|---|---|---|
  | `+Br-`        | 0.535 | 0.105 | **9.8×** | +2.28 |
  | `+NH4+`       | 0.513 | 0.314 | 2.3× | +0.83 |
  | `+(CH4N2O)H+` | 0.379 | 0.232 | 2.0× | +0.70 |

  By polarity: neg **9.8×** (bromide is chemically distinctive — mass defect + 1:1 ⁷⁹/⁸¹Br isotope
  pair), pos **2.1×** (the +NH₃/+urea offsets are generic mass differences). So corroboration is a
  **real but modest, adduct-specific** signal — not the dominant factor the naive random-null 104×
  implied.

  **Fold-in — implemented (D6).** The measured weights are folded into `p_correct` as an
  **additive per-adduct log-odds update** (not the adduct-agnostic count heuristic, which is kept
  only for display): `logit(p_correct') = logit(p_correct) + clamp(Σ_{observed adducts a} w_a, ±cap)`
  (`apply_corroboration`, cap 3.0). A compound seen via several confident adducts lifts each winner
  by the co-occurring adducts' `w_a`; the engine does this as a run-level post-pass over the M0
  winners and records `provenance.corroboration = {adducts, n_adducts, boost}`. The weights ride on
  the per-instrument :class:`Calibration` in the **calibration store** (`assignment_calibration`
  table + `calibration_store.load_calibration`, falling back to the in-code provisional curve), so
  they are refit per instrument + adduct panel, never hardcoded into the engine. Caveats: the
  weights are **instrument/library-specific** (they encode the Br/Ur reagent chemistry); "present"
  is monoisotopic-only, so the LRs are a **conservative lower bound**; and the anchor-swap pool
  includes real primaries (also conservative). Weights: `_corroboration_metrics.json`.
- **Then:** intensity-consistency across adducts and in-source-fragment grouping (the deeper
  CAMERA/IPA model).

## 5. References

- Kind, T.; Fiehn, O. *Mass accuracy is insufficient even at less than 1 ppm.* BMC
  Bioinformatics 2006, 7:234.
  [link](https://bmcbioinformatics.biomedcentral.com/articles/10.1186/1471-2105-7-234)
- Kind, T.; Fiehn, O. *Seven Golden Rules…* BMC Bioinformatics 2007, 8:105.
  Open-access mirror: [PMC1851972](https://pmc.ncbi.nlm.nih.gov/articles/PMC1851972/).
  **`chemical_plausibility` takes its numbers verbatim from this paper's Table 2**
  (element-ratio common / extended / extreme bands for H/C, N/C, O/C, P/C, S/C, F/C,
  Cl/C, Br/C, Si/C) **and Table 3** (the multi-element NOPS count restrictions, Rule 6).
  [link](https://bmcbioinformatics.biomedcentral.com/articles/10.1186/1471-2105-8-105)
- Böcker, S. et al. *SIRIUS: decomposing isotope patterns…* Bioinformatics 2009,
  25(2):218–224. [link](https://academic.oup.com/bioinformatics/article/25/2/218/218950)
- Dührkop, K. et al. *SIRIUS 4…* Nature Methods 2019, 16:299–302.
  [link](https://www.nature.com/articles/s41592-019-0344-8)
- Kuhl, C. et al. *CAMERA: LC-MS peak annotation and identification.* Anal. Chem. 2012,
  84(1):283–289. [link](https://pubs.acs.org/doi/10.1021/ac202450g)
- Del Carratore, F.; Schmidt, K.; Vinaixa, M. et al. *Integrated Probabilistic Annotation
  (IPA): a Bayesian-based annotation method… integrating biochemical connections, isotope
  patterns and adduct relationships.* Anal. Chem. 2019, 91(20):12799–12807.
  [link](https://pubs.acs.org/doi/10.1021/acs.analchem.9b02354)
- Broeckling, C. D. et al. *Enabling efficient and confident annotation of LC-MS
  metabolomics data through MS1 spectrum and time prediction.* Anal. Chem. 2016,
  88(18):9226–9234. [link](https://pubs.acs.org/doi/10.1021/acs.analchem.6b02479)
- Scheubert, K. et al. *Significance estimation for large-scale metabolomics annotations by
  spectral matching.* Nature Communications 2017, 8:1494.
  [link](https://www.nature.com/articles/s41467-017-01318-5)
- Schymanski, E. L. et al. *Identifying small molecules via HRMS: communicating
  confidence.* Environ. Sci. Technol. 2014, 48:2097–2098.
  [link](https://pubs.acs.org/doi/10.1021/es5002105)
- Sumner, L. W. et al. *Proposed minimum reporting standards for chemical analysis (MSI).*
  Metabolomics 2007, 3:211–221. [link](https://doi.org/10.1007/s11306-007-0082-2)
- <a id="platt"></a>Platt, J. *Probabilistic outputs for SVMs and comparisons to
  regularized likelihood methods.* Advances in Large Margin Classifiers, 1999.
  [link](https://en.wikipedia.org/wiki/Platt_scaling)

[platt]: https://en.wikipedia.org/wiki/Platt_scaling
