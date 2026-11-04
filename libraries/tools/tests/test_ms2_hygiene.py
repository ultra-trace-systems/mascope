"""MS2 peak hygiene, `mascope_tools.composition.ms2.hygiene`: harmonic ghosts and
side lobes flagged, and a window read with each of them one row with no formula.

Ported from peaky (https://github.com/ultra-trace-systems/peaky, main at
aa119ac): the harmonic-ghost tests of `tests/test_ms2_replicates.py` (peaky PR
82, `readings.harmonic_ghosts` / `harmonic_ions`) and the side-lobe test of
`tests/test_ms2_breakdown.py` (peaky PR 80, `attribute.satellite_fragments`).
Each test names the peaky test it ports; the numbers are peaky's.

Measured cases are peaky's public fixtures, re-read from
`fixtures/ms2/peaky_hygiene.json`, which `fixtures/ms2/make_peaky_hygiene.py`
writes with peaky's own code: every window of its three fixtures, and the
exploratory file's 179.1069 window in the server's S/N-3 spectra, whose
precursor carries its 2f ghost at four energies. The rest is synthetic
exact-mass chemistry.
"""

import dataclasses
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from mascope_tools.composition import ms2 as M
from mascope_tools.composition.ms2 import hygiene as H


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ms2"
DOC = Path(__file__).resolve().parents[1] / "docs" / "ms2.md"
NAN = float("nan")
_GOLDEN = json.loads((FIXTURES / "peaky_hygiene.json").read_text(encoding="utf-8"))
#: the fields of a flagged row that are an unexplained row's
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


def _mz(ion: str) -> float:
    return M.ion_mz(ion)


def _peaks(peaks: dict, *, resolution=None, sn=None) -> dict:
    """{ion formula or m/z: intensity} -> the peak columns read_window takes;
    every peak reports `resolution` when given, and an S/N of its intensity /
    `sn` when given."""
    mz = [_mz(k) if isinstance(k, str) else float(k) for k in peaks]
    out = {"mz": mz, "intensity": [float(v) for v in peaks.values()]}
    if resolution is not None:
        out["resolution"] = [float(resolution)] * len(mz)
    if sn is not None:
        out["signal_to_noise"] = [float(v) / sn for v in peaks.values()]
    return out


def _window(parent: str, steps, **kw) -> dict:
    """One window's spectra, keyed as Mascope keys a group, from (nce, {ion or
    m/z: intensity}) steps."""
    p = round(_mz(parent), 4)
    return {f"{p:.4f}@hcd{nce:.2f}": _peaks(peaks, **kw) for nce, peaks in steps}


def _one(mz, intensity, **cols) -> dict:
    """One spectrum, keyed 's'."""
    return {"s": {"mz": list(mz), "intensity": list(intensity), **cols}}


def _ghosts(mz, intensity, **cols) -> dict[int, str]:
    """{peak: note} of flag_harmonics on one spectrum."""
    flags = M.flag_harmonics(_one(mz, intensity, **cols))["s"]
    return {p: f.note for p, f in enumerate(flags) if f is not None}


def _at(rows, mz, spectrum=None):
    """The rows of the peak at `mz` (to 1e-6), in one spectrum or all."""
    return [
        r
        for r in rows
        if math.isclose(r.mz, mz, rel_tol=0, abs_tol=1e-6)
        and (spectrum is None or r.spectrum == spectrum)
    ]


def _roles(rows) -> dict[tuple[str, int], str]:
    """A peak's role as peaky's `attribute.peak_roles` names it: a flagged
    peak's own, else precursor when a window ion reads it as its precursor,
    fragment when one reads it at all, else unexplained."""
    out: dict[tuple[str, int], str] = {}
    for r in rows:
        key, cur = (r.spectrum, r.peak), out.get((r.spectrum, r.peak))
        if r.role in M.FLAG_ROLES or r.role == "precursor":
            out[key] = r.role
        elif cur is None or (cur == "unexplained" and r.role == "fragment"):
            out[key] = r.role
    return out


# ------------------------------------------------------- harmonic ghosts ----
P, F = 460.2500, 300.1000


def test_harmonic_ghosts_finds_a_lines_2f_and_3f_images():
    """2f at m/z / 4 within 40 ppm and <= 0.5 % of the line; 3f at m/z / 9 and
    <= 8 %. Above the cap a peak is an ion; beyond the band, another peak; of
    two in one band the brighter is the ghost; a fragment line has its ghosts
    too. peaky: test_harmonic_ghosts_finds_a_lines_2f_and_3f_images."""
    mz = [
        P / 9 * (1 + 15e-6),
        P / 4 * (1 + 12e-6),
        P / 4 * (1 - 45e-6),
        F / 4 * (1 + 5e-6),
        F / 4 * (1 + 20e-6),
        F,
        P,
    ]
    inten = [2.5e3, 100.0, 120.0, 30.0, 10.0, 5e4, 1e5]
    g = _ghosts(mz, inten)
    assert sorted(g) == [0, 1, 3]
    assert g[0] == "3f ghost of the line at 460.2500 (m/z / 9): +15.0 ppm, 2.5 % of it"
    assert g[1] == "2f ghost of the line at 460.2500 (m/z / 4): +12.0 ppm, 0.1 % of it"
    assert g[3].startswith("2f ghost of the line at 300.1000")  # the brighter of two
    flags = M.flag_harmonics(_one(mz, inten))["s"]
    assert len(flags) == len(mz)
    f = flags[1]
    assert (f.role, f.line, f.line_mz, f.order) == ("harmonic", 6, P, 2)
    assert (f.mz, f.share) == (mz[1], 100.0 / 1e5)
    assert f.offset == pytest.approx(12.0, abs=1e-6)
    # above the cap: an ion
    assert _ghosts([P / 4, P], [600.0, 1e5]) == {}
    assert _ghosts([P / 9, P], [9e3, 1e5]) == {}
    assert _ghosts([P / 9, P], [7.9e3, 1e5]) != {}
    # the band's edges, and nothing to test
    assert _ghosts([P / 4 * (1 + 39.9e-6), P], [10.0, 1e5]) != {}
    assert _ghosts([P / 4 * (1 + 40.1e-6), P], [10.0, 1e5]) == {}
    assert _ghosts([P / 4 * (1 - 39.9e-6), P], [10.0, 1e5]) != {}
    assert _ghosts([P / 4 * (1 - 40.1e-6), P], [10.0, 1e5]) == {}
    assert _ghosts([], []) == {} and _ghosts([P], [1.0]) == {}
    assert _ghosts([P / 4, P], [10.0, NAN]) == {}
    assert _ghosts([P / 4, P], [10.0, 0.0]) == {}
    # 4f is not read (never seen)
    assert _ghosts([P / 16, P], [10.0, 1e5]) == {}
    # a peak that is the ghost of two lines (m/z / 4 of one, m/z / 9 of the
    # other) is named after the brighter
    assert _ghosts([25.0, 100.0, 225.0], [10.0, 1e4, 1e5])[0].startswith(
        "3f ghost of the line at 225.0000"
    )
    assert _ghosts([25.0, 100.0, 225.0], [10.0, 1e5, 1e4])[0].startswith(
        "2f ghost of the line at 100.0000"
    )


