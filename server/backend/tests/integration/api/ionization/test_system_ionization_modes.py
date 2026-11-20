"""
Tests: the ionization modes Mascope ships, over ``/api/ionization/modes``.

A seeded mode stands for one chemistry, the same ``system_key`` on every
server, so routing by acquisition method has something to route to. That only
holds if the row keeps saying which chemistry it is, so its name, token,
polarity and mechanisms are not the deployment's to change and it cannot be
deleted. Its calibration and diagnostic collections are the deployment's own
data, and are the part it may set - without them the mode calibrates and
matches nothing, so refusing those too would leave the row permanently unusable.

Every one of them is listed from the first start, as shipped: choosing one for
a file is how a deployment takes it up, so there is nothing to adopt before it
is offered (MAS-337). Each row says what it has routed - how many files the
ingest filed under it, and when the newest was acquired - which is how the
Chemistry settings tell a profile in use from one nobody has chosen. What a
deployment has put on a seeded mode - a collection of its own, a sample, an
edit (``holds_deployment_data``) - decides one thing only: whether the mode
may be released when a mechanism it holds is deleted.

Startup seeds the real catalogue; these tests mint their own rows so they do
not depend on which chemistries the catalogue happens to name, nor on which
mechanisms this database has.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

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


_SYSTEM_KEY = "test-chemistry"
_MECHANISMS = (
    "mascope_backend.api.controllers.ionization_mechanisms."
    "ionization_mechanisms_controller"
)


@pytest_asyncio.fixture(scope="session")
async def sys_mechanisms(async_session_factory):
    """Two negative-polarity mechanisms for the seeded mode to name."""
    ids = {"a": gen_id(), "b": gen_id()}
    async with async_session_factory() as session:
        for key, notation in (
            ("a", "+Br- (sys-mode-test)"),
            ("b", "-H+ (sys-mode-test)"),
        ):
            session.add(
                IonizationMechanism(
                    ionization_mechanism_id=ids[key],
                    ionization_mechanism_polarity="-",
                    ionization_mechanism=notation,
                )
            )
        await session.commit()
    return ids


@pytest_asyncio.fixture(scope="session")
async def sys_collection(async_session_factory):
    """A global calibrants collection: readable by any caller."""
    collection_id = gen_id()
    async with async_session_factory() as session:
        session.add(
            TargetCollection(
                target_collection_id=collection_id,
                target_collection_name="Calibrants (sys-mode-test)",
                target_collection_type="CALIBRANTS",
                workspace_id=None,
            )
        )
        await session.commit()
    return collection_id


@pytest_asyncio.fixture
async def system_mode(async_session_factory, sys_mechanisms):
    """A mode Mascope ships: a system key, no token, no collections.

    Removed directly at teardown. The API cannot delete a seeded mode, so a
    fixture that only inserted would pile rows up in the shared per-category
    database and leave later tests reading whatever earlier ones left.
    """
    mode_id = gen_id()
    async with async_session_factory() as session:
        session.add(
            IonizationMode(
                ionization_mode_id=mode_id,
                ionization_mode_name=f"Seeded Chemistry {mode_id}",
                ionization_mode_token=None,
                ionization_mode_polarity="-",
                ionization_mechanism_ids=[sys_mechanisms["a"], sys_mechanisms["b"]],
                system_key=f"{_SYSTEM_KEY}-{mode_id}",
            )
        )
        await session.commit()
    yield mode_id
    async with async_session_factory() as session:
        # Every version of it: an edit of its collections makes the next one,
        # which carries the key.
        await session.execute(
            delete(IonizationMode).where(
                (IonizationMode.ionization_mode_id == mode_id)
                | (IonizationMode.system_key == f"{_SYSTEM_KEY}-{mode_id}")
            )
        )
        await session.commit()


@pytest_asyncio.fixture
async def collected_mode(async_session_factory, sys_mechanisms, sys_collection):
    """A seeded mode the deployment has pointed at a collection of its own."""
    mode_id = gen_id()
    async with async_session_factory() as session:
        session.add(
            IonizationMode(
                ionization_mode_id=mode_id,
                ionization_mode_name=f"Adopted Chemistry {mode_id}",
                ionization_mode_token=None,
                ionization_mode_polarity="-",
                ionization_mechanism_ids=[sys_mechanisms["a"], sys_mechanisms["b"]],
                calibration_collection_id=sys_collection,
                system_key=f"{_SYSTEM_KEY}-collected-{mode_id}",
            )
        )
        await session.commit()
    yield mode_id
    async with async_session_factory() as session:
        await session.execute(
            delete(IonizationMode).where(IonizationMode.ionization_mode_id == mode_id)
        )
        await session.commit()


@pytest_asyncio.fixture
async def own_mode(async_session_factory, sys_mechanisms):
    """A mode the deployment made, for contrast."""
    mode_id = gen_id()
    async with async_session_factory() as session:
        session.add(
            IonizationMode(
                ionization_mode_id=mode_id,
                ionization_mode_name=f"Our Chemistry {mode_id}",
                ionization_mode_token=None,
                ionization_mode_polarity="-",
                ionization_mechanism_ids=[sys_mechanisms["a"]],
                system_key=None,
            )
        )
        await session.commit()
    yield mode_id
    async with async_session_factory() as session:
        await session.execute(
            delete(IonizationMode).where(IonizationMode.ionization_mode_id == mode_id)
        )
        await session.commit()


def _body(mechanisms, **overrides):
    """A full PATCH body echoing the seeded mode's own definition."""
    body = {
        "ionization_mode_name": None,  # filled by the caller
        "ionization_mode_polarity": "-",
        "ionization_mechanism_ids": [mechanisms["a"], mechanisms["b"]],
    }
    body.update(overrides)
    return {key: value for key, value in body.items() if value is not None}


