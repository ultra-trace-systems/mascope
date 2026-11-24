# Assignment engine comparison

`compare_runs.py` puts two peak-assignment engines side by side on the same
samples, one row per observed peak, and reports where they agree, where they
disagree, and how each disagreement class distributes over peak intensity.

It reads nothing but the server: both engines' runs live in the peak-assignment
store, so the in-app engine's run and an externally published one (for example a
`peaky publish` of the same sample) are two rows in the sample's run list. The
script picks the latest completed run of each engine per sample, pulls both
ledgers through the SDK and joins them on `sample_peak_id`.

## Prerequisites

- An SDK token for the deployment (`MASCOPE_URL`, `MASCOPE_ACCESS_TOKEN`; see
  the SDK's README). A self-signed deployment also needs
  `MASCOPE_SDK_VERIFY_SSL=0`.
- Every sample to compare carries a completed run of both engines. The in-app
  run is launched from the Sample view or with
  `POST /api/peak-assignments/sample/{id}/assign`; an external run is imported
  with `POST .../runs/import` (peaky's `publish` command does this). Samples
  missing either run are skipped and named.

## Usage

```sh
uv run python tooling/assignment_compare/compare_runs.py \
    --workspace "<workspace>" --sample <id> [<id> ...] --out compare/

uv run python tooling/assignment_compare/compare_runs.py \
    --workspace "<workspace>" --batch "<batch name>" --dataset "<dataset>" \
    --engine-a mascope --engine-b peaky --out compare/
```

`--engine-b-before <timestamp>` reads the second engine's newest completed run
created before that instant instead of its latest, so a reference that was
re-published can still be read as it stood before (the timestamp is UTC unless
it carries an offset). The nightly run prune keeps two completed runs per sample
and engine by default, so on such a deployment this reaches back one publish once
the prune has run, and further between a publish and the next prune.
`summary.json` records the timestamp, and every joined row names the run it
read.

## Output

| file | content |
|---|---|
| `joined_<sample>.csv` | one row per peak: both engines' role, tier, formula, adduct, ion, fit, mass error, plus the join verdict and the peak's intensity rank |
| `joined_all.csv` | the same, pooled over every compared sample |
| `summary.json` | pooled and per-sample metrics |
| `summary.md` | the headline table and the intensity-rank table, ready to paste |

The verdict of a peak both engines commit to is one of `same_formula`,
`same_neutral_other_adduct`, `same_ion_other_split` (the same ion read as a
different neutral/adduct pair, which no spectrum can separate) or
`different_formula`; a peak only one engine commits to carries the other
engine's role (`a_only_unassigned`, `b_only_iso_child`, ...).

Beyond agreement, the summary reports each engine's tier distribution, the
share of its `assigned` rows the other engine does not confirm, mass-error
statistics of the committed peaks, and a chemistry sanity check on the
committed formulas (carbon-free formulas, N >= 5, N/C > 0.6, H/C < 0.5,
O/C > 1.5, RDBE > 15) - the classes a mass-fit-only assignment produces and
ambient chemistry does not. None of it is a ground truth: the second engine is
the reference only in the sense that a disagreement is where a human should
look, and the `tier_disagrees` filter in the app's ledger shows the same rows.

## Decoy arms (`--decoy`)

```sh
uv run python tooling/assignment_compare/compare_runs.py \
    --workspace "<workspace>" --batch "<batch name>" --dataset "<dataset>" \
    --decoy --out decoy/
```

The agreement above says nothing about how many rows a run would still assign
if the sample's chemistry were wrong. A decoy run measures it: the in-app
engine run again on a sample it has already assigned (its control) with one
arm made wrong, scored at the control's own calibration
(`docs/user/how-it-works/peak-assignment.md`, *Decoy runs*). Launch one per
sample and arm with `POST /api/peak-assignments/sample/{id}/assign` and the
control's settings plus a `decoy` block:

- `{"decoy": {"mz_shift_ppm": 9}}` and `-9`: the m/z axis scaled 9 ppm either
  way, on an Orbitrap;
- `{"decoy": {"mz_shift_da": 0.35}}`: every peak moved 0.35 Da, on a TOF, where
  a 9 ppm shift is inside the window and is refused;
- `{"decoy": {"swap_adducts": true}}`: every channel searched through an adduct
  the profile does not make.

A decoy that cannot bound the sample's latest run (none, other settings, a
shift inside its window, no wrong adduct left) is refused with a 422 naming
why, and creates no run. Decoy runs stay out of the runs listing unless it is
asked for them (`include_decoys`, which this script sets), and out of the batch
fold and every other reader of the sample's result.

With `--decoy` the script reads each sample's newest decoy run per arm against
the run it names as its control, and writes what follows. Each arm keeps its
own retention quota, so a decoy can outlive its control: that arm is named as
not read, and the rest of the set still is.

| file | content |
|---|---|
| `decoy.json` | per arm, pooled over the set and per sample: the control's and the arm's assigned rows and intensity below and above m/z 350 and per 50-Da bin, the share the arm holds, and on the adduct arm the rows that are new ions against those that read an ion the control reads as another neutral; per sample the measurement model version of the control and of the decoy, and per arm the same counts split by the control's version (`by_model`) and the decoys at another version than their control (`model_differs`); the arms that were refused, are still running or lost their control, and why |
| `decoy.md` | the pooled table, one row per arm, ready to paste |

A row counts where it is a monoisotopic row at the `assigned` tier. The m/z
350 split is the reference engine's: below it a 0.35 Da shift lands in the
mass-defect gap no CHNOS formula sits in. A shifted arm claims the source's
own lines where its control did. One recorded before that (its `decoy_arm`
has no `source_ions`) looked for them on the moved axis, and on a dalton
shift claimed none, so its brightest ions were offered to the stages like any
peak: an arm's rows on peaks the control claimed as reagent are counted apart
(`on_control_reagent`), and the intensity share is read knowing them, since
one such row can outweigh every analyte. There is no target yet (decision 31):
the stage gates report the arms, and a target is set once two gates have read
them.

## The gate card (`--card`)

```sh
uv run python tooling/assignment_compare/compare_runs.py \
    --sets sets.json --card cards/ --gate [thresholds.json] \
    [--baseline previous/card.json] [--decoy] [--deny names.txt]
```

The card is the plan's table as code: every metric of the measurement log's
table (`docs/dev/assignment_quality_plan_log.md`, *Metrics and targets*) and
G9 to G14 of the plan's stage 3, read off the stored runs of both engines over
fixed sets of samples, one column per set and one pooled over them all. Each
metric is a function in `metrics.py` whose docstring quotes its definition; the
log's table names the function beside each metric.

`--sets` names the sets in a manifest kept beside the data, never in the
repository:

```json
{
  "sets": {
    "A": {"workspace": "<workspace>", "samples": ["<id>", "..."]},
    "E": {"workspace": "<workspace>", "batch": "<batch>", "dataset": "<dataset>",
          "instrument": "tof"},
    "G": {"workspace": "<workspace>", "samples": ["..."], "sulfur_free": true},
    "N": {"workspace": "<workspace>", "samples": ["..."], "instrument": "tof",
          "truth": {"file": "truth/N.json"}}
  },
  "deny": ["<an instrument's name>", "<a site's name>"]
}
```

A set is named by an upper-case letter, then at most one digit or lower-case
letter, then at most a sign (`A`, `C2`, `Hr`, `I+`), and the manifest is
refused otherwise, as it is when the deny list names a set (below). `instrument` is `orbitrap` (the
default) or `tof`, which a target can be restricted by. `sulfur_free` says the set's chemistry holds no
sulfur, so G9 counts a sulfur-bearing neutral as implausible there. Every
sample with a completed run of the first engine is read; one that also carries
the reference's run is joined to it, and one that does not is read by the
intrinsic metrics alone (G3, G7 to G14, the mass error, the reasons), its
reference metrics left out. A set with no reference run at all reads `-` on
every reference metric. A sample with no completed run of the first engine, or
an empty ledger, is skipped and counted on the card's `samples_skipped` row.
The `model_v1`, `model_v2` and `model_before` rows count the samples read by
the measurement model their run of the first engine recorded
(`config.measurement_model.version`): 1, the model fitted after Stage A's
match, or 2, the refit on the corroborated commits the run adopted. A run
from before that record kept only its `pattern_scoring` snapshot and counts
as `model_before`, not as v1. They are readings, gated by no target; a
baseline made before them reads them as moved from `-`, which fails nothing. A
set on which no sample is read keeps its column, every reading `-`, and fails
the gate (`no sample read on D`): the bench could not read it, which is not a
pass. `truth` is the set's authors' list (below): the neutral formulas
themselves, or `{"file": P}`, a JSON list of them at `P` relative to the
manifest's folder. `runs` names the run of the first engine to read per
sample (`{"<sample>": "<run id>"}`), in place of its newest completed one;
a named run that is not a completed run of that engine skips its sample.
`batch_arm` and `batch_arm_off` are the batch arm's (below).

The tier reasons, the mass gate's record, the predicted envelope (3.4e) and
the peak's separation (3.4f) are in each row's provenance. The ledger serves
what the card reads of them on every row (`tier_reason_rules`,
`mass_gate_corroborated`, `envelope_absent_lines`, `peak_unseparated`), so a
run is one paged read. A run older than a record reads `-` on its metric (G13
before tiering rule set 11, G14 before 13), not 0.

The identification level (step 5.8) is read off the ledger's `level` column:
the share of the first engine's committed monoisotopic rows at 4a, 4b, 5a and
5b, of those that state a level; the 5b rows split by what put them there,
read off the records G13 and G14 read (an absent line, an unseparated peak,
or neither of them, which leaves the plausibility floor; a row can carry
both of the first two); where the tier and the level cross, the
share of the assigned rows at level 5 and of the rows under assigned at 4 or
above; and on each decoy arm, the share of its committed rows at 4 or above.
They are readings, gated by no target, and a run from before the level reads
`-` on them.

A server whose ledger predates those columns serves none of them, and the card
then reads each committed row of the first engine through the per-assignment
detail read: one request a row, about 65 ms on the testbed, which answers them
one at a time. A six-sample sparse Orbitrap set (about 2,300 rows) takes under
three minutes that way, a dense one (about 11,000) thirteen. A detail read that
fails, on an error answer or on no answer once the client's retries are spent,
leaves its row unread and is counted on the `detail_failed` row. The reason
coverage, G13 and G14 leave such a row out, so where a set has one and they
meet their target, the gate marks them *not read*: the row left out could hold
the miss. G7 counts it as uncorroborated, so its pass stands. Off the ledger
no row's read can fail, and `detail_failed` reads 0.

### What the card writes

| file | content |
|---|---|
| `card.md` | the table: a metric per row, a set per column and the pooled one, the target of the stage the thresholds name, a `MISS` on every gated reading that misses; the pass-or-fail line; the readings that moved since the baseline |
| `card.json` | the same as data: per column its instrument, the engines' versions, the tiering rule sets, the decoy arms not read, and every reading as `{"value": ..., "n": ...}`, the number and the rows it is a share of or counted over; the gate and its marks; the thresholds; the diff |
| `card_<set>.md`, `card_<set>.json` | one set's card: each reading with its count, its target and its mark, and the set's batch arm where it has one. Its JSON is a card of its own, so it can be the baseline of another |

A card whose sets asked for the batch arm also writes it, under *After the
batch pass* in `card.md` and as `batch_arm` in `card.json` (below). A card
with no such set holds neither, so it is the card it was before the arm.

The command prints the table and the pass-or-fail line:

```text
GATE stage3: FAIL - 2 of 140 gated readings missed: B G9 3.1 (<= 2); D G12 1 (<= 0); 6 not read (A G13, ...)
GATE stage3: FAIL - no sample read on K; 120 gated readings met; 6 not read (A G13, ...)
```

It exits 0 when the card is written and passes (or no gate was asked), 1 when
the gate fails and 2 when the scrub refuses the card. The pooled column is not
marked: every target is stated per set.

**The gate against a baseline.** With `--gate` and `--baseline` the gate
reads the card's misses against the baseline's, the baseline judged by the
same thresholds, and its verdict is the one for this ref:

```text
GATE stage3: PASS for this ref - 0 new, 5 develop's (Db G6_assigned 8 (<= 5); ...), 1 fixed (Nm G9 1.8 (<= 2), was 5.4)
GATE stage3: FAIL for this ref - 1 new (Nc G9 3 (<= 2), was 1), 5 develop's (...), 0 fixed
```

- **new**: the card misses a reading the baseline met, did not read, or
  holds no column for (`not read on the baseline`). A miss both have that
  the card makes worse by more than the metric's `noise` (the thresholds
  file) is new too, marked `worse than` the baseline's value: higher under a
  `max`, lower under a `min`.
