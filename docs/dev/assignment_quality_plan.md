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
| 1.3 - same-ion tie policy in the finder | #2082 | measured: the step's target met on A (47 -> 87% same formula); the note's mass-only policy needed a closed-shell key the gate supplied |
| 1.4 - reagent-cluster pre-pass | #2086 | measured: 60 peaks carry 81.2% of set A's signal; the claim is anchored on the sample's own base ions and the envelope reaches the floor it searches; no reference analyte taken on A/B/C/D/E/F1 |
| 1.5 - satellite claim and ringing artifacts | #2088 | measured: G8 met, 0 ownerless isotopologue rows on every set; B claims 969 more satellites and D 423, 88-97% of them confirmed by the reference, and D gains 61 analytes and 27 agreements; two review rounds fixed the envelope's anchor and then restored the requirement anchoring it took away; G6 missed, and the reference's parent ion is outside the searched grid for 78 of A's 79 (decision 11) |
| 1.6 - cap and mass window | #2090 | measured: G5 met (35,496 unsearched peaks -> 0, of which 5,304 the reference calls Assigned and 8,928 it commits any analyte on) and G2 clears its stage-1 target on A, B, C and D for the first time (B 20.7 -> 95.2%); the mass window was already instrument-class-resolved by 1.1; the grid is enumerated once per band instead of once per peak, so A and C search 5-8x more peaks and finish faster, worst sample 36s; G1 rises on the sets that gained most and G6 with it, and the review found G6's rise has an envelope part beside the grid gap, now decision 11's rider with homes in 2.1 and 2.4 |
| 1.7 - stage 1 gate, engine 0.4.0 | #2091 | measured: the 0.4.0 build reproduces the 1.6 ledger field for field, so stage 1's numbers are final; G1 met on A, B, C and D (73 -> 41.5, 57 -> 24.3, 99 -> 41.5, 68 -> 37.3%) and missed on C2 (55.2%); G2's same-formula bound met on the same four (39 -> 95.6, 12 -> 95.2, 18 -> 87.3, 17 -> 80.1%) and its same-ion bound on A and B only; G3 met but for B's 2.7% N >= 5, with no carbon-free formula from the untargeted stage on any of the 43 samples; G5 and G8 met everywhere; the mass-error target met on all five Orbitrap sets; G4a below 90% and G6 read, not gated; about half of what stage 1 does not recover carries an element the searched grid cannot build, which is step 2.5b's |
| 2.1 - v2 fit for Stage B | #2092 | measured: the finder ranks with the v2 fit at the sample's own mass width and on the file's own per-peak signal-to-noise, and every committed reading is measured again as an ion, so the engine computes one fit (decision 12); G1 falls on every Orbitrap set (A 41.5 -> 35.4, B 24.3 -> 18.9, C 41.5 -> 34.7, C2 55.2 -> 40.1, D 37.3 -> 21.0) with G2 unchanged on A and B and up on C, C2 and D, G5 and G8 still zero and the mass error flat or better on the Orbitrap sets; the fit distributions of confirmed and contradicted rows separate for the first time (B -0.001 -> 0.129, D 0.020 -> 0.249) and the odd-electron share of assigned rows falls on seven of the eight sets; the TOF sets gain 293-675 committed analytes at their own width; the sibling task, a TOF-capable reference run from peaky, is not in this PR because publishing one re-bases every set |
| 2.1b - TOF-capable reference: peaky's scorer through `score_pattern_v2` with the sample's fitted sigma | #2093 + peaky `epic/v2-fit-reference` | measured: all 43 reference runs re-published at the sample's own width, with the engine's runs untouched (same run id on 43 of 43 after the publish); the reference commits less on the Orbitrap sets and more on the TOF ones (B 7,614 -> 4,680 M0, F1 698 -> 1,034), its committed mass error improves on every Orbitrap set (B 0.290 -> 0.174 ppm) and its own fit finally separates its confirmed rows from its contradicted ones (A 0.089 -> 0.174, C 0.066 -> 0.181, C2 -0.051 -> +0.052, on hundreds of rows a side); the share of this engine's assigned rows the reference CONTRADICTS falls on every Orbitrap set (C 2.4 -> 0.3%, C2 4.3 -> 1.3%) while G1 rises because a conservative reference is silent more (A 35.4 -> 40.2, B 18.9 -> 44.2, D 21.0 -> 21.6); G2 formula 93.8 A, 92.8 B, 90.6 C, 77.7 C2, 80.4 D; G5 and G8 still zero; the peaks endpoint did not carry signal-to-noise and now does; two rounds of the gate caught the same defect in the shared fit, that it returns the offset and the width together and reports neither below its anchor minimum |
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
  The reference engine's own changes live on peaky's `epic/v2-fit-reference`,
  which pins `mascope-tools` to a Mascope revision by git source so that
  nothing on peaky's main depends on unreleased code; step 2.7 says when
  and how that branch merges.
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
  and `finder.assign_compositions` the family is scored once - the evidence
  belongs to the ion and not to the split - and ranked by two keys in this
  order. First the reading whose neutral is a closed-shell molecule wins
  (decision 9). That key separates two readings only when the fragment between
  them carries a half-integer DBE of its own, as HCO3 does and NH3, urea, HNO3
  and HBr do not, so on most families it is silent. Then, among readings it
  does not separate, the one whose mechanism contributes the most mass - the
  adduct or cluster reading - wins, and the covalent reading is kept as a
  structured alternative flagged `same_ion`. Ties between genuinely different
  ions break on score, then |ppm|, then plausibility, then formula and ion,
  never on enumeration order.
  `engine.untargeted_matches_to_peak_assignments` stores the family members as
  alternatives with the flag so the inspector can show the ambiguity.
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
  written as `role = reagent`, with their isotopologue satellites claimed as
  reagent rows too (intensity-gated through IsoSpec envelopes), and excluded
  from both stages' candidate peaks. Bare clusters and hydrates only:
  organic-acid adducts of the reagent are analyte channels and stay out,
  which is the lesson peaky learned. Nothing is "locked": the ordering does
  that work, because a peak the pre-pass claimed is never offered to a stage
  in the first place (decision 10).
- **Where.** `mascope_tools.composition.reagents` already exists from step
  1.2, holding the channel probes (the narrow half of this library: enough
  exact masses to say whether a carrier is in the spectrum); this step grows
  that module into the full cluster grammar with isotopologues and hydrates
  rather than starting a second one. A `reagent_pass.py` beside `engine.py`; `engine.py` gains
  `ROLE_REAGENT` and `ROLE_ARTIFACT`; `schemas.AssignmentSource` gains
  `reagent` (the read model, `batch_peaks.ROLE_CODES` and the import path
  already accept the roles). Three call paths need it, not one: the
  run-backed orchestrator, the run-less ingest fold, and
  `batch_untargeted.choose_representatives` - a reagent anchor's consensus
  tier is `unassigned`, having no formula to vote on, so the batch search
  would otherwise be the one path that puts an analyte formula back on a
  reagent peak.
- **Why.** Cause 5: 82% of the signal, the top-ten peaks of every sample.
- **Verify.** Unit tests on the library masses; on the testbed every peak
  peaky labels reagent **and names an ion formula for** must be a reagent row
  (G4a) and no confident analyte may be claimed (checked against the agreed
  rows). The qualification is not a softening: peaky's reagent role also covers
  the ringing skirt of a bright cluster, which it records without a formula and
  which this engine gives the separate `artifact` role in step 1.5. But it is
  not the whole story either - peaky also writes formula-less reagent rows for
  peaks that are not skirt, with a bromine twin and a strongly negative mass
  defect - so the unqualified G4 stays in the table as the 1.7 gate's measure,
  counted against `reagent` OR `artifact` once 1.5 lands, with G4a as this
  step's own target beside it.
- **Also verify.** No peak the reference assigns an analyte to may become a
  reagent row: `compare_runs.py`'s `b_role_where_a_reagent` has to be free of
  reference M0 rows on every set whose reagent the reference also models. That
  is the acceptance test for the claim window, and the one metric that catches
  a window wide enough to swallow a neighbouring ion - "no analyte agreement
  lost" cannot, because it only sees peaks this engine had already assigned.
- **Size.** M. Depends on 1.1.

### 1.5 Satellite claim and ringing artifacts

- **What.** Stage B receives the whole peak list as pattern context and the
  searched set as `targets` (the finder already supports this split, and the
  batch search already uses it), so an M0's satellites are claimed wherever
  they sit. That is the substance of the step: the searched set is capped at
  the 300 most intense unexplained peaks, so an envelope was previously scored
  against at most 300 peaks - 2.5% of a dense spectrum - and a satellite
  outside that set was not found, leaving its peak to be searched on its own
  account. A satellite row is written by the M0 that claims it and names that
  M0 as its owner from the start; it is never linked to a parent after the
  fact, and a satellite whose ion commits no M0 is not written at all - that
  second pass is the fix for the ownerless rows. The matcher anchors a
  predicted envelope on the ion's own monoisotopic line rather than on the
  predictor's most abundant one, which is the same line by accident for an
  ordinary ion and two mass units away for a dibromide. The finder's duplicate
  resolution ranks a row that IS somebody's monoisotopic line ahead of another
  candidate's satellite for the same peak; that one is a guard rather than a
  live rule, because the loop claims each m/z as it emits it. FT sidelobes come
  from the existing `mascope_tools.alignment.utils.flag_satellite_peaks` and get
  `role = artifact` in a pre-pass beside the reagent one, excluded from both
  stages.
- **Where.** `service._run_sample_assignment` (the `assign_compositions` call
  and the new pre-pass), `engine.untargeted_matches_to_peak_assignments`,
  `peak_assignments/artifact_pass.py`, `heuristic_filter.match_isotopic_pattern`
  (the envelope's anchor), and the finder's duplicate resolution in
  `assign_compositions`.
- **Why.** Mascope commits an analyte M0 on peaks the reference reads as
  isotopologues (79 on instrument A), and the artifact role was unused. And the
  untargeted stage wrote isotopologue rows that belong to nothing: on the
  bromide Orbitrap set every sample carried 12-23 `iso_child` rows with no
  owner (71 over six samples, mostly 81Br lines above m/z 360, some at
  `assigned` tier), and for 64 of them the parent peak sat in the ledger
  unassigned. The counts were identical before and after step 1.4, so they were
  the stage's own.
- **Verify.** Gate metric G8, untargeted `iso_child` rows without an owner, at
  0 on every set - met, from 71 / 6 / 1 on D / E / F1. Peaks that lose a
  committed analyte outright between one run and the engine's previous one, at
  0 on every set - the number the review's finding needed, which the comparison
  now reports. Artifact rows present where the flag fires. Gate metric G6 (main peaks on reference isotopologues,
  at most 10 on A) - **missed**, and the measurement says why: the reference
  names the parent ion of 78 of A's 79 with an element Mascope's grid has no
  room for, silicon in 76 of them. Those are column-bleed siloxanes, and no
  envelope logic reaches them while the search cannot build the parent. The
  residue is a search-space gap, which is what decision 11 settles: stage 1
  records G6 and step 2.5b is where its target is met. See "What G6 measures,
  and what step 1.5 could not reach".
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
  Measured 2026-09-08: G5 met on every set, and lifting the cap is what let G2
  clear its stage-1 target on A, B, C and D. The window half of this step was
  already done by 1.1. The grid rework the size note called conditional was
  needed: the densest TOF sample cost 133 seconds with the cap lifted, and
  enumerating once per band brought it to 39 offline and 33 on the testbed.
- **Size.** S. Depends on 1.1.

### 1.7 Stage 1 gate, engine 0.4.0

- Run the twelve-sample protocol, fill the status table, bump the engine
  version, changelog entry, and rebuild the stakeholder view (the brightest
  peaks of one sample, both readings) from the new runs. Expected: G1 near
  the 45-50% the offline experiment reached, G3-G5 at target, G6 read and
  recorded but not gated (decision 11: it is judged after step 2.5b), and
  step 1.5's coherence count (untargeted isotopologue rows without an owner)
  at zero. Recorded but not gated, as the before-numbers step 2.1 has to
  move: the elections the whole-spectrum context flipped toward a candidate
  with fewer observable lines (10 on A, 15 on B after 1.5) and the share of
  untargeted M0 rows whose neutral is odd-electron.
  Measured 2026-09-08 (section below): G1 met on A, B, C and D and missed on
  C2; G3, G5 and G8 met, with the untargeted stage writing no carbon-free
  formula on any of the 43 samples; G2's same-formula bound met on the four G1
  clears and its same-ion bound on A and B; the mass-error target met on every
  Orbitrap set. G4a stays below 90% for the reasons step 1.4 recorded, and G6
  is read but not gated. The stage-1 build reproduces the 1.6 ledger field for
  field, so the version bump stamps the runs and changes nothing else. The two
  before-numbers step 2.1 has to move are recorded there: the odd-electron
  share per set, and that 80-99% of committed analytes stand on the
  monoisotopic line alone.
- **Size.** S.

## Stage 2 - earn the tier (engine 0.5.0)

The confidence layer. This is where "assigned" starts meaning something.

### 2.1 The v2 fit for Stage B

- **What.** After the finder picks each peak's winner and family, the
  winners' `(formula, mechanism)` seeds are re-scored through
  `seeded_scoring.score_seeds` - one `compute_match_isotopes` pass per
  sample, the SNR-aware v2 fit with the same gating Stage A uses - and that
  fit is what evidence and tier are read from. Nothing carries the finder's
  v1 number - not provenance, not the ledger, not a comparator column
  (decision 12). The same fit also ranks the candidates inside the
  finder (`match_isotopic_pattern`, where `score_pattern` decides which
  reading of a peak wins before the same-ion election of decision 9):
  re-scoring the winner afterwards leaves a wrong election in place, and the
  election is where the v1 score does its damage. The finder scores on the
  real per-peak signal-to-noise, not on v2's no-SNR mode: peak detection
  writes one per peak to the filestore and the targeted matcher's read
  (`load_peaks`) already carries it as a coordinate, while the engine's own
  read (`load_sample_peaks`) surfaces id, m/z and intensity only - the
  column has to travel from that read into the frame the finder is handed,
  beside the sample's fitted sigma. The envelope predictor's
  1% abundance cutoff (`ISOTOPE_ABUNDANCE_THRESHOLD`) goes with it: under the
  detectability gate a faint line is predicted and then judged against the
  noise rather than dropped before anyone looks. It survives as the SHALLOW
  bound - no peak's envelope is predicted less deeply than the old cutoff
  predicted every peak's - and as the default for a caller that describes no
  sample; what goes is its use as the floor on a bright peak. After step 1.6, 45 of the
  100 G6 rows on B whose parent Mascope reads with the reference's own
  formula are lines the predictor never emitted (decision 11's rider).
- **Why.** Cause 2: the v1 fit cannot rank, and Stage A and Stage B evidence
  are on different scales (noted in `config.py`). It is also what makes a
  TOF assignable at all: v1 scales its mass term by a fixed 5 ppm, v2 by the
  sample's fitted mass width. Step 1.5 measured how v1 ranks once the whole
  spectrum is the pattern context: its score is 60% the mean mass error and
  20% the mean intensity error over the lines it matched, and a missing line
  costs it only through a cosine term the M0 dominates - so a candidate that
  finds one more line, imperfectly, scores below one that finds none. On A
  the 40,000-count peak at m/z 299.079 moved from C10H19O8S+ (0.893, then
  0.686 once its 34S line was found at -36%) to C16H13NO5+ (0.825, nothing
  found, a 13C line that had to be there absent); m/z 358.113 from
  C15H20NO9+ (0.972 to 0.649 on its 13C at -26%) to C22H18N2OS+ (0.960);
  m/z 403.233 from C20H35O8+ (0.983 to 0.907) to C27H33NS+ (0.926). Ten
  elections flipped on A and fifteen on B, toward sulfur-rich,
  hydrogen-poor radicals that predict lines the score does not charge for
  missing. The v2 fit charges a missing line when it should have been
  detectable and ignores one below noise, which is the whole difference.
  (The other v1 flaw the context exposed, anchoring the match on the most
  abundant configuration rather than the monoisotopic line, is step 1.5's
  own fix.)
- **Sibling task.** A TOF-capable reference: peaky's local scorer scoring
  through `score_pattern_v2` with the sample's fitted sigma, so the TOF gate
  sets get a reference run worth comparing against. It is step 2.1b, with a
  before and after of its own, and it goes before 2.2 (decision 13).
- **Verify.** Fit distributions per verdict class separate; the goldens in
  `tooling/score_eval` are untouched; the config comment about stage
  heterogeneity is retired; on gate set E both engines commit more than the
  reagent ions; the 25 elections step 1.5's context flipped on A and B go
  back to a reading whose predicted lines are present, and the share of
  untargeted M0 rows whose neutral is odd-electron (7% on A to 37% on E after
  1.5, one line per engine in `compare_runs.py`) falls on the uronium and
  bromide sets, where such a neutral is rarely chemistry; the G6 rows whose
  parent Mascope commits with the reference's own formula (100 on B, 32 on D
  after 1.6) shrink, because their lines are now predicted and judged; no
  row's provenance carries a v1 number, and every row's `score_version`
  names the fit that produced it (today every Stage B row is stamped 2 and
  scored with v1); the evidence records the base peak's signal-to-noise the
  detectability gate used, so the re-read can show the finder's fit was the
  SNR-aware one on every gate set rather than assume it.
  Measured 2026-09-09 (section below): every run records the width it judged a
  mass error at and whether that width was its own; the fit distributions of
  confirmed and contradicted rows separate on seven of the eight sets, from a gap of 0.010 on
  A and -0.001 on B to 0.181 and 0.129; G1 falls on every Orbitrap set with G2
  unchanged or better and G5, G8 and the mass error unmoved; the odd-electron
  share of *assigned* untargeted rows falls on seven sets (D 22.2 -> 12.8%);
  the TOF sets gain 293-675 committed analytes; 54 of the 72 readings of step
  1.5's twelve flipped ions have moved off that election, 39 of them back to
  the reading it displaced. No row carries a second score, both stages stamp a
  `score_version` that is now true of them, and every committed row records the
  base peak's signal-to-noise its absent lines were judged against. The goldens
  in `tooling/score_eval` are untouched because neither score's arithmetic
  changed. The G6 rows whose parent this
  engine reads with the reference's own formula shrink only slightly (B 101 ->
  83, D 32 -> 30): the rest are lines the envelope predicts and the intensity
  gate refuses, which is step 2.4's neighbour rule. The sibling task is NOT
  done: publishing a TOF-capable reference re-bases every set's comparison, so
  it is its own change with its own before and after.
