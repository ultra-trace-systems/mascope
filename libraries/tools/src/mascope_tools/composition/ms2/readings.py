"""MS2 readings: which sub-formulas of a window ion fit a fragment peak, and
what the ion must have lost to make each of them.

A fragment of a singly charged precursor is an element-wise sub-formula of the
precursor ION (the charge stays on the fragment), and the difference is the
neutral the ion lost. Every peak of an MS2 spectrum is read against every ion
of its isolation window - the target and its co-isolates - with the same rules,
and a sub-formula is a reading only when its loss passes one test:

* DBE is affine in the element counts, so DBE(loss) = DBE(parent) -
  DBE(fragment) + 1. A loss between two ions of the same electron parity has an
  integer DBE (closed-shell); between ions of opposite parity a half-integer
  one (a radical).
* The loss must split into at most ``k_max`` molecules, not be ONE molecule:
  MS2 fragments are often second-generation. An ammonium adduct declusters and
  then dehydrates (loss H3N + H2O + H2O, DBE -2: no single molecule has it).
  k molecules each of DBE >= 0 sum to DBE(loss) + k - 1, so k >= 1 - DBE(loss)
  is a necessary bound that refuses the absurd ones (H8 needs four H2) before
  any search.
* The pieces come from an ordered library of common neutrals
  (:data:`CLOSED_SHELL_LOSSES`, :data:`RADICAL_LOSSES`) plus at most ONE
  unnamed remainder, which must be a plausible molecule on its own: integer
  DBE >= 0, Senior's second rule, and at least one H or halogen. The last
  clause keeps a bare-carbon "loss" out: C3H6NS+ fits C7H6NS+ minus C4 at
  -0.5 ppm in peaky's exploratory fixture, but the window also held C4H10NS2+,
  and that ion minus CH4S is the reading.

Loss names are formulas joined by ``' + '`` - never trivial names, never a
structure: ``C3H6O`` is a composition, whatever the reader thinks left.

Readings are ranked per (peak, parent) and never across parents: which window
ion made a fragment needs intensities across collision energies, which this
module does not see. :func:`read_fragments` reports, for every peak, every
parent that can explain it (``n_parents`` above 1 is composition-ambiguous).
:func:`ladder_gate` reads the readings of one window across its energies and
keeps a fourth loss piece only on a ladder.

The rules are peaky's (``peaky/ms2/subformula.py`` and ``readings.py``, peaky
PRs 78 and 80), ported with its known answers as tests, so the two engines read
one spectrum alike; ``libraries/tools/docs/ms2.md`` documents every rule and
threshold. Formulas are written in Mascope's notation (:func:`to_hill_order`,
15N as ``[15N]``); peaky's caret label ``^N`` is accepted on input and written
as ``[15N]``. A labelled atom is its own element, in masses and in counts: a
labelled-ammonium adduct loses H3[15N], 18.0236, which is neither H3N
(17.0265) nor H2O (18.0106).

Pure: numpy and Mascope's formula layer only, no I/O. :mod:`.frame` builds a
DataFrame from the readings.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import NamedTuple

import numpy as np
from pyteomics.mass import calculate_mass

from mascope_tools.composition.config import ELECTRON_MASS
from mascope_tools.composition.custom_elements import CUSTOM_ELEMENTS
from mascope_tools.composition.utils import to_hill_order, to_pyteomics


__all__ = [
    "CLOSED_SHELL_LOSSES",
    "DEFAULT_TOLERANCE",
    "ELEMENTS",
    "FRAGMENT_DBE_MIN_EVEN",
    "FRAGMENT_DBE_MIN_ODD",
    "FRAGMENT_SENIOR_MARGIN",
    "GRID_MAX_ROWS",
    "H_OR_HALOGEN",
    "K_MAX_DEFAULT",
    "K_MAX_LIMIT",
    "LADDER_K",
    "RADICAL_LOSSES",
    "READING_TOLERANCE_MDA",
    "READING_TOLERANCE_PPM",
    "SENIOR_CAP_BASE",
    "SENIOR_CAP_PER_ATOM",
    "VALENCES",
    "AnchorResidual",
    "FragmentCandidate",
    "LossDecomposition",
    "MassOffset",
    "Reading",
    "Tolerance",
    "apply_offset",
    "best_readings",
    "decompose_loss",
    "format_ion",
    "fragment_candidates",
    "fragment_ok",
    "ion_mz",
    "ion_parity",
    "ladder_gate",
    "mass_offset",
    "parse_ion",
    "read_fragments",
]

# ---------------------------------------------------------------------------
# Elements and valences
# ---------------------------------------------------------------------------
#: The element axis every count vector is written on, in peaky's order. The
#: order is part of the rules in one place: when several earlier steps of a
#: ladder are read and none is a printed piece, :func:`ladder_gate` names the
#: first of them in this axis's lexicographic order.
ELEMENTS: tuple[str, ...] = (
    "C",
    "H",
    "O",
    "N",
    "S",
    "P",
    "Si",
    "F",
    "Cl",
    "Br",
    "I",
    "[15N]",
)

#: Valences of the DBE and Senior accounting: C and Si 4; N, 15N and P 3; O and
#: S 2 (S is never hypervalent here: SO2 and SO3 are library pieces, so they
#: need no valence rule); H and the halogens 1. 2 x DBE = 2 + sum((v - 2) * n),
#: which for the elements it lists is ``config.UNSATURATION_COEFFICIENTS``.
VALENCES: dict[str, int] = {
    "C": 4,
    "H": 1,
    "O": 2,
    "N": 3,
    "S": 2,
    "P": 3,
    "Si": 4,
    "F": 1,
    "Cl": 1,
    "Br": 1,
    "I": 1,
    "[15N]": 3,
}

#: The elements an unnamed loss piece must hold one of: what keeps a bare
#: skeleton (C4, a lone C or O) from passing as a molecule.
H_OR_HALOGEN: tuple[str, ...] = ("H", "F", "Cl", "Br", "I")

#: Senior's cap on an ion's DBE, SENIOR_CAP_BASE + sum(weight * n): C and Si 1,
#: N and 15N 1/2. P adds to the DBE but not to the cap, as in peaky.
SENIOR_CAP_PER_ATOM: dict[str, float] = {"C": 1.0, "Si": 1.0, "N": 0.5, "[15N]": 0.5}
SENIOR_CAP_BASE = 1.0

#: The fragment gate (:func:`fragment_ok`): an even-electron fragment ion needs
#: DBE >= -0.5 (H3O+, NH4+ and every protonated saturated molecule sit there),
#: an odd-electron one DBE >= 0, and either DBE <= Senior's cap + 0.5, the
#: ion's half step above the neutral's cap.
FRAGMENT_DBE_MIN_EVEN = -0.5
FRAGMENT_DBE_MIN_ODD = 0.0
FRAGMENT_SENIOR_MARGIN = 0.5

_IDX = {e: i for i, e in enumerate(ELEMENTS)}
_VAL = np.array([VALENCES[e] for e in ELEMENTS], dtype=np.int64)
# 2 x DBE = 2 + sum(_VM2 * n): integer arithmetic for every parity test.
_VM2 = _VAL - 2
_HX = np.array([e in H_OR_HALOGEN for e in ELEMENTS])
_CAP2 = np.array(
    [round(2 * SENIOR_CAP_PER_ATOM.get(e, 0.0)) for e in ELEMENTS], dtype=np.int64
)
_CAP2_BASE = round(2 * SENIOR_CAP_BASE)
_T2_MIN_EVEN = round(2 * FRAGMENT_DBE_MIN_EVEN)
_T2_MIN_ODD = round(2 * FRAGMENT_DBE_MIN_ODD)
_T2_MARGIN = round(2 * FRAGMENT_SENIOR_MARGIN)
# Monoisotopic masses from Mascope's own source (pyteomics), 15N as N[15].
_MASS = {e: calculate_mass(formula=to_pyteomics(e)) for e in ELEMENTS}

# ---------------------------------------------------------------------------
# The loss library (Hill formulas, in PRIORITY order)
# ---------------------------------------------------------------------------
#: The closed-shell pieces a loss is named in, in priority order. The library
#: only NAMES pieces and orders ties: a loss that is not a library formula is
#: still read as one unnamed piece when it passes the remainder rules. Priority
#: decides between decompositions of equal size - H4O2 is ``H2O + H2O``, not
#: ``H2 + H2O2`` - and the order the pieces are written in. The first 37 are the
#: common small neutrals of MS2 interpretation; the next three are neutrals
#: peaky's channels lose often enough that a reading should not count them as
#: unnamed: ethanol (ethyl esters and ethers), H2SO4 (organosulfates) and
#: C2H6OSi (dimethylsilanone, the ring-contraction loss of cyclic
#: methylsiloxanes). The last three carry no H or halogen, so the remainder rule
#: would refuse them as unnamed pieces, and they are real losses: CS2 and COS
#: leave dithiocarbamates and thiocarbamates, N2O nitroso and N-nitro species.
CLOSED_SHELL_LOSSES: tuple[str, ...] = (
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

#: The radical pieces, in priority order: a radical loss carries exactly ONE of
#: these, or one unnamed radical remainder, next to closed-shell pieces.
RADICAL_LOSSES: tuple[str, ...] = (
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

#: The default largest number of molecules a loss may split into. Every extra
#: piece admits more losses, and so more chance compositions, at every peak.
K_MAX_DEFAULT = 3
#: ``k_max`` above this is refused. The search holds only the piece multisets
#: that fit inside the loss, prunes any whose rest needs more pieces than are
#: left and stops at the first size that reads, so depth costs nothing for a
#: real loss; the cap bounds a pathological one (random large halogenated
#: losses at 6 take a few ms, about 0.1 s at worst, once - results are cached).
#: 6 still reads the longest chain an ammonium adduct of a hexose walks: NH3 and
#: five waters, H13NO5, DBE -5.
K_MAX_LIMIT = 6
#: The piece count :func:`ladder_gate` admits on a ladder: one above the default.
LADDER_K = 4
#: Sub-formula grids above this many rows are refused rather than built (about
#: 30 MB of int16 counts before the gate): a C60H100O30N3S2 parent is 2.3M rows,
#: and anything beyond 5M is no CIMS precursor.
GRID_MAX_ROWS = 5_000_000
#: The relative half-width of the reading window, ppm of the peak's m/z.
READING_TOLERANCE_PPM = 5.0
#: The window's absolute floor, mDa. An Orbitrap's low-mass error is an
#: absolute offset: peaky's exploratory fixture read its C2H3O+ fragment at
#: -3.8 and -4.1 ppm (-0.16 and -0.18 mDa) after the precursor recalibration,
#: and 5 ppm at m/z 43 is 0.21 mDa, no margin. The floor makes it 7 ppm there
#: and is inert above m/z 60.
READING_TOLERANCE_MDA = 0.3
# Cache sizes: sub-formula grids per (composition, charge), loss decompositions
# per (loss, k_max).
_GRID_CACHE = 32
_LOSS_CACHE = 1 << 16
_ROWS_PER_BLOCK = 1 << 12  # frontier rows per (rows, library, E) int16 broadcast

# ---------------------------------------------------------------------------
# Formulas
# ---------------------------------------------------------------------------
# A token: a bracket isotope ('[15N]'), a caret label ('^N') or an element, with
# an optional count. The body of a formula is nothing but tokens.
_TOKEN = r"(?:\[\d+[A-Z][a-z]?\]|\^?[A-Z][a-z]?)"
_TOKEN_RE = re.compile(rf"({_TOKEN})(\d*)")
_BODY_RE = re.compile(rf"(?:{_TOKEN}\d*)+")
# body, ONE sign, an optional radical mark ('.', the bullet U+2022, the middle
# dot U+00B7)
_ION_RE = re.compile(rf"((?:{_TOKEN}\d*)+)([+-])([.\u2022\u00b7]?)")
#: Caret label -> the bracket isotope it stands for: '^N' -> '[15N]'.
_CARET = {
    sym: f"[{ce.labelled_massnumber}{ce.base_element}]"
    for sym, ce in CUSTOM_ELEMENTS.items()
}


def _symbol(token: str, what: str) -> str:
    """One element symbol on the axis, a caret label normalised."""
    symbol = _CARET.get(token, token)
    if symbol not in _IDX:
        raise ValueError(
            f"unknown element {token!r} in {what} (the elements read are "
            f"{', '.join(ELEMENTS)}; '^N' is read as '[15N]')"
        )
    return symbol


def _parse_body(body: str, what: str) -> dict[str, int]:
    """A formula body -> counts, strictly: the whole string must be element
    tokens, and an unknown symbol raises. :func:`parse_composition` skips what it
    cannot read ('C2H4o2' -> C2H4, 'H2O-H2' -> H4O), and a typo must not
    silently shrink a precursor or a loss."""
    if not _BODY_RE.fullmatch(body):
        raise ValueError(
            f"not a formula: {what} (expected element tokens such as 'C2H4O2' or "
            f"'H3[15N]')"
        )
    counts: dict[str, int] = {}
    for token, n in _TOKEN_RE.findall(body):
        symbol = _symbol(token, what)
        counts[symbol] = counts.get(symbol, 0) + (int(n) if n else 1)
    return {e: n for e, n in counts.items() if n}


def _counts(formula: Mapping[str, int], what: str) -> dict[str, int]:
    """A counts mapping on the axis: symbols normalised, no negative count."""
    out: dict[str, int] = {}
    for token, n in formula.items():
        if isinstance(n, bool) or not isinstance(n, (int, np.integer)):
            raise ValueError(f"count of {token!r} in {what} must be an integer")
        if n < 0:
            raise ValueError(f"negative count in {what}")
        symbol = _symbol(str(token), what)
        out[symbol] = out.get(symbol, 0) + int(n)
    return {e: n for e, n in out.items() if n}


def _vec(counts: Mapping[str, int]) -> np.ndarray:
    v = np.zeros(len(ELEMENTS), dtype=np.int64)
    for el, n in counts.items():
        v[_IDX[el]] = n
    return v


def _formula_of(vec: Sequence[int]) -> str:
    """A count vector as a Hill formula in Mascope's notation."""
    return to_hill_order({e: int(n) for e, n in zip(ELEMENTS, vec) if n})


