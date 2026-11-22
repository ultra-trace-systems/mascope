# mascope_viz

Visual digests of Mascope data: a package beside
[`mascope_sdk`](../sdk/README.md) that turns a scope of data (a dataset, a set
of batches, a time range) into charts, an interactive dashboard (HTML) and a
report (PDF), for the long time series the app does not draw. The design is
[`docs/dev/visual_digest.md`](../../docs/dev/visual_digest.md).

The package is young: it has its data layer, the scope, the campaign store
and the views, a command line over them, the Figure spec charts are drawn
from and its two renderers, and its test backbone.

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
views.diurnal(store, keys, normalise=["ion:...", "ion:..."])  # by the reagent's sum
views.series(store, [("ion:...", "ion:...")])  # the reagent's total as one series
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
- **Normalisation** is `none` by default, `tic`, a series key such as a
  reagent ion, or a list of series keys, divided by their sum. Each value
  is divided by its own sample's normaliser before any binning; a sample
  whose normaliser is missing or 0 is NaN.
- **Normalise by the sum when the reagent is spread over clusters.** In
  iodide and bromide chemistry the reagent is the bare ion and its water
  cluster; in nitrate chemistry the monomer, dimer and trimer. Their
  shares move with humidity and temperature while their total does not,
  so dividing by one of them leaves that one's share in every species: a
  diurnal cycle that is the instrument's, not the air's. Name every ion
  the reagent is spread over: `normalise=["ion:<bare>", "ion:<cluster>"]`.
  A sample's sum is that of the ions with a value in it, so where one is
  below detection the others divide; `attrs["normalise_complete"]` is the
  share of the samples in which every ion had a value. A list of one key
  is that key. A key twice, a key the store does not hold, or ions read in
  two polarities are refused: a normalisation never spans chemistries.
- **A sum is a series too.** A tuple among the keys, such as
  `[("ion:<bare>", "ion:<cluster>")]`, is the per-sample sum of its keys,
  by the same rule, keyed by them joined with `+` and named by their names
  joined with ` + `. The reagent's total is then drawn and binned like any
  other key. A sum has no m/z, so `spectrum` does not take one.
- **Time** is UTC in the store. A view shows it in the instrument's
  acquisition zone, or the zone the scope named; `tz=` names another, and
  `tz="instrument"` asks for the instrument's. Bins, hours of day, days,
  weeks (Monday to Sunday) and months are that zone's, clock changes
  included. `start=` and `end=` keep the samples in a window.
- **Every frame says what it is** in its `attrs`: `view`, `tz`, `cadence`,
  `normalise` and `normalise_name` (a list of keys and their names joined
  with ` + ` for a sum), `normalise_members` (the keys divided by: none for
  `none` and `tic`) and `normalise_complete`, `keys`, the window, the
  store's `provenance`, and `mixed_runs` and `run_changes`: whether the
  window's formulas, the keys', a sum's or the ones normalised by, come
  from more than one engine run, and where the run changes. They are over the batches whose
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
  normalisation or a sum among the keys, a `stat` other than median or mean, a `band` other than
  (10, 90) or None, a window whose first or last bin reaches outside it,
  a formula two batch peaks of one batch carry (`n_shared_keys`), two
  engine readings of a formula in one bin, or rollup days the last sync
  left pending. The TIC is binned from the samples. `attrs["source"]`
  says which drew the frame: `rollups` or `rows`.

## Charts: the Figure spec and its renderers

A chart is a `Figure`: what every renderer draws, and the numbers behind it.
Its panels draw columns of one frame, each panel with one of eight
primitives. The renderers draw the spec, never the code that made it, so a
figure reads the same as a PNG in a report and as an interactive plot in an
HTML dashboard.

```python
import pandas as pd
from mascope_viz.charts import Context, Figure, Panel
from mascope_viz.data import views
from mascope_viz import render

frame = views.series(store, ["ion:...", "tic"], cadence="1h")
# What a legend calls each series: its species' name, else its key
species = store.table("species").dropna(subset=["name"])
names = dict(zip(species["series_key"], species["name"]), tic="TIC")
frame["name"] = [names.get(key, key) for key in frame["key"]]
fig = Figure(
    "species_series",
    "Species over time",
    context=Context.from_attrs(frame.attrs),  # the subtitle is written from it
    panels=[
        Panel(
            "series",
            axes={"x": {"label": f"Time ({frame.attrs['tz']})"}, "y": {"scale": "log"}},
            encodings={
                "x": "t",
                "y": "value",
                "group": "key",  # a series' identity: its colour, its legend entry
                "name": "name",  # what its legend entry and hover text say
                "lo": "lo",
                "hi": "hi",
            },
            options={
                "spans": [
                    {
                        "start": "2026-07-01 06:00",
                        "end": "2026-07-01 10:00",
                        "label": "Episode 1",
                        "kind": "event",
                    }
                ]
            },
        )
    ],
    summary={"bins": len(frame)},
    data=frame,
)
render.save(fig, "species.png")  # matplotlib
render.save(fig, "species.html", theme="dark")  # plotly, one self-contained file
render.matplotlib(fig)  # a matplotlib.figure.Figure
render.plotly(fig)  # a plotly.graph_objects.Figure
```