- **Size.** M. Depends on stage 1. First in stage 2: once step 1.5 made the
  pattern context whole, the score became the weakest link, and every stage-2
  number is read off it.

### 2.1b TOF-capable reference: peaky through the v2 fit

- **What.** peaky's local scorer (`score_candidates_local` in its
  `local_scoring.py`, the default path; the network scorer is the opt-in)
  scores every candidate through `score_pattern_v2` with a `PatternScoring`
  built for the sample the way the engine builds its own
  (`pattern_scoring_for`): the width and offset from the sample's own mass
  errors, the line-matching window the instrument class's (5 ppm Orbitrap,
  15 ppm TOF, what a run snapshot's `mz_tolerance_ppm` records), the
  abundance floor the engine's. Three inputs have to be sourced, and the
  first is a Mascope change:
  - *The fit.* `fit_sample_mass_accuracy`, `mass_accuracy_anchors` and
    `MASS_ACCURACY_MIN_ANCHORS` live in the backend's match controller
    (`match_score_v2.py`), and peaky depends on `mascope_tools` alone. They
    move into the library beside `PatternScoring` and
    `resolve_fallback_sigma_ppm`; the engine's `sample_mass_accuracy` imports
    them from there and computes nothing differently (a test pins the same
    mu, sigma and anchor count on the same frame). One implementation of the
    fit for engine and reference: peaky's own pass-1 `calibrate` is the same
    robust fit already - median, scaled MAD, a floor - so calling the
    library's loses nothing. This half is its own PR on the epic and lands
    first, because step 2.2 builds on the same function.
  - *The anchors.* peaky's peaks frame already carries the sample's targeted
    matches (its `estimate_offset` seeds an offset from them). The fit runs
    over those at the start of the run; below the anchor minimum the width
    is the instrument class's from the library's table, which is the
    engine's rule. Whether peaky's later pass-1 self-calibration re-scores is
    peaky's call, not this step's.
  - *The signal-to-noise.* The peaks endpoint returns it per peak and the
    SDK frame carries every field the endpoint sends, so the column is
    already in peaky's fetch; the scorer keeps only m/z, height and id and
    has to carry it beside them into the fit. Without it v2 runs in its
    no-SNR mode, which charges an absent line by a fixed abundance rule
    rather than by the noise, and the reference would then decide the faint
    lines differently from the engine - the decisions step 2.1 turned on.
    peaky caches each sample's peaks frame; a frame cached before the
    endpoint carried the field lacks it, so the gate samples are re-fetched.
    A file with no stored signal-to-noise scores in the no-SNR mode on both
    sides, as the engine does today.
  peaky's score thresholds - its categories at 0.8 and 0.4, its passes'
  `tau_good` - were set on v1's scale; on v2's scale they are re-read, and
  the section records the reference's own tier counts per set before and
  after, so that a G2 move can be told from a threshold move. The
  line-matching window override peaky gained for the TOF sets
  (`PEAKY_MATCH_PPM`, on an unmerged branch) is subsumed: the window comes
  from the class. `score_pattern` stays in the library for the goldens
  harness (decision 12). peaky's repository is public, so its commits and
  pull requests follow the same anonymisation rule as this one.
- **Why.** The reference has scored with v1 all along - peaky imports the
  library's `score_pattern` - and v1 scales its mass term by a fixed 5 ppm,
  so on a TOF the reference fails as the engine did: it commits fewer
  analytes than reagent ions on set E, and E's and F's G1 and G2 cannot be
  read (baselines). Since step 2.1 the engine scores with v2 and the
  reference still with v1, so every G1 and G2 read since then compares a v2
  engine to a v1 reference; through stage 1 both scored with v1, which is
  the condition the gate bounds were set under, and this step restores that
  symmetry at v2. It goes before 2.2 (decision 13): 2.2 is the first step
  whose main effect lands where a v1 reference cannot read - the TOF sets
  and the mass width - its Verify has a reference clause, and the fit it
  builds on is the function this step moves. The reference sharing the
  engine's scorer does not make the gate circular: the gate has always
  measured what the score does not decide - the candidate universe, the
  election, the tiers and peaky's arbitration.
- **Verify.** The engine does not change: the 2.1 runs of 2026-09-09 (build
  ddafa70) are the engine side before and after, and no engine run is
  launched while the reference is published and re-read - the publish of
  all 43 gate samples and the comparison are this step's critical section on
  the testbed, as a deploy is for an engine step. The previous reference's
  ledgers are copied out first: the store keeps a bounded number of runs
  per sample and engine, and the publish supersedes them. Then a "reference
  re-based" section restates, for every set against the same engine runs,
  the metrics the reference enters - G1, G2, G4, G5, G6 - beside the
  post-2.1 numbers, with the reference's committed M0 count, its tier counts
  and the share of its committed formulas that changed, per set; the
  stage-2 bounds are then read against the re-based numbers, and whether
  they still bind is the question the re-base answers. On set E both
  engines commit more than the reagent ions (step 2.1's clause the
  reference could not meet), and on E, F1 and F2 the reference's Assigned
  rows sit at the instrument's own width, so G1 and G2 read there for the
  first time. **Measured: half met.** The TOF reference is scored at its own
  width and commits more (E 119 -> 139 analytes, F1 698 -> 1,034, F2 755 ->
  827), but on E it still commits 139 against 336 reagent rows: what limits
  it there is not the scorer but its candidate enumeration - pass 1 offers
  two target peaks and one plausible formula on a 1,159-peak spectrum, and
  nearly every commit comes from the certified list. G1 and G2 are therefore
  readable on the TOF sets in the sense that both sides now score alike, and
  still not decisive: the two engines agree the formula of six peaks on E,
  thirty-seven on F1 and forty-two on F2, against hundreds of disagreements, so
  there is no separation to read there either way - at 1-4 ppm across three
  channels the mass does not settle which reading is right. That is 2.3's corroboration and 2.4's density.
  The goldens in `tooling/score_eval` are untouched, the engine's tests pin
  the moved fit, and the section names the peaky commit and the library
  commit the reference was scored with, as an engine step names its build
  tag: peaky `epic/v2-fit-reference` at 5fa9b59, `mascope_tools` at
  fc25575da (the library half's branch head, pinned in peaky's lockfile).
- **Size.** S-M: a peaky change, one library PR, 43 publishes and a
  re-based table. Depends on 2.1. The library PR lands before 2.2 starts;
  the peaky half can run beside 2.2 after that, since their footprints are
  then disjoint.

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
  off the allowlist), and one more from decision 11's rider: an M0 committed
  on a peak that a committed neighbour's envelope predicts a line for, within
  the matcher's tolerance, carries the neighbour and the line as its reason
  and cannot sit at assigned tier - after step 1.6, 332 of B's 460 G6 rows
  and 275 of D's 328 do. Rules only demote; every row carries
  `provenance.tier_reasons`, and the run records the rule version.
- **Why.** Cause 2: the tier must degrade with evidence, and it must be able
  to say why.
