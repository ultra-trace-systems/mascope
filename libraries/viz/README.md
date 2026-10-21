# mascope_viz

Visual digests of Mascope data: a package beside
[`mascope_sdk`](../sdk/README.md) that turns a scope of data (a dataset, a set
of batches, a time range) into charts, an interactive dashboard (HTML) and a
report (PDF), for the long time series the app does not draw. The design is
[`docs/dev/visual_digest.md`](../../docs/dev/visual_digest.md).

The package is a skeleton for now. What it has is its test backbone.

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
