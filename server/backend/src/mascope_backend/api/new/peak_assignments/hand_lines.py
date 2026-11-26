"""
An isotope line assigned by hand, and the run's M0 it folds under.

A person who assigns a peak as one of a compound's isotope lines (``M+1``,
``M+4``) names a formula and an adduct, and the run may already hold that
compound's monoisotopic row. When it holds exactly one, the line is linked to
it: it gets that row as its owner, folds under it like a run's own line, and
shows its level (``identification_level.inherited_level``). Its tier stays
the one the hand path gave it. When the run holds none, or more than one, the
line names no owner and stands as a row of its own (MAS-1316), and the edit's
message says which case it was.

The link runs both ways. An edit that commits an M0 (a hand M0, or the undo
that promotes a winner back) takes the run's ownerless hand lines of its
compound, by the same one-M0 rule. And an edit that replaces the M0's
compound **detaches** a hand line rather than demoting it: the line keeps the
formula the person gave it and loses its owner. A person's choice is never
undone as a side effect, the rule the undo already follows for a row curated
by hand after a demotion (``curation._restore_demoted``).

A hand line is told by the action on its own row's manual block
(:data:`HAND_ACTIONS`), not by its ``source``: the lines a hand M0 will claim
(MAS-1318) are ``manual`` too, and they belong to the M0's click, so they are
demoted with it.
"""

from typing import NamedTuple

from sqlalchemy import select

from mascope_backend.api.new.peak_assignments.identification_level import (
    IDENTIFICATION_LEVEL,
    hand_level,
    inherited_level,
)
from mascope_backend.api.new.peak_assignments.row_terms import (
    ROLE_ISO_CHILD,
    ROLE_M0,
    SOURCE_MANUAL,
)
from mascope_backend.db import PeakAssignment


#: The actions a person takes on a row itself. A line whose own manual block
#: records one of them is that person's choice.
HAND_ACTIONS = frozenset({"set_assignment", "promote_alternative"})


class HandLink(NamedTuple):
    """What linking an edited row came to.

    ``owner`` is the M0 an isotope line was linked under; ``found`` how many
    M0 rows of its compound the run holds (None for a row that is not a
    line); ``linked`` the hand lines an M0 took; ``note`` what the edit's
    message says of it.
    """

    owner: PeakAssignment | None = None
    found: int | None = None
    linked: tuple[PeakAssignment, ...] = ()
    note: str = ""


def is_hand_line(row: PeakAssignment) -> bool:
    """Whether a row is an isotope line a person assigned by hand."""
    manual = (row.provenance or {}).get("manual")
    return (
        row.role == ROLE_ISO_CHILD
        and isinstance(manual, dict)
        and manual.get("action") in HAND_ACTIONS
    )


async def _run_m0_rows(session, row: PeakAssignment) -> list[PeakAssignment]:
    """The run's M0 rows of a row's formula and adduct, the row itself
    included when it is one."""
    return list(
        (
            await session.execute(
                select(PeakAssignment).where(
                    PeakAssignment.peak_assignment_run_id == row.peak_assignment_run_id,
                    PeakAssignment.role == ROLE_M0,
                    PeakAssignment.assigned_formula == row.assigned_formula,
                    PeakAssignment.ionization_mechanism_id.is_not_distinct_from(
                        row.ionization_mechanism_id
                    ),
                )
            )
        )
        .scalars()
        .all()
    )


def _show_owner_level(line: PeakAssignment, owner: PeakAssignment) -> None:
    """Give a linked line its owner's level, as a run's line shows it."""
    record = inherited_level(
        (owner.provenance or {}).get(IDENTIFICATION_LEVEL), owner.peak_assignment_id
    )
    line.provenance = {**(line.provenance or {}), IDENTIFICATION_LEVEL: record}
    line.level = record["level"]


