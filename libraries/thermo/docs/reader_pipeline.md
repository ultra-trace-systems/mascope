# The spectrum reading & averaging pipeline

This document explains, end to end, how Mascope turns a Thermo `.raw` file into
the spectra and quantities the application uses, and why the non-obvious maths is
the way it is. It binds together the docstrings in `mascope_thermo` (reading,
averaging, reconstruction) and `mascope_signal` (sum signal, instrument fit).

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
`_selected(...)` filter scans by polarity (`+`/`-`), MS level (`Ms`/`Ms2`) and a
retention-time window `[t_min, t_max]`. One subtlety: the pipeline drops a
high-TIC outlier first scan when present (`thermo.py` `_bad_first_scan`), and
both backends apply it, so scan counts agree.

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
   matches the point density Thermo emits (~30k points).
3. Linear-interpolate each scan onto the frequency grid and sum. Because the
   peaks are aligned, this is the true mean shape (times `scans_combined`) with
   no integral rescaling.
4. Convert the frequency grid back to m/z with the reference calibration.

Falls back to a constant-ppm m/z grid only for non-FTMS data or when the
conversion parameters are unavailable.

### 3.2 Aligning the averaged profile to the calibrated m/z

Step 4 above uses the *reference* calibration; it still omits Thermo's per-scan
calibration *compensations* (~10-20 ppm, m/z-dependent). The exact, fully
calibrated m/z values live in the centroid labels. So
`_align_profile_grid_to_centroids()` matches the strongest, well-separated
profile peaks to their nearest centroid, rejects outliers, and fits a low-order
m/z correction to the whole grid. Result: the profile m/z lands on the
calibrated axis.

### 3.3 Baseline zero-fill

OpenTFRaw returns only non-zero profile samples; Thermo's profile has explicit
zero baseline between peak clusters. Linear interpolation across a large empty
gap would draw spurious ramps that, summed over scans, inflate the baseline.
`_zero_fill_profile_baseline()` (driven by `_ZEROFILL_GAP_FACTOR`) inserts a zero
just outside each cluster edge -- any m/z gap more than a few times the median
sample spacing is treated as a cluster boundary -- so interpolation stays local
and the baseline floor matches Thermo.

---

## 4. Multi-scan averaged centroids

`average_centroids()` produces the averaged centroid list `(masses,
intensities, resolutions, signal_to_noise)`. Thermo gets these by re-centroiding
the averaged profile; OpenTFRaw reconstructs the same result from the per-scan
labels:

1. **Pool** the per-scan FT label peaks across the selected scans.
2. **ppm-bin** them (`_ppm_bin`): peaks within `ppm` of each other (default 1)
   collapse into one bin. m/z is exact to sub-ppm this way; resolution and S:N
   are approximate.
3. **Merge jitter-splits** (`_merge_split_centroids`): the ~2 ppm between-scan
   m/z wobble can exceed the bin, splitting one real peak's centroids across two
   adjacent bins. Neighbours whose gap is well below the local FWHM (=
   m/z / resolution, gated by `_AVG_CENTROID_MERGE_FWHM`) are merged, while
   genuinely resolved peaks stay separate -- mirroring Thermo's re-centroid,
   which never splits one peak.
4. **Scale S:N to the averaged spectrum** (the `n/sqrt(N)` correction): Thermo
   reads S:N off the noise-reduced *averaged* profile. Averaging N scans drops
   the noise ~`sqrt(N)`, so a peak present in `n` of the `N` scans has averaged
   S:N ~= `(mean per-scan S:N) * n / sqrt(N)`. Without this the pooled per-scan
   S:N runs ~`sqrt(N)` too low, and near-threshold peaks that the weak-peak
   filter should keep would be dropped. (`present` is the per-peak scan count
   threaded through from the binning/merge.)
5. **Source peak height from the profile apex** (`_heights_from_profile_apex`):
   the ppm-bin intensity sum runs ~5-6% high versus Thermo, because Thermo's
   height comes from re-centroiding the averaged profile (whose apex carries a
   small interpolation loss). So the heights are taken from the
   frequency-averaged profile apex (section 3), which matches Thermo to ~1-2%.
   This uses the **real** profile (`reconstruct=False`) to avoid recursion with
   the reconstruction (section 5).

