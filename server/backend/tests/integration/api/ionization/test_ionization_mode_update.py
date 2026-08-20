"""
Tests: editing ionization modes via ``PATCH /api/ionization/modes/{id}``.

Covers the editable-ionization-mode changes:
- Editing (PATCH) and deleting (DELETE) a mode are editor level, matching
  creation and the instrument-config surface; guests remain read-only.
- The calibration / diagnostic collection of a mode may be changed to another
  collection (previously only allowed when not yet defined), but cannot be
  cleared back to null.
- Affected batches (those containing samples using the mode) are flagged:
  ``recalibrate`` when the calibration collection changes, otherwise
  ``rematch`` when mechanisms or the diagnostic collection change.

The mode endpoints are gated purely on the global role and have no workspace
ACL, so the global ``guest_client`` / ``editor_client`` / ``admin_client``
fixtures from ``tests/integration/api/conftest.py`` are sufficient. The
behavioural tests below still drive the endpoint as an admin, which remains
permitted since the roles are hierarchical.
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from mascope_backend.db import (
    Dataset,
    IonizationMechanism,
    IonizationMode,
    SampleBatch,
    SampleFile,
    SampleItem,
    TargetCollection,
    Workspace,
)
from mascope_backend.db.id import gen_id


_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
_NOW_NAIVE = datetime(2026, 1, 1)


# ---------------------------------------------------------------------------
# Shared reference data (workspace/dataset/file, mechanisms, collections)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def ion_dataset(async_session_factory):
    """A workspace + dataset to host the mode's affected batches."""
    workspace_id = gen_id()
    dataset_id = gen_id()
    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=workspace_id,
                workspace_name="Ionization Workspace",
                workspace_status="active",
                workspace_utc_created=_NOW,
                workspace_utc_modified=_NOW,
            )
        )
        session.add(
            Dataset(
                dataset_id=dataset_id,
                workspace_id=workspace_id,
                dataset_name="Ionization Dataset",
                dataset_type="ANALYSIS",
                dataset_utc_created=_NOW,
            )
        )
        await session.commit()
    return dataset_id


@pytest_asyncio.fixture(scope="session")
async def ion_sample_file(async_session_factory):
    """A sample file referenced by the mode's affected sample item."""
    file_id = gen_id()
    async with async_session_factory() as session:
        session.add(
            SampleFile(
                sample_file_id=file_id,
                filename=f"test-orbion_{file_id}.raw",
                instrument="test-orbion",
                datetime=_NOW_NAIVE,
                datetime_utc=_NOW,
                length=60.0,
                range={"min": 0, "max": 500},
                polarity="+",
            )
        )
        await session.commit()
    return file_id


@pytest_asyncio.fixture(scope="session")
async def mechanisms(async_session_factory):
    """Two positive-polarity ionization mechanisms."""
    ids = {"a": gen_id(), "b": gen_id()}
    async with async_session_factory() as session:
        session.add(
            IonizationMechanism(
                ionization_mechanism_id=ids["a"],
                ionization_mechanism_polarity="+",
                ionization_mechanism="+H+ (ion-mode-test)",
            )
        )
        session.add(
            IonizationMechanism(
                ionization_mechanism_id=ids["b"],
                ionization_mechanism_polarity="+",
                ionization_mechanism="+Na+ (ion-mode-test)",
            )
        )
        await session.commit()
    return ids


@pytest_asyncio.fixture(scope="session")
async def collections(async_session_factory):
    """Two calibration and two diagnostic collections to swap between."""
    ids = {key: gen_id() for key in ("cal_a", "cal_b", "diag_a", "diag_b")}
    specs = [
        ("cal_a", "CALIBRANTS", "Calibrants A (ion-mode-test)"),
        ("cal_b", "CALIBRANTS", "Calibrants B (ion-mode-test)"),
        ("diag_a", "DIAGNOSTICS", "Diagnostics A (ion-mode-test)"),
        ("diag_b", "DIAGNOSTICS", "Diagnostics B (ion-mode-test)"),
    ]
    async with async_session_factory() as session:
        for key, tc_type, name in specs:
            session.add(
                TargetCollection(
                    target_collection_id=ids[key],
                    target_collection_name=name,
                    target_collection_type=tc_type,
                    workspace_id=None,
                )
            )
        await session.commit()
    return ids


