# mascope_viz

Visual digests of Mascope data: a package beside
[`mascope_sdk`](../sdk/README.md) that turns a scope of data (a dataset, a set
of batches, a time range) into charts, an interactive dashboard (HTML) and a
report (PDF), for the long time series the app does not draw. The design is
[`docs/dev/visual_digest.md`](../../docs/dev/visual_digest.md).

The package is young: it has its data layer, the scope, the campaign store
and the views, a command line over them, and its test backbone.

## Scope

A `Scope` names what a digest is about; `resolve` turns it into the batches,
files and collections it covers, through the SDK's listings.

```python
from mascope_sdk import MascopeClient
from mascope_viz.data import Scope, resolve

client = MascopeClient()
resolved = resolve(
    client, Scope(dataset="Spring campaign", start="2026-03-02", end="2026-03-05")
)
resolved.batches  # one row per batch, oldest first, with its sample count and span
resolved.files  # every file acquired in the range, in_batch says whether a batch holds it
resolved.key  # the same short hash for every spelling of the scope
```

A time without a zone is in the instrument's acquisition zone (`tz=` names
another), and a sample is in the range when its file started in it, the end
excluded. With a range, only the batches whose samples reach it have their
samples listed: the batch listing gives each batch's span, so a month of a
long dataset reads that month's batches, not the dataset's. A server older
than that span gives none, and every batch is read. Names match as literal text, a compiled `re.Pattern` as a regex;
of `batches`, a string names one batch and a pattern every batch it matches.
`mode=` takes an ionization mode's name, as `mascope.ionization.modes()`
lists it; with `instrument=`, only that instrument's modes and those of
every instrument are looked at.
A name that matches several objects is not an error: `resolved.ambiguous` is
true, `resolved.candidates` lists them, and nothing past the ambiguity was
read. One that matches nothing raises `mascope_viz.errors.NotFound`, which
lists the names there are.

## The campaign store

`sync` keeps a scope's data in a local parquet cache, so a long time series
is not rebuilt from per-sample calls each time. The first sync reads each
batch with one call per series kind; a later one reads again only the
batches that changed.

```python
from mascope_viz.data import Scope, sync

report = sync(client, Scope(dataset="Spring campaign", start="2026-03-01"))
store = report.store
store.table("ion_series")  # one row per (sample, target ion), keyed ion:<target_ion_id>
store.table("rollups")  # hourly and daily median, mean, p10, p90 and n per key
status = store.status()
status.batches  # each batch: when it was synced, its ledger run, failed or not
status.mixed_runs  # whether one polarity's formulas come from different engine runs
```

The tables are those of section 2.2 of the note, in UTC:

| Table | One row per |
|---|---|
| `samples` | sample, with `t_mid_utc`, the middle of its acquisition window, and its ionization mode's ID and name |
| `files` | file acquired in the range; `in_batch` says whether a batch holds it |
| `ion_series` | (sample, target ion) with a match record, keyed `ion:<target_ion_id>`; a record without a match is NaN |
| `peak_series` | (sample, batch peak) with a consensus formula, keyed `formula:<formula>\|<mechanism>`, with the ledger run that read it |
| `species` | series key: name, formula, ion formula, mechanism, class, m/z, element counts, O/C, H/C and OSc |
| `rollups` | UTC hour or day, series key and reading: median, mean, 10th and 90th percentiles and count |

A batch is fetched again when the server lists it modified since, its
sample count or its ledger's current run changed, a batch run (a rebuild,
a search, an import) started or ended on its ledger however it ended, its
ledger was edited under the same run (a batch peak curated by hand, a
sample folded again), its last fetch failed, or it holds a sample from the
last day: the batch holding today, still filling. An edit is seen by the
ledger's own modified time, which the runs listing gives; a server too old
to give it leaves such an edit unseen until the store is removed. Batches are read one at a time, `pause=`
seconds apart (half a second by default). A batch whose fetch fails is
skipped and recorded; the store keeps what an earlier sync gave for it,
`status()` marks it `failed` or `stale`, and the next sync tries it again.
A batch whose runs listing fails keeps its tables, and `status()` calls it
`unchecked` until a sync lists it again. A batch read while a batch run
was rewriting its ledger is marked `run_in_flight`, and read again once
the run ends. `status().complete` is False after a sync that was stopped.

