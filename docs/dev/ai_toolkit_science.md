`ULTRA TRACE MASCOPE - DESIGN DOC - AI TOOLKIT, THE SCIENCE`

# The agent toolkit for atmospheric chemistry: what a scientist asks, and the tools that answer

- **Status:** design
- **As of:** 2026-11-05
- **Tracked in:** [AI analyst shell](https://linear.app/ultra-trace/project/ai-analyst-shell-2b0a6a7e8af8)
  (Linear, P-MAS-21: the tools of section 3 are milestone 1, "A surface
  small models can drive", beside MAS-718; the recipes they compose into
  are milestone 2; the briefs of section 8 are proposed, not filed)
- **What:** The next tools of `mascope_ai` planned from an atmospheric
  chemist's questions of CIMS and Orbitrap data: which the toolkit answers
  today, which only through pandas the model writes, which not at all, and
  the coarse tools, bench questions and skills that close the gap.

## Purpose

The toolkit (`libraries/ai`, package `mascope_ai`) finds, describes and loads
Mascope's data as Frames, has Mascope score candidate formulas, and writes
only through a plan. Its bench, the golden fixture of 22 questions in four
families, proves the plumbing: the right batch is found, a time series'
maximum is read, a verdict is Mascope's and not the model's, a trap is
declined. None of the 22 is a question an atmospheric chemist brings to a
campaign. A scientist with a week of bromide CIMS or a chamber run on an
Orbitrap asks how oxidised the organic mass is, which homologous series are
present, whether the reagent ion was stable, what rose in the plume and what
is still unexplained. Today those answers are either pandas the model
writes itself over `load_peaks` or, where the answer rests on the
engine's formulas or the source's chemistry, not available through the
toolkit at all (section 2), and the first row of the model matrix
([`ai_analyst_shell.md`](ai_analyst_shell.md) section 3.4 is the method;
the numbers are in the body of
[#2983](https://github.com/ultra-trace-systems/mascope/pull/2983)) shows
what that costs: the open-weight finalist answered the metadata questions
eight of eight and the analysis questions none of six, until a single
aggregate tool (`reduce`, MAS-718) turned one of them from missing to
correct (A1) and one from wrong to correct (A4), with the top-ions question
(A6) still missing.

This note writes the scientist's questions down, sorts them into families the
way the fixture does, and plans the tools that answer each family in one
call, with the bench questions that prove them and the skills that teach
them. It follows the milestone-1 doctrine of the shell note (section 3.2):
coarse tools that do one unit of work a scientist would name, closed output
spaces, every number computed in code, and the escape hatch kept: Python on
the session's Frames, with `derive` recording what it computed.
Where a computation already exists in peaky or in an SDK notebook, the
boundary rule of [`peaky_mascope_boundary.md`](peaky_mascope_boundary.md)
decides where it lands: a rule a result depends on is Mascope's and goes to
`mascope_tools`; a composition of steps is a recipe and stays Peaky's.

Three positions of the owner's hold throughout. An m/z offset is a
calibration fault, so a tool that reads mass error says so and never tunes
around it. Peaky's output is a reference to read, never a target to tune
toward, and literature that names an ion is a citation, not ground truth.
Only the public demo dataset, the orange-peel uronium and bromide batches of
2025-08-11 and the 2026 acquisitions beside them, appears in a fixture.

## 1. What exists

### 1.1 The tool surface

Seventeen MCP tools (`mcp.py` `TOOLS`, `reduce` among them since MAS-718
merged on 2026-11-05), and two more `Toolkit` methods a model reaches only
from Python: `resolve` and `derive`. What each returns, and what a model
must still compute in Python after calling it:

| Tool | Returns | What the model still computes itself |
|---|---|---|
| `find`, `describe` (MCP); `resolve` (Python only) | refs; a small dict: a batch's sample count, span, ionization modes with their mechanisms, collections; a sample's mode, mechanisms, m/z range, TIC, calibration status, match | nothing: the metadata family is answered |
| `load_peaks` | a Frame: every sample's averaged peak list with heights, areas, the matched isotope, ion and compound and their scores; one point per sample is the batch time series | every aggregate: the per-sample series of a species, its maximum, a normalised ratio, a background, a TIC, a top-N table (A2, A3 and A6 are pandas; A1's maximum is `reduce`'s since #2983) |
| `load_timeseries` | a Frame: the per-scan series of matched targets within samples | what happens inside a sample: a stage mean, a rise time |
| `load_spectrum` | a Frame: `mz`, `intensity` of one sample's averaged spectrum, or a window | the apex is `reduce`'s `argmax` since #2983 (A4); anything else about the profile is pandas |
| `load_peaks_by_stage` | a Frame: a sample's averaged peaks per time range | the comparison between stages |
| `compute_timeseries` | a dict: peaks in range, how many had a series, how many were computed | nothing; it makes a stage whole |
| `query_by_mz` | `Candidates`: Mascope's formulas for an m/z | nothing; the model chooses which to score |
| `match_compounds` | `MatchResult`: a verdict per candidate, the tree as a Frame | nothing; the model narrates |
| `reduce` | a dict: one statistic of one column, over the Frame or by group, filtered, top N | nothing for a median, a maximum or its row; everything that is two statistics combined (a ratio, a difference, a fraction) |
| `derive` (Python only) | a Frame kept with its sources | everything, by construction: it is how Python's result re-enters the manifest |
| `answer` | the manifest record | nothing |
| `plan_organize`, `plan_collection`, `plan_associate`, `plan_match`, `apply` | a plan; a receipt | nothing |
| `session` | the Python to run and the lines that open the session | the whole analysis, in that Python: over MCP this is the escape hatch, and `derive` is reachable only inside it |

[#2991](https://github.com/ultra-trace-systems/mascope/pull/2991), open
as of 2026-11-05, has every Frame handle say what its columns mean
(`mascope_ai.columns`, `Frame.column_meanings()`, and a user page "Tables
and columns" under `docs/user/concepts/`): a peak list's `height` is the
averaged peak's apex, a time series' one scan's value, a stage's its mean
over its own scans. Once it merges, the tools below return small dicts
whose keys are named the same way, and a derived Frame's columns carry a
meaning as the loaders' do; 8.1's `columns.py` line waits for it.

Two things are not on the surface at all, and the gaps below trace back to
them. The toolkit has no loader for the **assignment ledger**: the SDK's
`load_batch_ledger` (one row per member by default, the batch peak's
consensus formula and tier beside the member's own reading; the species
table, one row per batch peak, on `attrs["batch_peaks"]`) and
`load_assignments` (one row per observed peak of a sample's latest run,
the unassigned ones included, each with its tier, source and role) are
what every composition question rests on, since the
peaks Frame carries a formula only for the peaks a target collection
matched. And the toolkit knows nothing of the **reagent chemistry** Mascope
already has: `mascope_tools.composition.profiles` names the reagent profile a
sample's mechanisms imply, and `composition.reagents` names the ions a source
makes of itself, with the works that name them (the user page
[peak-assignment.md](../user/how-it-works/peak-assignment.md), "The ions a
source makes of itself"). Neither reaches a model.

### 1.2 The bench

[`libraries/ai/tests/ai_golden.py`](../../libraries/ai/tests/ai_golden.py)
holds 22 questions: eight metadata (M1 to M8), six analysis (A1 to A6),
three interpretation (I1 to I3), five decline (D1 to D5). Each has a solver
over the tool surface, a known answer from the demo bundle's goldens with the
bundle's tolerances, and for the traps a check that no value was given. A
model run's verdicts are correct, wrong (stated but false), missing
(abstained), sloppy (an instruction not followed), with infrastructure
failures apart ([`golden_transcripts/README.md`](../../libraries/ai/tests/golden_transcripts/README.md)).
[`ai_safety.py`](../../libraries/ai/tests/ai_safety.py) runs the plan/apply
safety scenarios as contract tests of the toolkit, not of a model.

What the analysis family measures today is the plumbing of one species at a
time: the maximum of a series (A1), a ratio of two lines (A2), a background
from the first five samples (A3), a spectrum's apex (A4), a stage read (A5),
a top-N table with mass error (A6). The reference computations are
`groupby` over the peaks Frame. A2 and A3 are the two atmospheric-chemistry
operations the set has, normalisation to a reagent ion and background
subtraction, and both are the model's pandas.

### 1.3 The analyses scientists already do by hand

Every SDK example notebook and every peaky stage is an analysis someone
already does. Where each stands against the surface:

| Analysis | Where it exists | Through the toolkit today |
|---|---|---|
| A species' series across a batch, at isotope, ion or compound level, resampled to a cadence | notebook 02; `mascope_viz` `series` view | `load_peaks` plus pandas |
| A species' series within a sample | notebook 03 | `load_timeseries` plus pandas |
| Mass defect plot | notebook 04; peaky `qc_figure`, `gka_figure` | pandas; no chart tool is served yet |
| Stages within a sample | notebook 05 | `load_peaks_by_stage` plus pandas |
| TIC normalisation; reagent-ion normalisation from a paired RI batch | notebook 06; peaky `timeseries.reagent_total`, `profiles.normaliser` | pandas; the model must know which ion is the reagent |
| Background subtraction, by matched ion or by m/z bin | notebook 07; peaky `timeseries` disposition | pandas (A3) |
| Correlation of peaks across samples, reference-peak and global clustering | notebook 08; peaky `cluster.py` | pandas plus scipy; nothing on the surface |
| Stages across a batch, stage means, TIC-normalised, fold change against a reference stage | notebook 09 | pandas |
| Reading an assignment run: tiers, sources, mass defect by tier, Van Krevelen | notebook 10; peaky `analyte_viz`, `composition.py` | not possible: no ledger loader |
| Multi-pass assignment, series expansion, residual explanation | peaky passes 0 to 7 | Mascope's engine, read through the ledger; not a toolkit concern |
| Homologous series by Kendrick and GKA; the rotating GKA | peaky `series_gka.py`, `series_detect.py`, `gka_figure.py` | nothing |
| Composition by backbone class, signal-weighted; HOM and oligomer flags | peaky `composition.py`, `cluster.formula_class` | nothing |
| Diel (hour-of-day) profiles by class | peaky `scripts/diel_classes.py`; `mascope_viz` `diurnal` view | pandas |
| Big standalone changers; flatness gate | peaky `cluster.big_changers`, `split_varying` | pandas |
| The batch report: findings, coverage, composition, GKA, families, changers, clusters | peaky `pdf_report.py` | a recipe (milestone 2), not a tool |

### 1.4 Computation that exists in Mascope's own packages

The rules the tools below need are partly in the tree already, and the plan
reuses rather than rewrites:

- `mascope_tools.composition.utils`: `parse_composition`, `composition_mass`,
  `to_hill_notation`, `combine_formula_and_ionization`, `parse_ionization`
  (a mechanism string in either notation); `composition.finder.get_unsaturation(atoms,
  counts)` (DBE, not exported from the package). The element counts behind
  every ratio.
- `mascope_tools.composition.profiles`: the reagent profiles (bromide,
  uronium, nitrate, 15N-nitrate, iodide, charge transfer, ESI) with their
  polarity, grid and mechanisms; `detect_reagent_profile` from a sample's
  mechanisms; the chemistry contexts with their ratio windows.
- `mascope_tools.composition.reagents`: the reagent-cluster library and the
  per-sample fingerprint that says which channels a source is running.
- `mascope_tools.composition.mass_accuracy`: `fit_sample_mass_accuracy`,
  `mass_accuracy_anchors`, `scoring_sigma_ppm`. (`composition.calibration.calibration_error`
  is the scorer's expected calibration error of predicted probabilities, not
  a mass error, and no tool here reaches for it.)
- `mascope_tools.stats`: `stats/peak.py` today; the covariation functions of
  3.5 are a new module beside it, not a new package.
- `mascope_viz.data`: the campaign store and its views, `series(keys,
  cadence, normalise, stat)`, `diurnal`, `periods`, `coverage`,
  `composition`, `top`; its species table already carries O/C, H/C and OSc
  per series key ([`visual_digest.md`](visual_digest.md) section 2.2) and a
  class from `store.compound_class` (CHO, CHON, CHOS, CHONS, CH, an `X`
  suffix for any other element, `inorganic` without carbon). Series keys are
  `ion:<target_ion_id>`, `formula:<formula>|<mechanism>` and `tic`. The
  views are the aggregation layer the tools below should call, so the
  toolkit, the digests and the app's future renderer agree on every number.

## 2. What an atmospheric chemist asks

The questions below are the ones brought to ambient and chamber data from a
CIMS (bromide, nitrate, iodide, uronium) or an Orbitrap or TOF with a
charge-transfer or electrospray source. They are grouped into families the
way the fixture is, each family one kind of reference computation. For each
question: **tool** means the surface answers it in a call or two with no
pandas; **pandas** means it is answerable only if the model writes the code
over a Frame; **cannot** means no Frame carries what the question needs.

### 2.1 Time series and events (family T)

| Question | Today |
|---|---|
| How did this species (or these) behave over the batch, the day, the campaign? Its maximum and when, its median, its spread | `load_timeseries` plus `reduce` for a maximum and its row (A1), pandas for the rest; nothing gives a series summary |
| What is the diurnal cycle of this species or class: the hour it peaks, its day to night ratio | pandas over several batches; nothing names a day's batches as one scope |
| When did a plume or an event happen: when did the species exceed its baseline, by how much, for how long | pandas; no baseline method is anywhere on the surface |
| What rose in the event and what did not: the species whose event-to-background fold is largest | pandas over the whole peaks Frame |
| Did this species change across stages of the run: background, exposure, recovery (a chamber experiment) | pandas (notebook 09) |
| What is the TIC over the batch, and did the signal drift | `describe` gives a sample's TIC one at a time; the series is pandas |

### 2.2 Normalisation and background (family N)

| Question | Today |
|---|---|
| This species normalised to the reagent ion: which ion is the reagent here, is it inside the acquired range, how stable was it | pandas (A2) once the model knows the reagent ion's formula; the toolkit does not say which it is, nor that protonated urea at m/z 61 is below a 122-750 scan window |
| This species normalised to TIC | pandas (notebook 06) |
| The background from a blank sample, from the first N samples, from a low percentile; the signal above it | pandas (A3, notebook 07) |
| Which peaks are inlet or background: flat over the run, present in the blank | pandas; the flatness rule is peaky's |
| Normalise to the reagent ion of a paired RI batch when the analyte batch excludes the reagent | pandas, with sample pairing by position (notebook 06) |

### 2.3 Composition and oxidation state (family C)

| Question | Today |
|---|---|
| What are the O:C, H:C and OSc of the assigned formulas: their distribution, their intensity-weighted means | cannot: the peaks Frame carries formulas only for target-matched peaks; the ledger's formulas have no loader |
| How much of the signal is CHO, CHON, CHOS, CHONS; by count and by intensity | cannot, same reason |
| Which peaks are HOMs (highly oxygenated organic molecules), and how much signal do they carry | cannot; and the HOM rule (an oxygen count at or above a threshold) is written nowhere in Mascope |
| Are there dimers or oligomers: high-carbon, high-oxygen formulas at twice a monomer's mass | cannot |
| The Van Krevelen of this batch, this period, this cluster | cannot; and no chart tool is served |
| What is this formula: its DBE, its ratios, its class, is it plausible in this context | pandas with the model's own arithmetic, which it must not do; `query_by_mz` gives mass and error, not ratios |

### 2.4 Series and Kendrick analysis (family S)

| Question | Today |
|---|---|
| Which homologous series are present: CH2, O, CO2, H2O, C2H4O ladders; their anchors, members and lengths | cannot for the ledger's formulas; pandas with the model's own Kendrick arithmetic for m/z alone, which it must not do |
| The Kendrick mass defect of these peaks under this repeat unit (the GKA, rotated to flatten a family) | pandas; the arithmetic is peaky's `series_gka.py` and nowhere in Mascope |
| Is this contaminant ladder present: siloxane (C2H6OSi), fluorinated (CF2) | cannot without the ledger |

### 2.5 Reagent ions, the source and the instrument (family R)

| Question | Today |
|---|---|
| Which reagent chemistry was this: bromide, uronium, nitrate; which mechanisms | `describe`: the mode and its mechanisms (M8); the profile name itself is in `mascope_tools` and not served |
| Which ions did the source make of itself, which of them are in the acquired range, and how did they behave | cannot: the reagent library is in `mascope_tools.composition.reagents` and not served; a model that names Br3- from memory is guessing |
| Was the ion source stable: the reagent ion's and the TIC's spread over the batch, their drift | pandas once the reagent ion is known |
| Is the mass axis right: the ppm error of calibrants or of assigned ions per sample, its median and drift; are any samples uncalibrated | `describe` gives a sample's calibration status one at a time; the mass error is pandas over `query_by_mz` per ion (A6) |
| Which adduct channels is the source running (ammonium, sodium, carbonate) | cannot: the fingerprint is in `composition.reagents` and not served |
| Did the detector saturate; what is the instrument's dynamic range | cannot, by design (D5) |

### 2.6 Covariation and attribution (family V)

| Question | Today |
|---|---|
| Which species covary with this one across the batch | pandas plus scipy (notebook 08) |
| Which families of covarying species are there; which are flat | pandas plus scipy; the method (log-space correlation, complete linkage at r above 0.6, the flatness gate) is peaky's |
| What is the source of this family | inference: the model's own, to be labelled as such; no tool can answer it and none should |

### 2.7 The unassigned (family U)

| Question | Today |
|---|---|
| How much of the signal is assigned, candidate, below assignability, unassigned; by count and by intensity | cannot: no ledger loader |
| What are the brightest unassigned peaks: their m/z, presence, median intensity | cannot, same |
| What could this unassigned peak be | `query_by_mz` then `match_compounds` (I1, I2): tool |
| Is this peak an isotope line, a reagent cluster or an artifact of a bright neighbour | cannot: the ledger's roles (M0, isotope child, reagent, artifact) have no loader |

### 2.8 Comparison across batches, sites and instruments (family X)

| Question | Today |
|---|---|
| Is this species higher today than yesterday; in this batch than that | pandas over two loads |
| Do two instruments or two chemistries agree on a species | pandas; and the rule that a bromide and a uronium reading of one formula are not the same molecule (ionization-selective detection) is written only in peaky's gotchas |
| Which species are new in this period, which disappeared | pandas |

### 2.9 Decline (family D, per family above)

Every family has a question whose right answer is "not available", and the
bench needs one of each before a tool is admitted: a diurnal cycle asked of a
batch that spans two hours; a reagent ion outside the acquired range; an O:C
asked of a peak with no formula; a CF2 ladder that is not there; a source
attribution asked as a fact; a comparison across polarities read as a
concentration ratio. A value given to one is wrong; a decline that does not
name what is missing is sloppy.

### 2.10 The shape of the gap

Of the forty-odd questions above, the surface answers the metadata and the
interpretation ones. Every time-series, normalisation, covariation and
comparison question is pandas the model writes over `load_peaks`, which the
matrix shows a cheap model cannot sustain over a 26-message session. Every
composition, series, source and unassigned question is not answerable at all,
for two reasons that two loaders and one small bridge to `mascope_tools`
remove: the ledger is not loaded, and the reagent chemistry is not served.
The rest of this note is those tools.

## 3. The tools to add

The doctrine, restated as the acceptance criterion of every tool below: **one
unit of work a scientist would name, inputs an enumeration or a name, a
result that is a small dict or a Frame handle, every number computed in
code, nothing a model could not also get through `derive` and pandas.** A
tool that needs a rule (a class vocabulary, a threshold, an arithmetic) takes
it from `mascope_tools`, where the engine and the digests take the same one;
a tool never carries a rule of its own. A tool that is a sequence of other
tools with parameters is a recipe and is not a tool: it belongs to milestone
2 and to Peaky's data files, run by `run_recipe`.

**Species and normalisers are named one way everywhere, and the key says
what a point is.** A tool takes a species as `isotope:<isotope formula>`,
`ion:<ion formula>`, `compound:<formula>` or `formula:<formula>|<mechanism>`
(the ledger's), and a normaliser as `none`, `tic` or one of those keys.
There is no `level=` beside a key: the key names its level. One point of a
series is one sample's value: for an `isotope:` key the height of that
line's peak; for an `ion:` key the match's summed intensity as Mascope
stores it, the number the app's batch chart draws; for a `compound:` key
the sum of its ions' points; for a `formula:` key the ledger member's
intensity. **An `ion:` point is read, not re-summed.** `load_peaks` carries
a line's match only when its category is above 0 after the match gates, so
a line on its mass that misses its isotope ratio shows as unmatched there,
while the stored ion sum keeps that line's intensity (only the mass gate
takes the intensity), which is what `batches.ion_series` serves and what
the store's `ion:<target_ion_id>` holds. A re-sum of `load_peaks`'s matched
lines would read an ion with two near-equal lines at about half the chart's
value in a file where one line misses the ratio, the toggling #2943 fixed
in the chart. So over one batch an `ion:` point comes from
`batches.ion_series` (one request per batch) and a `compound:` point is the
sum of its ions' points; only an `isotope:` point is read per line off
`load_peaks`. **A peak counts once per sample** where lines are read:
`load_peaks` gives one row per match, so a peak two targets matched appears
twice, and a tool keeps one row per line and sample before it sums, as A6's
solver does. An ion formula that names two target ions (in the demo's
uronium batch six do, `C10H20NO2+` among them: C10H16O2 through `[M+NH4]+`
and C10H19NO2 through `[M+H]+`, the same peak in every sample, 24 isotope
formulas between the six) is refused as ambiguous, with the two `compound:`
keys to ask for instead. `tic` is the samples listing's `tic`, the mean TIC
per MS1 scan that `mascope_viz` divides by, never notebook 06's sum of peak
heights. Over one batch the tool reads an `isotope:` key off `load_peaks`'s
`target_isotope_formula` (which only target-matched peaks carry), an `ion:`
or `compound:` key off `batches.ion_series`, and a `formula:` key off
`load_ledger`, since a peak list holds no ledger formula; until
`load_ledger` exists a tool refuses a `formula:` key and says so. Over a `mascope_viz` scope it translates them
to the store's keys through the species table (an ion formula to its
`ion:<target_ion_id>`, a ledger formula to its
`formula:<formula>|<mechanism>`), and refuses an isotope key there, since
the store holds no per-isotope series. The translation is one function in
`mascope_ai`, stated in the Frame's summary, so the grammar the model
learns does not change when `load_series` arrives.

Every tool below writes nothing, needs no plan, keeps any table it computes
as a derived Frame, and comes back through the MCP server under the 4 KB
bound with names inert. Its dict carries `refs`, the Mascope objects it
read, and `frame`, the derived Frame's id, as `compute_timeseries` carries
its `ref`: `_result` records a dict's refs and values only when the dict
names them, and only the outermost call is written to the manifest, so a
tool that loads inside itself and returned a bare dict would leave an
answer that names no object. The first tool brief (8.2) teaches `_result`
to record a dict's `refs` and `frame`, and every later tool follows.

### 3.1 Loaders that close the two gaps

**`load_ledger(dataset=, batches=, *, tier=None, min_n_present=2,
members=False, max_rows=)` -> Frame.** The batch ledger: one row per (sample, batch peak) with the
member's intensity and tier, the batch peak's `batch_mz`, consensus
formula, ion formula and tier, its `consensus_role` and `isotopologue_of`,
and the sample's time, so it is a series like `load_peaks`'s. Rests on two
reads per batch: `batch_peaks.series` (the compact route the app's batch
overview draws; its columns are the member's ids, `batch_mz`, the three
`consensus_*` fields, `intensity` and `tier`, and no role) and
`batch_peaks.list` for the species table, whose `consensus_role` and
`isotopologue_of` are joined on `batch_peak_id`; `samples.list` gives the
times. The two reads are pinned to one run: the loader lists the batch's
runs once (`batch_peaks.runs`) and passes that `run_id` to both, as
`mascope_viz`'s store does, since `series` without one pins itself to the
run current when it looks and `list` reads the live ledger, and a run that
completes between the two would join one state's intensities to another's
roles. A run the listing shows running makes the Frame incomplete with a
warning of the loader's own; the SDK only logs a line, which the MCP
server's sink drops, so there is no SDK warning to forward. The member's
own role (`M0`, `iso_child`, `reagent`, `artifact`, `unassigned`) is on
`batch_peaks.members`, a paged read that takes no `run_id` and so reads the
live ledger, and is not loaded by default: `members=True` asks for it, and
the loader lists the runs again after that read and makes the Frame
incomplete if the current run moved meanwhile. A batch with no ledger is
refused, with how
one is built (a batch assignment run from the app, or
`peak_assignments.assign` on its samples, after which the ledger folds them
in): `load_assignments` has no batch peaks (no `batch_peak_id`, `batch_mz`,
consensus or `isotopologue_of`), so nothing aligned across samples can be
read off it. Row cap and completeness as `load_peaks`. **One rule for
every reader:** an isotopologue anchor carries its parent's formula and
tier (the engine gives an `iso_child` row its parent's `assigned_formula`),
so `composition`, `homologous_series` and a `formula:` series drop the rows
whose `isotopologue_of` is set, as the store does; otherwise every
formula's 13C and 81Br lines count again. This is the Frame every
family-C, S and U tool reads, and the reason it comes first.

**`load_series(scope, *, keys, cadence=None, normalise="none", stat="median")`
-> Frame.** A tidy long series (one row per bin per key) over a scope of
batches, through `mascope_viz`'s campaign store and its `series` view:
targeted ions, ledger formulas, `tic` and the reagent ions, named in the
grammar above and translated to the store's `ion:<target_ion_id>` and
`formula:<formula>|<mechanism>` keys. Cadence defaults to the view's rule
(the ceiling of the native spacing); normalisation is `none`, `tic` or a
species key, and the normaliser used goes into the Frame's summary, never
silently. This is the
loader the time-series tools below rest on, and the one that makes a
campaign (several batches, several days) a scope rather than a loop the model
writes. It depends on `mascope_viz`'s phase 1 (MAS-222 to MAS-225 in the
Visual digests project; MAS-225, the store CLI, is still Todo) and on
`mascope_viz` being published to PyPI, which no workflow does today and no
issue plans (8.17); until then `timeseries_of` below computes over one
batch, from `load_peaks` and `batches.ion_series`, and says so.

### 3.2 Family T: time series and events

**`timeseries_of(scope, species, *, normalise="none", cadence="raw")`
-> dict + Frame.** One species' (or a few species') series over a batch or
a scope, one point per sample or per bin, at the level its key names; the
dict is its summary: `n_points`, `max` and `max_at`, `min`, `mean`,
`median`, `p10`, `p90`, `first`, `last`, the normaliser and cadence used,
the fraction of samples the species was found in. Rests on `load_peaks`
and `batches.ion_series` (one batch) or `load_series` (a scope); the
aggregation is `mascope_viz`'s
`series` view, at `cadence="raw"` by default over one batch, since the
view's own cadence rule rounds the native spacing up to whole minutes (the
uronium batch's 74 s spacing bins at 2 minutes and changes the point
count). Notebook 02 is the prototype. A1 becomes one call for
`isotope:C10H17O2+`; A2 becomes one call for `isotope:CH2BrO2-` with
`normalise="isotope:[81Br]Br2-"` over the batch (an isotope line, which
only the one-batch path over `load_peaks` can serve; over a store scope the
normaliser is the ion, whose four lines sum to 2.6 times that one line, so
the two answers differ and each names its key).

**`background(scope, species, *, method, n=5, sample=None, percentile=10)`
-> dict + Frame.** The background of a species and its series above it:
`method` is `first_n`, `sample` (a named blank) or `percentile`; the dict
gives the background value, the method as applied, the peak above background
and when, and the fraction of points above it. Rests on `timeseries_of`.
Notebook 07 and A3 are the prototypes. The three methods are closed; a
rolling-percentile baseline is `episodes`'s and not this tool's. A
percentile reads the background only where most of the run is background:
on the demo's uronium batch `C10H17O2+` is above three times its pre-event
level in 75 of 81 samples, so its 10th percentile (3.2e5) sits inside the
plume at seven times A3's 4.6e4, and the dict says which fraction of points
lie above the background it took, so a model can see that.

**`stages(batch, stages, *, species=None, normalise="none",
reference=None)` -> Frame + dict.** Stage means across a batch's timeline
(whole samples per stage, UTC boundaries, ends exclusive, a sample in no
stage reported), per species key or for every target ion when `species` is
None, with every undetected species counted as zero before averaging as
notebook 09 does, and the log2 fold change against a `reference` stage. A
species absent from the reference stage has no fold (NaN, as notebook 09
makes it, never infinity) and is listed under `new_in_stage` instead, so
the species the event brought in are not dropped by the arithmetic that
cannot rank them. Returns the per-stage table as a Frame and a dict of the
top risers, fallers and new species against the reference.
`load_peaks_by_stage` stays the within-sample counterpart.

**`diurnal(scope, species, *, tz="instrument", by=None)` -> dict + Frame.**
The hour-of-day profile over a scope of at least two days: the median per
local hour (and interquartile band) per species or class, the hour of the
maximum, the day-to-night ratio (a closed definition: 10 to 16 h against 22
to 4 h local), `n_days`. Rests on `load_series` and the `diurnal` view.
Peaky's `diel_classes.py` is the reference; its class grouping uses
`formula_class` (3.4). **Refuses** a scope under two days or one day with a
gap over half the hours, naming the span: the demo's batches span two hours,
so on the demo this tool's bench question is a decline.

**`episodes(scope, species, *, baseline="rolling_percentile", window="6h",
percentile=20, factor=3.0, min_duration="10min")` -> dict + Frame.** The
events of a species: a baseline as a rolling low percentile, an exceedance
above `factor` times it, exceedances merged into episodes with their start,
end, peak, peak time and fold. `visual_digest.md` lists this chart as tier 3,
"method to choose and state"; the method above is a proposal, and the choice
of baseline is the open Decision MAS-244 of the Visual digests project
(decision 7.1 here attaches to it rather than filing a second). Until
decided the tool is deferred; the demo's one event is read with
`background` and `method="first_n"` or `method="sample"` (the pre-event
samples), not with a percentile, which sits inside that plume.

### 3.3 Family N and R: normalisation, the reagent and the instrument

**`reagent_ions(scope)` -> dict.** The reagent chemistry of a batch or a
sample and the ions the source made of itself, as Mascope knows them: the
profile `detect_reagent_profile` names from the mode's mechanisms together
with the sample's polarity and instrument type, which `describe` already
returns (bromide, uronium, nitrate, 15N-nitrate, iodide; charge transfer
only on an Orbitrap, ESI only from the polarity, none), the ions of the
reagent's own ladder and of the air from `composition.reagents`, each
flagged **inside or outside the scan window** and, apart from that,
**beyond the recorded axis**: the window is the sample view's `mz_min` and
`mz_max`, the stream's scan range, which `describe` gains; the recorded
axis is `describe`'s `mz_range`, the file's m/z axis ends, which sit tens
of m/z inside the window (the demo's bromide files start at 82.02 to 83.05
and end between 661 and 750 against their 82-750 window; the uronium files
end at 741 to 749 against 750), so an ion such as `[NO2+2xH2O]-` at
82.0146 is inside the window and beyond every file's axis, and the two
flags say so apart. A batch scope reads each sample's window and reports
the intersection, naming the samples whose window differs. For the in-range
ions the tool gives the median height, presence fraction and coefficient
of variation over the batch, matched to the peaks through
`composition.reagents.match_reagent_clusters` (the library's own matcher,
since a single ppm window does not fit a reagent ladder) or, where a ledger
exists, through the engine's own claims (`consensus_role` `reagent`), and
the recommended normaliser as a key of the grammar above, which must be one
`timeseries_of` can read: the brightest in-window reagent line that a
target matches in every sample (`isotope:[81Br]Br2-` on the demo's bromide
batch), and `tic` when there is none (the demo's uronium batch: no target
matches the urea trimer, which has a peak within 3 ppm in one of 81 files,
so its answer is `tic`). Over MCP the dict leads with the
in-range ions and the recommendation, carries the citations as short keys
into the user page's reference list rather than the works in full, and
counts what the 4 KB bound left out (the bromide library alone is 29 ions).
Rests on `describe`, `load_peaks` and the two `mascope_tools` modules; a
secondary channel's fingerprint (ammonium, sodium, carbonate) comes with
it as present or absent, from `composition.reagents`' probes. This is the
tool that stops a model normalising a 122-750 uronium window to protonated
urea at m/z 61: the answer names the trimer at 181.1044 as the one reagent
ion inside the window, says it is matched by no target and found in one
file, and recommends `tic` with that reason. It is also the ion-source
diagnostic: a
reagent ion whose coefficient of variation is high, or that vanishes
mid-batch, is reported as such, with no cause named.

**`instrument_health(scope)` -> dict.** The instrument's state over a
batch or scope, from what Mascope stores: the TIC series' median, spread
and drift (first to last tercile), the reagent ions' from `reagent_ions`,
the calibration status counts (`describe`'s `mz_calibration.status` and
`verified` over the samples), and the mass accuracy in two readings that
must not be confused. The **acquisition's offset** is in the calibration
record's quality, `pre_fit_mz_error_ppm` (the unsigned mean error before
the fit; Mascope warns above 10 ppm on an Orbitrap), which `describe` gains
beside `status` and `verified`, read per sample and summarised over the
scope with its tercile trend. The **assigned ions' residual** comes from
each sample's latest completed peak assignment run, not from the batch
ledger (whose runs carry an action, a status, a config and a summary, and
no width): `peak_assignments.list_runs` gives the run's `mu_ppm` and
`sigma_ppm` (null on a batch run; the decoy runs left out by default), and
`load_assignments` the rows' `mz_error_ppm`, per sample and over the scope.
The stored m/z are post-calibration, so the
calibrant collection's own ions sit at about 0 ppm by construction and are
reported as the fit's residual, never as the offset; `query_by_mz` (a 5 ppm
window over C, H, O and N) is not the way to a theoretical m/z here, and
`composition.mass_accuracy`'s fitting functions need columns no Frame
carries, so neither is used. The tool reports an offset; it never corrects
for one. The skill says what
the owner says: an offset is a calibration fault, calibrate before
assigning, and a run whose assigned ions sit at a common offset is suspect
as a whole. The `qa` digest recipe of `visual_digest.md` is the composition
this tool is a step of.

### 3.4 Family C and S: composition, oxidation state, series

Three rules land in `mascope_tools` first, as one function each, with the
engine's ledger, the digests' species table and the toolkit as callers:

- **`formula_metrics(formula, *, context=None)`** in `composition.utils`:
  element counts (caret isotopes folded into their element, so a 15N
  formula is CHON, not CHONX), DBE by the engine's own convention
  (`heuristic_filter.effective_counts`, public already and used by
  `implausibility` and `mascope_reference`; `finder.get_unsaturation` has no
  coefficient for Si or P and gives a siloxane a negative DBE), O/C,
  H/C, N/C, OSc as 2 O/C minus H/C (Kroll et al. 2011), the class, the
  HOM flag, the oligomer flag, and whether the context admits the formula
  in two parts, as the engine applies a context twice: `ratios_ok`, the
  engine's ratio gate (`heuristic_filter._context_ratios_ok`, made public:
  effective counts, the windows applied from three carbons up), and
  `within_caps`, the context's `element_caps` applied to the neutral (the
  caps narrow the engine's search grid through `resolve_element_ranges`,
  so a formula over a cap is one the context's search never proposes,
  though Stage A's known set may still bring one in). The two are reported
  apart, since the ratio gate alone admits C10H16N4O4, C10H15IO3 and
  C6H5FO2 under ambient air, whose caps are N 3, I 0 and F 0. **No mass.**
  The function serves no
  exact, nominal or Kendrick mass of a formula: `ai_layer.md` 3.3 makes
  "no tool computes a mass" the property that keeps a model from inventing
  an assignment, and nothing in C2 or S2 needs one (S2 gives a measured
  m/z). The HOM threshold (six oxygens, Bianchi et al. 2019, in the
  ambient-air context; the context may set another) and the oligomer rule
  are *calls for the plan owner* (decision 7.2): peaky's `oligomer_flag` is
  carbon 18 to 40, oxygen at least 7, assigned tier only, no silicon; the
  alternative is relative to the context (carbon at or above twice the
  context's monomer carbon number, oxygen at or above the HOM threshold).
  The function takes them as parameters with the context's defaults.
- **`formula_class(formula, *, backbone=False)`** is a copy of
  `mascope_viz.data.store.compound_class`'s rule (the letters of C, H, O,
  N, S the formula holds, an `X` suffix for any other element, `inorganic`
  without carbon), over counts with caret isotopes folded; `compound_class`
  itself stays in `mascope_viz`, whose data layer takes no `mascope_tools`
  dependency, and a parity test holds the two to one answer, a 15N formula
  among its cases. `backbone=True` is peaky's `backbone()` rule exactly:
  CHOS when sulfur is present, else CHON when nitrogen is, else CHO, so
  CHONS counts as CHOS and CH as CHO, with Si, F and halogens folded in;
  the report compositions ask for it and the default does not. `diurnal`,
  `composition` and the digests' `class_series` group by it.
