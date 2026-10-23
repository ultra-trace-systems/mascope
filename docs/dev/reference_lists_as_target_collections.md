`ULTRA TRACE MASCOPE - DESIGN DOC - REFERENCE LISTS AS TARGET COLLECTIONS`

# Reference lists as target collections: design

- **Status:** design
- **As of:** 2026-10-23
- **Tracked in:** [Reference lists as target collections](https://linear.app/ultra-trace/project/reference-lists-as-target-collections-63b440eeab90) (Linear; decisions MAS-447 to MAS-452; phases 0 to 3 filed 2026-10-23 as MAS-453 to MAS-467)
- **What:** Curated reference lists usable as target collections, pinned to a version and materialized as ordinary compounds, with target management moved onto the batch.

Status: **design, nothing built** (2026-10-23). Section 3 holds the decisions
this note is built on, each with its reason; section 9 holds the calls the
plan owner still has to make, each with a leaning, filed as the Decision
issues MAS-447 to MAS-452. Code anchors were read on `develop` on the same
date and are named where a claim rests on them.

## Picking this up

The feature, as a person meets it:

- **A curated list is a target collection.** The reference lists Mascope
  ships, and any list an operator loads, can be attached to a batch like a
  collection someone built by hand. Matching scores every compound of the
  list in every sample, the Batch chart plots them, and the collection tag
  carries the alarm colour.
- **Lists are visible in the app.** A page lists what is loaded: each list's
  name, version, licence, polarity, how many species, and whether it is in
  use as targets. Today the only way to learn a list exists is the CLI.
- **Attaching targets is one step.** From the batch's own collections pane a
  person ticks the collections to use, saves, and matching runs. Today that
  is a right-click, a dialog, a save, and a click on a status icon.
- **Discovery promotes to monitoring.** A compound peak assignment found and a
  list named is added to a collection from the row it was found on.

The design in one paragraph. A target collection may be **backed by a
reference source**: it records the source's name and the version it was
built from, and its membership is **materialized** as ordinary target
compounds, ions and isotopes through the create path every collection uses,
so the matcher, the match tables, calibration and the SDK see nothing new.
Membership is read-only; a newer list version is an **explicit update** that
flags the attached batches for a rematch as any collection edit does. Target
management moves to the batch: the collections pane attaches, and saving an
attachment runs the refresh. A read-only **Reference lists** page is where
a list is seen and turned into targets.

| Phase | Content | State |
|---|---|---|
| 0 | The reference sources API and the Reference lists page, read-only | open |
| 1 | List-backed collections: schema, materialization, update, SDK | open |
| 2 | Target management on the batch: attach from the pane, save runs matching, promote from a row | open |
| 3 | Convergence: environments suggest list collections, seeded profile collections share the mechanism, results read off assignments | open; waits on other plans |

Related designs: `public_database_integration.md` (the reference tables, and
in its phase 3 the Stage A seam this note leaves alone), the user page
`docs/user/guides/reference-lists.md` (the lists and their headers; the
authoring note it replaced is retired), `peak_assignment_paradigm.md`
(section 3.2 the same seam from the engine's side, section 5.1 targeted
analysis as a view over assignments), `sample_filters_and_metadata.md`
section 4.6 (environments selecting lists), `chemistry_profiles.md`
section 4.4 (the seam from the profile side), `ux_review_2026_10.md` (the
dialog grammar and the retirement decision). Section 11 says how this
design touches each.

---

## 1. Why

Mascope holds two curated models of a compound and they do not meet where a
person works. A **target collection** is what a person builds and attaches to
a batch, and it is the only thing the targeted matching path reads. A
**reference list** is a versioned, licensed compilation an operator loads,
and the only thing that reads it is Stage A of peak assignment, which
matches its formulas and names them on assignment rows. Thirteen lists ship
with the product, from a 573-species monoterpene HOM list to six reactive
iodine species, and none of them can be a target.

The consequences, as users report them:

- A person who wants to monitor the HOM list in a campaign batch has to
  rebuild it by hand, pasting from a spreadsheet, and loses the list's
  provenance, licence and version in the process.
- Target management takes many clicks for the one thing a person does most,
  attaching an existing collection to a batch and seeing the scores. The
  collections pane only lists; creating, editing membership and attaching are
  three modes of one 1500-line dialog reached from two different context
  menus; and a change flags the batch but does not rematch until a status
  icon is clicked.
- The lists themselves are invisible: no route lists them, no page shows them
  (section 2.4).

The two models are converging in the engine already (Stage A reads both:
`public_database_integration.md` phase 3). This note converges them where
the person stands: in the collections a batch is matched against.

---

## 2. Current state

### 2.1 Two models of a compound

| | Target collection | Reference source |
|---|---|---|
| Tables | `target_collection` -> `target_compound` (shared) -> `target_ion` -> `target_isotope`, with `target_collection_in_sample_batch` and `target_compound_in_target_collection` as junctions | `reference_source` (one row per loaded version) -> `reference_compound` |
| Scope | a workspace, or global when `workspace_id` is null; a global one needs the global admin role to create ([target_collections_routes.py](../../server/backend/src/mascope_backend/api/routes/target/collections/target_collections_routes.py), the admin check on create and on a move to global) | the deployment; no workspace |
| Authored by | a person, in the dialog, the SDK or a paste | an operator, by `mascope reference seed` or `sync`; shipped lists live in [libraries/reference/src/mascope_reference/lists/](../../libraries/reference/src/mascope_reference/lists/) |
| Membership | explicit, edited row by row | the active version of the source; replaced whole by a new version |
| Ions and isotopes | persisted per compound, for every ionization mechanism, by `create_target_ions` and `generate_target_ions_from_composition` ([target_ions_compute.py](../../server/backend/src/mascope_backend/api/controllers/target/lib/compute/target_ions_compute.py)) | none persisted; Stage A expands formulas at run time |
| Identity | formula, name, CAS, and since MAS-28 InChIKey, InChI, SMILES | formula, name, InChIKey, InChI, SMILES, `source_native_id`, `xrefs` (CAS, DOI, tags), a per-record licence |
| Carries | per-ion match parameters (`target_ion.filter_params`) | a window, a radical allowance and a polarity on the source row ([scope.py](../../libraries/reference/src/mascope_reference/scope.py)) |

### 2.2 Where they meet: Stage A, one direction

Stage A of peak assignment unions two frames: the batch's target library,
read through the junctions by `_fetch_known_target_isotopes`, and the
**reference mirror**, every active source's admitted formulas expanded into
isotope rows by `_build_reference_isotopes_df` and cached per deployment
state (`_fetch_reference_known_isotopes`, `_combine_known_isotopes`, all in
[service.py](../../server/backend/src/mascope_backend/api/new/peak_assignments/service.py)).
The library comes from `iter_known_compositions` in
[known.py](../../libraries/reference/src/mascope_reference/known.py). A
mirror row carries `reference_identities` and no `target_compound_id`, so a
reference match becomes an assignment with a name in its **listed as**
column and nothing else: no `match_*` row, no collection score, no Batch
chart series, no alarm. Where a target and a mirror row read the same ion on
the same peak, the target wins and the mirror row is dropped as a copy
(`_reference_copies` in
[engine.py](../../server/backend/src/mascope_backend/api/new/peak_assignments/engine.py)),
which is the precedence the seam was designed with: the target library owns
the target id, reference identity is attached and never matched on
(`public_database_integration.md` phase 3, `peak_assignment_paradigm.md`
section 3.2).

### 2.3 What the matching path reads

The targeted matcher reads target isotopes through
`target_collection_in_sample_batch` alone
([target_isotopes_fetch.py](../../server/backend/src/mascope_backend/api/controllers/target/lib/fetch/target_isotopes_fetch.py)),
persists `match_isotope`, `match_ion`, `match_compound` and
`match_collection` rows keyed to the target rows, and aggregates them up to
the collection and sample scores the pane and the Batch chart draw. It runs
on `POST /api/match/compute/batch/{id}` (add new targets only) and
`/rematch/batch/{id}` (remove and recompute)
([match_routes.py](../../server/backend/src/mascope_backend/api/routes/match/match_routes.py)).
Editing a collection's membership sets its batches to `rematch`
(`update_target_collection` in
[target_collections_controller.py](../../server/backend/src/mascope_backend/api/controllers/target/collections/target_collections_controller.py),
step 6); attaching a collection to a batch does the same. Nothing here can
read a `reference_compound` row.

### 2.4 Lists in the app

Nothing. The backend read path,
[api/new/reference/service.py](../../server/backend/src/mascope_backend/api/new/reference/service.py),
serves annotation (`annotate_formulas`, `known_listings`) and registers no
router. The lists are loaded by the developer CLI
([cmd/reference/main.py](../../tooling/cli/src/mascope_cli/cmd/reference/main.py))
or the backend scripts `reference_seed` and `reference_sync` inside the
container, and inspected with `mascope reference status`. In the UI a list
appears only as the **listed as** name on an assignment row
([referenceListings.js](../../server/frontend/src/lib/referenceListings.js)).

### 2.5 Target management as it is clicked

- The lower-left pane ([MatchCollectionTable.vue](../../server/frontend/src/lib/panes/PaneBrowserMatch/MatchCollectionTable.vue))
  lists the focused batch's collections with their scores and one action,
  create. Attaching an existing collection is **Edit batch targets** on the
  batch's context menu, which opens
  [DialogBatchOp.vue](../../server/frontend/src/lib/dialogs/DialogBatchOp/DialogBatchOp.vue)
  on its Targets tab ([PaneSelectTargets.vue](../../server/frontend/src/lib/dialogs/DialogBatchOp/PaneSelectTargets.vue)),
  or **Edit batches** on the collection's context menu, which opens
  [DialogTargetCollectionOp.vue](../../server/frontend/src/lib/dialogs/DialogTargetCollectionOp.vue)
  in its `update_batches` mode.
- That dialog is also create and edit: a Compounds tab with a two-pane
  selector (paste, import from another collection or *All compounds*, create
  new) and a Batches tab. It merges compounds by CAS or by formula
  (`findExistingCompound` in [chem.js](../../server/frontend/src/lib/chem.js)),
  which MAS-419 is changing to respect a differing InChIKey.
- Saving flags the batch; the person then clicks the status icon or
  **Process -> Refresh matches**. The user guide spells this out as a
  separate step ([target-collections.md](../user/guides/target-collections.md),
  "Run matching").
- A compound can be added to a collection from an ion row and from the peak
  search ([PopoverTargetCompoundAdd.vue](../../server/frontend/src/lib/dialogs/PopoverTargetCompoundAdd.vue),
  used by `MatchIonTable.vue` and `PanePeakSearch.vue`), but not from an
  assignment or batch-peak row, where the list's name is shown.

### 2.6 Work already planned beside this

- **Environments select lists** (MAS-336, MAS-342, `sample_filters_and_metadata.md`
  section 4.6): `reference_source` gains `applies_to_contexts` and
  `always_active`, and Stage A reads a list for the regimes of the batch's
  samples. It selects what Stage A reads; it does not make a list a target.
- **Seeded calibrant and diagnostic collections per chemistry profile**
  (MAS-312): system-owned collections created at start beside each seeded
  profile. The same shape as a list-backed collection: owned by the system,
  global, with a known origin.
- **Find structure** (MAS-357): a target compound's identity filled from the
  reference rows that share its formula. The reverse bridge of this note,
  one compound at a time.
- **CSV import and export of a collection** (GitHub #747, #748): a file
  format for moving a collection between instances. A list file is already
  such a format; section 6 says how the two relate.
- **The Match tab's future** (MAS-83, MAS-162): maintained, frozen or
  retired once assignment is the default. Section 4.6 says why this design
  does not wait on it.
- **Isomers in the dialog** (MAS-419), **the type constraint** (#1491) and
  **the global admin requirement** (#1909) each touch a rule this design
  leans on; section 10 names each.

---

## 3. Decisions

| # | Decision | Reason |
|---|---|---|
| 1 | A list reaches the targeted path **as a collection**, not by teaching the matcher to read reference rows. | The matcher, the four `match_*` tables, calibration and the SDK all key on `target_isotope` rows. A second expansion path in the matcher would duplicate Stage A's mirror and leave match rows with no target to point at. |
| 2 | A list-backed collection is **materialized**: real compound, ion and isotope rows, created by the path a pasted collection uses. | Zero changes downstream. The cost is the one-time cost of pasting the list, which the dialog already pays for a collection of any size. |
| 3 | A list-backed collection **pins the source version** it was built from. Membership is **read-only** in the app; it changes only by an explicit *Update to version X*. | A collection is what a batch was matched against. A list that changed under a batch would change results no one asked for. The pin names what the results mean, and the update flags the batches as any edit does. |
| 4 | A list-backed collection is **global** (`workspace_id` null), is **created by a global admin or the system**, and is **attached by an editor** of the batch. | A reference source is deployment-wide, so its collection is. The admin rule for global collections already exists (#1909 is the complaint about it, section 10). Attaching is the per-batch act and keeps the editor role it has. |
| 5 | Its **type is TARGETS**. Calibrant and diagnostic collections stay with MAS-312. | A list is a set of analytes. A site that wants a list as calibrants forks it (decision 7) and changes the type. |
| 6 | **Compounds stay shared.** Materialization reuses an existing compound where the identity rule says it is the same (InChIKey, else CAS, else name and formula, the MAS-419 rule), and creates the rest. | "Compounds are shared reference data" is the product's stance, and a list compound picked into a person's own collection is a feature. A second compound table for list compounds would be the two-model problem again, one level down. |
| 7 | **Fork is the existing import.** A person who wants to edit a list's membership creates a collection and imports from the list-backed one. No copy route. | The dialog's *Import existing* with a collection as source already does this. |
| 8 | **The licence gate applies.** A source outside `reference_licenses` is not offered as targets and is not materialized. | A deployment that declined to match a source in Stage A has not accepted its terms for the targeted path either. |
| 9 | **Saving an attachment runs matching.** The Targets save calls `/api/match/compute/batch/{id}` after the attach; membership edits keep the flag only. | Attaching is the common act and is always followed by the refresh; a membership edit is often one of several. |
| 10 | **Nothing changes in Stage A.** A list-backed collection attached to a batch and the same list active in the mirror produce one reading per peak: the target row wins and the mirror copy is dropped, which is today's precedence. | Section 2.2. The seam phase 3 of `public_database_integration.md` owns stays its own. |
| 11 | **The seed does not create collections.** The Reference lists page, the SDK and the API do. | Materializing the HOM list writes tens of thousands of isotope rows a small site may never match against. Loading a list is an operator's choice already ("nothing loads them unasked"); using one as targets is a second choice. |

---

## 4. The model

### 4.1 A list-backed collection

`target_collection` gains two nullable columns:

| Column | Type | Meaning |
|---|---|---|
| `reference_source_name` | `String(64)`, indexed | the source this collection is built from; null for a collection someone built |
| `reference_source_version` | `String(128)` | the version it was materialized from |

The pair is the source row's natural key (`reference_source.name`,
`.version`), carried as text rather than as a foreign key so the pin
survives `reference sync --prune` deleting the old version row: a collection
whose version is no longer loaded still says what it was. A collection is
list-backed when `reference_source_name` is set. Its name defaults to the
list's label (from the shipped catalogue, `catalogue()` in
[seed.py](../../libraries/reference/src/mascope_reference/seed.py), when the
source is a shipped list; the source name otherwise) and its description to
the list's citation. Both are editable. Type `TARGETS`, `workspace_id` null.

No other table changes. The junction rows are ordinary; the compound rows
are ordinary.

### 4.2 Materialization

A backend service, `materialize_reference_collection(source_name, version)`,
beside the collection controller:

1. Reads the admitted records of the source version through the library's
   query path (`active_join` in
   [query.py](../../libraries/reference/src/mascope_reference/query.py) for
   the active version; the version row by name and version otherwise), under
   the licence gate (`reference_license_gate()` in
   `peak_assignments/service.py`, lifted to a shared place).
2. Maps each record to a `TargetCompoundBase`: formula, name, InChIKey,
   InChI, SMILES, CAS from `xrefs["cas"]` or `xrefs["casrn"]`. A record
   without a name gets its formula as its name, as the dialog does for a
   pasted formula column.
3. Creates the collection and its membership through
   `create_target_collection` with `target_compounds_create`, which runs
   compound dedup and `create_target_ions` for every ionization mechanism.
   A formula the generator rejects is skipped and logged, as it is for a
   pasted row (the `assert_valid_formula` guard in `target_ions_compute.py`).
4. Records `reference_source_name` and `reference_source_version`.

It runs as a background task with a notification, like a rematch: the HOM
list is 573 species times every mechanism through IsoSpec, and a request
that may take tens of seconds should not hold the dialog. The notification
names the collection and how many compounds were created, reused and
skipped.

The radicals of an opt-in list (the monoterpene RO2 list) pass through the
same generator Stage A already runs them through, so no chemistry changes.

### 4.3 Versions and updates

A list-backed collection shows the version it pins. When the source's active
version differs, the collection carries **update available** in the pane,
the dialog and the Reference lists page. *Update to version X* re-runs the
materialization as a diff against the pinned version, through
`update_target_collection`: compounds the new version adds are added,
compounds it dropped are removed from the collection (decision for the plan
owner, section 9, P1), the pin moves, and the attached batches go to
`rematch` as they do for any membership edit. Existing runs and match rows
are what they were until the person refreshes, which is the stance the
assignment work takes for a changed environment (MAS-342).

Deactivating a source (`mascope reference deactivate`) does not touch its
collection: the collection is the batch's record of what it was matched
against. The page says the source is inactive.

### 4.4 Scope, roles and the licence gate

- Create and update: the global admin role, as for any global collection.
- Attach and detach: the editor role on the batch, as today.
- Membership edits through `PATCH /api/target/collections/{id}` that send
  `target_compound_ids` or `target_compounds_create` are refused for a
  list-backed collection with a message naming *Update to version X*. Name,
  description and batches stay editable.
- Delete: as any global collection; `delete_orphan_compounds` applies.
- A source outside the licence gate is listed on the page with its licence
  and no *Use as targets* action. A collection built before the gate was
  narrowed stays (its rows are in the deployment's own database) and the page
  says the source is now outside the gate.

### 4.5 What matching does with it

Nothing new. The collection's isotopes are fetched through the junctions,
matched, scored and aggregated as any collection's are; the collection tag,
the ion table, the Match tab and the Batch chart draw them. The per-ion match
parameters (`filter_params`) are editable in the Match tab's drawer as for
any ion.

### 4.6 Reading results, with and without the Match tab

MAS-83 asks whether the Match tab is maintained, frozen or retired once
assignment is the default. This design does not wait on the answer:

- The collection is the unit of "what to monitor" under every answer.
  Attaching, the collection score and the alarm colour are collection-level
  facts the pane carries, not the tab.
- Under *retired*, `peak_assignment_paradigm.md` section 5.1 makes targeted
  analysis a view over assignments where `target_compound_id` is set. A
  list-backed collection's compounds carry ids like any others, so the view
  groups them by collection as it would a hand-built one.
- Nothing in phases 0 to 2 adds to the Match tab itself.

---

## 5. Target management on the batch

### 5.1 The Targets panel

The collections pane (`MatchCollectionTable.vue`) gains a second header
action beside create: **Attach collections**. It opens `PaneSelectTargets`
in `targets` mode, in a dialog of its own (not `DialogBatchOp`'s Info and
Targets tabs), pre-ticked with the batch's current collections. The list
groups **Reference lists** above the person's collections, each list-backed
row with a list glyph, its version and *update available* where it applies.
The type filter and search stay. The footer is *Cancel* and *Save and
refresh matches*.

The row of a list-backed collection in the pane shows the same glyph. Its
context menu ([collectionContextMenu.js](../../server/frontend/src/lib/panes/PaneBrowserMatch/stores/collectionContextMenu.js))
offers *Update to version X* when one is available and *Edit* opens the
dialog with the Compounds tab read-only (section 5.5).

### 5.2 Attaching runs matching

*Save and refresh matches* sends the batch's new collection list
(`PATCH /api/sample/batches/{id}`, as `DialogBatchOp` does) and then
`POST /api/match/compute/batch/{id}`. The batch row shows its spinner and
the toast reports completion, as a refresh does today. Detaching alone also
calls compute, which removes the detached collection's matches on its way
(the controller's step for collections no longer attached). A membership
edit in the collection dialog keeps today's flag-only behaviour.

### 5.3 The Reference lists page

A new tab in the sidebar menu ([SidebarMenu.vue](../../server/frontend/src/lib/toolbars/ToolbarAppFilters/SidebarMenu/SidebarMenu.vue),
beside Workspaces, Notifications, Settings and About), `ReferenceListsPane.vue`,
over a new data store `stores/data/modules/reference.js`. One row per active
source: label, name, version, licence, polarity, radicals allowed, window,
species count, loaded at, and the regimes once MAS-336 records them. A row's
expander lists the species, paged, with name, formula, mass and the DOI a
species carries. Two actions:

- **Use as targets** (admin): creates the list-backed collection
  (section 4.2). Afterwards the row links to the collection and says which
  version it pins.
- **Update targets to this version** (admin): when the collection pins an
  older version.

A source outside the licence gate is shown with its licence and no action.
A person who is not an admin sees the page and the collections, and attaches
them from the Targets panel.

### 5.4 Promote from an assignment row

The assignment ledger ([PaneBrowserAssignment.vue](../../server/frontend/src/lib/panes/PaneBrowserMatch/PaneBrowserAssignment.vue))
and the batch ledger ([PaneBrowserBatchPeaks.vue](../../server/frontend/src/lib/panes/PaneBrowserMatch/PaneBrowserBatchPeaks.vue))
gain a row action, **Add to collection**, that opens
`PopoverTargetCompoundAdd.vue` with the row's neutral formula and, where the
row is *listed as* a list compound, that identity's name and InChIKey. The
popover gains `name` and `inchikey` props and otherwise behaves as it does
from an ion row: it reuses an existing compound where the identity rule
matches and offers the batch's collections (not a list-backed one, whose
membership is read-only) as the destination. This is the path from
discovery to monitoring: what a run found and a list named becomes a target
in the person's own collection, with its structure.

### 5.5 The collection dialog

Two changes, both small beside the UX review's rebuild (MAS-156):

- For a list-backed collection the Compounds tab is read-only: the table of
  members, a banner naming the source and version, and a link to *Update to
  version X* when one is available. The Batches tab works as today.
- *Import existing*'s source select groups **Reference lists** above the
  person's collections, so a fork is one pick.

---

## 6. API, SDK and CLI surface

| Surface | Route or call | Phase |
|---|---|---|
| Reference sources | `GET /api/reference/sources`: the active sources with label, name, version, licence, polarity, `allow_radicals`, `known_window`, `record_count`, `ingested_at`, whether the licence gate admits it, and the id and pinned version of its collection if one exists. A new router in `api/new/reference/routes.py` (prefix `/api/reference`), registered in [api/routes/__init__.py](../../server/backend/src/mascope_backend/api/routes/__init__.py). Any signed-in user. | 0 |
| Source species | `GET /api/reference/sources/{name}/compounds?version=&page=&limit=`: the records of a version, paged. | 0 |
| Create from a list | `POST /api/target/collections/from-reference` body `{source_name, version?, target_collection_name?}`, admin, `202` with a notification. | 1 |
| Update from the list | `POST /api/target/collections/{id}/update-from-reference`, admin, `202`. | 1 |
| Membership guard | `PATCH /api/target/collections/{id}` refuses `target_compound_ids` and `target_compounds_create` on a list-backed collection. | 1 |
| Collection read | `GET /api/target/collections` and `/{id}` carry the two new columns and `reference_update_available`. | 1 |
| SDK reads | `mascope.reference.sources()` and `.compounds(source_name)` in a new `resources/reference.py`. | 0 |
| SDK writes | `mascope.collections.from_reference(source_name, version=None, name=None)` and `.update_from_reference(collection_id)` in [collections.py](../../libraries/sdk/src/mascope_sdk/resources/collections.py); the role table in [docs/user/sdk/index.md](../user/sdk/index.md) gains both rows. | 1 |
| CLI | Nothing new. `mascope reference status` cannot say whether a source is in use as targets without the library reading target tables, which crosses the ownership boundary (`libraries/reference/README.md`). The page and the SDK are the surfaces. | - |

**Relation to CSV import and export (#747, #748).** A list file (schema 2,
[peaklist.py](../../libraries/reference/src/mascope_reference/peaklist.py))
is already a portable, licensed, versioned description of a compound list,
and `mascope reference sync peaklist` loads one. With this design, "import a
collection from a file" is: load the file as a source, then *Use as
targets*. An export of a collection as a list file (formula, name, InChIKey,
CAS, the citation in the header) would close the loop and make #747's format
the list format rather than a second one. Not in this plan's phases; noted
for the plan owner (P5).

---

## 7. Schema changes

| Table | Change | Phase |
|---|---|---|
| `target_collection` | `reference_source_name` (`String(64)`, nullable, indexed), `reference_source_version` (`String(128)`, nullable) | 1 |

One Alembic migration under
[server/backend/alembic/versions/](../../server/backend/alembic/versions/),
additive, with the parity test the suite runs over new columns. No change to
`target_compound`, the junctions, or the `reference_*` tables. MAS-336's two
columns on `reference_source` are its own migration and this design reads
them when they exist.

---

## 8. Phases

Each phase is one or two PRs and lands on its own. The file lists are what a
brief for the step names; a step's own reading may add to them.

### Phase 0 - the reference sources API and the Reference lists page

**Content.** `GET /api/reference/sources` and `/{name}/compounds`; the
`reference` data store; `ReferenceListsPane.vue` as a sidebar tab, read-only
(no actions yet); the SDK `reference` resource.

**Where.**
`server/backend/src/mascope_backend/api/new/reference/routes.py` (new),
`api/new/reference/service.py` (a `sources()` reader beside the annotation
readers; the label from `mascope_reference.seed.catalogue`),
`api/routes/__init__.py`;
`libraries/reference/src/mascope_reference/query.py` if a sources listing
belongs in the library rather than the backend;
`server/frontend/src/stores/data/modules/reference.js` (new) and
`stores/data/index.js`,
`server/frontend/src/lib/toolbars/ToolbarAppFilters/SidebarMenu/ReferenceListsPane.vue`
(new), `SidebarMenu.vue`, `index.js`;
`libraries/sdk/src/mascope_sdk/resources/reference.py` (new),
`libraries/sdk/src/mascope_sdk/client.py`;
`docs/user/guides/reference-lists.md` (a section on seeing what is loaded),
`docs/maintaining.md` ("Loading reference chemistry data" links the page),
`docs/user/sdk/index.md`; a `changelog.d` fragment.

**Verify.** Backend: `server/backend/tests/integration/api/reference/`
(new): the route lists active sources only, carries the licence verdict,
pages the species, and a user outside every workspace can read it (it is
deployment data). Frontend unit: `server/frontend/tests/unit/lib/` a spec
for the pane's rows and the licence state. SDK:
`libraries/sdk/tests/test_contract.py` gains the two reads. e2e on the demo
stack (its seed loads the default lists): the page lists the HOM list with
573 species.

**Size.** S-M.

### Phase 1 - list-backed collections

**Content.** The two columns and their migration; the materialization
service and its notification; `from-reference` and `update-from-reference`;
the membership guard; *update available*; *Use as targets* and *Update
targets* on the page; the read-only Compounds tab and the grouped import
source in the dialog; *Update to version X* in the collection context menu;
the SDK writes.

**Where.**
`server/backend/src/mascope_backend/db/models.py` (`TargetCollection`),
one file under `server/backend/alembic/versions/`,
`api/models/target/collections/target_collection_pydantic_model.py`,
`api/controllers/target/collections/target_collections_controller.py`
(the guard in `update_target_collection`; the new service beside it, e.g.
`collections/lib/reference_materialize.py`),
`api/routes/target/collections/target_collections_routes.py`,
`api/new/peak_assignments/service.py` (lift `reference_license_gate` to a
shared module both paths import),
`api/new/reference/service.py` (the version row by name and version);
`server/frontend/src/lib/dialogs/DialogTargetCollectionOp.vue`,
`lib/panes/PaneBrowserMatch/MatchCollectionTable.vue`,
`MatchCollectionContextMenu.vue`, `stores/collectionContextMenu.js`,
`lib/toolbars/ToolbarAppFilters/SidebarMenu/ReferenceListsPane.vue`,
`lib/constants.js` (the glyph);
`libraries/sdk/src/mascope_sdk/resources/collections.py`;
`docs/user/guides/target-collections.md`, `docs/user/guides/reference-lists.md`
(a loaded list can be targets, and what the pin means for a re-sync),
`docs/user/concepts/index.md` ("Targeted analysis"),
`docs/user/sdk/index.md`; a `changelog.d` fragment.

**Verify.** Backend, on the LAN agent host:
`tests/integration/api/target/test_target_collection_from_reference.py`
(new): creating from the demo's HOM source materializes every admitted
species with ions for every mechanism, reuses a compound that exists under
the identity rule, skips and logs a formula the generator refuses, sets the
pin; a second create for the same source is refused; the guard refuses a
membership edit and allows a rename and a batch change; an update to a newer
version adds, removes (per P1) and moves the pin and sets the batches to
`rematch`; a source outside `reference_licenses` is refused; a non-admin is
refused. `tests/migrations/` parity. **A measurement, before the PR is
opened:** the time and the row counts for materializing the HOM list and the
contaminants list against the demo's mechanism set, and the time of
`compute/batch` for the demo batch with the HOM collection attached,
recorded in the PR. Frontend unit:
`tests/unit/lib/dialogTargetCollectionOp.spec.js` (read-only tab, grouped
import source), the pane spec (actions by role and licence). SDK:
`tests/test_writes.py`. e2e: `tests/e2e/target-collection.spec.js` creates
the collection from the page through the `api` fixture, attaches it to the
`scratch` batch, and sees its tag in the pane.

**Size.** M-L.

### Phase 2 - target management on the batch

**Content.** *Attach collections* in the pane and its dialog; *Save and
refresh matches*; *Add to collection* on assignment and batch-peak rows with
the identity prefilled.

**Where.**
`server/frontend/src/lib/panes/PaneBrowserMatch/MatchCollectionTable.vue`,
`lib/dialogs/DialogBatchOp/PaneSelectTargets.vue` (the grouped list and the
glyph; the pane is reused, not forked), a new
`lib/dialogs/DialogAttachTargets.vue` (or a mode of `DialogBatchOp` if the
rebuild of MAS-156 has landed and made that cheap), `lib/dialogs/index.js`,
`stores/data/modules/batch.js` (the attach-then-compute call),
`lib/panes/PaneBrowserMatch/PaneBrowserAssignment.vue`,
`PaneBrowserBatchPeaks.vue`, `lib/dialogs/PopoverTargetCompoundAdd.vue`
(`name` and `inchikey` props), `lib/referenceListings.js` (the identity a
row offers); `docs/user/guides/target-collections.md` ("Attach or detach an
existing collection" and "Run matching" become one section),
`docs/user/_help/matching.md`; a `changelog.d` fragment.

**Verify.** Frontend unit: the pane's action, the dialog's grouping and
footer, the popover's prefill from a listed row and from an unlisted one.
e2e on the demo stack: attach from the pane, see the spinner and the
completion toast and the collection tag without clicking the status icon;
promote a listed assignment row into a scratch collection and find the
compound in it with its InChIKey.

**Size.** M.

### Phase 3 - convergence

Three steps, each waiting on another plan and briefed when that plan is
there:

- **Environments suggest list collections** (after MAS-342). When the
  batch's environments select lists for Stage A, the Targets panel offers
  the lists' collections as suggestions. Not auto-attached (P4).
- **Seeded profile collections share the mechanism** (with MAS-312). A
  collection the system seeds beside a chemistry profile is system-owned
  and global as a list-backed one is; the two should share whatever marks
  a collection as owned by the system rather than a person, and the
  read-only treatment in the dialog.
- **Results read off assignments** (under MAS-83's *retired*). The
  collection view over `peak_assignment` rows where `target_compound_id` is
  set, grouped by collection, carries list-backed collections unchanged.

---

## 9. Decisions for the plan owner

Each is a call, not a task; the phase named waits on it.

- **P1. Removal on update.** When a newer list version drops a species,
  does *Update to version X* remove the compound from the collection, or
  only add? *Leaning:* remove, through `update_target_collection`, so the
  collection is the list at that version and nothing else; the match rows go
  as they do for any removal (and #1492's cascade concerns apply equally).
  Phase 1.
- **P2. Who creates.** Admin only, as for every global collection, or may
  an editor create a workspace-scoped copy from the page? *Leaning:* admin
  only; a copy per workspace multiplies 573 compounds' ions per workspace
  and leaves several pins to update. Editors attach. Phase 1.
- **P3. List compounds in the *All compounds* picker.** Materializing the
  HOM list adds hundreds of formula-named compounds to the shared pool the
  dialog's *All compounds* source lists. Hide compounds that belong only to
  list-backed collections behind a toggle, or show them? *Leaning:* show
  them; picking a list's compound into one's own collection is the point of
  sharing, and the search field narrows. Revisit if the picker proves noisy.
  Phase 1.
- **P4. Environments and attachment.** When MAS-342 lands and environments
  select lists for Stage A, should the batch's Targets also follow? *Leaning:*
  suggest, never attach unasked, consistent with "re-run nothing unasked"
  (MAS-107). Phase 3.
- **P5. Export as a list file.** Should a collection export as a schema 2
  list file, making the list format the answer to #747 and #748? *Leaning:*
  yes, as a step of its own after phase 1, briefed against those two issues.
  Not in these phases.
- **P6. The seed and the demo.** Should the demo (`mascope demo`,
  `seed_reference_demo.py`) create a list-backed collection so the shipped
  experience shows one? *Leaning:* yes, one small list (the contaminants
  list, 50 species), after the measurement in phase 1 says what it costs.
  Phase 1.

---

## 10. Costs and risks

- **Isotope rows.** A collection's isotopes are persisted per mechanism.
  The HOM list is 573 species; a CIMS deployment with a few dozen mechanisms
  is tens of thousands of ions and more isotopes. Stage A pays the same
  expansion in memory and caps its cache at eight frames; this persists it.
  The phase 1 measurement bounds it before the design is committed to; if it
  is too large, the fallback is to generate ions only for the mechanisms of
  the batches the collection is attached to, which is a change to
  `create_target_ions` with its own brief.
- **Matching time.** The matcher's cost scales with the attached isotopes.
  The same measurement records `compute/batch` on the demo batch with the
  HOM collection attached. The fetch already filters by the sample's
  polarity and mechanisms.
- **Compound collisions.** Under decision 6 a list compound that matches a
  person's compound by the identity rule is the same row. Editing it edits
  both collections' member. The compound dialog can say which collections a
  compound belongs to (`fetch_compound_collections_and_batches` exists);
  phase 1 should show it there. MAS-419's InChIKey rule reduces false
  merges; a list species without a structure and a user compound of the
  same formula and name still merge, which is what sharing means.
- **Removal cascade.** Updating a list that dropped species walks the
  match-removal path #1492 questions. P1 may choose add-only to avoid it
  until that issue is settled.
- **Roles.** #1909 complains that a system-workspace collection needs the
  global admin role. Decision 4 keeps that rule for list-backed collections;
  if #1909 relaxes it, this design follows.
- **The type constraint.** #1491's batch-type constraint allows `TARGETS` on
  analysis and acquisition batches, which is what a list is attached to. If
  the constraint goes, nothing here changes.
- **The licence gate narrowed later.** A collection built under a wider
  gate stays; its rows are the deployment's own. The page says so; nothing
  deletes data.
- **Prune.** `reference sync --prune` deletes the old version's rows.
  The pin is text, so the collection still names its version; *Update*
  then diffs against the live collection rather than the pruned source.
- **The Match tab decision.** Section 4.6. Phases 0 to 2 add nothing to the
  tab; a retirement moves the collection view, not the collection.

---

## 11. How this touches the related designs

- **`public_database_integration.md`**: its phase 3, the Stage A seam, is
  untouched. The known set and its precedence rule are what make decision
  10 free: a list that is both a mirror source and an attached collection
  reads once per peak. The question the seam's design left open, whether
  "the target owns the id, the reference adds the name" matches how
  targeted alarms and collections should behave, is answered here: the
  collection owns the target id and the alarm; the mirror adds the name.
  Its phase 5 (productization, partly shipped) gains in phase 0 here the
  first surface for the sources themselves, beside the identities the
  ledger already shows; GitHub #2508 and MAS-373 carry the phase.
- **`docs/user/guides/reference-lists.md`**, the user page the retired
  `reference_data_authoring.md` became: gains a section saying a loaded
  list can be used as a target collection from the Reference lists page,
  and what the pin means for a re-sync.
- **`peak_assignment_paradigm.md`**: section 5.1's "targeted analysis
  becomes a filtered view" is where phase 3's third step lands; a
  list-backed collection is a collection there.
- **`sample_filters_and_metadata.md`**: its section 4.6 selects lists for
  Stage A by environment; this design adds the suggestion step (phase 3,
  P4) and nothing to its schema. Its phase 3 columns on `reference_source`
  are shown on the Reference lists page when present.
- **`chemistry_profiles.md`**: section 4.4's `reference_tags` seam is now
  MAS-342's; unchanged by this note.
- **`ux_review_2026_10.md`**: M2 (one dialog grammar) rebuilds the dialog
  this design adds a read-only mode to; D2 (the retirement path) is
  section 4.6 here. The attach dialog of phase 2 should be built in the
  grammar M2 settles, or after it.
- **MAS-312** (seeded calibrant and diagnostic collections): the second
  user of a system-owned global collection; phase 3's second step aligns
  the two.
