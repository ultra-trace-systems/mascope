"""An ion's monoisotopic isotopologue: the M0 an isotope table counts from.

Every role and offset label the engine writes counts from it
(``engine.invert_matches_to_peak_assignments``): for a bromine- or
chlorine-rich ion the lightest line of the cluster rather than the tallest, for
a labelled ion the labelled line rather than the unlabelled remainder below it.
Moved out of ``engine`` with :func:`monoisotopic_positions`, which reads the
same row for every ion of a frame at once (MAS-1172).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from mascope_tools.composition.custom_elements import CUSTOM_ELEMENTS
from mascope_tools.composition.utils import parse_formula_tokens


def labelled_isotopes(ion_formula) -> dict[str, int]:
    """The isotopes an ion carries by design, spelled the way its isotopologue
    formulas spell them: a labelled reagent's ``^N`` is ``{"[15N]": 1}``,
    ``^N2`` is ``{"[15N]": 2}``, and an ion without a label carries none.

    :param ion_formula: The ion's formula (``C9H16O7^N-``).
    """
    labels: dict[str, int] = {}
    if not isinstance(ion_formula, str):
        return labels
    for symbol, count in parse_formula_tokens(ion_formula).items():
        element = CUSTOM_ELEMENTS.get(symbol)
        if element is not None:
            token = f"[{element.labelled_massnumber}{element.base_element}]"
            labels[token] = labels.get(token, 0) + count
    return labels


def _substituted_isotopes(isotopologue_formula: str) -> dict[str, int]:
    """The isotopes an isotopologue formula names in brackets, with counts."""
    return {
        symbol: count
        for symbol, count in parse_formula_tokens(isotopologue_formula).items()
        if symbol.startswith("[")
    }


def is_monoisotopic_formula(formula, labels: dict[str, int] | None = None) -> bool:
    """Whether an isotopologue formula names the ion's monoisotopic isotopologue.

    The generator writes a substituted isotope in brackets (``C5[13C]H13O6+``,
    ``[81Br]Br2-``) and the monoisotopic isotopologue - every element at its
    most abundant isotope - without (``C6H13O6+``, ``Br3-``). A labelled
    reagent's atom is written in brackets too, because its isotope is the one
    the label put there, so the monoisotopic isotopologue of a labelled ion
    names exactly its labels and nothing else: ``[15N]C9H16O7-`` for
    ``C9H16O7^N-``. The formula without a bracket is then the reagent's
    unlabelled remainder, one mass unit below the line the ion is measured by.

    At a low resolution one line holds several isotopologues, their names
    joined by "/"; it is the monoisotopic line when any of them is.

    :param formula: An isotopologue formula.
    :param labels: The ion's labelled isotopes (:func:`labelled_isotopes`);
        none for an ion without a label.
    """
    if not isinstance(formula, str) or not formula:
        return False
    expected = labels or {}
    return any(_substituted_isotopes(name) == expected for name in formula.split("/"))


def monoisotopic_row(ion_rows: pd.DataFrame) -> pd.Series:
    """The row of an ion's monoisotopic isotopologue: the M0 every role and
    offset label counts from, the way an isotope table counts - which for a
    bromine- or chlorine-rich ion is the lightest peak of the cluster, not the
    tallest, and for a labelled ion is the labelled line, not the unlabelled
    remainder below it. The lightest row stands in when no formula carries the
    isotope marker that tells them apart, and is the same row wherever an
    element's most abundant isotope is also its lightest.

    Positionally off a sort rather than ``.loc[idxmin()]``: a frame that has
    been through a gate and a scorer can carry a duplicated index, and that
    lookup would then hand back a frame where every caller expects one row.

    :param ion_rows: One ion's rows of an isotope frame (``mz``, and
        ``target_isotope_formula`` and ``target_ion_formula`` where the frame
        has them).
    """
    ordered = ion_rows.sort_values("mz")
    if "target_isotope_formula" in ordered.columns:
        labels = (
            labelled_isotopes(ordered["target_ion_formula"].iloc[0])
            if "target_ion_formula" in ordered.columns
            else {}
        )
        mono = ordered[
            ordered["target_isotope_formula"]
            .map(lambda formula: is_monoisotopic_formula(formula, labels))
            .astype(bool)
        ]
        if not mono.empty:
            return mono.iloc[0]
    return ordered.iloc[0]


def monoisotopic_positions(frame: pd.DataFrame) -> list[int]:
    """Each ion's monoisotopic row in an isotope frame, by position: the row
    :func:`monoisotopic_row` picks from the ion's rows, one per ion of
    ``groupby("target_ion_id")``.

    Read off the frame's columns once rather than sorted and mapped ion by
    ion, which on a sample's whole known set is seconds (MAS-1172), and the
    same row: the ion's rows in the order ``sort_values("mz")`` puts them
    (``pandas``' quicksort, a missing m/z last), then the first whose formula
    names the monoisotopic isotopologue, else the first.

    :param frame: An isotope frame with ``target_ion_id`` and ``mz``, and
        ``target_isotope_formula`` and ``target_ion_formula`` where it has them.
    """
    mz = frame["mz"].to_numpy(dtype=float)
    columns = frame.columns
    isotopes = (
        frame["target_isotope_formula"].to_numpy(dtype=object)
        if "target_isotope_formula" in columns
        else None
    )
    ions = (
        frame["target_ion_formula"].to_numpy(dtype=object)
        if "target_ion_formula" in columns
        else None
    )
    picked = []
    for rows in frame.groupby("target_ion_id", sort=False).indices.values():
        present = rows[~np.isnan(mz[rows])]
        ordered = np.concatenate(
            [present[mz[present].argsort(kind="quicksort")], rows[np.isnan(mz[rows])]]
        )
        pick = ordered[0]
        if isotopes is not None:
            labels = {} if ions is None else labelled_isotopes(ions[ordered[0]])
            pick = next(
                (
                    row
                    for row in ordered
                    if is_monoisotopic_formula(isotopes[row], labels)
                ),
                pick,
            )
        picked.append(int(pick))
    return picked


__all__ = [
    "is_monoisotopic_formula",
    "labelled_isotopes",
    "monoisotopic_positions",
    "monoisotopic_row",
]