def test_the_band_edges_and_the_brightest_decide_the_ghost():
    """Of two candidates in a band the brighter is the ghost wherever it sits;
    of two lines equally bright, the first order names it; a peak just outside
    one line's band is the ghost of the line whose band holds it; a peak under
    the cap but just outside the band, at another energy, makes the place an
    ion's on either side; and a peak of no intensity is neither a ghost nor an
    ion's place."""
    g = _ghosts([F / 4 * (1 + 20e-6), F / 4 * (1 + 5e-6), F], [10.0, 30.0, 5e4])
    assert list(g) == [1]
    assert _ghosts([25.0, 100.0, 225.0], [10.0, 1e5, 1e5])[0].startswith(
        "2f ghost of the line at 100.0000"
    )
    edge = _ghosts([25.0, 100.0, 225.0 * (1 + 40.2e-6)], [10.0, 1e4, 1e5])
    assert edge[0].startswith("2f ghost of the line at 100.0000"), edge
    for side in (1, -1):
        ghost = {"mz": [P / 4 * (1 + side * 39.6e-6), P], "intensity": [300.0, 1e5]}
        out = {"mz": [P / 4 * (1 + side * 40.4e-6), P], "intensity": [300.0, 1e5]}
        assert M.flag_harmonics({"a": ghost})["a"][0] is not None
        flags = M.flag_harmonics({"a": ghost, "b": out})
        assert flags == {"a": (None, None), "b": (None, None)}, side
    assert _ghosts([P / 4, P], [0.0, 1e5]) == {}
    zero = {"mz": [P / 4], "intensity": [0.0]}
    flags = M.flag_harmonics(
        {"a": {"mz": [P / 4, P], "intensity": [10.0, 1e5]}, "b": zero}
    )
    assert flags["a"][0] is not None


def test_a_place_another_spectrum_reads_as_an_ion_holds_no_ghost():
    """Of two peaks in one band, the brighter is the ghost - unless another
    spectrum of the window reads it as an ion (here: above the cap of every line
    whose band holds it); then the other is. peaky: the `ions_at` case of
    test_harmonic_ghosts_finds_a_lines_2f_and_3f_images."""
    U, G, L = 61.0396, 549.3647 / 9 * (1 + 31e-6), 549.3647
    alone = M.flag_harmonics({"a": {"mz": [U, G, L], "intensity": [5e3, 2.2e3, 1e5]}})
    assert [p for p, f in enumerate(alone["a"]) if f] == [0]
    both = M.flag_harmonics(
        {
            "a": {"mz": [U, G, L], "intensity": [5e3, 2.2e3, 1e5]},
            "b": {"mz": [U, L], "intensity": [6e4, 1e5]},
        }
    )
    assert [p for p, f in enumerate(both["a"]) if f] == [1]
    assert both["b"] == (None, None)


#: A synthetic [M+NH4]+ whose 2f ghost (m/z / 4, +34 ppm: inside the band)
#: lands 4.5 ppm from C4H7O+, a sub-formula it can make.
GHOST_ION = "C15H26NO4+"


def _ghost_steps(real=0.0):
    g = _mz(GHOST_ION) / 4 * (1 + 34e-6)
    out = []
    for nce, i, a, b in ((1, 1e5, 0.0, 0.0), (10, 7e4, 3e4, 0.0), (20, 3e4, 6e4, 1e4)):
        peaks = {GHOST_ION: i, g: 0.003 * i}
        if a:
            peaks["C15H22O4+"] = a
        if b:
            peaks["C11H15O+"] = b
        if real:
            peaks[g] = real * i
        out.append((nce, peaks))
    return g, out


def test_a_ghost_on_a_formula_is_never_read():
    """Read as a peak, the ghost is C4H7O+ (-4.5 ppm) at every energy. Read as a
    ghost: ONE row with no formula, its role 'harmonic', its note naming the
    line, out of the TIC and the peak count and counted apart. The same peak at
    2 % of the line is above the cap: an ion, read. peaky:
    test_a_ghost_on_a_formula_is_never_read."""
    g, steps = _ghost_steps()
    spectra = _window(GHOST_ION, steps)
    on = M.read_window(spectra, [GHOST_ION])
    off = M.read_window(spectra, [GHOST_ION], keep=True)
    assert {r.formula for r in _at(off, g)} == {"C4H7O+"}
    rd = _at(on, g)
    assert len(rd) == 3 and {r.spectrum for r in rd} == set(spectra)
    for r in rd:
        assert {f: getattr(r, f) for f in UNEXPLAINED} == UNEXPLAINED
        assert math.isnan(r.dbe) and math.isnan(r.ppm)
        assert r.role == "harmonic"
        assert r.flag_note == (
            "2f ghost of the line at 284.1856 (m/z / 4): +34.0 ppm, 0.3 % of it"
        )
    assert "C4H7O+" not in {r.formula for r in on}
    # every other row as read_fragments writes it
    assert [r for r in on if r.role != "harmonic"] == [
        r for r in off if not math.isclose(r.mz, g, abs_tol=1e-6)
    ]
    counts, before = M.spectrum_counts(on), M.spectrum_counts(off)
    assert [c.spectrum for c in counts] == list(spectra)
    for c, b, (_, peaks) in zip(counts, before, steps):
        assert (c.n_harmonic, c.n_peaks) == (1, b.n_peaks - 1)
        assert c.harmonic_intensity == pytest.approx(0.003 * peaks[GHOST_ION])
        assert c.tic == pytest.approx(b.tic - c.harmonic_intensity)
        assert (c.n_satellite, c.satellite_intensity, b.n_harmonic) == (0, 0.0, 0)
    # above the cap it is an ion
    g, steps = _ghost_steps(real=0.02)
    rd = M.read_window(_window(GHOST_ION, steps), [GHOST_ION])
    assert {r.formula for r in _at(rd, g)} == {"C4H7O+"}
    assert all(r.role != "harmonic" for r in rd)


def test_a_ghost_two_window_ions_read_is_one_ghost_peak():
    """The ghost read by two window ions (the target and a co-isolate both read
    it as C4H7O+: two reading rows) becomes ONE row per spectrum, and counts as
    one peak. peaky: test_a_ghost_two_window_ions_read_is_one_ghost_peak."""
    g, steps = _ghost_steps()
    co = "C16H30NO3+"
    for _, peaks in steps:
        peaks[co] = 5e3
    spectra = _window(GHOST_ION, steps)
    on = M.read_window(spectra, [GHOST_ION, co])
    off = M.read_window(spectra, [GHOST_ION, co], keep=True)
    was = _at(off, g)
    assert len(was) == 6 and {r.formula for r in was} == {"C4H7O+"}
    assert {r.n_parents for r in was} == {2}
    rd = _at(on, g)
    assert len(rd) == 3 and {r.role for r in rd} == {"harmonic"}
    assert [c.n_harmonic for c in M.spectrum_counts(on)] == [1, 1, 1]


#: A synthetic urea adduct [M + urea + H]+ (549.3647) whose main product,
#: protonated urea CH5N2O+ (61.0396), lies 14.4 ppm below m/z / 9 - inside the
#: 3f band; [M + H]+ the other product (urea lost).
UREA_ADDUCT, UREA_H, UREA_LOSS = "C29H49N4O6+", "CH5N2O+", "C28H45N2O5+"


def _urea_steps(ghost):
    """The product holds 1 / 4 / 40 / 225 / 1500 % of the precursor at NCE 1 /
    3 / 6 / 10 / 15: under the 3f cap at the two lowest. `ghost`: the
    precursor's own 3f ghost too, +31 ppm of m/z / 9 at 2.2 % of it."""
    p = _mz(UREA_ADDUCT)
    out = []
    for nce, ip, iu, im in (
        (1, 1e5, 1.0e3, 5e2),
        (3, 9e4, 3.6e3, 1.8e3),
        (6, 6e4, 2.4e4, 1.2e4),
        (10, 2e4, 4.5e4, 2.2e4),
        (15, 4e3, 6e4, 3e4),
    ):
        peaks = {UREA_ADDUCT: ip, UREA_H: iu, UREA_LOSS: im}
        if ghost:
            peaks[p / 9 * (1 + 31e-6)] = 0.022 * ip
        out.append((nce, peaks))
    return out


