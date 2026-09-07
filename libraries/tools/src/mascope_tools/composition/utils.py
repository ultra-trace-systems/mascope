import re

from pyteomics.mass import Composition, calculate_mass

from mascope_tools.composition.config import ELECTRON_MASS
from mascope_tools.composition.custom_elements import CUSTOM_ELEMENTS
from mascope_tools.composition.exceptions import CompositionFinderException
from mascope_tools.composition.models import (
    Atom,
    IonizationMechanism,
)


def to_pyteomics(formula: str) -> str:
    """Convert bracket-first isotope notation to Pyteomics element-first.
    e.g. '[15N]O3' -> 'N[15]O3'
    """
    return re.sub(r"\[(\d+)([A-Z][a-z]?)\]", r"\2[\1]", formula)


# Caret-prefixed heavy isotopes used for labelled reagents, e.g. '^N' = 15N (the
# 15N-labelled nitrate reagent '+^NO3-'). pyteomics masses isotopes via 'N[15]'
# notation and cannot mass the bare '^N' symbol, so map them for mass computation.
# Derived from the single custom-element registry (custom_elements.py).
CARET_ISOTOPES = {sym: ce.pyteomics_isotope for sym, ce in CUSTOM_ELEMENTS.items()}


def composition_mass(composition: Composition) -> float:
    """Monoisotopic mass of a parsed Composition, resolving caret-labelled heavy
    isotopes ('^N' = 15N) that pyteomics cannot mass directly. Equivalent to
    ``composition.mass()`` for compositions with no caret isotopes."""
    total = 0.0
    for sym, n in composition.items():
        total += calculate_mass(formula=CARET_ISOTOPES.get(sym, sym)) * n
    return total


def combine_formula_and_ionization(
    formula: str, ionization_mechanism: IonizationMechanism
) -> str:
    """
    Combine a neutral formula and ionization into a single ion formula in Hill notation.
    """
    # Parse formula (Pyteomics requires element-first notation)
    comp_formula = Composition(formula=to_pyteomics(formula))
    # Parse the adduct with parse_composition (not raw pyteomics) so custom
    # labelled elements like '^N' (the 15N-nitrate reagent) survive; pyteomics
    # rejects the bare '^N' symbol.
    comp_ionization = (
        parse_composition(ionization_mechanism.formula)
        if ionization_mechanism
        else Composition(formula="")
    )
    if ionization_mechanism.addition:
        combined_composition = comp_formula + comp_ionization
    else:
        combined_composition = comp_formula - comp_ionization

    charge_sign = (
        "+" if ionization_mechanism and ionization_mechanism.charge > 0 else "-"
    )
    ion_formula = to_hill_order(combined_composition) + charge_sign
    return ion_formula


