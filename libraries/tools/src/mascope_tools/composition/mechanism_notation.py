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

The standard notation also says three things the legacy one cannot, and only
it writes them:

- a **multimer**, ``[2M+H]+``: the count of molecules before ``M``;
- a **charge above one**, ``[M+2H]2+``, ``[M+Ca]2+``, ``[M-2H]2-``: the count
  before the ion's sign. A term may then carry a coefficient, and only as the
  mechanism's one term with the ion's charge as its count (one charge per
  species): ``2H`` is stored as the formula ``(H)2``, and written back as
  ``2H`` wherever the ion's charge is two. One species written out as many
  times as the charge is the same ion and is folded into that form
  (``[M+H+H]2+`` is ``[M+2H]2+``); several species stay as they are typed
  (``[M+H+H+Na]3+``). A species that carries one charge is refused alone on
  an ion of a higher one (``[M+H]2+``, the 2 left out) and repeated inside
  its formula (``[M+H2]2+``): each is ``[M+2H]2+`` mistyped;
- a **neutral loss**, ``[M+H-H2O]+``: a term removed from a mechanism that
  adds. The ion's charge is carried by what is added, and what is removed is
  neutral. Only a mechanism that adds has one: what a mechanism removes is
  all its species (``[M-H-H2O]-`` removes ``(H)H2O``).

Anything else that both adds and removes, a removed proton among them
(``[M+H2O-H]-``), a coefficient anywhere but there and an electron transfer
that changes more than one charge are refused rather than approximated. Those
three shapes have no legacy spelling (:attr:`MechanismParts.legacy` is None).

A mechanism is also a **row** (:class:`AdductRow`, :func:`to_row`,
:func:`from_row`): whether a species is added or removed, its formula, the
charge it carries, how many molecules the ion holds and what neutral it loses.
The ion's polarity and charge, its display form and its legacy spelling are
derived from the row rather than typed, and the row and the standard spelling
map onto each other exactly.
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

#: The head of a standard spelling: the bracket, the molecule's count where
#: the ion holds more than one, and ``M``.
_STANDARD_HEAD = re.compile(r"\[([0-9]+)?M")

#: The tail of a standard spelling, its radical dot taken off: the bracket
#: closing the ion, the charge's size where it is more than one, its sign.
_STANDARD_TAIL = re.compile(r"\]([0-9]+)?([+-])\Z")

#: A term with a coefficient, ``2H``: the count and what it counts.
_COEFFICIENT_TERM = re.compile(r"([0-9]+)(.*)", re.DOTALL)

#: One element repeated, ``H2`` or ``Na2``: the element and its count.
_REPEATED_ELEMENT = re.compile(r"([A-Z][a-z]?)([0-9]+)")

#: The species that carry one charge each, never more: the proton, the alkali
#: metals, the halides, ammonium, nitrate and silver. One of them alone with a
#: charge above one is a coefficient left out (``[M+H]2+`` for ``[M+2H]2+``),
#: and the same element repeated as a formula is the coefficient written as
#: one (``[M+H2]2+``). A species missing here lets that mistake through, as
#: before; a multivalent ion is never refused (``[M+Ca]2+``).
_MONOVALENT_SPECIES = frozenset(
    {"H", "Li", "Na", "K", "Rb", "Cs", "F", "Cl", "Br", "I", "NH4", "NO3", "Ag"}
)


class MechanismNotationError(ValueError):
    """A mechanism written in neither notation."""


