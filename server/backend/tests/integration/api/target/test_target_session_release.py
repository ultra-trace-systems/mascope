"""
Integration tests: a target controller that opens its own session returns its
connection when it fails or is cancelled.

``create_target_compound``, ``delete_target_compound`` and
``create_target_ions`` run an independent transaction in a session of their own
(``owned_session``). They used to commit it and never close it, so a failure
between the first query and the commit left the connection checked out until
the garbage collector found the session - SQLAlchemy then logs "The garbage
collector is trying to clean up non-checked-in connection" and terminates it.
Production saw one per failed target collection save.

Each test records the sessions the code under test opens and holds on to them,
so the garbage collector cannot close one first, then asserts that every one
has ended its transaction - which is what returns its connection. That is the
sessions' own state: a connection that another test's background work checks
in or out meanwhile cannot move the result, as a count of the shared pool can.
"""

import asyncio

import pytest

import mascope_backend.db as db_module
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


@pytest.fixture
def opened_sessions(async_session_factory, monkeypatch):
    """Every session ``async_session`` opens during the test, in order."""
    sessions = []

    def record():
        session = async_session_factory()
        sessions.append(session)
        return session

    monkeypatch.setattr(db_module, "ASYNC_SESSION_MAKER", record)
    return sessions


def _released(sessions) -> bool:
    """Whether a session was opened and every one opened has ended its
    transaction, returning its connection."""
    return bool(sessions) and not any(s.in_transaction() for s in sessions)


def _compound() -> TargetCompoundBase:
    return TargetCompoundBase(
        target_compound_name=f"Leak regression {gen_id()}",
        target_compound_formula="CH4N2O",
    )


@pytest.mark.asyncio
async def test_a_failed_compound_create_returns_its_connection(
    opened_sessions, monkeypatch
):
    async def fail(**_):
        raise RuntimeError("ion generation failed")

    monkeypatch.setattr(target_compounds_controller, "create_target_ions", fail)

    with pytest.raises(Exception) as failure:
        await create_target_compound(
            target_compounds=[_compound()], independent_transaction=True
        )

    # api_controller replaces the message; the cause is the failure raised.
    assert isinstance(failure.value.__cause__, RuntimeError)
    assert _released(opened_sessions)


@pytest.mark.asyncio
async def test_a_failed_compound_delete_returns_its_connection(opened_sessions):
    with pytest.raises(Exception) as failure:
        await delete_target_compound(gen_id(), independent_transaction=True)

    assert "not found" in str(failure.value)
    assert _released(opened_sessions)


@pytest.mark.asyncio
async def test_a_failed_ion_create_returns_its_connection(opened_sessions):
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

    with pytest.raises(Exception) as failure:
        await create_target_ions(
            target_compound=compound,
            ionization_mechanisms=[mechanism],
            independent_transaction=True,
        )

    assert failure.value is not None
    assert _released(opened_sessions)


@pytest.mark.asyncio
async def test_a_cancelled_compound_create_returns_its_connection(
    opened_sessions, monkeypatch
):
    """A request cancelled mid-save - a client gone - is cancelled again at its
    next await, the way the request's task group re-delivers a cancellation.
    That next await is the session's close, which has to finish regardless."""
    parked = asyncio.Event()

    async def park(**_):
        parked.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(target_compounds_controller, "create_target_ions", park)
    task = asyncio.create_task(
        create_target_compound(
            target_compounds=[_compound()], independent_transaction=True
        )
    )
    await asyncio.wait_for(parked.wait(), timeout=10)
    (session,) = opened_sessions
    # The connection the controller's queries have checked out.
    connection = await session.connection()

    task.cancel()
    await asyncio.sleep(0)  # the cancellation unwinds into the close
    task.cancel()  # and is delivered again while the close is under way
    with pytest.raises(asyncio.CancelledError):
        await task

    # A shielded close carries on after the task is gone; let it finish.
    for _ in range(200):
        if connection.closed:
            break
        await asyncio.sleep(0.01)
    assert connection.closed
    assert _released(opened_sessions)
