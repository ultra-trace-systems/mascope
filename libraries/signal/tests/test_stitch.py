"""The stitch map, the composite mask and the overlap reading
(``mascope_signal.stitch``).

A composite acquisition measures one chemistry as several scan ranges, and
the stitch says which stream owns each m/z of the one spectrum they make.
The rule is pinned here on the layouts it was fitted to and checked on: the
ones a site ran while it tuned such a method, as its files' own census gives
them, ranges and microscans. Nothing here reads a file.
"""

import json

import numpy as np
import pytest

import mascope_signal.stitch as m_stitch


def _stream(scan_range, microscans=None, polarity="-", scan_mode="Full", event=None):
    """A stream as the census records it, as far as the stitch reads it."""
    ranges = scan_range if isinstance(scan_range, list) else [scan_range]
    text = ", ".join(f"{lower:.4f}-{upper:.4f}" for lower, upper in ranges)
    key = f"FTMS {polarity} p NSI {scan_mode} ms [{text}] R=120000"
    return {
        "key": key if event is None else f"{key} event={event}",
        "signature_key": key,
        "signature": {
            "polarity": polarity,
            "scan_mode": scan_mode,
            "ms_order": 1,
            "scan_ranges": [list(pair) for pair in ranges],
            "resolution": 120000,
        },
        "acquisition_params": {
            "source": "opentfraw",
            "scans_sampled": 1,
            "constant": {} if microscans is None else {"Micro Scan Count:": microscans},
            "varying": [],
        },
    }


def _streams(*windows, polarity="-"):
    """One stream per ``(range, microscans)`` or ``(range, microscans, mode)``."""
    return [
        _stream(
            window[0],
            window[1],
            polarity=polarity,
            scan_mode=window[2] if len(window) > 2 else "Full",
            event=event,
        )
        for event, window in enumerate(windows, start=1)
    ]


def _runs(streams, polarity="-", **kwargs):
    return m_stitch.stitch_map(streams, **kwargs)["runs"][polarity]


# A reagent scan over the whole low range at one microscan, then the analyte
# windows at ten: the layout the site settled on for its negative chemistry.
REAGENT, LOW, MID, HIGH = (40, 138), (66, 124), (132, 460), (440, 900)
SETTLED = [(REAGENT, 1), (LOW, 10), (MID, 10), (HIGH, 10)]

# Every multi-window layout the site ran, each with the map the rule gives
# it: ``[lower, upper, stream index]`` per run, a run owning lower <= m/z <
# upper.
LAYOUTS = {
    "two-windows-that-touch-at-128": (
        [((40, 128), 1), ((128, 600), 10)],
        [[40, 128, 0], [128, 600, 1]],
    ),
    "two-windows-that-touch-at-135": (
        [((40, 135), 1), ((135, 600), 10)],
        [[40, 135, 0], [135, 600, 1]],
    ),
    "a-low-window-of-one-scan-defined-last": (
        [((40, 135), 1), ((135, 500), 10), ((500, 1000), 10), ((65, 125), 10)],
        [[40, 66, 0], [66, 122, 3], [122, 135, 0], [135, 500, 1], [500, 1000, 2]],
    ),
    "a-sim-low-window": (
        [((40, 138), 1), ((72, 110), 10, "SIM"), ((132, 490), 10), ((460, 900), 10)],
        [[40, 73, 0], [73, 108, 1], [108, 133, 0], [133, 465, 2], [465, 900, 3]],
    ),
    "the-settled-layout": (
        SETTLED,
        [[40, 67, 0], [67, 122, 1], [122, 133, 0], [133, 444, 2], [444, 900, 3]],
    ),
    "a-reagent-scan-that-stops-short-of-the-mid-window": (
        [((40, 131), 1), ((66, 118), 10), ((132, 490), 10), ((460, 900), 10)],
        [[40, 67, 0], [67, 116, 1], [116, 131, 0], [132, 465, 2], [465, 900, 3]],
    ),
    "the-same-with-a-sim-low-window": (
        [((40, 131), 1), ((66, 115), 10, "SIM"), ((132, 460), 10), ((440, 900), 10)],
        [[40, 67, 0], [67, 113, 1], [113, 131, 0], [132, 444, 2], [444, 900, 3]],
    ),
    "the-sim-window-moved-up-to-72": (
        [((40, 131), 1), ((72, 115), 10, "SIM"), ((132, 460), 10), ((440, 900), 10)],
        [[40, 73, 0], [73, 113, 1], [113, 131, 0], [132, 444, 2], [444, 900, 3]],
    ),
    "the-other-chemistry-with-a-high-window-to-1200": (
        [((40, 131), 1), ((72, 118), 10, "SIM"), ((132, 460), 10), ((440, 1200), 10)],
        [[40, 73, 0], [73, 116, 1], [116, 131, 0], [132, 444, 2], [444, 1200, 3]],
    ),
    "the-settled-layout-with-a-high-window-to-1200": (
        [(REAGENT, 1), (LOW, 10), (MID, 10), ((440, 1200), 10)],
        [[40, 67, 0], [67, 122, 1], [122, 133, 0], [133, 444, 2], [444, 1200, 3]],
    ),
    "settle-then-measure-on-one-range": (
        [((40, 600), 1), ((40, 600), 10)],
        [[40, 600, 1]],
    ),
}


