"""Reading one scan stream of a sample file (``mascope_signal.compute``).

A raw Orbitrap file is read scan by scan, so its reads can be narrowed to the
scans of one experiment of its method (``mascope_thermo.streams``). The other
sample types hold no streams: a TofDaq file is one acquisition on one mass
axis, and a zarr file keeps a stored signal. What is pinned here is that each
read hands the stream on to the reader, that none of them quietly ignores one
asked of a file that has none, and that a stream's sum signal is cached apart
from every signal cached before streams could be read apart.
"""

import asyncio
import os

import numpy as np
import pytest
import xarray as xr
from signal_test_support import SIGNAL_TEST_FILENAME

import mascope_file.name as m_name
import mascope_signal.compute as m_compute
from mascope_thermo.thermo import UnknownStreamError


STREAM = "FTMS - p NSI Full ms [40.0000-160.0000] R=120000 event=2"
OTHER_STREAM = "FTMS - p NSI Full ms [40.0000-160.0000] R=120000 event=1"


@pytest.fixture
def raw_orbitrap(monkeypatch, sample_file_path):
    """The test sample read as a raw Orbitrap file, with a reader that records
    what each read was asked for and answers with one scan."""
    monkeypatch.setattr(m_compute.m_name, "get_sample_file_type", lambda _: "orbi_raw")
    asked: dict[str, dict] = {}

    def _reader(name, answer):
        def read(datafile_path, *args, **kwargs):
            asked[name] = {"args": args, **kwargs}
            return answer

        return read

    profile = xr.DataArray(
        np.array([1.0, 2.0, 3.0]),
        dims=["mz"],
        coords={"mz": np.array([100.0, 101.0, 102.0])},
        name="sum_signal",
    )
    signal = xr.Dataset(
        {"signal": (("mz", "time"), np.ones((3, 1)))},
        coords={"mz": np.array([100.0, 101.0, 102.0]), "time": np.array([0.0])},
    )
    timeseries = xr.DataArray(
        np.ones((1, 1)),
        dims=("mz", "time"),
        coords={"mz": np.array([100.0]), "time": np.array([0.0])},
        name="signal",
    )
    for name, answer in {
        "get_scan_timestamps": np.array([0.0, 1.0]),
        "compute_sum_signal": (profile, 1),
        "get_signal": signal,
        "get_tic_per_scan": (np.array([0.0]), np.array([5.0])),
        "get_centroids": (np.array([100.0]),) * 4,
        "get_centroids_per_scan": [],
        "get_peak_timeseries": timeseries,
    }.items():
        monkeypatch.setattr(m_compute.m_thermo, name, _reader(name, answer))
    return asked


def _stream_asked(asked: dict) -> object:
    """The stream a recorded read was asked for, however it was passed."""
    if "stream" in asked:
        return asked["stream"]
    # The reads that take it positionally name it last.
    return asked["args"][-1]


# One entry per read: how to call it, and which reader call it makes.
_READS = {
    "get_scan_timestamps": (
        lambda **kw: m_compute.get_scan_timestamps(SIGNAL_TEST_FILENAME, **kw),
        "get_scan_timestamps",
    ),
    "get_sum_signal": (
        lambda **kw: m_compute.get_sum_signal(SIGNAL_TEST_FILENAME, **kw),
        "compute_sum_signal",
    ),
    "load_signal": (
        lambda **kw: m_compute.load_signal(SIGNAL_TEST_FILENAME, **kw),
        "get_signal",
    ),
    "get_tic_per_scan": (
        lambda **kw: m_compute.get_tic_per_scan(SIGNAL_TEST_FILENAME, **kw),
        "get_tic_per_scan",
    ),
    "get_acquisition_window": (
        lambda **kw: m_compute.get_acquisition_window(SIGNAL_TEST_FILENAME, **kw),
        "get_scan_timestamps",
    ),
    "get_orbi_centroids": (
        lambda **kw: asyncio.run(
            m_compute.get_orbi_centroids(SIGNAL_TEST_FILENAME, **kw)
        ),
        "get_centroids",
    ),
    "get_orbi_centroids_per_scan": (
        lambda **kw: m_compute.get_orbi_centroids_per_scan(SIGNAL_TEST_FILENAME, **kw),
        "get_centroids_per_scan",
    ),
    "get_peak_timeseries": (
        lambda **kw: asyncio.run(
            m_compute.get_peak_timeseries(SIGNAL_TEST_FILENAME, [100.0], **kw)
        ),
        "get_peak_timeseries",
    ),
}


