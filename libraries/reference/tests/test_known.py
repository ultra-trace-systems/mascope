"""Tests for the bulk known-composition provider (Stage A input)."""

from datetime import datetime, timezone

import pytest


pytest.importorskip("aiosqlite")

from sqlalchemy import insert  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    async_sessionmaker,
    create_async_engine,
)

from mascope_reference.known import iter_known_compositions  # noqa: E402
from mascope_reference.schema import reference_compound, reference_source  # noqa: E402


def _seed(sync_engine, source_rows):
    """Insert (source, compounds) groups. source_rows: list of (source_dict, [compound_dicts])."""
    with sync_engine.begin() as conn:
        for source, compounds in source_rows:
            sid = conn.execute(
                insert(reference_source)
                .values(
                    name=source["name"],
                    version="v1",
                    license=source.get("license", "public-domain"),
                    record_count=len(compounds),
                    is_active=source.get("is_active", True),
                    ingested_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                )
                .returning(reference_source.c.reference_source_id)
            ).scalar_one()
            for c in compounds:
                conn.execute(
                    insert(reference_compound).values(
                        reference_source_id=sid,
                        formula=c["formula"],
                        monoisotopic_mass=c.get("mass"),
                        charge=c.get("charge"),
                        inchikey=c.get("inchikey"),
                        name=c.get("name"),
                        smiles=None,
                        inchi=None,
                        source_native_id=c.get("id", c["formula"]),
                        xrefs=c.get("xrefs", {}),
                        license=c.get(
                            "license", source.get("license", "public-domain")
                        ),
                    )
                )


async def _known(db_path, **kw):
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    try:
        async with async_sessionmaker(engine)() as s:
            return await iter_known_compositions(s, **kw)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_dedup_on_formula_with_one_to_many_identities(sync_engine, db_path):
    _seed(
        sync_engine,
        [
            (
                {"name": "src-a"},
                [
                    {"formula": "C10H16O3", "mass": 184.11, "name": "Pinonic acid"},
                    {"formula": "C9H14O4", "mass": 186.09, "name": "Pinic acid"},
                ],
            ),
            (
                {"name": "src-b"},
                [
                    {
                        "formula": "C10H16O3",
                        "mass": 184.11,
                        "name": "Norpinonic acid isomer",
                    },
                ],
            ),
        ],
    )
    result = await _known(db_path)
    by_formula = {k.formula: k for k in result}
    assert set(by_formula) == {"C10H16O3", "C9H14O4"}
    # One formula shared by two sources -> one composition, two identities.
    names = sorted(i.name for i in by_formula["C10H16O3"].identities)
    assert names == ["Norpinonic acid isomer", "Pinonic acid"]
    assert {i.source for i in by_formula["C10H16O3"].identities} == {"src-a", "src-b"}


@pytest.mark.asyncio
async def test_charged_rows_are_excluded(sync_engine, db_path):
    """Intrinsic charge is recorded, never matched (issue #1726).

    Stage A pairs a neutral formula with an ionization mechanism, so a charged
    row has no neutral precursor to expand. NULL and 0 are both neutral - NULL
    is also what every pre-charge-column row carries.
    """
    _seed(
        sync_engine,
        [
            (
                {"name": "s"},
                [
                    {"formula": "C10H16O3", "mass": 184.11},  # NULL charge - kept
                    {"formula": "C9H14O4", "mass": 186.09, "charge": 0},  # kept
                    {"formula": "C5H14NO", "mass": 104.11, "charge": 1},  # dropped
                ],
            ),
        ],
    )
    result = await _known(db_path)
    assert {k.formula for k in result} == {"C10H16O3", "C9H14O4"}


@pytest.mark.asyncio
async def test_atmospheric_element_bound(sync_engine, db_path):
    _seed(
        sync_engine,
        [
            (
                {"name": "s"},
                [
                    {"formula": "C10H16O3", "mass": 184.11},  # CHO - kept
                    {
                        "formula": "C8HF15O2",
                        "mass": 413.97,
                    },  # has F - dropped by default
                ],
            )
        ],
    )
    default = {k.formula for k in await _known(db_path)}
    assert default == {"C10H16O3"}
    # Disabling the element filter lets the fluorinated compound through.
    widened = {k.formula for k in await _known(db_path, elements=None)}
    assert widened == {"C10H16O3", "C8HF15O2"}


@pytest.mark.asyncio
async def test_carbon_and_mass_bounds(sync_engine, db_path):
    _seed(
        sync_engine,
        [
            (
                {"name": "s"},
                [
                    {"formula": "C10H16O3", "mass": 184.11},
                    {"formula": "C50H2O2", "mass": 620.0},  # too many carbons
                    {"formula": "C12H10O2", "mass": 800.0},  # too heavy
                ],
            )
        ],
    )
    kept = {k.formula for k in await _known(db_path, max_carbon=40, max_mass=700.0)}
    assert kept == {"C10H16O3"}
    loosened = {
        k.formula for k in await _known(db_path, max_carbon=None, max_mass=None)
    }
    assert loosened == {"C10H16O3", "C50H2O2", "C12H10O2"}


@pytest.mark.asyncio
async def test_license_filter_and_active_only(sync_engine, db_path):
    _seed(
        sync_engine,
        [
            (
                {"name": "public", "license": "public-domain"},
                [
                    {"formula": "C10H16O3", "mass": 184.11, "license": "public-domain"},
                ],
            ),
            (
                {"name": "custom", "license": "custom"},
                [
                    {"formula": "C9H14O4", "mass": 186.09, "license": "custom"},
                ],
            ),
            (
                {"name": "stale", "is_active": False},
                [
                    {"formula": "C8H12O4", "mass": 172.07},
                ],
            ),
        ],
    )
    # Inactive source never appears.
    all_active = {k.formula for k in await _known(db_path)}
    assert all_active == {"C10H16O3", "C9H14O4"}
    # License filter keeps only public-domain.
    public = {k.formula for k in await _known(db_path, licenses={"public-domain"})}
    assert public == {"C10H16O3"}


@pytest.mark.asyncio
async def test_identity_cap(sync_engine, db_path):
    _seed(
        sync_engine,
        [
            (
                {"name": "s"},
                [
                    {
                        "formula": "C10H16O3",
                        "mass": 184.11,
                        "name": f"iso-{i}",
                        "id": f"x{i}",
                    }
                    for i in range(10)
                ],
            )
        ],
    )
    (comp,) = await _known(db_path, max_identities=3)
    assert comp.formula == "C10H16O3"
    assert len(comp.identities) == 3
