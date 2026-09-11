"""The reference-list format: reading it, the checks a shipped list must pass,
and the adapter that loads one.

Radical status is the part with history. The reference engine's own lists set
their radical flag from an odd hydrogen count, which is wrong as soon as
nitrogen is present: an organic nitrate like C10H15NO8 has odd hydrogen and no
unpaired electron. So parity is read from the formula, and these tests pin that
it is the formula and not the flag that decides - in the checks and at ingest.
"""

import json
from pathlib import Path

import pytest

from mascope_reference.adapters.peaklist import PeakListAdapter
from mascope_reference.peaklist import (
    PeakListError,
    admitted_species,
    dbe,
    is_odd_electron,
    list_problems,
    read_peak_list,
)
from mascope_reference.sources import get_adapter


def _good(**overrides) -> dict:
    data = {
        "schema_version": 2,
        "id": "test-list",
        "label": "A test list",
        "data_version": "2026.09",
        "license": "CC-BY-4.0",
        "references": [
            {
                "citation": "A. Author, A paper. J. Test 1 (2026) 1-2.",
                "doi": "10.1234/t.1",
            }
        ],
        "species": [
            {"formula": "C10H16O7", "conditions": ["NOx"], "evidence": "formula"},
            {"formula": "C10H15NO8", "name": "An organic nitrate"},
        ],
    }
    data.update(overrides)
    return data


def _write(tmp_path: Path, data, name: str | None = None) -> Path:
    path = tmp_path / f"{name or data['id']}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _problems(tmp_path: Path, data, name: str | None = None, **kwargs) -> list[str]:
    return list_problems(read_peak_list(_write(tmp_path, data, name)), **kwargs)


# --- Parity -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("formula", "odd"),
    [
        ("C10H16O7", False),  # a closed-shell HOM
        ("C10H15O8", True),  # its peroxy radical
        ("C10H15NO8", False),  # odd hydrogen, but an organic nitrate is closed-shell
        ("C10H16NO9", True),  # even hydrogen, and a nitrogen-bearing radical
        ("HO2", True),
        ("H3N", False),
        ("C2H8O2Si", False),  # dimethylsilanediol: silicon counts with carbon
        ("C6H15O4P", False),  # triethyl phosphate
        ("IO2", True),  # iodine dioxide: iodine counts with hydrogen
    ],
)
def test_parity_is_read_from_the_formula(formula, odd):
    assert is_odd_electron(formula) is odd


def test_a_quaternary_ammonium_salt_has_a_negative_dbe():
    assert dbe("C25H54ClN") == -1
    assert dbe("C10H16O7") == 3


# --- What a shipped list has to be ------------------------------------------


def test_a_well_formed_list_has_no_problems(tmp_path):
    assert _problems(tmp_path, _good(), licenses={"CC-BY-4.0"}) == []


def test_a_radical_needs_a_list_that_allows_radicals(tmp_path):
    radical = _good(species=[{"formula": "C10H15O8"}])
    problems = _problems(tmp_path, radical)
    assert any("does not allow radicals" in p for p in problems)
    assert _problems(tmp_path, {**radical, "allow_radicals": True}) == []


def test_a_radical_claim_is_checked_against_the_formula(tmp_path):
    # How the reference engine's list marks an organic nitrate.
    claimed = _good(species=[{"formula": "C10H15NO8", "radical": True}])
    problems = _problems(tmp_path, claimed)
    assert problems == [
        "species 1 (C10H15NO8): marked a radical, but the formula is even-electron"
    ]
    agreeing = _good(
        allow_radicals=True, species=[{"formula": "C10H15O8", "radical": True}]
    )
    assert _problems(tmp_path, agreeing) == []


@pytest.mark.parametrize(
    ("formula", "expected"),
    [
        ("C25H54ClN", "its DBE is -1, which no molecule has"),  # a quaternary chloride
        ("C16H36N", "its DBE is -0.5"),  # the tetrabutylammonium cation
        ("C2H3O2-", "not a neutral molecular formula"),  # an ion, charge and all
        ("Not available", "not a neutral molecular formula"),
    ],
)
def test_ions_and_salts_are_not_molecules(tmp_path, formula, expected):
    problems = _problems(tmp_path, _good(species=[{"formula": formula}]))
    assert any(expected in p for p in problems), problems