@pytest.mark.parametrize("read", list(_READS))
def test_each_read_hands_the_stream_to_the_reader(raw_orbitrap, read):
    call, reader_call = _READS[read]

    call(stream=STREAM)

    assert _stream_asked(raw_orbitrap[reader_call]) == STREAM


@pytest.mark.parametrize("read", list(_READS))
def test_a_read_given_no_stream_asks_the_reader_for_none(raw_orbitrap, read):
    call, reader_call = _READS[read]

    call()

    asked = raw_orbitrap[reader_call]
    # Asked for nothing, or not mentioned at all: either way every stream.
    assert asked.get("stream") is None
    assert STREAM not in asked["args"]


# The reads a sample type without streams can answer at all. Centroids are
# refused for them whatever is asked.
_READS_OF_ANY_TYPE = [
    "get_scan_timestamps",
    "get_sum_signal",
    "load_signal",
    "get_tic_per_scan",
    "get_acquisition_window",
    "get_peak_timeseries",
]


@pytest.mark.parametrize("sample_type", ["tof_h5", "tof_zarr", "orbi_zarr"])
@pytest.mark.parametrize("read", _READS_OF_ANY_TYPE)
def test_a_stream_is_refused_for_a_file_that_has_none(
    monkeypatch, sample_file_path, sample_type, read
):
    """Ignoring it would hand back every scan as if they were the stream's."""
    monkeypatch.setattr(m_compute.m_name, "get_sample_file_type", lambda _: sample_type)
    call, _reader_call = _READS[read]

    with pytest.raises(ValueError, match="has no scan streams"):
        call(stream=STREAM)


def test_a_refused_stream_is_not_answered_with_an_empty_signal(
    monkeypatch, sample_file_path
):
    """``load_signal`` answers an empty range with an empty dataset, and must
    not answer this the same way: the caller would read "no data"."""
    monkeypatch.setattr(m_compute.m_name, "get_sample_file_type", lambda _: "tof_zarr")

    with pytest.raises(ValueError):
        m_compute.load_signal(SIGNAL_TEST_FILENAME, stream=STREAM)


@pytest.mark.parametrize("read", list(_READS))
def test_a_key_the_file_does_not_hold_is_refused_by_every_read(
    raw_orbitrap, monkeypatch, read
):
    """The reader refuses a key the file holds no stream under, and no read
    above it turns the refusal into an answer. ``load_signal`` is the one that
    would: it answers the reader's other failures with an empty signal, and an
    empty signal of a stream that is not there reads "no data"."""
    call, reader_call = _READS[read]

    def refuses(datafile_path, *args, **kwargs):
        raise UnknownStreamError(STREAM, [OTHER_STREAM])

    monkeypatch.setattr(m_compute.m_thermo, reader_call, refuses)

    with pytest.raises(UnknownStreamError) as raised:
        call(stream=STREAM)

    assert raised.value.stream == STREAM
    assert raised.value.held == [OTHER_STREAM]


def test_a_reader_fault_is_still_answered_with_an_empty_signal(
    raw_orbitrap, monkeypatch
):
    """Only the unknown key is let through. Whatever else the reader raises,
    ``load_signal`` answers as it always has."""

    def fails(datafile_path, *args, **kwargs):
        raise OSError("the raw file could not be read")

    monkeypatch.setattr(m_compute.m_thermo, "get_signal", fails)

    signal = m_compute.load_signal(SIGNAL_TEST_FILENAME, stream=STREAM)

    assert signal.signal.shape == (0, 0)


# -- the sum signal cache ----------------------------------------------------------


def test_a_signal_cached_before_streams_keeps_its_name():
    """The stream joins the cache key only when one is asked for, so no stored
    signal is orphaned and averaged again."""
    for arguments in [(None, None, None), (0.0, 2.0, "+"), (None, None, "-")]:
        assert m_compute._get_sum_signal_hash_name(
            *arguments, "tof_h5", None
        ) == m_compute._get_sum_signal_hash_name(*arguments, "tof_h5")
    # The names themselves, as they have always been.
    assert m_compute._get_sum_signal_hash_name(None, None, None, "tof_h5") == (
        "sum_signal"
    )
    assert m_compute._get_sum_signal_hash_name(0.0, 2.0, "+", "tof_h5") == (
        "sum_signal_ced4dea1859e"
    )