@dataclass(frozen=True)
class MechanismParts:
    """An ionization mechanism, whichever notation it was written in.

    :param addition: Whether the moiety is added to the molecule (True) or
        removed from it. Electron transfer is an electron attached
        (``[M]-.``, an addition) or removed (``[M]+.``).
    :param moiety: What is added or removed, as the legacy notation writes it,
        its terms in order: ``"H"``, ``"NO3"``, ``"(CH4N2O)H"``, ``"(H)2"``
        for the two protons of ``[M+2H]2+``. Empty for electron transfer.
    :param charge: The ion's charge: ``+1`` or ``-1``, ``+2`` for ``[M+2H]2+``.
    :param multimer: How many molecules the ion holds, 2 for ``[2M+H]+``.
    :param neutral_loss: The neutral a mechanism that adds also removes, its
        terms in order as the moiety's are: ``"H2O"`` for ``[M+H-H2O]+``.
        Empty for none.
    """

    addition: bool
    moiety: str
    charge: int
    multimer: int = 1
    neutral_loss: str = ""

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
    def simple(self) -> bool:
        """Whether the legacy notation can say it too: one molecule, a single
        charge and no neutral loss. The composition finder searches only
        these."""
        return self.multimer == 1 and abs(self.charge) == 1 and not self.neutral_loss

    @property
    def terms(self) -> tuple[str, ...]:
        """The moiety's terms as the standard notation writes them.

        ``2H`` for the moiety ``(H)2`` of an ion of charge two: a group
        multiplied by the ion's charge is written with a coefficient.
        """
        size = abs(self.charge)
        if size > 1:
            multiplied = _multiplied_group(self.moiety)
            if multiplied is not None and multiplied[1] == size:
                return (f"{size}{multiplied[0]}",)
        return _split_moiety(self.moiety)

    @property
    def formulas(self) -> tuple[str, ...]:
        """Every term as a formula, the moiety's and then the neutral loss's:
        ``(H)2`` where :attr:`terms` writes ``2H``."""
        loss = _split_moiety(self.neutral_loss) if self.neutral_loss else ()
        return _split_moiety(self.moiety) + loss

    @property
    def loss_terms(self) -> tuple[str, ...]:
        """The neutral loss's terms, as the standard notation writes them."""
        return _split_moiety(self.neutral_loss) if self.neutral_loss else ()

    @property
    def standard(self) -> str:
        """The mechanism in the standard adduct notation, ``[M-H]-``."""
        molecule = f"{self.multimer}M" if self.multimer > 1 else "M"
        size = abs(self.charge)
        charge = (str(size) if size > 1 else "") + self.polarity
        if self.electron_transfer:
            return f"[{molecule}]{charge}."
        operation = "+" if self.addition else "-"
        inside = "".join(operation + term for term in self.terms)
        inside += "".join("-" + term for term in self.loss_terms)
        return f"[{molecule}{inside}]{charge}"

    @property
    def legacy(self) -> str | None:
        """The mechanism in the legacy notation, ``-H+``; None for a multimer,
        a charge above one or a neutral loss, which it cannot say."""
        if not self.simple:
            return None
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


def _multiplied_group(moiety: str) -> tuple[str, int] | None:
    """What a moiety written ``(X)k`` multiplies, and by how much; None for
    any other moiety."""
    if not moiety.startswith("("):
        return None
    close = _closing_paren(moiety)
    if close is None or close < 2:
        return None
    count = moiety[close + 1 :]
    if not count.isdigit() or count[0] == "0":
        return None
    return moiety[1:close], int(count)


def _join_terms(terms: list[str] | tuple[str, ...]) -> str:
    """The legacy moiety of these terms: all but the last parenthesised."""
    return "".join(f"({term})" for term in terms[:-1]) + terms[-1]


def _ordered_terms(terms: Iterable[str]) -> tuple[str, ...]:
    """The terms in the order a mechanism is written in: alphabetical.

    Each term is first split as far as a legacy moiety splits it, so a term
    that opens with a group reads as the terms it holds wherever it stands:
    ``[M+K+(H2O)Na]+`` and ``[M+(H2O)Na+K]+`` are both ``[M+H2O+K+Na]+``.
    Split that far, the sorted terms come back unchanged from being joined
    into a moiety and split again, so they are the terms the stored moiety
    holds.
    """
    parts = tuple(terms)
    while (split := tuple(p for term in parts for p in _split_moiety(term))) != parts:
        parts = split
    return tuple(sorted(parts))


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


def _count(text: str | None, notation: str, what: str, single: str) -> int:
    """A molecule count or a charge's size as written: 1 where it is not."""
    if text is None:
        return 1
    if text[0] == "0" or int(text) < 2:
        raise MechanismNotationError(
            f"Ionization mechanism {notation!r}: {what} is written {single}, "
            "with no count."
        )
    return int(text)