def test_a_formula_is_listed_once_however_it_is_written(tmp_path):
    twice = _good(species=[{"formula": "C10H16O7"}, {"formula": "O7C10H16"}])
    assert _problems(tmp_path, twice) == [
        "species 2 (O7C10H16): the same formula as species 1"
    ]


def test_isomers_share_a_formula_under_different_names(tmp_path):
    # Identity is one-to-many on a formula: acetic acid and glycolaldehyde are
    # both C2H4O2, and a list may name both.
    isomers = _good(
        species=[
            {"formula": "C2H4O2", "name": "Acetic acid"},
            {"formula": "C2H4O2", "name": "Glycolaldehyde"},
        ]
    )
    assert _problems(tmp_path, isomers) == []
    twice = _good(
        species=[
            {"formula": "C2H4O2", "name": "Acetic acid"},
            {"formula": "O2C2H4", "name": "Acetic acid"},
        ]
    )
    assert _problems(tmp_path, twice) == [
        "species 2 (O2C2H4): the same formula and name as species 1"
    ]


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        ({"citation": "A thesis."}, "neither a DOI nor an ISBN"),
        ({"citation": "A thesis.", "isbn": "978-3-95806-596-4"}, "not a valid ISBN"),
        ({"citation": "A paper.", "doi": "doi:10.1234/x"}, "is not a DOI"),
        ({"doi": "10.1234/x"}, "no citation"),
        (
            {"citation": "A paper.", "doi": "10.1234/x", "url": "u"},
            "unknown key(s) url",
        ),
    ],
)
def test_every_reference_cites_its_source(tmp_path, reference, expected):
    problems = _problems(tmp_path, _good(references=[reference]))
    assert any(expected in p for p in problems), problems


def test_an_isbn_with_a_valid_check_digit_cites(tmp_path):
    thesis = _good(references=[{"citation": "A thesis.", "isbn": "978-3-95806-596-3"}])
    assert _problems(tmp_path, thesis) == []


def test_a_list_needs_a_reference_and_a_licence(tmp_path):
    bare = _good()
    del bare["references"]
    del bare["license"]
    problems = _problems(tmp_path, bare)
    assert "no reference" in problems
    assert "no licence" in problems


def test_the_licence_is_a_tag_the_gate_knows(tmp_path):
    problems = _problems(tmp_path, _good(license="made-up"), licenses={"CC-BY-4.0"})
    assert problems == ["licence 'made-up' is not a tag the licence gate knows"]


def test_the_file_is_named_by_the_id(tmp_path):
    problems = _problems(tmp_path, _good(), name="other-name")
    assert problems == ["the file is named 'other-name.json' but its id is 'test-list'"]


def test_unknown_keys_are_named(tmp_path):
    data = _good(system="x", species=[{"formula": "C10H16O7", "origin": "y"}])
    assert _problems(tmp_path, data) == [
        "unknown key(s) system",
        "species 1 (C10H16O7): unknown key(s) origin",
    ]


def test_a_per_row_reference_is_a_doi(tmp_path):
    data = _good(species=[{"formula": "C10H16O7", "reference": "Author 2020"}])
    assert _problems(tmp_path, data) == [
        "species 1 (C10H16O7): its reference 'Author 2020' is not a DOI"
    ]


# --- Reading ------------------------------------------------------------------

#: The reference engine's own format, as its HOM list writes it - flag and all.
SCHEMA_1 = {
    "schema_version": 1,
    "id": "engine_list",
    "system": "monoterpene_OH_oxidation",
    "data_version": "2024.1",
    "references": [{"authors": "S. Kang", "year": 2022}],
    "n_species": 3,
    "species": [
        {"formula": "C10H16O7", "conditions": ["pure"], "radical": False},
        {"formula": "C10H15NO8", "conditions": ["NOx"], "radical": True},
        {"formula": "C10H15O8", "conditions": ["NOx"], "radical": True},
    ],
}