A sync that fetches a batch lists the ionization modes once, to record each
sample's mode name beside its ID. A server that refuses the token on that
listing (one older than the token access to the modes) leaves the names
empty, and `coverage` then shows the IDs. They stay empty after the server
is upgraded, since that gives no batch a reason to be fetched again: a
batch's next fetch names its samples, and deleting the store names them
all at the next sync. A mode renamed on the server is not a reason to
fetch a batch again either: a batch keeps the name it was fetched with,
and `coverage` takes the name of the batch fetched last, whatever its
acquisition time. `mascope-viz view coverage` prints a mode's ID beside its
name when two modes share the name. A store written before the names were
kept is fetched afresh by its next sync.

An isotopologue batch peak repeats its parent's formula, so only the parent
carries the formula's series. A formula is read by the ledger run of each
batch, and runs of different engine versions or settings can read the
same peak as different formulas: `status().runs` lists the readings the
batches carry, `run_changes` where they change between batches of one
polarity (a formula is read in one polarity only; a polarity-switching
`+-` batch, as an analysis batch is by default, is followed in both), and
a rollup never mixes two readings in one bin.

A store lives under `$MASCOPE_VIZ_STORE`, else the user's cache directory
(`~/.cache/mascope_viz/stores` on Linux), one directory per server,
workspace and resolved scope key. It is a cache: `store.remove()` deletes it,
and the next sync rebuilds it. Run one sync at a time per store.

## Views

The views are the only place the package aggregates. Each is a pandas
function from a store's tables to a long frame, which a chart draws. A view
takes series keys as the store spells them (`ion:<target_ion_id>`,
`formula:<formula>|<mechanism>`), or `tic` for the samples' TIC.

```python
from mascope_viz.data import views

views.series(store, ["ion:...", "tic"])  # each key over time, at the auto cadence
views.series(store, keys, cadence="1h", normalise="ion:...")  # by a reagent ion
views.diurnal(store, keys, by="weekday")  # the hour-of-day cycle
views.periods(store, keys, "week")  # the distribution per week
views.coverage(store).gaps  # stretches without a sample, and the files in them
views.composition(store, "month", by="class")  # the formulas' budget by class
views.spectrum(store, (start, end), other=(start2, end2))  # a period against another
views.top(store, 10, "ratio", period="week")  # the biggest changers
```

| View | One row per |
|---|---|
| `series` | bin and key: `t`, `key`, `value`, `lo`, `hi`, `n` (values) and `n_samples` |
| `diurnal` | hour of day and key, and group with `by=` (month, season, weekday): the median and quartiles |
| `periods` | day, week or month and key: `n`, mean, min, p5, p25, median, p75, p95, max |
| `coverage` | hour, polarity and mode: files, unfiled files and samples (a polarity-switching file counts under both polarities), with the mode's ID and name; also the gaps and the totals |
| `composition` | period and formula, or class or carbon number with `by=`: median, mean, share, and the species' element counts, O/C, H/C and OSc |
| `spectrum` | formula, by m/z: the mean over a period, and against another period the difference and ratio |
| `top` | ranked key: by median, by the ratio of the last period to the first (up or down), or new in the last period |

What every view does the same way:

