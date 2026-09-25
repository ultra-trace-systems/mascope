`ULTRA TRACE MASCOPE - DESIGN DOC - ASSIGNMENT QUALITY PLAN`

# Closing the assignment quality gap: the work, step by step

## Status

Stages 1 and 2 are on `develop`: PR #2077 merged on 2026-09-24 as a
fast-forward of 304 commits, the epic branch `epic/assignment-quality` is
gone, and the feature stays behind `peak_assignment = false` until it is
switched on. Stage 3 steps land as PRs straight into `develop`, one per
step, and are named here as they merge; their baseline is the last stage 2
round (step 2.8, engine 0.5.0, tiering rule set 6) and, for the chamber
dataset's sets G to J, the chemist's reading of 2026-09-24 (decision 21).

| step | PR | state |
|---|---|---|
| 0 - gate, epic branch, design commit | #2077 | done: testbed, comparison tool, baselines A-F, epic branch |
| 1.1 - assignment profiles (library presets, resolution, stamping) | #2078 | measured on the fixed engine: G3 met, mass-error target met on all four Orbitrap sets, C and D move furthest |
| 1.1 fix - finder: deprotonation charge and labelled reagent mass | #2079 | merged: found by the 1.1 gate run; sets C-F re-run with 1.1 on top of it |
| 1.1 fix - finder: the labelled reagent's atom in ion formulas | #2080 | merged: the label reaching the ion string is what pyteomics could not parse |
| 1.2 - opportunistic adduct channels | #2081 | measured: fingerprint gate works and refuses sodium; carbonate settled on the broad-window nitrate set |
| 1.3 - same-ion tie policy in the finder | #2082 | measured: the step's target met on A (47 -> 87% same formula); the note's mass-only policy needed a closed-shell key the gate supplied |
| 1.4 - reagent-cluster pre-pass | #2086 | measured: 60 peaks carry 81.2% of set A's signal; the claim is anchored on the sample's own base ions and the envelope reaches the floor it searches; no reference analyte taken on A/B/C/D/E/F1 |
| 1.5 - isotopologue claim and ringing artifacts | #2088 | measured: G8 met, 0 ownerless isotopologue rows on every set; B claims 969 more isotopologues and D 423, 88-97% of them confirmed by the reference, and D gains 61 analytes and 27 agreements; two review rounds fixed the envelope's anchor and then restored the requirement anchoring it took away; G6 missed, and the reference's parent ion is outside the searched grid for 78 of A's 79 (decision 11) |
| 1.6 - cap and mass window | #2090 | measured: G5 met (35,496 unsearched peaks -> 0, of which 5,304 the reference calls Assigned and 8,928 it commits any analyte on) and G2 clears its stage-1 target on A, B, C and D for the first time (B 20.7 -> 95.2%); the mass window was already instrument-class-resolved by 1.1; the grid is enumerated once per band instead of once per peak, so A and C search 5-8x more peaks and finish faster, worst sample 36s; G1 rises on the sets that gained most and G6 with it, and the review found G6's rise has an envelope part beside the grid gap, now decision 11's rider with homes in 2.1 and 2.4 |
| 1.7 - stage 1 gate, engine 0.4.0 | #2091 | measured: the 0.4.0 build reproduces the 1.6 ledger field for field, so stage 1's numbers are final; G1 met on A, B, C and D (73 -> 41.5, 57 -> 24.3, 99 -> 41.5, 68 -> 37.3%) and missed on C2 (55.2%); G2's same-formula bound met on the same four (39 -> 95.6, 12 -> 95.2, 18 -> 87.3, 17 -> 80.1%) and its same-ion bound on A and B only; G3 met but for B's 2.7% N >= 5, with no carbon-free formula from the untargeted stage on any of the 43 samples; G5 and G8 met everywhere; the mass-error target met on all five Orbitrap sets; G4a below 90% and G6 read, not gated; about half of what stage 1 does not recover carries an element the searched grid cannot build, which is step 2.5b's |
| 2.1 - v2 fit for Stage B | #2092 | measured: the finder ranks with the v2 fit at the sample's own mass width and on the file's own per-peak signal-to-noise, and every committed reading is measured again as an ion, so the engine computes one fit (decision 12); G1 falls on every Orbitrap set (A 41.5 -> 35.4, B 24.3 -> 18.9, C 41.5 -> 34.7, C2 55.2 -> 40.1, D 37.3 -> 21.0) with G2 unchanged on A and B and up on C, C2 and D, G5 and G8 still zero and the mass error flat or better on the Orbitrap sets; the fit distributions of confirmed and contradicted rows separate for the first time (B -0.001 -> 0.129, D 0.020 -> 0.249) and the odd-electron share of assigned rows falls on seven of the eight sets; the TOF sets gain 293-675 committed analytes at their own width; the sibling task, a TOF-capable reference run from peaky, is not in this PR because publishing one re-bases every set |
| 2.1b - TOF-capable reference: peaky's scorer through `score_pattern_v2` with the sample's fitted sigma | #2093 + peaky `epic/v2-fit-reference` | measured: all 43 reference runs re-published at the sample's own width, with the engine's runs untouched (same run id on 43 of 43 after the publish); the reference commits less on the Orbitrap sets and more on the TOF ones (B 7,614 -> 4,680 M0, F1 698 -> 1,034), its committed mass error improves on every Orbitrap set (B 0.290 -> 0.174 ppm) and its own fit finally separates its confirmed rows from its contradicted ones (A 0.089 -> 0.174, C 0.066 -> 0.181, C2 -0.051 -> +0.052, on hundreds of rows a side); the share of this engine's assigned rows the reference CONTRADICTS falls on every Orbitrap set (C 2.4 -> 0.3%, C2 4.3 -> 1.3%) while G1 rises because a conservative reference is silent more (A 35.4 -> 40.2, B 18.9 -> 44.2, D 21.0 -> 21.6); G2 formula 93.8 A, 92.8 B, 90.6 C, 77.7 C2, 80.4 D; G5 and G8 still zero; the peaks endpoint did not carry signal-to-noise and now does; two rounds of the gate caught the same defect in the shared fit, that it returns the offset and the width together and reports neither below its anchor minimum |
| 2.2 - self-calibrated mass gate | #2094 | measured: every committed row records `mass_z` and every run records the calibration it was measured in, fitted over its own corroborated commits - 212 to 3,970 anchors a set where Stage A had 10 to 127. The gate itself is a guard, not a lever: it caps 49 rows over the 43 samples - 3 analyte commits, all on one TOF set and none of them a row the reference confirms, and 46 isotopologue rows the tracking rule reads as coincidences - and moves G1 on no set, because the failure it was written for was closed by 2.1 - the widest committed top-tier error on a straight Orbitrap axis is 1.02 ppm, and on A the rows the reference contradicts sit CLOSER to the calibration (0.088 ppm median) than the ones it confirms (0.149). No row the reference confirms was demoted on any set (G2 and its n identical everywhere) and no election changed (0 verdict shifts of 48,894 peaks), so G7 is 0 where the gate applied, which was all 43. The offset half of the 2.1b defect was tried and withdrawn on the measurement: fitting an offset from the five to seven anchors a width is refused for moved the TOF bromide set 4 ppm the wrong way (MAD 2.03 -> 2.33), so an anchor set too small to say how wide it is cannot say where its centre is. What the runs now report instead is a per-sample calibration reading, and it says the TOF sets carry 2.8-6.9 ppm widths and C2 a flat -1.1 ppm bias on files whose stored calibration is marked verified. The TOF sets were then recalibrated through the node and E re-baselined on both engines: its axis was out by 8-10 ppm and is now inside a ppm, which lifts the reference's G2 on E from 15.4/26.9 to 33.3/42.9% and the signal it explains from 0.5 to 26.2%, while the engine's top tier is the same SIZE on both axes (424 rows) and not the same rows - 147 of them sit on a peak that held it before, 7 keep their formula, tiers moved on 765 of the 1,343 M0 peaks both commit, and 70% of those peaks change formula - so mass is not what constrains a TOF commit, which is 2.4's. F1 and F2 did not move at all (their axes were already right, merely never fitted) and their runs stand; C2 cannot be fitted until its mode has more than two disagreeing calibrants |
| 2.3 - cross-channel corroboration and the reagent-N rule | #2096 | measured: a run groups its committed monoisotopic winners by neutral across the channels it searched and records what that corroborates, reaching 5.4% to 59.4% of a set's commits where Stage A's curated-compound version reached 25 of A's 2,062 rows and none at all on six sets; the ledger's corroboration marker now renders it, and the row carries the best tier any partner channel holds. It is the strongest separator the engine has: on the reference's own Assigned rows the engine agrees on the formula 98.7% of the time when the neutral has a second channel against 54.7% when it does not (A), 98.0 against 71.2 (B), 97.6 against 83.2 (D), 100 against 5.1 (F1), and it survives an intensity-decile control on both conditionings. Nothing is promoted on it; the flag is what 2.4 weighs. The reagent-N rule is what its absence makes necessary: `+NH4+` on M and `+H+` on M+NH3 are the SAME ion formula, so no mass, envelope or fit separates them and the finder does not try - since 1.3 `elect_same_ion_families` collapses them before ranking and elects a reading by a stated prior (closed-shell neutral first, then the mechanism carrying the most mass), keeping the displaced reading on the row as a `same_ion` alternative. That prior is not an observation, so a winner through a nitrogen-donating channel whose own family holds a reading through a channel donating none is capped at `candidate` with the reason `ambiguous_nitrogen` unless a nitrogen-free channel or a second, different nitrogen-donating reagent saw the same neutral: 359 analytes and 51 isotopologues on A, 921 and 55 on B, 1,225 and 9 on F2, none on the five other sets - C and C2 because their reagent is 15N-labelled, which both stops it donating nitrogen and stops the finder proposing the alternative at all, D, E and F1 because they have no nitrogen-donating channel. Whether such a prior deserves trusting is a question about the reagent and the gate answers it: the same arithmetic holds for bromide, and 246 of D's 277 lone bromide readings are confirmed (89%) against 79 of A's 409 lone nitrogen ones (19%), which is what scopes the rule to nitrogen. G1 falls 40.2 -> 25.4 on A, 44.2 -> 40.2 on B and 99.2 -> 98.8 on F2 and is flat elsewhere; G2 and its denominator are identical on all eight sets and there are 0 verdict shifts and 0 formula shifts of 48,894 peaks, because the rule moves tiers and nothing else. Two costs are recorded rather than claimed as wins: on F2 it takes 1,225 of 3,008 assigned rows on a set where the reference is silent, so nothing judges the exchange and 2.4 must weigh it against the mode's own prior; and it demotes 405 rows the reference confirms, though on every one of them the reference reached that formula through the same nitrogen-donating channel and itself calls 57 of A's 67 and 313 of B's 336 `candidate`, leaving the engine more conservative than the reference on 33 of the 2,505 rows it capped |
| 2.4 - mechanical tiers with reasons | #2099 (measurements), #2100 (tiering), #2101 (inspector), #2105 (curated rows) | **2.4a measured** on build `step-2.4-mechanical-tiers-2026.09.10-70438cb`: every committed row records `provenance.candidate_density`, the number of formulas the peak's evidence cannot separate from the one the row commits - anchored on the committed formula and not on the arbitration's top, which the finder need not have committed - counted one-sided, so a rival the evidence ranks ABOVE the commit is never reported as nothing tying it; the two anchorings differ on 444 of the 32,449 committed rows, every one of them upward, and on 23 rows at assigned tier - 22 on F1, 1 on F2 - of which one crosses from 1 to 2. Recording it changes nothing: 0 of 48,894 ledger rows differ from the 2.3 build on any other column and every gate number is identical. Measured, it is a sharp guard rather than a lever - 11 of A's 1,002 assigned rows sit at density >= 2 and 0 of those 11 are confirmed, against 75.4% at density 1 - because a continuous fit separates almost everything; the number of formulas the MASS admits is a different and wider quantity, and reaches 68 on the same set. **The plan's carbon-cluster demote is withdrawn.** `DBE/C >= 1` with the effective DBE reduces to `H <= 2 + N`, which names hydrogen-poor molecules rather than large skeletons: on the gate it took formic, oxalic and glyoxylic acid and a confirmed nitrogen heterocycle - 46 rows, 8 of them right - and no carbon cluster. What keeps a cluster out of a run is the chemistry context's DBE/C and H/C windows (ambient-air 0.75 and 0.7, uronium 1.1 and 0.4) and not the heuristic filter, which grades a formula and rejects none on H/C - C60 passes at plausibility 1.0 - and those windows apply only from three carbons up, which is where the signature misjudged; a run under context `none` has no such window and this would not have closed that gap either. The oxygen-lattice floor of five is measured on what it SPARES (at O >= 4 it reaches 260 rows and the reference confirms 35, at O >= 5 it reaches 212 and 3, flat to O >= 9); what it takes is 3 confirmed against 17 contradicted and 189 the reference does not judge, 174 of them on the TOF sets, so on the Orbitrap sets it is 3 right and 3 wrong. The carbon-free signature reaches nothing, because every resolved grid floors carbon at one and every carbon-free committed row on the gate is curated - and a curated row is not asked (#2105; as 2.4b first merged the pass did ask, and took 7 curated rows, none confirmed; re-measured on `step-2.4d-curated-implausibility-2026.09.11-2012665` the signature takes 0). The cross-family degeneracy measurement is not per-run: about 27 s of band grids a spectrum plus 6 ms a peak, against ten seconds for a whole sample's assignment **2.4b measured** on build `step-2.4b-tiering-2026.09.11-639e1df`: every committed monoisotopic row records `provenance.tier_reasons`, and none of the 32,449 carries zero - the 31 curated isotopologue rows with no owner that carried none on this build now say `not_measured`, which moves no tier; the rules only demote and move nothing but tiers - 0 formula, role or verdict shifts of 48,894 peaks, G2 and its denominator identical on all eight sets. The pass takes 3,778 assigned rows and the reference confirms 38 of them (1.0%): an odd-electron neutral, a rule the plan did not have and the gate found, 1,782 with none confirmed; candidate density 2,636 with 32; the oxygen lattice 212 with 3; the envelope neighbour 166 with 4; carbon-free none, because no curated row is asked the formula-shape questions (#2105, rule set version 2, measured on `step-2.4d-curated-implausibility-2026.09.11-2012665`: as first merged the pass took 7 curated carbon-free rows - trisulfur on C2, the dibromide ion's bromine on F1 - with none confirmed, and exempting them moves those rows and their 8 isotopologues back to assigned and nothing else). Read on the rows the reference commits an M0 on (decision 14), G1 meets the 20% bound on all five Orbitrap sets - A 3.1, B 12.3 (1.1 with the same-ion splits set aside), C 1.3, C2 0.4, D 1.4 - against 23.0, 36.9, 21.0, 24.6 and 8.4 unconditioned, a remainder that is 73-91% reference silence; the TOF sets are carried to 2.5 and 2.7. G6 at assigned tier falls 159 -> 71, of which 47 are the grid gap and 24 the envelope part, every one of them spared by the rule's height test or by the run holding no neighbour to predict it, so that Verify line as first written is missed; re-worded in 2.4c to the height-qualified form, which is the rule as measured, it is met. An isotopologue row follows an owner any pass capped; the earlier passes already cap their own, so that count is 0 on all 43 runs. **2.4c** puts the reasons in the peak inspector under *Why this tier* - the rule by name over the run's own sentence, a mark on each reason that holds the tier down, and on an isotopologue its M0's reasons beneath its own; a row no rule judged shows none. Display and documentation only: no run or gate number moves |
| 2.5a - reference seed: list format, the lifted lists, `reference seed` (seed proposal phases 0-1) | #2111 (seed), #2112 (Stage A fix), #2122 (the search pane's copy of the M0 rule), #2126 (the isotopologue tables' labels) | measured: schema 2 list files with radical status read from the formula, the `peaklist` adapter, `mascope reference seed`, the demo hook and an integrity test over every list; the lift is Kang 2021 split into 573 closed-shell and 257 RO2 formulas (opt-in), Keller 2008 less its ions and salts (50 of 59), peaky's pass-0 families as seven cited lists (45 of their 65 formulas, plus the D3-D6 siloxanes) and the example list, 1,008 formulas in all. The default seed (10 lists, 751 compounds) raises G2 same-formula on every set - A 93.8 -> 95.4, B 92.8 -> 93.5, C 91.7 -> 93.9, C2 77.7 -> 83.3 (now inside its bound), D 80.4 -> 84.0, E 33.3 -> 40.5, F1 13.0 -> 22.8, F2 15.9 -> 17.4 - and moves 211 peaks towards the reference's formula against 53 away, 23 of those with a seed row on the peak and 9 of the 23 one ion read two ways on B. The first seed round had lowered C and C2 (to 73.9 and 59.8) through two Stage A defects older than the seed, a labelled ion's M0 and isotopologues with no owner; #2112 fixes both and was measured alone (40 of 48,894 peaks moved, none away from the reference), and the insert overflow on merged TOF isotopologue names is fixed in #2111. On the TOF sets the seed's matches become most of Stage A's anchors and widen the fitted mass width 2-3x (F2 2.6 -> 6.5 ppm), which moves thousands of untargeted elections; that is the next row's, and D4/D5, triethyl phosphate and G6 wait for 2.5b's per-source window |
| 2.5a fix - the reference seed out of the mass widths | #2113 | measured: Stage A's width is fitted over the target library's lines alone, only the target library's rows count as curated for the mass gate (decision 3's second addendum), the ingest fold runs the gate, and Stage A falls back to the instrument class's width as the untargeted stage does. With the seed loaded, every set's search width and Stage A anchor count equal the pre-seed round's (F2 6.54 -> 2.62 ppm, gate 6.75 -> 2.62). G2 same formula rises on E, F1 and F2 (40.5 -> 45.2, 22.8 -> 23.6, 17.4 -> 18.8) and holds on the Orbitrap sets (C2 83.3 -> 82.6, D 84.0 -> 83.9). All 3,883 peaks that change owner had been moved by the seed, and 3,866 go back to their pre-seed owner. Of the 30 mirror rows the estimate named, the gate caps 4 and the narrower width holds the other 26 below its cap |
| 2.5b - Stage A window per source, polarity, radical filter, deactivate, the isoprene lift (seed proposal phase 3) | #2116 (schema and CLI), #2117 (engine, lists) | measured: #2116 records each source's window, radical allowance and polarity on its row (a database mirror at today's window, a list unbounded, existing rows backfilled at today's window), lets `reference sync` set them (`--polarity` included), has `reference seed` write and refresh them, and adds `reference deactivate`. #2117 makes Stage A read all three under the context's ceiling (every shipped context opens Si, P, F, Cl, Br and I at C40/700 Da; `none` sets none) and lifts the isoprene list, D7/D8 (Fromme et al. 2019) and L3-L5; the monoterpene HOM list matches both polarities (decision 17's addendum). With the seed refreshed, G2 same formula rises on every set (A 95.4 -> 97.1, B 93.5 -> 95.1, C2 82.6 -> 87.1, D 83.9 -> 85.5, E 45.2 -> 54.8, F1 23.6 -> 27.6, F2 18.8 -> 26.1); D3-D8, the phosphates and TFA resolve on every sample the reference commits them on, the siloxanes mostly at candidate tier by the evidence's own reading (decision 18); G6 on A falls 87 -> 50 with its grid part 69 -> 5. 987 owners change, 125 towards the reference and 9 away (5 new siloxane isotopologue claims on B, 4 isoprene nitrates on F2). IBr2- commits as an analyte on 8 reagent peaks, read at the gate |
| 2.5c - the same-ion ambiguity on a Stage A mirror row | #2118 | measured: a reference mirror's M0 row carries the readings of its ion the untargeted search would have held, as `same_ion` alternatives (2,725 rows over the 43 samples), and the reagent-N rule asks it from both sides, since a list can put the nitrogen on the analyte; a second channel fixes the count either way and a target library row stays exempt. It moves tiers and nothing else: no formula, role or owner change on 48,894 peaks, G2 identical on every set, no untargeted or target library row touched. The rule reaches 746 mirror rows and caps 167 from assigned (A 24, B 67, F2 76) with 25 isotopologues. All nine same-ion steals on B carry their ammonium reading: two are capped and seven fixed by the same neutral's urea adduct. G1 A 24.3 -> 23.2, B 36.8 -> 37.0 (conditioned 11.6 -> 11.7), F2 97.5 -> 97.3; on B the cap takes 50 rows the reference confirms, 18 of them Keller's amines through `+H+` |
| 2.5d - review of the curated lists | #2136 (a library entry's line whatever its spelling), #2137 (the cap on library rows, the plan) | measured: the 46 entries the gate's modes attach were audited against the step's four flags, and the plan owner's verdicts taken on each. The four odd-electron workaround entries left the lists (HCO3 and CHO3 for CO3-, HS3 for S3-, Br for Br2-); the entries that never commit a row stay, with their reasons - reagent lines the pre-pass claims first, or compounds absent or out of range; no entry is left whose ion another channel of the same sample reads as a different neutral. Removing the workaround entries cost C2 its Stage A offset: four to five of its eleven or twelve matched lines were theirs, so it scores at the class width with no offset while its own runs sit at -1.1 ppm, and 146 rows leave assigned, 106 of them rows the reference commits the same formula on (the section after this step has the two ways back). The audit found an engine defect, fixed in #2136: a reference list's copy of a library reading took the entry's line, on the spelling or on a shade better fit - 63 rows on four sets, 12 density caps lifted, no tier moved. Decision 3's exemption is lifted from the cap in #2137 (its fourth addendum): a library line off calibration is capped unless an isotopologue tracks it, which moves the three isotopologues that exemption protected from assigned to candidate and nothing else |
| 2.5e - an offset from the reagent lines where the library is thin | #2140 | measured: where Stage A matches fewer than eight library lines, both stages score at the median error of every line the reagent pre-pass claimed, isotopologues included, where it claimed three or more and the median is beyond the width the sample is scored at; the width stays the class's. Only C2 reaches it, at -1.25 to -1.26 ppm over seven lines where its commits sit at -1.10 to -1.13: 114 of the 146 monoisotopic rows 2.5d took from assigned come back, 100 of them on the reference's formula, its assigned rows go from 181 to 321, G1 30.9 -> 20.6 (conditioned 0.0 -> 0.8 over 257 rows) and G2 86.4 -> 87.5. The other seven sets are identical row for row. Every run keeps its calibration's offset and width and the gate caps the same rows; on C2 the re-elections move the calibration's line through m/z, which three samples now refuse and two fit shallower |
| 2.5f - a list hit meets the untargeted grid | #2148 (the measurement), #2149 (the election) | measured: a run that searches puts every monoisotopic list hit to the search, its reading one candidate beside the grid's. A closed-shell rival takes the peak only where its evidence is past twice the reading's by more than a tie and it explains the reading's own tracking isotope lines; a target library compound keeps its peak (decision 19). A hit that keeps its peak counts the grid's closed-shell rivals into its density, and the density rule holds a lone one with a rival at candidate (decision 3's sixth addendum). On the gate, rivals take 1,093 list-hit peaks, none of them held at assigned on the measurement's round; the reference commits the rival's formula on 19 and the list's on none. The lines keep 47 hits, 27 of them B's siloxanes, and the library 1. 31 list hits at assigned lose the second channel a taken reading had given them. G2 is identical on every set but F2 (26.1 -> 29.0); G1 conditioned moves on B, D, F1 and F2 (F2 78.6 -> 76.0) |
| 2.4e - isotopologue claims under interference | #2143 | measured: a monoisotopic row the envelope-neighbour rule flags under a neighbour held at assigned is read as that neighbour's isotopologue at candidate, the reading it displaced first among its alternatives, unless it is a target library compound, another channel committed its neutral, the neighbour already holds a line there, or its error does not follow the neighbour's within what the line can deliver; the ledger's passes then run again over the claims. An isotopologue's tracking allows for its line's noise below a signal-to-noise of 15 and for a peak within two widths of it: a line only that far off is in doubt and held at candidate, never lower, and one further off is held at candidate at least. Of the 861 flagged rows, 252 are claimed (184 tracking, 68 in doubt) and 578 stay under a neighbour below assigned; the reference had read 67 of the claims as the same formula's isotopologues and 6, all on B and five of them 2H lines, as the M0 the claim displaced. G6 falls from 681 to 596, and no monoisotopic row changes formula or tier, so G1, G1 conditioned and G2 are identical on every set. Set C's 18O, 2H and 13C2 lines of its strongest ion are all its candidate isotopologues. 69 isotopologues go from assigned to candidate, 25 of them lines the reference confirms, and the calibration refits on the three samples where a claimed row had been an anchor. Taken ahead of 2.5f's first PR |
| 2.4f - an oxygen-free neutral in a nitrate cluster | #2147 | measured: a row read as nitrate clustered with a neutral that has no oxygen - through the plain or labelled ion, or their nitric acid clusters - is held at candidate with the reason `oxygen_free_cluster`, its isotopologues with it; a target library compound is exempt, a reference list's row is not, and no second channel lifts it. Measured two ways with carbonate beside it, the plan owner took the cap and left carbonate out (decision 18's third addendum). The rule names 134 rows on the three nitrate sets and takes 10 from assigned, all on F2, where the reference commits the same reading on one; nothing else moves on 48,894 peaks, G2 is identical on every set, and F2's G1 conditioned goes from 77.6 to 78.6. Restricting the search instead read 75 of F2's 115 rows as the same ion without its proton, 18 of them at assigned; carbonate would have taken 27 rows from assigned, 20 of them on the reference's own formula |
| 2.6 - frontend: profile, reasons, roles | #2150 | built: the launchers offer the chemistry profile and context, on *Auto*, and name what *Auto* resolves to for the sample or per group of a batch's samples, from two read routes that resolve a run config's names without starting a run; a named profile of the other polarity is warned about. A run's chip names the profile it recorded. The inspector shows `mass_z` beside the ppm error and the same ion's other readings under the tier reasons, and marks the same-ion and displaced alternatives. Reagent and artifact peaks show their role in place of the tier, and the ledger counts, filters and sorts them apart from the tiers. No engine change: no run or gate number moves |
| 2.7 - stage 2 gate, engine 0.5.0 | #2152 | measured: the 0.5.0 build re-assigned all 43 gate samples, read against the frozen and the refreshed reference. Against the refreshed one G1 meets 20% on A, C, C2 and D (6.1, 10.9, 19.8, 7.8) and misses on B (32.7, of it 23.7 points the reference's silence); G1 conditioned meets it on all eight sets, the TOF sets included; G2's formula bound holds on all five Orbitrap sets and its ion bound on A; G5, G7 and G8 are 0; every committed row carries its reasons, and the top-24 view holds no reagent peak fitted as an analyte. Before the round C2 was recalibrated on weak lines, its two calibrants being its two brightest (the plan owner's rule, now step 3.6): its commits move from -1.14 to -0.08 ppm and it has a usable reference again. The plan owner's IBr2- decision is the polyhalide rule, which takes the top tier from six rows no reference confirms |
| 2.2b - mass-dependent centre for the mass gate | #2131 | measured: a run's mass gate judges a row at its own m/z where the run's commits demand a centre that follows `ppm = a + b * 1000 / mz`, accepted on peaky's rules; the line is fitted over every committed monoisotopic row (the plan owner's answer, recorded in the step), and the constant centre and the width stay the anchors'. A takes a line on all six samples (-0.113 to -0.137 mDa over m/z 57 to about 500) and C2 on one (-0.077); every other run keeps the constant centre and records the rule that refused the line. It moves no tier, formula, role, owner or cap on 48,894 peaks, and G1, G1 conditioned and G2 are identical on every set: every row that crosses three widths was already below assignability. What moves is `mass_z` - 2,316 rows on A, 130 on C2 - and A's seven itemised curated rows come inside three widths (-3.49..-4.38 to -0.80..-1.62). The plan owner kept the target library's exemption from the cap for now, to be revisited after 2.5d's review (decision 3's third addendum) |
| 2.7a - reference refresh: peaky's branch rebased on main 0.8.0, re-pinned, the 43 runs re-published | #2151, peaky `epic/v2-fit-reference` at `26e0ff3` | measured: the branch is rebased on peaky's main, pinned to this epic's head and green in CI for the first time since 2.1b, and all 43 runs are re-published at `26e0ff3`, the batch sets pinned to the gate's samples because main's `batch` now picks its own. Against the refreshed reference G1 is 6.1 on A, 10.9 on C, 7.9 on D and 32.7 on B, where the reference is silent on 23.7 points, and G1 conditioned meets 20% on the TOF sets for the first time (E 13.0, F1 7.8, F2 17.1). C2's refreshed reference scores at no offset: step 2.5d's list edits left it four anchors, and peaky skips the labelled reagent's own lines, so C2 is read against the frozen reference until it is recalibrated. `compare_runs.py --engine-b-before` reads the frozen reference from the store |
| 2.8 - the band first, one reading per ion, the inspector's ionization and list names | #2154 | measured: every row under the top band names it first (14,855 of the round's monoisotopic rows); a row whose ion also reads as a closed-shell molecule through another channel is held at candidate unless a second channel committed its neutral (decision 20), which takes 75 rows from assigned - 70 urea adducts against an ammonium reading the nitrogen rule did not ask and 5 bromide clusters - and returns 12 nitrate clusters on F2 whose only other reading is a carbonate radical; a channel the mode and its profile both name is searched once, so no row lists its own reading as another one (265 on C before), and stays secondary: C's declared carbonate channel keeps the minor-channel cap, on the plan owner's call (decision 20); no owner changes; G2 identical, G7 0; the inspector names the ionization and the reference lists' compounds, looked up on the detail read as Stage A matches |
| 3.1 - a profile for the charge-transfer source | #2197 | built: `EASYIC_POS` and `EASYIC_NEG` resolve from the bare sign on an Orbitrap after every reagent (a reagent mode that also declares electron transfer keeps its reagent; a mode with only protonation or deprotonation stays ESI; a bare-sign mode on a TOF, an ambient-ion stream, keeps the ESI path it had; a declared proton transfer or deprotonation beside the bare sign is the mode's own channel, not an opportunistic one), both under the ambient context; the fluoranthene beam is the reagent ladder (the air-plasma cations wait for 3.3); hydride abstraction `-H-` and proton transfer (positive) and deprotonation (negative) are secondary channels switched on by the beam, hydronium, and the source's own deprotonated acids, and left on where a narrow window cannot show them; their reading of an ion the bare sign also reads stands only where the sample commits the neutral through a mode channel (the partner gate, `engine.apply_partner_gates`, read over both stages' rows; carbonate is not gated until re-measured), so tropylium is toluene less a hydride rather than protonated C7H6; `parse_ionization` now reads `-H-` as the grammar and the validator do (a hydride removed, a cation) instead of as deprotonation - a breaking change for library callers: peaky rewrites `-H+` to `-H-` on the way in (a workaround from before `-H+` parsed as an anion, itself unreleased until the same library release), so peaky drops both rewrites and requires that release. The `-H-` mechanism row is an operator step, since seeding never creates mechanisms. Measured on the testbed (six representatives per set, `-H-` and `+HCOO-` rows added): set I- assigned rows per sample 66 -> 3 and assigned intensity 22.9 -> 1.7%, the 16 survivors all C2 nitrogen-rich formulas through the bare sign below the ratio windows' carbon floor (3.6's prior), so G9 reads 100% of a set 23 times smaller; set I+ assigned per sample 42 -> 43 with the bare sign 243 -> 111 rows and proton transfer 83 and hydride abstraction 37 opened (65 of the proton-transfer commits corroborated by the same neutral on the bare sign), tropylium read as toluene less a hydride at assigned in 5 of 6 samples, G9 51 -> 31% (71 of 231, all C2 or smaller); G10 stays 0 on both, the beam sits above both windows and the air-plasma cations are 3.3's. Sets C and C2 unchanged in tiers |
| 3.1c - the stronger partner decides | - | planned (decision 24), step section written 2026-09-25; the need measured on set K: the benzyl cation as protonated C7H6 at assigned in 6 of 6 files with toluene less a hydride on the row |
| 3.2 - formate as an opportunistic channel | #2198, stacked on #2197 | built: `+HCOO-` is a secondary channel of the nitrate, 15N-nitrate, bromide and iodide profiles, probed on formate, its dimer with formic acid and (nitrate) its cluster with the reagent's acid, built from the reagent so the labelled profile probes `[HCOO+H^NO3]-`; the nitrate profiles keep it on where the window cannot show a probe, as they do carbonate, because the batch carrying the C11 pseudo-acids is acquired from m/z 130 and the source makes no formate carrier above 127 (the two-acid cluster absent, the two-formic-acid cluster at 0.01-0.05% of base on the wide-window sister batch); the halide profiles claim only what they show. The election alone is not enough: measured with the channel open, it read every deprotonated acid as the molecule 46 Da lighter with formate (1,239 acid rows of six no-reagent-ion samples moved to formate and were capped; set C's same-formula agreement with the reference fell from 89.6% to 62.6%), so formate takes the partner gate of step 3.1 (`engine.apply_partner_gates`): the formate reading is the row's only where the lighter neutral is committed through a mode channel, otherwise the acid stands and the formate reading is set aside. Measured on the testbed with the partner gate judging every row against the ledger as it stands: set G without reagent ion G11 24.4 -> 5.6% of assigned-plus-candidate intensity (assigned-only 32.9 -> 7.1%), the deprotonated C11 rows 195 -> 79 (51 assigned), 104 of them now their C10 formate reading at assigned, each corroborated by the C10 neutral through a mode channel, 51 standing because no reading of the C10 neutral exists through a mode channel, 28 held at candidate; assigned rows per sample 486 -> 440, assigned intensity 58.4 -> 56.5%, of which 272 list-matched acids drop to candidate because their formate rival's lighter neutral is itself committed (O4 to O8 products; a real ambiguity for the small acids, and for a list's C11 the C10 reading is right). Set G with reagent ion G11 1.2 -> 0.5%. Set C tiers within two rows of before (assigned 615 -> 613), same-formula agreement with the reference 89.6 -> 88.0% where a partnered formate reading replaces an acid the reference reads as an acid; set C2 unchanged (probe absent, channel off). The target of under 1% is met with reagent ion and not without: the remaining 5.6% is C11 acids whose C10 partner is seen through no mode channel, which the rule leaves standing |
| 3.3 - name the source ions | - | planned |
| 3.3b - the standard adduct notation | - | planned (decision 23); before 2.0, as its own change |
| 3.4 - an opportunistic channel needs a second channel | - | planned |
| 3.5 - calibrants below the brightest lines, an offset term, and the low-mass bend (calibration node) | - | planned |
| 3.6 - priors and the dataset's context | - | planned |
| 3.7 - stage 3 gate, engine 0.6.0 | - | planned |
| 4.1 - series detection on the batch ledger | - | planned |
| 4.2 - time-series coherence | - | planned |
| 4.3 - calibration from verdicts, per profile | - | planned |
| 4.4 - gate automation | - | planned |
| 4.5 - profiles as versioned rows, routing by detection | - | planned |

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
  written as `role = reagent`, with their isotopologues claimed as
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

