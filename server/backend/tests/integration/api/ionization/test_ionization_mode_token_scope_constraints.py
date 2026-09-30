"""
Tests: what the database itself allows a scoped ionization mode's token to be.

The token used to be unique outright. Reuse across instruments is the point of
#1463, so what has to hold instead is that a token is unique among the modes
that could match the same file - and that is two partial indexes, not one
composite: Postgres counts NULLs as distinct, so a composite on
(token, instrument) would let two unscoped modes share a token, which is the
ambiguity the rule exists to prevent.

Only a database can show that. The application's own check
(``token_is_unique``) follows the same rule and is tested hermetically in
``tests/unit/api/ionization_modes/test_ionization_mode_instrument_scope.py``;
these are the guard behind it.
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from mascope_backend.db import (
    Dataset,
    IonizationMechanism,
    IonizationMode,
    SampleBatch,
    SampleFile,
    SampleItem,
    Workspace,
)
from mascope_backend.db.id import gen_id


@pytest_asyncio.fixture
async def mechanism(async_session_factory):
    """One negative mechanism for these modes to name.

    A real row, not a made-up id: the update route checks that a mode's
    mechanisms exist, so a placeholder turns a test about the scope into a 400
    about the mechanism.
    """
    mechanism_id = gen_id()
    async with async_session_factory() as session:
        session.add(
            IonizationMechanism(
                ionization_mechanism_id=mechanism_id,
                ionization_mechanism_polarity="-",
                ionization_mechanism=f"+Br- (scope-test {gen_id(6)})",
            )
        )
        await session.commit()

    yield mechanism_id

    async with async_session_factory() as session:
        await session.execute(
            delete(IonizationMechanism).where(
                IonizationMechanism.ionization_mechanism_id == mechanism_id
            )
        )
        await session.commit()


@pytest_asyncio.fixture
async def modes(async_session_factory, mechanism):
    """Adds ionization modes and takes them away again."""
    made: list[str] = []
    token = f"TOK{gen_id(6)}"

    async def add(instrument: str | None, *, with_token: str | None = ...) -> str:
        mode = IonizationMode(
            ionization_mode_id=gen_id(),
            ionization_mode_name=f"Mode {gen_id(8)}",
            ionization_mode_token=token if with_token is ... else with_token,
            ionization_mode_polarity="-",
            ionization_mechanism_ids=[mechanism],
            instrument=instrument,
        )
        async with async_session_factory() as session:
            session.add(mode)
            await session.commit()
        made.append(mode.ionization_mode_id)
        return mode.ionization_mode_id

    # For a mode created through the route: registered the moment it exists,
    # so a failing assertion below does not leave it behind while the
    # mechanism it names is deleted.
    yield add, token, made.append

    async with async_session_factory() as session:
        await session.execute(
            delete(IonizationMode).where(IonizationMode.ionization_mode_id.in_(made))
        )
        await session.commit()


@pytest_asyncio.fixture
async def in_use_mode(async_session_factory, mechanism):
    """A mode with an ACQUISITION batch, which the in-use guard protects."""
    made = {}
    token = f"USED{gen_id(6)}"
    name = f"In use {gen_id(8)}"
    mode = IonizationMode(
        ionization_mode_id=gen_id(),
        ionization_mode_name=name,
        ionization_mode_token=token,
        ionization_mode_polarity="-",
        ionization_mechanism_ids=[mechanism],
    )
    workspace = Workspace(
        workspace_id=gen_id(),
        workspace_name=f"Scope test {gen_id(6)}",
        is_system=True,
    )
    dataset = Dataset(
        dataset_id=gen_id(),
        workspace_id=workspace.workspace_id,
        dataset_name=f"Acquisitions {gen_id(6)}",
        dataset_type="ACQUISITION",
        instrument=f"instrument-{gen_id(8)}",
    )
    batch = SampleBatch(
        sample_batch_id=gen_id(),
        dataset_id=dataset.dataset_id,
        sample_batch_name=f"2026-01-01 {name} acquisition",
        sample_batch_type="ACQUISITION",
    )
    sample_file = SampleFile(
        sample_file_id=gen_id(),
        filename=f"{gen_id(10)}.raw",
        instrument="SCOPE-A",
        instrument_type="orbi",
        datetime=datetime(2026, 1, 1),
        datetime_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
        length=60.0,
        range=[40.0, 600.0],
        polarity="-",
    )
    item = SampleItem(
        sample_item_id=gen_id(),
        sample_batch_id=batch.sample_batch_id,
        sample_file_id=sample_file.sample_file_id,
        sample_item_name=f"Item {gen_id(6)}",
        sample_item_type="ACQUISITION",
        ionization_mode_id=mode.ionization_mode_id,
    )
    async with async_session_factory() as session:
        session.add_all([mode, workspace, dataset, batch, sample_file, item])
        await session.commit()
    made = {
        "mode_id": mode.ionization_mode_id,
        "token": token,
        "name": name,
        "item_id": item.sample_item_id,
        "file_id": sample_file.sample_file_id,
        "batch_id": batch.sample_batch_id,
        "dataset_id": dataset.dataset_id,
        "workspace_id": workspace.workspace_id,
    }

    yield made

    async with async_session_factory() as session:
        await session.execute(
            delete(SampleItem).where(SampleItem.sample_item_id == made["item_id"])
        )
        await session.execute(
            delete(SampleFile).where(SampleFile.sample_file_id == made["file_id"])
        )
        await session.execute(
            delete(SampleBatch).where(SampleBatch.sample_batch_id == made["batch_id"])
        )
        await session.execute(
            delete(Dataset).where(Dataset.dataset_id == made["dataset_id"])
        )
        await session.execute(
            delete(Workspace).where(Workspace.workspace_id == made["workspace_id"])
        )
        await session.execute(
            delete(IonizationMode).where(
                IonizationMode.ionization_mode_id == made["mode_id"]
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_two_instruments_may_share_a_token(modes):
    """The feature: one token meaning a different chemistry on each."""
    add, _token, _track = modes
    await add("SCOPE-A")
    await add("SCOPE-B")


@pytest.mark.asyncio
async def test_one_instrument_may_not_use_a_token_twice(modes):
    add, _token, _track = modes
    await add("SCOPE-A")
    with pytest.raises(IntegrityError):
        await add("SCOPE-A")


@pytest.mark.asyncio
async def test_two_unscoped_modes_may_not_share_a_token(modes):
    """What a plain composite index would have allowed.

    Both would be matched against every file name, so a name carrying the token
    would match two modes in one polarity and bind to neither.
    """
    add, _token, _track = modes
    await add(None)
    with pytest.raises(IntegrityError):
        await add(None)


@pytest.mark.asyncio
async def test_a_scoped_mode_may_share_a_token_with_an_unscoped_one(modes):
    """Deliberately allowed: this is how one instrument overrides the default.

    Both match that instrument's files, and the resolver prefers the scoped one
    rather than calling it ambiguous - which is what lets a site add one scoped
    mode without scoping every other mode at the same time.
    """
    add, _token, _track = modes
    await add(None)
    await add("SCOPE-A")


@pytest.mark.asyncio
async def test_a_token_is_still_optional_for_any_number_of_modes(modes):
    """Several modes with no token at all stay legal, scoped or not."""
    add, _token, _track = modes
    await add(None, with_token=None)
    await add(None, with_token=None)
    await add("SCOPE-A", with_token=None)


@pytest.mark.asyncio
async def test_widening_a_scope_rechecks_the_token(
    admin_client, async_session_factory, modes, mechanism
):
    """The token did not change, but what it is compared against did.

    A scoped mode's token is only matched against its own instrument's file
    names, so it may duplicate an unscoped mode's. Widening that mode to every
    instrument makes both match every file, and the update has to notice - a
    check that only ran when the token itself changed would let it through.
    """
    add, token, _track = modes
    await add(None)
    scoped_id = await add("SCOPE-A")

    resp = await admin_client.patch(
        f"/api/ionization/modes/{scoped_id}",
        json={
            "ionization_mode_name": f"Widened {gen_id(6)}",
            "ionization_mode_token": token,
            "ionization_mode_polarity": "-",
            "ionization_mechanism_ids": [mechanism],
            "instrument": None,
        },
    )

    assert resp.status_code >= 400, resp.text
    assert "similar token" in resp.text

    async with async_session_factory() as session:
        stored = (
            await session.execute(
                select(IonizationMode).where(
                    IonizationMode.ionization_mode_id == scoped_id
                )
            )
        ).scalar_one()
        assert stored.instrument == "SCOPE-A", "the refused change was not applied"


# --- through the API, not the ORM ------------------------------------------


@pytest.mark.asyncio
async def test_the_api_lets_a_scoped_mode_repeat_an_unscoped_token(
    admin_client, modes, async_session_factory, mechanism
):
    """The case the ORM tests above prove the database allows.

    Inserting through the ORM skips ``token_is_unique``, so the constraint tests
    passed while the application refused this outright - the one thing the
    CHANGELOG, the design note and ``_prefer_scoped`` are all built around.
    Driven through the route, which is how anyone would actually reach it.
    """
    add, token, track = modes
    await add(None)

    resp = await admin_client.post(
        "/api/ionization/modes",
        json={
            "ionization_mode_name": f"Scoped {gen_id(6)}",
            "ionization_mode_token": token,
            "ionization_mode_polarity": "-",
            "ionization_mechanism_ids": [mechanism],
            "instrument": "SCOPE-A",
        },
    )

    assert resp.status_code == 201, resp.text
    track(resp.json()["data"]["ionization_mode_id"])
    assert resp.json()["data"]["instrument"] == "SCOPE-A"

    async with async_session_factory() as session:
        stored = await session.get(
            IonizationMode, resp.json()["data"]["ionization_mode_id"]
        )
        assert stored.instrument == "SCOPE-A"


@pytest.mark.asyncio
async def test_a_mode_that_has_routed_files_can_still_be_scoped(
    admin_client, async_session_factory, in_use_mode, mechanism
):
    """Every mode the feature is for has acquisition batches.

    The in-use guard refuses changes to a mode that samples were bound under,
    and the scope fell through to its default case - so the one class of mode
    worth scoping, "a mode only ever run on one instrument", could not be. The
    scope renames nothing (a batch is named after the mode) and rebinds nothing
    (a sample item holds its own mode), so it joins the fields the guard lets
    through. Every earlier test used a mode with no batches and never reached
    this.
    """
    resp = await admin_client.patch(
        f"/api/ionization/modes/{in_use_mode['mode_id']}",
        json={
            "ionization_mode_name": in_use_mode["name"],
            "ionization_mode_token": in_use_mode["token"],
            "ionization_mode_polarity": "-",
            "ionization_mechanism_ids": [mechanism],
            "instrument": "SCOPE-A",
        },
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["instrument"] == "SCOPE-A"

    async with async_session_factory() as session:
        stored = await session.get(IonizationMode, in_use_mode["mode_id"])
        assert stored.instrument == "SCOPE-A"


# --- the scope is normalised ------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sent", "stored_as"),
    [("  SCOPE-A  ", "SCOPE-A"), ("", None), ("   ", None)],
)
async def test_the_scope_is_stripped_and_an_empty_one_means_every_instrument(
    admin_client, async_session_factory, mechanism, modes, sent, stored_as
):
    """A padded or empty name would scope a mode to an instrument no file has.

    The mode then quietly stops routing anything, while the pane goes on showing
    it as "Every instrument".
    """
    _add, _token, track = modes
    resp = await admin_client.post(
        "/api/ionization/modes",
        json={
            "ionization_mode_name": f"Normalised {gen_id(6)}",
            "ionization_mode_polarity": "-",
            "ionization_mechanism_ids": [mechanism],
            "instrument": sent,
        },
    )

    assert resp.status_code == 201, resp.text
    mode_id = resp.json()["data"]["ionization_mode_id"]
    track(mode_id)
    async with async_session_factory() as session:
        stored = await session.get(IonizationMode, mode_id)
        assert stored.instrument == stored_as
