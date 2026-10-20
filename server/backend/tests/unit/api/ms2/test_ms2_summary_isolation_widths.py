"""
The MS2 summary of a file whose events use different isolation widths.

A targeted method sets the width per event or per inclusion-list entry, so a
file's MS2 scans need not share one. The summary used to refuse such a file
with a 400, though its centroids and timeseries answered; it now reports each
group's width and leaves the file's ``null``. A file none of whose MS2 scans
stores a width still has no summary, and the refusal says so in one sentence.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from mascope_backend.api.lib.exceptions.api_exceptions import ApiException
from mascope_backend.api.new.ms2 import service as ms2_service
from mascope_thermo.backend import _ms2_isolation_widths


_MS2 = "mascope_backend.api.new.ms2.service"

SAMPLE = SimpleNamespace(
    filename="OrbiTest_2026.01.01-12h00m00s",
    sample_item_name="2026-01-01 12:00:00",
    t0=None,
    t1=None,
    polarity="-",
)


async def _summary(get_ms2_summary):
    with (
        patch(f"{_MS2}.fetch_sample", AsyncMock(return_value=SAMPLE)),
        patch(f"{_MS2}.m_compute.get_ms2_summary", get_ms2_summary),
    ):
        return await ms2_service.get_ms2_summary("si-1")


@pytest.mark.asyncio
async def test_a_summary_without_one_width_is_answered():
    summary = {
        "parent_peaks": [137.096, 300.5],
        "groups": [
            {"parent_peak_mz": 137.096, "isolation_width": 2.0},
            {"parent_peak_mz": 300.5, "isolation_width": 150.0},
        ],
        "isolation_width": None,
    }

    response = await _summary(AsyncMock(return_value=summary))

    assert response["data"] == summary


@pytest.mark.asyncio
async def test_no_scan_storing_a_width_is_refused_in_one_sentence():
    # The reader's own refusal, as the summary meets it.
    with pytest.raises(ValueError) as no_width:
        _ms2_isolation_widths({})

    with pytest.raises(ApiException) as excinfo:
        await _summary(AsyncMock(side_effect=no_width.value))

    assert excinfo.value.status_code == 400
    assert excinfo.value.user_message == (
        "Failed to get ms2 summary. No MS2 scan stores an isolation width."
    )
