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
    `peak_assignment = false` under `[meta]` in the environment's config toml and
    restart the stack — that is the whole procedure. With
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

The tiers are the product-facing summary of the confidence layer, and the quantity
underneath them is the **evidence** — fit × plausibility, the same product the candidates
were competed on, rather than the fit alone. A tier therefore reflects both how well the
measured isotope pattern matches *and* how chemically plausible the formula is: a
composition that fits the mass beautifully but describes an unlikely molecule no longer
earns the top tier on the strength of the match. The band a row lands in is read off the
same quantity that won it the peak in the first place, so the tier and the arbitration
cannot disagree.

**The percentage on a tier chip is that combined evidence, not the match quality alone.**
The fit score is unchanged — still recorded, still shown beside the assignment as the pure
measurement — so the two stay visible apart. A *batch peak*'s chip carries no percentage
at all: its consensus tier is a weighted vote over what the batch's samples each concluded
about the peak, not a threshold on any single number.

The long-term goal is to report a community-standard **identification level** ([Schymanski
et al. 2014][sch14]; MSI reporting standards, [Sumner et al. 2007][sum07]) alongside the
confidence, since that is how the field communicates identification certainty.

> **Note.** The fit score is a pure measurement and stays one. Chemistry, spectral context
> and calibration are *layers on top* of it and are never folded back into the score —
> tiering reads the fit and the plausibility together, but the fit score itself is the
> measurement alone, unchanged, which keeps it reproducible while the confidence layers
> evolve. The current tier thresholds are provisional and will be recalibrated per
> instrument; tying a tier to a calibrated probability of being correct is still where this
> is heading, and still waits on calibration coverage across instruments.

## Assigning a peak yourself

--8<-- "_help/assignment-curation.md"

A hand assignment says "this candidate is the better reading of the evidence"; a
verification says "I have evidence of *this grade* that it is right". Keeping the two
apart is what keeps the calibration honest: the labelled record that future confidence
curves are fit on stays a record of stated evidence, not of preferences. It is the same
reason an override drops the engine's calibrated P(correct) instead of carrying it over
&mdash; the curve was fit to score the engine's arbitration, and a probability quoted
beside a formula it never scored would be a number with nothing behind it.

The run-scoped lifetime follows from what a run is: one reading of the sample, computed
from the data at a moment. Editing a row of it corrects that reading; it is not a
standing instruction, so the next run starts from the data again and knows nothing about
it. Verifications are the layer built to outlive a run &mdash; keyed on the peak, formula
and ionization mechanism rather than on a run &mdash; which is why they carry over a
re-assignment and an override does not.

### Curating a species for the whole batch

In a sample served from the batch ledger (its runs list shows *Batch ledger*), the
inspector's close alternatives are the other identities the batch has seen at that
peak, each with the share of the batch's evidence behind it. *Use this* on one of them
acts on the batch peak rather than on the sample: the chosen identity is pinned as the
species for the whole batch, then measured in every sample that holds the peak. A sample
where it can be measured now reads it with a fit of its own; one where it cannot keeps
what it had. The batch peak claims the pinned formula whatever the samples' vote says -
and says so when the two disagree - and a hand icon beside the formula in the *Batch
peaks* ledger marks it. *Release*, in the inspector's note, undoes it: the samples that
were re-measured go back to what they read before, and the batch decides again.

## Verifying assignments

--8<-- "_help/assignment-verification.md"

The evidence levels follow the field's identification-confidence ladder
([Schymanski et al. 2014][sch14]): a reference standard is a Level-1 identification,
and each weaker level is worth correspondingly less as a label. Verdicts deliberately
capture the *evidence* behind a judgment rather than echoing the model's own score,
so the labelled record stays informative for recalibration.

### Batch-level verdicts

--8<-- "_help/batch-peak-verdicts.md"

