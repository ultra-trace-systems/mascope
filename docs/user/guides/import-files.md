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

**A filename Mascope can read.** Mascope reads three things out of the filename.
An upload without the first two is rejected; one without the third waits for you
to choose its chemistry. This applies to both upload paths. Name files as:

```
<instrument>_<timestamp>_<ionization-token>...<.raw|.h5>
```

- **Instrument** — the first segment, before the first underscore. Use only
  letters, digits, and hyphens in this segment. Whether the file is an
  Orbitrap or a TOF acquisition is decided by the file itself when it is
  converted, not by the name, so the instrument can be called anything. A
  paired **File Agent** that names its instrument in its configuration files
  every upload under that name, and its file names need not carry the
  instrument at all: the server puts `<instrument>_` in front of the stored
  name. A browser upload whose name starts with no instrument Mascope can
  place is not refused either: it asks which instrument the files came from,
  offering the ones it knows and taking the name of a new one, and reports
  that with the upload the way an agent does. A name it has never seen is
  a new instrument, so it asks you to confirm before creating it.
- **Timestamp** — an acquisition date/time somewhere in the name, in one of the
  recognised forms (for example `20240115_1430`, `20240115143000`, or
  `2024.01.15-14h30m00s`). Mascope uses it to place and order the file.
- **Ionization token** — the short token of a configured **ionization mode** in
  the name is how Mascope knows how the sample was ionized, and lets it
  process the file on its own. The name must match exactly one mode for each
  polarity in the file: a file acquired in both polarities needs the token of a
  positive mode and the token of a negative mode. A file whose name matches no
  mode, or two modes of the same polarity, is still uploaded and converted, but
  it gets no samples until you
  [choose its chemistry](#choose-the-chemistry-of-a-file-that-needs-one).

**Configured ionization modes.** A file is processed on its own only when its
name carries the token of a configured ionization mode, so the modes you use
should exist first: a file uploaded before its mode waits for someone to choose
its chemistry. This is a prerequisite in its own right — see
[Set up ionization modes](#set-up-ionization-modes) below.

**A place to analyse in.** Your copies of the samples will live in a **batch**,
which lives in a **dataset** inside a **workspace** of your own. If you do not
have them yet, create them first — see
[First steps in the app](../getting-started/first-steps.md#create-a-dataset-and-a-batch).

## Set up ionization modes

An **ionization mode** tells Mascope how a measurement was ionized, and it is
what links a raw file to the right processing. Its filename token is what lets
Mascope recognise and process an uploaded file on its own, so configure the
modes you acquire under before you import; a file whose name carries no token
still uploads, and waits for its chemistry to be chosen. See
[Concepts → Ionization](../concepts/index.md#ionization-modes-and-mechanisms) for
what a mode represents.

Open the **Raw files** tab and click **Edit ionizations** to open the *Edit
Ionization Settings* dialog. It has two tabs: **Ionization Modes** and
**Ionization Mechanisms**.

**1. Define the mechanisms first (Ionization Mechanisms tab).** A mechanism
describes a single charge-forming reaction, written in the standard adduct
notation: inside the brackets, what is added to or removed from the molecule
`M`, and after them the charge of the ion it makes. For example:

- `[M+H]+` — protonation (positive)
- `[M-H]-` — deprotonation (negative)
- `[M+Br]-` — bromide adduct (negative)
- `[M-H]+` — hydride abstraction (positive)
- `[M+CH4N2O+H]+` — a cluster with urea and a proton, one term per species added
  (positive)
- `[M+^NO3]-` — a labelled reagent, written as it is (negative)
- `[M]+.` or `[M]-.` — electron transfer, the dot marking the radical ion

The ion's polarity is the sign at the end, so a mechanism of the wrong polarity
cannot be saved. One molecule and a single charge are what a mechanism
describes: `[2M+H]+` and `[M+2H]2+` are refused, as is a mechanism that both adds
and removes (`[M+Na-2H]-`). The terms are saved in alphabetical order, so a
mechanism has one spelling however it is typed: `[M+H+CH4N2O]+` is saved as
`[M+CH4N2O+H]+`, and cannot be added a second time in the other order.

The older spelling is still accepted and saved in the standard one: an operation,
the formula, and the charge of the species moved rather than of the ion (`+H+`
for `[M+H]+`, `-H+` for `[M-H]-`, `+Br-` for `[M+Br]-`, a bare `+` or `-` for
electron transfer).

Mascope ships the mechanisms its own chemistry uses, so they are in the list from
the first start: the nitrate, 15N-nitrate, bromide, iodide, urea, ammonium and
15N-ammonium adducts, protonation, deprotonation, hydride abstraction, electron
transfer in either polarity, and the carbonate, formate, dibromide, diiodide,
sodium and potassium adducts that
[peak assignment](../how-it-works/peak-assignment.md) searches as secondary
channels. They carry a lock instead of a delete button, because Mascope would
only create them again at its next start. Add any other mechanism your modes
need.

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
   read (unknown instrument prefix or wrong extension) is listed as invalid and
   left out; fix the name and try again. A file whose name carries no
   ionization token is uploaded, with a note that it will wait for its
   chemistry to be chosen.
4. Watch the progress notification until the uploads finish.

However they arrive, uploaded files appear in the raw-files table (listed by
filename, status, polarity, and datetime), and Mascope processes each one
automatically in the background: for every ionization mode in the file it
creates a calibrated, matched **sample** in the instrument's
`Acquisitions <instrument>` workspace. Those acquisition records are read-only —
to analyse the data, copy the samples into a batch of your own. That is the
next step.

The **Status** column says how far processing got. Hover a status to read what
it means for that file, and use the status filter at the top of the tab to list
the files that need attention across every page. With a status chosen, the time
window goes by when each file's status was recorded, so a file uploaded or
re-processed long after it was acquired is listed too.

| Status | Meaning |
|---|---|
| Converted, Queued, Bound, Calibrated | Still being processed: the file was read (or processing was asked for again, and waits its turn), its samples exist, and its m/z axis was calibrated. |
| Done | Every sample of the file was matched, or it is a blank measurement with nothing to match. The detail says when the file was not calibrated, and why. |
| Needs a chemistry | The file's name binds it to no ionization mode - it carries no mode's token, tokens of two modes of one polarity, or, for a file of both polarities, a token for only one of them - so it has no samples yet. Choose its chemistry (below), or fix the tokens and re-process it. |
| Calibration failed | An m/z calibration failed or is below the quality bar, so some or all of the samples were not matched. A TOF file is matched only on a verified m/z calibration, so one whose ionization mode has no calibration collection ends here too, and the detail names the missing collection. |
| Failed | Processing stopped on an error, or was interrupted by a server restart. Re-process the file. |

A file still shown in progress a day after its status was recorded has
stopped - a server worker restarted under it, say - and its status says so:
re-process the file, or choose its chemistry. Files processed before Mascope
recorded the status show none. When an Orbitrap method alternates scan ranges
or scan modes within one polarity, the detail also says that peak detection
pools those scan streams into one peak list.

### Choose the chemistry of a file that needs one

A file whose name binds it to no ionization mode - no mode's token, tokens of
two modes of one polarity, or a token for only one of a file's two
polarities - is still converted and stored, but it waits as
**Needs a chemistry** until someone says which chemistry it was acquired
under. The people answerable for the instrument find it under **Needs
attention** in the notifications pane.

1. In the **Raw files** tab, set the status filter to **Needs a chemistry**.
2. Select the files that share a chemistry, right-click them and choose
   **Choose chemistry**.
3. Pick an ionization mode for each polarity the files hold, then **Process**.

The files are processed under those modes as if their names carried the
modes' tokens: calibrated, matched, and filed in the daily acquisition
batches. An editor of the instrument may do it. Re-processing a file whose
name carries no mode's token later keeps the modes it was given. Re-processing
refuses a name with tokens of two modes of one polarity, or with a token for
only one of its polarities: choose its chemistry again, or fix the tokens.

The same action gives a chemistry to a file that failed before its samples
were made, and corrects a wrong choice: a file that has samples already is
rebuilt under the modes you pick. It is not offered while a file is being
processed. Only the acquisition samples are replaced: a sample someone made
from the file in a batch of their own stays, and the file keeps its m/z
calibration rather than having it reset under that sample. If the mode you
pick calibrates the file, the new calibration marks that batch for
re-matching, as any new calibration of the file does. Re-processing still
refuses such a file.

!!! tip "Finding files after upload"
    The table shows one time window at a time (default: the last 24 hours). Use
    the time-range, status and polarity filters and the filename search at the
    top of the tab to locate older files.

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
  Mascope needs. Check the instrument prefix matches the extension and that a
  timestamp is present, then fix the name and re-upload. A missing ionization
  token no longer rejects a file: it waits in Raw files as *Needs a chemistry*.
- **The filename token isn't recognised.** Confirm an ionization mode with that
  exact token exists in **Edit ionizations → Ionization Modes**, and that the
  token field is filled in (a mode with no token cannot match a filename).
  Processing a single file by hand does not depend on the token — pick the
  ionization mode in the dialog instead — and neither does uploading: a file
  without a token waits in Raw files for its chemistry to be chosen. Batch
  import into a batch of your own still reads the token.
- **"Paste samples" doesn't appear in the menu.** Copy samples first, then make
  sure your own batch is open — the paste goes into the batch whose *Samples*
  pane you right-click.
- **"Process selected" is greyed out.** Select a batch in the sample browser
  first, then select at least one raw file. If a file has mixed polarity, choose a
  polarity from the dropdown.
- **A file needs re-processing.** Right-click it in the raw-files table and choose
  **Re-process** to rebuild its acquisition data under the current ionization
  modes. A file whose name matches no token keeps the modes its samples have,
  such as one whose chemistry was chosen by hand. This is only available for
  files not tied to a batch you created.
- **Uploads from the File Agent keep failing.** See the File Agent's
  [troubleshooting section](../instruments/index.md#troubleshooting-uploads) —
  it covers rejected tokens, HTTP 404s, and the 100 MB size limit.
