"""
Integration tests: a mechanism in either notation, stored in the standard one.

A mechanism is written ``[M-H]-`` - the standard adduct notation, the ion's
charge last - and a row written before that holds the legacy ``-H+`` until the
migration rewrites it. Both have to behave as one mechanism: read back in the
standard notation, found by either spelling, and refused a second time in the
other one, which the column's unique constraint alone would let through. A
mechanism of several terms is one mechanism whichever order they are typed in,
and is stored with them in order.
"""

import itertools
import random

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text

from mascope_backend.db import IonizationMechanism
from mascope_backend.db.id import gen_id


#: The moieties are drawn in turn, from a random start, so that no two tests
#: of a session draw the same one.
_DRAWS = itertools.count(random.randrange(12_544))


def _unique_moiety() -> str:
    """A moiety no other test stores, since the mechanism column is unique.

    Light, because creating a mechanism builds its ion for every compound in
    the library, and a heavy moiety makes every one of those envelopes large:
    one of 12,544, C2-9 H2-99 N0-3 O0-3. It sorts ahead of ``H``, as a term.
    """
    draw = next(_DRAWS) % 12_544
    carbon, draw = 2 + draw % 8, draw // 8
    hydrogen, draw = 2 + draw % 98, draw // 98
    nitrogen, oxygen = draw % 4, draw // 4
    return (
        f"C{carbon}H{hydrogen}"
        + (f"N{nitrogen}" if nitrogen else "")
        + (f"O{oxygen}" if oxygen else "")
    )


async def _create(client, mechanism: str):
    return await client.post(
        "/api/ionization_mechanisms", json={"ionization_mechanism": mechanism}
    )


async def _refused_as_a_duplicate(client, mechanism: str) -> None:
    """Assert the mechanism is refused as one that exists, and take out a row a
    regression creates, so that one red test does not leave its spelling and
    its ions behind for the tests after it."""
    response = await _create(client, mechanism)
    if response.status_code == 201:
        await client.delete(
            "/api/ionization_mechanisms/"
            + response.json()["data"]["ionization_mechanism_id"]
        )
    assert response.status_code == 409, (mechanism, response.text)


@pytest_asyncio.fixture
async def legacy_row(async_session_factory):
    """A stored row still holding the legacy spelling of its mechanism."""
    mechanism_id = gen_id()
    moiety = _unique_moiety()
    legacy = f"-{moiety}+"
    async with async_session_factory() as session:
        session.add(
            IonizationMechanism(
                ionization_mechanism_id=mechanism_id,
                ionization_mechanism_polarity="-",
                ionization_mechanism=legacy,
            )
        )
        await session.commit()

    yield mechanism_id, legacy, f"[M-{moiety}]-"

    async with async_session_factory() as session:
        await session.execute(
            delete(IonizationMechanism).where(
                IonizationMechanism.ionization_mechanism_id == mechanism_id
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_a_legacy_row_is_read_in_the_standard_notation(
    async_session_factory, guest_client, legacy_row
):
    mechanism_id, legacy, standard = legacy_row

    async with async_session_factory() as session:
        stored = (
            await session.execute(
                text(
                    "SELECT ionization_mechanism FROM ionization_mechanism "
                    "WHERE ionization_mechanism_id = :id"
                ),
                {"id": mechanism_id},
            )
        ).scalar_one()
        read = (
            await session.execute(
                select(IonizationMechanism.ionization_mechanism).where(
                    IonizationMechanism.ionization_mechanism_id == mechanism_id
                )
            )
        ).scalar_one()
    assert stored == legacy
    assert read == standard

    response = await guest_client.get(f"/api/ionization_mechanisms/{mechanism_id}")
    assert response.status_code == 200, response.text
    assert response.json()["data"]["ionization_mechanism"] == standard


@pytest.mark.asyncio
@pytest.mark.parametrize("spelling", ["legacy", "standard"])
async def test_a_legacy_row_is_found_by_either_spelling(
    guest_client, legacy_row, spelling
):
    mechanism_id, legacy, standard = legacy_row
    wanted = legacy if spelling == "legacy" else standard

    response = await guest_client.get(
        "/api/ionization_mechanisms", params={"ionization_mechanism": [wanted]}
    )

    assert response.status_code == 200, response.text
    assert [row["ionization_mechanism_id"] for row in response.json()["data"]] == [
        mechanism_id
    ]


@pytest.mark.asyncio
async def test_a_legacy_row_is_not_created_again_in_the_standard_spelling(
    editor_client, legacy_row
):
    _, _, standard = legacy_row

    await _refused_as_a_duplicate(editor_client, standard)


@pytest.mark.asyncio
async def test_a_legacy_spelling_is_created_in_the_standard_one(
    editor_client, async_session_factory
):
    moiety = _unique_moiety()

    response = await _create(editor_client, f"+{moiety}-")

    assert response.status_code == 201, response.text
    data = response.json()["data"]
    try:
        assert data["ionization_mechanism"] == f"[M+{moiety}]-"
        assert data["ionization_mechanism_polarity"] == "-"
        async with async_session_factory() as session:
            stored = (
                await session.execute(
                    text(
                        "SELECT ionization_mechanism FROM ionization_mechanism "
                        "WHERE ionization_mechanism_id = :id"
                    ),
                    {"id": data["ionization_mechanism_id"]},
                )
            ).scalar_one()
        assert stored == f"[M+{moiety}]-"
    finally:
        await editor_client.delete(
            f"/api/ionization_mechanisms/{data['ionization_mechanism_id']}"
        )


@pytest.mark.asyncio
async def test_a_mechanism_is_stored_with_its_terms_in_order_and_created_once(
    editor_client,
):
    moiety = _unique_moiety()
    stored = f"[M+{moiety}+H]+"

    response = await _create(editor_client, f"[M+H+{moiety}]+")

    assert response.status_code == 201, response.text
    created = response.json()["data"]
    try:
        assert created["ionization_mechanism"] == stored
        for again in (stored, f"+(H){moiety}+", f"+({moiety})H+"):
            await _refused_as_a_duplicate(editor_client, again)
    finally:
        await editor_client.delete(
            f"/api/ionization_mechanisms/{created['ionization_mechanism_id']}"
        )
