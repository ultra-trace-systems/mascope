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
