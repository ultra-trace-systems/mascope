`ULTRA TRACE MASCOPE - DESIGN DOC - VISUAL DIGESTS`

# Visual digests: charts, dashboards and reports over the SDK

## Purpose

Mascope's web app draws one batch at a time, and a batch is one instrument-day
of one chemistry. The people who run these instruments are atmospheric
scientists, and the questions they bring are longer than a day: how a species
behaved over a campaign, what its diurnal cycle looks like, which compounds
moved together in an episode, whether the instrument was healthy all month.
Today those questions are answered in a notebook, by hand, each time.

This document designs `mascope_viz`: a Python package beside the SDK that
turns a scope of Mascope data (a dataset, a set of batches, a time range) into
a **visual digest**, a set of charts compiled into an interactive dashboard
(HTML) and a report (PDF). It is a library a person can call, a CLI an agent
can drive, and the one place in which figure code over the SDK lives.

It is an engineering design and phased plan, not a user guide.

> **Status (2026-10-21).** Proposal, reviewed by the owner on 2026-10-21: it
> stands as written, with datashader added as an optional raster path on top
> of the two renderers (section 2.4, decision 2). No code written. Section 1
> was verified against `develop` at 5bd66b2d1 on 2026-10-20; file links are
> the evidence. The plan is a Linear project (appendix B); this note stays
> the record.

> **Scope decision (v1): read-only, over the public API.** The package reads
> through `mascope_sdk` with the user's own token and writes nothing to the
> server. It adds no computation Mascope does not already do, except the
> aggregation a chart needs (time binning, normalisation, period statistics),
> which it does in pandas on the client and names in every figure.

> **Division of labour.** The SDK loads; `mascope_viz` looks; the author
> (a person, or an agent through the skill) interprets. The package never
> writes a sentence of interpretation: every number in a digest traces to a
> view it computed, and the narrative is a labelled, authored section.

---

## 1. Current state (as-is)

### 1.1 The app: one batch, one day

