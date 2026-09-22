"""
Integration tests: choosing the chemistry of files that need one.

A file whose name carries no token of a configured mode is parked with the
status ``needs_chemistry`` and no samples. ``POST /api/sample/files/bind``
processes such files under the ionization modes a person chose: each file is
bound to the chosen mode of each polarity it holds, and claimed first, so a
second choice cannot start a second run. A file that has samples already is
rebuilt under the chosen modes; one with a sample a person made from it is
refused, as is one the chosen modes do not fit. Re-processing, and
processing a file on request, keep the modes a file was bound to that way,
since no token binds it again.

The pipeline itself is stubbed: what is pinned is which files start, under
which modes, and what is refused.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from mascope_backend.api.controllers.sample.files.process import (
    service as process_service,
)
from mascope_backend.api.controllers.sample.files.process import status
from mascope_backend.api.routes.sample.files import sample_files_routes
from mascope_backend.db import (
    Dataset,
    IonizationMode,
    SampleBatch,
    SampleFile,
    SampleItem,
    Workspace,
    WorkspaceMember,
)
from mascope_backend.db.id import gen_id


INSTRUMENT = "bind-orbi"
WORKSPACE = f"Acquisitions {INSTRUMENT}"
_NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def setup(async_session_factory, test_users):
    """The instrument's workspace with a dataset, and a mode per polarity.

    The editor may edit the workspace and the guest may only read it.
    """
    ids = {
        "workspace": gen_id(16),
        "dataset": gen_id(16),
        "batch": gen_id(16),
        "negative": gen_id(16),
        "positive": gen_id(16),
    }
    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=ids["workspace"],
                workspace_name=WORKSPACE,
                workspace_status="active",
                is_system=True,
            )
        )
        await session.flush()
        for role in ("editor", "guest"):
            session.add(
                WorkspaceMember(
                    workspace_member_id=gen_id(16),
                    workspace_id=ids["workspace"],
                    user_id=test_users[role].id,
                    workspace_role=role,
                )
            )
        session.add(
            Dataset(
                dataset_id=ids["dataset"],
                workspace_id=ids["workspace"],
                dataset_name="2026",
                dataset_type="ACQUISITION",
                instrument=INSTRUMENT,
                dataset_utc_created=_NOW,
            )
        )
        session.add(
            SampleBatch(
                sample_batch_id=ids["batch"],
                dataset_id=ids["dataset"],
                sample_batch_name="2026-09-01 Bind acquisition",
                sample_batch_type="ACQUISITION",
                polarity="-",
                sample_batch_utc_created=_NOW,
            )
        )
        for polarity, key in (("-", "negative"), ("+", "positive")):
            session.add(
                IonizationMode(
                    ionization_mode_id=ids[key],
                    ionization_mode_name=f"Bind {key} {ids[key]}",
                    ionization_mode_token=None,
                    ionization_mode_polarity=polarity,
                    ionization_mechanism_ids=[],
                )
            )
        await session.commit()
    yield ids
    async with async_session_factory() as session:
        await session.execute(
            delete(SampleFile).where(SampleFile.instrument == INSTRUMENT)
        )
        await session.execute(
            delete(Workspace).where(Workspace.workspace_id == ids["workspace"])
        )
        await session.execute(
            delete(Workspace).where(Workspace.workspace_name.like("Bind analysis %"))
        )
        await session.execute(
            delete(IonizationMode).where(
                IonizationMode.ionization_mode_id.in_(
                    [ids["negative"], ids["positive"]]
                )
            )
        )
        await session.commit()


@pytest.fixture(autouse=True)
def quiet(monkeypatch) -> None:
    """Nothing is sent to browsers."""
    monkeypatch.setattr(status, "emit_record_updated", AsyncMock())


@pytest.fixture
def spawn(monkeypatch) -> AsyncMock:
    """Record which pipelines start instead of starting them."""
    stub = AsyncMock(return_value=None)
    monkeypatch.setattr(process_service, "spawn_auto_process_sample_file", stub)
    return stub


async def _user_sample(async_session_factory, test_users, sample_file_id: str) -> str:
    """A sample a person made from the file, in a batch of their own.

    Typed ACQUISITION, as the create dialog lets a person type it, so only
    where it lives tells it from the pipeline's own.
    """
    ids = {"workspace": gen_id(16), "dataset": gen_id(16), "batch": gen_id(16)}
    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=ids["workspace"],
                workspace_name=f"Bind analysis {ids['workspace']}",
                workspace_status="active",
                is_system=False,
            )
        )
        await session.flush()
        session.add(
            Dataset(
                dataset_id=ids["dataset"],
                workspace_id=ids["workspace"],
                dataset_name="Mine",
                dataset_type="ANALYSIS",
                dataset_utc_created=_NOW,
            )
        )
        session.add(
            SampleBatch(
                sample_batch_id=ids["batch"],
                dataset_id=ids["dataset"],
                sample_batch_name="Mine",
                sample_batch_type="ANALYSIS",
                polarity="+-",
                sample_batch_utc_created=_NOW,
            )
        )
        sample_item_id = gen_id()
        session.add(
            SampleItem(
                sample_item_id=sample_item_id,
                sample_batch_id=ids["batch"],
                sample_file_id=sample_file_id,
                sample_item_name="Mine",
                sample_item_type="ACQUISITION",
                sample_item_attributes={},
                polarity="-",
                sample_item_utc_created=_NOW,
            )
        )
        await session.commit()
    return sample_item_id


async def _status(async_session_factory, sample_file_id: str) -> str | None:
    async with async_session_factory() as session:
        return await session.scalar(
            select(SampleFile.processing_status).where(
                SampleFile.sample_file_id == sample_file_id
            )
        )


async def _set_status(async_session_factory, sample_file_id: str, value: str) -> None:
    async with async_session_factory() as session:
        row = await session.get(SampleFile, sample_file_id)
        row.processing_status = value
        await session.commit()


async def _file(
    async_session_factory,
    name: str,
    polarity: str,
    sample_under: tuple[str, str] | None = None,
) -> str:
    """A file with no token in its name.

    With ``sample_under`` - a batch and a mode - it has an ACQUISITION sample
    under that mode, as a file bound by hand does once processed.
    """
    sample_file_id = gen_id()
    async with async_session_factory() as session:
        session.add(
            SampleFile(
                sample_file_id=sample_file_id,
                filename=f"{INSTRUMENT}_{name}.raw",
                instrument=INSTRUMENT,
                instrument_type="orbi",
                datetime=datetime(2026, 9, 1, 12, 0, 0),
                datetime_utc=_NOW,
                length=60.0,
                range=[50.0, 500.0],
                polarity=polarity,
                processing_status="needs_chemistry" if sample_under is None else "done",
            )
        )
        await session.flush()
        if sample_under is not None:
            batch_id, mode_id = sample_under
            session.add(
                SampleItem(
                    sample_item_id=gen_id(),
                    sample_batch_id=batch_id,
                    sample_file_id=sample_file_id,
                    sample_item_name="2026-09-01 12:00:00",
                    sample_item_type="ACQUISITION",
                    sample_item_attributes={},
                    polarity=polarity,
                    ionization_mode_id=mode_id,
                    sample_item_utc_created=_NOW,
                )
            )
        await session.commit()
    return sample_file_id


def _started(spawn: AsyncMock) -> dict[str, list[str]]:
    """The files started, with the modes each was started under."""
    return {
        call.kwargs["sample_file_id"]: call.kwargs["ionization_mode_ids"]
        for call in spawn.await_args_list
    }


# ---------------------------------------------------------------------------
# Binding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_file_is_bound_to_the_chosen_mode_of_each_polarity(
    async_session_factory, setup, spawn, editor_client, test_users
):
    both = await _file(async_session_factory, "both", "+-")

    resp = await editor_client.post(
        "/api/sample/files/bind",
        json={
            "sample_file_ids": [both],
            "ionization_mode_ids": [setup["negative"], setup["positive"]],
        },
    )

    assert resp.status_code == 202, resp.text
    assert resp.json()["data"] == {"started": [both], "refused": []}
    # In the file's own polarity order.
    assert _started(spawn) == {both: [setup["positive"], setup["negative"]]}
    call = spawn.await_args
    assert call.kwargs["instrument"] == INSTRUMENT
    assert call.kwargs["user_id"] == test_users["editor"].id
    assert call.kwargs["independent_transaction"] is True
    assert call.kwargs["reset_calibration"] is True
    # Claimed before its run starts.
    assert await _status(async_session_factory, both) == "queued"


@pytest.mark.asyncio
async def test_a_mode_of_a_polarity_the_file_lacks_is_passed_over(
    async_session_factory, setup, spawn, editor_client
):
    negative = await _file(async_session_factory, "neg", "-")

    resp = await editor_client.post(
        "/api/sample/files/bind",
        json={
            "sample_file_ids": [negative],
            "ionization_mode_ids": [setup["negative"], setup["positive"]],
        },
    )

    assert resp.status_code == 202, resp.text
    assert _started(spawn) == {negative: [setup["negative"]]}


@pytest.mark.asyncio
async def test_a_file_with_samples_is_rebuilt_under_the_chosen_modes(
    async_session_factory, setup, spawn, editor_client
):
    """A wrong choice can be corrected: its samples are the pipeline's own."""
    processed = await _file(
        async_session_factory,
        "processed",
        "-",
        sample_under=(setup["batch"], setup["negative"]),
    )

    resp = await editor_client.post(
        "/api/sample/files/bind",
        json={
            "sample_file_ids": [processed],
            "ionization_mode_ids": [setup["negative"]],
        },
    )

    assert resp.status_code == 202, resp.text
    assert _started(spawn) == {processed: [setup["negative"]]}


