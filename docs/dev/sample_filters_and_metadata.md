`ULTRA TRACE MASCOPE - DESIGN DOC - SAMPLE FILTERS, REFRESHABLE BATCHES AND ACQUISITION METADATA`

# Sample filters, refreshable batches and acquisition metadata: design

Status: **design, nothing built** (2026-10-05). The decisions in section 3 were
settled in design review on that date. Section 9 lists what is open, each item
with a recommendation. Code anchors were read on `develop` on the same date
and are named where a claim rests on them.

## Picking this up

The feature, as a person meets it:

- **Find samples by filter instead of by folder.** One finder over the
  acquisition record of every instrument the person can read: time range,
  instrument, chemistry, polarity, m/z range, and later any metadata field.
  It replaces walking `Acquisitions <instrument>` -> year dataset -> daily
  batch, one instrument and one day at a time.
- **Make a batch from the selection.** The batch lives in the person's own
  workspace and is an ordinary analysis batch. It remembers the filter it was
  built from and can be refreshed: the filter is run again and the samples
  that have arrived since are appended.
- **Stamp the acquisition record with typed metadata.** Per deployment (an
  instrument at a site for a period) or per sample. Filters see it, and every
  batch built from those samples sees it.
- **Say what was sampled, and let that pick the reference lists.** A sample
  tagged "boreal forest" is read against the lists that belong to that
  chemistry, without the person choosing lists.

The design in one paragraph. A **filter** is a small versioned JSON predicate
over acquisition samples; the API, the SDK, the finder's URL and a batch's
stored recipe all use the same one. A batch built from a filter keeps the
filter as its **recipe** and each of its samples keeps an **origin key**, so a
**refresh** can tell what is new. **Acquisition metadata** is keyed to the raw
file rather than to a sample row, with **deployments** supplying defaults by
instrument and time, so it survives re-processing and is shared by every copy.
An **environment** is a named bundle of a chemistry context and chemistry
regimes; reference lists already declare their regimes, and the two meet in
Stage A.

| Phase | Content | State |
|---|---|---|
| 1 | Finder on built-in fields, batch from a selection, origin key, refresh | open |
| 2 | Metadata field registry, acquisition metadata, per-sample stamping, metadata in the filter | open |
| 3 | Environments select reference lists and the chemistry context | open |
| 4 | Deployments | open |
| 5 | Auxiliary time series (meteorology, other sensors) | open; shape only, section 4.7 |

Related designs: `ingest_routing_and_splitting.md` (the part a sample is cut
from, chemistry profiles as the unit, declarations from the instrument side),
`chemistry_profiles.md` (reagent profile and chemistry context),
`reference_data_authoring.md` (the reference lists and their headers),
`peak_assignment_batch.md` (batch peaks), `ui_state_persistence.md` (the
location a URL carries). Section 10 says how this design touches each.

---

## 1. Why

Data is organised as workspace -> dataset -> batch -> sample, and the UI opens
one batch at a time. A batch is typically one day of one chemistry on one
instrument. Anything more flexible - a longer time range, two chemistries side
by side, "every sample from the forest site" - is done in Python through the
SDK, whose own selection is by dataset and by name patterns on batches and
samples (`MascopeClient.load_peaks(dataset, batches, samples)`,
[client.py](../../libraries/sdk/src/mascope_sdk/client.py)).

Building an analysis batch today means
([import-files.md](../user/guides/import-files.md)): switch to the
`Acquisitions <instrument>` workspace, open the year dataset, copy a daily
acquisition batch or some of its samples, switch back, paste. For a month of
data that is thirty copies per chemistry, and per instrument.

Sample metadata is too weak to select on (section 2.5), and nothing on a
sample says what was sampled, so the engine cannot tell which reference lists
apply (section 2.6).

---

## 2. Current state

### 2.1 A batch does three jobs

