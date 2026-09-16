"""A line in doubt, read as an isotopologue of the formula that predicts it.

The envelope-neighbour rule (:mod:`tiering`) finds monoisotopic rows that sit on
a line a committed neighbour's envelope predicts, no taller than that line could
make them. Such a row says the spectrum holds two things where the evidence
holds one. Where the neighbour is a formula the run holds at ``assigned``, the
line is read as that formula's isotopologue instead, at ``candidate``: whatever
made the run commit it as a compound of its own is what puts the reading in
doubt, and under doubt the run says candidate (decision 18 of the assignment
quality plan). Where the neighbour is itself at candidate, the row stays what it
was and its reason says so.

This module does the row surgery: what a claimed row becomes, and what happens
to the isotopologues it carried as a compound of its own. Which rows are claimed
is the tiering pass's call, because only there are the neighbour's final tier
and the line's tracking both known. The service applies the claims and runs the
passes again over the ledger they leave.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

from mascope_backend.api.new.peak_assignments.engine import (
    ROLE_ISO_CHILD,
    SOURCE_DATABASE,
    _isotope_offset_label,
    fit_isotope_formula,
)
from mascope_backend.api.new.peak_assignments.tiers import TIER_CANDIDATE
from mascope_tools.composition.finder import replace_atom_with_isotope


#: The provenance block a claimed row carries: the line it is read as, how its
#: mass error tracks the owner's, and the reading it displaced.
ENVELOPE_CLAIM = "envelope_claim"


@dataclass(frozen=True)
class ClaimedLine:
    """One peak a claim reads as a line of its owner's envelope.

    :param row_id: The row on the peak.
    :param label: The line, as the envelope predictor names it (``18O``,
        ``13C+81Br``).
    :param line_mz: Where the owner's envelope puts the line.
    :param share: The line's predicted height, relative to the owner's own line.
    :param tracking: How the peak's mass error follows the owner's
        (``mass_gate.tracking_of``): it tracks, or it is in doubt.
    """

    row_id: str
    label: str
    line_mz: float
    share: float
    tracking: str


@dataclass(frozen=True)
class EnvelopeClaim:
    """A monoisotopic row read as a line of an assigned neighbour.

    :param owner_id: The neighbour whose envelope predicts the line.
    :param line: The claimed row, as a line of that envelope.
    :param carried: The claimed row's own isotopologues that the owner's
        envelope predicts too. They go with it.
    :param released: The claimed row's own isotopologues that it does not. They
        belonged to a reading the run no longer holds, so they leave the ledger
        the way an isotopologue whose ion committed no monoisotopic peak does,
        and their peaks are left unassigned.
    """

    owner_id: str
    line: ClaimedLine
    carried: tuple[ClaimedLine, ...] = ()
    released: tuple[str, ...] = ()

    @property
    def row_id(self) -> str:
        return self.line.row_id


def is_claimed(row: dict) -> bool:
    """Whether a row is a line some claim read as an isotopologue."""
    provenance = row.get("provenance")
    return isinstance(provenance, dict) and isinstance(
        provenance.get(ENVELOPE_CLAIM), dict
    )


def apply_claims(
    assignments: list[dict],
    claims: Iterable[EnvelopeClaim],
    *,
    max_alternatives: int,
) -> list[dict]:
    """Read every claimed row as its owner's line, in place.

    :param assignments: The committed rows of one sample.
    :param claims: What the tiering pass found claimable.
    :param max_alternatives: Cap on stored alternatives per row. A claimed row's
        first alternative is the reading it displaced, so promoting it by hand
        puts the row back.
    :return: The rows that stay on the ledger: all of them but the released
        isotopologues.
    """
    by_id = {str(row.get("peak_assignment_id")): row for row in assignments}
    released: set[str] = set()
    for claim in claims:
        row, owner = by_id.get(claim.row_id), by_id.get(claim.owner_id)
        if row is None or owner is None:
            continue
        _read_as_line(row, owner, claim.line, max_alternatives=max_alternatives)
        for line in claim.carried:
            child = by_id.get(line.row_id)
            if child is not None:
                _read_as_line(
                    child,
                    owner,
                    line,
                    max_alternatives=max_alternatives,
                    carried_with=claim.row_id,
                )
        released.update(claim.released)
    return [
        row for row in assignments if str(row.get("peak_assignment_id")) not in released
    ]


def _reading_of(row: dict) -> dict:
    """What a row said before a claim read it as another formula's line.

    Shaped as an alternative, so the inspector lists it with the row's other
    readings and promoting it by hand commits it again.
    """
    provenance = row.get("provenance") or {}
    return {
        "assigned_formula": row.get("assigned_formula"),
        "ion_formula": row.get("ion_formula"),
        "ionization_mechanism_id": row.get("ionization_mechanism_id"),
        "isotope_label": row.get("isotope_label"),
        "target_compound_id": row.get("target_compound_id"),
        "target_ion_id": row.get("target_ion_id"),
        "fit_score": row.get("fit_score"),
        "mz_error_ppm": row.get("mz_error_ppm"),
        "plausibility": provenance.get("plausibility"),
        "source": row.get("source"),
        # What tells a reader this is the reading the claim took the peak from,
        # rather than a rival the peak's own election considered.
        "displaced_by_claim": True,
        **(
            {"reference_identities": provenance["reference_identities"]}
            if provenance.get("reference_identities")
            else {}
        ),
    }


def _line_names(
    owner: dict, line: ClaimedLine
) -> tuple[str | None, str | None, str | None]:
    """The label and formulas a claimed line is written with.

    In its owner's convention, so a claimed line reads like the lines its owner
    committed itself: a target list's isotopologue is labelled by its nominal
    mass offset and keeps the ion's formula, with the isotopologue's own formula
    beside it, and an untargeted one is labelled by its substitution and carries
    the isotopologue's formula as its ion.
    """
    ion = owner.get("ion_formula")
    try:
        isotopologue = replace_atom_with_isotope(str(ion), line.label) if ion else None
    except ValueError:
        isotopologue = None
    if owner.get("source") == SOURCE_DATABASE:
        return (
            _isotope_offset_label(line.line_mz, _float(owner.get("sample_peak_mz"))),
            ion,
            fit_isotope_formula(isotopologue),
        )
    return line.label, isotopologue or ion, None


def _read_as_line(
    row: dict,
    owner: dict,
    line: ClaimedLine,
    *,
    max_alternatives: int,
    carried_with: str | None = None,
) -> None:
    """Rewrite one row as its owner's line, keeping what it said before."""
    displaced = _reading_of(row)
    previous = row.get("provenance") or {}
    owner_provenance = owner.get("provenance") or {}
    label, ion_formula, isotope_formula = _line_names(owner, line)
    peak_mz = _float(row.get("sample_peak_mz"))
    intensity = _float(row.get("sample_peak_intensity"))
    owner_intensity = _float(owner.get("sample_peak_intensity"))
    observed_share = (
        intensity / owner_intensity
        if intensity is not None and owner_intensity
        else None
    )
    claim = {
        "line": line.label,
        "predicted_mz": round(line.line_mz, 6),
        "predicted_share": round(line.share, 6),
        "observed_share": None if observed_share is None else round(observed_share, 6),
        "tracking": line.tracking,
        "displaced": {
            "assigned_formula": row.get("assigned_formula"),
            "ion_formula": row.get("ion_formula"),
            "isotope_label": row.get("isotope_label"),
            "source": row.get("source"),
            # The tier the reading's evidence gave it. A claim is applied to the
            # rows as the stages built them, before any rule of the run held
            # them lower, so a reading the run showed at candidate can read
            # assigned here.
            "tier": row.get("tier"),
            "evidence": previous.get("evidence"),
        },
    }
    if carried_with is not None:
        # An isotopologue of the claimed row, which the owner's envelope
        # predicts too: it goes with the peak it belonged to.
        claim["carried_with"] = carried_with
    row.update(
        role=ROLE_ISO_CHILD,
        assigned_formula=owner.get("assigned_formula"),
        ion_formula=ion_formula,
        ionization_mechanism_id=owner.get("ionization_mechanism_id"),
        isotope_label=label,
        isotope_formula=isotope_formula,
        source=owner.get("source"),
        # A line of the owner's ion is measured by the owner's fit, as every
        # isotopologue the stages commit is.
        fit_score=owner.get("fit_score"),
        mz_error_ppm=(
            None if peak_mz is None else (peak_mz - line.line_mz) / line.line_mz * 1e6
        ),
        abundance_error=(
            None
            if observed_share is None or line.share <= 0
            else observed_share / line.share - 1.0
        ),
        tier=TIER_CANDIDATE,
        target_compound_id=owner.get("target_compound_id"),
        target_ion_id=owner.get("target_ion_id"),
        owner_peak_assignment_id=owner.get("peak_assignment_id"),
        alternatives=(
            [displaced, *(row.get("alternatives") or [])][: max_alternatives or 0]
            or None
        ),
        provenance={
            key: owner_provenance[key]
            for key in ("plausibility", "evidence", "score_version")
            if key in owner_provenance
        }
        | {ENVELOPE_CLAIM: claim},
    )


def _float(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
