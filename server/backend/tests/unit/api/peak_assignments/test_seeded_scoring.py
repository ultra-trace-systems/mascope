"""Unit tests for the width and offset the seeded scoring chain scores at.

The chain fits a sample's width and offset off the seeded frame itself, which a
short seed list - a pin, the inspector's evidence for one family - cannot do.
Such a list is scored at the instrument class's width, the one Stage A and the
untargeted search stand in with, so one ion in one sample reads alike alone and
among many seeds. Nor can it fit an offset, and it is centred where the
sample's reagent lines put the axis, as Stage A centres one. The matcher, the
mechanism read and the reagent pre-pass are stubbed; the ions, the gating and
the fit are the real ones. See ``seeded_scoring.py``.
"""

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from mascope_backend.api.new.peak_assignments import seeded_scoring, service
from mascope_backend.api.new.peak_assignments.engine import (
    ReagentOffset,
    score_ions_by_fit,
)
from mascope_backend.api.new.peak_assignments.seeded_scoring import score_seeds
from mascope_match.params import OrbiMatchParams
from mascope_tools.composition.mass_accuracy import fit_sample_mass_accuracy
from mascope_tools.composition.profiles import INSTRUMENT_FALLBACK_SIGMA_PPM


pytestmark = pytest.mark.asyncio

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
    target_isotopes_df: pd.DataFrame, target_error_ppm: float = TARGET_ERROR_PPM
) -> pd.DataFrame:
    """The lines a spectrum shows found at their predicted height, the target's
    ``target_error_ppm`` off and the others' spread about zero; the rest
    absent."""
    frame = target_isotopes_df.copy().reset_index(drop=True)
    found = frame["relative_abundance"] >= FOUND_ABUNDANCE
    target = frame["target_compound_formula"] == TARGET[0]
    others = frame.index[found & ~target]
    pattern = [-SPREAD_PPM, 0.0, SPREAD_PPM]
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


@pytest.fixture
def reagent(monkeypatch):
    """The sample's reagent pre-pass, as ``score_seeds`` resolves it: none by
    default; set ``offset`` to give the sample's lines one. ``calls`` records
    the samples it was resolved for."""
    state = SimpleNamespace(offset=None, calls=[])

    async def sample_reagent_offset(sample):
        state.calls.append(sample)
        return state.offset

    monkeypatch.setattr(service, "sample_reagent_offset", sample_reagent_offset)
    return state


class _Frames(list):
    """The frames the stubbed matcher returned, and the target's lines' error
    it places them at, which a test moves."""

    target_error_ppm = TARGET_ERROR_PPM


@pytest.fixture
def stubbed(monkeypatch, reagent):
    frames = _Frames()

    async def mechanisms(mechanism_ids):
        return {
            MECH: SimpleNamespace(
                ionization_mechanism_id=MECH,
                ionization_mechanism="[M+H]+",
                ionization_mechanism_polarity="+",
            )
        }

    async def match(filename, target_isotopes_df, polarity):
        frames.append(_matched(target_isotopes_df, frames.target_error_ppm))
        return frames[-1]

    monkeypatch.setattr(seeded_scoring, "fetch_mechanisms_by_id", mechanisms)
    monkeypatch.setattr(seeded_scoring, "compute_match_isotopes", match)
    monkeypatch.setattr(seeded_scoring, "get_instrument_type", lambda name: "orbi")
    return frames


SAMPLE = SimpleNamespace(filename="sample.raw", polarity="+")


async def _fit(seeds, **kwargs):
    ion_by_seed, fit_by_ion, _errors, _scored = await score_seeds(
        SAMPLE, seeds, OrbiMatchParams(), **kwargs
    )
    return fit_by_ion[ion_by_seed[TARGET]]


