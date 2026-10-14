"""
Integration tests: choosing the chemistry of files that need one.

A file whose name carries no token of a configured mode is parked with the
status ``needs_chemistry`` and no samples. ``POST /api/sample/files/bind``
processes such files under the ionization modes a person chose: each file is
bound to the chosen mode of each polarity it holds, and claimed first, so a
second choice cannot start a second run - unless the run holding it stalled.
A file that has samples already is rebuilt under the chosen modes; one with a
sample a person made from it keeps its calibration, and one the chosen modes
do not fit is refused. Re-processing, and processing a file on request, keep
the modes a file was bound to that way, whatever its name and its acquisition
record say, until somebody chooses again. A file whose samples no mode binds
any longer is started on neither road, since the run would park it - but on
request, where the method rung is on and the file's acquisition method binds
it, it is left to the run, which binds a file before it clears anything of
it. Only the pipeline's own samples are replaced, wherever a person's sample
is.

For a file nobody chose the chemistry of, its acquisition record is asked
first on both of those roads, as the pipeline asks it: a file its record
binds starts with no modes of its own, whatever its name says and whatever
its record or a token bound it to, and one its record does not bind is
judged as a file with no record is. A record that cannot be read is no
answer to act on, and neither are samples that cannot be.

The pipeline itself is stubbed: what is pinned is which files start, under
which modes, and what is refused. One test runs the pipeline's own binding
behind the check, so that the two are held together and not only apart. The
last ones run the pipeline itself as far as its binding: a run binds a file
before it clears anything of it, so a file it parks keeps its samples.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from mascope_backend.api.controllers.sample.files.process import (
    service as process_service,
)
from mascope_backend.api.controllers.sample.files.process import status
from mascope_backend.api.controllers.sample.files.process.bindings import (
    MethodRouting,
)
from mascope_backend.api.lib.exceptions.api_exceptions import ApiException
from mascope_backend.api.models.sample.files.config import STALLED_AFTER
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

#: The columns a record's identifiers are kept in beside it.
_RECORD_IDS = ("agent_id", "sequence_run_id", "step_id", "acquisition_id")


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
            delete(Workspace).where(
                Workspace.workspace_name.like("Bind analysis %")
                | Workspace.workspace_name.like("Bind system %")
            )
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


#: Where a person's batch can be: a workspace of their own; a system workspace
#: like the "System Workspace" an older deployment moved its datasets into;
#: and a dataset they made in the instrument's own workspace.
PLACES = ("own workspace", "legacy system workspace", "instrument workspace")


async def _user_sample(
    async_session_factory,
    test_users,
    sample_file_id: str,
    place: str = "own workspace",
    instrument_workspace: str | None = None,
) -> str:
    """A sample a person made from the file, in a batch of their own.

    Typed ACQUISITION, as the create dialog lets a person type it, so only
    where it lives tells it from the pipeline's own. ``place`` is one of
    ``PLACES``; the instrument's workspace is ``instrument_workspace``.
    """
    ids = {"workspace": gen_id(16), "dataset": gen_id(16), "batch": gen_id(16)}
    async with async_session_factory() as session:
        if place == "instrument workspace":
            ids["workspace"] = instrument_workspace
        else:
            legacy = place == "legacy system workspace"
            session.add(
                Workspace(
                    workspace_id=ids["workspace"],
                    workspace_name=(
                        f"Bind {'system' if legacy else 'analysis'} {ids['workspace']}"
                    ),
                    workspace_status="active",
                    is_system=legacy,
                )
            )
            await session.flush()
        session.add(
            Dataset(
                dataset_id=ids["dataset"],
                workspace_id=ids["workspace"],
                dataset_name=f"Mine {ids['dataset']}",
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


async def _set_status(
    async_session_factory,
    sample_file_id: str,
    value: str,
    recorded_ago: timedelta = timedelta(0),
) -> None:
    async with async_session_factory() as session:
        row = await session.get(SampleFile, sample_file_id)
        row.processing_status = value
        row.processing_updated_utc = datetime.now(timezone.utc) - recorded_ago
        await session.commit()


#: Longer than any run goes without recording a stage: its run has stopped.
_STALLED = STALLED_AFTER + timedelta(hours=1)


async def _file(
    async_session_factory,
    name: str,
    polarity: str,
    sample_under: tuple[str, str | None] | None = None,
    declaring: str | None = None,
    bound_by: str | None = None,
) -> str:
    """A file with no token in its name, unless ``name`` is given one.

    With ``sample_under`` - a batch and a mode - it has an ACQUISITION sample
    under that mode, as a file bound by hand does once processed; a mode of
    None is the sample of a file whose mode was deleted since. ``bound_by``
    is the rung the sample records: "explicit" where a person chose, None for
    a sample older than the column.

    With ``declaring`` it came with an acquisition record that names that
    chemistry, stored as an upload stores one: the document, and its
    identifiers in their columns.
    """
    sample_file_id = gen_id()
    record = None
    if declaring is not None:
        record = {
            "schema": "mascope-acquisition/1",
            "source_filename": f"{name}.raw",
            **{column: str(uuid4()) for column in _RECORD_IDS},
            "ionization": declaring,
        }
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
                acquisition=record,
                **{column: (record or {}).get(column) for column in _RECORD_IDS},
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
                    bound_by=bound_by,
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
@pytest.mark.parametrize("place", PLACES)
async def test_a_file_someone_made_a_sample_from_keeps_its_calibration(
    async_session_factory, setup, spawn, editor_client, test_users, place
):
    """Processed by hand into a person's batch: bound, not reset under them."""
    parked = await _file(async_session_factory, "parked", "-")
    used = await _file(async_session_factory, "used", "-")
    await _user_sample(
        async_session_factory,
        test_users,
        used,
        place=place,
        instrument_workspace=setup["workspace"],
    )

    resp = await editor_client.post(
        "/api/sample/files/bind",
        json={
            "sample_file_ids": [parked, used],
            "ionization_mode_ids": [setup["negative"]],
        },
    )

    assert resp.status_code == 202, resp.text
    assert resp.json()["data"] == {"started": [parked, used], "refused": []}
    reset = {
        call.kwargs["sample_file_id"]: call.kwargs["reset_calibration"]
        for call in spawn.await_args_list
    }
    assert reset == {parked: True, used: False}


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
async def test_a_file_whose_run_stalled_can_be_given_a_chemistry(
    async_session_factory, setup, spawn, editor_client
):
    """A worker restart left it bound, with no run behind it and no restart."""
    stalled = await _file(async_session_factory, "stalled", "-")
    await _set_status(async_session_factory, stalled, "bound", recorded_ago=_STALLED)

    resp = await editor_client.post(
        "/api/sample/files/bind",
        json={"sample_file_ids": [stalled], "ionization_mode_ids": [setup["negative"]]},
    )

    assert resp.status_code == 202, resp.text
    assert _started(spawn) == {stalled: [setup["negative"]]}
    assert await _status(async_session_factory, stalled) == "queued"


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


def _report(refused) -> str:
    """What a refused run tells its user: the message, and the reasons per
    file that the notification carries beside it."""
    return f"{refused.value.user_message}\n{refused.value.tech_message}"


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


