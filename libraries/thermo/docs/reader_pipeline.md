# The spectrum reading & averaging pipeline

This document explains, end to end, how Mascope turns a Thermo `.raw` file into
the spectra and quantities the application uses, and why the non-obvious maths is
the way it is. It binds together the docstrings in `mascope_thermo` (reading,
averaging) and `mascope_signal` (sum signal, instrument fit).

It is reference material for developers; each section points at the function that
implements it. Nothing here is required reading to *use* the public functions in
`mascope_thermo.thermo` -- it is for understanding *why* the implementations look
the way they do.

---

## 1. The backend seam

All file reading goes through a `ReaderBackend` (see
`mascope_thermo/backend.py`), selected by the `MASCOPE_THERMO_BACKEND`
environment variable:

- **`opentfraw`** (default) -- the open-source OpenTFRaw reader (Rust), via the
  `opentfraw` wheel. No proprietary dependency.
- **`thermo`** -- Thermo's RawFileReader (.NET via pythonnet). Opt-in; needs the
  proprietary DLLs.

`ReaderBackend` is a *capability protocol*, not an emulation of the .NET RawFile
object: each backend implements profile/centroid/averaging/XIC/metadata natively.
The public functions in `mascope_thermo.thermo` are backend-agnostic.

The key consequence for this document: Thermo's .NET library computes several
things natively (multi-scan averaging, the extracted-ion chromatogram, the
re-centroided averaged profile). OpenTFRaw exposes the raw per-scan data, so the
OpenTFRaw backend **reimplements those operations in NumPy**. Most of the maths
below is that reimplementation, validated against Thermo by
`tests/test_backend_parity.py`.

---

## 2. The data model: profile vs centroids vs labels

A single Orbitrap (FTMS) scan can be read three ways:

- **Profile** -- the quasi-continuous measured spectrum: an `(m/z, intensity)`
  trace with many samples per peak. `backend.profile_per_scan()` /
  `RawFile.profile(scan)`.
- **Centroids** -- one `(m/z, intensity)` point per detected peak.
- **Centroid labels** -- the centroids *plus* per-peak `resolution` and
  `signal_to_noise`, decoded from Thermo's centroid-stream binary. Only FT scans
  carry these (finite resolution / S:N). `backend.centroids_per_scan()` /
  `RawFile.centroid_labels(scan)`.

Per-peak resolution and S:N matter downstream (the instrument fit uses FWHM =
m/z / resolution; peak detection uses S:N), so the labels are first-class.

**Scan selection** is shared by every read: `_selector(...)` /
`_selected(...)` filter scans by polarity (`+`/`-`), MS level (`Ms`/`Ms2`), a
retention-time window `[t_min, t_max]` and, when one is asked for, a scan
stream (`backend.md`, "Selecting one stream"). One subtlety: the pipeline drops
a high-TIC outlier first scan when present (`thermo.py` `_bad_first_scan`), and
both backends apply it, so scan counts agree. Asked for a stream, they compare
the first scan with the other scans of its own stream rather than with the
whole file.

---

## 3. Multi-scan profile averaging (the core)

`OpenTFRawBackend.average_profile()` reproduces Thermo's `AverageScans` over
profile data. A naive "put every scan on a common m/z grid and sum" is **wrong**,
and understanding why is the crux of the whole pipeline.

### 3.1 Why average in the frequency domain

In an Orbitrap an ion's *physical frequency* is the same in every scan; the small
between-scan wobble (~2 ppm) lives only in the per-scan frequency->m/z
calibration. So:

- Averaging on a fixed **m/z** grid misaligns each scan's copy of a peak by the
  calibration wobble, which **broadens** the averaged peak and, with
  interpolate-and-sum, **inflates the apex** (~+8%).
- Averaging on a **frequency** grid aligns the peaks exactly: no broadening
  (averaged FWHM == single-scan FWHM) and the apex equals `mean * scans_combined`.

This is exactly what Thermo does, and it is why the implementation goes back to
frequency. The steps (`average_profile`, the frequency branch):

1. Convert each scan's profile m/z back to frequency with that scan's Orbitrap
   conversion parameters B and C: `m/z = B/f^2 + C/f^4` (inverted by Newton's
   method, `_mz_to_freq`). Frequency is calibration-independent.
2. Build the output grid as the **union of the scans' frequencies, quantized to
   the native FFT-bin spacing** -- occupied cells only, so it is bounded and
   matches the point density Thermo emits (~30k points). Each grid point sits at
   the **mean of the real frequencies in its cell**, not at the cell's centre;
   see 3.1.1.
3. Linear-interpolate each scan onto the frequency grid and sum. Because the
   peaks are aligned, this is the true mean shape (times `scans_combined`) with
   no integral rescaling. A scan counts beside its own samples only: within
   two bins of one, and half a bin past the two ends of its range (3.1.2).
4. Convert the frequency grid back to m/z, each profile peak on the
   intensity-weighted mean of the scans' calibrations (3.2).

Falls back to a constant-ppm m/z grid only for non-FTMS data or when the
conversion parameters are unavailable.

### 3.1.1 Why the grid points sit on the samples, not on cell centres

A raw file keeps a *reduced* profile: about **3 points per FWHM**, not the
transient's full spectrum. At that density, where the output grid falls relative
to the stored samples is not a detail. A grid point placed at a cell's centre
lands between two samples, so the interpolation in step 3 returns a chord drawn
across the peak instead of the measured value, and the apex read off it comes
out low.

Every scan of a file is transformed on the same FFT bin grid, so a cell holds
one sample per scan. Where those agree to within a few percent of a bin, as
they do in over half the cells (3.1.2 has the rest), their mean is a frequency
the instrument actually sampled, and interpolating there returns what it
measured.

