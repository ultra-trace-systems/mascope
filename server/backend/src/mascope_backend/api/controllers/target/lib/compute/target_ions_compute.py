"""
Functions for target ions and target isotopes generation.
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from mascope_backend.api.models.target.compounds.target_compound_pydantic_model import (
    TargetCompoundBase,
)
from mascope_backend.db import (
    IonizationMechanism,
    TargetIon,
    TargetIsotope,
)
from mascope_backend.db.id import gen_id
from mascope_backend.runtime import runtime
from mascope_tools.composition.config import (
    ENVELOPE_ABUNDANCE_FLOOR,
    TARGET_LIBRARY_ABUNDANCE_FLOOR,
)
from mascope_tools.composition.custom_elements import CUSTOM_ELEMENTS
from mascope_tools.composition.finder import replace_atom_with_isotope
from mascope_tools.composition.line_model import (
    predict_isotopes as predict_fine_lines,
)
from mascope_tools.composition.line_model import resolve_lines
from mascope_tools.composition.mechanism_notation import (
    AdductRow,
    MechanismNotationError,
    to_row,
)
from mascope_tools.composition.utils import (
    assert_valid_formula,
    parse_composition,
    parse_formula_tokens,
    to_hill_notation,
)


# The resolving power the stored library's LOW lines are merged at. The
# classic match (`mascope_match`) matches a TOF file against these stored lines
# and stores its results against their ids, so they are computed once, when a
# target is saved, with no file to read a resolution off. The peak assignment
# reads no stored line: it predicts each ion's lines at the file's own R(m/z)
# (`ion_lines`, MAS-977), as the envelope match does.
STORED_TOF_RESOLVING_POWER = 1e4
# The column a low resolution isotope's joined formulae are stored in
TARGET_ISOTOPE_FORMULA_LENGTH = (
    TargetIsotope.__table__.c.target_isotope_formula.type.length
)


#: The column a file's resolved line carries its leading member's formula in:
#: the brightest isotopologue it holds, which the line is read as. On an
#: Orbitrap the 13C line holds its 17O line and folds its 2H line, and is still
#: the 13C line the run's response curve is fitted on
#: (``envelope_fit.envelope_lines``).
LEAD_ISOTOPE_FORMULA = "lead_isotope_formula"


@dataclass(frozen=True)
class FileLines:
    """The file an ion's lines are predicted for: the R(m/z) they are merged at.

    :ivar resolution_type: The file's resolution type, ``LOW`` for a TOF and
        ``HIGH`` otherwise, which the lines are labelled with.
    :ivar resolution: The file's resolving power as a function of m/z, or None
        where its instrument functions could not be read. Then a TOF's lines
        are merged at :data:`STORED_TOF_RESOLVING_POWER`, as the stored
        library's are, and another file's lines are the fine structure.
    """

    resolution_type: str
    resolution: Callable[[float], float] | None = None

    def resolving_power(self) -> Callable[[float], float] | None:
        """The R(m/z) to merge at, or None for the fine structure."""
        if self.resolution is not None:
            return self.resolution
        if self.resolution_type == "LOW":
            return lambda _: STORED_TOF_RESOLVING_POWER
        return None


class SkipIonizationMechanism(Exception):
    """Exception to skip an ionization mechanism when generating target ions."""

    pass


class UnknownIonizationMechanism(Exception):
    """Exception for unknown ionization mechanisms."""

    pass


class UnknownCustomElement(Exception):
    """Exception for unknown custom elements in formulas."""

    pass


def charge_string(charge: int) -> str:
    """Get charge string (+/-) for an ion charge.

    The sign once for each charge, so an ion formula's charge is still the
    signs it ends in and its formula what is left when they are stripped:
    a count before the sign would read as the last element's.

    :param charge: Ion charge (e.g. +1, -1, +2, 0)
    :type charge: int
    :return: Charge string: "+" or "-", "++" for +2, or "" for a neutral
    :rtype: str

    Examples
    --------
    >>> charge_string(1)
    '+'
    >>> charge_string(-1)
    '-'
    >>> charge_string(2)
    '++'
    >>> charge_string(0)
    ''
    """
    if charge > 0:
        return "+" * charge
    if charge < 0:
        return "-" * -charge
    return ""


def generate_target_ions_from_composition(
    target_compound: TargetCompoundBase,
    ionization_mechanisms: list[IonizationMechanism],
    *,
    file_lines: "FileLines | None" = None,
) -> tuple[list[TargetIon], list[TargetIsotope]]:
    """Generate target ions and isotopes based on target compound composition and given ionization mechanisms

    Combines the neutral formula with each ionization mechanism (formula addition/
    subtraction), potentially including custom elements (e.g. ^N). Predicts isotopic
    patterns with the line model (``mascope_tools.composition.line_model``).

    The stored library holds two sets of lines per ion: ``HIGH``, the fine
    structure, and ``LOW``, merged at :data:`STORED_TOF_RESOLVING_POWER` for
    the classic match. A reader with a file in hand passes ``file_lines`` and
    gets one set, the lines that file's R(m/z) shows (:func:`ion_lines`),
    labelled with the file's resolution type so a reader filtering by it keeps
    them.

    :param target_compound: Target compound to use as a base for the ions
    :type target_compound: TargetCompoundBase
    :param ionization_mechanisms: List of ionization mechanisms to apply to the target compound
    :type ionization_mechanisms: list[IonizationMechanism]
    :param file_lines: The file whose lines to predict, or None for the stored
        library's two sets.
    :return: 2-tuple of (list of ions (instances of TargetIon), list of isotopes (instances of TargetIsotope))
    :rtype: tuple
    """
    target_ions = []
    target_isotopes = []

    # generate and create ion records
    target_compound_formula = target_compound.target_compound_formula.rstrip()

    # parse_composition silently drops characters it does not recognise, so an
    # invalid formula (garbage like 'xyz', a leftover numeric mass, or an unknown
    # custom element) would otherwise yield an empty/partial composition and
    # produce bogus adduct-only ions - or make IsoSpecPy raise further down. The
    # retired molmass fork raised FormulaError here, which the caller treated as
    # "skip". Preserve that: reject the whole compound (no ions) up front. This
    # also guards existing (pre-validation) rows reached via create_target_ions.
    try:
        assert_valid_formula(target_compound_formula)
    except ValueError as e:
        # INFO: fires per row of a user-imported collection
        runtime.logger.info(
            f"Skipping target compound with invalid formula "
            f"'{target_compound_formula}': {e}"
        )
        return [], []

    for ionization_mechanism in ionization_mechanisms:
        mechanism = ionization_mechanism.ionization_mechanism

        try:
            row = _read_mechanism(mechanism)
            compound_composition = _get_compound_composition(
                target_compound_formula, row
            )
            ion_composition, ion_charge = _get_raw_ion(row, compound_composition)
        except (SkipIonizationMechanism, ValueError) as e:
            runtime.logger.debug(
                f"Skipping ionization mechanism {mechanism} for compound {target_compound_formula}: {e}"
            )
            continue
        except UnknownIonizationMechanism as e:
            runtime.logger.info(
                f"Failed to parse ion formula for compound {target_compound_formula} "
                f"and mechanism {mechanism}: {e}"
            )
            # Try to create ions with other mechanisms
            continue

        # Ion formula (no charge) in classic Hill notation. Explicit isotopes are
        # folded into their base element (labelled '^X' elements are preserved).
        ion_formula = to_hill_notation(ion_composition)
        charge_str = charge_string(ion_charge)

        # construct and save ion row
        runtime.logger.debug(
            f"Generated ion formula {ion_formula}{charge_str} for compound "
            f"{target_compound_formula} with mechanism {mechanism}"
        )
        ion = TargetIon(
            target_ion_id=gen_id(16),
            target_compound_id=target_compound.target_compound_id,
            ionization_mechanism_id=ionization_mechanism.ionization_mechanism_id,
            target_ion_formula=ion_formula + charge_str,
            filter_params={},
        )
        target_ions.append(ion)

        predicted_isotopes = dict()
        if file_lines is not None:
            predicted_isotopes[file_lines.resolution_type] = ion_lines(
                ion_composition, ion_charge, file_lines.resolving_power()
            )
        else:
            # The fine structure, and the classic match's TOF lines
            predicted_isotopes["HIGH"] = predict_isotopes(ion_composition, ion_charge)
            predicted_isotopes["LOW"] = group_target_isotopes(
                *predicted_isotopes["HIGH"], STORED_TOF_RESOLVING_POWER
            )
        # Store target isotopes
        for resolution, (masses, probs, formulae) in predicted_isotopes.items():
            target_isotopes.extend(
                [
                    TargetIsotope(
                        target_isotope_id=gen_id(16),
                        target_ion_id=ion.target_ion_id,
                        mz=mz,
                        relative_abundance=rel_abu,
                        resolution=resolution,
                        target_isotope_formula=form,
                    )
                    for mz, rel_abu, form in zip(masses, probs, formulae)
                ]
            )

    return target_ions, target_isotopes


def _read_mechanism(ionization_mechanism: str) -> AdductRow:
    """Read a stored mechanism, in either notation, as its structured row:
    ``[M+H]+`` or ``+H+``, ``[M-H]-`` or ``-H+`` (deprotonation), ``[M+Br]-``
    or ``+Br-``, electron transfer, ``[M]+.`` / ``[M]-.`` or ``+`` / ``-``,
    and what only the standard notation says: ``[2M+H]+``, ``[M+2H]2+``,
    ``[M+H-H2O]+``.

    :raises UnknownIonizationMechanism: The text is neither notation - a row
        older validation let through, such as ``"++"``.
    """
    try:
        return to_row(ionization_mechanism)
    except MechanismNotationError as e:
        raise UnknownIonizationMechanism(str(e)) from e


def _composition_counts(formula: str) -> dict[str, int]:
    """Parse ``formula`` into a plain ``{symbol: count}`` dict, preserving labelled
    '^X' custom elements. Uses ``parse_composition`` (which handles parentheses and
    folds explicit isotopes into their base element) but returns a plain dict so
    that count arithmetic does not go through pyteomics' element re-parsing, which
    rejects the bare '^N' symbol."""
    return dict(parse_composition(formula))


def _combine_counts(
    compound_counts: dict[str, int], mechanism_counts: dict[str, int], *, add: bool
) -> dict[str, int]:
    """Add or subtract element counts, dropping symbols that reach zero."""
    combined = dict(compound_counts)
    for symbol, count in mechanism_counts.items():
        combined[symbol] = combined.get(symbol, 0) + (count if add else -count)
    return {symbol: count for symbol, count in combined.items() if count > 0}


def _subtract(composition: dict[str, int], formula: str, what: str) -> dict[str, int]:
    """Take a formula's atoms off a composition, dropping symbols that reach
    zero.

    :raises SkipIonizationMechanism: The composition lacks an atom the formula
        takes off.
    """
    taken = _composition_counts(formula)
    for element, count in taken.items():
        # Check if element to be subtracted exists in compound composition
        if element not in composition:
            raise SkipIonizationMechanism(
                f"Element {element} from {what} {to_hill_notation(taken)} "
                f"not in compound formula {to_hill_notation(composition)}"
            )
        # Check if there are enough atoms of the element to be subtracted
        if count > composition[element]:
            raise SkipIonizationMechanism(
                f"Cannot subtract {count} of element {element} from compound formula "
                f"{to_hill_notation(composition)}"
            )
    return _combine_counts(composition, taken, add=False)


def _get_compound_composition(
    target_compound_formula: str, row: AdductRow
) -> dict[str, int] | None:
    """Get the composition of the molecules the ion holds, handling special
    cases.

    :param target_compound_formula: Target compound formula string
    :type target_compound_formula: str
    :param row: The ionization mechanism, read (:func:`_read_mechanism`)
    :type row: AdductRow
    :raises SkipIonizationMechanism: If the ionization mechanism cannot be applied:
        - Electron transfer on empty formula
        - Abstraction from empty formula
        - A multimer or a neutral loss of the empty formula
        - Not enough atoms of a subtracted element
    :return: The composition of the ion's ``multimer_n`` molecules as
        ``{symbol: count}``, or None for empty "()"
    :rtype: dict[str, int] | None
    """
    runtime.logger.debug(
        f"Processing compound formula '{target_compound_formula}' with ionization mechanism '{row.display}'"
    )
    electron_transfer = not row.species
    subtraction = row.operation == "remove" and not electron_transfer

    # Handle the special case when generating ions for empty formula "()"
    if target_compound_formula == "()":
        if electron_transfer:
            # Electron transfer does not apply
            raise SkipIonizationMechanism(
                "Electron transfer does not apply to empty formula"
            )
        if subtraction:
            # Cannot subtract from empty formula
            raise SkipIonizationMechanism(
                "Subtraction mechanisms do not apply to empty formula"
            )
        if row.multimer_n > 1 or row.neutral_loss:
            # The empty formula is the reagent ion alone, of no molecule.
            raise SkipIonizationMechanism(
                "Multimers and neutral losses do not apply to empty formula"
            )
        return None

    compound_composition = {
        symbol: count * row.multimer_n
        for symbol, count in _composition_counts(target_compound_formula).items()
    }

    if subtraction:
        # For subtraction mechanisms, ensure the compound composition can support it
        _subtract(compound_composition, row.species, "mechanism formula")

    return compound_composition


def _get_raw_ion(
    row: AdductRow, compound_composition: dict[str, int] | None
) -> tuple[dict[str, int], int]:
    """Get the ion composition and charge for a mechanism and compound composition.

    :param row: The ionization mechanism, read (:func:`_read_mechanism`)
    :type row: AdductRow
    :param compound_composition: The composition of the ion's molecules
        (None for empty "()")
    :type compound_composition: dict[str, int] | None
    :raises SkipIonizationMechanism: If the resulting ion has no atoms, or
        lacks an atom its neutral loss takes off.
    :return: 2-tuple of (ion composition as ``{symbol: count}``, the ion's
        charge, which the mechanism states)
    :rtype: tuple[dict[str, int], int]
    """
    mechanism_composition = _composition_counts(row.species)

    if row.operation == "add" or not row.species:
        # The moiety is added; electron transfer moves nothing but an electron,
        # so the ion keeps the compound's atoms whichever way it went.
        if compound_composition is None:
            # Special case: empty formula "()"
            ion_composition = mechanism_composition
        else:
            ion_composition = _combine_counts(
                compound_composition, mechanism_composition, add=True
            )
    else:
        ion_composition = _combine_counts(
            compound_composition, mechanism_composition, add=False
        )

    if row.neutral_loss:
        # Lost from the ion the species made: [M+H-H2O]+ needs the water in
        # the protonated molecule, not in the reagent.
        ion_composition = _subtract(ion_composition, row.neutral_loss, "neutral loss")

    if not ion_composition:
        # E.g. an empty-modification mechanism on the empty compound "()", or a
        # subtraction that removes every atom. An atomless ion has no meaningful
        # formula or isotope pattern (its mass would be the electron mass alone).
        raise SkipIonizationMechanism(
            f"Mechanism {row.display} yields an ion with no atoms"
        )

    return ion_composition, row.ion_charge


def predict_isotopes(
    ion_composition: dict[str, int], ion_charge: int
) -> tuple[list[float], list[float], list[str]]:
    """An ion's fine structure, one line per isotopologue, as the library stores it.

    The line model's predictor (``mascope_tools.composition.line_model``), the
    one every reader of an ion's isotope lines predicts by, to the target
    library's depth (``TARGET_LIBRARY_ABUNDANCE_FLOOR``). A labelled reagent
    atom ('^N') is read at the purity in effect, the run's setup's inside a run.
    Each line is named by its isotopologue's formula, every heavy isotope
    written out (:func:`isotopologue_formula`).

    :param ion_composition: Composition of the ion as ``{symbol: count}`` (may
        contain '^X' custom elements)
    :type ion_composition: dict[str, int]
    :param ion_charge: Ion charge (e.g. +1, -1)
    :type ion_charge: int
    :raises UnknownCustomElement: If a custom element is unknown.
    :return: 3-tuple lists of (m/z values, abundances, isotope formulae)
    :rtype: tuple[list[float], list[float], list[str]]
    """
    for symbol, count in ion_composition.items():
        if symbol.startswith("^") and count > 0 and symbol not in CUSTOM_ELEMENTS:
            raise UnknownCustomElement(symbol)
    composition = {symbol: count for symbol, count in ion_composition.items() if count}
    ion_formula = to_hill_notation(composition)
    masses, probs, labels = predict_fine_lines(
        ion_formula, ion_charge, None, TARGET_LIBRARY_ABUNDANCE_FLOOR
    )
    charge_str = charge_string(ion_charge)
    return (
        [float(mass) for mass in masses],
        [float(prob) for prob in probs],
        [isotopologue_formula(composition, label) + charge_str for label in labels],
    )


def isotopologue_formula(composition: dict[str, int], label: str) -> str:
    """One isotopologue's formula from the ion's composition and its line's label.

    The label is the line model's: ``M0`` for the ion itself, ``13C`` or
    ``13C2+18O`` for natural substitutions, and for a labelled reagent atom
    ('^N') the atoms of it left at the light isotope (``14N``, ``14N2``), every
    other one at the labelled isotope. The natural part is written as the
    envelope's rows write it (``replace_atom_with_isotope``), the labelled
    atoms before it, and the heavy isotopes then lead the formula:
    ``[13C]C5H12O6``, ``[15N]2C2H4O``, ``[15N]NC2H4O``.

    :param composition: The ion's composition, '^X' atoms included.
    :param label: The line's label.
    :return: The isotopologue's formula, no charge.
    """
    customs = {
        symbol: count for symbol, count in composition.items() if symbol.startswith("^")
    }
    base = to_hill_notation(
        {
            symbol: count
            for symbol, count in composition.items()
            if symbol not in customs
        }
    )
    light = {
        f"{CUSTOM_ELEMENTS[symbol].isotopes[0][1]}"
        f"{CUSTOM_ELEMENTS[symbol].base_element}": symbol
        for symbol in customs
    }
    light_counts = dict.fromkeys(customs, 0)
    natural = []
    for part in [] if label in ("M0", "", "---") else label.split("+"):
        match = re.fullmatch(r"(\d+[A-Z][a-z]?)(\d*)", part)
        if match is not None and match.group(1) in light:
            light_counts[light[match.group(1)]] += int(match.group(2) or 1)
        else:
            natural.append(part)
    base_formula = replace_atom_with_isotope(base, "+".join(natural)) if base else ""
    if not customs:
        return base_formula
    labelled = ""
    for symbol, count in customs.items():
        element = CUSTOM_ELEMENTS[symbol]
        heavy = count - light_counts[symbol]
        lighter = light_counts[symbol]
        if heavy:
            labelled += f"[{element.isotopes[-1][1]}{element.base_element}]"
            labelled += str(heavy) if heavy > 1 else ""
        if lighter:
            labelled += element.base_element + (str(lighter) if lighter > 1 else "")
    raw = labelled + base_formula
    return _reorder_isotopes_first(to_hill_notation(parse_formula_tokens(raw)))


def _reorder_isotopes_first(formula: str) -> str:
    """Reorder formula to put isotope labels (e.g., [15N], [18O]) at the beginning.

    :param formula: Chemical formula string
    :return: Formula with isotope labels moved to the front

    Examples
    --------
    >>> _reorder_isotopes_first("HN[15N]O6")
    '[15N]HNO6'
    >>> _reorder_isotopes_first("[18O]C2H4[15N]")
    '[18O][15N]C2H4'
    >>> _reorder_isotopes_first("C6H12O6")
    'C6H12O6'
    >>> _reorder_isotopes_first("")
    ''
    """
    if not formula or "[" not in formula:
        return formula

    # Extract all isotope labels like [15N], [18O], [2H], [15N]2, etc.
    isotope_pattern = r"\[\d+[A-Za-z]+\]\d*"
    isotopes = re.findall(isotope_pattern, formula)
    remaining = re.sub(isotope_pattern, "", formula)

    return "".join(isotopes) + remaining


def fit_isotope_names(
    names: Sequence[str], limit: int, abundances: Sequence[float] | None = None
) -> str:
    """Join the names of the isotopologues one line merges, within a column.

    At a low resolution one line merges every isotopologue in its bin, and for
    a large ion carrying bromine or chlorine that is hundreds of names - past
    the column they are stored in, where one row too long fails the insert of
    everything written with it. The label tells which isotopologues make up
    the line, so whole names are taken, the most abundant first, until the
    next one does not fit, and are joined in their own order. When not even
    the first fits, it is cut to the limit.

    :param names: The isotopologues' names, in the order the label lists them.
    :param limit: The most characters the label may hold.
    :param abundances: The isotopologues' abundances, to take the most
        abundant first; without them, names are taken from the front.
    :return: The names joined by "/", at most ``limit`` long.
    """
    joined = "/".join(names)
    if len(joined) <= limit:
        return joined
    order = range(len(names))
    if abundances is not None:
        # Stable, so names of equal abundance are taken in their own order.
        order = sorted(order, key=lambda index: -abundances[index])
    kept = []
    length = -1  # no separator before the first name
    for index in order:
        length += len(names[index]) + 1
        if length > limit:
            break
        kept.append(index)
    if not kept:
        return names[order[0]][:limit]
    return "/".join(names[index] for index in sorted(kept))


def group_target_isotopes(
    masses: list,
    probs: list,
    formulae: list,
    resolution: float | Callable[[float], float],
) -> tuple[list, list, list]:
    """The lines a file of this resolving power shows, from an ion's fine lines.

    The line model's resolution (``line_model.resolve_lines``), the one the
    envelope match predicts by: one peak holds, from the most abundant line
    not yet taken, every line within half a FWHM of it (mascope#2587); lines
    under the envelope's floor less than one FWHM apart that together clear it
    are one line (mascope#3163); a line still under it less than one FWHM from a
    line over it is added to that line (mascope#2591). The library keeps "all"
    lines, so a line under the floor that no line took stays a line of its own.

    The isotope formulae are concatenated with "/" separator for all isotopes
    that fall within the same line, in m/z order, the most abundant first up
    to what the column holds (:func:`fit_isotope_names`). The line's m/z and
    abundance count every isotope in it, named or not.

    :param masses: The fine lines' m/z.
    :param probs: Their abundances.
    :param formulae: Their isotopologue formulae.
    :param resolution: The resolving power, a number or a function of m/z.
    :return: The lines' m/z, abundances and joined formulae, in m/z order.
    """
    lines = _resolved_target_lines(masses, probs, formulae, resolution)
    return (
        [line[0] for line in lines],
        [line[1] for line in lines],
        [line[2] for line in lines],
    )


def _resolved_target_lines(
    masses, probs, formulae, resolution: float | Callable[[float], float]
) -> list[tuple[float, float, str, str]]:
    """:func:`group_target_isotopes`' lines, each with the formula of the
    member that leads it: its brightest, which the line is read as.

    :return: Per line, in m/z order, its m/z, abundance, joined formulae and
        leading member's formula.
    """
    # Sorted by m/z, so each line lists its names in m/z order
    mz = np.array(masses, dtype=float)
    order = np.argsort(mz, kind="stable")
    mz = mz[order]
    intensity = np.array(probs, dtype=float)[order]
    formula = np.array(formulae, dtype=object)[order]
    if not mz.size:
        return []
    power = resolution if callable(resolution) else (lambda _, r=resolution: r)
    lines = [
        (
            line.mz,
            line.abundance,
            fit_isotope_names(
                formula[list(line.members)].tolist(),
                TARGET_ISOTOPE_FORMULA_LENGTH,
                intensity[list(line.members)],
            ),
            str(formula[line.members[int(np.argmax(intensity[list(line.members)]))]]),
        )
        for line in resolve_lines(
            mz,
            intensity,
            formula.tolist(),
            power,
            ENVELOPE_ABUNDANCE_FLOOR,
            keep_under_floor=True,
        )
    ]
    lines.sort(key=lambda line: line[0])
    return lines


def ion_lines(
    ion_composition: dict[str, int],
    ion_charge: int,
    resolution: Callable[[float], float] | None,
) -> tuple[list, list, list]:
    """An ion's lines as a file of this resolving power shows them.

    The fine structure (:func:`predict_isotopes`) resolved at the file's
    R(m/z) (:func:`group_target_isotopes`), what the peak assignment's Stage
    A, its seeded chain and the composition chart match against, so a target
    ion's line and a search candidate's are the same line. Without a
    resolution, the fine structure itself.

    :param ion_composition: The ion's composition.
    :param ion_charge: Its charge.
    :param resolution: The file's resolving power as a function of m/z, or None.
    :return: The lines' m/z, abundances and formulae.
    """
    fine = predict_isotopes(ion_composition, ion_charge)
    if resolution is None:
        return fine
    return group_target_isotopes(*fine, resolution)


def lines_at_file(
    fine: pd.DataFrame,
    file_lines: FileLines,
    abundance_threshold: float | str = 0.0,
) -> pd.DataFrame:
    """A frame of ions' fine lines as one file shows them.

    Each ion's fine structure (the stored library's ``HIGH`` rows, or the same
    lines predicted in memory) is resolved at the file's R(m/z)
    (:func:`group_target_isotopes`), the way the envelope match predicts a
    candidate's lines, and the lines under the abundance threshold are left
    out after the merge, so a line's share is what its peak holds. A resolved
    line takes the id of its first member and the file's resolution type;
    every other column is the ion's. The ions come out in the order they
    first appear in ``fine``, each one's lines by m/z, whatever their ids.

    :param fine: One row per fine line: ``target_ion_id``,
        ``target_isotope_id``, ``target_isotope_formula``, ``mz``,
        ``relative_abundance``, and the ion's own columns.
    :param file_lines: The file whose lines to resolve.
    :param abundance_threshold: The least abundance a line keeps, or the name
        of the column holding each ion's own.
    :return: The frame of the file's lines, its columns the input's and
        :data:`LEAD_ISOTOPE_FORMULA`.
    """
    if fine.empty:
        return fine.assign(
            resolution=file_lines.resolution_type, **{LEAD_ISOTOPE_FORMULA: None}
        )
    power = file_lines.resolving_power()
    if power is None:
        # Each fine line is a line of its own, and leads itself.
        lines = fine.assign(
            resolution=file_lines.resolution_type,
            **{LEAD_ISOTOPE_FORMULA: fine.get("target_isotope_formula")},
        )
    else:
        # One ion's fine lines together, by m/z; the ion's own columns are
        # copied from its first line, by position. The ions keep the order
        # they first appear in, not their ids': a reference ion's id is minted
        # at random per process, so sorting by it would order two runs of one
        # sample differently (#3351).
        ordinal = pd.factorize(fine["target_ion_id"])[0]
        order = np.lexsort((fine["mz"].to_numpy(dtype=float), ordinal))
        ordered = fine.iloc[order].reset_index(drop=True)
        ions = ordered["target_ion_id"].to_numpy()
        starts = np.flatnonzero(np.r_[True, ions[1:] != ions[:-1]])
        ends = np.r_[starts[1:], ions.size]
        mzs = ordered["mz"].to_numpy(dtype=float)
        abundances = ordered["relative_abundance"].to_numpy(dtype=float)
        formulae = ordered["target_isotope_formula"].to_numpy(dtype=object)
        isotope_ids = ordered["target_isotope_id"].to_numpy(dtype=object)
        first, line_ids, line_mzs, line_abundances, names = [], [], [], [], []
        leads = []
        for start, end in zip(starts, ends):
            resolved = _resolved_target_lines(
                mzs[start:end], abundances[start:end], formulae[start:end], power
            )
            first.extend([start] * len(resolved))
            line_ids.extend(f"{isotope_ids[start]}.{k}" for k in range(len(resolved)))
            for mz, abundance, joined, lead in resolved:
                line_mzs.append(mz)
                line_abundances.append(abundance)
                names.append(joined)
                leads.append(lead)
        lines = ordered.iloc[first].reset_index(drop=True)
        lines["target_isotope_id"] = line_ids
        lines["target_isotope_formula"] = names
        lines[LEAD_ISOTOPE_FORMULA] = leads
        lines["mz"] = np.asarray(line_mzs, dtype=float)
        lines["relative_abundance"] = np.asarray(line_abundances, dtype=float)
        lines = lines.assign(resolution=file_lines.resolution_type)
    threshold = (
        lines[abundance_threshold]
        if isinstance(abundance_threshold, str)
        else abundance_threshold
    )
    return lines[lines["relative_abundance"] >= threshold].reset_index(drop=True)
