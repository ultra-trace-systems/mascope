`ULTRA TRACE MASCOPE - DESIGN DOC - PUBLIC DATABASE INTEGRATION`

# Public Chemistry & Spectral Database Integration: Design & Plan

## Purpose

Mascope assigns chemistry to peaks entirely from first principles: given an m/z,
it enumerates valence-legal elemental formulas and scores them by mass error and
isotope pattern. It is not connected to any external chemistry or mass-spectral
database, so an assigned formula carries no identity (name, structure, prior
likelihood of existing) and no independent MS/MS evidence.

This document plans the integration of **free-to-use public databases** to add
two capabilities on top of the existing engine:

1. **Formula -> identity annotation** (MS1): map an assigned formula to the
   known compounds that share it - name, structure, cross-references, and a
   prior that the formula corresponds to a real substance.
2. **MS/MS spectral matching** (MS2): score measured fragmentation spectra
   against open reference libraries.

It is an engineering design and phased plan, not a user guide. It records the
current state as-is, the databases to be used, the proposed storage and
ingestion, and the sequencing of work. It is a companion to
[peak_assignment_paradigm.md](peak_assignment_paradigm.md): the reference
compound set specified here is the "curated known compositions" that document's
Stage A is designed to consume.

---

## 1. Current state (as-is)

Composition assignment is **de novo only**. `find_compositions(mz, config)`
([finder.py](../../libraries/tools/src/mascope_tools/composition/finder.py))
enumerates neutral formulas within a ppm tolerance across ionization mechanisms
and scores them; `api/new/cheminfo`
([service.py](../../server/backend/src/mascope_backend/api/new/cheminfo/service.py))
exposes this for a single m/z, optionally matching the candidates against a
sample's isotope envelopes. Formula normalization and Hill ordering live in
[composition/utils.py](../../libraries/tools/src/mascope_tools/composition/utils.py)
(`to_hill_order`, `normalize_formula_with_isotopes`, `parse_composition`).

Two facts frame the integration:

1. **There is no reference set of known compounds anywhere in the system.** A
   returned formula is just a string plus a mass and a score. `TargetCompound`
   ([models.py](../../server/backend/src/mascope_backend/db/models.py)) already
   carries `target_compound_name` and `cas_number` columns, but they are populated
   only for user-curated targets - the fields exist and sit empty for everything
   discovered untargeted. Those columns are the natural landing spot for
   annotation.

2. **MS2 data is already extracted but never compared to a library.** Averaged
   MS2 centroids per parent peak are available
   ([ms2.py](../../libraries/sdk/src/mascope_sdk/resources/ms2.py)); the missing
   pieces are a reference library and a spectral-similarity scorer.

The two capabilities attach at different points and can be sequenced
independently: MS1 annotation enriches `find_compositions` results and is small;
MS2 matching is a separate, larger subsystem.

---

## 2. Databases

All entries below are genuinely free to use. Access mode and license per record
matter for storage and provenance (Section 3).

### 2.1 Structure / identity (MS1 annotation)

