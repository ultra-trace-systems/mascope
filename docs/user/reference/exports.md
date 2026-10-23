# Export files

What leaves Mascope as a file, and what each file carries. Every export names
the records it was built from by their ids and says which build of Mascope
wrote it, so a spreadsheet that ends up beside a paper can be traced back to
the dataset, the batch and the server it came from.

Three exports are made from the app:

| Export | Where | Format |
|---|---|---|
| **Batch data** | Right-click a batch, *Download > Batch data* | A spreadsheet (`.xlsx`) of five sheets, below |
| **Peak data** | Right-click a batch, *Download > Peak data*; or a sample's own *Download > Peak data* | A CSV, semicolon-separated |
| **Batch ledger** | The batch peaks view's menu behind the cog, *Export ledger (CSV)* | A CSV: one row per member peak, described under [Batch peaks](../how-it-works/peak-assignment.md#batch-peaks) |

A fourth export covers a whole dataset: the [deposit package](#the-deposit-package),
one archive to upload to a data repository such as Zenodo. It is not made from
the app yet. Whoever operates the server writes it with one command.

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
| Instrument model, Ion source, Mass analyser, Detector, Scan polarity, each followed by its *accession* column | The same acquisition context as [PSI-MS](https://github.com/HUPO-PSI/psi-ms-CV) controlled-vocabulary terms, each by its name and its accession (`MS:1000484`, ...), so another tool can read them without knowing Mascope's own names. A value the vocabulary has no term for, such as a TOF instrument's model or a family name, leaves both cells empty; a polarity-switching sample lists its two polarity terms joined by `; ` |

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

### Provenance sheet

The [provenance block](#provenance) of the build that wrote the file, one
key/value row per entry with nested keys joined by a dot
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

Before asking for one, fill in the dataset's licence, authors, keywords,
related identifiers and funding in the dataset dialog. They are what the
archive's README and the repository's record say about the data. The archive
is written by whoever operates the server
([Packaging a dataset for deposit](https://github.com/ultra-trace-systems/mascope/blob/master/docs/maintaining.md#packaging-a-dataset-for-deposit)).

Unzipped, it is one folder, `mascope-package_<dataset id>_<dataset name>`:

| File | Content |
|---|---|
| `README.md` | The dataset for a person: its name, description, licence, authors, keywords, related identifiers and funding; the instruments and what they measured, in PSI-MS terms; the ionization modes, batches and target collections; the software versions; and what each file holds |
| `DATA_LICENSE` | The dataset's licence, with a link for a Creative Commons one. A dataset without a licence says so: nobody but its authors may reuse it |
| `raw/` | Each sample file's raw file, `.raw` or `.h5`, as it was uploaded |
| `raw/<file>.mascope.json` | Beside a raw file, the [acquisition record](../instruments/acquisition-records.md) the instrument's control program sent with it, where it sent one |
| `batches/` | Each batch's [batch data spreadsheet](#the-batch-data-spreadsheet), the same file *Download > Batch data* gives |
| `target_collections/` | Each collection a batch was matched against, or an ionization mode calibrates with: its compounds with their CAS number, InChIKey, InChI and SMILES where recorded, and the ions formed from them |
| `instrument_functions.json` | The peak shape and resolution each raw file was processed with |
| `manifest.json` | All of the above for a program, below |
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

Every record carries its id, and on a deployment with an id its global id
too. Every file the manifest names carries its SHA-256 and size.

A sample file whose raw file Mascope no longer keeps, only the data converted
from it, is listed with `included` set to `false`, and the README names it.
A raw file whose SHA-256 differs from the one recorded at its upload is packed
as it is, both values in its entry.

After the repository mints a DOI, add it to the dataset's related
identifiers. A package written again then points at the deposit.

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
