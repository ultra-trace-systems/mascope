"""Write every ionization mechanism in the standard adduct notation

A mechanism was stored in Mascope's own ``<operation><moiety><moiety charge>``
notation - ``-H+`` for deprotonation - whose trailing sign is the charge of the
species moved rather than of the ion, and which almost everyone who met it read
the other way. The standard adduct notation puts the ion's charge last:
``[M-H]-``. The write validator stores a new mechanism in it and every reader
reads either notation, so this revision is what is left: the rows written
before, rewritten, so that no stored mechanism still depends on the legacy
grammar when that grammar is retired at 2.0 (step 3.3b of the assignment
quality plan, decision 23).

It reaches two places:

- ionization_mechanism.ionization_mechanism, the mechanism itself;
- assignment_calibration.corroboration_weights, a JSON object whose KEYS are
  mechanisms. A weight is matched to an adduct by mechanism rather than by
  spelling, so this is hygiene rather than repair, but a key left legacy would
  stop matching the day the legacy grammar goes.

Not reached, on purpose: the notations a run recorded about itself -
peak_assignment.provenance and peak_assignment_run.config. They are a record of
what that run did, the server reads them only to count or quote them, and the
frontend shows a legacy spelling in the standard one.

A row is written as the application reads it: stripped, and with its terms in
alphabetical order, which is the one spelling a mechanism has. A leading
parenthesised group that something follows is a term of its own
(``+(CH4N2O)H+`` is ``[M+CH4N2O+H]+``), a group with a multiplier stays in its
term (``+(CH4N2O)2H+`` is ``[M+(CH4N2O)2H]+``), and a bare ``+`` or ``-`` is
electron transfer, ``[M]+.`` or ``[M]-.``. Every legacy spelling the fleet
stores is already written that way, and on those the map is exact: each
converts to a standard spelling and back to itself, so a round trip leaves the
row as it was. Any other row comes back as the one legacy spelling of its
mechanism, which is the same mechanism to the code a downgrade restores. The
downgrade rewrites every standard row, including those written natively after
the upgrade: that code reads the legacy notation only.

The map is restated here rather than imported from mascope_tools, where it
also lives (``composition.mechanism_notation``): a migration describes the
notation as it was when it ran, and the library's legacy grammar is retired at
2.0. The two are pinned to agree by the migration's test.

A row that reads as neither notation - a free-text label an older validator let
through - is left as it is and reported, and so is a row whose rewrite would
take a spelling another row already holds: two rows of one mechanism, stored
before a mechanism had one spelling. The column is unique, and merging two
mechanism rows means moving everything that references one onto the other,
which is not a rewrite of notation; the report names both rows for an operator
to merge. Until then the application reads both as the one mechanism and
searches it once.

Revision ID: 5193d1e942e0
Revises: 4f8b2e6d9c17
Create Date: 2026-09-25 12:00:00.000000

"""

import json
import re
from typing import Callable, Sequence, Union

from alembic import op
from sqlalchemy import text
from sqlalchemy.engine import Connection


# revision identifiers, used by Alembic.
revision: str = "5193d1e942e0"
down_revision: Union[str, Sequence[str], None] = "4f8b2e6d9c17"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# --- The map ------------------------------------------------------------------

#: What a moiety or a term may hold: element symbols and counts, parenthesised
#: groups, bracketed isotopes and caret-labelled elements.
_FORMULA_TEXT = re.compile(r"[A-Za-z0-9()\[\]^]+")

#: A standard spelling: ``[M``, signed terms, ``]``, the ion's charge, and the
#: radical dot of electron transfer.
_STANDARD = re.compile(r"\[M((?:[+-][^+-]+)*)\]([+-])(\.?)")

#: One signed term of a standard spelling's inside.
_SIGNED_TERM = re.compile(r"([+-])([^+-]+)")

#: A mechanism read: the operation (``"+"`` adds, ``"-"`` removes; empty for
#: electron transfer), its terms in order, and the ion's charge.
Mechanism = tuple[str, list[str], str]


