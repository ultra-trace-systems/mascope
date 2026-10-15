# Handoff - FAIR Phase 1 (describable, licensable datasets)

*The plan is [fair_roadmap.md](fair_roadmap.md); this document designs and
hands off its §6 Phase 1 - items 6 to 9. Phase 0 is built and merged
([handoff_fair_phase0.md](handoff_fair_phase0.md) records it as built);
Phase 1 is the first phase that changes the schema.*

Phase-level acceptance (roadmap §9): *a dataset can carry a license, authors
and a related publication DOI; a target compound resolves to an InChIKey;
deleting a dataset leaves a tombstone.* The other half of the roadmap's
tombstone line - instrument definitions surviving their last file - belongs
to the ionization redesign's `Instrument` table, which has not landed; it is
not pulled into this phase.

## 1. Read these, in order

1. [fair_roadmap.md](fair_roadmap.md) §6 Phase 1 - the four items.
2. [handoff_fair_phase0.md](handoff_fair_phase0.md) - §3 ground rules (still
   binding), §4 the provenance block and `mascope_backend.deployment`
   (Phase 1 builds directly on both), §8 for how the plan's file-level
   guesses fared against the tree (expect the same here: re-verify every
   pointer below before editing).
3. This document. §3 lists design decisions already taken - review them
   against the tree before coding, then follow them.

## 2. Scope

| Item | What ships | Closes |
|---|---|---|
| 6 | Five nullable metadata fields on `Dataset`, through API, SDK, exports and the dataset dialog | F2, R1.1 |
| 7 | The namespaced-id contract `<deployment_id>:<entity>:<local_id>`, derived inside the provenance block | F1 (the exportable half) |
| 8 | `dataset_tombstone`: a snapshot written on every dataset delete, readable over the API | A2 |
| 9 | `inchikey` / `inchi` / `smiles` on `TargetCompound`, with a conservative CAS backfill | I3 |

One Alembic migration covers all of it (§5). **Explicitly out of scope:**
per-result provenance on the classic matching path (Phase 0 handoff §9 names
it; it touches the hot matching path and the peak-centric paradigm already
carries its own run provenance - if it is ever worth doing, it is its own
piece of work); any tombstone UI (the API answer is the A2 requirement; a
list view is a follow-up); reference-identity *search* UI for compounds
(entry fields only); anything Phase 2 (CV terms, mzML, Schymanski).

## 3. Design decisions taken here - review, then follow

1. **InChIKey is the link to reference chemistry; there is no FK to
   `ReferenceCompound`.** The roadmap item says "a nullable FK", written
   before the load semantics existed. Every ingest of a source inserts a
   *new* load with fresh `reference_compound` rows and new ids; an activating
   load flips the previous one inactive and keeps it on disk for
   reproducibility, and `ingest(..., prune=True)` deletes the inactive loads
   and their compounds outright (`mascope_reference.ingest`,
   `_prune_inactive`; the `cascade="all, delete"` on `ReferenceSource` is
   what carries that delete to the compounds). A row-level FK would therefore
   point at one load's row: stale as soon as the source is re-ingested, gone
   when an operator prunes. `ReferenceCompound.inchikey` is already "the
   cross-source dedup key" (its own docstring) and is the identity that stays
   stable across loads and sources; resolve identity to the *active*
   reference rows at query time via the indexed `inchikey` columns on both
   sides.
2. **JSON columns, not child tables**, for `authors`, `keywords`,
   `related_identifiers`, `funding`. The lists are small, always read with
   the dataset, and never queried relationally in any planned phase; the
   codebase stores exactly this shape as JSON already (`icon`,
   `sample_item_attributes`, `xrefs`, `known_window`). Pydantic validates
   shape at the boundary.
3. **A formula change clears compound identity.** `update_target_compound`
   *recreates* the row when the formula changes (delete old, create new,
   re-associate) - a different formula is a different substance, so
   `inchikey`/`inchi`/`smiles` do not carry over unless the update supplies
   them afresh. Carry `cas_number` over exactly as the current code does or
   does not - do not change its behaviour in passing.
