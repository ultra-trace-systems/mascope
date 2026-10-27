"""Versioned loads of the spectral libraries, against a SQLite mirror."""

from pathlib import Path

import pytest
from sqlalchemy import func, select

from mascope_reference.adapters.gnps import GnpsAdapter
from mascope_reference.adapters.massbank import MassBankAdapter
from mascope_reference.adapters.mona import MonaAdapter
from mascope_reference.adapters.pubchem import PubChemAdapter
from mascope_reference.ingest import (
    EmptyIngest,
    SourceKindConflict,
    ingest,
    ingest_spectra,
)
from mascope_reference.schema import (
    reference_compound,
    reference_source,
    reference_spectrum,
)
from mascope_reference.spectrum import (
    DEPRECATED,
    LIBRARY_CLASS,
    NO_PRECURSOR,
    NOT_MS2,
    PREDICTED,
)


FIXTURES = Path(__file__).parent / "fixtures" / "msms"
MASSBANK = FIXTURES / "massbank_records.txt"
MONA = FIXTURES / "mona.msp"
GNPS = FIXTURES / "gnps.mgf"

SDF = """> <PUBCHEM_COMPOUND_CID>
2519

> <PUBCHEM_MOLECULAR_FORMULA>
C8H10N4O2

$$$$
"""


def _sources(engine) -> list[tuple]:
    with engine.connect() as conn:
        return conn.execute(
            select(
                reference_source.c.name,
                reference_source.c.version,
                reference_source.c.is_active,
                reference_source.c.kind,
                reference_source.c.record_count,
                reference_source.c.license,
            ).order_by(reference_source.c.reference_source_id)
        ).all()


def _spectra(engine) -> list:
    with engine.connect() as conn:
        return conn.execute(
            select(reference_spectrum).order_by(
                reference_spectrum.c.reference_spectrum_id
            )
        ).all()


# The source row's licence is what `sources` and `status` show for a load. The
# spectra each carry their own, so the row claims no terms: a GNPS load holds
# imported libraries' spectra whose terms are unknown.
@pytest.mark.parametrize(
    "adapter, path, ingested, skipped",
    [
        (MassBankAdapter(), MASSBANK, 2, {NOT_MS2: 1, DEPRECATED: 1}),
        (MonaAdapter(), MONA, 2, {PREDICTED: 1, NOT_MS2: 1}),
        (GnpsAdapter(), GNPS, 2, {LIBRARY_CLASS: 1, NO_PRECURSOR: 1, NOT_MS2: 1}),
    ],
)
def test_each_library_loads_and_says_what_it_left_out(
    sync_engine, adapter, path, ingested, skipped
):
    result = ingest_spectra(sync_engine, adapter, path, version="2026.03")
    assert result.source == adapter.name
    assert result.ingested == ingested
    assert result.skipped == skipped
    assert result.skipped_total == sum(skipped.values())
    assert _sources(sync_engine) == [
        (
            adapter.name,
            "2026.03",
            True,
            "spectrum",
            ingested,
            f"{adapter.name}-unstated",
        )
    ]
    assert len(_spectra(sync_engine)) == ingested


def test_a_stored_spectrum_carries_its_record(sync_engine):
    result = ingest_spectra(sync_engine, MassBankAdapter(), MASSBANK, version="1")
    caffeine, bisphenol = _spectra(sync_engine)
    assert caffeine.reference_source_id == result.reference_source_id
    assert caffeine.source_native_id == "MSBNK-TEST-TS000001"
    assert caffeine.name == "Caffeine"
    assert caffeine.formula == "C8H10N4O2"
    assert caffeine.inchikey == "RYYVLZVUVIJVGH-UHFFFAOYSA-N"
    assert caffeine.smiles == "CN1C=NC2=C1C(=O)N(C(=O)N2C)C"
    assert caffeine.inchi.startswith("InChI=1S/C8H10N4O2")
    assert caffeine.precursor_mz == 195.0877
    assert caffeine.adduct == "[M+H]+"
    assert caffeine.polarity == "positive"
    assert caffeine.collision_energy == 35.0
    assert caffeine.collision_energy_kind == "nce"
    assert caffeine.collision_energy_text == "35(NCE)"
    # Ascending in m/z, as pairs.
    assert caffeine.peaks == [
        [83.0604, 12000.0],
        [110.0713, 30000.0],
        [138.0662, 199800.0],
        [195.0877, 80000.0],
    ]
    assert caffeine.license == "CC-BY"
    assert (bisphenol.collision_energy, bisphenol.collision_energy_kind) == (
        20.0,
        "ev",
    )
    assert bisphenol.license == "CC-BY-NC-SA"


