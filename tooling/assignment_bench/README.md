# The assignment bench

One persistent Mascope instance, env `bench`, that holds fixed sample sets,
so that both assignment engines can be run on the same data on demand. The
design is `docs/dev/assignment_bench.md` (milestone 6 of the assignment
quality plan). This folder has its first three steps: `bench init`, the
instance built from a sets manifest; `bench reference`, the reference
engine at a pinned release published into it; and `bench run`, a ref's
in-app engine on the sets beside that reference, with the plan's card and
its diff against develop. Posting the card on a PR follows as step 6.6.

## Commands

Run them from a checkout, with the dev stack up (`mascope dev up`):

```sh
uv run python tooling/assignment_bench/bench.py init tooling/assignment_bench/manifests/demo.json
uv run python tooling/assignment_bench/bench.py reference --pin v0.9.0
uv run python tooling/assignment_bench/bench.py run origin/develop --baseline
uv run python tooling/assignment_bench/bench.py run <your branch> --baseline
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
  release tag beside the in-app runs; its section below has the detail.
- **`run <ref>`** runs a ref's in-app engine on the sets and writes the
  card; **`baseline`** shows develop's rolling baseline card. Their section
  below has the detail.
- **`status`** prints the env and slot, the build the last `init` started,
  every set with its sample counts and the builds that ingested it, the
  reference pin with peaky's version string and the `mascope-tools` it
  scored with, develop's baseline card, and the last five cards with their
  ref, commit and verdict. It needs no runtime home.
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
4. When it has samples to run, it checks out the ref `init` built from
   (develop by default) at its head, since a `bench run` leaves no build
   behind, and brings the database to that head, refusing a ref older than
   the database as `run` does. It starts that backend with peak assignment
   switched on, which the import route needs, and writes `bench.env` afresh.
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
`mascope-tools` version, and by the profile and window it ran with. A
second call at the same pin publishes nothing and starts nothing. A set
whose profile or window the manifest changed (`init` records both) runs
again. A new pin, or a reinstall that resolved another `mascope-tools`,
starts a new reference block and publishes every set again, also when it
goes back to an earlier pin. The call then lists the sets whose reference
changed since the previous pin, sample by sample: the peak, role,
formula, adduct and tier of each ledger row, compared as a digest. A set
with no sample published at both pins is listed as not compared. That
list is the first reading of a peaky release's boundary review (step
5.1). A sample that failed is named, kept with its error, and run again
by the next call. A publish that broke off resumes: its import id is
derived from the block, the set, the sample, the profile, the window and
the ledger.

**What is reported.** A set whose profile the pinned peaky does not know
is named as not run, in the output and by `status`. So is a sample `init`
left with a problem. Either makes the call exit 1, as a failed sample does.

## A ref's run and its card

`bench run <ref>` judges a ref the way the plan judges a step: its in-app
engine on every bench set, the pinned reference beside it, the plan's card,
and with `--baseline` the diff against develop's last card. It runs on this
box in the caller's own session, one run at a time. In order:

1. **The build.** It checks `<ref>` out into the bench's worktree,
   `origin/<ref>` first, then a local ref or a commit. It runs
   `uv sync --frozen`, so the ref's own lock is used as it is.
2. **The database.** It reads the bench database's alembic revision and
   the ref's own migrations. A ref that does not know the database's
   revision is refused, with the revision named: a later build migrated the
   database, and alembic cannot go back on a ref that lacks it. Otherwise it
   migrates the database to the ref's head with `mascope dev migrate
   upgrade`, which keeps a dump from before.
3. **The backend.** It starts the ref's backend and file converter on the
   bench slot, with peak assignment on, and signs in. The backend has
   `--workers N` uvicorn workers (default 4), where the dev defaults have
   one: an engine run and the card's detail reads are CPU-bound in their
   worker. The bench writes the count into the env's own config layer,
   `dev.mascope.toml` in the env's folder, which every ref reads over its
   dev defaults, and removes that file again for one worker. A file there
   that the bench did not write is left as it is.
4. **The reference.** A sample whose newest completed `peaky` run is not
   the one `bench reference` published at the pin is not read. Nor is one
   with no reference run at the pin, or one `init` left with a problem. The
   card would read such a sample against another reference, or by the
   intrinsic metrics alone.
5. **The engine.** It runs the in-app engine on every other sample, as an
   assign request with no config: the engine's own defaults, as the gate's
   runs had. `--jobs N` samples run at once (default 4). With `--decoys` each
   sample's decoy arms follow its run, made against it as their control
   (step 5.2): 9 ppm either way and the swapped adducts on an Orbitrap set,
   0.35 Da and the swapped adducts on a TOF set. An arm the server refuses,
   a shift inside the sample's window for one, is counted as refused, not as
   a failure. It waits for every run.
6. **The time budget.** Each set has `--budget-min` minutes (default 120)
   for its runs. A set that runs out is left off the card whole, so a diff
   never compares half a set. The runs it left behind are released when the
   backend next starts. A Ctrl-C, or a job that breaks on any sample, the
   first or one behind a run still going, stops the runs in flight from
   being waited on at their next poll: no arm is asked for after it, the
   samples still queued are not started, and the cleanup follows within
   one poll (10 s), or within a request's 2-minute timeout should the
   server hang.
7. **The checks.** A sample is read only where its newest completed in-app
   run is the one this run made and its newest reference run is still the
   pin's. A sample whose run failed or that the server would not assign is
   left out and counted.
8. **The card.** `compare_runs.py --card --gate`, with `--decoy` when the
   arms ran, reads the sets and writes one card per set and one pooled
   (`tooling/assignment_compare/README.md`). It is the comparison tool of
   the checkout `bench.py` runs from, not the ref's. A branch that changed
   the metrics would otherwise judge itself by them. It reads every
   committed row's provenance one request at a time (#3134), so it takes
   longer than the runs ("What it costs", below). It has no budget, since
   its time grows with the rows it reads; it writes a line per sample, and
   one that writes nothing for 30 minutes is taken for hung and stopped.
9. **The scrub.** The comparison is given a deny list: every name and id
   the instance holds (its workspaces, datasets, batches, samples, files,
   modes and collections, and its instruments by name, by the names its
   files, datasets and modes gave them, and by serial number), every
   sample's file name and id, the reference's and this run's run ids, and
   the hosts. The bench then scrubs every file the comparison wrote again
   with the same list. A card either refuses is reported, by file, and never
   written.
10. **The cleanup.** It stops the ref's backend and removes its worktree,
    also when a step fails. The instance stays: its env, slot, database and
    filestore.

It exits 0 when the card passes its gate with no sample left out, 1 when
the gate fails or something was left out or a decoy run failed, and 2 when
no card was written: refused by the scrub, nothing could be read, or a step
could not go on.

**Develop's rolling baseline.** `bench run origin/develop --baseline`
diffs the card against develop's last baseline card, then becomes the
baseline itself. That happens only when the ref is develop's head, the run
read every bench set, and nothing was left out. `bench run <branch>
--baseline` diffs against it and leaves it alone. A run of some sets diffs
those sets' columns alone: the baseline's pooled column pools other sets, so
the card names its own as not on the baseline. Without a baseline yet, the
run says so and makes no diff. `bench baseline` prints the baseline, and
`bench baseline <card>` makes an earlier card of develop the baseline. The
card is named by its folder under `cards/`, and its commit must be on
develop. It is held to the rule a run is held to, but for being develop's
head: a gate verdict, every set the bench holds now on the card, and
nothing left out. The baseline records the sets the card read.

**One run at a time.** A run waits for the bench's lock while another bench
command holds it, and says so; `--no-wait` refuses instead. `init` and
`reference` take the same lock.

**What it costs.** On the agent box, with four workers and four samples at
once, the engine took 12 minutes on Db and 14 on Du, and the card 67
minutes over both: about an hour and a half a run. One worker took 26 and
31 minutes for the runs, and its card had read 126 of the 161 samples
after 2 hours 47 minutes. The decoy arms run three more engine runs per
Orbitrap sample.

**What a run writes**, in `cards/<ref>/<timestamp>/` of the bench's home
(`origin/develop` is `origin-develop`):

| file | what | leaves the box |
|---|---|---|
| `card.md`, `card.json`, `card_<set>.md`, `card_<set>.json` | the card (`tooling/assignment_compare/README.md`, "What the card writes") | yes, scrubbed |
| `diff.md`, `diff.json` | with `--baseline`: the readings that moved since develop's baseline, and which card that was | yes, scrubbed |
| `run.json` | the run: the ref and its commit, develop's head, the migration, the pin, per set the samples read and left out by reason, the minutes it took and each decoy arm's outcomes, the verdict, the baseline, whether it became the baseline | yes, scrubbed |
| `run.log` | every step, and each sample by id with what became of its runs and why | no: it names samples |
| `work/` | the comparison's input: the sets with their sample ids, the deny list | no |

The console says what each step did by set and count, then prints the card,
the gate line and the diff line. It names no sample: those are in
`run.log`. Paste the card or the diff, never `run.log`.

**After a branch with a migration of its own.** Benching such a branch
moves the bench database to the branch's revision. develop is then refused,
by the rule above, until that migration is on develop. A branch whose
migration never lands leaves the bench where only `init --rebuild` brings
it back (#3171).

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
| `state.json` | env, slot, the last build `init` started, what was seeded, every sample by set with its build, the reference pin, the cards, develop's baseline card |
| `creds.json` | the bench account, made on the instance's first start |
| `bench.env` | `MASCOPE_URL` and an SDK token (`MASCOPE_ACCESS_TOKEN`) for the instance |
| `worktree/` | the git worktree the backend runs from; a run removes it when it is done, and the next command checks its build out again |
| `logs/` | one backend log per start |
| `bench.lock` | held by the bench command that runs; it names its pid |
| `cards/<ref>/<timestamp>/` | one run's card, diff, summary, run log and inputs |
| `reference/peaky/` | the clone of peaky the pins are installed from |
| `reference/src/<pin>/`, `reference/tools/`, `reference/bin/` | the exported tag and its tool environment |
| `reference/installed.json` | the pin and commit installed |
| `reference/runs/<pin>/<set>/<sample>/` | each sample's ledger, manifest and peaky log |

The runtime home is private to the box. The sample ids and a private
manifest's names stay there, never in a checkout.
