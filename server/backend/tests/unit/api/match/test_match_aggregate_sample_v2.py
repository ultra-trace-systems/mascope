"""The per-sample targeted match (``aggregate_sample_match_compounds``) under
the v2 fit score: every ion of the sample scored in one ``ion_scores_v2``
pass, at the sample's fitted offset and width, the fit ``ion_score_v2`` gives
each ion alone. The default v1 score is the abundance-weighted sum it always
was.

The sample and the matcher are stubbed: the matched lines are made up from
the target isotopes the controller generates, each off by the sample's own
offset.
"""

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from mascope_backend.api.controllers.match.aggregate.sample import (
    match_aggregate_sample_controller as controller,
)
from mascope_backend.api.controllers.match.lib.match_score_v2 import ion_score_v2
from mascope_match.params import OrbiMatchParams
from mascope_tools.composition.mass_accuracy import fit_sample_mass_accuracy


OFFSET_PPM = 1.4

FORMULAS = [
    "C3H6O3",
    "C2H2O4",
    "C4H6O5",
    "C5H8O4",
    "C6H8O7",
    "C7H6O3",
    "C8H10O2",
    "C10H16O4",
    "HNO3",
    "C4H4O4",
]


@pytest.fixture
def stubbed(monkeypatch):
    rng = np.random.default_rng(3)

    async def sample(sample_item_id):
        return SimpleNamespace(
            filename="sample.raw",
            polarity="-",
            instrument="instrument X",
            sample_item_name="sample",
        )

    async def match(*, filename, target_isotopes_df, match_params, polarity):
        frame = target_isotopes_df.copy()
        lines = len(frame)
        found = rng.random(lines) < 0.85
        frame["match_mz_error"] = OFFSET_PPM + rng.normal(0, 0.4, lines)
        frame["sample_peak_intensity"] = np.where(
            found, rng.uniform(1e3, 1e6, lines), 0.0
        )
        frame["sample_peak_mz"] = frame["mz"]
        frame["signal_to_noise"] = np.where(
            rng.random(lines) < 0.8, rng.uniform(3, 500, lines), np.nan
        )
        frame["match_score"] = rng.uniform(0, 1, lines)
        return frame

    monkeypatch.setattr(controller, "fetch_sample", sample)
    monkeypatch.setattr(controller, "compute_match_isotopes", match)
    monkeypatch.setattr(controller, "apply_match_params", lambda df, params: df)


async def _ions_and_isotopes(test_ionization_mechanisms):
    ids = [
        m.ionization_mechanism_id
        for m in test_ionization_mechanisms
        if m.ionization_mechanism in ("[M-H]-", "[M+NO3]-")
    ]
    result = await controller.aggregate_sample_match_compounds(
        sample_item_id="si-1",
        target_compound_formulas=FORMULAS,
        match_params=OrbiMatchParams(),
        ion_mechanism_ids=ids,
    )
    ions = [ion for compound in result["data"] for ion in compound["children"]]
    isotopes = pd.DataFrame([isotope for ion in ions for isotope in ion["children"]])
    return ions, isotopes


@pytest.mark.asyncio
async def test_v2_scores_every_ion_as_each_does_alone(
    stubbed, monkeypatch, test_ionization_mechanisms
):
    monkeypatch.setenv("MASCOPE_MATCH_SCORE_VERSION", "2")
    ions, isotopes = await _ions_and_isotopes(test_ionization_mechanisms)
    mu, sigma = fit_sample_mass_accuracy(isotopes)

    # The premise: the sample has an offset of its own to read the ions at.
    assert len(ions) >= 10
    assert len(ions) == isotopes["target_ion_id"].nunique()
    assert mu == pytest.approx(OFFSET_PPM, abs=0.3)
    for ion in ions:
        lines = isotopes[isotopes["target_ion_id"] == ion["target_ion_id"]]
        alone = ion_score_v2(lines, sigma_ppm=sigma, mu=mu)
        assert ion["match_score"] == pytest.approx(alone, abs=1e-12)


@pytest.mark.asyncio
async def test_v1_scores_are_the_weighted_sum(
    stubbed, monkeypatch, test_ionization_mechanisms
):
    monkeypatch.delenv("MASCOPE_MATCH_SCORE_VERSION", raising=False)
    ions, isotopes = await _ions_and_isotopes(test_ionization_mechanisms)

    for ion in ions:
        lines = isotopes[isotopes["target_ion_id"] == ion["target_ion_id"]]
        weighted = (lines["match_score"] * lines["relative_abundance"]).sum()
        assert ion["match_score"] == pytest.approx(weighted, abs=1e-12)
