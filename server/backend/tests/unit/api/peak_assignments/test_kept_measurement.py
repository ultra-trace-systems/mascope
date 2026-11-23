"""Unit tests for what a pass of the batch stages keeps of a sample's
measurement between two judgings of it (MAS-1073).

A judging scores only the readings the pass has not measured at the same
inputs, and puts the rest back from what an earlier judging scored, in the
order the scorer builds its frame; a listed reading is measured again when the
claims it is measured without move; a sample whose peaks, match parameters or
model moved is measured whole; and so is one whose model no record held,
which keeps nothing. With the real seeded chain, the frame put together from
two scorings is the frame one scoring of every reading gives. See
``kept_measurement.py``.
"""

from collections import Counter
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from mascope_backend.api.new.peak_assignments import (
    model_readers,
    sample_inputs,
    seeded_scoring,
)
from mascope_backend.api.new.peak_assignments.kept_measurement import (
    KeptMeasurement,
    score_kept,
)
from mascope_backend.api.new.peak_assignments.seeded_scoring import (
    score_seeds,
    scored_maps,
)
from mascope_match.params import OrbiMatchParams
from mascope_tools.composition.custom_elements import labelled_purity
from mascope_tools.composition.mass_accuracy import SampleMassAccuracy
from mascope_tools.composition.measurement_model import MeasurementModel
from mascope_tools.composition.profiles import INSTRUMENT_FALLBACK_SIGMA_PPM


pytestmark = pytest.mark.asyncio

MECH = "mech-h"
A, B, C = ("C10H18O3", MECH), ("C8H14O4", MECH), ("C12H22O2", MECH)
MODEL = MeasurementModel.v1(SampleMassAccuracy(), INSTRUMENT_FALLBACK_SIGMA_PPM["orbi"])
PEAKS = pd.DataFrame(
    {"sample_peak_id": ["p1", "p2"], "mz": [100.0, 200.0], "intensity": [1.0, 2.0]}
)


class Scorer:
    """A stand-in scorer: each seed's ion two lines on two peaks named after
    it, at a fit its formula gives; the seeds and listed seeds each call was
    asked for, in order."""

    def __init__(self):
        self.calls: list[set] = []
        self.listed: list[set] = []
        self.claimed: list = []
        self.minted = 0

    async def __call__(self, sample, seeds, match_params, **kwargs):
        self.calls.append(set(seeds))
        self.listed.append(set(kwargs["listed_seeds"]))
        self.claimed.append(kwargs["claimed_peak_ids"])
        ion_by_seed, rows = {}, []
        for formula, mechanism_id in sorted(seeds):
            self.minted += 1
            ion = f"ion-{self.minted}"
            ion_by_seed[(formula, mechanism_id)] = ion
            for line, abundance in enumerate((1.0, 0.1)):
                rows.append(
                    {
                        "target_ion_id": ion,
                        "target_compound_formula": formula,
                        "ionization_mechanism_id": mechanism_id,
                        "sample_peak_id": f"{formula}-{line}",
                        "relative_abundance": abundance,
                        "match_score": len(formula) / 10,
                        "match_mz_error": 0.1 * line,
                    }
                )
        frame = pd.DataFrame(rows)
        fit_by_ion, errors = scored_maps(frame)
        return ion_by_seed, fit_by_ion, errors, frame


def _ctx(kept, seeds, *, model=MODEL, recorded=True, claimed=(), peaks=PEAKS):
    return SimpleNamespace(
        kept=kept,
        sample=SimpleNamespace(sample_item_id="si-1"),
        seeds=set(seeds),
        match_params=OrbiMatchParams(),
        model=model,
        model_recorded=recorded,
        peaks_df=peaks,
        resolution=None,
        claimed_peak_ids=frozenset(claimed),
    )


def _readings(frame: pd.DataFrame) -> list[tuple]:
    """The frame's rows as what they read, without the synthetic ion ids."""
    return list(
        frame[["target_compound_formula", "sample_peak_id", "match_score"]].itertuples(
            index=False, name=None
        )
    )


async def test_a_judging_scores_only_the_readings_the_pass_has_not_measured():
    kept, scorer = KeptMeasurement(), Scorer()
    await score_kept(_ctx(kept, {A, B}), scorer, set())
    ion_by_seed, fit_by_ion, errors, frame = await score_kept(
        _ctx(kept, {A, B, C}), scorer, set()
    )

    assert scorer.calls == [{A, B}, {C}]
    assert kept.counts["si-1"] == Counter(readings_scored=1, readings_kept=2)
    # The whole answer, as one scoring of the three gives it.
    _, whole_fits, whole_errors, whole = await Scorer()(
        None, {A, B, C}, None, listed_seeds=set(), claimed_peak_ids=frozenset()
    )
    assert _readings(frame) == _readings(whole)
    assert set(ion_by_seed) == {A, B, C}
    assert sorted(fit_by_ion.values()) == sorted(whole_fits.values())
    assert len(errors) == len(whole_errors)
    assert fit_by_ion[ion_by_seed[A]] == pytest.approx(0.8)


