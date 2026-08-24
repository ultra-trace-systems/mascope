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
- A named collection must be readable by the caller, but only when the request
  *changes* it: the client echoes the mode's current bindings on every save,
  so checking those by value would make a mode bound to a workspace-scoped
  collection uneditable outside that workspace. ``private_bound_mode`` covers
  the echo case; ``unbound_mode`` covers the first-binding case.

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


# ===== Only ids the request changes are checked =====
#
# The client sends the mode's whole current state back on every PATCH, so a
# by-value check turned any pre-existing binding to a workspace-scoped
# collection into a permanent lock: nobody outside that workspace - global
# admins included, since ``_enforce`` bypasses on ``is_superuser`` alone -
# could rename the mode or touch its mechanisms. Re-stating a binding grants
# nothing the mode does not already publish, so it is compared against the
# stored value and skipped when unchanged.


@pytest_asyncio.fixture(scope="session")
async def private_diagnostic_collection(async_session_factory, ion_dataset):
    """A diagnostics collection in the same members-less workspace.

    Lets a single request name one unreadable collection it is echoing and a
    different unreadable one it is not, which is what pins the check to the
    individual field rather than to the request as a whole.
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
                target_collection_name="Private Diagnostics (ion-mode-test)",
                target_collection_type="DIAGNOSTICS",
                workspace_id=workspace_id,
            )
        )
        await session.commit()
    return collection_id


@pytest_asyncio.fixture
async def private_bound_mode(async_session_factory, mechanisms, private_collection):
    """A mode already bound to a collection no test client can read.

    Stands in for a mode bound before the read check existed, and for one bound
    by a member of the owning workspace afterwards - the two ways a mode ends
    up naming a collection its next editor cannot see.
    """
    mode_id = gen_id()
    name = f"Private Bound Mode {mode_id}"
    async with async_session_factory() as session:
        session.add(
            IonizationMode(
                ionization_mode_id=mode_id,
                ionization_mode_name=name,
                ionization_mode_token=None,
                ionization_mode_polarity="+",
                ionization_mechanism_ids=[mechanisms["a"]],
                calibration_collection_id=private_collection,
                diagnostic_collection_id=None,
            )
        )
        await session.commit()
    return {
        "mode_id": mode_id,
        "name": name,
        "mech_a": mechanisms["a"],
        "mech_b": mechanisms["b"],
        "calibration": private_collection,
    }


@pytest_asyncio.fixture
async def unbound_mode(async_session_factory, mechanisms):
    """A mode with no calibration collection at all.

    Exercises the ``stored is None`` side of the diff: there is nothing to
    echo, so the first id the request names is a new grant and must be
    checked like any other.
    """
    mode_id = gen_id()
    name = f"Unbound Mode {mode_id}"
    async with async_session_factory() as session:
        session.add(
            IonizationMode(
                ionization_mode_id=mode_id,
                ionization_mode_name=name,
                ionization_mode_token=None,
                ionization_mode_polarity="+",
                ionization_mechanism_ids=[mechanisms["a"]],
                calibration_collection_id=None,
                diagnostic_collection_id=None,
            )
        )
        await session.commit()
    return {"mode_id": mode_id, "name": name, "mech_a": mechanisms["a"]}


def _private_body(ctx, **overrides):
    """A full PATCH body echoing ``private_bound_mode``'s current state."""
    body = {
        "ionization_mode_name": ctx["name"],
        "ionization_mode_polarity": "+",
        "ionization_mechanism_ids": [ctx["mech_a"]],
        "calibration_collection_id": ctx["calibration"],
    }
    body.update(overrides)
    return body


@pytest.mark.asyncio
async def test_unchanged_unreadable_binding_does_not_block_an_edit(
    editor_client, private_bound_mode
):
    """Renaming a mode does not require access to the collection it keeps.

    The request echoes the stored calibration id, as the pane does, and changes
    only the mechanisms. Checking that echoed id by value made every mode bound
    to a workspace collection uneditable outside that workspace - which,
    because ``_enforce`` bypasses only on ``is_superuser``, means a global
    admin who is not a member is refused like anyone else. Only an id that
    *differs* from the stored one is a new grant.
    """
    resp = await editor_client.patch(
        f"/api/ionization/modes/{private_bound_mode['mode_id']}",
        json=_private_body(
            private_bound_mode,
            ionization_mechanism_ids=[private_bound_mode["mech_b"]],
        ),
    )
    assert resp.status_code == 200
    # The binding it could not read is preserved, not silently dropped.
    assert (
        resp.json()["data"]["calibration_collection_id"]
        == private_bound_mode["calibration"]
    )


