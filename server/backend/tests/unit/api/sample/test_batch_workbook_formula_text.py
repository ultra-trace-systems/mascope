"""The batch spreadsheet stores a typed name as text, never as a formula.

openpyxl reads a string that begins with ``=`` as a formula, so a sample or
compound name typed as one would run when the workbook is opened. The
workbook stores such a cell as text, with no apostrophe: the prefix is the
CSV exports' convention (``mascope_sdk.csv_cells``), and a typed text cell
needs none.
"""

import io

import openpyxl
import pandas as pd

from mascope_backend.api.controllers.sample.batches.export.service import (
    BatchSheets,
    write_batch_workbook,
)


def _workbook(samples: pd.DataFrame) -> openpyxl.Workbook:
    sheets = BatchSheets(
        sample_batch_name="Batch",
        batch_info=pd.DataFrame([["Batch name", "=1+1"]]),
        samples=samples,
        compounds=pd.DataFrame({"Compound name": ["=HYPERLINK(1)"]}),
        ions=pd.DataFrame(),
        provenance=pd.DataFrame([["build", "test"]]),
        verifications=pd.DataFrame(),
    )
    target = io.BytesIO()
    write_batch_workbook(target, sheets)
    target.seek(0)
    return openpyxl.load_workbook(target)


def _cell(sheet, value):
    """The one cell of a sheet holding ``value``."""
    (cell,) = [c for row in sheet.iter_rows() for c in row if c.value == value]
    return cell


def test_a_name_that_begins_with_a_formula_is_stored_as_text():
    book = _workbook(
        pd.DataFrame(
            {
                "Sample name": ["=1+1", "+cmd", "@SUM(A1)", "Spring"],
                "Offset": [-1.5, 2.0, 3.0, 4.0],
            }
        )
    )

    samples = book["Samples"]
    for value in ("=1+1", "+cmd", "@SUM(A1)", "Spring"):
        # The text as typed, no apostrophe, and a string cell, not a formula.
        assert _cell(samples, value).data_type == "s"
    assert _cell(samples, -1.5).data_type == "n"
    assert _cell(book["Batch"], "=1+1").data_type == "s"
    assert _cell(book["Match compounds"], "=HYPERLINK(1)").data_type == "s"
