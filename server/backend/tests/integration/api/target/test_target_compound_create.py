"""
Integration tests: creating a target compound builds its ions.

``create_target_compound`` builds every new compound's ions under every stored
ionization mechanism. It used to take those mechanisms from the listing
controller and rebuild rows from its response, which broke once the response
carried a field that is not a column (``shipped``): every create - every target
collection save that adds a compound, and every formula edit, which recreates
the compound - failed with a TypeError.
"""

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from mascope_backend.db import IonizationMechanism, TargetCompound, TargetIon
from mascope_backend.db.id import gen_id


_NOTATION = "[M+Br2]-"


@pytest_asyncio.fixture
async def mechanism(async_session_factory):
    """A stored mechanism the compound's ions are built under.

    The mechanism column is unique and the notation may be stored already, so
    an existing row is used as it is and only a row created here is removed.
    """
    async with async_session_factory() as session:
        existing = await session.scalar(
            select(IonizationMechanism).where(
                IonizationMechanism.ionization_mechanism == _NOTATION
            )
        )
        if existing is not None:
            yield existing.ionization_mechanism_id
            return
        mechanism_id = gen_id()
        session.add(
            IonizationMechanism(
                ionization_mechanism_id=mechanism_id,
                ionization_mechanism_polarity="-",
                ionization_mechanism=_NOTATION,
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
async def compound_name(async_session_factory):
    """A compound name no stored compound has; the compound is removed after."""
    name = f"Create regression {gen_id()}"

    yield name

    async with async_session_factory() as session:
        await session.execute(
            delete(TargetCompound).where(TargetCompound.target_compound_name == name)
        )
        await session.commit()


async def _ion_formulas(async_session_factory, compound_name, mechanism_id):
    """The ion formulas the named compound holds under one mechanism."""
    async with async_session_factory() as session:
        return (
            await session.scalars(
                select(TargetIon.target_ion_formula)
                .join(TargetCompound)
                .where(
                    TargetCompound.target_compound_name == compound_name,
                    TargetIon.ionization_mechanism_id == mechanism_id,
                )
            )
        ).all()


@pytest.mark.asyncio
async def test_a_created_compound_gets_ions_under_the_stored_mechanisms(
    editor_client, async_session_factory, mechanism, compound_name
):
    response = await editor_client.post(
        "/api/target/compounds",
        json=[
            {
                "target_compound_name": compound_name,
                "target_compound_formula": "CH4N2O",
            }
        ],
    )
    assert response.status_code == 201, response.text
    assert response.json()["result"]["created_compounds_result"] == 1

    assert await _ion_formulas(async_session_factory, compound_name, mechanism) == [
        "CH4Br2N2O-"
    ]


@pytest.mark.asyncio
async def test_a_formula_edit_rebuilds_the_compound_with_ions(
    editor_client, async_session_factory, mechanism, compound_name
):
    """A formula edit deletes the compound and creates it again, inside the
    edit's own session - the other way ``create_target_compound`` is reached."""
    created = await editor_client.post(
        "/api/target/compounds",
        json=[
            {
                "target_compound_name": compound_name,
                "target_compound_formula": "CH4N2O",
            }
        ],
    )
    assert created.status_code == 201, created.text
    (compound,) = created.json()["data"]["created_compounds"]

    response = await editor_client.patch(
        "/api/target/compounds",
        json=[
            {
                "target_compound_id": compound["target_compound_id"],
                "target_compound_name": compound_name,
                "target_compound_formula": "C2H6N2O",
            }
        ],
    )
    assert response.status_code == 200, response.text
    assert response.json()["result"]["updated_compounds_results"] == 1

    assert await _ion_formulas(async_session_factory, compound_name, mechanism) == [
        "C2H6Br2N2O-"
    ]
