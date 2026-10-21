"""
resolve() against a live demo stack: the published demo dataset resolves.

Opt-in, with the same switch and settings as the SDK's contract tests
(``libraries/sdk/tests/test_contract.py``): a running demo stack and
``MASCOPE_SDK_CONTRACT=1``; ``MASCOPE_SDK_TEST_URL``,
``MASCOPE_SDK_TEST_TOKEN`` and ``MASCOPE_SDK_TEST_WORKSPACE`` point it
elsewhere. Locally::

    docker compose -f docker-compose.demo.yaml up -d
    MASCOPE_SDK_CONTRACT=1 uv run pytest libraries/viz/tests/ -v

The scope comes from the demo data itself and the assertions are on shapes
and agreement with the SDK's own listings, not on values, so they survive
bundle updates.
"""

import os

import pandas as pd
import pytest

from mascope_viz.data import Scope, resolve


BASE_URL = os.environ.get("MASCOPE_SDK_TEST_URL", "http://127.0.0.1:8080")
TOKEN = os.environ.get("MASCOPE_SDK_TEST_TOKEN", "mascope_demo_sdk_token")

pytestmark = pytest.mark.skipif(
    os.environ.get("MASCOPE_SDK_CONTRACT") != "1",
    reason=(
        "contract tests need a running demo stack; start one "
        "(docker compose -f docker-compose.demo.yaml up -d) and set "
        "MASCOPE_SDK_CONTRACT=1"
    ),
)


def _workspace_with_data() -> str | None:
    """The first workspace with datasets (the demo stack also has a system
    workspace, so the client cannot pick one by itself)."""
    import requests

    override = os.environ.get("MASCOPE_SDK_TEST_WORKSPACE")
    if override:
        return override
    headers = {"Authorization": f"Bearer {TOKEN}", "X-Service-Name": "mascope_sdk"}
    workspaces = requests.get(
        f"{BASE_URL}/api/workspaces", headers=headers, timeout=30
    ).json()["data"]
    for workspace in workspaces:
        datasets = requests.get(
            f"{BASE_URL}/api/workspaces/{workspace['workspace_id']}/datasets",
            headers=headers,
            timeout=30,
        ).json()
        if datasets.get("data"):
            return workspace["workspace_name"]
    return workspaces[0]["workspace_name"] if workspaces else None


@pytest.fixture(scope="module")
def client():
    from mascope_sdk import MascopeClient

    return MascopeClient(
        url=BASE_URL,
        access_token=TOKEN,
        workspace=_workspace_with_data(),
        service_name="mascope_sdk",
    )


@pytest.fixture(scope="module")
def dataset(client) -> pd.Series:
    """The demo's first dataset that has batches."""
    for row in client.datasets.list().itertuples():
        batches = client.batches.list(row.dataset_id)
        if batches is not None and not batches.empty:
            return pd.Series(row._asdict())
    pytest.fail("the demo stack has no dataset with batches")


@pytest.fixture(scope="module")
def resolved(client, dataset):
    return resolve(client, Scope(dataset=dataset["dataset_id"]))


def test_the_demo_dataset_resolves_to_its_batches(client, dataset, resolved):
    assert not resolved.ambiguous
    listed = client.batches.list(dataset["dataset_id"])
    assert not resolved.batches.empty
    assert set(resolved.batches["sample_batch_id"]) <= set(listed["sample_batch_id"])
    assert (resolved.batches["n_samples"] > 0).all()
    assert resolved.batches["first_datetime_utc"].is_monotonic_increasing
    assert (
        resolved.batches["first_datetime_utc"] <= resolved.batches["last_datetime_utc"]
    ).all()


def test_the_demo_batches_files_are_listed_in_them(resolved):
    assert not resolved.files.empty
    assert resolved.files["datetime_utc"].is_monotonic_increasing
    assert resolved.files["in_batch"].any()


def test_a_name_resolves_as_its_id_does(client, dataset, resolved):
    by_name = resolve(client, Scope(dataset=dataset["dataset_name"]))

    if by_name.ambiguous:
        found = by_name.candidates["dataset"]
        assert dataset["dataset_id"] in {candidate.id for candidate in found}
    else:
        assert by_name.key == resolved.key


def test_a_range_of_the_first_batch_selects_it(client, resolved):
    first = resolved.batches.iloc[0]
    scope = Scope(
        dataset=resolved.scope.dataset,
        start=first["first_datetime_utc"],
        end=first["last_datetime_utc"] + pd.Timedelta(seconds=1),
    )

    ranged = resolve(client, scope)

    assert first["sample_batch_id"] in set(ranged.batches["sample_batch_id"])
    assert (ranged.files["datetime_utc"] >= first["first_datetime_utc"]).all()
