"""
A peak's matches come back in the same order however the rows arrive.

``query_peak_matches`` reads its rows with no ``ORDER BY``, so Postgres may
hand them over in any order, and a different one from one read to the next.
Matches of one peak with equal scores, and the collections of one match, must
still come back in one order: a script that takes a peak's first match, or
compares two loads row by row, must not see a difference where nothing
changed (#2775).
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from mascope_backend.api.controllers.samples.lib import samples_matches


def row(peak, isotope, score, collection):
    """One match row as the query returns it: a match in one collection."""
    values = {
        "sample_peak_id": peak,
        "match_mz_error": 0.1,
        "match_abundance_error": 0.0,
        "match_score": score,
        "sample_peak_intensity": 100.0,
        "target_isotope_id": isotope,
        "relative_abundance": 1.0,
        "target_isotope_formula": f"formula_{isotope}",
        "target_ion_id": f"ion_{isotope}",
        "target_ion_formula": f"ion_formula_{isotope}",
        "ionization_mechanism_id": "mech",
        "filter_params": None,
        "target_compound_id": f"compound_{isotope}",
        "target_compound_name": f"name_{isotope}",
        "target_compound_formula": f"compound_formula_{isotope}",
        "target_collection_id": collection,
        "target_collection_name": f"collection {collection}",
        "instrument": "instrument",
        "instrument_type": None,
    }
    return SimpleNamespace(_asdict=lambda: dict(values))


#: Peak p1 has a best match and two tied behind it, one of them in two
#: collections; peak p2 has two tied matches only.
ROWS = [
    row("p1", "iso_c", 0.9, "col_1"),
    row("p1", "iso_b", 0.5, "col_1"),
    row("p1", "iso_a", 0.5, "col_2"),
    row("p1", "iso_a", 0.5, "col_1"),
    row("p2", "iso_e", 0.7, "col_1"),
    row("p2", "iso_d", 0.7, "col_1"),
]


class Session:
    """An ``async_session`` that answers every query with the given rows."""

    def __init__(self, rows):
        self.rows = rows

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, query):
        return SimpleNamespace(all=lambda: self.rows)


def categorised(df):
    """Match params that keep every match, so only the order is under test."""
    return df.assign(match_category=1)


async def read(rows):
    with (
        patch.object(samples_matches, "async_session", Session(rows)),
        patch.object(samples_matches, "apply_match_params", categorised),
    ):
        return await samples_matches.query_peak_matches(
            "sample", "instrument", ["p1", "p2"]
        )


#: Orders the rows may arrive in: as listed, reversed, rotated, and with only
#: the tied rows of each peak swapped. Reversing alone already catches a sort
#: on the score only, or a key without the isotope or the collection id.
ORDERS = [
    [0, 1, 2, 3, 4, 5],
    [5, 4, 3, 2, 1, 0],
    [2, 3, 4, 5, 0, 1],
    [3, 4, 5, 0, 1, 2],
    [0, 3, 1, 2, 5, 4],
    [0, 2, 3, 1, 4, 5],
]


@pytest.mark.asyncio
@pytest.mark.parametrize("order", ORDERS, ids=lambda o: "".join(map(str, o)))
async def test_tied_matches_come_back_in_one_order(order):
    """However the rows arrive, the lists are the same."""
    expected = await read(ROWS)
    assert await read([ROWS[i] for i in order]) == expected


@pytest.mark.asyncio
async def test_ties_break_on_the_ids():
    """Best score first, then target isotope; a match's collections by id."""
    p1, p2 = await read(list(reversed(ROWS)))

    assert [m["target_isotope_id"] for m in p1] == ["iso_c", "iso_a", "iso_b"]
    assert [m["target_isotope_id"] for m in p2] == ["iso_d", "iso_e"]
    iso_a = p1[1]
    assert iso_a["target_collection_ids"] == ["col_1", "col_2"]
    assert iso_a["target_collection_names"] == ["collection col_1", "collection col_2"]