The single-scan case measures this, because with one scan nothing is averaged
and the apex must reproduce that scan's own centroid label -- the instrument's
own fit, which the Thermo library reports verbatim. Over 41,323 labels of ten
demo files:

| output grid | apex / label (median) | p10 | p90 |
| --- | --- | --- | --- |
| cell centres | 0.964 | 0.937 | 0.986 |
| **cell means** | **0.992** | 0.981 | 0.999 |

The loss is not a scale factor, which is what makes it worth removing. Split by
m/z, the same labels give:

| m/z | 82-200 | 200-257 | 257-327 | 327-430 | 430-750 | spread |
| --- | --- | --- | --- | --- | --- | --- |
| cell centres | 0.978 | 0.966 | 0.961 | 0.965 | 0.954 | 2.36 pp |
| **cell means** | 0.993 | 0.992 | 0.992 | 0.992 | 0.992 | **0.08 pp** |

So the old grid moved intensities against each other across the mass range by a
couple of percent, distorting the shape of a spectrum rather than its scale.
That the pattern is an artefact and not the instrument is settled by sampling
the cells' lower edges instead, a pure half-bin phase shift: the bands come out
in a different order again (2.09 pp spread, and the 430-750 band becomes the
*highest*).

Neighbouring peaks are barely affected, because the phase ramps over the whole
mass range and so is almost identical two daltons apart. An isotope ratio is
therefore near-blind to this: measured against the known 79Br/81Br abundance
ratio over the same 491 mono-brominated ions, the cell-mean grid is closer to
truth on 49.5% of them, which is a coin flip. Cross-mass comparisons gain;
isotope patterns do not.

The second reason is worse than the loss itself. Recovering frequency from m/z
leaves a residual scale error, so the phase between the cell lattice and the
samples **ramps across the mass range** -- half a bin end to end -- and the
interpolation loss ramps with it, rippling the heights by several percent in a
pattern set by nothing physical, only by how the reader chose to write the m/z
axis. That is what made reader 1.4.1's axis correction look like a height
change: measured against the Thermo library over 29,493 matched peaks, the
averaged-centroid bias differs by 1.3 percentage points between reader 1.4.0 and
2.0.0 on cell centres, and by 0.1 on cell means.

### 3.1.2 Where a scan is in the sum

A raw file keeps a scan's profile around its peaks only, so every scan has
stretches it stored nothing in, and step 3 has to say where a scan counts.

- **Not across what it left out.** Interpolating a scan from one cluster to
  the next would draw a ramp under every peak another scan holds in between,
  and summed over the scans those ramps inflate and flat-top the peaks that
  are in a few scans only. A scan is added within `_AVG_PROFILE_GAP_DF`
  (2) bins of one of its own samples, and is zero beyond. Beside each of its
  clusters it therefore still adds, for a bin or two, the chord towards its
  next cluster.
- **At its own first and last samples.** A grid point is the mean of what
  every scan sampled in its cell (3.1.1), so a scan's own sample lies a little
  to one side of it, above as often as below. At the two ends of a scan's
  stored range, where another scan sampled the same cell, the grid point
  therefore falls outside the range about half the time, and a search bounded
  by the scan's own end frequencies leaves the scan out of the sum at its own
  end sample. The search reaches `_AVG_PROFILE_END_DF` (half a) bin beyond
  either end instead: far enough for the grid point of the scan's own cell,
  short of the next bin's. The scan adds its end sample's value there.

The two-bin threshold sits on a lattice distance. The grid point two bins
past one of a scan's clusters lies two bins give or take the scan's sub-bin
offset from the cluster's last sample, so it is kept for one sign of the
offset and zeroed for the other: of such contributions about a quarter are
kept, 14,487 of 60,023 on the demo files and 773,524 of 2,966,994 on the
corpus. A threshold of 1.5 or 2.5 bins would not turn on the offset. Of the
two, 1.5 is the closer to the Thermo library. At the 2,816 grid points of 11
demo files that the choice changes, the sum is 1.25 times Thermo's with 1.5,
1.33 times with 2 and 1.68 times with 2.5, and the 676 centroid heights it
changes read a median 1.10, 1.11 and 1.13 of Thermo's. Neither is a small
change, though. With 1.5, 2,916 of the demo files' 310,095 centroid heights
change, 515 of them at S:N 3 or above by more than 1%, and 14,133 of the
corpus's 473,670; with 2.5 it is 8,628 and 56,687. The threshold stays at 2
until that is a change of its own.

Bounded by the end frequencies, 376 of the 1,288 end samples of the 161 demo
files stayed out of the sum, and 1,625 of the 10,788 of the internal
regression corpus. A scan alone in its cell is its grid point; of the demo
files' 815 end samples that share theirs with another scan, the 376 are 46%.
With the half bin it is none and 4. Those four lie more than half a bin from
the mean of their cell, because the scans' samples in one cell do not always
agree closely: of the demo files' cells that hold more than one sample, 44%
spread over more than a twentieth of a bin and 5.7% over more than half of
one.

It is two samples per scan, at the ends of its whole stored range, which
usually lie on the outer skirt of the first and last peak the scan stored: a
median 4e-7 of a corpus acquisition's summed signal, and 0.17% at most. It
shows where several scans begin on the flank of one peak. In a 12-scan
acquisition of the corpus they begin on three neighbouring bins of a peak's
rising flank, and those three samples read 31%, 39% and 16% low in the sum;
the instrument-function fit read that peak's resolution 8% high, and the
acquisition's resolution coefficient `a` moves by 3.2% once the samples are
in. Elsewhere little moves: the figures of 3.2 are the same to the last digit
shown, one of the demo files' 310,095 centroid heights changes (below S:N 9),
and 102 of the corpus's 473,670 do, eight of them at S:N 9 or above, by 0.19%
at most. Of 182 corpus acquisitions with a fit, `a` moves by more than 0.1% in
seven, and by more than 1% in that one alone.