def parse_ion(ion: str) -> tuple[dict[str, int], int]:
    """An ion formula -> its counts and charge: ``'C10H17O+'`` ->
    ``({'C': 10, 'H': 17, 'O': 1}, 1)``, ``'C4H10^NO4+'`` -> ``({'C': 4, 'H':
    10, '[15N]': 1, 'O': 4}, 1)``.

    Exactly one sign is required: a formula without one is a neutral passed by
    mistake, and ``2+`` or ``++`` would be a doubly charged ion, which nothing
    here models. A trailing radical mark (``'C7H8+.'``, a bullet or a middle dot)
    is accepted and checked: on an even-electron formula it is a typo and
    raises. Unknown element symbols raise; surrounding whitespace is ignored.

    :raises ValueError: For anything that is not one singly charged ion formula.
    """
    if not isinstance(ion, str):
        raise ValueError(f"ion formula must be a string, got {ion!r}")
    m = _ION_RE.fullmatch(ion.strip())
    if not m:
        raise ValueError(
            f"not a singly charged ion formula: {ion!r} (expected e.g. "
            f"'C10H17O+', 'C10H17O5-', 'C4H10[15N]O4+', 'C7H8+.')"
        )
    body, sign, mark = m.groups()
    counts = _parse_body(body, repr(ion))
    if not counts:
        raise ValueError(f"empty ion formula {ion!r}")
    if mark and ion_parity(counts) == "EE":
        raise ValueError(f"{ion!r} carries a radical mark but is even-electron")
    return counts, 1 if sign == "+" else -1