def _added_formula(term: str, size: int, alone: bool, notation: str) -> str:
    """An added or removed term as a formula: a coefficient, ``2H``, is the
    group ``(H)2``, and only where the ion's charge is its count."""
    if not term[0].isdigit():
        return term
    if size == 1:
        raise MechanismNotationError(
            f"Ionization mechanism {notation!r}: write the term {term!r} "
            "as a formula, '(H2O)2' rather than '2H2O'."
        )
    count, species = _COEFFICIENT_TERM.fullmatch(term).groups()
    if not alone:
        raise MechanismNotationError(
            f"Ionization mechanism {notation!r}: a coefficient is written on "
            "a mechanism's only term, as in '[M+2H]2+'; write several terms "
            "one by one, '[M+H+H+Na]3+'."
        )
    if not species or count[0] == "0" or int(count) != size:
        raise MechanismNotationError(
            f"Ionization mechanism {notation!r}: the coefficient of {term!r} "
            "is the ion's charge, one charge for each species, as in "
            "'[M+2H]2+'; write any other count as a formula, '(H2O)2'."
        )
    return f"({species}){count}"


def _folded_terms(terms: list[str], size: int) -> list[str]:
    """The terms of an ion of charge ``size``, one species repeated as many
    times as the charge folded into the group it multiplies: ``H`` and ``H``
    of ``[M+H+H]2+`` are ``(H)2``, the moiety of ``[M+2H]2+``. Any other terms
    as they are, so ``[M+H+H+Na]3+`` keeps its three. The terms are counted
    split as far as they split, so ``[M+(H)H]2+`` folds too."""
    if size == 1:
        return terms
    ordered = _ordered_terms(terms)
    if len(ordered) != size or len(set(ordered)) != 1 or ordered[0][0].isdigit():
        return terms
    return [f"({ordered[0]}){size}"]


def _check_single_charges(
    terms: list[str], parts: MechanismParts, notation: str
) -> None:
    """Refuse one species that carries a single charge written as carrying
    the ion's whole charge, ``[M+H]2+``, or the coefficient written into the
    formula, ``[M+H2]2+``: both mean ``[M+2H]2+``."""
    size = abs(parts.charge)
    if size == 1 or len(terms) != 1:
        return
    (term,) = terms
    repeated = _REPEATED_ELEMENT.fullmatch(term)
    if term in _MONOVALENT_SPECIES:
        species = term
        reading = f"one {term} carries one charge, not {size}"
    elif (
        repeated is not None
        and repeated.group(1) in _MONOVALENT_SPECIES
        and int(repeated.group(2)) == size
    ):
        species = repeated.group(1)
        reading = f"{term!r} is {size} {species}, which carry one charge each"
    else:
        return
    meant = MechanismParts(
        addition=parts.addition,
        moiety=f"({species}){size}",
        charge=parts.charge,
        multimer=parts.multimer,
        neutral_loss=parts.neutral_loss,
    )
    raise MechanismNotationError(
        f"Ionization mechanism {notation!r}: {reading}; write "
        f"{meant.standard!r}, one charge for each species."
    )


