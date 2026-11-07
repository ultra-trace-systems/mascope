"""A calibrant's share of the TIC does not shrink with the file's length.

A calibrant's intensity is the mean over the scans it was read in, so its
share is of the mean TIC per scan; against their sum, a file of twice the
scans read half the share (GitHub #2845). A TOF peak's area is its bins'
ion rate summed times the sample interval in ns, so on TOF the TIC takes
the interval on too and the share is a fraction of the ions.

The per-stream fit's shares and its summary are pinned in
``tests/unit/libraries/signal/test_calibration_per_stream.py``.
"""

import json
import os
from unittest.mock import AsyncMock, patch

import numpy as np
import pandas as pd
import pytest
import tof_audit_support as support

import mascope_file.name as m_name
from mascope_backend.api.controllers.calibration.lib.calibration_mz_fit import (
    OrbiCalibrationHandler,
    TofCalibrationHandler,
)
from mascope_backend.api.models.calibration.calibration_pydantic_model import (
    CalibrationFitParams,
    OrbiCalibrationParams,
)
from mascope_signal.peak import DEFAULT_SAMPLE_INTERVAL_NS


NODE = "mascope_backend.api.controllers.calibration.lib.calibration_mz_fit"

#: Every scan's TIC, whatever the file's length
TIC_PER_SCAN = 50.0
AREA = 100.0
#: A file whose props hold no sample interval at all
UNRECORDED = object()


@pytest.fixture
def filestore(tmp_path, monkeypatch):
    monkeypatch.setattr(m_name.runtime.meta, "filestore", str(tmp_path))
    monkeypatch.setattr(m_name, "_INSTRUMENT_TYPE", {})
    return tmp_path


def _tic(n_scans):
    return patch(
        f"{NODE}.m_compute.get_tic_per_scan",
        return_value=(np.arange(n_scans), np.full(n_scans, TIC_PER_SCAN)),
    )


def _set_sample_interval(root, filename, interval):
    path = os.path.join(
        root,
        m_name.get_instrument_name(filename),
        m_name.timestamp_from_filename(filename).strftime("%Y.%m.%d"),
        filename,
        ".props",
    )
    with open(path) as props:
        recorded = json.load(props)
    with open(path, "w") as props:
        json.dump({**recorded, "sample_interval": interval}, props)


async def _tof_shares(filestore, monkeypatch, n_scans, sample_interval=UNRECORDED):
    monkeypatch.setattr(support, "N_SCANS", n_scans)
    filename = support.write_file(
        filestore,
        [{**peak, "area": AREA} for peak in support.calibrant_peaks()],
        filename=f"AuditTof_2026.10.20_12h00m00s_scans{n_scans}",
    )
    if sample_interval is not UNRECORDED:
        _set_sample_interval(filestore, filename, sample_interval)
    handler = TofCalibrationHandler(filename, support.params())
    with (
        patch.object(
            TofCalibrationHandler,
            "_resolve_calibration_isotopes",
            AsyncMock(return_value=support.isotopes().drop(columns="ion_label")),
        ),
        patch(
            f"{NODE}.read_instrument_functions",
            AsyncMock(return_value=({}, support.resolution)),
        ),
        _tic(n_scans),
    ):
        await handler.fit()
    assert handler.fit_result is not None
    *points, summary = handler.stats
    return [point["calibrant_to_tic"] for point in points], summary["calibrant_to_tic"]


@pytest.mark.asyncio
async def test_a_tof_file_of_twice_the_scans_has_the_same_share(filestore, monkeypatch):
    short, short_total = await _tof_shares(filestore, monkeypatch, 4)
    long, long_total = await _tof_shares(filestore, monkeypatch, 8)

    assert long == pytest.approx(short, rel=1e-12)
    assert long_total == pytest.approx(short_total, rel=1e-12)
    # A fraction of the ions: the area over the interval is the bins' sum
    assert short == pytest.approx(
        [AREA / DEFAULT_SAMPLE_INTERVAL_NS / TIC_PER_SCAN] * len(short), rel=1e-12
    )
    assert short_total == pytest.approx(sum(short), rel=1e-12)


@pytest.mark.asyncio
async def test_a_tof_share_takes_the_sample_interval_the_file_records(
    filestore, monkeypatch
):
    shares, _ = await _tof_shares(filestore, monkeypatch, 4, sample_interval=0.5)

    assert shares == pytest.approx([AREA / 0.5 / TIC_PER_SCAN] * len(shares))


@pytest.mark.asyncio
async def test_a_tof_file_that_records_none_is_not_scaled(filestore, monkeypatch):
    """Its areas were integrated in m/z, which no interval makes a bin sum."""
    shares, _ = await _tof_shares(filestore, monkeypatch, 4, sample_interval=None)

    assert shares == pytest.approx([AREA / TIC_PER_SCAN] * len(shares))


def _orbi_matches():
    return pd.DataFrame(
        {
            "target_ion_id": ["a", "b", "c"],
            "mz": [100.0, 200.0, 300.0],
            "sample_peak_mz": [100.0, 200.0, 300.0],
            "sample_peak_intensity": [30.0, 20.0, 10.0],
        }
    )


async def _orbi_shares(n_scans):
    params = CalibrationFitParams(
        calibration_collection_id="tc-1",
        ionization_mechanism_ids=["im-1"],
        polarity="-",
        # Shares of a few lines alone, far over any cap on them
        **{**OrbiCalibrationParams().model_dump(), "calibrant_tic_share_max": None},
    )
    handler = OrbiCalibrationHandler("OrbiFile_2026.10.20_12h00m00s_x", params)
    matches = _orbi_matches()

    def kept_as_they_are(matches_df):
        return {"par": {"old_factor": 1.0}}, matches_df.assign(
            match_mz_error=0.0, calibration_mz_error=0.0
        )

    with (
        patch.object(
            OrbiCalibrationHandler, "_has_peaks", new_callable=lambda: property(bool)
        ),
        patch.object(
            OrbiCalibrationHandler,
            "_resolve_calibration_isotopes",
            AsyncMock(return_value=matches),
        ),
        patch.object(OrbiCalibrationHandler, "_polarity_streams", lambda self: None),
        patch.object(
            OrbiCalibrationHandler,
            "_match_calibration_compounds",
            AsyncMock(return_value=(matches, matches)),
        ),
        patch.object(
            OrbiCalibrationHandler,
            "_fit_retained_matches",
            lambda self, df: kept_as_they_are(df),
        ),
        patch.object(OrbiCalibrationHandler, "_send_progress", AsyncMock()),
        _tic(n_scans),
    ):
        await handler.fit()
    *points, summary = handler.stats
    return [point["calibrant_to_tic"] for point in points], summary["calibrant_to_tic"]


@pytest.mark.asyncio
async def test_an_orbitrap_file_of_twice_the_scans_has_the_same_share():
    short, short_total = await _orbi_shares(4)
    long, long_total = await _orbi_shares(8)

    assert long == pytest.approx(short, rel=1e-12)
    assert short == pytest.approx(
        [30 / TIC_PER_SCAN, 20 / TIC_PER_SCAN, 10 / TIC_PER_SCAN]
    )
    assert long_total == pytest.approx(short_total, rel=1e-12)
    assert short_total == pytest.approx(60 / TIC_PER_SCAN)
