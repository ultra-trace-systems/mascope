"""The three spectral library adapters, each over a small fixture of its format.

The fixtures under ``fixtures/msms`` are written for these tests: real
compounds and their InChIKeys, invented spectra, in each library's layout.
Each holds spectra under more than one licence and records each adapter must
leave out, so what a load keeps and why is pinned per source.
"""

import gzip
import io
import tarfile
from pathlib import Path

import pytest

from mascope_reference.adapters._msms_io import (
    msp_comments,
    peak_pair,
    read_massbank_records,
    read_msp_records,
)
from mascope_reference.adapters.gnps import GnpsAdapter
from mascope_reference.adapters.massbank import MassBankAdapter
from mascope_reference.adapters.mona import MonaAdapter
from mascope_reference.sources import (
    available_sources,
    available_spectrum_sources,
    get_adapter,
    get_spectrum_adapter,
    is_spectrum_source,
)
from mascope_reference.spectrum import (
    DEPRECATED,
    LIBRARY_CLASS,
    NOT_MS2,
    PREDICTED,
    SkippedSpectrum,
    SpectrumRecord,
)


FIXTURES = Path(__file__).parent / "fixtures" / "msms"
MASSBANK = FIXTURES / "massbank_records.txt"
MONA = FIXTURES / "mona.msp"
GNPS = FIXTURES / "gnps.mgf"


def _parse(adapter, path) -> tuple[list[SpectrumRecord], list[str]]:
    kept, skipped = [], []
    for parsed in adapter.parse(path):
        if isinstance(parsed, SkippedSpectrum):
            skipped.append(parsed.reason)
        else:
            kept.append(parsed)
    return kept, skipped


# --- The registry -------------------------------------------------------------


def test_spectral_libraries_are_registered_apart_from_compound_sources():
    assert available_spectrum_sources() == ["gnps", "massbank", "mona"]
    assert not set(available_spectrum_sources()) & set(available_sources())
    assert is_spectrum_source("massbank") and not is_spectrum_source("pubchem")
    assert isinstance(get_spectrum_adapter("mona"), MonaAdapter)


def test_an_unknown_name_says_which_spectral_libraries_there_are():
    with pytest.raises(KeyError, match="spectral libraries: gnps, massbank, mona"):
        get_adapter("massbnk")
    with pytest.raises(KeyError, match="Available: gnps, massbank, mona"):
        get_spectrum_adapter("pubchem")


# --- MassBank -----------------------------------------------------------------


def test_massbank_reads_a_record_file():
    kept, skipped = _parse(MassBankAdapter(), MASSBANK)
    assert [s.source_native_id for s in kept] == [
        "MSBNK-TEST-TS000001",
        "MSBNK-TEST-TS000002",
    ]
    # The MS1 record and the deprecated one.
    assert skipped == [NOT_MS2, DEPRECATED]

    caffeine, bisphenol = kept
    assert caffeine.source == "massbank"
    assert caffeine.name == "Caffeine"
    assert caffeine.formula == "C8H10N4O2"
    assert caffeine.inchikey == "RYYVLZVUVIJVGH-UHFFFAOYSA-N"
    assert caffeine.smiles == "CN1C=NC2=C1C(=O)N(C(=O)N2C)C"
    assert caffeine.inchi.startswith("InChI=1S/C8H10N4O2")
    assert caffeine.precursor_mz == 195.0877
    assert caffeine.adduct == "[M+H]+"
    assert caffeine.polarity == "positive"
    assert caffeine.collision_energy_text == "35(NCE)"
    # The intensity column, not the relative one; not the annotation block.
    assert caffeine.peaks == [
        (195.0877, 80000.0),
        (83.0604, 12000.0),
        (138.0662, 199800.0),
        (110.0713, 30000.0),
    ]
    assert caffeine.license == "CC-BY"

    assert bisphenol.polarity == "negative"
    assert bisphenol.adduct == "[M-H]-"
    assert bisphenol.collision_energy_text == "20 eV"
    # Each record keeps its own licence, a non-commercial one included.
    assert bisphenol.license == "CC-BY-NC-SA"