@pytest.mark.asyncio
async def test_a_file_someone_made_a_sample_from_is_refused_and_the_rest_go_ahead(
    async_session_factory, setup, spawn, editor_client, test_users
):
    """Partly done: a warning names the refused file, and the others start."""
    parked = await _file(async_session_factory, "parked", "-")
    used = await _file(async_session_factory, "used", "-")
    await _user_sample(async_session_factory, test_users, used)

    resp = await editor_client.post(
        "/api/sample/files/bind",
        json={
            "sample_file_ids": [parked, used],
            "ionization_mode_ids": [setup["negative"]],
        },
    )

    assert resp.status_code == 207, resp.text
    body = resp.json()
    assert body["detail"]["started"] == [parked]
    (refusal,) = body["detail"]["refused"]
    assert refusal["sample_file_id"] == used
    assert "sample someone made from it" in refusal["message"]
    assert "1 could not be bound" in body["error"]
    assert _started(spawn) == {parked: [setup["negative"]]}
    assert await _status(async_session_factory, used) == "needs_chemistry"


@pytest.mark.asyncio
async def test_a_second_choice_for_the_same_file_starts_no_second_run(
    async_session_factory, setup, spawn, editor_client
):
    parked = await _file(async_session_factory, "twice", "-")
    body = {"sample_file_ids": [parked], "ionization_mode_ids": [setup["negative"]]}

    first = await editor_client.post("/api/sample/files/bind", json=body)
    second = await editor_client.post("/api/sample/files/bind", json=body)

    assert first.status_code == 202, first.text
    assert second.status_code == 422, second.text
    assert "being processed already" in second.text
    assert spawn.await_count == 1