### 3.2 Which calibration the averaged profile is written on

From reader 2.0.0 a scan's profile m/z is the instrument's own, point for
point, on the models 5.1 measures: the reader converts each frequency with the
scan's calibration and applies the same per-chunk m/z corrections the centroid
labels carry. So there is nothing to correct against the labels. What step 4
still has to choose is which calibration writes the frequency grid out, because
every scan carries its own.

They need not agree. A lock mass that engages part-way through a file, or
whose correction wanders from scan to scan, moves a scan's calibration by up
to a few ppm while the ions' frequencies stay put, and every label of that scan
moves with it. An averaged centroid reports the intensity-weighted mean of its
labels as written (section 4), and m/z is linear in B and C, so that mean is
the ion's frequency on the *intensity-weighted mean of the scans'
calibrations*, weighted by that ion's own intensity in each scan. For an ion
present alike in every scan that is the plain mean of them. An ion that comes
and goes leans towards the scans it was in, so ions with different time
courses sit on different calibrations, and no one axis matches them all.

Step 4 therefore writes each profile peak on its own calibration
(`_frequency_grid_to_mz`):

- **A peak** is the run of grid points between two valleys of the summed
  profile, or up to a gap in the occupied cells. A valley lies between samples
  of one run: the last sample before a gap belongs to the peak it ends, however
  tall the first sample beyond the gap is.
- **Its calibration** is the weighted mean of the scans' B and C at its tallest
  sample, the weights being what each scan contributes to that sample. Those
  are the weights of its centroid, so the peak lands on its centroid whatever
  the scans' calibrations did and whenever the ion was there.
- **The peak moves as a whole.** Every sample of it is written on that one
  calibration, so its width stays what the frequency axis gives it.
- **Peaks do not cross.** Neighbours on different calibrations move against
  each other, and at low m/z, where a grid step is about one ppm, two that
  overlap can end up past each other. Peaks that would cross are written on
  one calibration, weighted over the tallest sample of each, until the grid is
  monotonic, so the profile's samples stay in the order they were measured in.

Measured as in 5.1 -- strong peaks (S:N >= 20), each located at the vertex of
the parabola through its three top samples -- as the median over files of each
file's median distance to its centroid, with the worst file:

| files | densest scan's calibration | plain mean | per peak |
| --- | --- | --- | --- |
| 161 demo files (calibration spread 0.05 ppm) | 0.052 ppm (0.087) | 0.053 ppm (0.059) | 0.053 ppm (0.059) |
| internal regression corpus, 163 acquisitions | 0.052 ppm | 0.040 ppm | 0.039 ppm |
| ... its 22 whose calibration spreads over 0.4 ppm | 0.116 ppm (1.06) | 0.047 ppm (0.33) | 0.038 ppm (0.26) |
| two files whose lock mass engaged part-way | 1.24 and 3.14 ppm | 0.07 and 0.08 ppm | 0.03 and 0.04 ppm |

Any single scan's calibration puts the profile as far off its centroids as that
scan is from the mean, and over the corpus it leaves a bias as well: the signed
median over all its strong peaks is -0.048 ppm, -0.11 above m/z 500, against
-0.008 on the plain mean and -0.003 per peak.

The plain mean is right wherever the ions are there throughout, which is why
the demo files do not tell the two apart. What it leaves is the long
acquisitions (60 to 1,500 scans) whose calibration drifts by 0.7 to 2.6 ppm,
and those whose scans alternate between mass ranges. One of 1,486 scans goes
from 0.33 ppm to 0.07 per peak, and one that alternates between two ranges
from 0.28 to 0.02; over the 22 drifting acquisitions the ninth decile falls
from 0.25 ppm to 0.07. Weighting one mean for the whole grid by each scan's
total signal does no better than the plain mean (worst file 0.75 ppm), because
it is still one axis.

The file left at 0.26 ppm is not a matter of calibration. Of its 1,881 strong
peaks some 1,700 are packed between m/z 60 and 65, in the skirts of the
reagent ions, where one peak's labels scatter by 1.6 ppm from scan to scan on
any one calibration, so no axis puts their average on its centroid. Its strong
peaks above m/z 100 go from 0.04 ppm to 0.02. Without that file the worst of
the 22 is 0.07 ppm.

Weak peaks gain the most, since a weak ion is often in a few scans only. Over
every centroid of six files whose lock mass engaged part-way, the median
distance falls from 0.86 ppm to 0.04, and the share with a profile peak within
3 ppm of them rises from 94.6% to over 99%.

**Why per peak and not per grid point.** Weighting every grid point by itself
reaches the same figures (0.038 ppm over the 22) but bends the peaks. A scan
stores nothing below its noise, so a weak scan holds less of a peak's flanks
than a strong one; the flanks then lean towards the strong scans' calibration
and the top towards all of them, and the peak is written wider or narrower
than it is. Over the 22 drifting acquisitions 18% of the strong peaks changed
width by more than 5% that way, against 1% per peak.

**Why crossing peaks share a calibration.** Peaks cross where a grid step is
small against the spread of the calibrations: 2,742 of the 243,000 profile
peaks of the 22 drifting acquisitions, in three of them, and 898 of 135,000
in the six files whose lock mass engaged part-way, at m/z 40-160. Putting the
samples involved back on the plain mean keeps the order just as well, but the
plain mean is no ion's calibration where a mass range is in some of the scans
only. In the acquisition that alternates between two ranges, the positive ions
below m/z 200 come from one scan in five; its strong positive peaks below
m/z 100 read 0.61 ppm on the plain mean, 0.19 with that fallback and 0.04
sharing.

