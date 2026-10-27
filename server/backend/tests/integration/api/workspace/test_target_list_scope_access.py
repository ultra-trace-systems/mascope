"""
Tests: the target ion, compound and isotope lists check the batch or
collection they filter by.

``GET /api/target/ions``, ``GET /api/target/compounds`` and
``GET /api/target/isotopes`` are open to every signed-in user: unfiltered,
they list the shared compound, ion and isotope library.
Their ``target_collection_id`` filter returns the contents of one collection,
and their ``sample_batch_id`` filter those of a batch's collections, so a
guest could read another workspace's collection through them. Now a named
collection needs the guest role on its workspace, as
``GET /api/target/collections/{id}`` does, and a named batch the guest role on
its workspace, as the association routes do. ``show_target_collection`` tags
each row with its collections, so it lists only the collections the caller
can read: the global ones and those of their workspaces.

Fixtures used:
- ``alpha_batch`` in ``ws_alpha`` (every test user is a member)
- ``beta_batch`` in ``ws_beta``, where only the owner (a superuser) is a member
"""

from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import delete

from mascope_backend.db import (
    IonizationMechanism,
    TargetCollection,
    TargetCompound,
    TargetCompoundInTargetCollection,
    TargetIon,
    TargetIsotope,
)
from mascope_backend.db.id import gen_id


IONS = "/api/target/ions"
COMPOUNDS = "/api/target/compounds"
ISOTOPES = "/api/target/isotopes"

EVERY_LIST = pytest.mark.parametrize("url", [IONS, COMPOUNDS, ISOTOPES])


@pytest_asyncio.fixture
async def scope(async_session_factory, ws_alpha, ws_beta):
    """A collection in ws_alpha, ws_beta and none, one compound, ion and isotope each."""
    ids = SimpleNamespace(
        mechanism=gen_id(),
        collection={"alpha": gen_id(), "beta": gen_id(), "global": gen_id()},
    )
    ids.compound = {name: gen_id() for name in ids.collection}
    ids.ion = {name: gen_id() for name in ids.collection}
    ids.isotope = {name: gen_id() for name in ids.collection}
    workspace_of = {
        "alpha": ws_alpha["workspace_id"],
        "beta": ws_beta["workspace_id"],
        "global": None,
    }
    async with async_session_factory() as session:
        session.add(
            IonizationMechanism(
                ionization_mechanism_id=ids.mechanism,
                ionization_mechanism_polarity="+",
                ionization_mechanism=f"[M+H]+ {ids.mechanism}",
            )
        )
        for name, compound_id in ids.compound.items():
            session.add(
                TargetCompound(
                    target_compound_id=compound_id,
                    target_compound_name=f"List Scope Compound {name} {compound_id}",
                    target_compound_formula="C6H12O6",
                )
            )
            session.add(
                TargetCollection(
                    target_collection_id=ids.collection[name],
                    target_collection_name=f"List Scope Collection {name}",
                    workspace_id=workspace_of[name],
                )
            )
        await session.flush()
        for name, compound_id in ids.compound.items():
            session.add(
                TargetCompoundInTargetCollection(
                    target_compound_id=compound_id,
                    target_collection_id=ids.collection[name],
                )
            )
            session.add(
                TargetIon(
                    target_ion_id=ids.ion[name],
                    target_compound_id=compound_id,
                    ionization_mechanism_id=ids.mechanism,
                    target_ion_formula="C6H13O6+",
                )
            )
        await session.flush()
        for name, ion_id in ids.ion.items():
            session.add(
                TargetIsotope(
                    target_isotope_id=ids.isotope[name],
                    target_ion_id=ion_id,
                    target_isotope_formula="C6H13O6+",
                    mz=181.0707,
                    relative_abundance=1.0,
                    resolution="HIGH",
                )
            )
        await session.commit()
    yield ids
    async with async_session_factory() as session:
        await session.execute(
            delete(TargetCompound).where(
                TargetCompound.target_compound_id.in_(ids.compound.values())
            )
        )
        await session.execute(
            delete(TargetCollection).where(
                TargetCollection.target_collection_id.in_(ids.collection.values())
            )
        )
        await session.execute(
            delete(IonizationMechanism).where(
                IonizationMechanism.ionization_mechanism_id == ids.mechanism
            )
        )
        await session.commit()


_ROW_KEY = {
    IONS: ("target_ion_id", "ion"),
    COMPOUNDS: ("target_compound_id", "compound"),
    ISOTOPES: ("target_isotope_id", "isotope"),
}