def _nests(text_: str, opening: str, closing: str) -> bool:
    """Whether every ``closing`` in ``text_`` closes an ``opening`` before it."""
    depth = 0
    for char in text_:
        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _is_formula_text(text_: str) -> bool:
    return (
        _FORMULA_TEXT.fullmatch(text_) is not None
        and _nests(text_, "(", ")")
        and _nests(text_, "[", "]")
    )


def _closing_paren(text_: str) -> int:
    """The index of the parenthesis closing the one ``text_`` starts with."""
    depth = 0
    for index, char in enumerate(text_):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _split(moiety: str) -> list[str]:
    """A legacy moiety's terms: each leading group something follows, then
    the rest as it is. ``_join`` gives back exactly the moiety it was given."""
    terms = []
    rest = moiety
    while rest.startswith("("):
        close = _closing_paren(rest)
        if close < 0 or close == len(rest) - 1:
            break
        group = rest[1:close]
        if not group or group[0].isdigit() or rest[close + 1].isdigit():
            break
        terms.append(group)
        rest = rest[close + 1 :]
    if rest:
        terms.append(rest)
    return terms


def _join(terms: list[str]) -> str:
    """The legacy moiety of these terms: all but the last parenthesised."""
    return "".join(f"({term})" for term in terms[:-1]) + terms[-1]


def _ordered(terms: list[str]) -> list[str]:
    """The terms in alphabetical order, each first split as far as a legacy
    moiety splits it, so a term that opens with a group reads as the terms it
    holds wherever it stands."""
    parts = list(terms)
    while (split := [part for term in parts for part in _split(term)]) != parts:
        parts = split
    return sorted(parts)


def _flip(sign: str) -> str:
    return "-" if sign == "+" else "+"


def _read(mechanism: str) -> Mechanism | None:
    """A stored mechanism in either notation; None where it is in neither."""
    text_ = mechanism.strip()
    match = _STANDARD.fullmatch(text_)
    if match is not None:
        inside, charge, radical = match.groups()
        if not inside:
            return ("", [], charge) if radical else None
        if radical:
            return None
        signed = _SIGNED_TERM.findall(inside)
        operations = {operation for operation, _ in signed}
        terms = [term for _, term in signed]
        if len(operations) != 1 or not all(
            _is_formula_text(term) and not term[0].isdigit() for term in terms
        ):
            return None
        return operations.pop(), _ordered(terms), charge
    if text_ in ("+", "-"):
        return "", [], text_
    if len(text_) < 3 or text_[0] not in "+-" or text_[-1] not in "+-":
        return None
    moiety = text_[1:-1]
    if not _is_formula_text(moiety):
        return None
    operation, moiety_charge = text_[0], text_[-1]
    # The ion's charge: the moiety's where it is added, the opposite where it
    # is removed - removing a proton leaves an anion.
    charge = moiety_charge if operation == "+" else _flip(moiety_charge)
    return operation, _ordered(_split(moiety)), charge


def to_standard(mechanism: str) -> str | None:
    """A stored mechanism in the standard notation; None where it reads as
    neither notation.

    :param mechanism: A stored mechanism, in either notation.
    :return: ``[M-H]-`` for ``-H+``.
    """
    read = _read(mechanism)
    if read is None:
        return None
    operation, terms, charge = read
    if not terms:
        return f"[M]{charge}."
    return "[M" + "".join(operation + term for term in terms) + f"]{charge}"


def to_legacy(mechanism: str) -> str | None:
    """A stored mechanism in the legacy notation; None where it reads as
    neither notation.

    :param mechanism: A stored mechanism, in either notation.
    :return: ``-H+`` for ``[M-H]-``.
    """
    read = _read(mechanism)
    if read is None:
        return None
    operation, terms, charge = read
    if not terms:
        return charge
    moiety_charge = charge if operation == "+" else _flip(charge)
    return operation + _join(terms) + moiety_charge


def _written_standard(mechanism: str) -> bool:
    return mechanism.strip().startswith("[")


def _written_legacy(mechanism: str) -> bool:
    return not _written_standard(mechanism)


# --- The rewrite ----------------------------------------------------------------


