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

The $\text{SNR}$ for each resolved TOF peak divides its height by the spread of the baseline around it.
For a peak at position $m/z$ with evaluated resolution $R$, the peak width is

$$w = \frac{m/z}{R}$$

and the baseline window around it spans half a unit of mass, or ten widths where that is wider:

$$\text{Window}_{\text{baseline}} = \pm \max\left(0.5,\ 10 \cdot w\right)$$

Every fitted peak's core, $\pm 3w$ around its centre, is cut out of the window, not only the peak's own: in a TOF spectrum the window holds the peak's wings and its neighbours at the same unit mass, and counting them as noise would make the noise grow with the heights around the peak.
The local noise ($\sigma_{\text{noise}}$) is the median absolute deviation of the remaining points, scaled to a standard deviation, so a peak too small to be fitted does not count as noise either.
Where the baseline is mostly empty, that deviation is zero: the spectrum is counting single ions, as an ambient-mode TOF's often is, and each ion is a short blip above zero.
The noise there is the height of one ion: the median height of the blips in the empty stretches of the whole spectrum, leaving out any that reach into a fitted peak.
Where the standard deviation of the remaining points is larger, it stands in.
So over an empty baseline a peak's $\text{SNR}$ is about the number of ions it holds, and a peak of a few stray ions stays below the gates that read the $\text{SNR}$.
A peak with no baseline left in its window has no $\text{SNR}$, nor has one over a flat baseline in a spectrum with no single ion to measure.

The localized signal-to-noise ratio is given by:

$$\text{SNR} = \frac{\text{Peak Height}}{\sigma_{\text{noise}}}$$

An Orbitrap file without its raw file is picked on this TOF path, but its $\text{SNR}$ keeps the earlier noise: the standard deviation of the summed spectrum between one and ten widths of the peak.

## Peak Area Integration
For each peak, the standard deviation ($\sigma$) is computed dynamically from the localized resolution:

$$\sigma = \frac{m/z}{R \cdot 2\sqrt{2\ln 2}}$$

Integration boundaries are strictly constrained to a $\pm 3\sigma$ window around the center mass:

$$m/z_{\text{min}} = m/z - 3\sigma, \quad m/z_{\text{max}} = m/z + 3\sigma$$

The cumulative peak area is computed by evaluating the predefined empirical peak shape across the raw mass vector enclosed within these boundaries.

## Quality Control Filtering

### Signal-to-Noise Ratio
Peaks exhibiting a signal-to-noise ratio ($\text{SNR}$) below a strict threshold ($\text{SNR} < 3$) are flagged as weak, on Orbitrap and on TOF files alike.
A peak with no $\text{SNR}$ is not flagged by this cutoff.

A weak peak stays in the file's peak store but is left out wherever peaks are read by default: the peak list, the peak marks on the spectrum, m/z calibration and assignment.

On a TOF file whose baseline is mostly empty, typically an ambient-mode file with no reagent ion, the noise is the height of one ion, so the cutoff means about three ions.
The fit keeps a cluster of one or two stray ions at most unit masses of such a spectrum, and these are now weak: such a file lists a fraction of the peaks it did.
A real ion of only one or two counts is hidden with them.
What decides how many peaks a TOF file loses is how full its baseline is, not whether it has a reagent ion.
A file with a sparse baseline, a reagent-ion one included, loses most of its small peaks as well.
Where the baseline holds many counts, as on most reagent-ion files, nearly every peak stands well above the cutoff.

TOF files detected before this flag existed keep their peaks unflagged until they are reprocessed.
An Orbitrap file without its raw file, detected on the TOF path, keeps its earlier $\text{SNR}$ and flags no peak weak.

### Local Floor (Orbitrap)
An Orbitrap peak's $\text{SNR}$ is the instrument's: its noise is one figure for a wide stretch of the spectrum, and it does not follow a floor that rises locally.
Around a dominant ion, such as a chemical-ionization reagent ion of millions of counts, a spectrum averaged over many microscans holds a continuous floor of maxima, a centroid every few ppm over several Da.
Averaging lowered the noise and left the floor, so each of its maxima reads an $\text{SNR}$ far above the threshold, and such a file can hold tens of thousands of them.

An Orbitrap peak is therefore also measured against the peaks of its own spectrum (its polarity, or its scan stream) within $\pm 3000\text{ ppm}$ of its m/z.
Where that window holds at least $300$ other peaks, one every $20\text{ ppm}$ on average, the stretch is a floor rather than a list of resolved ions, and a peak lower than $5$ times the median height of the window's peaks is flagged as weak.
The window is wide so that the comb of side lobes a few hundred ppm either side of a reagent ion, dense over too short a stretch to fill it, is left to the satellite flag below.
An ion standing five times out of the floor is kept; a real ion weaker than that is flagged weak with the floor.
Where the window holds fewer peaks, the stretch says nothing of a floor, and its peaks are judged by their $\text{SNR}$ alone.

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