def _ids(url: str, resp) -> list[str]:
    key = _ROW_KEY[url][0]
    return [row[key] for row in resp.json()["data"]]


def _expected(url: str, scope, name: str) -> list[str]:
    return [getattr(scope, _ROW_KEY[url][1])[name]]


# --- A named target collection ----------------------------------------------


@pytest.mark.asyncio
@EVERY_LIST
async def test_a_guest_naming_another_workspaces_collection_is_refused(
    guest_client, scope, url
):
    resp = await guest_client.get(
        url, params={"target_collection_id": scope.collection["beta"]}
    )

    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
@EVERY_LIST
@pytest.mark.parametrize("collection", ["alpha", "global"])
async def test_a_guest_reads_a_collection_of_their_workspace_or_a_global_one(
    guest_client, scope, url, collection
):
    resp = await guest_client.get(
        url, params={"target_collection_id": scope.collection[collection]}
    )

    assert resp.status_code == 200, resp.text
    assert _ids(url, resp) == _expected(url, scope, collection)


@pytest.mark.asyncio
@EVERY_LIST
async def test_a_collection_that_does_not_exist_is_refused(guest_client, url):
    resp = await guest_client.get(url, params={"target_collection_id": gen_id()})

    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
@EVERY_LIST
async def test_a_superuser_reads_a_collection_of_any_workspace(
    owner_client, scope, url
):
    resp = await owner_client.get(
        url, params={"target_collection_id": scope.collection["beta"]}
    )

    assert resp.status_code == 200, resp.text
    assert _ids(url, resp) == _expected(url, scope, "beta")


# --- A named sample batch ---------------------------------------------------


@pytest.mark.asyncio
@EVERY_LIST
async def test_a_guest_naming_another_workspaces_batch_is_refused(
    guest_client, beta_batch, url
):
    resp = await guest_client.get(url, params={"sample_batch_id": beta_batch})

    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
@EVERY_LIST
async def test_a_guest_reads_by_a_batch_of_their_workspace(
    guest_client, alpha_batch, url
):
    resp = await guest_client.get(url, params={"sample_batch_id": alpha_batch})

    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
@EVERY_LIST
async def test_a_batch_that_does_not_exist_is_refused(guest_client, url):
    resp = await guest_client.get(url, params={"sample_batch_id": gen_id()})

    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
@EVERY_LIST
async def test_a_readable_batch_does_not_open_another_workspaces_collection(
    guest_client, scope, alpha_batch, url
):
    """Both filters named: each is checked on its own."""
    resp = await guest_client.get(
        url,
        params={
            "sample_batch_id": alpha_batch,
            "target_collection_id": scope.collection["beta"],
        },
    )

    assert resp.status_code == 403, resp.text


# --- No scope filter --------------------------------------------------------


@pytest.mark.asyncio
@EVERY_LIST
async def test_the_unfiltered_library_stays_open_to_a_guest(guest_client, scope, url):
    """The shared library is a separate question; this change leaves it open."""
    resp = await guest_client.get(url)

    assert resp.status_code == 200, resp.text
    assert set(_ids(url, resp)) >= {
        *_expected(url, scope, "alpha"),
        *_expected(url, scope, "beta"),
    }


# --- show_target_collection -------------------------------------------------


def _collections(url: str, scope, resp) -> set[str]:
    """The fixture's collections the rows are tagged with."""
    ours = set(scope.collection.values())
    return {
        row["target_collection_id"]
        for row in resp.json()["data"]
        if row.get("target_collection_id") in ours
    }


@pytest.mark.asyncio
@EVERY_LIST
async def test_show_target_collection_hides_another_workspaces_collection(
    guest_client, scope, url
):
    """Grouping the rows by collection must not give what the filter refuses."""
    resp = await guest_client.get(url, params={"show_target_collection": "true"})

    assert resp.status_code == 200, resp.text
    assert _collections(url, scope, resp) == {
        scope.collection["alpha"],
        scope.collection["global"],
    }
    assert not set(_ids(url, resp)) & set(_expected(url, scope, "beta"))


@pytest.mark.asyncio
@EVERY_LIST
async def test_show_target_collection_lists_every_collection_to_a_superuser(
    owner_client, scope, url
):
    resp = await owner_client.get(url, params={"show_target_collection": "true"})

    assert resp.status_code == 200, resp.text
    assert _collections(url, scope, resp) == set(scope.collection.values())
