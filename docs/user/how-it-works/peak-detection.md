# Peak Detection

The [peak detection algorithm](https://github.com/ultra-trace-systems/mascope/blob/master/libraries/signal/src/mascope_signal/peak.py) is designed to identify and fit ion signals in mass spectra.
In Mascope, the peaks are detected in the summed mass spectrum, which is the result of summing across all spectra from all scans.

## Orbitrap Peak Detection

The centroiding algorithm is applied to high-resolution Orbitrap profile spectra, where the peak apex can be approximated locally by a Gaussian function.
This approximation enables sub-channel estimation of the peak centroid and apex intensity from a small number of neighboring samples.
A Gaussian peak profile is defined as:

$$y(x) = I_{\max} \exp\left( -\frac{(x - x_0)^2}{2\sigma^2} \right)$$

where $y(x)$ represents the spectral intensity at a mass-to-charge (m/z) coordinate $x$, $I_{\max}$ is the true apex intensity, $x_0$ is the true centroid position, and $\sigma$ is the standard deviation governing peak width. 

Taking the natural logarithm of the intensity linearizes the exponential term, transforming the Gaussian profile into a second-order polynomial (parabola):

$$Y(x) = \ln[y(x)] = \ln(I_{\max}) - \frac{(x - x_0)^2}{2\sigma^2}$$

Expanding this expression yields a standard quadratic equation:

$$Y(x) = ax^2 + bx + c$$

where the geometric vertex of the parabola corresponds directly to the sub-channel resolved centroid coordinate $x_0$:

$$x_0 = -\frac{b}{2a}$$

The algorithm scans the profile spectrum to isolate local maxima. 
For each detected maximum at index $k$, a three-point coordinate system is established consisting of the peak apex and its two immediate adjacent neighbors: $P_{k-1} = (x_{k-1}, y_{k-1})$, $P_k = (x_k, y_k)$, and $P_{k+1} = (x_{k+1}, y_{k+1})$.
Assuming a uniform channel spacing $\Delta x = x_k - x_{k-1} = x_{k+1} - x_k$, the log-transformed intensities are defined as:

$$Y_{-1} = \ln(y_{k-1}), \quad Y_0 = \ln(y_k), \quad Y_1 = \ln(y_{k+1})$$

By fitting a parabola through these three discrete points, the fractional position shift $\delta$ relative to the central channel coordinate $x_k$ is derived analytically via finite differences:

$$\delta = \frac{Y_{-1} - Y_1}{2(Y_{-1} - 2Y_0 + Y_1)}$$

The final centroid mass coordinate $x_0$ is then computed as:

$$x_0 = x_k + \delta \cdot \Delta x$$

Using the calculated fractional shift $\delta$, the true apex intensity $I_{\max}$ and standard deviation $\sigma$ can be extracted directly from the parabolic coefficients:

$$I_{\max} = \exp\left( Y_0 - \frac{\delta(Y_1 - Y_{-1})}{4} \right)$$

$$\sigma = \Delta x \cdot \sqrt{\frac{1}{2Y_0 - Y_{-1} - Y_1}}$$

## Time-of-Flight (TOF) Peak Detection

TOF profiles are resolved through a localized multi-peak fitting routine applied across segmented sections of the continuous mass spectrum.

The continuous total mass spectrum is segmented into individual mass windows centered around unique integer mass units ($u$) using a fixed width of $\pm \Delta m/z$ (where $\Delta m/z = 0.5\text{ Th}$):

$$\text{Window}_u = [u - 0.5, u + 0.5]$$

Up to 5 overlapping peaks per unit mass window are resolved by executing non-linear least-squares minimization routines using the empirical peak shape and mass-dependent resolution function.
Non-physical optimization results displaying non-positive heights or areas are automatically discarded.
To eliminate redundant fits arising at the boundary edges of adjacent mass segments, peaks are sorted by mass and evaluated using an absolute tolerance ($\text{Tol}$) derived from the minimum sampling interval of the experimental mass axis ($\Delta m/z_{\text{spacing}}$):

$$\text{Tol} = \frac{\Delta m/z_{\text{spacing}}}{2}$$

Isobaric groups falling within this tolerance are deduplicated by keeping only the optimization result yielding the highest peak intensity.

The $\text{SNR}$ for each resolved TOF peak is computed via an adaptive windowing strategy.
For a peak at position $m/z$ with evaluated resolution $R$, an inner peak exclusion zone ($\text{Excl}$) is defined as:

$$\text{Excl} = \pm \frac{m/z}{R}$$

An outer baseline window is then established spanning 10 times the width of the exclusion zone:

$$\text{Window}_{\text{baseline}} = \pm 10 \cdot \text{Excl}$$

The local noise standard deviation ($\sigma_{\text{noise}}$) is calculated using the raw data points situated strictly within the baseline window but outside the inner exclusion zone:

$$\text{Baseline Regions} = \{x \mid \text{Excl} \le |x - m/z| \le \text{Window}_{\text{baseline}}\}$$

The localized signal-to-noise ratio is given by:

$$\text{SNR} = \frac{\text{Peak Height}}{\sigma_{\text{noise}}}$$

## Peak Area Integration
For each peak, the standard deviation ($\sigma$) is computed dynamically from the localized resolution:

$$\sigma = \frac{m/z}{R \cdot 2\sqrt{2\ln 2}}$$

Integration boundaries are strictly constrained to a $\pm 3\sigma$ window around the center mass:

$$m/z_{\text{min}} = m/z - 3\sigma, \quad m/z_{\text{max}} = m/z + 3\sigma$$

The cumulative peak area is computed by evaluating the predefined empirical peak shape across the raw mass vector enclosed within these boundaries.

## Quality Control Filtering

### Signal-to-Noise Ratio
Peaks exhibiting a signal-to-noise ratio ($\text{SNR}$) below a strict threshold ($\text{SNR} < 3$) are flagged as weak.

### Satellite Peak Identification
Fourier-transform and Orbitrap mass spectrometers can generate electronic ringing artifacts, apodization side lobes, or high-intensity harmonic shoulders flanking major ionic signals.
These are formally classified as satellite peaks and must be flagged to prevent redundant compound assignment.

The algorithm targets artifacts generated exclusively around dominant ionic signals.
Potential parent components (base peaks) are isolated using an intensity criterion.
Peaks are classified as base candidates if their intensities meet or exceed a percentile threshold of $99\%$.
If the total count of candidates exceeds $20$, the array is trimmed to keep only the top $20$ most intense parent profiles across the dataset.

For each validated base peak with coordinates $(m/z_{\text{parent}}, I_{\text{parent}})$, a localized search range is established.
A bounding mass tolerance $\Delta m/z_{\text{win}}$ is computed:

  $$\Delta m/z_{\text{win}} = m/z_{\text{parent}} \cdot 100 \cdot 10^{-6}$$

where $100\text{ ppm}$ is the experimentally obtained window size for satellite detection: measured side-lobe families extend to roughly $\pm 70\text{ ppm}$ around the parent, while a wider window mostly adds chance pairings in dense spectra.
Candidates are restricted to the domain $m/z_{\text{parent}} \pm \Delta m/z_{\text{win}}$, excluding the parent itself.
Candidates must show a relative intensity ratio ($r = I_{\text{candidate}} / I_{\text{parent}}$) that falls strictly within the bounds of emperically derived range $10^{-6}$ to $0.04$.

To prevent the accidental removal of true chemical features, genuine isotopic peaks appearing on the positive mass side ($\Delta m/z > 0$) are shielded from satellite classification.
Potential nominal mass shifts are calculated dynamically for charges $z$ of $+1$ and $+2$:

  $$\Delta m/z_{\text{iso}} = \frac{m_{\text{neutron}}}{z}$$

where $m_{\text{neutron}}$ is a mass of a neutron.
A candidate is excluded from the satellite filtering pool if its distance to a predicted isotope line falls within a localized mass tolerance of $2.0\text{ ppm}$.

True instrument-induced side lobes emerge symmetrically on both sides of a major peak and share a comparable abundance profile.
Remaining candidates are split into left-sided ($\Delta m/z < 0$) and right-sided ($\Delta m/z > 0$) sub-arrays.
For every right-side mass shift, a binary search scans the left side to look for an equivalent negative offset:

  $$\left| \Delta m/z_{\text{right}} - \left|\Delta m/z_{\text{left}}\right| \right| \le m/z_{\text{parent}} \cdot 1.5 \cdot 10^{-6}$$

where $1.5\text{ ppm}$ is the experimentally derived mass tolerance for symmetry matching.
If a symmetric pair is paired successfully, their relative intensity ratios to the parent ($r_{\text{left}}$ and $r_{\text{right}}$) are checked for consistency:

  $$\frac{\min(r_{\text{left}}, r_{\text{right}})}{\max(r_{\text{left}}, r_{\text{right}})} \ge 0.25$$

The similarity threshold is deliberately loose: measured Fourier-transform side-lobe pairs mirror in position to well under a ppm while their intensities routinely differ severalfold, so a matching mirror offset is treated as the primary evidence.
If the similarity threshold is achieved, both peaks are formally flagged as satellites.

Any remaining unflagged candidate that fails the symmetry criteria can still be categorized as a satellite if it represents a shoulder peak adjacent to the parent peak within a mass window of $\pm 8\text{ ppm}$ - roughly one peak width, where a weak neighbor cannot be a resolved real peak.
More distant satellites require the mirror-pair evidence above, since dense spectra carry genuine weak peaks at tens of ppm from strong ones.

Following evaluation across all base peaks, the boolean status is mapped back to the original index positions and appended to the output dataset.
