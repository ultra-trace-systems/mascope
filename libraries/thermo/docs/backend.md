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
- **`SCAN_STAT_FIELDS`**: Per-scan statistics such as `BasePeakIntensity`, `TIC`, `ScanType`, and `IsCentroidScan`, named as in Thermo's `ScanStats`. Both backends return every field for every scan. OpenTFRaw fills them as follows:
  - `ScanType` is the scan filter as OpenTFRaw renders it (see [Scan Streams](#scan-streams) for how the renderings differ), and `IsCentroidScan` is read from that filter's scan data type.
  - `ScanEventNumber` is the trailer's `Scan Event:` minus one.
  - The UV, PDA and analog detector fields (`Frequency`, the wavelength fields, `NumberOfChannels`, `IsUniformTime`, `AbsorbanceUnitScale`, `WavelengthStep`) hold the fixed values Thermo's `ScanStats` holds for every MS scan (`MS_SCAN_DETECTOR_STATS`).
  - A field a backend cannot read is `None`. For OpenTFRaw those are `PacketCount`, `SegmentNumber` and `CycleNumber` (`OPENTFRAW_UNAVAILABLE_SCAN_STATS`). It decodes the scan-index words behind the first two but does not pass them to Python ([Sigilweaver/OpenTFRaw#56](https://github.com/Sigilweaver/OpenTFRaw/pull/56)).
- **`_OTF_TRAILER_FIELDS`**: Descriptive labels for acquisition data decoded by OpenTFRaw (Ion Injection Time, Precursor m/z).

## Public Methods

The `ReaderBackend` protocol defines the standard interface for all backend implementations.
All methods returning time values convert internal units (minutes) to **seconds** for consistency.

### File and Instrument Metadata

- **`created()`**: Returns the creation timestamp of the raw data file.
- **`instrument_details()`**: Returns a dictionary of instrument metadata (e.g., Model, SerialNumber) as defined by `INSTRUMENT_FIELDS`.
- **`num_scans()`**: Returns the total number of scans in the file.

### Scan-Level Metadata

- **`polarities()`**: Returns the set of polarities (`+`, `-`) present in the file.
- **`scan_times(polarity, t_min, t_max, ms_type)`**: Returns the start time in seconds of each selected scan.
- **`tic_per_scan(polarity, t_min, t_max, ms_type)`**: Returns the start times and Total Ion Current (TIC) of the selected scans.
- **`scan_statistics(polarity, t_min, t_max, ms_type)`**: Returns the selected scans' metrics (e.g., BasePeakIntensity, ScanType) defined in `SCAN_STAT_FIELDS`, plus `MsType`, with the same keys from both backends.
- **`scan_acquisition_settings(polarity, t_min, t_max, ms_type)`**: Returns a per-scan table of acquisition settings for the selected scans: the full trailer from the Thermo backend, the `_OTF_TRAILER_FIELDS` subset from OpenTFRaw.
- **`scan_filters()`**: Returns every scan's number, start time in seconds and filter text, in acquisition order, with no scan left out.
- **`scan_trailer(scan_number)`**: Returns one scan's trailer, the instrument's own `{label: value}` table. Values are text from the Thermo backend and typed scalars from OpenTFRaw.
- **`acquisition_parameters(max_scans, scan_numbers)`**: Summarises the trailers of up to `max_scans` scans, sampled evenly from `scan_numbers` (every MS1 scan by default), into the values constant across them and the names of those that vary.
- **`scan_indices(polarity, t_min, t_max, ms_type)`**: Returns the 1-based numbers of the selected scans.
- **`mass_range()`**: Returns the run's `(low, high)` m/z range.

### Data Access and Processing

- **`profile_per_scan(polarity, t_min, t_max, ms_type, mz_min, mz_max)`**: Retrieves the raw profile m/z and intensity arrays of each selected scan, with the scan times.
- **`centroids_per_scan(polarity, t_min, t_max, ms_type, mz_min, mz_max)`**: Retrieves the centroided peaks (m/z, intensity, resolution, S/N) of each selected scan.
- **`centroids_meta()`**: Returns every scan's centroid m/z, intensity, resolution and noise, decoded from its centroid labels.
- **`average_profile(scan_indices, ppm, average, reconstruct)`**: Executes frequency-domain averaging across multiple scans, including m/z calibration, jitter correction, and $n/\sqrt{N}$ S/N scaling.
- **`average_centroids(scan_indices, ppm, average)`**: Returns an approximation of centroids derived from an averaged profile.
- **`xic(mzs, ppm, polarity, t_min, t_max, ms_type)`**: Generates an Extracted Ion Chromatogram within `ppm` of each target m/z across the selected scans.

### MS2 Specific Methods

- **`ms2_events_by_scan(polarity, t_min, t_max)`**: Decodes and returns the precursor m/z and activation (e.g. `hcd40.00`) for MS2 acquisition events. The activation is what separates the steps of a stepped-energy acquisition, whose scans share one precursor.
- **`ms2_acquisition_info(polarity, t_min, t_max)`**: Returns the MS2 isolation width and each MS2 scan's collision energy.
- **`ms2_centroids_for_scans(scan_indices)`**: Retrieves centroid data specifically for a set of MS2 scans.

## Scan Streams

`mascope_thermo.scan_filter` parses a scan filter (`FTMS - p NSI Full ms [40.0000-600.0000]`) into the signature that tells scan streams apart: analyzer, polarity, scan data type, source, source fragmentation, FAIMS CV, scan mode, MS order, the precursors of targeted MSn scans, and the scan ranges. `mascope_thermo.streams.scan_streams` groups a file's scans by that signature plus the trailer's FT resolution, and reports per stream its scan count, blocks and time span, plus, for an MS1 stream, the acquisition parameters of its own scans. The converter stores the result in `.props` as `scan_streams`.

The two backends render some filters differently, because OpenTFRaw does not render every token of the filter.

- **`lock`.** The Thermo library writes `lock` on each scan that found its lock mass (its trailer's `Number of LM Found` is above zero), and OpenTFRaw never does ([Sigilweaver/OpenTFRaw#58](https://github.com/Sigilweaver/OpenTFRaw/issues/58)). `lock` describes one scan's outcome, so it is left out of the signature.
- **Other tokens OpenTFRaw leaves out:**
  - the source fragmentation of a scan acquired with in-source CID (`sid=20.00`, [Sigilweaver/OpenTFRaw#57](https://github.com/Sigilweaver/OpenTFRaw/issues/57));
  - the FAIMS compensation voltage (`cv=`);
  - flags such as wideband activation (`w`) and multiplexing (`msx`);
  - the `{segment,event}` prefix.

  The signature leaves out the prefix too. The others stay in it, so where a file carries one, the stream keys differ between the backends. On an LTQ FT Ultra file the MS2 stream is `ITMS + c ESI d w Full ms2 ...` under the Thermo library and has no `w` under OpenTFRaw. Scans that differ only in such a token pool into one stream under OpenTFRaw.
- **Precision.** OpenTFRaw writes m/z to four decimals, where the Thermo library follows the file's precision. The parser normalises numbers, so this does not change a key.

On the internal regression corpus, the census agrees between the backends on 181 of the 182 files both read, and the one difference is `sid=`. Three more files, each a single scan, open only in the Thermo library: OpenTFRaw's search for the trailer's layout fails on some files of fewer than five scans ([Sigilweaver/OpenTFRaw#54](https://github.com/Sigilweaver/OpenTFRaw/pull/54)).

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