- **The auto cadence** is peaky's rule: the median spacing of the samples,
  rounded up to whole minutes. A bin is then never narrower than the
  spacing, so slightly irregular samples do not leave a comb of empty bins.
  Over several keys it is the widest of theirs. `cadence=` also takes `raw`
  (a row per sample) or a pandas offset (`10min`, `1h`, `1D`, `W`, `MS`).
  A step of whole hours that divides a day, such as `1h` or `6h`, starts
  its bins at the same local hours every day, so a bin across a clock
  change is an hour shorter or longer. Any other step, and the auto cadence
  always, keeps every bin the same length; `10min` bins still start on the
  clock's tens of minutes. `W` weeks run Monday to Sunday, as in `periods`.
  `ME`, `QE` and `YE` are calendar months, quarters and years.
- **Gaps stay gaps.** A bin without a sample is NaN, never interpolated. A
  value below detection, or an ion record without a match, is NaN, never 0.
  At `raw`, a NaN row breaks the line where a bin would be empty.
- **A sample's value of a key** is the sum of its values: two batch peaks
  can carry one formula, and the SDK notebooks sum per sample the same way.
- **Normalisation** is `none` by default, `tic`, or a series key such as a
  reagent ion. Each value is divided by its own sample's normaliser before
  any binning; a sample whose normaliser is missing or 0 is NaN.
- **Time** is UTC in the store. A view shows it in the instrument's
  acquisition zone, or the zone the scope named; `tz=` names another, and
  `tz="instrument"` asks for the instrument's. Bins, hours of day, days,
  weeks (Monday to Sunday) and months are that zone's, clock changes
  included. `start=` and `end=` keep the samples in a window.
- **Every frame says what it is** in its `attrs`: `view`, `tz`, `cadence`,
  `normalise` and `normalise_name`, `keys`, the window, the store's
  `provenance`, and `mixed_runs` and `run_changes`: whether the window's
  formulas, the keys' or the one normalised by, come from more than one
  engine run, and where the run changes. They are over the batches whose
  samples the view draws, in the polarity the formulas are read in: a
  change between the other polarity's batches is not flagged.
  A chart prints them; a run change is flagged, never joined silently. A
  `spectrum` against another period flags a change between the two, at
  the batch where the run changed.
- **Long series come from the rollups.** `series` at `cadence="1h"`, in
  UTC or a zone a whole number of hours from it, or at `1D` in UTC, bins
  from the store's hourly and daily rollups rather than every sample's
  values, and gives the same frame. It bins the rows instead, as fast as
  before, where the rollups would give another answer: with a
  normalisation, a `stat` other than median or mean, a `band` other than
  (10, 90) or None, a window whose first or last bin reaches outside it,
  a formula two batch peaks of one batch carry (`n_shared_keys`), two
  engine readings of a formula in one bin, or rollup days the last sync
  left pending. The TIC is binned from the samples. `attrs["source"]`
  says which drew the frame: `rollups` or `rows`.

## The command line

`mascope-viz` drives the data layer from a shell or a coding agent. Its
commands take a scope as options and print a summary, never a frame:

```sh
mascope-viz scopes --dataset "Spring campaign" --from 2026-03-01
mascope-viz store sync --dataset "Spring campaign" --from 2026-03-01
mascope-viz store status --dataset "Spring campaign" --from 2026-03-01
mascope-viz view series --dataset "Spring campaign" --from 2026-03-01 \
    --keys ion:... --keys tic --cadence 1h --csv series.csv
mascope-viz store rm --dataset "Spring campaign" --from 2026-03-01
```

| Command | What it prints |
|---|---|
| `scopes` | the scope's key, its batches, the files acquired in its range and how many no batch holds, the span, the collections, and whether its store is synced; the candidates when a name matches several objects |
| `store sync` | the batches read and why, those unchanged, failed and removed, and the store's path; `--pause` sets the seconds between two batches' reads (0.5) |
| `store status` | what the store holds, from the disk alone: when it was synced, whether it is complete and why not, each batch's state, the rows per table, the engine runs that read the formulas and where they change |
| `store rm` | the store deleted, and its size |
| `view NAME` | one of the seven views: its rows, span, zone, cadence and normalisation, and per key the number of values, their median and their maximum with its time; `--csv` writes the frame |