- **develop's**: both miss it, and the card does not make it worse.
- **fixed**: the baseline misses it and the card meets it.

The verdict fails on a new miss, or on a set with no sample read, and never
on develop's misses alone; the line always counts them. The exit code follows
that verdict. `card.json` keeps the card's own verdict as `absolute` and the
three lists under `against`; each set's card splits its own misses the same
way. `--absolute-gate` keeps the line above, the card judged alone, while
`--baseline` still diffs: the bench gives it on a card of develop's head,
whose misses are its own.

`--decoy` adds the decoy arms (above) as rows of their own,
`decoy <arm> held`, `held_lt_350`, `held_ge_350`, `held_intensity`,
`new_ion` and `on_control_reagent`, per set and pooled. They have no target
yet (decision 31).
A decoy reads its control's measurement model unchanged, so each arm's held
share is also read per model version of the controls:
`decoy <arm> held_model_v1`, `held_model_v2` and `held_model_before`, each
the share of the rows of the controls at that version the arm still holds,
with those rows as its count. An arm's move can so be read against the
controls that moved to the refit. `decoy <arm> model_differs` counts the arms
whose decoy states another version than its control, which should be none.
A card reads a sample's arm only where its control is the run the card
reads of that sample. An arm refused before it ran makes no run, so the
newest arm the store keeps can be one an earlier build made against its own
control: that arm is counted among the decoy arms not read.