def _tarball(tmp_path: Path, members: dict[str, str]) -> Path:
    path = tmp_path / "MassBank-data-2026.03.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        for name, text in members.items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return path


def _records() -> list[str]:
    return [chunk + "//\n" for chunk in MASSBANK.read_text().split("//\n") if chunk]


def test_massbank_reads_a_release_archive_without_unpacking_it(tmp_path):
    first, second, ms1, _ = _records()
    path = _tarball(
        tmp_path,
        {
            "MassBank-data-2026.03/TEST/MSBNK-TEST-TS000001.txt": first,
            "MassBank-data-2026.03/TEST/MSBNK-TEST-TS000002.txt": second,
            "MassBank-data-2026.03/TEST/MSBNK-TEST-TS000003.txt": ms1,
            # What else a release holds is not read as records.
            "MassBank-data-2026.03/README.md": "ACCESSION: not-a-record\n//\n",
            "MassBank-data-2026.03/TEST/notes.txt": "ACCESSION: not-a-record\n//\n",
        },
    )
    kept, skipped = _parse(MassBankAdapter(), path)
    assert [s.source_native_id for s in kept] == [
        "MSBNK-TEST-TS000001",
        "MSBNK-TEST-TS000002",
    ]
    assert skipped == [NOT_MS2]
    assert not list(tmp_path.glob("MassBank-data-2026.03/*"))


def test_massbank_reads_a_gzipped_record_file(tmp_path):
    path = tmp_path / "massbank.txt.gz"
    path.write_bytes(gzip.compress(MASSBANK.read_bytes()))
    kept, _ = _parse(MassBankAdapter(), path)
    assert len(kept) == 2


def test_massbank_reads_a_last_record_that_lost_its_terminator(tmp_path):
    path = tmp_path / "one.txt"
    path.write_text(_records()[0].removesuffix("//\n"))
    assert [r.first("ACCESSION") for r in read_massbank_records(path)] == [
        "MSBNK-TEST-TS000001"
    ]


def test_massbank_placeholder_structure_fields_read_as_absent(tmp_path):
    path = tmp_path / "na.txt"
    path.write_text(
        _records()[0]
        .replace("CH$SMILES: CN1C=NC2=C1C(=O)N(C(=O)N2C)C", "CH$SMILES: N/A")
        .replace("CH$LINK: INCHIKEY RYYVLZVUVIJVGH-UHFFFAOYSA-N\n", "")
    )
    (spectrum,), _ = _parse(MassBankAdapter(), path)
    assert spectrum.smiles is None
    assert spectrum.inchikey is None


# --- MoNA ---------------------------------------------------------------------


def test_mona_reads_an_msp_export():
    kept, skipped = _parse(MonaAdapter(), MONA)
    assert [s.source_native_id for s in kept] == ["TEST000101", "TEST000102"]
    # The in-silico record, and the GC-MS one, which is no MS2 spectrum.
    assert skipped == [PREDICTED, NOT_MS2]

    atrazine, pfoa = kept
    assert atrazine.source == "mona"
    assert atrazine.name == "Atrazine"
    assert atrazine.formula == "C8H14ClN5"
    assert atrazine.inchikey == "MXWJVTOOROXGIU-UHFFFAOYSA-N"
    assert atrazine.smiles == "CCNC1=NC(Cl)=NC(NC(C)C)=N1"
    assert atrazine.inchi.startswith("InChI=1S/C8H14ClN5")
    assert atrazine.precursor_mz == 216.1010
    assert atrazine.adduct == "[M+H]+"
    assert atrazine.polarity == "positive"
    assert atrazine.collision_energy_text == "35 %"
    # Two peaks on one line, split by ';'.
    assert atrazine.peaks == [(174.0541, 100.0), (104.001, 12.5), (132.0323, 30.25)]
    assert atrazine.license == "CC-BY"

    # A record that states no licence is tagged so, not given MoNA's terms.
    assert pfoa.license == "mona-unstated"
    assert pfoa.polarity == "negative"
    # The computed SMILES stands in where the record has none of its own.
    assert pfoa.smiles.startswith("OC(=O)C(F)(F)")
    # A quoted annotation after a peak is not read as a number.
    assert pfoa.peaks == [(368.9765, 100.0), (168.9888, 22.5)]


