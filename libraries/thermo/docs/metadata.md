# Raw File Metadata

Orbitrap raw files contain a rich set of metadata variables that provide critical context for interpreting the mass spectrometry data.

The list focuses on the metadata fields relevant for Orbitrap Exploris instruments and may not be exhaustive for all Thermo raw file types or models.

## Spectrum / Scan Variables

- SpectrumPacketType: Data packet format/type for the spectrum.
- HighMass: Upper m/z limit of the scan range.
- LowMass: Lower m/z limit of the scan range.
- LongWavelength: Maximum wavelength measured (for UV/optical data).
- ShortWavelength: Minimum wavelength measured.
- BasePeakIntensity: Intensity of the most intense peak in the scan.
- BasePeakMass: m/z value of the most intense peak.
- TIC: Total ion current, sum of all ion intensities in the scan.
- StartTime: Scan start time (in minutes).
- PacketCount: Size of the scan's data packet in bytes, as recorded in the scan index.
- NumberOfChannels: Number of detector channels.
- ScanNumber: Sequential index of the scan in the run.
- ScanEventNumber: Instrument-defined event identifier for the scan.
- SegmentNumber: Acquisition segment identifier within the method.
- IsCentroidScan: Indicates centroid (true) vs profile (false) data.
- Frequency: Sampling or acquisition frequency.
- IsUniformTime: Indicates constant time spacing between data points.
- AbsorbanceUnitScale: Scaling factor for absorbance intensity values.
- WavelengthStep: Increment between consecutive wavelength points.
- ScanType: The scan filter, e.g. `FTMS + p NSI Full ms [40.0000-600.0000]`. Mascope reports the MS order alone as `MsType` (e.g., `Ms`, `Ms2`).
- CycleNumber: Acquisition cycle index grouping related scans.

## Instrument / Metadata Variables

- Name: Instrument name or identifier.
- Model: Instrument model designation.
- SerialNumber: Manufacturer-assigned instrument serial number.
- SoftwareVersion: Version of controlling/acquisition software.
- HardwareVersion: Instrument hardware/firmware version.
- Units: Measurement units for data values.
- Flags: Bitwise status or metadata flags.
- AxisLabelX: Label for x-axis (m/z).
- AxisLabelY: Label for y-axis (intensity).
- IsValid: Indicates metadata integrity/validity.
- HasAccurateMassPrecursors: Indicates high-accuracy precursor m/z assignment.

## Acquisition / Scan Settings

- Scan Description: Text description of scan method/settings.
- Multiple Injection: Indicates multiple ion injections per scan.
- Multi Inject Info: Details of multi-injection configuration.
- AGC: Automatic gain control status or mode.
- Micro Scan Count: Number of microscans averaged per scan.
- Scan Segment: Method segment index for acquisition.
- Scan Event: Specific scan event identifier within segment.
- Master Index: Index linking related scans in a cycle (e.g., MS1 and its associated MS2 scans).
- Master Scan Number: Reference scan number for grouped scans (e.g., MS1 scan number for MS2 scans).
- Charge State: Assigned precursor ion charge.
- Monoisotopic M/Z: Calculated monoisotopic precursor m/z.
- Error in isotopic envelope fit: Fit error for isotope pattern matching.
- Ion Injection Time (ms): Actual ion accumulation time in milliseconds.
- Max. Ion Time (ms): Maximum allowed ion accumulation time.
- FT Resolution: Fourier transform resolving power setting.
- MS2 Isolation Width: m/z window width for precursor isolation.
- MS2 Isolation Offset: Offset applied to isolation window center.
- AGC Target: Target ion population for AGC.
- HCD Energy: Normalized collision energy for higher-energy collisional dissociation (HCD).
- HCD Energy V: Collision energy in volts.
- Analyzer Temperature: Temperature of the mass analyzer.

## Mass Calibration Variables

- Conversion Parameter B: Calibration coefficient B for m/z conversion.
- Conversion Parameter C: Calibration coefficient C for m/z conversion.
- Temperature Comp. (ppm): Mass correction for temperature drift (ppm).
- RF Comp. (ppm): Mass correction for radio frequency deviations (ppm).
- Space Charge Comp. (ppm): Mass correction for ion space charge effects (ppm).
- Resolution Comp. (ppm): Mass correction based on resolution deviations (ppm).
- Number of Lock Masses: Count of configured lock mass references.
- Lock Mass #1 (m/z): m/z of first lock mass.
- Lock Mass #2 (m/z): m/z of second lock mass.
- Lock Mass #3 (m/z): m/z of third lock mass.
- LM Search Window (ppm): Tolerance window for lock mass search (ppm).
- LM Search Window (mmu): Tolerance window for lock mass search (millimass units).
- Number of LM Found: Number of lock masses detected in scan.
- Last Locking (sec): Time since last successful lock mass correction.
- LM m/z-Correction (ppm): Applied lock mass mass correction (ppm).

## Ion Optics Settings

- S-Lens RF Level: RF voltage level applied to S-lens for ion transmission efficiency.

## Diagnostic Data

- Application Mode: Instrument operation mode preset.
- Mild Trapping Mode: Indicates reduced ion trapping conditions.
- APD: Advanced peak detection status or parameter.
- OT Intens Comp Factor: Orbitrap intensity compensation factor.
- Res. Dep. Intens: Resolution-dependent intensity correction applied.
- Q Trans Comp: Quadrupole transmission compensation factor.
- PrOSA NumF: Predictive oscillation signal analysis parameter (numeric factor).
- PrOSA Comp: PrOSA compensation factor.
- PrOSA ScScr: PrOSA scan score or quality metric.
- RawOvFt: Ratio of raw to Fourier-transformed signal.
- Dynamic RT Shift (min): Real-time retention time correction applied.
- LC FWHM parameter: Peak width parameter from LC (full width at half maximum).
- PS Inj. Time (ms): Ion injection time for phase space or predictive scan.
- AGC PS Mode: AGC mode for phase space control.
- AGC PS Diag: Diagnostic value for AGC phase space.
- AGC Target Adjust: Adjustment factor applied to AGC target.
- AGC Diag 1: First AGC diagnostic metric.
- AGC Diag 2: Second AGC diagnostic metric.
- HCD abs. Offset: Absolute offset applied to HCD energy.
- Source CID eV: In-source collision-induced dissociation energy (eV).
- AGC Fill: Degree of ion trap filling relative to AGC target.
- Injection t0: Initial time offset for ion injection.
- t0 FLP: Time-zero correction for flight path.
- Iso Para R: Isolation parameter ratio or scaling factor.
- Inj Para R: Injection parameter ratio or scaling factor.
- Access Id: Internal access or record identifier.
- Analog In A (V): Voltage reading from analog input channel A.
- Analog In B (V): Voltage reading from analog input channel B.
- FAIMS Attached: Indicates FAIMS device presence.
- FAIMS Voltage On: Indicates FAIMS voltage enabled.
- FAIMS CV: Compensation voltage applied in FAIMS.