# -- the default map ----------------------------------------------------------------


@pytest.mark.parametrize(("windows", "runs"), LAYOUTS.values(), ids=list(LAYOUTS))
def test_every_layout_the_site_ran_is_mapped_with_nothing_configured(windows, runs):
    """A one-scan window, a SIM window, a high window to 1200, two windows
    that touch without overlapping, and the same range run twice: each gets
    its map from its ranges and microscans alone. The settled layout's is
    the one the site drew for it by hand."""
    stitch = m_stitch.stitch_map(_streams(*windows))

    assert stitch["runs"] == {"-": runs}
    assert stitch["sources"] == {"-": "default"}
    assert stitch["notes"] == []


def test_a_trim_is_a_share_of_the_edges_mz_and_not_of_the_ranges_width():
    """One per cent of 66 and two of 124 put the low window's claim at 67 to
    122; of its width of 58 they would put it at 67 to 123. The mid window
    starts at 133 by its edge, and at 135 by its width of 328. The first is
    what the site drew."""
    runs = _runs(_streams(*SETTLED))

    assert [67, 122, 1] in runs
    assert [133, 444, 2] in runs


def test_more_microscans_own_an_mz_two_streams_claim():
    """Whichever of them starts higher. The ten-microscan window owns all it
    claims, 40 to 196, and the one-microscan window gets what is left of its
    own range above that."""
    runs = _runs(_streams(((40, 200), 10), ((100, 300), 1)))

    assert runs == [[40, 196, 0], [196, 300, 1]]


def test_among_equal_microscans_the_window_that_starts_higher_owns():
    """A window reads weakest toward its top, so where the mid and the high
    window both claim an m/z, 444 to 451, it is the high one's."""
    runs = _runs(_streams((MID, 10), (HIGH, 10)))

    assert runs == [[132, 444, 0], [444, 900, 1]]


def test_streams_the_rule_cannot_tell_apart_go_by_their_order_in_the_file():
    """The same microscans on ranges that start at the same m/z: nothing in
    the rule separates them, and something has to. The first one owns, so a
    file's map is the same every time it is drawn."""
    wide, narrow = ((40, 600), 10), ((40, 300), 10)

    assert _runs(_streams(wide, narrow)) == [[40, 600, 0]]
    # The narrow one first: it owns what it claims, 40 to 294
    assert _runs(_streams(narrow, wide)) == [[40, 294, 0], [294, 600, 1]]
    assert _runs(_streams(wide, wide)) == [[40, 600, 0]]


def test_an_mz_no_claim_covers_goes_to_the_range_that_holds_it():
    """The claims of two windows that touch at 128 stop at 125 and start at
    129, and the high window's stops at 588. What lies between and above is
    still measured, by the window whose untrimmed range holds it."""
    runs = _runs(_streams(((40, 128), 1), ((128, 600), 10)))

    assert runs == [[40, 128, 0], [128, 600, 1]]


def test_a_held_mz_two_ranges_reach_goes_by_the_same_order():
    """Between the claims of two windows that overlap by one m/z, 99 to 100,
    both ranges hold it, and the one with more microscans owns it."""
    runs = _runs(_streams(((40, 100), 10), ((99, 300), 1)))

    assert runs == [[40, 100, 0], [100, 300, 1]]


def test_an_mz_no_range_holds_is_a_gap():
    """A reagent scan that ends at 131 and a mid window that starts at 132:
    nothing measured the m/z between them, and no run covers it."""
    runs = _runs(_streams(((40, 131), 1), ((132, 460), 10)))

    assert runs == [[40, 131, 0], [132, 460, 1]]


def test_a_gap_between_two_ranges_of_one_stream_stays_a_gap():
    """A multiplexed stream owns both sides of what lies between its two
    ranges, 320 to 400, and nothing measured that either: its two runs are
    not joined across it."""
    wide = _stream((100, 310), 1, event=1)
    multiplexed = _stream([(300, 320), (400, 420)], 10, scan_mode="SIM", event=2)

    assert _runs([wide, multiplexed]) == [[100, 303, 0], [303, 320, 1], [400, 420, 1]]


