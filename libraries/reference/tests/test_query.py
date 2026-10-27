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
    SOURCE_PAGE_CAP,
    STRUCTURE_PAGE_CAP,
    active_sources,
    annotate_formulas,
    by_formula,
    by_mass_window,
    source_compounds,
    structures_by_formula,
    xref_cas_number,
)
from mascope_reference.schema import (  # noqa: E402
    reference_compound,
    reference_source,
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


# -- structures_by_formula: the compound editor's Find structure ---------------

GLUCOSE = "WQZGKKKJIJFFOK-GASJEMHNSA-N"
FRUCTOSE = "BJHIKXHVCXFQLS-UYFOZJQFSA-N"
GALACTOSE = "WQZGKKKJIJFFOK-SVZMEOIVSA-N"
MANNOSE = "WQZGKKKJIJFFOK-QTVWNMPRSA-N"


def _source(conn, name: str, *, active: bool = True, license: str = "CC-BY-4.0"):
    """Insert a reference source; its id."""
    return conn.execute(
        reference_source.insert()
        .values(name=name, version="v1", license=license, is_active=active)
        .returning(reference_source.c.reference_source_id)
    ).scalar_one()


def _compound(conn, source_id: int, native_id: str, **values):
    """Insert a C6H12O6 reference row unless ``values`` says otherwise."""
    conn.execute(
        reference_compound.insert().values(
            reference_source_id=source_id,
            formula=values.pop("formula", "C6H12O6"),
            source_native_id=native_id,
            license=values.pop("license", "CC-BY-4.0"),
            xrefs=values.pop("xrefs", {}),
            **values,
        )
    )


def _seed_hexoses(sync_engine) -> None:
    """Hexoses in two active sources and one inactive load, and a hexose row
    with no structure at all."""
    with sync_engine.begin() as conn:
        comptox = _source(conn, "comptox")
        custom = _source(conn, "custom", license="custom")
        stale = _source(conn, "comptox-old", active=False)
        _compound(
            conn,
            comptox,
            "DTXSID1",
            name="Fructose",
            inchikey=FRUCTOSE,
            xrefs={"casrn": "57-48-7"},
        )
        _compound(
            conn,
            comptox,
            "DTXSID2",
            name="D-Glucose",
            inchikey=GLUCOSE,
            inchi="InChI=1S/C6H12O6/c7-1-2-3(8)4(9)5(10)6(11)12-2/h2-11H,1H2",
            smiles="OCC1OC(O)C(O)C(O)C1O",
            xrefs={"casrn": "50-99-7"},
        )
        # Hand-typed: undashed, with blanks around it.
        _compound(
            conn,
            custom,
            "A1",
            name="Galactose",
            inchikey=GALACTOSE,
            license="CC0-1.0",
            xrefs={"cas": " 59234 "},
        )
        _compound(conn, custom, "A2", name="Mannose", smiles="OCC1OC(O)C(O)C1O")
        _compound(conn, custom, "A3", name="Hexose, unnamed")
        _compound(
            conn,
            custom,
            "A4",
            name="Sorbose",
            inchi="InChI=1S/C6H12O6/c7-1-3-4(9)5(10)6(11,2-8)12-3",
        )
        _compound(
            conn,
            stale,
            "DTXSID9",
            name="Glucose (old load)",
            inchikey=MANNOSE,
            xrefs={"casrn": "50-99-7"},
        )
        _compound(conn, comptox, "DTXSID3", formula="H2O", inchikey="X" * 27)


async def _structures(db_path, formula: str, **kwargs):
    maker, engine = _sessionmaker(db_path)
    try:
        async with maker() as session:
            return await structures_by_formula(session, formula, **kwargs)
    finally:
        await engine.dispose()


def _ids(page) -> list[str]:
    return [c.record.source_native_id for c in page.candidates]


@pytest.mark.asyncio
async def test_structures_are_the_active_rows_of_the_formula_with_a_structure(
    sync_engine, db_path
):
    _seed_hexoses(sync_engine)

    page = await _structures(db_path, "C6H12O6")

    # Not the inactive load's row, not the row without a structure, not water;
    # a key, a SMILES or an InChI alone is a structure.
    assert page.total == 5
    assert sorted(_ids(page)) == ["A1", "A2", "A4", "DTXSID1", "DTXSID2"]
    assert page.formula == "C6H12O6"


@pytest.mark.asyncio
async def test_structures_normalise_the_formula_asked_about(sync_engine, db_path):
    _seed_hexoses(sync_engine)

    page = await _structures(db_path, "HOCH2(CHOH)4CHO")

    assert page.formula == "C6H12O6"
    assert page.total == 5


@pytest.mark.asyncio
async def test_structures_carry_source_and_licence_per_row(sync_engine, db_path):
    _seed_hexoses(sync_engine)

    page = await _structures(db_path, "C6H12O6")

    by_id = {c.record.source_native_id: c.record for c in page.candidates}
    assert (by_id["DTXSID2"].source, by_id["DTXSID2"].license) == (
        "comptox",
        "CC-BY-4.0",
    )
    assert (by_id["A1"].source, by_id["A1"].license) == ("custom", "CC0-1.0")
    assert by_id["DTXSID2"].inchi.startswith("InChI=1S/C6H12O6/")
    assert by_id["DTXSID2"].smiles == "OCC1OC(O)C(O)C(O)C1O"


@pytest.mark.asyncio
async def test_structures_put_the_cas_match_first_then_the_name_match(
    sync_engine, db_path
):
    _seed_hexoses(sync_engine)

    # Asked with glucose's CAS number undashed and galactose's name in
    # another case: the CAS match leads, the name match follows, then the
    # stable order (source, then its id).
    page = await _structures(db_path, "C6H12O6", cas_number="50997", name=" GALACTOSE ")

    assert _ids(page) == ["DTXSID2", "A1", "DTXSID1", "A2", "A4"]
    assert [(c.cas_match, c.name_match) for c in page.candidates] == [
        (True, False),
        (False, True),
        (False, False),
        (False, False),
        (False, False),
    ]


@pytest.mark.asyncio
async def test_structures_match_a_cas_number_under_either_key_dashes_aside(
    sync_engine, db_path
):
    _seed_hexoses(sync_engine)

    # Galactose's custom row records its number undashed under "cas", with
    # blanks around it.
    page = await _structures(db_path, "C6H12O6", cas_number="59-23-4")

    assert _ids(page)[0] == "A1"
    assert page.candidates[0].cas_match


@pytest.mark.asyncio
async def test_structures_without_cas_or_name_keep_the_stable_order(
    sync_engine, db_path
):
    _seed_hexoses(sync_engine)

    page = await _structures(db_path, "C6H12O6")

    assert _ids(page) == ["DTXSID1", "DTXSID2", "A1", "A2", "A4"]
    assert not any(c.cas_match or c.name_match for c in page.candidates)


@pytest.mark.asyncio
async def test_structures_page_through_every_row_once(sync_engine, db_path):
    _seed_hexoses(sync_engine)

    pages = [
        await _structures(
            db_path, "C6H12O6", cas_number="50-99-7", limit=3, offset=offset
        )
        for offset in (0, 3)
    ]

    assert [page.total for page in pages] == [5, 5]
    assert _ids(pages[0]) + _ids(pages[1]) == [
        "DTXSID2",
        "DTXSID1",
        "A1",
        "A2",
        "A4",
    ]


@pytest.mark.asyncio
async def test_structures_of_a_formula_no_row_has_are_none(sync_engine, db_path):
    _seed_hexoses(sync_engine)

    page = await _structures(db_path, "C7H14O6")

    assert (page.candidates, page.total, page.formula) == ([], 0, "C7H14O6")


@pytest.mark.asyncio
async def test_structures_of_what_is_no_formula_are_none(sync_engine, db_path):
    _seed_hexoses(sync_engine)

    page = await _structures(db_path, "Not available")

    assert (page.candidates, page.total, page.formula) == ([], 0, None)


@pytest.mark.parametrize("limit", [0, STRUCTURE_PAGE_CAP + 1])
@pytest.mark.asyncio
async def test_structures_refuse_a_page_size_past_the_cap(sync_engine, db_path, limit):
    with pytest.raises(ValueError, match="limit"):
        await _structures(db_path, "C6H12O6", limit=limit)


@pytest.mark.asyncio
async def test_structures_refuse_a_negative_offset(sync_engine, db_path):
    with pytest.raises(ValueError, match="offset"):
        await _structures(db_path, "C6H12O6", offset=-1)


def test_xref_cas_number_reads_comptox_then_custom_keys():
    assert xref_cas_number({"casrn": " 50-99-7 ", "cas": "1-1-1"}) == "50-99-7"
    assert xref_cas_number({"casrn": "", "cas": "50997"}) == "50997"
    assert xref_cas_number({"dtxsid": "DTXSID1"}) is None
    assert xref_cas_number(None) is None


def _seed_sources(sync_engine) -> None:
    """An active and a replaced version of one list, and another active list."""
    with sync_engine.begin() as conn:
        old = conn.execute(
            reference_source.insert()
            .values(name="hom", version="1", license="CC-BY-4.0", is_active=False)
            .returning(reference_source.c.reference_source_id)
        ).scalar_one()
        new = conn.execute(
            reference_source.insert()
            .values(
                name="hom",
                version="2",
                license="CC-BY-4.0",
                record_count=3,
                is_active=True,
                known_window={"c_max": 20},
                allow_radicals=True,
                polarity="negative",
            )
            .returning(reference_source.c.reference_source_id)
        ).scalar_one()
        _source(conn, "amines", license="CC0")
        _compound(conn, old, "old-1", formula="C10H16O3", monoisotopic_mass=184.1)
        for native_id, formula, mass in (
            ("b", "C10H16O4", 200.1),
            ("a", "C10H16O3", 184.1),
            ("c", "C10H16O5", 216.1),
        ):
            _compound(
                conn,
                new,
                native_id,
                formula=formula,
                monoisotopic_mass=mass,
                xrefs={"reference": "10.1000/x", "tags": ["hom"]},
            )


async def _read(db_path, fn, *args, **kwargs):
    maker, engine = _sessionmaker(db_path)
    try:
        async with maker() as session:
            return await fn(session, *args, **kwargs)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_active_sources_are_the_active_versions_by_name(sync_engine, db_path):
    _seed_sources(sync_engine)
    rows = await _read(db_path, active_sources)
    assert [(row.name, row.version) for row in rows] == [("amines", "v1"), ("hom", "2")]
    hom = rows[1]
    assert hom.license == "CC-BY-4.0"
    assert hom.record_count == 3
    assert hom.known_window == {"c_max": 20}
    assert hom.allow_radicals is True
    assert hom.polarity == "negative"


@pytest.mark.asyncio
async def test_source_compounds_read_the_active_version_by_mass(sync_engine, db_path):
    _seed_sources(sync_engine)
    page = await _read(db_path, source_compounds, "hom")
    assert page.version == "2"
    assert page.total == 3
    assert [r.source_native_id for r in page.records] == ["a", "b", "c"]
    assert page.records[0].xrefs == {"reference": "10.1000/x", "tags": ["hom"]}
    assert page.records[0].source == "hom"


@pytest.mark.asyncio
async def test_source_compounds_read_a_named_version_even_inactive(
    sync_engine, db_path
):
    _seed_sources(sync_engine)
    page = await _read(db_path, source_compounds, "hom", version="1")
    assert page.version == "1"
    assert page.total == 1
    assert [r.source_native_id for r in page.records] == ["old-1"]


@pytest.mark.asyncio
async def test_source_compounds_page_through_every_row_once(sync_engine, db_path):
    _seed_sources(sync_engine)
    first = await _read(db_path, source_compounds, "hom", limit=2)
    second = await _read(db_path, source_compounds, "hom", limit=2, offset=2)
    assert [r.source_native_id for r in first.records] == ["a", "b"]
    assert [r.source_native_id for r in second.records] == ["c"]
    assert first.total == second.total == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name, version", [("nothing", None), ("hom", "9"), ("nothing", "1")]
)
async def test_source_compounds_of_no_such_version_are_none(
    sync_engine, db_path, name, version
):
    _seed_sources(sync_engine)
    assert await _read(db_path, source_compounds, name, version=version) is None


@pytest.mark.asyncio
async def test_source_compounds_of_a_source_with_no_active_version_are_none(
    sync_engine, db_path
):
    with sync_engine.begin() as conn:
        _source(conn, "retired", active=False)
    assert await _read(db_path, source_compounds, "retired") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("limit, offset", [(0, 0), (SOURCE_PAGE_CAP + 1, 0), (1, -1)])
async def test_source_compounds_refuse_a_page_out_of_range(
    sync_engine, db_path, limit, offset
):
    with pytest.raises(ValueError):
        await _read(db_path, source_compounds, "hom", limit=limit, offset=offset)
