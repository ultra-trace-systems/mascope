"""What a source row records about how its compounds may be matched.

The window is stored as JSON with every axis written, so an unbounded axis and
a bounded one read back the same way. What an adapter writes depends on what the
source is: a database at the mirror window, a list someone authored unbounded,
and a list file with the radical allowance and polarity its header names. The
flags a person passes at load time go over any of them, one axis at a time.
"""

import json
from pathlib import Path

import pytest

from mascope_reference.adapters.custom import CustomAdapter
from mascope_reference.adapters.peaklist import PeakListAdapter
from mascope_reference.adapters.pubchem import PubChemAdapter
from mascope_reference.scope import (
    MIRROR_WINDOW,
    UNBOUNDED,
    KnownWindow,
    SourceScope,
    parse_elements,
    scope_of,
)


def test_the_mirror_window_is_the_one_stage_a_bounded_every_source_to():
    assert MIRROR_WINDOW.to_json() == {
        "elements": ["C", "H", "N", "O", "S"],
        "max_carbon": 40,
        "max_mass": 700.0,
    }


@pytest.mark.parametrize(
    "window",
    [
        MIRROR_WINDOW,
        UNBOUNDED,
        KnownWindow(elements=frozenset({"C", "H", "O", "Si"})),
        KnownWindow(max_carbon=12),
        KnownWindow(max_mass=450.0),
    ],
    ids=["mirror", "unbounded", "elements-only", "carbon-only", "mass-only"],
)
def test_a_window_reads_back_as_it_was_stored(window):
    # Through a JSON round trip, as the column gives it back.
    stored = json.loads(json.dumps(window.to_json()))
    assert KnownWindow.from_json(stored) == window


def test_an_unbounded_axis_is_written_as_null_not_left_out():
    assert UNBOUNDED.to_json() == {
        "elements": None,
        "max_carbon": None,
        "max_mass": None,
    }
    assert UNBOUNDED.is_unbounded and not MIRROR_WINDOW.is_unbounded


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"elements": frozenset({"C", "Xx"})}, "Xx"),
        ({"max_carbon": -1}, "negative"),
        ({"max_mass": 0.0}, "positive"),
    ],
)
def test_a_window_refuses_what_no_formula_could_meet(kwargs, message):
    with pytest.raises(ValueError, match=message):
        KnownWindow(**kwargs)


def test_a_stored_value_that_is_not_a_window_is_refused():
    with pytest.raises(ValueError, match="object"):
        KnownWindow.from_json(["C", "H"])


def test_a_polarity_the_row_cannot_record_is_refused():
    # A list may say it was measured in both; the row says so by recording none.
    with pytest.raises(ValueError, match="both"):
        SourceScope(MIRROR_WINDOW, polarity="both")


def test_a_scope_writes_its_three_columns():
    scope = SourceScope(UNBOUNDED, allow_radicals=True, polarity="negative")
    assert scope.row_values() == {
        "known_window": UNBOUNDED.to_json(),
        "allow_radicals": True,
        "polarity": "negative",
    }


def test_the_description_names_every_axis_and_the_allowance():
    assert (
        SourceScope(MIRROR_WINDOW).describe()
        == "C, H, N, O, S; C <= 40; <= 700 Da; no radicals; both polarities"
    )
    assert (
        SourceScope(UNBOUNDED, allow_radicals=True, polarity="positive").describe()
        == "unbounded; radicals allowed; positive"
    )
    assert (
        KnownWindow(elements=frozenset({"Si", "C", "O", "H"})).describe()
        == "C, H, O, Si; any carbon count; any mass"
    )


# --- Overrides at load time -----------------------------------------------------


def test_a_flag_sets_one_axis_and_leaves_the_others():
    scope = SourceScope(MIRROR_WINDOW).overridden(elements="C,H,N,O,S,Si,P")
    assert scope.known_window == KnownWindow(
        frozenset({"C", "H", "N", "O", "S", "Si", "P"}), 40, 700.0
    )
    assert scope.allow_radicals is False and scope.polarity is None


def test_the_token_lifts_a_bound():
    scope = SourceScope(MIRROR_WINDOW).overridden(
        elements="any", max_carbon="any", max_mass="ANY"
    )
    assert scope.known_window == UNBOUNDED


def test_a_flag_bounds_an_unbounded_list():
    scope = SourceScope(UNBOUNDED, allow_radicals=True, polarity="negative").overridden(
        max_carbon="20", max_mass="350.5", allow_radicals=False
    )
    assert scope == SourceScope(
        KnownWindow(max_carbon=20, max_mass=350.5),
        allow_radicals=False,
        polarity="negative",
    )


def test_an_unset_radical_flag_keeps_the_sources_own():
    allowed = SourceScope(UNBOUNDED, allow_radicals=True)
    assert allowed.overridden(elements="C,H,O").allow_radicals is True
    assert SourceScope(UNBOUNDED).overridden(allow_radicals=True).allow_radicals is True


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"elements": " , "}, "at least one element"),
        ({"elements": "C,H,Q"}, "Q"),
        ({"max_carbon": "forty"}, "carbon cap"),
        ({"max_mass": "heavy"}, "mass cap"),
        ({"max_carbon": "-3"}, "negative"),
    ],
)
def test_a_flag_that_cannot_be_read_is_refused(kwargs, message):
    with pytest.raises(ValueError, match=message):
        SourceScope(MIRROR_WINDOW).overridden(**kwargs)


def test_element_symbols_are_read_as_written():
    assert parse_elements("C, H ,Cl,Br") == frozenset({"C", "H", "Cl", "Br"})
    # Case is part of the symbol: "CL" is not chlorine.
    with pytest.raises(ValueError, match="CL"):
        parse_elements("C,CL")


# --- What each kind of source writes ----------------------------------------------


def test_a_database_mirror_is_scoped_at_the_mirror_window(tmp_path):
    assert scope_of(PubChemAdapter(), tmp_path / "dump.sdf") == SourceScope(
        MIRROR_WINDOW
    )


def test_a_hand_authored_list_is_scoped_unbounded(tmp_path):
    assert scope_of(CustomAdapter(), tmp_path / "list.csv") == SourceScope(UNBOUNDED)


def _list_file(tmp_path, **header) -> Path:
    data = {
        "schema_version": 2,
        "id": "a-list",
        "label": "A list",
        "data_version": "v1",
        "license": "CC-BY-4.0",
        "references": [{"citation": "A paper.", "doi": "10.1234/t.1"}],
        "species": [{"formula": "C10H16O3"}],
        **header,
    }
    path = tmp_path / "a-list.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "header, expected",
    [
        ({}, SourceScope(UNBOUNDED)),
        (
            {"allow_radicals": True, "polarity": "negative"},
            SourceScope(UNBOUNDED, allow_radicals=True, polarity="negative"),
        ),
        ({"polarity": "positive"}, SourceScope(UNBOUNDED, polarity="positive")),
        ({"polarity": "both"}, SourceScope(UNBOUNDED)),
    ],
    ids=["silent", "radicals-negative", "positive", "both"],
)
def test_a_list_file_is_scoped_as_its_header_says(tmp_path, header, expected):
    assert scope_of(PeakListAdapter(), _list_file(tmp_path, **header)) == expected
