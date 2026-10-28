"""A batch's peak lists as one table: every peak of every sample, one read.

The batch-level counterpart of ``GET /api/samples/{sample_item_id}/peaks``.
Each sample is read exactly as that route reads it - its own polarity, the
whole acquisition, the stored sums averaged or summed - and the rows of all
of them are stacked into one Parquet file. A batch of several hundred spectra
is hundreds of thousands of rows: a client that fanned out one request per
sample, each answered in JSON, paid for that table hundreds of times over.

What a sample's read says beyond its rows rides in the file's key-value
metadata under ``mascope`` (:data:`METADATA_KEY`), a JSON object: the samples
read, the samples whose read failed and why, and the segments of each
stitched sample. A sample whose read fails does not fail the batch; it is
named there, as a fan-out of per-sample reads would have reported it.
"""

from __future__ import annotations

import asyncio
import io
import json
import math
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import select

from mascope_backend.api.controllers.samples.lib.samples_matches import (
    query_peak_matches,
)
from mascope_backend.api.controllers.samples.lib.samples_peaks import extract_peaks
from mascope_backend.api.controllers.samples.samples_controller import _segments_of
from mascope_backend.api.lib.exceptions.api_exceptions import process_exception
from mascope_backend.db import async_session
from mascope_backend.db.views import Sample


#: The media type the table is served with.
PARQUET_MEDIA_TYPE = "application/vnd.apache.parquet"

#: The key of the file's key-value metadata that holds what the reads said.
METADATA_KEY = b"mascope"

#: Bumped when the metadata or the columns change in a way a reader must know.
TABLE_VERSION = 1

#: How many samples are read at once. A read is mostly file I/O on the
#: worker threads, so a few at a time keep the batch quick without taking
#: every thread a busy server has.
_CONCURRENT_READS = 4

#: The columns of every table, in order.
PEAK_SCHEMA = pa.schema(
    [
        ("sample_item_id", pa.string()),
        ("sample_item_name", pa.string()),
        ("datetime_utc", pa.timestamp("us", tz="UTC")),
        ("peak_id", pa.string()),
        ("mz", pa.float64()),
        ("sparsity", pa.float64()),
        ("signal_to_noise", pa.float64()),
        ("segment", pa.int64()),
        ("area", pa.float64()),
        ("height", pa.float64()),
        ("is_satellite", pa.bool_()),
    ]
)

#: The columns ``matches`` adds, one row per match of a peak: the keys of
#: each match ``query_peak_matches`` lists, in the order the SDK lays them out.
MATCH_SCHEMA = pa.schema(
    [
        ("match_score_isotope", pa.float64()),
        ("relative_abundance", pa.float64()),
        ("target_isotope_id", pa.string()),
        ("target_isotope_formula", pa.string()),
        ("target_ion_id", pa.string()),
        ("target_ion_formula", pa.string()),
        ("target_compound_id", pa.string()),
        ("target_compound_name", pa.string()),
        ("target_compound_formula", pa.string()),
        ("ionization_mechanism_id", pa.string()),
        ("target_collection_ids", pa.list_(pa.string())),
        ("target_collection_names", pa.list_(pa.string())),
    ]
)


def table_schema(*, matches: bool) -> pa.Schema:
    """The columns of a table, with the match columns when asked for."""
    if not matches:
        return PEAK_SCHEMA
    return pa.schema(list(PEAK_SCHEMA) + list(MATCH_SCHEMA))


async def _batch_samples(sample_batch_id: str) -> list[Sample]:
    """The batch's samples in the order the sample listing gives them."""
    async with async_session() as session:
        result = await session.execute(
            select(Sample)
            .where(Sample.sample_batch_id == sample_batch_id)
            .order_by(Sample.datetime_utc, Sample.sample_item_id)
        )
        return list(result.scalars().all())


def _finite_or_none(values: list[float] | None, count: int) -> list[float | None]:
    """A column of floats with every non-finite value, or a missing column,
    as a null: a peak with no noise estimate is not one measured noise-free."""
    if values is None:
        return [None] * count
    return [v if math.isfinite(v) else None for v in values]