The guard acts on a crossing, not on a squeeze, so two neighbouring peaks can
be left with almost no room between them. Of the 110,000 valleys between peaks
in contiguous samples of the 22 drifting acquisitions, 1,967 keep less than a
quarter of their step and 744 less than a tenth, the smallest 0.0002 of a
step, which a plot draws as a vertical step. Nearly all lie between weak
peaks: where both peaks are strong (S:N >= 20) it is a couple at most, in the
skirts of the reagent ions.

Fitting the axis to the centroid labels -- needed while the reader left the
profile a few ppm off them -- does worse on every count. A fit matches the
profile's sampled maxima to the nearest per-scan label, so on steady files it
adds a tenth of a ppm or two (0.083 ppm on the demo files, against 0.053), and
where a line fitted to mid-range anchors extrapolates it moves the ends of the
range by up to 10 ppm. On a file whose calibration steps, the nearest label to
a peak is the densest scan's own, so a fit leaves that scan's offset in place.

The Thermo library's own averaged profile agrees: over 31 files it puts a
strong peak within 0.063 ppm of where it is written per peak, and within 0.065
of the plain mean, against 0.077 for the densest scan's. Those files hold a
handful of scans each and their strong ions are there throughout, so they do
not tell the per-peak calibration from the plain mean. Where the scans'
calibrations spread over several ppm at low m/z the library's averaged peak is
smeared across them, and its position is no reference there.

### 3.3 Baseline zero-fill

OpenTFRaw returns only non-zero profile samples; Thermo's profile has explicit
zero baseline between peak clusters. Left as it comes off the grid, the
averaged profile would run in a straight line from the last sample of one
cluster to the first of the next, however far apart they lie.
`_zerofill_baseline()` inserts a zero just outside each cluster edge, so the
profile drops to the baseline between clusters, whatever reads it interpolates
locally, and the floor matches Thermo.

**Where a cluster ends** is read off the frequency grid: at a step to the next
occupied cell more than `_ZEROFILL_GAP_FACTOR` (4) times the grid's median
step, each step taken relative to its frequency. It is not read off the m/z
axis, because 3.2 writes every peak on a calibration of its own, which
stretches or squeezes the step between two neighbouring peaks by up to a few
ppm. A threshold on the written axis moves with the scans' calibrations; the
cells the scans occupied do not. Judged on the written axis, the 22 drifting
acquisitions had 1,008 boundaries the plain mean's axis does not give and
lacked 1,375 it does, of 118,730, seven of them between adjacent bins, across
a valley the axis had stretched.

m/z goes as 1/f^2, so a relative step in frequency is half the relative step
in m/z all along the grid, and on one calibration the two axes pick the same
gaps. Against the m/z rule on the plain mean's axis, the frequency rule
differs in none of the corpus's 353,485 boundaries and in one of the demo
files' 257,373. It is one rule (`_zerofill_baseline`), handed the axis to
read the steps off: the frequency grid here, and the m/z grid itself in the
m/z fallback, which has no other.

The threshold is relative, so it is not a number of bins. A bin is the same
step in frequency all along the grid, a larger share of the frequency the
higher the m/z, and a step of n bins ends a cluster only above (4/n)^2 times
the median m/z of the occupied cells. On a grid from m/z 50 to 750 with that
median at 126, one missing bin ends a cluster above about m/z 500, and four
missing bins do not end one below about m/z 80. Where the boundaries fall
thus still depends on where a file's peaks lie, though no longer on its
calibration.

**Where the zeros go**: `_ZEROFILL_EDGE_PPM` (2 ppm) outside each of the two
edge samples. A gap under 4 ppm wide has no room for that. The two zeros
would pass each other, and in a gap under 2 ppm each would land among the
next cluster's samples, a zero inside a peak. Such a gap takes one zero, at
its middle. That is where the two meet as a gap narrows to 4 ppm, so the
baseline does not jump with a width the calibrations still stretch and
squeeze. The demo files have no such gap. Of the 353,485 boundaries this rule
finds on the corpus 3,182 are that narrow, 232 of them under 2 ppm, all in
seven acquisitions whose bins are under 1 ppm wide, where four median steps
come to less than 4 ppm. Of the 353,058 boundaries read off the written axis
it was 2,317 and 180: the rule here also marks 1,317 narrow gaps that the
written axis had squeezed under its threshold, which then got no zero at all.

**Why a threshold and not every missing bin.** The cells are exact, a bin is
missing or it is not, so a boundary could go at every missing bin instead.
That is closer to Thermo's profile. Over 11 demo files it reads zero inside
all 17,599 gaps the threshold marks, and also inside all but 9 of the 741 gaps
of two or more missing bins the threshold leaves bridged; in the 505 of one
missing bin it dips to a median quarter of the lower edge sample. At Thermo's
points inside those gaps the bridged profile is off Thermo's by 0.59 of the
taller edge sample on average, and by 0.10 with a zero at every missing bin.
It is 7% more boundaries (275,223 against 257,372 on the demo files, 377,580
against 353,485 on the corpus), nearly all of one to four missing bins at the
lower end of the mass range, where a median step is the most bins.

It also moves what reads the profile, on files whose calibration is steady,
which reading the boundaries off the frequency grid was not meant to do. A
zero beside a peak's top sample changes the parabola its height is read off
(section 4): on the corpus 142 centroid heights change, seven of them at
S:N 9 or above, by up to 11%. The instrument-function fit sees the zeros in
its windows, and its resolution coefficient moves in every demo file, by up to
0.39%. With the threshold the demo files keep their centroid heights and
their fit (`a` moves by 2e-10 at most), and on the corpus four heights
change, none at S:N 9 or above, and `a` moves by more than 0.1% in two
acquisitions of 182. A boundary at every missing bin is a change to make on
its own, with its own generation. So is a third rule, between the two and
not measured here: a fixed number of bins, the same all along the grid, as
`_frequency_grid_to_mz` counts its gaps.