| Primitive | Draws | Encodings (needed; optional) | Options |
|---|---|---|---|
| `series` | lines over time or a number, a band, gaps as gaps | `x`, `y` (one column or a list); `group`, `name`, `lo`, `hi` | `mode`, `markers`, `colours`, `names`, `spans`, `rules` |
| `strip` | categorical rows by time, a cell where a row has a value | `x` (time), `y`; `value`, `mark` | `width`, `mark_label`, `spans`, `rules` |
| `heatmap` | an x by y grid of cells coloured by value | `x`, `y`, `value`; `gap` | `mode`, `palette`, `centre`, `gap_label` |
| `scatter` | points, area by size, colour by column, the top N labelled | `x`, `y`; `size`, `colour`, `name`, `label`, `hollow` | `top_n`, `palette`, `colours`, `hollow_label` |
| `sticks` | a spectrum: lines from zero, signed, the top N labelled | `x`, `y`; `group`, `name`, `label`, `mark` | `top_n`, `colours`, `by_sign`, `mark_label` |
| `bars` | bars per category, side by side or stacked, an interval | `x`, `y`; `group`, `name`, `lo`, `hi` | `stack`, `colours` |
| `box` | a box per category from its quartiles | `x`, `q1`, `median`, `q3`, `lo`, `hi`; `group`, `name`, `mean`, `hollow` | `colours`, `hollow_label` |
| `table` | rows of the frame, formatted | `columns` (a list); `mark` | `headers`, `formats`, `max_rows`, `mark_label` |

`mascope_viz.charts.PRIMITIVES` holds the same, with the column types each
encoding takes and each option's default. What every figure shares:

- **The context** is what the view behind a figure says of its values,
  as fields of `Figure.context`: `cadence`, `stat`, `band`, `tz`,
  `start`, `end`, `normalise` (`none`, `tic`, a key, or a list of keys
  whose sum divides), `normalise_name`, `normalise_members`,
  `normalise_complete`, `mixed_runs` and `run_changes`.
  `Context.from_attrs(frame.attrs)` takes them from a view frame. Whatever
  reads a figure later reads these fields, never the subtitle's text.
- **The subtitle** is written from the context when a figure is given
  none: the cadence with its statistic and band, the normalisation by
  name, the window and the time zone (`1h medians, p10-p90 band;
  normalised to I- + IH2O-; from 2026-07-01 00:00 to 2026-07-03 00:00;
  times in Europe/Helsinki`). `subtitle(context)` writes the same. Both
  renderers wrap it, matplotlib beside its legend, and neither cuts it
  off. A time axis' label names the zone too.
- **Display names.** A `group` column is a series' identity: its colour
  and its one legend entry. A `name` column gives what its legend entry
  and hover text say, so a key such as `ion:12+ion:14` is never shown for
  a name; `names` does the same for the lines of a list of `y`.
- **Gaps stay gaps.** A NaN breaks a line on both renderers, a value alone
  between two gaps is drawn as a point, and a value that is not positive
  is a gap on a log axis. A heatmap cell whose `gap` is true (no samples)
  is drawn in the gap style, a grey tint held apart from both ramps; a
  cell with samples and no value is blank.
- **A heatmap's cells meet halfway** between two values, in log space on
  a log axis, where a row or column at a value that is not positive is
  not drawn. A lone cell is one unit wide, a day on a time axis and as
  wide as its value on a log axis. Both renderers place the cells alike.
- **A group keeps its colour** in every panel of a figure, and appears
  once in its legend. Groups take the categorical palette in the order
  they first appear; `colours` names a group's colour by token instead,
  the diverging ramp's ends (`diverging.low`, `diverging.high`) among
  them. A categorical column's values are drawn in the order of its
  categories, any other column's in the order they first appear.
- **Marks.** `mark` flags a strip's cells, a stick at its tip (such as a
  peak seen in one period only) and a table's rows (shaded, and a note
  says what the shading means), each named by `mark_label`. `hollow`
  draws a scatter's points as rings and a box without its fill (too few
  samples), named by `hollow_label`. `by_sign` colours a stick above zero
  in the diverging ramp's high end and one below in its low end.
