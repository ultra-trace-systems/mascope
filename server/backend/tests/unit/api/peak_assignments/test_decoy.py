"""Decoy runs: the engine re-run with its chemistry made wrong on purpose.

What a decoy arm still assigns bounds what the engine assigns falsely, and it
only does if three things hold, each pinned here: the arm is exactly what was
asked (one arm, a shift every read sees, a swap after the chemistry resolves);
every stage scores at the control's calibration and envelope tolerance rather
than measuring the shift back out of the spectrum; and a decoy that cannot bound its control is
refused rather than run. What keeps a decoy out of the sample's result is the
integration suite's (``test_decoy_runs.py``).
"""

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError
from test_engine import TestScoreIonsByFit  # same directory, as test_orchestrator
from test_mass_gate import PRECISION, _anchors, _row
from test_orchestrator import (
    _PIPE,
    TestTheLaddersAreClimbedThroughTheOpenedChannels,
    TestTheListsAreReadThroughTheOpenedChannels,
    _patches,
    _peaks_df,
    _Recorder,
    _run,
    _stage_a_rows,
    _stand_in,
    _start,
)

from mascope_backend.api.new.peak_assignments import sample_mechanisms
from mascope_backend.api.new.peak_assignments.config import (
    PeakAssignmentConfig,
    PeakAssignmentDecoy,
)
from mascope_backend.api.new.peak_assignments.decoy import (
    DECOY_ARM_KEY,
    REFUSED_NO_CONTROL,
    REFUSED_NO_SCORING,
    DecoyPlan,
    DecoyRefused,
    declared_channels,
    plan_decoy,
    swap_channels,
    wrong_adducts,
)
from mascope_backend.api.new.peak_assignments.engine import (
    ENVELOPE_TOLERANCE_KEY,
    MEASUREMENT_MODEL_KEY,
    measurement_model_record,
    pattern_scoring_for,
    score_ions_by_fit,
)
from mascope_backend.api.new.peak_assignments.envelope_fit import inherited_envelope_fit
from mascope_backend.api.new.peak_assignments.mass_gate import (
    apply_mass_gate,
    measure_mass_gate,
)
from mascope_backend.api.new.peak_assignments.profiles import (
    RESOLVED_PROFILE_KEY,
    NamedContext,
    resolve_profile,
)
from mascope_backend.api.new.peak_assignments.sample_inputs import load_sample_peaks
from mascope_backend.api.new.peak_assignments.tiers import (
    TIER_ASSIGNED,
    TIER_BELOW_ASSIGNABILITY,
)
from mascope_match import shift_mz, shifted_mz_axis
from mascope_match.compute.axis import MzShift, current_mz_shift
from mascope_tools.composition.envelope_tolerance import (
    DEFAULT_TOLERANCE,
    FORM_CURVE,
    EnvelopeLines,
    EnvelopeTolerance,
    fit_envelope_tolerance,
)
from mascope_tools.composition.mass_accuracy import (
    MassTrend,
    ReagentOffset,
    SampleMassAccuracy,
    scoring_sigma_ppm,
)
from mascope_tools.composition.measurement_model import MeasurementModel


SHIFT = PeakAssignmentDecoy(mz_shift_ppm=9.0)
DALTON = PeakAssignmentDecoy(mz_shift_da=0.35)
SWAP = PeakAssignmentDecoy(swap_adducts=True)


@pytest.fixture(autouse=True)
def _stop_all_patches():
    """The orchestrator's edges are patched with ``start``; none outlives its test."""
    yield
    patch.stopall()


class TestTheDecoyBlock:
    """One arm per run, and a name for it the row and the tool read."""

    @pytest.mark.parametrize(
        "fields",
        [
            {},
            {"mz_shift_ppm": 9.0, "swap_adducts": True},
            {"mz_shift_ppm": 9.0, "mz_shift_da": 0.35},
            {"mz_shift_ppm": 0.0},
            {"mz_shift_da": 0.0},
            {"mz_shift_ppm": 101.0},
            {"mz_shift_da": 0.5},
            {"mz_shift_da": -0.5},
        ],
    )
    def test_a_decoy_names_one_arm_and_a_shift_that_moves_the_axis(self, fields):
        with pytest.raises(ValidationError):
            PeakAssignmentDecoy(**fields)

    def test_each_arm_is_named_as_the_row_records_it(self):
        assert SHIFT.arm == "mz_shift_ppm=+9"
        assert PeakAssignmentDecoy(mz_shift_ppm=-9.0).arm == "mz_shift_ppm=-9"
        assert PeakAssignmentDecoy(mz_shift_da=0.35).arm == "mz_shift_da=+0.35"
        assert SWAP.arm == "swap_adducts"

    def test_a_run_is_no_decoy_unless_it_asks(self):
        config = PeakAssignmentConfig()

        assert config.decoy is None
        assert "decoy" not in config.settings()
        assert PeakAssignmentConfig(decoy=SHIFT).settings() == config.settings()


class TestTheShiftedAxis:
    """Every read of a block sees the axis moved, and nothing outside it does."""

    def test_outside_a_block_the_axis_is_read_as_stored(self):
        stored = [100.0, 200.0]

        assert shift_mz(stored) is stored
        assert current_mz_shift() is None

    def test_a_ppm_shift_scales_and_a_dalton_shift_offsets(self):
        with shifted_mz_axis(ppm=9.0):
            assert list(shift_mz([100.0, 200.0])) == pytest.approx([100.0009, 200.0018])
        with shifted_mz_axis(da=0.35):
            assert list(shift_mz([100.0, 200.0])) == pytest.approx([100.35, 200.35])
        assert current_mz_shift() is None

    def test_a_block_with_no_shift_reads_the_axis_as_stored(self):
        with shifted_mz_axis(ppm=None, da=None):
            assert current_mz_shift() is None

    def test_the_run_s_peak_table_is_read_on_the_shifted_axis(self):
        peak_data = SimpleNamespace(
            peak_ids=[1, 2],
            mz_values=[100.0, 300.0],
            heights=[10.0, 20.0],
            areas=None,
            signal_to_noise=None,
            n_scans=None,
            n_scans_total=None,
        )
        sample = SimpleNamespace(
            filename="orbi-sample.raw", polarity="+", t0=None, t1=None
        )
        with (
            _stand_in("extract_peaks", return_value=peak_data),
            _stand_in("get_instrument_type", return_value="orbi"),
        ):
            stored = load_sample_peaks(sample)
            with shifted_mz_axis(ppm=-9.0):
                shifted = load_sample_peaks(sample)

        assert stored["mz"].tolist() == [100.0, 300.0]
        assert shifted["mz"].tolist() == pytest.approx([99.9991, 299.9973])
        assert shifted["sample_peak_id"].tolist() == ["1", "2"]


