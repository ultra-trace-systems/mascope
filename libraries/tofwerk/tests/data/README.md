# TOF calibration specimens

One derived fixture per reagent class of the TOF fleet, each from one real TOF
file in the fleet regression corpus (MAS-191, MAS-936). Nothing else in CI fits a TOF
calibration on real data: the demo dataset and the reproducibility goldens are
Orbitrap.

| Fixture | Reagent | Polarity | Lines kept |
|---|---|---|---|
| `tof_specimen_bromide.json` | bromide | − | 7 of 7, three ions |
| `tof_specimen_nitrate.json` | nitrate | − | 3 of 3, three ions |
| `tof_specimen_propylamine.json` | propylamine | + | 6 of 6, six ions |

The propylamine specimen's 228 Th line is a cluster, C13H26NO2+, and its 287 Th
line is that cluster with propylamine added, C16H35N2O2+: each is listed under
the ion formula the node matched. Six ions are enough for the node to compare
mode 2 with mode 0, which it does on no other specimen; it keeps mode 0, at
7.0 ppm held out against mode 2's 9.7 ppm.

## What a fixture holds

What the calibration node reads near each calibrant line, and what it made of
it. Nothing names the file, its instrument, site, date or ionization mode.

- `reagent`, `polarity`.
- `axis`: the file's m/z axis when the node fitted it, `mode` and `par`, on the
  peak store's TOF index. It is the acquisition software's record, but the
  store's index counts from the first sample the converter keeps, so the raw
  file's own record is off by that many samples. The axis here is the mode-0
  axis through the fit's points at the m/z the node read them at, which it
  reproduces to 0.03 ppm.
- `params`: the refine window and the residual tolerance the node fitted with,
  from the record's `quality`: the node's defaults.
- `calibrants`: every isotope line the ionization mode's calibration
  collection offered, with its ion, isotope formula, exact m/z and relative
  abundance. An ion reached through two mechanisms is listed twice, as the
  collection offers it.
- `peaks`: every detected peak near a calibrant line, matched or not: within
  200 ppm (twice the refine window) on bromide and nitrate, which node version
  2 fitted at ingest; within 600 ppm on propylamine, which version 3 fitted,
  so that the fixture holds every peak its 500 ppm search window and the
  overlap filter's 100 ppm reach beyond it read. Each peak has its TOF index,
  its m/z on `axis`, its mean area over the polarity's scans (what the node
  ranks candidates by), whether the store held its timeseries (if not, the
  area is its sum spread over the scans), its summed area and height, S/N,
  polarity, the weak and satellite flags, and the resolving power the
  instrument function gives it.
- `fit`: the node's fit at ingest, `mode` and `par`, and its `points`: every
  line it chose from, with its ion, exact and observed m/z, its peak's TOF
  index, its residual under the fit in ppm, and whether the fit kept it. On
  propylamine also `compared`: the models the node compared, each with its
  error in ppm at the calibrants it was not fitted on, from the record's
  `quality.model`. The other two keep three ions each, too few to compare the
  models on.

## Who reads them

- `libraries/tofwerk/tests/test_tof_specimens.py` refits the kept points
  with `mz_calibrate` and pins the parameters and every residual, and pins
  the loss on the bromide lines with one of them misread.
- `server/backend/tests/unit/api/calibration/test_tof_calibration_specimens.py`
  writes each fixture into a filestore as a TOF file and runs the node's
  `TofCalibrationHandler` on it: the same lines and the same axis as at
  ingest, to 0.01 ppm, the models compared to 0.01 ppm where the fixture
  holds them, and the calibration audit's refit equal to the node's.
  It also changes one thing at a time on the bromide specimen (a misread
  line, a brighter neighbour, a line's S/N) so that the residual tolerance,
  the refine window and the S/N floor each decide. And it adds negative
  peaks to the propylamine specimen, a positive-mode file with none of its
  own, to show a positive fit takes none of them.

## Re-baselining

A change to the matching window, the S/N floor, the selection rule, the model
or the fit's loss moves these fits, and the tests fail: that is what they are
for. When the change is meant, or a calibrant set changes on purpose, take
the specimens again from a kept corpus replay of the new build
(`corpus replay <ref> --keep`) and say in the commit what moved. The full
specimens, every peak of each file with its stored record, are in the corpus
under `runs/2026-11-17_mas191-tof-specimens/` and
`runs/2026-11-19_mas936-tof-specimen/`.
