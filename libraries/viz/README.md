# mascope_viz

Visual digests of Mascope data: a package beside
[`mascope_sdk`](../sdk/README.md) that turns a scope of data (a dataset, a set
of batches, a time range) into charts, an interactive dashboard (HTML) and a
report (PDF), for the long time series the app does not draw. The design is
[`docs/dev/visual_digest.md`](../../docs/dev/visual_digest.md).

The package is young: it has its first piece of the data layer, the scope,
and its test backbone.

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
excluded. Names match as literal text, a compiled `re.Pattern` as a regex;
of `batches`, a string names one batch and a pattern every batch it matches.
A name that matches several objects is not an error: `resolved.ambiguous` is
true, `resolved.candidates` lists them, and nothing past the ambiguity was
read. One that matches nothing raises `mascope_viz.errors.NotFound`, which
lists the names there are.

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

`tests/test_viz_scope_contract.py` runs `resolve` on a demo stack, with the SDK
contract tests' switch and settings (`MASCOPE_SDK_CONTRACT=1`), and is
skipped without them.
