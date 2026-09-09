"""Stage B is tiered on the fit Stage A would have given its reading.

The composition finder ranks candidates against the peak list; that score
decides which reading of a peak wins and stays on the row for audit. What the
committed row is TIERED on is the same ion measured again through the match
path - one ``compute_match_isotopes`` pass with the run's gating - so that a
Stage B "assigned" and a Stage A "assigned" are the same claim. These cover the
pure halves of that: which seeds are measured, what the sample is measured at,
and what the conversion does with the answer.
"""

import math

import pandas as pd
import pytest

from mascope_backend.api.controllers.match.lib.match_score_v2 import PRED_SIGMA_PPM
from mascope_backend.api.new.peak_assignments.engine import (
    TIER_ASSIGNED,
    TIER_CANDIDATE,
    pattern_scoring_for,
    untargeted_matches_to_peak_assignments,
    untargeted_seeds,
)
from types import SimpleNamespace


CANDIDATE = 0.45
ASSIGNED = 0.75
MECHANISMS = {"+H+": "mech-h", "+NH4+": "mech-nh4"}


def _orbi_params(tolerance: int = 5, floor: float = 1e-5) -> SimpleNamespace:
    return SimpleNamespace(mz_tolerance=tolerance, isotope_abundance_threshold=floor)


def _row(**overrides) -> dict:
    return {
        "mz": 100.1,
        "formula": "C5H10O2",
        "ion": "C5H11O2+",
        "isotope_label": "M0",
        "ionization_mechanism": "+H+",
        "isotopic_pattern_score": 0.92,
        "mz_error_ppm": 0.4,
        "intensity_error": 0.02,
        "other_candidates": "",
    } | overrides


def _peaks(*peaks) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "sample_peak_id": [peak_id for peak_id, _, _ in peaks],
            "mz": [mz for _, mz, _ in peaks],
            "intensity": [intensity for _, _, intensity in peaks],
        }
    )


def _convert(rows, peaks, fit_by_seed=None):
    return untargeted_matches_to_peak_assignments(
        pd.DataFrame(rows),
        peaks,
        "sample1",
        "run1",
        CANDIDATE,
        ASSIGNED,
        mechanism_id_by_notation=MECHANISMS,
        fit_by_seed=fit_by_seed,
    )


class TestPatternScoringForTheSample:
    """What the finder is told about the sample it is searching."""

    def test_the_width_is_the_one_stage_a_measured(self):
        scoring = pattern_scoring_for(_orbi_params(), (-0.24, 0.30))

        assert scoring.sigma_ppm == pytest.approx(math.hypot(0.30, PRED_SIGMA_PPM))
        assert scoring.mu_ppm == pytest.approx(-0.24)

    def test_the_match_tolerance_stands_in_when_nothing_fitted_a_width(self):
        # Too few anchors to fit: the instrument's own tolerance is its
        # statement about its accuracy, and a tolerance is about three sigma.
        scoring = pattern_scoring_for(_orbi_params(), (0.0, None))

        assert scoring.sigma_ppm == pytest.approx(math.hypot(5 / 3, PRED_SIGMA_PPM))

    def test_a_tof_is_judged_at_a_tofs_width_and_window(self):
        scoring = pattern_scoring_for(_orbi_params(tolerance=15, floor=1e-4), (0.0, None))

        assert scoring.sigma_ppm == pytest.approx(math.hypot(5.0, PRED_SIGMA_PPM))
        assert scoring.mz_tolerance_ppm == 15
        assert scoring.abundance_floor == 1e-4

    def test_the_envelope_floor_is_the_samples_own(self):
        # The same floor Stage A generates its isotopes at, so the two stages
        # predict a line to the same depth.
        assert pattern_scoring_for(_orbi_params(), (0.0, 0.3)).abundance_floor == 1e-5


