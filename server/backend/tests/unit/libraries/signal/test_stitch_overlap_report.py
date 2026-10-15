"""The report of what stitched files' ranges read where two of them overlap.

Which range owns an m/z two of them measure is decided by a rule of thumb
today - more microscans wins - and decision 17 of the ingest design is to
make it on what a site's files show. Every stitched store records what two
ranges read of the ions both hold; ``report_stitch_overlaps`` reads those
records over a site's newest files and says, per layout and per overlap,
who owns it, how the two read the shared ions, what each holds alone, and
which ions the two do not measure alike
(``docs/dev/ingest_routing_and_splitting.md``, section 4.5).

The reading of one file runs on the scripted composite of
``composite_acquisition``; the summary over files is pure.
"""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from composite_acquisition import COMPOSITE, KEYS, MICROSCANS
from scripted_acquisition import SAMPLE_FILENAME

import mascope_file.io as m_io
import mascope_signal.peak as m_peak
from mascope_backend.db.scripts import report_stitch_overlaps as report
from mascope_backend.db.scripts.report_stitch_overlaps import (
    FileReading,
    PairReading,
    read_file,
    summarise,
)


REAGENT, LOW, MID, HIGH = KEYS
ACQUIRED = datetime(2026, 10, 9, 14, 32, tzinfo=timezone.utc)


def _file(filename=SAMPLE_FILENAME, acquired=ACQUIRED, file_id="sf-1"):
    return SimpleNamespace(
        filename=filename,
        instrument="OrbiTest",
        datetime_utc=acquired,
        sample_file_id=file_id,
    )


# -- one file ----------------------------------------------------------------------


def test_a_stitched_file_is_read_for_each_pair_of_ranges_that_overlap(composite):
    (reading,) = read_file(_file())

    assert (reading.instrument, reading.polarity) == ("OrbiTest", "-")
    assert reading.layout == tuple(KEYS)
    assert reading.acquired == ACQUIRED
    pairs = {(pair.first, pair.second): pair for pair in reading.pairs}
    # The reagent scan and the low window both claim 67 to 122, and the mid
    # and the high window 444 to 451
    assert set(pairs) >= {(REAGENT, LOW), (MID, HIGH)}
    # The map the file is stitched by, its owners by key
    assert reading.runs == (
        (40, 67, REAGENT),
        (67, 122, LOW),
        (122, 133, REAGENT),
        (133, 444, MID),
        (444, 900, HIGH),
    )


def test_a_pair_says_who_owns_the_overlap_and_what_each_reads(composite):
    (reading,) = read_file(_file())
    low = next(pair for pair in reading.pairs if pair.second == LOW)

    # The low window owns what it claims: ten microscans against one
    assert [(owner, lower) for lower, _upper, owner in low.owners] == [(LOW, 67)]
    # They share the ion at 80, which the window reads 14 a scan to the
    # reagent scan's 10, half a ppm higher
    assert low.shared == 1
    assert low.ratio == pytest.approx(1.4)
    assert low.ppm == pytest.approx(0.5, abs=0.01)
    # And the window holds two more ions there, at 100 and 121
    assert (low.kept_first, low.kept_second) == (1, 3)


def test_a_ranges_peaks_are_counted_where_its_own_factor_put_them(
    composite, instrument_functions
):
    """The streams of a file are calibrated each by its own factor. The low
    window's, far larger than any real one, takes its ion at 121 past the
    overlap's upper edge as the instrument recorded it, and the ion is the
    window's all the same: the edge moves with it."""
    m_io.update_props(
        SAMPLE_FILENAME,
        {
            "mz_calibration": {
                "par": {"calibration_factor": 1.0},
                "streams": {LOW: {"calibration_factor": 1.01}},
            }
        },
    )
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions)

    (reading,) = read_file(_file())
    low = next(pair for pair in reading.pairs if pair.second == LOW)

    assert 121.0 * 1.01 > 122
    assert (low.kept_first, low.kept_second) == (1, 3)
    assert low.shared == 1
    assert low.ppm == pytest.approx(0.5, abs=0.01)


def test_a_file_that_stitches_nothing_has_no_reading(acquire, instrument_functions):
    acquire(COMPOSITE, MICROSCANS)

    assert read_file(_file()) == []

    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=False)

    assert read_file(_file()) == []


class _Doctored:
    """A store's group with other attributes; all else is the group's own."""

    def __init__(self, group, attrs):
        self._group, self.attrs = group, attrs

    def __getattr__(self, name):
        return getattr(self._group, name)


