"""
Fixtures for mascope_viz's hermetic tests.

``campaign`` is the synthetic month of :mod:`fixtures.campaign`, once per
chemistry; ``halide_campaign`` and ``positive_campaign`` name one, and
``cluster_campaign`` is the halide month with two reagent ions. ``synced``
is that month synced into a campaign store from :mod:`fixtures.server`'s
fake server. Each is made once per session: a month is two million
peak-series rows, and tests read it without changing it.
"""

import pandas as pd
import pytest
from fixtures.campaign import (
    HALIDE_CLUSTERS,
    HALIDE_NEGATIVE,
    POSITIVE,
    Campaign,
    generate,
)
from fixtures.server import SCOPE, FakeClient

from mascope_viz.data import store as store_module
from mascope_viz.data.store import sync


@pytest.fixture(scope="session")
def halide_campaign() -> Campaign:
    return generate(HALIDE_NEGATIVE, seed=0)


@pytest.fixture(scope="session")
def positive_campaign() -> Campaign:
    return generate(POSITIVE, seed=0)


@pytest.fixture(scope="session")
def cluster_campaign() -> Campaign:
    """The halide month with its reagent over the bare ion and its water
    cluster (:data:`fixtures.campaign.HALIDE_CLUSTERS`)."""
    return generate(HALIDE_CLUSTERS, seed=0)


@pytest.fixture(scope="session", params=["halide_negative", "positive"])
def campaign(request) -> Campaign:
    return request.getfixturevalue(f"{request.param.split('_')[0]}_campaign")


@pytest.fixture(scope="session")
def synced(campaign, tmp_path_factory):
    """The month of each chemistry, synced once a month after it ended:
    the campaign, the fake server and the sync's report."""
    patch = pytest.MonkeyPatch()
    patch.setattr(store_module, "_now", lambda: pd.Timestamp("2026-08-01T00:00:00Z"))
    patch.setattr(store_module.time, "sleep", lambda seconds: None)
    try:
        client = FakeClient(campaign)
        report = sync(client, SCOPE, root=tmp_path_factory.mktemp("stores"))
    finally:
        patch.undo()
    return campaign, client, report
