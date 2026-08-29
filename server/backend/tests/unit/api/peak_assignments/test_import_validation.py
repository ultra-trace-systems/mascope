"""
Unit tests for the payload rules an imported assignment run is held to.

An imported ledger is data a workspace editor asserts, and these rules are the
line between "the importer's judgement", which is accepted, and "a value this
server presents as its own", which is not. They decide from the payload alone,
so they are tested here without a database or a peak file; the parts that need
either live in the integration suite.
"""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from mascope_backend.api.new.peak_assignments.config import (
    IN_APP_ENGINE,
    MAX_IMPORT_JSON_BYTES,
)
from mascope_backend.api.new.peak_assignments.engine import tier_for_score
from mascope_backend.api.new.peak_assignments.import_validation import (
    chunk_offset_error,
    coherent_tiers,
    duplicate_peak_ids,
    is_chunk_replay,
    json_size_error,
    normalize_engine,
    owner_link_errors,
    owner_of_owner_error,
    strip_server_owned_provenance,
    tier_coherence_error,
    unknown_peak_ids,
    unresolved_owner_error,
)
from mascope_backend.api.new.peak_assignments.schemas import ImportAssignmentRow
from mascope_backend.api.new.peak_assignments.service import _provenance_scalars
from mascope_backend.db import PeakAssignment


def _row(sample_peak_id: str, owner: str | None = None, role: str = "iso_child"):
    """A stand-in for a payload row, carrying only the owner-link fields."""
    return SimpleNamespace(
        sample_peak_id=sample_peak_id, owner_sample_peak_id=owner, role=role
    )


class TestEngineIsReserved:
    """The engine name is the provenance badge, so it must not be forgeable.

    Everything the import trust model claims - "first-class but always
    attributable", a reader knowing which engine produced a ledger before
    trusting a tier, imported verdicts staying out of the instrument
    calibration - holds only if a client cannot stamp the in-app identity on
    its own run.
    """

    def test_the_in_app_identity_is_rejected(self):
        name, error = normalize_engine(IN_APP_ENGINE)

        assert name is None
        assert "reserved" in error

    @pytest.mark.parametrize("value", ["MASCOPE", "Mascope", "  mascope  "])
    def test_case_and_padding_do_not_get_around_it(self, value):
        """Otherwise the check is decoration: 'Mascope' would read as in-app."""
        name, error = normalize_engine(value)

        assert name is None
        assert "reserved" in error

    def test_an_external_engine_is_accepted_and_trimmed(self):
        assert normalize_engine("  peaky  ") == ("peaky", None)

    @pytest.mark.parametrize("value", ["", "   "])
    def test_an_empty_name_is_rejected(self, value):
        name, error = normalize_engine(value)

        assert name is None
        assert "non-empty" in error


