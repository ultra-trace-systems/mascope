# The assignment bench

One persistent Mascope instance, env `bench`, that holds fixed sample sets,
so that both assignment engines can be run on the same data on demand. The
design is `docs/dev/assignment_bench.md` (milestone 6 of the assignment
quality plan). This folder has its first two steps: `bench init`, the
instance built from a sets manifest, and `bench reference`, the reference
engine at a pinned release published into it. The run and its card
(`bench run`) and the posting follow as steps 6.5 and 6.6.

## Commands

Run them from a checkout, with the dev stack up (`mascope dev up`):

```sh
uv run python tooling/assignment_bench/bench.py init tooling/assignment_bench/manifests/demo.json
uv run python tooling/assignment_bench/bench.py reference --pin v0.9.0
uv run python tooling/assignment_bench/bench.py status
```

- **`init <manifest> [--ref REF]`** builds the instance, or brings it up to
  the manifest. In order:
  1. It binds env `bench` to a free instance slot in the runtime home's
     registry (`mascope instance list` shows it).
  2. It checks `REF` (default `develop`) out into the bench's own worktree.
     It starts that worktree's backend and file converter on the slot, as
     `mascope dev run --instance` does.
  3. It seeds each set's ionization mode, the mode's calibration and
     diagnostic collections, and the set's own collections through the API.
     It loads the reference lists with `mascope reference seed`.
  4. It uploads every file the instance does not hold yet, through the
     resumable upload route, as the File Agent does. Each file goes with its
     SHA-256, which is how a sample file is found again. An upload is tried
     again only on a 503, which the server answers until a file converter
     connects, or a dropped connection. A refused token, name or hash is
     reported at once.
  5. It waits for the pipeline, then checks every sample. A sample must
     have resolved its set's mode and have peaks, and its file must have
     finished processing.

  It writes each sample's id under its set's name in `state.json`, with the
  commit of the build that ingested it, and stops the backend. The database
  and the filestore stay. `init` names every sample that failed a check and
  exits 1. A peak count the API would not give is named as such, not as a
  sample without peaks. A fresh instance has no
  ionization modes of its own, so a set whose mode did not load shows up
  here as samples that resolved none.
- **A second `init` over the same manifest does nothing** and starts
  nothing. A manifest that grew gets only its new files. A later `init`
  uploads again a file that never reached the instance, and checks again
  one that came in with a problem. A manifest that changed only how its
  sets are read (a label, profile, class or window) is recorded in the
  state with nothing started. One that dropped a set or a file, or changed
  the mode of a set already ingested, is refused: the instance cannot
  forget what it holds.
- **`init --rebuild`** drops the env's database and filestore, then builds
  again.
- **`init` touches only the bench's own env.** Before it drops or binds
  anything, it checks the env: either the registry binds it to the bench's
  worktree, or nothing of it exists yet (no binding, no database, no env
  folder). Any other env belongs to another instance, and `init` refuses
  it.
- **`reference --pin vX.Y.Z`** puts the reference engine, peaky, at a
  release tag beside the in-app runs; the next section has the detail.
- **`status`** prints the env and slot, the build the last `init` started,
  every set with its sample counts and the builds that ingested it, the
  reference pin with peaky's version string and the `mascope-tools` it
  scored with, and the last cards. It needs no runtime home.
- `--checkout DIR` names a `corpus checkout` for a manifest that names
  corpus objects. `--keep-running` leaves the backend up. `--wait` queues
  behind another bench command. `--home` and `--env` put the bench
  elsewhere, for a second bench on one box.

`init` takes the runtime home it finds (`MASCOPE_PATH`) and that home's
slot registry, so the same command builds a bench on any dev stack. The
demo manifest needs nothing but the demo bundle, which `init` fetches when
it is not cached. That makes it the set a CI run can build too.

## The reference at a pin

`bench reference --pin vX.Y.Z` reads the reference at a tagged release,
never at a pre-release or a branch (decision 29 of the assignment quality
plan). In order:

1. It clones peaky into the bench's home the first time (`--peaky-url`
   names another source) and fetches its tags.
2. It exports the tag's tree and installs it with
   `uv tool install --python 3.12` into a tool environment of the bench's
   own (`reference/tools`, with `UV_TOOL_DIR` and `UV_TOOL_BIN_DIR` pointed
   there). A pin installed before is reused. `--reinstall` installs it
   again and resolves its dependencies afresh.
3. It asks that peaky for its version string, the `mascope-tools` version
   it scores with, and which of the sets' profiles it knows.
4. It starts the instance's backend with peak assignment switched on, which
   the import route needs, and writes `bench.env` afresh.
5. For every sample of every set it runs `peaky assign` with the set's
   profile, and with `--ppm` and `--search-ppm` from the set's window when
   the manifest names one. It then runs `peaky publish` with that version
   string. The run lands under the engine badge `peaky`. Peaky's batch
   command picks its own samples, so each sample runs alone. A per-sample
   publish carries every role and peaky's own tier on each row.