The scope options are those of `Scope`: `--dataset`, `--batches` (repeat it
for several), `--instrument`, `--from`, `--to`, `--polarity`, `--mode`,
`--collection` and `--tz`, which is the zone a time without one is in and
the zone times are shown in. The same scope finds the same store: give
`view` and `store status` the options the sync had.

`view` takes `--keys` (repeated; `series`, `diurnal` and `periods` need
them, the other views take every key of the store without), `--cadence`,
`--normalise`, `--stat`, `--by`, `--period`, `-n`, `--min-gap`,
`--window-from` and `--window-to` (the samples it reads; for `spectrum`,
the period), and `--other-from` and `--other-to` (the period `spectrum`
compares with). An option the view does not take is refused, not ignored.
`view top --by median` lists the store's brightest keys.

Every command prints one JSON object with `--json`, a failure included
(`error` names its kind, `message` says what to do). `scopes`,
`store status` and `view` take `--limit`, which caps the lists they print
(50; 0 for all); the batches a sync or the store's last sync failed on are
always listed in full. The output is ASCII: names are printed as JSON
strings and other text with its other characters escaped, so it prints the
same in any console, `PYTHONUTF8` or not. The exit status is 0 when the
command did what it was asked, 1 when it failed or a sync left a batch
behind, and 2 for a usage error, such as an unknown option or view, which
prints click's message on stderr and no JSON. An ambiguous name is not a
failure of `scopes`, which lists the candidates; the other commands do
nothing with it and exit 1. `store rm` fails when the delete leaves files
behind, as one another program holds open does.

The URL and the token come from the environment or a `.env` file, as the
SDK reads them (`MASCOPE_URL`, `MASCOPE_ACCESS_TOKEN`), and so does the
workspace, `MASCOPE_WORKSPACE`, when the token's user has more than one.
`--server` and `--workspace` override the URL and the workspace; the token
is read from those two places alone. Stores are kept per server, so
`--server` also selects the store directory. The SDK's own log lines go to
stderr at `WARNING` and above, unless `MASCOPE_SDK_LOG_LEVEL` says
otherwise (`INFO` shows a line or two per call).

## The synthetic campaign

`tests/fixtures/campaign.py` generates a month of one instrument at one
sample a minute, as the campaign store's tables (`samples`, `files`,
`ion_series`, `peak_series`, `species`; section 2.2 of the note), with the
answers it was built from beside them: the diurnal cycle of each species,
two episodes, a day acquired but filed into no batch, a reagent-ion drift, a
step in the TIC and an assignment run that reads three formulas differently
from mid-month on. Two parameter sets give the two chemistries, a
halide-negative one with a reagent ion and a positive one without.

```python
from fixtures.campaign import HALIDE_NEGATIVE, generate

campaign = generate(HALIDE_NEGATIVE, seed=0)
campaign.tables["peak_series"]  # one row per (sample, batch peak)
campaign.truth.episodes  # what a test asserts against
```

The tests get it through the `campaign` fixture (both chemistries) or
`halide_campaign` / `positive_campaign`. The same seed gives the same parquet
bytes on every platform: the generator draws uniforms only and computes with
`+`, `-`, `*`, `/` and `sqrt`, which IEEE 754 rounds the same everywhere.

```sh
uv run pytest libraries/viz/tests/ -v
```

`tests/test_viz_scope_contract.py` runs `resolve` on a demo stack,
`tests/test_viz_store_contract.py` syncs the demo dataset there,
`tests/test_viz_views_contract.py` checks that normalising by a reagent ion
gives notebook 06's numbers on it, and `tests/test_viz_cli_contract.py`
runs the command line against it, with the SDK contract tests' switch and
settings (`MASCOPE_SDK_CONTRACT=1`); all four are skipped without them.
