"""The spectrum and the peak listing say which segment each reading is of.

A stitched spectrum is several scan streams' signals laid end to end, each
within the m/z it owns, with nothing rescaled where two meet: it can step at
a boundary, and a peak's intensity is what its own stream measured. So the
sample spectrum returns the map's runs with where each one's samples are,
and the peak listing each peak's segment
(``samples.lib.samples_segments``;
``docs/dev/ingest_routing_and_splitting.md``, section 4.5). A sample whose
store stitches nothing answers as it did.

On the scripted composite of ``composite_acquisition``: a reagent scan that
owns the bottom of the range and the band between the low and the mid
window, then the low, the mid and the high window.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest
from composite_acquisition import (
    COMPOSITE,
    KEYS,
    MICROSCANS,
    POSITIVE,
    RUNS,
    record_calibration,
)
from scripted_acquisition import SAMPLE_FILENAME

import mascope_signal.compute as m_compute
import mascope_signal.peak as m_peak
from mascope_backend.api.controllers.samples import samples_controller
from mascope_backend.api.controllers.samples.lib import samples_segments
from mascope_backend.api.controllers.samples.lib.samples_segments import (
    read_sample_segments,
    segment_label,
)
from mascope_signal.compute import StalePeakStoreError


LABELS = ["m/z 40-138", "m/z 66-124", "m/z 132-460", "m/z 440-900"]


def _sample(polarity="-"):
    return SimpleNamespace(
        sample_item_id="si-1",
        sample_item_name="composite",
        sample_file_id="sf-1",
        filename=SAMPLE_FILENAME,
        instrument="OrbiTest",
        instrument_type="orbi",
        polarity=polarity,
        t0=0.0,
        t1=20.0,
    )


@pytest.fixture
def asked(monkeypatch):
    """Ask the two controllers for the test sample, with no stream rows for
    its file unless a test gives some."""
    rows = {}

    async def with_rows(sample_file_id, segments):
        return [
            {**segment, **rows.get(segment["key"], {"scans": None, "microscans": None})}
            for segment in segments
        ]

    monkeypatch.setattr(samples_controller, "with_stream_rows", with_rows)

    async def _ask(controller, polarity="-", **params):
        with patch.object(
            samples_controller,
            "fetch_sample",
            AsyncMock(return_value=_sample(polarity)),
        ):
            return (await controller("si-1", **params))["data"]

    _ask.rows = rows
    return _ask


@pytest.fixture
def pooled(acquire, instrument_functions):
    """The same file detected whole, as every file is with the setting off."""
    acquire(COMPOSITE, MICROSCANS)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=False)


# -- how a segment is named ---------------------------------------------------------


@pytest.mark.parametrize(
    "key, label",
    [
        ("FTMS - p NSI Full ms [40.0000-138.0000] R=120000", "m/z 40-138"),
        ("FTMS + p NSI SIM ms [72.0000-118.0000] R=120000", "m/z 72-118, SIM"),
        (
            "FTMS - p NSI Full ms [40.0000-600.0000] R=120000 event=2",
            "m/z 40-600, event 2",
        ),
        ("FTMS - p NSI Full ms [66.5000-124.2500] R=60000", "m/z 66.5-124.25"),
        ("a key that states no range", "a key that states no range"),
    ],
)
def test_a_segment_is_named_by_its_scan_range(key, label):
    assert segment_label(key) == label


# -- what a sample's composite is made of ------------------------------------------


def test_a_stitched_polarity_names_its_streams_and_its_runs(composite):
    segments = read_sample_segments(SAMPLE_FILENAME, "-")

    assert [segment["index"] for segment in segments.segments] == [0, 1, 2, 3]
    assert [segment["key"] for segment in segments.segments] == KEYS
    assert [segment["label"] for segment in segments.segments] == LABELS
    assert segments.runs == RUNS
    assert segments.boundaries() == [
        {"segment": index, "mz_lower": float(lower), "mz_upper": float(upper)}
        for lower, upper, index in RUNS
    ]


def test_the_boundaries_are_on_the_files_own_axis(composite):
    """The map is in m/z as the instrument recorded them, and the spectrum
    it is drawn over is on the calibrated axis."""
    factor = 1 + 5e-6
    record_calibration(factor)

    boundaries = read_sample_segments(SAMPLE_FILENAME, "-").boundaries()

    assert boundaries[1]["mz_lower"] == 67 * factor
    assert boundaries[1]["mz_upper"] == 122 * factor


@pytest.mark.parametrize("per_stream", [False, None], ids=["pooled", "no-store"])
def test_a_file_that_stitches_nothing_has_no_segments(
    acquire, instrument_functions, per_stream
):
    acquire(COMPOSITE, MICROSCANS)
    if per_stream is not None:
        m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=False)

    assert read_sample_segments(SAMPLE_FILENAME, "-") is None
    assert m_compute.peak_store_composite(SAMPLE_FILENAME, "-") is None


def test_a_polarity_with_one_stream_has_no_segments(acquire, instrument_functions):
    acquire(COMPOSITE + [(POSITIVE, 5, {59.0: 70.0})] * 2, {**MICROSCANS, 5: 10})
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)

    assert read_sample_segments(SAMPLE_FILENAME, "+") is None
    assert read_sample_segments(SAMPLE_FILENAME, "-") is not None


def test_a_stream_that_owns_nothing_is_no_segment(acquire, instrument_functions):
    """A method that settles the source with a short scan and then measures
    over the same range: the measurement owns the whole range, and the
    settling scans are part of no spectrum a person reads."""
    settle_then_measure = "FTMS - p NSI Full ms [40.0000-600.0000]"
    acquire(
        [(settle_then_measure, 1, {62.0: 100.0})] * 2
        + [(settle_then_measure, 2, {62.0: 1000.0, 188.0: 50.0})] * 4,
        {1: 1, 2: 10},
    )
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)

    segments = read_sample_segments(SAMPLE_FILENAME, "-")

    assert [(segment["index"], segment["label"]) for segment in segments.segments] == [
        (1, "m/z 40-600, event 2")
    ]
    assert [run[2] for run in segments.runs] == [1]


# -- the spectrum ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_spectrum_returns_its_runs_and_where_their_samples_are(
    composite, asked
):
    data = await asked(samples_controller.get_sample_spectrum)

    mz = np.array(data["mz"])
    assert [segment["label"] for segment in data["segments"]] == LABELS
    assert [
        (run["segment"], run["mz_lower"], run["mz_upper"]) for run in data["runs"]
    ] == [(index, lower, upper) for lower, upper, index in RUNS]
    # Every sample is in exactly one run, in order, and inside its edges
    assert data["runs"][0]["from"] == 0
    assert data["runs"][-1]["to"] == mz.size
    for run, after in zip(data["runs"], data["runs"][1:]):
        assert run["to"] == after["from"]
    for run in data["runs"]:
        own = mz[run["from"] : run["to"]]
        assert own.size
        assert (own >= run["mz_lower"]).all() and (own < run["mz_upper"]).all()


@pytest.mark.asyncio
async def test_a_run_is_the_level_of_its_own_stream(composite, asked):
    """What the boundaries are for: the signal steps from one stream's level
    to the next one's where the runs meet, and nowhere else."""
    data = await asked(samples_controller.get_sample_spectrum)

    intensity = np.array(data["intensity"])
    levels = [
        set(np.round(intensity[run["from"] : run["to"]], 6)) for run in data["runs"]
    ]
    assert all(len(level) == 1 for level in levels)
    # The reagent scan owns two runs, and reads the same in both
    assert levels[0] == levels[2]
    assert len({next(iter(level)) for level in levels}) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("factor", [1 + 5e-6, 1 - 5e-6], ids=["above", "below"])
