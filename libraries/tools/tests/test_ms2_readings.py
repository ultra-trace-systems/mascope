"""The MS2 composition core, `mascope_tools.composition.ms2.readings`.

Ported from peaky (https://github.com/ultra-trace-systems/peaky, main at
aa119ac): `tests/test_ms2_subformula.py` (ion parsing, electron parity, the
loss decomposition, the per-parent candidates and their ranking, the
recalibration, the readings table) and the readings and ladder-gate tests of
`tests/test_ms2_breakdown.py`. Each test names the peaky test it ports; the
numbers are peaky's, the formulas in Mascope's notation (15N as `[15N]`).

Measured cases are peaky's own lab data, inlined there and here: (A) its
exploratory 15N-ammonium file, energy-averaged MS2 of seven precursors, read
from the readings that day's script committed; (B) two monoterpene standards
in 15N-ammonium mode. (C) is synthetic exact-mass chemistry. (D) is peaky's
readings of its three public fixtures (`tests/fixtures/ms2`), every peak of
every window, re-read here from `fixtures/ms2/peaky_readings.json`, which
`fixtures/ms2/make_peaky_readings.py` writes with peaky's own code.
"""

import dataclasses
import functools
import itertools
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from mascope_tools.composition import ms2 as M
from mascope_tools.composition.config import (
    ELECTRON_MASS,
    UNSATURATION_COEFFICIENTS,
)
from mascope_tools.composition.ms2 import readings as R
from mascope_tools.composition.utils import to_hill_order


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ms2"
DOC = Path(__file__).resolve().parents[1] / "docs" / "ms2.md"
NAN = float("nan")


def _mz(ion: str) -> float:
    return M.ion_mz(ion)


def _rows(readings, peak, parent=None, spectrum=None):
    return [
        r
        for r in readings
        if r.peak == peak
        and (parent is None or r.parent == parent)
        and (spectrum is None or r.spectrum == spectrum)
    ]


def _top(readings, peak, parent, spectrum=None):
    rows = _rows(readings, peak, parent, spectrum)
    return rows[0] if rows else None


def _col(readings, field):
    return [getattr(r, field) for r in readings]


def _dbe(counts) -> float:
    """DBE of a formula, written out independently of the module."""
    c = counts
    return (
        1
        + c.get("C", 0)
        + c.get("Si", 0)
        + (c.get("N", 0) + c.get("[15N]", 0) + c.get("P", 0)) / 2
        - sum(c.get(e, 0) for e in ("H", "F", "Cl", "Br", "I")) / 2
    )


def _senior_cap(counts) -> float:
    c = counts
    return c.get("C", 0) + c.get("Si", 0) + (c.get("N", 0) + c.get("[15N]", 0)) / 2 + 1


def _counts(formula: str) -> dict[str, int]:
    return R._parse_body(formula, formula)


# ------------------------------------------------------------------ (A) ----
# peaky's exploratory 15N-ammonium file, positive mode, [15N]H4+ reagent,
# energy-averaged MS2 of seven precursors. Per precursor: (m/z, top formula,
# loss formula) as that day's script read it, or (m/z, None, None) where it read
# nothing from that precursor. m/z on the precursor-recalibrated axis, rounded
# to 0.1 mDa as recorded. peaky: FILE_0910.
FILE_A = {
    "C7H6NS+": [
        (136.0215, "C7H6NS+", ""),
        (88.0215, None, None),
        (109.0106, "C6H5S+", "CHN"),
        (136.0249, None, None),
        (95.0491, None, None),
        (118.0579, None, None),
        (65.0385, "C5H5+", "C2HNS"),
        (105.0447, None, None),
        (136.0816, None, None),
        (136.0757, None, None),
        (100.0474, None, None),
    ],
    "C4H10[15N]O4+": [
        (137.0575, "C4H10[15N]O4+", ""),
        (137.0597, None, None),
        (137.0249, None, None),
        (119.0702, None, None),
        (119.0338, "C4H7O4+", "H3[15N]"),
        (137.0939, None, None),
        (137.1302, None, None),
        (137.0727, None, None),
        (119.1066, None, None),
        (119.0468, "C4H8[15N]O3+", "H2O"),
        (119.0855, None, None),
        (101.0596, None, None),
        (137.1325, None, None),
        (137.0961, None, None),
        (137.0209, None, None),
    ],
    "C11H13O+": [
        (161.0961, "C11H13O+", ""),
        (43.0177, "C2H3O+", "C9H10"),
        (133.0647, "C9H9O+", "C2H4"),
        (143.0702, None, None),
        (143.1066, None, None),
        (161.0941, None, None),
        (161.1302, None, None),
        (161.1325, None, None),
        (119.0855, "C9H11+", "C2H2O"),
        (125.0960, None, None),
        (161.0808, None, None),
        (125.0596, None, None),
        (161.1006, None, None),
        (161.0916, None, None),
        (143.0855, "C11H11+", "H2O"),
    ],
    "C11H15O2+": [
        (179.1067, "C11H15O2+", ""),
        (161.0960, "C11H13O+", "H2O"),
        (59.0490, "C3H7O+", "C8H8O"),
        (119.0854, "C9H11+", "C2H4O2"),
        (121.0647, "C8H9O+", "C3H6O"),
        (179.0636, None, None),
        (43.0177, "C2H3O+", "C9H12O"),
        (133.0647, "C9H9O+", "C2H6O"),
        (91.0541, "C7H7+", "C4H8O2"),
        (161.0808, None, None),
        (179.1340, None, None),
        (179.1014, None, None),
        (179.1195, None, None),
        (143.0702, None, None),
        (179.0703, None, None),
        (179.1119, None, None),
        (179.1000, None, None),
        (179.1087, None, None),
        (179.1317, None, None),
        (179.1429, None, None),
        (179.0388, None, None),
        (161.0917, None, None),
        (43.0171, None, None),
        (179.0855, None, None),
    ],
    "C11H18[15N]O2+": [
        (179.1066, "C11H15O2+", "H3[15N]"),
        (197.1302, "C11H18[15N]O2+", ""),
        (161.0961, "C11H13O+", "H5[15N]O"),
        (179.1196, "C11H16[15N]O+", "H2O"),
        (197.0961, None, None),
        (197.1536, None, None),
        (197.1173, None, None),
        (197.0809, None, None),
        (197.1900, None, None),
    ],
    "C12H22[15N]O2+": [
        (177.1274, "C12H17O+", "H5[15N]O"),
        (213.1615, "C12H22[15N]O2+", ""),
        (195.1510, "C12H20[15N]O+", "H2O"),
        (177.1404, "C12H18[15N]+", "H4O2"),
        (213.1849, None, None),
        (213.1486, None, None),
        (213.1122, None, None),
        (159.1168, "C12H15+", "H7[15N]O2"),
    ],
    "C8H28[15N]O4Si4+": [
        (297.0825, "C8H25O4Si4+", "H3[15N]"),
        (315.1060, "C8H28[15N]O4Si4+", ""),
        # 4.8 ppm low on the recalibrated axis: inside 5 ppm, as it was inside
        # the 6 ppm of that day's script
        (281.0498, "C7H21O4Si4+", "CH7[15N]"),
    ],
}


@pytest.mark.parametrize("precursor", list(FILE_A))
def test_a_every_reading_of_the_exploratory_file_is_reproduced(precursor):
    """peaky: test_a_every_reading_of_the_0910_file_is_reproduced."""
    rows = FILE_A[precursor]
    rd = M.read_fragments([mz for mz, _, _ in rows], {"target": precursor})
    assert sorted({r.peak for r in rd}) == list(range(len(rows)))
    for i, (mz, formula, loss) in enumerate(rows):
        top = _top(rd, i, "target")
        if formula is None:
            # read nothing then -> no reading from this precursor now either
            assert top is None, (mz, top)
            continue
        assert top is not None, (mz, formula)
        assert (top.formula, top.loss) == (formula, loss), (mz, top)
        assert top.rank == 1 and top.parity == "EE"
        assert top.is_precursor == (loss == "")
        if loss == "":
            assert (top.loss_name, top.loss_pieces, top.unnamed_pieces) == ("", 0, 0)
        else:
            assert top.loss_pieces >= 1
        assert top.parent_ion == precursor
        assert abs(top.ppm) < 5.0


def test_a_loss_decompositions_are_formulas_in_library_order():
    """peaky: test_a_loss_decompositions_are_formulas_in_library_order."""
    want = {
        ("C11H18[15N]O2+", 161.0961): ("H2O + H3[15N]", 2, 0),
        ("C12H22[15N]O2+", 159.1168): ("H2O + H2O + H3[15N]", 3, 0),
        ("C12H22[15N]O2+", 177.1404): ("H2O + H2O", 2, 0),
        ("C8H28[15N]O4Si4+", 281.0498): ("H3[15N] + CH4", 2, 0),
        ("C7H6NS+", 109.0106): ("CHN", 1, 0),
        # one unnamed piece beats two named ones: fewest pieces first
        ("C11H15O2+", 43.0177): ("C9H12O", 1, 1),
        ("C11H15O2+", 91.0541): ("C4H8O2", 1, 1),
        ("C7H6NS+", 65.0385): ("C2HNS", 1, 1),
    }
    for (parent, mz), (name, k, unnamed) in want.items():
        top = M.fragment_candidates(parent, mz)[0]
        assert (top.loss_name, top.loss_pieces, top.unnamed_pieces) == (
            name,
            k,
            unnamed,
        ), (parent, mz, top)


def test_a_bare_carbon_loss_is_refused_and_the_co_isolate_reads_the_peak():
    """88.0215 = C3H6NS+ fits C7H6NS+ minus C4 at -0.5 ppm, but no neutral C4
    leaves an ion: the 0.4 Da window also held C4H10NS2+ (136.0248, 13 % of the
    window signal), and that ion minus CH4S IS C3H6NS+. peaky:
    test_a_bare_carbon_loss_is_not_a_reading_the_co_isolate_is."""
    window = {"target": "C7H6NS+", "co1": "C4H10NS2+", "co2": "C8H10NO+"}
    rows = FILE_A["C7H6NS+"]
    rd = M.read_fragments([mz for mz, _, _ in rows], window)
    p88 = _rows(rd, 1)
    assert _col(p88, "parent") == ["co1"]
    r = p88[0]
    assert (r.formula, r.loss, r.loss_name, r.n_parents) == (
        "C3H6NS+",
        "CH4S",
        "CH4S",
        1,
    )
    assert M.decompose_loss("C4") is None
    # and C7H6NS+ - C4 is no candidate of the target even alone
    assert M.fragment_candidates("C7H6NS+", 88.0215) == []
    # the window ions are their own precursor readings
    assert _top(rd, 3, "co1").is_precursor and _top(rd, 9, "co2").is_precursor
    # 65.0385 = C5H5+ is a sub-formula of the target AND of C8H10NO+: both kept
    p65 = _rows(rd, 6)
    assert _col(p65, "parent") == ["target", "co2"]
    assert set(_col(p65, "n_parents")) == {2}
    assert _col(p65, "loss") == ["C2HNS", "C3H5NO"]


def test_a_an_ammonium_cluster_of_a_saturated_molecule_is_its_own_precursor():
    """C6H14O2 + [15N]H4+ = C6H18[15N]O2+ has ion DBE -1.5, below the fragment
    gate, yet it was a co-isolate of the 137.057 window (137.1302, 10 % of the
    signal). A parent is an input: it is never gated, and its -H3[15N] product
    (C6H15O2+, DBE -0.5) is an ordinary fragment. peaky:
    test_a_an_ammonium_cluster_of_a_saturated_molecule_is_its_own_precursor."""
    assert not M.fragment_ok("C6H18[15N]O2+")
    window = {"target": "C4H10[15N]O4+", "C6": "C6H18[15N]O2+"}
    rd = M.read_fragments([137.1302, 119.1066], window)
    assert _top(rd, 0, "C6").is_precursor
    r = _top(rd, 1, "C6")
    assert (r.formula, r.loss) == ("C6H15O2+", "H3[15N]")
    assert _top(rd, 1, "target") is None


# ------------------------------------------------------------------ (B) ----
# Two monoterpene standards in 15N-ammonium mode. [M+H]+ C10H17+ energy-averaged
# MS2 (the first standard), and the [M+[15N]H4]+ window at m/z 155 (C10H20[15N]+
# plus the co-isolated C10H19O+ at 155.143). peaky: MT_137.
MT_137 = [  # (m/z, fragment, loss name); None: a window ion, never a fragment
    (137.1325, "C10H17+", ""),
    (81.0698, "C6H9+", "C4H8"),
    (95.0854, "C7H11+", "C3H6"),
    (137.0586, None, None),
    (137.0246, None, None),
    (119.0701, None, None),
    (119.0338, None, None),
    (67.0541, "C5H7+", "C5H10"),
    (101.0596, None, None),
    (119.1066, None, None),
    (57.0698, "C4H9+", "C6H8"),
    (119.0468, None, None),
    (119.0854, "C9H11+", "H2 + CH4"),
    (101.0232, None, None),
    (69.0697, "C5H9+", "C5H8"),
]


def test_b_monoterpene_protonated_fragments():
    """C10H17+ gives C6H9+, C7H11+, C5H7+, C4H9+, C5H9+ and C9H11+. peaky:
    test_b_monoterpene_protonated_fragments."""
    peaks = {
        "mz": [m for m, _, _ in MT_137],
        "intensity": np.arange(len(MT_137), dtype=float),
    }
    rd = M.read_fragments(peaks, "C10H17+")
    assert set(_col(rd, "parent")) <= {"C10H17+", ""}
    for i, (mz, formula, name) in enumerate(MT_137):
        top = _top(rd, i, "C10H17+")
        if formula is None:
            assert top is None, (mz, top)
            row = _rows(rd, i)[0]
            assert (row.n_candidates, row.n_parents, row.formula) == (0, 0, "")
            continue
        assert (top.formula, top.loss_name) == (formula, name), (mz, top)
        assert abs(top.ppm) < 3.0 and not top.radical_loss
        assert top.intensity == float(i)
    # C4H8 / C3H6 are library pieces; C5H10 / C6H8 are single unnamed pieces;
    # CH6 (DBE -1) needs two molecules
    r = _top(rd, 12, "C10H17+")
    assert (r.loss, r.loss_pieces, r.unnamed_pieces) == ("CH6", 2, 0)
    for i, unnamed in ((1, 0), (2, 0), (7, 1), (10, 1)):
        assert _top(rd, i, "C10H17+").unnamed_pieces == unnamed, MT_137[i]


def test_b_monoterpene_ammonium_adduct_window():
    """The adduct gives C10H17+ by loss of H3 and the labelled N. Its formula is
    given in peaky's caret notation and read as Mascope's. peaky:
    test_b_monoterpene_ammonium_adduct_window."""
    window = {"adduct": "C10H20^N+", "water cluster": "C10H19O+"}
    peaks = [
        155.15606,
        137.13246,
        155.14299,
        81.06979,
        95.08546,
        109.10112,
        155.07026,
        155.10663,
    ]
    rd = M.read_fragments(peaks, window)
    r = _top(rd, 1, "adduct")
    assert (r.parent_ion, r.formula, r.loss, r.loss_name) == (
        "C10H20[15N]+",
        "C10H17+",
        "H3[15N]",
        "H3[15N]",
    )
    assert r.pieces == ("H3[15N]",)
    assert _top(rd, 1, "water cluster").loss == "H2O"
    # 137 / 81 / 95 / 109 are sub-formulas of both window ions: composition
    # cannot attribute them, and both readings are kept
    for i in (1, 3, 4, 5):
        assert set(_col(_rows(rd, i), "n_parents")) == {2}
    assert _top(rd, 0, "adduct").is_precursor
    assert _top(rd, 2, "water cluster").is_precursor
    for i in (6, 7):  # unread window ions
        assert len(_rows(rd, i)) == 1 and _top(rd, i, "") is not None