| Job | How | Fate in this design |
|---|---|---|
| Selection | `sample_item.sample_batch_id`: a sample is in exactly one batch | assisted by filters |
| Analysis context | target collections attach to the batch (`TargetCollectionInSampleBatch`); batch peaks are frozen anchors per (batch, ionization mode) (`BatchPeak`); the batch carries the `rematch` / `recalibrate` status | unchanged |
| Ownership | the workspace -> dataset -> batch -> sample permission chain, locks, delete cascade | unchanged |

([models.py](../../server/backend/src/mascope_backend/db/models.py))

### 2.2 The acquisition record is already a materialised filter

Acquisition datasets are created per (workspace, instrument, year) and daily
acquisition batches per (dataset, day, ionization mode), with polarity in the
batch's natural key - the partial unique indexes
`uq_dataset_acquisition_natural_key` and
`uq_sample_batch_acquisition_natural_key` say so. A person who opens a daily
acquisition batch is reading the result of "instrument X, day D, chemistry M".

The folders are about to multiply. The ingest design's next cut
(`ingest_routing_and_splitting.md` section 4.5) makes one acquisition item
per MS1 stream of a file and gathers a day's items into batches by what their
experiment measured, so one chemistry on one day becomes one batch per scan
range. That is right for the batch ledger, whose anchors assume comparable
spectra, and it is one more level to walk by hand.

### 2.3 What a copy is

`copy_sample_items`
([sample_items_controller.py](../../server/backend/src/mascope_backend/api/controllers/sample/items/sample_items_controller.py))
creates a new `sample_item` row in the target batch that references the same
`sample_file`. It carries the source's `sample_file_id`, `polarity`,
`ionization_mode_id`, `t0`, `t1`, `tic`, `filter_id` and
`sample_item_attributes`; an `ACQUISITION` sample becomes `UNKNOWN`.

- Match results are not carried across batches: the target batch is set to
  `rematch`. Only a whole-batch copy, or a copy within one batch, copies them.
- The copy holds no reference to the sample it came from.
- `t0` and `t1` are editable on the copy (`SampleItemUpdate`).
- The ingest design gives `sample_item` a nullable `stream_id`, carried by
  copy and move, where NULL keeps today's meaning: the scans of the item's
  polarity (its sections 3.2 and 4.4). Not built yet.
- Acquisition samples are locked (`ACQUISITION_AUTO_LOCK`), and a file's
  acquisition samples are recreated when the file is re-processed, so their
  ids are not stable. A sample someone made from the file in a batch of their
  own stays.

### 2.4 What can be queried today

- **`GET /api/samples`** declares `instrument`, `datetime_min`, `datetime_max`,
  `polarity` and `sample_item_type` parameters, but the route refuses a call
  that names neither a batch nor a sample
  ([samples_routes.py](../../server/backend/src/mascope_backend/api/routes/samples/samples_routes.py)),
  so it is not a cross-batch query. Its time filter is written with
  `julianday`, a SQLite function; no Postgres definition of it was found in
  the repository and the path was not exercised for this document. The finder
  needs a route of its own rather than an extension of this one.
- **`sample_view`** joins `sample_item` to `sample_file`
  ([views.py](../../server/backend/src/mascope_backend/db/views.py)); it is the
  natural thing to query.
- **The raw-files listing** already filters across instruments by time,
  instrument, filename and processing status (`GetSampleFilesQueryParams`),
  under the rule that a person sees the files of instruments whose workspace
  they belong to ([authorization.md](../authorization.md)). The finder's
  access rule follows it.
- **The location** a URL or a reload carries holds a single batch
  ([location/schema.js](../../server/frontend/src/lib/location/schema.js)).

Facts a filter wants that are not columns:

