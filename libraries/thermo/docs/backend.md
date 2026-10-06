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
  - A field a backend cannot read is `None`. For OpenTFRaw that is `CycleNumber` alone (`OPENTFRAW_UNAVAILABLE_SCAN_STATS`), which nothing decodes. `PacketCount`, `SegmentNumber` and `ScanEventNumber` are read from the scan index (`data_size`, `scan_segment`, `scan_event`, exposed from 1.5.0 by [Sigilweaver/OpenTFRaw#56](https://github.com/Sigilweaver/OpenTFRaw/pull/56)), which is where Thermo reads them too; all three equal Thermo's values on every scan measured, and `test_backend_parity` asserts them per scan. `ScanEventNumber` previously came from the trailer's `Scan Event:` as a stand-in, and the two agree on every scan. How strong that agreement is differs by field: the packet size varies per scan, and two corpus files run eight and four scan events, so the event was compared across 0..7 and a mapping off by a constant would have shown; every file in reach is in segment 0, so `SegmentNumber` is confirmed to come from the right place but not against a varying value. Where the index holds `0xFFFF` for a field never set, Thermo reports `-1` for the scan event and `0` for the segment. That is every scan of an acquisition started with no method loaded: 14 of the internal regression corpus's 185 files, whose trailers say `Scan Event: 0`, and both backends report `-1` and `0` for them.
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
- **`scan_times(polarity, t_min, t_max, ms_type, stream)`**: Returns the start time in seconds of each selected scan.
- **`tic_per_scan(polarity, t_min, t_max, ms_type, stream)`**: Returns the start times and Total Ion Current (TIC) of the selected scans.
- **`scan_statistics(polarity, t_min, t_max, ms_type, stream)`**: Returns the selected scans' metrics (e.g., BasePeakIntensity, ScanType) defined in `SCAN_STAT_FIELDS`, plus `MsType`, with the same keys from both backends.
- **`scan_acquisition_settings(polarity, t_min, t_max, ms_type, stream)`**: Returns the selected scans' trailers as one table: `header_labels`, the trailer's labels, and `settings`, each scan's values in label order. A file defines its trailer's labels once for all of its scans. OpenTFRaw fills each row by label, so a scan whose trailer lacks a label, or that has no trailer record, gets `None` there and the table keeps a single label list.
- **`scan_filters()`**: Returns every scan's number, start time in seconds, filter text and experiment, in acquisition order, with no scan left out. The experiment is the method segment and the scan event that produced the scan: a method numbers its scan events within each of its segments, so the pair is what names one. Both are read from the scan index and counted from 1, as the method and the trailer's `Scan Segment:` and `Scan Event:` count them, and both are `None` where the file records no event. `scan_statistics()` reports the same two index words as the Thermo library does, from 0 with `-1` for an unset event; `_method_scan_event` is the one bridge between the conventions.
- **`scan_trailer(scan_number)`**: Returns one scan's trailer, the instrument's own `{label: value}` table. Values are text from the Thermo backend and typed scalars from OpenTFRaw.
- **`acquisition_parameters(max_scans, scan_numbers)`**: Summarises the trailers of up to `max_scans` scans, sampled evenly from `scan_numbers` (every MS1 scan by default), into the values constant across them and the names of those that vary.
- **`scan_indices(polarity, t_min, t_max, ms_type, stream)`**: Returns the 1-based numbers of the selected scans.
- **`mass_range()`**: Returns the run's `(low, high)` m/z range.

### Data Access and Processing

- **`profile_per_scan(polarity, t_min, t_max, ms_type, mz_min, mz_max, stream)`**: Retrieves the raw profile m/z and intensity arrays of each selected scan, with the scan times.
- **`centroids_per_scan(polarity, t_min, t_max, ms_type, mz_min, mz_max, stream)`**: Retrieves the centroided peaks (m/z, intensity, resolution, S/N) of each selected scan.
- **`centroids_meta()`**: Returns every scan's centroid m/z, intensity, resolution and noise, decoded from its centroid labels.
- **`average_profile(scan_indices, ppm, average)`**: Averages the selected scans' profiles in the frequency domain, where an ion's peak lines up across scans, and converts the result back to m/z, each peak on the intensity-weighted mean of the scans' calibrations. The result is the measured signal, which is also what the spectrum views draw.
- **`average_centroids(scan_indices, ppm, average)`**: Returns an approximation of centroids derived from an averaged profile.
- **`xic(mzs, ppm, polarity, t_min, t_max, ms_type, stream)`**: Generates an Extracted Ion Chromatogram within `ppm` of each target m/z across the selected scans.

### MS2 Specific Methods

- **`ms2_events_by_scan(polarity, t_min, t_max)`**: Decodes and returns the precursor m/z and activation (e.g. `hcd40.00`) for MS2 acquisition events. The activation is what separates the steps of a stepped-energy acquisition, whose scans share one precursor.
- **`ms2_acquisition_info(polarity, t_min, t_max)`**: Returns the MS2 isolation width and each MS2 scan's collision energy.
- **`ms2_centroids_for_scans(scan_indices)`**: Retrieves centroid data specifically for a set of MS2 scans.

## Scan Streams

`mascope_thermo.scan_filter` parses a scan filter (`FTMS - p NSI Full ms [40.0000-600.0000]`) into the signature that tells scan streams apart: analyzer, polarity, scan data type, source, source fragmentation, FAIMS CV, scan mode, MS order, the precursors of targeted MSn scans, and the scan ranges. `mascope_thermo.streams.scan_streams` groups a file's scans into streams, and reports per stream its scan event, scan count, blocks and time span, plus, for an MS1 stream, the acquisition parameters of its own scans. The converter stores the result in `.props` as `scan_streams`.

**A stream is the scans of one experiment of the acquisition method.** A method is built of experiments, each defining its scan parameters, and every scan records the one that produced it: its scan event, numbered within its segment (`scan_filters()`). So an MS1 scan belongs to its signature plus FT resolution *and* its segment and scan event, and two experiments are two streams whatever sets them apart - including a microscan count or an AGC target, which no filter shows.

- **What a stream is, and what it is called, are two things.** Its identity is `signature_key` - the signature and resolution as one line, `FTMS - p NSI Full ms [40.0000-600.0000] R=120000` - with `scan_segment` and `scan_event`, and it reads the same in every file of one method. Its `key` is its name in its own file: the `signature_key`, closed by the experiment only where another experiment of the same file shares the signature, as `... R=120000 event=2`, or `... R=120000 segment=2 event=1` outside the method's first segment. So a key is unique within a file and **not** stable across files: a run stopped before a repeated experiment came round again names the first one by its signature alone. Never compare streams across files by key, and compare the part of the identity the comparison is about: the method binding's signature class is built from `signature_key` alone, so it does not move with a method's layout, and no class moved when streams began to follow the experiment.
- **Nearly every file keeps the keys it had** before streams followed the experiment, because its signatures already separate its experiments: on the internal regression corpus that is 183 of 185 files. The other two repeat a scan definition later in their method, and go from four streams to eight and from two to four, each repeat one contiguous block.
- **A file that records no event** - an acquisition started with no method loaded, 14 of the 185 - is grouped by signature and resolution alone, as before, and its streams carry `scan_segment` and `scan_event` as `None`. A setting changed by hand part way through leaves no mark in it.
- **MSn scans are grouped by signature and resolution alone**, whatever they record, and an MSn stream carries neither `scan_segment` nor `scan_event`: a missing key says "not grouped by it", where `None` says "the file records none". Whether a dependent scan's event counts its experiment or its place in the cycle is not measured on any file in reach - the corpus holds no MSn scan - and a family split by it would be one stream per slot.
- **The pair is how the vendor library addresses a method's events; a second segment is not measured.** `IScanEvents` (`ThermoFisher.CommonCore.Data.Interfaces`, the type of an open file's `ScanEvents`) gives a count of `Segments`, `GetEventCount(segment)` for each, and `GetEvent(segment, eventNumber)`: an event has its number within a segment. On the 171 corpus files that record events, every scan's pair is one of the pairs its method's own table holds, and `test_scan_experiments_are_in_the_methods_table` asserts the same on the committed files where the Thermo library is installed. Every one of those files is in one segment, so grouping on the pair changes nothing there, and that a second segment starts again at event 1 is read off the interface, not off a file. It is what keeps a segmented method from pooling the event 1 of each of its segments.
- **Both backends read the same experiment** for every scan of all 185 files. `test_scan_experiments_match_thermo` asserts it per scan on the committed files where the Thermo library is installed, and a scripted reader on each side holds both wirings wherever the suite runs.

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

On the internal regression corpus, the census agrees between the backends on 184 of the 185 files, and the one difference is `sid=`. Three more files, each a single scan, used to open only in the Thermo library: OpenTFRaw's search for the trailer's layout failed on some files of fewer than five scans ([Sigilweaver/OpenTFRaw#54](https://github.com/Sigilweaver/OpenTFRaw/pull/54)). That is fixed from 1.5.0, and the pinned reader now carries the fix, so all 185 open.

### Selecting one stream

Every method that selects scans by polarity, time and MS order also takes `stream`, a stream key from the census, and so do the public reads built on them in `mascope_thermo.thermo` (`get_signal`, `compute_sum_signal`, `get_tic_per_scan`, `get_scan_timestamps`, `get_peak_timeseries`, `get_centroids`, `get_centroids_per_scan`). The MS2 reads do not take one yet. `None`, the default, selects as before, and reads no key.

- **The keys are the census's.** Selection compares `stream` with `mascope_thermo.streams.scan_stream_keys`, the function `scan_streams` groups by, so the scans selected for a stream are the ones the census counted into it. A backend reads the keys once per open file: a key holds the FT resolution, which is a trailer read per scan.
- **A stream is selected within the other filters.** A key of another polarity or MS order than the one asked for selects nothing, so a fragmentation stream needs `ms_type="Ms2"` or `None`. A selection that finds no scan raises `NoScansFoundError`, which names the stream.
- **A key the file does not hold is not an empty selection.** It raises `UnknownStreamError`, which lists the keys the file does hold: all of them in `.held`, and the first eight in its message, which travels into logs and has to stay bounded on a file of many streams. That error is deliberately neither a `NoScansFoundError` nor a `ValueError`: the code above the reader takes an empty selection to mean a polarity the file does not carry, or a window with nothing in it, and would read a stale key the same way. A key can go stale without being mistyped, because it is a name: the name of one experiment depends on what else its file holds, on the backend that rendered its filter, and on the version of the code that keys. A file with no scans holds no key, and a stream asked of it is the empty selection the scanless-file handling expects.
- **A key selects under the backend whose census reported it, and only there.** The backends render some filters differently (above), so they can key the same scans differently. Usually the other backend then holds no such key, and says so. Where it pools scans, it does not: on a file that records no experiment, with two scans rendered `... sid=20.00 Full ms [40.0000-160.0000]` and three without the token, the plain key selects scans 3 to 5 under a rendering that keeps `sid=` and scans 1 to 5 under one that drops it. One corpus file has the `sid=` difference.
- **The first scan.** A file's first scan is left out of every selection when its TIC is five times the median of the others or more. With no stream given, the others are every other scan of the file, of any polarity and MS order. With one given, they are the other scans of the first scan's own stream - the scans that measured what it measured - and a stream that does not hold the first scan loses nothing. On a file with one stream the two are the same comparison, so its stream selects exactly what no stream selects.

  On a file with several, they need not be. Of the internal regression corpus's two such files, one loses its first scan to the file-wide comparison and keeps it under its stream: its TIC equals the median of its own experiment's other scans, and is sixty times the file's median only because the other polarity's scans are that much weaker.
- **On the corpus**, under both backends: each of the 195 streams selects the scans the census counted into it, less a first scan left out in eleven single-stream files; the backends select the same scans for every key both report; and the selections of a file's streams together are the default selection in 184 of 185 files, the one above being the other.

## Underlying Algorithms

### Frequency-Domain Averaging

The `average_profile` algorithm operates in the **frequency domain**.

- **Grid**: One point per native FFT bin, at the mean of the frequencies the scans sampled in it; a constant **0.2 ppm** m/z grid only for scans without Conversion Parameters.
- **Sum**: Each scan is interpolated onto the grid and added from **0.5** bin before its first stored sample to **0.5** bin after its last, which takes in the grid points of its own end samples, and nowhere further than **2.0** bins from one of its samples, so it adds nothing across the stretches it stored nothing in.
- **m/z Axis**: The frequency grid is converted back to m/z peak by peak. Each profile peak, the samples between two valleys, is written on the mean of the scans' Conversion Parameters B and C weighted by what each scan contributes to its tallest sample, which is the calibration its averaged centroid sits on. Neighbouring peaks that would cross share one calibration. Nothing is fitted to the centroids.
- **S/N Scaling**: Averaged Signal-to-Noise is scaled by $n/\sqrt{N}$.

### Averaged Centroids and Baseline

- **Peak Sourcing**: Peak heights are refined from the profile apex within a 3.0 ppm window.
- **Centroid Merging**: Centroids are merged if their gap is below **0.5 \* local FWHM**.
- **Zero-filling**: Baseline zeros are placed 2.0 ppm outside cluster edges; a gap narrower than 4 ppm takes one zero, at its middle.
  A cluster ends where the step to the next occupied bin exceeds **4.0 \* the median step**, both relative, so in bins the threshold is lower the higher the m/z. The step is read off the frequency grid, so the boundaries do not depend on the calibrations the peaks are written on; it is read off the m/z axis only for scans without Conversion Parameters.