def _census(*ms_orders):
    """A negative file's scan-stream census, one stream per MS order."""
    return [
        {
            "key": f"FTMS - p NSI Full ms{order}",
            "signature": {"ms_order": order, "polarity": "-"},
        }
        for order in ms_orders
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("census", [None, [], _census(1), _census(1, 2)])
async def test_reprocessing_refuses_a_file_nothing_binds(
    async_session_factory, setup, pipeline, monkeypatch, census
):
    """Whatever scans it holds, so long as one of them is an MS1 scan - or
    nothing says otherwise: it would only park again."""
    monkeypatch.setattr(
        process_service, "read_scan_streams", AsyncMock(return_value=census)
    )
    parked = await _file(async_session_factory, "parked", "-")

    with pytest.raises(Exception, match="(?i)ionization mode tokens"):
        await process_service.re_process_sample_files(sample_file_ids=[parked])

    pipeline.assert_not_called()


@pytest.mark.asyncio
async def test_reprocessing_lets_the_rung_answer_for_a_parked_file(
    async_session_factory, setup, pipeline, monkeypatch
):
    """Re-processing the parked files is how a site picks up what it collected.

    Selecting them in Raw files and pressing Re-process calls this, so
    refusing a file on its name here would report "no tokens" for exactly the
    files a routing deployment can now bind. The method is not asked first: a
    file with no samples has nothing a run could cost it, so it goes through
    with no modes of its own, and parks again if the method cannot place it.
    """
    monkeypatch.setattr(process_service, "routes_on_method_binding", lambda: True)
    parked = await _file(async_session_factory, "parked-for-the-rung", "-")

    result = await process_service.re_process_sample_files(sample_file_ids=[parked])

    assert "Successfully re-processed 1" in result["message"]
    pipeline.assert_awaited_once()
    # No modes of its own: by_token stays true, the token rule raises, and the
    # pipeline consults the binding.
    assert pipeline.await_args.kwargs["ionization_mode_ids"] is None
    assert pipeline.await_args.kwargs["kept_provenance"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("by_method", [False, True], ids=["rung off", "rung on"])
@pytest.mark.parametrize(
    "has_its_part", [False, True], ids=["no sample yet", "with its MS2 part"]
)
async def test_reprocessing_lets_a_file_of_fragmentation_scans_alone_through(
    async_session_factory, setup, pipeline, monkeypatch, by_method, has_its_part
):
    """It needs no chemistry, so having none to keep is no reason to refuse it.

    The pipeline files it without one, as it did the first time: its earlier
    MS2 sample has no mode for re-processing to keep, and the rebuild clears
    it before the run files it again. With the method rung on, a file whose
    samples have no mode goes on only if its method binds it, because its
    run would park it with them gone - not this one, which its run files
    again, and whose method is not asked: this file's reports no name, and
    asked, it would decline.
    """
    monkeypatch.setattr(process_service, "routes_on_method_binding", lambda: by_method)
    monkeypatch.setattr(
        process_service, "read_scan_streams", AsyncMock(return_value=_census(2))
    )
    filed = await _file(
        async_session_factory,
        "ms2-only",
        "-",
        sample_under=(setup["batch"], None) if has_its_part else None,
    )

    result = await process_service.re_process_sample_files(sample_file_ids=[filed])

    assert "Successfully re-processed 1" in result["message"]
    pipeline.assert_awaited_once()
    assert pipeline.await_args.kwargs["ionization_mode_ids"] is None
    assert pipeline.await_args.kwargs["kept_provenance"] is None


def _asked_in(steps: "_Steps", rung: AsyncMock) -> None:
    """Note in ``steps`` when the method rung's stand-in is asked."""
    answer = rung.return_value

    def asked(*_args):
        steps.done.append("asked its method")
        return answer

    rung.side_effect = asked


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "under", [None, "positive"], ids=["mode deleted", "mode of another polarity"]
)
async def test_reprocessing_leaves_a_file_with_samples_its_method_binds_to_the_run(
    async_session_factory, setup, pipeline, steps, monkeypatch, under
):
    """A file bound once, whose samples no longer give a mode per polarity,
    where the rung is on: a mode deleted since leaves them with none, and one
    whose polarity was changed since is no mode of the file's.

    It has no modes to keep, which is what a parked file with no samples says
    too, but its samples are still there, and re-processing clears them
    before the pipeline asks the method. So the method is asked first, as the
    run asks it, on the file's scan-stream census, and a file it binds is
    sent on with no modes of its own. It is asked in the loop that rebuilds
    the files, just before the file is claimed, and not with the checks in
    front of the batch: the files ahead of it are bound by then, and teach
    the bindings as they are.
    """
    monkeypatch.setattr(process_service, "routes_on_method_binding", lambda: True)
    rung, census = _method_binds(monkeypatch, setup)
    _asked_in(steps, rung)
    orphaned = await _file(
        async_session_factory,
        "mode-deleted-bound-by-its-method",
        "-",
        sample_under=(setup["batch"], setup[under] if under else None),
    )

    result = await process_service.re_process_sample_files(sample_file_ids=[orphaned])

    assert "Successfully re-processed 1" in result["message"]
    asked_for, streams = rung.await_args.args
    assert asked_for.sample_file_id == orphaned
    assert streams is census
    assert steps.done == [
        "decided",
        "asked its method",
        "reset the calibration",
        "detected per stream",
        "cleared the samples",
        "ran the pipeline",
    ]
    assert pipeline.await_args.kwargs["sample_file_id"] == orphaned
    assert pipeline.await_args.kwargs["ionization_mode_ids"] is None
    assert pipeline.await_args.kwargs["kept_provenance"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "under", [None, "positive"], ids=["mode deleted", "mode of another polarity"]
)
async def test_reprocessing_refuses_a_file_with_samples_its_method_does_not_bind(
    async_session_factory, setup, steps, monkeypatch, under
):
    """The same file, where the method does not bind it either: its run could
    only park it, with its samples gone. It is left as it is, with the
    method's own reason and remedy, as processing it on request refuses it.
    The rung itself is asked: this file's method reports no name, which is
    one of the ways it declines."""
    monkeypatch.setattr(process_service, "routes_on_method_binding", lambda: True)
    monkeypatch.setattr(
        process_service, "read_scan_streams", AsyncMock(return_value=[])
    )
    rung = AsyncMock(wraps=process_service.resolve_modes_by_method_binding)
    monkeypatch.setattr(process_service, "resolve_modes_by_method_binding", rung)
    orphaned = await _file(
        async_session_factory,
        "mode-deleted-declined-by-its-method",
        "-",
        sample_under=(setup["batch"], setup[under] if under else None),
    )

    with pytest.raises(Exception, match="no longer bind it") as refused:
        await process_service.re_process_sample_files(sample_file_ids=[orphaned])

    reason = _report(refused)
    assert "ionization mode tokens" in reason.lower()
    assert "does not say either: its acquisition method reports no name" in reason
    assert "Files of this instrument need a filename token" in reason
    assert "It was left as it is." in reason
    rung.assert_awaited_once()
    assert steps.done == ["decided"]
    assert await _samples_of(async_session_factory, orphaned) == 1
    assert await _status(async_session_factory, orphaned) == "done"


@pytest.mark.asyncio
async def test_reprocessing_leaves_a_file_as_it_is_when_its_method_cannot_be_asked(
    async_session_factory, setup, steps, monkeypatch
):
    """A method that cannot be asked is not taken for one that binds the file
    or for one that declines it. The file is left as it is, and the report
    says that the method could not be asked, not what the error said."""
    monkeypatch.setattr(process_service, "routes_on_method_binding", lambda: True)
    monkeypatch.setattr(
        process_service,
        "resolve_modes_by_method_binding",
        AsyncMock(side_effect=RuntimeError("the database went away")),
    )
    orphaned = await _file(
        async_session_factory,
        "mode-deleted-method-unasked",
        "-",
        sample_under=(setup["batch"], None),
    )

    with pytest.raises(
        Exception, match="Failed to ask its acquisition method"
    ) as refused:
        await process_service.re_process_sample_files(sample_file_ids=[orphaned])

    assert "the database went away" not in _report(refused)
    assert steps.done == ["decided"]
    assert await _samples_of(async_session_factory, orphaned) == 1
    assert await _status(async_session_factory, orphaned) == "done"


@pytest.mark.asyncio
async def test_reprocessing_does_not_leave_unread_samples_to_the_rung(
    async_session_factory, setup, pipeline, monkeypatch
):
    """Samples that could not be read are not no samples either: this file
    has one, under a mode that still binds it, and sent on to the rung it
    would be cleared and bound by its method, or parked.

    The report says that the read failed and not what the error said: a
    fault's own text can hold SQL with its bound parameters, and stays in
    the log."""
    monkeypatch.setattr(process_service, "routes_on_method_binding", lambda: True)
    monkeypatch.setattr(
        process_service,
        "_kept_modes",
        AsyncMock(side_effect=RuntimeError("the database went away")),
    )
    bound = await _file(
        async_session_factory,
        "samples-unread",
        "-",
        sample_under=(setup["batch"], setup["negative"]),
    )

    with pytest.raises(
        Exception, match="Failed to read the modes its samples have"
    ) as refused:
        await process_service.re_process_sample_files(sample_file_ids=[bound])

    assert "the database went away" not in _report(refused)
    pipeline.assert_not_called()
    assert await _samples_of(async_session_factory, bound) == 1
    assert await _status(async_session_factory, bound) == "done"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failing", "says"),
    [
        ("_modes_its_record_declares", "Failed to read its acquisition record"),
        ("resolve_ionization_modes_by_tokens", "Failed to resolve ionization modes"),
        (
            "_clear_sample_items_for_reprocessing",
            "Processing failed: Processing stopped on an unexpected error",
        ),
    ],
)
async def test_reprocessing_reports_a_fault_without_its_text(
    async_session_factory, setup, pipeline, monkeypatch, failing, says
):
    """The report goes to whoever pressed Re-process, and a fault's own text
    can hold SQL with its bound parameters. Wherever one stops a file - at
    the read of its record, at the reading of its name, or at the clearing
    before its run - the report says what failed, and what the error said
    is left to the log."""
    monkeypatch.setattr(
        process_service,
        failing,
        AsyncMock(side_effect=RuntimeError("the database went away")),
    )
    bound = await _file(
        async_session_factory,
        "stopped-by-a-fault",
        "-",
        sample_under=(setup["batch"], setup["negative"]),
    )

    with pytest.raises(Exception, match=says) as refused:
        await process_service.re_process_sample_files(sample_file_ids=[bound])

    assert "the database went away" not in _report(refused)
    pipeline.assert_not_called()
    assert await _samples_of(async_session_factory, bound) == 1