def format_ion(counts: Mapping[str, int], charge: int) -> str:
    """Counts and a charge -> the ion formula: ``({'C': 6, 'H': 9}, 1)`` ->
    ``'C6H9+'``. Hill order as :func:`to_hill_order`, no radical mark (the
    parity is a field of its own).

    :raises ValueError: For a charge other than +1 or -1, or no atoms.
    """
    if isinstance(charge, bool) or charge not in (1, -1):
        raise ValueError(f"charge must be +1 or -1, got {charge!r}")
    counts = _counts(counts, "ion")
    if not counts:
        raise ValueError("empty ion formula")
    return to_hill_order(counts) + ("+" if charge > 0 else "-")


def _ion_counts(ion: str | Mapping[str, int]) -> dict[str, int]:
    if isinstance(ion, Mapping):
        return _counts(ion, repr(dict(ion)))
    return parse_ion(ion)[0]


def ion_mz(ion: str) -> float:
    """The exact m/z of a singly charged ion formula: its atoms' monoisotopic
    masses minus (cation) or plus (anion) one electron."""
    counts, charge = parse_ion(ion)
    # summed in axis order, so two spellings of one ion give one float
    neutral = sum(_MASS[e] * counts[e] for e in ELEMENTS if e in counts)
    return neutral - charge * ELECTRON_MASS


def ion_parity(ion: str | Mapping[str, int]) -> str:
    """``'EE'`` (even-electron) or ``'OE'`` (odd-electron) of a singly charged ION.

    An ion is even-electron iff its formula's DBE is HALF-integer: C10H17O+ 2.5
    EE, C10H17O5- 2.5 EE, C7H8+. 4.0 OE. On a NEUTRAL formula the same
    half-integer DBE means a radical, the opposite reading.
    """
    t2 = 2 + int(_vec(_ion_counts(ion)) @ _VM2)
    return "EE" if t2 % 2 else "OE"


def _gate(sub: np.ndarray, col: Sequence[int]) -> tuple[np.ndarray, np.ndarray]:
    """THE fragment gate, vectorised: :func:`fragment_ok` and every parent's
    sub-formula grid both call it. ``sub`` holds ion counts, one row per
    formula, whose columns sit at the axis positions ``col``. -> (ok, 2 x DBE)
    per row. Column by column, so a 5M-row grid never needs an (n, E) int64
    copy."""
    n = len(sub)
    t2 = np.full(n, 2, dtype=np.int64)
    cap2 = np.full(n, _CAP2_BASE, dtype=np.int64)
    non_h = np.zeros(n, dtype=np.int64)
    for c, g in enumerate(col):
        x = sub[:, c].astype(np.int64)
        t2 += _VM2[g] * x
        cap2 += _CAP2[g] * x
        if g != _IDX["H"]:
            non_h += x
    floor = np.where(t2 % 2 == 1, _T2_MIN_EVEN, _T2_MIN_ODD)
    ok = (non_h > 0) & (t2 >= floor) & (t2 <= cap2 + _T2_MARGIN)
    return ok, t2


def fragment_ok(ion: str | Mapping[str, int]) -> bool:
    """The structural gate on a FRAGMENT ion formula: at least one non-H atom,
    DBE >= :data:`FRAGMENT_DBE_MIN_EVEN` when even-electron or
    :data:`FRAGMENT_DBE_MIN_ODD` when odd-electron, and DBE <= Senior's cap +
    :data:`FRAGMENT_SENIOR_MARGIN`.

    Fragments only: a PARENT is an input and is never gated. An ammonium
    cluster of a saturated molecule, C6H18[15N]O2+ (ion DBE -1.5), fails this
    test and is still a real window ion and its own precursor reading - do not
    pre-screen window ions with it.
    """
    v = _vec(_ion_counts(ion))
    return bool(_gate(v[None, :], range(len(ELEMENTS)))[0][0])


# ---------------------------------------------------------------------------
# Loss decomposition
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LossDecomposition:
    """How a neutral loss splits into molecules: ``pieces`` in library priority
    order, the unnamed remainder (if any) last; ``radical`` when the loss has a
    half-integer DBE (exactly one piece is then a radical)."""

    formula: str
    pieces: tuple[str, ...]
    unnamed: int
    radical: bool

    @property
    def k(self) -> int:
        return len(self.pieces)

    @property
    def name(self) -> str:
        return " + ".join(self.pieces)


def _check_k_max(k_max: int, lowest: int = 1) -> int:
    if (
        isinstance(k_max, bool)
        or not isinstance(k_max, (int, np.integer))
        or not lowest <= k_max <= K_MAX_LIMIT
    ):
        raise ValueError(
            f"k_max must be an integer in {lowest}..{K_MAX_LIMIT}, got {k_max!r}"
        )
    return int(k_max)


_N_CLOSED = len(CLOSED_SHELL_LOSSES)
_LIB_NAMES: tuple[str, ...] = CLOSED_SHELL_LOSSES + RADICAL_LOSSES
_LIB_VEC = np.vstack(
    [_vec(_parse_body(f, f"library piece {f!r}")) for f in _LIB_NAMES]
).astype(np.int16)
_LIB_INDEX = {tuple(int(n) for n in v): i for i, v in enumerate(_LIB_VEC)}


