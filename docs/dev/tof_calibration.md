# TOF mass calibration

- **Status:** building
- **As of:** 2026-10-29
- **Tracked in:** [TOF mass calibration](https://linear.app/ultra-trace/project/tof-mass-calibration-8271ef05d142) (Linear, MAS-171 to MAS-197)
- **What:** TOF mass calibration across the analysed range: measure, fix calibrants, model, re-calibrate.

*The plan to give every TOF file a calibration that holds across the mass range it is analysed
over: measure each instrument, fix its calibrant sets, add a three-parameter model and coverage
checks to the calibration node, and re-calibrate the TOF data already stored. The work is tracked
in the Linear project
[TOF mass calibration](https://linear.app/ultra-trace/project/tof-mass-calibration-8271ef05d142);
this note is its record.*

**Status.** Milestone 0 (measure) is done: the assessment that started the project, the audit
tool and the fleet baseline (section 2). Of the calibrant sets (milestone 1), TOF H's has been
corrected, and TOF M's and site B's were measured and kept. Of the node's changes (milestone 2),
the record that says what a fit was made on is in (MAS-182); the rest are still to come. Sites
are named by letter and instruments by neutral labels (TOF H, TOF M, B1–B3) throughout, as
everywhere in this repository.

## 1. The problem

On 2026-10-19 an assessment of one customer TOF (site A, TOF H: positive-ion propylamine CIMS,
R ≈ 2,300–2,900 FWHM) followed up the customer's report that the instrument read low at higher
masses. 278 acquisitions from one week were refit offline from their own peak TOF indices with the
node's rules, and the same files were ingested on a test instance whose node reproduced the
offline fits to 0.01 ppm. A composition-free check measured the axis without trusting any single
identity: the offset of strong peaks from their nearest CcHhOo·C3H10N+ formula, whose members are
36.4 mDa apart.

- **The axis was 10–21 ppm low at 160–250 Th** while 40–150 Th held within ±5 ppm, and the error
  drifted over the week. The calibrant set (the bare reagent, C3H4, C3H9N, C3H8N2O, (C3H9N)2)
  stopped at 148 Th, so everything above was extrapolated.
- **The top calibrant was compromised.** C6H18N3O+ (148.144) sits 8 mDa from the ¹³C peak of
  C7H19N2O+ and read 4–5 ppm high. The listed (C3H9N)3H+ at 178 Th never forms, so the set only
  looked as if it reached 178 Th.
- **228 Th fixes it.** The customer's C10H16O2, seen as its C3H7NH3+ adduct (C13H26NO2+,
  228.1958), has S/N ≈ 90 in every file and is the only peak at its nominal mass. Anchoring on it
  centres the 200–250 Th family within about 3 ppm on every day (old set: 228 Th at −11.9 ppm
  median, the family at −11 ppm; corrected set: −2.4 and −3 ppm).
- **246 Th is not a calibrant.** The customer's C10H18O3 adduct (C13H28NO3+, 246.2064) merges with
  C9H14O4·C3H7NH3+ (246.170), 36 mDa away and unresolved at this resolution. It reads about 13 ppm
  low against the 228 Th axis and −4 to −19 ppm from day to day; 248 Th drifts by 35 ppm for the
  same reason.
- **A compound brings every ion of the mode.** Plain C10H16O2 in the collection also made its
  [M+H]+ (169.122) a calibrant, and a different peak sits 45 ppm from it in 95% of files. Entering
  the whole cluster as a formula (C13H25NO2) avoids the side ion.
- **The two-parameter model leaves a bow** even on a well-spread set: −8 ppm at 41–58 Th and +2 to
  +4 ppm at 100–150 Th. A three-parameter fit takes 41–58 Th to −4 ppm and the held-out RMS at
  86–147 Th from 2.5 to 2.1 ppm.

The old record looked healthy throughout: 6 points, a post-fit residual of 3–5 ppm, status `ok`.
Nothing in the record or the UI could show that the fit covered 41–148 Th of a file whose strong
peaks reached ~450 Th.

The same shape is on every TOF in the fleet (next section): calibrant sets end at 125–241 Th on
axes that reach 628–1,381 Th.

**Goal.** Every TOF file carries a calibration that holds across the mass range it is analysed
over; its record says what it was fitted on and how good it is; and existing data is re-calibrated
once the pipeline is updated.

## 2. The fleet on 2026-10-19

| Site | Instrument | Files (last 30 d) | Fitted by the node | Calibrant span vs axis |
| -- | -- | -- | -- | -- |
| A | TOF H, propylamine CIMS (+) | 957 (955) | all; corrected set from 10-17 on | 41–228 Th of 10–628 |
| A | TOF M, MION (−): nitrate, bromide/nitrate | 2,720 (1,070) | 2,693 | 46–188 / 62–241 Th of 10–998 |
| B | B1, APi (−): nitrate, bromide, ambient | 19,335 (3,974) | 5,630, since 2026-08-18 | 17–188 / 17–241 Th of ~950; ambient none |
| B | B2, MION (−): nitrate, bromide, ambient, zero | 19,482 (3,982) | 4,082, since 2026-08-18 | 62–188 (3 ions) / 62–241 Th of ~1,100; ambient, zero none |
| B | B3 (−): nitrate | 6,394 (722) | 1,488, since 2026-08-18 | 62–188 Th (3 ions) of ~1,385 |
| internal | five test TOFs | 2,167 (0) | none | legacy records |

- Before 2026-08-18, TOF files carry only the acquisition software's calibration (a record without
  a quality block): about 30,600 files on site B.
- Site B's ambient and zero modes have no calibrant collection: 3,400 files recorded `unfitted`
  since 2026-09-18, plus the older ones. Those raw axes are typically 90–130 ppm off (the median
  pre-fit error of the fitted files on the same instruments).
- The raw axes are far off before the node's fit everywhere: median pre-fit error 75 ppm on TOF M,
  93 on B1, 128 on B2, 21 on B3.
- The three-ion nitrate sets of B2 and B3 ((HNO3)n·NO3−, n = 0–2) leave no spare point: one bad
  match and the fit either fails or keeps the wrong one.
- The other sites run no TOF.

A census of the stored records by kind (the input to re-calibration, section 6):

| Site | TOF files | Node-fitted (`ok`/`poor`, since 2026-08-18) | Legacy (no quality block) | Unfitted (no calibrants) |
| -- | -- | -- | -- | -- |
| B | 45,211 | 11,197 | 30,589 (from 2025-09) | 3,422 (since 2026-09-18) |
| A | 3,677 | 3,649 | 27 | 1 |
| internal | 2,167 | 0 | 2,167 | 0 |

The re-calibration readiness inventory (milestone 4) is added here when it is run.

### The baseline on 2026-10-21

The audit tool on every TOF instrument and ionization mode of sites A and B (MAS-175). It ran
read-only on the sites, through the database's read-only role, with the audit code of develop at
`5bd66b2d1`. Per mode it read two runs: one file a day over the 60 days 2026-08-22 to 2026-10-20
(*sampled*), and every file of one recent day (*day*). That is 901 files.

- Errors are in ppm of the exact m/z, positive where the axis reads high. A cell gives the
  sampled run's median, then the day run's in parentheses.
- The fit read is the stored one: the axis the files carry.
- Modes are named by their reagent. The run names no modes, so B2's two ambient/zero modes are
  told apart only by order, and TOF M has a second nitrate mode of five files on one day.
- *Strong peaks* have S/N ≥ 20 and the mode's polarity. A band has them when the median file has
  five or more there.

**Each instrument and mode.** *Calibrant span*: the lowest and highest calibrant the node's refit
under the mode's collection kept (median file). *Raw axis*: the converter's axis before any fit,
read on the collection's and the cluster series' lines from 40 to 200 Th, as a range over the bands
and both runs. *Node fit error*: the stored record's mean absolute post-fit error, sampled / day.
*Refit = node fit*: the files whose refit lands within 0.01 ppm of the stored fit.