class TestTierCoherence:
    """A tier must mean what the run's declared bands say it means.

    The two engines share a fit-score scale, but the bands are run config, not
    engine constants. Without this check an engine tiering at 0.6/0.3 publishes
    'assigned' rows at 0.62 that sort, filter and roll up beside in-app
    'assigned' rows meaning something considerably stricter.
    """

    @pytest.mark.parametrize(
        "fit_score,expected",
        [
            (1.0, {"assigned"}),
            (0.8, {"assigned"}),
            (0.79, {"candidate"}),
            (0.5, {"candidate"}),
            (0.49, {"below_assignability"}),
            (0.0, {"below_assignability"}),
            (None, {"unassigned", "below_assignability"}),
        ],
    )
    def test_thresholds_are_inclusive_at_the_band_edge(self, fit_score, expected):
        assert coherent_tiers(fit_score, 0.8, 0.5) == expected

    @pytest.mark.parametrize("fit_score", [1.0, 0.8, 0.79, 0.5, 0.49, 0.01, 0.0])
    def test_a_scored_row_is_tiered_by_the_in_app_engine(self, fit_score):
        """The check delegates rather than restating, so it cannot drift.

        Restating it is what made an earlier version refuse rows the in-app
        engine itself writes. This pins the delegation, not a second copy of
        the thresholds.
        """
        assert coherent_tiers(fit_score, 0.8, 0.5) == {
            tier_for_score(fit_score, possible_threshold=0.5, probable_threshold=0.8)
        }

    def test_a_zero_score_is_below_assignability_even_at_a_zero_band(self):
        """`tier_for_score` guards on `score <= 0` before the bands apply.

        An engine may legitimately declare `candidate: 0.0`; a 0.0 score is
        still not a candidate, and that is what the in-app engine records.
        """
        assert coherent_tiers(0.0, 0.8, 0.0) == {"below_assignability"}
        assert tier_coherence_error("below_assignability", 0.0, 0.8, 0.0) is None
        assert tier_coherence_error("candidate", 0.0, 0.8, 0.0) is not None

    def test_a_coherent_row_passes(self):
        assert tier_coherence_error("assigned", 0.91, 0.8, 0.5) is None

    def test_an_inflated_tier_is_rejected(self):
        """The case the whole rule exists for."""
        error = tier_coherence_error("assigned", 0.62, 0.8, 0.5)

        assert error is not None
        assert "candidate" in error

    def test_a_demoted_tier_is_rejected_too(self):
        """Bands are checked, not merely bounded: a tier is derived, not chosen."""
        assert tier_coherence_error("candidate", 0.95, 0.8, 0.5) is not None

    def test_the_declared_bands_are_what_is_applied(self):
        """0.62 is 'assigned' under 0.6/0.3 and 'candidate' under 0.8/0.5."""
        assert tier_coherence_error("assigned", 0.62, 0.6, 0.3) is None
        assert tier_coherence_error("assigned", 0.62, 0.8, 0.5) is not None

    def test_a_scored_row_cannot_claim_unassigned(self):
        assert tier_coherence_error("unassigned", 0.9, 0.8, 0.5) is not None

    def test_an_unscored_row_is_unassigned_or_below_assignability(self):
        """Both are shapes the in-app ledger writes, so both are accepted.

        A peak nothing was assigned to is 'unassigned'; an assigned row whose
        score came back non-finite is 'below_assignability' (`tier_for_score`
        maps a None score to exactly that). Refusing the second - as an earlier
        version did - refused an engine for reproducing Mascope's own output.
        """
        assert tier_coherence_error("unassigned", None, 0.8, 0.5) is None
        assert tier_coherence_error("below_assignability", None, 0.8, 0.5) is None
        error = tier_coherence_error("assigned", None, 0.8, 0.5)
        assert error is not None
        assert "no fit_score" in error


class TestRowFieldBoundsMatchTheColumns:
    """A payload bound per string column that the ledger table actually has.

    Without one, an over-long value passes every payload rule and fails in the
    insert, where Postgres raises a class-22 data exception that reaches the
    client as a 500 where every other payload rule gives a 422.
    """

    #: Fields a closed vocabulary bounds instead of a length: the Literal is
    #: already narrower than the column, so a max_length would add nothing.
    VOCABULARY_BOUNDED = {"role", "source", "tier"}

    @pytest.mark.parametrize("poison", [float("inf"), float("-inf"), float("nan")])
    def test_no_float_field_accepts_a_non_finite_value(self, poison):
        """The same hole from the numeric side, and it detonates on the *read*.

        `json.loads` accepts the `NaN`/`Infinity` literals and turns the
        RFC-valid `1e999` into `inf`, and a double precision column stores
        both - so the import succeeds and `GET /sample/{id}` then fails to
        render for the whole run, which is 'completed' and so beyond the
        abandon endpoint's reach.
        """
        floats = [
            name
            for name, field in ImportAssignmentRow.model_fields.items()
            if float in (field.annotation, *getattr(field.annotation, "__args__", ()))
        ]
        assert floats, "no float fields found; this test is vacuous"

        base = {
            "sample_peak_id": "peak-0",
            "sample_peak_mz": 181.0707,
            "sample_peak_intensity": 5000.0,
            "role": "M0",
            "tier": "assigned",
        }
        for name in floats:
            with pytest.raises(ValidationError):
                ImportAssignmentRow.model_validate({**base, name: poison})

    def test_row_field_bounds_match_the_columns(self):
        """Fails if a column narrows, widens, or a new string field is added."""
        columns = PeakAssignment.__table__.columns
        for name, field in ImportAssignmentRow.model_fields.items():
            if name in self.VOCABULARY_BOUNDED:
                continue
            width = getattr(getattr(columns.get(name), "type", None), "length", None)
            if width is None:
                continue
            declared = [
                getattr(meta, "max_length", None)
                for meta in field.metadata
                if getattr(meta, "max_length", None) is not None
            ]
            assert declared == [width], (
                f"ImportAssignmentRow.{name} declares max_length {declared}, "
                f"but peak_assignment.{name} is {width} characters wide"
            )


