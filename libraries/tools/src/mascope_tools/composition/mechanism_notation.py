"""The two notations an ionization mechanism is written in, and the map between.

The **standard adduct notation** is how chemists and every other tool write an
ion: ``[M+H]+``, ``[M-H]-``, ``[M+Br]-``, ``[M+NH4]+``, ``[M-H]+``,
``[M+CH4N2O+H]+``, ``[M]+.``. The terms inside the brackets are what is added
to or removed from the neutral molecule ``M``, and the sign after them is the
ion's own charge. A bare ``[M]`` with the radical dot is electron transfer:
``[M]+.`` is an electron removed, ``[M]-.`` one attached. A labelled moiety is
written as it is (``[M+^NO3]-``). This is the notation Mascope stores and
shows.

The **legacy notation**, ``<operation><moiety><moiety charge>``, is read on
input too: ``+H+`` adds a proton, ``-H+`` removes one and leaves an anion,
``+Br-`` adds a bromide, ``-H-`` removes a hydride and leaves a cation, and a
bare ``+`` or ``-`` is electron transfer. Its trailing sign is the charge of
the moiety, not of the ion, and that is the reading everyone who meets it gets
wrong: ``-H+`` is the anion ``[M-H]-`` and ``-H-`` the cation ``[M-H]+``.

One mechanism has one spelling in each notation. Its terms are written in
alphabetical order whichever order they were typed in, so ``[M+H+CH4N2O]+``
is ``[M+CH4N2O+H]+``, and a mechanism is compared, looked up and shown by that
spelling. A parenthesised group at the front of a legacy moiety becomes a
term of its own when something follows it (``+(CH4N2O)H+`` is
``[M+CH4N2O+H]+``); a group with a multiplier stays inside the term it
multiplies (``+(CH4N2O)2H+`` is ``[M+(CH4N2O)2H]+``). A standard spelling is
stored as the legacy round trip writes it, so it survives a downgrade and an
upgrade unchanged: ``[M+(CH4N2O)H]+`` is stored as ``[M+CH4N2O+H]+``.

The map between the two notations is exact on every spelling already written
that way, and every legacy spelling the fleet stores is: it converts to a
standard one and back to itself character for character, which is what lets
the data migration be one function and its inverse. Any other spelling
converts to the one spelling of the same mechanism.

Only what both notations can say is accepted: one molecule, a single charge,
and terms that are all added or all removed. ``[2M+H]+``, ``[M+2H]2+`` and
``[M+Na-2H]-`` are refused rather than approximated.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache


#: The characters a moiety or a term may hold: element symbols and counts,
#: parenthesised groups, bracketed isotopes (``[15N]``) and caret-labelled
#: elements (``^N``). Whether the symbols are real elements is the formula
#: validator's question, not the notation's.
_FORMULA_TEXT = re.compile(r"[A-Za-z0-9()\[\]^]+")

#: One signed term of a standard spelling's inside, ``+H`` or ``-CH3``.
_SIGNED_TERM = re.compile(r"([+-])([^+-]+)")


class MechanismNotationError(ValueError):
    """A mechanism written in neither notation."""


@dataclass(frozen=True)
class MechanismParts:
    """An ionization mechanism, whichever notation it was written in.

    :param addition: Whether the moiety is added to the molecule (True) or
        removed from it. Electron transfer is an electron attached
        (``[M]-.``, an addition) or removed (``[M]+.``).
    :param moiety: What is added or removed, as the legacy notation writes it,
        its terms in order: ``"H"``, ``"NO3"``, ``"(CH4N2O)H"``. Empty for
        electron transfer.
    :param charge: The ion's charge, ``+1`` or ``-1``.
    """

    addition: bool
    moiety: str
    charge: int

    @property
    def electron_transfer(self) -> bool:
        """Whether nothing but an electron is added or removed."""
        return not self.moiety

    @property
    def moiety_charge(self) -> int:
        """The charge of what is added or removed.

        The ion's own charge where the moiety is added, the opposite where it
        is removed: removing a proton leaves an anion. The electron's -1 for
        electron transfer.
        """
        return self.charge if self.addition else -self.charge

    @property
    def polarity(self) -> str:
        """The ion's polarity, ``"+"`` or ``"-"``."""
        return "+" if self.charge > 0 else "-"

    @property
    def terms(self) -> tuple[str, ...]:
        """The moiety's terms as the standard notation writes them."""
        return _split_moiety(self.moiety)

    @property
    def standard(self) -> str:
        """The mechanism in the standard adduct notation, ``[M-H]-``."""
        if self.electron_transfer:
            return f"[M]{self.polarity}."
        operation = "+" if self.addition else "-"
        inside = "".join(operation + term for term in self.terms)
        return f"[M{inside}]{self.polarity}"

    @property
    def legacy(self) -> str:
        """The mechanism in the legacy notation, ``-H+``."""
        if self.electron_transfer:
            return self.polarity
        operation = "+" if self.addition else "-"
        return operation + self.moiety + ("+" if self.moiety_charge > 0 else "-")


