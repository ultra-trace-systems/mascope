# Import data files

Getting a measurement into Mascope is a two-step process: get the raw
instrument file **in** — automatically via the **File Agent** running on the
instrument PC, or by **uploading from your computer** in the web app — then
build your batch from the samples Mascope creates. Every arriving file is
processed automatically into calibrated, matched samples in the instrument's
acquisition workspace; you **copy** the acquisition batch (or a selection of
its samples) into your own workspace to analyse it.

New to the terms *sample file*, *sample*, *batch*, and *ionization mode*? See
[Concepts](../concepts/index.md) first — this guide assumes them.

## Prerequisites

Before your first import, make sure the following are in place.

**Your access.** An **editor** role (or higher) in the instrument's workspace.
Guests can view and export but cannot upload or process. See
[Authorization](https://github.com/ultra-trace-systems/mascope/blob/master/docs/authorization.md).
Uploading a file for a brand-new instrument creates that instrument's workspace
and makes you its owner.

**A supported file.**

| Instrument | Extension | Max size (web upload) | Max size (File Agent) |
| --- | --- | --- | --- |
| Orbitrap | `.raw` | 2.5 GB | none* |
| Tofwerk TOF | `.h5` | 2.5 GB | none* |

*The File Agent uploads files of any size in resumable chunks. Older
File Agent versions upload each file in a single request capped at
100 MB - download the newest installer to lift the limit.

**A filename Mascope can read.** Mascope reads three things out of the filename,
so uploads are rejected if any is missing. This applies to both upload paths.
Name files as:

```
<instrument>_<timestamp>_<ionization-token>...<.raw|.h5>
```

- **Instrument** — the first segment, before the first underscore. It must
  identify the instrument type: names containing `orbi` are treated as Orbitrap,
  and names containing `tof` or `api` as TOF. The instrument type must match the
  extension (`orbi…` with `.raw`, `tof…` with `.h5`). Use only letters, digits,
  and hyphens in this segment.
- **Timestamp** — an acquisition date/time somewhere in the name, in one of the
  recognised forms (for example `20240115_1430`, `20240115143000`, or
  `2024.01.15-14h30m00s`). Mascope uses it to place and order the file.
- **Ionization token** — the short token of a configured **ionization mode** must
  appear in the name. This is how Mascope knows how the sample was ionized.

**Configured ionization modes.** Because the filename must contain a known
ionization token, the ionization modes you use have to exist first. This is a
prerequisite in its own right — see [Set up ionization modes](#set-up-ionization-modes)
below.

**A place to analyse in.** Your copies of the samples will live in a **batch**,
which lives in a **dataset** inside a **workspace** of your own. If you do not
have them yet, create them first — see
[First steps in the app](../getting-started/first-steps.md#create-a-dataset-and-a-batch).

## Set up ionization modes

An **ionization mode** tells Mascope how a measurement was ionized, and it is
what links a raw file to the right processing. Its filename token is what lets
Mascope recognise and process an uploaded file, so the modes you acquire under
must be configured before you import. See
[Concepts → Ionization](../concepts/index.md#ionization-modes-and-mechanisms) for
what a mode represents.

Open the **Raw files** tab and click **Edit ionizations** to open the *Edit
Ionization Settings* dialog. It has two tabs: **Ionization Modes** and
**Ionization Mechanisms**.

**1. Define the mechanisms first (Ionization Mechanisms tab).** A mechanism
describes a single charge-forming reaction as an operation, a modification
formula, and the charge of the transferred species: a leading `+` (add) or `-`
(remove), the formula added or removed (for example `H` or `Br`), and a trailing
`+` or `-` giving that species' charge. The *ion* polarity is not written
directly — it follows from the two signs: adding a positively charged species or
removing a negatively charged one yields a positive ion, and vice versa. For
example:

- `+H+` — protonation, `[M+H]+` (positive)
- `-H+` — deprotonation, i.e. removal of a proton (`H+`), giving `[M-H]-` (negative)
- `+Br-` — bromide adduct, `[M+Br]-` (negative)
- a bare `+` or `-` — electron transfer

A mode can only use mechanisms of its own polarity, so make sure the ones you
need exist.

**2. Create the mode (Ionization Modes tab).** Fill in the *Create New Ionization
Mode* form:

| Field | Required | What it is |
| --- | --- | --- |
| **Mode Name** | Yes | A descriptive name for the mode. |
| **Filename token** | For imports | The token to look for in filenames. Without it, files acquired in this mode cannot be recognised on upload. |
| **Polarity** | Yes | `+` or `-`. Choose this first — it filters the available mechanisms. |
| **Mechanisms** | Yes | One or more mechanisms of the chosen polarity. |
| **Calibration Collection** | Optional | A [calibrants](../concepts/index.md#targeted-analysis) collection used to calibrate the mass axis for samples in this mode. |
| **Diagnostic Collection** | Optional | A [diagnostics](../concepts/index.md#targeted-analysis) collection used to monitor instrument health. |

Click **Create**. The calibrant and diagnostic collections are optional, but
setting a calibrant collection is what lets Mascope calibrate samples acquired
in this mode automatically on import — without it, samples in this mode stay
uncalibrated.

!!! note "Who can change modes"
    Any **editor** can create a mode. **Editing or deleting** a mode requires
    **admin**, because it affects every sample already processed under it —
    changing the calibrant collection flags the affected batches for
    re-calibration, and changing the mechanisms or diagnostic collection flags
    them for re-matching.

## Get the raw files in

### Automatically, with the File Agent

The **File Agent** is a small program that runs on the instrument PC, watches an
acquisition folder, and uploads new files to Mascope as they are written. This is
the recommended path for routine acquisition — once it is set up, files arrive in
Mascope with no manual step. The same filename rules above apply, so name your
acquisition method's output accordingly.

Installing, pairing, and configuring the agent (the watched folder, the file
pattern, upgrades, and troubleshooting) is covered in full on the
[Instruments & acquisition](../instruments/index.md) page. Note that the File
Agent skips files larger than 100 MB; upload those from the web app instead.

### Manually, from your computer

To import files you already have on your machine:

1. Open the **Raw files** tab (the first tab of the right-hand panel).
2. Either click **Upload** and pick your files, or drag them onto the pane. You
   can add many files at once (up to 2.5 GB each).
3. Mascope validates each file's name against the rules above. Anything it cannot
   read (unknown instrument prefix, wrong extension, or no matching ionization
   token) is listed as invalid and left out; fix the name and try again.
4. Watch the progress notification until the uploads finish.

However they arrive, uploaded files appear in the raw-files table (listed by
filename, polarity, and datetime), and Mascope processes each one automatically
in the background: for every ionization mode in the file it creates a
calibrated, matched **sample** in the instrument's `Acquisitions <instrument>`
workspace. Those acquisition records are read-only — to analyse the data, copy
the samples into a batch of your own. That is the next step.

!!! tip "Finding files after upload"
    The table shows one time window at a time (default: the last 24 hours). Use
    the time-range and polarity filters and the filename search at the top of the
    tab to locate older files.

## Build your batch from the acquisition samples

Everything a file needs to become analysable has already happened by the time
it is uploaded: the `Acquisitions <instrument>` workspace holds a dataset per
year, a daily batch per ionization mode (named like
`2026-07-28 Nitrate acquisition`), and one processed sample per file — already
calibrated and matched. The recommended way to get an analysis batch is to
copy a whole acquisition batch and make it your own:

1. Open the **Home menu** (house icon, top-left) and select the
   `Acquisitions <instrument>` workspace.
2. Open the year dataset, right-click the daily acquisition batch that holds
   your measurements, and choose **Copy batch**.
3. Switch back to your own workspace via the Home menu, open your dataset,
   right-click the empty space in the *Batches* pane, and choose
   **Paste batch**.

The copy — with all its samples — is yours: rename it, and delete the samples
that do not belong to your analysis. The acquisition record is untouched.

To cherry-pick individual measurements instead, open the acquisition batch,
select the samples you want (hold `Shift` or `Ctrl` to select several),
right-click and choose **Copy samples**, then paste them into a batch of your
own (right-click the empty space in its *Samples* pane → **Paste samples**).

### Alternative: process raw files by hand

The **Raw files** tab can also process files straight into your batch — for
the cases the automatic processing does not cover, such as custom time windows
within a file or per-sample metadata pasted from an autosampler report. Prefer
the copy flow above for routine work.

1. In the sample browser, select the **batch** the samples should go into (the
   **Process selected** button stays disabled until a batch is selected).
2. In the **Raw files** tab, select the raw files to process. Select files of
   a single polarity, or pick a polarity from the dropdown if a file contains both.
3. Click **Process selected**:
   - **One file** opens a dialog to create a single sample from it. Its
     **Ionization Mode** is preselected from the token in the filename; when the
     filename carries no configured token, choose the mode the file was acquired
     in — the list offers every mode of the sample's polarity.
   - **Several files** opens the batch-import dialog, where you paste per-sample
     metadata (sample **name** and **type** are required; a **filter ID** and any
     extra attributes are optional) from a spreadsheet or autosampler report. The
     dialog previews the samples and flags any issues before you confirm. Here
     every filename does need a recognised ionization token.
4. Confirm. Mascope processes the files — you will see progress in the batch — and
   the new samples appear in the batch, tagged with their ionization mode.

## What happens next

Your samples arrive already calibrated and matched by the processing pipeline
([How it works](../how-it-works/index.md) explains the stages), so once a
batch has samples, you can go straight to analysis:

- **Attach a target collection** and run **matching** to find and score your
  compounds in each sample — see
  [Build a target collection and run matching](target-collections.md).
- **Compare and visualise** samples in the **Batch** and **Sample** views.

## Troubleshooting

- **A file was rejected as invalid on upload.** The name is missing something
  Mascope needs. Check the instrument prefix matches the extension, that a
  timestamp is present, and that the name contains a configured ionization
  token. Add the ionization mode (or fix the name) and re-upload.
- **The filename token isn't recognised.** Confirm an ionization mode with that
  exact token exists in **Edit ionizations → Ionization Modes**, and that the
  token field is filled in (a mode with no token cannot match a filename).
  Processing a single file by hand does not depend on the token — pick the
  ionization mode in the dialog instead — but upload and batch import do.
- **"Paste samples" doesn't appear in the menu.** Copy samples first, then make
  sure your own batch is open — the paste goes into the batch whose *Samples*
  pane you right-click.
- **"Process selected" is greyed out.** Select a batch in the sample browser
  first, then select at least one raw file. If a file has mixed polarity, choose a
  polarity from the dropdown.
- **A file needs re-processing.** Right-click it in the raw-files table and choose
  **Re-process** to rebuild its acquisition data under the current ionization
  modes. This is only available for files not tied to a batch you created.
- **Uploads from the File Agent keep failing.** See the File Agent's
  [troubleshooting section](../instruments/index.md#troubleshooting-uploads) —
  it covers rejected tokens, HTTP 404s, and the 100 MB size limit.
