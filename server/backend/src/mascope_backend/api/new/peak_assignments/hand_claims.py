"""
The isotope lines a hand M0 claims, and the ones it names and leaves.

A person who assigns a peak as a compound's monoisotopic line (``M0``) is asked
whether the spectrum holds the lines its formula predicts
(``curation._persons_choice``, ``tiering.expected_lines``). That check finds
each line at a peak, and the run's ledger has a row on that peak. Where the
row is **free**, the hand M0 claims it as its own line, written as a run
writes one: an ``iso_child`` owned by the M0, with the line's label, its
isotopologue's formula, the line check's mass and abundance errors, a tier no
higher than the M0's and the M0's level (MAS-1312, option C).

A row is free when nobody has decided it: its role is ``unassigned`` and its
source is not ``manual``. Any other row is named in the edit's message and left
as it is: another formula's M0 or line, a reagent or artifact row, and a peak
a person, or an earlier override, left unassigned.

A line is claimed only as a run would take it. The line check finds a line
wherever its window holds a peak: the run's match window about the line and
about the line moved by the M0's own mass error, the tallest peak there, at
any height. A run takes a line as its ion's when the line's own match is
within the match window of the predicted m/z and its height passes the line
model's height test at the run's line-share tolerance
(``engine.line_heights_within``, ``line_model.within_intensity_window``). On an
Orbitrap run that test reads a line by its scan presence and holds it to the
fixed window; the claim, which has only the peak list's share, holds every
Orbitrap line to that window, and opens it to the curve's lower end on a TOF
run only. A found line that misses either is named, not claimed.

A claimed line is part of the M0's click. When the M0's compound is replaced,
the line is demoted and archived with the rest of its family, unlike an
isotope line a person assigned on its own peak (``hand_lines``), which is
detached. The two are told apart by the action on the line's manual block:
:data:`ACTION_CLAIM_LINE` here. The undo that commits the compound back
restores the line with ``source: manual`` (:func:`restored_source`).
"""

import math
from collections.abc import Callable
from typing import NamedTuple

import numpy as np
from sqlalchemy import select

from mascope_backend.api.new.peak_assignments.engine import fit_isotope_formula
from mascope_backend.api.new.peak_assignments.hand_lines import show_owner_level
from mascope_backend.api.new.peak_assignments.row_terms import (
    ENVELOPE_TOLERANCE_KEY,
    ROLE_ISO_CHILD,
    ROLE_M0,
    ROLE_UNASSIGNED,
    SOURCE_DATABASE,
    SOURCE_MANUAL,
    SOURCE_UNTARGETED,
    TIERING_KEY,
)
from mascope_backend.api.new.peak_assignments.tiering import (
    LINE_FOUND,
    PREDICTED_ENVELOPE,
    LineGate,
)
from mascope_backend.db import PeakAssignment, PeakAssignmentRun
from mascope_tools.composition.envelope_tolerance import EnvelopeTolerance
from mascope_tools.composition.finder import replace_atom_with_isotope
from mascope_tools.composition.line_model import within_intensity_window


#: The action a claimed line's manual block records: the M0's edit claimed it.
ACTION_CLAIM_LINE = "claim_isotope_line"

#: The sources a restored isotopologue may claim besides ``manual`` on a line
#: a hand M0 claimed. The archive is JSON an imported run could have written
#: anything into, so anything else comes back sourceless rather than
#: mislabelled.
ENGINE_SOURCES = (SOURCE_DATABASE, SOURCE_UNTARGETED)


class LineClaims(NamedTuple):
    """What a hand M0's lines came to.

    ``claimed`` are the rows it took; ``note`` is what the edit's message says
    of them and of the lines it left.
    """

    claimed: tuple[PeakAssignment, ...] = ()
    note: str = ""


def is_claimed_line(provenance) -> bool:
    """Whether a row's provenance records a line a hand M0 claimed."""
    manual = (provenance or {}).get("manual") if isinstance(provenance, dict) else None
    return isinstance(manual, dict) and manual.get("action") == ACTION_CLAIM_LINE