def _with_attrs(monkeypatch, change):
    """Read the test file's store with its attributes changed."""
    opened = report.m_io.open_zarr_store

    def doctored(path, *args, **kwargs):
        group = opened(path, *args, **kwargs)
        attrs = dict(group.attrs)
        if "stitch_overlaps" in attrs:
            change(attrs)
        return _Doctored(group, attrs)

    monkeypatch.setattr(report.m_io, "open_zarr_store", doctored)


def test_a_files_reading_of_a_pair_is_its_median(composite, monkeypatch):
    """The store records the quartiles over the ions two ranges share; the
    middle one is what the file says of the pair."""

    def three_ions(attrs):
        attrs["stitch_overlaps"] = [
            {**reading, "ratio": [1.0, 2.0, 3.0], "ppm": [0.1, 0.2, 0.3]}
            for reading in attrs["stitch_overlaps"]
        ]

    _with_attrs(monkeypatch, three_ions)

    (reading,) = read_file(_file())

    assert {(pair.ratio, pair.ppm) for pair in reading.pairs} == {(2.0, 0.2)}


def test_a_peak_a_load_of_the_store_drops_is_not_held(composite, monkeypatch):
    """What a range holds in an overlap is what a sample of it would list:
    a weak peak or a sidelobe is no ion a change of owner gains."""
    load_array = report.m_io.load_array

    def with_a_weak_peak(filename, var):
        store = load_array(filename, var=var).compute()
        weak = store.is_weak.values.copy()
        weak[(store.mz.values > 99.5) & (store.mz.values < 100.5)] = True
        return store.assign(is_weak=("mz", weak))

    monkeypatch.setattr(report.m_io, "load_array", with_a_weak_peak)

    (reading,) = read_file(_file())
    low = next(pair for pair in reading.pairs if pair.second == LOW)

    assert (low.kept_first, low.kept_second) == (1, 2)


def test_a_per_stream_store_with_no_map_has_no_reading(composite, monkeypatch):
    """A stale store: its peaks are detected again when a match meets it,
    and until then it says nothing of its overlaps."""
    _with_attrs(monkeypatch, lambda attrs: attrs.pop("stitch_map"))

    assert read_file(_file()) == []


# -- a layout's files ----------------------------------------------------------------


def _pair(
    ratio, shared=10, kept=(12, 40), outliers=(), ppm=0.4, owners=((67, 122, LOW),)
):
    return PairReading(
        first=REAGENT,
        second=LOW,
        overlap=((67, 122),),
        owners=owners,
        shared=shared,
        ratio=ratio,
        ppm=ppm,
        kept_first=kept[0],
        kept_second=kept[1],
        outliers=tuple(outliers),
    )


def _reading(
    *pairs, layout=(REAGENT, LOW), hour=14, file_id="sf", instrument="A", runs=()
):
    return FileReading(
        instrument=instrument,
        polarity="-",
        layout=layout,
        acquired=datetime(2026, 10, 9, hour, tzinfo=timezone.utc),
        sample_file_id=file_id,
        pairs=tuple(pairs),
        runs=tuple(runs),
    )


def test_a_layouts_overlap_is_the_middle_of_its_files():
    files = [
        _reading(_pair(2.0), hour=14, file_id="old"),
        _reading(_pair(2.4), hour=16, file_id="new"),
        _reading(_pair(2.2), hour=15, file_id="mid"),
    ]

    (layout,) = summarise(files)

    assert (layout["instrument"], layout["polarity"], layout["files"]) == ("A", "-", 3)
    assert layout["layout"] == [REAGENT, LOW]
    assert (layout["first_acquired"].hour, layout["last_acquired"].hour) == (14, 16)
    # The file whose stream rows say how the layout is measured now
    assert layout["newest_file_id"] == "new"
    (pair,) = layout["pairs"]
    assert pair["files"] == 3
    assert pair["ratio"] == pytest.approx([2.1, 2.2, 2.3])
    assert pair["owners"] == ((67, 122, LOW),)
    # What a change of owner would gain and lose: the peaks only one holds
    assert pair["only_first"] == [2.0, 2.0, 2.0]
    assert pair["only_second"] == [30.0, 30.0, 30.0]


def test_a_file_that_shares_no_ion_does_not_count_toward_the_ratio():
    files = [_reading(_pair(None, shared=0, ppm=None)), _reading(_pair(2.0))]

    ((pair,),) = [layout["pairs"] for layout in summarise(files)]

    assert pair["files"] == 2
    assert pair["ratio"] == [2.0, 2.0, 2.0]
    assert pair["shared"] == pytest.approx([2.5, 5.0, 7.5])