async def test_a_calibrated_files_runs_hold_their_own_streams_samples(
    composite, asked, factor
):
    """The signal is cut where the map's boundaries fall on the file's
    calibrated axis, so that is where its runs are found again: placed by
    the boundaries as the instrument recorded them, a run would take the
    sample at its edge from the stream beside it, or lose its own to it."""
    record_calibration(factor)

    data = await asked(samples_controller.get_sample_spectrum)

    intensity = np.array(data["intensity"])
    for run in data["runs"]:
        assert len(set(np.round(intensity[run["from"] : run["to"]], 6))) == 1, run


@pytest.mark.asyncio
async def test_a_part_of_the_spectrum_places_its_runs_on_that_part(composite, asked):
    data = await asked(
        samples_controller.get_sample_spectrum, mz_min=100.0, mz_max=140.0
    )

    mz = np.array(data["mz"])
    placed = [(run["segment"], run["from"], run["to"]) for run in data["runs"]]
    # Nothing below 100 is answered, so the first run holds none of it, and
    # the high window none at all
    assert placed[0] == (0, 0, 0)
    assert placed[-1][1] == placed[-1][2] == mz.size
    assert placed[1][1] == 0 and mz[placed[1][2]] >= 122
    assert mz[placed[3][1]] >= 133


@pytest.mark.asyncio
async def test_a_segment_carries_what_its_files_stream_row_says(composite, asked):
    asked.rows[KEYS[0]] = {"scans": 5, "microscans": 1.0}

    data = await asked(samples_controller.get_sample_spectrum)

    assert (data["segments"][0]["scans"], data["segments"][0]["microscans"]) == (5, 1.0)
    assert (data["segments"][1]["scans"], data["segments"][1]["microscans"]) == (
        None,
        None,
    )