6. It stops the backend.

peaky runs with only the bench's URL and token: the caller's own
`MASCOPE_*` variables are dropped, and `HOME` is the bench's, so a sourced
testbed env cannot send it elsewhere. `--jobs N` runs N samples at once
(default 2), and `--set NAME` limits a call to the sets named.

**What is cached.** A published sample is kept by pin, set, sample and
`mascope-tools` version. A second call at the same pin publishes nothing
and starts nothing. A new pin, or a reinstall that resolved another
`mascope-tools`, publishes every set again. The call then lists the sets
whose reference changed since the previous pin, sample by sample: the
peak, role, formula, adduct and tier of each ledger row, compared as a
digest. That list is the first reading of a peaky release's boundary
review (step 5.1). A sample that failed is named, kept with its error,
and run again by the next call. A publish that broke off resumes: its
import id is derived from the pin, the library, the sample and the
ledger.

**What is reported.** A set whose profile the pinned peaky does not know
is named as not run, in the output and by `status`. So is a sample `init`
left with a problem. Either makes the call exit 1, as a failed sample does.

## The manifest

```json
{
  "schema": "mascope-bench-manifest/1",
  "name": "demo",
  "sets": {
    "Db": {
      "label": "demo bundle, bromide CIMS, negative",
      "instrument_class": "orbitrap",
      "mode": "Bromide m/z 82-750",
      "profile": "BR",
      "reference_window_ppm": null,
      "seed": {"bundle": "1.4.4"},
      "collections": [],
      "reference_lists": "default",
      "files": [{"bundle": "1.4.4", "name": "Orbion_neg_Br_NoRI_20250811142350.raw"}]
    }
  }
}
```

- **A set** is named as the gate card names one
  (`tooling/assignment_compare`): an upper-case letter, then at most one
  digit or lower-case letter, then at most a sign (`D`, `C2`, `Db`, `I+`).
- **`files`** names each file in one of two forms:
  - `{"bundle": V, "name": N}` is sample file `N` of the published demo
    bundle `V`, from the CLI's bundle cache (`mascope demo fetch`).
  - `{"sha256": H}` is fleet regression corpus object `H`. It is read from
    a `corpus checkout`, whose `manifest.json` gives the file's name.
- **`mode`** is the ionization mode every sample of the set must resolve
  to. **`seed`** says where the mode is loaded from: `{"bundle": V}` is the
  bundle's own `seed/` dump, and `{"corpus": "seed.json"}` is the
  checkout's seed. **`collections`** names further target collections to
  load from the same seed.
- **`reference_lists`** is `"default"` (every shipped list that loads by
  default) or a list of list ids (`mascope reference seed --list`).
- **`profile`** is the assignment profile the set is read under (`BR`,
  `UR`, ...). **`instrument_class`** is `orbitrap` or `tof`.
- **`reference_window_ppm`** is `null` for the reference engine's own
  defaults. Otherwise it is `{"trust": ppm, "search": ppm}`, as the TOF sets
  ran at 8 and 25 ppm.

`manifests/demo.json` is the public manifest. It holds the demo bundle's
MS1 acquisitions at the CLI's default bundle version, one set per
chemistry: `Db` for bromide (80 files) and `Du` for uronium (81 files). The
bundle's four MS2 acquisitions are left out, since the bench reads MS1
assignment. When the default bundle version moves, the manifest moves with
it; the CLI suite's `test_assignment_bench.py` fails until it does.

**A manifest that names corpus objects is private.** The corpus is
customer-derived, so such a manifest lives beside the bench, never in the
repository.

## The bench's home

`$MASCOPE_PATH/.runtime/bench`, or `--home` or `MASCOPE_BENCH_HOME`:

| path | what |
|---|---|
| `manifest.json` | the manifest the last `init` built from |
| `state.json` | env, slot, the last build started, what was seeded, every sample by set with its build, the reference pin, the cards |
| `creds.json` | the bench account, made on the instance's first start |
| `bench.env` | `MASCOPE_URL` and an SDK token (`MASCOPE_ACCESS_TOKEN`) for the instance |
| `worktree/` | the git worktree the backend runs from |
| `logs/` | one backend log per start |
| `cards/` | the cards of later steps |
| `reference/peaky/` | the clone of peaky the pins are installed from |
| `reference/src/<pin>/`, `reference/tools/`, `reference/bin/` | the exported tag and its tool environment |
| `reference/installed.json` | the pin and commit installed |
| `reference/runs/<pin>/<set>/<sample>/` | each sample's ledger, manifest and peaky log |

The runtime home is private to the box. The sample ids and a private
manifest's names stay there, never in a checkout.
