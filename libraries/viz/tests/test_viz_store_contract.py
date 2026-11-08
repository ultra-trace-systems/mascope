"""
sync() against a live demo stack: the published demo dataset syncs.

Opt-in, as ``test_viz_scope_contract.py`` is (whose client and dataset it
takes): a running demo stack and ``MASCOPE_SDK_CONTRACT=1``. The assertions
are on shapes and on agreement with the SDK's own reads, not on values, so
they survive bundle updates; a demo stack without batch ledgers syncs ion
series alone.
"""

import numpy as np
import pandas as pd
import pytest
from test_viz_scope_contract import (  # noqa: F401 - the fixtures are used by name
    client,
    dataset,
    pytestmark,
)

from mascope_viz.data import Scope
from mascope_viz.data.store import SCHEMAS, TABLES, sync


@pytest.fixture(scope="module")
def synced(client, dataset, tmp_path_factory):  # noqa: F811
    return sync(
        client,
        Scope(dataset=dataset["dataset_id"]),
        pause=0,
        root=tmp_path_factory.mktemp("stores"),
    )


def test_the_demo_dataset_syncs_every_batch(synced):
    assert not synced.resolved.ambiguous
    assert synced.failed == {}
    assert set(synced.fetched) == set(synced.resolved.batches["sample_batch_id"])
    status = synced.store.status()
    assert status.complete
    assert status.rows["samples"] == synced.resolved.batches["n_samples"].sum()
    assert status.rows["files"] == len(synced.resolved.files)


def test_every_table_has_the_store_columns(synced):
    for name in TABLES:
        assert list(synced.store.table(name).columns) == SCHEMAS[name].names


def test_the_series_join_the_samples(synced):
    store = synced.store
    samples = store.table("samples")
    ids = set(samples["sample_item_id"])

    for name, pattern in (
        ("ion_series", r"ion:.+"),
        ("peak_series", r"formula:[^|]+\|\[.+\].*"),
    ):
        rows = store.table(name)
        assert set(rows["sample_item_id"].astype(str)) <= ids
        assert rows["series_key"].astype(str).str.fullmatch(pattern).all()
    keys = set(store.table("ion_series")["series_key"].astype(str)) | set(
        store.table("peak_series")["series_key"].astype(str)
    )
    assert keys == set(store.table("species")["series_key"].astype(str))
    assert not store.table("ion_series").empty


def test_the_ion_series_are_the_sdks(client, synced):  # noqa: F811
    store = synced.store
    held = store.table("ion_series")
    for batch_id in synced.resolved.batches["sample_batch_id"]:
        ids = set(store.table("samples", batches=[batch_id])["sample_item_id"])
        read = client.batches.ion_series(batch_id)
        read = read[read["sample_item_id"].isin(ids)]
        mine = held[held["sample_item_id"].isin(ids)]
        assert len(mine) == len(read)
        matched = read["match_category"] > 0
        np.testing.assert_allclose(
            np.sort(mine["intensity"].dropna().to_numpy()),
            np.sort(read.loc[matched, "intensity"].to_numpy()),
        )


def test_samples_are_timed_inside_their_file(synced):
    samples = synced.store.table("samples")
    files = synced.store.table("files").set_index("sample_file_id")

    start = samples["sample_file_id"].map(files["datetime_utc"])
    length = pd.to_timedelta(samples["sample_file_id"].map(files["length"]), unit="s")
    held = start.notna()
    assert held.any()
    assert (samples.loc[held, "t_mid_utc"] >= start[held]).all()
    assert (samples.loc[held, "t_mid_utc"] <= start[held] + length[held]).all()


def test_the_runs_are_the_ledgers_current_ones(client, synced):  # noqa: F811
    batches = synced.store.status().batches.set_index("sample_batch_id")
    for batch_id, row in batches.iterrows():
        runs = client.batch_peaks.runs(batch_id)
        current = (
            None
            if runs is None
            else next(
                iter(runs.loc[runs["current"].astype(bool), "batch_peak_run_id"]), None
            )
        )
        assert (None if pd.isna(row["run_id"]) else row["run_id"]) == current


def test_each_batch_holds_the_ledger_stamp_its_current_run_gives(client, synced):  # noqa: F811
    for batch_id, stamp in synced.store.meta["batches"].items():
        runs = client.batch_peaks.runs(batch_id)
        if runs is None:
            assert stamp["run"] is None
            continue
        current = runs[runs["current"].astype(bool)].iloc[0]
        given = current.get("ledger_utc_modified")
        held = stamp["run"]["ledger_utc_modified"]
        if given is None or pd.isna(given):
            assert held is None
        else:
            assert pd.Timestamp(held) == pd.Timestamp(given)


def test_a_second_sync_makes_no_series_calls(client, dataset, synced, monkeypatch):  # noqa: F811
    calls = []
    for resource, method in (
        (client.batches, "ion_series"),
        (client.batch_peaks, "series"),
        (client.batch_peaks, "list"),
    ):
        original = getattr(resource, method)

        def counted(*args, _original=original, _method=method, **kwargs):
            calls.append(_method)
            return _original(*args, **kwargs)

        monkeypatch.setattr(resource, method, counted)

    again = sync(
        client,
        Scope(dataset=dataset["dataset_id"]),
        pause=0,
        root=synced.store.path.parents[2],
    )

    assert again.store.path == synced.store.path
    # No demo batch holds a sample from the last day
    assert again.fetched == {}
    assert calls == []