def test_schema_1_reads_but_does_not_ship(tmp_path):
    peak_list = read_peak_list(_write(tmp_path, SCHEMA_1))
    assert peak_list.schema_version == 1
    assert peak_list.license is None
    problems = list_problems(peak_list)
    assert "schema_version is 1; a shipped list is schema 2" in problems
    assert "no licence" in problems


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("not json", "not valid JSON"),
        ("[1, 2]", "holds one JSON object"),
        (json.dumps({**SCHEMA_1, "schema_version": 3}), "schema_version 3"),
        (json.dumps({**SCHEMA_1, "schema_version": True}), "schema_version True"),
        (json.dumps({**SCHEMA_1, "id": ""}), "has no id"),
        (json.dumps({**SCHEMA_1, "species": {}}), "no species array"),
        (json.dumps({**SCHEMA_1, "species": [{"name": "x"}]}), "needs a formula"),
    ],
)
def test_a_file_that_is_not_a_list_is_refused(tmp_path, content, expected):
    path = tmp_path / "broken.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(PeakListError, match=expected):
        read_peak_list(path)


# --- The adapter --------------------------------------------------------------


def _formulas(records) -> list[str]:
    return [record.formula for record in records]


def test_the_adapter_holds_radicals_back_unless_the_list_allows_them(tmp_path):
    species = [
        {"formula": "C10H16O7"},
        {"formula": "C10H15NO8"},
        {"formula": "C10H15O8"},
    ]
    held = _write(tmp_path, _good(species=species), name="held")
    allowed = _write(
        tmp_path, _good(id="allowed", species=species, allow_radicals=True)
    )
    assert _formulas(PeakListAdapter().parse(held)) == ["C10H16O7", "C10H15NO8"]
    assert _formulas(PeakListAdapter().parse(allowed)) == [
        "C10H16O7",
        "C10H15NO8",
        "C10H15O8",
    ]


def test_parity_not_the_flag_decides_what_a_schema_1_list_loads(tmp_path):
    # The nitrate the flag calls a radical loads; the peroxy radical does not.
    records = list(PeakListAdapter().parse(_write(tmp_path, SCHEMA_1)))
    assert _formulas(records) == ["C10H16O7", "C10H15NO8"]
    # A schema 1 list names no licence, so its rows carry the adapter's.
    assert {record.license for record in records} == {"custom"}


def test_a_record_carries_only_facts_of_its_own(tmp_path):
    data = _good(
        species=[
            {"formula": "C10H16O7", "name": "A HOM", "reference": "10.1234/row.1"},
            {"formula": "C10H15NO8", "conditions": ["NOx"], "evidence": "formula"},
        ]
    )
    first, second = PeakListAdapter().parse(_write(tmp_path, data))
    assert first.name == "A HOM"
    assert first.xrefs == {"reference": "10.1234/row.1"}
    assert first.license == "CC-BY-4.0"
    # Conditions, evidence and everything list-level stay in the file.
    assert second.xrefs == {}
    assert second.name is None


def test_a_row_is_identified_by_its_formula_and_name(tmp_path):
    # Isomers share a formula, so the formula alone cannot identify a row.
    data = _good(
        species=[
            {"formula": "C2H4O2", "name": "Acetic acid"},
            {"formula": "C2H4O2", "name": "Glycolaldehyde"},
            {"formula": "C10H16O7"},
        ]
    )
    ids = [r.source_native_id for r in PeakListAdapter().parse(_write(tmp_path, data))]
    assert ids == ["C2H4O2 Acetic acid", "C2H4O2 Glycolaldehyde", "C10H16O7"]


def test_admitted_species_are_what_the_adapter_loads(tmp_path):
    peak_list = read_peak_list(_write(tmp_path, SCHEMA_1))
    assert [s.formula for s in admitted_species(peak_list)] == ["C10H16O7", "C10H15NO8"]


def test_the_registry_knows_the_adapter():
    adapter = get_adapter("peaklist")
    assert isinstance(adapter, PeakListAdapter)
    assert adapter.license == "custom"
