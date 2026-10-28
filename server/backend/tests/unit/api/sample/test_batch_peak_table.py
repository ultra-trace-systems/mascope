"""
A batch's peak lists in one read are the per-sample reads, stacked.

``GET /api/batch-peaks/batch/{sample_batch_id}/peaks`` answers one Parquet
table for every sample of a batch. A client that used to fan out one
``GET /api/samples/{sample_item_id}/peaks`` per sample has to get the same
rows from it - the same peaks, the same values, the sample's own polarity -
or switching to it would change every batch analysis built on it. So each
sample here is read both ways from the same store and the two compared.

What a read says beyond its rows rides in the file's metadata: a sample whose
read failed is named there and the batch is answered without it, as a fan-out
reported a failed sample; a stitched sample's segments are listed per sample.
"""

import datetime as dt
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import numpy as np
import pyarrow.parquet as pq
import pytest
import xarray as xr

from mascope_backend.api.controllers.samples import batch_peak_table, samples_controller
from mascope_backend.api.controllers.samples.lib import samples_peaks
from mascope_backend.api.models.samples.sample_pydantic_model import (
    GetSamplePeaksQueryParams,
)
from mascope_backend.api.new.peak_assignments import batch_peaks_routes
from mascope_backend.api.routes.samples.samples_routes import get_sample_peaks_route


TIME = [0.0, 1.0, 2.0]
USER = SimpleNamespace(to_dict=lambda: {"id": "user_1"})

#: Per file: its peaks as (m/z, polarity, height per scan, weak, satellite).
STORES = {
    "file_a": [
        (100.0, "+", 3.0, False, False),
        (150.0, "+", 5.0, False, True),
        (200.0, "-", 7.0, False, False),
        (250.0, "+", 9.0, True, False),
        (300.0, "+", 11.0, False, False),
    ],
    "file_b": [
        (120.0, "+", 2.0, False, False),
        (220.0, "+", 4.0, False, False),
    ],
}
SIGNAL_TO_NOISE = {"file_a": [10.0, 4.0, np.nan, 1.0, np.inf], "file_b": None}


def store(filename: str) -> xr.Dataset:
    """A peak store as it is on disk: weak and satellite peaks included."""
    peaks = STORES[filename]
    mzs = [p[0] for p in peaks]
    heights = np.array([[p[2]] * len(TIME) for p in peaks])
    data = {
        "peak_heights": (("mz", "time"), heights),
        "peak_areas": (("mz", "time"), heights * 2),
        "sum_peak_heights": ("mz", heights.sum(axis=1)),
        "sum_peak_areas": ("mz", heights.sum(axis=1) * 2),
        "sparsity": ("mz", np.linspace(0.1, 0.5, len(mzs))),
        "is_timeseries_computed": ("mz", np.ones(len(mzs), dtype=bool)),
        "polarity": ("mz", np.array([p[1] for p in peaks])),
        "is_weak": ("mz", np.array([p[3] for p in peaks])),
        "is_satellite": ("mz", np.array([p[4] for p in peaks])),
    }
    if SIGNAL_TO_NOISE[filename] is not None:
        data["signal_to_noise"] = ("mz", np.array(SIGNAL_TO_NOISE[filename]))
    return xr.Dataset(
        data,
        coords={
            "mz": mzs,
            "time": TIME,
            "peak_id": ("mz", [f"{filename}_{int(mz)}" for mz in mzs]),
        },
    )


def load_peak_data(filename: str, drop_bad_peaks: bool = True, composite=True):
    """``mascope_file.io.load_peak_data`` over :func:`store`."""
    if filename not in STORES:
        raise FileNotFoundError(filename)
    data = store(filename)
    if drop_bad_peaks:
        bad = data.is_weak.values | data.is_satellite.values
        data = data.isel(mz=np.flatnonzero(~bad))
    return data


