`ULTRA TRACE MASCOPE - DESIGN DOC - MS2 ANALYSIS`

# MS2 analysis: fragmentation spectra as a stored, assigned and served object

- **Status:** design
- **As of:** 2026-10-23
- **Tracked in:** [MS2 analysis](https://linear.app/ultra-trace/project/ms2-analysis-7fdc525bca51)
  (Linear, MAS-380 to MAS-407)
- **What:** Fragmentation spectra as a stored, assigned and served object: the MS2 store, the fragment composition core shared with peaky, MS2 evidence on ledger rows, the MS2 tab, energy-resolved analysis.

## Purpose

An Orbitrap CIMS instrument records fragmentation (MS2) scans when a person
wants to know what an ion is made of: a stepped-energy method that breaks one
precursor at a ladder of collision energies, a targeted method over an
inclusion list, or a manual acquisition in the tuning software. Mascope reads
those scans today and serves four routes over them, but it reads them from the
raw file on every request, stores nothing, assigns nothing, and shows nothing
in the browser. Every MS2 question a user asks is answered in a notebook, or
in peaky, whose MS2 module (seven PRs merged between 2026-10-16 and
2026-10-19) had to rebuild on the client what the server could have served.

This document designs MS2 as a first-class object of the platform: measured
once at conversion into a store beside the peak store, inventoried in the
database, assigned against the ledger's own formulas with one fragment
composition core that peaky also uses, served to the app, the SDK and the
agent, and analysed across collision energies where the rules have settled.

It is an engineering design and phased plan, not a user guide.

> **Status (2026-10-22).** Design. Nothing is built. The plan is a Linear
> project (appendix B); this note stays the record. Section 1 was verified
> against `develop` at 0d9890e60 on 2026-10-22; file links are the evidence.
> The nine design decisions of section 6 are one Decision issue (MAS-380)
> for the plan owner. Two issues of phase 0 (MAS-381, the reader; MAS-385,
> the fixture) were filed `agent-ready` and picked up by the agent loop on
> the day of filing.

> **Decisions (2026-10-23).** The nine decisions of section 6 stand,
> confirmed by the plan owner (MAS-380), with one amendment: the
> composition core's package is `mascope_tools.composition.ms2`, not
> `composition.fragments`, because the engine's "fragments" are already the
> MS1 in-source fragments of the reagent pass (1.5) and `peaky/ms2/` is the
> layout it mirrors. MAS-381 (the reader) merged on 2026-10-22; MAS-385 (the
> fixture) and MAS-386 (the demo acquisition) are in review. The notebook
> toolkit's open defect (MAS-425) is parked under decision 9.

> **Scope decision (v1): Orbitrap raw files, formula-level evidence.** MS2
> is read from Orbitrap raw files only, as today; a TOF sample answers "no
> MS2". What MS2 says about a peak is a statement about its *formula*: which
> sub-formulas its products are and which losses explain them. It never
> names a structure. A structure claim is the business of the identification
> claims design ([identification_claims.md](identification_claims.md)) and
> of the library spectral match (MAS-360), which this note leaves where
> they are.

> **Division of labour.** Mascope measures, stores, assigns and serves;
> peaky analyses and corrects, as it does for the m/z axis
> ([Orbitrap axis fingerprint](https://linear.app/ultra-trace/project/orbitrap-axis-fingerprint-4155b8edcbf9)).
> The one place a fragment peak becomes formula readings lives in
> `mascope_tools`, the way the one isotope scorer does, so the two engines
> read one spectrum identically.

---

## 1. Current state (as-is)

### 1.1 The reader

Both Thermo backends read an MS2 scan's precursor and activation from its
scan filter, with one parser
([backend.py:79](../../libraries/thermo/src/mascope_thermo/backend.py)):
`"FTMS + p NSI Full ms2 137.0960@hcd25.00 [40.0000-160.0419]"` gives
`(137.096, "hcd25.00")`, and a chained activation
(`445.1200@cid30.00@hcd20.00`) is kept whole. The isolation width and the HCD
volts come from the scan's trailer
([backend.py:449](../../libraries/thermo/src/mascope_thermo/backend.py),
`MS2 Isolation Width:` and `HCD Energy V:`;
[backend.py:655](../../libraries/thermo/src/mascope_thermo/backend.py)). The
NCE is therefore in the activation string and the volts in the trailer, and
the two are never converted into each other, because the volts one NCE
applies depend on the precursor m/z. Until MAS-381 nothing read the scan
range, the injection time or the low mass per scan; its per-scan record
does, and says for each field whether the filter, the trailer or the index
gave it.

Scans are grouped by precursor *and* activation
([thermo.py:976](../../libraries/thermo/src/mascope_thermo/thermo.py)):
near-duplicate precursors within a tolerance (1 mDa) are one canonical parent,
and every step of a stepped-energy acquisition is its own group, keyed
`"<parent m/z>@<activation>"`. `by_activation=False` folds the steps into one
group per precursor
([thermo.py:950](../../libraries/thermo/src/mascope_thermo/thermo.py)), which
is what the centroid route serves by default for compatibility. The summary
([thermo.py:1052](../../libraries/thermo/src/mascope_thermo/thermo.py))
reports the parents, one record per group (scan count, time span, volts,
isolation width), the energies per precursor, and the MS1 and MS2 scan counts.
Since 1.13.0 each group carries its own width and the file's one is `null`
when the groups disagree (GitHub #2421). A group whose own scans store two
widths reported none until MAS-381 (on `develop` since 2026-10-22; GitHub
#2441, Linear MAS-265): each group now lists every width it holds with its
scan count (`isolation_widths`), and both backends give one record per MS2
scan (`ms2_scan_records`), the filter's and the trailer's fields alike.

What links a fragment scan to its precursor was measured for the ingest
design ([ingest_routing_and_splitting.md, section 4.2](ingest_routing_and_splitting.md)):
a manual acquisition records no master scan and no scan event, so the
precursor is read from the fragment scan's own filter and the survey scan is
the MS1 scan before it in scan order; the recorded master scan, where there
is one, is one or two survey scans earlier and is not used. A targeted file
with a named inclusion list misread by OpenTFRaw is read by Mascope's own
scan-event reader since 1.13.0, as an interim (GitHub #2391).

The reader-side half of all this is pinned by a scripted acquisition over
the committed MS1 raw file
([test_manual_ms2_acquisition.py](../../libraries/thermo/tests/test_manual_ms2_acquisition.py)),
which relabels scans as fragment scans at the OpenTFRaw reader object and
needs no site file. No committed file records a fragmentation scan, and the
demo bundle has none ([demo_dataset.md, section E](../demo_dataset.md)).

### 1.2 The routes, and what they cost

Four routes under `/api/samples/{id}/ms2/`
([routes.py](../../server/backend/src/mascope_backend/api/new/ms2/routes.py),
[service.py](../../server/backend/src/mascope_backend/api/new/ms2/service.py)),
all token-accessible at workspace guest:

| Route | Answers | Reads |
|---|---|---|
| `summary` | parents, groups, energies, widths, scan counts | the raw file |
| `ms1_centroids` | the averaged MS1 centroids of the sample | the raw file |
| `centroids` (`by_activation`, `noise_threshold`) | one averaged centroid spectrum per parent, or per group | the raw file |
| `timeseries` (`parent_peak_mz`, `activation`, `normalize_by`) | per-scan fragment intensities of one parent | the raw file |

Every one opens the raw file
([compute.py:1779](../../libraries/signal/src/mascope_signal/compute.py),
[compute.py:1860](../../libraries/signal/src/mascope_signal/compute.py),
[compute.py:1902](../../libraries/signal/src/mascope_signal/compute.py)),
under a per-request timeout of 120 s that answers 504 when exceeded, and
nothing is kept between requests. The calibration factor of the sample's MS1
calibration is applied to the fragment m/z on read. A sample of a file with
no MS1 scans answers its MS1 centroids empty rather than failing, by the
census ([service.py](../../server/backend/src/mascope_backend/api/new/ms2/service.py),
`get_ms1_averaged_centroids`). There is no per-scan read of a group's
centroids: the only per-scan view is the time series, one parent at a time.

The cost shows at the client. Peaky paces its MS2 reads one second apart,
refuses an inventory over more than 100 samples because each costs a summary
request, and documents that a compute timeout costs about four 120-second
computations through the SDK's retries
(peaky `docs/MS2.md`, section 10.2). Nothing in Mascope answers "which
samples of this batch have MS2" without opening every file.

### 1.3 Ingest: the census, the window, the MS2 parts

The converter records a scan-stream census in the file's props
([schema.py:28](../../server/backend/src/mascope_backend/file_converter/schema.py),
`SampleFileProps.scan_streams`), one stream per scan signature, with the MS
order; fragmentation scans are grouped by signature alone
([streams.py](../../libraries/thermo/src/mascope_thermo/streams.py)). The
census is what the pipeline reads about a file's scans without opening it
([census.py](../../server/backend/src/mascope_backend/api/controllers/sample/files/process/census.py)).

A sample item's window spans every scan type since 1.8.0, so a manual
acquisition's fragment scans, recorded after its MS1 block, are read through
its sample; before that fix they fell outside the window and the file read as
MS1-only. A file that records no MS1 scans is filed, since 1.12.0, as an
*MS2 part*: one ACQUISITION item per polarity that holds fragmentation scans,
no ionization mode, in the day's `<date> MS2 acquisition (no chemistry)`
batch ([ms2_parts.py](../../server/backend/src/mascope_backend/api/controllers/sample/files/process/ms2_parts.py)).
Such a sample has no peaks, no calibration, no matches and no ledger; its
fragmentation scans are reachable through the routes and the SDK, and
"nothing in the browser shows the fragmentation scans yet" (the 1.12.0
changelog). Whether each (precursor, activation) stream should become an item
of its own is the ingest note's open decision 6; nothing here needs it.

The peak store holds MS1 only. GitHub #1374 ("Write MS/MS scans into a
separate zarr file") is open and attached to MAS-94 in the ingest project.

### 1.4 The SDK and the notebook toolkit

`Ms2Resource` ([ms2.py](../../libraries/sdk/src/mascope_sdk/resources/ms2.py))
wraps the four routes, `by_activation` off by default as on the server, and
documents the key shapes a client reads; the contract suite pins them on a
demo stack, where only the summary can be checked because the bundle has no
MS2 scans.

The MS2 notebook toolkit
([tooling/notebooks/ms2_analysis/](../../tooling/notebooks/ms2_analysis/))
is the in-repository analysis surface: a `DataExtractor` over the SDK, a
`CompositionMap` that labels fragments from a match export, a
`ClusterClassifier` that sorts each parent into declustering (the charge
stays with the reagent) or proton transfer (the analyte fragments) or
undetermined, a fragment table with CSV export, a ratio chart, a stem-plot
dashboard with time series, and a `FragmentationAnalysis` that draws stacked
bars per type. It is CIMS science by hand in a notebook, and its classifier
is the crude ancestor of peaky's binding class (1.6).

### 1.5 Nothing in the app, nothing in the ledger

The Sample view ([PaneTabSample.vue](../../server/frontend/src/lib/panes/PaneTabSample.vue))
holds the MS1 spectrum ([ChartSampleSpectrum](../../server/frontend/src/lib/charts/ChartSampleSpectrum/data.js),
sticks coloured by tier) and the assignment time series; no component reads
an MS2 route. The sample listing carries no MS2 field, so a person cannot tell
which of a day's 1,440 acquisitions fragmented anything.

The peak-assignment engine records what each pass took under a key in
provenance ([engine.py:147-190](../../server/backend/src/mascope_backend/api/new/peak_assignments/engine.py))
and marks a winner that the isotope pattern cannot separate from a rival as a
tie ([engine.py:1391](../../server/backend/src/mascope_backend/api/new/peak_assignments/engine.py);
[engine.py:1738](../../server/backend/src/mascope_backend/api/new/peak_assignments/engine.py),
`record_grid_rivals`). Its "fragments" are MS1 in-source fragments claimed
through a profile's ladders
([reagent_pass.py:452](../../server/backend/src/mascope_backend/api/new/peak_assignments/reagent_pass.py)),
not MS2. The top tier is named `assigned` and not `identified` precisely
because "in mass spectrometry an identification is read as MS2 or
reference-standard evidence"
([tiers.py](../../server/backend/src/mascope_backend/api/new/peak_assignments/tiers.py)).
The identification claims design maps `msms` evidence to Schymanski level 2
for a *structure* and leaves the engine's own level at 4 at best
([identification_claims.md, section 5.4](identification_claims.md)); its
MS/MS path is a library spectral match (phase 4 of
[public_database_integration.md](public_database_integration.md), MAS-360),
which has no project yet.

### 1.6 What peaky built, and what it had to work around

Peaky's MS2 module is seven PRs, each with its rules named, its own-data
known answers pinned and its tests run adversarially:

| peaky PR | What it does |
|---|---|
| [#78](https://github.com/ultra-trace-systems/peaky/pull/78) | **Sub-formula readings with loss decomposition.** Every MS2 peak read as a sub-formula of every ion in the isolation window; kept only when the neutral loss splits into at most 3 molecules from an ordered library (43 closed-shell, 16 radical) plus one unnamed remainder with integer DBE, Senior's rule and H or a halogen; window max(5 ppm, 0.3 mDa); readings never ranked across parents; 15N its own element. |
| [#79](https://github.com/ultra-trace-systems/peaky/pull/79) | **The Mascope source.** `ms2 list` (one summary per sample) and `ms2 extract` (spectra per energy, parents, the isolation-window inventory from the MS1 centroids, a manifest); the precursor located once per parent by a nearest-strong rule; S/N 3 rather than the SDK's 10 because the weak fragments are where the breakdown is; fallbacks for every server quirk. |
| [#80](https://github.com/ultra-trace-systems/peaky/pull/80) | **Across energies.** Survival F, F_lo and S per window ion; E50 on NCE, volts and centre-of-mass energy with flags; fragment onsets; co-isolate attribution (candidates by composition, the intact test, the mass-balance guard, share weights); the ladder-gated fourth loss molecule. |
| [#81](https://github.com/ultra-trace-systems/peaky/pull/81) | **Per scan.** The scans behind each server spectrum, named through one file-metadata read and one time series per spectrum; a raw file the server cannot read rebuilt from its trailer ("the trailer, never the filter"); the server's averaging reproduced to 0.005 ppm. |
| [#82](https://github.com/ultra-trace-systems/peaky/pull/82) | **Replicates and ghosts.** Per-scan spread, a bootstrap band on every E50, and the Orbitrap harmonic ghost (m/z / 4 and m/z / 9 of a strong line) never read as a formula. |
| [#83](https://github.com/ultra-trace-systems/peaky/pull/83) | **Binding class.** Every product read against the ion's reagent adduct (reagent ion, reagent loss, monomer shedding, skeletal); each ion classed adduct, cluster, covalent, mixed or unknown. |
| [#85](https://github.com/ultra-trace-systems/peaky/pull/85) | **Arbitration.** Rival formulas of one precursor read one at a time, credited by their products against 8 decoys; verdicts supported, contradicted, outscored, undecided; support never granted by elimination. |

Half of that work is science Mascope should carry; the other half is
scaffolding around what the server does not serve. The second half is the
plan's phase 0 and 1:

| What peaky had to do | Why | What Mascope serves instead |
|---|---|---|
| Pace reads 1 s apart, refuse batches over 100 samples | every route is a raw read | the MS2 store (2.2) and the group inventory (2.3) |
| Name the scans behind a spectrum through `/metadata` plus one time series per spectrum | no per-scan read of a group | `GET .../ms2/scans` from the store |
| Read the trailer itself for NCE, volts, width, scan range, injection time | the reader serves the filter's precursor and activation and two trailer fields | one per-scan record from the reader (MAS-381) |
| Build the isolation-window inventory and locate the precursor on the client | nothing serves either | `GET .../ms2/window`, the precursor located at store time (2.5) |
| Treat "summary refused" and "hidden tail" as statuses | the two-width defect; files ingested before the window fix | MAS-381; the store makes a hidden tail impossible |
| Implement the composition rules itself | Mascope has no fragment reader | `mascope_tools.composition.ms2` (2.4) |

### 1.7 Already filed, and taken up here

- GitHub #2441 / MAS-265: a group with two isolation widths reports none.
  Taken up by MAS-381 under decision 2.
- GitHub #1374 / MAS-94: MS/MS scans stored apart from MS1. Taken up by
  MAS-382; MAS-94's brief keeps the other file shapes.
- GitHub #2391: OpenTFRaw's misread of version-66 scan-event records. The
  interim reader in Mascope stands; not this project's.
- MAS-360: MS/MS candidate ranking by spectral similarity over library
  spectra. A structure-level question; stays in the assignment quality plan.
- Ingest decision 6: items per (precursor, activation) stream. Not needed
  by this design; left open.

---

## 2. Architecture

### 2.1 Where MS2 sits

```
raw file ──converter──▶ peak store (MS1)        ms2 store (per file)
                        census ─────────────▶ ms2_group (per file, polarity, precursor, activation)
                                                      │
   routes: summary · scans · centroids · window · readings · breakdown · timeseries
                                                      │
   engine: MS2 pass ─▶ ms2_support per ledger row ─▶ arbitration of ties
                                                      │
   app: MS2 tab (inventory, spectrum, ladder, breakdown) · inspector · sample table
   SDK: Ms2Resource · batches.ms2_groups · batches.ms2_e50
   peaky: reads the served tables; reads fragments through the one core
```

Five things are new, in the order the phases build them: a **store** the
converter writes; an **inventory** the database holds; a **composition core**
in `mascope_tools`; a **pass** in the engine that writes MS2 evidence to
provenance; and a **tab** in the Sample view. The across-energy analysis
(2.7) and the SDK, peaky and agent surfaces (2.9) sit on those.

### 2.2 The MS2 store

**What it is.** One store per file, written by the converter when the census
holds a fragmentation stream, beside the peak store, and recorded in
`SampleFileProps` (`ms2_store`: layout version, the grouping tolerance, the
group keys). Per (polarity, precursor key, activation) group it holds:

- the **scan table**: scan number, time, polarity, precursor m/z, activation
  kind and NCE, volts, isolation width, scan range, injection time, TIC, and
  for each field whether it came from the filter or the trailer (the reader's
  per-scan record, MAS-381);
- the **per-scan centroids**: m/z on the instrument's own axis, intensity,
  resolution, S/N, as a ragged array indexed by scan;
- the **averaged centroids** of the group at an S/N floor of 0, computed as
  the raw read computes them today, so the routes' floor is applied on read
  and a client that asks for S/N 3 and one that asks for 10 read one spectrum;
- the group's **metadata**: scan count, time span, the widths it holds with
  their scan counts, the located precursor (2.5), the activation kind.

The calibration factor of the sample's MS1 calibration is applied on read, as
today, so a recalibration of the file never rewrites the store.

**Why at conversion.** The raw file is opened once there anyway; the cost of
reading its fragmentation scans is paid once instead of on every request; and
a file converted on one reader stays readable through the store when the
reader changes. A hidden tail (fragment scans past a sample's `t1`, which an
ingest older than the window fix left unreadable) cannot exist: the store is
the file's, not the sample's, and a sample reads the groups whose scans fall
in its window.

**Why a store of its own and not the peak store.** The peak store is keyed by
MS1 peaks and their per-scan time series; MS2 groups are keyed by precursor
and activation and hold whole centroid lists per scan. Putting one in the
other would make every MS1 reader skip MS2 keys. GitHub #1374 asked for the
separate store; this is it.

**Size.** A stepped-energy acquisition is a few hundred fragment scans of a
few dozen centroids each: hundreds of kilobytes. An acquisition day that
fragments nothing writes nothing. The fleet's MS2 files are a small fraction
of its 80k samples a month; the backfill (MAS-383) counts them before it
writes anything.

**Reads.** Every route answers from the store when the file has one and from
the raw file when it does not, and says which (`source`). A request whose
`parent_peak_tolerance` differs from the store's grouping tolerance is
answered from the store's grouping, with that tolerance in the response; it
never falls back to the raw read the store exists to remove. The group key
has one string form, the parent m/z with four decimals, `@`, the activation
(`137.0960@hcd25.00`), so a store key, an `ms2_group` row and a
`scans?group=` query spell one group one way. A file converted
before the store existed is backfilled by a resumable, rate-limited script
over the census; re-processing a file rewrites its store; deleting the file
deletes it.

### 2.3 The group inventory

An `ms2_group` table, one row per (file, polarity, precursor key,
activation): kind, NCE, volts, the widths it holds, scan count, time span,
scan range, the located precursor. Written when the store is; filled by the
backfill.

It answers in one query what costs a raw read per sample today:
`GET /api/batches/{id}/ms2/groups` lists the groups of every sample of a
batch; the samples listing carries `ms2_group_count`; the sample's own
summary answers from the table. The sample finder
([sample_filters_and_metadata.md](sample_filters_and_metadata.md)) gains a
typed field to filter on ("has MS2", "fragmented this precursor"), named in
its schema and built there.

### 2.4 The fragment composition core

`mascope_tools.composition.ms2` is the one place a fragment peak becomes
formula readings, for the engine's pass, the readings route, and peaky. It
is a package named for what it reads, not `fragments`: the engine's
"fragments" are the MS1 in-source fragments of the reagent pass (1.5), and
`peaky/ms2/` is the layout it mirrors module for module (`readings`,
`attribute`, `breakdown`, `arbitrate`). It is pure: no I/O, no network, no
server. Its rules are peaky's, ported with peaky's known answers as tests
(appendix A has the mapping):

- **Readings.** A fragment of a singly charged precursor is an element-wise
  sub-formula of the precursor *ion*; the difference is the neutral it lost.
  Every peak is read against every ion in the isolation window, and a reading
  is kept only when its loss decomposes into at most `k_max` (3) molecules
  from an ordered library of closed-shell and radical formulas, plus at most
  one unnamed remainder with integer DBE at or above 0, Senior's second rule,
  and H or a halogen. A radical loss holds exactly one radical piece. Loss
  names are formulas, never trivial names and never structures.
- **Window.** max(5 ppm, 0.3 mDa), judged on the recalibrated axis: one
  constant-ppm offset per isolation window, measured on the target's located
  precursor.
- **Ranking** within one (peak, parent): closed-shell loss before radical,
  then fewer pieces, fewer unnamed pieces, smaller |ppm|. Readings are never
  ranked across parents; `n_parents` marks a peak more than one window ion
  can explain, and the attribution (2.7) decides.
- **The ladder gate.** A fourth loss molecule is admitted only when the same
  window ion shows the step before it at rank 1 at some energy. A blanket
  `k_max` of 4 lets more chance compositions read at every peak; the gate
  admits the ammonium-adduct chains (NH3 then water, step by step) that need
  it, and only when their previous step is observed.
- **Peak hygiene.** Two kinds of centroid are not ions and read as
  sub-formulas of almost anything: the Orbitrap harmonic ghost (a weak peak
  within 40 ppm of m/z / 4 or m/z / 9 of a strong line, under 0.5 % and 8 %
  of it, unless some energy reads that place as an ion) and the side-lobe
  satellite (a fragment every peak of which sits within 5 line widths of a
  peak 50 times brighter). Both are flags on the peak, never deletions; a
  flagged peak is one row with no formula, out of every sum.
- **Notation.** Mascope's, with isotope labels as `[15N]`; peaky's caret
  labels accepted on input. A labelled atom is its own element in masses and
  in counts.

Every threshold is a named constant documented in
`libraries/tools/docs/ms2.md`, as `fit_score.md` does for the isotope
scorer. Determinism is a test: the same input gives the same bytes under two
hash seeds.

### 2.5 Window ions, the window, and the readings

**Window ions are named, never guessed.** The ions a group is read against
are, in this order of availability: the ledger's assigned and candidate rows
whose ion m/z lies in the group's isolation window, and their grid rivals;
the batch's target ions in the window; ions a caller names. A named ion's
typed m/z must lie within max(10 ppm, 5 mDa) of its formula's exact m/z, or
it is refused as a typo with the nearest key. Two named ions within the
reading tolerance of each other are aliases (the CHO / N[15N] and CO3 /
[15N]O3 pairs of labelled data): the window cannot tell them apart, only the
first is read, and the other is named as left out. An MS2 part, which has no
ledger, reads only named ions.

**The window inventory** (`GET .../ms2/window`): per group, the MS1 centroids
within +/-(width / 2 + 5 mDa) of the parent key, each with its offset, share
of the window and an `is_parent` mark, from the sample's averaged MS1
centroids. An MS2 part has no MS1 scans and answers an empty window, said.

**The precursor is located once per parent**, at store time: among the MS1
peaks within max(10 ppm, 5 mDa) of the key that hold at least 10 % of the
strongest, the nearest to the key, in the lowest-energy spectrum that holds
one; each spectrum's precursor peak is then tracked within max(5 ppm,
0.5 mDa) of it, `None` where spent. The rule exists because a key typed with
two decimals sits millidaltons off its ion, and nearest-alone picks a
satellite while strongest-alone picks a co-isolate.

**The readings route** (`GET .../ms2/readings?group=&ions=`): per spectrum of
the group, one row per (peak, parent) reading with its role (precursor,
fragment, unexplained, harmonic, satellite), formula, loss and pieces, ppm,
rank and counts; the recalibration offset; the aliases left out. Cached in
the store by the ions and the core's version. `ions` becomes optional once
the engine's pass has named the ledger's window ions, and the response names
its source.

### 2.6 MS2 as evidence in the ledger

**The pass.** After the stages and the in-source fragment claim, for every
sample of a run whose inventory holds a group, the engine reads each group
against its window ions (2.5) through the same core call the route makes,
and writes per ledger row an `ms2_support` record under `MS2_KEY`: the groups
read, the products explained (count and share of the spectrum's intensity
after ghosts and satellites), the *diagnostic* products (read by this formula
and by no rival in the window), the named losses, per energy. A run on a
batch with no MS2 group is byte-identical to a run before the pass.

**Arbitration of ties.** A tie is where MS1 stops. For a tied row whose
rivals lie in a window the pass reads the window once per rival with the
other ions fixed, credits each rival with a product's intensity times its
attribution weight (nothing for a reading whose remainder is a carbon-poor
polyoxy piece), scores each on its own and every other rival's recalibration
and keeps the best, and controls against 8 decoys in which every product peak
is moved by +/-7, 11, 15 and 19 mDa. The verdict is `supported` (a share lead
of at least 0.10 over every rival, at least 2 diagnostic products of at least
1 % each, and a lead beyond the decoys' by 0.35), `contradicted` (an element
signature absent where products should show it), `outscored` or `undecided`.
Support is never granted by elimination. On peaky's own files no window
separates two rivals; the supported paths are pinned on synthetic windows,
and Mascope's will be measured on a harness under `tooling/score_eval` before
anything acts on them.

**What it may move.** Nothing, at first: the record is written, shown in the
inspector and the exports, and measured. The identification claims note's
level mapping ([identification_claims.md, section 5.4](identification_claims.md))
makes the boundary clear: `msms` evidence is a level-2 statement about a
*structure*, and MS2 support here names no structure, so it never writes a
claim. What it can do is make a *formula* less equivocal: a `supported`
verdict on a tied row is the fact that un-ties it (the engine's level 5
becomes 4, "unequivocal formula") once the harness says the false-support
rate is low enough (decision 5; MAS-393 takes it on the measurement). Raising
a row's evidence towards the assigned band is a second decision, after a
second measurement over tied rows a person has verified.

**The contract.** `ms2_support` is one shape for both engines: it enters the
run-import contract ([sdk_peak_assignment.md, section 8](sdk_peak_assignment.md))
so a peaky run lands with its support, and the ledger export, the batch
spreadsheet and `load_assignments` carry it.

### 2.7 Energy-resolved analysis

A stepped-energy acquisition is recorded to see a precursor break: what
survives at each energy, what appears, and where the halfway point lies.
Peaky's across-energy module is where the rules live today, and they fall
into two groups.

**Served by Mascope (phase 4).** The rules that have held across peaky's
PRs and rest on definitions rather than tuned constants:

- *Attribution* (MAS-399): a fragment's candidates are the window ions that
  read it as a formula within the reading tolerance of its signal-weighted
  m/z; one candidate takes it; several go to the intact test on
  TIC-normalised precursor fractions (a candidate still at 98 % or more of
  its lowest-energy fraction at the fragment's onset is excluded), the
  mass-balance guard (a call the ion could not have carried is withdrawn),
  and otherwise a split by the lowest-energy survivors, the MS1 window
  shares, the precursor where first seen, or equal; an ion absent at every
  energy with a measured MS1 share of 0 weighs nothing; branching above 1.05
  is flagged. No least-squares deconvolution.
- *Survival and E50* (MAS-400): F = I / (I + the ion's weighted fragments),
  a ratio within one spectrum that holds whatever happened to the precursor
  supply between energies; F_lo with every shared fragment given whole; S =
  I(E) / I(E_min), reported with the caveat that it needs a steady supply.
  E50 by linear interpolation at the first downward crossing of 0.5 on NCE
  and on volts; E_cm = V · m_gas / (m_gas + m/z) with the gas a parameter;
  the flags `interpolated`, `below_range`, `above_range`, `single_energy`,
  `gap` (never interpolated across a hole) and `no_data`; one activation kind
  per breakdown; only observed products, so a reagent ion released below the
  scan's first mass is in no sum and S is what sees that loss.

These are served per (sample, precursor) by `GET .../ms2/breakdown`, cached
in the store, drawn in the MS2 tab, and rolled up per batch by
`GET /api/batches/{id}/ms2/e50` for the campaign question (did this ion's
E50 drift; which ions break early), which Visual digests can then draw.

**Kept in peaky until a decision (MAS-401).** The binding class (every
product read against the reagent adduct; adduct, cluster, covalent, mixed,
unknown) and the replicate statistics (per-scan spread, a bootstrap band on
every E50) are days old, pinned mostly on synthetic windows, and their
constants rest on two files. The recommendation is to wait one peaky release
and port the class as the core was ported, replicates last; the store serves
the per-scan tables the replicates need either way, so peaky loses nothing
meanwhile. The notebook toolkit's declustering-versus-proton-transfer
classifier retires whichever way the decision goes.

### 2.8 The Sample view

An **MS2 tab** beside the Sample tab, shown when the focused sample's group
count is above 0, and the tab an MS2 part opens on, since it has no MS1
spectrum. Behind no feature flag: a sample with no groups shows no tab, and
nothing else changes. The tab is driven by the inventory, never by a raw
read, so a file with MS2 whose store has not been backfilled shows no tab
until MAS-383 has run on that server.

- The **inventory** of the sample's groups (precursor key, located
  precursor, kind and NCE, volts, widths, scans, time span), one selected and
  persisted in the UI state as the focused peak is.
- The **annotated spectrum** of the selected group: sticks by role
  (precursor, fragment, unexplained, harmonic, satellite), hover with the
  window ion, formula, loss, ppm and rank; an S/N floor control; an ions box
  for a sample with no ledger, whose formulas are checked as the route checks
  them and persisted per sample.
- The **ladder**: the per-energy spectra of one precursor as small multiples
  on a shared m/z axis, fragments linked by formula across the panels.
- The **time series** of the group's brightest fragments over the scans
  (the existing route), and the **breakdown** chart once phase 4 serves it.

The **inspector** gains an MS2 section on a ledger row (the groups read, the
products explained and the diagnostic ones with their losses, the verdict of
a tied row with its lead and the decoys' mean) and a link into the tab at
that group. The **sample table** gains an MS2 column and a row marker.

### 2.9 The SDK, peaky and the agent

`Ms2Resource` gains a wrapper per route (`get_scans`, `get_window`,
`get_readings`, `get_breakdown`) and the batches resource `ms2_groups` and
`ms2_e50`, each a frame with the provenance block in `attrs` and a version
gate that names the server release a route needs.

Peaky's `ms2 extract` reads the scan table and the window from the routes
when the server has them, keeps `--file` for files the server cannot read,
and reads fragments through `mascope_tools.composition.ms2` behind a
mascope-tools version gate; its known answers must not move. The notebook
toolkit is retired (decision 9), and nothing more is spent on it before
then. The AI layer's
oracle gains `ms2_groups`, `ms2_readings` and `ms2_breakdown` and one golden
question. The user docs gain a how-it-works page on fragment readings,
breakdown curves and what a verdict does and does not claim.

---

## 3. The routes

All under `/api/samples/{id}/ms2/` unless noted, token access at workspace
guest, Orbitrap only (a TOF sample answers an empty inventory with the reason
`unsupported`, an Orbitrap file with no groups the reason `none`; never 500).

| Route | Phase | Answers | Source |
|---|---|---|---|
| `summary` | exists; 0 | parents, groups (widths as a list), counts | inventory table, else store, else raw |
| `ms1_centroids` | exists | averaged MS1 centroids | raw (MS1), as today |
| `centroids` | exists; 0 | averaged spectrum per parent or per group, at the S/N floor asked | store, else raw |
| `timeseries` | exists; 0 | per-scan fragment intensities of one parent | store, else raw |
| `scans?group=` | 0 | the scan table and per-scan centroids of one group | store |
| `/api/batches/{id}/ms2/groups` | 0 | every group of every sample of the batch | inventory table |
| `window[?width=]` | 1 | MS1 centroids per isolation window, shares, `is_parent`, the located precursor | MS1 centroids + store |
| `readings?group=&ions=` | 1 (2: `ions` optional) | readings per spectrum, roles, offset, aliases | core; cached in the store |
| `breakdown?precursor=&ions=` | 4 | F, F_lo, S, budget per ion and energy; E50 rows with flags; fragments with onsets, calls, weights | core; cached in the store |
| `/api/batches/{id}/ms2/e50?ions=` | 4 | E50 rows per (sample, ion) | cached breakdowns, within a budget |

Every response says `source` (`store` or `raw`) and, where cached, `cached`
and the core's version. The group key `<parent m/z>@<activation>` does not
move (decision 2).

---

## 4. Phased plan

Each phase lands independently and leaves the system shippable. One issue
per bullet; the identifiers are the Linear issues (appendix B).

### Phase 0 - Read it right, store it once (no user-visible behaviour beyond faster routes)

- MAS-380: the nine design decisions of section 6 confirmed.
- MAS-381: the reader's per-scan MS2 record from the trailer and the filter
  alike (NCE and volts both, width and scan range per scan, injection time,
  the source of each field); a group whose scans store two widths reports
  them all and its key does not move (GitHub #2441, MAS-265); the scripted
  acquisition gains a stepped-energy shape.
- MAS-385: the hermetic MS2 fixture, three acquisition shapes (manual,
  interleaved, stepped-energy) built on the committed MS1 file with
  synthetic fragment centroids and one JSON of known answers that every
  later phase tests on.
- MAS-382: the MS2 store written at conversion, the four routes reading it,
  `GET .../ms2/scans` (GitHub #1374).
- MAS-383: the backfill over the files whose census holds a fragmentation
  stream, `--count` first.
- MAS-384: the `ms2_group` table, the batch-level groups route,
  `ms2_group_count` on the listing, the field named for the sample finder.
- MAS-386 (needs input): an MS2 acquisition for the demo bundle (1.4.0) and
  an MS2 golden in the reproducibility check.

Phase 0 is worth having on its own: a store read in place of a 120-second
raw read per request, and a batch's MS2 inventory in one query.

### Phase 1 - The fragment composition core

- MAS-387: `mascope_tools.composition.ms2` (`readings`), the readings with
  loss decomposition, the ranking, the ladder gate; peaky's known answers as
  tests; `libraries/tools/docs/ms2.md`.
- MAS-388: harmonic ghosts and side-lobe satellites as flags.
- MAS-389: the window inventory route and the precursor located at store
  time.
- MAS-390: the readings route over named ions, cached, with its SDK wrapper.

### Phase 2 - MS2 evidence in the ledger

- MAS-391: the MS2 pass; window ions from the ledger and the targets;
  `ms2_support` per row; nothing moves.
- MAS-392: the arbitration of tied rows against decoys; the harness and its
  report.
- MAS-393 (decision): whether a supported verdict un-ties a row and feeds the
  tier, taken on the report.
- MAS-394: `ms2_support` in the run-import contract, the exports and
  `load_assignments`.

### Phase 3 - The Sample view

- MAS-395: the MS2 tab with the inventory and the annotated spectrum; an MS2
  part opens on it.
- MAS-396: the energy ladder and the fragment time series; the breakdown
  chart's slot.
- MAS-397: the sample table's MS2 column and marker.
- MAS-398: the inspector's MS2 section and the link into the tab.

Phases 1 to 3 can run beside each other once phase 0 is in; phase 3's tab
draws readings as soon as MAS-390 serves them and the ledger's ions as soon
as MAS-391 names them.

### Phase 4 - Energy-resolved analysis

- MAS-399: co-isolate attribution.
- MAS-400: survival, E50 and onsets; the breakdown route and chart.
- MAS-401 (decision): the collision gas; binding class and replicates in
  Mascope or in peaky.
- MAS-402: E50 per ion across a batch.

### Phase 5 - SDK, peaky, agent and docs

- MAS-403: the SDK wrappers and contract tests, route by route.
- MAS-404: peaky reads the served tables and defers to the core (in the
  peaky repository).
- MAS-405: the notebook toolkit retired.
- MAS-406: the AI layer's MS2 oracle wrappers and one golden question.
- MAS-407: the user docs.

---

## 5. Costs and risks

- **The reader is the foundation and has had three defects in a month**
  (#2391, #2421, #2441). Phase 0 reads MS2 metadata from the trailer and the
  filter alike and pins both backends on a scripted acquisition with every
  shape the fleet produces. A store written on a misread file is a misread
  store: a file refused for misread scan events (1.13.0's rule) gets no
  store, and re-processing rewrites it.
- **No real MS2 file in the repository.** The fixture (MAS-385) is synthetic
  centroids on the committed MS1 file's scan skeleton, which pins the rules
  but not the instrument. Peaky's public fixtures (two monoterpene standards
  and a labelled-ammonium exploratory file, with no identifiers) pin the
  rules on real spectra, and an MS2 acquisition in the demo bundle
  (MAS-386) is the owner's call. Until it exists the e2e suite cannot open
  the tab and the contract tests skip their centroid checks.
- **Two implementations of one science.** Every rule ported from peaky is a
  rule that can drift. The port carries peaky's known answers as tests, the
  core is versioned and its version is in every cached response, and MAS-404
  turns peaky onto the core so there is one implementation again. What has
  not settled (binding class, replicates) is not ported until it has.
- **Formula-level only, by design.** A user who reads "supported" as
  "identified" has been misled. The inspector, the exports and the docs word
  every verdict as a statement about a formula; the claim machinery stays
  the identification claims design's.
- **Volume.** The store is small per file and absent for an MS1-only file;
  the risk is the backfill's wall time on a server with years of MS2
  acquisitions, which is why it counts before it writes and is rate-limited
  and resumable. The breakdown cache is keyed by the ions and the core's
  version and is recomputed, not migrated, when either changes.
- **The goldens.** Nothing here moves MS1 output; the reproducibility check
  stays green until MAS-386 adds an MS2 golden, after which a change to the
  store's averaging is caught as an MS1 change is.
- **A window with no named ion reads nothing.** An MS2 part, or a sample
  whose ledger has no row in the window, has no readings until a person or a
  target collection names an ion. That is the price of "named, never
  guessed", and the tab's ions box is the remedy.

---

## 6. Key design decisions

The recommendations, confirmed by the plan owner on 2026-10-23 (MAS-380):

1. **Store once, at conversion.** MS2 is written to a store beside the peak
   store and the routes read it; the raw file is read only for a file
   converted before the store existed, and the response says which.
2. **The group key does not move.** `<parent m/z>@<activation>` stays. A
   group whose scans isolate at two widths reports every width it holds and
   its scan table says which scan used which (MAS-265).
3. **One composition core, in `mascope_tools`.** `composition.ms2` is the
   one place a fragment becomes formula readings, for the engine and for
   peaky, as `score_pattern_v2` is the one isotope scorer. Named `ms2`, not
   `fragments`: the engine's fragments are MS1 in-source fragments (1.5),
   and the package mirrors `peaky/ms2/` module for module.
4. **Window ions are named, never guessed.** The ledger's assigned and
   candidate rows and their grid rivals, the batch's target ions, or ions a
   caller names; an MS2 part reads only named ions.
5. **Formula-level evidence, recorded before it acts.** `ms2_support` is
   written to provenance first, may un-tie a row or feed the tier only after
   measurement on a harness, and never writes a structure claim.
6. **Serve the stable subset of the across-energy analysis.** Attribution,
   survival, E50 and onsets in Mascope; binding class and replicates in
   peaky until MAS-401.
7. **Orbitrap raw files only**, declared on every route; a TOF sample answers
   "no MS2" rather than failing.
8. **The MS2 tab shows when there is something to show**, behind no feature
   flag; an MS2 part opens on it.
9. **The notebook toolkit retires** once the tab and the SDK cover it.

---

## 7. Open questions

1. **The collision gas** for E_cm: nitrogen by default with an instrument
   setting, or a per-request parameter only (MAS-401).
2. **Target ions as window ions.** The targeted path is being folded into
   the peak-centric one (MAS-162, MAS-80); until a target match is a ledger
   row, the pass reads the batch's target ions directly. Whether that stays
   after the fold is the fold's question.
3. **Items per (precursor, activation) stream** (ingest decision 6). The
   store is keyed by file and group, so nothing here needs it; if it is
   taken, the inventory gains a stream id and the routes a stream parameter.
4. **A peaky-imported run's `ms2_support` against Mascope's.** Both carry
   the core's version; a run imported with support from an older core keeps
   it, labelled. Whether to recompute on import is open.
5. **MS2 on the fleet's TOFs.** None record MS2 today. If one does, the
   reader gains a TOF backend and nothing above the store changes.

---

## Appendix A: where each of peaky's rules lands

| peaky | Rule | Lands in | Issue |
|---|---|---|---|
| #78 `subformula.py` | sub-formula readings, loss decomposition, library, ranking, `n_parents`, 15N | `mascope_tools.composition.ms2` (`readings`) | MAS-387 |
| #80 `readings.py` | the ladder gate on a fourth piece | same | MAS-387 |
| #82 `readings.harmonic_ghosts` | harmonic ghosts | same, as flags | MAS-388 |
| #80 `attribute.satellite_fragments` | side lobes | same, as flags | MAS-388 |
| #79 `sources.locate_precursor`, `window_table` | precursor location, window inventory | the reader at store time; `GET .../ms2/window` | MAS-389 |
| #79 `sources.*`, #81 `scans.py` | per-energy and per-scan tables, the trailer read | the MS2 store and the scans route | MAS-381, MAS-382 |
| #79 `ms2 list` | the inventory | `ms2_group`, the batch route | MAS-384 |
| #80, #84 `attribute.py` | candidates, intact test, guard, share weights, branching | `ms2.attribute` | MAS-399 |
| #80 `breakdown.py` | F, F_lo, S, E50, E_cm, flags, onsets, budget | `ms2.breakdown`; `GET .../ms2/breakdown` | MAS-400 |
| #85 `arbitrate.py` | rivals, decoys, verdicts | `ms2.arbitrate`; the MS2 pass | MAS-392 |
| #83 `channels.py` | product channels, binding class | peaky, until MAS-401 | MAS-401 |
| #82 `replicates.py` | per-scan spread, bootstrap bands | peaky, until MAS-401 | MAS-401 |
| #78 "next PRs: ledger annotation" | `ms2_support` | the MS2 pass; the import contract | MAS-391, MAS-394 |

## Appendix B: the Linear project, as proposed

[MS2 analysis](https://linear.app/ultra-trace/project/ms2-analysis-7fdc525bca51),
team Mascope, initiative B (Assignment science and product), filed
2026-10-22, MAS-380 to MAS-407. Six milestones follow the phases above:

| Milestone | Issues |
|---|---|
| 0 · Read it right, store it once | MAS-380 (decision), MAS-381, MAS-382, MAS-383, MAS-384, MAS-385, MAS-386 (needs input) |
| 1 · The fragment composition core | MAS-387, MAS-388, MAS-389, MAS-390 |
| 2 · MS2 evidence in the ledger | MAS-391, MAS-392, MAS-393 (decision), MAS-394 |
| 3 · The Sample view | MAS-395, MAS-396, MAS-397, MAS-398 |
| 4 · Energy-resolved analysis | MAS-399, MAS-400, MAS-401 (decision), MAS-402 |
| 5 · SDK, peaky, agent and docs | MAS-403, MAS-404, MAS-405, MAS-406, MAS-407 |

Milestone 0's tasks are Todo and the rest Backlog, ordered by blocking
relations. GitHub #2441 (MAS-265) and #1374 (MAS-94) are attached to the
issues that close them; the PRs say `Fixes #N` and `Fixes MAS-N`.

## Appendix C: references

- Schymanski, E. L. et al. (2014). Identifying small molecules via high
  resolution mass spectrometry: communicating confidence. *Environmental
  Science & Technology*, 48(4), 2097-2098. The level language the
  identification claims note uses; MS2 support here reaches level 4 at most.
- Senior, J. K. (1951). Partitions and their representative graphs.
  *American Journal of Mathematics*, 73(3), 663-689. The rules a formula and
  an unnamed loss remainder must satisfy.
- Kind, T. and Fiehn, O. (2007). Seven Golden Rules for heuristic filtering
  of molecular formulas obtained by accurate mass spectrometry. *BMC
  Bioinformatics*, 8, 105. The heuristic filter the formula layer already
  applies to a precursor.
- Olsen, J. V. et al. (2007). Higher-energy C-trap dissociation for peptide
  modification analysis. *Nature Methods*, 4, 709-712. HCD, the activation
  every group here is keyed by.
- peaky, `docs/MS2.md` (the MS2 module's rules, constants and known answers),
  and its PRs #78 to #85, listed in section 1.6.
