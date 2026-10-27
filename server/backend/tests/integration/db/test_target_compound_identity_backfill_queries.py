"""
Tests: what the target compound identity backfill reads and writes.

Which compound gets which identity is decided in memory and tested
hermetically. This is the part only a database can show: that the candidates
are the compounds with a CAS number and no InChIKey, InChI or SMILES, that the
reference rows
come from active sources alone and are found by CAS number under either
cross-reference key, digits only, and that a run writes what its plan says -
or nothing, on a dry run - without overwriting any identity field given since.
"""

from datetime import datetime, timezone
from random import randrange
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from mascope_backend.db import (
    ReferenceCompound,
    ReferenceSource,
    TargetCompound,
)
from mascope_backend.db.id import gen_id
from mascope_backend.db.scripts import backfill_target_compound_identity as script
from mascope_reference import canonical_formula, monoisotopic_mass


_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)

GLUCOSE_KEY = "WQZGKKKJIJFFOK-GASJEMHNSA-N"
GLUCOSE_INCHI = (
    "InChI=1S/C6H12O6/c7-1-2-3(8)4(9)5(10)6(11)12-2/h2-11H,1H2/t2-,3-,4+,5-,6+/m1/s1"
)
UREA_KEY = "XSQUKJJJFZCRTK-UHFFFAOYSA-N"
UREA_INCHI = "InChI=1S/CH4N2O/c2-1(3)4/h(H4,2,3,4)"
OTHER_KEY = "AAAAAAAAAAAAAA-BBBBBBBBBB-N"
# What a user may enter without a key: either is an identity the backfill
# must neither replace nor clear.
PARTIAL_IDENTITIES = {
    "smiles": "OCC1OC(O)C(O)C(O)C1O",
    "inchi": "InChI=1S/C6H12O6/c7-1-2-3(8)4(9)5(10)6(11)12-2/h2-11H,1H2",
}


def _cas() -> str:
    """A CAS-shaped number no other test uses, dashed."""
    digits = f"{randrange(10**8):08d}"
    return f"{digits[:-3]}-{digits[-3:-1]}-{digits[-1]}"


def _reference(source_id: int, formula: str, inchikey: str, inchi, xrefs: dict):
    return ReferenceCompound(
        reference_source_id=source_id,
        formula=canonical_formula(formula),
        monoisotopic_mass=monoisotopic_mass(canonical_formula(formula)),
        inchikey=inchikey,
        name=None,
        smiles=None,
        inchi=inchi,
        source_native_id=gen_id(),
        xrefs=xrefs,
        license="CC-BY-4.0",
    )


@pytest_asyncio.fixture
async def seeded(async_session_factory):
    """Glucose under CompTox's key, urea under a custom list's, both active,
    and a row of glucose's CAS number with no InChIKey; an inactive load naming
    another key for glucose's CAS number; and compounds: glucose and urea
    with dashed CAS numbers and no identity, one with an InChIKey already, one
    each with only a SMILES or only an InChI under glucose's CAS number, and
    one with no CAS number."""
    ids = SimpleNamespace(
        glucose_cas=_cas(),
        urea_cas=_cas(),
        glucose=gen_id(),
        urea=gen_id(),
        identified=gen_id(),
        no_cas=gen_id(),
        partial={field: gen_id() for field in PARTIAL_IDENTITIES},
        sources=[],
    )
    async with async_session_factory() as session:
        for name, active in (("comptox-test", True), ("comptox-old", False)):
            source = ReferenceSource(
                name=f"{name}-{gen_id(6)}",
                version="test",
                license="CC-BY-4.0",
                record_count=1,
                is_active=active,
                ingested_at=_NOW,
                known_window={},
            )
            session.add(source)
            await session.flush()
            ids.sources.append(source.reference_source_id)
        active_id, inactive_id = ids.sources
        session.add_all(
            [
                _reference(
                    active_id,
                    "C6H12O6",
                    GLUCOSE_KEY,
                    GLUCOSE_INCHI,
                    {"dtxsid": "x", "casrn": ids.glucose_cas},
                ),
                _reference(
                    active_id,
                    "CH4N2O",
                    UREA_KEY,
                    UREA_INCHI,
                    {"cas": ids.urea_cas.replace("-", "")},
                ),
                _reference(
                    active_id,
                    "C6H12O6",
                    None,
                    None,
                    {"casrn": ids.glucose_cas},
                ),
                _reference(
                    inactive_id,
                    "C6H12O6",
                    OTHER_KEY,
                    None,
                    {"casrn": ids.glucose_cas},
                ),
                TargetCompound(
                    target_compound_id=ids.glucose,
                    target_compound_name=f"Backfill glucose {ids.glucose}",
                    target_compound_formula="C6H12O6",
                    cas_number=ids.glucose_cas,
                ),
                TargetCompound(
                    target_compound_id=ids.urea,
                    target_compound_name=f"Backfill urea {ids.urea}",
                    target_compound_formula="CH4N2O",
                    cas_number=ids.urea_cas,
                ),
                TargetCompound(
                    target_compound_id=ids.identified,
                    target_compound_name=f"Backfill identified {ids.identified}",
                    target_compound_formula="C6H12O6",
                    cas_number=ids.glucose_cas,
                    inchikey=GLUCOSE_KEY,
                ),
                *(
                    TargetCompound(
                        target_compound_id=ids.partial[field],
                        target_compound_name=f"Backfill {field} only {ids.partial[field]}",
                        target_compound_formula="C6H12O6",
                        cas_number=ids.glucose_cas,
                        **{field: value},
                    )
                    for field, value in PARTIAL_IDENTITIES.items()
                ),
                TargetCompound(
                    target_compound_id=ids.no_cas,
                    target_compound_name=f"Backfill no CAS {ids.no_cas}",
                    target_compound_formula="C6H12O6",
                ),
            ]
        )
        await session.commit()

    yield ids

    async with async_session_factory() as session:
        await session.execute(
            delete(TargetCompound).where(
                TargetCompound.target_compound_id.in_(
                    [
                        ids.glucose,
                        ids.urea,
                        ids.identified,
                        ids.no_cas,
                        *ids.partial.values(),
                    ]
                )
            )
        )
        await session.execute(
            delete(ReferenceCompound).where(
                ReferenceCompound.reference_source_id.in_(ids.sources)
            )
        )
        await session.execute(
            delete(ReferenceSource).where(
                ReferenceSource.reference_source_id.in_(ids.sources)
            )
        )
        await session.commit()