@pytest_asyncio.fixture(scope="session")
async def private_collection(async_session_factory, ion_dataset):
    """A calibrants collection scoped to a workspace with no members.

    ``ion_dataset``'s workspace has no membership rows, so no test client can
    read this collection - which is what makes it usable for the negative case
    below.
    """
    collection_id = gen_id()
    async with async_session_factory() as session:
        workspace_id = (
            await session.execute(
                select(Dataset.workspace_id).where(Dataset.dataset_id == ion_dataset)
            )
        ).scalar_one()
        session.add(
            TargetCollection(
                target_collection_id=collection_id,
                target_collection_name="Private Calibrants (ion-mode-test)",
                target_collection_type="CALIBRANTS",
                workspace_id=workspace_id,
            )
        )
        await session.commit()
    return collection_id


@pytest_asyncio.fixture
async def mode_ctx(
    async_session_factory, ion_dataset, ion_sample_file, mechanisms, collections
):
    """A fresh ionization mode plus a batch+item using it (per test).

    The mode starts with mechanism ``a``, calibration ``cal_a`` and diagnostic
    ``diag_a``. The batch contains one sample item referencing the mode, so it
    is an "affected batch" that update flagging can target. Function-scoped so
    each test mutates an isolated mode/batch.
    """
    mode_id = gen_id()
    batch_id = gen_id()
    item_id = gen_id()
    name = f"Test Mode {mode_id}"
    async with async_session_factory() as session:
        session.add(
            IonizationMode(
                ionization_mode_id=mode_id,
                ionization_mode_name=name,
                ionization_mode_token=None,
                ionization_mode_polarity="+",
                ionization_mechanism_ids=[mechanisms["a"]],
                calibration_collection_id=collections["cal_a"],
                diagnostic_collection_id=collections["diag_a"],
            )
        )
        session.add(
            SampleBatch(
                sample_batch_id=batch_id,
                dataset_id=ion_dataset,
                sample_batch_name=f"Mode Batch {batch_id}",
                sample_batch_utc_created=_NOW,
            )
        )
        session.add(
            SampleItem(
                sample_item_id=item_id,
                sample_batch_id=batch_id,
                sample_file_id=ion_sample_file,
                sample_item_name="Mode Item",
                sample_item_type="ANALYSIS",
                sample_item_attributes={},
                polarity="+",
                tic=1000.0,
                t0=0.0,
                t1=60.0,
                ionization_mode_id=mode_id,
                sample_item_utc_created=_NOW,
            )
        )
        await session.commit()
    return {
        "mode_id": mode_id,
        "batch_id": batch_id,
        "name": name,
        "mech_a": mechanisms["a"],
        "mech_b": mechanisms["b"],
        "cal_a": collections["cal_a"],
        "cal_b": collections["cal_b"],
        "diag_a": collections["diag_a"],
        "diag_b": collections["diag_b"],
    }


def _body(ctx, **overrides):
    """Build a full (valid) PATCH body reflecting the mode's current state.

    ``ionization_mode_name``, ``ionization_mode_polarity`` and
    ``ionization_mechanism_ids`` are required by ``IonizationModeUpdate``, so
    every PATCH must include them; ``overrides`` change individual fields.
    """
    body = {
        "ionization_mode_name": ctx["name"],
        "ionization_mode_polarity": "+",
        "ionization_mechanism_ids": [ctx["mech_a"]],
        "calibration_collection_id": ctx["cal_a"],
        "diagnostic_collection_id": ctx["diag_a"],
    }
    body.update(overrides)
    return body


async def _batch_status(async_session_factory, batch_id):
    async with async_session_factory() as session:
        batch = await session.get(SampleBatch, batch_id)
        return batch.status


# ============= The collections a mode may name =============


