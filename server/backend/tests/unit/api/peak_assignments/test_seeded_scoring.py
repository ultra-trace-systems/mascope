"""Unit tests for the measurement model the seeded scoring chain scores at.

Every seed list is scored at one measurement model: the one its caller hands
it - a run's, a decoy's control's - or, outside a run, the one the sample's
latest run recorded (``model_readers.recorded_model``). It never fits one of its own:
a pin or the inspector's shortlist is a handful of formulas, mostly wrong ones,
and a bright ion that matches eight lines alone measures only itself. So one
ion in one sample reads alike alone and among many seeds. The matcher, the
mechanism read and the recorded model are stubbed; the ions, the gating and the
fit are the real ones. See ``seeded_scoring.py``.
"""

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from mascope_backend.api.new.peak_assignments import (
    judging_loop,
    model_readers,
    sample_inputs,
    seeded_scoring,
)
from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig
from mascope_backend.api.new.peak_assignments.engine import (
    ReagentOffset,
    SampleMassAccuracy,
    score_ions_by_fit,
)
from mascope_backend.api.new.peak_assignments.seeded_scoring import (
    build_seeded_isotopes_df,
    score_seeds,
)
from mascope_match.params import (
    ORBI_DEFAULT_ISOTOPE_ABUNDANCE_THRESHOLD,
    OrbiMatchParams,
)
from mascope_tools.composition.custom_elements import CUSTOM_ELEMENTS, label_purity
from mascope_tools.composition.envelope_tolerance import EnvelopeTolerance
from mascope_tools.composition.mass_accuracy import fit_sample_mass_accuracy
from mascope_tools.composition.measurement_model import MeasurementModel
from mascope_tools.composition.profiles import INSTRUMENT_FALLBACK_SIGMA_PPM


pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _no_resolution(monkeypatch):
    """These samples have no file to read a resolving power off: their seeds'
    lines are the fine structure, as a file without one gets them."""

    async def none(sample):
        return None

    monkeypatch.setattr(sample_inputs, "_resolution_of", none)


MECH = "mech-h"
TARGET = ("C20H38O2", MECH)
#: Twenty others, C and O counts the target's, so their envelopes are as deep.
OTHERS = {(f"C20H{h}O2", MECH) for h in range(2, 42, 2)}
ORBI_WIDTH = INSTRUMENT_FALLBACK_SIGMA_PPM["orbi"]
#: The target's lines sit this far off: inside the Orbitrap's tolerance, far
#: enough out that the width it is judged at shows in the fit.
TARGET_ERROR_PPM = 0.5
#: The others' lines sit at -e, 0 and +e in equal shares, so the frame's fitted
#: offset is zero and its fitted width - 1.4826 x the median absolute
#: deviation, here e - is the class's own.
SPREAD_PPM = ORBI_WIDTH / 1.4826
#: The lines a spectrum shows: an ion's M0, 13C and 13C2. Fewer than the eight
#: a width is fitted from, as for most single ions; the rest are below noise.
FOUND_ABUNDANCE = 0.01
#: Where the reagent lines put the gate's labelled-nitrate set's axis, about
#: 2.2 of the Orbitrap class's widened widths out.
AXIS_PPM = -1.26


def _matched(
    target_isotopes_df: pd.DataFrame,
    target_error_ppm: float = TARGET_ERROR_PPM,
    found_abundance: float = FOUND_ABUNDANCE,
    others_centre_ppm: float = 0.0,
    others_spread_ppm: float = SPREAD_PPM,
) -> pd.DataFrame:
    """The lines a spectrum shows - those at ``found_abundance`` and above -
    found at their predicted height, the target's ``target_error_ppm`` off and
    the others' ``others_spread_ppm`` about ``others_centre_ppm``; the rest
    absent."""
    frame = target_isotopes_df.copy().reset_index(drop=True)
    found = frame["relative_abundance"] >= found_abundance
    target = frame["target_compound_formula"] == TARGET[0]
    others = frame.index[found & ~target]
    pattern = [
        others_centre_ppm + e for e in (-others_spread_ppm, 0.0, others_spread_ppm)
    ]
    frame["match_mz_error"] = np.where(found, target_error_ppm, np.nan)
    frame.loc[others, "match_mz_error"] = [pattern[i % 3] for i in range(len(others))]
    frame["sample_peak_id"] = [
        f"peak-{i}" if hit else None for i, hit in zip(frame.index, found)
    ]
    frame["sample_peak_intensity"] = np.where(
        found, frame["relative_abundance"] * 1e8, 0.0
    )
    frame["match_abundance_error"] = np.where(found, 0.0, np.nan)
    frame["match_score"] = np.where(found, 1.0, 0.0)
    return frame


