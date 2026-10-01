"""
Integration tests: a target controller that opens its own session returns its
connection when it fails.

``create_target_compound``, ``delete_target_compound`` and
``create_target_ions`` open a session of their own for an independent
transaction. They used to commit it and never close it, so a failure between
the first query and the commit left the connection checked out until the
garbage collector found the session - SQLAlchemy then logs "The garbage
collector is trying to clean up non-checked-in connection" and terminates it.
Production saw one per failed target collection save.

Each test keeps the exception - and with it the failed call's frame and session
- alive while it counts the pool's checked-out connections, so a connection
still held by that session shows up as one, not as a later GC warning.
"""

import pytest

from mascope_backend.api.controllers.target.compounds import (
    target_compounds_controller,
)
from mascope_backend.api.controllers.target.compounds.target_compounds_controller import (
    create_target_compound,
    delete_target_compound,
)
from mascope_backend.api.controllers.target.ions.target_ions_controller import (
    create_target_ions,
)
from mascope_backend.api.models.target.compounds.target_compound_pydantic_model import (
    TargetCompoundBase,
)
from mascope_backend.db import IonizationMechanism, TargetCompound
from mascope_backend.db.id import gen_id


def _checked_out(async_engine) -> int:
    return async_engine.sync_engine.pool.checkedout()


@pytest.mark.asyncio
async def test_a_failed_compound_create_returns_its_connection(
    async_engine, monkeypatch
):
    async def fail(**_):
        raise RuntimeError("ion generation failed")

    monkeypatch.setattr(target_compounds_controller, "create_target_ions", fail)
    before = _checked_out(async_engine)

    with pytest.raises(Exception) as failure:
        await create_target_compound(
            target_compounds=[
                TargetCompoundBase(
                    target_compound_name=f"Leak regression {gen_id()}",
                    target_compound_formula="CH4N2O",
                )
            ],
            independent_transaction=True,
        )

    # api_controller replaces the message; the cause is the failure raised.
    assert isinstance(failure.value.__cause__, RuntimeError)
    assert _checked_out(async_engine) == before


@pytest.mark.asyncio
async def test_a_failed_compound_delete_returns_its_connection(async_engine):
    before = _checked_out(async_engine)

    with pytest.raises(Exception) as failure:
        await delete_target_compound(gen_id(), independent_transaction=True)

    assert "not found" in str(failure.value)
    assert _checked_out(async_engine) == before


@pytest.mark.asyncio
async def test_a_failed_ion_create_returns_its_connection(async_engine):
    """The commit itself fails here: neither the compound nor the mechanism
    the ions reference is stored."""
    compound = TargetCompound(
        target_compound_id=gen_id(), target_compound_formula="CH4N2O"
    )
    mechanism = IonizationMechanism(
        ionization_mechanism_id=gen_id(),
        ionization_mechanism_polarity="-",
        ionization_mechanism="[M+Br2]-",
    )
    before = _checked_out(async_engine)

    with pytest.raises(Exception) as failure:
        await create_target_ions(
            target_compound=compound,
            ionization_mechanisms=[mechanism],
            independent_transaction=True,
        )

    assert failure.value is not None
    assert _checked_out(async_engine) == before