### 1.5 Isotopologue claim and ringing artifacts

- **What.** Stage B receives the whole peak list as pattern context and the
  searched set as `targets` (the finder already supports this split, and the
  batch search already uses it), so an M0's isotopologues are claimed wherever
  they sit. That is the substance of the step: the searched set is capped at
  the 300 most intense unexplained peaks, so an envelope was previously scored
  against at most 300 peaks - 2.5% of a dense spectrum - and an isotopologue
  outside that set was not found, leaving its peak to be searched on its own
  account. An isotopologue row is written by the M0 that claims it and names that
  M0 as its owner from the start; it is never linked to a parent after the
  fact, and an isotopologue whose ion commits no M0 is not written at all - that
  second pass is the fix for the ownerless rows. The matcher anchors a
  predicted envelope on the ion's own monoisotopic line rather than on the
  predictor's most abundant one, which is the same line by accident for an
  ordinary ion and two mass units away for a dibromide. The finder's duplicate
  resolution ranks a row that IS somebody's monoisotopic line ahead of another
  candidate's isotopologue for the same peak; that one is a guard rather than a
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

### 2.2b Mass-dependent centre for the mass gate

- **What.** `fit_run_mass_accuracy` fits the run's anchors to
  `ppm = a + b * 1000 / mz` beside the constant model, and `MassCalibration
  .z_of` reads a row's distance from the centre at the row's own m/z. The
  trend is accepted only on the rules peaky's `masscal.fit_mass_trend`
  settled on (peaky #30): the slope beyond three standard errors, the
  trimmed residual RMS at most 0.8 of the constant model's, `|b|` at most
  0.5 mDa (a larger constant term is a broken calibration, not a residual
  to model), at least five anchors in each half of the fitted range so a
  single far anchor cannot lever the line, and the centre clamped to the
  anchors' m/z coverage so a backbone at 150-480 never extrapolates onto an
  m/z 61 it never saw. A flat run keeps the constant model exactly, and the
  run records which model it judged at. The width stays one number,
  floored below about m/z 120 by an absolute 0.03 mDa.
- **Why.** Step 2.2 measured the low-mass residual as calibration shape on
  every instrument (issue #2095), and decision 3's addendum kept curated
  rows exempt from the cap because ten of the fourteen off-calibration
  monoisotopic rows were one ion at the low-mass edge, off the same way in
  every sample. Step 4.3 defers the mass-dependent term to verdict anchors
  below m/z 100. peaky measured the same shape on a labelled-ammonium file
  - the backbone at -0.76 ppm for m/z 80-120, -0.29 at 120-160, -0.18
  above, and every bright ion below m/z 80 at -2 ppm, which is -0.12 mDa,
  rejected at z = 6 by the constant centre - and built the centre from the
  run's own anchors, which the gate has in hundreds. The verdicts are not
  needed for it. With the centre fair at low mass, the curation exemption
  is revisited on evidence: decision 3's addenda were taken on a gate whose
  centre punished the calibration's shape.
- **Verify.** On the 43 samples: which runs accept a trend and its `b` in
  mDa; the `mass_z` of the 26 curated rows decision 3's addendum itemised,
  and of the low-mass untargeted rows, before and after; the gate caps
  nothing below m/z 200 that it did not cap before unless the row is off
  the trend too. G1 conditioned stays inside its bound on the Orbitrap
  sets.
- **Size.** S-M. After the TOF width fix, which changes the anchors the
  gate fits over, and after 2.5c; before 2.6 and 2.7. Independent of 2.5b
  in what it changes.
- *Addendum (2026-09-15, the plan owner's answer on the implementing agent's
  estimate): the line is fitted over every committed monoisotopic row, and
  the constant centre and the width stay on the anchors.* Estimated on 2.5c's
  last round, the anchors as written accept a line on four of B's six runs
  and no other. A's low-mass shape is refused on all six by the coverage
  rule: 3 or 4 of its 39 to 45 anchors sit below the fitted range's midpoint
  near m/z 104, where the rule asks for five, because a small ion's
  isotopologue is too weak to track. B's accepted line describes a dip to
  -0.9 ppm at m/z 300-450 that about 140 commits above m/z 450, near 0 ppm,
  contradict. Over every commit, A accepts a line on all six at -0.11 to
  -0.14 mDa, B on none and C2 on one. Rows banded by their evidence were
  tried as the pool too, and refuse A as well: of A's 27 to 29 commits below
  m/z 104, 19 to 22 are below assignability, at a median evidence of 0.18 to
  0.28 against 0.88 to 0.92 above m/z 160.

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
  and plausibility demotes (**the carbon-cluster demote was written and
  withdrawn on the measurement - see the status row**; oxygen lattices at
  O/C > 1.3 with at least five oxygens; carbon-free formulas off the
  allowlist), and one more from decision 11's rider: an M0 committed
  on a peak that a committed neighbour's envelope predicts a line for, within
  the matcher's tolerance, carries the neighbour and the line as its reason
  and cannot sit at assigned tier - after step 1.6, 332 of B's 460 G6 rows
  and 275 of D's 328 do. Rules only demote; every row carries
  `provenance.tier_reasons`, and the run records the rule version.
- **Why.** Cause 2: the tier must degrade with evidence, and it must be able
  to say why.
- **Verify.** Gate metric G1 to 20% or below on the Orbitrap sets, read over
  the assigned rows the reference commits an M0 on, with the same-ion splits
  shown beside it and the unconditioned G1 reported with it (decision 14);
  the TOF sets are carried to 2.5 and the 2.7 gate. Every committed row has
  at least one reason; the decoy harness (`tooling/score_eval`) confirms the
  demotes do not lower contested top-1; no G6 row of the envelope part
  (decision 11's rider) at assigned tier on a line that a neighbour the run
  commits at candidate or above predicts within the matcher's window, with
  the peak no more than twice that line's predicted height, and the G6 rows
  the height test spares reported beside it. Re-worded in 2.4c: the line was
  written before the height test existed, and read without one the rider
  flags 342 of B's rows on the 2.3 ledgers, 203 of which the reference
  confirms - more rows it confirms than rows it does not.
- **Size.** L (three PRs: the pure measurements in `mascope_tools`, the
  backend tiering, the reasons in the inspector). Depends on 2.1-2.3.

### 2.4e Isotopologue claims under interference

- **What.** Two changes, measured together because they meet on the same
  lines:
  - **The claim.** An M0 the envelope-neighbour rule flags today - a line a
    committed formula's envelope predicts on the peak, tall enough to account
    for it - is claimed as that formula's isotopologue at candidate tier when
    the formula is assigned, instead of standing as an M0 capped below
    assigned. Where the formula is itself at candidate the M0 stays as it is
    and is reported beside. This is decision 18's addendum made mechanical:
    under doubt, a candidate isotopologue of an assigned formula rather than a
    new M0.
  - **The tracking test.** Whether an isotopologue's mass error tracks its
    parent's is judged with what the line can deliver: the bar widens for a
    line near the noise floor, and for a line inside the resolving power of
    another line, which pushes the two centroids apart. A doublet partner -
    13C2 beside 18O, 2H beside 13C - is then neither read as a coincidence and
    capped, nor taken as the corroboration a tracking line gives.
- **Why.** On step 2.2b's round, with set C read on the round after the
  monitor fix here and in 2.4f, 862 M0 rows sit on a line a committed
  formula's envelope predicts (A 70, B 305, C 20, C2 1, D 101, E 38, F1 187,
  F2 140), all at candidate or below by the rule. On 356 of them the formula
  is assigned, and on the Orbitrap sets the reference reads 183 of the 497 as
  isotopologues. Set C's strongest line shows both halves:
  - its 18O M+2 line is committed as C9H13N2 through the labelled nitrate on
    four of five samples, and its 2H M+1 line as C8H13N3 through carbonate on
    two;
  - its 13C2 and 18O M+2 lines, 11.6 ppm apart in theory, measure 14.3 to
    15.2 ppm apart, each pushed 1.2 to 2.1 ppm from its place.

  Isotopologues that do not track their parent with another line within 20
  ppm: A 13, B 68, C 11, C2 9 and D 38, of them at assigned A 5, B 21, C 7,
  C2 4 and D 3.
- **Verify.** The 862 rows by what they become; gate metric G6 falls; G1
  conditioned and G2 inside their bounds on the Orbitrap sets; every row the
  reference confirms as an M0 that the claim takes as an isotopologue,
  reported; set C's doublet lines read as its strongest line's isotopologues
  at the tier the rule gives; the gate's isotopologue caps re-counted by
  overlap and intensity.
- **Size.** M. After 2.5d, whose curated formulas predict many of the
  envelopes, and after 2.5f's measurement, which settles which list rows
  stand at assigned; on the reference frozen at `cc07ce1` (decision 16).
- **As built (2026-09-16).** Taken ahead of 2.5f's first PR on the plan
  owner's word, and measured on step 2.5e's round. 2.5f's measurement reads
  the claims this step makes; a list hit it caps below assigned stops being a
  neighbour a line can be claimed for.
  - **What a line can deliver.** A line's own noise widens the class's bar by
    the square root of how much fainter it is than a line at a signal-to-noise
    of 15, the two lines' variances taken together. The tallest other peak
    within two of the line's widths (the file's resolving power) adds up to a
    quarter width, in proportion to its height up to the line's own. The 15 is
    measured: on the Orbitrap sets, the child-minus-parent error of the
    isotopologues the reference confirms narrows as about 1.15 / sqrt(SNR)
    ppm. The two widths and the quarter width are set C's doublet, which
    measures 1.6 to 1.7 widths apart where theory puts 1.3, each line pushed
    1.2 to 2.1 ppm by a partner as tall.
  - **What each verdict does.** Inside the class's bar a line tracks and
    corroborates, as before. Inside the widened bar only, it is in doubt: held
    at candidate, never lower for its distance from the calibration, and it
    corroborates nothing. Beyond both it is capped like any other row, and held
    at candidate at least. That last part reaches lines that miss their parent
    while sitting within three widths of the centre, which stayed assigned
    before. The two verdicts compare two of the run's own lines, so they hold
    whether or not the run measured a calibration.
  - **What a claim takes.** The envelope-neighbour flag and a neighbour at
    assigned after every rule, and a line whose error follows the neighbour's
    at least as far as the widened bar allows: a line that misses by more is
    the tracking test's coincidence, and the row keeps its reading. Also
    held back: a compound of the target library, a neutral another channel
    committed (evidence from outside the peak), and a line the neighbour
    already holds; of two rows on one line, the nearer is claimed. A claimed
    line corroborates nothing, even where it tracks. The row's own
    isotopologues go with it where the neighbour's envelope predicts their
    lines too, and leave the ledger otherwise. The claimed row is written in
    its owner's convention (a mass-offset label and the isotopologue formula
    beside the ion for a list's row, the substitution for the search's), with
    the reading it displaced as its first alternative. A row on two assigned
    neighbours' lines is claimed by the one its envelope reason names, the
    first found in m/z order.
  - **What the record says.** The claim block's `displaced.tier` is the tier
    the displaced reading's evidence gave it: a claim is applied to the rows as
    the stages built them, before any rule held them lower. On 27 of the gate's
    252 claims it reads assigned where the run had shown the row at candidate,
    under the envelope rule itself and on some under density, the nitrogen
    ambiguity or the radical rule as well. Step 2.7a's re-read should take it
    so, or the block can carry the tier the run held.
  - **The ledger is judged again.** A claim takes a monoisotopic row off the
    ledger, so the mass gate, the cross-channel pass and the tiering pass run
    again over what the claims leave, until a round finds nothing new (at most
    four). The calibration is then fitted without the claimed rows, no claimed
    row counts as a channel, and a row flagged only by a row a claim took is
    no longer flagged. The run-less ingest fold runs no tiering, so it claims
    nothing; it reads its lines for the gate as a run does.

### 2.4f An oxygen-free neutral in a nitrate cluster

- **What.** A nitrate cluster (`+NO3-`, its labelled form, `+(HNO3)NO3-`)
  holds on to the neutral's oxygen-bearing functional groups, so a reading
  through one whose neutral carries no oxygen is not held at assigned. It is
  measured two ways and chosen on the numbers: as a cap with its own tier
  reason, and as a restriction on the search, where the channel does not
  propose such a neutral and the peak goes to its next reading. Carbonate
  (`+CO3-`) is measured beside it and decided by the plan owner.
- **Why.** The plan owner's reading of set C's strongest line: its 18O line
  committed as C9H13N2 through the labelled nitrate names a neutral with
  nothing for nitrate to attach to. On step 2.2b's round, the nitrate-cluster
  M0 rows whose neutral has no oxygen are C 12 of 282, C2 27 of 222 and F2 115
  of 3,607, 11 of them at assigned, and the reference reads 11 of C's as
  isotopologue lines of other ions. Carbonate is less clear-cut: C 93 of 280,
  C2 30 of 96, E 16, F1 84 and F2 89, and on C and C2 the reference commits
  the same oxygen-free formula on 19 of their 22 assigned rows.
- **Verify.** The rows each form takes, by set, tier and what the reference
  commits on the peak; what the restricted search elects instead; G1
  conditioned and G2; the carbonate answer recorded.
- **Size.** S. After 2.5d, and independent of 2.4e, though read after it the
  rows 2.4e claims are no longer M0s.
- **As built (2026-09-16).** The cap, on the plan owner's answer to the
  measurement (*After step 2.4f*), and measured on step 2.4e's round.
  - **Which channels.** Read off the mechanism: nitrate, with a label counted
    as its element and any number of nitric acid molecules around it. A
    subtraction, a cation, nitrite and a hydrate are not such a cluster, and
    neither is a mechanism that cannot be parsed.
  - **Which rows.** A monoisotopic row whose neutral has no oxygen at all, and
    its isotopologues with it, as with every rule.
    - A compound of the target library is exempt, for the reagent-N rule's
      reason: the workspace named it for the channels of the modes its
      collection is attached to. C2's hydrogen bromide through the labelled
      nitrate is on the nitrate monitor list.
    - A reference list's row is asked, since a list names a compound and not
      the channel it is seen through.
    - A second channel does not lift the cap: the doubt is about the ion, as
      the radical rule's is.
  - **Carbonate is not asked**, on the plan owner's answer. The reference
    commits the same oxygen-free neutral on 20 of the 27 carbonate clusters
    the cap would take.
  - **The search restriction was not taken.** It hands the peak to its next
    reading. On the unlabelled nitrate set that is mostly the same ion read
    without its proton, an organic nitrate the reagent-N rule does not ask,
    and the restriction put more such readings at assigned than the cap takes.
  - **The record.** The tier reason `oxygen_free_cluster` ("no oxygen to
    cluster on" in the inspector) and rule set 4. The tiering pass reads the
    run's mechanisms.

### 2.5 The reference seed and the Stage A window

This step is the reference-database seed proposal, adopted as a track that
runs beside the engine work: its phases 0, 1 and 3 sit on the assignment
path, its literature sweep (phase 2) continues through stages 2 and 3 with
its own status.

- **2.5a What.** Reference lists as self-describing JSON files, in the format
  peaky's lists introduced, versioned as schema 2. That is the format of the
  lists Mascope ships; the `custom` CSV adapter stays the path for a user's
  own list (decision 15).
  - **The header** carries what belongs to the whole list: its id, which
    names the source, a version, a licence, references with a DOI or ISBN
    each, the detection ion and context tags. Those stay in the file. Stage A
    copies every identity it matches, `xrefs` included, into the provenance
    of the row and its alternatives, so a list's constants there would be
    written onto every matched peak and read by nothing. Context tags wait for
    `reference_source.tags`.
  - **Radical status is read from the formula** - a half-integer DBE, the
    nitrogen rule - never from a flag. A list may hold radicals only when it
    says `allow_radicals`. peaky's flag was set from odd hydrogen and is wrong
    on every nitrogen-bearing row of its HOM list.
  - **The tooling:** a `peaklist` adapter in `mascope_reference`,
    `mascope reference seed` with its deployment counterpart inside the
    backend container, the demo hook, and an integrity test over every list
    file. peaky's copy of the lists carries the test too, until step 2.7
    settles what peaky reads.
  - **The lift:**
    - Kang 2021's 830 HOM formulas, as a closed-shell list (573) and an RO2
      list (257) that no seed loads unasked.
    - Keller 2008's contaminants, less the ions and salts it lists as
      neutrals (50 of 59).
    - The seven families hard-coded in peaky's pass-0 directors, each species
      with a literature citation. That keeps 45 of their 65 formulas: 16 have
      no paper reporting them observed, and four are unnamed. None of the
      provenance the directors' code comments carry comes with them.
    - The cyclic siloxanes D3 to D6, with a citation. No day-one list held
      them, although the Verify below names D4 and D5.
    - The example atmospheric list.
  - **No engine code changes**, so the step's store round measures the seed
    alone. There is one exception and one precursor. The seed's first round
    overflowed `peak_assignment.isotope_formula` on merged low-resolution
    names, so that insert is fixed here. The same round found two Stage A
    defects older than the seed: a labelled ion's M0, and isotopologues with
    no owner. Those were fixed in #2112 and measured without the seed before
    this step's round was re-run on top (*After step 2.5a*).
- **2.5b What.** The Stage A known window (`iter_known_compositions`:
  C/H/N/O/S, C <= 40, 700 Da) becomes per source, bounded by the context's
  `known_window`, so a curated list brings I, F, Si, P, Cl and Br into Stage
  A while a PubChem mirror stays bounded. Scoped 2026-09-14 on the
  implementing agent's brief; the questions it asked are decision 17.
  - **The source row carries its window, its radical allowance and its
    polarity.** A migration adds `known_window` (JSON: elements, maximum
    carbon, maximum mass; a null field is unbounded), `allow_radicals`
    (false) and `polarity` (`positive`, `negative`, or null for both) to
    `reference_source`, mirrored in the library's schema. The migration
    backfills today's window on every existing row, so a mirror loaded
    before this step stays bounded until it is re-synced. `reference seed`
    writes a shipped list's row unbounded - a cited list is its own bound -
    with the allowance and the polarity from its header, and refreshes
    those three fields on a version that is already active instead of
    skipping it, so a deployment that upgrades runs the seed once.
    `reference sync` writes a list file or a `custom` CSV unbounded as
    well, and a database mirror at today's window; `--elements`,
    `--max-carbon`, `--max-mass` and `--allow-radicals` override either,
    and `reference sources` prints each source's window.
  - **Stage A applies, per source, the row's window intersected with the
    context's ceiling, the row's allowance, and the row's polarity against
    the sample's.** A formula keeps the identities of the sources that
    admit it. The known-state fingerprint gains the three fields, the cache
    key gains the context's ceiling and the sample's polarity, and the
    resolved profile's snapshot records the window a run matched against.
  - **The context's `known_window` is a ceiling.** Every shipped context
    opens C, H, N, O, S, Si, P, F, Cl, Br and I at 40 carbons and 700 Da
    (D6 is 444 Da, the longest PFCA 614). `none` sets no ceiling, as the
    identity it is for the ratio windows: the two ESI profiles and the
    identity profile default to it, and the siloxane list's second citation
    is nanoESI background. A narrowing per context waits until a chemistry
    gives a reason; polarity already keeps the negative-mode lists off the
    uronium sets.
  - **The radical filter.** Stage A drops an odd-electron formula unless
    its source row allows radicals: decision 15's rule carried to every
    source, and what guards a `custom` list or a public mirror, since only
    the list adapter holds radicals back at ingest. The format's rule
    stays. On the testbed nothing changes once the seed has refreshed the
    rows; before it has, HO2 and OIO would be filtered, which is why the
    seed runs before the round.
  - **`reference deactivate <source>`** takes the source's active load out,
    with a confirmation and `--yes`, and `activate` brings a version back;
    it gets a counterpart inside the backend container beside the seed's.
  - **Ion generation for Si and P** is verified - D4 [M+H]+ at 297.0824
    with its 29Si and 30Si lines, triethyl phosphate [M+H]+ at 183.0781,
    chlorpyrifos' 37Cl lines, the iodine ions - and gets its test.
  - **The isoprene lift**: `isoprene-oxidation-wennberg2018`, the one list
    peaky added after 2.5a's lift, 27 closed-shell products of isoprene's
    OH, NO3 and HO2 chemistry (Wennberg et al., Chemical Reviews 118 (2018)
    3337-3390, DOI 10.1021/acs.chemrev.7b00439; peaky #30): 8 already in
    the default seed, 19 new, 9 of them with nitrogen, all inside today's
    window; negative polarity, closed-shell, loaded by default. Mascope's
    reader parses peaky's schema-1 file as it is, and the format check
    names the six header gaps the lift closes: the schema, the hyphenated
    id, the licence, the reference's citation and key names, and the
    `n_species` and `system` keys the format does not carry. Its
    provenance is written generically, as decision 15 requires; peaky's
    names the campaign the list was curated for.
  - **Siloxane content for G6.** A literature check adds only what a
    verified citation supports: the linear L3 to L5 are covered by the
    siloxane list's Genualdi 2011 citation already, and D7 and D8 need a
    source of their own. What no citation supports is recorded as the
    residue, named. D7 and D8 have one: Fromme et al. 2019 (Environment
    International 126, 145-152) measured both in indoor air. L3 to L5 ship
    as their own list, `linear-siloxanes`, since the cyclic list's id says
    what it holds.
  - **Estimated on the #2113 round** (build `8c163be`, default seed
    loaded; the agent's `window_estimate.py`): 41 default-seed formulas sit
    outside today's window (P 17, F 11, Si 4, I 4, Cl+P 2, Cl+I 1, Br+I 1,
    and one Keller formula over both bounds that reaches no peak). Their M0
    ions reach 84 peaks on A and 91 on B, where the reference commits the
    admitted formula on 31 and 39 - D3 to D6, triethyl, tributyl and
    triphenyl phosphate, triphenylphosphine oxide; today those peaks are
    unassigned or carry CHNO readings, D5's urea adduct among them - and
    TFA on every sample of C2 and D (12 peaks each, unassigned today). The
    positive-mode lists reach 94 peaks on the negative sets where the
    reference confirms none of them, and three assigned rows on D that it
    does confirm; that is what the polarity gate is for. Of A's 87 G6
    rows, 52 have a seed parent now outside the window (12 of the 13 at
    assigned tier), 18 a parent inside it and 17 a parent no list holds,
    so the grid part goes from 69 to 17 at best and the target of 10 needs
    list content. The isoprene list's new ions reach C2 18, D 56, E 47, F1
    68 and F2 66 peaks, mostly under the formula the untargeted stage
    already elects there, so the lift moves rows from Stage B to Stage A
    rather than changing formulas; on F2, 12 of those peaks hold a Stage A
    row with another formula. IBr's `+Br-` ion lands on peaks the
    reference reads as reagent (D 2, E 3, F1 4). The long PFCAs tier low
    by `formula_plausibility` (C8 0.60), which is left as it is.
  - **Two PRs.** The schema, the seed's refresh, the sync flags and
    `deactivate` first, with no engine change and no store round; the
    engine, the isoprene list and the Si/P test second, with the round.
- **Why.** The siloxane and phosphate peaks are the brightest wrong answers
  in every sample, and the families peaky hard-codes are exactly the ones no
  formula grid reaches: known-formula matching is the only way they get
  assigned. A seed is a prior, not a label set - Stage A wins the peak -
  which is why decision 3 (tiers and the mass gate apply to Stage A rows)
  is part of this step's safety.
- **Verify.** The integrity test over every shipped list; D3 to D6,
  triethyl, tributyl and triphenyl phosphate and triphenylphosphine oxide
  per sample on A and B (polarity keeps those lists off the negative sets),
  and TFA on C2 and D; when a seed loads, the gate reports the peaks that
  change owner between the database and untargeted stages, split by
  whether a newly admitted formula is on the peak, because a seed formula
  stealing a peak from a better untargeted answer is the main risk; G1 and
  G2 on every set; gate metric G3; G4 on the IBr2- peaks; the rows the
  isoprene lift moves from Stage B to Stage A; the size of the known set
  and the run time; and gate metric G6 (at most 10 on A), which decision 11
  moves here from the stage-1 gate: on A the reference's parent ion for 78
  of the 79 carries silicon or phosphorus, and the 29Si and 30Si lines of
  the column-bleed siloxanes attach to their parents only once the known
  window names them. The target stands as written; the residue is reported
  with its parents named and split by whether a cited list could hold them.
  The round runs with the seed refreshed, on the reference frozen at
  `cc07ce1` (decision 16).
- **2.5c What.** The same-ion ambiguity on a Stage A mirror row: its own
  PR right after 2.5b, measured alone, for mirror rows only. The reagent-N
  rule reads a row's same-ion readings off the finder's `alternatives`, and
  a Stage A row carries none, so dropping the `source == database`
  exemption in the cross-channel pass changes nothing on its own: carrying
  the ambiguity means building the same-ion splits for a mirror row's ion
  and recording them as the rule does for an election. The seed round's
  cost was nine steals on B, where a nitrogen-bearing seed formula read
  through `+H+` is the same ion as the reference's ammonium adduct of the
  nitrogen-free neutral; and the isoprene lift adds nine nitrogen-bearing
  formulas on the nitrate sets, where a row the untargeted stage elects
  today is inside the rule's reach and the same row matched by Stage A is
  not. The radical rule and the formula-shape signatures keep exempting
  every Stage A row: narrowed to the target library they would cap HOI,
  HIO3, ICl and IBr by the carbon-free signature, which is what the iodine
  list exists to assign. **Verify.** The nine same-ion steals on B carry
  the ambiguity; the owner-change report; G1 and G2 on B and on the nitrate
  sets.
- **Size.** M for 2.5a, M for 2.5b in two PRs, S for 2.5c. Independent of
  2.1-2.4. The seed
  proposal's four decisions (sweep order nitrate, bromide, urea and
  ammonium; opt-in production loading; radicals and clusters off by
  default; widen the window together with the seed) are taken there and
  assumed here, and decision 15 records the list format.

### 2.5d Review of the curated lists

- **What.** Before a list's rows count as curated, every collection attached
  to the gate samples' batches is audited, which includes the calibrant and
  diagnostic collections the ionization modes name.
  - **Flag** an entry whose formula is odd-electron; whose matched ion goes
    through a mechanism the mode does not declare, or is reachable only as a
    workaround for one; whose ion another channel of the same sample reads as
    a different neutral; and one that never matches.
  - **Fix or remove** each flagged entry on the testbed with the plan owner,
    and record the verdict and its reason. The lists on the internal server
    the testbed is cloned from are the plan owner's to change.
  - **Decide how calibrant and diagnostic lists count.** The modes attach
    them automatically, and they hold instrument and reagent lines as well as
    analytes. Today their rows count as curated identities: exempt from the
    radical rule, the formula-shape signatures, the reagent-N rule and the
    mass gate's cap, and anchoring Stage A's width and the run's calibration.
  - **Then revisit decision 3's exemption** on the reviewed lists.
- **Why.** The gate batches carry no targets list at all: every target
  library row on the 43 samples comes from 12 collections the modes attach, 5
  of calibrants and 7 of diagnostics, holding 47 compounds. Six entries were
  odd-electron formulas written so that one of the mode's mechanisms reaches
  an ion:
  - C11H15O4 and C10H15O, on nitrate modes that declared no carbonate channel,
    for C10H14O's carbonate cluster;
  - HCO3 and CHO3 for CO3-, HS3 for S3-, and Br for Br2-.

  On step 2.2b's round they committed 31 M0 rows under the curated exemption
  (C 5, C2 18, F1 8), and C11H15O4 held set C's strongest line. The first two
  are fixed on the testbed (the section after step 2.2b): that line now reads
  as the reference reads it, and G1 conditioned on C falls from 1.2 to 0.2.
- **Verify.** Every flagged entry on the gate batches with its verdict; the
  gate re-run on the reviewed lists and read against the round before; the
  curated rows beyond three widths and the rows the exemption protects,
  re-counted; decision 3's exemption answered.
- **Size.** S-M: mostly curation and a round. If deciding how calibrant and
  diagnostic lists count changes the engine, that is its own PR. On the
  reference frozen at `cc07ce1` (decision 16).

*The plan owner's answers (2026-09-15), taken on the audit.*
- **The four odd-electron workaround entries are removed** from the lists the
  gate batches carry: HCO3 and HS3 from C2's nitrate monitor, CHO3 from F1's
  bromide monitor, and Br from that monitor and from the bromide calibrants.
- **The engine defect the audit found is fixed in its own PR:** a reference
  list's copy of a reading a library entry makes could take the entry's line.
- **Calibrant and diagnostic lists keep counting as curated, except for the
  mass gate's cap** (decision 3's fourth addendum). Their rows anchor Stage A's
  width and the run's calibration and escape the rules that doubt an
  election, and a line of theirs off calibration is capped unless an
  isotopologue tracks it.
- **Entries that never commit a row are kept**, each recorded with its reason.

### 2.5e An offset from the reagent lines where the library is thin

- **What.** Where Stage A matched too few of the target library's lines to fit
  an offset, the run scores the untargeted stage at the offset the reagent
  pre-pass already measured from the source's own ions. The width stays the
  instrument class's: a handful of bright reagent lines is not a measurement
  of what the instrument does to an analyte.
  - **Only when there is a bias to correct.** The offset is taken only where
    it is larger than the class's own precision, so a sample whose reagent
    lines say the axis sits where it should is scored exactly as it is today.
  - **A fallback, not a pooled anchor set.** Reagent lines cluster at the low
    end of the range, where step 2.2b measured an Orbitrap's residual growing
    as 1/mz, so adding them to a library's own lines would pull a sample that
    can already measure its offset away from it.
  - **Recorded** on the run: `pattern_scoring.mu_source` says `reagent`, and
    `reagent_lines` and `reagent_mu_ppm` say what the lines read wherever the
    pre-pass ran, whether the offset was taken from them or not.
  - **As built (2026-09-16): every claimed line, and at least three.** The
    offset is the median mass error of every line the pre-pass claims,
    isotopologues included, which is what the measurements below read. It is
    not the correction the pass claims its own rungs against, which is the
    median of two or three anchors, the source's brightest ions. On C2 those
    sit at +1.26 and -0.05 ppm while their five isotopologue lines sit at -0.7
    to -1.9, so their median of +0.60 is on the wrong side of zero and beyond
    the guard. Fewer than three lines are read as no offset: one line is its
    own error and two are their mean. The guard is the width a sample with no
    fitted width is scored at, the class's precision widened for the
    prediction's error: 0.58 ppm on an Orbitrap and 3.04 on a TOF, the widths
    the measurements below compare against.
- **Why.** Step 2.5d's round left C2 scoring at no offset: its curated lines
  were what measured one, and four to five of them were the odd-electron
  entries the step removed. Measured on the gate's last round, per sample:
  - **C2** claims 7 reagent lines between m/z 62 and 129 whose median error is
    -1.25 to -1.26 ppm, against the -1.10 to -1.13 its own committed rows
    measure. That is nearer the run's own centre than the removed entries ever
    put Stage A (-1.53 to -1.58), and far nearer than the zero it scores at
    now.
  - **A** claims 10 lines between m/z 61 and 181 whose median is -0.90 to
    -0.96 while its commits sit at +0.02 to +0.06 - the 1/mz residual, and the
    reason these are a fallback rather than anchors.
  - **D** (median 0.00 to +0.35 against commits at -0.08 to -0.23) and **E**
    (+0.19 to +0.71 against +0.55 to +1.61 on a 3.04 ppm class) are inside
    the guard and do not move.
  - **C** claims none: its mass range starts above its reagent's lines.
  - **B, F1 and F2** fit their own offset and never reach the fallback.
- **What it does not fix.** C2's axis is genuinely off, by the same 1.1 to 1.3
  ppm its reagent lines and its own commits agree on. The repair is the
  calibration node, and those reagent cluster lines are the calibrants step
  2.2 found its mode lacking; recalibrating C2 re-bases every C2 number in
  this plan, so it belongs with 2.7a's refresh rather than mid-stage.
- **Verify.** Only a sample scoring at no offset may move, which on the gate is
  C2: how many of the 146 rows it lost in 2.5d come back and what the
  reference says of them; A, B, C, D, E, F1 and F2 identical row for row; the
  run's own calibration and the gate's caps unchanged, since this is what the
  search is scored at and not what the gate measures; G1, G1 conditioned and
  G2 per set.
- **Size.** S: one number into Stage A's scoring, a guard, a record, and a
  round. On the reference frozen at `cc07ce1` (decision 16).

### 2.5f A list hit meets the untargeted grid