def _read_at_every_energy(spectra, ion, frag, n, **kw):
    """`frag` is read (rank 1, a fragment of `ion`) in each of the `n` spectra
    that hold it, flagged in none, exactly as with every peak kept; and no
    peak of the window is a ghost."""
    on = M.read_window(spectra, [ion], **kw)
    off = M.read_window(spectra, [ion], keep=True, **kw)
    best = [r for r in on if r.formula == frag and r.rank == 1]
    assert len(best) == n and {r.role for r in best} == {"fragment"}
    assert all(r.role != "harmonic" for r in on)
    assert on == off


@pytest.mark.parametrize("ghost", [False, True])
def test_a_product_in_a_harmonic_band_is_read_at_every_energy(ghost):
    """A product the window reads above the cap at some energy is an ion at that
    place at every energy: never half erased where it is still weak. Beside it,
    the precursor's true 3f ghost is the ghost at every energy (not the product
    where it is the brighter of the two). peaky:
    test_a_product_in_a_harmonic_band_is_read_at_every_energy."""
    spectra = _window(UREA_ADDUCT, _urea_steps(ghost))
    rd = M.read_window(spectra, [UREA_ADDUCT])
    prod = _at(rd, _mz(UREA_H))
    assert len({r.spectrum for r in prod}) == 5
    assert all(r.role != "harmonic" for r in prod)
    assert {r.formula for r in prod if r.rank == 1} == {UREA_H}
    g = [r for r in rd if r.role == "harmonic"]
    if not ghost:
        assert not g
        assert rd == M.read_window(spectra, [UREA_ADDUCT], keep=True)
        return
    assert [r.spectrum for r in g] == list(spectra)
    assert np.allclose([r.mz for r in g], _mz(UREA_ADDUCT) / 9 * (1 + 31e-6))
    assert {r.flag_note for r in g} == {
        "3f ghost of the line at 549.3647 (m/z / 9): +31.0 ppm, 2.2 % of it"
    }


@pytest.mark.parametrize("loss", [True, False])
def test_a_product_whose_line_is_gone_is_read_at_every_energy(loss):
    """A fragile urea adduct: at NCE 1 and 3 its protonated urea holds 1 % and
    4 % of it (under the 3f cap, 14.4 ppm below m/z / 9), and from NCE 6 on the
    adduct is gone. There no line's band holds the product: its place is an
    ion's, and it is read at every energy, as without the rule. Without the
    [M+H]+ product the spectra from NCE 6 on hold the protonated urea alone.
    peaky: test_a_product_whose_line_is_gone_is_read_at_every_energy."""
    steps = []
    for nce, ip, iu, im in (
        (1, 1e5, 1.0e3, 5e2),
        (3, 9e4, 3.6e3, 1.8e3),
        (6, 0.0, 6e4, 3e4),
        (10, 0.0, 6e4, 3e4),
        (15, 0.0, 5e4, 2.5e4),
    ):
        peaks = {UREA_H: iu}
        if ip:
            peaks[UREA_ADDUCT] = ip
        if loss or ip:
            peaks[UREA_LOSS] = im
        steps.append((nce, peaks))
    spectra = _window(UREA_ADDUCT, steps)
    # the first two energies alone hold a ghost there: the gone line is the rule
    first = dict(list(spectra.items())[:2])
    assert all(f[0] is not None for f in M.flag_harmonics(first).values())
    _read_at_every_energy(spectra, UREA_ADDUCT, UREA_H, 5)


def test_a_product_that_drifts_out_of_the_band_is_read_at_every_energy():
    """A product read 39.6 ppm above m/z / 4 of a line at NCE 1 (0.3 % of it: a
    2f ghost, alone), at 40.4 and 40.6 ppm where it holds 3.75 % and 50 % of the
    line (a ppm of jitter: just out of the band). Where no line's band holds
    it, it is no ghost: its place is an ion's at every energy - a spectrum of
    that one peak alone too. peaky:
    test_a_product_that_drifts_out_of_the_band_is_read_at_every_energy."""
    spectra = {
        "1": {"mz": [P / 4 * (1 + 39.6e-6), P], "intensity": [300.0, 1e5]},
        "2": {"mz": [P / 4 * (1 + 40.4e-6), P], "intensity": [3e3, 8e4]},
        "3": {"mz": [P / 4 * (1 + 40.6e-6), P], "intensity": [2e4, 4e4]},
    }
    assert M.flag_harmonics({"1": spectra["1"]})["1"][0] is not None
    assert all(f == (None, None) for f in M.flag_harmonics(spectra).values())
    lone = {
        "1": spectra["1"],
        "2": {"mz": [P / 4 * (1 + 39.8e-6)], "intensity": [2e4]},
    }
    assert M.flag_harmonics(lone)["1"] == (None, None)


def test_a_product_s_place_holds_across_its_jitter_and_just_above_the_cap():
    """(1) A product's m/z moves between energies: the urea product read 1.0 /
    -0.8 / 0.6 / -1.2 / 0.4 ppm off its formula at NCE 1-15 is one place, within
    the reading tolerance - read at every energy. (2) A product just above the
    3f cap at ONE energy (8.5 % of the line at NCE 6; 5 % and 7 % at NCE 3 and
    10) is an ion's place: read at all three. (3) At the cap a peak is under
    it; in the bands of two lines, above one line's cap and under the other's,
    it is under, and clears no place. peaky:
    test_a_product_s_place_holds_across_its_jitter_and_just_above_the_cap."""
    steps = []
    for (nce, peaks), ppm in zip(_urea_steps(False), (1.0, -0.8, 0.6, -1.2, 0.4)):
        steps.append(
            (
                nce,
                {
                    (_mz(k) * (1 + ppm * 1e-6) if k == UREA_H else k): v
                    for k, v in peaks.items()
                },
            )
        )
    spectra = _window(UREA_ADDUCT, steps)
    rd = M.read_window(spectra, [UREA_ADDUCT])
    assert all(r.role != "harmonic" for r in rd)
    assert rd == M.read_window(spectra, [UREA_ADDUCT], keep=True)
    assert len([r for r in rd if r.formula == UREA_H and r.rank == 1]) == 5
    steps = [
        (3, {UREA_ADDUCT: 1e5, UREA_H: 5e3, UREA_LOSS: 2e3}),
        (6, {UREA_ADDUCT: 8e4, UREA_H: 6.8e3, UREA_LOSS: 1e4}),
        (10, {UREA_ADDUCT: 6e4, UREA_H: 4.2e3, UREA_LOSS: 2e4}),
    ]
    _read_at_every_energy(_window(UREA_ADDUCT, steps), UREA_ADDUCT, UREA_H, 3)
    # without NCE 6 the product is under the cap at both energies: a ghost
    assert all(
        f[1] is not None
        for f in M.flag_harmonics(_window(UREA_ADDUCT, steps[::2])).values()
    )
    at = M.HARMONIC_CAP[3] * 1e5
    assert M.flag_harmonics(_one([P / 9, P], [at, 1e5]))["s"][0] is not None
    assert M.flag_harmonics(_one([P / 9, P], [at * 1.001, 1e5]))["s"][0] is None
    # a reading just above the cap makes the place an ion's in another spectrum
    clears = M.flag_harmonics(
        {
            "a": {"mz": [P / 9, P], "intensity": [at * 1.001, 1e5]},
            "b": {"mz": [P / 9, P], "intensity": [0.05e5, 1e5]},
        }
    )
    assert clears["b"] == (None, None)
    # m/z 25 in the 2f band of the line at 100 and the 3f band of 225: above
    # one's cap, under the other's (either way round) - under, no place, so
    # the third spectrum's ghost of 225 stays a ghost
    for i25, i100, i225 in ((600.0, 1e5, 1e4), (400.0, 1e5, 4e3)):
        flags = M.flag_harmonics(
            {
                "two": {"mz": [25.0, 100.0, 225.0], "intensity": [i25, i100, i225]},
                "c": {"mz": [25.0, 225.0], "intensity": [0.05 * 1e4, 1e4]},
            }
        )
        assert flags["c"][0] is not None, (i25, i100, i225)
        assert flags["two"][0] is not None


