"""Adapter parsing tests, one small fixture per source format."""

from pathlib import Path

from mascope_reference.adapters.chebi import ChebiAdapter
from mascope_reference.adapters.coconut import CoconutAdapter
from mascope_reference.adapters.comptox import CompToxAdapter
from mascope_reference.adapters.custom import CustomAdapter
from mascope_reference.adapters.hmdb import HmdbAdapter
from mascope_reference.adapters.lipidmaps import LipidMapsAdapter
from mascope_reference.adapters.norman import NormanAdapter
from mascope_reference.adapters.pubchem import PubChemAdapter


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


PUBCHEM_SDF = """2244
  -OEChem-

 21 21  0     0  0  0  0  0  0999 V2000
    connection table line ignored
M  END
> <PUBCHEM_COMPOUND_CID>
2244

> <PUBCHEM_MOLECULAR_FORMULA>
C9H8O4

> <PUBCHEM_IUPAC_NAME>
2-acetyloxybenzoic acid

> <PUBCHEM_OPENEYE_CAN_SMILES>
CC(=O)OC1=CC=CC=C1C(=O)O

> <PUBCHEM_IUPAC_INCHIKEY>
BSYNRYMUTXBXSQ-UHFFFAOYSA-N

> <PUBCHEM_IUPAC_INCHI>
InChI=1S/C9H8O4/c1-6(10)13-8-5-3-2-4-7(8)9(11)12/h2-5H,1H3,(H,11,12)

$$$$
"""


def test_pubchem_adapter(tmp_path):
    path = _write(tmp_path, "pubchem.sdf", PUBCHEM_SDF)
    records = list(PubChemAdapter().parse(path))
    assert len(records) == 1
    r = records[0]
    assert r.source == "pubchem"
    assert r.source_native_id == "2244"
    assert r.formula == "C9H8O4"
    assert r.name == "2-acetyloxybenzoic acid"
    assert r.inchikey == "BSYNRYMUTXBXSQ-UHFFFAOYSA-N"
    assert r.smiles == "CC(=O)OC1=CC=CC=C1C(=O)O"
    assert r.inchi.startswith("InChI=1S/C9H8O4")
    assert r.license == "public-domain"
    assert r.xrefs == {"pubchem_cid": "2244"}


def test_pubchem_skips_record_without_formula(tmp_path):
    sdf = "> <PUBCHEM_COMPOUND_CID>\n999\n\n$$$$\n"
    path = _write(tmp_path, "nf.sdf", sdf)
    assert list(PubChemAdapter().parse(path)) == []


COMPTOX_CSV = (
    "DTXSID,PREFERRED_NAME,CASRN,MOLECULAR_FORMULA,SMILES,INCHIKEY,INCHI_STRING\n"
    "DTXSID7020182,Bisphenol A,80-05-7,C15H16O2,CC(C)(c1ccccc1)c1ccccc1,"
    "IISBACLAFKSPIT-UHFFFAOYSA-N,InChI=1S/C15H16O2\n"
)


def test_comptox_adapter(tmp_path):
    path = _write(tmp_path, "comptox.csv", COMPTOX_CSV)
    records = list(CompToxAdapter().parse(path))
    assert len(records) == 1
    r = records[0]
    assert r.source == "comptox"
    assert r.source_native_id == "DTXSID7020182"
    assert r.formula == "C15H16O2"
    assert r.name == "Bisphenol A"
    assert r.inchikey == "IISBACLAFKSPIT-UHFFFAOYSA-N"
    assert r.xrefs == {"dtxsid": "DTXSID7020182", "casrn": "80-05-7"}


CHEBI_SDF = """
> <ChEBI ID>
CHEBI:15377

> <ChEBI Name>
water

> <Formulae>
H2O

> <InChIKey>
XLYOFNOQVPJJNP-UHFFFAOYSA-N

> <SMILES>
[H]O[H]

> <InChI>
InChI=1S/H2O/h1H2

$$$$
"""


def test_chebi_adapter(tmp_path):
    path = _write(tmp_path, "chebi.sdf", CHEBI_SDF)
    records = list(ChebiAdapter().parse(path))
    assert len(records) == 1
    r = records[0]
    assert r.source == "chebi"
    assert r.source_native_id == "CHEBI:15377"
    assert r.formula == "H2O"
    assert r.name == "water"
    assert r.license == "CC-BY-4.0"


LIPIDMAPS_SDF = """
> <LM_ID>
LMFA01010001

> <NAME>
Formic acid

> <FORMULA>
CH2O2

> <INCHI_KEY>
BDAGIHXWWSANSR-UHFFFAOYSA-N

> <SMILES>
OC=O

> <INCHI>
InChI=1S/CH2O2/c2-1-3/h1H,(H,2,3)

$$$$
"""


def test_lipidmaps_adapter(tmp_path):
    path = _write(tmp_path, "lmsd.sdf", LIPIDMAPS_SDF)
    records = list(LipidMapsAdapter().parse(path))
    assert len(records) == 1
    r = records[0]
    assert r.source == "lipidmaps"
    assert r.source_native_id == "LMFA01010001"
    assert r.formula == "CH2O2"
    assert r.inchikey == "BDAGIHXWWSANSR-UHFFFAOYSA-N"


