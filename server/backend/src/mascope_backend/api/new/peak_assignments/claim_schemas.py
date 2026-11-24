"""The identification claim a verdict body carries, on both verification routes.

A confirmation may name a structure, or at 3b a class, and say what its
evidence showed (``docs/dev/identification_claims.md`` section 5, the claims'
mapping at version 2). The per-sample body (``schemas.VerifyAssignmentBody``)
and the batch-peak one (``schemas.VerifyBatchPeakBody``) take the same four
fields with the same rules, so they are declared once here. The level is not
sent: the server fixes it (``claims.claim_level``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from mascope_backend.api.models.target.compounds.target_compound_pydantic_model import (
    validate_inchikey,
)
from mascope_backend.api.new.peak_assignments.claims import claim_problem


# What a confirmation's evidence showed, which decides the letter of the level
# it states (``claims.EVIDENCE_DETAILS``): msms library | fragments | loss,
# orthogonal separation | data_and_list | class | context_list | homologue,
# pattern split_pinned | split_open. Which words go with which evidence level
# is ``claims.claim_problem``'s rule.
EvidenceDetail = Literal[
    "library",
    "fragments",
    "loss",
    "separation",
    "data_and_list",
    "class",
    "context_list",
    "homologue",
    "split_pinned",
    "split_open",
]
# The line beside a reference standard that takes a claim to level 1
# (``claims.INDEPENDENT_LINES``).
IndependentLine = Literal["msms", "separation", "instrument"]


def as_utc(value):
    """A time without a zone, read as UTC."""
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _optional_text(value):
    """A name stripped; a blank one is no name."""
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


class ClaimFields(BaseModel):
    """The claim a verdict body may carry. Each body's own ``model_validator``
    calls :func:`refuse_bad_claim` once its verdict and evidence level are set."""

    claimed_inchikey: str | None = Field(
        None,
        description=(
            "The InChIKey of the structure the confirmation names: an "
            "identification claim. Only a confirmation names one, and one that "
            "states level 1, 2 or a 3 naming a candidate must. A class claim "
            "(3b) names none."
        ),
    )
    claimed_name: str | None = Field(
        None,
        description=(
            "The name of the structure named, with claimed_inchikey; on a class "
            "claim (3b), the class it names, alone."
        ),
    )
    evidence_detail: EvidenceDetail | None = Field(
        None,
        description=(
            "What the confirmation's evidence showed, which decides the level it "
            "states: on msms, library (2a), fragments (2b: two or more that fit "
            "one structure, rivals excluded) or loss (3b: one loss, a class); on "
            "orthogonal, separation (2b: one structure left), data_and_list "
            "(3a), class (3b), context_list (3c) or homologue (3d); on pattern, "
            "split_pinned (4a) or split_open (4b, also when left out). Required "
            "on msms and orthogonal."
        ),
    )
    independent_line: IndependentLine | None = Field(
        None,
        description=(
            "On a reference_standard confirmation - an authentic standard on "
            "this instrument with the same reagent chemistry - the independent "
            "line beside it: msms, separation or instrument (the same air on "
            "another instrument). With one the claim states level 1; without, "
            "2b, its lift 'an independent line'."
        ),
    )

    _validate_claimed_inchikey = field_validator("claimed_inchikey")(validate_inchikey)
    _strip_claimed_name = field_validator("claimed_name", mode="before")(_optional_text)


def refuse_bad_claim(body) -> None:
    """Refuse a body whose claim breaks a rule of ``claims.claim_problem``."""
    problem = claim_problem(
        body.verdict,
        body.evidence_level,
        body.claimed_inchikey,
        body.claimed_name,
        body.evidence_detail,
        body.independent_line,
    )
    if problem:
        raise ValueError(problem)