#: A sample its latest run recorded no model of its own for: the class's width,
#: no centre, today's line tolerance.
STAND_IN = MeasurementModel.v1(SampleMassAccuracy(), ORBI_WIDTH)


def _model(mu_ppm=None, tolerance=None) -> MeasurementModel:
    """A model at the class's width, centred at ``mu_ppm`` (a stand-in centre
    from the reagent lines), reading a line's share at ``tolerance``."""
    reagent = (
        None
        if mu_ppm is None
        else ReagentOffset(mu_ppm=mu_ppm, lines=7, beyond_width=True)
    )
    return MeasurementModel.v1(
        SampleMassAccuracy(reagent=reagent), ORBI_WIDTH, tolerance
    )


@pytest.fixture
def recorded(monkeypatch):
    """The model the sample's latest run recorded, as ``score_seeds`` reads it
    outside a run: the stand-in by default; set ``model`` to give it another.
    ``calls`` records the samples it was read for, ``configs`` the
    configuration each was read under."""
    state = SimpleNamespace(model=STAND_IN, calls=[], configs=[])

    async def recorded_model(sample, config=None):
        state.calls.append(sample)
        state.configs.append(config)
        return state.model

    monkeypatch.setattr(model_readers, "recorded_model", recorded_model)
    return state


class _Frames(list):
    """The frames the stubbed matcher returned, and the target's lines' error,
    the faintest line found and the others' centre and spread it places them
    at, which a test moves."""

    target_error_ppm = TARGET_ERROR_PPM
    found_abundance = FOUND_ABUNDANCE
    others_centre_ppm = 0.0
    others_spread_ppm = SPREAD_PPM
    #: The labelled reagent's purity the sample's setup carries: None, as for
    #: a sample bound under no mode, leaves the library's default.
    setup_purity = None


@pytest.fixture
def stubbed(monkeypatch, recorded):
    frames = _Frames()
    frames.purities = []

    async def setup_purity(sample_item_id):
        return frames.setup_purity

    def build(*args):
        # Where the seeded ions' lines are built, off the event loop.
        frames.purities.append(label_purity("^N"))
        return build_seeded_isotopes_df(*args)

    async def mechanisms(mechanism_ids):
        return {
            MECH: SimpleNamespace(
                ionization_mechanism_id=MECH,
                ionization_mechanism="[M+H]+",
                ionization_mechanism_polarity="+",
            )
        }

    async def match(filename, target_isotopes_df, polarity):
        frames.append(
            _matched(
                target_isotopes_df,
                frames.target_error_ppm,
                frames.found_abundance,
                frames.others_centre_ppm,
                frames.others_spread_ppm,
            )
        )
        return frames[-1]

    monkeypatch.setattr(seeded_scoring, "fetch_mechanisms_by_id", mechanisms)
    monkeypatch.setattr(seeded_scoring, "compute_match_isotopes", match)
    monkeypatch.setattr(seeded_scoring, "sample_label_purity", setup_purity)
    monkeypatch.setattr(seeded_scoring, "build_seeded_isotopes_df", build)
    monkeypatch.setattr(sample_inputs, "get_instrument_type", lambda name: "orbi")

    return frames


SAMPLE = SimpleNamespace(sample_item_id="si-1", filename="sample.raw", polarity="+")


async def _fit(seeds, **kwargs):
    ion_by_seed, fit_by_ion, _errors, _scored = await score_seeds(
        SAMPLE, seeds, OrbiMatchParams(), **kwargs
    )
    return fit_by_ion[ion_by_seed[TARGET]]


