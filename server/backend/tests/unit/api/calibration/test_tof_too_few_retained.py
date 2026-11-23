"""
A TOF fit whose residual filter keeps fewer calibrants than the model needs.

More than five matches went through a one-pass residual rule up to node
version 3, which kept the lines within the tolerance of a fit to them all.
When the lines scatter, that can be a single one, and the two-parameter
mode-0 fit refuses a single point with a ValueError: the sample's
calibration failed as a server error instead of the "not enough calibration
peaks" warning the pipeline retries and reports. The audit still selects that
way to reproduce such a node's fits (``one_pass``).

Since version 4 the node drops the worst line one at a time, and stops at the
fewest lines the fit needs; it then keeps those of them within the tolerance,
which on a scatter are too few as well, and the fit is the same warning.

The node's own search hands the residual rule only lines that agree with one
axis, so it no longer meets such a scatter; the match within the refine
window, which the audit reproduces version-2 fits with, still does, through
the same handler.
"""

from unittest.mock import patch

import numpy as np
import pytest
import tof_audit_support as support

import mascope_file.name as m_name
from mascope_backend.api.controllers.calibration.lib import (
    tof_calibration_audit as audit,
)


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


@pytest.mark.parametrize(
    "one_pass, kept",
    [
        # One fit of all seven: the on-axis line alone is within 15 ppm
        pytest.param(True, [125], id="one pass"),
        # Down to the three lines a mode-0 fit needs, of which two are
        # within 15 ppm of the fit to the three
        pytest.param(False, [62, 195], id="worst line first"),
    ],
)
@pytest.mark.asyncio
async def test_too_few_retained_lines_are_a_warning_not_an_error(
    filestore, one_pass, kept
):
    formulas = support.CALIBRANTS + support.MORE_CALIBRANTS
    filename = support.write_file(
        filestore, support.calibrant_peaks(formulas, off=SCATTER)
    )
    isotopes = support.isotopes(formulas)
    handler = audit.AuditCalibrationHandler(
        audit.read_stored_peaks(filename),
        support.params(),
        isotopes,
        support.resolution,
        windowed=True,
        one_pass=one_pass,
    )

    await handler.fit()

    assert [round(row["mz"]) for row in handler.stats] == kept
    assert handler.fit_result is None
    assert handler.error is None
    assert handler.warning.startswith("Not enough calibration peaks")


@pytest.mark.asyncio
async def test_the_worst_line_rule_fits_no_fewer_lines_than_the_fit_needs(filestore):
    """On the scatter, the rule drops one line per refit from seven down to
    the three a mode-0 fit needs, and no further: a fit to two lines passes
    through both, and would call them in tolerance whatever they are."""
    formulas = support.CALIBRANTS + support.MORE_CALIBRANTS
    filename = support.write_file(
        filestore, support.calibrant_peaks(formulas, off=SCATTER)
    )
    handler = audit.AuditCalibrationHandler(
        audit.read_stored_peaks(filename),
        support.params(),
        support.isotopes(formulas),
        support.resolution,
        windowed=True,
    )
    _, matches = await handler._match_calibration_compounds(handler._isotopes)
    sizes = []
    fit = handler._fit_matches

    def counted(matches_df, mode=None):
        sizes.append(len(matches_df))
        return fit(matches_df, mode)

    with patch.object(handler, "_fit_matches", counted):
        kept = handler._select_from_many_matches(matches)

    assert sizes == [7, 6, 5, 4, 3]
    assert sorted(round(mz) for mz in kept["mz"]) == [62, 195]


@pytest.mark.asyncio
async def test_a_line_the_fit_gives_no_mass_is_dropped_first(filestore):
    """Seven lines on the axis, and a fit that gives the line at 97 Th no
    mass: its residual is NaN, which no comparison calls outside the
    tolerance. The rule counts it as the furthest outside, drops it, and
    keeps the other six."""
    formulas = support.CALIBRANTS + support.MORE_CALIBRANTS
    filename = support.write_file(filestore, support.calibrant_peaks(formulas))
    handler = audit.AuditCalibrationHandler(
        audit.read_stored_peaks(filename),
        support.params(),
        support.isotopes(formulas),
        support.resolution,
    )
    _, matches = await handler._match_calibration_compounds(handler._isotopes)
    assert len(matches) == 7
    evaluate = handler._evaluate_fit

    def no_mass_at_97(matches_df, fit_result):
        stats = evaluate(matches_df, fit_result)
        at_97 = matches_df["mz"].round().to_numpy() == 97
        stats["post_dmz"] = np.where(at_97, np.nan, stats["post_dmz"])
        return stats

    with patch.object(handler, "_evaluate_fit", no_mass_at_97):
        kept = handler._select_from_many_matches(matches)

    assert sorted(round(mz) for mz in kept["mz"]) == [62, 79, 81, 125, 188, 195]