def parse_composition(formula_string: str, multiplier: int = 1) -> Composition:
    """Recursevely parses formulas like "(CH3CH2)2NH", "((CH3CH2)2NH)H", "(C6H10O2)H", "CH4N2OH"
    into pyteomics.Composition

    Examples
    --------
    >>> parse_composition("(CH3CH2)2NH", 1)
    Composition({'C': 4, 'H': 11, 'N': 1})
    >>> parse_composition("((CH3CH2)2NH)H", 1)
    Composition({'C': 4, 'H': 12, 'N': 1})
    >>> parse_composition("(C6H12O6)H", 1)
    Composition({'C': 6, 'H': 13, 'O': 6})
    >>> parse_composition("CH4N2OH", 1)
    Composition({'C': 1, 'H': 5, 'N': 2, 'O': 1})
    >>> parse_composition("HN^NO6", 1)
    Composition({'H': 1, 'N': 1, '^N': 1, 'O': 6})
    >>> parse_composition("[18O]C2H4^N", 1)
    Composition({'O': 1, 'C': 2, 'H': 4, '^N': 1})
    >>> parse_composition("H2O", 2)
    Composition({'H': 4, 'O': 2})
    >>> parse_composition("(H2O)", 2)
    Composition({'H': 4, 'O': 2})
    >>> parse_composition("(H2O)2", 1)
    Composition({'H': 4, 'O': 2})
    >>> parse_composition("((H2O)2)2", 1)
    Composition({'H': 8, 'O': 4})
    >>> parse_composition("^NO3", 1)
    Composition({'^N': 1, 'O': 3})
    >>> parse_composition("HHCCOO", 1)
    Composition({'H': 2, 'C': 2, 'O': 2})
    >>> parse_composition("", 1)
    Composition({})

    :param formula_string: String containing the formula to parse.
    :type formula_string: str
    :param multiplier: Multiplier after brackets, defaults to 1
    :type multiplier: int, optional
    :return: Parsed composition as a pyteomics.Composition object.
    :rtype: Composition
    """
    elements = Composition(formula="")
    s = formula_string
    while "(" in s:
        # Find the first '(' and its matching ')'
        open_idx = s.find("(")
        depth = 1
        close_idx = open_idx + 1
        while close_idx < len(s) and depth > 0:
            if s[close_idx] == "(":
                depth += 1
            elif s[close_idx] == ")":
                depth -= 1
            close_idx += 1
        if depth != 0:
            # Unmatched parenthesis, skip
            break
        # Extract group and multiplier
        group = s[open_idx + 1 : close_idx - 1]
        # Find multiplier after group
        mult_str = ""
        idx = close_idx
        while idx < len(s) and s[idx].isdigit():
            mult_str += s[idx]
            idx += 1
        group_mult = int(mult_str) if mult_str else 1
        before = s[:open_idx]
        after = s[idx:]
        elements += parse_composition(before, multiplier)
        elements += parse_composition(group, group_mult * multiplier)
        s = after
    # Parse remaining string (elements outside brackets)
    i = 0
    while i < len(s):
        m = re.match(r"(\^?[A-Z][a-z]?)(\d*)", s[i:])
        if m:
            elem = m.group(1)
            count = int(m.group(2)) if m.group(2) else 1
            elements[elem] += count * multiplier
            i += len(m.group(0))
        else:
            i += 1
    return elements


_VALID_FORMULA_CHARS = re.compile(r"[A-Za-z0-9()\[\]^]*")


def assert_valid_formula(formula: str) -> None:
    """Validate a chemical formula string, raising ``ValueError`` on anything invalid.

    Accepts element symbols, parenthesis groups, bracket isotopes (``[15N]``) and
    labelled ``^X`` custom elements. An empty string and ``"()"`` (adduct-only) are
    valid. Unlike :func:`parse_composition` -- which silently skips characters it
    does not recognise -- this raises on invalid characters, unbalanced brackets,
    and unknown element symbols.

    Examples
    --------
    >>> assert_valid_formula("C6H12O6")
    >>> assert_valid_formula("^NO3")
    >>> assert_valid_formula("(CH4N2O)H")
    >>> assert_valid_formula("()")
    >>> assert_valid_formula("H2O!")
    Traceback (most recent call last):
        ...
    ValueError: Formula 'H2O!' contains invalid characters.
    >>> assert_valid_formula("Zz")
    Traceback (most recent call last):
        ...
    ValueError: Formula 'Zz' contains an unknown element or is not a valid chemical formula.
    """
    stripped = formula.strip()
    if stripped in ("", "()"):
        return
    if not _VALID_FORMULA_CHARS.fullmatch(stripped):
        raise ValueError(f"Formula '{formula}' contains invalid characters.")
    if stripped.count("(") != stripped.count(")") or stripped.count(
        "["
    ) != stripped.count("]"):
        raise ValueError(f"Formula '{formula}' has unbalanced brackets.")
    composition = parse_composition(stripped)
    if not composition:
        raise ValueError(f"Formula '{formula}' contains no recognisable elements.")
    try:
        # composition_mass masses every symbol (mapping labelled '^X'), so an
        # unknown element symbol raises here.
        composition_mass(composition)
    except ValueError:
        raise
    except Exception as exc:  # pyteomics raises its own error type
        raise ValueError(
            f"Formula '{formula}' contains an unknown element or is not a "
            f"valid chemical formula."
        ) from exc


