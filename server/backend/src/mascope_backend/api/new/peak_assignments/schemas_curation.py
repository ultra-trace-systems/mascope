"""Manual curation of a sample's row: promote a runner-up or set an assignment."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from mascope_backend.api.new.peak_assignments.schemas_ledger import (
    PeakAssignmentDetailRecord,
)


# ---------------------------------------------------------------------------
# Manual curation
# ---------------------------------------------------------------------------


class PromoteAlternativeBody(BaseModel):
    """Commit one of a row's stored runner-ups as its assignment.

    The candidate is named by its position in the row's ``alternatives`` list,
    because an alternative has no id of its own - the list is a JSON blob whose
    entries the three producers write in three different shapes. Position is
    only meaningful against the list the caller actually read, which is what
    ``expected_formula`` is for.
    """

    action: Literal["promote_alternative"]
    alternative_index: int = Field(
        ge=0,
        description=(
            "Index into the assignment's `alternatives` list, as served by the "
            "detail endpoint."
        ),
    )
    expected_formula: str | None = Field(
        None,
        max_length=256,
        description=(
            "The formula the caller believes sits at that index. Supplied, it "
            "is checked before anything is written and a mismatch is refused "
            "with 409 - so a second curator's override, landing between the "
            "read and this call, cannot silently turn a click on one candidate "
            "into a commitment to another."
        ),
    )


class SetAssignmentBody(BaseModel):
    """Commit a composition the caller supplies, for the re-search case.

    The scores are the ones this server's own composition search reported for
    the formula against this sample; they are recorded as the caller's
    declaration, are re-tiered here under the run's bands, and are labelled in
    provenance with where they came from. The calibrated fields (`p_correct`
    and friends) are deliberately absent: they are this server's judgement
    about its own engine's arbitration and are never taken from a client - the
    same rule the import path enforces with
    ``strip_server_owned_provenance``.
    """

    action: Literal["set_assignment"]
    assigned_formula: str = Field(
        min_length=1,
        max_length=256,
        description="Neutral formula to commit for this peak.",
    )
    ionization_mechanism_id: str = Field(
        min_length=1,
        max_length=16,
        description=(
            "The adduct the formula is assigned under. Required: a formula "
            "without its mechanism is half an assignment, and the mechanism is "
            "part of a verification's identity."
        ),
    )
    ion_formula: str | None = Field(None, max_length=4096)
    isotope_label: str | None = Field(
        None,
        max_length=64,
        description=(
            "Which isotopologue of the ion this peak is: 'M0' (or omitted) for "
            "the main one, otherwise 'M+1', 'M+2' ... A row labelled anything "
            "but M0 is committed as an `iso_child`, so an isotopologue is never "
            "recorded as a compound's main peak."
        ),
    )
    isotope_formula: str | None = Field(None, max_length=256)
    fit_score: float | None = Field(None, ge=0.0, le=1.0)
    mz_error_ppm: float | None = Field(None, allow_inf_nan=False)
    abundance_error: float | None = Field(None, allow_inf_nan=False)
    # No `plausibility` here on purpose: it is a pure function of the formula
    # (Seven Golden Rules), so the server computes it from what is committed
    # rather than accepting a number about chemistry from a caller.


#: The two curation actions, discriminated on `action` so an unknown one is a
#: 422 naming the accepted values rather than a silently ignored body.
CurateAssignmentBody = Annotated[
    PromoteAlternativeBody | SetAssignmentBody, Field(discriminator="action")
]


class AssignmentCurationResponse(BaseModel):
    """The rows a manual override rewrote.

    `data[0]` is the curated row. After it come the isotopologue rows the same
    edit moved, in two groups and always in this order:

    1. The isotopologues the override **demoted**: the family of the
       formula it replaced, stripped to `unassigned` rather than left claiming
       a compound their M0 no longer carries. Empty when the edit commits the
       formula and mechanism the row already held, since then the family still
       stands for exactly what it stood for.
    2. The isotopologues it **restored**: the family of the compound now being
       committed, put back from the archive an earlier override of this row
       left behind. This is what makes promoting the displaced winner back a
       real undo rather than one that revives the M0 and leaves its isotopologues
       unassigned and ownerless.

    A demoted isotopologue that someone has curated by hand since is deliberately
    not restored, and so is not in `data` either - their judgement is newer
    than the undo. How many were left alone that way is in `message`.
    `message` counts a second group apart from those, and it means the
    opposite: isotopologues the undo could not put back at all - the row gone from
    this run, or the state archived for it unusable - which are missing from
    `data` not out of restraint towards a row somebody else now owns but
    because the restore did not reach them. The curated row's
    `provenance.manual` carries the ids behind both, under `restore_skipped`
    and `restore_failed`, beside the `restored` ids of the ones that did go
    back.

    Full detail records throughout, so a client can refresh every row the edit
    touched without a follow-up read.
    """

    status: str = "success"
    message: str
    results: int
    data: list[PeakAssignmentDetailRecord]
