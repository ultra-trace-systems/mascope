"""Stage A and the seeded re-score hold a line's height to the line model's test.

A line the gate turns away is absent to the fit score, and its peak goes to the
search. The classic match's fixed isotope-ratio tolerance (0.2 on an Orbitrap)
would turn away lines the finder admits, the test the search's lines are held
to: 40% either side of the share, opened downward by the run's fitted curve at
the line's expected S/N. On the demo's uronium samples a siloxane's merged
silicon lines read about 0.22 to 0.25 under their share, failed 0.2, and left
their peaks to odd-electron candidates (MAS-977). So the assignment gates its
lines by the line model's window (``engine.line_heights_within``, through
``seeded_scoring.gate_lines``) and keeps the match parameters' mass window and
intensity floor; the classic match keeps its tolerance.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from mascope_backend.api.new.match.params.lib import apply_match_params
from mascope_backend.api.new.peak_assignments import service
from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig
from mascope_backend.api.new.peak_assignments.engine import (
    STAGE_A_FIT_MIN_IONS,
    SampleMassAccuracy,
    line_heights_within,
    score_ions_by_fit,
)
from mascope_backend.api.new.peak_assignments.envelope_fit import RunEnvelopeFit
from mascope_backend.api.new.peak_assignments.seeded_scoring import (
    gate_and_score,
    gate_lines,
)
from mascope_match.params import OrbiMatchParams, TofMatchParams
from mascope_tools.composition.envelope_tolerance import (
    FORM_CURVE,
    EnvelopeLines,
    EnvelopeTolerance,
    fit_envelope_tolerance,
)
from mascope_tools.composition.mass_accuracy import mass_accuracy_anchors


_SVC = "mascope_backend.api.new.peak_assignments.service"

#: A curve that reads a line at expected S/N 3 to 50 down to 0.3 of its share,
#: and one above 50 at its share.
OPENED = EnvelopeTolerance(snr_edges=(3.0, 50.0), lower=(0.3, 1.0))
#: The main line's S/N: a line of share 0.2 is expected at 20, in the curve's
#: opened bin, where the main line itself would be in the closed one.
MAIN_SNR = 100.0


def _line(
    ion="ion-1",
    share=1.0,
    error=0.0,
    *,
    snr=np.nan,
    scans=np.nan,
    mz_error=0.5,
    intensity=None,
):
    """One matched line: its share of its ion's main line and its height's
    error against it, observed / predicted - 1."""
    error = float(error)
    return {
        "target_ion_id": ion,
        "relative_abundance": share,
        "match_abundance_error": error,
        "match_mz_error": mz_error,
        "sample_peak_intensity": (
            1e5 * share * (1.0 + error) if intensity is None else intensity
        ),
        "signal_to_noise": snr,
        "ratio_scan_count": scans,
        "match_score": max(0.0, 1.0 - min(1.0, abs(error)))
        * max(0.0, 1.0 - 1e-2 * abs(mz_error)),
    }


def _ion(*errors, ion="ion-1", share=0.2, main_snr=MAIN_SNR, scans=np.nan):
    """An ion's main line and one line of ``share`` per error."""
    return [_line(ion, 1.0, 0.0, snr=main_snr)] + [
        _line(ion, share, error, scans=scans) for error in errors
    ]


def _within(rows, tolerance=None):
    return line_heights_within(pd.DataFrame(rows), tolerance).tolist()