def test_an_ion_off_the_ratio_is_named_where_most_files_list_it():
    """One file listing an ion is that file. The layout's ion is the one its
    files keep listing: the ammonia channel, read against a range that holds
    the reagent dimer."""
    ammonia = (78.0651, 0.07)
    files = [
        _reading(_pair(2.0, outliers=[ammonia, (95.1 + n, 9.0)])) for n in range(8)
    ]

    ((pair,),) = [layout["pairs"] for layout in summarise(files)]

    assert pair["off_ratio"] == [{"mz": 78.0651, "files": 8, "ratio": 0.07}]
    assert pair["off_ratio_more"] == 0


def test_an_ion_on_a_rounding_step_is_one_ion():
    """The same ion from file to file within the few ppm two readings of one
    file are, though half its readings round one way and half the other."""
    low, high = 78.06549, 78.06551
    assert round(low, 3) != round(high, 3)
    files = [
        _reading(_pair(2.0, outliers=[(low if n % 2 else high, 0.07)]))
        for n in range(8)
    ]

    ((pair,),) = [layout["pairs"] for layout in summarise(files)]

    assert pair["off_ratio"] == [{"mz": 78.0655, "files": 8, "ratio": 0.07}]


def test_two_ions_further_apart_than_two_readings_of_one_stay_two():
    """Ten ppm apart, each listed by every file."""
    files = [
        _reading(_pair(2.0, outliers=[(78.0650, 0.07), (78.0658, 9.0)]))
        for _ in range(4)
    ]

    ((pair,),) = [layout["pairs"] for layout in summarise(files)]

    assert [(ion["mz"], ion["files"]) for ion in pair["off_ratio"]] == [
        (78.065, 4),
        (78.0658, 4),
    ]


def test_a_run_of_readings_each_near_the_last_is_not_one_ion():
    """Three ions four ppm apart: the first and the last are eight apart,
    which is two ions, whatever lies between them."""
    step = 78.0 * 4e-6
    files = [
        _reading(_pair(2.0, outliers=[(78.0 + n * step, 0.1) for n in range(3)]))
        for _ in range(4)
    ]

    ((pair,),) = [layout["pairs"] for layout in summarise(files)]

    assert len(pair["off_ratio"]) == 2


def test_a_file_counts_once_toward_an_ion_it_lists_twice():
    """One file of eight lists the ion twice, and nobody else does: it is
    one file's, and stays off the list."""
    twice = _reading(_pair(2.0, outliers=[(78.06500, 0.07), (78.06501, 0.08)]))
    files = [twice] + [_reading(_pair(2.0)) for _ in range(7)]

    ((pair,),) = [layout["pairs"] for layout in summarise(files)]

    assert pair["off_ratio"] == []


def test_an_ion_is_counted_by_the_files_that_list_it():
    """Every file lists the ion, and one of them twice: four files, though
    five readings, and the ratio the middle of all of them."""
    files = [_reading(_pair(2.0, outliers=[(78.06500, 0.07)])) for _ in range(3)]
    files.append(_reading(_pair(2.0, outliers=[(78.06500, 0.07), (78.06501, 0.09)])))

    ((pair,),) = [layout["pairs"] for layout in summarise(files)]

    assert pair["off_ratio"] == [{"mz": 78.065, "files": 4, "ratio": 0.07}]


def test_files_of_one_method_stitched_by_two_maps_are_read_apart():
    """The method's microscan counts were changed: its ranges keep their
    keys, and the overlap went from the low window to the reagent scan. Each
    map is listed with its own files and its own owner, the newest file of
    each saying how it is measured."""
    by_low = [(40, 67, REAGENT), (67, 122, LOW), (122, 135, REAGENT)]
    by_reagent = [(40, 135, REAGENT)]
    files = [
        _reading(
            _pair(2.0, owners=((67, 122, LOW),)), runs=by_low, file_id=f"a{n}", hour=10
        )
        for n in range(3)
    ] + [
        _reading(
            _pair(0.4, owners=((67, 122, REAGENT),)),
            runs=by_reagent,
            file_id=f"b{n}",
            hour=20,
        )
        for n in range(2)
    ]

    first, second = summarise(files)

    assert (first["files"], second["files"]) == (3, 2)
    assert first["layout"] == second["layout"] == [REAGENT, LOW]
    assert first["runs"] == [list(run) for run in by_low]
    assert second["runs"] == [list(run) for run in by_reagent]
    assert first["pairs"][0]["owners"] == ((67, 122, LOW),)
    assert second["pairs"][0]["owners"] == ((67, 122, REAGENT),)
    assert (first["pairs"][0]["files"], second["pairs"][0]["files"]) == (3, 2)
    assert second["newest_file_id"].startswith("b")
    assert second["pairs"][0]["ratio"] == [0.4, 0.4, 0.4]