- **`composition.series`**, a new module, in two halves that answer two
  questions. **`kendrick(mz, unit, *, convention="km_minus_round",
  scale=None)`** is the one Kendrick function, landing with 8.4 and reused
  by 8.8: the defect's sign and rounding are a convention (peaky's
  `series_gka` takes `GKM - round(GKM)`, its `gka_figure` the reverse), so
  it is a parameter, and peaky's scaling factor `X` another.
  **`find_homolog_series(formula_mz, unit, min_length)`** lists the ladders
  of a set of exact formulas under a repeat unit with their members (peaky's
  `series_gka.find_homolog_series`, which walks formulas and takes no
  tolerance, is the reference). **`series_enrichment(mz, unit,
  tolerance_ppm, min_links)`** is the decoy-controlled test of whether a
  repeat unit is enriched among a set of m/z at all, with the chains of
  peaks it rests on (peaky's `series_detect.detect_series`, which returns
  the unit's link count, chain count, decoy mean, enrichment and
  significance, `min_links=12`, and `series_detect.unit_chains`, the
  maximal chains of peaks spaced by the unit's mass; both run over the
  unexplained and, by default, the low-confidence M0 peaks, not the
  assigned ones). Both halves move as specifications with their bench
  readings per section 8 of the boundary note (one register row each, A13
  for the ladders, A13b for the enrichment test). Peaky's copies stay
  behind a switch for one release.