@pytest.mark.parametrize(
    ("window", "claim"),
    [((50, 125), (50, 122)), ((150, 175), (152, 172))],
    ids=["50.5-and-122.5", "151.5-and-171.5"],
)
def test_a_trimmed_edge_on_a_half_goes_to_the_even_mz(window, claim):
    """One per cent above 50 is 50.5 and two below 125 is 122.5. A half is
    rounded to the even m/z, and by exact arithmetic, so that the answer is
    a rule's and not whatever a product of floats lands on."""
    runs = _runs(_streams(((10, 1000), 1), (window, 10)))

    assert runs == [[10, claim[0], 0], [claim[0], claim[1], 1], [claim[1], 1000, 0]]


@pytest.mark.parametrize(
    ("edges", "stated", "claim"),
    [
        ((50.0001, 125.0001), "[50.0001-125.0001]", (51, 123)),
        ((50.00004, 125.00004), "[50.0000-125.0000]", (50, 122)),
        ((50.00000001, 125.00000001), "[50.0000-125.0000]", (50, 122)),
    ],
    ids=["a-fourth-decimal-counts", "a-fifth-does-not", "nor-a-floats-noise"],
)
def test_an_edge_is_read_at_the_four_decimals_its_key_states(edges, stated, claim):
    """A stream's key states its range to four decimals, and the map is drawn
    from the edge as the key states it, so that two streams of one key get
    one map. Edges of 50 and 125 trim to 50.5 and 122.5, and so to 50 and
    122. A ten-thousandth above them the key says so, and the trims pass the
    half: 51 and 123. A hundred-thousandth above them, or a float away from
    what the method states, the key still reads 50.0000 and 125.0000, and
    the map is that of 50 and 125, where the edges read as the floats they
    are would trim to 51 and 123."""
    streams = _streams(((10, 1000), 1), (edges, 10))

    assert stated in streams[1]["key"]
    assert round(edges[0] * 1.01) == 51
    assert round(edges[1] * 0.98) == 123
    assert _runs(streams) == [
        [10, claim[0], 0],
        [claim[0], claim[1], 1],
        [claim[1], 1000, 0],
    ]


@pytest.mark.parametrize("ten", [10, 10.0, "10", " 10 "])
def test_microscans_are_a_number_whichever_way_a_reader_reports_them(ten):
    """One reader backend hands trailer values over as numbers, the other as
    text."""
    runs = _runs(_streams((REAGENT, "1"), (LOW, ten)))

    assert runs == [[40, 67, 0], [67, 122, 1], [122, 138, 0]]


@pytest.mark.parametrize("unknown", [None, "n/a", float("nan"), float("inf")])
def test_a_stream_whose_microscans_the_census_cannot_say_loses_the_contest(unknown):
    """A count that varied among the scans sampled is not recorded, and one
    that is no number says nothing. Such a stream owns what nobody else
    claims."""
    runs = _runs(_streams((REAGENT, 1), (LOW, unknown)))

    assert runs == [[40, 138, 0]]


def test_a_census_with_no_acquisition_parameters_is_still_mapped():
    """Then the edges decide alone."""
    bare = [
        {"key": "low", "signature": {"polarity": "-", "scan_ranges": [[40, 138]]}},
        {"key": "high", "signature": {"polarity": "-", "scan_ranges": [[66, 124]]}},
    ]

    assert _runs(bare) == [[40, 67, 0], [67, 122, 1], [122, 138, 0]]


def test_a_multiplexed_stream_claims_each_of_its_ranges():
    """A multiplexed scan measures several ranges at once, and each is
    trimmed inside its own edges."""
    wide = _stream((100, 600), 1, event=1)
    multiplexed = _stream([(300, 320), (400, 420)], 10, scan_mode="SIM", event=2)

    assert _runs([wide, multiplexed]) == [
        [100, 303, 0],
        [303, 314, 1],
        [314, 404, 0],
        [404, 412, 1],
        [412, 600, 0],
    ]


def test_a_stream_that_states_no_range_owns_nothing_and_the_map_says_so():
    """It cannot be placed on a map. Its peaks are left out of the composite,
    which is not something to do without a word."""
    unplaced = _stream((40, 138), 10, event=3)
    unplaced["signature"]["scan_ranges"] = []

    stitch = m_stitch.stitch_map([*_streams((REAGENT, 1), (LOW, 10)), unplaced])

    assert stitch["runs"] == {"-": [[40, 67, 0], [67, 122, 1], [122, 138, 0]]}
    assert len(stitch["notes"]) == 1
    assert unplaced["key"] in stitch["notes"][0]
    assert "no scan range" in stitch["notes"][0]


