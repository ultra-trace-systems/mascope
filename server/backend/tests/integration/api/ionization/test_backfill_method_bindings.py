"""
Tests: reading method bindings out of files already routed.

The script runs by hand on a production database, so nothing else exercises
it. These do: they seed the shape it reads - an ACQUISITION item, in an
ACQUISITION batch, of an ACQUISITION dataset, in a system workspace - and run
the script's body against it.

Two of them exist because of what a first version got wrong. It selected
`json` columns under `SELECT DISTINCT`, which Postgres refuses outright
("could not identify an equality operator for type json"), so the script
could not run at all. And it skipped keys already in the table instead of
folding into them - which, since learning is on by default, meant the busiest
methods, whose rows live learning had already made, were exactly the ones
that never got their history.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from mascope_backend.db import (
    Dataset,
    IonizationMode,
    MethodBinding,
    SampleBatch,
    SampleFile,
    SampleItem,
    Workspace,
)
from mascope_backend.db.id import gen_id
from mascope_backend.db.scripts.backfill_method_bindings import (
    backfill_method_bindings,
)
from mascope_backend.method_keys import binding_digest, method_key, signature_class


TOF_METHOD = "nitrate_tof.meth"


@pytest_asyncio.fixture
async def history(async_session_factory):
    """A system workspace with an acquisition dataset, and a way to fill it.

    TOF files throughout: their reader records no scan-stream census, so the
    signature class is the polarity and nothing has to read a `.props` file
    off a filestore these tests do not have.
    """
    instrument = f"instrument-{gen_id(8)}"
    made: dict[str, list] = {
        "files": [],
        "items": [],
        "modes": [],
        "workspaces": [],
    }

    workspace = Workspace(
        workspace_id=gen_id(),
        workspace_name=f"Backfill test {gen_id(6)}",
        is_system=True,
    )
    dataset = Dataset(
        dataset_id=gen_id(),
        workspace_id=workspace.workspace_id,
        dataset_name=f"Acquisitions {gen_id(6)}",
        dataset_type="ACQUISITION",
        instrument=instrument,
    )
    batch = SampleBatch(
        sample_batch_id=gen_id(),
        dataset_id=dataset.dataset_id,
        sample_batch_name=f"Batch {gen_id(6)}",
        sample_batch_type="ACQUISITION",
    )
    async with async_session_factory() as session:
        session.add_all([workspace, dataset, batch])
        await session.commit()

    async def add_mode(polarity="-", mechanisms=("mech-no3", "mech-deprot")):
        mode = IonizationMode(
            ionization_mode_id=gen_id(),
            ionization_mode_name=f"Mode {gen_id(8)}",
            ionization_mode_polarity=polarity,
            ionization_mechanism_ids=list(mechanisms),
        )
        async with async_session_factory() as session:
            session.add(mode)
            await session.commit()
        made["modes"].append(mode.ionization_mode_id)
        return mode

    async def add_file(
        mode,
        *,
        minutes=0,
        instrument_type="tof",
        method_file=TOF_METHOD,
        batch_type="ACQUISITION",
        item_type="ACQUISITION",
        dataset_type="ACQUISITION",
        system_workspace=True,
        items=1,
    ):
        when = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=minutes)
        sample_file = SampleFile(
            sample_file_id=gen_id(),
            filename=f"{gen_id(10)}.raw",
            instrument=instrument,
            instrument_type=instrument_type,
            method_file=method_file,
            datetime=when.replace(tzinfo=None),
            datetime_utc=when,
            length=60.0,
            range=[40.0, 600.0],
            polarity=mode.ionization_mode_polarity,
        )
        extra = []
        holder = batch
        if (
            batch_type != "ACQUISITION"
            or dataset_type != "ACQUISITION"
            or not system_workspace
        ):
            home = dataset
            if dataset_type != "ACQUISITION" or not system_workspace:
                other_workspace = workspace
                if not system_workspace:
                    other_workspace = Workspace(
                        workspace_id=gen_id(),
                        workspace_name=f"Someone's own {gen_id(6)}",
                        is_system=False,
                    )
                    extra.append(other_workspace)
                    made["workspaces"].append(other_workspace.workspace_id)
                home = Dataset(
                    dataset_id=gen_id(),
                    workspace_id=other_workspace.workspace_id,
                    dataset_name=f"Other {gen_id(6)}",
                    dataset_type=dataset_type,
                    instrument=instrument,
                )
                extra.append(home)
            holder = SampleBatch(
                sample_batch_id=gen_id(),
                dataset_id=home.dataset_id,
                sample_batch_name=f"Other {gen_id(6)}",
                sample_batch_type=batch_type,
            )
            extra.append(holder)
        made_items = [
            SampleItem(
                sample_item_id=gen_id(),
                sample_batch_id=holder.sample_batch_id,
                sample_file_id=sample_file.sample_file_id,
                sample_item_name=f"Item {gen_id(6)}",
                sample_item_type=item_type,
                polarity=mode.ionization_mode_polarity,
                ionization_mode_id=mode.ionization_mode_id,
            )
            for _ in range(items)
        ]
        async with async_session_factory() as session:
            session.add_all([*extra, sample_file, *made_items])
            await session.commit()
        made["files"].append(sample_file.sample_file_id)
        made["items"].extend(item.sample_item_id for item in made_items)
        return sample_file

    yield {"instrument": instrument, "add_mode": add_mode, "add_file": add_file}

    async with async_session_factory() as session:
        await session.execute(
            delete(MethodBinding).where(MethodBinding.instrument == instrument)
        )
        await session.execute(
            delete(SampleItem).where(SampleItem.sample_item_id.in_(made["items"]))
        )
        await session.execute(
            delete(SampleFile).where(SampleFile.sample_file_id.in_(made["files"]))
        )
        await session.execute(
            delete(IonizationMode).where(
                IonizationMode.ionization_mode_id.in_(made["modes"])
            )
        )
        await session.execute(
            delete(Workspace).where(
                Workspace.workspace_id.in_(
                    [workspace.workspace_id, *made["workspaces"]]
                )
            )
        )
        await session.commit()


async def _rows(async_session_factory, instrument):
    async with async_session_factory() as session:
        return (
            (
                await session.execute(
                    select(MethodBinding).where(MethodBinding.instrument == instrument)
                )
            )
            .scalars()
            .all()
        )


@pytest.mark.asyncio
async def test_it_runs_at_all(async_session_factory, history):
    """The first query must be one Postgres accepts.

    `sample_file.range` and `ionization_mode.ionization_mechanism_ids` are
    both `json`, which has no equality operator, so selecting them under
    DISTINCT is refused and the script dies on its first statement.
    """
    mode = await history["add_mode"]()
    await history["add_file"](mode)

    await backfill_method_bindings()

    rows = await _rows(async_session_factory, history["instrument"])
    assert len(rows) == 1
    assert rows[0].method_key == TOF_METHOD
    assert rows[0].signature_class == "-"
    assert rows[0].source == "history"
    assert rows[0].n_streams == 1


@pytest.mark.asyncio
async def test_files_of_one_method_fold_into_one_key(async_session_factory, history):
    mode = await history["add_mode"]()
    for minutes in range(3):
        await history["add_file"](mode, minutes=minutes)

    await backfill_method_bindings()

    rows = await _rows(async_session_factory, history["instrument"])
    assert len(rows) == 1
    assert rows[0].n_streams == 3
    assert rows[0].state == "learned"


@pytest.mark.asyncio
async def test_two_chemistries_under_one_method_are_ambiguous(
    async_session_factory, history
):
    nitrate = await history["add_mode"]()
    bromide = await history["add_mode"](mechanisms=("mech-br", "mech-deprot"))
    await history["add_file"](nitrate, minutes=0)
    await history["add_file"](bromide, minutes=1)

    await backfill_method_bindings()

    rows = await _rows(async_session_factory, history["instrument"])
    assert len(rows) == 1
    assert rows[0].state == "ambiguous"
    assert len(rows[0].chemistry_keys) == 2
    assert rows[0].n_disagreements == 1
    # The chemistry seen first is the one the row points at.
    assert rows[0].ionization_mode_id == nitrate.ionization_mode_id


@pytest.mark.asyncio
async def test_history_folds_into_a_row_live_learning_already_made(
    async_session_factory, history
):
    """The case that matters: shadow learning is on before anyone runs this.

    The busiest methods have a row built from the last few files by the time
    the backfill runs, so skipping those would leave exactly the keys worth
    routing with no history - and a key whose past holds two chemistries
    would read `learned` on the handful seen live.
    """
    nitrate = await history["add_mode"]()
    bromide = await history["add_mode"](mechanisms=("mech-br", "mech-deprot"))
    await history["add_file"](bromide, minutes=0)
    live = await history["add_file"](nitrate, minutes=10)

    # What live learning would have made from the most recent file alone.
    digest = binding_digest(
        history["instrument"], method_key(TOF_METHOD), signature_class([], "-", "tof")
    )
    async with async_session_factory() as session:
        session.add(
            MethodBinding(
                method_binding_id=gen_id(),
                binding_key=digest,
                instrument=history["instrument"],
                method_key=TOF_METHOD,
                signature_class="-",
                ionization_mode_id=nitrate.ionization_mode_id,
                chemistry_keys=["mech-deprot,mech-no3"],
                state="learned",
                source="token",
                first_seen=live.datetime_utc,
                last_seen=live.datetime_utc,
                n_streams=1,
                n_disagreements=0,
            )
        )
        await session.commit()

    await backfill_method_bindings()

    rows = await _rows(async_session_factory, history["instrument"])
    assert len(rows) == 1
    row = rows[0]
    # The bromide file in its past is now known, so the key stops routing.
    assert row.state == "ambiguous"
    assert len(row.chemistry_keys) == 2
    # And it still points where live learning put it.
    assert row.ionization_mode_id == nitrate.ionization_mode_id


@pytest.mark.asyncio
async def test_running_it_again_is_safe(async_session_factory, history):
    mode = await history["add_mode"]()
    await history["add_file"](mode)

    await backfill_method_bindings()
    first = await _rows(async_session_factory, history["instrument"])
    await backfill_method_bindings()
    second = await _rows(async_session_factory, history["instrument"])

    assert len(second) == len(first) == 1
    # A second run adds the same history again, which is why it is worth
    # saying that n_streams counts observations rather than files.
    assert second[0].binding_key == first[0].binding_key
    assert second[0].state == first[0].state


@pytest.mark.asyncio
async def test_a_census_less_orbitrap_file_is_skipped(async_session_factory, history):
    """Converted before the census existed: its signature class is unknown."""
    mode = await history["add_mode"]()
    await history["add_file"](mode, instrument_type="orbi", method_file="nitrate.meth")

    await backfill_method_bindings()

    assert await _rows(async_session_factory, history["instrument"]) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    [
        {"batch_type": "ANALYSIS"},
        {"item_type": "ANALYSIS"},
        {"dataset_type": "ANALYSIS"},
        {"system_workspace": False},
    ],
)
async def test_only_the_pipeline_s_own_items_are_read(
    async_session_factory, history, kind
):
    """Live learning only ever sees the pipeline's bindings.

    A person can make an ACQUISITION-typed item in a batch of their own, and
    reading those would teach this table things ingest never would.
    """
    mode = await history["add_mode"]()
    await history["add_file"](mode, **kind)

    await backfill_method_bindings()

    assert await _rows(async_session_factory, history["instrument"]) == []


@pytest.mark.asyncio
async def test_a_file_with_several_items_under_one_mode_counts_once(
    async_session_factory, history
):
    """A windowed acquisition makes more than one item of the same binding."""
    mode = await history["add_mode"]()
    await history["add_file"](mode, items=3)

    await backfill_method_bindings()

    rows = await _rows(async_session_factory, history["instrument"])
    assert len(rows) == 1
    assert rows[0].n_streams == 1


@pytest.mark.asyncio
async def test_a_deployment_that_records_nothing_is_not_backfilled(
    async_session_factory, history, monkeypatch
):
    monkeypatch.setattr(
        "mascope_backend.db.scripts.backfill_method_bindings.method_binding_mode",
        lambda: "off",
    )
    mode = await history["add_mode"]()
    await history["add_file"](mode)

    await backfill_method_bindings()

    assert await _rows(async_session_factory, history["instrument"]) == []


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(async_session_factory, history, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "1")
    mode = await history["add_mode"]()
    await history["add_file"](mode)

    await backfill_method_bindings()

    assert await _rows(async_session_factory, history["instrument"]) == []
