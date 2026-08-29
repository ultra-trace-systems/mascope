# Peak Assignment & Identification Confidence

Where [target matching](matching.md) answers *"do these known compounds appear in the
sample?"*, **peak assignment** answers the inverse, peak-first question: *"for every
observed peak, what is the most likely chemical composition, and how confident are we?"*
Each peak gets exactly one assignment per run, together with a **fit score** and a
**confidence tier**.

!!! note "Peak assignment is on by default"

    Targeted matching keeps working exactly as before either way — peak assignment is
    an addition, not a replacement. Target collections, ion tables, the batch overview
    and the Match tab are all unaffected. With it on, a sample is assigned against the
    known target library as it is processed, the assignment views appear, and the
    composition search reports assignment confidence.

    A deployment that would rather not assign at ingest can switch it off: set
    `peak_assignment = false` under `[meta]` in the environment's config toml (or export
    `MASCOPE_PEAK_ASSIGNMENT=0`) and restart the stack — that is the whole procedure. With
    it off nothing is assigned when a sample is processed, the composition search
    reports the familiar match score, the Sample tab keeps its peak ledger, and the API
    refuses to launch assignment runs (the write routes return 403; reads stay open, so
    results from a period when it was on remain visible).

The design rests on a foundational result of the field: **accurate mass alone — even at
sub-ppm — cannot uniquely determine an elemental composition**, and isotope-pattern
information is worth more than another order of magnitude of mass accuracy
([Kind & Fiehn 2006][kf06]). Identification is therefore treated as *accumulating
independent evidence* and *arbitrating between candidates that all fit the mass*.

```
   measurement              evidence layers                    decision
 ┌──────────────┐   ┌────────────────────────────┐   ┌────────────────────────┐
 │  FIT SCORE   │ → │ chemistry (plausibility) ·  │ → │  assignment + a         │
 │ how well the │   │ (spectral context, later)   │   │  confidence + a tier    │
 │ data fit one │   └────────────────────────────┘   └────────────────────────┘
 │  candidate   │
 └──────────────┘
```

--8<-- "_help/assignment-evidence.md"

## The two stages

Every peak is assigned in a two-stage engine:

- **Stage A — database-first.** The peak is matched against the sample's known target
  library (the same target isotopologues used by [target matching](matching.md)); the
  best-fitting known composition wins the peak.
- **Stage B — untargeted.** Peaks that Stage A left unexplained are run through a
  bounded composition search that enumerates every elemental formula whose ion lands
  within the mass tolerance — the classic mass-decomposition problem
  ([Böcker & Lipták 2007][bl07]) — and scores each candidate the same way.

Peaks that neither stage explains are recorded as *unassigned*, so a run is a complete,
queryable ledger: one row per observed peak.

**Single owner, within tolerance.** Each peak has exactly one owner per run, and a peak is
only owned by an isotopologue whose measured m/z is *within tolerance* of the prediction.
A predicted isotopologue that has no real peak is left unmatched rather than being pinned to
a nearby, out-of-tolerance peak — that peak is released to the untargeted stage (or left
unassigned) so it can get its own correct assignment instead of being mislabelled as a
poorly-fitting isotopologue of something else.

## The fit score — a pure measurement

The **fit score** measures exactly one thing: *how well does the observed data fit the
predicted spectrum of this candidate?* It is on `[0, 1]` (1.0 = perfect fit), computed
per ion from its isotopologue peaks, and is deliberately:

- **competitor-blind** — it knows nothing about alternative formulas;
- **not a probability of correctness** — because mass alone cannot prove a composition
  ([Kind & Fiehn 2006][kf06]);
- **deterministic and reproducible** — the same spectrum always yields the same score.

