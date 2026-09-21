"""The reference-list compounds an assignment row's formulas are listed as.

The detail read names them for every formula the inspector shows - the
committed one, the close alternatives and the other readings of the ion - so a
formula a list holds is named wherever it appears, including where the run
reached it through the formula search. It asks as Stage A matches: the sample's
polarity, the run's ceiling and the deployment's licences. No database: the
lookup is faked.
"""

import pytest

import mascope_backend.api.new.peak_assignments.service as service
from mascope_backend.api.new.peak_assignments.service import (
    _recorded_ceiling,
    with_known_compounds,
)
from mascope_tools.composition.known_window import KnownWindow


DMF = {
    "name": "N,N-Dimethylformamide",
    "source": "contaminants-list",
    "license": "CC-BY-4.0",
    "inchikey": None,
    "source_native_id": "C3H7NO N,N-Dimethylformamide",
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
    """Fake the scoped lookup; record what it was asked."""
    asked = {}

    async def known_listings(formulas, *, licenses=None, ceiling=None, polarity=None):
        asked.update(
            formulas=list(formulas),
            licenses=licenses,
            ceiling=ceiling,
            polarity=polarity,
        )
        listed = {
            "C3H7NO": {"identities": [DMF], "total": 1},
            "C3H4O": {"identities": [ACROLEIN], "total": 3},
        }
        return {formula: listed[formula] for formula in formulas if formula in listed}

    monkeypatch.setattr(service.reference_service, "known_listings", known_listings)
    return asked


@pytest.mark.asyncio
async def test_the_committed_formula_is_named(lookup):
    record = await with_known_compounds(the_row())
    assert [known["name"] for known in record["known_compounds"]] == [
        "N,N-Dimethylformamide"
    ]
    assert record["known_compounds_total"] == 1


@pytest.mark.asyncio
async def test_so_is_each_alternative_a_list_holds_with_its_count(lookup):
    record = await with_known_compounds(the_row())
    same_ion, other = record["alternatives"]
    assert [known["name"] for known in same_ion["known_compounds"]] == ["Acrolein"]
    # Three records name it; the count says so past the names the read carries.
    assert same_ion["known_compounds_total"] == 3
    assert "known_compounds" not in other
    assert "known_compounds_total" not in other


@pytest.mark.asyncio
async def test_one_lookup_asks_for_every_formula_the_row_shows(lookup):
    await with_known_compounds(the_row())
    assert lookup["formulas"] == ["C3H7NO", "C3H4O", "C4H11N"]


@pytest.mark.asyncio
async def test_it_asks_as_a_run_of_the_sample_matches(lookup, monkeypatch):
    # The sample's polarity, the ceiling its run recorded and the licences the
    # deployment matches against: a compound a run could not have matched is
    # no name for its formula.
    monkeypatch.setattr(service, "reference_license_gate", lambda: ["CC-BY-4.0"])
    ceiling = KnownWindow(frozenset({"C", "H", "N", "O"}), 40, 700.0)
    await with_known_compounds(the_row(), polarity="negative", ceiling=ceiling)
    assert (lookup["licenses"], lookup["ceiling"], lookup["polarity"]) == (
        ["CC-BY-4.0"],
        ceiling,
        "negative",
    )


@pytest.mark.asyncio
async def test_a_formula_no_list_holds_is_named_by_nothing(lookup):
    record = await with_known_compounds(the_row(assigned_formula="C9H9N"))
    assert (record["known_compounds"], record["known_compounds_total"]) == ([], 0)


@pytest.mark.asyncio
async def test_a_lookup_that_fails_leaves_the_row_as_it_was(monkeypatch):
    async def known_listings(*args, **kwargs):
        raise RuntimeError("the reference mirror is not migrated")

    monkeypatch.setattr(service.reference_service, "known_listings", known_listings)
    record = await with_known_compounds(the_row())
    assert record == the_row()


@pytest.mark.asyncio
async def test_a_row_that_commits_nothing_asks_nothing(monkeypatch):
    async def known_listings(*args, **kwargs):
        raise AssertionError("nothing to name, so nothing to ask")

    monkeypatch.setattr(service.reference_service, "known_listings", known_listings)
    record = await with_known_compounds({"assigned_formula": None, "alternatives": []})
    assert "known_compounds" not in record


class TestTheRunsCeiling:
    def test_is_read_off_the_resolved_profile_the_run_recorded(self):
        window = KnownWindow(frozenset({"C", "H", "O"}), 30, 500.0)
        config = {"resolved_profile": {"known_window": window.to_json()}}
        assert _recorded_ceiling(config) == window

    def test_a_run_that_recorded_none_sets_none(self):
        assert _recorded_ceiling(None) is None
        assert _recorded_ceiling({}) is None
        assert _recorded_ceiling({"resolved_profile": {"known_window": None}}) is None

    def test_an_unreadable_record_bounds_nothing(self):
        config = {"resolved_profile": {"known_window": {"elements": ["Qq"]}}}
        assert _recorded_ceiling(config) is None
