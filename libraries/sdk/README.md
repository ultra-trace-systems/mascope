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

# List datasets (returns a DataFrame)
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

---

### `load_assignments`: Peak assignments across batches

Load the persisted **peak-assignment** results (see [Peak assignments](#peak-assignments)) of every sample across one or more batches, concatenated into a single DataFrame enriched with batch and sample metadata — the peak-assignment counterpart of `load_peaks`.

Read-only: each sample contributes its **latest completed** assignment run; samples without one are skipped (and logged), not assigned on the fly.

```python
# Assignments of every sample in matching batches
assignments = mascope.load_assignments(dataset="My Dataset", batches="Uronium")

# Confidently identified peaks that came from the untargeted stage
assignments = mascope.load_assignments(
    dataset="My Dataset", tier="identified", source="untargeted"
)

# Tier breakdown per sample
assignments.groupby(["sample_item_name", "tier"]).size()
```

Key columns: `sample_batch_name`, `sample_item_name`, `datetime_utc`, plus all columns from `peak_assignments.get` (one row per observed peak; core rows only — fetch `alternatives`/`provenance` per assignment with `peak_assignments.detail`).

---

### Confirmation prompt

`load_peaks`, `load_peak_timeseries`, and `load_assignments` show an interactive confirmation prompt when the number of samples exceeds `confirm_above`. Defaults are 100 for `load_peaks` and `load_assignments`, and 20 for `load_peak_timeseries`. This prevents accidentally launching hundreds of concurrent requests from a notebook cell. Set `confirm_above=None` to disable.

## Peak Assignments

Mascope's **peak-centric assignment** engine assigns a composition to *every*
observed peak of a sample — database-known targets first (Stage A), then
untargeted composition search (Stage B) — arbitrates a single owner per peak,
and files each assignment into a confidence tier (`identified` | `candidate` |
`below_assignability` | `unassigned`). Runs are launched from the Mascope app
and persisted; the SDK reads the results (it does not trigger runs).

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
identified = mascope.peak_assignments.get(sample_id, tier="identified")
stage_b = mascope.peak_assignments.get(sample_id, source="untargeted")

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
`ion_formula`, `isotope_formula`, `source` (`database` | `untargeted`),
`fit_score`, `mz_error_ppm`, `tier`, `p_correct`, and
`target_compound_id`/`target_ion_id` for database-sourced assignments.

For cross-sample analysis use the [`load_assignments`](#load_assignments-peak-assignments-across-batches)
loader; for a guided walk-through see tutorial notebook `10_peak_assignment.ipynb`.

## Caching

Dataset, batch, sample, and ionization mechanism listings are cached (in volatile memory) automatically after the first call. This speeds up repeated name resolution and avoids redundant API calls. When data on the server changes (e.g. new batch created), the cache needs to be cleared to reload the data on the next call. The cache is not persisted on disk, so restarting the kernel always clears the cache.

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

#### `mascope.batches`

| Method          | Description                         | Returns             |
| --------------- | ----------------------------------- | ------------------- |
| `list(dataset)` | List batches in a dataset (by name) | `pd.DataFrame│None` |

#### `mascope.samples`

| Method                                          | Description                                          | Returns             |
| ----------------------------------------------- | ---------------------------------------------------- | ------------------- |
| `list(batch=, batches=, dataset=, samples=)`    | List samples from one or more batches                | `pd.DataFrame│None` |
| `get(sample_id)`                                | Get sample details                                   | `dict│None`         |
| `get_peaks(sample_id, ...)`                     | Get peak data with optional match/filter/time params | `pd.DataFrame│None` |
| `get_peak_timeseries(sample_id, mz=, peak_id=)` | Get intensity over time for a peak                   | `pd.DataFrame│None` |
| `get_spectrum(sample_id, ...)`                  | Get averaged spectrum                                | `pd.DataFrame│None` |
| `get_spectra(sample_ids, ...)`                  | Get spectra for multiple samples                     | `pd.DataFrame│None` |
| `get_centroids(sample_ids)`                     | Get centroid data                                    | `dict│None`         |

`list` accepts exactly one of `batch` (must match a single batch; raises if ambiguous) or `batches` (returns samples from all matching batches, with an added `sample_batch_name` column).

#### `mascope.matching`

| Method                                 | Description                  | Returns           |
| -------------------------------------- | ---------------------------- | ----------------- |
| `match_compound(sample_id, formula)`   | Match a compound in a sample | `dict│None`       |
| `match_compounds(sample_id, formulas)` | Match multiple compounds     | `list[dict]│None` |

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

Columns: `ionization_mechanism_id`, `ionization_mechanism` (human-readable name), `ionization_mechanism_polarity`.

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
│   ├── samples.py       # SamplesResource (peaks, spectra, timeseries)
│   └── workspaces.py    # WorkspacesResource
└── examples/            # Jupyter notebook examples
```

**Key patterns:**

- **`client.py`** owns the public API. High-level loaders (`load_peaks`, etc.) are thin wrappers that delegate to `_loaders.py`.
- **`resources/`** contains one class per API domain. Each resource inherits `BaseResource` which provides `_get()` / `_post()` helpers and automatic datetime column coercion.
- **`_concurrent.py`** centralises `ThreadPoolExecutor` usage with `run_concurrent()`, which handles progress bars (tqdm), `None`-filtering, future cancellation on error, and the `max_workers <= 8` guard.
- **`_resolve.py`** handles name matching (literal substring for strings, regex for compiled patterns) and name -> ID resolution (used by resources and loaders).
- **Underscore-prefixed modules** (`_http`, `_loaders`, `_concurrent`, `_resolve`, `_agents`) are internal — not part of the public API.
