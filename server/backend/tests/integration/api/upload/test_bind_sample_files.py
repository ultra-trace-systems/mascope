"""
Integration tests: choosing the chemistry of files that need one.

A file whose name carries no token of a configured mode is parked with the
status ``needs_chemistry`` and no samples. ``POST /api/sample/files/bind``
processes such files under the ionization modes a person chose: each file is
bound to the chosen mode of each polarity it holds. A file that has samples
is refused, as is one the chosen modes do not fit. Re-processing keeps the
modes a file was bound to that way, since no token binds it again.

The pipeline itself is stubbed: what is pinned is which files start, under
which modes, and what is refused.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import delete

from mascope_backend.api.controllers.sample.files.process import (
    service as process_service,
)
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
            delete(IonizationMode).where(
                IonizationMode.ionization_mode_id.in_(
                    [ids["negative"], ids["positive"]]
                )
            )
        )
        await session.commit()


@pytest.fixture
def spawn(monkeypatch) -> AsyncMock:
    """Record which pipelines start instead of starting them."""
    stub = AsyncMock(return_value=None)
    monkeypatch.setattr(process_service, "spawn_auto_process_sample_file", stub)
    return stub


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
async def test_a_file_with_samples_is_refused_and_the_rest_go_ahead(
    async_session_factory, setup, spawn, editor_client
):
    parked = await _file(async_session_factory, "parked", "-")
    processed = await _file(
        async_session_factory,
        "processed",
        "-",
        sample_under=(setup["batch"], setup["negative"]),
    )

    resp = await editor_client.post(
        "/api/sample/files/bind",
        json={
            "sample_file_ids": [parked, processed],
            "ionization_mode_ids": [setup["negative"]],
        },
    )

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["data"]["started"] == [parked]
    (refusal,) = body["data"]["refused"]
    assert refusal["sample_file_id"] == processed
    assert "has samples already" in refusal["message"]
    assert "1 could not be bound" in body["message"]
    assert _started(spawn) == {parked: [setup["negative"]]}


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
