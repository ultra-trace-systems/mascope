"""
Tests: the match ion and isotope records routes check the ion scope, not only
the sample scope.

``POST /api/match/records/ion``, ``POST /api/match/records/ion/series`` and
``GET /api/match/records/isotope`` answer one record per target ion, carrying
the ion's compound name and formulas. A guest of the batch could name a target
collection, or target ions, of a workspace they are no member of and read its
contents through them. Now a named collection needs the guest role on its
workspace, as ``GET /api/target/collections/{id}`` does, and every named ion
must be in a collection the caller can read: global, or of a workspace they are
a member of.

The record services are stubbed for the ACL tests (``services``): the
sample-level queries read a database view this suite does not create, and a
stub also shows a refused request never reaches the query. One test runs the
real series query.

Fixtures used:
- ``alpha_batch`` and ``alpha_item`` in ``ws_alpha`` (every test user is a member)
- ``ws_beta``, where only the owner (a superuser) is a member
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import delete

from mascope_backend.api.new.match.records import routes
from mascope_backend.db import (
    IonizationMechanism,
    TargetCollection,
    TargetCompound,
    TargetCompoundInTargetCollection,
    TargetIon,
    Workspace,
)
from mascope_backend.db.id import gen_id


ION_RECORDS = "/api/match/records/ion"
ION_SERIES = "/api/match/records/ion/series"
ISOTOPE_RECORDS = "/api/match/records/isotope"


@pytest_asyncio.fixture
async def scope(async_session_factory, ws_alpha, ws_beta):
    """Collections in ws_alpha, ws_beta, a memberless workspace and none.

    One compound and ion per collection, plus one ion whose compound is in
    both the alpha and the beta collection.
    """
    ids = SimpleNamespace(
        mechanism=gen_id(),
        ws_gamma=gen_id(),
        collection={
            "alpha": gen_id(),
            "beta": gen_id(),
            "gamma": gen_id(),
            "global": gen_id(),
        },
    )
    compounds = {
        name: gen_id() for name in ("alpha", "beta", "gamma", "global", "both")
    }
    ids.ion = {name: gen_id() for name in compounds}
    workspace_of = {
        "alpha": ws_alpha["workspace_id"],
        "beta": ws_beta["workspace_id"],
        "gamma": ids.ws_gamma,
        "global": None,
    }
    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=ids.ws_gamma,
                workspace_name=f"Gamma Workspace {ids.ws_gamma}",
                workspace_status="active",
                workspace_utc_created=datetime.now(timezone.utc),
                workspace_utc_modified=datetime.now(timezone.utc),
            )
        )
        session.add(
            IonizationMechanism(
                ionization_mechanism_id=ids.mechanism,
                ionization_mechanism_polarity="+",
                ionization_mechanism=f"[M+H]+ {ids.mechanism}",
            )
        )
        for name, compound_id in compounds.items():
            session.add(
                TargetCompound(
                    target_compound_id=compound_id,
                    target_compound_name=f"Scope Compound {name} {compound_id}",
                    target_compound_formula="C6H12O6",
                )
            )
        await session.flush()
        for name, collection_id in ids.collection.items():
            session.add(
                TargetCollection(
                    target_collection_id=collection_id,
                    target_collection_name=f"Scope Collection {name} {collection_id}",
                    workspace_id=workspace_of[name],
                )
            )
        await session.flush()
        links = [(name, name) for name in ids.collection] + [
            ("both", "alpha"),
            ("both", "beta"),
        ]
        for compound, collection in links:
            session.add(
                TargetCompoundInTargetCollection(
                    target_compound_id=compounds[compound],
                    target_collection_id=ids.collection[collection],
                )
            )
        for name, compound_id in compounds.items():
            session.add(
                TargetIon(
                    target_ion_id=ids.ion[name],
                    target_compound_id=compound_id,
                    ionization_mechanism_id=ids.mechanism,
                    target_ion_formula="C6H13O6+",
                )
            )
        await session.commit()
    yield ids
    async with async_session_factory() as session:
        await session.execute(
            delete(TargetCompound).where(
                TargetCompound.target_compound_id.in_(compounds.values())
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
        await session.execute(
            delete(Workspace).where(Workspace.workspace_id == ids.ws_gamma)
        )
        await session.commit()


@pytest.fixture
def services(monkeypatch):
    """Stub the three record services; collects the arguments of each call."""
    calls = []

    async def records(**kwargs):
        calls.append(kwargs)
        return {"status": "success", "message": "stub", "results": 0, "data": []}

    for name in (
        "get_match_ion_records",
        "get_match_ion_series",
        "get_match_isotope_records",
    ):
        monkeypatch.setattr(routes, name, records)
    return calls


async def _ask_for_collection(client, url, collection_id, sample_scope):
    if url == ISOTOPE_RECORDS:
        params = {**_query_scope(sample_scope), "target_collection_id": collection_id}
        return await client.get(url, params=params)
    return await client.post(
        url, json={**sample_scope, "target_collection_id": collection_id}
    )


async def _ask_for_ions(client, url, ion_ids, sample_scope):
    if url == ISOTOPE_RECORDS:
        (ion_id,) = ion_ids
        params = {**_query_scope(sample_scope), "target_ion_id": ion_id}
        return await client.get(url, params=params)
    return await client.post(url, json={**sample_scope, "target_ion_ids": ion_ids})


def _query_scope(sample_scope: dict) -> dict:
    """The isotope route takes one sample item ID as a query parameter."""
    if "sample_item_ids" in sample_scope:
        (sample_item_id,) = sample_scope["sample_item_ids"]
        return {"sample_item_id": sample_item_id}
    return sample_scope


ALL_ROUTES = pytest.mark.parametrize("url", [ION_RECORDS, ION_SERIES, ISOTOPE_RECORDS])
ION_LIST_ROUTES = pytest.mark.parametrize("url", [ION_RECORDS, ION_SERIES])


@pytest.fixture(params=["batch", "samples"])
def sample_scope(request, alpha_batch, alpha_item):
    """The caller's own sample scope, as a batch or as a sample item list."""
    if request.param == "batch":
        return {"sample_batch_id": alpha_batch}
    return {"sample_item_ids": [alpha_item]}