The *Verdict* column of the *Batch peaks* ledger is where a batch-level verdict is
recorded: click the cell to judge the species, change the verdict or retract it. The
samples it covers show it as a borrowed badge - in parentheses in the assignment
ledger, as a dashed pill in the inspector - and the assignment ledger's verdict filter
counts them under it, so *Unverified* lists only rows that show no badge at all. A
per-sample verdict always wins: verifying one of those samples yourself records an
exception, and where the two disagree the per-sample badge says so.

Confirming or rejecting names the formula you judged. If the consensus has moved since
the ledger was read - another sample's fold can move it - the verdict is refused and the
row reloads, so you never confirm a formula you did not see. A verdict whose formula the
consensus has since left stays on record, outlined as stale, until you judge the new
formula or retract it. Batch-level verdicts are kept apart from the labelled record that
confidence calibration is fit on: one judgment fanned out over a batch would count as
many correlated labels, so it counts as none.

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

A sample whose peaks are in the batch ledger but that has no run of its own &mdash; its
runs were deleted or pruned, or it was folded into the batch without one &mdash; is shown
from the batch ledger instead. The run selector lists it as **Batch ledger**; the
ledger carries what the batch knows about each peak (formula, adduct, tier, fit,
probability and isotopologue family), and the inspector's close alternatives are what
the rest of the batch saw at that m/z. It carries no mass error or isotope label, and
it cannot be edited by hand &mdash; assign the sample for a ledger of its own. Verdicts
can still be recorded against it.

A sample served from the batch ledger (its run reads *Batch ledger*) carries each peak's
fit and tier, but not the numbers a run would have stored beside them: the m/z and
abundance error of each isotopologue, the isotope labels, the chemical plausibility, the
evidence the tier was read off. The peak inspector measures those on demand when you focus
such a peak - the family's composition is scored against the sample's own peaks, through
its M0 - and fills them in a moment later. They are computed for the view and never stored;
run an assignment on the sample to persist a full ledger of its own.

The sample browser marks each sample's assignment status with a tag badge beside the
match and calibration badges: green for a sample with a completed run of its own (the
tooltip names the engine, its version and the time), the accent colour for a sample served
from the batch ledger without a run of its own, faint for one with nothing assigned yet.
The tooltip also says how many of the sample's peaks carry an assignment in the ledger.

## Batch peaks

--8<-- "_help/batch-peaks.md"

Every processed sample folds into the batch peaks as it arrives - assigned from the
known compositions, without a per-sample run of its own - and so does every completed
assignment run. *Rebuild batch ledger* does the same for a whole batch on demand: a
sample with an assignment run folds from it, one without is assigned from the known
compositions and folded without a run (a blank, or a sample whose m/z calibration is
not verified, is skipped). Use it to populate a batch that predates the ledger or was
never assigned, or to refresh after an import. There is no batch-wide assignment run:
the untargeted search runs once per batch peak instead (below), and a species is
curated once at its batch peak rather than sample by sample.

*Search untargeted*, beside *Rebuild batch ledger*, first asks for the search's parameters
- m/z precision, formula ranges, the peak ceiling, the intensity threshold and the number
of alternatives kept, the same settings as a per-sample run's untargeted stage, applied
to the whole search - and then runs the untargeted composition search
for the batch peaks nothing has assigned yet &mdash; once per species, on its brightest
peak in that sample's own spectrum &mdash; and then measures the composition it found
against every other sample the species was seen in, so each carries a fit of its own.
It writes no per-sample runs: the results appear in the batch ledger and in each
sample's view, marked as untargeted. An assignment run on a sample still takes
precedence for that sample.

The whole ledger leaves the app as a CSV from the view menu behind the cog: *Export
ledger (CSV)* writes one row per member peak - every sample's reading of every batch
peak, with the batch peak's consensus beside it - and the browser downloads the file
when it is ready. The same rows are one call away in the SDK
(`mascope.load_batch_ledger(...)`, or `mascope.batch_peaks.members(batch_id)` per
batch), which is the shortest way from a batch's assignment to any other format.

