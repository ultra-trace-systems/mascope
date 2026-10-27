"""
Integration tests: a target compound's structure identity through the routes.

A compound may carry an InChIKey, an InChI and a SMILES string. The InChI must
describe the formula the row will have after the write: the incoming one on a
create or a formula change, the stored one on an update that leaves the
formula alone - which is why the check lives in the controllers and not in the
request models, which never see the stored formula. A formula change recreates
the compound as another substance, so its identity does not carry over.
"""

from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from mascope_backend.db import TargetCompound
from mascope_backend.db.id import gen_id


METHANE = {
    "target_compound_formula": "CH4",
    "inchikey": "VNWKTOKETHGBQD-UHFFFAOYSA-N",
    "inchi": "InChI=1S/CH4/h1H4",
    "smiles": "C",
}
ETHANE = {
    "target_compound_formula": "C2H6",
    "inchikey": "OTMSDBZUPAUEDD-UHFFFAOYSA-N",
    "inchi": "InChI=1S/C2H6/c1-2/h1-2H3",
    "smiles": "CC",
}
IDENTITY = ("inchikey", "inchi", "smiles")


@pytest_asyncio.fixture
async def compound_name(async_session_factory):
    """A compound name no stored compound has; every compound whose name starts
    with it is removed after."""
    name = f"Identity test {gen_id()}"

    yield name

    async with async_session_factory() as session:
        await session.execute(
            delete(TargetCompound).where(
                TargetCompound.target_compound_name.startswith(name, autoescape=True)
            )
        )
        await session.commit()


async def _stored(async_session_factory, name: str) -> list[TargetCompound]:
    async with async_session_factory() as session:
        return (
            await session.scalars(
                select(TargetCompound).where(
                    TargetCompound.target_compound_name == name
                )
            )
        ).all()


def _identity(compound: TargetCompound) -> dict:
    return {field: getattr(compound, field) for field in IDENTITY}


def _identity_of(compound: dict) -> dict:
    return {field: compound[field] for field in IDENTITY}


async def _create(editor_client, name: str, compound: dict):
    return await editor_client.post(
        "/api/target/compounds",
        json=[{"target_compound_name": name, **compound}],
    )


@pytest.mark.asyncio
async def test_a_created_compound_keeps_its_identity(
    editor_client, async_session_factory, compound_name
):
    response = await _create(editor_client, compound_name, METHANE)
    assert response.status_code == 201, response.text

    (stored,) = await _stored(async_session_factory, compound_name)
    assert _identity(stored) == _identity_of(METHANE)


@pytest.mark.asyncio
async def test_a_create_whose_inchi_is_another_formulas_is_refused(
    editor_client, async_session_factory, compound_name
):
    response = await _create(
        editor_client, compound_name, {**METHANE, "inchi": ETHANE["inchi"]}
    )

    assert response.status_code == 422, response.text
    assert "C2H6" in response.text
    assert "CH4" in response.text
    assert await _stored(async_session_factory, compound_name) == []


@pytest.mark.asyncio
async def test_an_identity_only_update_is_checked_against_the_stored_formula(
    editor_client, async_session_factory, compound_name
):
    """The update sends no formula, so the stored one is what the InChI must
    describe; refusing it leaves the compound as it was."""
    created = await _create(
        editor_client, compound_name, {"target_compound_formula": "CH4"}
    )
    (compound,) = created.json()["data"]["created_compounds"]

    response = await editor_client.patch(
        "/api/target/compounds",
        json=[
            {
                "target_compound_id": compound["target_compound_id"],
                "inchi": ETHANE["inchi"],
            }
        ],
    )

    assert response.status_code == 422, response.text
    assert "C2H6" in response.text
    assert "'CH4'" in response.text
    (stored,) = await _stored(async_session_factory, compound_name)
    assert _identity(stored) == {"inchikey": None, "inchi": None, "smiles": None}


@pytest.mark.asyncio
async def test_the_same_inchi_sent_with_a_matching_formula_is_accepted(
    editor_client, async_session_factory, compound_name
):
    """With the formula it describes, the same InChI is the identity of the
    compound the formula change recreates."""
    created = await _create(
        editor_client, compound_name, {"target_compound_formula": "CH4"}
    )
    (compound,) = created.json()["data"]["created_compounds"]

    response = await editor_client.patch(
        "/api/target/compounds",
        json=[
            {
                "target_compound_id": compound["target_compound_id"],
                "target_compound_name": compound_name,
                **ETHANE,
            }
        ],
    )

    assert response.status_code == 200, response.text
    (stored,) = await _stored(async_session_factory, compound_name)
    assert stored.target_compound_formula == "C2H6"
    assert _identity(stored) == _identity_of(ETHANE)