async def test_a_lone_seed_scores_as_it_does_among_twenty_others(stubbed):
    lone = await _fit({TARGET})
    crowd = await _fit({TARGET} | OTHERS)

    # The premise: one ion's three lines fit no width, twenty-one ions' do,
    # and the crowd's is the class's own at no offset. Both are scored at the
    # sample's model, which neither frame fits.
    assert fit_sample_mass_accuracy(stubbed[0]) == (None, None)
    mu, sigma = fit_sample_mass_accuracy(stubbed[-1])
    assert mu == pytest.approx(0.0, abs=1e-9)
    assert sigma == pytest.approx(ORBI_WIDTH, abs=1e-6)
    assert lone == pytest.approx(crowd, abs=0.01)


async def test_the_generic_width_would_read_the_lone_seed_otherwise(stubbed):
    """Why the model's width matters: scored without it, the lone seed is
    judged at the score's generic 2 ppm, where 0.5 ppm off reads as near
    perfect."""
    lone = await _fit({TARGET})
    generic = score_ions_by_fit(stubbed[-1])["match_score"].iloc[0]
    assert generic - lone > 0.05


async def test_a_lone_bright_seed_is_not_calibrated_on_its_own_lines(stubbed):
    """A bright ion found in every line above the match floor matches more than
    the eight lines a fit needs, all of them its own. Fitted on them it would
    be judged against itself: its own error as the offset, the floor as the
    width. It reads as it does among twenty others instead."""
    stubbed.found_abundance = ORBI_DEFAULT_ISOTOPE_ABUNDANCE_THRESHOLD
    bright = await _fit({TARGET})
    alone = stubbed[-1]
    crowd = await _fit({TARGET} | OTHERS)

    # The premise: the lone ion's own lines are enough to fit, and fitted they
    # put the offset at its error and the width at the floor.
    mu, sigma = fit_sample_mass_accuracy(alone)
    assert mu == pytest.approx(TARGET_ERROR_PPM)
    assert sigma == pytest.approx(0.05)
    self_calibrated = score_ions_by_fit(
        seeded_scoring.apply_match_params(alone, OrbiMatchParams()),
        fallback_sigma_ppm=ORBI_WIDTH,
    )["match_score"].iloc[0]

    assert bright == pytest.approx(crowd, abs=0.01)
    assert self_calibrated - bright > 0.2


async def test_a_long_seed_list_is_not_centred_on_itself(stubbed):
    """Twenty-one ions all 1.26 ppm off would fit their own offset there, and
    read as on the axis. The chain fits none: they are read at the sample's
    model, which puts them 1.26 ppm off it, as a run of the sample reads them
    (a decoy's moved axis is the case this matters most for)."""
    stubbed.target_error_ppm = AXIS_PPM
    stubbed.others_centre_ppm = AXIS_PPM
    crowd = await _fit({TARGET} | OTHERS)
    mu, _sigma = fit_sample_mass_accuracy(stubbed[-1])
    stubbed.target_error_ppm = 0.0
    stubbed.others_centre_ppm = 0.0
    centred = await _fit({TARGET} | OTHERS)

    assert mu == pytest.approx(AXIS_PPM, abs=1e-6)
    assert centred - crowd > 0.1


async def test_a_seed_list_is_scored_at_the_centre_the_sample_s_model_puts(
    stubbed, recorded
):
    """A pin, the inspector, the batch ledger: each reads the centre the
    sample's latest run recorded, a stand-in centre from the reagent lines
    included, as that run read its own rows."""
    stubbed.target_error_ppm = 0.0
    centred = await _fit({TARGET})
    stubbed.target_error_ppm = AXIS_PPM
    uncorrected = await _fit({TARGET})
    recorded.model = _model(AXIS_PPM)
    corrected = await _fit({TARGET})

    assert recorded.calls == [SAMPLE] * 3
    assert corrected == pytest.approx(centred, abs=0.01)
    # Uncorrected, 2.2 widths off reads as a much worse ion.
    assert corrected - uncorrected > 0.1