4. **InChI-vs-formula consistency is enforced, with a labelled-compound
   escape - in the controllers, against the formula the row will have.**
   Whenever a compound ends up with both an `inchi` and a formula, the
   InChI's formula layer (the segment after `InChI=1S/` up to the next `/`)
   must equal that formula under `mascope_tools` Hill normalisation;
   mismatch is a 422 naming both strings. The check cannot live only in the
   pydantic models: `TargetCompoundUpdate` lets a caller omit the formula, so
   an identity-only update would never see the stored formula it has to
   agree with, and could save an identity for a different compound.
   `create_target_compound` checks the incoming pair;
   `update_target_compound` checks the incoming `inchi` against the formula
   the row will have after the update - the incoming one when it changes, the
   stored one otherwise (a formula change without new identity clears the
   identity under decision 3, leaving nothing to check). Skip the check when
   that formula carries caret isotopes (`^N` names a labelled reagent the
   InChI layer cannot express). A wrong identity is worse than none.
5. **Tombstones are written on both delete paths.** `delete_dataset`
   (dataset controller) and `delete_workspace`
   (`api/new/workspaces/service.py`, which deletes datasets by cascade
   without passing through the dataset controller) both write snapshots in
   the same transaction as the delete. A `deleted_via` column records which.
   Raw SQL and restored dumps bypass this, as they bypass every controller;
   the table's docstring says so (a trigger would be the DB-level answer and
   is disproportionate).
6. **Global-id derivation lives inside `build_provenance`.** For each
   recognised `<entity>_id` key in `inputs`, when `deployment_id()` is
   non-null, the block gains a sibling `<entity>_global_id`. One
   implementation, every consumer (Provenance sheet, future package)
   inherits it; no second assembly point. When the deployment has no id,
   no global key is emitted - never fabricate a namespace.
7. **Item 9 proceeds now; it does not wait for the ionization redesign.**
   The roadmap's §7 sequencing gated item 9 on the ionization Phase 1
   landing, written when both were hypothetical. Its actual prerequisites -
   the reference tables and the annotation path - shipped with the
   public-database integration, and nothing in item 9 touches instruments
   or ionization (the roadmap carries a dated correction pointing here).
   Phase 2 still waits.
8. **License is a free string with a suggested vocabulary, not a validated
   SPDX enum.** Trimmed, non-empty-if-present, length-bounded. The UI offers
   the common cases (`CC-BY-4.0`, `CC0-1.0`, `CC-BY-NC-4.0`, `proprietary`)
   and accepts anything; freezing the full SPDX list in code buys churn, not
   correctness. Note this is the *dataset's own data license* - a different
   axis from `[backend] reference_licenses`, which gates which mirrored
   sources may be matched.

## 4. Item 6 - dataset descriptive metadata

**Schema** - five nullable columns on `dataset`
(`server/backend/src/mascope_backend/db/models.py`, `Dataset`; do not
disturb the two partial unique indexes or their comments):

- `license: String(64)`
- `authors: JSON` - list of `{"name": str, "orcid": str|null,
  "affiliation": str|null}`; `name` required non-empty; `orcid` validated by
  regex `^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$` (format only - the ISO 7064 check
  digit is not recomputed; say so in the validator docstring); list order is
  author order.
- `keywords: JSON` - list of trimmed non-empty strings.
- `related_identifiers: JSON` - list of `{"identifier": str,
  "relation": str, "identifier_type": str|null}`. `relation` is free text;
  the UI suggests the DataCite relationType values that matter here
  (`IsSupplementTo`, `IsDescribedBy`, `IsDerivedFrom`, `Cites`,
  `References`, `IsPartOf`); `identifier_type` suggests `DOI` / `URL`.
- `funding: JSON` - list of `{"funder": str, "award": str|null}`.

**API** - fields join `DatasetBase` (so `DatasetRead` reports them) and
`DatasetUpdate`
(`api/models/dataset/dataset_pydantic_model.py`; note `DatasetUpdate` is
deliberately the user-editable subset - these are user-editable, so they
belong there; `dataset_type` and `instrument` stay out). New sub-models
(`DatasetAuthor`, `RelatedIdentifier`, `Funding`) carry the validators.
Nullable throughout: never block a create or an ingest on metadata a user
does not have, and ACQUISITION datasets accept metadata like any other.
The update controller copies the fields like the existing ones; check
`update_dataset` for the field-copy pattern.