@pytest.mark.asyncio
async def test_an_identity_only_update_that_agrees_is_stored(
    editor_client, async_session_factory, compound_name
):
    created = await _create(
        editor_client, compound_name, {"target_compound_formula": "CH4"}
    )
    (compound,) = created.json()["data"]["created_compounds"]

    response = await editor_client.patch(
        "/api/target/compounds",
        json=[
            {
                "target_compound_id": compound["target_compound_id"],
                **{field: METHANE[field] for field in IDENTITY},
            }
        ],
    )

    assert response.status_code == 200, response.text
    (stored,) = await _stored(async_session_factory, compound_name)
    assert stored.target_compound_id == compound["target_compound_id"]
    assert _identity(stored) == _identity_of(METHANE)


@pytest.mark.asyncio
async def test_a_formula_change_clears_the_identity(
    editor_client, async_session_factory, compound_name
):
    """A different formula is a different substance: the recreated compound
    has no identity unless the update gives it one."""
    created = await _create(editor_client, compound_name, METHANE)
    (compound,) = created.json()["data"]["created_compounds"]

    response = await editor_client.patch(
        "/api/target/compounds",
        json=[
            {
                "target_compound_id": compound["target_compound_id"],
                "target_compound_name": compound_name,
                "target_compound_formula": "C2H6",
            }
        ],
    )

    assert response.status_code == 200, response.text
    (stored,) = await _stored(async_session_factory, compound_name)
    assert stored.target_compound_formula == "C2H6"
    assert _identity(stored) == {"inchikey": None, "inchi": None, "smiles": None}


@pytest.mark.asyncio
async def test_a_create_naming_a_stored_compound_fills_its_missing_identity(
    editor_client, async_session_factory, compound_name
):
    first = await _create(
        editor_client, compound_name, {"target_compound_formula": "CH4"}
    )
    assert first.status_code == 201, first.text

    again = await _create(editor_client, compound_name, METHANE)

    assert again.status_code == 201, again.text
    assert again.json()["message-logs"]["1"]["status_code"] == 200
    (stored,) = await _stored(async_session_factory, compound_name)
    assert _identity(stored) == _identity_of(METHANE)


@pytest.mark.asyncio
async def test_a_create_naming_a_stored_compound_keeps_the_identity_it_has(
    editor_client, async_session_factory, compound_name
):
    await _create(editor_client, compound_name, METHANE)

    again = await _create(
        editor_client,
        compound_name,
        {**METHANE, "smiles": "[H]C([H])([H])[H]", "inchikey": None, "inchi": None},
    )

    assert again.status_code == 201, again.text
    (stored,) = await _stored(async_session_factory, compound_name)
    assert _identity(stored) == _identity_of(METHANE)
    assert "already has a structure identity" in again.text


@pytest.mark.asyncio
async def test_a_create_sending_no_identity_says_nothing_of_one(
    editor_client, async_session_factory, compound_name
):
    """Naming a stored compound that has an identity, without sending one, is
    the ordinary reuse: its log says the compound was used, and nothing else."""
    await _create(editor_client, compound_name, METHANE)

    again = await _create(
        editor_client, compound_name, {"target_compound_formula": "CH4"}
    )

    assert again.status_code == 201, again.text
    assert len(again.json()["message-logs"]["1"]["messages"]) == 1
    (stored,) = await _stored(async_session_factory, compound_name)
    assert _identity(stored) == _identity_of(METHANE)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sent",
    [ETHANE, {**ETHANE, "inchi": None}],
    ids=["with an InChI", "without an InChI"],
)
async def test_a_stored_compound_found_by_cas_is_not_given_another_formulas_identity(
    editor_client, async_session_factory, compound_name, sent
):
    """A create matched to a stored compound by CAS number alone can carry
    another formula; the identity it sends is that formula's, so it is not
    written onto the stored compound - also when no InChI comes with it to
    disagree with the stored formula."""
    cas_number = f"{uuid4().int % 10**7:07d}-00-0"
    stored_name = f"{compound_name} stored"
    async with async_session_factory() as session:
        session.add(
            TargetCompound(
                target_compound_id=gen_id(),
                target_compound_name=stored_name,
                target_compound_formula="CH4",
                cas_number=cas_number,
            )
        )
        await session.commit()

    response = await _create(
        editor_client,
        f"{compound_name} sent",
        {**sent, "cas_number": cas_number},
    )

    assert response.status_code == 201, response.text
    assert response.json()["message-logs"]["1"]["status_code"] == 200
    (stored,) = await _stored(async_session_factory, stored_name)
    assert _identity(stored) == {"inchikey": None, "inchi": None, "smiles": None}
    assert "Structure identity not added" in response.text