Then the tools:

**`formula_facts(formula, *, mechanism=None, mz=None, unit="CH2",
context="ambient-air")` -> dict.** `formula_metrics` served: counts, DBE,
ratios, OSc, class, the HOM and oligomer flags, whether the context admits
it (the context's own words, from `profiles`; the names are the library's,
`ambient-air` with its aliases `ambient` and `atmospheric`, never
`ambient_air`), and, only when a measured `mz` is given, its Kendrick mass
and defect under `unit`. No mass of the formula itself, for the reason
above. An ion formula is given with its `mechanism` (`[M+H]+`, `[M+Br]-`),
and the tool reduces it to the neutral through `parse_ionization` and the
inverse of `combine_formula_and_ionization`, lifted into `composition.utils`
from the backend's `ionization_catalogue._neutral` (8.4), before the
metrics; without a mechanism the formula is read as the neutral. Never a
score or a verdict; those are `match_compounds`'. The one tool a model
calls when it is about to do formula arithmetic in its head.

**`composition(scope, *, tier="assigned", weight="intensity")` -> dict +
Frame.** The composition of a batch or scope from its ledger: per backbone
class the count of distinct neutrals and the share of assigned intensity;
the intensity-weighted mean and the quartiles of O/C, H/C and OSc; the
carbon-number distribution; the HOM count and intensity share; the oligomer
candidates; the fraction of total signal the tier covers (the coverage
sentence peaky's report leads with). The per-formula table (one row per
species with its metrics) is the derived Frame, which `chart` draws as the
Van Krevelen (`van_krevelen`), the oxidation-state plot and the
class-composition bars once `chart` is served (MAS-553). Rests on
`load_ledger` and `formula_metrics`. Peaky's `composition.py`
(`signal_by_backbone`, `top_species_by_signal`, `oligomer_flag`) is the
reference; its ammonium-amine degeneracy accounting is a positive-mode rule
that moves with it.

**`homologous_series(scope, *, unit="CH2", tier="assigned", min_length=3)`
-> dict + Frame.** The series found among a scope's assigned formulas under
a repeat unit (`CH2`, `O`, `CO2`, `H2O`, `C2H4O`, `C2H6OSi`, `CF2`): each
with its base formula, its members in order, its length and its summed
intensity; the ladder table as the Frame, which `chart` draws as
`mass_defect` in its Kendrick form. Rests on `load_ledger` (the
isotopologue rows dropped) and `composition.series.find_homolog_series`.
With `tier="unassigned"` the tool answers the other question, whether the
unit is enriched among the unexplained peaks' m/z at all, through
`series_enrichment`, and returns the test's counts, its tolerance and its
significance rather than ladders. A unit with no series is an empty list
with the count of formulas tried (and, for the enrichment half, the
tolerance), so a question about a ladder that is not there is declined with
evidence.

### 3.5 Family V and U: covariation and the unassigned

**`covariation(scope, *, species=None, top=100, normalise="none",
cadence="raw", r_min=0.7)` -> dict + Frame.** The families of covarying
series: a reference species' most correlated partners when `species` is
given (the asked ion's own isotope lines and the same compound's other
ions left out, since they covary by construction), otherwise the
hierarchical clusters of the `top` brightest series (members, size, a
representative), with the series outside every family listed. Two methods
exist and the tool ships with the first: **notebook 08's**, Pearson
correlation on raw heights, distance `1 - |r|`, average linkage, a flat cut
at `r` 0.7, and no flatness test, so a family of flat lines is a family;
and **peaky's** (`batch/cluster.py`), correlation of log10 intensities, a
signed distance that keeps anti-phase apart, complete linkage at `r` above
0.6, and a flatness gate that pulls a trace out before clustering unless
its coefficient of variation reaches 0.30 or its smoothed maximum over
median reaches 1.7 (`trace_varies`, both criteria), then a merge of
near-identical families and a demotion of flat clusters. Rests on
`load_series` or `load_peaks` and a `mascope_tools.stats.covariation`
module the rule lands in. The first PR ships notebook 08's rule, which the
fixture pins; `visual_digest.md` lists the chart as tier 3 with peaky's
method as the reference, and its open question 5, whether that method is
ported, is the Decision MAS-244, so peaky's moves by the protocol (register
row A14) as a second PR once MAS-244 says so, and the fixture's known
answers move with it, with the bench card as the proof. The tool names
families, never sources: a cause is the model's inference and is recorded as
such.

**`unassigned(scope, *, top=20, sample=None)` -> dict + Frame.** What the
ledger did not explain. **Unassigned** here is a batch peak of
`consensus_tier` `unassigned` whose `consensus_role` is null: a peak the
source's pre-pass claimed keeps the unassigned tier beside a
`consensus_role` of `reagent` or `artifact` and a `consensus_ion_formula`,
and on a CIMS batch those are the brightest peaks and most of the signal,
so they are reported on a line of their own (the source's share, by count
and intensity), as the app keeps them apart, and never as unexplained. Per
tier the peak count and the intensity share; the `top` unassigned and
below-assignability peaks by median intensity over the batch, or in one
`sample` when given, with their `batch_mz` and presence fraction. The
species table's `isotopologue_of` is null for every unclaimed unassigned
anchor by construction (no member carries a formula, so the consensus
makes no link), so the tool says of such a peak that Mascope makes no
isotope reading of it, never that it is no isotope line. Rests on
`load_ledger`. The follow-up for one peak is `query_by_mz` and
`match_compounds`, which already exist; the skill says to call them and to
present the result as Mascope's verdict.

### 3.6 Family X: comparison

**`compare(scope_a, scope_b, species, *, normalise="none",
stat="median")` -> dict.** One species in two scopes: each scope's summary
(as `timeseries_of`), the ratio of the statistics, the number of days or
samples each rests on, and the two scopes' reagent profiles and polarities
from `reagent_ions`. **The closed rule:** when the profiles or polarities
differ the dict carries `comparable: false` with the reason (a bromide and
a uronium reading of one formula are two ionization-selective measurements
of possibly different molecules) and withholds the ratio; the two summaries
stay, each under its own scope. Rests on `timeseries_of` twice. The matrix of a campaign against a campaign is the
`comparison` chart of `visual_digest.md`, tier 3, which this tool serves the
numbers to.

### 3.7 What stays Peaky's: the recipes

The batch report (findings, coverage, composition, scrutiny, GKA, families,
changers, clusters, methods), "assess this campaign", "what changed this
week", and the `campaign`, `weekly`, `qa` and `composition` digests are
compositions of the tools above with parameters and a narrative template.
They are milestone 2's recipes, run by the toolkit's `run_recipe` so the
manifest is kept, with their compositions in Peaky's data files under
decision 5.4 of the shell note (MAS-662). This note adds no recipe; it adds
the steps a recipe is made of, and section 6 says which steps the batch
report needs first.

### 3.8 What the escape hatch keeps

Python on the session's Frames stays for the question none of the above
fits: a ratio of two species' backgrounds, a stage mean over a window the
tools do not offer, a correlation against an external time series. Over
MCP that path is the `session` tool, whose Python opens the Frames and a
toolkit on the same session; `derive` is the method in that Python that
keeps the computed table with its sources, and it is not an MCP tool. The
skill says when to reach for it (after the coarse tool declined, never
before) and the manifest records its code, so a derived number is
auditable. The bench keeps one analysis question per family whose
reference path is Python and `derive`, so the escape hatch is exercised and
a model that cannot use it is seen.

### 3.9 Summary