async def test_the_frame_is_put_back_in_the_order_the_scorer_builds_it():
    """Formula by formula, whichever judging measured which reading."""
    kept, scorer = KeptMeasurement(), Scorer()
    await score_kept(_ctx(kept, {C}), scorer, set())
    await score_kept(_ctx(kept, {A}), scorer, set())
    _, _, _, frame = await score_kept(_ctx(kept, {A, B, C}), scorer, set())
    formulas = frame["target_compound_formula"].tolist()
    assert formulas == sorted(formulas)
    assert list(frame.index) == list(range(len(frame)))


async def test_a_reading_left_out_is_left_out_of_the_frame():
    kept, scorer = KeptMeasurement(), Scorer()
    await score_kept(_ctx(kept, {A, B, C}), scorer, set())
    ion_by_seed, _, _, frame = await score_kept(_ctx(kept, {B}), scorer, set())
    assert scorer.calls == [{A, B, C}]
    assert set(ion_by_seed) == {B}
    assert set(frame["target_compound_formula"]) == {B[0]}


async def test_a_listed_reading_is_measured_again_when_the_claims_move():
    """A reading the lists hold is measured without the claimed peaks; one
    they do not hold keeps its lines on them as context, whatever is claimed."""
    kept, scorer = KeptMeasurement(), Scorer()
    await score_kept(_ctx(kept, {A, B}, claimed={"x"}), scorer, {A})
    await score_kept(_ctx(kept, {A, B}, claimed={"x"}), scorer, {A})
    await score_kept(_ctx(kept, {A, B}, claimed={"x", "y"}), scorer, {A})
    assert scorer.calls == [{A, B}, {A}]
    assert scorer.listed == [{A}, {A}]
    assert scorer.claimed[-1] == frozenset({"x", "y"})
    assert kept.counts["si-1"] == Counter(readings_scored=1, readings_kept=1)


@pytest.mark.parametrize(
    "moved",
    [
        {"model": MeasurementModel.v1(SampleMassAccuracy(), 3.0)},
        {"peaks": PEAKS.assign(mz=[100.0, 200.5])},
    ],
    ids=["model", "peaks"],
)
async def test_a_sample_measured_at_other_inputs_is_measured_whole(moved):
    kept, scorer = KeptMeasurement(), Scorer()
    await score_kept(_ctx(kept, {A, B}), scorer, set())
    await score_kept(_ctx(kept, {A, B}, **moved), scorer, set())
    assert scorer.calls == [{A, B}, {A, B}]


async def test_other_match_parameters_measure_the_sample_whole():
    kept, scorer = KeptMeasurement(), Scorer()
    await score_kept(_ctx(kept, {A}), scorer, set())
    ctx = _ctx(kept, {A})
    ctx.match_params = OrbiMatchParams(mz_tolerance=OrbiMatchParams().mz_tolerance + 1)
    await score_kept(ctx, scorer, set())
    assert scorer.calls == [{A}, {A}]


async def test_another_labelled_purity_measures_the_sample_whole():
    """A labelled ion's lines are built at the purity the sample's setup
    carries, which the stage list sets around the pass."""
    kept, scorer = KeptMeasurement(), Scorer()
    await score_kept(_ctx(kept, {A}), scorer, set())
    with labelled_purity(0.9):
        await score_kept(_ctx(kept, {A}), scorer, set())
    assert scorer.calls == [{A}, {A}]


async def test_a_sample_whose_model_no_record_held_keeps_nothing():
    """A stand-in model its refit refits (MAS-1071): every reading is measured
    every time, and the judging says the sample had nothing to keep."""
    kept, scorer = KeptMeasurement(), Scorer()
    await score_kept(_ctx(kept, {A, B}), scorer, set())
    await score_kept(_ctx(kept, {A, B}, recorded=False), scorer, set())
    await score_kept(_ctx(kept, {A, B}, recorded=False), scorer, set())
    assert scorer.calls == [{A, B}, {A, B}, {A, B}]
    assert "si-1" not in kept.samples
    assert kept.counts["si-1"] == Counter(samples_unrecorded=1)


