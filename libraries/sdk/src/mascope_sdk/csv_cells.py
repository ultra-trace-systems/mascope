"""Text cells in Mascope's CSV exports, and how to read them back.

A spreadsheet program reads a cell that begins with ``=``, ``+``, ``-`` or
``@`` as a formula, and a tab or a carriage return there can start one too
(OWASP's "CSV Injection"). The names and the claims in an export are typed
by people, so Mascope writes such a text cell with one leading apostrophe,
which a spreadsheet shows as the text it is. Numbers are never touched: a
negative number is a legitimate ``-``.

Reading a CSV export back strips that one apostrophe again where the next
character is one of the six, so a round trip returns the text as it was
typed. A text that really begins with an apostrophe followed by one of the
six loses it on the way back; that is the one case that does not round-trip.

The server writes its exports with :func:`escape_cell` and
:func:`escape_frame`; :func:`read_export_csv` reads one back as the server
wrote it, and :func:`unescape_cell` and :func:`unescape_frame` undo the
prefix for a file read some other way. ``docs/user/concepts/tables.md``
describes the convention for readers outside Python.
"""

from __future__ import annotations

import os
from typing import Any

import pandas as pd


#: What a spreadsheet takes a text cell beginning with for a formula.
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")

#: The prefix that shows such a cell as text.
PREFIX = "'"


def escape_cell(value: Any) -> Any:
    """A value as a CSV export writes it.

    :param value: A cell's value.
    :return: A text that begins with one of :data:`FORMULA_STARTS`, behind one
        apostrophe; every other value, a number or None included, as it is.
    """
    if isinstance(value, str) and value.startswith(FORMULA_STARTS):
        return PREFIX + value
    return value


def unescape_cell(value: Any) -> Any:
    """A value of a CSV export as it was before :func:`escape_cell`.

    :param value: A cell's value, as read from the file.
    :return: A text that begins with an apostrophe and then one of
        :data:`FORMULA_STARTS`, without that apostrophe; every other value as
        it is.
    """
    if (
        isinstance(value, str)
        and value.startswith(PREFIX)
        and value[1:].startswith(FORMULA_STARTS)
    ):
        return value[1:]
    return value


def _text_columns(frame: pd.DataFrame) -> list:
    """The columns that can hold text: object and string dtypes."""
    return [
        column
        for column in frame.columns
        if pd.api.types.is_object_dtype(frame[column].dtype)
        or pd.api.types.is_string_dtype(frame[column].dtype)
    ]


def _map_text(frame: pd.DataFrame, cell) -> pd.DataFrame:
    out = frame.copy()
    for column in _text_columns(out):
        out[column] = out[column].map(cell, na_action="ignore")
    return out


def escape_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """A copy of a frame with every text cell escaped (:func:`escape_cell`).

    Only the text columns are read; a numeric column is left as it is.
    """
    return _map_text(frame, escape_cell)


def unescape_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """A copy of a frame with every text cell unescaped (:func:`unescape_cell`)."""
    return _map_text(frame, unescape_cell)


def read_export_csv(
    path: str | os.PathLike, sep: str = ";", **kwargs: Any
) -> pd.DataFrame:
    """Read a CSV that Mascope exported, its text cells as they were typed.

    The batch ledger and the peak exports separate their columns with ``;``,
    the calibration trend with ``,`` (pass ``sep=","``).

    Example::

        from mascope_sdk import read_export_csv

        ledger = read_export_csv("20261123T101500_batch_ledger_Spring.csv")

    :param path: The file.
    :param sep: The column separator.
    :param kwargs: Passed on to :func:`pandas.read_csv`.
    :return: The table, every text cell with the export's apostrophe removed
        (:func:`unescape_frame`).
    """
    return unescape_frame(pd.read_csv(path, sep=sep, **kwargs))
