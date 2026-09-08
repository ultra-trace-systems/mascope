"""The neutral grid: every composition the search may propose, enumerated once.

A composition search asks the same question of every peak - which combinations of
the allowed element counts have a mass this close to this one - and the set of
combinations does not depend on the peak. Only the window into it does. So the
grid is built once, sorted by mass, and each peak is answered by two binary
searches into it.

That is the whole of this module, and it is worth a module because the
alternative is what the finder used to do: walk the count tree from the root for
every peak, with the peak's own window as the pruning bound. On a dense spectrum
the same tree was walked thousands of times. Enumerating once is not a constant
factor - it is the difference between cost that scales with peaks x tree and cost
that scales with peaks + tree.

The walk itself is the same depth-first enumeration with the same pruning, which
is why a single-peak caller loses nothing: a grid over one peak's window IS that
peak's search. What changes is who owns the window.

The size guard is the price of holding the answer instead of recomputing it. An
element box wide enough to be interesting over a mass range wide enough to be a
spectrum can hold millions of compositions, and a caller may hand this any box
the API's species cap allows. :data:`DEFAULT_MAX_GRID_ROWS` bounds what is kept;
past it the build gives up and answers None, and the caller falls back to a grid
per peak - which is bounded by construction, because a peak's window is narrow.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from math import ceil, floor

import numpy as np

from mascope_tools.composition import utils
from mascope_tools.composition.config import UNSATURATION_COEFFICIENTS
from mascope_tools.composition.exceptions import CompositionFinderWarning
from mascope_tools.composition.models import Atom, CompositionSearchConfig


__all__ = [
    "DEFAULT_MAX_GRID_ROWS",
    "NeutralGrid",
    "build_neutral_grid",
]


#: How many compositions a shared grid may hold. Sized so the arrays behind it
#: stay in the tens of megabytes (a row is one float64 mass, one float32
#: unsaturation and one int32 per element), which is what a search running in a
#: worker thread beside the rest of an API process can spend. It is a bound on
#: the answer, not on the question: a box too wide for it is still searched, one
#: peak at a time, at the cost the finder used to pay for every peak.
DEFAULT_MAX_GRID_ROWS = 2_000_000

#: Rows accumulated in Python lists before they are packed into arrays.
#: Small enough that the flat lists stay a few megabytes, large enough
#: that the packing is not what the walk spends its time on.
_CHUNK_ROWS = 262_144


@dataclass(frozen=True)
class NeutralGrid:
    """Neutral compositions of one element box, sorted by mass.

    :param atoms: The element box, in the order the count columns are in.
    :param mass: Monoisotopic neutral mass of each composition, ascending.
    :param counts: One row per composition, one column per atom.
    :param unsaturation: Ring-plus-double-bond equivalents per composition, or
        None when the search did not ask for it.
    :param pyteomics_symbols: Each atom's symbol in pyteomics notation, which is
        what an ion formula has to be built from.
    """

    atoms: tuple[Atom, ...]
    mass: np.ndarray
    counts: np.ndarray
    unsaturation: np.ndarray | None
    pyteomics_symbols: tuple[str, ...]

    def __len__(self) -> int:
        return int(self.mass.size)

    def window(self, centre: float, tolerance: float) -> range:
        """The rows whose mass is within ``tolerance`` of ``centre``.

        :param centre: The neutral mass sought.
        :param tolerance: Half-width of the window, in daltons.
        :return: A range of row indices, empty when nothing is in the window.
        """
        # 'left' on the low bound and 'right' on the high one make the window
        # closed at both ends, which is what the search's `<= tolerance` means.
        first = np.searchsorted(self.mass, centre - tolerance, side="left")
        last = np.searchsorted(self.mass, centre + tolerance, side="right")
        return range(int(first), int(last))

    def composition(self, row: int) -> dict[str, int]:
        """The element counts of one row, keyed by the grid's own symbols."""
        return {
            atom.symbol: int(count)
            for atom, count in zip(self.atoms, self.counts[row])
            if count
        }

    def pyteomics_composition(self, row: int) -> dict[str, int]:
        """The element counts of one row, keyed for pyteomics ('N[15]')."""
        return {
            symbol: int(count)
            for symbol, count in zip(self.pyteomics_symbols, self.counts[row])
            if count
        }


