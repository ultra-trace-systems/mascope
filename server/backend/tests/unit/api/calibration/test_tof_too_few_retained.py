"""
A TOF fit whose residual filter keeps fewer calibrants than the model needs.

More than five matches go through the one-pass residual rule, which keeps the
lines within the tolerance of a fit to them all. When the lines scatter, that
can be a single one, and the two-parameter mode-0 fit refuses a single point
with a ValueError: the sample's calibration failed as a server error instead
of the "not enough calibration peaks" warning the pipeline retries and reports.
"""

from unittest.mock import AsyncMock, patch

import pytest
import tof_audit_support as support

import mascope_file.name as m_name
from mascope_backend.api.controllers.calibration.lib.calibration_mz_fit import (
    TofCalibrationHandler,
)


NODE = "mascope_backend.api.controllers.calibration.lib.calibration_mz_fit"

#: Seven lines, which take the set past five matches. One sits on the true
#: axis and the rest scatter well inside the refine window but far outside
#: the 15 ppm tolerance of a fit to them all: that fit leaves the on-axis
#: line about 2 ppm off and every other one 40 ppm or more.
SCATTER = {62: 60.0, 79: -60.0, 81: -60.0, 97: 60.0, 125: 0.0, 188: 60.0, 195: -60.0}


@pytest.fixture
def filestore(tmp_path, monkeypatch):
    """A filestore of its own, so no test reads or writes a real one."""
    monkeypatch.setattr(m_name.runtime.meta, "filestore", str(tmp_path))
    monkeypatch.setattr(m_name, "_INSTRUMENT_TYPE", {})
    return tmp_path


@pytest.mark.asyncio
async def test_a_single_retained_line_is_a_warning_not_an_error(filestore):
    formulas = support.CALIBRANTS + support.MORE_CALIBRANTS
    filename = support.write_file(
        filestore, support.calibrant_peaks(formulas, off=SCATTER)
    )
    isotopes = support.isotopes(formulas)
    handler = TofCalibrationHandler(filename, support.params())

    with (
        patch.object(
            TofCalibrationHandler,
            "_resolve_calibration_isotopes",
            AsyncMock(return_value=isotopes.drop(columns="ion_label")),
        ),
        patch(
            f"{NODE}.read_instrument_functions",
            AsyncMock(return_value=({}, support.resolution)),
        ),
    ):
        await handler.fit()

    # The filter kept the on-axis line alone
    assert [round(row["mz"]) for row in handler.stats] == [125]
    assert handler.fit_result is None
    assert handler.error is None
    assert handler.warning.startswith("Not enough calibration peaks")
