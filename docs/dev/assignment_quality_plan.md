`ULTRA TRACE MASCOPE - DESIGN DOC - ASSIGNMENT QUALITY PLAN`

# Closing the assignment quality gap: the work, step by step

## Status

Epic branch `epic/assignment-quality`, draft PR #2077 into `develop`;
step PRs land on the epic and are named here as they merge.

| step | PR | state |
|---|---|---|
| 0 - gate, epic branch, design commit | #2077 | done: testbed, comparison tool, baselines A-F, epic branch |
| 1.1 - assignment profiles (library presets, resolution, stamping) | #2078 | measured on the fixed engine: G3 met, mass-error target met on all four Orbitrap sets, C and D move furthest |
| 1.1 fix - finder: deprotonation charge and labelled reagent mass | #2079 | merged: found by the 1.1 gate run; sets C-F re-run with 1.1 on top of it |
| 1.1 fix - finder: the labelled reagent's atom in ion formulas | #2080 | merged: the label reaching the ion string is what pyteomics could not parse |
| 1.2 - opportunistic adduct channels | #2081 | measured: fingerprint gate works and refuses sodium; carbonate settled on the broad-window nitrate set |
| 1.3 - same-ion tie policy in the finder | #2082 | in progress (handed over 2026-09-07) |
| 1.4 - reagent-cluster pre-pass | - | planned |
| 1.5 - satellite claim and ringing artifacts | - | planned |
| 1.6 - cap and mass window | - | planned |
| 1.7 - stage 1 gate, engine 0.4.0 | - | planned |
| 2.1 - v2 fit for Stage B | - | planned |
| 2.2 - self-calibrated mass gate | - | planned |
| 2.3 - cross-channel corroboration and the reagent-N rule | - | planned |
| 2.4 - mechanical tiers with reasons | - | planned |
| 2.5a - reference seed: peak-list adapter, lift peaky's lists and families (seed proposal phases 0-1) | - | planned |
| 2.5b - Stage A window per source, radical switch, deactivate (seed proposal phase 3) | - | planned |
| 2.6 - frontend: profile, reasons, roles | - | planned |
| 2.7 - stage 2 gate, engine 0.5.0 | - | planned |
| 3.1 - series detection on the batch ledger | - | planned |
| 3.2 - time-series coherence | - | planned |
| 3.3 - calibration from verdicts, per profile | - | planned |
| 3.4 - gate automation | - | planned |
| 3.5 - profiles as versioned rows, routing by detection | - | planned |

## Purpose

The in-app assignment engine was compared with peaky on the same samples,
peak by peak, on two Orbitrap instruments (the testbed protocol and the
comparison tool are in `tooling/assignment_compare/`). The gap has five
measured causes, ranked by the peaks they move:

1. **The neutral/adduct split is decided by enumeration order and two
   channels are missing.** 43% of the peaks both engines commit to are the
   same ion read as a different neutral/adduct pair; Mascope keeps the
   `[M+H]+` reading in 378 of 384 cases because the finder enumerates it
   first. `[M+NH4]+` and `[M+Na]+` are not searched at all.
2. **The tier does not follow the evidence.** The Stage B fit (v1
   `score_pattern`) is 0.96-0.98 in every intensity band and every
   disagreement class, so 94% of committed peaks clear the "assigned" band
   while the brightest peaks (reagent clusters) come out "candidate" or
   "below assignability". 73% (instrument A) and 57% (instrument B) of the
   "assigned" rows are not confirmed by peaky.
3. **An unconstrained candidate space** (`C0-100 H0-100 O0-100 N0-100` at
   10 ppm, no S/Si/Na/P, no context): 13-15% of committed formulas carry five
   or more nitrogens, siloxane contaminants become N11 formulas in every
   sample, 59 formulas contain no carbon, 29Si satellites become compounds.
4. **The 300-peak cap** leaves 22% of peaky's Assigned peaks unsearched on
   instrument A and 78% on the denser instrument B.
5. **No reagent, artifact or satellite handling**: 82% of the signal is
   reagent chemistry that is either fitted with a formula or tiered low.

Peaky's advantage is its first pass (1,115 of 1,192 main peaks): a bounded,
profile-driven grid, self-calibration, arbitration with an adduct policy, and
mechanical tiers. The exotic passes contribute 77 peaks. This plan harvests
the first pass in three stages and leaves the exotic passes for the data to
justify.

### Relationship to the existing designs

- [chemistry_profiles.md](chemistry_profiles.md) (two profile axes, library
  presets seeded as DB rows, the reagent pre-pass, reference-list tagging,
  batch series) is adopted as the shape of stages 1 and 3. This plan
  re-sequences it by measured impact and makes two changes: presets ship in
  the library and are resolved from the ionization mode first, with the
  versioned DB rows and their settings UI last (step 3.5), because
  editability is not what the stakeholders judged; and its harvest map's
  "do not port tiers, degeneracy, self-calibration gates" is reopened -
  the judgement layer is the second-largest gap - but it is reimplemented on
  Mascope's own arbitration seam, not ported as peaky's frame code.
- [assignment_confidence.md](assignment_confidence.md): stage 2 is layers L2
  (spectral neighbourhood), L3 (reagent priors) and L5 (arbitration with
  honest verdicts) of that architecture; the fit score stays pure throughout.
- [verification_calibration_loop.md](verification_calibration_loop.md): step
  3.3 feeds it Stage B rows and profile keys.
- [peak_assignment_batch_primary.md](peak_assignment_batch_primary.md): stage
  3 runs on the batch ledger's anchors and propagation machinery.
- The reference-database seed proposal ("Atmospheric CIMS Reference Seed",
  2026-09-07): its plumbing, lift and engine phases are step 2.5 of this
  plan; its literature sweep runs beside stages 2 and 3 as its own track.
  It establishes that Stage A matches only formulas inside a hard-wired
  window (C/H/N/O/S, C <= 40, 700 Da), which is why the families peaky
  hard-codes - iodine, fluorine, silicon, phosphorus, chlorine and bromine
  species - never assign today.

## How the work runs

- **Branch and PRs.** Epic branch `epic/assignment-quality` off `develop`,
  one PR per step, self-merged by rebase when CI is green; the epic is
  reviewed into `develop` per stage. Each stage ends with an engine version
  bump (`PEAK_ASSIGNMENT_ENGINE_VERSION` 0.4.0, 0.5.0, 0.6.0) because each
  changes results; steps inside a stage do not bump.