def build_neutral_grid(
    config: CompositionSearchConfig,
    mass_min: float,
    mass_max: float,
    max_rows: int = DEFAULT_MAX_GRID_ROWS,
) -> NeutralGrid | None:
    """Enumerate every allowed composition whose mass falls in a range.

    :param config: The search whose element box, and whose unsaturation window
        when it uses one, decides what is in the grid.
    :param mass_min: Lowest neutral mass to keep.
    :param mass_max: Highest neutral mass to keep.
    :param max_rows: Give up and answer None past this many compositions.
    :return: The grid, or None when the box is too wide to hold at this range.
    """
    if mass_max < mass_min:
        return None
    atoms = utils.parse_atom_count_ranges(config.element_count_ranges)
    # Heaviest first: the coarsest choice made at the top of the tree is the one
    # that prunes the most below it.
    atoms.sort(key=lambda atom: atom.mass, reverse=True)
    if not atoms:
        return None

    coefficients = (
        _unsaturation_coefficients(atoms) if config.use_unsaturation else None
    )
    enumerated = _enumerate(
        atoms=atoms,
        mass_min=mass_min,
        mass_max=mass_max,
        coefficients=coefficients,
        min_unsaturation=config.min_unsaturation,
        max_unsaturation=config.max_unsaturation,
        only_integer_unsaturation=config.only_integer_unsaturation,
        max_rows=max_rows,
    )
    if enumerated is None:
        return None
    masses, counts, unsaturations = enumerated

    order = np.argsort(masses, kind="stable")
    return NeutralGrid(
        atoms=tuple(atoms),
        mass=masses[order],
        counts=counts[order],
        unsaturation=None if unsaturations is None else unsaturations[order],
        pyteomics_symbols=tuple(utils.to_pyteomics(atom.symbol) for atom in atoms),
    )


def _unsaturation_coefficients(atoms: list[Atom]) -> list[int]:
    """The unsaturation coefficient of each atom, warning once about the unknown.

    :func:`finder.get_unsaturation` warns per formula; this warns per grid, for
    the same reason and to the same effect - a search over a box containing
    sulphur said so a million times.
    """
    coefficients = []
    for atom in atoms:
        if atom.symbol not in UNSATURATION_COEFFICIENTS:
            warnings.warn(
                f"Unsaturation coefficient for '{atom.symbol}' not supported, using 0.",
                CompositionFinderWarning,
            )
        coefficients.append(UNSATURATION_COEFFICIENTS.get(atom.symbol, 0))
    return coefficients