async def test_the_sample_is_read_under_the_callers_configuration(stubbed, recorded):
    """The batch ledger's propagation is a step of a batch search whose
    profile may be overridden, and reads the sample under that search's
    configuration where no run recorded a model; a pin or the inspector, with
    none, under the default."""
    config = PeakAssignmentConfig(profile="UR")

    await _fit({TARGET}, config=config)
    await _fit({TARGET})

    assert recorded.configs == [config, None]


async def test_a_handed_model_is_used_and_none_is_read(stubbed, recorded):
    """A caller inside a run hands over its model; the sample's recorded one
    is then not read."""
    stubbed.target_error_ppm = AXIS_PPM
    recorded.model = _model(AXIS_PPM)
    resolved = await _fit({TARGET})
    recorded.calls.clear()

    handed = await _fit({TARGET}, model=_model(AXIS_PPM))
    uncentred = await _fit({TARGET}, model=STAND_IN)

    assert recorded.calls == []
    assert handed == pytest.approx(resolved)
    assert resolved - uncentred > 0.1


async def test_a_runs_seeded_re_score_is_scored_at_the_runs_model(stubbed, recorded):
    """The untargeted stage's commits are measured again through this chain
    (``judging_loop._seeded_rescore``) at the run's model, the one Stage A was
    scored at and the search ranked at, and none is read or fitted."""
    stubbed.target_error_ppm = AXIS_PPM
    model = _model(AXIS_PPM)

    fits, frame = await judging_loop._seeded_rescore(
        SAMPLE, OrbiMatchParams(), {TARGET}, model=model
    )

    assert recorded.calls == []
    assert fits[TARGET] == pytest.approx(await _fit({TARGET}, model=model))
    assert fits[TARGET] - await _fit({TARGET}, model=STAND_IN) > 0.1
    assert set(frame.ion_by_seed) == {TARGET}


async def test_the_re_score_s_frame_scores_again_as_a_fresh_pass_would(stubbed):
    """The run scores its rows again at a refitted model off the frame the
    re-score kept (``judging_loop.fits_at``): the same lines, the same gate, at the
    new centre and width, without another match pass."""
    stubbed.target_error_ppm = AXIS_PPM
    seeds = {TARGET} | set(sorted(OTHERS)[:3])
    _fits, frame = await judging_loop._seeded_rescore(
        SAMPLE, OrbiMatchParams(), seeds, model=STAND_IN
    )
    passes = len(stubbed)

    again = judging_loop.fits_at(_model(AXIS_PPM), None, frame)

    assert len(stubbed) == passes
    for seed in seeds:
        assert again["untargeted"][seed] == pytest.approx(
            await _fit_seed(seed, seeds, model=_model(AXIS_PPM))
        )


async def _fit_seed(seed, seeds, **kwargs):
    ion_by_seed, fit_by_ion, _errors, _scored = await score_seeds(
        SAMPLE, seeds, OrbiMatchParams(), **kwargs
    )
    return fit_by_ion[ion_by_seed[seed]]


async def test_a_decoy_s_re_score_is_scored_at_its_control_s_model(stubbed):
    """A decoy run's commits sit on an axis moved on purpose. A list long
    enough to fit its own offset would centre on the move; the re-score reads
    its control's model instead, through every step of the chain."""
    stubbed.target_error_ppm = 3.0
    stubbed.others_centre_ppm = 3.0
    seeds = {TARGET} | OTHERS
    control = MeasurementModel.v1(
        SampleMassAccuracy(mu_ppm=0.0, sigma_ppm=0.2, anchors=40, anchor_ions=12),
        ORBI_WIDTH,
    )
    stubbed.others_centre_ppm = 0.0
    stubbed.target_error_ppm = 0.0
    on_axis, _ = await judging_loop._seeded_rescore(
        SAMPLE, OrbiMatchParams(), seeds, model=control
    )
    stubbed.target_error_ppm = 3.0
    stubbed.others_centre_ppm = 3.0
    decoy, _ = await judging_loop._seeded_rescore(
        SAMPLE, OrbiMatchParams(), seeds, model=control
    )

    # At the control's centre the target reads three ppm off it.
    assert on_axis[TARGET] - decoy[TARGET] > 0.3