def test_a_range_stated_backwards_is_no_range():
    """And its stream is noted like one that states none."""
    stitch = m_stitch.stitch_map(_streams((REAGENT, 1), ((124, 66), 10)))

    assert stitch["runs"] == {"-": [[40, 138, 0]]}
    assert len(stitch["notes"]) == 1
    assert "no scan range" in stitch["notes"][0]


def test_each_polarity_is_stitched_on_its_own():
    """A file that switches polarity and runs two windows in each."""
    streams = [
        *_streams((REAGENT, 1), (LOW, 10)),
        *_streams(((40, 131), 1), ((72, 118), 10), polarity="+"),
    ]

    assert m_stitch.stitch_map(streams)["runs"] == {
        "-": [[40, 67, 0], [67, 122, 1], [122, 138, 0]],
        "+": [[40, 73, 2], [73, 116, 3], [116, 131, 2]],
    }


def test_a_polarity_with_one_stream_has_nothing_to_stitch():
    """A reagent scan in one polarity and the measurement in the other: one
    stream each, and no map for either."""
    streams = [_stream((40, 128), 1, polarity="+"), _stream((40, 600), 10)]

    stitch = m_stitch.stitch_map(streams)

    assert stitch == {
        "rule": m_stitch.STITCH_RULE,
        "runs": {},
        "sources": {},
        "notes": [],
    }


def test_a_map_is_recorded_with_the_rule_that_drew_it_and_survives_json():
    """It is stored as an attribute of a zarr store, so as JSON."""
    stitch = m_stitch.stitch_map(_streams(*SETTLED))

    assert stitch["rule"] == m_stitch.STITCH_RULE == 1
    assert json.loads(json.dumps(stitch)) == stitch


# -- a layout that fixes the map ----------------------------------------------------

# The other chemistry's layout, and the map its site drew for it: the low
# window starts where the reagent ion stops leaking into it, which no rule
# reads off the ranges.
DRAWN_FOR = [((40, 131), 1), ((72, 118), 10, "SIM"), ((132, 460), 10), ((440, 900), 10)]
DRAWN = [
    [40, 75, [40, 131]],
    [75, 116, [72, 118]],
    [116, 133, [40, 131]],
    [133, 444, [132, 460]],
    [444, 900, [440, 900]],
]


def test_a_layout_fixes_the_map_of_a_file_that_holds_its_ranges():
    """Each run names its owner by scan range, and is kept to the m/z that
    range reaches: the drawing gives the reagent scan 116 to 133, and its
    range ends at 131."""
    stitch = m_stitch.stitch_map(_streams(*DRAWN_FOR), layout={"-": DRAWN})

    assert stitch["runs"] == {
        "-": [[40, 75, 0], [75, 116, 1], [116, 131, 0], [133, 444, 2], [444, 900, 3]]
    }
    assert stitch["sources"] == {"-": "layout"}
    assert stitch["notes"] == []


def test_a_layout_naming_a_range_the_file_does_not_hold_is_passed_over():
    """The method was tuned under one name, so a layout can meet a file whose
    low window sits elsewhere. Such a file takes the default map, whole, and
    says why."""
    moved = [((40, 131), 1), ((66, 115), 10, "SIM"), ((132, 460), 10), ((440, 900), 10)]

    stitch = m_stitch.stitch_map(_streams(*moved), layout={"-": DRAWN})

    assert stitch["runs"] == m_stitch.stitch_map(_streams(*moved))["runs"]
    assert stitch["sources"] == {"-": "default"}
    assert len(stitch["notes"]) == 1
    assert "scan range 72-118" in stitch["notes"][0]
    assert "none of the file's streams" in stitch["notes"][0]


def test_a_layout_naming_a_range_two_streams_share_is_passed_over():
    """Settle and measure on one range: a range does not say which of the
    two a run is for."""
    shared = [((40, 600), 1), ((40, 600), 10)]

    stitch = m_stitch.stitch_map(
        _streams(*shared), layout={"-": [[40, 600, [40, 600]]]}
    )

    assert stitch["runs"] == {"-": [[40, 600, 1]]}
    assert stitch["sources"] == {"-": "default"}
    assert "more than one of the file's streams" in stitch["notes"][0]


def test_a_layout_whose_runs_overlap_is_passed_over():
    """It would name two owners for one m/z."""
    overlapping = [[40, 80, [40, 138]], [70, 122, [66, 124]]]

    stitch = m_stitch.stitch_map(
        _streams((REAGENT, 1), (LOW, 10)), layout={"-": overlapping}
    )

    assert stitch["runs"] == {"-": [[40, 67, 0], [67, 122, 1], [122, 138, 0]]}
    assert stitch["sources"] == {"-": "default"}
    assert "overlap at m/z 70" in stitch["notes"][0]