def test_msp_comments_split_at_the_first_equals_sign():
    comments = msp_comments(
        '"InChI=InChI=1S/CH4/h1H4" "license=CC BY" "license=CC0" "x"'
    )
    assert comments == {"InChI": "InChI=1S/CH4/h1H4", "license": "CC BY"}


def test_msp_records_without_a_blank_line_between_them(tmp_path):
    path = tmp_path / "packed.msp"
    path.write_text(
        "Name: A\nNum Peaks: 1\n10 1\nName: B\nNum Peaks: 1\n20 2\n", encoding="utf-8"
    )
    records = list(read_msp_records(path))
    assert [r.first("name") for r in records] == ["A", "B"]
    assert [r.peaks for r in records] == [[(10.0, 1.0)], [(20.0, 2.0)]]


def test_an_annotation_holding_a_semicolon_splits_no_peak(tmp_path):
    path = tmp_path / "annotated.msp"
    path.write_text(
        'Name: A\nNum Peaks: 2\n107.0491 43 "y1; 2 3"; 110.07 5\n', encoding="utf-8"
    )
    (record,) = read_msp_records(path)
    assert record.peaks == [(107.0491, 43.0), (110.07, 5.0)]


@pytest.mark.parametrize(
    "line, pair",
    [
        ("107.0491 43", (107.0491, 43.0)),
        ("107.0491\t43", (107.0491, 43.0)),
        ("107.0491 340449.8 43", (107.0491, 340449.8)),
        ("1.5e2 3E1", (150.0, 30.0)),
        ("107.0491", None),
    ],
)
def test_peak_pair(line, pair):
    assert peak_pair(line) == pair


# --- GNPS ---------------------------------------------------------------------


def test_gnps_reads_a_library_mgf():
    kept, skipped = _parse(GnpsAdapter(), GNPS)
    assert [s.source_native_id for s in kept] == [
        "CCMSLIBTEST0001",
        "CCMSLIBTEST0002",
        # PEPMASS=0 is GNPS's own; the common rule leaves it out at finalize.
        "CCMSLIBTEST0004",
    ]
    # Class 10, which GNPS does not document, and the MS1 block.
    assert skipped == [LIBRARY_CLASS, NOT_MS2]

    caffeine, aspirin, _ = kept
    assert caffeine.source == "gnps"
    # The ion type GNPS appends to the name is read off it, and bracketed.
    assert caffeine.name == "Caffeine"
    assert caffeine.adduct == "[M+H]+"
    assert caffeine.polarity == "positive"
    # The formula from the InChI's formula layer; the key from INCHIAUX.
    assert caffeine.formula == "C8H10N4O2"
    assert caffeine.inchikey == "RYYVLZVUVIJVGH-UHFFFAOYSA-N"
    assert caffeine.precursor_mz == 195.0877
    assert caffeine.collision_energy_text is None
    # GNPS's own library is CC0.
    assert caffeine.license == "CC0"

    assert aspirin.name == "Aspirin"
    assert aspirin.adduct == "[M-H]-"
    assert aspirin.polarity == "negative"
    # PEPMASS's intensity is not the m/z.
    assert aspirin.precursor_mz == 179.0350
    assert aspirin.inchi is None and aspirin.formula is None
    # An imported library keeps its source's terms, which the MGF does not state.
    assert aspirin.license == "gnps-unstated"