---

## 4. Multi-scan averaged centroids

`average_centroids()` produces the averaged centroid list `(masses,
intensities, resolutions, signal_to_noise)`. Thermo gets these by re-centroiding
the averaged profile; OpenTFRaw reconstructs the same result from the per-scan
labels:

1. **Pool** the per-scan FT label peaks across the selected scans.
2. **Key them by frequency** (`_labels_on_one_calibration`): each scan's label
   m/z is converted to frequency with that scan's Conversion Parameter B/C and
   back with one reference scan's. The profile averaging goes to frequency the
   same way (section 3.1), though it writes each peak back on the weighted
   mean of the scans' calibrations (3.2); a key only has to group, so any one
   scan serves. What
   shifts a label between scans is the per-scan
   calibration, and when a lock mass engages part-way through a file that
   calibration steps by a few ppm at once. At low m/z such a step is wider
   than the bin below *and* than half a FWHM, so without this key one peak's
   centroids land in two bins that step 4 cannot rejoin (measured: a 2.5 ppm
   step on seven-scan acquisitions split every strong peak below m/z 70).
   Scans without B/C (non-FTMS data) bin on the labels as written.
3. **ppm-bin** them (`_ppm_bin`) on that key: peaks within `ppm` of each other
   (default 1) collapse into one bin. The reported m/z is the intensity-weighted
   mean of the labels as written, exact to sub-ppm; resolution and S:N are
   approximate.
4. **Merge jitter-splits** (`_merge_split_centroids`): the residual sub-ppm
   between-scan m/z wobble can still exceed the bin, splitting one real peak's
   centroids across two adjacent bins. Neighbours whose gap is well below the
   local FWHM (= m/z / resolution, gated by `_AVG_CENTROID_MERGE_FWHM`) are
   merged, while genuinely resolved peaks stay separate -- mirroring Thermo's
   re-centroid, which never splits one peak. A second rule reaches further, to
   `_AVG_CENTROID_EXCLUSIVE_MERGE_FWHM` (1.5 FWHM), for neighbours the scans
   hold one or the other of but not both. A dominant ion at a few million
   counts per scan can have its measured position jitter by about its own
   width from scan to scan with the calibration steady (measured: a base peak
   alternating between two positions 1.1 FWHM apart, half the scans each), so
   each scan yields one centroid that lands in one of two bins, whereas two
   real ions that close are resolved within a scan and so share scans. The
   test is intensity-weighted (`_AVG_CENTROID_EXCLUSIVE_OVERLAP`: a stray weak
   label in a shared scan does not veto the merge, a real minor ion present in
   the same scans does), requires each side to hold a substantial share of
   the scans (`_AVG_CENTROID_EXCLUSIVE_MIN_SIDE`), which
   one ion alternating between two positions does and the wobbling fragments
   beside an intense peak, present in a couple of scans, do not -- it is also
   what keeps two noise labels from different scans apart, so on a
   noise-dominated file the rule changes nothing -- requires the two sides
   together to cover most of the scans (`_AVG_CENTROID_EXCLUSIVE_COVERAGE`),
   which only decides when they overlap in scans, and requires the side
   holding more intensity to change at least twice along the scans
   (`_AVG_CENTROID_EXCLUSIVE_MIN_ALTERNATIONS`): two ions that hand over in
   time, one present before a transition and the other after, share no scan
   either but change side once, and joining them would report a mass about
   2 ppm off both. A calibration step without conversion parameters is a
   hand-over too, so only the frequency key rejoins one.
   Every one of those tests reads a scan without a label as a scan without
   the ion, which only holds well above the noise, so each side's
   intensity-weighted per-scan S:N must also reach
   `_AVG_CENTROID_EXCLUSIVE_MIN_SN` (10). On the published demo dataset
   (161 files of four scans each), a peak at per-scan S:N 3 to 4 misses at
   least one scan in half the cases and one at 4 to 5 in 15%, against under
   3% from S:N 10 up. Without the floor, two weak neighbours taking turns
   above the noise -- the 18O and 13C2 isotopologues of one ion, 12 to 13 ppm
   apart, or two unrelated ions 15 ppm apart -- passed every other gate as a
   clean alternation over four scans: the rule joined 40 such pairs across
   38 of those files, moving matched isotopes by up to 7 ppm or onto the
   wrong target, and joins none with it.
   Measured over a 183-file fleet corpus against Thermo, the per-side floor
   is what keeps the rule specific: without it the share of strong centroids
   matching a Thermo centroid fell by more than 0.02 in 31 files, with it in
   11 and never by more than 0.05, while the jittering base peak still merges.
   Thermo's own re-centroid reports such a jittering ion as two peaks, its
   averaged profile being flat-topped, so here the two readers differ by
   design.
5. **Scale S:N to the averaged spectrum** (the `n/sqrt(N)` correction): Thermo
   reads S:N off the noise-reduced *averaged* profile. Averaging N scans drops
   the noise ~`sqrt(N)`, so a peak present in `n` of the `N` scans has averaged
   S:N ~= `(mean per-scan S:N) * n / sqrt(N)`. Without this the pooled per-scan
   S:N runs ~`sqrt(N)` too low, and near-threshold peaks that the weak-peak
   filter should keep would be dropped. (`present` is the per-peak scan count
   threaded through from the binning/merge.)