class TestTheHeightTest:
    """``line_heights_within``: the finder's window, read off a match frame."""

    def test_today_a_line_is_the_ions_within_40_percent_of_its_share(self):
        rows = _ion(-0.3, 0.3, -0.39, -0.5, 0.5)
        assert _within(rows) == [True, True, True, True, False, False]

    def test_a_line_without_an_error_is_not_within(self):
        assert _within(_ion(np.nan)) == [True, False]

    def test_the_runs_curve_opens_the_window_down_at_the_lines_expected_snr(self):
        rows = _ion(-0.6, -0.75, 0.5)
        assert _within(rows) == [True, False, False, False]
        # Expected at 100 x 0.2 = 20, where the curve reads a line down to 0.3
        # of its share; above the share the 40% cut stays.
        assert _within(rows, OPENED) == [True, True, False, False]

    def test_the_expected_snr_is_the_main_lines_times_the_share(self):
        # At ten times the main line's S/N the line is expected at 200, where
        # the curve reads it at its share and the window stays shut.
        assert _within(_ion(-0.6, main_snr=10 * MAIN_SNR), OPENED) == [True, False]

    def test_the_main_line_is_the_ions_most_abundant_wherever_it_sits(self):
        # The line listed first, with an S/N of its own that the curve would
        # not open at: read against the main line's, it is expected at 20.
        rows = [
            _line("ion-1", 0.2, -0.6, snr=1000.0),
            _line("ion-1", 1.0, 0.0, snr=MAIN_SNR),
        ]
        assert _within(rows, OPENED) == [True, True]

    def test_each_ion_reads_its_own_main_line(self):
        # The second ion's main line carries no S/N, so its line has no
        # expected S/N and keeps the fixed window, as the finder keeps it.
        rows = _ion(-0.6) + _ion(-0.6, ion="ion-2", main_snr=np.nan)
        assert _within(rows, OPENED) == [True, True, True, False]
        # Nor does a main line at an S/N of 0, a file's mark for none.
        assert _within(_ion(-0.6, main_snr=0.0), OPENED) == [True, False]

    def test_a_line_read_scan_by_scan_keeps_the_window_at_its_share(self):
        # Its share is read over the scans holding it and its main line, the
        # dilution the curve allows for taken out.
        assert _within(_ion(-0.6, scans=4), OPENED) == [True, False]
        assert _within(_ion(-0.3, scans=4), OPENED) == [True, True]

    def test_a_frame_with_a_duplicated_index_is_read_by_position(self):
        frame = pd.DataFrame(_ion(-0.3, -0.5), index=[0, 0, 1])
        assert line_heights_within(frame).tolist() == [True, True, False]

    def test_a_frame_without_snr_or_scan_counts_keeps_the_fixed_window(self):
        frame = pd.DataFrame(_ion(-0.3, -0.6)).drop(
            columns=["signal_to_noise", "ratio_scan_count"]
        )
        assert line_heights_within(frame, OPENED).tolist() == [True, True, False]

    def test_an_empty_frame_has_no_lines(self):
        assert line_heights_within(pd.DataFrame()).tolist() == []
        assert gate_lines(pd.DataFrame(), OrbiMatchParams()).empty


class TestTheGate:
    """``gate_lines``: the match parameters' gate with the line model's height
    test standing in for their ratio tolerance."""

    def test_a_line_the_ratio_tolerance_turns_away_is_kept(self):
        frame = pd.DataFrame(_ion(-0.3, 0.3, -0.5))

        classic = apply_match_params(frame, OrbiMatchParams())
        gated = gate_lines(frame, OrbiMatchParams())

        # The premise: 0.3 off its share is outside the Orbitrap's 0.2.
        assert (classic["match_score"].iloc[1:3] == 0).all()
        assert gated["match_score"].tolist() == pytest.approx(
            frame["match_score"].tolist()[:3] + [0.0]
        )
        assert (gated["sample_peak_intensity"] > 0).all()

    def test_the_run_s_tolerance_opens_the_gate(self):
        frame = pd.DataFrame(_ion(-0.6, -0.75))

        today = gate_lines(frame, OrbiMatchParams())
        opened = gate_lines(frame, OrbiMatchParams(), OPENED)

        assert today["match_score"].iloc[1] == 0
        assert opened["match_score"].iloc[1] > 0
        assert opened["match_score"].iloc[2] == 0

    def test_the_mass_window_and_the_intensity_floor_still_gate(self):
        frame = pd.DataFrame(
            [
                _line("ion-1", 1.0, 0.0, snr=MAIN_SNR),
                _line("ion-1", 0.2, -0.1, mz_error=8.0),
                _line("ion-1", 0.2, -0.1, intensity=5.0),
            ]
        )
        params = OrbiMatchParams(peak_min_intensity=10.0)

        gated = gate_lines(frame, params)

        assert gated["match_score"].tolist()[1:] == [0.0, 0.0]

    def test_the_classic_match_keeps_its_ratio_tolerance(self):
        gated = apply_match_params(pd.DataFrame(_ion(-0.3)), OrbiMatchParams())
        assert gated["match_score"].iloc[1] == 0

    def test_the_seeded_chain_gates_at_the_tolerance_it_scores_at(self):
        # A line the gate keeps carries its intensity into the fit; one it
        # turns away is absent to it (``engine.score_ions_by_fit``).
        frame = pd.DataFrame(_ion(-0.3, -0.6, share=0.2))

        def kept(tolerance):
            scored = gate_and_score(
                frame,
                OrbiMatchParams(),
                fallback_sigma_ppm=0.3,
                reagent_offset=None,
                envelope_tolerance=tolerance,
            )
            return (scored["sample_peak_intensity"] > 0).tolist()

        assert kept(None) == [True, True, False]
        assert kept(OPENED) == [True, True, True]