# ------------------------------------------------------------------ (C) ----
def test_ion_parsing_and_formatting():
    """peaky: test_ion_parsing_and_formatting, plus the notation."""
    assert M.parse_ion("C10H17O+") == ({"C": 10, "H": 17, "O": 1}, 1)
    assert M.parse_ion("C10H17O5-") == ({"C": 10, "H": 17, "O": 5}, -1)
    labelled = ({"C": 4, "H": 10, "[15N]": 1, "O": 4}, 1)
    assert M.parse_ion("C4H10^NO4+") == M.parse_ion("C4H10[15N]O4+") == labelled
    assert M.parse_ion("C7H8+.") == M.parse_ion("C7H8+\u2022") == ({"C": 7, "H": 8}, 1)
    assert M.format_ion({"C": 6, "H": 9}, 1) == "C6H9+"
    assert M.format_ion({"[15N]": 1, "O": 3}, -1) == "[15N]O3-"
    assert M.format_ion({"^N": 1, "O": 3}, -1) == "[15N]O3-"
    # Mascope's Hill order (utils.to_hill_order): [15N] before N
    assert M.format_ion(M.parse_ion("C6H17N^NO5+")[0], 1) == "C6H17[15N]NO5+"
    for bad in (
        "C10H17O",
        "C10H17O++",
        "C10H17O2+2",
        "2+",
        "+",
        "",
        "C9Xx+",
        "C7H7+.",  # a radical mark on an even-electron ion
        "c10h17o+",
        "C9[13C]H17+",  # no isotope but 15N is read
        "C9[14N]H17+",
        "C9H17^C+",  # no caret label but ^N
    ):
        with pytest.raises(ValueError):
            M.parse_ion(bad)
    with pytest.raises(ValueError):
        M.format_ion({"C": 6, "H": 9}, 2)


def test_ion_mass_and_parity():
    """peaky: test_ion_mass_and_parity (chemistry.ion_mass is ion_mz here)."""
    assert _mz("^NO3-") == pytest.approx(62.98540, abs=1e-5)
    assert _mz("[15N]O3-") == _mz("^NO3-")
    assert _mz("CH5N2O+") == pytest.approx(61.03964, abs=1e-5)
    assert _mz("C2H3O+") == pytest.approx(43.01784, abs=1e-5)
    # an anion sits two electron masses above the cation of its formula
    assert _mz("C2H3O-") - _mz("C2H3O+") == pytest.approx(2 * ELECTRON_MASS, abs=1e-12)
    # two spellings of one ion are one float
    assert _mz("H3[15N]O+") == _mz("[15N]H3O+") == _mz("OH3^N+")
    with pytest.raises(ValueError):
        _mz("C2H3O")
    assert M.ion_parity("C10H17O+") == "EE"  # DBE 2.5
    assert M.ion_parity("C10H17O5-") == "EE"  # 2.5
    assert M.ion_parity("C7H8+.") == "OE"  # 4.0
    assert M.fragment_ok("H3O+") and M.fragment_ok("H4N+")  # DBE -0.5
    assert not M.fragment_ok("H3+") and not M.fragment_ok("C10H40+")
    assert not M.fragment_ok("P2+")  # DBE 2 > Senior cap 1 + 1/2 (P adds no cap)
    assert not M.fragment_ok("H6[15N]O+")  # [15N]H4+.H2O, DBE -1.5
    assert M.fragment_ok("C2H7Si2+")  # Si counts in the cap
    with pytest.raises(ValueError):
        M.fragment_ok({"C": 2, "O": -1})


def _all_subformulas(ion: str) -> set[str]:
    """Every element-wise sub-formula of an ion, by plain iteration (no numpy)."""
    counts, charge = M.parse_ion(ion)
    els = list(counts)
    out = set()
    for sub in itertools.product(*[range(counts[e] + 1) for e in els]):
        c = {e: n for e, n in zip(els, sub) if n}
        if c:
            out.add(M.format_ion(c, charge))
    return out


def test_fragment_gate_is_chemistry_and_the_grid_uses_it():
    """fragment_ok, read against a DBE and a Senior cap written out here, on
    random formulas; and every parent's sub-formula grid is exactly the
    sub-formulas fragment_ok accepts, plus the parent itself. peaky:
    test_fragment_gate_is_chemistry_and_the_grid_uses_it."""
    rng = np.random.default_rng(7)
    for _ in range(3000):
        counts = {e: int(rng.integers(0, 4)) for e in M.ELEMENTS if rng.random() < 0.35}
        counts["H"] = int(rng.integers(0, 24))
        counts = {e: n for e, n in counts.items() if n}
        if not counts:
            continue
        d = _dbe(counts)
        ee = d != int(d)
        want = (
            sum(n for e, n in counts.items() if e != "H") > 0
            and d >= (-0.5 if ee else 0.0)
            and d <= _senior_cap(counts) + 0.5
        )
        assert M.fragment_ok(counts) == want, counts
        assert M.ion_parity(counts) == ("EE" if ee else "OE")
    for parent in (
        "C6H18[15N]O2+",
        "C8H28[15N]O4Si4+",
        "C7H8+.",
        "C4H10NS2+",
        "C4H11O4P2+",
        "C3H9ClOSi2+",
        "C5H9[15N]O3-",
        "C10H18[15N]O7-",
        "C4H6BrF2IO+",
        "C10H16NO7-",
    ):
        counts, charge = M.parse_ion(parent)
        grid = R._parent_grid(R._ion_key(counts), charge)
        formulas = [
            M.format_ion({e: int(n) for e, n in zip(grid.els, row) if n}, charge)
            for row in grid.sub
        ]
        want = {f for f in _all_subformulas(parent) if M.fragment_ok(f)}
        assert set(formulas) == want | {M.format_ion(counts, charge)}, parent
        assert len(formulas) == len(set(formulas))  # no duplicate rows
        assert list(grid.t2) == [round(2 * _dbe(M.parse_ion(f)[0])) for f in formulas]
        assert np.allclose(grid.mass, [_mz(f) for f in formulas], rtol=0, atol=1e-9)
    # the gate removes fragments, never the parent: the cluster's own
    # ammonium-water fragment (DBE -1.5) is not a reading
    assert M.fragment_candidates("C6H18[15N]O2+", _mz("H6[15N]O+")) == []
    assert [
        c.formula for c in M.fragment_candidates("C6H18[15N]O2+", _mz("C6H18[15N]O2+"))
    ] == ["C6H18[15N]O2+"]


def test_valences_are_the_unsaturation_coefficients():
    """The DBE this module counts is Mascope's: 2 x DBE per atom is v - 2, which
    is `config.UNSATURATION_COEFFICIENTS` for every element both know."""
    shared = [e for e in M.VALENCES if e in UNSATURATION_COEFFICIENTS]
    assert len(shared) >= 8
    for e in shared:
        assert M.VALENCES[e] - 2 == UNSATURATION_COEFFICIENTS[e], e
    assert set(M.VALENCES) == set(M.ELEMENTS)


def test_loss_libraries_are_hill_formulas_of_the_right_parity():
    """peaky: test_loss_libraries_are_hill_formulas_of_the_right_parity."""
    for f in M.CLOSED_SHELL_LOSSES:
        assert to_hill_order(_counts(f)) == f
        d = _dbe(_counts(f))
        assert d >= 0 and d == int(d), f
    for f in M.RADICAL_LOSSES:
        assert to_hill_order(_counts(f)) == f
        d = _dbe(_counts(f))
        assert d >= 0.5 and d != int(d), f
    assert len(set(M.CLOSED_SHELL_LOSSES + M.RADICAL_LOSSES)) == len(
        M.CLOSED_SHELL_LOSSES
    ) + len(M.RADICAL_LOSSES)


def test_loss_libraries_are_the_documented_ones_and_each_entry_is_named():
    """The lists docs/ms2.md section 6 prints, in priority order: 43 closed-shell
    and 16 radical formulas. Dropping an entry would not change a loss FORMULA -
    most entries also pass as one unnamed piece - but it changes
    `unnamed_pieces`, which ranks. peaky:
    test_loss_libraries_are_the_documented_ones_and_each_entry_is_named."""
    assert M.CLOSED_SHELL_LOSSES == (
        "H2O",
        "H3N",
        "H3[15N]",
        "CO",
        "CO2",
        "H2",
        "CH4",
        "C2H4",
        "CH2O",
        "CH4O",
        "CH2O2",
        "C2H2O",
        "C2H4O2",
        "CHN",
        "CHNO",
        "CH4N2O",
        "H2O2",
        "HNO2",
        "H[15N]O2",
        "HNO3",
        "H[15N]O3",
        "H2S",
        "CH4S",
        "O2S",
        "O3S",
        "HF",
        "HCl",
        "HBr",
        "HI",
        "N2",
        "O2",
        "C2H2",
        "C3H6",
        "C4H8",
        "C3H6O",
        "C2H4O",
        "C5H8",
        "C2H6O",
        "H2O4S",
        "C2H6OSi",
        "CS2",
        "COS",
        "N2O",
    )
    assert M.RADICAL_LOSSES == (
        "H",
        "CH3",
        "HO",
        "HO2",
        "NO",
        "[15N]O",
        "NO2",
        "[15N]O2",
        "NO3",
        "[15N]O3",
        "CHO",
        "CH3O",
        "C2H5",
        "Cl",
        "Br",
        "I",
    )
    assert (len(M.CLOSED_SHELL_LOSSES), len(M.RADICAL_LOSSES)) == (43, 16)
    for f in M.CLOSED_SHELL_LOSSES + M.RADICAL_LOSSES:
        d = M.decompose_loss(f, k_max=1)
        assert (d.pieces, d.unnamed, d.radical) == ((f,), 0, f in M.RADICAL_LOSSES), f
    # priority breaks size ties: H4O2 is two waters, not H2 + H2O2
    assert M.decompose_loss("H4O2").pieces == ("H2O", "H2O")


def test_h_free_library_losses_read():
    """CS2, COS and N2O have no H or halogen, so only the library lets them
    read: a protonated methyl dimethyldithiocarbamate (C4H10NS2+) can lose CS2
    to C3H10N+, its thiocarbamate analogue COS, and a C2H7N2O2+ ion N2O.
    peaky: test_h_free_library_losses_read."""
    for loss in ("CS2", "COS", "N2O"):
        d = M.decompose_loss(loss)
        assert (d.pieces, d.unnamed) == ((loss,), 0), loss
    for parent, frag, loss in (
        ("C4H10NS2+", "C3H10N+", "CS2"),
        ("C4H10NOS+", "C3H10N+", "COS"),
        ("C2H7N2O2+", "C2H7O+", "N2O"),
    ):
        top = M.fragment_candidates(parent, _mz(frag))[0]
        assert (top.formula, top.loss, top.loss_name, top.unnamed_pieces) == (
            frag,
            loss,
            loss,
            0,
        ), parent
    # still no bare-skeleton pieces: CS is neither named nor H-bearing
    assert M.decompose_loss("CS") is None


def test_sequential_loss_is_accepted_as_two_molecules():
    """peaky: test_sequential_loss_is_accepted_as_two_molecules."""
    # protonated C10H16O losing water and then H2: H4O has DBE -1, so k >= 2
    top = M.fragment_candidates("C10H17O+", _mz("C10H13+"))[0]
    assert (top.formula, top.loss, top.loss_name, top.loss_pieces) == (
        "C10H13+",
        "H4O",
        "H2O + H2",
        2,
    )
    assert (top.pieces, top.unnamed_pieces, top.is_precursor) == (
        ("H2O", "H2"),
        0,
        False,
    )
    # the precursor reading carries no loss and no pieces at all
    pre = M.fragment_candidates("C10H17O+", _mz("C10H17O+"))
    assert [
        (
            c.is_precursor,
            c.loss,
            c.loss_name,
            c.loss_pieces,
            c.unnamed_pieces,
            c.pieces,
            c.radical_loss,
        )
        for c in pre
    ] == [(True, "", "", 0, 0, (), False)]


def test_minimal_k_bound_and_k_max():
    """peaky: test_minimal_k_bound_and_k_max."""
    assert M.decompose_loss("H8") is None  # DBE -3 -> k >= 4
    d = M.decompose_loss("H8", k_max=4)
    assert d.pieces == ("H2", "H2", "H2", "H2") and d.unnamed == 0
    assert M.fragment_candidates("C9H17O+", _mz("C9H9O+")) == []
    four = M.fragment_candidates("C9H17O+", _mz("C9H9O+"), k_max=4)
    assert [c.loss_name for c in four] == ["H2 + H2 + H2 + H2"]
    assert M.decompose_loss("H5[15N]O").name == "H2O + H3[15N]"
    assert M.decompose_loss("H5^NO").name == "H2O + H3[15N]"
    assert M.decompose_loss("H7[15N]O2").k == 3
    assert M.K_MAX_DEFAULT == 3
    for bad in (0, M.K_MAX_LIMIT + 1, 2.0, True, "3", None):
        with pytest.raises(ValueError):
            M.decompose_loss("H2O", k_max=bad)


def test_a_long_dehydration_chain_reads_when_k_max_allows_it():
    """A hexose's [M+[15N]H4]+ (C6H16[15N]O6+) declusters and dehydrates step
    by step. -H3[15N] -4 H2O (C6H5O2+, loss H11[15N]O4, DBE -4) needs five
    molecules and -H3[15N] -5 H2O (C6H3O+, H13[15N]O5, DBE -5) six: no library
    can do it in fewer, so the default k_max 3 refuses both and an explicit
    k_max reads them. peaky:
    test_a_long_dehydration_chain_reads_when_k_max_allows_it."""
    p = "C6H16[15N]O6+"
    for frag, k in (
        ("C6H13O6+", 1),
        ("C6H11O5+", 2),
        ("C6H9O4+", 3),
        ("C6H7O3+", 4),
        ("C6H5O2+", 5),
        ("C6H3O+", 6),
    ):
        for k_max in range(1, M.K_MAX_LIMIT + 1):
            got = M.fragment_candidates(p, _mz(frag), k_max=k_max)
            if k_max < k:
                assert got == [], (frag, k_max)
            else:
                assert [(c.formula, c.loss_name) for c in got] == [
                    (frag, " + ".join(["H2O"] * (k - 1) + ["H3[15N]"]))
                ], (frag, k_max)
    assert M.decompose_loss("H11[15N]O4", k_max=5).pieces == ("H2O",) * 4 + ("H3[15N]",)
    # the cap itself, by value: k_max 6 reads the whole -H3[15N] -5 H2O chain,
    # and 7 is refused
    assert M.K_MAX_LIMIT == 6
    six = M.fragment_candidates(p, _mz("C6H3O+"), k_max=6)
    assert [
        (c.formula, c.loss, c.loss_name, c.loss_pieces, c.unnamed_pieces) for c in six
    ] == [("C6H3O+", "H13[15N]O5", "H2O + H2O + H2O + H2O + H2O + H3[15N]", 6, 0)]
    with pytest.raises(ValueError):
        M.fragment_candidates(p, _mz("C6H3O+"), k_max=7)