@pytest.mark.asyncio
async def test_editor_can_move_a_mode_off_an_unreadable_collection(
    editor_client, private_bound_mode, collections
):
    """Repairing a bad binding needs access to the incoming collection only.

    Gating the *outgoing* id would leave a mode stuck on an unreadable
    collection unrepairable by anyone but a superuser, and unbinding only
    narrows what the mode publishes. Passes before this change too - the
    outgoing id was never in the payload. It is here to pin the design
    decision, so a later "also check what is being unbound" does not silently
    land.
    """
    resp = await editor_client.patch(
        f"/api/ionization/modes/{private_bound_mode['mode_id']}",
        json=_private_body(
            private_bound_mode, calibration_collection_id=collections["cal_b"]
        ),
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["calibration_collection_id"] == collections["cal_b"]


@pytest.mark.asyncio
async def test_echoing_one_binding_does_not_licence_naming_another(
    editor_client, private_bound_mode, private_diagnostic_collection
):
    """The skip is per field, not per request.

    Echoing the calibration id the mode already has must not carry an
    unreadable *diagnostic* collection in with it. Also 403 before this change,
    for the other reason (the echoed calibration id was checked). It earns its
    place by failing an over-broad fix that exempts a whole request once any
    field matches.
    """
    resp = await editor_client.patch(
        f"/api/ionization/modes/{private_bound_mode['mode_id']}",
        json=_private_body(
            private_bound_mode,
            diagnostic_collection_id=private_diagnostic_collection,
        ),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_first_binding_on_an_unbound_mode_is_still_checked(
    editor_client, unbound_mode, private_collection
):
    """A NULL stored binding exempts nothing.

    ``stored`` reports ``None`` for a field the mode has never had, so the
    incoming id differs and is checked. Guards against a diff that reads a
    missing stored value as "unchanged".
    """
    resp = await editor_client.patch(
        f"/api/ionization/modes/{unbound_mode['mode_id']}",
        json={
            "ionization_mode_name": unbound_mode["name"],
            "ionization_mode_polarity": "+",
            "ionization_mechanism_ids": [unbound_mode["mech_a"]],
            "calibration_collection_id": private_collection,
        },
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_editor_cannot_create_a_mode_on_an_unreadable_collection(
    editor_client, mechanisms, private_collection
):
    """Create has no stored state, so every id it names is checked.

    Also 403 before this change. It is the only test of
    ``POST /api/ionization/modes`` in the suite, so it is coverage of the
    create path rather than proof of this fix.
    """
    resp = await editor_client.post(
        "/api/ionization/modes",
        json={
            "ionization_mode_name": f"New Mode {gen_id()}",
            "ionization_mode_polarity": "+",
            "ionization_mechanism_ids": [mechanisms["a"]],
            "calibration_collection_id": private_collection,
        },
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_unknown_mode_still_answers_not_found(editor_client, collections):
    """Reading the stored ids must not turn a missing mode into a 500.

    The update route now reads the mode's current bindings before the service
    does its own lookup. That read has to tolerate no row - naming a readable
    collection on an id that does not exist stays a 404 from the service, not
    a ``NoResultFound`` leaking out as a 500.
    """
    resp = await editor_client.patch(
        f"/api/ionization/modes/{gen_id()}",
        json={
            "ionization_mode_name": f"Ghost Mode {gen_id()}",
            "ionization_mode_polarity": "+",
            "ionization_mechanism_ids": [gen_id()],
            "calibration_collection_id": collections["cal_a"],
        },
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_unknown_mode_naming_an_unreadable_collection_is_refused(
    editor_client, private_collection
):
    """No stored bindings means every named id counts as new.

    Fails closed: the caller is refused on the collection before the missing
    mode is ever looked up, so a 403 here rather than the 404 the service
    would give. It leaks nothing - the refusal depends only on the
    collection, which the caller genuinely cannot read.
    """
    resp = await editor_client.patch(
        f"/api/ionization/modes/{gen_id()}",
        json={
            "ionization_mode_name": f"Ghost Mode {gen_id()}",
            "ionization_mode_polarity": "+",
            "ionization_mechanism_ids": [gen_id()],
            "calibration_collection_id": private_collection,
        },
    )
    assert resp.status_code == 403
