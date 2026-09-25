"""What a run measures of its own mass accuracy, and what it does about it.

The gate's arithmetic is small and its judgement is not: which rows are allowed
to define the run's centre decides which rows can be found to sit away from it,
and a mistake there condemns exactly the samples that most need the correction -
the ones sitting a ppm out. These pin the corroboration rule, the fit over it,
the direction the cap may move a tier, and the two ways a run declines to gate
at all, the centre that follows the mass range where a run's commits do, and
what an isotopologue's own line can deliver when it misses its parent.
"""

import math
from dataclasses import replace

import pandas as pd
import pytest

from mascope_backend.api.new.peak_assignments.envelope_claims import ENVELOPE_CLAIM
from mascope_backend.api.new.peak_assignments.mass_gate import (
    BELOW_ASSIGNABILITY_Z,
    CORROBORATED_CURATED,
    CORROBORATED_ISOTOPOLOGUE,
    OFF_CALIBRATION_Z,
    OVERLAP_PUSH_FWHM,
    OVERLAP_REACH_FWHM,
    PRECISION_SNR,
    REASON_ISOTOPOLOGUE_IN_DOUBT,
    REASON_ISOTOPOLOGUE_UNTRACKED,
    REASON_OFF_CALIBRATION,
    TRACKING_IN_DOUBT,
    TRACKING_TRACKS,
    TRACKING_UNTRACKED,
    UNREAD_LINE,
    LineQuality,
    MassCalibration,
    SpectrumLines,
    apply_mass_gate,
    corroboration_of,
    fit_run_mass_accuracy,
    isotopologue_tracking,
    line_tolerance_ppm,
    peak_mz,
    tracking_of,
    tracking_tolerance_ppm,
)
from mascope_backend.api.new.peak_assignments.tiers import (
    TIER_ASSIGNED,
    TIER_BELOW_ASSIGNABILITY,
    TIER_CANDIDATE,
    TIER_UNASSIGNED,
)
from mascope_tools.composition.mass_accuracy import (
    MASS_TREND_ABS_FLOOR_MDA,
    TREND_TOO_FEW_POINTS,
    MassTrend,
)


#: The instrument class's precision the gate is exercised at here: the
#: Orbitrap's, so an isotopologue tracks its parent within 0.3 ppm and the width
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
    compound=None,
    mz=None,
    provenance=None,
):
    return {
        "peak_assignment_id": row_id,
        "sample_peak_id": f"peak-{row_id}",
        "role": role,
        "source": source,
        "assigned_formula": formula,
        "mz_error_ppm": ppm,
        "sample_peak_mz": mz,
        "tier": tier,
        "owner_peak_assignment_id": owner,
        "target_compound_id": compound,
        "provenance": dict(provenance or {}),
    }


def _library(row_id, **fields):
    """A Stage A row the target library won, which keeps its compound id."""
    return _row(row_id, source="database", compound=f"compound-{row_id}", **fields)


def _mirror(row_id, **fields):
    """A Stage A row a reference mirror won, which persists with no compound id."""
    return _row(row_id, source="database", **fields)


def _anchors(count, *, ppm=0.0, spread=0.1, start=0):
    """`count` corroborated commits scattered evenly about `ppm`."""
    return [
        _library(f"anchor-{start + i}", ppm=ppm + (spread if i % 2 else -spread))
        for i in range(count)
    ]


