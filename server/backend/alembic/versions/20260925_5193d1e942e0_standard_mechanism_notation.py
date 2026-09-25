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

The map is exact in the direction a row travels: every legacy spelling converts
to a standard one and back to itself. A leading parenthesised group that
something follows becomes a term of its own (``+(CH4N2O)H+`` is
``[M+CH4N2O+H]+``), a group with a multiplier stays in its term
(``+(CH4N2O)2H+`` is ``[M+(CH4N2O)2H]+``), and a bare ``+`` or ``-`` is
electron transfer, ``[M]+.`` or ``[M]-.``. So downgrade() is the inverse and a
round trip leaves every row as it was. The downgrade rewrites every standard
row, including those written natively after the upgrade: the code a downgrade
restores reads the legacy notation only.

The map is restated here rather than imported from mascope_tools, where it
also lives (``composition.mechanism_notation``): a migration describes the
notation as it was when it ran, and the library's legacy grammar is retired at
2.0. The two are pinned to agree by the migration's test.

A row that reads as neither notation - a free-text label an older validator let
through - is left as it is and reported, and so is a row whose rewrite would
take a spelling another row already holds: the column is unique, and merging
two mechanism rows means re-pointing everything that references them, which is
not a rewrite of notation.

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


def _is_formula_text(text_: str) -> bool:
    return (
        _FORMULA_TEXT.fullmatch(text_) is not None
        and text_.count("(") == text_.count(")")
        and text_.count("[") == text_.count("]")
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


def _flip(sign: str) -> str:
    return "-" if sign == "+" else "+"


def to_standard(mechanism: str) -> str | None:
    """The standard spelling of a legacy mechanism; None where it is not one.

    :param mechanism: A stored mechanism.
    :return: ``[M-H]-`` for ``-H+``, or None for anything not legacy.
    """
    if mechanism in ("+", "-"):
        return f"[M]{mechanism}."
    if len(mechanism) < 3 or mechanism[0] not in "+-" or mechanism[-1] not in "+-":
        return None
    moiety = mechanism[1:-1]
    if not _is_formula_text(moiety):
        return None
    operation, moiety_charge = mechanism[0], mechanism[-1]
    # The ion's charge: the moiety's where it is added, the opposite where it
    # is removed - removing a proton leaves an anion.
    charge = moiety_charge if operation == "+" else _flip(moiety_charge)
    inside = "".join(operation + term for term in _split(moiety))
    return f"[M{inside}]{charge}"


def to_legacy(mechanism: str) -> str | None:
    """The legacy spelling of a standard mechanism; None where it is not one.

    :param mechanism: A stored mechanism.
    :return: ``-H+`` for ``[M-H]-``, or None for anything not standard.
    """
    match = _STANDARD.fullmatch(mechanism)
    if match is None:
        return None
    inside, charge, radical = match.groups()
    if not inside:
        return charge if radical else None
    if radical:
        return None
    signed = _SIGNED_TERM.findall(inside)
    operations = {operation for operation, _ in signed}
    terms = [term for _, term in signed]
    if len(operations) != 1 or not all(
        _is_formula_text(term) and not term[0].isdigit() for term in terms
    ):
        return None
    operation = operations.pop()
    moiety_charge = charge if operation == "+" else _flip(charge)
    return operation + _join(terms) + moiety_charge


# --- The rewrite ----------------------------------------------------------------


def _rewrite_mechanisms(
    connection: Connection,
    convert: Callable[[str], str | None],
    back: Callable[[str], str | None],
) -> tuple[int, list[str]]:
    """Rewrite every mechanism row ``convert`` reads.

    :param connection: The migration's connection.
    :param convert: The map in this direction.
    :param back: The map in the other direction, telling a row that is already
        in the destination notation from one that is in neither.
    :return: How many rows were rewritten, and a line per row left as it is
        for a reason worth reporting.
    """
    rows = connection.execute(
        text(
            "SELECT ionization_mechanism_id, ionization_mechanism "
            "FROM ionization_mechanism ORDER BY ionization_mechanism_id"
        )
    ).all()
    taken = {mechanism for _, mechanism in rows}
    rewritten = 0
    left: list[str] = []
    for mechanism_id, mechanism in rows:
        new = convert(mechanism)
        if new is None:
            if back(mechanism) is None:
                left.append(f"'{mechanism}' ({mechanism_id}) reads as neither notation")
            continue
        if new in taken:
            left.append(
                f"'{mechanism}' ({mechanism_id}) would become '{new}', which "
                "another row already holds"
            )
            continue
        connection.execute(
            text(
                "UPDATE ionization_mechanism SET ionization_mechanism = :new "
                "WHERE ionization_mechanism_id = :id"
            ),
            {"new": new, "id": mechanism_id},
        )
        taken.discard(mechanism)
        taken.add(new)
        rewritten += 1
    return rewritten, left


def _rekey(weights: dict, convert: Callable[[str], str | None]) -> dict:
    """The weights with their keys in the destination notation.

    A key already in it wins over another that converts onto it: the two name
    one adduct, and the one written in the current notation is the later word.
    """
    kept = {key for key in weights if convert(key) is None}
    rekeyed: dict = {}
    for key, weight in weights.items():
        new = convert(key)
        if new is None:
            rekeyed[key] = weight
        elif new not in kept and new not in rekeyed:
            rekeyed[new] = weight
    return rekeyed


def _rewrite_weights(
    connection: Connection, convert: Callable[[str], str | None]
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
        rekeyed = _rekey(weights, convert)
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
        print(f"Left an ionization mechanism as it is: {line}")


def _standard_round_trips(mechanism: str) -> str | None:
    """``to_standard``, held to what makes the downgrade exact: a spelling that
    would not come back as itself is not rewritten. None does not arise for a
    legacy spelling - the map is exact by construction - but a migration that
    cannot be undone must not rely on that."""
    new = to_standard(mechanism)
    if new is None or to_legacy(new) != mechanism:
        return None
    return new


def upgrade() -> None:
    connection = op.get_bind()
    mechanisms, left = _rewrite_mechanisms(connection, _standard_round_trips, to_legacy)
    weights = _rewrite_weights(connection, _standard_round_trips)
    _report("standard adduct", mechanisms, left, weights)


def downgrade() -> None:
    connection = op.get_bind()
    mechanisms, left = _rewrite_mechanisms(connection, to_legacy, to_standard)
    weights = _rewrite_weights(connection, to_legacy)
    _report("legacy", mechanisms, left, weights)