async def test_without_a_store_every_reading_is_scored():
    scorer = Scorer()
    await score_kept(_ctx(None, {A, B}), scorer, {A})
    await score_kept(_ctx(None, {A, B}), scorer, {A})
    assert scorer.calls == [{A, B}, {A, B}]
    assert scorer.listed == [{A}, {A}]


async def test_a_reading_that_scores_nothing_is_kept_as_nothing():
    kept = KeptMeasurement()

    async def nothing(sample, seeds, match_params, **kwargs):
        nothing.calls.append(set(seeds))
        return {}, {}, {}, pd.DataFrame()

    nothing.calls = []
    first = await score_kept(_ctx(kept, {A}), nothing, set())
    again = await score_kept(_ctx(kept, {A}), nothing, set())
    assert nothing.calls == [{A}]
    assert first[3].empty and again[3].empty
    assert again[0] == {}


# --- the real seeded chain ------------------------------------------------------


@pytest.fixture
def chain(monkeypatch):
    """The seeded chain with only the matcher, the mechanism read and the
    resolving power stood in for: each line of 1 % and up found on a peak
    named after it, a fixed error off; the rest absent."""

    async def match(filename, target_isotopes_df, polarity):
        frame = target_isotopes_df.copy().reset_index(drop=True)
        found = frame["relative_abundance"] >= 0.01
        frame["sample_peak_id"] = [
            f"{c}|{i}" if hit else None
            for c, i, hit in zip(
                frame["target_compound_formula"],
                frame["target_isotope_formula"],
                found,
                strict=True,
            )
        ]
        frame["sample_peak_mz"] = np.where(found, frame["mz"], np.nan)
        frame["sample_peak_intensity"] = np.where(
            found, frame["relative_abundance"] * 1e7, 0.0
        )
        frame["match_mz_error"] = np.where(found, 0.4, np.nan)
        frame["match_abundance_error"] = np.where(found, 0.0, np.nan)
        frame["match_score"] = np.where(found, 1.0, 0.0)
        return frame

    async def mechanisms(mechanism_ids):
        return {
            MECH: SimpleNamespace(
                ionization_mechanism_id=MECH,
                ionization_mechanism="[M+H]+",
                ionization_mechanism_polarity="+",
            )
        }

    async def no_purity(sample_item_id):
        return None

    async def none(sample):
        return None

    monkeypatch.setattr(seeded_scoring, "compute_match_isotopes", match)
    monkeypatch.setattr(seeded_scoring, "fetch_mechanisms_by_id", mechanisms)
    monkeypatch.setattr(seeded_scoring, "sample_label_purity", no_purity)
    monkeypatch.setattr(sample_inputs, "_resolution_of", none)
    monkeypatch.setattr(sample_inputs, "get_instrument_type", lambda name: "orbi")
    monkeypatch.setattr(model_readers, "recorded_model", None)


def _measured(frame: pd.DataFrame) -> pd.DataFrame:
    """What a scored frame measured, row by row, without its synthetic ids
    (the scorer's own index has gaps where it dropped an ion)."""
    ids = ["target_ion_id", "target_isotope_id", "match_isotope_id"]
    return frame.drop(columns=[c for c in ids if c in frame.columns]).reset_index(
        drop=True
    )


async def test_on_the_real_chain_the_pieces_are_one_scoring_of_every_reading(chain):
    """Each ion is measured alone - matched line by line, gated on its own
    heights, scored at a model the frame is never fitted on - so two
    scorings put together are one scoring of every reading, row for row."""
    sample = SimpleNamespace(sample_item_id="si-1", filename="s.raw", polarity="+")
    claimed = frozenset({f"{A[0]}|C10H19O3+"})
    whole_ions, whole_fits, _, whole = await score_seeds(
        sample,
        {A, B, C},
        OrbiMatchParams(),
        model=MODEL,
        resolution=None,
        claimed_peak_ids=claimed,
        listed_seeds={A},
    )
    kept = KeptMeasurement()
    ctx = _ctx(kept, {A, C}, claimed=claimed)
    ctx.sample = sample
    await score_kept(ctx, score_seeds, {A})
    ctx.seeds = {A, B, C}
    ions, fits, _, frame = await score_kept(ctx, score_seeds, {A})

    assert kept.counts["si-1"] == Counter(readings_scored=1, readings_kept=2)
    assert not whole.empty
    pd.testing.assert_frame_equal(_measured(frame), _measured(whole))
    assert set(ions) == set(whole_ions)
    for seed in whole_ions:
        assert fits.get(ions[seed]) == whole_fits.get(whole_ions[seed])