Rather than scoring the monoisotopic mass alone, the fit score scores the **whole isotope
pattern**: each predicted isotopologue contributes a mass likelihood (a Gaussian in ppm,
its width set by the instrument's *measured* mass accuracy — and widened for weak peaks,
whose centroids are legitimately less precise — so the score is fair on both
high-resolution Orbitrap and lower-resolution TOF instruments) and an intensity
likelihood (its tolerance set by the peak's own signal-to-noise). A predicted peak that is
**absent but should have been detectable** counts against the assignment; one that is
below the noise is simply excluded. The per-peak likelihoods are combined as an
abundance-weighted geometric mean. This construction follows the probabilistic
isotope-pattern matching of **SIRIUS** ([Böcker et al. 2009][bo09]; [Dührkop et al.
2019][du19]), adapted to centroided industrial spectra by adding detection-limit
awareness. The full mathematical model is in the developer reference,
`libraries/tools/docs/fit_score.md`.

**A consequence users see:** a lone mass-only match (one peak, no isotopic corroboration)
scores *low* by design, while a fully corroborated isotope envelope scores near 1.0. This
is intentional — mass alone is weak evidence.

## Chemical plausibility — the Seven Golden Rules

Most mass-degenerate formulas are chemically impossible or implausible. Mascope scores
each candidate's **chemical plausibility** on `[0, 1]` from the **Seven Golden Rules**
([Kind & Fiehn 2007][kf07]), combining three referenced factors:

1. **Valence feasibility (Lewis/Senior).** The ring-and-double-bond equivalents must be
   non-negative and the atoms must be able to form a connected molecule; an over-saturated
   formula (more hydrogens than any structure can carry) is driven to zero.
2. **Element-ratio plausibility (Rules 4–5).** The ratios of H, N, O, P, S, halogens to
   carbon are graded against the *common / extended / extreme* ranges the paper derived
   from tens of thousands of real formulas (its Table 2).
3. **Heteroatom co-occurrence (Rule 6).** Simultaneous high counts of N, O, P and S are
   improbable; graded against the paper's multi-element restrictions (its Table 3).

Plausibility is **conservative and fail-open**: it *grades* candidates rather than
hard-rejecting them, and unusual-but-real chemistry (radicals, exotic elements) is never
penalised — only the provably impossible is.

## Arbitration — competing the candidates

For a single peak, the surviving candidates are competed by their combined **evidence =
fit × plausibility**: a candidate must both fit the data *and* be chemically sensible.
Mascope reports, per candidate, a **confidence** (its evidence share among the peak's
candidates) and is **honest about ties** — when two candidates are genuinely
indistinguishable it says so rather than inventing a winner. On the reference dataset,
folding chemistry into the ranking this way resolves markedly more of the hard,
mass-degenerate cases than the fit score alone, because it demotes the spectrally-plausible
but chemically-implausible decoys the fit score is (by design) blind to.

This is the "which of the well-fitting compositions is most likely" problem — the core of
identification. Reliability at scale is estimated with **target–decoy** methods, the
established approach for large-scale MS annotation ([Scheubert et al. 2017][sch17]).

## Calibrated confidence (probability of being correct)

--8<-- "_help/assignment-p-correct.md"

The evidence score ranks assignments, but a raw evidence of 0.85 is not "85% likely
correct" — the calibration that closes that gap is **Platt scaling** ([Platt
1999][platt]), a logistic curve `P = sigmoid(a·evidence + b)` fit on assignments
whose truth is known.

In practice:

- **It is per instrument.** The same raw evidence means different things on an Orbitrap
  (sub-ppm, high resolution) than on a lower-resolution TOF, so each instrument class has
  its own curve. The label data comes from **confident identifications** — most strongly,
  compounds confirmed by a **reference standard** (a Level‑1 identification,
  [Schymanski et al. 2014][sch14]) — versus near-mass decoys. This is why calibration is
  tied to your **reference dataset**, and why you can, in principle, **calibrate your own
  instrument** by running known standards.
- **An uncalibrated assignment shows the raw evidence** in place of a probability. Today
  one **provisional** Orbitrap curve ships (fit on a preliminary reference set); it will
  be replaced by a curated fit, and TOF is uncalibrated until a TOF reference set exists.
- **The adduct lift is measured, not assumed.** A real compound rarely appears as a single
  ion — it also shows up through other adducts (e.g. `[M+H]⁺` alongside `[M+NH₄]⁺`, or
  `[M−H]⁻` alongside `[M+Br]⁻`), and each adduct's corroborating worth is *measured*: a
  chemically distinctive adduct like bromide corroborates strongly, while a generic one
  (ammonium, protonation) barely moves it. The lift is bounded, and — like the calibration
  curve — the per-adduct weights are specific to your instrument's reagent chemistry.

## Confidence tiers

--8<-- "_help/assignment-tiers.md"

The tiers are the product-facing summary of the confidence layer; the underlying score is
the continuous fit quality. The long-term goal is to report a community-standard
**identification level** ([Schymanski et al. 2014][sch14]; MSI reporting standards,
[Sumner et al. 2007][sum07]) alongside the confidence, since that is how the field
communicates identification certainty.

> **Note.** The fit score is the headline number and a pure measurement. Chemistry,
> spectral context and calibration are *layers on top* of it and are never folded back into
> the score — this keeps the measurement reproducible while the confidence layers evolve.
> The current tier thresholds are provisional and will be recalibrated per instrument.

## Verifying assignments

--8<-- "_help/assignment-verification.md"

The evidence levels follow the field's identification-confidence ladder
([Schymanski et al. 2014][sch14]): a reference standard is a Level-1 identification,
and each weaker level is worth correspondingly less as a label. Verdicts deliberately
capture the *evidence* behind a judgment rather than echoing the model's own score,
so the labelled record stays informative for recalibration.

## Assignment runs

--8<-- "_help/assignment-runs.md"