def _ghost_floor_steps():
    """GHOST_ION's 2f ghost holds 0.3 % of its line at NCE 1, 10 and 20; at NCE
    30 the line has fallen to 4e3 and the ghost (24 counts) is read only where
    the noise lifts it: 0.6 % of the line, above the 2f cap."""
    g = _mz(GHOST_ION) / 4 * (1 + 34e-6)
    out = []
    for nce, ip, a, b, share in (
        (1, 1e5, 0.0, 0.0, 0.003),
        (10, 7e4, 3e4, 0.0, 0.003),
        (20, 3e4, 6e4, 1e4, 0.003),
        (30, 4e3, 6e4, 3e4, 0.006),
    ):
        peaks = {GHOST_ION: ip, g: share * ip}
        if a:
            peaks["C15H22O4+"] = a
        if b:
            peaks["C11H15O+"] = b
        out.append((nce, peaks))
    return g, out


@pytest.mark.parametrize(
    "sn,ghost_at",
    [
        (7.0, [1, 10, 20]),  # the NCE-30 ghost at S/N 3.4
        (3.4, [1, 10, 20]),  # ... at 7.1
        (2.0, []),  # ... at 12: its place is an ion's
        (1.0, []),  # ... at 24
        (None, []),  # no S/N known
    ],
)
def test_a_ghost_read_near_the_floor_clears_no_place(sn, ghost_at):
    """A ghost's share of its line reads high where it is read near the S/N
    floor: at NCE 30 it reads 0.6 %, above the 2f cap. At an S/N under
    HARMONIC_CLEAR_SN (10) such a reading clears no place: the ghost stays a
    ghost at NCE 1, 10 and 20, and is read (C4H7O+) only at NCE 30, where it is
    above the cap itself. At S/N 12 or 24, or with no S/N known, its place is
    an ion's: read at every energy. peaky:
    test_a_ghost_read_near_the_floor_clears_no_place and
    test_the_s_n_a_reading_needs_to_clear_its_place_is_10."""
    g, steps = _ghost_floor_steps()
    rd = M.read_window(_window(GHOST_ION, steps, sn=sn), [GHOST_ION])
    at = _at(rd, g)
    nce = [float(r.spectrum.split("hcd")[1]) for r in at if r.role == "harmonic"]
    assert nce == ghost_at
    read = {r.spectrum for r in at if r.formula == "C4H7O+"}
    assert len(read) == 4 - len(ghost_at)


def test_the_s_n_a_reading_needs_to_clear_its_place():
    """The unit: a reading above the cap clears its place at an S/N of at least
    HARMONIC_CLEAR_SN, or with none known; a peak in no band clears its place
    whatever its S/N. peaky: test_a_ghost_read_near_the_floor_clears_no_place."""
    assert M.HARMONIC_CLEAR_SN == 10.0
    under = {"mz": [P / 4, P], "intensity": [10.0, 1e5]}  # a ghost, under the cap
    for sn, cleared in (
        (M.HARMONIC_CLEAR_SN, True),
        (M.HARMONIC_CLEAR_SN * 0.99, False),
        (NAN, True),
    ):
        flags = M.flag_harmonics(
            {
                "a": {
                    "mz": [P / 4, P],
                    "intensity": [24.0, 4e3],
                    "signal_to_noise": [sn, 500.0],
                },
                "b": under,
            }
        )
        assert (flags["b"][0] is None) == cleared, sn
    lone = M.flag_harmonics(
        {
            "a": {"mz": [P / 4], "intensity": [24.0], "signal_to_noise": [1.0]},
            "b": under,
        }
    )
    assert lone["b"] == (None, None)


def test_a_product_s_place_is_read_within_the_mda_floor_of_the_tolerance():
    """Below m/z 60 the reading tolerance is its 0.3 mDa floor, not 5 ppm: the
    place a product holds is matched as read_fragments reads it. C2H5O+
    (45.0335) lies 24 ppm below m/z / 4 of C11H18NO+ (180.1383); at NCE 1 it
    holds 0.3 % of the line (under the 2f cap), read 6 ppm high (0.27 mDa:
    inside the floor, outside 5 ppm), and from NCE 10 on it is above the cap at
    its formula's mass - one place, read at every energy. Read 8 ppm high
    (0.36 mDa) under a 0.4 mDa floor, one place too. peaky:
    test_a_product_s_place_is_read_within_the_mda_floor_of_the_tolerance."""
    p, q = "C11H18NO+", "C2H5O+"
    mp, mq = _mz(p), _mz(q)
    assert (mq - mp / 4) / (mp / 4) * 1e6 == pytest.approx(-24.0, abs=0.1)
    steps = [
        (1, {p: 1e5, mq * (1 + 6e-6): 300.0}),
        (10, {p: 7e4, q: 3e3}),
        (20, {p: 3e4, q: 6e3}),
    ]
    _read_at_every_energy(_window(p, steps), p, q, 3)
    # under a pure 5 ppm window the NCE-1 peak is another place: a ghost
    flags = M.flag_harmonics(_window(p, steps), tolerance=M.Tolerance(ppm=5.0, mda=0))
    assert next(iter(flags.values()))[1] is not None
    steps[0] = (1, {p: 1e5, mq * (1 + 8e-6): 300.0})
    assert mq * 8e-6 * 1e3 == pytest.approx(0.36, abs=0.005)
    tol = M.Tolerance(mda=0.4)
    _read_at_every_energy(_window(p, steps), p, q, 3, tolerance=tol)
    assert next(iter(M.flag_harmonics(_window(p, steps)).values()))[1] is not None


# --------------------------------------------------------- the 179 window ----
_X179 = next(
    w for w in _GOLDEN["windows"] if w["fixture"] == "ms2_nh4_exploratory_179_sn3"
)


def _spectra(window) -> dict:
    return {
        s["spectrum"]: {
            k: s[k] for k in ("mz", "intensity", "signal_to_noise", "resolution")
        }
        for s in window["spectra"]
    }


def _ions(window) -> list[str]:
    return [M.format_ion(*M.parse_ion(i)) for i in window["window_ions"]]


