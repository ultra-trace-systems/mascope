# Export files

What leaves Mascope as a file, and what each file carries. Every export names
the records it was built from by their ids and says which build of Mascope
wrote it, so a spreadsheet that ends up beside a paper can be traced back to
the dataset, the batch and the server it came from.

Four exports are made from the app:

| Export | Where | Format |
|---|---|---|
| **Batch data** | Right-click a batch, *Download > Batch data* | A spreadsheet (`.xlsx`) of up to six sheets, below |
| **Peak data** | Right-click a batch, *Download > Peak data*; or a sample's own *Download > Peak data* | A CSV, semicolon-separated |
| **Batch ledger** | The batch peaks view's menu behind the cog, *Export ledger (CSV)* | A CSV: one row per member peak, described under [Batch peaks](../how-it-works/peak-assignment.md#batch-peaks) |
| **Deposit package** | Right-click a dataset, *Prepare for publication* | A zip archive of the whole dataset, to upload to a data repository such as Zenodo, [below](#the-deposit-package) |

Each export made from the app is written in the background, and the browser
downloads the file when it is ready. The same data is one call away in the
[Python SDK](../sdk/index.md), whose frames carry the same
[provenance](#provenance) on `df.attrs["provenance"]`.

**Additions append.** A new column, row or sheet goes after the ones an export
already had, so a reader written against an earlier export keeps finding what
it found there, where it found it.

## The batch data spreadsheet

### Batch

Label and value rows, one per line, in this order. A blank row separates the
groups.

| Row | Content |
|---|---|
| Name, Description, Type, Polarity, Dataset | The batch and its dataset |
| Target collections | The names of the collections attached to the batch, comma-separated, or `none` |
| Total Samples, Total Compounds, Total Ions, Total Isotopes | Counts of the batch's match data |
| Dataset ID, Sample batch ID, Target collection IDs | The records the export was built from |
| Instruments, Method files | Every distinct instrument and method file across the batch's samples; which is whose is on the *Samples* sheet |
| License, Authors, Keywords, Related identifiers, Funding | The dataset's descriptive metadata, as entered in the dataset dialog ([First steps](../getting-started/first-steps.md#create-a-dataset-and-a-batch)). Every row is written, empty when the dataset has none. A list is joined with `; `; an author reads `Name (ORCID), Affiliation`, a related identifier `relation: identifier`, a funder `Funder, award` |

### Samples

One row per sample.

| Column | Content |
|---|---|
| Sample name, Filename, Datetime, Sample type, TIC, Filter ID | The sample as the sample browser shows it. *Datetime* is the acquisition's start in the instrument's local time |
| Total peak intensity (cps), Match score | The sample's match summary, empty for a sample with no match |
| Sample item ID, Sample file ID | The sample's id, and the id of the raw file it was cut from |
| Datetime UTC | The acquisition's start in UTC, written without a zone (Excel stores none) |
| Instrument, Instrument type, Method file, Polarity, m/z range min, m/z range max | The acquisition context, as the raw file records it |
| Ionization mode, Ionization mode ID | The ionization mode the sample was processed under |
| Instrument function ID | The resolution function in force for the sample |
| m/z calibration | The calibration record's status: `ok` or `poor` (a fit was applied), `failed` (given up on), `unfitted` (the acquisition's own axis), or `none` (no record) |
| m/z calibration verified, m/z calibration error (ppm) | Whether the fit cleared the [quality bar](../how-it-works/calibration.md#verification), and its mean post-fit m/z error |
| Acquisition ID, Step ID, Sequence run ID, Agent ID | The identifiers of the file's [acquisition record](../instruments/acquisition-records.md), empty for a file that came with none |
| File SHA-256 | The raw file's hash as it was uploaded, where the agent reported one and the bytes received had it |
| Instrument model, Ion source, Mass analyser, Detector, Scan polarity, each followed by its *accession* column | The same acquisition context as [PSI-MS](https://github.com/HUPO-PSI/psi-ms-CV) controlled-vocabulary terms, each by its name and its accession (`MS:1000484`, ...), so another tool can read them without knowing Mascope's own names. The model is the one the instrument's raw files report, recorded on the instrument when a file of it is uploaded, so a sample whose raw file is no longer kept still has it; for an instrument with no file uploaded since Mascope began recording models, it is read from the sample's raw file instead. A value the vocabulary has no term for, such as a TOF instrument's model or a family name, leaves both cells empty; a polarity-switching sample lists its two polarity terms joined by `; ` |

### Match compounds

One row per compound matched in a sample. Written only when the batch has
match data.

| Column | Content |
|---|---|
| Sample name, Filename, Sample type | The sample |
| Compound name, Compound formula | The target compound |
| Total peak intensity (cps), Match score | The compound's match in that sample |
| Sample item ID, Target compound ID | The records behind the row |

### Match ions

One row per ion matched in a sample. Written only when the batch has match
data.

| Column | Content |
|---|---|
| Sample name, Filename, Sample type | The sample |
| Compound name, Compound formula, Ionization mechanism, Ion formula | The target ion |
| Total peak intensity (cps), Match score | The ion's match in that sample |
| Sample item ID, Target compound ID, Target ion ID | The records behind the row |

Rows carry the ids of the records they describe (sample item, target compound,
target ion), not the ids of the match rows, which a rematch replaces.

### Verifications

The batch's current [verification verdicts](../how-it-works/peak-assignment.md#verifying-assignments),
one row per judged peak, as each sample's assignment ledger shows them.
Written only when something in the batch was judged. The sheet is the table
`GET /api/peak-assignments/batch/{sample_batch_id}/verifications` answers and
the SDK's `peak_assignments.verifications_for_batch` reads, row for row and
under the same column names, so a script reads the sheet and the frame alike.

A row is either the sample's own verdict, or a batch-level verdict that
reaches the sample, with `borrowed` true. A batch-level verdict reaches a
sample when the sample's peak folded into the judged batch peak and claims
the formula and mechanism judged, and the sample has no current verdict of
its own on it. A verdict a later one replaced is not listed.

| Column | Content |
|---|---|
| `sample_item_id`, `sample_item_name`, `sample_batch_id`, `filename`, `datetime`, `datetime_utc` | The sample. `datetime` is the acquisition's start in the instrument's local time, `datetime_utc` in UTC |
| `sample_peak_id`, `sample_peak_mz` | The judged peak and its m/z |
| `assigned_formula`, `ionization_mechanism_id` | The claim judged |
| `verdict`, `evidence_level`, `note`, `verified_by`, `verified_utc` | The verdict, the evidence it rests on, the note, the user's id and when it was recorded |
| `superseded_utc` | Empty: only current verdicts are listed |
| `context` | On a batch-level verdict, the batch peak's consensus as the user saw it, as JSON |
| `fit_score`, `evidence`, `p_correct` | The assignment's score when the sample's own verdict was recorded; empty on a borrowed one |
| `borrowed` | Whether the verdict is a batch-level one reaching the sample |
| `assignment_verification_id`, `peak_assignment_id`, `peak_assignment_run_id` | The records behind the sample's own verdict |
| `batch_peak_verification_id`, `batch_peak_id`, `stale` | The records behind a borrowed verdict; `stale` when the batch peak's consensus has since left the formula judged |

Times are in UTC and written without a zone, which is all Excel stores.

### Provenance sheet

The last sheet. The [provenance block](#provenance) of the build that wrote
the file, one key/value row per entry with nested keys joined by a dot
(`produced_with.mascope_version`), and `inputs` naming the dataset, the batch
and the target collections.

## The peak data CSV

Semicolon-separated, one row per peak. The batch export writes every sample's
detected peaks with each peak's stored sum over the file's scans; the sample
export writes every peak's intensity in every scan of the sample's time
window, so a sample file of many scans gives many rows per peak.

| Column | Content |
|---|---|
| `mz`, `intensity`, `unit` | The peak. The unit is `counts` for an Orbitrap and `ions` for a TOF; the batch export sums an Orbitrap peak's height and a TOF peak's area |
| `datetime`, `datetime_utc`, `tic` | In the sample export, the scan's time in the instrument's local zone and in UTC, and the scan's total ion current; in the batch export, the sample's acquisition start and its TIC |
| `sample_batch_name`, `sample_item_name`, `filename`, `filter_id`, `sample_item_type`, `instrument` | The sample |
| `sample_file_id`, `sample_item_id`, `sample_batch_id`, `dataset_id` | The records behind the row |

The CSVs carry no provenance block: a constant column on every row would
multiply the size of a per-scan export for a value the spreadsheet and the API
already give.

## The deposit package

A dataset as one zip archive, for a data repository such as
[Zenodo](https://zenodo.org). Uploaded there, it tells whoever downloads it
what was measured, how, under what licence, by whom, and with which software.
Mascope writes the archive. The repository keeps it and mints its DOI.

Right-click the dataset and choose *Prepare for publication*. The dialog
shows what the archive will hold: the dataset's metadata record as it stands,
its batches and samples, its raw files and their size, its target collections
and its ionization modes. Above them it lists what the dataset still lacks for
a deposit:

- **No licence.** Without one, nobody but the authors may reuse the data.
- **No authors.** A repository cites a deposit by its creators.
- **No description.** A repository shows it as what the deposit is.
- **No samples.** The package would describe no measurement.

*Edit dataset* opens the dataset dialog over it, where the licence, authors,
description, keywords, related identifiers and funding are filled in. They are
what the archive's README and the repository's record say about the data. When
the dataset dialog closes, the list is read again. *Build package* writes the
archive in the background, gaps or not, and the browser downloads it when it is
ready. The notification gives its size, and counts the sample files with no
raw file kept and the raw files changed since upload. Preparing a dataset
takes the editor role in its workspace.

Whoever operates the server can write the same archive with one command
([Packaging a dataset for deposit](https://github.com/ultra-trace-systems/mascope/blob/master/docs/maintaining.md#packaging-a-dataset-for-deposit)).
The app and the command run the same builder, so for the same dataset they
write the same archive.

Unzipped, it is one folder, `mascope-package_<dataset id>_<dataset name>`:

| File | Content |
|---|---|
| `README.md` | The dataset for a person: its name, description, licence, authors, keywords, related identifiers and funding; the instruments and what they measured, in PSI-MS terms; the ionization modes, batches and target collections; the software versions; and what each file holds |
| `DATA_LICENSE` | The dataset's licence, with a link for a Creative Commons one. A dataset without a licence says so: nobody but its authors may reuse it |
| `raw/` | Each sample file's raw file, `.raw` or `.h5`, as it was uploaded. Its header is unchanged, and may name the instrument and the computer that acquired it |
| `raw/<file>.mascope.json` | Beside a raw file, the [acquisition record](../instruments/acquisition-records.md) the instrument's control program sent with it, where it sent one. Its `machine`, the computer's host name and Windows machine GUID, is left out; Mascope keeps the record whole |
| `batches/` | Each batch's [batch data spreadsheet](#the-batch-data-spreadsheet), the same file *Download > Batch data* gives |
| `target_collections/` | Each collection a batch was matched against, or an ionization mode calibrates with: its compounds with their CAS number, InChIKey, InChI and SMILES where recorded, and the ions formed from them |
| `instrument_functions.json` | The peak shape and resolution each raw file was processed with |
| `manifest.json` | All of the above for a program, below |
| `ro-crate-metadata.json` | The same as an [RO-Crate](#the-ro-crate), the format data repositories and RO-Crate viewers read |
| `SHA256SUMS` | The SHA-256 of every other file. In the folder, `sha256sum -c SHA256SUMS` checks them all |

`manifest.json` is laid out as the demo dataset's manifest is. Its `schema`
is `mascope-package/1`, and its keys are a contract in the way the
[provenance](#provenance) block's are:

| Key | Content |
|---|---|
| `dataset` | The dataset's metadata record, under the names the API gives the fields |
| `measurement` | The instruments, instrument types and models, and the first and last acquisition time, in UTC |
| `produced_with` | The provenance block's versions, and the raw file reader's |
| `provenance` | The [provenance](#provenance) block, its `inputs` naming the dataset and its batches |
| `raw` | One entry per sample file: `name`, `sha256` and `bytes` of its raw file; its acquisition context, m/z calibration verdict and PSI-MS terms; the SHA-256 recorded when it was uploaded; and its acquisition record's identifiers |
| `batches` | One entry per batch: its samples, with their time windows and ionization modes, its target collections and its spreadsheet |
| `target_collections` | One entry per collection: what it was used as (`matched`, `calibration`, `diagnostic`), its counts and its file |
| `ionization_modes` | The modes the samples were processed under, with their ionization mechanisms and collections |

Every record carries its id. On a deployment with an id, the dataset, its
batches, samples and sample files, and the collections with their compounds
and ions carry their global id too. Ionization modes, their mechanisms and the
instrument functions have none. In `provenance`, `inputs` gives the dataset's
global id; the batches' are in `batches`. Every file the manifest names
carries its SHA-256 and size.

A sample file whose raw file Mascope no longer keeps, only the data converted
from it, is listed with `included` set to `false`, and the README names it.
A raw file whose SHA-256 differs from the one recorded at its upload is packed
as it is, both values in its entry.

After the repository mints a DOI, add it to the dataset's related
identifiers. A package written again then points at the deposit.

### The RO-Crate

`ro-crate-metadata.json` describes the package as an
[RO-Crate 1.2](https://w3id.org/ro/crate/1.2): JSON-LD in
[schema.org](https://schema.org) terms. Data repositories, harvesters and
generic RO-Crate viewers read it without knowing Mascope. It says nothing
`manifest.json` does not, and the manifest keeps its keys.

| In the crate | What it holds |
|---|---|
| The root `Dataset` | The dataset's name, description, licence, keywords, global id, and the time its files were acquired in |
| `author` | Each author as a `Person`, with their ORCID iD as the `@id` where it is recorded, and their affiliation |
| `funding`, `funder` | Each funding entry as a `Grant`, its award the `identifier`, and the funder as an `Organization` |
| `citation`, `isBasedOn`, `isPartOf` | The related identifiers, a DOI or URL as the `@id`. `IsDerivedFrom`, `IsNewVersionOf` and `IsVersionOf` become `isBasedOn`, `IsPartOf` becomes `isPartOf`, and any other relation `citation`. Each one's description keeps the relation as written. An identifier given under two relations is one work that names both, and one that is Mascope's repository refers to Mascope itself |
| `measurementTechnique` | The PSI-MS terms of the instrument models, ion sources, mass analysers and detectors |
| `hasPart` | Every file, with its SHA-256 (`sha256`) and size, and each folder |
| `mentions` | The action that wrote the package: when, and with which Mascope and raw-file reader versions. Also a work cited by an identifier that is neither a DOI nor a URL: RO-Crate cites by URI alone |

A licence the dataset dialog suggests is named by its SPDX entry. Any other
licence, or none, is named as it was recorded: RO-Crate asks for a licence,
and the crate does not guess one. `SHA256SUMS` has its size in the crate but
no checksum, since it holds the crate's own.

## Provenance

Every spreadsheet, and every frame the SDK's loaders return, carries the same
block, which says which software on which deployment produced the result and
from which records:

```json
{
  "provenance_version": 1,
  "generated_utc": "2026-10-01T12:00:00Z",
  "deployment_id": "k3J9xQ2mP0aB7cD1",
  "produced_with": {
    "mascope_version": "v1.13.0",
    "match_score_version": 1,
    "peak_assignment_engine_version": "0.5.0"
  },
  "inputs": {
    "dataset_id": "...",
    "dataset_global_id": "k3J9xQ2mP0aB7cD1:dataset:...",
    "sample_batch_id": "...",
    "sample_batch_global_id": "k3J9xQ2mP0aB7cD1:sample_batch:...",
    "target_collection_ids": ["..."]
  }
}
```

- `deployment_id` names the server. Record ids are unique within one
  deployment only, so this is what tells two deployments' exports apart. A
  deployment without an id reports `null`; whoever operates the server sets or
  reads it ([Deployment identity](https://github.com/ultra-trace-systems/mascope/blob/master/docs/maintaining.md#deployment-identity)).
- `produced_with` describes the server as it runs when the file is written:
  the Mascope version, the match-score version in force, and the peak
  assignment engine's version, whether or not peak assignment is on. A result
  stored earlier was computed by whichever build ran then; where a record
  carries provenance of its own, as an assignment run does, that record is the
  authority.
- `inputs` names the records the export was built from. Beside each id, a
  deployment with an id writes the **global id**
  `<deployment_id>:<entity>:<local_id>`, which is unambiguous across
  deployments.
- The keys are a contract: a key that has shipped is never renamed or given a
  new meaning. A key may be added without changing `provenance_version`,
  which moves only when the meaning of an existing key does.

The same block, without `inputs`, is `GET /api/provenance`, which the SDK
reads and stamps onto the frames it returns
([SDK readme, Provenance](https://github.com/ultra-trace-systems/mascope/blob/master/libraries/sdk/README.md#provenance)).