class TestServerOwnedProvenanceIsDropped:
    """The ledger's calibrated confidence is this server's judgement, not input.

    ``_provenance_scalars`` reads these keys to render P(correct), its
    provisional marker and the corroboration count, and the batch consensus
    reads ``p_correct`` as a member weight. An importer's confidence belongs in
    ``fit_score``; it does not get to populate a column the UI presents as this
    server's calibrated probability - on a run that may have disclosed no
    calibration at all.
    """

    def test_the_confidence_keys_are_removed(self):
        cleaned = strip_server_owned_provenance(
            {
                "p_correct": 0.99,
                "calibration": {"provisional": False},
                "corroboration": {"n_adducts": 9},
                "note": "kept",
            }
        )

        assert cleaned == {"note": "kept"}

    def test_every_key_the_ledger_flattens_is_covered(self):
        """Runs the read model's own collapse over a blob that forges all of it.

        The pin has to be against `_provenance_scalars`, not against a second
        hand-written copy of the same three names - that restates the constant
        rather than checking it, and would still pass if the ledger learned a
        fourth scalar that the strip list then silently let an importer supply.
        """
        forged = {
            "p_correct": 0.99,
            "calibration": {"provisional": False},
            "corroboration": {"n_adducts": 7},
            # Sent under the names the ledger *renders*, too, in case a future
            # read reaches for them directly rather than through the nests.
            "p_correct_provisional": False,
            "corroboration_adducts": 7,
        }

        rendered = _provenance_scalars(strip_server_owned_provenance(forged))

        assert rendered, "the ledger derives no scalars; this test is vacuous"
        assert all(value is None for value in rendered.values()), rendered

    def test_the_importers_own_detail_survives(self):
        """Provenance is inspector detail, and stays - only the verdicts go."""
        cleaned = strip_server_owned_provenance(
            {"evidence": 0.87, "plausibility": 0.5, "score_version": 2}
        )

        assert cleaned == {"evidence": 0.87, "plausibility": 0.5, "score_version": 2}

    @pytest.mark.parametrize("value", [None, {}, {"p_correct": 1.0}])
    def test_nothing_left_to_store_becomes_null(self, value):
        assert strip_server_owned_provenance(value) is None


class TestChunkOffsets:
    """A retried chunk must be a no-op, not a duplicate or a second run.

    The SDK's HTTP layer retries POSTs on timeouts with no way to opt out, so a
    chunk the server already applied will arrive twice. ``chunk.index`` is an
    offset in rows for exactly that reason: the server can tell a replay from a
    fresh chunk without the client having to know whether its last request
    landed.
    """

    def test_the_next_chunk_is_accepted(self):
        assert chunk_offset_error(10, 10, 5) is None
        assert is_chunk_replay(10, 10, 5) is False

    def test_the_first_chunk_of_an_empty_run_is_accepted(self):
        assert chunk_offset_error(0, 0, 5) is None

    def test_resending_the_last_chunk_is_a_replay_not_an_error(self):
        """Ten rows staged, the last chunk of five arriving again."""
        assert is_chunk_replay(5, 10, 5) is True
        assert chunk_offset_error(5, 10, 5) is None

    def test_a_gap_is_refused(self):
        error = chunk_offset_error(12, 10, 5)

        assert error is not None
        assert "gap" in error
        # The message carries the count the client resynchronises from.
        assert "10" in error

    def test_a_deeper_rewind_is_refused_rather_than_silently_dropped(self):
        """Answering this as a no-op would discard rows the client is sending."""
        error = chunk_offset_error(0, 10, 3)

        assert error is not None
        assert "rewind" in error
        assert is_chunk_replay(0, 10, 3) is False

    def test_a_rewind_whose_span_does_not_reach_the_count_is_not_a_replay(self):
        """Only the chunk that produced the current count counts as one."""
        assert is_chunk_replay(2, 10, 3) is False
        assert chunk_offset_error(2, 10, 3) is not None