| Database | Content | Access | License |
| --- | --- | --- | --- |
| **PubChem** | ~120M compounds; name, SMILES, InChIKey, exact & monoisotopic mass, formula, xrefs | [PUG-REST](https://pubchem.ncbi.nlm.nih.gov/docs/pug-rest) (<=5 req/s) + FTP bulk dumps | Public domain |
| **EPA CompTox / CCTE** | ~1.2M environment-relevant chemicals, "MS-ready" structures, formula & mass search | [CCTE REST APIs](https://api-ccte.epa.gov) + bulk downloads | Public domain |
| **ChEBI** | ~200k curated biologically-relevant molecules + ontology | SDF/FTP dump, API | CC BY 4.0 |
| **HMDB** | ~250k human metabolites, some predicted MS/MS | XML/SDF bulk download | Free/open (attribution; verify commercial terms) |
| **LIPID MAPS (LMSD)** | ~48k curated lipids, systematic nomenclature | SDF download | CC BY 4.0 |
| **COCONUT** | ~400k natural products | Bulk download, API | CC0 / open |
| **NORMAN Suspect List Exchange** | Curated suspect lists (PFAS, pesticides, ...) | Downloadable lists via CompTox | Open |

### 2.2 MS/MS spectral libraries (MS2 matching)

| Library | Notes | Access | License |
| --- | --- | --- | --- |
| **MassBank Europe** | Open, vendor-neutral reference spectra | [GitHub releases](https://github.com/MassBank/MassBank-data/releases) + [Zenodo](https://doi.org/10.5281/zenodo.3378723), records API | CC (open) |
| **MoNA** | Metadata-rich, large; includes in-silico predicted spectra | [MSP/SDF/JSON](https://mona.fiehnlab.ucdavis.edu/downloads) + REST API | Mixed per record - must filter |
| **GNPS / MassIVE** | Community MS/MS, natural products | [MGF download](https://gnps.ucsd.edu/ProteoSAFe/libraries.jsp) | Contributed spectra default CC0 |

### 2.3 Deliberately excluded

Not free-to-use, so out of scope unless a licensing decision changes: **NIST**
MS library (paid), **mzCloud** (browse-only, not redistributable), **METLIN**
(restricted), **CAS**/SciFinder (paid), **ChemSpider** (non-commercial key).
**KEGG** is conditional - the web/REST API is free for academic use but bulk FTP
is a paid subscription and commercial use is restricted; treat it as
conditional, not free.

### 2.4 Priority for this codebase

The peaky skill and the atmospheric/environmental focus (HOM, CIMS, PFAS)
make the high-value set: **PubChem** (universal coverage), **EPA CompTox +
NORMAN** (environmental suspect screening - the direct PFAS/contaminant use
case), and **MassBank Europe** (open MS/MS). Start there; add the rest via the
same adapter pattern.

---

## 3. Architecture

### 3.1 Local mirror, not live API

The core decision is mirror vs. live query, and for Mascope it is **local
mirror** for batch/scoring paths, with live API reserved for occasional
interactive lookups:

- PUG-REST is capped at 5 req/s and adds latency and an external failure mode
  into a hot path. The peaky pipeline scores thousands of peaks per sample across
  many samples - live per-peak lookups are not viable there.
- A mirror lets Mascope **index by canonical formula and by monoisotopic mass**,
  so annotation is an indexed lookup and known compounds can be used to
  pre-filter candidates *before* the expensive isotope scoring, not only to label
  results after it.
- Reproducibility (a value already baked into the demo dataset and the
  `PeakAssignmentRun` versioning) requires a pinned, versioned snapshot of the
  reference data, which a live API cannot give.

### 3.2 New library: `mascope_reference`

A new library under `libraries/` (sibling to `mascope_tools`, `mascope_chem`,
`mascope_match`), owning ingestion and querying so source-specific quirks never
leak into the backend. Two responsibilities:

- **ETL adapters**, one per source, each normalizing a source dump to a common
  record: canonical formula in Hill order (reuse `to_hill_order` /
  `normalize_formula_with_isotopes` so reference and assigned formulas compare
  identically), monoisotopic mass, InChIKey, name, source, source-native id,
  cross-references, and a per-record license tag.
- **Query interface** - `by_formula(formula)` and `by_mass_window(mz, ppm)` -
  returning normalized records, consumed by the backend.

### 3.3 Storage (existing Postgres)

Two tables, added via the existing Alembic flow
([alembic/versions](../../server/backend/alembic/versions)) - no new
infrastructure.

**`reference_source`** - one row per ingested source + version: name, release
version/date, license, record count, ingestion timestamp. Makes an annotation
reproducible and attributable.

**`reference_compound`** - one row per compound per source:

| column | meaning |
| --- | --- |
| `reference_compound_id` | primary key |
| `reference_source_id` | FK to `reference_source` (provenance + license) |
| `formula` | canonical neutral formula, Hill order (indexed) |
| `monoisotopic_mass` | computed on ingest (indexed) |
| `inchikey` | dedup key across sources (indexed) |
| `name` | preferred name |
| `smiles`, `inchi` | structure (nullable) |
| `source_native_id` | e.g. PubChem CID, DTXSID, ChEBI id |
| `xrefs` (JSON) | cross-references to other sources |
| `license` | per-record license tag |

Index on `formula` and on `monoisotopic_mass`; `inchikey` is the cross-source
dedup key. The **per-record `license`** column is load-bearing: MoNA and others
are mixed-license, and if Mascope is ever used commercially, annotations must be
filterable by license. Keeping one row per (compound, source) preserves
provenance; a de-duplicated view can collapse on InChIKey when a single answer is
wanted.

### 3.4 Ingestion via the CLI

A `mascope reference sync <source> <dump> --version <tag>` Typer command
([tooling/cli](../../tooling/cli)) that runs the adapter over an
already-downloaded dump (fetches happen out of band - the dumps are large and
some sources need registration), upserts into `reference_compound`, and records
a `reference_source` row. Each load is versioned so a past annotation can be
reproduced. This mirrors how the demo dataset is built and keeps ingestion out
of the request path.

### 3.5 Wiring MS1 annotation into the composition flow

In `retrieve_compositions_by_mz`
([service.py](../../server/backend/src/mascope_backend/api/new/cheminfo/service.py)),
after each `Result` is built, do an indexed `by_formula` lookup and attach known
identities (name, structure, xrefs, source, license). This is **purely
additive** - it enriches results without touching the de novo scoring. It also
enables a "known compounds only" prior, which for suspect screening
(PFAS/contaminants) is frequently what the analyst wants and what CompTox/NORMAN
exist to serve.

For the peak-centric engine, the same reference set is the concrete instance of
the "curated known compositions" Stage A in
[peak_assignment_paradigm.md](peak_assignment_paradigm.md) is written to consume:
a peak whose winning formula matches a `reference_compound` gets `source=database`
and populated identity, so annotation and the two-stage assignment reinforce each
other rather than duplicating logic.

### 3.6 MS2 spectral matching (later phase)

Ingest MassBank/MoNA/GNPS spectra into a `reference_spectrum` table keyed by
precursor m/z + formula + InChIKey (carrying peak lists and license), then add a
spectral-similarity scorer (cosine / modified cosine over aligned centroids) that
runs against the extracted MS2 centroids
([ms2.py](../../libraries/sdk/src/mascope_sdk/resources/ms2.py)). This is a
distinct, larger subsystem and is sequenced after MS1 annotation is proven.

---

## 4. Phased plan

Each phase is intended to land independently and leave the system shippable.

### Phase 0 - Foundations
- New `mascope_reference` library skeleton: common record model, `by_formula` /
  `by_mass_window` query interface, formula normalization reuse.
- `reference_source` + `reference_compound` tables + Alembic migration.
- No ingestion yet - schema, query interface, and read path only.

### Phase 1 - PubChem + EPA CompTox (highest ROI)
- ETL adapters for PubChem and CompTox bulk dumps -> normalized records.
- `mascope reference sync` CLI command with versioned loads.
- Wire `by_formula` annotation into `retrieve_compositions_by_mz`. Proves the
  full path end to end.

### Phase 2 - Curated & suspect-screening sources
- Adapters for ChEBI, HMDB, LIPID MAPS, COCONUT, and NORMAN suspect lists.
- InChIKey-based cross-source dedup and a collapsed view.
- "Known compounds only" prior surfaced through the cheminfo path.

### Phase 3 - Peak-centric integration
- Expose `reference_compound` as the known-composition set for Stage A of the
  peak-centric engine; annotate `source=database` assignments with identity.

### Phase 4 - MS/MS spectral matching
- `reference_spectrum` table + MassBank/MoNA/GNPS adapters (license-filtered).
- Spectral-similarity scorer against extracted MS2 centroids; surface hits.

### Phase 5 - Productization
- UI/SDK surfaces for annotations and spectral hits.
- Refresh cadence for source dumps; version pinning for reproducibility.
- Performance: keep annotation an indexed lookup; bound live-API fallback.

---

## 5. Key design decisions

1. **Mirror vs. live API.** *Recommend local mirror* for all batch/scoring
   paths, with live PUG-REST reserved for occasional interactive one-off
   lookups. Rate limits, latency, an external failure mode, and reproducibility
   all argue against live query in the hot path.

2. **New `mascope_reference` library vs. backend module.** *Recommend a new
   library.* Ingestion and source quirks are self-contained and reusable by CLI,
   backend, and peaky alike, matching the existing `libraries/` layout.

3. **One row per (compound, source) vs. pre-merged.** *Recommend per-source rows*
   with InChIKey dedup as a view. Preserves provenance and per-record license;
   avoids lossy early merging.

4. **License handling.** *Recommend a per-record license tag* carried from
   ingest through to results, so annotations are always attributable and
   filterable - required before any commercial use and cheap to add up front.

5. **MS1 before MS2.** *Recommend sequencing annotation first.* It is additive,
   small, and independently valuable; spectral matching is a separate subsystem
   that should not gate it.

---

## 6. Scope summary

The genuinely new engineering is:

- the `mascope_reference` library (record model + query interface + adapters),
- the `reference_source` / `reference_compound` tables + migration + sync CLI,
- the additive annotation hook in the cheminfo path,
- and, later and separately, `reference_spectrum` + a spectral-similarity scorer.

Everything else reuses what exists: formula normalization from
`mascope_tools.composition`, the empty identity columns on `TargetCompound`, the
Alembic and CLI flows, and the Stage A hook already anticipated by the
peak-centric paradigm. The design keeps de novo assignment untouched and layers
known-compound identity and open spectral evidence on top of it.