def restored_source(source, provenance) -> str | None:
    """The source an isotopologue put back from its owner's archive takes.

    An engine's source, as it was; ``manual`` only on a line a hand M0
    claimed, which was a person's click and stays one; else none.

    :param source: The source the archive recorded.
    :param provenance: The provenance the archive puts back on the row.
    """
    if source in ENGINE_SOURCES:
        return source
    if source == SOURCE_MANUAL and is_claimed_line(provenance):
        return SOURCE_MANUAL
    return None


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _run_would_take(line: dict, gate: LineGate, tolerance: EnvelopeTolerance) -> str:
    """Why a run's own match would not take a found line; empty where it would.

    :param line: The line check's entry for the line.
    :param gate: The run's line gate, whose match window the line is held to.
    :param tolerance: The run's line-share tolerance.
    """
    error = _number(line.get("mz_error_ppm"))
    if error is None or abs(error) > gate.mz_tolerance_ppm:
        shown = "unknown" if error is None else f"{error:+.1f} ppm"
        return (
            f"its peak is {shown} off the line, outside the run's "
            f"{gate.mz_tolerance_ppm:g} ppm window"
        )
    height = _number(line.get("abundance_error"))
    expected = _number(line.get("expected_snr"))
    if height is None or not bool(
        within_intensity_window(
            np.array([height]),
            np.array([np.nan if expected is None else expected]),
            tolerance,
            # An Orbitrap run reads a line's share by its scan presence, the
            # dilution the curve allows for taken out, and holds it to the
            # fixed window. A peak list's share cannot be read so, so the claim
            # holds it to the fixed window too: no lower than the run would.
            None if gate.tof else np.array([True]),
        )[0]
    ):
        observed = _number(line.get("observed_share"))
        shown = "unknown" if observed is None else f"{observed:.1%}"
        return (
            f"its peak is {shown} of the M0's, against a predicted "
            f"{float(line.get('predicted_share') or 0.0):.1%}, which the run's "
            "line match would not take"
        )
    return ""


def _reads(row: PeakAssignment | None) -> str:
    """What a row the claim leaves reads now, for the message."""
    if row is None:
        return "this run has no row on its peak"
    if row.role == ROLE_UNASSIGNED:
        return "unassigned, left so by hand or by an override"
    if row.role == ROLE_M0:
        return f"it reads {row.assigned_formula}"
    if row.role == ROLE_ISO_CHILD:
        label = f"the {row.isotope_label}" if row.isotope_label else "an isotope"
        return f"it reads {label} line of {row.assigned_formula}"
    return f"it reads a {row.role} row"


def _isotopologue(ion: str | None, label: str) -> str | None:
    """The line's own formula, or None where the label names none."""
    try:
        return (
            fit_isotope_formula(replace_atom_with_isotope(ion, label)) if ion else None
        )
    except ValueError:
        return None


def _claim(
    line_row: PeakAssignment,
    m0: PeakAssignment,
    line: dict,
    *,
    user_id: int | None,
    at: str,
    push_previous: Callable[[PeakAssignment], list | None],
) -> None:
    """Write a free row as the M0's line, as a run writes one."""
    label = str(line["line"])
    isotopologue = _isotopologue(m0.ion_formula, label)
    owner_provenance = m0.provenance or {}
    line_row.alternatives = push_previous(line_row)
    line_row.provenance = {
        **{
            key: owner_provenance[key]
            for key in ("plausibility", "evidence", "score_version")
            if key in owner_provenance
        },
        "manual": {
            "action": ACTION_CLAIM_LINE,
            "claimed_by": m0.peak_assignment_id,
            "user_id": user_id,
            "at": at,
        },
    }
    line_row.role = ROLE_ISO_CHILD
    line_row.owner_peak_assignment_id = m0.peak_assignment_id
    line_row.source = SOURCE_MANUAL
    line_row.assigned_formula = m0.assigned_formula
    # A hand line's shape (MAS-1316): the line's own formula is its ion.
    line_row.ion_formula = isotopologue
    line_row.isotope_formula = isotopologue
    line_row.isotope_label = label
    line_row.ionization_mechanism_id = m0.ionization_mechanism_id
    line_row.target_compound_id = m0.target_compound_id
    line_row.target_ion_id = m0.target_ion_id
    # Measured by the M0's fit, as every line a run commits is.
    line_row.fit_score = m0.fit_score
    line_row.mz_error_ppm = _number(line.get("mz_error_ppm"))
    line_row.abundance_error = _number(line.get("abundance_error"))
    line_row.tier = m0.tier
    line_row.engine_tier = None
    line_row.engine_level = None
    show_owner_level(line_row, m0)


