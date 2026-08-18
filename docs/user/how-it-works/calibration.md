# Mass Calibration

Mass-to-charge ratio ($m/z$) [calibration](https://github.com/ultra-trace-systems/mascope/blob/master/server/backend/src/mascope_backend/api/controllers/calibration/lib/calibration_mz_fit.py) is a critical procedure required to ensure high mass accuracy by aligning known peaks (such as reagent ions) m/zs with their theoretical m/zs.

## Candidate Selection and Matching

Prior to executing instrument-specific regressions, a unified data-cleaning and matching pipeline isolates high-confidence reference points.

Detected peaks are extracted from the sample file and filtered based on their signal-to-noise ratio. 

Reference compounds are pulled from a calibration target collection, generating a list of exact theoretical isotopic masses based on the designated ionization mechanisms and instrument.
Peaks are only retained if their observed mass coordinates lie within a parts-per-million refinement window relative to any target isotopic mass.

On Orbitrap data, a local-dominance guard additionally rejects any candidate that lies close to a much stronger peak.
Fourier-transform centroiding of short transients produces weak sidelobe ("satellite") peaks around every intense centroid; these can carry a high signal-to-noise ratio while being orders of magnitude weaker than their parent, and would otherwise anchor the calibration to a slightly shifted mass whenever the true calibrant falls outside the refinement window.

To avoid isobaric interference, adjacent candidate peaks are checked for Full-Width at Half-Maximum (FWHM) overlap.
Each peak is given an expected width envelope, and any overlapping pairs are discarded to keep only clean calibration points.

## Quality Assessment and Scoring

Each target isotope is matched to the highest-intensity sample peak located within the chosen mass tolerance range.
Duplicate assignments across overlapping ionization pathways are eliminated by keeping only the first unique occurrence.

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
Each subset is evaluated by fitting the model and scoring the results according to the number of retained points, minimized mean internal residuals, and maximized external errors.
The highest-scoring consistent subset is chosen.
If no sub-group cleanly decouples in-tolerance points from outliers, the calibration is aborted.

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

### Orbitrap One-Point Scaling Calibration

The Orbitrap pipeline applies a localized, linear adjustment based on a single-point scaling approach, requiring a minimum of 1 calibration point.
Rather than adjusting multiple coefficients, the algorithm determines the median ratio between the exact target masses and the observed sample masses across all accepted matches:

$$\text{Scaling Factor} = \text{median}\left(\frac{m_{\text{target}}}{m_{\text{observed}}}\right)$$

The instrument's new operational calibration factor is calculated by scaling its pre-existing calibration factor (which defaults to 1.0 if no prior calibration exists):

$$\text{Calibration Factor}_{\text{new}} = \text{Calibration Factor}_{\text{old}} \times \text{Scaling Factor}$$

Calibrated mass values are computed by directly multiplying the observed mass values by the scaling factor:

$$m_{\text{calibrated}} = m_{\text{observed}} \times \text{Scaling Factor}$$