def test_each_stream_has_a_cache_name_of_its_own():
    whole = m_compute._get_sum_signal_hash_name(None, None, None, "tof_h5")
    polarity = m_compute._get_sum_signal_hash_name(None, None, "-", "tof_h5")
    one = m_compute._get_sum_signal_hash_name(None, None, "-", "tof_h5", STREAM)
    other = m_compute._get_sum_signal_hash_name(None, None, "-", "tof_h5", OTHER_STREAM)
    bare = m_compute._get_sum_signal_hash_name(None, None, None, "tof_h5", STREAM)

    # A stream of the whole file is not the whole file's signal.
    assert len({whole, polarity, one, other, bare}) == 5


def test_a_streams_sum_signal_is_cached_apart_from_the_files(raw_orbitrap, monkeypatch):
    """Read twice, each is averaged once: a stream is served from its own
    cache and never from the file's."""
    computed = []

    def compute_sum_signal(datafile_path, **kwargs):
        computed.append(kwargs["stream"])
        height = 1.0 if kwargs["stream"] is None else 7.0
        return (
            xr.DataArray(
                np.full(3, height),
                dims=["mz"],
                coords={"mz": np.array([100.0, 101.0, 102.0])},
                name="sum_signal",
            ),
            1,
        )

    monkeypatch.setattr(m_compute.m_thermo, "compute_sum_signal", compute_sum_signal)

    for _ in range(2):
        whole = m_compute.get_sum_signal(SIGNAL_TEST_FILENAME)
        one = m_compute.get_sum_signal(SIGNAL_TEST_FILENAME, stream=STREAM)

    np.testing.assert_allclose(whole.compute().values, [1.0, 1.0, 1.0])
    np.testing.assert_allclose(one.compute().values, [7.0, 7.0, 7.0])
    assert computed == [None, STREAM]
    name = m_compute._get_sum_signal_hash_name(None, None, None, "orbi_raw", STREAM)
    assert os.path.exists(m_name.filename_to_zarr_path(SIGNAL_TEST_FILENAME, name))


def test_a_streams_average_divides_by_its_own_scans(raw_orbitrap, monkeypatch):
    """An averaged signal divides by the scans selected, and a stream's are
    fewer than its polarity's: divided by the polarity's count, an ion one
    stream alone measures would be diluted, which is what pooling did."""
    scans = {None: 10, STREAM: 4}
    monkeypatch.setattr(
        m_compute.m_thermo,
        "get_scan_timestamps",
        lambda datafile_path, *args, stream=None, **kwargs: np.zeros(scans[stream]),
    )
    monkeypatch.setattr(
        m_compute.m_thermo,
        "compute_sum_signal",
        lambda datafile_path, **kwargs: (
            xr.DataArray(
                np.full(3, 40.0),
                dims=["mz"],
                coords={"mz": np.array([100.0, 101.0, 102.0])},
                name="sum_signal",
            ),
            1,
        ),
    )

    averaged = m_compute.get_sum_signal(
        SIGNAL_TEST_FILENAME, stream=STREAM, average=True
    )

    np.testing.assert_allclose(averaged.compute().values, [10.0, 10.0, 10.0])


def test_a_streams_window_is_its_own_first_and_last_scan(raw_orbitrap, monkeypatch):
    monkeypatch.setattr(
        m_compute.m_thermo,
        "get_scan_timestamps",
        lambda datafile_path, **kwargs: (
            np.array([120.0, 130.0, 140.0])
            if kwargs.get("stream") == STREAM
            else np.array([0.0, 60.0, 120.0, 130.0, 140.0, 300.0])
        ),
    )

    assert m_compute.get_acquisition_window(SIGNAL_TEST_FILENAME, "-") == (0.0, 300.0)
    assert m_compute.get_acquisition_window(
        SIGNAL_TEST_FILENAME, "-", stream=STREAM
    ) == (120.0, 140.0)