- **What.** A formula's presence on a list is evidence for the row, not a
  label on it: a list hit is `assigned` only where nothing plausible
  competes with it. Today Stage A wins the peak, the untargeted stage runs
  on the remainder alone, and a Stage A row's `candidate_density` counts
  rivals inside the known set only, so the alternatives are never
  enumerated on exactly the rows where the list decides. Two PRs, measured
  one after the other:
  - **The measurement.** The untargeted enumeration runs over Stage A's
    peaks as well, and the density anchored on the list formula counts the
    grid's rivals beside the known set's. The density rule then reaches a
    list hit as it reaches an election - capped unless a second channel
    corroborates it - and the reason names it. No election changes and no
    owner moves: this PR answers how many list hits have a rival inside the
    width, which nothing has measured.
  - **The election.** The list formula is one candidate in the arbitration
    and carries a prior; a grid rival may win the peak, and the assigned
    tier comes from the same rules for every row. The prior orders a tie,
    it does not tier one: a tie stays candidate. The mass gate already
    reads a library row like any other since step 2.5d, so nothing of the
    cap changes here (decision 3's fifth addendum). The row keeps saying
    which list named it.
  - **What stays.** Stage A's width and the run's calibration fit over
    corroborated list hits and never depended on the tier; they keep doing
    so. Isotopologue rows follow their owner as before. Uniqueness is
    relative to the searched grid: the siloxanes, the phosphates and the
    iodine species promote straight back because the grid builds no rival,
    which is right, and the run records its search scope.
- **Why.** Decision 18 applied to the lists, as the plan owner put it on
  2026-09-15: the aim is the most likely formula for each peak, and a
  formula's presence on a reference list is evidence that should not mean
  `assigned` by itself where plausible alternatives exist. The share at
  stake is large. On step 2.2b's round, of the monoisotopic rows at
  assigned, Stage A holds A 219 of 1,012, B 408 of 4,065, C 246 of 602, C2
  135 of 312, D 458 of 897, E 53 of 225, F1 414 of 752 and F2 276 of 696;
  the target library's part is A 29, B 22, C 15, C2 16, F1 24 and F2 32,
  and the reference mirror's the rest. Most rest on the fit alone: the rows
  whose gate record names neither a tracking isotopologue nor a curation
  are A 145, B 239, C 213, C2 113, D 208, E 39, F1 336 and F2 229. Read
  against the reference, it commits another formula on A 7, B 25, C 5, D 1,
  E 6, F1 14 and F2 14 of them and is silent on A 48, B 52, C 16, C2 34, D
  16, E 41, F1 367 and F2 249. On the Orbitrap sets the contradictions are
  the same-ion nitrogen ambiguity - a nitrogen-bearing neutral through
  `+H+` against a nitrogen-free one through `+NH4+` - and C's five are the
  workaround entry step 2.5d's section fixed. The reference is no measure
  of the rivals either, since it assigns list hits the same way: on A and
  B the target library's C9H19NO is assigned on every sample through `+H+`
  and through the uronium adduct, the reference reads the second the same
  way and the first as C9H16O through `+NH4+`, and so splits one neutral
  across its channels. The number that decides is the grid's, and the
  first PR is what produces it. Step 2.5d has since moved C2's count (181
  assigned rows on its round), and the measurement reads the round it runs
  on. The tiers already exist to carry the answer: the density rule, the
  envelope-neighbour rule, the cross-channel escape and the mass gate reach
  a Stage A row today, and 3,953 Stage A monoisotopic rows sit below
  assigned on the 2.2b round, 3,586 of them on their own fit with no rule
  involved.
- **Verify.** From the first PR: the Stage A rows at assigned with a grid
  rival inside the width, by set, by list and by whether a second channel
  holds the neutral; the rows the density rule caps and what the reference
  commits on their peaks; the run time. From the second: the peaks a grid
  rival wins from a list formula, split by whether the reference agrees
  with either; the list-only families (siloxanes, phosphates, the iodine
  species, the PFCAs) promoting back through uniqueness; G1 and G1
  conditioned, expected to rise because the reference assigns list hits
  too, and read as decision 18 reads them; G2 unchanged where no rival
  wins; the three library isotopologues step 2.5d's cap took, re-read with
  the grid's rivals beside them.
- **Size.** S for the measurement, M-L for the election, which needs the
  prior's weight decided (a decision of its own). After 2.5d, whose audit
  matters here because a wrong entry with a prior leans on every tie it
  touches, and independent of 2.5e; the first PR was to come before 2.4e,
  which claims lines under assigned formulas. 2.4e went first on the plan
  owner's word, so the first PR also reports the claims whose neighbour it
  takes below assigned, which a run then leaves as the rows they were. On the
  reference frozen at `cc07ce1` (decision 16).
- **As built, the measurement (2026-09-16).** #2148, measured on step 2.4f's
  round (*After step 2.5f's first PR*).
  - **The question put to the grid.** A run that searches enumerates, for every
    monoisotopic Stage A row, the formulas the element box holds for its peak.
    It uses the search's own window, channels and heuristic filter, and scores
    them beside the list's reading with the search's own fit and the whole
    spectrum as context. Nothing is committed or claimed. On the gate the
    reading's fit on that scale equals Stage A's, and run over the search's own
    rows the count equals the density the search stored on all but F1's densest
    peaks, where more candidates compete than the scorer keeps.
  - **What counts as a rival.** A closed-shell formula the evidence cannot
    separate from the list's reading, on the plan owner's answer (decision 3's
    sixth addendum). The reading's own ion split another way is the
    nitrogen-ambiguity rule's question, and a formula the known set already
    counted is not counted twice.
  - **Into the density.** The rivals are added to the row's
    `candidate_density`, and the density rule reads it as it reads an
    election's: a lone list hit with a rival is held at candidate, a second
    channel keeps it, and the reason names the rivals.
  - **The record.** The row carries `provenance.grid_rivals` (the known set's
    count, the rivals added and the first five named, the reading's fit on the
    search's scale, whether the box holds the formula), and the run carries
    `search_scope.list_hits`. The run-less ingest fold runs no search, so its
    list hits keep the known set's count.
- **As built, the election (2026-09-17).** #2149, measured on the
  measurement's round (*After step 2.5f's second PR*).
  - **The question put to the search.** Every monoisotopic Stage A row whose
    channel the search runs is one of the search's targets. The list's reading
    is a candidate beside the grid's, scored on the search's own fit.
  - **When a rival takes the peak.** Its evidence (fit times plausibility) has
    to be past the reading's times the prior, 2, by more than the gap ties are
    counted at. Its envelope has to hold its required lines, and it has to
    explain the reading's own lines (decision 19).
  - **What a rival's win does.** The rival's rows are committed as the search
    commits any election. The reading's isotopologues leave the ledger with
    it, and their peaks go to the rival's envelope or stay unassigned.
  - **Where the reading keeps its peak.** The row stays as Stage A built it,
    the grid's closed-shell rivals are counted into its density from the same
    pass, and its lines stay the list's. A compound of the target library keeps
    its peak whatever the grid holds. A list's peak is elected on even where an
    earlier envelope matched it as a line.
  - **The record.** A row that took a list hit's peak lists the list's reading
    first among its alternatives (`displaced_by_rival`). It also carries
    `provenance.list_reading`: the list's formula, ion, source, compound, tier
    and identities, both evidences and the prior. The tier is the one the
    reading's evidence gave it, before the run's rules judged the ledger, as
    on a claim's displaced reading.
    - A kept hit's `grid_rivals` names the rival that cleared the prior under
      `held_against`, with why.
    - `search_scope.list_hits` counts the peaks measured, kept with a rival,
      taken, and held by lines or by the library.
  - **What holds no election.** The run-less fold and the batch ledger's
    search, which enumerates only unassigned anchors, hold none.

### 2.6 Frontend: profile, reasons, roles

- **What.** `PeakAssignConfigForm.vue` gains the profile and context
  selectors (auto by default, resolved name shown); the run provenance chip
  shows the profile; the inspector shows `mass_z` and the same-ion
  alternatives beside the tier reasons 2.4c put there; the ledger renders
  `reagent` and `artifact` roles
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
  the released lower bound, re-locks, bumps peaky's version (its package
  version string derives from `pyproject` since peaky #39, so nothing is
  corrected by hand), and peaky's CI then installs from PyPI, which is
  the test that main still works with master; peaky releases; and only then
  is `score_pattern` deprecated in the library (decision 12), once the
  goldens harness has moved as well. The stage-2 epic's review into develop
  does not move the branch: the coupling is to the release.