- **Steps in parallel.** Two steps may be in flight at once when their
  footprints are disjoint, and the footprint is what decides, not the step
  number. Stage 1's steps all end in two places - the finder's ranking
  (`assign_compositions`, `match_isotopic_pattern`) and the engine's Stage B
  conversion (`untargeted_matches_to_peak_assignments`) - so a step that
  touches both is split: the library half first, as its own PR with tests
  in `libraries/tools/tests`, and the engine half after the step it would
  collide with has merged, rebased onto it. The status table of this note
  is edited by every step and conflicts trivially; CHANGELOG merges by
  union. The testbed is one deployed build, and the comparison reads the
  latest completed run of each engine per sample, so deploy, gate re-run
  and comparison are one critical section per step, done at the end of its
  PR, one branch at a time, with the build tag named in the PR comment.
- **The gate.** Every step ends by re-running the in-app engine on the fixed
  twelve-sample testbed set (six per instrument, peaky's runs already in the
  store) and running `compare_runs.py`; the numbers go into the status table
  of this note. The metrics and their stage targets are in the table at the
  end. A step that moves a metric the wrong way is discussed before merge,
  not merged on green CI alone.
- **The gate set.** Decision 6 widens it from the two uronium batches to
  every chemistry and instrument class the product is judged on, each as a
  representative sample set (five time-spaced samples plus the max-TIC one
  of a batch) with peaky's run published beside the in-app run:

  | set | instrument | chemistry | samples | status |
  |---|---|---|---|---|
  | A | Orbitrap, sparse spectra | urea CIMS, positive | 6 | measured |
  | B | Orbitrap, dense spectra | urea CIMS, positive | 6 | measured |
  | C | Orbitrap A | 15N-nitrate CIMS, negative, acquired from m/z 131 | 5 | measured |
  | C2 | Orbitrap A | 15N-nitrate CIMS, negative, the same chemistry acquired from m/z 50 | 6 | measured |
  | D | Orbitrap A | bromide CIMS, negative (the demo dataset's source batch) | 6 | measured |
  | E | TOF, single acquisition set | bromide CIMS, negative | 3 | measured |
  | F | TOF, multi-scheme source | bromide and nitrate CIMS, negative, one day each | 6 + 5 | measured |

  Sets D to F need peaky reference runs with TOF-appropriate windows where
  the instrument is a TOF (its Orbitrap defaults of 1 ppm trust and 3 ppm
  search are meaningless at 10 ppm accuracy), and the in-app engine's TOF
  window default from step 1.1. The demo dataset carries set D's chemistry
  in public form, which is what step 3.4 can run in CI. A stage gate is
  judged on the whole set; a chemistry that regresses blocks the stage even
  when the pooled number improves.
- **Regression guards.** With the identity profile (`none`) and today's
  config a run must reproduce today's ledger content for content (ids and
  timestamps excluded); the scoring goldens in `tooling/score_eval` do not
  move, because no step touches the fit score's arithmetic; the four CI
  suites stay green. "Today's" means the engine as fixed, not as shipped: the
  deprotonation-charge fix of #2079 legitimately changes every `-H+` row's ion
  string and mass error, so the identity comparison is against an engine
  carrying that fix, and a guard written before it is re-pinned rather than
  defended.
- **Tests.** Pure logic lands in `mascope_tools` with tests in
  `libraries/tools/tests`; engine logic in `server/backend/tests/unit/api/peak_assignments`
  and the integration suite there; frontend in Vitest. Backend suites run on
  the LAN host.

## Stage 1 - search the right space (engine 0.4.0)

Config and policy. No schema change. Cheap, and it removes causes 1, 3, 4
and 5 as far as they are search problems.

### 1.1 Assignment profiles: library presets, resolution, stamping

- **What.** A `mascope_tools.composition.profiles` module: the
  `ReagentProfile` and `ChemistryContext` dataclasses of the profiles design
  and its presets (`BR`, `UR`, `NO3`, `NO3_15N`, `IODIDE`, `ESI_POS`,
  `ESI_NEG`; contexts including `uronium`, `ambient-air`, `none`), ported
  from peaky's `chem/profiles.py` and `chem/contexts.py` with their tests.
  A backend `peak_assignments/profiles.py` resolves a sample's profile from
  its ionization mode's mechanism notations (the fingerprint: the urea
  adduct means `UR`, `+Br-` means `BR`, `+NO3-`/`+^NO3-` the nitrate pair,
  `+I-` iodide, otherwise the ESI preset of the polarity).
  `PeakAssignmentConfig` gains `profile` and `context` names (default
  `auto`), and the resolved content is snapshotted into `run.config`.
- **Where.** `service._run_sample_assignment` builds
  `CompositionSearchConfig` from the profile: `element_count_ranges` is the
  profile grid intersected with the context's element caps (carbon at least
  1 for the organic grid), `mass_range_ppm` the profile's instrument-class
  window (3 ppm Orbitrap, 20 ppm TOF, overridable). The context's ratio
  windows - `h_to_c`, `o_to_c`, `n_to_c` and `dbe_to_c` together - ride on
  `HeuristicFilterConfig` as one new rule, `rule_context_ratios`, rather than
  through `carbon_element_ratio_range`: that rule reads raw element pairs and
  would reject urea (H/C 4.0) and a trihalogenated acid (H/C 0.5) on ratios
  neither violates, where the context windows are peaky's - effective counts
  (Si with carbon, halogens with hydrogen) above a three-carbon floor.
  `batch_untargeted.search_config` takes the same profile.
  peaky's per-context grid box (`grid_c_max`/`grid_o_max`) is deliberately not
  ported: in Mascope the grid belongs to the reagent profile and a context may
  only narrow it, so no context can silently widen a grid the reagent
  chemistry bounded.
- **Why.** Cause 3. The offline experiment on one sample took N >= 5
  formulas from 17% to 8% with the grid alone and to 10% at 3 ppm with
  no cap.
- **Verify.** Unit tests for resolution, grids and windows; the identity
  profile reproduces today's config; gate metric G3 (N >= 5 share, carbon-free
  count).
- **Size.** M. Depends on nothing.

### 1.2 Opportunistic adduct channels

- **What.** The profile's adduct panel lists the secondary channels a source
  can produce: `+NH4+` for the urea and ESI presets, `+CO3-` and `+Br2-` for
  bromide, `+Na+` and `+K+` in the ESI presets only. A secondary channel is
  switched on per sample by its **fingerprint**, not by its presence in the
  deployment's mechanism table: the reagent-cluster library of step 1.4
  looks for the channel's own cluster ions (urea-NH4+ for ammonium, urea-Na+
  and Na(H2O)n+ for sodium, the carbonate and dibromide clusters for the
  halide channels) and enables the channel only when they are present above
  a floor. An enabled secondary channel gets peaky's minor-channel
  treatment: a ranking penalty against the primary channels, and a commit
  only with corroboration until stage 2's tiers take that over. The run
  config records which channels were on and why. Stage A is unchanged
  (targets carry their own ions).
