# Assignment timing

Times peak assignment's two uses on a copy of the demo dataset, so the
numbers can be measured again after a change (MAS-1179). The findings and
the tables are in `docs/dev/assignment_performance.md`.

- **Fold-in.** A batch of N samples is assigned and consistent. One more
  sample arrives the way ingest folds it (`auto_assign_sample_peaks`), and
  a pass of the batch stages brings the batch back to consistent. The time
  is the arrival's fold plus the pass.
- **Whole batch.** **Assign batch** from nothing: one pass that folds every
  sample first, then runs every stage.

`perf.py` runs both in its own process with the backend's code, against an
env's database and filestore. `profile_report.py` reads a py-spy profile of
such a run down to the functions. Neither is the bench
(`tooling/assignment_bench/`): the bench reads what the engine decides, this
reads how long it takes.

## What a run records

Per step (the arrival's fold, the pass), per batch stage, per judging of a
sample and per pipeline stage inside a judging or a fold:

- wall time, and CPU time beside it: CPU over wall is the number of cores
  busy, so 1.00 means one core worked and the others stood idle;
- the time spent executing SQL and the number of statements, and the reads
  that repeat a read already made in the same span, same statement and
  same parameters ("repeated reads");
- the peak resident memory of the process while the span ran.

The timers wrap the backend's functions from outside (`perf.instrument`);
nothing in the backend changes. A pass's own record of each stage's start
and end is kept beside them (`pass.stage_spans` in the JSON).

A fold-in or an arrival also records where the sample's peaks landed (on a
batch peak that already had a consensus formula, on one without, or on a new
one) and how many members the batch held before changed a decision: the
consensus formula, a tier, an isotope line, or a record: the series, a batch
check, a stamped line, a known-species claim, a water-ladder rung or a merged
line, with the verdicts nested in them. Measured numbers in those records
(an r, a ppm, a share, the files holding a species) are not decisions and are
not compared, nor are the co-variation and the flat-series flag, which are
readings. While a pass runs, the script keeps its copy of the ledger's
decisions on disk (`<out>.digests/`), so the pass's peak memory is the
backend's.

**A run that did not do what it timed is refused.** Ingest skips a fold it
may not run and swallows one that fails, so an arrival that landed no peak
in the batch, or a pass that did not end `success` (`partial`, `cancelled`,
`skipped`), still writes its JSON and tables, marked "Not a measurement",
and the command exits non-zero. The step table names each step's outcome:
`pass (success)`, `arrival fold (1669 peaks landed)`. A renamed backend
function the timers wrap stops the run before it starts.

## Recipe

From a checkout with `uv sync --all-groups` done and the dev stack up. Each
measurement needs an env of its own: a copy of the demo env, migrated to the
checkout's schema. Nothing else may write in it while a run goes.

```sh
export PYTHONUTF8=1
uv run mascope env create perfbase
uv run mascope dev db clone perfbase --source demo --yes
cp -r "$MASCOPE_PATH/.runtime/env/demo/filestore" "$MASCOPE_PATH/.runtime/env/perfbase/"
MASCOPE_ENV=perfbase uv run mascope dev migrate upgrade
# then one env per run, cloned from perfbase the same way (db clone + filestore copy)
```

The batch is the demo's largest, its uronium batch (81 samples). `limit`
keeps its first N samples in acquisition order and moves the rest into a
holding batch of the same dataset; an arrival moves the earliest held sample
back in.

```sh
B=<the uronium batch's id>
export MASCOPE_ENV=perf80
uv run python tooling/assignment_perf/perf.py limit $B 80
uv run python tooling/assignment_perf/perf.py whole $B whole80.json    # Assign batch, from nothing
uv run python tooling/assignment_perf/perf.py foldin $B foldin80.json  # the 81st sample arrives
uv run python tooling/assignment_perf/perf.py table foldin80.json --start whole80.json
```

Arrivals one after another, for how much one arrival changes and what
deferring the batch stages costs (`--every 1` a pass after each arrival,
`--every 5` after every fifth and the last, `--every 0` none, the fold
alone):

```sh
uv run python tooling/assignment_perf/perf.py limit $B 4
uv run python tooling/assignment_perf/perf.py whole $B start.json
uv run python tooling/assignment_perf/perf.py arrivals $B every1.json --count 16 --every 1
# in a second env made the same way:
uv run python tooling/assignment_perf/perf.py arrivals $B every5.json --count 16 --every 5
uv run python tooling/assignment_perf/perf.py converge every1.json every5.json
```

Two envs' ledgers as they stand, without a run in either (for copies kept
from earlier runs):

```sh
MASCOPE_ENV=perfa uv run python tooling/assignment_perf/perf.py digest $B a.json
MASCOPE_ENV=perfb uv run python tooling/assignment_perf/perf.py digest $B b.json
uv run python tooling/assignment_perf/perf.py diff a.json b.json
```

A profile, with py-spy (not a dependency of the repository; `uv tool install
py-spy`). Run the venv's Python directly, so py-spy traces the process that
does the work:

```sh
py-spy record --rate 20 --nonblocking --threads --idle --format raw \
  --output foldin80.prof -- .venv/bin/python tooling/assignment_perf/perf.py foldin $B foldin80.json
uv run python tooling/assignment_perf/profile_report.py foldin80.prof
```

`--nonblocking` reads the process without stopping it, so the profiled run
keeps its timings. The report counts a frame as Mascope's when its file is
under the backend's or a library's `src/`; every other package, and the
standard library, is someone else's. cProfile is no substitute here: under asyncio it credits
time to the wrong callers, and it does not see the threads `to_thread` runs.

## Reading it

- A run's wall time depends on the box's load. Each JSON records the load
  average at its start and the CPU count; compare runs made under like load.
  Two runs made one after the other agree within about 5 % per step, and
  within 6 % on a stage (the note's section 2.1 has the pairs); two made
  side by side, each slowing the other, can differ by a third.
- `whole` at N = 80 takes about two hours on the agent box and `foldin`
  about an hour more. Runs in separate envs can go side by side.
- The demo's names never go into a PR or an issue: the tables name stages,
  steps and functions, and N.
