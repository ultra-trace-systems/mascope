"""What a run measures of its own mass accuracy, and what it does about it.

The gate's arithmetic is small and its judgement is not: which rows are allowed
to define the run's centre decides which rows can be found to sit away from it,
and a mistake there condemns exactly the samples that most need the correction -
the ones sitting a ppm out. These pin the corroboration rule, the fit over it,
the direction the cap may move a tier, and the two ways a run declines to gate
at all.
"""

import pytest

from mascope_backend.api.new.peak_assignments.mass_gate import (
    BELOW_ASSIGNABILITY_Z,
    CORROBORATED_CURATED,
    CORROBORATED_ISOTOPOLOGUE,
    OFF_CALIBRATION_Z,
    REASON_OFF_CALIBRATION,
    apply_mass_gate,
    corroboration_of,
    fit_run_mass_accuracy,
    tracking_tolerance_ppm,
)
from mascope_backend.api.new.peak_assignments.tiers import (
    TIER_ASSIGNED,
    TIER_BELOW_ASSIGNABILITY,
    TIER_CANDIDATE,
    TIER_UNASSIGNED,
)


#: The instrument class's precision the gate is exercised at here: the
#: Orbitrap's, so a satellite tracks its parent within 0.3 ppm and the width
#: the search scored at - the floor on the gate's own width - is hypot(0.3, 0.5).
PRECISION = 0.3


def _row(
    row_id,
    *,
    ppm=0.0,
    tier=TIER_ASSIGNED,
    role="M0",
    source="untargeted",
    formula="C6H12O6",
    owner=None,
):
    return {
        "peak_assignment_id": row_id,
        "role": role,
        "source": source,
        "assigned_formula": formula,
        "mz_error_ppm": ppm,
        "tier": tier,
        "owner_peak_assignment_id": owner,
        "provenance": {},
    }


def _anchors(count, *, ppm=0.0, spread=0.1, start=0):
    """`count` corroborated commits scattered evenly about `ppm`."""
    return [
        _row(
            f"anchor-{start + i}",
            ppm=ppm + (spread if i % 2 else -spread),
            source="database",
        )
        for i in range(count)
    ]


class TestWhatCorroboratesACommit:
    """Which rows the run has more than a mass fit for."""

    def test_a_curated_row_is_corroborated_by_its_library(self):
        rows = [_row("a", source="database")]

        assert corroboration_of(rows, precision_ppm=PRECISION) == {
            "a": CORROBORATED_CURATED
        }

    def test_an_envelope_corroborates_both_of_its_rows(self):
        # The M0 by having kept an isotopologue, the child by having an owner:
        # one piece of evidence, and it is evidence about the ion rather than
        # about either row on its own.
        rows = [_row("m0"), _row("child", role="iso_child", owner="m0")]

        assert corroboration_of(rows, precision_ppm=PRECISION) == {
            "m0": CORROBORATED_ISOTOPOLOGUE,
            "child": CORROBORATED_ISOTOPOLOGUE,
        }

    def test_an_untargeted_row_alone_is_corroborated_by_nothing(self):
        assert corroboration_of([_row("a")], precision_ppm=PRECISION) == {"a": None}

    def test_a_pre_pass_row_is_not_a_commit_at_all(self):
        # A reagent hit and a ringing artifact name no formula, so neither has a
        # mass error that says "this composition is this far out" - the reagent
        # row's error is the distance to a cluster ion the run deliberately did
        # not assign. Neither anchors the fit nor is judged by it.
        rows = [
            _row("reagent", role="reagent", source="reagent", formula=None, ppm=4.0),
            _row("artifact", role="artifact", source="artifact", formula=None),
            _row("blank", role="unassigned", formula=None, tier=TIER_UNASSIGNED),
        ]

        assert corroboration_of(rows, precision_ppm=PRECISION) == {}


class TestTheRunsOwnCalibration:
    """The fit over the corroborated commits, and when it refuses one."""

    def test_it_measures_the_centre_the_corroborated_rows_sit_at(self):
        rows = _anchors(12, ppm=-1.2)

        calibration = fit_run_mass_accuracy(
            rows, corroboration_of(rows, precision_ppm=PRECISION)
        )

        assert calibration.mu_ppm == pytest.approx(-1.2)
        assert calibration.sigma_ppm == pytest.approx(0.1, rel=0.5)
        assert calibration.anchors == 12
        assert calibration.measured

    def test_an_uncorroborated_row_does_not_widen_the_distribution(self):
        # The rows being judged must not set the width they are judged at: a
        # distribution fitted over them would stretch to cover whatever sits in
        # its own tail, and the gate could then never find anything.
        anchors = _anchors(12)
        wild = [_row(f"wild-{i}", ppm=9.0) for i in range(12)]

        with_wild = fit_run_mass_accuracy(
            anchors + wild, corroboration_of(anchors + wild, precision_ppm=PRECISION)
        )
        without = fit_run_mass_accuracy(
            anchors, corroboration_of(anchors, precision_ppm=PRECISION)
        )

        assert with_wild.sigma_ppm == pytest.approx(without.sigma_ppm)
        assert with_wild.anchors == without.anchors == 12

    def test_too_few_corroborated_commits_measure_nothing(self):
        rows = _anchors(3)

        calibration = fit_run_mass_accuracy(
            rows, corroboration_of(rows, precision_ppm=PRECISION)
        )

        assert not calibration.measured
        assert calibration.z_of(5.0) is None