def _remainder_ok(rest: np.ndarray, radical: np.ndarray) -> np.ndarray:
    """Is each row of ``rest`` (counts, (m, E)) acceptable as the ONE unnamed piece?

    Closed-shell: integer DBE >= 0, Senior's second rule (sum of valences >= 2 x
    the largest valence present), and at least one H or halogen. Radical
    (``radical`` True for that row): half-integer DBE >= 1/2, sum of valences >=
    2 x largest - 1 (CH3: 7 >= 7, HO: 3 >= 3), and H or halogen. DBE >= 0 is
    Senior's third rule for these valences; the second rule refuses pieces like
    NH (4 < 6); the H-or-halogen clause refuses the bare skeletons the other two
    let through (C4, DBE 5; a lone C or O). CO, CO2, N2, O2, SO2, CS2, COS and
    N2O are named library pieces and never need this test."""
    rest = rest.astype(np.int64)
    r2 = 2 + rest @ _VM2
    vsum = rest @ _VAL
    vmax = np.where(rest > 0, _VAL, 0).max(axis=1)
    hx = (rest[:, _HX] > 0).any(axis=1)
    closed_ok = (r2 % 2 == 0) & (r2 >= 0) & (vsum >= 2 * vmax)
    radical_ok = (r2 % 2 == 1) & (r2 >= 1) & (vsum >= 2 * vmax - 1)
    return hx & np.where(radical, radical_ok, closed_ok)


def _k_min2(t2: np.ndarray) -> np.ndarray:
    """The necessary piece count of a formula from its 2 x DBE. k molecules of
    DBE >= 0 sum to DBE + k - 1, so a closed-shell formula needs k >= 1 - DBE; a
    radical one holds one piece of DBE >= 1/2, so k >= 3/2 - DBE, which for its
    half-integer DBE is the same ceil(1 - DBE). And k >= 1."""
    return np.maximum(1, (3 - t2) // 2)


@lru_cache(maxsize=_LOSS_CACHE)
def _decompose(key: tuple[int, ...], k_max: int) -> LossDecomposition | None:
    """``key`` = loss counts on the axis (all >= 0, not all 0).

    A search by total size k = 1, 2, ... k_max. The frontier holds every
    multiset of k - 1 library pieces that fits inside the loss (at most one
    radical piece, only for a radical loss), as ascending library-index tuples
    in lexicographic order. Size k reads either as one more library piece that
    makes the loss exactly (all named) or as a frontier multiset plus ONE
    unnamed remainder; all-named is tried first. The first hit is the answer,
    which is the documented choice: fewest pieces, then fewer unnamed, then
    library priority. Frontier multisets whose rest needs more pieces than are
    left (:func:`_k_min2`) are dropped, so only plausible splits are ever held."""
    loss = np.array(key, dtype=np.int16)
    t2 = 2 + int(loss.astype(np.int64) @ _VM2)
    radical = bool(t2 % 2)
    k_min = int(_k_min2(np.array([t2]))[0])
    if k_min > k_max:
        return None
    formula = _formula_of(key)
    if k_min == 1:
        # size 1, the common case: a library formula, else one valid unnamed
        # piece (a library hit always has the loss's own parity - tested)
        if key in _LIB_INDEX:
            return LossDecomposition(
                formula, (_LIB_NAMES[_LIB_INDEX[key]],), 0, radical
            )
        if _remainder_ok(loss[None, :], np.array([radical]))[0]:
            return LossDecomposition(formula, (formula,), 1, radical)
    # a closed-shell loss holds no radical piece
    n_lib = len(_LIB_NAMES) if radical else _N_CLOSED
    lib = _LIB_VEC[:n_lib]
    lib_rad = np.arange(n_lib) >= _N_CLOSED
    lib_ids = np.arange(n_lib)
    width = len(ELEMENTS)
    # the frontier, size 0: the empty multiset
    idx = np.zeros((1, 0), dtype=np.int16)  # (m, s) library indices, ascending
    acc = np.zeros((1, width), dtype=np.int16)  # (m, E) their element sums
    nrad = np.zeros(1, dtype=bool)  # (m,) holds the radical piece
    for k in range(1, k_max + 1):
        # every allowed one-piece extension that still fits, in (row, piece) =
        # lexicographic order: pieces at or after the row's last (no
        # permutations), and no second radical
        last = (
            idx[:, -1].astype(np.int64)
            if idx.shape[1]
            else np.zeros(len(idx), np.int64)
        )
        rows, js, exact = [], [], []
        for b in range(0, len(idx), _ROWS_PER_BLOCK):
            s = acc[b : b + _ROWS_PER_BLOCK, None, :] + lib[None, :, :]
            ok = (
                (s <= loss).all(axis=2)
                & (lib_ids[None, :] >= last[b : b + _ROWS_PER_BLOCK, None])
                & ~(lib_rad[None, :] & nrad[b : b + _ROWS_PER_BLOCK, None])
            )
            r, j = np.nonzero(ok)
            rows.append(r + b)
            js.append(j)
            exact.append((s[r, j] == loss).all(axis=1))
        rows, js, exact = (
            np.concatenate(rows),
            np.concatenate(js),
            np.concatenate(exact),
        )
        if k >= max(k_min, 2):  # size 1 was the fast path above
            if exact.any():  # all named
                i = int(np.flatnonzero(exact)[0])
                named = tuple(int(n) for n in idx[rows[i]]) + (int(js[i]),)
                return LossDecomposition(
                    formula, tuple(_LIB_NAMES[n] for n in named), 0, radical
                )
            # a named frontier multiset + one unnamed remainder, which is the
            # radical piece when the named part has none (never empty: an exact
            # row is a hit, so none reaches the frontier)
            rem = loss[None, :] - acc
            hit = np.flatnonzero(_remainder_ok(rem, radical & ~nrad))
            if len(hit):
                h = int(hit[0])
                named_pieces = tuple(_LIB_NAMES[int(n)] for n in idx[h])
                return LossDecomposition(
                    formula, named_pieces + (_formula_of(rem[h]),), 1, radical
                )
        if k == k_max:
            return None
        # the size-k frontier (no row of it is exact: an exact row at a size
        # below max(k_min, 2) would be a library piece, and k_min 1)
        idx = np.concatenate([idx[rows], js[:, None].astype(np.int16)], axis=1)
        acc = acc[rows] + lib[js]
        nrad = nrad[rows] | lib_rad[js]
        rest = (loss[None, :] - acc).astype(np.int64)
        keep = _k_min2(2 + rest @ _VM2) <= k_max - k
        idx, acc, nrad = idx[keep], acc[keep], nrad[keep]
        if not len(idx):
            return None
    return None


def decompose_loss(
    loss: str | Mapping[str, int],
    *,
    k_max: int = K_MAX_DEFAULT,
    allow_radical: bool = True,
) -> LossDecomposition | None:
    """Split a neutral loss into at most ``k_max`` molecules, or None when it
    cannot be.

    A closed-shell loss (integer DBE) splits into closed-shell pieces only; a
    radical loss (half-integer DBE; None unless ``allow_radical``) into exactly
    one radical piece plus closed-shell ones. Pieces are library formulas
    (:data:`CLOSED_SHELL_LOSSES`, :data:`RADICAL_LOSSES`) plus at most ONE
    unnamed remainder, which must itself pass: integer DBE >= 0 (radical:
    half-integer, >= 1/2), Senior's second rule (sum of valences >= 2 x the
    largest; radical: >= 2 x largest - 1), and at least one H or halogen. The
    choice is deterministic: fewest pieces, then fewer unnamed, then library
    priority. A loss string is parsed strictly (``'C2H4o2'`` and ``'H2O-H2'``
    raise). Memoised per (loss, k_max).

    :param loss: The loss formula, or its counts.
    :param k_max: The most pieces, 1 to :data:`K_MAX_LIMIT`.
    :param allow_radical: Whether a radical loss may be read at all.
    :raises ValueError: For a malformed or empty loss, or a bad ``k_max``.
    """
    k_max = _check_k_max(k_max)
    if isinstance(loss, str):
        counts = _parse_body(loss.strip(), f"loss {loss!r}")
    elif isinstance(loss, Mapping):
        counts = _counts(loss, f"loss {dict(loss)!r}")
    else:
        raise ValueError(f"loss must be a formula or counts, got {loss!r}")
    key = tuple(int(n) for n in _vec(counts))
    if not any(key):
        raise ValueError(f"empty loss {loss!r}")
    dec = _decompose(key, k_max)
    if dec is not None and dec.radical and not allow_radical:
        return None
    return dec


# ---------------------------------------------------------------------------
# The sub-formula grid of a parent ion
# ---------------------------------------------------------------------------
class _Grid(NamedTuple):
    els: tuple[str, ...]  # the parent's elements (its own axis)
    col: list[int]  # their positions on the global axis
    parent: np.ndarray  # (E,) parent counts on the global axis
    sub: np.ndarray  # (n, len(els)) valid fragment counts, mass-sorted
    mass: np.ndarray  # (n,) exact ion m/z, ascending
    t2: np.ndarray  # (n,) 2 x DBE


def _check_grid_size(key: tuple[tuple[str, int], ...], charge: int) -> None:
    n_rows = math.prod(n + 1 for _, n in key)
    if n_rows > GRID_MAX_ROWS:
        raise ValueError(
            f"parent {format_ion(dict(key), charge)} has {n_rows:,} sub-formulas "
            f"(limit {GRID_MAX_ROWS:,})"
        )


@lru_cache(maxsize=_GRID_CACHE)
def _parent_grid(key: tuple[tuple[str, int], ...], charge: int) -> _Grid:
    """Every element-wise sub-formula of the parent ion that passes the fragment
    gate (:func:`_gate`, the one :func:`fragment_ok` uses; the parent itself
    always stays), with its ion m/z, sorted by mass. Built once per parent
    composition AND charge: the masses carry ``- charge x electron``, so the
    cache key must too. One numpy meshgrid: a C30H50O15 parent has 25k
    sub-formulas before the gate."""
    _check_grid_size(key, charge)
    els = tuple(e for e, _ in key)
    cnt = [n for _, n in key]
    axes = np.meshgrid(*[np.arange(n + 1, dtype=np.int16) for n in cnt], indexing="ij")
    sub = np.stack([a.ravel() for a in axes], axis=1)
    col = [_IDX[e] for e in els]
    ok, t2 = _gate(sub, col)
    # the parent itself (the last meshgrid row) is an INPUT, not a reading, so
    # the gate never removes it: an ammonium cluster of a saturated molecule
    # sits at DBE -1.5 and is still its own precursor peak
    ok[-1] = True
    sub, t2 = sub[ok], t2[ok]
    mass = (
        sub.astype(np.float64) @ np.array([_MASS[e] for e in els])
        - charge * ELECTRON_MASS
    )
    order = np.argsort(mass, kind="stable")
    sub, t2, mass = sub[order], t2[order], mass[order]
    parent = np.zeros(len(ELEMENTS), dtype=np.int64)
    parent[col] = cnt
    return _Grid(els, col, parent, sub, mass, t2)


def _ion_key(counts: Mapping[str, int]) -> tuple[tuple[str, int], ...]:
    return tuple((e, int(counts[e])) for e in ELEMENTS if counts.get(e, 0))


def _resolve_parent(parent: str) -> tuple[tuple[tuple[str, int], ...], int]:
    """A parent is an ion formula STRING, checked by :func:`parse_ion` (sign,
    known elements, radical mark); anything else raises."""
    counts, charge = parse_ion(parent)
    return _ion_key(counts), charge


# ---------------------------------------------------------------------------
# The reading window
# ---------------------------------------------------------------------------
def _finite_number(name: str, value: object) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float, np.integer, np.floating))
        or not math.isfinite(value)
    ):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    return float(value)