def to_hill_order(elements: dict) -> str:
    """Convert a dictionary of elements to Hill notation string."""
    # For empty formula, return '()'
    if not elements:
        return "()"
    # Filter out zero and negative counts (can be if -H- is the ionization mechanism)
    elements = {k: v for k, v in elements.items() if v > 0}

    def normalize_symbol(symbol: str) -> str:
        # Accept both [15N] and N[15] and canonicalize to [15N].
        bracket_first = re.fullmatch(r"\[(\d+)([A-Z][a-z]?)\]", symbol)
        if bracket_first:
            return symbol

        element_first = re.fullmatch(r"([A-Z][a-z]?)\[(\d+)\]", symbol)
        if element_first:
            element, mass_num = element_first.groups()
            return f"[{mass_num}{element}]"

        return symbol

    normalized_elements = {}
    for symbol, count in elements.items():
        normalized_symbol = normalize_symbol(symbol)
        normalized_elements[normalized_symbol] = (
            normalized_elements.get(normalized_symbol, 0) + count
        )

    def hill_sort_key(symbol: str) -> tuple[int, str, int, int, str]:
        bracket_match = re.fullmatch(r"\[(\d+)([A-Z][a-z]?)\]", symbol)
        if bracket_match:
            mass_num, element = bracket_match.groups()
            priority = 0 if element == "C" else 1 if element == "H" else 2
            return (priority, element, 0, int(mass_num), symbol)

        plain_match = re.fullmatch(r"([A-Z][a-z]?)", symbol)
        if plain_match:
            element = plain_match.group(1)
            priority = 0 if element == "C" else 1 if element == "H" else 2
            return (priority, element, 1, 0, symbol)

        return (3, symbol, 1, 0, symbol)

    atomic_symbols = sorted(normalized_elements.keys(), key=hill_sort_key)
    formula = "".join(
        f"{symbol}{normalized_elements[symbol] if normalized_elements[symbol] > 1 else ''}"
        for symbol in atomic_symbols
    )
    formula = remove_ones_from_formula(formula)
    return formula


def remove_ones_from_formula(formula: str) -> str:
    formula = re.sub(r"([A-Za-z]+)1(?![0-9])", r"\1", formula)
    return formula


# Token: a bracket isotope ('[15N]'), a caret custom element ('^N'), or a plain
# element ('C', 'Br'), each with an optional trailing count.
_FORMULA_TOKEN = re.compile(r"(\[\d+[A-Z][a-z]?\]|\^?[A-Z][a-z]?)(\d*)")


def parse_formula_tokens(formula: str) -> dict[str, int]:
    """Parse a flat (no-parenthesis) formula into ``{symbol: count}``, preserving
    bracket isotopes (``[15N]``) and caret custom elements (``^N``) as distinct
    symbols. Unlike :func:`parse_composition` this keeps isotope/custom tokens
    verbatim (it does not fold them into pyteomics element notation), which is
    what :func:`to_hill_notation` needs to emit exact formula strings.

    Examples
    --------
    >>> parse_formula_tokens("C6H12O6")
    {'C': 6, 'H': 12, 'O': 6}
    >>> parse_formula_tokens("[15N]BrHO3")
    {'[15N]': 1, 'Br': 1, 'H': 1, 'O': 3}
    >>> parse_formula_tokens("HN^NO6")
    {'H': 1, 'N': 1, '^N': 1, 'O': 6}
    """
    counts: dict[str, int] = {}
    for symbol, count in _FORMULA_TOKEN.findall(formula):
        counts[symbol] = counts.get(symbol, 0) + (int(count) if count else 1)
    return counts


def _hill_base_and_isotope(symbol: str) -> tuple[str, int, int]:
    """Return ``(base_element, is_isotope, mass_number)`` for a formula symbol.

    A bracket isotope ``[15N]`` -> ``("N", 1, 15)``; a plain or caret element
    ``C`` / ``^N`` -> ``(symbol, 0, 0)``.
    """
    m = re.fullmatch(r"\[(\d+)([A-Z][a-z]?)\]", symbol)
    if m:
        return m.group(2), 1, int(m.group(1))
    return symbol, 0, 0