class TestTheGate:
    """What the distance costs a row that has nothing else behind it."""

    def test_it_records_the_distance_on_every_commit(self):
        # Twelve anchors at +-0.1 ppm fit a centre of 0 and a width of
        # 1.4826 x 0.1 = 0.148 ppm, but the distance is expressed in the width
        # the gate judges at - here the search's hypot(0.3, 0.5) = 0.583, the
        # wider of the two - so a row 1 ppm out is 1.7 of it. That is the
        # number a reader needs, and it is not readable off the 1 ppm alone.
        rows = _anchors(12) + [_row("far", ppm=1.0)]

        summary = apply_mass_gate(rows, fallback_sigma_ppm=PRECISION)

        assert rows[-1]["provenance"]["mass_z"] == pytest.approx(1.72, rel=0.02)
        assert all("mass_z" in row["provenance"] for row in rows)
        # Both widths are on the record, so the ratio above is checkable.
        assert summary["sigma_ppm"] == pytest.approx(0.148, rel=0.05)
        assert summary["gate_sigma_ppm"] == pytest.approx(0.583, rel=0.01)
        assert summary["search_sigma_ppm"] == pytest.approx(0.583, rel=0.01)

    def test_the_distance_is_measured_from_the_run_s_own_centre(self):
        # The reason the offset half of the fit is load-bearing. Every row of
        # this sample sits 1.2 ppm low, which is the calibration, not an error:
        # measured from zero the whole ledger is off calibration, and the gate
        # would demote a correct run entire.
        rows = _anchors(12, ppm=-1.2) + [_row("typical", ppm=-1.2)]

        summary = apply_mass_gate(rows, fallback_sigma_ppm=PRECISION)

        assert rows[-1]["provenance"]["mass_z"] == pytest.approx(0.0, abs=0.5)
        assert rows[-1]["tier"] == TIER_ASSIGNED
        assert summary["capped"] == 0

    def test_an_uncorroborated_outlier_is_capped_at_candidate(self):
        rows = _anchors(12, spread=0.1) + [_row("off", ppm=2.5)]

        summary = apply_mass_gate(rows, fallback_sigma_ppm=PRECISION)

        assert rows[-1]["tier"] == TIER_CANDIDATE
        assert rows[-1]["provenance"]["mass_gate"] == {
            "corroborated_by": None,
            "capped": TIER_CANDIDATE,
            "reason": REASON_OFF_CALIBRATION,
        }
        assert summary["capped"] == 1
        # The formula stays on the row: what the run withdraws is its
        # confidence, not its reading.
        assert rows[-1]["assigned_formula"] == "C6H12O6"

    def test_far_enough_out_it_is_below_assignability(self):
        rows = _anchors(12, spread=0.1) + [_row("wild", ppm=5.0)]

        summary = apply_mass_gate(rows, fallback_sigma_ppm=PRECISION)

        assert rows[-1]["tier"] == TIER_BELOW_ASSIGNABILITY
        assert summary["below_assignability"] == 1

    def test_a_corroborated_outlier_is_recorded_and_kept(self):
        # A confirmed envelope is evidence the mass error does not overrule -
        # and on the gate sets a fifth of the corroborated rows sit beyond three
        # sigma, so a gate that demoted them would be demoting its own anchors.
        # The distance is still recorded: the row is an outlier by this run's
        # own reckoning and says so, it is just not demoted for it.
        rows = _anchors(12, spread=0.1) + [
            _row("m0", ppm=5.0),
            _row("child", ppm=5.0, role="iso_child", owner="m0"),
        ]

        summary = apply_mass_gate(rows, fallback_sigma_ppm=PRECISION)

        assert rows[-2]["tier"] == TIER_ASSIGNED
        assert abs(rows[-2]["provenance"]["mass_z"]) > BELOW_ASSIGNABILITY_Z
        assert rows[-2]["provenance"]["mass_gate"] == {
            "corroborated_by": CORROBORATED_ISOTOPOLOGUE
        }
        assert summary["capped"] == 0

    def test_it_only_ever_demotes(self):
        # A row the bands already put below the cap keeps the tier they gave it,
        # and the gate says nothing about it: it did not decide that tier and
        # must not appear to have.
        rows = _anchors(12, spread=0.1) + [_row("low", ppm=0.6, tier=TIER_CANDIDATE)]

        summary = apply_mass_gate(rows, fallback_sigma_ppm=PRECISION)

        assert rows[-1]["tier"] == TIER_CANDIDATE
        assert "capped" not in rows[-1]["provenance"]["mass_gate"]
        assert summary["capped"] == 0

    def test_a_run_that_measured_no_calibration_gates_nothing(self):
        # Standing down is recorded rather than left indistinguishable from a
        # run that found nothing to demote: a sample with three corroborated
        # commits has not earned the right to withdraw a tier.
        rows = _anchors(3) + [_row("far", ppm=9.0)]

        summary = apply_mass_gate(rows, fallback_sigma_ppm=PRECISION)

        assert summary["applied"] is False
        assert summary["capped"] == 0
        assert rows[-1]["tier"] == TIER_ASSIGNED
        assert "mass_z" not in rows[-1]["provenance"]
        assert rows[-1]["provenance"]["mass_gate"] == {"corroborated_by": None}

    def test_a_commit_with_no_mass_error_is_left_alone(self):
        rows = _anchors(12) + [_row("no_error", ppm=None)]

        apply_mass_gate(rows, fallback_sigma_ppm=PRECISION)

        assert "mass_z" not in rows[-1]["provenance"]
        assert rows[-1]["tier"] == TIER_ASSIGNED

    def test_the_run_records_what_it_fitted_and_what_it_cost(self):
        rows = _anchors(12, ppm=-0.5, spread=0.1) + [_row("off", ppm=1.5)]

        summary = apply_mass_gate(rows, fallback_sigma_ppm=PRECISION)

        assert summary["mu_ppm"] == pytest.approx(-0.5)
        assert summary["mu_source"] == "fitted"
        assert summary["sigma_source"] == "fitted"
        assert summary["anchors"] == 12
        assert summary["committed"] == 13
        assert summary["corroborated"] == 12
        assert summary["cap_z"] == OFF_CALIBRATION_Z
        assert summary["floor_z"] == BELOW_ASSIGNABILITY_Z
        assert summary["capped"] == 1


