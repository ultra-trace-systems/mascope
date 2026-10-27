"""What the MS2 store decides without a file: its key, its gate, its window.

The store itself is written and read on the hermetic MS2 fixture, in the
backend's suite (``server/backend/tests/unit/api/ms2/test_ms2_store.py``):
the converter is what writes it.
"""

import numpy as np
import pytest

from mascope_signal.ms2_store import (
    Ms2Store,
    _window_mask,
    group_key,
    holds_fragmentation,
    parse_group_key,
)
from mascope_thermo.thermo import InvalidRangeError


class TestTheGroupKey:
    def test_is_the_mz_at_four_decimals_and_the_activation(self):
        assert group_key(137.096, "hcd25.00") == "137.0960@hcd25.00"

    def test_keeps_every_stage_of_a_chained_activation(self):
        assert group_key(445.12, "cid30.00@hcd20.00") == "445.1200@cid30.00@hcd20.00"

    def test_of_a_group_with_no_activation_is_its_mz(self):
        assert group_key(137.096, "") == "137.0960"

    @pytest.mark.parametrize(
        ("key", "read"),
        [
            ("137.0960@hcd25.00", (137.096, "hcd25.00")),
            # As the centroid route spells the same group
            ("137.096@hcd25.00", (137.096, "hcd25.00")),
            # More decimals than the key keeps name the same group
            ("137.09604@hcd25.00", (137.096, "hcd25.00")),
            ("445.1200@cid30.00@hcd20.00", (445.12, "cid30.00@hcd20.00")),
            ("137.0960", (137.096, "")),
            (" 137.0960@hcd25.00 ", (137.096, "hcd25.00")),
        ],
    )
    def test_reads_back(self, key, read):
        assert parse_group_key(key) == read

    def test_reads_back_what_it_wrote(self):
        for mz, activation in ((137.096, "hcd25.00"), (99.99995, ""), (1.0, "cid35")):
            assert parse_group_key(group_key(mz, activation)) == (
                round(mz, 4),
                activation,
            )

    @pytest.mark.parametrize(
        "key", ["hcd25.00", "", "@hcd25.00", "m137@hcd25", "nan@hcd25.00", "inf"]
    )
    def test_that_names_no_mz_is_refused_saying_what_one_reads(self, key):
        with pytest.raises(ValueError, match="137.0960@hcd25.00"):
            parse_group_key(key)


class TestWhatGetsAStore:
    @pytest.mark.parametrize("order", [2, 3])
    def test_a_census_with_a_fragmentation_stream(self, order):
        streams = [{"signature": {"ms_order": 1}}, {"signature": {"ms_order": order}}]

        assert holds_fragmentation(streams)

    @pytest.mark.parametrize(
        "streams",
        [
            None,
            [],
            [{"signature": {"ms_order": 1}}],
            # True is an int, and would read as MS1 if read as one
            [{"signature": {"ms_order": True}}],
            [{"signature": {"ms_order": "2"}}],
            [{"signature": {}}],
            [{}],
        ],
    )
    def test_not_one_without(self, streams):
        assert not holds_fragmentation(streams)


class TestTheWindow:
    """As both reader backends select a sample's scans."""

    TIMES = np.array([1.2, 2.4, 3.6, 4.8])

    def test_takes_both_its_bounds(self):
        assert _window_mask(self.TIMES, 2.4, 3.6).tolist() == [
            False,
            True,
            True,
            False,
        ]

    def test_widens_them_by_machine_epsilon(self):
        eps = np.finfo(np.float64).eps * 3.6

        assert _window_mask(self.TIMES, 2.4 + eps / 2, 3.6 - eps / 2).tolist() == [
            False,
            True,
            True,
            False,
        ]
        assert not _window_mask(self.TIMES, 2.4 + 2 * eps, 3.5).any()

    def test_with_no_bounds_takes_every_scan(self):
        assert _window_mask(self.TIMES, None, None).all()

    def test_with_one_bound_runs_to_the_last_or_from_the_first_scan(self):
        assert _window_mask(self.TIMES, 3.6, None).tolist() == [
            False,
            False,
            True,
            True,
        ]
        assert _window_mask(self.TIMES, None, 2.4).tolist() == [
            True,
            True,
            False,
            False,
        ]

    def test_that_ends_before_it_starts_is_refused(self):
        with pytest.raises(InvalidRangeError):
            _window_mask(self.TIMES, 3.6, 2.4)


class TestAFileWithNoPropsToRead:
    """Has no store to read: the raw read answers for it, or refuses."""

    def test_one_not_in_the_filestore(self):
        assert Ms2Store.open("ORBI-1_2026.01.01-12h00m00s_absent") is None

    def test_one_whose_name_places_it_nowhere(self):
        assert Ms2Store.open("not-a-sample-file.raw") is None