| Tool | Family | Rests on | Rule lands in | Prototype or reference |
|---|---|---|---|---|
| `load_ledger` | C, S, U | SDK `batch_peaks.runs`, `batch_peaks.series` and `batch_peaks.list`, pinned to one run | none (a loader) | notebook 10 |
| `load_series` | T, V, X | `mascope_viz` store and `series` view | `mascope_viz` views | `visual_digest.md` 2.4 |
| `timeseries_of` | T | `load_peaks` and SDK `batches.ion_series` (one batch), `load_series` (a scope) | `mascope_viz` `series` | notebook 02; A1, A2 |
| `background` | N | `timeseries_of` | the three methods, in the tool | notebook 07; A3 |
| `stages` | T | `load_peaks` and SDK `batches.ion_series` (every target ion when `species` is None) | zero-fill before averaging, in the tool | notebook 09 |
| `diurnal` | T | `load_series`, `diurnal` view, `formula_class` | `mascope_viz`; `formula_class` | peaky `diel_classes.py` |
| `episodes` | T | `load_series` | deferred: decision 7.1 | `visual_digest.md` `episodes` |
| `reagent_ions` | N, R | `describe`, `load_peaks`, `composition.profiles`, `composition.reagents` | `mascope_tools` (exists) | peaky `reagents.py`, `profiles.normaliser` |
| `instrument_health` | R | `describe` (TIC, calibration status and quality), `reagent_ions`, SDK `peak_assignments.list_runs` (`mu_ppm`, `sigma_ppm`) and `load_assignments` (`mz_error_ppm`) | none (reads what Mascope stores) | `qa` digest; `tof_calibration.md` |
| `formula_facts` | C | `formula_metrics`, `kendrick` | `composition.utils` (new function; `formula_class` a copy of viz's `compound_class` with a parity test), `composition.series.kendrick` | peaky `chemistry.py` |
| `composition` | C | `load_ledger`, `formula_metrics` | `composition.utils` | peaky `composition.py` |
| `homologous_series` | S | `load_ledger`, `composition.series` | `composition.series` (new module, register A13 and A13b) | peaky `series_gka.find_homolog_series`, `series_detect.detect_series` |
| `covariation` | V | `load_series`, `stats.covariation` | `mascope_tools.stats.covariation` (new module; notebook 08's rule first, peaky's by register A14 once MAS-244 decides) | notebook 08; peaky `batch/cluster.py` |
| `unassigned` | U | `load_ledger` | none | notebook 10; peaky's coverage page |
| `compare` | X | `timeseries_of`, `reagent_ions` | the comparability rule, in the tool | peaky's cross-CIMS gotcha |

## 4. The bench additions

Every tool comes with two or three golden questions on the public demo
dataset, in the fixture's vocabulary and with the shape of its reference
computation, and at least one trap or decline per family. The questions
below name the demo's objects the fixture already names: the 2025-08-11
bromide batch (80 acquisitions, `[M+Br]-` and `[M-H]-`, scan window m/z
82-750), the 2025-08-11 uronium batch (81 acquisitions, the mode's
mechanisms `[M+H]+`, `[M+NH4]+` and `[M+CH4N2O+H]+`, scan window m/z
122-750; "122-740" is the mode's name, not its window; spanning 11:23 to
13:00 UTC), the 2026 acquisitions beside them, and the ions the goldens
know: `C10H17O2+` (the uronium event), `C11H21N2O3+`, `C10H20NO2+`,
`C8H19O3+`, `C16H23O4+`, `CH2BrO2-` (formic acid) and `[81Br]Br2-` (the
reagent trimer's brightest line). A reference value marked *goldens* is
computed from the bundle's `expected/peaks.parquet` as the existing
`golden=` callables are; one marked *ledger* needs an assignment run on the
stack, which is the prerequisite in 4.9.

**Categories and IDs stay the fixture's.** `test_ai_golden.py` pins the
categories to the four of `CATEGORIES` and each ID's initial to its
category's, and pins the declines to a list whose every entry has
`available` False, a `Words` reason and `check=_no_value`. The letters
below (T1, N1, C1, S1, R1, V1, U1, X1, D-T and so on) are the note's
**family tags**, not IDs: when a question is filed it takes the fixture's
category and the next ID in it (a time-series summary is `analysis`, A7; a
verdict on an unassigned peak is `interpretation`, I4; every decline is
`decline`, D6 onward, in the declines' exact shape), and gains a `family`
field (T, N, C, S, R, V, U, X) that `list` prints as a column, so the
note's grouping survives in the fixture without a fifth category and the
shell note's per-class thresholds (decision 5.3) still cover every
question. The brief that adds the field and extends the two tests is the
first tool brief (8.2); the declines' test, which lists D1 to D5 exactly,
becomes a rule (every `D` ID, in order, in the declines' shape) there, so
a later brief adds a decline without touching it. Every species in a known
answer is named by its key in the grammar of section 3, so what a point is
cannot be misread.

**What the fixture scores for a decline, and what it cannot.** A decline's
known answer is `available` False and a `Words` reason; `_no_value` fails a
JSON number anywhere in the answer, and `Words` passes any text that holds
its words, whatever else the text says. So the fixture catches a value
given and a reason that misses what is missing, and nothing else: a cause,
a verdict or a "higher" written into the reason's words passes, and is a
human verdict in the model-run review until a check exists (8.13 plans one
as an option, for D-V's source names). Where a decline's right answer
carries a number (D-S's count and tolerance, D-C's m/z), the number goes in
the reason's text, never in a field, or `_no_value` fails the right
answer.

### 4.1 Family T

- **T1** "Summarise `isotope:C10H17O2+` over the 2025 uronium batch: how
  many points, its mean and median, its maximum and when, its 10th and 90th
  percentile." Reference: the per-sample heights of that line from the
  goldens, `describe` of the series; A1's maximum reappears as one field.
  The key is the isotope line, since the ion's lines sum to 10 to 11% above
  it, outside the 1% tolerance. Through `timeseries_of`.
- **T2** "Split the 2025 uronium batch into a background stage (its first
  five samples, about the first five minutes) and an event stage (the
  rest): the mean of `isotope:C10H17O2+` in each and its log2 fold change."
  Reference: the goldens' heights in sample order, the first five against
  the rest (A3's background is the same five: `C10H17O2+` sits near 4.6e4
  there, rises at the sixth sample about six minutes in, and peaks at
  14:34:03, eleven minutes in, so a longer background stage would hold the
  plume), zero for a sample the ion is absent from, means, log2 of the
  ratio. The goldens carry no `datetime_utc`, only the sample's local file
  time, so the fixture states the boundary as a sample count or in the
  samples' own local time and maps it onto UTC for the tool's call.
  Through `stages`.
- **D-T** (decline) "What is the diurnal cycle of `C10H17O2+` in the 2025
  uronium batch: at which hour does it peak?" The batch spans under two
  hours of one day; the right answer is not available, naming the span
  (`describe`'s `first_measured` and `last_measured`). A peak hour given is
  wrong; a profile over two hours called a cycle in the reason's words is a
  human verdict, since the decline shape reads no words beyond its own.
  Through `diurnal`, which refuses.

### 4.2 Family N

- **N1** "Formic acid (`isotope:CH2BrO2-`) in the 2025 bromide batch
  normalised to the reagent line the toolkit recommends: which key, its
  mean and its maximum and where." Reference: `reagent_ions` must name
  `isotope:[81Br]Br2-` as the brightest in-range reagent line present in
  every sample, and the ratio for that key is A2's (mean 0.0959); the ion
  key, `ion:Br3-`, whose four lines sum to 2.61 times that line, gives
  0.0367 and is the wrong answer to this question. Through `reagent_ions`,
  `timeseries_of`. The question differs from A2 in that the model is not
  told the line.
- **N2** "`isotope:C10H17O2+` above the background of the 2025 uronium
  batch's first sample: the background and the peak above it." Reference:
  the goldens' height in the first sample, the maximum of the difference.
  Through `background` with `method="sample"`; A3 remains the `first_n`
  form, and the percentile method has no honest question on this batch,
  since its 10th percentile sits inside the plume (3.2).
- **D-N** (decline) "Normalise `C10H17O2+` in the 2025 uronium batch to
  protonated urea, `CH5N2O+`." m/z 61.0397 is below the acquired range (D3
  already says so for the peak); the right answer is not available with the
  reason, and names what `reagent_ions` recommends instead on this batch,
  `tic` (the one in-window reagent ion, the trimer, is matched by no target
  and found in one file). A ratio given is wrong; a ratio to TIC presented
  as the urea normalisation is a human verdict.

### 4.3 Family C

- **C1** "What are the intensity-weighted mean O:C, H:C and OSc of the
  assigned formulas of the 2025 uronium batch, and how many distinct
  neutrals are CHO, CHON, CHOS?" Reference (*ledger*): the batch ledger's
  assigned species, `formula_metrics` of each, weighted by summed member
  intensity; counts by `formula_class`. Through `load_ledger`,
  `composition`.
- **C2** "What are the DBE, O:C, H:C, OSc and class of `C10H16O2`, and does
  the ambient-air context admit it?" Reference: `formula_metrics` with the
  context's windows; a fixed answer (DBE 3, O/C 0.2, H/C 1.6, OSc minus
  1.2, CHO, admitted). Through `formula_facts`. The checker fails a model
  that computes the ratios itself without the tool (the ops the harness
  passes to the check show no `formula_facts` call): sloppy.
- **D-C** (decline) "What is the O:C of the brightest unassigned peak of
  the 2025 bromide batch?" Reference (*ledger*): the brightest batch peak
  of tier `unassigned` with `consensus_role` null (the source's own Br3-
  lines sit in the unassigned tier with a role and an ion formula, and are
  not it). Such a peak has no formula; the right answer names its m/z and
  says the ratio is not available; a ratio from a mass-query candidate
  presented as the peak's is wrong (the candidate is a candidate, scored or
  not).

### 4.4 Family S

- **S1** "Which CH2 homologous series of at least three members are
  present among the assigned formulas of the 2025 uronium batch, and which
  is longest?" Reference (*ledger*): `find_homolog_series` over the
  assigned formulas with the CH2 unit; the series, sorted by length then
  intensity. Through `homologous_series`.
- **S2** "What are the Kendrick mass and Kendrick mass defect of
  `C10H17O2+`'s measured m/z in the 14:34:03 uronium sample, under the CH2
  base?" Reference: A4's m/z through `kendrick` (8.4's function, the
  `km_minus_round` convention); a fixed arithmetic. Through `formula_facts`
  with `mz=` and `mechanism="[M+H]+"`.
- **D-S** (decline) "How long is the CF2 ladder in the 2025 bromide batch?"
  Reference (*ledger*): `find_homolog_series` under CF2 finds no ladder
  among the assigned formulas, and `series_enrichment` finds the unit not
  enriched among the unexplained peaks' m/z (both to be confirmed on the
  stack when the fixture is written; if a ladder is found, the trap moves
  to the C2H6OSi unit or to the uronium batch). The right answer is "none
  found", with the count of formulas tried and, for the enrichment half,
  its tolerance; a length given is wrong.

### 4.5 Family R

- **R1** "Which reagent chemistry is the 2025 bromide batch, which ions did
  its source make of itself inside the acquired range, and which is the
  recommended normaliser?" Reference: the bromide profile; the reagent
  ladder's ions inside the 82-750 scan window (the bromide hydrates, the
  Br2- and Br3- lines) each found or not in the goldens' peaks, Br- itself
  (m/z 78.92 and 80.92) flagged as outside the window, and `[NO2+2xH2O]-`
  at 82.0146 flagged as inside the window yet beyond every file's recorded
  axis (which starts at 82.02 to 83.05); the recommendation
  `isotope:[81Br]Br2-`. Through `reagent_ions`. The exact ion list is read
  off `composition.reagents` when the fixture is written, so the question
  tests that the model reports Mascope's list, not its own.
- **R2** "Over the 2025 uronium batch, what was the acquisition's mass
  error before calibration, sample by sample and over the batch, and what
  are the median and spread of the assigned ions' residual error in the
  samples' assignment runs, by tercile of the batch?" Reference: each
  sample's calibration
  record, `quality.pre_fit_mz_error_ppm`, summarised (read off the stack's
  records when the fixture is written, as M5 reads its span); each sample's
  latest completed assignment run's `mu_ppm` and `sigma_ppm`
  (`peak_assignments.list_runs`), or its rows' `mz_error_ppm`
  (`load_assignments`), per tercile (*runs*: the same setup step as the
  ledger questions, since a run is what builds the ledger). Through
  `instrument_health`. The calibrant collection's own
  ions are not the question: post-calibration they sit at about 0 ppm by
  construction. The answer that recommends tuning the match tolerance to
  absorb an offset is sloppy (the check reads the answer's text); the skill
  says to calibrate.
- **D-R** (decline) "How stable were the uronium (protonated urea) reagent
  ions in the 2025 bromide batch?" The profile is bromide and the polarity
  negative; protonated urea and its clusters are positive ions, and none is
  in the data. Not available, naming the profile and the polarity. A
  stability figure is wrong. (Iodide is not the trap: every bromide sample
  has a peak within 3 ppm of I- at 126.905 with a cv of 0.09 over the
  batch, so a figure for that line would be a real reading, not a
  fabrication.)

### 4.6 Family V

- **V1** "Which five lines of other compounds covary most strongly with
  `isotope:C10H17O2+` across the 2025 uronium batch?" Reference
  (*goldens*): notebook 08's rule as the tool first ships it, the
  per-sample heights of every found isotopologue line, Pearson on the raw
  heights against `C10H17O2+`'s, the top five by `r` among lines present in
  at least half the samples, **leaving out the asked ion's own isotope
  lines and the other ions of its compound** (on the goldens the unfiltered
  top five are its own 13C line at `r` 0.9996, two formula-consistent
  fragments and C10H16O2's urea adduct and that adduct's 13C line, which is
  the compound covarying with itself). The margin between the fifth and
  sixth place is checked when the question is written, since ranks 4 to 6
  sit within 2e-4 in `r` before the filter; a margin under the tolerance
  moves the question to the top three. Through `covariation` with
  `species=`. When peaky's method moves in (register row A14) the answer is
  recomputed on log10 heights and both are recorded, as V2's are.
- **V2** "How many covarying families of at least three members are there
  among the 50 brightest lines of the 2025 uronium batch?" Reference
  (*goldens*): notebook 08's rule as the tool first ships it, Pearson on
  the heights, distance `1 - |r|`, average linkage cut at `r` 0.7; the
  count (two on the goldens, of 21 and 7 lines, with 22 outside). No flat
  count is asked: notebook 08's rule has no flatness test, and the family
  of 7 has coefficients of variation of 0.05 to 0.10, which peaky's gate
  would pull out. When peaky's method moves in (log10, signed distance,
  complete linkage at 0.6, the gate on cv 0.30 or smoothed max over median
  1.7; register row A14) the known answer is recomputed under it, the flat
  count is added, and the two values are recorded side by side in the
  fixture's comment, so the move's effect on the demo is written down.
- **D-V** (decline) "What is the source of the family `isotope:C10H17O2+`
  belongs to?" There is no tool answer and no value: the right answer is
  not available, the reason naming that a family is a shared time
  behaviour and the source is not in the data. A value given is wrong; a
  source written into the reason's words passes the fixture (the decline
  shape reads no words beyond its own) and is a human verdict until 8.13's
  optional check exists. The question a model may answer with its own
  reading is V1, where a cause offered beside the members belongs in the
  manifest's `inferred`, which the skill says.

### 4.7 Family U

- **U1** "What share of the 2025 uronium batch's summed intensity is
  assigned, candidate, below assignability, unassigned, and the source's
  own, and what are the three brightest unassigned peaks?" Reference
  (*ledger*): the ledger's tiers by summed member intensity, the
  `consensus_role` `reagent` and `artifact` anchors counted apart as the
  source's; the top three batch peaks of tier `unassigned` with a null
  role, by median intensity, with their `batch_mz` and presence. Through
  `unassigned`.
- **U2** "What is the brightest unassigned peak of the 2025 uronium batch:
  its m/z, Mascope's candidates for it in the 14:34:03 sample and their
  verdicts?" Reference (*ledger* for the peak; the oracle for the verdicts):
  `unassigned` names the peak over the batch, `query_by_mz` and
  `match_compounds` give the verdicts in the named sample as I2 does. The
  I-family check applies: nothing unscored is a verdict.