def _closing_paren(text: str) -> int | None:
    """The index of the parenthesis closing the one ``text`` starts with."""
    depth = 0
    for index, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def _split_moiety(moiety: str) -> tuple[str, ...]:
    """A legacy moiety's terms: each leading group that something follows, then
    the rest as it is.

    ``(CH4N2O)H`` is ``CH4N2O`` and ``H``. A group with a multiplier, an empty
    one, or one with nothing after it stays in the term it belongs to, so
    :func:`_join_terms` gives back exactly the moiety this was given.
    """
    terms: list[str] = []
    rest = moiety
    while rest.startswith("("):
        close = _closing_paren(rest)
        if close is None or close == len(rest) - 1:
            break
        group = rest[1:close]
        if not group or group[0].isdigit() or rest[close + 1].isdigit():
            break
        terms.append(group)
        rest = rest[close + 1 :]
    if rest:
        terms.append(rest)
    return tuple(terms)


def _join_terms(terms: list[str] | tuple[str, ...]) -> str:
    """The legacy moiety of these terms: all but the last parenthesised."""
    return "".join(f"({term})" for term in terms[:-1]) + terms[-1]


def _ordered_terms(terms: Iterable[str]) -> tuple[str, ...]:
    """The terms in the order a mechanism is written in: alphabetical.

    Each term is taken as a legacy moiety splits it. Joining the sorted terms
    can put a group at the front of the last one, which the split then takes
    apart (``[M+(A)+(B)C]+`` is ``[M+(A)+B+C]+``), so the order is the one
    that splits the same way again once joined. The terms only ever split
    further, so this settles.
    """
    ordered = tuple(sorted(_split_moiety(_join_terms(tuple(terms)))))
    while (again := _split_moiety(_join_terms(ordered))) != ordered:
        ordered = tuple(sorted(again))
    return ordered


def _nests(text: str, opening: str, closing: str) -> bool:
    """Whether every ``closing`` in ``text`` closes an ``opening`` before it."""
    depth = 0
    for char in text:
        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _check_formula_text(text: str, notation: str) -> None:
    """Refuse a moiety or term that cannot be a formula at all."""
    if not _FORMULA_TEXT.fullmatch(text):
        raise MechanismNotationError(
            f"Ionization mechanism {notation!r}: {text!r} is not a formula."
        )
    if not (_nests(text, "(", ")") and _nests(text, "[", "]")):
        raise MechanismNotationError(
            f"Ionization mechanism {notation!r}: {text!r} has unbalanced brackets."
        )


def _parse_legacy(notation: str) -> MechanismParts:
    if notation in ("+", "-"):
        # "+" removes an electron and leaves a cation, "-" attaches one.
        return MechanismParts(
            addition=notation == "-", moiety="", charge=1 if notation == "+" else -1
        )
    if len(notation) < 3 or notation[0] not in "+-" or notation[-1] not in "+-":
        raise MechanismNotationError(
            f"Ionization mechanism {notation!r} is not in the standard adduct "
            "notation ('[M+H]+', '[M-H]-', '[M+Br]-', '[M]+.')."
        )
    moiety = notation[1:-1]
    _check_formula_text(moiety, notation)
    addition = notation[0] == "+"
    moiety_charge = 1 if notation[-1] == "+" else -1
    return MechanismParts(
        addition=addition,
        moiety=_join_terms(_ordered_terms(_split_moiety(moiety))),
        charge=moiety_charge if addition else -moiety_charge,
    )


