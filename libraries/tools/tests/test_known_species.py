"""Tests for deciding a known species once for its batch
(``mascope_tools.composition.known_species``, step 4.2h), and for the per-file
claim a decision licenses (``reagents.match_batch_species``)."""

import numpy as np
import pytest

from mascope_tools.composition.heuristic_filter import predict_isotopes
from mascope_tools.composition.known_species import (
    MIN_PRESENT_FILES,
    MIN_PRESENT_SHARE,
    MIN_TEST_FILES,
    REASON_FEW_FILES,
    REASON_ONE_FILE,
    REASON_TEST_NEVER_HELD,
    VERDICT_ABSENT,
    VERDICT_PRESENT,
    VERDICT_UNPOOLED,
    PooledEvidence,
    decide_known_species,
    thresholds,
)
from mascope_tools.composition.reagents import (
    DEFAULT_ISOTOPOLOGUE_MIN_RELATIVE,
    ReagentCalibration,
    match_batch_species,
    match_reagent_clusters,
    reagent_library,
)


class TestTheDecision:
    def test_a_species_present_in_most_files_and_claimed_in_one_is_present(self):
        """The reference's case: a per-file lock that fired in one file of ten."""
        verdict = decide_known_species(
            PooledEvidence(files=10, present=9, test_held=1, claimable=8)
        )

        assert verdict.verdict == VERDICT_PRESENT
        assert verdict.present
        assert verdict.reason is None

    def test_a_species_in_one_file_is_not_pooled(self):
        verdict = decide_known_species(PooledEvidence(files=10, present=1, test_held=1))

        assert verdict.verdict == VERDICT_UNPOOLED
        assert verdict.reason == REASON_ONE_FILE

    def test_a_species_in_one_file_of_one_is_not_pooled_either(self):
        """A batch of one file has no batch to pool: the share is whole, the
        count is not."""
        verdict = decide_known_species(PooledEvidence(files=1, present=1, test_held=1))

        assert verdict.verdict == VERDICT_UNPOOLED
        assert verdict.reason == REASON_ONE_FILE

    def test_a_species_in_under_half_the_files_is_not_pooled(self):
        verdict = decide_known_species(PooledEvidence(files=10, present=4, test_held=4))

        assert verdict.verdict == VERDICT_UNPOOLED
        assert verdict.reason == REASON_FEW_FILES

    def test_half_the_files_is_enough(self):
        verdict = decide_known_species(PooledEvidence(files=10, present=5, test_held=1))

        assert verdict.verdict == VERDICT_PRESENT

    def test_a_species_no_file_claimed_is_absent(self):
        """The mass alone, at the batch's tolerance, is not the species."""
        verdict = decide_known_species(
            PooledEvidence(files=10, present=10, test_held=0, claimable=10)
        )

        assert verdict.verdict == VERDICT_ABSENT
        assert verdict.reason == REASON_TEST_NEVER_HELD

    def test_the_thresholds_are_the_ones_named(self):
        assert thresholds() == {
            "min_present_files": MIN_PRESENT_FILES,
            "min_present_share": MIN_PRESENT_SHARE,
            "min_test_files": MIN_TEST_FILES,
        }

    def test_the_thresholds_can_be_read_at_other_values(self):
        evidence = PooledEvidence(files=10, present=5, test_held=1)

        assert decide_known_species(evidence, min_present_share=0.6).reason == (
            REASON_FEW_FILES
        )
        assert decide_known_species(evidence, min_present_files=6).reason == (
            REASON_ONE_FILE
        )
        assert decide_known_species(evidence, min_test_files=2).reason == (
            REASON_TEST_NEVER_HELD
        )

    def test_the_record_carries_the_evidence(self):
        verdict = decide_known_species(
            PooledEvidence(files=8, present=6, test_held=2, claimable=3, kept=1)
        )

        assert verdict.to_json() == {
            "verdict": VERDICT_PRESENT,
            "reason": None,
            "files": 8,
            "present": 6,
            "present_share": 0.75,
            "test_held": 2,
            "claimable": 3,
            "kept": 1,
        }

    def test_an_empty_partition_reads_no_share(self):
        assert PooledEvidence(files=0, present=0, test_held=0).present_share == 0.0