def _fitted():
    """A run's own curve: lines at expected S/N 20 read at 0.3 of their share."""
    return RunEnvelopeFit(
        fit_envelope_tolerance(
            FORM_CURVE, EnvelopeLines(np.full(30, 20.0), np.full(30, 0.3))
        )
    )


class TestStageA:
    """Stage A's two passes gate the matched frame through ``gate_lines``."""

    FRAME = pd.DataFrame(_ion(-0.3, -0.6, 0.6))

    async def _first_pass(self, inherited_envelope=None):
        scored = MagicMock(side_effect=lambda df, **_kwargs: df)
        patches = [
            patch(
                f"{_SVC}._fetch_known_isotopes",
                AsyncMock(return_value=self.FRAME.copy()),
            ),
            patch(f"{_SVC}.listed_formula_identities", return_value=frozenset()),
            patch(
                f"{_SVC}.compute_match_isotopes",
                AsyncMock(return_value=self.FRAME.copy()),
            ),
            patch(f"{_SVC}.score_ions_by_fit", scored),
            patch(f"{_SVC}.sample_mass_accuracy", return_value=SampleMassAccuracy()),
            patch(f"{_SVC}.get_instrument_type", return_value="orbi"),
            patch(f"{_SVC}.load_calibration", AsyncMock(return_value=None)),
            patch(f"{_SVC}.invert_matches_to_peak_assignments", return_value=[]),
        ]
        for started in patches:
            started.start()
        try:
            first = await service._stage_a_first_pass(
                SimpleNamespace(
                    sample_item_id="si-1", filename="orbi-sample.raw", polarity="-"
                ),
                PeakAssignmentConfig(),
                OrbiMatchParams(),
                ["m-1"],
                [],
                "run-1",
                fallback_sigma_ppm=0.6,
                known_window=None,
                inherited_envelope=inherited_envelope,
            )
        finally:
            for started in patches:
                started.stop()
        return first, scored.call_args.args[0]

    @staticmethod
    def _kept(frame):
        return (frame["match_score"] > 0).tolist()

    @pytest.mark.asyncio
    async def test_the_first_pass_holds_lines_to_the_finders_window(self):
        first, gated = await self._first_pass()

        assert self._kept(gated) == [True, True, False, False]
        # What a second pass gates again: the frame as matched.
        pd.testing.assert_frame_equal(first.matched, self.FRAME)

    @pytest.mark.asyncio
    async def test_a_decoys_first_pass_gates_at_its_controls_tolerance(self):
        _, gated = await self._first_pass(inherited_envelope=_fitted())

        assert self._kept(gated) == [True, True, True, False]

    @pytest.mark.asyncio
    async def test_the_second_pass_gates_the_matched_frame_at_the_fitted_curve(
        self,
    ):
        first = service._StageAFirstPass(
            rows=[],
            scored=self.FRAME,
            matched=self.FRAME,
            mass_accuracy=SampleMassAccuracy(),
            confidence_calibration=None,
            listed_formulas=frozenset(),
            assignments_of=MagicMock(return_value=[]),
        )
        scored = MagicMock(side_effect=lambda df, **_kwargs: df)
        with (
            patch(f"{_SVC}._stage_a_first_pass", AsyncMock(return_value=first)),
            patch(
                f"{_SVC}.fit_run_envelope_tolerance",
                AsyncMock(return_value=_fitted()),
            ),
            patch(f"{_SVC}.score_ions_by_fit", scored),
        ):
            await service._stage_a_assignments(
                SimpleNamespace(sample_item_id="si-1", filename="orbi-sample.raw"),
                PeakAssignmentConfig(),
                OrbiMatchParams(),
                ["m-1"],
                [],
                "run-1",
                fallback_sigma_ppm=0.6,
                known_window=None,
            )

        # 0.6 under its share is the fitted curve's; 0.6 over it no curve's.
        assert self._kept(scored.call_args.args[0]) == [True, True, True, False]