class TestPeakRules:
    """One row per peak, and only peaks the sample actually has."""

    def test_a_repeated_peak_is_reported(self):
        assert duplicate_peak_ids(["p1", "p2", "p1", "p3", "p2"]) == ["p1", "p2"]

    def test_a_clean_chunk_reports_nothing(self):
        assert duplicate_peak_ids(["p1", "p2", "p3"]) == []

    def test_peaks_the_sample_does_not_have_are_reported(self):
        assert unknown_peak_ids(["p1", "ghost", "p2"], {"p1", "p2"}) == ["ghost"]

    def test_the_report_is_bounded_so_a_wrong_payload_stays_readable(self):
        unknown = unknown_peak_ids([f"g{index}" for index in range(50)], set(), limit=3)

        assert len(unknown) == 3


class TestOwnerLinkage:
    """Owner linkage models one thing: an isotopologue naming the M0 it belongs
    to. Two rules keep it that shape - a row with an owner is an `iso_child`,
    and an `iso_child` is never named as an owner (the second is enforced at
    finalize, since an owner may arrive in a later chunk). Depth and acyclicity
    follow from the pair rather than being policed directly: a cycle needs every
    row in it to be both a child and an owner, which the pair forbids.
    """

    def test_a_row_cannot_own_itself(self):
        """It would resolve happily against its own sample_peak_id."""
        errors = owner_link_errors([_row("p1", owner="p1"), _row("p2", owner="p1")])

        assert len(errors) == 1
        assert "p1" in errors[0]

    def test_a_normal_owner_link_is_fine(self):
        assert owner_link_errors([_row("p2", owner="p1"), _row("p1", role="M0")]) == []

    @pytest.mark.parametrize("role", ["M0", "reagent", "artifact", "unassigned"])
    def test_only_an_iso_child_may_name_an_owner(self, role):
        """Anything else pointing at an owner is not the shape the link models."""
        errors = owner_link_errors([_row("p2", owner="p1", role=role)])

        assert len(errors) == 1
        assert role in errors[0]

    def test_a_two_row_cycle_is_caught_by_the_role_rule(self):
        """A owns B and B owns A: both would have to be owners *and* children.

        Only the direct self-reference was checked before, so this pair resolved
        happily into a cycle no in-app run can produce. The finalize half of the
        rule is what rejects it - both rows are `iso_child`, so each is named as
        an owner while being one.
        """
        assert owner_link_errors([_row("p1", owner="p2"), _row("p2", owner="p1")]) == []
        message = owner_of_owner_error(["p1", "p2"])
        assert "p1" in message and "iso_child" in message

    def test_the_owner_of_owner_message_is_bounded(self):
        message = owner_of_owner_error([f"p{index}" for index in range(20)], limit=3)

        assert "and 17 more" in message


class TestJsonSizeCap:
    """`config` is opaque, so nothing else bounds it.

    It is re-served in full by the run listing, which the SDK reads on every
    ledger read and the run selector polls - the blob-on-a-list-read shape the
    ledger rows were deliberately slimmed to avoid.
    """

    def test_a_normal_config_passes(self):
        assert (
            json_size_error("config", {"passes": 3, "ranges": "C0-100 H0-200"}) is None
        )

    def test_none_passes(self):
        assert json_size_error("config", None) is None

    def test_an_oversized_blob_is_rejected_naming_the_field(self):
        oversized = {"junk": "x" * (MAX_IMPORT_JSON_BYTES + 1)}

        error = json_size_error("config", oversized)

        assert error is not None
        assert "config" in error
        assert str(MAX_IMPORT_JSON_BYTES) in error

    def test_the_cap_measures_encoded_bytes_not_characters(self):
        """A multi-byte payload under the character count can still be over."""
        multibyte = {"note": "å" * (MAX_IMPORT_JSON_BYTES // 2)}

        assert json_size_error("calibration", multibyte) is not None


def test_an_unresolvable_owner_names_the_reference():
    message = unresolved_owner_error(["ghost-1", "ghost-2"])

    assert "ghost-1" in message
    assert "ghost-2" in message


def test_an_unresolvable_owner_report_is_bounded():
    message = unresolved_owner_error([f"g{index}" for index in range(20)], limit=2)

    assert "18 more" in message