def test_bounded_columns_are_fitted_rather_than_failing_the_load(sync_engine, tmp_path):
    path = tmp_path / "long.mgf"
    path.write_text(
        "BEGIN IONS\nPEPMASS=300.1\nMSLEVEL=2\nIONMODE=Positive\nNAME=A M+H\n"
        f"SPECTRUMID={'X' * 300}\n100.0 1.0\nEND IONS\n",
        encoding="utf-8",
    )
    ingest_spectra(sync_engine, GnpsAdapter(), path, version="1")
    (row,) = _spectra(sync_engine)
    assert len(row.source_native_id) == 128


def test_a_reload_deactivates_the_previous_version(sync_engine):
    ingest_spectra(sync_engine, MonaAdapter(), MONA, version="v1")
    ingest_spectra(sync_engine, MonaAdapter(), MONA, version="v2")
    assert [
        (name, version, active) for name, version, active, *_ in _sources(sync_engine)
    ] == [
        ("mona", "v1", False),
        ("mona", "v2", True),
    ]
    # The earlier load's spectra stay, for reproducibility.
    assert len(_spectra(sync_engine)) == 4


def test_prune_deletes_the_earlier_loads_spectra_and_no_one_elses(sync_engine):
    ingest_spectra(sync_engine, MonaAdapter(), MONA, version="v1")
    ingest_spectra(sync_engine, GnpsAdapter(), GNPS, version="v1")
    ingest_spectra(sync_engine, MonaAdapter(), MONA, version="v2", prune=True)
    assert [(name, version) for name, version, *_ in _sources(sync_engine)] == [
        ("gnps", "v1"),
        ("mona", "v2"),
    ]
    with sync_engine.connect() as conn:
        held = dict(
            conn.execute(
                select(reference_source.c.name, func.count())
                .select_from(
                    reference_spectrum.join(
                        reference_source,
                        reference_spectrum.c.reference_source_id
                        == reference_source.c.reference_source_id,
                    )
                )
                .group_by(reference_source.c.name)
            ).all()
        )
        orphans = conn.execute(
            select(func.count())
            .select_from(reference_spectrum)
            .where(
                reference_spectrum.c.reference_source_id.not_in(
                    select(reference_source.c.reference_source_id)
                )
            )
        ).scalar()
    assert held == {"gnps": 2, "mona": 2}
    assert orphans == 0


def test_a_staged_load_does_not_replace_the_active_one(sync_engine):
    ingest_spectra(sync_engine, MonaAdapter(), MONA, version="v1")
    ingest_spectra(sync_engine, MonaAdapter(), MONA, version="v2", activate=False)
    assert [(version, active) for _, version, active, *_ in _sources(sync_engine)] == [
        ("v1", True),
        ("v2", False),
    ]


def test_a_staged_load_prunes_nothing(sync_engine):
    # Pruning follows activation: a staged load replaces nothing, so the load it
    # would have replaced is not deleted either.
    ingest_spectra(sync_engine, MonaAdapter(), MONA, version="v1")
    ingest_spectra(
        sync_engine, MonaAdapter(), MONA, version="v2", activate=False, prune=True
    )
    assert [(version, active) for _, version, active, *_ in _sources(sync_engine)] == [
        ("v1", True),
        ("v2", False),
    ]
    assert len(_spectra(sync_engine)) == 4


def test_two_exports_of_one_library_load_side_by_side_under_names_of_their_own(
    sync_engine,
):
    ingest_spectra(sync_engine, MonaAdapter(), MONA, version="1", source_name="mona-a")
    ingest_spectra(sync_engine, MonaAdapter(), MONA, version="1", source_name="mona-b")
    assert [(name, active) for name, _, active, *_ in _sources(sync_engine)] == [
        ("mona-a", True),
        ("mona-b", True),
    ]