MATCH_PARAMS = SimpleNamespace(mz_tolerance=5.0, isotope_abundance_threshold=0.01)


def _round_trip(model: MeasurementModel) -> MeasurementModel:
    """A model, recorded as a control records it and read back as a decoy
    reads it."""
    return MeasurementModel.from_record(
        measurement_model_record(model, scoring={"mz_tolerance_ppm": 5.0}, gate={})
    )


#: A control that ended at v2: a centre that follows m/z, floored at v1.
CONTROL_V2 = MeasurementModel.v1(
    SampleMassAccuracy(mu_ppm=-0.3, sigma_ppm=0.25, anchors=40, anchor_ions=12), 0.3
).v2(
    mu_ppm=-0.2,
    sigma_ppm=0.12,
    commits=134,
    source_lines=6,
    source_capped=1,
    trend=MassTrend(
        intercept_ppm=0.1,
        offset_mda=-0.12,
        sigma_ppm=0.2,
        points=120,
        mz_lo=60.0,
        mz_hi=480.0,
    ),
    trend_rows=130,
)


class TestTheControlsMeasurementIsInherited:
    """A decoy scores and caps at the model its control recorded, unchanged."""

    @pytest.mark.parametrize(
        "accuracy",
        [
            # The library fitted both numbers.
            SampleMassAccuracy(
                mu_ppm=-0.41, sigma_ppm=0.22, anchors=40, anchor_ions=12
            ),
            # Too few ions: the class's width, the reagent lines' offset.
            SampleMassAccuracy(
                anchors=3,
                anchor_ions=2,
                reagent=ReagentOffset(mu_ppm=-1.26, lines=7, beyond_width=True),
            ),
            # Nothing measured, and the lines inside the width.
            SampleMassAccuracy(reagent=ReagentOffset(mu_ppm=0.2, lines=5)),
            SampleMassAccuracy(),
        ],
    )
    def test_every_number_a_stage_reads_is_the_control_s(self, accuracy):
        model = MeasurementModel.v1(accuracy, 0.3)
        inherited = _round_trip(model)

        original = pattern_scoring_for(MATCH_PARAMS, model)
        again = pattern_scoring_for(MATCH_PARAMS, inherited)
        assert again.sigma_ppm == pytest.approx(original.sigma_ppm, abs=1e-4)
        assert again.mu_ppm == pytest.approx(original.mu_ppm, abs=1e-4)
        assert inherited.mu_source == model.mu_source
        assert inherited.measured == model.measured
        assert inherited.reagent == model.reagent
        if model.sigma_ppm is None:
            assert inherited.sigma_ppm is None
        else:
            assert inherited.sigma_ppm == pytest.approx(model.sigma_ppm, abs=1e-3)
        assert dict(inherited.anchors) == dict(model.anchors)

    def test_a_v2_control_is_read_back_with_its_trend(self):
        inherited = _round_trip(CONTROL_V2)

        assert inherited.version == 2
        for error, mz in ((-1.2, 61.0), (0.3, 250.0), (2.0, 600.0), (1.0, None)):
            assert inherited.z_of(error, mz) == pytest.approx(
                CONTROL_V2.z_of(error, mz), abs=1e-3
            )
        assert inherited.record() == CONTROL_V2.record()

    def test_stage_a_scores_a_shifted_frame_at_the_inherited_centre(self):
        # Ten library ions 9 ppm out, as Stage A meets them on a shifted axis.
        # Fitted, the frame is centred on the shift and every ion reads as on
        # the axis; at the control's centre they read 9 ppm off it.
        rows = [
            TestScoreIonsByFit._iso(
                f"lib{i}", 1.0, 9.0 + (0.1 if i % 2 else -0.1), 1000.0, 0.9, snr=50.0
            )
            for i in range(10)
        ]
        control = MeasurementModel.v1(
            SampleMassAccuracy(mu_ppm=0.0, sigma_ppm=0.2, anchors=40), 0.3
        )

        fitted = score_ions_by_fit(pd.DataFrame(rows), fallback_sigma_ppm=0.3)
        inherited = score_ions_by_fit(pd.DataFrame(rows), model=control)

        assert fitted["match_score"].min() > 0.5
        assert inherited["match_score"].max() < fitted["match_score"].min()

    def test_the_mass_gate_judges_a_shifted_ledger_at_the_control_s_centre(self):
        # Every commit 9 ppm out. Refitted over its own anchors the gate
        # centres on the shift and caps nothing; at the control's model the
        # uncorroborated commit is far below assignability.
        def ledger():
            return _anchors(12, ppm=9.0) + [_row("off", ppm=9.0)]

        stand_in = MeasurementModel.v1(SampleMassAccuracy(), PRECISION)
        own = ledger()
        apply_mass_gate(own, model=stand_in, fallback_sigma_ppm=PRECISION)
        decoy = ledger()
        control = stand_in.v2(mu_ppm=0.0, sigma_ppm=0.15, commits=12, source_lines=0)
        summary, judged_at = apply_mass_gate(
            decoy,
            model=control,
            fallback_sigma_ppm=PRECISION,
            refit=False,
        )

        assert own[-1]["tier"] == TIER_ASSIGNED
        assert decoy[-1]["tier"] == TIER_BELOW_ASSIGNABILITY
        assert summary["inherited"] is True
        assert judged_at is control
        assert (
            "inherited"
            not in apply_mass_gate(ledger(), model=stand_in, fallback_sigma_ppm=0.3)[0]
        )

    def test_a_control_whose_model_measured_nothing_caps_nothing(self):
        inherited = _round_trip(MeasurementModel.v1(SampleMassAccuracy(anchors=3), 0.3))

        assert not inherited.measured
        assert inherited.z_of(9.0, 200.0) is None