async def test_a_lone_seed_scores_as_it_does_among_twenty_others(stubbed):
    lone = await _fit({TARGET})
    crowd = await _fit({TARGET} | OTHERS)

    # The premise: one ion's three lines fit no width, twenty-one ions' do,
    # and the crowd's is the class's own at no offset - so the two can only
    # agree if the lone seed is scored at the class's width too.
    assert fit_sample_mass_accuracy(stubbed[0]) == (None, None)
    mu, sigma = fit_sample_mass_accuracy(stubbed[-1])
    assert mu == pytest.approx(0.0, abs=1e-9)
    assert sigma == pytest.approx(ORBI_WIDTH, abs=1e-6)
    assert lone == pytest.approx(crowd, abs=0.01)


async def test_the_generic_width_would_read_the_lone_seed_otherwise(stubbed):
    """Why the width is passed: scored without the class's width, the lone seed
    is judged at the score's generic 2 ppm, where 0.5 ppm off reads as near
    perfect."""
    lone = await _fit({TARGET})
    generic = score_ions_by_fit(stubbed[-1])["match_score"].iloc[0]
    assert generic - lone > 0.05


async def test_a_lone_seed_is_centred_where_the_reagent_lines_put_the_axis(
    stubbed, reagent
):
    """A run of the sample centres a frame too short to fit an offset on its
    reagent lines where they are beyond the width; the seeded readers - a pin,
    the inspector, the batch ledger - read the same ion alike."""
    stubbed.target_error_ppm = 0.0
    centred = await _fit({TARGET})
    stubbed.target_error_ppm = AXIS_PPM
    uncorrected = await _fit({TARGET})
    reagent.offset = ReagentOffset(mu_ppm=AXIS_PPM, lines=7, beyond_width=True)
    corrected = await _fit({TARGET})

    assert reagent.calls == [SAMPLE] * 3
    assert fit_sample_mass_accuracy(stubbed[-1]) == (None, None)
    assert corrected == pytest.approx(centred, abs=0.01)
    # Uncorrected, 2.2 widths off reads as a much worse ion.
    assert corrected - uncorrected > 0.1


async def test_an_offset_inside_the_width_is_not_taken(stubbed, reagent):
    """As in Stage A: lines that put the axis inside the width say nothing the
    score does not already allow for."""
    stubbed.target_error_ppm = AXIS_PPM
    uncorrected = await _fit({TARGET})
    reagent.offset = ReagentOffset(mu_ppm=AXIS_PPM, lines=7, beyond_width=False)

    assert await _fit({TARGET}) == pytest.approx(uncorrected)


async def test_a_runs_own_offset_is_used_and_none_is_resolved(stubbed, reagent):
    """A caller inside a run hands over its pre-pass's offset, None included
    (a profile with no reagent library); the sample's is then not read
    again."""
    stubbed.target_error_ppm = AXIS_PPM
    reagent.offset = ReagentOffset(mu_ppm=AXIS_PPM, lines=7, beyond_width=True)
    resolved = await _fit({TARGET})
    reagent.calls.clear()

    handed = await _fit(
        {TARGET},
        reagent_offset=ReagentOffset(mu_ppm=AXIS_PPM, lines=7, beyond_width=True),
    )
    no_library = await _fit({TARGET}, reagent_offset=None)

    assert reagent.calls == []
    assert handed == pytest.approx(resolved)
    assert resolved - no_library > 0.1


async def test_a_runs_seeded_re_score_is_handed_the_runs_offset(stubbed, reagent):
    """The untargeted stage's commits are measured again through this chain
    (``service._seeded_fits``) at the offset the run's pre-pass read, the one
    Stage A was scored at, and the sample's lines are not claimed twice."""
    stubbed.target_error_ppm = AXIS_PPM
    offset = ReagentOffset(mu_ppm=AXIS_PPM, lines=7, beyond_width=True)

    fits = await service._seeded_fits(
        SAMPLE, OrbiMatchParams(), {TARGET}, reagent_offset=offset
    )

    assert reagent.calls == []
    assert fits[TARGET] == pytest.approx(await _fit({TARGET}, reagent_offset=offset))
    assert fits[TARGET] - await _fit({TARGET}, reagent_offset=None) > 0.1
