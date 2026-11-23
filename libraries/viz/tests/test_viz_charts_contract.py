"""
The species charts against a live demo stack: each draws the demo
dataset's brightest species on both backends, its y axis naming the
intensity the server's instrument type gives, its summary plain JSON.

Opt-in, as ``test_viz_scope_contract.py`` is (whose client and dataset it
takes): a running demo stack and ``MASCOPE_SDK_CONTRACT=1``. The assertions
are on shapes, not on values, so they survive bundle updates.
"""

import json

import pytest
from test_viz_scope_contract import (  # noqa: F401 - the fixtures are used by name
    client,
    dataset,
    pytestmark,
)

from mascope_viz import render
from mascope_viz.charts import registry
from mascope_viz.data import Scope
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


@pytest.mark.parametrize("kind", ["species_series", "diurnal", "period_boxes"])
def test_each_chart_draws_the_demo_dataset(store, kind):
    fig = registry.get(kind).build(store, species="top:3")

    assert 1 <= len(fig.summary["species"]) <= 3
    assert json.loads(fig.to_json())["id"] == kind
    # The server spells the instrument types its own way (orbi, tof)
    types = store.table("samples")["instrument_type"].dropna().astype(str)
    assert set(types)
    named = ["Peak area", "Peak height"]
    assert fig.panels[0].axes.y.label in (*named, "Peak area or peak height")
    assert len(render.matplotlib(fig).axes) >= len(fig.panels)
    json.loads(render.plotly(fig).to_json())
