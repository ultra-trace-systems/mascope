# Molecular Composition Assignment and Heuristic Filtering
The assignment of molecular compositions to experimental mass spectrometry peaks is a challenging combinatorial problem that requires highly constrained computational optimization to manage the rapid expansion of chemical search spaces.
The architecture detailed here couples a bounded, recursive tree-traversal routine with an aggressive filtering pipeline to map raw mass-to-charge ratios ($m/z$) to unique elemental formulae.
By dynamically computing allowable atomic boundaries, pruning unfeasible elemental combinations early, and verifying surviving candidates against simulated isotope distributions, the workflow systematically isolates high-confidence chemical compositions while discarding mathematically valid but physically impossible configurations.

## Core Combinatorial Optimization Algorithm
[The primary composition discovery engine](../src/mascope_tools/composition/finder.py) uses a depth-first, recursive tree-search strategy to systematically explore the multi-dimensional space of potential element counts.
Rather than executing an exhaustive brute-force search over all permutations, the algorithm relies on dynamic mass-domain pruning to eliminate non-viable computational branches before they are fully evaluated.

### Precomputation and Search Space Initialization
Prior to executing the recursive search, the chosen list of allowable elements is sorted in descending order of atomic mass.
The algorithm then precomputes two essential suffix arrays tracking the cumulative absolute minimum and maximum mass contributions that could be provided by all remaining elements downstream of any given index.

For each individual ionization pathway under evaluation, the target experimental mass-to-charge value is adjusted by adding or subtracting the specific carrier mass (e.g., protons or electron mass shifts) to isolate the required target neutral monoisotopic mass.
A search window is then defined around this target using a user-specified parts-per-million ($\text{ppm}$) mass tolerance.

### Recursive Branch Pruning Strategy
As the algorithm traverses down the element tree, it determines the feasible bounds for the current element count by evaluating the physical constraints.
The minimum allowable count for the current element is bounded by its predefined minimum range or by a value derived from subtracting the maximum remaining downstream mass from the target window threshold.
The maximum count is constrained by the predefined maximum range or by a value derived from subtracting the minimum remaining downstream mass from the upper target window threshold.

At each node, the search space is dynamically pruned using the precomputed suffix values.
If the current accumulated mass plus the absolute minimum remaining downstream mass overshoots the upper boundary of the mass tolerance window, the loop breaks immediately, abandoning that branch and all higher counts for the current element.
If the current accumulated mass plus the maximum possible downstream mass fails to reach the lower boundary of the mass tolerance window, the algorithm skips to the next iteration, as subsequent elements cannot provide enough mass to satisfy the target.

### Leaf-Node Evaluation and Validation
When the recursive search successfully reaches a leaf node (signifying that all allowed elements have been assigned a count), the final aggregate mass of the candidate composition is validated against the target window.
Compositions that fall within the exact mass boundaries are evaluated against double-bond equivalent (unsaturation) filters if enabled.
Candidates whose calculated unsaturation falls outside the accepted minimum and maximum thresholds, or those containing fractional unsaturation values when strictly integer restrictions are applied, are rejected.
Valid formulae are passed to the downstream filtering layers.

## Heuristic and Isotopic Filtering Layers
While the initial generation step limits candidates to broad mass and unsaturation envelopes, the pipeline subjects the remaining formula pool to strict [structural and spectral validation filters](../src/mascope_tools/composition/heuristic_filter.py).
Although the broader framework references [Seven Golden Rules](https://doi.org/10.1186/1471-2105-8-105) of chemical space validation, this processing pipeline enforces automated decisions through vectorized element-ratio verification and multi-parametric isotopic pattern matching.

### Element Ratio Filter
Candidates are passed to a vectorized ratio-checking routine designed to eliminate formulae with unphysical element collections.
The algorithm evaluates specific elemental counts relative to the carbon count if carbon is present.
For specialized structures lacking carbon, an alternative configuration block can be deployed to enforce independent, non-carbon element ratio envelopes.

Formulas that violate any single user-provided element-to-carbon or non-carbon boundary condition are discarded.

### Isotopic Pattern Verification and Multi-Parametric Scoring
Compositions that pass the structural ratio tests undergo an experimental verification step by predicting and matching their expected isotopic profiles against the provided peaks.

For each candidate formula, a theoretical isotopic distribution is calculated.
The engine simulates exact isotopic masses and probabilities, retaining all configurations that meet or exceed a minimum abundance threshold.
The predicted masses are converted to $m/z$ values based on the ionization mechanism's charge and matched against the provided peaks.
A user-defined $\text{ppm}$ search window isolates corresponding experimental features.
Higher isotopic states are only evaluated if their corresponding monoisotopic base peak is successfully matched.
If a matching peak's relative intensity deviation exceeds a defined intensity tolerance threshold, that specific isotope is excluded from matching.

Both errors the matching step reports are **signed relative to the prediction** - the mass
error as $(m/z_\text{obs} - m/z_\text{pred}) / m/z_\text{pred}$ and the intensity error as
$I_\text{rel,obs} / I_\text{rel,pred} - 1$ - which is the convention the targeted matcher
uses for `match_mz_error` and `match_abundance_error`. A consumer can therefore recover the
prediction from an observation and its error, which is how Mascope's peak inspector and
spectrum chart draw the theoretical envelope of an assignment. The tolerance gate, the
candidate ranking, the duplicate resolution and the scores below all use the magnitude, so
the sign carries evidence without ever acting as a penalty.

A joint mathematical scoring routine assigns each candidate a **fit-quality score** bounded
between 0.0 and 1.0. This score measures *how well the observed data fit the candidate's
predicted spectrum* — it is a reproducible measurement, **not** a probability of correctness
(mass alone cannot prove a composition), and deciding *which* of several well-fitting
candidates is the real one is a separate identification-confidence layer (a distinct
workstream that builds on this score).

This pipeline scores with the **v2** fit score (`score_pattern_v2`), fully documented in
[`fit_score.md`](fit_score.md), carried on each candidate as `isotopic_pattern_score`. Each
predicted isotopologue contributes a Gaussian mass likelihood (with a resolution-aware,
per-sample-fitted width, valid for both Orbitrap and TOF) times an intensity likelihood
whose tolerance is set by the peak's own signal-to-noise; a predicted peak that is *absent
but should have been detectable* is penalised, while an undetectable one is excluded; and
the per-isotopologue likelihoods are combined as a predicted-abundance-weighted geometric
mean. What the sample is judged at — the fitted mass width and offset, the window a line
may be matched in, how deep the envelope is predicted — is `PatternScoring`, passed in by
the caller; its defaults reproduce the fixed Orbitrap-shaped constants this pipeline
applied to every sample before a caller could describe one.

Filling its width in is `composition.mass_accuracy`: `fit_mass_accuracy` measures a
sample's own mass errors (median and scaled MAD, refusing to state a width below
`MASS_ACCURACY_MIN_ANCHORS` anchors), and `scoring_sigma_ppm` turns that - or the
instrument class's `resolve_fallback_sigma_ppm` where nothing measured one - into the
width a fit is judged at. It is here rather than in an engine because more than one
engine scores these spectra: two implementations of the fit would make a comparison
between them partly a comparison of how each measured its ruler.