### The Instrument's Side-Lobe Flag (Orbitrap)
An Orbitrap marks some of the centroids it records in each scan ([Thermo Fisher Scientific, RawFileReader](#thermo-rfr)).
The Exception mark says that the instrument reads the centroid as a weak side lobe of a brighter peak beside it: the ringing a Fourier transform leaves around an intense line ([Mathur and O'Connor, 2009](#mo09)).
About one centroid in twenty carries it on the published demo raws, and more on some acquisition methods.
Mascope reads the mark for every Orbitrap file it detects from its raw file.

The summed spectrum averages the scans, so a peak of it holds a centroid from most of them.
For each peak, the centroids that the averaged scans recorded within half the peak's width of its m/z are counted, together with how many of them carry the mark:

$$s = \frac{N_{\text{Exception}}}{N_{\text{centroids}}}, \qquad |m/z_{\text{centroid}} - m/z_{\text{peak}}| \le \frac{m/z_{\text{peak}}}{2R}$$

where $R$ is the peak's resolution.
A peak with $s > 0.5$ is the instrument's side lobe, and is flagged as a satellite like those above.
The share is nearly all or nothing: of the peaks with any marked centroid, 85 % have $s > 0.8$.
These lobes sit about 30 ppm from their parent, at well under a percent of its height.
Most of them clear the signal-to-noise threshold, and the ratio test above, which only takes the strongest peaks as bases, misses about half of them.

The instrument marks a centroid by where it sits, not by what it is, and a real isotope line can sit where it expects a lobe.
An ion's $^{2}\text{H}$ line lies 2.92 mDa above its $^{13}\text{C}$ line, about 2.4 peak widths at m/z 150 to 230, and is marked whenever the $^{13}\text{C}$ line is bright.
So a marked peak is kept when it sits on an isotopologue line of a peak that is not marked.
The lines are those of $^{13}\text{C}$, $^{2}\text{H}$, $^{15}\text{N}$, $^{17}\text{O}$, $^{18}\text{O}$, $^{34}\text{S}$, $^{37}\text{Cl}$ and $^{81}\text{Br}$, at their spacing $\Delta$ above the other peak ([Coursey et al.](#nist-iso)):

$$|m/z_{\text{peak}} - m/z_{\text{other}} - \Delta| \le \max\left(\frac{m/z_{\text{peak}}}{2R},\ 1\ \text{ppm}\right)$$

The tolerance is half the peak's width because a real line and a lobe on top of it are averaged into one peak, centroided between the two.
The marked peak's height also has to be one that line can have beside the other peak.
For $^{13}\text{C}$ that is between 0.004 and 0.5 of it, about half a carbon to 47 carbons, and never above 1.5 times what the most carbons the other peak's mass can hold would give.
For the other lines, the same range is scaled by their abundance per atom over that of $^{13}\text{C}$.
A lobe of a peak's own isotope line sits one spacing above the same lobe of the peak, which is marked too, so it is not kept by this rule.
$^{29}\text{Si}$ is left out: its spacing falls 3.8 mDa below that of $^{13}\text{C}$, where the negative lobe of an ion's $^{13}\text{C}$ line sits.
The exemption follows the side-lobe guard of the reference engine, peaky ([sidelobes.py](#peaky-sl)).

Measured on the 165 demo raws and 88 Orbitrap files of three instruments, the instrument marks over half the centroids of 6.5 % of the peaks above the noise.
Of those, 22 % sit on an isotopologue line and are kept.
The rest are satellites, 5.0 % of the peaks above the noise; 2.1 % were not satellites before and now leave the peak list.
The summed spectrum itself does not change: it still holds every centroid the instrument recorded, marked or not.

#### References

- <a id="thermo-rfr"></a>Thermo Fisher Scientific. *RawFileReader*, the `PeakOptions`
  of a centroid (Reference, Exception, Merged, Fragmented).
  [link](https://github.com/thermofisherlsms/RawFileReader)
- <a id="mo09"></a>Mathur, R.; O'Connor, P. B. *Artifacts in Fourier transform mass
  spectrometry.* Rapid Communications in Mass Spectrometry 2009, 23(4):523–529.
  [link](https://doi.org/10.1002/rcm.3904)
- <a id="nist-iso"></a>Coursey, J. S.; Schwab, D. J.; Tsai, J. J.; Dragoset, R. A.
  *Atomic Weights and Isotopic Compositions with Relative Atomic Masses.* NIST Standard
  Reference Database 144. [link](https://www.nist.gov/pml/atomic-weights-and-isotopic-compositions-relative-atomic-masses)
- <a id="peaky-sl"></a>peaky, the reference assignment engine: `chem/sidelobes.py`, its
  isotopologue and fine-structure exemptions.
  [link](https://github.com/ultra-trace-systems/peaky/blob/main/peaky/chem/sidelobes.py)
