"""Integration tests: an ionization mode is a versioned setup.

An edit that changes what a run computes from a mode - its polarity, its
mechanisms, either collection - inserts the next version and marks the edited
one inactive; the samples bound under it keep it. The name, the token and the
instrument scope are edited in place. What routes new files follows the new
version (the method bindings move with the edit; the listing and every rung
see current versions only), and a re-process moves its samples onto the
current version before it reads their mode.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text

from mascope_backend.api.controllers.calibration import calibration_controller
from mascope_backend.api.controllers.match import match_controller
from mascope_backend.api.controllers.sample.files.process import service as process
from mascope_backend.api.new.ionization.modes import service as modes_service
from mascope_backend.api.new.ionization.modes.versions import (
    current_versions,
    lineage,
    move_to_current_versions,
    root_ids,
)
from mascope_backend.api.new.peak_assignments import service as assignments
from mascope_backend.db import (
    Dataset,
    IonizationMechanism,
    IonizationMode,
    MethodBinding,
    Sample,
    SampleBatch,
    SampleFile,
    SampleItem,
    TargetCollection,
    Workspace,
)
from mascope_backend.db.id import gen_id


_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
_NOW_NAIVE = datetime(2026, 1, 1)


class _Stop(Exception):
    """Raised by a stub where a test has seen what it came for."""


@pytest_asyncio.fixture(scope="module", autouse=True)
async def sample_view(async_engine):
    """The re-process entry points read samples through ``sample_view``.

    ``create_all`` does not make the view (``tests/integration/api/match/
    conftest.py`` says why), so it is made here as there.
    """
    async with async_engine.begin() as conn:
        await conn.execute(text(Sample.drop_view()))
        await conn.execute(text(Sample.create_view()))


@pytest_asyncio.fixture(scope="module")
async def world(async_session_factory):
    """A dataset, a file, mechanisms of both polarities and two of each collection."""
    ids = {
        "workspace": gen_id(),
        "dataset": gen_id(),
        "file": gen_id(),
        "mech_a": gen_id(),
        "mech_b": gen_id(),
        "mech_neg": gen_id(),
        **{key: gen_id() for key in ("cal_a", "cal_b", "diag_a", "diag_b")},
    }
    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=ids["workspace"],
                workspace_name=f"Versions {ids['workspace']}",
                workspace_status="active",
                workspace_utc_created=_NOW,
                workspace_utc_modified=_NOW,
            )
        )
        session.add(
            Dataset(
                dataset_id=ids["dataset"],
                workspace_id=ids["workspace"],
                dataset_name="Versions",
                dataset_type="ANALYSIS",
                dataset_utc_created=_NOW,
            )
        )
        session.add(
            SampleFile(
                sample_file_id=ids["file"],
                filename=f"test-orbion_{ids['file']}.raw",
                instrument="test-orbion",
                datetime=_NOW_NAIVE,
                datetime_utc=_NOW,
                length=60.0,
                range={"min": 0, "max": 500},
                polarity="+",
            )
        )
        for key, polarity, notation in (
            ("mech_a", "+", "[M+H]+ (versions)"),
            ("mech_b", "+", "[M+Na]+ (versions)"),
            ("mech_neg", "-", "[M-H]- (versions)"),
        ):
            session.add(
                IonizationMechanism(
                    ionization_mechanism_id=ids[key],
                    ionization_mechanism_polarity=polarity,
                    ionization_mechanism=notation,
                )
            )
        for key, kind in (
            ("cal_a", "CALIBRANTS"),
            ("cal_b", "CALIBRANTS"),
            ("diag_a", "DIAGNOSTICS"),
            ("diag_b", "DIAGNOSTICS"),
        ):
            session.add(
                TargetCollection(
                    target_collection_id=ids[key],
                    target_collection_name=f"{key} (versions {ids[key]})",
                    target_collection_type=kind,
                    workspace_id=None,
                )
            )
        await session.commit()
    return ids


@pytest_asyncio.fixture
async def mode(async_session_factory, world):
    """A mode with one sample bound under it, in a batch of its own."""
    ctx = {
        "mode_id": gen_id(),
        "batch_id": gen_id(),
        "item_id": gen_id(),
        "name": f"Versioned {gen_id(6)}",
        "token": f"tok{gen_id(6)}",
    }
    async with async_session_factory() as session:
        session.add(
            IonizationMode(
                ionization_mode_id=ctx["mode_id"],
                ionization_mode_name=ctx["name"],
                ionization_mode_token=ctx["token"],
                ionization_mode_polarity="+",
                ionization_mechanism_ids=[world["mech_a"]],
                calibration_collection_id=world["cal_a"],
                diagnostic_collection_id=world["diag_a"],
            )
        )
        session.add(
            SampleBatch(
                sample_batch_id=ctx["batch_id"],
                dataset_id=world["dataset"],
                sample_batch_name=f"Versions batch {ctx['batch_id']}",
                sample_batch_utc_created=_NOW,
            )
        )
        session.add(
            SampleItem(
                sample_item_id=ctx["item_id"],
                sample_batch_id=ctx["batch_id"],
                sample_file_id=world["file"],
                sample_item_name="Versioned item",
                sample_item_type="ANALYSIS",
                sample_item_attributes={},
                polarity="+",
                tic=1000.0,
                t0=0.0,
                t1=60.0,
                ionization_mode_id=ctx["mode_id"],
                sample_item_utc_created=_NOW,
            )
        )
        await session.commit()
    yield ctx
    async with async_session_factory() as session:
        await session.execute(
            delete(SampleBatch).where(SampleBatch.sample_batch_id == ctx["batch_id"])
        )
        await session.execute(
            delete(MethodBinding).where(
                MethodBinding.binding_key.startswith(f"versions-{ctx['mode_id']}")
            )
        )
        versions = await lineage(session, ctx["mode_id"])
        await session.execute(
            delete(IonizationMode).where(
                IonizationMode.ionization_mode_id.in_(versions)
            )
        )
        await session.commit()


def _body(world, mode, **changes):
    body = {
        "ionization_mode_name": mode["name"],
        "ionization_mode_token": mode["token"],
        "ionization_mode_polarity": "+",
        "ionization_mechanism_ids": [world["mech_a"]],
        "calibration_collection_id": world["cal_a"],
        "diagnostic_collection_id": world["diag_a"],
    }
    body.update(changes)
    return body


async def _row(async_session_factory, mode_id) -> IonizationMode:
    async with async_session_factory() as session:
        return await session.get(IonizationMode, mode_id)


async def _item_mode(async_session_factory, item_id) -> str:
    async with async_session_factory() as session:
        return (await session.get(SampleItem, item_id)).ionization_mode_id


async def _mint(admin_client, world, mode, **changes) -> str:
    resp = await admin_client.patch(
        f"/api/ionization/modes/{mode['mode_id']}",
        json=_body(world, mode, **changes),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["ionization_mode_id"]


_VERSIONED_EDITS = {
    "mechanisms": lambda w: {"ionization_mechanism_ids": [w["mech_a"], w["mech_b"]]},
    "polarity": lambda w: {
        "ionization_mode_polarity": "-",
        "ionization_mechanism_ids": [w["mech_neg"]],
    },
    "calibration collection": lambda w: {"calibration_collection_id": w["cal_b"]},
    "diagnostic collection": lambda w: {"diagnostic_collection_id": w["diag_b"]},
}


# --- what an edit does ------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("edit", list(_VERSIONED_EDITS))
async def test_an_edit_of_what_a_run_computes_makes_the_next_version(
    admin_client, async_session_factory, world, mode, edit
):
    changes = _VERSIONED_EDITS[edit](world)
    new_id = await _mint(admin_client, world, mode, **changes)

    assert new_id != mode["mode_id"]
    new = await _row(async_session_factory, new_id)
    old = await _row(async_session_factory, mode["mode_id"])
    assert (new.version, new.is_active, new.supersedes_id) == (2, True, mode["mode_id"])
    for field, value in changes.items():
        assert getattr(new, field) == value
    # The edited version is kept as it was, for the sample bound under it.
    assert old.is_active is False
    assert old.ionization_mechanism_ids == [world["mech_a"]]
    assert old.calibration_collection_id == world["cal_a"]
    assert await _item_mode(async_session_factory, mode["item_id"]) == mode["mode_id"]
    # What the edit did not touch carries over.
    assert new.ionization_mode_name == mode["name"]
    assert new.ionization_mode_token == mode["token"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"ionization_mode_name": "Renamed in place"},
        {"ionization_mode_token": "anothertoken"},
        {"instrument": "ORBI-VERSIONS"},
    ],
    ids=["name", "token", "scope"],
)
async def test_a_name_token_or_scope_edit_is_made_in_place(
    admin_client, async_session_factory, world, mode, changes
):
    if "ionization_mode_token" in changes:
        changes = {"ionization_mode_token": f"other{gen_id(6)}"}
    resp = await admin_client.patch(
        f"/api/ionization/modes/{mode['mode_id']}",
        json=_body(world, mode, **changes),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["ionization_mode_id"] == mode["mode_id"]
    row = await _row(async_session_factory, mode["mode_id"])
    assert (row.version, row.is_active) == (1, True)
    for field, value in changes.items():
        assert getattr(row, field) == value


@pytest.mark.asyncio
async def test_an_edit_that_changes_nothing_makes_no_version(
    admin_client, async_session_factory, world, mode
):
    """The browser echoes the whole mode back; an unchanged echo is no edit."""
    resp = await admin_client.patch(
        f"/api/ionization/modes/{mode['mode_id']}", json=_body(world, mode)
    )
    assert resp.json()["data"]["ionization_mode_id"] == mode["mode_id"]
    assert (await _row(async_session_factory, mode["mode_id"])).is_active is True


@pytest.mark.asyncio
async def test_the_browser_is_told_one_row_left_and_another_came(
    admin_client, world, mode, monkeypatch
):
    created, updated, deleted = AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(modes_service, "emit_record_created", created)
    monkeypatch.setattr(modes_service, "emit_record_updated", updated)
    monkeypatch.setattr(modes_service, "emit_record_deleted", deleted)

    new_id = await _mint(
        admin_client, world, mode, **_VERSIONED_EDITS["mechanisms"](world)
    )

    deleted.assert_awaited_once_with(
        record_type="ionization_mode", record_id=mode["mode_id"]
    )
    record = created.await_args.kwargs["record"]
    assert created.await_args.kwargs["record_id"] == new_id
    assert record["superseded_ids"] == [mode["mode_id"]]
    updated.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_edit_in_place_is_an_update_carrying_the_history(
    admin_client, world, mode, monkeypatch
):
    updated = AsyncMock()
    monkeypatch.setattr(modes_service, "emit_record_updated", updated)
    monkeypatch.setattr(modes_service, "emit_record_created", AsyncMock())
    monkeypatch.setattr(modes_service, "emit_record_deleted", AsyncMock())
    new_id = await _mint(
        admin_client, world, mode, **_VERSIONED_EDITS["mechanisms"](world)
    )

    resp = await admin_client.patch(
        f"/api/ionization/modes/{new_id}",
        json=_body(
            world,
            mode,
            ionization_mode_name="Renamed v2",
            **_VERSIONED_EDITS["mechanisms"](world),
        ),
    )
    assert resp.status_code == 200, resp.text
    assert updated.await_args.kwargs["record"]["superseded_ids"] == [mode["mode_id"]]


@pytest.mark.asyncio
async def test_the_edit_moves_the_method_bindings(
    admin_client, async_session_factory, world, mode
):
    """What routes new files follows the setup to its new version."""
    async with async_session_factory() as session:
        for suffix, column in (
            ("bound", "ionization_mode_id"),
            ("cand", "candidate_mode_id"),
        ):
            session.add(
                MethodBinding(
                    method_binding_id=gen_id(),
                    binding_key=f"versions-{mode['mode_id']}-{suffix}",
                    instrument="test-orbion",
                    method_key="m.meth",
                    signature_class="FTMS +",
                    chemistry_keys=["k"],
                    state="learned",
                    source="token",
                    first_seen=_NOW,
                    last_seen=_NOW,
                    **{column: mode["mode_id"]},
                )
            )
        await session.commit()

    new_id = await _mint(
        admin_client, world, mode, **_VERSIONED_EDITS["mechanisms"](world)
    )

    async with async_session_factory() as session:
        rows = {
            row.binding_key.rsplit("-", 1)[-1]: row
            for row in (
                await session.execute(
                    select(MethodBinding).where(
                        MethodBinding.binding_key.startswith(
                            f"versions-{mode['mode_id']}"
                        )
                    )
                )
            ).scalars()
        }
    assert rows["bound"].ionization_mode_id == new_id
    assert rows["cand"].candidate_mode_id == new_id


@pytest.mark.asyncio
async def test_a_superseded_version_is_neither_edited_nor_deleted(
    admin_client, world, mode
):
    await _mint(admin_client, world, mode, **_VERSIONED_EDITS["mechanisms"](world))

    edit = await admin_client.patch(
        f"/api/ionization/modes/{mode['mode_id']}",
        json=_body(world, mode, ionization_mode_name="Too late"),
    )
    remove = await admin_client.delete(f"/api/ionization/modes/{mode['mode_id']}")
    assert edit.status_code == 400
    assert "Edit the current version" in edit.text
    assert remove.status_code == 400
    assert "Delete the current version" in remove.text


# --- what reads the modes -----------------------------------------------------


@pytest.mark.asyncio
async def test_the_listing_offers_current_versions_with_their_history(
    admin_client, world, mode
):
    new_id = await _mint(
        admin_client, world, mode, **_VERSIONED_EDITS["mechanisms"](world)
    )

    listed = {
        row["ionization_mode_id"]: row
        for row in (await admin_client.get("/api/ionization/modes")).json()["data"]
    }
    assert mode["mode_id"] not in listed
    assert listed[new_id]["superseded_ids"] == [mode["mode_id"]]
    assert listed[new_id]["version"] == 2

    # A superseded version is still read by id: a sample names it.
    old = (await admin_client.get(f"/api/ionization/modes/{mode['mode_id']}")).json()
    assert old["data"]["is_active"] is False


@pytest.mark.asyncio
async def test_routing_reads_current_versions_only(admin_client, world, mode):
    new_id = await _mint(
        admin_client, world, mode, **_VERSIONED_EDITS["mechanisms"](world)
    )

    routed = {
        m.ionization_mode_id for m in await modes_service.fetch_all_ionization_modes()
    }
    assert new_id in routed
    assert mode["mode_id"] not in routed


@pytest.mark.asyncio
async def test_a_name_and_token_left_on_a_superseded_version_are_free(
    admin_client, world, mode
):
    new_id = await _mint(
        admin_client, world, mode, **_VERSIONED_EDITS["mechanisms"](world)
    )
    renamed = await admin_client.patch(
        f"/api/ionization/modes/{new_id}",
        json=_body(
            world,
            mode,
            ionization_mode_name=f"{mode['name']} renamed",
            ionization_mode_token=f"other{gen_id(6)}",
            **_VERSIONED_EDITS["mechanisms"](world),
        ),
    )
    assert renamed.status_code == 200, renamed.text

    created = await admin_client.post(
        "/api/ionization/modes",
        json={
            "ionization_mode_name": mode["name"],
            "ionization_mode_token": mode["token"],
            "ionization_mode_polarity": "+",
            "ionization_mechanism_ids": [world["mech_a"]],
        },
    )
    assert created.status_code == 201, created.text
    await admin_client.delete(
        f"/api/ionization/modes/{created.json()['data']['ionization_mode_id']}"
    )


@pytest.mark.asyncio
async def test_delete_takes_every_version_once_no_sample_holds_one(
    admin_client, async_session_factory, world, mode
):
    new_id = await _mint(
        admin_client, world, mode, **_VERSIONED_EDITS["mechanisms"](world)
    )

    # The sample bound under the first version holds the whole setup.
    refused = await admin_client.delete(f"/api/ionization/modes/{new_id}")
    assert refused.status_code == 400
    assert "associated with existing samples" in refused.text

    async with async_session_factory() as session:
        await session.execute(
            delete(SampleItem).where(SampleItem.sample_item_id == mode["item_id"])
        )
        await session.commit()
    removed = await admin_client.delete(f"/api/ionization/modes/{new_id}")
    assert removed.status_code == 200, removed.text
    assert await _row(async_session_factory, new_id) is None
    assert await _row(async_session_factory, mode["mode_id"]) is None


@pytest.mark.asyncio
async def test_version_helpers_walk_the_chain(
    admin_client, async_session_factory, world, mode
):
    second = await _mint(
        admin_client, world, mode, **_VERSIONED_EDITS["mechanisms"](world)
    )
    third = await _mint(
        admin_client,
        world,
        {**mode, "mode_id": second},
        **_VERSIONED_EDITS["calibration collection"](world),
        ionization_mechanism_ids=[world["mech_a"], world["mech_b"]],
    )
    async with async_session_factory() as session:
        assert await lineage(session, mode["mode_id"]) == [
            third,
            second,
            mode["mode_id"],
        ]
        assert await root_ids(session, [third, second]) == {
            third: mode["mode_id"],
            second: mode["mode_id"],
        }
        current = await current_versions(session, [mode["mode_id"], second, None])
    assert {key: row.ionization_mode_id for key, row in current.items()} == {
        mode["mode_id"]: third,
        second: third,
    }


# --- a re-process moves its samples onto the current version ----------------


@pytest.mark.asyncio
async def test_moving_a_batch_moves_its_items(
    admin_client, async_session_factory, world, mode
):
    new_id = await _mint(
        admin_client, world, mode, **_VERSIONED_EDITS["mechanisms"](world)
    )

    assert await move_to_current_versions(sample_batch_ids=[gen_id()]) == 0
    assert await move_to_current_versions(sample_batch_ids=[mode["batch_id"]]) == 1
    assert await _item_mode(async_session_factory, mode["item_id"]) == new_id


@pytest.mark.asyncio
async def test_a_sample_rematch_moves_the_sample_first(
    admin_client, async_session_factory, world, mode, monkeypatch
):
    new_id = await _mint(
        admin_client, world, mode, **_VERSIONED_EDITS["mechanisms"](world)
    )
    seen = {}

    async def removal(**kwargs):
        seen["mode"] = await _item_mode(async_session_factory, kwargs["sample_item_id"])
        raise _Stop

    monkeypatch.setattr(match_controller, "match_remove_sample", removal)
    # The controller reports what it caught as an ApiException.
    with pytest.raises(Exception):  # noqa: B017, PT011
        await match_controller.rematch_sample(mode["item_id"])
    assert seen["mode"] == new_id


@pytest.mark.asyncio
async def test_a_batch_rematch_moves_its_samples_first(
    admin_client, async_session_factory, world, mode, monkeypatch
):
    new_id = await _mint(
        admin_client, world, mode, **_VERSIONED_EDITS["mechanisms"](world)
    )
    seen = {}

    async def removal(**kwargs):
        seen["mode"] = await _item_mode(async_session_factory, mode["item_id"])
        raise _Stop

    monkeypatch.setattr(match_controller, "match_remove_batch", removal)
    monkeypatch.setattr(
        match_controller, "send_progress_user_notification", AsyncMock()
    )
    # The controller reports what it caught as an ApiException.
    with pytest.raises(Exception):  # noqa: B017, PT011
        await match_controller.rematch_batch(
            mode["batch_id"], force=True, process_id=gen_id(8)
        )
    assert seen["mode"] == new_id


@pytest.mark.asyncio
async def test_a_recalibration_moves_the_sample_first(
    admin_client, async_session_factory, world, mode, monkeypatch
):
    new_id = await _mint(
        admin_client, world, mode, **_VERSIONED_EDITS["calibration collection"](world)
    )
    seen = {}

    async def fit(*args, **kwargs):
        seen["mode"] = await _item_mode(async_session_factory, mode["item_id"])
        raise _Stop

    monkeypatch.setattr(calibration_controller, "calibration_mz_fit", fit)
    monkeypatch.setattr(
        calibration_controller, "send_progress_user_notification", AsyncMock()
    )
    with pytest.raises(Exception):  # noqa: B017, PT011
        await calibration_controller.calibration_mz_calibrate_sample(
            mode["item_id"],
            calibration_controller.MzCalibrationParams(refine_window=10),
        )
    assert seen["mode"] == new_id


@pytest.mark.asyncio
async def test_an_assignment_run_is_created_under_the_current_version(
    admin_client, async_session_factory, world, mode
):
    new_id = await _mint(
        admin_client, world, mode, **_VERSIONED_EDITS["mechanisms"](world)
    )

    await assignments.create_pending_run(mode["item_id"])
    assert await _item_mode(async_session_factory, mode["item_id"]) == new_id


@pytest.mark.asyncio
async def test_an_assignment_run_moves_the_sample_before_reading_it(
    admin_client, async_session_factory, world, mode, monkeypatch
):
    new_id = await _mint(
        admin_client, world, mode, **_VERSIONED_EDITS["mechanisms"](world)
    )
    seen = {}

    async def fetch(sample_item_id):
        seen["mode"] = await _item_mode(async_session_factory, sample_item_id)
        raise _Stop

    monkeypatch.setattr(assignments, "fetch_sample", fetch)
    with pytest.raises(_Stop):
        await assignments._run_sample_assignment(mode["item_id"])
    assert seen["mode"] == new_id


@pytest.mark.asyncio
async def test_a_file_is_bound_and_reprocessed_under_current_versions(
    admin_client, world, mode
):
    new_id = await _mint(
        admin_client, world, mode, **_VERSIONED_EDITS["mechanisms"](world)
    )

    current = await process.current_ionization_modes([mode["mode_id"], new_id])
    assert {key: row.ionization_mode_id for key, row in current.items()} == {
        mode["mode_id"]: new_id,
        new_id: new_id,
    }
    assert [
        m.ionization_mode_id
        for m in await process.fetch_ionization_modes([mode["mode_id"], new_id])
    ] == [new_id]
    with pytest.raises(ValueError, match="No ionization mode has the id"):
        await process.current_ionization_modes([gen_id()])