6. **Source peak height from the profile apex** (`_heights_from_profile_apex`):
   the ppm-bin intensity sum runs ~5-6% high versus Thermo, because Thermo's
   height comes from re-centroiding the averaged profile. So the heights are
   taken from the frequency-averaged profile apex (section 3). On a single scan
   that reproduces the instrument's own centroid label to 0.8% (section 3.1.1).
   On an averaged spectrum it reads **about 3% above Thermo**, consistently
   across the intensity range -- the apex of the measured averaged profile
   genuinely sits there, and Thermo's averaging convention reports a little
   less. Matching Thermo exactly would mean reproducing its convention, which is
   not something this pipeline can derive, so the difference is left standing
   and stated rather than tuned away.

This is an *approximation* of Thermo's re-centroiding, so parity here is "very
close" not "exact": m/z to sub-0.1 ppm, summed intensity within a few percent,
and the count of peaks above the S:N threshold tracking Thermo. The unmatched
few percent are sub-threshold noise and ringing/satellite artifacts, not
analytes (see `test_centroids_average_matches_thermo`).

Two departures from Thermo are deliberate. On a file whose calibration steps
between scans (step 2), Thermo's averaged profile is broadened by the step and
its re-centroided height drops with it -- about a quarter for a 2.5 ppm step at
m/z 42, measured against the same file -- while the frequency-keyed path keeps
the per-scan heights. The same ion in a file without the step gets the same
height from both readers, so it is Thermo's number that varies with the lock
state, not the ion. And an ion whose position jitters by about its own width
(step 4) is one centroid here and two in Thermo, whose averaged profile of it
is flat-topped; its height here is the per-scan sum, as for any other peak.

---

## 5. What the spectrum views draw: the measured profile

The spectrum endpoints -- the sample spectrum, the sample file spectrum and the
match view's isotope windows, and through them the SDK's `get_spectrum` and
`get_spectra` -- return the measured averaged profile of section 3. It is the
same signal, from the same cache entry, that the instrument-function fit and the
peak heights read, and the entry is named after the reader and averaging that
computed it (section 6), so what is drawn is what the current reader measures.
The spectrum views dot its samples wherever they land far enough apart on screen
to tell one from the next (`server/frontend/src/lib/charts/samples.js`): in the
match view's narrow isotope windows usually from the start, in the sample
spectrum once zoomed in. The profile's density, about three points per FWHM, is
then visible rather than implied.

Nothing is drawn in its place. The display used to show a reconstruction
instead -- one Gaussian per averaged centroid (centre = m/z, height = intensity,
FWHM = m/z / resolution) -- for two reasons, and neither holds.

### 5.1 Alignment

The reconstruction overlaid the centroid markers by construction, where the
measured profile sat a few ppm off them. A raw file stores an m/z correction
with each chunk of a scan's profile, a contiguous run of samples, to be added to
the m/z after conversion; it is what carries the per-scan compensations the
centroid labels have. Up to reader 1.4.0 the reader added it to the frequency
before converting instead. Reader 1.4.1 applies it where it belongs, and 2.0.0
is the first such reader pinned here:

| | per-scan profile apex minus its label | averaged profile apex minus its averaged centroid |
| --- | --- | --- |
| reader 1.4.0 | -4.60 ppm median, -5.40 below m/z 200 | -- |
| reader 2.0.0 | **-0.001 ppm** median, +-0.1 ppm in every band | **0.053 ppm** median absolute, no bias |

On the four instrument models it was measured on, a Q Exactive Plus and three
Exploris models, the per-scan axis is in fact the Thermo library's. Over the
28.1 million stored samples of 5,394 profile-mode MS1 scans in 185 files, 4,823
scans are bit-identical on every sample, and the other 571 differ by one factor
per scan: 1.4e-4 ppm at most, the samples of a scan agreeing on it to 3.3e-9
ppm. Reader 1.4.0 opens 182 of those files, 181 of them with profile scans, and
differs on every sample of them, by 6.7 ppm at the median file's worst.