@pytest.mark.asyncio
async def test_editor_cannot_bind_an_unreadable_collection(
    editor_client, mode_ctx, private_collection
):
    """A mode may not be pointed at a collection the caller cannot read.

    Modes are global reference data: every workspace processing a sample under
    one reads the collections it names. Binding a collection only the editor
    can see would publish it through the mode, which is not theirs to grant.
    """
    resp = await editor_client.patch(
        f"/api/ionization/modes/{mode_ctx['mode_id']}",
        json=_body(mode_ctx, calibration_collection_id=private_collection),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_editor_can_bind_a_global_collection(editor_client, mode_ctx):
    """An ordinary editor is not blocked from a global collection.

    The read check has to be at guest level for this to hold:
    ``check_target_collection_access`` demands the global *admin* role for any
    higher bar on a collection with no workspace, which would lock the editors
    who are supposed to manage modes out of every ordinary swap.
    """
    resp = await editor_client.patch(
        f"/api/ionization/modes/{mode_ctx['mode_id']}",
        json=_body(mode_ctx, calibration_collection_id=mode_ctx["cal_b"]),
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_collection_used_by_a_mode_cannot_be_narrowed(
    owner_client, async_session_factory, mode_ctx, ion_dataset
):
    """The same rule from the other side, so it cannot simply be walked around.

    Without this, the check above buys nothing: bind a global collection to the
    mode, then pull that collection into your own workspace. Refused even for a
    superuser, because the objection is not about the caller's access - it is
    that the rest of the instance still matches against the collection through
    the mode.
    """
    async with async_session_factory() as session:
        workspace_id = (
            await session.execute(
                select(Dataset.workspace_id).where(Dataset.dataset_id == ion_dataset)
            )
        ).scalar_one()

    resp = await owner_client.patch(
        f"/api/target/collections/{mode_ctx['cal_a']}",
        json={"workspace_id": workspace_id},
    )
    assert resp.status_code == 409


# ============= Editor level (PATCH / DELETE) =============


@pytest.mark.asyncio
async def test_editor_can_update_mode(editor_client, mode_ctx):
    """Editors can edit an ionization mode, as they can create one."""
    resp = await editor_client.patch(
        f"/api/ionization/modes/{mode_ctx['mode_id']}", json=_body(mode_ctx)
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_editor_can_delete_mode(editor_client, mode_ctx):
    """Editors are not refused the delete endpoint on role grounds.

    ``mode_ctx`` builds a mode that samples reference, which the endpoint
    refuses on business grounds (400) whatever the caller's role. That is
    exactly what makes it a usable authorization test: reaching the 400 proves
    the request got past the role check, which is the part under test here.
    Asserted as the exact status rather than "not 403", so a 500 or a moved
    route cannot pass it by accident.
    """
    resp = await editor_client.delete(f"/api/ionization/modes/{mode_ctx['mode_id']}")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_guest_cannot_update_mode(guest_client, mode_ctx):
    """Guests remain read-only on shared reference data."""
    resp = await guest_client.patch(
        f"/api/ionization/modes/{mode_ctx['mode_id']}", json=_body(mode_ctx)
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_guest_cannot_delete_mode(guest_client, mode_ctx):
    """Guests remain read-only on shared reference data."""
    resp = await guest_client.delete(f"/api/ionization/modes/{mode_ctx['mode_id']}")
    assert resp.status_code == 403


# ============= Changing calibration / diagnostic collection =============


@pytest.mark.asyncio
async def test_admin_change_calibration_flags_recalibrate(
    admin_client, async_session_factory, mode_ctx
):
    """Changing the calibration collection succeeds and flags 'recalibrate'."""
    resp = await admin_client.patch(
        f"/api/ionization/modes/{mode_ctx['mode_id']}",
        json=_body(mode_ctx, calibration_collection_id=mode_ctx["cal_b"]),
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["calibration_collection_id"] == mode_ctx["cal_b"]
    assert await _batch_status(async_session_factory, mode_ctx["batch_id"]) == (
        "recalibrate"
    )


@pytest.mark.asyncio
async def test_admin_change_diagnostic_flags_rematch(
    admin_client, async_session_factory, mode_ctx
):
    """Changing the diagnostic collection succeeds and flags 'rematch'."""
    resp = await admin_client.patch(
        f"/api/ionization/modes/{mode_ctx['mode_id']}",
        json=_body(mode_ctx, diagnostic_collection_id=mode_ctx["diag_b"]),
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["diagnostic_collection_id"] == mode_ctx["diag_b"]
    assert await _batch_status(async_session_factory, mode_ctx["batch_id"]) == "rematch"


@pytest.mark.asyncio
async def test_admin_change_mechanisms_flags_rematch(
    admin_client, async_session_factory, mode_ctx
):
    """Changing mechanisms (calibration unchanged) flags 'rematch'."""
    resp = await admin_client.patch(
        f"/api/ionization/modes/{mode_ctx['mode_id']}",
        json=_body(
            mode_ctx, ionization_mechanism_ids=[mode_ctx["mech_a"], mode_ctx["mech_b"]]
        ),
    )
    assert resp.status_code == 200
    assert await _batch_status(async_session_factory, mode_ctx["batch_id"]) == "rematch"


@pytest.mark.asyncio
async def test_admin_cannot_clear_calibration_collection(
    admin_client, async_session_factory, mode_ctx
):
    """Clearing the calibration collection to null is ignored (preserved)."""
    resp = await admin_client.patch(
        f"/api/ionization/modes/{mode_ctx['mode_id']}",
        json=_body(mode_ctx, calibration_collection_id=None),
    )
    assert resp.status_code == 200
    # Collection is preserved, not cleared...
    assert resp.json()["data"]["calibration_collection_id"] == mode_ctx["cal_a"]
    # ...and nothing changed, so the batch is not flagged.
    assert await _batch_status(async_session_factory, mode_ctx["batch_id"]) == "ready"