async def _identity(async_session_factory, compound_id: str) -> tuple:
    async with async_session_factory() as session:
        compound = await session.scalar(
            select(TargetCompound).where(
                TargetCompound.target_compound_id == compound_id
            )
        )
        return compound.inchikey, compound.inchi


async def _full_identity(async_session_factory, compound_id: str) -> dict:
    async with async_session_factory() as session:
        compound = await session.get(TargetCompound, compound_id)
        return {
            field: getattr(compound, field) for field in ("inchikey", "inchi", "smiles")
        }


@pytest.mark.asyncio
async def test_the_candidates_have_a_cas_number_and_no_identity(seeded):
    offered = {c["target_compound_id"] for c in await script._candidates()}

    assert {seeded.glucose, seeded.urea} <= offered
    assert seeded.identified not in offered
    assert not offered & set(seeded.partial.values())
    assert seeded.no_cas not in offered


@pytest.mark.asyncio
async def test_reference_rows_are_found_by_either_key_in_active_sources_only(seeded):
    """And only rows with an InChIKey: one without has no identity to give."""
    glucose = script.cas_digits(seeded.glucose_cas)
    urea = script.cas_digits(seeded.urea_cas)

    rows = await script._reference_rows({glucose, urea})

    assert sorted((row["inchikey"], row["cas"]) for row in rows) == sorted(
        [(GLUCOSE_KEY, {glucose}), (UREA_KEY, {urea})]
    )


@pytest.mark.asyncio
async def test_a_dry_run_writes_nothing_and_a_run_fills_both(
    seeded, async_session_factory, monkeypatch
):
    # The test session's engine is configured already; run() would build the
    # deployment's own.
    async def configured():
        return None

    monkeypatch.setattr(script, "configure_database_engine", configured)

    await script.run(dry_run=True)
    assert await _identity(async_session_factory, seeded.glucose) == (None, None)

    await script.run(dry_run=False)
    assert await _identity(async_session_factory, seeded.glucose) == (
        GLUCOSE_KEY,
        GLUCOSE_INCHI,
    )
    assert await _identity(async_session_factory, seeded.urea) == (
        UREA_KEY,
        UREA_INCHI,
    )
    # A SMILES or an InChI entered without a key shares glucose's CAS number
    # and formula, and is left exactly as it was.
    for field, value in PARTIAL_IDENTITIES.items():
        assert await _full_identity(async_session_factory, seeded.partial[field]) == {
            "inchikey": None,
            "inchi": None,
            "smiles": None,
            field: value,
        }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [("inchikey", OTHER_KEY), *PARTIAL_IDENTITIES.items()],
)
async def test_an_identity_given_since_the_read_is_not_overwritten(
    seeded, async_session_factory, field, value
):
    async with async_session_factory() as session:
        compound = await session.get(TargetCompound, seeded.urea)
        setattr(compound, field, value)
        await session.commit()

    await script._apply(
        [{"id": seeded.urea, "inchikey": UREA_KEY, "inchi": UREA_INCHI, "smiles": None}]
    )

    assert await _full_identity(async_session_factory, seeded.urea) == {
        "inchikey": None,
        "inchi": None,
        "smiles": None,
        field: value,
    }