Every chart in the web app is scoped to one batch. The six charts share one
Plotly wrapper
([BaseChartPlotly.vue](../../server/frontend/src/lib/charts/BaseChartPlotly.vue))
and are listed in
[charts/index.js](../../server/frontend/src/lib/charts/index.js). The two batch
charts draw one point per sample over the focused batch's `datetime`: the
Targets overview, one trace per target ion from `/match/records/ion/series`
([ChartBatchOverview/data.js:322-326](../../server/frontend/src/lib/charts/ChartBatchOverview/data.js)),
and the Assignments overview, at most 300 batch peaks from
`/batch-peaks/records/series`
([batchChart.js:11](../../server/frontend/src/lib/batchChart.js)). The other
four are one sample or one sample-and-ion: the spectrum, the within-file peak
time series (seconds since the file's start), and the two match panels.

The batch is the unit by construction. The sample store loads the focused
batch only
([stores/data/modules/sample.js:19-34](../../server/frontend/src/stores/data/modules/sample.js)),
and `GET /api/samples` refuses a call that names neither a batch nor a sample
([samples_routes.py:49-54](../../server/backend/src/mascope_backend/api/routes/samples/samples_routes.py)).
Automatic ingest files each acquisition into a daily batch named
`<date> <mode> acquisition`
([process/service.py:2969-2983](../../server/backend/src/mascope_backend/api/controllers/sample/files/process/service.py)),
under a dataset per (workspace, instrument, year). So the longest time axis
the app draws is one day, and the sample-finder design states the gap
plainly: "a trend over six months is seen only after a batch exists and has
been matched", and "the batch views draw one point per sample today; the
overview chart will need to bin by time. Not designed here"
([sample_filters_and_metadata.md, section 8](sample_filters_and_metadata.md)).
Building a month-long analysis batch today means copying thirty daily batches
per chemistry and per instrument (the same note, section 1).

The volumes are what make binning a necessity rather than a nicety: the batch
views were tuned for about 1,440 samples a day
([ingest_routing_and_splitting.md](ingest_routing_and_splitting.md), the
"scale" paragraph), one production Orbitrap cycles about 2,500 files a day
(the same note, section on multi-range files), and the fleet acquires roughly
80k samples a month
([peak_assignment_batch_primary.md](peak_assignment_batch_primary.md)). A
summer campaign at one site is 100k to 200k samples; a chart that draws a
point per sample is not the tool.

No report generation exists anywhere in the repository: no PDF, no HTML
templates, no notebook-to-report tooling in use (`voila` serves one
instrument-function notebook under `tooling/notebooks/`). The batch exports
are data, not figures: the batch spreadsheet
([batches/export/routes.py](../../server/backend/src/mascope_backend/api/routes/sample/batches/export/routes.py)),
the per-(sample, peak) CSV `export_peaks`, the per-scan CSV
`export_peak_data`, and the ledger CSV.

### 1.2 The SDK: frames with a time column, no across-sample series loader

[mascope_sdk](../../libraries/sdk/) returns pandas frames. What matters to a
chart:

| Loader | Rows | Time | Intensity |
|---|---|---|---|
| `load_peaks(dataset, batches, samples, matches=True)` ([client.py:564](../../libraries/sdk/src/mascope_sdk/client.py)) | one per (sample, peak, isotope match); unmatched peaks one row with NaN match columns | `datetime_utc`, the sample **file's** acquisition start ([_loaders.py:485-496](../../libraries/sdk/src/mascope_sdk/_loaders.py)) | `height`, `area`: per-scan mean over the file when `average=True` ([samples_peaks.py:95-136](../../server/backend/src/mascope_backend/api/controllers/samples/lib/samples_peaks.py)) |
| `load_peak_timeseries(..., compound, ion, isotope)` ([client.py:702](../../libraries/sdk/src/mascope_sdk/client.py)) | one per (peak, scan), matched peaks only | `time` (s within the file) and a per-point `datetime_utc` | raw per-scan `height` |
| `load_assignments(dataset, batches, run="latest")` ([client.py:988](../../libraries/sdk/src/mascope_sdk/client.py)) | one per observed peak per sample | `datetime_utc` | `sample_peak_intensity`: mean height on Orbitrap, mean area on TOF ([peak_assignments/service.py:1945-1975](../../server/backend/src/mascope_backend/api/new/peak_assignments/service.py)) |
| `load_batch_ledger(dataset, batches, members=True)` ([client.py:927](../../libraries/sdk/src/mascope_sdk/client.py)) | one per (batch peak, sample) | **none**: join `samples.list` on `sample_item_id` ([batch_peaks.py:111](../../libraries/sdk/src/mascope_sdk/resources/batch_peaks.py)) | `intensity` in the unit `intensity_variable` names (`sum_peak_areas` on TOF, else `sum_peak_heights`) |
| `samples.list(batch= / batches=)` ([samples.py:157](../../libraries/sdk/src/mascope_sdk/resources/samples.py)) | one per sample | `datetime` (instrument-local, naive), `datetime_utc` (tz-aware), `t0`, `t1`, `length` | `tic` |

Four facts shape the data layer:

- **There is no across-sample series loader.** The "one point per sample
  over a campaign" frame, which is what every long time series is, is built
  in the example notebooks by `groupby(datetime_utc, label)["height"].sum()`
  over `load_peaks`
  ([02_batch_timeseries.ipynb](../../libraries/sdk/src/mascope_sdk/examples/02_batch_timeseries.ipynb)).
  `load_peaks` fetches every peak of every sample to get there: for a month at
  one sample a minute that is 43k requests and tens of millions of rows to
  draw fifty series.
- **The server has the compact form, and a token cannot reach it.**
  `POST /api/match/records/ion/series` returns, per target ion, parallel
  arrays of sample ids, intensity sums and match categories
  ([match/records/routes.py:151-179](../../server/backend/src/mascope_backend/api/new/match/records/routes.py),
  [ion/service.py:68-168](../../server/backend/src/mascope_backend/api/new/match/records/ion/service.py));
  `POST /api/batch-peaks/records/series` returns, per batch peak, the
  consensus metadata once and `peak_series` arrays of sample ids, peak ids,
  intensities and tiers
  ([batch_peaks_records.py:88-224](../../server/backend/src/mascope_backend/api/new/peak_assignments/batch_peaks_records.py)).
  These are what the app's two batch charts draw from. Neither is decorated
  `token_access=True`, so the SDK cannot call them; the SDK rebuilds the same
  data from `/members` (paged at 5,000) and per-sample calls. Opening them,
  route by route as the AI layer's audit did
  ([ai_layer.md, appendix](ai_layer.md)), is the single backend change this
  design needs (phase 0). Both are guest reads that write nothing.
- **Time is the file's start.** `datetime_utc` is stamped from the sample
  file ([models.py:907-929](../../server/backend/src/mascope_backend/db/models.py));
  several samples cut from one file share it, and `t0`/`t1` give each one's
  window in seconds. A sample's timestamp for a chart is `datetime_utc +
  (t0 + t1) / 2`. `datetime` is instrument-local and naive; the file carries
  `acquisition_timezone` ([models.py:952-955](../../server/backend/src/mascope_backend/db/models.py)).
  The only cross-batch time listing is `GET /api/sample/files` with
  `datetime_min`/`max` and an instrument filter
  ([sample_files_routes.py:96-120](../../server/backend/src/mascope_backend/api/routes/sample/files/sample_files_routes.py)),
  which is what a coverage chart needs: every file acquired, whether or not
  it landed in a batch. The SDK has no wrapper for it: its files resource
  offers `get_scans` and `download_raw` only
  ([sample_files.py](../../libraries/sdk/src/mascope_sdk/resources/sample_files.py));
  phase 0 adds the listing.
- **Caps count samples, not rows, and prompt.** `confirm_above` (100 samples
  for `load_peaks`, 20 for `load_peak_timeseries`) prompts on stdin and raises
  at end-of-file ([_loaders.py:211-238](../../libraries/sdk/src/mascope_sdk/_loaders.py));
  `max_workers` is capped at 8 ([_concurrent.py:104-105](../../libraries/sdk/src/mascope_sdk/_concurrent.py));
  a failed per-sample task leaves `attrs["complete"] = False` with
  `attrs["failures"]` ([_concurrent.py:53-74](../../libraries/sdk/src/mascope_sdk/_concurrent.py)).
  The package always passes the confirm guard off and reads `complete`.

Nothing in the SDK normalises an intensity. The notebooks label heights
"[cps]"; TIC and reagent-ion normalisation are inline code in
[06_normalization.ipynb](../../libraries/sdk/src/mascope_sdk/examples/06_normalization.ipynb).

### 1.3 Figure code today: thirty-odd Plotly cells, no library

The SDK has no plotting code. Every figure lives in the ten example notebooks
(and the MS2 toolkit under `tooling/notebooks/ms2_analysis/`), almost all
Plotly Express on the dark template: per-ion and per-compound time series,
hourly and ten-minute resampling, mass-defect scatters (one animated by
sample), stage comparisons with shaded spans and fold-change heatmaps, TIC
and reagent-ion normalisation, blank subtraction, Pearson correlation with a
dendrogram and a cluster dropdown, and the Van Krevelen of an assignment run
([04_mass_defect_plot.ipynb](../../libraries/sdk/src/mascope_sdk/examples/04_mass_defect_plot.ipynb),
[08_correlation_analysis.ipynb](../../libraries/sdk/src/mascope_sdk/examples/08_correlation_analysis.ipynb),
[10_peak_assignment.ipynb](../../libraries/sdk/src/mascope_sdk/examples/10_peak_assignment.ipynb)).
These are the package's first catalogue: each notebook figure is a chart kind
below, and once the package exists the notebooks become its consumers.

Plotting libraries: `plotly` 7.1 and `matplotlib` 3.11 are in the lock, as
the root's `dev` group and the SDK's `examples` extra
([sdk/pyproject.toml](../../libraries/sdk/pyproject.toml)); `kaleido` (static
export from Plotly, which needs a browser engine) is not, and neither is any
PDF library. `mascope_chem` renders datashader heatmaps, spectrograms and
waterfalls server-side
([image.py](../../libraries/chem/src/mascope_chem/image.py)), and
`datashader` is in the lock through `mascope_chem`
([chem/pyproject.toml](../../libraries/chem/pyproject.toml)). That module's
rasters and Plotly trace templates are the precedent for the optional
big-data path of section 2.4; the dependency itself stays an extra, so the
core install remains light.

Aggregation helpers worth reusing rather than rewriting:
`mascope_tools.alignment.calibration.Spectra.get_timeseries` builds an m/z by
timestamp matrix from centroids, `average_sample_item_spectra` averages
spectra on a common grid
([alignment/utils.py](../../libraries/tools/src/mascope_tools/alignment/utils.py)),
and `mascope_tools.stats.peak.assign_intensity_change_score` scores intensity
change with changepoints. `mascope_tools` is public and on PyPI.

### 1.4 The peaky precedent: a deterministic PDF from sections

[peaky](https://github.com/ultra-trace-systems/peaky) ships the one working
report pipeline over Mascope data, and its shape is right for this package:

- `pdf_report.py` loads a run's artifacts **once** into a context dict (the
  only place numbers are derived), then draws an ordered list of sections,
  each a `section(ctx, pdf)` function into a matplotlib `PdfPages`; a
  section that throws becomes a one-page error note, so **one bad section
  never kills the report**; matplotlib stamps a fixed `SOURCE_DATE_EPOCH`, so
  the report is byte-deterministic; a compressed companion is produced
  outside `build` so the primary stays deterministic.
- Its figures are pure data functions plus lazy-matplotlib renderers: the
  Van Krevelen and per-ion time traces (`analyte_viz.py`), the mass-defect
  and mass-error QC figure (`qc_figure.py`), the Kendrick figure
  (`gka_figure.py`), and the co-varying-family cluster panels
  (`batch/clustering.py`).
- Its time series is a samples by m/z-bin matrix built by ppm gap-clustering,
  normalised to the reagent ion, with a cadence helper that bins at the
  ceiling of the native median inter-sample spacing so a bin never aliases
  into a comb of empty bins (`batch/timeseries.py`).

What it does not do: it is per batch (one day), its report is about an
assignment run, and its compressed companion uses PyMuPDF, which is AGPL and
would fail Mascope's licence gate
([check-licenses.py](../../tooling/check-licenses.py)). The AI layer note's
decision stands here too: adopt peaky's patterns, not its code (open
decision 4 of [ai_layer.md](ai_layer.md)).

### 1.5 The AI layer already reserves a chart tool

[ai_layer.md](ai_layer.md) section 3.4 specifies `chart(frame, kind, x, y)`
and `table(frame, max_rows)` returning artifacts "rendered the same way on
both transports", and MAS-129 briefs them. Its phase 2 lists Kendrick and
Van Krevelen among the skills. Without this package, MAS-129 would start a
second figure stack. With it, `mascope_ai.chart` is a thin front over
`mascope_viz` (section 2.5), and a `digest` tool joins it.

---

## 2. Architecture

### 2.1 Where the package sits

```
   mascope_sdk            loads: frames from the API, the user's token
        |
   mascope_viz            looks: scope -> store -> views -> charts -> digest
     data/                 Scope, the campaign store, views (pure pandas)
     charts/               chart kinds -> Figure specs (pure functions of views)
     render/               matplotlib (PNG, SVG, PDF) and plotly (HTML)
     digest/               recipes -> Digest -> dashboard.html + report.pdf + bundle/
     cli                   mascope-viz: scopes, store, list, describe, chart, digest
     skill/                SKILL.md: how an agent drives it, and what it must not do
        |                                  |
   notebooks (consumers)          mascope_ai: chart(), table(), digest() delegate here
                                           |
                                  Claude Code (now) / in-app panel (AI layer phase 4)
```

Dependency direction: `mascope_viz` depends on `mascope_sdk`, pandas, numpy,
pyarrow, matplotlib and plotly, with datashader behind a `[big]` extra;
nothing on the server depends on `mascope_viz`; `mascope_ai` depends on it;
peaky does not (section 2.5).

Location: `libraries/viz/` in this repository, package `mascope_viz`,
published to PyPI beside `mascope_sdk`, for the reasons MAS-212 gives for
`mascope_ai`: one PR can change the SDK and the package together (phase 0
adds SDK wrappers the package needs), the CI jobs, ruff, the licence gate and
the demo-stack contract tests cover it with no new plumbing, and it can move
out later without changing its import name. Decision 1.

### 2.2 Scope and the campaign store

A **Scope** names what a digest is about, in Mascope's own terms plus a time
range:

```python
Scope(dataset=None, batches=None, instrument=None,
      start=None, end=None, polarity=None, mode=None,
      collection=None, tz="instrument")
```

Resolution goes through the SDK listings (`find`-style literal matching, the
same contract the AI layer unified) and the files listing for the time
range. A scope resolves to a list of batches and, for coverage, the files
acquired in the range whether or not they are in one.

The **campaign store** is what makes a long time series cheap: a local
parquet cache, one directory per server and scope key, holding per-batch
tables that the compact routes fill in one call per batch:

| Table | One row per | Columns | Source |
|---|---|---|---|
| `samples` | sample | `sample_item_id`, `sample_batch_id`, `t_mid_utc`, `datetime_utc`, `t0`, `t1`, `tic`, `polarity`, `instrument`, `instrument_type`, `filename` | `samples.list(batches=)` |
| `files` | file in range | `sample_file_id`, `datetime_utc`, `length`, `instrument`, `acquisition_timezone`, in-a-batch flag | `GET /api/sample/files` |
| `ion_series` | (sample, target ion) | `series_key`, `intensity`, `match_category` | `/match/records/ion/series` per batch |
| `peak_series` | (sample, batch peak) | `series_key`, `intensity`, `tier`, `batch_peak_id`, `run_id` | `/batch-peaks/records/series` per batch |
| `species` | series key | name, formula, ion formula, mechanism, class, nC/nO/nH, O/C, H/C, OSc | the ion and batch-peak metadata |
| `rollups` | (bin, series key) | hourly and daily median, mean, p10, p90, n | computed on sync |

Sync is incremental: a batch whose modified stamp and sample count are
unchanged is not refetched; the current day's batch always is. A month at one
sample a minute with 300 series is about 13M rows in long format, tens of
megabytes as parquet; a year is served from the rollups unless a chart asks
for raw cadence inside a window. Every table records the provenance block the
SDK stamps on its frames (deployment id, `mascope_version`, engine versions)
and the batch's assignment run id, so a digest can say which engine produced
the formulas it draws and flag a scope that mixes runs.

The store is a cache, never a source of truth: deletable at any time,
rebuildable from its recorded scope. It is also what makes a scheduled digest
(the weekly report of a site) a cron line rather than a full reload.

### 2.3 Series identity across batches

A long time series needs an identity that survives the batch boundary. Three
exist, with different strengths:

| Series key | Identity | Stable across batches | Source | In |
|---|---|---|---|---|
| `ion:<target_ion_id>` | a compound's ion in a target collection | yes, by construction | `/match/records/ion/series` | v1 |
| `formula:<formula>|<adduct>` | an assigned formula and adduct from the batch ledger | yes while the engine agrees with itself; the store records the run per batch | `/batch-peaks/records/series` | v1 |
| `mz:<centre>` | an untargeted m/z bin aligned across batches | needs ppm gap-clustering across batches and tolerance for calibration drift | batch-peak consensus m/z, or peaks | v3 (section 3, `covariation`) |

v1 charts draw targeted ions and assigned formulas. The time by m/z heatmap
(`spectrogram`) needs no alignment: it bins intensity onto a fixed m/z grid.
Decision 3.

### 2.4 Views, charts, renderers

**Views** are pure pandas functions from store tables to tidy frames, and the
only place aggregation happens: `series(keys, cadence, normalise, stat)`,
`diurnal(keys, by=None)`, `periods(keys, period)`, `coverage()`,
`composition(period)`, `spectrum(period, other=None)`, `top(n, by)`. Cadence
defaults to peaky's rule (the ceiling of the native median spacing, so a bin
never aliases) and can be set (`10min`, `1h`, `1d`, `raw`). Normalisation is
`none` (the default), `tic`, or `ion:<key>` for a reagent ion the chemistry
names; the chosen one goes into the figure's y label and the digest's
summary, never silently (decision 7). Gaps stay gaps: a bin without samples
is NaN and a line breaks there; below-detection is NaN, not zero.

**Charts** are functions from views to a **Figure spec**, a small vocabulary
of eight primitives that both renderers implement:

| Primitive | Used by |
|---|---|
| `series` (time x, one or many y, optional band, log, event spans, gap-aware) | species, instrument signal, class series, comparisons |
| `strip` (categorical rows by time, presence shading) | coverage |
| `heatmap` (x by y grid, log colour) | spectrogram, weekly cycle, fold change |
| `scatter` (numeric x, y, size, colour, top-N labels) | Van Krevelen, mass defect, oxidation state, species scatter |
| `sticks` (m/z by intensity, labels) | period spectrum, difference spectrum |
| `bars` (categorical x, groups, error bars) | tier counts, stage means |
| `box` (distribution per category) | period distributions |
| `table` | scope summary, top species, episodes |

A chart kind is a function `(store, params) -> Figure`, with a title, axis
units, the normalisation and cadence in its subtitle, a machine-readable
summary (the numbers a caption may cite: the series' medians, maxima and their
times, the coverage fraction) and the data frame behind it. Tests assert on
specs and summaries, not pixels; one smoke render per backend.

**Renderers**: matplotlib for PNG, SVG and the PDF (via `PdfPages`,
section 2.6); plotly for the HTML dashboard. Two renderers over eight
primitives is the cost of having both a print-quality, deterministic report
and an interactive dashboard without kaleido or a browser engine on the
machine. Decision 2. One theme module holds the palette (categorical,
sequential and diverging ramps validated for contrast, light and dark), the
typography and the mark sizes, so a digest reads as one system across both
backends and matches the app's Plotly look where the app has one.

**Datashader, an optional third path for big data.** Some views are too
large to draw as marks: the time by m/z spectrogram of a campaign at raw
cadence, a year of samples for a dozen species without rolling up, the
untargeted m/z view of tier 3. Datashader rasterises such a view onto a
pixel grid, aggregating every point (count, sum, max, or a per-category
colour) in compiled code, and returns an image with known axes; both
renderers embed it as an image on real axes (`imshow` in matplotlib, a
layout image or `go.Image` in plotly), with the pan and zoom of the plotly
version re-rasterising on demand if a later phase wants it. The chart kinds
do not change: the `heatmap` and `series` primitives gain a `raster` mode
that a chart requests above a point count, and the spec says which mode
drew it. The dependency lives behind a `mascope_viz[big]` extra, since
datashader brings numba and dask; without it the same charts fall back to
pandas binning at a coarser cadence and say so in their subtitle. Phase 5
measures where it pays, on a year of a real acquisition dataset, before any
chart requires it. `mascope_chem.image` (section 1.3) is the precedent to
read first.

### 2.5 Relations: `mascope_ai`, peaky, the app

- **`mascope_ai`** (AI layer phase 1): `chart(frame, kind, x, y)` and
  `table(frame)` from MAS-129 become thin wrappers over `mascope_viz.charts`
  and `mascope_viz.render`, returning the AI layer's `Artifact`; a
  `digest(scope, recipe)` tool joins them. MAS-129's brief is amended to
  depend on this package's phase 2 rather than drawing on its own.
  Decision 5.
- **peaky** stays a standalone package and skill. It keeps its figures; it
  may adopt `mascope_viz` renderers when it next touches them, at its own
  pace. No dependency either way in v1.
- **The web app** gets nothing from this plan directly. Whether the app
  shows long time series natively, embeds a stored dashboard (the AI layer's
  artifact panel is the natural host), or leaves it to the package, is a
  product question this note records and does not decide (decision 8). The
  pilot (phase 3) is where the demand is measured.
- **The notebooks**: once phase 2 lands, notebooks 02, 03, 04 and 08 are
  rewritten on `mascope_viz` and shrink to the data story; the package's
  docs point at them.

### 2.6 Digests

A **Digest** is an ordered list of sections, each a chart or table with a
caption, under a cover that states the scope. A **recipe** is a named digest
template with parameters (the species to draw, the cadence, the
normalisation, the periods to compare). Four ship:

| Recipe | Sections | For |
|---|---|---|
| `campaign` | scope summary, coverage, instrument signal, species series (a named list or the top N), diurnal, period distributions, spectrogram, period spectrum, Van Krevelen, top species | a campaign, a season, a month |
| `weekly` | scope summary, coverage, instrument signal, species series (this week against last), changers | a site's standing weekly digest |
| `qa` | coverage, instrument signal, assignment health, mass accuracy | an instrument's health over a period |
| `composition` | period spectrum and difference, Van Krevelen, mass defect, oxidation state, class series | what was there, and how it changed |

Outputs land in one bundle directory:

```
<scope-slug>_<recipe>_<YYYY-MM-DDTHHMMSSZ>/
  dashboard.html        plotly, self-contained (plotly.js inlined; --plotlyjs cdn for a small file)
  report.pdf            matplotlib PdfPages, A4, byte-deterministic
  figures/<id>.png|svg  every chart, both forms
  data/<id>.csv         the frame behind every chart
  summary.json          every chart's machine-readable summary (what a caption may cite)
  manifest.json         scope, recipe, parameters, store provenance, versions, timings
  findings.md           the authored narrative, if any (section 2.7)
```

The PDF follows peaky's spine: one context load, ordered sections, a bad
section becomes an error page, `SOURCE_DATE_EPOCH` pinned. The dashboard is
one HTML file with a table of contents, each figure interactive (zoom, pan,
range slider on long series, hover values), captions under the figures and
the manifest as a footer. A `--static` dashboard embeds the SVGs instead,
for mail and for machines without a browser engine.

### 2.7 The agent surface, and what an agent may not do

The package is meant to be driven by prompt. The surface an agent sees, in
the order it becomes available:

1. **The CLI plus a skill, from Claude Code** (phase 4; needs nothing of the
   AI layer). `mascope-viz scopes` finds datasets and batches by name and
   time; `store sync` fills the cache; `list` and `describe <chart>` print
   the catalogue with parameters as JSON, the model's discovery surface;
   `chart <id> --scope ... --out` and `digest <recipe> --scope ... --out`
   produce bundles and print their paths and `summary.json`. A `SKILL.md`
   shipped in the package (`mascope-viz install-skill`, peaky's pattern)
   teaches the three-layer model (scope, store, views), the chart catalogue,
   the recipes, and the pitfalls (gaps, normalisation, time zones, mixed
   engine runs). Only paths and summaries reach the model; frames never do.
2. **`mascope_ai` tools** (AI layer phase 1): `chart`, `table`, `digest`
   return artifacts into whichever transport the AI layer runs on.
3. **The in-app panel** (AI layer phase 4) renders the same artifacts.

The rule that keeps a digest honest is the AI layer's, applied to pictures:
**the package computes, the author narrates, and the two are labelled.** A
digest's figures and tables carry only computed numbers. The narrative is an
authored section (`findings.md`, rendered as the report's "Findings" page and
the dashboard's lead), written by a person or by the agent, and marked as
authored. The skill tells the agent that every number it writes must appear
in `summary.json`, that it says "the series shows" rather than "the
atmosphere did", and that it must not invent a cause. An agent cannot
fabricate a chart, because it has no tool that draws one from values it
supplies: `chart` takes a scope and a kind, not data.

Prompt injection is live here as it is in the AI layer: sample, batch,
dataset and collection names flow into titles and captions. The package
renders them inertly and the skill says they are data.

---

## 3. The catalogue

Chart ids are the CLI's and the recipes' names. "Series" says which identity
(section 2.3) feeds the chart. Tiers: **1** ships in phase 2 and the
`campaign` recipe; **2** in phase 5; **3** needs method work or data the
server does not yet serve.

### Coverage and instrument health

| Id | Question | Drawn as | Series | Tier |
|---|---|---|---|---|
| `coverage` | When was the instrument acquiring, and is every file in a batch? | strip: one row per mode and polarity, files and samples per hour, gaps shaded, acquired-but-unfiled marked | files, samples | 1 |
| `instrument_signal` | Was the ion source stable? | series: TIC and the reagent ions, hourly median with a p10-p90 band, log y | `tic`, `ion:` (reagent) | 1 |
| `assignment_health` | Did the engine keep up? | series: peaks per sample, assigned fraction, tier counts over time; a marker where the run id changes | `peak_series` | 2 |
| `mass_accuracy` | Is the mass axis drifting? | series: ppm error of reference or assigned ions over time, by ion, with the calibration-change markers | ledger members' ppm | 2 (complements the per-instrument trend planned in [tof_calibration.md](tof_calibration.md), MAS-190) |

### Long time series

| Id | Question | Drawn as | Series | Tier |
|---|---|---|---|---|
| `species_series` | How did these species behave over the period? | series, overlaid or small multiples: raw or normalised, cadence auto/10min/1h/1d, median line with a percentile band at coarse cadence, log y, optional event spans | `ion:`, `formula:` | 1 |
| `diurnal` | What is the daily cycle? | series over hour-of-day: median with the interquartile band, one panel per species, optionally split by month, season or weekday/weekend | `ion:`, `formula:` | 1 |
| `period_boxes` | How did the distribution shift week to week, month to month? | box per calendar period per species | `ion:`, `formula:` | 1 |
| `weekly_cycle` | Is there a weekly pattern? | heatmap: day-of-week by hour-of-day, median | `ion:`, `formula:` | 2 |
| `class_series` | How did the composition budget evolve? | stacked series by compound class (CHO, CHON, CHOS, ...), by carbon number, or by O/C bin | `formula:` | 2 |
| `episodes` | When did a species exceed its baseline, and for how long? | table plus shading on `species_series`: baseline as a rolling percentile, exceedances above a factor, merged into episodes | `ion:`, `formula:` | 3 (method to choose and state) |
| `comparison` | Do two sites, instruments or chemistries agree? | series: the same species from two scopes, aligned in time, with a ratio panel | `ion:`, `formula:` | 3 |

### Composition snapshots

| Id | Question | Drawn as | Series | Tier |
|---|---|---|---|---|
| `spectrogram` | What does the whole campaign look like in one picture? | heatmap: time by m/z on a fixed grid, log colour; the first look at a campaign; rasterised by datashader at raw cadence with the `[big]` extra (phase 5), binned in pandas otherwise | `peak_series` on a grid | 1 |
| `period_spectrum` | What was there in this period, and what differs from that one? | sticks: the period-average spectrum, top-N labelled; a second period as a difference spectrum (day vs night, episode vs background) | `peak_series` | 1 |
| `van_krevelen` | What chemistry is this? | scatter: O/C by H/C of the assigned formulas, size by median intensity, colour by carbon number, class or amplitude (cv) | `formula:` | 1 |
| `mass_defect` | Which homologous series are present? | scatter: m/z by mass defect, or Kendrick mass by Kendrick mass defect (CH2, O), colour by class or tier | `peak_series`, `formula:` | 2 |
| `oxidation_state` | How oxidised is the organic mass? | scatter: carbon oxidation state (OSc = 2 O/C - H/C) by carbon number, size by intensity | `formula:` | 2 |

### Relationships and tables

| Id | Question | Drawn as | Series | Tier |
|---|---|---|---|---|
| `top_species` | What was brightest, what changed most, what is new? | table: by median intensity; by the ratio of the last period to the first; present in the last period and absent before | `ion:`, `formula:` | 1 |
| `scope_summary` | What is this digest about? | table on the cover: datasets, batches, samples, files, span, instrument, chemistry, collections, engine runs, normalisation, cadence, versions, generated at | store | 1 |
| `covariation` | Which species moved together? | the co-varying families: log-space correlation on a common cadence, hierarchical clustering, one panel per family with its traces; the flat remainder listed | `ion:`, `formula:`, later `mz:` | 3 (peaky's method as the reference; notebook 08 as the prototype) |
| `species_scatter` | How do two species relate? | scatter: x species by y species, colour by hour or by time | `ion:`, `formula:` | 3 |
| `aux_overlay` | What was the wind, the temperature? | series: an external time series (CSV aligned on the time axis) under the species panels | external | 3 (the shape of auxiliary data in [sample_filters_and_metadata.md](sample_filters_and_metadata.md) section 4.7) |

Twenty-one kinds; ten in tier 1. The science charts carry their references
(appendix C) in the user docs, as the how-it-works pages do.

---

## 4. Phased plan

Each phase lands independently and leaves the package shippable. Sizes are
rough: S a day or two, M a week, L two to three weeks for an implementing
agent with review.

### Phase 0 - Foundations and audits (no user-visible behaviour)

- **Decisions 1 to 7** confirmed by the owner (section 6).
- **Token access**: open `POST /api/match/records/ion/series` and
  `POST /api/batch-peaks/records/series` to token auth after reading each
  route's ACL and cost, and pin them in
  [test_token_access_coverage.py](../../server/backend/tests/unit/api/routes/test_token_access_coverage.py).
  Both are guest reads that compute and write nothing; the first has no size
  cap and the second's cap is `min_n_present`. Measure a full-batch call on a
  1,440-sample batch and record the number here. S.
- **SDK wrappers**: `batches.ion_series(batch, collection=None, ions=None)`
  and `batch_peaks.series(batch, tier=None, min_n_present=2)` returning long
  frames with `sample_item_id`, `series_key`, `intensity` and the category or
  tier; `sample_files.list(datetime_min, datetime_max, instrument)` over
  `GET /api/sample/files`, which has no SDK wrapper today. Each stamps
  provenance like the loaders. S.
- **The synthetic campaign fixture**: a generator for hermetic tests, a
  month at one sample a minute, fifty species with diurnal cycles, two
  episodes, a gap, a reagent-ion drift and a run-id change mid-month,
  emitted as the store's tables. The demo dataset is a handful of files and
  cannot stand in for a campaign; this fixture is the test backbone of every
  later phase. M.
- Verify: the two routes answer an SDK token and refuse a wrong service; the
  wrappers' contract tests pass against the demo stack; the fixture round
  trips through the store.

### Phase 1 - The data layer: scope, store, views

- `Scope` and its resolution over the SDK listings and the files listing;
  literal name matching, candidates on ambiguity.
- The campaign store: parquet per server and scope key, the six tables,
  incremental sync, the rollups, provenance and run ids recorded, a `status`
  that says what is cached and how stale.
- Views: `series`, `diurnal`, `periods`, `coverage`, `composition`,
  `spectrum`, `top`; the cadence rule; the three normalisations; sample
  timestamps at the window's middle; time zone handling (UTC in the store,
  the instrument's zone or a named one for display, decision 6).
- CLI: `scopes`, `store sync|status|rm`, `view <name>` printing a summary.
- Verify: unit tests on the fixture for every view (binning never aliases;
  gaps stay NaN; normalisation by a reagent ion reproduces notebook 06 on its
  data); a contract test syncs the demo dataset; a month of a real
  acquisition dataset syncs on the LAN host in under a few minutes and the
  store size is recorded here. L.

### Phase 2 - Charts: primitives, renderers, tier 1

- The Figure spec and the eight primitives; the matplotlib and plotly
  renderers; the theme.
- The ten tier-1 charts, each with its summary and its data frame.
- CLI `list`, `describe`, `chart`.
- Notebooks 02, 03, 04 and 08 rewritten on the package.
- Verify: spec and summary tests on the fixture for every chart; one render
  per backend per chart as a smoke test; the rewritten notebooks run by hand
  against the demo stack (CI runs no notebook today; a notebook check is its
  own small issue, not this phase's). L.

### Phase 3 - Digests, the report and the dashboard, the pilot

- `Digest`, the four recipes, the bundle layout, `summary.json` and
  `manifest.json`.
- The PDF (PdfPages, sections, the error page, determinism) and the
  dashboard (one HTML, inline or CDN plotly.js, `--static`).
- CLI `digest`.
- **The pilot**: a `campaign` digest over a real campaign (an anonymised
  scope on the LAN host) read by two atmospheric scientists at customer
  sites; their reading, and what they asked for next, is the acceptance test
  and feeds the tier-2 order. Their names and sites stay out of the
  repository.
- Verify: the same scope twice gives the same `report.pdf` bytes; a bad
  section renders as an error page and the rest of the report stands; the
  dashboard opens offline; the pilot's findings recorded in this note. M,
  plus the pilot's calendar time.

### Phase 4 - The agent surface

- `SKILL.md` and `install-skill`; `list` and `describe` as JSON; the
  narrative contract (`findings.md`, the authored label, the "numbers from
  `summary.json`" rule); the injection note.
- `mascope_ai`: `chart`, `table` and `digest` over the package; MAS-129
  amended.
- The evaluation: five prompts over the fixture ("build the campaign digest
  for June", "which species had the strongest diurnal cycle", ...) whose
  bundles are checked by pytest for the right sections, and whose
  `findings.md` is checked for numbers that exist in `summary.json`.
- User docs: `docs/user/sdk/digests.md` (what a digest is, the recipes, the
  CLI, the bundle), and how-it-works pages for the science charts with
  their references.
- Verify: the evaluation passes; an agent session from a clean checkout
  produces a campaign digest from one prompt. M.

### Phase 5 - Tier 2, and the questions the pilot raised

- The six tier-2 charts, in the order the pilot asked for them.
- Scheduled digests: `digest --since last` and a documented cron line for a
  site's weekly report.
- The datashader raster path behind the `[big]` extra (section 2.4): the
  `raster` mode of the `heatmap` and `series` primitives, the spectrogram
  and raw-cadence species series on it, measured against pandas binning on
  a year of a real acquisition dataset; the result recorded here decides
  whether any chart ever requires the extra.
- Tier 3 as its own decisions: `episodes` (which baseline method),
  `covariation` (port the method or call peaky), `comparison`,
  `aux_overlay` (with the auxiliary-data design of the sample-finder note),
  untargeted `mz:` alignment across batches.
- Verify: per chart as in phase 2; the scheduled digest runs a week on the
  internal server; the raster measurement is in this note. L.

Phases 0 to 3 are worth having if the agent surface never ships; phases 0 to
2 are worth having if no one ever builds a digest, because they give the
notebooks and `mascope_ai` their figure library.

---

## 5. Costs and risks

- **Volume.** A high-rate site is 2,500 files a day. The store holds raw
  per-sample rows per batch and serves long ranges from rollups; a year of
  raw rows for 300 series is about 150M rows and is never loaded whole. The
  first sync of a year is one call per batch per series kind, 730 calls,
  minutes not hours; after that it is incremental.
- **Route cost.** The compact routes have no size cap; a 1,440-sample batch
  with thousands of batch peaks is what the app already draws, but 365 of
  them in a row is a load the server has not seen from one client. Phase 0
  measures; the store's sync is sequential with a pause between batches by
  default.
- **Identity drift.** An assigned formula can change between engine runs.
  The store records the run per batch, the scope summary lists the runs, and
  `assignment_health` marks the changes; the package never silently joins
  two runs' readings as one series.
- **Normalisation is a scientific choice.** Reagent-ion normalisation is the
  physical quantity in a halide CIMS and wrong for a chemistry without a
  reagent series. The default is raw with the choice named in every title;
  recipes per chemistry may set it (decision 7). The reagent ions a chemistry
  uses have to come from somewhere (open question 1).
- **A digest invites over-reading.** The authored-narrative rule
  (section 2.7) and the summary contract bound what an agent can claim; a
  person can still over-read a chart, which is why every chart states its
  cadence, normalisation and gap handling on its face.
- **Two renderers.** Eight primitives twice is the price of a deterministic
  PDF and an interactive dashboard without a browser engine. The spec tests
  keep them honest; a primitive that drifts between backends is a bug.
- **Licences.** matplotlib (PSF-style), plotly (MIT) and datashader (BSD,
  already in the lock through `mascope_chem`) pass the gate; nothing AGPL
  (PyMuPDF) and nothing with system libraries (WeasyPrint) enters.
- **Time zones.** A diurnal cycle in the wrong zone is a wrong result. The
  store is UTC, display defaults to the file's `acquisition_timezone`, and
  every time axis labels its zone.

---

## 6. Key design decisions

Recorded with a recommendation so the plan is actionable; each is a Decision
issue for the owner in the Linear project.

1. **Name and location.** *Recommend `mascope_viz` in `libraries/viz/`,
   published to PyPI.* Alternatives: `mascope_digest` (names the product
   but not the library); an extra of the SDK (`mascope_sdk[viz]`), rejected
   because the SDK's version is the API contract's version and a figure
   library moves at another pace.
2. **Render stack.** *Recommend matplotlib for PDF, PNG and SVG and plotly
   for HTML, over one Figure spec of eight primitives; no kaleido, no
   browser engine, no AGPL; datashader as an optional `[big]` extra that
   rasterises the big-data views for both renderers, never required by the
   core (the owner's addition at review, 2026-10-21).* Alternative: plotly
   only, with the PDF assembled from kaleido PNGs, rejected on the install
   burden and determinism.
3. **Series identity in v1.** *Recommend target ions and assigned
   formula-plus-adduct; the spectrogram on a fixed grid; untargeted m/z
   alignment across batches in tier 3.*
4. **The compact routes.** *Recommend opening both series routes to token
   auth in phase 0, route by route, as the AI layer did.* Alternative: build
   series from `/members` and per-sample peaks through the SDK, rejected on
   cost (one call per sample against one per batch).
5. **`mascope_ai` and peaky.** *Recommend `mascope_ai.chart`, `table` and
   `digest` delegate to this package (MAS-129 amended), and peaky stays
   independent.*
6. **Time zone.** *Recommend UTC in the store, the file's acquisition zone
   for display by default, a named zone on request, the zone on every
   axis.*
7. **Normalisation default.** *Recommend raw, named in every title; recipes
   per chemistry may set TIC or a reagent ion; never silent.*
8. **The app.** *Not this plan's to decide.* Native long time series in the
   web app, a stored dashboard embedded in it, or neither: a product call,
   informed by the pilot. Recorded so it is not decided by default.

---

## 7. Open questions

1. **Where do a chemistry's reagent ions come from?** The ionization mode
   names mechanisms, not the reagent ions to normalise by; peaky keeps them
   in reagent profiles. A recipe parameter in v1; a field on the mode or a
   per-site profile later.
2. **Is `tic` on the sample view populated for every instrument type?** The
   coverage and instrument-signal charts read it; if not, the TIC comes from
   the scans table or the peak sum, which the fixture should cover both
   ways.
3. **The `datetime_min`/`max` filter of `GET /api/samples`** uses an
   SQLite function and the sample-finder note says it was never exercised on
   Postgres. The package filters client-side on the listing; a server fix is
   a separate small issue.
4. **Store location and multi-server.** A per-user cache directory keyed by
   server is the leaning; a shared store on a server for scheduled digests
   is phase 5's question.
5. **How much of peaky's clustering becomes a view here** (tier 3
   `covariation`): port the method, or shell out. The AI layer's leaning,
   patterns not code, applies until the pilot asks for it.

---

## Appendix A: data sources per chart

| Chart | Route | SDK method | Token today | Cost unit |
|---|---|---|---|---|
| `coverage` | `GET /api/sample/files` (range, instrument), `GET /api/samples` (per batch) | `sample_files.list`, `samples.list(batches=)` | yes, yes | one call per page, one per batch |
| `instrument_signal` | `GET /api/samples` (`tic`), `POST /api/match/records/ion/series` (reagent ions) | `samples.list`, `batches.ion_series` (phase 0) | yes, **no** (phase 0 opens it) | one per batch |
| `species_series`, `diurnal`, `period_boxes`, `weekly_cycle`, `top_species`, `episodes`, `comparison`, `species_scatter` | the two series routes | `batches.ion_series`, `batch_peaks.series` (phase 0) | **no** (phase 0) | one per batch |
| `spectrogram`, `period_spectrum`, `mass_defect` | `POST /api/batch-peaks/records/series` with `min_n_present=1` | `batch_peaks.series` | **no** (phase 0) | one per batch |
| `van_krevelen`, `oxidation_state`, `class_series`, `assignment_health` | `GET /api/batch-peaks/batch/{id}` (species) and the series route | `batch_peaks.list`, `batch_peaks.series` | yes, **no** | one per batch |
| `mass_accuracy` | `GET /api/batch-peaks/batch/{id}/members` (ppm per member) | `batch_peaks.members` | yes | pages of 5,000 per batch |
| `scope_summary` | the listings and `GET /api/provenance` | `datasets.list`, `batches.list`, `samples.list` | yes | cached |
| `aux_overlay` | none (a local CSV) | - | - | - |

## Appendix B: the Linear project, as proposed

Project **Visual digests** under initiative *E - Peaky, the SDK and the AI
layer*; this note linked as the design note; related to *AI layer*
(MAS-129), *Sample finder and acquisition metadata* (the six-month-trend gap,
auxiliary data), *TOF mass calibration* (MAS-190) and *FAIR data roadmap*
(provenance in the manifest). Milestones follow section 4, one issue per
bullet, briefs in the team's shape (Context, Task with paths, Where, Verify,
Size), Decision issues for section 6:

| Milestone | Issues |
|---|---|
| 0 - Foundations and audits | Decisions 1-7 (one issue each; 8 recorded as a Decision with no milestone); open the two series routes to token auth; the SDK series wrappers; the synthetic campaign fixture |
| 1 - The data layer | Scope and resolution; the campaign store and sync; the views and the cadence rule; the store CLI |
| 2 - Charts | The Figure spec, primitives and both renderers; the theme; tier-1 charts (one issue per two or three charts); `list`/`describe`/`chart`; the notebooks rewritten |
| 3 - Digests and the pilot | Digest, recipes and the bundle; the PDF; the dashboard; `digest`; the pilot (a task with the owner, sites unnamed) |
| 4 - The agent surface | The skill and the narrative contract; `mascope_ai` delegation (MAS-129 amended); the evaluation; user docs |
| 5 - Tier 2 and scheduling | The tier-2 charts; scheduled digests; the datashader raster path; the tier-3 decisions |

## Appendix C: references the science charts rest on

- van Krevelen, D. W. (1950). Graphical-statistical method for the study of
  structure and reaction processes of coal. *Fuel* 29, 269-284.
- Kendrick, E. (1963). A mass scale based on CH2 = 14.0000 for high
  resolution mass spectrometry of organic compounds. *Analytical Chemistry*
  35, 2146-2154.
- Hughey, C. A., Hendrickson, C. L., Rodgers, R. P., Marshall, A. G. and
  Qian, K. (2001). Kendrick mass defect spectrum: a compact visual analysis
  for ultrahigh-resolution broadband mass spectra. *Analytical Chemistry* 73,
  4676-4681.
- Kroll, J. H. et al. (2011). Carbon oxidation state as a metric for
  describing the chemistry of atmospheric organic aerosol. *Nature Chemistry*
  3, 133-139.
- Carslaw, D. C. and Ropkins, K. (2012). openair: an R package for air
  quality data analysis. *Environmental Modelling & Software* 27-28, 52-61.
  The diurnal, weekly and period views follow the conventions its
  `timeVariation` and `timePlot` functions made standard among atmospheric
  scientists.
