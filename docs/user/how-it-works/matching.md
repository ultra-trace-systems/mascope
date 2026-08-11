# Target Isotope Matching

> **This page describes the legacy *targeted* match** (do these known compounds appear in
> the sample?), which remains the default for the target-matching workflow. The peak-first
> question — assign a composition and a confidence to *every* peak — and the consolidated
> **fit score** that scores it are described in
> [Peak Assignment & Identification Confidence](peak-assignment.md). The isotope-level
> scoring below is being superseded by that fit score (see
> [the "Fit score" note](#a-note-on-scoring-the-fit-score) at the end of this page).

--8<-- "_help/matching.md"

The [isotope matching](https://github.com/ultra-trace-systems/mascope/blob/master/libraries/match/src/mascope_match/compute/isotopes.py) workflow establishes high-confidence links between a predefined list of target compositions and detected experimental peaks in sample mass spectra.

The procedure should be distinguished from unconstrained [composition assignment scoring](https://github.com/ultra-trace-systems/mascope/blob/master/libraries/tools/src/mascope_tools/composition/finder.py) (which screens peaks against wide combinatorial formula spaces).
Instead, this matching engine operates exclusively on a fixed set of target compounds, their designated ionization mechanisms, and their predictable isotopic envelopes.

## Data Pre-Filtering and Peak Extraction
Before the algorithm matches targets to experimental peaks, the input datasets undergo instrument-specific filtering to ensure that the theoretical models match the physical limitations of the hardware detector.

### Resolution-Driven Isotope Selection
Target isotope masses and intensities are computed and grouped dynamically depending on the instrument configuration to account for peak merging at different resolving powers.

For high resolution (Orbitrap) instruments, the algorithm loads the complete fine-structure target array, utilizing a default abundance threshold of 0.00001 to retain trace isotopes.

For low resolution (Time-of-Flight) instruments, lower resolving powers cause closely spacing fine-structure lines to merge into a single combined peak profile.
To mirror this hardware limitation, theoretical isotopes are passed through a binning routine with a resolution constant of 10,000.
For each local window, the total intensity is calculated as the sum of the binned sub-isotopes, and the mass coordinate is mapped to the weighted center-of-mass of the cluster.

### Instrument-Specific Intensity Quantification
The matching process employs different intensity metrics depending on the instrument type to account for their distinct detector characteristics.

**Orbitrap instruments** use **peak height** for signal intensity quantification.
High-resolution profile spectra allow precise apex intensity determination from the Gaussian centroiding algorithm, making peak height a reliable measure of ion abundance.

**Time-of-Flight (TOF) instruments** use **peak area** for signal intensity quantification.
Since TOF peaks are resolved through multi-peak fitting rather than Gaussian centroiding, the integrated peak area provides a more robust measure of ion abundance that is less sensitive to peak shape variations and baseline uncertainties.

### Mass Window Slicing and Extraction
Experimental peak timeseries data are loaded within a localized mass tolerance window of 0.5 Da around the target theoretical mass-to-charge ($m/z$) values.
If a specific scan polarity is defined, the matching engine filters out opposite-polarity peaks.
The retrieved raw timeseries are collapsed along the time dimension to calculate a mean intensity profile, retaining only peaks with positive intensity values.

## Isotope-Peak Assignment and Selection Constraints
The core alignment mechanism operates at the individual isotope level and employs a competitive prioritization strategy governed by a strict hierarchy of constraints.

### Candidate Sorting and Prioritization
For each target isotope, a list of candidate experimental peaks within the 0.5 Da window is assembled.
These candidates are sorted primarily by absolute mass difference relative to the target, and secondarily by ascending mass-to-charge value.
The matching algorithm iterates through the target isotopes, prioritizing those with higher theoretical relative abundances and smaller $m/z$ values.

### Ion-Level Structural Rules
During assignment, several constraints prevent unphysical peak pairings.
Experimental peaks must be entirely unique within a single target ion - multiple isotopes belonging to the same ion are prohibited from sharing the same sample peak.
Different distinct target ions are permitted to independently match against and share the same experimental sample peaks.
When multiple isotopes within the same ion compete for an identical peak, the isotope with the higher theoretical relative abundance takes precedence.
The sequence of assigned experimental peak mass-to-charge coordinates must strictly mirror the sequential order of the theoretical target isotope masses.
An experimental peak will be skipped if its assignment would cause an inversion of the mass order within that specific ion.

If an isotope fails to find an experimental peak that satisfies all constraints, it is flagged as unmatched.

## Multi-Level Match Scoring Architecture

Once assignments are completed, the pipeline executes a [bottom-up hierarchical scoring sequence](https://github.com/ultra-trace-systems/mascope/blob/master/server/backend/src/mascope_backend/api/controllers/match/lib/match_aggregate.py) from individual isotopes up to the global sample context, generating metrics bounded strictly between 0.0 and 1.0.

### Target Isotope Match Score
For unmatched isotopes, the match score is defined as 0.0.
For each successfully paired isotope, the match score is calculated based on its localized mass accuracy and relative abundance accuracy:

$$\text{matchScore}_{\text{isotope}} = (1 - \min(1, \text{|abundanceError|})) \times \max\left(0, 1 - \frac{|\text{mzError}_{\text{ppm}}|}{100}\right)$$

The abundance error represents the intensity deviation relative to the principal (most abundant) peak of the ion cluster:

$$ \text{abundanceError} = \frac{\text{relativeIntensity}_{\text{measured}}}{\text{relativeAbundance}_{\text{theoretical}}} - 1.0 $$

For the main monoisotopic peak, this error evaluates by definition to 0.0.

The parts-per-million mass deviation is calculated as:

$$\text{mzError}_{\text{ppm}} = \frac{m/z_{\text{measured}} - m/z_{\text{theoretical}}}{m/z_{\text{theoretical}}} \times 10^6$$

An error of $\ge 100\text{ ppm}$ automatically forces the mass penalty term to 0.0, resulting in an isotope match score of 0.0.
As the mass deviation decreases toward zero, the score linearly approaches its abundance-bounded maximum.

### Target Ion Match Score
The match score for a target ion (a molecular configuration under a specific ionization mechanism) is computed as the weighted sum of its constituent isotope scores, using the theoretical abundances as weights:

$$\text{matchScore}_{\text{ion}} = \sum_{\text{isotope}} \left( \text{theoreticalAbundance}_{\text{isotope}} \times \text{matchScore}_{\text{isotope}} \right)$$

This weighting scheme ensures that the maximum possible score for an ion equals the sum of the relative abundances of its successfully matched isotopes times their respective match scores, making the score highly sensitive to the presence of the primary isotopic lines.

### Target Compound Match Score
Because a single target compound may generate multiple valid ionization types (such as various adducts or protonated states), the overall compound score is determined by selecting the highest score among its derivative ions:

$$\text{matchScore}_{\text{compound}} = \max\left(\text{matchScore}_{\text{ion}}\right)$$

### Target Collection and Sample Scoring
Moving up to the highest organizational layers, the score for a targeted collection of compounds is defined by its best-performing compound match:

$$\text{matchScore}_{\text{collection}} = \max\left(\text{matchScore}_{\text{compound}}\right)$$

Finally, the global match score assigned to an experimental sample item is established by the maximum match score among all target collections linked to it:

$$\text{matchScore}_{\text{sample}} = \max\left(\text{matchScore}_{\text{collection}}\right)$$

## A note on scoring: the fit score

The per-isotopologue score above (a fixed mass-penalty × abundance-penalty, summed over the
envelope) is the **legacy v1** scoring. It has three known weaknesses: it ignores predicted
isotopologues that should have been visible but were not, it judges intensities without
reference to noise, and its mass penalty uses a fixed window rather than the instrument's
measured accuracy.

These are all addressed by the consolidated **fit score** (`score_pattern_v2`): a Gaussian
mass likelihood whose width is the *fitted* per-sample mass accuracy (so it is fair on both
Orbitrap and TOF), an intensity likelihood scaled by each peak's signal-to-noise, a
detectability gate that only penalises an absent peak when it should have been seen, and an
abundance-weighted geometric-mean aggregation. On the reference dataset this improves both
ranking (ROC-AUC) and calibration over v1. The fit score is the scoring engine of
[peak assignment](peak-assignment.md); its full model, parameters and literature references
are in the developer reference, `libraries/tools/docs/fit_score.md`. The legacy v1
score is retained for the targeted path and documented above for reference.