@pytest.mark.asyncio
async def test_a_file_whose_own_run_is_under_way_is_left_to_it(
    async_session_factory, setup, spawn, editor_client
):
    converted = await _file(async_session_factory, "converted", "-")
    await _set_status(async_session_factory, converted, "converted")

    resp = await editor_client.post(
        "/api/sample/files/bind",
        json={
            "sample_file_ids": [converted],
            "ionization_mode_ids": [setup["negative"]],
        },
    )

    assert resp.status_code == 422, resp.text
    spawn.assert_not_called()


@pytest.mark.asyncio
async def test_a_file_that_failed_before_its_samples_can_be_given_a_chemistry(
    async_session_factory, setup, spawn, editor_client
):
    """Queued at a restart, say: failed, with no samples and no token."""
    failed = await _file(async_session_factory, "failed", "-")
    await _set_status(async_session_factory, failed, "failed")

    resp = await editor_client.post(
        "/api/sample/files/bind",
        json={"sample_file_ids": [failed], "ionization_mode_ids": [setup["negative"]]},
    )

    assert resp.status_code == 202, resp.text
    assert _started(spawn) == {failed: [setup["negative"]]}


@pytest.mark.asyncio
async def test_modes_that_do_not_fit_a_file_bind_nothing(
    async_session_factory, setup, spawn, editor_client
):
    both = await _file(async_session_factory, "both", "+-")

    resp = await editor_client.post(
        "/api/sample/files/bind",
        json={"sample_file_ids": [both], "ionization_mode_ids": [setup["negative"]]},
    )

    assert resp.status_code == 422, resp.text
    assert "one per polarity" in resp.text
    spawn.assert_not_called()


