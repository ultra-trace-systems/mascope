# Demo dataset & end-to-end reproducibility

Mascope ships a public **demo dataset** so a newcomer can install Mascope and
immediately explore real data, and the same artifact powers an **end-to-end
reproducibility test**. One artifact serves both: the raw instrument files are
the single source of truth, and everything else, an instant-load database
snapshot and the golden outputs the reproducibility test asserts against, is
_derived_ from them by a documented, repeatable build. The local entry point is
one command: `mascope demo`.

## Contents

- [The demo bundle](#the-demo-bundle)
- [Where it lives](#where-it-lives-zenodo)
- [The `mascope demo` command](#the-mascope-demo-command)
- [End-to-end reproducibility test](#end-to-end-reproducibility-test)
- [De-identification](#de-identification)
- [Step-by-step: create, load, and update](#step-by-step-create-load-and-update)

## The demo bundle

The canonical artifact is a single versioned **demo bundle**:

```
mascope-demo-dataset/
  raw/             # de-identified Thermo .raw files - the SOURCE OF TRUTH
  seed/            # AUTHORED reference data (ionization modes, instrument config,
                   #   calibration/diagnostic collections, demo user); restored by
                   #   --rebuild BEFORE ingesting raw
  snapshot/        # DERIVED pg_dump (.dump) + filestore tree (the "instant" path)
  expected/        # DERIVED golden outputs (parquet) the reproducibility test asserts
  manifest.json    # names, instrument, sha256s, tool versions, tolerances
  DATA_LICENSE     # data license (e.g. CC-BY-4.0), separate from the code license
  README.md        # human-facing description of the measurement
```

The **seed** is _authored_ reference data the pipeline needs before it can
calibrate + match (it cannot be derived from the raw files); **snapshot** and
**expected** are _derived_ by running the pipeline. A from-scratch rebuild
restores the frozen seed, then regenerates everything else from the raw files,
so the authored reference data is a checksummed, reproducible fixture.

### `manifest.json`

The manifest makes a bundle self-describing, so `mascope demo` and the
reproducibility test can verify exactly what they loaded:

```jsonc
{
  "bundle_version": "1.0.0",
  "measurement": {
    "instrument": "Orbion",
    "instrument_type": "orbi",
    "acquired": "2025-08-11",
  },
  "produced_with": {
    "mascope_version": "<git describe at snapshot time>",
    "opentfraw_version": "<pinned reader version>",
  },
  "raw": [{ "name": "Orbion_pos_Ur...raw", "sha256": "...", "bytes": 531701 }],
  "seed": { "dump": "seed/mascope_demo.dump", "sha256": "..." },
  "snapshot": {
    "dump": "snapshot/mascope_demo.dump",
    "sha256": "...",
    "filestore": "snapshot/filestore",
  },
  "expected": { "peaks": "expected/peaks.parquet", "sha256": "..." },
  "tolerances": { "mz_ppm": 0.1, "intensity_rel": 0.01, "area_rel": 0.02 },
}
```

`produced_with.opentfraw_version` is load-bearing: the reproducibility test pins
it so a reader change that shifts the numbers is caught (see
[OpenTFRaw migration](../libraries/thermo/OpenTFRaw_migration.md)).

## Where it lives (Zenodo)

The bundle is published to **Zenodo**, which gives a citable DOI and
**immutable** versions: the bytes are frozen, so the reproducibility test can
assert a checksum and trust it indefinitely. The download URL, archive MD5, and
DOI are recorded in the bundle registry
(`tooling/cli/src/mascope_cli/cmd/demo/bundles.py`) and consumed by
`mascope demo fetch`.

## The `mascope demo` command

A top-level CLI app (`tooling/cli/src/mascope_cli/cmd/demo/`) that always
operates on a dedicated `demo` runtime env in `dev` mode, so it never touches a
developer's working env.

> **Just want to run the demo (no clone)?** Use the containerized stack instead:
> `docker compose -f docker-compose.demo.yaml up` pulls the published images and
> loads the same bundle (its `demo_init` step runs `tooling/demo-init.sh`, which
> reuses the fetch + `seed_demo` paths below). The `mascope demo` command here is
> the from-source path for contributors and maintainers.

```sh
mascope demo                 # fetch (if needed) -> restore snapshot -> launch
mascope demo --fresh         # clean empty env (no bundle) for authoring reference data
mascope demo --rebuild       # ingest from raw through the real pipeline instead of restoring
mascope demo --local <dir>   # use a local bundle dir instead of fetching (pre-publish testing)
mascope demo --no-launch     # seed only, don't start the app
mascope demo fetch           # download + checksum-verify the bundle into the local cache
mascope demo verify          # run the reproducibility comparison against expected/ goldens
mascope demo snapshot        # MAINTAINER: (re)build a bundle from raw/
```

### Seed path (default): the "instant" experience

`mascope demo` pins `MASCOPE_ENV=demo`, creates the dev secrets and `demo` env
dirs if missing, starts PostgreSQL + Redis, fetches and checksum-verifies the
bundle from Zenodo, restores `snapshot/mascope_demo.dump` + the filestore into
the demo env, launches `backend`/`frontend`/`file-converter`, seeds the fixed
demo credentials, and prints the URL, login, and SDK token.

The seeded demo user + token are the real unlock for a low threshold: they
remove the owner-registration and token-generation steps, and make the SDK
(`pip install mascope_sdk`) work against the local demo out of the box.

### Demo credentials

Fixed and well-known, seeded after the database is ready by `seed_demo.py` (also
runnable via `mascope dev db script run seed_demo`). Idempotent: it resets the
password and re-creates the tokens with fresh timestamps each run, so they work
regardless of how old the restored snapshot is.

| What      | Value                               |
| --------- | ----------------------------------- |
| Web login | `demo@mascope.app` / `mascope-demo` |
| SDK token | `mascope_demo_sdk_token`            |
| Role      | `owner`, superuser                  |

The demo user is an **owner** (satisfies the app's "first owner" registration
gate). The seed also creates `file-converter` and `file-agent` service tokens.
The upload route needs the former, the rebuild uploader uses the latter. Tokens
are seeded at demo time (not baked into the snapshot) so they never expire.
These credentials are **public and for local demo use only**, never seed them
into a real deployment.

### Rebuild path (`--rebuild`): the "reproducibility" experience

Same as the seed path, but instead of restoring the snapshot it restores the
**reference seed** (so the pipeline has the ionization modes, instrument config,
and calibration/diagnostic collections), launches the app, then a background
thread uploads each `raw/` file to the real `POST /api/sample/files/upload`
endpoint — as the File Agent does (bearer `file-agent` token +
`X-Service-Name: file-agent`). The `file-converter` ingests them and runs the
real `RawProcessor` -> peak detection -> matching pipeline.

Direct drops into `filestreams/` cannot work: the file context (uploader
identity + token) is registered only by the upload endpoint, so a dropped file
is "not registered" and fails. This path runs the real pipeline, so it is what
the reproducibility test drives.

**Uploading is gated on the converter, not just the backend.** The upload
endpoint emits each file's context over the `/file-converter` socket at upload
time, so a file uploaded before the converter has connected has its context
emitted into the void and fails deep in processing with "not registered in file
converter service" — quarantined into `<env>/filestreams/failed_files/` while
both sample batches still finish `ready`, which is exactly how a rebuild ends up
short without saying so. The uploader therefore samples the backend's
`mascope:service:file-converter` presence key *before* the stack starts and
holds the first upload until a converter has connected since (waiting for the
key to merely exist would be satisfied by a stale key from a killed run, or by
another worktree's converter — the key is not env-scoped). It then keeps
sweeping `failed_files/` for the rest of the run, re-uploading the bundle's own
files up to twice each and reporting by name any that still never made it.

## End-to-end reproducibility test

Location: `server/backend/tests/system/reproducibility/`. It is the asserted
form of the `--rebuild` path, driven against a **rebuild-mode demo compose
stack** (`MASCOPE_DEMO_REBUILD=1` makes `demo_init` restore the reference seed
instead of the snapshot, so the stack comes up with no samples):

```sh
MASCOPE_DEMO_REBUILD=1 docker compose -f docker-compose.demo.yaml up -d
MASCOPE_REPRO_TEST=1 uv run pytest server/backend/tests/system/reproducibility/ -v
```

What it asserts:

1. Assert input integrity: every `raw/` file SHA-256 matches the manifest.
2. Run the real conversion + peak detection + matching pipeline on `raw/`.
3. Export the produced peaks and compare against `expected/peaks.parquet`. The
   goldens are the **found** isotope peaks (`match_score > 0`). Matching scores
   every possible isotopologue, but the vast majority have negligible abundance
   and are never detected. Each peak is joined on the **stable key**
   `(filename, target_isotope_id)` — both survive a rebuild, unlike
   `sample_item_id`, which is regenerated every ingestion and asserted within
   the manifest's tolerances (m/z within `mz_ppm`, intensity within
   `intensity_rel`, no unexpected peaks).
4. Pin `opentfraw` to `produced_with.opentfraw_version`; a mismatch fails loudly.

Step 2 waits for the pipeline to settle rather than for a fixed time, so a file
that fell out mid-run would otherwise show up only as a timeout. Once the
counters stop moving the test looks in `failed_files/` first: a quarantined file
never produces sample items, so it fails immediately naming them and the usual
cause, instead of idling out the stall window with a bare "pipeline stalled".

The export seam is
`mascope_backend.db.scripts.export_goldens.get_golden_peaks` (a plain ORM read);
the comparison (`compare_peaks`) is shared with `mascope demo verify` and
unit-tested in `tooling/cli/tests/test_demo_verify.py`, so the CLI and the test
never drift. Because the key includes `filename`, goldens must be captured from
a from-bundle rebuild (step A3) so the stored filenames match a reproducibility
rebuild of the same `raw/`.

Booting the stack and ingesting raw files makes this a **heavy** test. It does
not run per-PR: CI runs it nightly against the default branch and on manual
dispatch (`.github/workflows/reproducibility.yaml`) - dispatch it on a branch
before merging a pipeline-touching PR. When the pipeline legitimately changes
the numbers, regenerate the goldens with `mascope demo snapshot --update`,
review the diff, and cut a new bundle version. Goldens are never updated
silently.

## De-identification

The data is a **real, de-identified** measurement, so the published bundle must
not leak anything sensitive. `mascope demo snapshot` writes a de-identification
**report** (`<BUNDLE>.deid_report.md`) of everything readable from the files for
human sign-off. Candidate identifiers here:

- **Filenames** embed the instrument label, a redundant timestamp, a run index,
  the sample short-names (`Ur`, `Br`), and a compact acquisition stamp. The
  instrument label is aliased and the redundant timestamp + run index are
  stripped (below); the sample short-names are standards (kept) and the compact
  stamp is kept so the timeseries stays meaningful.
- **Embedded `.raw` metadata** may include operator name, sample comments, and
  instrument serial. The report surfaces these; the maintainer decides what (if
  anything) to scrub. Deep rewriting of `.raw` internals is avoided unless
  something genuinely sensitive is flagged, the publishability call rests with
  the data owner, not the tooling.

### Filename de-identification

The build renames every raw file, keeping byte content (and checksums)
unchanged:

```
KORBI2_2025.08.11-14h23m12s_pos_Ur_NoRI_1_20250811142302.raw
  -> Orbion_pos_Ur_NoRI_20250811142302.raw
```

The published `manifest.json` records only de-identified names. The report lists
the full original -> published map, so it contains the real instrument label and
**must never be published**; the builder writes it as a _sibling_ of the bundle
directory (`<BUNDLE>.deid_report.md`, never inside `<BUNDLE>/`) so it cannot be
archived by accident.

## Step-by-step: create, load, and update

Commands run from the repo root with the venv active (or `uv run`-prefixed);
Docker must be running. `<RAW>` is the directory of raw files (kept outside the
repo); `<BUNDLE>` is the working bundle directory the tooling accumulates into.

### A. Create a new demo dataset

1. **Author the reference data.** Bring up a clean, empty demo env:

   ```sh
   mascope demo --fresh            # offers to reset mascope_demo; say yes
   ```

   Log into the UI (`http://localhost:8090`, `demo@mascope.app` / `mascope-demo`)
   and create what the pipeline needs _before_ it can process raw: ionization
   modes/mechanisms, the instrument config, and the calibration + diagnostic
   target collections. Do **not** ingest raw yet.

2. **Capture the reference seed:**

   ```sh
   mascope demo snapshot --raw <RAW> --out <BUNDLE> --seed
   ```

   Copies + de-identifies `raw/`, writes `manifest.json` + the
   `<BUNDLE>.deid_report.md` sign-off report, and exports `seed/mascope_demo.dump`.
   Stop the demo (Ctrl+C) before the next step so the database can be reset.

3. **Rebuild from the seed** (restores the seed, ingests raw through the real
   endpoint, runs the pipeline):

   ```sh
   mascope demo --rebuild --local <BUNDLE>
   ```

   Watch for `Uploading N raw file(s)`; in the UI, confirm calibration and
   run/confirm matching against the target collection.

4. **Capture the full snapshot + goldens:**

   ```sh
   mascope demo snapshot --out <BUNDLE> --update
   ```

   `--raw` is omitted (step 2 already copied it). Exports `snapshot/` (pg_dump +
   filestore) and `expected/` goldens, preserving the `seed/` block. `<BUNDLE>`
   now holds `raw/ + seed/ + snapshot/ + expected/ + manifest.json`.

   Before writing `expected/`, this checks that the demo database actually
   covers the manifest's whole `raw` set and **refuses to write goldens** that
   would not, naming the files that are missing (`build_bundle.check_raw_coverage`).
   The database stores the filename the converter reconstructs from the file's
   own metadata (`Orbion_2025.08.11-14h23m50s_neg_Br_NoRI_20250811142350`), not
   the bundle's de-identified name, so the comparison keys on the compact
   14-digit acquisition stamp the two share. If it refuses, go back to A3: a
   short rebuild is a rebuild that dropped files, and goldens captured from it
   freeze an incomplete expectation that nothing downstream questions. This is
   the check that would have caught bundle v1.0.0 shipping with 152 of its 161
   files.

5. **Publish** to Zenodo and register the bundle — see [D](#d-publish-to-zenodo-versioning).
   Confirm `<BUNDLE>.deid_report.md` before publishing.

### B. Load the demo (newcomer)

```sh
mascope demo            # instant: fetch bundle -> restore snapshot -> launch
mascope demo --rebuild  # full pipeline: fetch -> restore seed -> upload raw -> launch
```

Both seed the fixed credentials and print the URL + login + SDK token.
`mascope demo fetch` downloads + checksum-verifies without launching;
`mascope demo info` shows the registered/cached status.

### C. Update an existing demo dataset

- **Re-author reference data:** repeat A1-A2 to refresh `seed/`, then A3-A4 to
  regenerate `snapshot/` + goldens.
- **Pipeline changed the numbers:** re-run A3-A4, **review the golden diff vs the
  previous bundle**, and pin the new `opentfraw` version.
- **Add/replace raw files:** update `<RAW>`, re-run from A2.

Either way, publish as a **new Zenodo version** (D) and bump the registry.

### D. Publish to Zenodo (versioning)

The published artifact is a zip of `<BUNDLE>`. The sign-off report lives outside
`<BUNDLE>`, so a plain archive contains nothing sensitive.

1. **Package:**

   ```sh
   cd <BUNDLE> && zip -r ../mascope-demo-v1.zip . && cd -                              # POSIX
   ```

   ```powershell
   Compress-Archive -Path <BUNDLE>\* -DestinationPath ..\mascope-demo-v1.zip -Force   # Windows
   ```

2. **Upload to Zenodo.** First release: new upload, attach the zip, fill in
   authors/description/license (e.g. CC-BY-4.0), publish — Zenodo mints a
   **concept DOI** (always latest) and a **version DOI** (this version). Later
   releases: open the record -> **New version** -> replace the file -> publish
   (keeps the concept DOI, mints a new version DOI). Never edit a published
   version's files in place.

3. **Get the file URL** (the direct `.../records/<id>/files/<name>.zip` link) and
   the archive **MD5** Zenodo displays next to the file — no local hashing needed;
   per-file integrity uses the manifest SHA-256s.

4. **Register** the version in
   [`bundles.py`](../tooling/cli/src/mascope_cli/cmd/demo/bundles.py):

   ```python
   BUNDLES = {
       "1.0.0": Bundle(
           version="1.0.0",
           url="https://zenodo.org/records/<id>/files/<name>.zip",
           archive_md5="<MD5 shown by Zenodo>",
           doi="10.5281/zenodo.<version-id>",
       ),
   }
   DEFAULT_BUNDLE_VERSION = "1.0.0"
   ```

   Use a semver version (Zenodo recommends it): PATCH when a pipeline/scoring
   change regenerates the goldens, MINOR when samples or reference collections
   are added, MAJOR when the underlying data changes. Keep old entries so pinned
   reproducibility runs stay resolvable; reference the concept DOI in the
   top-level README.

5. **Verify** end to end:

   ```sh
   mascope demo fetch --force      # download + checksum-verify
   mascope demo                    # instant load from the published snapshot
   ```
