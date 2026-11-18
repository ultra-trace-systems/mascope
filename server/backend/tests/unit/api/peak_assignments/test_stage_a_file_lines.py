"""Stage A reads the target library's fine lines and resolves them at the
sample file's R(m/z), the abundance threshold applied to the resolved lines
(MAS-977)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from mascope_backend.api.controllers.target.lib.compute.target_ions_compute import (
    LEAD_ISOTOPE_FORMULA,
    FileLines,
)
from mascope_backend.api.new.peak_assignments import service


pytestmark = pytest.mark.asyncio

SAMPLE = SimpleNamespace(
    sample_item_name="Sample One",
    sample_batch_id="batch-1",
    instrument="instrument X",
    polarity="-",
)

#: One ion's fine lines: its own, a 13C line and a 15N line 6.3 mDa under it,
#: each line under the threshold the two make together.
FINE = [
    ("iso-a", "C10H14NO3-", 196.0979, 0.88),
    ("iso-b", "[13C]C9H14NO3-", 197.1013, 0.0004),
    ("iso-c", "[15N]C10H14O3-", 197.0950, 0.0003),
]


def _row(isotope_id, formula, mz, abundance):
    values = {
        "target_isotope_id": isotope_id,
        "target_ion_id": "ion-1",
        "target_isotope_formula": formula,
        "mz": mz,
        "relative_abundance": abundance,
        "resolution": "HIGH",
        "target_ion_formula": "C10H14NO3-",
        "ionization_mechanism_id": "mech-h",
        "target_compound_id": "compound-1",
        "target_compound_formula": "C10H15NO3",
        "ionization_mechanism": "[M-H]-",
        "ionization_mechanism_polarity": "-",
        "abundance_threshold": 5e-4,
    }
    return SimpleNamespace(_asdict=lambda: values)


async def _fetch(file_lines):
    result = MagicMock()
    result.all.return_value = [_row(*line) for line in FINE]
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=False)
    with patch.object(service, "async_session", return_value=context):
        frame = await service._fetch_known_target_isotopes(
            SAMPLE, 1e-4, ["mech-h"], file_lines
        )
    (statement,), _ = session.execute.call_args
    sql = statement.compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
    )
    return frame, str(sql)


async def test_the_library_is_read_at_its_fine_lines():
    _, sql = await _fetch(FileLines("LOW", lambda mz: 5000.0))

    assert "target_isotope.resolution = 'HIGH'" in sql
    assert "'LOW'" not in sql


async def test_a_tof_file_reads_one_line_where_one_peak_holds_two():
    # At 5000 a FWHM at m/z 197 is 39 mDa: the 13C and 15N lines are one line,
    # which clears the threshold neither clears alone.
    frame, _ = await _fetch(FileLines("LOW", lambda mz: 5000.0))

    assert len(frame) == 2
    assert set(frame["resolution"]) == {"LOW"}
    assert "abundance_threshold" not in frame.columns
    merged = frame.sort_values("mz").iloc[1]
    assert merged["relative_abundance"] == pytest.approx(0.0007)
    assert merged["target_isotope_formula"] == "[15N]C10H14O3-/[13C]C9H14NO3-"
    # Read as its brightest member, the 13C line.
    assert merged[LEAD_ISOTOPE_FORMULA] == "[13C]C9H14NO3-"


async def test_without_a_merge_the_threshold_leaves_each_faint_line_out():
    frame, _ = await _fetch(FileLines("HIGH"))

    assert frame["target_isotope_id"].tolist() == ["iso-a"]
