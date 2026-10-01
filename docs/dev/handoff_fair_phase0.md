# Handoff - FAIR Phase 0 (provenance & identifiers)

*The plan is [fair_roadmap.md](fair_roadmap.md); this document hands off its
§6 Phase 0 - five tasks, no schema change, independent of the ionization
redesign and the assignment epic - and records how they were built.*

**Status: built.** T4 shipped first, as the published OpenAPI document; T1,
T2, T3 and T5 together after it. §8 lists where the build departed from the
plan as first written, and why. §9 is what Phase 0 leaves for later phases.

The phase-level acceptance criterion (roadmap §9): *an exported spreadsheet
names the Mascope version, engine version, instrument, method and calibration
that produced it, and every row can be traced back to a record id.*

## 1. Where each task lives

| Task | What | Where |
|---|---|---|
| T1 | The provenance block, one implementation | `mascope_backend.provenance` (`build_provenance`, `flatten_provenance`) |
| T1 | The deployment id | `mascope_backend.deployment`; `[backend] deployment_id` in `mascope_runtime.config` |
| T1 | `GET /api/provenance` | `mascope_backend/api/new/provenance/routes.py` |
| T2 + T3 | Ids, acquisition context, Provenance sheet | `api/controllers/sample/batches/export/service.py` |
| T2 | Ids in the two peak CSVs | `sample_batch_export_peaks`, `sample_item_export_peaks` |
| T4 | Static OpenAPI document | `mascope_backend.openapi`, the `openapi-build` stage of `server/frontend/Dockerfile`, the render step of `tests.yaml` |
| T5 | Provenance on SDK frames | `MascopeClient.provenance()` and `_with_provenance` in `libraries/sdk/src/mascope_sdk/client.py` |

Operator documentation: `docs/maintaining.md`, *Deployment identity*. SDK
documentation: the SDK README, *Provenance*.

## 2. Decisions consumed - settled, do not relitigate

All five roadmap §8 decisions were settled on 2026-08-20, each per the
recommendation recorded there:

1. **Ambition: through Phase 3** (deposit-ready packages). Phase 0 is that
   package's foundation - every key and column it added is a contract.
2. **License granularity: dataset-level, nullable, inheritable.** Phase 1.
3. **Identifier namespace: a configured `deployment_id`, defaulting to a
   generated, persisted value.** Phase 0 implements the *resolution*; the
   `<namespace>:<entity>:<nanoid>` exported-id format is Phase 1 item 7.
4. **Attribution: structured but optional.** Phase 1.
5. **Public read path: separate call, out of scope.** Phase 0 adds no
   anonymous surface: `GET /api/provenance` answers signed-in users and API
   tokens only, and the deployment id stays out of `[meta]`, which the
   frontend publishes before sign-in.

## 3. Ground rules - still binding on anything that touches these surfaces

- **No Alembic migration for Phase 0 work.** A task that appears to need one
  is a scoping error.
- **Additive only on export surfaces.** Existing sheets, columns and rows keep
  their names and order; additions append. Notebooks and downstream parsers
  consume the earlier shapes. The tests pin the legacy order explicitly.
- **Provenance keys are a contract.** A key that has shipped is never renamed
  or repurposed. Adding a key does not bump `provenance_version`; changing what
  an existing key means does.
- **One implementation.** Every consumer calls `build_provenance`; nobody
  assembles a provenance dict by hand.

## 4. T1 - the provenance block

Shape, `provenance_version` 1:

```jsonc
{
  "provenance_version": 1,
  "generated_utc": "2026-10-01T12:00:00Z",
  "deployment_id": "k3J9xQ2mP0aB7cD1",     // null when the deployment has none
  "produced_with": {
    "mascope_version": "v1.10.1",           // MASCOPE_VERSION, else "unknown"
    "match_score_version": 1,               // the process-wide switch
    "peak_assignment_engine_version": "0.5.0"
  },
  "inputs": { "dataset_id": "...", "sample_batch_id": "..." }  // only when passed
}
```

`produced_with` uses the vocabulary of the demo bundle's `manifest.json`
([demo_dataset.md](../demo_dataset.md)), because Phase 3 generalises that
manifest.