def _parse_standard(notation: str) -> MechanismParts:
    radical = notation.endswith(".")
    body = notation[:-1] if radical else notation
    head = _STANDARD_HEAD.match(body)
    if head is None:
        raise MechanismNotationError(
            f"Ionization mechanism {notation!r}: an ion is written around its "
            "molecule, '[M', as in '[M+H]+', or around one of several, "
            "'[2M+H]+'."
        )
    tail = _STANDARD_TAIL.search(body)
    if tail is None or tail.start() < head.end():
        raise MechanismNotationError(
            f"Ionization mechanism {notation!r} must end in the ion's charge: "
            "']+' or ']-', or ']2+' for a doubly charged ion."
        )
    multimer = _count(head.group(1), notation, "one molecule", "'[M'")
    size = _count(tail.group(1), notation, "a single charge", "']+' or ']-'")
    charge = size if tail.group(2) == "+" else -size
    inside = body[head.end() : tail.start()]
    if not inside:
        if not radical:
            raise MechanismNotationError(
                f"Ionization mechanism {notation!r}: electron transfer is "
                f"written '{body}.', the dot marking the radical ion."
            )
        if size > 1:
            raise MechanismNotationError(
                f"Ionization mechanism {notation!r}: electron transfer moves "
                "one electron, '[M]+.' or '[M]-.'."
            )
        return MechanismParts(
            addition=charge < 0, moiety="", charge=charge, multimer=multimer
        )
    if radical:
        raise MechanismNotationError(
            f"Ionization mechanism {notation!r}: only electron transfer, "
            "'[M]+.' or '[M]-.', carries the radical dot."
        )
    # The inner quantifier is possessive. A term can only end where the next
    # sign begins or where the string does, so giving characters back never
    # turns a failure into a match - and without that the engine retries every
    # split of an unbounded value, which is what a caller sends.
    if not re.fullmatch(r"(?:[+-][^+-]++)+", inside):
        raise MechanismNotationError(
            f"Ionization mechanism {notation!r}: each term inside the brackets "
            "is added with '+' or removed with '-', as in '[M+H]+' or '[M-H]-'."
        )
    signed = _SIGNED_TERM.findall(inside)
    added = [term for operation, term in signed if operation == "+"]
    removed = [term for operation, term in signed if operation == "-"]
    # A mechanism that adds and removes adds what carries the charge and
    # loses a neutral: [M+H-H2O]+.
    moiety_terms, lost = (added, removed) if added else (removed, [])
    for term in moiety_terms + lost:
        _check_formula_text(term, notation)
    for term in lost:
        if term[0].isdigit():
            raise MechanismNotationError(
                f"Ionization mechanism {notation!r}: write the neutral loss "
                f"{term!r} as a formula, '(H2O)2' rather than '2H2O'."
            )
    neutral_loss = _join_terms(_ordered_terms(lost)) if lost else ""
    # A removed proton carries a charge, so it is never the neutral loss:
    # [M+H2O-H]- and [M+Na-H-H]- say an ion the row has no way to write.
    if lost and "H" in _ordered_terms(lost):
        raise MechanismNotationError(
            f"Ionization mechanism {notation!r}: a removed 'H' is a proton, "
            "not a neutral loss. A mechanism that adds and removes adds what "
            "carries the charge and loses a neutral, as in '[M+H-H2O]+'."
        )
    moiety_terms = _folded_terms(moiety_terms, size)
    formulas = [
        _added_formula(term, size, len(moiety_terms) == 1, notation)
        for term in moiety_terms
    ]
    parts = MechanismParts(
        addition=bool(added),
        moiety=_join_terms(_ordered_terms(formulas)),
        charge=charge,
        multimer=multimer,
        neutral_loss=neutral_loss,
    )
    _check_single_charges(moiety_terms, parts, notation)
    return parts


@lru_cache(maxsize=1024)
def parse_mechanism(text: str) -> MechanismParts:
    """Read an ionization mechanism written in either notation.

    Examples
    --------
    >>> parse_mechanism("[M-H]-")
    MechanismParts(addition=False, moiety='H', charge=-1, multimer=1, neutral_loss='')
    >>> parse_mechanism("-H+")
    MechanismParts(addition=False, moiety='H', charge=-1, multimer=1, neutral_loss='')
    >>> parse_mechanism("[M]+.")
    MechanismParts(addition=False, moiety='', charge=1, multimer=1, neutral_loss='')

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
    :raises MechanismNotationError: The text is neither notation, or a
        mechanism the legacy notation cannot say: a multimer, a charge above
        one or a neutral loss.
    :return: The legacy spelling.
    """
    parts = parse_mechanism(text)
    if parts.legacy is None:
        raise MechanismNotationError(
            f"Ionization mechanism {text.strip()!r} has no legacy spelling: "
            "that notation says neither a multimer, a charge above one nor a "
            "neutral loss."
        )
    return parts.legacy


