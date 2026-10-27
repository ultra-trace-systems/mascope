"""The spectra read path, over the three fixtures loaded as a scorer would find them.

Requires aiosqlite, like the compound read path's tests. The fixtures are
loaded through the real ingest, so these tests answer the brief's question end
to end: a few spectra per library load, are filtered by licence, and answer
``by_inchikey`` and ``by_precursor``.
"""

from pathlib import Path

import pytest


pytest.importorskip("aiosqlite")

from sqlalchemy.ext.asyncio import (  # noqa: E402
    async_sessionmaker,
    create_async_engine,
)

from mascope_reference import by_inchikey, by_precursor  # noqa: E402
from mascope_reference.adapters.gnps import GnpsAdapter  # noqa: E402
from mascope_reference.adapters.massbank import MassBankAdapter  # noqa: E402
from mascope_reference.adapters.mona import MonaAdapter  # noqa: E402
from mascope_reference.ingest import ingest_spectra  # noqa: E402
from mascope_reference.spectrum import NCE  # noqa: E402


FIXTURES = Path(__file__).parent / "fixtures" / "msms"
CAFFEINE = "RYYVLZVUVIJVGH-UHFFFAOYSA-N"
CAFFEINE_MH = 195.0877


@pytest.fixture
def library(sync_engine, db_path):
    """The three fixtures, each its library's active version."""
    ingest_spectra(
        sync_engine, MassBankAdapter(), FIXTURES / "massbank_records.txt", "1"
    )
    ingest_spectra(sync_engine, MonaAdapter(), FIXTURES / "mona.msp", "1")
    ingest_spectra(sync_engine, GnpsAdapter(), FIXTURES / "gnps.mgf", "1")
    return db_path


async def _ask(db_path: Path, query, *args, **kwargs):
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    try:
        async with async_sessionmaker(engine)() as session:
            return await query(session, *args, **kwargs)
    finally:
        await engine.dispose()


def _ids(spectra) -> list[tuple[str, str]]:
    return [(s.source, s.source_native_id) for s in spectra]


@pytest.mark.asyncio
async def test_by_inchikey_finds_the_structure_in_every_library(library):
    spectra = await _ask(library, by_inchikey, CAFFEINE)
    assert _ids(spectra) == [
        ("gnps", "CCMSLIBTEST0001"),
        ("massbank", "MSBNK-TEST-TS000001"),
    ]
    massbank = spectra[1]
    assert massbank.name == "Caffeine"
    assert massbank.formula == "C8H10N4O2"
    assert massbank.precursor_mz == CAFFEINE_MH
    assert massbank.adduct == "[M+H]+"
    assert massbank.polarity == "positive"
    assert (massbank.collision_energy, massbank.collision_energy_kind) == (35.0, NCE)
    assert massbank.peaks[0] == (83.0604, 12000.0)
    assert massbank.license == "CC-BY"


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["RYYVLZVUVIJVGH", "not a key", ""])
async def test_by_inchikey_matches_nothing_on_what_is_not_a_key(library, key):
    assert await _ask(library, by_inchikey, key) == []


@pytest.mark.asyncio
async def test_by_inchikey_reads_a_prefixed_key(library):
    spectra = await _ask(library, by_inchikey, f"InChIKey={CAFFEINE}")
    assert len(spectra) == 2


@pytest.mark.asyncio
async def test_spectra_come_in_library_order_whatever_their_ids(sync_engine, db_path):
    # Named so that the library order and the id order disagree.
    ingest_spectra(
        sync_engine,
        MassBankAdapter(),
        FIXTURES / "massbank_records.txt",
        "1",
        source_name="a-massbank",
    )
    ingest_spectra(sync_engine, GnpsAdapter(), FIXTURES / "gnps.mgf", "1")
    expected = [("a-massbank", "MSBNK-TEST-TS000001"), ("gnps", "CCMSLIBTEST0001")]
    assert _ids(await _ask(db_path, by_inchikey, CAFFEINE)) == expected
    assert _ids(await _ask(db_path, by_precursor, CAFFEINE_MH, 5)) == expected


