"""The finder ranks with the v2 fit, at the sample's own mass width and noise.

Four things decide a candidate here, and each is a property of the sample rather
than a constant of the search: how wide the mass term is (the fitted sigma), what
window a line may be matched in, how deep the envelope is predicted, and whether
an absent line is charged. The tests below pin each one against the behaviour it
replaced - v1 averaged its terms over the lines a candidate matched, so a
prediction nobody could find cost nothing, and every instrument was judged at
5 ppm.
"""

import numpy as np
import polars as pl
import pytest

from mascope_tools.composition.heuristic_filter import (
    ISOTOPE_ABUNDANCE_THRESHOLD,
    PATTERN_BASE_SNR,
    PATTERN_REQUIRED_LINES,
    envelope_floor_for_peak,
    match_isotopic_pattern,
    predict_isotopes,
    score_pattern,
)
from mascope_tools.composition.models import PatternScoring


SAMPLE_FLOOR = 1e-5  # an Orbitrap's match-params abundance floor


def _candidate(formula: str, ion: str, observed_mass: float, error_ppm: float) -> dict:
    return {
        "formula": formula,
        "ion": ion,
        "observed_mass": observed_mass,
        "composition_error_ppm": error_ppm,
    }


def _lines(ion: str, charge: int, floor: float = SAMPLE_FLOOR) -> dict[str, tuple]:
    mzs, intensities, labels = predict_isotopes(ion, charge, threshold=floor)
    base = float(intensities[list(labels).index("M0")])
    return {
        label: (float(mz), float(intensity) / base)
        for mz, intensity, label in zip(mzs, intensities, labels)
    }


class TestEnvelopeFloor:
    """How deep a peak's envelope is predicted, and what bounds it."""

    def test_a_bright_clean_peak_reaches_below_the_one_percent_default(self):
        scoring = PatternScoring(abundance_floor=SAMPLE_FLOOR)
        assert envelope_floor_for_peak(4.0e6, 10_000.0, 1.0, scoring) == pytest.approx(
            3e-4
        )

    def test_a_weak_peak_is_not_predicted_deeper_than_it_could_be_seen(self):
        # SNR 20: a line under 15% of it is at the noise, and the old fixed
        # cutoff is already deeper than that, so the cutoff stands.
        scoring = PatternScoring(abundance_floor=SAMPLE_FLOOR)
        assert (
            envelope_floor_for_peak(4.0e3, 20.0, 1.0, scoring)
            == ISOTOPE_ABUNDANCE_THRESHOLD
        )

    def test_without_snr_the_peak_list_still_bounds_it(self):
        # No noise estimate, but a line fainter than the faintest measured peak
        # is not in the spectrum whatever the noise would have said.
        scoring = PatternScoring(abundance_floor=SAMPLE_FLOOR)
        assert envelope_floor_for_peak(1.0e6, None, 50.0, scoring) == pytest.approx(
            5e-5
        )

    def test_a_zero_intensity_peak_does_not_open_the_envelope(self):
        # A peak frame carries zeros for peaks that averaged to nothing over the
        # window, and the smallest intensity it HOLDS is not its minimum.
        # Reading the minimum would say the spectrum can hold a line of any
        # depth, and predict every peak's envelope to the floor - four times the
        # matching work on a file that records no noise at all.
        scoring = PatternScoring(abundance_floor=SAMPLE_FLOOR, sigma_ppm=1.0)
        lines = _lines("C6H13O6", 1)
        candidates = [_candidate("C6H12O6", "C6H13O6+", lines["M0"][0], 0.5)]

        def envelope(intensities, mzs):
            _, data = match_isotopic_pattern(
                candidates,
                pl.DataFrame({"mz": mzs, "intensity": intensities}).sort("mz"),
                scoring,
            )
            return len(data[0]["predicted_masses"])

        mzs = [lines["M0"][0], lines["13C"][0]]
        assert envelope([1.0e6, 0.0], mzs) == envelope([1.0e6, 5.0e4], mzs)

    def test_the_default_scoring_predicts_exactly_as_deep_as_it_used_to(self):
        # A caller that says nothing about its sample gets the fixed 1% cutoff
        # on every peak, however bright and however clean.
        assert (
            envelope_floor_for_peak(4.0e6, 10_000.0, 1.0, PatternScoring())
            == ISOTOPE_ABUNDANCE_THRESHOLD
        )


