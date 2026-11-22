"""The composition fit (the chart's and the API's) predicts a composition's lines
as a run reads them: at the sample file's R(m/z) (MAS-977)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from mascope_backend.api.new.peak_assignments import sample_inputs, visualization
from mascope_backend.db import IonizationMechanism
from mascope_match.params import TofMatchParams


pytestmark = pytest.mark.asyncio

SAMPLE = SimpleNamespace(
    sample_item_id="si-1",
    sample_item_name="Sample One",
    filename="sample.h5",
    polarity="-",
)
MECHANISM = IonizationMechanism(
    ionization_mechanism_id="mech-h",
    ionization_mechanism="[M-H]-",
    ionization_mechanism_polarity="-",
)


async def _lines(resolution) -> pd.DataFrame:
    """The lines the composition fit hands the matcher for C10H15NO3 on a TOF
    file of this resolving power."""
    session = MagicMock()
    session.get = AsyncMock(return_value=MECHANISM)
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=False)
    seen = []

    async def match(filename, target_isotopes_df, match_params, polarity):
        seen.append(target_isotopes_df)
        return target_isotopes_df

    with (
        patch.object(visualization, "async_session", return_value=context),
        patch.object(visualization, "get_instrument_type", return_value="tof"),
        patch.object(visualization, "compute_match_isotopes", side_effect=match),
        patch.object(
            sample_inputs, "_resolution_of", AsyncMock(return_value=resolution)
        ),
    ):
        await visualization._composition_match_isotopes(
            SAMPLE, "C10H15NO3", "mech-h", TofMatchParams()
        )
    (lines,) = seen
    return lines


async def test_the_lines_are_the_files():
    # About 40000 at m/z 197: the 15N line, 6.3 mDa under the 13C one, is a
    # peak of its own, where the stored library's 10000 merges the two.
    at_file = await _lines(lambda mz: 200.0 * mz)
    unread = await _lines(None)

    assert set(at_file["resolution"]) == set(unread["resolution"]) == {"LOW"}
    assert len(at_file) > len(unread)
    assert any(
        "[15N]" == name.split("C")[0] for name in at_file["target_isotope_formula"]
    )