def sample(item_id: str, filename: str, polarity: str = "+", hour: int = 0):
    return SimpleNamespace(
        sample_item_id=item_id,
        sample_item_name=f"Sample {item_id}",
        sample_file_id=f"sf_{item_id}",
        filename=filename,
        polarity=polarity,
        t0=TIME[0],
        t1=TIME[-1],
        datetime_utc=dt.datetime(2026, 1, 1, hour, tzinfo=dt.UTC),
        instrument="instrument X",
        instrument_type="orbitrap",
    )


SAMPLES = [
    sample("si_1", "file_a", "+", hour=1),
    sample("si_2", "file_a", "-", hour=2),
    sample("si_3", "file_b", "+", hour=3),
]


@pytest.fixture
def stores():
    """Every read here goes to the stores above, and no sample is stitched."""
    with (
        patch.object(samples_peaks, "load_peak_data", side_effect=load_peak_data),
        patch.object(
            samples_peaks.m_compute,
            "get_scan_timestamps",
            return_value=np.array(TIME),
        ),
        patch.object(
            samples_peaks.m_compute,
            "stored_scans_per_peak",
            side_effect=lambda data, *_: np.full(data.mz.size, len(TIME)),
        ),
        patch.object(samples_controller, "_segments_of", AsyncMock(return_value=None)),
        patch.object(batch_peak_table, "_segments_of", AsyncMock(return_value=None)),
    ):
        yield


async def table(samples=SAMPLES, **options):
    """The batch's table, and the metadata it carries."""
    with patch.object(
        batch_peak_table, "_batch_samples", AsyncMock(return_value=samples)
    ):
        content = await batch_peak_table.build_batch_peak_table("sb_1", **options)
    parquet = pq.read_table(io.BytesIO(content))
    meta = json.loads(parquet.schema.metadata[batch_peak_table.METADATA_KEY])
    return parquet.to_pylist(), meta


async def per_sample_read(item: SimpleNamespace) -> dict:
    """What ``GET /api/samples/{sample_item_id}/peaks`` answers for a sample."""
    with patch.object(samples_controller, "fetch_sample", AsyncMock(return_value=item)):
        response = await get_sample_peaks_route(
            sample_item_id=item.sample_item_id,
            query_params=GetSamplePeaksQueryParams(),
            user=USER,
            membership=None,
        )
    return json.loads(response.body)["data"]


@pytest.mark.asyncio
async def test_the_table_holds_each_samples_per_sample_read(stores):
    rows, meta = await table()

    for item in SAMPLES:
        expected = await per_sample_read(item)
        got = [row for row in rows if row["sample_item_id"] == item.sample_item_id]
        assert [row["peak_id"] for row in got] == expected["peak_id"]
        for column in ("mz", "area", "height", "sparsity", "signal_to_noise"):
            # A file with no noise estimate: one null for the whole column in
            # the per-sample read, a null per row in the table
            want = expected[column] or [None] * len(got)
            assert [row[column] for row in got] == want, column
        assert {row["sample_item_name"] for row in got} == {item.sample_item_name}
        assert {row["datetime_utc"] for row in got} == {item.datetime_utc}
        assert {row["segment"] for row in got} == {None}
    assert meta["failures"] == []
    assert [s["sample_item_id"] for s in meta["samples"]] == ["si_1", "si_2", "si_3"]
    assert [s["rows"] for s in meta["samples"]] == [2, 1, 2]


@pytest.mark.asyncio
async def test_each_sample_answers_its_own_polarity(stores):
    rows, _ = await table()

    by_sample = {}
    for row in rows:
        by_sample.setdefault(row["sample_item_id"], []).append(row["mz"])
    # file_a's one negative peak is the negative sample's only one
    assert by_sample == {
        "si_1": [100.0, 300.0],
        "si_2": [200.0],
        "si_3": [120.0, 220.0],
    }


@pytest.mark.asyncio
async def test_a_peak_with_no_noise_estimate_is_null_not_a_number(stores):
    rows, _ = await table()

    # si_1 keeps file_a's 100 (s/n 10) and 300 (s/n inf); file_b has none
    assert [row["signal_to_noise"] for row in rows] == [10.0, None, None, None, None]


