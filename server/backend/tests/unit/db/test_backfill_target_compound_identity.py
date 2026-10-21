"""
Unit tests for the target compound identity backfill script.

The plan decides every compound the script fills, so it is tested directly on
rows shaped as the two queries return them: a compound is filled only when its
CAS number and its formula both match, and only by one InChIKey. The queries
themselves are in the integration tests. Hermetic - no database.
"""

import pytest

from mascope_backend.db.scripts import backfill_target_compound_identity as script


GLUCOSE_KEY = "WQZGKKKJIJFFOK-GASJEMHNSA-N"
GALACTOSE_KEY = "WQZGKKKJIJFFOK-PHYPRBDBSA-N"
GLUCOSE_INCHI = (
    "InChI=1S/C6H12O6/c7-1-2-3(8)4(9)5(10)6(11)12-2/h2-11H,1H2/t2-,3-,4+,5-,6+/m1/s1"
)


def _compound(cas: str = "50-99-7", formula: str = "C6H12O6") -> dict:
    return {
        "target_compound_id": "tc1",
        "target_compound_formula": formula,
        "cas_number": cas,
    }


def _row(
    n: int,
    *,
    cas: str = "50997",
    formula: str = "C6H12O6",
    inchikey: str = GLUCOSE_KEY,
    inchi: str | None = GLUCOSE_INCHI,
    smiles: str | None = "OCC1OC(O)C(O)C(O)C1O",
) -> dict:
    return {
        "reference_compound_id": n,
        "formula": formula,
        "inchikey": inchikey,
        "inchi": inchi,
        "smiles": smiles,
        "cas": {cas},
    }


def test_cas_numbers_compare_as_digits():
    assert script.cas_digits("50-99-7") == script.cas_digits(" 50997 ") == "50997"
    assert script.cas_digits(None) == ""


def test_one_inchikey_fills_all_three_fields():
    plan = script._plan([_compound()], [_row(1)])

    assert plan["updates"] == [
        {
            "id": "tc1",
            "inchikey": GLUCOSE_KEY,
            "inchi": GLUCOSE_INCHI,
            "smiles": "OCC1OC(O)C(O)C(O)C1O",
        }
    ]
    assert plan["ambiguous"] == []
    assert plan["unmatched"] == 0


def test_rows_of_one_key_fill_from_the_first_that_has_each_field():
    """Two sources holding the same compound count as one InChIKey."""
    plan = script._plan(
        [_compound()],
        [_row(1, inchi=None, smiles=None), _row(2, smiles=None), _row(3)],
    )

    (update,) = plan["updates"]
    assert update["inchi"] == GLUCOSE_INCHI
    assert update["smiles"] == "OCC1OC(O)C(O)C(O)C1O"


def test_several_inchikeys_leave_the_compound_alone():
    plan = script._plan(
        [_compound()], [_row(1), _row(2, inchikey=GALACTOSE_KEY, inchi=None)]
    )

    assert plan["updates"] == []
    assert plan["ambiguous"] == [
        {
            "id": "tc1",
            "cas_number": "50-99-7",
            "inchikeys": [GLUCOSE_KEY, GALACTOSE_KEY],
        }
    ]


def test_a_cas_number_with_another_formula_is_no_match():
    """The CAS number was entered against the wrong compound, or the source
    records another form of it: either way not this compound's structure."""
    plan = script._plan([_compound(formula="C6H14O6")], [_row(1)])

    assert plan["updates"] == []
    assert plan["unmatched"] == 1


def test_a_formula_matches_in_any_order():
    plan = script._plan([_compound(formula="H12C6O6")], [_row(1)])

    assert [update["inchikey"] for update in plan["updates"]] == [GLUCOSE_KEY]


def test_another_cas_number_is_no_match():
    plan = script._plan([_compound(cas="58-86-6")], [_row(1)])

    assert plan["updates"] == []
    assert plan["unmatched"] == 1


def test_a_caret_labelled_compound_matches_no_reference_formula():
    """A labelled reagent is not the unlabelled compound the source lists."""
    plan = script._plan(
        [_compound(formula="H^NO3")],
        [_row(1, formula="HNO3", inchikey="GRYLNZFGIOXLOG-UHFFFAOYSA-N", inchi=None)],
    )

    assert plan["updates"] == []


def test_a_malformed_reference_inchikey_is_not_a_candidate():
    plan = script._plan([_compound()], [_row(1, inchikey="not-a-key")])

    assert plan["updates"] == []
    assert plan["unmatched"] == 1


def test_an_inchi_of_another_formula_is_left_out():
    """The key and the SMILES still fill: the reference row's formula is the
    compound's, only its InChI string disagrees."""
    plan = script._plan(
        [_compound()], [_row(1, inchi="InChI=1S/CH4/h1H4"), _row(2, inchi="1S/x")]
    )

    (update,) = plan["updates"]
    assert update["inchikey"] == GLUCOSE_KEY
    assert update["inchi"] is None
    assert update["smiles"] == "OCC1OC(O)C(O)C(O)C1O"


@pytest.mark.parametrize(
    ("argv", "env", "dry_run"),
    [([], None, False), (["--dry-run"], None, True), ([], "1", True)],
)
def test_a_dry_run_is_asked_for_by_flag_or_by_environment(
    monkeypatch, argv, env, dry_run
):
    """``mascope ... db script run`` passes no arguments, only the environment."""
    seen = []

    async def run(dry_run):
        seen.append(dry_run)

    monkeypatch.setattr(script, "run", run)
    monkeypatch.setattr("sys.argv", ["backfill_target_compound_identity", *argv])
    if env is None:
        monkeypatch.delenv("DRY_RUN", raising=False)
    else:
        monkeypatch.setenv("DRY_RUN", env)

    script.main()

    assert seen == [dry_run]


def test_reference_values_are_stored_as_the_routes_would_store_them():
    """Stripped, and a blank SMILES is no SMILES: the next row's is used."""
    plan = script._plan(
        [_compound()],
        [_row(1, inchi=f"{GLUCOSE_INCHI}\n", smiles="  "), _row(2, smiles=" OCC1 ")],
    )

    (update,) = plan["updates"]
    assert update["inchi"] == GLUCOSE_INCHI
    assert update["smiles"] == "OCC1"