| Fact | Where it is | Gap |
|---|---|---|
| m/z range | `sample_file.range`, a JSON list `[min, max]` | not comparable in SQL without a cast per row; no index. Once a file is split into streams the range that matters is the stream's, which the ingest design keeps in the stream's signature |
| chemistry | `sample_item.ionization_mode_id` | a mode row is not a chemistry: a site replaces its mode rows over time and two instruments name the same chemistry differently. `method_keys.chemistry_key` (the mode's mechanism set) is the comparable identity, computed in Python |
| instrument | `sample_file.instrument` | recorded with inconsistent case; `method_keys.instrument_key` folds it |

### 2.5 Metadata today

- `sample_item.sample_item_attributes` is a JSON object of free-form labels.
  An attribute template is a list of labels with a default and a required
  flag, its values are strings (`TemplateField.value`), and its only allowed
  type is `sample_item`
  ([attribute_template_pydantic_model.py](../../server/backend/src/mascope_backend/api/models/attribute_templates/attribute_template_pydantic_model.py)).
- Nothing filters on attributes server-side and there is no index. The batch
  overview offers them as x-axis fields and reads every one as a string
  ([ChartBatchOverview/data.js](../../server/frontend/src/lib/charts/ChartBatchOverview/data.js)).
- Attributes live on the sample row. A stamp on a copy is local to that
  batch, and a stamp on an acquisition sample would be lost when the file is
  re-processed.
- The paste-a-table import maps extra columns into attributes
  ([DialogBatchImport/generic.js](../../server/frontend/src/lib/dialogs/DialogBatchImport/generic.js)),
  for samples being created in a batch of one's own.

### 2.6 Reference lists and the chemistry context

- The shipped lists declare where they apply. The monoterpene list's header
  says `"applies_to_contexts": ["monoterpene_ox", "biogenic_soa"]`, and the
  contaminant and cyclic-siloxane lists say `"always_active": true`. Both keys
  are read and nothing acts on them: Stage A matches every active source in
  every context ([reference_data_authoring.md](reference_data_authoring.md)
  section 6; [known.py](../../libraries/reference/src/mascope_reference/known.py)
  selects on `is_active` and polarity alone). The source row does not record
  them.
- The chemistry context (`ambient-air`, `chamber`, `indoor-air`,
  `object-headspace`, `combustion`, `water`, `food`, `uronium`, `none`) is a
  per-run setting. `auto`, the default, resolves to the reagent profile's own
  default context
  ([peak_assignments/profiles.py](../../server/backend/src/mascope_backend/api/new/peak_assignments/profiles.py),
  `resolve_profile`). The ion source cannot tell a forest from a coast.
- The two vocabularies do not meet: `monoterpene_ox` is not the name of any
  shipped context, so nothing on the sample side can speak the lists'
  language.
- The assign launcher already previews what a run would search under, grouped
  by chemistry with sample counts (`preview_resolutions`).

---

## 3. Decisions

Settled in design review, 2026-10-05.

| # | Decision |
|---|---|
| 1 | Filters select from the **acquisition record**. The batch stays the unit of analysis and of ownership: the workflow is filter -> selection -> batch in a workspace of one's own. A filter is not a view that owns results. |
| 2 | A batch built from a filter is **refreshable**. Refresh is asked for by a person and only appends. |
| 3 | Metadata that filters see lives on the **acquisition side**, and **editors of the instrument workspace** may write it. |
| 4 | Stamping works at **two levels**, a deployment and a single sample, and both are first-class: field campaigns and one-off laboratory samples are both use cases. A per-sample value replaces the deployment's value for that field. |
| 5 | What was sampled is recorded as **environment tags**, and the tags select the reference lists through a mapping; a person does not pick lists. An untagged sample is read as today, against every list. |
| 6 | A batch that mixes environments reads the **union** of its samples' lists. Building a sensible batch is the person's responsibility; the app shows what the mix is and does not refuse it. |
| 7 | Importing **auxiliary data** from other instruments and sensors (meteorology) is a long-term goal. The metadata model must leave room for it; it is not designed here beyond its shape. |
| 8 | Acquisition metadata is keyed to the **raw file**, not to a sample row, so it survives re-processing and leaves the locked acquisition samples untouched. |
| 9 | A copy **reads acquisition metadata through**, live. Nothing is snapshotted at copy time. |
| 10 | The **origin key leaves out the ionization mode**. A copy whose file's chemistry is corrected afterwards stays as it is, and the refresh preview lists such copies. |

Not chosen, and why:

- **A filter as a live view** (open a query as if it were a batch, with no
  copy). Per-sample results hang off the copied `sample_item`, target
  collections attach to a batch, and batch-peak anchors are per batch, so a
  view would have nothing to read for the targeted overview or the batch
  ledger without re-keying results by (sample, analysis context). That is a
  large refactor for a narrower gain than decision 1 gives. Its cost is named
  in section 8: there is no quick look across a long range without making a
  batch.
- **Direct list selection per batch.** It asks a person for knowledge the
  list curators have and they do not.

---

## 4. The model

### 4.1 The filter

One JSON object, versioned, used verbatim by the search route, the SDK, the
finder's shareable state and a batch's stored recipe.

```json
{
  "v": 1,
  "datetime_utc": {"from": "2026-06-01T00:00:00Z", "to": null},
  "instrument": ["instrument-a", "instrument-b"],
  "chemistry": ["<chemistry key>"],
  "polarity": ["-"],
  "mz_range": {"covers": [100, 600]},
  "metadata": [
    {"field": "environment", "op": "any_of", "value": ["boreal-forest"]},
    {"field": "inlet_height_m", "op": "between", "value": [10, 40]}
  ]
}
```

Semantics: every clause must hold (AND); a list inside a clause is any-of
(OR). No nesting in version 1 - it keeps the chips, the URL and the facet
counts simple, and `v` leaves room for it.

| Clause | Reads | Notes |
|---|---|---|
| `datetime_utc` | `sample_file.datetime_utc` | half-open `[from, to)`; `to: null` is "and onwards", which is what makes a refresh useful. Relative ranges ("last 30 days") are left out: with an append-only refresh they would only ever accumulate |
| `instrument` | `sample_file.instrument`, folded as `instrument_key` does | |
| `chemistry` | the mode's chemistry key | not `ionization_mode_id` (section 2.4). Resolved to the set of mode ids sharing the key before the query, so no schema change. Once chemistry profiles are the unit a person sees (ingest design, phase 8), the clause names profiles |
| `polarity` | `sample_item.polarity` | |
| `mz_range` | the sample's m/z bounds: its file's today, its stream's once files are split | `covers: [a, b]` (the sample's range contains it) or `overlaps: [a, b]`. Needs numeric columns (section 6) |
| `instrument_type` | `sample_file.instrument_type` | `orbi` / `tof` |
| `method` | `method_keys.method_key(sample_file.method_file)` | groups "similar hardware settings" where the method name varies |
| `metadata` | effective acquisition metadata (section 4.4) | phase 2; operators by field type: `eq`, `any_of`, `between`, `exists` |