def _rewrite_mechanisms(
    connection: Connection, convert: Callable[[str], str | None]
) -> tuple[int, list[str]]:
    """Rewrite every mechanism row ``convert`` writes otherwise than it is.

    :param connection: The migration's connection.
    :param convert: The map in this direction.
    :return: How many rows were rewritten, and a line per row left as it is
        for a reason worth reporting.
    """
    rows = connection.execute(
        text(
            "SELECT ionization_mechanism_id, ionization_mechanism "
            "FROM ionization_mechanism ORDER BY ionization_mechanism_id"
        )
    ).all()
    held = {mechanism: mechanism_id for mechanism_id, mechanism in rows}
    rewritten = 0
    left: list[str] = []
    for mechanism_id, mechanism in rows:
        new = convert(mechanism)
        if new is None:
            left.append(
                f"'{mechanism}' ({mechanism_id}) reads as neither notation; "
                "correct or remove it"
            )
            continue
        if new == mechanism:
            continue
        if new in held:
            left.append(
                f"'{mechanism}' ({mechanism_id}) is the mechanism '{new}' that "
                f"{held[new]} holds, and the column is unique; merge the two, "
                f"moving what references {mechanism_id} onto {held[new]}"
            )
            continue
        connection.execute(
            text(
                "UPDATE ionization_mechanism SET ionization_mechanism = :new "
                "WHERE ionization_mechanism_id = :id"
            ),
            {"new": new, "id": mechanism_id},
        )
        del held[mechanism]
        held[new] = mechanism_id
        rewritten += 1
    return rewritten, left


def _rekey(
    weights: dict,
    convert: Callable[[str], str | None],
    written_in_destination: Callable[[str], bool],
) -> dict:
    """The weights with their keys in the destination notation.

    Two keys may name one adduct. The first written in the destination
    notation wins, and the first otherwise, which in the standard direction is
    the library's own rule (``corroboration_by_mechanism``), so a calibration
    scores the same after the upgrade as before it. A key that reads as
    neither notation is kept as it is.
    """
    rekeyed: dict = {}
    settled: set = set()
    for key, weight in weights.items():
        new = convert(key)
        if new is None:
            new = key
        written = written_in_destination(key)
        if new in rekeyed and (new in settled or not written):
            continue
        rekeyed[new] = weight
        if written:
            settled.add(new)
    return rekeyed


def _rewrite_weights(
    connection: Connection,
    convert: Callable[[str], str | None],
    written_in_destination: Callable[[str], bool],
) -> int:
    """Rewrite the mechanism keys of every calibration's weights.

    :return: How many calibrations were rewritten.
    """
    rows = connection.execute(
        text(
            "SELECT assignment_calibration_id, corroboration_weights "
            "FROM assignment_calibration WHERE corroboration_weights IS NOT NULL"
        )
    ).all()
    rewritten = 0
    for calibration_id, weights in rows:
        if isinstance(weights, str):
            weights = json.loads(weights)
        if not isinstance(weights, dict):
            continue
        rekeyed = _rekey(weights, convert, written_in_destination)
        if rekeyed == weights and list(rekeyed) == list(weights):
            continue
        connection.execute(
            text(
                "UPDATE assignment_calibration "
                "SET corroboration_weights = CAST(:weights AS json) "
                "WHERE assignment_calibration_id = :id"
            ),
            {"weights": json.dumps(rekeyed), "id": calibration_id},
        )
        rewritten += 1
    return rewritten


def _report(direction: str, mechanisms: int, left: list[str], weights: int) -> None:
    """Print what was rewritten and what was left, unless there was neither."""
    if mechanisms or weights:
        print(
            f"Wrote {mechanisms} ionization mechanism(s) and the weights of "
            f"{weights} calibration(s) in the {direction} notation"
        )
    for line in left:
        print(f"WARNING: left an ionization mechanism as it is: {line}")


def upgrade() -> None:
    connection = op.get_bind()
    mechanisms, left = _rewrite_mechanisms(connection, to_standard)
    weights = _rewrite_weights(connection, to_standard, _written_standard)
    _report("standard adduct", mechanisms, left, weights)


def downgrade() -> None:
    connection = op.get_bind()
    mechanisms, left = _rewrite_mechanisms(connection, to_legacy)
    weights = _rewrite_weights(connection, to_legacy, _written_legacy)
    _report("legacy", mechanisms, left, weights)
