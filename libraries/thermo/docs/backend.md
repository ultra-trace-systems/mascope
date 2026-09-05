# Thermo `backend` Module Documentation

## Overview and Environment Setup

The backend module provides a **reader-backend seam** for `mascope_thermo`, allowing the library to switch between proprietary and open-source data readers.

- **Default Environment**: The open-source Rust-based [`OpenTFRaw`](https://github.com/Sigilweaver/OpenTFRaw) reader is the default backend.
- **Thermo Opt-in**: To use Thermo's [`RawFileReader`](https://github.com/thermofisherlsms/RawFileReader), users must:
  1. Set the `MASCOPE_THERMO_BACKEND` environment variable to `thermo`.
  2. Point `MASCOPE_THERMO_DLL_DIR` to the directory containing the proprietary Thermo DLLs.
- **Dependencies**: The default path requires the `opentfraw` package from PyPI. .NET loading for the Thermo backend is performed lazily to ensure the package imports without requiring proprietary binaries.

## Backend Resolution

Backends are resolved through the `open_backend(datafile_path)` function.
It selects the implementation based on the `MASCOPE_THERMO_BACKEND` environment variable.
Public functions in `mascope_thermo` are backend-agnostic, interacting only with the resolved instance via a standardized protocol.

## API Contract: `ReaderBackend`

All implementations must satisfy the `ReaderBackend` protocol, which defines a capability interface rather than emulating specific .NET objects.

### Core Capabilities

- **Data Access**: Profile arrays, centroids, multi-scan averaging, and XIC.
- **Metadata**: Access to run headers and per-scan trailers.
- **Standardized Units**: Scan times are converted from the backend-reported minutes to **seconds** for the public API.

### Standardized Field Sets

To ensure consistency across backends, the following field sets are enforced:

- **`INSTRUMENT_FIELDS`**: Serialized instrument metadata including `Model`, `SerialNumber`, and `SoftwareVersion`.
- **`SCAN_STAT_FIELDS`**: Per-scan statistics such as `BasePeakIntensity`, `TIC`, `Frequency`, and `IsCentroidScan`.
- **`_OTF_TRAILER_FIELDS`**: Descriptive labels for acquisition data decoded by OpenTFRaw (Ion Injection Time, Precursor m/z).

## Public Methods

The `ReaderBackend` protocol defines the standard interface for all backend implementations.
All methods returning time values convert internal units (minutes) to **seconds** for consistency.

### File and Instrument Metadata

- **`created()`**: Returns the creation timestamp of the raw data file.
- **`instrument_details()`**: Returns a dictionary of instrument metadata (e.g., Model, SerialNumber) as defined by `INSTRUMENT_FIELDS`.
- **`num_scans()`**: Returns the total number of scans in the file.

### Scan-Level Metadata

- **`polarities()`**: Returns the ion polarity (+ or -) for each scan.
- **`scan_times()`**: Returns the acquisition start time for each scan in seconds.
- **`tic_per_scan()`**: Returns the Total Ion Current (TIC) for every scan.
- **`scan_statistics(scan_number)`**: Retrieves per-scan metrics (e.g., BasePeakIntensity, Frequency) defined in `SCAN_STAT_FIELDS`.
- **`scan_acquisition_settings(scan_number)`**: Returns detailed acquisition parameters.
- **`scan_indices()`**: Returns the integer indices for all scans in the raw file.
- **`mass_range(scan_number)`**: Returns the m/z range for the specified scan.

### Data Access and Processing

- **`profile_per_scan(scan_number)`**: Retrieves the raw profile m/z and intensity arrays for a single scan.
- **`centroids_per_scan(scan_number)`**: Retrieves the centroided peaks (m/z and intensity) for a given scan.
- **`centroids_meta(scan_number)`**: Returns resolution and Signal-to-Noise (S/N) for each centroided peak per scan decoded from the scan's centroid labels.
- **`average_profile(scan_indices)`**: Executes frequency-domain averaging across multiple scans, including m/z calibration, jitter correction, and $n/\sqrt{N}$ S/N scaling.
- **`average_centroids(scan_indices)`**: Returns an approximation of centroids derived from an averaged profile.
- **`xic(mz_range, scan_range)`**: Generates an Extracted Ion Chromatogram for a target m/z window across a range of scans.

### MS2 Specific Methods

- **`ms2_events_by_scan(scan_number)`**: Decodes and returns the precursor m/z and activation (e.g. `hcd40.00`) for MS2 acquisition events. The activation is what separates the steps of a stepped-energy acquisition, whose scans share one precursor.
- **`ms2_acquisition_info(scan_number)`**: Returns specialized MS2 metadata, such as isolation width and collision energy.
- **`ms2_centroids_for_scans(scan_indices)`**: Retrieves centroid data specifically for a set of MS2 scans.

## Underlying Algorithms

### Frequency-Domain Averaging

The `average_profile` algorithm operates in the **frequency domain**.

- **Grid Resolution**: Uses a constant **0.2 ppm** output grid to sample per-peak FWHM while collapsing jitter.
- **m/z Correction**: The profile axis is aligned to centroid labels by sampling 8 scans for reference, anchoring on peaks with at least 60 ppm separation, and applying a low-order correction fit.
- **S/N Scaling**: Averaged Signal-to-Noise is scaled by $n/\sqrt{N}$.

### Profile Reconstruction

- **Gaussian Reconstruction**: Displayed profiles are reconstructed as a Gaussian-per-centroid to exactly overlay centroids.
- **Peak Sourcing**: Peak heights are refined from the profile apex within a 3.0 ppm window.
- **Centroid Merging**: Centroids are merged if their gap is below **0.5 \* local FWHM**.
- **Zero-filling**: Baseline zeros are placed 2.0 ppm outside cluster edges.
  Boundaries are defined where gaps exceed **4.0 \* median spacing**.