# ---------------------------------------------------------------------------
# Re-processing a file that came with an acquisition record
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def mode_with_token(async_session_factory):
    """Make an ionization mode with a token of its own, removed afterwards.

    Called with a polarity, it gives the mode's id and its token.
    """
    made: list[str] = []

    async def make(polarity: str = "-") -> tuple[str, str]:
        mode_id, token = gen_id(16), f"T{gen_id(6)}"
        async with async_session_factory() as session:
            session.add(
                IonizationMode(
                    ionization_mode_id=mode_id,
                    ionization_mode_name=f"Bind declared {token}",
                    ionization_mode_token=token,
                    ionization_mode_polarity=polarity,
                    ionization_mechanism_ids=[],
                )
            )
            await session.commit()
        made.append(mode_id)
        return mode_id, token

    yield make
    async with async_session_factory() as session:
        await session.execute(
            delete(IonizationMode).where(IonizationMode.ionization_mode_id.in_(made))
        )
        await session.commit()


async def _samples_of(async_session_factory, sample_file_id: str) -> int:
    async with async_session_factory() as session:
        return len(
            (
                await session.execute(
                    select(SampleItem.sample_item_id).where(
                        SampleItem.sample_file_id == sample_file_id
                    )
                )
            ).all()
        )


class _SeenEnough(Exception):
    """Ends the pipeline where a test has seen what it bound."""


@pytest.fixture
def binding(setup, monkeypatch) -> dict[str, AsyncMock]:
    """Run the pipeline's own binding behind the check.

    The check and the pipeline behind it, together: what re-processing
    starts is the pipeline's own ``_auto_process_sample_file``, on a row
    stored as an upload stores it, as far as the samples it would make. The
    method rung is off, as it is unless a deployment turns it on.

    Gives ``create``, called with the modes the samples would be made under,
    and ``learn``, the method-binding learner.
    """
    create = AsyncMock(side_effect=_SeenEnough)
    learn = AsyncMock()
    for name, stand_in in {
        "create_acquisition_batches_and_items": create,
        "reset_mz_calibration": AsyncMock(),
        "read_scan_streams": AsyncMock(return_value=[]),
        "read_store_stitch": AsyncMock(return_value=status.StoreStitch()),
        "learn_method_bindings": learn,
        "get_acquisition_dataset": AsyncMock(
            return_value={"data": {"dataset_id": setup["dataset"]}}
        ),
    }.items():
        monkeypatch.setattr(process_service, name, stand_in)

    async def as_far_as_the_binding(sample_file_id, **started_with):
        try:
            # Re-processing has cleared the file already, so the wrapper's
            # clearing step has nothing to do here.
            await process_service._auto_process_sample_file(
                sample_file_id=sample_file_id,
                ionization_mode_ids=started_with["ionization_mode_ids"],
                kept_provenance=started_with["kept_provenance"],
                clear_earlier_run=AsyncMock(),
            )
        except _SeenEnough:
            pass
        return {"_notification_data": {}}

    monkeypatch.setattr(
        process_service, "auto_process_sample_file", as_far_as_the_binding
    )
    return {"create": create, "learn": learn}


@pytest.mark.asyncio
async def test_reprocessing_binds_a_file_that_waited_for_its_records_token(
    async_session_factory, setup, mode_with_token, binding
):
    """A file whose chemistry is in its acquisition record has no reason to
    carry a token in its name. It parks while no mode has the token the
    record names, and once one does, selecting the parked files and pressing
    Re-process is how a site picks them up."""
    mode_id, token = await mode_with_token("-")
    parked = await _file(
        async_session_factory, "parked-with-a-record", "-", declaring=token
    )

    result = await process_service.re_process_sample_files(sample_file_ids=[parked])

    assert "Successfully re-processed 1" in result["message"]
    bound = binding["create"].await_args.kwargs
    assert [mode.ionization_mode_id for mode in bound["ionization_modes"]] == [mode_id]
    assert bound["provenance"] == {mode_id: process_service.ItemProvenance("declared")}


@pytest.mark.asyncio
async def test_a_choice_against_the_record_and_the_name_outlasts_a_reprocess(
    async_session_factory, setup, mode_with_token, binding
):
    """A file named for one chemistry, whose record declares it too, and a
    person who chose another by hand: a record cannot be corrected, so that
    is the one way to say it is wrong. Re-processing that day's files, for
    whatever reason, makes the file's samples under what the person chose
    again, still recorded as their choice. The method learns nothing from
    the run: the choice taught it once, when it was made."""
    _mode_id, token = await mode_with_token("-")
    chosen = await _file(
        async_session_factory,
        f"{token}_chosen-against-both",
        "-",
        sample_under=(setup["batch"], setup["negative"]),
        declaring=token,
        bound_by="explicit",
    )

    result = await process_service.re_process_sample_files(sample_file_ids=[chosen])

    assert "Successfully re-processed 1" in result["message"]
    bound = binding["create"].await_args.kwargs
    assert [mode.ionization_mode_id for mode in bound["ionization_modes"]] == [
        setup["negative"]
    ]
    assert bound["provenance"] == {
        setup["negative"]: process_service.ItemProvenance("explicit")
    }
    binding["learn"].assert_not_awaited()


@pytest.mark.asyncio
async def test_reprocessing_refuses_a_file_its_record_does_not_bind_either(
    async_session_factory, setup, pipeline
):
    """Nothing runs for it, and the reason says what its record named: the
    token to give a mode is in the message of the action that was refused."""
    parked = await _file(
        async_session_factory, "parked-unanswered", "-", declaring="T-no-mode-has"
    )

    with pytest.raises(Exception) as refused:
        await process_service.re_process_sample_files(sample_file_ids=[parked])

    assert "names the chemistry 'T-no-mode-has', but no ionization mode" in str(
        refused.value
    )
    assert "ionization mode tokens" in str(refused.value).lower()
    pipeline.assert_not_called()


@pytest.mark.asyncio
async def test_reprocessing_leaves_a_file_its_record_does_not_bind_its_samples(
    async_session_factory, setup, pipeline
):
    """A file bound once, whose mode was deleted since: its samples are still
    there, with no mode. Its record names a token no mode has, so nothing
    binds it now, and a run would clear the samples before it found that
    out. It is refused before anything of it is touched."""
    orphaned = await _file(
        async_session_factory,
        "mode-deleted",
        "-",
        sample_under=(setup["batch"], None),
        declaring="T-no-mode-has",
    )

    with pytest.raises(Exception, match="names the chemistry 'T-no-mode-has'"):
        await process_service.re_process_sample_files(sample_file_ids=[orphaned])

    pipeline.assert_not_called()
    assert await _samples_of(async_session_factory, orphaned) == 1
    assert await _status(async_session_factory, orphaned) == "done"


@pytest.mark.asyncio
@pytest.mark.parametrize("bound_by", ["declared", "token", None])
async def test_reprocessing_asks_the_record_before_the_modes_a_file_has(
    async_session_factory, setup, pipeline, mode_with_token, bound_by
):
    """The token a control program sends sat on the wrong mode, and files
    were bound by their records under it. Moved to the right mode, it binds
    them again there: the modes a file's samples have stand in only where
    nothing binds the file now. So for samples a token bound before it was
    renamed, and for ones older than the record of how they were bound. In
    none of those did anybody choose."""
    _mode_id, token = await mode_with_token("-")
    bound = await _file(
        async_session_factory,
        "bound-under-the-wrong-mode",
        "-",
        sample_under=(setup["batch"], setup["negative"]),
        declaring=token,
        bound_by=bound_by,
    )

    await process_service.re_process_sample_files(sample_file_ids=[bound])

    pipeline.assert_awaited_once()
    assert pipeline.await_args.kwargs["ionization_mode_ids"] is None
    assert pipeline.await_args.kwargs["kept_provenance"] is None


