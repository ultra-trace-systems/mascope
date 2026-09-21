"""
Integration: the detail read names the reference-list compounds a row's
formulas are listed as.

Seeds two active reference sources under different licences - one holding the
committed formula of the read fixture's monoisotopic row, one holding its close
alternative's - and reads the row through the HTTP stack, with and without the
deployment's licence gate.
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete

import mascope_backend.api.new.peak_assignments.service as service
from mascope_backend.db import ReferenceCompound, ReferenceSource
from mascope_reference import canonical_formula, monoisotopic_mass


_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _compound(source_id: int, formula: str, name: str, license: str):
    return ReferenceCompound(
        reference_source_id=source_id,
        formula=canonical_formula(formula),
        monoisotopic_mass=monoisotopic_mass(canonical_formula(formula)),
        inchikey=None,
        name=name,
        smiles=None,
        inchi=None,
        source_native_id=f"{formula} {name}",
        xrefs={},
        license=license,
    )


@pytest_asyncio.fixture
async def listed(async_session_factory):
    """The fixture row's formula in an open list, its alternative's in a closed one.

    A third list, detected in negative mode only, also holds the row's formula:
    the fixture's sample is positive, so it names nothing there.
    """
    source_ids = []
    async with async_session_factory() as session:
        for name, license, formula, compound, polarity in (
            ("open-list", "CC-BY-4.0", "C6H12O6", "glucose", None),
            ("closed-list", "restricted", "C7H16O5", "a heptitol", None),
            ("negative-list", "CC-BY-4.0", "C6H12O6", "fructose", "negative"),
        ):
            source = ReferenceSource(
                name=name,
                version="test",
                license=license,
                record_count=1,
                is_active=True,
                ingested_at=_NOW,
                known_window={},
                polarity=polarity,
            )
            session.add(source)
            await session.flush()
            source_ids.append(source.reference_source_id)
            session.add(
                _compound(source.reference_source_id, formula, compound, license)
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


async def _detail(client, pa_test_data) -> dict:
    response = await client.get(
        f"/api/peak-assignments/sample/{pa_test_data['sample_item_id']}"
        f"/assignment/{pa_test_data['m0_assignment_id']}"
    )
    assert response.status_code == 200
    (record,) = response.json()["data"]
    return record


@pytest.mark.asyncio
async def test_the_row_and_its_alternative_are_named(
    guest_client, pa_test_data, listed
):
    record = await _detail(guest_client, pa_test_data)
    # The negative-only list's fructose is no name on a positive sample.
    assert [known["name"] for known in record["known_compounds"]] == ["glucose"]
    assert record["known_compounds"][0]["source"] == "open-list"
    assert record["known_compounds_total"] == 1
    alternative = record["alternatives"][0]
    assert alternative["assigned_formula"] == "C7H16O5"
    assert [known["name"] for known in alternative["known_compounds"]] == ["a heptitol"]


@pytest.mark.asyncio
async def test_a_list_the_deployment_does_not_match_against_names_nothing(
    guest_client, pa_test_data, listed, monkeypatch
):
    monkeypatch.setattr(service, "reference_license_gate", lambda: ["CC-BY-4.0"])
    record = await _detail(guest_client, pa_test_data)
    assert [known["name"] for known in record["known_compounds"]] == ["glucose"]
    assert "known_compounds" not in record["alternatives"][0]


@pytest.mark.asyncio
async def test_with_no_list_holding_it_the_row_names_nothing(
    guest_client, pa_test_data
):
    record = await _detail(guest_client, pa_test_data)
    assert (record["known_compounds"], record["known_compounds_total"]) == ([], 0)