This is an *approximation* of Thermo's re-centroiding, so parity here is "very
close" not "exact": m/z to sub-0.1 ppm, summed intensity within a few percent,
and the count of peaks above the S:N threshold tracking Thermo. The unmatched
few percent are sub-threshold noise and ringing/satellite artifacts, not
analytes (see `test_centroids_average_matches_thermo`).

---

## 5. Display reconstruction (real vs reconstructed profile)

A crucial, non-obvious fact: **Thermo's averaged profile is itself a
reconstruction** -- one Gaussian per centroid (verified: the averaged profile's
local-maxima count equals the centroid count exactly, with a ~1e-10 baseline
floor). It is not the raw measured signal.

So Mascope keeps **two** profiles and the choice is deliberate:

- **Real measured profile** (`average_profile(reconstruct=False)`, the default)
  -- the frequency-averaged signal of section 3. It carries the genuine per-peak
  freq->m/z residual (a few ppm). Used by the **quantitative** path (the
  instrument-function fit, peak heights).
- **Reconstructed profile** (`average_profile(reconstruct=True)`,
  `_reconstruct_profile`) -- one Gaussian per averaged centroid (center = m/z,
  height = intensity, FWHM = m/z / resolution), summed on a per-peak sample grid
  (`_RECON_PTS` samples over +-`_RECON_SIGMA` sigma, ~Thermo's density). It
  **overlays the centroids exactly** and matches Thermo's reconstructed profile.
  Used for **display**, so the rendered profile and the centroid markers line up.

Using the reconstruction everywhere would break the instrument fit (it needs the
real measured peak shapes); using the real profile for display would show the
profile a few ppm off the centroid markers. Hence the split.

---

## 6. The sum signal (application entry point)

`mascope_signal/compute.py` `get_sum_signal()` is what the app calls. It:

- Resolves the sample type; **`reconstruct` is honoured only for live
  `orbi_raw`** (other sample types return the real signal regardless).
- Caches the real and reconstructed signals **separately** (the cache name
  carries a `_recon` suffix via `_get_sum_signal_hash_name`).
- Computes via `m_thermo.compute_sum_signal(...)` -> `average_profile(...,
  average=False)` (sum, i.e. apex = mean * scans_combined), optionally dividing
  by an averaging factor for the averaged view.

Display endpoints (spectrum/match views in the server controllers) pass
`reconstruct=True`; the fit and other quantitative consumers use the default
`reconstruct=False`.

---

## 7. Downstream consumers

- **Instrument-function fit** (`mascope_signal/instrument_func/fit.py`):
  `_process_peak_shapes` selects quality peaks from the **real** sum signal and
  measures per-peak FWHM; `_fit_resolution_function` fits the Orbitrap
  resolution law `R = a / sqrt(m)`. The coefficient `a` is the acceptance metric
  for the whole averaging path -- `test_instrument_fit_parity.py` asserts it
  agrees across backends.
- **Peak detection** uses the averaged-centroid S:N against a weak-peak
  threshold (S:N >= 3); this is why the `n/sqrt(N)` S:N scaling (section 4.4)
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
| Per-scan profile m/z (after alignment) | Within a few ppm (real measured residual) |
| Averaged centroid m/z | Sub-0.1 ppm (matched peaks) |
| Averaged centroid intensity (profile-apex) | ~1-2% |
| Averaged S:N above-threshold count | Tracks Thermo (via n/sqrt(N)) |
| Reconstructed profile vs centroids | Overlays exactly (<0.2 ppm) |
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
| Profile->centroid m/z alignment | `_align_profile_grid_to_centroids` |
| Baseline zero-fill | `_zero_fill_profile_baseline` |
| ppm binning | `_ppm_bin` |
| Averaged centroids | `average_centroids`, `_merge_split_centroids`, `_heights_from_profile_apex` |
| Reconstruction | `_reconstruct_profile` |
| XIC | `xic` |
| Sum signal (app) | `mascope_signal/compute.py:get_sum_signal`, `thermo.py:compute_sum_signal` |
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
  space-charge) terms beyond the ideal `B/f^2` conversion, and for why the real
  profile carries a small per-scan calibration residual that section 3.2 corrects
  against the centroid labels.
- **Makarov, A.; et al.** "First 20 Years of Orbitrap Mass Spectrometry as the
  Mainstream Analytical Technique." *Mass Spectrom. Rev.* **2025**.
  doi:10.1002/mas.70024. Recent comprehensive review.

(DOIs are provided for verification; confirm exact page numbers against the
publisher before citing externally.)