def test_bare_skeleton_remainders_are_refused_named_ones_are_not():
    """peaky: test_bare_skeleton_remainders_are_rejected_named_ones_are_not."""
    for loss in ("C4", "C", "O", "C2O", "C3", "O3"):
        assert M.decompose_loss(loss) is None, loss
    assert M.decompose_loss("NH") is None  # Senior rule 2: 4 < 6
    # CO / CO2 are library pieces, so a bare-skeleton loss stays possible
    assert M.decompose_loss("CO").pieces == ("CO",)
    assert M.decompose_loss("CO2").pieces == ("CO2",)
    assert M.fragment_candidates("C10H19O+", _mz("C10H19+")) == []  # -O
    co = M.fragment_candidates("C10H19O+", _mz("C9H19+"))[0]
    assert (co.loss, co.loss_name, co.unnamed_pieces) == ("CO", "CO", 0)
    # one unnamed piece on its own
    d = M.decompose_loss("C11H20O3")
    assert d.pieces == ("C11H20O3",) and d.unnamed == 1
    # library pieces PLUS one unnamed remainder: C5H14O (DBE -1) needs two
    # molecules, and H2O + C5H12 (k 2) beats the all-named H2O + CH4 + C4H8 (k 3)
    for loss, pieces in (("C5H14O", ("H2O", "C5H12")), ("C6H16O", ("H2O", "C6H14"))):
        d = M.decompose_loss(loss)
        assert (d.pieces, d.unnamed, d.radical) == (pieces, 1, False), loss
    # a remainder that fails its rules is not taken: H6O (DBE -2) is not
    # H2O + H4 (H4 has DBE -1) but the all-named H2O + H2 + H2
    d = M.decompose_loss("H6O")
    assert (d.pieces, d.unnamed) == (("H2O", "H2", "H2"), 0)


def test_halogen_only_losses_are_one_unnamed_molecule():
    """The H-or-halogen clause, halogen side: I2 and Br2 are no library pieces
    (HI, HBr, I, Br are), so they read only as one unnamed closed-shell piece -
    the iodide reagent's declustering: I3- -> I- and an [M-H+I2]- adduct ->
    [M-H]-, both by losing I2. peaky:
    test_halogen_only_losses_are_one_unnamed_molecule."""
    for loss in ("I2", "Br2", "BrI"):
        d = M.decompose_loss(loss)
        assert (d.pieces, d.unnamed, d.radical) == ((loss,), 1, False), loss
    for parent, frag, loss in (
        ("I3-", "I-", "I2"),
        ("Br3-", "Br-", "Br2"),
        ("C5H9I2O2-", "C5H9O2-", "I2"),  # [M-H+I2]- of C5H10O2
    ):
        got = M.fragment_candidates(parent, _mz(frag))
        assert [
            (c.formula, c.loss, c.loss_name, c.unnamed_pieces, c.radical_loss)
            for c in got
        ] == [(frag, loss, loss, 1, False)], parent


def test_anion_losses_and_the_15n_nitrate_cluster():
    """peaky: test_anion_losses_and_the_15n_nitrate_cluster."""
    p = "C10H17O5-"  # an [M-H]- ion
    w = M.fragment_candidates(p, _mz("C10H15O4-"))[0]
    assert (w.loss, w.loss_name, w.parity) == ("H2O", "H2O", "EE")
    c = M.fragment_candidates(p, _mz("C9H17O3-"))[0]
    assert (c.loss, c.loss_name) == ("CO2", "CO2")
    # C10H18O4 . 15NO3- (M = C10H18O4): the cluster releases the reagent ion ...
    cl = "C10H18[15N]O7-"
    r = M.fragment_candidates(cl, _mz("[15N]O3-"))[0]
    assert (r.formula, r.loss, r.loss_pieces, r.unnamed_pieces) == (
        "[15N]O3-",
        "C10H18O4",
        1,
        1,
    )
    assert r.mz_theo == pytest.approx(62.98540, abs=1e-5)
    # ... or the analyte's [M-H]- (C10H17O4-) by losing the labelled nitric acid
    mh = M.fragment_candidates(cl, _mz("C10H17O4-"))
    assert [
        (c.formula, c.loss, c.loss_name, c.loss_pieces, c.unnamed_pieces) for c in mh
    ] == [("C10H17O4-", "H[15N]O3", "H[15N]O3", 1, 0)]
    # ... and C10H17O5- = [M+O-H]- (one O more than the analyte: an O-atom
    # transfer from the nitrate) by losing the labelled nitrous acid
    a = M.fragment_candidates(cl, _mz("C10H17O5-"))[0]
    assert (a.formula, a.loss, a.loss_name, a.unnamed_pieces) == (
        "C10H17O5-",
        "H[15N]O2",
        "H[15N]O2",
        0,
    )


def test_radical_losses_are_flagged_ranked_last_and_optional():
    """peaky: test_radical_losses_are_flagged_ranked_last_and_optional."""
    # EE precursor -> OE fragment by CH3 loss
    r = M.fragment_candidates("C10H17O5-", _mz("C9H14O5-"))
    assert len(r) == 1
    assert (r[0].loss, r[0].parity, r[0].radical_loss) == ("CH3", "OE", True)
    assert (
        M.fragment_candidates("C10H17O5-", _mz("C9H14O5-"), allow_radical=False) == []
    )
    # OE precursor -> EE fragment by H loss
    h = M.fragment_candidates("C7H8+.", _mz("C7H7+"))
    assert [(c.formula, c.loss, c.parity, c.radical_loss) for c in h] == [
        ("C7H7+", "H", "EE", True)
    ]
    assert M.fragment_candidates("C7H8+.", _mz("C7H7+"), allow_radical=False) == []
    # ranking (a): a closed-shell reading comes before a radical one even when
    # the radical one is closer. Two readings of opposite parity at one nominal
    # mass differ by an odd number of 14N; in this 14N-only parent the cheapest
    # trade (N for CH2) is 12.6 mDa, so this needs a wide window: an
    # organonitrate anion losing NO2 (radical) vs CH2O2 (closed).
    p, oe = "C10H16NO7-", "C10H16O5-"
    wide = M.Tolerance(ppm=0.0, mda=15.0)
    cands = M.fragment_candidates(p, _mz(oe), tolerance=wide)
    assert [(c.formula, c.radical_loss) for c in cands] == [
        ("C9H14NO5-", False),
        (oe, True),
    ]
    assert abs(cands[1].ppm) < abs(cands[0].ppm)
    rd = M.read_fragments([_mz(oe)], p, tolerance=wide)
    assert _col(rd, "rank") == [1, 2] and _col(rd, "radical_loss") == [False, True]
    assert _col(rd, "parity") == ["EE", "OE"] and _col(rd, "dbe") == [3.5, 3.0]
    # the OE precursor's reading: an EE fragment by a radical loss
    rd = M.read_fragments([_mz("C7H7+")], "C7H8+.")
    assert (_col(rd, "parity"), _col(rd, "loss"), _col(rd, "radical_loss")) == (
        ["EE"],
        ["H"],
        [True],
    )
    rd = M.read_fragments([_mz(oe)], p, tolerance=wide, allow_radical=False)
    assert _col(rd, "formula") == ["C9H14NO5-"]


def test_parity_flipping_trades_at_one_nominal_mass():
    """docs/ms2.md section 8, by value: at one nominal mass, opposite-parity
    readings differ by an odd number of 14N atoms. The cheapest trades are
    [15N] for NH (10.8 mDa, only in a parent holding both N and [15N]) and N for
    CH2 (12.6 mDa); the closer ones are multi-atom ([15N]2 for NO, 2.2 mDa,
    also mixed-label only; CH4N3 for C3H6O, 1.3 mDa). peaky:
    test_parity_flipping_trades_at_one_nominal_mass."""

    def trade(a, b):  # mDa between two neutral compositions, as cations
        return (_mz(a + "+") - _mz(b + "+")) * 1e3

    assert trade("HN", "[15N]") == pytest.approx(10.79, abs=0.01)
    assert trade("CH2", "N") == pytest.approx(12.58, abs=0.01)
    assert trade("NO", "[15N]2") == pytest.approx(-2.23, abs=0.01)
    assert trade("C2H2O", "N3") == pytest.approx(1.34, abs=0.01)
    # the [15N]H4+ adduct of a C4H9NO amide holds both labels: its OE fragment
    # C3H7[15N]+ (radical loss) has an EE twin C3H8N+ 10.8 mDa away, and rule
    # (a) ranks the closed-shell twin first
    got = M.fragment_candidates(
        "C4H13N[15N]O+", _mz("C3H7[15N]+"), tolerance=M.Tolerance(ppm=0.0, mda=11.0)
    )
    assert [
        (c.formula, c.parity, c.loss, c.loss_name, c.radical_loss) for c in got
    ] == [
        ("C3H8N+", "EE", "CH5[15N]O", "CH5[15N]O", False),
        ("C3H7[15N]+", "OE", "CH6NO", "H3N + CH3O", True),
    ]
    assert (_mz("C3H7[15N]+") - got[0].mz_theo) * 1e3 == pytest.approx(-10.79, abs=0.01)
    # the 14N-only adduct (NH4+): no twin inside 11 mDa, two at N for CH2 (12.6)
    p14 = "C4H13N2O+"
    eleven = M.Tolerance(ppm=0.0, mda=11.0)
    thirteen = M.Tolerance(ppm=0.0, mda=13.0)
    assert [
        c.formula for c in M.fragment_candidates(p14, _mz("C3H7N+"), tolerance=eleven)
    ] == ["C3H7N+"]
    assert [
        (c.formula, c.parity)
        for c in M.fragment_candidates(p14, _mz("C3H7N+"), tolerance=thirteen)
    ] == [("C4H9+", "EE"), ("C2H5N2+", "EE"), ("C3H7N+", "OE")]


def test_unnamed_radical_remainders():
    """A radical loss that is no library radical: one unnamed radical piece
    (half-integer DBE >= 1/2, sum of valences >= 2 x largest - 1, H or
    halogen), or a library radical plus one unnamed closed-shell remainder.
    peaky: test_unnamed_radical_remainders."""

    def dec(f):
        d = M.decompose_loss(f)
        return d and (d.pieces, d.unnamed, d.radical)

    assert dec("C3H7") == (("C3H7",), 1, True)  # one unnamed radical
    assert dec("H2N") == (("H2N",), 1, True)  # valences 5 >= 2*3 - 1
    assert dec("CH") is None  # valences 5 < 2*4 - 1
    assert dec("C2H7") == (("H2", "C2H5"), 0, True)  # DBE -1/2: two pieces, named
    assert dec("C3H10N") == (("H3N", "C3H7"), 1, True)  # named + unnamed radical
    assert dec("C3H10[15N]O") == (("H2O", "C3H8[15N]"), 1, True)
    # no closed library piece fits CClF4 (DBE -1/2): the library radical Cl plus
    # an unnamed CLOSED-shell remainder
    assert dec("CClF4") == (("Cl", "CF4"), 1, True)
    # ... and at k_max 2, where it is the LAST size: once Cl is held, the rest
    # CF4 is closed-shell and needs one piece, so the search must not prune it
    # with the radical bound (which would ask for two)
    d = M.decompose_loss("CClF4", k_max=2)
    assert (d.pieces, d.unnamed, d.radical) == (("Cl", "CF4"), 1, True)
    assert M.decompose_loss("CClF4", k_max=1) is None
    # the remainder rules themselves, clause by clause
    rules = {  # formula: acceptable as the one unnamed piece?
        "C5H12": True,
        "C4": False,
        "H4": False,
        "NH": False,
        "CF4": True,  # closed
        "C3H7": True,
        "CH": False,
        "CH7": False,
        "C2H": True,
        "CH5": False,  # radical
        "C2O3": False,
        "Cl": True,
        "NO2": False,  # no H or halogen: it reads only as a library radical
        "I2": True,
        "Br2": True,
        "BrI": True,
        "CBr4": True,  # halogen, no H: closed
    }
    rows = np.array([R._vec(_counts(f)) for f in rules])
    radical = np.array([_dbe(_counts(f)) % 1 != 0 for f in rules])
    assert list(R._remainder_ok(rows, radical)) == list(rules.values())
    # the parity is part of each rule: a closed-shell piece is no radical
    # remainder, nor a radical one a closed-shell remainder
    swapped = np.array([R._vec(_counts(f)) for f in ("C2H4", "C3H7")])
    assert list(R._remainder_ok(swapped, np.array([True, False]))) == [False, False]
    # allow_radical=False: a radical loss is no decomposition at all
    assert M.decompose_loss("CH3", allow_radical=False) is None
    assert M.decompose_loss("C3H10N", allow_radical=False) is None
    assert M.decompose_loss("H2O", allow_radical=False).pieces == ("H2O",)


def test_ranking_rules_b_to_e():
    """Within one (peak, parent): (a) closed-shell loss first - see
    test_radical_losses_are_flagged_ranked_last_and_optional - then (b) fewer
    loss pieces, (c) fewer unnamed pieces, (d) smaller |ppm|, (e) formula. Each
    case below is decided by exactly one rule against the others. peaky:
    test_ranking_rules_b_to_e."""

    def read(parent, mz, **kw):
        return [
            (c.formula, c.loss_pieces, c.unnamed_pieces)
            for c in M.fragment_candidates(parent, mz, **kw)
        ]

    # (b) at the default window: 2 pieces at -4.7 ppm before 3 pieces at 0 ppm
    assert read("C8H28[15N]O4Si4+", _mz("C3H3O3+")) == [
        ("C2H7Si2+", 2, 1),
        ("C3H3O3+", 3, 1),
    ]
    # (b) before (c): 1 piece with 1 unnamed before 3 named pieces at 0 ppm
    assert read("C11H18[15N]O2+", _mz("C7H3+"), tolerance=M.Tolerance(mda=10.0)) == [
        ("C3H4[15N]O2+", 1, 1),
        ("C7H3+", 3, 0),
    ]
    # (c) before (d): no unnamed piece (C2H4O2) at 362 ppm before one unnamed
    # (C2H4S) at 0 ppm
    assert read("C3H9O2S+", _mz("CH5O2+"), tolerance=M.Tolerance(mda=20.0)) == [
        ("CH5S+", 1, 0),
        ("CH5O2+", 1, 1),
    ]
    # (d) is |ppm|, not signed: +7.8 ppm before -36 ppm
    mz = _mz("H4[15N]O2-") + 0.4e-3
    got = M.fragment_candidates(
        "C10H18[15N]O7-", mz, tolerance=M.Tolerance(ppm=0.0, mda=3.0)
    )
    assert [(c.formula, round(c.ppm)) for c in got] == [
        ("H4[15N]O2-", 8),
        ("C4H3-", -36),
    ]
    # (d) at a sub-ppm gap, against the formula order: in a heteroatom-rich
    # (synthetic) anion, two one-unnamed-piece closed-shell readings 0.3 ppm
    # apart; +4.68 ppm comes first although 'C5H9NO9S-' sorts before
    # 'C9H9NO4S2-' (a |ppm| key rounded to whole ppm would flip them)
    got = M.fragment_candidates("C15H25N3O9S2-", 258.99906)
    assert [
        (
            c.formula,
            c.loss,
            c.loss_pieces,
            c.unnamed_pieces,
            c.radical_loss,
            round(c.ppm, 2),
        )
        for c in got
    ] == [
        ("C9H9NO4S2-", "C6H16N2O5", 1, 1, False, 4.68),
        ("C5H9NO9S-", "C10H16N2S", 1, 1, False, -4.98),
    ]

    # (e) the formula string, when all else ties (no real pair of formulas ties
    # on |ppm|, so the key itself)
    def cand(formula, ppm):
        return M.FragmentCandidate(
            formula, "EE", 0.5, 100.0, ppm, "H2O", 1, "H2O", 0, False, False, ("H2O",)
        )

    pool = [cand("C2+", 1.0), cand("C1+", -1.0), cand("C3+", -2.0), cand("C0+", 0.5)]
    assert [c.formula for c in sorted(pool, key=M.FragmentCandidate.sort_key)] == [
        "C0+",
        "C1+",
        "C2+",
        "C3+",
    ]
    # (d) on the key itself, at a 0.1 ppm gap against the formula order
    pool = [cand("C0+", 0.35), cand("C1+", -0.25)]
    assert [c.formula for c in sorted(pool, key=M.FragmentCandidate.sort_key)] == [
        "C1+",
        "C0+",
    ]


