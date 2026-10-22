`ULTRA TRACE MASCOPE - DESIGN DOC - ASSIGNMENT QUALITY PLAN`

# Closing the assignment quality gap: the work, step by step

- **Status:** building
- **As of:** 2026-10-22
- **Tracked in:** [Assignment quality plan](https://linear.app/ultra-trace/project/assignment-quality-plan-553f4854192c) (Linear, MAS-37 to MAS-73)

## Status

Stages 1 and 2 are on `develop`: PR #2077 merged on 2026-09-24 as a
fast-forward of 304 commits, the epic branch `epic/assignment-quality` is
gone, and the feature stays behind `peak_assignment = false` until it is
switched on. Stage 3 steps land as PRs straight into `develop`, one per
step; merged so far are 3.1 (#2197), 3.1c (#2205), 3.1d (#2211), 3.2
(#2198), 3.3 (#2218), 3.3b (#2203, #2204), 3.3c (#2221), 3.3e (#2217),
3.3f (#2220), 3.4b (#2212), 3.4d (#2223) and 3.4g (#2379); open are 3.3d,
3.4, 3.4c, 3.4e, 3.4f, 3.4h, 3.5, 3.5b, 3.6 and the 3.7 gate. Their
baseline is the last stage 2 round (step 2.8, engine 0.5.0, tiering rule
set 6) and, for the chamber dataset's sets G to J, the chemist's reading
of 2026-09-24 (decision 21). Stage 4 has not started. Of milestone 5, the
reference's 0.10 review, step 5.5 is built (#2377) and the rest are
planned. Every planned step is an issue in the Linear project Assignment
quality plan (MAS-37 to MAS-73; step 5.3 is MAS-68, in progress). The
measurement log and the specifications of the completed steps are in
[`assignment_quality_plan_log.md`](assignment_quality_plan_log.md); the
table below names each step's PR, its merge date and one line on its state.

| step | PR | merged | state |
|---|---|---|---|
| 0 - gate, epic branch, design commit | #2077 | 2026-09-24 | done: testbed, comparison tool, baselines A-F, epic branch |
| 1.1 - assignment profiles (library presets, resolution, stamping) | #2078 | 2026-09-07 | measured: G3 met, mass-error target met on all four Orbitrap sets |
| 1.1 fix - finder: deprotonation charge and labelled reagent mass | #2079 | 2026-09-07 | merged: found by the 1.1 gate run |
| 1.1 fix - finder: the labelled reagent's atom in ion formulas | #2080 | 2026-09-07 | merged: the label reaching the ion string is what pyteomics could not parse |
| 1.2 - opportunistic adduct channels | #2081 | 2026-09-07 | measured: fingerprint gate works and refuses sodium; carbonate settled on the nitrate set |
| 1.3 - same-ion tie policy in the finder | #2082 | 2026-09-08 | measured: the step's target met on A (47 -> 87% same formula) |
| 1.4 - reagent-cluster pre-pass | #2086 | 2026-09-08 | measured: 60 peaks carry 81.2% of set A's signal; no reference analyte taken |
| 1.5 - isotopologue claim and ringing artifacts | #2088 | 2026-09-08 | measured: G8 met, 0 ownerless isotopologue rows on every set; G6 missed (decision 11) |
| 1.6 - cap and mass window | #2090 | 2026-09-08 | measured: G5 met (35,496 unsearched peaks -> 0); G2 clears its stage-1 target on A-D |
| 1.7 - stage 1 gate, engine 0.4.0 | #2091 | 2026-09-08 | measured: stage 1's numbers final; G1 met on A, B, C and D, missed on C2 |
| 2.1 - v2 fit for Stage B | #2092 | 2026-09-09 | measured: one v2 fit at the sample's own width; G1 falls on every Orbitrap set |
| 2.1b - TOF-capable reference: peaky's scorer through `score_pattern_v2` with the sample's fitted sigma | #2093 + peaky `epic/v2-fit-reference` | 2026-09-09 | measured: all 43 reference runs re-published at the sample's own width |
| 2.2 - self-calibrated mass gate | #2094 | 2026-09-09 | measured: every committed row records `mass_z`; the gate is a guard, not a lever |
| 2.3 - cross-channel corroboration and the reagent-N rule | #2096 | 2026-09-10 | measured: cross-channel corroboration recorded on every run; lone nitrogen readings capped |
| 2.4 - mechanical tiers with reasons | #2099 (measurements), #2100 (tiering), #2101 (inspector), #2105 (curated rows) | 2026-09-11 | measured: tier reasons on every committed row; G1 conditioned meets 20% on all five Orbitrap sets |
| 2.5a - reference seed: list format, the lifted lists, `reference seed` (seed proposal phases 0-1) | #2111 (seed), #2112 (Stage A fix), #2122 (the search pane's copy of the M0 rule), #2126 (the isotopologue tables' labels) | 2026-09-12 to 2026-09-15 | measured: 1,008 formulas lifted; the default seed raises G2 same-formula on every set |
| 2.5a fix - the reference seed out of the mass widths | #2113 | 2026-09-14 | measured: Stage A's width fitted over the target library's lines alone |
| 2.5b - Stage A window per source, polarity, radical filter, deactivate, the isoprene lift (seed proposal phase 3) | #2116 (schema and CLI), #2117 (engine, lists) | 2026-09-14 to 2026-09-15 | measured: each source carries its window, radical allowance and polarity; G2 rises on every set |
| 2.5c - the same-ion ambiguity on a Stage A mirror row | #2118 | 2026-09-15 | measured: a mirror row carries its ion's other readings; moves tiers and nothing else |
| 2.5d - review of the curated lists | #2136 (a library entry's line whatever its spelling), #2137 (the cap on library rows, the plan) | 2026-09-16 | measured: the 46 attached entries audited; the four odd-electron workaround entries removed |
| 2.5e - an offset from the reagent lines where the library is thin | #2140 | 2026-09-16 | measured: only C2 reaches it; 114 of the 146 rows 2.5d took return to assigned |
| 2.5f - a list hit meets the untargeted grid | #2148 (the measurement), #2149 (the election) | 2026-09-17 | measured: every list hit put to the search; rivals take 1,093 list-hit peaks |
| 2.4e - isotopologue claims under interference | #2143 | 2026-09-16 | measured: 252 of 861 flagged rows claimed as isotopologues; G6 falls 681 -> 596 |
| 2.4f - an oxygen-free neutral in a nitrate cluster | #2147 | 2026-09-16 | measured: 134 rows named on the nitrate sets, 10 taken from assigned, all on F2 |
| 2.6 - frontend: profile, reasons, roles | #2150 | 2026-09-17 | built: the launchers offer profile and context; the inspector shows `mass_z`, reasons and roles |
| 2.7 - stage 2 gate, engine 0.5.0 | #2152 | 2026-09-18 | measured: G1 meets 20% on A, C, C2 and D, misses on B; conditioned, all eight |
| 2.2b - mass-dependent centre for the mass gate | #2131 | 2026-09-15 | measured: A takes a line on all six samples, C2 on one; no tier moves |
| 2.7a - reference refresh: peaky's branch rebased on main 0.8.0, re-pinned, the 43 runs re-published | #2151, peaky `epic/v2-fit-reference` at `26e0ff3` | 2026-09-18 | measured: the 43 runs re-published at `26e0ff3`; G1 6.1 A, 10.9 C, 7.9 D, 32.7 B |
| 2.8 - the band first, one reading per ion, the inspector's ionization and list names | #2154 | 2026-09-21 | measured: every row under the top band names it first; 75 rows leave assigned (decision 20) |
| 3.1 - a profile for the charge-transfer source | #2197 | 2026-09-25 | built: `EASYIC_POS` and `EASYIC_NEG` resolve from the bare sign; I- assigned 66 -> 3 a sample |
| 3.1c - the stronger partner decides | #2205 | 2026-09-25 | built: the brighter partner takes the ion, a rival within the margin; tiering rule set 7 |
| 3.1d - the declared channel's prior | #2211 | 2026-09-25 | measured (pass 9): 155 of L's rows return to assigned, 65 stay held on the rival |
| 3.2 - formate as an opportunistic channel | #2198, stacked on #2197 | 2026-09-25 | built: `+HCOO-` a secondary channel of nitrate and halide profiles; G11 on G 24.4 -> 5.6% |
| 3.3 - name the source ions | #2218 | 2026-09-26 | measured (pass 10): the air's ions read as reagent, 84% of I+'s intensity, 88% of K3's |
| 3.3b - the standard adduct notation | #2203, #2204 stacked on it | 2026-09-25 | built: both notations read everywhere, rows stored in the standard form; moves no metric |
| 3.3c - reagent rows read like rows | #2221 | 2026-09-26 | built: a reagent row's ion carries its charge and owns its isotope lines; not measured |
| 3.3d - source-solvent clusters | - |  | planned, from the reference engine's reading (its #55): solvent ladders as reagent rows |
| 3.3e - the chemistry a run searches under is shipped | #2217 | 2026-09-26 | measured (pass 10): the shipped mechanisms are created at every start; no channel unavailable |
| 3.3f - a list is read through the channels the run opened | #2220 | 2026-09-26 | built: Stage A reads the lists through the channels the run opened; pass 11 measures it |
| 3.4 - an opportunistic channel needs a second channel | - |  | planned |
| 3.4b - one peak is not enough | #2212 | 2026-09-25 | measured (pass 9): G12 at 0 on every set |
| 3.4c - measure an alternative before committing | - |  | planned, from the plan owner's reading of 2026-09-25: a close alternative shows its evidence first |
| 3.4d - the columns of a tiering still in progress | #2223 | 2026-09-26 | built: the listed-as column, the probability hidden while provisional, the reagent anchor by majority |
| 3.4e - an expected line that is absent | - |  | planned, from the plan owner's notes of 2026-09-26; gate G13; three 0.10 riders |
| 3.4f - a peak that is not separated | - |  | planned, from the same notes; gate G14; the bimodality criterion of the 0.10 review |
| 3.4g - the chart draws each peak in the trace's unit, and a lone M0 | #2379 | 2026-10-19 | built: the lone M0 drawn, the envelope in the trace's unit; engine and tiers untouched |
| 3.4h - the inspector links the other channels, and the listing column folds | - |  | planned, from the same notes |
| 3.5 - calibrants below the brightest lines, an offset term, and the low-mass bend (calibration node) | - |  | planned |
| 3.5b - the envelope's tolerance, fitted per run | - |  | planned, from the plan owner's reading of 2026-09-25 and the pass-9 ledgers |
| 3.6 - priors and the dataset's context | - |  | planned; two riders from the reference's 0.10 line (the review section) |
| 3.7 - stage 3 gate, engine 0.6.0 | - |  | planned; read against the literature and chemical reasoning, not the reference (decision 27) |
| 4.1 - series detection on the batch ledger | - |  | planned |
| 4.2 - time-series coherence | - |  | planned; five riders from the reference's 0.10 line (the review section) |
| 4.3 - calibration from verdicts, per profile | - |  | planned |
| 4.4 - gate automation | - |  | planned |
| 4.5 - profiles as versioned rows, routing by detection | - |  | planned |
| 5.1 - the reference pinned at a release, a boundary review per peaky release | - |  | planned 2026-10-13; the first review is the section below |
| 5.2 - decoy arms in the gate: a shifted axis and swapped adducts through the in-app engine | - |  | planned; no target until two gates have read them |
| 5.3 - measure the minor-line response: isotope lines by height and by area against expected S/N | - |  | in progress (MAS-68); before 3.5b, which fits what it finds |
| 5.4 - the side lobe's signatures: width-scaled offset and narrowness in the sidelobe flag | - |  | planned; after 3.5b and 3.5 |
| 5.5 - the TOF signal-to-noise column: verify it tracks height, fix the picker or fall back to counting statistics | #2377 | 2026-10-16 | built: the picker's TOF noise is now the baseline's MAD; measured next on E, F, J |
| 5.6 - the import contract: a verdict on batch rows, the engine tier where the user looks | - |  | planned; between rounds |
| 5.7 - experiment: the file's own locks as calibrants, read against the calibration node's residuals | - |  | planned; after 3.5; feeds 4.3 and the axis-fingerprint project |
| 5.8 - an identification level in Mascope's own vocabulary | - |  | planned 2026-10-14 (decision 30 taken); after 3.7 |

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
   sample, 59 formulas contain no carbon, 29Si isotopologues become compounds.
4. **The 300-peak cap** leaves 22% of peaky's Assigned peaks unsearched on
   instrument A and 78% on the denser instrument B.
5. **No reagent, artifact or isotopologue handling**: 82% of the signal is
   reagent chemistry that is either fitted with a formula or tiered low.

Peaky's advantage is its first pass (1,115 of 1,192 main peaks): a bounded,
profile-driven grid, self-calibration, arbitration with an adduct policy, and
mechanical tiers. The exotic passes contribute 77 peaks. This plan harvests
the first pass in four stages and leaves the exotic passes for the data to
justify.

### Relationship to the existing designs

- [chemistry_profiles.md](chemistry_profiles.md) (two profile axes, library
  presets seeded as DB rows, the reagent pre-pass, reference-list tagging,
  batch series) is adopted as the shape of stages 1 and 3. This plan
  re-sequences it by measured impact and makes two changes: presets ship in
  the library and are resolved from the ionization mode first, with the
  versioned DB rows and their settings UI last (step 4.5), because
  editability is not what the stakeholders judged; and its harvest map's
  "do not port tiers, degeneracy, self-calibration gates" is reopened -
  the judgement layer is the second-largest gap - but it is reimplemented on
  Mascope's own arbitration seam, not ported as peaky's frame code.
- [assignment_confidence.md](assignment_confidence.md): stage 2 is layers L2
  (spectral neighbourhood), L3 (reagent priors) and L5 (arbitration with
  honest verdicts) of that architecture; the fit score stays pure throughout.
- [verification_calibration_loop.md](verification_calibration_loop.md): step
  4.3 feeds it Stage B rows and profile keys.
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

- **Branch and PRs.** Stages 1 and 2 ran on the epic branch
  `epic/assignment-quality` off `develop`, one PR per step, self-merged by
  rebase when CI was green, and the epic was reviewed into `develop` as one
  PR (#2077, merged 2026-09-24). Stage 3 runs the same way without the
  epic: one PR per step straight into `develop`, reviewed the same way,
  the feature dark behind `peak_assignment = false` until it is switched
  on, so a step that lands ships nothing by itself. Each stage ends with
  an engine version bump (`PEAK_ASSIGNMENT_ENGINE_VERSION` 0.4.0, 0.5.0,
  0.6.0, 0.7.0) because each changes results; steps inside a stage do not bump.
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
  is edited by every step and conflicts trivially; a changelog entry is a
  fragment of its own in `changelog.d/` and does not. The testbed is one
  deployed build, and the comparison reads the latest completed run of
  each engine per sample, so deploy, gate re-run and comparison are one
  critical section per step, done at the end of its PR, one branch at a
  time, with the build tag named in the PR comment.
- **The gate.** Every step ends by re-running the in-app engine on the fixed
  twelve-sample testbed set (six per instrument, peaky's runs already in the
  store) and running `compare_runs.py`; the numbers go into the measurement
  log in [`assignment_quality_plan_log.md`](assignment_quality_plan_log.md),
  which also holds the metrics and their stage targets in the table at its
  top. A step that moves a metric the wrong way is discussed before merge,
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
  | G | Orbitrap, chamber oxidation | 15N-nitrate CIMS, negative, with and without reagent ion | 6 + 6 | measured 2026-09-24, no reference run |
  | H | Orbitrap, chamber oxidation | urea CIMS, positive, with and without reagent ion | 5 + 6 | measured 2026-09-24, no reference run |
  | I | Orbitrap, chamber oxidation | EASY-IC charge-transfer source, positive and negative | 6 + 6 | measured 2026-09-24, no reference run |
  | J | TOF, chamber oxidation | nitrate and bromide CIMS mixed, negative | 6 | measured 2026-09-24, not gated (decision 22) |
  | K | Orbitrap, certified cylinder | EASY-IC charge-transfer source, positive, acquired from m/z 50 to 200 | 6 | measured 2026-09-25, no reference run; the 36 files whose calibration verified |
  | K2 | Orbitrap, certified cylinder | the same source, acquired from m/z 210 to 500 | 6 | cloned 2026-09-25; not runnable until the window has a calibrant (decision 24) |
  | K3 | Orbitrap, certified cylinder | the same source, one window from m/z 40 to 500 | 6 + 1 | measured 2026-09-25, no reference run; the injection-hour file added |
  | L | Orbitrap, certified cylinder | a proton-transfer source declaring protonation beside the bare sign, positive, m/z 50 to 200 | 6 | measured 2026-09-25, no reference run |

  Sets G to J are a customer's alpha-pinene chamber oxidation dataset, cloned
  to the testbed on 2026-09-24 with its processed peak lists (the private
  testbed note holds the names). They carry no reference run and are judged
  on the intrinsic metrics G9 to G11 (decision 21). Sets K to L are the
  internal Orbitrap's certified 18-component calibration cylinder of 22 and
  23 September 2026, cloned to the testbed on 2026-09-25 with every file of
  its five batches: a known mixture is ground truth for a source profile,
  since every committed peak is either in the bottle or it is not
  (decision 24).

  Sets D to F need peaky reference runs with TOF-appropriate windows where
  the instrument is a TOF (its Orbitrap defaults of 1 ppm trust and 3 ppm
  search are meaningless at 10 ppm accuracy), and the in-app engine's TOF
  window default from step 1.1. The demo dataset carries set D's chemistry
  in public form, which is what step 4.4 can run in CI. A stage gate is
  judged on the whole set; a chemistry that regresses blocks the stage even
  when the pooled number improves.
- **The reference moves; the gate reads it at releases.** peaky's main takes
  several PRs a week, so the gate's reference runs are re-published only at
  a tagged peaky release, never at a pre-release or a main commit, with the
  tag recorded in the status table (step 2.7a did this on 0.8.0; the next is
  0.10.0 when it is cut). Each peaky minor gets one boundary review: its
  changelog and docs read once, every behaviour binned as parity, rider,
  new step, not ours or an ask of Mascope, written into this note as a dated
  section and into the steps it rides with (step 5.1). The first review is
  the section after stage 4.
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

Specifications of the completed steps (1.1, 1.2, 1.3, 1.4, 1.5, 1.6 and 1.7)
are in [`assignment_quality_plan_log.md`](assignment_quality_plan_log.md).

## Stage 2 - earn the tier (engine 0.5.0)

The confidence layer. This is where "assigned" starts meaning something.

Specifications of the completed steps (2.1, 2.1b, 2.2, 2.2b, 2.3, 2.4, 2.4e,
2.4f, 2.5, 2.5d, 2.5e, 2.5f, 2.6, 2.7 and 2.8) are in
[`assignment_quality_plan_log.md`](assignment_quality_plan_log.md).

## Stage 3 - read the source right (engine 0.6.0)

What a chemist reading the ledger of a customer's chamber oxidation dataset
found wrong before any batch is consulted (decision 21). The dataset holds
six Orbitrap chemistries and a mixed-reagent TOF batch (sets G to J); the
reading is in the private testbed notes and its numbers are under "Baselines
on the chamber dataset" in
[`assignment_quality_plan_log.md`](assignment_quality_plan_log.md). Each
step is small and lands as its own PR into `develop`; the stage ends with a
gate over sets A to J and the 0.6.0 bump.

The build order, agreed 2026-09-26 with 3.1, 3.2, 3.3b, 3.1c, 3.1d and
3.4b on develop: 3.3, then 3.3c as its own PR once 3.3 has merged (the two
rewrite the reagent pass's one row builder and its tests, so neither in
parallel nor in one PR), then 3.5b and 3.5 together, since both re-base
the score and one pass measures them, and only then 3.4, 3.6 and the 3.7
gate, so that the tier rules and the gate are read on a fit that says what
the evidence is. 3.4c is the inspector's and lands beside any of them.
3.3e, the shipped chemistry, is built beside 3.3 and is on develop before
the pass that measures 3.3, so that from pass 10 on every measurement runs
on chemistry any instance reproduces. 3.3f, the list read through the
opened channels, follows 3.3 and 3.3e and can run beside 3.3c, since it
touches Stage A and neither the reagent pass nor the browser.

Specifications of the completed steps (3.1, 3.1c, 3.1d, 3.2, 3.3, 3.3b, 3.3c,
3.3e, 3.3f, 3.4b, 3.4d and 3.4g) are in
[`assignment_quality_plan_log.md`](assignment_quality_plan_log.md).

### 3.3d Source-solvent clusters

- **What.** The proton-bound and hydride-bound ladders of the source's
  solvent vapours, water, methanol, ethanol and acetone, as (S)nH+,
  (S)n.H3O+ and [S-H]+.S, claimed as reagent rows under the reference
  engine's ladder rules (its #55): the monomer ion is present, the ladder is
  contiguous from it, every rung's mass fits at the run's precision, the
  intensities fall along the ladder, and a rung displaces an analyte reading
  only where its ladder evidence outweighs the analyte's by a bar; the water
  rung never displaces [good70, sun88].
- **Why.** On set I+ the acetone dimer at 117.091 sits in three of six
  samples and reads as an analyte, and the reference engine's chamber run
  finds ethanol ladders in the positive charge-transfer batch. A source's
  solvent is not the sample.
- **Where.** `reagent_pass.py`, a ladder claim beside the cluster claim; the
  profiles' solvent lists per source.
- **Verify.** G10 on I+ and K at or above target; no certified component of
  the cylinder claimed; the dimer rows on I+ claimed.
- **Size.** S-M.

### 3.4 An opportunistic channel needs a second channel

- **What.** `_apply_minor_channel_policy` no longer lifts the cap on an
  isotopologue alone: a secondary-channel winner reaches "assigned" when a
  primary channel committed the same neutral in the sample, or a list names
  it. The isotopologue still counts toward the row's band.
- **Why.** A 13C partner proves the ion's carbon count, never which neutral
  clustered: benzene and methanol read through the carbonate channel reached
  "assigned" on isotopologue corroboration alone. The rule was followed and
  the chemistry was not.
- **Where.** `engine.py`, the policy's corroboration branch and its reason
  in provenance.
- **Verify.** On sets C, C2 and G the carbonate-channel assigned rows drop to
  those with a second channel or a list; G7 stays 0; no other set moves.
- **Size.** S.

### 3.4c Measure an alternative before committing

- **What.** A close alternative in the inspector is measured on the sample
  before it is taken. The alternatives list shows, for each entry the run
  did not measure (a batch-ledger identity, a list's compound the run did
  not match), a *measure* action that scores the formula on the peak the
  way Stage A scores a list's compound (the seeded fit: mass error against
  the run's calibration, the envelope's lines and their shares, fit,
  plausibility, and the tier the row would take under the run's own bands
  and rules), and shows the result beside the current reading without
  writing a row. *Use this* stays the commit, and takes what was measured.
- **Why.** The plan owner's reading of 2026-09-25 on the internal
  Orbitrap's uronium batch: a peak at m/z 297.0825 read as C10H16O10
  `[M+H]+` at fit 0%, and the batch ledger offered
  octamethylcyclotetrasiloxane (D4) `[M+H]+` as an alternative marked *not
  measured*, with no fit, plausibility or tier to judge it on. Pinning it
  was the only way to find out, and pinning is a batch-wide act. The
  assignment then took D4 with three lines and two channels (3.5b says what
  it made of them).
- **Where.** The peak-assignment pane's alternatives list and its tooltip; a
  read route that scores one formula on one peak of one sample through
  `seeded_scoring` and the tiering pass's reasons without a ledger write;
  the how-it-works page's paragraph on the alternatives.
- **Verify.** On the uronium sample the D4 alternative shows its fit, its
  lines, its plausibility and the tier it would take before *use this*; a
  measured alternative reads the same numbers the assignment writes when it
  is taken.
- **Size.** M.

### 3.4e An expected line that is absent

- **What.** A demote rule of the tiering pass, beside `lone_peak`: a
  monoisotopic row whose predicted envelope holds a line the detectability
  gate says the spectrum should have shown, and which the spectrum does
  not hold, is capped at candidate with the reason `absent_line`, naming
  the line, its predicted share and the share the gate asked for. The gate
  is the fit's own (`DETECT_SNR_K` against the peak's signal-to-noise,
  `REL_DETECT_NO_SNR` without one), the envelope is the one
  `envelope_claims` predicts for the row, and the lines are read off the
  run's spectrum snapshot as the mass gate reads them, within the
  tolerance 3.5b fits. No second channel lifts the cap: a second channel
  says the neutral is in the sample, and cannot put a bromine line into a
  spectrum that lacks one. Every committed monoisotopic row is asked, a
  Stage A row and a hand assignment included, since the statement is about
  the spectrum and not about who named the formula; the fit's own refusal
  (`pattern_has_required_lines`, the ion's line and the lead line) stays
  as it is, and this rule reaches the lines the fit only charges. Beside
  the rule, the absence becomes visible: the inspector's isotopologue
  table lists the predicted lines that were not found under the found
  ones, with their predicted m/z and share and the words *not found*, and
  the chart's theoretical envelope draws them as empty circles at the
  predicted heights, so the detail carries the row's predicted envelope
  (`predicted_mz`, `predicted_share`, found or not) for the focused row.
- **Why.** The plan owner's note of 2026-09-26: in bromide mode a row
  read through `[M+Br]-` kept its tier with its `[81Br]` line, half the
  ion's signal, absent from the spectrum, because a second channel
  committed the neutral. 3.4b's second-channel escape was written for a
  lone line, not for a line that should have been there and was not; the
  fit charges the absence (the v2 score), and the bands can still put the
  row at assigned. And the table showed only what was found, so the
  absence had to be inferred from what was not listed.
- **Where.** `tiering.py` (the reason constant, an `absent_line_reason`
  beside `lone_peak_reason`, reading the row's predicted envelope and the
  spectrum lines the pass already holds), the reason in provenance and the
  detail's envelope, `PanePeakAssign.vue` (the table's absent rows),
  `ChartSampleSpectrum/data.js` (the envelope drawn from the prediction
  rather than from the found rows, which 3.4g also needs), the
  how-it-works page and the tiers help, `CHANGELOG.md`; `test_tiering.py`
  (a bromine row capped on its absent `81Br` line, a second channel not
  lifting it, a line under the gate ignored, a curated row asked), the
  inspector and chart specs.
- **Verify.** G13 at 0 on every set. On D, E and F the assigned rows that
  move, ten of them read by hand against the spectrum; the chlorine
  compounds on the sets that hold them; no reagent row moves; G1 and G2
  as readings.
- **Size.** M. After 3.5b, whose fitted tolerance decides what "not
  found" means; beside 3.4f.

### 3.4f A peak that is not separated

- **What.** A demote rule of the tiering pass: a row whose peak the
  instrument did not separate from a neighbouring peak, a neighbour within
  two widths of it (the width from the peak's own `resolution`, m/z over
  full width at half maximum, as the peak picker records it) that is not a
  line of the row's own envelope, is capped at candidate with the reason
  `unresolved_peak`, naming the neighbour, its distance in widths and its
  height against the peak's. One escape: a second channel committing the
  neutral whose own peak is separated, read through the same test, since
  a partner the instrument did not separate either corroborates a mass it
  did not measure. A list naming the compound is not an escape, for the
  reason 3.4e gives: the statement is about the measurement. Two widths
  is the starting value; the step reads E, F and J by hand at one and a
  half, two and three widths before it is fixed, and records the reading.
- **Why.** The plan owner's note of 2026-09-26: on the TOF, heavily
  overlapping peaks reach assigned when another channel supports the
  formula, and that channel's peak may be as overlapped. A centroid and a
  height read off an unresolved pair carry neither the mass nor the
  intensity the fit scores, and the bands cannot know: the candidate
  density rule asks about formulas the mass window cannot separate, not
  about peaks the instrument did not.
- **Where.** `tiering.py` (the rule and its reason; the spectrum lines the
  pass holds gain each peak's width, which the peak table carries), the
  cross-channel record (the partner's separation), the inspector (the
  neighbour named beside the reason), the how-it-works page, the tiers
  help, `CHANGELOG.md`; `test_tiering.py` (an overlapped peak capped, a
  separated partner lifting it, an overlapped partner not, an envelope
  line not counted as a neighbour, a resolved Orbitrap pair untouched).
- **Verify.** G14 at 0 on every set: no assigned M0 row whose peak is
  unseparated and whose neutral no separated partner channel commits. On
  E, F and J the rows that move, read by hand; the Orbitrap sets report
  what moves, expected next to nothing; G1 and G2 as readings.
- **Size.** M. Beside 3.4e; the TOF sets are where it is read.

### 3.4h The inspector links the other channels, and the listing column folds

- **What.** Two changes to what the app shows. The inspector's
  "Supported by N channels" badge opens the other readings of the
  neutral, each a link that focuses that peak, read off the ledger's rows
  by neutral formula as 3.4d's grouping reads them, since the
  cross-channel record names channels and not peaks. And the listed-as
  column folds to a mark: an icon, with the list's tag beside it where
  the list carries one, whose tooltip carries the name, the list, the
  tags and the count of further names; a switch in the view menu shows
  the full column, folded by default, in both ledgers.
- **Why.** The plan owner's notes of 2026-09-26: the ledger is too wide
  with the column, and the badge names channels the reader then has to
  find.
- **Where.** `PanePeakAssign.vue` (the badge's popover and links),
  `PaneBrowserAssignment.vue` and `PaneBrowserBatchPeaks.vue` (the mark,
  the switch), `CHANGELOG.md`; the inspector and ledger specs.
- **Verify.** In the browser: the badge's links focus the other
  channels' peaks; the ledger's width with the column folded; the switch.
- **Size.** S. No tier moves; can go now, beside 3.4g.

### 3.5 Calibrants below the brightest lines, an offset term, and the low-mass bend

- **What.** The m/z calibration node leaves out a calibrant line brighter
  than a cap, as it already leaves out one below `peak_intensity_min`. The
  cap is a node parameter, absolute or relative to the base peak, with an
  instrument-class default, and the fit records it. Where its calibrants
  span the mass range, the Orbitrap fit also takes an offset beside its
  factor, the `ppm = a + b * 1000 / mz` form of step 2.2b's line, so a
  constant offset in millidaltons is calibrated out rather than scored.
- **Why the offset.** Set A's axis carries -0.11 to -0.14 mDa (step 2.2b's
  table in [`assignment_quality_plan_log.md`](assignment_quality_plan_log.md)),
  which one factor cannot remove, and at m/z 74 it puts the axis
  1.1 ppm low. The engine's fit scores every line against one centre for the
  sample, so dimethylformamide `[M+H]+` fits at 5 to 16% on all six samples
  while the run's own line puts it within half a width of calibration (step
  2.8). Centring the fit on the run's line in the engine is not the fix -
  estimated on A it moves 155 rows up and 176 down, as the line and the
  anchors also disagree at high m/z - and the plan owner's rule is to
  calibrate before assigning (decision 20).
- **Why.** The brightest lines are the worst calibrants on both instruments
  (the plan owner, 2026-09-18). On the assignment gate the two brightest
  reagent lines read above every weaker line of the same ladder on every
  Orbitrap set:
  - B +1.87 against -0.43 ppm;
  - C2 +0.60 against -1.41;
  - D +0.44 against -0.03, the gap growing with the lines' height.

  The Orbitrap fit is one factor, the median of its lines, so a list holding
  the reagent's own base peak lands between the bright lines and the weak
  ones. On C2 it left the axis 1.1 ppm low, which step 2.7 refitted by hand.
  Calibrant lists without the reagent ions already exist for the
  higher-m/z nitrate modes of the same instrument; the cap makes that the
  node's rule on every mode.
- **Why, from the chamber dataset.** The uronium batch with reagent ion
  sits at +0.9 ppm across the range (median +0.88, interquartile 0.62 to
  1.15 ppm on assigned rows), which the mass gate absorbs and the node
  should remove. At m/z 45 to 47 formate and 15N-nitrite read 5 to 11 ppm
  off while everything above m/z 100 sits within 0.3 ppm: the curve bends
  where the source ions live. Both are calibration-shape matters, not
  assignment ones, and the offset term this step adds is the first half of
  straightening them; the bend needs the low-mass calibrants step 3.3 names.
- **Verify.** Refit the gate's Orbitrap files with the cap and with today's
  lists. The committed analytes' median error moves toward zero where bright
  lines had pulled a fit, and away from it nowhere.
- **Size.** S. It is the calibration node's, not the engine's, and it
  re-bases every set it touches, so it lands between rounds.
- **Also here (pass 10, 2026-09-26): the mass gate keeps the lines the
  reagent pass claims.** On the wide-window cylinder set the mode's
  calibration compounds, N3+ and NO2+, are the discharge's ions and are
  now claimed as reagent before Stage A sees them, so the mass gate's fit
  (step 2.2) lost its brightest anchors and four of seven files run at the
  fallback width with no fitted centre. A claimed reagent line is the
  best-known mass in the file; the gate reads the reagent pass's
  monoisotopic hits as anchors beside the committed rows, which is what
  the reagent-line offset of step 2.5e already does for a thin library.

### 3.5b The envelope's tolerance, fitted per run

- **What.** The fit score's intensity term takes its width from the run.
  Today a matched isotope line is scored on `exp(-0.5 * ((observed -
  predicted) / sigma)^2)` with `sigma` the larger of the line's own noise,
  5% of its predicted share and 0.001 (`score_pattern_v2`), so for a clean
  peak the tolerance is 5% of the predicted share, and a line 15% under it
  is a three-sigma miss that alone takes the ion's fit to about a half. The
  run instead fits, on its own confidently committed envelopes (a second
  channel or a list, mass within the gate, lines the gate calls tracking),
  the relative error of a committed line against its predicted share: a
  centre, since an Orbitrap reads its isotope lines low against the anchor,
  and a width, which floors the 5%. The score subtracts the centre and
  scores against the fitted width; the SNR terms stay as they are, since
  they only ever widen. The run records both (`config.envelope_tolerance`),
  with the count of lines they were fitted on, and falls back to today's
  numbers where a run has too few. The mass term's width already works this
  way (the per-sample `sigma_ppm`, and the mass gate's centre and width of
  step 2.2); this is the same fit for the intensity axis.
- **Why.** The plan owner's reading of 2026-09-25: on the internal
  Orbitrap's uronium batch, D4 `[M+H]+` at m/z 297.0825 is committed with
  three lines (M0 at 0.04 ppm, the 29Si line at -0.15 ppm, the 30Si line at
  -0.09 ppm), through two channels and from a list, and sits below
  assignability: fit 41%, evidence 41% under the candidate band of 45%,
  because the 29Si line is 15.5% under its predicted share. That is as much
  evidence as a mass spectrum gives without an orthogonal method, and the
  band reads it as a weak row. Measured on the pass-9 ledgers, the committed
  isotope lines of assigned and candidate rows miss their predicted share
  by a median 6.8% on the chamber Orbitrap (1,257 lines), 7.2% on the
  cylinder Orbitrap (717) and 7.1% on the TOF (340); the upper quartile
  starts at 14 to 16% and the top tenth at 27, 27 and 37%. The miss has a
  sign: -9.6% on the chamber Orbitrap and -6.0% on the cylinder one, the
  lines reading low against the anchor, +4.2% on the TOF. Under the 5%
  tolerance the intensity terms alone hold the fit under the assigned band
  for 22%, 21% and 53% of the committed rows that have lines, and under the
  candidate band for 12%, 10% and 38%; at a 15% tolerance 4%, 1% and 19%,
  and 1%, 0% and 8%; at 25% next to none. The actual fit is under the
  assigned band for 27%, 34% and 45% of them, the rest being the mass term
  on the brightest lines, which is 3.5's. The plan owner's own ruler is the
  bromide doublet: 79Br and 81Br at 50.7 to 49.3, on which the internal
  Orbitrap is seen to miss on known ions. The fit is not wrong to score the
  shares; it is wrong about how well the instrument measures them.
- **The open call, until this is built.** A row with a second line, a
  second channel and a list is the converse of decision 25, and the band
  (2.8: the band first, the rules only lower) holds it below on the fit
  number alone. The plan's answer is to fix the number, not to add a rule
  that lifts a row over its band, since a fitted tolerance makes the fit
  say what the evidence is and the band then reads right without a second
  opinion. Whether such a row should meanwhile be read at candidate at
  least is the plan owner's call.
- **Where.** `score_pattern_v2` in `heuristic_filter.py` (a floor and a
  centre for the relative error, parameters with today's values as
  defaults); the engine's scoring call and the seeded fit; the service's
  per-run fit of the two numbers over the run's committed envelopes, run
  after Stage A and before the search is scored, or as a second scoring
  pass over the committed rows; `config.py`; `fit_score.md` and the
  how-it-works page's paragraph on the fit; tests on the score (a line 15%
  under at a fitted 12% width scores as a one-sigma miss; the centre
  subtracted; the fallback) and on the fit of the two numbers.
- **Verify.** On the uronium sample the D4 row is at assigned with its
  three lines named. On the gate's Orbitrap sets the share of committed
  rows with lines whose fit is under the assigned band falls from 27 and
  34% toward the mass term's share, and the bromide files read their
  doublets at a fit above the band. No row gains assigned on a line the
  gate calls untracked, and G12 stays at 0. G1 and G2 read as readings
  (decision 27).
- **Size.** M. The score is the library's, so the library and the backend
  release together.
- **Order.** Built with 3.5, right after 3.3c, before 3.4 and 3.6 (agreed
  2026-09-26). Every step after it is read on the fit, and the two steps
  are the same fix on the score's two axes: on the cylinder Orbitrap the
  intensity term explains 21 of the 34 points of committed envelopes
  under the assigned band and the mass term on the brightest lines the
  rest, which is the cylinder's own certified components. Both re-base
  every set, so one pass measures them. Landing it next also makes the
  open call above moot: the band is left as it is.

### 3.6 Priors and the dataset's context

- **What.** Every atmospheric context carries a sulfur prior and an
  odd-nitrogen prior: a formula with sulfur, or with two or more nitrogens
  and fewer than two oxygens per nitrogen, needs corroboration beyond the
  mass fit to reach "assigned". A dataset or batch can name its context
  once, and a run on `auto` takes it before the profile's default. On a TOF
  the untargeted grid is bounded by the sample's fitted width rather than the
  instrument class's 10 ppm. Chlorine is allowed where the context or a
  reference list shows it: the certified cylinder holds chlorobenzene,
  which a grid with no chlorine cannot read (the reference engine's #53
  found the same gap), and a chlorine formula takes the 37Cl line at a
  third of M0 as the second observation 3.4b asks for, which the batch
  fold predicts (4.2). The odd-nitrogen prior's first target is the
  nitrogen-rich C2 family of the cylinder sets, 21 to 34% of their
  assigned rows and 36% of set K's assigned intensity, in a nitrogen-free
  mixture.
- **Why.** Sulfur formulas are 5% of the assigned neutrals in the nitrate
  batches, 2% in uronium and 22% on the TOF, in an alpha-pinene system with
  no sulfur source; the chamber context, run by hand on the nitrate
  representatives, halved them and touched nothing else. The engine
  auto-resolved "ambient air" for a chamber experiment because nothing told
  it otherwise.
- **Where.** `profiles.py` contexts; the run config's context resolution
  (`resolved_profile.context` from the dataset when set); the grid's window
  on TOF in the untargeted stage.
- **Verify.** G9 on every set at or below target with no loss on G2; the
  TOF set's candidate and below-assignability shares fall.
- **Size.** S-M.

### 3.7 Stage 3 gate, engine 0.6.0

- Protocol run over sets A to L, status table, version bump, changelog.
  The verdict is read against the literature and chemical reasoning, not
  the reference engine's formulas (decision 27). Expected: G9 at or below
  2% and G10 at or above 95% on every gated set, G11 at 0 on set G, G12 at
  0 on every set; on the cylinder sets every certified component at
  assigned through its right reading and nothing the bottle does not hold
  at assigned; and a chemist's reading of the fifty brightest assigned
  rows of each chamber set against the known products of alpha-pinene
  oxidation [yu99, gla00, ehn14, bia19] and the source chemistry (the
  references under 3.3), every row either in the literature, argued from
  it, or reported as neither. G1 and G2 are reported beside the rest as
  readings of where the two engines differ, each difference explained by
  one side's chemistry or listed as open; they are no longer a target.
  Set J is measured and reported, not gated (decision 22). References:
  yu99 Yu, Cocker, Griffin, Flagan, Seinfeld (1999) J. Atmos. Chem. 34,
  207; gla00 Glasius et al. (2000) Environ. Sci. Technol. 34, 1001; ehn14
  Ehn et al. (2014) Nature 506, 476; bia19 Bianchi et al. (2019) Chem.
  Rev. 119, 3472.

## Stage 4 - use the batch (engine 0.7.0)

Corroboration that only a batch can give, on the batch ledger.

### 4.1 Series detection on the batch ledger

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
  peaky's `residual:series` rows where both exist. peaky's merged ledger
  now carries `alternatives` (every losing reading, best first) and
  `stage` (`cover` or `residual`; peaky #43, #44), and `peaky
  publish-batch` maps fixed columns, so the comparison reads them from
  peaky's run folder rather than from the import.
- **Size.** L. Depends on stages 2 and 3.

### 4.2 Time-series coherence

- **What.** For a neutral seen in two channels, the channels' batch time
  series (the records series endpoint) must correlate; r >= 0.6 corroborates,
  anti-correlation demotes to candidate with a reason. A channel holding a
  constant ratio to a bright parent across the batch is a sidelobe and
  becomes `artifact`.
  - **Persistence is admission, not evidence** (peaky #34). A peak
    present in more of the batch's spectra than the batch's own split of
    the occurrence distribution is searched however faint, and an M0
    admitted by persistence alone is capped at candidate until an
    isotopologue, a channel or a series corroborates it: persistence
    proves the ion is real, and at a few counts nothing constrains which
    formula it got.
  - **Predicted diagnostic satellites in the batch fold** (peaky #45). The
    15N, 18O, 34S, 29Si, 30Si, 37Cl and 81Br lines of every committed M0
    sit below the picker's edge in most files and stand only where a plume
    lifts them; the fold predicts them at the parent's trace centre and
    stamps a line only where the height ratio holds in the same sample
    and across the track, since a true satellite passes in nearly every
    judged sample and an independent compound in few.
  - **The consensus rule, stated against peaky's.** peaky's merge is a
    two-stage file-count vote - which ion, then which label - with a
    curated exemption (peaky #43); Mascope's fold weights by intensity.
    The two are compared on one batch before either is called right.
  - **A flat series is background** (peaky #53). A formula whose batch
    time series holds a constant ratio to the batch's normaliser, varying
    in fewer files than a floor share, is background rather than an
    analyte: capped at candidate with the reason, and read as a source ion
    where 3.3's list names it. The nitrogen-rich C2 family of the cylinder
    sets, flat in every file, is its first target.
- **Verify.** On the two 400-600-sample testbed batches; compare with
  peaky's pass-7 skips.
- **Size.** M. Depends on 2.3 and 2.4.

### 4.3 Calibration from verdicts, per profile

- **What.** `assignment_calibration` records the reagent profile (the
  profiles design's first calibration step); `recalibrate_instrument`
  admits Stage B rows now that their evidence is on the v2 scale; a review
  routine on the testbed verifies the top disagreements between the engines
  (`tier_disagrees`) so labels accumulate; the corroboration weights are
  refit per profile. The fitted axis gains a mass-dependent term once the
  verdict anchors reach below m/z 100: after step 2.2 the residual was
  measured to curve at the low-mass end on every instrument, on files the
  calibration node marks verified (issue #2095), while the gate's single
  width caps nothing there.
- **Verify.** Before and after ECE from the recalibration route; the
  provisional gate behaves.
- **Size.** M. Depends on 2.1 and the verification UI (shipped).

### 4.4 Gate automation

- **What.** `compare_runs.py --gate thresholds.json` fails when a metric
  regresses past its target; a scheduled run on the testbed re-runs the
  engine on the fixed sample set after each epic merge and posts the table.
- **Size.** S.

### 4.5 Profiles as versioned rows, routing by detection

- **What.** The profiles design's DB half: `reagent_profile` and
  `chemistry_context` tables seeded from the library, `ionization_mode
  .reagent_profile_id`, the settings surface, and the fingerprint-based
  `resolve_ionization_modes_by_peaks` from the setup-simplification
  proposal. Last because it changes who can edit chemistry, not what the
  engine concludes. The library also ports 15N-ammonium, the second
  profile peaky added after the 1.1 port (`[M+^NH4]+` with a declustering
  `[M+H]+`, purity 0.98, no opportunistic channels on a labelled run; peaky
  #30); the first, EasyIC, moved forward to step 3.1.
- **Size.** L. Decision D5.

## The reference's 0.10 line, reviewed (2026-10-13)

The reference engine did not stand still while stage 3 was built. peaky
released 0.9.0 on 2026-09-30 (the v2 fit on the library Mascope 1.10
publishes, the standard notation, the abstraction and solvent-cluster
channels), cut the 0.10.0b1 pre-release on 2026-10-07 (twelve stacked PRs:
an evidence scale beside its tier, batch isotope checks, a merge vote ranked
by evidence before file count, side channels declared per reagent, TOF
gates, and the measurement scripts it now validates every release with), and
put four more PRs on its main by 2026-10-10 (a batch m/z axis measured and
corrected before assignment, precision stages keyed on the instrument class,
isotope lines read by area against the file's own minor-line response, a
resolvability fix). 316 commits, 296 files, 86,000 lines; its test suite
went from 256 to 2,615 tests. Its 0.10.0 is not cut: "open items can still
move results", one PR is open (its #77, a false axis step on 15N-labelled
batches) and its experimental flags stay off.

This section is the first boundary review (step 5.1): what of that Mascope
takes, what it folds into steps already planned, what it has already, and
what is peaky's own. The reference is read, not targeted (decisions 18, 20,
27): nothing below tunes a rule toward agreement with peaky, and its numbers
are quoted as what it measured on its own engine, on its own batches (a
labelled-nitrate Orbitrap batch, a uronium Orbitrap batch, a bromide/nitrate
TOF batch, and for the later work a nitrate Orbitrap batch checked against
raw profiles and an independent expert assignment). The Linear project
"Assignment quality plan" (team Mascope) carries the same steps as issues
under its milestone 5.

### How each behaviour was binned

| bin | meaning | count |
|---|---|---|
| parity | Mascope has it, by construction or by an earlier step; verify only | the composed reagent profile (1.1), the m/z-dependent scoring centre (2.2b, Mascope first), opportunistic channels opened by fingerprint rather than by the server's list (1.2), the element budget on the grid (1.1, 2.5b), the FT artifact role (1.5), the shipped electron-transfer mechanisms (3.3e), peak areas and signal-to-noise on the peaks endpoint (2.1b) |
| rider | folds into a planned step; written into that step's issue and named in its status row | 3.4e (three), 3.4f (one), 3.6 (two), 3.3d (one), 4.2 (five) |
| new step | its own step under milestone 5 | 5.1 to 5.7 below |
| not ours | the reference's strategy, exploration or narrative, by the charter's boundary | its evidence scale and claims as a vocabulary, its merge vote, trace-first and the rolling centre, its scorecard and A/B scripts as scripts, its report and workbook, its MCP tools |
| ask of Mascope | a server, library or contract gap the reference worked around | the batch import's missing verdict (5.6), the TOF signal-to-noise column (5.5), the Orbitrap axis (5.7 and the axis-fingerprint project), the censored minor lines (5.3), a per-line centre in the library's score (3.5b), the mechanism catalogue read as a configuration (5.6's docs line) |

### 5.1 The reference pinned at a release, and a boundary review per peaky release

- **What.** Two standing rules, now in "How the work runs": the gate's 43
  reference runs are re-published only at a tagged peaky release, with the
  tag and the published version string in the status table and G1 and G2
  re-read as readings; and every peaky minor gets one review in this form,
  landed as a dated section here and as issues, its decisions raised for the
  plan owner rather than taken.
- **Why.** Chasing peaky's main would re-base every set each week; ignoring
  it would let the engines drift apart unmeasured. A release is the unit the
  two repositories already share (step 2.7, the epic merged at the Mascope
  release that published the library).
- **Verify.** The status table names the reference's tag for every set; a
  review section exists for every peaky minor since 0.9.0.
- **Size.** S per release. Next: 0.10.0 when it is cut; until peaky's #77
  merges, no reference run on a labelled set uses its axis correction.

### 5.2 Decoy arms in the gate

- **What.** The in-app engine re-run on the gate sets with the m/z axis
  shifted (9 ppm either way on an Orbitrap, 0.35 Da on a TOF, a shift inside
  the file's own window skipped) and with the adducts swapped for ones the
  profile does not declare, every arm at the control's calibration; the share
  of the control's assigned rows and intensity the decoy still holds at
  assigned, below and above m/z 350, reported per set by `compare_runs.py`.
  A `decoy` block on the run config, recorded in the snapshot and marked on
  the run row, so a decoy run is never read by the fold, the consensus or the
  calibration from verdicts.
- **Why.** G1 to G14 count agreement, plausibility and rule outcomes; none
  estimates what a sample would still get if its chemistry were wrong. The
  reference's own arms are the argument: a 9 ppm shift kept 7.7% of a
  labelled-nitrate batch's Assigned rows and 1.1% of a uronium batch's; the
  wrong-adduct arm kept 74% on uronium ("the ion is pinned by mass, the
  neutral/adduct split is not") and, before its TOF gates, 183% of the
  control on a TOF.
- **Where.** `config.py`, `service.py`, `engine.py` (the swap through the
  channel table), the fold's and the calibration store's exclusion,
  `tooling/assignment_compare/compare_runs.py`.
- **Verify.** On set A the shifted arm holds under 5% of the control's
  assigned rows; the TOF sets are reported, not gated; a control run is
  identical with the block absent.
- **Size.** M. Independent; beside 3.5b and 3.5, read in pass 11; the
  stage-3 gate reports the arms with no target until two gates have read
  them.

### 5.3 Measure the minor-line response

- **What.** On the pass-9 ledgers' committed envelopes, the ratio observed
  over predicted of every tracking 13C and 18O line, by height and by area,
  binned by the line's expected signal-to-noise (the anchor's times the
  predicted share): the median and the 20th percentile per bin and set, the
  share of lines under 0.9 and under 0.5, whether area sits above height at
  low expected S/N and whether either overshoots at mid S/N; the reagent
  rows' lines beside them. Then the call for the plan owner: whether 3.5b
  fits a centre and a width as written, or a response curve against expected
  S/N read by area on Orbitrap-class runs, or one form per instrument class.
- **Why.** 3.5b measured the Orbitrap lines reading low against the anchor
  by a median 7% with a sign. The reference's 0.10 line explains the sign:
  an Orbitrap scan keeps a line only where it clears that scan's detection
  floor, and the whole-file peak list sums the scans, so a minor line near
  the floor is censored scan by scan and reads about 0.3x its share by area
  where its expected S/N in the summed list is about 20 on 1-microscan
  files, 0.9 to 1.0x once it clears every scan, and lower still by height.
  Its fix (lines by area, a per-file response fitted on the 13C lines of
  committed CHO and CHON rows, a line matched anywhere between the 20th
  percentile at its S/N and its natural share) moved its same-formula
  agreement with an expert set from 390 to 421 on 1-microscan files and
  took Assigned N3+ formulas on deuterium-labelled files from 14 to 3; its
  known limits are a dim over-claim no longer cleared by its 13C line and
  mid-S/N lines reading 7 to 13% high by area on one instrument. The
  censoring is a property of Mascope's own summed peak list, so the
  measurement is ours to make before the fit is chosen.
- **Where.** A measurement over the run ledgers and the peaks endpoint; the
  result into 3.5b's section; no engine change.
- **Verify.** The table per set with line counts; the call recorded on 3.5b
  before its PR opens.
- **Size.** S. Blocks 3.5b.

### 5.4 The side lobe's signatures

- **What.** Measure, on the gate's Orbitrap sets, the peaks the sidelobe
  flag and the artifact pass take against the peaks a width-scaled rule
  takes (a weak entry one to five line widths beside a line at least 50x
  brighter, in units of the parent's own observed width, with an artifact
  signature: narrower than 0.7x a line, at the negative-lobe offset of
  -2.6 to -2.1 FWHM, or mirrored; exempt the isotope fine-structure lines
  and a candidate at an isotopologue offset of any brighter line within
  1 ppm), ten disagreements per set read against the raw profile; where the
  signatures catch lobes the ratio test misses, add them to
  `flag_satellite_peaks` as a second pass keyed on the peak's width and
  area, with the exemptions, the artifact pass reading the same function.
- **Why.** Our test asks how much weaker than a base within 100 ppm; the
  reference's asks where, in the parent's own width, and how wide. They
  disagree on lobes of mid-height peaks and on real weak peaks near a bright
  one. The reference measured a wider 0.3 FWHM exemption and rejected it
  (all eight peaks it spared were lobes).
- **Where.** `mascope_tools.alignment.utils`, the picker's call,
  `artifact_pass.py`; a picker change moves the reproducibility goldens and
  ships with their refresh.
- **Verify.** Every peak the new signatures flag and the engine had assigned
  is a lobe by the raw profile; no isotopologue of a bright line is flagged;
  G6 and G8 unchanged.
- **Size.** M. After 3.5b and 3.5; before 3.7.

### 5.5 The TOF signal-to-noise column

- **What.** Verify on the testbed's TOF files (sets E, F, J) and two
  Orbitrap sets as controls that `signal_to_noise` tracks height (Spearman
  per file over the picked peaks); read the TOF path of the picker against
  the Orbitrap path. If the column is wrong on a TOF, fix the picker with a
  library test pinning the correlation on a TOF fixture and re-detect the
  TOF sets before the next pass; if it cannot be a per-peak S/N on a
  counting detector, give the library the counting-statistics fallback (the
  height over the root of the height plus the squared detection edge),
  keyed on the instrument class, and have the engine use it on TOF-class
  runs. Either way the run records which S/N it scored on.
- **Why.** The v2 fit reads the column in three places (the absent-line
  charge, the matched-line tolerance, the centroiding width), and step 2.1
  made the engine score on "the file's own per-peak signal-to-noise". The
  reference measured the column on its bromide/nitrate TOF batch and found it
  does not track height (Spearman -0.07 to 0.17 in 28 of 28 files; a
  478-count peak carrying 1.1, a 2-count peak 12) where every Orbitrap file
  gives 0.99 to 1.00; with its fallback 155 of 1,622 per-file Assigned rows
  fell under its Good bar, 89 of them bromide adducts with no 81Br child.
  Sets E, F and J have been read on that column since 2.1.
- **Where.** `mascope_signal.peak` (the TOF branch of the S/N), the
  library's score module, the scoring snapshot.
- **Verify.** Spearman at or above 0.9 on every TOF file after the fix, or
  the fallback recorded on every TOF run; the TOF rows whose fit changes,
  read by hand; Orbitrap sets identical.
- **Size.** S to verify, M to fix. Before 3.4e and 3.4f; can go now.

### 5.6 The import contract

- **What.** The batch import row takes an optional engine tier (the sample
  import's vocabulary since its `engine_tier` column) and an optional engine
  claim as free text; the batch ledger stores them, shows the engine tier
  beside the derived one and offers the disagreement filter the sample
  ledger has; the tier strip and roll-ups gain an engine-tier view, a switch
  off by default. Rows without the fields import as today; the SDK wrapper
  and `sdk_peak_assignment.md` document them and the contract version bumps.
  In the SDK and import docs: the mechanism table is a catalogue of what a
  deployment can name, not a configuration of what an engine should open.
- **Why.** "A batch import row carries the m/z, the neutral and ion formulas
  and the mechanism id but no verdict, so every merged Candidate landed in
  Mascope's batch ledger indistinguishable from an Assigned reading"; the
  reference now sends Assigned rows only (878, 360 and 2,181 rows held back
  on its three batches). And "Mascope's tier column, tier strip, tier filter
  and roll-ups read the tier it derives, not peaky's", which it counts as
  thousands of rows per batch. Decision 20 stands: the derived tier is
  Mascope's; the ask is visibility. The reference also found its runs
  opening every channel the server listed (Mascope 1.10 lists them all);
  our engine opens channels by profile and fingerprint, so the gap is in
  what the docs say the table is. The electron-attachment mechanism it
  publishes with a null id, `[M]-.`, is shipped since 3.3e (v1.11.0) as the
  charge-transfer profile's negative mechanism; the map is the reference's
  to update.
- **Where.** `batch_import.py`, `import_validation.py`, the batch-peak
  records and migration, the SDK's assignment resource, the two ledger
  panes, the docs.
- **Verify.** A batch import with the engine tier on every row shows it and
  the filter; one without is unchanged; no run or gate number moves.
- **Size.** S-M. Between rounds.

### 5.7 Experiment: the file's own locks against the calibration node

- **What.** Run the reference's lock model offline on the gate's Orbitrap
  sets (never on a labelled set until its #77 merges) and read the axis it
  proposes per file (spline, segments, steps) against the calibration node's
  residual after its fit (2.2b's lines) and, on Q Exactive files, the axis
  fingerprint's band steps. Then the call: whether the node takes self-found
  locks as a calibrant source beside the list (4.3's anchors without
  verdicts) with a trend model, or whether the band table covers what the
  locks see and the rest is the bright-peak shift 3.5 caps.
- **Why.** On a nitrate Orbitrap batch whose axis read 0 ppm at m/z 62,
  +2.4 near 200 and +0.2 at 308 in every file, the reference's uncorrected
  run held 307 ions with P, S and Cl formulas on CHON ions; corrected by its
  reference ions it held 641 and its same-formula agreement with a
  hand-curated set rose from 171 to 573; its calibration-free locks (about
  480 recurring peaks whose window holds one closed-shell formula once their
  own isotope lines strike the rivals, a spline, then a walk modelling the
  steps an Orbitrap axis takes, -2.6 ppm between adjacent peaks) took it to
  693 and put held-out locks 0.11 ppm from the model against 1.6 raw. It
  refuses a correction above 4 ppm as "a calibration to fix at the
  instrument" and cannot publish the correction. The plan owner's rule is
  to calibrate before assigning (decision 20): what a file's own peaks can
  say of its axis belongs in the node.
- **Where.** The LAN agent host and the corpus; the result into this section
  and the axis-fingerprint project's calibration milestone.
- **Verify.** A table per file: node residual, fingerprint steps, lock-model
  steps and trend, with their agreement; the call recorded.
- **Size.** S-M. After 3.5.

### 5.8 An identification level in Mascope's own vocabulary

- **What.** A Schymanski-style identification level on every committed
  row, derived from the facts the engine already records: the tier reasons
  (a second channel, a second line, a list, the mass gate, candidate
  density, the absent-line and unresolved-peak rules once 3.4e and 3.4f
  land) and, after stage 4, the batch's series and time-series
  corroboration. Defined once in the layered confidence design
  (`assignment_confidence.md`), shown in the inspector, the ledgers and the
  exports, and carried by the import contract beside the engine's own claim
  (5.6). The tier stays what the fold and the UI read first; the level says
  what a reader may state of the row.
- **Why.** Decision 30, taken 2026-10-14: a community-standard confidence
  scale is the target, and the reference's scale is not ported while it
  moves (two revisions inside its pre-release, three quarters of its top
  level resting on one lock on its labelled-nitrate run, a TOF not
  assessed). Built on Mascope's facts after they stop moving, it is a
  mapping and a surface, not a new engine.
- **Where.** `tiering.py` (the mapping from reasons to a level, beside the
  tier), `assignment_confidence.md`, the inspector and ledger panes, the
  exports, `sdk_peak_assignment.md`, the how-it-works page.
- **Verify.** Every committed row carries a level; the level never
  contradicts the tier (an assigned row holds at least the level a second
  observation earns); on the cylinder sets every certified component
  reaches the identified level through its right reading; the reference's
  claim, imported, is read beside the level on the sets it published, the
  differences explained by one side's facts.
- **Size.** M. After the stage-3 gate (3.7); the mapping is revisited at
  the stage-4 gate, when batch corroboration can lift a level.

### Riders written into planned steps

- **3.4e.** An artifact confirms no line and refutes none: a window only
  artifact rows hold is untestable, never absent, and the probe takes the
  tallest other peak. The absent-line share is the detection curve the file
  itself shows, self-calibrated on the 13C and 18O lines of committed CHON
  rows, a picked line under the share a sighting needs reported as low, not
  absent. On a TOF, bromine and chlorine are judged by the ion's own M+2
  line, and an assigned reading must show it.
- **3.4f.** The threshold is the bimodality criterion (the separation at
  which two Gaussians of a given height ratio turn bimodal), height-ratio
  aware, not a flat width count; the reference's own table was 1.39x too
  wide until 2026-10-08 (equal heights at 2.36 HWHM where the value is 1.70;
  a 1:50 pair at 2.29 FWHM where it is 1.65), so E, F and J are read by hand
  against the corrected criterion before it is fixed.
- **3.6.** The carbon skeleton of organonitrates on nitrate Orbitrap runs:
  an -ONO2 or -NO2 group adds N, 2 O and one DBE, so dinitrates, trinitrates
  and nitroaromatics fail windows written for CHO skeletons; the context
  reads up to three groups' skeletons and a row whose outcome the skeleton
  changed is candidate, never assigned (59 such rows on the reference's
  NOx-chemistry batch, all judged right). The halogen-twin re-read: on a
  15N-nitrate run chlorine and C2H-4 15N are 0.044 mDa apart, so a
  chlorinated deprotonated ion is mass-identical to a labelled nitrate
  reading with two more carbons; a committed halogen-free row whose 37Cl or
  81Br twin sits unexplained at the exact M+2 offset is re-read as the
  halogen formula where it passes every gate (its labelled-nitrate batch:
  merged Assigned 498 to 519).
- **3.3d.** The nitric-acid dimer core as rung zero of the reagent-water
  ladder on any instrument class.
- **4.2.** Merged isotopologue rows read spectrum by spectrum against every
  merged ion's lines by area (median ratio 0.4 to 1.4 over three or more
  spectra, under a tenth above 2x) and stripped to the parent; the batch
  isotope checks as positive facts (the exact-spacing halogen lock, where a
  29Si line decides a chlorine lock's silicon; the 15N twin test; the
  uronium pair); known species decided once by pooled evidence; the merge
  window sized by the batch's own mass scatter (an Orbitrap stays at 6 ppm,
  a TOF merges at 12); the reagent-ion water ladder measured per acquisition
  segment on the batch series, a rung passing on presence, a decoy margin
  and, on a TOF, a carbon-free M+1 (181 rungs and the unexplained share of
  signal from 17 to 6% on its five-day TOF batch; none on an Orbitrap).

### What the reference asks of Mascope, and where each lands

- A verdict and a claim on batch import rows, and the engine tier shown
  where the user looks: 5.6.
- The TOF signal-to-noise column: 5.5.
- The Orbitrap axis (a trend a constant plus 1/mz cannot absorb, steps
  between adjacent peaks, a correction it cannot publish): 5.7, step 3.5,
  the axis-fingerprint project.
- Minor isotope lines censored in the summed peak list: 5.3, then 3.5b.
- A per-line centre in the library's pattern score (it pre-subtracts a
  trend's centre before calling `score_pattern_v2` and reports a
  mass-free ion score): 3.5b's library change takes it, or the trend of
  2.2b is applied before scoring, which is the same thing.
- The scorer's isotopologue label convention (lines counted from the mono
  line, the most abundant isotopologue committed): read explicitly on its
  side; documented on ours with the library's envelope prediction.
- Isotopologues attached to the committed ion, not the compound: 3.3c
  already keys reagent lines on the ion; the envelope claim keys on the row.
- The electron-attachment mechanism: shipped since 3.3e.
- Peak areas in the peaks endpoint and the batch time series: served.

### Not taken

- **The evidence scale and the claims, as an implementation.** The
  reference now carries, beside its tier, a level (3c, 4a, 4b, 5a, 5b on a
  CIMS-adapted Schymanski scale) and a claim (identified, neutral, ion,
  tentative), with the rule that a level-3 unlock must be evidence carrying
  formula-specific information. The premise is taken (a community-standard
  confidence scale is the target, step 5.8); the implementation is not,
  while it moves. Mascope's tier stays assigned, candidate and below
  assignability, with reasons; an imported run shows its engine's own words
  through 5.6 and does not adopt them. Decision 30, taken 2026-10-14.
- Its merge vote, its trace-first and rolling-centre estimators (inert on
  both classes so far, experimental by its own label), its scorecard,
  level-ledger and A/B scripts as scripts (their method is 5.2's), its
  report, workbook and MCP tools.
- Its known-species registry and certified-neutral passes (the note's
  "Not in this plan").

### Decisions proposed to the plan owner (30 taken 2026-10-14)

29. **The reference is pinned at a release, and each peaky minor gets a
    boundary review** (step 5.1).
30. **A Schymanski-style identification level is the target, built in
    Mascope's own vocabulary; the reference's scale is not ported while it
    moves** (taken 2026-10-14 by the plan owner). The level is derived from
    the facts the engine records, after the stage-3 gate (step 5.8); an
    imported run shows its engine's tier and claim beside it (5.6); the
    derived tier stays what the fold and the UI read.
31. **The decoy arms join the gate without a target** until two gates have
    read them; then a target is set from those readings, not from the
    reference's numbers.

### Worth watching

- peaky's #77: its axis correction builds a false -1.5 ppm step on
  15N-labelled batches until the lock space carries the label; no reference
  run on sets C, C2 or G uses it before the fix is released.
- "A peak list holding two axis states (every ion twice, 3 to 6 ppm apart)"
  defeats its lock model: a file whose scans straddle a recalibration is a
  converter-side shape to recognise.
- Very bright Orbitrap ions sitting +0.6 to +1.0 ppm off their formula's
  mass while their own isotope lines sit on centre, so the pattern score
  commits nothing for them: the bright-peak shift 3.5's cap is for, seen
  from the other engine.
- Its TOF picker finding: weak satellites beside a bright line at about a
  tenth of its height, 1 to 1.5 FWHM below and 0.5 to 1 above, which its
  water-ladder decoys had to step around.

## Decisions (taken 2026-09-07)

1. **Presets before rows.** Profiles ship as library presets resolved from
   the ionization mode; the versioned DB rows and settings UI are step 4.5.
   The run config snapshot keeps runs reproducible without a schema change.
2. **The same-ion policy.** The adduct or cluster reading wins when two
   candidates form the same ion; the covalent reading is kept as a flagged
   alternative. It matches the source chemistry, and Stage A's curated
   targets still win their peaks.
3. **Tiers may demote Stage A rows.** The mechanical rules and the mass gate
   apply to curated targets too: a target matched 4 ppm off on a 0.2 ppm
   instrument is not "assigned", and a curated hit within calibration is
   corroborated by its curation.
   *Addendum (2026-09-11, with step 2.4d, #2105).* As built, the demotes
   split by what they doubt. The rules that judge the peak a row sits on
   reach a curated row like any other, since neither checks the source:
   candidate density (formulas the peak's evidence could not separate) and
   the envelope neighbour (a line a committed neighbour predicts on the
   peak). The rules that doubt what a mass search elected do not, because a
   curated row was matched to an identity somebody authored rather than
   elected by one: the radical rule (2.4b), the formula-shape signatures
   (2.4d), the reagent-N rule (2.3), and the minor-channel cap, which only
   the untargeted stage applies. The mass gate (2.2) records a curated row's
   `mass_z` and fits the calibration over it, but counts the curation as the
   corroboration that exempts a row from its cap, so the example above - a
   target matched 4 ppm off on a 0.2 ppm instrument - is not capped by the
   gate as built. On the latest runs of the 43 gate samples, 26 curated
   rows sit beyond the gate's width of three - on A 7 monoisotopic rows,
   the widest at 4.4; on F2 6 monoisotopic and 8 isotopologue rows, the
   widest at 7.1; on F1 one of each; on C 2 and on C2 1 isotopologue row.
   Every one sits where the gate caps an untargeted row, but a cap only
   lowers: 19 are already below assignability on their own evidence, all
   fourteen monoisotopic rows among them, and 4 are at candidate, the tier
   the gate would give them, so applying it to curated rows would move
   only the three isotopologues at assigned - two of C11H15O4 on C and one
   of C3H4O4 on F2, each with an owner inside one width.
   *Taken 2026-09-11: the gate keeps counting curation as corroboration,
   and the 4 ppm example above is superseded.* Ten of the fourteen
   monoisotopic rows are one ion at the low-mass edge, off the same way in
   every sample while the run's centre sits near zero - C3H6O [M+H]+ at
   m/z 59 about -2.3 ppm on all six A samples, NO2- at m/z 46 +10 to +12
   ppm on four F2 samples - and mass accuracy is often poorer at low m/z.
   The gate fits one offset and one width for the whole range, so capping
   them would punish the calibration's shape rather than a wrong identity.
   The other four are single rows: C3H9N at m/z 60 on A, C2H3NO5 at m/z
   120 (+12.3 ppm) and nitrous acid's nitrate cluster at m/z 109 (+5.9 ppm)
   on F2, and the bromide adduct of C3H6O3 at m/z 169 on F1 (-14.9 ppm on
   a TOF run 4.7 ppm wide), which is neither low-mass nor small and which
   the low-mass argument does not cover; all four already sit below
   assignability. Whether the same shape over-caps untargeted rows at low
   m/z is a calibration question, not this decision's; measured the same
   day for untargeted rows, it does not - the gate caps none below m/z 200
   (step 2.2's section, in [`assignment_quality_plan_log.md`](assignment_quality_plan_log.md)).
   *Second addendum (2026-09-14, with #2113). Taken by the plan owner on the
   TOF width fix's brief: the curation the gate counts is the target
   library's.*
   - **The rule.** A reference mirror's row is not curated for the mass
     gate. An isotopologue that tracks it corroborates it, as for a search
     result. Only then does it anchor the run's calibration, and without that
     it is capped when it sits off calibration.
   - **Why.** A seed is a prior matched against every sample, not the
     workspace's curation, and the cap only lowers. Counted as curated on TOF,
     the seed's chance lines widened the gate's own fit from 3.0, 4.3 and 2.1
     ppm to 7.0, 7.3 and 6.7 on E, F1 and F2.
   - **What it leaves alone.** The low-mass argument above concerns the target
     library's rows, which keep the exemption. The rules that doubt a search's
     election (the radical rule, the formula-shape signatures and the
     reagent-N rule) still exempt every Stage A row; whether a curated row
     should carry the same-ion ambiguity is step 2.5c's.
   - **What it moves.** Estimated on the seed round's ledgers, the cap would
     reach 30 mirror rows, 26 of them on F2. Those sit -9.6 to +13.8 ppm off
     runs centred within 1 ppm of zero, and 5 of them are below m/z 200.
     Measured in #2113's round, the gate caps four of them to candidate. The
     narrower width from the same PR holds the other 26 below the cap on
     their own evidence, and the gate caps no other mirror row on the gate.
   - **The ingest fold.** The run-less ingest fold now runs the gate too.
     Its premise that every Stage A commit is curated was pinned by a test
     for exactly this case.
   *Third addendum (2026-09-15, taken by the plan owner on #2131's question):
   the exemption stays for now, and is revisited after step 2.5d.*
   - **The evidence.** With the centre at A's line (step 2.2b), no target
     library row on A sits beyond three widths. The three the exemption still
     protects at assigned are isotopologue lines whose monoisotopic rows are
     on calibration, and read one by one their mass errors say nothing
     against the identity. C3H4O4's M+2 on F2 is a line at 1e-5 to 1e-4 of
     the base peak whose error scatters from -1.9 to +12.3 ppm between
     samples. C11H15O4's 13C2 M+2 is one half of a partly resolved pair with
     its own 18O line, which pushes the two apart.
   - **What was wrong was the entry.** C11H15O4 was written so that
     deprotonation reaches C10H14O's carbonate cluster, on modes that
     declared no carbonate channel. It is fixed on the testbed (the section
     after step 2.2b, in [`assignment_quality_plan_log.md`](assignment_quality_plan_log.md)).
   - **The caveat, as the plan owner put it:** the exemption is kept, and
     treated critically. A curated row is only as good as its list, and step
     2.5d audits the lists before the exemption is weighed again.
   *Fourth addendum (2026-09-15, taken by the plan owner on step 2.5d's
   audit): the cap applies to the target library's rows.*
   - **The rule.** A committed row off calibration is capped unless an
     isotopologue that tracks it corroborates it, whoever proposed the
     formula. A target library's rows still anchor the run's calibration and
     Stage A's width, and still escape the rules that doubt a search's
     election: the radical rule, the formula-shape signatures and the
     reagent-N rule.
   - **Why.** A list names a compound, not where each of its lines has to
     sit. On the reviewed lists the exemption protected three rows at
     assigned, all isotopologue lines of monoisotopic rows on calibration:
     C3H4O4's weak M+2 on F2, and both isotopologues of C10H14O's carbonate
     cluster on C, whose 13C2 and 18O lines push each other apart (step
     2.4e's case). The low-mass argument this decision was first taken on no
     longer protects a row since step 2.2b's centre follows m/z.
   - **What it moves.** Measured on the reviewed lists: those three rows go
     to candidate and nothing else moves - the gate's whole cap goes from 48
     rows to 51 over the 43 runs, with no owner or formula change on 48,894
     peaks and G1, G2 and G6 identical on every set.
   *Fifth addendum (2026-09-15, the plan owner's principle on the lists):
   a formula's presence on a list is evidence, not a label.* A list hit is
   `assigned` only where nothing plausible competes with it, and today
   nothing looks: Stage A wins the peak and the untargeted stage never
   enumerates rivals there. Step 2.5f measures the rivals first and then
   makes the list formula one candidate with a prior, judged by the same
   rules as every row. The fourth addendum lifted the cap's exemption; this
   one reaches the tier a list hit earns against the grid.
   *Sixth addendum (2026-09-16, the plan owner on step 2.5f's measurement):
   a radical is no plausible rival to a list hit.* The rivals the grid adds to
   a list hit's density are closed-shell formulas only. A radical is never held
   at assigned (the radical rule), and counting radicals would have held 173
   list hits at candidate instead of 113, including 18 on D that the
   reference commits and whose every rival was a radical. An election's own
   density still counts every rival; on step 2.4f's round 173 search rows sit
   at candidate over radical rivals alone (F1 71, F2 66, B 20, E 12), which is
   left open.
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

    *A reagent's isotopologues are reagent rows that name no owner.* Owner linkage models one
    thing in this ledger - an isotopologue naming the M0 analyte it belongs to
    - and the import path enforces it, refusing a reagent row that names an
    owner. Rather than widen a published contract as a side effect of this
    step, the isotopologue carries the reagent role (so G4 counts it, as the
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
    rows until 4.3. So after 2.1 the engine computes one fit, v2, with the
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

14. **Step 2.4 is judged on G1 over the rows the reference commits on**
    (taken 2026-09-11 with step 2.4b). G1 counts every assigned-tier row the
    reference does not confirm, and once the tiering's two large rules have
    run, most of what is left on the Orbitrap sets is rows the reference
    commits nothing on at all: 73-97% of the unconfirmed assigned rows on
    each set, on B 1,150 of 1,581, with 342 more the same ion split into a
    different neutral and adduct and 33 contradictions (measured on the 2.4a
    ledgers before this decision). Nothing this engine can read separates
    those silent rows from the ones the reference confirms, so a demote rule
    can close the unconditioned bound only by taking rows the reference
    confirms - which is the trade the demote-only design exists to refuse.
    Step 2.4 is therefore judged on G1 over the assigned rows where the
    reference commits an M0, with the same-ion splits shown beside it rather
    than counted as wrong, since step 2.3 established that they are one ion
    under two conventions; the 20% bound holds on that conditioning for the
    Orbitrap sets (A, B, C, C2, D). The TOF sets (E, F1, F2) are carried to
    step 2.5, which gives them a reference that commits there, and to the
    stage-2 gate at 2.7. The unconditioned G1 is still reported beside the
    conditioned one, so a step that raises it stays visible.
15. **The seed's lists are schema 2 files, and a radical is read from its
    formula** (taken 2026-09-11 with step 2.5a).
    - **The format.** peaky's peak-list JSON is the format of the lists
      Mascope ships, versioned as schema 2. Every list must carry a licence
      and a citable reference. The `custom` CSV stays the path for a user's
      own list.
    - **Radicals.** Radical status is a property of the formula, not an
      authored flag. peaky's own lists set theirs from odd hydrogen, which
      misfiles all 121 nitrogen-bearing rows of the HOM list.
      - Only a list that says `allow_radicals` may hold a radical. So
        decision 7's "radicals as separate lists" is the HOM list's 257 RO2
        formulas as their own list, off by default.
      - The Stage A filter on radicals stays with 2.5b, where the allowance
        moves onto the source row. After the lift, the one radical a default
        seed puts within Stage A's reach is HO2, which bromide CIMS measures
        and its list wants. No public mirror is loaded on the testbed for the
        filter to guard. Keeping the engine unchanged also keeps 2.5a's store
        round a measurement of the seed alone.
    - **Where list facts live.** List-level facts stay in the list file, not
      in each compound's `xrefs`, which Stage A copies into every matched
      row's provenance.
    - **Ions and salts** a source lists as neutrals are left out, not
      recorded with a charge that Stage A would refuse anyway.
    - **Two copies.** The lifted lists exist in Mascope and in peaky, and
      both copies carry the integrity test until step 2.7 settles what peaky
      reads. Publishing the reference library is a release change. *Since
      peaky 0.8.0 (#32) the two copies agree on content: peaky reads radical
      status from parity, its HOM flags say 573 and 257, and its Keller list
      holds the same 50. What 2.7 settles is which copy peaky reads.*
    - **Owner.** It was recorded with the step, from the review's answers to
      the step's format note. Merging step 2.5a makes it the plan owner's
      decision, as merging #2105 did decision 3's addendum.
16. **The reference is frozen at `cc07ce1` until step 2.7a refreshes it
    from peaky's main, and peaky fixes land on main** (taken 2026-09-14).
    peaky's main moved 154 commits past the reference branch's base between
    2026-09-10 and 09-13 (#27-#48, release 0.8.0), all on main: the reflist
    parity fix (#32) was retargeted from the reference branch to main
    because main had the bug and the branch's CI dies at install. Four of
    those change what peaky commits on a sample - the noise-edge height
    gate, list activation on single samples, the mass-dependent centre, the
    labelled reagent's impurity line - so a rebased reference does not
    reproduce today's table. Refreshing it mid-stage would make every
    step's delta unreadable against the round before it; never refreshing
    it would judge the TOF sets against a reference decision 14 already
    called inadequate. So: frozen through the TOF width fix, 2.5b, 2.5c,
    2.2b and 2.6;
    refreshed once before the gate (2.7a); the gate table read against
    both. Step 2.7's branch rule is restated: fixes land on main, the
    reference branch carries only the twelve commits that need the
    unreleased library, and it is rebased on main at 2.7a and at the
    release. peaky's isoprene list is lifted in 2.5b, its mass-dependent
    centre becomes step 2.2b, its persistence admission, predicted
    satellites and vote rule are named in 4.2, and its two new profiles in
    3.1 and 4.5. *Refreshed 2026-09-17 by step 2.7a: the branch is `26e0ff3`, the
    twelve commits and one that re-pins the library to this epic's head.*

17. **A source's window, radical allowance and polarity live on its row,
    and Stage A reads all three** (taken 2026-09-14 with step 2.5b, on the
    implementing agent's brief). The six questions the brief asked, as
    decided:
    - **Polarity gates Stage A**, as a column beside the window, null
      meaning both. It is a detection fact, which 2.5a left to a later
      `tags` column, but the estimate made it a safety: the positive-mode
      lists reach 94 peaks on the negative sets where the reference
      confirms none of them and three assigned rows it does confirm, and a
      2.8 ppm coincidence on an Orbitrap is inside the gate's width. The
      lists already declare it.
    - **The window's meaning.** A shipped list, a list file and a `custom`
      CSV are their own bound and load unbounded; a database mirror loads
      at today's window; the flags override either. The context's window
      is a ceiling: every shipped context opens the six elements at today's
      carbon and mass bounds, and `none` sets none, as the identity it is
      for the ratio windows - the ESI profiles default to it, and the
      siloxane list's own second citation is nanoESI background. The
      migration backfills today's window on every existing row, so null
      (unbounded) is only ever written by the seed, the list adapters and
      the flags.
    - **"A radical filter, off by default"** reads as: no source's radicals
      reach Stage A unless its row allows them. Decision 15's rule carried
      to every source; the other reading, a deployment switch, would leave
      a custom list's radicals in until someone turned it on.
    - **The same-ion ambiguity on a mirror row is step 2.5c**, measured
      alone, right after 2.5b. Folded into 2.5b, its owner changes would be
      unreadable against the window's.
    - **Siloxane content for G6** is what a verified citation supports. The
      target of at most 10 on A stands as written; the residue is reported
      with its parents named and split by whether a cited list could hold
      them, and judged at the gate with the numbers in hand.
    - **The testbed workspace's target library is left as it is** through
      2.5b's round; a clean-up gets its own control round, on the user's
      word.
    - **Two PRs** for 2.5b: the schema and the CLI first, without a round;
      the engine, the list and the round second.

    *Addendum (taken 2026-09-14 with the engine PR's round).* Two questions
    the round raised, decided by the plan owner:
    - **The monoterpene HOM list matches both polarities.** Its header says
      negative, the polarity its source measured in, and the gate took it
      off the uronium sets: 114 rows on A and 419 on B, of which the
      reference had confirmed 17 and 120; the untargeted stage kept 116 of
      those and the round's cost was 20 rows moving away. Ammonium and urea
      CIMS detect HOMs as adducts, which is why the reference commits them
      there, so that list alone is recorded as `both`, with the reason in
      its provenance; the seed's refresh writes it without a version bump.
      The other negative-mode lists - acids, nitroaromatics, iodine species,
      isoprene's products - stay as they are.
    - **IBr2- on the bromide sets is read at the gate.** The reactive-iodine
      list's IBr commits through `+Br-` on 8 peaks the reference reads as
      reagent (D 1, E 3, F1 4), a G4 cost; whether the list keeps IBr on
      those sets or the reagent pre-pass learns IBr2- is decided at 2.7 with
      the numbers in hand. *Decided 2026-09-18 by the plan owner: the list
      keeps IBr, and on a halide channel it is held at candidate. Neither
      reference reads IBr on the eight peaks, but both call the peak reagent
      by a mass-defect rule rather than an identification, and bromide CIMS
      measures the air's IBr exactly this way; only the peak's time series can
      tell the air from the source (step 2.7, the polyhalide rule).*

18. **The reference is a reference, not ground truth, and the assigned tier
    is reserved for near-certainty** (taken 2026-09-14 by the plan owner,
    with the 2.5b round). peaky is itself under development and is known to
    be wrong at times, so a row the reference confirms is evidence, not
    proof, and a row it contradicts is not thereby wrong. The goal is that
    the assignments are as accurate as possible and that no tier overstates
    what the evidence supports: a row is `assigned` only where the engine
    is quite certain of it, and under any uncertainty it says `candidate`.
    So a step that moves rows from assigned to candidate for a stated
    reason is not a regression, and a step that raises the assigned count
    is not better for it unless what the rows say holds up. The 2.5b round
    is read that way: the siloxanes resolve on every peak the reference
    commits them on, and most sit at candidate or below assignability by
    the evidence's own tier, with no rule capping them - which is what the
    tiers are for.
    *Addendum (2026-09-15, the plan owner on #2131).* The same principle
    decides what a doubtful line becomes. Where a line could be an
    isotopologue of an assigned formula but the evidence is in doubt - a line
    overlapping another, or near the noise floor - it is claimed as a
    candidate-tier isotopologue of that formula rather than committed as a new
    M0 (step 2.4e).
    *Second addendum (2026-09-15, the plan owner).* The same principle
    applied to the lists: a formula's presence on a reference list is
    evidence, not a label, and does not mean `assigned` by itself where
    plausible alternatives exist (step 2.5f, decision 3's fifth addendum).
    *Third addendum (2026-09-16, the plan owner on step 2.4f's
    measurement).* A reading whose ion the source is unlikely to make keeps
    its formula at candidate rather than being refused in the search.
    Refused, the peak goes to its next reading, and the doubt does not go
    with it: on the unlabelled nitrate set, the next reading of nitrate
    clustered with an oxygen-free neutral is mostly the same ion without its
    proton, which no rule doubts and which the run put at assigned more often
    than the cap takes. Carbonate clusters are not judged by the rule.
19. **A list's reading counts twice against the grid, and a rival has to
    explain the reading's own lines** (taken 2026-09-17 by the plan owner, on
    step 2.5f's measurement and its first election round).
    - **The prior.** A grid rival takes a list hit's peak only where its
      evidence is past twice the reading's by more than the gap ties are
      counted at. The estimate on the measurement's round, by weight:
      - 1: rivals would take 1,544 peaks, 24 of them where the reference
        commits the list's formula;
      - 1.5: 1,308 peaks, 4 of them;
      - 2: 1,153 peaks, none of them; 1,137 are readings below
        assignability.
    - **The reading's own lines.** The fit charges a reading for a line it
      predicts and the spectrum lacks, and never charges a rival for a line the
      spectrum holds and the rival leaves unexplained. At the prior alone:
      - CHO formulas took 27 siloxane peaks on B whose 29Si and 30Si lines sat
        within 0.1 ppm of the prediction;
      - a formula the tiered measure put at 0 took the one assigned hit, a
        sulfonamide through bromide with four tracking lines.

      So a rival also has to explain every line of the reading's envelope that
      the spectrum was expected to show and whose mass error follows the ion's
      within two widths. It explains a line it matched and predicts at least
      half of, so a trace line of the right mass does not count. A CHO rival
      shares a CHO reading's 13C line, so the lines decide only where the
      envelope is distinctive.
    - **The target library.** A compound of the sample's target library keeps
      its peak, and its closed-shell rivals still count into its density, as
      decision 3's fourth addendum keeps the library clear of the rules that
      doubt a search's election.
    - **Not taken.** Weighing both readings on the measure the row is tiered
      on would re-measure each would-be rival first. About 188 peaks would have
      stayed with the list on the first election round, at the cost of a
      second pass over the list peaks.
20. **One reading per ion, and the reference is read rather than targeted**
    (taken 2026-09-18 by the plan owner, on the peak inspector's tier
    reasons).
    - **The same-ion rule reaches every reagent.** A row whose ion also reads
      as a closed-shell molecule through another of the run's channels is
      held at candidate unless a second channel committed its neutral (step
      2.8). Step 2.3 scoped the rule to nitrogen on the reference's agreement
      with the bromide prior - 246 of 277 lone bromide readings against 79 of
      409 lone nitrogen ones - but the reference prefers the cluster reading by
      a policy of its own, so the agreement measures two priors rather than the
      spectrum. A radical reading is no rival, since the tiering pass already
      refuses radicals the top tier, and the target library stays exempt.
    - **The reference is read, not targeted.** The reference engine's tiering
      is being reworked on its own branch, so a step is no longer tuned
      toward agreement with it. The gate still reports G1 and G2, and the plan
      returns to them when the rework lands.
    - **A mass offset across the range is the calibration node's to remove.**
      The offset term goes into the node's fit (step 3.5) rather than a
      second centre into the engine's scoring: data is calibrated before it
      is assigned.
    - **Declaring a channel does not make its reagent the mode's own**
      (added 2026-09-21 by the plan owner, on the review of step 2.8).
      Carbonate on a nitrate source is an opportunistic side channel: a mode
      declares it so the run can search and match through it, and an
      uncorroborated winner through it stays capped at candidate. A channel
      the profile names secondary stays secondary wherever a mode declares
      it.
21. **The source is read before the batch** (taken 2026-09-24 by the plan
    owner, on a chemist's reading of a customer chamber oxidation dataset:
    six Orbitrap chemistries and a mixed-reagent TOF batch, sets G to J).
    What the reading found ranks above batch corroboration: a profile for
    the charge-transfer source, a formate channel, the source ions named, a
    second channel for an opportunistic one, calibration before assignment,
    and priors with the dataset's context become stage 3; the batch stage
    becomes stage 4 and its engine version 0.7.0. The dataset is judged on
    chemical plausibility alone (G9 to G11), with no reference run: decision
    20's second bullet taken further, since there is nothing to target.
22. **Mixed-reagent modes wait for the ingest split** (taken 2026-09-24 by
    the plan owner). The mixed nitrate and bromide TOF batch (set J) resolves
    to the nitrate profile and the bromide half of its reagent chemistry,
    63% of its intensity, goes unnamed. The engine's fix would be the union
    of every declared reagent channel's library, but such acquisitions are
    not typical, and the ingest routing and splitting design
    (`ingest_routing_and_splitting.md`, #2098 phase 2) splits a
    multi-chemistry acquisition into sample items of one chemistry each,
    after which each item resolves to its own profile. Set J is measured at
    every gate and not gated until then.
23. **The mechanism notation moves to the standard adduct form before 2.0**
    (taken 2026-09-24 by the plan owner). `[M-H]-` rather than `-H+`: the
    sign at the end is the ion's charge, which is how everyone reads it and
    how the reference engine already keys its tables. Sooner rather than
    later, as its own change (step 3.3b) rather than inside a stage-3 step:
    the old form is accepted and the new one shown first, stored rows are
    migrated by a reversible mapping, and the old form is refused at 2.0,
    the release that turns assignment on and makes the notation user-facing
    in the inspector and in the reference engine's publish path. The
    structured adduct row of the ionization method design can follow.
24. **The certified cylinder is a gate set for the charge-transfer source**
    (taken 2026-09-25 by the plan owner). A certified 18-component mixture
    is ground truth for a source profile in a way a chamber dataset is not:
    every committed peak is either in the bottle or it is not. The internal
    Orbitrap's cylinder runs of 22 and 23 September 2026 are cloned to the
    testbed as sets K (the charge-transfer source, m/z 50 to 200), K2 (210
    to 500), K3 (one window from 40) and L (a proton-transfer source with
    protonation declared beside the bare sign), and steps 3.1, 3.3, 3.5 and
    3.6 are read against them. Two things it decided at once: between two
    opportunistic readings that both have a partner, the reading whose
    neutral the sample commits more strongly through a mode channel is the
    row's, not the heavier mechanism's (step 3.1c: the benzyl cation as
    toluene less a hydride, since toluene is 3% of the signal and C7H6 a
    trace); and a window that holds no calibrant is not assigned until it
    has one, so K2 waits on step 3.5 rather than on a relaxed rule.
25. **The assigned tier needs a second observation** (taken 2026-09-25 by
    the plan owner). A row whose evidence is one peak is a candidate at
    most, whatever its fit: a committed isotopologue row, a second channel
    committing the same neutral, or a reference list naming it is what
    lifts it, and the batch's series membership joins them with step 4.2.
    Taken on a TOF row assigned at fit 76% on one line at the noise floor
    that its own batch reads three ways (step 3.4b). It restates decision
    18 as a rule the ledger can show: a row at assigned names what saw it
    twice.
26. **One margin, read both ways, and the declared channel keeps its
    formula** (taken 2026-09-25 by the plan owner, on the pass-8 reading of
    step 3.1c). A reading through the mode's own channel is settled by its
    second channel unless the rival's molecule is shown ten times more
    strongly than the row's own, or the row's own is not shown beyond its
    channel at all; a rival through a channel the run opened for itself
    must beat the declared reading by the contest's own margin, not merely
    fail to lose by it (step 3.1d). Decision 24's scope stands: a declared
    channel never yields its formula to a brighter partner, because the
    brighter partner can be a fragment family, as alpha-pinene's is on the
    proton-transfer cylinder set, and a fragment promoting a wrong formula
    to assigned is the failure the tiers exist to prevent. The rows that
    stay candidate under both are the lopsided ones, with the rival named,
    until the fragments are named (3.3).
27. **The stage 3 gate is read against the literature and chemical
    reasoning, not the reference engine's formulas** (taken 2026-09-25 by
    the plan owner). By the end of stage 3 the engine is expected to read
    a source better than the reference engine does today, so agreement
    with its formulas stops being a target and becomes a reading reported
    beside the others, as decision 20 already says of its tiers. The
    gate's verdict rests on the certified cylinder, which is ground truth
    by construction (decision 24); on the intrinsic metrics G9 to G12; and
    on a chemist's reading of each set's brightest assigned rows against
    the literature of that chemistry, the known oxidation products on the
    chamber sets and the source ions of 3.3, with the references cited in
    the plan and on the how-it-works page. Every disagreement with the
    reference engine is explained by one side's chemistry or reported as
    open, and the comparison tool keeps reporting G1 and G2 so that the
    reference's own progress can be read.
28. **A mechanism's identity across instances is its notation, and the
    chemistry the engine can search under is shipped** (taken 2026-09-26
    by the plan owner, provisioning a fresh instance). The nineteen
    mechanisms the shipped modes declare and the profiles can open are
    seeded at every start, with the target ions of the compounds already
    in the library, so a fresh instance searches under the same chemistry
    as any other and no channel waits on an operator (step 3.3e). The
    identity a mechanism carries across instances is its standard
    notation, unique and canonical since 3.3b; the id is the instance's
    own, fixed where the seed creates the row and kept where it adopts
    one, because the ion, assignment and verification tables reference it
    and nothing that crosses instances needs it equal. Ions are built when
    the row is, as the API builds them, so a library never carries a
    compound with ions for some mechanisms and not others; measured on
    the fleet, that is at most a minute and a half at one start.

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
  demotion auditable, and a verification verdict overrides. From step 2.5f a
  list hit with a rival inside the width sits at candidate by design, with
  the rival named.
- **Stage heterogeneity during stage 1.** Until 2.1, Stage B evidence stays
  on the v1 scale; the stage 1 gate therefore judges search metrics (G2-G6),
  not G1 alone.

## Not in this plan

peaky's residual explainer, ladder gap-fill, labelled-reagent rescue and
certified-neutral passes (77 peaks on the testbed; revisit with the data
after stage 4); retention-time and MS2 evidence (their own designs); the
reagent axis' eventual move into `IonizationSetup` (its target model is
phase 8 of section 10 in
[ingest_routing_and_splitting.md](ingest_routing_and_splitting.md); the
ionization redesign note that held it was retired on 2026-10-22).

## Related documents

- [`assignment_quality_plan_log.md`](assignment_quality_plan_log.md) - the
  measurement log and the as-built specifications of the completed steps,
  moved out of this note on 2026-10-22.
- [chemistry_profiles.md](chemistry_profiles.md) - the profile model this
  plan sequences.
- [assignment_confidence.md](assignment_confidence.md) - the layered
  confidence architecture.
- [peak_assignment_paradigm.md](peak_assignment_paradigm.md) - the engine.
- [peak_assignment_batch_primary.md](peak_assignment_batch_primary.md) - the
  batch ledger stage 4 builds on.
- [verification_calibration_loop.md](verification_calibration_loop.md) -
  verdicts to calibration.
- [reference_data_authoring.md](reference_data_authoring.md) and the
  reference-database seed proposal (2026-09-07) - step 2.5.
- `tooling/assignment_compare/README.md` - the measurement.
- The Linear project "Assignment quality plan" (team Mascope): the steps
  of stages 3 to 5 as issues with their order, read beside this note.