async def link_hand_lines(session, row: PeakAssignment) -> HandLink:
    """Set an edited row's owner, and an edited M0's hand lines.

    Called once the row holds its new formula, role and level. A line takes
    the run's one M0 of its compound as owner; an M0 that is its compound's
    only one takes the run's ownerless hand lines of it. Any other row stands
    for its formula on its own: the run arbitrated a family for the
    composition it replaced, not for this one.

    :param session: The edit's session; the rows read are locked.
    :param row: The row being curated.
    :return: The link, and the message's note on it.
    """
    row.owner_peak_assignment_id = None
    formula = row.assigned_formula
    if not formula or row.role not in (ROLE_M0, ROLE_ISO_CHILD):
        return HandLink()
    # The row's new formula and role, and the family this edit moved, are
    # still pending on the session; read without a flush, a session that does
    # not autoflush would answer with the ledger as it stood before the edit.
    await session.flush()
    m0_rows = [
        m0
        for m0 in await _run_m0_rows(session, row)
        if m0.peak_assignment_id != row.peak_assignment_id
    ]
    if row.role == ROLE_ISO_CHILD:
        if len(m0_rows) != 1:
            note = (
                f" No M0 of {formula} in this run, so the line stands on its own."
                if not m0_rows
                else f" {len(m0_rows)} M0 rows of {formula}, left on its own."
            )
            return HandLink(found=len(m0_rows), note=note)
        (owner,) = m0_rows
        row.owner_peak_assignment_id = owner.peak_assignment_id
        _show_owner_level(row, owner)
        return HandLink(
            owner=owner,
            found=1,
            note=f" Linked under {formula}'s M0 at m/z {owner.sample_peak_mz:.4f}.",
        )
    if m0_rows:
        # Another M0 of the compound: which of them a line is of, the run
        # cannot say, so neither takes it.
        return HandLink()
    candidates = (
        (
            await session.execute(
                select(PeakAssignment)
                .where(
                    PeakAssignment.peak_assignment_run_id == row.peak_assignment_run_id,
                    PeakAssignment.role == ROLE_ISO_CHILD,
                    PeakAssignment.owner_peak_assignment_id.is_(None),
                    PeakAssignment.source == SOURCE_MANUAL,
                    PeakAssignment.assigned_formula == formula,
                    PeakAssignment.ionization_mechanism_id.is_not_distinct_from(
                        row.ionization_mechanism_id
                    ),
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    linked = [line for line in candidates if is_hand_line(line)]
    for line in linked:
        line.owner_peak_assignment_id = row.peak_assignment_id
        _show_owner_level(line, row)
    note = (
        f" {len(linked)} isotope line{'s' if len(linked) != 1 else ''} assigned "
        f"by hand linked under it, at m/z "
        f"{', '.join(f'{line.sample_peak_mz:.4f}' for line in linked)}."
        if linked
        else ""
    )
    return HandLink(linked=tuple(linked), note=note)


def detach_hand_lines(
    family: list[PeakAssignment], formula: str | None, at: str
) -> tuple[list[PeakAssignment], list[PeakAssignment], str]:
    """Detach the hand lines from a family whose compound is being replaced.

    A hand line keeps everything the person gave it and loses its owner, so
    it stands as a row of its own and shows the level a hand line with no M0
    shows (``identification_level.hand_level``). Its manual block records
    which M0 it left and when. The rest of the family is the caller's to
    demote.

    :param family: The rows the replaced M0 owned.
    :param formula: The formula being replaced, for the message.
    :param at: The edit's instant.
    :return: ``(the rows to demote, the lines detached, the message's note)``.
    """
    demote = [row for row in family if not is_hand_line(row)]
    detached = [row for row in family if is_hand_line(row)]
    for line in detached:
        manual = dict(line.provenance["manual"])
        manual["detached_from"] = line.owner_peak_assignment_id
        manual["detached_at"] = at
        line.owner_peak_assignment_id = None
        record = hand_level({"role": line.role})
        line.provenance = {
            **line.provenance,
            "manual": manual,
            IDENTIFICATION_LEVEL: record,
        }
        line.level = record["level"]
    note = (
        f" {len(detached)} isotope line{'s' if len(detached) != 1 else ''} of "
        f"{formula} assigned by hand, at m/z "
        f"{', '.join(f'{line.sample_peak_mz:.4f}' for line in detached)}, "
        f"detached: {'they keep' if len(detached) != 1 else 'it keeps'} "
        "the formula given by hand and "
        f"{'stand' if len(detached) != 1 else 'stands'} on "
        f"{'their' if len(detached) != 1 else 'its'} own."
        if detached
        else ""
    )
    return demote, detached, note