### The batch arm (`batch_arm`)

The card reads each sample's own run, which reads none of what a batch
decides: the isotope-line owners (the strips and the stamps), the known
species and the water ladder, the series, and what the batch's untargeted
search adds. The batch arm reads it. A set gives it as `"batch_arm": {"runs":
{"<sample>": "<run id>"}}`: the sample's run after a pass of the batch stages
on the set's batch, which reads the batch's decisions as declared inputs
(MAS-985). The set's own column still reads its `runs`, the control, and so
does the gate: the arm is gated by nothing, and a diff against a baseline
leaves it out.

For each set with an arm, `card.json`'s `batch_arm.columns.<set>` holds:

- `readings`: every reading the set's column holds, read on the runs after
  the pass, joined to the same reference; no decoy arm, since the decoy arms
  stay on the control;
- `moves`: the peaks whose row moved from the control to the run after the
  pass, over the samples both read: `role` (its role changed), `tier` (its
  tier alone), `reading` (its formula, ion, mechanism or isotope line), and
  `moved`, the peaks with any of them; `by_role`, each role change counted;
  `by_decision`, each moved row of the run after the pass counted under each
  batch decision it cites: `known_species` and `water_rung` (a source row the
  claim stage took on the batch's word, `provenance.reagent.reason`),
  `listed_compound` (a `batch_known_species` tier reason), `isotopologue_strip`
  (a row the strip read as its parent's line, `provenance.batch_isotopologue`)
  and `series` (a `series_anchor` tier reason), `none` for a row that cites
  none and `not_read` for one whose detail read failed. The two tier reasons
  are read off the ledger; the provenance only through the detail read, which
  the card makes on the moved source rows and isotope lines alone;
  `control_read_decisions` and `after_read_decisions`, the samples whose run
  records reading any batch decision (`config.batch_decisions`): the control
  reads none on a bench that starts clean of them.

`batch_arm.off.<set>` names a set that asked for the arm and has none, with
why (`"batch_arm_off": "<why>"` in its manifest entry). The markdown puts
the control's and the arm's readings side by side, a pair of columns per
set, then the moves, and says what the arm does not cover: the decoy arms,
and the readings that live on the batch peak alone (co-variation, the batch
checks, blends, the ratio grade), which no sample's row carries (MAS-909).

### An authors' list (`truth`)

A set drawn from a published or shared study can come with the peak list its
authors assigned. Its neutral formulas, the set's `truth`, are read against
both engines, and the set's column gains seven rows after G14, none gated:

| key | reading |
|---|---|
| `list_formulas` | the list's distinct formulas |
| `list_held` | of the first engine's assigned monoisotopic formulas, the share the list holds |
| `list_found` | of the list's formulas, the share the first engine assigns |
| `list_found_candidate` | the same, at assigned or candidate |
| `ref_list_held`, `ref_list_found`, `ref_list_found_candidate` | the same three for the reference, at its own tier (`engine_tier`, as G2 reads it), on the samples that carry its run |

Formulas are compared by their element counts, as the join compares the two
engines' (Hill order, `NO8C10H15` and `C10 H15 N O8` are one formula), and
counted distinct over the set's samples: a formula two samples assign is one.
A list's entry must be a neutral formula of element symbols and counts, with
no charge, isotope or adduct; the manifest is refused otherwise, naming the
entry's place in the list and never its text. The pooled column reads each set
against its own list and adds the counts. The card names no formula.

The lists are not exhaustive: the authors assigned what their study needed,
so a formula off the list is not wrong, and the share of the list an engine
finds is not a recall. These are readings: no target names them, and the card
says so above its table. The list is read by this tool alone and never loaded
into the instance: the in-app engine reads a batch's own target collections
as known targets, so a list attached there would hand it the answers.

`--baseline` diffs the card against a previous `card.json` (or a set's), in
the columns both hold: every reading whose value or count differs, or that
only one of the two columns holds, is listed under *Since the baseline*, with
what it was and what it is now. A column only one card holds is named once
(`not on the baseline: G, pooled`), so a set's card as the baseline of a whole
one compares that set alone. The baseline's format, the `--deny` file and the
thresholds are read before any set is, so a wrong path fails at once.

### The scrub

The card names sets by their letter and nothing else. Before anything is
written, its text, every file of it, is checked against a deny list: the
manifest's workspace, batch and dataset names, every sample id it read, the
manifest's own `deny` list, the lines of `--deny` (`#` starts a comment), and
the host of `MASCOPE_URL` and of the machine the tool runs on. A name is
denied whole and word by word (three characters or more, not a number), in any
case, where no letter or digit adjoins it; a word the card prints of its own
is not a reason to refuse it, but the name it is part of still is. The card's
own words are its labels, such as *assigned* or *peaks*, the keys of its JSON,
the engines' names and the words of the thresholds file it carries. A host is
denied whole even when it is an address of numbers alone. A name of the
manifest's `deny` list or of `--deny` is denied whole whatever it is made of,
and a manifest whose set the deny list names is refused before anything is
read. A match refuses the card, names the token and the file it is in, and
writes nothing.

### The thresholds file

`thresholds.json`, shipped beside the script and used by `--gate` without a
path, holds the plan's stage targets: stages 1, 2 and 4 from the log's table,
stage 3's G9 to G14 from the plan's stage 3 text.

```json
{
  "format": 1,
  "stage": "stage3",
  "stages": ["stage1", "stage2", "stage3", "stage4"],
  "not_gated_sets": ["J"],
  "targets": {
    "G1": {"instruments": ["orbitrap"], "stage1": {"max": 45}, "stage2": {"max": 20},
           "stage3": {"max": 20, "reading": true}},
    "G2_formula": {"stage1": {"min": 80}, "sets": {"B": {"stage1": {"min": 70}}}},
    "G6": {"stage1": null}
  }
}
```

- `stage` is the stage the card is marked against; `stages` their order.
- A target is `{"max": n}` or `{"min": n}`, keyed by the reading's key in
  `card.json` (`G1`, `G4a`, `MAD`, `decoy swap_adducts held`, ...).
- A stage a metric does not name holds the stage before it, as the log's
  *hold* does; `null` is no target from that stage on.
- `"reading": true` marks the reading against the target and leaves it out of
  the verdict: decision 27 makes G1 and G2 readings from the stage 3 gate on.
- `only_sets` restricts a target to the sets it names, and `instruments` to sets
  of those classes: the log states G1, G2 and the mass error for the Orbitrap
  sets A to D and C2, and the shipped file holds them there. It reads G4 and
  G4a on A, B, D, E and F1 alone: on C, C2 and F2 the reference's reagent rows
  are another claim, so the raw share does not measure the engine. `sets` gives a set
  a target of its own for a stage; a set in `not_gated_sets` is marked against
  nothing (set J, decision 22).
- A gated reading with no value is *not read*: named on the line, neither met
  nor missed.
- `noise` gives per metric how far a reading moves between two cards of one
  commit. Against a baseline, a miss both cards share is the card's own only
  when it is worse by more than that. A metric it does not name has none.

The shipped targets, per stage (the log's table, and the plan's stage 3 text
for G9 to G14):

| reading | stage 1 | stage 2 | stage 3 | stage 4 |
|---|---|---|---|---|
| `G1` (A to D, C2) | <= 45 | <= 20 | a reading | <= 15, a reading |
| `G1_conditioned` (A to D, C2) | - | <= 20 | a reading | hold |
| `G2_formula` (A to D, C2) | >= 80, B >= 70 | >= 85 | a reading | hold |
| `G2_ion` (A to D, C2) | >= 95 | hold | a reading | hold |
| `G3_N5` | <= 1 | hold | hold | hold |
| `G3_carbon_free_untargeted` | 0 | hold | hold | hold |
| `G4`, `G4a` (A, B, D, E, F1) | >= 90 | 100 | hold | hold |
| `G5`, `G8` | 0 | hold | hold | hold |
| `G6_assigned` | - | <= 5 | hold | hold |
| `G7` | - | 0 | hold | hold |
| `MAD` (A to D, C2) | <= 0.35 | hold | hold | hold |
| `reasons` | - | 100 | hold | hold |
| `G9` | - | - | <= 2 | hold |
| `G10` | - | - | >= 95 | hold |
| `G11`, `G12`, `G13`, `G14` | - | - | 0 | hold |

### How the card reads G9 to G14

- **G9** counts an assigned monoisotopic neutral with two or more nitrogens and
  fewer than two oxygens per nitrogen, H/C above 2.4, a negative double-bond
  equivalent (halogens monovalent), or sulfur on a `sulfur_free` set.
- **G10** reads the source ions of the reagent library
  (`mascope_tools.composition.reagents.reagent_library`) of the profile each
  run resolved, every family, each on the brightest peak within the run's
  match window that reaches the reagent pre-pass's floor of the base peak
  (1e-4). A peak counts as named when the run parks it as reagent or artifact,
  or commits it from a list tagged as background (the siloxanes, 3.3f). A
  contaminant no list names is not in the library, and is not counted.
- **G11** reads a C11 CHO neutral assigned through `[M-H]-` whose ion also
  reads as the acid less CH2O2, the C10 neutral, through `[M+HCOO]-`, where
  that lighter neutral is assigned through another channel in the sample: its
  share of the assigned monoisotopic intensity (gated), and beside it the same
  over the assigned and candidate rows (`G11_committed`). C11 is the plan
  owner's passes' reading of the C(n+1) the definition names, the chamber's
  monoterpene products with formic acid (`metrics.FORMATE_ACID_CARBONS`); read
  at any n, the pairing finds a quarter of a bromide set's intensity.
- **G12** counts an assigned monoisotopic row from the formula search with no
  isotopologue row of its own, fewer than two channels committing its neutral
  (`corroboration_channels`), no list naming it (`reference_listing`) and no
  homologous series of its batch anchoring it (a `series_anchor` tier reason,
  read off `tier_reason_rules`): the four escapes of the tiering rule's
  `lone_peak` (`tiering.lone_peak_reason`, `metrics.LONE_PEAK_ESCAPES`; a
  backend test pins the two lists together). A row a series alone saw twice
  is the series' lift, which the plan owner ruled corroboration enough
  (MAS-1255): G12 exempts it, and the lines under G12 count it, so the card
  still watches what a series lifts. They appear only on a column whose runs
  hold such a row, in practice the batch arm's; the control has none, and its
  card is the card it was. Readings, never gated:
  - `G12_series`: the series-lifted lone peaks, over the assigned search rows;
  - `G12_series_same`, `G12_series_other`, `G12_series_silent`: of them, over
    the samples with a reference run, the share on which the reference commits
    the same formula through the same adduct, any other reading (another
    formula, adduct or role), and nothing at all;
  - `G12_yardstick_same`, `_other`, `_silent`: the same three shares over the
    run's other assigned search rows, the yardstick beside them.

  Whether the stage 4 gate should hold the lifted rows' agreement near the
  yardstick is the plan owner's call (MAS-1104); nothing gates it yet.
- **G13** counts an assigned monoisotopic row whose `predicted_envelope` holds
  an asked line that is absent; **G14** one whose `peak_separation` says it is
  not separated and whose `cross_channel.separated_through` names no partner.