async def test_a_re_score_with_nothing_to_score_hands_on_nothing(stubbed, monkeypatch):
    no_seeds = await judging_loop._seeded_rescore(
        SAMPLE, OrbiMatchParams(), set(), model=STAND_IN
    )

    async def nothing(**_kwargs):
        return pd.DataFrame()

    monkeypatch.setattr(seeded_scoring, "compute_match_isotopes", nothing)
    no_lines = await judging_loop._seeded_rescore(
        SimpleNamespace(**vars(SAMPLE), sample_item_name="sample"),
        OrbiMatchParams(),
        {TARGET},
        model=STAND_IN,
    )

    assert no_seeds == ({}, None)
    assert no_lines == ({}, None)


async def test_the_seeds_are_read_at_the_purity_of_the_samples_setup(stubbed):
    """A run reads its sample's labelled ions at the purity the sample's setup
    carries, and the readers outside a run - the batch search's propagation,
    the inspector's evidence and alternatives - score through here, so they
    read them alike."""
    await _fit({TARGET})
    stubbed.setup_purity = 0.9
    await _fit({TARGET})

    assert stubbed.purities == [CUSTOM_ELEMENTS["^N"].default_purity, 0.9]
    assert label_purity("^N") == CUSTOM_ELEMENTS["^N"].default_purity


#: A run's fitted tolerance that reads a line 10% under its share as centred:
#: the TOF's form, which the stub's lines (no S/N, found at their share) feel.
CENTRED_LOW = EnvelopeTolerance(centre=-0.1, floor=0.05)


async def test_a_seed_list_outside_a_run_is_read_at_its_latest_runs_tolerance(
    stubbed, recorded
):
    """A pin, the inspector, the batch ledger's propagation: none of them is a
    run, and each reads a line at the tolerance the sample's latest run fitted,
    with the rest of its model, as that run read its own rows."""
    recorded.model = _model(tolerance=CENTRED_LOW)

    resolved = await _fit({TARGET})

    assert recorded.calls == [SAMPLE]
    assert resolved == pytest.approx(
        await _fit({TARGET}, model=_model(tolerance=CENTRED_LOW))
    )
    assert resolved < await _fit({TARGET}, model=STAND_IN) - 0.05


async def test_a_runs_seeded_re_score_is_handed_the_runs_tolerance(stubbed, recorded):
    """The search's commits are measured again at the tolerance Stage A fitted,
    the run's model's, not at what an earlier run recorded."""
    fits, _frame = await judging_loop._seeded_rescore(
        SAMPLE, OrbiMatchParams(), {TARGET}, model=_model(tolerance=CENTRED_LOW)
    )

    assert recorded.calls == []
    assert fits[TARGET] < await _fit({TARGET}, model=STAND_IN) - 0.05


def _low_resolution(mz):
    """A resolving power at which one peak holds an ion's M+1 cluster."""
    return 2000.0


async def test_the_seeds_lines_are_resolved_at_the_files_resolution(
    stubbed, monkeypatch
):
    """The seeded chain predicts its lines as Stage A and the search do, at the
    file's R(m/z) (MAS-977): fewer, merged lines where the file reads one."""
    await score_seeds(SAMPLE, {TARGET}, OrbiMatchParams())
    fine = len(stubbed[-1])

    async def resolution(sample):
        return _low_resolution

    monkeypatch.setattr(sample_inputs, "_resolution_of", resolution)
    await score_seeds(SAMPLE, {TARGET}, OrbiMatchParams())

    assert len(stubbed[-1]) < fine


async def test_a_runs_re_score_is_handed_the_runs_resolution(stubbed, monkeypatch):
    """A run reads its file's resolving power once and hands it on; the chain
    does not read it again."""
    await score_seeds(SAMPLE, {TARGET}, OrbiMatchParams(), resolution=_low_resolution)
    merged = len(stubbed[-1])

    async def unread(sample):
        raise AssertionError("the run's re-score read the resolution again")

    monkeypatch.setattr(sample_inputs, "_resolution_of", unread)
    await judging_loop._seeded_rescore(
        SAMPLE,
        OrbiMatchParams(),
        {TARGET},
        model=STAND_IN,
        resolution=_low_resolution,
    )

    assert len(stubbed[-1]) == merged