@pytest.mark.asyncio
async def test_reprocessing_keeps_a_chemistry_a_person_chose_against_the_record(
    async_session_factory, setup, pipeline, mode_with_token
):
    """A record is stored as it was sent. Where it names the wrong
    chemistry, choosing by hand is the one way to overrule it, and a
    re-process of that day's files, for whatever reason, must not quietly
    bind the file by its record again."""
    _mode_id, token = await mode_with_token("-")
    chosen = await _file(
        async_session_factory,
        "chosen-against-its-record",
        "-",
        sample_under=(setup["batch"], setup["negative"]),
        declaring=token,
        bound_by="explicit",
    )

    await process_service.re_process_sample_files(sample_file_ids=[chosen])

    pipeline.assert_awaited_once()
    started = pipeline.await_args.kwargs
    assert started["ionization_mode_ids"] == [setup["negative"]]
    assert started["kept_provenance"] == {
        setup["negative"]: process_service.ItemProvenance("explicit")
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("with_a_record", [True, False])
async def test_reprocessing_keeps_a_choice_for_a_file_its_name_binds(
    async_session_factory, setup, pipeline, mode_with_token, with_a_record
):
    """The rule about a person's choice does not stop at the file's name.
    Sent to the pipeline with no modes of its own, as a file with a token in
    its name used to be, it is bound by its record again, or by that token:
    the choice is undone one step after the check stepped aside for it, and
    the run reports nothing but success. It starts under what was chosen,
    with a record and without one."""
    _mode_id, token = await mode_with_token("-")
    named = await _file(
        async_session_factory,
        f"{token}_chosen",
        "-",
        sample_under=(setup["batch"], setup["negative"]),
        declaring=token if with_a_record else None,
        bound_by="explicit",
    )

    await process_service.re_process_sample_files(sample_file_ids=[named])

    pipeline.assert_awaited_once()
    started = pipeline.await_args.kwargs
    assert started["ionization_mode_ids"] == [setup["negative"]]
    assert started["kept_provenance"] == {
        setup["negative"]: process_service.ItemProvenance("explicit")
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("bound_by", ["declared", "token", None])
async def test_reprocessing_reads_the_name_of_a_file_nobody_chose_for(
    async_session_factory, setup, pipeline, mode_with_token, bound_by
):
    """Only a person's choice stands ahead of the name. Samples a record
    bound, or a token did, or ones older than the record of how they were
    bound, are nobody's choice: a token in the file's name binds it again,
    as fixing the tokens and re-processing has always had it."""
    _mode_id, token = await mode_with_token("-")
    named = await _file(
        async_session_factory,
        f"{token}_unchosen",
        "-",
        sample_under=(setup["batch"], setup["negative"]),
        bound_by=bound_by,
    )

    await process_service.re_process_sample_files(sample_file_ids=[named])

    pipeline.assert_awaited_once()
    assert pipeline.await_args.kwargs["ionization_mode_ids"] is None
    assert pipeline.await_args.kwargs["kept_provenance"] is None


@pytest.mark.asyncio
async def test_reprocessing_reads_the_name_of_a_file_whose_chosen_mode_is_gone(
    async_session_factory, setup, pipeline, mode_with_token
):
    """A choice whose mode was deleted since is no choice any more: there is
    nothing to rebuild the file under, and its name is asked."""
    _mode_id, token = await mode_with_token("-")
    orphaned = await _file(
        async_session_factory,
        f"{token}_choice-deleted",
        "-",
        sample_under=(setup["batch"], None),
        bound_by="explicit",
    )

    await process_service.re_process_sample_files(sample_file_ids=[orphaned])

    pipeline.assert_awaited_once()
    assert pipeline.await_args.kwargs["ionization_mode_ids"] is None
    assert pipeline.await_args.kwargs["kept_provenance"] is None


@pytest.mark.asyncio
async def test_reprocessing_reads_the_name_of_a_file_chosen_for_in_part(
    async_session_factory, setup, pipeline, mode_with_token
):
    """Every mode a file has must be a person's choice for the name to go
    unread. Choosing binds every polarity of a file at once, so samples that
    disagree about it did not come from one choice, and the tokens in the
    file's name bind both polarities again."""
    (_negative, negative_token), (_positive, positive_token) = (
        await mode_with_token("-"),
        await mode_with_token("+"),
    )
    mixed = await _file(
        async_session_factory, f"{negative_token}_{positive_token}", "+-"
    )
    async with async_session_factory() as session:
        for polarity, mode, bound_by in (
            ("-", "negative", "explicit"),
            ("+", "positive", "token"),
        ):
            session.add(
                SampleItem(
                    sample_item_id=gen_id(),
                    sample_batch_id=setup["batch"],
                    sample_file_id=mixed,
                    sample_item_name=f"2026-09-01 12:00:00 {polarity}",
                    sample_item_type="ACQUISITION",
                    sample_item_attributes={},
                    polarity=polarity,
                    ionization_mode_id=setup[mode],
                    bound_by=bound_by,
                    sample_item_utc_created=_NOW,
                )
            )
        await session.commit()

    await process_service.re_process_sample_files(sample_file_ids=[mixed])

    pipeline.assert_awaited_once()
    assert pipeline.await_args.kwargs["ionization_mode_ids"] is None
    assert pipeline.await_args.kwargs["kept_provenance"] is None


@pytest.mark.asyncio
async def test_reprocessing_refuses_a_file_whose_choice_cannot_be_read(
    async_session_factory, setup, pipeline, mode_with_token, monkeypatch
):
    """Whether somebody chose is not guessed at. Taken for "nobody did", a
    failed read sends this file to the pipeline with no modes of its own,
    and its name binds it over the choice. It is refused with nothing
    touched."""
    _mode_id, token = await mode_with_token("-")
    chosen = await _file(
        async_session_factory,
        f"{token}_choice-unread",
        "-",
        sample_under=(setup["batch"], setup["negative"]),
        bound_by="explicit",
    )
    monkeypatch.setattr(
        process_service,
        "_kept_modes",
        AsyncMock(side_effect=RuntimeError("the database went away")),
    )

    with pytest.raises(Exception, match="or the modes its samples have") as refused:
        await process_service.re_process_sample_files(sample_file_ids=[chosen])

    assert "the database went away" not in _report(refused)
    pipeline.assert_not_called()
    assert await _samples_of(async_session_factory, chosen) == 1
    assert await _status(async_session_factory, chosen) == "done"


@pytest.mark.asyncio
async def test_reprocessing_keeps_the_modes_of_a_file_its_record_does_not_bind(
    async_session_factory, setup, pipeline
):
    """Its record bound it once, and the token it names has been taken off
    the mode since: nothing binds the file now, and it is rebuilt under the
    mode it has, bound as it was."""
    kept = await _file(
        async_session_factory,
        "its-token-was-removed",
        "-",
        sample_under=(setup["batch"], setup["negative"]),
        declaring="T-no-mode-has",
        bound_by="declared",
    )

    await process_service.re_process_sample_files(sample_file_ids=[kept])

    pipeline.assert_awaited_once()
    started = pipeline.await_args.kwargs
    assert started["ionization_mode_ids"] == [setup["negative"]]
    assert started["kept_provenance"] == {
        setup["negative"]: process_service.ItemProvenance("declared")
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("answers", [True, False])
async def test_reprocessing_asks_the_record_before_an_ambiguous_name(
    async_session_factory, setup, pipeline, mode_with_token, answers
):
    """The pipeline reads the name only where the record did not bind the
    file. So a name that matches too much refuses a file its record does not
    answer for, with both reasons, and is not asked about one it does."""
    (_first, first_token), (_second, second_token) = (
        await mode_with_token("-"),
        await mode_with_token("-"),
    )
    both = await _file(
        async_session_factory,
        f"{first_token}_{second_token}",
        "-",
        declaring=first_token if answers else "T-no-mode-has",
    )

    if answers:
        await process_service.re_process_sample_files(sample_file_ids=[both])

        pipeline.assert_awaited_once()
        assert pipeline.await_args.kwargs["ionization_mode_ids"] is None
        return

    with pytest.raises(Exception) as refused:
        await process_service.re_process_sample_files(sample_file_ids=[both])

    assert "names the chemistry 'T-no-mode-has'" in str(refused.value)
    assert "2 modes match polarity -" in str(refused.value)
    pipeline.assert_not_called()


@pytest.mark.asyncio
async def test_reprocessing_refuses_a_file_whose_record_cannot_be_read(
    async_session_factory, setup, pipeline, monkeypatch
):
    """A record that could not be read is not one that binds nothing. Judged
    by its samples instead, this file would be cleared and rebuilt under the
    mode it has while its record may name another, and reported as
    re-processed. It is refused with nothing touched, and the file selected
    with it runs."""

    async def unread_record(sample_file):
        # As the reading itself has it: a file with no record is not read.
        if sample_file.acquisition_id is None:
            return [], None
        raise RuntimeError("the database went away")

    monkeypatch.setattr(process_service, "_modes_its_record_declares", unread_record)
    unread = await _file(
        async_session_factory,
        "record-unread",
        "-",
        sample_under=(setup["batch"], setup["negative"]),
        declaring="T-anything",
        bound_by="declared",
    )
    plain = await _file(
        async_session_factory,
        "no-record",
        "-",
        sample_under=(setup["batch"], setup["negative"]),
    )

    with pytest.raises(Exception, match="Failed to read its acquisition record"):
        await process_service.re_process_sample_files(sample_file_ids=[unread])
    pipeline.assert_not_called()

    # Selected with another file, it is the one that is reported, as a
    # warning, and the other is re-processed.
    await process_service.re_process_sample_files(sample_file_ids=[unread, plain])

    pipeline.assert_awaited_once()
    assert pipeline.await_args.kwargs["sample_file_id"] == plain
    assert await _samples_of(async_session_factory, unread) == 1
    assert await _status(async_session_factory, unread) == "done"


@pytest.mark.asyncio
async def test_reprocessing_keeps_a_choice_for_a_name_that_matches_too_much(
    async_session_factory, setup, pipeline, mode_with_token
):
    """A name with the tokens of two modes of one polarity parks a file,
    and choosing its chemistry is the remedy its status offers. The choice
    is not refused at the next re-process for the name it answered."""
    (_first, first_token), (_second, second_token) = (
        await mode_with_token("-"),
        await mode_with_token("-"),
    )
    both = await _file(
        async_session_factory,
        f"{first_token}_{second_token}",
        "-",
        sample_under=(setup["batch"], setup["negative"]),
        bound_by="explicit",
    )

    await process_service.re_process_sample_files(sample_file_ids=[both])

    pipeline.assert_awaited_once()
    started = pipeline.await_args.kwargs
    assert started["ionization_mode_ids"] == [setup["negative"]]
    assert started["kept_provenance"] == {
        setup["negative"]: process_service.ItemProvenance("explicit")
    }


@pytest.mark.asyncio
async def test_reprocessing_refuses_an_ambiguous_name_nobody_chose_for(
    async_session_factory, setup, pipeline
):
    """Only a name no token matches stands its earlier binding in, where
    that binding was nobody's choice."""
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
async def test_reprocessing_takes_over_a_stalled_run(
    async_session_factory, setup, pipeline
):
    bound = await _file(
        async_session_factory,
        "stalled",
        "-",
        sample_under=(setup["batch"], setup["negative"]),
    )
    await _set_status(async_session_factory, bound, "queued", recorded_ago=_STALLED)

    result = await process_service.re_process_sample_files(sample_file_ids=[bound])

    assert "Successfully re-processed 1" in result["message"]
    pipeline.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("place", PLACES)
async def test_reprocessing_refuses_a_file_someone_made_a_sample_from(
    async_session_factory, setup, pipeline, test_users, place
):
    """Whatever the sample's type, and wherever it is: re-processing would
    delete it."""
    bound = await _file(
        async_session_factory,
        "used",
        "-",
        sample_under=(setup["batch"], setup["negative"]),
    )
    await _user_sample(
        async_session_factory,
        test_users,
        bound,
        place=place,
        instrument_workspace=setup["workspace"],
    )

    with pytest.raises(Exception, match="user-created"):
        await process_service.re_process_sample_files(sample_file_ids=[bound])

    pipeline.assert_not_called()


# ---------------------------------------------------------------------------
# Re-processing detects a file's peaks under the setting of the moment
# ---------------------------------------------------------------------------


class _Steps:
    """What a re-processing does to a file, in order.

    The decision and the detection are stood in for
    (``process.peaks``, tested on a scripted file in
    ``test_peaks_redetected_on_reprocess``): ``decision`` is what the file is
    decided, or the error reading it raises, and ``detection_error`` what
    detecting its peaks raises.
    """

    def __init__(self):
        self.done: list[str] = []
        self.decision: bool | None | Exception = True
        self.detection_error: Exception | None = None


@pytest.fixture
def steps(monkeypatch, pipeline) -> _Steps:
    steps = _Steps()
    clear = process_service._clear_sample_items_for_reprocessing

    async def decide(sample_file):
        steps.done.append("decided")
        if isinstance(steps.decision, Exception):
            raise steps.decision
        return steps.decision

    async def detect(sample_file, per_stream):
        if steps.detection_error is not None:
            raise steps.detection_error
        steps.done.append(f"detected {'per stream' if per_stream else 'whole'}")

    async def reset(sample_file):
        steps.done.append("reset the calibration")

    async def cleared(**kwargs):
        steps.done.append("cleared the samples")
        return await clear(**kwargs)

    async def ran(**kwargs):
        steps.done.append("ran the pipeline")
        return {"_notification_data": {}}

    monkeypatch.setattr(process_service, "redetection_decision", decide)
    monkeypatch.setattr(process_service, "redetect_peaks", detect)
    monkeypatch.setattr(process_service, "reset_mz_calibration", reset)
    monkeypatch.setattr(
        process_service, "_clear_sample_items_for_reprocessing", cleared
    )
    pipeline.side_effect = ran
    return steps


async def _detail(async_session_factory, sample_file_id: str) -> str | None:
    async with async_session_factory() as session:
        return await session.scalar(
            select(SampleFile.processing_detail).where(
                SampleFile.sample_file_id == sample_file_id
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "decision, detected",
    [(True, "detected per stream"), (False, "detected whole")],
    ids=["setting-on", "setting-off"],
)
async def test_reprocessing_detects_a_files_peaks_before_it_makes_its_samples(
    async_session_factory, setup, steps, decision, detected
):
    """On the acquisition axis, as a first conversion detects them, so after
    the calibration is reset; and under the samples the pipeline is about to
    make, so before the old ones go and the new ones are cut from the store."""
    bound = await _file(
        async_session_factory,
        "restitched",
        "-",
        sample_under=(setup["batch"], setup["negative"]),
    )
    steps.decision = decision

    result = await process_service.re_process_sample_files(sample_file_ids=[bound])

    assert "Successfully re-processed 1" in result["message"]
    assert steps.done == [
        "decided",
        "reset the calibration",
        detected,
        "cleared the samples",
        "ran the pipeline",
    ]


@pytest.mark.asyncio
async def test_reprocessing_leaves_the_peaks_of_a_file_nothing_is_decided_for(
    async_session_factory, setup, steps
):
    """Nearly every file: its store is the one any decision gives it."""
    bound = await _file(
        async_session_factory,
        "as-it-was",
        "-",
        sample_under=(setup["batch"], setup["negative"]),
    )
    steps.decision = None

    await process_service.re_process_sample_files(sample_file_ids=[bound])

    assert steps.done == [
        "decided",
        "reset the calibration",
        "cleared the samples",
        "ran the pipeline",
    ]


@pytest.mark.asyncio
async def test_reprocessing_refuses_a_file_it_cannot_decide_for_untouched(
    async_session_factory, setup, steps
):
    """A file whose streams cannot be read cannot be told from one whose
    peaks have to be detected apart. It is refused before it is claimed, so
    it keeps the status of its last run, its calibration and its samples.

    The report says which read failed and not what the error said, whose
    text can hold a path of the filestore: that is left to the log, as for
    every other read a re-processing refuses a file on."""
    unreadable = await _file(
        async_session_factory,
        "unreadable",
        "-",
        sample_under=(setup["batch"], setup["negative"]),
    )
    steps.decision = OSError("/srv/filestore/site/file: the raw file is gone")

    with pytest.raises(Exception, match="nothing of it was changed") as refused:
        await process_service.re_process_sample_files(sample_file_ids=[unreadable])

    assert "Could not read its scan streams or its peak store" in _report(refused)
    assert "the raw file is gone" not in _report(refused)
    assert "/srv/filestore" not in _report(refused)
    assert steps.done == ["decided"]
    assert await _status(async_session_factory, unreadable) == "done"
    assert await _samples_of(async_session_factory, unreadable) == 1


@pytest.mark.asyncio
async def test_a_file_whose_peaks_cannot_be_detected_again_keeps_its_samples(
    async_session_factory, setup, steps
):
    """Its run ends there, and says so: no pipeline follows to record how it
    ended. The samples of its last run are still its samples, and the status
    everyone who lists the file can read names the stage and not the error,
    whose text can hold a path. The report to whoever asked adds the kind of
    failure, in the words a failed file's status uses, and no more of the
    error than that."""
    failing = await _file(
        async_session_factory,
        "undetectable",
        "-",
        sample_under=(setup["batch"], setup["negative"]),
    )
    steps.detection_error = OSError("/srv/filestore/site/file: No space left")

    with pytest.raises(Exception, match="could not be detected again") as failed:
        await process_service.re_process_sample_files(sample_file_ids=[failing])

    assert "Processing stopped on an unexpected error" in _report(failed)
    assert "No space left" not in _report(failed)
    assert "/srv/filestore" not in _report(failed)
    assert steps.done == ["decided", "reset the calibration"]
    assert await _samples_of(async_session_factory, failing) == 1
    assert await _status(async_session_factory, failing) == "failed"
    detail = await _detail(async_session_factory, failing)
    assert "could not be detected again" in detail
    assert "/srv/filestore" not in detail


@pytest.mark.asyncio
async def test_one_files_failed_detection_does_not_stop_the_files_after_it(
    async_session_factory, setup, steps, monkeypatch
):
    first = await _file(
        async_session_factory,
        "fails-first",
        "-",
        sample_under=(setup["batch"], setup["negative"]),
    )
    second = await _file(
        async_session_factory,
        "follows",
        "-",
        sample_under=(setup["batch"], setup["negative"]),
    )
    failed_for: list[str] = []

    async def detect(sample_file, per_stream):
        if not failed_for:
            failed_for.append(sample_file.sample_file_id)
            raise OSError("No space left")
        steps.done.append("detected per stream")

    monkeypatch.setattr(process_service, "redetect_peaks", detect)

    # A partial result is a warning, which the task reports and does not raise
    await process_service.re_process_sample_files(sample_file_ids=[first, second])

    assert steps.done.count("ran the pipeline") == 1
    other = second if failed_for == [first] else first
    assert await _samples_of(async_session_factory, failed_for[0]) == 1
    assert await _status(async_session_factory, failed_for[0]) == "failed"
    assert await _status(async_session_factory, other) == "queued"


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
@pytest.mark.parametrize(
    "under", [None, "positive"], ids=["mode deleted", "mode of another polarity"]
)
async def test_processing_on_request_leaves_a_file_no_mode_binds_its_samples(
    async_session_factory, setup, admin_client, monkeypatch, under
):
    """A file bound once, whose samples no longer give a mode per polarity:
    a mode deleted since leaves them with none, and one whose polarity was
    changed since is no mode of the file's. It has no modes to keep, which
    is what a parked file with no samples says too - but this one has
    samples, and with the method rung off a run could only park it. It is
    refused before it is claimed, and keeps its status."""
    monkeypatch.setattr(process_service, "routes_on_method_binding", lambda: False)
    orphaned = await _file(
        async_session_factory,
        "on-request-mode-deleted",
        "-",
        sample_under=(setup["batch"], setup[under] if under else None),
    )
    spawn = AsyncMock()
    monkeypatch.setattr(sample_files_routes, "spawn_auto_process_sample_file", spawn)

    resp = await admin_client.post(f"/api/sample/files/{orphaned}/process")

    assert resp.status_code == 422, resp.text
    reason = resp.json()["error"]
    assert "ionization mode tokens" in reason.lower()
    assert "no longer bind it" in reason
    assert "Choose its chemistry in Raw files" in reason
    spawn.assert_not_called()
    assert await _samples_of(async_session_factory, orphaned) == 1
    assert await _status(async_session_factory, orphaned) == "done"


def _method_binds(monkeypatch, setup) -> tuple[AsyncMock, list[dict]]:
    """Have the method rung bind a file, and give the file a census to read.

    :return: The rung's stand-in, and the census it should be handed.
    """
    census = _census(1)
    monkeypatch.setattr(
        process_service, "read_scan_streams", AsyncMock(return_value=census)
    )
    rung = AsyncMock(
        return_value=(
            [
                MethodRouting(
                    mode=IonizationMode(ionization_mode_id=setup["negative"]),
                    binding_id=gen_id(),
                )
            ],
            None,
        )
    )
    monkeypatch.setattr(process_service, "resolve_modes_by_method_binding", rung)
    return rung, census


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "under", [None, "positive"], ids=["mode deleted", "mode of another polarity"]
)
async def test_processing_on_request_leaves_a_file_its_method_binds_to_the_run(
    async_session_factory, setup, admin_client, monkeypatch, under
):
    """The same file where the server routes on method bindings, and its
    acquisition method binds it. The method is asked as the run asks it, on
    the file's scan-stream census, and the file starts with no modes of its
    own. The run asks again and rebuilds it under that chemistry; a run
    binds a file before it clears anything of it, so one whose method no
    longer binds it by then parks with its samples as they were."""
    monkeypatch.setattr(process_service, "routes_on_method_binding", lambda: True)
    rung, census = _method_binds(monkeypatch, setup)
    orphaned = await _file(
        async_session_factory,
        "on-request-bound-by-its-method",
        "-",
        sample_under=(setup["batch"], setup[under] if under else None),
    )
    spawn = AsyncMock()
    monkeypatch.setattr(sample_files_routes, "spawn_auto_process_sample_file", spawn)

    resp = await admin_client.post(f"/api/sample/files/{orphaned}/process")

    assert resp.status_code == 202, resp.text
    asked_for, streams = rung.await_args.args
    assert asked_for.sample_file_id == orphaned
    assert streams is census
    assert spawn.await_args.kwargs["sample_file_id"] == orphaned
    assert spawn.await_args.kwargs["ionization_mode_ids"] is None
    assert spawn.await_args.kwargs["kept_provenance"] is None
    assert await _samples_of(async_session_factory, orphaned) == 1
    assert await _status(async_session_factory, orphaned) == "queued"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "under", [None, "positive"], ids=["mode deleted", "mode of another polarity"]
)
async def test_processing_on_request_refuses_a_file_its_method_does_not_bind(
    async_session_factory, setup, admin_client, monkeypatch, under
):
    """The same file, where the method does not bind it either: a run could
    only park it. It is refused before it is claimed, with the method's own
    reason and remedy, and keeps its status. The rung itself is asked: this
    file's method reports no name, which is one of the ways it declines."""
    monkeypatch.setattr(process_service, "routes_on_method_binding", lambda: True)
    monkeypatch.setattr(
        process_service, "read_scan_streams", AsyncMock(return_value=[])
    )
    orphaned = await _file(
        async_session_factory,
        "on-request-declined-by-its-method",
        "-",
        sample_under=(setup["batch"], setup[under] if under else None),
    )
    spawn = AsyncMock()
    monkeypatch.setattr(sample_files_routes, "spawn_auto_process_sample_file", spawn)

    resp = await admin_client.post(f"/api/sample/files/{orphaned}/process")

    assert resp.status_code == 422, resp.text
    reason = resp.json()["error"]
    assert "ionization mode tokens" in reason.lower()
    assert "no longer bind it" in reason
    assert "does not say either: its acquisition method reports no name" in reason
    assert "Files of this instrument need a filename token" in reason
    assert "It was left as it is." in reason
    spawn.assert_not_called()
    assert await _samples_of(async_session_factory, orphaned) == 1
    assert await _status(async_session_factory, orphaned) == "done"


@pytest.mark.asyncio
@pytest.mark.parametrize("by_method", [False, True], ids=["rung off", "rung on"])
async def test_processing_on_request_files_a_file_of_fragmentation_scans_again(
    async_session_factory, setup, admin_client, monkeypatch, by_method
):
    """The file above, but its census holds no MS1 scan: its one sample is
    the MS2 part its run filed without a chemistry, with no mode, and the
    run files it again after clearing it. Nothing of it waits for a
    chemistry, so it is started with no modes of its own, as re-processing
    starts it."""
    monkeypatch.setattr(process_service, "routes_on_method_binding", lambda: by_method)
    monkeypatch.setattr(
        process_service, "read_scan_streams", AsyncMock(return_value=_census(2))
    )
    filed = await _file(
        async_session_factory,
        "on-request-ms2-only",
        "-",
        sample_under=(setup["batch"], None),
    )
    spawn = AsyncMock()
    monkeypatch.setattr(sample_files_routes, "spawn_auto_process_sample_file", spawn)

    resp = await admin_client.post(f"/api/sample/files/{filed}/process")

    assert resp.status_code == 202, resp.text
    spawn.assert_awaited_once()
    assert spawn.await_args.kwargs["ionization_mode_ids"] is None


@pytest.mark.asyncio
async def test_processing_on_request_asks_the_record_of_a_file_no_mode_binds(
    async_session_factory, setup, admin_client, mode_with_token, monkeypatch
):
    """The record is asked before the samples are counted. A file its record
    binds starts with no modes of its own though its samples have none: the
    run binds it by the record, and rebuilds it. One whose record names a
    chemistry nothing here answers to is refused where the method rung is
    off, and the reason says what the record named."""
    monkeypatch.setattr(process_service, "routes_on_method_binding", lambda: False)
    _mode_id, token = await mode_with_token("-")
    declared = await _file(
        async_session_factory,
        "on-request-record-binds",
        "-",
        sample_under=(setup["batch"], None),
        declaring=token,
    )
    unanswered = await _file(
        async_session_factory,
        "on-request-record-unanswered",
        "-",
        sample_under=(setup["batch"], None),
        declaring="T-no-mode-has",
    )
    spawn = AsyncMock()
    monkeypatch.setattr(sample_files_routes, "spawn_auto_process_sample_file", spawn)

    resp = await admin_client.post(f"/api/sample/files/{declared}/process")

    assert resp.status_code == 202, resp.text
    assert spawn.await_args.kwargs["sample_file_id"] == declared
    assert spawn.await_args.kwargs["ionization_mode_ids"] is None

    resp = await admin_client.post(f"/api/sample/files/{unanswered}/process")

    assert resp.status_code == 422, resp.text
    reason = resp.json()["error"]
    assert "names the chemistry 'T-no-mode-has', but no ionization mode" in reason
    assert "no longer bind it" in reason
    spawn.assert_awaited_once()
    assert await _samples_of(async_session_factory, unanswered) == 1
    assert await _status(async_session_factory, unanswered) == "done"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "binds", [True, False], ids=["method binds", "method declines"]
)
async def test_processing_on_request_asks_the_method_after_an_unanswered_record(
    async_session_factory, setup, admin_client, monkeypatch, binds
):
    """A record that names a chemistry nothing here answers to binds nothing,
    so where the method rung is on, the file's acquisition method is asked
    next, as the run asks it. A file its method binds is left to the run. One
    it does not bind is refused, and the reason says what the record named
    and why the method did not answer: this file's method reports no name."""
    monkeypatch.setattr(process_service, "routes_on_method_binding", lambda: True)
    if binds:
        _method_binds(monkeypatch, setup)
    else:
        monkeypatch.setattr(
            process_service, "read_scan_streams", AsyncMock(return_value=[])
        )
    unanswered = await _file(
        async_session_factory,
        "on-request-record-unanswered-rung-on",
        "-",
        sample_under=(setup["batch"], None),
        declaring="T-no-mode-has",
    )
    spawn = AsyncMock()
    monkeypatch.setattr(sample_files_routes, "spawn_auto_process_sample_file", spawn)

    resp = await admin_client.post(f"/api/sample/files/{unanswered}/process")

    if binds:
        assert resp.status_code == 202, resp.text
        assert spawn.await_args.kwargs["ionization_mode_ids"] is None
        assert await _status(async_session_factory, unanswered) == "queued"
    else:
        assert resp.status_code == 422, resp.text
        reason = resp.json()["error"]
        assert "names the chemistry 'T-no-mode-has', but no ionization mode" in reason
        assert "does not say either: its acquisition method reports no name" in reason
        spawn.assert_not_called()
        assert await _status(async_session_factory, unanswered) == "done"
    assert await _samples_of(async_session_factory, unanswered) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("by_method", [False, True], ids=["rung off", "rung on"])
async def test_processing_a_parked_file_on_request_leaves_it_to_the_run(
    async_session_factory, setup, admin_client, monkeypatch, by_method
):
    """No samples, so nothing a run could cost it: it starts with no modes
    of its own, and the run binds it or parks it again. Where the method
    rung is on, the method is not asked first: this file's method reports
    no name, and asked here it would decline."""
    monkeypatch.setattr(process_service, "routes_on_method_binding", lambda: by_method)
    monkeypatch.setattr(
        process_service, "read_scan_streams", AsyncMock(return_value=[])
    )
    parked = await _file(async_session_factory, "on-request-parked", "-")
    spawn = AsyncMock()
    monkeypatch.setattr(sample_files_routes, "spawn_auto_process_sample_file", spawn)

    resp = await admin_client.post(f"/api/sample/files/{parked}/process")

    assert resp.status_code == 202, resp.text
    assert spawn.await_args.kwargs["ionization_mode_ids"] is None
    assert spawn.await_args.kwargs["kept_provenance"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("by_method", [False, True], ids=["rung off", "rung on"])
async def test_processing_on_request_leaves_an_ambiguous_name_its_samples(
    async_session_factory, setup, admin_client, mode_with_token, monkeypatch, by_method
):
    """A name two tokens of one polarity match is a configuration to fix,
    which no earlier binding stands in for and the method rung is never
    asked about: the run would bind the file to nothing and park it.
    Refused before it is claimed, with what the tokens matched and the two
    ways out its park would have offered. The same name on a file with no
    samples is left to its run, which says why it parks."""
    monkeypatch.setattr(process_service, "routes_on_method_binding", lambda: by_method)
    _one, token_one = await mode_with_token("-")
    _two, token_two = await mode_with_token("-")
    bound = await _file(
        async_session_factory,
        f"{token_one}_{token_two}_bound",
        "-",
        sample_under=(setup["batch"], setup["negative"]),
    )
    parked = await _file(async_session_factory, f"{token_one}_{token_two}_parked", "-")
    spawn = AsyncMock()
    monkeypatch.setattr(sample_files_routes, "spawn_auto_process_sample_file", spawn)

    resp = await admin_client.post(f"/api/sample/files/{bound}/process")

    assert resp.status_code == 422, resp.text
    reason = resp.json()["error"]
    assert "2 modes match polarity -" in reason
    assert "Configure tokens in ionization settings" in reason
    assert "Or choose its chemistry in Raw files" in reason
    assert "left as it is" in reason
    spawn.assert_not_called()
    assert await _samples_of(async_session_factory, bound) == 1
    assert await _status(async_session_factory, bound) == "done"

    resp = await admin_client.post(f"/api/sample/files/{parked}/process")

    assert resp.status_code == 202, resp.text
    assert spawn.await_args.kwargs["sample_file_id"] == parked
    assert spawn.await_args.kwargs["ionization_mode_ids"] is None


@pytest.mark.asyncio
async def test_processing_on_request_keeps_a_choice_for_an_ambiguous_name(
    async_session_factory, setup, admin_client, mode_with_token, monkeypatch
):
    """Choosing its chemistry is the way out the refusal above offers, so
    the file it was chosen for is started under that choice, ahead of the
    name, as re-processing starts it."""
    _one, token_one = await mode_with_token("-")
    _two, token_two = await mode_with_token("-")
    chosen = await _file(
        async_session_factory,
        f"{token_one}_{token_two}_chosen",
        "-",
        sample_under=(setup["batch"], setup["negative"]),
        bound_by="explicit",
    )
    spawn = AsyncMock()
    monkeypatch.setattr(sample_files_routes, "spawn_auto_process_sample_file", spawn)

    resp = await admin_client.post(f"/api/sample/files/{chosen}/process")

    assert resp.status_code == 202, resp.text
    assert spawn.await_args.kwargs["ionization_mode_ids"] == [setup["negative"]]
    assert spawn.await_args.kwargs["kept_provenance"] == {
        setup["negative"]: process_service.ItemProvenance("explicit")
    }


@pytest.mark.asyncio
async def test_processing_on_request_fails_when_its_samples_cannot_be_counted(
    async_session_factory, setup, admin_client, monkeypatch
):
    """Samples that could not be counted are not no samples: started with no
    modes on that guess, the run would clear the one this file has. The
    request fails before anything is claimed, as it does for a record and
    for a choice that cannot be read."""
    monkeypatch.setattr(
        process_service,
        "_pipeline_item_count",
        AsyncMock(side_effect=RuntimeError("the database went away")),
    )
    unread = await _file(
        async_session_factory,
        "on-request-samples-unread",
        "-",
        sample_under=(setup["batch"], None),
    )
    spawn = AsyncMock()
    monkeypatch.setattr(sample_files_routes, "spawn_auto_process_sample_file", spawn)

    resp = await admin_client.post(f"/api/sample/files/{unread}/process")

    assert resp.status_code >= 500, resp.text
    assert "the database went away" not in resp.text
    spawn.assert_not_called()
    assert await _samples_of(async_session_factory, unread) == 1
    assert await _status(async_session_factory, unread) == "done"


@pytest.mark.asyncio
async def test_processing_on_request_asks_the_record_before_the_modes_a_file_has(
    async_session_factory, setup, admin_client, mode_with_token, monkeypatch
):
    """As re-processing does: the two start a run by the same rule."""
    _mode_id, token = await mode_with_token("-")
    bound = await _file(
        async_session_factory,
        "on-request-with-a-record",
        "-",
        sample_under=(setup["batch"], setup["negative"]),
        declaring=token,
        bound_by="declared",
    )
    spawn = AsyncMock()
    monkeypatch.setattr(sample_files_routes, "spawn_auto_process_sample_file", spawn)

    resp = await admin_client.post(f"/api/sample/files/{bound}/process")

    assert resp.status_code == 202, resp.text
    assert spawn.await_args.kwargs["ionization_mode_ids"] is None
    assert spawn.await_args.kwargs["kept_provenance"] is None


@pytest.mark.asyncio
async def test_processing_on_request_keeps_a_chemistry_a_person_chose(
    async_session_factory, setup, admin_client, mode_with_token, monkeypatch
):
    _mode_id, token = await mode_with_token("-")
    chosen = await _file(
        async_session_factory,
        "on-request-chosen-by-hand",
        "-",
        sample_under=(setup["batch"], setup["negative"]),
        declaring=token,
        bound_by="explicit",
    )
    spawn = AsyncMock()
    monkeypatch.setattr(sample_files_routes, "spawn_auto_process_sample_file", spawn)

    resp = await admin_client.post(f"/api/sample/files/{chosen}/process")

    assert resp.status_code == 202, resp.text
    assert spawn.await_args.kwargs["ionization_mode_ids"] == [setup["negative"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("with_a_record", [True, False])
async def test_processing_on_request_keeps_a_choice_for_a_file_its_name_binds(
    async_session_factory,
    setup,
    admin_client,
    mode_with_token,
    monkeypatch,
    with_a_record,
):
    """As re-processing does: a token in the file's name does not undo what
    a person chose, with a record or without one."""
    _mode_id, token = await mode_with_token("-")
    named = await _file(
        async_session_factory,
        f"{token}_on-request-chosen",
        "-",
        sample_under=(setup["batch"], setup["negative"]),
        declaring=token if with_a_record else None,
        bound_by="explicit",
    )
    spawn = AsyncMock()
    monkeypatch.setattr(sample_files_routes, "spawn_auto_process_sample_file", spawn)

    resp = await admin_client.post(f"/api/sample/files/{named}/process")

    assert resp.status_code == 202, resp.text
    assert spawn.await_args.kwargs["ionization_mode_ids"] == [setup["negative"]]
    assert spawn.await_args.kwargs["kept_provenance"] == {
        setup["negative"]: process_service.ItemProvenance("explicit")
    }


@pytest.mark.asyncio
async def test_processing_on_request_reads_the_name_of_a_file_nobody_chose_for(
    async_session_factory, setup, admin_client, mode_with_token, monkeypatch
):
    _mode_id, token = await mode_with_token("-")
    named = await _file(
        async_session_factory,
        f"{token}_on-request-unchosen",
        "-",
        sample_under=(setup["batch"], setup["negative"]),
        bound_by="token",
    )
    spawn = AsyncMock()
    monkeypatch.setattr(sample_files_routes, "spawn_auto_process_sample_file", spawn)

    resp = await admin_client.post(f"/api/sample/files/{named}/process")

    assert resp.status_code == 202, resp.text
    assert spawn.await_args.kwargs["ionization_mode_ids"] is None
    assert spawn.await_args.kwargs["kept_provenance"] is None


@pytest.mark.asyncio
async def test_processing_on_request_fails_when_the_choice_cannot_be_read(
    async_session_factory, setup, admin_client, mode_with_token, monkeypatch
):
    """Before anything is claimed, as for a record: no run starts on a guess
    that nobody chose the file's chemistry."""
    _mode_id, token = await mode_with_token("-")
    chosen = await _file(
        async_session_factory,
        f"{token}_on-request-choice-unread",
        "-",
        sample_under=(setup["batch"], setup["negative"]),
        bound_by="explicit",
    )
    monkeypatch.setattr(
        process_service,
        "_kept_modes",
        AsyncMock(side_effect=RuntimeError("the database went away")),
    )
    spawn = AsyncMock()
    monkeypatch.setattr(sample_files_routes, "spawn_auto_process_sample_file", spawn)

    resp = await admin_client.post(f"/api/sample/files/{chosen}/process")

    assert resp.status_code >= 500, resp.text
    spawn.assert_not_awaited()
    assert await _status(async_session_factory, chosen) == "done"


@pytest.mark.asyncio
async def test_processing_on_request_fails_when_the_record_cannot_be_read(
    async_session_factory, setup, admin_client, monkeypatch
):
    """Before anything is claimed: the file is as it was, and no run starts
    on a guess about what its record says."""
    monkeypatch.setattr(
        process_service,
        "_modes_its_record_declares",
        AsyncMock(side_effect=RuntimeError("the database went away")),
    )
    unread = await _file(
        async_session_factory,
        "on-request-record-unread",
        "-",
        sample_under=(setup["batch"], setup["negative"]),
        declaring="T-anything",
        bound_by="declared",
    )
    spawn = AsyncMock()
    monkeypatch.setattr(sample_files_routes, "spawn_auto_process_sample_file", spawn)

    resp = await admin_client.post(f"/api/sample/files/{unread}/process")

    assert resp.status_code >= 500, resp.text
    spawn.assert_not_awaited()
    assert await _status(async_session_factory, unread) == "done"


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
async def test_processing_a_stalled_file_on_request_takes_it_over(
    async_session_factory, setup, admin_client, monkeypatch
):
    stalled = await _file(async_session_factory, "stalled", "-")
    await _set_status(async_session_factory, stalled, "calibrated", _STALLED)
    spawn = AsyncMock()
    monkeypatch.setattr(sample_files_routes, "spawn_auto_process_sample_file", spawn)

    resp = await admin_client.post(f"/api/sample/files/{stalled}/process")

    assert resp.status_code == 202, resp.text
    spawn.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("place", PLACES)
async def test_a_rerun_clears_only_the_pipelines_own_samples(
    async_session_factory, setup, test_users, place
):
    """A person's sample from the file stays, whatever its type and place."""
    bound = await _file(
        async_session_factory,
        "cleared",
        "-",
        sample_under=(setup["batch"], setup["negative"]),
    )
    mine = await _user_sample(
        async_session_factory,
        test_users,
        bound,
        place=place,
        instrument_workspace=setup["workspace"],
    )

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


# ---------------------------------------------------------------------------
# A run binds a file before it clears what an earlier run left
# ---------------------------------------------------------------------------


@pytest.fixture
def run_to_the_binding(setup, monkeypatch) -> AsyncMock:
    """Let the pipeline itself run, on the rows, as far as its binding.

    The checks above stand in front of a run, and the run can start minutes
    after them: behind the ingest gate, or behind the files a re-processing
    batch takes first. This is the run. What reads the filestore is stood
    in for, and so is the m/z reset, which gives whether it ran.
    """
    reset = AsyncMock()
    for name, stand_in in {
        "read_scan_streams": AsyncMock(return_value=[]),
        "read_store_stitch": AsyncMock(return_value=status.StoreStitch()),
        "get_acquisition_dataset": AsyncMock(
            return_value={"data": {"dataset_id": setup["dataset"]}}
        ),
        "learn_method_bindings": AsyncMock(),
        "reset_mz_calibration": reset,
    }.items():
        monkeypatch.setattr(process_service, name, stand_in)
    return reset


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("chosen", "by_method"),
    [(True, False), (False, False), (False, True)],
    ids=["a chosen mode deleted", "no modes, no token", "no modes, rung declines"],
)
async def test_a_run_that_cannot_bind_a_file_leaves_it_its_samples(
    async_session_factory, setup, run_to_the_binding, monkeypatch, chosen, by_method
):
    """A mode chosen for the file was deleted while the run waited, or the
    run was started with no modes for a file whose name and record bind it
    to nothing. The run parks it, and the sample an earlier run made, and
    the calibration it was matched on, are as they were. The status says the
    file needs a chemistry, and that its samples were kept."""
    monkeypatch.setattr(process_service, "routes_on_method_binding", lambda: by_method)
    bound = await _file(
        async_session_factory,
        "kept-by-a-parked-run",
        "-",
        sample_under=(setup["batch"], setup["negative"]),
        bound_by="explicit",
    )

    result = await process_service.auto_process_sample_file(
        sample_file_id=bound,
        ionization_mode_ids=[gen_id(16)] if chosen else None,
        reset_calibration=True,
    )

    assert result["status"] == "parked"
    assert await _samples_of(async_session_factory, bound) == 1
    run_to_the_binding.assert_not_awaited()
    assert await _status(async_session_factory, bound) == "needs_chemistry"
    assert "Its samples, from an earlier run, were left as they were." in (
        await _detail(async_session_factory, bound)
    )


@pytest.mark.asyncio
async def test_a_run_that_binds_a_file_clears_it_before_making_samples(
    async_session_factory, setup, run_to_the_binding, monkeypatch
):
    """The other half. A bound file's samples are still replaced, and gone
    before the new ones are made: create_acquisition_batches_and_items does
    not look for existing ones. Its calibration is reset by then too."""
    bound = await _file(
        async_session_factory,
        "replaced-by-a-bound-run",
        "-",
        sample_under=(setup["batch"], setup["negative"]),
    )
    seen = {}

    async def create(**_):
        seen["samples"] = await _samples_of(async_session_factory, bound)
        seen["resets"] = run_to_the_binding.await_count
        raise _SeenEnough

    monkeypatch.setattr(process_service, "create_acquisition_batches_and_items", create)

    # Undecorated: the background-task decorator re-raises anything but an
    # ApiException as one, so _SeenEnough would not reach this test through it.
    with pytest.raises(_SeenEnough):
        await process_service.auto_process_sample_file.__wrapped__(
            sample_file_id=bound,
            ionization_mode_ids=[setup["negative"]],
            reset_calibration=True,
        )

    assert seen == {"samples": 0, "resets": 1}


@pytest.mark.asyncio
async def test_a_retry_that_cannot_bind_a_file_clears_what_its_own_run_made(
    async_session_factory, setup, run_to_the_binding, mode_with_token, monkeypatch
):
    """The first attempt binds the file, clears the earlier run's sample,
    makes its own and fails on a busy database. The chosen mode is deleted
    while it waits, and the retry cannot bind the file. The sample left is
    the first attempt's, never calibrated or matched, and no earlier run's:
    the park clears it, as a retry that bound the file would have, and says
    nothing of kept samples."""
    chosen, _token = await mode_with_token("-")
    bound = await _file(
        async_session_factory,
        "retried-into-a-park",
        "-",
        sample_under=(setup["batch"], setup["negative"]),
    )
    attempts = []

    async def create(**_):
        attempts.append(await _samples_of(async_session_factory, bound))
        async with async_session_factory() as session:
            session.add(
                SampleItem(
                    sample_item_id=gen_id(),
                    sample_batch_id=setup["batch"],
                    sample_file_id=bound,
                    sample_item_name="2026-09-01 12:00:00",
                    sample_item_type="ACQUISITION",
                    sample_item_attributes={},
                    polarity="-",
                    sample_item_utc_created=_NOW,
                )
            )
            await session.execute(
                delete(IonizationMode).where(
                    IonizationMode.ionization_mode_id == chosen
                )
            )
            await session.commit()
        raise ApiException("busy", {}, 503)

    monkeypatch.setattr(process_service, "create_acquisition_batches_and_items", create)
    monkeypatch.setattr(process_service, "_AUTO_PROCESS_RETRY_DELAYS_S", (0, 0, 0))
    monkeypatch.setattr(process_service, "_shutdown", asyncio.Event())

    result = await process_service.auto_process_sample_file(
        sample_file_id=bound, ionization_mode_ids=[chosen]
    )

    assert result["status"] == "parked"
    # The earlier run's sample was gone before the first attempt made its own.
    assert attempts == [0]
    assert await _samples_of(async_session_factory, bound) == 0
    assert await _status(async_session_factory, bound) == "needs_chemistry"
    detail = await _detail(async_session_factory, bound)
    assert f"No ionization mode has the id {chosen}" in detail
    assert "earlier run" not in detail
