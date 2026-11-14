"""Which line of a target ion seeds its batch peak: the monoisotopic one the
engine counts from (``engine.monoisotopic_row``), which is not always the
lightest - a labelled ion's unlabelled remainder sits one unit below it."""

from types import SimpleNamespace

from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    _monoisotopic_mz,
)


def _rows(ion, lines):
    return [
        SimpleNamespace(target_ion_formula=ion, target_isotope_formula=f, mz=mz)
        for f, mz in lines
    ]


def test_an_unlabelled_ion_seeds_at_its_line_without_a_bracket():
    rows = _rows(
        "Br3-", [("[81Br]Br2-", 238.7), ("Br3-", 236.7), ("[81Br]2Br-", 240.7)]
    )
    assert _monoisotopic_mz(rows) == 236.7


def test_a_labelled_ion_seeds_at_its_labelled_line_not_the_lighter_remainder():
    rows = _rows(
        "C9H16O7^N-",
        [("C9H16NO7-", 250.0932), ("[15N]C9H16O7-", 251.0903)],
    )
    assert _monoisotopic_mz(rows) == 251.0903


def test_where_no_formula_names_the_monoisotopic_line_the_lightest_stands():
    rows = _rows(None, [("[13C]CH5+", 31.0), ("[2H]CH4+", 30.9)])
    assert _monoisotopic_mz(rows) == 30.9