- **Sizes.** A scatter point's area is in proportion to its `size`, the
  largest at the theme's largest marker; a value too small to see is drawn
  at the smallest. The larger points are drawn behind the smaller and the
  points are translucent, and a size legend titled by the `size` axis'
  label gives two or three round values.
- **Intervals.** A bar's interval runs from `lo` to `hi` whether or not it
  holds the bar's top, the same on both renderers.
- **`spans`** shade stretches of a time panel (`kind` `event` or `gap`),
  and **`rules`** mark a moment, such as a change of engine run, each with
  a label. A time without a zone is in the x column's zone.
- **Axes.** Each plotted panel has its own scale, range and label on each
  axis. A bars or box panel's x and a strip's rows are categories whatever
  their column holds, as are a text or a boolean column; they take neither
  a range nor a log scale. The y of a list of `y` and a box's y draw
  numbers. `mascope_viz.charts.spec.axis_kind` says which, for the spec's
  check and the renderers alike.
- **`share_x`** joins the x axis of every plotted panel, zoom included,
  on both renderers. They must all draw times or all numbers.
- **`where`** keeps the rows of the figure's frame a panel draws, so small
  multiples share one frame: `Panel(..., where={"key": "ion:..."})`.
- **`mode="raster"`** on `series` and `heatmap` is reserved for the
  datashader path of the `[big]` extra, which does not exist yet: the
  panel is drawn as marks, and a note under the figure says so.
- **Notes** print under the figure, with a line for a table that shows
  only its first `max_rows` rows.
- **Names are data.** A sample or a species name in a title, a legend or a
  table is drawn as written: plotly gets it HTML-escaped and matplotlib
  with its `$` escaped, so neither reads it as markup or mathtext.

**The spec is a document.** `fig.to_json()` writes a JSON document of
version `figure_spec_version` (1): the panels, each with its primitive,
axes, encodings and options; the context; the summary, as plain JSON
(NaN as null, a time as ISO 8601 with its offset, a NumPy `datetime64`,
which names no zone, in UTC); the notes; and the
frame column by column, each column with its type (`time` with its zone,
`number`, `integer`, `boolean`, `text` or `category` with its categories in
order). `Figure.from_json()` reads it back to an equal figure (two figures
are equal when their documents are), which renders the same, and refuses a
document of a later version or with a field it does not know: a primitive
has no escape hatch to one renderer's settings. A panel
names colours, palettes and span kinds by token, and the theme gives them
values, so the web app can draw the same document with its own tokens.

**Time.** matplotlib places each value at its instant and labels the axis
in the column's zone. plotly.js has no zones, so the plotly renderer draws
the wall-clock time in the column's zone: the same labels, except around a
clock change, where plotly shows the repeated hour twice at one place.

**The theme** (`mascope_viz.render.theme`) has a light and a dark variant
of the web app's look: its grounds, IBM Plex Sans (DejaVu Sans where it is
not installed), and its five-series palette first, then five more hues.
The sequential ramp is magma, light to dark on the light ground and dark to
light on the dark one; the diverging ramp runs from blue through grey to
the accent. `theme.check("light")` is the contrast check both themes pass:
every categorical colour at 3:1 or more on every ground (WCAG 2.2, 1.4.11),
any two of them apart, the ramps ordered and apart from the ground, and a
heatmap's gap cell apart from its ground and from every colour along both
ramps. The gap cell's tint is heavier in the light theme: there a light
tint would match the diverging ramp's grey centre.
`theme.get("dark").tokens()` lists every token's colour.

**Saving.** `render.save(fig, path, theme=..., plotlyjs="inline")` writes
by the extension: `.png`, `.svg` and `.pdf` with matplotlib, `.html` with
plotly (`plotlyjs="cdn"` for a small file that loads plotly.js from its
CDN), `.json` the document. A PNG and an HTML file are the same bytes on
every run; an SVG and a PDF are once `SOURCE_DATE_EPOCH` pins the date
they record.

**The chart kinds** register in `mascope_viz.charts.registry`: a
`ChartKind` says what it answers, its tier, the series it draws, its
parameters (`Param`: name, type, default, choices, help) and its summary's
fields, and holds the function that builds its figures. `register` adds a
kind, `get` finds one by id, and `all` lists them, for the command line's
`list` and `describe`. The registry is empty until the chart kinds land.

**The import boundary.** `mascope_viz.data` and `mascope_viz.charts`
import pandas, NumPy and pyarrow, never matplotlib or plotly, so a server
can build and send specs without the renderers (the note's section 2.8).
`mascope_viz.render` imports each renderer only when it draws.

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
`--normalise` (repeated, the sum of the keys it names), `--stat`, `--by`,
`--period`, `-n`, `--min-gap`,
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
