"""
Tests: the ionization modes Mascope ships, over ``/api/ionization/modes``.

A seeded mode stands for one chemistry, the same ``system_key`` on every
server, so routing by acquisition method has something to route to. That only
holds if the row keeps saying which chemistry it is, so its name, token,
polarity and mechanisms are not the deployment's to change and it cannot be
deleted. Its calibration and diagnostic collections are the deployment's own
data, and are the part it may set - without them the mode calibrates and
matches nothing, so refusing those too would leave the row permanently unusable.

A deployment that has not adopted one should not be offered it either, so the
listing leaves those out - but only those. Adoption is anything of the
deployment's on the mode: either target collection, or a sample bound to it
(``system_mode_adopted``). An adopted mode has to be listed like any other,
because a sample bound to one carries an id the browser must resolve, and it
is the same predicate that decides whether the mode may be released when a
mechanism it holds is deleted.

Startup seeds the real catalogue; these tests mint their own rows so they do
not depend on which chemistries the catalogue happens to name, nor on which
mechanisms this database has.
"""

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from mascope_backend.db import IonizationMechanism, IonizationMode, TargetCollection
from mascope_backend.db.id import gen_id


_SYSTEM_KEY = "test-chemistry"


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
        await session.execute(
            delete(IonizationMode).where(IonizationMode.ionization_mode_id == mode_id)
        )
        await session.commit()


@pytest_asyncio.fixture
async def adopted_mode(async_session_factory, sys_mechanisms, sys_collection):
    """A seeded mode the deployment has adopted, by giving it a collection."""
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
                system_key=f"{_SYSTEM_KEY}-adopted-{mode_id}",
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
async def test_the_listing_leaves_an_unadopted_seeded_mode_out(
    admin_client, system_mode, own_mode
):
    """Without collections it calibrates and matches nothing."""
    resp = await admin_client.get("/api/ionization/modes")
    assert resp.status_code == 200
    listed = {mode["ionization_mode_id"] for mode in resp.json()["data"]}
    assert own_mode in listed
    assert system_mode not in listed


@pytest.mark.asyncio
async def test_the_listing_carries_an_adopted_seeded_mode(admin_client, adopted_mode):
    """Adoption is the collection. Hidden after it, a sample bound to the mode
    would carry an id the browser cannot resolve."""
    resp = await admin_client.get("/api/ionization/modes")
    assert resp.status_code == 200
    listed = {mode["ionization_mode_id"] for mode in resp.json()["data"]}
    assert adopted_mode in listed


@pytest.mark.asyncio
async def test_the_listing_can_be_asked_for_the_unadopted_ones(
    admin_client, system_mode
):
    """Hidden by default, but reachable - otherwise nothing could adopt one."""
    resp = await admin_client.get("/api/ionization/modes?include_system=true")
    assert resp.status_code == 200
    listed = {mode["ionization_mode_id"] for mode in resp.json()["data"]}
    assert system_mode in listed


# ============= A seeded mode never pins a mechanism =============


@pytest.mark.asyncio
async def test_deleting_a_mechanism_releases_an_unadopted_seeded_mode(
    admin_client, async_session_factory
):
    """A seeded mode cannot be deleted through its own route.

    So one built on a mechanism the deployment is retiring would pin that
    mechanism for good. Unadopted, the mode holds nothing of the
    deployment's - no collections, no samples - and goes with it.
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

    resp = await admin_client.delete(f"/api/ionization_mechanisms/{mechanism_id}")

    assert resp.status_code == 200
    async with async_session_factory() as session:
        assert await session.get(IonizationMode, mode_id) is None


@pytest.mark.asyncio
async def test_deleting_a_mechanism_an_adopted_mode_uses_is_still_refused(
    admin_client, async_session_factory, sys_collection
):
    """Adopted, the mode carries the deployment's own collection."""
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
    """Left out of the listing, not absent: later phases bind to them."""
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
async def test_a_diagnostic_only_seeded_mode_counts_as_adopted(
    admin_client, async_session_factory, sys_mechanisms, sys_collection
):
    """One definition of adopted, read the same by the listing and the release.

    Read differently, a mode with only a diagnostic collection was hidden from
    the listing and still pinned its mechanisms behind a refusal naming it.
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
    assert mode_id in listed, "adopted, so it must be offered"
    assert delete_resp.status_code == 400, "adopted, so it must not be released"

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
