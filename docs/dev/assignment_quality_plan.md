`ULTRA TRACE MASCOPE - DESIGN DOC - ASSIGNMENT QUALITY PLAN`

# Closing the assignment quality gap: the work, step by step

## Status

Epic branch `epic/assignment-quality`, draft PR #2077 into `develop`;
step PRs land on the epic and are named here as they merge.

| step | PR | state |
|---|---|---|
| 0 - gate, epic branch, design commit | #2077 | done: testbed, comparison tool, baselines A-F, epic branch |
| 1.1 - assignment profiles (library presets, resolution, stamping) | - | in progress (handed over 2026-09-07) |
| 1.2 - opportunistic adduct channels | - | planned |
| 1.3 - same-ion tie policy in the finder | - | planned |
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
  | C | Orbitrap A | 15N-nitrate CIMS, negative | 5 | measured |
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
  suites stay green.
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
  window (3 ppm Orbitrap, 20 ppm TOF, overridable). Context ratio windows go
  through `HeuristicFilterConfig.carbon_element_ratio_range`, which
  `rule_element_ratio` already applies; a `dbe_to_c` window is a new rule
  beside it. `batch_untargeted.search_config` takes the same profile.
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
- **Size.** S-M. Independent of 1.1 and 1.2.

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
- **Where.** A `reagent_pass.py` beside `engine.py`; `engine.py` gains
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

The stage targets below are stated for the Orbitrap sets A-D; C and D
start from a worse baseline than A and B and are held to the same targets.

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
