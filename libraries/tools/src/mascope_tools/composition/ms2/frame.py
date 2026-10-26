"""The readings of :mod:`.readings` as a DataFrame, with fixed column dtypes.

The core returns :class:`~.readings.Reading` rows so that it needs nothing but
numpy; a caller that tabulates them - a notebook, peaky, an export - builds the
frame here. One row per reading, in the readings' order, and the columns of
:data:`READINGS_COLUMNS` whatever the content: an empty table has them too.
"""

from collections.abc import Iterable

import pandas as pd

from mascope_tools.composition.ms2.readings import Reading


#: The readings table: every scalar field of :class:`Reading`, in its order, by
#: dtype. ``str`` is whatever pandas makes of ``dtype="str"`` (its string dtype
#: on pandas 3). The ``pieces`` tuple is left out: ``loss_name`` spells it.
READINGS_COLUMNS: dict[str, str] = {
    "spectrum": "str",
    "peak": "int64",
    "mz": "float64",
    "mz_cal": "float64",
    "intensity": "float64",
    "parent": "str",
    "parent_ion": "str",
    "formula": "str",
    "parity": "str",
    "dbe": "float64",
    "ppm": "float64",
    "loss": "str",
    "loss_pieces": "int64",
    "loss_name": "str",
    "unnamed_pieces": "int64",
    "radical_loss": "bool",
    "is_precursor": "bool",
    "rank": "int64",
    "n_candidates": "int64",
    "n_parents": "int64",
    "ladder_support": "str",
}


def readings_frame(readings: Iterable[Reading]) -> pd.DataFrame:
    """The readings as a DataFrame with the columns and dtypes of
    :data:`READINGS_COLUMNS`."""
    rows = list(readings)
    return pd.DataFrame(
        {
            column: pd.Series([getattr(r, column) for r in rows], dtype=dtype)
            for column, dtype in READINGS_COLUMNS.items()
        }
    )