def test_our_own_2f_ghost_of_the_179_precursor():
    """Verify, on the ported peaky fixture: the precursor C11H15O2+ (179.1068)
    carries its 2f ghost at m/z 44.7766 at NCE 1, 5, 10 and 15 - -1.9 to -2.7
    ppm of m/z / 4, 0.0985, 0.0985, 0.0938 and 0.0814 % of the line - and the
    rule flags those four and nothing else. Nothing reads the ghost (no formula
    has its mass defect), so the readings are as before but for its four rows;
    out of the peak count, the spectra hold 25, 22, 28, 23, 19 and 19 peaks.
    peaky: test_our_own_2f_ghost_of_the_179_precursor."""
    spectra = _spectra(_X179)
    flags = M.flag_harmonics(spectra)
    found = [(k, p) for k, fl in flags.items() for p, f in enumerate(fl) if f]
    assert [k for k, _ in found] == [f"179.1069@hcd{e:.2f}" for e in (1, 5, 10, 15)]
    got = [flags[k][p] for k, p in found]
    assert np.allclose([f.mz for f in got], 44.7766, atol=1e-4)
    assert [round(f.offset, 1) for f in got] == [-1.9, -1.9, -2.0, -2.7]
    assert [float(f"{f.share * 100:.3g}") for f in got] == [
        0.0985,
        0.0985,
        0.0938,
        0.0814,
    ]
    assert {(f.order, round(f.line_mz, 3)) for f in got} == {(2, 179.107)}
    ions = _ions(_X179)
    on = M.read_window(spectra, ions, offset_ppm=_X179["offset_ppm"])
    off = M.read_window(spectra, ions, offset_ppm=_X179["offset_ppm"], keep=True)
    ghosts = [r for r in on if r.role == "harmonic"]
    assert len(ghosts) == 4
    was = [
        r for g in ghosts for r in off if (r.spectrum, r.peak) == (g.spectrum, g.peak)
    ]
    assert len(was) == 4 and {r.role for r in was} == {"unexplained"}
    flagged = {(r.spectrum, r.peak) for r in on if r.role in M.FLAG_ROLES}
    assert [r for r in on if (r.spectrum, r.peak) not in flagged] == [
        r for r in off if (r.spectrum, r.peak) not in flagged
    ]
    counts = M.spectrum_counts(on)
    assert [c.n_harmonic for c in counts] == [1, 1, 1, 1, 0, 0]
    assert [c.n_peaks + c.n_satellite for c in counts] == [25, 22, 28, 23, 19, 19]


@pytest.mark.parametrize(
    "window",
    _GOLDEN["windows"],
    ids=[f"{w['fixture']}-{w['parent_mz']}" for w in _GOLDEN["windows"]],
)
def test_peakys_hygiene_of_its_public_fixtures_is_reproduced(window):
    """Every window of peaky's public fixtures, read as peaky's `ms2 analyze`
    read it: every harmonic ghost peaky's rule finds, with peaky's own note,
    and no other; every peak's role peaky's `peak_roles` gives it, but for the
    side lobes, which peaky leaves unexplained - and no peak peaky reads as a
    fragment or a precursor is one (peaky: no side-lobe fragment in these
    files, test_our_own_files_hold_no_side_lobe_fragment)."""
    spectra = _spectra(window)
    harmonic = M.flag_harmonics(spectra)
    rows = M.read_window(spectra, _ions(window), offset_ppm=window["offset_ppm"])
    roles = _roles(rows)
    for s in window["spectra"]:
        key = s["spectrum"]
        got = {str(p): f.note for p, f in enumerate(harmonic[key]) if f}
        assert got == s["harmonic"], key
        assert s["satellite"] == []
        for p, want in enumerate(s["roles"]):
            mine = roles[(key, p)]
            assert mine == want or (want, mine) == ("unexplained", "satellite"), (
                key,
                p,
                want,
                mine,
            )


def test_the_side_lobes_of_peakys_fixtures():
    """What the side-lobe rule makes of peaky's public fixtures: 113 of the 424
    peaks peaky reads as unexplained sit within 5 line widths of a line at least
    50 times brighter in every spectrum that holds their place - 74 beside a
    precursor, 39 beside a fragment, a median 3.8 widths out and 0.4 % of the
    line - and are satellites here, out of the TIC. None is a fragment or a
    precursor peaky reads."""
    n_lobes, n_unexplained, beside = 0, 0, {"precursor": 0, "fragment": 0}
    offsets, shares = [], []
    for window in _GOLDEN["windows"]:
        flags = M.flag_satellites(_spectra(window))
        for s in window["spectra"]:
            n_unexplained += s["roles"].count("unexplained")
            for p, f in enumerate(flags[s["spectrum"]]):
                if f is None:
                    continue
                assert s["roles"][p] == "unexplained"
                assert f.role == "satellite" and f.order == 0
                assert f.share <= 1 / M.SATELLITE_RATIO
                assert abs(f.offset) <= M.SATELLITE_FWHM
                n_lobes += 1
                beside[s["roles"][f.line]] += 1
                offsets.append(abs(f.offset))
                shares.append(f.share)
    assert (n_lobes, n_unexplained, beside) == (
        113,
        424,
        {"precursor": 74, "fragment": 39},
    )
    assert np.median(offsets) == pytest.approx(3.84, abs=0.01)
    assert np.median(shares) == pytest.approx(0.0040, abs=0.0001)


# ------------------------------------------------------------- side lobes ----
#: peaky's organonitrate target (1000 counts at every energy: it never
#: fragments) and its co-isolate falling into C11H13O+.
ON, ON_CO = "C10H21N[15N]O5+", "C15H22O3+"
NCE = (1, 10, 20, 30)


def _lobe_steps(lobe_da=-0.0022, share=0.01):
    """C11H13O+'s line carries a weak centroid `lobe_da` beside it holding
    `share` of it - 2.2 mDa below, it reads as C7H14[15N]O3+, a sub-formula of
    the target only (+0.23 ppm, loss C3H7NO2)."""
    lobe = _mz("C11H13O+") + lobe_da
    steps = []
    for e, y in zip(NCE, (1000.0, 700.0, 400.0, 100.0)):
        pk = {ON: 1000.0, ON_CO: y}
        if y < 1000:
            pk["C11H13O+"] = (1 - share) * (1000 - y)
            pk[lobe] = share * (1000 - y)
        steps.append((e, pk))
    return lobe, steps


def test_a_side_lobe_of_a_brighter_line_is_not_a_fragment():
    """A weak centroid 2.2 mDa below a line 99x brighter (2.3 line widths at a
    resolving power of 170 000: where an Orbitrap's negative side lobe sits) is
    no ion. Read against the window ions it is C7H14[15N]O3+, a sub-formula of
    the target only; every peak of it sits within 5 widths of a peak >= 50x
    brighter, so each is a satellite: one row with no formula, out of the TIC.
    A fragment one of whose peaks clears the band or the ratio is kept; a
    window whose centroids report no resolution is not tested. peaky:
    test_a_side_lobe_of_a_brighter_line_is_not_a_fragment."""
    lobe, steps = _lobe_steps()
    spectra = _window(ON, steps, resolution=170000.0)
    on = M.read_window(spectra, [ON, ON_CO])
    off = M.read_window(spectra, [ON, ON_CO], keep=True)
    assert {r.formula for r in _at(off, lobe) if r.rank == 1} == {"C7H14[15N]O3+"}
    sat = _at(on, lobe)
    assert len(sat) == 3 and {r.role for r in sat} == {"satellite"}
    for r in sat:
        assert {f: getattr(r, f) for f in UNEXPLAINED} == UNEXPLAINED
        assert re.fullmatch(
            r"side lobe of the line at 161\.0961: -2\.32 line widths "
            r"\(-2\.20 mDa\), 1\.01 % of it",
            r.flag_note,
        ), r.flag_note
    assert "C7H14[15N]O3+" not in {r.formula for r in on}
    flags = [f for fl in M.flag_satellites(spectra).values() for f in fl if f]
    assert len(flags) == 3
    f = flags[0]
    assert (f.role, f.order, f.line_mz) == ("satellite", 0, _mz("C11H13O+"))
    assert f.offset == pytest.approx(-0.0022 / (_mz("C11H13O+") / 170000.0))
    assert f.share == pytest.approx(0.01 / 0.99)
    counts = M.spectrum_counts(on)
    assert [c.n_satellite for c in counts] == [0, 1, 1, 1]
    for c, b in zip(counts, M.spectrum_counts(off)):
        assert c.tic == pytest.approx(b.tic - c.satellite_intensity)
        assert c.n_peaks == b.n_peaks - c.n_satellite
    # no resolution reported: no width, no test - the old reading
    bare = _window(ON, steps)
    assert M.read_window(bare, [ON, ON_CO]) == M.read_window(
        bare, [ON, ON_CO], keep=True
    )
    # beyond 5 widths (a resolving power of 400 000: 5.5 widths), or one peak
    # only 40x weaker than its line: an ion, kept
    louder = [
        (
            e,
            {
                k: (v * 2.5 if isinstance(k, float) and e == 30 else v)
                for k, v in pk.items()
            },
        )
        for e, pk in steps
    ]
    for res, st in ((400000.0, steps), (170000.0, louder)):
        kept = M.read_window(_window(ON, st, resolution=res), [ON, ON_CO])
        assert all(r.role != "satellite" for r in kept), res
        assert "C7H14[15N]O3+" in {r.formula for r in kept}