- **The reference is refreshed once, before the gate (2.7a).** Since the
  reference was pinned at `cc07ce1` (2.1b), peaky's main took 22 PRs and
  release 0.8.0 (tagged 2026-09-13 at `c7e0fe7`, 154 commits past the
  reference branch's base), none of them on the reference branch, and
  four of them change what peaky commits on a single sample:
  - the pass height gate went from an absolute 100 cps to the sample's
    own noise edge (peaky #33), and the 100 cps gate is why the reference
    commits 28, 100 and 46 Assigned peaks on the TOF sets;
  - the reference lists now activate on single-sample runs (peaky #32; at
    `cc07ce1` only the batch path activated them, so no reference run so
    far had the list rescue), and peaky's copies of the lists now read
    radical status from parity and carry the same Keller split as ours;
  - the tier gate judges a row against a mass-dependent centre (peaky
    #30; step 2.2b takes the same rules);
  - a labelled reagent's unlabelled impurity line reaches the local scorer
    at the profile's purity (peaky #30), and pass 0 gained positive-mode
    cyclosiloxane and indoor-sulfur families.
  A rebased reference therefore does not reproduce today's table. The
  reference stays frozen at `cc07ce1` through the TOF width fix, 2.5b,
  2.5c, 2.2b and 2.6, so each step's round is read against the round
  before it, and is
  refreshed once as step 2.7a: the reference branch rebased on main 0.8.0
  (the overlap is `cli.py`, `io_mascope.py`, `assign.py`, `provenance.py`,
  `local_scoring.py` and the lockfile), `mascope-tools` re-pinned from
  `fc25575da` - on no branch since the 2.1b rebase, only under
  `refs/pull/2093/head` - to the epic's head,
  `tool.hatch.metadata.allow-direct-references` set so the branch's CI
  installs at all (peaky #32's note), all 43 runs re-published with
  peaky's commit recorded, and the 2.7 table read against both references:
  the frozen one for continuity with stages 1 and 2, the refreshed one as
  the number that carries forward. The TOF sets, which decision 14 carried
  to 2.7 for a reference that commits there, get that reference here.
  Fixes to peaky land on main; the reference branch holds only the twelve
  commits that need the unreleased library (decision 16).

### 2.8 The band first, one reading per ion

- **What.** Four changes, from the plan owner's reading of the peak inspector
  on the 0.5.0 round (decision 20):
  - **The band leads the reasons.** A row under the top band names it first
    (`evidence_band`): its evidence, the fit and plausibility it is the
    product of, and the band it falls short of. The tier is the band's before
    any rule, and a row the band held low listed only what it stood on. The
    band is not a rule, so it never `caps`.
  - **One reading per ion.** The cross-channel pass caps at candidate a row
    whose ion also reads as a closed-shell molecule through another channel,
    unless a second channel committed its neutral: `ambiguous_nitrogen` where
    the two readings put a different number of nitrogen atoms on the
    compound, two nitrogen-carrying adducts against each other included, and
    `ambiguous_adduct` where they do not, a nitrogen rival named before one of
    the same count whatever order the family is stored in. A radical reading,
    the target library and a second channel settle it, and the row says which
    (`same_ion_settled`); `no_close_rival` no longer says the evidence
    separates the readings of one ion. A reading of the row's own neutral, one
    that puts a labelled atom on the analyte and one nothing can read are not
    weighed, nor is a row whose own channel the run cannot name.
  - **A channel is searched once, and a secondary one stays secondary.** A
    mode that declares a channel its profile also opens - set C's modes
    declare `+CO3-` since 2026-09-15, so that the monitor's carbonate reading
    can be matched - searched it twice, and every reading through it came back
    as another reading of its own ion. The resolved profile adds to the search
    only the secondary channels the mode does not declare, in the run and the
    batch search alike. A declared channel the profile names secondary is
    still held to what a secondary channel is, whatever the spectrum showed:
    the cap on an uncorroborated winner and the tie that goes to the mode's
    other channels (decision 20).
  - **The inspector names the ionization and the lists.** The ionization
    mechanism, and what a reference list calls the formula, sit above the
    evidence. The detail read looks the names up for the row, its close
    alternatives and the other readings of its ion (`known_compounds`), scoped
    as Stage A matches - the sample's polarity, each source's window under the
    run's ceiling, radicals only from a source that allows them, the
    deployment's licences - with how many records name each formula in all
    (`known_compounds_total`). The inspector marks a name the run did not match
    from the list as *potential*.
- **Why.** The inspector showed dimethylformamide `[M+H]+` at m/z 74 below
  assignability under two ticks, a second channel and no close rival: its fit
  of 8% set the band, and nothing said so. And 3,677 of the round's 8,567
  assigned monoisotopic rows carry another reading of their ion: a second
  channel settles 2,866, the other reading is only a radical on 736, and
  nothing settles 75 - 70 urea adducts against an ammonium reading, which the
  nitrogen rule did not ask because both channels carry nitrogen, and 5
  bromide clusters. 265 rows on C listed their own reading as another one.
- **Verify.** The gate round at rules 6: which rows move and why; every row
  under the top band names it first; no row lists its own reading; G7 at 0.
  G1 and G2 are read and reported, not targeted (decision 20).
- **Size.** S. The engine stays at 0.5.0, since steps inside a stage do not
  bump; the rule set is 6.

## Stage 3 - read the source right (engine 0.6.0)

What a chemist reading the ledger of a customer's chamber oxidation dataset
found wrong before any batch is consulted (decision 21). The dataset holds
six Orbitrap chemistries and a mixed-reagent TOF batch (sets G to J); the
reading is in the private testbed notes and its numbers are under "Baselines
on the chamber dataset" below. Each step is small and lands as its own PR
into `develop`; the stage ends with a gate over sets A to J and the 0.6.0
bump.

### 3.1 A profile for the charge-transfer source

- **What.** The library gains the EasyIC profile, pulled forward from step
  4.5 where peaky's port was listed: positive, charge transfer `[M]+.` with
  hydride abstraction `[M-H]+` and protonation `[M+H]+` as secondary
  channels (peaky #27, #53); negative, deprotonation with the source's own
  anions as reagent lines. Both take the ambient context's element caps and
  ratio windows. A mode declaring only a bare `+` or `-` resolves to this
  profile - and until it exists, to the identity profile with the ambient
  context - never to the ESI profiles with no context.
- **Why.** The two EASY-IC batches of the chamber dataset resolve to
  `ESI_NEG` and `ESI_POS` with no context, and the untargeted grid (C0-60
  H0-120 N0-6 O0-25 S0-3, no ratio windows) then commits 357 of 373 assigned
  neutrals on the negative batch as formulas no atmosphere produces (C4H2N5-,
  C3N2O-, C3HN4-, median H/C 0.5 on three-carbon molecules) while carbonate,
  nitrate, bicarbonate, bromide and trifluoroacetate stay unassigned. On the
  positive batch the toluene radical cation is assigned, protonated toluene
  is a candidate, and tropylium, a third of the batch's intensity, sits below
  assignability because `+` is the mode's only mechanism.
- **Where.** `mascope_tools.composition.profiles` (the polarity fallback and
  the new profile); the reagent pass's source-ion library for the EasyIC
  source.
- **Verify.** Sets I+ and I-: G9 from 51% and 96% to the target; G10 from
  0. No change on any other set.
- **Size.** M.

### 3.1c The stronger partner decides

- **What.** The partner gate (`engine.apply_partner_gates`) judges a contest
  it does not judge today. A monoisotopic row read through a partner-gated
  channel whose family holds another reading through a gated channel, both
  with a partner, takes the reading whose partner is the stronger: the
  partner committed at the higher tier, and at equal tier the brighter row.
  The partner ledger records, per neutral, the strongest row that commits it
  through a mode channel (tier, then peak intensity) and stays current
  through every swap, as the count does today. The reading it takes is
  swapped in the way the gate swaps now (the ion's fit and mass error stay,
  the tier is read off the reading's own plausibility under the mass gate's
  ceiling) and the record names the reading it outweighed and by what. The
  outweighed reading stays on the row as a same-ion alternative and is not
  marked as unmet, because the sample did bear it out. The margin decides
  what the cross-channel pass makes of it: where the partners' tiers differ,
  or the stronger partner's row is at least ten times the weaker's
  (`PARTNER_MARGIN`, a named constant beside the round cap), the reading is
  marked outweighed and the pass reads the ion as settled by the stronger
  partner (`SETTLED_BY_PARTNER` beside `second_channel`, `target_library`
  and `radical`); within the margin nothing marks it, and the pass reads a
  rival the sample also shows as the doubt it is, `ambiguous_adduct`, so the
  row is candidate with the rival named. That last rule is the pass's, not
  the gate's, and it reaches every row: a corroborated reading whose rival's
  molecule the sample commits through a mode channel is no longer settled by
  its own corroboration alone. Nothing else moves. A reading whose neutral
  has no partner still goes through the fresh branch, and the mode's own
  reading of an ion is not contested by an opportunistic one (decision 24
  keeps a declared channel's formula), only doubted where its rival is
  shown.
- **Why.** The how-it-works page already promises that between two
  opportunistic readings the one whose molecule the sample shows wins. The
  cylinder shows the promise is kept only where one of them is shown. On set
  K the benzyl cation at 91.054 reads as protonated C7H6 at assigned in six
  of six files, because C7H6 is seen through the bare sign at 90.046 in every
  file, at assigned, at 200 to 2,900 counts, while toluene is seen the same
  way at 92.062, at assigned, at 27 to 31 times that. The election's prior
  for the heavier mechanism decides, and the gate, finding a partner for the
  proton's reading, never looks at the hydride's. The methylbenzyl cation at
  105.070 is the same case with xylene against styrene, at 22 to 41 times,
  and C5H7+ at 67.054 with isoprene against C5H6, at seven to ten times. A
  certified mixture holds toluene, xylene and isoprene and none of C7H6,
  protonated styrene's neutral or cyclopentadiene as analytes; the rows are
  wrong, and they are wrong by a rule the sample could have overturned. The
  margin is where decision 18 enters: the benzyl and methylbenzyl contests
  are decided by more than an order of magnitude, the C5H7+ contest by less,
  and a reading the sample shows seven times more strongly than its rival is
  the better reading but not a certain one.
- **Where.** `engine.py`: the partnered branch of `apply_partner_gates`, the
  partner ledger it keeps, `swap`'s record for a contest, the outweighed mark
  on the alternative, and `PARTNER_MARGIN`; `cross_channel.py`: the settled
  reason and the rule that a shown rival is a doubt (`same_ion_question`,
  `same_ion_readings`); the batch path reads it through
  `batch_untargeted.gate_search_rows` unchanged. Tests: `TestThePartnerGate`
  in `test_minor_channels.py` (a contest decided by tier, one by intensity,
  one within the margin, the ledger kept current through a swap, a mode's
  own reading left uncontested), `test_cross_channel.py` (a shown rival
  doubts a corroborated row, an outweighed one does not), and one judged
  ledger in `test_judge_commits.py`. `docs/user/how-it-works/peak-assignment.md`
  gains the margin in the paragraph that already states the rule;
  `CHANGELOG.md`. `tierReasons.js` labels the reason, not the settled-by
  value; if the inspector shows what settled an ion, the new value needs its
  word there.
- **Verify.** Set K, six files: the benzyl cation as toluene less a hydride
  at assigned in six, the methylbenzyl cation as xylene less a hydride at
  assigned in six, C5H7+ as isoprene less a hydride in six, at candidate
  wherever isoprene's row is under ten times the C5H6 row's; the certified
  components' own rows through the bare sign unchanged, G9 unchanged or
  lower. Set L, the control for the scope: every formula stays the declared
  channel's; the rows whose hydride rival's molecule is seen through the bare
  sign fall from assigned to candidate with the rival named, which is the
  tier the other four files of the set already carry. Set I+: the same
  contests between proton transfer and hydride abstraction, reported as a
  reading, G9 not higher. Sets C, C2 and G: report every row a contest or
  the shown-rival rule moves (formate against carbonate is the only pair that
  can contest there), G11 not higher.
- **Size.** S to M.

### 3.2 Formate as an opportunistic channel

- **What.** `+HCOO-` joins the secondary adducts of the negative profiles
  (nitrate, 15N-nitrate, bromide, iodide), gated on the formate ion at m/z
  44.998 the way carbonate is gated on its probe, and capped like carbonate.
  The same-ion policy (decision 20) reaches it: where a peak reads both as a
  C(n+1) acid deprotonated and as a C(n) neutral with formate, and that C(n)
  neutral has a reading of its own on a primary channel in the sample, the
  formate reading is the row's, at candidate unless a second channel commits
  it.
- **Why.** In the 15N-nitrate batch without reagent ion a series read as C11
  acids - C11H20O7 at 263.11 is the batch's strongest assigned peak, with
  C11H20O5, C11H18O6, C11H18O7, C11H20O8 and C11H20O9 behind it - carries 24%
  of the assigned-plus-candidate intensity, all at "assigned" with candidate
  density 1 and errors within 0.15 ppm. Alpha-pinene is C10 and nothing in
  its oxidation adds a carbon; every one of these formulas is, atom for atom,
  a C10 product the engine assigns 46 Da lower plus formic acid, and formate
  is among the strongest ions in the spectra. Neither mass nor co-occurrence
  can separate the two readings (in spectra this dense any small offset finds
  a partner), so the chemistry decides, and the engine cannot express the
  adduct because no profile knows the channel. The formate dimer at 91.004,
  28% of the M0 intensity with reagent ion, is the same gap.
- **Where.** `profiles.py` `secondary_adducts` and the channel probe;
  `engine._apply_minor_channel_policy`; the arbitration's same-ion key.
- **Verify.** Set G without reagent ion: the assigned C11 anion share of
  assigned-plus-candidate intensity (G11) from 24% to under 1%, each row
  moving to its C10 formate reading; the C10 partners' own rows unchanged.
  No change on a set where the formate probe is absent.
- **Size.** M.

### 3.3 Name the source ions

- **What.** The reagent and artifact pass claims the small ions a source
  makes and a chemist recognises at sight, per polarity and profile: formate,
  nitrite and its 15N form, the ozone anion, carbonate with its water and
  hydroxide clusters, bicarbonate and peroxybicarbonate, CF3- and CF3O- (the
  fluorinated fragments that ride with trifluoroacetic acid), bromide with
  its water and peroxide clusters where a mode declares bromide, and
  nitronium, NO+ and O2+ on a charge-transfer source. A claimed row carries
  role `reagent` or `artifact` and names its ion, as decision 10 requires.
  Formate and nitrite double as low-mass calibrants for step 3.5.
- **Why.** These ions are honestly left unassigned today, which is the right
  failure, but they carry 15% of the summed intensity of the 15N-nitrate
  batch with reagent ion (formate alone 5.5%) and 63% of the mixed-reagent
  TOF batch's, and every reading counts them as unassigned intensity. The
  engine already names the reagent's own ladder; this is the rest of the
  source.
- **Where.** `reagent_pass.py`'s library and the profiles' reagent lines.
- **Verify.** G10 on sets G to J at or above target; G4 unchanged or better;
  no analyte row claimed (the stage-1 guard).
- **Size.** S. The union of two reagent libraries on a mixed-reagent mode is
  not in this step (decision 22).

### 3.3b The standard adduct notation

- **What.** The ionization mechanism is written the way chemists and every
  other tool write it: `[M-H]-`, `[M+Br]-`, `[M+NH4]+`, `[M]+.`, `[M-H]+`,
  `[M-CH3]+`, `[M+CH4N2O+H]+`, with the ion's own charge at the end and a
  labelled moiety kept as it is (`[M+^NO3]-`). Three moves, the first two in
  this step and the third at 2.0 (decision 23):
  1. *Accept and show.* `parse_ionization`, the mechanism validator and
     `_mechanism_parts` read both forms; new rows are stored in the standard
     form; the catalogue, the profiles' fingerprints and secondary-channel
     tables, the `tooling/score_eval` panels and the docs are spelled in it;
     the mode editor, the inspector's chips, the ledger exports and the SDK
     show it. The old form still parses on input.
  2. *Migrate.* A data migration rewrites every stored row by the mapping
     `+X-` to `[M+X]-`, `+X+` to `[M+X]+`, `-X+` to `[M-X]-`, `-X-` to
     `[M-X]+`, `+` to `[M]+.`, `-` to `[M]-.`, a parenthesised moiety
     expanded to its terms, with the downgrade applying it backwards. The
     mapping is total and reversible, so the migration is one function and
     its inverse, and a row it cannot read is left as it is and logged.
  3. *Retire the old form* at 2.0, the release that turns assignment on: the
     validator refuses it on input and the parser's second grammar goes.
- **Why.** The `<operation><moiety><moiety charge>` form is read wrong by
  everyone who meets it. Shipped UI text had it wrong until July (the
  ionization method design note, section 2.1); the composition library
  special-cased `-H-` as deprotonation while the validator stored the same
  string as a cation, so two parsers disagreed for a year (step 3.1); and the
  reference engine spells every subtraction the inverse way on both
  polarities, because its authors read the trailing sign as the ion's, which
  is what the standard form makes it. The step 3.1 review cost a round on
  exactly this. The standard form has no such reading: the sign at the end is
  the ion's charge, `[M-H]-` is deprotonation and `[M-H]+` hydride
  abstraction, and the reference engine's adapter becomes an identity.
- **Where.** `mascope_tools.composition.utils.parse_ionization` and
  `combine_formula_and_ionization`; the mechanism pydantic validator and
  `target_ions_compute._mechanism_parts`; `ionization_catalogue`;
  `profiles.py` and `reagents.py`; an alembic data migration; the frontend's
  mode editor and inspector; the SDK's mechanism helpers; the ionization
  method design note's section 4.4, whose structured `Adduct` row is the
  fuller answer and can follow this step rather than precede it.
- **Verify.** Every stored row on every fleet server round-trips old to new
  to old (the fleet corpus's mechanism table, 24 spellings); the gate's
  ledgers on sets A to J are identical before and after, since no mass
  changes; the reference engine reads a run's mechanisms without its adapter.
- **Size.** M, in two PRs (accept and show; migrate). Not a stage-3 gate
  item: it moves no metric, and it lands whenever it is ready before 2.0.

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

### 3.5 Calibrants below the brightest lines, an offset term, and the low-mass bend

- **What.** The m/z calibration node leaves out a calibrant line brighter
  than a cap, as it already leaves out one below `peak_intensity_min`. The
  cap is a node parameter, absolute or relative to the base peak, with an
  instrument-class default, and the fit records it. Where its calibrants
  span the mass range, the Orbitrap fit also takes an offset beside its
  factor, the `ppm = a + b * 1000 / mz` form of step 2.2b's line, so a
  constant offset in millidaltons is calibrated out rather than scored.
- **Why the offset.** Set A's axis carries -0.11 to -0.14 mDa (step 2.2b's
  table), which one factor cannot remove, and at m/z 74 it puts the axis
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

### 3.6 Priors and the dataset's context

- **What.** Every atmospheric context carries a sulfur prior and an
  odd-nitrogen prior: a formula with sulfur, or with two or more nitrogens
  and fewer than two oxygens per nitrogen, needs corroboration beyond the
  mass fit to reach "assigned". A dataset or batch can name its context
  once, and a run on `auto` takes it before the profile's default. On a TOF
  the untargeted grid is bounded by the sample's fitted width rather than the
  instrument class's 10 ppm.
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

- Protocol run over sets A to J, status table, version bump, changelog.
  Expected: G9 at or below 2% and G10 at or above 95% on every gated set,
  G11 at 0 on set G; G1 and G2 unchanged within noise on sets A to F. Set J
  is measured and reported, not gated (decision 22).

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
(the 300-peak cap stands until step 1.6), G6 (isotopologue claiming is step 1.5).

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

| metric | today A | today B | today C | after stage 1 | after stage 2 | after stage 4 (the batch) |
|---|---|---|---|---|---|---|
| G1 "assigned" rows the reference does not confirm (stage 1 gate: A 41.5%, B 24.3%, C 41.5%, D 37.3% - met; C2 55.2% - missed. After 2.1: A 35.4, B 18.9, C 34.7, C2 40.1, D 21.0 - met on all five, and B inside the stage-2 bound. After 2.4, read on the rows the reference commits an M0 on (decision 14): A 3.1, B 12.3, C 1.3, C2 0.4, D 1.4 - met on all five - against A 23.0, B 36.9, C 21.0, C2 23.1, D 8.4 unconditioned. Stage 2 gate (0.5.0, against the refreshed reference): A 6.1, B 32.7, C 10.9, C2 19.8, D 7.8 - met on A, C, C2 and D; conditioned, within 20% on all eight sets) | 73% | 57% | 99% | <= 45% | <= 20% | <= 15% |
| G2 reference Assigned peaks recovered: same formula / same ion (stage 1 gate: A 95.6/97.2% and B 95.2/96.1% - both bounds met; C 87.3/87.9% and D 80.1/82.3% - same formula met, same ion missed; C2 68.3% - missed, and 4.2 points of it are the nitrate ladder the pre-pass correctly claims. After 2.1: A 95.6/97.2, B 95.2/96.1, C 87.7/88.2, C2 74.9/74.9, D 80.6/82.8. Stage 2 gate, refreshed reference: A 95.2/96.6, B 93.8/94.8, C 93.7/93.7, C2 87.2/87.2, D 85.0/86.6 - the formula bound met on all five) | 39% / - | 12% / - | 18% / - | >= 80% / >= 95% (A, C), >= 70% / >= 95% (B) | >= 85% / >= 95% | hold |
| G3 committed formulas with N >= 5; carbon-free formulas (stage 1 gate: A 1.0% - met, B 2.7% - missed, 0.0% on every other set; every carbon-free formula left on any of the 43 samples is a Stage A curated row and the untargeted stage writes none, so the carbon half is met outright. After 2.1: A 1.2% and B 2.8%, still no carbon-free formula from the untargeted stage on any set) | 13%; 59 | 15%; - | 17%; - | <= 1%; 0 off the allowlist | hold | hold |
| G4 reference reagent peaks labelled reagent or artifact (stage 1 gate: A 48 of 58, B 14 of 24, D 123 of 262, E 48 of 336, F1 50 of 333; on C, C2 and F2 the reference's reagent rows are a different claim, so the raw share does not measure this engine's pass) | 0 of 58 | 0 of 24 | 0 of 29 | >= 90% | 100% | hold |
| G4a of those, the ones that **name an ion** (step 1.4's own target; stage 1 gate: A 82.8%, B 58.3%, D 84.4%, E 87.3%, F1 74.6% - missed, and A's and B's misses are second centroids and a reference label 5-7 ppm off, not a missing library entry) | 0 of 58 | 0 of 24 | 0 of 15 | >= 90% | 100% | hold |
| G5 reference Assigned peaks never searched (stage 1 gate: 0 on every set, from 5,304 pooled over A-F2 on the reference's own Assigned tier - A 186, B 4,180, C 7, which reproduces the step-0 baselines beside them) | 190 | 4,181 | 8 | 0 | 0 | 0 |
| G6 main peaks on reference isotopologues (stage 1 gate: 107 A, 460 B, 328 D, 57 C, 24 C2, 8 E, 57 F1, 33 F2, up from 79/54/75 because the peaks the cap hid are now searched - as a share of committed rows A is flat at 5.2%, B 3.4 -> 5.0%, D 8.8 -> 13.8%. Of the rows 1.6 added, the reference's parent ion is outside the searched grid for 20 of A's 28, 293 of B's 406 and 90 of D's 255 - that part is 2.5b's; the rest have the parent on the grid and are the envelope logic refusing or never predicting the line, which decision 11's rider gives to 2.1 and 2.4. After 1.5 the parent was outside the grid for 78 of A's 79 and 52 of D's 75, which is what decision 11 read. After 2.1: 103 A, 432 B, 314 D, 49 C, 18 C2, 7 E, 61 F1, 33 F2, and of the rows whose parent this engine reads with the reference's own formula 100 -> 83 on B and 32 -> 30 on D. At assigned tier: 159 over the eight sets before step 2.4's tiering and 71 after, 47 of them the grid gap - silicon and phosphorus parents - and 24 the envelope part its height test spares) | 96 | - | - | read, not gated (decision 11: <= 10 after 2.5b) | <= 5 | hold |
| G7 uncorroborated commits beyond 3 sigma (stage 2 gate: 0 - no assigned monoisotopic row beyond 3 widths at all) | not gated | not gated | not gated | - | 0 | 0 |
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
2. Every other rung, and every isotopologue, is claimed at the **instrument's own
   precision** (3 ppm Orbitrap, 10 ppm TOF) against a mass corrected by that
   offset, widened by the anchors' own spread where the lock mass jitters. An
   isotopologue is searched at its parent's measured offset.
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

One more defect the anchored build surfaced. The isotopologue search asked for an
envelope and then looked for lines in it, but the prediction stopped at the
scoring path's 1% - so the 18O line, 0.411% of a two-oxygen ion and 0.206% of a
one-oxygen one, was never in the envelope at all. On set A that line is the
19th brightest peak of a sample (7e4 counts): once the pre-pass claimed the
urea dimer and Stage A's urea target went with it, the untargeted stage read
the freed peak as ethylene glycol on the urea channel, at candidate tier, on
all six A samples and both anchored B samples. The phantom this step exists to
prevent, one line below where it used to happen.

`predict_isotopes` now takes a threshold (default unchanged, so the scoring
path is untouched) and the pre-pass passes its own isotopologue floor, which drops
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
`_isotopologue_hits` took the lightest predicted line as the monoisotopic
reference, which for a labelled reagent is the 14N impurity one mass unit
*below* the ion. The ion then became an isotopologue of its own impurity at 49x its
height, every relative was 50 times too large for the intensity gate to bite,
and the real 14N lines were skipped as if they were the M0. The reference is
now the line the prediction labels `M0`.

### Set C claims nothing, and that is correct

Its acquisition starts at m/z 131 and holds no peak within 100 ppm of any rung
of the nitrate ladder: the source declusters beyond the dimer, the same fact
step 1.2 recorded for carbonate. A window that starts above the ladder cannot
show it. C2 is the same chemistry acquired from m/z 50 and claims 90.9% of its
signal.

### After step 1.5, the isotopologue claim and the artifact role (2026-09-08)

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
isotopologues that were there all along. The signal each engine can account for
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
correctly dropped the stray isotopologue too, so the small peak went unassigned as
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

The isotopologue claim takes peaks the reference reads as analyte M0s: **+2 on B
and +23 on D** (33 of D's 51 at the reference's assigned tier). Of the 26 peaks
on D newly claimed as isotopologues in that class, 20 were unassigned in Mascope
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
uniqueness constraint: a reagent cluster's weakest isotopologue - the 81Br line of
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
plainly why, and it is not the isotopologue claim:

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
so it cannot attach the isotopologue to it, and what it does instead is fit a
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
on-grid rows carry the label "M0" on an isotopologue row under the compound-envelope
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
It cost D 173 committed M0 rows and the isotopologues they owned. The peak now
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

**Every reference run above was scored by one scorer**, peaky's at 5fa9b59 with
the library pinned at fc25575da: the first rounds of A, C, C2 and F2 predated the
anchor rule and were re-run at the head. Each run now records the commit it was
produced at, and the store shows three - 5fa9b59 and the two after it, which
change what a run RECORDS about itself (its version string, its commit, where a
batch publish looks for its manifest) and touch no file on the scoring path.
The re-run reproduces what it replaced: on A, 12 of 2,626 ledger rows differ, all
of them a commentary line and two of them a series unit, where two equally-scoring
series anchors tie and the tie falls the other way between processes; no formula,
tier or score moves. All 43 published runs read `0.7.0+assign0.5.0`,
`score_version` 2, and a noise estimate actually measured on the sample.

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

### After step 2.2, the self-calibrated mass gate (2026-09-09)

Same protocol, on `step-2.2-self-calibrated-mass-gate-2026.09.09-c82f6d5`: all 43
samples re-run, the reference untouched (43 of 43 still the 14:15-14:26Z runs at
`0.7.0+assign0.5.0`), compared against the re-based reference this step's
predecessor published. The before column is the "reference re-based" section
above, so both sides of every number below are v2.

**The step changes no election.** Across the eight sets and 48,894 joined peaks,
not one verdict moved. What moved is tiers, on 49 rows - and the 49 are almost
all isotopologues rather than analytes, which the columns below keep apart because
G7 is read as a statement about analytes:

- **3 analyte commits capped**, all on F2, all from `assigned` to `candidate`.
  The reference contradicts one of the three and does not commit on the other
  two; none is a row it confirms.
- **46 isotopologues capped**, spread over every set, each one a row the
  tracking rule reads as not its parent's. On 40 of them the parent itself stays
  at `assigned`, which is the honest outcome: the ion is still committed, and
  what the run has withdrawn is the second line's claim to be part of it.

Whether an isotopologue the rule reads as a coincidence should be capped at all
rather than left un-owned is step 2.4's question, and this step leaves it capped
rather than inventing a third answer for it.

| set | G1 | G2 formula / ion | its n | fitted width | gate width | analytes capped | isotopologues capped |
|---|---|---|---|---|---|---|---|
| A | 40.2, flat | 93.8 / 96.2, flat | 784 | 0.09 - 0.16 | 0.53 - 0.64 | 0 | 3 |
| B | 44.2, flat | 92.8 / 94.8, flat | 1933 | 0.18 - 0.28 | 0.53 - 0.57 | 0 | 19 |
| C | 35.4, flat | 90.6 / 90.6, flat | 445 | 0.09 - 0.11 | 0.58 - 0.66 | 0 | 5 |
| C2 | 41.8, flat | 77.7, flat | 264 | 0.08 - 0.22 | 0.66 - 0.79 | 0 | 6 |
| D | 21.6, flat | 80.4 / 82.1, flat | 800 | 0.25 - 0.43 | 0.58 | 0 | 6 |
| E | 97.9, flat | 33.3 / 42.9, flat | 42 | 3.03 - 4.37 | 3.04 - 4.37 | 0 | 2 |
| F1 | 99.1, flat | 13.0 / 14.6, flat | 123 | 3.76 - 5.29 | 4.40 - 7.69 | 0 | 0 |
| F2 | 99.2, flat | 15.9, flat | 69 | 1.41 - 2.31 | 1.61 - 4.11 | 3 | 5 |

G2 and its denominator are identical on every set, which is the step's own bound
met exactly rather than approximately: **no row the reference confirms was
demoted anywhere**, so the "agreed rows kept above 95%" clause reads 100%. Eleven
of the 49 were capped from `candidate` to `below_assignability`, and all eleven
are isotopologues. Committed mass error is unchanged on all eight sets, and G1 does
not move on any of them.

Two widths per set, because they answer different questions and the gate needs
both. The fitted width is what the run's own corroborated monoisotopic rows
scatter by - the honest reading of the axis, and what the earlier tables in this
section should be read against. The gate width is the wider of that and the width
the SEARCH scored at, and it is what a row is judged in. They differ by a factor
of three to eight on the Orbitrap sets because the anchors are the run's
best-corroborated rows and so its best measured ones, while the rows the gate
judges rest on the mass fit alone: on A the anchors scatter by 0.10 ppm and the
uncorroborated commits by 0.36. Judging the second population by the first is not
a discovery, it is a category error - measured, it demoted 105 rows the reference
confirms on A alone. A row cannot be off calibration for a distance its own
search was told to accept.

**G7 is 0, and it is no longer vacuous anywhere.** A capped row is still a
committed row, so "uncorroborated commits beyond 3 sigma" can only reach zero as
"beyond 3 sigma AT THE ASSIGNED TIER", which is what the gate enforces and what
is measured here. All 43 samples fitted a calibration and the gate applied on all
43. On analyte commits it fires only on F2, where three rows sit 5.5 to 8.8 ppm
out at a gate width of 1.6 to 4.1; the other seven sets have no analyte row far
enough out to reach it, which is the same "the failure was closed upstream"
result the rest of this section reports.

**What counts as a confirmed envelope, and why the first answer was wrong.** The
first version of this step anchored the calibration on curated rows, on any
monoisotopic row that kept an isotopologue, and on the isotopologues themselves.
On a crowded TOF spectrum that is mostly not an envelope. An isotopologue is paired
inside the instrument class's matching window - 15 ppm on a TOF - so a peak that
is nobody's isotopologue lands there by coincidence, and the coincidence then
corroborated the reading it was matched to and anchored the fit.

| set | child-minus-parent width | children within 3 ppm of their parent |
|---|---|---|
| A, B, D | 0.35, 0.28, 0.45 ppm | 98 - 99% |
| E | 6.1 ppm | 37% |
| F1 | 6.5 ppm | 39% |
| F2 | 4.6 ppm | 46% |

Two lines of one ion differ only by what centroiding does to each, so on the
Orbitrap sets the children are real and track their parents - the bromine
children on D track exactly as the carbon ones do. On the TOF sets most of them
do not. On E those children and the M0 rows they "confirmed" were two thirds of
the anchors, so the run recorded +0.4 to +1.6 ppm at 4.4 to 4.6 ppm wide while
its own uncorroborated M0 rows sat at -0.1 to +0.1 and 2.7 to 2.9 wide. What the
run called its calibration was the scatter of coincidences, and every TOF row's
`mass_z` was stated in it.

So a child corroborates its parent, and is corroborated by it, only when its own
mass error tracks the parent's; and only monoisotopic rows anchor the fit, an
isotopologue being the wider row wherever it is real (0.35 ppm against 0.12 for the
M0 rows it belongs to on A). E still fits a calibration afterwards rather than
standing down - 48 to 53 anchors a sample, well above the eight the fit needs.

**The bar is three sigma of the difference, not one.** What is being tested is a
difference of two measurements, and on the sets that have real envelopes that
difference is about as wide as the class's precision itself, so testing at the
bare precision is a two-thirds-of-one-sigma test. Measured, the share of children
it keeps at one, two and three times the precision:

| set | 1x | 2x | 3x |
|---|---|---|---|
| A | 59% | 84% | 91% |
| B | 66% | 88% | 95% |
| C | 67% | 78% | 81% |
| C2 | 66% | 82% | 90% |
| D | 50% | 74% | 84% |
| E, F1, F2 | 37 - 46% | 61 - 69% | 77 - 82% |

At the bare precision the rule threw away a third to a half of the genuine
children on the Orbitrap sets - the sets whose children the table above shows to
be real. What the anchors then fit does not move at any multiple (A +0.02 ppm at
0.12 wide from one to five times, D -0.17 at 0.35 to 0.40), so the constant never
decided the calibration; it decides which rows a run calls corroborated, which is
the flag step 2.4 reads, and at the old bar a third of that flag was wrong.

On a TOF no multiple separates the two populations, because a coincidental
pairing spreads evenly across the matching window instead of clustering: the
three TOF sets keep 37-46% at one and 77-82% at three, with no step between. The
test there is a purity choice rather than a separation, which is why step 2.4
weighs isotope corroboration by instrument class rather than counting it.

**The gate is a guard, not a lever, and the measurement says why.** Step 2.1 put
the v2 fit at the sample's own width into the finder's ranking, so a candidate
three sigma out stops winning its peak in the first place: the widest mass error
on a committed top-tier row is 0.62 (C), 0.70 (A), 0.92 (D) and 1.02 ppm (B) on
the four Orbitrap sets whose axis is straight, and 2.08 on C2, of which 1.1 ppm
is the axis offset below rather than a fit error. Worse for the premise,
mass error no longer separates right from wrong at all. Of the engine's
assigned-tier rows, the median |ppm| the reference CONFIRMS against the median it
CONTRADICTS:

| set | confirmed | contradicted | reference silent |
|---|---|---|---|
| A | 0.149 (n=814) | 0.088 (n=17) | 0.146 (n=485) |
| B | 0.163 (n=3019) | 0.225 (n=217) | 0.458 (n=1759) |
| C | 0.155 (n=463) | 0.438 (n=2) | 0.187 (n=247) |
| C2 | 1.146 (n=231) | 1.253 (n=5) | 1.097 (n=160) |
| D | 0.195 (n=808) | 0.205 (n=65) | 0.324 (n=116) |

On A the rows the reference contradicts sit *closer* to the calibration than the
ones it confirms; on C2 and D the two are indistinguishable. The one real signal
is B's silence - 0.458 against 0.163 - and B's own fitted width is wide enough
(0.326 ppm, so 3 sigma is 0.98) that almost none of it is reachable. Simulated
before any of this was written: anchoring B's fit on its curated rows alone
(0.227 ppm) would cap 390 of its uncorroborated top-tier rows, 389 of which the
reference does not confirm, for G1 44.2 -> 39.9 at no measured cost. It is not
adoptable as a rule - the same anchors lose five confirmed rows for one on A, and
D has too few curated rows to fit anything - but it says where B's silence lives,
which is 2.3's and 2.4's business.

**The offset half of 2.1b's defect was tried and withdrawn, on the gate.** The
recorded defect was that `fit_mass_accuracy` returns the offset and the width
together and reports `(0.0, None)` below its anchor minimum, so a caller cannot
tell a centred sample from an unmeasured one. Reporting them separately is kept,
and is what made the rest of this section visible. Giving the offset its own
lower minimum is not: an offset looks like the easier measurement, and the gate
measured the opposite.

| set | Stage A anchors | their median | what the sample's own commits say |
|---|---|---|---|
| E | 5, 7, 7 | -8.55, -6.86, -5.29 | -2.06, -2.23, -2.64 (125-142 rows each) |
| F1 | 19-25 | +1.07 to +2.48 | +1.24 to +2.30 (173-215 rows each) |
| C | 5-8 | -0.12 to +0.01 | -0.09 to -0.22 (57-69 rows each) |

Three samples of one TOF acquisition put their five to seven anchors' median 3.3
ppm apart from each other and 4 ppm away from what each sample's own committed
rows say. Correcting by them widened E's committed mass error from 2.03 to 2.33
ppm, added 25 rows to its top tier and halved the share of them the reference
confirms. What made those anchors useless is exactly what the refused width would
have reported - they scatter as wide as the 15 ppm window they were matched in -
so **an anchor set too small to say how wide it is cannot say where its centre
is**, and the two minimums are now one. On C, where the anchors are consistent,
the offset this withdraws was worth 0.1 ppm and changed no election.

**What a run now records, and the gap it exposes.** Two fits, deliberately
distinct: `pattern_scoring` is what Stage A could tell the finder before the
search, `mass_calibration` is what the finished ledger turned out to be able to
measure. They are not close.

| set | searched at (ppm) | measured (ppm) | ratio | Stage A anchors | run anchors |
|---|---|---|---|---|---|
| A | 0.590 | 0.09 - 0.16 | 4.7 | 14 | 36 - 45 |
| B | 0.545 | 0.18 - 0.28 | 2.3 | 12 | 97 - 409 |
| C | 0.599 | 0.09 - 0.11 | 6.0 | 6 | 23 - 27 |
| C2 | 0.743 | 0.08 - 0.22 | 5.0 | 11 | 14 - 16 |
| D | 0.583 (class) | 0.25 - 0.43 | 1.7 | 1 | 123 - 294 |
| E | 3.041 (class) | 3.03 - 4.37 | 0.8 | 6 | 48 - 53 |
| F1 | 5.392 | 3.76 - 5.29 | 1.2 | 21 | 61 - 85 |
| F2 | 2.582 | 1.41 - 2.31 | 1.4 | 23 | 36 - 45 |

The search is scored at a width two to seven times what the run's own
corroborated rows turn out to scatter by, on 1 to 23 anchors where the run itself
has 12 to 290. That gap is why the gate judges at the wider of the two rather
than at the fit alone: the search's width is the one the committed rows were
actually selected under, so it is the floor on what may be called off
calibration. Re-scoring the search at the run's own calibration is circular in
one pass and is not this step's to do, but it is now a measured gap rather than a
suspicion.

### What this step actually found: the axis, not the assignment

Every file behind these 43 samples carries a stored m/z calibration marked
`verified`. This is what each sample's own committed rows say about that axis,
and it is the reading that outlives the gate:

| set | instrument | offset ppm | width ppm |
|---|---|---|---|
| A | Orbitrap | +0.01 to +0.04 | 0.09 - 0.16 |
| B | Orbitrap | -0.21 to -0.19 | 0.18 - 0.28 |
| C | Orbitrap | -0.20 to -0.13 | 0.09 - 0.11 |
| **C2** | Orbitrap | **-1.11 to -1.10** | 0.08 - 0.22 |
| D | Orbitrap | -0.21 to -0.09 | 0.25 - 0.43 |
| **E** | TOF | +0.34 to +1.58 | **3.03 - 4.37** |
| **F1** | TOF | +0.80 to +2.05 | **3.76 - 5.29** |
| F2 | TOF | +0.08 to +2.34 | 1.41 - 2.31 |

(Read on the corroborated monoisotopic rows, and for E on the recalibrated axis.
The first version of this table anchored on isotopologues too and read three to
seven times wider on the TOF sets; the subsection above says why that was the
isotopologues' scatter rather than the samples'.)

**A measurable offset across the mass range is a calibration fault, not an
assignment problem.** C2 sits 1.1 ppm low on an Orbitrap, consistently, on all
six samples; E sat 8 to 10 ppm low on all three until it was recalibrated
(below), which its own calibrants showed and this fit did not - and the reason
is structural rather than a defect in the anchors. **A self-calibration cannot
see an axis error larger than the spacing of the candidates it chose from.** It
is fitted over the run's own commits, and the search picks, for each peak, the
candidate nearest that peak; on a crowded spectrum there is a candidate near
zero whatever the axis is doing, so the fit centres on the search's choices
rather than on the truth. E's uncorroborated M0 rows sat at -0.3 to +0.1 ppm on
the wrong axis too, so anchoring on them instead would have missed it just the
same. This section's own "finding that outlives it" is the same fact from the
other side: 70% of E's formulas changed when the axis moved, and the engine
never noticed. Only external calibrants can see that error, which is exactly
the division of labour issue #2095 is filed on - the assignment layer measures
what it can and the calibration node owns the axis. That is the m/z axis being
wrong, and the place to fix it is the mass calibration node of the signal
processing pipeline - the calibration these files already carry, and which
already claims to be verified - not a correction applied downstream while the
stored axis stays wrong. On the TOF sets the width says the same thing more
loudly: 3 to 7 ppm of scatter is wider than the 10 ppm window the search runs in,
so no assignment rule can discriminate a formula there, and G1 near 99% with G2
near 15% on E, F1 and F2 has been measuring a calibration fault all along rather
than an assignment one.

The calibration node's own stored record says the same thing, and says where the
work is. Each file's `mz_calibration` carries what the fit achieved:

| set | mode | calibrant points | node's post-fit error | what the engine measures |
|---|---|---|---|---|
| A | one-point | 8 | 3.66 -> 0.43 ppm | -0.02 to +0.03, width 0.18-0.27 |
| B | one-point | 3 | 0.52 -> 0.10 ppm | -0.22 to -0.17, width 0.22-0.33 |
| C | one-point | 1 | 0.43 -> 0.00 ppm | -0.22 to -0.09, width 0.13-0.16 |
| C2 | one-point | 2 | 1.261 -> 1.261 ppm | -1.14 to -1.11, width 0.18-0.29 |
| D | one-point | (no quality recorded) | - | -0.18 to -0.08, width 0.36-0.55 |
| E, F1, F2 | TOF mode 0 | (no quality recorded) | - | up to -2.6 ppm, width 2.8-6.9 |

Three different states, and only the first is a calibration. A is a real fit and
the engine's independent reading agrees with it. C2's node ran, reported a
post-fit residual **identical to its pre-fit residual** on two calibrant points -
1.2613440 to 1.2613450 ppm - and stamped the record `"status": "ok"` and
`"verified": true`; the engine then measures that same 1.1 ppm on all six samples.
C's rests on a single calibrant worth 0.07% of the TIC, where a one-point fit
zeroes its own residual by construction. The TOF files carry only the
acquisition's two mode-0 coefficients with no quality block at all: Mascope never
fitted them, and `verified` there means the acquisition's axis was accepted.

So the fix is in the calibration node, and it is two distinct things: fit the TOF
files at all (a calibrant collection for the mode, which is what the node stands
down without), and gate `verified` on the fit's own post-fit residual and on how
many and how strong its calibrant points were, rather than on the fit having
completed. The tolerance in those records, 5 ppm, is the window calibrants are
MATCHED in; nothing in them is a quality bar. The per-run reading this step adds
is the independent check on that bar once it exists.

This turns the engine's own `mu` correction into an open design question rather
than a feature. The engine corrects by Stage A's fitted offset wherever it has
the anchors for one - -1.53 ppm on C2, +2.18 on F1 - which papers over exactly
the defect above: the sample scores as though its axis were right, and nothing
downstream ever reports that the file needs recalibrating. The honest division of
labour is that the assignment layer MEASURES the axis and says so, which it now
does on every run, and the calibration node FIXES it. Withdrawing the correction
would change results on five sets and is not this step's to take; the plan owner's
call, and a candidate decision for 2.7.

Two consequences for this plan, either way:

- **The TOF sets have since been re-calibrated; C2 could not be.** Done in the
  section below, which re-baselines E on both engines and shows F1 and F2 did
  not move. C2's numbers still measure its axis rather than the engine, and
  should not be read as passing or failing an assignment target until the node
  can fit it.
- **A per-run calibration reading is now available to make that checkable.** Every
  run records the offset and width its own corroborated commits show, with the
  anchor count behind them, so "is this file calibrated well enough to assign?"
  is answerable per sample from the store rather than by inspection.

### The TOF sets recalibrated, and what E's axis was worth (2026-09-09)

The section above ends by saying the TOF sets should be recalibrated before they
are assigned again. They now have been, through the calibration node's own
routes at the instrument defaults it seeds its dialog with - a fit and an apply
per file, nothing hand-made. C2 is deliberately not among them: its mode offers
two calibrants, they disagree by the 1.26 ppm the node reported as both its pre-
and its post-fit residual, and a one-point fit on two disagreeing points has
nothing to break the tie with. C2 needs more calibrants, which is the node's
problem to be given, not a refit.

**What the node did.** Two different outcomes, and the difference is the whole
result:

| set | calibrant points | node's pre-fit | node's post-fit | axis moved |
|---|---|---|---|---|
| E | 7, 7, 7 | 8.31 - 10.10 ppm | **0.33 - 0.68 ppm** | **+6.9 to +9.5 ppm** |
| F1 | 4, 4, 4, 4, 4, 5 | 0.06 - 1.13 ppm | 0.055 - 1.126 ppm | 0.000 ppm |
| F2 | 3, 3, 3, 3, 3 | 0.01 - 1.11 ppm | 0.015 - 1.108 ppm | 0.000 ppm |

E's axis was wrong by the better part of 10 ppm and is now right to under a ppm.
F1 and F2 were already right: their refit reproduces the acquisition's own
coefficients to every digit that matters, so not one peak moved. What changed on
those two sets is only the record - it now carries the points, the residual and a
status, where before it carried the converter's coefficients and a bare
`verified: true`. That is the honest reading of "never fitted": the axis was
fine and nothing had checked.

Because F1 and F2 did not move, their runs stand. Re-running the engine over F1's
six samples as a control reproduces the ledger exactly - 100% of rows identical
on both tier and formula, all six - so only E is re-baselined here, and only E's
reference was re-run and re-published.

**E, before and after its axis was corrected.** Both engines re-run on the new
axis; the reference is peaky's `epic/v2-fit-reference` at `cc07ce1`.

| metric | mis-calibrated | corrected |
|---|---|---|
| G1 assigned-tier not confirmed | 99.8% | 97.9% |
| G2 same formula / same ion | 15.4 / 26.9% | **33.3 / 42.9%** |
| G2 n (reference assigned rows) | 26 | 42 |
| reference M0 committed | 139 | 158 |
| both commit, same formula | 6 | 18 |
| signal explained, reference | 0.5% | **26.2%** |
| engine M0 committed | 1,526 | 1,552 |
| engine M0 at assigned tier | 424 | 424 |

The reference improves everywhere: it recovers three times as many agreeing rows
and explains a quarter of the signal where it explained half a percent. Its own
anchors say why, and the two readings of them are different fits that have to be
named apart. Stage A's anchor medians - this server's own matched known species,
five to seven a sample - sat at -8.55, -6.86 and -5.29 ppm before the correction.
What the reference now fits over its own six anchors a sample is +0.71, -0.18 and
+0.69. Stage A's fit still reports no offset on the corrected axis, because five
to seven anchors are below the minimum a width needs and the offset is refused
with it; what moved is the axis both of them read.

**The engine's own reading, and the finding that outlives it.** The run's
self-calibration improves too, and not to zero:

| sample | offset before | offset after | width before | width after |
|---|---|---|---|---|
| 1 | -2.639 | +1.553 | 6.846 | 4.554 |
| 2 | -2.230 | +1.161 | 6.904 | 4.574 |
| 3 | -2.061 | +0.396 | 6.748 | 4.434 |

A corrected axis, calibrants inside a ppm, and the engine's committed rows still
say +0.4 to +1.6 ppm at a width of 4.5. That residual is not the axis - it is the
nearest-candidate bias this section already warned about, measured now against an
axis known to be right.

And the number that matters most:

> **Of the 1,343 peaks the engine committed an M0 on under both axes, 70% carry a
> different formula after the correction - while the count it commits barely
> moves (566 -> 584, 601 -> 610, 572 -> 584).**

An 8 ppm error in the mass axis did not stop this engine committing and did not
reduce how much it committed. What it changed was which formula got named, on
seven rows in ten.

The top tier says the same thing more sharply, and the way to say it is about
its SIZE rather than its membership: 424 rows before and 424 after, and they are
not the same 424. Of the top-tier rows on the corrected axis, 147 sit on a peak
that held the top tier before, and 7 of those carry the formula they carried
then; tiers moved on 765 of the 1,343 M0 peaks both axes commit. The engine
committed as much, and as confidently, on an axis that was 8 ppm wrong - it just
committed different chemistry. That is the strongest available statement
that mass accuracy is not what constrains a commit on a crowded TOF spectrum: the
untargeted stage will find something within its window whatever the window is
centred on, and what decides whether it should is candidate density and
degeneracy, which is step 2.4's. It also sets the bar for 2.4 on these sets: a
rule that cannot tell those two ledgers apart is not measuring the chemistry.

**A trap the re-baseline found, for whoever does the next one.** Applying an m/z
calibration removes the sample's matches and leaves the batch at `rematch`. The
engine does not care - Stage A matches the known set itself at run time - but the
reference reads this server's stored matches as its anchors, so a reference run
started before the rematch completes scores with zero of them, reports
`mu_source: assumed_zero`, and looks like a successful run. The order is: apply,
rematch, wait for the matches to come back, clear the reference's per-sample peak
cache, then run. Both the first reference run of this re-baseline and its publish
had to be discarded for exactly this.

**F1's residual is the finder's question, not the node's.** On an axis its own
bromide calibrants put inside 0.8 ppm, F1's assigned rows sit a consistent +1 to
+2.5 ppm high, flat over m/z, and Stage A's fitted offset (+1.07 to +2.48) is
what corrects it today. A flat bias on a straight axis is not a calibration
fault; it is a question about the bromide mass convention the finder builds its
candidates with, and it belongs to 2.4's brief beside the density work rather
than to the calibration node.

The consequence for the earlier tables is narrow and worth stating: E's rows in
the two tables above ("what a run now records" and "what this step actually
found") were measured on the mis-calibrated axis and stand as a record of it. F1,
F2 and every Orbitrap set are unaffected. C2's -1.1 ppm stands unfixed and is
still the clearest node defect on the gate.

**The residual curves at the low-mass end, and the gate does not cap there
(measured 2026-09-11, after step 2.4d).** On the latest runs of the 43 samples
the uncorroborated monoisotopic rows' mass error, read against the run's own
fitted centre, is a function of m/z on every instrument. On A it is -2.3 ppm
below m/z 60 (9 rows), -1.5 at 60-80 (46), -0.9 at 80-100 (58), -0.4 at
100-150 and zero from 150 up, on a gate width of about 0.6 ppm; the
corroborated anchors curve the same way where they reach, -1.3 ppm at 60-80 on
six rows with almost no spread, and no Orbitrap set has an anchor below m/z 60.
D reads +0.65 ppm at 80-100 (52 rows) against 0.0 to +0.2 above 100; F1 +5.0 at 60-80
(28) and +3.7 at 80-100 (33) against +0.5 above 250; F2 +4.8 below 60 (11)
against +0.1 above 250. That is the axis and not the formulas, because the
anchors are corroborated readings and bend with the rest. The gate fits one
centre and one width over anchors that sit above m/z 100, so a low-mass row
beyond three widths is where that curvature puts it - and the gate caps none
of them: over the 43 runs it capped 3 monoisotopic rows, all on F2 and all
above m/z 200 (216, 323 and 401), because every low-mass row beyond three
widths already sits below assigned on its own evidence. The gate's single
width therefore stands for untargeted rows, as decision 3's addendum found for
curated ones. What the measurement names is a calibration-shape defect on
files the node marks verified, recorded on issue #2095; a mass-dependent term
belongs in the fitted axis once anchors reach below m/z 100, which is step
4.3's, not in the gate.

### Baselines on the chamber dataset (2026-09-24)

Sets G to J, read on chemical plausibility with no reference run (decision
21). Three intrinsic metrics join the gate:

- **G9, implausible assigned neutrals:** the share of assigned M0 neutrals
  that carry sulfur in a sulfur-free system, or two or more nitrogens with
  fewer than two oxygens per nitrogen, or H/C above 2.4, or a negative
  double-bond equivalent. Target at or below 2% on every set.
- **G10, source ions named:** the share of the summed intensity on the source
  and reagent ions of step 3.3's list that a run parks as reagent or
  artifact. Target at or above 95%.
- **G11, formate pseudo-acids:** the share of assigned-plus-candidate M0
  intensity on assigned rows read as a C(n+1) acid whose C(n) formate reading
  has a partner assigned in the sample. Target 0 at "assigned".

| set | samples | peaks per sample | intensity assigned / candidate / reagent / unassigned | G9 | G10 | G11 | mass error, assigned rows |
|---|---|---|---|---|---|---|---|
| G, no reagent ion | 6 | 1,530 | 58 / 21 / 0 / 9 % | 5.1% (120 of 2,364) | 0 | 24% (250 rows) | +0.14 ppm |
| G, reagent ion | 6 | 619 | 16 / 24 / 40 / 15 % | 4.0% (38 of 939) | reagent ladder only | 0.8% (34) | +0.15 ppm |
| H, no reagent ion | 6 | 1,212 | 69 / 19 / 0 / 1 % | 2.4% (73 of 3,039) | - | 0 | +0.01 ppm |
| H, reagent ion | 5 | 328 | 1 / 1 / 96 / 1 % | 4.0% (18 of 445) | reagent ladder only | 0 | +0.88 ppm |
| I, negative | 6 | 224 | 23 / 36 / 0 / 39 % | 96% (357 of 373) | 0 | 0 | +0.43 ppm |
| I, positive | 6 | 134 | 3 / 25 / 0 / 44 % (24% below assignability) | 51% (124 of 243) | 0 | 0 | -0.27 ppm |
| J | 6 | 1,383 | 4 / 4 / 7 / 74 % | 25% (164 of 645) | nitrate ladder only; bromide 0 | 0.3% (36) | +0.32 ppm (worst 9) |

### The certified cylinder, sets K to L (2026-09-25)

Measured on the develop engine with steps 3.1 and 3.2 in (`c14a3fe16`), on the
internal Orbitrap's certified 18-component calibration cylinder: the
charge-transfer source in three windows (sets K, K2, K3) and a proton-transfer
source declaring protonation beside the bare sign (set L), five time-spaced
files plus the brightest per batch, and on K3 the injection-hour file too,
since the fluoranthene beam dominates that window's total signal and the
brightest file is not the cylinder's. The rows and reagent claims are the
engine's; the reference engine's reading of the same cylinder is in its own
record (peaky #53).

| set | samples | M0 per sample | intensity assigned / candidate / below / unassigned | certified components at assigned through charge transfer | nitrogen-rich assigned neutrals (N >= 2, O < 2N) |
|---|---|---|---|---|---|
| K, 50-200 | 6 | 166 | 46 / 13 / 1 / 40 % | benzene 5, toluene 6, xylene 6, isoprene 6, styrene 6 of 6; acetone at candidate in 6; alpha-pinene below assignability in 4 | 21% of assigned rows, 26% of assigned intensity |
| K3, 40-500 | 7 | 73 | 15 / 59 / 17 / 3 % (reagent 5) | on the injection file benzene, xylene, isoprene and styrene assigned; toluene is the library's row, below assignability in 4 of 7 | 34% of assigned rows |
| L, 50-200 | 6 | 298 | 32 / 25 / 13 / 30 % | benzene 6, toluene 5, xylene 5, styrene 5, hexanal 5 of 6; isoprene 3; alpha-pinene below assignability in 5 | 11% of assigned rows, 8% of assigned intensity |

What the set says, three findings and one about itself:

- **Between two partnered opportunistic readings, the mechanism's mass still
  decides.** The benzyl cation at 91.0542 reads as protonated C7H6 at
  assigned in every K file, the failure the reference engine's cylinder run
  names first, although toluene less a hydride is on the row. Both readings
  have a partner (C7H6 through the bare sign at 90.046, toluene at 92.062 at
  3% of signal), so the gate lets the election's heavier-mechanism prior take
  the proton. The same for C5H7+ (protonated cyclopentadiene over isoprene
  less a hydride, 6 of 6) and C6H11O+ (protonated C6H10O over hexanal, 4 of
  6). On L, which declares protonation, the same rows sit at candidate with
  toluene as the named rival: the honest tier, the declared channel's
  formula. Step 3.1c.
- **The instrument's bright-peak shift takes the brightest analyte.**
  Alpha-pinene's radical cation, 13% of L's signal and its second-brightest
  peak, reads 0.9 to 1.9 ppm high while the run's mass z stays under 1.3,
  and its fit falls to 0.01 to 0.42: below assignability in 5 of 6 L files
  and 4 of 6 K files, assigned only where it sits within 0.5 ppm. The
  library's toluene row on K3 sits below assignability the same way. Pinene's
  methyl-loss ion at 121.10, 8% of L's signal, reads as protonated cumene:
  no `-CH3-` channel exists, and pinene itself is no partner while it is
  below. Step 3.5, and the methyl-loss channel in 3.3.
- **A nitrogen-free cylinder assigns nitrogen-rich formulas.** C2HN3, C2N2O,
  C2H3N3 and C2H2N2O are among the neutrals assigned in most files of K and
  K3, through the bare sign on their fit alone, 21 to 34% of the assigned
  rows. The reference engine reads the same family as the source's air-plasma
  background and holds it by its flat time series. Step 3.6's odd-nitrogen
  prior, and 3.3's source-ion list.
- **The narrow windows carry no calibrant.** The mode's calibration
  collection holds NO2, N3, C4H8 and fluoranthene; a window from 50 to 200
  sees two of them and verified 36 of 280 files, the injection hour, and the
  210-500 window has no collection and no calibration at all, so the engine
  refuses it (K2) by the rule that a sample is calibrated before it is
  assigned. The ions the reference engine finds in every file of this source,
  the PAH ladder in the low window and the siloxanes' methyl-loss ions in the
  high one, are the calibrants those windows lack. Step 3.5.

Sound, for the record: the fluoranthene beam is claimed as reagent on every
K3 file; acetone reads as its own hydride ion and its protonated form, at
assigned on K3 and at candidate on K; the charge-transfer readings of the
aromatic components sit within 0.7 ppm of their masses.

What the reading confirmed as sound, for the record: the reagent ladders of
every profile are parked correctly (15N-nitrate, its acid dimer and water
clusters; urea, its dimer and trimer; nitrate's on the TOF); the first-
generation alpha-pinene products come out on top in the chemistry-aware
batches (pinonic, norpinonic and the C10 hydroxy acids deprotonated;
pinonaldehyde, pinonic acid and the C10H14O class protonated and as urea
adducts, each through two channels); the contaminant lists name
bis(2-ethylhexyl) phthalate, triethyl phosphate, the D3 siloxane and the
perfluorinated acids in every ion form; isotopologue bookkeeping holds
(median 13C abundance error 0.07 to 0.08); and mass accuracy on assigned rows
is within 0.4 ppm interquartile wherever the calibration is good.

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
   (step 2.2's section).
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
     after step 2.2b).
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

### After step 2.3, cross-channel corroboration and the reagent-N rule (2026-09-10)

All 43 samples re-run on `step-2.3-cross-channel-corroboration-2026.09.10-ecca5df`;
before-state `ledgers22r3` / `verify_out22r3`, after `ledgers23c` / `verify_out23c`.
The build before it recorded the same ledger to the row - 0 of 48,894 differ - but
read the partner tier by rescanning the ledger per row, which made the pass
quadratic: 917 s of engine time over the 43 against 544 s before the field
existed, and a slowest sample of 119 s against 44. Carrying the tier on the
channel index `channels_by_neutral` already builds puts it back at 513 s and 44 s.

**The corroboration half is the strongest separator the engine has, and it is
free.** A run now groups its committed monoisotopic winners by neutral across the
channels it searched, and a neutral seen through two or more of them is recorded
as corroborated. The plan's verification for this step is agreement on the
reference's own Assigned rows conditioned on that flag:

| set | corroborated n | same_formula | lone n | same_formula |
|---|---|---|---|---|
| A | 703 | **98.7%** | 75 | 54.7% |
| B | 1,672 | **98.0%** | 219 | 71.2% |
| C | 121 | 95.9% | 295 | 97.3% |
| C2 | 33 | 100.0% | 184 | 93.5% |
| D | 330 | **97.6%** | 386 | 83.2% |
| E | 13 | 76.9% | 11 | 36.4% |
| F1 | 12 | **100.0%** | 78 | 5.1% |
| F2 | 7 | 85.7% | 38 | 13.2% |

On seven of the eight sets a corroborated reading is right where a lone one is a
coin toss or worse - F1's twelve corroborated rows are all confirmed against 5.1%
of its 78 lone ones. C is the exception and its two columns are both about 96%,
so nothing separates there rather than the flag being wrong.

**It is not brightness in disguise.** A neutral bright enough to appear in two
channels is also a neutral both engines find easy, so the comparison was redone
inside intensity deciles, where a corroborated row is only ever compared with a
lone row of the same size. The two populations stay as far apart as they are
pooled. On the conditioning of the table above - the reference's Assigned rows -
A runs 96-100% against 0-88% by decile and D 92-100% against 67-97%; over every
committed M0 row instead, with the reference's silence counted against the
engine, A runs 60-85% against 0-36% (decile 5: 83.0% against 0.0%), B 38-75%
against 7-37% and D 54-83% against 27-50%. The two conditionings answer different
questions and the section states both rather than mixing them.

**Coverage is what varies, not the signal.** The share of committed M0 rows whose
neutral has a second channel: A 48.7%, B 59.4%, D 26.6%, C 13.5%, E 11.9%, C2
8.5%, F2 7.9%, F1 5.4%. Where the modes are chemically distinct - protonation
against two reagent adducts on the uronium sets - half the ledger is corroborated;
on the TOF sets, where one channel dominates, it reaches a twentieth. Nothing here
promotes a row on the flag; it is recorded for step 2.4's mechanical tiers to
weigh, and the reach above is what 2.4 can expect from it. The row also records
the best tier any partner channel holds, because that is most of what the flag is
worth: on the Orbitrap sets a candidate-tier partner corroborates about as well as
an assigned one (A 35 rows at 97.1%, B 110 at 95.5%, D 100 at 98.0%) and a
below-assignability one less well (B 20 at 85%, D 40 at 92.5%), while on the TOF
sets most partners sit below assignability (F1 9 of 12, E 4 of 14).

Stage A's existing per-compound adduct corroboration reaches 25 of A's 2,062
committed rows and 22 of B's 9,077, and none at all on the other six sets,
because it is keyed on a curated compound id. This is the same evidence read off
the ledger instead, and it is now what the ledger's "supported by N channels"
marker renders.

#### The reagent-N rule: an election is a prior, not an observation

`+NH4+` on a neutral M and `+H+` on the neutral M+NH3 are the same ion formula -
the same exact mass, the same isotope envelope, the same fit at every width - so
nothing measured separates them. The engine does not pretend otherwise. Since
step 1.3 the finder collapses the two into one hypothesis before anything is
ranked (`heuristic_filter.elect_same_ion_families`) and elects a reading by a
stated policy: a closed-shell neutral first, then the mechanism carrying the most
mass. So the ammoniated reading of M beats the protonated reading of M+NH3, and
the nitrate cluster beats the deprotonated nitrate ester, by decision 9's prior
rather than by any measurement. The readings the election displaces stay on the
row as `same_ion` alternatives, carrying the winner's own fit and mass error.

That policy is defensible. What it is not is an observation: on a run where
nothing else saw the neutral, the analyte's nitrogen count is the prior's answer
and no part of the spectrum's. So a winner through a nitrogen-donating channel
whose own family holds a reading through a channel that donates none is capped at
`candidate` with the reason `ambiguous_nitrogen`, keeping its formula, unless a
nitrogen-free channel or a second and different nitrogen-donating reagent
observed the same neutral. The rule reads the family off the row rather than
rebuilding a candidate from the element grid, so what it doubts is what this run
actually proposed.

**Whether a prior deserves trusting is a question about the reagent, and the gate
answers it.** The same arithmetic holds for bromide - `+Br-` on M is `-H+` on
M+HBr, and D's grid holds both - so the rule could as easily have been written
over same-ion families in general. Measured, it should not be:

| set | reagent | lone donor-channel rows at assigned | reference confirms |
|---|---|---|---|
| D | bromide | 277 | 246 (**89%**) |
| A | uronium (ammonium and urea) | 409 | 79 (**19%**) |

The bromide prior is borne out and the nitrogen prior is not, which is what
scopes this rule to nitrogen.

| set | committed M0 | ambiguous | analytes capped | isotopologues | G1 before | G1 after |
|---|---|---|---|---|---|---|
| A | 2,062 | 711 | 359 | 51 | 40.2 | **25.4** |
| B | 9,077 | 2,407 | 921 | 55 | 44.2 | **40.2** |
| C | 1,100 | 0 | 0 | 0 | 35.4 | 35.4 |
| C2 | 639 | 0 | 0 | 0 | 41.8 | 41.8 |
| D | 2,368 | 0 | 0 | 0 | 21.6 | 21.6 |
| E | 1,552 | 0 | 0 | 0 | 97.9 | 97.9 |
| F1 | 8,927 | 0 | 0 | 0 | 99.1 | 99.1 |
| F2 | 6,724 | 2,062 | 1,225 | 9 | 99.2 | **98.8** |

**G2 and its denominator are identical on all eight sets**, and there were **0
verdict shifts and 0 formula shifts across 48,894 joined peaks**: the rule moves
tiers and nothing else. G1 falls because the rows leaving the assigned tier are
mostly ones the reference does not confirm - on A, 292 of the 359.

**The 15N-labelled sets are untouched, twice over.** C and C2 run a `+[15N]O3-`
reagent whose nitrogen is 0.997 Da from an analyte's own, so the deprotonated
nitrate ester is a different ion at a different mass and the spectrum chooses
between them. The channel therefore donates no nitrogen for this rule; and
independently, the finder never proposes the labelled neutral, so those rows have
no same-ion family to read - 0 of the 55 labelled-nitrate winners on one C sample
carry one, against 51 of 118 on that sample's deprotonation channel. D, E and F1
have no nitrogen-donating channel at all. Had the label been read as ordinary
nitrogen the rule would have reached both sets; simulated that way it capped 297
of their readings with 93 the reference confirms, which is the cost the labelling
exists to avoid.

**A carbonate reading counts as a plain one, and on F2 that matters.** The rule
asks whether the displaced reading came through a channel donating no nitrogen,
not whether it came through protonation: `+CO3-` on M is `-H+` on M+H2CO3, and
puts the nitrogen on the analyte exactly as the deprotonated reading does. On F2,
73 of the 2,062 ambiguous rows rest on a carbonate family member alone, and would
not be in question without that channel.

**The cost on F2 is real and this step does not settle it.** There the rule takes
1,225 of 3,008 assigned rows off the tier, on a set where the reference commits
almost nothing, so no gate number judges the exchange: G1 moves 0.4 points and
the reference confirms 2 of the 1,225. On a nitrate source the cluster is the
expected product and the deprotonated nitrate ester the unlikely one, so the
mode's own prior is stronger there than this rule allows for. Step 2.4 has to
weigh the two; recorded here as an open cost rather than as a win.

**What the cap costs elsewhere, and the check that settles it.** It demotes 405
rows the reference confirms - 67 on A, 336 on B, 2 on F2. That looks like the
rule removing right answers, and the check is which channel the reference reached
those formulas through: on **all 405**, the same nitrogen-donating channel this
engine used. The agreement is two implementations of one convention rather than
two measurements - the reference applies the same preference, and re-reads a pure
hydrocarbon through an N-carrying reagent as the protonated N-compound in its own
`relabel_reagent_n_adducts` stage, which is exactly what A's example rows are
(C10H18, C11H18 and C8H14 via the urea adduct). Where the two conventions diverge
the verdict is `same_ion_other_split`, and the cap finds those: 21 of A's 62
splits, 74 of B's 530 and 6 of F2's 9. The reference also already declines to call
these rows assigned - 57 of A's 67 and 313 of B's 336 sit at `candidate` in its
own ledger - so after this step the engine is more conservative than the reference
on 33 rows of the 2,505 it capped, and agrees with it on the rest. That
`relabel_reagent_n_adducts` exception is itself a candidate for step 2.4's
plausibility demotes.

**What this does not settle.** The neutral is still reported with the nitrogen
where the election put it: the tier says the count is unverified, the formula does
not. The alternative has been first-class on the row since step 1.3, so what 2.4
adds is counting the same-ion family in candidate density - and the `alternative`
this record names is a copy of a family member it could point at instead.

### After step 2.4, mechanical tiers with reasons (2026-09-11)

Every committed row now carries `provenance.tier_reasons` - why it holds the tier
it holds, as a list of `{rule, detail, caps}` - and the rows whose answer is that
the top tier was not earned lose it. The evidence bands are untouched and remain
the floor: every rule only demotes, so no row ends above what its evidence
bought, and the run records the rule set's version, its thresholds and what each
rule took on `config.tiering`, for the same reason it records the bands. The
mass gate's, the reagent-N rule's and the minor-channel caps are restated in the
same vocabulary rather than re-applied, so a reader asking why a row is a
candidate does not have to know which pass to ask. Measured on all 43 samples
against the merged 2.4a build (`70438cb`), whose ledgers are the before: first at
`step-2.4b-tiering-2026.09.11-639e1df`, and again at
`step-2.4d-curated-implausibility-2026.09.11-2012665` once curated rows were no
longer asked the formula-shape questions (below). The numbers here are the
second build's, which differs from the first on C2 and F1 only.

**All 32,449 committed monoisotopic rows carry at least one reason, and none
carries zero.** A row that was capped names what took it; a row that stands names
what it stood on - a second channel, or no formula its own evidence could not
separate from the one it commits; a row nothing this pass reads was measured for
says that instead of pretending to a finding. On the measured build that held for
the monoisotopic rows and not quite for every committed row: 31 committed
isotopologue rows with no owner recorded, all curated - 5 on C, 20 on C2, 6 on
F1 - carried no reason, because the pass had no owner's answer for them to carry.
They now say `not_measured`, with the owner unknown; that changes provenance and
no tier, so every number here stands.

**The pass moves tiers and nothing else.** Of 48,894 joined peaks, 0 changed
formula, role or verdict; every tier move is `assigned` to `candidate`, none is
upward; and G2 and its denominator are identical on all eight sets, same-formula
and same-ion alike. The decoy harness cannot read a difference, since the pass
touches no library code and the harness scores the ranking, which it does not
move.

| set | assigned before -> after | rows taken (confirmed) | G1 | G1 where the reference commits (n) | of which same-ion splits |
|---|---|---|---|---|---|
| A | 1,002 -> 967 | 35 (2) | 25.4 -> 23.0 | 4.1 -> **3.1** (769) | 24 |
| B | 4,490 -> 4,229 | 261 (15) | 40.2 -> 36.9 | 14.7 -> **12.3** (3,041) | 342 |
| C | 717 -> 585 | 132 (1) | 35.4 -> 21.0 | 1.5 -> **1.3** (468) | 5 |
| C2 | 397 -> 305 | 92 (1) | 41.8 -> 24.6 | 2.5 -> **0.4** (231) | 0 |
| D | 1,031 -> 869 | 162 (12) | 21.6 -> 8.4 | 11.7 -> **1.4** (807) | 0 |
| E | 424 -> 239 | 185 (0) | 97.9 -> 96.2 | 70.0 -> 60.9 (23) | 4 |
| F1 | 2,299 -> 522 | 1,777 (4) | 99.1 -> 96.7 | 90.8 -> 67.3 (52) | 0 |
| F2 | 1,783 -> 649 | 1,134 (3) | 98.8 -> 97.1 | 89.4 -> 79.1 (91) | 0 |

**Read on decision 14's conditioning, the step meets its bound on all five
Orbitrap sets**: over the assigned rows the reference commits an M0 on, G1 is
3.1% on A, 12.3% on B, 1.3% on C, 0.4% on C2 and 1.4% on D, and on B 342 of the
373 rows it counts are the same ion split into another neutral and adduct - 1.1%
with the splits set aside. Unconditioned, D is inside 20% (8.4) and the others
are not: A 23.0, C 21.0, C2 24.6, B 36.9. What that remainder is was measured
before the decision and holds on this build: 73-91% of the unconfirmed assigned
rows on each Orbitrap set sit on peaks the reference commits nothing on at all
(B 1,139 of 1,561, with the 342 splits, 31 contradictions and 46 peaks it reads
as an isotopologue beside them), so what is left is a disagreement about whether
to commit, which no rule here can see - the rules that would close it on paper
are the ones that take confirmed rows. The TOF sets fall 1.7-2.4 points on the
unconditioned reading, and the reference commits on too few of their rows (23-91)
for the conditioned one to judge them; decision 14 carries them to 2.5 and 2.7.

**What each rule took**, over the 3,778 rows that were at assigned tier before
the pass and below it after. The reference confirms 38 of them, 1.0%; a row two
rules name is counted under both:

| rule | rows taken | the reference confirms |
|---|---|---|
| candidate density | 2,636 | 32 (1.2%) |
| odd-electron neutral | 1,782 | **0** |
| oxygen lattice | 212 | 3 (1.4%) |
| envelope neighbour | 166 | 4 (2.4%) |
| carbon-free off the allowlist | 0 | - |

**The largest rule is not in the plan, and the gate is what found it.** A
committed neutral that breaks the nitrogen rule names a radical rather than a
molecule. Of the 1,794 assigned-tier rows whose neutral is odd-electron, across
all eight sets, the reference confirms none, and the relation holds in both
directions: on the 954 peaks both engines commit where this engine's formula is
odd-electron, the 462 where the reference's is and the 151 where both are, the two
agree on the formula zero times. No corroboration rescues one, so the rule has no
corroboration escape. Decision 9 already elects the closed-shell reading when two
readings make one ion; this says the same of a reading with no rival, that a
tie-break is not evidence. A curated row is exempt, because what the rule doubts
is the finder's election and a curated row was matched to an identity somebody
authored - radical anions such as dibromide, carbonate and trisulfur are exactly
what such a library holds. The exemption spares 12 assigned rows, none of which
the reference confirms either, so it is taken on the principle rather than on
the count.

**A curated row is not asked the formula-shape questions either.** The
implausibility signatures name what a mass search produces, and their module
says a caller holding a curated row should not ask; as 2.4b first merged, the
pass asked anyway. On `639e1df` the carbon-free signature named eight rows, every
one of them curated, and took seven: trisulfur (written HS3) on all six C2
samples and the bromine of the dibromide reagent ion on one F1 sample. The
reference confirms none of them - it commits nothing on the trisulfur peaks and
reads the dibromide one as its reagent. The oxygen lattice named six curated
peroxyacetyl nitrate rows on F2, a species these sources are built to see, which
their evidence had already put below assigned. Both signatures now exempt a
curated row, for the radical rule's reason, and the rule set is at version 2.
Re-run on `step-2.4d-curated-implausibility-2026.09.11-2012665`, that moves 15
rows and nothing else: the seven monoisotopic rows and the eight isotopologues
that followed them go back to assigned, with no formula, role or verdict shift,
and G2 and the conditioned G1 identical on all eight sets. The seven now stand
on `no_close_rival`. What it costs is C2's unconditioned G1, 23.1 -> 24.6,
because the reference is silent on those six peaks. The bromine atom stays off
the carbon-free allowlist: no untargeted grid can write it, an untargeted one is
a radical the odd-electron rule caps, and that a reagent ion reaches a curated
analyte row at all is 2.5's curated-library question.

**Candidate density escapes only on a second channel.** The isotopologue flag is
among the corroboration the pass reads, and it is not an escape: of the 2,636
rows the density rule takes, 54 own a committed isotopologue row, and the
reference confirms 6 of those and contradicts 13. A confirmed envelope says the
ion's formula has the right shape, which every rival in the tie shares closely
enough to be in it. The density counts distinct ions: the finder collapses a
same-ion family into one hypothesis before it scores (step 1.3), and its members
share one envelope exactly, so counting them would make every family a tie. Step
2.3's suggestion to count them is declined for that reason; the nitrogen they
disagree about is what the reagent-N rule already caps.

**Decision 11's rider needed a height test, and an owner the run stands behind.**
Taken on the 13C spacing alone it flags 342 of B's rows on the 2.3 ledgers and
the reference confirms 203 of them - a dense spectrum puts some committed peak
one spacing above another most of the time. Requiring the predicted line to
account for the peak outright, no more than twice its predicted height, is what
makes it a rule. The envelope has to be predicted in the pass rather than read
off the ledger, because a predicted line landing on a peak another reading had
claimed never became an isotopologue row. The line's height comes from the
neighbour's formula, so a neighbour below assignability - a formula the run
itself does not believe - no longer predicts one: on the 2.4a ledgers those
owners took 52 of the rule's 218 rows and were wrong on 4, and on this build the
rule takes exactly those 52 fewer, with 4 confirmed where it had 8.

**The rider's Verify line, as first written, is missed, and most of what is
left is the grid gap.** Before the pass 159 assigned rows sat on a peak the reference reads as an
isotopologue (G6 at assigned tier); the pass takes 88 of them and leaves 71 - A 8,
B 46, C 9, C2 1, D 4, E 2, F2 1. 47 of the 71 are decision 11's other part: the
reference's parent carries silicon (39), phosphorus (7) or phosphorus and chlorine
(1), which no set's grid builds, so no committed neighbour exists to predict the
line, and they are 2.5b's. The envelope part is the other 24, and the rule's own
test, run over them offline with each run's tolerance and floor, finds none it
should have taken: on 16 (15 of them on B) the peak is more than twice the height
the reference's parent predicts for that line - two to seven times on 14, 36 and
305 times on the other two - so it holds more than the line and the height test
spares it on purpose; on 7 this engine commits another reading on the parent's
peak, so the envelope does not exist in the run; on 1 the line falls outside the
matcher's window. Meeting the line would mean dropping the height test that makes
the rule pay, or predicting from readings the run did not commit. Step 2.4c
re-worded it to the height-qualified form, which is the rule as measured: none
of the 24 is a row it should have taken, so that form is met, and the 24 are
reported beside G6 rather than gated.

**An isotopologue row follows its owner down whichever pass capped the owner.**
The run counts the two cases apart: 151 isotopologue rows went with an owner
this pass capped (A 4, B 8, C 25, C2 2, D 52, E 10, F1 41, F2 9), and 0 on any
of the 43 runs with an owner an earlier pass capped - the mass gate and the
reagent-N rule cap the isotopologues of the rows they cap themselves, and the
minor-channel cap cannot reach an owner that has one. So it is the rule stated
once rather than a new demote. It does not answer the question step 2.2 left
open, which is the opposite case: an isotopologue the tracking rule reads as a
coincidence while its owner stands, as 40 of that step's 46 did. This pass reads
owners and not the lines' own mass, so it neither confirms those caps nor
reverses them, and that question stays open.

**Two costs are recorded rather than claimed as wins.** On F1 the pass takes 1,777
of 2,299 assigned rows and on F2 1,134 of 1,783, sets where the reference is
nearly silent, so no gate number judges the exchange - the same shape of cost
step 2.3 recorded on F2. That step asked 2.4 to weigh the reagent-N rule's F2
cost against the nitrate mode's own prior; this pass does not, since weighing
it needs a reference that commits on F2, and decision 14 carries it to 2.5 with
the rest of the TOF sets. Nor does it demote the reference's
`relabel_reagent_n_adducts` readings, which step 2.3 offered as a plausibility
demote: decision 14 shows those splits beside G1 as a convention rather than
counting them as wrong, and a demote would be deciding the convention. The pass
itself costs about 0.15 s on the largest sample, against ten to forty seconds
for the assignment it judges.

**Step 2.4c puts the reasons in front of the reader.** The peak inspector lists
every reason the focused row carries under *Why this tier*: the rule by name,
the run's own sentence beneath it, and a mark on each reason that holds the tier
down. That mark's hover text is read off the tier the row actually holds, because
`caps` records what a rule would take: one that fires on a row its evidence
already banded lower took nothing, and says so. An isotopologue carries only
`inherited_from_owner`, so the card fetches its owner's detail and shows the
owner's reasons beneath, marked as borrowed, in the grammar the corroboration
badge already uses. A row no pass judged - a run from before it, an imported
run, a row assigned by hand, whose provenance curation rebuilds - shows no list
rather than an empty one, and neither does a row served from the batch ledger,
whose tier is a vote across samples that no rule judged. It is display and
documentation only: no run, ledger or gate number changes.

One name was renamed apart from this step, in #2107. Step 2.3's run record
counted the isotopologue rows its reagent-N rule capped as
`config.cross_channel.capped_satellites`, a word this codebase keeps for signal
artifacts - an FT side lobe - and never for an isotopologue; it is now
`capped_isotopologues`, the name 2.4b gave its own counts. Runs written before
that keep the old key and are not translated: nothing in Mascope reads the count
back from a stored run, so a reader of stored run configs accepts either name.

### After step 2.5a, the reference seed (2026-09-11)

Step 2.5a ships the seed's lists and the tooling that loads them:
- 11 schema 2 list files, holding 1,008 formulas
- the `peaklist` adapter
- `mascope reference seed`, and its counterpart inside the backend container
- the demo hook

A default seed loads 10 of the lists, 751 compounds; the RO2 list stays
opt-in. The store round ran in four parts, because the first seed round found
defects in Stage A that are older than the seed.

**The control reproduces the 2.4d round exactly.** It was re-run without the
seed on `step-2.5a-reference-seed-2026.09.11-bf7a33e`, and all 43 samples match
2.4d's ledgers: 0 formula and 0 tier changes on every M0 in all eight sets, and
0 owner changes over 48,894 peaks.

**The first seed round found three Stage A defects that the seed only made
visible.** They all showed with the seed loaded on the same build:

- **An insert crash.** At low resolution one line merges many isotopologues,
  and the generator names all of them in the row's `isotope_formula`. That
  column holds 256 characters, while the target column it is copied from holds
  4,096. Three of the six F1 samples failed on insert. #2111 fixes it with
  `fit_isotope_formula`, which keeps whole names from the front.
- **A labelled ion counted from the wrong line.** The M0 rule took the
  isotopologue formula without a bracket as the monoisotopic line. A labelled
  reagent's atom is bracketed too, so a 15N-nitrate ion's M0 was its unlabelled
  remainder: 2% of the labelled line, one mass unit below it. The ion's own
  line was written as its "M+1", on 115 rows on C and 93 on C2. The target
  library already showed the defect without the seed, on 5 rows and 12.
- **Isotopologues with no owner.** Stage A wrote an isotopologue whose ion had
  not won its M0 peak, with a null owner, where the untargeted stage refuses
  such a row. With the seed there were 534 of them across the gate, against 31
  in the control. 214 of them were on F1, mostly the 81Br line of a bromide
  adduct.

#2112 fixes the labelled M0 and the ownerless rows together. It was measured on
its own, with the seed removed from the store.

The composition search's hand assignment kept its own copy of the old M0 rule,
so a labelled hit committed from the pane was labelled from the remainder too.
#2122 carries the fix there - the pane reads the labels off the ion formula
the hit carries, as `monoisotopic_row` does - and adds the labelled case to
the user page's M0 definition. Frontend only, no round; the store held no
hand-assigned row to repair. The isotopologue tables labelled a row from its
formula's brackets (`formatIsotopeFormula`), so on the 15N sets a labelled
ion's M0 read "[15N]" and its remainder "M0". #2126 labels a line by how it
differs from the M0 instead, with the ion formula at each of the five call
sites, and reads a line written in the ion's own caret notation - how an
imported run spells a labelled ion's M0 - as the labelled line. Frontend only,
no round. Still open: the match tab opens on the lightest isotope, which for
a labelled ion is the remainder; a separate change.

**#2112 alone moves 40 of 48,894 peaks, and none of them away from an answer
the reference confirms.** A, B, D and E are identical row for row.

- On C, the five labelled main lines become M0 rows, and the reference
  confirms all five. G2 same-formula goes 90.6 -> 91.7 and G1 21.0 -> 20.8.
- On C2, twelve labelled reagent-cluster ions become M0 rows that the
  reference does not judge (hydrogen carbonate and hydrogen bromide with
  15N-nitrate). Hydrogen bromide is carbon-free, so C2's carbon-free count
  goes 12 -> 18; the six new ones are that curated row, one per sample.
- Ownerless database isotopologues go to 0 on every set (before: C 5, C2 20,
  F1 6). The eight that had no owner at all are no longer written, and their
  peaks are unassigned.
- The remaining moves come through the mass fit. Stage A's matched lines are
  the anchors a sample's mass width is fitted from, and Stage B is scored at
  that width. So a change in which ions reach Stage A moves Stage B elections,
  even where no Stage A row changes:
  - One election on C2 moved, through a calibration that shifted by 0.05 ppm.
  - 14 low-intensity elections moved on one F2 sample. F2's unlabelled nitrate
    mode still has labelled reagent compounds in its target library. The old
    rule dropped them by accident, because their unlabelled line is the
    reagent peak. On that sample the 15N line of the nitric acid-nitrate
    cluster is unclaimed, so one more genuine line joins the fit (25 -> 26
    anchors, 1.64 -> 1.90 ppm). None of those compounds is committed on the
    gate in either round. They are the workspace library's to clean before
    2.5b gives each source its own window.

**The seed on top of #2112 raises G2 same-formula on every set.** This was
measured on `step-2.5a-reference-seed-2026.09.11-77f3850` against #2112's round,
and all 43 runs complete. The first seed round had lowered C and C2, to 73.9 and
59.8; now C2 clears its stage-1 bound.

| set | G2 same formula | G1 unconditioned | assigned M0 rows | seed rows among them |
|---|---|---|---|---|
| A | 93.8 -> 95.4 | 23.0 -> 23.9 | 967 -> 1,010 | 191 |
| B | 92.8 -> 93.5 | 36.9 -> 38.3 | 4,229 -> 4,182 | 490 |
| C | 91.7 -> 93.9 | 20.8 -> 19.9 | 590 -> 594 | 227 |
| C2 | 77.7 -> 83.3 | 24.8 -> 22.8 | 306 -> 311 | 112 |
| D | 80.4 -> 84.0 | 8.4 -> 8.0 | 869 -> 930 | 458 |
| E | 33.3 -> 40.5 | 96.2 -> 94.7 | 239 -> 244 | 87 |
| F1 | 13.0 -> 22.8 | 96.7 -> 95.0 | 522 -> 764 | 464 |
| F2 | 15.9 -> 17.4 | 96.9 -> 98.0 | 642 -> 904 | 673 |

**The seed takes few confirmed answers, and most of those it takes are one ion
read two ways.** The owner changed on 10,959 peaks: 211 towards the reference's
formula, and 53 away from a formula the reference confirms.

- **23 of the 53 have a seed row on the peak.** Against them, 101 seed rows
  moved a peak towards the reference's answer, and 80 assigned a peak that
  nothing held before.
- **9 of those 23 are on B**, where a seed formula carrying nitrogen, read
  through `+H+`, is the same ion as the reference's ammonium adduct of the
  nitrogen-free neutral. The formulas are DMF and NMP from Keller 2008 and
  organic nitrates from Kang 2021. The untargeted stage elects the adduct
  reading by step 1.3's same-ion policy, and the reagent-N rule records the
  ambiguity. A curated row goes through neither, so it wins. Whether a curated
  row should carry that ambiguity is step 2.5c's.
- **Of the other 14**, one is at assigned tier, three at candidate and ten
  below assignability.
- **The remaining 30 of the 53** have no seed row on the peak, and move through
  the mass fit.
- **Against the first seed round:** it had 185 steals, 139 of them on C and C2.
  C and C2 now have 4.

**On TOF the seed widens the fitted mass width.** With the seed loaded, most of
a sample's Stage A anchors are the seed's lines: the median goes 7 -> 122 on E,
21 -> 332 on F1 and 24 -> 391 on F2.

- On the Orbitrap sets the 3 ppm gate keeps those matches accurate, and the
  width barely moves: A 0.59 -> 0.60, B 0.55 -> 0.66, C 0.58 -> 0.54 ppm.
- On the TOF sets the 10 ppm gate admits many coincidental matches, and the
  width more than doubles: E 3.0 -> 8.2, F1 5.1 -> 8.2, F2 2.6 -> 6.5 ppm.
- The wider width is what moves thousands of untargeted elections on F1 and F2
  (2,882 and 1,712 formula changes). It also raises the committed mass error:
  MAD E 2.12 -> 2.69, F1 0.96 -> 1.23, F2 1.22 -> 1.44 ppm.
- The mass gate's width widens too, by another route. The gate fits its width
  over the run's corroborated monoisotopic commits, and a curated row counts as
  corroborated, so the seed's own rows anchor it: by median E 3.0 -> 8.2, F1
  5.2 -> 8.2, F2 2.6 -> 6.7 ppm.
- Keeping a reference mirror out of both widths takes two changes, and each
  alone leaves the gate about as wide as before. The gate judges at the wider
  of its own fit and the width the search scored at.
  - The first change fits Stage A's width, which both stages score at, over
    the target library's lines alone.
  - The second stops counting a mirror's curation as the gate's corroboration.
  - Estimated on these ledgers, the median gate width on E, F1 and F2 goes
    8.2, 8.2 and 6.7 ppm -> 3.3, 5.2 and 2.6 with both changes, against 3.0,
    5.2 and 2.6 before the seed. The first change alone gives 7.0, 7.3 and
    6.7; the second alone gives 8.2, 8.2 and 6.5.
  - Fitting over the lines that won their peak instead does not work on TOF.
    A chance line seldom has a Stage A rival for its peak, and over this
    round's winners the width is still 7.9, 7.5 and 6.9 ppm.
  - Both changes land in #2113, before 2.5b widens the window (*The reference
    seed out of the mass widths*).

**Verify.**
- **The integrity test** holds every shipped list.
- **The owner-change report** is above.
- **G3.** N >= 5 falls on A (1.2 -> 0.8, now inside its bound) and on B
  (2.8 -> 2.6). Carbon-free formulas rise where the seed's inorganics match:
  nitrous, nitric and pernitric acid, HO2, ammonia and sulfuric acid, all of
  them curated rows.
- **Radicals.** HO2 is the one radical the seed commits, as decision 15
  intended: 12 M0 rows at assigned tier across C2, D, E, F1 and F2.
- **Carried to 2.5b:** the D4/D5 siloxane peaks, triethyl phosphate, and G6.
  - Silicon, phosphorus, fluorine and iodine are outside Stage A's known window
    until 2.5b. So the siloxane, organophosphate, insecticide, PFCA and iodine
    lists match nothing yet.
  - G6 at assigned tier rises on A (8 -> 13), F1 (0 -> 4), C2 (1 -> 4) and B
    (46 -> 48), and falls by one or two on C, D and E. Eleven of the new rows
    are the seed's:
    - on A, one Kang formula, C12H20O13, on the 30Si line of D5 in all six
      samples, where the untargeted stage had already put the same formula;
    - on B, one more of the same formula;
    - on F1, three organic nitrates on lines the reference reads as 13C
      isotopologues of sulfur-bearing parents;
    - on F2, one row on a 37Cl line.

    The other seven are untargeted rows that reached assigned tier in the new
    round, five of them on the lines of silicon-bearing parents.

### The reference seed out of the mass widths (2026-09-14)

#2113 keeps a reference mirror out of both widths, before 2.5b widens the
window. It makes three changes:
- Stage A's fit, which Stage A and Stage B score at, runs over the target
  library's lines alone.
- Only the target library's rows count as curated for the mass gate (decision
  3's second addendum).
- The run-less ingest fold runs the gate too.

The store round ran twice, with the seed loaded and against the seed round's
ledgers. The first round, on `step-2.5a-seed-out-of-widths-2026.09.14-ca49959`,
found a fourth defect, older than the seed: Stage A's fallback width.

- **Where the target library fits no width, Stage A scored at a generic 2
  ppm.** The untargeted stage and the gate stand in the instrument class's
  width (0.3 ppm Orbitrap, 3.0 TOF). Stage A's own scoring passed no width,
  and `score_pattern_v2` took its generic fallback.
  - The seed had hidden this: with it loaded, every sample had enough lines
    to fit a width.
  - Once the seed's lines were out of the fit, D (two library lines a sample)
    and four of C's five samples fell back.
  - On D, 155 more of the seed's rows reached assigned tier and G1 rose from
    8.0 to 15.3%. On C, G1 rose from 19.9 to 22.1%.
  - The same PR now passes the class width to Stage A. A second round, on
    `...-2026.09.14-8c163be`, puts D back at 8.3% and C at 20.4%.
  - Every sample whose library fits a width is identical between the two
    rounds, and one peak on E changes owner.

The figures below compare the seed round with the second round.

**Both widths are back to the target library's own on every set, and the gate
measures a TOF run again.** Medians per sample, seed round -> #2113, with the
round before the seed in brackets:

| set | Stage A anchors | search width (ppm) | gate's own fit (ppm) | gate width (ppm) |
|---|---|---|---|---|
| A | 76.5 -> 14 [14] | 0.60 -> 0.59 [0.59] | 0.24 -> 0.11 [0.12] | 0.60 -> 0.59 [0.59] |
| B | 245 -> 13.5 [13.5] | 0.66 -> 0.55 [0.55] | 0.33 -> 0.27 [0.27] | 0.66 -> 0.55 [0.55] |
| C | 64 -> 6 [6] | 0.54 -> 0.58 [0.58] | 0.16 -> 0.10 [0.11] | 0.54 -> 0.58 [0.58] |
| C2 | 45.5 -> 11 [11] | 0.67 -> 0.74 [0.74] | 0.35 -> 0.15 [0.15] | 0.67 -> 0.74 [0.74] |
| D | 245.5 -> 2 [2] | 0.61 -> 0.58 [0.58] | 0.41 -> 0.40 [0.40] | 0.61 -> 0.58 [0.58] |
| E | 122 -> 7 [7] | 8.15 -> 3.04 [3.04] | 7.00 -> 3.19 [3.03] | 8.15 -> 3.19 [3.04] |
| F1 | 331.5 -> 20.5 [20.5] | 8.16 -> 5.11 [5.11] | 7.26 -> 4.58 [4.33] | 8.16 -> 5.38 [5.18] |
| F2 | 391 -> 24 [24] | 6.54 -> 2.62 [2.62] | 6.75 -> 1.79 [2.05] | 6.75 -> 2.62 [2.62] |

- The search width and Stage A's anchor count equal the pre-seed round's on
  every set, as the matcher's per-ion pairing predicts.
- The gate's own fit on the TOF sets is near the pre-seed one but not equal.
  It rests on slightly fewer anchors: E 51, F1 72 and F2 31, against 52, 77
  and 38 before the seed.
- The gate caps rows on TOF again: E 0 -> 3 and F2 0 -> 7, against 2 and 9
  before the seed. Where its width narrows with the search width it caps more
  (B 12 -> 20, D 3 -> 5), and on C, where the width widens, one fewer (6 -> 5).

**Every peak it moves goes back to its pre-seed owner.** The owner changed on
3,883 of 48,894 peaks:

| set | changed | towards | away | neither | reference silent |
|---|---|---|---|---|---|
| A | 15 | 0 | 1 | 1 | 13 |
| B | 204 | 16 | 4 | 54 | 130 |
| C | 2 | 0 | 0 | 0 | 2 |
| C2 | 22 | 0 | 3 | 4 | 15 |
| D | 134 | 4 | 7 | 17 | 106 |
| E | 349 | 2 | 0 | 16 | 331 |
| F1 | 2,317 | 4 | 3 | 172 | 2,138 |
| F2 | 840 | 4 | 5 | 91 | 740 |

- The seed round had moved every one of them.
- 3,866 now hold the owner they had before the seed, including all 30
  towards and all 23 away. The 23 are elections the inflated width had
  turned to the reference's formula, and they are back at their pre-seed
  tiers: 20 candidate, 2 assigned and 1 below assignability.
- Nearly all of the change is untargeted elections on the TOF sets: F1's
  2,317, F2's 840 and E's 349.

**G2 same formula rises on the TOF sets and holds on the Orbitrap sets.**

| set | G2 same formula | G1 unconditioned | assigned M0 rows | mass error MAD (ppm) |
|---|---|---|---|---|
| A | 95.4 | 23.9 -> 24.7 | 1,010 -> 1,016 | 0.218 |
| B | 93.5 | 38.3 -> 36.9 | 4,182 -> 4,126 | 0.299 -> 0.305 |
| C | 93.9 | 19.9 -> 20.4 | 594 -> 598 | 0.167 -> 0.168 |
| C2 | 83.3 -> 82.6 | 22.8 -> 26.3 | 311 -> 297 | 0.249 -> 0.265 |
| D | 84.0 -> 83.9 | 8.0 -> 8.3 | 930 -> 895 | 0.319 -> 0.321 |
| E | 40.5 -> 45.2 | 94.7 -> 93.9 | 244 -> 228 | 2.693 -> 2.356 |
| F1 | 22.8 -> 23.6 | 95.0 -> 95.2 | 764 -> 729 | 1.232 -> 1.097 |
| F2 | 17.4 -> 18.8 | 98.0 -> 97.8 | 904 -> 764 | 1.437 -> 1.475 |

- **C2.** The width returns to its library's own 0.74 ppm, and G2 stays inside
  its bound. G1 rises because ten more target-library rows reach assigned
  tier, all unconfirmed:
  - Br- on five samples, which the reference reads as its reagent;
  - C3H5O3- on four and S3- on one, which it leaves unassigned.
- **The committed mass error** stays above the pre-seed values (E 2.115,
  F1 0.961, F2 1.215). The seed's rows are still committed, since #2113
  changes the widths and the tiers but not which peaks Stage A wins.

**The 30 mirror rows the estimate named: four are capped by the gate, and
the narrower width already holds the other 26 below the cap.**
- Every one is still a mirror row on the same peak, and none is at assigned
  tier.
- The gate caps four to candidate: an isotopologue on B, two on D, and the
  M0 of C10H14O5 on F2 (C10H13O5-, 12.2 ppm off).
- Of the other 26, 23 are below assignability on their own evidence (E 1,
  F2 22) and 3 are at candidate (F2), where the cap would leave them.
- Across the whole gate, the gate caps no other mirror row.

**Still open: the seeded re-score's fallback.** `score_seeds` measures a list
of formulas against a sample's peaks. It serves four readers:
- the readings Stage B commits;
- the batch ledger's identities on the samples holding them;
- the inspector's alternatives;
- a derived row's evidence.

It fits its width over its own seeded frame and passes no class width. A frame
too small to fit one is still scored at `score_pattern_v2`'s generic 2 ppm, the
defect #2113 closed for Stage A. A run's frame usually holds hundreds of lines.
The inspector's alternatives and a derived row's evidence measure a handful of
formulas, which is where the generic width is reached. Changing it is its own
measurement.

### After step 2.5b, each source inside its own window (2026-09-14)

#2117, on #2116, makes Stage A read what each source's row records (its
window under the context's ceiling, its radical allowance and its polarity) and
lifts the step's lists. The store round ran twice, after the migration and a
`reference_seed`:
- **The first round**, on `step-2.5b-stage-a-window-2026.09.14-af9d90d`,
  refreshed the rows of the nine loaded lists whose version held and loaded the
  isoprene list, the linear siloxanes and the cyclic list's new version with D7
  and D8 (12 lists, 783 species). Its polarity gate took the monoterpene HOM
  list off the uronium sets, which decision 17's addendum answered.
- **The second round**, on `...-8917b25`, carries the HOM list recorded as
  both polarities; the seed's refresh wrote that one row. Only A and B move
  between the two rounds (91 and 488 peaks); the other six sets are identical
  peak for peak.

The figures below compare the #2113 round (`8c163be`) with the second round.

**The known set follows the sample's polarity.** Every sample used to match the
same 665 formulas inside the one window. Now a positive sample matches 692 and a
negative sample 704 (132 on a positive sample in the first round, without the
HOM list). The median run time is 19.3 s a sample on B against 13.9, and within
1.2 s of the #2113 round elsewhere.

**G2 same formula rises on every set, and G6 on A falls 87 -> 50 with its grid
part 69 -> 5.**

| set | G2 same formula | G2 same ion | G1 unconditioned | assigned M0 rows | G6 (assigned) | mass error MAD (ppm) |
|---|---|---|---|---|---|---|
| A | 95.4 -> 97.1 | 96.3 -> 98.0 | 24.7 -> 24.3 | 1,016 -> 1,036 | 87 (13) -> 50 (2) | 0.218 -> 0.203 |
| B | 93.5 -> 95.1 | 94.8 -> 96.4 | 36.9 -> 36.8 | 4,126 -> 4,132 | 309 (47) -> 245 (38) | 0.305 -> 0.301 |
| C | 93.9 -> 94.8 | 93.9 -> 94.8 | 20.4 -> 20.3 | 598 -> 602 | 53 (7) | 0.168 |
| C2 | 82.6 -> 87.1 | 82.6 -> 87.1 | 26.3 -> 26.6 | 297 -> 312 | 18 (1) | 0.265 -> 0.268 |
| D | 83.9 -> 85.5 | 85.5 -> 87.1 | 8.3 -> 8.1 | 895 -> 897 | 168 (2) | 0.321 -> 0.319 |
| E | 45.2 -> 54.8 | 54.8 -> 64.3 | 93.9 -> 93.3 | 228 -> 225 | 12 (3) | 2.356 -> 2.391 |
| F1 | 23.6 -> 27.6 | 25.2 -> 29.3 | 95.2 -> 94.9 | 729 -> 752 | 100 (4) -> 101 (4) | 1.097 -> 1.107 |
| F2 | 18.8 -> 26.1 | 18.8 -> 26.1 | 97.8 -> 97.5 | 764 -> 772 | 31 (1) -> 32 (1) | 1.475 -> 1.479 |

**The Verify's compounds resolve on every sample the reference commits them
on,** where none did before. On those peaks the tier each takes is the
evidence's own reading: every tier reason on the rows is `corroborated` or
`no_close_rival` with `caps: false`, so no rule holds one down. Tiers on the
reference's peaks:

| set | compound | reference's M0 peaks resolved | assigned | candidate | below assignability |
|---|---|---|---|---|---|
| A | D3 | 9 of 9 | 7 | 2 | 0 |
| A | D4 | 3 of 3 | 0 | 3 | 0 |
| A | D5 | 6 of 6 | 0 | 5 | 1 |
| A | D6 | 1 of 1 | 0 | 1 | 0 |
| A | D7 | 1 of 1 | 0 | 1 | 0 |
| A | triethyl phosphate | 12 of 12 | 12 | 0 | 0 |
| B | D3 | 6 of 6 | 3 | 3 | 0 |
| B | D4 | 1 of 1 | 0 | 1 | 0 |
| B | D7 | 4 of 4 | 0 | 4 | 0 |
| B | D8 | 4 of 4 | 0 | 2 | 2 |
| B | triethyl phosphate | 12 of 12 | 11 | 1 | 0 |
| B | tributyl phosphate | 8 of 8 | 4 | 0 | 4 |
| B | triphenylphosphine oxide | 12 of 12 | 3 | 3 | 6 |
| C2 | TFA | 12 of 12 | 7 | 5 | 0 |
| D | TFA | 12 of 12 | 9 | 2 | 1 |

- **Read by decision 18.** A silicon envelope at candidate is the tier saying
  what the evidence supports, and the siloxanes will mostly sit outside G1's
  assigned rows at the stage-2 gate; triethyl phosphate is the one family
  assigned throughout.
- **L5's** two reference peaks on B are its ammonium adduct, a channel Stage A
  does not search there.
- **The only capped rows** of these compounds are two L3 rows on B, on no
  reference peak: `envelope_neighbour` holds them below assignability, the peak
  being the 13C line of C9H14O6.
- **Beyond the reference:** triphenylphosphine oxide is also committed on all
  six A samples (5 at assigned tier), where the reference leaves the peak
  unassigned.

**Owner changes: 987 peaks, 125 towards the reference's formula and 9 away.**

| set | changed | towards | away | neither | reference silent |
|---|---|---|---|---|---|
| A | 156 | 32 | 0 | 12 | 112 |
| B | 306 | 48 | 5 | 36 | 217 |
| C | 5 | 4 | 0 | 0 | 1 |
| C2 | 30 | 12 | 0 | 13 | 5 |
| D | 118 | 13 | 0 | 36 | 69 |
| E | 82 | 4 | 0 | 4 | 74 |
| F1 | 166 | 5 | 0 | 8 | 153 |
| F2 | 124 | 7 | 4 | 4 | 109 |

- **Towards:** 124 of the 125 are a newly admitted formula taking the peak.
- **Away, 5 on B: isotopologue claims of the new siloxanes.** D5's M+1 (2) and
  M+3 (1) lines and L5's M+2 (2) take peaks the reference reads as other M0s.
- **Away, 4 on F2: isoprene nitrates.** C5H10N2O8 and C5H9NO5 take peaks the
  reference reads as C14H10OS.
- **The first round's polarity gate, for the record.** It took the HOM list
  off A and B, which had held 114 and 419 of its M0 rows, 17 and 120 of them
  confirmed by the reference. The untargeted stage elected the same formula on
  13 and 103 of those, and the gate cost 20 rows moving away, which is why
  decision 17's addendum records the list as both. In the second round the
  gate removes one row on B, a nitroaromatic.
- **HOM rows on isotopologue lines.** 15 of A's 50 G6 rows are HOM-list M0s
  on lines the reference gives to other parents (12 of them D5's and D3's),
  against 20 before 2.5b, and their tiers fell: 11 candidate, 3 below
  assignability and 1 assigned, where 12 of the 20 had been assigned.

**The isoprene lift moves rows into Stage A under the formula they had.** Its
19 new formulas own:
- C2: 18 Stage A rows, every one the formula the untargeted stage elected
  before; the reference confirms 13.
- D: 56, every one the elected formula; the reference confirms 31.
- E: 35, 31 of them the elected formula; F1: 86, 52; F2: 73, 40; C: 1.

**IBr lands on reagent peaks,** a G4 cost read at the gate (decision 17's
addendum). IBr2- is committed as an analyte on 8 peaks the reference reads as
reagent (D 1, E 3, F1 4), 5 of them at assigned tier. With ICl (D 2, F2 2) and
HOI on E (3), they are the round's new carbon-free rows.

**G6's residue, named.** The grid part - decision 11's rider reads it as the
rows whose reference parent nothing the engine searches could name - is every
parent outside the old window before 2.5b, and after it the parents no list
holds in the sample's polarity:

| set | grid part (assigned) | its residue parents |
|---|---|---|
| A | 69 (13) -> 5 (1) | C16H23N2O2PS2 (2), C4H15N3O3Si3, C13H32O9Si4, C9H18O9Si |
| B | 215 (28) -> 137 (19) | led by C4H12O2Si2 (10), C14H36O11Si5 (9), C15H44O8Si8 (8), C10H22O8Si3 (8), C7H12O3Si (8); only L7 (2 rows) is a methylsiloxane a cited list could hold |
| C | 37 (7) | 26 are the linear silanediols C6H20O4Si3 and C8H26O5Si4 |
| C2 | 18 (1) | all C6H20O4Si3 |
| D | 91 (0) | mostly chlorinated paraffins (C14H26Cl4, C14H24Cl6, ...), 6 C6H20O4Si3 |
| E | 5 (3) | organophosphorus and chlorinated formulas from the reference's own search |
| F1 | 60 (1) | the same kind |
| F2 | 25 (0) | the same kind |

On A the grid part is inside the target of 10. The linear silanediols, which
2.5a dropped for want of a citation, are most of C's and all of C2's.

**Verify, item by item.**
- **The integrity test** holds all 13 lists.
- **G3:** N >= 5 falls on A (0.7 -> 0.3) and B (2.6 -> 2.5, still missed);
  the carbon-free rows rise by IBr, ICl and HOI only.
- **G4:** the IBr2- peaks above.
- **G6 on A:** 50, with a grid part of 5.
- **The known set and the run time:** above.
- **Recorded:** under the identity context the ceiling's mass cap is the only
  bound pushed into the query, so a database mirror would stream whole and be
  decided row by row. No mirror is loaded anywhere yet; a source's own cap can
  join the query when one is.

### After step 2.5c, a mirror row's nitrogen count (2026-09-15)

#2118 gives a reference mirror's M0 row the readings of its ion that the
untargeted search would have held, and lets the reagent-N rule ask it what it
asks an election:
- **The family.** `heuristic_filter.propose_same_ion_readings` builds it for a
  `(formula, mechanism)` reading: through every other mechanism of the same
  charge, the neutral that makes the same ion formula, kept where the grid
  holds it (`grid.admits`) and the heuristic rules pass it. On three readings
  the test finds it equal to what `find_compositions`, the rules and
  `elect_same_ion_families` hold for that ion.
- **Where it is written.** `engine.record_mirror_same_ion_readings` stores it as
  `same_ion` alternatives ahead of the row's rivals, under the run's own box and
  filter. A target library row gets none.
- **The rule, from both sides.** An election is still asked only where its
  reading is the donor's, the preference for the heavier mechanism being the
  prior in doubt. A list can put the nitrogen on the analyte, so a mirror row
  read through a channel that donates none is asked too, where its ion reads
  through a donor as a neutral with less nitrogen. A second channel fixes the
  count from either side, and a target library row stays exempt.
- **Recorded.** `config.cross_channel` counts `ambiguous_nitrogen_mirror` and
  `capped_mirror`, and the tier reason names the side the doubt is from.

The radical rule and the formula-shape signatures still exempt every Stage A
row. The run-less ingest fold, which assigns a newly processed sample by Stage
A alone, runs the same check through the channels a run reads (#2118's second
commit), so the batch ledger does not hold a row at assigned where a run caps
it; a second channel there can only be another Stage A commit.

All 43 samples re-run on `step-2.5c-mirror-same-ion-2026.09.15-22b0807`,
compared with 2.5b's second round (`...-8917b25`); no list or seed changed. A
second round on `...-fcaa09d`, which adds the ingest commit and moves the run's
channel resolution and check into helpers the fold shares, is identical to
it: every row, every same-ion reading and every nitrogen record.

**It moves tiers and nothing else.** No formula, role, stage or owner changes on
any of the 48,894 peaks, and G2 and its denominator are identical on all eight
sets. No untargeted or target library row changes tier. Every move is a mirror
row from assigned to candidate: A 24 M0 rows and 5 isotopologues, B 67 and 17,
F2 76 and 3.

| set | mirror M0 rows with same-ion readings | the rule's reach (donor side / the other) | capped from assigned | G1 unconditioned | G1 conditioned (n) |
|---|---|---|---|---|---|
| A | 158 | 45 (19 / 26) | 24 | 24.3 -> 23.2 | 2.5 -> 2.5 (804 -> 797) |
| B | 440 | 143 (105 / 38) | 67 | 36.8 -> 37.0 | 11.6 -> 11.7 (2,953 -> 2,900) |
| C | 115 | 0 | 0 | 20.3 | 1.2 |
| C2 | 49 | 0 | 0 | 26.6 | 0.4 |
| D | 31 | 0 | 0 | 8.1 | 1.0 |
| E | 87 | 0 | 0 | 93.3 | 42.3 |
| F1 | 634 | 0 | 0 | 94.9 | 49.3 |
| F2 | 1,211 | 558 (542 / 16) | 76 | 97.5 -> 97.3 | 80.4 -> 77.6 (97 -> 85) |

- **Where the rule reads nothing.** C and C2's reagent is labelled and the
  bromide sets have no donor channel. Their readings are the splits those modes'
  elections record: carbonate against deprotonation, and bromide against it.
- **The estimate held.** It was made on 2.5b's ledgers and named the same reach:
  45, 143 and 558 rows, 24, 67 and 76 of them at assigned tier.
- **The runs' `capped_mirror` is 25, 73 and 95.** The difference from the tier
  moves is rows 2.5b's tiering pass capped after this pass. This pass now caps
  them first: `envelope_neighbour` A 14 -> 13, B 33 -> 27, F2 33 -> 31, and
  `candidate_density` F2 687 -> 670.

**The nine same-ion steals on B all carry the ambiguity.** Each holds its
ammonium reading: acrolein for DMF, C5H6O for NMP, and C9H12O5, C9H12O6 and
C10H14O7 for the Kang nitrates.
- **Two are capped.** C9H15NO6 and C10H17NO7 on one sample go from assigned to
  candidate.
- **Seven are fixed by a second channel.** The same sample commits the same
  neutral through the urea adduct: C9H15NO5 on four samples, NMP on two, DMF on
  one. The reference commits that urea reading itself on six of them while
  reading the protonated peak as the ammonium adduct, one neutral read two ways.
  The seven keep their evidence's tier, 4 assigned and 3 candidate.

**What the capped rows are, and what the reference says of them:**

| set | side | rows | formulas | the reference commits the same formula | another | the displaced reading | silent |
|---|---|---|---|---|---|---|---|
| A | donor (urea adduct, `+H+` reading) | 13 | HOMs and atmospheric organics 11, Keller 2 | 2 | 0 | 0 | 11 |
| A | plain (`+H+`, ammonium reading) | 11 | Keller's amines C6H15N and C8H19N | 5 | 0 | 0 | 6 |
| B | donor (urea adduct, `+H+` reading) | 43 | HOMs and atmospheric organics 42, Keller 1 | 32 | 1 | 0 | 10 |
| B | plain (`+H+`, ammonium or urea reading) | 24 | Keller's amines 22, Kang nitrates 2 | 18 | 0 | 2 | 4 |
| F2 | donor (`+NO3-`, `-H+` reading; one `+CO3-`) | 74 | Kang HOMs 63, atmospheric organics and isoprene 7, Keller 4 | 0 | 12 | 0 | 62 |
| F2 | plain (`-H+`, `+NO3-` reading) | 2 | Kang formulas with nitrogen | 0 | 0 | 0 | 2 |

- **The cost is on B.** The cap takes 50 rows the reference confirms, 43 of them
  at the reference's own assigned tier.
  - 32 are on the donor side, where the same formula elected through the urea
    adduct would be capped too.
  - 18 are Keller's amines through `+H+` (C8H19N, C9H21N, C7H10N2, C10H15N,
    C13H24N2O), whose other reading is an alkene or arene through ammonium, or
    through the urea adduct.
- **Read by decision 18,** the nitrogen count on those rows is the list's answer
  and not the spectrum's, so candidate is what the evidence supports. The plan
  owner's answer to the PR's question keeps it so: where the ion reads equally
  well another way, a reference list makes its formula the preferred reading
  and not an assigned one. The ingest fold was the PR's second question, and
  the answer was that it should apply the check too.
- **On F2** the reference commits none of the 76 and another formula on 12. A
  HOM through `+NO3-` reads through `-H+` as the nitrate ester one HNO3 heavier.
  One row is capped on a radical reading, the only one crossing the boundary,
  which the election's rule would do as well.

**Verify, item by item.**
- **The nine same-ion steals on B:** above.
- **The owner-change report:** empty.
- **G1 and G2 on B and on the nitrate sets:** the table. C and C2 do not move,
  and neither does G2 anywhere.

### After step 2.2b, a centre that follows m/z (2026-09-15)

#2131 lets a run's mass gate judge a row at its own m/z:
- **The line.** `mass_accuracy.fit_mass_trend` fits `ppm = a + b * 1000 / mz`,
  trimmed at three robust widths, and accepts it on peaky's rules
  (`masscal.fit_mass_trend`, peaky #30): twenty points, five kept in each half
  of the fitted range, the slope beyond three standard errors, the residual
  RMS at most 0.8 of the constant model's and `|b|` at most 0.5 mDa. The
  centre is held at the edges of the m/z the points covered. The port gives
  peaky main's answer on 3,000 random samples, 748 of them accepted.
- **Its rows.** Every committed monoisotopic row, by the plan owner's answer
  recorded in the step. The constant centre and the width stay the
  corroborated anchors'.
- **The gate.** `MassCalibration.z_of` measures from the line's centre at the
  row's m/z, in the run's one width floored by 0.03 mDa, where the run has a
  line; from the constant centre otherwise. A run with no line judges every
  row exactly as before.
- **Recorded.** `config.mass_calibration` carries the `centre` a run judged at
  (`trend`, `constant` or `none`), the `trend` and `trend_refused`, the rule
  that refused a line.

All 43 samples re-run on `step-2.2b-mass-dependent-centre-2026.09.15-0f2c9e7`,
compared with 2.5c's confirmation round (`...-fcaa09d`); no list or seed
changed.

**It moves no tier.** No tier, formula, role, owner or mass error changes on
any of the 48,894 peaks. The gate caps the same rows on every run, and G1, G1
conditioned and G2 with its denominator are identical on all eight sets. The
anchors' constant fit is unchanged on all 43 runs. What moves is `mass_z` on
the seven runs judged at a line, and the runs' record.

| set | runs judged at a line | offset (mDa) | m/z covered | the others refused as | rows whose `mass_z` moved |
|---|---|---|---|---|---|
| A | 6 of 6 | -0.113 to -0.137 | 57 to 446-505 | - | 2,316 of 2,344 |
| B | 0 of 6 | - | - | no better than constant 6 | 0 |
| C | 0 of 5 | - | - | no better 3, slope not significant 2 | 0 |
| C2 | 1 of 6 | -0.077 | 71-404 | no better 5 | 130 of 133 |
| D | 0 of 6 | - | - | slope not significant 5, no better 1 | 0 |
| E | 0 of 3 | - | - | no better 3 | 0 |
| F1 | 0 of 6 | - | - | no better 4, slope not significant 1, one-sided 1 | 0 |
| F2 | 0 of 5 | - | - | no better 3, slope not significant 1, one-sided 1 | 0 |

- **A's lines** were fitted over 329 to 358 commits and kept 284 to 323. They
  put the centre at -1.42 to -1.65 ppm at m/z 60, -0.64 to -0.74 at m/z 100,
  -0.10 to +0.01 at m/z 200 and +0.18 to +0.34 at m/z 400, where the constant
  centre sat at +0.02 to +0.06.
- **C2's one line** runs from -1.88 ppm at m/z 71 to -1.00 at m/z 400, about
  that sample's constant -1.13.
- **B keeps the constant centre.** On its four long-range samples the commits
  dip to -0.7 ppm at m/z 300-450 and come back to 0 above m/z 450, which no
  1/mz line describes.
- **The estimate held:** the same runs and the same offsets.

**Why no tier moves.** 37 rows cross three widths: 28 inward on A, 1 inward on
C2 and 8 outward on A. One isotopologue on A crosses six outward. Every one of
them is already below assignability on its own evidence, where the cap has
nothing to take. A's low-mass rows stay there because the fit scores them
before the gate sees them: of its 27 to 29 commits below m/z 104, 19 to 22 are
below assignability.

**Verify, item by item.**
- **Which runs accept a trend, and its `b` in mDa:** the table.
- **The 26 curated rows decision 3's addendum itemised.** Five are no longer on
  the ledgers.
  - A's seven come inside three widths: C3H6O `[M+H]+` at m/z 59 on all six
    samples goes from -3.77..-4.38 to -1.09..-1.62, and C3H9N at m/z 60 from
    -3.49 to -0.80.
  - The rest sit on runs that keep the constant centre and do not move: C's two
    C11H15O4 isotopologues (3.10, 3.43), F1's bromide adduct of C3H6O3
    (-3.40), and F2's nitrous acid, oxalic, malonic and nitrate cluster rows
    (-3.04 to 7.12).
- **The low-mass untargeted rows.** On A the median |z| of the untargeted M0
  rows below m/z 120 goes from 1.54 to 0.39, and those beyond three widths go
  from 11 to 5 of 206 (between m/z 120 and 200, 10 to 9 of 546). Coming inside:
  C4H9+ at m/z 57 on all six samples, C3H8N+ at m/z 58 on three and C4H8N+ at
  m/z 70 on two, all of which the reference is silent on, and a reference
  list's acetic acid at m/z 61 on three, a peak the reference calls an
  artifact.
- **The gate caps nothing below m/z 200 that it did not cap before:** it caps
  nothing new anywhere. Six rows below m/z 200 are newly beyond three widths:
  C9H11+ at m/z 119 on four samples, C5H10NO2+ at m/z 116 and C3H11N3O2+ at m/z
  121 on one each. They sit at +1.3 to +1.7 ppm where the line's centre is
  -0.5, so they are off the line too, and all six are below assignability with
  the reference silent.
- **G1 conditioned on the Orbitrap sets:** identical, A 2.5, B 11.7, C 1.2, C2
  0.4, D 1.0.

**The curation exemption, revisited on the evidence.** With the centre at A's
line, every target library row on A sits within three widths of it, so the
low-mass argument of decision 3's addendum no longer protects a row there.
Across the 43 runs, 15 target library rows sit beyond three widths, against
22 before, all on runs that keep the constant centre:
- **At assigned:** C's two C11H15O4 isotopologues (+2.07 and +1.65 ppm) and
  F2's C3H4O4 M+2 (+11.2 ppm), whose owners sit at 0.65, 0.83 and 0.0 widths.
- **At candidate:** three F2 isotopologues.
- **Below assignability:** F1's bromide adduct and F2's nitrous acid rows.

Lifting the exemption from the cap alone, the target library rows still
anchoring the fit, would move those three isotopologues to candidate and
nothing else. The plan owner kept it for now (decision 3's third addendum).

#### The nitrate monitor's workaround entries, fixed on the testbed (2026-09-15)

Reading those three rows led to the entry behind one of them. Set C's
strongest line, C11H14O4- at m/z 210.0898, was committed from the target
library as deprotonated C11H15O4, an odd-electron neutral. The reference reads
the same ion as C10H14O clustered with carbonate, and deprotonated C10H14O is
on all five samples. The entry came from the diagnostics collection shared by
the two 15N-nitrate modes. Those modes declared only the labelled nitrate and
deprotonation, and a match uses only the mode's own mechanisms, so an
odd-electron formula was the one way to reach the carbonate cluster's line.
Its neighbour in the collection, C10H15O, was the same kind of entry.

On the plan owner's word the testbed's data was changed, not the code:
- **The change.** Both modes declare `+CO3-`, and the collection holds
  C10H14O in place of C11H15O4, with C10H15O removed. The edits went through
  the app's own mode and collection updates, which flag the modes' batches
  for rematch. The internal server the testbed is cloned from is unchanged.
- **Set C, re-run on step 2.2b's build and read against its round.** 48 rows
  change, all on set C. The strongest line is C10H14O through carbonate on
  all five samples, assigned, and corroborated by the same neutral's
  deprotonated line, which is now a target library row too.
  - 612 rows are at assigned (602 before), and G1 holds at 20.3.
  - G1 conditioned falls from 1.2 to 0.2 (n 486 -> 489), and the same-ion
    splits on C go from 5 to 0.
  - G2 and its denominator are unchanged, and G6 goes from 53 to 55 (7 at
    assigned before and after).
- **Declaring carbonate reaches the reference lists too.** Stage A now
  matches their carbonate readings. Malonic acid's goes from an untargeted
  candidate to assigned on all five samples, where the reference commits the
  same reading. C10H16's carbonate cluster, an oxygen-free neutral, is
  assigned on one sample: one of step 2.4f's cases.
- **Removing C10H15O mattered.** While it stayed in the collection, its
  carbonate line landed on the strongest line's 2H isotopologue as an M0 on
  one sample and counted there as a curated anchor. That gave the sample's
  Stage A the eight anchors a fitted width needs, and the fitted width put the
  strongest line's fit below the assigned band. Removing the entry took both
  back.

The next step reads set C against this round, not against step 2.2b's.

### After step 2.5d, the curated lists reviewed (2026-09-15)

The gate batches carry no targets list. Every target library row on the 43
samples comes from the calibrant and diagnostic collections the ionization
modes attach: 12 collections holding 46 compounds once the nitrate monitor's
fix was in, or 72 pairs of a set and an entry. Each pair was read against the
latest round - set C on the second monitor re-run, every other set on step
2.2b's round - for the step's four flags.

#### What the audit found, and the plan owner's verdicts

| finding | entries (sets) | rows on the round before | verdict |
|---|---|---|---|
| an odd-electron formula written so a declared mechanism reaches the ion | HCO3 (C2) and CHO3 (F1) for CO3-, HS3 (C2) for S3-, Br (D, E, F1) for Br2- | 39 with their isotopologues: C2 27, of them HS3's 12 at assigned; F1 12, of them Br's 3 at assigned; none on D and E, where the reagent pre-pass claims Br2- | removed from the lists the gate batches carry |
| the entry's own line taken by a reference list's copy of the same neutral, spelled in Hill order | NH3 (A), CH3COOH (C2), H2SO4 (F2) | 27 committed as the copy, 12 of them capped by candidate density | fixed in the engine |
| never commits a row: a reagent line the pre-pass claims first | the empty neutral `()` (B, C2, D, E, F1, F2), urea (A, B), nitric acid and its dimers (C2, F2), Br2 (D, E, F1), water (F1, F2) | 0 | kept; they are calibration lines |
| never commits a row: absent from the samples or outside the mode's range | C's two high-m/z calibrants, A's C14H21NO3 and C9H12O, C2's PFHpA, 6:2 FTOH and MSA, D's C11H14O2 and nonanamide, E's nonanamide, F1's sulfuric acid, and the 15N-labelled nitric acid dimer on F2's unlabelled mode | 0 | kept |
| an ion another channel of the same sample reads as a different neutral | none left once C11H15O4 was replaced | - | - |

- **Two readings the reference makes differently are not that last flag.** It
  reads nonanamide's and caprolactam's protonated lines as ammonium clusters of
  C9H16O and C6H8O, through a channel the uronium modes do not declare, but
  both entries also commit their urea cluster, at assigned on every sample of
  A and B: the identity has its own second channel.
- **The `()` entries are the empty neutral.** Through each mechanism their ion
  is that mechanism's reagent ion - NO3-, Br-, protonated urea - which is what a
  calibrant list is for.
- **Rematch flags.** The 14 batches the monitor's fix flagged, and the batches
  this step's edits flagged, stay flagged: a rematch recomputes the stored
  matches the match tab shows, and assignment computes its own.
- **The lists on the internal server** are unchanged.

#### The workaround entries removed (build `...0f2c9e7`, no engine change)

- **Six sets do not move.** A, B, C, D, E and F2 are identical row for row; on
  D and E the reagent pre-pass already claimed Br2- before Stage A.
- **The entries' own rows** go to unassigned. The reference leaves the same
  peaks unassigned, and reads F1's Br2- lines as reagent.
- **C2's assigned rows fall from 312 to 181.** Of the 146 that leave the tier,
  the reference commits the same formula on 107, and on 106 of them once they
  had moved. Six of the 146 are HS3's own rows; the rest is Stage A. Four
  to five of each C2 sample's 11 to 12 matched library lines were the removed
  entries' ions, so every sample drops below the eight a fitted width and
  offset need. Stage A then scores the search at the instrument class's 0.583
  ppm and no offset, where it had fitted 0.59 to 0.79 ppm about -1.53 to -1.58
  ppm. The run's own calibration still sits at -1.10 to -1.15 ppm. This is the
  bias step 2.2 recorded: a flat -1.1 ppm on files whose stored calibration is
  marked verified, which C2's calibrants cannot fit.
- **F1 stays fitted** on 16 to 21 lines, but its widths and offsets move by up
  to 0.6 and 0.5 ppm, and 1,162 peaks change owner, 1,078 of them where the
  reference is silent. Assigned 752 -> 758, G1 94.9 -> 95.1.
- **Metrics:** G1 on C2 26.6 -> 30.9, conditioned 0.4 -> 0.0 (n 230 -> 125);
  G2 same formula on C2 87.1 -> 86.4; every other set unchanged.

#### For the plan owner: C2's offset

C2's scoring leaned on the workaround entries' lines for the offset its
stored calibration does not carry. Their ions were real lines at the right
mass; only the neutrals written for them were wrong. Two ways back, neither
in this step:
- **Recalibrate C2's files** through the calibration node, as step 2.2 did for
  E. Step 2.2 found C2's axis cannot be fitted until its mode has more than
  two disagreeing calibrants, so this starts with C2's calibrant list.
- **Anchor Stage A on the reagent pre-pass's lines** where the library matches
  fewer than eight. The pre-pass already measures an offset from the source's
  own reagent ions, and uses it only for its claim window. D and E score at
  the class width for the same reason; C, whose range starts above its
  reagent's lines, claims none.

#### The spelling fix (#2136, build `...e9eec1b`)

Stage A compared formulas as text, and dropped nothing a reference list
copied. Now a formula is compared by its composition, and a list's copy of a
reading the library makes on the same peak through the same mechanism is
dropped before the peak is arbitrated.
- **63 rows** go from a reference list's copy to the library entry making the
  same reading: A 6 (ammonia), C2 10 (acetic acid), F1 33 (lactic, nitric,
  formic and iodic acid), F2 14 (sulfuric and formic acid). On A and C2, and
  for F2's sulfuric acid, the spelling decided the tie. The other 36 are
  spelled alike: there the copy's isotopes, generated at run time, fitted up
  to 0.002 better than the library's stored ones.
- **12 density caps** come off (A 6, C2 6) with the 4 isotopologues that
  inherited them, and those rows stay below assignability on their evidence.
- **Nothing else moves**: no tier or owner on 48,894 peaks, G1, G1
  conditioned and G2 identical, Stage A identical on all 43 runs. The run
  calibration counts the new library rows as anchors (1 to 3 more on 19
  runs), which on F1 and F2 moves `mass_z` on 13,807 rows; the 45 that cross
  three widths are at candidate or below.

#### The cap on the target library's rows (build `...600e1c4`)

Only an isotopologue that tracks a row exempts it from the cap now, and a
library row is judged like any other against the calibration it anchors.
- **Three rows move, all from assigned to candidate**, and they are the three
  decision 3's third addendum left protected: C10H14O's M+1 and M+2 on two C
  samples, 3.5 and 3.1 widths off while their monoisotopic row sits inside
  one, and C3H4O4's M+2 on one F2 sample, 3.8 widths off on a line at 1e-5 to
  1e-4 of the base peak.
- **The gate's whole cap goes from 48 rows to 51** over the 43 runs. Nothing
  else moves: 0 owner or formula changes on 48,894 peaks, and G1, G1
  conditioned, G2 with its denominator and G6 identical on all eight sets.
- **No library row is left above the cap's tier on its curation alone.**
- **The record:** of the 492 library rows, 337 are corroborated by an
  isotopologue that tracks them and 155 by their curation; `capped_curated`
  counts the three the gate took.

#### Verify, item by item

- **Every flagged entry on the gate batches, with its verdict:** the audit's
  table.
- **The gate re-run on the reviewed lists and read against the round
  before:** three rounds, each against the one before it - the list edits on
  the merged engine, then #2136, then the cap.
- **The curated rows beyond three widths and the rows the exemption
  protects, re-counted:** 15 on step 2.2b's round and 15 once the workaround
  entries were gone; 16 once the spelling fix gave the library back its own
  rows. The exemption held 3 of them at assigned, and holds none: the gate
  caps those three at candidate.
- **Decision 3's exemption answered:** its fourth addendum.


### After step 2.5e, the reagent lines' offset (2026-09-16)

Build `step-2.5e-reagent-line-offset-2026.09.16-c08dfc6` (#2140), read
against step 2.5d's last round on all eight sets.

- **Only C2 reaches the fallback.** Its six samples score at -1.25 to -1.26
  ppm from seven lines each, where their calibration's offset stays at -1.10
  to -1.13. The other sets:
  - D reads its lines at 0.00 to +0.35 ppm over 9 to 11, and E at +0.19 to
    +0.71 over 16 on a 3.04 ppm width: both inside the guard.
  - C claims no line.
  - A (-0.90 to -0.96 over 10 lines), B (-0.25 and -0.31 over 9 on two
    samples, no line on four), F1 and F2 fit their own offset.
  - All seven are identical row for row.
- **C2's rows come back.** Of the 146 monoisotopic rows step 2.5d took from
  the assigned tier, 114 are assigned again. The reference commits the same
  formula on 100 of them, is silent on 12, and reads the other two as another
  formula and as an isotopologue. 25 are candidate, one is below
  assignability, and the six that were HS3's own stay unassigned.
- **C2's assigned rows go from 181 to 321**, where step 2.2b's round had 312.
  - 150 rows rise to the tier, 138 of them Stage A's, at a median error of
    -1.21 ppm.
  - Ten fall from it, and the reference is silent on eight of those. Six are
    reference-list rows at -0.28 to -0.46 ppm, which fitted well against a
    centre at zero and sit 0.8 to 1.0 ppm from the one they are scored at now.
- **The untargeted stage re-elects 48 peaks** at the new offset, and gains
  and loses one each. Against the reference, 3 move towards its reading, 1
  away (a candidate at m/z 403.084) and 14 neither; it is silent on the other
  32.
- **C2's metrics:**
  - G1 30.9 -> 20.6.
  - G1 conditioned 0.0 -> 0.8, over 257 rows instead of 125. The two it
    counts are one other formula and one same-ion split.
  - G2 same formula 86.4 -> 87.5, over 264.
  - Mass error MAD 0.265 -> 0.252.
  - G6 at assigned 4 -> 4.
  - The tiering rules cap 90 rows where they capped 81, 88 of them by the
    odd-electron rule, since more rows now reach a tier it lowers, and three
    by candidate density, which capped none before.
- **C2's calibration line moves, and its caps do not.** The gate's centre
  through m/z is fitted over every committed monoisotopic row, not over the
  anchors, and the 48 re-elected rows moved from a median error of +0.52 ppm
  to -1.46: scored at zero, they were elected near zero, and that was the
  line's lever.
  - Of the five samples that fitted a line on the cap round, three now refuse
    it as no better than a constant centre, and two fit a shallower one (-0.08
    and -0.09 mDa where they fitted -0.10 and -0.12). The offset and the width
    stay.
  - `mass_z` moves on 593 rows whose formula stayed, by less than half a width
    on 522. Twelve rows cross three widths and two cross six, all untargeted
    and at candidate or below.
  - The gate caps the same six rows at the same tiers. No assigned
    monoisotopic row sits beyond 1.9 widths, and no assigned isotopologue
    beyond 2.3.
  - For step 2.7a: with the offset scored, three of the five C2 samples whose
    commits demanded a 1/mz line no longer do, and the two that still do
    demand a shallower one.
- **Which lines the offset reads.** The anchors the pre-pass corrects its own
  claims by would have put C2 at +0.60 ppm (the step's as-built note), so the
  offset reads every line it claims.

#### Verify, item by item

- **Only a sample scoring at no offset may move:** only C2 moved; D, E and
  four of C's samples score at no offset and did not.
- **How many of the 146 rows come back, and what the reference says of
  them:** 114, with the reference's formula on 100.
- **A, B, C, D, E, F1 and F2 identical row for row:** they are, on 47,474
  peaks.
- **The run's own calibration and the gate's caps unchanged:** the offset, the
  width and the capped rows are unchanged on all 43 runs, since the anchors
  are the same corroborated commits. The line through m/z is not: on five of
  C2's samples it moved with the search's re-elections, which it is fitted
  over, and the caps did not move with it.
- **G1, G1 conditioned and G2 per set:** above for C2; the other seven are
  unchanged.

### After step 2.4e, isotopologue claims under interference (2026-09-16)

Build `step-2.4e-isotopologue-claims-2026.09.16-62fbc59` (#2143), read against
step 2.5e's round on all eight sets. The envelope-neighbour rule flags 861
monoisotopic rows on that round (862 on step 2.2b's, where the step counted
them).

What the 861 rows became, and what held the others back:

| set | claimed (tracks / in doubt) | neighbour below assigned | another channel | does not follow | target library |
|---|---|---|---|---|---|
| A | 30 (29 / 1) | 40 | - | - | - |
| B | 128 (93 / 35) | 156 | 9 | 11 | 1 |
| C | 16 (4 / 12) | 4 | - | - | - |
| C2 | 0 | 1 | - | - | - |
| D | 13 (9 / 4) | 86 | - | 2 | - |
| E | 12 (10 / 2) | 26 | - | - | - |
| F1 | 40 (32 / 8) | 142 | 3 | - | - |
| F2 | 13 (7 / 6) | 123 | 4 | - | - |

- **The rest of the 861.**
  - One more row on F1 is no longer flagged: its only neighbour was a claimed
    row.
  - The 252 claimed rows were 177 below assignability and 75 candidate as
    monoisotopic rows. Six of their own isotopologues went with them (C 3, D
    1, F1 2), and none left the ledger.
- **What the reference read on the claimed peaks.**
  - An isotopologue of the formula now owning them: 67. Of another formula:
    18.
  - An M0 of another formula: 10. Silent: 151.
  - The M0 the claim displaced, with the same formula: 6, all on B and all
    candidate before and after. Five are 2H lines beside a 13C line: C10H19N3
    at m/z 199.19 on three samples (C12H20O through ammonium), C8H10N2 at
    152.12 (C10H14O) and C11H21NO2 at 260.20 (C14H26O4). The sixth is
    C18H22O14 at m/z 523.14, the 18O line of C17H28O18.
- **What the holds kept.** The reference reads 4 of the 13 rows whose error
  does not follow their neighbour's as that neighbour's lines, and one as the
  M0 this engine commits. It reads 7 of the 16 held for another channel as
  the neighbour's lines. One of those 16 is a reference-list PFHxA row on F1,
  which it commits as the M0 it is and a claim would have read as a bromide's
  81Br line. The target library row held back is one it commits.
- **The metrics.**
  - G6 (monoisotopic rows on reference isotopologues) 681 -> 596: A 50 -> 27,
    B 245 -> 210, C 55 -> 46, D 168 -> 156, E 12 -> 9, F1 101 -> 98, C2 and
    F2 unchanged. At assigned it stays at 61.
  - No monoisotopic row changes formula or tier on any set, so G1, G1
    conditioned and G2, same formula and same ion, are identical everywhere.
    Where both engines commit an M0, B's same-formula count falls by the six
    above, and the other-formula count by 1 on A, 3 on B, 5 on F1 and 1 on F2.
  - Mass error MAD falls slightly on the seven sets with claims, by 0.002 to
    0.010 ppm.
- **Set C's strongest line, C10H14O through carbonate.**
  - Its 18O line, committed as C9H13N2 through the labelled nitrate on four
    samples, is its isotopologue on all four, in doubt, at candidate. The
    reference reads it so on all four.
  - Its 2H line, committed as C8H13N3 through carbonate on two samples, is
    claimed on both.
  - Its 13C2 line is held in doubt at candidate on the four samples that
    commit it. Three had it at assigned, and the fourth had it capped off
    calibration.
  - Its 13C+18O line, which C9H13N2 carried as its own 13C line, went with the
    claim on three samples.
  - On the fifth sample, where the 18O line was already this ion's
    isotopologue, that line is held in doubt as well. Every one of these lines
    is now candidate.
- **The gate's holds, re-counted by what the lines are.**
  - Over the 43 runs, 4,867 isotopologues track, 485 are in doubt and 278 do
    not track, claims included.
  - The gate holds 127 rows where it capped 51. The distance cap takes 30:
    the same six monoisotopic rows, and 24 isotopologues. Of the 45
    isotopologues it took on the round before, 21 are now in doubt.
  - 85 rows are held in doubt. The reference reads 22 of them as the same
    formula's isotopologues and is silent on 51.
  - 12 rows are held for not tracking. The reference reads 6 as the same
    formula's isotopologues and is silent on 4.
  - 69 isotopologues go from assigned to candidate, 60 in doubt and 9 not
    tracking, and 25 of them are lines the reference reads under the same
    formula: A 5, B 5, C 4, D 11. Four in-doubt lines the distance cap had
    put below assignability are at candidate now (B 2, C2 1, D 1).
  - What puts a line in doubt, claims apart: on the Orbitrap sets its noise
    alone on 86, a close peak alone on 18, both on 110. On the TOF sets it is
    14, 22 and 165, since nearly every TOF line is faint and crowded both.
- **The runs' own records.** The full `mass_calibration` record moves on 26
  of the 43 runs.
  - Offset, width and anchors are unchanged on 40. On three samples a claimed
    row had been an anchor, since its own isotopologue tracked it, and the
    refit moves them:
    - C: -0.160 -> -0.166 ppm, 28 -> 27 anchors;
    - D: -0.124 -> -0.122 ppm, 195 -> 194 anchors;
    - F1: +1.56 -> +1.46 ppm and 4.47 -> 4.26 wide, 61 -> 60 anchors.
  - The line through m/z is accepted or refused as before on every run. On
    A, the claimed rows leave the rows it is fitted over, and the offset
    moves by at most 0.0024 mDa.
  - `mass_z` moves on 2,314 monoisotopic rows of those nine samples. Six cross
    three widths, all already below assignability. No monoisotopic cap or
    corroboration changes on any of the 32,580 rows, and no assigned
    monoisotopic row sits beyond 2.8 widths.
  - `corroborated` rises by 12. The 18 claimed lines of target library rows
    count as curated, as every line of a library row does. The three former
    anchors and their three lines no longer count.
  - `capped` falls from 51 to 30, since the lines in doubt have their own
    count.
- **The runs themselves.**
  - 36 runs took two rounds and 7 took one. No chain needed a third, and no
    claim was left unapplied.
  - The tiering pass caps 3,250 rows where it capped 3,271. As monoisotopic
    rows, the claimed rows had been capped by the envelope rule 21 times, by
    candidate density 10 and as radicals 6.
  - Run time over the 43: 862 s -> 774 s, within the testbed's spread.
- **For the plan owner.**
  - On the TOF sets, the widened bar is wide: a line near a signal-to-noise
    of 2 or 3 on a peak 150 ppm wide. So most TOF lines that miss their
    parent are in doubt rather than coincidences, and the reference commits
    too little there to say whether that is right. Step 2.7a's refreshed
    reference is where that is read.
  - The height rule has two tails the reference speaks to. The rule claims a
    line however much shorter than its prediction it is, and up to twice as
    tall. On the 252 claimed rows the reference confirms 16% of the 114 below
    0.6 of the predicted height, 51% of the 82 within 0.6 to 1.4, and 12% of
    the 56 between 1.4 and 2.0.

#### Verify, item by item

- **The rows by what they become:** the table above; 252 claimed, 608 held,
  one no longer flagged.
- **G6 falls:** 681 -> 596, and 61 -> 61 at assigned.
- **G1 conditioned and G2 inside their bounds on the Orbitrap sets:** both
  are identical to step 2.5e's round - G1 conditioned A 2.5, B 11.7, C 0.2,
  C2 0.8, D 1.0; G2 same formula 97.1, 95.1, 94.8, 87.5, 85.5.
- **Every row the reference confirms as an M0 that a claim takes, reported:**
  the six on B above.
- **Set C's doublet lines at the tier the rule gives:** the 18O and 13C2 lines
  of its strongest ion are candidate isotopologues on every sample that
  commits them, in doubt.
- **The gate's isotopologue caps re-counted by overlap and intensity:** above,
  by verdict, by what the reference reads and by cause.


### After step 2.4f, an oxygen-free neutral in a nitrate cluster (2026-09-16)

#2147 holds at candidate a row read as nitrate clustered with a neutral that
has no oxygen (the step's as-built note). The two forms were measured first,
with carbonate beside nitrate, on builds that are not merged: the tier cap
over both families, and the search restriction over both families and over
nitrate alone. The plan owner took the cap for nitrate and left carbonate out
(decision 18's third addendum). The step was then re-run on
`step-2.4f-oxygen-free-cluster-2026.09.16-d9906c3`. Every round is read
against step 2.4e's round.

**The oxygen-free cluster readings on step 2.4e's round** (monoisotopic rows;
the assigned rows' reference readings in brackets):

| set | nitrate | at assigned | carbonate | at assigned (the reference's own formula) |
|---|---|---|---|---|
| C | 6 of 273 | 0 | 91 of 278 | 10 (9) |
| C2 | 19 of 205 | 1, a target library row | 30 of 107 | 12 (10) |
| E | - | - | 15 of 325 | 0 |
| F1 | - | - | 84 of 2,481 | 0 |
| F2 | 115 of 3,602 | 10 | 88 of 1,442 | 5 (1) |

**What the cap does, as built.**
- **Only the 10 F2 rows move**, from assigned to candidate, each with this
  reason alone. No formula, role, owner or other tier changes on the 48,894
  peaks, and none of the ten owns an isotopologue or a claimed line.
  - The reference is silent on 9 of them: eight search rows (C14H13N3,
    C15H19N3S, C15H21N3S, C16H19N3, C17H15N3, C19H17N3, C19H21N3, C25H26S) and a
    reference list's ClI.
  - It commits the tenth, C19H24S, through nitrate as well, at its own
    candidate tier.
- **The rule names 134 rows**, all on the three nitrate sets (C 6, C2 13, F2
  115). The other 124 were already at candidate or below.
  - On C and C2, 11 are the radical C11H13S at candidate, which the reference
    reads as a siloxane's isotopologue line; it is silent on the other 8.
  - On F2 the reference commits the same formula on 2 (C19H24S, and a list's
    ClI below assignability), another formula on 7 and an isotopologue line on
    1, and is silent on 105.
- **C2's target library row**, hydrogen bromide through the labelled nitrate,
  keeps its tier on the six samples that commit it (assigned on one). The
  reference is silent on it.
- **The metrics.** F2: assigned 696 -> 686, G1 97.3 -> 97.4, G1 conditioned
  77.6 -> 78.6 (n 85 -> 84). G2 is identical on every set, and the other seven
  sets are identical in every metric: G1 conditioned A 2.5, B 11.7, C 0.2, C2
  0.8, D 1.0.
- **The runs' records.** `pattern_scoring`, `mass_calibration` and
  `cross_channel` are identical on all 43 runs.
  - `config.tiering` is rule set 4, and its `capped` count goes from 3,250 to
    3,260.
  - `capped_by_rule` names the rule 43 times over 16 runs. It counts every row
    the rule found at the tier its evidence gave, so 33 of those are rows
    another rule capped as well: the C11H13S radicals on C and C2, and 22 on
    F2.

**The forms compared.**
- **The cap over carbonate** would have taken 27 rows from assigned: C 10, C2
  12, F2 5.
  - The reference commits the same formula on 20 of them: C8H18N2 on all
    five C samples and five of the six C2 samples, C10H22N2 on four and five
    of them, and C19H24S on one F2 sample.
  - Their 21 isotopologues would have followed (C 9, C2 12).
  - Assigned would have gone C 612 -> 602 and C2 321 -> 309, with G1
    conditioned unchanged at 0.2 and 0.8 over 480 and 247 rows.
- **The search restriction over nitrate** hands each peak to its next reading.
  - C: the 6 rows go unassigned, 5 of them lines the reference reads as a
    siloxane's.
  - C2: 6 go unassigned, all such lines, and 7 go to other ions below
    assignability. The library rows are untouched.
  - F2: 75 of the 115 are read as the same ion without its proton, an organic
    nitrate through `-H+`, and 18 of those are assigned (16 up from
    candidate). The reagent-N rule asks only a winner through the channel
    that donates the nitrogen, so it does not reach them.
  - F2 again: 33 go to other ions (4 assigned, 3 of them up from candidate), 6
    go unassigned, and one goes to the same ion's oxygen-free carbonate
    reading.
  - F2's assigned count rises 696 -> 709 and its G2 falls 26.1 -> 24.6, since
    C19H24S becomes C19H25NO3S. A list's ClI, which the reference commits,
    goes unassigned.
  - It also takes 9 lines on C that step 2.4e had claimed from such readings:
    the 18O lines of the C10H14O and C11H18O carbonate clusters, and the
    13C+18O lines carried with them.
- **The search restriction over carbonate** reads the same ions without a
  proton, as radicals, which the radical rule holds at candidate. It displaces
  43 readings the reference confirms (C 29, C2 12, F2 2); on F1 it reads 28
  of the 84 through bromide instead.

**For the plan owner (step 2.7a).** The paper's bar is two hydrogen-bond
donors on oxygen, and the rule takes the surest case, a neutral with no oxygen
at all. Of the 134 rows it names, 12 are hydrocarbons, 120 carry nitrogen or
sulfur (nitrogen 45, both 40, sulfur 35), and 2 are a reference list's ClI.
- **A halogen bond to nitrate** is the one cluster the reason's sentence, that
  the source is unlikely to make the ion, over-reaches on.
- The cap holds such a row at candidate, which is decision 18's side of the
  doubt, and the target library's HBr is exempt.
- Step 2.7a can decide whether a list's halogen or interhalogen read through
  nitrate is exempt like the target library's rows, or the sentence is worded
  as doubt.

#### Verify, item by item

- **The rows each form takes, by set, tier and what the reference commits on
  the peak:** the table and the two lists above.
- **What the restricted search elects instead:** the organic nitrate on F2,
  other ions or nothing on the labelled sets, and the carbonate clusters as
  radicals.
- **G1 conditioned and G2:** identical on every set but F2's G1 conditioned,
  77.6 -> 78.6; G2 identical everywhere.
- **The carbonate answer:** not judged by the rule, on the plan owner's
  answer (decision 18's third addendum).

### After step 2.5f's first PR, a list hit's rivals (2026-09-16)

#2148 asks the grid about every list hit of a run that searches (the step's
as-built note). The step was measured offline first, over step 2.4f's round:
- its own rows' densities reproduced;
- the reading's fit compared with Stage A's;
- both ways of counting rivals.

The plan owner took closed-shell rivals (decision 3's sixth addendum), and the
gate ran on `step-2.5f-list-hit-rivals-2026.09.16-49121c8`, read against step
2.4f's round.

**The measurement reproduces the search.**
- **The search's own rows.** Over the round's 26,452 search rows, the count
  equals the density the search stored on 24,084 of 24,445 measured rows. Six
  sets agree exactly and B differs on one row. F1 differs on 360, always lower,
  all above m/z 564 where a peak holds 67 or more candidates, more than the
  scorer keeps.
- **One scale.** A list reading's fit on the search's scale equals Stage A's:
  the median difference is 0.000 on every set.
- **Radicals as rivals.** Counting them too would have held 173 list hits at
  candidate: A 4, B 6, C 2, D 19, E 5, F1 106, F2 31. The reference commits the
  list's formula on 24 of them, 18 on D, where every rival was a radical.

**Stage A monoisotopic rows with a closed-shell rival**, at any tier: A 43 of
402, B 130 of 956, C 25 of 304, C2 9 of 221, D 76 of 953, E 88 of 298, F1 601
of 1,375, F2 790 of 1,626.

**The list hits at assigned with a rival, by set, list and a second channel:**

| set | with a rival | held at candidate | kept by a second channel | lists |
|---|---|---|---|---|
| A | 1 | 0 | 1 | cyclic siloxanes |
| B | 1 | 1 | 0 | linear siloxanes |
| C | 3 | 1 | 2 | atmospheric organics |
| C2 | 0 | 0 | 0 | - |
| D | 2 | 0 | 2 | monoterpene HOM |
| E | 2 | 2 | 0 | monoterpene HOM |
| F1 | 102 | 85 | 17 | monoterpene HOM 99, atmospheric organics, contaminants, nitroaromatics 1 each |
| F2 | 35 | 24 | 11 | monoterpene HOM 34, contaminants 1 |

- **What moves.** Exactly the 113 list hits the table holds at candidate,
  each by the density rule alone; no target library row has a rival at
  assigned.
  - The reference commits the list's formula on 2 (C 1, F1 1), one of the
    rivals' on 1, another formula on 3, an isotopologue line on 2, and nothing
    on 105.
  - B's is the linear siloxane C12H36O4Si5 through the uronium cluster, a
    formula outside the searched box, against C24H28O8 through a proton at
    +0.30 ppm, which fits better (0.92 against 0.84): a box can build a rival
    for a formula it cannot hold.
  - C's is C8H12O5 through the labelled nitrate, against C16H12OS without its
    proton at 0.775 to its 0.778; the reference commits C8H12O5.
- **The claims it unwinds.** Four lines on F1 that step 2.4e had claimed for a
  list hit now held at candidate go back to their search readings, below
  assigned. Three isotopologues of held list hits follow them to candidate.
  Nothing else moves on the 48,894 peaks.
- **The metrics.**
  - F1: assigned 758 -> 673, G1 95.1 -> 94.7, G1 conditioned 50.7 -> 48.6 (n
    75 -> 70), G6 at assigned 4 -> 2.
  - Assigned F2 686 -> 662, E 225 -> 223, B 4,065 -> 4,064, C 612 -> 611.
  - G2 is identical on every set, and G1 conditioned on the Orbitrap sets: A
    2.5, B 11.7, C 0.2, C2 0.8, D 1.0.
- **The runs' records.**
  - The pattern scoring and the calibration's offset, width, anchors and line
    are identical on all 43 runs. On the two F1 runs whose claims unwound, the
    isotopologue counts and the committed monoisotopic count move with them.
  - `config.tiering`: capped 3,260 -> 3,373, density 2,285 -> 2,403, claimed
    252 -> 248.
  - `search_scope.list_hits` counts the rows as Stage A built them, before 20
    were read as other formulas' isotopologues: A 407 measured and 44 with a
    rival, B 957 and 130, C 306 and 25, C2 221 and 9, D 955 and 76, E 300 and
    89, F1 1,381 and 604, F2 1,628 and 790.
- **The run time.** The measurement took 49 s over the 43 runs, at most 4.2 s
  a run. The runs took 792 s where today's five rounds before took 730 to
  774 s.

**For the plan owner.**
- **The second PR.** The election, with the list formula as one candidate
  carrying a prior, waits on the prior's weight (the step's size note).
- **Where the rivals live.** On the Orbitrap sets few list hits at assigned
  have a closed-shell rival (A 1, B 1, C 3, C2 0, D 2). On the TOF sets they
  are common, since the window and the width are wide there, and the monoterpene
  HOM list carries almost all of them.
- **Elections' own density** still counts radical rivals (decision 3's sixth
  addendum).

#### Verify, item by item

- **The Stage A rows at assigned with a grid rival inside the width, by set,
  by list and by whether a second channel holds the neutral:** the table.
- **The rows the density rule caps and what the reference commits on their
  peaks:** the 113 above.
- **The run time:** 49 s of measurement over the 43 runs.
- **The claims whose neighbour it takes below assigned:** the four on F1.

### After step 2.5f's second PR, the election (2026-09-17)

#2149 holds the election (the step's as-built note). It was measured on
`step-2.5f-list-hit-election-2026.09.17-233c265`, read against the measurement's
round. Decision 19's lines answer a first build with the prior alone
(`...-bdf0381`).

**What the first build showed.** With the prior alone, rivals took 1,137
list-hit peaks: 1,121 readings below assignability, 15 at candidate and one at
assigned. The reference commits the rival's formula on 19 and the list's on
none.
- **No evidence.** 212 of those readings the evidence puts at 0. Stage A's own
  fit is 0 on them too, since they sit several widths from their peaks.
- **Lines left unexplained.** 41 had isotopologues that tracked them, and on 28
  the rival explains none of those lines: B's siloxanes, bromide adducts on D
  and F1, and the one assigned hit, which the two measures read opposite ways.
  The search's fit gives the reading 0.17 against Stage A's 0.83, and the rival
  0.44 against 0. 92 released lines went unassigned.
- **The siloxanes.** D5 through urea on B matches its 29Si and 30Si lines within
  0.1 ppm. Its 29Si line holds 69% of the prediction, which the fit's abundance
  term reads as a miss, and the CHO rival is charged nothing for either line.
- **The tiered measure.** Weighed on it instead, about 949 peaks would have been
  taken.

The plan owner took the lines and the library exemption (decision 19).

**Who holds the list's peaks.** Rivals take 1,093 of them: A 22, B 68, C 19, C2
1, D 16, E 61, F1 308, F2 598.
- **The records.** 1,089 rows carry the list's reading. The claims pass then
  reads the other 4 as a neighbour's isotopologue, as the measurement's round
  read them, and their alternatives keep both readings.
- **The readings taken.** The list's reading was below assignability on 1,086
  and at candidate on 3. The monoterpene HOM list holds 1,014 of them, the
  siloxanes 3.
- **The rivals.** They sit at assigned on 143, candidate on 627 and below on 319.
- **The reference.** It commits the rival's formula on 19, the list's on none,
  another formula on 138 and an isotopologue on 12, and is silent on 920.
- **The released lines.** 21 are now the rival's, and 27 are unassigned.

**Kept against a rival that cleared the prior.**
- **By the reading's lines: 47.** B 27, all siloxanes; D 7; E 1; F1 12.
  - Their tiers: the assigned sulfonamide, 11 at candidate and 35 below.
  - The reference commits another formula on 7, a siloxane (C11H26O8Si3) on 4
    of them, and the rival's formula on none.
- **By the library: 1** (E, at candidate).

**What else moves.**
- **Second channels.** 32 list hits at assigned go to candidate (F2 28, F1 4),
  and the reference is silent on 27 of them and commits another formula on 5.
  - On 31, the other channel's reading of their neutral was a list hit a rival
    took. Without it, the reagent-N rule holds 25 and the density rule 6.
  - The 32nd keeps both channels and falls off its run's calibration, which the
    taken rows moved.
- **Search rows.** 95 change tier or role around the taken peaks, through the
  rivals' envelopes, the seeded fits and the calibration. 21 remainder peaks
  are now a rival's isotopologues.
- **The calibration.**
  - The offset moves on 13 runs: by at most 0.27 ppm on one F1 sample, 0.17 ppm
    elsewhere, and 0.01 ppm on D.
  - The width moves on 15 runs, by at most 0.34 ppm.
  - The line through m/z moves on 5 (A 4, C2 1), by at most 0.006 mDa, and
    the reason it is refused changes on 4 runs where it stays refused.
  - The pattern scoring is identical on all 43 runs.
- **The tiering record.** Capped 3,373 -> 3,475, density 2,403 -> 2,498,
  claimed 248 -> 247.

**The metrics.**
- **Assigned:** B 4,064 -> 4,078, C 611 -> 615, D 897 -> 899, E 223 -> 236, F1
  673 -> 699, F2 662 -> 721; A and C2 unchanged.
- **G1:** B 37.0 -> 37.2, C 20.3 -> 20.7, D 8.1 -> 8.3, E 93.3 -> 93.6, F1 94.7
  -> 94.8, F2 97.3 -> 96.7.
- **G1 conditioned:** B 11.7 -> 11.9, D 1.0 -> 1.2, F1 48.6 -> 50.7, F2 78.6 ->
  76.0 (n 84 -> 100); A, C, C2 and E unchanged.
- **G2:** identical on every set but F2, where it rises from 26.1 to 29.0.
- **G6 at assigned:** B 38 -> 39, C 7 -> 10, F2 1 -> 2.

**The run time.** The runs took 913 s, and 742 s when the same build ran again.
The first build took 760 s, and the two rounds before the election 792 and 730
s. F1's runs took 368 and 266 s against 272, 284 and 263. The election costs no
more than the measurement it replaces, within the host's spread. F2, where
rivals take 598 peaks and their rows are measured again, took 104 to 118 s
against 81 to 91.

**For the plan owner.**
- **Second channels.** A list hit held at assigned by a second channel's weak
  list reading loses that hold when a rival takes the other peak (31 of the 32
  above).
- **The two fits.** On six taken peaks, Stage A's own evidence for the reading,
  times the prior, would have held the rival off (B 1, D 1, E 1, F1 2, F2 1),
  and the reference is silent on all six. On two of them, readings Stage A
  scored at 0.535 and 0.800 went to rivals that sit below assignability. This is
  what not taking the tiered measure costs on this round (decision 19).
- **The fit's abundance term.** It reads a matched line at 69% of its predicted
  height as a miss, which put B's siloxanes below their rivals. The lines
  answer it for list hits; the search's own elections still meet it. For 2.7a.
- **Elections' own density** still counts radical rivals (decision 3's sixth
  addendum).

#### Verify, item by item

- **The peaks a grid rival wins from a list formula, split by whether the
  reference agrees with either:** 1,093; the reference agrees with the rival
  on 19 and with the list on none.
- **The list-only families promoting back through uniqueness:** none promotes.
  At assigned, the siloxanes (13), phosphates (35), iodine species (16) and
  fluorinated acids (37) keep their tiers, and the density rule still holds
  those at candidate that have a rival. Rivals take 3 siloxanes, 3 phosphates
  and 4 fluorinated acids below assignability, and the lines keep 27 siloxanes.
- **G1 and G1 conditioned:** above; conditioned, they rise on B, D and F1 and
  fall on F2.
- **G2 unchanged where no rival wins:** rivals win on every set; G2 is
  identical on seven and rises on F2.
- **The three library isotopologues step 2.5d's cap took, re-read with the
  grid's rivals beside them:** unchanged at candidate. Their monoisotopic rows,
  C10H14O on C and C3H4O4 on F2, have no closed-shell rival within the width
  and keep their peaks.

### After step 2.6, profile, reasons and roles in the app (2026-09-17)

Display and documentation, plus two reads the launchers needed. No engine code
changed, so no run, ledger or gate number moves, and the baseline for the next
step stays step 2.5f's round.

**The chemistry in the launchers.** *Assign peaks* and the batch's untargeted
search share one form, and it now opens on the profile and context selectors,
both on *Auto*.
- **How the form learns what *Auto* means.** The server answers it; the browser
  holds no copy of the fingerprint. Two read routes resolve a run config's names
  over a sample or over a batch's samples, from their ionization modes and
  polarities. The detection order and the polarity fallback therefore stay in
  `detect_reagent_profile`, where step 4.5 will change them. A batch gets one
  answer per distinct resolution with its sample count, since the batch search
  resolves per sample.
- **What a preview leaves out.** It names the profile, the context and the grid
  they give, and nothing the mechanisms do not decide: the m/z window needs the
  instrument class, and the secondary channels need the spectrum.
- **The presets** are served with the run config's bounds (`/params`), from the
  library the config validates against.
- **Persistence.** Develop's shared launch settings, which reached the epic with
  its rebase, persist every field a launcher shows, and the two names follow
  them. A named profile carried to a sample of the other polarity would search
  reagent chemistry that sample was never measured with. The form says so rather
  than refusing, and *Reset to defaults* puts both names back on *Auto*.

**The chemistry on the run.** The run selector's provenance chip names the
profile an in-app run recorded (`config.resolved_profile`). Its hover gives:
- the context, the grid and the window, each marked where the run set it
  rather than resolved it;
- the secondary channels, and a channel the deployment could not express.

**Beside the reasons.**
- **mass z.** The inspector shows `mass_z` right after the ppm error. Its hover
  gives the run's centre, the gate's width and `cap_z` / `floor_z`. The value is
  marked past `cap_z`, and whether the cap applied stays the reasons' to say.
- **The same ion's other readings.** The `same_ion` alternatives are listed
  under *Why this tier*, which is where *ambiguous nitrogen* points.
- **The marked alternatives.** The close alternatives mark the same-ion
  readings, and also the two displaced readings steps 2.4e and 2.5f put first
  (`displaced_by_claim`, `displaced_by_rival`). The reasons' sentences already
  tell a reader those are there.

**The roles.** The reagent and artifact passes write their rows at tier
`unassigned`, which is true (no compound was assigned) and read as "nothing
explained this" on a chip.
- **The chip.** The role now replaces the tier on the chip wherever it is drawn.
- **The strip.** The strip above the ledger counts the two roles apart from the
  tiers and from each other. `tierCounts` already excluded them from the
  analyte counts; it had counted both as one "reagent".
- **Sorting.** The tier column sorts the role rows after every tier.

**The testbed.** The testbed now runs the step's build, the first from the
rebased epic. Develop ships `peak_assignment = false` since v1.8.0, so the
testbed's env config now sets it true.

#### Verify, item by item

- **Vitest on the form and the inspector rows:** green. The frontend suite has
  1,246 tests.
  - The form's chemistry has 13 cases; the store has 4 more and the name helpers
    their own spec.
  - The inspector has 10 cases for *mass z*, the same-ion readings and the
    marked alternatives.
  - The ledger's role chips, filters and sort, the tier tag's role chips, the
    run chip's chemistry and `tierHistogram` are covered, and both launchers are
    checked to pass their scope.
- **Reversions.** Each new frontend line was reverted with the specs re-run: 37
  of 37 red. The backend's 15 of 15 are red too.
- **The reads, against the gate's samples.** On the testbed's build of this
  step, one sample of each gate set previews as its latest run recorded it: the
  same profile, context and grid. Each gate batch (3 to 595 samples) previews
  as one answer with its count. A named bromide profile over the urea sets
  returns the polarity pair the form warns on. An unknown name is a 422, and a
  missing sample or batch is a 404.

### After step 2.7a, the reference refreshed (2026-09-17)

No engine change. The in-app runs are the round step 2.6 left, and every
engine-side number below is the same on both sides. What moved is the
reference, once, as decision 16 planned.

**The branch.** peaky's `epic/v2-fit-reference` is now `26e0ff3`.
- **Its base** is peaky's main at `823f8dd`: release 0.8.0 and three PRs after
  it, a help-text fix and the privacy scan, none of them on the scoring path.
- **On top** sit the twelve commits that need the unreleased library, and one
  more. That one pins `mascope-tools` to this epic's head, `49aa69ca5` (it was
  `fc25575da`), and sets `allow-direct-references`.
- **Its CI** installs and passes, on both Pythons and the locked job, for the
  first time since step 2.1b. Every push before this failed at install.
- **Every run says so.** All 43 published runs record commit `26e0ff3` and
  version `0.8.0+assign0.6.0`.

**A change on main the plan did not list.** `peaky batch` now picks its own
samples: the fewest spectra that cover the batch's persistent ions, then a
residual stage. On the gate's batches that picks 0 to 2 of each set's gate
samples.
- **So the batch runs are pinned.** Sets A to D run on the gate's samples, with
  the residual stage off, and each run records that selection.
- **The rest is as before.** E, F1 and F2 are single-sample runs, and every
  other flag is the frozen run's.

**The run is reproducible.** Set A ran twice, on two machines and two operating
systems. All 2,626 rows agree in every published field. The only difference is
the sixteenth digit of some floats inside `alternatives`, which the publish
does not carry. Every batch ledger carries its time-series disposition, the
trap step 2.1b found.

**Both references stay readable.** The store keeps the two newest runs per
sample and engine, so the frozen reference is now each sample's previous peaky
run. `compare_runs.py --engine-b-before <timestamp>` reads the newest run
created before that instant.

**What each reference is judged at.** Its anchors are the server's own matches
of the sample that peaky can mass.

| set | anchors | width | offset ppm |
|---|---|---|---|
| A | 11-12 | fitted | -0.33 to 0.00, both |
| B | 5-8 | class on four samples, fitted on two | 0.00 to +0.23, both |
| C | 2 -> 1 | class | none |
| C2 | 6 -> 4 | class | -1.23 to -1.18 -> **none** |
| D | 3 -> 2 | class | none |
| E | 6 -> 5 | class | -0.18 to +0.71 -> -0.18 to +0.95 |
| F1 | 14-17 -> 13-15 | fitted | +1.07 to +2.67 -> +1.07 to +2.43 |
| F2 | 17-21 | fitted | +0.11 to +0.88, both |

- **Where the anchors went.** Two testbed edits since the freeze removed
  matched lines, and with them anchors:
  - the nitrate monitor's fix, which replaced the entry on C's base peak;
  - step 2.5d's removal of the odd-electron workaround entries: HCO3 and HS3 on
    C2, Br on D, E and F1, and CHO3 on F1.
- **C2 falls below the five an offset needs.** Its four remaining anchors are
  acetic acid, nitric acid, HBr and C3H6O3, at -0.7 to -2.2 ppm with a median
  of -1.25 to -1.28 on every sample.
  - The refreshed reference therefore scores C2 at no offset, on a source that
    reads 1.2 ppm low. That is the collapse step 2.1b described.
  - The in-app engine met the same loss in 2.5d, and 2.5e answered it with the
    reagent lines.
- **The labelled reagent's own lines are not counted.** C2 matches its 15N
  reagent ions too, but peaky skips every matched row whose isotope formula
  carries a bracket, and a labelled ion is written with one.
  - Four of those rows are base lines, not isotopologues: `[15N]O3-`, the
    nitric acid and HBr clusters, and the labelled dimer. peaky masses all
    four.
  - Counted, they give every C2 sample eight anchors and an offset of -1.05 to
    -1.07 ppm. The reagent ion itself reads +1.25 ppm, against -0.05 to -1.95
    for the other three.

So **C2's refreshed numbers are not a reference.** They are shown below for
completeness, and C2 is read against the frozen reference.

**The reference's own ledger.** Its committed M0 rows and their tiers by
peaky's own bands, how often a peak both references commit changed formula,
and its committed mass error.

| set | committed M0 | its Assigned | its Candidate | formula changed | its MAD ppm |
|---|---|---|---|---|---|
| A | 1002 -> 1599 | 784 -> 980 | 218 -> 619 | 29 of 994 (2.9%) | 0.150 -> 0.160 |
| B | 4680 -> 5081 | 1933 -> 2164 | 2747 -> 2917 | 199 of 4562 (4.4%) | 0.174 -> 0.174 |
| C | 681 -> 799 | 445 -> 509 | 236 -> 290 | 0 of 673 | 0.121 -> 0.127 |
| C2 | 381 -> 159 | 264 -> 67 | 117 -> 92 | 22 of 77 (28.6%) | 0.167 -> 0.409 |
| D | 1554 -> 1632 | 800 -> 791 | 754 -> 841 | 2 of 1535 (0.1%) | 0.189 -> 0.190 |
| E | 158 -> 552 | 42 -> 169 | 116 -> 383 | 16 of 90 (17.8%) | 1.106 -> 1.120 |
| F1 | 1034 -> 2265 | 123 -> 399 | 911 -> 1866 | 138 of 227 (60.8%) | 0.995 -> 1.140 |
| F2 | 827 -> 1267 | 69 -> 318 | 758 -> 949 | 81 of 145 (55.9%) | 0.934 -> 0.714 |

- **It commits more everywhere but C2,** and on the TOF sets one and a half to
  three and a half times as much. The runs cut at each sample's own noise edge,
  main's height gate, which the plan named as the reason the TOF reference had
  committed so little. The four changes on main were not measured apart.
- **The Orbitrap reference keeps its readings.** Where both references commit,
  the formula moves on 0 to 4.4% of the peaks.
- **The TOF reference does not.** It moves on 18% of E's shared peaks and over
  half of F1's and F2's, so a TOF number here is a reading of this reference
  rather than a settled one.

**The gate against both references.** The engine side is identical: G3, G5
(0 everywhere) and G8 (0 everywhere) do not move.

| set | G1 | G2 formula / ion | G2 n | G4 | G6 (at assigned) |
|---|---|---|---|---|---|
| A | 23.2 -> **6.1** | 97.1 -> 95.2 / 98.0 -> 96.6 | 784 -> 980 | 48 of 58 | 27 -> 38 (2 -> 4) |
| B | 37.2 -> 32.7 | 95.1 -> 93.8 / 96.4 -> 94.8 | 1933 -> 2164 | 14 of 24 | 210 -> 249 (39 -> 51) |
| C | 20.7 -> **10.9** | 94.8 -> 93.7 / 94.8 -> 93.7 | 445 -> 509 | 0 of 29 | 46 (10 -> 9) |
| C2 | 20.6 -> 91.0 | 87.5 -> 49.3 / 87.5 -> 49.3 | 264 -> 67 | 0 of 33 | 18 -> 16 (4) |
| D | 8.3 -> **7.9** | 85.5 -> 85.0 / 87.1 -> 86.6 | 800 -> 791 | 124 of 344 | 156 -> 164 (2 -> 3) |
| E | 93.6 -> 66.1 | 54.8 -> 54.4 / 64.3 -> 56.8 | 42 -> 169 | 48 of 322 -> 48 of 313 | 9 -> 20 (3 -> 1) |
| F1 | 94.8 -> 78.0 | 27.6 -> 41.6 / 29.3 -> 42.1 | 123 -> 399 | 50 of 323 -> 50 of 291 | 96 -> 164 (2 -> 6) |
| F2 | 96.7 -> 83.9 | 29.0 -> 41.2 / 29.0 -> 43.1 | 69 -> 318 | 0 of 10 | 31 -> 68 (2 -> 4) |

**G1 falls because the reference speaks, not because it agrees more.** Its
three parts, over the engine's assigned monoisotopic rows: the reference reads
the peak as another ion, as another compound's isotopologue, or as reagent or
artifact (contradicts); it reads the same ion as another neutral and adduct,
which no spectrum can separate (the split); or it commits nothing on the peak
at all.

| set | contradicts | same ion, other split | does not commit | = G1 |
|---|---|---|---|---|
| A | 0.2 -> 0.7 | 2.0 -> 2.4 | 21.0 -> 3.1 | 23.2 -> 6.1 |
| B | 1.7 -> 2.2 | 7.8 -> 6.8 | 27.7 -> 23.7 | 37.2 -> 32.7 |
| C | 1.8 -> 2.3 | 0.0 -> 0.2 | 18.9 -> 8.5 | 20.7 -> 10.9 |
| C2 | 1.6 -> 9.7 | 0.3 -> 0.0 | 18.7 -> 81.3 | 20.6 -> 91.0 |
| D | 1.3 -> 1.9 | 0.0 -> 0.0 | 7.0 -> 6.0 | 8.3 -> 7.9 |
| E | 5.1 -> 5.5 | 1.3 -> 0.4 | 87.3 -> 60.2 | 93.6 -> 66.1 |
| F1 | 6.4 -> 3.3 | 0.0 -> 0.0 | 88.4 -> 74.7 | 94.8 -> 78.0 |
| F2 | 10.8 -> 3.5 | 0.0 -> 0.4 | 85.9 -> 80.0 | 96.7 -> 83.9 |

- **On the Orbitrap sets** the silent share falls by up to 85%, and the
  contradicted share rises by 0.6 points at most.
- **On F1 and F2** the contradicted share roughly halves and falls by two
  thirds.

**G1 conditioned (decision 14)**, over the assigned rows the reference commits
an M0 on, with n:

| set | frozen | refreshed |
|---|---|---|
| A | 2.5 (797) | 2.8 (977) |
| B | 11.9 (2906) | 10.2 (3057) |
| C | 0.2 (489) | 1.1 (554) |
| C2 | 0.8 (257) | 48.2 (56) |
| D | 1.2 (834) | 1.7 (842) |
| E | 42.3 (26) | **13.0 (92)** |
| F1 | 50.7 (73) | **7.8 (167)** |
| F2 | 76.0 (100) | **17.1 (140)** |

**The TOF sets now have the reference decision 14 carried them to.** It
commits on 92, 167 and 140 of their assigned rows, and on those the engine
meets the 20% bound on all three. The unconditioned G1 stays at 66 to 84%,
which is the reference's silence.

**What the refresh answers for 2.7.** Against the refreshed reference, G1 is
met on A, C and D and missed on B. B's miss is silence: the reference commits
nothing on 23.7 points of its 32.7. G2's formula bound holds on A, B, C and D,
and its ion bound only on A. C2 is read against the frozen reference until its
axis or its anchors are fixed.

**The items this step was to read.**
- **A claim's `displaced.tier` (2.4e).** It is informative as it stands, the
  displaced reading's evidence tier.
  - On the Orbitrap sets the refreshed reference commits the displaced reading
    on 5 of the 17 claims whose displaced tier says assigned, 4 of 25 at
    candidate, and none of 145 below assignability. It confirms the claim on
    9, 12 and 41 of them.
  - So a claim that displaces a reading its evidence called assigned is the
    contested kind. The re-read takes the tier so, and the block needs no
    second tier.
- **The height rule's tails (2.4e).** Over the 247 claims a row makes for
  itself - six more rows are carried with one - the refreshed reference confirms
  17% below 0.6 of the predicted height, 57% between 0.6 and 1.4, and 26%
  between 1.4 and 2.0 (frozen: 16%, 53% and 13%). The low tail is where the
  doubt is.
- **The widened bar on the TOF sets (2.4e).** The refreshed reference now
  speaks there, and the lines in doubt fare worse than the lines that track.
  - It confirms 24% of the TOF isotopologue rows whose line tracks (7% frozen)
    and 4% of those in doubt (under 1% frozen).
  - Where it commits on a line in doubt, it reads the peak otherwise 17 times
    against 8 confirmations. On the tracking lines it is 124 against 197.
  - Where the reference speaks, a TOF line in doubt is more often read
    otherwise than confirmed. That argues for keeping it out of the evidence
    that lifts its owner, for 2.7 to take or leave.
- **Oxygen-free nitrate clusters (2.4f).** The rule caps 144 rows now, and the
  refreshed reference commits on 21 of them: 13 as isotopologues, 6 as another
  neutral and 2 as the same.
  - The two ClI rows read as they did. The reference commits ClI through
    nitrate on one of the two peaks, at below assignability, and nothing on the
    other.
  - So the reference does not settle whether a list's halogen read through
    nitrate should be exempt.
- **List hits a rival took (2.5f).** Of the 1,089, the refreshed reference
  commits the rival's formula on 44 (19 frozen) and the list's on none. It is
  silent on 930.
  - Every refreshed run activates one list, Keller's contaminants. The HOM
    list, whose readings are 1,014 of the taken peaks, is not one the
    reference reads, so its silence there is no verdict on the list.
- **The fit's abundance term (2.5f).** Its first answer is B's siloxanes.
  - The lines kept 27 of them against their rivals, and the refreshed reference
    commits the list's siloxane on 20 (none frozen). The lines were right.
  - The search's own elections cannot be read from the store. Of the untargeted
    rows the refreshed reference reads as a different ion, the run held the
    reference's reading among its alternatives on 357 of 3,214, and those are
    stored without a fit. Measuring the term there needs the candidates scored
    again, with and without it.
- **C2's lines through m/z (2.2b, 2.5e).** The refreshed reference cannot speak
  to them, since it scores C2 at no offset (above).

**For the plan owner.**
- **C2.** The step was to recalibrate C2 with the refresh. Doing so writes the
  six sample files, needs more calibrants on its mode than the two that
  disagree, and runs the calibration routes, which take a signed-in session.
  - **Recalibrated,** C2's axis is right, and the refreshed reference's zero
    offset becomes true.
  - **Separately,** peaky's anchor rule misses a labelled reagent's own lines,
    a fix for peaky's main whatever C2's axis.

#### Verify, item by item

- **The branch:** CI green on both Pythons and the locked job. peaky's own
  suite, the privacy scan included, passes on Linux.
- **The runs:** 43 published, each recording commit `26e0ff3` and version
  `0.8.0+assign0.6.0`. Set A's two runs agree in every published field.
  - One batch worker crashed on D's first attempt, a segfault in the
    interpreter on a fully loaded host. D ran again on its own and completed.
- **The engine side:** the same 43 in-app runs before and after, so G3, G5 and
  G8 do not move.
- **The frozen read:** after the publish, `--engine-b-before` gives the
  comparison made before it, byte for byte, on all eight sets.
- **The anchors:** C2's four and the four labelled base lines were read from
  the peaks endpoint on all six samples.

### The stage 2 gate: engine 0.5.0 (2026-09-18)

Branch `step-2.7-stage-2-gate` deployed on the testbed in prod mode
(`...-2026.09.18-dc06a54`). All 43 gate samples were re-assigned and read against
both references. Since step 2.7a, three things changed:
- **The version.** `PEAK_ASSIGNMENT_ENGINE_VERSION` is 0.5.0.
- **One rule.** The polyhalide rule is the plan owner's IBr2- decision (decision
  17's addendum).
- **One data change, made before the round.** C2 was recalibrated.

#### C2 recalibrated

- **Why.** C2's mode calibrates on two lines, and they are its two brightest.
  - They are the 15N reagent's base peak (+1.26 ppm, 8.6 million counts) and its
    labelled dimer (-0.06 ppm, 5.5 million).
  - Every weaker line of the same ladder reads -0.73 to -1.95 ppm, and the run's
    committed analytes read -1.13.
  - The node's Orbitrap fit is one factor, the median of its lines, so it could
    not move. The plan owner's rule is that the brightest peaks are not
    calibrants: they carry artifacts and distortion on both instruments (step
    3.5).
- **How.** The six files were refitted on a list without the reagent's own
  lines: nitric acid's weak lines, acetic acid, malonic through azelaic acid
  and palmitic acid, all existing compounds. The fit and the apply are the
  node's own.
  - **The mode keeps its list.** Changing a mode's calibration collection flags
    every batch of the mode for recalibration and changes what Stage A
    matches.
  - **The fit** kept 4 or 5 lines per file, at 3,800 to 97,000 counts, and
    moved the axis +0.96 to +1.11 ppm. Its residuals are within 0.37 ppm, with
    no quality issue.
  - **Afterwards** the six samples were rematched, both engines re-ran C2, and
    C2's reference was re-published.
- **What it did.**
  - **The engine's commits.** The median error of its assigned rows goes
    from -1.14 to -0.08 ppm, and their spread (median absolute deviation) from
    0.156 to 0.135.
  - **The refreshed reference.** Its four anchors still score C2 at no
    offset, and that is now true. It commits 393 analytes at -0.08 ppm, where
    it had 159 on the old axis (step 2.7a) and the frozen reference 381.
  - **C2 has a usable reference again.** G1 against it is 19.8, and G1
    conditioned 2.7 over 258 rows.
- **What it does not do.** The frozen reference's C2 runs are on the old axis.
  Their formulas are still compared peak for peak below, but their mass errors
  are not comparable.

#### What the round changed

Against the round before it (step 2.6's on 37 samples, C2's recalibrated round
on six):
- **The version.** All 43 runs record 0.5.0.
- **Nothing else moves but the rule's rows.** No peak changes owner, and every
  set's row counts are identical.
- **The polyhalide rule names 10 rows**, all from the reactive iodine list on
  the bromide sets: IBr on eight peaks and ICl on two.
  - It takes the top tier from six of them: E 1, F1 4, D 1. Neither reference
    confirms any of the six.
  - G1 moves on three sets: D 7.9 -> 7.8, E 66.1 -> 66.0 and F1 78.0 -> 77.8.
    G1 conditioned moves only on F1 (7.8 -> 6.7). G2 is identical everywhere.
- **The run time.** The 43 runs took 796 s.

#### The gate, against both references

G1 conditioned is decision 14's, over the assigned rows the reference commits
an M0 on; G2 is against the refreshed reference; G6 counts main peaks at the
assigned tier on a reference isotopologue.

| set | assigned | G1 frozen / refreshed | G1 conditioned, frozen / refreshed (n) | G2 formula / ion (n) | G6 | MAD ppm |
|---|---|---|---|---|---|---|
| A | 1012 | 23.2 / **6.1** | 2.5 (797) / 2.8 (977) | **95.2** / **96.6** (980) | 4 | **0.200** |
| B | 4078 | 37.2 / 32.7 | 11.9 (2906) / 10.2 (3057) | **93.8** / 94.8 (2164) | 51 | **0.294** |
| C | 615 | 20.7 / **10.9** | 0.2 (489) / 1.1 (554) | **93.7** / 93.7 (509) | 9 | **0.162** |
| C2 | 313 | 22.7 / **19.8** | 2.0 (247) / 2.7 (258) | **87.2** / 87.2 (250) | 9 | **0.254** |
| D | 898 | **8.2** / **7.8** | 1.2 (834) / 1.7 (842) | **85.0** / 86.6 (791) | 3 | **0.317** |
| E | 235 | 93.6 / 66.0 | 42.3 (26) / **13.0 (92)** | 54.4 / 56.8 (169) | 1 | 2.265 |
| F1 | 695 | 94.8 / 77.8 | 50.7 (73) / **6.7 (165)** | 41.6 / 42.1 (399) | 6 | 1.013 |
| F2 | 721 | 96.7 / 83.9 | 76.0 (100) / **17.1 (140)** | 41.2 / 43.1 (318) | 4 | 1.296 |

G1's three parts against the refreshed reference:

| set | contradicts | same ion, other split | does not commit | = G1 |
|---|---|---|---|---|
| A | 0.7 | 2.4 | 3.1 | 6.1 |
| B | 2.2 | 6.8 | 23.7 | 32.7 |
| C | 2.3 | 0.2 | 8.5 | 10.9 |
| C2 | 3.8 | 1.3 | 14.7 | 19.8 |
| D | 1.9 | 0.0 | 5.9 | 7.8 |
| E | 5.1 | 0.4 | 60.4 | 66.0 |
| F1 | 2.7 | 0.0 | 75.1 | 77.8 |
| F2 | 3.5 | 0.4 | 80.0 | 83.9 |

#### Metric by metric

- **G1 `<= 20%`.**
  - **Against the refreshed reference:** met on A, C, C2 and D (6.1, 10.9,
    19.8, 7.8) and missed on B (32.7). B's miss is silence: the reference
    commits nothing on 23.7 of its 32.7 points, and contradicts 2.2.
  - **Against the frozen reference:** met on D only. That reference is silent
    on a fifth of A's, C's and C2's assigned rows.
  - **Conditioned (decision 14):** within 20% on all eight sets against the
    refreshed reference, the TOF sets included (E 13.0, F1 6.7, F2 17.1).
    Against the frozen reference it is within 20% on the five Orbitrap sets
    only.
- **G2 `>= 85%` same formula, `>= 95%` same ion.** The formula bound is met on
  all five Orbitrap sets: A 95.2, B 93.8, C 93.7, C2 87.2, D 85.0. The ion
  bound is met on A only (96.6); B, C, C2 and D read 94.8, 93.7, 87.2 and 86.6.
- **G3, held.** Formulas with five or more nitrogens: A 0.5%, and 0.0% on every
  set but B, whose 2.5% is the uronium context's own nitrogen cap of 5. Every
  carbon-free formula comes from the target library or a reference list (159
  rows); the formula search writes none, as at stage 1.
- **G4, `100%`: not met, and unchanged since stage 1.** Of the reference's
  reagent rows, this engine calls reagent or artifact 48 of 58 on A, 14 of 24
  on B and 124 of 344 on D. On C, C2 and F2 the reference's reagent rows are a
  different claim (stage 1's reading).
- **G5 and G8: 0** on every set.
- **G6 `<= 5` at assigned:** met on A (4), D (3), E (1) and F2 (4); missed on
  B (51), C (9), C2 (9) and F1 (6).
- **G7: 0.** No assigned monoisotopic row sits beyond 3 widths of its run's
  calibration at all, corroborated or not. Every committed M0 row records its
  `mass_z`.
- **The mass error, held.** Every Orbitrap set is inside 0.35 ppm.
- **Every committed row carries its tier reasons:** 38,190 of 38,190.
- **The top-24 view.** On the stakeholder sample, the brightest 24 peaks hold six
  reagent peaks, all labelled reagent, and no reagent peak is fitted as an
  analyte. The cyclic siloxanes D3 to D5 carry their silicon formulas, and the
  refreshed reference commits the same formulas on them. Stage 1 had fitted
  carbon-rich formulas there.

**What the gate says.** The two measures the stage was built to move are met
on the sets they gate:
- the share of assigned rows the reference contradicts is 0.7 to 3.8% on the
  Orbitrap sets and 2.7 to 5.1% on the TOF sets;
- the assigned rows the reference commits on are confirmed to within 20% on
  every set.

What is not met:
- B's silence;
- the same-ion bound where one ion is read two ways (decision 9's policy);
- the reagent-agreement target (G4);
- G6 on four sets.

None of these is a tier the engine asserts without evidence. On the plan's own
terms, this is the point at which the feature can be re-presented.

**For the plan owner.**
- **The stakeholder page** is rebuilt from this round against the refreshed
  reference, as step 1.7 rebuilt it.
- **The TOF reference** changes its formula on more than half of F1's and F2's
  shared peaks between its two versions (step 2.7a). A TOF number here is a
  reading of today's reference, not a settled one.
- **Stage 3's order** stands: 3.1 series, 3.2 time series (which also settles
  where a polyhalide came from), 3.3 calibration from verdicts, 3.4 gate
  automation, 3.5 profiles as rows (renumbered 4.1 to 4.5 on 2026-09-24,
  decision 21). 3.6, the calibration node's cap (now step 3.5), is
  independent of the others.

#### Verify, item by item

- **Tests at the head.**
  - The whole backend suite passes: 3,561 passed and 6 skipped.
  - The library suite (687) and the frontend unit suite (1,247) pass.
  - ruff and the strict docs build are clean.
- **Reversions.** Each new line of the polyhalide rule was reverted and its
  tests re-run: 7 of 7 red. So are the frontend label's reversion and undoing
  either version bump: a test pins each number, the rule set's 5 on the
  tiering record and the engine's 0.5.0 on the run row.
- **The round.** 43 of 43 runs completed at 0.5.0. The row counts, owners and
  G2 are identical to the round before, except for the rule's six rows.
- **C2's recalibration.** The six stored records moved by exactly their fitted
  factor and stay verified. The records from before are kept, and applying the
  inverse factor restores the files.
- **G7 and the tier reasons** were read from the store over every committed row
  of the round.

### After step 2.8, the band first and one reading per ion (2026-09-18)

All 43 samples re-run on `step-2.8-tier-reasons-same-ion-2026.09.21-6f308c3`,
the step's head after its review, compared with the 0.5.0 round of step 2.7.
The engine stays at 0.5.0 and every run records rule set 6. Every peak's tier
equals the step's first round, on `1c4ac51` before the review. A round between
the two let C's declared carbonate channel out of the minor-channel cap; the
plan owner kept the cap (decision 20), and what it holds is below.

#### What the round changed

No peak changes owner, formula, role or mechanism on any of the 48,894 peaks,
and every set's row counts are identical. 87 monoisotopic rows change tier, and
7 isotopologues follow them.
- **75 go from assigned to candidate**, each on the same-ion rule:
  - A 13 and B 57 on `ambiguous_nitrogen`: urea adducts whose ion also reads as
    the ammonium adduct of the analyte plus isocyanic acid, one nitrogen richer,
    which the rule did not ask while both channels carried nitrogen. A's
    thirteen are two compounds, C5H12N2O and C4H6N2O2, on every sample.
  - D 3 and F1 2 on `ambiguous_adduct`: bromide clusters whose ion also reads as
    the deprotonated molecule that holds the hydrogen bromide.

  The reference commits the same formula on 28 of the 75, all of A's among
  them, by an adduct preference of its own; it reads 2 as the same ion split
  another way, commits another formula on 17, an isotopologue on 7 and nothing
  on 21.
- **12 on F2 go from candidate to assigned.** Each is a nitrate cluster whose
  only other reading is a radical through the carbonate channel - C5H10O5
  through `+NO3-` is the radical C4H10NO5 through `+CO3-` - which the nitrogen
  rule counted as a rival because carbonate carries no nitrogen. A radical is no
  rival (decision 20), so they stand at the tier their evidence earned. The
  reference confirms 2, commits another formula on 2 and nothing on 8. Several
  are oxygen-rich formulas that the time-of-flight evidence alone holds at
  assigned, C19H18O24 and C39H39NO25 among them: a question for that evidence,
  not for this rule.

#### What the rules now say

- **The same-ion rule.** Over the 43 runs, 4,145 rows' ions also read as a
  molecule with another nitrogen count and 566 as one with the same, most of
  them already under the top band on their evidence; 2,265 are capped. A second
  channel settles 4,090 more and a radical reading 7,834. No row of a target
  library has an ion that reads another way.
- **The row's own reading** is listed as another reading of its ion on no row;
  the round before listed it on 265, all on C.
- **The band.** 14,855 monoisotopic rows sit under the top band and every one
  names it first; none of the 17,713 at the top band does. None records it as a
  cap: the band is where the row stood before any rule.
- **The minor-channel cap** holds C's declared carbonate channel as the
  profile's secondary one. 174 rows on C carry it, and 170 stand at candidate
  on it alone: untargeted carbonate readings of closed-shell molecules with no
  second channel and no isotopologue, at evidence 0.75 to 1.0, 167 of them
  with a radical as the only other reading of their ion. Let out of the cap
  they stood at assigned, and the reference commits the same formula on 96 of
  them, at its own candidate tier on 94; it reads 46 as the same ion split
  another way and commits nothing on 23. The brightest is C11H20O12 at m/z
  404 on every sample. The cap holds 3,030 rows on the other sets.
- **The inspector's card** for set A's dimethylformamide `[M+H]+`, read through
  the API: the band first ("evidence 8% (fit 8% x plausibility 100%) is under
  the candidate band of 45%"), then the second channel, the other reading
  settled ("the same ion also reads as C3H4O through +NH4+; C3H7NO is also
  committed through +(CH4N2O)H+, which settles it") and no close rival; and the
  background contaminants list's N,N-dimethylformamide as its reference-list
  match, the one record that names the formula. No list on the testbed holds
  C3H4O, so its reading carries no name.

#### The gate, read rather than targeted

Against the refreshed reference (decision 20):

| set | assigned | G1 | G1 conditioned (n) | G2 formula / ion |
|---|---|---|---|---|
| A | 1012 -> 999 | 6.1 -> 6.2 | 2.8 (977 -> 964) | 95.2 / 96.6 |
| B | 4078 -> 4021 | 32.7 -> 32.0 | 10.2 -> 9.7 (3057 -> 3026) | 93.8 / 94.8 |
| C | 615 | 10.9 | 1.1 (554) | 93.7 / 93.7 |
| C2 | 313 | 19.8 | 2.7 (258) | 87.2 / 87.2 |
| D | 898 -> 895 | 7.8 -> 7.7 | 1.7 (842 -> 840) | 85.0 / 86.6 |
| E | 235 | 66.0 | 13.0 (92) | 54.4 / 56.8 |
| F1 | 695 -> 693 | 77.8 | 6.7 -> 6.1 (165 -> 164) | 41.6 / 42.1 |
| F2 | 721 -> 733 | 83.9 | 17.1 -> 18.1 (140 -> 144) | 41.2 / 43.1 |

G2 is identical on every set. G5, G7 and G8 are 0, G6 at assigned falls on B
from 51 to 44, and all 38,190 committed rows carry their reasons.

#### Verify, item by item

- **Tests at the head.**
  - The whole backend suite passes: 3,618 passed and 6 skipped. The
    reference library passes: 207.
  - The frontend unit suite passes: 1,282 tests in 112 files.
  - ruff and the strict docs build are clean.
- **Reversions.** Each new backend line was reverted and its tests re-run, 28
  of 28 red: the channel searched once, each part of the same-ion rule and what
  settles it, the band and its place and precision, the rule set's 6, the bands
  reaching the pass, and the detail read's names, their licence filter, bound
  and failure. So are twelve reversions in the frontend, 12 of 12. The review's
  fixes add 23 of 23 in the backend - the channel searched once and kept
  secondary, in the run and the batch search alike, the nitrogen rival first,
  the own-channel guard, the own neutral and a labelled analyte left out, the
  band's `caps`, the lookup's scope and count, and the bands stated once - and
  14 of 14 in the frontend.
- **The round.** 43 of 43 runs completed on the head's build. No owner changes;
  the tier moves are the ones above.
- **G7 and the tier reasons** were read from the store over every committed row
  of the round.

## Not in this plan

peaky's residual explainer, ladder gap-fill, labelled-reagent rescue and
certified-neutral passes (77 peaks on the testbed; revisit with the data
after stage 4); retention-time and MS2 evidence (their own designs); the
reagent axis' eventual move into `IonizationSetup`
([ionization_method_config.md](ionization_method_config.md)).

## Related documents

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