@pytest.mark.asyncio
async def test_an_unknown_mode_binds_nothing(
    async_session_factory, setup, spawn, editor_client
):
    negative = await _file(async_session_factory, "neg", "-")

    resp = await editor_client.post(
        "/api/sample/files/bind",
        json={"sample_file_ids": [negative], "ionization_mode_ids": ["no-such-mode"]},
    )

    assert resp.status_code == 422, resp.text
    spawn.assert_not_called()


@pytest.mark.asyncio
async def test_a_guest_cannot_choose(async_session_factory, setup, spawn, guest_client):
    negative = await _file(async_session_factory, "neg", "-")

    resp = await guest_client.post(
        "/api/sample/files/bind",
        json={
            "sample_file_ids": [negative],
            "ionization_mode_ids": [setup["negative"]],
        },
    )

    assert resp.status_code == 403, resp.text
    spawn.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"sample_file_ids": [], "ionization_mode_ids": ["m"]},
        {"sample_file_ids": ["f"], "ionization_mode_ids": []},
        {"sample_file_ids": ["f"], "ionization_mode_ids": ["a", "b", "c"]},
        {"sample_file_ids": ["f", "f"], "ionization_mode_ids": ["m"]},
    ],
)
async def test_a_malformed_choice_is_refused(admin_client, body):
    resp = await admin_client.post("/api/sample/files/bind", json=body)

    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# Re-processing
# ---------------------------------------------------------------------------


@pytest.fixture
def pipeline(monkeypatch) -> AsyncMock:
    """Stand in for the pipeline re-processing runs, and the reset before it."""
    stub = AsyncMock(return_value={"_notification_data": {}})
    monkeypatch.setattr(process_service, "auto_process_sample_file", stub)
    monkeypatch.setattr(process_service, "reset_mz_calibration", AsyncMock())
    return stub


@pytest.mark.asyncio
async def test_reprocessing_keeps_the_modes_a_file_was_bound_to(
    async_session_factory, setup, pipeline
):
    bound = await _file(
        async_session_factory,
        "bound-by-hand",
        "-",
        sample_under=(setup["batch"], setup["negative"]),
    )

    result = await process_service.re_process_sample_files(sample_file_ids=[bound])

    assert "Successfully re-processed 1" in result["message"]
    pipeline.assert_awaited_once()
    assert pipeline.await_args.kwargs["ionization_mode_ids"] == [setup["negative"]]


@pytest.mark.asyncio
async def test_reprocessing_refuses_a_file_nothing_binds(
    async_session_factory, setup, pipeline
):
    parked = await _file(async_session_factory, "parked", "-")

    with pytest.raises(Exception, match="(?i)ionization mode tokens"):
        await process_service.re_process_sample_files(sample_file_ids=[parked])

    pipeline.assert_not_called()


@pytest.mark.asyncio
async def test_reprocessing_refuses_an_ambiguous_name_whatever_its_samples(
    async_session_factory, setup, pipeline
):
    """Only a name no token matches stands its earlier binding in."""
    tokens = [f"T{gen_id(6)}", f"T{gen_id(6)}"]
    mode_ids = [gen_id(16), gen_id(16)]
    async with async_session_factory() as session:
        for mode_id, token in zip(mode_ids, tokens):
            session.add(
                IonizationMode(
                    ionization_mode_id=mode_id,
                    ionization_mode_name=f"Bind ambiguous {token}",
                    ionization_mode_token=token,
                    ionization_mode_polarity="-",
                    ionization_mechanism_ids=[],
                )
            )
        await session.commit()
    try:
        both = await _file(
            async_session_factory,
            f"{tokens[0]}_{tokens[1]}",
            "-",
            sample_under=(setup["batch"], setup["negative"]),
        )

        with pytest.raises(Exception, match="2 modes match polarity -"):
            await process_service.re_process_sample_files(sample_file_ids=[both])

        pipeline.assert_not_called()
    finally:
        async with async_session_factory() as session:
            await session.execute(
                delete(IonizationMode).where(
                    IonizationMode.ionization_mode_id.in_(mode_ids)
                )
            )
            await session.commit()