It is not so on every Orbitrap. The reader's
[changelog](https://github.com/Sigilweaver/OpenTFRaw/blob/main/CHANGELOG.md)
lists, as fixed after 2.0.0, MS1 profiles of Fusion Lumos, Eclipse and Orbitrap
Elite files that come back as frequencies in place of m/z.

What is left on those 571 scans appears to be on the Thermo side. Its axis for
such a scan is what converting the scan with an earlier scan's conversion
coefficients gives, where B and C both lie within 0.01 of that scan's, while the
open reader converts every scan with its own. This is read off the two readers'
outputs and not from anything the vendor documents: taking the first such
earlier scan reproduces 5,383 of the 5,394 scans, shifted or not, to the last
digit of the factor, and in the other 11 the coefficients are another earlier
scan's, also within 0.01. It caps the shift at 0.01 / B: 1.5e-4 ppm at the Q
Exactive Plus's B of 6.8e7, 5.9e-5 ppm at the Exploris models' 1.7e8.

`test_profile_matches_thermo` (in `test_backend_parity.py`) holds every stored
sample of every profile-mode MS1 scan to 1e-3 ppm, the samples of one scan to a
single factor within 1e-6 ppm, and every intensity to equality.

The averaged figure is over 80,416 strong peaks (S:N >= 20) of the 161 demo
files, each located at the vertex of the parabola through its three top samples,
the way the Thermo library centroids one (5.3); its signed median is -0.006 ppm,
and it runs from 0.04 ppm below m/z 150 to 0.09 above m/z 500. It holds where the
scans' calibrations differ, because each peak is written on the weighted mean
of them (3.2). Against a 4-8 ppm FWHM, a tenth of a ppm is not a visible offset.
`test_sum_signal_peaks_sit_on_the_centroids` (in `test_thermo_spec_extraction.py`)
holds the averaged profile to it under each backend, locating peaks the same
way.

### 5.2 Smoothness, and what it cost

The other reason was that a raw file keeps about three points per FWHM, so the
measured profile renders as a short polyline rather than a curve. The
reconstruction was no denser: its 15 samples over +-5 sigma come to 3.3 per
FWHM. It looked smoother because every peak in it was symmetric and had a
sample exactly on its apex.

That regularity is what it cost. It drew a symmetric Gaussian of the label's
resolution over whatever was measured, so a shoulder, an asymmetry, a flat top
or an unresolved neighbour disappeared from the picture -- exactly what someone
zooming into a peak to tell one ion from two needs to see.

Resampling the measured signal more finely would buy the smoothness back, and
is deliberately not done. Interpolating a profile this sparse loses height
(section 3.1.1), and the same endpoints feed the SDK, so what they return has to
be the samples themselves.

### 5.3 Thermo's profile is NOT a reconstruction

The reconstruction was also once justified as matching the vendor: that Thermo's
averaged profile is itself one Gaussian per centroid. That does not survive a
direct test, and the evidence once cited for it is not discriminating:

- *"The local-maxima count equals the centroid count exactly."* It does -- and so
  it does for the **real measured per-scan profile**, 736 maxima for 736
  centroids. A stored profile is already reduced to the regions around detected
  peaks, so one bump per centroid is what measured data looks like too.
- *"The baseline floor is ~1e-10 of the base peak."* Also true of the measured
  averaged profile (4e-12 here). A low floor says points were dropped, not drawn.
- *"Peaks are Gaussian to <1%."* Measured, the averaged profile's Gaussian
  residual is 2.7% of peak height -- worse than the real per-scan signal's 1.5%.

The discriminating test is that a profile drawn from the centroids reproduces
them *exactly*, because that is how it was drawn. Against Mascope's former
display reconstruction as a known positive:

| averaged profile | apex / centroid intensity | fitted FWHM / (m/z / resolution) |
| --- | --- | --- |
| Gaussian per centroid (known positive) | **1.00000** | **1.00000** |
| Thermo `AverageScans` | 1.012 (p10 0.987, p90 1.029) | 0.970 (p10 0.946, p90 1.010) |
| Mascope measured | 0.990 | 0.811 |

Thermo's averaged profile misses both marks, with real spread, so it is not
synthesised from its centroid list. The dependence runs the other way: its
averaged centroids are read off the profile, each one the vertex of the parabola
through its peak's three top samples, to within 1.1e-5 ppm over 5,950 peaks of
21 demo files.

What it *is* doing is resampling onto a finer grid than the native sample
spacing -- 8,244 non-zero points against our 7,274 for the same scans -- which is
why its peaks look smoother and why a Gaussian fitted to them recovers 0.97 of
the nominal width where the natively-spaced signal gives 0.81. That smoothness
is the likely source of the impression that the vendor's profile is synthetic.

The per-scan profile is even plainer: Thermo's non-zero points are the stored
samples, agreeing with the open reader's **to the last bit in intensity** on
every one of them, and in m/z to the last bit or to 1.4e-4 ppm (5.1). The only
other difference is that Thermo also carries the baseline zeros around each
cluster, which the open reader omits (and which `_zerofill_baseline` puts back).

---

## 6. The sum signal (application entry point)

`mascope_signal/compute.py` `get_sum_signal()` is what the app calls. It:

- Resolves the sample type and reads the file with the matching reader.
- Caches the signal per window (`_get_sum_signal_hash_name`: the full signal as
  `sum_signal`, a filtered one under a hash of its time window and polarity).
  A raw Orbitrap file's cache name also carries what averaged the profile,
  `averaged_profile_signature()` (`sum_signal_suffix`): the reader, its version
  and `AVERAGED_PROFILE_GENERATION`, as in `sum_signal_<hash>.otf2.0.0-g5`.
- Computes via `m_thermo.compute_sum_signal(...)` -> `average_profile(...,
  average=False)` (sum, i.e. apex = mean * scans_combined), optionally dividing
  by an averaging factor for the averaged view.
- Puts a raw Orbitrap file's signal on its calibrated m/z axis, the full one as
  well as a filtered window. Applying a calibration rescales every stored sum
  signal in place (`OrbiCalibrationHandler`), so a stored axis is the
  acquisition axis times the file's current calibration factor, and a signal
  averaged after the file was calibrated has to start there too - which, once
  a new reader renames the cache, is every one. An Orbitrap file kept without
  its raw file (`orbi_zarr`) takes no factor: it is summed from its stored
  signal, which a calibration rescales in place as well.

The display endpoints (the spectrum and match views in the server controllers)
and the quantitative consumers read the same signal, so a window's cache entry
serves both (section 5).

The signature is there because nothing else would tell a cached profile from a
fresh one. Before it, a file processed under reader 1.4.0 kept serving that
reader's profile after the upgrade - averaged on the old grid (3.1.1) - to the
instrument-function fit, and to peak detection when it was re-run. A reader
upgrade now changes the name by itself. A change to `average_profile` that
alters its output for the same samples has to bump `AVERAGED_PROFILE_GENERATION`.
The entries left behind are never read again;
`python -m mascope_backend.db.admin.filestore delete-stale-sum-signal` deletes
them, touching raw Orbitrap files only.

---

## 7. Downstream consumers

- **Instrument-function fit** (`mascope_signal/instrument_func/fit.py`):
  `_process_peak_shapes` selects quality peaks from the **real** sum signal and
  measures per-peak FWHM; `_fit_resolution_function` fits the Orbitrap
  resolution law `R = a / sqrt(m)`. The coefficient `a` is the acceptance metric
  for the whole averaging path -- `test_instrument_fit_parity.py` asserts it
  agrees across backends.
- **Peak detection** uses the averaged-centroid S:N against a weak-peak
  threshold (S:N >= 3); this is why the `n/sqrt(N)` S:N scaling (section 4.5)
  matters -- it keeps/drops the same near-threshold peaks as Thermo.
- **Extracted-ion chromatogram** (`xic()`): for each target m/z, sum the
  centroid intensities in its ppm window per selected scan (vectorized per scan
  via a sorted prefix sum). The Thermo backend uses `GetChromatogramData`;
  parity is asserted by `test_xic_matches_thermo`.

---

## 8. What is exact vs approximate

| Quantity | Parity with Thermo |
|---|---|
| Per-scan centroid m/z / resolution / S:N | Exact (same binary stream); sub-0.0002 ppm m/z |
| Per-scan profile m/z | On the models measured (a Q Exactive Plus, three Exploris models), from reader 2.0.0: bit-identical on nine scans in ten; within 1.4e-4 ppm on the rest, where Thermo's axis is what an earlier scan's coefficients give (section 5.1) |
| Per-scan profile intensity | Exact (the stored samples) |
| Averaged centroid m/z | Sub-0.1 ppm (matched peaks) |
| Averaged centroid intensity (profile-apex) | ~3% high, and flat across the intensity range (section 6, step 6) |
| Single-scan centroid intensity (profile-apex) | 0.8% of the instrument's own label |
| Averaged S:N above-threshold count | Tracks Thermo (via n/sqrt(N)) |
| Averaged profile peak vs its own averaged centroid | 0.053 ppm median absolute (section 5.1); exact under Thermo |
| XIC | rtol 1e-4 |

The averaged-centroid path is the only genuine *approximation* (Thermo
re-centroids the averaged profile; we reconstruct from per-scan labels). It is
validated as "very close, not exact" by the parity suite; closing the last few
percent would require re-centroiding the averaged profile rather than tightening
a tolerance.

---

## 9. Map of the code

| Step | Function (`mascope_thermo/backend.py` unless noted) |
|---|---|
| Backend selection | `open_backend`, `ReaderBackend` |
| Scan selection / first-scan drop | `_selector`, `_selected`, `thermo.py:_bad_first_scan` |
| Per-scan centroids + labels | `centroids_per_scan` |
| Per-scan profile | `profile_per_scan` |
| Frequency-domain averaging | `average_profile`, `_mz_to_freq` |
| Frequency grid back to m/z, each peak on its weighted calibration | `_frequency_grid_to_mz` |
| Baseline zero-fill | `_zerofill_baseline` |
| ppm binning | `_ppm_bin` |
| Labels keyed by frequency | `_labels_on_one_calibration` |
| Averaged centroids | `average_centroids`, `_merge_split_centroids`, `_heights_from_profile_apex` |
| XIC | `xic` |
| Sum signal (app) | `mascope_signal/compute.py:get_sum_signal`, `thermo.py:compute_sum_signal` |
| Sum signal cache name | `averaged_profile_signature`, `mascope_signal/compute.py:sum_signal_suffix` |
| Instrument fit | `mascope_signal/instrument_func/fit.py` |
| Cross-backend parity tests | `tests/test_backend_parity.py`, `signal/tests/test_instrument_fit_parity.py` |

---

## 10. References (the public basis for the approach)

The frequency-domain averaging and the `m/z = B/f^2 + C/f^4` conversion are not
reverse-engineered from Thermo's library -- they follow from the published
Orbitrap physics. Key references:

- **Makarov, A.** "Electrostatic Axially Harmonic Orbital Trapping: A
  High-Performance Technique of Mass Analysis." *Anal. Chem.* **2000**, 72(6),
  1156-1162. doi:10.1021/ac991131p. The foundational Orbitrap paper: ions
  oscillate axially at a frequency proportional to `(m/z)^-1/2`, detected by
  image current and transformed by FFT -- i.e. each m/z maps to a distinct
  frequency, so `m/z = B/f^2` to leading order. This is *why* peaks align across
  scans in the frequency domain (section 3.1).
- **Hu, Q.; Noll, R. J.; Li, H.; Makarov, A.; Hardman, M.; Cooks, R. G.** "The
  Orbitrap: a new mass spectrometer." *J. Mass Spectrom.* **2005**, 40(4),
  430-443. doi:10.1002/jms.856. Accessible review of trapping, detection and FT
  processing.
- **Zubarev, R. A.; Makarov, A.** "Orbitrap Mass Spectrometry." *Anal. Chem.*
  **2013**, 85(11), 5288-5296. doi:10.1021/ac4001223. Review covering resolution,
  transient length and mass accuracy.
- **Lange, O.; Damoc, E.; Wieghaus, A.; Makarov, A.** "Enhanced Fourier
  transform for Orbitrap mass spectrometry." *Int. J. Mass Spectrom.* **2014**,
  369, 16-22. doi:10.1016/j.ijms.2014.05.019. Describes eFT (absorption-mode)
  processing -- context for why the centroids/profile Thermo returns are a
  *processed* spectrum, not the raw transient.
- **Calibration Function for the Orbitrap FTMS Accounting for the Space Charge
  Effect.** *J. Am. Soc. Mass Spectrom.* **2010**, 21(11), 1846-1851.
  doi:10.1016/j.jasms.2010.06.021. Basis for the higher-order (`C/f^4`,
  space-charge) terms beyond the ideal `B/f^2` conversion, and for why every
  scan carries a calibration of its own, which section 3.2 averages.
- **Makarov, A.; et al.** "First 20 Years of Orbitrap Mass Spectrometry as the
  Mainstream Analytical Technique." *Mass Spectrom. Rev.* **2025**.
  doi:10.1002/mas.70024. Recent comprehensive review.

(DOIs are provided for verification; confirm exact page numbers against the
publisher before citing externally.)