@pytest.mark.parametrize(
    "name, charge, mode, expected_name, adduct",
    [
        ("Caffeine M+H", "1", "Positive", "Caffeine", "[M+H]+"),
        ("Caffeine M+2H", "2", "Positive", "Caffeine", "[M+2H]2+"),
        ("Aspirin M-H", "0", "Negative", "Aspirin", "[M-H]-"),
        ("Some ion M+", "1", "Positive", "Some ion", "[M]+"),
        ("Glucose 2M+Na", "1", "Positive", "Glucose", "[2M+Na]+"),
        ("Glucose [M+Na]+", "1", "Positive", "Glucose", "[M+Na]+"),
        # No sign to give it: kept as written.
        ("Caffeine M+H", "1", "N/A", "Caffeine", "M+H"),
        # A name that ends in no ion type keeps its last word.
        ("Vitamin B12", "1", "Positive", "Vitamin B12", None),
    ],
)
def test_gnps_ion_type_from_the_name(
    tmp_path, name, charge, mode, expected_name, adduct
):
    path = tmp_path / "one.mgf"
    path.write_text(
        "BEGIN IONS\nPEPMASS=300.1\nCHARGE={}\nMSLEVEL=2\nIONMODE={}\n"
        "ORGANISM=GNPS-LIBRARY\nNAME={}\nSPECTRUMID=X1\n100.0 1.0\nEND IONS\n".format(
            charge, mode, name
        ),
        encoding="utf-8",
    )
    (spectrum,), _ = _parse(GnpsAdapter(), path)
    assert spectrum.name == expected_name
    assert spectrum.adduct == adduct


def test_gnps_reads_no_formula_off_a_charged_structure(tmp_path):
    path = tmp_path / "choline.mgf"
    path.write_text(
        "BEGIN IONS\nPEPMASS=104.107\nMSLEVEL=2\nIONMODE=Positive\n"
        "ORGANISM=GNPS-LIBRARY\nNAME=Choline M+\nSPECTRUMID=X2\n"
        "INCHI=InChI=1S/C5H14NO/c1-6(2,3)4-5-7/h7H,4-5H2,1-3H3/q+1\n"
        "60.08 10.0\nEND IONS\n",
        encoding="utf-8",
    )
    (spectrum,), _ = _parse(GnpsAdapter(), path)
    assert spectrum.formula is None


def test_gnps_library_named_gnps_itself_is_its_own(tmp_path):
    path = tmp_path / "own.mgf"
    path.write_text(
        "BEGIN IONS\nPEPMASS=104.107\nMSLEVEL=2\nORGANISM=GNPS\nNAME=A M+H\n"
        "SPECTRUMID=X3\n60.08 10.0\nEND IONS\n",
        encoding="utf-8",
    )
    (spectrum,), _ = _parse(GnpsAdapter(), path)
    assert spectrum.license == "CC0"


def test_gnps_reads_past_a_blank_line_inside_a_block(tmp_path):
    path = tmp_path / "blank.mgf"
    path.write_text(
        "BEGIN IONS\nPEPMASS=104.107\n\nMSLEVEL=2\nNAME=A M+H\nSPECTRUMID=X5\n"
        "60.08 10.0\n\n61.08 5.0\nEND IONS\n",
        encoding="utf-8",
    )
    (spectrum,), _ = _parse(GnpsAdapter(), path)
    assert spectrum.peaks == [(60.08, 10.0), (61.08, 5.0)]


def test_gnps_admits_a_block_that_states_no_library_class(tmp_path):
    path = tmp_path / "noclass.mgf"
    path.write_text(
        "BEGIN IONS\nPEPMASS=104.107\nMSLEVEL=2\nNAME=A M+H\nSPECTRUMID=X4\n"
        "60.08 10.0\nEND IONS\n",
        encoding="utf-8",
    )
    kept, skipped = _parse(GnpsAdapter(), path)
    assert len(kept) == 1 and skipped == []
