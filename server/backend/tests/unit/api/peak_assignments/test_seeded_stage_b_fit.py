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
from types import SimpleNamespace

import pandas as pd
import pytest

from mascope_backend.api.new.peak_assignments.engine import (
    TIER_ASSIGNED,
    TIER_CANDIDATE,
    ReagentOffset,
    SampleMassAccuracy,
    pattern_scoring_for,
    pattern_scoring_snapshot,
    reagent_line_offset,
    sample_mass_accuracy,
    untargeted_matches_to_peak_assignments,
    untargeted_seeds,
)
from mascope_tools.composition.mass_accuracy import PRED_SIGMA_PPM, fit_mass_accuracy
from mascope_tools.composition.profiles import INSTRUMENT_FALLBACK_SIGMA_PPM


CANDIDATE = 0.45
ASSIGNED = 0.75
ORBI_ACCURACY = INSTRUMENT_FALLBACK_SIGMA_PPM["orbi"]
TOF_ACCURACY = INSTRUMENT_FALLBACK_SIGMA_PPM["tof"]
MECHANISMS = {"[M+H]+": "mech-h", "[M+NH4]+": "mech-nh4"}

#: The lines the reagent pre-pass claims on a labelled-nitrate sample of the
#: gate, in ppm: the core ion and its first rung, then five isotopologue lines
#: of the two. The sample's own commits sit at -1.10.
NITRATE_LINES = [1.2556, -0.0517, -0.7262, -1.3953, -1.2555, -1.9426, -1.7316]
#: What they say, as the pre-pass hands it on.
NITRATE_READING = ReagentOffset(mu_ppm=-1.2555, lines=7, beyond_width=True)


def _orbi_params(tolerance: int = 5, floor: float = 1e-5) -> SimpleNamespace:
    return SimpleNamespace(mz_tolerance=tolerance, isotope_abundance_threshold=floor)