def _parse_standard(notation: str) -> MechanismParts:
    radical = notation.endswith(".")
    body = notation[:-1] if radical else notation
    if not body.startswith("[M"):
        raise MechanismNotationError(
            f"Ionization mechanism {notation!r}: an ion is written around one "
            "molecule, '[M', as in '[M+H]+'."
        )
    if len(body) < 4 or body[-2] != "]" or body[-1] not in "+-":
        raise MechanismNotationError(
            f"Ionization mechanism {notation!r} must end in ']+' or ']-', the "
            "charge of a singly charged ion."
        )
    charge = 1 if body[-1] == "+" else -1
    inside = body[2:-2]
    if not inside:
        if not radical:
            raise MechanismNotationError(
                f"Ionization mechanism {notation!r}: electron transfer is "
                f"written '{body}.', the dot marking the radical ion."
            )
        return MechanismParts(addition=charge < 0, moiety="", charge=charge)
    if radical:
        raise MechanismNotationError(
            f"Ionization mechanism {notation!r}: only electron transfer, "
            "'[M]+.' or '[M]-.', carries the radical dot."
        )
    if not re.fullmatch(r"(?:[+-][^+-]+)+", inside):
        raise MechanismNotationError(
            f"Ionization mechanism {notation!r}: each term inside the brackets "
            "is added with '+' or removed with '-', as in '[M+H]+' or '[M-H]-'."
        )
    signed = _SIGNED_TERM.findall(inside)
    operations = {operation for operation, _ in signed}
    if len(operations) > 1:
        raise MechanismNotationError(
            f"Ionization mechanism {notation!r} both adds and removes; a "
            "mechanism adds its terms or removes them."
        )
    terms = [term for _, term in signed]
    for term in terms:
        _check_formula_text(term, notation)
        if term[0].isdigit():
            raise MechanismNotationError(
                f"Ionization mechanism {notation!r}: write the term {term!r} "
                "as a formula, '(H2O)2' rather than '2H2O'."
            )
    return MechanismParts(
        addition=operations == {"+"},
        moiety=_join_terms(_ordered_terms(terms)),
        charge=charge,
    )


@lru_cache(maxsize=1024)
def parse_mechanism(text: str) -> MechanismParts:
    """Read an ionization mechanism written in either notation.

    Examples
    --------
    >>> parse_mechanism("[M-H]-")
    MechanismParts(addition=False, moiety='H', charge=-1)
    >>> parse_mechanism("-H+")
    MechanismParts(addition=False, moiety='H', charge=-1)
    >>> parse_mechanism("[M]+.")
    MechanismParts(addition=False, moiety='', charge=1)

    :param text: The mechanism, ``"[M-H]-"`` or ``"-H+"``.
    :raises MechanismNotationError: The text is neither notation.
    :return: The mechanism's parts.
    """
    notation = text.strip()
    if notation.startswith("["):
        return _parse_standard(notation)
    return _parse_legacy(notation)


def standard_notation(text: str) -> str:
    """The mechanism in the standard adduct notation, from either notation.

    Examples
    --------
    >>> standard_notation("-H+")
    '[M-H]-'
    >>> standard_notation("+(CH4N2O)H+")
    '[M+CH4N2O+H]+'
    >>> standard_notation("+")
    '[M]+.'
    >>> standard_notation("[M+Br]-")
    '[M+Br]-'
    >>> standard_notation("[M+H+CH4N2O]+")
    '[M+CH4N2O+H]+'

    :param text: The mechanism in either notation.
    :raises MechanismNotationError: The text is neither notation.
    :return: The standard spelling, the one Mascope stores.
    """
    return parse_mechanism(text).standard


def legacy_notation(text: str) -> str:
    """The mechanism in the legacy notation, from either notation.

    Examples
    --------
    >>> legacy_notation("[M-H]-")
    '-H+'
    >>> legacy_notation("[M-H]+")
    '-H-'
    >>> legacy_notation("[M+CH4N2O+H]+")
    '+(CH4N2O)H+'
    >>> legacy_notation("[M]-.")
    '-'

    :param text: The mechanism in either notation.
    :raises MechanismNotationError: The text is neither notation.
    :return: The legacy spelling.
    """
    return parse_mechanism(text).legacy


def mechanism_key(text: str) -> str:
    """The spelling two mechanisms are compared by.

    The standard notation where the text reads as a mechanism, so ``-H+``,
    ``[M-H]-`` and, for a mechanism of several terms, any order they are
    written in are one mechanism; the text as it is otherwise, so a stored row
    that reads as neither still equals itself and nothing else.

    :param text: The mechanism, in either notation or neither.
    :return: The key to compare it by.
    """
    try:
        return standard_notation(text)
    except MechanismNotationError:
        return text.strip()


def mechanism_spellings(texts: Iterable[str]) -> list[str]:
    """Every spelling a stored row of these mechanisms may carry.

    A row is stored in the standard notation, or in the legacy one where it
    was written before the standard existed, so a lookup by ``[M-H]-`` has to
    reach ``-H+`` too, and the reverse. Either way its terms are in order: the
    validator stores them so, and the legacy rows the fleet stores are. Text
    that reads as neither is looked up as it is.

    Examples
    --------
    >>> mechanism_spellings(["[M-H]-", "+Br-"])
    ['[M-H]-', '-H+', '+Br-', '[M+Br]-']

    :param texts: Mechanisms in either notation.
    :return: The spellings to match, each once, in a stable order.
    """
    spellings: dict[str, None] = {}
    for text in texts:
        spellings[text] = None
        try:
            parts = parse_mechanism(text)
        except MechanismNotationError:
            continue
        spellings[parts.standard] = None
        spellings[parts.legacy] = None
    return list(spellings)
