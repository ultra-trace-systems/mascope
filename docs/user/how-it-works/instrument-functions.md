# Instrument Functions

The methodology for [calculating instrument-specific peak shapes and resolution functions](https://github.com/ultra-trace-systems/mascope/blob/master/libraries/signal/src/mascope_signal/instrument_func/fit.py) is based on the approach proposed by [DeCarlo et al, 2016](https://pubs.acs.org/doi/10.1021/ac061249n), which emphasizes estimating these parameters directly from experimental data.
The implementation utilizes iterative optimization and statistical weighting to characterize the mass spectrometer's performance across the mass-to-charge ($m/z$) range.

## Peak Shape Estimation

The peak shape calculation is a unified process that extracts a representative morphology from the raw spectrum.
The algorithm identifies potential candidates for shape estimation using local maxima detection. Found peaks are filtered based on a distance $\text{dmz}=0.5$ and a quantile-based height threshold of $95\%$ to isolate high-quality signals.
Each selected peak is fitted using a skewed Gaussian profile.
For each modeled peak with R-squared above $95\%$, the algorithm calculates the Center of Mass (COM) and the Full Width at Half Maximum (FWHM).
The standard deviation of the Gaussian distribution is computed for each peak as $\sigma = \frac{\text{FWHM}}{2 \cdot \sqrt{2 \cdot \log{2}}}$. The final peak shape is computed as the median peak shape from a 2D array of individual peaks, alligned by COM and normalized by $\sigma$.
This approach mitigates the influence of outliers or artifacts in individual peak measurements.

For TOF spectra, the algorithm includes a detection step to determine if the spectrum should be treated as "ambient" based on a peaks' signal-to-noise ratio threshold of $100$.
The ambient spectra are processed with a more lenient R-squared threshold of $85\%$ to accommodate the increased noise and variability typically observed in such measurements.

## Resolution Function Estimation

The resolution function $R$ characterizes how the resolving power of the mass spectrometer varies with $m/z$.

In mass spectrometry, $R$ and $\text{FWHM}$ are fundamentally linked by $m/z$:

$$R = \frac{m/z}{\text{FWHM}}$$

Thus, the resolution function can be derived from the observed $\text{FWHM}$ values across the $m/z$ range.

### Orbitrap Resolution Function
In Orbitrap mass analyzers, the resolution $R$ is modeled as being inversely proportional to the square root of the $m/z$ ([Perry et al, 2008](https://doi.org/10.1002/mas.20186)):

$$R(m/z) = \frac{a}{\sqrt{m/z}}$$

where $a$ is a coefficient determined during the fitting process.

The algorithm employs a statistical anomalous data filtering mechanism based on the distribution of residuals to refine $R$.
The algorithm first generates a preliminary fit of the observed $\text{FWHM}$ values against their corresponding $m/z$ values.
For each data point, a residual is calculated as the numerical difference between the observed $\text{FWHM}$ and the value predicted by the initial fit.
The standard deviation (SD) of the entire set of residuals is calculated to establish a measure of the global fit error.
Data points are identified as outliers if their individual residuals deviate from the mean by more than one SD.

### TOF Resolution Function
TOF spectra are typically more noisy and have more interfering peaks, which makes the estimation of $R$ more challenging.

The $R$ for TOF instruments is modeled using a rational polynomial function ([Junninen, 2013](https://helda.helsinki.fi/server/api/core/bitstreams/6b7681ca-5529-4c82-bc86-de670fbaf77f/content)):

$$R(m/z) = \frac{m/z}{a \cdot m/z + b}$$

To relate $\text{FWHM}$ to $m/z$, the algorithm performs a weighted linear fit using iterative Huber weighting.
This method minimizes the influence of outliers by adjusting weights $w$ based on residuals.
Residuals are scaled by a factor derived from the median absolute deviation:

$$s = 1.4826 \cdot \text{median}(|\text{residuals} - \text{median}(\text{residuals})|)$$

Weights are assigned such that $w = 1$ for small residuals and $w = 1/|\text{residuals}|$ for residuals exceeding a defined threshold.

To prevent the model from entering a premature plateau, the dynamic range - calculated as the difference between the maximum and minimum values of the rational polynomial divided by their mean - is checked against a target threshold of $0.05$.
If the range is too low, the intercept coefficient $b$ is scaled upward to increase curvature.

Coefficients $a$ and $b$ are strictly enforced to be positive ($> 10^{-12}$).
Additionally, the intercept $b$ is adjusted to ensure it contributes to the early curvature of the function based on the median $m/z$ of the observed peaks.
In cases where fewer than $5$ peaks are available, the algorithm falls back to an approximation where the plateau $a$ is derived from the median resolution of the available data points.