def test_a_layout_fixes_only_the_polarity_it_names():
    streams = [
        *_streams((REAGENT, 1), (LOW, 10)),
        *_streams((REAGENT, 1), (LOW, 10), polarity="+"),
    ]
    layout = {"+": [[40, 70, [40, 138]], [70, 124, [66, 124]]]}

    stitch = m_stitch.stitch_map(streams, layout=layout)

    assert stitch["runs"] == {
        "-": [[40, 67, 0], [67, 122, 1], [122, 138, 0]],
        "+": [[40, 70, 2], [70, 124, 3]],
    }
    assert stitch["sources"] == {"-": "default", "+": "layout"}


def test_a_layout_for_a_polarity_with_one_stream_changes_nothing():
    """There is no composite there for it to fix."""
    stitch = m_stitch.stitch_map(
        [_stream((40, 600), 10)], layout={"-": [[40, 600, [40, 600]]]}
    )

    assert stitch["runs"] == {}
    assert stitch["notes"] == []


# -- the composite mask -------------------------------------------------------------


def _mask(peaks, stitch):
    """The mask of ``(m/z, stream index, polarity)`` peaks."""
    mz, stream, polarity = zip(*peaks)
    return m_stitch.composite_mask(
        np.array(mz), np.array(stream), np.array(polarity), stitch
    ).tolist()


def test_a_peak_is_its_composites_where_its_own_stream_owns_its_mz():
    """Both the reagent scan and the low window record the ion at 80, and
    the low window owns it; both record the one at 125, and that is the
    reagent scan's. The other reading of each stays in the store, and out of
    the composite."""
    stitch = m_stitch.stitch_map(_streams(*SETTLED))

    assert _mask(
        [
            (62.0, 0, "-"),
            (80.0, 0, "-"),
            (80.0, 1, "-"),
            (125.0, 0, "-"),
            (125.0, 1, "-"),
            (300.0, 2, "-"),
            (450.0, 2, "-"),
            (450.0, 3, "-"),
        ],
        stitch,
    ) == [True, False, True, True, False, True, False, True]


def test_a_run_owns_its_lower_edge_and_not_its_upper():
    """The low window owns 67 up to 122, and the reagent scan owns 122."""
    stitch = m_stitch.stitch_map(_streams(*SETTLED))
    below = float(np.nextafter(122.0, 0.0))

    assert _mask(
        [(67.0, 1, "-"), (below, 1, "-"), (122.0, 1, "-"), (122.0, 0, "-")], stitch
    ) == [True, True, False, True]


def test_a_peak_where_the_map_has_a_gap_is_in_no_composite():
    stitch = m_stitch.stitch_map(_streams(((40, 131), 1), ((132, 460), 10)))

    assert _mask([(131.5, 0, "-"), (131.5, 1, "-"), (39.0, 0, "-")], stitch) == [
        False,
        False,
        False,
    ]


def test_every_peak_of_a_polarity_with_one_stream_is_its_composites():
    """So the mask of any per-stream store reads the same way, whether a
    polarity of it is stitched or not."""
    streams = [*_streams((REAGENT, 1), (LOW, 10)), _stream((40, 600), 10, polarity="+")]
    stitch = m_stitch.stitch_map(streams)

    assert _mask(
        [(80.0, 0, "-"), (80.0, 1, "-"), (80.0, 2, "+"), (599.0, 2, "+")], stitch
    ) == [False, True, True, True]


def test_the_owner_of_an_mz_is_a_stream_index_or_none():
    runs = _runs(_streams(((40, 131), 1), ((132, 460), 10)))

    owner = m_stitch.owners(np.array([10.0, 40.0, 130.999, 131.0, 132.0, 460.0]), runs)

    assert owner.tolist() == [-1, 0, 0, -1, 1, -1]
    assert m_stitch.owners(np.array([80.0]), []).tolist() == [-1]
    assert m_stitch.owners(np.array([]), runs).tolist() == []


def test_a_runs_samples_are_those_from_its_lower_edge_up_to_its_upper():
    """A signal is cut where a peak is placed: a sample on a run's lower
    edge is the run's, one on its upper edge the next run's."""
    axis = np.array([66.5, 67.0, 80.0, 121.5, 122.0, 123.0])

    assert m_stitch.owned_slice(axis, 67, 122) == slice(1, 4)
    assert m_stitch.owned_slice(axis, 122, 133) == slice(4, 6)
    assert m_stitch.owned_slice(axis, 200, 300) == slice(6, 6)


# -- a calibrated file --------------------------------------------------------------