@pytest.mark.asyncio
async def test_reprocessing_says_which_file_parked_instead(
    async_session_factory, setup, pipeline
):
    """A token removed while the batch waited: the file was not re-processed."""
    bound = await _file(
        async_session_factory,
        "parks",
        "-",
        sample_under=(setup["batch"], setup["negative"]),
    )
    pipeline.return_value = {
        "status": "parked",
        "message": "No ionization mode tokens found. Or choose its chemistry in Raw files.",
    }

    with pytest.raises(Exception, match="choose its chemistry"):
        await process_service.re_process_sample_files(sample_file_ids=[bound])


@pytest.mark.asyncio
async def test_reprocessing_leaves_a_file_being_processed_to_its_run(
    async_session_factory, setup, pipeline
):
    bound = await _file(
        async_session_factory,
        "busy",
        "-",
        sample_under=(setup["batch"], setup["negative"]),
    )
    await _set_status(async_session_factory, bound, "bound")

    with pytest.raises(Exception, match="being processed already"):
        await process_service.re_process_sample_files(sample_file_ids=[bound])

    pipeline.assert_not_called()


@pytest.mark.asyncio
async def test_reprocessing_refuses_a_file_someone_made_a_sample_from(
    async_session_factory, setup, pipeline, test_users
):
    """Whatever the sample's type: re-processing would delete it."""
    bound = await _file(
        async_session_factory,
        "used",
        "-",
        sample_under=(setup["batch"], setup["negative"]),
    )
    await _user_sample(async_session_factory, test_users, bound)

    with pytest.raises(Exception, match="user-created"):
        await process_service.re_process_sample_files(sample_file_ids=[bound])

    pipeline.assert_not_called()


# ---------------------------------------------------------------------------
# Processing a file on request, and the pipeline's own samples
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_processing_a_hand_bound_file_on_request_keeps_its_modes(
    async_session_factory, setup, admin_client, monkeypatch
):
    bound = await _file(
        async_session_factory,
        "on-request",
        "-",
        sample_under=(setup["batch"], setup["negative"]),
    )
    spawn = AsyncMock()
    monkeypatch.setattr(sample_files_routes, "spawn_auto_process_sample_file", spawn)

    resp = await admin_client.post(f"/api/sample/files/{bound}/process")

    assert resp.status_code == 202, resp.text
    assert spawn.await_args.kwargs["ionization_mode_ids"] == [setup["negative"]]


@pytest.mark.asyncio
async def test_processing_a_file_being_processed_on_request_is_refused(
    async_session_factory, setup, admin_client, monkeypatch
):
    busy = await _file(async_session_factory, "busy", "-")
    await _set_status(async_session_factory, busy, "calibrated")
    spawn = AsyncMock()
    monkeypatch.setattr(sample_files_routes, "spawn_auto_process_sample_file", spawn)

    resp = await admin_client.post(f"/api/sample/files/{busy}/process")

    assert resp.status_code == 409, resp.text
    spawn.assert_not_called()


@pytest.mark.asyncio
async def test_a_rerun_clears_only_the_pipelines_own_samples(
    async_session_factory, setup, test_users
):
    """A person's sample from the file stays, whatever its type."""
    bound = await _file(
        async_session_factory,
        "cleared",
        "-",
        sample_under=(setup["batch"], setup["negative"]),
    )
    mine = await _user_sample(async_session_factory, test_users, bound)

    await process_service._delete_partial_acquisition_items(bound)

    async with async_session_factory() as session:
        left = set(
            await session.scalars(
                select(SampleItem.sample_item_id).where(
                    SampleItem.sample_file_id == bound
                )
            )
        )
    assert left == {mine}