class TestTheStageAOnlyLedger:
    """Why the run-less ingest fold needs no gate to agree with a run.

    ``_fold_sample_peaks_without_run`` runs Stage A and the two pre-passes and
    does not call the gate. That is only safe while every commit such a ledger
    can hold is one a curated identity proposed, which is what this pins: add an
    uncorroborated Stage A source, or the untargeted stage to that path, and
    this fails rather than the two ledgers quietly tiering two ways.
    """

    def test_a_stage_a_ledger_has_nothing_the_gate_could_demote(self):
        rows = [
            _row("a", ppm=9.0, source="database"),
            _row("b", ppm=-9.0, source="database"),
            *_anchors(10, start=10),
            _row("reagent", role="reagent", source="reagent", formula=None, ppm=8.0),
        ]

        summary = apply_mass_gate(rows, fallback_sigma_ppm=PRECISION)

        assert summary["applied"] is True
        assert summary["capped"] == 0
        assert all(
            row["tier"] == TIER_ASSIGNED for row in rows if row["assigned_formula"]
        )


class TestWhenASatelliteIsEvidence:
    """A child corroborates its parent only if it measures the same axis.

    The rule these pin was written against a measurement: on the three TOF
    sets the child-minus-parent mass error is 4.6 to 6.5 ppm wide with 37-46%
    of children inside 3 ppm of their parent, against 0.28-0.45 ppm and 98-99%
    on the Orbitrap sets. A satellite is paired inside the class's matching
    window - 15 ppm on a TOF - so on a crowded spectrum a peak that is nobody's
    isotopologue lands there by coincidence, and without this rule the
    coincidence corroborates the reading it was matched to and then anchors the
    run's calibration.
    """

    def test_a_child_whose_error_tracks_its_parent_corroborates_it(self):
        rows = [
            _row("m0", ppm=0.4),
            _row("child", ppm=0.5, role="iso_child", owner="m0"),
        ]

        assert corroboration_of(rows, precision_ppm=PRECISION) == {
            "m0": CORROBORATED_ISOTOPOLOGUE,
            "child": CORROBORATED_ISOTOPOLOGUE,
        }

    def test_a_child_that_does_not_track_corroborates_nothing(self):
        # Including itself: a peak that is not this ion's isotopologue is not
        # evidence for the ion, and having been paired to it is not evidence
        # for the peak either.
        rows = [
            _row("m0", ppm=0.4),
            _row("child", ppm=4.0, role="iso_child", owner="m0"),
        ]

        assert corroboration_of(rows, precision_ppm=PRECISION) == {
            "m0": None,
            "child": None,
        }

    def test_one_tracking_child_is_enough(self):
        rows = [
            _row("m0", ppm=0.4),
            _row("near", ppm=0.5, role="iso_child", owner="m0"),
            _row("far", ppm=9.0, role="iso_child", owner="m0"),
        ]

        corroboration = corroboration_of(rows, precision_ppm=PRECISION)

        assert corroboration["m0"] == CORROBORATED_ISOTOPOLOGUE
        assert corroboration["near"] == CORROBORATED_ISOTOPOLOGUE
        assert corroboration["far"] is None

    def test_the_bar_is_three_sigma_of_the_difference_not_one(self):
        # The quantity tested is a difference of two measurements, and on the
        # sets with real envelopes it is about as wide as the class's precision
        # (0.28 to 0.45 ppm against a 0.3 ppm class). Testing at the bare
        # precision is a two-thirds-of-one-sigma test: measured on the gate it
        # threw away a third to a half of the genuine children (A 59% kept, D
        # 50%), which are exactly the rows step 2.4 reads as corroboration.
        rows = [
            _row("m0", ppm=0.0),
            _row("child", ppm=0.6, role="iso_child", owner="m0"),
        ]

        assert corroboration_of(rows, precision_ppm=PRECISION) == {
            "m0": CORROBORATED_ISOTOPOLOGUE,
            "child": CORROBORATED_ISOTOPOLOGUE,
        }
        assert tracking_tolerance_ppm(PRECISION) == pytest.approx(0.9)

    def test_the_bar_is_the_instrument_class_s(self):
        # The same pair, read on two instruments: 4 ppm apart is a coincidence
        # on an Orbitrap (bar 0.9) and ordinary centroiding on a TOF (bar 9.0).
        rows = [
            _row("m0", ppm=0.0),
            _row("child", ppm=4.0, role="iso_child", owner="m0"),
        ]

        assert corroboration_of(rows, precision_ppm=0.3)["m0"] is None
        assert (
            corroboration_of(rows, precision_ppm=3.0)["m0"] == CORROBORATED_ISOTOPOLOGUE
        )