@dataclass(frozen=True)
class Tolerance:
    """The reading window: max(``ppm`` x m/z, ``mda``) - ppm at high mass, an
    absolute floor at low mass, judged on the recalibrated axis.

    :param ppm: The relative half-width, ppm (default
        :data:`READING_TOLERANCE_PPM`).
    :param mda: The absolute floor, mDa (default :data:`READING_TOLERANCE_MDA`).
    :raises ValueError: For a negative or non-finite value.
    """

    ppm: float = READING_TOLERANCE_PPM
    mda: float = READING_TOLERANCE_MDA

    def __post_init__(self) -> None:
        for name in ("ppm", "mda"):
            value = _finite_number(f"tolerance {name}", getattr(self, name))
            if value < 0:
                raise ValueError(f"tolerance {name} must be >= 0, got {value!r}")
            object.__setattr__(self, name, value)

    def window_da(self, mz: float) -> float:
        """The window's half-width at ``mz``, Da."""
        return max(self.ppm * mz * 1e-6, self.mda * 1e-3)


#: The documented window: max(5 ppm, 0.3 mDa).
DEFAULT_TOLERANCE = Tolerance()


def _check_tolerance(tolerance: Tolerance) -> Tolerance:
    if not isinstance(tolerance, Tolerance):
        raise ValueError(f"tolerance must be a Tolerance, got {tolerance!r}")
    return tolerance


# ---------------------------------------------------------------------------
# Candidates for one peak and one parent
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FragmentCandidate:
    """One reading of a fragment m/z as a sub-formula of one parent ion."""

    formula: str  # fragment ion, e.g. 'C6H9+'
    parity: str  # 'EE' / 'OE'
    dbe: float  # of the fragment ion formula
    mz_theo: float  # exact m/z of `formula`
    ppm: float  # (measured - theoretical) / theoretical * 1e6
    loss: str  # parent - fragment, Hill; '' for the precursor itself
    loss_pieces: int
    loss_name: str  # pieces joined by ' + '; '' for the precursor
    unnamed_pieces: int
    radical_loss: bool  # half-integer-DBE loss (fragment parity != parent's)
    is_precursor: bool
    pieces: tuple[str, ...] = ()

    def sort_key(self) -> tuple:
        """Within one (peak, parent): closed-shell losses before radical ones,
        then fewer loss pieces, fewer unnamed pieces, smaller |ppm|, formula."""
        return (
            self.radical_loss,
            self.loss_pieces,
            self.unnamed_pieces,
            abs(self.ppm),
            self.formula,
        )


