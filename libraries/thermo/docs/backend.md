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
  - A field a backend cannot read is `None`. For OpenTFRaw that is `CycleNumber` alone (`OPENTFRAW_UNAVAILABLE_SCAN_STATS`), which nothing decodes. `PacketCount`, `SegmentNumber` and `ScanEventNumber` are read from the scan index (`data_size`, `scan_segment`, `scan_event`, exposed from 1.5.0 by [Sigilweaver/OpenTFRaw#56](https://github.com/Sigilweaver/OpenTFRaw/pull/56)), which is where Thermo reads them too; all three equal Thermo's values on every scan measured, and `test_backend_parity` asserts them per scan. `ScanEventNumber` previously came from the trailer's `Scan Event:` as a stand-in, and the two agree on every scan. Where the index holds `0xFFFF` for a field never set, Thermo reports `-1` for the scan event and `0` for the segment; no file here carries one, so that mapping is a guard rather than something the corpus exercises.
- **The trailer** is the instrument's own table of per-scan acquisition settings (`FT Resolution:`, `AGC Target:`, `Ion Injection Time (ms):` and dozens more), so it is not a fixed field set: its labels depend on the instrument. Both backends report it whole, under the same labels in the same order (`scan_trailer`, `scan_acquisition_settings`). OpenTFRaw reads it with `scan_parameters()`. The values keep each backend's types:
  - The Thermo library gives text: numbers in the machine's number format, rounded to the digits it displays (`0,11`); switches as `On`/`Off` or `Yes`/`No`; an empty string for a section heading such as `=== Mass Calibration: ===:`.
  - OpenTFRaw gives the stored values: numbers at full precision (`0.11146822731511463`), `True`/`False`, and `None` for a section heading.

  On the internal regression corpus, the demo bundle and the committed sample files (345 Orbitrap files, all of their scans MS1), the backends report the same labels on every scan, and every value agrees within the digits the Thermo library displays. None of those trailers repeats a label. `scan_parameters()` returns a dict, so a label an instrument repeated would appear once in OpenTFRaw's table and each time it occurs in the Thermo library's; `test_scan_acquisition_settings_match_thermo` fails on such a file and names the label.

## Public Methods

The `ReaderBackend` protocol defines the standard interface for all backend implementations.
All methods returning time values convert internal units (minutes) to **seconds** for consistency.

### File and Instrument Metadata

- **`created()`**: Returns the creation timestamp of the raw data file.
- **`instrument_details()`**: Returns a dictionary of instrument metadata (e.g., Model, SerialNumber) as defined by `INSTRUMENT_FIELDS`. OpenTFRaw fills `Model` and `Name` only. From 2.0.0 it falls back to the InstID block when a file has no embedded instrument method ([Sigilweaver/OpenTFRaw#59](https://github.com/Sigilweaver/OpenTFRaw/issues/59)), so such a file now reports a model where it reported none -- under OpenTFRaw's registry name, which can be shorter than the Thermo library's string (`Q Exactive Plus` against `Q Exactive Plus Orbitrap`). Nothing keys on it: the scan signature does not include the model, and the only readers are the census line `mascope file scans` prints and the file's stored metadata.
- **`num_scans()`**: Returns the total number of scans in the file.

### Scan-Level Metadata

Scan selection reads metadata only. The OpenTFRaw backend builds it from `scan_table()`, which reads the scan index and the scan events and touches no peak data; `iter_scans()` would decode every scan's arrays for a selection that only looks at times, polarities and MS orders. On the longest file of the internal regression corpus (1,486 scans) that is 88.9 ms against 19.0 ms, and on a 61-scan file of similar size 22.5 ms against 0.8 ms. `xic()` reads the peaks it needs per scan, for the scans it selected.

- **`polarities()`**: Returns the set of polarities (`+`, `-`) present in the file.
- **`scan_times(polarity, t_min, t_max, ms_type)`**: Returns the start time in seconds of each selected scan.
- **`tic_per_scan(polarity, t_min, t_max, ms_type)`**: Returns the start times and Total Ion Current (TIC) of the selected scans.
- **`scan_statistics(polarity, t_min, t_max, ms_type)`**: Returns the selected scans' metrics (e.g., BasePeakIntensity, ScanType) defined in `SCAN_STAT_FIELDS`, plus `MsType`, with the same keys from both backends.
- **`scan_acquisition_settings(polarity, t_min, t_max, ms_type)`**: Returns the selected scans' trailers as one table: `header_labels`, the trailer's labels, and `settings`, each scan's values in label order. A file defines its trailer's labels once for all of its scans. OpenTFRaw fills each row by label, so a scan whose trailer lacks a label, or that has no trailer record, gets `None` there and the table keeps a single label list.
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

  The `lock` and `sid=` issues are still open upstream. The values are partly reachable without the rendering -- every scan carries `is_wideband`, and from 1.6.0 the scan dictionaries carry `faims_cv` and an `extra` table with the source-CID energy and the lock-mass count -- but composing the tokens here was considered and **declined**:

  - `sid=` would not close the gap it exists for. The `extra` entry is a lookup of the trailer's source-CID label, not the scan event, and the one corpus file whose census differs carries no such label at all. Composing it would make the filters agree on the files that never disagreed and still disagree on the one that does, while retiring a gap the parity suite currently states plainly.
  - `lock` is deliberately outside the signature, so composing it changes no key. Its `extra` entry also falls back from the number of lock masses *found* to the number the method *configured*, which is a different quantity and not the one the Thermo library writes `lock` from.
  - `cv=` is reachable and is part of the signature, but nothing here acquires with FAIMS: every corpus and demo file reports the voltage off. Rendering it would put untested text into a key that routes.

  So the asymmetric comparison in `test_backend_parity._assert_same_filter` stays as it is: whatever OpenTFRaw writes must agree, and what it omits is allowed. Tightening it is worth revisiting when a FAIMS file or a source-CID file lands in the corpus, or when the upstream issues close.
- **Precision.** OpenTFRaw writes m/z to four decimals, where the Thermo library follows the file's precision. The parser normalises numbers, so this does not change a key.

On the internal regression corpus, the census agrees between the backends on 181 of the 182 files both read, and the one difference is `sid=`. Three more files, each a single scan, used to open only in the Thermo library: OpenTFRaw's search for the trailer's layout failed on some files of fewer than five scans ([Sigilweaver/OpenTFRaw#54](https://github.com/Sigilweaver/OpenTFRaw/pull/54)). That is fixed from 1.5.0, and the pinned reader now carries the fix, so all 185 open.

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