def to_hill_notation(counts: dict[str, int]) -> str:
    """Format ``{symbol: count}`` in classic Hill order.

    Classic Hill (as used by molmass, and by Mascope for the ``target_ion_formula``
    / ``target_isotope_formula`` strings persisted for target ions): if carbon is
    present, list C then H then the remaining elements alphabetically; if no carbon
    is present, list *all* elements (including H) alphabetically. A bracket isotope
    ``[mX]`` sorts as element ``X`` (plain ``X`` before its isotopes, isotopes by
    ascending mass number); a caret custom element ``^X`` sorts by its raw symbol
    (so ``^N`` follows the regular elements).

    NOTE: this differs from :func:`to_hill_order`, which always places H second
    even when no carbon is present. ``to_hill_order`` is kept for the composition
    finder / scoring paths that rely on that convention; ``to_hill_notation`` is
    the molmass-compatible ordering used where exact stored formula strings matter.
    """
    counts = {k: v for k, v in counts.items() if v > 0}
    if not counts:
        return ""
    has_carbon = any(_hill_base_and_isotope(s)[0] == "C" for s in counts)

    def sort_key(symbol: str) -> tuple:
        base, is_isotope, mass_number = _hill_base_and_isotope(symbol)
        if has_carbon and base == "C":
            group: tuple = (0, "")
        elif has_carbon and base == "H":
            group = (1, "")
        else:
            group = (2, base)
        return (group, is_isotope, mass_number, symbol)

    parts = []
    for symbol in sorted(counts, key=sort_key):
        n = counts[symbol]
        parts.append(symbol if n == 1 else f"{symbol}{n}")
    return "".join(parts)


_BRACKET_ISOTOPE = re.compile(r"\[(\d+)([A-Z][a-z]?)\]")

#: Bracketed isotope token -> the caret custom element it denotes, for every
#: labelled element the finder knows: "[15N]" -> "^N".
_CARET_BY_BRACKET = {
    f"[{ce.labelled_massnumber}{ce.base_element}]": symbol
    for symbol, ce in CUSTOM_ELEMENTS.items()
}


def _caret_labelled(moiety: str) -> str:
    """Rewrite bracketed labelled isotopes in a mechanism's moiety to caret form.

    A mechanism reaches the finder in explicit-isotope notation ("+[15N]O3-"),
    but `parse_composition` reads a bracketed token as its base element - "[15N]"
    as N - and the label is lost: the labelled nitrate reagent was massed as the
    unlabelled one, 0.997 Da light, and every candidate on that channel was a
    formula fitting the wrong adduct mass. The caret form ("^N") is what the
    custom-element machinery masses and predicts correctly, so the token is
    rewritten to it; a bracketed isotope with no custom element is refused
    rather than silently unlabelled.

    :param moiety: The mechanism's moiety, e.g. "[15N]O3" or "(CH4N2O)H".
    :raises CompositionFinderException: A bracketed isotope the finder cannot
        mass.
    :return: The moiety with labelled isotopes in caret notation.
    """

    def replace(match: re.Match) -> str:
        token = match.group(0)
        symbol = _CARET_BY_BRACKET.get(token)
        if symbol is None:
            raise CompositionFinderException(
                f"Unsupported labelled isotope {token!r} in ionization mechanism: "
                f"the finder can mass only {sorted(_CARET_BY_BRACKET)}"
            )
        return symbol

    return _BRACKET_ISOTOPE.sub(replace, moiety)


def parse_ionization(ionization_string: str) -> IonizationMechanism:
    """Parse ionization mechanism string from Mascope format into an IonizationMechanism object.

    :param ionization_string: String representing the ionization mechanism.
    :type ionization_string: str
    :raises CompositionFinderException: If the ionization is unsupported.
    :return: Parsed IonizationMechanism object.
    :rtype: IonizationMechanism
    """
    ionization_string = ionization_string.strip()
    formula = ""
    mass = ELECTRON_MASS
    if ionization_string == "+":
        # Abstract electron being kicked out
        addition = False
        charge = 1
    elif ionization_string == "-":
        # Abstract electron being added
        addition = True
        charge = -1
    elif ionization_string == "-H-":
        # Deprotonation
        addition = False
        formula = "H"
        charge = -1
        mass = calculate_mass(formula="H")
    else:
        # Regex pattern: start charge, base, end charge
        pattern = r"^([+-])?(.*?)([+-])?$"

        match = re.match(pattern, ionization_string)
        if match:
            addition = match.group(1) == "+"
            composition = parse_composition(_caret_labelled(match.group(2)))
            formula = to_hill_order(composition)
            # The trailing sign is the charge of the MOIETY that is added or
            # removed - the proton in "+H+" and "-H+", the bromide in "+Br-" -
            # so the moiety's mass accounts for that charge's electron. The
            # ION's charge follows from what was done with the moiety: adding
            # a cation or removing an anion leaves a positive ion, removing a
            # cation or adding an anion a negative one. "-H+" is deprotonation,
            # and the ion it leaves is the anion: one electron heavier than the
            # cation the trailing sign used to be read as, which put every
            # deprotonated candidate's predicted M0 two electron masses light.
            moiety_charge = 1 if match.group(3) == "+" else -1
            charge = moiety_charge if addition else -moiety_charge
            mass = composition_mass(composition) - ELECTRON_MASS * moiety_charge
        else:
            raise CompositionFinderException(
                f"Unsupported ionization mechanism: '{ionization_string}'"
            )

    ionization_mech = IonizationMechanism(
        mascope_notation=ionization_string,
        addition=addition,
        formula=formula,
        mass=mass,
        charge=charge,
    )

    return ionization_mech


