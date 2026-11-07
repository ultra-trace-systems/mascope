"""The calibrant cap the mass gate reads the source's own lines by.

The m/z calibration node leaves a calibrant brighter than its cap out of its
fit; the assignment engine's mass gate leaves such a source line out of its
anchors, by the cap of the file's instrument class, held against the mean
ion current per scan of the sample's polarity. The gate's side is pinned in
``test_mass_gate.py``.
"""

from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from mascope_backend.api.models.calibration.config import calibration_config
from mascope_backend.api.new.peak_assignments import service
from mascope_backend.api.new.peak_assignments.mass_gate import SourceLineCap


SAMPLE = SimpleNamespace(
    filename="AnyFile_2026.10.20_12h00m00s_x",
    polarity="-",
    sample_item_name="sample",
)


async def _cap(instrument_type="orbi", tic=(800.0, 1200.0), raises=None):
    def instrument(_filename):
        if isinstance(instrument_type, Exception):
            raise instrument_type
        return instrument_type

    def tic_read(filename, polarity=None):
        if raises is not None:
            raise raises
        return np.arange(len(tic)), np.asarray(tic)

    with (
        patch.object(service, "get_instrument_type", instrument),
        patch.object(service, "get_tic_per_scan", side_effect=tic_read) as read,
    ):
        cap = await service._source_line_cap(SAMPLE)
    return cap, read


@pytest.mark.asyncio
async def test_an_orbitrap_samples_lines_are_held_to_the_orbitrap_cap():
    cap, read = await _cap()

    assert cap == SourceLineCap(
        tic=1000.0,
        tic_share_max=calibration_config.ORBI_CALIBRANT_TIC_SHARE_MAX,
        peak_intensity_max=None,
    )
    read.assert_called_once_with(SAMPLE.filename, polarity="-")


@pytest.mark.asyncio
async def test_a_tof_samples_lines_are_not_capped():
    cap, read = await _cap("tof")

    assert cap is None
    read.assert_not_called()


@pytest.mark.asyncio
async def test_a_sample_of_no_known_instrument_is_not_capped():
    cap, _ = await _cap(ValueError("no data file"))

    assert cap is None


@pytest.mark.asyncio
async def test_a_sample_whose_ion_current_cannot_be_read_is_not_capped():
    cap, _ = await _cap(raises=FileNotFoundError("signal.zarr"))

    assert cap is None
