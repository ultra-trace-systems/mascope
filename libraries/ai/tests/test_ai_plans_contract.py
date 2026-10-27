"""Plans and ``apply`` against a live demo stack, in scratch workspaces.

Opt-in, as ``test_ai_contract.py``: a running demo stack and
``MASCOPE_SDK_CONTRACT=1``. What a token may not do - make workspaces and
batches, and mint the token itself - goes over a session of the demo user
(``MASCOPE_SDK_TEST_EMAIL`` / ``MASCOPE_SDK_TEST_PASSWORD``), as the SDK's
write contract tests do; every plan is applied through the toolkit with an
``ai-agent`` token, which the server refuses under any other service name.
The demo data is never written to: the batch the plans work on is a copy.
Deleting the scratch workspaces afterwards deletes what is in them.
"""

import os
import secrets
import time

import pytest

from mascope_ai import (
    Move,
    PlanConsumed,
    PlanDrifted,
    Session,
    Toolkit,
)


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


def _eventually(client, probe, timeout: float = 120):
    """What *probe* returns once it is truthy, for a write the server runs
    in the background; the client's cache is cleared before each probe."""
    deadline = time.monotonic() + timeout
    while True:
        client.clear_cache()
        result = probe()
        if result is not None and not getattr(result, "empty", not result):
            return result
        if time.monotonic() > deadline:
            raise AssertionError(f"still waiting after {timeout} s")
        time.sleep(1)


def _demo_batch(session) -> dict:
    """A demo batch with samples, read over the session."""
    headers = {"Authorization": f"Bearer {TOKEN}", "X-Service-Name": "mascope_sdk"}
    import requests

    for workspace in requests.get(
        f"{BASE_URL}/api/workspaces", headers=headers, timeout=30
    ).json()["data"]:
        if workspace["workspace_name"].startswith(SCRATCH_PREFIX):
            continue
        datasets = requests.get(
            f"{BASE_URL}/api/workspaces/{workspace['workspace_id']}/datasets",
            headers=headers,
            timeout=30,
        ).json()
        for dataset in datasets.get("data") or ():
            batches = requests.get(
                f"{BASE_URL}/api/sample/batches",
                params={"dataset_id": dataset["dataset_id"]},
                headers=headers,
                timeout=30,
            ).json()
            for batch in batches.get("data") or ():
                return batch
    pytest.fail("the demo stack has no batch")


@pytest.fixture(scope="module")
def scratch(tmp_path_factory):
    """Two scratch workspaces, a dataset in the first with a copy of a demo
    batch and an empty batch, and a toolkit on an ``ai-agent`` token."""
    from types import SimpleNamespace

    from mascope_sdk import MascopeClient

    session = _signed_in()
    tag = secrets.token_hex(4)
    workspaces = []
    try:
        for name in ("a", "b"):
            workspaces.append(
                _api(
                    session,
                    "POST",
                    "workspaces",
                    json={"workspace_name": f"{SCRATCH_PREFIX}ai-{tag}-{name}"},
                )
            )
        home, other = (w["workspace_id"] for w in workspaces)
        dataset = _api(
            session,
            "POST",
            f"workspaces/{home}/datasets",
            json={"dataset_name": "plans"},
        )
        spare = _api(
            session,
            "POST",
            f"workspaces/{home}/datasets",
            json={"dataset_name": "to move"},
        )
        empty = _api(
            session,
            "POST",
            "sample/batches",
            json={
                "dataset_id": dataset["dataset_id"],
                "sample_batch_name": "empty",
                "target_collection_ids": [],
            },
        )
        demo = _demo_batch(session)
        session.post(
            f"{BASE_URL}/api/sample/batches/{demo['sample_batch_id']}/copy",
            json={"dataset_id": dataset["dataset_id"], "sample_batch_name": "copy"},
            timeout=60,
        ).raise_for_status()
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

        def copied():
            batches = client.batches._list_by_id(dataset["dataset_id"])
            if batches is None:
                return None
            ready = batches[
                (batches["sample_batch_name"] == "copy")
                & (batches["status"] == "ready")
            ]
            if ready.empty:
                return None
            samples = client.samples._list_by_id(ready.iloc[0]["sample_batch_id"])
            if samples is None or len(samples) < 2:
                return None
            return ready.iloc[0].to_dict()

        copy = _eventually(client, copied)
        kit = Toolkit(
            client, session=Session(root=tmp_path_factory.mktemp("plan-sessions"))
        )
        yield SimpleNamespace(
            session=session,
            kit=kit,
            client=client,
            home=home,
            other=other,
            dataset_id=dataset["dataset_id"],
            spare_id=spare["dataset_id"],
            copy_id=copy["sample_batch_id"],
            empty_id=empty["sample_batch_id"],
        )
    finally:
        for workspace in workspaces:
            session.delete(
                f"{BASE_URL}/api/workspaces/{workspace['workspace_id']}", timeout=60
            )


