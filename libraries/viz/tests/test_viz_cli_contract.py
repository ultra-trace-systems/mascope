"""
``mascope-viz`` against a live demo stack: the demo dataset's scope lists
its batches, a sync caches them, and a view prints a summary.

Opt-in, as ``test_viz_scope_contract.py`` is (whose client, dataset and
settings it takes): a running demo stack and ``MASCOPE_SDK_CONTRACT=1``. The
command line reads its credentials as the SDK does, from
``MASCOPE_URL``, ``MASCOPE_ACCESS_TOKEN`` and ``MASCOPE_WORKSPACE``, which
the tests set to the contract tests' settings. The assertions are on
agreement with the SDK's own listings and the library's own views, not on
values, so they survive bundle updates.
"""

import json

import pandas as pd
import pytest
from test_viz_scope_contract import (  # noqa: F401 - the fixtures are used by name
    BASE_URL,
    TOKEN,
    client,
    dataset,
    pytestmark,
)
from typer.testing import CliRunner

from mascope_viz import cli
from mascope_viz.data import Scope, resolve, views
from mascope_viz.data.store import CampaignStore, store_path


@pytest.fixture(scope="module")
def environment(client, tmp_path_factory):  # noqa: F811
    return {
        "MASCOPE_URL": BASE_URL,
        "MASCOPE_ACCESS_TOKEN": TOKEN,
        "MASCOPE_WORKSPACE": client.workspace_id,
        "MASCOPE_VIZ_STORE": str(tmp_path_factory.mktemp("stores")),
        # Set here, so that the runner puts it back after each command
        cli.LOG_LEVEL: cli.QUIET,
    }


@pytest.fixture(scope="module")
def run(environment):
    def run(*args: str, code: int = 0) -> dict:
        result = CliRunner().invoke(cli.app, [*args, "--json"], env=environment)
        assert result.exit_code == code, (result.stdout, result.stderr)
        assert result.stdout.isascii()
        return json.loads(result.stdout)

    return run


@pytest.fixture(scope="module")
def scope(dataset) -> list[str]:  # noqa: F811
    return ["--dataset", str(dataset["dataset_id"])]


@pytest.fixture(scope="module")
def synced(run, scope):
    return run("store", "sync", *scope, "--pause", "0")


def test_scopes_lists_the_demo_batches(client, dataset, run, scope):  # noqa: F811
    found = run("scopes", *scope, "--limit", "0")
    resolved = resolve(client, Scope(dataset=dataset["dataset_id"]))
    assert found["ambiguous"] is False
    assert found["key"] == resolved.key
    assert [b["sample_batch_id"] for b in found["batches"]] == list(
        resolved.batches["sample_batch_id"]
    )
    listed = client.batches.list(dataset["dataset_id"])
    assert {b["sample_batch_id"] for b in found["batches"]} <= set(
        listed["sample_batch_id"]
    )
    assert found["n_samples"] == int(resolved.batches["n_samples"].sum())
    assert found["n_files"] == len(resolved.files)


def test_sync_then_status_reports_the_batches_cached(client, run, scope, synced):  # noqa: F811
    assert synced["failed"] == {}
    assert synced["complete"] is True
    status = run("store", "status", *scope, "--limit", "0")
    assert status["exists"] is True
    assert status["complete"] is True
    assert {b["state"] for b in status["batches"]} == {"synced"}
    assert {b["sample_batch_id"] for b in status["batches"]} == set(synced["fetched"])
    assert run("scopes", *scope)["store"]["synced"] is True


def test_view_series_prints_a_summary_and_no_table(
    client,  # noqa: F811
    dataset,  # noqa: F811
    environment,
    scope,
    synced,
):
    resolved = resolve(client, Scope(dataset=dataset["dataset_id"]))
    store = CampaignStore(
        store_path(client, resolved, environment["MASCOPE_VIZ_STORE"])
    )
    assert store.exists()
    ions = store.table("ion_series").dropna(subset=["intensity"])
    if ions.empty:
        pytest.skip("the demo dataset's batches match no target ion")
    key = str(ions["series_key"].value_counts().index[0])

    args = ["view", "series", *scope, "--keys", key, "--keys", "tic"]
    result = CliRunner().invoke(cli.app, [*args, "--json"], env=environment)
    assert result.exit_code == 0, result.stderr
    found = json.loads(result.stdout)
    frame = views.series(store, [key, "tic"])
    assert found["rows"] == len(frame)
    assert [entry["key"] for entry in found["entries"]] == [key, "tic"]
    for entry in found["entries"]:
        values = frame.loc[frame["key"] == entry["key"], "value"]
        assert entry["n"] == values.notna().sum()
        assert entry["max"] == pytest.approx(values.max())

    shown = CliRunner().invoke(cli.app, args, env=environment)
    assert shown.exit_code == 0
    assert shown.stdout.isascii()
    lines = shown.stdout.splitlines()
    # What it is, the span, a header and a line per key: no bin of the frame
    assert len(lines) == 6
    assert lines[0].startswith(f"view series of store {found['key']}: ")


@pytest.mark.parametrize("name", cli.VIEWS)
def test_every_view_prints_json(run, scope, synced, name):
    options = ["--keys", "tic"] if name in ("series", "diurnal", "periods") else []
    found = run("view", name, *scope, *options)
    assert found["view"] == name
    assert found["rows"] >= 0
    if name == "coverage":
        assert found["n_files"] > 0
    assert pd.Timestamp(found["store"]["synced_utc"]) <= pd.Timestamp.now(tz="UTC")


def test_rm_deletes_the_demo_store(run, scope, synced):
    removed = run("store", "rm", *scope)
    assert removed["removed"] is True and removed["size_bytes"] > 0
    assert run("store", "status", *scope)["exists"] is False