| Site | Instrument, mode | Files | Audited | Calibrant span | Axis to | Strong peaks to | Raw axis, 40–200 Th | Node fit error | Refit = node fit |
| -- | -- | --: | --: | -- | --: | -- | --: | --: | --: |
| A | TOF H, propylamine (+) | 1,186 | 10 / 142 | 41–228 | 628 | 200–300 | +5 to +20 | 3.8 / 3.4 | 3/10 / 138/140 |
| A | TOF M, bromide/nitrate | 1,469 | 23 / 48 | 62–241 | 998 | 700–1,000 | −81 to −77 | 0.6 / 0.5 | 23/23 / 48/48 |
| A | TOF M, nitrate | 1,022 | 9 / 111 | 46–188 | 998 | 700–1,000 | −71 to −63 | 2.3 / 2.9 | 9/9 / 110/111 |
| A | TOF M, nitrate, second mode | 5 | 1 / 5 | 62–176 | 998 | 200–300 | −78 to −73 | 0.6 / 1.0 | 1/1 / 5/5 |
| B | B1, nitrate | 2,702 | 58 / 48 | 62–125 | 950 | 300–500 | +60 to +91 | 3.5 / 3.4 | 56/58 / 48/48 |
| B | B1, bromide | 2,682 | 59 / 24 | 79–241 | 950 | 300–500 | +58 to +89 | 3.2 / 3.7 | 6/59 / 23/24 |
| B | B1, ambient | 2,679 | 1 / 0 | none | 950 | – | – | – / – | – / – |
| B | B2, nitrate | 1,984 | 60 / 34 | 62–188 | 1,099 | 500–700 | +99 to +134 | 0.7 / 0.8 | 59/59 / 34/34 |
| B | B2, bromide | 1,976 | 60 / 33 | 62–241 | 1,099 | 500–700 | +101 to +128 | 1.6 / 1.6 | 43/60 / 21/33 |
| B | B2, ambient/zero, first | 1,985 | 57 / 33 | none | 1,099 | 700–1,000 | +77 to +93 | – / – | – / – |
| B | B2, ambient/zero, second | 1,985 | 1 / 0 | none | 1,099 | 700–1,000 | – | – / – | – / – |
| B | B3, nitrate | 1,437 | 60 / 24 | 62–188 | 1,381 | 300–500 | −62 to −8 | 1.7 / 1.9 | 60/60 / 24/24 |