class TestWhatCorroboratesACommit:
    """Which rows the run has more than a mass fit for."""

    def test_a_curated_row_is_corroborated_by_its_library(self):
        rows = [_library("a")]

        assert corroboration_of(rows, precision_ppm=PRECISION) == {
            "a": CORROBORATED_CURATED
        }

    def test_a_confirmed_envelope_is_answered_ahead_of_curation(self):
        # Both are true of a library row whose isotopologue tracks it, and the
        # envelope is the answer the cap reads.
        rows = [
            _library("m0", ppm=0.4),
            _library("child", ppm=0.5, role="iso_child", owner="m0"),
        ]

        assert corroboration_of(rows, precision_ppm=PRECISION) == {
            "m0": CORROBORATED_ISOTOPOLOGUE,
            "child": CORROBORATED_ISOTOPOLOGUE,
        }

    def test_a_library_isotopologue_that_does_not_track_is_only_curated(self):
        rows = [
            _library("m0", ppm=0.0),
            _library("child", ppm=2.5, role="iso_child", owner="m0"),
        ]

        assert corroboration_of(rows, precision_ppm=PRECISION) == {
            "m0": CORROBORATED_CURATED,
            "child": CORROBORATED_CURATED,
        }

    def test_a_reference_mirror_row_is_not_corroborated_by_its_list(self):
        # A mirror is a prior matched against every sample, not a library
        # somebody assembled for this data. On a TOF most of its pairings are
        # lines the match window reached by chance, so its curation says nothing
        # about whether this peak is that compound.
        rows = [_mirror("seed")]

        assert corroboration_of(rows, precision_ppm=PRECISION) == {"seed": None}

    def test_a_reference_mirror_row_an_isotopologue_tracks_is_corroborated(self):
        # The same second place in the spectrum that corroborates a search
        # result corroborates a mirror's reading.
        rows = [
            _mirror("seed", ppm=0.4),
            _mirror("seed-child", ppm=0.5, role="iso_child", owner="seed"),
        ]

        assert corroboration_of(rows, precision_ppm=PRECISION) == {
            "seed": CORROBORATED_ISOTOPOLOGUE,
            "seed-child": CORROBORATED_ISOTOPOLOGUE,
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

    def test_a_reference_mirror_row_does_not_anchor_the_calibration(self):
        # Counted as curated, a loaded seed's chance lines anchored this fit and
        # widened it: on the gate's three TOF sets from 3.0, 4.3 and 2.1 ppm to
        # 7.0, 7.3 and 6.7.
        anchors = _anchors(12)
        chance = [_mirror(f"seed-{i}", ppm=-6.0 + i * 12.0 / 11) for i in range(12)]

        with_chance = fit_run_mass_accuracy(
            anchors + chance,
            corroboration_of(anchors + chance, precision_ppm=PRECISION),
        )
        without = fit_run_mass_accuracy(
            anchors, corroboration_of(anchors, precision_ppm=PRECISION)
        )

        assert with_chance.sigma_ppm == pytest.approx(without.sigma_ppm)
        assert with_chance.anchors == without.anchors == 12

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
            "ceiling": TIER_CANDIDATE,
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

    def test_a_library_row_off_calibration_is_capped_like_a_mirrors(self):
        # The same error on the target library's row and on a mirror's. A list
        # names a compound, not where each of its lines has to sit, so neither
        # identity is evidence the mass error does not overrule. The library's
        # row still anchors the calibration it is judged against; the mirror's
        # does not.
        rows = _anchors(12, spread=0.1) + [
            _library("library", ppm=5.0),
            _mirror("seed", ppm=5.0),
        ]

        summary = apply_mass_gate(rows, fallback_sigma_ppm=PRECISION)

        library, seed = rows[-2], rows[-1]
        assert library["tier"] == TIER_BELOW_ASSIGNABILITY
        assert library["provenance"]["mass_gate"] == {
            "corroborated_by": CORROBORATED_CURATED,
            "ceiling": TIER_BELOW_ASSIGNABILITY,
            "capped": TIER_BELOW_ASSIGNABILITY,
            "reason": REASON_OFF_CALIBRATION,
        }
        assert seed["tier"] == TIER_BELOW_ASSIGNABILITY
        assert seed["provenance"]["mass_gate"] == {
            "corroborated_by": None,
            "ceiling": TIER_BELOW_ASSIGNABILITY,
            "capped": TIER_BELOW_ASSIGNABILITY,
            "reason": REASON_OFF_CALIBRATION,
        }
        assert summary["capped"] == 2
        assert summary["below_assignability"] == 2
        assert summary["capped_curated"] == 1
        assert summary["anchors"] == 13

    def test_a_library_row_an_isotopologue_tracks_is_kept(self):
        rows = _anchors(12, spread=0.1) + [
            _library("m0", ppm=5.0),
            _library("child", ppm=5.0, role="iso_child", owner="m0"),
        ]

        summary = apply_mass_gate(rows, fallback_sigma_ppm=PRECISION)

        assert [row["tier"] for row in rows[-2:]] == [TIER_ASSIGNED, TIER_ASSIGNED]
        assert rows[-2]["provenance"]["mass_gate"] == {
            "corroborated_by": CORROBORATED_ISOTOPOLOGUE
        }
        assert summary["capped"] == 0

    def test_a_library_isotopologue_off_its_own_line_is_capped(self):
        # What the curation's exemption held at the top tier on the gate: an
        # isotopologue line three widths off while its monoisotopic row sits on
        # calibration - a weak line, or one half of a partly resolved pair.
        rows = _anchors(12, spread=0.1) + [
            _library("m0", ppm=0.0),
            _library("child", ppm=2.5, role="iso_child", owner="m0"),
        ]

        summary = apply_mass_gate(rows, fallback_sigma_ppm=PRECISION)

        m0, child = rows[-2], rows[-1]
        assert m0["tier"] == TIER_ASSIGNED
        assert child["tier"] == TIER_CANDIDATE
        assert child["provenance"]["mass_gate"] == {
            "corroborated_by": CORROBORATED_CURATED,
            "tracking": TRACKING_UNTRACKED,
            "ceiling": TIER_CANDIDATE,
            "capped": TIER_CANDIDATE,
            "reason": REASON_OFF_CALIBRATION,
        }
        assert (summary["capped"], summary["capped_curated"]) == (1, 1)

    def test_it_only_ever_demotes(self):
        # A row the bands already put below the cap keeps the tier they gave it,
        # and the gate says nothing about it: it did not decide that tier and
        # must not appear to have.
        rows = _anchors(12, spread=0.1) + [_row("low", ppm=0.6, tier=TIER_CANDIDATE)]

        summary = apply_mass_gate(rows, fallback_sigma_ppm=PRECISION)

        assert rows[-1]["tier"] == TIER_CANDIDATE
        assert "capped" not in rows[-1]["provenance"]["mass_gate"]
        assert summary["capped"] == 0

    def test_a_row_already_at_the_cap_records_the_ceiling_it_would_hold(self):
        # A pass that lifts a tier later (the partner gate) reads it: the
        # row's own line is what the gate judged, whichever reading it ends
        # up carrying.
        rows = _anchors(12, spread=0.1) + [_row("held", ppm=2.5, tier=TIER_CANDIDATE)]

        summary = apply_mass_gate(rows, fallback_sigma_ppm=PRECISION)

        assert rows[-1]["tier"] == TIER_CANDIDATE
        assert rows[-1]["provenance"]["mass_gate"] == {
            "corroborated_by": None,
            "ceiling": TIER_CANDIDATE,
            "reason": REASON_OFF_CALIBRATION,
        }
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
    """What the gate may act on in a ledger that holds only Stage A.

    ``_fold_sample_peaks_without_run`` runs Stage A and the two pre-passes and
    then the gate, over its Stage A rows (``test_fold_without_run`` pins the
    call). Every commit on that path is a Stage A one, and these pin which of
    them the gate may lower: any that sits off calibration with no isotopologue
    behind it, the target library's as well as a reference mirror's. Without the
    gate on that path, such a row would hold a tier there that a run takes from
    it.
    """

    def test_the_target_library_s_rows_off_calibration_are_capped(self):
        rows = [
            _library("a", ppm=9.0),
            _library("b", ppm=-9.0),
            *_anchors(10, start=10),
            _row("reagent", role="reagent", source="reagent", formula=None, ppm=8.0),
        ]

        summary = apply_mass_gate(rows, fallback_sigma_ppm=PRECISION)

        assert summary["applied"] is True
        assert (summary["capped"], summary["capped_curated"]) == (2, 2)
        assert [rows[0]["tier"], rows[1]["tier"]] == [TIER_BELOW_ASSIGNABILITY] * 2
        assert all(row["tier"] == TIER_ASSIGNED for row in rows[2:-1])
        assert rows[-1]["tier"] == TIER_ASSIGNED

    def test_a_reference_mirror_s_row_off_calibration_is_capped(self):
        rows = [
            _mirror("seed", ppm=2.5),
            *_anchors(10, start=10),
            _row("reagent", role="reagent", source="reagent", formula=None, ppm=8.0),
        ]

        summary = apply_mass_gate(rows, fallback_sigma_ppm=PRECISION)

        assert summary["applied"] is True
        assert rows[0]["tier"] == TIER_CANDIDATE
        assert summary["capped"] == 1


class TestWhenAnIsotopologueIsEvidence:
    """A child corroborates its parent only if it measures the same axis.

    The rule these pin was written against a measurement: on the three TOF
    sets the child-minus-parent mass error is 4.6 to 6.5 ppm wide with 37-46%
    of children inside 3 ppm of their parent, against 0.28-0.45 ppm and 98-99%
    on the Orbitrap sets. An isotopologue is paired inside the class's matching
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

    def test_an_isotopologue_does_not_anchor_the_fit(self):
        # It is the same ion on a weaker peak, so it is the wider row wherever
        # it is real - and where it is not real it is the coincidence above.
        # Measured: letting isotopologues anchor put the bromide TOF set at 4.4-4.6
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


#: The line the commits of a run follow below: an absolute offset of -0.15 mDa,
#: which is -2.1 ppm at m/z 61 and near zero at m/z 400.
TREND_INTERCEPT_PPM, TREND_OFFSET_MDA = 0.35, -0.15


def _centre(mz):
    return TREND_INTERCEPT_PPM + TREND_OFFSET_MDA * 1000.0 / mz


def _trend_run(probe_ppm, probe_mz=61.0, *, located=True):
    """A run whose commits follow the line over m/z 59-400, then a probe.

    Twelve target library anchors at m/z 250-400, forty uncorroborated commits
    spread evenly in 1000/mz over the whole range, and last an uncorroborated
    probe. ``located=False`` drops every row's m/z, which leaves the run
    nothing to fit a line over.
    """

    def at(mz):
        return mz if located else None

    anchors = [
        _library(f"anchor-{i}", ppm=_centre(mz) + (0.05 if i % 2 else -0.05), mz=at(mz))
        for i, mz in enumerate(250.0 + 150.0 * i / 11 for i in range(12))
    ]
    commits = [
        _row(f"commit-{i}", ppm=_centre(mz) + (0.05 if i % 2 else -0.05), mz=at(mz))
        for i, mz in enumerate(
            1000.0 / (2.5 + (1000.0 / 59.0 - 2.5) * i / 39) for i in range(40)
        )
    ]
    return anchors + commits + [_row("probe", ppm=probe_ppm, mz=at(probe_mz))]


def _calibration_with_a_trend(mz_lo=59.0):
    """A run centred at -0.12 ppm and judged at 0.583, whose commits drew the line."""
    return MassCalibration(
        mu_ppm=-0.12,
        sigma_ppm=0.08,
        anchors=12,
        gate_sigma_ppm=0.583,
        trend=MassTrend(
            intercept_ppm=TREND_INTERCEPT_PPM,
            offset_mda=TREND_OFFSET_MDA,
            sigma_ppm=0.05,
            points=53,
            mz_lo=mz_lo,
            mz_hi=400.0,
        ),
        trend_rows=53,
    )


class TestTheCentreFollowsTheMassRange:
    """Where a run's commits drift with m/z, a row is judged at its own."""

    def test_a_small_ion_on_the_run_s_line_is_on_calibration(self):
        # -2.1 ppm at m/z 61 is where this run's commits sit there. From the
        # constant centre, which the anchors put near -0.1 ppm, it is more than
        # three widths out, capped for the calibration's shape.
        rows = _trend_run(probe_ppm=_centre(61.0))

        summary = apply_mass_gate(rows, fallback_sigma_ppm=PRECISION)

        assert summary["centre"] == "trend"
        assert rows[-1]["provenance"]["mass_z"] == pytest.approx(0.0, abs=0.3)
        assert rows[-1]["tier"] == TIER_ASSIGNED

        constant = _trend_run(probe_ppm=_centre(61.0), located=False)
        summary = apply_mass_gate(constant, fallback_sigma_ppm=PRECISION)

        assert summary["centre"] == "constant"
        assert constant[-1]["provenance"]["mass_z"] < -OFF_CALIBRATION_Z
        assert constant[-1]["tier"] == TIER_CANDIDATE

    def test_a_small_ion_off_the_run_s_line_is_capped(self):
        # On the constant centre, and 2.1 ppm off the line the run's own commits
        # draw at m/z 61: the shape is no excuse for a row that is not on it.
        rows = _trend_run(probe_ppm=0.0)

        summary = apply_mass_gate(rows, fallback_sigma_ppm=PRECISION)

        assert rows[-1]["provenance"]["mass_z"] > OFF_CALIBRATION_Z
        assert rows[-1]["tier"] == TIER_CANDIDATE
        assert summary["capped"] == 1

        constant = _trend_run(probe_ppm=0.0, located=False)
        apply_mass_gate(constant, fallback_sigma_ppm=PRECISION)

        assert constant[-1]["tier"] == TIER_ASSIGNED

    def test_the_line_is_the_commits_and_the_centre_and_width_the_anchors(self):
        # The anchors thin out below m/z 100, where a small ion's isotopologue
        # is too weak to track, so the line is fitted over every commit. The
        # constant centre and the width stay the anchors' alone: the rows being
        # judged do not set the width they are judged in.
        rows = _trend_run(probe_ppm=_centre(61.0))
        anchors = [row for row in rows if row["source"] == "database"]

        calibration = fit_run_mass_accuracy(
            rows, corroboration_of(rows, precision_ppm=PRECISION)
        )
        alone = fit_run_mass_accuracy(
            anchors, corroboration_of(anchors, precision_ppm=PRECISION)
        )

        assert alone.trend is None
        assert alone.trend_refused == TREND_TOO_FEW_POINTS
        assert calibration.trend.offset_mda == pytest.approx(TREND_OFFSET_MDA, abs=0.01)
        assert calibration.trend_rows == len(rows)
        assert (calibration.mu_ppm, calibration.sigma_ppm, calibration.anchors) == (
            alone.mu_ppm,
            alone.sigma_ppm,
            alone.anchors,
        )

    def test_an_isotopologue_does_not_draw_the_line(self):
        # The same ion on a weaker peak, and on a crowded spectrum often a line
        # the matching window reached: it is judged at the line, not fitted.
        rows = _trend_run(probe_ppm=_centre(61.0))
        children = [
            _row(f"child-{i}", ppm=3.0, role="iso_child", mz=59.0 + i)
            for i in range(40)
        ]

        with_children = fit_run_mass_accuracy(
            rows + children,
            corroboration_of(rows + children, precision_ppm=PRECISION),
        )
        without = fit_run_mass_accuracy(
            rows, corroboration_of(rows, precision_ppm=PRECISION)
        )

        assert with_children.trend == without.trend
        assert with_children.trend_rows == without.trend_rows

    def test_a_run_whose_commits_are_flat_keeps_the_constant_centre_exactly(self):
        # Every row's distance and tier are what the constant centre gives a
        # run none of whose rows has an m/z at all. The off row sits at m/z 40,
        # where the line's 0.03 mDa floor is 0.75 ppm against the 0.583 ppm
        # this run judges at: a floor that reached a run without a line would
        # put it inside three widths and leave it uncapped.
        def flat(located):
            rows = _anchors(12, ppm=-0.1, spread=0.05) + [
                _row(
                    f"commit-{i}",
                    ppm=-0.1 + (0.05 if i % 2 else -0.05),
                    mz=(1000.0 / (2.5 + 14.5 * i / 39)) if located else None,
                )
                for i in range(40)
            ]
            return rows + [_row("off", ppm=2.0, mz=40.0 if located else None)]

        rows, unlocated = flat(True), flat(False)
        summary = apply_mass_gate(rows, fallback_sigma_ppm=PRECISION)
        apply_mass_gate(unlocated, fallback_sigma_ppm=PRECISION)

        assert summary["centre"] == "constant"
        assert summary["trend"] is None
        assert summary["trend_refused"] is not None
        assert [row["provenance"] for row in rows] == [
            row["provenance"] for row in unlocated
        ]
        assert [row["tier"] for row in rows] == [row["tier"] for row in unlocated]

    def test_below_the_lowest_commit_the_centre_is_held_where_it_was_measured(self):
        # Extended to m/z 70, the line fitted over m/z 90-400 would put its
        # centre 0.5 ppm further out than anything the run measured. Held, m/z
        # 70 is judged from the centre at m/z 90, and m/z 900 from m/z 400's.
        calibration = _calibration_with_a_trend(mz_lo=90.0)

        assert calibration.z_of(_centre(90.0), 70.0) == pytest.approx(0.0)
        assert calibration.z_of(_centre(400.0), 900.0) == pytest.approx(0.0)

    def test_at_low_mass_the_width_is_floored_in_millidaltons(self):
        # 0.03 mDa is 0.75 ppm at m/z 40, wider than the 0.583 ppm the gate
        # judges at; at m/z 200 it is 0.15 ppm, and the gate's width stands.
        calibration = _calibration_with_a_trend()

        assert calibration.z_of(_centre(59.0) + 1.5, 40.0) == pytest.approx(
            1.5 / (MASS_TREND_ABS_FLOOR_MDA * 1000.0 / 40.0)
        )
        assert calibration.z_of(_centre(200.0) + 1.5, 200.0) == pytest.approx(
            1.5 / 0.583
        )

    def test_a_run_without_a_line_puts_no_floor_under_its_width(self):
        # The floor belongs to the line. At m/z 40 it is 0.75 ppm, wider than
        # the 0.583 ppm this run judges at, and a run without a line judges a
        # row there in its own width, as it does everywhere.
        calibration = replace(_calibration_with_a_trend(), trend=None)

        assert calibration.z_of(1.5 - 0.12, 40.0) == pytest.approx(1.5 / 0.583)

    def test_a_row_whose_mz_is_unknown_is_judged_at_the_constant_centre(self):
        calibration = _calibration_with_a_trend()

        assert calibration.z_of(0.46) == pytest.approx((0.46 + 0.12) / 0.583)

    @pytest.mark.parametrize(
        ("value", "usable"),
        [
            (61.0, 61.0),
            (None, None),
            (float("nan"), None),
            (float("inf"), None),
            (0.0, None),
            (-61.0, None),
        ],
    )
    def test_only_a_usable_mz_places_a_row_on_the_mass_range(self, value, usable):
        # A row without one neither draws the line nor is read at it: there is
        # no position on the mass range to take the centre or the floor at.
        assert peak_mz({"sample_peak_mz": value}) == usable

    def test_the_run_records_the_centre_it_judged_at(self):
        summary = apply_mass_gate(
            _trend_run(probe_ppm=_centre(61.0)), fallback_sigma_ppm=PRECISION
        )

        assert summary["trend_refused"] is None
        trend = summary["trend"]
        assert trend["offset_mda"] == pytest.approx(TREND_OFFSET_MDA, abs=0.01)
        assert trend["intercept_ppm"] == pytest.approx(TREND_INTERCEPT_PPM, abs=0.05)
        assert trend["rows"] == 53
        assert 0 < trend["kept"] <= 53
        assert (trend["mz_lo"], trend["mz_hi"]) == pytest.approx((59.0, 400.0), abs=0.5)
        assert trend["abs_floor_mda"] == MASS_TREND_ABS_FLOOR_MDA

        unlocated = apply_mass_gate(
            _trend_run(probe_ppm=0.0, located=False), fallback_sigma_ppm=PRECISION
        )

        assert unlocated["centre"] == "constant"
        assert unlocated["trend"] is None
        assert unlocated["trend_refused"] == TREND_TOO_FEW_POINTS

        unmeasured = apply_mass_gate(_anchors(3), fallback_sigma_ppm=PRECISION)

        assert unmeasured["centre"] == "none"
        assert unmeasured["trend"] is None
        assert unmeasured["trend_refused"] is None


def _flat_resolution(mz):
    """A resolving power of 100,000 at every m/z: every line is 10 ppm wide."""
    return 100_000.0


def _spectrum(peaks, resolution=_flat_resolution):
    """`peaks` as (peak id, m/z, height, signal-to-noise)."""
    ids, mzs, heights, snrs = zip(*peaks)
    return SpectrumLines(list(ids), mzs, heights, snrs, resolution)


def _isotopologue_pair(
    child_ppm,
    *,
    parent_ppm=0.0,
    child_snr=100.0,
    neighbour=False,
    tier=TIER_ASSIGNED,
    provenance=None,
):
    """A calibrated run with one M0 at m/z 200 and its line at m/z 201.

    The M0 is bright and alone. The line is a tenth of its height at
    ``child_snr``, and with ``neighbour`` a peak as tall sits 15 ppm - 1.5 of
    its widths - beside it, which pushes it by a quarter width, 2.5 ppm.
    """
    rows = _anchors(12, spread=0.1) + [
        _row("m0", ppm=parent_ppm, mz=200.0),
        _row(
            "child",
            ppm=child_ppm,
            role="iso_child",
            owner="m0",
            mz=201.0,
            tier=tier,
            provenance=provenance,
        ),
    ]
    peaks = [
        ("peak-m0", 200.0, 10_000.0, 500.0),
        ("peak-child", 201.0, 1_000.0, child_snr),
    ]
    if neighbour:
        peaks.append(("peak-near", 201.0 * (1 + 15e-6), 1_000.0, 100.0))
    return rows, _spectrum(peaks)


class TestWhatALineCanDeliver:
    """The bar an isotopologue's miss is judged against, from its own line."""

    def test_two_lines_the_class_describes_share_the_class_s_bar(self):
        assert line_tolerance_ppm(PRECISION) == pytest.approx(
            tracking_tolerance_ppm(PRECISION)
        )
        at_the_class = LineQuality(snr=PRECISION_SNR)
        brighter = LineQuality(snr=10 * PRECISION_SNR)
        assert line_tolerance_ppm(PRECISION, at_the_class, brighter) == pytest.approx(
            0.9
        )

    def test_a_fainter_line_is_placed_worse_by_the_root_of_how_much(self):
        # On the gate's Orbitrap sets the confirmed isotopologues' miss narrows
        # as 1 / sqrt(SNR), which is the class's precision at SNR 15. A line five
        # times fainter than that carries five times the variance, and the bar
        # takes the two lines' variances together.
        faint = LineQuality(snr=PRECISION_SNR / 5)

        assert faint.noise_ratio == pytest.approx(5.0)
        assert line_tolerance_ppm(PRECISION, faint) == pytest.approx(
            0.9 * math.sqrt(3.0)
        )
        assert line_tolerance_ppm(PRECISION, faint, faint) == pytest.approx(
            0.9 * math.sqrt(5.0)
        )

    @pytest.mark.parametrize("snr", [None, float("nan"), 0.0, -3.0])
    def test_a_noise_nobody_measured_is_the_class_s(self, snr):
        assert LineQuality(snr=snr).noise_ratio == 1.0

    def test_a_push_is_a_shift_and_adds_to_the_bar(self):
        pushed, nudged = LineQuality(push_ppm=2.5), LineQuality(push_ppm=0.5)

        assert line_tolerance_ppm(PRECISION, pushed, nudged) == pytest.approx(3.9)


class TestReadingTheSpectrum:
    """How close and how tall a line's neighbours are, in its own width."""

    def test_a_peak_as_tall_within_reach_pushes_a_quarter_width(self):
        lines = _spectrum(
            [("a", 200.0, 100.0, 50.0), ("b", 200.0 * (1 + 15e-6), 100.0, 50.0)]
        )

        assert lines.fwhm_ppm(200.0) == pytest.approx(10.0)
        assert lines.of("a").push_ppm == pytest.approx(OVERLAP_PUSH_FWHM * 10.0)
        assert lines.of("b").push_ppm == pytest.approx(OVERLAP_PUSH_FWHM * 10.0)

    def test_a_shorter_neighbour_pushes_in_proportion_and_a_taller_one_fully(self):
        lines = _spectrum(
            [("tall", 200.0, 100.0, 50.0), ("short", 200.0 * (1 + 15e-6), 40.0, 50.0)]
        )

        assert lines.of("tall").push_ppm == pytest.approx(0.4 * 2.5)
        assert lines.of("short").push_ppm == pytest.approx(2.5)

    def test_the_tallest_neighbour_in_reach_is_the_one_that_pushes(self):
        lines = _spectrum(
            [
                ("line", 200.0, 100.0, 50.0),
                ("small", 200.0 * (1 - 12e-6), 20.0, 50.0),
                ("half", 200.0 * (1 + 18e-6), 50.0, 50.0),
            ]
        )

        assert lines.of("line").push_ppm == pytest.approx(0.5 * 2.5)

    def test_a_peak_out_of_reach_pushes_nothing(self):
        # Two widths is where a neighbour stops counting: the partly resolved
        # pair it was drawn from measured 1.6 to 1.7 of theirs apart.
        apart = OVERLAP_REACH_FWHM * 10.0 + 1.0
        lines = _spectrum(
            [("a", 200.0, 100.0, 50.0), ("b", 200.0 * (1 + apart * 1e-6), 100.0, 50.0)]
        )

        assert lines.of("a").push_ppm == 0.0

    def test_a_line_alone_is_pushed_by_nothing(self):
        assert _spectrum([("a", 200.0, 100.0, 50.0)]).of("a").push_ppm == 0.0

    def test_without_a_resolution_no_line_is_read_as_pushed(self):
        lines = _spectrum(
            [("a", 200.0, 100.0, 5.0), ("b", 200.0 * (1 + 5e-6), 100.0, 5.0)],
            resolution=None,
        )

        assert lines.fwhm_ppm(200.0) is None
        assert lines.of("a") == LineQuality(snr=5.0, push_ppm=0.0)

    @pytest.mark.parametrize(
        "resolution",
        [
            lambda mz: 0.0,
            lambda mz: -1.0,
            lambda mz: float("nan"),
            lambda mz: 1.0 / 0.0,
        ],
    )
    def test_a_resolution_that_cannot_answer_pushes_nothing(self, resolution):
        lines = _spectrum(
            [("a", 200.0, 100.0, 5.0), ("b", 200.0 * (1 + 5e-6), 100.0, 5.0)],
            resolution=resolution,
        )

        assert lines.of("a").push_ppm == 0.0

    def test_a_peak_the_spectrum_does_not_hold_is_one_the_class_describes(self):
        lines = _spectrum([("a", 200.0, 100.0, 5.0)])

        assert lines.of("elsewhere") == UNREAD_LINE
        assert lines.of(None) == UNREAD_LINE

    def test_a_noise_the_file_does_not_record_is_not_invented(self):
        lines = _spectrum([("a", 200.0, 100.0, float("nan")), ("b", 300.0, 1.0, 7.0)])

        assert lines.of("a").snr is None
        assert lines.of("b").snr == pytest.approx(7.0)

    def test_it_reads_the_frame_the_service_loads(self):
        frame = pd.DataFrame(
            {
                "sample_peak_id": ["a", "b"],
                "mz": [200.0 * (1 + 15e-6), 200.0],
                "intensity": [100.0, 100.0],
                "signal_to_noise": [3.0, 30.0],
            }
        )

        lines = SpectrumLines.from_peaks(frame, _flat_resolution)
        silent = SpectrumLines.from_peaks(frame.drop(columns="signal_to_noise"))

        assert lines.of("a").snr == pytest.approx(3.0)
        assert lines.of("a").push_ppm == pytest.approx(2.5)
        assert lines.of("b").snr == pytest.approx(30.0)
        assert silent.of("a") == LineQuality(snr=None, push_ppm=0.0)
        assert lines.snapshot() == {
            "noise": True,
            "resolution": True,
            "precision_snr": PRECISION_SNR,
            "overlap_reach_fwhm": OVERLAP_REACH_FWHM,
            "overlap_push_fwhm": OVERLAP_PUSH_FWHM,
        }
        assert (silent.snapshot()["noise"], silent.snapshot()["resolution"]) == (
            False,
            False,
        )


class TestHowAnIsotopologueFollowsItsParent:
    """Tracks, in doubt, or not at all."""

    def test_inside_the_class_s_precision_it_tracks(self):
        assert tracking_of(0.5, -0.3, precision_ppm=PRECISION) == TRACKING_TRACKS

    def test_a_faint_line_that_misses_by_its_own_noise_is_in_doubt(self):
        faint = LineQuality(snr=3.0)

        assert (
            tracking_of(1.3, 0.0, precision_ppm=PRECISION, child=faint)
            == TRACKING_IN_DOUBT
        )
        assert (
            tracking_of(1.8, 0.0, precision_ppm=PRECISION, child=faint)
            == TRACKING_UNTRACKED
        )

    def test_a_bright_line_that_misses_as_far_does_not_track(self):
        bright = LineQuality(snr=300.0)

        assert (
            tracking_of(1.3, 0.0, precision_ppm=PRECISION, child=bright)
            == TRACKING_UNTRACKED
        )

    def test_a_line_pushed_by_its_neighbour_is_in_doubt(self):
        # Set C's strongest line: its 13C2 and 18O lines, about as tall as each
        # other and 1.3 widths apart, each sit 1.2 to 2.1 ppm off their places
        # while the ion itself is on calibration.
        pushed = LineQuality(snr=35.0, push_ppm=2.2)

        assert (
            tracking_of(-1.9, 0.3, precision_ppm=PRECISION, child=pushed)
            == TRACKING_IN_DOUBT
        )
        assert (
            tracking_of(-1.9, 0.3, precision_ppm=PRECISION, child=UNREAD_LINE)
            == TRACKING_UNTRACKED
        )

    @pytest.mark.parametrize(
        ("child", "parent"), [(None, 0.0), (0.0, None), (float("nan"), 0.0)]
    )
    def test_a_missing_error_does_not_track(self, child, parent):
        assert tracking_of(child, parent, precision_ppm=PRECISION) == TRACKING_UNTRACKED

    def test_every_committed_isotopologue_with_an_owner_is_read(self):
        rows, lines = _isotopologue_pair(1.3, child_snr=3.0)
        rows += [
            _row("orphan", ppm=9.0, role="iso_child"),
            _row("lost", ppm=0.1, role="iso_child", owner="nobody"),
        ]

        assert isotopologue_tracking(rows, precision_ppm=PRECISION, lines=lines) == {
            "child": TRACKING_IN_DOUBT,
            "lost": TRACKING_UNTRACKED,
        }
        # Without the spectrum every line is one the class describes, and a
        # miss the faint line's noise explained is a miss.
        assert isotopologue_tracking(rows, precision_ppm=PRECISION) == {
            "child": TRACKING_UNTRACKED,
            "lost": TRACKING_UNTRACKED,
        }


class TestTheGateOnAnIsotopologue:
    """What a line that misses its parent is held at, and why."""

    def test_a_line_in_doubt_is_held_at_candidate_and_corroborates_nothing(self):
        rows, lines = _isotopologue_pair(1.3, child_snr=3.0)

        summary = apply_mass_gate(rows, fallback_sigma_ppm=PRECISION, lines=lines)

        m0, child = rows[-2], rows[-1]
        assert child["tier"] == TIER_CANDIDATE
        assert child["provenance"]["mass_gate"] == {
            "corroborated_by": None,
            "tracking": TRACKING_IN_DOUBT,
            "ceiling": TIER_CANDIDATE,
            "capped": TIER_CANDIDATE,
            "reason": REASON_ISOTOPOLOGUE_IN_DOUBT,
        }
        # A line in doubt is not the second place a tracking line is: the M0
        # rests on its own fit and does not anchor the calibration.
        assert m0["provenance"]["mass_gate"] == {"corroborated_by": None}
        assert m0["tier"] == TIER_ASSIGNED
        assert summary["anchors"] == 12
        assert (summary["capped"], summary["capped_in_doubt"]) == (0, 1)

    def test_a_line_in_doubt_is_not_taken_lower_for_its_distance(self):
        # Four ppm off a parent on calibration, which is seven widths from the
        # centre: a distance its neighbour's push explains, as it explains the
        # miss, so the line is held at candidate rather than dropped below.
        rows, lines = _isotopologue_pair(4.0, child_snr=3.0, neighbour=True)

        summary = apply_mass_gate(rows, fallback_sigma_ppm=PRECISION, lines=lines)

        child = rows[-1]
        assert child["provenance"]["mass_z"] > BELOW_ASSIGNABILITY_Z
        assert child["tier"] == TIER_CANDIDATE
        assert child["provenance"]["mass_gate"]["reason"] == (
            REASON_ISOTOPOLOGUE_IN_DOUBT
        )
        assert (summary["capped"], summary["below_assignability"]) == (0, 0)

    def test_the_same_miss_without_the_neighbour_is_a_coincidence(self):
        rows, lines = _isotopologue_pair(4.0, child_snr=3.0)

        summary = apply_mass_gate(rows, fallback_sigma_ppm=PRECISION, lines=lines)

        child = rows[-1]
        assert child["tier"] == TIER_BELOW_ASSIGNABILITY
        assert child["provenance"]["mass_gate"] == {
            "corroborated_by": None,
            "tracking": TRACKING_UNTRACKED,
            "ceiling": TIER_BELOW_ASSIGNABILITY,
            "capped": TIER_BELOW_ASSIGNABILITY,
            "reason": REASON_OFF_CALIBRATION,
        }
        assert (summary["capped"], summary["below_assignability"]) == (1, 1)

    def test_a_line_that_does_not_track_is_not_assigned_even_on_calibration(self):
        # 1.5 ppm is 2.6 widths from the centre, inside the distance cap, and
        # 1.5 ppm from a parent a bright line should sit within 0.9 of.
        rows, lines = _isotopologue_pair(1.5)

        summary = apply_mass_gate(rows, fallback_sigma_ppm=PRECISION, lines=lines)

        child = rows[-1]
        assert abs(child["provenance"]["mass_z"]) < OFF_CALIBRATION_Z
        assert child["tier"] == TIER_CANDIDATE
        assert child["provenance"]["mass_gate"] == {
            "corroborated_by": None,
            "tracking": TRACKING_UNTRACKED,
            "ceiling": TIER_CANDIDATE,
            "capped": TIER_CANDIDATE,
            "reason": REASON_ISOTOPOLOGUE_UNTRACKED,
        }
        assert (summary["capped"], summary["capped_untracked"]) == (0, 1)

    def test_a_line_that_tracks_keeps_its_tier_and_corroborates_its_parent(self):
        rows, lines = _isotopologue_pair(0.4, child_snr=3.0, neighbour=True)

        summary = apply_mass_gate(rows, fallback_sigma_ppm=PRECISION, lines=lines)

        m0, child = rows[-2], rows[-1]
        assert child["tier"] == TIER_ASSIGNED
        assert child["provenance"]["mass_gate"] == {
            "corroborated_by": CORROBORATED_ISOTOPOLOGUE,
            "tracking": TRACKING_TRACKS,
        }
        assert m0["provenance"]["mass_gate"]["corroborated_by"] == (
            CORROBORATED_ISOTOPOLOGUE
        )
        assert summary["anchors"] == 13

    def test_a_tier_already_below_the_cap_is_left_alone(self):
        # The gate lowers nothing and counts nothing; it records the ceiling
        # it would have held the row to, for a pass that lifts tiers later.
        rows, lines = _isotopologue_pair(1.5, tier=TIER_CANDIDATE)

        summary = apply_mass_gate(rows, fallback_sigma_ppm=PRECISION, lines=lines)

        assert rows[-1]["tier"] == TIER_CANDIDATE
        assert rows[-1]["provenance"]["mass_gate"] == {
            "corroborated_by": None,
            "tracking": TRACKING_UNTRACKED,
            "ceiling": TIER_CANDIDATE,
            "reason": REASON_ISOTOPOLOGUE_UNTRACKED,
        }
        assert summary["capped_untracked"] == 0

    def test_the_isotopologue_rule_does_not_wait_for_a_calibration(self):
        # It compares two of the run's own lines, which needs no centre.
        rows = [
            _row("m0", ppm=0.0, mz=200.0),
            _row("child", ppm=1.5, role="iso_child", owner="m0", mz=201.0),
        ]

        summary = apply_mass_gate(rows, fallback_sigma_ppm=PRECISION)

        assert summary["applied"] is False
        assert rows[0]["tier"] == TIER_ASSIGNED
        assert rows[1]["tier"] == TIER_CANDIDATE
        assert rows[1]["provenance"]["mass_gate"]["reason"] == (
            REASON_ISOTOPOLOGUE_UNTRACKED
        )
        assert "mass_z" not in rows[1]["provenance"]

    def test_a_claimed_line_corroborates_nothing_and_its_claim_holds_it(self):
        # A line a claim read as the ion's was committed as something else
        # first. It tracks here, so its distance is its parent's: the parent,
        # which nothing else corroborates, is taken below assignability for it
        # and the claimed line stays at the candidate its claim put it at.
        claim = {ENVELOPE_CLAIM: {"line": "13C", "tracking": TRACKING_TRACKS}}
        rows, lines = _isotopologue_pair(
            8.0, parent_ppm=8.0, tier=TIER_CANDIDATE, provenance=claim
        )

        summary = apply_mass_gate(rows, fallback_sigma_ppm=PRECISION, lines=lines)

        m0, child = rows[-2], rows[-1]
        assert corroboration_of(rows, precision_ppm=PRECISION)["m0"] is None
        assert m0["tier"] == TIER_BELOW_ASSIGNABILITY
        assert child["tier"] == TIER_CANDIDATE
        assert child["provenance"]["mass_gate"] == {
            "corroborated_by": None,
            "tracking": TRACKING_TRACKS,
        }
        assert summary["anchors"] == 12

    def test_the_run_records_how_its_isotopologues_follow(self):
        rows = _anchors(12, spread=0.1) + [
            _row("m0", ppm=0.0, mz=200.0),
            _row("tracks", ppm=0.2, role="iso_child", owner="m0", mz=201.0),
            _row("doubt", ppm=-1.3, role="iso_child", owner="m0", mz=202.0),
            _row("off", ppm=1.5, role="iso_child", owner="m0", mz=203.0),
        ]
        lines = _spectrum(
            [
                ("peak-m0", 200.0, 10_000.0, 500.0),
                ("peak-tracks", 201.0, 1_000.0, 100.0),
                ("peak-doubt", 202.0, 100.0, 3.0),
                ("peak-off", 203.0, 100.0, 100.0),
            ]
        )

        summary = apply_mass_gate(rows, fallback_sigma_ppm=PRECISION, lines=lines)

        assert summary["isotopologues"] == {
            TRACKING_TRACKS: 1,
            TRACKING_IN_DOUBT: 1,
            TRACKING_UNTRACKED: 1,
        }
        assert (summary["capped_in_doubt"], summary["capped_untracked"]) == (1, 1)
        assert summary["lines"] == lines.snapshot()
        # Without a spectrum the run says it read nothing.
        bare = apply_mass_gate(_anchors(12), fallback_sigma_ppm=PRECISION)
        assert (bare["lines"]["noise"], bare["lines"]["resolution"]) == (False, False)
        assert bare["isotopologues"] == {
            TRACKING_TRACKS: 0,
            TRACKING_IN_DOUBT: 0,
            TRACKING_UNTRACKED: 0,
        }
