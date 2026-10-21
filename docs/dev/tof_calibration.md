# TOF mass calibration

*The plan to give every TOF file a calibration that holds across the mass range it is analysed
over: measure each instrument, fix its calibrant sets, add a three-parameter model and coverage
checks to the calibration node, and re-calibrate the TOF data already stored. The work is tracked
in the Linear project
[TOF mass calibration](https://linear.app/ultra-trace/project/tof-mass-calibration-8271ef05d142);
this note is its record.*

**Status.** Milestone 0 (measure) is done: the assessment that started the project, the audit
tool and the fleet baseline (section 2). One instrument's calibrant set has been corrected
(milestone 1). Every code change is still to come. Sites are named by letter and instruments by
neutral labels (TOF H, TOF M, B1–B3) throughout, as everywhere in this repository.

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

The same shape is likely on every TOF in the fleet (next section): calibrant sets end at
188–241 Th on axes that reach 950–1,385 Th.

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
- The three ambient/zero modes have no fit. The audit reads their converter record as one, so it
  has no per-band error for them ([#2462](https://github.com/ultra-trace-systems/mascope/issues/2462)).
  Their raw axis is the axis they carry.
- Where the refit misses the node's fit, it kept another number of points, 1–3 ppm apart over the
  calibrants (median). On TOF H the misses are 17 ppm apart: its set changed on 2026-10-17, inside
  the sampled window.

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
  lines matched there read +67 to +95 ppm, with an IQR of a few ppm. They are neighbours.
  (HNO3)3·NO3− at 251 Th reads +67, +71 and +71 ppm in the bromide modes of TOF M, B1 and B2,
  whose raw axes and calibrants differ. It reads +0.7 ppm in B2's nitrate mode, where it is the
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
  B1 +58 to +91, B2 +99 to +134, B3 −8 to −62. Each is flat over 40–200 Th within a run. B1, B2
  and B3 read 24–30 ppm lower on the day than over the 60 days. B2's first ambient/zero mode has
  no fit, so its files carry the raw axis: +77 to +93 ppm.
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

The calibrants are the isotopes of the mode's calibration collection: every compound of the
collection under every mechanism of the mode, at the instrument's resolution, kept when their
relative abundance is at least 0.15 (`DEFAULT_ISOTOPE_ABUNDANCE_MIN`). They are looked for among
the file's detected peaks:

1. The peak list is loaded without weak and satellite peaks (`load_peak_data`, `drop_bad_peaks`).
2. Peaks of the mode's polarity with S/N ≥ 10 (`TOF_SNR_THRESHOLD`) are kept.
3. Of those, the ones within the refine window of some calibrant, 100 ppm by default
   (`TOF_DEFAULT_REFINE_WINDOW`).
4. Peaks that overlap a neighbour within one FWHM by the instrument's resolution function are
   dropped, both of them (`_remove_overlapping_mzs`). The Orbitrap's local-dominance guard is off
   for TOF.
5. Each calibrant takes the strongest remaining peak within its refine window, by time-averaged
   peak area (`_match_max_in_range`). A peak two calibrants claim gives one point, read as the
   calibrant for which it is the more abundant line (`_one_reading_per_peak`).

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

The block does not say which ions the fit kept or their residuals, which m/z span it covers, which
model it used, or which version of the calibrant collection it came from. The handler builds a row
per match (`self.stats`) and the block keeps only the counts and means. Two other kinds of record
exist: the converter's `{"mode": 0, "par": [...]}` with the acquisition software's coefficients,
stamped `status: "unfitted"` since 2026-09-18 and recognised without the stamp before that
(`is_unfitted_record`), and the legacy records of the same shape, with or without
`"verified": true`.

### Apply

A TOF fit is **absolute**. `TofCalibrationHandler._apply_sync` recomputes the m/z axis from the TOF
index with `tof_to_mass(arange(n), mode, par)` and writes it to the `signal` and every
`sum_signal*` store, recomputes the peaks' m/z from their stored `tof` coordinate in
`peak_timeseries`, and stores the record in the file's props. Nothing depends on the axis the file
had before (unlike an Orbitrap apply, which rescales the stored axis by a cumulative factor). So:

- re-calibrating needs no peak re-detection: refit from the stored peak TOF indices, then apply;
- an old fit is restored exactly from its two parameters;
- applying the same fit twice changes nothing.

`calibration_mz_apply` then clears the file's matches and sets its non-acquisition batches to
`rematch`. Editing the calibration collection a mode already uses only sets its batches to
`rematch`: the files stay on their old calibration until someone re-calibrates them (MAS-188).

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
  `audit_tof_calibration` (`mascope prod db script run audit_tof_calibration`, configured through
  `TOF_AUDIT_*` variables listed in its docstring): a subclass of `TofCalibrationHandler` that
  swaps only what the handler reads for reads that write nothing, so the matching, selection and
  fit are the node's own. The family offset reads strong peaks (S/N ≥ 20) against CcHhOo neutrals
  of up to three double bonds and rings under each mechanism of the mode, where members of one
  nominal mass are 36.4 mDa apart; with four or more, two members can sit 15.3 mDa apart.
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
- **TOF M**: high-mass calibrants that reach the top of its ~1,000 Th axis, chosen from the
  baseline (MAS-179).
- **Site B**: nitrate and bromide sets for B1–B3, with spare points beyond the three-ion nitrate
  series (MAS-180).
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
  instrument at a time.
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
needs re-detection first, or has no peaks; why 804 B2 files are parked at `calibration_failed`;
which files have no sample item and so no mode. The table comes here.

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