def test_the_width_is_the_brighter_peaks():
    """By hand: the width is the BRIGHTER peak's m/z over its resolution; a
    peak with no such neighbour is no lobe. peaky: the helper case of
    test_a_side_lobe_of_a_brighter_line_is_not_a_fragment."""
    mz, inten = [100.0, 100.003, 100.5], [1000.0, 10.0, 5.0]

    def lobes(res):
        flags = M.flag_satellites(_one(mz, inten, resolution=res))["s"]
        return [p for p, f in enumerate(flags) if f]

    assert lobes([1e5, NAN, NAN]) == [1]  # 3 widths
    assert lobes([2e5, NAN, NAN]) == []  # 6 widths
    assert lobes([NAN, 1e5, 1e5]) == []  # no width for the bright one
    assert lobes([0.0, 1e5, 1e5]) == []
    assert lobes([1e5, 1e5, 1e5]) == [1]
    f = M.flag_satellites(_one(mz, inten, resolution=[1e5, NAN, NAN]))["s"][1]
    assert (f.line, f.line_mz, f.share) == (0, 100.0, 0.01)
    assert f.offset == pytest.approx(3.0)
    # the ratio's edge: 50x brighter is a lobe, 49.9x is not
    assert M.flag_satellites(
        _one([100.0, 100.003], [500.0, 10.0], resolution=[1e5, 1e5])
    )["s"][1]
    assert not M.flag_satellites(
        _one([100.0, 100.003], [499.0, 10.0], resolution=[1e5, 1e5])
    )["s"][1]
    # the band's edge: within 5 widths (both sides), not beyond
    for d, lobe in ((0.00499, True), (-0.00499, True), (0.00501, False)):
        flags = M.flag_satellites(
            _one([100.0, 100.0 + d], [1000.0, 10.0], resolution=[1e5, 1e5])
        )
        assert (flags["s"][1] is not None) == lobe, d
    # of two lines that make it a lobe, the brighter is named
    two = M.flag_satellites(
        _one([100.0, 100.002, 100.004], [600.0, 10.0, 900.0], resolution=[1e5] * 3)
    )
    assert two["s"][1].line == 2
    # a peak with no intensity is no lobe, and no ion's place either
    assert M.flag_satellites(_one(mz, [1000.0, NAN, 5.0], resolution=[1e5] * 3))[
        "s"
    ] == (None, None, None)
    for none in (NAN, 0.0):
        flags = M.flag_satellites(
            {
                "a": {"mz": mz[:2], "intensity": inten[:2], "resolution": [1e5] * 2},
                "b": {"mz": [mz[1]], "intensity": [none], "resolution": [1e5]},
            }
        )
        assert flags["a"][1] is not None and flags["b"] == (None,), none


def test_a_lobe_place_another_energy_reads_as_an_ion_is_kept():
    """Verify: a real fragment one of whose peaks clears the band is kept - at
    every energy, the ones where it sits as a lobe too; and a product weak
    beside a bright line at its first energies, whose line is gone at a later
    one, is never flagged. Each spectrum alone flags it."""
    lobe, steps = _lobe_steps()
    gone = [(e, dict(pk)) for e, pk in steps]
    del gone[-1][1]["C11H13O+"]  # at NCE 30 the line is gone, the product left
    spectra = _window(ON, gone, resolution=170000.0)
    rd = M.read_window(spectra, [ON, ON_CO])
    assert all(r.role != "satellite" for r in rd)
    assert len({r.spectrum for r in rd if r.formula == "C7H14[15N]O3+"}) == 3
    for key in list(spectra)[1:3]:
        alone = M.flag_satellites({key: spectra[key]})[key]
        assert sum(f is not None for f in alone) == 1
    # one ppm of jitter between energies is one place: still kept
    jitter = [
        (
            e,
            {
                (k * (1 + 1e-6 * e / 10) if isinstance(k, float) else k): v
                for k, v in pk.items()
            },
        )
        for e, pk in gone
    ]
    rd = M.read_window(_window(ON, jitter, resolution=170000.0), [ON, ON_CO])
    assert all(r.role != "satellite" for r in rd)
    # 6 ppm between energies is two places at 5 ppm, one at 8: the window's
    # tolerance is the place's
    far = [
        (
            e,
            {
                (k * (1 + 6e-6) if isinstance(k, float) and e == 30 else k): v
                for k, v in pk.items()
            },
        )
        for e, pk in gone
    ]
    spectra = _window(ON, far, resolution=170000.0)
    assert {r.role for r in M.read_window(spectra, [ON, ON_CO])} >= {"satellite"}
    wide = M.Tolerance(ppm=8.0)
    rd = M.read_window(spectra, [ON, ON_CO], tolerance=wide)
    assert all(r.role != "satellite" for r in rd)
    assert all(
        f is None
        for fl in M.flag_satellites(spectra, tolerance=wide).values()
        for f in fl
    )


def test_a_window_ions_precursor_is_never_a_side_lobe():
    """A co-isolate whose precursor sits 3.1 line widths beside a target 100x
    brighter (C3 for SH4, 3.4 mDa) is a lobe by its peaks alone; the window
    names it, so its precursor reading stands (peaky's satellite rule reads
    fragments only). The same peak unnamed is a satellite."""
    target, co = "C10H17O+", "C7H21OS+"
    t = _mz(target)
    assert (_mz(co) - t) / (t / 140000.0) == pytest.approx(3.08, abs=0.01)
    spectra = {
        "a": {
            "mz": [_mz(co), t],
            "intensity": [1000.0, 1e5],
            "resolution": [140000.0, 140000.0],
        }
    }
    assert M.flag_satellites(spectra)["a"][0] is not None
    named = M.read_window(spectra, [target, co])
    assert [r.role for r in named if r.peak == 0] == ["precursor"]
    assert named == M.read_window(spectra, [target, co], keep=True)
    unnamed = M.read_window(spectra, [target])
    assert [r.role for r in unnamed if r.peak == 0] == ["satellite"]


