"""The width of a file's lines at the reference m/z, as a sample serves it.

Read the way the assignment engine reads the file's resolution: by the
sample's polarity, so a file detected per scan stream answers with the
stream that owns the m/z. A function that cannot be read, or that says
nothing usable there, leaves the width null rather than failing the record.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

from mascope_backend.api.controllers.samples import samples_controller
from mascope_backend.api.new.instrument_configs import lib


def _reads(resolution):
    return patch.object(
        lib,
        "read_instrument_functions",
        AsyncMock(return_value=({}, resolution)),
    )


@pytest.mark.asyncio
async def test_the_width_is_read_by_the_samples_polarity_at_the_reference_mz():
    asked = []

    def resolution(mz):
        asked.append(np.asarray(mz).tolist())
        return np.asarray(mz) * 100.0

    with _reads(resolution) as read:
        width = await lib.file_line_width("f.raw", polarity="-")

    read.assert_awaited_once_with("f.raw", polarity="-")
    assert asked == [[200.0]]
    assert width == {
        "reference_mz": 200.0,
        "resolution": pytest.approx(20000.0),
        "fwhm_ppm": pytest.approx(50.0),
    }


@pytest.mark.asyncio
async def test_the_width_is_read_at_the_mz_asked():
    """By a function that answers a scalar, as the file's own may."""
    with _reads(lambda mz: float(np.asarray(mz).ravel()[0]) * 100.0):
        width = await lib.file_line_width("f.raw", mz=300.0)

    assert width["reference_mz"] == 300.0
    assert width["resolution"] == pytest.approx(30000.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [0.0, -5.0, float("nan"), float("inf")])
async def test_a_width_the_function_cannot_say_is_null(value):
    with _reads(lambda mz: value):
        width = await lib.file_line_width("f.raw")

    assert width == {"reference_mz": 200.0, "resolution": None, "fwhm_ppm": None}


@pytest.mark.asyncio
async def test_a_file_without_functions_has_no_width():
    with patch.object(
        lib,
        "read_instrument_functions",
        AsyncMock(side_effect=ValueError("Instrument configuration not found")),
    ):
        width = await lib.file_line_width("f.raw")

    assert width == {"reference_mz": 200.0, "resolution": None, "fwhm_ppm": None}


@pytest.mark.asyncio
async def test_a_sample_reads_its_files_width_by_its_own_polarity():
    sample = SimpleNamespace(filename="lworbi_x", polarity="+")
    width = {"reference_mz": 200.0, "resolution": 1.0, "fwhm_ppm": 1e6}
    with (
        patch.object(
            samples_controller, "file_line_width", AsyncMock(return_value=width)
        ) as read,
        patch.object(samples_controller, "get_instrument_type", return_value="orbi"),
    ):
        answer = await samples_controller.sample_line_width(sample)

    read.assert_awaited_once_with("lworbi_x", polarity="+")
    assert answer == {"instrument_class": "orbi", **width}
