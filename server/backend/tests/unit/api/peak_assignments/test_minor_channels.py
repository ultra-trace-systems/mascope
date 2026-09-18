"""How an opportunistic secondary channel is treated once it is searched.

The fingerprint that decides whether a channel is searched at all is covered in
``libraries/tools``. What is pinned here is what the engine does with a channel
it opened for itself: it must not let one win a peak the mode's own chemistry
explains equally well, and it must not let one hand out the ledger's strongest
word on its own authority.
"""

import pandas as pd
import pytest

from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig
from mascope_backend.api.new.peak_assignments.engine import (
    untargeted_matches_to_peak_assignments,
)
from mascope_backend.api.new.peak_assignments.profiles import (
    ResolvedProfile,
    resolve_profile,
    with_secondary_channels,
)
from mascope_backend.api.new.peak_assignments.tiers import (
    TIER_ASSIGNED,
    TIER_CANDIDATE,
)


MECHANISM_IDS = {"+H+": "im-h", "+NH4+": "im-nh4"}
UREA = ["+H+", "+(CH4N2O)H+"]


def _peaks(*specs):
    return pd.DataFrame(
        [
            {"sample_peak_id": pid, "mz": mz, "intensity": intensity}
            for pid, mz, intensity in specs
        ]
    )


def _match(mz, formula, ion, mechanism, score, isotope_label="M0"):
    return {
        "mz": mz,
        "formula": formula,
        "ion": ion,
        "ionization_mechanism": mechanism,
        "isotopic_pattern_score": score,
        "isotope_label": isotope_label,
        "other_candidates": "",
        "mz_error_ppm": 0.0,
        "intensity_error": 0.0,
    }


def _assign(matches, peaks, minor=frozenset({"+NH4+"})):
    return untargeted_matches_to_peak_assignments(
        pd.DataFrame(matches),
        peaks_df=peaks,
        sample_item_id="si-1",
        peak_assignment_run_id="run-1",
        candidate_threshold=0.45,
        assigned_threshold=0.75,
        mechanism_id_by_notation=MECHANISM_IDS,
        max_alternatives=5,
        minor_channels=minor,
    )


class TestTheCrossCompositionContest:
    """Two compositions landing on one observed peak, where an opportunistic
    channel loses a tie.

    Which READING of a peak wins - the same ion split as X.[M+NH4]+ or as
    (X+NH3).[M+H]+ - is the same-ion policy's decision in the finder, not this
    rule's, and the two must not re-rank each other."""

    def test_a_tie_goes_to_the_modes_own_channel(self):
        peaks = _peaks(("p1", 200.0, 1.0e6))
        rows = _assign(
            [
                _match(200.0, "C9H14O3", "C9H13O3+", "+NH4+", 0.9),
                _match(200.0, "C6H9NO2", "C6H10NO2+", "+H+", 0.9),
            ],
            peaks,
        )
        assert [r["assigned_formula"] for r in rows] == ["C6H9NO2"]
        # The loser is kept, so the peak still shows both readings.
        assert rows[0]["alternatives"][0]["assigned_formula"] == "C9H14O3"

    def test_a_secondary_channel_still_wins_on_better_evidence(self):
        peaks = _peaks(("p1", 200.0, 1.0e6))
        rows = _assign(
            [
                _match(200.0, "C9H14O3", "C9H13O3+", "+NH4+", 0.95),
                _match(200.0, "C6H9NO2", "C6H10NO2+", "+H+", 0.60),
            ],
            peaks,
        )
        assert [r["assigned_formula"] for r in rows] == ["C9H14O3"]

    def test_without_a_minor_set_the_order_is_unchanged(self):
        peaks = _peaks(("p1", 200.0, 1.0e6))
        rows = _assign(
            [
                _match(200.0, "C9H14O3", "C9H13O3+", "+NH4+", 0.9),
                _match(200.0, "C6H9NO2", "C6H10NO2+", "+H+", 0.9),
            ],
            peaks,
            minor=frozenset(),
        )
        assert [r["assigned_formula"] for r in rows] == ["C6H9NO2"]