**Exports** - the *Batch* sheet of the batch spreadsheet
(`api/controllers/sample/batches/export/service.py`) appends rows after the
Phase 0 additions: `License`, `Authors` (rendered `Name (ORCID), Affiliation`
joined with `; `), `Keywords`, `Related identifiers`
(`relation: identifier`), `Funding`. Rows render empty when unset -
unconditional rows keep the sheet shape fixed for parsers, which is how
Phase 0 shipped its additions. The Phase 0 tests pin the legacy order;
extend them, do not loosen them.

**SDK** - dataset listings pass server dicts through, so the fields arrive
for free; add one test asserting they do, and a README line under the
existing dataset-loading docs.

**Frontend** - a new metadata section component used by
`server/frontend/src/lib/dialogs/DialogDatasetOp.vue` (151 lines today -
keep it lean by putting the section in its own component): license
select-with-custom-entry, an authors row editor (name / ORCID /
affiliation, add-remove, order preserved), keyword chips, related-identifier
rows, funding rows. Vitest covers the component's add/remove/emit logic
(`server/frontend/tests/unit/`, mirroring the `src/` path).

## 5. The migration - one revision for the whole phase

Head at design time: `7a2d5c8e3b94`
(`20261001_7a2d5c8e3b94_method_binding_follows_newest_row.py`) - **re-check
when branching** and chain from the real head. Filename convention:
`YYYYMMDD_<12hex>_snake_description.py`.

Contents: the five `dataset` columns; the three `target_compound` columns
plus the `inchikey` index; `create_table("dataset_tombstone", ...)` (§6).
All columns nullable, so this is `ADD COLUMN` without rewrite and carries no
backfill.

Traps, each has bitten before:

- **Constraint naming.** `Base.metadata` carries a naming convention
  (`models.py`, `NAMING_CONVENTION`): name constraints in models with the
  *bare* name (`formula_not_a_mass` style) and let the convention prefix
  `ck_<table>_`; a name you write *with* a `ck_` prefix gets prefixed again,
  and nothing in CI catches the double-prefixed result. In the migration,
  write names exactly as the convention renders them. Verify the created
  names against a live schema (`\d dataset_tombstone`) in development, and
  make the migration test assert what you expect.
- **Two heads surface as a red e2e, not as a migration failure.** CI builds
  the PR merge; if develop gains a migration while this branch is open, the
  demo stack's `demo_init` fails on two heads. Rebase onto develop
  immediately before opening and before merging, and re-chain
  `down_revision` if the head moved.
- The backend suite's migration tests need Postgres - this workstation does
  not run it; the suite runs on the LAN host (§10).

## 6. Item 8 - tombstones

**Table `dataset_tombstone`** - append-only, **no foreign keys and no
relationships** (its rows must survive everything they describe, including
the workspace):

- `dataset_id: String(16)` primary key (the original id). Write with an
  upsert on `dataset_id`: a dataset restored from a dump and deleted again
  overwrites its old tombstone rather than faulting.
- Snapshots: `workspace_id: String(16)` (plain column), `workspace_name:
  String(256)`, `dataset_name: String(256)`, `dataset_description: Text`,
  `dataset_type: String(64)`, `instrument: String(64)` - all as the row
  stood.
- `descriptive_metadata: JSON` - the item 6 five, as one
  `{license, authors, keywords, related_identifiers, funding}` object.
- Counts at delete time: `n_sample_batches`, `n_sample_items`,
  `n_sample_files` (distinct files via the sample-item join) - integers,
  cheap aggregates in the delete transaction.
- `dataset_utc_created`, `dataset_utc_modified` snapshots;
  `deleted_utc: TIMESTAMP(timezone=True)`.
- `deleted_by: Text` nullable - the acting user's email as a plain string
  snapshot (no FK: users are deletable); `deleted_via: String(16)` -
  `dataset` or `workspace`.