async def _sample_table(
    sample: Sample, *, matches: bool, satellites: bool, average: bool
) -> tuple[pa.Table, list[dict] | None]:
    """One sample's rows, and the segments of its composite where stitched.

    Read as ``GET /api/samples/{sample_item_id}/peaks`` reads it without a
    time or m/z range.
    """
    peaks = await asyncio.to_thread(
        extract_peaks,
        filename=sample.filename,
        polarity=sample.polarity,  # type: ignore[attr-defined]
        sample_t0=sample.t0,  # type: ignore[attr-defined]
        sample_t1=sample.t1,  # type: ignore[attr-defined]
        average=average,
        satellites=satellites,
    )
    segments = await _segments_of(sample)
    count = peaks.count
    columns: dict[str, list[Any]] = {
        "sample_item_id": [sample.sample_item_id] * count,
        "sample_item_name": [sample.sample_item_name] * count,
        "datetime_utc": [sample.datetime_utc] * count,
        "peak_id": peaks.peak_ids,
        "mz": peaks.mz_values,
        "sparsity": peaks.sparsity or [],
        "signal_to_noise": _finite_or_none(peaks.signal_to_noise, count),
        # As the per-sample read: a peak names its segment only where the
        # sample's spectrum is stitched from several
        "segment": (
            peaks.streams if segments and peaks.streams is not None else [None] * count
        ),
        "area": peaks.areas or [],
        "height": peaks.heights or [],
        "is_satellite": peaks.satellite or [False] * count,
    }
    if matches:
        per_peak = await query_peak_matches(
            sample.sample_item_id,
            sample.instrument,
            peaks.peak_ids,
            instrument_type=sample.instrument_type,
        )
        columns = _one_row_per_match(columns, per_peak)
    table = pa.Table.from_pydict(columns, schema=table_schema(matches=matches))
    return table, segments.described if segments else None


def _one_row_per_match(
    columns: dict[str, list[Any]], per_peak: list[list[dict]]
) -> dict[str, list[Any]]:
    """Repeat each peak's row once per match, the match's fields beside it.

    A peak no target matched keeps one row, its match columns null - the
    shape the SDK makes of a per-sample read's nested matches.
    """
    keys = MATCH_SCHEMA.names
    out: dict[str, list[Any]] = {name: [] for name in [*columns, *keys]}
    for index, peak_matches in enumerate(per_peak):
        for match in peak_matches or [None]:
            for name, values in columns.items():
                out[name].append(values[index])
            for key in keys:
                out[key].append(match.get(key) if match else None)
    return out


async def build_batch_peak_table(
    sample_batch_id: str,
    *,
    matches: bool = False,
    satellites: bool = False,
    average: bool = True,
) -> bytes:
    """Every peak of every sample of a batch, as one Parquet file.

    :param sample_batch_id: The batch.
    :param matches: Add the match columns, one row per match of a peak.
    :param satellites: Keep the peaks detection flagged satellites, which
        the per-sample read leaves out; ``is_satellite`` says which they are.
    :param average: Average each peak's stored sums over its scans rather
        than return the sums.
    :return: The file's bytes.
    """
    samples = await _batch_samples(sample_batch_id)
    gate = asyncio.Semaphore(_CONCURRENT_READS)

    async def read(sample: Sample):
        async with gate:
            try:
                return await _sample_table(
                    sample, matches=matches, satellites=satellites, average=average
                )
            except Exception as e:
                # The failure is the sample's, as a per-sample read would
                # have answered it; the batch goes on. Logged with its ref.
                return process_exception(
                    e, f"Failed to read the peaks of sample '{sample.sample_item_name}'"
                )

    answers = await asyncio.gather(*(read(sample) for sample in samples))

    tables: list[pa.Table] = []
    read_samples: list[dict] = []
    failures: list[dict] = []
    segments: dict[str, list[dict] | None] = {}
    for sample, answer in zip(samples, answers):
        where = {
            "sample_item_id": sample.sample_item_id,
            "sample_item_name": sample.sample_item_name,
        }
        if isinstance(answer, Exception):
            failures.append(
                {
                    **where,
                    "status_code": answer.status_code,
                    "error": answer.user_message,
                }
            )
            continue
        table, sample_segments = answer
        tables.append(table)
        segments[sample.sample_item_id] = sample_segments
        read_samples.append({**where, "rows": table.num_rows})

    schema = table_schema(matches=matches)
    table = (
        pa.concat_tables(tables) if tables else schema.empty_table()
    ).replace_schema_metadata(
        {
            METADATA_KEY: json.dumps(
                {
                    "version": TABLE_VERSION,
                    "sample_batch_id": sample_batch_id,
                    "samples": read_samples,
                    "failures": failures,
                    "segments": segments,
                },
                default=str,
            ).encode()
        }
    )
    buffer = io.BytesIO()
    pq.write_table(table, buffer, compression="zstd")
    return buffer.getvalue()
