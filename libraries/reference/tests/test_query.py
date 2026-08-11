"""Async query-path tests over a SQLite mirror.

Requires aiosqlite; skipped where it is not installed. Ingest runs through the
synchronous engine, then the async query interface reads the same database
file, exercising the active-version filtering that keeps a re-synced source
from doubling results.
"""

from pathlib import Path

import pytest


pytest.importorskip("aiosqlite")

from sqlalchemy.ext.asyncio import (  # noqa: E402
    async_sessionmaker,
    create_async_engine,
)

from mascope_reference.adapters.pubchem import PubChemAdapter  # noqa: E402
from mascope_reference.ingest import ingest  # noqa: E402
from mascope_reference.query import (  # noqa: E402
    annotate_formulas,
    by_formula,
    by_mass_window,
)


SDF_TWO = """> <PUBCHEM_COMPOUND_CID>
962

> <PUBCHEM_MOLECULAR_FORMULA>
H2O

> <PUBCHEM_IUPAC_INCHIKEY>
XLYOFNOQVPJJNP-UHFFFAOYSA-N

$$$$
> <PUBCHEM_COMPOUND_CID>
2244

> <PUBCHEM_MOLECULAR_FORMULA>
C9H8O4

> <PUBCHEM_IUPAC_INCHIKEY>
BSYNRYMUTXBXSQ-UHFFFAOYSA-N

$$$$
"""


def _seed(sync_engine, tmp_path: Path, version: str = "v1") -> None:
    path = tmp_path / f"pubchem-{version}.sdf"
    path.write_text(SDF_TWO, encoding="utf-8")
    ingest(sync_engine, PubChemAdapter(), path, version=version)


def _sessionmaker(db_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    return async_sessionmaker(engine), engine


@pytest.mark.asyncio
async def test_by_formula_returns_active_records(sync_engine, db_path, tmp_path):
    _seed(sync_engine, tmp_path)
    maker, engine = _sessionmaker(db_path)
    try:
        async with maker() as session:
            records = await by_formula(session, "H2O")
    finally:
        await engine.dispose()

    assert len(records) == 1
    assert records[0].source == "pubchem"
    assert records[0].source_native_id == "962"
    assert records[0].inchikey == "XLYOFNOQVPJJNP-UHFFFAOYSA-N"


@pytest.mark.asyncio
async def test_by_formula_canonicalizes_query(sync_engine, db_path, tmp_path):
    _seed(sync_engine, tmp_path)
    maker, engine = _sessionmaker(db_path)
    try:
        async with maker() as session:
            # Non-canonical spelling still finds the canonical stored formula.
            records = await by_formula(session, "OH2")
    finally:
        await engine.dispose()
    assert len(records) == 1
    assert records[0].formula == "H2O"


@pytest.mark.asyncio
async def test_by_mass_window(sync_engine, db_path, tmp_path):
    _seed(sync_engine, tmp_path)
    maker, engine = _sessionmaker(db_path)
    try:
        async with maker() as session:
            records = await by_mass_window(session, 18.0106, ppm=10)
    finally:
        await engine.dispose()
    assert [r.formula for r in records] == ["H2O"]


@pytest.mark.asyncio
async def test_reingest_does_not_double_results(sync_engine, db_path, tmp_path):
    _seed(sync_engine, tmp_path, version="v1")
    _seed(sync_engine, tmp_path, version="v2")
    maker, engine = _sessionmaker(db_path)
    try:
        async with maker() as session:
            records = await by_formula(session, "H2O")
    finally:
        await engine.dispose()
    # Only the active (v2) load is visible - one row, not two.
    assert len(records) == 1


@pytest.mark.asyncio
async def test_annotate_formulas_batches(sync_engine, db_path, tmp_path):
    _seed(sync_engine, tmp_path)
    maker, engine = _sessionmaker(db_path)
    try:
        async with maker() as session:
            annotated = await annotate_formulas(session, ["H2O", "C9H8O4", "C99H2"])
    finally:
        await engine.dispose()
    assert len(annotated["H2O"]) == 1
    assert len(annotated["C9H8O4"]) == 1
    # A formula with no match maps to an empty list, not a missing key.
    assert annotated["C99H2"] == []


@pytest.mark.asyncio
async def test_annotate_formulas_does_not_duplicate_repeated_inputs(
    sync_engine, db_path, tmp_path
):
    # The composition search passes one entry per (composition, ionization
    # mechanism), so the same neutral formula arrives once per adduct. Each
    # matching compound must still be listed once.
    _seed(sync_engine, tmp_path)
    maker, engine = _sessionmaker(db_path)
    try:
        async with maker() as session:
            annotated = await annotate_formulas(
                session, ["H2O", "H2O", "H2O", "C9H8O4"]
            )
    finally:
        await engine.dispose()
    assert len(annotated["H2O"]) == 1
    assert len(annotated["C9H8O4"]) == 1


@pytest.mark.asyncio
async def test_annotate_formulas_dedupes_across_spellings_of_one_formula(
    sync_engine, db_path, tmp_path
):
    # Two spellings canonicalize to the same key; each keeps its own bucket and
    # neither accumulates the other's copy.
    _seed(sync_engine, tmp_path)
    maker, engine = _sessionmaker(db_path)
    try:
        async with maker() as session:
            annotated = await annotate_formulas(session, ["H2O", "OH2", "H2O"])
    finally:
        await engine.dispose()
    assert len(annotated["H2O"]) == 1
    assert len(annotated["OH2"]) == 1


@pytest.mark.asyncio
async def test_query_order_is_stable_across_calls(sync_engine, db_path, tmp_path):
    # Identity order feeds de-duplication and the per-formula identity cap, so an
    # unordered query would change which identities an assignment carries between
    # two runs over the same pinned data.
    _seed(sync_engine, tmp_path)
    maker, engine = _sessionmaker(db_path)
    try:
        async with maker() as session:
            first = await by_mass_window(session, 100.0, ppm=2_000_000)
            second = await by_mass_window(session, 100.0, ppm=2_000_000)
    finally:
        await engine.dispose()
    key = [(r.formula, r.source, r.source_native_id) for r in first]
    assert key == [(r.formula, r.source, r.source_native_id) for r in second]
    assert len(key) > 1, "window should span both seeded compounds"
