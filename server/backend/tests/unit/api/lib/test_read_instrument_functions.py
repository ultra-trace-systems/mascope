"""Which resolution function a file's peaks are read by.

A file detected per scan stream has a function per stream as well, and a
read that names the polarity or the stream of its peaks gets the one the
peaks came from (``mascope_signal.instrument_func.streams``). One that names
neither, and any file whose store cannot say, gets the file's.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mascope_backend.api.new.instrument_configs import lib


CONFIG = SimpleNamespace(peakshape={"x": [0.0], "y": [1.0]}, resolution_function=[2e6])


def _patched(per_segment):
    return (
        patch.object(
            lib, "fetch_instrument_config_by_filename", AsyncMock(return_value=CONFIG)
        ),
        patch.object(lib.m_stream_functions, "peak_store_resolution", per_segment),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "asked", [{"polarity": "-"}, {"stream": "k"}, {"polarity": "-", "stream": "k"}]
)
async def test_peaks_of_a_polarity_or_a_stream_are_read_by_their_segments(asked):
    segments = MagicMock(name="by segment")
    per_segment = MagicMock(return_value=segments)
    fetch, store = _patched(per_segment)
    with fetch, store:
        peakshape, resolution = await lib.read_instrument_functions("f.raw", **asked)

    assert resolution is segments
    assert peakshape == CONFIG.peakshape
    (filename, fallback), kwargs = per_segment.call_args
    assert filename == "f.raw"
    assert fallback(200.0) == pytest.approx(2e6 / 200**0.5)
    assert kwargs == {"polarity": None, "stream": None, **asked}


@pytest.mark.asyncio
async def test_a_read_that_names_neither_is_the_files():
    per_segment = MagicMock()
    fetch, store = _patched(per_segment)
    with fetch, store:
        _, resolution = await lib.read_instrument_functions("f.raw")

    per_segment.assert_not_called()
    assert resolution(200.0) == pytest.approx(2e6 / 200**0.5)


@pytest.mark.asyncio
async def test_a_store_that_cannot_say_leaves_the_files():
    per_segment = MagicMock(side_effect=FileNotFoundError("no store"))
    fetch, store = _patched(per_segment)
    with fetch, store:
        _, resolution = await lib.read_instrument_functions("f.raw", polarity="-")

    assert resolution(200.0) == pytest.approx(2e6 / 200**0.5)