The *Verdict* column, last in the ledger, records and shows a
[batch-level verdict](#batch-level-verdicts) on the species: one judgment that covers
every sample in the batch without a verdict of its own.

The rows you tick in the *Batch peaks* ledger are what the batch chart draws, one
trace per batch peak. The ledger lists every anchor in the batch, which on a large
batch is far more than a chart can usefully show, so a selection is capped at 300 —
select all on a bigger ledger takes the first 300 rows and tells you so. Which 300 is
up to you: filter the ledger first, with the tier chips or the Formula column's
filter, and then select.

One row per species, not per peak: a compound's isotopologue peaks are
folded under its main peak and counted in the **+N** marker beside the formula, the
same way the per-sample assignment ledger folds them. The *Isotopologues* toggle,
behind the cog at the end of the tier-chip row, unfolds them as indented rows
underneath. The link is derived rather than given — a
batch peak is an m/z anchor and carries no compound of its own — so a peak is folded
only when its per-sample assignments agree, across most of the samples that assigned
it, that it belongs to another anchor's compound. One that is an isotopologue in one
sample and a species in its own right in the rest stays a row of its own.

The *Intensity* column is the highest intensity the species reaches in any sample of
the batch, in the instrument's own unit (summed peak heights on an Orbitrap, summed
peak areas on a TOF). It is a property of the trace rather than of the assignment, so
unassigned anchors carry one too — sorting by it is how you find the largest thing in
the batch that nothing was assigned to.

Because a batch peak is one identity for a species across the batch, the focused peak
follows you between samples: pick another sample and the inspector and the spectrum
stay on the same species rather than on nothing. It is the batch peak that decides
what "the same" means here, not the nearest m/z — so a peak follows only where that
species was actually observed. Move to a sample where it was not, and the selection
clears the way it always did. The same is true in a batch whose batch peaks have not
been computed yet: there is no anchor to follow, so nothing does. Picking a peak
yourself always wins over this — it will not overwrite a choice you just made, or
refill a selection you cleared.

To see how an assignment looks in a spectrum, use the arrow beside a row's intensity: it
opens the brightest sample that holds the batch peak, with that peak focused, in the
*Sample* tab - the same click-through as a data point in the batch chart, without having
to tell which trace to click when several are plotted. When an earlier run is on screen,
the jump reads that run's members.

### Batch runs

--8<-- "_help/batch-runs.md"

The run selector beside *Rebuild batch ledger* lists the batch's runs newest first, each
with what it did and, for a search, the parameters it was given. The current run is the
live ledger; picking an earlier one shows the *Batch peaks* ledger and the chart as that
run left them, read-only - verdicts and curation act on the current run, so the Verdict
column waits until you pick it again. A run that fails is kept and marked, and never
becomes current. The same history is one call away in the SDK
(`mascope.batch_peaks.runs(batch_id)`, and `list(batch_id, run_id=...)` for the species
table as an earlier run left it).

### Importing an engine's batch result

An external engine that works on the batch as a whole - one identity per m/z - can
land its result on the batch ledger as a run of its own, through the SDK
(`mascope.batch_peaks.import_run(batch_id, rows, engine=..., engine_version=...)`) or
`POST /api/batch-peaks/batch/{id}/runs/import`. Each row is matched to the batch peak
nearest its m/z (within 5 ppm by default) and its composition is then measured against
every sample that holds that peak, so the ledger shows Mascope's own fit of the engine's
formula, with the engine named as the source. Curated batch peaks are left alone, as are
isotopologue peaks, and rows whose adduct could not be resolved to a mechanism; the run's
summary counts what landed and why the rest did not. The ledger as it was stays under
the previous run in the run selector, so the two views can be compared, and *Rebuild
batch ledger* puts Mascope's own view back.

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
