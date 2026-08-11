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

A joint mathematical scoring routine assigns each candidate a **fit-quality score** bounded
between 0.0 and 1.0. This score measures *how well the observed data fit the candidate's
predicted spectrum* — it is a reproducible measurement, **not** a probability of correctness
(mass alone cannot prove a composition), and deciding *which* of several well-fitting
candidates is the real one is a separate identification-confidence layer (a distinct
workstream that builds on this score).

This pipeline scores with **v1** (`score_pattern`): a fixed
$0.6\,\text{mass} + 0.2\,\text{cosine} + 0.2\,\text{intensity}$ blend over matched peaks
only, carried on each candidate as `isotopic_pattern_score`. The same module also ships the
newer **v2** fit score (`score_pattern_v2`), fully documented in
[`fit_score.md`](fit_score.md). In brief, each predicted isotopologue contributes a Gaussian
mass likelihood (with a resolution-aware, per-sample-fitted width, valid for both Orbitrap
and TOF) times an intensity likelihood whose tolerance is set by the peak's own
signal-to-noise; a predicted peak that is *absent but should have been detectable* is
penalised, while an undetectable one is excluded; and the per-isotopologue likelihoods are
combined as a predicted-abundance-weighted geometric mean. Nothing in this pipeline selects
between the two — the module constant `SCORE_VERSION` records the newest version shipped,
but no code branches on it. v2 is called directly by the consumers that want the fit score:
Mascope's peak-assignment engine (Stage A, `score_ions_by_fit`) and the backend's
`ion_score_v2` adapter behind `MASCOPE_MATCH_SCORE_VERSION=2`.

The highest-scoring formula candidate is assigned as the primary chemical identification, and its corresponding higher isotopes are flagged and linked within the final output data tables to complete the processing sequence.