# m/z calibrations of a few parts per million. Divided back out of what they
# were multiplied onto, the last two do not return every m/z they were given.
FACTORS = [1 + 3e-6, 1.0000071, 1 - 5e-6, 0.9999950000000002]


def test_a_calibration_moves_a_peak_and_not_the_boundary_it_is_placed_by():
    """The low window's ion at 121.9999 is its composite's: the window owns
    up to 122. Calibrated by five parts per million it is stored at 122.0005,
    and placed by that it would be read as a peak in the reagent scan's run
    that the reagent scan did not record, and dropped."""
    stitch = m_stitch.stitch_map(_streams(*SETTLED))
    factor = 1 + 5e-6
    stored = np.array([121.9999 * factor])
    low, polarity = np.array([1]), np.array(["-"])

    assert stored[0] > 122.0
    assert m_stitch.composite_mask(stored, low, polarity, stitch, factor).tolist() == [
        True
    ]
    assert m_stitch.composite_mask(stored, low, polarity, stitch).tolist() == [False]


@pytest.mark.parametrize("factor", FACTORS)
def test_a_calibrated_mz_is_placed_exactly_as_the_one_the_instrument_recorded(factor):
    """Down to an m/z on a boundary itself, which belongs to the run above.
    The boundaries are multiplied by the factor the values carry. Taking the
    factor off the values instead puts some of them a float below where they
    were, and on the wrong side."""
    runs = _runs(_streams(*SETTLED))
    recorded = np.arange(40.0, 900.0, 0.25)

    placed = m_stitch.owners(recorded * factor, runs, factor)

    assert placed.tolist() == m_stitch.owners(recorded, runs).tolist()


def test_taking_a_calibration_off_an_mz_does_not_always_give_the_mz_back():
    """What the test above rests on."""
    recorded = np.arange(40.0, 900.0)

    returned = [
        int(np.sum(recorded * factor / factor != recorded)) for factor in FACTORS
    ]

    assert returned[:2] == [0, 0]
    assert returned[2] > 0
    assert returned[3] > 0


@pytest.mark.parametrize("factor", FACTORS)
def test_a_calibrated_axis_is_cut_where_the_recorded_one_is(factor):
    axis = np.arange(40.0, 138.25, 0.25)

    for lower, upper in ((40, 67), (67, 122), (122, 133)):
        assert m_stitch.owned_slice(axis * factor, lower, upper, factor) == (
            m_stitch.owned_slice(axis, lower, upper)
        )


@pytest.mark.parametrize(
    ("recorded", "factor"),
    [(121.9999, 1 + 5e-6), (67.0001, 1 - 5e-6)],
    ids=["moved-up-past-122", "moved-down-past-67"],
)
def test_the_overlap_of_a_calibrated_file_is_where_both_streams_claim(recorded, factor):
    """An ion both streams record just inside what both claim, 67 to 122, is
    read wherever the file's calibration has put it since: above 122, or
    below 67."""
    streams = _streams((REAGENT, 1), (LOW, 10))
    mz = np.array([recorded, recorded]) * factor
    arguments = (mz, np.array([0, 1]), np.array([50.0, 70.0]), np.ones(2, bool))

    (placed,) = m_stitch.overlap_readings(
        streams, *arguments, np.array([1, 1]), calibration=factor
    )
    (misplaced,) = m_stitch.overlap_readings(streams, *arguments, np.array([1, 1]))

    assert not 67 <= mz[0] < 122
    assert placed["shared"] == 1
    assert placed["ratio"] == pytest.approx([1.4, 1.4, 1.4])
    assert misplaced["shared"] == 0


# -- the overlap reading ------------------------------------------------------------

# Five reagent scans, three of the low window, four of the mid, two of the
# high: what the settled layout runs.
SCANS = np.array([5, 3, 4, 2])


def _reading(peaks, streams, scans=SCANS, kept=None):
    """The readings of ``(m/z, stream index, summed height)`` peaks."""
    peaks = sorted(peaks)
    mz, stream, heights = (np.array(column) for column in zip(*peaks))
    return m_stitch.overlap_readings(
        streams,
        mz,
        stream,
        heights.astype(float),
        np.ones(mz.size, dtype=bool) if kept is None else np.asarray(kept),
        scans,
    )


def test_a_reading_is_taken_over_the_mz_both_streams_claim():
    """The reagent scan claims 40 to 135 and the low window 67 to 122, so
    they are read against each other from 67 to 122. Both record ions at
    66.5 and at 123 as well, at the edge of the low window, where it is not
    to be trusted."""
    peaks = [
        (mz, stream, 300.0)
        for mz in (66.5, 80.0, 100.0, 121.9, 123.0)
        for stream in (0, 1)
    ]

    (reading,) = _reading(peaks, _streams((REAGENT, 1), (LOW, 10)))

    assert reading["streams"] == [0, 1]
    assert reading["overlap"] == [[67, 122]]
    assert reading["shared"] == 3


