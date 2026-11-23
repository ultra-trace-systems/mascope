"""
Excel utility functions for batch export operations.
"""

from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet


def auto_adjust_column_width(
    worksheet: Worksheet,
    max_width: int = 50,
    padding: int = 2,
) -> None:
    """
    Auto-adjust column widths based on content length.

    Iterates through all columns in the worksheet and sets width based on
    the longest cell content, with configurable maximum width and padding.

    :param worksheet: OpenPyXL worksheet to adjust
    :param max_width: Maximum column width to prevent excessively wide columns
    :param padding: Extra padding characters to add to calculated width
    """
    for column in worksheet.columns:
        column_letter = get_column_letter(column[0].column)
        max_length = 0

        for cell in column:
            if cell.value is not None:
                try:
                    cell_length = len(str(cell.value))
                    max_length = max(max_length, cell_length)
                except (TypeError, AttributeError):
                    # Skip cells with problematic values
                    continue

        # Apply width with padding, capped at max_width
        adjusted_width = min(max_length + padding, max_width)
        worksheet.column_dimensions[column_letter].width = adjusted_width


def type_formulas_as_text(worksheet: Worksheet) -> None:
    """
    Store every cell openpyxl took for a formula as the text it is.

    openpyxl reads a string that begins with ``=`` as a formula, and a sheet
    written from a frame holds only values: a sample, compound or collection
    name typed by someone, which a spreadsheet program would otherwise run.
    A typed text cell shows as text with no prefix; the other characters a
    CSV export escapes (``+``, ``-``, ``@``) are text cells already.

    :param worksheet: OpenPyXL worksheet written from values only.
    """
    for row in worksheet.iter_rows():
        for cell in row:
            if cell.data_type == "f":
                cell.data_type = "s"
