"""Unit tests for what a reference list calls a row's formula, flattened.

Step 3.4d of the assignment quality plan: the ledgers show beside the formula
the name and list of the identity a row matched, as the inspector's *listed as*
field shows it. A ledger row serves no provenance, so the row carries one
flattened field; the batch ledger reads the same off the registry entry its
consensus names. See ``listing.py``.
"""

import pytest

from mascope_backend.api.new.peak_assignments.batch_peaks import candidate_index
from mascope_backend.api.new.peak_assignments.fold_view import member_row
from mascope_backend.api.new.peak_assignments.listing import (
    consensus_listing,
    reference_listing,
)
from mascope_backend.api.new.peak_assignments.service import _provenance_scalars


def _identity(name, source, tags=None):
    """An identity as a run records it (``asdict(KnownIdentity)``)."""
    return {
        "name": name,
        "source": source,
        "license": "CC0-1.0",
        "inchikey": None,
        "source_native_id": name,
        "xrefs": {"tags": tags} if tags is not None else {},
    }


D5 = _identity("decamethylcyclopentasiloxane", "cyclic-siloxanes", ["background"])
PINENE = _identity("alpha-pinene", "monoterpenes")
CAMPHENE = _identity("camphene", "monoterpenes")


# --- reference_listing ----------------------------------------------------------


def test_the_first_identity_names_the_listing():
    assert reference_listing([PINENE, CAMPHENE]) == {
        "name": "alpha-pinene",
        "source": "monoterpenes",
        "tags": [],
        "total": 2,
    }


def test_a_tagged_list_carries_its_tag():
    assert reference_listing([D5]) == {
        "name": "decamethylcyclopentasiloxane",
        "source": "cyclic-siloxanes",
        "tags": ["background"],
        "total": 1,
    }


def test_the_tags_are_every_listing_lists_each_once():
    """As the inspector unions them: a tag is the list's reading, so a second
    list naming the formula brings its own."""
    other = _identity("D5 (NIST)", "contaminants", ["background", "other"])
    assert reference_listing([PINENE, D5, other])["tags"] == ["background", "other"]


@pytest.mark.parametrize(
    "identities", [None, [], "D5", {"name": "D5"}, [None, "D5"]], ids=repr
)
def test_a_row_no_list_names_has_no_listing(identities):
    assert reference_listing(identities) is None


def test_an_unnamed_record_still_names_its_list():
    listing = reference_listing([_identity(None, "mirror")])
    assert listing["name"] is None and listing["source"] == "mirror"


# --- on the sample ledger -------------------------------------------------------


def test_the_ledger_row_carries_the_listing_of_the_identities_it_matched():
    scalars = _provenance_scalars(
        {"evidence": 0.8, "reference_identities": [D5, PINENE]}, None
    )
    assert scalars["reference_listing"] == {
        "name": "decamethylcyclopentasiloxane",
        "source": "cyclic-siloxanes",
        "tags": ["background"],
        "total": 2,
    }


@pytest.mark.parametrize(
    "provenance",
    [None, {}, {"evidence": 0.8}, {"reference_identities": None}],
    ids=["no provenance", "empty", "a search's row", "a target-library row"],
)
def test_a_row_no_list_names_carries_none(provenance):
    assert _provenance_scalars(provenance, None)["reference_listing"] is None


# --- on the batch registry --------------------------------------------------------


LISTED = reference_listing([D5])


def test_a_new_entry_records_the_listing_its_member_brought():
    registry = []
    index = candidate_index(
        registry, "C10H30O5Si5", "C9H27O5Si5+", "m-ch3", "database", listing=LISTED
    )
    assert registry[index]["listing"] == LISTED


def test_an_entry_the_search_brought_gains_the_listing_a_list_match_brings():
    """The search's member reached the identity first; a later member matched it
    from a list, which says the list holds it. The entry is replaced, not
    written into, so a registry copied out of a loaded row leaves the row's own
    value alone and compares unequal to it - which is how the fold knows to
    write it back."""
    stored = [
        {
            "formula": "C10H30O5Si5",
            "ion_formula": "C9H27O5Si5+",
            "ionization_mechanism_id": "m-ch3",
            "source": "untargeted",
        }
    ]
    registry = list(stored)
    index = candidate_index(
        registry, "C10H30O5Si5", "C9H27O5Si5+", "m-ch3", "database", listing=LISTED
    )
    assert index == 0
    assert registry[0]["listing"] == LISTED
    # The source stays the first member's.
    assert registry[0]["source"] == "untargeted"
    assert "listing" not in stored[0]
    assert registry != stored