@lru_cache(maxsize=1024)
def mechanism_key(text: str) -> str:
    """The spelling two mechanisms are compared by.

    The standard notation where the text reads as a mechanism, so ``-H+``,
    ``[M-H]-`` and, for a mechanism of several terms, any order they are
    written in are one mechanism; the text as it is otherwise, so a stored row
    that reads as neither still equals itself and nothing else. Cached: the
    mechanism column reads every row through it, and a deployment holds a few
    dozen spellings at most.

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
        if parts.legacy is not None:
            spellings[parts.legacy] = None
    return list(spellings)


#: The two operations of a row: the species is added to the molecule, or
#: removed from it.
OPERATIONS = ("add", "remove")


@dataclass(frozen=True)
class AdductRow:
    """An ionization mechanism as a structured row.

    What is stored is what a person decides: whether a species is added or
    removed, which, the charge it carries, how many molecules the ion holds
    and what neutral it loses. What follows from those - the ion's polarity
    and charge, the display form and the legacy spelling - is derived, so
    nobody types a sign whose meaning is ambiguous. :func:`to_row` and
    :func:`from_row` map a row and its standard spelling onto each other
    exactly.

    :param operation: ``"add"`` or ``"remove"``. Electron transfer attaches
        an electron (``[M]-.``) or removes one (``[M]+.``).
    :param species: What is added or removed, as a formula: ``"H"``,
        ``"Br"``, ``"NO3"``, ``"(CH4N2O)H"`` for two terms, ``"(H)2"`` for the
        two protons of ``[M+2H]2+``. Empty for the electron.
    :param species_charge: The charge the species carries, all of it: ``+1``
        for a proton, ``-1`` for bromide and for the electron, ``+2`` for the
        two protons of ``[M+2H]2+`` or a calcium ion.
    :param multimer_n: How many molecules the ion holds: 2 for ``[2M+H]+``.
    :param neutral_loss: The neutral the ion loses, a formula; None for none.
        Only a mechanism that adds loses one: ``[M+H-H2O]+``.
    :param name: What people call it, ``"protonation"``; not part of the
        mechanism.
    """

    operation: str
    species: str
    species_charge: int
    multimer_n: int = 1
    neutral_loss: str | None = None
    name: str | None = None

    @property
    def parts(self) -> MechanismParts:
        """The mechanism the row describes, as the parser reads one."""
        addition = self.operation == "add"
        return MechanismParts(
            addition=addition,
            moiety=self.species,
            charge=self.species_charge if addition else -self.species_charge,
            multimer=self.multimer_n,
            neutral_loss=self.neutral_loss or "",
        )

    @property
    def ion_charge(self) -> int:
        """The ion's charge: the species' where it is added, the opposite
        where it is removed (removing a proton leaves an anion)."""
        return self.parts.charge

    @property
    def ion_polarity(self) -> str:
        """The ion's polarity, ``"+"`` or ``"-"``."""
        return self.parts.polarity

    @property
    def display(self) -> str:
        """The row in the standard adduct notation, ``[M+H]+``."""
        return from_row(self)

    @property
    def legacy(self) -> str | None:
        """The row in the legacy notation, ``+H+``; None for what it cannot
        say."""
        return self.parts.legacy


def to_row(text: str, name: str | None = None) -> AdductRow:
    """The structured row of a mechanism written in either notation.

    Examples
    --------
    >>> to_row("[M-H]-")
    AdductRow(operation='remove', species='H', species_charge=1, multimer_n=1, neutral_loss=None, name=None)
    >>> to_row("[2M+H-H2O]+").neutral_loss
    'H2O'
    >>> to_row("[M+2H]2+").species
    '(H)2'

    :param text: The mechanism, ``"[M+H]+"`` or ``"+H+"``.
    :param name: What people call it, stored beside the mechanism.
    :raises MechanismNotationError: The text is neither notation.
    :return: The row.
    """
    parts = parse_mechanism(text)
    return AdductRow(
        operation="add" if parts.addition else "remove",
        species=parts.moiety,
        species_charge=parts.moiety_charge,
        multimer_n=parts.multimer,
        neutral_loss=parts.neutral_loss or None,
        name=name,
    )


def from_row(row: AdductRow) -> str:
    """A row's mechanism in the standard adduct notation.

    The inverse of :func:`to_row`: ``to_row(from_row(row))`` is the row, its
    name aside. A row :func:`to_row` would not write - an unknown operation,
    no charge, terms out of order, a neutral loss on a mechanism that
    removes - is refused rather than written as some other mechanism.

    Examples
    --------
    >>> from_row(AdductRow("add", "H", 1, multimer_n=2))
    '[2M+H]+'
    >>> from_row(AdductRow("remove", "H", 1))
    '[M-H]-'

    :param row: The row.
    :raises MechanismNotationError: The row is not one a mechanism reads as.
    :return: The standard spelling, the one Mascope stores.
    """
    if row.operation not in OPERATIONS:
        raise MechanismNotationError(
            f"An adduct row's operation is 'add' or 'remove', not {row.operation!r}."
        )
    if row.species_charge == 0 or row.multimer_n < 1:
        raise MechanismNotationError(
            "An adduct row's species carries a charge, and its ion holds at "
            "least one molecule."
        )
    parts = row.parts
    standard = parts.standard
    try:
        read = parse_mechanism(standard)
    except MechanismNotationError as error:
        raise MechanismNotationError(
            f"The adduct row {row} is not a mechanism: {error}"
        ) from error
    if read != parts:
        raise MechanismNotationError(
            f"The adduct row {row} is not written as a mechanism reads: "
            f"{standard!r} reads as {to_row(standard)}."
        )
    return standard