- B1's ambient mode and B2's second ambient/zero mode could not be read: the audit failed on every
  file but one with `ValueError` ([#2463](https://github.com/ultra-trace-systems/mascope/issues/2463)).
- The three ambient/zero modes have no fit. The audit of this baseline read their converter record
  as one, so it has no per-band error for them. It now counts such files as `unfitted` instead
  ([#2462](https://github.com/ultra-trace-systems/mascope/issues/2462)). Their raw axis is the axis
  they carry.
- Where the refit misses the node's fit, it mostly kept other points. Most misses are in the
  bromide modes of B1 and B2, 0.7–3.0 ppm apart over the calibrants (median per run). The rest
  are further apart: TOF H's sampled run by 17 ppm, as its set changed on 2026-10-17, inside the
  sampled window; two B1 nitrate files by 8 ppm; one TOF H file of the day by 9; one TOF M
  nitrate file by 35. Four misses are not another choice of points: the refit found other peaks
  in two B1 bromide files and one TOF H file, and did not fit one TOF H file of the day.

**Error per band on the calibrant and cluster lines.** The stored fit on every line the audit
matched: the mode's calibrants and the cluster series HSO4−·(H2SO4)n, NO3−·(HNO3)n and
Br−·(H2O)n, n = 0–4. Bands run up to the highest with strong peaks. ‡ marks lines above the top
calibrant that are a neighbour, not the ion (below).

| Instrument, mode | 40–100 | 100–150 | 150–200 | 200–300 | 300–500 | 500–700 | 700–1,000 |
| -- | --: | --: | --: | --: | --: | --: | --: |
| A TOF H, propylamine (+) | +0.6 (−6.0) | −2.7 (+3.6) | – (−2.0) | −15.1 (−2.4) |  |  |  |
| A TOF M, bromide/nitrate | −0.5 (−0.5) | −0.8 (−0.6) | −0.7 (−0.4) | +0.2 (+0.2) | +66.4‡ (–) | – | – |
| A TOF M, nitrate | −1.4 (−2.1) | +1.9 (+3.9) | −0.4 (−0.4) | – | – | – | – |
| A TOF M, nitrate, second mode | −0.1 (−0.5) | −0.3 (+0.4) | +0.6 (−1.2) | – |  |  |  |
| B B1, nitrate | +1.6 (−0.2) | +1.3 (+1.3) | +3.6 (–) | – | – |  |  |
| B B1, bromide | +2.0 (+3.4) | −6.9 (−6.8) | −3.0 (−4.3) | +3.9 (+2.3) | +95.2‡ (+95.7‡) |  |  |
| B B1, ambient | no fit |  |  |  |  |  |  |
| B B2, nitrate | +0.7 (+0.7) | −1.0 (−1.2) | +0.5 (+0.6) | – (+0.7) | – (+1.7) | – |  |
| B B2, bromide | +0.2 (+0.2) | −0.2 (−0.2) | +0.6 (+0.8) | +0.6 (+0.5) | +93.3‡ (+91.7‡) | – |  |
| B B2, ambient/zero, first | no fit |  |  |  |  |  |  |
| B B2, ambient/zero, second | no fit |  |  |  |  |  |  |
| B B3, nitrate | −1.5 (−1.7) | +2.5 (+2.8) | −1.2 (−1.3) | – | – |  |  |

**The family check.** The offset of strong peaks from the nearest CcHhOo member under the
reagent's mechanism, median [IQR], within ±30 ppm of a member. A flat distribution over that
window has an IQR of 30 ppm and says nothing.

| Instrument, mode | Family | 40–100 | 100–150 | 150–200 | 200–300 | 300–500 | 500–700 | 700–1,000 |
| -- | -- | --: | --: | --: | --: | --: | --: | --: |
| A TOF H, propylamine (+) | [M+C3H7NH3]+ | −3.1 [6] (+1.2 [9]) | −4.5 [16] (+2.1 [14]) | −2.7 [19] (−0.2 [20]) | −6.8 [25] (−2.3 [23]) |  |  |  |
| A TOF M, bromide/nitrate | [M+Br]- | – | +23.8 [23] (+17.9 [27]) | +11.8 [14] (+12.7 [33]) | +18.6 [25] (−1.8 [45]) | −3.5 [27] (−1.1 [28]) | −0.8 [17] (−1.2 [16]) | −0.2 [11] (+1.0 [13]) |
| A TOF M, nitrate | [M+NO3]- | – | −2.2 [2] (−1.9 [2]) | −3.5 [2] (−2.8 [2]) | −2.8 [2] (−1.7 [4]) | +0.5 [13] (+1.7 [20]) | +10.7 [28] (+5.8 [29]) | +19.4 [8] (+15.5 [27]) |
| A TOF M, nitrate, second mode | [M+NO3]- | – | – | – | −8.5 [21] (−9.0 [23]) |  |  |  |
| B B1, nitrate | [M+NO3]- | – | +2.0 [9] (+2.1 [11]) | +10.4 [8] (+9.9 [8]) | +16.2 [19] (+14.4 [35]) | −3.3 [19] (−5.4 [9]) |  |  |
| B B1, bromide | [M+Br]- | – | +17.1 [13] (+13.3 [23]) | +8.4 [13] (+9.0 [10]) | +15.7 [31] (+18.3 [27]) | −1.4 [26] (−1.5 [30]) |  |  |
| B B2, nitrate | [M+NO3]- | +7.0 [6] (+2.0 [9]) | +4.1 [8] (+3.0 [7]) | +1.2 [4] (+1.9 [7]) | +1.8 [5] (+0.8 [11]) | −2.1 [25] (−1.0 [29]) | −4.1 [14] (−4.4 [17]) |  |
| B B2, bromide | [M+Br]- | – | −10.4 [11] (−0.3 [12]) | +0.4 [7] (+0.1 [10]) | +1.4 [15] (−1.3 [23]) | −5.1 [17] (−2.1 [23]) | −4.9 [11] (−1.8 [15]) |  |
| B B3, nitrate | [M+NO3]- | – | +7.9 [7] (–) | +1.3 [12] (−2.4 [6]) | +7.9 [13] (+8.5 [18]) | +0.3 [30] (+2.3 [23]) |  |  |

**Each calibrant.** *Present*: the share of files whose refit matched it. *Kept*: the share of
fitted files whose fit kept it. *Residual*: its median error under the refit, or held out (\*)
where the fit never kept it. Sampled run, day run in parentheses.

<details><summary>Calibrants per instrument and mode</summary>

| Instrument, mode | Calibrant | Present | Kept | Residual |
| -- | -- | --: | --: | --: |
| A TOF H, propylamine (+) | C3H5+ 41.0 | 100% (99%) | 100% (88%) | −7.6 (−7.0) |
|  | C3H10N+ 60.1 | 100% (99%) | 100% (100%) | +1.3 (+0.5) |
|  | C6H14N+ 100.1 | 100% (99%) | 100% (100%) | +3.0 (+3.6) |
|  | C6H19N2+ 119.2 | 90% (99%) | 90% (96%) | +4.4 (+3.6) |
|  | C9H28N3+ 178.2 | – (4%) | – (2%) | – (−2.8) |
|  | C13H26NO2+ 228.2 | 100% (99%) | 100% (92%) | −2.5 (−2.4) |
|  | C16H35N2O2+ 287.3 | 10% (17%) | 10% (4%) | −4.7 (−10.0) |
|  | C16H35N2O2+ 288.3 | – (15%) | – (5%) | – (−1.4) |
| A TOF M, bromide/nitrate | NO3- 62.0 | 100% (100%) | 100% (100%) | +1.5 (+1.4) |
|  | Br- 78.9 | 100% (100%) | 100% (100%) | −0.6 (−0.5) |
|  | Br- 80.9 | 100% (100%) | 100% (100%) | −0.6 (−0.6) |
|  | BrNO3- 140.9 | 9% (–) | 0% (–) | +89.8* (–) |
|  | BrNO3- 142.9 | 17% (46%) | 0% (0%) | +85.5* (+53.5*) |
|  | Br2- 157.8 | 70% (100%) | 70% (100%) | +0.0 (−0.1) |
|  | Br2- 159.8 | 87% (98%) | 87% (98%) | −0.3 (−0.1) |
|  | Br2- 161.8 | 70% (48%) | 70% (48%) | −0.3 (−0.2) |
|  | Br3- 238.8 | 100% (100%) | 100% (100%) | +0.1 (+0.1) |
|  | Br3- 240.8 | 100% (100%) | 100% (100%) | +0.2 (+0.1) |
| A TOF M, nitrate | NO2- 46.0 | 100% (94%) | 100% (94%) | −0.6 (−0.8) |
|  | NO3- 62.0 | 100% (93%) | 100% (93%) | −1.9 (−2.8) |
|  | HN2O5- 109.0 | 44% (16%) | 0% (1%) | +36.2* (+9.2) |
|  | C2F3O2- 113.0 | 100% (98%) | 100% (98%) | +6.8 (+8.1) |
|  | HN2O6- 125.0 | 100% (100%) | 100% (99%) | −2.6 (−2.3) |
|  | C2HF3NO5- 176.0 | 100% (100%) | 100% (100%) | +0.2 (+0.5) |
|  | H2N3O9- 188.0 | 100% (95%) | 100% (95%) | −1.7 (−2.0) |
| A TOF M, nitrate, second mode | NO3- 62.0 | 100% (100%) | 100% (100%) | −1.0 (−1.5) |
|  | O3^N- 63.0 | 100% (100%) | 100% (100%) | +0.9 (+0.3) |
|  | C2F3O2- 113.0 | 100% (100%) | 100% (100%) | −0.3 (+1.5) |
|  | HNO6^N- 126.0 | 100% (100%) | 100% (100%) | +0.7 (+0.8) |
|  | C2HF3NO5- 176.0 | 100% (100%) | 100% (100%) | −0.2 (−1.1) |
| B B1, nitrate | NO3- 62.0 | 100% (100%) | 100% (100%) | +4.0 (+3.8) |
|  | H2NO4- 80.0 | 100% (100%) | 100% (100%) | −5.2 (−5.1) |
|  | HN2O6- 125.0 | 100% (100%) | 100% (100%) | +1.3 (+1.3) |
|  | H2N3O9- 188.0 | 9% (–) | 9% (–) | +3.6 (–) |
| B B1, bromide | Br- 78.9 | 100% (100%) | 100% (100%) | +4.9 (+4.2) |
|  | Br- 80.9 | 100% (100%) | 100% (100%) | +3.8 (+4.0) |
|  | BrH2O- 96.9 | 100% (100%) | 100% (100%) | −3.4 (−3.1) |
|  | BrH2O- 98.9 | 100% (100%) | 100% (100%) | −3.8 (−3.3) |
|  | Br2- 157.8 | 88% (79%) | 88% (79%) | −1.6 (−1.0) |
|  | Br2- 159.8 | 83% (75%) | 83% (75%) | −1.7 (−2.9) |
|  | Br2- 161.8 | 81% (96%) | 78% (96%) | −1.7 (−1.9) |
|  | Br3- 238.8 | 66% (83%) | 66% (83%) | +1.9 (+0.9) |
|  | Br3- 240.8 | 66% (88%) | 66% (88%) | +2.0 (+1.9) |
| B B2, nitrate | NO3- 62.0 | 98% (100%) | 100% (100%) | +0.6 (+0.7) |
|  | HN2O6- 125.0 | 98% (100%) | 100% (100%) | −1.0 (−1.2) |
|  | H2N3O9- 188.0 | 98% (100%) | 100% (100%) | +0.5 (+0.6) |
| B B2, bromide | NO3- 62.0 | 100% (100%) | 100% (100%) | +5.1 (+5.0) |
|  | Br- 78.9 | 100% (100%) | 100% (100%) | −1.8 (−2.0) |
|  | Br- 80.9 | 100% (100%) | 100% (100%) | −1.9 (−1.9) |
|  | BrHNO3- 141.9 | 100% (100%) | 100% (100%) | −0.9 (−0.7) |
|  | BrHNO3- 143.9 | 98% (100%) | 98% (100%) | −0.5 (−0.4) |
|  | Br2- 157.8 | 10% (12%) | 10% (12%) | −0.9 (−2.2) |
|  | Br2- 159.8 | 28% (33%) | 28% (33%) | −0.9 (−1.1) |
|  | Br2- 161.8 | 57% (45%) | 57% (45%) | −0.4 (−1.6) |
|  | Br3- 238.8 | 97% (100%) | 97% (100%) | +0.5 (+0.5) |
|  | Br3- 240.8 | 98% (100%) | 98% (100%) | +0.6 (+0.5) |
| B B3, nitrate | NO3- 62.0 | 100% (100%) | 100% (100%) | −1.5 (−1.7) |
|  | HN2O6- 125.0 | 100% (100%) | 100% (100%) | +2.5 (+2.8) |
|  | H2N3O9- 188.0 | 100% (100%) | 100% (100%) | −1.2 (−1.3) |

</details>

**What it shows.**

- **Inside its calibrant span every axis holds.** TOF M, B2 and B3 read within ±3 ppm on their
  calibrant and cluster lines, but for two calibrants listed below. B1 holds within ±8 ppm. Its
  bromide fit keeps Br− at +4 to +5 ppm and leaves Br−·(H2O)2–4 at −6 to −8 ppm: a bow like
  TOF H's. TOF H keeps the bow of section 1: 41 Th at −7 ppm, 100–119 Th at +3 to +4.
- **Every set stops at 125–241 Th; the strong peaks do not.** The axes reach 628–1,381 Th. Strong
  peaks reach the 200–300 Th band on TOF H, 300–500 on B1 and B3, 500–700 on B2 and 700–1,000 on
  TOF M. B1's nitrate set stops at 125 Th in practice: its 188 Th trimer is matched in 9% of the
  sampled files and in none of the day's.
- **Above the top calibrant, no matched line measures the axis.** In the bromide modes the cluster
  lines matched there read +67 to +95 ppm. They are neighbours.
  (HNO3)3·NO3− at 251 Th reads +67, +71 and +71 ppm in the bromide modes of TOF M, B1 and B2,
  whose raw axes and calibrants differ. Its IQR is 2–4 ppm on TOF M and B2, 15–18 on B1. It reads +0.7 ppm in B2's nitrate mode, where it is the
  ion (six files). HSO4−·H2SO4 at 195 Th reads −90 ppm on TOF M, between calibrants (Br2−, Br3−)
  that sit within 0.3 ppm.
- **The family check reads the axis to about 300 Th in the nitrate modes.** TOF M's nitrate axis
  holds at −1.7 to −3.5 ppm over 100–300 Th (IQR 2–4). B2's holds at +0.8 to +1.9 ppm over
  150–300 Th (IQR 4–11). B1's nitrate axis drifts above its 125 Th top calibrant: +10 ppm at
  150–200 Th (IQR 8) on both runs, +14 to +16 at 200–300 (IQR 19–35). B3 reads +8 ppm at
  200–300 Th on both runs (IQR 13–18). In the bromide modes the check disagrees with the
  calibrants below 300 Th: TOF M's [M+Br]− family reads +12 to +24 ppm where its calibrants read
  within 1 ppm. There it measures peaks that are not CHO·Br−.
- **Above 300 Th the family check is a hint at most.** Members of one nominal mass are 36.4 mDa
  apart, 36–120 ppm over 300–1,000 Th. An offset of a whole spacing reads as none, and the offsets
  spread widely (IQR 8–30 ppm). With that caveat: at 500–1,000 Th TOF M reads −1 to +1 ppm in its
  bromide/nitrate mode, whose set reaches 241 Th (IQR 11–17), and +6 to +19 ppm in its nitrate
  mode, whose set stops at 188 Th. Elsewhere above 300 Th the medians sit within ±6 ppm, with
  IQRs of 9–30 ppm.
- **The raw axes are far off and move.** Before the fit: TOF M −63 to −81 ppm, TOF H +5 to +20,
  B1 +58 to +91, B2 +99 to +134, B3 −8 to −62 (the table below).
  - Within a run, TOF M's axis is flat to within 6 ppm over 40–200 Th. TOF H, B1 and B2 vary by
    4–14 ppm across the three bands, with no shape in common.
  - B3's axis slopes: it rises by 28–32 ppm from 40–100 to 150–200 Th, in both runs. Its range
    above is that slope plus a day shift, not one offset.
  - B1, B2 and B3 read 22–30 ppm lower on the day than over the 60 days, in every band but one:
    B2's bromide mode at 150–200 Th reads 11 ppm lower.
  - B2's first ambient/zero mode has no fit, so its files carry the raw axis: +77 to +93 ppm.

  The raw axis per band, sampled run, day run in parentheses, in ppm. *Range within the run*: the
  highest band minus the lowest. A band needs five matched lines.

  | Instrument, mode | 40–100 | 100–150 | 150–200 | Range within the run |
  | -- | --: | --: | --: | --: |
  | A TOF H, propylamine (+) | +5 (+15) | +15 (+20) | – (+6) | 10 (14) |
  | A TOF M, bromide/nitrate | −79 (−77) | −80 (−79) | −81 (−80) | 2 (2) |
  | A TOF M, nitrate | −69 (−67) | −67 (−63) | −71 (−69) | 4 (6) |
  | A TOF M, nitrate, second mode | – (−73) | – (−76) | – (−78) | – (5) |
  | B B1, nitrate | +91 (+67) | +82 (+60) | +81 (–) | 10 (6) |
  | B B1, bromide | +89 (+62) | +83 (+58) | +85 (+61) | 6 (4) |
  | B B2, nitrate | +134 (+105) | +127 (+99) | +125 (+100) | 9 (6) |
  | B B2, bromide | +128 (+101) | +124 (+101) | +121 (+110) | 7 (10) |
  | B B2, ambient/zero, first | +87 (+77) | +93 (+83) | – (+81) | 6 (5) |
  | B B3, nitrate | −37 (−62) | −13 (−36) | −8 (−30) | 28 (32) |
- **Calibrants that do not work** (milestone 1):
  - BrNO3− in TOF M's bromide/nitrate set: matched in 9–46% of files, never kept, +54 to +90 ppm
    where matched.
  - HN2O5− (109 Th) in TOF M's nitrate set: matched in 16–44%, held out at +36 ppm.
  - TFA (C2F3O2−, 113 Th) in the same set: kept at +7 to +8 ppm while its neighbours sit within
    ±3 ppm. In TOF M's second nitrate mode it reads within ±1.5.
  - NO3− in B2's bromide set: kept at +5 ppm. Br2− there is matched in 10–57% of files.
  - (C3H9N)3H+ (178 Th) and C16H35N2O2+ (287 Th) in TOF H's set: matched in 4–17% of files.

**Against the brief's check.**

- *A reading in every band from 40 Th to the top band with strong peaks*: 9 of the 12 rows. The
  exceptions are the three ambient/zero modes. Above the top calibrant the reading is the family
  offset, which is ambiguous above 300 Th.
- *The day run within 1 ppm of the sampled run, per band*: on the calibrant and cluster lines, 20
  of 29 band cells. Every cell of TOF M's bromide/nitrate mode, B2 and B3 agrees. The misses:
  - TOF H in all three bands, by 6–13 ppm: the sampled run straddles the set change of
    2026-10-17.
  - B1 in four cells, by 1.3–1.9 ppm.
  - TOF M's nitrate modes in two cells, by 1.8–2.0 ppm.

  The family offsets agree in 14 of 39. With an IQR of 10–45 ppm their median moves by a few ppm
  between runs. Where it moves by 10–20 ppm (TOF M bromide/nitrate at 200–300 Th, B2 bromide at
  100–150), what changed is which peaks are there.

**Not measured.** An unambiguous reading of the axis above 300 Th on any instrument, where TOF M
and B2 have most of their strong peaks
([#2464](https://github.com/ultra-trace-systems/mascope/issues/2464)). The axes of B1's ambient
mode and of B2's second ambient/zero mode (#2463).

### Site B's calibrant sets on 2026-10-29

A screen of site B's five nitrate and bromide modes for calibrants above the top of their sets
(MAS-180). It ran read-only on the site, release v1.13.0, through the database's read-only role,
with the audit code of develop at `46e303c5e`. Per mode it read two files a day over the 60 days
2026-08-30 to 10-28 (*sampled*), every file of one recent day (*day*), and the files parked at
`calibration_failed`. That is 711 files.

- Each file was refit under its mode's collection, and under the collection with the passing
  candidates added, on the axis the file carries. On the day runs every refit reproduced the
  stored fit but one. Over the 60 days, 90 of B1 bromide's 102 and 28 of B2 bromide's 112 stored
  fits had kept other points; the numbers below are the refit's.
- Cells give the sampled run, then the day run in parentheses, in ppm.

**The rules.** A candidate passes when it passes all six, in each run:

- *presence*: all its lines on peaks of S/N ≥ 10 in at least 90% of the files;
- *stable*: the IQR of its offset over the files at most 3 ppm;
- *neighbour*: a peak of a tenth of its intensity within one FWHM in at most 10% of the files;
- *¹³C*: the ¹³C line of the peak 1 Da below moves its centroid by more than 1 ppm in at most 10%
  of the files;
- *side ion*: in at most 10% of the files does another ion of its compound read more than 3 ppm
  off while the 15 ppm rule would keep it;
- *agrees*: its median offset within 2 ppm, or its IQR, of the other passing candidates within a
  factor 1.5 in m/z; within 5 ppm of the axis where there are fewer than two of those.

The candidates were the CcHhOo members under each mechanism of the mode in at least half the
sampled files, from 150 Th up, and named ions: HSO4−·(H2SO4)n, n = 0–3; HSO4−·HNO3 and
(HNO3)3·NO3− (251 Th) in the nitrate modes; Br3−, Br−·(H2O)n, n = 1–4, Br−·HNO3 and Br−·H2SO4 in
the bromide modes.

**No ion above a set's top calibrant passes, in any mode.** All five sets stay as they are, on the
plan owner's word (MAS-584).

- HSO4−·(H2SO4)n are absent on all three instruments. HSO4− itself is in 0–9% of the files, and
  in 67% of B2 nitrate's, where it reads +7 ppm with an IQR of 12.
- (HNO3)3·NO3− (251 Th) and Br−·H2SO4 (179 Th) are absent, or in at most 3% of the sampled files.
  On the day the 251 Th line is in 15% of B2 nitrate's files, at +4.1 ppm with an IQR of 17, and
  in 4% of B3's. Where a line is matched at 251 Th in the bromide modes it reads +70 ppm: a
  neighbour, as in the baseline.
- Br3− (241 Th) already tops both bromide sets, and Br−·HNO3 is already in B2's.
- Above the top only CHO members are left. None passes on B1 or B3. On B2 nitrate four pass, at
  164–234 Th, and they fail on new files (below).

| Instrument, mode | Calibrant span, Th (points) | Strong peaks to | Its calibrants | Passes above the top |
| -- | -- | --: | -- | -- |
| B1, bromide | 79–241 (8) | 500 | −3.4 to +4.2; Br−·(H2O)2–4 −6 to −7, held out | none |
| B1, nitrate | 62–125 (3) | 500 | −5.1 to +3.9 | none |
| B2, bromide | 62–241 (8–10) | 700 | within ±2 from 79 Th; NO3− +5.2 | none; Br−·H2O, inside the span |
| B2, nitrate | 62–188 (3) | 700 | −1.1 to +0.6 | four CHO adducts, 164–234 Th |
| B3, nitrate | 62–188 (3) | 500 | −1.5 to +2.5 | none |

- **B1 bromide.** An ion reads the mean of its isotope lines. Br− reads +4.2 (+4.1), Br−·H2O
  −3.4 (−3.2), Br2− −1.6 (−1.9), Br3− +2.2 (+1.4). Br−·(H2O)2–4 at 115–151 Th, held out, read −6
  to −7. That bow is the two-parameter model's (MAS-183), not the set's. Br−·(H2O)2–4 are in
  94–100% of the files but lie below the top. Seven CHO members are in 51–86% of the files. The
  bromide adducts among them, at 205–259 Th, read +19 to +24 ppm: they are other ions.
- **B1 nitrate.** NO3− reads +3.9, H2NO4− −5.1, HN2O6− +1.3. The 188 Th trimer is kept in 12% of
  the sampled files and in none of the day's 48. The mode's 23 CHO members read −21 to +21, with
  IQRs of 5–16. Most NO3− adducts read high and most [M−H]− ions low, so they are no yardstick.
- **B2 bromide.** Everything from 79 to 241 Th reads within ±2; NO3− reads +5.2 at 62 Th. Br−·H2O
  (97 Th) passes every rule (below). No CHO member passes.
- **B2 nitrate.** The three ions read +0.6, −1.1 and +0.5. Held out, NO3− adducts at 164–234 Th
  read +0.8 to +1.9 (+1.1 to +2.1). Ions at 248–294 Th that miss a rule read −1.0 to +2.5 (−6.4
  to +4.8, IQR up to 16), so they are no yardstick. Today's set holds the axis within ±2.1 ppm to
  234 Th, and nothing reads it above.
- **B3 nitrate.** −1.5, +2.5 and −1.2. Nothing passes. The strongest NO3− adducts at 224–260 Th
  read +5 to +11 on the day, while their [M−H]− ions at 161–197 Th read −8 to −1: the pairs
  disagree.

**How a new file is calibrated.** Each file was also calibrated as the pipeline calibrates a new
one: from the converter's axis, in up to seven attempts that double the tolerance and widen the
refine window (15/100 ppm first, 240/241 on the fifth), judged by the quality bar. *Converter's
axis*: its median offset before any fit. *Attempt*: the attempt that fits, under today's set, as
counts of files.

| Instrument, mode | Converter's axis, ppm | Attempt that fits | Clears the bar | Kept another peak |
| -- | --: | -- | -- | -- |
| B1, bromide | +88 (+60) | 1 in 83, 4 in 19 of 102 (1) | all | 10 of 102 (1 of 24) |
| B1, nitrate | +87 (+61) | 1 in 52, 4–5 in 50 of 102 (1) | all | none |
| B2, bromide | +127 (+172) | 4–5 in 101 of 112 (5) | all | none |
| B2, nitrate | +128 (+170) | 4–5 in 105 of 110 (5) | all; 2 of 112 have too few calibrant peaks | none |
| B3, nitrate | −15 (−10) | 1 (1) | all | none |

- B2's axis is further off than the first attempt's 100 ppm window in 101–103 of the 112 sampled
  files, and in every file of the day. So a new B2 file is calibrated at a tolerance of 120 to
  240 ppm. The site's records agree: 67% of B2 bromide's and 82% of B2 nitrate's fits in the
  window were made at 240 ppm.
- Today's sets still land exactly on B2, because their ions have no neighbour inside those
  windows.

**B2 nitrate, the rules' proposal.** Four compounds: C3H4O4, C4H6O3, C7H10O4 and C8H12O4. Their
NO3− adducts at 164–234 Th are in 91–100% of the files, with an IQR of 2.5 ppm at most. Refit on
the axis the files carry, the set keeps 8–11 points to 234 Th and moves the 200–300 Th band from
+1.8 (+2.2) to +0.4 (+0.3) ppm.

- **It fails on new files.** On the day, all 33 files were fitted on the first attempt, on wrong
  peaks between 157 and 234 Th, 233 ppm off, and every one cleared the quality bar. Over the 60
  days, 20 of 111 files kept a wrong peak, and the bar flagged 13
  ([#2776](https://github.com/ultra-trace-systems/mascope/issues/2776)).
- On the day, the compounds' [M−H]− ions at 101–171 Th are kept off the axis in 18–70% of the
  files: a compound brings an ion for every mechanism of the mode (MAS-186).
- **The lean pair**, C3H4O4 and C7H10O4 (6–7 points to 220 Th), lands where it should. It gains up
  to 1.3 ppm at 200–300 Th but costs 0.8–1.3 ppm at NO3−, and 6 of the day's 33 files and 4 of
  111 sampled miss the bar (`residual`).
- B2's and B3's nitrate collections hold the same three compounds, and each is used by two modes:
  most likely they are one collection. An edit for B2 would reach B3, where these ions fail the
  rules.

**B2 bromide, H2O.** It adds Br−·H2O at 97 and 99 Th, inside the span. It is in every file at +0.1
to +0.2 ppm (IQR at most 1.0) and passes every rule in both runs, taking the fit from 8 to 10
points (10 to 12 on the day). New files fit on the same attempts, all clear the bar and none keeps
another peak. No line moves by more than 0.1 ppm, so it was not added.

**The parked files.** 954 B2 files are at `calibration_failed`, from 2026-09-24 to 10-24, B2's
last file (804 at the baseline's count).

- 950 are in one ambient/zero mode and 4 in the other. Each has the same reason: the mode has no
  calibration collection. The file keeps the converter's record, and the pipeline matches no file
  on that.
- B2's nitrate and bromide modes have no parked file, so no set changes the count. All 954 stay
  parked until calibration transfer (MAS-187) gives them a neighbouring fit, as MAS-178 chose for
  multi-scheme instruments.
- B1 has two: one in its ambient mode, for the same reason, and one nitrate file with too few
  calibrant peaks under every set.

**Against the brief's check.**

- *Each band up to the highest with strong peaks within ±5 ppm*: no set can meet it. No mode has a
  reading above its top calibrant but B2 nitrate, which holds within ±2.1 ppm to 234 Th on
  held-out ions. Inside its span B1 bromide misses ±5 ppm by the model's bow (MAS-183). Elsewhere
  only two calibrants sit just outside: H2NO4− on B1 nitrate at −5.1 and NO3− on B2 bromide at
  +5.2.
- *The `calibration_failed` count on B2's new files drops to its explained remainder*: all 954 are
  explained, and none is in a mode a set calibrates (MAS-187).

**Not measured.** A reading of the axis above the top calibrant on B1, B3 and B2 bromide, and
above 234 Th on B2 nitrate. Why most files of B2's other ambient/zero mode, and of B1's, end
`done`: the screen reads no file of a mode without a collection.

## 3. How TOF calibration works today

The node is `TofCalibrationHandler` in
[`calibration_mz_fit.py`](../../server/backend/src/mascope_backend/api/controllers/calibration/lib/calibration_mz_fit.py);
the model is [`mascope_tofwerk.calibration`](../../libraries/tofwerk/src/mascope_tofwerk/calibration.py);
the record, the quality verdict and apply are in
[`calibration_controller.py`](../../server/backend/src/mascope_backend/api/controllers/calibration/calibration_controller.py);
the constants are in
[`config.py`](../../server/backend/src/mascope_backend/api/models/calibration/config.py)
(`calibration_config`). The user-facing description is
[`docs/user/how-it-works/calibration.md`](../user/how-it-works/calibration.md).

### Matching

The matching steps and their constants - the 0.15 abundance floor, S/N >= 10, the 100 ppm
refine window, the FWHM overlap drop, one reading per peak, three points at least - are in
[calibration.md](../user/how-it-works/calibration.md#candidate-selection-and-matching) since
2026-10-22. In the code: `DEFAULT_ISOTOPE_ABUNDANCE_MIN`; `load_peak_data` and `drop_bad_peaks`
(weak and satellite peaks out); `TOF_SNR_THRESHOLD`; `TOF_DEFAULT_REFINE_WINDOW`;
`_remove_overlapping_mzs` (the Orbitrap's local-dominance guard is off for TOF);
`_match_max_in_range` and `_one_reading_per_peak`.

A peak that sits beside an unresolved neighbour, or a ¹³C line closer than the overlap test
catches, is matched like any other: nothing at this stage tells a clean calibrant from a
compromised one.

### The fit

`mz_calibrate` fits Tofwerk's mode 0, `i = p1·√m + p2`, where `i` is the TOF sample index: a
`scipy.optimize.least_squares` fit (trust-region, soft-L1 loss) started from the linear
least-squares solution of `i` against `√m`. The record is `{"mode": 0, "par": [p1, p2]}`.
`tof_to_mass` already evaluates modes 0–5, so a record in another mode applies; only the fit is
limited to mode 0.

A fit needs at least three matched points (`TOF_MINIMUM_CALIBRATION_POINTS`); fewer leaves the file
with the warning "Not enough calibration peaks". A fit with a zero coefficient fails.

### Selection

The fit decides which matches to keep by the 15 ppm residual rule (`TOF_MZ_ERROR_TOLERANCE`):

- **More than five matches** (`_select_from_many_matches`): fit all of them, keep those whose
  post-fit residual is within 15 ppm, and refit on those. One pass; if none is within 15 ppm all are
  kept.
- **Five or fewer** (`_select_from_few_matches`): if the fit on all of them leaves every point
  within 15 ppm, keep all. Otherwise search every subset exactly, from the largest down to three
  points, for one whose retained points are all within 15 ppm and whose excluded points are all
  outside it. Among such subsets of one size, the lowest mean retained residual wins, then the
  smallest mean pre-fit correction. The largest size with a valid subset ends the search; none
  found skips the calibration.

Two consequences matter for this plan. With three ions there is no spare point to outvote a bad
match. And a model with as many parameters as retained points fits them exactly, with zero
residual by construction, so the subset ranking would always prefer it: a three-parameter model
cannot be added without changing the search (milestone 2).

A fit whose mean post-fit residual exceeds 15 ppm, or whose points all come from one ion, warns
"Calibration inaccurate".

### The record's quality block

`calibration_mz_fit` attaches a `quality` block (`fit_quality`) to the fit, and apply stores it in
`sample_file.mz_calibration`:

- `n_points`, `n_ions`;
- `pre_fit_mz_error_ppm`, `post_fit_mz_error_ppm`: mean absolute error of the retained points
  before and after the fit;
- `calibrant_to_tic`;
- `mz_error_tolerance`, `refine_window`.

The fit is sealed (`seal_fit`) and judged against the quality bar (`calibration_quality_issues`):
for TOF a post-fit residual of at most 10 ppm (`TOF_MAX_POST_FIT_MZ_ERROR_PPM`), at least three
points from at least two ions, and no calibrant-to-TIC floor. The deployment's
`calibration_quality_gate` (`warn` by default, or `enforce`) decides whether a fit below the bar is
stored `verified`; the record's `status` is `ok` or `poor`. A pre-fit error above 50 ppm
(`TOF_ACQUISITION_DRIFT_WARNING_PPM`) is reported as acquisition drift, at most once a day per
instrument.

Since MAS-182 the block of a TOF fit also says what the fit was made on: every line it chose from,
kept or left out, with its ion, residual and S/N (`points`); the m/z span of the lines kept
(`span`); the collection's id and a hash of its isotopes (`collection`); the `model`; and the
`node_version` ([calibration.md](../user/how-it-works/calibration.md#what-a-tof-record-says)).
A record fitted before then keeps only the counts and means. Two other kinds of record
exist: the converter's `{"mode": 0, "par": [...]}` with the acquisition software's coefficients,
stamped `status: "unfitted"` since 2026-09-18 and recognised without the stamp before that
(`is_unfitted_record`), and the legacy records of the same shape, with or without
`"verified": true`.

### Apply

That a TOF fit is absolute - the axis recomputed from the stored TOF index, so a refit needs no
re-detection, an old fit is restored from its parameters and a fit applied twice changes nothing -
and what applying one does to the file's matches and batches are in
[calibration.md](../user/how-it-works/calibration.md#time-of-flight-tof-multi-point-calibration)
since 2026-10-22. In the code: `TofCalibrationHandler._apply_sync` writes the axis to the `signal`
and every `sum_signal*` store and recomputes the peaks' m/z from their stored `tof` coordinate in
`peak_timeseries`; `calibration_mz_apply` then clears the file's matches and sets its
non-acquisition batches to `rematch`. Editing the calibration collection a mode already uses only
sets its batches to `rematch` (MAS-188).

## 4. The plan

The project runs in five milestones. Every change is judged against the baseline with the audit
tool, and accuracy is quoted in ppm per mass band, not as one RMS.

### 0 · Measure every TOF

- **The assessment** of TOF H (section 1). Done (MAS-171).
- **An audit tool** that refits stored peaks exactly as the node does, read-only: it reads each
  file's peaks from `peak_timeseries.zarr` and its stored record, matches and selects with the
  node's rules (reusing them, factored out of the handler if needed), refits under named calibrant
  sets and models, and reports per ion and per mass band the median and IQR error, held-out
  accuracy, each calibrant's presence rate, the composition-free family offset for the mode's
  reagent (CHO·C3H10N+, CHO·NO3−, CHO·Br−, bare CHO) and known cluster series. Its mode-0 refit
  must equal the node's stored fit to 0.01 ppm (MAS-174). It is the maintenance script
  `audit_tof_calibration` (how to run it and its `TOF_AUDIT_*` variables:
  [maintaining.md](../maintaining.md#auditing-a-tof-instruments-mass-calibration)): a subclass of `TofCalibrationHandler` that
  swaps only what the handler reads for reads that write nothing, so the matching, selection and
  fit are the node's own. The family offset reads strong peaks (S/N ≥ 20) against CcHhOo neutrals
  of up to three double bonds and rings under each mechanism of the mode, where members of one
  nominal mass are 36.4 mDa apart; with four or more, two members can sit 15.3 mDa apart.
  A peak counts within ±30 ppm of its member, or half the member's distance to its nearest
  neighbour where that is narrower (above about 600 Th, and wherever the two bromine lines'
  lattices interleave, 17.7 mDa apart). A band's offset is given only where its IQR is at most
  half that window; offsets spread evenly over ±w have an IQR of w, and the report says "no
  reading" there (#2464, MAS-274).
- **This note** (MAS-173).
- **The fleet baseline**: the audit tool on every instrument × mode, a sample per day over 60 days
  plus one complete day, in bands 40–100, 100–150, 150–200, 200–300, 300–500 Th and above where
  strong peaks exist. Done: section 2 (MAS-175). It has no unambiguous reading above 300 Th
  ([#2464](https://github.com/ultra-trace-systems/mascope/issues/2464)).

### 1 · Calibrant sets per instrument

Configuration on the sites, applied on the plan owner's word and measured before and after.

- **TOF H**: the corrected set (bare reagent, C3H4, C3H9N, (C3H9N)2 and the 228 Th adduct entered
  as the cluster C13H25NO2), 5 points at 41, 60, 100, 119 and 228 Th, post-fit 3–4 ppm. New files
  calibrate on it from 2026-10-19 (MAS-172, done). The batches of 2026-10-12 to 10-16 (about 600
  files) and two earlier files are still on the old set (MAS-176).
- **TOF M**: both sets kept as they are (MAS-179, done). A read-only screen with the audit's code,
  on 253 files over the 60 days to 2026-10-28, found no ion above either top calibrant that passes
  the rules. Each axis stays extrapolated above its top calibrant: 241 Th in the bromide/nitrate
  mode, 188 Th in the nitrate mode.
  - *Bromide/nitrate.* Br3− at 241 Th is the highest reagent ion: in every file, within ±0.2 ppm.
    HSO4−·(H2SO4)n are absent: the lines at 195, 293 and 489 Th read −90 to +68 ppm, other peaks.
    Above 241 Th the only candidates are CHO members in at most 80% of the files. Their M+1 lines
    show fewer carbons than their formulas, or they read 21–28 ppm off. The one new ion that passes
    every rule, NO3−·C7H10O4 at 220 Th, lies inside the span and moves no band by more than 0.3 ppm.
  - *Nitrate.* No reagent ion lies above (HNO3)2·NO3− at 188 Th, which is kept in 84% of the files
    and in 28% of the latest day's. NO3− adducts of C7–C10 organics at 236–312 Th are strong, with
    no neighbour within a peak width, but are in only 69–90% of the files. On today's axis they
    read −1.5 to +0.9 ppm over the 60 days and −0.2 to +2.3 on the latest day. Two of them,
    NO3−·C10H16O5 (278 Th) and NO3−·C10H18O7 (312 Th), were refitted into the set as a test (the
    table). Each, held out, reads within 1.4 ppm. The axis steadies: over the 60 days the seven
    candidate ions at 236–296 Th outside the set vary from file to file by an IQR of 0.6–1.6 ppm
    instead of 2.4–3.9. But the fit tilts. Take the organic ions at 150–250 Th that pass the
    neighbour and ¹³C rules, are in every file of the latest day and read within 5 ppm: ten ions.
    On that day each reads 0.6–1.3 ppm lower, further from zero (−1.1 to +0.2 over the 60 days),
    and the calibrant lines at 150–200 Th read −1.0 ppm instead of −0.1. A lower band reads worse,
    so the set is kept. A wider variant, with NO3−·C7H10O5 (236 Th) and NO3−·C10H18O6 (296 Th) too,
    tilts the same way.
  - *Nitrate, mode 0 only.* The test fitted the node's one model today, mode 0, so the verdict is
    mode 0's. With the two ions in, the lines the fit keeps bow on the latest day: +0.5 ppm at
    40–100 Th, −0.7 at 100–150, −1.0 at 150–200, 0.0 at 278 Th and +0.8 at 312 Th. That is the
    shape the three-parameter model (MAS-183) is there to remove. The comparison is repeated under
    it before site A's history is re-calibrated (MAS-196):
    [#2785](https://github.com/ultra-trace-systems/mascope/issues/2785).
  - *Second nitrate mode.* Five files on one day. Nothing passes, and its collection serves seven
    modes. Unchanged.
  - Reaching the top would take an ion in every file above those masses, such as a calibrant dosed
    into the inlet: a choice for the lab, not a set change. Until then the coverage check (MAS-184)
    is what flags the extrapolation.

  The nitrate test under mode 0, ppm, 60 days (latest day). *Calibrant lines*: the lines the fit
  kept. *Family*: the audit's CHO·NO3− family offset, median [IQR] (section 2).

  | Band or line (Th) | Read on | Today's set | With the two |
  | -- | -- | --: | --: |
  | 40–100 | calibrant lines | −1.3 (−0.9) | −1.1 (+0.5) |
  | 100–150 | calibrant lines | −0.3 (+0.1) | −0.4 (−0.7) |
  | 100–150 | family | −1.8 [2] (no reading) | −1.4 [2] (no reading) |
  | 150–200 | calibrant lines | −0.2 (−0.1) | −0.4 (−1.0) |
  | 150–200 | family | −2.4 [4] (−1.1 [4]) | −2.4 [2] (−2.1 [4]) |
  | 200–300 | family | −1.0 [4] (−0.2 [4]) | −1.4 [3] (−1.5 [4]) |
  | 278 | NO3−·C10H16O5, not in the fit | 0.0 (+1.4) | −0.5 (0.0) |
  | 312 | NO3−·C10H18O7, not in the fit | +0.9 (+2.3) | +0.9 (+1.4) |
  | 300–500 | family | no reading | +1.5 [13] (no reading) |
- **Site B**: the nitrate and bromide sets of B1–B3 stay as they are (MAS-180, done; the plan
  owner's word in MAS-584). No ion above any set's top calibrant passes the rules: section 2,
  "Site B's calibrant sets on 2026-10-29". B2's nitrate set already holds within ±2.1 ppm to
  234 Th; the only ions that reach further fit new files on wrong peaks (#2776). B1 bromide's bow
  needs the three-parameter model (MAS-183), not a set. Reaching the top of these axes would take
  a calibrant dosed into the inlet, a choice for the lab.
- **Modes without calibrants** (site B ambient and zero): a decision (section 5).
- **How to choose calibrants**: a section in the user docs on the four ways a set goes wrong
  (it stops below the analysed range; a calibrant merges with an unresolved CHO neighbour, O
  against CH4 being 36.4 mDa, less than one peak width at R ≈ 2,800 above ~100 Th; a calibrant sits
  beside the ¹³C line of a stronger ion; a compound brings an ion for every mechanism of the mode)
  (MAS-177).

### 2 · The calibration node for TOF

Code, shipped in releases.

- **Three-parameter model.** Add Tofwerk's mode 2, `i = p1·m^p3 + p2`, to `mz_calibrate`, started
  from the mode-0 solution with p3 = 0.5 and fitted with the same soft-L1 loss. The handler fits
  both and keeps mode 2 only when it lowers the leave-one-out residual by more than a threshold,
  with at least four retained calibrants; the record says which model won and by how much. The
  minimum point count and the exact subset search change so that an exact fit is never preferred
  (MAS-183; the choice is MAS-181's).
- **A record that says what it was fitted on.** The quality block gains `points` (per match: ion,
  target and observed m/z, residual, S/N, retained or not), `span` [lo, hi], `collection` (id and
  a content hash of its isotopes under the mode's mechanisms), `model` and the node version. Older
  records stay readable and the seal covers the new keys (MAS-182).
- **Coverage check.** A quality issue (`extrapolated`, warn-only) when the file's strong peaks
  (S/N ≥ 20) reach well beyond the calibrant span at either end, with the threshold from the
  baseline and both spans in the message: "calibrated on 41–148 Th; peaks to 450 Th are
  extrapolated" (MAS-184).
- **Calibrant hygiene.** Per collection × instrument, the share of recent files in which each
  calibrant is matched, and a flag on calibrant peaks with an unresolved neighbour or a ¹³C line
  beside them (MAS-185).
- **Ion-level calibrant entries.** A calibrant entry can name the one ion it calibrates on, so a
  compound no longer brings an ion for every mechanism of the mode (MAS-186).
- **Calibration transfer**, if the decision on modes without calibrants picks it: a file whose mode
  has no calibrants takes the parameters of the nearest fitted files of the same instrument and
  polarity, interpolated in time within a window, recorded as `status: transferred` with its
  sources and the gap. A fit of the file itself wins (MAS-187).
- **Collection edits lead to re-calibration.** Editing the compounds of a collection a mode uses
  flags its batches `recalibrate`, as pointing the mode at another collection already does
  (MAS-188).

### 3 · Keep it right

- **TOF thresholds in the quality gate**, from the baseline: post-fit residual, minimum points and
  ions, and the coverage check, before any site moves the gate to `enforce` (MAS-189).
- **A calibration trend per instrument**: calibrant residuals and the raw-axis offset over time,
  so a calibrant that starts to read off, or a chemistry change that moves a neighbour onto it,
  shows (MAS-190). On TOF H, 246 and 248 Th moved by 14 and 35 ppm in one week.
- **Regression coverage**: de-identified TOF specimens in the fleet regression corpus, one per
  reagent class in use (propylamine +, nitrate −, bromide −), and a test that the node's fit equals
  an independent refit of its matched points to 0.01 ppm. Nothing in CI fits a TOF calibration on
  real data today: the demo dataset and the reproducibility goldens are Orbitrap (MAS-191).

### 4 · Re-calibrate existing data

Waits for the release that carries milestone 2 and, per instrument, for its calibrant set
(milestone 1). Section 6.

### Order

0 → 1, per instrument. 0 → the model decision → 2 → 3. 4 runs after 1 and 2 for the instruments
it touches: site A first as the canary, then site B, the internal server last.

## 5. Open decisions

Calls for the plan owner, each a *Decision* issue in the project. The leanings are this plan's, not
the answers.

- **When the TOF fit uses three parameters** (MAS-181). *Decided:* fit both, and keep mode 2 only
  when it lowers the leave-one-out residual by more than a threshold (0.5 ppm proposed), with at
  least four retained calibrants; the comparison goes into the quality block. No new setting.
- **What calibrates files whose mode has no calibrants** (site B ambient and zero, about 16,000
  files) (MAS-178). Options: (a) a calibrant list per such mode from ions ambient or zero spectra
  reliably show (NO3−, HSO4− and their clusters), which needs the customer's chemistry and measured
  presence; (b) transfer from the nearest reagent-mode files of the same instrument; (c) leave them
  uncalibrated and say so in the UI and exports. *Leaning:* (b) for multi-scheme instruments, (a)
  where an ambient spectrum carries enough known ions across the range, (c) only as the visible
  interim state. Also open: whether a transferred calibration is marked as such in the record and
  the badge.
- **How existing data is re-calibrated** (MAS-192): scope, who runs it, what happens to assignment
  runs and exports made on the old axis, what customers hear and when, and how long the previous
  parameters are kept. *Leaning:* section 6.
- **Calibrant sets per instrument** (milestone 1) are applied on the plan owner's word, one
  instrument at a time. *Decided* for site B: keep all five nitrate and bromide sets (MAS-584).
- Related, in the assignment quality plan ([`assignment_quality_plan.md`](assignment_quality_plan.md)):
  the calibrant cap and the offset term live in the same node and the same quality block (MAS-53,
  MAS-145); the cap's TOF default is off.

## 6. Re-calibrating existing data

About 50,000 customer TOF files are in scope (the census in section 2): their calibration comes
from a set that stops far below their analysed range, from the acquisition software alone, or from
nothing at all.

**Why it is cheap.** A TOF fit is absolute (section 3). Re-calibrating a file is a refit from its
stored peak TOF indices followed by an apply, which recomputes every m/z axis of the file from the
TOF index. No peak re-detection, no re-conversion, and an earlier fit is restored bit for bit from
its parameters.

**Readiness** (MAS-193). A read-only census per site: files per instrument × mode × record kind ×
month; whether the oldest files still hold the stores a refit needs (`peak_timeseries.zarr` with a
`tof` coordinate, `sum_signal.zarr`) in the current format; every file classed as refit-ready,
needs re-detection first, or has no peaks; which files have no sample item and so no mode. The
table comes here. Why B2's files are parked at `calibration_failed` is known: all 954 by
2026-10-24 are in its two ambient/zero modes, which have no calibrant collection (section 2), and
they wait for calibration transfer (MAS-187).

**The runner** (MAS-195), in `tooling/` beside the audit tool, on the pattern of the paced
re-process driver of the composite-cut rollout (MAS-90: 7,358 files on one site in ten hours):

1. Refit and apply with the node's own handler, under the instrument's current calibrant set and
   model, newest first, in batches with a gap between them and a backlog guard.
2. A per-file ledger: old and new parameters, record version, residuals.
3. Idempotent: a file whose record already carries the target collection version and node version
   (from MAS-182's record) is skipped, so a stopped run resumes.
4. A dry run that only reports the axis change per mass band each file would get.
5. Roll back a file or a batch from the ledger.
6. Rematch follows by itself, because apply clears the file's matches; assignment runs are flagged
   as the decision says.

**The run** (MAS-196), on the plan owner's word, one site at a time: site A first as the canary,
then site B, the internal server last, each measured with the audit tool before and after.

**What customers hear** (MAS-197): per instrument, a short note through the plan owner on what
changed, over which dates, how far each mass band moved, and what to re-check.

**The plan's leaning on MAS-192**, pending the plan owner's answer:

- an operator runs it per site with the runner, over all files with a fittable mode;
- rematch stays automatic, and peak-assignment runs made on the old axis are flagged stale, not
  re-run;
- exports are left alone, and the customer note lists the dates and the shift per mass band;
- previous parameters are kept for 90 days.