@pytest.mark.asyncio
async def test_a_sample_whose_read_fails_is_named_and_the_rest_answered(stores):
    lost = sample("si_9", "file_missing", hour=4)

    rows, meta = await table([SAMPLES[0], lost, SAMPLES[2]])

    assert {row["sample_item_id"] for row in rows} == {"si_1", "si_3"}
    assert [(f["sample_item_id"], f["status_code"]) for f in meta["failures"]] == [
        ("si_9", 404)
    ]
    assert "Sample si_9" in meta["failures"][0]["error"]
    assert "si_9" not in meta["segments"]


@pytest.mark.asyncio
async def test_satellites_are_left_out_unless_asked_for_and_then_flagged(stores):
    default, _ = await table(SAMPLES[:1])
    kept, _ = await table(SAMPLES[:1], satellites=True)

    assert [(row["mz"], row["is_satellite"]) for row in default] == [
        (100.0, False),
        (300.0, False),
    ]
    # The satellite comes in flagged; the weak peak stays out either way
    assert [(row["mz"], row["is_satellite"]) for row in kept] == [
        (100.0, False),
        (150.0, True),
        (300.0, False),
    ]


@pytest.mark.asyncio
async def test_a_store_that_flags_no_satellites_reads_none_flagged(stores):
    unflagged = store("file_b").drop_vars("is_satellite")

    with patch.object(samples_peaks, "load_peak_data", return_value=unflagged):
        rows, _ = await table(SAMPLES[2:])

    assert [(row["mz"], row["is_satellite"]) for row in rows] == [
        (120.0, False),
        (220.0, False),
    ]


@pytest.mark.asyncio
async def test_sums_are_served_when_average_is_off(stores):
    averaged, _ = await table(SAMPLES[:1])
    summed, _ = await table(SAMPLES[:1], average=False)

    assert [row["height"] for row in averaged] == [3.0, 11.0]
    assert [row["height"] for row in summed] == [9.0, 33.0]
    assert [row["area"] for row in summed] == [18.0, 66.0]


@pytest.mark.asyncio
async def test_matches_add_one_row_per_match_and_keep_an_unmatched_peak(stores):
    def match(isotope: str, ion: str) -> dict:
        return {
            "target_isotope_id": isotope,
            "match_score_isotope": 0.9,
            "relative_abundance": 1.0,
            "target_isotope_formula": "C",
            "target_ion_id": ion,
            "target_ion_formula": "C+",
            "target_compound_id": "tc_1",
            "target_compound_name": None,
            "target_compound_formula": "C",
            "ionization_mechanism_id": "im_1",
            "target_collection_ids": ["col_1"],
            "target_collection_names": ["Collection 1"],
        }

    matches = AsyncMock(return_value=[[match("i_1", "n_1"), match("i_2", "n_2")], []])
    with patch.object(batch_peak_table, "query_peak_matches", matches):
        plain, _ = await table(SAMPLES[:1])
        rows, _ = await table(SAMPLES[:1], matches=True)

    assert "target_isotope_id" not in plain[0]
    assert [(row["peak_id"], row["target_isotope_id"]) for row in rows] == [
        ("file_a_100", "i_1"),
        ("file_a_100", "i_2"),
        ("file_a_300", None),
    ]
    assert rows[0]["target_collection_ids"] == ["col_1"]
    assert rows[2]["target_collection_names"] is None
    matches.assert_awaited_with(
        "si_1",
        "instrument X",
        ["file_a_100", "file_a_300"],
        instrument_type="orbitrap",
    )


@pytest.mark.asyncio
async def test_a_stitched_sample_names_each_peaks_segment(stores):
    described = [{"stream": 0}, {"stream": 1}]
    stitched = store("file_a").assign(stream=("mz", np.array([0, 1, 0, 1, 1])))

    with (
        patch.object(
            samples_peaks,
            "load_peak_data",
            side_effect=lambda f, drop_bad_peaks=True, composite=True: stitched.isel(
                mz=[0, 4]
            ),
        ),
        patch.object(
            batch_peak_table,
            "_segments_of",
            AsyncMock(return_value=SimpleNamespace(described=described)),
        ),
    ):
        rows, meta = await table(SAMPLES[:1])

    assert [row["segment"] for row in rows] == [0, 1]
    assert meta["segments"] == {"si_1": described}