def _curve_record(ratio: float = 0.8) -> dict:
    """The envelope tolerance an Orbitrap control fitted on 25 13C lines."""
    record = fit_envelope_tolerance(
        FORM_CURVE, EnvelopeLines(np.full(25, 15.0), np.full(25, ratio))
    ).record()
    record["acquisition"] = None
    return record


def _control(
    *,
    precision=3.0,
    tolerance=5.0,
    profile="UR",
    scoring=True,
    envelope=None,
    **settings,
) -> dict:
    """A control's stored config, as a completed in-app run records it."""
    config = PeakAssignmentConfig(**settings).model_dump()
    if envelope is not None:
        config[ENVELOPE_TOLERANCE_KEY] = envelope
    config[RESOLVED_PROFILE_KEY] = {"profile": profile, "mz_precision_ppm": precision}
    if scoring:
        v1 = MeasurementModel.v1(
            SampleMassAccuracy(mu_ppm=-0.2, sigma_ppm=0.2, anchors=30), 0.3
        )
        config[MEASUREMENT_MODEL_KEY] = measurement_model_record(
            v1.v2(mu_ppm=-0.2, sigma_ppm=0.1, commits=200, source_lines=0),
            ranked_at=v1,
            scoring={"mz_tolerance_ppm": float(tolerance)},
            gate={"cap_z": 3.0},
        )
    return config


class TestPlanningADecoy:
    """A decoy runs only where it can bound the run it is a decoy of."""

    def test_a_decoy_inherits_its_control_s_calibration(self):
        control = _control()

        plan = plan_decoy(SHIFT, PeakAssignmentConfig(decoy=SHIFT), "run-c", control)

        assert plan.control_run_id == "run-c"
        # The model the control scored and capped at: its v2, whose width is
        # floored at the v1 its search ranked at.
        assert plan.model.version == 2
        assert plan.model.scoring_mu_ppm == pytest.approx(-0.2)
        assert plan.model.width_ppm == pytest.approx(
            scoring_sigma_ppm(0.2, 0.3), abs=1e-4
        )
        # The wider of the search's 3 ppm and the 5 ppm lines are matched in.
        assert plan.window_ppm == 5.0
        assert plan.record() == {
            "arm": "mz_shift_ppm=+9",
            "control_run_id": "run-c",
            "calibration": "control",
            "model_version": 2,
            "source_ions": "stored_axis",
            "window_ppm": 5.0,
            "swapped_channels": None,
        }

    def test_a_decoy_inherits_its_control_s_envelope_tolerance(self):
        record = _curve_record()

        plan = plan_decoy(
            SHIFT,
            PeakAssignmentConfig(decoy=SHIFT),
            "run-c",
            _control(envelope=record),
        )

        assert plan.envelope.tolerance == EnvelopeTolerance.from_record(record)
        assert plan.envelope.tolerance != DEFAULT_TOLERANCE
        assert plan.envelope.record() == {**record, "inherited": True}

    def test_a_control_from_before_the_envelope_fit_gives_today_s_numbers(self):
        plan = plan_decoy(SHIFT, PeakAssignmentConfig(decoy=SHIFT), "run-c", _control())

        assert plan.envelope.tolerance == DEFAULT_TOLERANCE
        assert plan.envelope.record()["inherited"] is True

    def test_a_decoy_resolves_under_what_its_control_s_batch_named(self):
        # MAS-918: what the batch and dataset named when the control ran is
        # the decoy's, whatever they name now.
        control = _control()
        control[RESOLVED_PROFILE_KEY]["named_context"] = {
            "batch": "chamber",
            "dataset": "ambient-air",
        }

        plan = plan_decoy(SHIFT, PeakAssignmentConfig(decoy=SHIFT), "run-c", control)

        assert plan.named_context == NamedContext(
            batch="chamber", dataset="ambient-air"
        )

    def test_a_control_from_before_the_named_context_names_none(self):
        # Such a control resolved `auto` to its profile's default, as a decoy
        # naming nothing does.
        plan = plan_decoy(SHIFT, PeakAssignmentConfig(decoy=SHIFT), "run-c", _control())

        assert plan.named_context == NamedContext()

    def test_a_sample_with_no_run_of_its_own_has_nothing_to_bound(self):
        with pytest.raises(DecoyRefused, match=re.escape(REFUSED_NO_CONTROL)):
            plan_decoy(SHIFT, PeakAssignmentConfig(decoy=SHIFT), None, None)

    def test_a_decoy_at_other_settings_bounds_another_run(self):
        with pytest.raises(DecoyRefused, match="assigned_threshold, profile differ"):
            plan_decoy(
                SHIFT,
                PeakAssignmentConfig(decoy=SHIFT, profile="BR", assigned_threshold=0.8),
                "run-c",
                _control(),
            )

    def test_a_control_s_settings_that_no_longer_validate_are_refused(self):
        control = _control()
        control["profile"] = "a profile the library dropped"

        with pytest.raises(DecoyRefused, match="no longer validate"):
            plan_decoy(SHIFT, PeakAssignmentConfig(decoy=SHIFT), "run-c", control)

    def test_a_control_with_no_scoring_has_no_calibration_to_inherit(self):
        with pytest.raises(DecoyRefused, match=re.escape(REFUSED_NO_SCORING)):
            plan_decoy(
                SHIFT,
                PeakAssignmentConfig(decoy=SHIFT),
                "run-c",
                _control(scoring=False),
            )

    def test_a_control_from_before_the_one_record_is_refused(self):
        # Such a control scored at one model and capped at another; a decoy
        # scores and caps at one, so it has none to inherit.
        control = _control(scoring=False)
        control["pattern_scoring"] = {
            "sigma_ppm": 0.54,
            "mu_ppm": -0.2,
            "sigma_source": "fitted",
            "mu_source": "fitted",
            "mz_tolerance_ppm": 5.0,
        }
        control["mass_calibration"] = {"mu_ppm": -0.2, "sigma_ppm": 0.1}

        with pytest.raises(DecoyRefused, match=re.escape(REFUSED_NO_SCORING)):
            plan_decoy(SHIFT, PeakAssignmentConfig(decoy=SHIFT), "run-c", control)

    def test_a_ppm_shift_inside_the_control_s_window_is_no_decoy(self):
        # A TOF searches 10 ppm and matches lines at 15: 9 ppm leaves the true
        # formula in reach of both, which is why the TOF arm is 0.35 Da.
        tof = _control(precision=10.0, tolerance=15.0)

        with pytest.raises(DecoyRefused, match="inside the 15 ppm window"):
            plan_decoy(SHIFT, PeakAssignmentConfig(decoy=SHIFT), "run-c", tof)

        dalton = PeakAssignmentDecoy(mz_shift_da=0.35)
        plan = plan_decoy(dalton, PeakAssignmentConfig(decoy=dalton), "run-c", tof)
        assert plan.window_ppm is None

    @pytest.mark.parametrize("shift", [9.0, -9.0])
    def test_a_shift_as_wide_as_the_window_is_still_inside_it(self, shift):
        # The matcher keeps a line at exactly its tolerance, so a shift the
        # width of the window leaves the true formula on its edge, in reach.
        control = _control(precision=3.0, tolerance=9.0)
        edge = PeakAssignmentDecoy(mz_shift_ppm=shift)

        with pytest.raises(DecoyRefused, match="inside the 9 ppm window"):
            plan_decoy(edge, PeakAssignmentConfig(decoy=edge), "run-c", control)

        past = PeakAssignmentDecoy(mz_shift_ppm=shift * 1.01)
        plan = plan_decoy(past, PeakAssignmentConfig(decoy=past), "run-c", control)
        assert plan.window_ppm == 9.0

    def test_the_window_is_the_wider_of_the_search_s_and_the_match_s(self):
        # A search window wider than the lines' is a reach of its own.
        wide_search = _control(precision=10.0, tolerance=5.0)

        with pytest.raises(DecoyRefused, match="inside the 10 ppm window"):
            plan_decoy(SHIFT, PeakAssignmentConfig(decoy=SHIFT), "run-c", wide_search)