def test_composition_ambiguity_keeps_both_parents():
    """peaky: test_composition_ambiguity_keeps_both_parents."""
    window = {"MH": "C10H17+", "adduct": "C10H20[15N]+"}
    rd = M.read_fragments([_mz("C6H9+"), _mz("C10H17+")], window)
    p0 = _rows(rd, 0)
    assert _col(p0, "parent") == ["MH", "adduct"]
    assert set(_col(p0, "n_parents")) == {2}
    assert _col(p0, "loss") == ["C4H8", "C4H11[15N]"]
    p1 = _rows(rd, 1)
    assert _col(p1, "is_precursor") == [True, False]
    assert _col(p1, "loss") == ["", "H3[15N]"]
    assert _col(p1, "loss_pieces") == [0, 1]
    assert _col(p1, "unnamed_pieces") == [0, 0]
    assert _col(p1, "loss_name") == ["", "H3[15N]"]
    # a sequence of formulas works like a mapping keyed by the formulas
    rd2 = M.read_fragments([_mz("C6H9+")], ["C10H17+", "C10H20[15N]+"])
    assert _col(rd2, "parent") == ["C10H17+", "C10H20[15N]+"]


def test_n_parents_counts_parents_and_n_candidates_counts_readings():
    """Two readings from ONE parent is not composition ambiguity: n_parents
    counts parents, n_candidates counts readings within (peak, parent). peaky:
    test_n_parents_counts_parents_and_n_candidates_counts_readings."""
    window = {"a": "C8H28[15N]O4Si4+", "b": "C10H17+"}
    rd = M.read_fragments([_mz("C3H3O3+")], window)
    assert _col(rd, "parent") == ["a", "a"] and _col(rd, "rank") == [1, 2]
    assert _col(rd, "n_candidates") == [2, 2] and set(_col(rd, "n_parents")) == {1}
    assert _col(rd, "formula") == ["C2H7Si2+", "C3H3O3+"]
    # one reading from each of two parents: n_parents 2, n_candidates 1 each
    rd = M.read_fragments([_mz("C6H9+")], {"MH": "C10H17+", "adduct": "C10H20[15N]+"})
    assert _col(rd, "n_candidates") == [1, 1] and _col(rd, "n_parents") == [2, 2]
    # best_readings keeps rank 1 per (peak, parent)
    best = M.best_readings(M.read_fragments([_mz("C3H3O3+")], window))
    assert _col(best, "formula") == ["C2H7Si2+"]


def test_dbe_and_ppm_are_the_ion_dbe_and_the_signed_error():
    """peaky: test_dbe_and_ppm_columns_are_the_ion_dbe_and_signed_error."""
    # C6H9+ read 0.2 mDa LOW: ppm negative, dbe the ion's 2.5 (not the
    # neutral-style 2.0 of C6H8)
    mz = _mz("C6H9+") - 0.2e-3
    c = M.fragment_candidates("C10H17+", mz)[0]
    assert (c.formula, c.parity, c.dbe) == ("C6H9+", "EE", 2.5)
    assert c.ppm == pytest.approx(-0.2e-3 / _mz("C6H9+") * 1e6) and c.ppm < 0
    rd = M.read_fragments([mz, _mz("C10H17+") + 0.3e-3], "C10H17+")
    assert _col(rd, "dbe") == [2.5, 2.5]
    assert rd[0].ppm < 0 < rd[1].ppm
    # an odd-electron fragment of an odd-electron precursor: integer ion DBE
    oe = M.fragment_candidates("C7H8+.", _mz("C5H6+") + 0.1e-3)[0]
    assert (oe.formula, oe.parity, oe.dbe, oe.loss, oe.radical_loss) == (
        "C5H6+",
        "OE",
        3.0,
        "C2H2",
        False,
    )
    assert oe.ppm > 0


# What a peak no parent explains carries, besides its own spectrum / peak / mz /
# mz_cal / intensity and NaN dbe / ppm.
UNEXPLAINED = {
    "parent": "",
    "parent_ion": "",
    "formula": "",
    "parity": "",
    "loss": "",
    "loss_pieces": 0,
    "loss_name": "",
    "unnamed_pieces": 0,
    "radical_loss": False,
    "is_precursor": False,
    "rank": 0,
    "n_candidates": 0,
    "n_parents": 0,
    "ladder_support": "",
    "pieces": (),
}

# The frame docs/ms2.md section 7 documents, written out here rather than read
# from READINGS_COLUMNS, so a changed declaration fails.
DOCUMENTED_DTYPES = {
    "spectrum": "str",
    "peak": "int64",
    "mz": "float64",
    "mz_cal": "float64",
    "intensity": "float64",
    "parent": "str",
    "parent_ion": "str",
    "formula": "str",
    "parity": "str",
    "dbe": "float64",
    "ppm": "float64",
    "loss": "str",
    "loss_pieces": "int64",
    "loss_name": "str",
    "unnamed_pieces": "int64",
    "radical_loss": "bool",
    "is_precursor": "bool",
    "rank": "int64",
    "n_candidates": "int64",
    "n_parents": "int64",
    "ladder_support": "str",
}


def _assert_documented_frame(df):
    assert list(df.columns) == list(DOCUMENTED_DTYPES)
    for col, want in DOCUMENTED_DTYPES.items():
        assert df[col].dtype == pd.Series([], dtype=want).dtype, (col, df[col].dtype)


def test_every_peak_appears_in_order_and_the_frame_is_typed():
    """peaky: test_every_peak_appears_with_stable_dtypes_and_order."""
    peaks = pd.DataFrame(
        {
            "mz": [_mz("C6H9+"), 250.0, _mz("C10H17+"), 40.5],
            "intensity": [1.0, 2.0, 3.0, 4.0],
        },
        index=[10, 11, 12, 13],
    )
    rd = M.read_fragments(peaks, {"a": "C10H17+", "b": "C10H20[15N]+"}, spectrum="s1")
    assert sorted({r.peak for r in rd}) == [0, 1, 2, 3]  # positions, not labels
    assert _col(rd, "peak") == sorted(_col(rd, "peak"))
    un = [r for r in rd if r.n_candidates == 0]
    assert _col(un, "peak") == [1, 3]
    # the whole unexplained row, field by field: empty text, zero counts, both
    # flags False, NaN dbe / ppm - so that a filter on is_precursor or
    # radical_loss never picks one up as a reading
    for r in un:
        assert {f: getattr(r, f) for f in UNEXPLAINED} == UNEXPLAINED, r
        assert math.isnan(r.dbe) and math.isnan(r.ppm)
        assert r.mz_cal == r.mz and r.spectrum == "s1"  # offset 0
    assert all(r.formula for r in rd if r.is_precursor)
    assert list(dict.fromkeys(_col(rd, "intensity"))) == [1.0, 2.0, 3.0, 4.0]
    assert set(_col(rd, "spectrum")) == {"s1"}
    for readings in (
        rd,
        M.read_fragments([], "C10H17+"),
        M.read_fragments([250.0], "C10H17+"),
    ):
        df = M.readings_frame(readings)
        _assert_documented_frame(df)
        assert len(df) == len(readings)
    df = M.readings_frame(rd)
    unread = df[df["peak"] == 1].iloc[0]
    assert unread["formula"] == "" and math.isnan(unread["ppm"])
    assert list(df["parent"]) == _col(rd, "parent")
    assert list(M.READINGS_COLUMNS) == list(DOCUMENTED_DTYPES)
    best = M.best_readings(rd)
    assert _col(best, "peak") == [0, 0, 1, 2, 2, 3]
    assert set(_col(best, "rank")) == {0, 1}


def test_readings_carry_the_input_mz_and_the_parent_ion_formula():
    """`mz` is the input as given, `mz_cal` the axis the reading is judged on
    (on unexplained rows too), `parent` the label and `parent_ion` the parent's
    normalised ion formula. peaky:
    test_output_carries_the_input_mz_and_the_parent_ion_formula."""
    raw = [_mz("C6H9+") * (1 + 5e-6), 250.0]
    rd = M.read_fragments({"mz": raw}, {"MH": "C10H17+"}, offset_ppm=5.0)
    assert _col(rd, "mz") == raw
    assert _col(rd, "mz_cal") == pytest.approx(
        list(M.apply_offset(np.array(raw), 5.0)), rel=0, abs=1e-9
    )
    assert all(r.mz != r.mz_cal for r in rd)
    # no 'intensity': NaN, never a measured 0
    assert all(math.isnan(r.intensity) for r in rd)
    assert (_col(rd, "parent"), _col(rd, "parent_ion")) == (["MH", ""], ["C10H17+", ""])
    assert rd[0].formula == "C6H9+" and abs(rd[0].ppm) < 1e-6
    # a radical-marked or caret-labelled parent is reported by its formula
    for parents, label, ion in (
        ({"tol": "C7H8+."}, "tol", "C7H8+"),
        (["C7H8+\u2022"], "C7H8+\u2022", "C7H8+"),
        (" C7H8+. ", " C7H8+. ", "C7H8+"),
    ):
        rd = M.read_fragments([_mz("C7H7+")], parents)
        assert (_col(rd, "parent"), _col(rd, "parent_ion")) == ([label], [ion]), parents
    rd = M.read_fragments([_mz("C10H17+")], {"adduct": "C10H20^N+"})
    assert _col(rd, "parent_ion") == ["C10H20[15N]+"]


def test_determinism_across_runs_and_cold_caches():
    """peaky: test_determinism_across_runs_and_cold_caches."""
    peaks = [_mz(i) for i in ("C6H9+", "C7H11+", "C5H7+", "C10H17+", "C9H11+")] + [
        100.0
    ]
    window = {"a": "C10H17+", "b": "C10H20[15N]+", "c": "C10H19O+"}
    first = M.read_fragments(peaks, window)
    R._decompose.cache_clear()
    R._parent_grid.cache_clear()
    R._loss_vec.cache_clear()
    second = M.read_fragments(peaks, window)
    assert repr(first) == repr(second)
    pd.testing.assert_frame_equal(M.readings_frame(first), M.readings_frame(second))


#: Run in a process of its own per hash seed: windows that hold N and [15N]
#: side by side (where a set-ordered formula would follow the seed), read and
#: gated; every field of every reading printed. Glucosamine + [15N]H4+: NCE 1
#: reads C6H9O4+ (loss H8[15N]NO = H2O + H3N + H3[15N], three pieces), NCE 10
#: the fourth step C6H7O3+ (H10[15N]NO2, four pieces), on that ladder.
_SEED_SCRIPT = r"""
import dataclasses, json
from mascope_tools.composition import ms2 as M
g = "C6H17N^NO5+"
def spec(nce, ions):
    return M.read_fragments({"mz": [M.ion_mz(i) for i in ions], "intensity": [200.0, 100.0]},
                            {"g": g}, k_max=M.LADDER_K, spectrum=f"198.1102@hcd{nce:.2f}")
gated = M.ladder_gate(spec(1, [g, "C6H9O4+"]) + spec(10, [g, "C6H7O3+"]))
amide = M.read_fragments([M.ion_mz(f) for f in ("C3H7^N+", "C3H8N+", "C2H5N2+", "CH4N+")],
                         {"a": "C4H13N^NO+", "b": "C4H13N2O+"},
                         tolerance=M.Tolerance(ppm=0.0, mda=13.0))
mono = M.read_fragments([155.15606, 137.13246, 81.06979, 95.08546],
                        {"adduct": "C10H20^N+", "water cluster": "C10H19O+"})
for rows in (gated, amide, mono):
    print(json.dumps([dataclasses.astuple(r) for r in rows]))
print(json.dumps(M.decompose_loss("CH8N^NO3", k_max=4).pieces))
"""


