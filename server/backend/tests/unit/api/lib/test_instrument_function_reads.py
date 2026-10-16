"""What each reader of a peak's width asks ``read_instrument_functions`` for.

A file detected per scan stream reads a peak's width from the stream it came
from, which the polarity of its peaks decides (or the one stream they are
of). That reaches a consumer only if it names the polarity or the stream:
batch alignment, the batch-peak fold and the assignment mass gate read by
the sample's polarity, calibration by the stream it calibrates. Each read is
spied on here, with everything around it patched.
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from mascope_backend.api.controllers.calibration.lib import calibration_mz_fit
from mascope_backend.api.controllers.calibration.lib.calibration_mz_fit import (
    OrbiCalibrationHandler,
)
from mascope_backend.api.controllers.sample.batches.lib import util as batch_util
from mascope_backend.api.models.calibration.calibration_pydantic_model import (
    CalibrationFitParams,
    OrbiCalibrationParams,
)
from mascope_backend.api.new.peak_assignments import (
    batch_peaks_controller,
    service,
)


STREAM = "FTMS - p NSI Full ms [66.0000-124.0000] R=120000"

#: A width per polarity, so which one a read got shows in what it returns
RESOLUTION = {"-": 1e5, "+": 2e5, None: 3e5}


def _spy():
    """A ``read_instrument_functions`` that records what it is asked, and
    answers with a resolution that says which polarity it was asked for."""
    asked = []

    async def read(filename, polarity=None, stream=None):
        asked.append((filename, polarity, stream))
        return None, lambda mz: np.full_like(
            np.asarray(mz, dtype=float), RESOLUTION[polarity]
        )

    return read, asked


def _session_of(execute):
    """``async_session`` yielding a session whose ``execute`` returns ``execute``."""
    session = MagicMock()
    session.execute = AsyncMock(return_value=execute)

    @asynccontextmanager
    async def async_session():
        yield session

    return async_session


@pytest.mark.asyncio
async def test_batch_alignment_reads_each_samples_widths_by_its_polarity():
    """One file measured in both polarities is two samples of the batch,
    each aligned by its own polarity's widths."""
    samples = [
        SimpleNamespace(filename="f.raw", polarity="-", ionization_mode_id="neg"),
        SimpleNamespace(filename="f.raw", polarity="+", ionization_mode_id="pos"),
    ]
    result = MagicMock()
    result.scalars.return_value.all.return_value = samples
    read, asked = _spy()

    def load(filename, polarity, intensity_variable):
        return np.array([100.0, 200.0]), np.ones(2), np.array(["a", "b"])

    with (
        patch.object(batch_util, "async_session", _session_of(result)),
        patch.object(batch_util, "read_instrument_functions", read),
        patch.object(batch_util, "_sync_load_peak_data", load),
        patch.object(batch_util.m_name, "get_instrument_type", return_value="orbi"),
    ):
        spectra, _ = await batch_util.collect_spectra_per_ionization_mode("batch-1")

    assert sorted(asked) == [("f.raw", "+", None), ("f.raw", "-", None)]
    np.testing.assert_allclose(spectra["neg"][0].resolution, RESOLUTION["-"])
    np.testing.assert_allclose(spectra["pos"][0].resolution, RESOLUTION["+"])


class _Stop(Exception):
    """Ends the fold once its width has been read."""


@pytest.mark.asyncio
async def test_the_batch_peak_fold_reads_the_samples_widths_by_its_polarity():
    sample = SimpleNamespace(
        sample_batch_id="batch-1",
        ionization_mode_id="pos",
        filename="f.raw",
        polarity="+",
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = sample
    read, asked = _spy()

    with (
        patch.object(batch_peaks_controller, "async_session", _session_of(result)),
        patch.object(batch_peaks_controller, "read_instrument_functions", read),
        patch.object(
            batch_peaks_controller,
            "_acquire_batch_fold_lock",
            AsyncMock(side_effect=_Stop),
        ),
        pytest.raises(_Stop),
    ):
        await batch_peaks_controller.fold_sample_into_batch_peaks(
            "sample-1", rows=[SimpleNamespace(mz_error_ppm=None)]
        )

    assert asked == [("f.raw", "+", None)]


@pytest.mark.asyncio
async def test_the_mass_gate_reads_the_samples_widths_by_its_polarity():
    sample = SimpleNamespace(filename="f.raw", polarity="-", sample_item_name="s")
    read, asked = _spy()

    with patch.object(service, "read_instrument_functions", read):
        resolution = await service._resolution_of(sample)

    assert asked == [("f.raw", "-", None)]
    assert resolution(200.0) == RESOLUTION["-"]


@pytest.mark.asyncio
async def test_calibration_sets_a_streams_candidates_apart_by_that_streams_widths():
    """Through the loader the fit calls, not the overlap filter alone."""
    read, asked = _spy()
    params = CalibrationFitParams(
        calibration_collection_id="tc-1",
        ionization_mechanism_ids=["im-1"],
        polarity="-",
        **OrbiCalibrationParams().model_dump(),
    )
    handler = OrbiCalibrationHandler("f.raw", params)
    handler._sync_load_and_filter_peaks = MagicMock(
        return_value=np.array([100.0, 200.0])
    )
    handler._load_peak_timeseries = AsyncMock(return_value="peaks")
    handler._drop_empty_peak_timeseries = MagicMock(side_effect=lambda peaks: peaks)
    handler._extract_intensity = MagicMock(side_effect=lambda peaks: peaks)

    with patch.object(calibration_mz_fit, "read_instrument_functions", read):
        await handler._load_and_filter_peaks(pd.Series([100.0, 200.0]), STREAM)

    assert asked == [("f.raw", "-", STREAM)]
