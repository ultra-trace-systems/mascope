"""
The message a hand edit of a ledger row answers with (``curation``).

It says what the edit did beyond the row: the isotopologues it demoted,
restored, left alone or could not reach, the isotope lines it linked or
detached (``hand_lines``), and what asking the row's lines came to. Each of
those is also on the rows themselves; the message is how the person who
clicked learns of it.
"""

from typing import NamedTuple


class EditOutcome(NamedTuple):
    """What one edit did, as the message reports it."""

    assigned: str | None
    previous_formula: str | None
    sample_peak_id: str
    sample_name: str
    demoted: int = 0
    restored: int = 0
    restore_skipped: int = 0
    restore_failed: int = 0
    #: The absent-line reason, where a line the row predicts is missing.
    line_reason: dict | None = None
    #: Whether the run asks lines and the sample's peaks could not be read.
    lines_unread: bool = False
    #: ``hand_lines``' notes: the row's link and the lines it detached.
    link_note: str = ""
    detach_note: str = ""


def _plural(count: int) -> str:
    return "s" if count != 1 else ""


def edit_message(outcome: EditOutcome) -> str:
    """The edit's message.

    :param outcome: What the edit did.
    :return: One paragraph, the row first.
    """
    o = outcome
    notes = []
    # Said here as well as in the row's reasons: the tier the person sees is
    # not the one the evidence alone gave, and this says why.
    if o.line_reason is not None:
        notes.append(
            f" Its {o.line_reason['line']} line is not in the spectrum, which "
            "holds it at candidate at most."
        )
    elif o.lines_unread:
        notes.append(
            " Its isotope lines were not checked: the sample's peaks could not be read."
        )
    notes.append(o.link_note)
    if o.demoted:
        notes.append(
            f" {o.demoted} isotopologue{_plural(o.demoted)} of "
            f"'{o.previous_formula}' demoted to unassigned."
        )
    notes.append(o.detach_note)
    if o.restored:
        notes.append(
            f" {o.restored} isotopologue{_plural(o.restored)} of "
            f"'{o.assigned}' restored."
        )
    if o.restore_skipped:
        notes.append(
            f" {o.restore_skipped} demoted isotopologue"
            f"{_plural(o.restore_skipped)} left as "
            f"{'they are' if o.restore_skipped != 1 else 'it is'}, curated by "
            "hand since."
        )
    # Said out loud rather than left to the provenance blob: without it the
    # message would report an undo while an isotopologue stayed demoted, and
    # the person clicking has no other way to learn that.
    if o.restore_failed:
        notes.append(
            f" {o.restore_failed} demoted isotopologue"
            f"{_plural(o.restore_failed)} could not be put back: "
            f"{'their rows are' if o.restore_failed != 1 else 'the row is'} gone "
            "from this run, or the archived state cannot be committed."
        )
    was = f" (was {o.previous_formula!r})" if o.previous_formula else ""
    return (
        f"Assigned '{o.assigned}' to peak {o.sample_peak_id} of sample "
        f"'{o.sample_name}' by hand{was}.{''.join(notes)}"
    )
