`ULTRA TRACE MASCOPE - DESIGN DOC - FAIR ROADMAP`

# FAIR: Review & Roadmap

## Purpose

This document reviews Mascope against the [FAIR principles](https://www.go-fair.org/fair-principles/)
(Findable, Accessible, Interoperable, Reusable) and proposes a phased plan.

It supersedes and expands section 7 of
[ionization_method_config.md](ionization_method_config.md), which introduced the
topic as a side-effect of the ionization redesign. That framing was right about
the important thing - **FAIR is mostly not a separate workstream, it is
acceptance criteria on work already planned** - but it was written from one
subsystem's vantage point, has since gone stale in several places, and dismissed
F and A too quickly. This document takes the whole-system view.

It is an engineering review and phased plan, not a user guide or a compliance
checklist to wave at reviewers.

---

## 1. Scope: three FAIR objects, three very different states

"Is Mascope FAIR?" is not one question. The repository contains three distinct
objects that FAIR applies to, and they are in wildly different shape:

| Object | What it is | State |
|---|---|---|
| **The software** | Mascope itself, judged by [FAIR4RS](https://doi.org/10.15497/RDA00068) | **Strong.** Little to do |
| **The reference data** | Mirrored public chemistry (`mascope_reference`) | **Good.** Provenance and licensing already modelled |
| **The user data** | What customers acquire, process and store - samples, matches, assignments | **Weak.** This is the actual gap |

Conflating them produces the two failure modes this plan avoids: claiming FAIR
compliance because `CITATION.cff` exists, or rebuilding provenance machinery
that `mascope_reference` already has.

**Everything below is about the third row unless stated otherwise.**

---

## 2. Audit

Status: **strong** / **partial** / **absent**, judged for user data.

### 2.1 Findable

| | Principle | Status | Evidence |
|---|---|---|---|
| **F1** | Globally unique, persistent IDs | **partial** | `gen_id()` mints 16-char nanoids ([id.py](../../server/backend/src/mascope_backend/db/id.py)). Unique per deployment, opaque, unnamespaced, non-resolvable. Two instances can mint the same id for different samples |
| **F2** | Rich metadata | **absent** | `Dataset` carries name, description, type, free-text `instrument`, icon, timestamps ([models.py](../../server/backend/src/mascope_backend/db/models.py), `Dataset`). No authors, contributors, keywords, license, funding, site, related publication |
| **F3** | Metadata include the data identifier | **absent** | The batch export emits no ids at all - not `sample_batch_id`, not `sample_item_id` ([export/service.py](../../server/backend/src/mascope_backend/api/controllers/sample/batches/export/service.py)). An exported spreadsheet cannot be traced back to the records that produced it |
| **F4** | Indexed in a searchable resource | **absent** | Search exists inside the app only. Nothing harvestable: no schema.org/JSON-LD, no DataCite, no sitemap, no OAI-PMH |

F3 is the cheapest and most embarrassing gap on this table. It is a column list.

### 2.2 Accessible

| | Principle | Status | Evidence |
|---|---|---|---|
| **A1** | Retrievable by identifier over a standard protocol | **strong** | HTTP/REST + Socket.IO, token auth, documented SDK |
| **A1.1** | Protocol open, free, universally implementable | **strong** | Plain HTTPS + JSON |
| **A1.2** | Protocol supports authn/authz *where necessary* | **strong** | Roles + `WorkspaceMember` + optional TOTP MFA ([authorization.md](../authorization.md)). FAIR explicitly permits this - "as open as possible, as closed as necessary". Commercial data is a legitimate closed case |
| **A2** | Metadata survive the data | **absent** | Deletes cascade end to end (`ondelete="CASCADE"` throughout). Delete the last file for an instrument and the instrument disappears; delete a dataset and every trace of it goes. No tombstones |

Two things are worth calling out that the sub-principle table hides:

- **OpenAPI is off in production.** `_docs_kwargs()` returns `openapi_url=None`
  outside dev ([fast.py](../../server/backend/src/mascope_backend/app/fast.py)).
  Defensible as hardening, but it means the API is machine-describable only
  where nobody needs it. A published, static, versioned OpenAPI document (in
  the docs site, not served by the backend) gets the interoperability without
  giving a directly-reachable backend recon value. *Closed since this review*
  (Phase 0 item 4): every deployment serves one at `/docs/openapi.json`,
  rendered from the routes when the frontend image is built
  ([openapi.py](../../server/backend/src/mascope_backend/openapi.py)).
- **There is no public read path, at all.** Not "closed by default" - closed
  by construction. A user who wants to open one dataset alongside a paper has
  no mechanism short of exporting spreadsheets by hand. That is the gap that
  actually bites scientific users, and it is section 4.

### 2.3 Interoperable

| | Principle | Status | Evidence |
|---|---|---|---|
| **I1** | Formal, shared knowledge representation | **absent** | No controlled vocabulary anywhere. `instrument` is free text, `String(64)`, on every table that names one ([models.py](../../server/backend/src/mascope_backend/db/models.py): `Dataset`, `SampleFile`, `InstrumentFunction`, `IonizationMode` and more). Polarity is a 4-char string. No PSI-MS CV terms |
| **I2** | Vocabularies that are themselves FAIR | **absent** | Follows from I1 |
| **I3** | Qualified references to other (meta)data | **partial** | **Split.** `ReferenceCompound` carries `inchikey`, `inchi`, `smiles`, `xrefs`, `source_native_id` and a per-record `license` ([models.py](../../server/backend/src/mascope_backend/db/models.py), `ReferenceCompound`) - genuinely good. `TargetCompound`, which is what users actually author, carries a nullable `cas_number` and nothing else ([models.py](../../server/backend/src/mascope_backend/db/models.py), `TargetCompound`). **The two are not linked** |
| **-** | Standard interchange formats | **absent** | Excel and CSV only. No mzML, no mzTab-M |

Two assets already in the tree make the interchange gap much cheaper than it
looks:

- **`to_mzml` exists in the pinned reader and is never called.** Confirmed
  present in the compiled `opentfraw` extension, alongside `mzml.rs` and **91
  embedded PSI-MS CV accessions** (`MS:1000449` LTQ Orbitrap, `MS:1000133` CID,
  `MS:1000016` scan start time, ...); `libraries/thermo` pins `opentfraw~=1.4`.
  Raw-file mzML export is closer to a route than a project, and the controlled
  vocabulary I1 asks for is already sitting in a dependency.
- **`pyteomics>=5.0.1` is already a direct dependency** of `mascope_tools`
  ([pyproject.toml](../../libraries/tools/pyproject.toml)), used today only
  for `Composition`/`calculate_mass`. It also reads and writes mzML and mzTab.
  Processed-result export needs no new dependency.

### 2.4 Reusable

| | Principle | Status | Evidence |
|---|---|---|---|
| **R1** | Rich, accurate, relevant attributes | **absent** | The batch export's "info" sheet is name, description, type, polarity, dataset, collections and four counts. No instrument, method, mass range, calibration, acquisition time, software version or engine version |
| **R1.1** | Clear data usage license | **absent** | Code is Apache-2.0; the demo bundle ships a `DATA_LICENSE`. **User datasets have no license field at all** |
| **R1.2** | Detailed provenance | **partial** | **Split by pipeline.** Peak-centric assignment records `engine_version` and a full `config` per run, plus per-assignment `provenance` and `alternatives` JSON ([models.py](../../server/backend/src/mascope_backend/db/models.py), `PeakAssignmentRun` and `PeakAssignment`) - this is genuinely good provenance. The classic targeted matching path records none of it |
| **R1.3** | Domain-relevant community standards | **absent** | No mzML, no mzTab-M, no MIAPE-MS-style reporting. Identification confidence uses a private tier vocabulary (`identified` / `candidate` / `below_assignability` / `unassigned`) where the field uses Schymanski levels |

R1.3's last point is already half-answered elsewhere:
[assignment_confidence.md](assignment_confidence.md) §P4 plans exactly this -
"assign a Schymanski/MSI identification level alongside the confidence". That is
a FAIR deliverable that is already someone's roadmap item. It should be
recognised as such, not duplicated.

---

## 3. What is already built

Do not rebuild these. Several are better than the audit above implies, because
they apply to the software and the demo bundle rather than to user data.

**The software (FAIR4RS) is close to exemplary:**

- Apache-2.0, public repository, semantic release tags, maintained CHANGELOG
- `CITATION.cff` with ORCIDs, affiliations, a Zenodo **concept DOI**
  (`10.5281/zenodo.21037634`), and a `preferred-citation` pointing at the paper
- Published Docker images, a one-command demo, MkDocs user + developer docs, an
  SDK on PyPI

**The demo bundle is a working reference implementation of FAIR data**, and is
the single most useful thing in the tree for this roadmap:

- Its own Zenodo DOI (`10.5281/zenodo.20929489`) and immutable versions
- `manifest.json` recording sha256 per artifact, `produced_with.mascope_version`,
  `produced_with.opentfraw_version`, and numeric tolerances
- A `DATA_LICENSE` deliberately separate from the code license
- Documented de-identification, and an end-to-end reproducibility test that
  asserts against checksummed golden outputs

**Everything the deposit-ready package in section 4 needs, the demo bundle
already does** - for one hand-built dataset, via the CLI. The work is
generalising it to any dataset, on demand, for users.

**Reference data provenance is already modelled:** `ReferenceSource` versions
each ingested public source with `name`, `version`, `license`, `record_count`,
`is_active` and `ingested_at`; adapters carry explicit license tags
(ChEBI `CC-BY-4.0`, COCONUT `CC0`, CompTox `public-domain`, HMDB
`hmdb-attribution`, ...). This is the pattern to copy for user data, not a
problem to solve.

---

## 4. The reframe: Mascope is not a repository, it is the on-ramp

The prior analysis concluded that F and A are "largely about publishing and
largely out of scope". Half right, and the half that is wrong is the important
half.

**Right:** Mascope should not become a data repository. It should not mint DOIs,
guarantee persistence, or host public data. Zenodo, PRIDE, MetaboLights and
GNPS exist, are funded to persist, and are what reviewers expect.

**Wrong:** F and A are not only about publishing. Mascope sits between the
instrument and the repository, and it is the **only** point in that chain where
the acquisition context still exists. Method file, calibration, instrument
configuration, ionization setup, engine version - a repository can only receive
those if Mascope hands them over. Today it cannot: a user depositing data to
accompany a paper has to hand-assemble the metadata from the UI, and the export
they would attach carries no identifiers, no instrument, and no versions.

> **The deliverable is a deposit-ready package**, not a repository. One
> command - one button - produces a self-describing archive of a dataset that a
> user can upload to Zenodo and cite.

This reframe is what makes the plan tractable, because that single feature pulls
all four letters along with it, and each pull is work that is independently
justified:

| Letter | What the package forces | Independently justified because |
|---|---|---|
| **F** | Stable exportable ids; a metadata record with authors, license, keywords | Support cannot currently trace an exported spreadsheet to a record |
| **A** | Metadata separable from data; tombstones | Deleting a dataset should not silently destroy the instrument definition |
| **I** | CV terms; standard formats in the payload | Users already ask to open their data in other tools |
| **R** | A provenance block: versions, instrument, method, config | Reproducibility, and answering "which build produced this number?" |

The DOI is then Zenodo's job, and the manifest links back to it.

---

## 5. Gaps ranked by leverage

| Rank | Gap | Why it ranks here |
|---|---|---|
| 1 | **No provenance block** | Prerequisite for exports, manifests, mzML metadata and reproducibility alike. Cheap. Nothing else is worth doing first |
| 2 | **Exports carry no identifiers** | A column list. Fixes F3 outright and is a support win today |
| 3 | **No dataset-level descriptive metadata or license** | Small schema addition. Blocks the package, and R1.1 is a hard requirement of every repository |
| 4 | **Metadata die with the data** | Already `Instrument`-table work in the ionization plan. FAIR is a second reason, not a new project |
| 5 | **No controlled vocabulary** | 91 PSI-MS CV terms already ship inside `opentfraw`. Low cost, unlocks I1/I2 |
| 6 | **`TargetCompound` has CAS and nothing else** | InChIKey + SMILES + a link to `ReferenceCompound`. The reference side is already built |
| 7 | **No standard export** | mzML is nearly free (§2.3). mzTab-M is real work and should follow the package, not lead it |
| 8 | **No deposit-ready package** | The capstone. Only sensible once 1-6 exist |

---

## 6. Plan

### Phase 0 - provenance and identifiers (days)

No schema change. Ships independently and is worth having regardless of FAIR.
Task-level handoff for agents: [handoff_fair_phase0.md](handoff_fair_phase0.md),
which also records Phase 0 as built and where the build departed from the items
below.

**Status (2026-10-01): built.** Item 4 shipped first, as the published OpenAPI
document; items 1-3 and 5 together after it.

1. **A provenance block, defined once.** A single serializer returning
   `mascope_version`, `engine_version`, `score_version`, generation timestamp,
   deployment id, and the ids and versions of everything that fed the result.
   One implementation, consumed by every export path.
2. **Ids in every export.** Add `dataset_id`, `sample_batch_id`,
   `sample_item_id`, `sample_file_id`, `target_compound_id` columns to the
   spreadsheet and CSV exports. Closes F3.
3. **Acquisition context in the batch export info sheet.** Instrument, method
   file, polarity, mass range, acquisition datetime (both stored forms), and
   calibration identity. The data is already on `SampleFile` and
   `InstrumentFunction`; the export simply never reads it.
4. **Publish a static OpenAPI document** to the docs site, generated in CI from
   the app. Keeps the prod backend closed while making the API machine-readable.
5. **Stamp the provenance block into SDK-returned frames** on `df.attrs`,
   matching the existing `df.attrs["run"]` convention in
   `load_assignments`. Provenance that survives into a notebook is provenance
   that survives into a paper.

### Phase 1 - describable, licensable datasets (weeks)

6. **Dataset descriptive metadata.** `license` (SPDX id), `authors` /
   `contributors` (with optional ORCID), `keywords`, `related_identifiers`
   (DOI/PID of a paper or a parent deposit), `funding`. Closes F2 and R1.1.
   Nullable throughout - never block an ingest on metadata a user does not have.
7. **Namespaced, exportable identifiers.** Keep nanoids as the storage key; add
   a deployment-scoped prefix so an exported id is globally unambiguous
   (`<instance-namespace>:sample_item:<nanoid>`). No migration, no id churn,
   and it makes cross-instance references possible later.
8. **Tombstones.** Deleting a dataset retains a minimal metadata record marked
   deleted. Closes A2 and stops instrument definitions vanishing with their
   last file.
9. **`TargetCompound` identity.** `inchikey`, `inchi`, `smiles`, and a nullable
   FK to `ReferenceCompound`. Populate opportunistically on the existing
   annotation path - `ReferenceCompound.inchikey` is already the cross-source
   dedup key. Closes I3 for user-authored chemistry.

### Phase 2 - vocabularies and standard formats (weeks)

10. **PSI-MS CV terms** for instrument model, source, analyser, detector and
    polarity, carried on `Instrument` (from the ionization plan's Phase 1) and
    emitted in exports. The 91 accessions embedded in `opentfraw` cover the
    Thermo side; Tofwerk needs a small hand-authored mapping.
11. **mzML export route** for raw and time-windowed sample data, over
    `to_mzml`. Requires an A/B against the current reader path before it is
    trusted - the same numeric check `ionization_method_config.md` §9 already
    asks for.
12. **Schymanski/MSI levels** alongside the private tier vocabulary. Already
    planned as [assignment_confidence.md](assignment_confidence.md) P4; this
    document only adds that it is also R1.3, and that the level belongs in the
    export and the package.
13. **mzTab-M export** for processed results. Real work, and the right shape
    for identifications + abundances. Sequenced after the package so its schema
    is driven by a concrete consumer.

### Phase 3 - the deposit-ready package (weeks)

14. **`mascope export package <dataset>`**, producing a self-describing archive:
    a metadata record (§6, item 6), the provenance block, checksummed data
    files, standard-format payloads where available, the target collections and
    ionization setup used, and a human-readable README. **Model it on the demo
    bundle's `manifest.json`** - same fields, same checksum discipline,
    generalised from one hand-built bundle to any dataset on demand.
15. **RO-Crate as the manifest format.** JSON-LD, schema.org-based, and the
    de-facto standard for exactly this. Gets I1/I2 in the manifest layer as a
    side-effect of a format choice rather than a vocabulary project.
16. **A UI path**, not just a CLI - "Prepare for publication" on a dataset.
    Scientists deposit data; they do not run CLIs.
17. **Zenodo deposition** (optional, last). The API is simple, and by this
    point the package is the payload. Explicitly *not* a prerequisite: the
    package is valuable on its own, and a manual upload is a fine v1.

### Deliberately out of scope

- Mascope minting or resolving DOIs
- Mascope guaranteeing persistence of anything
- A public data portal, federation, or cross-instance search
- Retrofitting provenance onto historical results - stamp forward, and let
  absence be legible

---

## 7. Sequencing against existing workstreams

FAIR competes for the same engineers as three live efforts. It should be
attached to them, not scheduled against them.

| Workstream | Relationship |
|---|---|
| **Ionization/method redesign** ([ionization_method_config.md](ionization_method_config.md)) | **Hard prerequisite** for most of I and R. Its `Instrument` table (Phase 1) closes A2's root cause, and its versioned `IonizationSetup` is what a provenance block points at. Phase 2 item 10 hangs directly off it |
| **Peak-centric assignment epic** | **Already the model to copy.** `engine_version` + `config` + per-row `provenance` is the pattern; the classic matching path is what lacks it. Do not invent a second provenance design |
| **Identification confidence** ([assignment_confidence.md](assignment_confidence.md)) | **Owns Phase 2 item 12.** Schymanski levels are already P4 there. FAIR adds a reason, not a task |
| **Public database integration** ([public_database_integration.md](public_database_integration.md)) | **Already delivered the I3 half that is done.** Phase 1 item 9 is a small extension of its annotation path |

The practical consequence: **Phase 0 can start now and depends on nothing.**
Phase 1 items 6-8 are independent. Phase 1 item 9 and all of Phase 2 should
follow the ionization Phase 1 landing on `develop`.

---

## 8. Decisions needed

> **Settled 2026-08-20:** all five were decided, each per the recommendation
> below. Kept in this form so the reasoning stays with the decision;
> [handoff_fair_phase0.md](handoff_fair_phase0.md) states what Phase 0
> consumes from them.

These are product calls that change the schema or the shape of the work.

1. **Ambition.** Does this stop at internal reproducibility (Phases 0-1), or
   go through to deposit-ready packages (Phase 3)? This is
   `ionization_method_config.md` §10.5 restated, and it is still open.
   **Recommendation: commit to Phase 3**, because the paper is out and users
   will be asked by reviewers to deposit data. Phases 0-1 are worth doing
   under either answer, so the decision can be deferred without blocking.
2. **License granularity.** Dataset-level only, or also per sample batch? A
   dataset spanning licenses is a real case in collaborative work.
   **Recommendation: dataset-level, nullable, inheritable.** Revisit on demand.
3. **Identifier namespace.** What names a deployment - the instance URL, a
   configured `deployment_id`, or a registered prefix? Affects whether ids
   collide across customer instances.
   **Recommendation: a configured `deployment_id`**, defaulting to a generated
   value, so air-gapped deployments work and there is no registry to run.
4. **Attribution model.** Are dataset authors free-text, Mascope users, or
   ORCID-backed identities? Repositories want structured contributors, but
   forcing every user to have an ORCID is hostile.
   **Recommendation: structured but optional** - name required, ORCID and
   affiliation optional, seeded from the Mascope user.
5. **Does a public read path exist at all?** A shareable read-only dataset link
   is the natural companion to a deposit-ready package, and it is a
   security-surface decision, not a metadata one. Not required for any phase
   above; call it separately.

---

## 9. How we know it worked

Acceptance criteria, in preference to a compliance score:

- An exported spreadsheet names the Mascope version, engine version,
  instrument, method and calibration that produced it, and every row can be
  traced back to a record id. *(Phase 0)*
- Deleting a dataset leaves a tombstone, and no instrument definition
  disappears with its last file. *(Phase 1)*
- A dataset can carry a license, authors and a related publication DOI, and a
  target compound resolves to an InChIKey. *(Phase 1)*
- A sample exports as valid mzML that a third-party tool opens. *(Phase 2)*
- One command turns a dataset into an archive that uploads to Zenodo and, on
  download, tells a stranger what was measured, how, under what license, by
  whom, and with which software version. *(Phase 3)*

The last one is the real test. If a reviewer can reproduce a figure from the
archive without contacting the authors, the work is done.

---

## 10. Summary

- **The software is already FAIR**; the user data is not. Do not confuse the
  two, and do not claim the first as the second.
- **F and A are in scope after all**, not as publishing but as the metadata and
  identifier work that lets someone else publish.
- **The demo bundle is the reference implementation.** Generalise it.
- **Phase 0 is days of work, depends on nothing, and is worth doing on its own
  merits.** Start there.
- **The rest is acceptance criteria on the ionization redesign, the assignment
  epic and the confidence workstream** - largely not new engineering, but a
  reason to finish existing engineering in a particular way.