async def claim_found_lines(
    session,
    m0: PeakAssignment,
    run: PeakAssignmentRun,
    *,
    user_id: int | None,
    at: str,
    push_previous: Callable[[PeakAssignment], list | None],
) -> LineClaims:
    """Claim the free rows a hand M0's found lines sit on, and name the rest.

    Called once the M0 holds its formula, tier and level, and its line check's
    record (:data:`tiering.PREDICTED_ENVELOPE`). A row the M0 already owns is
    its line already, and is passed over. The rows are read by run and peak,
    which no edit changes, so the edit's pending changes need no flush.

    :param session: The edit's session; the rows read are locked.
    :param m0: The row being curated.
    :param run: Its run, whose line gate and line-share tolerance a line is
        held to.
    :param user_id: The curating user.
    :param at: The edit's instant, which the M0's own record carries.
    :param push_previous: How a row's earlier state goes onto its
        alternatives, as an override archives a displaced winner.
    :return: The rows claimed, and the message's note on the lines.
    """
    record = (m0.provenance or {}).get(PREDICTED_ENVELOPE)
    config = run.config if isinstance(run.config, dict) else {}
    gate = LineGate.from_record(config.get(TIERING_KEY))
    # Only a monoisotopic row is asked its lines, so only an M0 has a record.
    if not isinstance(record, dict) or gate is None:
        return LineClaims()
    found = [
        line
        for line in record.get("lines") or []
        if isinstance(line, dict)
        and line.get("state") == LINE_FOUND
        and line.get("peak_id")
        and line.get("line")
    ]
    if not found:
        return LineClaims()
    tolerance = EnvelopeTolerance.from_record(config.get(ENVELOPE_TOLERANCE_KEY))
    rows = {
        row.sample_peak_id: row
        for row in (
            await session.execute(
                select(PeakAssignment)
                .where(
                    PeakAssignment.peak_assignment_run_id == m0.peak_assignment_run_id,
                    PeakAssignment.sample_peak_id.in_(
                        [str(line["peak_id"]) for line in found]
                    ),
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    }
    claimed: list[PeakAssignment] = []
    left: list[str] = []
    for line in found:
        row = rows.get(str(line["peak_id"]))
        if row is not None and (
            row.owner_peak_assignment_id == m0.peak_assignment_id
            or row.peak_assignment_id == m0.peak_assignment_id
        ):
            continue
        where = (
            f"{line['line']} line found at m/z "
            f"{row.sample_peak_mz if row is not None else line['predicted_mz']:.4f}"
        )
        if row is None or row.role != ROLE_UNASSIGNED or row.source == SOURCE_MANUAL:
            left.append(f"{where}, not claimed: {_reads(row)}")
            continue
        missed = _run_would_take(line, gate, tolerance)
        if missed:
            left.append(f"{where}, not claimed: {missed}")
            continue
        _claim(row, m0, line, user_id=user_id, at=at, push_previous=push_previous)
        claimed.append(row)
    notes = []
    if claimed:
        notes.append(
            f" Claimed {len(claimed)} isotope line{'s' if len(claimed) != 1 else ''} "
            "of it: "
            + ", ".join(
                f"{row.isotope_label} at m/z {row.sample_peak_mz:.4f}"
                for row in claimed
            )
            + "."
        )
    notes.extend(f" {entry}." for entry in left)
    return LineClaims(claimed=tuple(claimed), note="".join(notes))
