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
)
from mascope_backend.api.new.peak_assignments.tiers import (
    TIER_ASSIGNED,
    TIER_BELOW_ASSIGNABILITY,
    TIER_CANDIDATE,
    TIER_UNASSIGNED,
)


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

        assert corroboration_of(rows) == {"a": CORROBORATED_CURATED}

    def test_an_envelope_corroborates_both_of_its_rows(self):
        # The M0 by having kept an isotopologue, the child by having an owner:
        # one piece of evidence, and it is evidence about the ion rather than
        # about either row on its own.
        rows = [_row("m0"), _row("child", role="iso_child", owner="m0")]

        assert corroboration_of(rows) == {
            "m0": CORROBORATED_ISOTOPOLOGUE,
            "child": CORROBORATED_ISOTOPOLOGUE,
        }

    def test_an_untargeted_row_alone_is_corroborated_by_nothing(self):
        assert corroboration_of([_row("a")]) == {"a": None}

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

        assert corroboration_of(rows) == {}


class TestTheRunsOwnCalibration:
    """The fit over the corroborated commits, and when it refuses one."""

    def test_it_measures_the_centre_the_corroborated_rows_sit_at(self):
        rows = _anchors(12, ppm=-1.2)

        calibration = fit_run_mass_accuracy(rows, corroboration_of(rows))

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
            anchors + wild, corroboration_of(anchors + wild)
        )
        without = fit_run_mass_accuracy(anchors, corroboration_of(anchors))

        assert with_wild.sigma_ppm == pytest.approx(without.sigma_ppm)
        assert with_wild.anchors == without.anchors == 12

    def test_too_few_corroborated_commits_measure_nothing(self):
        rows = _anchors(3)

        calibration = fit_run_mass_accuracy(rows, corroboration_of(rows))

        assert not calibration.measured
        assert calibration.z_of(5.0) is None


class TestTheGate:
    """What the distance costs a row that has nothing else behind it."""

    def test_it_records_the_distance_on_every_commit(self):
        # Twelve anchors at +-0.1 ppm fit a centre of 0 and a width of
        # 1.4826 x 0.1 = 0.148 ppm, so a row 1 ppm out is 6.7 of this run's own
        # sigma - which is the number a reader needs, and is not readable off
        # the 1 ppm alone.
        rows = _anchors(12) + [_row("far", ppm=1.0)]

        apply_mass_gate(rows)

        assert rows[-1]["provenance"]["mass_z"] == pytest.approx(6.74, rel=0.01)
        assert all("mass_z" in row["provenance"] for row in rows)

    def test_the_distance_is_measured_from_the_run_s_own_centre(self):
        # The reason the offset half of the fit is load-bearing. Every row of
        # this sample sits 1.2 ppm low, which is the calibration, not an error:
        # measured from zero the whole ledger is off calibration, and the gate
        # would demote a correct run entire.
        rows = _anchors(12, ppm=-1.2) + [_row("typical", ppm=-1.2)]

        summary = apply_mass_gate(rows)

        assert rows[-1]["provenance"]["mass_z"] == pytest.approx(0.0, abs=0.5)
        assert rows[-1]["tier"] == TIER_ASSIGNED
        assert summary["capped"] == 0

    def test_an_uncorroborated_outlier_is_capped_at_candidate(self):
        rows = _anchors(12, spread=0.1) + [_row("off", ppm=0.6)]

        summary = apply_mass_gate(rows)

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
        rows = _anchors(12, spread=0.1) + [_row("wild", ppm=2.0)]

        summary = apply_mass_gate(rows)

        assert rows[-1]["tier"] == TIER_BELOW_ASSIGNABILITY
        assert summary["below_assignability"] == 1

    def test_a_corroborated_outlier_is_recorded_and_kept(self):
        # A confirmed envelope is evidence the mass error does not overrule -
        # and on the gate sets a fifth of the corroborated rows sit beyond three
        # sigma, so a gate that demoted them would be demoting its own anchors.
        # The distance is still recorded: the row is an outlier by this run's
        # own reckoning and says so, it is just not demoted for it.
        rows = _anchors(12, spread=0.1) + [
            _row("m0", ppm=2.0),
            _row("child", ppm=2.0, role="iso_child", owner="m0"),
        ]

        summary = apply_mass_gate(rows)

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

        summary = apply_mass_gate(rows)

        assert rows[-1]["tier"] == TIER_CANDIDATE
        assert "capped" not in rows[-1]["provenance"]["mass_gate"]
        assert summary["capped"] == 0

    def test_a_run_that_measured_no_calibration_gates_nothing(self):
        # Standing down is recorded rather than left indistinguishable from a
        # run that found nothing to demote: a sample with three corroborated
        # commits has not earned the right to withdraw a tier.
        rows = _anchors(3) + [_row("far", ppm=9.0)]

        summary = apply_mass_gate(rows)

        assert summary["applied"] is False
        assert summary["capped"] == 0
        assert rows[-1]["tier"] == TIER_ASSIGNED
        assert "mass_z" not in rows[-1]["provenance"]
        assert rows[-1]["provenance"]["mass_gate"] == {"corroborated_by": None}

    def test_a_commit_with_no_mass_error_is_left_alone(self):
        rows = _anchors(12) + [_row("no_error", ppm=None)]

        apply_mass_gate(rows)

        assert "mass_z" not in rows[-1]["provenance"]
        assert rows[-1]["tier"] == TIER_ASSIGNED

    def test_the_run_records_what_it_fitted_and_what_it_cost(self):
        rows = _anchors(12, ppm=-0.5, spread=0.1) + [_row("off", ppm=1.5)]

        summary = apply_mass_gate(rows)

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

        summary = apply_mass_gate(rows)

        assert summary["applied"] is True
        assert summary["capped"] == 0
        assert all(
            row["tier"] == TIER_ASSIGNED for row in rows if row["assigned_formula"]
        )