class TestWhichReadingsAreMeasuredAgain:
    """Every committed reading, in the form it is stored in."""

    def test_each_distinct_formula_and_mechanism_once(self):
        seeds = untargeted_seeds(
            pd.DataFrame(
                [
                    _row(),
                    _row(mz=101.1, isotope_label="13C"),
                    _row(mz=118.1, ionization_mechanism="+NH4+"),
                ]
            ),
            MECHANISMS,
        )

        assert seeds == {("C5H10O2", "mech-h"), ("C5H10O2", "mech-nh4")}

    def test_a_satellite_is_seeded_like_its_parent(self):
        # A satellite's ion is a hypothesis about the peak it sits on, and it
        # can lose that peak to another reading whose fit a reader then compares
        # against it. Both have to be on one scale for the comparison to mean
        # anything.
        seeds = untargeted_seeds(
            pd.DataFrame([_row(mz=101.1, isotope_label="13C")]), MECHANISMS
        )

        assert seeds == {("C5H10O2", "mech-h")}

    def test_placeholders_and_ionization_rows_are_not_readings(self):
        seeds = untargeted_seeds(
            pd.DataFrame(
                [
                    _row(formula="---", ion="---", isotope_label="---"),
                    _row(formula="()", ion="()"),
                ]
            ),
            MECHANISMS,
        )

        assert seeds == set()

    def test_a_mechanism_the_run_cannot_name_cannot_be_seeded(self):
        assert untargeted_seeds(pd.DataFrame([_row()]), {}) == set()

    def test_the_formula_is_seeded_as_it_will_be_stored(self):
        # The seeded chain generates ions from the target library's notation, so
        # the key has to be the formatted formula and not the finder's own.
        seeds = untargeted_seeds(
            pd.DataFrame([_row(formula="[15N]NO3")]),
            MECHANISMS,
            formula_formatter=lambda formula: formula.replace("[15N]", "^N"),
        )

        assert seeds == {("^NNO3", "mech-h")}


class TestTheRowIsTieredOnTheSeededFit:
    """The finder ranks; the re-score tiers."""

    def test_the_seeded_fit_is_the_rows_fit_score(self):
        [assignment] = _convert(
            [_row()], _peaks(("pA", 100.1, 5000.0)), {("C5H10O2", "mech-h"): 0.55}
        )

        assert assignment["fit_score"] == pytest.approx(0.55)

    def test_the_finders_own_score_stays_in_provenance(self):
        [assignment] = _convert(
            [_row()], _peaks(("pA", 100.1, 5000.0)), {("C5H10O2", "mech-h"): 0.55}
        )

        assert assignment["provenance"]["pattern_fit"] == pytest.approx(0.92)

    def test_the_tier_follows_the_re_score_and_not_the_ranking(self):
        # The finder liked this reading; measured as an ion against the whole
        # spectrum, the envelope it predicted is not all there. Under the old
        # binding the row was 'assigned' on the finder's number alone.
        [assignment] = _convert(
            [_row()], _peaks(("pA", 100.1, 5000.0)), {("C5H10O2", "mech-h"): 0.55}
        )

        assert assignment["tier"] == TIER_CANDIDATE
        assert assignment["provenance"]["evidence"] == pytest.approx(
            round(0.55 * assignment["provenance"]["plausibility"], 4)
        )

    def test_without_a_re_score_the_finders_number_stands(self):
        # A path with no sample file to measure against - and the row says so by
        # carrying the same number twice.
        [assignment] = _convert([_row()], _peaks(("pA", 100.1, 5000.0)))

        assert assignment["fit_score"] == pytest.approx(0.92)
        assert assignment["provenance"]["pattern_fit"] == pytest.approx(0.92)
        assert assignment["tier"] == TIER_ASSIGNED

    def test_a_reading_the_pass_could_not_measure_keeps_its_own_number(self):
        # `score_seeds` answers for the ions it could generate and match; a seed
        # missing from the answer is not a zero.
        [assignment] = _convert(
            [_row()], _peaks(("pA", 100.1, 5000.0)), {("C9H12O", "mech-h"): 0.31}
        )

        assert assignment["fit_score"] == pytest.approx(0.92)


class TestTheContestIsSettledOnIt:
    """Two readings on one peak, ranked by the fit the tier is read from."""

    ROWS = [
        _row(),
        _row(formula="C4H8N2O", ion="C4H9N2O+", isotopic_pattern_score=0.95),
    ]

    def test_the_better_measured_ion_takes_the_peak(self):
        # The finder ranked C4H8N2O higher against the peak list; measured as
        # ions, the other explains the spectrum better and wins the peak.
        [assignment] = _convert(
            self.ROWS,
            _peaks(("pA", 100.1, 5000.0)),
            {("C5H10O2", "mech-h"): 0.90, ("C4H8N2O", "mech-h"): 0.40},
        )

        assert assignment["assigned_formula"] == "C5H10O2"

    def test_the_loser_is_kept_at_the_same_measurement(self):
        [assignment] = _convert(
            self.ROWS,
            _peaks(("pA", 100.1, 5000.0)),
            {("C5H10O2", "mech-h"): 0.90, ("C4H8N2O", "mech-h"): 0.40},
        )

        [alternative] = assignment["alternatives"]
        assert alternative["assigned_formula"] == "C4H8N2O"
        assert alternative["fit_score"] == pytest.approx(0.40)