def _enumerate(
    atoms: list[Atom],
    mass_min: float,
    mass_max: float,
    coefficients: list[int] | None,
    min_unsaturation: float,
    max_unsaturation: float,
    only_integer_unsaturation: bool,
    max_rows: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None] | None:
    """Depth-first walk of the count tree, keeping what lands in the mass range.

    The walk recurses over every element but the last, and takes the last one -
    the lightest, since the atoms are ordered heaviest first - a whole run at a
    time. That run is where nearly all the leaves are: hold the heavy atoms fixed
    and the light one's feasible counts are a contiguous stretch of integers,
    tens of rows on an ordinary box. Recursing into it would spend a Python call
    per composition, and there are millions of them; as a range it is a handful
    of array operations.

    Rows land in preallocated buffers that are packed away as they fill, rather
    than in per-row tuples - at grid size the intermediate, not the result, is
    what runs a process out of memory.
    """
    width = len(atoms)
    masses = [atom.mass for atom in atoms]
    minima = [atom.min_count for atom in atoms]
    maxima = [atom.max_count for atom in atoms]
    count_dtype = np.int16 if max(maxima, default=0) <= 32767 else np.int32
    # Lightest and heaviest the atoms after position i can still contribute; the
    # bounds that let a branch be abandoned before its leaves are reached.
    tail_min = [0.0] * width
    tail_max = [0.0] * width
    running_min = running_max = 0.0
    for i in range(width - 1, -1, -1):
        tail_min[i] = running_min
        tail_max[i] = running_max
        running_min += minima[i] * masses[i]
        running_max += maxima[i] * masses[i]

    last = width - 1
    last_mass = masses[last]
    last_coefficient = 0 if coefficients is None else coefficients[last]

    mass_chunks: list[np.ndarray] = []
    count_chunks: list[np.ndarray] = []
    unsaturation_chunks: list[np.ndarray] = []
    mass_buffer = np.empty(_CHUNK_ROWS, dtype=float)
    count_buffer = np.empty((_CHUNK_ROWS, width), dtype=count_dtype)
    unsaturation_buffer = (
        np.empty(_CHUNK_ROWS, dtype=np.float32) if coefficients is not None else None
    )
    filled = 0
    kept = 0
    counts = [0] * width
    overflowed = False

    def flush() -> None:
        nonlocal filled
        mass_chunks.append(mass_buffer[:filled].copy())
        count_chunks.append(count_buffer[:filled].copy())
        if unsaturation_buffer is not None:
            unsaturation_chunks.append(unsaturation_buffer[:filled].copy())
        filled = 0

    def emit(
        row_masses: np.ndarray,
        row_last_counts: np.ndarray,
        row_unsaturations: np.ndarray | None,
    ) -> None:
        """Take a run of the last element's counts into the buffers."""
        nonlocal filled, kept, overflowed
        taken = 0
        total = row_masses.size
        while taken < total:
            if kept >= max_rows:
                overflowed = True
                return
            room = min(_CHUNK_ROWS - filled, total - taken, max_rows - kept)
            end = taken + room
            mass_buffer[filled : filled + room] = row_masses[taken:end]
            count_buffer[filled : filled + room, :last] = np.asarray(
                counts[:last], dtype=count_dtype
            )
            count_buffer[filled : filled + room, last] = row_last_counts[taken:end]
            if unsaturation_buffer is not None and row_unsaturations is not None:
                unsaturation_buffer[filled : filled + room] = row_unsaturations[
                    taken:end
                ]
            filled += room
            kept += room
            taken = end
            if filled == _CHUNK_ROWS:
                flush()

    def walk(index: int, mass: float, unsaturation_sum: int) -> None:
        # The counts that could still reach the range, given what the atoms after
        # this one can add. The one-count slack on each side is the float margin
        # the finder has always carried here.
        atom_mass = masses[index]
        lowest = max(
            minima[index],
            int(ceil((mass_min - mass - tail_max[index]) / atom_mass)) - 1,
        )
        highest = min(
            maxima[index],
            int(floor((mass_max - mass - tail_min[index]) / atom_mass)) + 1,
        )
        if lowest > highest:
            return

        if index == last:
            run = np.arange(lowest, highest + 1)
            run_masses = mass + run * last_mass
            keep = (run_masses >= mass_min) & (run_masses <= mass_max)
            run_unsaturations = None
            if coefficients is not None:
                run_unsaturations = (
                    unsaturation_sum + last_coefficient * run + 2
                ) / 2.0
                keep &= run_unsaturations >= min_unsaturation
                keep &= run_unsaturations <= max_unsaturation
                if only_integer_unsaturation:
                    keep &= run_unsaturations == np.floor(run_unsaturations)
                run_unsaturations = run_unsaturations[keep]
            if keep.any():
                emit(run_masses[keep], run[keep], run_unsaturations)
            return

        coefficient = 0 if coefficients is None else coefficients[index]
        for count in range(lowest, highest + 1):
            counts[index] = count
            walk(
                index + 1,
                mass + count * atom_mass,
                unsaturation_sum + coefficient * count,
            )
            if overflowed:
                return

    walk(0, 0.0, 0)
    if overflowed:
        return None
    if filled or not mass_chunks:
        flush()

    return (
        np.concatenate(mass_chunks),
        np.concatenate(count_chunks),
        np.concatenate(unsaturation_chunks) if coefficients is not None else None,
    )
