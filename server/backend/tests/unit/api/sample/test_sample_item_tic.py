"""
The TIC a new sample item is given, and the scans it is taken over.

A sample's TIC is that of the MS1 scans of its polarity, as the sample reads
them. An acquisition of fragmentation scans alone records no MS1 scan, so
asking for that TIC finds nothing - the reader raises rather than hand back an
empty selection - and the item could not be created at all: every MS2 route is
keyed on a sample item, so the file's data had nothing to be read through.
Such an item now takes the TIC of the scans its polarity does have.

What must survive that is the refusal of a polarity the file does not carry.
The wider read raises for it too, and the item is still not created.
"""

from unittest.mock import patch

import numpy as np
import pytest

from mascope_thermo.thermo import NoScansFoundError


_ITEMS = "mascope_backend.api.controllers.sample.items.sample_items_controller"


def _tic_reads(answers):
    """Stand in for both reads, recording each.

    The MS1 read goes through ``_polarity_tic_per_scan``, which reads the
    polarity as its sample does; the wider read asks ``get_tic_per_scan`` for
    every scan type.

    :param answers: What each read gives, by the scan type it asked for
        (``"Ms"`` for the first): a TIC array, or an exception to raise.
    :return: The two stand-ins, and the list their reads are recorded in.
    """
    reads = []

    def answer(base_filename, polarity, scan_type):
        reads.append((base_filename, polarity, scan_type))
        given = answers[scan_type]
        if isinstance(given, Exception):
            raise given
        return np.arange(len(given), dtype=float), np.asarray(given, dtype=float)

    def polarity_tic_per_scan(base_filename, polarity):
        return answer(base_filename, polarity, "Ms")

    def get_tic_per_scan(base_filename, polarity=None, scan_type="Ms"):
        return answer(base_filename, polarity, scan_type)

    return polarity_tic_per_scan, get_tic_per_scan, reads


async def _tic(answers):
    from mascope_backend.api.controllers.sample.items.sample_items_controller import (
        _sample_item_tic,
    )

    polarity_tic_per_scan, get_tic_per_scan, reads = _tic_reads(answers)
    with (
        patch(f"{_ITEMS}._polarity_tic_per_scan", polarity_tic_per_scan),
        patch(f"{_ITEMS}.m_compute.get_tic_per_scan", get_tic_per_scan),
    ):
        tic = await _sample_item_tic(filename="instrument-A_file", polarity="+")
    return tic, reads


@pytest.mark.asyncio
async def test_it_is_the_tic_of_the_ms1_scans():
    tic, reads = await _tic({"Ms": [1.0, 2.0, 3.0], None: [100.0]})

    assert tic == 6.0
    # One read: a file with MS1 scans never pays for a second.
    assert reads == [("instrument-A_file", "+", "Ms")]


@pytest.mark.asyncio
async def test_a_polarity_with_no_ms1_scans_takes_the_scans_it_has():
    tic, reads = await _tic(
        {"Ms": NoScansFoundError("no MS1 scans"), None: [10.0, 20.0]}
    )

    assert tic == 30.0
    assert [scan_type for _, _, scan_type in reads] == ["Ms", None]


@pytest.mark.asyncio
async def test_a_polarity_the_file_does_not_carry_is_still_refused():
    """The wider read finds nothing either, and says so the same way."""
    with pytest.raises(NoScansFoundError, match="no scans at all"):
        await _tic(
            {
                "Ms": NoScansFoundError("no MS1 scans"),
                None: NoScansFoundError("no scans at all"),
            }
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [TypeError("NoneType is not subscriptable"), ValueError("a broken store")],
)
async def test_an_error_that_is_not_an_empty_selection_is_not_read_around(error):
    """The TOF reader's empty selection is a ``TypeError``, with no second kind
    of scan to fall back on - and a fault of any other class is a fault, a
    peak store that cannot be read included: read around, it would give the
    item the TIC of scans its sample does not read."""
    with pytest.raises(type(error)):
        await _tic({"Ms": error, None: [1.0]})