def _mechanism(mechanism_id: str, notation: str) -> SimpleNamespace:
    return SimpleNamespace(
        ionization_mechanism_id=mechanism_id,
        ionization_mechanism=notation,
        ionization_mechanism_polarity=notation[-1],
    )


class TestSwappingAdducts:
    """Each of the mode's channels, through an adduct the chemistry does not make."""

    def test_an_adduct_the_profile_names_is_never_a_wrong_one(self):
        # Uronium makes ammonium as a secondary channel: the reference's own
        # wrong set for a positive source is sodium and ammonium, and here the
        # ammonium is the source's.
        declared = declared_channels("UR", ["[M+CH4N2O+H]+", "[M+H]+"])

        assert wrong_adducts("+", declared) == ["[M+Na]+", "[M+K]+"]
        assert wrong_adducts("-", declared_channels("BR", ["[M+Br]-"])) == [
            "[M+Cl]-",
            "[M+I]-",
        ]
        assert wrong_adducts("-", declared_channels("IODIDE", ["[M+I]-"])) == [
            "[M+Cl]-",
            "[M+Br]-",
        ]

    def test_a_secondary_adduct_is_declared_whether_or_not_it_has_a_channel(
        self, monkeypatch
    ):
        # The shipped profiles name each secondary adduct as a channel too; a
        # profile that names one without probing for it still makes it.
        from mascope_backend.api.new.peak_assignments import decoy

        monkeypatch.setattr(decoy, "secondary_channels", lambda name: ())
        declared = declared_channels("UR", ["[M+CH4N2O+H]+"])

        assert wrong_adducts("+", declared) == ["[M+Na]+", "[M+K]+"]

    def test_each_channel_takes_one_in_order_and_one_left_without_is_dropped(self):
        channels = [
            _mechanism("im-urea", "[M+CH4N2O+H]+"),
            _mechanism("im-h", "[M+H]+"),
        ]
        held = [_mechanism("im-k", "[M+K]+")]

        searched, swapped = swap_channels(channels, ["[M+Na]+", "[M+K]+"], held)

        # The deployment holds no sodium row, so potassium is the first taken.
        assert [m.ionization_mechanism_id for m in searched] == ["im-k"]
        assert swapped == {"[M+CH4N2O+H]+": "[M+K]+", "[M+H]+": None}

    def test_the_profile_s_fingerprint_channel_is_swapped_first(self):
        channels = [
            _mechanism("im-h", "[M+H]+"),
            _mechanism("im-urea", "[M+CH4N2O+H]+"),
        ]
        held = [_mechanism("im-na", "[M+Na]+")]

        _, swapped = swap_channels(channels, ["[M+Na]+"], held, fingerprint=["[M+H]+"])

        assert swapped == {"[M+H]+": "[M+Na]+", "[M+CH4N2O+H]+": None}

    def test_a_mode_of_one_channel_searches_one_wrong_adduct(self):
        held = [_mechanism("im-na", "[M+Na]+"), _mechanism("im-k", "[M+K]+")]

        searched, swapped = swap_channels(
            [_mechanism("im-h", "[M+H]+")], ["[M+Na]+", "[M+K]+"], held
        )

        assert [m.ionization_mechanism_id for m in searched] == ["im-na"]
        assert swapped == {"[M+H]+": "[M+Na]+"}

    def test_a_mode_of_two_channels_searches_two_wrong_adducts(self):
        channels = [
            _mechanism("im-h", "[M+H]+"),
            _mechanism("im-urea", "[M+CH4N2O+H]+"),
        ]
        held = [_mechanism("im-k", "[M+K]+"), _mechanism("im-na", "[M+Na]+")]

        searched, swapped = swap_channels(channels, ["[M+Na]+", "[M+K]+"], held)

        assert [m.ionization_mechanism_id for m in searched] == ["im-na", "im-k"]
        assert swapped == {"[M+CH4N2O+H]+": "[M+Na]+", "[M+H]+": "[M+K]+"}