def _approve(kit, plan) -> str:
    return kit.approver.approve(plan, irreversible=plan.gate == "hard")


def _samples(scratch, batch_id: str) -> list[str]:
    scratch.client.clear_cache()
    rows = scratch.client.samples._list_by_id(batch_id)
    return [] if rows is None else rows["sample_item_id"].tolist()


def test_a_dataset_move_applies_once(scratch):
    kit = scratch.kit
    plan = kit.plan_organize([Move(scratch.spare_id, scratch.other, kind="dataset")])
    token = _approve(kit, plan)

    receipt = kit.apply(plan.id, token)

    assert receipt.complete and receipt.service_name == "ai-agent"
    assert [(r.id, r.parent) for r in receipt.results] == [
        (scratch.spare_id, scratch.other)
    ]
    scratch.client.clear_cache()
    assert scratch.spare_id not in scratch.client.datasets.list()["dataset_id"].tolist()
    assert kit.apply(plan.id, token).repeated
    with pytest.raises(PlanConsumed):
        kit.apply(plan.id, _approve(kit, plan))


def test_a_sample_move_whose_sample_moved_meanwhile_is_refused(scratch):
    kit = scratch.kit
    first, second = _samples(scratch, scratch.copy_id)[:2]
    plan = kit.plan_organize(
        [
            Move(first, scratch.empty_id, kind="sample"),
            Move(second, scratch.empty_id, kind="sample"),
        ]
    )
    # Someone moves the first sample underneath, through the SDK directly.
    # The server makes a moved sample anew, under another ID
    scratch.client.samples.move([first], scratch.empty_id)
    _eventually(scratch.client, lambda: first not in _samples(scratch, scratch.copy_id))

    with pytest.raises(PlanDrifted) as drifted:
        kit.apply(plan.id, _approve(kit, plan))

    (diff,) = drifted.value.diff
    assert diff["ref"]["id"] == first
    assert (diff["planned_before"], diff["now"]) == (scratch.copy_id, None)
    assert second in _samples(scratch, scratch.copy_id)

    moved = len(_samples(scratch, scratch.empty_id))
    again = kit.plan_organize([Move(second, scratch.empty_id, kind="sample")])
    receipt = kit.apply(again.id, _approve(kit, again))
    assert receipt.complete and receipt.responses[0].get("message")
    assert [r.id for r in receipt.results] == [scratch.empty_id]
    _eventually(
        scratch.client,
        lambda: (
            len(_samples(scratch, scratch.empty_id)) == moved + 1
            and second not in _samples(scratch, scratch.copy_id)
        ),
    )


def test_a_collection_is_created_and_associated(scratch):
    kit = scratch.kit
    name = f"plans {secrets.token_hex(3)}"
    created = kit.apply(
        (plan := kit.plan_collection(name, ["CH2O2", "C2H4O2"])).id, _approve(kit, plan)
    )
    (collection,) = created.results
    assert collection.name == name and collection.parent == scratch.home

    plan = kit.plan_associate(collection, [scratch.copy_id, scratch.empty_id])
    kit.apply(plan.id, _approve(kit, plan))
    record = scratch.client.collections.get(collection.id)
    assert {b["sample_batch_id"] for b in record["sample_batches"]} == {
        scratch.copy_id,
        scratch.empty_id,
    }

    plan = kit.plan_associate(collection, detach=[scratch.empty_id])
    assert plan.changes[0].after == [scratch.copy_id]
    kit.apply(plan.id, _approve(kit, plan))
    record = scratch.client.collections.get(collection.id)
    assert [b["sample_batch_id"] for b in record["sample_batches"]] == [scratch.copy_id]


def test_matching_previews_freely_and_saves_behind_the_hard_gate(scratch):
    kit = scratch.kit
    preview = kit.plan_match(scratch.copy_id)
    assert preview.gate == "none"
    assert set(preview.preview) == {
        "match_ions",
        "match_compounds",
        "match_collections",
        "match_samples",
    }

    save = kit.plan_match(scratch.copy_id, save=True)
    assert save.gate == "hard" and not save.reversible
    assert "sample(s)" in save.estimated_cost
    receipt = kit.apply(save.id, _approve(kit, save))
    assert receipt.complete and receipt.service_name == "ai-agent"
