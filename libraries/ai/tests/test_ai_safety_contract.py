"""The plan/apply safety scenarios (``ai_safety.py``) against a live demo
stack, in scratch workspaces.

Opt-in, as ``test_ai_plans_contract.py``: a running demo stack and
``MASCOPE_SDK_CONTRACT=1``. The world is built over a session of the demo
user (two scratch workspaces with look-alike names, a copy of a demo batch
in each, a collection with compounds on the home copy, one with injected
text in its description) and every scenario runs through the toolkit with
an ``ai-agent`` token. The demo data is never written to. Afterwards the
scratch workspaces' collections are deleted, then the workspaces, which
deletes what else is in them.

Two scenarios the fake server alone holds: a workspace the token is not a
member of (the demo stack has one user), and a write the server is made to
fail part-way.
"""

import os
import secrets
import time

import ai_safety
import pytest
from ai_safety import SCENARIOS, World, collateral

from mascope_ai import Session, Toolkit


BASE_URL = os.environ.get("MASCOPE_SDK_TEST_URL", "http://127.0.0.1:8080")
TOKEN = os.environ.get("MASCOPE_SDK_TEST_TOKEN", "mascope_demo_sdk_token")
EMAIL = os.environ.get("MASCOPE_SDK_TEST_EMAIL", "demo@mascope.app")
PASSWORD = os.environ.get("MASCOPE_SDK_TEST_PASSWORD", "mascope-demo")
#: The SDK's write tests' prefix: the suites' workspace pickers skip it.
SCRATCH_PREFIX = "sdk-contract-"

pytestmark = pytest.mark.skipif(
    os.environ.get("MASCOPE_SDK_CONTRACT") != "1",
    reason=(
        "contract tests need a running demo stack; start one "
        "(docker compose -f docker-compose.demo.yaml up -d) and set "
        "MASCOPE_SDK_CONTRACT=1"
    ),
)


def _signed_in():
    import requests

    session = requests.Session()
    session.post(
        f"{BASE_URL}/api/auth/login",
        data={"username": EMAIL, "password": PASSWORD},
        timeout=30,
    ).raise_for_status()
    return session


def _api(session, method: str, path: str, whole: bool = False, **kwargs):
    response = session.request(method, f"{BASE_URL}/api/{path}", timeout=60, **kwargs)
    response.raise_for_status()
    body = response.json()
    return body if whole else body["data"]


def _eventually(probe, timeout: float = 180):
    deadline = time.monotonic() + timeout
    while True:
        result = probe()
        if result:
            return result
        if time.monotonic() > deadline:
            raise AssertionError(f"still waiting after {timeout} s")
        time.sleep(2)


def _demo_batch() -> dict:
    """The first demo batch of two samples or more outside the scratch
    workspaces, by name, read with the demo stack's SDK token (the one the
    other contract suites read with).

    By name, because the server lists in no set order: a demo bundle
    rebuilt from its raw files draws new IDs, and with them which batch a
    listing gives first. The copy has to hold two samples to be ready
    (:func:`_copy_ready`), and some demo batches hold one.
    """
    import requests

    headers = {"Authorization": f"Bearer {TOKEN}", "X-Service-Name": "mascope_sdk"}

    def get(path: str, key: str, **params):
        response = requests.get(
            f"{BASE_URL}/api/{path}", headers=headers, params=params, timeout=30
        )
        response.raise_for_status()
        return sorted(response.json().get("data") or (), key=lambda row: row[key])

    for workspace in get("workspaces", "workspace_name"):
        if workspace["workspace_name"].startswith(SCRATCH_PREFIX):
            continue
        datasets = get(
            f"workspaces/{workspace['workspace_id']}/datasets", "dataset_name"
        )
        for dataset in datasets:
            batches = get(
                "sample/batches", "sample_batch_name", dataset_id=dataset["dataset_id"]
            )
            for batch in batches:
                samples = get(
                    "samples",
                    "sample_item_id",
                    sample_batch_id=batch["sample_batch_id"],
                )
                if len(samples) >= 2:
                    return batch
    pytest.fail("the demo stack has no batch of two samples")


def _settled(kit: Toolkit) -> bool:
    """Whether the world reads the same twice, two seconds apart.

    The server finishes some of what the world's writes start after they
    return (a new collection's compounds, the collections a batch copy
    takes), and a scenario whose first snapshot falls in that window reports
    the server's own work as its collateral.
    """
    first = ai_safety.snapshot(kit)
    time.sleep(2)
    return ai_safety.snapshot(kit) == first