- **`mascope_version`** is `runtime.version or "unknown"` - exactly what
  `GET /api/version` reports. Compose passes `MASCOPE_VERSION` into the
  containers, and `mascope dev run` exports a git-derived one before it starts
  the backend, so the backend needs no git fallback of its own.
- **`match_score_version`** is the process-wide switch
  (`MASCOPE_MATCH_SCORE_VERSION`). A frame without the per-isotopologue columns
  v2 needs is still scored on the v1 scale (`effective_match_score_version`);
  the block records the switch, not any one result's scale, and its module
  docstring says so.
- **`peak_assignment_engine_version`** is `PEAK_ASSIGNMENT_ENGINE_VERSION`,
  reported whether or not peak assignment is switched on.
- All three describe the server when the block is generated. Where a record
  carries provenance of its own - a peak assignment run's `engine_version` and
  `config` - that record is the authority.

**The deployment id** (`mascope_backend.deployment`):

1. `[backend] deployment_id`, when configured - 1-64 letters, digits, `.`,
   `_` or `-`, starting with a letter or digit, no `:` (so it can prefix the
   Phase 1 exported ids). Validated when the config loads.
2. Otherwise the id generated on the backend's first start and kept in
   `deployment.json` at the root of the env's filestore - an `{"deployment_id",
   "generated_utc"}` record, written atomically
   (`mascope_runtime.atomic.write_json`).

Generated only by `ensure_deployment_id`, which `init_main_process` calls once,
before any worker serves, so no two processes race to create it. Reading
(`deployment_id()`) never writes, and keeps an id once it has read one, so the
route the SDK asks after every load does not reopen the file each time. An
unreadable file is logged at startup and left for an operator - a quietly
replaced id would split one deployment's exports across two names - and the
deployment reports no id until it is fixed.

**`GET /api/provenance`** returns the block without `inputs`. It is
guest-gated and `token_access`, because the SDK reads it.

## 5. T2 + T3 - identifiers and acquisition context in exports

The *Batch data* spreadsheet (`POST /sample/batches/{id}/export/spreadsheet`):

- **Batch** - after the existing rows: `Dataset ID`, `Sample batch ID`,
  `Target collection IDs`, then `Instruments` and `Method files`, every
  distinct value in the batch.
- **Samples** - after the existing columns: `Sample item ID`, `Sample file ID`,
  `Datetime UTC` (written as naive UTC: Excel stores no time zone), `Instrument`,
  `Instrument type`, `Method file`, `Polarity`, `m/z range min`,
  `m/z range max`, `Ionization mode`, `Ionization mode ID`,
  `Instrument function ID`, `m/z calibration` (the record's status: `ok`,
  `poor`, `failed`, `unfitted`, or `none` when the file has no record),
  `m/z calibration verified`, `m/z calibration error (ppm)` (the post-fit
  error).
- **Match compounds** - `Sample item ID`, `Target compound ID`.
- **Match ions** - `Sample item ID`, `Target compound ID`, `Target ion ID`.
- **Provenance** - a new last sheet: the block flattened to key/value rows
  (`produced_with.mascope_version`, ...), with `inputs` naming the dataset, the
  batch and the target collections.

Rows carry the natural keys of their records (sample item and target ids),
not the `match_*` ids, which a rematch replaces.

The *Peak data* CSVs, of a batch and of a sample: `sample_batch_id` and
`dataset_id`, appended after the existing columns. They carry no provenance
block: a constant column per row would multiply the size of a per-scan export
for a value the spreadsheet and the API already give.

## 6. T4 - static OpenAPI to the docs site

Shipped before the rest of Phase 0. `mascope_backend.openapi.render` imports
the app in a child process against a throwaway prod runtime with placeholder
secrets; the frontend image's `openapi-build` stage runs it and nginx serves
the result at `/docs/openapi.json`, linked from the user docs' *SDK & API*
page; the docs job in `tests.yaml` renders it too, so a route that cannot be
described fails CI. The prod backend still serves no schema of its own (`_docs_kwargs`).

## 7. T5 - provenance on SDK frames

- `MascopeClient.provenance()` reads `GET /api/provenance` afresh on every call,
  with the client's own timeout and retries.
- `load_peaks`, `load_peak_timeseries`, `load_peaks_by_stage`,
  `load_batch_ledger` and `load_assignments` stamp the block on
  `df.attrs["provenance"]`, beside the existing `attrs["run"]` and
  `attrs["batch_peaks"]`. Each load asks again, so a server updated mid-session
  is reported as it is, but frames from one deployment and build carry the
  same block - the first this client received for that build - because
  `pd.concat` keeps `attrs` only when every input's are equal, and two answers
  from one build differ in `generated_utc` alone.
- **Best effort:** the stamping request gets a short timeout and a single
  attempt, so it cannot hold a finished frame back. The SDK on PyPI talks to
  older servers: a 404 means a server without the route, the frame is returned
  unstamped and the server is not asked again by that client. Any other
  failure to ask - an error status, a timeout, an answer that is not a
  provenance block - is logged and the frame is still returned.
- `attrs` does not reliably survive pandas' merge or copy, and CSV and Excel
  drop it; the README says so, and shows how to keep it beside a saved file.

## 8. Where the build departed from the plan, and why

- **`deployment_id` is a `[backend]` setting, not `[meta]`.** `[meta]` is
  serialised whole into `/runtime-config.js`, which the frontend serves before
  sign-in; the id has no business on anonymous surface (decision 5).
  `[backend] reference_licenses` sits there for the same reason.
- **The generated id lives in the filestore, not `.runtime/secrets/`.** The
  prod containers mount the env directory and the filestore, and receive
  secrets only as individual compose secrets - an id written to the secrets
  directory inside a container would be regenerated with every container. The
  filestore is persistent in every deployment shape and is what the off-site
  backup copies with the database dumps, so the id survives a restore with the
  records it names. The tools that copy a filestore between deployments -
  `mascope env sync` and the demo bundle's export and seed - leave the file
  behind (`mascope_runtime.config.DEPLOYMENT_FILE` names it for both sides),
  so a copy keeps or generates its own id; a configured id travels with the
  env's config and is documented as such in `docs/maintaining.md`.
- **No git fallback for `mascope_version`.** The plan had the backend call
  `runtime.parse_version()` when `MASCOPE_VERSION` is unset; the CLI already
  exports it before launching the backend in dev, and `/api/version`'s
  semantics are deliberately "unknown where unset".
- **`GET /api/version` was already guest-gated** with token access by the time
  Phase 0 was built; the plan described it as admin-gated.
- **Acquisition context went on the Samples sheet, as columns.** The plan put
  it on the info sheet, as per-file rows; the Samples sheet is already one row
  per sample (hence per file), so appending columns gives the same per-file
  rendering without a second table on the key/value sheet. The Batch sheet
  names every distinct instrument and method file for a reader who stops
  there.
- **Ionization mode and calibration verdict, beyond the plan.** The sample's
  ionization mode is the chemistry its numbers were read with, which a
  repository cannot recover from anything else; the calibration is exported as
  its status, verification and residual - the fit's parameters mean nothing
  outside Mascope, and its seal is the server's own.
- **Human-readable column names** (`Sample item ID`), matching the sheet,
  where the plan used field names. The Provenance sheet uses the block's own
  dotted keys.
- **The SDK asks per loader call rather than caching per client**, so a
  server updated mid-session is reported as it is; it reuses the block it
  stamped before when the build has not changed, which keeps the block through
  `pd.concat`. The extra request is one small GET beside the many a loader
  makes, with a budget of its own.

## 9. What Phase 0 leaves for later

- **Namespaced exported ids** (Phase 1 item 7) build on `deployment_id()`.
- **Per-result provenance on the classic matching path** (roadmap R1.2): the
  block names the build that wrote an export, not the build that computed each
  stored match - match records carry no version of their own.
- **mzML, CV terms, Schymanski levels** (Phase 2) and the **deposit package**
  (Phase 3), whose manifest the Provenance sheet's keys anticipate.