@pytest.mark.asyncio
async def test_a_per_stream_store_not_stitched_names_no_segment(stores):
    # A store that labels each peak's stream, of a polarity the composite
    # does not stitch: the per-sample read sends no segment, nor does the table
    labelled = store("file_a").assign(stream=("mz", np.array([0, 1, 0, 1, 1])))

    with patch.object(
        samples_peaks,
        "load_peak_data",
        side_effect=lambda f, drop_bad_peaks=True, composite=True: labelled.isel(
            mz=[0, 4]
        ),
    ):
        rows, meta = await table(SAMPLES[:1])
        expected = await per_sample_read(SAMPLES[0])

    assert [row["segment"] for row in rows] == [None, None]
    assert expected["segment"] is None
    assert meta["segments"] == {"si_1": None}


@pytest.mark.asyncio
async def test_only_the_samples_asked_for_are_read(stores):
    with patch.object(
        samples_peaks, "load_peak_data", side_effect=load_peak_data
    ) as opened:
        rows, meta = await table(sample_item_ids=["si_3", "si_1"])

    # The listing's order, not the order asked in; si_2's store never opened
    assert [s["sample_item_id"] for s in meta["samples"]] == ["si_1", "si_3"]
    assert {row["sample_item_id"] for row in rows} == {"si_1", "si_3"}
    assert sorted(call.args[0] for call in opened.call_args_list) == [
        "file_a",
        "file_b",
    ]
    assert meta["failures"] == []


@pytest.mark.asyncio
async def test_an_id_that_is_no_sample_of_the_batch_is_a_failure(stores):
    rows, meta = await table(sample_item_ids=["si_1", "si_other"])

    assert {row["sample_item_id"] for row in rows} == {"si_1"}
    assert [(f["sample_item_id"], f["status_code"]) for f in meta["failures"]] == [
        ("si_other", 404)
    ]


@pytest.mark.asyncio
async def test_an_empty_batch_is_an_empty_table_with_the_columns(stores):
    rows, meta = await table([])

    assert rows == []
    assert meta["samples"] == [] and meta["failures"] == []


@pytest.mark.asyncio
async def test_the_route_serves_the_table_as_parquet(stores):
    with (
        patch.object(batch_peaks_routes, "check_batch_access", AsyncMock()) as access,
        patch.object(
            batch_peak_table, "_batch_samples", AsyncMock(return_value=SAMPLES)
        ),
    ):
        response = await batch_peaks_routes.get_batch_peak_lists_route(
            sample_batch_id="sb_1",
            sample_item_id=None,
            matches=False,
            satellites=False,
            average=True,
            user=USER,
        )

    access.assert_awaited_once_with("sb_1", USER, "guest")
    assert response.status_code == 200
    assert response.media_type == batch_peak_table.PARQUET_MEDIA_TYPE
    parquet = pq.read_table(io.BytesIO(response.body))
    assert parquet.num_rows == 5
    assert parquet.schema.names == batch_peak_table.PEAK_SCHEMA.names


@pytest.mark.asyncio
async def test_the_route_reads_the_table_as_asked():
    build = AsyncMock(return_value=b"PAR1")
    with (
        patch.object(batch_peaks_routes, "check_batch_access", AsyncMock()),
        patch.object(batch_peaks_routes, "build_batch_peak_table", build),
    ):
        await batch_peaks_routes.get_batch_peak_lists_route(
            sample_batch_id="sb_1",
            sample_item_id=["si_2"],
            matches=True,
            satellites=True,
            average=False,
            user=USER,
        )

    build.assert_awaited_once_with(
        "sb_1",
        sample_item_ids=["si_2"],
        matches=True,
        satellites=True,
        average=False,
    )
