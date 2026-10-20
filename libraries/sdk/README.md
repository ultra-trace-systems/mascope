# Mascope SDK

Python SDK for the Mascope mass spectrometry data analysis platform. Designed for researchers who want to load and analyze data from a Mascope server in Jupyter notebooks or Python scripts, or export it to use in other environments.

> **New to Python?** The user docs include a step-by-step
> [getting started guide](../../docs/user/sdk/getting-started.md) that walks
> you from installing an editor to running your first tutorial notebook. It is
> also available on any Mascope instance under `/docs/sdk/getting-started/`.

## Contents

- [Installation](#installation)
- [Tutorial Notebooks](#tutorial-notebooks)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [High-Level Loaders](#high-level-loaders)
- [Peak Assignments](#peak-assignments)
- [Changing Data](#changing-data)
- [Caching](#caching)
- [API Reference](#api-reference)
- [Examples](#examples)
- [For Developers](#for-developers)

## Installation

### Prerequisites

- **Python 3.10+**: [python.org/downloads](https://www.python.org/downloads/)
- **An IDE that supports Jupyter notebooks**: [VS Code](https://code.visualstudio.com/) with [Jupyter](https://marketplace.visualstudio.com/items?itemName=ms-toolsai.jupyter) and [Data Wrangler](https://marketplace.visualstudio.com/items?itemName=ms-toolsai.datawrangler) extensions is recommended

### Set up a virtual environment

```bash
python -m venv .venv
```

Activate it (Windows):

```bash
.venv\Scripts\activate
```

Or on macOS/Linux:

```bash
source .venv/bin/activate
```

### Install the SDK

```bash
pip install mascope_sdk
```

Or with [uv](https://github.com/astral-sh/uv):

```bash
uv add mascope_sdk
```

To run the bundled [tutorial notebooks](#tutorial-notebooks), install with the
`examples` extra instead - it adds the plotting and analysis libraries the
notebooks use (plotly + nbformat, matplotlib, numpy, scipy, ipykernel):

```bash
pip install "mascope_sdk[examples]"   # or: uv add "mascope_sdk[examples]"
```

## Tutorial Notebooks

The best way to learn the SDK is to walk through the bundled example notebooks. They cover everything from basic setup to advanced analysis workflows.

The notebooks need the `examples` extra (see [Installation](#installation)): `pip install "mascope_sdk[examples]"`.

Copy them to your project directory:

```python
import mascope_sdk

mascope_sdk.copy_examples("./tutorials")
```

This creates a `tutorials/` folder with the following notebooks:

| #   | Notebook                           | Topic                                                   |
| --- | ---------------------------------- | ------------------------------------------------------- |
| 1   | `01_getting_started.ipynb`         | Connect, list datasets/batches/samples, view a spectrum |
| 2   | `02_batch_timeseries.ipynb`        | Load peaks across batches, filter, and plot             |
| 3   | `03_intra_sample_timeseries.ipynb` | Per-scan intensity timeseries for specific compounds    |
| 4   | `04_mass_defect_plot.ipynb`        | Mass defect visualization                               |
| 5   | `05_peaks_by_stage.ipynb`          | Compare measurement stages within a single sample       |
| 6   | `06_normalization.ipynb`           | Normalize intensities by TIC or reagent-ion signal      |
| 7   | `07_background_subtraction.ipynb`  | Subtract a background sample (matched ions or m/z bins) |
| 8   | `08_correlation_analysis.ipynb`    | Find co-varying peaks via correlation and clustering    |
| 9   | `09_batch_stages.ipynb`            | Split a batch into stages and compare per-stage averages |
| 10  | `10_peak_assignment.ipynb`         | Read a server-side peak-assignment run: tiers, sources, Van Krevelen |

Open them in VS Code (or any Jupyter-compatible IDE) and run the cells. Each notebook is self-contained, just make sure your `.env` credentials are set up first (see [Configuration](#configuration)).

> Existing files are never overwritten, so you can safely re-run `copy_examples` after an SDK update to get new notebooks.

## Quick Start

### 1. Configure credentials

Create a `.env` file in your working directory (or any parent directory):

```env
MASCOPE_URL=https://example.mascope.app
MASCOPE_ACCESS_TOKEN=your-api-token
```

> **Tip:** Generate the API token in your Mascope instance's user settings.

### 2. Use the SDK

```python
from mascope_sdk import MascopeClient

# Auto-loads credentials from .env
mascope = MascopeClient(workspace="My Workspace")

# List datasets (returns a DataFrame). Besides the name, type and instrument,
# each row carries the dataset's descriptive metadata: license, authors
# (name, orcid, affiliation), keywords, related_identifiers and funding,
# empty where the dataset has none
datasets = mascope.datasets.list()

# List batches by dataset name
batches = mascope.batches.list("My Dataset")

# List samples from a single batch (raises if ambiguous)
samples = mascope.samples.list(batch="My Batch")

# List samples from all matching batches (containing the given keyword)
samples = mascope.samples.list(batches="Uronium")

# Load peaks across all samples in matching batches
peaks = mascope.load_peaks(dataset="My Dataset", batches="Uronium")

# Load peaks across a subset of samples, within matching batches
peaks = mascope.load_peaks(dataset="My Dataset", batches="Uronium", samples="12:")

# Plot a sample spectrum (based on sample id)
spectrum = mascope.samples.get_spectrum(sample_id=samples.iloc[0]["sample_item_id"])

import matplotlib.pyplot as plt

plt.scatter(spectrum["mz"], spectrum["intensity"])
plt.xlabel("m/z")
plt.ylabel("Intensity")
plt.show()
```

See more examples [below](#examples).

## Configuration

The `MascopeClient` can be configured in three ways (in override priority order):

1. **Constructor parameters** (highest priority):

   Initialize client with parameters:

   ```python
   mascope = MascopeClient(
       url="https://example.mascope.app",
       access_token="your-token",
       workspace="My Workspace",
   )
   ```

2. **Environment variables**:

   Set environment variables:

   ```bash
   export MASCOPE_URL=https://example.mascope.app
   export MASCOPE_ACCESS_TOKEN=your-token
   ```

   Initialize client (parameters are read from the environment unless overridden):

   ```python
   mascope = MascopeClient()
   ```

3. **`.env` file** (**_recommended for notebooks_**):

   Create `.env` file in the project directory:

   ```env
   MASCOPE_URL=https://example.mascope.app
   MASCOPE_ACCESS_TOKEN=your-token
   ```

   Initialize client (parameters are read from the `.env` file unless overridden):

   ```python
   mascope = MascopeClient()
   ```

### Workspace Selection

The `workspace` parameter selects which workspace to operate on. It accepts a name, substring, or ID:

```python
# Explicit workspace selection
mascope = MascopeClient(workspace="My Workspace")
```

If omitted and your account belongs to exactly one workspace, it is auto-selected. If you belong to multiple workspaces, a `ConfigurationError` is raised listing the available options.

## High-Level Loaders

The SDK provides four convenience loaders that handle dataset/batch/sample resolution, concurrent requests, and progress bars automatically. These are the recommended way to load data for analysis.

---

### `load_peaks`: Peaks across batches

Load averaged peaks ("sum spectrum") for all samples across one or more batches, returned as a single DataFrame enriched with batch and sample metadata.

The `batches` / `samples` filters are case-insensitive literal substrings, so
one filter can select several batches at once. Pass `exact=True` to match a
single batch by its full name, or a compiled `re.Pattern` for a regex.

```python
import re

# All peaks from every batch whose name contains "Uronium"
peaks = mascope.load_peaks(dataset="My Dataset", batches="Uronium")

# Exactly one batch, by full name
peaks = mascope.load_peaks(dataset="My Dataset", batches="Uronium 2026-01", exact=True)

# Regex: batches from 2025 or 2026
peaks = mascope.load_peaks(
    dataset="My Dataset", batches=re.compile("2025|2026", re.IGNORECASE)
)

# Filter by sample name
peaks = mascope.load_peaks(dataset="My Dataset", samples="blank")

# All peaks from every batch (skip confirmation prompt)
peaks = mascope.load_peaks(dataset="My Dataset", confirm_above=None)

# Without match data, areas only
peaks = mascope.load_peaks(dataset="My Dataset", matches=False, heights=False)
```

---

### `load_peak_timeseries`: Intra-sample timeseries

Load per-scan intensity timeseries for peaks matching a compound, ion, or isotope across batches. Provide exactly one of `compound`, `ion`, or `isotope` — the value can be a formula or compound name. Pass a list to load multiple targets in a single call (peaks are discovered once per sample).

```python
# Timeseries for all peaks matched to Urea (by name or formula)
ts = mascope.load_peak_timeseries(
    dataset="My Dataset",
    batches="Uronium",
    compound="Urea",  # or compound="CH4N2O"
)

# Multiple compounds in one call
ts = mascope.load_peak_timeseries(
    dataset="My Dataset",
    compound=["Urea", "Lactic acid"],
)

# Plot per-sample timeseries
import matplotlib.pyplot as plt

for name, group in ts.groupby("sample_item_name"):
    plt.plot(group["time"], group["height"], label=name)
plt.legend()
plt.show()
```

---

### `load_peaks_by_stage`: Stage-based peak loading

Load averaged peaks for distinct time-range stages of a single sample. Useful when a measurement has phases (e.g. blank, sample introduction, wash).

```python
stages = [
    (0, 30, "blank"),
    (30, 120, "sample"),
    (120, 180, "wash"),
]

peaks = mascope.load_peaks_by_stage(sample="My Sample", stages=stages)

# Compare areas between stages
peaks.groupby("stage_name")["area"].sum()
```

The `sample` parameter accepts a sample name or ID. Stage tuples can be `(t_min, t_max)` or `(t_min, t_max, name)`.

Key columns: `stage`, `stage_name`, `t_min`, `t_max`, plus all columns from `get_peaks`.

#### Peaks left out of a time range

A stage, like any `get_peaks(..., t_min=, t_max=)`, is averaged from each peak's per-scan timeseries, and the server computes a timeseries only when it is first requested. Matching requests the peaks it matches, so the ones still without are mostly the unmatched peaks - and a time-ranged read **leaves those out**. The frame is then shorter than the same read without a time range, and nothing in its rows shows it.

The server says so in a warning, which the SDK logs and attaches to the frame:

```python
peaks = mascope.load_peaks_by_stage(sample=sample_id, stages=stages)
# WARNING | API warning: 212 peak(s) were excluded because their timeseries
#           have not been computed yet. ... SDK:
#           samples.compute_peak_timeseries('...') computes them.

peaks.attrs["warnings"]  # [] when nothing was left out
```

When *every* peak is left out - a sample that was detected and never matched has no timeseries at all - the result is an empty frame that carries the warning, not `None`. `None` still means that nothing was found and nothing was warned about.

`compute_peak_timeseries` requests the timeseries of every peak that has none, which computes and stores them; a time-ranged read made afterwards includes all peaks:

```python
if peaks is not None and peaks.attrs["warnings"]:
    mascope.samples.compute_peak_timeseries(sample_id)
    peaks = mascope.load_peaks_by_stage(sample=sample_id, stages=stages)
```

It is slow where many peaks are missing: the API computes one peak per request, and the server reads the sample's data file back for each, so a sample that was never matched can take thousands of requests. Give it `mz_min` / `mz_max`, or `peak_ids=[...]`, to compute only what the analysis needs. Peaks that already have a timeseries are skipped, so it can be interrupted and run again, and it goes on where it stopped.

**Older servers.** An empty `attrs["warnings"]` means "nothing was left out" from a server newer than 1.10.1. Servers up to 1.10.1 warn only while the read still lists some peaks: one that left out *every* peak is answered exactly as a range that holds none. The SDK cannot tell the two apart there, so an empty time-ranged read from such a server carries a warning of the SDK's own that says it may be either. Their warning also still ends "Re-run peak detection", which does not bring the peaks back; the SDK's hint that follows it in the log is the one to act on.

**Combining loads.** `pd.concat` keeps `attrs` only when every frame's are equal, and the warning carries a count - so two samples that are both short differ, and `pd.concat` drops their warnings *and* their [provenance](#provenance). Combine loads with `mascope.concat`, which keeps both:

```python
peaks = mascope.concat(
    mascope.load_peaks_by_stage(sample=sample_id, stages=stages)
    for sample_id in sample_ids
)

peaks.attrs["warnings"]  # what any of the loads warned, each once
peaks.attrs["provenance"]  # kept when every load came from the same build
```

`attrs["warnings"]` is on the frames `get_peaks`, `load_peaks` and `load_peaks_by_stage` return, with the caveats every `attrs` entry has (see [Provenance](#provenance)): read it from the frame the loader returned, or from `mascope.concat` of several.

---

### `load_batch_ledger`: The batch ledger

Load a batch's **batch ledger** — the batch-primary record of peak assignment: one
**batch peak** per species across the batch's samples, with the consensus formula and
tier the samples' assignments vote for, and one **member** per sample the species was
seen in, carrying that sample's own reading of the peak. Every processed sample folds
in as it arrives, so the ledger is complete without a per-sample run in sight.

```python
# One row per member: the whole ledger of every matching batch, flat
ledger = mascope.load_batch_ledger(dataset="My Dataset", batches="Uronium")
ledger.to_csv("ledger.csv", index=False)  # a short way to any format

# The species table rides along: one row per batch peak
species = ledger.attrs["batch_peaks"]
species.groupby("sample_batch_name")["consensus_tier"].value_counts()

# Or the species table alone
species = mascope.load_batch_ledger(dataset="My Dataset", members=False)
```

Key columns of a member row: the anchor's `batch_peak_id`, `batch_mz`,
`consensus_formula`, `consensus_tier`, `support_fraction`, `n_present`, `curated`
(pinned by hand for the whole batch), beside the member's `sample_item_name`,
`sample_peak_id`, `mz`, `intensity`, `assigned_formula`, `source`, `tier`, `role`,
`fit_score` — so a sample that dissents from the batch reads as one row saying both.
Per batch, the same reads are `mascope.batch_peaks.list(batch_id)`,
`.members(batch_id, sample_id=...)` and `.verdicts(batch_id)` (the batch-level verdicts
recorded on its species). The app's *Batch peaks* pane exports the same rows as a CSV
from its view menu.

### `load_assignments`: Peak assignments across batches

Load the persisted **peak-assignment** results (see [Peak assignments](#peak-assignments)) of every sample across one or more batches, concatenated into a single DataFrame enriched with batch and sample metadata — the peak-assignment counterpart of `load_peaks`.

Read-only: each sample contributes its **latest completed** assignment run; samples without one are skipped (and logged), not assigned on the fly.

```python
# Assignments of every sample in matching batches
assignments = mascope.load_assignments(dataset="My Dataset", batches="Uronium")

# Confidently assigned peaks that came from the untargeted stage
assignments = mascope.load_assignments(
    dataset="My Dataset", tier="assigned", source="untargeted"
)

# Tier breakdown per sample
assignments.groupby(["sample_item_name", "tier"]).size()
```

Key columns: `sample_batch_name`, `sample_item_name`, `datetime_utc`, plus all columns from `peak_assignments.get` (one row per observed peak; core rows only — fetch `alternatives`/`provenance` per assignment with `peak_assignments.detail`).

---

### Confirmation prompt

`load_peaks`, `load_peak_timeseries`, and `load_assignments` show an interactive confirmation prompt when the number of samples exceeds `confirm_above`. Defaults are 100 for `load_peaks` and `load_assignments`, and 20 for `load_peak_timeseries`. This prevents accidentally launching hundreds of concurrent requests from a notebook cell. Set `confirm_above=None` to disable.

A caller with no interactive input - a script run with stdin closed or redirected from `/dev/null`, a pipeline, an agent's shell - cannot answer the prompt, and is stopped rather than waved through: the loader raises `ConfirmationRequiredError` before loading anything, with the sample count and the threshold on `count` and `threshold`. A script that means to load that many passes `confirm_above=None` (or a higher threshold).

```python
from mascope_sdk import ConfirmationRequiredError

try:
    peaks = mascope.load_peaks(dataset="My Dataset")
except ConfirmationRequiredError as e:
    print(f"{e.count} samples - narrow the selection, or pass confirm_above=None")
```

### Partial loads

The loaders read sample by sample, and a sample whose read fails does not stop the others: the load returns what it could read, and logs a warning. So that a script can tell "these samples have little in them" from "some samples failed to load", the frame says which it is:

```python
peaks = mascope.load_peaks(dataset="My Dataset", batches="Uronium")
peaks.attrs["complete"]  # True when every read succeeded
peaks.attrs["failures"]  # one dict per failed read, [] when none failed
```

Each failure names the sample (`sample_batch_name`, `sample_item_id`, `sample_item_name`; `load_peaks_by_stage` the stage, `load_peak_timeseries` also the `peak_id` where a peak's series failed) and the error (`error_type`, `error`). When every read failed the loader returns an empty frame carrying the failures, not None, which would say there was nothing to load. `load_peaks`, `load_assignments`, `load_peaks_by_stage`, `load_peak_timeseries` and `samples.list(batches=...)` (per batch) report this; `mascope.concat` of several loads keeps every load's failures, and is complete only when every load was.

### Provenance

Every frame a high-level loader returns carries the provenance of the server that served it, on `df.attrs["provenance"]`: the deployment's id, the Mascope version it runs, the match-score and peak-assignment engine versions in force, and when this client first heard from that build. Keep it with what you derive from the frame, and a published figure can name the build its numbers came from.

```python
peaks = mascope.load_peaks(dataset="My Dataset", batches="Uronium")

peaks.attrs["provenance"]
# {'provenance_version': 1,
#  'generated_utc': '2026-10-01T12:00:00Z',
#  'deployment_id': 'k3J9xQ2mP0aB7cD1',
#  'produced_with': {'mascope_version': 'vX.Y.Z',
#                    'match_score_version': 1,
#                    'peak_assignment_engine_version': '0.5.0'}}

# The same block, asked directly
mascope.provenance()
```

The versions describe the server as it runs when asked. A result it stored earlier was computed by whichever build ran then; where a record carries provenance of its own, as a peak-assignment run does on `df.attrs["run"]`, that record is the authority for it.

Frames loaded from one deployment and build carry the same block, so `pd.concat` of several loads keeps it. Frames from different builds - the server was updated between two loads - carry different blocks, and `pd.concat` drops the attribute: no single build produced the result. `pd.concat` compares the whole of `attrs`, so the same happens when two peak loads differ in `attrs["warnings"]`. That is not only a load [short of peaks](#peaks-left-out-of-a-time-range) beside a whole one: the warning carries a count, so any two loads that are both short differ, which is the usual case for a loop over samples. `mascope.concat` combines loads without that: it keeps every load's warnings, and the provenance when every load carries the same block.

The attribute is best effort, in two ways. A server that predates it (`GET /api/provenance` answers 404) returns its frames without the attribute, and the load succeeds as before; so does any other failure to ask, which holds the frame back for about 15 seconds at most. And pandas does not reliably carry `attrs` through other operations - `merge`, `copy` and the like - so read it from the frames the loaders returned, or from their concatenation: the same caveat `attrs["run"]` and `attrs["batch_peaks"]` live with.

#### Keeping it when you save a frame

`attrs` live only on the DataFrame: saving it as CSV or Excel drops them without a word. Write the block beside the file instead:

```python
import json

peaks.to_csv("peaks.csv", index=False)
with open("peaks.provenance.json", "w", encoding="utf-8") as f:
    json.dump(peaks.attrs["provenance"], f, indent=2)
```

Parquet keeps `attrs` inside the file - `peaks.to_parquet("peaks.parquet")`, and `pd.read_parquet` brings them back - as long as pandas is 2.1 or newer and `pyarrow` is installed, neither of which the SDK itself requires. A `load_batch_ledger` frame is the exception: its `attrs` also hold the species table, a DataFrame that Parquet cannot keep there, so `to_parquet` raises. Save the species table as a file of its own first:

```python
ledger = mascope.load_batch_ledger(dataset="My Dataset")

species = ledger.attrs.pop("batch_peaks")
species.to_parquet("species.parquet")
ledger.to_parquet("ledger.parquet")  # keeps attrs["provenance"]
```

## Peak Assignments

Mascope's **peak-centric assignment** engine assigns a composition to *every*
observed peak of a sample — database-known targets first (Stage A), then
untargeted composition search (Stage B) — arbitrates a single owner per peak,
and files each assignment into a confidence tier (`assigned` | `candidate` |
`below_assignability` | `unassigned`). The tier is read off the row's
**evidence** — its `fit_score` weighted by the chemical plausibility of the
assigned formula — under the run's `tier_bands`. `fit_score` is served
alongside it, unchanged, as the pure fit measurement. Runs are launched from
the Mascope app and persisted; the SDK reads the results (it does not trigger
runs).

The top tier used to be called `identified`. The API still accepts that
spelling wherever a tier is sent and normalises it to `assigned`, so scripts
written against the old vocabulary keep working.

This coexists with targeted matching (`mascope.matching`, `get_peaks` match
columns): a `database`-sourced assignment *is* the targeted result, anchored
on the peak.

```python
# Run history of a sample, newest first
runs = mascope.peak_assignments.list_runs(sample_id)

# The whole ledger of the latest completed run: one row per observed peak.
# Pages through the API internally; run metadata rides on df.attrs["run"].
assignments = mascope.peak_assignments.get(sample_id)
assignments.attrs["run"]["engine_version"]
assignments["tier"].value_counts()

# Server-side filters (a bad value raises ValidationError naming the accepted set)
assigned = mascope.peak_assignments.get(sample_id, tier="assigned")
stage_b = mascope.peak_assignments.get(sample_id, source="untargeted")
curated = mascope.peak_assignments.get(sample_id, source="manual")

# A specific (e.g. older) run
old = mascope.peak_assignments.get(
    sample_id, run_id=runs.iloc[-1]["peak_assignment_run_id"]
)

# Full detail of one assignment: alternatives considered + scoring provenance
full = mascope.peak_assignments.detail(
    sample_id, assignments.iloc[0]["peak_assignment_id"]
)
```

Key `get()` columns: `sample_peak_mz`, `sample_peak_intensity`, `role` (`M0` |
`iso_child` | `reagent` | `artifact` | `unassigned`), `assigned_formula`,
`ion_formula`, `isotope_formula`, `source` (`database` | `untargeted` |
`manual`), `fit_score`, `evidence`, `mz_error_ppm`, `tier`, `p_correct`, and
`target_compound_id`/`target_ion_id` for database-sourced assignments.

### Hand-curated rows (`source: manual`)

A person can overrule the engine on a single row from the app's peak
inspector — promote a close alternative, or commit a re-search hit. That row is
persisted with `source` **`manual`**, so it **leaves** `database`/`untargeted`
rather than joining them:

```python
# Wrong: these two no longer sum to the run
stage_a = mascope.peak_assignments.get(sample_id, source="database")
stage_b = mascope.peak_assignments.get(sample_id, source="untargeted")

# Right: read the run once and split it locally, so nothing can fall out
assignments = mascope.peak_assignments.get(sample_id)
assignments.groupby("source", dropna=False).size()
```

Peaks nothing explained carry no source at all (`None`), which is why the
groupby above passes `dropna=False`. A `manual` row is not necessarily an
*assigned* one either: when an override displaces a compound, the
isotopologues of that compound are stripped and end up `source: manual`,
`tier: unassigned`, with no formula.

`peak_assignments.detail()` on a curated row returns a `provenance.manual`
block — `action`, `user_id`, `at`, and `previous` (the displaced winner, kept
verbatim), plus `manual.demoted`, the archive of the isotopologues the override
stripped so committing that compound back restores them.

### Which engine produced a run

A run's ledger does not say who computed it — the run does. `list_runs` (and
`df.attrs["run"]`) carries:

| Column | Meaning |
| --- | --- |
| `engine` | `mascope` for a run this deployment computed, otherwise the external engine that published its ledger here. Never null, and `mascope` is reserved server-side so an import cannot claim it. |
| `engine_version` | That engine's version string. |
| `tier_bands` | The `assigned` / `candidate` evidence thresholds the run tiered with. |
| `calibration` | What an external engine disclosed about its calibration at import. Null for `mascope` runs, whose calibration state is the sample's own. |

This matters because reads default to the **latest completed run whatever its
engine**, so a published run is what you get unless you ask for another. It is
also what makes two engines comparable on one sample — read each run by id and
join on `sample_peak_id`:

```python
runs = mascope.peak_assignments.list_runs(sample_id)
runs[["engine", "engine_version", "tier_bands", "status"]]

mine = mascope.peak_assignments.get(sample_id, run_id=<a mascope run id>)
theirs = mascope.peak_assignments.get(sample_id, run_id=<an imported run id>)
side_by_side = mine.merge(theirs, on="sample_peak_id", suffixes=("_mascope", "_ext"))
```

Compare tiers only against each run's own `tier_bands`: the same word means
different confidence under different thresholds. An imported run's `p_correct`
is always empty — that column is Mascope's own calibrated judgement and an
import may not write it.

For cross-sample analysis use the [`load_assignments`](#load_assignments-peak-assignments-across-batches)
loader; for a guided walk-through see tutorial notebook `10_peak_assignment.ipynb`.

## Changing Data

The SDK can also change what your role lets you change: datasets, batches,
samples, target collections and compounds, and saved matches. Each write
method is one call to one API route and does what that route does, no more:
the server checks the fields and your role, and a refusal comes back as the
SDK's exceptions.

```python
dataset = mascope.datasets.create("Spring campaign")
mascope.batches.copy(batch_id, dataset["dataset_id"], name="Spring copy")

collection = mascope.collections.create(
    "Organic acids",
    compounds=[
        {"target_compound_formula": "C2H4O2", "target_compound_name": "Acetic acid"}
    ],
)
mascope.collections.associate(collection["target_collection_id"], [batch_id])

preview = mascope.matching.aggregate_batch(batch_id)  # what a save would save
mascope.matching.save_batch(batch_id)
```

- A method returns the route's data: the dataset, batch, sample or collection
  as it now is. One whose route answers with a message alone returns that
  answer as a dict.
- A **copy**, a **move** and a **batch delete** run in the background on the
  server: the call returns once the work has started, with its `process_id`,
  and the result shows in the listings when it is done.
- Some updates replace rather than add. `batches.update` takes the batch's
  target collections and sets them to that list, so to only rename a batch
  send the collections it has now; `collections.associate` sets the batches a
  collection is associated with, and an empty list detaches it from them all.
  `collections.update` sets a collection's compounds to the ones it creates
  (`compounds=`) and names (`compound_ids=`): to add compounds, send the
  current `compound_ids` too, or the ones left out are taken out.
  `samples.update` takes the whole sample: start from `samples.get`, and give
  an ingested sample (type `ACQUISITION`, which only the system sets) a user
  type in the same call, or the route refuses it.
- A formula change in `compounds.update` deletes the compound and creates it
  again under a new ID, with the name and CAS number the item gives: send
  both with the formula.
- A write clears the client's cache (see [Caching](#caching)), so the next
  listing shows the change.
- Deleting a dataset, a batch, a collection or a compound and
  `matching.resave_batch` cannot be undone.

A write your role does not allow raises `PermissionDeniedError`. The server
says only that it refused; the error also names what the call acted on and
the role it needs:

```python
from mascope_sdk import PermissionDeniedError

try:
    mascope.datasets.move(dataset_id, other_workspace_id)
except PermissionDeniedError as e:
    print(e.object_type, e.object_id)  # dataset abc123
    print(e.required_role)  # editor in its workspace and in workspace '...'
```

The role each write needs is listed in the
[SDK & API guide](https://github.com/ultra-trace-systems/mascope/blob/master/docs/user/sdk/index.md#what-a-token-may-change).

## Caching

Dataset, batch, sample, and ionization mechanism listings are cached (in volatile memory) automatically after the first call. This speeds up repeated name resolution and avoids redundant API calls. Each call hands out a copy of the cached listing, so editing what you got leaves the cache, and the next call, as fetched. A write made through the SDK clears it. When data on the server changes otherwise (e.g. a batch created in the web app, or a copy the server finishes in the background), the cache needs to be cleared to reload the data on the next call. The cache is not persisted on disk, so restarting the kernel always clears the cache.

```python
# Clear the cache when server data changes
mascope.clear_cache()
```

## API Reference

### MascopeClient

```python
from mascope_sdk import MascopeClient

mascope = MascopeClient(workspace="My Workspace")
```

### Resources

All `list()` methods accept names (or substrings) instead of IDs and return `pd.DataFrame | None`.
A plain string filters case-insensitively as a **literal substring** — regex
metacharacters carry no special meaning, so a name like `"Sample (A)"` matches
as-is. To filter with a regular expression, pass a compiled pattern; case
comes from its flags. For example,
`batches=re.compile("2025|2026")` matches batch names containing "2025" or "2026".

#### `mascope.datasets`

| Method   | Description                  | Returns             |
| -------- | ---------------------------- | ------------------- |
| `list()` | List all accessible datasets | `pd.DataFrame│None` |
| `create(name, description=, dataset_type=, instrument=, icon=, workspace_id=)` | Create a dataset | `dict` |
| `update(dataset_id, name=, description=, icon=, workspace_id=)` | Rename it, or change its description or icon | `dict` |
| `move(dataset_id, target_workspace_id, workspace_id=)` | Move it into another workspace | `dict` |
| `delete(dataset_id, workspace_id=)` | Delete it and everything in it | `dict` |
| `tombstones()` | What deleted datasets were, newest delete first | `list[dict]` |

The writes act in the client's workspace unless `workspace_id` names another.

A deleted dataset leaves a tombstone: its name, description, descriptive
metadata, how many batches, samples and files it held, who deleted it and
when, and whether alone or with its workspace. `tombstones()` returns those of
every workspace you are a member of (a global admin's: all of them), across
workspaces, not only the client's.

#### `mascope.batches`

| Method          | Description                         | Returns             |
| --------------- | ----------------------------------- | ------------------- |
| `list(dataset)` | List batches in a dataset (by name) | `pd.DataFrame│None` |
| `update(batch_id, target_collection_ids, name=, description=)` | Rename it and set its target collections | `dict` |
| `copy(batch_id, dataset_id, name, description=)` | Copy it into a dataset, in the background | `dict` |
| `delete(batch_id)` | Delete it and its samples, in the background | `dict` |

#### `mascope.samples`

| Method                                          | Description                                          | Returns             |
| ----------------------------------------------- | ---------------------------------------------------- | ------------------- |
| `list(batch=, batches=, dataset=, samples=)`    | List samples from one or more batches                | `pd.DataFrame│None` |
| `get(sample_id)`                                | Get sample details                                   | `dict│None`         |
| `get_peaks(sample_id, ...)`                     | Get peak data with optional match/filter/time params | `pd.DataFrame│None` |
| `get_peak_timeseries(sample_id, mz=, peak_id=)` | Get intensity over time for a peak                   | `pd.DataFrame│None` |
| `compute_peak_timeseries(sample_id, peak_ids=)` | Have the server compute the timeseries peaks lack    | `int`               |
| `get_spectrum(sample_id, ...)`                  | Get averaged spectrum                                | `pd.DataFrame│None` |
| `get_spectra(sample_ids, ...)`                  | Get spectra for multiple samples                     | `pd.DataFrame│None` |
| `get_centroids(sample_ids)`                     | Get centroid data                                    | `dict│None`         |
| `update(sample_id, sample_item)`                | Update a sample's fields (the whole sample)          | `dict`              |
| `move(sample_ids, batch_id)`                    | Move samples into a batch, in the background         | `dict`              |
| `copy(sample_ids, batch_id)`                    | Copy samples into a batch, in the background         | `dict`              |

A `get_peaks` over a time range (`t_min` / `t_max`) leaves out the peaks whose timeseries has not been computed yet; see [Peaks left out of a time range](#peaks-left-out-of-a-time-range).

`list` accepts exactly one of `batch` (must match a single batch; raises if ambiguous) or `batches` (returns samples from all matching batches, with an added `sample_batch_name` column).

#### `mascope.sample_files`

| Method                                                              | Description                                           | Returns             |
| ------------------------------------------------------------------- | ----------------------------------------------------- | ------------------- |
| `get_scans(sample_file_id, fields=, first_scan=, last_scan=, ...)`  | A file's scan table: one row per scan, no centroids   | `pd.DataFrame│None` |
| `download_raw(sample_file_id, path)`                                | The file's source `.raw` / `.h5`, streamed to disk    | `Path`              |

A sample names the file it was cut from in `sample_file_id` (`mascope.samples.get(sample_id)["sample_file_id"]`). `get_scans` returns one row per scan of that file with the scan's statistics (`ScanNumber`, `StartTime` in minutes, `TIC`, `ScanType`, `MsType`) and its trailer under the instrument's own labels, colon included (`"FT Resolution:"`, `"Conversion Parameter B:"`). Scans of every MS order are in it and none is left out; filter on `MsType` for the MS1 scans alone. No peak is read for it, so it stays under a megabyte for a file whose centroids run to hundreds.

```python
file_id = mascope.samples.get(sample_id)["sample_file_id"]
scans = mascope.sample_files.get_scans(
    file_id,
    fields=[
        "Conversion Parameter B:",
        "Conversion Parameter C:",
        "FT Resolution:",
        "MsType",
    ],
    first_scan=1,
    last_scan=100,
)
```

`fields` keeps the named columns (`ScanNumber` always comes with them); `first_scan` / `last_scan`, `t_min` / `t_max` (seconds) and `polarity` narrow the scans. The number of scans in the file and the file's own statistics ride on `df.attrs["num_of_scans"]` and `df.attrs["stats_per_file"]`. Only a file whose raw Orbitrap data is stored has a scan table; any other is refused with a `ValidationError`.

`download_raw` saves the file the instrument wrote, byte for byte as the server stores it, streaming it to disk a megabyte at a time. Give it a directory and the file keeps the name it was uploaded with (a path ending in `/` is a directory, made if it does not exist yet); give it a file path and it is written there. A download is not resumed: one cut off part way raises `MascopeConnectionError` and leaves nothing at the destination. The server limits how many files an account may download in a while (60 in ten minutes) and answers 429 past that.

```python
raw = mascope.sample_files.download_raw(file_id, "downloads/")
```

#### `mascope.matching`

| Method                                 | Description                  | Returns           |
| -------------------------------------- | ---------------------------- | ----------------- |
| `match_compound(sample_id, formula)`   | Match a compound in a sample | `dict│None`       |
| `match_compounds(sample_id, formulas)` | Match multiple compounds     | `list[dict]│None` |
| `aggregate_sample(sample_id, target_ion_id=, match_params=)` | Run a sample's matching, saving nothing | `dict` |
| `aggregate_batch(batch_id, target_ion_id=, match_params=)` | Run a batch's matching, saving nothing | `dict` |
| `save_sample(sample_id, ...)` | Run a sample's matching and save it | `dict` |
| `save_batch(batch_id, ...)` | Run a batch's matching and save it | `dict` |
| `resave_batch(batch_id, ...)` | Replace a batch's saved matches with a fresh run's | `dict` |

The saves take `target_ion_id` and `match_params` as the runs do. The batch saves also take `match_ions=`, `match_compounds=`, `match_collections=`, `match_samples=`: False leaves that level's matches out. `save_sample` always saves every level; its route takes no such flags. A save answers with the server's `status` and `message`.

#### `mascope.collections`

| Method | Description | Returns |
| ------ | ----------- | ------- |
| `list(batch_id=, workspace_id=, all_workspaces=)` | List the workspace's collections and the global ones, or those of one batch | `pd.DataFrame│None` |
| `get(collection_id)` | One collection, with its compounds and batches | `dict` |
| `create(name, description=, collection_type=, compounds=, compound_ids=, batch_ids=, workspace_id=, is_global=)` | Create a target collection | `dict` |
| `update(collection_id, name=, description=, collection_type=, compounds=, compound_ids=)` | Change it or its compounds | `dict` |
| `associate(collection_id, batch_ids)` | Set the batches it is associated with | `dict` |
| `delete(collection_id, delete_orphan_compounds=)` | Delete it | `dict` |

A collection belongs to the client's workspace unless `workspace_id` names another, or to every workspace with `is_global=True`. `associate` needs only the guest role on the collection, and the editor role on each batch it adds or removes.

#### `mascope.compounds`

| Method | Description | Returns |
| ------ | ----------- | ------- |
| `add(compounds)` | Add compounds, each `{"target_compound_formula": ..., "target_compound_name": ...}` | `dict` |
| `update(compounds)` | Update compounds, each with its `target_compound_id` | `dict` |
| `remove(compound_id)` | Delete a compound, taking it out of every collection | `dict` |

The compound list is shared by every workspace, so these need the global editor role. `add` lists a compound that already exists (the same name and formula, or CAS number) under `existing_compounds` instead of creating it again.

#### `mascope.peak_assignments`

| Method                              | Description                                                       | Returns             |
| ----------------------------------- | ----------------------------------------------------------------- | ------------------- |
| `list_runs(sample_id)`              | List a sample's assignment runs, newest first                     | `pd.DataFrame│None` |
| `get(sample_id, run_id=, tier=, role=, source=)` | Full run ledger, one row per peak; run metadata on `df.attrs["run"]` | `pd.DataFrame│None` |
| `detail(sample_id, peak_assignment_id)` | One assignment in full (`alternatives`, `provenance`)          | `dict│None`         |

`get` reads the latest completed run unless `run_id` is given, and pages through the API internally — the whole run comes back as one DataFrame. See [Peak Assignments](#peak-assignments).

#### `mascope.ionization`

| Method   | Description                          | Returns             |
| -------- | ------------------------------------ | ------------------- |
| `list()` | List available ionization mechanisms | `pd.DataFrame│None` |

Columns: `ionization_mechanism_id`, `ionization_mechanism` (the mechanism in the standard adduct notation: `[M+H]+`, `[M-H]-`, `[M+Br]-`, `[M]+.` for electron transfer), `ionization_mechanism_polarity`, `shipped` (whether Mascope ships the mechanism, so that every server holds it from its first start).

#### `mascope.cheminfo`

| Method                                | Description                               | Returns      |
| ------------------------------------- | ----------------------------------------- | ------------ |
| `query_by_mz(mz, mechanism_ids, ...)` | Query potential formulas for an m/z value | `list[dict]` |

## Examples

### Load peaks and plot by compound

```python
from mascope_sdk import MascopeClient

mascope = MascopeClient(workspace="My Workspace")

peaks = mascope.load_peaks(dataset="My Dataset", batches="Uronium")

# Filter to matched peaks and summarise by compound
matched = peaks[peaks["target_compound_formula"].notna()]
summary = matched.groupby("target_compound_formula")["area"].mean()
summary.sort_values(ascending=False).head(10).plot.barh()
```

### Intra-sample timeseries

```python
import matplotlib.pyplot as plt
from mascope_sdk import MascopeClient

mascope = MascopeClient(workspace="My Workspace")

ts = mascope.load_peak_timeseries(
    dataset="My Dataset",
    compound="Urea",
)

# Plot per-isotope timeseries for one sample
sample = ts[ts["sample_item_name"] == ts["sample_item_name"].iloc[0]]
for isotope, group in sample.groupby("target_isotope_formula"):
    plt.plot(group["time"], group["height"], label=isotope)
plt.xlabel("Time (s)")
plt.ylabel("Intensity")
plt.legend()
plt.show()
```

### Compare stages within a sample

```python
from mascope_sdk import MascopeClient

mascope = MascopeClient(workspace="My Workspace")

# First load samples so the name is cached for resolution
mascope.samples.list(batch="My Batch")

stages = [
    (0, 30, "blank"),
    (30, 120, "sample"),
    (120, 180, "wash"),
]

peaks = mascope.load_peaks_by_stage(sample="My Sample", stages=stages)
peaks.groupby("stage_name")[["area", "height"]].mean()
```

### Low-level peak timeseries

```python
import matplotlib.pyplot as plt
from mascope_sdk import MascopeClient

mascope = MascopeClient(workspace="My Workspace")

ts = mascope.samples.get_peak_timeseries(
    sample_id="sample-123",
    mz=180.063,
    mz_tolerance_ppm=5.0,
)

if ts is not None:
    plt.plot(ts["time"], ts["height"])
    plt.xlabel("Time (s)")
    plt.ylabel("Intensity")
    plt.title(f"Peak at m/z {ts['mz'].iloc[0]:.3f}")
    plt.show()
```

## For Developers

### Logging

The SDK logs operational info (batch resolution, request counts, etc.) via [loguru](https://github.com/Delgan/loguru). The default level is `INFO`.

Set the `MASCOPE_SDK_LOG_LEVEL` environment variable to change it:

```env
MASCOPE_SDK_LOG_LEVEL=DEBUG    # verbose (HTTP requests, cache hits, etc.)
MASCOPE_SDK_LOG_LEVEL=WARNING  # quiet (only warnings and errors)
```

Or in Python before importing the SDK:

```python
import os

os.environ["MASCOPE_SDK_LOG_LEVEL"] = "DEBUG"

from mascope_sdk import MascopeClient
```

### SSL Verification

By default the SDK verifies SSL certificates. To disable verification (e.g. for local development with a self-signed certificate), set:

```env
MASCOPE_SDK_VERIFY_SSL=false
```

Or pass it to the constructor:

```python
mascope = MascopeClient(verify_ssl=False)
```

### Error Handling

```python
from mascope_sdk import MascopeClient
from mascope_sdk.exceptions import (
    AuthenticationError,
    NotFoundError,
    ConfigurationError,
)

try:
    mascope = MascopeClient()
    sample = mascope.samples.get("invalid-id")
except ConfigurationError:
    print("Missing MASCOPE_URL or MASCOPE_ACCESS_TOKEN")
except AuthenticationError:
    print("Invalid API token")
except NotFoundError:
    print("Sample not found")
```

#### Exception Hierarchy

- `MascopeError` — Base exception
  - `ConfigurationError` — Missing/invalid configuration
  - `MascopeConnectionError` — Network/connection issues
    - `MascopeTimeoutError` — Request timeout
  - `MascopeAPIError` — API errors (includes `status_code`, `message`, `url`)
    - `AuthenticationError` — 401/403 responses
      - `PermissionDeniedError` — 403: the token is valid, its user lacks the role; a write names its `object_type`, `object_id` and `required_role`
    - `NotFoundError` — 404 responses
    - `ValidationError` — 422 responses
    - `ServerError` — 5xx responses

### Project Structure

```
mascope_sdk/
├── __init__.py          # Public exports (MascopeClient, exceptions)
├── client.py            # MascopeClient: main entry point and high-level loader methods
├── exceptions.py        # Exception hierarchy
├── _http.py             # Low-level HTTP session (requests wrapper)
├── _resolve.py          # Name-to-ID resolution helpers
├── _loaders.py          # High-level loaders (load_peaks, load_peak_timeseries, load_peaks_by_stage, load_assignments)
├── _concurrent.py       # ThreadPoolExecutor wrapper with progress bars and cancellation
├── _agents.py           # Internal HTTP helpers for Mascope agents (file-agent)
├── resources/
│   ├── _base.py         # BaseResource: shared HTTP helpers and datetime coercion
│   ├── batches.py       # BatchesResource
│   ├── cheminfo.py      # CheminfoResource (m/z queries)
│   ├── datasets.py      # DatasetsResource
│   ├── ionization.py    # IonizationResource
│   ├── matching.py      # MatchingResource (compound matching)
│   ├── peak_assignments.py  # PeakAssignmentsResource (read persisted assignment runs)
│   ├── sample_files.py  # SampleFilesResource (a file's scan table)
│   ├── samples.py       # SamplesResource (peaks, spectra, timeseries)
│   └── workspaces.py    # WorkspacesResource
└── examples/            # Jupyter notebook examples
```

**Key patterns:**

- **`client.py`** owns the public API. High-level loaders (`load_peaks`, etc.) are thin wrappers that delegate to `_loaders.py`.
- **`resources/`** contains one class per API domain. Each resource inherits `BaseResource` which provides `_get()` / `_post()` helpers and automatic datetime column coercion.
- **`_concurrent.py`** centralises `ThreadPoolExecutor` usage with `run_concurrent()`, which handles progress bars (tqdm), `None`-filtering, future cancellation on error, and the `max_workers <= 8` guard. It returns the failed tasks beside the results; `mark_complete()` turns them into a frame's `attrs["complete"]` / `attrs["failures"]`.
- **`_resolve.py`** handles name matching (literal substring for strings, regex for compiled patterns) and name -> ID resolution (used by resources and loaders).
- **Underscore-prefixed modules** (`_http`, `_loaders`, `_concurrent`, `_resolve`, `_agents`) are internal — not part of the public API.
