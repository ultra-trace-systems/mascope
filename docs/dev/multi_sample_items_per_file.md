# Multiple sample items per sample file - status and design

Goal: let a user generate more than one sample item from the same sample
file, either by **time range** (e.g. one item per autosampler injection, or a
background window and a signal window) or by **scan parameters** (e.g. one
item per consecutive run of scans in a polarity in a dual-polarity file).

This document records the verified current state of the codebase and a phased
implementation plan, so that work can be picked up without re-doing the
analysis. Findings verified against commit `76a3994d2` (2026-08-13); line
numbers are approximate anchors, function names are the stable reference.

## Current status: what already exists

The groundwork is substantially in place, and polarity-based splitting
already works for the acquisition pipeline.

### Data model (ready)

- `SampleItem` (`server/backend/src/mascope_backend/db/models.py`, ~line 489)
  carries `t0`, `t1`, `polarity`, `ionization_mode_id`, `tic`, `filter_id`.
  Its docstring already states that each item is a time-windowed segment of a
  sample file and that multiple items can be created per file.
- `SampleFile.polarity` is a `String(4)` and can hold `"+-"` for
  dual-polarity files (same module, ~line 478).
- `filter_id` is **not** an instrument scan filter: it is a 6-char
  user-supplied ID for physical-filter workflows
  (`FILTER_REGENERATION` / `FILTER_BACKGROUND` types; see
  `api/models/sample/items/config.py`). It is orthogonal to this feature.

### Creation API (ready)

- `create_sample_items`
  (`api/controllers/sample/items/sample_items_controller.py`, ~line 190)
  bulk-creates items, accepts per-item `polarity`/`t0`/`t1`, and when
  `tic`/`t0`/`t1` are omitted computes them per polarity via
  `mascope_signal.compute.get_tic_per_scan(base_filename, polarity=...)`.
- `process_sample_item`
  (`api/controllers/sample/items/sample_items_process_controller.py`)
  chains create -> match for a single item from an existing sample file.
- Pydantic validation exists: `t0 < t1`, non-negative times, filter-ID
  format, per-type filter-ID rules
  (`api/models/sample/items/sample_item_pydantic_model.py`).

### Acquisition auto-processing (polarity split already works)

- `create_acquisition_batches_and_items`
  (`api/controllers/sample/files/process/service.py`, ~line 639) creates one
  ACQUISITION sample item **per ionization mode resolved from the file** - a
  dual-polarity file already yields two items, each in its own daily batch.
- `resolve_ionization_modes_by_tokens`
  (`api/new/ionization/modes/util.py`) explicitly handles files with one or
  two polarities.
- No `t0`/`t1` are set there (items default to the file's full time span per
  polarity). No time- or segment-based splitting exists anywhere.

### Data access layer (ready)

- `get_tic_per_scan` and `get_scan_timestamps`
  (`libraries/signal/src/mascope_signal/compute.py`) accept polarity and
  time bounds; the Thermo backend filters scans by polarity natively
  (`libraries/thermo/src/mascope_thermo/thermo.py`).
- `extract_peaks`
  (`api/controllers/samples/lib/samples_peaks.py`) supports polarity +
  `t_min`/`t_max` + `mz_min`/`mz_max`. Full-sample aggregation uses
  pre-computed whole-file sums (fast path); time-windowed aggregation sums
  per-peak timeseries and **excludes peaks whose timeseries have not been
  computed** (`is_timeseries_computed` flag, warning returned to caller).
- The converted peak store (xarray via `mascope_file.io.load_peak_data`) has
  a per-peak `polarity` coordinate, whole-file `sum_peak_areas` /
  `sum_peak_heights`, and per-peak timeseries variables.
- REST routes for sample spectra/peaks already accept `t_min`/`t_max`
  (`api/routes/samples/samples_routes.py`). The frontend does not use them
  yet (SDK-facing only).

### Frontend (partial)

- `DialogSampleOp.vue` (single-file "Process selected" flow) shows a
  polarity dropdown when the file polarity is `"+-"` and posts to
  `process_sample_item`. No time-window inputs anywhere.
- There is no TIC-vs-time / chromatogram component in the frontend (only
  per-match timeseries in `ChartMatchTimeseries`), so there is currently no
  UI surface to host a time-range selection.

## The gaps

1. **`t0`/`t1` are stored but never applied downstream.** This is the
   load-bearing gap. Matching goes
   `match_compute_sample` -> `compute_and_create_sample_match_isotope_data`
   (`api/controllers/match/lib/match_compute.py`, ~line 104) ->
   `mascope_match.compute.isotopes.compute_match_isotopes(filename, ...,
   polarity)`. Polarity is forwarded; the time window is not. A
   time-windowed item today is matched against the **whole file**. The
   spectrum/peaks endpoints likewise only time-filter when the caller passes
   `t_min`/`t_max` explicitly - the item's own `t0`/`t1` act only as
   validation bounds.