Facets: a second route returns counts per value for each clause under the
current filter (samples per instrument, per chemistry, per month, per rounded
m/z range, per value of an enum or tag field). The facet counts are what make
a filter discoverable - a person sees what exists before typing anything -
and the rounded m/z-range facet is how "the 50-750 method" is picked without
knowing its bounds.

### 4.2 The finder

**Scope.** Samples in `ACQUISITION` batches of instrument workspaces in which
the caller is at least guest, with the global-role and superuser bypasses the
workspace checks already have. Nothing in another person's workspace is ever
returned. Samples a person cut by hand into a batch of their own (custom time
windows) are not in the acquisition record and are not found; that path stays
as it is.

**Routes** (names tentative): `POST /api/samples/search` taking a filter, a
sort and a page, and `POST /api/samples/facets` taking a filter. Both
token-accessible, so the SDK uses the same ones.

**Surface.** A finder pane with the filter as chips, a time histogram to
brush a range on, the matching count always visible, and a result table. Two
actions: **Create batch from this filter** and **Add to the open batch**.
Checking individual rows and copying them is today's copy path and stays.

Where the pane lives is open (section 9). The finder's filter can ride in the
shareable location later; the batch it produces is an ordinary batch, so the
location chain itself does not change.

### 4.3 A batch from a selection

**Create.** The server evaluates the filter (the client does not ship
thousands of ids), creates an `ANALYSIS` batch in the chosen dataset with the
chosen target collections, and copies the matching samples through the
existing `copy_sample_items` path. The batch then needs matching against its
own collections, exactly as a pasted selection does today. The copy is
already a background task with progress notifications; a count is shown
before it starts and a large one asks for confirmation, as the SDK's
`confirm_above` does.