def test_the_ratio_is_of_what_each_stream_reads_per_scan():
    """A peak's height is summed over its own stream's scans, five of the
    reagent scan and three of the low window. An ion that reads 100 a scan
    in the first and 137 in the second is 500 against 411."""
    peaks = [(80.0, 0, 500.0), (80.0, 1, 411.0), (100.0, 0, 50.0), (100.0, 1, 41.1)]

    (reading,) = _reading(peaks, _streams((REAGENT, 1), (LOW, 10)))

    assert reading["ratio"] == pytest.approx([1.37, 1.37, 1.37])


def test_a_reading_is_of_the_stream_that_starts_higher_against_the_other():
    """Whichever comes first in the file. The low window, defined last here,
    reads an ion 0.44 ppm below the reagent scan and twice as high."""
    streams = _streams((LOW, 10), (REAGENT, 1))
    lower = 80.0 * (1 - 0.44e-6)

    (reading,) = _reading([(80.0, 1, 100.0), (lower, 0, 200.0)], streams, scans=[1, 1])

    assert reading["streams"] == [1, 0]
    assert reading["ratio"] == pytest.approx([2.0, 2.0, 2.0])
    assert reading["ppm"] == pytest.approx([-0.44, -0.44, -0.44], abs=1e-6)


def test_the_quartiles_are_over_the_ions_both_hold():
    ratios = [1.0, 1.2, 1.3, 1.4, 2.0]
    peaks = [(70.0 + i, 0, 100.0) for i in range(5)]
    peaks += [(70.0 + i, 1, 100.0 * ratio) for i, ratio in enumerate(ratios)]

    (reading,) = _reading(peaks, _streams((REAGENT, 1), (LOW, 10)), scans=[1, 1])

    assert reading["shared"] == 5
    assert reading["ratio"] == pytest.approx([1.2, 1.3, 1.4])
    assert reading["ppm"] == [0.0, 0.0, 0.0]


def test_two_peaks_are_one_ion_within_five_ppm_and_no_further():
    within, beyond = 80.0 * (1 + 4e-6), 100.0 * (1 + 6e-6)
    peaks = [(80.0, 0, 1.0), (within, 1, 1.0), (100.0, 0, 1.0), (beyond, 1, 1.0)]

    (reading,) = _reading(peaks, _streams((REAGENT, 1), (LOW, 10)))

    assert reading["shared"] == 1
    assert reading["ppm"] == pytest.approx([4.0, 4.0, 4.0], abs=1e-6)


@pytest.mark.parametrize("crowded", [0, 1], ids=["the-first", "the-second"])
def test_no_peak_is_paired_twice(crowded):
    """Two peaks of one stream within reach of one peak of the other: it is
    paired with the nearer, and the other is no shared ion. Whichever of the
    two streams holds the pair."""
    alone = 1 - crowded
    nearer, further = 80.0 * (1 + 1e-6), 80.0 * (1 - 3e-6)
    peaks = [(80.0, alone, 100.0), (nearer, crowded, 200.0), (further, crowded, 900.0)]

    (reading,) = _reading(peaks, _streams((REAGENT, 1), (LOW, 10)), scans=[1, 1])

    assert reading["shared"] == 1
    assert reading["ratio"] == pytest.approx([2.0 if crowded else 0.5] * 3)


def test_peaks_a_load_drops_take_no_part():
    """A weak peak or a satellite is not what a stream measured of an ion."""
    peaks = [(80.0, 0, 100.0), (80.0, 1, 100.0), (100.0, 0, 100.0), (100.0, 1, 100.0)]

    (reading,) = _reading(
        peaks, _streams((REAGENT, 1), (LOW, 10)), kept=[True, True, True, False]
    )

    assert reading["shared"] == 1


def test_an_ion_the_two_streams_do_not_measure_alike_is_listed():
    """Most ions of the overlap read 1.3 times higher in the low window. One
    reads twenty times higher there: the window holds something that makes
    it, and the reagent scan does not. It is listed with its ratio, and the
    median is not moved by it."""
    peaks = [(70.0 + i, 0, 100.0) for i in range(9)]
    peaks += [(70.0 + i, 1, 130.0) for i in range(8)] + [(78.0, 1, 2000.0)]

    (reading,) = _reading(peaks, _streams((REAGENT, 1), (LOW, 10)), scans=[1, 1])

    assert reading["ratio"][1] == pytest.approx(1.3)
    assert reading["outlier_count"] == 1
    assert reading["outliers"] == [[78.0, pytest.approx(20.0)]]