def _plan(decoy=SHIFT, **fields) -> DecoyPlan:
    """A plan, its model reading the control's tolerance as ``plan_decoy``
    reads it beside the model's own record."""
    fields.setdefault("envelope", inherited_envelope_fit(None))
    return DecoyPlan(
        decoy=decoy,
        control_run_id="run-control",
        model=MeasurementModel.v1(
            SampleMassAccuracy(mu_ppm=0.05, sigma_ppm=0.2, anchors=30),
            0.3,
            fields["envelope"].tolerance,
        ).v2(mu_ppm=0.05, sigma_ppm=0.1, commits=200, source_lines=0),
        window_ppm=5.0 if decoy.mz_shift_ppm is not None else None,
        **fields,
    )


class TestADecoyRun:
    """The orchestrator runs the arm it planned, every stage at the control's."""

    def _start(self, plan=None, **patches):
        peaks = _peaks_df([("p1", 181.0707, 10000.0), ("p2", 182.0741, 660.0)])
        self.recorder = _Recorder()
        mocks = _start(_patches(self.recorder, peaks, _stage_a_rows()))
        self.axes: dict[str, MzShift | None] = {}

        def reads_peaks(sample):
            self.axes["peaks"] = current_mz_shift()
            return peaks

        async def matches(**kwargs):
            self.axes["matcher"] = current_mz_shift()
            return pd.DataFrame(_stage_a_rows())

        mocks["load_peaks"].side_effect = reads_peaks
        mocks["matcher"].side_effect = matches
        mocks["plan"] = _stand_in(
            "plan_sample_decoy", new_callable=AsyncMock, return_value=plan
        ).start()
        mocks["fold"] = patch(
            "mascope_backend.api.new.peak_assignments.batch_peaks_controller."
            "fold_sample_into_batch_peaks",
            new_callable=AsyncMock,
        ).start()
        mocks["gate"] = _stand_in("measure_mass_gate", wraps=measure_mass_gate).start()
        return mocks

    @pytest.mark.asyncio
    async def test_a_decoy_runs_every_stage_of_the_list_at_its_controls(self):
        # A decoy arm is the stage list's DECOY path: the run's stages, with
        # the measurement its control recorded.
        from mascope_backend.api.new.peak_assignments import pipeline

        passes = []
        run_stages = pipeline.run_stages

        async def recording(ctx):
            passes.append(ctx)
            return await run_stages(ctx)

        self._start(_plan())
        patch(f"{_PIPE}.run_stages", side_effect=recording).start()

        await _run(PeakAssignmentConfig(decoy=SHIFT))

        (ctx,) = passes
        assert ctx.path is pipeline.DECOY
        assert ctx.path.measurement is pipeline.Measurement.CONTROL
        assert ctx.ran == list(pipeline.STAGES)

    @pytest.mark.asyncio
    async def test_a_decoy_reads_its_control_s_context_whatever_its_batch_names_now(
        self,
    ):
        # MAS-918: its control ran on `auto` while the batch named the chamber;
        # the batch has named another context since. The decoy searches under
        # the chamber's, the context its control searched under, and records it.
        mocks = self._start(_plan(named_context=NamedContext(batch="chamber")))
        mocks["named_context"].return_value = NamedContext(batch="ambient-air")

        await _run(PeakAssignmentConfig(decoy=SHIFT))

        mocks["named_context"].assert_not_awaited()
        resolved = self.recorder.recorded_configs()[-1][RESOLVED_PROFILE_KEY]
        assert (resolved["context"], resolved["context_source"]) == (
            "chamber",
            "batch",
        )
        assert resolved["named_context"] == {"batch": "chamber", "dataset": None}

    @pytest.mark.asyncio
    async def test_every_read_of_the_run_sees_the_shifted_axis(self):
        self._start(_plan())

        await _run(PeakAssignmentConfig(decoy=SHIFT))

        assert self.axes == {
            "peaks": MzShift(ppm=9.0),
            "matcher": MzShift(ppm=9.0),
        }
        assert current_mz_shift() is None

    @pytest.mark.asyncio
    async def test_every_stage_scores_at_the_control_s_calibration(self):
        plan = _plan()
        mocks = self._start(plan)

        await _run(PeakAssignmentConfig(decoy=SHIFT))

        # Stage A, the search's re-score and the gate read the control's
        # model unchanged, and the gate refits none of its own.
        assert mocks["fit"].call_args.kwargs["model"] is plan.model
        assert mocks["seeded"].call_args.kwargs["model"] is plan.model
        assert {
            (id(call.kwargs["model"]), call.kwargs["refit"])
            for call in mocks["gate"].call_args_list
        } == {(id(plan.model), False)}
        scoring = mocks["compositions"].call_args.kwargs["scoring"]
        assert scoring.sigma_ppm == pytest.approx(plan.model.width_ppm)
        assert scoring.mu_ppm == pytest.approx(plan.model.scoring_mu_ppm)

    @pytest.mark.asyncio
    async def test_the_judging_is_told_what_the_lists_name(self):
        # The decoy reads its lists as the control does, so a secondary
        # channel's winner a list names is corroborated in both (3.4), by the
        # second-channel cap of the judging rounds.
        from mascope_backend.api.new.peak_assignments import judging_loop

        self._start(_plan(envelope=inherited_envelope_fit(_curve_record())))
        judging = _stand_in(
            "cap_second_channel", wraps=judging_loop.cap_second_channel
        ).start()

        await _run(PeakAssignmentConfig(decoy=SHIFT))

        assert judging.call_args.kwargs["listed_formulas"] == frozenset({"C6H12O6"})

    @pytest.mark.asyncio
    async def test_every_score_reads_the_control_s_envelope_tolerance(self):
        # The control fitted a curve; the decoy fits none on its own commits,
        # and Stage A, the search's ranking and the seeded re-score all read
        # the control's from the first pass.
        record = _curve_record()
        plan = _plan(envelope=inherited_envelope_fit(record))
        mocks = self._start(plan)
        fitted = _stand_in("fit_run_envelope_tolerance", new_callable=AsyncMock).start()

        await _run(PeakAssignmentConfig(decoy=SHIFT))

        tolerance = EnvelopeTolerance.from_record(record)
        fitted.assert_not_awaited()
        assert [
            call.kwargs["model"].envelope_tolerance
            for call in mocks["fit"].call_args_list
        ] == [tolerance]
        scoring = mocks["compositions"].call_args.kwargs["scoring"]
        assert scoring.envelope_tolerance == tolerance
        assert mocks["seeded"].await_args.kwargs["model"].envelope_tolerance == (
            tolerance
        )
        assert self.recorder.recorded_configs()[-1][ENVELOPE_TOLERANCE_KEY] == {
            **record,
            "inherited": True,
        }

    @pytest.mark.asyncio
    async def test_the_run_records_its_arm_and_its_control_and_is_never_folded(self):
        plan = _plan()
        mocks = self._start(plan)

        result = await _run(PeakAssignmentConfig(decoy=SHIFT))

        assert result["status"] == "success"
        configs = self.recorder.recorded_configs()
        # Recorded before the stages too, so a decoy that fails says so.
        assert configs[0][DECOY_ARM_KEY] == plan.record()
        assert configs[-1][DECOY_ARM_KEY] == plan.record()
        assert configs[-1]["decoy"] == SHIFT.model_dump()
        model = configs[-1][MEASUREMENT_MODEL_KEY]
        assert model["gate"]["inherited"] is True
        # The run scored and capped at the control's model, not at what this
        # sample's two library lines would have measured (nothing).
        assert model == {
            **plan.model.record(),
            "scoring": model["scoring"],
            "gate": model["gate"],
        }
        assert (model["version"], model["mu_ppm"], model["mu_source"]) == (
            2,
            0.05,
            "fitted",
        )
        assert model["width_ppm"] == pytest.approx(
            scoring_sigma_ppm(0.2, 0.3), abs=1e-4
        )
        mocks["fold"].assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_batch_s_series_reaches_no_decoy(self):
        # The batch ledger is the control's chemistry: a homologous series it
        # holds is no observation of a reading on a shifted axis.
        self._start(_plan())
        series = _stand_in(
            "batch_series_by_peak",
            new_callable=AsyncMock,
            return_value={
                "p1": {
                    "formula": "C6H12O6",
                    "ionization_mechanism_id": "[M+H]+",
                    "has_anchor": True,
                }
            },
        ).start()

        await _run(PeakAssignmentConfig(decoy=SHIFT))

        series.assert_not_awaited()
        assert all(
            "series" not in (row.get("provenance") or {}) for row in self.recorder.rows
        )
        assert self.recorder.recorded_configs()[-1]["tiering"]["series_anchored"] == 0

    @pytest.mark.asyncio
    async def test_the_adduct_arm_searches_the_swapped_channels_alone(self):
        sodium = _mechanism("im-na", "[M+Na]+")
        plan = _plan(SWAP, mechanisms=(sodium,), swapped={"[M+H]+": "[M+Na]+"})
        mocks = self._start(plan)

        await _run(PeakAssignmentConfig(decoy=SWAP))

        # The profile is the sample's own, resolved through its mode's channel.
        configs = self.recorder.recorded_configs()
        assert configs[0]["resolved_profile"]["profile"] == "ESI_POS"
        assert mocks["known"].call_args.args[2] == ["im-na"]
        assert mocks["ionizations"].call_args.args[0] == [sodium]
        assert self.axes["peaks"] is None

    @pytest.mark.asyncio
    async def test_the_adduct_arm_opens_no_secondary_channel(self):

        sodium = _mechanism("im-na", "[M+Na]+")
        plan = _plan(SWAP, mechanisms=(sodium,), swapped={"[M+H]+": "[M+Na]+"})
        self._start(plan)
        opened = _mechanism("im-k", "[M+K]+")

        async def secondary(sample, resolved_profile, peaks_df):
            return resolved_profile, [opened]

        _stand_in("_resolve_secondary_channels", side_effect=secondary).start()
        searched = _stand_in(
            "_searched_mechanisms", wraps=sample_mechanisms._searched_mechanisms
        ).start()

        await _run(PeakAssignmentConfig(decoy=SWAP))

        assert searched.call_args.args[:2] == ([sodium], [])

    @pytest.mark.asyncio
    async def test_a_refused_decoy_is_no_run(self):
        mocks = self._start()
        mocks["plan"].side_effect = DecoyRefused("the reason")

        result = await _run(PeakAssignmentConfig(decoy=SHIFT))

        assert result["status"] == "skipped"
        assert "no decoy: the reason" in result["message"]
        mocks["create_run"].assert_not_awaited()
        mocks["load_peaks"].assert_not_called()

    @pytest.mark.asyncio
    async def test_a_refused_decoy_s_adopted_run_is_failed_with_the_reason(self):
        from mascope_backend.api.new.peak_assignments.service import assign_sample_peaks

        mocks = self._start()
        mocks["plan"].side_effect = DecoyRefused("the reason")
        _stand_in("in_flight_run_id", new_callable=AsyncMock, return_value=None).start()

        await assign_sample_peaks(
            sample_item_id="si-1",
            config=PeakAssignmentConfig(decoy=SHIFT),
            user_id=None,
            run_id="run-adopted",
        )

        mocks["finalize"].assert_awaited_once_with(
            "run-adopted", "failed", error="no decoy: the reason"
        )

    @pytest.mark.asyncio
    async def test_a_control_run_reads_the_axis_as_stored_and_fits_its_own(self):
        mocks = self._start()

        await _run(PeakAssignmentConfig())

        mocks["plan"].assert_not_awaited()
        assert self.axes == {"peaks": None, "matcher": None}
        assert mocks["fit"].call_args.kwargs["model"] is None
        seeded = mocks["seeded"].call_args.kwargs["model"]
        assert seeded.version == 1
        assert {call.kwargs["refit"] for call in mocks["gate"].call_args_list} == {True}
        assert DECOY_ARM_KEY not in self.recorder.recorded_configs()[-1]
        assert (
            "inherited"
            not in self.recorder.recorded_configs()[-1][MEASUREMENT_MODEL_KEY]["gate"]
        )
        assert (
            "inherited"
            not in self.recorder.recorded_configs()[-1][ENVELOPE_TOLERANCE_KEY]
        )
        mocks["fold"].assert_awaited_once_with("si-1")


