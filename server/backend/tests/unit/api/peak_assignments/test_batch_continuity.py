"""Unit tests for the ratio-stability pass's pure parts (step 4.11).

No DB: the grade of one ratio series - flat, drifting either way, spiked,
jumping, tied, short, on shared timestamps - the pairs a batch's anchors
form - an isotope line by its family root, chained and split links, two
channels of one neutral - which samples a pair's series counts, the batch's
time-ordering gate, what each anchor records and the summary, and what the
ledger and a snapshot serve of it. The database half (the pass, its triggers
and its route) is ``tests/integration/api/peak_assignments/
test_batch_continuity_api.py``. See ``continuity.py``.
"""

import importlib.util
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from mascope_backend.api.new.peak_assignments.batch_peaks import (
    CO_VARIATION_KEY,
    CONTINUITY_GRADES,
    DRIFT_LOG_RED,
    GRADE_DRIFT,
    GRADE_INCONCLUSIVE,
    GRADE_INSUFFICIENT,
    GRADE_RED,
    GRADE_STABLE,
    MAX_PAIRS_PER_ANCHOR,
    MIN_DISTINCT_TIMES,
    MIN_DRIFT_SAMPLES,
    MIN_PAIRED_SAMPLES,
    PAIR_ADDUCT,
    PAIR_ISOTOPOLOGUE,
    continuity_stale,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_records import (
    _batch_peak_meta,
)
from mascope_backend.api.new.peak_assignments.batch_runs import (
    anchor_snapshot_row,
    snapshot_anchor_meta,
)
from mascope_backend.api.new.peak_assignments.continuity import (
    REASON_BETWEEN,
    REASON_DRIFT,
    REASON_DRIFT_CO_VARYING,
    REASON_FEW_SAMPLES,
    REASON_FEW_TIMES,
    REASON_GAP,
    REASON_SPREAD,
    REASON_TIED,
    REASON_TIGHT,
    REASON_UNTESTED_DRIFT,
    SKIPPED_IN_PROGRESS,
    WARNING_PARTIAL,
    Pair,
    RatioSeries,
    SampleTime,
    _settled,
    acquisition_order,
    continuity_outcome,
    form_pairs,
    grade_batch,
    member_reads_consensus,
    ratio_series,
    read_ratio,
    rollup,
    root_parent_id,
    spearman_rho,
    theil_sen_slope,
    time_ordering,
)
from mascope_backend.db import BatchPeak


NOTATIONS = {"m-h": "[M+H]+", "m-nh4": "[M+NH4]+", "m-na": "[M+Na]+"}
T0 = datetime(2026, 11, 1, tzinfo=timezone.utc)
N = 12
#: The M+1 line's share of a C6 compound's M0.
LINE_SHARE = 0.066


def anchor(key, formula="C6H12O6", mechanism="m-h", **extra):
    """A batch peak as the pass reads one: a monoisotopic M0 by default. Its
    registry holds its own identity at 0 and another formula at 1, which a
    dissenting member names."""
    fields = dict(
        batch_peak_id=key,
        sample_batch_id="sb-1",
        mz=181.0707,
        ionization_mode_id="mode-1",
        intensity_variable="sum_peak_heights",
        consensus_formula=formula,
        consensus_ion_formula=f"{formula}-ion" if formula else None,
        ionization_mechanism_id=mechanism,
        consensus_tier="assigned" if formula else "unassigned",
        consensus_role=None,
        is_ambiguous=0,
        isotopologue_of=None,
        max_intensity=1e5,
        n_present=N,
        best_fit_score=0.9,
        support_fraction=1.0,
        engine_tier=None,
        engine_claim=None,
        candidates=[
            {"formula": formula, "ionization_mechanism_id": mechanism},
            {"formula": "C9H9NO", "ionization_mechanism_id": mechanism},
        ],
        provenance=None,
        continuity_grade=None,
        continuity=None,
    )
    fields.update(extra)
    return SimpleNamespace(**fields)


def line(key, parent, formula="C6H12O6", mechanism="m-h", **extra):
    """An isotope line of ``parent``'s family."""
    fields = dict(
        isotopologue_of=parent,
        consensus_ion_formula=f"[13C]{formula}-ion",
        mz=182.0741,
    )
    fields.update(extra)
    return anchor(key, formula, mechanism, **fields)


def hourly(n=N, **extra):
    return [SampleTime(f"s{i:02d}", T0 + timedelta(hours=i), **extra) for i in range(n)]


def series(log_ratio, seconds=None):
    """A ratio series in acquisition order, an hour apart by default."""
    n = len(log_ratio)
    seconds = [3600.0 * i for i in range(n)] if seconds is None else seconds
    return RatioSeries(
        log_ratio=list(log_ratio), seconds=list(seconds), order=list(range(n))
    )


#: The source's swing: the M0's trace over the batch, three orders of
#: magnitude, which a ratio cancels.
SOURCE = [10 ** (4 + 1.5 * math.sin(0.9 * i)) for i in range(N)]
#: A flat ratio's scatter: small, and no trend.
WOBBLE = [0.03 * math.sin(2.3 * i + 0.4) for i in range(N)]


def flat(base=math.log(LINE_SHARE)):
    return [base + w for w in WOBBLE]


def trace(values, samples, candidate=0):
    return {s.sample_item_id: (v, candidate) for s, v in zip(samples, values)}


# --- the reading -------------------------------------------------------------------


class TestTheReading:
    def test_a_flat_ratio_is_stable_while_its_traces_swing(self):
        reading = read_ratio(series(flat()), PAIR_ISOTOPOLOGUE)
        assert reading.grade == GRADE_STABLE
        assert reading.reason == REASON_TIGHT
        assert reading.n == N
        assert reading.drift_tested
        assert math.exp(reading.median_log) == pytest.approx(LINE_SHARE, rel=0.05)
        assert reading.sigma_log < 0.05

    @pytest.mark.parametrize("direction", [1.0, -1.0])
    def test_an_isotope_ratio_that_drifts_either_way_is_red(self, direction):
        # 0.06 per hour over eleven hours: a 1.9x end-to-end change, rising or
        # falling, which a fixed isotope ratio cannot do.
        values = [v + direction * 0.06 * i for i, v in enumerate(flat())]
        reading = read_ratio(series(values), PAIR_ISOTOPOLOGUE)
        assert reading.grade == GRADE_RED
        assert reading.reason == REASON_DRIFT
        assert reading.rho == pytest.approx(direction, abs=0.05)
        assert reading.drift_log == pytest.approx(direction * 0.66, abs=0.05)

    def test_a_drift_whose_traces_co_vary_is_drift_not_red(self):
        values = [v + 0.1 * i for i, v in enumerate(flat(0.0))]
        assert read_ratio(series(values), PAIR_ADDUCT, co_varying=True).grade == (
            GRADE_DRIFT
        )
        reading = read_ratio(series(values), PAIR_ADDUCT, co_varying=True)
        assert reading.reason == REASON_DRIFT_CO_VARYING
        # Without a reading that says so, the same drift is red.
        assert read_ratio(series(values), PAIR_ADDUCT).grade == GRADE_RED

    def test_the_same_rise_is_loud_for_an_isotope_ratio_and_not_for_a_channel(self):
        # 0.66 end to end: past ln 1.5, short of ln 2.
        values = [v + 0.06 * i for i, v in enumerate(flat())]
        drift = 0.06 * (N - 1)
        assert DRIFT_LOG_RED[PAIR_ISOTOPOLOGUE] < drift < DRIFT_LOG_RED[PAIR_ADDUCT]
        assert read_ratio(series(values), PAIR_ISOTOPOLOGUE).grade == GRADE_RED
        adduct = read_ratio(series(values), PAIR_ADDUCT)
        assert adduct.grade == GRADE_INCONCLUSIVE
        assert adduct.reason == REASON_BETWEEN

    def test_a_slope_with_no_monotone_trend_is_no_drift(self):
        # A rise of 0.77 end to end under a scatter that keeps rho at 0.57:
        # the slope alone is no drift.
        values = [0.07 * i + (0.3 if i % 2 == 0 else -0.3) for i in range(N)]
        reading = read_ratio(series(values), PAIR_ISOTOPOLOGUE)
        assert reading.rho == pytest.approx(0.566, abs=0.01)
        assert abs(reading.drift_log) > DRIFT_LOG_RED[PAIR_ISOTOPOLOGUE]
        assert reading.sigma_log < 0.5
        assert reading.grade == GRADE_INCONCLUSIVE
        assert reading.reason == REASON_BETWEEN

    def test_a_trend_too_small_to_matter_is_no_drift(self):
        # Perfectly monotonic, and 0.11 end to end: under either effect size.
        values = [0.01 * i for i in range(N)]
        reading = read_ratio(series(values), PAIR_ISOTOPOLOGUE)
        assert reading.rho == pytest.approx(1.0)
        assert reading.grade == GRADE_STABLE

    def test_a_spiked_sample_cannot_move_the_grade(self):
        values = flat()
        values[5] += math.log(10.0)  # a co-elution in one sample
        values[9] -= math.log(8.0)  # and a saturated scan in another
        reading = read_ratio(series(values), PAIR_ISOTOPOLOGUE)
        assert reading.grade == GRADE_STABLE
        assert reading.sigma_log < 0.1

    def test_a_ratio_jumping_between_two_levels_is_red_on_its_spread(self):
        values = [0.6 if i % 2 else -0.6 for i in range(N)]
        reading = read_ratio(series(values), PAIR_ADDUCT)
        assert reading.grade == GRADE_RED
        assert reading.reason == REASON_SPREAD
        assert abs(reading.rho) < 0.6

    def test_the_spread_is_the_scaled_median_absolute_deviation(self):
        # A ratio a factor e^0.3 either side of its median: MAD 0.3.
        values = [0.3 if i % 2 else -0.3 for i in range(N)]
        reading = read_ratio(series(values), PAIR_ISOTOPOLOGUE)
        assert reading.sigma_log == pytest.approx(1.4826 * 0.3)

    def test_isotope_physics_pins_a_ratio_tighter_than_a_channel_s(self):
        # A spread of 0.2: past an isotope ratio's 0.15, inside a channel's 0.25.
        values = [0.135 if i % 2 else -0.135 for i in range(N)]
        assert read_ratio(series(values), PAIR_ADDUCT).grade == GRADE_STABLE
        isotope = read_ratio(series(values), PAIR_ISOTOPOLOGUE)
        assert isotope.sigma_log == pytest.approx(0.2, abs=0.001)
        assert isotope.grade == GRADE_INCONCLUSIVE
        assert isotope.reason == REASON_BETWEEN

    def test_tied_ratios_are_no_precision(self):
        # Eight of twelve ratios equal: a deviation of zero.
        values = [0.0, 0.0, 0.1, 0.0, 0.0, -0.1, 0.0, 0.0, 0.2, 0.0, 0.0, -0.2]
        reading = read_ratio(series(values), PAIR_ISOTOPOLOGUE)
        assert reading.sigma_log == 0.0
        assert reading.grade == GRADE_INCONCLUSIVE
        assert reading.reason == REASON_TIED

    def test_a_pair_too_short_for_the_drift_test_is_never_stable(self):
        values = flat()[: MIN_DRIFT_SAMPLES - 1]
        reading = read_ratio(series(values), PAIR_ISOTOPOLOGUE)
        assert not reading.drift_tested
        assert reading.grade == GRADE_INCONCLUSIVE
        assert reading.reason == REASON_UNTESTED_DRIFT
        reading = read_ratio(series(flat()[:MIN_DRIFT_SAMPLES]), PAIR_ISOTOPOLOGUE)
        assert reading.grade == GRADE_STABLE

    def test_a_short_pair_is_still_red_on_a_spread(self):
        values = [0.6 if i % 2 else -0.6 for i in range(MIN_PAIRED_SAMPLES)]
        assert read_ratio(series(values), PAIR_ADDUCT).grade == GRADE_RED

    def test_under_the_paired_samples_gate_nothing_is_claimed(self):
        reading = read_ratio(series(flat()[: MIN_PAIRED_SAMPLES - 1]), PAIR_ADDUCT)
        assert reading.grade == GRADE_INSUFFICIENT
        assert reading.reason == REASON_FEW_SAMPLES
        assert reading.sigma_log is None

    def test_segments_of_one_file_are_one_time_point(self):
        # Ten samples, two files: two acquisition times.
        seconds = [0.0] * 5 + [3600.0] * 5
        reading = read_ratio(series(flat()[:10], seconds), PAIR_ISOTOPOLOGUE)
        assert reading.n == 10
        assert reading.n_times == 2
        assert reading.grade == GRADE_INSUFFICIENT
        seconds = [3600.0 * (i // 2) for i in range(10)]  # five files of two
        reading = read_ratio(series(flat()[:10], seconds), PAIR_ISOTOPOLOGUE)
        assert reading.n_times == MIN_DISTINCT_TIMES
        assert reading.grade == GRADE_STABLE

    def test_spearman_against_order_and_theil_sen_against_time(self):
        import numpy as np

        assert spearman_rho(np.array([3.0, 1.0, 2.0]), np.array([0, 1, 2])) == (
            pytest.approx(-0.5)
        )
        assert spearman_rho(np.array([1.0, 1.0, 1.0]), np.array([0, 1, 2])) is None
        # Two segments share each time and give no slope between them.
        seconds = np.array([0.0, 0.0, 10.0, 10.0, 20.0])
        assert theil_sen_slope(3.0 * seconds, seconds) == pytest.approx(3.0)
        assert theil_sen_slope(np.array([1.0, 2.0]), np.array([5.0, 5.0])) == 0.0


# --- the pairs ---------------------------------------------------------------------


def _pairs(*anchors):
    return {(p.kind, p.a, p.b) for p in form_pairs(anchors, NOTATIONS)}


class TestThePairs:
    def test_an_isotope_line_pairs_with_its_m0(self):
        assert _pairs(anchor("m0"), line("l1", "m0")) == {
            (PAIR_ISOTOPOLOGUE, "m0", "l1")
        }

    def test_a_chained_line_pairs_with_the_family_root(self):
        # The ledger draws l2 under m0, so it pairs with m0, not with l1.
        assert _pairs(anchor("m0"), line("l1", "m0"), line("l2", "l1")) == {
            (PAIR_ISOTOPOLOGUE, "m0", "l1"),
            (PAIR_ISOTOPOLOGUE, "m0", "l2"),
        }

    def test_a_link_the_ledger_cannot_follow_forms_no_pair(self):
        index = {"x": line("x", "y"), "y": line("y", "x")}
        assert root_parent_id(index["x"], index) is None
        assert _pairs(*index.values()) == set()
        # A parent the batch does not hold.
        assert _pairs(line("l1", "gone")) == set()

    def test_a_split_family_s_line_forms_no_pair(self):
        # Its children split their votes: the link is NULL, which the column
        # cannot tell from an M0, and the line's ion is no monoisotopic one.
        split = line("l1", None)
        assert _pairs(anchor("m0"), split) == set()

    def test_a_line_and_root_reading_two_formulas_form_no_pair(self):
        assert _pairs(anchor("m0"), line("l1", "m0", formula="C7H14O5")) == set()

    @pytest.mark.parametrize("tier", ["below_assignability", "unassigned"])
    def test_both_peaks_must_be_assigned_or_candidate(self, tier):
        assert _pairs(anchor("m0"), line("l1", "m0", consensus_tier=tier)) == set()
        assert _pairs(anchor("m0", consensus_tier=tier), line("l1", "m0")) == set()
        assert _pairs(anchor("m0", consensus_tier="candidate"), line("l1", "m0"))

    def test_an_isotope_pair_does_not_cross_an_instrument_partition(self):
        tof = line("l1", "m0", intensity_variable="sum_peak_areas")
        assert _pairs(anchor("m0"), tof) == set()

    def test_two_channels_of_one_neutral_pair(self):
        assert _pairs(anchor("a"), anchor("b", mechanism="m-nh4")) == {
            (PAIR_ADDUCT, "a", "b")
        }
        assert (
            len(
                _pairs(
                    anchor("a"),
                    anchor("b", mechanism="m-nh4"),
                    anchor("c", mechanism="m-na"),
                )
            )
            == 3
        )

    def test_an_ambiguous_channel_pairs_with_nothing(self):
        assert _pairs(anchor("a"), anchor("b", mechanism="m-nh4", is_ambiguous=1)) == (
            set()
        )

    def test_one_peak_reads_a_channel_and_it_is_the_brighter(self):
        pairs = _pairs(
            anchor("a1", max_intensity=10.0),
            anchor("a2", max_intensity=500.0),
            anchor("b", mechanism="m-nh4"),
        )
        assert pairs == {(PAIR_ADDUCT, "a2", "b")}

    def test_a_line_is_no_channel(self):
        # The line repeats its M0's channel; only the M0 pairs across.
        assert _pairs(anchor("a"), line("l1", "a"), anchor("b", mechanism="m-nh4")) == {
            (PAIR_ISOTOPOLOGUE, "a", "l1"),
            (PAIR_ADDUCT, "a", "b"),
        }

    def test_the_best_supported_pairs_come_first(self):
        pairs = form_pairs(
            [
                anchor("a", n_present=5),
                anchor("b", mechanism="m-nh4", n_present=9),
                line("l1", "a", n_present=5),
                anchor("c", formula="C7H14O5", n_present=12),
                line("l2", "c", formula="C7H14O5", n_present=11),
            ],
            NOTATIONS,
        )
        assert [(p.kind, p.b, p.support) for p in pairs] == [
            (PAIR_ISOTOPOLOGUE, "l2", 11),
            (PAIR_ISOTOPOLOGUE, "l1", 5),
            (PAIR_ADDUCT, "b", 5),
        ]


# --- the series --------------------------------------------------------------------


class TestTheSeries:
    def _members(self, samples, **sides):
        return {key: trace(values, samples) for key, values in sides.items()}

    def test_the_ratio_is_the_numerator_over_the_denominator(self):
        samples = hourly()
        members = self._members(samples, m0=SOURCE, l1=[v * LINE_SHARE for v in SOURCE])
        got = ratio_series(
            Pair(PAIR_ISOTOPOLOGUE, "m0", "l1", N),
            anchor("m0"),
            line("l1", "m0"),
            members,
            {s.sample_item_id: s for s in samples},
            acquisition_order(samples),
        )
        assert got.n_both == N
        assert got.log_ratio == pytest.approx([math.log(LINE_SHARE)] * N)
        assert got.order == list(range(N))

    def test_a_dissenting_member_leaves_its_sample_out(self):
        samples = hourly()
        members = self._members(samples, m0=SOURCE, l1=[v * LINE_SHARE for v in SOURCE])
        # Two samples read the line as another compound, one as nothing.
        members["l1"]["s03"] = (members["l1"]["s03"][0] * 40, 1)
        members["l1"]["s07"] = (members["l1"]["s07"][0] * 40, 1)
        members["m0"]["s09"] = (SOURCE[9], None)
        got = ratio_series(
            Pair(PAIR_ISOTOPOLOGUE, "m0", "l1", N),
            anchor("m0"),
            line("l1", "m0"),
            members,
            {s.sample_item_id: s for s in samples},
            acquisition_order(samples),
        )
        assert got.n_excluded_dissenting == 3
        assert len(got.log_ratio) == N - 3
        assert got.log_ratio == pytest.approx([math.log(LINE_SHARE)] * (N - 3))

    def test_blanks_and_missing_intensities_leave_their_samples_out(self):
        samples = hourly()
        samples[2] = SampleTime("s02", samples[2].acquired, blank=True)
        members = self._members(samples, m0=SOURCE, l1=[v * LINE_SHARE for v in SOURCE])
        members["m0"]["s04"] = (0.0, 0)
        members["l1"]["s05"] = (None, 0)
        members["l1"]["s06"] = (float("nan"), 0)
        members["m0"]["s08"] = (float("inf"), 0)
        members["m0"]["unplaced"] = (1.0, 0)
        members["l1"]["unplaced"] = (1.0, 0)
        by_sample = {s.sample_item_id: s for s in samples}
        got = ratio_series(
            Pair(PAIR_ISOTOPOLOGUE, "m0", "l1", N),
            anchor("m0"),
            line("l1", "m0"),
            members,
            by_sample,
            acquisition_order(samples),
        )
        assert got.n_both == N + 1
        assert got.n_excluded_blank == 2  # the blank, and a sample with no time
        assert got.n_excluded_zero == 4
        assert len(got.log_ratio) == N - 5
        assert all(math.isfinite(value) for value in got.log_ratio)

    def test_an_absent_sample_is_a_gap_not_a_point(self):
        samples = hourly()
        members = self._members(
            samples, m0=SOURCE, l1=[v * LINE_SHARE for v in SOURCE][:7]
        )
        got = ratio_series(
            Pair(PAIR_ISOTOPOLOGUE, "m0", "l1", 7),
            anchor("m0"),
            line("l1", "m0"),
            members,
            {s.sample_item_id: s for s in samples},
            acquisition_order(samples),
        )
        assert got.n_both == 7
        assert len(got.log_ratio) == 7

    def test_only_a_member_naming_the_consensus_formula_reads_it(self):
        m0 = anchor("m0")
        assert member_reads_consensus(m0, 0)
        assert not member_reads_consensus(m0, 1)
        assert not member_reads_consensus(m0, None)
        assert not member_reads_consensus(m0, 7)
        assert not member_reads_consensus(anchor("u", formula=None), 0)

    def test_segments_are_ordered_by_where_they_start(self):
        at = T0
        order = acquisition_order(
            [
                SampleTime("b", at, t0=30.0),
                SampleTime("a", at, t0=60.0),
                SampleTime("c", at - timedelta(hours=1)),
            ]
        )
        assert order == {"c": 0, "b": 1, "a": 2}


# --- the time axis -----------------------------------------------------------------


class TestTimeOrdering:
    def test_an_acquisition_batch_is_time_ordered(self):
        assert time_ordering("ACQUISITION", hourly(2))[0] is True

    def test_an_evenly_spaced_analysis_batch_is(self):
        ordered, reason, facts = time_ordering("ANALYSIS", hourly())
        assert (ordered, reason) == (True, None)
        assert facts["distinct_times"] == N
        assert facts["median_gap_seconds"] == 3600.0

    def test_one_gap_far_above_the_median_breaks_it(self):
        samples = hourly()
        samples.append(SampleTime("late", T0 + timedelta(days=30)))
        assert time_ordering("ANALYSIS", samples)[:2] == (False, REASON_GAP)

    def test_the_gaps_are_between_distinct_times(self):
        # Six files of four segments each: a median gap over samples is zero.
        samples = [
            SampleTime(f"s{f}-{k}", T0 + timedelta(hours=f), t0=60.0 * k)
            for f in range(6)
            for k in range(4)
        ]
        assert time_ordering("ANALYSIS", samples)[:2] == (True, None)

    def test_too_few_distinct_times_is_not_a_series(self):
        samples = [
            SampleTime(f"s{f}-{k}", T0 + timedelta(hours=f), t0=60.0 * k)
            for f in range(MIN_DISTINCT_TIMES - 1)
            for k in range(4)
        ]
        assert time_ordering("ANALYSIS", samples)[:2] == (False, REASON_FEW_TIMES)

    def test_blanks_are_no_time_points(self):
        samples = hourly(MIN_DISTINCT_TIMES - 1) + [
            SampleTime("blank", T0 + timedelta(hours=8), blank=True)
        ]
        assert time_ordering("ANALYSIS", samples)[:2] == (False, REASON_FEW_TIMES)


# --- the batch ---------------------------------------------------------------------


def _co_variation(own, other, verdict):
    """A standing 4.2 record on ``own`` naming ``other`` as a partner."""
    return {
        CO_VARIATION_KEY: {
            "formula": own.consensus_formula,
            "ionization_mechanism_id": own.ionization_mechanism_id,
            "verdict": verdict,
            "partners": [
                {"batch_peak_id": other, "verdict": verdict, "r": 0.91, "n_spectra": N}
            ],
        }
    }


def _family_batch(samples):
    """An M0 with a stable M+1, and a second channel drifting against it."""
    m0, l1 = anchor("m0"), line("l1", "m0")
    nh4 = anchor("nh4", mechanism="m-nh4", mz=198.0972)
    members = {
        "m0": trace(SOURCE, samples),
        "l1": trace(
            [v * LINE_SHARE * math.exp(w) for v, w in zip(SOURCE, WOBBLE)], samples
        ),
        "nh4": trace(
            [v * 0.5 * math.exp(0.1 * i) for i, v in enumerate(SOURCE)], samples
        ),
    }
    return [m0, l1, nh4], members


class TestTheBatch:
    def test_every_anchor_in_a_pair_records_it_from_its_own_side(self):
        samples = hourly()
        anchors, members = _family_batch(samples)
        records, summary = grade_batch(
            anchors, NOTATIONS, members, samples, "ACQUISITION", computed_utc="t"
        )
        m0_grade, m0 = records["m0"]
        l1_grade, l1 = records["l1"]
        nh4_grade, nh4 = records["nh4"]
        # The drifting channel outranks the stable line on the M0.
        assert m0_grade == GRADE_RED
        assert l1_grade == GRADE_STABLE
        assert nh4_grade == GRADE_RED
        assert [p["kind"] for p in m0["pairs"]] == [PAIR_ADDUCT, PAIR_ISOTOPOLOGUE]
        to_line = m0["pairs"][1]
        assert to_line["partner_batch_peak_id"] == "l1"
        assert to_line["ratio_median"] == pytest.approx(LINE_SHARE, rel=0.05)
        assert to_line["co_variation"] is None
        [to_m0] = l1["pairs"]
        assert to_m0["ratio_median"] == pytest.approx(1 / LINE_SHARE, rel=0.05)
        assert to_m0["sigma_log"] == to_line["sigma_log"]
        # A rise read from one side is a fall read from the other.
        assert m0["pairs"][0]["drift_log"] == pytest.approx(1.1, abs=0.05)
        assert nh4["pairs"][0]["drift_log"] == pytest.approx(-1.1, abs=0.05)
        assert nh4["pairs"][0]["rho"] == -m0["pairs"][0]["rho"]
        assert nh4["pairs"][0]["drift_fold"] == pytest.approx(math.exp(1.1), rel=0.05)
        # What was graded, which a reader holds the ledger against.
        assert m0["formula"] == "C6H12O6"
        assert m0["ionization_mechanism_id"] == "m-h"
        assert m0["n_present_at_compute"] == N
        assert m0["eligible"] is True
        assert m0["computed_utc"] == "t"
        assert m0["n_pairs"] == 2
        assert m0["grades"] == {GRADE_RED: 1, GRADE_STABLE: 1}
        assert summary["pairs"] == 2
        assert summary["pairs_by_kind"] == {PAIR_ISOTOPOLOGUE: 1, PAIR_ADDUCT: 1}
        assert summary["grades"][GRADE_RED] == 1
        assert summary["grades"][GRADE_STABLE] == 1
        assert summary["anchors"] == {
            GRADE_RED: 2,
            GRADE_DRIFT: 0,
            GRADE_STABLE: 1,
            GRADE_INCONCLUSIVE: 0,
            GRADE_INSUFFICIENT: 0,
        }

    @pytest.mark.parametrize(
        "verdict, grade", [("co_varying", GRADE_DRIFT), ("not_co_varying", GRADE_RED)]
    )
    def test_the_co_variation_reading_decides_a_channel_drift(self, verdict, grade):
        samples = hourly()
        anchors, members = _family_batch(samples)
        # The record is on either anchor; here it is the partner's.
        anchors[2].provenance = _co_variation(anchors[2], "m0", verdict)
        records, _ = grade_batch(
            anchors, NOTATIONS, members, samples, "ACQUISITION", computed_utc="t"
        )
        [to_nh4] = [p for p in records["m0"][1]["pairs"] if p["kind"] == PAIR_ADDUCT]
        assert to_nh4["grade"] == grade
        assert to_nh4["co_variation"] == {"verdict": verdict, "r": 0.91, "n_spectra": N}

    def test_the_record_s_entry_for_this_partner_is_the_one_read(self):
        samples = hourly()
        anchors, members = _family_batch(samples)
        record = _co_variation(anchors[2], "m0", "co_varying")
        partners = record[CO_VARIATION_KEY]["partners"]
        # Another partner of the channel, read first, said otherwise.
        partners.insert(
            0,
            {"batch_peak_id": "other", "verdict": "not_co_varying", "r": 0.1},
        )
        anchors[2].provenance = record
        records, _ = grade_batch(
            anchors, NOTATIONS, members, samples, "ACQUISITION", computed_utc="t"
        )
        [to_nh4] = [p for p in records["m0"][1]["pairs"] if p["kind"] == PAIR_ADDUCT]
        assert to_nh4["co_variation"]["r"] == 0.91
        assert to_nh4["grade"] == GRADE_DRIFT

    def test_the_spread_is_shown_as_a_factor(self):
        samples = hourly()
        anchors, members = _family_batch(samples)
        records, _ = grade_batch(
            anchors, NOTATIONS, members, samples, "ACQUISITION", computed_utc="t"
        )
        [to_line] = records["l1"][1]["pairs"]
        assert to_line["ratio_spread"] == pytest.approx(
            math.exp(to_line["sigma_log"]) - 1, abs=1e-4
        )

    def test_a_record_read_for_another_claim_says_nothing(self):
        samples = hourly()
        anchors, members = _family_batch(samples)
        record = _co_variation(anchors[2], "m0", "co_varying")
        record[CO_VARIATION_KEY]["ionization_mechanism_id"] = "m-na"
        anchors[2].provenance = record
        records, _ = grade_batch(
            anchors, NOTATIONS, members, samples, "ACQUISITION", computed_utc="t"
        )
        assert records["nh4"][1]["pairs"][0]["co_variation"] is None
        assert records["nh4"][0] == GRADE_RED

    def test_a_batch_that_is_not_time_ordered_has_its_grades_withdrawn(self):
        samples = hourly()
        samples.append(SampleTime("late", T0 + timedelta(days=30)))
        anchors, members = _family_batch(samples)
        records, summary = grade_batch(
            anchors, NOTATIONS, members, samples, "ANALYSIS", computed_utc="t"
        )
        assert set(records) == {"m0", "l1", "nh4"}
        for grade, detail in records.values():
            assert grade is None
            assert detail["eligible"] is False
            assert detail["reason"] == REASON_GAP
            assert detail["pairs"] == []
        assert records["m0"][1]["n_pairs"] == 2
        assert summary["time_ordered"] is False
        assert summary["pairs_graded"] == 0

    def test_a_pass_over_its_limit_marks_what_it_left(self):
        samples = hourly()
        anchors, members = _family_batch(samples)
        # Both pairs hold twelve samples: the isotope pair goes first.
        records, summary = grade_batch(
            anchors,
            NOTATIONS,
            members,
            samples,
            "ACQUISITION",
            computed_utc="t",
            max_pairs=1,
        )
        assert summary["pairs_graded"] == 1
        assert summary["pairs_skipped"] == 1
        assert records["l1"] == (GRADE_STABLE, records["l1"][1])
        assert records["l1"][1]["warnings"] == []
        # The M0 is graded on the pair it got, and says it is short of one.
        assert records["m0"][0] == GRADE_STABLE
        assert records["m0"][1]["warnings"] == [WARNING_PARTIAL]
        assert records["m0"][1]["n_pairs"] == 2
        # The channel's only pair was left: no grade, and the reason.
        assert records["nh4"][0] is None
        assert records["nh4"][1]["warnings"] == [WARNING_PARTIAL]
        assert records["nh4"][1]["pairs"] == []

    def test_the_detail_lists_the_alarms_first_and_the_grade_reads_every_pair(self):
        samples = hourly()
        m0 = anchor("m0")
        anchors = [m0]
        members = {"m0": trace(SOURCE, samples)}
        for k in range(MAX_PAIRS_PER_ANCHOR + 2):
            key = f"l{k}"
            anchors.append(line(key, "m0", mz=182.0 + k))
            red = k == MAX_PAIRS_PER_ANCHOR + 1
            # The stable lines are held in 8 to 12 samples, the later the more;
            # the red one in the fewest the drift test runs on, so that the
            # stable lines would crowd it out of a list capped by support alone.
            held = MIN_DRIFT_SAMPLES if red else MIN_DRIFT_SAMPLES + k
            members[key] = trace(
                [
                    v * LINE_SHARE * math.exp(w + (0.08 * i if red else 0.0))
                    for i, (v, w) in enumerate(zip(SOURCE, WOBBLE))
                ][:held],
                samples,
            )
        records, _ = grade_batch(
            anchors, NOTATIONS, members, samples, "ACQUISITION", computed_utc="t"
        )
        grade, detail = records["m0"]
        assert grade == GRADE_RED
        assert detail["n_pairs"] == MAX_PAIRS_PER_ANCHOR + 2
        assert len(detail["pairs"]) == MAX_PAIRS_PER_ANCHOR
        # The alarm first, then the stable lines held in the most samples.
        assert [p["partner_batch_peak_id"] for p in detail["pairs"]] == [
            "l5",
            "l4",
            "l3",
            "l2",
        ]
        assert detail["pairs"][0]["grade"] == GRADE_RED
        assert [p["n"] for p in detail["pairs"]] == [MIN_DRIFT_SAMPLES, 12, 11, 10]
        assert detail["grades"] == {
            GRADE_RED: 1,
            GRADE_STABLE: MAX_PAIRS_PER_ANCHOR + 1,
        }

    def test_an_anchor_in_no_pair_gets_no_record(self):
        samples = hourly()
        anchors, members = _family_batch(samples)
        anchors.append(anchor("lone", formula="C9H9NO"))
        records, _ = grade_batch(
            anchors, NOTATIONS, members, samples, "ACQUISITION", computed_utc="t"
        )
        assert "lone" not in records

    def test_the_detail_is_json_and_finite(self):
        import json

        samples = hourly(MIN_PAIRED_SAMPLES - 1)
        anchors, members = _family_batch(hourly())
        records, _ = grade_batch(
            anchors, NOTATIONS, members, samples, "ACQUISITION", computed_utc="t"
        )
        for _, detail in records.values():
            json.dumps(detail, allow_nan=False)
            assert detail["pairs"][0]["grade"] == GRADE_INSUFFICIENT


# --- the rollup, the outcome and the read model --------------------------------------


def test_an_anchor_takes_its_most_alarming_pair_s_grade():
    assert rollup([GRADE_STABLE, GRADE_INSUFFICIENT, GRADE_DRIFT]) == GRADE_DRIFT
    assert rollup([GRADE_DRIFT, GRADE_RED]) == GRADE_RED
    assert rollup([GRADE_INCONCLUSIVE, GRADE_STABLE]) == GRADE_STABLE
    assert rollup([GRADE_INSUFFICIENT, GRADE_INCONCLUSIVE]) == GRADE_INCONCLUSIVE
    assert rollup([]) is None


def test_two_passes_reaching_one_reading_compare_equal():
    assert _settled({"computed_utc": "a", "n": 1}) == _settled(
        {"computed_utc": "b", "n": 1}
    )
    assert _settled(None) is None


def _graded(**over):
    fields = dict(
        continuity_grade=GRADE_STABLE,
        continuity={
            "formula": "C6H12O6",
            "ionization_mechanism_id": "m-h",
            "n_present_at_compute": N,
            "pairs": [],
        },
    )
    fields.update(over)
    return anchor("m0", **fields)


@pytest.mark.parametrize(
    "over, stale",
    [
        ({}, False),
        ({"n_present": N + 1}, True),
        ({"consensus_formula": "C7H14O5"}, True),
        ({"ionization_mechanism_id": "m-nh4"}, True),
        ({"continuity": None}, False),
    ],
)
def test_a_grade_the_ledger_has_moved_past_is_stale(over, stale):
    assert continuity_stale(_graded(**over)) is stale


def test_the_ledger_and_a_snapshot_serve_the_grade_and_whether_it_is_stale():
    graded = _graded()
    meta = _batch_peak_meta(graded)
    assert meta["continuity_grade"] == GRADE_STABLE
    assert meta["continuity"] == graded.continuity
    assert meta["continuity_stale"] is False
    row = anchor_snapshot_row("run-1", graded, [])
    assert row["continuity_grade"] == GRADE_STABLE
    assert row["continuity"] == graded.continuity
    snap = snapshot_anchor_meta(SimpleNamespace(**row), "sb-1")
    assert snap["continuity_grade"] == GRADE_STABLE
    assert snap["continuity_stale"] is False
    # A snapshot of a ledger already past its grade says so.
    row["n_present"] = N + 3
    assert snapshot_anchor_meta(SimpleNamespace(**row), "sb-1")["continuity_stale"]
    assert _batch_peak_meta(_graded(n_present=N + 3))["continuity_stale"]


def test_the_outcome_names_what_happened():
    assert continuity_outcome({"skipped": SKIPPED_IN_PROGRESS}, "sb")["status"] == (
        "partial"
    )
    none = continuity_outcome({"pairs": 0}, "sb")
    assert none["status"] == "partial"
    assert "no batch peak" in none["message"]
    unordered = continuity_outcome(
        {"pairs": 3, "time_ordered": False, "reason": REASON_GAP}, "sb"
    )
    assert unordered["status"] == "partial"
    assert "not time-ordered" in unordered["message"]
    grades = {grade: 0 for grade in CONTINUITY_GRADES}
    grades[GRADE_STABLE] = 2
    done = continuity_outcome(
        {
            "pairs": 3,
            "time_ordered": True,
            "pairs_graded": 2,
            "pairs_skipped": 1,
            "grades": grades,
        },
        "sb",
    )
    assert done["status"] == "success"
    assert "2 pairs" in done["message"]
    assert "2 stable" in done["message"]
    assert "1 more" in done["message"]
    assert "No tier moved" in done["message"]
    assert done["_notification_data"] == {"sample_batch_id": "sb"}


def test_the_grades_the_database_accepts_are_the_pass_s():
    """The model's CHECK and the migration's are spelt from one list."""
    check = next(
        c
        for c in BatchPeak.__table__.constraints
        if str(c.name).endswith("continuity_grade_valid")
    )
    for grade in CONTINUITY_GRADES:
        assert f"'{grade}'" in str(check.sqltext)
    path = next(
        Path(__file__)
        .resolve()
        .parents[4]
        .joinpath("alembic", "versions")
        .glob("*_batch_peak_continuity.py")
    )
    spec = importlib.util.spec_from_file_location("continuity_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.GRADES == ", ".join(f"'{g}'" for g in CONTINUITY_GRADES)
