"""Unit tests for ``monoisotopic.monoisotopic_positions`` (MAS-1172).

It picks each ion's monoisotopic row for a whole frame at once, and has to
pick the very row ``monoisotopic_row`` picks from the ion's rows: the lightest
line of a bromine cluster, the labelled line of a labelled ion, the lightest
row where no formula says, and among rows of one m/z the one pandas' sort puts
first.
"""

import numpy as np
import pandas as pd
import pytest

from mascope_backend.api.new.peak_assignments.monoisotopic import (
    monoisotopic_positions,
    monoisotopic_row,
)


def _by_rows(frame: pd.DataFrame) -> list[str]:
    """What ``monoisotopic_row`` picks, ion by ion, as the row's id."""
    return [
        monoisotopic_row(group)["row"]
        for _, group in frame.groupby("target_ion_id", sort=False)
    ]


def _at_once(frame: pd.DataFrame) -> list[str]:
    return list(frame["row"].iloc[monoisotopic_positions(frame)])


BROMIDE = pd.DataFrame(
    {
        "row": ["r1", "r2", "r3", "r4"],
        "target_ion_id": ["br3", "br3", "br3", "br3"],
        "target_ion_formula": ["Br3-"] * 4,
        # The tallest line is not the monoisotopic one.
        "target_isotope_formula": ["[81Br]Br2-", "Br3-", "[81Br]2Br-", "[81Br]3-"],
        "mz": [238.75, 236.75, 240.75, 242.75],
    }
)

LABELLED = pd.DataFrame(
    {
        "row": ["u1", "u2", "u3"],
        "target_ion_id": ["lab"] * 3,
        "target_ion_formula": ["C9H16O7^N-"] * 3,
        # The unlabelled remainder one unit below the labelled line.
        "target_isotope_formula": ["C9H16NO7-", "[15N]C9H16O7-", "[15N]C8[13C]H16O7-"],
        "mz": [249.08, 250.08, 251.08],
    }
)


@pytest.mark.parametrize("frame", [BROMIDE, LABELLED], ids=["bromide", "labelled"])
def test_the_monoisotopic_line_is_picked(frame):
    assert _at_once(frame) == _by_rows(frame)
    assert _at_once(frame) == (["r2"] if frame is BROMIDE else ["u2"])


def test_without_isotope_formulas_the_lightest_row_stands_in():
    frame = BROMIDE.drop(columns=["target_isotope_formula"])
    assert _at_once(frame) == _by_rows(frame) == ["r2"]


def test_without_ion_formulas_no_label_is_read():
    frame = LABELLED.drop(columns=["target_ion_formula"])
    assert _at_once(frame) == _by_rows(frame)


def test_a_row_with_no_mz_comes_last():
    frame = BROMIDE.assign(
        mz=[np.nan, 236.75, 240.75, 242.75],
        target_isotope_formula=["Br3-", "[81Br]Br2-", "[81Br]2Br-", "[81Br]3-"],
    )
    assert _at_once(frame) == _by_rows(frame) == ["r1"]
    # With no formula naming it, the lightest row with an m/z stands in.
    unnamed = frame.assign(target_isotope_formula="[81Br]Br2-")
    assert _at_once(unnamed) == _by_rows(unnamed) == ["r2"]


def test_every_ion_of_a_frame_is_picked_as_row_by_row():
    """A frame of many ions, interleaved, with lines of one m/z and formulas
    that name no monoisotopic line: the same row as ``monoisotopic_row``."""
    rng = np.random.default_rng(1172)
    rows = []
    for ion in range(300):
        # Some ions past the sort's small-array path, where its order of
        # equal m/z values is no longer the rows' own.
        n = int(rng.integers(1, 9)) if ion % 10 else int(rng.integers(30, 60))
        for line in range(n):
            rows.append(
                {
                    "target_ion_id": f"ion-{ion}",
                    "target_ion_formula": "C9H16O7^N-" if ion % 7 == 0 else "C6H13O6+",
                    "target_isotope_formula": rng.choice(
                        ["C6H13O6+", "C5[13C]H13O6+", "[15N]C9H16O7-", "x/C6H13O6+"]
                    ),
                    # Few distinct values, so many ties.
                    "mz": float(rng.integers(0, 4)) + (np.nan if line == 5 else 0.0),
                }
            )
    frame = pd.DataFrame(rows).sample(frac=1.0, random_state=3).reset_index(drop=True)
    frame["row"] = [f"row-{i}" for i in range(len(frame))]
    assert _at_once(frame) == _by_rows(frame)
