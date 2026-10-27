"""
Integration: the reference sources API says which sources are loaded and what
one of them holds.

Seeds a shipped list's source in two versions (the older one replaced), a
custom source under another licence and a source that was deactivated, then
reads both routes through the HTTP stack as a signed-in user who is a member
of no workspace: reference data belongs to the deployment, not to one.
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.app.fast import fast
from mascope_backend.db import ReferenceCompound, ReferenceSource, User
from mascope_backend.db.id import gen_id
from mascope_backend.runtime import runtime
from mascope_reference import canonical_formula, catalogue, monoisotopic_mass


_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)

#: A list Mascope ships, so its source carries the list's label. Its label
#: ("Mass spectrometry ...") sorts after the custom source's, its name before.
_SHIPPED = next(p for p in catalogue() if p.id == "contaminants-keller2008")

_CUSTOM = "d-test-custom-list"
_RETIRED = "d-test-retired-list"

_HOM = ("C10H16O3", "C10H16O4", "C10H16O5")


def _compound(source_id: int, formula: str, native_id: str, license: str):
    canon = canonical_formula(formula)
    return ReferenceCompound(
        reference_source_id=source_id,
        formula=canon,
        monoisotopic_mass=monoisotopic_mass(canon),
        inchikey=None,
        name=f"compound {native_id}",
        smiles=None,
        inchi=None,
        source_native_id=native_id,
        xrefs={"reference": "10.1000/test", "tags": ["test-tag"]},
        license=license,
    )


@pytest_asyncio.fixture
async def sources(async_session_factory):
    """The four source rows and their compounds, removed afterwards."""
    source_ids = []
    async with async_session_factory() as session:
        for name, version, license, active, formulas in (
            (_SHIPPED.id, "old", "CC-BY-4.0", False, _HOM[:1]),
            (_SHIPPED.id, "new", "CC-BY-4.0", True, _HOM),
            (_CUSTOM, "1", "restricted", True, _HOM[:2]),
            (_RETIRED, "1", "CC-BY-4.0", False, _HOM[:1]),
        ):
            source = ReferenceSource(
                name=name,
                version=version,
                license=license,
                record_count=len(formulas),
                is_active=active,
                ingested_at=_NOW,
                known_window={"c_max": 20},
                allow_radicals=name == _CUSTOM,
                polarity="negative" if name == _CUSTOM else None,
            )
            session.add(source)
            await session.flush()
            source_ids.append(source.reference_source_id)
            session.add_all(
                _compound(
                    source.reference_source_id, formula, f"{version}-{i}", license
                )
                for i, formula in enumerate(formulas)
            )
        await session.commit()

    yield

    async with async_session_factory() as session:
        await session.execute(
            delete(ReferenceCompound).where(
                ReferenceCompound.reference_source_id.in_(source_ids)
            )
        )
        await session.execute(
            delete(ReferenceSource).where(
                ReferenceSource.reference_source_id.in_(source_ids)
            )
        )
        await session.commit()


@pytest_asyncio.fixture
async def outsider_client(async_session_factory, roles, create_jwt_auth_token):
    """A signed-in guest who is a member of no workspace."""
    async with async_session_factory() as session:
        user = User(
            email=f"reference-outsider-{gen_id(8)}@test.com",
            username=f"reference_outsider_{gen_id(8)}",
            hashed_password="123456",
            is_active=True,
            is_verified=False,
            role_id=roles["guest"].role_id,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
    async with AsyncClient(
        transport=ASGITransport(app=fast),
        base_url="http://test",
        cookies={auth_settings.COOKIE_NAME: create_jwt_auth_token(user)},
    ) as client:
        yield client
    async with async_session_factory() as session:
        await session.execute(delete(User).where(User.id == user.id))
        await session.commit()


async def _sources(client) -> list[dict]:
    response = await client.get("/api/reference/sources")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == len(body["data"])
    return [s for s in body["data"] if s["name"] in (_SHIPPED.id, _CUSTOM, _RETIRED)]


@pytest.mark.asyncio
async def test_the_active_sources_are_listed_by_label(outsider_client, sources):
    custom, shipped = await _sources(outsider_client)
    ingested_at = datetime.fromisoformat(shipped.pop("ingested_at"))
    assert ingested_at == _NOW
    assert shipped == {
        "name": _SHIPPED.id,
        "version": "new",
        "label": _SHIPPED.label,
        "references": [
            {
                "citation": _SHIPPED.references[0]["citation"],
                "doi": "10.1016/j.aca.2008.04.043",
                "isbn": None,
                "note": None,
            }
        ],
        "license": "CC-BY-4.0",
        "polarity": None,
        "allow_radicals": False,
        "known_window": {"c_max": 20},
        "record_count": 3,
        "admitted": True,
    }
    # A source no shipped list names is labelled by its name.
    assert (custom["name"], custom["label"]) == (_CUSTOM, _CUSTOM)
    assert (custom["polarity"], custom["allow_radicals"]) == ("negative", True)
    # ... and cites no publication: the database keeps none for it.
    assert custom["references"] == []


@pytest.mark.asyncio
async def test_the_list_is_sorted_by_label_not_name(outsider_client, sources):
    assert [s["name"] for s in await _sources(outsider_client)] == [
        _CUSTOM,
        _SHIPPED.id,
    ]
    response = await outsider_client.get("/api/reference/sources")
    labels = [s["label"].casefold() for s in response.json()["data"]]
    assert labels == sorted(labels)


@pytest.mark.asyncio
async def test_a_source_outside_the_licence_gate_is_not_admitted(
    outsider_client, sources, monkeypatch
):
    monkeypatch.setattr(
        runtime.full_config.backend, "reference_licenses", ["CC-BY-4.0"]
    )
    custom, shipped = await _sources(outsider_client)
    assert shipped["admitted"] is True
    assert custom["admitted"] is False


@pytest.mark.asyncio
async def test_with_no_gate_every_source_is_admitted(outsider_client, sources):
    assert runtime.full_config.backend.reference_licenses is None
    assert all(s["admitted"] for s in await _sources(outsider_client))


@pytest.mark.asyncio
async def test_the_compounds_of_the_active_version(outsider_client, sources):
    response = await outsider_client.get(
        f"/api/reference/sources/{_SHIPPED.id}/compounds"
    )
    assert response.status_code == 200
    body = response.json()
    assert (body["name"], body["version"], body["total"]) == (_SHIPPED.id, "new", 3)
    first = body["data"][0]
    assert first == {
        "name": "compound new-0",
        "formula": canonical_formula("C10H16O3"),
        "monoisotopic_mass": pytest.approx(
            monoisotopic_mass(canonical_formula("C10H16O3"))
        ),
        "inchikey": None,
        "source_native_id": "new-0",
        "reference": "10.1000/test",
        "tags": ["test-tag"],
    }
    # By mass: one oxygen more each.
    assert [c["source_native_id"] for c in body["data"]] == ["new-0", "new-1", "new-2"]


@pytest.mark.asyncio
async def test_the_compounds_page(outsider_client, sources):
    url = f"/api/reference/sources/{_SHIPPED.id}/compounds"
    pages = [
        (await outsider_client.get(url, params={"page": page, "limit": 2})).json()
        for page in (0, 1)
    ]
    assert [[c["source_native_id"] for c in p["data"]] for p in pages] == [
        ["new-0", "new-1"],
        ["new-2"],
    ]
    assert {p["total"] for p in pages} == {3}


@pytest.mark.asyncio
async def test_a_named_version_is_served_even_when_replaced(outsider_client, sources):
    response = await outsider_client.get(
        f"/api/reference/sources/{_SHIPPED.id}/compounds", params={"version": "old"}
    )
    assert response.status_code == 200
    body = response.json()
    assert (body["version"], body["total"]) == ("old", 1)
    assert [c["source_native_id"] for c in body["data"]] == ["old-0"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name, params",
    [
        (_SHIPPED.id, {"version": "missing"}),
        (_RETIRED, {}),
        ("no-such-source", {}),
    ],
)
async def test_no_such_version_is_not_found(outsider_client, sources, name, params):
    response = await outsider_client.get(
        f"/api/reference/sources/{name}/compounds", params=params
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_a_page_past_the_cap_is_refused(outsider_client, sources):
    response = await outsider_client.get(
        f"/api/reference/sources/{_SHIPPED.id}/compounds", params={"limit": 1001}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    ["/api/reference/sources", f"/api/reference/sources/{_SHIPPED.id}/compounds"],
)
async def test_both_routes_need_a_signed_in_user(sources, path):
    async with AsyncClient(
        transport=ASGITransport(app=fast), base_url="http://test"
    ) as client:
        response = await client.get(path)
    assert response.status_code == 401