- `provenance: JSON` - `build_provenance()` at delete time, naming the build
  that deleted.

**Write paths** (decision 5): in `delete_dataset`
(`api/controllers/dataset/dataset_controller.py`) build the snapshot from
the fetched row *before* `session.delete`, in the same session, committed
together; in `delete_workspace` (`api/new/workspaces/service.py`) snapshot
every dataset of the workspace first, `deleted_via="workspace"`, same
transaction as the workspace delete. Both routes know their acting user -
thread the email through to `deleted_by` (the dataset controller may need
the parameter added; follow how `granted_by` style attribution is passed
elsewhere rather than inventing a context mechanism).

**Read path** - `GET /api/datasets/tombstones` under `api/new/` (copy the
`api/new/provenance/` route pattern - `@api_route(token_access=True)` with a
`guest_user` dependency - so the SDK can read it). Visibility: rows whose `workspace_id` the caller is a member
of; global admins and owners see all rows, including those whose workspace
is gone. Sorted `deleted_utc` descending, paged like other listings.

**SDK** - a small read method on the datasets resource
(`libraries/sdk/src/mascope_sdk/resources/datasets.py`), returning the rows
as dicts; one hermetic test.

**Tests** - backend: delete a dataset → tombstone row carries name,
metadata snapshot, counts and provenance; delete a workspace → one
tombstone per dataset with `deleted_via="workspace"`; tombstone survives
(workspace row gone, tombstone row readable); upsert on re-delete. Mutation
check (house rule: prove the test pins the behaviour): drop the snapshot
write and watch the test go red.

## 7. Item 7 - namespaced ids

- `global_id(entity: str, local_id: str) -> str | None` in
  `mascope_backend.deployment`, beside `deployment_id()`:
  `f"{deployment_id()}:{entity}:{local_id}"`, `None` when the deployment has
  no id. Entities are table names (`dataset`, `sample_batch`, `sample_item`,
  `sample_file`, `target_collection`, `target_compound`, `target_ion`).
  The format is parseable by construction: the configured id's charset
  already forbids `:` (validated at config load - Phase 0 built it this way
  *for this item*), the generated id's alphabet has no `:`, and entity names
  use `_`.
- `build_provenance` derives `<entity>_global_id` beside each recognised
  `<entity>_id` in `inputs` (decision 6). Adding keys does not bump
  `provenance_version` (Phase 0 contract); changing the scheme later would.
- The Provenance sheet needs no code of its own - it renders the flattened
  block, so `inputs.dataset_global_id` and `inputs.sample_batch_global_id`
  appear once the derivation exists. Extend the sheet's test to pin them.
- Document the contract where operators read about the id:
  `docs/maintaining.md`, *Deployment identity* - the format, that it is
  stable, and that two deployments sharing a configured id defeats it.

## 8. Item 9 - target compound identity

**Schema** - on `target_compound` (`models.py`): `inchikey: String(27)`
indexed, `inchi: Text`, `smiles: Text`, all nullable. Format validation in
the pydantic layer (`api/models/target/compounds/target_compound_pydantic_model.py`,
`TargetCompoundBase` and `TargetCompoundUpdate`): InChIKey regex
`^[A-Z]{14}-[A-Z]{10}-[A-Z]$`; `inchi` must start `InChI=`; `smiles` trimmed
non-empty (no cheminformatics dependency is added to parse it - do not pull
one in for validation). The InChI formula-layer consistency check of
decision 4 is **not** a pydantic validator: it needs the formula the row
will have, which an identity-only update does not carry. It lives in the
controllers as one helper both of them call, using the Hill normalisation
already in `mascope_tools.composition.utils`.

**Controllers** (`api/controllers/target/compounds/...`): create checks the
incoming `inchi` against the incoming formula and passes the fields through;
update checks the incoming `inchi` against the formula the row will have
after the update - the incoming one when it changes, the stored one
otherwise - and follows decision 3 on the formula-change recreation path.
Read `update_target_compound` closely before touching it: it re-associates
collections and queues rematches. **Test to add:** an identity-only update
whose `inchi` disagrees with the stored formula is refused with a 422
naming both strings; the same `inchi` sent together with a matching formula
is accepted.

