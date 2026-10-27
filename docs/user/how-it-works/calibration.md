# Mass Calibration

Mass-to-charge ratio ($m/z$) [calibration](https://github.com/ultra-trace-systems/mascope/blob/master/server/backend/src/mascope_backend/api/controllers/calibration/lib/calibration_mz_fit.py) is a critical procedure required to ensure high mass accuracy by aligning known peaks (such as reagent ions) m/zs with their theoretical m/zs.

## Candidate Selection and Matching

Prior to executing instrument-specific regressions, a unified data-cleaning and matching pipeline isolates high-confidence reference points.

Detected peaks are extracted from the sample file and filtered based on their signal-to-noise ratio. 

Reference compounds are pulled from a calibration target collection, generating a list of exact theoretical isotopic masses based on the designated ionization mechanisms and instrument.
The chemistries Mascope ships bring their own when they have a reagent: the reagent ion and its clusters, such as `[Br]-`, `[Br2]-` and `[Br3]-` for bromide, `[NO3]-` with one and two nitric acids for nitrate, or the fluoranthene ion of an Orbitrap's EASY-IC source for the ambient ones.
They are in every collection list, named after their chemistry (*Bromide, negative calibrants*), each beside a *reagent ions* collection that is matched in every file of the chemistry to follow the source.
Each compound in them is named for the ion it stands for.
A cluster series gives a TOF fit the three points it needs and an Orbitrap the one it needs, but a file whose scan range starts above the clusters holds none of them; give its ionization mode a calibration collection of your own then, as you would any mode.
Mascope brings a shipped collection up to date with each release until you edit it, and leaves an edited one as you left it.
Peaks are only retained if their observed mass coordinates lie within a parts-per-million refinement window relative to any target isotopic mass.

On Orbitrap data, a local-dominance guard additionally rejects any candidate that lies close to a much stronger peak.
Fourier-transform centroiding of short transients produces weak sidelobe ("satellite") peaks around every intense centroid; these can carry a high signal-to-noise ratio while being orders of magnitude weaker than their parent, and would otherwise anchor the calibration to a slightly shifted mass whenever the true calibrant falls outside the refinement window.

To avoid isobaric interference, adjacent candidate peaks are checked for Full-Width at Half-Maximum (FWHM) overlap.
Each peak is given an expected width envelope, and any overlapping pairs are discarded to keep only clean calibration points.

On a TOF file the numbers are these.
A calibrant's isotopes are kept when their relative abundance is at least 0.15.
Peaks of the mode's polarity with a signal-to-noise ratio of at least 10 are candidates, and the refinement window is 100 ppm around each calibrant.
A peak that overlaps a neighbour within one FWHM by the instrument's resolution function is dropped, and so is the neighbour; the local-dominance guard is an Orbitrap rule and is off for TOF.
Each calibrant then takes the strongest remaining peak in its window, by time-averaged peak area.
A fit needs at least three matched points; fewer leaves the file with the warning *Not enough calibration peaks*.
The residual rule that keeps or drops a point in the fit below is 15 ppm, and a fit whose mean post-fit residual exceeds 15 ppm, or whose points all come from one ion, warns *Calibration inaccurate*.

## Quality Assessment and Scoring

Each target isotope is matched to the highest-intensity sample peak located within the chosen mass tolerance range.
A peak can be matched by several target ions of the collection: an ion reached through two ionization pathways, or the main line of one calibrant that is also a minor isotope line of another, such as a labelled reagent beside its unlabelled compound.
Such a peak contributes one point, read as the line with the highest relative abundance among its matches, so a calibrant's main line is not discarded for also being another ion's minor line.
Matches of equal abundance are separated by their mass error and isotope formula, never by anything that differs between servers, so the same file and collection give the same calibrants everywhere.

To evaluate the quality of candidate calibration assignments, a multi-parametric statistical assessment is performed on each matched target isotope.
For each distinct target ion, a principal reference point is defined by isolating the "main isotope" - the configuration with the maximum theoretical relative abundance.
The observed intensities are normalized against the selected main isotope reference to calculate relative observed intensities $I_{\text{rel}}$.
Normalized theoretical abundance $A_{\text{norm}}$ is derived by scaling the theoretical isotopic abundance against the theoretical abundance of the main reference isotope.
Abundance error $E_{\text{abundance}}$ is evaluated as the relative difference between the measured and theoretical normalized distributions:

$$E_{\text{abundance}} = \frac{I_{\text{rel}}}{A_{\text{norm}}} - 1.0$$

The mass accuracy of the observed peak is checked by tracking its displacement from the theoretical position.
The localized $m/z$ error ($E_{m/z}$) is converted to $\text{ppm}$ units to provide a standardized metric for comparison across the mass range:

$$E_{m/z} = 10^6 \times \frac{m/z_{\text{observed}} - m/z_{\text{theoretical}}}{m/z_{\text{theoretical}}}$$

To combine the metrics into a single confidence indicator, the algorithm applies a multiplicative joint scoring routine.
Both the abundance and mass components are bounded to protect the system from extreme outliers.
Penalty scaling is capped so that an abundance error of $1.0$ ($100\%$) or greater drops the abundance component score to zero:

$$\text{Component}_{\text{abundance}} = 1.0 - \min\left(1.0, |E_{\text{abundance}}|\right)$$

Mass accuracy component is evaluated using a linear penalty coefficient of $10^{-2}$ ($1\%$) per $\text{ppm}$ of deviation, with a floor constraint preventing negative values:

$$\text{Component}_{m/z} = \max\left(0.0, 1.0 - 10^{-2} \times |E_{m/z}|\right)$$

The final aggregate match score represents the product of these two components, yielding a value strictly bounded within a $[0.0, 1.0]$ interval where $1.0$ indicates an ideal experimental-to-theoretical alignment:

$$\text{Match Score} = \text{Component}_{\text{abundance}} \times \text{Component}_{m/z}$$

The resulting pairs are filtered using four strict quality control filters: a minimum relative isotopic abundance, a minimum absolute peak intensity, a maximum allowable baseline mass error, and a minimum match score.

## Outlier Rejection and Model Fitting

To insulate the calibration from false matches, the pipeline splits its outlier rejection strategy based on the size of the qualified match pool.

### Large Match Pool (More than 5 Points)

The entire dataset is initially fitted to the calibration model.
Elements whose resulting post-calibration residual errors exceed the set mass error tolerance are pruned, and the remaining clean subset is passed to the final fit.

### Small Match Pool (5 or Fewer Points)

An exact Random Sample Consensus (RANSAC) routine brute-forces every mathematically viable subset down to the instrument's minimum required calibration points.
Each subset is evaluated by fitting the model and scoring the results according to the number of retained points and then minimized mean internal residuals.
The highest-scoring consistent subset is chosen.
If no sub-group cleanly decouples in-tolerance points from outliers, the calibration is aborted.

A subset no larger than the model can absorb is fitted exactly and has no internal residual to be scored by.
This is the case for an Orbitrap file whose calibrants all disagree with one another by more than the tolerance: each one alone is a perfect one-point fit.
Between such subsets the one that asks for the smallest correction is kept, the calibrant closest to its theoretical mass before the fit, because a mass axis is far more likely to be nearly right than far off and a match that needs a large correction is more likely a neighbouring peak than the calibrant.
The verification below still judges the result: a one-point fit is trusted only while its correction is small.

## Calibration Models

Once a clean set of paired calibration points is established, the pipeline branches into instrument-specific regression models.

### Time-of-Flight (TOF) Multi-Point Calibration

The [TOF architecture](https://github.com/ultra-trace-systems/mascope/blob/master/libraries/tofwerk/src/mascope_tofwerk/calibration.py) maps ion arrival times to mass coordinates using a multi-point regression that requires a minimum of 3 calibration points. 
An initial guess for the relationship is established by computing a linear least-squares regression between the square root of the exact target masses and their corresponding observed time-of-flight values.
A non-linear least-squares minimization is executed using the Trust Region Reflective algorithm.
The optimization minimizes a residual function defined as:

$$\text{Residual} = p_0 \sqrt{m} + p_1 - \text{TOF}$$

where $m$ is the exact mass, $\text{TOF}$ is the measured time-of-flight, and $p_0, p_1$ are the coefficients being optimized.
The optimization utilizes a soft $L_1$ loss function to handle minor residual deviations.
The optimized parameters are used to re-calculate the mass axis according to the following analytical form:

$$m = \left( \frac{\text{TOF} - p_1}{p_0} \right)^2$$

A TOF fit is absolute.
Applying it recomputes the m/z axis from the stored time-of-flight index of every point of the spectrum and every detected peak, and nothing depends on the axis the file had before (an Orbitrap fit, by contrast, rescales the stored axis by a cumulative factor).
So re-calibrating needs no peak re-detection, an earlier fit is restored exactly from its two parameters, and applying the same fit twice changes nothing.
Applying a fit clears the file's matches and marks its batches for rematching.
Editing the calibration collection a mode already uses only marks its batches: the files stay on their old calibration until someone re-calibrates them.
A TOF file that has never been fitted carries the axis the acquisition software wrote, stamped `unfitted` in its calibration record.

#### What a TOF record says

Beside its parameters and the counts and mean errors of its quality block, a TOF fit's calibration record says what the fit was made on:

- **Points**: every calibrant line the fit chose from, the ones it left out included. Each has its ion, its exact and observed m/z, its residual under the fit in ppm, the signal-to-noise ratio of its peak, and whether the fit kept it. The residual is the fit's m/z at the peak's time-of-flight index against the exact m/z, as the TOF calibration audit reads it. A line the matching turned down never reached the fit and is not listed.
- **Span**: the exact m/z of the lowest and the highest line kept. Peaks outside it are on an extrapolated axis.
- **Collection**: the calibration collection, and a hash of the isotopes it offered under the mode's ionization mechanisms: their formulas, exact m/z and abundances. Editing the collection's compounds changes the hash, so files fitted on different versions of one collection can be told apart. The hash is the same on every server.
- **Model**: the model fitted, the square-root model above, which Tofwerk numbers mode 0. The record keeps that number and the model's equation in Tofwerk's names, `i = p1*sqrt(m) + p2`: `i` is the time-of-flight index, and `p1` and `p2` are $p_0$ and $p_1$ above.
- **Node version**: the version of the calibration step that made the fit.

The calibration dialog lists the points of the fit it previews, a line the fit left out in muted text, and the badge of a calibrated TOF sample names the span.
A record written before these were kept has the counts and mean errors only, and reads and applies as it always did.

### Orbitrap One-Point Scaling Calibration

The Orbitrap pipeline applies a localized, linear adjustment based on a single-point scaling approach, requiring a minimum of 1 calibration point.
Rather than adjusting multiple coefficients, the algorithm determines the median ratio between the exact target masses and the observed sample masses across all accepted matches:

$$\text{Scaling Factor} = \text{median}\left(\frac{m_{\text{target}}}{m_{\text{observed}}}\right)$$

The instrument's new operational calibration factor is calculated by scaling its pre-existing calibration factor (which defaults to 1.0 if no prior calibration exists):

$$\text{Calibration Factor}_{\text{new}} = \text{Calibration Factor}_{\text{old}} \times \text{Scaling Factor}$$

Calibrated mass values are computed by directly multiplying the observed mass values by the scaling factor:

$$m_{\text{calibrated}} = m_{\text{observed}} \times \text{Scaling Factor}$$

### The Orbitrap Axis Fingerprint

An Orbitrap measures how often each ion oscillates in the trap, and converts that frequency $f$ to m/z with the instrument's mass calibration, $m/z = B/f^2 + C/f^4$ ([Makarov, 2000](https://doi.org/10.1021/ac991131p); the [OpenTFRaw format documentation](https://github.com/Sigilweaver/OpenTFRaw/blob/main/docs/docs/format/10-frequency-to-mz.md)).
On some Orbitrap models, the Q Exactive Plus among them, the data system divides the frequency axis into bands about 16 kHz wide.
Each mass calibration of the instrument stores one small m/z offset per band, on top of a smooth trend.
After a good calibration the offsets are a fraction of a ppm to a few ppm; after a poor one they can reach tens of ppm.
The instrument does not report them, and a lock mass cannot remove them: it scales a whole scan by one factor, while the offset steps from band to band.

The raw file keeps these offsets: it stores one m/z correction with each stretch of a scan's profile ([OpenTFRaw changelog](https://github.com/Sigilweaver/OpenTFRaw/blob/main/CHANGELOG.md), 1.4.1).
When Mascope converts a raw Orbitrap file, it reads the offset of each frequency band back from the spacing of the stored profile points, for each acquisition mode of the file, and records which lock mass each scan was corrected with.
This per-file description is the file's **axis fingerprint**.
It is stored with the file and served through the API and the SDK ([The axis fingerprint of an Orbitrap file](../sdk/index.md#the-axis-fingerprint-of-an-orbitrap-file)), for clients that correct the mass axis band by band themselves, such as [Peaky](https://github.com/ultra-trace-systems/peaky).

Mascope itself changes no m/z with the fingerprint.
Its own Orbitrap calibration is the one-point scaling above: one factor per file, or per scan range, multiplied into every m/z.
On a model whose data system uses no such bands, as on the Exploris models measured, the fingerprint shows no steps between bands.
A file converted before the fingerprint existed has none until an operator runs the backfill script for it.

The measurement, its quality flags and the code are described in the [reader pipeline notes](https://github.com/ultra-trace-systems/mascope/blob/master/libraries/thermo/docs/reader_pipeline.md#71-the-axis-fingerprint).

## Verification

A fit that runs to completion is not necessarily a good one: two calibrants that disagree are split down the middle, and a single calibrant fits itself perfectly.
Matching and peak assignment trust a calibrated file's mass axis, so after a fit is applied it is checked against a quality bar:

| Criterion | Orbitrap | TOF |
|---|---|---|
| Mean post-calibration $\lvert m/z \rvert$ error | at most 1 ppm | at most 10 ppm |
| Correction made by a fit nothing corroborates (one point, or two isotopes of one ion) | at most 10 ppm | at most 10 ppm |
| Distinct calibrant ions, for a fit on 3 or more points | at least 2 | at least 2 |

One or two calibration points are common for narrow mass ranges and EasyIC.
Two points from different ions check each other, so such a fit is judged on its error alone.
A single point (or two isotopes of one ion) cannot, so it is trusted only while the correction it makes is small: a single calibrant that moves the axis tens of ppm has most likely been matched to the wrong peak.
The correction is measured from the axis the instrument wrote, so recalibrating a file an earlier fit moved does not hide it.

A file stitched from several scan ranges is calibrated range by range, and each range fitted on calibrants of its own is checked against the bar on them, as well as the file as a whole.
A range fitted on a single calibrant (or on two isotopes of one ion) is corroborated by another range of the same file when that range is fitted on calibrants of two or more ions, clears the bar itself, and makes a correction within 2 ppm of the first range's.
The two ranges then read the same offset of the instrument's axis, where a single calibrant matched to the wrong peak would move its range tens of ppm away from the others.

A fit that misses the bar is still applied, since it is usually closer than the axis the instrument wrote, and the sample gets an amber badge whose tooltip gives the reasons.
What happens next is a server setting, `calibration_quality_gate` in the `[backend]` section of the server configuration:

- `"warn"` (the default): the sample is matched and assigned as usual. Treat its mass errors with care until the calibration is fixed.
- `"enforce"`: the sample is left out of matching and peak assignment until it is recalibrated, or until an operator accepts the fit in the calibration dialog. An accepted fit is used downstream, and the sample keeps a warning badge saying so.

Run on `"warn"` first and look at which ionization modes the amber badges gather in.
Usually the lasting fix is in the calibration collection: two calibrants that disagree by a couple of ppm need a third, so that three or more points from at least two ions are found across the mass range.
Once the badges are rare, switch to `"enforce"`.

TOF files arrive carrying the axis the acquisition software wrote.
Until a fit replaces it, that axis is unverified and the sample is not matched.
If the ionization mode had no calibration collection when the files were uploaded, calibrate them from the batch menu once a collection is set.

## Calibration Status Badge

The sample browser shows each sample's calibration outcome in the calibration column:

| Badge | Meaning |
|---|---|
| Green | Calibrated and verified. |
| Teal | Calibrated and verified, but the file's own axis was far off before calibration (acquisition drift). The data is fine; the instrument needs retuning. |
| Amber | Calibrated below the quality bar. Matched anyway (warn-only servers), accepted by an operator and used, or left out of matching - the tooltip says which, and why the fit missed the bar. |
| Red | Calibration failed; the sample is uncalibrated and not matched. |
| Grey | Not calibrated (a TOF file still on its acquisition axis), or not verified. |
| Faint grey | Calibration does not apply: no calibration collection, or a blank file. |

Click a badge to open the calibration dialog for that sample.
