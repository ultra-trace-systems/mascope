# Build a target collection and run matching

Matching is how Mascope answers "is my compound in this sample, and how sure
are we?" — you define the compounds to look for in a **target collection**,
attach it to a batch, and Mascope finds and scores them in every sample. This
guide walks through building a collection, running matching, and reading the
results.

New to the terms *target collection*, *compound*, *ion*, and *match score*?
See [Concepts → Targeted analysis](../concepts/index.md#targeted-analysis)
first — this guide assumes them.

## Prerequisites

- A **batch with samples** in it — see [Import data files](import-files.md).
- An **editor** role (or higher) in the workspace.
- The batch open in the sample browser, so the *Target collections* pane
  (bottom-left) is showing.

## Create a collection

1. In the *Target collections* pane, click the **Create target collection**
   plus button. The *Create a new target collection* dialog opens.
2. Fill in a **Name** (and optionally a **Description**).
3. Pick the collection type: **TARGETS** for the compounds you want to detect,
   **CALIBRANTS** for mass-axis calibration references, **DIAGNOSTICS** for
   instrument-health monitoring. For this guide, choose **TARGETS**.
4. Choose the **workspace scope** — which workspace the collection belongs to.
   Admins can also make a collection *Global (all workspaces)*.

### Add compounds

You only provide the neutral molecule — a **formula**, plus an optional name
and CAS number. Mascope generates the target ions (one per configured
ionization mechanism) and their isotopic patterns automatically; at match time
it uses the ions that fit each sample's ionization mode.

Three ways to fill the **Compounds** tab, mix and match freely:

- **Paste from a spreadsheet.** Copy cells and paste them onto the left-hand
  *Collection* panel: one column is read as formulas; two columns as name +
  formula; three columns as name + formula + CAS number.
- **Import existing.** In the *Add compounds* panel, pick a source — another
  collection, or *All compounds* — search, and tick the compounds you want.
  Compounds are shared reference data, so reusing beats recreating.
- **Create new.** Switch the toggle to *Create new* and enter a **Formula**
  (required), **Name**, and **CAS Number**, then click **Create compound**. If
  the compound already exists in Mascope, the button reads **Add compound**
  and reuses it.

Each row in the collection list gets a status badge — *Create*, *Add*, *Keep*,
or *Remove* — so you always see what saving will change.

### Assign and save

On the **Batches** tab, tick the batches to analyse with this collection — the
batch you have open is pre-selected when its type is compatible. Click
**Save**, review the summary (scope, batch assignments, compound counts), and
confirm with **Create**.

## Attach or detach an existing collection

To analyse a batch against a collection that already exists:

1. Right-click the batch in the *Batches* pane (or the *Target collections*
   breadcrumb) and choose **Edit batch targets**.
2. On the **Targets** tab, tick the collections to attach — or untick to
   detach. Detaching never deletes the collection. Use the type filter
   (*Targets / Diagnostics / Calibrants / All*) and search to find them.
3. Click **Save**.

The mirror image also works: right-click a collection and choose **Edit
batches** to assign it across many batches at once.

## Run matching

Changing a batch's collections (or a collection's compounds) does not
recompute matches immediately — the batch is *flagged*: its status icon in the
*Batches* pane changes to circling arrows with the tooltip *"Batch has been
modified, matches may be out of date"*.

- **Click the status icon** to refresh the matches, or right-click the batch
  and choose **Process → Refresh matches**. (**Process → Rematch** rebuilds
  all matches from scratch — use it if results look stale after parameter
  changes.)
- To refresh a whole dataset at once, right-click it in the *Datasets* pane and
  choose **Process → Refresh matches**. Unlike the per-batch entry this one asks
  you to confirm first, because the run walks the whole dataset and cannot be
  stopped once it starts. Its batches are refreshed one after another, newest
  first; batches that are already up to date are skipped, and a batch that is
  mid-processing is left alone. A toast reports what was done when the run
  finishes.
- While matching runs, the batch row shows a spinner, progress bars appear
  along the bottom edge of the app, and a toast reports completion. New files
  processed into a batch are matched automatically as part of processing.

## Read the results

- **Target collections pane** — each attached collection carries a
  colour-coded percentage tag: the batch-level match score. For target
  collections the colouring is alarm-style — a confident detection shows
  **red**, not green.
- Click a collection to open its **ion table**: every target ion with its
  score, formula, compound, and ionization mechanism. Click an ion's expander
  (*Visualize ion match*) to open the **Match** tab for it.
- **Match tab** — one ion in detail: the matched isotopes with their *m/z* and
  relative abundances, the spectrum around each isotope, and the timeseries of
  matched peaks across the batch.
- **Batch tab** — the batch-wide overview chart. It needs a collection
  selected (it says so until you click one); then it plots the matched
  intensities per sample. Click any data point to jump to that sample's match.

### Tune the match parameters

In the **Match** tab, the sliders button (top-left, *Match parameters*) opens
the parameter drawer for the visualized ion. **Isotope settings** (*m/z
tolerance*, *minimum isotope abundance*, *isotope ratio tolerance*) and **Peak
settings** (*minimum peak intensity*) change what counts as a match — they
apply on the next refresh/rematch. The **match score thresholds** (*possible*
/ *probable*) only re-categorise the scores you already have. Parameters are
saved per ion and instrument with **Save parameters**; **Set defaults**
restores them.

### Record your judgement

The star button (top-right of the Match tab, *Rate Match*) records your expert
verdict on the visualized match: **Detection**, **Ambiguous**, or **No
Detection**. If your rating disagrees with the algorithm, a short
questionnaire asks what you saw, so the disagreement is captured with context.

## Take the results further

For anything beyond the built-in views — custom plots, statistics, reports —
load the results with the Python SDK, which pulls peaks and matches straight
into dataframes in a notebook or script:

```python
from mascope_sdk import MascopeClient

mascope = MascopeClient(workspace="My Workspace")
peaks = mascope.load_peaks(dataset="My Dataset", batches="My Batch")
```

See [SDK & API](../sdk/index.md) for installation and the full reference.

## Troubleshooting

- **The collections pane is empty.** It only lists collections once a batch is
  focused — open one in the sample browser first.
- **A collection cannot be ticked in *Edit batch targets*.** Greyed-out
  entries with a lock icon belong to another workspace; recreate the
  collection in this workspace or move your batch.
- **Scores did not change after editing a collection.** Matching has not run
  yet — check the batch's status icon and refresh its matches.
- **Editing a calibrants collection warns about calibration.** Changing
  calibrants affects how samples are calibrated; affected batches are flagged
  for re-calibration, which is an administrative operation. See
  [Concepts → Calibration](../concepts/index.md#calibration).
