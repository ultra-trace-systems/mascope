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
- **Ionization token** (optional) — the short token of one of your server's own
  **ionization modes** in the name tells Mascope how the sample was ionized,
  and lets it process the file on its own. The name must match exactly one mode
  for each polarity in the file: a file acquired in both polarities needs the
  token of a positive mode and the token of a negative mode. A file whose name
  matches no mode, or two modes of the same polarity, is still uploaded and
  converted, but it gets no samples until you
  [choose its chemistry](#choose-the-chemistry-of-a-file-that-needs-one).

**A chemistry for each file.** Mascope processes a file under the chemistry it
was acquired under, and looks for it in this order: a chemistry someone chose
for the file, what the file's [acquisition record](../instruments/acquisition-records.md)
names, the token of a mode in its name, and, on a server that routes files by
their acquisition method, the chemistry the file's method has been seen
running. A file none of these names waits as **Needs a chemistry**. A fresh
server needs nothing set up for it: every server ships
[chemistry profiles](#chemistry-profiles), and choosing one for the file is a
single click. Modes and tokens of your own are the
[advanced path](#advanced-modes-of-your-own), for files that should be
processed on their own by name.

**A place to analyse in.** Your copies of the samples will live in a **batch**,
which lives in a **dataset** inside a **workspace** of your own. If you do not
have them yet, create them first — see
[First steps in the app](../getting-started/first-steps.md#create-a-dataset-and-a-batch).

## Chemistry profiles

A **chemistry profile** is a chemistry Mascope ships: its name, its polarity,
the [ionization mechanisms](../concepts/index.md#ionization-modes-and-mechanisms)
its ions are formed by and, where it has a reagent of its own, the calibrants
its files are calibrated on. Every server has the same profiles from its first
start, so they are there before anything has been processed, and nothing has to
be set up or adopted to use one: choosing a profile for a file is how it is
used (see [Choose the chemistry of a file that needs one](#choose-the-chemistry-of-a-file-that-needs-one)).

Open the **Raw files** tab and click **Chemistry** (the sliders button beside
the instrument) to open the *Chemistry* settings. Its **Profiles** tab lists
the profiles, each marked **Shipped**, with the calibrants it ships, how many
files it has routed and when the newest of them was acquired.

| Profiles | Calibrants they ship |
| --- | --- |
| Nitrate, Nitrate 15N, Bromide and Iodide (negative); Uronium (positive) | The reagent ion and its own clusters, such as `[Br]-`, `[Br2]-` and `[Br3]-` for bromide. An Orbitrap needs one of them in its scan window; a TOF fit needs three, and they all sit at the low end of the range. |
| Charge transfer (EASY-IC), negative and positive | The one ion of an Orbitrap's EASY-IC source, its lock mass. A TOF fit cannot use a single anchor, so a TOF file under one of these is not calibrated. |
| Deprotonation (negative); Protonation, Ammonium and Ammonium 15N (positive) | None: they have no reagent of their own. Their files are not calibrated until the profile is pointed at a calibrant collection under **Advanced**. |

A profile's name, polarity and mechanisms are the chemistry's, the same on every
server, so they cannot be changed and a profile cannot be deleted. The
collections it points at can: under **Advanced**, a profile can be pointed at a
calibrant or diagnostic collection of your own in place of the one it ships.

### Advanced: modes of your own

An **ionization mode** of your own describes a chemistry the profiles do not, or
the way your site names its files. Its filename token is what lets Mascope
recognise and process an uploaded file on its own, so set up the modes whose
tokens your file names carry before you import; a file whose name carries no
token still uploads, and waits for its chemistry to be chosen. See
[Concepts → Ionization](../concepts/index.md#ionization-modes-and-mechanisms) for
what a mode represents.

The **Advanced** tab of the *Chemistry* settings has two tabs of its own:
**Ionization Modes** and **Ionization Mechanisms**. The modes table lists your
server's own modes first, then the profiles: a profile's row edits only its
calibration and diagnostic collections.

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

Three more shapes are written in the same notation:

- `[2M+H]+` — a dimer: the count of molecules before `M`
- `[M+2H]2+`, `[M-2H]2-`, `[M+Ca]2+` — a doubly charged ion: the charge's size
  before its sign. A count on the term itself is the ion's charge, one charge for
  each species, and is written only on a mechanism's one term: `[M+3H]3+`, but
  `[M+H+H+Na]3+` for three species
- `[M+H-H2O]+` — a neutral loss: a term removed from a mechanism that adds. The
  ion's charge comes with what is added, and what is removed is neutral

The ion's polarity is the sign at the end, so a mechanism of the wrong polarity
cannot be saved. Under the field, Mascope shows the ion the typed mechanism makes:
its charge, and how many molecules it holds and what it loses where it has them.
The terms are saved in alphabetical order, so a mechanism has one spelling however
it is typed: `[M+H+CH4N2O]+` is saved as `[M+CH4N2O+H]+`, and cannot be added a
second time in the other order. **Name** is optional: what you call the mechanism
("protonation"), shown in the list beside it.

Target ions are built for all of these shapes. The composition search and
[peak assignment](../how-it-works/peak-assignment.md) read only ions of one
molecule, a single charge and no neutral loss so far: they leave a dimer, a doubly
charged ion and a neutral loss out of their search.

The older spelling is still accepted and saved in the standard one: an operation,
the formula, and the charge of the species moved rather than of the ion (`+H+`
for `[M+H]+`, `-H+` for `[M-H]-`, `+Br-` for `[M+Br]-`, a bare `+` or `-` for
electron transfer). It has no way to write the three shapes above.

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
    Any **editor** can create, edit or delete a mode of your own, and point a
    profile at other collections. An edit reaches the samples already
    processed under the mode when they are processed again: changing the
    calibrant collection flags the affected batches for re-calibration, and
    changing the mechanisms or diagnostic collection flags them for
    re-matching.

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
| Done | Every sample of the file was matched, or it has nothing to match: a blank measurement, or an acquisition of fragmentation (MS2) scans alone, which has no MS1 peaks. The detail says when the file was not calibrated, and why. |
| Needs a chemistry | Nothing named the file's chemistry: its name binds it to no ionization mode - it carries no mode's token, tokens of two modes of one polarity, or, for a file of both polarities, a token for only one of them - and no record, choice or method did either, so it was not processed. A new file has no samples yet; a file processed before keeps the samples it had, and the detail says so. Choose its chemistry (below), or fix the tokens and re-process it. |
| Calibration failed | An m/z calibration failed or is below the quality bar, so some or all of the samples were not matched. A TOF file is matched only on a verified m/z calibration, so one whose ionization mode has no calibration collection ends here too, and the detail names the missing collection. |
| Failed | Processing stopped on an error, or was interrupted by a server restart. Re-process the file. |

A file still shown in progress a day after its status was recorded has
stopped - a server worker restarted under it, say - and its status says so:
re-process the file, or choose its chemistry. Files processed before Mascope
recorded the status show none. When an Orbitrap method runs more than one
experiment in one polarity - alternating scan ranges or scan modes, or the same
scan defined twice - the detail also says that peak detection pools those scan
streams into one peak list.

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
3. Pick a chemistry for each polarity the files hold, then **Process**. The
   list offers the [profiles](#chemistry-profiles) that ship calibrants first,
   under **Profiles**, and your server's own modes under **Advanced**, with a
   profile that ships no calibrants marked *(no calibrants)*. When none of them
   is the chemistry the files were run under, **Set up a mode of your own**
   opens the *Chemistry* settings at **Advanced** and brings you back.

The files are processed under that chemistry as if their names carried its
token: calibrated, matched, and filed in the daily acquisition batches, one
named after the profile or mode. An editor of the instrument may do it. The
choice stays with the file: re-processing it later keeps it, whatever tokens
its name carries and whatever tokens are configured by then, and its status
goes on saying so: "Matched 1 sample. Bound to the profile 'Nitrate,
negative' (-) as chosen by hand." A file bound to a profile names it so in its
status whatever bound it, since a profile has no token for the file's name to
show. To change the choice, or to put the file under the mode its name would
bind it to, choose its chemistry again.

The same action gives a chemistry to a file that failed before its samples
were made, and corrects a wrong choice: a file that has samples already is
rebuilt under the modes you pick. It is not offered while a file is being
processed. Only the acquisition samples are replaced: a sample someone made
from the file in a batch of their own stays, and the file keeps its m/z
calibration rather than having it reset under that sample. If the mode you
pick calibrates the file, the new calibration marks that batch for
re-matching, as any new calibration of the file does. Re-processing still
refuses such a file.

### Fragmentation (MS2) acquisitions

Mascope detects peaks in a file's MS1 scans. Its fragmentation scans are not
turned into peaks: they are read as they were recorded, through any sample of
the file, grouped by precursor and by activation - so an acquisition that
steps the collision energy comes back one spectrum per step. In Python:

```python
from mascope_sdk import MascopeClient

mascope = MascopeClient(workspace="Acquisitions <instrument>")
batches = mascope.batches.list(dataset="2026")
samples = mascope.samples.list(batch="2026-09-07 MS2 acquisition (no chemistry)")

ms2 = mascope.samples.ms2(samples["sample_item_id"].iloc[0])
for group in ms2.get_summary()["groups"]:
    print(group["parent_peak_mz"], group["activation"], group["scan_count"])
spectra = ms2.get_averaged_centroids(by_activation=True)
windows = ms2.get_window()
```

Which sample that is depends on whether the file records MS1 scans:

- **A file with MS1 scans** gets its sample when it is bound to an
  ionization mode - by its token, its acquisition method or a choice - in
  the mode's daily batch, and its MS2 data is read through that sample. Until
  then it waits as **Needs a chemistry** with no sample, and so does its MS2
  data. A manual fragmentation experiment, recorded in the instrument's
  tuning software with no method loaded, names no method to be recognised
  by: give it a filename token, or choose its chemistry.
- **A file with no MS1 scans at all** is imported too. It has no peaks, so
  nothing is calibrated, matched or assigned, and its spectrum is empty - and
  so it needs no chemistry. When a token or its method binds it, it gets its
  sample in the mode's daily batch. Otherwise it is filed as an **MS2
  sample** with no ionization mode, in the daily batch
  `<date> MS2 acquisition (no chemistry)` of the instrument's year
  dataset, and ends
  **Done**.

`get_window()` lists each group's isolation window. It answers a
dictionary: the records are under `windows["data"]`, one per group, beside
`reason`. A record holds the MS1 centroids isolated together with the
precursor, each with its share of the window, and the precursor as the
group's MS2 spectra locate it. Pass `width=` to draw every window at one
width instead of each group's own. A file with no MS1 scans has no
centroids to list: its windows are empty, and `reason` is
`"no_ms1_scans"`.

A group's fragments can be read as formulas over the ions you name: the
precursor's ion, and any ion isolated with it. Each must lie in the group's
isolation window, and the precursor's within 5 mDa of the group's m/z (10 ppm
above m/z 500); the answer says where an ion that does not belongs. Every
energy of the precursor is read together, and each peak comes back with its
formula, the neutral loss and how it splits into molecules, or why it has
none:

```python
readings = ms2.get_readings("195.0877@hcd20.00", ["C8H11N4O2+", "C10H11O4+"])
best = readings[readings["rank"] == 1]
print(best[["group", "mz", "role", "formula", "loss", "pieces"]])
```

The [MS2 readings reference](https://github.com/ultra-trace-systems/mascope/blob/master/libraries/tools/docs/ms2.md)
has the rules and every threshold.

### Files stored as centroids

An Orbitrap method can store each scan's centroids alone, without the
profile they were found in: the scan filter then reads `c` where it reads
`p` otherwise (`FTMS + c NSI Full ms [50.0000-750.0000]`). Such a file is
processed like any other. Its peaks are the instrument's own centroids,
averaged over the file's scans, and its spectrum shows each centroid as a
line. With no profile there is no peak shape to fit, so the file's instrument
function is a Gaussian at the resolution its centroids report. A file of
this kind converted before Mascope read it this way stays a blank
measurement, with no peaks, until it is deleted and uploaded again.

!!! tip "Finding files after upload"
    The table shows one time window at a time (default: the last 7 days; **All
    time** lists every file). Use the time-range, status and polarity filters
    and the filename search at the top of the tab to locate older files.

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

To move one of your own batches into another dataset rather than copy it,
right-click the batch and choose **Cut batch**, then open the other dataset,
right-click the empty space in the *Batches* pane, and choose **Paste batch**.
The batch moves whole, at once: it keeps its samples, their matches and its
target collections. **Cut batch** is not offered on an acquisition batch, and
**Paste batch** appears only in a dataset you may edit that is neither locked
nor an acquisition dataset. A batch that is locked, or sits in a locked
dataset, only a global admin can move. If the move is refused, for example
because a target collection of the batch belongs to another workspace, the
message says why and the batch stays where it was.

To move a cut batch into a dataset that does not exist yet, right-click the
empty space in the *Datasets* pane and choose **Paste batch into a new
dataset**. Name the dataset and choose **Paste**: the dataset is created in
the open workspace and the batch moves into it. The entry appears only in a
workspace you may edit that is not an instrument workspace. If the move is
refused after the dataset was created, the dialog stays open, and **Paste**
again retries the move into that same dataset.

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
  exact token exists in **Chemistry → Advanced → Ionization Modes**, and that
  the token field is filled in (a mode with no token, a profile included,
  cannot match a filename).
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
  modes. A file whose chemistry was chosen by hand keeps it, whatever its name
  says, and a file whose name matches no token keeps the modes its samples
  have. This is only available for files not tied to a batch you created.
- **A fragmentation (MS2) file shows no MS2 data.** Its sample was made
  before v1.8.0, when a sample's time window ended with its last MS1 scan
  and so before fragment scans recorded after them. Re-process the file, or
  ask your administrator to run `db script run fix_acquisition_window`,
  which widens every such window on the server.
- **Uploads from the File Agent keep failing.** See the File Agent's
  [troubleshooting section](../instruments/index.md#troubleshooting-uploads) —
  it covers rejected tokens, HTTP 404s, and the 100 MB size limit.