async def _mode_name(async_session_factory, mode_id):
    async with async_session_factory() as session:
        mode = await session.get(IonizationMode, mode_id)
        return mode.ionization_mode_name


# ============= The definition is Mascope's =============


@pytest.mark.asyncio
async def test_a_seeded_mode_cannot_be_renamed(
    admin_client, async_session_factory, system_mode, sys_mechanisms
):
    before = await _mode_name(async_session_factory, system_mode)
    resp = await admin_client.patch(
        f"/api/ionization/modes/{system_mode}",
        json=_body(sys_mechanisms, ionization_mode_name="Something Else"),
    )
    assert resp.status_code == 400
    assert await _mode_name(async_session_factory, system_mode) == before


@pytest.mark.asyncio
async def test_a_seeded_modes_mechanisms_cannot_be_changed(
    admin_client, system_mode, sys_mechanisms
):
    """Its mechanisms are what make it the chemistry it claims to be."""
    resp = await admin_client.patch(
        f"/api/ionization/modes/{system_mode}",
        json=_body(
            sys_mechanisms,
            ionization_mode_name=f"Seeded Chemistry {system_mode}",
            ionization_mechanism_ids=[sys_mechanisms["a"]],
        ),
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_a_seeded_mode_cannot_be_given_a_token(
    admin_client, system_mode, sys_mechanisms
):
    """A token would make file names route to a mode with no collections."""
    resp = await admin_client.patch(
        f"/api/ionization/modes/{system_mode}",
        json=_body(
            sys_mechanisms,
            ionization_mode_name=f"Seeded Chemistry {system_mode}",
            ionization_mode_token="SYS",
        ),
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_a_seeded_mode_cannot_be_deleted(admin_client, system_mode):
    resp = await admin_client.delete(f"/api/ionization/modes/{system_mode}")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_a_seeded_mode_cannot_be_scoped_to_an_instrument(
    admin_client, system_mode, sys_mechanisms, async_session_factory
):
    """The scope filters token matching, and a seeded mode has no token.

    It ships without one and cannot be given one, so scoping it could not
    change how anything routes. Refused rather than accepted-and-inert, so the
    setting never reads as if it were doing something. It belongs among the
    deployment's own fields on the day a seeded mode can carry a token.
    """
    resp = await admin_client.patch(
        f"/api/ionization/modes/{system_mode}",
        json=_body(
            sys_mechanisms,
            ionization_mode_name=f"Seeded Chemistry {system_mode}",
            instrument="SCOPE-A",
        ),
    )

    assert resp.status_code == 400
    assert "instrument" in resp.text

    async with async_session_factory() as session:
        mode = await session.get(IonizationMode, system_mode)
        assert mode.instrument is None


# ============= The collections are the deployment's =============


@pytest.mark.asyncio
async def test_a_seeded_mode_takes_the_deployments_collection(
    admin_client, system_mode, sys_mechanisms, sys_collection
):
    """The one edit that must work: without it the mode stays unusable."""
    resp = await admin_client.patch(
        f"/api/ionization/modes/{system_mode}",
        json=_body(
            sys_mechanisms,
            ionization_mode_name=f"Seeded Chemistry {system_mode}",
            calibration_collection_id=sys_collection,
        ),
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["calibration_collection_id"] == sys_collection


@pytest.mark.asyncio
async def test_a_seeded_mode_takes_the_deployments_reagent_purity(
    admin_client, system_mode, sys_mechanisms
):
    """A labelled reagent's purity is the site's bottle, not the chemistry."""
    resp = await admin_client.patch(
        f"/api/ionization/modes/{system_mode}",
        json=_body(
            sys_mechanisms,
            ionization_mode_name=f"Seeded Chemistry {system_mode}",
            label_purity=0.99,
        ),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["label_purity"] == 0.99
    assert resp.json()["data"]["version"] == 2


@pytest.mark.asyncio
async def test_echoing_the_definition_back_is_not_a_change(
    admin_client, system_mode, sys_mechanisms, sys_collection
):
    """The client sends the whole mode on every save, nulls included.

    The seeded mode's token is NULL, so a client echoing it sends
    ``ionization_mode_token: null``. Refusing on the presence of a protected
    field, or comparing a sent null against anything but the stored null,
    would make the one permitted edit impossible to submit.
    """
    body = _body(
        sys_mechanisms,
        ionization_mode_name=f"Seeded Chemistry {system_mode}",
        calibration_collection_id=sys_collection,
    )
    body["ionization_mode_token"] = None
    resp = await admin_client.patch(f"/api/ionization/modes/{system_mode}", json=body)
    assert resp.status_code == 200
    assert resp.json()["data"]["ionization_mode_token"] is None


@pytest.mark.asyncio
async def test_mechanisms_echoed_in_another_order_are_not_a_change(
    admin_client, async_session_factory, system_mode, sys_mechanisms, sys_collection
):
    """Mechanism ids are a set; the order they arrive in means nothing.

    Accepted, but not written: the stored definition has to keep reading the
    same on every server, so a reordered or repeated echo leaves it alone.
    """
    resp = await admin_client.patch(
        f"/api/ionization/modes/{system_mode}",
        json=_body(
            sys_mechanisms,
            ionization_mode_name=f"Seeded Chemistry {system_mode}",
            ionization_mechanism_ids=[
                sys_mechanisms["b"],
                sys_mechanisms["a"],
                sys_mechanisms["a"],
            ],
            calibration_collection_id=sys_collection,
        ),
    )
    assert resp.status_code == 200
    async with async_session_factory() as session:
        stored = (
            await session.get(IonizationMode, system_mode)
        ).ionization_mechanism_ids
    assert stored == [sys_mechanisms["a"], sys_mechanisms["b"]]


# ============= A deployment's own modes are unaffected =============


@pytest.mark.asyncio
async def test_a_deployments_own_mode_can_still_be_renamed(
    admin_client, async_session_factory, own_mode, sys_mechanisms
):
    resp = await admin_client.patch(
        f"/api/ionization/modes/{own_mode}",
        json=_body(
            sys_mechanisms,
            ionization_mode_name="Renamed By Us",
            ionization_mechanism_ids=[sys_mechanisms["a"]],
        ),
    )
    assert resp.status_code == 200
    assert await _mode_name(async_session_factory, own_mode) == "Renamed By Us"


# ============= The listing =============


@pytest.mark.asyncio
async def test_the_listing_carries_a_seeded_mode_from_the_start(
    admin_client, system_mode, own_mode
):
    """As seeded - no collection, no sample, no edit - and listed all the
    same: choosing it for a file is how it is taken up (MAS-337)."""
    resp = await admin_client.get("/api/ionization/modes")
    assert resp.status_code == 200
    listed = {mode["ionization_mode_id"]: mode for mode in resp.json()["data"]}
    assert own_mode in listed
    assert system_mode in listed
    assert listed[system_mode]["system_key"] == f"{_SYSTEM_KEY}-{system_mode}"
    assert listed[own_mode]["system_key"] is None


@pytest.mark.asyncio
async def test_the_listing_carries_a_seeded_mode_given_a_collection(
    admin_client, collected_mode
):
    """A sample bound to one carries an id the browser must resolve."""
    resp = await admin_client.get("/api/ionization/modes")
    assert resp.status_code == 200
    listed = {mode["ionization_mode_id"] for mode in resp.json()["data"]}
    assert collected_mode in listed


@pytest.mark.asyncio
async def test_the_old_switch_changes_nothing(admin_client, system_mode, own_mode):
    """``include_system`` is gone. A client that still sends it - an SDK
    released before the change - gets the same listing, not a 422."""
    plain = await admin_client.get("/api/ionization/modes")
    asked = await admin_client.get("/api/ionization/modes?include_system=true")
    assert plain.status_code == asked.status_code == 200
    ids = {mode["ionization_mode_id"] for mode in plain.json()["data"]}
    assert ids == {mode["ionization_mode_id"] for mode in asked.json()["data"]}
    assert {system_mode, own_mode} <= ids


# ============= What a mode has routed =============


_ROUTED_AT = datetime(2026, 3, 4, 5, 6, 7, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def routed_world(async_session_factory):
    """A workspace and a dataset, with a way to file samples into them.

    ``file(mode_id, batch, at)`` registers a raw file acquired at *at* and
    files one sample of it under *mode_id* in *batch*, an ``ACQUISITION``
    batch unless ``kind`` says otherwise. Everything is removed at teardown,
    the files' samples and batches through the cascade.
    """
    workspace_id, dataset_id = gen_id(), gen_id()
    file_ids: list[str] = []
    batch_ids: dict[str, str] = {}
    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=workspace_id,
                workspace_name=f"Routed {workspace_id}",
                workspace_status="active",
                workspace_utc_created=_ROUTED_AT,
                workspace_utc_modified=_ROUTED_AT,
            )
        )
        session.add(
            Dataset(
                dataset_id=dataset_id,
                workspace_id=workspace_id,
                dataset_name="Routed",
                dataset_type="ACQUISITION",
                dataset_utc_created=_ROUTED_AT,
            )
        )
        await session.commit()

    async def file(mode_id, batch, at, kind="ACQUISITION", sample_file_id=None):
        async with async_session_factory() as session:
            if batch not in batch_ids:
                batch_ids[batch] = gen_id()
                session.add(
                    SampleBatch(
                        sample_batch_id=batch_ids[batch],
                        dataset_id=dataset_id,
                        sample_batch_name=f"{batch} {batch_ids[batch]}",
                        sample_batch_type=kind,
                        sample_batch_utc_created=_ROUTED_AT,
                    )
                )
            if sample_file_id is None:
                sample_file_id = gen_id()
                file_ids.append(sample_file_id)
                session.add(
                    SampleFile(
                        sample_file_id=sample_file_id,
                        filename=f"routed-test_{sample_file_id}.raw",
                        instrument="routed-test",
                        instrument_type="orbi",
                        datetime=at.replace(tzinfo=None),
                        datetime_utc=at,
                        length=60.0,
                        range={"min": 0, "max": 500},
                        polarity="-",
                    )
                )
            session.add(
                SampleItem(
                    sample_item_id=gen_id(),
                    sample_batch_id=batch_ids[batch],
                    sample_file_id=sample_file_id,
                    sample_item_name=at.strftime("%Y-%m-%d %H:%M:%S"),
                    sample_item_type=kind,
                    sample_item_attributes={},
                    polarity="-",
                    tic=1000.0,
                    t0=0.0,
                    t1=60.0,
                    ionization_mode_id=mode_id,
                    sample_item_utc_created=_ROUTED_AT,
                )
            )
            await session.commit()
        return sample_file_id

    yield file
    async with async_session_factory() as session:
        await session.execute(
            delete(SampleFile).where(SampleFile.sample_file_id.in_(file_ids))
        )
        await session.execute(
            delete(Workspace).where(Workspace.workspace_id == workspace_id)
        )
        await session.commit()


async def _listed(client, mode_id):
    resp = await client.get("/api/ionization/modes")
    assert resp.status_code == 200
    (row,) = [m for m in resp.json()["data"] if m["ionization_mode_id"] == mode_id]
    return row


@pytest.mark.asyncio
async def test_a_mode_nothing_was_routed_to_says_so(admin_client, system_mode):
    row = await _listed(admin_client, system_mode)
    assert row["routed_files"] == 0
    assert row["last_file_utc"] is None


@pytest.mark.asyncio
async def test_a_row_says_how_many_files_it_routed_and_when_the_last_was_acquired(
    admin_client, system_mode, routed_world
):
    """Counted off the daily acquisition batches, one sample a file."""
    earlier = _ROUTED_AT.replace(day=1)
    await routed_world(system_mode, "day one", earlier)
    await routed_world(system_mode, "day four", _ROUTED_AT)

    row = await _listed(admin_client, system_mode)

    assert row["routed_files"] == 2
    assert datetime.fromisoformat(row["last_file_utc"]) == _ROUTED_AT
    # And the single record reads the same, as the socket's does.
    single = await admin_client.get(f"/api/ionization/modes/{system_mode}")
    assert single.json()["data"]["routed_files"] == 2


@pytest.mark.asyncio
async def test_a_copy_in_a_batch_of_ones_own_is_not_a_second_file(
    admin_client, system_mode, routed_world
):
    """A user's batch holds a copy of the acquisition sample: the same file."""
    routed = await routed_world(system_mode, "acquired", _ROUTED_AT.replace(day=1))
    await routed_world(
        system_mode,
        "campaign",
        _ROUTED_AT,
        kind="ANALYSIS",
        sample_file_id=routed,
    )
    await routed_world(system_mode, "processed by hand", _ROUTED_AT, kind="ANALYSIS")

    row = await _listed(admin_client, system_mode)

    assert row["routed_files"] == 1
    assert datetime.fromisoformat(row["last_file_utc"]) == _ROUTED_AT.replace(day=1)


@pytest.mark.asyncio
async def test_what_an_earlier_version_routed_counts_for_the_mode(
    admin_client, system_mode, sys_mechanisms, sys_collection, routed_world
):
    """A file processed before an edit was routed to the same setup."""
    await routed_world(system_mode, "before the edit", _ROUTED_AT)
    resp = await admin_client.patch(
        f"/api/ionization/modes/{system_mode}",
        json=_body(
            sys_mechanisms,
            ionization_mode_name=f"Seeded Chemistry {system_mode}",
            calibration_collection_id=sys_collection,
        ),
    )
    assert resp.status_code == 200
    successor = resp.json()["data"]["ionization_mode_id"]
    assert successor != system_mode
    await routed_world(successor, "after the edit", _ROUTED_AT.replace(day=5))

    row = await _listed(admin_client, successor)

    assert row["routed_files"] == 2
    assert datetime.fromisoformat(row["last_file_utc"]) == _ROUTED_AT.replace(day=5)


# ============= A seeded mode never pins a mechanism =============


@pytest.mark.asyncio
async def test_deleting_a_mechanism_releases_a_seeded_mode_as_seeded(
    admin_client, async_session_factory
):
    """A seeded mode cannot be deleted through its own route.

    So one built on a mechanism the deployment is retiring would pin that
    mechanism for good. As seeded, the mode holds nothing of the
    deployment's - no collections, no samples - and goes with it. It was
    listed, as every mode is, so the browsers are told it is gone.
    """
    mechanism_id = gen_id(11)
    mode_id = gen_id()
    async with async_session_factory() as session:
        session.add(
            IonizationMechanism(
                ionization_mechanism_id=mechanism_id,
                ionization_mechanism_polarity="-",
                ionization_mechanism="+Cl- (sys-mode-test)",
            )
        )
        session.add(
            IonizationMode(
                ionization_mode_id=mode_id,
                ionization_mode_name=f"Retired Chemistry {mode_id}",
                ionization_mode_token=None,
                ionization_mode_polarity="-",
                ionization_mechanism_ids=[mechanism_id],
                system_key=f"{_SYSTEM_KEY}-retired-{mode_id}",
            )
        )
        await session.commit()

    with patch(f"{_MECHANISMS}.emit_record_deleted", new_callable=AsyncMock) as emit:
        resp = await admin_client.delete(f"/api/ionization_mechanisms/{mechanism_id}")

    assert resp.status_code == 200
    async with async_session_factory() as session:
        assert await session.get(IonizationMode, mode_id) is None
    assert [call.kwargs for call in emit.await_args_list] == [
        {"record_type": "ionization_mode", "record_id": mode_id},
        {"record_type": "ionization_mechanism", "record_id": mechanism_id},
    ]


@pytest.mark.asyncio
async def test_deleting_a_mechanism_a_seeded_mode_given_a_collection_uses_is_refused(
    admin_client, async_session_factory, sys_collection
):
    """The mode carries the deployment's own collection, so it stays."""
    mechanism_id = gen_id(11)
    mode_id = gen_id()
    async with async_session_factory() as session:
        session.add(
            IonizationMechanism(
                ionization_mechanism_id=mechanism_id,
                ionization_mechanism_polarity="-",
                ionization_mechanism="+F- (sys-mode-test)",
            )
        )
        session.add(
            IonizationMode(
                ionization_mode_id=mode_id,
                ionization_mode_name=f"Kept Chemistry {mode_id}",
                ionization_mode_token=None,
                ionization_mode_polarity="-",
                ionization_mechanism_ids=[mechanism_id],
                calibration_collection_id=sys_collection,
                system_key=f"{_SYSTEM_KEY}-kept-{mode_id}",
            )
        )
        await session.commit()

    resp = await admin_client.delete(f"/api/ionization_mechanisms/{mechanism_id}")

    assert resp.status_code == 400
    async with async_session_factory() as session:
        assert await session.get(IonizationMode, mode_id) is not None
        await session.execute(
            delete(IonizationMode).where(IonizationMode.ionization_mode_id == mode_id)
        )
        await session.execute(
            delete(IonizationMechanism).where(
                IonizationMechanism.ionization_mechanism_id == mechanism_id
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_the_seeded_modes_are_still_in_the_database(
    async_session_factory, system_mode
):
    """Written to the database, not only listed: the rungs bind to them."""
    async with async_session_factory() as session:
        found = (
            await session.execute(
                select(IonizationMode.ionization_mode_id).where(
                    IonizationMode.ionization_mode_id == system_mode
                )
            )
        ).scalar_one_or_none()
    assert found == system_mode


@pytest.mark.asyncio
async def test_a_diagnostic_only_seeded_mode_holds_the_deployments_data(
    admin_client, async_session_factory, sys_mechanisms, sys_collection
):
    """Either collection of the deployment's keeps a seeded mode from being
    released, and the mode its refusal names is one the listing shows.
    """
    mechanism_id = gen_id(11)
    mode_id = gen_id()
    async with async_session_factory() as session:
        session.add(
            IonizationMechanism(
                ionization_mechanism_id=mechanism_id,
                ionization_mechanism_polarity="-",
                ionization_mechanism="+Br3- (sys-mode-test)",
            )
        )
        session.add(
            IonizationMode(
                ionization_mode_id=mode_id,
                ionization_mode_name=f"Diagnostic Only {mode_id}",
                ionization_mode_token=None,
                ionization_mode_polarity="-",
                ionization_mechanism_ids=[mechanism_id],
                diagnostic_collection_id=sys_collection,
                system_key=f"{_SYSTEM_KEY}-diag-{mode_id}",
            )
        )
        await session.commit()

    listing = await admin_client.get("/api/ionization/modes")
    delete_resp = await admin_client.delete(
        f"/api/ionization_mechanisms/{mechanism_id}"
    )

    listed = {mode["ionization_mode_id"] for mode in listing.json()["data"]}
    assert mode_id in listed, "every mode is listed"
    assert delete_resp.status_code == 400, "the deployment's, so not released"

    async with async_session_factory() as session:
        await session.execute(
            delete(IonizationMode).where(IonizationMode.ionization_mode_id == mode_id)
        )
        await session.execute(
            delete(IonizationMechanism).where(
                IonizationMechanism.ionization_mechanism_id == mechanism_id
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_a_superseded_seeded_version_is_not_released_with_a_mechanism(
    admin_client, async_session_factory, sys_collection
):
    """An edit of a seeded mode's collections leaves its first version, as
    seeded, behind its edited successor. It is that mode's history, not a
    mode nobody took up, so it is not released. No sample
    holds it, so it does not block either: the mechanism both versions name
    is shown, and its delete refused, for the current one alone."""
    mechanism_id = gen_id(11)
    first, second = gen_id(), gen_id()
    key = f"{_SYSTEM_KEY}-versions-{first}"
    async with async_session_factory() as session:
        session.add(
            IonizationMechanism(
                ionization_mechanism_id=mechanism_id,
                ionization_mechanism_polarity="-",
                ionization_mechanism="+Cl- (sys-mode-versions)",
            )
        )
        session.add(
            IonizationMode(
                ionization_mode_id=first,
                ionization_mode_name=f"Versioned Chemistry {first}",
                ionization_mode_polarity="-",
                ionization_mechanism_ids=[mechanism_id],
                system_key=key,
                is_active=False,
            )
        )
        session.add(
            IonizationMode(
                ionization_mode_id=second,
                ionization_mode_name=f"Versioned Chemistry {first}",
                ionization_mode_polarity="-",
                ionization_mechanism_ids=[mechanism_id],
                calibration_collection_id=sys_collection,
                system_key=key,
                version=2,
                supersedes_id=first,
            )
        )
        await session.commit()

    try:
        listed = await admin_client.get(f"/api/ionization_mechanisms/{mechanism_id}")
        resp = await admin_client.delete(f"/api/ionization_mechanisms/{mechanism_id}")
        async with async_session_factory() as session:
            kept = await session.get(IonizationMode, first)
    finally:
        async with async_session_factory() as session:
            await session.execute(
                delete(IonizationMode).where(
                    IonizationMode.ionization_mode_id.in_([second, first])
                )
            )
            await session.execute(
                delete(IonizationMechanism).where(
                    IonizationMechanism.ionization_mechanism_id == mechanism_id
                )
            )
            await session.commit()

    assert resp.status_code == 400
    blocking = resp.json()["detail"]["ionization_modes"]
    assert {(m["ion_mode_id"], m["version"], m["is_active"]) for m in blocking} == {
        (second, 2, True),
    }
    assert kept is not None
    assert listed.status_code == 200, listed.text
    assert {
        (m["ion_mode_id"], m["version"], m["is_active"])
        for m in listed.json()["data"]["ionization_modes"]
    } == {(second, 2, True)}
