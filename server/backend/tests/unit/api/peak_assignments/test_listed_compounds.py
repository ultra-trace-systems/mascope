"""The reference-list compounds an assignment row's formulas are listed as.

The detail read names them for every formula the inspector shows - the
committed one, the close alternatives and the other readings of the ion - so a
formula a list holds is named wherever it appears, including where the run
reached it through the formula search. No database: the lookup is faked.
"""

import pytest

import mascope_backend.api.new.peak_assignments.service as service
from mascope_backend.api.new.peak_assignments.service import with_known_compounds
from mascope_reference.known import DEFAULT_MAX_IDENTITIES


DMF = {
    "name": "N,N-Dimethylformamide",
    "source": "contaminants-list",
    "license": "CC-BY-4.0",
    "inchikey": None,
    "source_native_id": "C3H7NO N,N-Dimethylformamide",
    "smiles": "CN(C)C=O",
    "inchi": "InChI=1S/C3H7NO/c1-4(2)3-5/h3H,1-2H3",
    "xrefs": {},
}
ACROLEIN = {**DMF, "name": "Acrolein", "source_native_id": "C3H4O Acrolein"}


def the_row(**overrides) -> dict:
    return {
        "assigned_formula": "C3H7NO",
        "alternatives": [
            {"assigned_formula": "C3H4O", "same_ion": True},
            {"assigned_formula": "C4H11N", "fit_score": 0.2},
        ],
        **overrides,
    }


@pytest.fixture
def lookup(monkeypatch):
    """Fake the reference lookup; record what it was asked."""
    asked = {}

    async def annotate(formulas, collapse=True, licenses=None):
        asked["formulas"] = list(formulas)
        asked["licenses"] = licenses
        listed = {"C3H7NO": [DMF], "C3H4O": [ACROLEIN]}
        return {formula: listed.get(formula, []) for formula in formulas}

    monkeypatch.setattr(service.reference_service, "annotate_formulas", annotate)
    return asked


@pytest.mark.asyncio
async def test_the_committed_formula_is_named(lookup):
    record = await with_known_compounds(the_row())
    assert [known["name"] for known in record["known_compounds"]] == [
        "N,N-Dimethylformamide"
    ]


@pytest.mark.asyncio
async def test_so_is_each_alternative_a_list_holds(lookup):
    record = await with_known_compounds(the_row())
    same_ion, other = record["alternatives"]
    assert [known["name"] for known in same_ion["known_compounds"]] == ["Acrolein"]
    assert "known_compounds" not in other


@pytest.mark.asyncio
async def test_one_lookup_asks_for_every_formula_the_row_shows(lookup):
    await with_known_compounds(the_row())
    assert lookup["formulas"] == ["C3H7NO", "C3H4O", "C4H11N"]


@pytest.mark.asyncio
async def test_a_compound_is_named_and_not_drawn(lookup):
    # The inspector names a compound; a formula a large list shares with many
    # compounds would otherwise carry every one of their structures.
    record = await with_known_compounds(the_row())
    assert record["known_compounds"] == [
        {
            "name": "N,N-Dimethylformamide",
            "source": "contaminants-list",
            "license": "CC-BY-4.0",
            "inchikey": None,
            "source_native_id": "C3H7NO N,N-Dimethylformamide",
        }
    ]


@pytest.mark.asyncio
async def test_a_formula_no_list_holds_is_named_by_nothing(lookup):
    record = await with_known_compounds(the_row(assigned_formula="C9H9N"))
    assert record["known_compounds"] == []


@pytest.mark.asyncio
async def test_only_the_sources_the_deployment_matches_against_name(
    lookup, monkeypatch
):
    monkeypatch.setattr(service, "reference_license_gate", lambda: ["CC-BY-4.0"])
    await with_known_compounds(the_row())
    assert lookup["licenses"] == ["CC-BY-4.0"]


@pytest.mark.asyncio
async def test_a_formula_many_compounds_share_is_named_within_the_runs_bound(
    monkeypatch,
):
    async def annotate(formulas, collapse=True, licenses=None):
        return {
            formula: [{**DMF, "name": f"isomer {i}"} for i in range(100)]
            for formula in formulas
        }

    monkeypatch.setattr(service.reference_service, "annotate_formulas", annotate)
    record = await with_known_compounds({"assigned_formula": "C3H7NO"})
    assert len(record["known_compounds"]) == DEFAULT_MAX_IDENTITIES


@pytest.mark.asyncio
async def test_a_lookup_that_fails_leaves_the_row_as_it_was(monkeypatch):
    async def annotate(*args, **kwargs):
        raise RuntimeError("the reference mirror is not migrated")

    monkeypatch.setattr(service.reference_service, "annotate_formulas", annotate)
    row = the_row()
    record = await with_known_compounds(row)
    assert record == the_row()


@pytest.mark.asyncio
async def test_a_row_that_commits_nothing_asks_nothing(monkeypatch):
    async def annotate(*args, **kwargs):
        raise AssertionError("nothing to name, so nothing to ask")

    monkeypatch.setattr(service.reference_service, "annotate_formulas", annotate)
    record = await with_known_compounds({"assigned_formula": None, "alternatives": []})
    assert "known_compounds" not in record
