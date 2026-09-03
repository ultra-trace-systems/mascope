"""
Unit tests for the packed storage of `PeakAssignment.alternatives`.

`CompactAlternatives` stores the untargeted finder's formula-only shortlist
entries as two-element lists and expands them on read. These pin the codec:
exactly the finder's shape packs, everything else passes through untouched in
both directions, order is kept, and the read side accepts rows written before
the packing (dict entries) as well as after it.

Pure: the type's two hooks are called directly, no database.
"""

from mascope_backend.db.models import (
    CompactAlternatives,
    pack_alternative,
    unpack_alternative,
)


_FORMULA_ONLY = {
    "assigned_formula": "C10H14O8",
    "plausibility": 1.0,
    "source": "untargeted",
}
_SCORED = {
    "assigned_formula": "C8H12O4",
    "ion_formula": "C8H12BrO4-",
    "fit_score": 0.27,
    "plausibility": 1.0,
    "source": "database",
}


def test_a_formula_only_entry_packs_to_formula_and_plausibility():
    assert pack_alternative(_FORMULA_ONLY) == ["C10H14O8", 1.0]


def test_a_null_plausibility_still_packs():
    entry = {**_FORMULA_ONLY, "plausibility": None}
    assert pack_alternative(entry) == ["C10H14O8", None]
    assert unpack_alternative(["C10H14O8", None]) == entry


def test_anything_else_passes_through():
    """A scored contender, an entry with a key of its own, a database-sourced
    formula, a bare formula without a source: none is the finder's shape."""
    for entry in (
        _SCORED,
        {**_FORMULA_ONLY, "note": "published"},
        {**_FORMULA_ONLY, "source": "database"},
        {"assigned_formula": "C7H8O", "plausibility": 0.8},
        {"assigned_formula": None, "plausibility": 0.8, "source": "untargeted"},
        None,
    ):
        assert pack_alternative(entry) is entry


def test_unpack_restores_the_dict_and_leaves_dicts_alone():
    assert unpack_alternative(["C10H14O8", 1.0]) == _FORMULA_ONLY
    assert unpack_alternative(_SCORED) is _SCORED
    # A list of any other length is not the packed shape and is left as it is.
    odd = ["C10H14O8"]
    assert unpack_alternative(odd) is odd


def test_the_column_type_round_trips_a_mixed_list_in_order():
    codec = CompactAlternatives()
    stored = codec.process_bind_param([_FORMULA_ONLY, _SCORED, _FORMULA_ONLY], None)
    assert stored == [["C10H14O8", 1.0], _SCORED, ["C10H14O8", 1.0]]
    assert codec.process_result_value(stored, None) == [
        _FORMULA_ONLY,
        _SCORED,
        _FORMULA_ONLY,
    ]


def test_the_column_type_reads_rows_written_before_the_packing():
    """A row the migration did not rewrite still holds dict entries."""
    codec = CompactAlternatives()
    legacy = [_FORMULA_ONLY, _SCORED]
    assert codec.process_result_value(legacy, None) == legacy


def test_the_column_type_passes_null_through_both_ways():
    codec = CompactAlternatives()
    assert codec.process_bind_param(None, None) is None
    assert codec.process_result_value(None, None) is None