def test_an_ion_is_off_where_it_is_more_than_the_window_factor_from_the_median():
    """Three times, above or below: within it two windows of one layout are
    seen to read an ion, and the furthest off is listed first."""
    ratios = [1.0] * 7 + [2.9, 3.5, 1 / 2.9, 1 / 3.2, 40.0]
    peaks = [(70.0 + i, 0, 100.0) for i in range(len(ratios))]
    peaks += [(70.0 + i, 1, 100.0 * ratio) for i, ratio in enumerate(ratios)]

    (reading,) = _reading(peaks, _streams((REAGENT, 1), (LOW, 10)), scans=[1, 1])

    assert m_stitch.WINDOW_FACTOR == 3.0
    assert reading["ratio"][1] == pytest.approx(1.0)
    assert reading["outlier_count"] == 3
    assert [ion for ion, _ratio in reading["outliers"]] == [81.0, 78.0, 80.0]


def test_a_reading_lists_the_furthest_ions_and_counts_them_all():
    """A pair of windows that measure different things would list every ion
    of their overlap into the store's attributes."""
    listed = m_stitch.OUTLIERS_LISTED
    ordinary, off = listed + 6, listed + 5
    peaks = [(67.0 + i, 0, 100.0) for i in range(ordinary + off)]
    peaks += [(67.0 + i, 1, 100.0) for i in range(ordinary)]
    peaks += [(67.0 + ordinary + i, 1, 1000.0 * (i + 1)) for i in range(off)]

    (reading,) = _reading(peaks, _streams((REAGENT, 1), (LOW, 10)), scans=[1, 1])

    assert reading["outlier_count"] == off
    assert len(reading["outliers"]) == listed
    assert reading["outliers"][0] == [
        67.0 + ordinary + off - 1,
        pytest.approx(10.0 * off),
    ]


def test_streams_whose_claims_do_not_meet_are_not_read_against_each_other():
    """The low and the mid window do not overlap at all, and two windows
    that touch claim nothing in common."""
    peaks = [(mz, stream, 1.0) for mz in (100.0, 128.0, 300.0) for stream in (0, 1)]

    assert _reading(peaks, _streams((LOW, 10), (MID, 10))) == []
    assert _reading(peaks, _streams(((40, 128), 1), ((128, 600), 10))) == []


def test_an_overlap_that_holds_no_shared_ion_is_still_a_reading():
    """The reagent scan reaches the mid window over two m/z, 133 to 135, and
    shares nothing with it there. That it does not is what a calibration
    looking for a way across has to know."""
    peaks = [(100.0, 0, 1.0), (134.2, 0, 1.0), (134.7, 2, 1.0), (300.0, 2, 1.0)]

    readings = _reading(peaks, _streams(*SETTLED))

    assert [(r["streams"], r["overlap"], r["shared"]) for r in readings] == [
        ([0, 1], [[67, 122]], 0),
        ([0, 2], [[133, 135]], 0),
        ([2, 3], [[444, 451]], 0),
    ]
    assert all(r["ratio"] is None and r["ppm"] is None for r in readings)
    assert all(r["outliers"] == [] and r["outlier_count"] == 0 for r in readings)


def test_streams_of_two_polarities_are_never_read_against_each_other():
    streams = [_stream(REAGENT, 1, polarity="+"), _stream(LOW, 10)]

    assert _reading([(80.0, 0, 1.0), (80.0, 1, 1.0)], streams, scans=[1, 1]) == []


def test_a_stream_with_no_scan_is_not_read():
    """There is nothing its heights could be divided by."""
    peaks = [(80.0, 0, 1.0), (80.0, 1, 1.0)]

    assert _reading(peaks, _streams((REAGENT, 1), (LOW, 10)), scans=[5, 0]) == []


def test_a_peak_with_no_height_takes_no_part():
    peaks = [(80.0, 0, 0.0), (80.0, 1, 100.0), (100.0, 0, 100.0), (100.0, 1, 130.0)]

    (reading,) = _reading(peaks, _streams((REAGENT, 1), (LOW, 10)), scans=[1, 1])

    assert reading["shared"] == 1
    assert reading["ratio"] == pytest.approx([1.3, 1.3, 1.3])


def test_the_readings_survive_json():
    """They are stored as an attribute of a zarr store too."""
    peaks = [(70.0 + i, 0, 100.0) for i in range(4)]
    peaks += [(70.0 + i, 1, 100.0 * ratio) for i, ratio in enumerate([1, 1, 1, 9])]

    readings = _reading(peaks, _streams(*SETTLED))

    assert json.loads(json.dumps(readings)) == readings
    assert readings[0]["outliers"] == [[73.0, pytest.approx(15.0)]]
