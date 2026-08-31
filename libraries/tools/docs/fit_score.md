# The Match Fit Score (v2)

*Reference for `mascope_tools.composition.heuristic_filter.score_pattern_v2`
(`SCORE_VERSION = 2`).*

## 1. What it is — and what it is not

The match score answers exactly one question:

> **How well does the observed data fit the predicted spectrum of *this* assignment?**

It is a **fit-quality measurement** on $[0, 1]$ (1.0 = perfect fit), computed per ion
from its isotopologue peaks. It is deliberately:

- **competitor-blind** — it knows nothing about alternative formulas that might explain
  the same peak;
- **not a probability of correctness** — mass alone cannot prove a composition
  ([Kind & Fiehn 2006](https://bmcbioinformatics.biomedcentral.com/articles/10.1186/1471-2105-7-234)),
  so "is this the *right* formula among the ones that fit?" is a separate
  **identification-confidence** layer (a distinct workstream that builds on this score);
- **deterministic and reproducible** — the same spectrum always yields the same score,
  with no dependence on a trained/calibrated model.

This separation is the central design decision: the score is the *pure-math measurement*;
chemistry, instrument context, and candidate arbitration are layered on top and never
folded back into it.

## 1a. Role in the peak-centric pipeline

The fit score is the **scoring engine** of the peak-centric assignment paradigm
([`peak_assignment_paradigm.md`](../../../docs/dev/peak_assignment_paradigm.md)): it ranks
candidate compositions per peak in **Stage A** (known/database compositions) and **Stage B**
(untargeted `find_compositions`). "Find the best-fitting known composition; if none fits well
enough, fall through to untargeted" is exactly *highest fit score in Stage A, gated by a
threshold*. The value persists as the `PeakAssignment` fit column; the confidence **tier**
(assigned / candidate / …) is decided by the separate confidence layer
([`assignment_confidence.md`](../../../docs/dev/assignment_confidence.md)).

**Wired into the engine (landed).** The peak-centric engine
(`api/new/peak_assignments/`) adopts the fit score *deliberately* as its scoring —
unconditionally, not via the legacy `MASCOPE_MATCH_SCORE_VERSION` switch:
- **Stage A** (`engine.score_ions_by_fit`): the per-ion fit score (`ion_score_v2` →
  `score_pattern_v2`) over each ion's full isotopologue envelope with the sample's fitted
  mass accuracy, replacing the targeted matcher's per-isotopologue
  `abundance_term · mz_term`. Run after `apply_match_params`, so tolerance / intensity-floor
  gating carries into the fit. It scores with **real per-peak SNR** because it computes the
  isotopologue rows itself; the paths that *read* them back from the database have no SNR
  and score in the no-SNR mode of §3.3a (see §6).
- **Stage B** (`engine.untargeted_matches_to_peak_assignments`): uses the isotope-pattern
  fit score `assign_compositions` already computes (`match_isotopic_pattern`), i.e. the fit
  score's **v1 degradation** — the untargeted path carries no SNR — instead of the crude
  single-peak term.
- **Tier bands (landed):** the confidence-tier bands no longer sit on the fit scale. They
  sit on **evidence** — fit × chemical plausibility, the product both stages already
  arbitrate a contested peak in — at `assigned_threshold = 0.75` /
  `candidate_threshold = 0.45` on `PeakAssignmentConfig`
  (`api/new/peak_assignments/config.py`). Only the scale moved: the key names and the
  shape of the `tier_bands` a run records are unchanged, and the fit is half of the
  product rather than a casualty of it — it stays the stored pure measurement. The bands
  sit below the legacy `match_params` 0.8/0.7 for the reason they always did (on the fit
  scale a lone mass-only match scores low by design), and they came down further with the
  multiplication, since plausibility ≤ 1 and weighting can only move a row down. How far
  was settled by sweeping the pair over a real ledger of 77,911 tiered rows rather than by
  taste: plausibility is a spike at 1.0 with a thin tail (92.8 % of tiered rows score
  exactly 1.0), so 0.75/0.45 was the pair closest to the split it replaces — assigned
  85.38 % against the 84.08 % fit-tiering at 0.8/0.5 gave, with 6.93 % of rows changing
  tier in both directions. Directional, not calibrated. The upper field also accepts its
  old name `identified_threshold` as a validation alias: the config is built from the
  assign request body, so a client pinned to the pre-rename name still tiers the run the
  way it asked instead of silently falling back to the default. One pair covers both
  stages knowingly — Stage A's fit is `ion_score_v2` and Stage B's the v1 degradation
  above, so a band means slightly different things to each (holding the upper band at 0.80
  would cost Stage B 5.3 % of its assigned rows and Stage A only 0.5 %) — but that
  heterogeneity predates this binding, was equally true under fit-tiering, and per-stage
  bands were deliberately not introduced. Still open: recalibrating those bands per
  instrument once verification labels accumulate.

**Naming.** This is being renamed `match_score` → **`fit_score`** across the schema/API to
say plainly that it measures *fit*, not identification.

**Default (legacy targeted path).** `MASCOPE_MATCH_SCORE_VERSION` defaults to **1** so the
existing targeted match behaviour is unchanged; the fit score (`=2`) is adopted
*deliberately* — as the peak-centric engine's scoring — rather than by silently flipping the
legacy default. It also degrades gracefully: where a lighter aggregation path lacks the
per-isotopologue evidence, scoring falls back to v1.

One path used to escape that switch: the on-demand composition search
(`api/new/cheminfo`) scored every candidate with `ion_score_v2` unconditionally, so an
existing user-facing feature reported fit/tier/plausibility instead of the legacy match
score regardless of `MASCOPE_MATCH_SCORE_VERSION`. It is now gated on the peak-assignment
feature flag — see
[`peak_assignment_paradigm.md`](../../../docs/dev/peak_assignment_paradigm.md) §5.1.

## 2. Scientific rationale

High mass accuracy alone is insufficient to determine elemental composition: even at
<1 ppm the number of formulas within tolerance grows quickly with mass and heteroatom
count, and a measurement at 3 ppm **plus** 2 % isotope-abundance accuracy outperforms a
hypothetical 0.1 ppm instrument with no isotope information
([Kind & Fiehn 2006](https://bmcbioinformatics.biomedcentral.com/articles/10.1186/1471-2105-7-234)).
The fit score therefore scores the **whole isotope pattern**, not just the monoisotopic
mass: each predicted isotopologue contributes mass *and* relative-intensity evidence.

The construction follows the probabilistic isotope-pattern matching of **SIRIUS**
([Böcker et al. 2009](https://academic.oup.com/bioinformatics/article/25/2/218/218950);
[Dührkop et al. 2019](https://www.nature.com/articles/s41592-019-0344-8)), where mass
deviations are modelled as normal distributions and the pattern is scored as a product of
per-peak likelihoods. v2 adapts this to centroided industrial spectra by adding
detection-limit awareness (real signal-to-noise) and resolution-aware mass widths so the
same score is valid for both Orbitrap and TOF instruments.

### Why v2 replaced v1

The legacy score was a fixed linear blend — $0.6\cdot\text{mass} + 0.2\cdot\text{cosine}
+ 0.2\cdot\text{intensity}$ — averaged over *matched* peaks only, with a hard 5 ppm mass
window. It (a) ignored predicted peaks that should have been visible but were not, (b)
judged intensities without reference to noise, and (c) used an instrument-agnostic mass
window. v2 fixes all three. On the demo golden set, ranking ROC-AUC improves 0.876→0.890
and held-out calibration ECE 0.020→0.0069 (see
`tooling/score_eval/DESIGN.md`, which is untracked scratch — it lives in the author's
worktree, not the repo).

## 3. The model

Inputs, per predicted isotopologue $i$ (index $0$ = the monoisotopic / base peak, ordered
by descending predicted abundance):

| symbol | meaning |
|---|---|
| $p_i$ | predicted relative abundance (base-normalised, $p_0 = 1$) |
| $e_i$ | observed mass error in ppm (offset-centred: the fitted $\mu$ is subtracted) |
| $o_i$ | observed intensity ($o_i = 0$ if no peak matched) |
| $s_i$ | observed signal-to-noise of the matched peak |
| $\sigma$ | instrument mass-error std in ppm (`sigma_ppm`) |

**Guard.** If the monoisotopic peak is absent ($o_0 \le 0$) the score is $0$ — without the
base peak there is no assignment.

### 3.1 Mass likelihood (Gaussian, resolution- and SNR-aware)

$$ L^{\text{mass}}_i = \exp\!\left(-\tfrac{1}{2}\left(\frac{e_i}{\sigma_i}\right)^2\right),
\qquad \sigma_i = \sqrt{\sigma^2 + \left(\frac{k}{s_i}\right)^2} $$

A Gaussian in ppm, as in SIRIUS, but with a **per-peak width**. The fixed part $\sigma$ is
the **instrument's measured mass accuracy**, fitted per sample from the matched peaks' mass
errors (robust median/MAD) and combined in quadrature with a small prediction term — this
makes the score **resolution-fair** (a 2 ppm error is near-perfect on a ~10 ppm TOF but poor
on a ~1 ppm Orbitrap). The second part is an **SNR-dependent centroiding term** $k/s_i$: a
weak peak's centroid is *legitimately* less precise, so its mass error should not be judged
against the tight high-SNR width. The demo goldens confirm this cleanly —
$\sigma_{\text{mass}}^2 = \sigma_{\text{floor}}^2 + (k/\text{SNR})^2$ with
$\sigma_{\text{floor}} \approx 0.23$ ppm and $k \approx 2.36$ ppm (`MASS_SNR_K`) — so mass σ
falls from ~0.63 ppm at SNR≈4 to ~0.10 ppm at SNR>1000. High-SNR peaks are unchanged
($k/s_i \to 0$). On the golden set this lifts ranking (top-1 contested 0.706→0.723) and,
especially, the low tail of correct-assignment scores (p10 0.45→0.50) — the trace
isotopologues that were over-penalised — while ROC-AUC and calibrated ECE hold. The fallback
`FALLBACK_SIGMA_PPM = 2.0` is Orbitrap-appropriate and *wrong* for TOF — always pass the
fitted $\sigma$.

### 3.2 Intensity likelihood (noise-propagated tolerance)

Let $r_i = o_i / o_0$ be the observed abundance relative to the base peak. The tolerance on
$r_i$ comes from **propagating counting noise** through the ratio (the relative error of a
quotient is the quadrature sum of the relative errors, and $\delta o / o \approx 1/\text{SNR}$):

$$ \sigma^{\text{rel}}_i = \max\!\left( r_i\sqrt{\tfrac{1}{s_i^2} + \tfrac{1}{s_0^2}},\; 0.05\,p_i,\; 10^{-3}\right), \qquad
L^{\text{int}}_i = \exp\!\left(-\tfrac{1}{2}\left(\frac{r_i - p_i}{\sigma^{\text{rel}}_i}\right)^2\right) $$

So a weak, noisy isotopologue is judged loosely (its ratio is uncertain) while a strong,
clean one is judged tightly — rather than a single global intensity tolerance. The floors
($5\%$ of predicted abundance, and $10^{-3}$) prevent over-confident penalties.

The base peak carries only mass evidence ($L_0 = L^{\text{mass}}_0$, since $r_0 \equiv 1$);
every other **matched** peak contributes $L_i = L^{\text{mass}}_i \cdot L^{\text{int}}_i$.

### 3.3 Detectability gate (censoring at the detection limit)

A predicted peak that is **absent** ($o_i = 0$) is only evidence *against* the assignment
if it should have been seen. Its expected SNR is $p_i \cdot s_0$ (its abundance relative to
the base peak, times the base peak's SNR), so:

- **detectable but absent** ($p_i\, s_0 \ge k_{\text{detect}}$): contributes a fixed
  penalty $L_i = \text{miss\_penalty}$ — the assignment predicts a visible peak that is not
  there;
- **undetectable** ($p_i\, s_0 < k_{\text{detect}}$): **excluded** from the score — its
  absence is expected (below noise) and carries no information.

This is a censored-data treatment: missing low-abundance isotopologues do not punish
genuine low-intensity ions, but a missing $^{81}$Br twin of a bromine ion does. Defaults
$k_{\text{detect}} = 3$, $\text{miss\_penalty} = 0.3$.

### 3.3a No-SNR mode (callers without per-peak signal-to-noise)

SNR is **optional**: every SNR term in the model is a *concession granted on evidence
that a peak is noisy* — it only ever widens a tolerance. A caller with no usable SNR
(`observed_snr=None`, or per-row values that are zero, negative, NaN or infinite — the
DB-read aggregation paths, which read matched isotopes back without noise data) therefore
grants no concession: each row is judged at the fixed instrument width `sigma_ppm` and the
abundance floors, exactly as a clean high-SNR peak is. The one term that cannot fall back
per-row is the detectability gate, whose expected-SNR test needs the **base** peak's SNR;
with it unknown, an absent isotopologue is instead penalised when its predicted abundance
alone says it should have been seen ($p_i \ge$ `REL_DETECT_NO_SNR`, default $0.10$ — the
~1:10 dynamic range any matchable peak demonstrably exceeds).

Because the real-SNR ratio tolerance only exceeds the 5 %-of-abundance floor below
SNR ≈ 20, a normally-measured envelope scores the same in both modes; they diverge only
for genuinely weak peaks, and then conservatively. The residual difference: an absent
isotopologue predicted between $k_{\text{detect}}/s_0$ and `REL_DETECT_NO_SNR` is excluded
rather than penalised in no-SNR mode (max deviation ≈ 0.085; see §6). Note the
`DEFAULT_CALIBRATION_V2` Platt fit was made on the real-SNR path and is **not** calibrated
for this mode.

### 3.4 Satellites

Ringing/satellite artefacts near intense peaks are **not** real matches; the caller flags
them and they are treated as absent (so the detectability gate applies). See
`ion_score_v2` in the backend adapter.

### 3.5 Aggregation (abundance-weighted geometric mean)

The included per-isotopologue likelihoods are combined as a **predicted-abundance-weighted
geometric mean**:

$$ \text{score} = \exp\!\left(\frac{\sum_i w_i \ln L_i}{\sum_i w_i}\right), \qquad w_i = p_i $$

The geometric mean is the natural combination of independent likelihoods (it is
$\exp$ of the mean log-likelihood, i.e. a normalised joint likelihood), and the abundance
weighting means the dominant isotopologues drive the score while trace peaks contribute
proportionally less. The result is in $[0,1]$, equals $1$ only for a flawless fit, and is
**monotone in isotopic corroboration** — more clean, in-pattern peaks → higher score.

## 4. Parameters

| parameter | default | role |
|---|---|---|
| `sigma_ppm` | per-sample fitted (fallback `2.0`) | fixed mass-term width; the instrument-resolution lever |
| `MASS_SNR_K` | `2.36` (ppm) | SNR-dependent mass width: $\sigma_i=\sqrt{\sigma^2+(k/s_i)^2}$; fit on the demo goldens |
| `k_detect` | `3.0` | expected-SNR threshold above which an absent peak is penalised |
| `miss_penalty` | `0.3` | likelihood assigned to a detectable-but-absent peak |
| `rel_detect_no_snr` | `0.10` | no-SNR fallback (§3.3a): predicted abundance above which an absent peak is penalised when the base peak's SNR is unknown |
| `PRED_SIGMA_PPM` | `0.5` (backend adapter) | prediction/centroiding term added to $\sigma$ in quadrature |

## 5. Properties (validated on the demo)

- **Reproducible / instrument-fair:** deterministic; resolution handled by the fitted
  $\sigma$. Live demo fit-quality: median **0.92**, max **1.0**.
- **Monotone in corroboration:** median score rises with the number of clean, in-tolerance
  isotopologues (1 peak ≈ 0.3 → full envelope ≈ 0.95). v1 gave ~0.95 regardless.
- **Correctly demotes weak matches:** 86 % of sub-0.5 ions on the demo have 0–1
  in-tolerance isotopologues — absent/trace assignments that v1 inflated on mass alone.

## 6. Limitations

- **Mass term is unforgiving of individual mass-accuracy outliers (investigated; by
  design).** The mass width $\sigma$ is fitted (robust MAD) from the sample's confident
  matches, whose errors cluster tightly near 0, so it is small (Orbitrap ~0.2–0.3 ppm;
  effective ~0.6 ppm after the `PRED_SIGMA_PPM` quadrature). An ion whose peaks carry
  *genuinely larger* mass errors than the bulk — e.g. the demo's $\mathrm{Br_3^-}$ at
  ~0.7–0.9 ppm (its intensity pattern is flawless) — sits at ~1.3–1.6 $\sigma$ and the
  Gaussian mass term drops to ~0.3–0.7, pulling the fit to ~0.6 — which no plausibility can
  lift back to the assigned band, since evidence only weights the fit downward.
  A golden-set investigation (see the handoff roadmap, B3) showed **no distribution-level
  fix is warranted**: the mass-error core is m/z-flat (~0.13–0.18 ppm), there is **no
  m/z-dependent $\sigma$ growth**, **no m/z-dependent offset** ($\mathrm{Br_3^-}$'s region
  has signed mean ~0), and only a mild high-intensity (space-charge) uptick. A **Student-t**
  mass term (heavier tails) actually scores $\mathrm{Br_3^-}$ *lower*, because its peaks are
  at the *shoulder* (~1.4 $\sigma$), not the deep tail where a t-distribution helps. So
  $\mathrm{Br_3^-}$ is a real **mass-accuracy outlier** and ~0.6 honestly reflects that its
  mass fit is worse than a typical assigned-tier ion; the calibrated confidence (~0.57) conveys
  the same. The one data-supported refinement that came out of the investigation is unrelated
  to $\mathrm{Br_3^-}$ and is now **implemented**: the **SNR-aware mass $\sigma$** of §3.1 —
  weak peaks have ~3× larger errors (0.22 vs 0.07 ppm) and are no longer scored against the
  tight bulk $\sigma$. (It does not lift $\mathrm{Br_3^-}$, whose peaks are high-SNR.)
- **Geometric-mean harshness (rare):** one badly-fitting high-abundance peak can dominate.
  On the demo this affects ~1 % of ions; revisit the aggregation (e.g. a soft floor or a
  robust mean) if it proves material.
- **No-SNR mode is slightly envelope-blinder (§3.3a):** without a base-peak SNR the
  detectability gate falls back to predicted abundance alone, so an absent isotopologue
  predicted between $k_{\text{detect}}/s_0$ and `REL_DETECT_NO_SNR` is excluded rather
  than penalised — a score deviation of at most ≈ 0.085 versus the real-SNR path. The
  Platt calibration is fitted on the real-SNR path, so no-SNR callers report the raw fit
  without a calibrated $P(\text{correct})$.
- **Depends on isotopologue matching completeness:** a *missed* match looks like a missing
  predicted peak, so the score is only as good as the upstream peak matching.
- **Single-ion:** it scores one ion's isotope envelope; cross-peak corroboration (adducts,
  in-source fragments) is the confidence layer's job, not the score's.
- **Not a probability:** pairing with `calibrate_score` (Platt) yields a single-candidate
  $P(\text{correct})$, but that belongs to the identification-confidence layer (a separate
  workstream), not the fit score.

## References

- Kind, T.; Fiehn, O. *Metabolomic database annotations via query of elemental
  compositions: mass accuracy is insufficient even at less than 1 ppm.* **BMC
  Bioinformatics** 2006, 7:234.
  [link](https://bmcbioinformatics.biomedcentral.com/articles/10.1186/1471-2105-7-234)
- Kind, T.; Fiehn, O. *Seven Golden Rules for heuristic filtering of molecular formulas
  obtained by accurate mass spectrometry.* **BMC Bioinformatics** 2007, 8:105.
  [link](https://bmcbioinformatics.biomedcentral.com/articles/10.1186/1471-2105-8-105)
- Böcker, S.; Letzel, M. C.; Lipták, Z.; Pervukhin, A. *SIRIUS: decomposing isotope
  patterns for metabolite identification.* **Bioinformatics** 2009, 25(2):218–224.
  [link](https://academic.oup.com/bioinformatics/article/25/2/218/218950)
- Dührkop, K. et al. *SIRIUS 4: a rapid tool for turning tandem mass spectra into
  metabolite structure information.* **Nature Methods** 2019, 16:299–302.
  [link](https://www.nature.com/articles/s41592-019-0344-8)