- **D-U** (decline) "Is the brightest unassigned peak of the 2025 uronium
  batch an isotope line of a brighter neighbour?" For an unclaimed
  unassigned anchor the species table's `isotopologue_of` is null by
  construction, so Mascope makes no isotope reading of it: the right answer
  is not available, the reason naming that the ledger reads isotope lines
  only for assigned and source-claimed peaks. The trap is a model that
  computes a 1.00335 spacing itself and declares an isotope, or that reads
  the null as "no": both wrong, since that reading is Mascope's to make and
  it has not made one.

### 4.8 Family X

- **X1** "Is `isotope:C10H17O2+`'s median height higher in the first half
  of the 2025 uronium batch or in the second, and by what factor?" Two scopes of
  one batch by time range: the first 40 acquisitions and the last 41.
  Reference (*goldens*): the per-sample heights in sample order, split at
  the 40th, medians, the ratio; one profile, so `comparable: true`.
  Through `compare`. (The 2026-10-15 Uronium RI acquisition is one MS2 file
  with no polarity segment in its name, which the goldens leave out, so it
  gives no reference; a comparison across two MS1 uronium batches waits
  for a second public day, decision 7.4.)
- **D-X** (decline) "Was formic acid higher in the 2025 bromide batch than
  in the 2025 uronium batch, and by what factor?" The profiles and
  polarities differ, and formic acid has no target in the uronium
  collections. The right answer is not available, the reason naming the two
  chemistries (ionization-selective detection) and that formic acid is not
  a target of the uronium batch; `compare` withholds the ratio for that
  reason, and a factor stated is wrong.

### 4.9 What the bench needs first

- **Assignment runs on the stack.** The *ledger* questions (C1, D-C, S1,
  D-S, U1, U2, D-U) need a batch ledger, and R2's residual half needs the
  samples' assignment runs; the demo snapshot ships with neither, since
  peak assignment is on only in CI's stack and a seeded sample is not
  assigned until a run is launched. The fixture gains a setup step that
  launches the two 2025 batches' runs through the SDK and waits, as the
  SDK's contract tests do, which makes both the runs and the ledger they
  fold into, and the known answers are then held to the engine version the
  run records (a change of engine moves them, as a bundle change moves the
  goldens). That setup step is the plan. The alternative, a demo bundle
  shipped with a completed assignment run and a batch ledger, is MAS-331 in
  the AI layer project, an Improvement in its backlog, not a Decision:
  decision 7.3 is filed as a Decision of its own that **blocks MAS-331**,
  since a related link gates nothing and only a `blockedBy` keeps the
  bundle from being re-cut as ordinary work, moving the goldens, before the
  owner has chosen.
- **The dry run.** Every new question goes through `ai_golden.py dryrun`
  before any model: the known answer passes, every fabrication fails, no
  question is missing.
- **A Python question per family stays.** A2, A3 and A6 keep their
  pandas-and-`derive` solvers beside the new tool solvers (A1 and A4 solve
  through `reduce` since #2983), as the evidence that the escape hatch (the
  `session` tool's Python, over MCP) still works and as the control the
  coarse tools are measured against on the matrix.
- **Sloppy becomes checkable, which is new work.** Two of the traps above
  are instruction-following checks: C2's reads the tools the solver called
  (the harness maps the record's calls to ops into `Outcome.tools`, outside
  the check today, so the check must be handed them), and R2's reads the
  answer's text. The fixture's `check=` hook receives the answer and the
  record (I1 and I2 use the record, through `_nothing_unscored_is_a_verdict`;
  I3's check reads the answer alone), but `run_question` scores any check
  failure as `wrong`, and a model run is manual today
  (`golden_transcripts/README.md`), with no driver in the repository. A
  `sloppy` status, a check that can return it and is handed the ops, and
  its place in the model-run vocabulary are a brief of their own (8.13),
  verified on a scripted question of its own, which lands before the two
  questions: 8.4 and 8.7 are blocked by it.

## 5. What the skills teach

Two skills, per the shell note's rule: the **tool skill** ships in
`mascope_ai` (`SKILL.md`) and documents the tools; the **domain skill** is
Peaky's and guides the science. A few lines of what each says per family;
neither is written here.

- **T, time series.** Tool skill: a batch's series is one point per sample
  and `timeseries_of` gives it summarised; `diurnal` needs days and refuses
  hours; `stages` is across a batch, `load_peaks_by_stage` within a sample;
  never read a series scan by scan for a batch question. Domain skill: name
  the cadence and the normaliser in every statement of a series; a maximum
  is an event only against a stated baseline; a two-hour batch has no cycle.
- **N and R, normalisation and the source.** Tool skill: call
  `reagent_ions` before normalising and use the normaliser it names; say
  which was used; TIC is the fallback and says so. Domain skill: a reagent
  ion outside the acquired range cannot normalise; a positive uronium window
  starting above m/z 121 has only the trimer; a cv above 0.3 on the reagent
  ion is an unstable source, reported, never explained away; an m/z offset
  is a calibration fault, calibrate before assigning, do not widen the
  tolerance.
- **C, composition.** Tool skill: `formula_facts` for every ratio, DBE or
  class, never arithmetic in prose; `composition` reads the ledger's
  assigned tier by default and says what share of the signal that is. Domain
  skill: O:C, H:C and OSc are the organic aerosol's coordinates (Kroll et al.
  2011; Van Krevelen after Kim et al. 2003); a HOM is a definition with a
  threshold, stated with the number; a formula is not a molecule, and a
  class is a formula's, not a compound's.
- **S, series.** Tool skill: `homologous_series` names the unit and finds
  the ladders; an empty result is evidence, with the tolerance and the count
  tried. Domain skill: CH2 ladders are alkyl chemistry, O and CO2 ladders are
  oxidation, C2H6OSi is siloxane and CF2 is fluorinated contamination; a
  series needs three members; Kendrick after Kendrick 1963 and Hughey et
  al. 2001, the generalised Kendrick analysis with any base after Alton et
  al. 2023.
- **V, covariation.** Tool skill: `covariation` names families; the
  reference-species form answers "what moves with X". Domain skill: a
  family is a shared time behaviour, not a source; a source is the
  analyst's inference and is recorded as inferred; flat traces are not
  families.
- **U, the unassigned.** Tool skill: `unassigned` gives the share and the
  brightest; one peak's identity goes through `query_by_mz` and
  `match_compounds`; the ledger's role (isotope child, reagent, artifact)
  is Mascope's. Domain skill: unexplained signal is a coverage figure, not
  a quality figure; the brightest unassigned peaks are the question to bring
  to the instrument scientist.
- **X, comparison.** Tool skill: `compare` carries `comparable`; repeat it
  in the answer. Domain skill: different reagent chemistries detect
  different compound sets, so a formula seen by both is not thereby the
  same molecule; compare within a chemistry, and across days at the same
  cadence and normaliser.
- **D, decline.** Both skills: a question the data cannot answer gets the
  reason in one sentence and the nearest thing the data can answer; no
  number as a value, and a count or an m/z the reason needs goes in its
  words.

## 6. Order of work

**First, four tools, in this order.**

1. **`load_ledger`.** Nothing in families C, S and U is answerable without
   it, and the engine's result, the thing a scientist most wants to
   interrogate, is invisible to a model today. It is a loader, so it carries
   no rule and needs no decision. Unblocks `composition`, `homologous_series`
   and `unassigned`.
2. **`timeseries_of`, with `background` and `stages` beside it.** Three of
   the six analysis questions become one call, the matrix's analysis column
   is measured again on the same questions, and the family-T bench grows.
   Rests on one batch's `load_peaks` and `batches.ion_series` until
   `mascope_viz` is on PyPI; the view functions are imported from it then.
   Unblocks `compare`.
3. **`reagent_ions`.** The one bridge to `mascope_tools`' reagent knowledge,
   a diagnostic a scientist asks for by name, and the fix for the
   normalisation trap (D-N) a model falls into today. Unblocks `compare`'s
   comparability rule and `instrument_health`.
4. **`formula_facts` and `composition`**, with `formula_metrics` landing in
   `mascope_tools` first. The oxidation-state and class questions are the
   chemistry insight the owner names as missing, and the Van Krevelen is the
   first chart a `chart` tool should draw.

**Then**, as the matrix re-run after each shows where a cheap model still
fails: `unassigned`, `instrument_health`, `homologous_series` (after the
register move of the Kendrick rule), `compare`.

**Deferred, with the decision that gates each:** `episodes` (the baseline
method, MAS-244), `covariation` beyond notebook 08's rule (the method move
from peaky, register row A14, once MAS-244 says it is ported and where;
the bench's covariation questions start with notebook 08's rule and are
recomputed when it moves), `diurnal` and `load_series` (behind
`mascope_viz` phase 1, MAS-225, and its publication to PyPI, 8.17; the
demo has one day of 2025 and two of 2026, so `diurnal`'s one bench
question is the decline D-T), and the multi-day questions themselves
(8.16, the one piece that waits for a multi-day public dataset, 7.4).

**The recipes** (the batch report, the campaign digest) come after the
first four tools exist, in milestone 2, under MAS-662.

## 7. Decisions: calls for the plan owner

Each is a Linear issue labelled *Decision* in P-MAS-21 when filed; none is
decided here. Seven in all.

1. **The episode baseline.** A rolling low percentile over a window, a
   fixed percentile of the batch, or a blank-anchored baseline; the factor
   and the minimum duration. `episodes` waits for it. This call is already
   filed: MAS-244, the open Decision of the Visual digests project on the
   tier-3 charts, asks it together with whether peaky's covariation method
   is ported, and where (viz's data layer cannot import scipy, so a port
   into viz as a view and this note's `mascope_tools.stats` are different
   answers). This note adds its options to MAS-244 and files nothing
   beside it; 8.12 and 8.9's second PR are blocked by MAS-244.
2. **The HOM and oligomer definitions.** The oxygen threshold (six by
   Bianchi et al. 2019 for the ambient-air context) and whether it is a
   property of the chemistry context. For the oligomer flag, two rules:
   peaky's `oligomer_flag`, absolute (carbon 18 to 40, oxygen at least 7,
   assigned tier only, no silicon, fitted on monoterpene systems), or a rule
   relative to the context (carbon at or above twice the context's monomer
   carbon number, oxygen at or above the HOM threshold), which follows the
   chemistry into a chamber of another precursor but has no bench reading
   yet. `formula_metrics` takes them as parameters; its defaults are this
   call.