Publishing a run from another engine is what makes the two comparable on the same
sample: both live in the same run history, so selecting one and then the other
switches the ledger between them peak for peak. What an imported run may assert
stops short of what Mascope presents as its own judgement &mdash; it declares the
tier bands it used and every row is checked against them, it discloses what it
calibrated against, and the calibrated P(correct) column stays empty on its rows.
The in-app engine's name is reserved, so the chip cannot be forged. Verifications
recorded against an imported run are kept and shown, but stay out of the
instrument-wide confidence calibration, whose labels come only from runs this
server computed.

## Batch peaks

--8<-- "_help/batch-peaks.md"

A whole batch can be assigned in one launch: the same engine runs over every eligible
sample with one shared configuration (blank samples and samples whose m/z calibration
is not verified are skipped, and the run continues in the background). The untargeted
stage is off by default for batches — its cost multiplies by the number of samples.
Every completed assignment run folds into the batch peaks automatically; a batch whose
runs predate the batch overview can be backfilled from them without any new
assignment work.

The rows you tick in the *Batch peaks* ledger are what the batch chart draws, one
trace per batch peak. The ledger lists every anchor in the batch, which on a large
batch is far more than a chart can usefully show, so a selection is capped at 300 —
select all on a bigger ledger takes the first 300 rows and tells you so. Which 300 is
up to you: filter the ledger first, with the tier chips or the Formula column's
filter, and then select.

One row per species, not per peak: a compound's isotopologue satellite peaks are
folded under its main peak and counted in the **+N** marker beside the formula, the
same way the per-sample assignment ledger folds them. The *Isotopologues* toggle
unfolds them as indented rows underneath. The link is derived rather than given — a
batch peak is an m/z anchor and carries no compound of its own — so a peak is folded
only when its per-sample assignments agree, across most of the samples that assigned
it, that it belongs to another anchor's compound. One that is a satellite in one
sample and a species in its own right in the rest stays a row of its own.

The *Intensity* column is the highest intensity the species reaches in any sample of
the batch, in the instrument's own unit (summed peak heights on an Orbitrap, summed
peak areas on a TOF). It is a property of the trace rather than of the assignment, so
unassigned anchors carry one too — sorting by it is how you find the largest thing in
the batch that nothing was assigned to.

## References

- <a id="kf06"></a>Kind, T.; Fiehn, O. *Metabolomic database annotations via query of
  elemental compositions: mass accuracy is insufficient even at less than 1 ppm.* BMC
  Bioinformatics 2006, 7:234.
  [link](https://bmcbioinformatics.biomedcentral.com/articles/10.1186/1471-2105-7-234)
- <a id="kf07"></a>Kind, T.; Fiehn, O. *Seven Golden Rules for heuristic filtering of
  molecular formulas obtained by accurate mass spectrometry.* BMC Bioinformatics 2007,
  8:105. [link](https://bmcbioinformatics.biomedcentral.com/articles/10.1186/1471-2105-8-105)
  ([open access](https://pmc.ncbi.nlm.nih.gov/articles/PMC1851972/))
- <a id="bl07"></a>Böcker, S.; Lipták, Z. *A fast and simple algorithm for the money
  changing problem.* Algorithmica 2007, 48(4):413–432.
  [link](https://doi.org/10.1007/s00453-007-0162-8)
- <a id="bo09"></a>Böcker, S.; Letzel, M. C.; Lipták, Z.; Pervukhin, A. *SIRIUS:
  decomposing isotope patterns for metabolite identification.* Bioinformatics 2009,
  25(2):218–224.
  [link](https://academic.oup.com/bioinformatics/article/25/2/218/218950)
- <a id="du19"></a>Dührkop, K. et al. *SIRIUS 4: a rapid tool for turning tandem mass
  spectra into metabolite structure information.* Nature Methods 2019, 16:299–302.
  [link](https://www.nature.com/articles/s41592-019-0344-8)
- <a id="sch17"></a>Scheubert, K. et al. *Significance estimation for large-scale
  metabolomics annotations by spectral matching.* Nature Communications 2017, 8:1494.
  [link](https://www.nature.com/articles/s41467-017-01318-5)
- <a id="platt"></a>Platt, J. *Probabilistic outputs for support vector machines and
  comparisons to regularized likelihood methods.* Advances in Large Margin Classifiers,
  1999. [link](https://en.wikipedia.org/wiki/Platt_scaling)
- <a id="sch14"></a>Schymanski, E. L. et al. *Identifying small molecules via high
  resolution mass spectrometry: communicating confidence.* Environ. Sci. Technol. 2014,
  48(4):2097–2098. [link](https://pubs.acs.org/doi/10.1021/es5002105)
- <a id="sum07"></a>Sumner, L. W. et al. *Proposed minimum reporting standards for chemical
  analysis (Metabolomics Standards Initiative).* Metabolomics 2007, 3:211–221.
  [link](https://doi.org/10.1007/s11306-007-0082-2)

[kf06]: #kf06
[kf07]: #kf07
[bl07]: #bl07
[bo09]: #bo09
[du19]: #du19
[sch17]: #sch17
[sch14]: #sch14
[sum07]: #sum07
[platt]: #platt