**The recipe.** `sample_batch.source_filter` holds the filter, its grammar
version, who built it, and when it was last refreshed. A batch without one is
a batch as today. The recipe can be edited afterwards; an edit changes what
the next refresh adds and removes nothing.

**The origin key.** Each copy records which acquisition part it came from:
`sample_item.origin_key`, a digest of the part's natural key - the file, the
stream's key and epoch within the file, `t0` and `t1` - taken from the source
at copy time and never edited. A sample with no stream uses its polarity as
the stream key, which is what the ingest design says a single-stream file's
key is, so the key means the same before and after files are split. It is the
natural key rather than the source's `sample_item_id` because acquisition
samples are recreated on re-processing (section 2.3), and it is a column of
its own rather than a comparison of the copy's fields because `t0` and `t1`
are editable on a copy. The ionization mode is deliberately not part of it
(decision 10): the key says which measurement a copy is, not how it was read.

**Refresh.** Run the recipe; drop every result whose origin key the batch
already holds or has excluded; show what is left ("14 new samples, 2026-09-28
to 2026-10-04"); on confirmation, append them through the same copy path and
flag the batch for matching. Batch-peak anchors are frozen and append-only
under arrival by design (`peak_assignment_batch.md` section 2), so appending
is the case that layer was built for. Whether the copy path folds new members
into the batch peaks on its own, or a refresh must ask for the fold, is to be
read off the code in phase 1.

**Exclusions.** The documented habit is to copy a batch and delete what does
not belong. A refresh must not bring those samples back, so deleting a sample
that has an origin key records the key in the batch's exclusion list. The
refresh preview says how many are excluded and offers to re-include them.

**What refresh does not do.** It never removes a sample, never re-copies one
that is present, and never changes a copy because its acquisition record
changed. A file whose chemistry was corrected after the copy was made keeps
its old copy in the batch, under the chemistry it was copied with. The
refresh preview lists the copies whose acquisition record has changed since,
so a person can replace them (decision 10).

**Mixed chemistries.** An analysis batch already holds both polarities
(`ANALYSIS_POLARITY`) and batch peaks are partitioned per ionization mode, so
a batch built across chemistries is structurally sound. Across chemistries
the m/z axes differ; where a view joins them, the key is the neutral formula.

### 4.4 Acquisition metadata

**Two tiers.**

| Tier | What it describes | Lives | Written by | Seen by |
|---|---|---|---|---|
| Acquisition metadata | what was physically measured: site, environment, inlet, campaign, the sample's source | keyed to the raw file (below) | editors of the instrument workspace | the finder, and every batch holding a sample of that file |
| Batch annotations | a sample's role in one study: stage, group, replicate | `sample_item.sample_item_attributes`, as today | editors of the batch's workspace | that batch |

**The field registry.** `metadata_field`: a stable key, a label, a type
(`text`, `number`, `datetime`, `enum`, `tags`), a unit, the allowed values of
an enum or tag field, a description, and a system flag for the fields Mascope
ships (`environment`). The registry is one per deployment of Mascope, not per
workspace: a filter across instruments needs "site" to be one field. An
attribute template becomes a named selection of registry fields to show in a
form; the free-form labels people use today keep working as batch annotations
and are not migrated.

**Storage.** `acquisition_metadata`: one row per (`sample_file_id`,
`part_key`), where an empty part key means the whole file, holding a JSONB
object of field key -> value, with who wrote it and when. It is keyed to the
file, not to a sample row (decision 8), for three reasons:

1. it survives re-processing, which recreates the acquisition samples;
2. a file acquired in two polarities yields two samples that were physically
   the same measurement;
3. writing it does not touch a locked `sample_item`.

An empty part key is the common case and stays so when files are split into
streams: the scan ranges of one file sampled the same thing. A part key names
one part of a file once recipes cut time windows (the ingest design's phase
6), and it is where that design's window labels would land, which today it
routes to `sample_item_attributes`.

**Effective metadata** for a sample is the merge of three sources, most
specific first, per field:

1. the part's row,
2. the file's row,
3. the deployment covering the file's instrument and start time (section 4.5).

A JSON null in a more specific source clears an inherited value - a blank
inside a forest campaign has no environment, rather than the forest's.

**Read-through, not snapshot** (decision 9). A copy references the same file
and part, so it reads the same acquisition metadata, live. Correcting a site
name reaches every batch at once, and nothing is duplicated at copy time. A
snapshot would go stale and would need a sync of its own. Reproducibility is
kept where it is kept today: a run snapshots what it resolved (section 4.6).

**Querying.** The finder narrows by time and instrument on indexed columns
first, then filters on the merged object. If facet counts on metadata get
slow, the effective object is materialised per file and maintained when a
deployment or a row changes; that is an implementation choice, not a model
change.

**Stamping routes**, all writing the same rows:

- **Bulk edit**: select samples in the finder, set a field. The interaction
  mirrors *Choose chemistry* on raw files.
- **Table import**: paste a table keyed by filename, extra columns mapped to
  registry fields. It is the acquisition-side sibling of the existing batch
  import, and the laboratory case's main route - an autosampler sequence
  already holds one row of facts per file.
- **Deployment**: section 4.5.
- **From the instrument side**, later: the ingest design's phase 7
  (declarations from the instrument side) is where an agent would declare
  per-file facts as it uploads.

In a batch of one's own the shared fields appear beside the batch's
annotations, typed, and editable only by someone who may write them. That
also gives the overview chart real numeric and datetime axes for those
fields.

### 4.5 Deployments

A **deployment** says: this instrument was at this place, measuring this,
from then until then. `instrument_deployment`: instrument (folded), a name, a
start, an end that may be open, and a JSONB object of field values. Two
deployments of one instrument may not overlap, so inheritance has one answer.

- A file inherits from the deployment covering its `datetime_utc`. Files that
  arrive later inherit on arrival with no one typing: this is ingest-time
  stamping for a campaign.
- Editing a deployment re-reads every sample in its period, because the
  values are read through, not copied.
- Editors of the instrument workspace manage them (decision 3).
- A per-sample value replaces the deployment's for that field (decision 4).

It is phase 4, after per-sample stamping, because per-sample stamping alone
covers both use cases - laboriously for a campaign - while a deployment alone
does not cover the laboratory.

### 4.6 Environments and the reference lists

**The mapping.** `environment` is a shipped `tags` field. Each value is a row
of an editable registry:

| Column | Example |
|---|---|
| key, label | `boreal-forest`, "Boreal forest" |
| chemistry context | `ambient-air` (one of the shipped contexts, or none) |
| regimes | `monoterpene_ox`, `biogenic_soa` |

Three vocabularies, each spoken by the party that knows it: people tag
samples with environments; list curators declare regimes
(`applies_to_contexts`, unchanged); the registry joins them. A new
environment is one row and no list is edited. Mascope ships a starting set;
which environments and which regimes is the list curators' call and is not
fixed here.

**Selecting lists.** `reference_source` gains the two header facts it does
not record today (`applies_to_contexts`, `always_active`), written by the
seed and by `reference sync`. A source is read for a set of regimes when any
of these holds:

1. no environment is in play (an untagged sample: today's behaviour);
2. the source is always active;
3. the source declares no regimes (a database mirror, a hand-loaded CSV);
4. its regimes intersect the set.

So only a list that declares regimes can be left out, and only for samples
someone tagged. A missing tag cannot lose an identification.

**In a batch** the set is the union over the batch's samples (decision 6),
and every sample of the batch is read under it, so one batch has one reading
of the lists. One untagged sample makes the union everything. The assign
launcher's preview (`preview_resolutions`) gains the environments with their
sample counts and the lists that result - "62 samples boreal forest, 3
seaside" - which is where a person sees a mixed batch before running it.

**The chemistry context.** `auto` resolves per sample, as it does today. A
sample whose environments name exactly one context takes it; one with none,
or with environments that disagree, falls back to the reagent profile's
default as today, and the preview says which happened.

**What a run records.** The resolved-profile snapshot on the run gains the
environments, the regimes, and the id and version of every list read, beside
the context and window it already holds.

**Before it is on by default.** The house rule of the assignment work
applies: measured on the testbed against the reference before any default
changes, with a deliberately wrong tag run as well to bound the harm of a
mistake, and with the untagged path checked to reproduce today's ledgers
exactly.

### 4.7 Auxiliary data (shape only)

Wind direction and temperature are not tags; they are series. The shape that
fits the rest of this design:

- an auxiliary source belongs to a deployment (a weather station at a site);
- its data is stored once, as a time series;
- a sample's value is an aggregate over the sample's own time window (the
  file's start plus `t0`..`t1`), exposed as a derived numeric field of the
  registry, so it filters ("wind 200-260 degrees") and plots like any other.

`mascope_signal.kecu.csv_to_xarr` already aligns an external CSV onto a
store's time axis and nothing calls it; the ingest design lists external CSV
channels as a trace source for splitting. The same arriving data could serve
both. Nothing here is designed further.

---

## 5. API and SDK surface

Tentative names; the shapes matter more.

| Route | Does | Phase |
|---|---|---|
| `POST /api/samples/search` | filter -> page of acquisition samples | 1 |
| `POST /api/samples/facets` | filter -> counts per clause value | 1 |
| `POST /api/sample/batches/from-filter` | filter, dataset, name, target collections -> new batch (background) | 1 |
| `GET /api/sample/batches/{id}/refresh` | preview: new and excluded samples | 1 |
| `POST /api/sample/batches/{id}/refresh` | append the previewed samples (background) | 1 |
| `/api/metadata/fields` | the registry | 2 |
| `/api/metadata/acquisition` | read and write values for files or parts, single and bulk | 2 |
| `/api/environments` | the environment registry | 3 |
| `/api/instruments/{instrument}/deployments` | deployments | 4 |

SDK: `find_samples(filter)` and `create_batch(filter, ...)` /
`refresh_batch(...)` on the client, taking the same JSON a person can copy
out of the finder. The existing loaders are unchanged: they read the batch
the filter produced.

---

## 6. Schema changes

| Table | Change | Phase |
|---|---|---|
| `sample_file` | `mz_min`, `mz_max` (Float, indexed), backfilled from `range` and kept in step where `range` is written (the converter, the m/z calibration, `fix_tofwerk_range`). The ingest design's `acquisition_stream` carries its own bounds when it lands | 1 |
| `sample_view` | expose the sample's `mz_min`, `mz_max` (its stream's where it has one, else its file's) and `origin_key` (the view is recreated by a migration) | 1 |
| `sample_batch` | `source_filter` (JSONB, null for a batch built by hand), including the exclusion list | 1 |
| `sample_item` | `origin_key` (String, indexed with `sample_batch_id`), null on existing rows | 1 |
| `metadata_field` | new | 2 |
| `acquisition_metadata` | new; unique on (`sample_file_id`, `part_key`); GIN index on the values | 2 |
| `reference_source` | `applies_to_contexts` (JSON), `always_active` (Boolean) | 3 |
| `environment` | new | 3 |
| `instrument_deployment` | new; no overlap per instrument | 4 |

Existing copies have no origin key. A batch can be given a recipe after the
fact, but its first refresh cannot tell which of the matching samples it
already holds; backfilling the key from the copies' own (`sample_file_id`,
polarity, `t0`, `t1`) is right for every copy whose window was not edited,
and is offered rather than done silently.

---

## 7. Phases

**Phase 1 - the finder and refreshable batches.** Built-in clauses only. It
is the core of the idea, needs no metadata schema, replaces the copy-paste
walk through the acquisition workspaces, and settles the filter grammar that
everything after plugs into. Includes the user-guide change: "Build your
batch from the acquisition samples" becomes the finder.

**Phase 2 - metadata.** The registry, acquisition metadata keyed to the file,
bulk edit and table import, the `metadata` clause and its facets, the shared
fields shown in batches.

**Phase 3 - environments.** The `environment` field and registry, the source
row's two columns, list selection in Stage A, the context from the
environment, the preview and the run snapshot. Measured before any default
changes.

**Phase 4 - deployments.** The table, inheritance by time, the management
surface in the instrument workspace.

**Phase 5 - auxiliary series.** Designed when it is reached.

Each phase ships on its own. Phase 3 depends on 2; phase 4 depends on 2;
phase 1 depends on nothing.

---

## 8. Costs and risks

- **No quick look without a batch.** Decision 1 means a trend over six months
  is seen only after a batch exists and has been matched. The answer is to
  make a batch cheap to build and to throw away, not to add a second read
  path.
- **Row growth.** Every exploratory batch duplicates sample rows and, once
  matched, match rows; `match_isotope` is the large table. Worth measuring
  after phase 1 before encouraging throwaway batches.
- **Large batches.** A filter can select a year. The copy is a background job
  and asks before a large one, but the batch views draw one point per sample
  today; the overview chart will need to bin by time. Not designed here.
- **Wrong tags.** A wrong environment leaves lists out. Decision 5's default
  (untagged reads everything), the preview, the run snapshot and the
  wrong-tag measurement of section 4.6 bound it; they do not remove it.
- **The part identity is the ingest design's.** The origin key and the
  metadata part key are written in its terms (stream, epoch, window). Its
  stream cut is the next thing built there and will probably land before
  phase 1 here; if it does not, phase 1 writes the polarity as the stream
  key, which is that design's own reading of an item with no stream, so keys
  written early stay comparable.

---

## 9. Open questions

| # | Question | Recommendation |
|---|---|---|
| 1 | Who may define registry fields and environments? | As ionization modes do: any editor creates, changing or deleting needs admin, since both reach every instrument's data |
| 2 | Where does the finder live? | A pane of its own beside *Raw files*, which already is the cross-instrument, time-filtered surface for files; the acquisition workspaces stay browsable but stop being the route to a batch |
| 3 | Does a refresh ever run on its own? | No (decision 2 says asked for). A badge on the batch saying new samples match its recipe is cheap and can follow |
| 4 | Does changing a sample's environment flag the batches holding it for re-assignment? | Show it on the batch; do not re-run anything unasked. Existing runs keep their snapshot |
| 5 | The list headers call regimes `applies_to_contexts`, but they are not chemistry contexts. Rename? | Keep the key in schema 2; call them regimes in the UI and here; hold them to a checked vocabulary as list `tags` are |
| 6 | Which time does `datetime_utc` filter on once files are split into windows: the file's start or the window's? | The window's start (file start plus `t0`), so a long file's parts sort and filter where they were measured |
| 7 | Should the finder also search analysis batches a person can read? | Not in this design (decision 1). It would be a different feature: finding one's own work |

---

## 10. How this touches the related designs

- **`ingest_routing_and_splitting.md`**: the acquisition record stays exactly
  as ingest writes it; daily acquisition batches become plumbing a person no
  longer browses, which matters more once a day's items are also batched by
  scan range (its section 4.5). This design consumes three things from
  there: the part identity (stream, epoch, window) as the origin key, the
  stream's own m/z bounds for the `mz_range` clause, and chemistry profiles
  as the unit (phase 8 there) as what the `chemistry` clause names. It offers
  two: acquisition metadata is where a declaration from the instrument side
  (phase 7 there) and a window's labels (phase 6 there) would land.
- **`chemistry_profiles.md`**: its section 4.4 planned for a chemistry
  context to activate reference sources by tag, and its section 11 left the
  context's scope open. This design answers both from the sample side: the
  environment on the sample supplies the regimes and, where unambiguous, the
  context.
- **`reference_data_authoring.md`**: the header keys it documents as "read,
  but nothing acts on them yet" gain their consumer, and the source row
  records them.
- **`peak_assignment_batch.md`**: unchanged. A refresh is incremental arrival
  into a batch, the case its frozen anchors exist for.
- **`ui_state_persistence.md`**: the location chain is unchanged, since a
  filter produces an ordinary batch. The finder's filter is a candidate for
  the shareable location.