class TestFaintLinesArePredicted:
    """The 1% cutoff dropped lines that are really there; the floor does not."""

    # The urea dimer's 18O line: 0.41% of the parent, and on the gate bright
    # enough to be the 19th peak of its sample - which the untargeted stage then
    # fitted an analyte to, because nothing had predicted it.
    ION = "C2H9N4O2"
    OXYGEN_18 = "18O"

    def _spectrum(self, snr: float) -> pl.DataFrame:
        lines = _lines(self.ION, 1)
        base = 4.0e6
        return pl.DataFrame(
            {
                "mz": [lines["M0"][0], lines["13C"][0], lines[self.OXYGEN_18][0]],
                "intensity": [
                    base,
                    base * lines["13C"][1],
                    base * lines[self.OXYGEN_18][1],
                ],
                "signal_to_noise": [snr, snr * lines["13C"][1], 1.0],
            }
        ).sort("mz")

    def _match(self, scoring: PatternScoring):
        lines = _lines(self.ION, 1)
        candidates = [
            _candidate("C2H8N4O2", f"{self.ION}+", lines["M0"][0], 0.1),
        ]
        return match_isotopic_pattern(candidates, self._spectrum(10_000.0), scoring)

    def test_the_faint_line_is_claimed_when_the_peak_can_hold_it(self):
        _, isotope_data = self._match(
            PatternScoring(abundance_floor=SAMPLE_FLOOR, sigma_ppm=0.5)
        )
        labels = list(isotope_data[0]["labels"])
        assert self.OXYGEN_18 in labels
        assert isotope_data[0]["masses"][labels.index(self.OXYGEN_18)] > 0

    def test_the_one_percent_cutoff_never_predicted_it(self):
        _, isotope_data = self._match(PatternScoring(sigma_ppm=0.5))
        assert self.OXYGEN_18 not in list(isotope_data[0]["labels"])


class TestDetectabilityIsCharged:
    """An absent line costs a candidate when the noise says it was visible."""

    # A sulfur-rich radical against the organic reading of one peak - the shape
    # of the elections the whole-spectrum context flipped. Its 34S line is 4.5%
    # of the parent: unmissable on a bright peak, invisible on a faint one.
    ION = "C10H19O8S"

    def _match(self, base_intensity: float, snr: float | None):
        lines = _lines(self.ION, 1)
        columns = {
            "mz": [lines["M0"][0]],
            "intensity": [base_intensity],
        }
        if snr is not None:
            columns["signal_to_noise"] = [snr]
        peaks = pl.DataFrame(columns).sort("mz")
        candidates = [_candidate("C10H18O8S", f"{self.ION}+", lines["M0"][0], 0.1)]
        ranked, _ = match_isotopic_pattern(
            candidates,
            peaks,
            PatternScoring(abundance_floor=SAMPLE_FLOOR, sigma_ppm=0.5),
        )
        return ranked[0]["isotopic_pattern_score"]

    def test_a_bright_peak_pays_for_the_lines_it_does_not_show(self):
        # SNR 4,000: the 13C line at 11% and the 34S at 4.5% should both be
        # there, and neither is.
        assert self._match(4.0e6, 4_000.0) < 0.9

    def test_a_faint_peak_is_not_charged_for_lines_below_its_noise(self):
        # SNR 8: nothing this ion predicts could have been measured, so their
        # absence says nothing about the reading.
        assert self._match(4.0e3, 8.0) > 0.95

    def test_v1_charged_neither(self):
        # The predecessor averaged mass and intensity over the MATCHED lines, so
        # a candidate that found only its own line scored as if the rest of the
        # envelope had never been predicted. That is what let a reading whose
        # lines cannot be there outrank one whose lines are.
        lines = _lines(self.ION, 1)
        predicted_rel = np.array([1.0, lines["13C"][1], lines["34S"][1]])
        observed = np.array([4.0e6, 0.0, 0.0])
        masses = np.array([lines["M0"][0], 0.0, 0.0])
        assert (
            score_pattern(
                masses, np.array([0.1, 0.0, 0.0]), observed, np.zeros(3), predicted_rel
            )
            > 0.9
        )


class TestTheSamplesOwnMassWidth:
    """The mass term is the instrument's, not a fixed 5 ppm."""

    # Two readings of one peak, 2.3 ppm apart in mass: the geometry of every
    # election the finder has to settle on an Orbitrap.
    ORGANIC = "C16H13NO5"
    SULFUR = "C10H19O8S"

    def _rank(self, sigma_ppm: float | None) -> list[tuple[str, float]]:
        sulfur = _lines(self.SULFUR, 1)
        observed_mz = sulfur["M0"][0]  # the sulfur reading is the one on mass
        # The peak stands alone, which is the population this matters for: with
        # no isotopologue to separate the two readings, the mass is all there is.
        peaks = pl.DataFrame(
            {
                "mz": [observed_mz],
                "intensity": [4.0e4],
                "signal_to_noise": [200.0],
            }
        ).sort("mz")
        candidates = [
            _candidate("C16H12NO5", f"{self.ORGANIC}+", observed_mz, 2.3),
            _candidate("C10H18O8S", f"{self.SULFUR}+", observed_mz, 0.1),
        ]
        ranked, _ = match_isotopic_pattern(
            candidates,
            peaks,
            PatternScoring(abundance_floor=SAMPLE_FLOOR, sigma_ppm=sigma_ppm),
        )
        return [(c["formula"], c["isotopic_pattern_score"]) for c in ranked]

    def test_at_the_instruments_own_width_the_reading_on_mass_wins_outright(self):
        ranked = self._rank(0.3)
        assert ranked[0][0] == "C10H18O8S"
        # Not a close thing: 2.3 ppm is eight sigma on this sample, and the
        # reading that far off its own prediction is not a hypothesis any more.
        assert ranked[1][1] < 0.01 * ranked[0][1]

    def test_at_a_tof_width_both_readings_stay_hypotheses(self):
        # The same 2.3 ppm on a spectrum measured to 8 ppm separates nothing,
        # and the fit says so instead of pretending otherwise.
        ranked = self._rank(8.0)
        assert ranked[1][1] > 0.5 * ranked[0][1]


