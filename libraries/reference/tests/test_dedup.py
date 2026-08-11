"""Cross-source InChIKey collapse tests."""

from mascope_reference.dedup import collapse_by_inchikey
from mascope_reference.record import ReferenceRecord


def _rec(source, native_id, inchikey=None, **kw) -> ReferenceRecord:
    return ReferenceRecord(
        formula="C6H12O6",
        source=source,
        source_native_id=native_id,
        inchikey=inchikey,
        license="l",
        xrefs={f"{source}_id": native_id},
        **kw,
    )


def test_collapse_prefers_curated_source_and_merges_xrefs():
    records = [
        _rec("pubchem", "5793", inchikey="WQZGKKKJIJFFOK-GASJEMHNSA-N", name="glucose"),
        _rec("chebi", "CHEBI:17234", inchikey="WQZGKKKJIJFFOK-GASJEMHNSA-N"),
    ]
    collapsed = collapse_by_inchikey(records)

    assert len(collapsed) == 1
    survivor = collapsed[0]
    # ChEBI outranks PubChem, so it wins identity...
    assert survivor.source == "chebi"
    # ...but its blank name is backfilled from PubChem.
    assert survivor.name == "glucose"
    # xrefs from both are merged and the contributing sources recorded.
    assert survivor.xrefs["pubchem_id"] == "5793"
    assert survivor.xrefs["chebi_id"] == "CHEBI:17234"
    assert survivor.xrefs["sources"] == ["chebi", "pubchem"]


def test_records_without_inchikey_pass_through():
    records = [
        _rec("pubchem", "1", inchikey=None),
        _rec("comptox", "2", inchikey=None),
    ]
    collapsed = collapse_by_inchikey(records)
    assert len(collapsed) == 2
    assert {r.source_native_id for r in collapsed} == {"1", "2"}


def test_distinct_inchikeys_are_kept_separately():
    records = [
        _rec("pubchem", "1", inchikey="AAA-A"),
        _rec("pubchem", "2", inchikey="BBB-B"),
    ]
    collapsed = collapse_by_inchikey(records)
    assert len(collapsed) == 2


def test_first_appearance_order_is_preserved():
    records = [
        _rec("norman", "n1", inchikey="ZZZ-Z"),
        _rec("pubchem", "p1", inchikey=None, name="keyless"),
        _rec("chebi", "c1", inchikey="ZZZ-Z"),
    ]
    collapsed = collapse_by_inchikey(records)
    # The ZZZ-Z group is anchored at index 0; the keyless record stays second.
    assert collapsed[0].xrefs["sources"] == ["chebi", "norman"]
    assert collapsed[1].name == "keyless"