def _candidates(
    grid: _Grid,
    charge: int,
    mz: float,
    tolerance: Tolerance,
    k_max: int,
    allow_radical: bool,
) -> list[FragmentCandidate]:
    tol = tolerance.window_da(mz)
    lo = int(np.searchsorted(grid.mass, mz - tol, side="left"))
    hi = int(np.searchsorted(grid.mass, mz + tol, side="right"))
    parent_odd = (2 + int(grid.parent @ _VM2)) % 2 == 0
    out: list[FragmentCandidate] = []
    for i in range(lo, hi):
        theo = float(grid.mass[i])
        t2 = int(grid.t2[i])
        frag = {e: int(n) for e, n in zip(grid.els, grid.sub[i]) if n}
        formula = format_ion(frag, charge)
        parity = "EE" if t2 % 2 else "OE"
        ppm = (mz - theo) / theo * 1e6
        loss = grid.parent.copy()
        loss[grid.col] -= grid.sub[i]
        key = tuple(int(n) for n in loss)
        if not any(key):
            out.append(
                FragmentCandidate(
                    formula, parity, t2 / 2, theo, ppm, "", 0, "", 0, False, True
                )
            )
            continue
        dec = _decompose(key, k_max)
        if dec is None or (dec.radical and not allow_radical):
            continue
        # parity bookkeeping: a radical loss flips the electron parity
        assert dec.radical == ((parity == "OE") != parent_odd)
        out.append(
            FragmentCandidate(
                formula,
                parity,
                t2 / 2,
                theo,
                ppm,
                dec.formula,
                dec.k,
                dec.name,
                dec.unnamed,
                dec.radical,
                False,
                dec.pieces,
            )
        )
    out.sort(key=FragmentCandidate.sort_key)
    return out


def _check_mz(mz: object) -> float:
    try:
        value = float(mz)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError(f"mz must be a finite positive number, got {mz!r}") from None
    if not (math.isfinite(value) and value > 0):
        raise ValueError(f"mz must be a finite positive number, got {mz!r}")
    return value


def fragment_candidates(
    parent_ion: str,
    mz: float,
    *,
    k_max: int = K_MAX_DEFAULT,
    tolerance: Tolerance = DEFAULT_TOLERANCE,
    allow_radical: bool = True,
) -> list[FragmentCandidate]:
    """Every sub-formula of ``parent_ion`` within the window of ``mz`` whose loss
    decomposes (:func:`decompose_loss`), best first.

    ``mz`` should already be on the recalibrated axis (:func:`apply_offset`). The
    parent itself is a candidate (``is_precursor``, loss ``''``). Ranking:
    closed-shell losses before radical ones, then fewer loss pieces, fewer
    unnamed pieces, smaller |ppm|, formula. Equal readings are all returned -
    nothing is hidden behind the first.

    :raises ValueError: For a malformed parent, m/z, ``k_max`` or tolerance, or a
        parent whose grid would exceed :data:`GRID_MAX_ROWS`.
    """
    tolerance = _check_tolerance(tolerance)
    k_max = _check_k_max(k_max)
    mz = _check_mz(mz)
    pkey, charge = _resolve_parent(parent_ion)
    grid = _parent_grid(pkey, charge)
    return _candidates(grid, charge, mz, tolerance, k_max, bool(allow_radical))


# ---------------------------------------------------------------------------
# Recalibration (constant-ppm model)
# ---------------------------------------------------------------------------
class AnchorResidual(NamedTuple):
    """One anchor of :func:`mass_offset`: its error before and after the offset."""

    ion: str
    measured_mz: float
    theo_mz: float
    error_ppm: float  # before the offset
    residual_ppm: float  # after it


@dataclass(frozen=True)
class MassOffset:
    """A spectrum's constant-ppm axis offset and the anchors it was measured on."""

    offset_ppm: float
    anchors: tuple[AnchorResidual, ...]


def apply_offset(mz, offset_ppm: float):
    """The recalibrated m/z: ``mz / (1 + offset_ppm * 1e-6)``. Scalars and arrays."""
    return mz / (1.0 + float(offset_ppm) * 1e-6)


def mass_offset(anchors: Iterable[tuple[float, str]]) -> MassOffset:
    """The constant-ppm offset of an MS2 spectrum from anchors of known
    composition: (measured m/z, ion formula) pairs - the surviving precursor,
    optionally the reagent ion.

    ``offset_ppm`` is the median anchor error (signed: negative when the axis
    reads low; the mean of two anchors). Each anchor's theoretical m/z is
    :func:`ion_mz` of its own formula AND charge (an anion sits two electron
    masses above the cation), and its residual is read after the offset is
    applied. Constant-ppm only: one or two anchors cannot fit the low-mass
    absolute term, which is what the tolerance's mDa floor absorbs.

    :raises ValueError: For no anchors, a bad m/z or a malformed ion formula.
    """
    rows = []
    for item in anchors:
        measured, ion = item
        measured = _check_mz(measured)
        counts, charge = parse_ion(ion)
        theo = ion_mz(ion)
        rows.append(
            (
                format_ion(counts, charge),
                measured,
                theo,
                (measured - theo) / theo * 1e6,
            )
        )
    if not rows:
        raise ValueError("mass_offset needs at least one anchor")
    offset = float(np.median([r[3] for r in rows]))
    res = tuple(
        AnchorResidual(ion, m, theo, err, (apply_offset(m, offset) - theo) / theo * 1e6)
        for ion, m, theo, err in rows
    )
    return MassOffset(offset, res)


# ---------------------------------------------------------------------------
# A whole spectrum x the window ions -> readings
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Reading:
    """One row of a spectrum's readings: one (peak, parent, candidate), or the
    one row of a peak no parent explains.

    ``spectrum`` is the caller's key for the spectrum (an energy of a window).
    ``peak`` is the 0-based position of the peak in the input. ``rank`` 1 is the
    best within (spectrum, peak, parent), 0 an unexplained row;
    ``n_candidates`` counts the readings within (spectrum, peak, parent),
    ``n_parents`` the parents with at least one reading of the peak.
    ``ladder_support`` is the earlier step a four-piece reading stands on
    (:func:`ladder_gate`), ``''`` otherwise. An unexplained row carries ``''`` in
    every text field, 0 in every count, False in both flags and NaN in ``dbe``
    and ``ppm``, so a filter on a flag or a piece count never picks it up as a
    reading.
    """

    spectrum: str
    peak: int
    mz: float
    mz_cal: float
    intensity: float
    parent: str
    parent_ion: str
    formula: str
    parity: str
    dbe: float
    ppm: float
    loss: str
    loss_pieces: int
    loss_name: str
    unnamed_pieces: int
    radical_loss: bool
    is_precursor: bool
    rank: int
    n_candidates: int
    n_parents: int
    ladder_support: str = ""
    pieces: tuple[str, ...] = ()


def _unexplained(
    spectrum: str, peak: int, mz: float, mz_cal: float, intensity: float
) -> Reading:
    return Reading(
        spectrum,
        peak,
        mz,
        mz_cal,
        intensity,
        "",
        "",
        "",
        "",
        math.nan,
        math.nan,
        "",
        0,
        "",
        0,
        False,
        False,
        0,
        0,
        0,
    )


def _peak_arrays(peaks) -> tuple[np.ndarray, np.ndarray]:
    """-> (m/z, intensity) from a 1-D array-like of m/z, or from a mapping or a
    frame with an ``mz`` column and optionally ``intensity``."""
    if isinstance(peaks, Mapping) or hasattr(peaks, "columns"):
        if "mz" not in peaks:
            raise ValueError("peaks need an 'mz' column")
        mz = np.asarray(peaks["mz"], dtype=np.float64)
        inten = (
            np.asarray(peaks["intensity"], dtype=np.float64)
            if "intensity" in peaks
            else np.full(mz.shape, np.nan)
        )
    else:
        mz = np.asarray(peaks, dtype=np.float64)
        inten = np.full(mz.shape, np.nan)
    if mz.ndim > 1:
        # (mz, intensity) pairs or an (n, 2) centroid array: flattening would
        # read every intensity as one more m/z
        raise ValueError(
            f"peaks must be a 1-D array-like of m/z, got shape {mz.shape}; pass "
            f"a mapping or frame with 'mz' and 'intensity' columns instead"
        )
    mz, inten = mz.reshape(-1), inten.reshape(-1)
    if len(inten) != len(mz):
        raise ValueError("peaks' 'mz' and 'intensity' differ in length")
    if not np.isfinite(mz).all() or (mz <= 0).any():
        raise ValueError("every peak m/z must be finite and positive")
    return mz, inten