def _row(**overrides) -> dict:
    return {
        "mz": 100.1,
        "formula": "C5H10O2",
        "ion": "C5H11O2+",
        "isotope_label": "M0",
        "ionization_mechanism": "[M+H]+",
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


class TestWhatStageAMeasured:
    """The engine fits the sample's width with the library's fit."""

    def test_the_offset_the_width_and_the_anchors_are_the_frames_own(self):
        errors = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
        frame = pd.DataFrame(
            {
                "match_mz_error": errors,
                "sample_peak_intensity": [100.0] * len(errors),
            }
        )

        measured = sample_mass_accuracy(frame)

        assert (measured.mu_ppm, measured.sigma_ppm) == fit_mass_accuracy(errors)
        assert measured.anchors == len(errors)

    def test_a_row_the_gating_zeroed_is_not_an_anchor(self):
        # Stage A's fit runs on the gated frame, where a rejected isotopologue
        # carries a zero intensity. The anchor count a run records has to say
        # the same, or it reports a confidence the width was not fitted from.
        frame = pd.DataFrame(
            {
                "match_mz_error": [0.1, 0.2, 0.3],
                "sample_peak_intensity": [100.0, 0.0, 100.0],
            }
        )

        assert sample_mass_accuracy(frame).anchors == 2

    def test_a_reference_mirror_line_is_not_an_anchor(self):
        # A mirror is matched against every sample, so on a TOF most of its
        # pairings are lines the window reached by chance. With the default seed
        # loaded they were over 90% of the anchors on the gate's TOF sets, and
        # the width came out as the window's. The target library's lines are the
        # measurement, and the count a run records is theirs.
        library = [0.0, 0.1, -0.1, 0.2, -0.2, 0.1, -0.1, 0.05, -0.05]
        mirror = [-9.0, -7.5, -6.0, -4.5, -3.0, 3.0, 4.5, 6.0, 7.5, 9.0, -8.0, 8.0]
        identities = [{"name": "a seed compound", "source": "test"}]
        frame = pd.DataFrame(
            {
                "match_mz_error": library + mirror,
                "sample_peak_intensity": [100.0] * (len(library) + len(mirror)),
                "reference_identities": [None] * len(library)
                + [identities] * len(mirror),
            }
        )

        measured = sample_mass_accuracy(frame)

        assert (measured.mu_ppm, measured.sigma_ppm) == fit_mass_accuracy(library)
        assert measured.anchors == len(library)

    def test_a_sample_with_no_matches_measured_nothing(self):
        measured = sample_mass_accuracy(pd.DataFrame())

        assert measured.sigma_ppm is None
        assert measured.anchors == 0

    def test_the_reagent_lines_reading_rides_beside_the_fit(self):
        # Carried, never fitted in: three library lines are still too few to
        # measure anything, whatever the source's own lines say.
        frame = pd.DataFrame(
            {
                "match_mz_error": [-1.2, -1.3, -1.25],
                "sample_peak_intensity": [100.0] * 3,
            }
        )

        measured = sample_mass_accuracy(frame, reagent=NITRATE_READING)

        assert (measured.mu_ppm, measured.sigma_ppm) == (None, None)
        assert measured.anchors == 3
        assert measured.reagent is NITRATE_READING


class TestTheReagentLinesOffset:
    """Where the source's own lines put the axis, for a library too thin to say."""

    def test_it_is_the_median_of_every_line_the_pass_claimed(self):
        # Not the correction the pass claims its rungs against, which is the
        # median of the two anchors alone. Those are the source's brightest
        # lines, and here they put the axis at +0.60 ppm, on the other side of
        # the -1.10 the sample's own commits measure.
        offset = reagent_line_offset(NITRATE_LINES, ORBI_ACCURACY)

        assert offset.mu_ppm == pytest.approx(-1.2555)
        assert offset.lines == len(NITRATE_LINES)
        assert offset.beyond_width

    def test_an_offset_inside_the_width_is_left_alone(self):
        # A bromide sample's lines, at +0.30 on a class scored at 0.58 ppm: its
        # commits sit at -0.18, so correcting by them would move it the wrong
        # way, and a bias that small is one the score already allows for.
        offset = reagent_line_offset(
            [0.33, 0.30, -0.04, -0.11, 0.0, 0.73, 0.44, 0.33, -0.12], ORBI_ACCURACY
        )

        assert offset.mu_ppm == pytest.approx(0.30)
        assert not offset.beyond_width

    def test_the_guard_is_the_width_the_sample_is_scored_at(self):
        # A sample that reaches this offset fitted no width either, so it is
        # scored at the class's precision widened for the prediction's error,
        # and 0.45 ppm lies between the two.
        widened = math.hypot(ORBI_ACCURACY, PRED_SIGMA_PPM)
        assert ORBI_ACCURACY < 0.45 < widened < 0.7

        assert not reagent_line_offset([0.45] * 5, ORBI_ACCURACY).beyond_width
        assert not reagent_line_offset([-0.45] * 5, ORBI_ACCURACY).beyond_width
        assert reagent_line_offset([0.7] * 5, ORBI_ACCURACY).beyond_width
        assert reagent_line_offset([-0.7] * 5, ORBI_ACCURACY).beyond_width

    def test_a_tofs_width_keeps_what_an_orbitraps_would_take(self):
        # The bromide TOF set's lines sit up to +0.71 ppm on a class scored at 3.
        assert not reagent_line_offset([0.71] * 16, TOF_ACCURACY).beyond_width
        assert reagent_line_offset([0.71] * 16, ORBI_ACCURACY).beyond_width

    def test_fewer_than_three_lines_are_no_offset(self):
        # The core ion and its first rung alone: their mean is on the wrong side
        # of the axis, and nothing in two lines says which of them is off.
        two = reagent_line_offset(NITRATE_LINES[:2], ORBI_ACCURACY)

        assert (two.mu_ppm, two.lines, two.beyond_width) == (None, 2, False)
        assert reagent_line_offset(NITRATE_LINES[:3], ORBI_ACCURACY).mu_ppm == (
            pytest.approx(-0.0517)
        )

    def test_an_unusable_error_is_not_a_line(self):
        offset = reagent_line_offset(
            [-1.3, float("nan"), None, -1.2, float("inf"), -1.4], ORBI_ACCURACY
        )

        assert offset.lines == 3
        assert offset.mu_ppm == pytest.approx(-1.3)

    def test_a_pass_that_claimed_nothing_measured_nothing(self):
        assert reagent_line_offset([], ORBI_ACCURACY) == ReagentOffset(None, 0)


class TestWhichOffsetTheSampleIsScoredAt:
    """The library's own offset, else the reagent lines', else none."""

    def test_the_librarys_offset_wins_wherever_it_measured_one(self):
        # The uronium set's lines sit at -0.9 ppm where its library and its
        # commits sit within 0.15 of zero: a sample that measures its own
        # offset keeps it.
        accuracy = SampleMassAccuracy(
            -0.03, 0.56, 15, ReagentOffset(-0.93, 10, beyond_width=True)
        )

        assert accuracy.scoring_mu_ppm == pytest.approx(-0.03)
        assert accuracy.mu_source == "fitted"

    def test_the_reagent_lines_stand_in_where_the_library_measured_none(self):
        accuracy = SampleMassAccuracy(None, None, 7, NITRATE_READING)

        assert accuracy.scoring_mu_ppm == pytest.approx(-1.2555)
        assert accuracy.mu_source == "reagent"

    @pytest.mark.parametrize(
        "reading",
        [None, ReagentOffset(0.30, 9), ReagentOffset(None, 2), ReagentOffset(None, 0)],
        ids=["no pre-pass", "inside the width", "too few lines", "no lines"],
    )
    def test_otherwise_nothing_measured_one(self, reading):
        accuracy = SampleMassAccuracy(None, None, 2, reading)

        assert accuracy.scoring_mu_ppm is None
        assert accuracy.mu_source == "none"


class TestPatternScoringForTheSample:
    """What the finder is told about the sample it is searching."""

    def test_the_width_is_the_one_stage_a_measured(self):
        scoring = pattern_scoring_for(
            _orbi_params(), SampleMassAccuracy(-0.24, 0.30, 14), ORBI_ACCURACY
        )

        assert scoring.sigma_ppm == pytest.approx(math.hypot(0.30, PRED_SIGMA_PPM))
        assert scoring.mu_ppm == pytest.approx(-0.24)

    def test_the_instrument_class_stands_in_when_nothing_fitted_a_width(self):
        # Too few known ions matched to fit anything. The class statement is a
        # poor substitute and the only honest one: the 5 ppm match tolerance
        # would be five times this instrument's real accuracy, and at that width
        # every candidate the search enumerated fits equally well.
        scoring = pattern_scoring_for(
            _orbi_params(), SampleMassAccuracy(), ORBI_ACCURACY
        )

        assert scoring.sigma_ppm == pytest.approx(
            math.hypot(ORBI_ACCURACY, PRED_SIGMA_PPM)
        )

    def test_a_tof_is_judged_at_a_tofs_width_and_window(self):
        scoring = pattern_scoring_for(
            _orbi_params(tolerance=15, floor=1e-4), SampleMassAccuracy(), TOF_ACCURACY
        )

        assert scoring.sigma_ppm == pytest.approx(
            math.hypot(TOF_ACCURACY, PRED_SIGMA_PPM)
        )
        assert scoring.mz_tolerance_ppm == 15
        assert scoring.abundance_floor == 1e-4

    def test_the_reagent_lines_set_the_offset_where_the_library_fits_none(self):
        # Stage A scored its own ions there, and the search is scored where
        # Stage A was. The width stays the class's: a handful of the source's
        # bright lines says where the axis is, not how an analyte scatters.
        scoring = pattern_scoring_for(
            _orbi_params(),
            SampleMassAccuracy(None, None, 7, NITRATE_READING),
            ORBI_ACCURACY,
        )

        assert scoring.mu_ppm == pytest.approx(-1.2555)
        assert scoring.sigma_ppm == pytest.approx(
            math.hypot(ORBI_ACCURACY, PRED_SIGMA_PPM)
        )

    def test_a_reading_inside_the_width_leaves_the_search_uncorrected(self):
        scoring = pattern_scoring_for(
            _orbi_params(),
            SampleMassAccuracy(None, None, 2, ReagentOffset(0.30, 9)),
            ORBI_ACCURACY,
        )

        assert scoring.mu_ppm == 0.0

    def test_a_measured_width_beats_the_class_statement(self):
        # However well or badly this sample measures, what it measured wins.
        loose = pattern_scoring_for(
            _orbi_params(), SampleMassAccuracy(0.0, 1.4, 31), ORBI_ACCURACY
        )

        assert loose.sigma_ppm == pytest.approx(math.hypot(1.4, PRED_SIGMA_PPM))

    def test_the_envelope_floor_is_the_samples_own(self):
        # The same floor Stage A generates its isotopes at, so the two stages
        # predict a line to the same depth.
        scoring = pattern_scoring_for(
            _orbi_params(), SampleMassAccuracy(0.0, 0.3, 9), ORBI_ACCURACY
        )
        assert scoring.abundance_floor == 1e-5


class TestTheRunSaysWhatItJudgedAt:
    """A ppm is not a ppm without the width it was judged against."""

    def test_a_fitted_width_says_so_and_names_its_anchors(self):
        accuracy = SampleMassAccuracy(-0.24, 0.30, 14)
        scoring = pattern_scoring_for(_orbi_params(), accuracy, ORBI_ACCURACY)

        snapshot = pattern_scoring_snapshot(scoring, accuracy)

        assert snapshot["sigma_source"] == "fitted"
        assert snapshot["fitted_anchors"] == 14
        assert snapshot["sigma_ppm"] == pytest.approx(
            math.hypot(0.30, PRED_SIGMA_PPM), abs=1e-4
        )
        assert snapshot["mu_ppm"] == pytest.approx(-0.24)

    def test_a_fallback_says_that_instead_and_still_names_them(self):
        # Two anchors is not a small measurement of a width, it is none - and
        # the count is what a reader needs to see why the class stood in.
        accuracy = SampleMassAccuracy(0.0, None, 2)
        scoring = pattern_scoring_for(_orbi_params(), accuracy, ORBI_ACCURACY)

        snapshot = pattern_scoring_snapshot(scoring, accuracy)

        assert snapshot["sigma_source"] == "instrument_class"
        assert snapshot["fitted_anchors"] == 2
        assert snapshot["sigma_ppm"] == pytest.approx(
            math.hypot(ORBI_ACCURACY, PRED_SIGMA_PPM), abs=1e-4
        )

    def test_an_offset_from_the_reagent_lines_says_so_and_names_them(self):
        accuracy = SampleMassAccuracy(None, None, 7, NITRATE_READING)
        scoring = pattern_scoring_for(_orbi_params(), accuracy, ORBI_ACCURACY)

        snapshot = pattern_scoring_snapshot(scoring, accuracy)

        assert snapshot["mu_source"] == "reagent"
        assert snapshot["mu_ppm"] == pytest.approx(-1.2555)
        assert snapshot["reagent_lines"] == 7
        assert snapshot["reagent_mu_ppm"] == pytest.approx(-1.2555)
        # The library's own count still says why it did not measure one.
        assert snapshot["fitted_anchors"] == 7
        assert snapshot["sigma_source"] == "instrument_class"

    @pytest.mark.parametrize(
        "reading, recorded",
        [(ReagentOffset(0.30, 9), 0.3), (ReagentOffset(None, 2), None)],
        ids=["inside the width", "too few lines"],
    )
    def test_a_reading_not_scored_at_is_still_recorded(self, reading, recorded):
        # "none" alone would not say whether the lines were read and found
        # inside the width, too few to read, or never asked.
        accuracy = SampleMassAccuracy(None, None, 2, reading)
        scoring = pattern_scoring_for(_orbi_params(), accuracy, ORBI_ACCURACY)

        snapshot = pattern_scoring_snapshot(scoring, accuracy)

        assert (snapshot["mu_source"], snapshot["mu_ppm"]) == ("none", 0.0)
        assert snapshot["reagent_lines"] == reading.lines
        assert snapshot["reagent_mu_ppm"] == recorded

    def test_a_run_with_no_pre_pass_records_no_reading(self):
        accuracy = SampleMassAccuracy(-0.24, 0.30, 14)
        scoring = pattern_scoring_for(_orbi_params(), accuracy, ORBI_ACCURACY)

        snapshot = pattern_scoring_snapshot(scoring, accuracy)

        assert snapshot["mu_source"] == "fitted"
        assert "reagent_lines" not in snapshot
        assert "reagent_mu_ppm" not in snapshot

    def test_the_window_and_the_floor_ride_along(self):
        accuracy = SampleMassAccuracy(0.0, 0.3, 11)
        scoring = pattern_scoring_for(
            _orbi_params(tolerance=15, floor=1e-4), accuracy, TOF_ACCURACY
        )

        snapshot = pattern_scoring_snapshot(scoring, accuracy)

        assert snapshot["mz_tolerance_ppm"] == 15
        assert snapshot["abundance_floor"] == 1e-4


class TestWhichReadingsAreMeasuredAgain:
    """Every committed reading, in the form it is stored in."""

    def test_each_distinct_formula_and_mechanism_once(self):
        seeds = untargeted_seeds(
            pd.DataFrame(
                [
                    _row(),
                    _row(mz=101.1, isotope_label="13C"),
                    _row(mz=118.1, ionization_mechanism="[M+NH4]+"),
                ]
            ),
            MECHANISMS,
        )

        assert seeds == {("C5H10O2", "mech-h"), ("C5H10O2", "mech-nh4")}

    def test_an_isotopologue_is_seeded_like_its_parent(self):
        # An isotopologue's ion is a hypothesis about the peak it sits on, and it
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

    def test_no_second_score_reaches_the_row(self):
        # Decision 12: the engine computes one fit. The finder elected this
        # reading with it and the re-score measured the same one, so a second
        # number on the row would name a version that no longer differs.
        [assignment] = _convert(
            [_row()], _peaks(("pA", 100.1, 5000.0)), {("C5H10O2", "mech-h"): 0.55}
        )

        assert "pattern_fit" not in assignment["provenance"]
        assert assignment["provenance"]["score_version"] == 2

    def test_the_noise_the_absent_lines_were_judged_against_is_recorded(self):
        # The difference between a fit scored against the noise and one scored
        # against abundance alone, which nothing else on the row would say.
        [assignment] = _convert(
            [_row(pattern_base_snr=412.7)],
            _peaks(("pA", 100.1, 5000.0)),
            {("C5H10O2", "mech-h"): 0.55},
        )

        assert assignment["provenance"]["base_snr"] == pytest.approx(412.7)

    def test_a_peak_list_with_no_noise_estimate_claims_none(self):
        [assignment] = _convert(
            [_row()], _peaks(("pA", 100.1, 5000.0)), {("C5H10O2", "mech-h"): 0.55}
        )

        assert "base_snr" not in assignment["provenance"]

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