def _tof_frame():
    """A TOF file's frame whose fitted curve admits lines today's window shuts.

    Twelve ions, enough to fit a width on their main lines alone. Each has one
    line of share 0.2, read at 0.4 of it and without scan counts, as a TOF line
    is: 0.6 under its share, outside today's window and inside the fitted
    curve's at its expected S/N of 20. Each sits 6 ppm off its main line, so
    counted as anchors those lines move the fitted width and offset.
    """
    rows = []
    for index, error in enumerate(np.linspace(-1.5, 1.5, 12)):
        ion = f"ion-{index}"
        rows.append(_line(ion, 1.0, 0.0, snr=MAIN_SNR, mz_error=error))
        rows.append(_line(ion, 0.2, -0.6, mz_error=error + 6.0))
    return pd.DataFrame(rows)


class TestStageAMassAccuracy:
    """Stage A's second pass scores at the width and offset of its first."""

    @pytest.mark.asyncio
    async def test_the_second_pass_scores_at_the_first_pass_measurement(self):
        frame = _tof_frame()
        fitted = _fitted()
        arbitrated = MagicMock(return_value=[])
        with (
            patch(f"{_SVC}._fetch_known_isotopes", AsyncMock(return_value=frame)),
            patch(f"{_SVC}.listed_formula_identities", return_value=frozenset()),
            patch(
                f"{_SVC}.compute_match_isotopes",
                AsyncMock(return_value=frame.copy()),
            ),
            patch(f"{_SVC}.get_instrument_type", return_value="tof"),
            patch(f"{_SVC}.load_calibration", AsyncMock(return_value=None)),
            patch(f"{_SVC}.invert_matches_to_peak_assignments", arbitrated),
            patch(
                f"{_SVC}.fit_run_envelope_tolerance",
                AsyncMock(return_value=fitted),
            ),
        ):
            _, _, accuracy, _, _ = await service._stage_a_assignments(
                SimpleNamespace(
                    sample_item_id="si-1", filename="tof-sample.h5", polarity="-"
                ),
                PeakAssignmentConfig(),
                TofMatchParams(),
                ["m-1"],
                [],
                "run-1",
                fallback_sigma_ppm=3.0,
                known_window=None,
            )
        first, second = (call.args[0] for call in arbitrated.call_args_list)

        # The premise: the first pass fits on the main lines alone, and the
        # fitted curve lets the minor lines in as anchors too.
        assert len(mass_accuracy_anchors(first)) == 12
        assert len(mass_accuracy_anchors(second)) == 24
        assert accuracy.anchors == 12

        def scores(inherited):
            return score_ions_by_fit(
                gate_lines(frame, TofMatchParams(), fitted.tolerance),
                fallback_sigma_ppm=3.0,
                reagent_offset=None,
                min_anchor_ions=STAGE_A_FIT_MIN_IONS,
                envelope_tolerance=fitted.tolerance,
                inherited_accuracy=inherited,
            )["match_score"].to_numpy()

        # Refitted on those, the width and offset would move, and the scores.
        assert not np.allclose(scores(None), scores(accuracy))
        # The pass scores at the one the run records and Stage B is scored at.
        np.testing.assert_allclose(second["match_score"].to_numpy(), scores(accuracy))