@pytest.mark.asyncio
async def test_a_spectrum_of_one_stream_is_answered_as_before(pooled, asked):
    data = await asked(samples_controller.get_sample_spectrum)

    assert data["mz"] and len(data["mz"]) == len(data["intensity"])
    assert (data["segments"], data["runs"]) == (None, None)


# -- the peak listing -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_listing_returns_each_peaks_segment(composite, asked):
    data = await asked(samples_controller.get_sample_peaks)

    by_mz = dict(zip(np.round(data["mz"], 3), data["segment"]))
    # The reagent ion and the dimer from the reagent scan, the ion both low
    # streams read from the low window that owns it, the rest from their own
    assert by_mz[62.0] == 0
    assert by_mz[125.0] == 0
    assert by_mz[80.0] == 1
    assert by_mz[100.0] == 1
    assert by_mz[300.0] == 2
    assert by_mz[700.0] == 3
    assert len(data["segment"]) == len(data["mz"])
    assert [segment["label"] for segment in data["segments"]] == LABELS


@pytest.mark.asyncio
async def test_a_listing_of_one_stream_names_no_segment(pooled, asked):
    data = await asked(samples_controller.get_sample_peaks)

    assert data["mz"]
    assert (data["segment"], data["segments"]) == (None, None)


@pytest.mark.asyncio
async def test_an_empty_listing_of_a_stitched_sample_still_names_its_segments(
    composite, asked
):
    data = await asked(
        samples_controller.get_sample_peaks, mz_min=1000.0, mz_max=1100.0
    )

    assert (data["mz"], data["segment"]) == ([], [])
    assert [segment["label"] for segment in data["segments"]] == LABELS


@pytest.mark.asyncio
async def test_a_store_that_cannot_say_its_segments_is_still_listed(
    composite, asked, monkeypatch
):
    """The listing answered for a store with no map before it named
    segments, and still does: what is wrong with the store is said by the
    reads that refuse it."""

    def no_map(filename, polarity):
        raise StalePeakStoreError("The peak store carries no stitch map.")

    monkeypatch.setattr(samples_controller, "read_sample_segments", no_map)

    data = await asked(samples_controller.get_sample_peaks)

    assert data["mz"]
    assert (data["segment"], data["segments"]) == (None, None)


# -- the file's stream rows -------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_segment_reads_its_scans_and_microscans_off_its_row(monkeypatch):
    rows = [
        SimpleNamespace(
            stream_key="reagent",
            scan_count=5,
            acquisition_params={"constant": {"Micro Scan Count:": 1}},
        ),
        SimpleNamespace(stream_key="window", scan_count=3, acquisition_params=None),
    ]

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def execute(self, _statement):
            return SimpleNamespace(scalars=lambda: iter(rows))

    monkeypatch.setattr(samples_segments, "async_session", _Session)

    described = await samples_segments.with_stream_rows(
        "sf-1",
        [
            {"index": 0, "key": "reagent", "label": "r"},
            {"index": 1, "key": "window", "label": "w"},
            {"index": 2, "key": "no row", "label": "n"},
        ],
    )

    assert [(entry["scans"], entry["microscans"]) for entry in described] == [
        (5, 1.0),
        (3, None),
        (None, None),
    ]
    assert [entry["label"] for entry in described] == ["r", "w", "n"]