class TestWhatAnchorsTheCalibration:
    """Monoisotopic rows only, and why the width has a floor."""

    def test_a_satellite_does_not_anchor_the_fit(self):
        # It is the same ion on a weaker peak, so it is the wider row wherever
        # it is real - and where it is not real it is the coincidence above.
        # Measured: letting satellites anchor put the bromide TOF set at 4.4-4.6
        # ppm wide when its own uncorroborated M0 rows sit at 2.7-2.9.
        anchors = _anchors(12, spread=0.1)
        children = [
            _row(f"child-{i}", ppm=0.25, role="iso_child", owner=f"anchor-{i}")
            for i in range(12)
        ]

        calibration = fit_run_mass_accuracy(
            anchors + children,
            corroboration_of(anchors + children, precision_ppm=PRECISION),
        )

        assert calibration.anchors == 12

    def test_the_gate_never_judges_tighter_than_the_search_scored(self):
        # The anchors are the run's best-corroborated rows and so its best
        # measured ones; the rows the gate judges rest on the mass fit alone and
        # spread wider. Judging the second by the first condemns its tails by
        # construction - on the sparse Orbitrap set it demoted 105 rows the
        # reference confirms. A row cannot be off calibration for a distance its
        # own search was told to accept.
        rows = _anchors(12, spread=0.01) + [_row("off", ppm=1.0)]

        summary = apply_mass_gate(rows, fallback_sigma_ppm=PRECISION)

        assert summary["sigma_ppm"] < summary["gate_sigma_ppm"]
        assert summary["gate_sigma_ppm"] == pytest.approx(summary["search_sigma_ppm"])
        assert summary["capped"] == 0
        assert rows[-1]["tier"] == TIER_ASSIGNED

    def test_a_run_that_measures_a_wider_axis_keeps_its_own_width(self):
        # The floor is a floor, not a replacement: a sample whose corroborated
        # rows genuinely scatter wider than the search's width is judged at what
        # it measured.
        rows = _anchors(12, spread=2.0) + [_row("off", ppm=30.0)]

        summary = apply_mass_gate(rows, fallback_sigma_ppm=PRECISION)

        assert summary["gate_sigma_ppm"] == pytest.approx(summary["sigma_ppm"])
        assert summary["gate_sigma_ppm"] > summary["search_sigma_ppm"]
        assert summary["capped"] == 1