COCONUT_CSV = (
    "identifier,name,molecular_formula,canonical_smiles,inchi,inchikey\n"
    "CNP0000001,Caffeine,C8H10N4O2,Cn1cnc2c1c(=O)n(C)c(=O)n2C,"
    "InChI=1S/C8H10N4O2,RYYVLZVUVIJVGH-UHFFFAOYSA-N\n"
)


def test_coconut_adapter(tmp_path):
    path = _write(tmp_path, "coconut.csv", COCONUT_CSV)
    records = list(CoconutAdapter().parse(path))
    assert len(records) == 1
    r = records[0]
    assert r.source == "coconut"
    assert r.source_native_id == "CNP0000001"
    assert r.formula == "C8H10N4O2"
    assert r.smiles == "Cn1cnc2c1c(=O)n(C)c(=O)n2C"
    assert r.license == "CC0"


NORMAN_CSV = (
    "Norman_SusDat_ID,Name,Molecular_Formula,SMILES,InChIKey,InChI,DTXSID,CAS_RN\n"
    "NS00000123,Perfluorooctanoic acid,C8HF15O2,OC(=O)C(F)(F)...,"
    "SNGREZUHAYWORS-UHFFFAOYSA-N,InChI=1S/C8HF15O2,DTXSID8031865,335-67-1\n"
)


def test_norman_adapter(tmp_path):
    path = _write(tmp_path, "norman.csv", NORMAN_CSV)
    records = list(NormanAdapter().parse(path))
    assert len(records) == 1
    r = records[0]
    assert r.source == "norman"
    assert r.source_native_id == "NS00000123"
    assert r.formula == "C8HF15O2"
    assert r.xrefs == {"dtxsid": "DTXSID8031865", "casrn": "335-67-1"}


HMDB_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hmdb>
  <metabolite>
    <accession>HMDB0000001</accession>
    <name>1-Methylhistidine</name>
    <chemical_formula>C7H11N3O2</chemical_formula>
    <smiles>CN1C=NC(C[C@H](N)C(O)=O)=C1</smiles>
    <inchi>InChI=1S/C7H11N3O2</inchi>
    <inchikey>BRMWTNUJHUMWMS-LURJTMIESA-N</inchikey>
  </metabolite>
  <metabolite>
    <accession>HMDB0000002</accession>
    <name>1,3-Diaminopropane</name>
    <chemical_formula>C3H10N2</chemical_formula>
    <inchikey>XFNJVJPLKCPIBV-UHFFFAOYSA-N</inchikey>
  </metabolite>
</hmdb>
"""


def test_hmdb_adapter(tmp_path):
    path = _write(tmp_path, "hmdb.xml", HMDB_XML)
    records = list(HmdbAdapter().parse(path))
    assert len(records) == 2
    first = records[0]
    assert first.source == "hmdb"
    assert first.source_native_id == "HMDB0000001"
    assert first.formula == "C7H11N3O2"
    assert first.name == "1-Methylhistidine"
    assert first.inchikey == "BRMWTNUJHUMWMS-LURJTMIESA-N"
    # Second metabolite has no smiles/inchi - those fields are None, not missing.
    assert records[1].smiles is None
    assert records[1].formula == "C3H10N2"


# A hand-authored list: a rich row (name + cas + citation) and a bare row
# (formula only, id falls back to a row number).
CUSTOM_CSV = (
    "name,formula,cas,reference,inchikey\n"
    "Pinonic acid,C10H16O3,473-72-3,10.1000/example,BFKGXSVWFIQNJS-UHFFFAOYSA-N\n"
    ",C10H16O5,,,\n"
)


def test_custom_adapter(tmp_path):
    path = _write(tmp_path, "hom-list.csv", CUSTOM_CSV)
    records = list(CustomAdapter().parse(path))
    assert len(records) == 2

    rich = records[0]
    assert rich.source == "custom"
    assert rich.name == "Pinonic acid"
    assert rich.formula == "C10H16O3"
    assert rich.source_native_id == "Pinonic acid"  # falls back to name
    assert rich.xrefs == {"cas": "473-72-3", "reference": "10.1000/example"}
    assert rich.inchikey == "BFKGXSVWFIQNJS-UHFFFAOYSA-N"
    assert rich.license == "custom"

    bare = records[1]
    assert bare.formula == "C10H16O5"
    assert bare.name is None
    assert bare.source_native_id == "custom-2"  # falls back to row number


def test_custom_adapter_tsv_and_aliases(tmp_path):
    # Tab-separated, and the header uses the 'molecular_formula' alias.
    tsv = "compound\tmolecular_formula\nTerpenylic acid\tC8H12O4\n"
    path = _write(tmp_path, "list.tsv", tsv)
    records = list(CustomAdapter().parse(path))
    assert len(records) == 1
    assert records[0].name == "Terpenylic acid"
    assert records[0].formula == "C8H12O4"


def test_custom_adapter_skips_rows_without_formula(tmp_path):
    path = _write(tmp_path, "bad.csv", "name,formula\nNoFormula,\n")
    assert list(CustomAdapter().parse(path)) == []