def test_an_entry_keeps_the_listing_it_has():
    first = reference_listing([PINENE])
    registry = [
        {
            "formula": "C10H16",
            "ion_formula": "C10H17+",
            "ionization_mechanism_id": "m-h",
            "listing": first,
        }
    ]
    candidate_index(registry, "C10H16", "C10H17+", "m-h", listing=LISTED)
    assert registry[0]["listing"] == first


def test_a_member_with_no_listing_leaves_the_registry_as_it_was():
    stored = [
        {
            "formula": "C10H16",
            "ion_formula": "C10H17+",
            "ionization_mechanism_id": "m-h",
        }
    ]
    registry = list(stored)
    candidate_index(registry, "C10H16", "C10H17+", "m-h", "untargeted")
    assert registry == stored


# --- the listing a consensus shows -------------------------------------------------


def _entry(formula, ion, mechanism, listing=None):
    entry = {
        "formula": formula,
        "ion_formula": ion,
        "ionization_mechanism_id": mechanism,
    }
    if listing is not None:
        entry["listing"] = listing
    return entry


def test_the_consensus_shows_its_own_entrys_listing():
    other = reference_listing([_identity("D5 (NIST)", "contaminants")])
    registry = [
        _entry("C10H30O5Si5", "C10H31O5Si5+", "m-h", other),
        _entry("C10H30O5Si5", "C9H27O5Si5+", "m-ch3", LISTED),
    ]
    assert consensus_listing(registry, "C10H30O5Si5", "C9H27O5Si5+", "m-ch3") == LISTED


def test_a_list_names_the_formula_whichever_channel_matched_it():
    """The consensus reads the methyl-loss ion; the one member that matched the
    compound from the list did so through proton transfer. The list holds the
    formula all the same."""
    registry = [
        _entry("C10H30O5Si5", "C9H27O5Si5+", "m-ch3"),
        _entry("C10H30O5Si5", "C10H31O5Si5+", "m-h", LISTED),
    ]
    assert consensus_listing(registry, "C10H30O5Si5", "C9H27O5Si5+", "m-ch3") == LISTED


def test_another_formulas_listing_is_not_the_consensus_one():
    registry = [_entry("C10H16", "C10H17+", "m-h", reference_listing([PINENE]))]
    assert consensus_listing(registry, "C9H14O", "C9H15O+", "m-h") is None


@pytest.mark.parametrize("candidates", [None, [], "x", [None, 3]], ids=repr)
def test_a_registry_with_nothing_to_read_names_no_listing(candidates):
    assert consensus_listing(candidates, "C10H16") is None


def test_an_unassigned_consensus_names_no_listing():
    registry = [_entry("C10H16", "C10H17+", "m-h", LISTED)]
    assert consensus_listing(registry, None) is None


# --- on a row derived from the batch ledger ---------------------------------------


class _Member:
    batch_peak_id = "bp-1"
    sample_item_id = "si-1"
    sample_peak_id = "p1"
    mz_delta_ppm = 0.0
    intensity = 100.0
    tier = 0
    role = 1
    fit_score = 0.9
    candidate = 0
    owner_batch_peak_id = None
    p_correct = None


class _Anchor:
    mz = 371.1012
    candidates = [_entry("C10H30O5Si5", "C10H31O5Si5+", "m-h", LISTED)]


def test_a_derived_row_carries_its_entrys_listing():
    assert member_row(_Member(), _Anchor())["reference_listing"] == LISTED


def test_a_derived_row_whose_entry_names_no_list_carries_none():
    anchor = _Anchor()
    anchor.candidates = [_entry("C10H16", "C10H17+", "m-h")]
    assert member_row(_Member(), anchor)["reference_listing"] is None