- **Where.** `service._untargeted_ionization_notations` and
  `fetch_sample_mechanisms`; a resolver like peaky's `resolve_mechanism_ids`;
  the fingerprint from step 1.4's library.
- **Why.** Cause 1, second half: 1,648 `[M+NH4]+` main peaks on instrument
  B that Mascope can only read as heavier N-free neutrals. Ammonium is a
  real channel there: the urea-NH4+ cluster is present in every spectrum,
  80% of peaky's ammonium readings are corroborated by a second channel and
  80% are peaky-Assigned. Sodium is the counter-example that motivates the
  fingerprint rule: no urea-Na+ or Na(H2O)n+ cluster is present in the
  uronium spectra (one trace at 0.008% of the base peak in one sample), and
  peaky's 317 `[M+Na]+` readings on instrument B are 81% Candidate, 11%
  corroborated and dim (median intensity rank 1,859) - a channel that
  absorbs unexplained mass rather than reads the source's chemistry.
- **Verify.** Gate metric G2 same-ion recovery on peaky's Assigned ammonium
  peaks; the sodium channel stays off on every urea set because its
  fingerprint is absent; the run config names the enabled channels.
- **Size.** S-M. Depends on 1.1 (the panel lives in the profile) and 1.4
  (the fingerprint comes from the cluster library).

### 1.3 Same-ion tie policy in the finder

- **What.** Candidates that form the same ion under different neutral/adduct
  pairs are one hypothesis family. In `heuristic_filter.match_isotopic_pattern`
  and `finder.assign_compositions` the family is ranked by policy - the
  reading whose mechanism contributes the most mass (the adduct or cluster
  reading) wins, the covalent reading is kept as a structured alternative
  flagged `same_ion` - and ties between genuinely different ions break on
  score, then |ppm|, then plausibility, then formula, never on enumeration
  order. `engine.untargeted_matches_to_peak_assignments` stores the family
  members as alternatives with the flag so the inspector can show the
  ambiguity.
- **Why.** Cause 1, first half: 384 of 892 overlapping peaks on instrument A,
  416 of 1,394 on B.
- **Verify.** `libraries/tools/tests/test_finder.py` cases for the family
  ranking and the deterministic tie-break; gate metric G2 same-formula
  agreement (from 47% to at least 85% on A). Stage A still wins a peak for a
  curated target, so a curated `[M+H]+` reading is unaffected.
- **With 1.2.** The two steps meet on the ammonium peaks and must not
  contradict each other there. The family rule decides the *reading* and
  lives in the finder: X.[M+NH4]+ beats (X+NH3).[M+H]+ because the mechanism
  carries the mass. The minor-channel rule of 1.2 decides the *tier* of a
  reading on a secondary channel and lives in the engine: a winner on
  `+NH4+` commits as assigned only with corroboration, and on equal
  evidence a primary-channel isotope child beats a secondary-channel M0 for
  the same observed peak. Neither rule re-ranks the other's decision. The
  library half of this step (the family ranking, the deterministic
  tie-break, the family carried on the finder's result rows) is independent
  of 1.2 and lands first; the engine half (the `same_ion` flag on the
  stored alternatives) edits the function 1.2 is editing and lands after
  1.2 has merged, rebased onto it.
- **Size.** S-M. Library half independent of 1.1 and 1.2; engine half after 1.2.

### 1.4 Reagent-cluster pre-pass

- **What.** Port peaky's `reagents.build_library` (pure) to
  `mascope_tools.composition.reagents`: the profile's cluster grammar
  enumerates the reagent ions with exact masses and isotopologues - for
  `UR` the protonated urea clusters `(CH4N2O)n H+`, their ammonium and
  water adducts; for `BR` the `Brn-` ladder, hydrates and HBr clusters;
  the nitrate and iodide ladders likewise. Before Stage A the library is
  matched against the peak list within the instrument window; hits are
  written as `role = reagent`, locked, with their isotopologue satellites
  claimed as reagent children (intensity-gated through IsoSpec envelopes),
  and excluded from both stages' candidate peaks. Bare clusters and
  hydrates only: organic-acid adducts of the reagent are analyte channels
  and stay out, which is the lesson peaky learned.
- **Where.** `mascope_tools.composition.reagents` already exists from step
  1.2, holding the channel probes (the narrow half of this library: enough
  exact masses to say whether a carrier is in the spectrum); this step grows
  that module into the full cluster grammar with isotopologues and hydrates
  rather than starting a second one. A `reagent_pass.py` beside `engine.py`; `engine.py` gains
  `ROLE_REAGENT` and `ROLE_ARTIFACT`; `schemas.AssignmentSource` gains
  `reagent` (the read model and the import path already accept the roles);
  `batch_peaks.ROLE_CODES` must carry both roles into the fold.
- **Why.** Cause 5: 82% of the signal, the top-ten peaks of every sample.
- **Verify.** Unit tests on the library masses; on the testbed every peak
  peaky labels reagent must be a reagent row (G4) and no confident analyte
  may be claimed (checked against the agreed rows).
- **Size.** M. Depends on 1.1.

### 1.5 Satellite claim and ringing artifacts

- **What.** Stage B receives the whole peak list as pattern context and the
  searched set as `targets` (the finder already supports this split), so an
  M0's satellites are claimed wherever they sit, and a peak that sits one
  13C or 29Si spacing above a brighter committed peak with a consistent
  intensity ratio is claimed before it is enumerated. FT sidelobes come from
  the existing `mascope_tools.alignment.utils.flag_satellite_peaks` and get
  `role = artifact`, excluded from the search.
- **Where.** `service._run_sample_assignment` (the `assign_compositions`
  call), `engine.untargeted_matches_to_peak_assignments`.
- **Why.** 96 of Mascope's main peaks on instrument A are peaks peaky
  attributes as isotopologues; the artifact role is unused.
- **Verify.** Gate metric G6 (main peaks on peaky isotopologues, at most 10
  on A); artifact rows present where the flag fires.
- **Size.** S-M. Depends on 1.1 (windows) and 1.4 (role constants).

### 1.6 Cap and mass window

- **What.** `max_untargeted_peaks` defaults to every peak (the 5,000
  ceiling stays as the hard bound and a run above it says so);
  `mz_precision_ppm` defaults from the profile's instrument class. If a
  dense sample under the bounded grid still costs more than a minute,
  enumerate the neutral grid once per sample and bisect per peak, as
  peaky's `candidates_for_peaks` does, instead of a recursive search per
  peak.
- **Why.** Cause 4. Under peaky's grid the full 435-peak search took 13 s
  offline; the recursion per peak, not the peak count, is what the wide
  grid made exponential.
- **Verify.** Gate metric G5 (peaky Assigned peaks never searched, from 190
  and 4,181 to 0); runtime per sample recorded in the status table.
- **Size.** S. Depends on 1.1.

### 1.7 Stage 1 gate, engine 0.4.0

- Run the twelve-sample protocol, fill the status table, bump the engine
  version, changelog entry, and rebuild the stakeholder view (the brightest
  peaks of one sample, both readings) from the new runs. Expected: G1 near
  the 45-50% the offline experiment reached, G3-G6 at target.
- **Size.** S.

## Stage 2 - earn the tier (engine 0.5.0)

The confidence layer. This is where "assigned" starts meaning something.

### 2.1 The v2 fit for Stage B

- **What.** After the finder picks each peak's winner and family, the
  winners' `(formula, mechanism)` seeds are re-scored through
  `seeded_scoring.score_seeds` - one `compute_match_isotopes` pass per
  sample, the SNR-aware v2 fit with the same gating Stage A uses - and that
  fit is what evidence and tier are read from. The finder's v1 score stays
  in provenance for audit.
- **Why.** Cause 2: the v1 fit cannot rank, and Stage A and Stage B evidence
  are on different scales (noted in `config.py`). It is also what makes a
  TOF assignable at all: v1 scales its mass term by a fixed 5 ppm, v2 by the
  sample's fitted mass width.
- **Sibling task.** A TOF-capable reference: peaky's local scorer scoring
  through `score_pattern_v2` with the sample's fitted sigma (a small change
  in peaky, beside the `PEAKY_MATCH_PPM` window it already gained), so the
  TOF gate sets get a reference run worth comparing against.
