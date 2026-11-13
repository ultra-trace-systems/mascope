`ULTRA TRACE MASCOPE - DESIGN DOC - ASSIGNMENT QUALITY PLAN`

# Closing the assignment quality gap: the work, step by step

- **Status:** building
- **As of:** 2026-11-11
- **Tracked in:** [Assignment quality plan](https://linear.app/ultra-trace/project/assignment-quality-plan-553f4854192c) (Linear, MAS-37 to MAS-73, MAS-408 to MAS-410, MAS-443, MAS-568 to MAS-570, MAS-609, MAS-708, MAS-709, MAS-777 and MAS-823 to MAS-841)
- **What:** The step-by-step plan closing the assignment quality gap: stages, gates, decisions and risks.

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
of 2026-09-24 (decision 21). Stage 4 starts with 4.1: it was pulled ahead
of the stage 3 gate on 2026-10-30 (decision 33) and, until the truth
corpus has a baseline, is judged as every stage so far, against the gate
sets and the reference pinned at a release (decision 34, 2026-11-05).
Milestone 6 (2026-11-11) builds the bench that runs both engines on
demand on the agent box, where the agents run it themselves
([`assignment_bench.md`](assignment_bench.md)). Of milestone 5, the
reference's 0.10 review, step 5.5 is built (#2377), 5.2 is built and
waits for its first reading, 5.3 is measured and the rest are planned; a readiness check against the reference's main on
2026-10-22 (the last section of the review) found nothing new to bin and
added step 5.9. Step 5.10 comes from the instrument's own centroid flags
(a census of 2026-10-23), not from the review. Every planned step is an
issue in the Linear project Assignment quality plan (MAS-37 to MAS-73,
MAS-410 and MAS-443; step 5.3 is MAS-68, measured; decisions 29 and 31
were taken on MAS-408 and MAS-409 on 2026-10-23). The measurement log and the specifications of the completed
steps are in
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
| 3.3d - source-solvent clusters | #3128 |  | built: the reagent pass climbs the solvent ladders of the charge-transfer and urea sources (water, methanol, ethanol, acetone; the rungs a work names) and the nitrate acid dimer core's water ladder, claiming a rung above a core the spectrum shows, contiguous, on mass and step, falling, and displacing a molecule only where the core stands ten times its own peak through another channel, never with water; read offline on the testbed, the acetone dimer leaves C6H12O2 on the three I+ samples that show it and on five of L's six, nothing on K, K3 or the nitrate sets; no tier rule moves; MAS-57 |
| 3.3e - the chemistry a run searches under is shipped | #2217 | 2026-09-26 | measured (pass 10): the shipped mechanisms are created at every start; no channel unavailable |
| 3.3f - a list is read through the channels the run opened | #2220 | 2026-09-26 | built: Stage A reads the lists through the channels the run opened; pass 11 measures it |
| 3.4 - an opportunistic channel needs a second channel | #3019 |  | built: a search row through a secondary channel reaches assigned only on a list naming its neutral or the neutral through a mode channel; its isotopologue is recorded and no longer lifts the cap; a list's reading through an opened channel keeps 3.3f's rule; tiering rule set 11; MAS-56 |
| 3.4b - one peak is not enough | #2212 | 2026-09-25 | measured (pass 9): G12 at 0 on every set |
| 3.4c - measure an alternative before committing | - |  | built: a derived row's alternative (a batch peak's identity) is measured on the sample's member before *use this* pins it, through the propagation's own scoring (`batch_untargeted.measured_reading`), so it shows the fit, m/z error, lines found, plausibility, evidence and the tier the pin writes there, beside what the member reads now; nothing is written; the pin records the measurement under `provenance.manual.measured`; the run-backed shortlist kept its on-demand scoring (`alternatives_scoring`); no tier moves; MAS-58 |
| 3.4d - the columns of a tiering still in progress | #2223 | 2026-09-26 | built: the listed-as column, the probability hidden while provisional, the reagent anchor by majority |
| 3.4e - an expected line that is absent | - |  | built: a committed monoisotopic row whose predicted envelope holds a line the fit's detectability gate asks for (the file's curve threshold, else an expected S/N of 3) and the spectrum lacks is capped at candidate with the reason `absent_line`, every source asked (a hand assignment when it is made, at the gate its run recorded), no second channel lifting it; a window only artifact rows hold, or a line beyond the peak list, is untestable; on a TOF a bromine or chlorine reading must show its own M+2 line; each row records its asked lines (`predicted_envelope`), which the inspector lists and the chart draws as empty circles; tiering rule set 11; the next pass measures G13 |
| 3.4f - a peak that is not separated | - |  | built: a committed monoisotopic row whose peak another peak of the spectrum sits closer to than the bimodality criterion for their height ratio (0.85 widths at equal heights, 1.65 at 1:50, the width from the file's resolving power at the peak's m/z) is capped at candidate with the reason `unresolved_peak`, naming the nearest such neighbour; the row's own envelope lines are no neighbours, nor is a peak under 0.3 of the row's peak's height, so the taller of a pair is asked only beside a neighbour at least 0.3 of its height (decided 2026-11-10); the one escape is another channel committing the neutral on a separated peak (`cross_channel.separated_through`), no list or series; each row records `peak_separation`; the inspector links the neighbour; tiering rule set 13; read on E, F and J before it was fixed (below); the next pass measures G14 |
| 3.4g - the chart draws each peak in the trace's unit, and a lone M0 | #2379 | 2026-10-19 | built: the lone M0 drawn, the envelope in the trace's unit; engine and tiers untouched |
| 3.4h - the inspector links the other channels, and the listing column folds | #2988 | 2026-11-05 | built: the badge opens the other channels' peaks as links, the listed-as column folds to a mark with a view-menu switch in both ledgers, and rows whose peak the sample no longer has are marked (#2025); no tier moves |
| 3.5 - calibrants below the brightest lines, an offset term, and the low-mass bend (calibration node) | #2593 (the gate's anchors) |  | in two PRs (decided 2026-10-22). Built: the mass gate's fit takes the reagent pass's monoisotopic lines beside its commits; estimated on the pass-10 ledgers, all seven K3 files fit a calibration. Planned: the cap and the offset report (MAS-469), landing with 3.5b |
| 3.5b - the envelope's tolerance, fitted per run | #2978 |  | built: Stage A fits a response curve per Orbitrap file (pooled with its batch siblings at the same settings, then the defaults) and a centre and width per TOF run; every fit score of the run reads it; pass 11 measures it |
| 3.5c - the targeted matcher reads an isotope line over the scans that hold its anchor | - |  | built: an Orbitrap line's ratio is read over the scans it shares with its anchor, the count stored and shown in the Match tab; TOF unchanged |
| 3.5d - the peak store keeps each Orbitrap peak's scan presence count; the scorer reads a line against its presence-corrected share | - |  | built: a raw Orbitrap store keeps `n_scans` and `n_scans_total` per peak, the peaks route, the batch table and the SDK serve them; the fit score reads a counted line at its mean per scan (the censored-normal estimate from its count and S/N); an absent line keeps the curve's threshold (the count's own, a hard gate, is held back for a soft one: #3123); a stage serves no counts until its S/N is its own (#3117); 3.5b's curve is the fallback for a file without counts; the gate's pass measures it |
| 3.6 - priors and the dataset's context | - |  | planned; two riders from the reference's 0.10 line (the review section) |
| 3.7 - stage 3 gate, engine 0.6.0 | - |  | planned; read against the literature and chemical reasoning, not the reference (decision 27) |
| 4.1 - series detection on the batch ledger | - |  | built: *Detect series* walks CH2, O, H2O, CO, CO2, C2H2O and, from a peak carrying their element, C2H6OSi and CF2 up to three steps from the batch's assigned peaks onto its unassigned and candidate ones within the search's window, commits a unit at 12 proposals and ten times its six decoys' mean (re-derived on the testbed: 3x commits every TOF family), measures each proposal through the propagation chain, records the series with `has_anchor` on the batch peak, and a run counts it as a second observation; tiering rule set 12; MAS-61 |
| 4.2 - time-series coherence | - |  | built, the co-variation reading (decision 35): *Read co-variation* reads every neutral the batch sees through two channels, per mode and instrument type, as Pearson r of the log traces, reagent-normalised, the common mode (each spectrum's median of the persistent traces) removed; co-varying at r >= 0.6, untestable under 10 shared spectra or a residual spread under 0.02 in log10 (both measured against decoy pairs on the testbed's 20 batch ledgers); recorded on the batch peak, shown in the ledger and the inspector, no tier moves. The nine riders are steps of their own (MAS-777); MAS-62 |
| 4.2c - predicted diagnostic isotopologues in the batch fold | - |  | built: *Stamp isotopologues* predicts the 15N, 18O, 34S, 29Si, 30Si, 37Cl and 81Br lines of every committed monoisotopic batch peak at its trace centre (a labelled atom is the label's; a line under half the peak it falls in at the anchor's resolving power is unresolved), finds the batch peak there, and in each sample holding both reads the height ratio inside that sample's fitted envelope tolerance (two widths; a sample whose two widths reach past half the ratio cannot tell); a line holding in 80 % of three or more such samples is stamped in each, `predicted` or `picker`; recorded on both batch peaks and the member rows, no tier moves; MAS-824 |
| 4.2f - merged isotopologue rows | - |  | built: *Strip isotopologue rows* reads every committed monoisotopic batch peak at the 13C and diagnostic lines of every brighter one below it (four times the partition's mass scatter, at least 1 ppm); a row whose height over the parent's is a median 0.4 to 1.4 times the line's share over three or more samples, fewer than a tenth above 2x, is stripped: its members and the samples' in-app rows become the parent's isotope line (recorded on both), so the consensus folds it under the parent; curated, hand-assigned and own-13C rows are held, imported runs never rewritten; read on the copy's batch ledgers, G6 on the batch ledger falls on A 9 -> 7, B 62 -> 56, D 46 -> 41 and holds on C and C2; MAS-827 |
| 4.2g - batch isotope checks as positive facts | - |  | built: *Read batch checks* records three display-only facts on a batch peak's reading: the halogen lock (a 37Cl or 81Br line 4.2c stamped, its median offset within the batch's position window; where the window cannot tell 30Si, the 29Si line a silicon reading implies decides), the label twin (a labelled ion's 14N twin at the impurity share the batch's own reagent ions show, the setup's purity where none does, present, absent or untestable where an absent line would be charged) and the uronium neutral pair (4.2's co-variation of [M+H]+ and [M+CH4N2O+H]+); on the copy's batch ledgers D locks bromine on 144 bromide clusters (71 of its 121 assigned), C2's labelled cores read their twins present in 88 of 88 samples at 0.42 of the 98 % share, no unlabelled ion is read, A and B count 99 and 458 uronium pairs; no tier moves; MAS-828 |
| 4.2h - known species decided once by pooled evidence | - |  | built: *Decide known species* reads each library ion a sample's reagent pass claimed (not a fragment) and each formula a list tags `background`, per mode and instrument type; present where its peak is in two samples or more and half or more, and one sample's own test held (the claim, or the listed formula at candidate or better), absent where none held, unpooled under that; a present species is claimed in every sample its peak is in (members and in-app rows: a library ion as the pre-pass's claim with its lines, a listed compound at candidate), hand, target, another ion's line or claim and a listed reading with lines of its own kept and recorded; where the fold split a trace, one batch peak claims (the one in the most samples whose claim still turns its reading once the samples reading the species on the other are kept) and the other is held; a sample's next run claims a present library ion on the batch's word; co-variation with the core recorded only (decision 35); the copy's ledgers and runs predate the claim registry, so the Verify reading is the planner's pass; MAS-829 |
| 4.3 - calibration from verdicts, per profile | - |  | planned |
| 4.4 - gate automation | - |  | moved to milestone 6 as step 6.4, the gate card as code (MAS-64); see `assignment_bench.md`. Built: `compare_runs.py --card` prints G1 to G14, the mass error, the reason coverage and the decoy arms per set and pooled, each metric a function in `metrics.py`; `--gate` marks them against the stage targets of `thresholds.json` with a pass-or-fail line, `--baseline` lists the readings that moved, and the scrub refuses a card that holds a name; MAS-64 |
| 4.5 - profiles as versioned rows, routing by detection | - |  | planned |
| 4.6 - a structured adduct row beside the notation string | - |  | built: the row's columns on `ionization_mechanism`, filled by a migration that refuses an unreadable row; `[2M+H]+`, `[M+2H]2+` and `[M+H-H2O]+` read, written and given target ions; the finder and the engine leave them out; corroboration keys unchanged; moves no metric; MAS-318 |
| 4.8 - blend splitting: a batch peak whose members disagree along intensity or time | - |  | the flag half built, the split not: after every vote the two leading formulas are tested for a clean parting, intensity by a Mann-Whitney rank test (p 0.01, 90 % of pairs one way), acquisition time by an exact runs test (p 0.01, at most half the runs an interleaving gives), over the members at candidate or better, never two formulas reading one ion; a probable blend is recorded on the batch peak (`blend`), marked in the ledger with a filter, both formulas on the chart; read on the testbed's nine fully folded ledgers, 92 flags (0 on set A's batch, 1 on the largest analysis batch), four of ten read by hand two compounds on one peak, so the split waits; no tier moves; MAS-320 |
| 4.9 - refuse or segregate mixed-instrument batches at the fold | - |  | built: segregated; the fold searches a batch's anchors per mode and per instrument type, read off the anchor's intensity unit; the ledger and the series name the partition (`instrument_type`), the Batch peaks ledger shows one at a time and the chart draws only its selection; a single-instrument batch folds as before; moves no metric; MAS-321 |
| 4.11 - temporal continuity: the ratio-stability pass, its columns and its endpoint | - |  | built: every isotope line and its family root, and every two channels of one neutral, at assigned or candidate, graded on the within-sample ratio across a time-ordered batch (spread 1.4826 x MAD, drift by Spearman rho and a Theil-Sen change): stable, drift (the traces co-vary per 4.2), red, inconclusive or insufficient, on two `batch_peak` columns the ledger serves with a stale mark; run by its endpoint and at a rebuild's end, the fold trigger off for 4.13; on the testbed's six folded analysis batches 1,752 pairs, 1,008 stable, 122 red, 24 drift, the isotope reds a dim line's response; no tier moves; MAS-323 |
| 4.15 - Stage B winner confidence | - |  | built, off by default: `stage_b_confidence` on the run config measures the formula-only shortlist of the untargeted stage's committed winners, the most intense first up to `stage_b_confidence_peaks` (200), in one seeded pass through every channel the run searched, scored at the width the winners were re-scored at, and stores each winner's `confidence`, `n_candidates` and `is_tie` in provenance as Stage A does, one candidate per ion on the peak (two splits of one ion are one hypothesis, the winner's own ion no rival) (`alternatives_scoring.measure_winner_confidence`, the inspector's placement rule); a winner past the budget records none; `search_scope.winner_confidence` records the counts and the seconds; `p_correct` stays null on Stage B rows (the scale now matches, the curve's population and the batch consensus that reads it do not); no tier moves; the runtime on the gate sets is the bench's to record; MAS-325 |
| 4.19 - the active-learning verification queue, with the three calibration-label calls | - |  | built: `GET /peak-assignments/sample/{id}/verification-queue` ranks the run's unjudged rows within 0.1 evidence of a band edge by closeness and by the labels the instrument class and reagent profile hold within 0.05, with seeded spot checks every fifth place and stale verdicts (a later run reassigned or rescored the judged peak) first; the ledger's *to verify* chip and column; no `p_correct` or evidence in the queue; the refit weighs labels by evidence level (reference standard and MS/MS 1, orthogonal 0.6, pattern 0.4, visual and unstated 0.25) and records the weights on the curve (`label_weights`); per instrument and profile, down-weight, keep the snapshot and mark it stale (section 7 of `verification_calibration_loop.md`); moves no metric; MAS-329 |
| 5.1 - the reference pinned at a release, a boundary review per peaky release | - |  | planned 2026-10-13; the first review is the section below; readiness check against the reference's main on 2026-10-22: nothing new to bin, 0.10.0 still uncut |
| 5.2 - decoy arms in the gate: a shifted axis and swapped adducts through the in-app engine | #2981 |  | built: a `decoy` block on the run config (`mz_shift_ppm`, `mz_shift_da`, `swap_adducts`), every stage at the control's calibration and envelope tolerance, the arm on the run row and out of the fold, the listing, the verdicts and the retention quota; `compare_runs.py --decoy` reports the share held below and above m/z 350; read in the next pass, no target until two gates have read them (decision 31); a shifted arm looks for the source's own ions (the channels' carriers, the reagent lines) on the axis as stored, so it opens and claims what its control did (MAS-720, #2980) |
| 5.3 - measure the minor-line response: isotope lines by height and by area against expected S/N | - |  | measured 2026-10-16 on the pass-9 ledgers; 3.5b's form decided 2026-10-22 (decision 32) |
| 5.4 - the side lobe's signatures: width-scaled offset and narrowness in the sidelobe flag | - |  | planned; after 3.5b and 3.5; a TOF rider from the readiness check of 2026-10-22 |
| 5.5 - the TOF signal-to-noise column: verify it tracks height, fix the picker or fall back to counting statistics | #2377 | 2026-10-16 | built: the picker's TOF noise is now the baseline's MAD; measured next on E, F, J |
| 5.6 - the import contract: a verdict on batch rows, the engine tier where the user looks | - |  | built: `engine_tier` and `engine_claim` on a batch import row, kept on the batch peak where its consensus names the row's identity; an Engine tier column, an Engine disagrees filter and an Engine tier view of the strip, off by default; the mechanism table documented as a catalogue; no run or gate number moves; MAS-71; the member rows, the ledger CSV and a sample shown from the batch ledger carry the verdict too (M0 rows reading the consensus only), MAS-727 |
| 5.7 - experiment: the file's own locks as calibrants, read against the calibration node's residuals | - |  | planned; after 3.5; feeds 4.3 and the axis-fingerprint project; the labelled sets wait for a release carrying the reference's #77 |
| 5.8 - an identification level in Mascope's own vocabulary | - |  | planned 2026-10-14 (decision 30 taken); after 3.7 |
| 5.9 - the width a run scored at, served on the run and sample records | #2784 | | built: `sigma_ppm`, `sigma_source`, `mu_ppm`, `mu_source` on the run record and in `list_runs` (an import discloses them in `scoring_width`); `line_width` on the sample record; no run or gate number moves; MAS-410 |
| 5.10 - the instrument's side-lobe flag: Exception-majority peaks into the satellite flag | #2779 |  | built: a peak most of whose per-scan centroids the instrument flagged is a satellite, unless on an isotopologue line; the averaged profile kept as measured (MAS-577); MAS-443 |
| 5.11 - a batch's peak lists in one read, and the SDK snapshot of it | #2764 |  | built: one Parquet read per batch, `load_peaks` takes it and keeps the per-sample reads for older servers, `save_path`; MAS-568 |
| 5.12 - the per-sample scoring derivation as one library function, and a bulk candidate scorer | #2767 | | built: `pattern_scoring_for_sample` and `score_candidates` in `mascope_tools`, the engine and the finder call them; no score moves beyond rounding; MAS-569 |
| 5.13 - the current verdicts of a batch or dataset in one read, through the API, the SDK and the export | - |  | built: one row per judged peak, a batch-level verdict marked `borrowed` where it reaches a sample, a sheet of the batch export; MAS-570 |
| 6.2 - the bench instance, built from a sets manifest | - |  | built: `tooling/assignment_bench/bench.py init <manifest>` binds env `bench` to a free instance slot, starts a ref's backend there, seeds each set's mode and collections through the API and the shipped reference lists, uploads the sets' files by hash and checks every sample resolved its set's mode and has peaks; each sample's id is recorded under its set in the bench's `state.json`, a second `init` changes nothing; `manifests/demo.json` holds the demo bundle's sets Db (bromide, 80 files) and Du (uronium, 81 files), built on the agent box; MAS-837 |
| 6.3 - the reference at a pin, published into the bench instance | - |  | built: `tooling/assignment_bench/bench.py reference --pin vX.Y.Z` installs peaky at the release tag into the bench's own tool environment, runs `peaky assign` on every sample of every set with the set's profile and window, and publishes each run with the engine badge `peaky` and the installed version string; cached by pin, set, sample and `mascope-tools` version, a second call publishes nothing, a new pin publishes every set again and lists the sets whose reference changed; on the agent box Db and Du carry peaky v0.9.0 (`0.9.0+assign0.6.1`); MAS-838 |
| 6.5 - bench run: a ref's in-app engine on the bench sets beside the pinned reference, the card and its diff | - |  | built: `tooling/assignment_bench/bench.py run <ref>` builds the ref into the bench's worktree, refuses a ref older than the bench database's alembic revision and otherwise migrates it forward, starts the ref's backend on the bench slot, runs the in-app engine with the engine's defaults on every sample whose reference run is the pin's (`--decoys`: the class's arms against it), within a budget per set, and cards the sets with `compare_runs.py --card --gate`, scrubbed again with every name and id the instance holds; `--baseline` diffs against develop's rolling baseline, which a whole run of develop's head replaces; the backend stops and the worktree goes after every run; one run at a time behind the bench's lock; MAS-840 |
| 6.6 - the posting and the nightly baseline: bench run posts its card on the PR, and develop's card is made every night | - |  | built: `bench.py run <ref> --post <pr>` posts the scrubbed card's gate line, headline table and moved headline readings on the PR as one comment, the whole card and each set's collapsed, edited in place on a re-run; a refused card posts the file and the kind of name, a failed run its error's tail, everything scrubbed again; a fork is refused; `--post` diffs against develop's baseline but not one older than the PR's merge base; `tooling/systemd/bench-baseline.{service,timer}` run `bench run origin/develop --baseline` nightly as user units; MAS-841 |

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
  store) and running `compare_runs.py --card --gate`: the card is the
  plan's table as code, one column per set, with a pass-or-fail line
  against the stage targets (`tooling/assignment_compare/README.md`, "The
  gate card"). Its numbers go into the measurement
  log in [`assignment_quality_plan_log.md`](assignment_quality_plan_log.md),
  which also holds the metrics and their stage targets in the table at its
  top. A step that moves a metric the wrong way is discussed before merge,
  not merged on green CI alone. Milestone 6 turns this pass into a
  command: the bench ([`assignment_bench.md`](assignment_bench.md))
  runs both engines on fixed sets on the agent box when a worker asks
  and posts the card on its PR; once its sets read like the gate sets, the card is
  the measurement and this pass confirms it.
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
  in public form, which is what step 6.4's card can run in CI. A stage gate is
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
- **The truth corpus joins the gate when it exists; nothing waits for it.**
  The boundary note (`peaky_mascope_boundary.md`) and the Linear project
  *Assignment truth corpus and bench* build a vetted table of true and
  false readings and one scorer over it, owned by peaky's owner. Until
  its baseline cards exist, every step here is judged as the stages
  before it: the gate sets, the reference's runs published at a pinned
  release, G1 to G14 with G1 and G2 as readings, the intrinsic metrics
  where no reference run exists, the certified cylinder, and the decoy
  arms once 5.2 lands (decision 34). When the baseline exists, step 4.20
  adds the corpus card to the gate and reads it once over every step
  merged before; a step whose card fails the acceptance rule is reopened
  as a finding. Stage 4's batch components are written from this note's
  riders and the reference's published docs, reviewed per release (5.1)
  and never ported; the specifications peaky's owner writes for the batch
  isotope checks, the evidence levels and the axis lock model are a
  cross-check when they arrive, not a prerequisite.
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
- **As built (MAS-57).** A ladder claim beside the cluster claim
  (`reagents.match_ion_ladders`, `reagent_pass.claim_ladder_peaks`), on the
  masses the cluster claim's anchors corrected and the peaks it left. The
  profiles carry the solvent lists (`ReagentProfile.source_solvents`): water,
  methanol, ethanol and acetone on `EASYIC_POS` and `UR`. A ladder holds the
  rungs a work names, the library's rule: methanol's (S)nH+ to n = 4, ethanol's
  dimer, acetone's to n = 4, each solvent's S.H3O+ and methanol's dimer on a
  hydronium, and [S-H]+.S for ethanol and acetone [gri73, lar82, mot84, nist];
  a mixed-solvent cluster, ethanol's trimer and methanol less a hydride are
  named by no work found and not built, and neither is the reference's
  condensation rung (a cluster less water). The rules as the brief writes
  them: the core (the protonated solvent, or the solvent less a hydride where
  the run searches `[M-H]+`) is a peak at an anchor's share of the base peak
  and is not claimed; the rung below is a peak of the spectrum; the rung and
  the step fit at the run's precision; a rung stands at most three times the
  rung below it. "Outweighs the analyte's by a bar" is read as the ladder's
  core against the molecule's own evidence: a rung whose ion also reads as a
  closed-shell, bondable formula with carbon through one of the run's channels
  displaces it only where its core stands ten times taller than any unclaimed
  peak that molecule shows through another channel; the reference's share of
  the spectrum's signal (0.1%) would leave two of the three I+ dimers, at 0.01
  to 0.02% of the signal. A water rung never displaces such a reading
  [good70, sun88]. The nitrate profiles' water ladder takes `(HNO3)2.NO3-` as
  rung zero and its first three hydrates, on any instrument class, under the
  same rules; their rows say the ladder shows them (`observed`). The rows are
  the pre-pass's, family `solvent` (the nitrate hydrates `reagent`), and
  record their ladder (`provenance.reagent.ladder`); the reagent-line offset
  is read off the library's lines alone, as it was measured. Read offline on
  the testbed's stored peak lists with the branch's pre-pass: on I+ the
  acetone dimer is claimed in the three samples that show it (each a
  C6H12O2 candidate before); on L in five of six (one C6H12O2 assigned, four
  candidate) and acetone less a hydride with a second one in one; on H one
  dimer (a C6H12O2 assigned); nothing on K, K3, A, B, C, C2, G or the nitrate
  TOF set, where the dimer core shows no hydrate at the run's precision. G10
  is the plan owner's pass.

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
- **As built (MAS-56).** "A list names it" is read as the lists the sample
  was read against in Stage A: every neutral of the target library and of
  the reference lists in the known set, matched or not, compared as
  compositions (`engine.listed_formula_identities`). The list is recorded
  ahead of a second channel where both hold (`corroborated_by: "list"`),
  since the partner gate puts the cap back when the second channel it read
  is gone and a list cannot go. The search row's verdict records its line
  (`isotopologue`), and the cap's reason says it lacked a second channel
  and a list, and that the line gives the carbon count only. A list's own
  reading through a channel the run opened (3.3f,
  `hold_opened_channel_readings`) is left as it was, an isotopologue or a
  second channel lifting it: the list is why it was read, so it is no
  corroboration of it, and what the search's reading lacks, a name for the
  neutral, it has. A batch search reads the same lists' neutrals, the
  target library through the mode's mechanisms and the reference lists
  through every searched one, without matching them, so a list lifts its
  cap as it does a run's (#3018).

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
- **As built (MAS-58).** The alternative that showed *not measured* is a
  derived row's: one of the batch peak's identities, which *use this* pins
  batch-wide and the propagation then measures in every sample. So the read
  route is the batch peak's (`GET /batch-peaks/batch/{id}/measure-candidate`,
  `batch_curation.measure_batch_peak_candidate`) and measures one identity on
  one sample's member with the propagation's own scoring
  (`batch_untargeted.score_in_sample` and `measured_reading`, which the
  propagation now calls too): the fit, the evidence and the tier banded under
  the default configuration's thresholds, as the pin writes them, with no
  tiering rule, since the pin applies none either. Beside them the m/z
  error, the envelope's lines with the peaks they paired to, and what the
  member reads now. An identity the pin would refuse (an isotope line) or
  one the member already carries is answered with the reason, unmeasured.
  The inspector measures each such alternative on focus, one request at a
  time, and *use this* sends the sample, which the task measures again and
  records on the pin (`provenance.manual.measured`); the pin's note shows
  it. A run-backed row's formula-only shortlist keeps its own on-demand
  scoring (`alternatives_scoring`); a runner-up the run competed carries
  its numbers already.

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
- **As built (MAS-55).** The threshold is the criterion itself, height-ratio
  aware, from its closed form (Robertson and Fryer 1969) rather than a table:
  equal heights at 2 sigma, 0.85 widths at half maximum, a 1:50 pair at 1.65.
  The width is the file's resolving power at the peak's m/z, as detection
  fitted it: the peak store keeps no width of its own per peak. Every other
  peak of the spectrum is a neighbour, a source ion or an unexplained peak
  included, but for a peak within the match window of one of the row's own
  predicted lines and a peak under 0.3 of the row's peak's height. A hand
  assignment is not asked.
- **The taller peak of a pair (decided 2026-11-10).** The shorter peak of an
  unseparated pair is always capped; the taller only beside a neighbour at
  least 0.3 of its height (`NEIGHBOUR_HEIGHT_FLOOR`), and the partner escape
  reads a partner's peak by the same test. Capping only the shorter would be
  a coin flip near 1:1, and would leave the taller of a near-equal pair
  assigned: 0.85 widths from a line at 0.8 to 0.95 of its height, the blend's
  maximum sits 0.16 to 0.25 widths off the taller line and 15 to 19% over its
  height. Such a pair is unseparated closer than 0.90 to 0.98 widths, where
  the capped pairs sit. Capping both caps a tall peak beside a small
  shoulder, whose maximum the shoulder moves under 0.04 widths at 0.3 of its
  height, and which can be its own tail split off by a TOF picker's fit or an
  Orbitrap's ringing (set D).
- **The floor at 0.3, not half (decided 2026-11-10).** The floor was first
  set at half; the reading at half (below) found 60 of the taller rows beside
  a neighbour at 0.3 to 0.5, and the call moved it to 0.3. Read as two
  Gaussians at the pair's distance and ratio, the blend puts the maximum a
  median 1.5 ppm off the taller line beside a neighbour under 0.3, about the
  line's own mass error (the rows show a median 1.0 ppm); at 0.3 to 0.5, a
  median 7 ppm (90th percentile 15 ppm, the run's match window); at half and
  up, 23 ppm. The stored mass errors do not clear the rows at 0.3 to 0.5: the
  rows at half and up show the lowest of the three (a median 0.80 ppm), so a
  flat mass error that cleared one group would clear the other, and with it
  the rule's premise that the picker's split inside one peak is not trusted.
  Under that premise 0.3 is where the doubt ends. The cost is 55 more TOF
  rows at candidate than at half, each naming its reason and linking its
  neighbour.
- **The reading**, read-only over the testbed's newest stored ledger of each
  gate sample (rule set 11) and the sample's own peaks:
  - Assigned monoisotopic rows the rule caps: E 9 of 111 (8%), F's bromide
    half 79 of 363 (22%), F's nitrate half 351 of 733 (48%), J 40 of 368
    (11%); on the Orbitrap sets A 0 of 883, B 5 of 4,021, C 0 of 364 and D 0
    of 733: 479 TOF rows. A floor at half took E 8, F 67 and 314, J 35 and B
    2 (424 TOF rows); capping both peaks of a pair took E 14, F 96 and 399, J
    46 and B 8 (555). Every row the floor at half capped is still capped, and
    no row the symmetric rule left alone is.
  - On 224 of the 555 the row's peak was the taller of the pair. Its nearest
    unseparated neighbour stood under 0.3 of its height on 65, at 0.3 to 0.5
    on 60, and at 0.5 and up on 99. The floor frees 57 of the 65 (the other 8
    sit beside a second neighbour of at least 0.3 of their height) and keeps
    54 of the 60 and 97 of the 99; a partner now read as separated lifts the
    other 6 and 2.
  - The 60 at 0.3 to 0.5 sit a median 0.89 widths from their neighbour, at a
    median 156 ppm width, and the blend's maximum stands 5% over the taller
    line's height (16% at the 90th percentile). Their stored mass errors do
    not show the blend's shift: a median 1.0 ppm, against 1.2 ppm for
    separated rows. Either the picker's fit placed their centroids, or their
    formulas followed the shifted centroid; the ledger cannot tell which.
  - The capped pairs sit a median 0.82 to 0.94 widths apart. In the raw
    summed TOF profile, 546 of the 555 pairs the symmetric rule capped show
    no dip between the two centroids: the spectrum holds one peak with a
    shoulder there, and the picker's fit reported two.

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
- **Decided (2026-10-22, the plan owner).** The step lands in two PRs:
  the gate's anchors first, on their own, then the cap and the offset
  report together with 3.5b. The cap is two optional node parameters,
  whichever is lower applying: `calibrant_tic_share_max`, a calibrant
  line's share of its stream's ion current (the polarity's for a file
  fit), and `peak_intensity_max`, beside `peak_intensity_min`. TOF off;
  on the Orbitrap at the knee of each line's error against its TIC share,
  measured first. The offset is fitted and reported in the fit's quality
  block, not applied: the record stays a factor until the axis-fingerprint
  project's reader change (MAS-18) makes the factor a function of m/z, and
  applying the offset is a follow-up there.
- **Built: the gate's anchors (#2593).** `fit_run_mass_accuracy` pools
  every monoisotopic line the pre-pass claims - the reagent's ladder, the
  air's ions and the calibrant beam; their isotope lines and the analyte
  fragments claimed after the stages are left out - with the corroborated
  commits, in a run and in the ingest fold alike. The centre's trend is
  still fitted over the commits alone. `config.mass_calibration` counts
  every point fitted in `anchors` and the source's lines among them in
  `reagent_anchors`. Estimated on the testbed's stored pass-10 ledgers
  through the branch's code, which reproduces every stored fit: K3 goes
  from three of seven files with a calibration to seven, 8 or 9 of the 15
  to 27 anchors the source's lines, each file judged at a trend over its
  commits, from which the committed analytes' median sits within 0.03 ppm;
  the constant centre there sits at -0.22 to +0.65 ppm against the
  analytes' -0.35 to -0.08, 0.02 to 0.82 ppm above them, pulled up by the
  calibrant beam's lines (+0.8 to +1.0). No K3 row changes tier: the ten
  that gain a ceiling are all below assignability already. On B the centre does not move (0 to 2
  lines among 100 to 413 anchors); on C2 it moves up by 0.01 to 0.05 ppm,
  away from the analytes' median on five of six files, and the fitted
  width doubles while the width judged at stays at the search's 0.58; on
  D it moves at most 0.02, toward the analytes. Across all eleven sets
  three TOF rows would lose a tier and two rows, on K and L, could gain
  one. Pass 11 measures it on the live engine.

### 3.5b The envelope's tolerance, fitted per run

- **What.** The fit score's intensity term takes its tolerance from the
  data, in the form decided on 5.3's numbers (decision 32). Today a matched
  isotope line is scored on `exp(-0.5 * ((observed - predicted) /
  sigma)^2)` with `sigma` the larger of the line's own noise, 5% of its
  predicted share and 0.001 (`score_pattern_v2`), so for a clean peak the
  tolerance is 5% of the predicted share, and a line 15% under it is a
  three-sigma miss that alone takes the ion's fit to about a half; and an
  absent line takes the miss penalty of 0.3 wherever its expected S/N, the
  anchor's S/N times its predicted share, is at least `k_detect = 3`.
  Lines stay read by height on every instrument, as today. What is fitted
  depends on the instrument class:
  - **Orbitrap-class runs: a response curve per file.** On the file's
    confidently committed envelopes (a second channel or a list, mass
    within the gate, lines the gate calls tracking), the 13C lines'
    observed / predicted share is binned by expected S/N; per bin the
    median and the 20th percentile, made non-decreasing in S/N and capped
    at 1.0. A matched line scores as a full match anywhere between the
    curve's 20th percentile at its expected S/N and its natural share, and
    outside that band is charged from the nearer end at today's width.
  - **The curve also sets which absent lines are charged.** On a fitted
    file an absent line takes the miss penalty only above the expected S/N
    where that file's lines are actually present, in place of `k_detect`.
    The step sets that threshold and records it, for example the lowest
    bin in which at least half the predicted 13C lines are in the list.
    At 3 to 10, 60 to 94% of predicted 13C lines are absent on every
    Orbitrap set, and today's charge costs a C10 ion about 11% of its fit
    and a C20 ion about 19% for a line the instrument usually does not
    deliver.
  - **Fallback in two steps.** A file with too few committed lines for a
    curve of its own takes the curve pooled over the run's files that
    share its acquisition settings (a 100-microscan file stays apart from
    its 10-microscan siblings), and only then today's numbers. Committed
    lines are thin on several sets: 34 over 7 files on K3, 30 over 6 on
    I positive, 52 over 5 on C2.
  - **The TOF: a centre and a width per run**, until 5.5's re-detection
    gives it a usable S/N. The run fits, on the same committed envelopes,
    the relative error of a committed line against its predicted share: a
    centre, which the score subtracts, and a width, which floors the 5%.
    The SNR terms stay as they are, since they only ever widen, and
    `k_detect` stays at 3. Revisited once sets E, F and J are re-read.

  The run records what it fitted (`config.envelope_tolerance`): per file
  the form, the level it used (its own curve, the pooled curve, or today's
  numbers), the bins with their median, 20th percentile and line count,
  and the detectability threshold; per TOF run the centre and the width;
  and the minimum line count the fit asks for. The mass term's width
  already works this way (the per-sample `sigma_ppm`, and the mass gate's
  centre and width of step 2.2); this is the same fit for the intensity
  axis.
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
- **What the instrument does to a minor line (5.3, measured 2026-10-16).**
  On the pass-9 ledgers' committed rows, every 13C line was read by height
  and by area off the file-averaged peak list (the engine's own read: its
  `sample_peak_intensity` is that height on an Orbitrap and that area on a
  TOF, equal on all 60 runs) against its predicted share, binned by its
  expected S/N, the anchor's S/N times the share. The committed tracking
  lines, pooled per instrument (median / 20th percentile, lines). The
  internal Orbitrap's column pools C and C2, ten files of one reagent;
  it leaves out A's one uronium file, a single file on another reagent
  ion, whose 40 lines read 0.90 by height with half under 0.9 (the log's
  per-set table) and would put the column's share under 0.9 at 38%:

  | expected S/N | chamber Orbitrap, height | area | cylinder Orbitrap, height | area | internal Orbitrap, height | area |
  |---|---|---|---|---|---|---|
  | 3 to 10 | 0.72 / 0.56 (59) | 0.97 / 0.72 | 0.92 / 0.76 (178) | 1.24 / 1.05 | 0.77 / 0.66 (39) | 1.07 / 0.92 |
  | 10 to 20 | 0.87 / 0.76 (307) | 1.17 / 1.05 | 0.92 / 0.86 (88) | 1.12 / 1.03 | 0.92 / 0.87 (68) | 1.18 / 1.03 |
  | 20 to 50 | 0.94 / 0.90 (300) | 1.08 / 1.04 | 0.97 / 0.91 (106) | 1.08 / 1.02 | 0.95 / 0.88 (37) | 1.05 / 0.99 |
  | 50 to 100 | 0.97 / 0.93 (147) | 1.05 / 1.01 | 0.97 / 0.94 (66) | 1.05 / 1.00 | 0.97 / 0.95 (10) | 1.01 / 0.99 |
  | 100 and up | 0.97 / 0.95 (146) | 1.00 / 0.99 | 0.98 / 0.95 (111) | 1.03 / 0.98 | 0.96 / 0.94 (20) | 0.98 / 0.97 |
  | under 0.9 / under 0.5 | 33% / 1% | 5% / 0% | 25% / 0% | 3% / 0% | 36% / 0% | 7% / 0% |

  These lines are what the untargeted matcher let through, which takes a
  line only within 40% of its share by height, so they cannot show the
  deep miss. Read over every predicted 13C line of the same rows instead,
  a line with no peak at its m/z counting as zero: under an expected S/N
  of 3 the line is in the list as good as never, at 3 to 10 it is absent
  for 89% (chamber), 62% (cylinder) and 76% (internal) of the rows, from
  20 up for 0 to 12%. The list's own cut is an S/N of 3, so a line at 3 to
  10 that read its share would be in it; the dim lines that are present
  are missing from part of the file's scans where their anchors are in
  every one (at 3 to 10, 95 to 100% of them on the chamber and internal
  Orbitraps and 54% on the cylinder one; on the chamber Orbitrap from a
  median half of the scans), and at 10 to 50 the height falls with the
  scans missed: 0.94 with none missed, 0.88 with up to a fifth, 0.72 with
  a fifth to a half, 0.44 with more. That is the scan-by-scan censoring
  the reference's 0.10 line names, on Mascope's own list. Three things the
  reference's reading does not say: area does not take the low bias away
  but turns it into a high one, by 12 to 18% at an expected S/N of 10 to
  20 and 5 to 8% at 20 to 50 on all three Orbitraps (the reference saw 7
  to 13% on one), within 5% from 50; the 18O lines, thin (108 committed
  lines on the chamber Orbitrap), read lower than the 13C ones by height,
  0.69 at 10 to 20; and on the one 100-microscan file, in set L, every
  line at 10 to 20 is absent and those at 20 to 50 read 0.41 where the
  set's 10-microscan files read 0.92 to 0.97, so the onset is the file's,
  not the instrument's. Above an expected S/N of 100 the reagent ladders
  of pass 10 read 0.93 to 1.04 by height and 0.95 to 1.10 by area (57
  Orbitrap lines; one internal Orbitrap ion's 13C line at 1.16 and 1.21
  the exception), and at 10 to 20 they show the same split, 0.81 by height
  and 1.20 by area on the chamber Orbitrap. The TOF's 83 committed lines
  read 1.03 by area (its read) with a 20th percentile of 0.90, all under
  an expected S/N of 20 on the S/N column 5.5 replaces, too few and on the
  wrong scale for a curve. Not measurable here: the testbed holds no 1-
  and 100-microscan nitrate pair (its Orbitrap files are 10-microscan, but
  for that one file), so the reference's 0.3 at an S/N of 20 on
  1-microscan files is neither confirmed nor refuted.
- **The form, decided (decision 32, 2026-10-22, on 5.3's numbers).**
  The curve on the Orbitraps and a centre and a width on the TOF, read by
  height. A single centre and width would average a bin 13% low with one
  2% low, and the dim lines would set the width, loosening the bright
  lines' 5% to roughly 15 to 20%, where a miss is real evidence against a
  formula. The onset of the miss is the file's (the 100-microscan file of
  L), so the curve is fitted per file. Area does not remove the bias but
  flips it, 12 to 18% high at 10 to 20 on all three Orbitraps, where a
  band capped at the share would charge those lines, and it does nothing
  for the absent ones; the curve gives height the low under-0.9 share
  that area promised. The TOF's 83 lines all sit under an expected S/N of
  20 on the column 5.5 replaced, and a per-run centre there also absorbs
  the engine's 13C-alone prediction on a TOF peak that holds the 15N, 2H
  and 17O lines too, until that follow-up lands. It landed with MAS-371:
  the envelope match now merges the lines one peak of the file's
  resolving power holds, so a TOF centre fitted after it no longer
  carries that bias. MAS-438 reads a neighbour's envelope the same way
  in the tiering pass's envelope rule and in the claims that read a
  peak as the neighbour's line (tiering rule set 10), and the reagent
  pre-pass claims the source's own lines one per peak the file
  resolves.
- **Where.** `score_pattern_v2` in `heuristic_filter.py`: a matched
  line's band (a lower end and the natural share, with a centre and a
  floor for the TOF's form) and the absent line's detectability
  threshold, as parameters with today's values as defaults; the engine's
  scoring call and the seeded fit; the service's fit, per file of the
  curve with its pooled and default fallbacks, per run of the centre and
  width, run after Stage A and before the search is scored, or as a
  second scoring pass over the committed rows; `config.py`; `fit_score.md`
  and the how-it-works page's paragraph on the fit; tests on the score (a
  line inside the curve's band scores as a match, one under it is charged
  from the band's lower end, an absent line under the fitted threshold is
  excluded and one above it charged, the TOF's centre subtracted and a
  line 15% under at a fitted 12% width a one-sigma miss, the fallback) and
  on the fit (non-decreasing, capped at 1.0, the pooled level chosen by
  acquisition settings, the level recorded).
- **Verify.** On the uronium sample the D4 row is at assigned with its
  three lines named. On the gate's Orbitrap sets the share of committed
  rows with lines whose fit is under the assigned band falls from 27 and
  34% toward the mass term's share, and the bromide files read their
  doublets at a fit above the band. On the Orbitrap sets, absent 13C
  lines at an expected S/N of 3 to 10 take no miss penalty on files whose
  fitted threshold is above them, and the share of committed lines under
  0.9 of their share falls from 25 to 36% toward 5.3's area reading of 3
  to 7%; `config.envelope_tolerance` names each file's level, and on L
  the 100-microscan file has a curve of its own or the default, never its
  siblings'. No row gains assigned on a line the gate calls untracked,
  and G12 stays at 0. G1 and G2 read as readings (decision 27).
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
- **As built (MAS-52).** The library holds the form and the score
  (`mascope_tools.composition.envelope_tolerance`, and `score_pattern_v2`'s
  and `score_patterns_v2`'s `tolerance`); the engine holds the fit
  (`peak_assignments/envelope_fit.py`). Today's tolerance scores exactly as
  before, so the score_eval goldens, which call the score at its defaults,
  do not move.
  - **Where the fit runs: after Stage A, before the search is scored.**
    Stage A scores its frame at today's numbers, builds its rows, and the
    run fits on what it committed; Stage A's frame is then scored again at
    the fitted tolerance and its peaks arbitrated on that, and the search's
    ranking (`PatternScoring.envelope_tolerance`) and the seeded re-score
    its rows are tiered on read the same tolerance, as all three read Stage
    A's width. So the envelopes read are list readings (the library's and
    the reference lists'), the "or a list" half of the criteria; a search
    row that a second channel corroborates is not read, since that is known
    only after the judging passes, and reading it there would have meant
    deriving every committed row's fit, evidence, tier and arbitration a
    second time. On the testbed copy's latest runs the list readings carry
    72,865 of the 162,542 tracking 13C lines that the full criteria would
    read.
  - **What is read.** A committed Stage A monoisotopic row whose line sits
    within `OFF_CALIBRATION_Z` (3) of the width Stage A scored at, about
    the centre it scored at; of its lines, those within `TRACKING_SIGMAS`
    of the class's precision of the ion's own line (the gate's "tracks");
    a predicted line with no intensity after the match parameters' gating
    counts as absent, unless its expected S/N is under 3, where no bin reads
    it and nothing charges it; a line that is present and does not track is
    not read. On an Orbitrap only lines that substitute 13C and nothing else
    (a labelled reagent's atom taken off), on a TOF every line. A line is
    read as the score reads it: against the most abundant predicted line,
    at its share of that line's S/N.
  - **The curve.** Bins at expected S/N 3, 10, 20, 50 and 100 up; a curve
    needs 20 tracking lines in them, a bin 5 for its median and 20th
    percentile; each made non-decreasing by pool adjacent violators,
    weighted by line count, and capped at 1.0. A bin with too few lines
    takes the lower end of the nearest read bin above it, and above the
    last read bin the band is the share alone. A line whose anchor has no
    S/N gets no allowance.
  - **The absent line's threshold.** A bin with at least 5 predicted 13C
    lines is judged, and falls short where fewer than half of them are in
    the list. Counted from the bottom: the threshold is the lower edge of
    the lowest judged bin that holds at least half, where a judged bin
    below it falls short, and 3 where none below it does, so a short bin
    above one that holds its lines is charged as today (a few bright lines
    the ratio window turned away free nothing under them). Where every
    judged bin falls short, the upper edge of the highest (the open top bin
    gives its lower edge, 100).
  - **The pooled level.** A run is one file. A file whose own lines are too
    few pools them with the lines its batch siblings' latest completed
    in-app runs recorded (`config.envelope_tolerance.observations`) under
    the same acquisition settings: polarity, microscan count and FT
    resolution, read off the sample's stream where it points at one, else
    the file's `.props`. A sibling with no run yet adds nothing, so the
    first file of a batch to be run reads its own lines only; a file that
    records neither count pools nothing. The testbed copy has no stream
    rows; of 2,000 of its files' `.props`, the 1,263 that hold an
    Orbitrap's scan trailer carry both counts.
  - **The TOF.** The median and the scaled MAD of a tracking line's
    relative error, on at least 20 lines; the width floors at 5%, never
    tighter than today; nothing pooled, and the threshold stays at 3.
  - **Outside a run.** A formula measured on its own (a pin, the inspector,
    the batch search and its propagation) is read at the tolerance its
    sample's latest completed in-app run recorded, as it is read at that
    run's centre. A sample with no such run reads at today's numbers. That
    includes one only folded into the batch ledger (at ingest, or by a
    rebuild): the fold's Stage A fits and scores at a tolerance of its own,
    but there is no run to record it on, so its readers and its siblings'
    pools do not see it (#2989).
  - **The record** (`config.envelope_tolerance`): the form, the level, the
    file's own lines in the list and absent, the threshold; for a curve the
    bins (lines, median and 20th percentile as read and as fitted, the
    lower end scored at, predicted lines), the counts the fit asks for, the
    settings, the file's own lines for a sibling to pool, and at the pooled
    level the files and lines pooled; for a TOF the centre, the width and
    the floor. The score reads the record's own rounded numbers, so
    `EnvelopeTolerance.from_record` rebuilds exactly what the run scored at.
  - **Read on the demo dataset** (Stage A alone, read-only, on two bromide
    and two uronium samples of the public demo bundle 1.3; the gate sets are
    pass 11's). Each file fits a curve of its own on 22 to 136 tracking 13C
    lines; the 20th percentile at an expected S/N of 3 to 10 is 0.76 to
    0.87 and from 50 up 0.95 where read, and at 3 to 10 only 29 to 39% of the
    predicted 13C lines are in the list, so all four count an absent line
    from 10. D4 `[M+H]+` on the two uronium samples goes from a fit of 0.45
    (below assignability) and 0.61 (candidate) to 0.81 and 0.81 (assigned)
    at Stage A, before the judging passes. Stage A's assigned rows go from
    96, 128, 229 and 264 to 119, 172, 239 and 278. Scoring the frame again
    and arbitrating it adds about 3 s to a Stage A pass of some 65,000
    predicted lines.
  - **Not done here.** The finder's list election still counts a line as
    evidence from an expected S/N of 3 (`finder._tracking_lines`), and the
    envelope predictor's depth stays at 3: the threshold moves what an
    absent line costs, not which lines are predicted or which present ones
    count.
  - **Since 3.5d the curve is the fallback, not the model.** Where a file's
    peaks carry their scan counts, a counted line is read over the scans that
    hold it and held to its share; an absent line is still charged from the
    curve's threshold (the count's own is mascope#3123). The curve is still
    fitted and recorded on every Orbitrap
    run, on the lines as listed, and scores the lines of a file without
    counts. Its record pools the same uncorrected readings for siblings that
    lack counts.

### 3.5c The targeted matcher reads an isotope line over the scans that hold its anchor

- **What.** The targeted matcher (`mascope_match.compute.isotopes`) reads
  an Orbitrap line's intensity relative to its anchor over the scans where
  the line and the anchor are both present: the line's sum over those
  scans over the anchor's sum over the same scans, in place of the ratio
  of the two means over every scan. The count of those scans is stored on
  the match isotope row (`ratio_scan_count`) and shown in the Match tab's
  isotope table beside the measured ratio. An ion whose anchor was matched
  in an earlier run reads against that stored anchor's series, found by
  its peak. A TOF file keeps the mean over mean: it detects on its summed
  spectrum, and its series is not thresholded per scan.
- **Why.** An Orbitrap scan keeps a centroid only where it clears that
  scan's floor, and the stored intensity divides the sum by every scan, so
  a dim line is read diluted by the share of scans it missed. 5.3's table
  (1,018 committed 13C lines at an expected S/N of 10 to 50): 0.94 of the
  share missing no scan, 0.88 up to a fifth, 0.72 a fifth to a half, 0.44
  more. The two lower rows exceed the 0.2 isotope ratio tolerance, which
  zeroes the line's score. Conditioning on the anchor keeps a compound
  present for only part of the file honest.
- **Known limit.** The kept scans are the line's upward fluctuations, so
  the reading overshoots where the dilution undershoots: about a quarter at
  a per-scan S/N of 3 with half the scans keeping the line, a few percent
  at nine in ten. The censoring-aware estimate is 3.5d (the presence
  count).
- **Where.** `libraries/match/src/mascope_match/compute/isotopes.py`
  (`present_scan_ratios`); `match_isotope.ratio_scan_count`; the reference
  fetch (`fetch_existing_main_isotope_references`) carries the stored
  anchor's peak; `MatchIsotopeTable.vue`; `docs/user/how-it-works/matching.md`.
- **Verify.** The 79Br/81Br known-answer control on the demo set's bromide
  ions, old reading against new; 5.3's table re-read by the new reading;
  the demo set's match categories before and after. The reproducibility
  goldens compare peak m/z and intensity only and do not move.
- **Size.** S. Before 3.5d, whose scorer form it previews.

### 3.5d The peak store keeps each Orbitrap peak's scan presence count

- **What.** Detection counts, per peak of a raw Orbitrap file, the scans
  holding a centroid within half its width (`n_scans`) of the scans averaged
  into it (`n_scans_total`: its polarity's, or its stream's for a store
  detected per stream), and the store keeps both beside `signal_to_noise`.
  `GET /api/samples/{id}/peaks`, the batch's Parquet peak table,
  `samples.get_peaks` and `load_peaks` serve them, and the fit score reads
  them: a matched line at its mean per scan, the censored-normal estimate
  from its count and S/N, against its anchor read alike, held to its share.
  An absent line is gated as before; the threshold the anchor's count and
  per-scan S/N set waits for a soft gate (mascope#3123). 3.5b's curve is the
  fallback for a file without counts.
- **Why.** The centroid averaging knew how many scans each peak came from and
  dropped it, so the untargeted engine read each line off the file-averaged
  list and modelled the censoring (3.5b's curve, the reference's response
  curve) instead of measuring it. The count is the one number per peak the
  censoring's cause is.
- **Where.** `mascope_thermo.thermo.get_centroid_presence` (one pass over
  the averaged scans gives the side-lobe flag share and the count);
  `mascope_signal.peak` (`N_SCANS`, `N_SCANS_TOTAL`); `samples_peaks.py`,
  the peaks route, `batch_peak_table.py`; the SDK docstrings and README;
  `mascope_tools.composition.scan_presence` and `score_pattern_v2` /
  `score_patterns_v2` (`n_scans`, `n_scans_total`), the untargeted matcher
  (`score_ion_envelope(s)`, `match_isotopic_pattern`, `score_candidates`);
  the engine's peak read and search columns; the targeted matcher's frame
  (`mascope_match.compute.isotopes`) for Stage A's `ion_scores_v2`;
  `rebuild_stale_peak_stores` (`FILL_SCAN_COUNTS=1`); `tooling/score_eval`.
- **Verify.** On the gate's Orbitrap sets, 5.3's table by the corrected
  share: flat across the scans-missed bins, and the share of committed lines
  under 0.9 at an expected S/N of 10 to 20 falling toward the bright lines'.
  The 79Br/81Br control on the untargeted path, paired. The score_eval
  goldens move only where a line's presence fraction is below one; the
  reproducibility goldens do not move. A store without the column loads and
  scores as before.
- **Size.** M.
- **As built (MAS-582).**
  - **The count.** A scan counts when it holds a centroid within half the
    peak's width of its m/z, read in the pass the side-lobe flag already made
    (every scan's centroids, flagged ones included). It is reader-independent:
    the OpenTFRaw averaging's own bin count can exceed the scans (one scan's
    two labels in one bin) and the vendor library's averaging has none. On the
    published demo raws the two agree for 99.8 % of the 18,978 peaks above the
    noise. NaN where a peak's width is unknown.
  - **The store.** The detector writes both counts; a store without them
    loads, serves null for both, and scores as before. No store is stale for
    lacking them: `rebuild_stale_peak_stores` counts such stores as current
    without scan counts and rebuilds them only on `FILL_SCAN_COUNTS=1`, since
    a rebuild sets their batches to rematch. A time-ranged peak read, a
    stage's, serves neither: its S/N is the whole window's, and the estimate
    reads a range's own counts against it low (0.78 to 0.95 of the share on a
    tenth of a 100-scan file), with no lower band to catch it. It serves them
    once a stage's S/N is its own (mascope#3117).
  - **A matched line.** Read at its mean per scan: the averaged S/N `S` of a
    peak in `n` of `N` scans gives its mean per-scan S/N over the kept scans,
    `xbar = S * sqrt(N) / n` (the averaging's own law, so no per-scan noise
    need be stored), and the censored-normal (Tobit) estimate with unit noise
    gives its mean over every scan, `mu`, the root of `n (xbar - mu) = (N -
    n) phi(F - mu) / Phi(F - mu)` (Tobin 1958; Amemiya 1984): unique, the
    log-likelihood being concave, and reached by Newton's steps from `xbar`
    (at most 9 above the noise). The line reads `I * mu * sqrt(N) / S`, `I`
    where every scan holds it, and its share is that over its anchor's read
    alike, held to the predicted share at the default floor: the curve's
    lower end, which allows for the dilution, applies to an uncounted line
    only, and to one some scans missed that has no S/N or that the estimate
    puts at or below the noise. The untargeted matcher's 40% window reads the
    same share, so a diluted dim line on a counted file is kept rather than
    turned absent (MAS-370 remains for a file without counts). Round 3 of
    the review built the first form, the mean over the kept scans
    `(I_line / f_line) / (I_anchor / f_anchor)`, and held it to the share:
    the kept scans are a dim line's upward fluctuations, so under the floor
    model it read 1.40 of the share at half presence and 1.76 at 0.31, a
    bias that does not shrink with the scans. The estimate reads 0.99 to
    1.00 there from 20 scans up (median over simulated lines; 0.99 to 1.08
    on four scans). Its limits: the averaged S/N's law (1.00 to 1.04 of the
    measured kept mean times `n / sqrt(N)` with OpenTFRaw and 0.90 to 0.96
    with the vendor library on the demo raws of 4 to 176 scans, which moves
    the share at most 2%), and an analyte that varies within the averaged
    window, whose dim line's kept scans are its high-concentration scans.
  - **An absent line, built and held back.** The anchor's per-scan S/N is `S * sqrt(N) / n` (the
    averaging's own scaling); the line reads `x ~ N(p t, 1)` per scan and a
    scan keeps it from the floor `F = 2` (the demo raws' centroids reach down
    to an S/N of about 2: 5th percentile 2.04 to 2.18). Charged where what the
    kept scans add to the list at its m/z beyond the noise's own,
    `(p t Phi(p t - F) + phi(p t - F) - phi(F)) n / sqrt(N)`, reaches 3, the
    list's cut. With the anchor in every scan: 3.2 at one scan, 4.2 at four,
    9.2 at a hundred; the curve's threshold is the fallback for an uncounted
    anchor. The floor model was read on the demo raws first: the median S/N
    of a peak over its kept scans against its presence fraction follows the
    truncated normal within about 0.2 on the 20-scan file. Held back before
    merge (the owner's second design review): on the demo bundle, its stores'
    counts filled from each raw, the rule lowered score_eval's top-1, 0.8461
    with the matched-line estimate alone and 0.8447 with both at `F = 2`,
    0.8481 and 0.8443 at 2.8 (develop 0.8367). It costs more at the floor
    that matches the demo's detection rate, so the hard gate fails, not the
    threshold: it charges the full miss penalty on lines the list holds
    about half the time. The scorer gates an absent line at the curve's
    threshold, counts or not; `absent_line_detectable` stays for a soft gate,
    an absent line charged by the chance the list would have held it
    (mascope#3123).
  - **Read on the demo raws** (161 four-scan files, the bundle's matched
    ions, the list read with the counts offline; the gate sets are the
    planner's pass). 13C lines missing one or two of their anchor's four
    scans read 0.77 of their share as listed, 1.08 over their kept scans and
    0.90 by the estimate; lines in every scan 0.96. The same with the vendor
    library's averaging: 0.77, 1.07, 0.90. At an expected S/N of 10 to 20 the
    share under 0.9 is 32% every way: nearly every such line is in all four
    scans there, so the demo's shortfall at 10 to 20 is not the censoring.
    The demo's long files (20 to 176 scans) are MS2 runs whose matched ions
    give almost no counted 13C line. The one-81Br line of the bromine ions:
    85 of 2,908 read differently by the estimate, 93% of them closer to
    natural (median paired change in error -0.11), within 0.2 of natural
    96.6% as listed, 97.6% over the kept scans, 97.4% by the estimate.
    Predicted 13C lines the list holds, by expected S/N: 6%
    at 3 to 4, 42% at 4.2 to 5, 64% at 5 to 6, 88 to 89% at 6 to 8, 98% or
    more from 10;
    the count rule would charge from 4.2, so it would charge some lines a
    four-scan file holds about half the time, where 3.5b's curve, fitted at
    10 on the demo's files, charges almost none of them and today's 3 all of
    them. Of the absent predicted 13C lines from 3 up it would charge 43%,
    today 100% and the curve 7%.

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

Corroboration that only a batch can give, on the batch ledger. Pulled
ahead of the remaining stage 3 steps on 2026-10-30 (decision 33): it is
the stage the reference's advantage rests on. Its steps start now and are
judged against the pinned reference until the truth corpus has a baseline
(decision 34); none waits for the stage 3 gate except 4.5, which changes
who edits chemistry and stays last.

### 4.1 Series detection on the batch ledger

- **What.** Series units and decoy-controlled series detection as a pure
  module, `mascope_tools.composition.series`, written from the rule as
  this step and the reference's published docs state it. The boundary
  note says how a rule moves (as a specification, never as code), so the
  reference's series modules are not read or copied and the thresholds
  are re-derived on our data.
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
- **Size.** L. Depends on stage 2; no longer on the stage 3 gate
  (decision 33). Starts after the finder fix MAS-444, which edits the
  same search function.
- **As built.** `mascope_tools.composition.series` holds the units, the
  walk and the decoy evidence; `batch_series.py` the batch run, route
  `POST /api/batch-peaks/batch/{id}/detect-series`, the *Detect series*
  button beside *Search untargeted*.
  - **The walk.** From every assigned M0 batch peak, per ionization mode,
    one unit up and one down: the source's neutral counts plus or minus the
    unit's, read through the source's own mechanism at its exact m/z. The
    nearest unassigned or candidate batch peak within the search's own
    window (`mz_precision_ppm`: 3 ppm Orbitrap, 10 ppm TOF) is proposed the
    formula, and the walk steps on from the proposal's exact mass, never the
    peak's, up to three units from the assigned peak; a target is reached
    once, at its shortest distance. A step keeps a carbon and no negative
    count; a contaminant unit steps only from a formula carrying its
    element (Si, F): a silicon atom stepped onto a terpene is a mass
    coincidence, not a series. No shipped profile names contaminant units
    of its own, so every profile walks both. Below assignability, pinned,
    claimed and isotope-line batch peaks are neither sources nor targets.
    An assigned batch peak that reads the formula its series record names
    is that series' member, measured there by the detection or lifted by a
    sample's run, and stays a target rather than a source: walked from, it
    would leave its family short of the proposals it was committed on, and
    a second detection on an unchanged ledger would take every record off.
    A pinned one is a source like any assigned pin.
  - **The decoys.** Each unit is walked again at six mass shifts, with the
    same formula arithmetic, window, sources and targets; a unit's family
    is committed at 12 proposals and an enrichment (proposals over the
    decoys' mean) of 10. The run keeps every unit's counts, its decoy
    counts and the batch's FDR over the committed families (the decoys'
    summed mean over the summed proposals).
  - **What a commit writes.** A proposal on a peak already held at
    candidate on the same formula is recorded and not measured. Every other
    one is measured on each member through the untargeted search's
    propagation chain (`_propagate_to_sample`), and where a member already
    reads the peak another way it takes the proposal only at no less
    evidence. The batch peak carries the record under `provenance.series`
    (unit, step, depth, the assigned peak the chain starts at and its
    formula, `has_anchor`, the run), written before the members are
    measured and again after: a detection that stops partway leaves it on
    every member it measured, so the next one walks to them rather than
    from them, and a sample folding in mid-run, which takes it off an
    anchor not measured yet, does not keep it off. The consensus keeps it
    while it names the proposed formula, and in between, when an anchor
    still to be measured carries it beside another reading, the ledger,
    the run's snapshot and a sample's run read none there
    (`standing_series_record`). A later detection replaces every record
    but those of a mode whose chemistry it could not resolve, and the
    batch run's snapshot keeps it. A per-sample run of the batch's
    sample copies the record onto its row of the same formula and
    mechanism, the tiering pass reads it as an escape from `lone_peak` and
    `candidate_density` and names it (`series_anchor`), and nothing else:
    a radical, an absent line or an off-calibration mass stays what it
    was, and an envelope claim is not held off by it (tiering rule set 12).
    A decoy arm copies nothing.
  - **The reference's rider.** On a labelled-nitrate source a formula
    holding C8S or C2F3 is not proposed: its 15N alias makes the same ion
    within 0.3 mDa and takes the same steps (the reference's changelog).
  - **The measurement behind the thresholds**, on the testbed's 20 batch
    ledgers (16 Orbitrap, 4 TOF; 2026-11-07). Walked one step at shifts
    from -100 to +100 mDa in 0.5 mDa steps, the reached count over the 16
    Orbitrap batches is 5,377 at the unit and peaks again at +-11, +-25,
    +-36 (the CH4/O swap), +-48, +-62 and +-73 mDa, with its floor between
    them; the six decoys (-92.5, -67, -31, +30.5, +66.5, +89.5 mDa) sit on
    that floor for every unit, where two of the reference's three (+31.7
    and -47.3 mDa) sit on the shoulder of a peak. Six more floor offsets,
    each walked as if it were a unit, stand for 960 families that are not
    there: at an enrichment of 10 none reaches 10 proposals, and 12
    commits the same families as 10. An enrichment of 3 commits every
    organic unit on the 4 TOF batches, where 40 to 60% of the proposals
    landing on a candidate agree with its formula (90 to 98% on the
    Orbitrap batches); from 10 none does. At 12 and 10: 59 families on 12
    of the 20 batches, 6,123 proposals, a pooled FDR of 3.6% on the
    decoys, 90% agreement with the candidates they land on (5,545 of them;
    578 land on unassigned peaks); the first step makes 81% of the
    proposals, the second 15%, the third 4%. The contaminant units commit
    nowhere: the testbed holds almost no assigned Si or F batch peak to
    walk from.
  - **Not done here.** The Verify line's comparison with the reference's
    `residual:series` rows reads its run folders and is the planner's pass
    after the merge. The walk does not open the family's element budget
    for a targeted re-search (`chemistry_profiles.md`, 4.6), and a
    contaminant ladder no assigned peak anchors is not detected among the
    unassigned peaks alone. A candidate anchor the series confirms keeps
    its tier until its samples are assigned again, which is when the
    tiering pass reads the series.

### 4.2 Time-series coherence

- **What.** For a neutral seen in two channels, read the channels' batch
  time series (the records series endpoint): log heights, reagent-
  normalised, the batch's common mode removed, Pearson r over the spectra
  both channels hold, recorded on the row as a co-variation fact with its
  r and the spectra counted (at or above 0.6 co-varying, below not, a
  flat partner untestable), shown in the ledger and the inspector, moving
  no tier until 4.13's null model gives it a likelihood ratio (decision
  35). Anti-correlation does not demote: a correct composition can be
  anti-correlated with its own parent channel when the source state
  moves. The within-sample ratio series is 4.11's diagnostic, built after
  this step; the sidelobe reading (a constant ratio to a bright parent)
  is 5.4's, read in the parent's own line width.
  - **Persistence is admission, not evidence** (peaky #34). A peak
    present in more of the batch's spectra than the batch's own split of
    the occurrence distribution is searched however faint, and an M0
    admitted by persistence alone is capped at candidate until an
    isotopologue, a channel or a series corroborates it: persistence
    proves the ion is real, and at a few counts nothing constrains which
    formula it got.
  - **Predicted diagnostic isotopologues in the batch fold** (peaky #45). The
    15N, 18O, 34S, 29Si, 30Si, 37Cl and 81Br lines of every committed M0
    sit below the picker's edge in most files and stand only where a plume
    lifts them; the fold predicts them at the parent's trace centre and
    stamps a line only where the height ratio holds in the same sample
    and across the track, since a true isotope line passes in nearly every
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
- **Size.** M. Depends on 2.3 and 2.4. Its nine riders are steps of their
  own since decision MAS-777 (2026-11-10), 4.2b to 4.2j: persistence as
  admission, the predicted isotopologues in the fold, the consensus
  measurement, the flat series, the merged isotopologue rows, the batch
  isotope checks as facts, known species pooled, the merge window from
  the batch's scatter, the water ladder (Linear MAS-823 to MAS-831, each
  with its brief), chained where two edit one function.
- **As built (the co-variation reading; MAS-62).** The pure half is
  `mascope_tools.composition.coherence`: `batch_residuals` puts a
  partition's traces in log10, divides each spectrum by its summed reagent
  signal where the partition holds reagent ions, and removes the common
  mode, each spectrum's median of the persistent traces (present in half
  the spectra or more) taken from their own medians, read where five or
  more hold it: one sweep of Tukey's median polish. Under that median a
  per-spectrum factor every trace shares, the reagent normaliser's
  included, comes out anyway; the normalisation is kept so the common mode
  is read off normalised traces, the unit 4.11's ratio series shares.
  `read_co_variation` is Pearson's r over the spectra both residual traces
  hold, with the count, co-varying at 0.6 or more, untestable under 10
  shared spectra or a residual spread under 0.02 in log10. The batch half is
  `batch_coherence.py`: *Read co-variation*
  (`POST /api/batch-peaks/batch/{id}/read-co-variation`), a batch run
  (action `read_co_variation`), pairs every two monoisotopic, unclaimed
  batch peaks of one neutral (`cross_channel.neutral_key`) through two
  different mechanisms at any tier that commits a formula; two through one
  mechanism are not partners. The common mode is read off every
  monoisotopic, unclaimed batch peak of the partition, the normaliser off
  its reagent batch peaks, their isotope lines left out. Each batch peak of
  a pair carries `provenance.co_variation`: its formula and mechanism, the
  strongest partner's verdict, r, count, reason and spread, every partner
  strongest first, whether the traces were normalised, and the run. The
  consensus keeps it while it names that formula through that mechanism; a
  new reading replaces every record. The ledger and a run's snapshot serve
  it as `co_variation` (`batch_peak_run_anchor.co_variation`, one
  migration), and a sample's detail read as `batch_co_variation` on a
  monoisotopic row reading its peak as the batch did. The ledger shows a
  *Co-variation* column once a reading has run; the inspector a line. No
  tier, reason or member moves.
  - **The measurement behind the thresholds**, on the testbed's 20 batch
    ledgers (2026-11-08), each neutral's channel pair read against a decoy
    pair: its first trace against one of another neutral in the same
    partition, of a prevalence within a quarter of its partner's. Over 3 to
    9 shared spectra 28 % of decoy pairs read r >= 0.6 (45 % of channel
    pairs), over 10 to 19 14 % (23 %), over 20 to 49 5 % (12 %): hence 10.
    Over ten or more spectra, channel pairs whose flatter residual trace
    spreads under 0.02 read co-varying in 4 % of 185 pairs, against 19 %
    between 0.02 and 0.04 and 39 % or more above 0.04: hence 0.02. At the
    thresholds the reading finds 3,775 channel pairs: 385 co-varying, 738
    not, 2,465 on too few spectra (the 6-sample gate sets) and 187 flat;
    channel pairs read co-varying 2.6 times as often as decoy pairs, a ratio
    4.13's null model has to turn into a likelihood ratio before any tier
    reads it. Over 200 spectra or more, decoy pairs read co-varying in 28 %
    of cases on the raw log traces and in 15 % on the residuals. The largest
    batch (8,368 batch peaks, 546 spectra) reads in 0.3 s.
  - **Not done here.** The four riders of the 0.8 line and the five of the
    0.10 line: none is built here (the second, predicted isotopologues, is
    step 4.2c below). The plan owner decided on 2026-11-10
    (MAS-777) that each becomes a step of its own, measured on its own
    pass, rather than a part of this one. The
    Verify line's comparison with the reference's pass-7 skips and its
    `tables/iso_checks.csv` reads the reference's run folders and is the
    planner's pass after the merge. The electron-attachment check could not
    be read on the testbed's copy: its batch ledgers hold no radical anion
    beside another channel of the same neutral (33 radical cations do, 21
    co-varying, none anti-correlated).

- **As built (the second rider, predicted diagnostic isotopologues; step
  4.2c, MAS-824).** The pure half is
  `mascope_tools.composition.diagnostic_isotopologues`:
  `predict_diagnostic_isotopologues` predicts an ion's envelope to 1e-5 of
  its brightest line and reads off each diagnostic isotopologue (one heavy
  atom, the rest light; its ratio to the monoisotopic line is n times the
  heavy-to-light abundance ratio). A labelled reagent's atom (`^N`) is the
  label's, so it adds no 15N isotope line and the offsets count from the
  labelled line. At the anchor's resolving power (read back off its frozen
  tolerance) the lines one peak cannot separate merge as the fit score
  merges them; an isotope line under half of the peak it falls in is
  unresolved and not judged, and a resolved one is read at that peak's
  centre and share. `judge_ratio` reads one sample's ratio as
  `score_pattern_v2` reads a share: the band the run's fitted tolerance
  admits at the line's expected S/N, the distance outside it in the line's
  width (both peaks' noise, the tolerance's floor, the score's 1e-3),
  holding within two widths; a sample whose two widths at the predicted
  ratio reach past half of it is uninformative and left out. `read_track`
  stamps an isotope line where the ratio holds in 80 % of three or more
  informative samples. The batch half is `batch_isotopologues.py`: *Stamp
  isotopologues* (`POST /api/batch-peaks/batch/{id}/stamp-isotopologues`), a
  batch run (action `stamp_isotopologues`), predicts at each committed
  monoisotopic, unclaimed batch peak's trace centre (its members' median
  m/z), finds the batch peak whose own trace centre is within its frozen
  tolerance, judges every sample holding both with that sample's latest
  in-app run tolerance (`envelope_fit.recorded_envelope_tolerances`) and
  both peaks' S/N off the sample's peak list, and stamps the line in each
  sample where it held: `picker` where the sample's own run read the member
  as the parent's isotopologue, `predicted` otherwise. A line two parents
  stamp goes to the larger share holding. The parent carries
  `provenance.diagnostic_isotopologues` (each isotope line predicted, found,
  judged, holding, its verdict and whether it was stamped) and the line
  `provenance.stamped_isotopologue_of` (its parent, the track's reading, the
  per-sample stamps); the ledger and a run's snapshot serve both
  (`batch_peak_run_anchor.diagnostic_isotopologues` and
  `.stamped_isotopologue_of`, one migration), the member rows
  `stamped_isotopologue_of` and `stamp_source`. The consensus keeps a
  parent's record while it names that reading, and a parent whose reading
  moves, or that goes, takes the stamps off its lines. The ledger folds a
  stamped line under its parent with its isotope beside the offset. No tier,
  reason, member or per-sample run moves.
  - **The measurement behind the thresholds**, on the testbed's 20 batch
    ledgers (2026-11-11). Their runs predate 3.5b, so each file was read at
    a 3.5b response curve fitted on the batch's own 13C lines (2,312
    Orbitrap files at their own level, 1,879 pooled, 20 TOF files at a
    centre and width). Each predicted isotope line was read beside a decoy: the
    same parent, ratio and offset taken below the parent, where no line of
    its envelope is. The share of tracks stamped, by informative samples:

    | class | 3-4 samples | 5-9 | 10 or more |
    |---|---|---|---|
    | isotope lines the files' runs had found | 88 % of 57 | 88 % of 172 | 78 % of 64 |
    | other lines at an isotope line's mass | 3 % of 395 | 18 % of 162 | 11 % of 652 |
    | decoys | 0.9 % of 437 | 6.7 % of 210 | 0.5 % of 581 |

    The decoys of the middle band are bromine's but one: an 81Br line is as
    tall as its parent, so the mirror of a real 79Br/81Br pair looks like a
    pair. Without the informative rule, decoys over five to nine samples
    were stamped in 53 % of cases (the replicate-like gate sets hold any
    ratio constant, and a dim line's width admits any peak of its size);
    at today's default tolerance, isotope lines the runs had found were stamped
    in 39 % of tracks over ten samples or more, the Orbitrap reading a dim
    line low. Over all 20 ledgers: 36,638 isotope lines predicted, 20,625
    resolved, 7,245 at a batch peak, 366 stamped (31,426 sample stamps,
    13,552 of them predicted), 1,136 not, 5,743 untestable; 42 stamped lines
    commit a formula of their own (7 assigned, 29 candidate, 6 below
    assignability), the facts 4.2f and 4.2g read. A second null, the
    one-atom line of an element the formula lacks, is stamped in 3 to 7 %
    of tracks, and at the Cl and Br offsets in 14 to 18 % of long tracks:
    real halogen lines under halogen-free formulas, which 4.2g's halogen
    lock reads, not a false-stamp rate.
  - **Verify.** On the bromide set D (its own six-sample batch): 122
    assigned monoisotopic batch peaks predict an 81Br isotope line, 120 are
    found at a batch peak, 71 are stamped (median track share 1.0), 4 do
    not hold and 45 are untestable. On the labelled-nitrate set C, 95 of
    356 predictions are on labelled ions, and each of the 20 15N ones is
    predicted from the natural nitrogen alone. G6 and G8 are per-sample run
    metrics and do not move; no tier moves. The planner's pass reads the
    stamps on the current gate runs after the merge.

- **As built (the fifth rider, merged isotopologue rows; step 4.2f,
  MAS-827).** The batch-level form of step 2.4e's claims. The pure half is
  `mascope_tools.composition.merged_isotopologues`:
  `predict_isotopologue_lines` reads a parent ion's 13C line and its
  diagnostic lines through 4.2c's `predict_diagnostic_isotopologues` (which
  takes the isotopes to read as a new argument, the diagnostic ones by
  default), merged at the anchor's resolving power; `mass_scatter_ppm` is the
  scaled median absolute deviation of the members' m/z around their rows'
  centres, pooled over rows of five or more members, and
  `position_window_ppm` four times it, floored at 1 ppm; `read_share` reads
  the row's intensity over the parent's per sample holding both as a factor
  of the predicted share, the row the parent's isotopologue where the median
  is 0.4 to 1.4 over three or more samples with fewer than a tenth above 2.
  The batch half is `batch_merged_isotopologues.py`: *Strip isotopologue
  rows* (`POST /api/batch-peaks/batch/{id}/strip-isotopologues`), a batch
  run (action `strip_isotopologues`, one migration with two snapshot
  columns). It reads every committed monoisotopic, unclaimed batch peak (4.2c's
  `is_parent`) at the lines of every brighter one below it in its partition;
  a bromine or chlorine parent counts as brighter as far as its line's share
  times 1.4 allows. Rows are read from the lowest m/z up, a row stripped is no
  parent above it, and a row two parents' lines reach goes to the median factor
  nearest one. Held: a pinned row, a row a sample of which is a hand assignment
  (a member folded from a `manual` row, or an identity a person brought), a row
  whose 13C line the consensus already folds under it, and a row too few of
  whose samples hold the parent's reading for its members' vote to turn. In
  each sample holding both, the row's member becomes the parent's
  isotopologue (role, owner, the line's identity in its registry, the parent
  member's tier and fit), and where the sample's folded run is in-app its row
  on that peak an isotopologue child of its row on the parent's peak
  (`service.read_as_batch_isotopologue`: the owner's convention as 2.4e
  writes a line, the owner's tier and fit, the displaced reading first among
  the alternatives as `displaced_by_claim`, `provenance.batch_isotopologue`;
  the owner's `provenance.batch_isotopologues` lists the line). A sample whose
  parent member reads another reading, whose row member a pre-pass claimed or
  that carries lines of its own there is left as it is; an imported run's rows
  are never rewritten. The consensus then reads the row as the parent's line by
  its members' own vote, so a recompute and a re-fold keep it. The row records
  `provenance.merged_isotopologue_of` (its parent, the line, the share read,
  what it displaced, each member's earlier state) and the parent
  `provenance.merged_isotopologues` (the lines it took); the ledger and a
  run's snapshot serve both, the members' states left to the snapshot. The
  summary counts per partition and lists the stripped and the held pairs. The
  ledger folds a stripped row under its parent with its line outlined beside
  the offset, marks the parent, and filters to the parents that took rows
  (*Stripped rows*).
  - **Where it runs.** The brief puts the reading in the fold's consensus
    pass. That pass recomputes the anchors one fold touched, per anchor and
    in chunks, and this reading needs every anchor of a partition against
    every other and every sample's member of both; run per arrival fold it
    would read the whole partition's members for each sample folded. It is a
    batch run beside 4.2c's stamping instead, as 4.2c was built for the same
    reason, and what it writes is what the consensus pass reads: the members,
    whose vote then folds the row, and the records the consensus carries. The
    stripped pairs are listed in the run's summary, the report the batch runs
    card shows. A sample assigned again is read afresh by its own run, and a
    later strip reads its rows again.
  - **Height or area.** The member's intensity is the anchor's unit: an area on
    a TOF and a height on an Orbitrap, which is what an Orbitrap's envelope
    tolerance is read by (decision 32). Reading Orbitrap areas would open every
    sample's peak list in the pass.
  - **The mass scatter** is measured in the pass, per partition, as 4.2i will
    record it on the batch; 4.2i can replace the measurement with the recorded
    one. A partition with no row of five members (set E's three samples) reads
    at the 1 ppm floor.
  - **Verify, read on the copy's batch ledgers** (2026-11-12): the branch's
    `plan_batch` over each gate set's batch ledger as the copy holds it, read
    only, beside the reference's rows on the same peaks. G6 on the batch
    ledger counts the committed monoisotopic batch peaks the reference reads
    as an isotopologue in most of the samples it read.

    | set | merged rows | at a line | stripped | held | off share | too few | G6 | window |
    |---|---|---|---|---|---|---|---|---|
    | A | 414 | 14 | 2 | 3 | 8 | 1 | 9 -> 7 | 1 ppm |
    | B | 2,138 | 167 | 18 | 0 | 121 | 28 | 62 -> 56 | 1 ppm |
    | C | 266 | 2 | 0 | 0 | 2 | 0 | 8 -> 8 | 1 ppm |
    | C2 | 153 | 3 | 0 | 0 | 3 | 0 | 4 -> 4 | 1 ppm |
    | D | 723 | 41 | 9 | 5 | 1 | 26 | 46 -> 41 | 1 ppm |
    | E (TOF) | 906 | 6 | 0 | 0 | 1 | 5 | 6 -> 6 | 1 ppm |
    | F1 (TOF) | 2,801 | 1,101 | 19 | 84 | 473 | 525 | 23 -> 23 | 43 ppm |
    | F2 (TOF) | 2,372 | 640 | 11 | 7 | 329 | 293 | 13 -> 13 | 31 ppm |

    G6 falls on every Orbitrap set that strips a row. Held: one row on D
    carries its own 13C line, every other hold is a row too few of whose
    samples hold the parent's reading (F1's 84 are the TOF's split traces,
    4.2i's case). The Orbitrap strips by line: 13C 15, 81Br 7, 30Si 3, 29Si 2,
    15N 1, 18O 1. The reference reads 11 of the 29 Orbitrap rows as the
    parent's isotopologue, 2 as another formula's, 5 as an M0 of another
    formula (all on B; where both engines committed an M0 there, G1's
    disagreements fall by those 5 and G2's agreements do not move, no stripped
    row having agreed with it), and leaves 11 unassigned; it leaves every TOF
    strip unassigned. Read by hand, the strips are of three kinds. Sulfur
    formulas sitting on the 13C line of a bright urea adduct (B, four rows at
    a factor of 0.49 to 0.57, the Orbitrap reading a 13C line low by height)
    or on the 81Br line of a bromide adduct (D, four rows at 0.76 to 1.09):
    the 13C or 81Br line read as an analyte. Siloxane 29Si and 30Si lines read
    as analytes holding no silicon (B, four rows at 0.63 to 1.01). And rows
    some of whose samples' runs had read the peak as the parent's target-list
    isotopologue and others as an M0 of another formula, which the vote had
    left with no family link (B, D, F2: six rows); the strip links them and
    rewrites the dissenting samples. On the TOF, one assigned row read 64 ppm
    off its own formula is the 13C line of a compound 6 ppm away.
  - **Not done here.** The planner's pass reads G6 on the gate's current runs
    after the merge. A sample's later run does not read the batch's strips; it
    reads its peaks afresh, and the next strip reads them again (#3169).
    A strip is not undone by the app: the members' earlier states are on the
    row's record and in the run before it.

- **As built (the sixth rider, the batch isotope checks as facts; step 4.2g,
  MAS-828).** Written from the plan's riders and the reference's published
  description of its batch checks (its rules H, K and U), never ported. The
  pure half is `mascope_tools.composition.batch_checks`; the batch half is
  `batch_checks.py`: *Read batch checks*
  (`POST /api/batch-peaks/batch/{id}/read-batch-checks`), a batch run (action
  `read_batch_checks`, one migration with one snapshot column). It reads the
  ledger's own records - 4.2c's stamps, 4.2's co-variation - and every batch
  peak's members, per ionization mode and instrument type, and records on each
  batch peak `provenance.batch_checks`: the reading it was read for (ion,
  formula, mechanism) and each fact that applies.
  - **The halogen lock** (`halogen_lock`), on a committed monoisotopic,
    unclaimed batch peak whose ion carries chlorine or bromine and that 4.2c
    has read: its 37Cl or 81Br entry in the stamping's record. Untestable where
    the ion carries both halogens (one M+2 peak holds both lines), the line is
    unresolved or on too few samples, or no sample it was stamped in still
    holds both lines (no offset to read); not locked where no batch peak
    stands at it, its ratio does not hold, another parent took it, or its
    median offset from the parent over the samples it was stamped in sits
    outside the partition's position window (4.2f's, four times the mass
    scatter, at least 1 ppm, measured in the pass). A 34S line is 1.25 mDa under 37Cl and outside
    that window on an Orbitrap; a 30Si line is 0.21 mDa under it, inside from
    m/z 206. Where the window cannot tell the two, the silicon reading is read:
    the line as 30Si is ratio / (a30/a28) silicons, whose 29Si line is 1.515
    times the line, one unit up, merged with the ion's own +1 lines at its
    resolving power; the batch peak there is judged across the track as 4.2c
    judges a stamp, and a held track makes the lock silicon's (`silicon`), one
    on too few samples leaves it untestable. A bromine lock reads no silicon
    test: no silicon count makes a bromine's height. A lock is a fact of the
    ion, so a bromide cluster locks the reagent's bromine; the fact counts the
    element's atoms in the ion and in the neutral.
  - **The label twin** (`label_twin`), on a labelled ion: monoisotopic,
    carrying the setup's labelled atom, the reagent's own ion or a committed
    analyte. Its 14N twin is one unit down at (1 - p) / p per labelled atom
    (`custom_elements.labelled_purity` with the setup's `label_purity`). A
    purity is a certificate's minimum: on C2 the reagent's own ions carry
    their 14N lines at 0.40 and 0.43 of the 98 % share, a reagent of about
    99.2 %, and read at the setup's share their twins read absent. So the
    partition's labelled reagent ions measure the share: each one's median
    twin over ion over the setup's share across three or more samples holding
    both, the median over the ions (`core_factor`, `impurity_factor`); every
    labelled ion's twin is read at the setup's share times that factor, the
    setup's alone where no reagent ion measures it. A sample tells where the
    twin's expected S/N (the ion's, off its peak list, times the share) reaches
    the detection threshold its run fitted (3.5b's `detect_snr`); the twin is
    present there where its ratio is not under the admitted band by more than
    two widths (above is no miss: ambient 14N only adds). Present where seen
    in more than a fifth of such samples, absent where missing from 80 %,
    untestable under three. The fact names the twin's batch peak and its
    reading, and the *excess*: the median twin over ion over the read share in
    every sample holding both. No unlabelled ion is read.
  - **The neutral pair** (`neutral_pair`), on each committed monoisotopic,
    unclaimed batch peak read through `[M+H]+` or `[M+CH4N2O+H]+` (by
    `mechanism_key`) whose neutral the partition also reads through the other:
    the strongest partner through the other channel in its co-variation
    record, its r and spectra, `co_varying` read as established. The run
    counts each pair of batch peaks once, from whichever channel names it: a
    neutral split over two protonated batch peaks is two pairs. Where no
    record pairs the two the channel is counted unread and the outcome asks
    for *Read co-variation*.
  - **Records.** The consensus keeps the record while it names the reading
    (`compute_consensus`'s `batch_checks`); `standing_batch_checks_record`
    serves a lock only while the stamping record it read stands
    (`stamping_run_id`) and a pair only while its co-variation record does
    (`co_variation_run_id`), so a later stamping or reading takes them off
    until the next checks. The reading runs outside the fold lock and writes
    under it; a batch peak whose reading moved or went gets no record
    (`anchors_moved`), a fact naming a batch peak that went is left out. The
    ledger and a run's snapshot serve `batch_checks`; the ledger export and
    the members route close each row with `halogen_lock`, `halogen_element`,
    `label_twin`, `neutral_pair` and `neutral_pair_r`; a sample's detail read
    serves `batch_checks` on an M0 row of the same reading or a reagent row of
    the same ion. The ledger shows a *Facts* column of tags, the inspector a
    line. No tier, reason, member or per-sample run moves.
  - **Where it runs, and three readings of the brief.** The brief puts the
    pass after the fold. The facts read the stamping's and the co-variation's
    records, which a fold does not refresh, and a pass after every fold would
    read the whole partition per sample folded; it is a batch run beside 4.2c
    and 4.2f, as both were built for the same reason, and the fold's part is
    the consensus keeping or dropping the record. The brief's "export's
    provenance sheet" is the ledger export it names (`batch_export.py`), which
    has no sheets: the facts are columns of its rows. The "anchor detail" is
    the *Facts* column's tooltip and the inspector's line. The twin test reads
    labelled ions, whose 14N line the brief's excess clause is about: the
    fact names that line and how far it runs above the impurity.
  - **Verify, read on the copy's batch ledgers** (2026-11-12). The copy
    predates 4.2c and 4.2, so a read-only probe ran the stamping, the
    co-variation reading and the checks in memory through the branch's code,
    each file at a 3.5b curve fitted on the batch's own 13C lines (4.2c's
    probe records), each peak's S/N off its peak list; nothing was written.

    | set | halogen parents | locked | not locked | untestable | labelled ions | twin present | absent | untestable | uronium pairs | established | not | untestable |
    |---|---|---|---|---|---|---|---|---|---|---|---|---|
    | A | 0 | | | | 0 | | | | 99 | 0 | 51 | 48 |
    | B | 0 | | | | 0 | | | | 458 | 0 | 0 | 458 |
    | C | 0 | | | | 63 | 0 | 1 | 62 | 0 | | | |
    | C2 | 2 | 1 | 1 | 0 | 44 | 0 | 1 | 43 | 0 | | | |
    | D | 442 | 154 | 114 | 174 | 0 | | | | 0 | | | |
    | E (TOF) | 526 | 0 | 186 | 340 | 0 | | | | 0 | | | |
    | F1 (TOF) | 1,682 | 0 | 263 | 1,419 | 0 | | | | 0 | | | |
    | F2 (TOF) | 1 | 0 | 0 | 1 | 0 | | | | 0 | | | |

    Every lock is bromine's. On D the 154 locks are 144 `[M+Br]-` and 10
    `[M+Br2]-` clusters, 71 of them among its 121 assigned bromide clusters
    (6 not locked, 44 on too few of its six samples): the stamping's own 71.
    D's 114 not locked are 61 lines no batch peak stands at, 26 off their
    ratio, 16 off the exact spacing and 11 chlorine lines; its untestable, 147
    on too few samples and 17 ions with both halogens. No chlorine line locks
    on any set (21, 65 and 475 chlorine parents on D, E and F1), so the
    silicon test is not met on the copy; it is pinned by the tests. On the
    TOF sets the stamping holds no line at the fitted tolerance (F1's window is
    43 ppm), and nothing locks. The copy's ledgers were folded before the
    reagent pass claimed its ions, so no batch peak reads a labelled core and
    the twin test reads the labelled analytes alone: almost all untestable (a
    2 % twin needs an ion at an S/N of 147), one absent on each of C and C2,
    and their 14N lines run a median 274 (C) and 68 (C2) times the impurity's
    share, the ambient nitrate's clusters. Read as the reagent's ions, the
    batch peaks at C2's ^NO3- and H^NO3.^NO3- masses (C's scan holds neither)
    carry their twins in 88 of 88 samples at 0.40 and 0.43 of the 98 % share;
    they set C2's factor at 0.42, both read present, and the analytes' one
    absent turns untestable. No unlabelled ion is read for a twin. On A the
    co-variation reading pairs 144 channels, 65 flat, 74 not co-varying and
    none co-varying (its largest r 0.36), so no uronium pair is established;
    B's six samples are under the ten spectra a reading needs. No tier moves.
  - **Not done here.** The step that counts the facts in the tiers follows on
    its own, after the bench reads their reliability (decision 35's posture);
    PKY-30's specification is a cross-check when it arrives. The planner's pass
    reads the facts on the current gate runs after the merge, where the ledgers
    hold their reagent ions; the copy's cannot show the labelled cores as such.

- **As built (the seventh rider, known species decided once; step 4.2h,
  MAS-829).** The pure half is `mascope_tools.composition.known_species`:
  `PooledEvidence` counts, over a species' partition, the files, the files
  its peak is present in, those whose own test held, those the batch may
  claim and those a hold keeps; `decide_known_species` reads it as
  *present* (present in `MIN_PRESENT_FILES` = 2 files or more, in
  `MIN_PRESENT_SHARE` = half of them or more, and `MIN_TEST_FILES` = 1 file's
  own test held), *absent* (present in enough files, no file's test held)
  or *unpooled* (too few files: each keeps its own reading).
  `reagents.match_batch_species` claims the ions a batch decided present on a
  file's corrected mass at the claim's tolerance, with no height floor and no
  ladder, their isotope lines with them. The batch half is
  `batch_known_species.py`: *Decide known species*
  (`POST /api/batch-peaks/batch/{id}/decide-known-species`), a batch run
  (action `decide_known_species`, one migration with one snapshot column).
  - **The species.** A monoisotopic batch peak is a library species where a
    member is a pre-pass's claim (a reagent row, no formula, no owner), its
    label, family, kind, works and exact mass read off the claims' rows, or,
    where no row is left (a run-less fold), off the profiles' libraries by the
    ion. A fragment is no species: it is licensed by its parent's commit in
    the same file, which a batch cannot pool (its family on the row, or its
    ion among any profile's fragment ions). A batch peak is a listed species
    where its registry holds a formula a list tags `background`; the species
    is the formula most of its committed members name, through the ion and
    mechanism most of them read it through. A file's own test held where its
    member is the claim, or reads the listed formula at candidate or better,
    on its own: not where its row was written on the batch's word (a
    decision's rewrite, a run's claim on the decision), and, for a member no
    row speaks for (none linked, or an import's the decision left), only
    where the decision before counted it (the record's `own_test_samples`),
    so the evidence never feeds on the batch's own claims (round 1 of the
    review).
  - **The claim.** In every file of a present species but those a hold keeps,
    the member becomes the species and, where the file's folded run is
    in-app, so does its row: a library ion as the pre-pass's claim
    (`reagent_pass.batch_species_row`: role `reagent`, no formula, tier
    `unassigned`, `provenance.reagent.reason = batch_known_species` with the
    decision, the earlier reading under `read_as` and first among the
    alternatives), the row's isotope lines with it as reagent lines owned as
    before, as the fragment claim keeps a fragment's; a listed compound as
    the list's reading off a file that committed it (off the batch peak's
    registry entry, at the ion's exact mass, where no such row is left), at
    candidate with no fit, the list's identities, the decision as its tier
    reason and the earlier reading first among the alternatives; a file that
    read the compound's own formula below candidate is only lifted to
    candidate, its fit and its lines kept. Kept and recorded with their
    readings: a hand assignment, a target compound, another ion's isotope
    line, another source ion's claim, and a reading that carries isotope
    lines the species cannot have: for a listed compound any of another
    formula (its lines are that formula's), for a library ion one naming an
    element the ion does not hold, or no label at all (a 13C line under a
    carbon-free ion). A curated batch peak is held, and so is one whose vote the
    claim would not turn (the consensus over the members as the claim leaves
    them, a claimed member voting at the floor weight). Nothing but a known
    species is ever claimed.
  - **The record.** Every species read carries `provenance.known_species`:
    the species, the verdict and its evidence, the hold, what the claim
    rewrote (members, rows, lines, rows kept by why), the files kept with
    their readings (`files_by_reason`, `files_recorded`) or, where the
    verdict is not present, the files whose own claim stands and those an
    earlier decision claimed (`batch_claim`), the samples whose own test
    held, each member's earlier state, the co-variation with the core, and the reading the anchor
    holds since (`reads`). The consensus keeps it while the anchor reads
    that; a new decision replaces every record. The ledger and a run's
    snapshot serve it as `known_species`, the ledger marks the formula, and
    a sample's next run (and the run-less fold) claims a present library ion
    on the batch's word (`batch_species_for`, `claim_batch_species`), one
    entry per ion: of two present batch peaks of one ion, the one in the
    most files.
  - **Where it runs.** The brief puts the decision after the fold. As 4.2c
    and 4.2f found, a reading of one anchor against others (here the core's
    trace, and the files of the whole partition) is a batch run's, not the
    per-fold consensus pass's; the per-file claim is the brief's
    "reagent_pass takes a batch verdict", read when a sample is assigned.
  - **The co-variation with the core** is recorded and decides nothing, as
    decision 35 holds the co-variation reading display-only: a library ion is
    read against the ion its ladder grew from, else the partition's brightest
    reagent or calibrant ion, on log traces with the batch's common mode
    removed and no reagent normalisation (the ion is part of the normaliser);
    a listed compound carries its own *Read co-variation* record.
  - **Calls left with the plan owner** (raised on the PR): the thresholds
    (half the files, one file's own test) are the rider's two cases, not a
    measurement; a listed compound is claimed at candidate, never assigned;
    a library ion pooled from one file's claim needs no pooled ladder or
    ratio test beyond the file's own.
  - **Verify.** The testbed copy cannot read it: its gate-set batch ledgers
    were folded on 2026-09-18, before a claimed member carried its ion in
    the registry and before an identity carried its list's tags, and its
    in-app runs are engine 0.5.0 (set B: 18 reagent rows over six files, no
    background-tagged identity). The siloxane claims, G4 and G10 on the
    uronium sets and G1 and G2 as readings are the planner's pass on fresh
    runs after the merge, or the bench's card once milestone 6 reads it.
  - **Not done here.** A listed compound's per-file claim on a sample's next
    run (the stages read it; the next decision reads the sample again); a
    pooled ladder or ratio test over the files' traces; taking an earlier
    decision's claims back where a later one no longer reads the species
    present (they stay, recorded as `batch_claim`).

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

- **What.** Moved to milestone 6 as step 6.4, the gate card as code
  (MAS-64): the metrics, the thresholds, the baseline diff and the scrub
  in `compare_runs.py`, run by the bench on the agent box rather than by
  a scheduled run on the testbed. See
  [`assignment_bench.md`](assignment_bench.md), section 6.

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

### 4.6 A structured adduct row beside the notation string

- **What.** The ionization design's `Adduct` (`ingest_routing_and_splitting.md`,
  4.4): `ionization_mechanism` gains `operation`, `species`, `species_charge`,
  `multimer_n`, `neutral_loss` and `name`, and the notation module maps a
  string and its row onto each other (`to_row`, `from_row`). The ion's
  polarity and charge, its mass shift, the display form and the legacy
  spelling are derived. The string still decides; the row is written from it.
  The notation reads the three shapes it refused: a multimer (`[2M+H]+`), a
  charge above one (`[M+2H]2+`, a coefficient only as the ion's charge on the
  one term) and a neutral loss (`[M+H-H2O]+`, removed terms in a mechanism
  that adds). They have no legacy spelling.
- **As built.** Target ions are built for the new shapes. The composition
  finder refuses them (`parse_ionization`), and a run leaves them out of the
  sample's channels (`_readable`) with one warning, so no engine or gate
  number moves; searching them is a follow-up. The corroboration weights are
  keyed as before (pinned in `test_calibration_layer.py`).
- **Size.** M.

### 4.8 Probable blends: a batch peak whose members part along intensity or time

- **What** (`peak_assignment_batch_primary.md` section 12, open item
  "splitting blends"). The consensus marks an anchor ambiguous when its top two
  formulas tie, not how its members disagree. Two compounds sharing a peak
  part along an axis: one holds the bright samples, or the early ones. V1
  flags that; splitting the anchor into two traces follows once the flag is
  right more often than not.
- **As built** (`batch_blend.py`). After the vote, the winner (or the pin)
  and the runner-up are tested over their members at candidate or better,
  three or more a side: intensity by a two-sided Mann-Whitney test, separating
  where p <= 0.01 and the pair share (U over n1 x n2) is 0.9 or more either
  way; acquisition time by an exact one-sided Wald-Wolfowitz runs test, the
  members ordered as the continuity pass orders samples, separating where
  p <= 0.01 and the runs are at most half the interleaved expectation. Two
  formulas whose sides' most common ion is one are not tested. The record
  (`provenance.blend`: the formulas, their ions, the counts, the axis, each
  axis' reading) is computed afresh by every recompute; the recompute reads
  the batch's acquisition times once per chunk. The ledger and the series
  serve it as `blend`, the run snapshot keeps it
  (`batch_peak_run_anchor.blend`), the ledger marks it with a filter
  (*Probable blends*), the chart names the trace by both formulas. No tier,
  vote or `is_ambiguous` reads it. Every threshold is provisional.
- **Measured** (2026-11-13, the branch's test over the testbed copy's nine
  fully folded batch ledgers, read-only; the two batches the brief names are
  set A's acquisition batch and the largest analysis batch, 8,368 anchors).
  As first written (every member carrying a formula, no ion rule) the test
  flagged 287: 1 on set A's batch and 2 on the analysis batch, and 73 and
  204 on two other analysis batches. Read by hand, two patterns were not two
  compounds. One ion read through two mechanisms (a neutral at [M+H]+ in a
  batch's first ~50 samples and another at [M+NH4]+ after): 141 of the 204.
  And dim members a weak fit flipped to another formula below assignability
  (on set A, 73 of the dimmer samples of a C10 compound read as another C10
  formula at fit 0; on the analysis batch, three dim members of a phthalate's
  [M+H]+ in 545 samples). With both rules the test flags 92: 0 on set A's
  batch, 1 on the analysis batch, and 3, 1, 55 and 32 on four other
  analysis batches; 47 by time, 44 by intensity, 20 by both. Ten read by
  hand, across the batches and both axes:
  - **Two compounds on one peak (4).** The trace's level steps where the
    reading switches: one ion goes and another comes. Three partings in time
    (levels moving 1.5 to 3 times at the switch) and one in intensity and
    time (a 13C line of an ozone cluster near 270 giving way to a nitrate
    cluster near 80).
  - **Undecided (3).** An organonitrate cluster and an organosulfate's 34S
    line taking turns at a slightly lower level, a known isobaric pair; a
    parting in time at a flat level, which a slow mass drift would also
    give; and a clean two-block switch at the sample where its whole batch
    changes mechanism (the one-ion pattern above), with two ions 7 ppm
    apart that may be one.
  - **Not a blend (3).** A high-mass anchor in 45 of 546 samples carrying
    16 formulas, whose two leading ones are both implausible; and two where
    5 and 10 dim members of a compound assigned in hundreds read a 13C or
    18O line of something else - dim flips at candidate tier, which the
    tier rule does not catch.
  - **So** four of ten, not more often than not: the split is not built. A
    sharper test would hold the minority side to a share of the anchor's
    members (the three misses are minorities of 2 % or less, or scatter
    over many formulas) and read the level step at a time switch.
- **Size.** M.

### 4.11 Ratio stability, the diagnostic of 4.2's reading

- **What** (decision 35). For every pair of related batch peaks - an M0
  and one of its isotope lines, two channels of one neutral - read their
  within-sample ratio across a time-ordered batch. Every effect that
  scales a whole spectrum scales both peaks, so the ratio cancels it; its
  spread and its drift say whether the channel chemistry moved (a smooth
  drift while the two traces co-vary) or two species may share a trace (a
  drift nothing says is shared, or a ratio that jumps). It never
  identifies, and it is display-only until 4.13's null model gives it a
  likelihood ratio. Design: the continuity note
  (`peak_assignment_continuity.md`, in git history before 2026-10-22),
  sections 1, 2, 8 and 9, increment A.
- **As built (MAS-323).** `continuity.py`, its pure half and its pass.
  - *The pairs.* An isotope line pairs with the root of its family, its
    `isotopologue_of` walked by the rule the ledger draws families with
    (`rootParentId`: a link it cannot follow, or a cycle, ends in no
    parent), where both read one formula; a NULL link forms no pair. Two
    channels of one neutral are 4.2's pairs
    (`batch_coherence.partition_anchors`), one batch peak per channel (the
    brightest), neither ambiguous. Both peaks of either kind are
    `assigned` or `candidate`.
  - *The series.* `ln(I_b / I_a)` over the samples both members are in,
    in the order of `SampleFile.datetime_utc`, a file's segments by
    `t0`. Left out: a blank (labelled `BLANK`, or a file with no
    instrument function), an intensity that is zero, missing or not
    finite, and a dissenting member, the dissent filter of the note's 1.5
    re-based on the slim member: its `candidate` entry names another
    formula than its batch peak's consensus. Each is counted on the pair.
  - *The reading.* Spread 1.4826 x MAD of the log ratio; drift when
    Spearman's |rho| against the acquisition order is 0.6 or more and the
    Theil-Sen slope against time times the pair's span is at least ln 1.5
    (isotope) or ln 2 (channel), either way. Graded in order:
    `insufficient` (under 6 samples or 5 distinct times), `drift` (it
    drifts and 4.2's record on either anchor reads the pair co-varying),
    `red` (it drifts otherwise, or its spread is 0.5 or more), `stable`
    (spread at most 0.15 isotope or 0.25 channel, on 8 samples or more,
    MAD above zero), `inconclusive`. The note's `drifting` and `noisy`
    are `red`, but for the drift decision 35 reads as the source's
    chemistry; an isotope pair has no 4.2 reading, so its drift is red. An
    anchor takes its most alarming pair's grade: red, drift, stable,
    inconclusive, insufficient.
  - *The gate of the note's 1.6.* An ACQUISITION batch is time-ordered;
    another is where its largest gap between distinct acquisition times
    is at most 10 times the median gap. A batch that is not has every
    grade withdrawn, the reason on each anchor.
  - *The columns* (one migration, `c3e8a5f1d7b2`):
    `batch_peak.continuity_grade` (a CHECK on the five) and
    `batch_peak.continuity` (JSON: what was graded - formula, mechanism,
    `n_present_at_compute` - the pair count, the grades, `partial` where
    the pass left some, and up to four pairs, the alarm first and then the
    best supported, each with its ratio from the anchor's own side,
    spread, rho, drift, counts and 4.2's verdict, r and count). Both on
    `batch_peak_run_anchor` too. The ledger and a run's snapshot serve
    them with `continuity_stale`: the anchor's prevalence, formula or
    mechanism is no longer what was graded.
  - *The pass* (`run_batch_continuity`): after the folds, never in one,
    under `pg_try_advisory_xact_lock` in its own namespace (a second pass
    on the batch skips); it reads the anchors, the pairs' members and the
    samples, grades in a thread, and writes the two columns under the fold
    lock through the table, only where they moved, clearing anchors no
    longer in a pair; `batch_peak_utc_modified` stays the fold's. Not a
    batch run. It runs from `POST
    /api/batch-peaks/batch/{id}/recompute-continuity` (editor, behind the
    flag, 409 while a batch run is in flight), at the end of a rebuild
    (two pending packets on its channel), and after a fold where
    `batch_peaks.CONTINUITY_ON_FOLD` says so: off, for 4.13 to decide; a
    pass it triggers stands down where a grade was read after the fold
    committed. Above `MAX_PAIRS_PER_BATCH` (20,000, the best supported
    first) the anchors left are marked `partial`.
  - *Built under the leanings* of the deferred decisions: no wash concept
    (8.6), `MIN_DISTINCT_TIMES` doing the work; the gap heuristic (8.5);
    every constant provisional (8.2). *Not built:* `n_no_parent` and
    `mixed_parent` (8.7, still open), the note's `residual_rho` (4.2's r
    stands in for it, decision 35), the grade in an anchor verdict's
    context snapshot, the column and badge (4.12, MAS-346) and the
    trigger's default (4.13, MAS-347).
- **Measured** (2026-11-11, the testbed copy, read-only: each batch
  ledger as it stands through the pure grading, with 4.2's reading run
  in-process beside it, since the copy holds none). The testbed's batches
  of 400 to 600 samples are the acquisition batches of sets A (423
  samples), B (595) and C (423) and six analysis batches of 545 to 547.
  All nine are time-ordered under the gate.
  - B and C hold few folded members (12,000 and 1,600: their gate samples
    and little else), so their pairs hold a median of 4 to 5 samples and
    grade `insufficient` or `inconclusive` throughout, but for 5 of B's
    channel pairs, `red` on their spread. A holds 186,000 members;
    its 185 pairs hold a median of 7 to 8 samples: 102 `stable`, 30
    `inconclusive`, 53 `insufficient`, none `red`.
  - The six analysis batches, folded throughout: 1,752 pairs. Isotope
    pairs (766): 475 `stable`, 156 `inconclusive`, 94 `insufficient`, 41
    `red`. Channel pairs (986): 533 `stable`, 240 `inconclusive`, 108
    `insufficient`, 81 `red`, 24 `drift`. Anchors: 1,356 `stable`, 468
    `inconclusive`, 279 `insufficient`, 219 `red`, 44 `drift`.
  - The 24 `drift` are channel drifts 4.2 reads as co-varying; without its
    reading they are `red` (125 channel reds over the 20 ledgers, 101 with
    it). 4.2 reads 332 of the 3,015 channel pairs co-varying, 613 not and
    2,070 untestable.
  - 38 of the 41 isotope reds are drifts, and they are not two species:
    their log ratio follows the M0's own log intensity (median Spearman
    0.71, against 0.15 over the 475 stable isotope pairs) and their M0s are
    about five times dimmer (median 10^3.2 against 10^3.9). A minor line
    read near the picker's floor (step 5.3's response) drifts with its
    parent's level; where that level trends with time, the time drift
    reads it. 4.13 has to tell an intensity-dependent ratio from a time
    drift before a grade is read as evidence.
  - Cost: the largest pass (872 pairs over 546 samples, 646,000 members in
    the batch) reads its anchors in 0.13 s and its pairs' members in 1.3 s,
    and grades in 1.6 s; every other ledger takes under 1.6 s in all. The
    budget at ceiling scale is 4.13's to set.
- **Not done here.** No gate number moves: the grade is read by nothing.
  The pass on a live testbed clone (rather than the copy's ledgers through
  the pure grading) and at 10^4 and 10^5 anchors are 4.13's.

## Milestone 6 - the bench

Mascope's assignment is developed in parallel with peaky until it
reaches a stable state (agreed 2026-11-11), so the comparison that was a
gate run a few times becomes a daily instrument, and the agents that
build the steps must be able to ask for it. Milestone 6 builds that
instrument: one command on the agent box that runs a ref's in-app engine
beside peaky at a pinned release on fixed sets, computes this plan's
metrics as code, scrubs the card of anything that names a site, and
posts it on the PR that asked. Its design is
[`assignment_bench.md`](assignment_bench.md); its steps 6.0 to 6.8 are
Linear MAS-64 (the gate card as code, formerly step 4.4), MAS-835 to
MAS-841 and MAS-849. Its two decisions were taken on 2026-11-11: the
bench runs on the agent box, where a worker runs it in its own session
and no trigger is needed (MAS-835), and its sets start with the demo
dataset, more sets being the plan owner's picks (MAS-836); a set's files
live in the fleet corpus store, canonical on the agent box and mirrored
to the LAN host.

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
batches; merged 2026-10-16, see the readiness check at the end of this
review) and its experimental flags stay off.

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
| ask of Mascope | a server, library or contract gap the reference worked around | the batch import's missing verdict (5.6), the TOF signal-to-noise column (5.5), the Orbitrap axis (5.7 and the axis-fingerprint project), the censored minor lines (5.3), a per-line centre in the library's score (3.5b), the mechanism catalogue read as a configuration (5.6's docs line), a batch's peak lists in one read (5.11), the verdicts of a batch or dataset in one read (5.13) |

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
- **Size.** S per release. Next: 0.10.0 when it is cut; until a tagged
  release carries peaky's #77 (merged 2026-10-16, in no release as of
  2026-10-22), no reference run on a labelled set uses its axis correction.

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
- **Measured** 2026-10-16: the per-instrument reading and the form it
  decided (decision 32) in 3.5b, the per-set tables in the log's block "After step 5.3"
  ([`assignment_quality_plan_log.md`](assignment_quality_plan_log.md)).
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
  sets (on a labelled set only with a commit or release carrying its #77,
  and then expecting no model above m/z about 340 and no correction where
  the axis is under 1 ppm off) and read the axis it
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

### 5.9 The width a run scored at, served on the run and sample records

- **What.** The engine's scoring snapshot (`engine.py`,
  `pattern_scoring_snapshot`) records the width Stage B fitted on the
  sample's own lines (`sigma_ppm`, with `sigma_source` fitted or
  instrument class) and the mass centre (`mu_ppm`, `mu_source`); the picker
  computes a resolution per peak. Neither reaches a reader: the run record
  (`schemas.py`, `PeakAssignmentRunRecord`) and the SDK's `list_runs` frame
  carry neither, and the peaks endpoint does not return the resolution.
  First read what is stored (whether the snapshot is persisted on the run,
  whether a per-peak resolution survives detection into the peak table);
  then serve the snapshot's four numbers on the run record and in
  `list_runs` beside `engine`, `tier_bands` and `calibration`, an imported
  run's the same way through the import contract (5.6); and on the sample
  record the instrument class and a width at a reference m/z from what
  detection measured, so a sample without a run still answers.
- **Why.** The reference resolves a sample's instrument class in the order
  Mascope does and measures the file's line width from the raw profile
  itself, because nothing it can read says what width the file's lines
  have ("a batch the server lists as TOF stays one whatever resolving power
  it is given"): item C.11 of the digest, the one ask the review above did
  not bin. Every class-keyed rule this plan adds reads a width (5.4, 3.4f,
  3.5b), and an engine comparing itself to a run should read the width
  that run scored at rather than re-measure it.
- **Where.** `server/backend/src/mascope_backend/api/new/peak_assignments/schemas.py`,
  `engine.py` and `service.py` (where the snapshot is written); the samples
  API and its record; `libraries/sdk` (`peak_assignments.py`, the sample
  loaders); `sdk_peak_assignment.md`, the SDK reference, `CHANGELOG.md`.
- **Verify.** `list_runs` on a gate set returns the width and its source
  for every run; a TOF and an Orbitrap sample without a run answer
  different classes and widths; the reference's own measurement on the same
  files agrees with the served width within its stated tolerance, reported
  as a reading, not a target. No run or gate number moves.
- **Size.** S. Independent; between rounds, with 5.6 at the earliest
  (MAS-410).
- **As built.** What was stored decided the shape. An in-app run already
  kept its `pattern_scoring` snapshot in its config, so the runs listing
  lifts the four numbers from it and nothing is backfilled. No per-peak
  resolution survives detection into the peak table, but the instrument
  functions detection fitted are kept per file, so the sample record reads
  the width off them at m/z 200, by the sample's polarity as the engine
  does. An imported run discloses its width in an optional `scoring_width`
  field on the sample import, beside `calibration` (the owner's answer to
  MAS-575: not gated on 5.6), kept in a column of its own because an
  import's config is opaque. On the testbed copy every completed in-app
  run answers a width (55 `fitted`, 3,423 `instrument_class`). The imported
  reference runs answer none yet: they carry a `pattern_scoring` key in
  their opaque config, which the server does not read. Served line widths
  at m/z 200: Orbitrap files median R 118,944 (8.4 ppm FWHM), TOF files
  6,286 (159 ppm). The reference's own measurement from the raw profile,
  as a reading on twelve samples of each class: within 0.98 to 1.12 of the
  served R on the Orbitrap (median 1.04), whose served profile is drawn
  from the stored resolutions; 0.53 to 1.31 on the TOF (median 1.09).

### 5.10 The instrument's side-lobe flag: Exception-majority peaks into the satellite flag

As built (MAS-443). The step was planned as Exception centroids out of the
averaged profile and the averaged centroids; its measurement stopped that,
and the plan owner's call on MAS-577 (2026-10-29) moved it to the picker,
with the averaged profile kept as measured.

- **What.** For every peak of each spectrum it averages (a polarity, or a
  scan stream), the raw Orbitrap detector reads the share of the per-scan
  centroids within half the peak's width that the instrument flagged
  Exception (Thermo's `PeakOptions` value 8, `CENTROID_FLAGS["exception"]`,
  read on both backends since MAS-6). A peak over half is marked
  `is_satellite`, which a load already drops, unless it sits within half its
  width (never under 1 ppm) of an isotopologue line (13C, 2H, 15N, 17O,
  18O, 34S, 37Cl, 81Br) of a peak under half, at a height that line can have
  beside it (the reference's ratio band and mass cap). The averaged profile
  and the averaged centroids keep every centroid, as the vendor's
  `AverageScans` does on a file opened as the reader opens one; no profile or
  centroid moves and `AVERAGED_PROFILE_GENERATION` stays at 6. The share is
  not stored.
- **Why.** The flag is mostly the FT side lobe: on the 161 MS1 demo raws
  5.2 % of the centroids carry it (3.9 to 6.8 % per file), 99.7 % of them
  within 200 ppm of an unflagged, brighter line at under 5 % of its height
  (a median 0.6 %, 33 ppm away), and instruments B and C show the same
  shape. 11,758 of the 218,933 peaks the demo raws keep are
  Exception-majority, and the ratio test takes about half of them. But the
  instrument flags by position, and it flags real lines where it expects a
  lobe of a brighter one: an ion's 2H line lies 2.92 mDa above its 13C line,
  about 2.4 widths at m/z 150-230, and 345 Exception-majority demo peaks sit
  there against 82 and 27 at offsets moved by 0.6 mDa. Leaving every
  Exception centroid out of the averages would have lost 595 of the goldens'
  948 matched 2H lines. Against control offsets the exemption finds real
  13C, 2H and 18O lines among the flagged peaks, and real links out to about
  0.6 FWHM from the exact spacing, a line averaged with the lobe it sits on
  being centroided between the two; hence half the width. 29Si is left out:
  its spacing falls 3.8 mDa below 13C's, on the negative lobe of an ion's
  13C line. The report and the readings against the raw profile are on
  MAS-443.
- **Measured.** On the 165 demo raws and 88 Orbitrap raws of the gate's
  sets A, C, C2, D, K, K2, K3, L (the demo instrument), B and G, H, I:
  18,311 kept peaks Exception-majority, 4,032 of them spared on a line,
  14,279 marked; 5,843 of those were not satellites before (3,343 on the
  161 MS1 demo raws, 1.5 % of their kept peaks). Of the demo goldens' 42,570 matched
  peaks, 307 sit on a newly marked peak: 62 M0 lines, every one read is a
  lobe of a bright line the matcher took for an ion (a shoulder pair, a
  ringing comb whose 13C copy is flagged too); 120 13C, 37 15N, 34 18O and
  28 2H lines, nearly all more than half a width from the exact line of
  their ion, which the matcher's window took. A few are a real line averaged
  with a lobe and pulled further out than half its width.
- **Where.** `mascope_thermo.thermo.get_centroid_flag_shares`,
  `mascope_signal.compute.get_orbi_centroid_flag_shares`,
  `mascope_signal.peak.OrbiPeakDetector`,
  `mascope_tools.alignment.utils.flag_exception_peaks`;
  `reader_pipeline.md` (sections 2 and 7) and the how-it-works page on peak
  detection.
- **Verify.** No Exception-majority peak the demo raws keep is outside the
  satellite flag except on an isotopologue line; every matched compound lost
  sat at an Exception-majority peak; both backends give the same shares
  (`test_centroid_flag_shares_match_thermo`); G6 and G8 unchanged, read on
  the planner's pass. The goldens move with the satellite flag and ship with
  a new demo bundle.
- **Size.** L. Beside 5.4 (MAS-69), which reads the same lobes by their
  width-scaled signatures and can take the instrument's flag as one of them.

### 5.11 A batch's peak lists in one read, and the SDK snapshot of it

- **What.** `GET /api/batch-peaks/batch/{sample_batch_id}/peaks` answers
  every peak of every sample of a batch as one Parquet table, each sample
  read as `GET /api/samples/{sample_item_id}/peaks` reads it with no range:
  `sample_item_id`, `sample_item_name`, `datetime_utc`, `peak_id`, `mz`,
  `sparsity`, `signal_to_noise`, `segment`, `area`, `height`,
  `is_satellite`. `matches` adds the match columns (one row per match,
  off by default), `satellites` keeps the satellite peaks the per-sample
  read leaves out, flagged. The file's metadata names the samples whose
  read failed; the batch is answered without them. The SDK's `load_peaks`
  reads each batch this way, lays the table out as its per-sample reads
  were, and reads sample by sample from a server that answers the route
  404; `matches` defaults to off there and on for the older server, as it
  always was. `load_peaks(..., save_path=...)` writes the frame, `attrs`
  included, as a Parquet file.
- **Why.** The reference builds its batch time series through
  `load_peaks`, which was one JSON request per sample with the match
  expansion on, and keeps the table only when asked for a parquet. Every
  batch consumer repeats that read and caches it its own way.
- **Where.** `api/controllers/samples/batch_peak_table.py`,
  `batch_peaks_routes.py`, `samples_peaks.py` (the satellite option);
  `mascope_sdk/_loaders.py`, `resources/batch_peaks.py` (`peak_lists`),
  `libraries/sdk/README.md`.
- **Verify.** On the demo stack, `load_peaks` over either demo batch
  returns the same frame through the batch read as through the per-sample
  reads, in 1 request instead of 80 and 81; the snapshot reads back equal.
  No gate number moves.
- **Size.** M. Independent (MAS-568).

### 5.12 The per-sample scoring derivation as one library function, and a bulk candidate scorer

- **What.** The `PatternScoring` a sample is judged at is derived in
  `mascope_tools.composition.scoring.pattern_scoring_for_sample(mass_accuracy,
  instrument_type, match_params, resolution)`: the width Stage A fitted or
  the instrument class's, both widened by `PRED_SIGMA_PPM`; the offset Stage
  A fitted, else the reagent lines' where they are beyond that width, else
  none; the match window and the envelope floor of the match parameters;
  the file's resolving power. `SampleMassAccuracy` and `ReagentOffset`, with
  the stand-in offset rule, moved into `mass_accuracy`.
  `engine.pattern_scoring_for` calls it, and the run and the batch search
  hand it the sample's instrument class instead of a resolved width.
  Candidates are scored on arrays: `heuristic_filter.score_patterns_v2` is
  `score_pattern_v2` over many envelopes laid end to end;
  `score_ion_envelopes` predicts each distinct envelope once, matches every
  line with one `searchsorted` and scores them together, and the finder's
  `match_isotopic_pattern` runs it; `scoring.score_candidates` is the public
  form for any candidate list over any peak list. Stage A's
  `score_ions_by_fit`, and through it the seeded re-score, scores every ion
  of its frame with `ion_scores_v2`, the many-ion form of `ion_score_v2`.
  The one-at-a-time rule stays as `score_ion_envelope`, the statement the
  bulk path is tested against.
- **Why.** The reference re-derived the scoring object from the SDK peak
  frame in the same order, and two copies of one rule drift: the resolution
  term, the stand-in offset and the TOF handling each landed on one side
  first. And both engines scored candidates one at a time, about a
  millisecond each over some 10^5 candidates of a file's first pass.
- **As built.** No score moves beyond the rounding of a sum. On the demo
  dataset's candidate pools of `tooling/score_eval` (90,490 candidates, 161
  files) the bulk scores equal develop's finder one candidate at a time
  within 3e-16, with the same top-1 (0.7795) and AUC (0.8842), in a third of
  the time. The engine on seven demo samples, develop against this step,
  records the same scoring snapshot and the same 8,546 ledger rows, fit
  scores within 2e-16. A tie
  at an ion's top abundance, where `ion_score_v2`'s sort picks the anchor
  with an unstable quicksort, is settled by that same sort. The reference is
  told of the two functions at its next boundary review (5.1).
- **Where.** `libraries/tools/src/mascope_tools/composition/` (`scoring.py`,
  `mass_accuracy.py`, `heuristic_filter.py`); the backend's `engine.py`,
  `service.py`, `batch_untargeted.py`, `seeded_scoring.py` and
  `match_score_v2.py`.
- **Size.** M (MAS-569). Lands before or with 5.9, which serves the object
  this computes.

### 5.13 The current verdicts of a batch or dataset in one read

- **What.** `GET /api/peak-assignments/batch/{sample_batch_id}/verifications`
  and `GET /api/peak-assignments/dataset/{dataset_id}/verifications` answer
  the current verdicts over every sample of the batch or dataset, one row
  per judged peak, as each sample's assignment ledger shows them: the
  sample's own verdict, or a batch-level verdict that reaches it, marked
  `borrowed`. A batch-level verdict reaches a sample when the sample's peak
  folded into the judged batch peak and claims the formula and mechanism
  judged, and the sample has no current verdict of its own on that claim;
  that precedence is settled before the filters (`verdict`,
  `evidence_level`, `since`, `include_superseded`) apply. Each row carries
  the sample's filename and acquisition time, the peak's m/z, the evidence
  level and the score snapshot. The SDK reads them as
  `peak_assignments.verifications_for_batch` and
  `verifications_for_dataset`, and the batch export writes the same table
  as its Verifications sheet, under the API's field names.
- **Why.** The reference's refactoring plan proposes a vetted truth corpus
  to judge engines by, and Mascope's verdicts are the one truth source
  Mascope produces. They left the app one sample at a time, so the labelled
  record could not be exported into a corpus, audited across a dataset, or
  joined by `tooling/assignment_compare` against a human rather than the
  other engine.
- **Where.** `api/new/peak_assignments/service.py`
  (`get_scope_verifications`), `routes.py`, `schemas.py`;
  `api/controllers/sample/batches/export/service.py`;
  `mascope_sdk/resources/peak_assignments.py`, `libraries/sdk/README.md`.
- **Verify.** Two verdicts on two samples of a batch, one superseded, read
  as one current row each through the batch and the dataset read, the
  superseded one only on request; a batch-level verdict reaches the member
  samples with none of their own, as `borrowed`, and a per-sample verdict
  wins; the export sheet is the read. No gate number moves.
- **Size.** S to M. Independent (MAS-570).

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
- A batch's peak lists in one read rather than one request per sample, and
  a snapshot of them to re-read offline (mascope#2757): 5.11.
- The per-sample scoring derivation in the library, and candidates scored
  in bulk: 5.12.
- The verdicts of a batch or dataset in one read, for a truth corpus and
  for comparing an engine against the human record (mascope#2759): 5.13.

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

### Decisions proposed to the plan owner (30 taken 2026-10-14, 29 and 31 on 2026-10-23, 32 on 2026-10-22, 33 on 2026-10-30, 34 and 35 on 2026-11-05)

29. **The reference is pinned at a release, and each peaky minor gets a
    boundary review** (step 5.1). Taken 2026-10-23 by the plan owner
    (MAS-408), with one amendment: a pre-release is also reviewed when
    it carries a behaviour the plan is about to build on.
30. **A Schymanski-style identification level is the target, built in
    Mascope's own vocabulary; the reference's scale is not ported while it
    moves** (taken 2026-10-14 by the plan owner). The level is derived from
    the facts the engine records, after the stage-3 gate (step 5.8); an
    imported run shows its engine's tier and claim beside it (5.6); the
    derived tier stays what the fold and the UI read.
31. **The decoy arms join the gate without a target** until two gates have
    read them; then a target is set from those readings, not from the
    reference's numbers. Taken 2026-10-23 by the plan owner as proposed
    (MAS-409).
32. **The envelope's tolerance is a per-file response curve on
    Orbitrap-class runs and a centre and a width on the TOF, read by
    height** (taken 2026-10-22 by the plan owner, on 5.3's numbers). On a
    fitted file the curve also sets above which expected S/N an absent
    line is charged; a file too thin for its own curve takes the curve of
    the run's files with its acquisition settings, then today's numbers.
    The TOF is revisited once E, F and J are re-read (step 3.5b).
33. **Stage 4 is pulled ahead of the remaining stage 3 steps** (taken
    2026-10-30 by the plan owner, confirmed with peaky's owner in the
    boundary meeting; Linear MAS-609). It is the stage the reference's
    advantage rests on: the batch isotope checks, the evidence levels and
    the series its merged ledger has and Mascope's does not. Steps 3.5c
    and 3.5d stay in stage 3 because they are small and the matcher fix
    is overdue on its own; the other open stage 3 steps proceed as
    footprints allow and no longer gate stage 4's start.
34. **The plan proceeds against the pinned reference before the truth
    corpus exists** (taken 2026-11-05 by the plan owner; Linear MAS-708).
    The corpus-first sequencing of the boundary note holds for peaky's
    library moves and for the corpus gate itself (step 4.20); it does
    not gate the engine's steps. Until the baseline cards exist, a step
    is judged as every step so far: the gate sets, the reference's runs
    at a pinned release, G1 to G14 with G1 and G2 as readings, the
    intrinsic metrics, the certified cylinder, the decoy arms. Stage 4's
    components are written from this note's riders and the reference's
    published docs, never ported, and the specifications peaky's owner
    writes are read as a cross-check when they arrive. When the baseline
    exists, 4.20 adds the corpus card and reads it once over every step
    merged before, and a step whose card fails the acceptance rule is
    reopened as a finding. The chain behind the corpus (inventory,
    schema, converters, vetting, split, scorer, baseline) is weeks of
    peaky's owner's time and changes nothing the open steps build;
    waiting would have left 3.5b idle while six steps wait on its
    tolerance, and stage 4 unstarted.
35. **The batch time series is read by co-variation, display-only until
    measured; the ratio series is its diagnostic** (taken 2026-11-05 by
    the plan owner; Linear MAS-709). Steps 4.2 and 4.11 had stated
    opposite rules for one reading. The reference's current main was read
    first: every time-series test there is Pearson r of log heights on
    reagent-normalised traces (the clustering with the diel common mode
    removed), no module tests a within-sample ratio series, co-variation
    moves a level only on narrow adduct-versus-alternative questions, and
    its two-route co-variation measured a likelihood ratio near 1. Its
    electron-attachment rows, an acid's composition as a radical anion
    beside its deprotonated ion, are correct and anti-correlated with the
    parent channel at r -0.8 to -0.9 over a multi-day batch: a source
    state favouring one ionization channel while the assignment stands.
    So: co-variation of the log traces after common-mode removal is the
    primary reading (4.2's mechanism), a constant change in channel
    favourability being a constant factor in log space; it is display-
    only until 4.13's null model gives it a likelihood ratio that clears
    a bar on two reagents (4.11's posture); the ratio series, its spread
    and drift are a diagnostic of channel chemistry (a smooth drift, the
    source moved; a jump uncorrelated with either trace, two species),
    never a veto; anti-correlation does not demote; the sidelobe clause
    moves to 5.4. Order: 4.2, then 4.11 on its trace plumbing.

### Worth watching

- peaky's #77 (merged 2026-10-16, in no release as of 2026-10-22): its
  axis correction built a false -1.5 ppm step on 15N-labelled batches while
  the lock space had no label; no reference run on sets C, C2 or G uses it
  before a release carries the fix, and with the label its model reaches
  only m/z about 293 to 342 on a labelled batch (the readiness check below).
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

### Readiness check against the reference's main (2026-10-22)

A re-read of peaky's `main` from the commit the review above covers
(`bd672f2`, 2026-10-10) to `aa119ac` (2026-10-21), ten merged PRs, before
the port of this milestone begins. 0.10.0 is still not cut (the latest tag
is v0.10.0b1), no PR is open, and one issue is (its #35, the tiering of
persistence-admitted peaks, which 4.2's "persistence is admission" rider
already covers). Nothing in the ten changes a bin above.

- **Its #78 to #85 are MS2** (fragment sub-formulas, energy-resolved MS2
  read from Mascope, breakdown curves, per-scan replicates, a binding class
  per window ion): another plan's scope. One boundary for that plan: its
  #85 arbitrates rival formulas of one precursor by their MS2 products
  against decoys, a formula-assignment verdict taken on MS2 evidence.
- **Its #77** (the lock model reads a labelled batch with its label in the
  formula space) merged on 2026-10-16 and is in no release. Its known
  limits go into 5.7's reading of the labelled sets: a labelled space is
  about three times larger, so the model ends near m/z 293 to 342; the
  corrections left sit under its 1 ppm bar and are not applied; `^N3`
  families stay outside the space; fluorinated families can still alias.
- **Its #86** raises its SDK floor to mascope-sdk 2026.10.20 (Mascope
  1.13): a stitched sample reads with a `segment` column it does not yet
  read, a batch time-series load is whole or refused, the service-name
  header carries an AI Agent token, and its curation writes use the token
  where 1.13 opened them. Each of these is a Mascope ask already met. Once
  it reads the segments, its batch publish has to put a corrected m/z back
  per scan range, since Mascope calibrates each range of a stitched file on
  its own since 1.12; 5.6 watches for it.

Filed on 2026-10-22: decisions 29 and 31 as the Decision issues MAS-408
and MAS-409 (proposed above, they had none); step 5.9 above as MAS-410, the
one ask of the digest the review did not bin; a TOF rider on 5.4 (MAS-69),
so the TOF picker's satellites of the list above are measured on sets E, F
and J beside the Orbitrap lobes; and a rider on the TOF calibration
project's calibrant-choice step (MAS-177), so the reference's graded
nitrate reference-ion table and its bromide twin rule are read as an input
when the calibrant sets are chosen. That table carries no provenance, so
no ion enters a Mascope list on its word: a work that names the ion, or
test-spectra evidence stated per ion, as step 3.3 required. The issues of
5.1 and 5.7 (MAS-66, MAS-72) and the three clauses above now say "until a
release carries its #77".

Asks back to the reference, filed nowhere yet: its publish map still sends
a null mechanism for `[M]-.`, shipped here since 3.3e; its Poisson
signal-to-noise fallback can retire once a release carries 5.5's picker
fix; and the per-range publish above.

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
    isotopologues and vote rule are named in 4.2, and its two new profiles in
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
after stage 4); retention-time and MS2 evidence (their own designs; retention time's is
[assignment_confidence.md](assignment_confidence.md#p4-design-a-retention-time-term),
waiting for a chromatographic dataset with standards); the
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
- The user guide [Reference compound lists](../user/guides/reference-lists.md)
  (formerly `reference_data_authoring.md`) and the reference-database seed
  proposal (2026-09-07) - step 2.5.
- `tooling/assignment_compare/README.md` - the measurement.
- The Linear project "Assignment quality plan" (team Mascope): the steps
  of stages 3 to 5 as issues with their order, read beside this note.