class TestTheMatchWindowIsTheSamples:
    """A line is matched in the sample's own tolerance, not in a fixed 5 ppm."""

    ION = "C6H13O6"

    def _match(self, mz_tolerance_ppm: float, shift_ppm: float):
        lines = _lines(self.ION, 1)
        peaks = pl.DataFrame(
            {
                "mz": [
                    lines["M0"][0],
                    lines["13C"][0] * (1 + shift_ppm * 1e-6),
                ],
                "intensity": [1.0e5, 1.0e5 * lines["13C"][1]],
            }
        ).sort("mz")
        candidates = [_candidate("C6H12O6", f"{self.ION}+", lines["M0"][0], 0.5)]
        _, isotope_data = match_isotopic_pattern(
            candidates,
            peaks,
            PatternScoring(mz_tolerance_ppm=mz_tolerance_ppm, sigma_ppm=8.0),
        )
        return isotope_data[0]

    def test_a_tof_line_eight_ppm_off_is_matched_in_a_tof_window(self):
        assert self._match(15.0, 8.0)["masses"][1] > 0

    def test_and_is_not_matched_in_an_orbitraps(self):
        assert self._match(5.0, 8.0)["masses"][1] == 0.0


class TestTheFittedOffset:
    """A calibration offset is removed before the fit, not charged to the ion."""

    ION = "C6H13O6"

    def _score(self, offset_ppm: float, mu_ppm: float) -> float:
        lines = _lines(self.ION, 1)
        shift = 1 + offset_ppm * 1e-6
        peaks = pl.DataFrame(
            {
                "mz": [lines["M0"][0] * shift, lines["13C"][0] * shift],
                "intensity": [1.0e5, 1.0e5 * lines["13C"][1]],
                "signal_to_noise": [500.0, 500.0 * lines["13C"][1]],
            }
        ).sort("mz")
        candidates = [
            _candidate("C6H12O6", f"{self.ION}+", lines["M0"][0] * shift, offset_ppm)
        ]
        ranked, _ = match_isotopic_pattern(
            candidates,
            peaks,
            PatternScoring(abundance_floor=SAMPLE_FLOOR, sigma_ppm=1.0, mu_ppm=mu_ppm),
        )
        return ranked[0]["isotopic_pattern_score"]

    def test_a_whole_spectrum_three_ppm_high_scores_as_if_it_were_centred(self):
        assert self._score(3.0, 3.0) == pytest.approx(self._score(0.0, 0.0), abs=1e-6)

    def test_and_pays_for_it_when_nobody_measured_the_offset(self):
        assert self._score(3.0, 0.0) < 0.1 * self._score(0.0, 0.0)


class TestTheNoiseIsReported:
    """What the detectability gate judged the absent lines against."""

    def _ranked(self, with_snr: bool):
        lines = _lines("C6H13O6", 1)
        columns = {"mz": [lines["M0"][0]], "intensity": [1.0e5]}
        if with_snr:
            columns["signal_to_noise"] = [412.7]
        candidates = [_candidate("C6H12O6", "C6H13O6+", lines["M0"][0], 0.5)]
        ranked, _ = match_isotopic_pattern(
            candidates, pl.DataFrame(columns), PatternScoring(sigma_ppm=1.0)
        )
        return ranked[0]

    def test_the_base_peaks_signal_to_noise_rides_on_every_candidate(self):
        assert self._ranked(True)[PATTERN_BASE_SNR] == pytest.approx(412.7)

    def test_a_peak_list_without_it_claims_nothing(self):
        # A fit scored against abundance alone rather than against the noise,
        # and the row has to be able to say which it was.
        assert self._ranked(False)[PATTERN_BASE_SNR] is None


class TestTheRequiredLinesAreReportedSeparately:
    """The refusal the fit no longer makes on its own."""

    def test_a_reading_that_finds_its_own_line_is_evidence(self):
        lines = _lines("C6H13O6", 1)
        peaks = pl.DataFrame({"mz": [lines["M0"][0]], "intensity": [1.0e5]})
        candidates = [_candidate("C6H12O6", "C6H13O6+", lines["M0"][0], 0.5)]
        ranked, _ = match_isotopic_pattern(
            candidates, peaks, PatternScoring(sigma_ppm=1.0)
        )
        assert ranked[0][PATTERN_REQUIRED_LINES] is True