- **Verify.** Fit distributions per verdict class separate; the goldens in
  `tooling/score_eval` are untouched; the config comment about stage
  heterogeneity is retired; on gate set E both engines commit more than the
  reagent ions.
- **Size.** M. Depends on stage 1.

### 2.2 Self-calibrated mass gate

- **What.** Per sample, `fit_sample_mass_accuracy` over the corroborated
  commits (curated targets and winners with a confirmed isotopologue) gives
  the run's `(mu, sigma)`; every committed row records `mass_z`. An
  uncorroborated row beyond 3 sigma is capped at `candidate` with the reason
  `off_calibration`, beyond 6 sigma it is `below_assignability`; the formula
  stays on the row.
- **Why.** A quarter of the assignments peaky refuses on instrument A sit
  more than 1 ppm off on an instrument at 0.2-0.3 ppm. Simulated on the
  served ledger, a 3 sigma gate keeps 96.5% of the agreed rows and drops 26%
  of the contested ones.
- **Verify.** Gate metric G7 (uncorroborated commits beyond 3 sigma, to 0);
  agreed rows kept above 95%.
- **Size.** S-M. Depends on 2.1.

### 2.3 Cross-channel corroboration and the reagent-N rule

- **What.** Within a sample, committed winners are grouped by neutral across
  mechanisms; a neutral seen in two or more channels is corroborated and
  says so in provenance (the mechanical form of Stage A's adduct
  corroboration, which stays calibrated where the D6 store has weights). The
  reagent-N rule: a winner via an N-donating adduct (`+NH4+`, the urea
  adduct) whose same-ion alternative is an N-richer neutral via `+H+` is
  `ambiguous_nitrogen` unless an N-free channel or the ammonium-plus-urea
  pair fixes the count; without that it is capped at `candidate`.
- **Why.** 92% of peaky's Assigned tier on one sample rests on exactly this
  evidence; Mascope has it in Stage A only.
- **Verify.** Agreement on peaky's Assigned rows conditioned on
  corroboration; gate metric G1.
- **Size.** M. Depends on 1.2 and 1.3.

### 2.4 Mechanical tiers with reasons

- **What.** A `tiering.py` consumed by both stages. Per committed row it
  reads: evidence (v2 fit times plausibility, the existing bands remain the
  floor), candidate density in the calibrated window (distinct plausible
  formulas within `arbitration.DEFAULT_TIE_TOL` of the winner, from the
  finder's candidate list through `arbitrate_candidates`), cross-family
  degeneracy (a pure port of peaky's `degeneracy.measure_degeneracy` into
  `mascope_tools`: how many plausible ions of any element family sit in the
  window), corroboration flags (isotopologue confirmed under the v2
  detectability gate, second channel, later a series anchor), `mass_z`,
  and plausibility demotes (carbon clusters with DBE/C >= 1 and no fluorine,
  oxygen lattices with O/C > 1.3 on a saturated mass, carbon-free formulas
  off the allowlist). Rules only demote; every row carries
  `provenance.tier_reasons`, and the run records the rule version.
- **Why.** Cause 2: the tier must degrade with evidence, and it must be able
  to say why.
- **Verify.** Gate metric G1 to 20% or below on both instruments; every
  committed row has at least one reason; the decoy harness
  (`tooling/score_eval`) confirms the demotes do not lower contested top-1.
- **Size.** L (three PRs: the pure measurements in `mascope_tools`, the
  backend tiering, the reasons in the inspector). Depends on 2.1-2.3.

### 2.5 The reference seed and the Stage A window

This step is the reference-database seed proposal, adopted as a track that
runs beside the engine work: its phases 0, 1 and 3 sit on the assignment
path, its literature sweep (phase 2) continues through stages 2 and 3 with
its own status.

- **2.5a What.** The `peaklist` adapter in `mascope_reference`, so peaky's
  peak-list JSON becomes the single authoring format (neutral formula,
  radical flag, detection ion, context tags, references, provenance); a
  `mascope reference seed` command, the demo hook and an integrity test over
  every list file. Then the lift: peaky's Keller 2008 contaminant list and
  Kang 2022 HOM list, the seven families hard-coded in peaky's pass-0
  directors (Br-CIMS inorganics, reactive iodine, nitroaromatics, PFCAs,
  silanediols and siloxanes, organophosphate and thiophosphate esters,
  ammonia) and the example atmospheric list - about 1,000 rows with no new
  literature work. Detection ion and context tags travel in `xrefs` now and
  become `reference_source.tags` activation per the profiles design.
- **2.5b What.** The Stage A known window (`iter_known_compositions`:
  C/H/N/O/S, C <= 40, 700 Da) becomes per source, bounded by the context's
  `known_window`, so a curated list brings I, F, Si, P, Cl and Br into Stage
  A while a PubChem mirror stays bounded; a radical switch on the known set,
  default off; a `reference deactivate` command. Verify ion generation
  handles Si and P.
- **Why.** The siloxane and phosphate peaks are the brightest wrong answers
  in every sample, and the families peaky hard-codes are exactly the ones no
  formula grid reaches: known-formula matching is the only way they get
  assigned. A seed is a prior, not a label set - Stage A wins the peak -
  which is why decision 3 (tiers and the mass gate apply to Stage A rows)
  is part of this step's safety.
- **Verify.** The integrity test; the D4/D5 siloxane peaks and triethyl
  phosphate resolve on every testbed sample; when a seed loads, the gate
  reports the peaks that change owner between the database and untargeted
  stages, because a seed formula stealing a peak from a better untargeted
  answer is the main risk; gate metric G3.
- **Size.** M for 2.5a, S-M for 2.5b. Independent of 2.1-2.4. The seed
  proposal's four decisions (sweep order nitrate, bromide, urea and
  ammonium; opt-in production loading; radicals and clusters off by
  default; widen the window together with the seed) are taken there and
  assumed here.