- **Verify.** Gate metric G1 to 20% or below on both instruments; every
  committed row has at least one reason; the decoy harness
  (`tooling/score_eval`) confirms the demotes do not lower contested top-1;
  no G6 row of the envelope part (decision 11's rider) at assigned tier.
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
  answer is the main risk; gate metric G3; and gate metric G6 (at most 10
  on A), which decision 11 moves here from the stage-1 gate: on A the
  reference's parent ion for 78 of the 79 carries silicon or phosphorus,
  and the 29Si and 30Si lines of the column-bleed siloxanes attach to their
  parents only once the known window names them.
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
- **The reference's branch merges at the release, not at the gate.** peaky
  scores with the library, and its main branch has to run against Mascope's
  master - the released server and the libraries on PyPI, which the publish
  workflow ships from master when a library's `pyproject` version changes.
  The v2 reference therefore lives on peaky's `epic/v2-fit-reference`, which
  pins `mascope-tools` to a Mascope revision by git source (a direct URL in
  its `pyproject`, so pip and uv install the same thing), and the stage-2
  gate is measured against that branch at one named commit, the reference
  re-published whenever its scoring changes. Until the release the branch
  is rebased on peaky's main as main moves, and it is the only place that
  needs the epic's library; against a server older than the release the
  peaks carry no noise estimate and the reference scores in v2's no-SNR
  mode rather than failing. When the stage-2 epic has been reviewed into
  develop and released to master, in this order: the library version on
  master differs from PyPI's and the publish workflow ships it (check its
  run, not the tag); peaky's merge PR into main replaces the git source with
  the released lower bound, re-locks, bumps peaky's version and corrects its
  package version string, and peaky's CI then installs from PyPI, which is
  the test that main still works with master; peaky releases; and only then
  is `score_pattern` deprecated in the library (decision 12), once the
  goldens harness has moved as well. The stage-2 epic's review into develop
  does not move the branch: the coupling is to the release.

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
| G1 "assigned" rows the reference does not confirm (stage 1 gate: A 41.5%, B 24.3%, C 41.5%, D 37.3% - met; C2 55.2% - missed. After 2.1: A 35.4, B 18.9, C 34.7, C2 40.1, D 21.0 - met on all five, and B inside the stage-2 bound) | 73% | 57% | 99% | <= 45% | <= 20% | <= 15% |
| G2 reference Assigned peaks recovered: same formula / same ion (stage 1 gate: A 95.6/97.2% and B 95.2/96.1% - both bounds met; C 87.3/87.9% and D 80.1/82.3% - same formula met, same ion missed; C2 68.3% - missed, and 4.2 points of it are the nitrate ladder the pre-pass correctly claims. After 2.1: A 95.6/97.2, B 95.2/96.1, C 87.7/88.2, C2 74.9/74.9, D 80.6/82.8) | 39% / - | 12% / - | 18% / - | >= 80% / >= 95% (A, C), >= 70% / >= 95% (B) | >= 85% / >= 95% | hold |
| G3 committed formulas with N >= 5; carbon-free formulas (stage 1 gate: A 1.0% - met, B 2.7% - missed, 0.0% on every other set; every carbon-free formula left on any of the 43 samples is a Stage A curated row and the untargeted stage writes none, so the carbon half is met outright. After 2.1: A 1.2% and B 2.8%, still no carbon-free formula from the untargeted stage on any set) | 13%; 59 | 15%; - | 17%; - | <= 1%; 0 off the allowlist | hold | hold |
| G4 reference reagent peaks labelled reagent or artifact (stage 1 gate: A 48 of 58, B 14 of 24, D 123 of 262, E 48 of 336, F1 50 of 333; on C, C2 and F2 the reference's reagent rows are a different claim, so the raw share does not measure this engine's pass) | 0 of 58 | 0 of 24 | 0 of 29 | >= 90% | 100% | hold |
| G4a of those, the ones that **name an ion** (step 1.4's own target; stage 1 gate: A 82.8%, B 58.3%, D 84.4%, E 87.3%, F1 74.6% - missed, and A's and B's misses are second centroids and a reference label 5-7 ppm off, not a missing library entry) | 0 of 58 | 0 of 24 | 0 of 15 | >= 90% | 100% | hold |
| G5 reference Assigned peaks never searched (stage 1 gate: 0 on every set, from 5,304 pooled over A-F2 on the reference's own Assigned tier - A 186, B 4,180, C 7, which reproduces the step-0 baselines beside them) | 190 | 4,181 | 8 | 0 | 0 | 0 |
| G6 main peaks on reference isotopologues (stage 1 gate: 107 A, 460 B, 328 D, 57 C, 24 C2, 8 E, 57 F1, 33 F2, up from 79/54/75 because the peaks the cap hid are now searched - as a share of committed rows A is flat at 5.2%, B 3.4 -> 5.0%, D 8.8 -> 13.8%. Of the rows 1.6 added, the reference's parent ion is outside the searched grid for 20 of A's 28, 293 of B's 406 and 90 of D's 255 - that part is 2.5b's; the rest have the parent on the grid and are the envelope logic refusing or never predicting the line, which decision 11's rider gives to 2.1 and 2.4. After 1.5 the parent was outside the grid for 78 of A's 79 and 52 of D's 75, which is what decision 11 read. After 2.1: 103 A, 432 B, 314 D, 49 C, 18 C2, 7 E, 61 F1, 33 F2, and of the rows whose parent this engine reads with the reference's own formula 100 -> 83 on B and 32 -> 30 on D) | 96 | - | - | read, not gated (decision 11: <= 10 after 2.5b) | <= 5 | hold |
| G7 uncorroborated commits beyond 3 sigma | not gated | not gated | not gated | - | 0 | 0 |
| G8 untargeted isotopologue rows without an owner (step 1.5's coherence count; was 71 over the bromide Orbitrap set's six samples, 0 on every set after 1.5, at the stage 1 gate and after 2.1, with six to eight times as many peaks searched) | 0 | 0 | 0 | 0 | 0 | 0 |
| mass error of committed peaks, MAD (stage 1 gate: A 0.22, B 0.30, C 0.17, C2 0.31, D 0.31 ppm - met on all five Orbitrap sets. After 2.1: 0.215, 0.298, 0.168, 0.25, 0.307 - met on all five) | 0.20 ppm | 0.20 ppm | 1.13 ppm | <= 0.35 ppm on an Orbitrap | hold | hold |
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

### After step 1.3, the same-ion tie policy (2026-09-07)

Same protocol, on a build carrying steps 1.1 to 1.3. "Peaks with a family" is
how many committed rows had more than one reading of their ion to choose
between - the size of the decision this step takes, measured on the ledger.

| set | peaks with a family | Mascope M0 (assigned) | both M0: same formula | G1 | G2 same formula |
|---|---|---|---|---|---|
| A uronium | 974 of 1,575 | 1,575 (1,341) | 784 of 898 (87%) | 43% | 76% |
| B uronium | 821 of 1,599 | 1,599 (1,372) | 1,166 of 1,391 (84%) | 21% | 21% |
| C 15N-nitrate, from m/z 131 | 488 of 1,079 | 1,079 (820) | 596 of 687 (87%) | 41% | 86% |
| C2 15N-nitrate, from m/z 50 | 177 of 651 | 651 (517) | 291 of 354 (82%) | 55% | 70% |
| D bromide | 21 of 797 | 797 (759) | 588 of 672 (88%) | 24% | 36% |
| E bromide TOF | 34 of 235 | 235 (81) | 3 of 21 | 99% | 7% |
| F1 bromide TOF | 164 of 983 | 983 (417) | 15 of 85 | 97% | 14% |
| F2 nitrate TOF | 758 of 1,172 | 1,172 (615) | 25 of 163 | 97% | 41% |

**The step's own target is met.** Set A's same-formula share goes 47% at
baseline, 52% after 1.2, to 87%, against a target of 85%. B goes 49 -> 54 ->
84%, C 16 -> 68 -> 87%, C2 54 -> 66 -> 82%. G1 falls wherever families are
dense: A 73 -> 68 -> 43%, B 57 -> 50 -> 21%, C 99 -> 54 -> 41%, C2 72 -> 62 ->
55%. G2 same-formula recovery on A goes 39 -> 43 -> 76%.

**Cause 1's first half is as large as the baseline said.** 62% of the peaks
Mascope commits on set A had a split to decide, 51% on B, 65% on F2, 45% on C.
The finder was settling all of them by the order it enumerated its mechanisms
in.

**The policy in the note was not sufficient, and the gate said so.** Ranking a
family on the mechanism's mass alone - the adduct reading over the covalent one
- read 159 of set C's deprotonated acids as carbonate adducts instead:
`C17H23O4-` as `[C16H23O + CO3]-` rather than `[C17H24O4 - H]-`, because
carbonate carries 60 Da more than a lost proton. `C16H23O` is not a molecule.
Set C lost 28 same-formula agreements against step 1.2 and its G2 fell 80 ->
56%.

The fix came out of the same comparison. Adding a fragment to a neutral moves
its DBE by that fragment's own DBE less one, so two readings of an ion differ
on closed-shell-ness exactly when the fragment between them carries a
half-integer DBE. HCO3 does, and so does a carbon read as a nitrogen; NH3,
urea, HNO3 and HBr do not, which is why this key decides the carbonate readings
and is silent on the uronium and nitrate-against-deprotonation families below.
Where it does speak the reference picks the closed-shell reading every time: on
the build that motivated the change, in **390 of the 390 split readings whose
DBE the comparison computes** (the three it leaves blank are carbon-free,
ammonia and nitric acid, and closed-shell as well), and in every reading the
two engines agree on. So the family is ranked on that first and on the
mechanism's mass second. With both keys set C reads 596 of 687 the same way
(86% G2) and holds its 1.2 chemistry. It is a tie-break and not a filter: where
a radical is the only reading of an ion it is still committed, which is what a
nitrate source measuring RO2 requires.

**The two rules of steps 1.2 and 1.3 do meet, and they hold.** Set C now
commits 279 carbonate readings where 1.2 committed 130, and 138 of them are the
reference's own carbonate reading of the same peak. Only 40 are assigned: the
other 239 are held at candidate by the minor-channel cap, because set C cannot
fingerprint the channel and has it on by profile default. The family rule
decides *which reading*, the channel rule decides *what it may commit to*, and
neither re-ranked the other.

**One metric moved the wrong way**: set F2 loses 2 of its 163 shared peaks. Its
four remaining splits are `[M+NO3]-` against the reference's `[M-H]-`, where
both neutrals are molecules, so the parity key is silent and the mass rule
decides - the same rule that wins A and B. On a TOF set with 163 shared peaks
out of 8,905 this is not evidence either way.

**The bromide sets cannot benefit from this step, for a reason worth
recording.** The universal element-ratio band `Br/C <= 0.05` removes any
neutral carrying a bromine below C20, so the `[M+Br]-` against `[M'-H]-`
family - whose second member is always brominated - is cut before it can form:
21 families on 797 committed peaks, against 974 on set A. Eight of set D's 20
remaining splits want the carbonate channel, which its own fingerprint says
that acquisition cannot show. This is the Stage B counterpart of the Stage A
window that keeps halogen families from ever assigning (step 2.5), and it is
the same rule that makes a genuinely brominated analyte unassignable.

**Stage A is untouched, as intended.** Every peak where Mascope reads
`[M+H]+` and the reference `[M+NH4]+` - 12 on A, 8 on B - is a curated Stage A
win, not a Stage B reading.

**What still splits on the uronium sets** is 45 Stage B peaks on A and 38 on B
where both neutrals are molecules and Mascope takes the heavier mechanism - the
urea cluster or ammonium - while the reference takes the lighter one. The
parity key is silent there by construction, so this is the mass rule against
the reference's own arbitration, on 5% of A's shared peaks. Two more on B go
the other way. Step 1.4 removes part of the class rather than arbitrating it:
its reagent library claims the urea clusters as reagent rows, which takes them
out of the analyte ledger altogether.

### After step 1.4, the reagent pre-pass (2026-09-08)

Same protocol, on a build carrying steps 1.1 to 1.4. The columns that matter
here are different from the earlier steps': this step does not change which
formula a peak gets, it changes which peaks are offered a formula at all. So
the measure of success is signal accounted for, and the measure of harm is
whether any peak the reference assigns an analyte to has become a reagent row.

| set | peaks | reagent rows | % of peaks | **% of signal** | of a sample's 10 brightest | reference analytes taken |
|---|---|---|---|---|---|---|
| A uronium | 2,626 | 60 | 2.3% | **81.2%** | 24 of 60 | 0 |
| B uronium | 12,055 | 18 | 0.1% | **60.0%** | 8 of 60 | 0 |
| C 15N-nitrate, from m/z 131 | 1,583 | 0 | 0% | 0% | 0 of 50 | 0 |
| C2 15N-nitrate, from m/z 50 | 1,420 | 42 | 3.0% | **91.0%** | 27 of 60 | 12 (see below) |
| D bromide | 5,217 | 58 | 1.1% | **60.0%** | 26 of 60 | 0 |
| E bromide TOF | 3,493 | 48 | 1.4% | **59.1%** | 18 of 30 | 0 |
| F1 bromide TOF | 13,595 | 68 | 0.5% | **79.8%** | 41 of 60 | 0 |
| F2 nitrate TOF | 8,905 | 37 | 0.4% | **85.2%** | 15 of 50 | 10 (see below) |

**Cause 5 is the size the baseline said, and this is what it looks like when it
is named.** Sixty peaks on set A - 2.3% of the peak list - carry 81.2% of the
total signal, and 24 of the six samples' sixty brightest peaks are among them.

Analyte agreement against the step 1.3 build: A **+3** both-M0 / **+3**
same-formula, B +2 / +2, C 0 / 0, C2 -6 / -6, D +3 / +1, E 0 / 0, F1 +1 / 0,
F2 -10 / -9. The nitrate movements are the reagent ladder itself leaving the
analyte ledger, which is the step working; A and B gain because peaks the
pre-pass takes out no longer compete for the untargeted stage's cap.

### The claim window: what the first attempt got wrong

The first build of this step claimed in a flat 40 ppm window, justified by
reading the peaks 21 to 28 ppm above the urea tetramer and pentamer masses as a
calibration drift that grew with mass, and by the observation that each was the
only peak within 40 ppm of its mass.

Both halves were wrong, and the way they were wrong is worth keeping.

Those three peaks are **one ambient compound read through three uronium
channels** - `C13H20O4` as `[M+H]+` at 241.1434, `[M+NH4]+` at 258.1700 and
`[M+(urea)H]+` at 301.1754 - each within about a ppm of its own exact mass, and
all three assigned by the reference. They are not drifted rungs of anything.
Nor was there a drift to fit: that sample's own reagent ions sit at +4.0, -0.3
and +4.7 ppm, and three "rungs" at +27.5, +25.9 and +21.1 are not on that
curve. **Being alone in a wide window is not evidence that a peak is the
reagent's**; it is only evidence that the window is wide.

The check that would have caught it is not the one that was run. "No analyte
agreement lost" cannot see this class at all, because on the previous build
these peaks were unassigned on the Mascope side - beyond the untargeted cap -
so there was no agreement to lose. The check that sees it is
`compare_runs.py`'s `b_role_where_a_reagent`: **reference analytes among this
engine's reagent rows**, which was 16 on set B and is now 0.

### The rule that replaced it: anchor first, then claim

The pass calibrates itself before it claims anything.

1. The **anchors** are the library's base ions - the bare halide clusters, the
   protonated urea monomer and dimer, the nitrate core and its first acid rung.
   They are the brightest ions a source makes and share a mass with nothing, so
   they are found in the probe's wide window and say where this spectrum puts
   the reagent's masses.
2. Every other rung, and every satellite, is claimed at the **instrument's own
   precision** (3 ppm Orbitrap, 10 ppm TOF) against a mass corrected by that
   offset, widened by the anchors' own spread where the lock mass jitters. A
   satellite is searched at its parent's measured offset.
3. A parent claim must also clear the probes' intensity floor, because a trace
   sitting on a reagent mass is a coincidence rather than the ion.

The test that separates a reagent ion from an analyte is therefore not how far
a peak sits from the nominal mass, but whether it sits where the sample's own
reagent ions say the reagent is. That keeps set E's bromide ladder, which
really is a uniform -8.6 to -10.2 ppm miscalibration measured across every
rung, and drops the C13H20O4 channels on B and the Br3 ringing skirt on D.

**With no anchor in range the pass searches nominal masses at the instrument
window** - conservative rather than clever. Four of set B's six samples start
at m/z 123, above both the urea monomer and dimer, so they anchor on nothing
and claim nothing; B claims 18 rows, all on its two anchored samples.

Nothing real is lost there, and it is worth saying why, because the obvious
repair is a trap. The peaks the flat window claimed on those four samples were
not reagent rungs: 138.0995 read as `[(urea)2+NH4]+` at +6.6 ppm is the 13C
line of `C9H12O` `[M+H]+`, which this engine assigns itself and already carries
as that ion's `iso_child`, and 181.1052 read as `[(urea)3+H]+` at +4.7 ppm is
the 13C line of `C10H10O2` `[M+NH4]+`, at 0.0 ppm from it and at the 11% a C10
ion predicts. The reference labels both reagent too, out of the same wide
window. A fallback that anchored on two rungs agreeing would have found +4.7
and +6.6 agreeing well enough and claimed two analytes' isotopologues. **Those
four samples hold no reagent ion the library can see, and claiming nothing on
them is the right answer**, so the fallback is deliberately not built.

### The envelope has to reach the floor the pass searches

One more defect the anchored build surfaced. The satellite search asked for an
envelope and then looked for lines in it, but the prediction stopped at the
scoring path's 1% - so the 18O line, 0.411% of a two-oxygen ion and 0.206% of a
one-oxygen one, was never in the envelope at all. On set A that line is the
19th brightest peak of a sample (7e4 counts): once the pre-pass claimed the
urea dimer and Stage A's urea target went with it, the untargeted stage read
the freed peak as ethylene glycol on the urea channel, at candidate tier, on
all six A samples and both anchored B samples. The phantom this step exists to
prevent, one line below where it used to happen.

`predict_isotopes` now takes a threshold (default unchanged, so the scoring
path is untouched) and the pre-pass passes its own satellite floor, which drops
to 1e-3: the two 18O shares sit either side of the old 4e-3, which is not a
distinction the chemistry supports. What protects an analyte is the excess
gate, not the floor. Measured against the build before it: A's reagent rows go
36 to 60 and its same-formula agreement rises 5, with the ethylene glycol rows
gone; C2, F1 and F2 gain rows on the same account, their ladders being
oxygen-bearing too, while D and E - whose claims are mostly bare halide
clusters - do not move.

### What G4 measures, and why the raw number is not it

Of the reference's reagent rows **that name an ion formula** - the ones that
are reagent-cluster identifications - this engine agrees on 48 of 58 (A), 54 of
64 (D), 48 of 55 (E) and 50 of 67 (F1). That is G4a, and at **82%, 84%, 87% and
74% it is below the row's own >= 90% target.** The misses are the anchored
window and the intensity floor doing what they were changed to do, so the
number is reported rather than reached for:

- six of D's ten are `[BrO3]-` claims at +10.8 ppm on a ladder whose anchors
  sit at 0.4-0.9 ppm. A one-bromine ion 10.8 ppm from bromate, on a spectrum
  calibrated to under a ppm, is not bromate by mass, whatever a 12 ppm window
  says;
- the remaining four of D's, seven of E's and most of F1's are traces below
  1e-4 of the base peak - 5e-5 on E, 1-3e-5 on F1 - which an anchored pass
  will not claim in their own right;
- F1's `Br2-` traces then fall to a curated `Br` target in Stage A
  (`source = database`), the same class as set C2's `HBr` below: a
  curated-library question rather than this step's.

Widening either bound to reach 90% would undo the fix this step needed, so the
alternative if the target is to be met as written is to restate G4a's
denominator as the reference's with-ion rows **above the probe floor**. That is
left open rather than decided here.

The rest of the reference's reagent rows on those sets name no formula at all:
198, 281 and 266 peaks. On set D 44% of them sit within 60 mDa of a peak this
engine has claimed, at a median 0.4% of its height - they are the FT ringing
skirt of the enormous Br3 cluster, and the same peaks around set A's
4.9-million-count `[urea+H]+` are ones the reference itself labels `artifact`.
**That class is step 1.5's**, and the roles are kept apart on purpose:
`reagent` means the peak IS a reagent ion, `artifact` means it is an instrument
response to one. The unqualified G4 therefore stays in the gate table, to be
counted against `reagent` or `artifact` once 1.5 lands.

Set A's remaining 22 are the same story one step in: this engine claims the
true `[urea+H]+` at -1.0 ppm and 4.9M counts on every sample, and declines a
second peak 6 ppm away at 5% of that height, which the reference's reagent
window swept up before its own ringing pass could see it. One library ion
claims one peak, and the one it claims is the ion.

### The two remaining reference analytes, and why they stand

**Set F2's ten are the nitrate ladder itself**, which the reference reads as
nitric acid because it has no nitrate cluster library at all. Those are right.

**Set C2's twelve are the labelled reagent's own 14N lines**, at exactly the
mass of unlabelled `NO3-`, which the reference assigns as `HNO3`. The
measurement settles it:

- the lines sit at **0.40-0.44x** the height a 98% pure reagent predicts, so
  the impurity alone over-explains the mass and leaves no budget for ambient
  nitric acid (the implied purity is about 99.2%);
- and they **scale with the number of reagent nitrogens in the cluster** -
  0.83% of the parent on the monomer, 1.76% on the dimer, a ratio of 2.1 -
  which ambient nitric acid cannot do, since its abundance is set by the air
  rather than by how many labelled nitrogens the cluster it sits beside
  contains.

The intensity gate is what adjudicates this in general: a peak more than three
times its predicted impurity height is left for the stages, so a sample with
real nitric acid on top of the impurity keeps it.

That same reference line was also the subject of a defect in the first build:
`_satellite_hits` took the lightest predicted line as the monoisotopic
reference, which for a labelled reagent is the 14N impurity one mass unit
*below* the ion. The ion then became a satellite of its own impurity at 49x its
height, every relative was 50 times too large for the intensity gate to bite,
and the real 14N lines were skipped as if they were the M0. The reference is
now the line the prediction labels `M0`.

### Set C claims nothing, and that is correct

Its acquisition starts at m/z 131 and holds no peak within 100 ppm of any rung
of the nitrate ladder: the source declusters beyond the dimer, the same fact
step 1.2 recorded for carbonate. A window that starts above the ladder cannot
show it. C2 is the same chemistry acquired from m/z 50 and claims 90.9% of its
signal.

### After step 1.5, the satellite claim and the artifact role (2026-09-08)

Branch `step-1.5-satellite-claim-2026.09.08-7db27fd` deployed on the testbed in
prod mode, the in-app engine re-run over all 43 gate samples, compared with the
1.4 numbers measured the same afternoon on the same box, so the two differ only
by this step.

The step's own gate row is G8, isotopologue rows that name no owner, and it is
met: **71 on D, 6 on E, 1 on F1 before, 0 on every set after.** The rest of the
step is one number - how much of each spectrum the engine can say is an
isotopologue of something it committed - and it moves a long way.

| set | peaks | isotopologue rows | of them the reference calls isotopologues too | artifact rows | G8 | committed analytes | assigned tier | same formula |
|---|---|---|---|---|---|---|---|---|
| A uronium | 2,626 | 168 -> **244** | 227 (93%) | 0 | 0 -> 0 | 1,561 -> 1,561 | 1,330 -> 1,327 | 787 -> 786 |
| B uronium | 12,055 | 170 -> **1,139** | 1,102 (97%) | 0 | 0 -> 0 | 1,596 -> 1,596 | 1,372 -> **1,456** | 1,168 -> 1,171 |
| C 15N-nitrate m/z 131 | 1,583 | 152 -> 155 | 109 (70%) | 0 | 0 -> 0 | 1,079 -> 1,079 | 820 -> 817 | 596 -> 596 |
| C2 15N-nitrate m/z 50 | 1,420 | 108 -> 108 | 70 (65%) | 0 | 0 -> 0 | 645 -> 645 | 516 -> 516 | 285 -> 285 |
| D bromide | 5,217 | 619 -> **1,042** | 921 (88%) | 123 | 71 -> **0** | 788 -> **849** | 760 -> **795** | 589 -> **616** |
| E bromide TOF | 3,493 | 49 -> 47 | 3 | 0 | 6 -> **0** | 233 -> 233 | 76 -> 73 | 3 -> 3 |
| F1 bromide TOF | 13,595 | 183 -> 215 | 23 (11%) | 0 | 1 -> **0** | 972 -> 974 | 418 -> 414 | 15 -> 15 |
| F2 nitrate TOF | 8,905 | 84 -> 106 | 12 (11%) | 0 | 0 -> 0 | 1,152 -> 1,152 | 599 -> 594 | 16 -> 16 |

The peaks the new isotopologue rows come from are peaks that were unassigned: B
loses 969 unassigned rows and gains 969 isotopologue rows, D loses 607 and gains
423 isotopologues, 123 artifacts and 61 analytes. The reference confirms 88-97%
of the new rows as isotopologues on the three sets where the class is large.

What that buys beyond tidiness: same-formula agreement rises on D from 589 to
616 and on B from 1,168 to 1,171, B gains 84 assigned-tier rows and D 35,
because an envelope scored against the whole spectrum is scored against the
satellites that were there all along. The signal each engine can account for
rises on B from 89.7% to 90.7% and on D from 88.5% to 90.2%. G1 - assigned-tier
rows the reference does not confirm - is flat everywhere, within 0.3 points on
every set, so the rows gained are confirmed at the rate the existing ones are.
Exactly one peak on the whole gate carried a committed analyte under 1.4 and
carries none now: m/z 424.857 on D, an 842-count `+Br2-` reading at candidate
tier that the reference reads as an isotopologue of a hexachloro compound.

Run time is unchanged: 16-19 s per sample. Enumeration cost scales with the
targets and only the context grew.

#### The envelope was anchored on the wrong line

The first build of this step lost bright peaks on the bromide Orbitrap set, and
the accounting above could not see it: 21 peaks that carried a committed analyte
under 1.4 became plain `unassigned`, 17 of them at assigned tier, while 33 other
peaks gained an analyte - so the role totals moved by one and said nothing had
happened. Four samples' m/z 464.991 at about 7,000 counts was among them.

The cause is older than this step and this step made it systematic.
`match_isotopic_pattern` anchored a predicted envelope on the predictor's first
line, and IsoSpec orders configurations by abundance: for an ion with no
heavy-isotope-rich element that line IS the monoisotopic one, so the assumption
held by accident everywhere except where it costs most. For a `+Br2-` candidate
the first line is the 79Br81Br configuration two mass units above the ion. The
matcher matched that line to whatever small peak sat there - 466.988 at 359
counts, 1.7 ppm off - normalised the envelope to those 359 counts, and then
found the 7,090-count target 3,700% too bright for its own monoisotopic line and
left it unmatched. `score_pattern` still gave that reading 0.936, above the
`+Br-` reading that matched the target and its 13C line at 0.858.
`process_isotopes` then wrote the main row at the anchor's m/z rather than at the
target, and the target got no row at all - after which the new orphan rule
correctly dropped the stray satellite too, so the small peak went unassigned as
well.

Under 1.4 the class was mostly invisible, because the small peak two mass units
up was rarely among the 300 searched peaks. Whole-spectrum context makes it
systematic: every bright peak on a bromide grid has a small peak 1.998 Da above
it.

The fix is to make index 0 mean what three separate places already read it as -
the intensity the envelope is normalised to, `score_pattern`'s "require
monoisotopic detection" guard, and the finder's main row. That line is the
target by construction, because the composition search matched the ion's
monoisotopic mass against the peak's m/z to propose the candidate at all. The
reagent pre-pass had already worked this out for labelled reagents and kept a
private copy of the rule; the two now share one.

#### ...and anchoring it correctly took a requirement away

Moving the monoisotopic line to index 0 separated two requirements that used to
be one row. With the brightest predicted line first, `score_pattern`'s
"require monoisotopic detection" also required the brightest line to be
observed. With the monoisotopic line first it no longer did, and a reading could
stand on its M0 alone - which on a bromide grid is the same phantom arriving
from the other side. The `+Br2-` candidate whose 79Br81Br line should be 1.95
times the target and is not in the spectrum used to swallow the target; now it
won the target instead, as an M0 with no envelope. Between the two builds the
bromide Orbitrap set gained 120 committed analytes, 76 of them `+Br2-` readings,
half of those matching nothing beyond their own line, and one of the 120
agreeing with the reference's formula. `+Br2-` untargeted analytes on the set
went from 17 to 99.

Two lines of logic put it back, and they are worth separating because they are
different statements. The absence test belongs in `score_pattern`: the ion's own
line and the line the prediction leads with both have to be observed, or the
pattern is not evidence. The other belongs in the finder: a candidate whose
pattern scored zero is not committed at all. That second one was implicit
before, because a zero score used to come with an unmatched anchor and
`process_isotopes` wrote nothing; with the anchor on the target the row can
always be written, and only the score says it should not be. Half of the
surviving phantoms carried `fit_score` exactly 0.0 in the ledger, which is the
engine writing down that it had no evidence and committing anyway.

With both, on set D: untargeted analytes 782 under 1.4, 899 anchored, **843**
now; `+Br2-` among them 17, 99, **20**; every one of the 21 peaks the first
build lost has a row again.

The class was invisible because no engine-against-engine number can see it: both
sides of it are one engine's own history. `compare_runs.py` now reads the run
before the latest one on the same engine and tabulates every role change, with
"M0 -> unassigned" as the number to read beside G8. Read it knowing what the
previous run was: against 1.4 it is 0 on every set but D, which has the one peak
above; against the intermediate build it is 49 on D and 12 on E, which is that
build's phantoms being withdrawn.

#### What it costs, counted the way step 1.4 learned to count it

A pass that claims peaks has to be measured against the peaks the reference
calls analytes, not only against its own agreement. Two claims to report.

The satellite claim takes peaks the reference reads as analyte M0s: **+2 on B
and +23 on D** (33 of D's 51 at the reference's assigned tier). Of the 26 peaks
on D newly claimed as satellites in that class, 20 were unassigned in Mascope
before - a residual becoming an isotopologue - and 6 were committed analytes
this step gave up. The disagreement itself is not settled by the gate: the
reference's readings there are nitrogen-rich untargeted fits of its own
(C12H25N3O12, C13H25N3O18), not curated standards.

The artifact pass claims 123 peaks, all on D, 0.69% of that set's signal. The
reference calls 67 of them reagent and 47 artifact - 114 of 123 agreeing that
they are not sample chemistry - 7 unassigned, and **2 it commits an analyte M0
on, both at assigned tier**. Those two sit 29 and 37 ppm from centroids of about
213,000 and 219,000 counts, at 0.48% and 0.26% of their height, which is the
shape the sidelobe rule looks for and both engines agree on the bright
neighbours. That is a reason to think the flag is right and the reference's
analyte is the artifact, but the gate cannot settle it, and it is recorded here
as a cost rather than as a win.

#### The two passes wanted the same peak

The first deployed build died on one sample of set D with the ledger's own
uniqueness constraint: a reagent cluster's weakest satellite - the 81Br line of
`[Br+2xHBr]-` at m/z 240.769, 310 counts - sits in the ringing skirt of the
bromide cluster beside it and was claimed by both pre-passes. The reagent claim
wins, because it is the more specific statement: it names the ion and predicts
the peak's height, where the sidelobe rule only says a peak is small and close
to a big one. The exclusion is applied after the flag rather than before it, so
a reagent peak still serves as a base peak or a mirror partner for the peaks
around it.

Worth keeping: the run's "one row per peak" test existed and passed throughout,
because its fixture has no reagent peaks. A ledger invariant is only tested by a
fixture that can break it.

#### What the FT sidelobe flag still has to find

The artifact half of the step is much smaller than the plan expected, and the
reason is that most of the work is already done upstream. The peak detector
flags sidelobes when it detects peaks, and `mascope_file.io.load_peak_data`
drops what it flagged, so the assignment engine is handed a list they have
already been taken out of. Running the same flag over the engine's own peak
list fires on **nothing at all on seven of the eight gate sets** and on 124
peaks on set D. Set D is not a different instrument from A, C and C2 - it is the
same Orbitrap - so what differs is the intensity the flag reads: the detector
judges a whole file on summed heights, a run judges one sample's time window on
averaged intensity, and a ratio that failed the test there can pass it here.

The role now has a producer and 123 rows on the gate, which is what the step
asked for. The number to watch is not this one, though: the reference labels
43-236 peaks per Orbitrap set as artifacts that Mascope still calls unassigned,
and the flag does not fire on them. Around set A's 4.9M-count urea reagent ion
they sit at -18.3, +16.3, +21.1, +30.0, +40.1, +44.6, -46.8, -49.1 and
-56.1 ppm - offsets whose mirror pairs are 2 ppm apart in magnitude, where the
flag's symmetry tolerance is 1.5 ppm. Reaching them means changing the rule,
not calling it in a new place, and that is not this step.

#### What G6 measures, and what step 1.5 could not reach

G6 counts peaks Mascope commits an analyte M0 on that the reference reads as
part of another ion's envelope. It is unchanged on every set but D, and the
target of at most 10 on A is missed by a factor of eight. The measurement says
plainly why, and it is not the satellite claim:

| set | G6 | the reference's parent ion is outside the grid Mascope searched | what is missing |
|---|---|---|---|
| A | 79 | 78 (99%) | Si in 76, P in 2 |
| B | 54 | 53 (98%) | Si in 43, P in 10 |
| C | 56 | 39 (70%) | Si in 35, Br in 4 |
| C2 | 24 | 18 (75%) | Si in 18 |
| D | 75 | 52 (69%) | Cl4-Cl6 against a Cl0-2 cap in 51 |
| F1 | 16 | 2 (12%) | P, S2 |
| F2 | 7 | 7 (100%) | P in 5, Cl in 3, S2 in 2 |

The reference's isotope labels on set A are 29Si 27, 30Si 23, 29Si+30Si 14,
13C 9, 13C+29Si 5: these are the silicon lines of cyclic siloxanes - the
`C6H18O3Si3` / `C8H24O4Si4` / `C10H30O5Si5` column-bleed series - and the
uronium grid is `C1-40 H0-90 N0-5 O0-15 S0-2`. Mascope cannot build the parent,
so it cannot attach the satellite to it, and what it does instead is fit a
carbon-rich phantom to each silicon line. No amount of pattern context reaches
that: an envelope can only claim a line if the ion whose envelope it is has been
committed.

Set D is the same story in a different element, and it is where the trade shows.
G6 there rises from 59 to 75 - the 61 analytes the step adds include peaks the
reference reads as chlorine isotopologues - but 52 of the 75 are ions the grid
cannot build, Cl4 to Cl6 against a Cl0-2 cap, against 34 of 59 before. Counting
only the peaks whose parent Mascope could have named, D's G6 is 25 before and 23
after. The set gains 61 analytes, 27 more same-formula agreements and 35
assigned-tier rows for that, with G1 within 0.3 points, so the trade is worth
making; but on a set whose chemistry is outside the grid, more committed
analytes means more of them landing where the reference sees an envelope.

The claim to resist here is that a rule keyed on the spacing alone would fix it.
It separates the class cleanly - 100% of A's 79 sit one isotope spacing above a
brighter peak, against 4.3% of the 762 rows both engines agree on - and adding
the intensity-ratio test the plan describes sharpens it further, to 81% of the
class against 0.3% of the agreed rows. But what such a rule can honestly write
is the question. Naming the peak an isotopologue of the parent means writing a
`29Si` line of a formula with no silicon in it, which is a different false
statement from the one it replaces; and refusing to commit anything, leaving the
peak unassigned, costs 26 of set C's 591 agreed rows for 23 of its 56. The rule
is only sound once the parent can be named, which is the same fix: silicon,
phosphorus and a wider halogen cap in the Stage B grid, step 2.5b's known window
per source. Decision 11 takes that reading.

#### The elections that moved, and what they moved to

Scoring an envelope against the whole spectrum changes which candidate wins some
peaks: 10 of set A's ~1,560 committed analytes, 15 of B's, 29 of D's. On B and D
it is an improvement (+3 and +11 agreements gained against 0 and 4 lost). On A
it is not, and "net -1" understates it: two of the ten replaced a reading the
reference shared, and **none of the ten new winners agrees with the reference** -
seven are a different formula, three sit on peaks the reference does not commit.
**All ten are odd-electron formulas** (C16H9O5, C27H29S, C21H13S), where two of
the readings they replaced were. Odd-electron ions are 7% of A's untargeted
analytes, 10% of B's and 17% of D's, so this is a small move within a standing
population rather than a new class.

Decision 9 keeps the radical reading as a tie-break rather than a filter, so a
better-scored radical wins here by design. What ranks them is the v1 pattern
score, which charges almost nothing for a line it predicted and did not find -
step 2.1 has the worked examples and owns the fix. Recorded here so the move is
not re-discovered as a regression of this step.

### After step 1.6, the cap lifted and the grid enumerated once (2026-09-08)

Branch `step-1.6-cap-and-window-2026.09.08-e9fe159` deployed on the testbed in
prod mode, all 43 gate samples re-run, compared against the same peaky runs. The
before-column is each sample's newest run from the previous build, addressed by
the absence of `search_scope` on its config rather than by recency, because the
gate was run twice on the new one.

`mz_precision_ppm` needed no work: step 1.1 already defaults it from the
resolved profile's instrument class (`resolve_mz_precision_ppm`), and both the
per-sample and the batch path already pass the sample's instrument type into
the resolution. What was left of this step was the cap, and what the cap cost.

| set | analyte M0 | assigned tier | G1 | G2 same formula | reference Assigned we commit no analyte on | G6 |
|---|---|---|---|---|---|---|
| A uronium | 1,561 -> 2,070 | 1,327 -> 1,774 | 42.0 -> 41.5% | 76.3 -> **95.6%** | 20.3 -> 0.7% | 79 -> 107 |
| B uronium dense | 1,596 -> 9,148 | 1,456 -> 8,098 | 20.9 -> 24.3% | 20.7 -> **95.2%** | 78.1 -> 1.0% | 54 -> 460 |
| C nitrate | 1,079 -> 1,115 | 817 -> 846 | 40.3 -> 41.5% | 86.0 -> **87.3%** | 8.3 -> 7.0% | 56 -> 57 |
| C2 nitrate broad | 645 -> 645 | 516 -> 516 | 55.2 -> 55.2% | 68.3 -> 68.3% | 20.2 -> 20.2% | 24 -> 24 |
| D bromide | 849 -> 2,378 | 795 -> 2,229 | 24.2 -> 37.3% | 37.6 -> **80.1%** | 58.7 -> 9.6% | 75 -> 328 |
| E bromide TOF | 233 -> 1,233 | 73 -> 534 | 98.6 -> 99.4% | 7.1 -> 10.7% | 71.4 -> 46.4% | 0 -> 8 |
| F1 bromide TOF | 974 -> 8,252 | 414 -> 4,207 | 97.1 -> 99.4% | 14.0 -> 21.0% | 73.0 -> 36.0% | 16 -> 57 |
| F2 nitrate TOF | 1,152 -> 6,173 | 594 -> 3,695 | 98.1 -> 99.2% | 21.7 -> 21.7% | 69.6 -> 50.0% | 7 -> 33 |

**G5 is met on every set: 35,496 peaks were never searched, now none are.** On
the gate row's own definition - peaks the reference calls *Assigned* - that is
5,304 pooled (A 186, B 4,180, C 7, C2 0, D 843, E 12, F1 60, F2 16), which
reproduces the 190 / 4,181 / 8 the row records from the step-0 engine. Counting
every peak the reference commits an analyte on at any tier it is 8,928. The metric is reconstructed rather
than recorded - the stage takes the most intense of what the pre-passes and
Stage A left, so ranking that remainder by intensity reproduces exactly the set
it saw - and a run now records the scope it searched under, so a later reading
does not have to reconstruct anything.

The last column but one counts reference-Assigned peaks Mascope commits no
analyte on, which is not the same as leaving them blank: on D 9.6% carry no
analyte and 6.1% carry nothing at all, the 3.5 points between being peaks
Mascope reads as somebody's isotopologue. A and B are within a tenth of a point
either way; D and F2 are where the two readings part.

**G2 clears its stage-1 target on A, B, C and D for the first time**, and on B
it is the difference between measuring the engine and measuring its cap: 20.7%
of the reference's Assigned peaks recovered with the same formula becomes 95.2%,
because 78% of them were peaks Mascope never looked at. The same reading holds
on D (37.6 -> 80.1%) and A (76.3 -> 95.6%). C moves barely and C2 not at all,
which is the control: their spectra were already inside the cap, so the step
could not touch them, and it did not.

A per-sample run records what it searched under `search_scope` on its own
config; a batch run has one config for many samples and no per-sample row to
stamp, so its search reports the anchors the cap left unsearched in the batch
result's counts instead. Both paths also log it.

**C2 is byte-identical before and after** - same analyte count, same tiers, same
G1, same G6. Nothing there was ever past the cap. A step that changed a set it
had no business changing would show up here.

#### What it costs

**G1 rises on the sets that gained most**: B 20.9 -> 24.3%, D 24.2 -> 37.3%, and
the three TOF sets from 97-99% to 99%. It falls slightly on A (42.0 -> 41.5%) and
rises a point on C. All four Orbitrap sets stay inside the stage-1 target of
<= 45%; C2 misses it at 55.2%, exactly as it did before this step. The rise is
what searching the faint end of a spectrum should do to a disagreement rate: the
peaks the cap was hiding are the ones both engines find hardest, and the
reference commits on fewer of them too.

**G6 rises, and part of the old number's smallness was the cap.** A 79 -> 107,
B 54 -> 460, D 75 -> 328. As a share of Mascope's own analyte rows the picture
is narrower: A 5.1 -> 5.2%, B 3.4 -> 5.0%, D 8.8 -> 13.8%. Most of these rows
sit at assigned tier - 332 of B's 460, 275 of D's 328 - so they are not a
low-confidence fringe.

**The rise has two mechanisms, not one, and only the first is step 2.5b's.**
Classifying each row step 1.6 added by what the searched element box could have
built, using the reference's own parent ion:

| set | rows added | parent outside the searched grid | parent on the grid |
|---|---|---|---|
| A | 28 | 20 | 8 |
| B | 406 | 293 | 113 |
| D | 255 | 90 | 165 |

The off-the-grid column is the chemistry gap step 1.5 recorded - the reference reads
Cl4-Cl6 envelopes on D and Si-bearing ones on A, the grid caps chlorine at two
and has no silicon, and an envelope Mascope cannot build is an envelope whose
lines it explains one at a time. **The on-the-grid column is not that.** There the
parent is a neutral the grid can build, and on many of those rows Mascope reads
the parent with the reference's own formula - 100 of B's 113, 32 of D's 165,
5 of A's 8 - and still does not claim the line. Counted through the reference's
own `owner_peak_assignment_id`, which is what makes them exact: matching a
parent by formula and label spacing instead over-counts D, because 17 of its
on-grid rows carry the label "M0" on a satellite row under the compound-envelope
convention, and any M0 of that formula in the sample then matches.

Three things stop the claim, all in the envelope logic rather than the grid: the
line is never predicted, because `ISOTOPE_ABUNDANCE_THRESHOLD` is 1%
and IsoSpec drops the 18O line of an ion with four or fewer oxygens (0.8%) and
the 15N line of one with one or two nitrogens; the line is predicted but
observed outside the 40% intensity tolerance; or it is inside the tolerance and
unclaimed anyway. A peak nobody predicts, or nobody accepts, cannot be claimed -
and under the cap it was never searched either, so it stayed blank and cost
nothing. Now it is searched, and a 3 ppm whole-spectrum search finds a formula
there nearly every time.

**So decision 11's premise no longer describes the residue.** "The residue needs
the grid rather than the envelope logic" was true of the 1.5 measurement; after
1.6 the residue has a grid part, which step 2.5b owns, and an envelope part,
which nothing owns yet. Two candidates for the second, for the plan owner:
step 2.1, where a detectability-gated fit would predict a faint line and judge
it rather than dropping it below a fixed 1% cutoff, and step 2.4, where an M0
committed on a peak that a committed neighbour's envelope predicts a line for
could carry that as a tier reason and be kept off assigned tier. Recorded here
so it is not re-discovered as a regression of 2.5b.

#### G3 and the mass error do not move, which is the reassuring part

Six to eight times as many committed formulas, and the chemistry of them is the
same chemistry. Formulas carrying five or more nitrogens: A 0.6 -> 1.0%, B 2.7
-> 2.7%, and 0.0% on every other set. Carbon-free formulas are identical to the
row before and after on all eight sets (A 6, C2 12, F1 29, F2 26, none
elsewhere) - the profile grids require at least one carbon, so an untargeted row
cannot be carbon-free and the count is Stage A's either way.

B's 2.7% is over the stage-1 target of <= 1% and was over it before this step;
it is a standing miss of G3 on that set, not something the cap was hiding. A at
1.0% sits exactly on the target having been under it.

Committed mass error, MAD, Mascope against the reference: A 0.215 / 0.171,
B 0.300 / 0.290, C 0.169 / 0.137, C2 0.311 / 0.213, D 0.305 / 0.260 ppm. All
four Orbitrap sets and C2 stay inside the <= 0.35 ppm target with the faint end
of the spectrum now in the population, which is the result that could most
easily have gone the other way: the peaks the cap was hiding are the weakest
ones, and a mass error that held there is the grid finding real formulas rather
than the nearest arithmetic. The TOF sets read 1.199 (E), 0.557 (F1) and 0.780
(F2); F1 and F2 are now better than the reference's own 1.009 and 0.919.

#### Run time, which is why the cap could go at all

Per-sample wall clock on the testbed, whole run including both stages and the
ledger write:

| set | peaks searched per sample | before | after |
|---|---|---|---|
| A | 414 | 3s | 2s |
| B | 1,993 | 4s | 5s |
| C | 310 | 2s | 1s |
| C2 | 218 | 1s | 1s |
| D | 838 | 5s | 6s |
| E | 1,142 | 4s | 13s |
| F1 | 2,233 | 8s | 33s |
| F2 | 1,750 | 3s | 8s |

**A and C search five to eight times as many peaks and finish faster than they
used to.** That is the grid rework, not the cap: the composition search walked
the element-count tree from the root for every peak, using that peak's window as
the pruning bound, so one spectrum walked the same tree hundreds of times. The
compositions an element box allows do not depend on the peak - only the window
into them does - so they are enumerated once, sorted by mass, and each peak is
answered by bisecting into them. Measured offline on the two densest samples
with the whole spectrum searched: the TOF bromide sample 133 -> 39 seconds, the
Orbitrap uronium one 27 -> 5.6.

The grid is built in ascending mass bands rather than whole. A TOF spectrum
reaches m/z 1,097 and the bromide box holds 5.5 million compositions over that
range; banding bounds what is resident to one band, and the band width is found
by halving until it fits and then carried forward. A box too wide for even one
band falls back to a window per peak, which is what the search did everywhere
before, so the fallback is slow rather than wrong. Answers are unchanged: a
differential test against the old recursion over the gate's five real grids and
nine masses agrees exactly, ordering included, with one deliberate exception -
where a peak has more candidates than `max_result_rows` allows, the ones kept
are now the closest in mass rather than whichever the walk reached first.

F1 at 33 seconds (36 worst case) is the slowest sample on the gate and the only
one over ten. It is 2,233 peaks at a 10 ppm window with three ionization
channels, so it enumerates the widest grid over the widest mass range and then
scores an envelope for each of 2,233 targets. Under a minute, which is the bar
this step set for itself; what remains is isotope matching and the heuristic
rules rather than the search, and step 2.1 owns that path.

#### G8 did not move, and the rows that are left are Stage A's

The untargeted stage writes no ownerless isotopologue row on any set, before or
after - step 1.5's coherence count holds with six to eight times as many peaks
searched. The 5 rows on C, 20 on C2 and 6 on F1 that the pooled count shows are
Stage A's, unchanged by this step and unrelated to it: two curated targets
sharing a peak, the loser's children staying. They are reported inside the total
by design (see the metric's note) and are step 2.5's to fix.

### The stage 1 gate: engine 0.4.0 (2026-09-08)

Branch `step-1.7-stage-1-gate-2026.09.08-d3dda34` deployed on the testbed in
prod mode, all 43 gate samples re-assigned, compared against the same peaky
runs. The only source change since the 1.6 head is
`PEAK_ASSIGNMENT_ENGINE_VERSION`, and the gate confirms it: **every field of
every set's summary is identical to the 1.6 run, and no peak changed role on any
of the 43 samples.** The bump does what a version is for - a 0.3.0 result and a
0.4.0 one are told apart wherever a run is shown - and nothing else.

The baseline column is the step-0 engine on the same samples against the same
reference runs; bold marks a stage-1 target met.

| set | analyte M0 (assigned tier) | G1 | G2 same formula / same ion | N >= 5 | mass error MAD |
|---|---|---|---|---|---|
| A uronium, Orbitrap sparse | 1,631 (1,535) -> 2,070 (1,774) | 73 -> **41.5%** | 39 -> **95.6 / 97.2%** | 13 -> **1.0%** | 0.20 -> **0.22 ppm** |
| B uronium, Orbitrap dense | 1,631 (1,587) -> 9,148 (8,098) | 57 -> **24.3%** | 12 -> **95.2 / 96.1%** | 15 -> 2.7% | 0.20 -> **0.30 ppm** |
| C 15N-nitrate | 1,078 (787) -> 1,115 (846) | 99 -> **41.5%** | 18 -> **87.3** / 87.9% | 17 -> **0.0%** | 1.13 -> **0.17 ppm** |
| C2 15N-nitrate, broad window | 779 (667) -> 645 (516) | 72 -> 55.2% | 64 -> 68.3 / 68.3% | 24 -> **0.0%** | 0.55 -> **0.31 ppm** |
| D bromide | 947 (818) -> 2,378 (2,229) | 68 -> **37.3%** | 17 -> **80.1** / 82.3% | 31 -> **0.0%** | 0.37 -> **0.31 ppm** |
| E bromide, TOF | 409 (206) -> 1,233 (534) | 99 -> 99.4% | 7 -> 10.7 / 25.0% | 22 -> 0.0% | 1.56 -> 1.20 ppm |
| F1 bromide, multi-scheme TOF | 1,449 (968) -> 8,252 (4,207) | 98 -> 99.4% | 18 -> 21.0 / 23.0% | 26 -> 0.0% | 0.94 -> 0.56 ppm |
| F2 nitrate, multi-scheme TOF | 1,452 (1,195) -> 6,173 (3,695) | 99 -> 99.2% | 44 -> 21.7 / 21.7% | 38 -> 0.0% | 0.73 -> 0.78 ppm |

G1 and G2 gate on the Orbitrap sets. The TOF rows are recorded because the
reference itself commits almost nothing there - 28, 100 and 46 Assigned peaks in
3,493, 13,595 and 8,905 - so agreement measures the shared v1 scorer rather than
either engine, and step 2.1's sibling task is what gives those sets a reference
at all.

#### Metric by metric

- **G1 `<= 45%`: met on A, B, C and D, missed on C2** at 55.2%. The four that
  clear it land at 41.5, 24.3, 41.5 and 37.3%, inside the 45-50% band the
  offline configuration experiment reached and below it on B and D.
- **G2 met on A, B, C and D for the same formula; the same-ion bound is met on
  A and B only.** Same formula against `>= 80%` (A, C, D, C2) and `>= 70%` (B):
  95.6, 95.2, 87.3 and 80.1%, with C2 at 68.3%. Same ion against `>= 95%`: 97.2
  and 96.1% on A and B, 87.9% on C, 82.3% on D, 68.3% on C2.
- **G3 met on four of five for nitrogen, and completely for carbon.** Formulas
  with five or more nitrogens fall from 13-38% to 1.0% on A and 0.0% on C, C2, D
  and every TOF set; B's 2.7% is the uronium context's own N cap of 5 showing
  through and is the one Orbitrap set above the `<= 1%` bound. Every carbon-free
  formula left anywhere is a Stage A row from the curated library - NH3 on A,
  HBr and HS3 on C2, HNO3 with its dimer, HIO3 and Br on F1, HNO2, H2SO4 and
  HIO3 on F2 - and **the untargeted stage produces none on any of the 43
  samples**, because the organic grid floors carbon at 1. Zero off the
  allowlist, which is what the target asks.
- **G4 is below its target where it measures this engine's pass, and on three
  sets it does not measure it at all.** Of the reference's reagent rows that
  name an ion: 48 of 58 on A (82.8%), 14 of 24 on B (58.3%), 54 of 64 on D
  (84.4%), 48 of 55 on E (87.3%) and 50 of 67 on F1 (74.6%). Step 1.4 recorded
  why D's, E's and F1's misses are the anchored window and the intensity floor
  doing what they were changed to do; A's and B's are read below, and neither is
  a missing library entry. On C, C2 and F2 the reference's reagent rows are a
  different claim entirely: bromide traces (`Br-`, `Br2-`, `BrO3-`) on the two
  nitrate sets, where this engine's pre-pass claims the nitrate ladder the
  reference does not label, and on F2 the ladder itself. The raw 0% there is not
  this engine's pass being wrong.
- **G5 met: zero on every set.** No peak the reference commits an analyte on
  went unsearched, and every run records the scope it searched under.
- **G6 read and not gated, per decision 11.** 107 on A, 460 on B, 57 on C, 24 on
  C2, 328 on D, 8 on E, 57 on F1, 33 on F2. Its two mechanisms and their homes
  in steps 2.1, 2.4 and 2.5b are in the step 1.6 section above.
- **G8 met: the untargeted stage leaves no ownerless isotopologue row on any
  set.** The 5 rows on C, 20 on C2 and 6 on F1 in the pooled count are Stage A's
  and are step 2.5's.
- **Mass error `<= 0.35 ppm` MAD on an Orbitrap: met on all five**, at 0.22,
  0.30, 0.17, 0.31 and 0.31 ppm - while committing five and a half times as many
  analytes on B and two and a half times as many on D as the baseline did. C's
  1.13 -> 0.17 ppm is the largest single move any metric makes in stage 1.

#### What A's and B's reagent misses actually are

Neither set's misses are a library entry this engine lacks; both are the metric
counting the reference's label against a reading this engine makes at the
instrument's own precision.

**B's eight are a label 5-7 ppm off on a spectrum calibrated to a third of a
ppm.** `_urea_clusters` carries the ammonium series from n = 2 and the
protonated ladder to n = 6, so both ions the reference names are in the library.
Four of B's six samples acquire from m/z 123, so neither anchor is in the window
and the base peak is 4.5e5 counts at m/z 158.15 rather than a reagent ion. On
those four the reference labels m/z 138.0995 `[(urea)2+NH4]+`, whose ion mass is
138.0986 - 6.6 ppm away - and m/z 181.1052 `C3H13N6O3`, which is the protonated
trimer at 181.1044, 4.7 ppm away, not the ammoniated one. Those four samples
carry a -0.24 ppm median error with a 0.33 MAD, and this engine reads the same
two peaks at 0.05-0.17 and -0.16 to -0.27 ppm: the M+1 line of a curated C9H12O
it commits in each sample, and the 13C line of an untargeted C10H10O2. At that
precision the reagent label is the doubtful reading. A library edit would change
nothing either way, because `match_reagent_clusters` claims at the instrument's
precision against its anchors and would not have claimed a peak 7 ppm off with
an anchor present. B's other two are second centroids beside the monomer, 10 ppm
below its mass at 5e-4 of the claimed peak.

One thing the same evidence says in passing, for the channel gate's owner rather
than this one: on those four samples the probe that switched the ammonium
channel on is that same m/z 138.0995 peak, found 6.6 ppm inside the 20 ppm probe
window. The channel is real on this source - the reference commits 1,648 main
peaks through it - but the record now says the channel was opened on a peak this
engine itself reads as an M+1 line.

**A's ten are second centroids beside the source's own ions.** Eight sit at m/z
61.0400 and 62.0437, 6.9 and 13 ppm above the `[urea+H]+` this engine claims and
its 13C line, at 4-5% of them; two sit at m/z 121.071, 9-10 ppm below the claimed
dimer at 0.3-0.4% of it. The reference labels them reagent under the parent's
formula; this engine leaves them unassigned, and the artifact pass - whose class
this is - flags nothing on A. Whether the reagent pass should claim a second
centroid of its own anchor is step 1.4's question, not this gate's.

#### F2's recovery falls, and the fall is the pre-pass being right

F2 is the one set whose G2 goes backwards, 44 -> 21.7%, and the whole of it is
ten peaks. The reference has 46 Assigned peaks there; ten of them are the
nitrate ladder itself, `NO3-` and `[HNO3+NO3]-`, which it reads as nitric acid
because it carries no nitrate cluster library. The reagent pre-pass now labels
those ten `reagent`, so they leave the numerator, and 10 of 46 is 21.7 points.
Step 1.4 judged that claim correct when it first appeared, and the same ten
peaks are the whole difference here. Set C2 carries the same class - 12 of its
287, the same two ions - which is 4.2 points of its G2 shortfall.

#### What stage 1 does not reach, and where it goes

Of the reference's Assigned peaks this engine does not recover as the same ion,
**about half carry an element or a count the searched grid cannot build**:

The last column counts every element or count a formula needs and the grid
lacks, so a formula short of two adds to both - which is why F1's and F2's
columns come to more than their row counts.

| set | reference Assigned | not recovered | of them off-grid | what the grid is missing |
|---|---|---|---|---|
| A | 949 | 27 (2.8%) | 13 (48%) | P 12, Si 1 |
| B | 5,373 | 209 (3.9%) | 70 (33%) | Si 38, P 32 |
| C | 527 | 64 (12.1%) | 34 (53%) | Si 19, F 9, Br 6 |
| C2 | 287 | 91 (31.7%) | 24 (26%) | Si 12, F 12 |
| D | 1,595 | 283 (17.7%) | 146 (52%) | Cl past the 0-2 cap 119, Si 12, F 12, P 2, I 1 |
| E | 28 | 21 (75.0%) | 15 (71%) | P 8, F 3, I 2, Cl 1, Si 1 |
| F1 | 100 | 77 (77.0%) | 42 (55%) | P 15, S past the cap 14, I 6, F 6, Cl 3 |
| F2 | 46 | 36 (78.3%) | 21 (58%) | P 8, F 7, Cl 5, I 3, Br 1 |

The elements are the same list every time - silicon, phosphorus, fluorine,
iodine, and chlorine or sulphur past the profile's cap - and it is the list step
2.5b's window per source exists to open. It is also the grid half of G6: the
absent parents that cost these agreements are the ones whose isotopologues get a
phantom formula fitted to them instead. On the grid the rest is this engine
reading the peak differently - 13 of A's 27, 92 of D's 283 - or leaving it open,
1 and 45. Over the whole of D's 283 another 56 are peaks it reads as somebody's
isotopologue, and every one of those is off the grid too. What is left on the
grid is the judgement layer stage 2 builds.

#### The numbers step 2.1 has to move

Recorded, not gated, because they are the before-column of the next stage.

**Four in five committed analytes rest on the monoisotopic line alone.** Of the
untargeted M0 rows the stage commits, the share owning no isotopologue row at
all is 89.9% on A (1,811 of 2,014), 80.0% on B, 88.4% on C, 89.8% on C2, 46.1%
on D and 95.9-98.8% on the three TOF sets. 1,549 of A's 1,774 assigned-tier rows
and 6,266 of B's 8,098 are untargeted commitments standing alone. D is the exception because a bromide adduct
puts a strong 81Br line beside every ion it makes. A row standing alone is not by
itself wrong - a faint ion's heavy-isotope line can sit under the noise - but it
is the population where the v1 fit has nothing but the mass to judge, and where
a line predicted and not found costs a candidate almost nothing. That is what
step 2.1's detectability gate and SNR-aware fit are for.

**The odd-electron share of untargeted M0 rows** is 7.9% on A, 12.3% on B, 15.8%
on C, 22.4% on C2, 23.6% on D, 34.0% on E, 25.7% on F1 and 21.8% on F2, against
1.0% of the reference's committed formulas on A. Decision 9 keeps the radical
reading as a tie-break rather than a filter, so these are won on score, and the
score is step 2.1's.

**The elections the whole-spectrum context flipped** toward a candidate with
fewer observable lines were counted at step 1.5 as a build-to-build transition -
10 on A, 15 on B, 29 on D. The count is not repeatable at a stage gate, because
step 1.6 changed which peaks are searched at all, but the peaks are, and step
2.1's Verify names them. Re-derived from the 1.4 and the final 1.5 run of each
sample, which are both still in the store, they are 10, 15 and 29; on the
stage-1 build 53 of the 54 still carry their 1.5 reading, all 25 of A's and B's
among them, and the one that moved is a D peak now read as an isotopologue.

| set | m/z | samples | before 1.5 | since 1.5, and still | tier now |
|---|---|---|---|---|---|
| A | 299.0792 | 4 | C10H18O8S | C16H9O5 | candidate |
| A | 358.1133 | 1 | C15H16O9 | C21H13S | assigned |
| A | 403.2326 | 5 | C18H24N5O2 / C20H34O8 | C27H29S | candidate |
| B | 355.0697 | 1 | C12H18O10S | C10H8N5O4S | assigned |
| B | 403.2324 | 1 | C18H24N5O2 | C20H34O8 | assigned |
| B | 432.1321 | 1 | C9H17N5O11 | C18H22O9S | candidate |
| B | 445.1199 | 1 | C15H16N2O10 | C22H14N3O2S | assigned |
| B | 521.1348 | 1 | C15H18N5O12 | C24H23O10S | candidate |
| B | 522.1356 | 3 | C28H17N2O3S | C21H19NO11 | assigned |
| B | 581.1675 | 1 | C31H24N2O2S2 | C39H20S | assigned |
| B | 581.1679 | 5 | C33H23O9 / C39H20S | C18H24N4O14 | assigned |
| B | 582.1684 | 1 | C16H29N2O15S | C38H29O2S2 | assigned |

All ten of A's are odd-electron formulas, which is the population above seen
from the other side: A's standing odd-electron rows number 160.

#### Run time

Wall clock per sample, from the run records rather than the gate's poll loop:

| set | median | worst |
|---|---|---|
| A uronium | 1.5 s | 2.2 s |
| B uronium dense | 5.2 s | 6.4 s |
| C nitrate | 1.0 s | 1.7 s |
| C2 nitrate broad | 0.7 s | 0.8 s |
| D bromide | 5.8 s | 6.8 s |
| E bromide TOF | 13.4 s | 13.4 s |
| F1 bromide TOF | 32.7 s | 36.4 s |
| F2 nitrate TOF | 7.9 s | 9.3 s |

Every sample of the gate is searched whole, and the slowest of the 43 takes 36
seconds. The whole gate - 43 samples, four at a time - re-runs in about seven
minutes, which is what lets it be run on every change rather than once a stage.

### After step 2.1, the v2 fit for Stage B (2026-09-09)

Measured on `step-2.1-v2-fit-stage-b-2026.09.09-ddafa70`, all 43 samples
re-assigned and compared against the same reference runs the stage-1 gate used.
Every field of the table below is identical on the two builds before it, which
carried the same scoring and wrote less about it. The engine version stays
0.4.0: a stage bumps once, at its own gate (2.7).

Two things changed and they do different work. The finder now ranks candidates
with the v2 fit at the sample's own mass width and on the file's own per-peak
signal-to-noise, which decides *which reading of a peak wins*; and every reading
it commits is measured again as an ion through the Stage A chain, which decides
*what the row is worth*. The first moved few elections on the Orbitrap uronium
and nitrate sets and most of them on the TOFs. The second moved almost every
tier.

It is one fit either way (decision 12), so only one number reaches a row: a
second score on it would name a version that no longer differs. The run says
what it judged at - a `pattern_scoring` block beside the resolved profile and
the search scope, carrying the width and offset the search used, whether that
width was fitted on this sample or the instrument class stood in, and how many
known ions it was fitted from. The first round of this step turned on exactly
that question and no run could answer it. What the row
records beside the fit is the noise the finder's detectability gate judged its
absent lines against - `provenance.base_snr` - because that is the difference
between a fit scored against the noise and one scored against abundance alone,
and nothing else on the row would say which happened.

Read back from the store, all 32,133 untargeted M0 rows of the 43 gate runs
carry it, so the finder's fit was the SNR-aware one on every set rather than
assumed to be. What it says about the ledger is worth its own line: the median
committed peak has a signal-to-noise of 6.4 (quartile 2.5, ninth decile 61.7),
so on half the rows the detectability gate can only charge for a line predicted
above about 45% of the parent.

What each set was judged at, read back off the runs:

| set | width from | anchors | sigma ppm | offset ppm |
|---|---|---|---|---|
| A | fitted | 13-15 | 0.53-0.64 | -0.15 to -0.03 |
| B | fitted | 10-15 | 0.53-0.57 | -0.09 to 0.00 |
| C | fitted on three, the class on two | 5-8 | 0.58-0.66 | -0.12 to 0.00 |
| C2 | fitted | 11-12 | 0.66-0.79 | -1.57 to -1.48 |
| D | the class | 1-2 | 0.58 | 0 |
| E | the class | 5-7 | 3.04 | 0 |
| F1 | fitted | 19-25 | 3.66-7.69 | +1.07 to +2.48 |
| F2 | fitted | 20-26 | 1.61-4.11 | +0.05 to +2.36 |

Three things in that table are new because nothing recorded them before. Set C
is not scored alike across its own five samples - three fit a width and two fall
back on seven anchors and five - so a C number pools two treatments. C2 carries
a -1.5 ppm offset that is now subtracted before scoring rather than charged to
every candidate. And F1's fitted width ranges from 3.7 to 7.7 ppm between
samples of one batch, which is the spread its committed mass error shows and the
first thing step 2.2 will have to explain.

That does not leave those rows untiered - it leaves them tiered on the mass, at
their own width. Of the 15,114 untargeted M0 rows on the five Orbitrap sets,
9,942 sit below signal-to-noise 30, and their tiers ladder cleanly by mass
error: 0.234 ppm median for the assigned ones, 0.558 for the candidates, 1.499
for the below-assignability ones, with only 5% of the assigned ones owning a
line at all. In the bright band the envelope decides instead: 58% of the
assigned rows there own one. Both are the measurement working, on the evidence
each peak actually offers, and step 2.2's `mass_z` is what makes the first of
them explicit per row.

| set | G1 | G2 same formula / same ion | G3 N>=5 | G6 | G8 | odd-electron: all / assigned | MAD |
|---|---|---|---|---|---|---|---|
| A uronium | 41.5 -> **35.4** | 95.6 -> 95.6 / 97.2 -> 97.2 | 1.0 -> 1.2 | 107 -> 103 | 0 | 7.9 -> 7.7 / 5.3 -> **3.3** | 0.22 -> 0.215 |
| B uronium dense | 24.3 -> **18.9** | 95.2 -> 95.2 / 96.1 -> 96.1 | 2.7 -> 2.8 | 460 -> 432 | 0 | 12.3 -> 12.0 / 8.7 -> **5.5** | 0.30 -> 0.298 |
| C nitrate | 41.5 -> **34.7** | 87.3 -> 87.7 / 87.9 -> 88.2 | 0.0 | 57 -> 49 | 0 | 15.8 -> 16.3 / 20.2 -> **17.3** | 0.17 -> 0.168 |
| C2 nitrate broad | 55.2 -> **40.1** | 68.3 -> **74.9** / 68.3 -> **74.9** | 0.0 | 24 -> 18 | 0 | 22.4 -> 23.3 / 25.5 -> **23.0** | 0.31 -> **0.25** |
| D bromide | 37.3 -> **21.0** | 80.1 -> 80.6 / 82.3 -> 82.8 | 0.0 | 328 -> 314 | 0 | 23.6 -> 23.3 / 22.2 -> **12.8** | 0.31 -> 0.307 |
| E bromide TOF | - | - | 0.0 | 8 -> 7 | 0 | 34.0 -> 32.8 / 33.5 -> **30.4** | 2.03 |
| F1 bromide TOF | - | - | 0.0 | 57 -> 61 | 0 | 25.7 -> 24.9 / 27.5 -> **19.7** | 0.96 |
| F2 nitrate TOF | - | - | 0.0 | 33 -> 33 | 0 | 21.8 -> 21.5 / 29.7 -> 29.9 | 1.22 |

G1 falls on every Orbitrap set, by 6 points on A and C and by 15-16 on C2 and
D. B reaches the stage-2 target of 20% (18.9) and D is a point off it (21.0).
G2 is unchanged on A and B, up on C and C2, and up half a point on D. G5 stays
at zero and so does G8. The untargeted stage still writes no carbon-free
formula on any of the 43 samples, so G3's carbon half stays met; its N >= 5
half moves by a tenth of a point on A and B - 21 rows of 2,070 becoming 24 of
2,062, and 243 of 9,148 becoming 252 of 9,077 - which is A crossing its 1%
bound on three rows, and the three are one ion. They are m/z 403.233 on three
of A's six samples, read as C18H24N5O2 through the urea adduct (C19H29N7O3+)
where the alternative is C20H34O8 through `+H+` (C20H35O8+). The two ions are
0.013 ppm apart, so no mass separates them and no envelope does either - both
predict the same lines to within the same rounding. It is a coin toss committed
at assigned tier, which is exactly step 2.4's candidate-density reason; and an
ion carrying seven nitrogens through a urea adduct is exactly step 2.3's
reagent-N rule. The same peak is one of step 1.5's flipped elections, so the
ranking is what brought it back - correctly, on the evidence available - and
what is missing is a reason to doubt a winner nothing can separate. The mass error is flat or better on
every Orbitrap set.

#### What "assigned" costs now

The tier is read from the seeded re-score, so a row keeps its formula and loses
its confidence when the fit says the envelope is incomplete.

| set | assigned | candidate | below assignability |
|---|---|---|---|
| A | 1,774 -> 1,361 | 277 -> 384 | 19 -> 317 |
| B | 8,098 -> 5,411 | 1,048 -> 2,571 | 2 -> 1,095 |
| C | 846 -> 717 | 269 -> 262 | 0 -> 121 |
| C2 | 516 -> 397 | 123 -> 142 | 6 -> 100 |
| D | 2,229 -> 1,031 | 145 -> 829 | 4 -> 508 |
| E | 534 -> 424 | 609 -> 452 | 90 -> 650 |
| F1 | 4,207 -> 2,299 | 3,779 -> 2,160 | 266 -> 4,468 |
| F2 | 3,695 -> 3,011 | 2,366 -> 2,103 | 112 -> 1,610 |

A third of B's assigned rows and half of D's are gone, and what they had in
common is the population step 1.7 measured: 80-99% of committed analytes stand
on the monoisotopic line alone. Where the noise says a 13C line should have
been visible and it is not, the fit now charges for it, and the row lands a
band lower with the formula still on it.

#### The fit distributions separate, which is the point

Median fit of a committed M0 row, by what the reference makes of the same peak:

| set | same formula | different formula | separation |
|---|---|---|---|
| A | 0.972 -> 0.917 | 0.962 -> 0.736 | 0.010 -> 0.181 |
| B | 0.951 -> 0.854 | 0.952 -> 0.725 | -0.001 -> 0.129 |
| C | 0.975 -> 0.955 | 0.902 -> 0.662 | 0.073 -> 0.293 |
| C2 | 0.854 -> 0.941 | 0.966 -> 0.758 | -0.112 -> 0.183 |
| D | 0.945 -> 0.817 | 0.925 -> 0.568 | 0.020 -> 0.249 |
| E | 0.890 -> 0.731 | 0.718 -> 0.607 | 0.172 -> 0.124 |
| F1 | 0.936 -> 0.947 | 0.892 -> 0.541 | 0.044 -> 0.406 |
| F2 | 0.919 -> 0.912 | 0.881 -> 0.829 | 0.038 -> 0.083 |

Before this step the two classes were the same number: on B a row the reference
contradicts scored a thousandth *higher* than one it confirms, and on C2 a
tenth higher. The fit was a measurement that did not measure the thing anyone
reads it for. It separates on seven of the eight sets now, by 0.13 to 0.41,
and the tier follows: on D, 91.4% of the rows the reference contradicts were
'assigned' and 34.1% are.

E is the exception, and it is the set with 28 reference-Assigned peaks to
judge on.

#### What the finder's ranking moved

Step 1.5 recorded twelve ions whose election the whole-spectrum context flipped
toward a candidate with fewer observable lines, 25 readings across A's and B's
samples and 29 more on D. On the 25 themselves, re-derived from the 1.4 and 1.5
runs: A 6 back to the reading 1.5 displaced, 3 still on the 1.5 one, 1 to a
third; B 6, 8 and 1; and of D's 29, 8 back, 13 still there, 5 to a third and 3
now isotopologues. Read instead across every sample of both sets that holds one
of those twelve peaks - 72 readings - 39 are back, 15 have moved to a third, and
18 still carry the 1.5 one.

The readings still on the 1.5 election are the weak ones: all but one stand
alone, at 0.9-9.7k counts, and 12 of the 14 are candidate or below. Where the
envelope can speak the election reverted; where the peak is too faint for any
line to be detectable the mass alone decides and the radical stays, which is
2.2's gate and 2.4's density reason rather than this step's. (The 1.4 and 1.5
runs themselves are gone from the testbed - its nightly retention keeps a
handful per sample - so those per-row counts are the last re-derivation of
them.)

A's m/z 299.079 is the plan's own example, and it is back to C10H18O8S on all
six samples, with the 34S line claimed on four of them. It is tiered
`below_assignability` at a fit of 0.22-0.34: the reading whose predicted line
the spectrum holds, said to be a weak one, because its 13C line should have
been visible at this peak's signal-to-noise and is not, and because 0.9 ppm is
three sigma on this instrument. Both terms agree, which is what the fit was
supposed to buy.

On the Orbitrap sets the elections barely move otherwise - 24 formulas on A, 93
on D - because a 3 ppm search window on a spectrum accurate to 0.3 ppm rarely
offers two candidates the mass cannot separate. On the TOFs it moves 132
formulas on E, 2,189 on F2 and 5,307 on F1: there the window is ten sigma, the
v1 score scaled its mass term by a fixed 5 ppm, and the fitted width is the
first thing that has told those candidates apart.

#### The TOF sets become assignable

| set | committed M0 | isotopologue rows | reference: analytes / reagent ions |
|---|---|---|---|
| E | 1,233 -> 1,526 | 62 -> 213 | 119 / 336 |
| F1 | 8,252 -> 8,927 | 343 -> 611 | 698 / 333 |
| F2 | 6,173 -> 6,724 | 117 -> 194 | 755 / 10 |

Set E is the verify this step wrote for itself: both engines committing more
than the reagent ions. This engine now commits 1,526 analytes against 48
reagent rows on E, up from 1,233, and the readings behind them were ranked at
this TOF's own width rather than at the fixed 5 ppm v1 scaled every instrument
by. The reference still commits 119 analytes against 336 reagent ions, and it
will until step 2.1b lands - the sibling task now has a row of its own in the
status table, because until peaky scores a TOF the way this engine does, the
stage-2 gate has no reference to read E's or F's G1 and G2 against. Agreement
on F1 fell from 34 of its 100 reference-Assigned peaks to 24, and that number
means nothing while the two engines score at different widths.

The TOF numbers here are therefore this engine's own, and they are more rows at
a wider spread: MAD 1.20 -> 2.03 ppm on E, 0.56 -> 0.96 on F1, 0.78 -> 1.22 on
F2, with the median moving onto the instrument's own offset (F1 +0.00 -> +2.00,
F2 +0.02 -> +1.04). That is what committing at the sample's fitted centre looks
like when the sample has an uncorrected offset, and step 2.2's `mass_z` is what
makes it visible per row rather than only in a pooled MAD.

#### What did not move, and where it goes

**The G6 rows whose parent this engine reads with the reference's own formula**
- decision 11's rider, the part of G6 that is not a grid gap - shrink but only
just: B 101 -> 83, D 32 -> 30, A 6 -> 6. The deeper envelope reaches some of
them; the rest are lines the envelope predicts and the intensity gate refuses,
which is the neighbour rule step 2.4 owns.

**The odd-electron share of all committed M0 rows** is flat (A 7.9 -> 7.7, B
12.3 -> 12.0, D 23.6 -> 23.3). The share of the *assigned* ones is where it
moved - D 22.2 -> 12.8, F1 27.5 -> 19.7, A 5.3 -> 3.3 - which is the honest
reading of what this step changed: a radical still wins the peaks where its
mass is the closest, and it stops being called confident.

Read the other way round, that is decision 9's revisit condition answered. Of
the untargeted M0 rows, the share reaching assigned tier is now 29% for an
odd-electron neutral against 70% for an even one on A, 27 against 64 on B and
24 against 50 on D - and level on the nitrate sets, 69 against 64 on C and 62
against 63 on C2. A radical is demoted where a radical is rarely the chemistry
and not where it is, which is what the decision asked the fit to show. The
population it wins is what did not move, and 2.4's density reason is what would
move that.

#### Two rounds, both about how far a mass error is allowed to be from zero

The first build of this step regressed set D: G2 80.1 -> 76.8, the mass error
0.31 -> 0.369 ppm and past its target, 252 peaks re-elected to formulas a
median 1.2 ppm off. The cause was the fallback, not the fit. The mass width was
Stage A's fitted spread where Stage A had eight matched rows to fit one, and
D's curated library holds two targets per sample, so D fell back - to the match
tolerance, 5 ppm, on a spectrum accurate to 0.3. At that width every candidate
the 3 ppm window enumerated fits equally well and the election falls to the
envelope alone. The instrument class now states its own accuracy beside the
window it already stated (0.3 ppm for an Orbitrap, 3 for a TOF), and D's
numbers came back: G1 21.0, G2 80.6, MAD 0.307.

The second round was the requirement step 1.5 added, meeting the score that
replaced the one it was written against. A reading missing the line its
prediction leads with used to score zero, so it could never rank first and "the
top candidate is not evidence" did mean "no reading of this peak is". The v2
fit charges that absence instead of refusing on it, so a reading with an
excellent mass and no envelope can rank above one whose envelope is all there -
and the peak was then refused on the first without the second being looked at.
It cost D 173 committed M0 rows and the satellites they owned. The peak now
goes to the best-ranked reading whose required lines are present.

#### Run time

| set | median | worst |
|---|---|---|
| A uronium | 1.5 -> 2.4 s | 2.2 -> 2.6 s |
| B uronium dense | 5.2 -> 10.3 s | 6.4 -> 10.8 s |
| C nitrate | 1.0 -> 6.3 s | 1.7 -> 6.7 s |
| C2 nitrate broad | 0.7 -> 1.1 s | 0.8 -> 1.2 s |
| D bromide | 5.8 -> 7.3 s | 6.8 -> 9.0 s |
| E bromide TOF | 13.4 -> 15.1 s | 13.4 -> 15.7 s |
| F1 bromide TOF | 32.7 -> 40.2 s | 36.4 -> 43.9 s |
| F2 nitrate TOF | 7.9 -> 12.5 s | 9.3 -> 14.0 s |

Two passes now instead of one - the envelope reaches deeper on a bright peak,
and every committed reading is measured again as an ion - and the slowest of
the 43 samples takes 44 seconds. The whole gate still re-runs in about six
minutes.

### After step 2.1b, the reference re-based (2026-09-09)

Two changes, one in each repository, and no engine change at all. The store says
so rather than the text: all 43 mascope runs carry the same run id after the
publish as before it, the same engine version and the same completion times of
08:45-08:48Z, and every engine-side number in the comparison - committed M0,
assigned-tier counts, mass-error MAD - is identical on both sides. What moved
here is the stick.

The box was redeployed to `step-2.1b-shared-mass-accuracy-2026.09.09-ab02285`
before any of it, because the reference cannot read a peak's noise until the
endpoint sends it. That build changes no engine behaviour - the fit's move is a
refactor its tests pin - and no assignment was launched on it.

- **Mascope, #2093.** The mass-accuracy fit moves into `mascope_tools` as
  `composition.mass_accuracy`, gaining `fit_mass_accuracy` for a caller whose
  anchors are not a match frame and `scoring_sigma_ppm` for the width a score is
  judged at. The instrument class gains the third of its three windows,
  `INSTRUMENT_MATCH_TOLERANCE_PPM`, pinned by a backend test to the targeted
  matcher's own class defaults. And the peaks endpoint carries each peak's
  `signal_to_noise`.
- **peaky, branch `epic/v2-fit-reference`.** `score_candidates_local` scores with
  `score_pattern_v2` and a `PatternScoring` built per sample, the peaks frame
  carries the noise estimate into the score, the envelope is anchored on the
  ion's own line, and a published run's config records `score_version` and the
  same `pattern_scoring` keys the engine stamps on its own - so a v1 reference
  and a v2 one are told apart in the store rather than by date. The branch pins
  the library by revision, because none of that API is on PyPI; its merge into
  main is the coordination point with Mascope's release.

**A premise of the hand-over was wrong, and it was load-bearing.** The peaks
endpoint did not return `signal_to_noise`: `PeakData` has carried it since step
2.1, but `get_sample_peaks` built its response from six keys and that was not one
of them - the engine reads the estimate through `extract_peaks`, an internal
path. A reference scoring without it would have judged every faint line by
predicted abundance alone, which is the decision step 2.1 turned on. Checked
against the deployed build before and after: all 43 gate samples now carry one.

**A defect this gate caught, twice, in the same place.** The library's fit
returns the offset and the width together, and below its anchor minimum it
returns `(0.0, None)` - a zero offset that reads exactly like a measured one.
The first round collapsed on C2: committed M0 429 -> 147, the reference's own
Assigned tier 287 -> 69, G1 40.1 -> 97.7%, G2 74.9 -> 14.5%. C2's six anchors sit
between -1.95 and -0.72 ppm; the source is 1.2 ppm low, scoring as if it were on
calibration charged that to every candidate, and the rival whose own error
cancelled the instrument's won the peak. A median and a robust spread are not the
same measurement - the spread needs a distribution, the median needs fewer points
- so the reference states the offset from the anchors even where the width falls
back, and records the two sources separately.

The second round showed what an anchor actually is. On a bromide TOF sample the
three anchors were -10.5, -10.4 and -2.8 ppm: an anchor is a peak the server
matched to a known species within the instrument's MATCHING window, 15 ppm on a
TOF and five times its accuracy, so two mis-matches to the same wrong species
carried the median. That run was scored at -10.4 ppm for a source the engine
measures within 0.3 ppm of calibration, and its median committed fit fell to
0.020 - the reference went on committing 47 peaks, because its passes commit on
mass gates and channel evidence with the fit as one input among several, which is
worth knowing when reading any of its numbers. The minimum is five anchors: two
bad ones cannot carry a median of five.

**The engine has the same trap and escapes it here by luck of the anchors.** It
falls back to mu = 0 wherever Stage A matched fewer than eight known ions, which
on this gate is D (1-2 anchors), C on two samples (5-8) and E (5-7). The
committed mass errors say those three sources sit within 0.3 ppm of calibration,
so nothing is charged; the one set with a real offset, C2, is also the one where
Stage A had 11-12 anchors and fitted it. That is a property of these 43 samples,
not of the rule. The wide anchor window has the same effect on the width: on F1
the anchors admitted at 15 ppm put the median at +2.67 ppm against +1.80 at
5 ppm, and the fitted sigma at 2.2-7.7 ppm. Both are step 2.2's.

**Every reference run above comes from one commit**, peaky at 5fa9b59 with the
library pinned at fc25575da, because the first rounds of A, C, C2 and F2 predated
the anchor rule and were re-run at the head. The re-run reproduces what it
replaced: on A, 12 of 2,626 ledger rows differ, all of them a commentary line and
two of them a series unit, where two equally-scoring series anchors tie and the
tie falls the other way between processes; no formula, tier or score moves.

C's first round did move, and not for that reason. Its five ledgers came back
with `ts_disposition` empty on every row: peaky's batch time-series step produced
nothing and the run finished without saying so, so none of its background
demotions happened and its Assigned tier read 545 where the re-run gives 445. The
committed set and every score were identical - only the tier moved - and the run
that lost the step was one of five peaky jobs sharing a workstation with a few
hundred MB of memory free. The numbers here are the re-run's. **A batch set's
ledgers are worth checking for a populated `ts_disposition` before its numbers
are read**; a single-sample assign, which is how E, F1 and F2 are run, has no
batch time series at all and the column is simply absent.

**What the reference is judged at.** Its anchors are the sample's own targeted
matches; the width is fitted above eight of them and the instrument class's
below, the offset above five and zero below.

| set | anchors | width | sigma ppm | offset ppm | window ppm |
|---|---|---|---|---|---|
| A | 11-12 | fitted | 0.70-0.94 | -0.33 to 0.00 | 5 |
| B | 5 on four samples, 8 on two | class / fitted | 0.58 / 0.65-0.68 | -0.00 to +0.23 | 5 |
| C | 2 | class | 0.58 | none | 5 |
| C2 | 6 | class | 0.58 | -1.18 to -1.23 | 5 |
| D | 3 | class | 0.58 | none | 5 |
| E | 1-3 | class | 3.04 | none | 15 |
| F1 | 14-17 | fitted | 2.28-7.68 | +1.07 to +2.67 | 15 |
| F2 | 17-21 | fitted | 1.26-1.60 | +0.11 to +0.88 | 15 |

peaky counts an anchor only where it can mass the ion itself, so it sees base
ions where the engine fits over every matched isotopologue row: 11-12 against
13-15 on A, 5-8 against 10-15 on B, 2 against 5-8 on C, 6 against 11-12 on C2,
3 against 1-2 on D, 1-3 against 5-7 on E, 14-17 against 19-25 on F1, 17-21
against 20-26 on F2. The reference therefore falls back to the class more often
than the engine does. Widening its anchors to matched isotopologues is the
obvious follow-up, and step 2.2 will want the same question asked of the engine's.

**The reference's own ledger.** Committed M0, its own tier split, the share of
shared commits whose neutral formula changed, and its committed mass error.

| set | committed M0 | its Assigned | its Candidate | formula changed | its MAD ppm |
|---|---|---|---|---|---|
| A | 1192 -> 1002 | 949 -> 784 | 243 -> 218 | 20 of 991 (2.0%) | 0.171 -> 0.150 |
| B | 7614 -> 4680 | 5373 -> 1933 | 2241 -> 2747 | 527 of 4498 (11.7%) | 0.290 -> 0.174 |
| C | 753 -> 681 | 527 -> 445 | 226 -> 236 | 4 of 670 (0.6%) | 0.137 -> 0.121 |
| C2 | 429 -> 381 | 287 -> 264 | 142 -> 117 | 10 of 368 (2.7%) | 0.213 -> 0.167 |
| D | 2108 -> 1554 | 1595 -> 800 | 513 -> 754 | 92 of 1299 (7.1%) | 0.260 -> 0.189 |
| E | 119 -> 139 | 28 -> 26 | 91 -> 113 | 24 of 114 (21.1%) | 1.158 -> 1.124 |
| F1 | 698 -> 1034 | 100 -> 123 | 598 -> 911 | 306 of 676 (45.3%) | 1.009 -> 0.995 |
| F2 | 755 -> 827 | 46 -> 69 | 709 -> 758 | 219 of 748 (29.3%) | 0.919 -> 0.934 |

The Orbitrap reference commits less and the TOF reference commits more, which is
what a fit that charges a missing line and stops scaling mass by a fixed 5 ppm
should do. What it drops is the thinly-evidenced end: on A, 8.5% of the peaks it
stopped committing own an isotope line against 25.0% of the ones it kept (B:
21.8% against 30.8%), and its committed mass error improves on every Orbitrap set
- by 40% on B. On the TOF sets it commits half again as many analytes on F1 and
its formulas change on a third to a half of the shared peaks, which is what a
reference scored at 5 ppm on an instrument that measures to 2-8 was always going
to do once it was scored at its own width.

**Its tier bands were re-read and left alone.** They are bands on a number that
now moves: v1 put a correct assignment near 0.95 whatever its envelope did, so
`tau_good = 0.8` admitted almost every commit. On A and C the Assigned share of
committed rows barely shifts (79.6 -> 78.2%) and on C it gives back five points
(70.0 -> 65.3%); on the dense set B it falls 70.6 -> 41.3%, because 61% of B's
committed rows now score at or above 0.8 where nearly all of them did before. That is the band starting to do work
rather than a band in the wrong place, and B's Assigned tier still agrees with
this engine on 92.8% of its peaks, so the numbers below are read with the bands
where the stage-1 reference had them.

**The re-based gate.**

| set | G1 | G2 formula / ion | G2 n | G4 | G5 | G6 | G8 |
|---|---|---|---|---|---|---|---|
| A | 35.4 -> 40.2 | 95.6 -> 93.8 / 97.2 -> 96.2 | 949 -> 784 | 82.8 (48 of 58) | 0 | 103 -> 74 | 0 |
| B | 18.9 -> 44.2 | 95.2 -> 92.8 / 96.1 -> 94.8 | 5373 -> 1933 | 58.3 (14 of 24) | 0 | 432 -> 295 | 0 |
| C | 34.7 -> 35.4 | 87.7 -> 90.6 / 88.2 -> 90.6 | 527 -> 445 | 0.0 (0 of 29) | 0 | 49 | 0 |
| C2 | 40.1 -> 41.8 | 74.9 -> 77.7 / 74.9 -> 77.7 | 287 -> 264 | 0.0 (0 of 33) | 0 | 18 | 0 |
| D | 21.0 -> 21.6 | 80.6 -> 80.4 / 82.8 -> 82.1 | 1595 -> 800 | 46.9 -> 36.0 | 0 | 314 -> 149 | 0 |
| E | 100.0 -> 99.8 | 10.7 -> 15.4 / 25.0 -> 26.9 | 28 -> 26 | 14.3 (48 of 336) | 0 | 7 -> 6 | 0 |
| F1 | 99.2 -> 99.1 | 17.0 -> 13.0 / 19.0 -> 14.6 | 100 -> 123 | 15.0 -> 15.5 | 0 | 61 -> 94 | 0 |
| F2 | 99.2 -> 99.2 | 23.9 -> 15.9 / 23.9 -> 15.9 | 46 -> 69 | 0.0 (0 of 10) | 0 | 33 -> 30 | 0 |

G4 is the gate's own definition - of the reference's reagent rows, the ones this
engine calls reagent OR artifact - and only D moves, because only there does this
engine read some of them as ringing rather than as reagent. Its denominator moves
with the reference: the re-scored reference labels 344 reagent rows on D against
262, so 123 of 262 becomes 124 of 344. Everywhere else the reference's reagent
set is unchanged and so is the share.

**G1 rises and the disagreement falls, and those are two different facts.** G1
counts an assigned-tier row as unconfirmed unless the reference reads the same
formula, so three different things are inside it: the reference reads a different
ion, the reference reads the same ion under another neutral and adduct - which no
spectrum can separate - or the reference does not commit at all. The three sum to
G1:

| set | contradicts | same ion, other split | does not commit | = G1 |
|---|---|---|---|---|
| A | 1.8 -> 1.2 | 3.0 -> 3.3 | 30.6 -> 35.6 | 35.4 -> 40.2 |
| B | 5.4 -> 4.0 | 2.4 -> 7.7 | 11.1 -> 32.5 | 18.9 -> 44.2 |
| C | 2.4 -> 0.3 | 1.1 -> 0.7 | 31.2 -> 34.4 | 34.7 -> 35.4 |
| C2 | 4.3 -> 1.3 | 0.3 -> 0.3 | 35.5 -> 40.3 | 40.1 -> 41.8 |
| D | 8.2 -> 6.3 | 3.8 -> 4.1 | 8.9 -> 11.3 | 21.0 -> 21.6 |
| E | 3.5 -> 4.2 | 0.7 -> 0.7 | 95.8 -> 94.8 | 100.0 -> 99.8 |
| F1 | 6.3 -> 8.9 | 0.0 -> 0.1 | 92.9 -> 90.1 | 99.2 -> 99.1 |
| F2 | 9.0 -> 10.1 | 0.2 -> 0.2 | 90.0 -> 88.9 | 99.2 -> 99.2 |

On every Orbitrap set the share of this engine's assigned rows the reference
CONTRADICTS falls - by a third on A, by seven-eighths on C, by two-thirds on C2,
by a quarter on B and D - while the share it is silent on rises by as much or
more. G1 reads worse because it counts silence as failure.

B's middle column is the exception worth a sentence: the re-scored reference
moved its adduct reading there, from 253 to 530 same-ion-other-split rows among
the peaks both engines call M0. Those are readings no spectrum can separate - the
same ion written as a different neutral and adduct - and which of the two an
engine writes is decision 9's policy rather than a measurement. On the uronium
sets that policy is exactly what the reagent-N isobar makes hard, which is step
2.3's.

**The reference's own score now separates its right answers from its wrong ones,**
which is the same measurement step 2.1 made on the engine: over the peaks both
engines call M0, the median reference fit of a row they agree the formula of,
less the median of one they disagree on. The n on each side is what makes it a
measurement or not.

| set | before | after | n agreed / disagreed, after |
|---|---|---|---|
| A | +0.089 | +0.174 | 880 / 48 |
| B | +0.040 | +0.044 | 3452 / 553 |
| C | +0.066 | +0.181 | 559 / 27 |
| C2 | -0.051 | +0.052 | 274 / 29 |
| D | +0.053 | +0.132 | 1044 / 219 |
| E | +0.036 | -0.068 | 6 / 56 |
| F1 | -0.047 | +0.016 | 37 / 739 |
| F2 | -0.058 | -0.047 | 42 / 664 |

C2's separation was NEGATIVE before - the v1 reference scored its contradicted
rows higher than its confirmed ones, which is a scorer telling you nothing - and
three of the four Orbitrap sets roughly double. B is flat, and it is the set
whose commitment fell hardest: what it dropped was the population the separation
was measured over. On the TOF sets there is nothing to read: E's agreed side is
six rows, F1's thirty-seven and F2's forty-two, against hundreds of
disagreements, so the sign of those three numbers is noise rather than a finding.
What the TOF rows do say is the count itself - the two engines agree on 6, 37
and 42 peaks.

**On the TOF sets the fit is necessary and not sufficient.** F2's 664
disagreements against 42 agreements are mostly a choice of channel: 117 peaks
this engine reads through `+NO3-` the reference reads through `+CO3-`, 112
through `-H+`, and on 111 more both choose `+NO3-` and disagree about the
neutral. At 1-4 ppm on three channels the mass does not decide, and neither
engine has the corroboration to. That is step 2.3's cross-channel work and step
2.4's candidate density, not this step's.

**What the re-base answers.** The stage-2 bounds still bind and are not close:
G1 <= 20% is met by no set (A 40.2, B 44.2, C 35.4, C2 41.8, D 21.6), and G2's
>= 85% formula bound is met on A, B and C but not C2 (77.7) or D (80.4), its
>= 95% ion bound only on A (96.2). The re-base does not move the target; it makes
the number honest, because until now every G1 and G2 since step 2.1 compared a v2
engine against a v1 reference.

One thing the re-base does change is how G1 should be read from here. A
conservative reference is silent on a third of this engine's assigned rows on A,
C and C2 and on a third of B's, so a G1 of 20% now requires the engine to be
confirmed on rows where the reference commits nothing at all. Reading the
contradiction share beside G1 - the middle column above - is what keeps steps
2.2 to 2.6 measurable; whether the gate's own definition should change is the
plan owner's call, not this step's.

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
9. **A same-ion family is ranked on its neutral before its mechanism** (taken
   2026-09-08 with step 1.3, after the gate refused this note's own rule). The
   policy written for step 1.3 - the mechanism carrying the most mass is the
   reading - is right where the two readings are equally chemistry, and wrong
   where one of them is not chemistry at all: on the nitrate set it read 159
   deprotonated acids as carbonate adducts of odd-electron radicals, because
   carbonate carries 60 Da more than a lost proton. So the first key is that
   the neutral should be a closed-shell molecule, and the mass rule decides
   among the readings that key does not separate - which is most of them, since
   it separates two readings only when the fragment between them has a
   half-integer DBE. It stays a tie-break and never a filter: a radical that is
   the only reading of its ion is still committed, which is what a nitrate
   source measuring RO2 requires. The evidence is the reference's own
   behaviour, not a prior: on the build that motivated the change its neutral
   is closed-shell in every one of the 390 split readings whose DBE the
   comparison computes, and in every reading the two engines agree on. Revisit
   if a chemistry turns up where the radical reading is the common one.
   Measured after step 1.5 (2026-09-08): 7% (A) to 37% (E) of the untargeted
   M0 rows per set carry an odd-electron neutral. Some of that may be
   chemistry on the nitrate sets; on the uronium and bromide sets it is the
   v1 score's - the readings step 1.5's context added won by predicting lines
   the score does not charge for missing - so the number is step 2.1's to
   move before this decision is revisited.
10. **A reagent row names its ion and no analyte, and is kept by ordering
    rather than by a lock** (taken 2026-09-08 with step 1.4). Three parts, and
    each is a choice the note's own wording did not settle.

    *No `assigned_formula`.* A reagent cluster's composition is known exactly,
    so it is tempting to record it as the assignment - the reference engine
    does. Here it would be read as an analyte: the consensus votes over members
    that carry a formula, so a reagent cluster would enter every cross-sample
    formula vote it touched. The `ion_formula` is recorded and the analyte slot
    is left empty, which makes the row inert everywhere an analyte is counted.
    The tier follows from that and is `unassigned`, meaning exactly what it
    says - no analyte was assigned to this peak - and it is also the tier the
    import path's coherence rule requires of a formula-less row, so the engine
    writes a shape it would accept back. What says the peak is explained is the
    role, so a reader measuring residual signal must key on the role, not on
    the tier.

    *No lock.* peaky locks a reagent peak because its passes run over one
    mutable ledger. Here the pre-pass runs before either stage and its peaks
    are removed from both stages' inputs, so there is nothing to lock against.
    The cost is that the pre-pass wins any collision with a curated target
    outright, which is why library membership is drawn as narrowly as it is.

    *Satellites are reagent rows that name no owner.* Owner linkage models one
    thing in this ledger - an isotopologue naming the M0 analyte it belongs to
    - and the import path enforces it, refusing a reagent row that names an
    owner. Rather than widen a published contract as a side effect of this
    step, the satellite carries the reagent role (so G4 counts it, as the
    reference engine also labels these reagent) and records its parent as
    provenance. Revisit if the inspector needs to collapse a reagent envelope
    structurally.

11. **G6 is judged after step 2.5b, not at the stage-1 gate** (taken
    2026-09-08 with step 1.5). The metric counts peaks Mascope commits an
    analyte M0 on that the reference reads as part of another ion's envelope,
    and the stage-1 target was at most 10 on A. Step 1.5 left it at 79, and
    the measurement says why: for 78 of the 79 the reference's parent ion
    carries an element the uronium grid does not search - silicon in 76,
    phosphorus in 2 - because they are the 29Si and 30Si lines of the cyclic
    siloxane column-bleed series, and an envelope can only claim a line whose
    ion has been committed. A rule keyed on the isotope spacing alone
    separates the class (81% of it against 0.3% of the rows both engines
    agree on) but cannot write anything true while the parent has no name:
    calling the peak a 29Si line of a silicon-free formula is a different
    false statement, and refusing to commit costs 26 of set C's 591 agreed
    rows for 23 of its 56. So the parent has to be named first, which is
    step 2.5b's known window per source. Until then the stage-1 gate reads G6
    and records it, the target of at most 10 on A is 2.5b's to meet, and the
    stage-2 target of at most 5 stands.

    *Rider (taken 2026-09-08 with step 1.6).* With every peak searched the
    residue has two parts. The grid part - the reference's parent ion outside
    the searched box: 20 of the 28 rows 1.6 added on A, 293 of B's 406, 90 of
    D's 255 - is what this decision read, and 2.5b still owns it. The envelope
    part has the parent on the grid, on most rows committed by Mascope with
    the reference's own formula (100 of B's 113, 32 of D's 165), and the line
    unclaimed all the same: either the predictor never emits it, because
    `ISOTOPE_ABUNDANCE_THRESHOLD` drops any line under 1% - the 18O line of an
    ion with four or fewer oxygens, the 15N line of one with one or two
    nitrogens - or the 40% intensity tolerance refuses it. A peak nobody
    predicts, or nobody accepts, cannot be claimed, and a whole-spectrum
    search at 3 ppm then finds a formula for it nearly every time. Two owners:
    step 2.1 retires the cutoff with the detectability-gated fit, so a faint
    line is predicted and judged rather than dropped, and step 2.4 gives an M0
    committed on a peak that a committed neighbour's envelope predicts a line
    for a tier reason and keeps it off assigned tier. The target of at most 10
    on A after 2.5b is read on the grid part; the envelope part is read at the
    stage-2 gate, where 332 of B's 460 and 275 of D's 328 rows sit at assigned
    tier today.

12. **One fit score, computed once, on the real signal-to-noise** (taken
    2026-09-09 with step 2.1). The premise was always one way to score an
    assignment: the fit-score reference names v2 the engine's scoring for
    both stages, and its own section is titled why v2 replaced v1. Two exist
    because v2 was wired in beside v1 under the "coexist, don't replace"
    principle: Stage A adopted it; Stage B inherited the finder's v1, which
    ranks candidates inside the library on a frame with no signal-to-noise;
    the legacy Match tab kept its older per-isotopologue score behind a
    switch whose default keeps it. What the split costs is measured above -
    cause 2, one tier band meaning different things to each stage (the
    comment in `config.py`), a TOF unassignable under a fixed 5 ppm,
    P(correct) refused on Stage B rows because the curve was fitted on v2 -
    and one thing it mislabels: every Stage B row is stamped `score_version`
    2 while its fit is v1, contained only because calibration admits Stage A
    rows until 3.3. So after 2.1 the engine computes one fit, v2, with the
    real per-peak signal-to-noise from the filestore, in the finder's
    election and in the re-score, and v1's number is carried nowhere - not
    in provenance for audit, as this step first said. The feature is in
    production nowhere, so there is no run to audit against, and a second
    score on a released row would confuse more than it explains; the 0.4.0
    runs in the store are the before. `score_pattern` itself stays in the
    library while peaky pins it (the sibling task moves peaky to v2) and
    while the goldens harness uses it as the comparator behind the AUC and
    calibration numbers; the library is public, so its removal is a
    deprecation after both, not a delete. The Match tab's switch is outside
    this plan: flipping its default or retiring it with the tab is a product
    call.

13. **The reference is re-based once, before the mass gate** (taken
    2026-09-09 with step 2.1b). The gate compares the engine to peaky, and
    peaky scores with the library's `score_pattern`: through stage 1 both
    engines scored with v1, and since step 2.1 the engine scores with v2
    while the reference does not, so a G1 or G2 read after 2.1 measures the
    scorer difference as well as the assignments. Two orders were open: the
    mass gate first, verified on intrinsic metrics where the reference
    cannot read, then the reference and one re-base of every number since;
    or the reference first, so that 2.2 to 2.5 are each measured against
    the stick the stage-2 gate will use. The second costs one re-base of
    the post-2.1 table against runs that are in the store today and touches
    no engine code; the first costs a re-read of every step in between. The
    reference sharing the engine's scorer is the stage-1 condition restored,
    not a new circularity: what the gate measures is what the score does
    not decide. The stage-2 bounds were set against the v1 reference and
    are read again against the re-based numbers before 2.2 is measured.

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