def _reads_the_block_s_axis(peaks: pd.DataFrame):
    """``load_sample_peaks``'s read of a stored frame: on the block's axis."""

    def read(sample):
        return peaks.assign(mz=shift_mz(peaks["mz"].to_numpy()))

    return read


class TestTheSourcesIonsAreWhereTheControlReadThem:
    """A shift moves the axis off the sample's formulas, not off the source:
    a shifted arm opens the channels its control opened and claims the
    reagent lines its control claimed (#2980). Moved 0.35 Da, the carriers
    and the lines sit out of both passes' 20 ppm reach on the moved axis."""

    UREA = SimpleNamespace(
        ionization_mechanism_id="im-ur",
        ionization_mechanism="[M+CH4N2O+H]+",
        ionization_mechanism_polarity="+",
    )
    AMMONIUM = SimpleNamespace(
        ionization_mechanism_id="im-nh4",
        ionization_mechanism="[M+NH4]+",
        ionization_mechanism_polarity="+",
    )

    def _start(self, decoy):
        from mascope_tools.composition.reagents import ion_mz

        # The uronium source's reagent ion and its dimer, which the pre-pass
        # claims, and the urea-ammonium cluster that carries the ammonium
        # channel, which it does not.
        self.stored = {
            "reagent": ion_mz("CH5N2O", 1),
            "dimer": ion_mz("C2H9N4O2", 1),
            "carrier": ion_mz("CH8N3O", 1),
            "analyte": 181.0707,
        }
        peaks = _peaks_df(
            [
                ("reagent", self.stored["reagent"], 1.0e6),
                ("dimer", self.stored["dimer"], 4.0e5),
                ("carrier", self.stored["carrier"], 2.0e5),
                ("analyte", self.stored["analyte"], 1.0e4),
            ]
        )
        self.recorder = _Recorder()
        patches = _patches(self.recorder, peaks, [])
        patches["load_peaks"] = _stand_in(
            "load_sample_peaks", side_effect=_reads_the_block_s_axis(peaks)
        )
        patches["mechanisms"] = _stand_in(
            "fetch_sample_mechanisms",
            new_callable=AsyncMock,
            return_value=(["im-ur"], [self.UREA]),
        )
        patches["secondary"] = _stand_in(
            "fetch_mechanisms_by_notation",
            new_callable=AsyncMock,
            return_value=[self.AMMONIUM],
        )
        mocks = _start(patches)
        _stand_in(
            "plan_sample_decoy",
            new_callable=AsyncMock,
            return_value=None if decoy is None else _plan(decoy),
        ).start()
        patch(
            "mascope_backend.api.new.peak_assignments.batch_peaks_controller."
            "fold_sample_into_batch_peaks",
            new_callable=AsyncMock,
        ).start()
        return mocks

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "decoy", [None, SHIFT, DALTON], ids=["control", "ppm", "dalton"]
    )
    async def test_the_arm_opens_the_channel_its_control_opened(self, decoy):
        mocks = self._start(decoy)

        await _run(PeakAssignmentConfig(run_untargeted=False, decoy=decoy))

        snapshot = self.recorder.recorded_configs()[0][RESOLVED_PROFILE_KEY]
        assert snapshot["profile"] == "UR"
        assert snapshot["secondary_channels"] == ["[M+NH4]+"]
        (evidence,) = snapshot["channel_evidence"]
        assert evidence["status"] == "found"
        # Found where the control found it, not where the arm moved it.
        assert evidence["mz_error_ppm"] == pytest.approx(0.0, abs=0.01)
        assert [
            mechanism.ionization_mechanism_id
            for mechanism in mocks["reference"].await_args.args[2]
        ] == ["im-ur", "im-nh4"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "decoy", [None, SHIFT, DALTON], ids=["control", "ppm", "dalton"]
    )
    async def test_the_arm_claims_the_lines_its_control_claimed(self, decoy):
        self._start(decoy)

        await _run(PeakAssignmentConfig(run_untargeted=False, decoy=decoy))

        reagent = {
            row["sample_peak_id"]: row
            for row in self.recorder.rows
            if row["role"] == "reagent"
        }
        assert sorted(reagent) == ["dimer", "reagent"]
        with shifted_mz_axis(
            ppm=None if decoy is None else decoy.mz_shift_ppm,
            da=None if decoy is None else decoy.mz_shift_da,
        ):
            read = {key: float(shift_mz(mz)) for key, mz in self.stored.items()}
        for peak_id, row in reagent.items():
            # Claimed at the offset the control measured, and named at the
            # m/z the run reads the peak at, as every other row of it is.
            assert row["mz_error_ppm"] == pytest.approx(0.0, abs=0.01)
            assert row["sample_peak_mz"] == pytest.approx(read[peak_id], abs=1e-9)