### 2.6 Frontend: profile, reasons, roles

- **What.** `PeakAssignConfigForm.vue` gains the profile and context
  selectors (auto by default, resolved name shown); the run provenance chip
  shows the profile; the inspector shows `tier_reasons`, `mass_z` and the
  same-ion alternatives; the ledger renders `reagent` and `artifact` roles
  with their own chips and excludes them from the analyte counts.
- **Verify.** Vitest on the form and the inspector rows.
- **Size.** M. Depends on 1.1, 1.4, 2.4.

### 2.7 Stage 2 gate, engine 0.5.0

- Protocol run, status table, version bump, changelog. Expected: G1 at or
  below 20%, G7 at 0, the top-24 view free of reagent peaks in low tiers.
  This is the point at which the feature can be re-presented.

## Stage 3 - use the batch (engine 0.6.0)

Corroboration that only a batch can give, on the batch ledger.

### 3.1 Series detection on the batch ledger

- **What.** Port `series_gka.py` units and `series_detect.py`'s
  decoy-controlled detection (pure) to `mascope_tools.composition.series`.
  A batch operation, "Detect series", walks from Assigned anchors along
  CH2, O, H2O, CO, CO2, C2H2O and the profile's contaminant units (C2H6OSi,
  CF2), proposes members at exact mass among unassigned or candidate
  anchors, scores them per sample through the batch untargeted propagation
  chain, and commits only families that beat the decoy offsets (re-derive
  the 12-link and 3x enrichment thresholds on our data). A member carries
  `has_anchor`, which 2.4's tiers count as corroboration.
- **Where.** `batch_untargeted.py`'s propagation and consensus recompute; a
  route and compute-bar entry beside "Search untargeted".
- **Verify.** FDR on the decoys per batch; series-derived rows agree with
  peaky's `residual:series` rows where both exist.
- **Size.** L. Depends on stage 2.

### 3.2 Time-series coherence

- **What.** For a neutral seen in two channels, the channels' batch time
  series (the records series endpoint) must correlate; r >= 0.6 corroborates,
  anti-correlation demotes to candidate with a reason. A channel holding a
  constant ratio to a bright parent across the batch is a sidelobe and
  becomes `artifact`.
- **Verify.** On the two 400-600-sample testbed batches; compare with
  peaky's pass-7 skips.
- **Size.** M. Depends on 2.3 and 2.4.

### 3.3 Calibration from verdicts, per profile