The whole object is derived in one place too, `composition.scoring.pattern_scoring_for_sample`
(`mass_accuracy`, `instrument_type`, `match_params`, `resolution=None`): the width Stage A
fitted or the instrument class's, the offset Stage A fitted or, where it fitted none, the
reagent lines' when they put the axis beyond that width (`SampleMassAccuracy.scoring_mu_ppm`,
with `SampleMassAccuracy` and `ReagentOffset` in `composition.mass_accuracy`), the window
and the floor of the sample's match parameters (any object with `mz_tolerance` and
`isotope_abundance_threshold`), and the file's resolving power. Mascope's engine calls it
for every sample it searches, so an outside engine that reads the sample's numbers through
the SDK and calls it gets the object the engine scored at, rather than a re-derivation that
puts the rules in another order.

`composition.scoring.score_candidates(candidates, peaks, scoring)` is the finder's ranking
measurement for any number of candidates at once: a frame with each candidate's `ion`
(`"C6H5O-"`) and, optionally, the `observed_mass` of the peak it explains, against a peak
list with `mz`, `intensity` and, where there is one, `signal_to_noise`. Each candidate's
envelope is predicted to the depth its own peak's noise allows, every distinct envelope
once, every predicted line is matched to the nearest peak in the window with one
`searchsorted` over the sorted peaks, and all envelopes are scored in one pass
(`heuristic_filter.score_patterns_v2`, the many-envelope form of `score_pattern_v2`). It
returns, on the candidates' index, `fit_score`, `pattern_has_required_lines`,
`pattern_base_snr`, the `envelope_floor`, `matched_lines`, and per predicted line the
`labels`, `predicted_mz`, `predicted_rel`, `observed_mz`, `mass_error_ppm` and
`intensity_error` (zero where nothing matched). A score equals the one-at-a-time rule
(`heuristic_filter.score_ion_envelope`) to the rounding of a sum: under 3e-16 over the
90,490 candidates of `tooling/score_eval`'s pools on the demo dataset, with the same top-1
and AUC. The finder itself scores each peak's readings this way. No
candidate is elected, capped or ranked: that is the finder's policy, not the measurement.

Two lines still have to be present for a reading to be evidence at all: the ion's own, and
the one the prediction leads with. The fit *charges* an absent line rather than refusing on
it, so that requirement is reported separately, as `pattern_has_required_lines` on each
ranked candidate.

The predecessor **v1** (`score_pattern`) still ships: a fixed
$0.6\,\text{mass} + 0.2\,\text{cosine} + 0.2\,\text{intensity}$ blend over the matched
peaks only, which is why an envelope that predicted three lines and found one cost a
candidate nothing under it. Nothing branches on a version — the module constant
`SCORE_VERSION` records the newest one shipped — and v1 is kept for the scoring harness
that measures v2 against it. v2 is also what Mascope's peak-assignment engine scores
Stage A with (`score_ions_by_fit`) and what the backend's `ion_score_v2` adapter computes
behind `MASCOPE_MATCH_SCORE_VERSION=2`.

The highest-scoring formula candidate is assigned as the primary chemical identification, and its corresponding higher isotopes are flagged and linked within the final output data tables to complete the processing sequence.