def test_layouts_are_told_apart_and_the_busiest_comes_first():
    """A window's edge moved between two days is two layouts, and so is the
    same method on another instrument."""
    moved = "FTMS - p NSI Full ms [72.0000-124.0000] R=120000"
    files = (
        [_reading(_pair(2.0))] * 2
        + [_reading(_pair(2.0), layout=(REAGENT, moved))] * 5
        + [_reading(_pair(2.0), instrument="B")]
    )

    summary = summarise(files)

    assert [(entry["instrument"], entry["files"]) for entry in summary] == [
        ("A", 5),
        ("A", 2),
        ("B", 1),
    ]
    assert summary[0]["layout"] == [REAGENT, moved]


def test_a_layout_none_of_whose_ranges_overlap_is_still_listed():
    (layout,) = summarise([_reading()])

    assert (layout["files"], layout["pairs"]) == (1, [])


# -- the walk ------------------------------------------------------------------------


def test_one_file_that_cannot_be_read_costs_the_others_nothing(composite, monkeypatch):
    files = [_file(file_id="good"), _file(filename="gone", file_id="gone")]

    async def pages(_limit):
        yield files

    def reading(sample_file):
        if sample_file.filename == "gone":
            raise OSError("the filestore lost it")
        return read_file(sample_file)

    monkeypatch.setattr(report, "_file_pages", pages)
    monkeypatch.setattr(report, "read_file", reading)

    walked = asyncio.run(report.walk(10))

    assert (walked.files, walked.stitched) == (2, 1)
    assert dict(walked.unreadable) == {"OSError": 1}
    assert [entry.sample_file_id for entry in walked.readings] == ["good"]


def test_the_report_says_what_it_read(composite, monkeypatch):
    async def pages(_limit):
        yield [_file()]

    async def settings(_sample_file_id):
        return {REAGENT: "5 scans, 1 microscan", LOW: "3 scans, 10 microscans"}

    lines = []
    monkeypatch.setattr(report, "_file_pages", pages)
    monkeypatch.setattr(report, "_stream_settings", settings)
    monkeypatch.setattr(report.runtime.logger, "info", lines.append)

    summary = asyncio.run(report.stitch_overlap_report(10))

    said = "\n".join(lines)
    assert "read 1 raw Orbitrap file(s)" in said and "1 of them are stitched" in said
    assert "range m/z 40-138 [5 scans, 1 microscan]" in said
    assert (
        "stitched: m/z 40-67 from m/z 40-138; m/z 67-122 from m/z 66-124; "
        "m/z 122-133 from m/z 40-138; m/z 133-444 from m/z 132-460; "
        "m/z 444-900 from m/z 440-900"
    ) in said
    assert "m/z 40-138 and m/z 66-124 overlap over m/z 67-122" in said
    assert "owned 67-122 by m/z 66-124" in said
    assert "m/z 66-124 reads them at 1.40 (1.40 to 1.40) of m/z 40-138" in said
    assert [layout["files"] for layout in summary] == [1]


def test_a_site_with_no_stitched_file_is_told_why(monkeypatch):
    async def pages(_limit):
        yield []

    lines = []
    monkeypatch.setattr(report, "_file_pages", pages)
    monkeypatch.setattr(report.runtime.logger, "info", lines.append)

    assert asyncio.run(report.stitch_overlap_report(10)) == []
    assert "composite_scan_streams" in "\n".join(lines)


# -- its bounds, and its promise ---------------------------------------------------------


def test_the_report_declares_that_it_writes_nothing():
    assert report.WRITES_NOTHING is True


@pytest.mark.parametrize("value", ["2O00", "0", "-5"])
def test_a_bound_that_cannot_be_read_stops_the_run(monkeypatch, value):
    monkeypatch.setenv("STITCH_REPORT_FILES", value)

    with pytest.raises(ValueError, match="STITCH_REPORT_FILES"):
        report._int_env("STITCH_REPORT_FILES", 5000, 1)


def test_an_unset_bound_is_the_default(monkeypatch):
    monkeypatch.delenv("STITCH_REPORT_FILES", raising=False)

    assert report._int_env("STITCH_REPORT_FILES", 5000, 1) == 5000