- **What.** `assignment_calibration` records the reagent profile (the
  profiles design's first calibration step); `recalibrate_instrument`
  admits Stage B rows now that their evidence is on the v2 scale; a review
  routine on the testbed verifies the top disagreements between the engines
  (`tier_disagrees`) so labels accumulate; the corroboration weights are
  refit per profile.
- **Verify.** Before and after ECE from the recalibration route; the
  provisional gate behaves.
- **Size.** M. Depends on 2.1 and the verification UI (shipped).

### 3.4 Gate automation

- **What.** `compare_runs.py --gate thresholds.json` fails when a metric
  regresses past its target; a scheduled run on the testbed re-runs the
  engine on the fixed sample set after each epic merge and posts the table.
- **Size.** S.

### 3.5 Profiles as versioned rows, routing by detection

- **What.** The profiles design's DB half: `reagent_profile` and
  `chemistry_context` tables seeded from the library, `ionization_mode
  .reagent_profile_id`, the settings surface, and the fingerprint-based
  `resolve_ionization_modes_by_peaks` from the setup-simplification
  proposal. Last because it changes who can edit chemistry, not what the
  engine concludes.
- **Size.** L. Decision D5.

## Metrics and targets

### Baselines (2026-09-07)

Both engines on every gate set, from the store with `compare_runs.py`.
"Own Assigned" is peaky's own tier; the same-formula share is of the peaks
both engines commit to.

| set | peaks | Mascope M0 (assigned tier) | peaky M0 (own Assigned) | both M0: same formula | G1 assigned rows unconfirmed | G2 peaky Assigned recovered, same formula | N >= 5, Mascope / peaky | mass error MAD, Mascope / peaky | peaky reagent peaks |
|---|---|---|---|---|---|---|---|---|---|
| A uronium, Orbitrap sparse | 2,626 | 1,631 (1,535) | 1,192 (949) | 416 of 892 (47%) | 73% | 39% | 13% / 0% | 0.20 / 0.17 ppm | 58 |
| B uronium, Orbitrap dense | 12,055 | 1,631 (1,587) | 7,614 (5,372) | 685 of 1,394 (49%) | 57% | 12% | 15% / 0.1% | 0.20 / 0.29 ppm | 24 |
| C 15N-nitrate, Orbitrap A | 1,583 | 1,078 (787) | 753 (527) | 92 of 568 (16%) | 99% | 18% | 17% / 0% | 1.13 / 0.14 ppm | 29 |
| D bromide, Orbitrap A | 5,217 | 947 (818) | 2,108 (1,595) | 281 of 657 (43%) | 68% | 17% | 31% / 0% | 0.37 / 0.26 ppm | 262 |
| E bromide, TOF | 3,493 | 409 (206) | 119 (28) | 3 of 29 | 99% | 7% | 22% / 0% | 1.56 / 1.16 ppm | 336 |
| F1 bromide, multi-scheme TOF | 13,595 | 1,449 (968) | 698 (100) | 18 of 126 | 98% | 18% | 26% / 0% | 0.94 / 1.01 ppm | 23 |
| F2 nitrate, multi-scheme TOF | 8,905 | 1,452 (1,195) | 755 (46) | 22 of 190 | 99% | 44% | 38% / 0% | 0.73 / 0.92 ppm | - |
| C2 15N-nitrate, Orbitrap A, from m/z 50 (joined at step 1.2) | 1,420 | 779 (667) | 429 (287) | 201 of 372 (54%) | 72% | 64% | 24% / 0% | 0.55 / 0.21 ppm | 33 |

Set C2 joined the gate at step 1.2, so its baseline was measured then, under
the identity profile (`none`: 10 ppm, `C0-100 H0-100 O0-100 N0-100`, the
300-peak cap) on the engine carrying the finder fixes. It is therefore not
confounded by the deprotonation-charge bug the way set C's row is, which is
why its same-formula share starts at 54% rather than 16%; the wide grid's
signature is otherwise the same, a quarter of the committed formulas with
five or more nitrogens and 90 carbon-free ones. Its step 1.2 numbers are in
the table for that step.

Sets E and F are a finding of their own: on a TOF the reference fails as
well, and on the better-calibrated multi-scheme TOF (mass error 0.7-1.0 ppm
MAD) peaky still calls only 100 of 698 and 46 of 755 main peaks Assigned. Peaky
commits 119 main peaks in 3,493 and calls 28 of them Assigned even with its
windows opened to 8 and 25 ppm, because `score_pattern` (v1) scales its
mass term by a fixed 5 ppm, so the 5-15 ppm errors of a TOF score near zero
in both engines; the reagent ions on this TOF also sit 10 ppm off, an
uncorrected offset neither engine estimates. For TOF sets G1 and G2 are
recorded but do not gate; the TOF gate uses the intrinsic metrics (reagent
peaks labelled, chemistry sanity, mass-error spread against the instrument's
own sigma, coverage of the brightest 300 peaks) until step 2.1 gives Stage B
an instrument-scaled fit, and step 2.1 gains a sibling task: a TOF-capable
reference run, peaky scoring through `score_pattern_v2` with the sample's
fitted sigma, so the TOF sets get a reference at all.

### After step 1.1, assignment profiles (2026-09-07)

Every gate sample re-assigned with the default config on a build carrying step
1.1 and the two finder fixes it uncovered (#2079, #2080); peaky's runs are the
ones already published. Same columns as the baselines above, so the two tables
subtract.

| set | peaks | Mascope M0 (assigned tier) | both M0: same formula | G1 assigned rows unconfirmed | G2 peaky Assigned recovered, same formula | N >= 5, Mascope | carbon-free, Mascope | mass error MAD, Mascope |
|---|---|---|---|---|---|---|---|---|
| A uronium, Orbitrap sparse | 2,626 | 1,561 (1,510) | 450 of 890 (51%) | 71% | 41% | 1.4% | 6 | 0.19 ppm |
| B uronium, Orbitrap dense | 12,055 | 1,593 (1,561) | 743 of 1,385 (54%) | 53% | 13% | 3.9% | 0 | 0.19 ppm |
| C 15N-nitrate, Orbitrap A | 1,583 | 1,069 (1,046) | 458 of 681 (67%) | 57% | 86% | 0% | 0 | 0.17 ppm |
| D bromide, Orbitrap A | 5,217 | 834 (783) | 588 of 687 (86%) | 26% | 36% | 0% | 12 | 0.24 ppm |
| E bromide, TOF | 3,493 | 226 (91) | 3 of 20 | 99% | 7% | 0% | 6 | 2.14 ppm |
| F1 bromide, multi-scheme TOF | 13,595 | 978 (468) | 15 of 85 | 97% | 14% | 0% | 41 | 1.75 ppm |
| F2 nitrate, multi-scheme TOF | 8,905 | 1,160 (725) | 28 of 162 | 97% | 44% | 0% | 45 | 1.44 ppm |

Every sample resolved the profile its mechanisms imply, with no configuration:
A and B `UR`/uronium, C `NO3_15N`/ambient-air, D, E and F1 `BR`/ambient-air,
F2 `NO3`/ambient-air. A sample takes about 16 s to assign under the bounded
grid at 3 ppm, against minutes under `C0-100 H0-100 O0-100 N0-100` at 10.

**G3 is met, which is what this step was for.** Formulas with five or more
nitrogens fall from 13-38% to 0-3.9%, and every carbon-free formula left is a
Stage A row from the curated set - HNO3, H2SO4, HBr, Br2, HIO3, NH3, water -
which is precisely the allowlist the target names; the untargeted stage
produces none on any set, because the organic grid floors carbon at 1. The B
set's 3.9% is above the <= 1% target and is the uronium context's own N cap of
5 showing through: the remaining rows are N5, not N8-N11.

**The mass-error target is met on every Orbitrap set, for the first time**:
0.19, 0.19, 0.17 and 0.24 ppm MAD against a <= 0.35 target, with medians
inside 0.15 ppm of zero. Set C's 1.13 -> 0.17 is the deprotonation-charge fix
(#2079) landing: its committed rows were never 1 ppm off, they were scored
against a cation mass.

**The negative-mode sets move furthest**, which is the same fix seen from the
agreement side. Set C: same-formula agreement 16 -> 67%, G1 99 -> 57%, G2
18 -> 86%. Set D: same-formula 43 -> 86%, G1 68 -> 26%, G2 17 -> 36%. Set C
therefore already clears the stage-1 G2 target (>= 80% same formula) and set D
the stage-1 G1 target (<= 45%). The positive-mode sets, which the fix does not
touch, move on the grid alone: A 47 -> 51% and G1 73 -> 71%, B 49 -> 54% and
57 -> 53%. G2 on B stays low because the 300-peak cap leaves most of a dense
spectrum unsearched until step 1.6.

**The one number still below its baseline is the TOF sets' committed mass
error** (E 1.56 -> 2.14, F1 0.94 -> 1.75, F2 0.73 -> 1.44 ppm MAD), and it is
not the window: those sets run at the same 10 ppm they always did. An A/B on
one TOF sample under the identity profile and the resolved one shows both
channels widening together, 139 rows at 1.0-1.4 ppm MAD becoming 72 rows at
2.0-2.3, with the medians near zero either way.

The reading is that on a spectrum whose real accuracy is 5-15 ppm, an
unbounded grid can nearly always find *some* formula within a ppm, so a low
MAD there measures the density of the grid rather than the quality of the
answer. Set C is the proof: it showed the same "MAD got worse" pattern, and
once the charge bug behind it was fixed the bounded grid's MAD came out seven
times *better* than the wide grid's. What the TOF sets show alongside the wider
spread is their nitrogen-heavy share going from 22-38% to zero - the rows the
grid removed are the implausible ones. Their gate stays the intrinsic metrics
until step 2.1 gives Stage B an instrument-scaled fit, which is also what would
let the window widen beyond 10 ppm; it was tried at 20 here and made all three
sets worse, so it stays at 10.

Unchanged by this step, as designed: G4 (no reagent role yet, step 1.4), G5
(the 300-peak cap stands until step 1.6), G6 (satellite claiming is step 1.5).

### After step 1.2, opportunistic adduct channels (2026-09-07)

Same protocol, on a build carrying steps 1.1 and 1.2. Only a set whose profile
declares a secondary channel can move; set C cannot (see below).

| set | channel switched on, and on what | rows it won | Mascope M0 (assigned) | both M0: same formula | G1 | G2 same formula |
|---|---|---|---|---|---|---|
| A uronium | `+NH4+`, on `[(CH4N2O)+NH4]+` at 0.14% of base | 124 | 1,575 (1,426) | 465 of 898 (52%) | 68% | 43% |
| B uronium | `+NH4+`, on `[(CH4N2O)2+NH4]+` at 0.17% | 112 | 1,599 (1,467) | 747 of 1,391 (54%) | 50% | 13% |
| C 15N-nitrate, from m/z 131 | `+CO3-`, unobservable and defaulted on | 130 | 1,079 (948) | 465 of 687 (68%) | 54% | 80% |
| C2 15N-nitrate, from m/z 50 | `+CO3-`, on `[CO3]-` at 0.53% | 42 | 651 (556) | 234 of 354 (66%) | 62% | 69% |
| D bromide | `+Br2-`, on `[Br2]-` at 1.4% | 17 | 797 (759) | 587 of 672 (87%) | 24% | 36% |
| E bromide TOF | `+CO3-` at 2.5%, `+Br2-` at 1.1% | 19 | 235 (87) | 3 of 21 | 99% | 7% |
| F1 bromide TOF | `+CO3-` at 0.09% | 89 | 983 (430) | 15 of 85 | 97% | 14% |
| F2 nitrate TOF | `+CO3-` at 0.011% | 62 | 1,172 (695) | 27 of 163 | 97% | 41% |

Every fingerprint decision is on the run: which channels were considered, which
were found, on which cluster ion, how far off its mass and at what fraction of
the base peak.

**The fingerprint rule works, and it refuses what the measurement said to
refuse.** Sodium is not on the urea profile's panel, and the same spectra show
why: neither `[urea+Na]+` nor `[urea2+Na]+` nor a solvated sodium ion is
present anywhere on set A. Every channel that did switch on was found on a real
cluster ion of its own carrier.

**G1 improves wherever a channel opened** - 73 -> 68% (A), 57 -> 50% (B),
68 -> 24% (D) against the pre-profile baseline - and the assigned-tier count
falls on those same sets, which is the corroboration rule doing what it is for:
of A's 124 ammonium rows, 25 commit as assigned and 99 are held at candidate
because neither a confirmed isotopologue nor the same neutral on a declared
channel backs them. The ledger says "assigned" less often and is wrong less
often when it does.

**The reference's ammonium peaks are recovered with the same formula for the
first time**: 0 -> 14 of the 107 the reference calls Assigned on set A, 0 -> 12
of 1,320 on set B. Both are small against the note's expectation, for two
reasons that are not this step's to fix. Of the ammonium peaks this step
claims, a third are the same ion read as a different neutral/adduct pair, which
step 1.3's family rule decides and this step deliberately does not; and on set B
we commit on 169 of those 1,320 at all, because the 300-peak cap leaves the
rest of a 12,055-peak spectrum unsearched until step 1.6.

**Two things about the fingerprint that only the data could say.**

- *The probe window is not the search window.* A cluster ion's mass is known
  and uncontested, so a tight window buys nothing but missing it - and it does:
  on set B the reagent's own ladder sits +4.7, +6.6 and +27.5 ppm out, because
  that acquisition's low-mass end is calibrated against the analytes rather
  than against ions this bright. A 3 ppm probe finds none of the three. The
  probe window is 20 ppm and the observed error is recorded with every hit.
- *The monomer cluster carries the evidence on a sparse spectrum.*
  `[urea+NH4]+` is the same ion as `[NH3+(urea)H]+`, ambient ammonia through
  its urea adduct, so the reagent library of step 1.4 must leave it alone - but
  a probe claims no peak, and on set A it is the only ammonium cluster in the
  spectrum at all.

**The narrow nitrate acquisition cannot fingerprint carbonate, and that turned
out to be a fact about the window rather than the source.** Set C acquires from
m/z 131 and carbonate is at 60. Set C2 is the same chemistry acquired from
m/z 50, pulled to settle the question: every sample there carries `[CO3]-` at
0.5-1.0% of the base peak, `[HCO3]-` at 0.1%, and the labelled acid clusters
`[CO3+H(15N)O3]-` and `[HCO3+H(15N)O3]-` at 0.1-0.44% - and *nothing above
m/z 126*, no carbonate-nitrate dimer at 188 and no trimer at 191, because the
source declusters beyond the dimer. So the channel is real on this chemistry
and an acquisition starting above 126 can never show it.

Two changes follow. The probe set gains the reagent-acid clusters, built from
the profile's own `reagent_formula` so a labelled reagent's label follows into
them without a second table: the 15N profile's clusters land at m/z 124 and
125, 0.997 Da above the unlabelled ones. That extends carbonate's reach from
m/z 61 to 125. And a channel none of whose probes lies inside the acquisition's
own mass range is recorded as `unobservable` rather than absent - the spectrum
was never asked, so its silence is not evidence - with each channel declaring
what to do in that case. The nitrate profiles declare `on`, on the C2 evidence;
everything else stays off, because silence is not evidence unless a profile has
a reason to say so.

The bromide panel keeps the bare carbonate ions only. Extending the same acid
cluster to it is unmeasured extrapolation, and it measured badly: the candidate
`[HCO3+HBr]-` line on set D is an order of magnitude weaker than nitrate's
clusters (0.06% of base against 0.1-0.44%), weak enough to be an analyte at
that mass, and reading it as a carrier switched the channel on and cost the set
24 of its same-formula agreements.

**What carbonate does on the nitrate sets.** On C it wins 130 peaks, 20 of them
as assigned and 110 held at candidate; 40 agree with the reference's formula
where 0 did before, and G1 falls 57 -> 54%. On C2, where the fingerprint is
found rather than assumed, it wins 42 with 13 assigned. The reference commits
85 carbonate readings on C2, all at its own Candidate tier, so neither engine
is confident here and both say so.

**One metric moved the wrong way on set C**: G2 same-formula 86.0 -> 79.7%,
while same-ion recovery holds at 86.5%. The peaks are not lost, they change
split: readings the reference calls `[M+NO3]-` that we used to reach through
`-H+` now go to carbonate instead, so they count as the same ion read
differently rather than the same formula. That is step 1.3's decision to make -
the family rule ranks the reading, and this step deliberately does not - and it
is the same 51 same-ion carbonate readings the C2 comparison shows.

**One metric moved the wrong way on the TOF**: G2 same-formula on set F2,
43.5 -> 41.3%,
where the carbonate channel won 62 peaks on a fingerprint that clears the floor
by 7% (0.011% of the base peak against a 0.01% floor). It is a TOF set, which
gates on intrinsic metrics, and the floor looks right for the chemistry rather
than wrong in general: the same probe clears it by two orders of magnitude on
the Orbitrap nitrate set, and it is marginal only where the intensity is a peak
area rather than a height. Left for step 1.4's fuller cluster library, which
gives a channel more than one ion to prove itself on.

The stage targets below are stated for the Orbitrap sets A-D and C2; C and D
start from a worse baseline than A and B and are held to the same targets, and
C2 is held to C's.

| metric | today A | today B | today C | after stage 1 | after stage 2 | after stage 3 |
|---|---|---|---|---|---|---|
| G1 "assigned" rows the reference does not confirm | 73% | 57% | 99% | <= 45% | <= 20% | <= 15% |
| G2 reference Assigned peaks recovered: same formula / same ion | 39% / - | 12% / - | 18% / - | >= 80% / >= 95% (A, C), >= 70% / >= 95% (B) | >= 85% / >= 95% | hold |
| G3 committed formulas with N >= 5; carbon-free formulas | 13%; 59 | 15%; - | 17%; - | <= 1%; 0 off the allowlist | hold | hold |
| G4 reference reagent peaks labelled reagent | 0 of 58 | 0 of 24 | 0 of 29 | >= 90% | 100% | hold |
| G5 reference Assigned peaks never searched | 190 | 4,181 | 8 | 0 | 0 | 0 |
| G6 main peaks on reference isotopologues | 96 | - | - | <= 10 | <= 5 | hold |
| G7 uncorroborated commits beyond 3 sigma | not gated | not gated | not gated | - | 0 | 0 |
| mass error of committed peaks, MAD | 0.20 ppm | 0.20 ppm | 1.13 ppm | <= 0.35 ppm on an Orbitrap | hold | hold |
| every committed row carries tier reasons | no | no | no | - | yes | yes |
| corroboration from series or time series | none | none | none | - | - | reported per batch |

Set C shows the nitrate chemistry is the worst of the three: the covalent
nitrate reading through `-H+` fits almost any peak within 10 ppm, 63% of the
committed formulas carry three or more nitrogens, and the committed mass
errors spread five times wider than the reference's. Peaky reads 145 of its
main peaks there through the carbonate channel, which the mode does not
offer (step 1.2).

"Hold" means the earlier target still applies. The reference is peaky's
ledger, which is not truth; the targets are agreement bounds a chemist then
audits through `tier_disagrees`.

## Decisions (taken 2026-09-07)

1. **Presets before rows.** Profiles ship as library presets resolved from
   the ionization mode; the versioned DB rows and settings UI are step 3.5.
   The run config snapshot keeps runs reproducible without a schema change.
2. **The same-ion policy.** The adduct or cluster reading wins when two
   candidates form the same ion; the covalent reading is kept as a flagged
   alternative. It matches the source chemistry, and Stage A's curated
   targets still win their peaks.
3. **Tiers may demote Stage A rows.** The mechanical rules and the mass gate
   apply to curated targets too: a target matched 4 ppm off on a 0.2 ppm
   instrument is not "assigned", and a curated hit within calibration is
   corroborated by its curation.
4. **The cap.** Every peak by default, with the 5,000 ceiling as the hard
   bound; ingest-time runs are Stage A only, so the cost lands on explicit
   runs.
5. **Release posture.** The feature stays unreleased until the stage 2 gate
   passes. Peaky's published ledgers remain available through the import
   path in the meantime, but are not the release.
6. **Broader test coverage, including TOF.** The gate set grows beyond the
   two Orbitrap uronium batches: a TOF batch and further chemistries, as
   the gate-set section describes. A stage gate is judged on the whole set.
7. **The seed proposal's defaults** stand as taken there: sweep order
   nitrate, bromide, urea and ammonium; opt-in production loading with the
   demo loading automatically; radicals and clusters as separate lists, off
   by default; the Stage A window widened with the seed.
8. **An unobservable channel is the profile's call, and the nitrate profiles
   say on** (taken 2026-09-07 with step 1.2). A secondary channel none of
   whose fingerprint ions lies inside a sample's acquisition window was never
   asked about, so its silence is not evidence: it is recorded as
   `unobservable`, distinct from `absent`, and each channel declares what to
   do. The default is off - silence stays silence unless a profile has a
   reason. The nitrate profiles' carbonate channel has one: on a broad-window
   run of that chemistry carbonate is in every sample at 0.5-1.0% of the base
   peak, and the source makes no carbonate carrier above m/z 126 at all, so an
   acquisition starting higher can never show a channel it is certainly
   running. What an unobservable-but-open channel may then do is unchanged: it
   takes no peak a declared mechanism won, and commits as assigned only with
   corroboration. Revisit per profile if a later acquisition of the same
   chemistry shows the carrier absent.

## Risks

- **Runtime without the cap.** Bounded by the profile grid; measured per
  step; the 5,000 ceiling stays. The grid-once enumeration is the fallback.
- **The reagent pre-pass claiming analytes.** Library restricted to bare
  clusters and hydrates; the gate checks agreed analyte rows are never
  claimed.
- **Double counting chemistry.** Context windows gate the grid in stage 1;
  the graded context factor of the profiles design joins in stage 2 only
  after the decoy harness shows it helps.
- **Over-demotion of curated targets.** Decision 3; the reasons make every
  demotion auditable, and a verification verdict overrides.
- **Stage heterogeneity during stage 1.** Until 2.1, Stage B evidence stays
  on the v1 scale; the stage 1 gate therefore judges search metrics (G2-G6),
  not G1 alone.

## Not in this plan

peaky's residual explainer, ladder gap-fill, labelled-reagent rescue and
certified-neutral passes (77 peaks on the testbed; revisit with the data
after stage 3); retention-time and MS2 evidence (their own designs); the
reagent axis' eventual move into `IonizationSetup`
([ionization_method_config.md](ionization_method_config.md)).

## Related documents

- [chemistry_profiles.md](chemistry_profiles.md) - the profile model this
  plan sequences.
- [assignment_confidence.md](assignment_confidence.md) - the layered
  confidence architecture.
- [peak_assignment_paradigm.md](peak_assignment_paradigm.md) - the engine.
- [peak_assignment_batch_primary.md](peak_assignment_batch_primary.md) - the
  batch ledger stage 3 builds on.
- [verification_calibration_loop.md](verification_calibration_loop.md) -
  verdicts to calibration.
- [reference_data_authoring.md](reference_data_authoring.md) and the
  reference-database seed proposal (2026-09-07) - step 2.5.
- `tooling/assignment_compare/README.md` - the measurement.