@pytest.mark.asyncio
async def test_by_precursor_finds_spectra_within_the_window(library):
    spectra = await _ask(library, by_precursor, CAFFEINE_MH, 5)
    assert _ids(spectra) == [
        ("gnps", "CCMSLIBTEST0001"),
        ("massbank", "MSBNK-TEST-TS000001"),
    ]
    # 195.0877 at 1 ppm is +-0.0002: 195.0880 is outside.
    assert await _ask(library, by_precursor, 195.0880, 1) == []


@pytest.mark.asyncio
async def test_by_precursor_orders_by_precursor_then_library(library):
    spectra = await _ask(library, by_precursor, 200.0, 200_000)
    assert [s.precursor_mz for s in spectra] == sorted(s.precursor_mz for s in spectra)
    assert _ids(spectra) == [
        ("gnps", "CCMSLIBTEST0002"),
        ("gnps", "CCMSLIBTEST0001"),
        ("massbank", "MSBNK-TEST-TS000001"),
        ("mona", "TEST000101"),
        ("massbank", "MSBNK-TEST-TS000002"),
    ]


@pytest.mark.asyncio
async def test_the_read_path_keeps_only_the_licences_asked_for(library):
    everything = await _ask(library, by_precursor, 300.0, 1_000_000)
    assert {s.license for s in everything} == {
        "CC-BY",
        "CC-BY-NC-SA",
        "CC0",
        "gnps-unstated",
        "mona-unstated",
    }
    # A deployment that may not match non-commercial or unstated terms.
    allowed = await _ask(
        library, by_precursor, 300.0, 1_000_000, licenses={"CC0", "CC-BY"}
    )
    assert _ids(allowed) == [
        ("gnps", "CCMSLIBTEST0001"),
        ("massbank", "MSBNK-TEST-TS000001"),
        ("mona", "TEST000101"),
    ]
    by_key = await _ask(library, by_inchikey, CAFFEINE, licenses=["CC-BY"])
    assert _ids(by_key) == [("massbank", "MSBNK-TEST-TS000001")]
    assert await _ask(library, by_inchikey, CAFFEINE, licenses=[]) == []


@pytest.mark.asyncio
async def test_the_read_path_keeps_one_polarity_when_asked(library):
    negative = await _ask(library, by_precursor, 300.0, 1_000_000, polarity="negative")
    assert {s.polarity for s in negative} == {"negative"}
    assert _ids(negative) == [
        ("gnps", "CCMSLIBTEST0002"),
        ("massbank", "MSBNK-TEST-TS000002"),
        ("mona", "TEST000102"),
    ]
    assert await _ask(library, by_inchikey, CAFFEINE, polarity="negative") == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query, args", [(by_inchikey, (CAFFEINE,)), (by_precursor, (195.0, 5))]
)
async def test_a_polarity_no_spectrum_records_is_refused(library, query, args):
    with pytest.raises(ValueError, match="polarity"):
        await _ask(library, query, *args, polarity="+")


@pytest.mark.asyncio
async def test_only_the_active_version_of_a_library_is_read(sync_engine, library):
    ingest_spectra(
        sync_engine, MassBankAdapter(), FIXTURES / "massbank_records.txt", "2"
    )
    spectra = await _ask(library, by_inchikey, CAFFEINE)
    assert _ids(spectra) == [
        ("gnps", "CCMSLIBTEST0001"),
        ("massbank", "MSBNK-TEST-TS000001"),
    ]
    staged = ingest_spectra(
        sync_engine, GnpsAdapter(), FIXTURES / "gnps.mgf", "2", activate=False
    )
    assert staged.ingested == 2
    assert len(await _ask(library, by_inchikey, CAFFEINE)) == 2
