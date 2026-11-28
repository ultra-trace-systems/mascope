"""The assignment vocabulary the peak assignments API's schemas share."""

from typing import Annotated, Literal

from pydantic import AfterValidator, BeforeValidator

from mascope_backend.api.new.peak_assignments.claim_schemas import (
    refuse_engine_level_one,
)
from mascope_backend.api.new.peak_assignments.tiers import normalize_tier


# Verification vocabulary (verification-calibration loop V1). Verdict is the label;
# evidence_level records why the user is confident -- the guardrail that lets the eventual
# calibration weight a reference-standard confirmation above a visual guess.
Verdict = Literal["confirmed", "rejected", "unsure"]
EvidenceLevel = Literal["reference_standard", "msms", "orthogonal", "pattern", "visual"]

# The assignment vocabulary, mirrored from the engine's constants. Typed rather
# than free strings so a misspelled filter is a 422 naming the accepted values,
# not a 200 with an empty ledger that reads as "this sample has no such peaks".
#
# The tier normalizes legacy spellings before that check (see
# `tiers.LEGACY_TIER_ALIASES`), and one Annotated type covers both directions on
# purpose: it types the imported row's tier AND the ledger's read filter, so a
# client that still says 'identified' - an external engine publishing to the
# older spec, or an SDK reader filtering by what its documentation taught it -
# is answered rather than 422'd, and stores or reads the current spelling.
AssignmentTier = Annotated[
    Literal["assigned", "candidate", "below_assignability", "unassigned"],
    BeforeValidator(normalize_tier),
]
AssignmentRole = Literal["M0", "iso_child", "reagent", "artifact", "unassigned"]
# 'manual' is a peer of the two stages rather than a flag beside them: it says
# which decision produced the row, and on a curated row that decision was a
# person's. It is in this shared literal - which types the ledger's read filter
# AND an imported row - so overrides are filterable in the ledger and survive a
# round trip through export/import (and, later, a copy between samples).
#
# 'reagent' is a peer for the same reason and says something different again:
# the row came from the reagent pre-pass, which assigns no formula but declares
# the peak to be the source's own chemistry. Filterable in the ledger because
# "show me what the source made" is the question that separates a bright
# residual from a real one.
# ...or the artifact pre-pass, which says the same about the instrument: the
# peak is ringing around a very intense neighbour rather than a species.
AssignmentSource = Literal["database", "untargeted", "manual", "reagent", "artifact"]
# The identification level's scale, shared with the reference engine (decision
# 38 of the assignment quality plan; ``identification_level.LEVELS``):
# Schymanski's numbers with a letter for the kind of evidence. Typed so a level
# outside the scale - on the ledger's filter or on an import - is a 422 naming
# the accepted values.
IdentificationLevel = Literal[
    "1", "2a", "2b", "3a", "3b", "3c", "3d", "4a", "4b", "5a", "5b"
]
# An import's engine_level: the scale without level 1, a person's claim.
EngineLevel = Annotated[IdentificationLevel, AfterValidator(refuse_engine_level_one)]
