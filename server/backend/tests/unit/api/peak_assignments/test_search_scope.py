"""Which peaks the untargeted stage is offered, and how a run says so.

The cap used to default to 300, so on a dense spectrum most peaks were never
searched and nothing in the ledger distinguished "searched and unexplained" from
"never looked at". It now defaults to every peak, bounded by the ceiling, and the
run records the difference.
"""

import pandas as pd
import pytest

from mascope_backend.api.new.peak_assignments.config import (
    MAX_UNTARGETED_PEAKS_CEILING,
    PeakAssignmentConfig,
)
from mascope_backend.api.new.peak_assignments.engine import (
    SEARCH_SCOPE_KEY,
    untargeted_targets,
)


def peaks(count: int) -> pd.DataFrame:
    """`count` peaks, brightest last, so a cap has to sort rather than slice."""
    return pd.DataFrame(
        {
            "sample_peak_id": [f"p{i}" for i in range(count)],
            "mz": [100.0 + i for i in range(count)],
            "intensity": [float(i + 1) for i in range(count)],
        }
    )


class TestTheDefault:
    def test_the_cap_is_unset_so_every_peak_is_searched(self):
        assert PeakAssignmentConfig().max_untargeted_peaks is None

    def test_an_unset_cap_offers_the_stage_the_whole_remainder(self):
        targets, scope = untargeted_targets(peaks(1200), None, 5000)
        assert len(targets) == 1200
        assert scope["limited"] is False
        assert scope["eligible_peaks"] == 1200
        assert scope["searched_peaks"] == 1200

    def test_a_run_that_searched_everything_says_it_was_not_limited(self):
        _, scope = untargeted_targets(peaks(10), None, 5000)
        assert scope["limited"] is False
        assert scope["at_ceiling"] is False
        assert scope["requested_limit"] is None


class TestTheCeiling:
    def test_it_still_bounds_a_spectrum_with_no_cap_of_its_own(self):
        targets, scope = untargeted_targets(peaks(60), None, ceiling=50)
        assert len(targets) == 50
        assert scope["limited"] is True
        assert scope["at_ceiling"] is True
        assert scope["ceiling"] == 50

    def test_the_peaks_it_keeps_are_the_brightest(self):
        targets, _ = untargeted_targets(peaks(60), None, ceiling=3)
        assert sorted(targets["sample_peak_id"]) == ["p57", "p58", "p59"]

    def test_a_cap_above_the_ceiling_does_not_raise_it(self):
        targets, scope = untargeted_targets(peaks(60), 10_000, ceiling=50)
        assert len(targets) == 50
        assert scope["requested_limit"] == 10_000

    def test_a_request_cannot_ask_for_more_than_the_ceiling(self):
        with pytest.raises(ValueError):
            PeakAssignmentConfig(max_untargeted_peaks=MAX_UNTARGETED_PEAKS_CEILING + 1)


class TestACapTheCallerAskedFor:
    def test_it_is_honoured(self):
        targets, scope = untargeted_targets(peaks(1200), 300, 5000)
        assert len(targets) == 300
        assert scope["searched_peaks"] == 300
        assert scope["requested_limit"] == 300

    def test_it_reports_that_it_bit(self):
        _, scope = untargeted_targets(peaks(1200), 300, 5000)
        assert scope["limited"] is True
        # The ceiling was not what stopped it; a reader should not be sent to
        # look for a deployment limit that had nothing to do with this run.
        assert scope["at_ceiling"] is False

    def test_a_cap_larger_than_the_spectrum_leaves_it_whole(self):
        _, scope = untargeted_targets(peaks(40), 300, 5000)
        assert scope["limited"] is False
        assert scope["searched_peaks"] == 40


class TestWhatTheRunRecords:
    def test_the_scope_lands_on_the_stored_config(self):
        from mascope_backend.api.new.peak_assignments.service import (
            _stored_run_config,
        )

        _, scope = untargeted_targets(peaks(60), None, ceiling=50)
        stored = _stored_run_config(PeakAssignmentConfig(), None, scope)
        assert stored[SEARCH_SCOPE_KEY]["searched_peaks"] == 50
        assert stored[SEARCH_SCOPE_KEY]["eligible_peaks"] == 60

    def test_a_run_without_the_stage_records_no_scope(self):
        from mascope_backend.api.new.peak_assignments.service import (
            _stored_run_config,
        )

        stored = _stored_run_config(PeakAssignmentConfig(run_untargeted=False))
        assert SEARCH_SCOPE_KEY not in stored
