"""
Integration: the sample ledger and the detail read put what a row's target
compound declares beside what a list calls its formula.

Points the read fixture's monoisotopic row at a target compound, gives the
compound a name and an InChIKey or not, seeds a list carrying the key or not,
and reads the row through the HTTP stack: the listing's ``target`` is the
compound's name and key and whether a list the deployment matches against
carries the key (identification claims, section 4.2).
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, update

import mascope_backend.api.new.peak_assignments.target_listing as target_listing
from mascope_backend.db import (
    PeakAssignment,
    ReferenceCompound,
    ReferenceSource,
    TargetCompound,
)
from mascope_backend.db.id import gen_id
from mascope_reference import canonical_formula, monoisotopic_mass


pytestmark = pytest.mark.asyncio

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
GLUCOSE = "WQZGKKKJIJFFOK-GASJEMHNSA-N"
UNLISTED = "WQZGKKKJIJFFOK-ZZZZZZZZSA-N"


@pytest_asyncio.fixture
async def targeted(async_session_factory, pa_test_data):
    """The fixture's M0 row matched to a target compound the test shapes.

    :return: A coroutine function ``(name, inchikey)`` that (re)declares the
        compound; the row, the compound and any list are put back after.
    """
    compound_id = gen_id(16)
    source_ids: list[int] = []
    async with async_session_factory() as session:
        session.add(
            TargetCompound(
                target_compound_id=compound_id, target_compound_formula="C6H12O6"
            )
        )
        await session.flush()
        await session.execute(
            update(PeakAssignment)
            .where(
                PeakAssignment.peak_assignment_id == pa_test_data["m0_assignment_id"]
            )
            .values(target_compound_id=compound_id)
        )
        await session.commit()

    async def declare(name, inchikey):
        async with async_session_factory() as session:
            await session.execute(
                update(TargetCompound)
                .where(TargetCompound.target_compound_id == compound_id)
                .values(target_compound_name=name, inchikey=inchikey)
            )
            await session.commit()

    async def list_key(inchikey, license="CC-BY-4.0", kind=None):
        async with async_session_factory() as session:
            source = ReferenceSource(
                name=f"sugars-{gen_id(6)}",
                version="test",
                license=license,
                record_count=1,
                is_active=True,
                ingested_at=_NOW,
                known_window={},
                kind=kind,
            )
            session.add(source)
            await session.flush()
            source_ids.append(source.reference_source_id)
            session.add(
                ReferenceCompound(
                    reference_source_id=source.reference_source_id,
                    formula=canonical_formula("C6H12O6"),
                    monoisotopic_mass=monoisotopic_mass(canonical_formula("C6H12O6")),
                    inchikey=inchikey,
                    name="D-glucose",
                    source_native_id=f"glc-{gen_id(6)}",
                    xrefs={},
                    license=license,
                )
            )
            await session.commit()

    declare.list_key = list_key
    yield declare

    async with async_session_factory() as session:
        await session.execute(
            update(PeakAssignment)
            .where(
                PeakAssignment.peak_assignment_id == pa_test_data["m0_assignment_id"]
            )
            .values(target_compound_id=None)
        )
        await session.execute(
            delete(TargetCompound).where(
                TargetCompound.target_compound_id == compound_id
            )
        )
        if source_ids:
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


async def _detail(client, pa_test_data) -> dict:
    response = await client.get(
        f"/api/peak-assignments/sample/{pa_test_data['sample_item_id']}"
        f"/assignment/{pa_test_data['m0_assignment_id']}"
    )
    assert response.status_code == 200, response.text
    (record,) = response.json()["data"]
    return record


async def _ledger(client, pa_test_data) -> dict:
    response = await client.get(
        f"/api/peak-assignments/sample/{pa_test_data['sample_item_id']}"
    )
    assert response.status_code == 200, response.text
    return {row["sample_peak_id"]: row for row in response.json()["data"]}


async def test_a_target_whose_key_a_list_carries_reads_listed(
    guest_client, pa_test_data, targeted
):
    await targeted("glucose", GLUCOSE)
    await targeted.list_key(GLUCOSE)
    record = await _detail(guest_client, pa_test_data)
    # The row matched no list itself, so the list's fields name nothing.
    assert record["reference_listing"] == {
        "name": None,
        "source": None,
        "inchikey": None,
        "tags": [],
        "total": 0,
        "target": {
            "label": "target",
            "name": "glucose",
            "inchikey": GLUCOSE,
            "listed": True,
        },
    }


async def test_the_ledger_row_carries_the_same_target(
    guest_client, pa_test_data, targeted
):
    await targeted("glucose", GLUCOSE)
    await targeted.list_key(GLUCOSE)
    rows = await _ledger(guest_client, pa_test_data)
    assert rows["peak-1"]["reference_listing"]["target"] == {
        "label": "target",
        "name": "glucose",
        "inchikey": GLUCOSE,
        "listed": True,
    }
    # A row matched to no target and named by no list still carries none.
    assert rows["peak-3"]["reference_listing"] is None


async def test_a_key_no_list_carries_reads_unlisted(
    guest_client, pa_test_data, targeted
):
    await targeted("glucose", UNLISTED)
    await targeted.list_key(GLUCOSE)
    target = (await _detail(guest_client, pa_test_data))["reference_listing"]["target"]
    assert (target["inchikey"], target["listed"]) == (UNLISTED, False)


async def test_a_list_the_deployment_does_not_match_against_does_not_list_it(
    guest_client, pa_test_data, targeted, monkeypatch
):
    await targeted("glucose", GLUCOSE)
    await targeted.list_key(GLUCOSE, license="restricted")
    monkeypatch.setattr(target_listing, "reference_license_gate", lambda: ["CC0-1.0"])
    target = (await _detail(guest_client, pa_test_data))["reference_listing"]["target"]
    assert target["listed"] is False


async def test_a_target_with_no_structure_shows_its_name_and_no_key(
    guest_client, pa_test_data, targeted
):
    await targeted("glucose", None)
    target = (await _detail(guest_client, pa_test_data))["reference_listing"]["target"]
    assert target == {
        "label": "target",
        "name": "glucose",
        "inchikey": None,
        "listed": None,
    }


async def test_a_row_the_run_also_matched_from_a_list_keeps_its_listing(
    guest_client, pa_test_data, targeted, async_session_factory
):
    """The engine's precedence holds: the target owns the row, the list's
    identities ride alongside, and the listing reads as it did."""
    await targeted("glucose", GLUCOSE)
    await targeted.list_key(GLUCOSE)
    identity = {
        "name": "D-glucose",
        "source": "sugars",
        "license": "CC0-1.0",
        "inchikey": GLUCOSE,
        "source_native_id": "glc",
        "xrefs": {},
    }
    async with async_session_factory() as session:
        row = await session.get(PeakAssignment, pa_test_data["m0_assignment_id"])
        original = row.provenance
        row.provenance = {**original, "reference_identities": [identity]}
        await session.commit()
    try:
        listing = (await _detail(guest_client, pa_test_data))["reference_listing"]
    finally:
        async with async_session_factory() as session:
            row = await session.get(PeakAssignment, pa_test_data["m0_assignment_id"])
            row.provenance = original
            await session.commit()
    assert (listing["name"], listing["source"], listing["inchikey"]) == (
        "D-glucose",
        "sugars",
        GLUCOSE,
    )
    assert listing["total"] == 1
    assert listing["target"]["inchikey"] == GLUCOSE


@pytest.mark.parametrize("name", [None, ""], ids=repr)
async def test_a_target_that_declares_nothing_leaves_the_row_as_it_was(
    guest_client, pa_test_data, targeted, name
):
    """No name and no key stands for nothing beyond the row's formula, so no
    candidate: the row reads as it did, not *target - Unnamed compound*."""
    await targeted(name, None)
    assert (await _detail(guest_client, pa_test_data))["reference_listing"] is None
    assert (await _ledger(guest_client, pa_test_data))["peak-1"][
        "reference_listing"
    ] is None