**Backfill** - `server/backend/src/mascope_backend/db/scripts/`
`backfill_target_compound_identity.py`, run manually by an operator
(precedent: `populate_orbitrap_method_file`, `reference_sync`). For each
compound with a `cas_number` and no `inchikey`: find *active* reference rows
whose `xrefs` CAS matches - the key is `casrn` (CompTox) or `cas` (custom
lists); compare digits-only so dash variants match - **and** whose Hill
formula equals the compound's. One distinct `inchikey` → fill all three
fields; several → skip and log the candidates; none → skip. Print a
summary; `--dry-run` first, like the other scripts. Never match on name.

**Exports** - the *Match compounds* sheet appends `CAS number` and
`InChIKey` after its Phase 0 columns.

**Frontend** - the compound add/edit surfaces gain the three fields
(optional inputs; find the compound editor through the target-collection
views - the dialog/table component naming will tell you more than this
document can promise); show `InChIKey` wherever `CAS` shows today. Vitest
for any new component logic.

## 9. Suggested slicing - one PR, commits land individually

develop merges PRs by rebase, so **every commit must pass the suite alone**:

1. Migration + models + migration-test coverage (nothing reads the columns
   yet).
2. Item 6 backend: pydantic models, controller, export rows, SDK
   pass-through + tests.
3. Item 8: tombstone writes on both paths, route, SDK method + tests.
4. Item 7: `global_id`, `build_provenance` derivation, maintaining.md,
   sheet-test pins.
5. Item 9 backend: validation, controllers, backfill script, export
   columns + tests.
6. Frontend: dataset metadata section + compound identity fields + Vitest.
7. Docs + CHANGELOG (one appended entry; never edit an existing one - the
   file merges by union and shows no conflicts).

Branch from develop; rebase (never merge) to stay current; Conventional
Commits, ASCII-only, no attribution trailers (repo rule overrides any
harness default); `uv run ruff check --fix . && uv run ruff format .` before
every commit. The repository is public: no real instrument, server or
customer names anywhere, including test data.

## 10. Working notes

- **Backend suite runs on the LAN host**, not this workstation (no
  Postgres here): plain `uv run pytest server/backend/tests/` in a worktree
  on the box, one suite at a time. Libraries, CLI, SDK and frontend-unit
  suites run anywhere.
- **Windows capture**: set `PYTHONUTF8=1` when capturing CLI output.
- **Reading an exported sheet back in a test**: under pandas 3,
  `frame.astype(str)` leaves missing cells as float NaN and `"x" in cell`
  then raises - stringify per cell (`str(cell)`).
- **No CLI changes expected.** Phase 0's CLI-floor lesson (a CLI import of
  a new runtime symbol needs a floor bump, which the packaging smoke cannot
  see) applies only if you import a new `mascope_runtime` symbol from the
  CLI - this phase should not.
- Real-data verification before finishing, on a demo-env instance: set
  metadata on a dataset through the dialog, export the batch spreadsheet,
  read the License/Authors rows and the `inputs.*_global_id` keys back;
  delete the dataset; read its tombstone over the API.

## 11. Definition of done

- [ ] One migration, chained on the real head, names verified against the
      live schema
- [ ] Dataset metadata flows dialog → API → export rows → SDK dicts, and
      refuses nothing that is merely absent
- [ ] Both delete paths write tombstones; the route and SDK read them;
      workspace deletion leaves them standing
- [ ] `inputs.*_global_id` appears in the provenance block and the sheet
      whenever the deployment has an id, and never says anything when it
      has none
- [ ] A target compound carries InChIKey/InChI/SMILES end to end; a formula
      change clears them; the CAS backfill fills unambiguous rows only
- [ ] Phase 0's export-shape tests still pass untouched except for appended
      expectations
- [ ] Backend suite green on the LAN host at the final tree; frontend unit
      and SDK suites green; CHANGELOG entry appended
- [ ] Roadmap §9 Phase 1 acceptance lines hold against the real-data check
