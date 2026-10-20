# TOF mass calibration

*The plan to give every TOF file a calibration that holds across the mass range it is analysed
over: measure each instrument, fix its calibrant sets, add a three-parameter model and coverage
checks to the calibration node, and re-calibrate the TOF data already stored. The work is tracked
in the Linear project
[TOF mass calibration](https://linear.app/ultra-trace/project/tof-mass-calibration-8271ef05d142);
this note is its record.*

**Status.** Milestone 0 (measure) is under way: the assessment that started the project is done,
and one instrument's calibrant set has been corrected (milestone 1). The audit tool, the fleet
baseline and every code change are still to come. Sites are named by letter and instruments by
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

The per-instrument, per-mode baseline from the audit tool (milestone 0) and the re-calibration
readiness inventory (milestone 4) are added here when they are run.

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
  must equal the node's stored fit to 0.01 ppm (MAS-174).
- **This note** (MAS-173).
- **The fleet baseline**: the audit tool on every instrument × mode, a sample per day over 60 days
  plus one complete day, in bands 40–100, 100–150, 150–200, 200–300, 300–500 Th and above where
  strong peaks exist. The table goes into section 2 (MAS-175).

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