def _window_ions(window_ions) -> list[tuple[str, str, tuple, int]]:
    """-> [(label, ion formula, key, charge)] in the order given."""
    if isinstance(window_ions, str):
        items = [(window_ions, window_ions)]
    elif isinstance(window_ions, Mapping):
        items = [(str(k), v) for k, v in window_ions.items()]
    elif isinstance(window_ions, Sequence):
        items = [(str(p), p) for p in window_ions]
    else:
        raise ValueError(
            f"window ions must be an ion formula, a sequence of them or a mapping "
            f"label -> ion formula, got {type(window_ions).__name__}"
        )
    if not items:
        raise ValueError("no window ions given")
    labels = [label for label, _ in items]
    if len(set(labels)) != len(labels):
        raise ValueError(f"duplicate window ion labels: {labels}")
    out = []
    seen: dict[str, str] = {}
    for label, ion in items:
        pkey, charge = _resolve_parent(ion)
        formula = format_ion(dict(pkey), charge)
        # one ion under two labels or spellings would be read as two parents
        # (n_parents 2): composition cannot tell isomers apart, so it is a mistake
        if formula in seen:
            raise ValueError(
                f"duplicate window ion {formula}: labels {seen[formula]!r} and "
                f"{label!r}"
            )
        seen[formula] = label
        _check_grid_size(pkey, charge)  # refused up front, peaks or not
        out.append((label, formula, pkey, charge))
    if len({charge for *_, charge in out}) > 1:
        raise ValueError("window ions of both polarities in one window")
    return out


def read_fragments(
    peaks,
    window_ions,
    *,
    k_max: int = K_MAX_DEFAULT,
    tolerance: Tolerance = DEFAULT_TOLERANCE,
    offset_ppm: float = 0.0,
    allow_radical: bool = True,
    spectrum: str = "",
) -> tuple[Reading, ...]:
    """Read every peak of one MS2 spectrum against every ion of its isolation window.

    This is the COMPOSITION test only: a peak readable from two window ions
    keeps both readings (``n_parents`` 2); picking one needs intensities across
    collision energies and is not done here. Every window ion's sub-formula
    grid is size-checked before any peak.

    The readings come in input peak order, then window ion order as given,
    then rank. A peak no window ion explains still gets one row, so every input
    peak is in the output. ``mz_cal`` = ``apply_offset(mz, offset_ppm)`` is the
    axis every candidate is judged on. Without intensities ``intensity`` is NaN.

    :param peaks: A 1-D array-like of m/z, or a mapping or a frame with an
        ``mz`` column and optionally ``intensity``. 2-D input raises.
    :param window_ions: One ion formula, a sequence of them (labelled by
        themselves), or a mapping label -> ion formula: the target plus the
        co-isolated window ions.
    :param k_max: The most pieces a loss may split into.
    :param tolerance: The reading window.
    :param offset_ppm: The spectrum's constant-ppm axis offset
        (:func:`mass_offset`).
    :param allow_radical: Whether a radical loss is a reading.
    :param spectrum: The caller's key for this spectrum, carried on every row
        (:func:`ladder_gate` reads one window's spectra together).
    :raises ValueError: For malformed peaks, window ions, ``k_max``, tolerance or
        offset, and for one window ion given twice (under two labels or two
        spellings of one ion).
    """
    tolerance = _check_tolerance(tolerance)
    k_max = _check_k_max(k_max)
    offset_ppm = _finite_number("offset_ppm", offset_ppm)
    if not isinstance(spectrum, str):
        raise ValueError(f"spectrum must be a string, got {spectrum!r}")
    mz, inten = _peak_arrays(peaks)
    ions = _window_ions(window_ions)
    if not len(mz):
        return ()
    # each grid once per call, not once per peak: a window of more ions than
    # the grid cache holds would rebuild every grid at every peak
    parents = [
        (label, ion, _parent_grid(pkey, charge), charge)
        for label, ion, pkey, charge in ions
    ]
    mz_cal = apply_offset(mz, offset_ppm)
    out: list[Reading] = []
    for i in range(len(mz)):
        m, mc, it = float(mz[i]), float(mz_cal[i]), float(inten[i])
        per_ion = [
            (
                label,
                ion,
                _candidates(grid, charge, mc, tolerance, k_max, bool(allow_radical)),
            )
            for label, ion, grid, charge in parents
        ]
        n_parents = sum(1 for *_, cands in per_ion if cands)
        if n_parents == 0:
            out.append(_unexplained(spectrum, i, m, mc, it))
            continue
        for label, ion, cands in per_ion:
            for rank, c in enumerate(cands, start=1):
                out.append(
                    Reading(
                        spectrum,
                        i,
                        m,
                        mc,
                        it,
                        label,
                        ion,
                        c.formula,
                        c.parity,
                        c.dbe,
                        c.ppm,
                        c.loss,
                        c.loss_pieces,
                        c.loss_name,
                        c.unnamed_pieces,
                        c.radical_loss,
                        c.is_precursor,
                        rank,
                        len(cands),
                        n_parents,
                        "",
                        c.pieces,
                    )
                )
    return tuple(out)


def best_readings(readings: Iterable[Reading]) -> tuple[Reading, ...]:
    """The rank-1 reading of every (spectrum, peak, parent) plus the unexplained
    rows: one row per peak and explaining parent, in the input order."""
    return tuple(r for r in readings if r.rank == 1 or r.n_candidates == 0)


# ---------------------------------------------------------------------------
# The ladder gate on a fourth loss piece
# ---------------------------------------------------------------------------
@lru_cache(maxsize=_LOSS_CACHE)
def _loss_vec(formula: str) -> tuple[int, ...]:
    return tuple(int(n) for n in _vec(_parse_body(formula, f"loss {formula!r}")))


def _one_molecule(vec: np.ndarray) -> bool:
    """Is this count vector ONE named closed-shell library molecule - what
    :func:`decompose_loss` reads in a single piece, with no unnamed remainder
    and no radical?"""
    if (vec < 0).any() or not vec.any():
        return False
    i = _LIB_INDEX.get(tuple(int(n) for n in vec))
    return i is not None and i < _N_CLOSED