# --- A named target collection ----------------------------------------------


@pytest.mark.asyncio
@ALL_ROUTES
async def test_a_guest_naming_another_workspaces_collection_is_refused(
    guest_client, scope, services, sample_scope, url
):
    resp = await _ask_for_collection(
        guest_client, url, scope.collection["beta"], sample_scope
    )

    assert resp.status_code == 403, resp.text
    assert services == []


@pytest.mark.asyncio
@ALL_ROUTES
@pytest.mark.parametrize("collection", ["alpha", "global"])
async def test_a_guest_reads_a_collection_of_their_workspace_or_a_global_one(
    guest_client, scope, services, sample_scope, url, collection
):
    resp = await _ask_for_collection(
        guest_client, url, scope.collection[collection], sample_scope
    )

    assert resp.status_code == 200, resp.text
    assert len(services) == 1


@pytest.mark.asyncio
@ALL_ROUTES
async def test_a_collection_that_does_not_exist_is_refused(
    guest_client, scope, services, sample_scope, url
):
    resp = await _ask_for_collection(guest_client, url, gen_id(), sample_scope)

    assert resp.status_code == 403, resp.text
    assert services == []


@pytest.mark.asyncio
@ALL_ROUTES
async def test_a_superuser_reads_a_collection_of_any_workspace(
    owner_client, scope, services, sample_scope, url
):
    resp = await _ask_for_collection(
        owner_client, url, scope.collection["gamma"], sample_scope
    )

    assert resp.status_code == 200, resp.text
    assert len(services) == 1


# --- Named target ions ------------------------------------------------------


@pytest.mark.asyncio
@ALL_ROUTES
async def test_a_guest_naming_an_ion_of_another_workspace_is_refused(
    guest_client, scope, services, sample_scope, url
):
    resp = await _ask_for_ions(guest_client, url, [scope.ion["beta"]], sample_scope)

    assert resp.status_code == 403, resp.text
    assert services == []


@pytest.mark.asyncio
@ALL_ROUTES
@pytest.mark.parametrize("ion", ["alpha", "global", "both"])
async def test_a_guest_reads_an_ion_in_a_collection_they_can_read(
    guest_client, scope, services, sample_scope, url, ion
):
    """``both`` is in the beta collection too: one readable collection is enough."""
    resp = await _ask_for_ions(guest_client, url, [scope.ion[ion]], sample_scope)

    assert resp.status_code == 200, resp.text
    assert len(services) == 1


@pytest.mark.asyncio
@ION_LIST_ROUTES
async def test_one_unreadable_ion_refuses_the_whole_list(
    guest_client, scope, services, sample_scope, url
):
    readable = [scope.ion["alpha"], scope.ion["global"], scope.ion["both"]]

    allowed = await _ask_for_ions(guest_client, url, readable, sample_scope)
    refused = await _ask_for_ions(
        guest_client, url, [*readable, scope.ion["gamma"]], sample_scope
    )

    assert allowed.status_code == 200, allowed.text
    assert refused.status_code == 403, refused.text
    assert len(services) == 1  # the allowed request only


@pytest.mark.asyncio
@ALL_ROUTES
async def test_an_ion_that_does_not_exist_is_refused(
    guest_client, scope, services, sample_scope, url
):
    resp = await _ask_for_ions(guest_client, url, [gen_id()], sample_scope)

    assert resp.status_code == 403, resp.text
    assert services == []


@pytest.mark.asyncio
@ALL_ROUTES
async def test_a_superuser_reads_an_ion_of_any_workspace(
    owner_client, scope, services, sample_scope, url
):
    resp = await _ask_for_ions(owner_client, url, [scope.ion["gamma"]], sample_scope)

    assert resp.status_code == 200, resp.text
    assert len(services) == 1


# --- No ion scope -------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [ION_RECORDS, ISOTOPE_RECORDS])
async def test_a_guest_without_an_ion_scope_reaches_the_query(
    guest_client, services, sample_scope, url
):
    """The app's default request names neither a collection nor ions."""
    if url == ISOTOPE_RECORDS:
        resp = await guest_client.get(url, params=_query_scope(sample_scope))
    else:
        resp = await guest_client.post(url, json=sample_scope)

    assert resp.status_code == 200, resp.text
    assert len(services) == 1


@pytest.mark.asyncio
async def test_the_series_of_a_readable_ion_still_comes_back(
    guest_client, scope, alpha_batch
):
    """The check refuses nothing it should pass on to the query."""
    resp = await guest_client.post(
        ION_SERIES,
        json={"sample_batch_id": alpha_batch, "target_ion_ids": [scope.ion["both"]]},
    )

    assert resp.status_code == 200, resp.text
    assert [each["target_ion_id"] for each in resp.json()["data"]] == [
        scope.ion["both"]
    ]