def test_a_dump_that_yields_no_spectrum_leaves_the_active_version_alone(
    sync_engine, tmp_path
):
    ingest_spectra(sync_engine, MonaAdapter(), MONA, version="v1")
    wrong = tmp_path / "not-mona.msp"
    # A GC-MS-only export: every record read, none a spectrum to store.
    wrong.write_text(MONA.read_text().split("\n\n")[-1], encoding="utf-8")
    with pytest.raises(EmptyIngest, match="1 not MS2"):
        ingest_spectra(sync_engine, MonaAdapter(), wrong, version="v2")
    assert [(version, active) for _, version, active, *_ in _sources(sync_engine)] == [
        ("v1", True)
    ]
    assert len(_spectra(sync_engine)) == 2


def test_an_empty_file_says_nothing_was_read(sync_engine, tmp_path):
    empty = tmp_path / "empty.mgf"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(EmptyIngest, match="no records read"):
        ingest_spectra(sync_engine, GnpsAdapter(), empty, version="v1")
    assert _sources(sync_engine) == []


def test_a_missing_dump_is_refused(sync_engine, tmp_path):
    with pytest.raises(FileNotFoundError):
        ingest_spectra(sync_engine, GnpsAdapter(), tmp_path / "nope.mgf", version="1")


def test_a_compound_load_records_its_kind(sync_engine, tmp_path):
    path = tmp_path / "pubchem.sdf"
    path.write_text(SDF, encoding="utf-8")
    ingest(sync_engine, PubChemAdapter(), path, version="1")
    ((*_, kind, _count, _license),) = _sources(sync_engine)
    assert kind == "compound"


def test_a_library_cannot_load_under_a_compound_sources_name(sync_engine, tmp_path):
    # MoNA's HMDB library under the name of the HMDB compound mirror would take
    # that mirror out of annotation and Stage A.
    path = tmp_path / "pubchem.sdf"
    path.write_text(SDF, encoding="utf-8")
    ingest(sync_engine, PubChemAdapter(), path, version="1", source_name="hmdb")
    with pytest.raises(SourceKindConflict, match="'hmdb' holds compounds"):
        ingest_spectra(
            sync_engine, MonaAdapter(), MONA, version="1", source_name="hmdb"
        )
    assert [
        (name, active, kind) for name, _, active, kind, *_ in _sources(sync_engine)
    ] == [("hmdb", True, "compound")]
    assert _spectra(sync_engine) == []


def test_a_compound_source_cannot_load_under_a_librarys_name(sync_engine, tmp_path):
    ingest_spectra(sync_engine, MassBankAdapter(), MASSBANK, version="1")
    path = tmp_path / "pubchem.sdf"
    path.write_text(SDF, encoding="utf-8")
    with pytest.raises(SourceKindConflict, match="'massbank' holds spectra"):
        ingest(sync_engine, PubChemAdapter(), path, version="1", source_name="massbank")
    assert [
        (name, active, kind) for name, _, active, kind, *_ in _sources(sync_engine)
    ] == [("massbank", True, "spectrum")]
    with sync_engine.connect() as conn:
        assert (
            conn.execute(select(func.count()).select_from(reference_compound)).scalar()
            == 0
        )


def test_a_source_row_without_a_kind_is_a_compound_source(sync_engine, tmp_path):
    # Rows written before sources had a kind, or by a schema without one.
    path = tmp_path / "pubchem.sdf"
    path.write_text(SDF, encoding="utf-8")
    ingest(sync_engine, PubChemAdapter(), path, version="1", source_name="old")
    with sync_engine.begin() as conn:
        conn.execute(reference_source.update().values(kind=None))
    with pytest.raises(SourceKindConflict):
        ingest_spectra(sync_engine, MonaAdapter(), MONA, version="1", source_name="old")
    ingest(sync_engine, PubChemAdapter(), path, version="2", source_name="old")


def _citations(engine) -> list:
    with engine.connect() as conn:
        return (
            conn.execute(
                select(reference_source.c.citations).order_by(
                    reference_source.c.reference_source_id
                )
            )
            .scalars()
            .all()
        )


def test_a_library_records_the_publications_it_is_given(sync_engine):
    cited = [{"citation": "A. Author, A library.", "doi": "10.1234/lib"}]
    ingest_spectra(sync_engine, MonaAdapter(), MONA, version="v1", citations=cited)
    ingest_spectra(sync_engine, MonaAdapter(), MONA, version="v2")
    # A library's dump names no publication, so a load given none records an
    # empty list: it cites nothing, where NULL would say nobody recorded it.
    assert _citations(sync_engine) == [cited, []]