class TestTheCorroborationCap:
    def test_an_uncorroborated_secondary_winner_is_capped(self):
        peaks = _peaks(("p1", 200.0, 1.0e6))
        rows = _assign([_match(200.0, "C9H14O3", "C9H13O3+", "+NH4+", 0.99)], peaks)
        assert rows[0]["tier"] == TIER_CANDIDATE
        assert rows[0]["provenance"]["minor_channel"] == {
            "corroborated_by": None,
            "capped": True,
        }

    def test_a_confirmed_isotopologue_corroborates(self):
        peaks = _peaks(("p1", 200.0, 1.0e6), ("p2", 201.0034, 1.0e5))
        rows = _assign(
            [
                _match(200.0, "C9H14O3", "C9H13O3+", "+NH4+", 0.99),
                _match(201.0034, "C9H14O3", "C9H13O3+", "+NH4+", 0.99, "13C"),
            ],
            peaks,
        )
        m0 = [r for r in rows if r["role"] == "M0"][0]
        assert m0["tier"] == TIER_ASSIGNED
        assert m0["provenance"]["minor_channel"]["corroborated_by"] == "isotopologue"

    def test_the_same_neutral_on_a_primary_channel_corroborates(self):
        # Two different peaks: the secondary channel owns one that no primary
        # contender reached, and the same neutral won another through +H+.
        peaks = _peaks(("p1", 200.0, 1.0e6), ("p2", 300.0, 5.0e5))
        rows = _assign(
            [
                _match(200.0, "C9H14O3", "C9H13O3+", "+NH4+", 0.99),
                _match(300.0, "C9H14O3", "C9H15O3+", "+H+", 0.99),
            ],
            peaks,
        )
        minor = [r for r in rows if r["ionization_mechanism_id"] == "im-nh4"][0]
        assert minor["tier"] == TIER_ASSIGNED
        assert (
            minor["provenance"]["minor_channel"]["corroborated_by"] == "second_channel"
        )

    def test_a_primary_channel_row_carries_no_minor_verdict(self):
        peaks = _peaks(("p1", 200.0, 1.0e6))
        rows = _assign([_match(200.0, "C6H9NO2", "C6H10NO2+", "+H+", 0.99)], peaks)
        assert rows[0]["tier"] == TIER_ASSIGNED
        assert "minor_channel" not in rows[0]["provenance"]

    def test_the_cap_never_promotes(self):
        # A row that was already below the assigned band stays where it is; the
        # rule only demotes.
        peaks = _peaks(("p1", 200.0, 1.0e6))
        rows = _assign([_match(200.0, "C9H14O3", "C9H13O3+", "+NH4+", 0.50)], peaks)
        assert rows[0]["tier"] == TIER_CANDIDATE
        assert rows[0]["provenance"]["minor_channel"]["capped"] is False


class TestResolution:
    def _resolved(self) -> ResolvedProfile:
        return resolve_profile(
            PeakAssignmentConfig(), UREA, instrument_type="orbi", polarity="+"
        )

    def test_a_channel_the_spectrum_shows_and_the_deployment_has_is_searched(self):
        resolved = with_secondary_channels(
            self._resolved(),
            [100.0, 138.0986],
            [1.0e6, 1.0e4],
            ["+NH4+"],
        )
        assert resolved.minor_channels == frozenset({"+NH4+"})
        assert resolved.unavailable_channels == ()

    def test_a_channel_the_spectrum_does_not_show_is_not_searched(self):
        resolved = with_secondary_channels(
            self._resolved(), [100.0, 200.0], [1.0e6, 1.0e4], ["+NH4+"]
        )
        assert resolved.minor_channels == frozenset()

    def test_a_channel_the_deployment_cannot_express_is_reported_not_searched(self):
        # Different from "the source does not run it", and only this one is
        # worth fixing by configuration - so the run says which it was.
        resolved = with_secondary_channels(
            self._resolved(), [100.0, 138.0986], [1.0e6, 1.0e4], []
        )
        assert resolved.minor_channels == frozenset()
        assert resolved.unavailable_channels == ("+NH4+",)

    def test_a_profile_with_no_secondary_channels_is_untouched(self):
        resolved = resolve_profile(
            PeakAssignmentConfig(profile="none"), UREA, instrument_type="orbi"
        )
        extended = with_secondary_channels(
            resolved, [100.0, 138.0986], [1.0e6, 1.0e4], ["+NH4+"]
        )
        assert extended is resolved
        assert extended.minor_channels == frozenset()

    def test_the_snapshot_records_every_channel_considered(self):
        snapshot = with_secondary_channels(
            self._resolved(), [100.0, 138.0986], [1.0e6, 1.0e4], ["+NH4+"]
        ).snapshot()
        assert snapshot["secondary_channels"] == ["+NH4+"]
        assert snapshot["channel_evidence"][0]["channel"] == "+NH4+"
        assert snapshot["channel_evidence"][0]["present"] is True
        assert snapshot["unavailable_channels"] == []

    def test_the_snapshot_of_a_run_that_opened_nothing_says_so(self):
        # A spectrum that spans the probes and does not carry them: the source
        # is not running the channel, which is a real answer.
        snapshot = with_secondary_channels(
            self._resolved(), [50.0, 100.0, 400.0], [1.0e6, 1.0e5, 1.0e4], ["+NH4+"]
        ).snapshot()
        assert snapshot["secondary_channels"] == []
        assert snapshot["channel_evidence"] == [
            {"channel": "+NH4+", "present": False, "status": "not_found"}
        ]

    def test_a_channel_the_window_could_not_show_is_recorded_as_such(self):
        # And is not searched, because the urea profile does not default it on.
        snapshot = with_secondary_channels(
            self._resolved(), [300.0, 400.0], [1.0e6, 1.0e4], ["+NH4+"]
        ).snapshot()
        assert snapshot["secondary_channels"] == []
        assert snapshot["channel_evidence"] == [
            {"channel": "+NH4+", "present": False, "status": "unobservable"}
        ]


@pytest.mark.parametrize("profile,expected", [("BR", 2), ("NO3", 1), ("UR", 1)])
def test_the_gate_sets_profiles_declare_their_channels(profile, expected):
    from mascope_tools.composition.reagents import secondary_channels

    assert len(secondary_channels(profile)) == expected
