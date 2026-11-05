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
| `decoy.json` | per arm, pooled over the set and per sample: the control's and the arm's assigned rows and intensity below and above m/z 350 and per 50-Da bin, the share the arm holds, and on the adduct arm the rows that are new ions against those that read an ion the control reads as another neutral; the arms that were refused, are still running or lost their control, and why |
| `decoy.md` | the pooled table, one row per arm, ready to paste |

A row counts where it is a monoisotopic row at the `assigned` tier. The m/z
350 split is the reference engine's: below it a 0.35 Da shift lands in the
mass-defect gap no CHNOS formula sits in. A dalton shift also moves the
source's own lines out of the reagent pre-pass's reach, so its brightest ions
are offered to the stages like any peak: an arm's rows on peaks the control
claimed as reagent are counted apart (`on_control_reagent`), and the intensity
share is read knowing them, since one such row can outweigh every analyte. There is no target yet (decision 31):
the stage gates report the arms, and a target is set once two gates have read
them.
