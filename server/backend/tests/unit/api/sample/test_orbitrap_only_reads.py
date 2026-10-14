"""An Orbitrap-only read asked of another sample file is refused as a request.

Per-scan centroids (``GET /api/samples/centroids``), a sample file's metadata
(``GET /api/sample/files/{id}/metadata``) and its scan table
(``GET /api/sample/files/{id}/scans``) are read from an Orbitrap raw file. Asked of a TOF sample, both went on to the reader, which raised
``NotImplementedError``, and answered 500: an error report for every such
request, though nothing on the server had failed. They are now refused with
422 before anything is read, and logged at INFO like any other client error.
So is an Orbitrap sample whose raw file is not kept: the reader has nothing to
open there either.

The sample rows and the file classification are stood in; the refusal itself
goes through the real controllers and their ``@api_controller`` handling.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from test_utils import captured_logs

from mascope_backend.api.controllers.sample.files import sample_files_controller
from mascope_backend.api.controllers.samples import samples_controller
from mascope_backend.api.lib.exceptions.api_exceptions import ApiException
from mascope_runtime.logging import _SENTRY_LEVELS


#: How the filestore would classify each stand-in filename, by its prefix: a
#: TOF file, an Orbitrap file whose raw file is not kept, and an Orbitrap file
#: with its raw file.
_FILE_TYPES = {"TOF-1": "tof_h5", "ORBI-0": "orbi_zarr", "ORBI-1": "orbi_raw"}

#: The two kinds of sample file neither read can serve.
_WITHOUT_RAW_FILE = pytest.mark.parametrize(
    "filename", ["TOF-1_b", "ORBI-0_b"], ids=["tof", "orbitrap-raw-not-kept"]
)


def _file_type(filename: str) -> str:
    """Classify a stand-in filename the way the filestore would."""
    return _FILE_TYPES[filename.split("_", 1)[0]]


def _reported(records) -> list:
    """The records error monitoring would receive."""
    return [r for r in records if r["level"].name in _SENTRY_LEVELS]


class _Session:
    """An ``async_session()`` whose one query answers with the given samples."""

    def __init__(self, samples):
        self.samples = samples

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, _stmt):
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: self.samples)
        )


def _sample(item_id: str, filename: str) -> SimpleNamespace:
    return SimpleNamespace(
        sample_item_id=item_id, filename=filename, t0=0.0, t1=60.0, polarity="-"
    )


class TestPerScanCentroids:
    @staticmethod
    async def _request(samples):
        extract = Mock(return_value=[])
        with (
            patch.object(
                samples_controller, "async_session", lambda: _Session(samples)
            ),
            patch.object(samples_controller, "get_sample_file_type", _file_type),
            patch.object(
                samples_controller.m_compute, "get_orbi_centroids_per_scan", extract
            ),
            captured_logs() as records,
        ):
            try:
                result = await samples_controller.get_samples_centroids(
                    sample_item_ids=[sample.sample_item_id for sample in samples]
                )
            except ApiException as refused:
                return refused, extract, records
        return result, extract, records

    @_WITHOUT_RAW_FILE
    @pytest.mark.asyncio
    async def test_a_sample_without_a_raw_file_is_refused_before_anything_is_read(
        self, filename
    ):
        refused, extract, records = await self._request(
            [_sample("orbi-item", "ORBI-1_a"), _sample("refused-item", filename)]
        )

        assert isinstance(refused, ApiException)
        assert refused.status_code == 422
        assert isinstance(refused.__cause__, HTTPException)
        assert "Orbitrap raw files" in refused.user_message
        assert "refused-item" in refused.user_message
        assert "orbi-item" not in refused.user_message
        extract.assert_not_called()

    @_WITHOUT_RAW_FILE
    @pytest.mark.asyncio
    async def test_the_refusal_is_not_reported_as_a_fault(self, filename):
        refused, _, records = await self._request([_sample("refused-item", filename)])

        assert isinstance(refused, ApiException)
        assert _reported(records) == []

    @pytest.mark.asyncio
    async def test_a_long_refusal_names_ten_and_counts_the_rest(self):
        samples = [_sample(f"tof-{i:02d}", f"TOF-1_{i:02d}") for i in range(13)]

        refused, _, _ = await self._request(samples)

        assert "tof-09" in refused.user_message
        assert "tof-10" not in refused.user_message
        assert "and 3 more" in refused.user_message

    @pytest.mark.asyncio
    async def test_orbitrap_samples_are_still_read(self):
        result, extract, _ = await self._request(
            [_sample("orbi-item", "ORBI-1_a"), _sample("orbi-other", "ORBI-1_c")]
        )

        assert set(result["data"]) == {"orbi-item", "orbi-other"}
        assert extract.call_count == 2


class TestSampleFileMetadata:
    @staticmethod
    async def _request(filename: str, include_centroids: bool = True):
        # The document whole, and in pieces: with its centroids it is streamed.
        read = Mock(
            return_value=SimpleNamespace(
                to_dict=lambda include_centroids=True: {"Model": "x"},
                iter_json=lambda: iter(['{"Model":"x"', "}"]),
            )
        )
        stored = AsyncMock(return_value={"data": {"filename": filename}})
        with (
            patch.object(sample_files_controller, "get_sample_file", stored),
            patch.object(sample_files_controller, "get_sample_file_type", _file_type),
            patch.object(sample_files_controller.m_compute, "get_metadata", read),
            captured_logs() as records,
        ):
            try:
                result = await sample_files_controller.get_sample_file_metadata(
                    "file-id", include_centroids
                )
            except ApiException as refused:
                return refused, read, records
        return result, read, records

    @_WITHOUT_RAW_FILE
    @pytest.mark.asyncio
    async def test_a_file_without_a_raw_file_is_refused_before_anything_is_read(
        self, filename
    ):
        refused, read, records = await self._request(filename)

        assert isinstance(refused, ApiException)
        assert refused.status_code == 422
        assert "Orbitrap raw file" in refused.user_message
        assert filename in refused.user_message
        read.assert_not_called()
        assert _reported(records) == []

    @pytest.mark.asyncio
    async def test_an_orbitrap_file_is_still_read(self):
        result, read, _ = await self._request("ORBI-1_a", include_centroids=False)

        assert result["data"] == {"Model": "x"}
        read.assert_called_once_with("ORBI-1_a")

    @pytest.mark.asyncio
    async def test_an_orbitrap_file_is_still_streamed_with_its_centroids(self):
        result, read, _ = await self._request("ORBI-1_a")

        assert isinstance(result, StreamingResponse)
        read.assert_called_once_with("ORBI-1_a")


class TestScanTable:
    @staticmethod
    async def _request(filename: str):
        table = {"num_of_scans": 1, "stats_per_scan": {1: {"ScanNumber": 1}}}
        read = Mock(return_value=table)
        stored = AsyncMock(return_value={"data": {"filename": filename}})
        with (
            patch.object(sample_files_controller, "get_sample_file", stored),
            patch.object(sample_files_controller, "get_sample_file_type", _file_type),
            patch.object(sample_files_controller.m_metadata, "scan_table", read),
            captured_logs() as records,
        ):
            try:
                result = await sample_files_controller.get_sample_file_scans("file-id")
            except ApiException as refused:
                return refused, read, records
        return result, read, records

    @_WITHOUT_RAW_FILE
    @pytest.mark.asyncio
    async def test_a_file_without_a_raw_file_is_refused_before_anything_is_read(
        self, filename
    ):
        refused, read, records = await self._request(filename)

        assert isinstance(refused, ApiException)
        assert refused.status_code == 422
        assert "Orbitrap raw file" in refused.user_message
        assert filename in refused.user_message
        read.assert_not_called()
        assert _reported(records) == []

    @pytest.mark.asyncio
    async def test_an_orbitrap_file_is_still_read(self):
        result, read, _ = await self._request("ORBI-1_a")

        assert result["results"] == 1
        assert read.call_args.args == ("ORBI-1_a",)