def parse_bool(val):
    """Parse a value into a boolean."""
    return str(val).lower() in ("1", "true", "yes", "on")


def parse_atom_count_ranges(count_ranges: str) -> list[Atom]:
    """Parse a string of element count ranges into a list of Atom objects.

    :param count_ranges: String containing element count ranges.
        e.g. "C0-30 H0-40 N0-3 O0-20 [18O]0-1 [13C]0-2"
    :type count_ranges: str
    :return: List of Atom objects.
    :rtype: list[Atom]
    """
    standard_pattern = re.compile(r"^([A-Z][a-z]?)(\d+)-(\d+)$")
    isotope_pattern = re.compile(r"^\[(\d+)([A-Z][a-z]?)\](\d+)-(\d+)$")
    legacy_isotope_pattern = re.compile(r"^[A-Z][a-z]?\[\d+\]\d+-\d+$")

    atoms = []
    tokens = count_ranges.split()
    for token in tokens:
        match = standard_pattern.fullmatch(token)
        if match:
            element, min_count, max_count = match.groups()
            symbol = element
            mass_formula = symbol
        else:
            match = isotope_pattern.fullmatch(token)
            if match:
                mass_number, element, min_count, max_count = match.groups()
                symbol = f"[{mass_number}{element}]"
                mass_formula = f"{element}[{mass_number}]"
            else:
                if legacy_isotope_pattern.fullmatch(token):
                    raise CompositionFinderException(
                        (
                            f"Invalid isotope format '{token}'. "
                            "Use bracket-first notation like '[15N]0-1'."
                        )
                    )
                raise CompositionFinderException(
                    f"Invalid element count range token '{token}'."
                )

        min_count_i = int(min_count)
        max_count_i = int(max_count)
        if min_count_i > max_count_i:
            raise CompositionFinderException(
                f"Invalid range '{token}': min count cannot exceed max count."
            )

        atoms.append(
            Atom(
                symbol=symbol,
                min_count=min_count_i,
                max_count=max_count_i,
                mass=calculate_mass(formula=mass_formula, charge=0),
            )
        )

    return atoms


def normalize_formula_with_isotopes(formula: str) -> str:
    """
    Normalize a chemical formula by removing explicit isotope notations.

    NOTE: This function does not combine isotopes with their non-isotopic counterparts,
    it simply removes the isotope brackets. Use `parse_composition` to get a combined composition
    if needed.

    Examples
    --------
    >>> normalize_formula_with_isotopes("HN^NO6")
    'HN^NO6'
    >>> normalize_formula_with_isotopes("[18O]C2H4^N")
    'OC2H4^N'
    >>> normalize_formula_with_isotopes("[18O]2C2H4^N")
    'O2C2H4^N'
    >>> normalize_formula_with_isotopes("C6H12O6")
    'C6H12O6'
    >>> normalize_formula_with_isotopes("[13C]C5[2H]H11[18O]O5")
    'CC5HH11OO5'
    >>> normalize_formula_with_isotopes("")
    ''

    :param formula: Chemical formula string, e.g. "[13C]H4[18O]"
    :type formula: str
    :return: Normalized formula string, e.g. "CH4O"
    :rtype: str
    """
    normalized_formula = re.sub(r"\[\d+([A-Za-z]+)\]", r"\1", formula)
    normalized_formula = re.sub(r"([A-Z][a-z]?)\[\d+\]", r"\1", normalized_formula)
    return normalized_formula
