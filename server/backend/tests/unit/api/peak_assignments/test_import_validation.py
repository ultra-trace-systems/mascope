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

from mascope_backend.api.new.peak_assignments.config import (
    IN_APP_ENGINE,
    MAX_IMPORT_JSON_BYTES,
)
from mascope_backend.api.new.peak_assignments.import_validation import (
    SERVER_OWNED_PROVENANCE_KEYS,
    chunk_offset_error,
    duplicate_peak_ids,
    is_chunk_replay,
    json_size_error,
    normalize_engine,
    self_owner_errors,
    strip_server_owned_provenance,
    tier_coherence_error,
    tier_for_fit_score,
    unknown_peak_ids,
    unresolved_owner_error,
)


def _row(sample_peak_id: str, owner: str | None = None):
    """A stand-in for a payload row, carrying only the owner-link fields."""
    return SimpleNamespace(sample_peak_id=sample_peak_id, owner_sample_peak_id=owner)


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
    'identified' rows at 0.62 that sort, filter and roll up beside in-app
    'identified' rows meaning something considerably stricter.
    """

    @pytest.mark.parametrize(
        "fit_score,expected",
        [
            (1.0, "identified"),
            (0.8, "identified"),
            (0.79, "candidate"),
            (0.5, "candidate"),
            (0.49, "below_assignability"),
            (0.0, "below_assignability"),
            (None, "unassigned"),
        ],
    )
    def test_thresholds_are_inclusive_at_the_band_edge(self, fit_score, expected):
        assert tier_for_fit_score(fit_score, 0.8, 0.5) == expected

    def test_a_coherent_row_passes(self):
        assert tier_coherence_error("identified", 0.91, 0.8, 0.5) is None

    def test_an_inflated_tier_is_rejected(self):
        """The case the whole rule exists for."""
        error = tier_coherence_error("identified", 0.62, 0.8, 0.5)

        assert error is not None
        assert "candidate" in error

    def test_a_demoted_tier_is_rejected_too(self):
        """Bands are checked, not merely bounded: a tier is derived, not chosen."""
        assert tier_coherence_error("candidate", 0.95, 0.8, 0.5) is not None

    def test_the_declared_bands_are_what_is_applied(self):
        """0.62 is 'identified' under 0.6/0.3 and 'candidate' under 0.8/0.5."""
        assert tier_coherence_error("identified", 0.62, 0.6, 0.3) is None
        assert tier_coherence_error("identified", 0.62, 0.8, 0.5) is not None

    def test_a_scored_row_cannot_claim_unassigned(self):
        assert tier_coherence_error("unassigned", 0.9, 0.8, 0.5) is not None

    def test_an_unscored_row_must_be_unassigned(self):
        assert tier_coherence_error("unassigned", None, 0.8, 0.5) is None
        error = tier_coherence_error("identified", None, 0.8, 0.5)
        assert error is not None
        assert "no fit_score" in error


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
        """Pins the list against the scalars the read model derives."""
        assert set(SERVER_OWNED_PROVENANCE_KEYS) == {
            "p_correct",
            "calibration",
            "corroboration",
        }

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

    def test_a_row_cannot_own_itself(self):
        """It would resolve happily against its own sample_peak_id."""
        errors = self_owner_errors([_row("p1", owner="p1"), _row("p2", owner="p1")])

        assert len(errors) == 1
        assert "p1" in errors[0]

    def test_a_normal_owner_link_is_fine(self):
        assert self_owner_errors([_row("p2", owner="p1"), _row("p1")]) == []


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