def test_a_ghost_comes_before_a_lobe():
    """A peak both rules flag is a harmonic ghost (peaky reads harmonics before
    side lobes)."""
    # the ghost 4 widths below a peak 120x brighter, which is above the 2f cap
    # (an ion) and more than the reading window away (its place is not the
    # ghost's)
    spectra = {
        "a": {
            "mz": [P / 4, P / 4 + 0.0015, P],
            "intensity": [50.0, 6000.0, 1e6],
            "resolution": [3e5, 3e5, 3e5],
        }
    }
    assert M.flag_harmonics(spectra)["a"][0] is not None
    assert M.flag_satellites(spectra)["a"][0] is not None
    rows = M.read_window(spectra, ["C30H60O5+"])
    assert [r.role for r in rows if r.peak == 0] == ["harmonic"]


def test_a_ghost_a_window_ion_reads_as_its_precursor_is_a_ghost():
    """Unlike a side lobe, a harmonic ghost is a ghost even where a window ion
    reads it as its precursor, as in peaky (no MS2 scan holds a line at four
    times its window's m/z, so this is a constructed case)."""
    ion = "C2H5O+"
    spectra = {"a": {"mz": [_mz(ion), 4 * _mz(ion)], "intensity": [30.0, 1e5]}}
    assert [r.role for r in M.read_window(spectra, [ion]) if r.peak == 0] == [
        "harmonic"
    ]
    kept = M.read_window(spectra, [ion], keep=True)
    assert [r.role for r in kept if r.peak == 0] == ["precursor"]


# ------------------------------------------------------- the ladder gate ----
HEX = "C6H16NO6+"
CHAIN = ("C6H16NO6+", "C6H13O6+", "C6H11O5+", "C6H9O4+", "C6H7O3+")


def test_a_flagged_peak_is_no_ladder_step():
    """The hexose [M+NH4]+ chain's fourth step, C6H7O3+ (four pieces), is read
    on the ladder when its third, C6H9O4+, is read at another energy. When
    that peak is a side lobe of a line 60x brighter 2.9 widths away, it is no
    reading and no step: the fourth step is dropped. Kept, it reads as
    before."""
    step = _mz(CHAIN[3])
    line = step + 0.0025
    spectra = {
        "198.0972@hcd1.00": {
            "mz": [_mz(HEX), step, line],
            "intensity": [1e5, 100.0, 6000.0],
            "resolution": [170000.0] * 3,
        },
        "198.0972@hcd10.00": {
            "mz": [_mz(HEX), _mz(CHAIN[4])],
            "intensity": [1e5, 500.0],
            "resolution": [170000.0] * 2,
        },
    }
    kept = M.ladder_gate(M.read_window(spectra, [HEX], k_max=M.LADDER_K, keep=True))
    four = [r for r in kept if r.formula == CHAIN[4]]
    assert [(r.loss_pieces, r.ladder_support) for r in four] == [(4, "H7NO2")]
    rows = M.read_window(spectra, [HEX], k_max=M.LADDER_K)
    assert [r.role for r in rows if r.mz == step] == ["satellite"]
    gated = M.ladder_gate(rows)
    assert CHAIN[4] not in {r.formula for r in gated}
    assert [r.role for r in gated if r.mz == step] == ["satellite"]
    assert [r.role for r in gated if r.mz == _mz(CHAIN[4])] == ["unexplained"]


# --------------------------------------------------------- read_window ----
def test_keep_reads_every_peak_as_read_fragments_does():
    """keep=True is read_fragments over each spectrum, under its key, with the
    same arguments - peaky's --keep-harmonics; and read_window passes every
    argument on."""
    g, steps = _ghost_steps()
    spectra = _window(GHOST_ION, steps)
    kw = {
        "k_max": 2,
        "tolerance": M.Tolerance(ppm=3.0, mda=0.2),
        "offset_ppm": 1.5,
        "allow_radical": False,
    }
    want = tuple(
        r
        for key, peaks in spectra.items()
        for r in M.read_fragments(peaks, [GHOST_ION], spectrum=key, **kw)
    )
    assert M.read_window(spectra, [GHOST_ION], keep=True, **kw) == want
    on = M.read_window(spectra, [GHOST_ION], **kw)
    assert [r for r in on if r.role != "harmonic"] == [
        r for r in want if not math.isclose(r.mz, g, abs_tol=1e-6)
    ]
    assert M.read_window({}, [GHOST_ION]) == ()
    # a frame of peaks reads as its columns
    frames = {k: pd.DataFrame(v) for k, v in spectra.items()}
    assert M.read_window(frames, [GHOST_ION]) == M.read_window(spectra, [GHOST_ION])


def test_the_flagged_row_takes_its_first_reading_s_place():
    """Whole peaks only: the ghost's rows are replaced by one row where its
    first row was, every other peak's rows (their ranks and n_parents) as they
    were, the peaks in input order."""
    g, steps = _ghost_steps()
    nce, peaks = steps[1]
    order = {g: peaks[g], **{k: v for k, v in peaks.items() if k != g}}
    spectra = {"s": _peaks(order)}
    on = M.read_window(spectra, [GHOST_ION, "C16H30NO3+"])
    off = M.read_window(spectra, [GHOST_ION, "C16H30NO3+"], keep=True)
    assert on[0].role == "harmonic" and on[0].peak == 0
    assert on[1:] == tuple(r for r in off if r.peak != 0)
    assert [r.peak for r in on] == sorted(r.peak for r in on)


def test_counts_count_each_peak_once():
    """spectrum_counts: each (spectrum, peak) once, by its first row; a peak with
    no intensity adds 0; spectra in the order they first appear."""
    rows = M.read_fragments(
        {"mz": [_mz("C6H9+"), 250.0, _mz("C10H17+")], "intensity": [1.0, NAN, 3.0]},
        {"a": "C10H17+", "b": "C10H20[15N]+"},
        spectrum="x",
    ) + M.read_fragments([_mz("C10H17+")], "C10H17+", spectrum="w")
    assert M.spectrum_counts(rows) == (
        M.SpectrumCounts("x", 4.0, 3, 0, 0.0, 0, 0.0),
        M.SpectrumCounts("w", 0.0, 1, 0, 0.0, 0, 0.0),
    )
    assert M.spectrum_counts(()) == ()


# ------------------------------------------------------- rows and frames ----
def test_a_flagged_row_survives_the_frame():
    """The roles and notes are frame columns; rebuilt from the frame, the rows
    are the same and the gate reads them alike."""
    lobe, steps = _lobe_steps()
    rows = M.read_window(_window(ON, steps, resolution=170000.0), [ON, ON_CO])
    frame = M.readings_frame(rows)
    assert set(frame["role"]) == {"precursor", "fragment", "satellite"}
    assert (frame.loc[frame["role"] != "satellite", "flag_note"] == "").all()
    rebuilt = tuple(M.Reading(**rec) for rec in frame.to_dict("records"))
    pd.testing.assert_frame_equal(M.readings_frame(rebuilt), frame)
    assert [r.flag_note for r in rebuilt] == [r.flag_note for r in rows]
    assert [r.role for r in M.ladder_gate(rebuilt)] == [
        r.role for r in M.ladder_gate(rows)
    ]