class TestTheSourcesLaddersAreTheSamples:
    """The adduct arm swaps what a stage reads a molecule through, never the
    source's lines: the reagent pre-pass's ladder claim reads the mode's own
    channels, so a decoy is offered the peaks its control is (step 3.3d). A
    shifted arm climbs them on the axis as stored, where its control did."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "decoy", [None, SWAP, DALTON], ids=["control", "decoy", "dalton"]
    )
    async def test_a_hydride_bound_rung_is_claimed_by_the_decoy_as_by_its_control(
        self, decoy
    ):
        from mascope_tools.composition.reagents import ion_mz

        opened = TestTheListsAreReadThroughTheOpenedChannels
        peaks = _peaks_df(
            [
                ("core", ion_mz("C2H5O", 1), 1.0e6),
                ("rung", ion_mz("C4H11O2", 1), 5.0e5),
            ]
        )
        recorder = _Recorder()
        patches = _patches(recorder, peaks, [])
        patches["load_peaks"] = _stand_in(
            "load_sample_peaks", side_effect=_reads_the_block_s_axis(peaks)
        )
        patches["mechanisms"] = _stand_in(
            "fetch_sample_mechanisms",
            new_callable=AsyncMock,
            return_value=(["im-ct"], [opened.CT]),
        )
        # The mode abstracts a hydride through an opened channel, which the
        # swap closes for the stages.
        patches["secondary"] = _stand_in(
            "fetch_mechanisms_by_notation",
            new_callable=AsyncMock,
            return_value=[
                opened.PROTON,
                TestTheLaddersAreClimbedThroughTheOpenedChannels.HYDRIDE,
            ],
        )
        _start(patches)
        sodium = _mechanism("im-na", "[M+Na]+")
        _stand_in(
            "plan_sample_decoy",
            new_callable=AsyncMock,
            return_value=(
                _plan(DALTON)
                if decoy is DALTON
                else _plan(SWAP, mechanisms=(sodium,), swapped={"[M]+.": "[M+Na]+"})
            ),
        ).start()
        patch(
            "mascope_backend.api.new.peak_assignments.batch_peaks_controller."
            "fold_sample_into_batch_peaks",
            new_callable=AsyncMock,
        ).start()

        await _run(PeakAssignmentConfig(run_untargeted=False, decoy=decoy))

        by_peak = {row["sample_peak_id"]: row for row in recorder.rows}
        assert by_peak["rung"]["role"] == "reagent"


class TestPlanningTheAdductArm:
    """The service reads the sample's channels and the deployment's rows."""

    def _start(self, *, profile: str, channels, held, polarity="-"):
        control = _control(profile=profile)
        _stand_in(
            "_latest_in_app_run",
            new_callable=AsyncMock,
            return_value=("run-c", control),
        ).start()
        _stand_in(
            "fetch_sample_mechanisms",
            new_callable=AsyncMock,
            return_value=([m.ionization_mechanism_id for m in channels], channels),
        ).start()
        self.asked = _stand_in(
            "fetch_mechanisms_by_notation",
            new_callable=AsyncMock,
            return_value=held,
        ).start()
        self.control = control
        return SimpleNamespace(
            sample_item_id="si-1", polarity=polarity, filename="orbi-sample.raw"
        )

    @pytest.mark.asyncio
    async def test_each_channel_is_swapped_the_fingerprint_first(self):
        from mascope_backend.api.new.peak_assignments.service import plan_sample_decoy

        # A nitrate mode that also declares carbonate: alphabetically the
        # carbonate channel would take the one wrong adduct the deployment
        # holds, and the nitrate channel, which reads most of the sample, none.
        sample = self._start(
            profile="NO3",
            channels=[
                _mechanism("im-co3", "[M+CO3]-"),
                _mechanism("im-no3", "[M+NO3]-"),
            ],
            held=[_mechanism("im-i", "[M+I]-")],
        )

        plan = await plan_sample_decoy(sample, PeakAssignmentConfig(decoy=SWAP))

        assert self.asked.call_args.args == (["[M+Cl]-", "[M+I]-", "[M+Br]-"], "-")
        assert [m.ionization_mechanism_id for m in plan.mechanisms] == ["im-i"]
        assert plan.swapped == {"[M+NO3]-": "[M+I]-", "[M+CO3]-": None}
        assert plan.record()["swapped_channels"] == plan.swapped

    @pytest.mark.asyncio
    async def test_no_wrong_adduct_the_deployment_holds_is_no_decoy(self):
        from mascope_backend.api.new.peak_assignments.service import plan_sample_decoy

        sample = self._start(
            profile="BR", channels=[_mechanism("im-br", "[M+Br]-")], held=[]
        )

        with pytest.raises(DecoyRefused, match="holds no adduct"):
            await plan_sample_decoy(sample, PeakAssignmentConfig(decoy=SWAP))

    @pytest.mark.asyncio
    async def test_a_mode_with_no_channel_has_nothing_to_swap(self):
        from mascope_backend.api.new.peak_assignments.service import plan_sample_decoy

        sample = self._start(profile="BR", channels=[], held=[])

        with pytest.raises(DecoyRefused, match="no channel"):
            await plan_sample_decoy(sample, PeakAssignmentConfig(decoy=SWAP))

    @pytest.mark.asyncio
    async def test_a_decoy_that_resolves_another_context_is_refused(self):
        # Under the control's named context the decoy resolves the profile's
        # default here, while its control searched under the chamber's: the
        # library has since dropped what it named, say. No decoy then.
        from mascope_backend.api.new.peak_assignments.service import plan_sample_decoy

        sample = self._start(
            profile="BR", channels=[_mechanism("im-br", "[M+Br]-")], held=[]
        )
        self.control[RESOLVED_PROFILE_KEY].update(
            context="chamber", named_context={"batch": None, "dataset": None}
        )

        with pytest.raises(DecoyRefused, match="its control searched under 'chamber'"):
            await plan_sample_decoy(sample, PeakAssignmentConfig(decoy=SHIFT))

    @pytest.mark.asyncio
    async def test_a_decoy_whose_mode_now_resolves_another_profile_is_refused(self):
        # The sample's mode was edited from bromide to nitrate since its control
        # ran. Both profiles default to the same context name, so the context
        # alone would let the decoy search another reagent's chemistry.
        from mascope_backend.api.new.peak_assignments.service import plan_sample_decoy

        sample = self._start(
            profile="BR", channels=[_mechanism("im-no3", "[M+NO3]-")], held=[]
        )
        context = resolve_profile(
            PeakAssignmentConfig(),
            mechanism_notations=["[M+NO3]-"],
            instrument_type="orbi",
            polarity="-",
        ).context.name
        self.control[RESOLVED_PROFILE_KEY].update(
            context=context, named_context={"batch": None, "dataset": None}
        )

        with pytest.raises(
            DecoyRefused,
            match="resolves the reagent profile 'NO3', and its control searched "
            "under 'BR'",
        ):
            await plan_sample_decoy(sample, PeakAssignmentConfig(decoy=SHIFT))

    @pytest.mark.asyncio
    async def test_a_decoy_under_its_control_s_named_context_is_planned(self):
        from mascope_backend.api.new.peak_assignments.service import plan_sample_decoy

        sample = self._start(
            profile="BR", channels=[_mechanism("im-br", "[M+Br]-")], held=[]
        )
        self.control[RESOLVED_PROFILE_KEY].update(
            context="chamber", named_context={"batch": "chamber", "dataset": None}
        )

        plan = await plan_sample_decoy(sample, PeakAssignmentConfig(decoy=SHIFT))

        assert plan.named_context == NamedContext(batch="chamber")

    @pytest.mark.asyncio
    async def test_a_shift_reads_no_channel(self):
        from mascope_backend.api.new.peak_assignments.service import plan_sample_decoy

        sample = self._start(profile="BR", channels=[], held=[])

        plan = await plan_sample_decoy(sample, PeakAssignmentConfig(decoy=SHIFT))

        assert plan.mechanisms == ()
        self.asked.assert_not_awaited()