3. **Assignment runs in the demo.** Whether the demo bundle ships a
   snapshot with assignment runs on the 2025 batches, so the ledger
   questions need no setup step and the demo shows the engine's result, or
   the fixture launches the runs. The plan is the setup step (4.9, 8.1).
   The bundle route exists as MAS-331 ("A demo bundle with a completed
   assignment run and a batch ledger", AI layer project, milestone 3), an
   Improvement in Backlog and not a Decision, so this call is filed as a
   Decision of its own that blocks MAS-331 (a related link gates nothing;
   the repository's rule is that a gate is a `blockedBy`): picked up as
   ordinary work, the bundle route would re-cut the bundle and move the
   goldens before anyone has chosen it.
4. **A multi-day public dataset.** The diurnal, weekly and episode tools
   have no honest bench question on a one-day demo. Whether a second public
   dataset (a few days of one chemistry) is worth publishing for the bench,
   or the diurnal family stays decline-only until a customer dataset can be
   de-identified.
5. **Whether `reagent_ions` recommends.** The tool proposes a normaliser;
   a view holds that the toolkit should report the candidates and leave the
   choice to the skill. The note's position is to recommend with the rule
   stated, since a small model needs the one answer; the owner decides.
6. **A new Linear project, or P-MAS-21.** This note's tools are milestone 1
   of P-MAS-21 as written, and the matrix is their acceptance test, so it
   stays there. A project of its own is warranted only if the `mascope_tools`
   moves (A13, A14) and the `mascope_viz` dependency make the shell project
   carry library work it should not; then "Toolkit science tools" with this
   note as its design is the split. The note's position: P-MAS-21.
7. **`mascope_tools` as a dependency.** Neither `mascope_ai` nor
   `mascope_viz` depends on `mascope_tools` today (their `pyproject.toml`
   name the SDK, pandas, numpy, pyarrow and loguru), and
   [`ai_layer.md`](ai_layer.md) decision 6 kept the in-process library out
   of the toolkit's first release on purpose. Briefs 8.3, 8.4, 8.5, 8.7,
   8.8 and 8.9 import `profiles`, `reagents`, `formula_metrics`,
   `composition.series` and `stats.covariation` from it. The facts: the
   `mascope_tools` distribution requires Python 3.12 and pandas 2.3.1 where
   `mascope_ai` and `mascope_viz` take 3.10, so any form that keeps that
   distribution drops the toolkit's 3.10 and 3.11 users; taking it whole
   also brings scipy, IsoSpecPy, polars, pyteomics and ruptures into the
   install and raises the pyarrow floor from 14 to 25; an extra cannot
   help, since an extra only adds requirements and cannot take those out of
   the base; and the modules are not pure data as they stand, since any
   import under `mascope_tools.composition` runs its `__init__`, which
   imports `finder` and `heuristic_filter` (polars, IsoSpecPy, scipy,
   pyteomics), `reagents` imports `heuristic_filter` itself, `utils`
   imports pyteomics, and the covariation linkage is scipy's. The options:
   (a) take the dependency whole and raise the toolkit's floor to 3.12,
   the price of one copy of every rule; (b) a separate light distribution
   (`mascope-tools-core`, say) holding profiles, reagents, the formula
   utilities, the series functions and the covariation rule, keeping
   pyteomics and IsoSpecPy, which are light (pyteomics is pure Python on
   3.10; IsoSpecPy a cffi wheel on 3.9) and which every reagent ion's m/z
   (`composition_mass`) and the reagent matcher's isotopologue claims
   (`predict_resolved_isotopes`) rest on, so that numpy, pandas, pyteomics
   and IsoSpecPy are what stays and polars, scipy, ruptures, pyarrow, tqdm
   and the SDK are what goes, none of which a light module imports (lazy
   imports in `composition/__init__`, `reagents` without the plausibility
   half of `heuristic_filter`, a numpy-only linkage or scipy as that
   distribution's one optional extra), which is a refactor
   brief of its own (8.15) that 8.3 is
   blocked by, and which the root project, `mascope_reference` and
   `mascope_signal` then depend on through `mascope_tools`; (c) the server
   exposes what the toolkit needs as routes (the profile of a sample, its
   reagent ions, a formula's metrics) and the toolkit reads them through
   the SDK, which keeps the toolkit light and is "Mascope computes" taken
   literally, but puts the covariation rule server-side or nowhere. For
   `mascope_viz` the note's position needs no decision: `compound_class`
   stays in viz, with a parity test against `formula_class`, so
   `visual_digest.md`'s rule that `mascope_viz.data` depends on the SDK,
   pandas, numpy and pyarrow alone holds. The owner decides the toolkit's
   side before 8.3 lands; the note's leaning is (b), with (c) for the
   profile and reagent facts if the server grows the routes first. The
   gate is one brief: 8.3 is the brief that adds the dependency, in the
   form decided, and 8.4, 8.5, 8.7, 8.8's tool PR and 8.9's first PR are
   blocked by 8.3.

## Risks

- **A tool that quietly carries a rule.** `background`'s three methods,
  `stages`' zero-fill, `compare`'s comparability rule and `diurnal`'s
  day-to-night hours are rules in tools, defended here as closed enumerations
  a scientist names. The line is: a rule that changes a number's value lands
  in `mascope_tools`; a rule that selects which computation to run may stay
  in the tool. Review holds each tool to that.
- **The ledger moves under a tool.** A batch being rebuilt is a mixed
  ledger; `load_ledger` lists the runs once, pins both reads to one run,
  and makes the Frame incomplete with a warning of its own when the
  listing shows a run in flight (the SDK only logs). A composition over it
  is still a number; the skill says to read `complete` first.
- **Two aggregation layers.** `timeseries_of` over one batch and over
  `mascope_viz`'s store must give the same numbers; a contract test holds
  them to each other at `cadence="raw"` before `load_series` is served,
  since the view's own cadence rule bins to whole minutes. It can hold only
  because an `ion:` point is read from `batches.ion_series` on both paths,
  not re-summed from `load_peaks`'s ratio-gated lines (section 3).
- **Too many tools.** Fifteen tools beside seventeen is a surface a small
  model may not navigate. The matrix decides: a tool that does not move a
  class's correct rate on two model columns is removed before milestone 3,
  and the first four are the ones to measure.
- **Peaky's methods as targets.** `covariation` and `homologous_series`
  take peaky's methods as references through the register protocol, with
  the bench card as the proof; the fixture's known answers are not peaky's
  output, they are computed from the goldens by the rule as specified.

## Related

- [`ai_layer.md`](ai_layer.md): the toolkit, its tool surface, the
  evaluation fixture.
- [`ai_analyst_shell.md`](ai_analyst_shell.md): milestones 1 and 2, the
  levers (3.2), the model matrix (3.4), Peaky as the shell (2.5).
- [`peaky_mascope_boundary.md`](peaky_mascope_boundary.md): the split, how a
  rule moves, the register (section 8).
- [`visual_digest.md`](visual_digest.md): the scope and store, the views and
  the Figure spec (section 2), the chart catalogue (section 3).
- [`assignment_quality_plan.md`](assignment_quality_plan.md) and
  [`chemistry_profiles.md`](chemistry_profiles.md): the reagent profiles and
  chemistry contexts the tools read.
- [`docs/user/concepts/index.md`](../user/concepts/index.md) and
  [`docs/user/how-it-works/`](../user/how-it-works/index.md): the domain
  model; "The ions a source makes of itself" in
  [peak-assignment.md](../user/how-it-works/peak-assignment.md).
- [`libraries/ai/README.md`](../../libraries/ai/README.md) and
  [`libraries/ai/src/mascope_ai/SKILL.md`](../../libraries/ai/src/mascope_ai/SKILL.md):
  the toolkit as shipped.
- The SDK example notebooks in
  [`libraries/sdk/src/mascope_sdk/examples/`](../../libraries/sdk/src/mascope_sdk/examples/):
  the hand-made analyses each tool replaces.

## 8. Proposed issues

Self-contained briefs for P-MAS-21, milestone 1 unless said otherwise, in
the order of section 6: sixteen briefs (8.1 to 8.13, and 8.15 to 8.17) and
the decisions (8.14). None is filed; none carries the `agent-ready` label. A
prerequisite named in a brief's "Blocked by" is a `blockedBy` link when the
issues are filed, never a sentence alone. Every tool brief includes the
same deliverables: the toolkit method; its MCP tool with a description
tuned on the fixture's misses; its name added to `ai_golden.py`'s
`SURFACE` (a solver that calls a tool off it fails
`test_solvers_call_only_the_tool_surface`); its bench questions with
`dryrun` passing and the question list in `ai_layer.md` regenerated from
`ai_golden.py list` (`test_the_note_lists_them` fails when the two
differ); the README table and `SKILL.md` lines; the changelog fragment
`changelog.d/<branch>.added.md`. A brief that adds questions without a tool
(8.16) owes the list the same.

### 8.1 `load_ledger`: the batch ledger as a Frame

**Context.** The toolkit loads peaks, time series, spectra and stages, but
not the assignment ledger: the formulas, tiers and roles Mascope's engine
gave every peak. Every composition, series and unassigned question
(`docs/dev/ai_toolkit_science.md` section 2) is unanswerable without it.
The SDK has `batch_peaks.series` (one compact read per batch) and
`load_assignments` (per sample).

**Task.** In `libraries/ai/src/mascope_ai/toolkit.py`, `load_ledger(dataset=,
batches=, *, tier=None, min_n_present=2, members=False,
max_rows=DEFAULT_MAX_ROWS) -> Frame`: one row per (sample, batch peak)
from two reads per batch, `batch_peaks.series` (the member's ids,
`batch_mz`, `consensus_formula`, `consensus_ion_formula`, `consensus_tier`,
`intensity`, `tier`; `SERIES_COLUMNS` in the SDK, which has no role) and
`batch_peaks.list` (the species table, whose `consensus_role` and
`isotopologue_of` are joined on `batch_peak_id`), both pinned to the one
`run_id` a single `batch_peaks.runs` listing names (as `mascope_viz`'s
store does), a run the listing shows running making the Frame incomplete
with the loader's own warning, plus the sample's `sample_item_name` and
`datetime_utc` from the samples listing; with `members=True` a third, paged
read, `batch_peaks.members`, adds the member's own `role`, and since that
read takes no `run_id` and reads the live ledger, the runs are listed again
after it and the Frame is made incomplete if the current run moved. A batch
with no
ledger is refused with `InvalidRequest`, naming how a ledger is built (a
batch assignment run in the app, or `peak_assignments.assign` on its
samples); `load_assignments` is no fallback, since its rows carry no batch
peak. Row cap, completeness and failures as `load_peaks` (`_RowCap`,
`_scope`); once #2991 has merged, the new columns get their lines in
`mascope_ai/columns.py` (its rule: no loader column without a meaning), in
this PR if it lands after, in a follow-up commit if before. The `formula:`
key of section 3's grammar is wired by whichever of 8.1 and 8.2 lands
second: if 8.2 is in, this PR makes `views.series_key()` read a `formula:`
key off this Frame with the isotopologue rows (`isotopologue_of` set)
dropped and removes the refusal 8.2 shipped; if not, 8.2 does it when it
lands. Register it in `mcp.py` `TOOLS` with a `_FRAME` description; add it
to `ai_golden.py` `SURFACE`; fake routes in `tests/conftest.py` `FakeAPI`
for the runs, series, list and members routes and a ledger per demo batch,
one anchor of it a `reagent` role in the unassigned tier and one an
isotopologue of another. Bench: U1 with a solver over `load_ledger` and
`derive` (the ledger's tiers by summed intensity, the source's anchors
apart), which 8.6 moves onto `unassigned` as A1's moved onto `reduce`.

**Where.** `libraries/ai/src/mascope_ai/toolkit.py`, `mcp.py`,
`tests/conftest.py`, `tests/test_ai_loaders.py`, `tests/test_ai_mcp.py`,
`tests/ai_golden.py` (U1, with the setup step of 4.9 in
`test_ai_golden.py`'s contract fixture), `libraries/ai/README.md`,
`SKILL.md`.

**Verify.** `uv run pytest libraries/ai/tests/` hermetic; the contract run
on a demo stack with assignment on loads both 2025 batches' ledgers whole;
`ai_golden.py dryrun` passes U1; `ai_golden.py list` regenerated into
`ai_layer.md`.

**Size.** M.

### 8.2 `timeseries_of`, `background`, `stages`: the time-series tools

**Context.** A2 and A3 are pandas the model writes over `load_peaks` (A1 solves through `reduce` since #2983), and
the model matrix shows a cheap model running out the clock on them. Three
coarse tools give the series summarised, the background and the stage means
in one call each (`ai_toolkit_science.md` 3.2); notebooks 02, 07 and 09
are the prototypes.

**Task.** In `toolkit.py`: `timeseries_of(scope, species, *,
normalise="none", cadence="raw")` returning the summary dict (`n_points`,
`max`, `max_at`, `min`, `mean`, `median`, `p10`, `p90`, `first`, `last`,
`normaliser`, `cadence`, `found_fraction`, and `refs` and `frame` as every
tool's dict) and keeping the per-sample series as a derived Frame;
`background(scope, species, *, method, n=5, sample=None, percentile=10)`
with the three closed methods and the fraction of points above the
background it took; `stages(batch, stages, *, species=None, normalise,
reference=None)` with zero-fill before averaging, a NaN fold and a
`new_in_stage` list for a species absent from the reference stage, never
infinity. Species and `normalise` take the grammar of section 3
(`isotope:`, `ion:`, `compound:`, `formula:<formula>|<mechanism>`; `none`,
`tic` or one of those) and no `level=`: a point is what the key says, an
`isotope:` point is read per line off `load_peaks` (one row per line and
sample before any sum), an `ion:` point off `batches.ion_series` (one
request per batch, the match's stored sum, the chart's number; a re-sum of
`load_peaks` leaves out ratio-gated lines), a `compound:` point as the sum
of its ions' points, an ion formula with two target ions is refused with
the two `compound:` keys, and `tic` is the samples listing's mean TIC per
MS1 scan. A `formula:` key is refused with a message naming `load_ledger`
until 8.1 is in; whichever of 8.1 and this brief lands second wires it, so
neither is blocked by the other. The translation to `mascope_viz`'s `ion:<target_ion_id>`
and `formula:<formula>|<mechanism>` keys is one function, `series_key()`,
in the new `mascope_ai/views.py`, with the isotope form refused for a store
scope. A normaliser must be found in every sample or the tool refuses with
the samples it is missing from. Aggregation code written as pure functions
in `views.py`, shaped to `mascope_viz`'s `series` view (the same `keys`,
`cadence`, `normalise`, `stat` arguments; `cadence="raw"` the default over
one batch) so the import swaps in later. `_result` in `toolkit.py` learns
to record a dict's `refs` and `frame`, so a tool that loads inside itself
leaves an answer that names its objects. MCP tools, enumerations in their
schemas; `mcp.py`'s server instructions and `SKILL.md`'s analysis
paragraph rewritten from "load a Frame, then compute in Python" to "ask
the coarse tool; Python after it declined". In `ai_golden.py`: a `family`
field on `Question`, printed by `list`, and `test_ai_golden.py`'s category
and ID tests extended to it (the categories stay four, the IDs their
initials), with `test_each_declines_with_a_reason_and_no_value` turned
from the list D1 to D5 into a rule over every `D` ID in order, so 8.3's
D-N and D-R and every later decline pass it unchanged; the questions filed
as A7 and onward with their family tags.
Bench: T1, T2, N2, D-T's data (the refusal is `diurnal`'s, later), A2 and
A3 kept with their `derive` solvers and A1 with its `reduce` one.

**Where.** `libraries/ai/src/mascope_ai/toolkit.py`, `views.py` (new),
`mcp.py` (the tools and the instructions), `SKILL.md`, `tests/conftest.py`
(`FakeAPI` gains the ion series route, `match/records/ion/series`, which it
does not serve today, with a series per demo batch; `recorded_api.json` has
none), `tests/test_ai_compute.py`, `tests/test_ai_mcp.py`,
`tests/ai_golden.py`, `tests/test_ai_golden.py`, `README.md`.

**Verify.** Hermetic suite, with one line of the fake's ion series missing
its ratio (unmatched in `load_peaks`, counted in the stored sum), so a
re-sum of the peak list fails the test and section 3's rule is pinned; the
known answers of T1, T2 and N2 held to the goldens in `test_ai_golden.py`
(`golden=` callables, each by its `isotope:` key); `dryrun` fails every
fabrication; the contract run answers
them on the demo stack; the matrix row re-run on the analysis class is the
acceptance (`golden_transcripts/`).

**Size.** L.

### 8.3 `reagent_ions`: the reagent chemistry, served

**Context.** `mascope_tools.composition.profiles` names a sample's reagent
profile from its mechanisms and `composition.reagents` names the ions a
source makes of itself with their citations, but no tool serves them: a
model normalises to an ion it remembers, and on the demo's 122-750 uronium
window it reaches for protonated urea at m/z 61, which is not acquired
(`ai_toolkit_science.md` 3.3, D-N).

**Task.** In `toolkit.py`: `reagent_ions(scope) -> dict` with the profile
(`detect_reagent_profile(mechanisms, polarity=, instrument_type=)` on
`describe`'s mechanisms, `polarity` and `instrument_type`, since charge
transfer is read only on an Orbitrap and ESI only from the polarity; a
batch scope describes its samples and reports the modes and windows it
finds, the intersection as the batch's), the reagent's and the air's ions
from `composition.reagents` each flagged inside or outside the scan window
(the sample view's `mz_min` and `mz_max`, which `describe` gains) and
beyond the recorded axis (`describe`'s `mz_range`, the file's axis ends,
tens of m/z inside the window on the demo), for the in-window ones their
median height, presence fraction and cv over the scope's samples from
`load_peaks`, matched through `composition.reagents.match_reagent_clusters`
(a reagent ladder does not fit one ppm window) or, where a ledger exists,
through the engine's `consensus_role` `reagent` anchors, the
secondary-channel fingerprints present or absent, and
`recommended_normaliser` as a key of section 3's grammar that
`timeseries_of` can read (the brightest in-window reagent line a target
matches in every sample, else `tic`) with the rule stated in the dict.
Over MCP the dict leads with the in-window ions and
the recommendation, carries citations as short keys into the user page's
reference list, and counts what the bound left out, so the Br2- and Br3-
lines R1 asks for survive `fit`. MCP tool; bench N1, R1, D-N, D-R.
`tests/conftest.py`'s fake uronium mode carries the mode's three
mechanisms (`[M+H]+`, `[M+NH4]+`, `[M+CH4N2O+H]+`), since `[M+H]+` alone
detects as ESI, and its window 122-750.

**Where.** `toolkit.py` (and `describe`, which gains `mz_min`, `mz_max`
and the calibration quality block), `mcp.py`, `tests/conftest.py`
(mechanisms, windows and axis ends on the fake's samples),
`tests/test_ai_compute.py`, `tests/ai_golden.py`, `README.md`, `SKILL.md`;
`libraries/ai/pyproject.toml`, which gains the dependency decision 7.7
settles (the whole `mascope_tools`, the light distribution of 8.15, or
none because the server grew the routes), with the install's new weight
and Python floor recorded in the PR; `libraries/tools` unchanged unless a
lookup needs a public function, then `composition/reagents.py` gains it
with a test.

**Verify.** Hermetic suite; on the demo stack the bromide batch's
recommendation is `isotope:[81Br]Br2-` and the uronium batch's is `tic`,
with the trimer named as in the window, unmatched by any target and found
in one of 81 files; `[NO2+2xH2O]-` comes back inside the window and beyond
every axis; `dryrun` fails a value on D-N and D-R.

**Size.** M. Blocked by 8.2 (the `_result` change its dict relies on and
the `family` field its questions carry), by decision 7.7 (the dependency's
form) and, under option (b), by 8.15. Decision 7.5 (whether the tool recommends) is read
before the recommendation field is final; the field ships either way, as
the recommendation or as the ranked candidates. This is the one brief that
adds the dependency: every later brief that imports `mascope_tools` is
blocked by it.

### 8.4 `formula_metrics` in `mascope_tools`, and `formula_facts`

**Context.** O:C, H:C, OSc, DBE, class, Kendrick mass and the HOM and
oligomer flags are computed in peaky's `chemistry.py`, in `mascope_viz`'s
species table and in the model's head. One function per rule, in
`mascope_tools`, with the toolkit, the digests and the engine as callers
(`peaky_mascope_boundary.md` section 3; `ai_toolkit_science.md` 3.4).

**Task.** In `libraries/tools/src/mascope_tools/composition/utils.py` (or a
new `metrics.py`): `formula_metrics(formula, *, context=None) -> dict`
with element counts (caret isotopes folded into their element), DBE by
`heuristic_filter.effective_counts` (public already; the engine's
convention, which covers Si and P where `finder.get_unsaturation` does
not), O/C, H/C,
N/C, OSc (2 O/C - H/C), `formula_class`, `is_hom` and `is_oligomer` under
the context's thresholds (parameters with the ambient-air defaults of
decision 7.2; documented as provisional until decided), and the context's
two verdicts apart: `ratios_ok` by `heuristic_filter._context_ratios_ok`
made public (effective counts, the windows from three carbons up), and
`within_caps` by the context's `element_caps` applied to the neutral (the
caps the engine's search grid is narrowed by). No exact, nominal or
Kendrick mass of the formula (`ai_layer.md` 3.3).
`formula_class(formula, *, backbone=False) -> str` copies
`mascope_viz.data.store.compound_class`'s rule over folded counts (CHO,
CHON, CHOS, CHONS, CH, the `X` suffix, `inorganic`); `backbone=True` is
peaky's `backbone()` exactly (CHOS if S, else CHON if N, else CHO; Si, F
and halogens folded); `compound_class` stays in `mascope_viz` (its data
layer takes no `mascope_tools` dependency, `visual_digest.md` section 2.1)
and a parity test in `libraries/viz/tests` holds the two to one answer over
the species table's formulas and a 15N formula (`C10H15^NO3` is CHON in
both). `composition/series.py` gains the one Kendrick function here,
`kendrick(mz, unit, *, convention="km_minus_round", scale=None)`, with the
sign convention and peaky's `X` as parameters, which 8.8 reuses rather
than writing a second. `neutral_formula(ion_formula, mechanism) -> str`,
the inverse of `combine_formula_and_ionization`, is lifted into
`composition.utils` from the backend's `ionization_catalogue._neutral`
("the inverse of the target ion generator", tested in
`test_seeded_collections.py` and copied in `tooling/score_eval`), with the
backend and the tooling switched to the library's copy and a round-trip
test (`C10H17O2+` with `[M+H]+` back to `C10H16O2`, `CH2BrO2-` with
`[M+Br]-` back to `CH2O2`). Then in `mascope_ai`: `formula_facts(formula,
*, mechanism=None, mz=None, unit="CH2", context="ambient-air") -> dict`
serving the metrics, the context's verdict (the context looked up by
`get_chemistry_context`, so its names and aliases are the library's:
`ambient-air`, `ambient`, `atmospheric`) and, only with a measured `mz`,
its Kendrick mass and defect; an ion formula given with its `mechanism` is
reduced to the neutral through `parse_ionization` and `neutral_formula`.
MCP tool; bench C2 and S2, and C2's sloppy check (no `formula_facts` op
among those the harness hands the check).

**Where.** `libraries/tools/src/mascope_tools/composition/` (`utils.py` or
`metrics.py`, `heuristic_filter.py` for `_context_ratios_ok` made public,
`series.py` new), its tests; `server/backend/src/mascope_backend/ionization_catalogue.py`
and `tooling/score_eval` (switched to the library's `neutral_formula`);
`libraries/viz/tests` (the parity test; `mascope_viz/data` itself
unchanged); `libraries/ai/src/mascope_ai/toolkit.py`, `mcp.py`, tests,
`ai_golden.py`, `README.md`, `SKILL.md`; the literature references (Kroll
et al. 2011; Kim et al. 2003; Kendrick 1963; Hughey et al. 2001; Alton et
al. 2023; Bianchi et al. 2019) in the function's docstring and, when the
first user-facing page describes the toolkit's composition tools, mirrored
there.

**Verify.** `mascope test run libraries`; fixed-value tests (C10H16O2:
DBE 3, O/C 0.2, H/C 1.6, OSc -1.2, CHO, `ratios_ok` and `within_caps` both
true; C10H16N4O4 under ambient air `ratios_ok` true and `within_caps`
false, the nitrogen cap being 3; a siloxane's DBE non-negative; the two
round trips; the Kendrick convention both ways); the parity test of
`compound_class` against `formula_class`; the backend's seeded-collection
tests unchanged; the hermetic `ai` suite; `dryrun` on C2 and S2, with
C2's sloppy row.

**Size.** M. Blocked by 8.3 (the dependency) and 8.13 (the sloppy status
C2's check returns).

### 8.5 `composition`: the ledger's chemistry in one call

**Context.** With `load_ledger` (8.1) and `formula_metrics` (8.4) the
composition of a batch is a groupby; the tool gives it as the closed dict of
`ai_toolkit_science.md` 3.4 and the per-species Frame a Van Krevelen is
drawn from. Peaky's `composition.py` is the reference.

**Task.** In `toolkit.py`: `composition(scope, *, tier="assigned",
weight="intensity") -> dict` (per class: distinct neutrals and intensity
share; weighted mean and quartiles of O/C, H/C, OSc; carbon-number
distribution as a small histogram; HOM count and share; oligomer
candidates; the tier's share of total signal) plus the per-species derived
Frame (formula, class, metrics, summed intensity, presence), the
isotopologue rows (`isotopologue_of` set) dropped first so no formula's
13C or 81Br line counts twice. Positive-mode ammonium-amine degeneracy
reported as a count, from peaky's `composition.py` rule, as a disclosed
two-way. MCP tool; bench C1, D-C.

**Where.** `toolkit.py`, `mcp.py`, tests, `ai_golden.py` (C1 and D-C as
*ledger* questions with the setup step), `README.md`, `SKILL.md`.

**Verify.** Hermetic suite over a fake ledger with known composition and an
isotopologue anchor that must not count; the contract run on the demo stack
with assignment on; `dryrun`.

**Size.** M. Blocked by 8.1, 8.3 and 8.4.

### 8.6 `unassigned`: what the engine did not explain

**Context.** A scientist's first question of an assignment is how much is
unexplained and what the brightest unexplained peaks are; the ledger has
the tiers and roles, and no tool reads them (`ai_toolkit_science.md` 3.5).

**Task.** In `toolkit.py`: `unassigned(scope, *, top=20, sample=None) ->
dict` with per tier the count and intensity share, the source's own anchors
(`consensus_role` `reagent` or `artifact`, whatever their tier) counted on
a line of their own and never as unexplained, the `top` batch peaks of tier
`unassigned` with a null role and of tier `below_assignability` by median
intensity over the batch or in the one `sample` given (`batch_mz`,
presence), a sentence that Mascope makes no isotope reading of an unclaimed
unassigned peak (`isotopologue_of` is null for it by construction), and
the follow-up line naming `query_by_mz` and `match_compounds`. MCP tool;
bench U1, U2, D-U; `SKILL.md`'s U lines.

**Where.** `toolkit.py`, `mcp.py`, tests, `ai_golden.py`, `README.md`,
`SKILL.md`.

**Verify.** Hermetic suite over a fake ledger whose brightest unassigned
anchor is a `reagent` role (it must not be named); contract run; `dryrun`
fails a value on D-U (a decline with `_no_value`; a "no" or an isotope
verdict written into the reason is a human verdict); U1's solver moved
from `derive` onto `unassigned`.

**Size.** S. Blocked by 8.1 and 8.2.

### 8.7 `instrument_health`: TIC, reagent ions, calibration, mass accuracy

**Context.** Whether the source was stable and the mass axis right is a
question before every other; `describe` has a sample's TIC and calibration
status one at a time, `reagent_ions` (8.3) has the source's ions, and the
mass error is A6's per-ion pandas (`ai_toolkit_science.md` 3.3).

**Task.** In `toolkit.py`: `instrument_health(scope) -> dict`: TIC median,
spread and tercile drift (the samples listing's `tic`); the reagent ions'
from `reagent_ions`; calibration status and `verified` counts over the
samples; the acquisition's offset as each sample's
`quality.pre_fit_mz_error_ppm` from its calibration record (which
`describe` gains in 8.3, beside `status` and `verified`), summarised with
its tercile trend and the count above Mascope's 10 ppm Orbitrap warning;
and the assigned ions' residual from each sample's latest completed peak
assignment run, `mu_ppm` and `sigma_ppm` through
`peak_assignments.list_runs` (which leaves the decoy runs out by default;
the fields are null on a batch run) and the rows' `mz_error_ppm` through
`load_assignments`, per sample and over the scope: the batch ledger carries
neither, so this tool does not read `load_ledger`. No theoretical m/z is
computed and nothing of `composition.mass_accuracy`
is called: the stored m/z are post-calibration, so a calibrant ion's
residual is about 0 ppm by construction and is reported as the fit's
residual, never as the offset. The dict reports an offset and never a
corrected value; its `notes` carry the calibrate-first sentence. MCP tool;
bench R2, and D5 stays the saturation decline.

**Where.** `toolkit.py`, `mcp.py`, `tests/conftest.py` (the fake samples
gain a calibration quality block, and `FakeAPI` gains two routes nothing in
`mascope_ai` reads today: `peak-assignments/sample/<id>/runs`, with
`mu_ppm` and `sigma_ppm` on the sample runs and one `batch` run carrying
nulls, and `peak-assignments/sample/<id>`, the rows with `mz_error_ppm`;
8.1's fake routes are the ledger's and do not cover these), the tests,
`ai_golden.py`, `README.md`, `SKILL.md`.

**Verify.** Hermetic suite with a fake whose third tercile's
`pre_fit_mz_error_ppm` is offset, whose sample runs carry `mu_ppm` and
`sigma_ppm` and whose batch run's nulls are left out; contract run;
`dryrun`; R2's sloppy check (a tolerance recommendation in the answer's
text).

**Size.** M. Blocked by 8.3 and 8.13 (not by 8.1: it reads the samples'
runs, not the ledger).

### 8.8 Kendrick and series detection into `mascope_tools`, then `homologous_series`

**Context.** Peaky's `series_gka.py` (Kendrick arithmetic after Alton et
al. 2023, repeat units, and `find_homolog_series`, which lists the ladders
of a set of formulas with their members) and `series_detect.py`
(`detect_series`, a decoy-controlled enrichment test per repeat unit over
the m/z of the unexplained and low-confidence peaks, returning the unit's
link and chain counts, the decoy mean, the enrichment and its
significance, `min_links=12`; and `unit_chains`, the maximal chains of
those same peaks spaced by the unit's mass, its member listing) are two
rules Mascope lacks, answering two questions. The boundary protocol moves each as a specification with its
bench reading, reimplemented in `mascope_tools`, peaky's copy behind a
switch for one release (`peaky_mascope_boundary.md` section 8). Register
rows A13 (the ladders) and A13b (the enrichment test).

**Task.** Peaky's owner writes the two specifications (the ladder walk over
exact formulas and its minimum length; the GKA tolerance rule `dGKA = (X /
mass(R)) * dm`, the decoy control, `min_links`, `min_enrichment`, and the
inclusion of the low-confidence M0 peaks) with a bench reading on the
corpus batches; Mascope adds to `composition/series.py` (whose `kendrick`
8.4 landed) `repeat_units`, `find_homolog_series(formula_mz, unit,
min_length) -> list[Series]` (no tolerance: it walks exact formulas) and
`series_enrichment(mz, unit, tolerance_ppm, min_links, min_enrichment,
include_low_confidence=True) -> Enrichment` (its chains after
`unit_chains`), tests on peaky's documented ground truths (the NBBS urea
ladder among them, as formulas, no customer data). Then
`homologous_series(scope, *, unit, tier, min_length)` in `toolkit.py` over
`load_ledger` with the isotopologue rows dropped, ladders for the assigned
tier and the enrichment test for `tier="unassigned"`, its MCP tool, bench
S1, D-S (S2 is 8.4's). Register rows A13 and A13b added to the boundary
note with the parity numbers when peaky flips its defaults.

**Where.** `libraries/tools/src/mascope_tools/composition/series.py` (new)
and tests; `docs/dev/peaky_mascope_boundary.md` section 8 (the row);
`libraries/ai/...` as the other tools; a PKY issue for the specification and
the switch, filed by peaky's owner.

**Verify.** `mascope test run libraries`; the bench card "same output
expected" against peaky's `find_homolog_series` for the ladders and
`detect_series` for the enrichment test on the training batches, run by
peaky's owner; the hermetic `ai` suite; `dryrun` on S1 and D-S.

**Size.** L, in two PRs (the library, then the tool). The library PR is
blocked by the two specifications (PKY issues) and 8.4 (`kendrick`); the
tool PR by the library PR, 8.1 and 8.3.

### 8.9 `covariation`: families of covarying series

**Context.** Notebook 08 and peaky's `batch/cluster.py` find the species
that move together by two different methods. Notebook 08: Pearson on raw
heights, distance `1 - |r|`, average linkage, a flat cut at `r` 0.7, no
flatness gate. Peaky: correlation of log10 intensities, a signed distance,
complete linkage at `r` above 0.6, a flatness gate that keeps a trace only
if its cv reaches 0.30 or its smoothed maximum over median reaches 1.7
(`trace_varies`), then a merge of near-identical families and a demotion of
flat clusters. `visual_digest.md` lists the chart as tier 3 with peaky's
method as the reference, and whether that method is ported, and where, is
the open Decision MAS-244. The tool ships with notebook 08's rule and
serves the families and the reference-species form; peaky's method moves
by the register (row A14) as the second PR once MAS-244 says so
(`ai_toolkit_science.md` 3.5).

**Task.** `mascope_tools/stats/covariation.py` (a new module beside
`stats/peak.py`): `correlate(matrix, log=False)`, `cluster(corr, r_min=0.7,
linkage="average", signed=False)` as pure functions with notebook 08's
values as the defaults of the first PR, and no flatness function yet; the
second PR adds peaky's options (`log=True`, `signed=True`,
`linkage="complete"`, `r_min=0.6`, `varies(matrix, cv_min=0.30,
peak_range=1.7)`, the merge and the demotion) under its specification and
flips the defaults when the bench card says so. Then `covariation(scope, *,
species=None, top=100, normalise, cadence="raw", r_min=0.7)` in
`toolkit.py` over `load_peaks` (one batch) or `load_series`, with the m/z
binning of notebook 08 for untargeted peaks and the asked species' own
lines and its compound's other ions left out of the reference form; the
dict never names a source. MCP tool; bench V1 and V2 (their known answers
recomputed and recorded again when the defaults flip, and V2's flat count
added then), D-V as a decline.

**Where.** `libraries/tools/src/mascope_tools/stats/covariation.py` and
tests; `libraries/ai/...`; `docs/dev/peaky_mascope_boundary.md` (row A14,
with the second PR); `docs/dev/visual_digest.md` open question 5 is
MAS-244's to answer, and this brief writes nothing there.

**Verify.** Library tests on a synthetic matrix with planted families; the
hermetic `ai` suite; contract run; `dryrun`.

**Size.** L. The first PR ships notebook 08's rule and is blocked by 8.2
(the series it correlates) and 8.3 (the dependency); the register move is
a second PR, blocked by MAS-244 and peaky's specification.

### 8.10 `compare`: one species in two scopes, with comparability

**Context.** Comparisons across days, batches and chemistries are pandas
over two loads, and the rule that two reagent chemistries do not measure
the same molecules lives only in peaky's gotchas (`ai_toolkit_science.md`
3.6).

**Task.** `compare(scope_a, scope_b, species, *, normalise, stat)
-> dict` over `timeseries_of` twice and `reagent_ions` for both profiles;
`comparable: false` with the reason when the profiles or polarities differ,
the ratio withheld then and the two summaries kept under their scopes. MCP
tool; bench X1, D-X (a decline).

**Where.** `toolkit.py`, `mcp.py`, tests, `ai_golden.py`, `README.md`,
`SKILL.md`.

**Verify.** Hermetic suite; contract run; `dryrun` fails a factor on D-X (a
"higher" in the reason's words is a human verdict).

**Size.** S. Blocked by 8.2 and 8.3.

### 8.11 `diurnal` and `load_series`: the campaign scope (deferred)

**Context.** The hour-of-day profile and every multi-day question need a
scope of several batches through `mascope_viz`'s store and views, which is
not yet released, and a public multi-day dataset for the bench, which does
not exist (decision 7.4). On the demo the diurnal question is the decline
D-T.

**Task.** When `mascope_viz` phase 1 is done and the package is on PyPI
(8.17): `load_series(scope, *,
keys, cadence, normalise, stat)` over the store and the `series` view;
`diurnal(scope, species, *, tz, by)` over the `diurnal` view with the
closed day-to-night definition, refusing a scope under two days with the
span named; `timeseries_of` switched to the view functions on a multi-batch
scope. Bench: D-T (it needs nothing but the refusal). The T-family
multi-day questions are not this brief's: they are 8.16, behind decision
7.4, so this brief is not.

**Where.** `libraries/ai/...`; `libraries/viz` as the dependency in
`libraries/ai/pyproject.toml`.

**Verify.** The one-batch contract: `timeseries_of` over one batch
(`load_peaks` and `batches.ion_series`) and over the store agree on the
demo batches to the goldens' tolerance at `cadence="raw"`; `dryrun` on D-T;
`libraries/ai/pyproject.toml`'s new `mascope_viz` dependency installs from
PyPI in a clean environment (8.17 published it).

**Size.** M. Blocked by MAS-225 (the last of `mascope_viz` phase 1,
MAS-222 to MAS-225), by 8.17 (`mascope_viz` on PyPI, which `mascope_ai`'s
own PyPI install needs before it can depend on it) and by 8.2 (it changes
`timeseries_of`, ships a dict tool through 8.2's `_result`, and files a
decline under 8.2's rule).

### 8.12 `episodes`: events above a baseline (deferred)

**Context.** When a species exceeded its baseline, by how much and for how
long is the event question of family T; `visual_digest.md` lists the chart
as tier 3, "method to choose and state", the Decision MAS-244 asks the
method, and `background` with `method="first_n"` or `method="sample"`
reads the demo's single event meanwhile (a percentile does not: the plume
holds 75 of the uronium batch's 81 samples; `ai_toolkit_science.md` 3.2).
The brief exists so that MAS-244 has an issue to block.

**Task.** Once MAS-244 is decided: `episodes(scope, species, *, baseline,
window, percentile, factor, min_duration) -> dict + Frame` in `toolkit.py`
over `timeseries_of`, the baseline as decided, exceedances above `factor`
times it merged into episodes with start, end, peak, peak time and fold;
the episode table as the derived Frame, which `chart` shades on
`species_series`. MCP tool; bench: an event question on the uronium batch
(its one plume: start, peak at 14:34:03, fold above the baseline) and a
decline on the bromide batch if it holds no exceedance.

**Where.** `toolkit.py`, `mcp.py`, tests, `ai_golden.py`, `README.md`,
`SKILL.md`.

**Verify.** Hermetic suite with a planted event; contract run; `dryrun`.

**Size.** M. Blocked by MAS-244 and by 8.2.

### 8.13 `sloppy` as a verdict the fixture can give

**Context.** The model-run vocabulary has correct, wrong, missing and
sloppy (`golden_transcripts/README.md`), but the fixture's `run_question`
scores every `check=` failure as `wrong`, and a model run is manual with no
driver in the repository. Two new traps (C2, R2) are instruction-following
checks that must score as sloppy, not wrong, or the fabrication measure is
polluted (`ai_toolkit_science.md` 4.9). Two things the fixture's code does
not give a check today: the tools the solver called (`run_question` maps
the record's calls to ops into `Outcome.tools` before the check runs, and
only does not hand them over; a dict-returning tool leaves no op among the
record's values), and the solver's `inferred` statements (`run_question`
passes none).

**Task.** In `libraries/ai/tests/ai_golden.py`: a check receives a
`Checked(answer, record, tools, inferred)` in place of `(answer, record)`
(the existing checks updated), and may return problems tagged as sloppy (a
small `Problem(kind, text)` beside the plain strings `compare` returns);
`run_question` gives the answer the status `sloppy` when every problem is
of that kind and the value compared correct, and `report` counts it apart.
`dryrun` gains, for every question whose check can return the kind, a
scripted "right value, wrong procedure" answer **with a scripted record
and tools**, since `check_answer` builds the record from the answer alone
today (`_record_for`, which holds only `values.unscored`) and the same
record for the known answer and the scripted one would make them
indistinguishable. This brief lands before any question with such a check
exists, so it is verified on a question of its own: `test_ai_golden.py`
defines one hermetic question whose check requires a `derive` op among the
tools, and asserts the three outcomes on it (correct, sloppy, wrong).
`golden_transcripts/README.md` says the status comes from the fixture for
the questions that carry such a check and stays a human verdict for the
rest; `ai_layer.md`'s fixture section records the fourth status.
**Optional, in the same brief if cheap:** a `forbids` phrase list on
`Words`, so a decline's reason that names a forbidden phrase fails as
wrong; `compare` already calls `Words.matches`, so it changes no pin (the
declines' test asserts only that the reason is a `Words` and the check
`_no_value`). It is kept to D-V's source names: a substring list cannot
tell D-U's, D-T's or D-X's verdicts from their right reasons ("no" sits
inside "not available", D-U's right reason names "isotope lines", D-T's
may say "no cycle", D-X's "whether it was higher"), so those three stay
human verdicts.

**Where.** `libraries/ai/tests/ai_golden.py`, `test_ai_golden.py`,
`golden_transcripts/README.md`, `docs/dev/ai_layer.md`.

**Verify.** `uv run pytest libraries/ai/tests/test_ai_golden.py` hermetic,
with the scripted question's three outcomes and the existing
`test_the_checker_fails_what_it_should` still green; `ai_golden.py dryrun`
shows no sloppy row until 8.4 and 8.7 add C2 and R2, then one each.

**Size.** S. Blocked by nothing; 8.4 and 8.7 are blocked by it, and each
shows its sloppy row in `dryrun` when it lands.

### 8.14 Decisions to file (labelled *Decision*)

One of the seven calls is an open Decision already, and this note attaches
to it rather than filing beside it: 7.1 is MAS-244 (Visual digests: the
episode baseline and whether peaky's covariation is ported, and where; it
blocks 8.12 and 8.9's second PR). One issue each for the rest: 7.2 (the
HOM and oligomer definitions; it blocks nothing, since 8.4 ships its
defaults as provisional and the decision moves them when it comes), 7.3
(assignment runs in the demo: the fixture's setup step, which is the plan
in 4.9 and 8.1, or the bundle route, which is the Improvement MAS-331 in
the AI layer project's milestone 3; it blocks MAS-331, across the two
projects, since a related link gates nothing and that `blockedBy` is what
keeps the bundle from being re-cut as ordinary work before the owner
chooses), 7.4 (a multi-day public dataset; blocks 8.16, the multi-day
questions, which are their own issue so that 8.11 is not gated), 7.5 (whether
`reagent_ions` recommends; read by 8.3),
7.6 (a project of its own, or P-MAS-21; it blocks nothing, and is filed so
the owner's answer is on record where the briefs are) and 7.7
(`mascope_tools` as a dependency of `mascope_ai`: whole with a 3.12 floor,
a separate light distribution, or server routes; blocks 8.3, the one brief
that takes the dependency). Each opens with the question, the options as
this note states them, the issue it blocks and the `blockedBy` link from
that issue. Beside the Decisions, one *Needs input* issue is filed with
8.17 for the owner's two steps (the `.github/workflows` commit and the
pending trusted publisher on PyPI), which 8.17 is blocked by, as MAS-613
and MAS-615 gated the toolkit's publish.

### 8.15 A light `mascope_tools` distribution (under decision 7.7, option b)

**Context.** The rules the toolkit needs from `mascope_tools` (the
profiles and chemistry contexts, the reagent library and its probes, the
formula utilities and metrics, the series functions, the covariation rule)
are small, but the distribution requires Python 3.12 and brings scipy,
IsoSpecPy, polars, pyteomics and ruptures, and every import under
`composition` runs an `__init__` that imports the heavy modules
(`ai_toolkit_science.md` 7.7). A toolkit on Python 3.10 with a light
install needs those cut.

**Task.** A second distribution built from the same tree
(`libraries/tools/pyproject.toml` gaining a `mascope-tools-core` build, or
a sibling package directory the full package depends on), holding
`composition.profiles`, `composition.reagents`, `composition.utils` (and
`metrics`), `composition.series`, `composition.mechanism_notation` and
`stats.covariation`, together with the six modules those import
(`config`, `custom_elements`, `envelope_tolerance`, `exceptions`,
`known_window` and `models`, which import numpy alone among third-party
packages; `grid` stays in the heavy half, since only
`propose_same_ion_readings` reaches it), with `requires-python >= 3.10`
and numpy, pandas,
pyteomics and IsoSpecPy as its dependencies. The last two stay because they
are light (pyteomics is pure Python and takes 3.10; IsoSpecPy is a cffi
wheel and takes 3.9) and because the light half cannot do without them:
every reagent ion's m/z is `composition_mass`, which is pyteomics'
`calculate_mass`, `kendrick` needs the repeat unit's exact mass the same
way, and `match_reagent_clusters` claims each cluster's isotopologues
through `predict_resolved_isotopes`, which is IsoSpecPy. What goes is
everything else the full package requires and no light module imports:
polars, scipy, ruptures, pyarrow (so option (a)'s floor of 25 is not this
option's), tqdm and the SDK. `composition/__init__.py` made lazy so
importing a light module runs no heavy import; `reagents` and the light
half of `heuristic_filter` (`effective_counts`, `_context_ratios_ok`,
`predict_resolved_isotopes` and `monoisotopic_index`, which `reagents`
imports beside it) split from the plausibility filter's polars and scipy;
the covariation linkage on numpy alone or scipy as the light
distribution's one optional extra, which 7.7 settles. The full
`mascope_tools` depends on the light distribution and re-exports, so the
root project, `mascope_reference`, `mascope_signal` and the engine change
no import.

**Where.** `libraries/tools/pyproject.toml` and `src/mascope_tools/`,
`libraries/tools/tests` (an import test that the light modules load with
the heavy packages absent, in a bare virtualenv under CI's "Library tests"),
`tooling/check-licenses.py`'s lockfile pass, the release tooling that
publishes `mascope-tools` to PyPI (a second artifact).

**Verify.** `mascope test run libraries`; the bare-venv import test; the
light distribution installs on Python 3.10 beside `mascope-ai` with no
scipy, polars or ruptures in the environment; `match_reagent_clusters` and
`kendrick` run there; the full package's tests unchanged.

**Size.** L. Blocked by decision 7.7 choosing option (b); blocks 8.3 under
it.

### 8.16 The multi-day questions of family T (deferred)

**Context.** `diurnal`, the weekly cycle and the multi-day comparisons have
no honest bench question on a one-day demo; decision 7.4 asks whether a
second public dataset of a few days is published for them. 8.11 ships
`diurnal` with its refusal (D-T) and is not gated by 7.4; the questions are
this brief, so that a `blockedBy` on 7.4 gates an issue whose whole content
waits for it (`ai_toolkit_science.md` 3.2, 7.4).

**Task.** Once 7.4 is decided for a dataset: the goldens of that dataset in
the fixture's shape (its `Goldens` loader extended to a second bundle or a
second workspace), two or three T-family questions on it (a species'
hour-of-day profile and its peak hour, its day-to-night ratio, a comparison
of two days through `compare`) with their `golden=` callables, and a decline
of their own (a diurnal profile asked of a species found on one day only).
If 7.4 decides against a dataset, this brief closes with the diurnal family
decline-only, and 8.11's D-T stays its one question.

**Where.** `libraries/ai/tests/ai_golden.py`, `test_ai_golden.py`,
`docs/dev/ai_layer.md` (the question list regenerated), and the demo bundle
registry if a second bundle is cut (`BUNDLES` in
`tooling/cli/src/mascope_cli/cmd/demo/bundles.py`; `docs/demo_dataset.md`
says how a bundle is added to it).

**Verify.** `dryrun` on the new questions; `test_the_note_lists_them`
green; the contract run on a stack that holds the dataset.

**Size.** M. Blocked by decision 7.4, by 8.11 (`diurnal`) and by 8.10
(`compare`, which the two-day comparison calls; a question whose tools name
`compare` before it is on `SURFACE` fails
`test_solvers_call_only_the_tool_surface`).

### 8.17 Publish `mascope_viz` to PyPI

**Context.** `mascope_ai` is on PyPI (MAS-611), and 8.11 makes it depend on
`mascope_viz`, which `.github/workflows/publish-pypi.yaml` does not publish:
it publishes the SDK, `mascope_tools`, `mascope_ai`, the runtime, the CLI
and the File Agent. No Visual digests issue adds it, and MAS-237, which
also has `mascope_ai` depend on `mascope_viz`, has none either. A
dependency on an unpublished package cannot install, so the publish is a
gate of 8.11 (and of MAS-237), and the repository's rule makes it an issue.

**Task.** The `mascope_viz` step in `.github/workflows/publish-pypi.yaml`,
as `mascope_ai`'s was done under MAS-613 and MAS-615. **Order:** the step
runs `publish-package.sh libraries/viz mascope-viz mascope_viz` after the
mascope-sdk step (`mascope_viz` depends on the SDK) and before the
mascope-ai step, since once `mascope_ai` depends on `mascope_viz` the
mascope-ai install check in `publish-package.sh` resolves it from the
shared wheelhouse or PyPI, and a step placed after it fails that check and
stops the job, leaving the runtime, the CLI and the File Agent unpublished
for that release. **Trigger:** `libraries/viz/pyproject.toml` added to
`on.push.paths`, which lists the six published `pyproject.toml` files
today, as MAS-613's diff added `libraries/ai`'s. **Metadata:**
`libraries/viz/pyproject.toml` already carries `license.text`, `readme`
and `[project.urls]`, and its name `mascope_viz` becomes `mascope-viz` on
PyPI as `mascope_ai` became `mascope-ai`, so no rename; what it needs is a
version bump to the release that first ships. The developer guide's
Publish section gains the package in its list (six today), and
`libraries/viz/README.md` the `pip install mascope-viz` line
`libraries/ai/README.md` has.

**Where.** `.github/workflows/publish-pypi.yaml` (the step and the
`paths` entry), `libraries/viz/pyproject.toml` (the version),
`docs/dev/developer_guide.md` (Publish), `libraries/viz/README.md`; filed
in Visual digests, or here, as the owner prefers.

**Verify.** The workflow has no dry-run job (its one job uploads, on a
push to master or a dispatch), so the check is MAS-613's: a local replay of
the step with `uv publish` stubbed out, which runs the build and the
install check against the wheelhouse, with the mascope-ai step replayed
after it once 8.11's dependency is in; after the release, `pip install
mascope-viz` resolves. Whether 8.11's `libraries/ai/pyproject.toml`
dependency installs from PyPI is 8.11's Verify, since this brief blocks
8.11.

**Size.** S. Blocked by MAS-225 (a release of phase 1 is what is
published) and by a *Needs input* issue of its own for the two steps only
the owner can take, as MAS-613 and MAS-615 were: the commit under
`.github/workflows`, which the builder App cannot push, and the pending
trusted publisher on PyPI for a project PyPI has never had (the developer
guide's Publish section). Blocks 8.11 and MAS-237.