2. **TIC bug at creation.** In `create_sample_items`, if the caller supplies
   `t0`/`t1` but omits `tic`, the computed TIC sums **all** scans of the
   polarity, not the window (the `tic_computation_needed` block, ~lines
   250-266 of `sample_items_controller.py`).
3. **No segment enumeration or split operation.** Nothing enumerates
   consecutive same-polarity scan runs in a file or proposes time segments;
   there is no endpoint to create several items from one file in one call.
4. **No UI for time ranges.** No chromatogram, no `t0`/`t1` inputs in any
   dialog.

## Design

### Phase 1 - make `t0`/`t1` semantically real (backend, no schema change)

Establish the invariant: *a sample item's signal is the aggregate of scans
of its polarity within `[t0, t1]`*.

- In the sample peaks/spectrum controllers, default `t_min`/`t_max` to the
  item's stored `t0`/`t1` whenever they are narrower than the file's bounds.
  Keep the pre-computed-sums fast path when they equal the file bounds
  (i.e. whole-file items behave exactly as today).
- Add optional `t_min`/`t_max` parameters to `compute_match_isotopes`
  (`libraries/match/src/mascope_match/compute/isotopes.py`) and pass
  `sample.t0`/`sample.t1` from `match_compute.py`. Intensity aggregation for
  windowed items follows the same per-peak-timeseries path as
  `_aggregate_time_range` in `samples_peaks.py`.
- Fix the creation TIC to sum only within the provided window (gap 2).
- Calibration stays per-file: `calibration_mz_calibrate_sample` writes
  `sample_file.mz_calibration`, and the mass axis is a property of the
  acquisition. Windowed items inherit the file calibration.
- Caveat to carry through: peaks with `is_timeseries_computed == False` are
  excluded from windowed aggregation. Windowed matching must surface the
  same warning `extract_peaks` already emits, so users know to re-run peak
  detection.

Phase 1 alone turns the existing groundwork into a working feature: after
it, any item created with a window via the existing API is correctly
windowed everywhere.

### Phase 2 - split/segment API

- `GET /sample/files/{id}/segments?by=polarity|time` - returns proposed
  segments (consecutive same-polarity scan runs derived from per-scan
  polarity metadata, which the Thermo backend exposes) plus a per-scan TIC
  trace for preview.
- `POST /sample/files/{id}/process` accepting a **list** of
  `{name, polarity, t0, t1, sample_item_type, sample_item_attributes,
  ionization_mode_id}` - a thin loop over the existing
  create -> calibrate-once-per-mode -> match pipeline (reuse the loop shape
  of `_auto_process_sample_file`). "Split by polarity" is then just the
  client posting back the segments the GET proposed.

### Phase 3 - frontend

- Raw-files tab, process dialog: a TIC-vs-time chromatogram (backed by a
  lightweight `GET /sample/files/{id}/tic?polarity=` endpoint) with plotly
  range selection appending segments to a small editable table, plus an
  "auto-split by polarity" button backed by the segments endpoint.
- `DialogSampleOp.vue`: editable `t0`/`t1` fields. Editing them on an
  existing item must flag the batch for rematch (the
  `update_sample_item` -> batch-status machinery already exists).

### Deferred: m/z-range splitting

Per-item m/z bounds would need new columns (`mz0`/`mz1` mirroring
`t0`/`t1`) plus a migration. `extract_peaks` already supports
`mz_min`/`mz_max`, so the plumbing cost is low later, but time + polarity
cover the stated use cases and scan-filter-based splitting depends on an
unverified assumption (below). Ship phases 1-3 first.

## Open questions

- Does the converted zarr store retain per-scan filter/scan-range metadata
  (Thermo scan filter strings)? Per-scan **polarity** is available (Thermo
  backend natively; the peak store has a per-peak polarity coordinate), but
  splitting by arbitrary scan filters needs verification on the
  file-converter side (`server/backend/src/mascope_backend/file_converter/`).
- Whether Tofwerk `.h5` files can be dual-polarity at all, or whether
  polarity segmentation is Orbitrap-only in practice
  (`get_tic_per_scan`'s `tof_h5` branch ignores the polarity argument).
- Whether windowed items should eventually get their own calibration
  (per-window drift correction). Out of scope for now.

## Verification pointers

- Backend suite: `uv run pytest server/backend/tests/` (needs
  `mascope dev up`); existing sample-item creation tests live under
  `tests/unit/api/` and `tests/integration/api/`.
- Matching behaviour changes should get an integration test that creates two
  windowed items over one file and asserts their match intensities differ
  and correspond to the windows.
- The demo dataset (`docker-compose.demo.yaml`, see `docs/demo_dataset.md`)
  is the easiest stack for end-to-end shakedown.
