# Peak Assignment & Identification Confidence — Study, Design and Plan

- **Status:** building
- **As of:** 2026-11-24
- **Tracked in:** [Assignment quality plan](https://linear.app/ultra-trace/project/assignment-quality-plan-553f4854192c) (Linear; MAS-73, MAS-939 to MAS-944 and MAS-1262 carry the identification level, decision 38 is MAS-938; MAS-324 to MAS-327 the open P2 to P4 steps)
- **What:** The layered science of going from a peak to a confident identification: plausibility, arbitration, calibration, tiers.

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

*The formula-shape table* (decision MAS-979, 2026-11-22; built in MAS-1111). The same
chemistry used to be checked as a hard reject, a graded score and a tier cap, each with its
own numbers and exemptions. Each criterion now has one row, in
`mascope_tools.composition.formula_shape` (version `FORMULA_SHAPE_VERSION`, recorded on a
run's tiering record beside the rule version), and every reader reads it. A hard reject is
only what Senior's rules or valence forbid (`rule_senior`); every ratio rule is graded or a
tier cap. The graded bands are Kind & Fiehn 2007, Tables 2 and 3.

| Criterion | Hard bound | Graded band (common, extended) | Tier cap | Exemption half |
|---|---|---|---|---|
| Hydrogen, H/C | none (was 0.1-6) | 0.2-3.1, 0.1-6.0 | - | composition |
| Nitrogen, N/C | none (was 2) | 0-1.3, 0-4.0 | `context_prior`, odd nitrogen: N >= 2 and O < 2 per N | composition |
| Oxygen, O/C | none (was 2) | 0-1.2, 0-3.0 | `oxygen_lattice`: O/C > 1.3 with O >= 5 | composition |
| Sulfur, S/C | none (was 0.1) | 0-0.8, 0-3.0 | `context_prior`, sulfur: S >= 1 | composition |
| Phosphorus, P/C | none (was 0.05) | 0-0.3, 0-2.0 | - | composition |
| Halogens, F/C, Cl/C, Br/C | none (was 0.05; the twin re-read opened 0.8); the bromide and iodide grids hold no Cl or Br (MAS-1280) | F 0-1.5, 0-6.0; Cl, Br 0-0.8, 0-2.0 | - | composition |
| Iodine, I/C | none (was 0.05) | not graded | - | composition |
| Silicon, Si/C | none | 0-0.5, 0-1.0 | - | composition |
| Heteroatom co-occurrence (Rule 6) | none | the paper's Table 3 caps, graded | - | composition |
| Context windows, H/C O/C N/C DBE/C | none; a context may opt in to the gate (`windows_gate`) | 1.0 inside, half a quarter of the window's width past its edge, the floor at half; below C3 the valence checks; not asked of a neutral a reference list names (MAS-1280) | `nox_skeleton` | composition |
| Carbon-free | none: the grid's carbon floor is open (`GRID_CARBON_FLOOR` 0) | fails open, 1.0 | `carbon_free` | composition |
| Radical | none (Senior keeps odd-electron formulas) | none | `odd_electron` | ion |
| Oxygen-free nitrate cluster | none | none | `oxygen_free_cluster` | ion |
| Polyhalide through a halide | none | none | `polyhalide_cluster` | ion |
| Same-ion reading | none | none | `ambiguous_nitrogen`, `ambiguous_adduct` | ion |
| Plausibility floor | 0.0, Senior-impossible | floor 0.1 (`PLAUSIBILITY_FLOOR`) | the identification level's 5b, under the floor | - |

The **exemption rule** (`formula_shape.exempt`), revised the same day after the demo's
uronium targets were read against their spectra (MAS-885: five of eight user-authored
targets named the wrong neutral): a cap in the composition half is not asked of a row a
reference list names (`engine.named_by_a_list`, the row's `reference_identities`); a cap
in the ion half is asked of every row; a row only the target library names is spared
nothing, and still shows its match. The tiering pass and the cross-channel election read
it; a target no longer settles a same-ion reading.

**Radicals** meet one rule: the tiering pass holds no radical at assigned, whoever names it,
so a radical is no rival to a molecule. The same-ion election prefers the molecule, a list
hit's grid rivals leave radicals out (`closed_shell_rivals`), a rival set leaves them out of
a molecule's density and confidence (`rival_set._rivals`), and the cross-channel pass gives
a reading whose other readings are all radicals no rival. A radical's own rivals are every
other ion.

The context windows' factor enters every formula's plausibility in a run
(`formula_shape.graded_in`, which `pipeline.run_stages` enters after the resolve
stage): the search's ranking, both stages' evidence and tiers, the rival sets and the
identification level read one number. The universal bands grade every formula, Stage A's
included.

The windows' grade follows the exemption rule's composition half (decision MAS-1280,
2026-11-25): a neutral a reference list the run read names is graded on the universal
bands alone. A cited list naming the compound is stronger evidence than the context's
shape prior. On the demo bromide batch the ambient-air windows held dinitrophenol, from a
cited list, below assignability in 70 of 80 samples: an aromatic's H/C (0.67) and DBE/C
(1.0) sit just outside the windows' 0.7 and 0.75, a factor of 0.355. The run learns the
names where it reads its lists (`formula_shape.spare_listed`, from
`stage_a_pass.reference_named`): after Stage A, from its frame and the reference ions it
left out of the frame for reaching no peak (MAS-1282), in a batch search, and where a
batch's readings are judged as a run's rows. Stage A's own arbitration grades its frame
with the names its rows' provenance carries (`formula_shape.listed_in`), the ions left
out included, so a row whose provenance names a list is spared, and a neighbour's first
pass, read for a thin file's envelope pool, grades with its own. A neutral only the
target library names is graded in the windows like any other.

The **halide grids** (decision MAS-1280). The bromide and iodide profiles' grids carried
`Cl0-2 Br0-2` and `Cl0-1`, ported unchanged from the reference engine's profiles. With no
ratio bound on them they read the reagent's own ion as a halogenated neutral:
C20H32O11 + Br⁻ is the same ion as C20H33BrO11 − H⁺. Five builds on 16 demo bromide samples
(control assigned rows, swap arm): develop 2,257 and 45; the branch with the bound open
1,370 and 64; a 0.05 per carbon bound in the table 2,102 and 44; the carbon floor back
1,421 and 64; no Cl or Br in the two grids 2,209 and 41. The last was chosen. No shipped
list or target names an inorganic halogen the grids would serve, and develop's grid never
reached one. A halogenated organic in a bromide or iodide run comes from a list, from its
twin line (`halogen_twins`: chlorine, in a bromide run), or from a context that opens the
element. A context only narrows a grid today; how one opens an element, and the
reagent-alias rule that applies once one opens the reagent's own halogen, are #3635.

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
*Gap.* Intensity-consistency across adducts and in-source-fragment grouping are measured and
recorded (§4, P3 progress, step 4.16) but not weighed: within one sample neither showed the effect
it is named for.

### L3 — Instrument & acquisition context
*Basis.* The instrument and method constrain identity. **Mass resolution** sets the mass
term's width (already in the fit score via the fitted $\sigma$). **Retention time** is a
strong orthogonal axis: predicted-vs-observed RT consistency markedly improves annotation
([Broeckling et al. 2016, *MS1 spectrum + time prediction*](https://pubs.acs.org/doi/10.1021/acs.analchem.6b02479)).
**Ionization mode and reagent chemistry** make some adducts/compounds (im)plausible.
*Today.* Resolution-aware mass is in the fit score. RT and ionization-behaviour priors are
**not** used as confidence evidence.
*Gap.* A retention-time consistency term (where RT data exists; designed, not built, in
§4 [P4 design: a retention-time term](#p4-design-a-retention-time-term)) and
ionization/reagent priors as candidate-level evidence.

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
  ionization`, e.g. `C10H15O5` + `[M+H]+` → `C10H16O5+`). After the radical fail-open, the rule
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
  `provenance` (no schema change; the `fit_score` column stays the pure measurement).
  Unit-tested (the over-saturated `C6H17NO4` loses its peak to glucose). Re-ran on the live
  demo dataset so the stored assignments reflect it.
- **The tier is banded on that same evidence.** Winner selection and tiering now read *one*
  quantity, which is precisely the fix: while the tier sat on the fit scale, a chemically
  implausible formula could win its peak on evidence and then be tiered as though it had fit
  cleanly — the ledger's strongest word contradicting the arbitration that produced it. The
  `fit_score` column is untouched by this and still stored and displayed as the pure
  measurement; it is simply no longer what buckets the row. Banding on evidence is only
  coherent if *nothing* still bands on the fit, so every derivation site moved together:
  both engine stages, manual curation and its demote-restore fallback, the copy service's
  re-tier, the composition-search preview, and the import tier-coherence check. They share
  two helpers — `engine.tier_for_evidence(evidence, *, candidate_threshold,
  assigned_threshold)` (renamed from `tier_for_score`, with the bands now **keyword-only**,
  because they read in the opposite order to their names and a positional call written in
  band order silently inverted them) and `engine.evidence_for(fit_score, formula)` for the
  callers that hold a stored row rather than a live scoring frame. `evidence_for`
  **recomputes** the plausibility from the formula instead of reading it off a payload — it
  is a pure function of the formula, so there is nothing to gain from trusting an asserted
  number — and fails open to the bare fit when the formula is absent or unparseable, since
  plausibility must never decide whether a write happens. `PEAK_ASSIGNMENT_ENGINE_VERSION`
  is `0.3.0`.
- **The bands themselves, measured (0.75 / 0.45).** Plausibility is ≤ 1, so folding it in can
  only move a row down; leaving the bands at the fit-scale 0.8/0.5 would have tiered every
  run stricter than before under an unchanged-looking config. How far to lower them was
  settled by sweeping the pair over a real ledger — all 161 demo samples assigned, 213,146
  rows, 77,911 of them tiered — rather than by taste. Plausibility turns out not to be a
  broad downshift but a **spike at 1.0 with a thin tail**: 92.8% of tiered rows score exactly
  1.0 (Stage A 98.3%, Stage B 88.4%), so only 7.2% of rows move at all, and the bands need
  far less taken off them than "evidence ≤ fit" suggests.

  | tiering | assigned | candidate | below_assignability |
  |---|---|---|---|
  | fit at 0.8 / 0.5 (old) | 84.08% | 12.41% | 3.51% |
  | **evidence at 0.75 / 0.45** | **85.38%** | **11.73%** | **2.89%** |

  6.93% of tiered rows change tier, and in **both directions** (2,717 up, 1,710 down) rather
  than draining one band — 0.75/0.45 was the closest pair in the sweep to the split it
  replaces. The config keys are unchanged (`assigned_threshold`, `candidate_threshold`) and a
  run still records the pair it tiered with in `tier_bands`; only the scale those numbers sit
  on moved. They are **directional, not calibrated truth** — a defensible starting point that
  keeps the tier histogram close to what it was while letting the chemistry demote the
  implausible. Binding the tier to a calibrated **P(correct)** remains the documented end
  state, still gated on universal calibration coverage (untargeted + all instruments) and
  still deferred; this is a step toward it, not its arrival — but the tier and that eventual
  probability are now read off the same number, since evidence is what the calibration maps
  *from*.
- **One pair of bands for both stages, and now one scale.** The bands were set while Stage
  A's fit was `ion_score_v2` and Stage B's was `score_pattern` (v1, no per-peak SNR), so a
  single band meant slightly different things to each — on the sweep, holding the upper
  band at 0.80 would have cost Stage B 5.3% of its assigned rows and Stage A only 0.5%.
  That heterogeneity is gone: Stage B's committed readings are measured again as ions
  through one `compute_match_isotopes` pass with the run's gating, so both stages are
  tiered on `ion_score_v2`. Per-stage bands, the apparatus this note argued was not worth
  building, are not needed.
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
     stores `p_correct = null` and the run records no curve, so the UI shows *uncalibrated*
     rather than a borrowed Orbitrap number. `fit_calibration` refuses to fit on too little /
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
  `invert_matches_to_peak_assignments` stores `p_correct` in the assignment `provenance`;
  which curve produced it is recorded once per run (`PeakAssignmentRun.confidence_calibration`,
  via `engine.calibration_meta`) and the detail read folds it back into the row as
  `calibrated` / `calibration`, so readers see the pair they always did. Unit-tested
  (`test_calibration_layer.py`, engine tests).

- **Remaining P2 / next:** replace the provisional Orbitrap curve with a curated fit and add
  a TOF curve once a TOF golden set exists; the **persisted, user-refittable per-instrument
  store** (a DB table — deferred). Stage B already *tiers* on evidence — its contenders
  carry a fit and a plausibility and it records `provenance.evidence` for its winner, so
  the untargeted stage is not blind to chemistry. It also already *competes* the
  candidates that land on one observed peak: they are ranked by evidence, closest mass,
  then formula, and the losers are kept as `alternatives` each with its own fit.
- **Stage B's confidence — built, behind a run option (step 4.15, MAS-325).** The
  winner's share among the peak's full scored candidate set needs a fit for every entry
  of the finder's `other_candidates` shortlist, which reaches the row as formula names
  only. The inspector measures one peak's shortlist on request (`alternatives_scoring.py`);
  a run now affords the same measurement once it is bounded. With
  `stage_b_confidence` on (off by default until the runtime cost is measured on the gate
  sets), `alternatives_scoring.seed_shortlists` takes the run's elected
  untargeted M0 winners before the judging (since MAS-1072; it ran after the judging
  passes before), the most intense first up to
  `stage_b_confidence_peaks` (200) of them, and seeds each one's stored formula-only
  shortlist (at most `max_alternatives`) under every adduct the run searched, the
  secondary channels it opened included, in **one** `compute_match_isotopes` pass. A
  formula counts for the peak only where its ion's monoisotopic line paired to it, by the
  inspector's own rule (`placements`). The pass is scored at the measurement the winners'
  own fits were scored at, the seeded re-score's (`seeded_scoring.scored_accuracy`), so
  the winner and its rivals are judged at one width, as Stage A judges its candidates
  inside one frame; never at one fitted off a frame of mostly wrong formulas. The winner, its recorded rivals (not the `same_ion` readings,
  which the finder elects before anything is ranked) and the placed entries go through
  `arbitrate_candidates` as **one candidate per ion on the peak** (`ion_identity`): the
  shortlist lists every composition whose mass fits under every adduct searched, so it
  carries one ion split two ways (a neutral read through `[M+Br]-` beside the neutral
  plus HBr read through `[M-H]-`). Counted as two, such a pair split its own weight and,
  where it was the winner's ion, tied the winner with itself: on the demo's bromide set
  that first cut flagged a third of the winners a tie. An entry reading the winner's
  ion is the winner; of another ion's readings the best evidence stands for it. The
  winner's `confidence`, `n_candidates` and `is_tie` are
  stored in provenance under Stage A's keys, so the inspector's confidence row reads them
  unchanged. A winner past the budget records none — a share of part of the field would
  read as a share of all of it — and one with no shortlist records its share among the
  rivals it already carries, at no cost. The fit and the alternatives stay what the run
  decided. The tier can move since MAS-1072: each measured formula joins the winner's
  rival set before the caps, so one the evidence cannot separate from the winner counts
  in its `candidate_density`, and the density rule can cap the winner at candidate. The run records the counts and the seconds the
  pass took in `search_scope.winner_confidence`, which is where the gate-set measurement
  reads the cost.

  **`p_correct` stays null on Stage B rows.** The scale objection is gone: both stages'
  fit is the seeded `ion_score_v2` re-score (milestone 2), so the curve would no longer be
  read across the v1/v2 gap. What still does not match is the population: the provisional
  curve was fitted on Stage A's candidates (library targets against near-mass decoys), and
  a Stage B row's `p_correct` would also enter the batch consensus, which takes the best
  member's `p_correct` (`batch_peaks`) and whose untargeted members are written
  uncalibrated on purpose. Whether the untargeted stage carries a probability is the
  calibration's call once it has untargeted labels, not this option's.

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
- **Validated on the refreshed demo** (read-only): of the confident (assigned/candidate)
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
  - **random offset** (a peak at `p + random Δ`): P = 0.008 → an inflated **~57×** LR.
  - **anchor-swap** (the *same* real adduct offset re-anchored on a random real peak): P = 0.251 →
    a defensible **~1.8×** LR. Adduct offsets like +NH₃ (17.027 Da) are generic mass differences
    that recur throughout a spectrum, so the random null is far too easy. The anchor-swap null is
    the honest one.

  **Adduct panel — use the operative one.** The panel must be the mechanisms actually assigned in
  the data, verified against the `ionization_mechanism` library — **not** the guessed `CHANNELS`
  in `make_candidates.py`. Only five mechanisms are ever assigned in the demo: `[M+H]+`,
  `[M+NH4]+`, `[M+CH4N2O+H]+` (pos) and `[M-H]-`, `[M+Br]-` (neg). An earlier run wrongly included
  `[M+HBrBr]-` (**not a real mechanism** — a scratch-script invention, ~1 Da off the real
  `[M+Br2]-`) and `[M+CO3]-` (defined but **never assigned** here); both are excluded below.
  `[M+Br2]-`/`[M+Br3]-`/`[M+NO3]-`/`[M+CO3]-` are defined but unused in this dataset. `[M+NH4]+`
  is real — in fact the *most common* positive adduct.

  Full run (152 files, 13,270 detections). The weight is strongly **adduct-specific** — the
  chemically distinctive bromide corroborates strongly, the generic pos adducts barely:

  | adduct | P(real) | P(swap) | LR | weight, ln LR | odds ratio (ln) |
  |---|---|---|---|---|---|
  | `[M+Br]-`       | 0.535 | 0.105 | **5.1×** | +1.63 | 9.8× (+2.28) |
  | `[M+NH4]+`      | 0.513 | 0.314 | 1.6× | +0.49 | 2.3× (+0.83) |
  | `[M+CH4N2O+H]+` | 0.379 | 0.232 | 1.6× | +0.49 | 2.0× (+0.70) |

  The weight is the likelihood ratio `P(real) / P(swap)`, the factor a Bayesian odds update
  multiplies by. The weights first shipped were the log odds ratios of the same rates, which
  overstate the update when the rates are not small (#3161); the benchmark prints both.

  By polarity, as odds ratios (their rates were not recorded): neg **9.8×** (bromide is chemically distinctive — mass defect + 1:1 ⁷⁹/⁸¹Br isotope
  pair), pos **2.1×** (the +NH₃/+urea offsets are generic mass differences). So corroboration is a
  **real but modest, adduct-specific** signal — not the dominant factor the naive random-null 57×
  implied.

  **Fold-in — implemented (D6).** The measured weights are folded into `p_correct` as an
  **additive per-adduct log likelihood ratio update** (not the adduct-agnostic count heuristic, which is kept
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
- **Intensity consistency and in-source fragments, measured (step 4.16).** The two deeper layers
  of the CAMERA/IPA model, measured the same way on the public demo dataset 1.4.4 (161 files,
  15,057 detections), with true likelihood ratios, an intensity-matched anchor swap and a
  bootstrap over files; the numbers and the controls are in `assignment_quality_plan.md`, step
  4.16, and `tooling/score_eval/_step416_metrics.json`.
  - *A neutral's channel ratio against its sample's other neutrals'*
    (`corroboration.channel_ratio_consistency`): the consistent band is 0.79 to 0.92 on every
    adduct pair, under one; real pairs spread wider than chance ones. It corroborates nothing
    within one sample and is recorded on the row (`cross_channel.intensity_consistency`), the
    per-sample point of the ratio the continuity pass reads over a batch.
  - *In-source fragments* (`corroboration.in_source_fragment_pairs`, a neutral-loss table of
    H2O, CO, CO2, CH2O2, CH4O, HNO3, SO3, the lighter on the shorter peak): every loss clears one
    (1.40 to 2.22), and so does the same neutral gained (1.13 to 1.48) and the series units CH2
    and O (1.32, 1.33), the spectrum's lattice of related formulas; against that mirror the named
    pair gains 5 to 12% but for HNO3 (1.9x), and no loss shows a fragment's intensity signature.
    A reason on the row that caps nothing (`in_source_fragment`).
  - Neither enters `p_correct` (the assignment plan's decision 37, MAS-860). HNO3's excess over
    its mirror is what the mirror's nitrogen imbalance predicts without fragmentation: the loss
    lands on a formula without nitrogen, the gain on a dinitrate. A weight, if one is earned,
    enters through calibration from verdicts (step 4.3).

### P4 design: a retention-time term

*Design only (step 4.17, MAS-327); nothing below is built.* Retention time is the strongest
orthogonal axis L3 names (Broeckling 2016), and it is meaningless for the data this plan is
measured on. A CIMS acquisition is direct-inlet: a sample is a time window of a continuous
acquisition, and the time a scan was taken says when the air was sampled, not how a compound
behaves on a column. So the term is gated before anything else: it exists only for a run whose
acquisition declares a chromatographic separation, and for every other run it is absent, not
neutral. Every gate set, the demo dataset and the fleet corpus are direct-inlet, so none of
them can move by it.

**1. Where the time comes from.** Two readings, both of which exist only for a declared
separation:

- *The declaration.* A run is chromatographic when its files resolve to a **separation
  method** (point 2): the file's method key (`method_keys.method_key`, the acquisition method's
  file name) through its method binding, or a `separation` block in the acquisition record
  ([Acquisition records](../user/instruments/acquisition-records.md); a later schema version,
  optional, naming the method and the injection time). Mascope does not detect chromatography
  from the data. A direct-inlet file can show a peak that rises and falls (a plume, a chamber
  injection), and reading that as elution would hand the term to exactly the runs it must not
  touch.
- *The observed time.* The reader already gives every scan its time from the acquisition
  start (`retention_time`, minutes; Thermo's `RetentionTimeFromScanNumber`). Today a peak is
  found in the spectrum averaged over the sample's window and carries no time. For a
  chromatographic run the term reads the peak's **elution apex**: the extracted ion
  chromatogram at the peak's m/z, within its fitted width, over the MS1 scans of its stream in
  the sample window. The apex is the intensity-weighted centre of the profile's top half, minus
  the method's declared delay, so time zero is the injection. The profile is *testable* only
  when it has one maximum above its half height, at least five scans above that half height,
  and falls below the half height on both sides inside the window. Otherwise it is
  *untestable* and the term is absent for that peak. The single-maximum condition covers two
  elutions at one m/z, such as isomers, whose averaged spectrum shows one peak: their centre
  would fall between them, at neither one's time. The both-sides condition covers a
  chromatographic file split into time windows: a window that cuts an elution, before or after
  its maximum, gives no time rather than a wrong one. Giving each elution its own time is a
  later increment.

**2. What a retention-time library is keyed by, and who writes it.** A retention time is a
property of one laboratory's column and gradient, not of a compound, and does not transfer
between systems without a mapping (Stanstrup et al. 2015; Boswell et al. 2011). So the key is
the separation, never the instrument or the reagent chemistry:

- A **separation method** row: the column (stationary phase, dimensions, particle size), the
  mobile phases, the gradient program (time against composition), flow, temperature, the dead
  time and the injection delay. Its identity is a digest of those parameters. It is versioned
  append-only as the profiles are ([`chemistry_profiles.md`](chemistry_profiles.md) §3.1), and
  stamped into the run's config snapshot (§3.2 there), so a re-run is reproducible. It is an
  axis of its own beside the two profile axes: one reagent chemistry runs behind many columns,
  and one column behind many sources.
- A **library entry**: (separation method, compound) to a retention time, its spread from
  replicate injections, the number of injections, the date measured and the author. The
  compound is a structure (InChIKey, with its formula), because isomers share a formula and
  differ in retention: the entry can name the structure behind a formula, which the
  identification claim ([`identification_claims.md`](identification_claims.md) §5.4) needs for
  levels 1 and 3.
- **Authored by the lab that runs the method**, from authentic standards injected on it, and
  imported as a table through the CLI or the API by an editor of the workspace. Mascope ships no
  retention library: a public table measured on another system would be a confident number for
  the wrong column. Predicted retention (a model trained on a large standard set, Domingo-Almenara
  et al. 2019) and mapping another system's table onto this one (PredRet, Stanstrup et al. 2015)
  are later sources, each with its own weight and spread, never mixed into measured entries.
- Column ageing and a new column lot shift every time. A later increment aligns each batch on
  internal standards it carries, as mass calibration aligns the axis; until then the spread a
  library entry records has to cover the drift the lab sees.

**3. How it enters evidence.** As a per-candidate log-odds term in arbitration (L4/L5), beside
plausibility and the adduct corroboration of P3, and never in the fit score (design rule 1):

$$\mathrm{logit}(p') = \mathrm{logit}(p) + w_{RT}(z), \qquad z = \frac{|t_{obs} - t_{lib}|}{s}, \qquad s = \max(s_{entry},\ s_{floor})$$

- $t_{lib}$ is the entry of the candidate formula's library compound closest to $t_{obs}$;
  $s_{entry}$ is the entry's recorded spread and $s_{floor}$ the larger of two scan intervals
  and the method's declared tolerance, so a library of single injections cannot claim a window
  narrower than the data can resolve.
- **Graded:** $w_{RT} = w_{in}$ for $z \le 1$, falling linearly to $w_{out}$ at $z \ge 3$.
- **Floored:** $w_{out}$ is negative and bounded (as `apply_corroboration` caps at 3.0), so a
  miss counts against a candidate but never vetoes it: grade, not gate, as for the chemistry
  context ([`chemistry_profiles.md`](chemistry_profiles.md) §4.2). A wrong library entry, or a
  co-eluting isomer the library lacks, then costs a rank, not the row.
- **Absent** - no term and no provenance key, which is not the same as 0 - when the run has no
  separation method, when no library entry shares the candidate's formula, or when the peak is
  untestable (point 1). Where it applies, the run records
  `provenance.retention = {separation_method, compound, t_obs, t_lib, z, boost}`, so the
  inspector can show why a candidate moved.
- $w_{in}$ and $w_{out}$ are measured (point 4), ride on the calibration store keyed by the
  separation method as the corroboration weights ride on it per instrument (D6), and are never
  constants in the engine. The term names a structure: a candidate lifted by it carries the
  library compound as its structure candidate, which is what lets a verdict claim level 3 from
  retention, and level 1 with MS/MS against the same standard.

**4. The measurement that would validate it.** A chromatographic dataset with standards, which
Mascope does not have: LC-HRMS acquisitions on one declared method, a mix of about 30 or more
authentic standards spanning the gradient and including same-formula isomer pairs, injected
neat and spiked into a matrix, with replicate injections on at least two days. The standards
are split: day 1's neat injections build the library, day 2's spiked ones are scored. It reads:

- the observed spread across days, which sets $s_{floor}$ and the shape of the window;
- the likelihood ratio, P(within the window | the true compound) against P(within the window |
  an anchor swap: another entry's library time), the null P3 found honest where a random offset
  was not; that ratio gives $w_{in}$, and the swap's rate beyond $z = 3$ gives $w_{out}$;
- the ranking: whether the term moves the true formula above mass-degenerate rivals
  (`score_eval`'s AUC), and the true structure above its isomer, without raising the decoy arms'
  false-assignment rate;
- the null on direct-inlet data: the gate sets' runs with the term built are identical to
  runs without it.

**Issues filed from this: none yet.** Every step needs the dataset first, and neither a step
nor its gate can be measured without one. When the plan owner has such a dataset in view, the
build is four steps, each blocked by the one before: (a) the separation method and library
tables with the import; (b) the elution apex reading in the peak store; (c) the term in
arbitration with its provenance and the inspector's line; (d) the measurement above, which sets
the weights. Until then retention time stays in the quality plan's *Not in this plan* list.

### P4 design: the identification level

*The scale peaky and Mascope share, described by peaky's owner in November 2026 and adopted by
the quality plan's decision 38; built in its steps 5.8 to 5.8g. This section is the one
definition: the plan, the claims note and the surfaces point here.*

**1. The scale.** The numbers keep Schymanski's meaning (2014). The letters name the kind of
evidence that earned the number, not a rank inside it: 3c is not weaker than 3a, it rests on
something else.

| Level | Meaning | Established by | Sub-levels | A reader may say |
|---|---|---|---|---|
| 1 | Confirmed structure | An authentic standard on this instrument with the same reagent chemistry (same mass, ionization and fragments), plus one independent line: MS/MS, a separation or another instrument. Assigned in review only. | - | "It is pinonic acid." |
| 2 | Probable structure | Two or more diagnostic fragments that fit one structure, or a library spectrum match; rivals excluded. | 2a library spectrum match; 2b diagnostic evidence: fragments, a separation, or a formula with one plausible structure (TFA, HSO4-) | "Almost certainly pinonic acid." |
| 3 | Tentative candidate | An established formula plus a context list, ionization behaviour or one MS/MS loss. The ceiling without MS/MS, for a formula with more than one plausible structure. | 3a data and list agree; 3b one functional group; 3c context list; 3d homologue series | "Consistent with pinonic acid", or "an organic acid of this formula". |
| 4 | Molecular formula | Every competing formula in the mass window excluded by mass and isotopes. | 4a neutral formula, the molecule-reagent split pinned; 4b ion formula, the split open | "C10H16O3, isomer unknown." |
| 5 | Exact mass | A measured mass; the look-alikes not excluded. | 5a rivals remain, listed; 5b the reading refuted by its isotopes, chemically implausible, or untestable | "A peak at m/z 183.1027." |

The numbers fall in three bands, a mass (5), a formula (4) and a claim about structure (3 to
1), and wherever a level is drawn its band takes a colour, always with the level's text beside
it. Peaky's owner's description draws the bands grey, yellow and green. Mascope draws them in
three tokens of their own (`--level-mass`, `--level-formula`, `--level-structure`), outside the
four state tokens: the level chip sits beside the tier chip, where green already says assigned
and yellow candidate ([`frontend_design_system.md`](frontend_design_system.md), "States"), and
a formula at 4 is not a warning.

**2. The row contract.** Every committed row carries three things, in its provenance and on
every surface that shows the row:

- `level`, the number and its letter (`4a`);
- `evidence`, one line naming what earned it ("13C line fits ten carbons; two rivals out by
  mass, one by its 13C line; one split");
- `lift`, what would raise it, written from the tests the row is missing ("a context list
  for the run's chemistry; MS/MS").

Beside them: the facts themselves, each named with the record it was read from; the rivals,
each with the test that excluded it or the word *open*; and, on the run, the version of the
rules that read them (`level_rules_version`, point 7). An isotopologue row shows its parent's
level; an artifact row carries none. An imported run's row may carry the same three
(`engine_level`, `engine_evidence` and `engine_lift` on the import contract, quality plan steps
5.6 and 5.8): the level checked against the scale's vocabulary, the two lines shown as the
reference wrote them, so the reference's level is read beside Mascope's rather than translated.

**3. Who may state which level.**

- **The engine** states 5, 4 and 3 from what a run and its batch record (steps 5.8 to 5.8d),
  and 2b only where a cited list marks the formula as having one plausible structure (5.8b;
  the marks are authored by peaky's owner as cited chemistry data, PKY-39).
- **MS/MS evidence** lets the engine state 4a (the adduct releases its reagent cleanly) and 3b
  (one named loss), from the MS2 project's per-row evidence once its decision lets a supported
  verdict move anything (5.8e). 2b (two or more fragments that fit one structure) and 2a (a
  library spectrum) name a structure, so they are offered to a person as a pre-filled claim
  until a harness has measured their false rate on decoys.
- **A person**, and only a person, states 1: a claim on the verification tables
  ([`identification_claims.md`](identification_claims.md) section 5), scoped to the sample or
  the time window its evidence covers, pointing to that evidence (5.8f). A person may also
  state 2 or 3 with the evidence that earns it.
- **The level a reader is shown** is the live claim's where there is one, else the engine's
  (the claims note's rule 4). A standard that disagrees is a result too: the claim is
  rejected and the row reads the engine's level again.

**4. From Mascope's facts to a level.** Each sub-level, the facts that earn it, and the step
that builds it. Nothing here is a new measurement: each fact is recorded today by the step
named, and the level is a reading of them.

A row shows one letter. Where it earns two of one number, a fixed order picks the one it
shows and the evidence line names the others; the order is part of the rules and versioned
with them (point 7). 5b goes before 5a: a refuted reading is not one with its rivals open. 4a
and 4b cannot both hold, since 4b is a split nothing pinned. 3a holds wherever its own
definition does (a context list with two routes, or with an anchored homologue, so 3c with 3d
is 3a); otherwise 3c, then 3d, then 3b, from compounds named, to a series, to a class. A
nitrate `[M-H]-` row on a context list reads 3c, "an acid" in its evidence line. The order
says which statement leads the row, not that one kind of evidence outranks another.

- **5b, refuted or untestable** (5.8; from the batch, 5.8c). Refuted by its isotopes: a line
  of its predicted envelope the spectrum should hold and does not (`absent_line`, quality plan
  3.4e), or, on a batch peak, a line the reading cannot make standing where a rival's would (a
  37Cl or 81Br line 4.2c stamped as the peak's isotopologue under a halogen-free reading; a
  batch fact, recorded after a sample's run has stated its level, so read by 5.8c).
  Implausible: plausibility under its floor (P1). Untestable: the peak is not separated from a
  neighbour (`unresolved_peak`, 3.4f), so neither its mass nor its height was measured.
- **5a, rivals remain** (5.8). The shortlist of formulas that fit the peak's mass through the
  run's channels (`other_candidates`, measured by 4.15) holds a rival no test excluded; the row
  lists each. A row whose shortlist was not measured reads 5a with "rivals not measured" as its
  evidence and "measure the shortlist" as its lift: the level does not assume an exclusion it
  did not see.
- **4, every rival excluded** (5.8; the batch's tests, 5.8c and 5.8d). Each rival on the
  shortlist is out on a named test: mass (its measured evidence under the winner's by more than
  the tie margin, the mass term separating them at the run's fitted width; 2.2, 5.9), isotopes
  (a tracking line the rival cannot make, its own predicted line absent where the
  detectability threshold says it would show, the carbon count a 13C line gives; from the
  batch, a halogen lock or a stamped line the rival cannot make, 5.8c), the molecule seen
  through a second channel that commits the winner's neutral and not the rival's (2.3; on a
  TOF only with co-variation, 5.8d), or a homologous series: a member at exact spacing from an
  anchor whose formula holds at 4, co-varying with it, takes the anchor's formula plus its
  units, since each of its rivals would need an anchor the anchor's own tests excluded (5.8d);
  its letter then follows the split, as on any row. A rival held back only by a prior (3.6)
  or by plausibility is not excluded; it stays listed. On a list's row the formula search
  names such a rival too: the grid's formulas the fit alone could not separate from the
  reading are measured into the row's rival set apart from those the evidence could not
  (`rival_set.fit_tied`, MAS-975), which the density and the arbitration do not read; and
  the grid's ions its candidate limit (64 per peak, nearest in mass first) left unscored
  count as rivals not tested (`rivals_unscored`). `no_close_rival` is a different
  statement - that the peak's evidence separated the winner, not that each rival failed a
  test - so the mapping reads the shortlist, not that reason.
- **4b, ion formula, split open** (5.8). The ion's formula holds, but the ion reads as more
  than one molecule-reagent split under the run's channels and nothing settled which: the
  same-ion readings the finder and the cross-channel pass record (`ambiguous_adduct`,
  `ambiguous_nitrogen`, a mirror row's other readings of 2.5c). The nitrate cluster against an
  N-bearing `[M-H]-`, and urea on uronium, are the common cases.
- **4a, neutral formula, split pinned** (5.8 in a sample, 5.8c and 5.8d from the batch, 5.8e from
  MS/MS). One plausible decomposition: the ion's formula cannot hold the unit a cluster or
  adduct channel of the run adds, so it reads only as the molecule's own ion (no N under
  nitrate, since the cluster's NO3 brings one; no Br under bromide; fewer than two N under
  uronium, since urea brings two; each profile names its units);
  a labelled reagent, whose label tells the reagent's atoms from the molecule's (a 15N
  profile, with 4.2g's label twin reading the label present); a second ionization route that
  commits the same neutral (`same_ion_settled`; on uronium, 4.2g's neutral pair once
  co-variation is weighed); or MS/MS in which the adduct releases its reagent cleanly.
- **3c, context list** (5.8b2). On a 4a row, a curated, cited list that the run's environment
  selected through its regimes holds a compound of the row's neutral formula; the row names
  the compounds, the list and the regimes. The environment is the join (the plan owner's call
  of 2026-11-24, MAS-1259): a list's `applies_to_contexts` holds regimes, not chemistry
  contexts, and an environment names the regimes its samples read
  ([`sample_filters_and_metadata.md`](sample_filters_and_metadata.md) 2.6 and 4.6, list
  selection by environment, MAS-342). A list read for any other reason raises nothing: with no
  environment in play, because it declares no regimes, or because it is `always_active` (a
  background list, such as the contaminants, is not a compound the study expects). Neither
  does a structure database or a target's declaration: those are candidates
  ([`identification_claims.md`](identification_claims.md) section 4), not context.
- **3b, one functional group** (5.8b from ionization, 5.8e from MS/MS). On a 4a row, one
  constraint on the structure: a class, not a name. From ionization, through a table each
  profile carries, authored by peaky's owner with every entry cited (PKY-39): deprotonation
  by a nitrate reagent says an acidic hydrogen, protonation on uronium a basic site. From
  MS/MS, one named loss: CO2 a carboxylic acid, H2O2 a hydroperoxide, NH3 an amide or amine.
- **3a, data and list agree** (5.8b2, 5.8d). A 3c row that also has two routes (a second
  channel committing its neutral) or an anchored homologue (a series member whose anchor
  holds 3a or 3b). The strongest automated level for a formula with more than one plausible
  structure.
- **3d, homologue** (5.8d). On a 4a row, a member of a series at exact spacing (4.1's CH2, O,
  CF2 and the other units) whose other members hold 3a or 3b and co-vary with it (4.2). A
  member left at 5a can reach 4a through the series itself (the level-4 test above), which is
  how a series carries a TOF row to 3d. The co-variation is read once decision 35 lets it
  count, at the bar of a null model that 4.13 left open and that has no step yet (#3237,
  MAS-903).
- **2a, 2b, 1** as point 3: MS/MS (5.8e), a one-structure list mark (5.8b), a person's claim
  (5.8f).

**5. The instrument class.** On a TOF most rows stay at 5a, and that is the honest reading,
not a defect of the mapping: a window of several ppm holds several formulas and the 13C line
merges with its neighbours, so no carbon count is read. Only a heavy-isotope twin (37Cl, 81Br,
4.2g's halogen lock) excludes a rival by isotopes there. A second reagent lifts a TOF row only
where the two channels co-vary, to rule out chance pairings; so does a homologous series at
exact spacing that co-varies (point 4's series test), and the same air measured on an
Orbitrap, whose formula at 4 corroborates the TOF row (5.8g). On an Orbitrap a rival left by
resolution is decided by its 13C line where that line is testable; under the file's
detectability threshold (3.5b's curve) a dim line excludes nothing, the row stays 5a, and its
lift says "more signal".

**6. The level and the tier.** They read different tests: the tier, the fit against its bands
and a second observation; the level, which rivals each test excluded and what context adds.
Neither is derived from the other, and the level moves no tier, role, member or claim. The
quality plan's gate reads them crossed per set (assigned rows at 5; rows under assigned at 4
or above) and reads the decoy arms' rows at 4 or above; whether a tier rule follows from the
crossing is decided at the stage-4 gate on those readings (MAS-945).

**7. Versioning.** The engine's mapping is a rule set, versioned as the tier's is
(`level_rules_version` on each run's provenance, `LEVEL_RULES_VERSION` beside
`TIERING_RULES_VERSION`) and bumped when a fact it reads, a rule in it or point 4's order
changes. It is not the claims' evidence-to-level mapping, whose version a claim records as
`level_mapping_version` (`claims.LEVEL_MAPPING_VERSION`; the claims note, section 5.2): a
claim's level is fixed when the claim is made, under that version, and the two sequences move
apart.

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
- Stanstrup, J.; Neumann, S.; Vrhovšek, U. *PredRet: prediction of retention time by direct
  mapping between multiple chromatographic systems.* Anal. Chem. 2015, 87(18):9421–9428.
  [link](https://doi.org/10.1021/acs.analchem.5b02287)
- Boswell, P. G. et al. *Easy and accurate high-performance liquid chromatography retention
  prediction with different gradients, flow rates, and instruments by back-calculation of
  gradient and flow rate profiles.* J. Chromatogr. A 2011, 1218(38):6742–6749.
  [link](https://doi.org/10.1016/j.chroma.2011.07.070)
- Domingo-Almenara, X. et al. *The METLIN small molecule dataset for machine learning-based
  retention time prediction.* Nature Communications 2019, 10:5811.
  [link](https://doi.org/10.1038/s41467-019-13680-7)
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