def _spectrum(*ions):
    """A spectrum holding each ``(formula, charge, height)`` with its envelope."""
    mz, intensity = [], []
    for formula, charge, height in ions:
        predicted_mz, predicted_intensity, _ = predict_isotopes(
            formula, charge, None, DEFAULT_ISOTOPOLOGUE_MIN_RELATIVE
        )
        base = max(predicted_intensity)
        for one_mz, one_intensity in zip(predicted_mz, predicted_intensity):
            mz.append(float(one_mz))
            intensity.append(height * float(one_intensity) / base)
    order = np.argsort(mz)
    return np.asarray(mz)[order], np.asarray(intensity)[order]


def _ion(label):
    return {cluster.label: cluster for cluster in reagent_library("BR")}[label]


class TestTheBatchClaim:
    """A file claims an ion its batch decided present below its own floor."""

    def test_an_ion_under_the_files_floor_is_claimed_on_the_batchs_word(self):
        # The bromide trimer at a millionth of the base peak: under the
        # library claim's floor of 1e-4, so the file's own claim leaves it.
        mz, intensity = _spectrum(("Br", -1, 1e9), ("Br3", -1, 1e3))
        trimer = _ion("[Br3]-")
        own, calibration = match_reagent_clusters(
            reagent_library("BR"), mz, intensity, claim_ppm=3.0
        )
        assert trimer.label not in {hit.cluster.label for hit in own}

        hits = match_batch_species(
            [trimer],
            mz,
            intensity,
            calibration=calibration,
            claim_ppm=3.0,
            taken=[hit.index for hit in own],
        )

        monoisotopic = [hit for hit in hits if not hit.is_isotopologue]
        assert [hit.cluster.label for hit in monoisotopic] == ["[Br3]-"]
        assert monoisotopic[0].mz == pytest.approx(trimer.mz, abs=1e-4)
        # Its lines go with it, as a library ion's do.
        assert {hit.isotope_label for hit in hits if hit.is_isotopologue}

    def test_a_peak_off_the_corrected_mass_is_not_taken(self):
        trimer = _ion("[Br3]-")
        mz = np.array([trimer.mz * (1 + 8e-6)])
        intensity = np.array([1e3])

        hits = match_batch_species(
            [trimer],
            mz,
            intensity,
            calibration=ReagentCalibration(0.0, 3.0),
            claim_ppm=3.0,
        )

        assert hits == []

    def test_the_mass_is_corrected_by_the_files_anchors(self):
        trimer = _ion("[Br3]-")
        mz = np.array([trimer.mz * (1 + 8e-6)])
        intensity = np.array([1e3])

        hits = match_batch_species(
            [trimer],
            mz,
            intensity,
            calibration=ReagentCalibration(8.0, 3.0),
            claim_ppm=3.0,
        )

        assert [hit.index for hit in hits] == [0]
        assert hits[0].mz_error_ppm == pytest.approx(8.0, abs=0.01)

    def test_a_peak_the_files_own_claim_took_is_left(self):
        trimer = _ion("[Br3]-")
        mz = np.array([trimer.mz])
        intensity = np.array([1e3])

        hits = match_batch_species(
            [trimer],
            mz,
            intensity,
            calibration=ReagentCalibration(0.0, 3.0),
            claim_ppm=3.0,
            taken=[0],
        )

        assert hits == []

    def test_nothing_to_claim_claims_nothing(self):
        assert (
            match_batch_species(
                [],
                np.array([100.0]),
                np.array([1.0]),
                calibration=ReagentCalibration(0.0, 3.0),
                claim_ppm=3.0,
            )
            == []
        )
        assert (
            match_batch_species(
                [_ion("[Br3]-")],
                np.array([]),
                np.array([]),
                calibration=ReagentCalibration(0.0, 3.0),
                claim_ppm=3.0,
            )
            == []
        )