def _ladder_step(
    loss: str, pieces: Sequence[str], steps: set[tuple[int, ...]]
) -> np.ndarray | None:
    """The read step (a count vector of ``steps``) a reading of ``loss`` stands
    on, or None: a read step that is this loss minus ONE named closed-shell
    library molecule (:func:`_one_molecule`). First the pieces its
    decomposition prints that are one, in that order (``ladder_support`` names
    the first that finds a step); then any read step one such molecule back, the
    steps in the axis's lexicographic order. The printed decomposition is one of
    several of the same loss and need not hold the piece the ladder took:
    CH11NO5 prints as H3N + CH4O + H2O2 + H2O2, with no H2O, while the step one
    H2O back (CH9NO4) is read. Nor is every printed piece a step: an unnamed
    remainder or a radical (OH) is no molecule the chain lost."""
    # a step is a read loss: never empty, no count negative
    whole = np.array(_loss_vec(loss), dtype=np.int64)
    for piece in pieces:
        piece_vec = np.array(_loss_vec(piece), dtype=np.int64)
        prev = whole - piece_vec
        if tuple(prev.tolist()) in steps and _one_molecule(piece_vec):
            return prev
    for step in sorted(steps):
        prev = np.array(step, dtype=np.int64)
        if _one_molecule(whole - prev):
            return prev
    return None


def _rerank(rows: Sequence[Reading], keep: Sequence[bool]) -> tuple[Reading, ...]:
    """Keep the rows ``keep`` marks and re-rank: ``rank`` and ``n_candidates``
    within (spectrum, peak, parent), ``n_parents`` within (spectrum, peak). A
    peak whose every reading was dropped becomes ONE unexplained row, at the
    place its first row held; the order of everything else is kept."""
    if all(keep):
        return tuple(rows)
    groups: dict[tuple[str, int], list[int]] = {}
    for i, r in enumerate(rows):
        groups.setdefault((r.spectrum, r.peak), []).append(i)
    out: list[Reading] = []
    for idx in groups.values():
        kept = [rows[i] for i in idx if keep[i]]
        if not kept:
            first = rows[idx[0]]
            out.append(
                _unexplained(
                    first.spectrum, first.peak, first.mz, first.mz_cal, first.intensity
                )
                if first.rank
                else first
            )
            continue
        if any(r.rank > 0 for r in kept):
            size: dict[str, int] = {}
            for r in kept:
                size[r.parent] = size.get(r.parent, 0) + 1
            seen: dict[str, int] = {}
            ranked = []
            for r in kept:
                seen[r.parent] = seen.get(r.parent, 0) + 1
                ranked.append(
                    replace(
                        r,
                        rank=seen[r.parent],
                        n_candidates=size[r.parent],
                        n_parents=len(size),
                    )
                )
            kept = ranked
        out.extend(kept)
    return tuple(out)


def _check_row_order(rows: Sequence[Reading]) -> None:
    """Refuse rows that are not :func:`read_fragments`' own order: each (spectrum,
    peak) together and at one m/z, an unexplained row alone, and the ranks of
    each (spectrum, peak, parent) 1, 2, ... in row order. Spectra that share a
    ``spectrum`` key, or a table re-sorted by ppm or intensity, would otherwise
    be read with the wrong steps and re-ranked across spectra."""
    done: set[tuple[str, int]] = set()
    group: tuple[str, int] | None = None
    mz = math.nan
    ranks: dict[str | None, int] = {}  # parent -> last rank; None: unexplained
    for r in rows:
        key = (r.spectrum, r.peak)
        if key != group:
            if key in done:
                raise ValueError(
                    f"ladder_gate: the rows of spectrum {r.spectrum!r} peak "
                    f"{r.peak} are not together; give each spectrum its own "
                    f"spectrum key and keep read_fragments' row order"
                )
            if group is not None:
                done.add(group)
            group, mz, ranks = key, r.mz, {}
        elif r.mz != mz:
            raise ValueError(
                f"ladder_gate: spectrum {r.spectrum!r} peak {r.peak} has two m/z, "
                f"{mz!r} and {r.mz!r}; give each spectrum its own spectrum key"
            )
        if r.rank == 0 or None in ranks:
            if ranks:
                raise ValueError(
                    f"ladder_gate: spectrum {r.spectrum!r} peak {r.peak} has an "
                    f"unexplained row beside readings; give each spectrum its own "
                    f"spectrum key"
                )
            ranks[None] = 0
            continue
        expected = ranks.get(r.parent, 0) + 1
        if r.rank != expected:
            raise ValueError(
                f"ladder_gate: spectrum {r.spectrum!r} peak {r.peak} parent "
                f"{r.parent!r} has rank {r.rank} where {expected} belongs; keep "
                f"read_fragments' row order"
            )
        ranks[r.parent] = r.rank


def ladder_gate(
    readings: Iterable[Reading], *, k: int = LADDER_K
) -> tuple[Reading, ...]:
    """Keep a ``k``-piece reading only when the same window ion's step before it
    is read, at any spectrum of the readings (one window, every energy).

    The step is a reading of the same ``parent`` that is first among the
    non-precursor readings of its (spectrum, peak, parent) with at most ``k`` -
    1 pieces - what ``k_max`` ``k`` - 1 would rank first - and whose loss is this
    loss minus ONE named closed-shell library molecule, printed among this
    reading's pieces or not; an unnamed remainder or a radical is no step.
    ``ladder_support`` records that step's loss (the first printed piece, in
    the decomposition's order, that is one and finds one; else the first such
    step). Readings above ``k`` pieces are dropped, every other reading is kept
    as it is, and the readings are re-ranked; a four-piece reading never
    supports another.

    The readings must hold ONE window, read at ``k_max`` >= ``k``
    (:func:`read_fragments` with ``k_max=LADDER_K``, once per spectrum, each
    with its own ``spectrum`` key), in :func:`read_fragments`' row order. An
    ammonium adduct of a polyol walks a chain - [M+NH4]+ loses NH3 and then
    water step by step - whose fourth step (NH3 + 3 H2O, DBE -3) needs four
    molecules in any library; the gate admits it only when its previous step is
    observed.

    :raises ValueError: For ``k`` outside 2 to :data:`K_MAX_LIMIT`, and for rows
        out of :func:`read_fragments`' order: a (spectrum, peak) that comes back
        after another or carries two m/z, an unexplained row beside readings,
        or ranks of a (spectrum, peak, parent) that are not 1, 2, ... in order.
        Two spectra under one ``spectrum`` key, or a table re-sorted before the
        gate, are refused rather than read wrong.
    """
    k = _check_k_max(k, lowest=2)
    rows = tuple(readings)
    if not rows:
        return rows
    _check_row_order(rows)
    # each window ion's read steps as count vectors (never strings): the first
    # <= k-1-piece non-precursor reading of each (spectrum, peak, parent)
    steps: dict[str, set[tuple[int, ...]]] = {}
    first_seen: set[tuple[str, int, str]] = set()
    for r in rows:
        if r.rank > 0 and r.loss_pieces <= k - 1 and not r.is_precursor:
            key = (r.spectrum, r.peak, r.parent)
            if key not in first_seen:
                first_seen.add(key)
                steps.setdefault(r.parent, set()).add(_loss_vec(r.loss))
    keep = [r.rank == 0 or r.loss_pieces < k for r in rows]
    out = list(rows)
    for i, r in enumerate(rows):
        if r.rank > 0 and r.loss_pieces == k:
            # a reading rebuilt from a table carries its pieces in loss_name
            pieces = r.pieces or tuple(r.loss_name.split(" + "))
            prev = _ladder_step(r.loss, pieces, steps.get(r.parent, set()))
            if prev is not None:
                keep[i] = True
                out[i] = replace(r, ladder_support=_formula_of(prev))
    return _rerank(out, keep)
