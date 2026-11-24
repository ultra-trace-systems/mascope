"""Stage A reads the target library's fine lines and resolves them at the
sample file's R(m/z), the abundance threshold applied to the resolved lines
(MAS-977)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest
from sqlalchemy.dialects import postgresql

from mascope_backend.api.controllers.target.lib.compute.target_ions_compute import (
    LEAD_ISOTOPE_FORMULA,
    FileLines,
    lines_at_file,
)
from mascope_backend.api.new.peak_assignments import sample_inputs
from mascope_tools.composition.line_model import HeightResolution


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
    with patch.object(sample_inputs, "async_session", return_value=context):
        frame = await sample_inputs._fetch_known_target_isotopes(
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


async def test_the_library_is_read_in_its_distinct_on_keys_order():
    # DISTINCT ON keeps its rows in no order unless the query states one, and
    # the known set lists its ions in the order they come (MAS-1164).
    _, sql = await _fetch(FileLines("HIGH"))

    assert "SELECT DISTINCT ON (target_isotope.target_isotope_id)" in sql
    assert sql.rstrip().endswith("ORDER BY target_isotope.target_isotope_id")


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


async def test_an_orbitrap_file_reads_a_merged_line_by_its_dominant_member():
    # At 12000 a FWHM at m/z 197 is 16 mDa: one peak holds both lines, but
    # the 15N line sits 0.38 FWHM off the 13C line, on its flank, and adds
    # nothing to the height an Orbitrap reads (mascope#3338). The 13C line
    # alone is under the threshold the two made together on a TOF.
    area, _ = await _fetch(FileLines("HIGH", lambda mz: 12000.0))
    height, _ = await _fetch(FileLines("HIGH", HeightResolution(lambda mz: 12000.0)))

    assert len(area) == 2
    assert height["target_isotope_formula"].tolist() == ["C10H14NO3-"]


def test_an_orbitrap_files_merged_line_sits_on_its_dominant_member():
    # Under a lower threshold the line is kept: at the 13C member's m/z, with
    # its share, still naming both members, and read as the 13C line.
    fine = pd.DataFrame([_row(*line)._asdict() for line in FINE])

    lines = lines_at_file(
        fine, FileLines("HIGH", HeightResolution(lambda mz: 12000.0)), 1e-4
    ).sort_values("mz")

    merged = lines.iloc[1]
    assert merged["mz"] == 197.1013
    assert merged["relative_abundance"] == pytest.approx(0.0004)
    assert merged["target_isotope_formula"] == "[15N]C10H14O3-/[13C]C9H14NO3-"
    assert merged[LEAD_ISOTOPE_FORMULA] == "[13C]C9H14NO3-"
