"""
Unit tests for the tier vocabulary, and the pre-rename spelling it still takes.

The top tier was narrowed from 'identified' to 'assigned' because an
identification is read as MS2 or reference-standard evidence, which a formula
fit does not provide. Stored rows migrate with the rename; payloads in flight do
not - an external engine publishes against the spec it was built for, an SDK
client filters with the tier its own documentation taught it, and a ledger
exported before the rename is re-imported exactly as it was written. So the old
spelling is accepted on the way in, and only the current one is ever stored.

This covers the pure functions and the models that apply them. What the
endpoints do with an aliased payload needs a database and is pinned by the
import and read suites instead.
"""

import pytest

from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig
from mascope_backend.api.new.peak_assignments.schemas import AssignSamplePeaksBody
from mascope_backend.api.new.peak_assignments.tiers import (
    TIER_ASSIGNED,
    TIERS,
    normalize_tier,
    normalize_tier_bands,
)


class TestNormalizeTier:
    """One tier spelling in, the stored vocabulary out."""

    def test_the_pre_rename_top_tier_maps_onto_the_stored_one(self):
        assert normalize_tier("identified") == TIER_ASSIGNED

    @pytest.mark.parametrize("tier", TIERS)
    def test_a_current_tier_is_left_alone(self, tier):
        assert normalize_tier(tier) == tier

    @pytest.mark.parametrize("value", ["nonsense", "", None, 5, 0.8, ["identified"]])
    def test_anything_else_is_returned_untouched(self, value):
        """This runs ahead of validation, so it must not swallow bad input.

        A wrongly typed or misspelled tier has to reach the validator to be
        reported as the 422 it is; mapping it to something valid here would turn
        a typo into a silently mis-tiered row.
        """
        assert normalize_tier(value) == value


class TestNormalizeTierBands:
    """The bands are keyed by tier, so the upper key carries the rename too."""

    def test_the_legacy_key_is_renamed_and_the_rest_is_untouched(self):
        assert normalize_tier_bands({"identified": 0.8, "candidate": 0.5}) == {
            "assigned": 0.8,
            "candidate": 0.5,
        }

    def test_current_bands_pass_through(self):
        bands = {"assigned": 0.8, "candidate": 0.5}

        assert normalize_tier_bands(bands) == bands

    def test_no_bands_stay_no_bands(self):
        """A run predating the column carries null, which is not an error here."""
        assert normalize_tier_bands(None) is None

    @pytest.mark.parametrize(
        "bands",
        [
            {"identified": 0.6, "assigned": 0.8, "candidate": 0.5},
            {"assigned": 0.8, "identified": 0.6, "candidate": 0.5},
        ],
    )
    def test_both_spellings_resolve_to_the_current_one_either_way_round(self, bands):
        """One band named twice is not two bands, whatever order they arrive in."""
        assert normalize_tier_bands(bands) == {"assigned": 0.8, "candidate": 0.5}

    def test_the_argument_is_not_mutated(self):
        """It is read off a run row, which the caller goes on using."""
        bands = {"identified": 0.8, "candidate": 0.5}

        normalize_tier_bands(bands)

        assert bands == {"identified": 0.8, "candidate": 0.5}


class TestTheRunConfigBandParsesUnderItsOldName:
    """A client pinned to `identified_threshold` still tiers the run it asked for.

    The band is a field of the assign request body, so without the alias such a
    client would not be refused - it would silently fall back to the default and
    get a run tiered at thresholds it never chose, which is the failure mode
    hardest to notice from the outside.
    """

    def test_an_assign_request_using_the_old_field_name_parses(self):
        body = AssignSamplePeaksBody.model_validate(
            {"config": {"identified_threshold": 0.9}}
        )

        assert body.config.assigned_threshold == 0.9

    def test_the_current_field_name_still_works(self):
        assert PeakAssignmentConfig(assigned_threshold=0.9).assigned_threshold == 0.9
        assert (
            PeakAssignmentConfig.model_validate(
                {"assigned_threshold": 0.9}
            ).assigned_threshold
            == 0.9
        )

    def test_only_the_current_name_is_stored(self):
        """`model_dump` lands verbatim in the run's config column.

        A legacy key surviving into it would put the old vocabulary back into
        the database a row at a time, on runs computed after the rename.
        """
        stored = PeakAssignmentConfig.model_validate(
            {"identified_threshold": 0.9}
        ).model_dump()

        assert stored["assigned_threshold"] == 0.9
        assert "identified_threshold" not in stored