def test_the_same_input_gives_the_same_bytes_under_any_hash_seed(tmp_path):
    """Each seed is its own process (one process has one seed); 8 and 16 are
    seeds under which peaky's set-ordered formula once wrote ^N before N and
    dropped a supported ladder step. peaky:
    test_the_ladder_does_not_depend_on_the_hash_seed."""
    outs = []
    for seed in ("0", "8", "16"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        p = subprocess.run(
            [sys.executable, "-c", _SEED_SCRIPT],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            timeout=300,
        )
        assert p.returncode == 0, p.stderr.decode()
        outs.append(p.stdout)
    assert outs[0] == outs[1] == outs[2]
    gated = json.loads(outs[0].splitlines()[0])
    fields = [f.name for f in dataclasses.fields(M.Reading)]
    four = [dict(zip(fields, r)) for r in gated if r[fields.index("loss_pieces")] == 4]
    assert [
        (r["formula"], r["loss"], r["rank"], r["ladder_support"]) for r in four
    ] == [("C6H7O3+", "H10[15N]NO2", 1, "H8[15N]NO")]
    assert not [r for r in gated if r[fields.index("rank")] == 0]


def test_offset_recovery_on_a_synthetic_shift():
    """peaky: test_offset_recovery_on_a_synthetic_shift."""
    ions = ["C10H20[15N]+", "H4[15N]+", "C10H17+"]
    shifted = {i: _mz(i) * (1 + 12.0e-6) for i in ions}
    off = M.mass_offset(
        [(shifted["C10H20[15N]+"], "C10H20[15N]+"), (shifted["H4[15N]+"], "H4[15N]+")]
    )
    assert off.offset_ppm == pytest.approx(12.0, abs=1e-6)
    assert all(abs(a.residual_ppm) < 1e-6 for a in off.anchors)
    assert M.AnchorResidual._fields == (
        "ion",
        "measured_mz",
        "theo_mz",
        "error_ppm",
        "residual_ppm",
    )
    # 12 ppm is outside the window uncorrected, and exact once applied
    assert M.fragment_candidates("C10H20[15N]+", shifted["C10H17+"]) == []
    rd = M.read_fragments(
        [shifted["C10H17+"]], "C10H20[15N]+", offset_ppm=off.offset_ppm
    )
    assert rd[0].formula == "C10H17+" and abs(rd[0].ppm) < 1e-6
    assert rd[0].mz_cal == pytest.approx(M.apply_offset(shifted["C10H17+"], 12.0))
    # median, not mean: one bad anchor of three does not move it
    med = M.mass_offset(
        [
            (_mz("C10H17+") * (1 + 2e-6), "C10H17+"),
            (_mz("H4[15N]+") * (1 + 3e-6), "H4[15N]+"),
            (_mz("C10H20[15N]+") * (1 + 40e-6), "C10H20[15N]+"),
        ]
    )
    assert med.offset_ppm == pytest.approx(3.0, abs=1e-6)
    # signs: an anchor reading HIGH has a positive error, and its residual is
    # the error re-read on the offset axis, (error - offset) / (1 + offset*1e-6)
    assert [a.error_ppm for a in med.anchors] == pytest.approx(
        [2.0, 3.0, 40.0], abs=1e-6
    )
    assert [a.residual_ppm for a in med.anchors] == pytest.approx(
        [(e - 3.0) / (1 + 3e-6) for e in (2.0, 3.0, 40.0)], abs=1e-6
    )
    assert med.anchors[0].residual_ppm < 0 < med.anchors[2].residual_ppm
    assert [a.theo_mz for a in med.anchors] == pytest.approx(
        [_mz("C10H17+"), _mz("H4[15N]+"), _mz("C10H20[15N]+")], abs=1e-9
    )
    # anchors are stored by their normalised ion formula (no radical mark, no
    # whitespace, Mascope's notation), in the order given
    oe = M.mass_offset(
        [
            (_mz("C7H8+"), "C7H8+."),
            (_mz("C10H17+"), " C10H17+ "),
            (_mz("H4^N+"), "H4^N+"),
        ]
    )
    assert [a.ion for a in oe.anchors] == ["C7H8+", "C10H17+", "H4[15N]+"]


def test_offset_sign_two_anchor_median_and_anion_anchors():
    """peaky: test_offset_sign_two_anchor_median_and_anion_anchors."""
    # a LOW-reading axis: a negative offset, which moves the m/z back UP
    lo_mz = _mz("C10H17+") * (1 - 12.0e-6)
    lo = M.mass_offset([(lo_mz, "C10H17+")])
    assert lo.offset_ppm == pytest.approx(-12.0, abs=1e-6)
    assert lo.anchors[0].error_ppm == pytest.approx(-12.0, abs=1e-6)
    assert abs(lo.anchors[0].residual_ppm) < 1e-6
    assert M.apply_offset(lo_mz, lo.offset_ppm) == pytest.approx(
        _mz("C10H17+"), rel=0, abs=1e-9
    )
    assert M.apply_offset(lo_mz, lo.offset_ppm) > lo_mz
    # two anchors (the precursor plus the reagent ion): the median of two is
    # their mean, and the residuals split evenly around it
    two = M.mass_offset(
        [
            (_mz("C10H17+") * (1 + 2e-6), "C10H17+"),
            (_mz("H4[15N]+") * (1 + 6e-6), "H4[15N]+"),
        ]
    )
    assert two.offset_ppm == pytest.approx(4.0, abs=1e-6)
    assert [a.residual_ppm for a in two.anchors] == pytest.approx(
        [(e - 4.0) / (1 + 4e-6) for e in (2.0, 6.0)], abs=1e-6
    )
    mixed = M.mass_offset(
        [
            (_mz("C10H17+") * (1 - 3e-6), "C10H17+"),
            (_mz("H4[15N]+") * (1 + 5e-6), "H4[15N]+"),
        ]
    )
    assert mixed.offset_ppm == pytest.approx(1.0, abs=1e-6)
    # ANION anchors (negative mode, the 15N-nitrate reagent ion and an [M-H]-):
    # the theoretical m/z is the anion's own, two electron masses above the
    # cation of the same formula (a cation mass would read +17 ppm on [15N]O3-)
    neg_ions = ("C10H17O5-", "[15N]O3-")
    neg = M.mass_offset([(_mz(i) * (1 + 12.0e-6), i) for i in neg_ions])
    assert neg.offset_ppm == pytest.approx(12.0, abs=1e-6)
    assert neg.anchors[1].theo_mz == pytest.approx(62.98540, abs=1e-5)
    assert neg.anchors[1].theo_mz - _mz("[15N]O3+") == pytest.approx(
        2 * ELECTRON_MASS, abs=1e-12
    )
    assert all(abs(a.residual_ppm) < 1e-6 for a in neg.anchors)
    assert [a.ion for a in neg.anchors] == list(neg_ions)
    exact = M.mass_offset([(_mz("[15N]O3-"), "[15N]O3-")])
    assert exact.offset_ppm == pytest.approx(0.0, abs=1e-6)
    # and the corrected anion axis reads the cluster's fragments exactly
    shifted = _mz("C10H17O4-") * (1 + 12.0e-6)
    assert M.fragment_candidates("C10H18[15N]O7-", shifted) == []
    rd = M.read_fragments([shifted], "C10H18[15N]O7-", offset_ppm=neg.offset_ppm)
    assert rd[0].formula == "C10H17O4-" and abs(rd[0].ppm) < 1e-6


def test_one_composition_as_a_cation_and_an_anion_parent():
    """The sub-formula grid is cached per (composition, charge): the same
    C10H17O5 as a cation and as an anion parent, in one process and either
    order, reads its own fragment exactly, and the two grids differ by two
    electron masses on every row. peaky:
    test_one_composition_as_a_cation_and_an_anion_parent."""
    for order in (("+", "-", "+"), ("-", "+", "-")):
        R._parent_grid.cache_clear()
        theo = {}
        for sign in order:
            got = M.fragment_candidates("C10H17O5" + sign, _mz("C10H15O4" + sign))
            assert [(c.formula, c.loss) for c in got] == [("C10H15O4" + sign, "H2O")]
            assert abs(got[0].ppm) < 1e-6
            theo.setdefault(sign, got[0].mz_theo)
            assert got[0].mz_theo == theo[sign]
        assert theo["-"] - theo["+"] == pytest.approx(
            2 * ELECTRON_MASS, rel=0, abs=1e-9
        )
    key = R._ion_key(M.parse_ion("C10H17O5+")[0])
    pos, neg = R._parent_grid(key, 1), R._parent_grid(key, -1)
    assert np.array_equal(pos.sub, neg.sub) and np.array_equal(pos.t2, neg.t2)
    assert np.allclose(neg.mass - pos.mass, 2 * ELECTRON_MASS, rtol=0, atol=1e-9)


def test_tolerance_floor_applies_at_low_mass_only():
    """peaky: test_tolerance_floor_applies_at_low_mass_only."""
    # C2H3O+ 43.0178 read 0.25 mDa low (-5.8 ppm): inside the 0.3 mDa floor,
    # outside a pure 5 ppm window (0.215 mDa at m/z 43)
    low = _mz("C2H3O+") - 0.25e-3
    assert [c.formula for c in M.fragment_candidates("C11H13O+", low)] == ["C2H3O+"]
    assert M.fragment_candidates("C11H13O+", low, tolerance=M.Tolerance(mda=0.0)) == []
    # the default floor is 0.3 mDa, pinned from both sides and both signs
    for off in (-0.29e-3, 0.29e-3):
        assert [
            c.formula for c in M.fragment_candidates("C11H13O+", _mz("C2H3O+") + off)
        ] == ["C2H3O+"], off
    for off in (-0.31e-3, 0.31e-3):
        assert M.fragment_candidates("C11H13O+", _mz("C2H3O+") + off) == [], off
    # at m/z 281 the ppm term (1.4 mDa) is the window and the floor is inert
    hi = _mz("C7H21O4Si4+")
    p = "C8H28[15N]O4Si4+"
    assert M.fragment_candidates(p, hi - 1.3e-3)[0].formula == "C7H21O4Si4+"  # -4.6 ppm
    assert M.fragment_candidates(p, hi - 1.6e-3) == []  # -5.7 ppm
    six = M.Tolerance(ppm=6.0)
    assert (
        M.fragment_candidates(p, hi - 1.6e-3, tolerance=six)[0].formula == "C7H21O4Si4+"
    )
    # the window is closed: a zero-width window reads a peak at a sub-formula's
    # exact mass, and nothing a hair beside it
    exact = M.fragment_candidates("C10H17+", _mz("C6H9+"))[0].mz_theo
    zero = M.Tolerance(ppm=0.0, mda=0.0)
    assert [
        c.formula for c in M.fragment_candidates("C10H17+", exact, tolerance=zero)
    ] == ["C6H9+"]
    for beside in (np.nextafter(exact, 0.0), np.nextafter(exact, 1e3)):
        assert M.fragment_candidates("C10H17+", float(beside), tolerance=zero) == []
    # the window itself, and the documented defaults
    assert (M.READING_TOLERANCE_PPM, M.READING_TOLERANCE_MDA) == (5.0, 0.3)
    assert M.DEFAULT_TOLERANCE == M.Tolerance() == M.Tolerance(5.0, 0.3)
    assert M.Tolerance().window_da(43.0) == pytest.approx(0.3e-3)
    assert M.Tolerance().window_da(281.0) == pytest.approx(5e-6 * 281.0)


def test_read_fragments_defaults_and_arguments_reach_the_reading():
    """read_fragments' own defaults are the documented 5 ppm / 0.3 mDa / k 3,
    and an explicit tolerance / k_max is what each reading uses. peaky:
    test_assign_fragments_defaults_and_arguments_reach_the_reading."""

    def read(peaks, parent, **kw):
        return _col(M.read_fragments(peaks, parent, **kw), "formula")

    # tolerance ppm: -4.6 ppm reads, -5.7 ppm does not by default, does at 6 ppm
    hi = _mz("C7H21O4Si4+")
    si = "C8H28[15N]O4Si4+"
    assert read([hi - 1.3e-3, hi - 1.6e-3], si) == ["C7H21O4Si4+", ""]
    assert read([hi - 1.6e-3], si, tolerance=M.Tolerance(ppm=6.0)) == ["C7H21O4Si4+"]
    assert read([hi - 1.3e-3], si, tolerance=M.Tolerance(ppm=4.0)) == [""]
    # tolerance mDa: the 0.3 mDa floor, both sides, and an explicit one
    c2 = _mz("C2H3O+")
    assert read(
        [c2 - 0.29e-3, c2 + 0.29e-3, c2 - 0.31e-3, c2 + 0.31e-3], "C11H13O+"
    ) == [
        "C2H3O+",
        "C2H3O+",
        "",
        "",
    ]
    assert read([c2 - 0.25e-3], "C11H13O+", tolerance=M.Tolerance(mda=0.0)) == [""]
    assert read([c2 - 0.31e-3], "C11H13O+", tolerance=M.Tolerance(mda=0.35)) == [
        "C2H3O+"
    ]
    # k_max: the hexose adduct's -H3[15N] -3/-4 H2O need 4 / 5 molecules
    hexose = "C6H16[15N]O6+"
    peaks = [_mz("C6H9O4+"), _mz("C6H7O3+"), _mz("C6H5O2+")]
    assert read(peaks, hexose) == ["C6H9O4+", "", ""]
    assert read(peaks, hexose, k_max=4) == ["C6H9O4+", "C6H7O3+", ""]
    rd = M.read_fragments(peaks, hexose, k_max=5)
    assert _col(rd, "formula") == ["C6H9O4+", "C6H7O3+", "C6H5O2+"]
    assert _col(rd, "loss_pieces") == [3, 4, 5]
    assert read(peaks, hexose, k_max=2) == ["", "", ""]
    # allow_radical and spectrum reach every row
    assert read([_mz("C7H7+")], "C7H8+.", allow_radical=False) == [""]
    assert set(_col(M.read_fragments(peaks, hexose, spectrum="e10"), "spectrum")) == {
        "e10"
    }


# ------------------------------------------------------------- refusals ----
@pytest.mark.parametrize(
    "call",
    [
        # peaks
        lambda: M.read_fragments(pd.DataFrame({"m": [1.0]}), "C10H17+"),
        lambda: M.read_fragments({"intensity": [1.0]}, "C10H17+"),
        lambda: M.read_fragments({"mz": [81.07, 95.08], "intensity": [1.0]}, "C10H17+"),
        lambda: M.read_fragments({"mz": ["x"]}, "C10H17+"),
        lambda: M.read_fragments([NAN], "C10H17+"),
        lambda: M.read_fragments([81.07, -81.07], "C10H17+"),
        lambda: M.read_fragments([81.07, 0.0], "C10H17+"),
        lambda: M.read_fragments([81.07, float("inf")], "C10H17+"),
        lambda: M.read_fragments(pd.DataFrame({"mz": [NAN]}), "C10H17+"),
        # window ions
        lambda: M.read_fragments([81.07], {"a": "C10H17+", "b": "C10H17O5-"}),
        lambda: M.read_fragments([81.07], {}),
        lambda: M.read_fragments([81.07], []),
        lambda: M.read_fragments([81.07], 137.13),
        lambda: M.read_fragments([81.07], None),
        lambda: M.read_fragments([81.07], {"a": None}),
        lambda: M.read_fragments([81.07], {"a": ({"C": 10, "H": 17}, 1)}),
        lambda: M.read_fragments([81.07], [({"C": 10, "H": 17}, 1)]),
        lambda: M.read_fragments([81.07], {"a": "C10H17"}),
        lambda: M.read_fragments([81.07], {"a": "C10H17Xx+"}),
        # the options
        lambda: M.read_fragments([81.07], "C10H17+", offset_ppm=NAN),
        lambda: M.read_fragments([81.07], "C10H17+", offset_ppm=float("inf")),
        lambda: M.read_fragments([81.07], "C10H17+", offset_ppm="5"),
        lambda: M.read_fragments([81.07], "C10H17+", k_max=0),
        lambda: M.read_fragments([81.07], "C10H17+", k_max=7),
        lambda: M.read_fragments([81.07], "C10H17+", tolerance=(5.0, 0.3)),
        lambda: M.read_fragments([81.07], "C10H17+", spectrum=1),
        lambda: M.fragment_candidates("C10H17+", 81.07, tolerance=5.0),
        lambda: M.fragment_candidates("C10H17+", 81.07, k_max=True),
        # m/z
        lambda: M.fragment_candidates("C10H17+", -81.07),
        lambda: M.fragment_candidates("C10H17+", 0.0),
        lambda: M.fragment_candidates("C10H17+", NAN),
        lambda: M.fragment_candidates("C10H17+", float("inf")),
        lambda: M.fragment_candidates("C10H17+", "x"),
        lambda: M.fragment_candidates("C10H17+", None),
        # parents: only an ion formula string goes through parse_ion's checks
        lambda: M.fragment_candidates(({"C": 10, "H": 17}, 1), 81.07),
        lambda: M.fragment_candidates(None, 81.07),
        lambda: M.fragment_candidates(137.13, 81.07),
        lambda: M.fragment_candidates("C10H17++", 81.07),
        # losses
        lambda: M.decompose_loss(""),
        lambda: M.decompose_loss(17),
        lambda: M.decompose_loss({"C": 2, "H": -2}),
        lambda: M.decompose_loss({"Xx": 1}),
        lambda: M.decompose_loss({"C": 2.0}),
        lambda: M.decompose_loss({"C": True}),
        # ions
        lambda: M.format_ion({}, 1),
        lambda: M.format_ion({"C": 0}, 1),
        lambda: M.format_ion({"C": -1, "H": 2}, 1),
        lambda: M.ion_parity({"C": 2, "O": -1}),
        lambda: M.ion_mz("C10H17"),
        # anchors
        lambda: M.mass_offset([]),
        lambda: M.mass_offset([(NAN, "C10H17+")]),
        lambda: M.mass_offset([(-137.1, "C10H17+")]),
        lambda: M.mass_offset([(0.0, "C10H17+")]),
        lambda: M.mass_offset([(137.1325, "C10H17")]),
        lambda: M.mass_offset([(137.1325, "C10H17+"), (81.07, "C6H9++")]),
        # the gate
        lambda: M.ladder_gate([], k=1),
        lambda: M.ladder_gate([], k=7),
        lambda: M.ladder_gate([], k=4.0),
    ],
)
def test_bad_inputs_are_refused(call):
    """Every refusal of the readings, the candidates, the loss decomposition,
    the ions, the anchors and the gate. peaky: test_bad_inputs_raise,
    test_parents_are_checked_ion_formula_strings,
    test_mass_offset_refuses_bad_anchors."""
    with pytest.raises(ValueError):
        call()


@pytest.mark.parametrize(
    ("ppm", "mda"),
    [
        (-1.0, 0.3),
        (5.0, -1.0),
        (NAN, 0.3),
        (5.0, NAN),
        (float("inf"), 0.3),
        ("5", 0.3),
        (True, 0.3),
    ],
)
def test_a_tolerance_is_finite_and_not_negative(ppm, mda):
    """An infinite window would read every sub-formula, a NaN one none. peaky:
    test_bad_inputs_raise (its tol_ppm / tol_mda)."""
    with pytest.raises(ValueError):
        M.Tolerance(ppm=ppm, mda=mda)


def test_empty_formulas_and_surrounding_whitespace():
    """peaky: test_bad_inputs_raise, its empty-formula and whitespace cases."""
    for bad in ("C0+", "C0H0-"):
        with pytest.raises(ValueError, match="empty"):
            M.parse_ion(bad)
    for bad in ({}, {"C": 0}, "C0"):
        with pytest.raises(ValueError, match="empty"):
            M.decompose_loss(bad)
    # one radical mark at most; whitespace is not part of the formula
    for bad in ("C7H8+..", "C7H8+.\u2022", "C7H8+. .", "C7 H8+"):
        with pytest.raises(ValueError):
            M.parse_ion(bad)
    assert M.parse_ion(" C10H17O+ ") == M.parse_ion("C10H17O+")
    assert M.parse_ion("\tC7H8+.\n") == ({"C": 7, "H": 8}, 1)


def test_peaks_must_be_one_dimensional():
    """(mz, intensity) pairs flattened would read every intensity as an m/z.
    peaky: test_peaks_must_be_one_dimensional."""
    for bad in (
        [(81.0698, 132.0), (137.1325, 95.0854)],
        np.array([[81.0698, 132.0], [95.0854, 500.0]]),
        list(zip([81.0698, 95.0854], [57.07, 69.07])),
        {"mz": np.array([[81.0698, 95.0854]])},
    ):
        with pytest.raises(ValueError, match="1-D"):
            M.read_fragments(bad, "C10H17+")
    # 1-D inputs: list, array, Series, a scalar, a mapping, a frame
    for ok in (
        [81.0698, 95.0854],
        np.array([81.0698, 95.0854]),
        pd.Series([81.0698, 95.0854], index=[7, 9]),
        {"mz": [81.0698, 95.0854]},
        pd.DataFrame({"mz": [81.0698, 95.0854]}),
    ):
        rd = M.read_fragments(ok, "C10H17+")
        assert _col(rd, "peak") == [0, 1] and _col(rd, "formula") == ["C6H9+", "C7H11+"]
        # no intensities: NaN, never a measured 0
        assert all(math.isnan(r.intensity) for r in rd)
    scalar = M.read_fragments(81.0698, "C10H17+")
    assert _col(scalar, "formula") == ["C6H9+"]


def test_formula_strings_are_parsed_strictly():
    """peaky: test_formula_strings_are_parsed_strictly."""
    # utils.parse_composition skips what it cannot read; a loss string must not
    for bad in (
        "C2H4o2",
        "H2O-H2",
        "H2O+",
        "2H2O",
        "H2 O",
        "H2O,CO",
        "h2o",
        "C2H4O2x",
        "Xx2",
    ):
        with pytest.raises(ValueError):
            M.decompose_loss(bad)
    assert M.decompose_loss(" C2H4O2 ").pieces == ("C2H4O2",)
    # the middle dot is a radical mark too, and is checked like the others
    assert M.parse_ion("C7H8+\u00b7") == ({"C": 7, "H": 8}, 1)
    with pytest.raises(ValueError):
        M.parse_ion("C7H7+\u00b7")
    # charge: exactly +1 / -1, never a bool
    for bad in (True, False, 2, 0, -2):
        with pytest.raises(ValueError):
            M.format_ion({"C": 6, "H": 9}, bad)
    # caret and bracket spellings of a loss are one loss
    assert M.decompose_loss("H5^NO") == M.decompose_loss("H5[15N]O")
    assert M.decompose_loss({"^N": 1, "H": 3}).pieces == ("H3[15N]",)


def test_duplicate_window_ion_labels_are_refused():
    """Also after str(): a sequence, and a mapping {1, '1'}. peaky:
    test_parents_are_checked_ion_formula_strings."""
    with pytest.raises(ValueError, match="duplicate"):
        M.read_fragments([81.0698], ["C10H17+", "C10H17+"])
    with pytest.raises(ValueError, match="duplicate"):
        M.read_fragments([81.0698], {1: "C10H17+", "1": "C10H20[15N]+"})


def test_oversize_parent_grid_is_refused_up_front():
    """peaky: test_oversize_parent_grid_is_refused_up_front."""
    big = "C100H201O50N4S2+"  # 15.6M sub-formulas > 5M
    assert M.GRID_MAX_ROWS == 5_000_000
    with pytest.raises(ValueError, match="sub-formulas"):
        M.fragment_candidates(big, 300.0)
    for peaks in ([], [300.0]):  # refused with or without peaks
        with pytest.raises(ValueError, match="sub-formulas"):
            M.read_fragments(peaks, {"ok": "C10H17+", "big": big})
    assert len(M.read_fragments([], "C40H70O20N2S+")) == 0


def test_performance_smoke_large_parent():
    """peaky: test_performance_smoke_large_parent (its 1 s bound, doubled for
    CI runners)."""
    parent = "C30H51O15+"  # a protonated C30H50O15
    M.fragment_candidates(parent, 300.0)  # the first call builds the grid
    grid = R._parent_grid(R._ion_key(M.parse_ion(parent)[0]), 1)
    assert 1000 < len(grid.mass) < 31 * 52 * 16
    # every grid row is a sub-formula of the parent that passes the gate
    assert (grid.sub <= np.array([30, 51, 15])).all()
    assert np.all(np.diff(grid.mass) >= 0)
    # probe EXACT sub-formula masses, so every call reaches the loss search: 40
    # random rows plus 20 whose loss needs >= 2 molecules, the loss cache cold
    t2_parent = 2 + int(R._VM2[grid.col] @ np.array([30, 51, 15]))
    multi = np.flatnonzero(t2_parent - grid.t2 + 2 <= -1)
    rng = np.random.default_rng(0)
    rows = np.concatenate(
        [
            rng.choice(len(grid.mass), 40, replace=False),
            rng.choice(multi, 20, replace=False),
        ]
    )
    for k_max in (3, M.K_MAX_LIMIT):
        R._decompose.cache_clear()
        t0 = time.perf_counter()
        cands = [
            M.fragment_candidates(parent, float(grid.mass[r]), k_max=k_max)
            for r in rows
        ]
        assert time.perf_counter() - t0 < 2.0, k_max
        assert R._decompose.cache_info().misses >= len(rows)  # the search ran
        assert sum(1 for c in cands if c) >= 15
        assert any(x.loss_pieces >= 2 for c in cands for x in c)


@functools.lru_cache(maxsize=None)
def _lib_multisets(n_lib: int, k: int):
    """Every multiset of k of the first n_lib library pieces, in lexicographic
    index order -> (indices, element sums, radical-piece count)."""
    lib = R._LIB_VEC[:n_lib].astype(np.int64)
    is_rad = (np.arange(n_lib) >= R._N_CLOSED).astype(np.int64)
    combos = list(itertools.combinations_with_replacement(range(n_lib), k))
    cs = np.array(combos, dtype=np.int64).reshape(len(combos), k)
    return cs, lib[cs].sum(axis=1), is_rad[cs].sum(axis=1)


def _brute_decompose(counts: dict, k_max: int):
    """decompose_loss by exhaustive enumeration with NO pruning: every library
    multiset of each size in lexicographic index order, all-named first, then
    named + one unnamed remainder judged by the module's own remainder rule
    (pinned clause by clause in test_unnamed_radical_remainders). The pruned
    search must give exactly this."""
    loss = R._vec(counts)
    radical = bool((2 + int(loss @ R._VM2)) % 2)
    n_lib = len(R._LIB_NAMES) if radical else R._N_CLOSED
    for k in range(1, k_max + 1):
        cs, tot, nrad = _lib_multisets(n_lib, k)
        hit = np.flatnonzero((tot == loss).all(axis=1) & (nrad <= 1))
        if len(hit):
            return tuple(R._LIB_NAMES[i] for i in cs[hit[0]]), 0
        cs, tot, nrad = _lib_multisets(n_lib, k - 1)
        rest = loss[None, :] - tot
        ok = (
            (rest >= 0).all(axis=1)
            & rest.any(axis=1)
            & (nrad <= 1)
            & R._remainder_ok(rest, radical & (nrad == 0))
        )
        hit = np.flatnonzero(ok)
        if len(hit):
            i = hit[0]
            return tuple(R._LIB_NAMES[j] for j in cs[i]) + (R._formula_of(rest[i]),), 1
    return None


def test_pruned_search_equals_exhaustive_enumeration():
    """The search drops frontier multisets whose rest needs more pieces than are
    left. That bound must never drop a valid split: compared with the unpruned
    enumeration on H-poor, halogenated, radical and closed losses (where the
    rest after a library radical is closed-shell, and the radical bound would
    over-prune it), at k_max 2 and 3. peaky:
    test_pruned_search_equals_exhaustive_enumeration."""
    rng = np.random.default_rng(11)
    hi = {
        "C": 4,
        "H": 7,
        "O": 4,
        "N": 2,
        "[15N]": 2,
        "S": 2,
        "F": 6,
        "Cl": 3,
        "Br": 2,
        "I": 2,
    }
    losses = [
        "CClF4",
        "BrF2",
        "C2BrF6HI",
        "CBrF3",
        "C2Cl2F4",
        "CHF3Br",
        "C3H7",
        "C2H7",
        "H2N",
        "C3H10N",
        "CClF3O",
        "C2F5I",
    ]
    while len(losses) < 400:
        c = {e: int(rng.integers(0, n)) for e, n in hi.items() if rng.random() < 0.45}
        c = {e: n for e, n in c.items() if n}
        if c:
            losses.append(to_hill_order(c))
    for k_max in (2, 3):
        R._decompose.cache_clear()
        n_read = 0
        for loss in losses:
            got = M.decompose_loss(loss, k_max=k_max)
            want = _brute_decompose(_counts(loss), k_max)
            assert (got and (got.pieces, got.unnamed)) == (want or None), (loss, k_max)
            n_read += got is not None
        assert n_read > 100, k_max  # the comparison is not all-None


def _radical_sums(n: int) -> list[str]:
    """Every distinct formula that is the sum of n RADICAL_LOSSES entries."""
    out = set()
    for combo in itertools.combinations_with_replacement(M.RADICAL_LOSSES, n):
        tot: dict[str, int] = {}
        for piece in combo:
            for e, k in _counts(piece).items():
                tot[e] = tot.get(e, 0) + k
        out.add(to_hill_order(tot))
    return sorted(out)


def test_a_loss_holds_at_most_one_radical_piece():
    """A radical loss is exactly ONE radical piece plus closed-shell ones; a
    closed-shell loss has none. The library has no closed-shell 15N oxide, so
    [15N]O + [15N]O2 + [15N]O3 ([15N]3O6) is no reading at k_max 3 or 4, where
    every split would need two or three radicals, and two radicals never make
    a closed-shell loss ([15N]2O3 = [15N]O + [15N]O2). The 14N N3O6 reads, as
    closed-shell pieces around one radical, only because N2O is named. peaky:
    test_a_loss_holds_at_most_one_radical_piece."""
    for loss, k_max in (
        ("[15N]3O6", 3),
        ("[15N]3O6", 4),
        ("N[15N]2O3", 3),
        ("Br2I3", 4),
        ("[15N]2O3", 3),
        ("[15N]2O3", M.K_MAX_LIMIT),
    ):
        assert M.decompose_loss(loss, k_max=k_max) is None, (loss, k_max)
    d = M.decompose_loss("N3O6")
    assert (d.pieces, d.unnamed, d.radical) == (("O2", "N2O", "NO3"), 0, True)
    # every sum of two (closed-shell losses) or three (radical losses) library
    # radicals against the unpruned enumeration, which counts radical pieces
    for n, k_max in itertools.product((2, 3), (3, 4)):
        R._decompose.cache_clear()
        n_none = 0
        for loss in _radical_sums(n):
            got = M.decompose_loss(loss, k_max=k_max)
            want = _brute_decompose(_counts(loss), k_max)
            assert (got and (got.pieces, got.unnamed)) == (want or None), (loss, k_max)
            assert got is None or (
                got.radical == (n == 3)
                and sum(p in M.RADICAL_LOSSES for p in got.pieces) <= int(got.radical)
            ), (loss, got)
            n_none += got is None
        assert n_none >= 10, (n, k_max)  # the rule refuses some of them


# Deep losses (k 5-6) where the frontier would grow combinatorially without the
# k-bound pruning: synthetic, found by a random search over large halogenated /
# S / N / Si losses; each splits into six pieces. peaky: _DEEP_LOSSES.
_DEEP_LOSSES = (
    "C6H18BrF7N3O7S2",
    "C6H23Cl2N2O7",
    "C7H25BrNO6",
    "C11H25Cl3F7IN[15N]O4S",
    "C6H21F7IN3O5SSi",
    "C6H16BrCl2F4O4",
    "C3H18BrCl2I[15N]O5Si2",
    "C6H22BrO6",
)


def test_deep_losses_decompose_fast_at_the_k_max_cap():
    """About 0.1 s together with the pruning, seconds without it. peaky:
    test_deep_losses_decompose_fast_at_the_k_max_cap (its 0.6 s bound, doubled
    for CI runners)."""
    R._decompose.cache_clear()
    t0 = time.perf_counter()
    decs = [M.decompose_loss(f, k_max=M.K_MAX_LIMIT) for f in _DEEP_LOSSES]
    elapsed = time.perf_counter() - t0
    assert all(d is not None and d.k == 6 for d in decs), [d and d.name for d in decs]
    assert elapsed < 1.2, elapsed


# ------------------------------------------------------- the ladder gate ----
HEX = "C6H16NO6+"  # hexose + NH4+ (14N)
#: the chain: -NH3, then water step by step; -NH3 -3 H2O (C6H7O3+, loss H9NO3,
#: DBE -3) is the first step that needs four molecules
CHAIN = ("C6H16NO6+", "C6H13O6+", "C6H11O5+", "C6H9O4+", "C6H7O3+")


def _spec(nce, ions, key="198.0972"):
    """(spectrum key, peaks): one energy of a window, its peaks at the exact
    m/z of `ions` (or the m/z given), brightest first."""
    mz = [_mz(i) if isinstance(i, str) else i for i in ions]
    return f"{key}@hcd{nce:.2f}", {
        "mz": mz,
        "intensity": np.arange(len(mz), 0, -1, dtype=float) * 100,
    }


def _window(steps, ions, *, k_max=M.LADDER_K, gate=True, tolerance=M.DEFAULT_TOLERANCE):
    """One window's readings across its energies: each spectrum read at
    `k_max`, then the ladder gate (`gate`). peaky's read_window: the default is
    k_max 4 gated, `ladder=False` is k_max 3 ungated, `k_max=4` ungated."""
    rows = []
    for key, peaks in steps:
        rows += M.read_fragments(
            peaks, ions, k_max=k_max, tolerance=tolerance, spectrum=key
        )
    return M.ladder_gate(rows) if gate else tuple(rows)


def _reading(readings, spectrum, formula, parent=None):
    rows = [
        r
        for r in readings
        if r.spectrum == spectrum
        and r.formula == formula
        and (parent is None or r.parent == parent)
    ]
    return rows[0] if rows else None


def test_the_ladder_admits_the_fourth_step_only_when_the_third_is_read():
    """The hexose [M+NH4]+ chain, through the gate and not without it. C6H7O3+
    (-NH3 -3 H2O) needs four pieces; it is read when the same window ion's
    previous step is read at some energy: C6H9O4+ (-NH3 -2 H2O, loss H7NO2 =
    this loss minus H2O) - or C6H10NO3+ (-3 H2O, this loss minus NH3). peaky:
    test_ladder_admits_the_fourth_step_only_when_the_third_is_read."""
    lone = [_spec(1, CHAIN[:3]), _spec(10, [CHAIN[0], CHAIN[4]])]
    rd = _window(lone, {"hex": HEX})
    p127 = [
        r
        for r in rd
        if r.spectrum == "198.0972@hcd10.00" and math.isclose(r.mz, _mz("C6H7O3+"))
    ]
    assert len(p127) == 1 and p127[0].rank == 0  # unexplained
    assert (p127[0].formula, p127[0].n_parents) == ("", 0)
    # the step before it is the ladder, at a lower, the same or a higher energy
    for steps in (
        [_spec(1, CHAIN[:4]), _spec(10, [CHAIN[0], CHAIN[4]])],
        [_spec(1, CHAIN[:3]), _spec(10, [CHAIN[0], CHAIN[3], CHAIN[4]])],
        [
            _spec(1, CHAIN[:3]),
            _spec(10, [CHAIN[0], CHAIN[4]]),
            _spec(20, [CHAIN[0], CHAIN[3]]),
        ],
    ):
        got = _reading(_window(steps, {"hex": HEX}), "198.0972@hcd10.00", "C6H7O3+")
        assert got is not None
        assert (got.loss, got.loss_name, got.loss_pieces) == (
            "H9NO3",
            "H2O + H2O + H2O + H3N",
            4,
        )
        assert (got.ladder_support, got.rank, got.n_candidates) == ("H7NO2", 1, 1)
    # -3 H2O keeping the ammonium (C6H10NO3+) is the other ladder
    rd = _window(
        [_spec(1, [CHAIN[0], "C6H10NO3+"]), _spec(10, [CHAIN[0], CHAIN[4]])],
        {"hex": HEX},
    )
    assert _reading(rd, "198.0972@hcd10.00", "C6H7O3+").ladder_support == "H6O3"
    # both previous steps read: the support is the FIRST piece, in the
    # decomposition's order (H2O + H2O + H2O + H3N), that finds one - H2O, whose
    # step is H7NO2 - not the last (H3N -> H6O3)
    rd = _window(
        [_spec(1, [CHAIN[0], CHAIN[3], "C6H10NO3+"]), _spec(10, [CHAIN[0], CHAIN[4]])],
        {"hex": HEX},
    )
    assert {
        r.loss
        for r in rd
        if r.spectrum == "198.0972@hcd1.00" and r.rank == 1 and not r.is_precursor
    } == {"H7NO2", "H6O3"}
    got = _reading(rd, "198.0972@hcd10.00", "C6H7O3+")
    assert (got.loss_name, got.ladder_support) == ("H2O + H2O + H2O + H3N", "H7NO2")
    # strict k_max 3 never reads it; an explicit k_max 4 always does, ungated
    steps = [_spec(1, CHAIN[:4]), _spec(10, [CHAIN[0], CHAIN[4]])]
    strict = _window(steps, {"hex": HEX}, k_max=3, gate=False)
    assert _reading(strict, "198.0972@hcd10.00", "C6H7O3+") is None
    k4 = _reading(
        _window(lone, {"hex": HEX}, gate=False), "198.0972@hcd10.00", "C6H7O3+"
    )
    assert k4 is not None and k4.ladder_support == ""
    # the support is each window ion's own. C7H11O4+ = C7H18NO6+ - H7NO2 is read
    # by the other ion only (the hexose adduct holds six C): it does not admit
    # the adduct's four-piece C6H7O3+ ... but it does admit the other ion's own,
    # whose loss CH11NO3 = H2O + H2O + H3N + CH4O is H7NO2 + CH4O
    other = _window(
        [_spec(1, [CHAIN[0], "C7H11O4+"]), _spec(10, [CHAIN[0], CHAIN[4]])],
        {"other": "C7H18NO6+", "hex": HEX},
    )
    o = _reading(other, "198.0972@hcd1.00", "C7H11O4+")
    assert (o.parent, o.loss, o.n_parents) == ("other", "H7NO2", 1)
    assert _reading(other, "198.0972@hcd10.00", "C6H7O3+", "hex") is None
    mine = _reading(other, "198.0972@hcd10.00", "C6H7O3+", "other")
    assert (mine.loss_name, mine.ladder_support, mine.n_parents) == (
        "H2O + H2O + H3N + CH4O",
        "H7NO2",
        1,
    )


def test_the_labelled_hexose_chain_reads_through_the_gate_and_not_without_it():
    """The hexose [M+[15N]H4]+ chain of the long-dehydration test, across
    energies: -H3[15N] -3 H2O (four pieces) reads when -H3[15N] -2 H2O is read
    at another energy, and neither at k_max 3 nor alone through the gate."""
    p = "C6H16[15N]O6+"
    chain = (p, "C6H13O6+", "C6H11O5+", "C6H9O4+", "C6H7O3+")
    key = f"{_mz(p):.4f}"
    ladder = [_spec(1, chain[:4], key), _spec(10, [p, chain[4]], key)]
    got = _reading(_window(ladder, {"hex": p}), f"{key}@hcd10.00", "C6H7O3+")
    assert (got.loss, got.loss_name, got.ladder_support) == (
        "H9[15N]O3",
        "H2O + H2O + H2O + H3[15N]",
        "H7[15N]O2",
    )
    assert (
        _reading(
            _window(ladder, {"hex": p}, k_max=3, gate=False),
            f"{key}@hcd10.00",
            "C6H7O3+",
        )
        is None
    )
    lone = [_spec(1, chain[:3], key), _spec(10, [p, chain[4]], key)]
    assert _reading(_window(lone, {"hex": p}), f"{key}@hcd10.00", "C6H7O3+") is None


def test_the_ladder_step_need_not_be_a_printed_piece():
    """A four-piece loss prints ONE decomposition - fewest pieces, then fewest
    unnamed, then library order - and it need not hold the piece the ladder
    took. C9H22NO6+ -> C8H11O+ loses CH11NO5, printed H3N + CH4O + H2O2 + H2O2
    (no H2O: the decompositions with water need an unnamed piece); C8H13O2+
    (loss CH9NO4, H2O + H2O + CH5NO2) is read, one H2O back. That step admits
    the reading. peaky: test_the_ladder_step_need_not_be_a_printed_piece."""
    p = "C9H22NO6+"
    dec = M.decompose_loss("CH11NO5", k_max=4)
    assert dec.name == "H3N + CH4O + H2O2 + H2O2" and "H2O" not in dec.pieces
    assert M.decompose_loss("CH9NO4", k_max=3).name == "H2O + H2O + CH5NO2"
    key = f"{_mz(p):.4f}"
    for steps in (
        [_spec(1, [p, "C8H13O2+"], key), _spec(10, [p, "C8H11O+"], key)],
        [_spec(10, [p, "C8H13O2+", "C8H11O+"], key)],
    ):
        got = _reading(_window(steps, {"p": p}), f"{key}@hcd10.00", "C8H11O+")
        assert got is not None
        assert (got.loss, got.loss_pieces, got.rank, got.ladder_support) == (
            "CH11NO5",
            4,
            1,
            "CH9NO4",
        )
    # no step one molecule back: unexplained, as before
    rd = _window([_spec(1, [p], key), _spec(10, [p, "C8H11O+"], key)], {"p": p})
    lone = [
        r
        for r in rd
        if r.spectrum == f"{key}@hcd10.00" and math.isclose(r.mz, _mz("C8H11O+"))
    ]
    assert len(lone) == 1 and lone[0].rank == 0
    # one molecule means one named closed-shell piece: not an unnamed remainder,
    # not a radical, not two
    v = R._vec
    assert R._one_molecule(v(_counts("H2O"))) and R._one_molecule(v(_counts("H3N")))
    assert not R._one_molecule(v(_counts("CH5NO2")))  # unnamed
    assert not R._one_molecule(v(_counts("HO")))  # a radical
    assert not R._one_molecule(v(_counts("H4O2")))  # two waters
    step = {tuple(v(_counts("CH9NO4")).tolist())}
    assert R._ladder_step("CH11NO5", ("H3N", "CH4O", "H2O2", "H2O2"), step) is not None
    two_back = {tuple(v(_counts("CH7NO3")).tolist())}
    assert R._ladder_step("CH11NO5", ("H3N", "CH4O", "H2O2", "H2O2"), two_back) is None


def test_with_no_printed_step_the_first_read_step_is_named():
    """Where no printed piece finds a step and two steps one molecule back are
    read, `ladder_support` names the first in the lexicographic order of their
    count vectors on ELEMENTS, whichever was read first (synthetic; peaky gives
    the same). C4H10NO3+ -> C4H+ loses H9NO3, printed H2O + H2O + H2O + H3N;
    the steps one H2O or one H3N back are not read, the ones one H2O2 back
    (H7NO, C4H3O2+) and one H2 back (H7NO3, C4H3+) are."""
    p = "C4H10NO3+"
    key = f"{_mz(p):.4f}"
    for first, support in (
        (["C4H3O2+", "C4H3+"], "H7NO"),
        (["C4H3+", "C4H3O2+"], "H7NO"),
        (["C4H3+"], "H7NO3"),
        (["C4H3O2+"], "H7NO"),
    ):
        steps = [_spec(1, [p, *first], key), _spec(10, [p, "C4H+"], key)]
        got = _reading(_window(steps, {"p": p}), f"{key}@hcd10.00", "C4H+")
        assert (got.loss, got.loss_name, got.ladder_support) == (
            "H9NO3",
            "H2O + H2O + H2O + H3N",
            support,
        ), first


def test_a_printed_unnamed_or_radical_piece_is_no_ladder_step():
    """A printed piece is a step only when it is one named closed-shell molecule
    - the rule for an unprinted one. The hexose [M+NH4]+ after its -3 H2O step
    C6H10NO3+ (loss H6O3) reads C5H5+ as H2O + H2O + H2O + CH5NO3 (an unnamed
    remainder), and after C6H9O4+ (loss H7NO2) reads C6H8O3+ as H2O + H2O + H3N
    + HO (OH): both are unexplained, as when alone; ungated k_max 4 still reads
    them. peaky: test_a_printed_unnamed_or_radical_piece_is_no_ladder_step."""
    v = R._vec

    def step(f):
        return {tuple(v(_counts(f)).tolist())}

    assert (
        R._ladder_step("CH11NO6", ("H2O", "H2O", "H2O", "CH5NO3"), step("H6O3")) is None
    )
    assert R._ladder_step("H8NO3", ("H2O", "H2O", "H3N", "HO"), step("H7NO2")) is None
    assert R._ladder_step("H8NO4", ("H2O", "H2O", "H3N", "HO2"), step("H7NO3")) is None
    assert M.decompose_loss("H8NO3", k_max=4).name == "H2O + H2O + H3N + HO"
    assert M.decompose_loss("H8NO4", k_max=4).name == "H2O + H2O + H3N + HO2"
    # a printed named closed-shell piece still is one (H2O: the hexose ladder)
    prev = R._ladder_step("H9NO3", ("H2O", "H2O", "H2O", "H3N"), step("H7NO2"))
    assert R._formula_of(prev) == "H7NO2"
    for first, frag, loss, name in (
        ("C6H10NO3+", "C5H5+", "CH11NO6", "H2O + H2O + H2O + CH5NO3"),
        ("C6H9O4+", "C6H8O3+", "H8NO3", "H2O + H2O + H3N + HO"),
    ):
        steps = [_spec(1, [HEX, first]), _spec(10, [HEX, frag])]
        rd = _window(steps, {"hex": HEX})
        r = [
            x
            for x in rd
            if x.spectrum == "198.0972@hcd10.00" and math.isclose(x.mz, _mz(frag))
        ]
        assert len(r) == 1 and r[0].rank == 0 and r[0].formula == "", frag
        k4 = _reading(
            _window(steps, {"hex": HEX}, gate=False), "198.0972@hcd10.00", frag
        )
        assert (k4.loss, k4.loss_name, k4.loss_pieces) == (loss, name, 4)


def test_readings_rebuilt_from_a_frame_gate_alike():
    """A caller that keeps the readings as a table (`readings_frame`) and
    rebuilds them has no `pieces` tuple: the gate reads the printed pieces off
    `loss_name`, so the support is still the first printed piece's step (H2O ->
    H7NO2), not the first read step in the axis's order (H6O3)."""
    steps = [
        _spec(1, [CHAIN[0], CHAIN[3], "C6H10NO3+"]),
        _spec(10, [CHAIN[0], CHAIN[4]]),
    ]
    rows = _window(steps, {"hex": HEX}, gate=False)
    frame = M.readings_frame(rows)
    rebuilt = [M.Reading(**rec) for rec in frame.to_dict("records")]
    assert all(r.pieces == () for r in rebuilt)
    for gated in (M.ladder_gate(rows), M.ladder_gate(rebuilt)):
        got = _reading(gated, "198.0972@hcd10.00", "C6H7O3+")
        assert (got.loss_name, got.ladder_support) == ("H2O + H2O + H2O + H3N", "H7NO2")


def _table(rows):
    """Readings from (spectrum, peak, parent, formula, loss, loss_name, pieces,
    radical, rank) rows, the rest filled consistently - as a caller rebuilds
    them from a table, with no `pieces` tuple (the gate reads loss_name)."""
    out = []
    for spec, peak, parent, formula, loss, name, k, radical, rank in rows:
        out.append(
            M.Reading(
                spec,
                peak,
                50.0 + peak,
                50.0 + peak,
                10.0,
                parent,
                parent,
                formula,
                "EE" if formula else "",
                1.5 if formula else NAN,
                0.0 if formula else NAN,
                loss,
                k,
                name,
                0,
                radical,
                False,
                rank,
                0,
                0,
            )
        )
    n_cand: dict = {}
    parents: dict = {}
    for r in out:
        if r.rank:
            n_cand[(r.spectrum, r.peak, r.parent)] = (
                n_cand.get((r.spectrum, r.peak, r.parent), 0) + 1
            )
            parents.setdefault((r.spectrum, r.peak), set()).add(r.parent)
    return tuple(
        dataclasses.replace(
            r,
            n_candidates=n_cand[(r.spectrum, r.peak, r.parent)],
            n_parents=len(parents[(r.spectrum, r.peak)]),
        )
        if r.rank
        else r
        for r in out
    )


FOUR = ("H2O + H2O + H2O + H3N", "H9NO3")


def test_an_unsupported_fourth_piece_is_dropped_and_the_peak_re_ranked():
    """peaky: test_an_unsupported_fourth_piece_is_dropped_and_the_peak_re_ranked."""
    rd = _table(
        [
            # peak 0: a closed-shell 4-piece reading outranks a radical 1-piece one
            ("s1", 0, "A", "C5H7O3+", FOUR[1], FOUR[0], 4, False, 1),
            ("s1", 0, "A", "C5H6O3+", "H10NO3", "H10NO3", 1, True, 2),
            # peak 1: read by A only as a 4-piece, by B as one piece
            ("s1", 1, "A", "C4H5O3+", FOUR[1], FOUR[0], 4, False, 1),
            ("s1", 1, "B", "C4H5O3+", "H2O", "H2O", 1, False, 1),
            # peak 2: its only reading is an unsupported 4-piece
            ("s1", 2, "A", "C3H3O3+", FOUR[1], FOUR[0], 4, False, 1),
            # peak 3: never read
            ("s1", 3, "", "", "", "", 0, False, 0),
        ]
    )
    out = M.ladder_gate(rd)
    p0 = _rows(out, 0)
    assert len(p0) == 1
    assert (p0[0].formula, p0[0].rank, p0[0].n_candidates, p0[0].n_parents) == (
        "C5H6O3+",
        1,
        1,
        1,
    )
    p1 = _rows(out, 1)
    assert _col(p1, "parent") == ["B"] and p1[0].n_parents == 1
    p2 = _rows(out, 2)
    assert len(p2) == 1 and p2[0].rank == 0 and p2[0].formula == ""
    assert p2[0].n_parents == 0 and math.isnan(p2[0].ppm)
    assert p2[0].intensity == 10.0 and p2[0].mz == 52.0
    assert _col(out, "peak") == [0, 1, 2, 3]  # order kept
    assert out[-1] == rd[-1]  # an unexplained row stays as it is
    # supported at some energy -> kept as it is, with its support
    sup = rd + _table(
        [("s2", 0, "A", "C5H9O4+", "H7NO2", "H2O + H2O + H3N", 3, False, 1)]
    )
    out = M.ladder_gate(sup)
    kept = _rows(out, 0, spectrum="s1")
    assert _col(kept, "formula") == ["C5H7O3+", "C5H6O3+"] and _col(kept, "rank") == [
        1,
        2,
    ]
    assert kept[0].ladder_support == "H7NO2"
    # the support is what k_max 3 would rank first: a <= 3-piece reading behind a
    # 4-piece one (here a closed 4-piece outranks a radical 3-piece) counts
    behind = rd + _table(
        [
            ("s2", 0, "A", "C4H3O2+", "H11NO4", "H2O + H2O + H2O + H5NO", 4, False, 1),
            ("s2", 0, "A", "C5H9O4+", "H7NO2", "H2O + H2O + H3N", 3, True, 2),
        ]
    )
    out = M.ladder_gate(behind)
    assert _rows(out, 0, spectrum="s1")[0].ladder_support == "H7NO2"
    # ... and a 4-piece reading supports nothing (s2's H11NO4 is dropped)
    assert "C4H3O2+" not in set(_col(out, "formula"))
    # a support that is not first among the <= 3-piece readings does not count
    weak = rd + _table(
        [
            ("s2", 0, "A", "C9H9+", "H2", "H2", 1, False, 1),
            ("s2", 0, "A", "C5H9O4+", "H7NO2", "H2O + H2O + H3N", 3, False, 2),
        ]
    )
    assert "C5H7O3+" not in set(_col(M.ladder_gate(weak), "formula"))
    # nor is a precursor reading a step: the first non-precursor reading is
    pre = rd + _table(
        [
            ("s2", 0, "A", "C6H16NO6+", "", "", 0, False, 1),
            ("s2", 0, "A", "C5H9O4+", "H7NO2", "H2O + H2O + H3N", 3, False, 2),
        ]
    )
    pre = tuple(
        dataclasses.replace(r, is_precursor=True) if r.formula == "C6H16NO6+" else r
        for r in pre
    )
    assert _rows(M.ladder_gate(pre), 0, spectrum="s1")[0].ladder_support == "H7NO2"


def test_the_gate_drops_what_lies_above_k_and_reads_its_own_k():
    """Readings above `k` pieces are dropped (a window read at k_max 5); `k`
    itself is the gate's argument (`LADDER_K`, 4, by default)."""
    p = "C6H16[15N]O6+"
    key = f"{_mz(p):.4f}"
    five = [_spec(1, [p, "C6H9O4+", "C6H7O3+"], key), _spec(10, [p, "C6H5O2+"], key)]
    rd = _window(five, {"hex": p}, k_max=5, gate=False)
    assert {r.loss_pieces for r in rd if r.rank} == {0, 3, 4, 5}
    gated = M.ladder_gate(rd)
    assert max(r.loss_pieces for r in gated) == 4
    assert _reading(gated, f"{key}@hcd1.00", "C6H7O3+").ladder_support == "H7[15N]O2"
    assert _reading(gated, f"{key}@hcd10.00", "C6H5O2+") is None
    # at k 5 the five-piece step stands on the four-piece one, read first among
    # its <= 4-piece readings
    at5 = M.ladder_gate(rd, k=5)
    assert _reading(at5, f"{key}@hcd10.00", "C6H5O2+").ladder_support == "H9[15N]O3"
    assert M.LADDER_K == 4


def test_the_gate_re_ranks_a_real_window():
    """At 20 ppm the [15N]H4+ hexose's 113.0233 reads CH6[15N]O5+ (one piece,
    19.8 ppm) and C5H5O3+ (four pieces, 0 ppm); the gate drops the second and
    the first is alone. peaky: test_the_gate_re_ranks_a_real_window."""
    steps = [_spec(1, [_mz("C6H16[15N]O6+"), 113.02332], key="199.0942")]
    wide = M.Tolerance(ppm=20.0)
    k4 = _window(steps, {"h": "C6H16[15N]O6+"}, tolerance=wide, gate=False)
    gated = _window(steps, {"h": "C6H16[15N]O6+"}, tolerance=wide)
    r4 = _rows(k4, 1)
    assert _col(r4, "formula") == ["CH6[15N]O5+", "C5H5O3+"]
    assert _col(r4, "n_candidates") == [2, 2]
    rg = _rows(gated, 1)
    assert _col(rg, "formula") == ["CH6[15N]O5+"] and _col(rg, "n_candidates") == [1]
    assert gated == _window(
        steps, {"h": "C6H16[15N]O6+"}, tolerance=wide, k_max=3, gate=False
    )


def test_a_drop_keeps_the_spectrum_and_peak_order():
    """The gate keeps the order of everything it does not drop: spectra in the
    order given (NCE 1, 2, 10 - not the keys' text order 1, 10, 2), each
    spectrum's peaks in input order, the dropped peak in its place as one
    unexplained row; every other row is the ungated k_max 4 one. peaky:
    test_a_drop_keeps_the_spectrum_and_peak_order."""
    steps = [
        _spec(1, CHAIN[:3]),
        _spec(2, [CHAIN[0], CHAIN[1]]),
        _spec(10, [CHAIN[0], CHAIN[4]]),
    ]
    keys = ["198.0972@hcd1.00", "198.0972@hcd2.00", "198.0972@hcd10.00"]
    gated = _window(steps, {"hex": HEX})
    k4 = _window(steps, {"hex": HEX}, gate=False)
    assert list(dict.fromkeys(_col(gated, "spectrum"))) == keys
    assert [(r.spectrum, r.peak) for r in gated] == [(r.spectrum, r.peak) for r in k4]
    dropped = [r.loss_pieces == 4 for r in k4]
    assert sum(dropped) == 1
    assert [g.rank for g, d in zip(gated, dropped) if d] == [0]
    assert [g for g, d in zip(gated, dropped) if not d] == [
        k for k, d in zip(k4, dropped) if not d
    ]
    assert M.ladder_gate([]) == ()


def test_re_ranking_is_within_each_window_ion():
    """After a drop, ranks are recomputed per (spectrum, peak, parent): a peak
    two window ions read keeps a rank 1 for each, and both stay its candidates.
    peaky: test_re_ranking_is_within_each_window_ion."""
    rd = _table(
        [
            # A: an unsupported 4-piece rank 1, then a 1-piece radical; B: one piece
            ("s1", 0, "A", "C5H7O3+", FOUR[1], FOUR[0], 4, False, 1),
            ("s1", 0, "A", "C5H6O3+", "H10NO3", "H10NO3", 1, True, 2),
            ("s1", 0, "B", "C5H7O3+", "H2O", "H2O", 1, False, 1),
        ]
    )
    out = M.ladder_gate(rd)
    assert _col(out, "parent") == ["A", "B"] and _col(out, "rank") == [1, 1]
    assert _col(out, "n_candidates") == [1, 1] and _col(out, "n_parents") == [2, 2]
    # through the readings: the hexose's lone four-piece C6H7O3+ is dropped (the
    # readings re-rank), and C6H11O5+, which both window ions read, keeps both
    steps = [_spec(1, [HEX, CHAIN[2]]), _spec(10, [HEX, CHAIN[2], CHAIN[4]])]
    rd = _window(steps, {"hex": HEX, "w": CHAIN[1]})
    assert "C6H7O3+" not in {r.formula for r in rd if r.parent == "hex"}
    for spec in ("198.0972@hcd1.00", "198.0972@hcd10.00"):
        r = [x for x in rd if x.spectrum == spec and x.formula == CHAIN[2]]
        assert _col(r, "parent") == ["hex", "w"] and _col(r, "rank") == [1, 1]
        assert _col(r, "n_parents") == [2, 2]


# ------------------------------------------------------------------ (D) ----
_GOLDEN = json.loads((FIXTURES / "peaky_readings.json").read_text(encoding="utf-8"))


def _canon(formula: str) -> str:
    return to_hill_order(_counts(formula)) if formula else ""


def _canon_ion(ion: str) -> str:
    return M.format_ion(*M.parse_ion(ion)) if ion else ""


@pytest.mark.parametrize(
    "window",
    _GOLDEN["windows"],
    ids=[f"{w['fixture']}-{w['parent_mz']}" for w in _GOLDEN["windows"]],
)
def test_d_peakys_readings_of_its_public_fixtures_are_reproduced(window):
    """Every peak of every window of peaky's three public fixtures (the
    exploratory 15N-ammonium file and the two monoterpene standards), read
    against the window ions and at the offset peaky's windowing gave it: every
    best reading peaky's `assign_fragments` made, field for field, and its ppm
    to 0.01 (the two mass tables differ in the last digits). And no peak needs a
    fourth piece: the gate at k_max 4 reads each window exactly as k_max 3 does.
    peaky: test_no_peak_of_our_files_needs_a_fourth_piece, and the readings
    under test_exploratory_file_calls."""
    ions = [_canon_ion(f) for f in window["window_ions"]]
    strict, at4 = [], []
    for s in window["spectra"]:
        peaks = {"mz": s["mz"], "intensity": s["intensity"]}
        kw = {"offset_ppm": window["offset_ppm"], "spectrum": s["spectrum"]}
        rd = M.read_fragments(peaks, ions, **kw)
        strict += rd
        at4 += M.read_fragments(peaks, ions, k_max=M.LADDER_K, **kw)
        assert len(rd) == s["n_rows"], s["spectrum"]
        best = M.best_readings(rd)
        assert len(best) == len(s["best"]), s["spectrum"]
        for r, want in zip(best, s["best"]):
            (
                peak,
                parent,
                formula,
                loss,
                name,
                unnamed,
                radical,
                pre,
                rank,
                n_c,
                n_p,
                ppm,
            ) = want
            assert (
                r.peak,
                r.parent,
                r.formula,
                r.loss,
                r.loss_name,
                r.unnamed_pieces,
                r.radical_loss,
                r.is_precursor,
                r.rank,
                r.n_candidates,
                r.n_parents,
            ) == (
                peak,
                _canon_ion(parent),
                _canon_ion(formula),
                _canon(loss),
                " + ".join(_canon(p) for p in name.split(" + ")) if name else "",
                unnamed,
                radical,
                pre,
                rank,
                n_c,
                n_p,
            ), (s["spectrum"], want)
            assert r.intensity == s["intensity"][peak]
            if ppm is None:
                assert math.isnan(r.ppm)
            else:
                assert r.ppm == pytest.approx(ppm, abs=0.01)
    assert M.ladder_gate(at4) == tuple(strict)


def test_d_the_golden_covers_every_window_and_reading_kind():
    """The golden is what its docstring says: 11 windows, 47 spectra, 656
    readings, with precursor, fragment, unexplained, radical and two-parent
    readings among them."""
    windows = _GOLDEN["windows"]
    best = [b for w in windows for s in w["spectra"] for b in s["best"]]
    assert (len(windows), sum(len(w["spectra"]) for w in windows), len(best)) == (
        11,
        47,
        656,
    )
    assert {w["fixture"] for w in windows} == {
        "ms2_nh4_exploratory",
        "ms2_monoterpene_a",
        "ms2_monoterpene_b",
    }
    assert any(b[7] for b in best)  # a precursor
    assert any(b[8] == 0 for b in best)  # unexplained
    assert any(b[6] for b in best)  # a radical loss
    assert any(b[10] == 2 for b in best)  # two parents
    assert any(b[5] for b in best)  # an unnamed piece
    assert any("^N" in b[3] for b in best)  # a labelled loss


# --------------------------------------------------------------- the doc ----
_CONSTANTS = (
    "READING_TOLERANCE_PPM",
    "READING_TOLERANCE_MDA",
    "K_MAX_DEFAULT",
    "K_MAX_LIMIT",
    "LADDER_K",
    "GRID_MAX_ROWS",
    "FRAGMENT_DBE_MIN_EVEN",
    "FRAGMENT_DBE_MIN_ODD",
    "FRAGMENT_SENIOR_MARGIN",
    "SENIOR_CAP_BASE",
)


def test_every_threshold_is_named_in_the_doc_with_its_value():
    """docs/ms2.md section 5 lists every constant, by name, with the value the
    code holds; the library sizes and the cache sizes too."""
    doc = DOC.read_text(encoding="utf-8")
    table = {
        m.group(1): m.group(2)
        for m in re.finditer(r"^\| `(\w+)` \| `([^`]+)`", doc, flags=re.MULTILINE)
    }
    for name in _CONSTANTS:
        value = getattr(R, name)
        assert name in table, name
        assert float(table[name].replace("_", "")) == value, (name, table[name], value)
    assert (
        int(table["_GRID_CACHE"])
        == R._GRID_CACHE
        == R._parent_grid.cache_info().maxsize
    )
    assert (
        int(table["_LOSS_CACHE"]) == R._LOSS_CACHE == R._decompose.cache_info().maxsize
    )
    assert f"| `CLOSED_SHELL_LOSSES` | {len(M.CLOSED_SHELL_LOSSES)} formulas |" in doc
    assert f"| `RADICAL_LOSSES` | {len(M.RADICAL_LOSSES)} formulas |" in doc
    for name in (
        "VALENCES",
        "H_OR_HALOGEN",
        "ELEMENTS",
        "SENIOR_CAP_PER_ATOM",
        "DEFAULT_TOLERANCE",
    ):
        assert f"| `{name}` |" in doc, name
    # section 6 prints both libraries, in priority order
    closed = doc.split("**Closed-shell** (`CLOSED_SHELL_LOSSES`):")[1].split(
        "**Radical**"
    )[0]
    radical = doc.split("**Radical** (`RADICAL_LOSSES`):")[1].split("##")[0]
    for lib, text in ((M.CLOSED_SHELL_LOSSES, closed), (M.RADICAL_LOSSES, radical)):
        found = re.findall(
            r"(?<![\w\[\]])((?:\[15N\]|[A-Z][a-z]?)(?:\d+|\[15N\]|[A-Z][a-z]?)*)", text
        )
        assert [f for f in found if f in lib] == list(lib)