def _copy_ready(session, dataset_id: str, name: str) -> dict | None:
    for batch in _api(
        session, "GET", "sample/batches", params={"dataset_id": dataset_id}
    ):
        if batch["sample_batch_name"] == name and batch["status"] == "ready":
            samples = _api(
                session,
                "GET",
                "samples",
                params={"sample_batch_id": batch["sample_batch_id"]},
            )
            if len(samples) >= 2:
                return {**batch, "samples": samples}
    return None


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    """The world on the demo stack, and a toolkit on an ``ai-agent`` token
    in its home workspace."""
    from types import SimpleNamespace

    from mascope_sdk import MascopeClient

    session = _signed_in()
    tag = secrets.token_hex(4)
    # Two names differing in a word, sharing the tag the query names
    home_name = f"{SCRATCH_PREFIX}ai-{tag} Larch Coatings"
    other_name = f"{SCRATCH_PREFIX}ai-{tag} Larch Coatings Nordic"
    workspaces = []
    try:
        for name in (home_name, other_name):
            workspaces.append(
                _api(session, "POST", "workspaces", json={"workspace_name": name})
            )
        home, other = (w["workspace_id"] for w in workspaces)
        dataset = _api(
            session,
            "POST",
            f"workspaces/{home}/datasets",
            json={"dataset_name": "plans"},
        )["dataset_id"]
        spare = _api(
            session,
            "POST",
            f"workspaces/{home}/datasets",
            json={"dataset_name": "to move"},
        )["dataset_id"]
        elsewhere = _api(
            session,
            "POST",
            f"workspaces/{other}/datasets",
            json={"dataset_name": "theirs"},
        )["dataset_id"]
        demo = _demo_batch()
        for into in (dataset, elsewhere):
            session.post(
                f"{BASE_URL}/api/sample/batches/{demo['sample_batch_id']}/copy",
                json={"dataset_id": into, "sample_batch_name": "copy"},
                timeout=60,
            ).raise_for_status()
        made = {}
        for name, into in (("receiving", dataset), ("movable", dataset)):
            made[name] = _api(
                session,
                "POST",
                "sample/batches",
                json={
                    "dataset_id": into,
                    "sample_batch_name": name,
                    "target_collection_ids": [],
                },
            )["sample_batch_id"]
        copy = _eventually(lambda: _copy_ready(session, dataset, "copy"))
        other_copy = _eventually(lambda: _copy_ready(session, elsewhere, "copy"))
        collection = _api(
            session,
            "POST",
            "target/collections",
            json={
                "target_collection_name": "monitor",
                "workspace_id": home,
                "target_compounds_create": [
                    {"target_compound_formula": "C10H16O", "target_compound_name": "x"},
                    {"target_compound_formula": "C9H12O", "target_compound_name": ""},
                    {"target_compound_formula": "C13H24O4", "target_compound_name": ""},
                ],
                "sample_batch_ids": [copy["sample_batch_id"]],
            },
        )["target_collection_id"]
        injected = _api(
            session,
            "POST",
            "target/collections",
            json={
                "target_collection_name": "notes",
                "workspace_id": home,
                "target_collection_description": ai_safety.INJECTED_TEXT,
                "target_compounds_create": [
                    {
                        "target_compound_formula": "C6H6O",
                        "target_compound_name": "phenol",
                    }
                ],
            },
        )["target_collection_id"]
        token = _api(
            session,
            "POST",
            "auth/access_token/regenerate",
            json={"service_name": "ai-agent"},
            whole=True,
        )["access_token"]
        client = MascopeClient(
            url=BASE_URL, access_token=token, workspace=home, service_name="ai-agent"
        )
        kit = Toolkit(
            client, session=Session(root=tmp_path_factory.mktemp("safety-sessions"))
        )
        _eventually(lambda: _settled(kit))
        world = World(
            home=home,
            home_name=home_name,
            other=other,
            other_name=other_name,
            lookalike=f"{tag} Larch Coatings",
            dataset=dataset,
            spare_dataset=spare,
            batch=copy["sample_batch_id"],
            receiving_batch=made["receiving"],
            movable_batch=made["movable"],
            other_batch=other_copy["sample_batch_id"],
            other_sample=other_copy["samples"][0]["sample_item_id"],
            other_dataset=elsewhere,
            collection=collection,
            injected=injected,
        )
        yield SimpleNamespace(kit=kit, client=client, world=world)
    finally:
        # Deleting a workspace does not delete the collections made in it, so
        # the world's, and those the scenarios applied, are deleted first.
        scratch = {w["workspace_id"] for w in workspaces}
        try:
            listed = _api(session, "GET", "target/collections")
        except Exception:
            listed = []
        for row in listed:
            if row.get("workspace_id") in scratch:
                session.delete(
                    f"{BASE_URL}/api/target/collections/{row['target_collection_id']}",
                    timeout=60,
                )
        for workspace in workspaces:
            session.delete(
                f"{BASE_URL}/api/workspaces/{workspace['workspace_id']}", timeout=60
            )


@pytest.mark.parametrize(
    "target", [each for each in SCENARIOS if each.contract], ids=lambda t: t.id
)
def test_the_toolkit_blocks_it_and_nothing_else_changes(stack, target):
    stack.client.clear_cache()
    assert collateral(stack.kit, target, stack.world) == []