def test_a_reading_s_role_follows_its_rank():
    """The read roles follow rank and is_precursor, whatever was given (so a
    replace keeps them true); a flag role is only a row of rank 0; a flag note
    only a flagged row's."""
    rows = M.read_fragments(
        [_mz("C10H17+"), _mz("C6H9+"), 250.0], "C10H17+", spectrum="s"
    )
    assert [r.role for r in M.best_readings(rows)] == [
        "precursor",
        "fragment",
        "unexplained",
    ]
    pre = rows[0]
    assert dataclasses.replace(pre, is_precursor=False).role == "fragment"
    assert dataclasses.replace(pre, role="unexplained").role == "precursor"
    un = rows[-1]
    sat = dataclasses.replace(un, role="satellite", flag_note="a lobe")
    assert (sat.role, sat.flag_note) == ("satellite", "a lobe")
    assert dataclasses.replace(sat, flag_note="").role == "satellite"
    with pytest.raises(ValueError, match="rank 0"):
        dataclasses.replace(sat, rank=1)
    with pytest.raises(ValueError, match="role must be one of"):
        dataclasses.replace(un, role="ghost")
    with pytest.raises(ValueError, match="flag_note"):
        dataclasses.replace(un, flag_note="a lobe")


# ---------------------------------------------------------- refusals ----
@pytest.mark.parametrize(
    "call",
    [
        lambda: M.flag_harmonics([{"mz": [1.0], "intensity": [1.0]}]),
        lambda: M.flag_satellites({1: {"mz": [1.0], "intensity": [1.0]}}),
        lambda: M.flag_harmonics({"s": {"mz": [1.0, 2.0], "intensity": [1.0]}}),
        lambda: M.flag_harmonics(
            {"s": {"mz": [1.0], "intensity": [1.0], "signal_to_noise": [1.0, 2.0]}}
        ),
        lambda: M.flag_satellites(
            {"s": {"mz": [1.0], "intensity": [1.0], "resolution": [1.0, 2.0]}}
        ),
        lambda: M.flag_harmonics({"s": {"mz": [-1.0], "intensity": [1.0]}}),
        lambda: M.flag_harmonics({"s": [[1.0, 2.0]]}),
        lambda: M.flag_harmonics({"s": [1.0]}, tolerance=(5.0, 0.3)),
        lambda: M.flag_satellites({"s": [1.0]}, tolerance=None),
        lambda: M.read_window({"s": [100.0]}, "C10H17+", keep="yes"),
        lambda: M.read_window({"s": [100.0]}, "C10H17"),
        lambda: M.read_window({}, "C10H17"),
        lambda: M.read_window([[100.0]], "C10H17+"),
        lambda: M.read_window({"s": [100.0]}, "C10H17+", tolerance=5.0),
    ],
)
def test_bad_inputs_are_refused(call):
    with pytest.raises(ValueError):
        call()


def test_peaks_without_intensity_are_never_flagged():
    """A spectrum of m/z only (as read_fragments takes one) is read, never
    flagged."""
    spectra = {"s": [P / 4, P]}
    assert M.flag_harmonics(spectra) == {"s": (None, None)}
    assert M.flag_satellites(spectra) == {"s": (None, None)}
    # NaN intensities: compared as text
    assert repr(M.read_window(spectra, ["C10H17+"])) == repr(
        M.read_window(spectra, ["C10H17+"], keep=True)
    )


# ------------------------------------------------------------ determinism ----
_SEED_SCRIPT = r"""
import dataclasses, json, sys
from mascope_tools.composition import ms2 as M
w = json.loads(open(sys.argv[1]).read())
for window in w["windows"]:
    spectra = {s["spectrum"]: {k: s[k] for k in ("mz", "intensity", "signal_to_noise",
               "resolution")} for s in window["spectra"]}
    ions = [M.format_ion(*M.parse_ion(i)) for i in window["window_ions"]]
    rows = M.read_window(spectra, ions, offset_ppm=window["offset_ppm"])
    print(json.dumps([dataclasses.astuple(r) for r in rows]))
    print(json.dumps([dataclasses.astuple(c) for c in M.spectrum_counts(rows)]))
"""


def test_the_same_input_gives_the_same_bytes_under_any_hash_seed(tmp_path):
    """Every golden window read with its flags, in a process of its own per hash
    seed: the same bytes."""
    outs = []
    for seed in ("0", "8"):
        p = subprocess.run(
            [sys.executable, "-c", _SEED_SCRIPT, str(FIXTURES / "peaky_hygiene.json")],
            cwd=tmp_path,
            env=dict(os.environ, PYTHONHASHSEED=seed),
            capture_output=True,
            timeout=300,
        )
        assert p.returncode == 0, p.stderr.decode()
        outs.append(p.stdout)
    assert outs[0] == outs[1]
    assert outs[0].count(b'"harmonic"') == 4 and b'"satellite"' in outs[0]


# ------------------------------------------------------ the core's version ----
#: What the core reads on peaky's public fixtures, by its version: a digest of
#: every window read as the readings route reads one (``read_window`` at
#: ``k_max`` ``LADDER_K``, then ``ladder_gate``) and of its counts. The readings
#: route caches under the version, so a change to what any reading, flag or
#: count says, read here, has to raise ``CORE_VERSION`` - with the digest it
#: then reads added here - or a cached reading of the old rules would be served
#: as the new rules' reading.
_READINGS_BY_VERSION = {
    1: "d1cd7fb8a3dc42b506d87fc9ae173dff8155929173079ffd1e23d17ba74a6fe7",
}


def _readings_digest() -> str:
    """Every golden window's readings and counts, as text, digested; the ppm
    to 1e-4, since a grid's masses are a matrix product whose last bits a
    machine's BLAS may round otherwise."""
    digest = hashlib.sha256()
    for window in _GOLDEN["windows"]:
        rows = M.ladder_gate(
            M.read_window(
                _spectra(window),
                _ions(window),
                k_max=M.LADDER_K,
                offset_ppm=window["offset_ppm"],
            )
        )
        for r in rows:
            fields = dataclasses.asdict(r)
            for name in ("mz", "mz_cal", "intensity"):
                fields.pop(name)
            fields["ppm"] = f"{r.ppm:.4f}"
            digest.update(repr(sorted(fields.items())).encode())
        for c in M.spectrum_counts(rows):
            digest.update(repr(dataclasses.astuple(c)).encode())
    return digest.hexdigest()


def test_the_cores_version_is_raised_with_what_it_reads():
    """CORE_VERSION names what the core reads: on peaky's public fixtures,
    the readings this version pins. A failure here is a change to what the
    core reads: raise CORE_VERSION, and add the digest this test reads now
    under it."""
    assert _READINGS_BY_VERSION.get(M.CORE_VERSION) == _readings_digest(), (
        f"the readings changed: raise CORE_VERSION ({M.CORE_VERSION}) and pin "
        f"{_readings_digest()} under it"
    )


# --------------------------------------------------------------- the doc ----
def test_every_hygiene_threshold_is_named_in_the_doc_with_its_value():
    """docs/ms2.md section 6 lists every constant of the rules, by name, with the
    value the code holds."""
    doc = DOC.read_text(encoding="utf-8")
    table = {
        m.group(1): m.group(2)
        for m in re.finditer(r"^\| `(\w+)` \| `([^`]+)`", doc, flags=re.MULTILINE)
    }
    for name in (
        "HARMONIC_PPM",
        "HARMONIC_CLEAR_SN",
        "SATELLITE_RATIO",
        "SATELLITE_FWHM",
    ):
        assert float(table[name]) == getattr(H, name), name
    assert table["HARMONIC_ORDERS"] == str(H.HARMONIC_ORDERS)
    assert table["HARMONIC_CAP"] == str(H.HARMONIC_CAP)
    for name in H.__all__:
        if name.isupper():
            assert f"`{name}`" in doc, name
    for role in M.ROLES:
        assert f"`'{role}'`" in doc, role
