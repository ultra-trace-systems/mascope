"""
The views against a live demo stack: reagent-ion normalisation reproduces
notebook 06.

Opt-in, as ``test_viz_scope_contract.py`` is (whose client and dataset it
takes): a running demo stack and ``MASCOPE_SDK_CONTRACT=1``.

Notebook 06 (``libraries/sdk/src/mascope_sdk/examples/06_normalization.ipynb``)
normalises by a reagent ion in its second method: the heights of the peaks
matched to the reagent's ion formula, summed per sample; each matched peak's
height divided by that; and the quotients summed per sample and ion. Here
the reagent's batch is the batch itself, which makes the notebook's pairing
of samples by position the identity, and the reagent is the batch's
brightest ion matched in every sample: the demo batches' m/z ranges leave
out the notebook's own uronium reagent ions.
"""

import numpy as np
import pandas as pd
import pytest
from test_viz_scope_contract import (  # noqa: F401 - the fixtures are used by name
    client,
    dataset,
    pytestmark,
)

from mascope_viz.data import Scope, views
from mascope_viz.data.store import sync


@pytest.fixture(scope="module")
def store(client, dataset, tmp_path_factory):  # noqa: F811
    report = sync(
        client,
        Scope(dataset=dataset["dataset_id"]),
        pause=0,
        root=tmp_path_factory.mktemp("stores"),
    )
    assert report.failed == {}
    return report.store


def _batches(store) -> list[str]:
    return list(store.status().batches["sample_batch_id"])


def _reagent(store, batch_id: str, n_samples: int) -> str:
    """The batch's brightest ion matched in every sample."""
    ions = store.table("ion_series", batches=[batch_id]).dropna(subset=["intensity"])
    found = ions.groupby("series_key", observed=True)["intensity"].agg(
        ["median", "size"]
    )
    found = found[found["size"] == n_samples]
    if found.empty:
        pytest.skip("no ion of the batch is matched in every sample")
    return str(found["median"].idxmax())


def _notebook(peaks: pd.DataFrame, reagent_formula: str) -> pd.Series:
    """Notebook 06's reagent-ion normalisation, by sample and target ion."""
    reagent = peaks[peaks["target_ion_formula"].isin([reagent_formula])]
    ri_height = (
        reagent.drop_duplicates(subset=["sample_item_id", "peak_id"])
        .groupby("sample_item_id")["height"]
        .sum()
    )
    peaks = peaks.copy()
    peaks["height_norm"] = peaks["height"] / peaks["sample_item_id"].map(ri_height)
    matched = peaks[peaks["target_ion_formula"].notna()]
    return matched.groupby(["sample_item_id", "target_ion_id"])["height_norm"].sum()


@pytest.mark.parametrize("which", [0, 1])
def test_reagent_normalisation_reproduces_notebook_06(client, store, which):  # noqa: F811
    batches = store.status().batches
    if which >= len(batches):
        pytest.skip("the demo dataset has fewer batches")
    batch = batches.iloc[which]
    batch_id = batch["sample_batch_id"]
    reagent = _reagent(store, batch_id, int(batch["n_samples"]))
    species = store.table("species").set_index("series_key")
    peaks = client.load_peaks(
        dataset=store.meta["scope"]["dataset"],
        batches=batch["sample_batch_name"],
        exact=True,
        matches=True,
        confirm_above=None,
    )
    notebook = _notebook(peaks, species.loc[reagent, "ion_formula"])

    ions = store.table("ion_series", batches=[batch_id])
    keys = sorted(set(ions["series_key"].astype(str)))
    samples = store.table("samples", batches=[batch_id])
    frame = views.series(
        store,
        keys,
        cadence="raw",
        normalise=reagent,
        tz="UTC",
        start=samples["t_mid_utc"].min(),
        end=samples["t_mid_utc"].max() + pd.Timedelta(seconds=1),
    )
    # A raw row is a sample, found by its time
    assert samples["t_mid_utc"].is_unique
    sample_at = pd.Series(
        samples["sample_item_id"].astype(str).to_numpy(), index=samples["t_mid_utc"]
    )
    rows = frame[(frame["n_samples"] == 1) & frame["value"].notna()]
    mine = pd.Series(
        rows["value"].to_numpy(),
        index=pd.MultiIndex.from_arrays(
            [
                sample_at.loc[rows["t"].dt.tz_convert("UTC")].to_numpy(),
                rows["key"].astype(str).str.removeprefix("ion:").to_numpy(),
            ]
        ),
    )

    assert frame.attrs["normalise"] == reagent
    assert len(mine) > 100
    # Every value the views give is the notebook's
    np.testing.assert_allclose(mine, notebook.loc[mine.index], rtol=1e-9)
    np.testing.assert_allclose(
        mine.xs(reagent.removeprefix("ion:"), level=1), 1.0, rtol=1e-12
    )
    # The notebook also counts the isotope matches of an ion the ion series
    # records as no match, which the store reads as no value (NaN)
    extra = notebook.index.difference(mine.index)
    records = ions.assign(
        sample_item_id=ions["sample_item_id"].astype(str),
        target_ion_id=ions["series_key"].astype(str).str.removeprefix("ion:"),
    ).set_index(["sample_item_id", "target_ion_id"])["match_category"]
    assert (records.loc[extra] == 0).all()
